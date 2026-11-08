"""Testes das aprovações (aprovacoes.py + rotas no server.py), tudo em pasta temporária (python3 -m unittest)."""
import hashlib
import http.client
import json
import os
import re
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import aprovacoes
import server
from tests import cfg

OFFICE_CFG = cfg.LEGADO

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=TZ).timestamp()
SECRET = "sk-" + "Q" * 24   # reconhecido por reader.mask()
NOME_FILA = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}\.json$")
CHAVES = ["v", "criado", "fonte", "ficheiro", "id", "accao", "nota", "sha256", "ip", "ua"]


def pedido(estado="pendente", criado="2026-10-23", titulo="# Campanha X\n\n", decisoes=None, extra=""):
    secao = "" if decisoes is None else "## Decisões\n" + "".join(f"{d}\n" for d in decisoes) + "\n"
    return (f"---\nestado: {estado}\ncriado: {criado}\nproduto: ebook\n---\n{titulo}"
            "## O quê\nPrimeiro parágrafo\ncontinua aqui.\n\nSegundo parágrafo.\n\n"
            "## Datas e orçamento\nDe 1/11 a 7/11. Tecto 145 €.\n\n" + secao + extra)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.mkt, self.geral, self.fila = root / "marketing", root / "geral", root / "fila"
        for d in (self.mkt, self.geral, self.fila):
            d.mkdir()
        self.dirs = {"marketing": self.mkt, "geral": self.geral}

    def tearDown(self):
        for d in (self.mkt, self.geral):
            os.chmod(d, 0o755)
        self.tmp.cleanup()

    def put(self, nome, text, root=None) -> Path:
        p = (root or self.mkt) / nome
        p.write_text(text, encoding="utf-8")
        return p

    def listar(self):
        return aprovacoes.listar(self.dirs, self.fila, TZ, NOW)

    def fila_json(self, **d):
        base = {"v": 1, "fonte": "marketing", "ficheiro": "a.md", "id": "P1", "accao": "aprovar", "nota": "",
                "sha256": "0" * 64}
        (self.fila / f"20261031T120000-{os.urandom(4).hex()}.json").write_text(json.dumps({**base, **d}))


