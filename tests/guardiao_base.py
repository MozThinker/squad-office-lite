"""Base dos testes do guardião da Conversa (ADR-023, revisão S2/S5): o `chat_host/guardiao.py` real numa porta TCP
local (CONVERSA_TLS_TESTE), pastas temporárias (`verificados/`, `conversas/`, `publico/` e o estado do guardião com o
`entrada.json` dos vectores, código por usar, e o `chaves.json` com a chave K e o `kid` dos vectores: um browser já
registado), sessões e bloqueio novos em cada teste e pedidos assinados com K (como o browser). `sessao()` entra por
desafio–resposta; `registar()` usa o código. Nunca a `~` real. Sem testes próprios."""
import hashlib
import hmac
import http.client
import importlib.util
import json
import secrets
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import chat

ROOT = Path(__file__).resolve().parent.parent
VECTORES = json.loads((ROOT / "tests" / "fixtures" / "conversa_vectores.json").read_text(encoding="utf-8"))
CHAVE = bytes.fromhex(VECTORES["chave"])
KID = VECTORES["kid"]
VERIFICADOR = VECTORES["verificador"]
TZ = ZoneInfo("Europe/Lisbon")
CID = "20261123-101500-abc123"
NONCE = "0123456789abcdef0123456789abcdef"
WEB = {"conversa.html": b"<!doctype html><title>Conversa</title><script src=conversa_inicio.js></script>",
       "conversa_inicio.js": b"export {};\n", "conversa.css": b"body{}\n", "fonts/letra.woff2": b"wOF2"}


def _carregar(nome: str, caminho: Path):
    # um só módulo por nome (o test_chat_ponte e estes testes partilham o mesmo guardião)
    if nome in sys.modules:
        return sys.modules[nome]
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


gm = _carregar("chat_host_guardiao", ROOT / "chat_host" / "guardiao.py")


def entrada(verificador_sha256=VECTORES["verificador_sha256"]) -> dict:
    return {"v": 1, "sal_office": VECTORES["sal_office"], "sal_ponte": VECTORES["sal_ponte"], "iteracoes": 600000,
            "verificador_sha256": verificador_sha256, "criado": "2026-11-23T10:00:00+00:00"}


def opt_falso(raiz: Path, web: dict | None = None, guardiao: bytes | None = None) -> Path:
    """Um /opt/office-conversa num tmp: guardiao.py, conversa.manifesto, web/ e o SHA256SUMS de tudo."""
    web = WEB if web is None else web
    opt = raiz / "opt"
    (opt / "chat_host").mkdir(parents=True)
    (opt / "web" / "fonts").mkdir(parents=True)
    ficheiros = {"chat_host/guardiao.py": guardiao if guardiao is not None
                 else (ROOT / "chat_host" / "guardiao.py").read_bytes(),
                 "conversa.manifesto": "".join(f"{n}\n" for n in web).encode(),
                 **{f"web/{n}": d for n, d in web.items()}}
    for rel, dados in ficheiros.items():
        (opt / rel).write_bytes(dados)
    (opt / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(d).hexdigest()}  {rel}\n"
                                            for rel, d in sorted(ficheiros.items())))
    return opt


