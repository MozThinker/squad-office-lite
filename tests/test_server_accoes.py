"""ADR-022: /api/state ganha "accoes" (opcional) a partir de <ACCOES_SAUDE_DIR>/saude.json do serviço accoes
(CA-13…CA-16). Só stdlib; dados falsos em pasta temporária; NOW fixo."""
import json
import os
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import server
from tests import cfg
from tests import test_server as base

OFFICE_CFG = {**cfg.LEGADO, "fuso": "Europe/Lisbon"}   # em Novembro = UTC

NOW = datetime(2026, 11, 18, 13, 23, tzinfo=timezone.utc).timestamp()
EM = "2026-11-18T13:23:00+00:00"
REGISTO = "registo de squads ilegível; nada colhido nesta passagem"
BOM = {"v": 1, "em": EM, "avisos": [REGISTO]}


class _Base(unittest.TestCase):
    def setUp(self):
        base.ServerTest.setUp(self)   # pastas vazias em tmp (sem herdar os testes do test_server)
        self.pasta = Path(self.tmp.name) / "accoes-saude"
        self.pasta.mkdir()
        self.old_pasta = server.ACCOES_SAUDE_DIR
        server.ACCOES_SAUDE_DIR = self.pasta

    def tearDown(self):
        server.ACCOES_SAUDE_DIR = self.old_pasta
        base.ServerTest.tearDown(self)

    def gravar(self, doc) -> None:
        (self.pasta / "saude.json").write_text(doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False),
                                                 encoding="utf-8")

    def estado(self, now=NOW) -> dict:
        return server.build_state(now)


class SaudeTest(_Base):
    def test_sem_ficheiro_sem_chave(self):  # CA-15
        self.assertNotIn("accoes", self.estado())
        server.ACCOES_SAUDE_DIR = Path(self.tmp.name) / "nao-existe"
        self.assertNotIn("accoes", self.estado())

    def test_valido(self):  # CA-16
        self.gravar(BOM)
        self.assertEqual(self.estado()["accoes"], {"avisos": [REGISTO], "em": EM, "parado": False, "desde": "13:23"})
        self.gravar({**BOM, "avisos": []})
        self.assertEqual(self.estado()["accoes"], {"avisos": [], "em": EM, "parado": False, "desde": "13:23"})
        self.assertEqual(list(self.estado()["accoes"]), ["avisos", "em", "parado", "desde"])

    def test_parado_dos_dois_lados_dos_10_min(self):  # CA-16: NOW − em > 600 s
        self.gravar(BOM)
        for dt, parado in ((599, False), (600, False), (601, True)):
            with self.subTest(dt=dt):
                self.assertIs(self.estado(NOW + dt)["accoes"]["parado"], parado)

    def test_em_no_futuro_nao_parado(self):
        self.gravar(BOM)
        a = self.estado(NOW - 3600)["accoes"]
        self.assertEqual((a["parado"], a["avisos"]), (False, [REGISTO]))

    def test_parado_esvazia_avisos(self):
        self.gravar(BOM)
        self.assertEqual(self.estado(NOW + 3600)["accoes"],
                         {"avisos": [], "em": EM, "parado": True, "desde": "13:23"})

    def test_desde_no_fuso_da_config(self):
        cfg.usar(self, {**cfg.LEGADO, "fuso": "Africa/Maputo"})   # UTC+2
        self.gravar({**BOM, "em": "2026-11-18T14:23:00+01:00"})
        a = self.estado()["accoes"]
        self.assertEqual((a["desde"], a["em"], a["parado"]), ("15:23", "2026-11-18T14:23:00+01:00", False))

    def test_api_state_http(self):
        agora = datetime.fromtimestamp(time.time(), timezone.utc).isoformat(timespec="seconds")
        self.gravar({**BOM, "em": agora})
        server._cache["at"] = 0.0
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/state", timeout=5) as r:
                st = json.loads(r.read())
        finally:
            httpd.shutdown()
            httpd.server_close()
            server._cache["at"] = 0.0
        self.assertEqual(st["accoes"]["avisos"], [REGISTO])
        self.assertEqual((st["accoes"]["em"], st["accoes"]["parado"]), (agora, False))

    def test_so_le_saude_json(self):  # CA-13: não lista a pasta nem abre mais nada dela
        self.gravar(BOM)
        antes = self.estado()
        (self.pasta / "vendas" / "mensagens").mkdir(parents=True)
        (self.pasta / "vendas" / "vista.json").write_text('{"v": 1, "segredo": "CORPO-X"}', encoding="utf-8")
        (self.pasta / "vendas" / "mensagens" / "m1.json").write_text('{"corpo": "CORPO-X"}', encoding="utf-8")
        pasta = os.path.realpath(self.pasta)
        abertos = []

        def dentro(p) -> bool:
            return isinstance(p, (str, bytes, os.PathLike)) and os.path.realpath(os.fsdecode(p)).startswith(pasta)

        def proibido(nome, original):
            def f(p=".", *a, **k):
                if dentro(p):
                    raise AssertionError(f"{nome} da pasta accoes-saude")
                return original(p, *a, **k)
            return f

        abrir = os.open

        def os_open(p, *a, **k):
            if dentro(p):
                abertos.append(os.fsdecode(p))
            return abrir(p, *a, **k)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(os, "scandir", proibido("scandir", os.scandir))
            mp.setattr(os, "listdir", proibido("listdir", os.listdir))
            mp.setattr(os, "open", os_open)
            depois = self.estado()
        self.assertEqual(depois["accoes"], antes["accoes"])
        self.assertEqual(abertos, [str(self.pasta / "saude.json")])
        self.assertNotIn("CORPO-X", json.dumps(depois))