class ParseTest(Base):
    def test_sem_fontes_ou_pasta_inexistente(self):
        self.assertEqual(aprovacoes.listar({}, self.fila, TZ, NOW), [])
        self.put("a.md", pedido())
        out = aprovacoes.listar({"x": Path(self.tmp.name) / "nao-existe", "marketing": self.mkt}, self.fila, TZ, NOW)
        self.assertEqual([a["fonte"] for a in out], ["marketing"])

    def test_so_md_regulares_na_raiz(self):
        self.put("a.md", pedido())
        self.put("README.md", pedido())
        self.put("nota.txt", pedido())
        (self.mkt / "provas").mkdir()
        self.put("b.md", pedido(), root=self.mkt / "provas")
        os.symlink(self.mkt / "a.md", self.mkt / "ligacao.md")
        self.put("sem-frontmatter.md", "# Só corpo\n\ntexto\n")
        self.put("frontmatter-aberto.md", "---\nestado: pendente\n# x\n")
        self.assertEqual([a["ficheiro"] for a in self.listar()], ["a.md"])

    def test_estados(self):
        casos = {"pendente": True, "Pendente   # pendente | aprovado": False,   # maiúsculas: como o aplicador
                 "parcial (MZ decidido 27/10; UE pendente)": True, "aprovado": False, "executado": False,
                 "recusado  # pendente | aprovado | recusado | executado": False, "": False}
        for i, (estado, conta) in enumerate(casos.items()):
            self.put(f"e{i}.md", pedido(estado=estado))
        got = {a["ficheiro"]: a["estado"] for a in self.listar()}
        self.assertEqual(got, {"e0.md": "pendente", "e2.md": "parcial (MZ decidido 27/10; UE pendente)"})

    def test_sem_secao_decisoes_da_asterisco(self):
        self.put("a.md", pedido())
        [a] = self.listar()
        self.assertEqual(a["decisoes"], [{"id": "*", "texto": "Campanha X", "valor": None, "feito": None, "data": None,
                                          "recebido": False, "aberta": True}])
        self.assertEqual(a["abertas"], 1)

    def test_titulos_antigos_nao_contam(self):
        extra = ("## Decisões do Ana (23/10/2026, via Claude Code)\n- [P1] aprovar 30 €\n\n"
                 "## Decisões pedidas ao Ana\n1. Isto?\n- [P2] aquilo\n\n## Decisões para si\n- [P3] x\n\n"
                 "## Decisões        (opcional)\n- [P4] y\n")
        self.put("a.md", pedido(extra=extra))
        [a] = self.listar()
        self.assertEqual([d["id"] for d in a["decisoes"]], ["*"])

    def test_secao_decisoes(self):
        linhas = ["- [P1] Tecto 145 € para a campanha", "texto solto ignorado", "- [P2] Publicar a 1/11",
                  "- [mau id com espaço] x", "- [P1] repetido ignorado", "- [P3] Já foi → aprovado (2026-10-24)",
                  "- [P4] Não → recusado (2026-10-25)"]
        p = self.put("a.md", pedido(decisoes=linhas, extra="## Depois\n- [P9] fora da secção\n"))
        p.write_text(p.read_text().replace("## Decisões\n", "## Decisões   \n"))   # espaços finais tolerados
        [a] = self.listar()
        self.assertEqual(a["decisoes"], [
            {"id": "P1", "texto": "Tecto 145 € para a campanha", "valor": "145 €", "feito": None, "data": None,
             "recebido": False, "aberta": True},
            {"id": "P2", "texto": "Publicar a 1/11", "valor": None, "feito": None, "data": None,
             "recebido": False, "aberta": True},
            {"id": "P3", "texto": "Já foi", "valor": None, "feito": "aprovado", "data": "2026-10-24",
             "recebido": False, "aberta": False},
            {"id": "P4", "texto": "Não", "valor": None, "feito": "recusado", "data": "2026-10-25",
             "recebido": False, "aberta": False}])
        self.assertEqual(a["abertas"], 2)

    def test_secao_sem_linhas_validas_da_asterisco(self):
        self.put("a.md", pedido(decisoes=["1. sem id", "- [] vazio"]))
        [a] = self.listar()
        self.assertEqual([d["id"] for d in a["decisoes"]], ["*"])

    def test_todas_feitas_nao_aparece(self):
        self.put("a.md", pedido(decisoes=["- [P1] x → aprovado (2026-10-24)", "- [P2] y → recusado (2026-10-24)"]))
        self.assertEqual(self.listar(), [])

    def test_valor(self):
        casos = {"tecto 145 €": "145 €", "a 4,75€ por dia": "4,75€", "até 1500 MT": "1500 MT",
                 "budget 20.5 USD": "20.5 USD", "ads $ 30": "$ 30", "ads $12,50 e 3 €": None,
                 "sem valor 12 dias": None}
        linhas = [f"- [V{i}] {t}" for i, t in enumerate(casos)]
        self.put("a.md", pedido(decisoes=linhas))
        [a] = self.listar()
        self.assertEqual([d["valor"] for d in a["decisoes"]], list(casos.values()))

    def test_asterisco_nao_procura_valor_no_corpo(self):
        self.put("a.md", pedido(titulo="# Gastar 300 €\n\n"))
        [a] = self.listar()
        self.assertEqual(a["decisoes"][0]["valor"], None)
        self.assertEqual(a["decisoes"][0]["texto"], "Gastar 300 €")

    def test_campos(self):
        p = self.put("2026-10-23-x.md", pedido())
        [a] = self.listar()
        self.assertEqual(a["fonte"], "marketing")
        self.assertEqual(a["titulo"], "Campanha X")
        self.assertEqual(a["criado"], "2026-10-23")
        self.assertEqual(a["idade_dias"], 8)
        self.assertEqual(a["resumo"], "Primeiro parágrafo\ncontinua aqui.")
        self.assertEqual(a["datas"], "De 1/11 a 7/11. Tecto 145 €.")
        self.assertEqual(a["sha256"], sha(p))
        self.assertNotIn("corpo", a)

    def test_titulo_resumo_e_datas_fallback(self):
        self.put("2026-10-23-sem-titulo.md", "---\nestado: pendente\ncriado: 2026-10-30\n---\n"
                 "## Contexto\nO resumo vem daqui.\n\nOutro.\n")
        [a] = self.listar()
        self.assertEqual(a["titulo"], "2026-10-23-sem-titulo")
        self.assertEqual(a["resumo"], "O resumo vem daqui.")
        self.assertIsNone(a["datas"])
        self.assertEqual(a["idade_dias"], 1)

    def test_criado_invalido_usa_mtime(self):
        p = self.put("a.md", pedido(criado="ontem"))
        mt = datetime(2026, 10, 28, 23, 30, tzinfo=TZ).timestamp()   # 22:30 UTC: o dia é o de Amesterdão
        os.utime(p, (mt, mt))
        [a] = self.listar()
        self.assertEqual((a["criado"], a["idade_dias"]), ("2026-10-28", 3))

    def test_idade_em_dias_de_calendario_amsterdao(self):
        self.put("a.md", pedido(criado="2026-10-30"))
        antes = datetime(2026, 10, 30, 23, 59, tzinfo=TZ).timestamp()
        depois = datetime(2026, 10, 31, 0, 1, tzinfo=TZ).timestamp()
        self.assertEqual(aprovacoes.listar(self.dirs, self.fila, TZ, antes)[0]["idade_dias"], 0)
        self.assertEqual(aprovacoes.listar(self.dirs, self.fila, TZ, depois)[0]["idade_dias"], 1)

    def test_cortes(self):
        longo = "palavra " * 400
        self.put("a.md", "---\nestado: pendente\ncriado: 2026-10-23\n---\n## O quê\n" + longo
                 + "\n\n## Datas e orçamento\n" + longo * 2 + "\n")
        [a] = self.listar()
        self.assertEqual(len(a["resumo"]), 400)
        self.assertEqual(len(a["datas"]), 1500)
        self.assertTrue(a["resumo"].endswith("…"))

    def test_mascara(self):
        self.put("a.md", pedido(titulo=f"# Título {SECRET}\n\n", decisoes=[f"- [P1] chave {SECRET}"],
                                extra="## O quê\nnão conta\n").replace("Primeiro parágrafo", SECRET)
                 .replace("De 1/11", SECRET))
        body = json.dumps(self.listar())
        self.assertNotIn(SECRET, body)
        self.assertIn("[segredo]", body)

    def test_ordem_mais_antigo_primeiro(self):
        self.put("b.md", pedido(criado="2026-10-20"))
        self.put("a.md", pedido(criado="2026-10-25"))
        self.put("c.md", pedido(criado="2026-10-20"), root=self.geral)
        self.assertEqual([(a["fonte"], a["ficheiro"]) for a in self.listar()],
                         [("geral", "c.md"), ("marketing", "b.md"), ("marketing", "a.md")])

    def test_html_sai_tal_e_qual(self):
        xss = "<script>alert(1)</script><img src=x onerror=alert(2)>"
        self.put("a.md", pedido(titulo=f"# {xss}\n\n", decisoes=[f"- [P1] {xss}"]))
        [a] = self.listar()
        self.assertEqual(a["titulo"], xss)
        self.assertEqual(a["decisoes"][0]["texto"], xss)