class GuardiaoBase(unittest.TestCase):
    """Guardião em 127.0.0.1:<porta>, entrada.json e chave dos vectores, ponte «viva» (pulsação do contrato 2)."""

    ambiente: dict = {}
    tls_teste = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("verificados", "conversas", "publico", "estado"):
            (self.root / d).mkdir(mode=0o700)
        self.estado = self.root / "estado"
        self.ver = self.root / "verificados"
        self.escrever_entrada()
        self.gravar_chaves([(KID, CHAVE)])
        self.pulsar()
        self.g = gm.Guardiao(self.config(), dict(WEB))
        self.srv = gm.servidor("127.0.0.1:0", self.g)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.srv.server_address[1]}"
        self.cookie = self.csrf = None

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def config(self, **mudar):
        env = {"CONVERSA_PASTA": str(self.root), "CONVERSA_GUARDIAO_ESTADO": str(self.estado),
               "CONVERSA_FUSO": "Europe/Lisbon", "CONVERSA_TLS_TESTE": "1" if self.tls_teste else "",
               **self.ambiente, **mudar}
        return gm.config(env)

    def usar(self, **mudar):
        # outra instalação (o drop-in da unit): permissões, voz, nome
        self.g.cfg = self.config(**mudar)

    # --- HTTP ---
    def req(self, path, method="GET", body=None, headers=None):
        c = http.client.HTTPConnection(self.host, timeout=10)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            c.close()

    def cab(self, sessao=True, csrf=True, origem=True) -> dict:
        h = {}
        if origem:
            h["Origin"] = f"http://{self.host}"
        if sessao and self.cookie:
            h["Cookie"] = f"{gm.COOKIE}={self.cookie}"
        if csrf and self.csrf:
            h["X-Csrf"] = self.csrf
        return h

    def get(self, path, headers=None, **k):
        code, _, body = self.req(path, headers={**self.cab(**k), **(headers or {})})
        return code, json.loads(body) if body else None

    def post_raw(self, path, corpo, headers=None, **k):
        body = corpo if isinstance(corpo, bytes) else json.dumps(corpo, ensure_ascii=False).encode()
        h = {"Content-Type": "application/json", **self.cab(**k), **(headers or {})}
        code, cab, resp = self.req(path, "POST", body, h)
        return code, cab, json.loads(resp) if resp else None

    def _abriu(self, r):
        code, cab, d = r
        if code == 200:
            self.cookie = cab["set-cookie"].split(";")[0].split("=", 1)[1]
            self.csrf = d["csrf"]
        return r

    def desafio(self, **k) -> str:
        return self.get("/api/conversa/estado", sessao=False, csrf=False, **k)[1]["desafio"]

    def sessao(self, kid=KID, chave=CHAVE, desafio=None, **k):
        # entrada por desafio–resposta com a chave K já registada
        desafio = desafio or self.desafio(**k)
        assinatura = hmac.new(chave, f"entrar\n{desafio}".encode(), "sha256").hexdigest()
        return self._abriu(self.post_raw("/api/conversa/entrar", {"kid": kid, "desafio": desafio,
                                                                  "assinatura": assinatura},
                                         sessao=False, csrf=False, **k))

    def registar(self, verificador=VERIFICADOR, kid=None, chave=None, **k):
        # registo de um browser novo com o código (por omissão, uma chave e um kid aleatórios)
        corpo = {"verificador": verificador, "kid": kid or secrets.token_hex(8),
                 "chave": (chave or secrets.token_bytes(32)).hex()}
        return self._abriu(self.post_raw("/api/conversa/registar", corpo, sessao=False, csrf=False, **k))

    def assinado(self, acao, chave=CHAVE, **campos) -> dict:
        # o `t` do relógio (o guardião recusa |t − agora| > 5 min)
        d = {"acao": acao, "pid": secrets.token_hex(8), "t": int(time.time() * 1000), **campos}
        d["assinatura"] = chat.assinar(chave, d)
        return d

    def post(self, corpo: dict, headers=None, **k):
        code, _, d = self.post_raw("/api/conversa/pedido", corpo, headers, **k)
        return code, d

    def enviar(self, texto="olá", cid=CID, **k):
        return self.assinado("enviar", conversa=cid, texto=texto, modo="normal", modelo="omissao",
                             permissoes="cartao", **k)

    # --- disco ---
    def iso(self, delta_s=0.0) -> str:
        return (datetime.now(TZ) + timedelta(seconds=delta_s)).isoformat(timespec="seconds")

    def escrever_entrada(self, **k):
        (self.estado / "entrada.json").write_text(json.dumps(entrada(**k)), encoding="utf-8")

    def gravar_chaves(self, chaves):
        # [(kid, K)] → chaves.json (0600), como o guardião o escreve
        f = self.estado / "chaves.json"
        f.write_text(json.dumps({"v": 1, "chaves": [{"kid": kid, "chave": c.hex(), "criada": self.iso()}
                                                    for kid, c in chaves]}))
        f.chmod(0o600)

    def pulsar(self, idade=0.0, versao=gm.CONTRATO, voz_modo="desligada", fornecedor=None, permissoes="cartao",
               dia_usd=0.0, vistos=()):
        d = {"v": 1, "pulsacao": self.iso(-idade), "versao": versao, "voz": voz_modo != "desligada",
             "voz_modo": voz_modo, "voz_fornecedor": fornecedor, "permissoes": permissoes, "dia_usd": dia_usd,
             "vistos": list(vistos)}
        (self.root / "conversas" / "estado.json").write_text(json.dumps(d), encoding="utf-8")

    def aceitar_tokens(self):
        p = chat.prefs_vazias()
        p["aviso_tokens"] = self.iso()
        (self.estado / "prefs.json").write_text(json.dumps(p), encoding="utf-8")

    def conversa(self, cid=CID, titulo="Conversa nova", estado="pronto", arquivada=False, pendentes=(),
                 eventos=(), ultimo_fim=None, actualizada=None) -> Path:
        p = self.root / "conversas" / cid
        p.mkdir(exist_ok=True)
        m = {"v": 1, "id": cid, "titulo": titulo, "criada": self.iso(-3600),
             "actualizada": actualizada or self.iso(), "estado": estado, "arquivada": arquivada,
             "n": len(eventos), "ultimo_fim": ultimo_fim, "pendentes": list(pendentes)}
        (p / "meta.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        (p / "eventos.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in eventos),
                                         encoding="utf-8")
        return p

    def pend(self, nonce=NONCE, tipo="permissao", delegacao=False, expira_s=600, n=1) -> dict:
        return {"n": n, "nonce": nonce, "tipo": tipo, "delegacao": delegacao, "expira": self.iso(expira_s)}

    def fila(self) -> list:
        return sorted(p.name for p in self.ver.iterdir() if p.name.endswith(".json"))

    def ler_fila(self) -> list:
        return [json.loads((self.ver / n).read_text(encoding="utf-8")) for n in self.fila()]
