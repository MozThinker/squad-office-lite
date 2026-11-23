"""Conversa (ADR-023, revisão do guardião), lado do Office: /api/conversa/* dá sempre 404 (a Conversa vive no guardião,
noutra origem), o botão lê só o publico.json (válido, velho, inválido, symlink, grande), /api/config com o `url` do
`site`, CA-1 byte a byte com ela desligada e o server.py sem `import chat`. Só stdlib; NOW fixo."""
import ast
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import server
from tests import cfg as tcfg
from tests import test_server as base

ROOT = Path(__file__).resolve().parent.parent
EXEMPLO = (ROOT / "office.example.yaml").read_text(encoding="utf-8")
LIGADA = "conversa:\n  ativa: true\n  pasta: /srv/office-conversa\n  site: conversa.exemplo.com\n"
NOW = datetime(2026, 11, 23, 11, 30, tzinfo=timezone.utc).timestamp()
EM = "2026-11-23T11:30:00+00:00"
BOM = {"v": 1, "em": EM, "nome": "Sexta-feira", "ativa": True, "pendentes": 2, "dia_usd": 1.25}
DESLIGADA = {"ativa": False, "nome": None, "pendentes": 0, "dia_usd": None}
ROTAS = ("/api/conversa/estado", "/api/conversa/lista", "/api/conversa/eventos?conversa=x&desde=0",
         "/api/conversa/sessao", "/api/conversa/pedido", "/api/conversa/audio", "/api/conversa/prefs",
         "/api/conversa/outra")


def usar(tc, texto: str) -> dict:
    cfg = tcfg.ler(texto)
    tcfg.usar(tc, cfg)
    return cfg


class _Base(unittest.TestCase):
    def setUp(self):
        base.ServerTest.setUp(self)   # pastas vazias em tmp (sem herdar os testes do test_server)
        self.pasta = Path(self.tmp.name) / "conversa-publico"
        self.pasta.mkdir()
        self.old_pasta = server.CONVERSA_PUBLICO_DIR
        server.CONVERSA_PUBLICO_DIR = self.pasta

    def tearDown(self):
        server.CONVERSA_PUBLICO_DIR = self.old_pasta
        base.ServerTest.tearDown(self)

    def gravar(self, doc) -> None:
        (self.pasta / "publico.json").write_text(doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False),
                                                   encoding="utf-8")

    def http(self, path: str, method: str = "GET") -> tuple[int, dict]:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}", method=method,
                                     data=b"{}" if method == "POST" else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
        finally:
            httpd.shutdown()
            httpd.server_close()
            server._cache["at"] = 0.0


