"""Conversa (ADR-023): chat_host/mcp.py, o servidor MCP stdio que é o `mcp__ponte__permissao`.
JSON-RPC por stdio (processo real), socket da ponte falso numa thread; nunca `allow` sem a ponte o dizer."""
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "chat_host" / "mcp.py"
_spec = importlib.util.spec_from_file_location("chat_mcp", MCP)
mcp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp)

TURNO = "ab" * 16


class PonteFalsa:
    """Socket Unix que responde a cada ligação com `resposta` (bytes) e guarda o pedido recebido."""

    def __init__(self, path: str, resposta: bytes | None):
        self.path, self.resposta, self.pedidos = path, resposta, []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(4)
        self.t = threading.Thread(target=self._correr, daemon=True)
        self.t.start()

    def _correr(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            with c:
                buf = b""
                while b"\n" not in buf:
                    b = c.recv(65536)
                    if not b:
                        break
                    buf += b
                self.pedidos.append(json.loads(buf.split(b"\n")[0]))
                if self.resposta is not None:
                    c.sendall(self.resposta)

    def fechar(self):
        self.srv.close()


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="chatmcp-"))
        self.sock = str(self.tmp / "ponte.sock")
        self.ponte = None

    def tearDown(self):
        if self.ponte:
            self.ponte.fechar()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def conversa(self, msgs, env=None) -> list[dict]:
        # o processo real, por stdio, como o claude o arranca
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        e.update(env if env is not None else {"CONVERSA_PONTE_SOCK": self.sock, "CONVERSA_PONTE_TURNO": TURNO})
        entrada = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in msgs)
        r = subprocess.run([sys.executable, str(MCP)], input=entrada, capture_output=True, text=True, env=e,
                           timeout=30, check=True)
        return [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]

    def chamar(self, args=None, **kw):
        args = args if args is not None else {"tool_name": "Bash", "input": {"command": "ls"}, "tool_use_id": "t1"}
        r = self.conversa([{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                            "params": {"name": "permissao", "arguments": args}}], **kw)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], 7)
        conteudo = r[0]["result"]["content"]
        self.assertEqual([c["type"] for c in conteudo], ["text"])
        return json.loads(conteudo[0]["text"])

    def test_initialize_list_ping_e_notificacao(self):
        r = self.conversa([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "x"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ])
        self.assertEqual([x["id"] for x in r], [1, 2, 3])   # a notificação não tem resposta
        self.assertEqual(r[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(r[0]["result"]["capabilities"], {"tools": {}})
        self.assertEqual(r[0]["result"]["serverInfo"]["name"], "ponte")
        ferr = r[1]["result"]["tools"]
        self.assertEqual([t["name"] for t in ferr], ["permissao"])
        self.assertEqual(ferr[0]["inputSchema"], {
            "type": "object", "properties": {"tool_name": {"type": "string"}, "input": {"type": "object"},
                                             "tool_use_id": {"type": "string"}},
            "required": ["tool_name", "input"]})
        self.assertEqual(r[2]["result"], {})

    def test_metodo_desconhecido_e_json_invalido(self):
        r = self.conversa([{"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, "{nao json",
                           {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "outra"}}])
        self.assertEqual(r[0]["error"]["code"], -32601)
        self.assertEqual((r[1]["id"], r[1]["error"]["code"]), (None, -32700))
        self.assertEqual(r[2]["error"]["code"], -32602)

    def test_allow_da_ponte_passa_tal_e_qual(self):
        resp = {"behavior": "allow", "updatedInput": {"command": "ls -la"}}
        self.ponte = PonteFalsa(self.sock, json.dumps(resp).encode() + b"\n")
        self.assertEqual(self.chamar(), resp)
        self.assertEqual(self.ponte.pedidos, [{"v": 1, "turno": TURNO, "tool_name": "Bash",
                                               "input": {"command": "ls"}, "tool_use_id": "t1"}])

    def test_deny_da_ponte(self):
        resp = {"behavior": "deny", "message": "o dono recusou"}
        self.ponte = PonteFalsa(self.sock, json.dumps(resp).encode() + b"\n")
        self.assertEqual(self.chamar(), resp)

    def test_tool_use_id_ausente_vai_null(self):
        self.ponte = PonteFalsa(self.sock, b'{"behavior":"deny","message":"x"}\n')
        self.chamar({"tool_name": "Read", "input": {"file_path": "/x"}})
        self.assertIsNone(self.ponte.pedidos[0]["tool_use_id"])

    def test_socket_em_falta_deny(self):
        self.assertEqual(self.chamar(), {"behavior": "deny", "message": "a ponte não respondeu"})

    def test_sem_variaveis_deny(self):
        self.assertEqual(self.chamar(env={})["behavior"], "deny")

    def test_ligacao_cortada_deny(self):
        self.ponte = PonteFalsa(self.sock, None)   # lê e fecha sem responder
        self.assertEqual(self.chamar(), {"behavior": "deny", "message": "a ponte não respondeu"})

    def test_resposta_fora_do_contrato_deny(self):
        for mau in (b'{"behavior":"allow"}\n', b'{"behavior":"allow","updatedInput":[]}\n',
                    b'{"behavior":"allow","updatedInput":{},"x":1}\n', b'{"behavior":"talvez"}\n', b"[]\n",
                    b"nao json\n"):
            with self.subTest(mau=mau):
                if self.ponte:
                    self.ponte.fechar()
                    os.unlink(self.sock)
                self.ponte = PonteFalsa(self.sock, mau)
                self.assertEqual(self.chamar()["behavior"], "deny")

    def test_argumentos_mal_formados_nao_vao_a_ponte(self):
        self.ponte = PonteFalsa(self.sock, b'{"behavior":"allow","updatedInput":{}}\n')
        self.assertEqual(self.chamar({"tool_name": "Bash", "input": "ls"})["behavior"], "deny")
        self.assertEqual(self.ponte.pedidos, [])

    def test_main_em_memoria(self):
        out = io.StringIO()
        mcp.main(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\n'), out, env={})
        self.assertEqual(json.loads(out.getvalue()), {"jsonrpc": "2.0", "id": 1, "result": {}})


if __name__ == "__main__":
    unittest.main()