class RecebidosTest(Base):
    def test_recebido_e_sha_mudado(self):
        p = self.put("a.md", pedido(decisoes=["- [P1] x", "- [P2] y"]))
        self.fila_json(id="P1", sha256=sha(p))
        self.fila_json(id="P2", sha256=sha(p), accao="comentar", nota="hmm")   # comentar não fecha
        [a] = self.listar()
        self.assertEqual([(d["recebido"], d["aberta"]) for d in a["decisoes"]], [(True, False), (False, True)])
        self.assertEqual(a["abertas"], 1)
        p.write_text(p.read_text() + "\nmudou\n")
        [a] = self.listar()
        self.assertEqual([d["aberta"] for d in a["decisoes"]], [True, True])

    def test_asterisco_recebido_tira_o_pedido(self):
        p = self.put("a.md", pedido())
        self.fila_json(id="*", sha256=sha(p), accao="recusar", nota="não")
        self.assertEqual(self.listar(), [])

    def test_lixo_e_subpastas_ignorados(self):
        p = self.put("a.md", pedido(decisoes=["- [P1] x"]))
        (self.fila / "feito").mkdir()
        doc = {"v": 1, "fonte": "marketing", "ficheiro": "a.md", "id": "P1", "accao": "aprovar", "sha256": sha(p)}
        (self.fila / "feito" / "20261031T000000-00000000.json").write_text(json.dumps(doc))
        (self.fila / ".20261031T000000-11111111.json").write_text(json.dumps(doc))
        (self.fila / "20261031T000000-22222222.json").write_text("{não é json")
        (self.fila / "20261031T000000-33333333.json").write_text("[1, 2]")
        (self.fila / "20261031T000000-44444444.json").write_text(json.dumps({**doc, "nota": "x" * 20000}))
        self.assertEqual(self.listar()[0]["decisoes"][0]["aberta"], True)