INVALIDOS = {
    "nao_json": "{",
    "v2": {**BOM, "v": 2},
    "v_bool": {**BOM, "v": True},
    "em_sem_fuso": {**BOM, "em": "2026-11-18T13:23:00"},
    "em_invalido": {**BOM, "em": "ontem"},
    "em_ano_9999": {**BOM, "em": "9999-12-31T23:59:59-05:00"},
    "em_ano_1": {**BOM, "em": "0001-01-01T00:00:00+05:00"},
    "avisos_nao_lista": {**BOM, "avisos": REGISTO},
    "11_avisos": {**BOM, "avisos": [f"aviso {i}" for i in range(11)]},
    "aviso_201": {**BOM, "avisos": ["x" * 201]},
    "aviso_nao_str": {**BOM, "avisos": [REGISTO, 3]},
    "controlo": {**BOM, "avisos": ["linha\nnova"]},
    "chave_a_mais": {**BOM, "extra": 1},
    "chave_a_menos": {"v": 1, "em": EM},
}


@pytest.mark.parametrize("caso", [*INVALIDOS, "symlink", "grande", "pasta"])
def test_invalido_ignorado(caso, tmp_path):  # CA-14 (e CA-15: inválido ⇒ sem a chave)
    pasta = tmp_path / "accoes-saude"
    pasta.mkdir()
    saude = pasta / "saude.json"
    if caso == "symlink":
        fora = tmp_path / "fora.json"
        fora.write_text(json.dumps(BOM), encoding="utf-8")
        saude.symlink_to(fora)
    elif caso == "grande":
        texto = json.dumps(BOM)
        saude.write_text(texto + " " * (server.ACCOES_SAUDE_MAX + 1 - len(texto)), encoding="utf-8")
    elif caso == "pasta":
        saude.mkdir()
    else:
        doc = INVALIDOS[caso]
        saude.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server, "ACCOES_SAUDE_DIR", pasta)
        assert server.accoes_saude(NOW) is None


def test_limites_validos(tmp_path):  # os limites exactos ainda passam (10 avisos de 200, 8 KB)
    pasta = tmp_path / "accoes-saude"
    pasta.mkdir()
    doc = {"v": 1, "em": EM, "avisos": [f"{i}" + "é" * 199 for i in range(10)]}
    texto = json.dumps(doc, ensure_ascii=False)
    assert len(texto.encode()) <= server.ACCOES_SAUDE_MAX
    (pasta / "saude.json").write_text(texto + " " * (server.ACCOES_SAUDE_MAX - len(texto.encode())),
                                        encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server, "ACCOES_SAUDE_DIR", pasta)
        assert server.accoes_saude(NOW)["avisos"] == doc["avisos"]


def test_omissao_da_pasta():
    assert server.ACCOES_SAUDE_DIR == Path(os.environ.get("ACCOES_SAUDE_DIR") or "/data/accoes-saude")
