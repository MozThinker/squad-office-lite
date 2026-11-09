"""Modo Docker no Office (AC-f5, AC-o1): segredo do proxy lido de OFFICE_PROXY_SEGREDO_FILE e "modo" no /api/config.

Servidor real em porta alta e pastas temporárias; só stdlib.
"""
import http.client
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import server
from tests.test_api_config import ROOT, Servidor

SEGREDO = "b2" * 32
FALTA = "Falta configurar: corre `docker compose run --rm configurar`"


class LerSegredoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def ficheiro(self, dados: bytes) -> str:
        f = self.raiz / "segredo"
        f.write_bytes(dados)
        return str(f)

    def test_validos(self):
        for dados, esperado in ((SEGREDO.encode(), SEGREDO), (SEGREDO.encode() + b"\n", SEGREDO),
                                (b"x", "x"), (b"a" * 1024, "a" * 1024), (b"a" * 1023 + b"\n", "a" * 1023)):
            with self.subTest(dados=dados[:10], n=len(dados)):
                self.assertEqual(server._ler_segredo(self.ficheiro(dados)), esperado)

    def test_invalidos(self):
        for dados in (b"", b"\n", b"a\n\n", b"a\r\n", b"a" * 1025, b"a" * 1024 + b"\n", b"a b", b" a", b"a\tb",
                      b"a\nb", "segredoá".encode(), b"\xff\xfe", b"a\x00"):
            with self.subTest(dados=dados[:10], n=len(dados)):
                self.assertEqual(server._ler_segredo(self.ficheiro(dados)), "")

    def test_nao_regulares_e_em_falta(self):
        alvo = Path(self.ficheiro(SEGREDO.encode()))
        link = self.raiz / "link"
        link.symlink_to(alvo)
        fifo = self.raiz / "fifo"
        os.mkfifo(fifo)
        pasta = self.raiz / "pasta"
        pasta.mkdir()
        for caminho in (link, fifo, pasta, self.raiz / "nao-existe", Path("/dev/null")):
            with self.subTest(caminho=caminho.name):
                self.assertEqual(server._ler_segredo(str(caminho)), "")


class OfficeDockerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.srv = None

    def tearDown(self):
        if self.srv:
            self.srv.parar()
        self.tmp.cleanup()

    def arranca(self, **extra) -> Servidor:
        env = {"OFFICE_PROXY_SEGREDO": "", "OFFICE_PROXY_SEGREDO_FILE": "", "OFFICE_MODO": ""} | extra
        self.srv = Servidor(self.raiz, ROOT / "office.example.yaml", env)
        return self.srv

    def req(self, method, path, segredo=None, body=None):
        h = {} if segredo is None else {"X-Office-Segredo": segredo}
        if body is not None:
            h["Content-Type"] = "application/json"
        c = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        try:
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def assert_falta(self):
        for metodo, path in (("GET", "/api/state"), ("GET", "/api/config"), ("GET", "/"), ("GET", "/healthz/x"),
                             ("POST", "/api/quadro/lancar"), ("POST", "/api/decisao")):
            for segredo in (None, "", SEGREDO):
                with self.subTest(metodo=metodo, path=path, segredo=segredo):
                    code, corpo = self.req(metodo, path, segredo, b"{}" if metodo == "POST" else None)
                    self.assertEqual(code, 503)
                    self.assertEqual(json.loads(corpo), {"error": FALTA})
        self.assertEqual(self.req("HEAD", "/", SEGREDO), (503, b""))
        self.assertEqual(self.req("GET", "/healthz"), (200, b"ok"))

    def assert_fechado_com(self, segredo):
        self.assertEqual(self.req("GET", "/healthz"), (200, b"ok"))
        for errado in (None, "", "errado", segredo[:-1], segredo + "x"):
            with self.subTest(errado=errado):
                self.assertEqual(self.req("GET", "/api/state", errado), (403, b""))
        code, corpo = self.req("GET", "/api/state", segredo)
        self.assertEqual(code, 200)
        self.assertNotIn(segredo.encode(), corpo)

    def test_ficheiro_valido_exige_cabecalho(self):
        f = self.raiz / "segredo"
        f.write_text(SEGREDO + "\n")
        self.arranca(OFFICE_PROXY_SEGREDO_FILE=str(f))
        self.assert_fechado_com(SEGREDO)
        self.assertNotIn(SEGREDO, self.srv.log.read_text())

    def test_ficheiro_em_falta_503_e_rele_quando_aparece(self):
        f = self.raiz / "proxy" / "segredo"
        self.arranca(OFFICE_PROXY_SEGREDO_FILE=str(f))
        self.assert_falta()
        f.parent.mkdir()
        f.write_text(SEGREDO)
        time.sleep(1.1)   # relê no máximo 1×/s enquanto falta
        self.assert_fechado_com(SEGREDO)
        self.assertIn("proxy: falta o segredo", self.srv.log.read_text())

    def test_ficheiro_invalido_ou_symlink_503(self):
        alvo = self.raiz / "verdadeiro"
        alvo.write_text(SEGREDO)
        link = self.raiz / "link"
        link.symlink_to(alvo)
        vazio = self.raiz / "vazio"
        vazio.write_text("")
        grande = self.raiz / "grande"
        grande.write_text("a" * 2048)
        for f in (link, vazio, grande, self.raiz):
            with self.subTest(f=f.name):
                self.arranca(OFFICE_PROXY_SEGREDO_FILE=str(f))
                self.assert_falta()
                self.srv.parar()
                self.srv = None

    def test_variavel_directa_ganha_ao_ficheiro(self):
        f = self.raiz / "segredo"
        f.write_text(SEGREDO)
        directo = "c3" * 32
        self.arranca(OFFICE_PROXY_SEGREDO=directo, OFFICE_PROXY_SEGREDO_FILE=str(f))
        self.assert_fechado_com(directo)
        self.assertEqual(self.req("GET", "/api/state", SEGREDO), (403, b""))
        # e com a directa definida um ficheiro em falta não interessa
        self.srv.parar()
        self.arranca(OFFICE_PROXY_SEGREDO=directo, OFFICE_PROXY_SEGREDO_FILE=str(self.raiz / "nao-existe"))
        self.assert_fechado_com(directo)

    def test_sem_nenhuma_fica_aberto(self):  # dev local e testes: comportamento de antes
        self.arranca()
        self.assertEqual(self.req("GET", "/api/state")[0], 200)
        self.assertEqual(self.req("GET", "/api/state", "qualquer")[0], 200)

    def test_modo(self):
        for valor, modo in (("docker", "docker"), ("", "vps"), ("vps", "vps"), ("Docker", "vps"), ("docker ", "vps")):
            with self.subTest(valor=valor):
                self.arranca(OFFICE_MODO=valor)
                code, corpo = self.req("GET", "/api/config")
                self.assertEqual(code, 200)
                cfg = json.loads(corpo)
                self.assertEqual(cfg["modo"], modo)
                self.srv.parar()
                self.srv = None

    def test_modo_docker_com_segredo_do_ficheiro(self):
        f = self.raiz / "segredo"
        f.write_text(SEGREDO)
        self.arranca(OFFICE_MODO="docker", OFFICE_PROXY_SEGREDO_FILE=str(f))
        code, corpo = self.req("GET", "/api/config", SEGREDO)
        self.assertEqual((code, json.loads(corpo)["modo"]), (200, "docker"))


if __name__ == "__main__":
    unittest.main()
