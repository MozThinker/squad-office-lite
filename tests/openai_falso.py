"""Servidor HTTP falso compatível com `POST /v1/audio/transcriptions` (o da OpenAI), para a voz `externa` da Conversa.

Corre numa thread do próprio teste, em 127.0.0.1 numa porta efémera; nunca sai do host. Regista cada pedido em
`pedidos` (caminho, cabeçalhos, campos do multipart e bytes do ficheiro). `modo`:
  "ok"    → 200 {"text": texto, "duration": duracao}
  "401"   → 401 (chave recusada)      "403" → 403
  "429"   → 429 (limite)              "500" → 500
  "lento" → dorme `demora` s antes do 200
  "lixo"  → 200 com um corpo que não é JSON
"""
from __future__ import annotations

import email.parser
import email.policy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def campos_multipart(ct: str, corpo: bytes) -> dict:
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        f"Content-Type: {ct}\r\n\r\n".encode() + corpo)
    out = {}
    for parte in msg.iter_parts():
        nome = parte.get_param("name", header="content-disposition")
        dados = parte.get_payload(decode=True)
        if parte.get_filename():
            out[nome] = {"nome": parte.get_filename(), "tipo": parte.get_content_type(), "dados": dados}
        else:
            out[nome] = dados.decode()
    return out


class ServidorOpenAI:
    def __init__(self, texto: str = "olá", duracao: float = 2.5):
        self.modo, self.texto, self.duracao, self.demora = "ok", texto, duracao, 5.0
        self.pedidos: list[dict] = []
        falso = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):   # só o seguimento de um redirect chega aqui: regista-o
                falso.pedidos.append({"caminho": self.path, "cabecalhos": dict(self.headers.items()), "bytes": 0})
                self._responder(404, b'{"error": "nao encontrado"}')

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                corpo = self.rfile.read(n)
                p = {"caminho": self.path, "cabecalhos": dict(self.headers.items()), "bytes": len(corpo)}
                try:
                    p["campos"] = campos_multipart(self.headers.get("Content-Type", ""), corpo)
                except Exception as e:   # noqa: BLE001 - o teste vê o erro
                    p["campos"] = {"erro": repr(e)}
                falso.pedidos.append(p)
                modo = falso.modo
                if modo == "lento":
                    time.sleep(falso.demora)
                    modo = "ok"
                if self.path != "/v1/audio/transcriptions":
                    self._responder(404, b'{"error": "nao encontrado"}')
                elif modo == "ok":
                    self._responder(200, json.dumps({"text": falso.texto, "duration": falso.duracao}).encode())
                elif modo in ("302", "307"):
                    self.send_response(int(modo))
                    self.send_header("Location", "/desvio")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif modo == "lixo":
                    self._responder(200, b"<html>nada</html>")
                else:
                    self._responder(int(modo), b'{"error": {"message": "falso"}}')

            def _responder(self, codigo: int, corpo: bytes):
                try:
                    self.send_response(codigo)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(corpo)))
                    self.end_headers()
                    self.wfile.write(corpo)
                except OSError:
                    pass   # o cliente já desistiu (tempo esgotado)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def fechar(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