class HttpTest(Base):
    def setUp(self):
        super().setUp()
        self.old = (server.APROVACOES, server.FILA)
        server.APROVACOES, server.FILA = self.dirs, self.fila
        server._cache["at"] = 0.0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"
        self.p = self.put("2026-10-23-x.md", pedido(decisoes=["- [P1] Tecto 145 €", "- [P2] Publicar",
                                                              "- [P3] Feito → aprovado (2026-10-24)"]))

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.APROVACOES, server.FILA = self.old
        server._cache["at"] = 0.0
        super().tearDown()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection(self.host, timeout=5)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"null")
        finally:
            c.close()

    def body(self, **kw):
        d = {"token": server.TOKEN, "fonte": "marketing", "ficheiro": "2026-10-23-x.md", "id": "P1",
             "accao": "aprovar", "nota": "", "sha256": sha(self.p)}
        d.update(kw)
        return {k: v for k, v in d.items() if v is not None}

    def post(self, data=None, raw=None, **headers):
        h = {"Content-Type": "application/json", "Origin": f"http://{self.host}"}
        h.update({k.replace("_", "-"): v for k, v in headers.items()})
        h = {k: v for k, v in h.items() if v is not None}
        payload = raw if raw is not None else json.dumps(data if data is not None else self.body()).encode()
        return self.req("POST", "/api/decisao", payload, h)

    def na_fila(self):
        return sorted(p.name for p in self.fila.iterdir() if p.is_file())

    def state(self):
        return self.req("GET", "/api/state")[1]

    # --- leitura ---
    def test_state_tem_aprovacoes_e_token_sem_corpo(self):
        self.put("b.md", pedido(extra="## Corpo\nTEXTO-SO-NO-CORPO\n"))
        st = self.state()
        self.assertEqual(st["token"], server.TOKEN)
        self.assertEqual([a["ficheiro"] for a in st["aprovacoes"]], ["2026-10-23-x.md", "b.md"])
        self.assertNotIn("TEXTO-SO-NO-CORPO", json.dumps(st))

    def test_get_aprovacao(self):
        self.p.write_text(self.p.read_text() + f"\n{SECRET}\n<script>alert(1)</script>\n")
        code, out = self.req("GET", "/api/aprovacao?fonte=marketing&ficheiro=2026-10-23-x.md")
        self.assertEqual(code, 200)
        self.assertEqual(set(out), {"fonte", "ficheiro", "titulo", "sha256", "corpo", "cortado"})
        self.assertEqual((out["titulo"], out["sha256"], out["cortado"]), ("Campanha X", sha(self.p), False))
        self.assertTrue(out["corpo"].startswith("# Campanha X"))
        self.assertNotIn("estado:", out["corpo"])
        self.assertNotIn(SECRET, out["corpo"])
        self.assertIn("<script>alert(1)</script>", out["corpo"])

    def test_get_aprovacao_cortado(self):
        self.put("grande.md", pedido(extra="x" * (210 * 1024)))
        code, out = self.req("GET", "/api/aprovacao?fonte=marketing&ficheiro=grande.md")
        self.assertEqual((code, out["cortado"], len(out["corpo"])), (200, True, 200 * 1024))

    def test_get_aprovacao_erros(self):
        self.put("feito.md", pedido(estado="executado"))
        os.symlink(self.p, self.mkt / "lig.md")
        self.put("README.md", pedido())
        casos = {"fonte=marketing&ficheiro=../x.md": 400, "fonte=marketing&ficheiro=a%2Fb.md": 400,
                 "fonte=marketing&ficheiro=x.txt": 400, "fonte=marketing": 400,
                 "fonte=outra&ficheiro=2026-10-23-x.md": 404, "fonte=marketing&ficheiro=nao.md": 404,
                 "fonte=marketing&ficheiro=feito.md": 404, "fonte=marketing&ficheiro=lig.md": 404,
                 "fonte=marketing&ficheiro=README.md": 404}
        for q, want in casos.items():
            with self.subTest(q=q):
                code, out = self.req("GET", "/api/aprovacao?" + q)
                self.assertEqual(code, want)
                self.assertEqual(out, {"error": "pedido inválido" if want == 400 else "não encontrado"})

    # --- envio ---
    def test_post_valido(self):
        self.state()   # enche a cache
        before = sha(self.p)
        code, out = self.post(User_Agent="UA" * 100, X_Forwarded_For=" 203.0.113.7 , 10.0.0.1")
        self.assertEqual((code, out), (202, {"ok": True}))
        [nome] = os.listdir(self.fila)
        self.assertRegex(nome, NOME_FILA)
        doc = json.loads((self.fila / nome).read_text())
        self.assertEqual(list(doc), CHAVES)
        self.assertEqual({k: doc[k] for k in CHAVES if k != "criado"},
                         {"v": 1, "fonte": "marketing", "ficheiro": "2026-10-23-x.md", "id": "P1", "accao": "aprovar",
                          "nota": "", "sha256": before, "ip": "10.0.0.1", "ua": ("UA" * 100)[:120]})
        self.assertIsNotNone(datetime.fromisoformat(doc["criado"]).tzinfo)
        self.assertEqual(sha(self.p), before)
        # cache invalidada: o estado seguinte já mostra «recebido»
        [a] = self.state()["aprovacoes"]
        self.assertEqual([(d["id"], d["recebido"]) for d in a["decisoes"]], [("P1", True), ("P2", False), ("P3", False)])

    def test_post_sem_xff_usa_ip_da_ligacao_e_html_tal_e_qual(self):
        xss = "<script>alert(1)</script>"
        self.assertEqual(self.post(self.body(accao="comentar", nota=xss), Origin=None,
                                   Sec_Fetch_Site="same-origin")[0], 202)
        [nome] = os.listdir(self.fila)
        raw = (self.fila / nome).read_text()
        self.assertIn(xss, raw)
        doc = json.loads(raw)
        self.assertEqual((doc["ip"], doc["nota"], doc["ua"]), ("127.0.0.1", xss, ""))

    def test_post_origin_igual_ao_host_com_path(self):
        self.assertEqual(self.post(Origin=f"HTTP://{self.host.upper()}")[0], 202)

    def test_403(self):
        casos = {"text/plain": {"Content_Type": "text/plain"},
                 "outro host": {"Origin": "https://evil.example"},
                 "Origin null": {"Origin": "null"},
                 "sem Origin nem Sec-Fetch-Site": {"Origin": None},
                 "Sec-Fetch-Site cross-site": {"Origin": None, "Sec_Fetch_Site": "cross-site"}}
        for nome, h in casos.items():
            with self.subTest(nome):
                code, out = self.post(**h)
                self.assertEqual(code, 403)
                self.assertIn(out["error"], ("Content-Type inválido", "origem não permitida"))
        for tok in (None, "", "errado", 123):
            with self.subTest(token=tok):
                self.assertEqual(self.post(self.body(token=tok)), (403, {"error": "token inválido, recarrega"}))
        self.assertEqual(self.na_fila(), [])

    def test_400(self):
        casos = {"fonte desconhecida": {"fonte": "outra"},
                 "ficheiro inválido": {"ficheiro": "../x.md"},
                 "id inválido": {"id": "tem espaço"},
                 "accao inválida": {"accao": "apagar"},
                 "sha256 inválido": {"sha256": "abc"},
                 "nota com mais de 2000 caracteres": {"nota": "x" * 2001},
                 "escreve o motivo": {"accao": "recusar", "nota": "   "}}
        for msg, kw in casos.items():
            with self.subTest(msg):
                self.assertEqual(self.post(self.body(**kw)), (400, {"error": msg}))
        for f in ("a/b.md", "/etc/x.md", "x.txt", "..md"):
            with self.subTest(ficheiro=f):
                self.assertEqual(self.post(self.body(ficheiro=f)), (400, {"error": "ficheiro inválido"}))
        self.assertEqual(self.post(self.body(accao="comentar", nota="")), (400, {"error": "escreve o motivo"}))
        self.assertEqual(self.post(self.body(accao="recusar", nota=None)), (400, {"error": "escreve o motivo"}))
        self.assertEqual(self.post(raw=b"{mau"), (400, {"error": "JSON inválido"}))
        self.assertEqual(self.post(raw=b"[1]"), (400, {"error": "JSON inválido"}))
        self.assertEqual(self.na_fila(), [])

    def test_surrogate_solto_da_400_sem_escrever(self):
        for campo in ("nota", "ficheiro", "fonte", "extra"):
            with self.subTest(campo):
                raw = json.dumps(self.body(**{"accao": "recusar", "nota": "motivo", campo: "a\ud800b"})).encode()
                self.assertIn(b"\\ud800", raw)
                self.assertEqual(self.post(raw=raw), (400, {"error": "JSON inválido"}))
        self.assertEqual(self.post(self.body(token="\udfff")), (403, {"error": "token inválido, recarrega"}))
        self.assertEqual(self.na_fila(), [])
        self.assertEqual(self.post(self.body(accao="recusar", nota="emoji 😀 ok"))[0], 202)   # par válido passa

    def test_nota_no_limite_passa(self):
        self.assertEqual(self.post(self.body(accao="recusar", nota="x" * 2000))[0], 202)

    def test_413_antes_de_ler(self):
        c = http.client.HTTPConnection(self.host, timeout=5)
        try:
            c.putrequest("POST", "/api/decisao")
            for k, v in (("Content-Type", "application/json"), ("Origin", f"http://{self.host}"),
                         ("Content-Length", "9000")):
                c.putheader(k, v)
            c.endheaders()   # o corpo nunca é enviado: o servidor tem de responder sem o ler
            r = c.getresponse()
            self.assertEqual((r.status, json.loads(r.read())), (413, {"error": "corpo grande demais"}))
        finally:
            c.close()
        self.assertEqual(self.post(raw=b" " * 8193)[0], 413)
        self.assertEqual(self.na_fila(), [])

    def test_409(self):
        actual = sha(self.p)
        self.put("feito.md", pedido(estado="aprovado", decisoes=["- [P1] x"]))
        casos = {"pedido não encontrado": {"ficheiro": "nao-existe.md"},
                 "pedido já não está pendente": {"ficheiro": "feito.md"},
                 "decisão inexistente": {"id": "P9"},
                 "decisão já tomada": {"id": "P3"},
                 "o pedido mudou, recarrega": {"sha256": "f" * 64}}
        for msg, kw in casos.items():
            with self.subTest(msg):
                self.assertEqual(self.post(self.body(**kw)), (409, {"error": msg}))
        # «*» não existe num pedido com ids
        self.assertEqual(self.post(self.body(id="*")), (409, {"error": "decisão inexistente"}))
        self.assertEqual(self.na_fila(), [])
        # duplicado: a 2.ª vez já está recebida; comentar não fecha
        self.assertEqual(self.post(self.body(id="P2", accao="comentar", nota="a"))[0], 202)
        self.assertEqual(self.post(self.body(id="P2", accao="comentar", nota="b"))[0], 202)
        self.assertEqual(self.post(self.body(id="P2", accao="recusar", nota="não"))[0], 202)
        self.assertEqual(self.post(self.body(id="P2")), (409, {"error": "decisão já recebida", "code": "recebida"}))
        self.assertEqual(len(self.na_fila()), 3)
        self.assertEqual(sha(self.p), actual)

    def test_pedido_mudou_depois_de_recebido_volta_a_abrir(self):
        old = sha(self.p)
        self.assertEqual(self.post()[0], 202)
        self.p.write_text(self.p.read_text() + "\nrevisto\n")
        self.assertEqual(self.post(self.body(sha256=old)), (409, {"error": "o pedido mudou, recarrega"}))
        self.assertEqual(self.post(self.body(sha256=sha(self.p)))[0], 202)

    def test_asterisco(self):
        p = self.put("so-titulo.md", pedido())
        self.assertEqual(self.post(self.body(ficheiro="so-titulo.md", id="*", sha256=sha(p)))[0], 202)
        self.assertEqual(self.post(self.body(ficheiro="so-titulo.md", id="*", sha256=sha(p)))[0], 409)

    def test_503_fila_indisponivel(self):
        server.FILA = Path(self.tmp.name) / "nao-existe"
        self.assertEqual(self.post(), (503, {"error": "fila indisponível"}))
        self.assertFalse(server.FILA.exists())

    @unittest.skipIf(os.geteuid() == 0, "root ignora as permissões")
    def test_503_fila_sem_escrita(self):
        os.chmod(self.fila, 0o555)
        try:
            self.assertEqual(self.post(), (503, {"error": "fila indisponível"}))
            self.assertEqual(self.na_fila(), [])
        finally:
            os.chmod(self.fila, 0o755)

    @unittest.skipIf(os.geteuid() == 0, "root ignora as permissões")
    def test_fontes_so_leitura_ficam_intactas(self):
        self.put("b.md", pedido(), root=self.geral)
        antes = {d: sorted((p.name, sha(p)) for p in d.iterdir()) for d in (self.mkt, self.geral)}
        for d in (self.mkt, self.geral):
            os.chmod(d, 0o555)
        with self.assertRaises(PermissionError):
            (self.mkt / "novo.json").write_text("x")
        self.assertEqual(self.post()[0], 202)
        self.assertEqual(self.post(self.body(fonte="geral", ficheiro="b.md", id="*",
                                             sha256=sha(self.geral / "b.md")))[0], 202)
        depois = {d: sorted((p.name, sha(p)) for p in d.iterdir()) for d in (self.mkt, self.geral)}
        self.assertEqual(antes, depois)
        self.assertEqual(len(self.na_fila()), 2)

    def test_sem_temporarios_e_outros_posts(self):
        self.assertEqual(self.post()[0], 202)
        self.assertEqual([n for n in os.listdir(self.fila) if n.startswith(".") or n.endswith(".tmp")], [])
        self.assertEqual(self.req("POST", "/api/outra", b"{}", {"Content-Type": "application/json"}),
                         (404, {"error": "não encontrado"}))

    def test_cache_de_5s_mantida_sem_post(self):
        self.state()
        self.put("novo.md", pedido())
        self.assertNotIn("novo.md", [a["ficheiro"] for a in self.state()["aprovacoes"]])   # sem POST, cache

    def test_post_a_meio_do_build_nao_deixa_a_cache_fresca(self):
        # o build lê a lista, o POST entra e invalida, o build grava dados de antes do POST: não podem ficar 5 s
        real, feito = aprovacoes.listar, []

        def listar(*a):
            out = real(*a)
            if not feito:
                feito.append(self.post())
            return out

        with mock.patch.object(aprovacoes, "listar", listar):
            antes = self.state()
        self.assertEqual(feito[0][0], 202)
        self.assertTrue(next(d for d in antes["aprovacoes"][0]["decisoes"] if d["id"] == "P1")["aberta"])
        self.assertEqual(server._cache["at"], 0.0)
        depois = next(d for d in self.state()["aprovacoes"][0]["decisoes"] if d["id"] == "P1")
        self.assertEqual((depois["aberta"], depois["recebido"]), (False, True))
        self.assertGreater(server._cache["at"], 0.0)   # sem POST pelo meio, volta a ficar fresca

    def test_gen_sobe_no_202_e_no_409(self):
        g = server._cache["gen"]
        self.assertEqual(self.post()[0], 202)
        self.assertEqual(self.post()[0], 409)
        self.assertEqual(self.post(self.body(sha256="x"))[0], 400)
        self.assertEqual(server._cache["gen"], g + 2)


if __name__ == "__main__":
    unittest.main()
