"""Segredo do proxy (ADR-006): com OFFICE_PROXY_SEGREDO o Office só responde a quem traz X-Office-Segredo.

Servidor real em porta alta (0 = escolhida pelo SO) e pastas temporárias; só stdlib.
"""
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import server
from tests import cfg

OFFICE_CFG = cfg.LEGADO

GLOBAIS = ("TASKS", "CLAUDE", "SQUADS_DIR", "APROVACOES", "FILA", "QUADRO", "LANCAR", "PROXY_SEGREDO")
SEGREDO = "a1" * 32


class SegredoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.quadro = root / "quadro"
        for d in (root / "tasks", root / "claude", root / "squads", self.quadro):
            d.mkdir(parents=True)
        self.old = tuple(getattr(server, g) for g in GLOBAIS)
        (server.TASKS, server.CLAUDE, server.SQUADS_DIR, server.APROVACOES, server.FILA, server.QUADRO,
         server.LANCAR, server.PROXY_SEGREDO) = (root / "tasks", root / "claude", root / "squads", {}, root / "fila",
                                                 self.quadro, root / "lancar", SEGREDO)
        server._cache["at"] = 0.0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for g, v in zip(GLOBAIS, self.old):
            setattr(server, g, v)
        server._cache["at"] = 0.0
        self.tmp.cleanup()

    def req(self, method, path, segredo=None, body=None, headers=None):
        h = dict(headers or {})
        if segredo is not None:
            h["X-Office-Segredo"] = segredo
        c = http.client.HTTPConnection(self.host, timeout=5)
        try:
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def criar(self, segredo):
        token = json.loads(self.req("GET", "/api/state", SEGREDO)[1])["token"]
        corpo = {"op": "criar", "token": token, "versao": "0", "titulo": "Ideia", "descricao": "",
                 "projecto": "loja", "coluna": "ideias", "etiquetas": []}
        return self.req("POST", "/api/quadro", segredo, json.dumps(corpo).encode(),
                        {"Content-Type": "application/json", "Origin": f"http://{self.host}"})

    def test_sem_cabecalho_403_sem_corpo(self):
        for metodo, path in (("GET", "/api/state"), ("GET", "/api/config"), ("GET", "/api/quadro"), ("GET", "/"),
                             ("GET", "/healthz/x"), ("HEAD", "/"), ("HEAD", "/healthz"), ("POST", "/api/decisao"),
                             ("POST", "/api/quadro/lancar")):
            for segredo in (None, "", "errado", SEGREDO[:-1], SEGREDO + "x", "á" * 4):
                with self.subTest(metodo=metodo, path=path, segredo=segredo):
                    self.assertEqual(self.req(metodo, path, segredo), (403, b""))

    def test_post_sem_segredo_nao_escreve(self):
        self.assertEqual(self.criar(None), (403, b""))
        self.assertFalse((self.quadro / "cartoes.json").exists())
        code, _ = self.criar(SEGREDO)
        self.assertEqual(code, 200)
        self.assertTrue((self.quadro / "cartoes.json").exists())

    def test_healthz_sem_segredo(self):  # HEALTHCHECK do Dockerfile, directo ao contentor
        self.assertEqual(self.req("GET", "/healthz"), (200, b"ok"))
        self.assertEqual(self.req("GET", "/healthz?x=1"), (200, b"ok"))

    def test_com_segredo_responde_e_nao_o_revela(self):
        for path in ("/api/state", "/api/config", "/api/quadro", "/"):
            with self.subTest(path=path):
                code, corpo = self.req("GET", path, SEGREDO)
                self.assertEqual(code, 200)
                self.assertNotIn(SEGREDO.encode(), corpo)

    def test_sem_env_comportamento_antigo(self):  # dev local e restantes testes
        server.PROXY_SEGREDO = ""
        self.assertEqual(self.req("GET", "/api/state")[0], 200)
        self.assertEqual(self.req("GET", "/api/state", "qualquer")[0], 200)


if __name__ == "__main__":
    unittest.main()