class RotasTest(_Base):
    def test_api_conversa_sempre_404(self):
        self.gravar(BOM)
        for bloco in ("", "conversa:\n  ativa: false\n", LIGADA):
            usar(self, EXEMPLO + bloco)
            for p in ROTAS:
                for metodo in ("GET", "POST"):
                    with self.subTest(bloco=bloco, p=p, metodo=metodo):
                        self.assertEqual(self.http(p, metodo), (404, {"error": "não encontrado"}))

    def test_api_state_http_leva_a_conversa(self):
        agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.gravar({**BOM, "em": agora})
        usar(self, EXEMPLO + LIGADA)
        server._cache["at"] = 0.0
        code, st = self.http("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(st["conversa"], {"ativa": True, "nome": "Sexta-feira", "pendentes": 2, "dia_usd": 1.25})

    def test_api_state_dia_usd_enorme_nao_da_500(self):   # R3: math.isfinite(10**400) lançava OverflowError
        agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.gravar(json.dumps({**BOM, "em": agora}).replace('"dia_usd": 1.25', '"dia_usd": 1' + "0" * 400))
        usar(self, EXEMPLO + LIGADA)
        server._cache["at"] = 0.0
        code, st = self.http("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(st["conversa"], DESLIGADA)


class ConfigTest(_Base):
    def test_ca1_config_e_state_byte_a_byte(self):
        self.gravar(BOM)   # desligada, nem o publico.json conta
        corpos = []
        for bloco in ("", "conversa:\n  ativa: false\n",
                      "conversa:\n  ativa: false\n  pasta: /srv/x\n  site: conversa.exemplo.com\n  tls: externo\n"):
            usar(self, EXEMPLO + bloco)
            corpos.append((server.CONFIG_BODY, json.dumps(server.build_state(NOW), ensure_ascii=False)))
        self.assertEqual(corpos[0], corpos[1])
        self.assertEqual(corpos[0], corpos[2])
        self.assertNotIn(b"conversa", corpos[0][0])
        self.assertNotIn('"conversa"', corpos[0][1])
        fixture = json.loads((ROOT / "tests" / "fixtures" / "api_config_exemplo.json").read_text(encoding="utf-8"))
        self.assertEqual(set(json.loads(corpos[0][0])) - {"modo"}, set(fixture) - {"modo"})

    def test_ligada_so_acrescenta_a_chave(self):
        usar(self, EXEMPLO)
        antes_cfg = json.loads(server.CONFIG_BODY)
        antes_st = server.build_state(NOW)
        usar(self, EXEMPLO + LIGADA)
        depois_cfg = json.loads(server.CONFIG_BODY)
        depois_st = server.build_state(NOW)
        self.assertEqual(depois_cfg.pop("conversa"), {"ativa": True, "url": "https://conversa.exemplo.com/"})
        self.assertEqual(depois_st.pop("conversa"), DESLIGADA)   # sem publico.json
        self.assertEqual((antes_cfg, antes_st), (depois_cfg, depois_st))
        # nunca a pasta, o tls nem o nome do assistente no config
        self.assertNotIn(b"/srv/office-conversa", server.CONFIG_BODY)
        self.assertNotIn(b"caddy", server.CONFIG_BODY)

    def test_externo_sem_site_url_nulo(self):
        usar(self, EXEMPLO + "conversa:\n  ativa: true\n  pasta: /srv/office-conversa\n  tls: externo\n")
        self.assertEqual(json.loads(server.CONFIG_BODY)["conversa"], {"ativa": True, "url": None})
        usar(self, EXEMPLO + "conversa:\n  ativa: true\n  pasta: /srv/o\n  tls: externo\n  site: c.exemplo.com\n")
        self.assertEqual(json.loads(server.CONFIG_BODY)["conversa"], {"ativa": True, "url": "https://c.exemplo.com/"})


class PublicoTest(_Base):
    def setUp(self):
        super().setUp()
        usar(self, EXEMPLO + LIGADA)

    def estado(self, now=NOW) -> dict:
        return server.build_state(now)["conversa"]

    def test_valido(self):
        self.gravar(BOM)
        self.assertEqual(self.estado(), {"ativa": True, "nome": "Sexta-feira", "pendentes": 2, "dia_usd": 1.25})
        self.gravar({**BOM, "ativa": False, "pendentes": 0, "dia_usd": None, "nome": "助手"})
        self.assertEqual(self.estado(), {"ativa": False, "nome": "助手", "pendentes": 0, "dia_usd": None})

    def test_limites_validos(self):
        for doc in ({**BOM, "nome": "a" * 40, "pendentes": 10000, "dia_usd": 0},
                    {**BOM, "nome": "A", "pendentes": 0, "dia_usd": 0.0}):
            with self.subTest(doc=doc):
                self.gravar(doc)
                self.assertEqual(self.estado(), {k: doc[k] for k in ("ativa", "nome", "pendentes", "dia_usd")})
        # 4 KB exactos ainda passam
        texto = json.dumps(BOM)
        self.gravar(texto + " " * (server.CONVERSA_PUBLICO_MAX - len(texto)))
        self.assertTrue(self.estado()["ativa"])

    def test_velho_dos_dois_lados_dos_120_s(self):
        self.gravar(BOM)
        for dt, ativa in ((119, True), (120, True), (121, False), (-120, True), (-121, False)):
            with self.subTest(dt=dt):
                e = self.estado(NOW + dt)
                self.assertIs(e["ativa"], ativa)
                if not ativa:   # velho: fica o nome, sem pendentes nem gasto
                    self.assertEqual(e, {**DESLIGADA, "nome": "Sexta-feira"})

    def test_em_nos_limites_do_calendario_e_velho(self):
        for em in ("9999-12-31T23:59:59-05:00", "0001-01-01T00:00:00+05:00"):
            with self.subTest(em=em):
                self.gravar({**BOM, "em": em})
                self.assertEqual(self.estado(), {**DESLIGADA, "nome": "Sexta-feira"})

    def test_em_noutro_fuso(self):
        em = (datetime.fromtimestamp(NOW, timezone(timedelta(hours=2)))).isoformat()
        self.gravar({**BOM, "em": em})
        self.assertTrue(self.estado()["ativa"])

    def test_sem_ficheiro(self):
        self.assertEqual(self.estado(), DESLIGADA)
        server.CONVERSA_PUBLICO_DIR = Path(self.tmp.name) / "nao-existe"
        self.assertEqual(self.estado(), DESLIGADA)

    def test_so_le_publico_json(self):
        self.gravar(BOM)
        (self.pasta / "outro.json").write_text('{"segredo": "CORPO-X"}', encoding="utf-8")
        pasta = os.path.realpath(self.pasta)
        abertos = []

        def dentro(p) -> bool:
            return isinstance(p, (str, bytes, os.PathLike)) and os.path.realpath(os.fsdecode(p)).startswith(pasta)

        def proibido(nome, original):
            def f(p=".", *a, **k):
                if dentro(p):
                    raise AssertionError(f"{nome} da pasta do publico.json")
                return original(p, *a, **k)
            return f

        abrir = os.open

        def os_open(p, *a, **k):
            if dentro(p):
                abertos.append(os.fsdecode(p))
            return abrir(p, *a, **k)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(os, "open", os_open)
            mp.setattr(os, "scandir", proibido("scandir", os.scandir))
            mp.setattr(os, "listdir", proibido("listdir", os.listdir))
            e = self.estado()
        self.assertTrue(e["ativa"])
        self.assertEqual(abertos, [str(self.pasta / "publico.json")])


INVALIDOS = {
    "nao_json": "{",
    "nao_objecto": [BOM],
    "v2": {**BOM, "v": 2},
    "v_bool": {**BOM, "v": True},
    "em_sem_fuso": {**BOM, "em": "2026-11-23T11:30:00"},
    "em_invalido": {**BOM, "em": "agora"},
    "em_numero": {**BOM, "em": 1},
    "nome_vazio": {**BOM, "nome": ""},
    "nome_espacos": {**BOM, "nome": "   "},
    "nome_41": {**BOM, "nome": "a" * 41},
    "nome_controlo": {**BOM, "nome": "a\nb"},
    "nome_c1": {**BOM, "nome": "a\x85b"},
    "nome_nao_str": {**BOM, "nome": 5},
    "ativa_str": {**BOM, "ativa": "true"},
    "ativa_int": {**BOM, "ativa": 1},
    "pendentes_neg": {**BOM, "pendentes": -1},
    "pendentes_10001": {**BOM, "pendentes": 10001},
    "pendentes_float": {**BOM, "pendentes": 1.0},
    "pendentes_bool": {**BOM, "pendentes": True},
    "dia_usd_neg": {**BOM, "dia_usd": -0.01},
    "dia_usd_str": {**BOM, "dia_usd": "1"},
    "dia_usd_bool": {**BOM, "dia_usd": False},
    "dia_usd_enorme": json.dumps(BOM).replace('"dia_usd": 1.25', '"dia_usd": 1' + "0" * 400),
    "dia_usd_inf": '{"v": 1, "em": "%s", "nome": "x", "ativa": true, "pendentes": 0, "dia_usd": Infinity}' % EM,
    "chave_a_mais": {**BOM, "chave": "x"},
    "chave_a_menos": {k: v for k, v in BOM.items() if k != "dia_usd"},
}


@pytest.mark.parametrize("caso", [*INVALIDOS, "symlink", "grande", "pasta", "fifo"])
def test_invalido_desligada(caso, tmp_path):
    pasta = tmp_path / "conversa-publico"
    pasta.mkdir()
    f = pasta / "publico.json"
    if caso == "symlink":
        fora = tmp_path / "fora.json"
        fora.write_text(json.dumps(BOM), encoding="utf-8")
        f.symlink_to(fora)
    elif caso == "grande":
        texto = json.dumps(BOM)
        f.write_text(texto + " " * (server.CONVERSA_PUBLICO_MAX + 1 - len(texto)), encoding="utf-8")
    elif caso == "pasta":
        f.mkdir()
    elif caso == "fifo":
        os.mkfifo(f)
    else:
        doc = INVALIDOS[caso]
        f.write_text(doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server, "CONVERSA_PUBLICO_DIR", pasta)
        assert server.conversa_publico(NOW) == DESLIGADA


def test_omissao_da_pasta():
    assert server.CONVERSA_PUBLICO_DIR == Path(os.environ.get("CONVERSA_PUBLICO_DIR") or "/data/conversa-publico")


def test_server_sem_chat():
    # o Office já não tem a Conversa: nem `import chat`, nem as pastas da fila e das conversas, nem sessões
    texto = (ROOT / "server.py").read_text(encoding="utf-8")
    arvore = ast.parse(texto)
    importados = {a.name for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names}
    importados |= {n.module for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)}
    assert "chat" not in importados
    nomes = {n.id for n in ast.walk(arvore) if isinstance(n, ast.Name)}
    assert "chat" not in nomes
    for proibido in ("CONVERSA_FILA", "CONVERSAS_DIR", "CONVERSA_PREFS", "CONVERSA_TLS_TESTE", "X-Office-Token",
                     "SESSOES", "BLOQUEIO", "_conversa_"):
        assert proibido not in texto, proibido
    copy = next(ln for ln in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
                if ln.startswith("COPY server.py "))
    assert " chat.py " not in copy
