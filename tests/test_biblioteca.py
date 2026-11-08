"""Testes da Biblioteca (biblioteca.py + rotas no server.py), tudo em pasta temporária (python3 -m unittest).

Matriz do PLAN §5: A allowlist/denylist · B traversal/S4 · C symlinks · D .tasks · E agrupamento · F produto/tipo ·
G estado · H datas/cache · I servir · J Range · K zip · L miniaturas · M só leitura · N regressão.
Tokens falsos por concatenação (gitleaks).
"""
import gzip
import http.client
import io
import json
import os
import random
import socket
import tempfile
import threading
import time
import unittest
import warnings
import zipfile
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote
from zoneinfo import ZoneInfo

import biblioteca
import server
from tests import cfg

OFFICE_CFG = cfg.LEGADO

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 11, 1, 22, 0, tzinfo=TZ).timestamp()
H = 3600
GH = "gh" + "p_" + "A1b2C3d4" * 5
BEARER = "Bearer " + "abcDEF123456" * 2
LOTE = "rascunhos/media/2026-11-01-status-ana"
NAO = {"error": "não encontrado"}

PEDIDO = """---
estado: {estado}
criado: {criado}
produto: {produto}
revisor: Aprovado — rascunhos/whatsapp-status/2026-11-01-revisao.md · ok
---
# Status do Ana

![01](../rascunhos/media/2026-11-01-status-ana/final/status-01-quem-9x16.jpg)
`rascunhos/media/2026-11-01-status-ana/final/status-06-convite-9x16.mp4`.
{extra}
"""


def pedido(estado="aprovado", criado="2026-11-01", produto="loja", extra="") -> str:
    return PEDIDO.format(estado=estado, criado=criado, produto=produto, extra=extra)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = NOW
        self.raizes = {}
        for nome in ("rascunhos", "relatorios", "rnd-estudos", "propostas", "mkt-knowledge"):
            (self.root / nome).mkdir()
            self.raizes[nome] = self.root / nome
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.aprov = self.root / "aprov-mkt"
        self.aprov.mkdir()
        self.mini_dir = self.root / "mini"
        self.mini_dir.mkdir()
        self.lote()
        self.idx = self.novo_indice()

    def tearDown(self):
        for d, _, fs in os.walk(self.root):
            os.chmod(d, 0o755)
        self.tmp.cleanup()

    def novo_indice(self):
        return biblioteca.Indice(self.raizes, self.tasks, {"marketing": self.aprov}, clock=lambda: self.now)

    def w(self, rel: str, data=b"x", idade: float = 2 * H) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
        os.utime(p, (self.now - idade, self.now - idade))
        return p

    def lote(self):
        L = LOTE
        self.w(f"{L}/folha.md", "# Folha: Estado do WhatsApp pessoal (6 peças)\n\n"
               "Copy: `rascunhos/whatsapp-status/2026-11-01-textos.md`.\n")
        for n in ("status-01-quem-9x16.jpg", "status-01-quem-v2-9x16.jpg", "status-05-preco-9x16.jpg"):
            self.w(f"{L}/final/{n}", b"\xff\xd8JPEG" + n.encode())
        self.w(f"{L}/final/status-06-convite-9x16.mp4", b"0123456789")
        self.w(f"{L}/final/status-01-quem-9x16.jpg.json", "{}")
        self.w(f"{L}/final/status-01-quem-9x16-semelhanca/parecida-1.jpg", b"jpg")
        self.w(f"{L}/final/status-06-convite-9x16-fotogramas/folha.png", b"png")
        self.w(f"{L}/final/status-06-convite-9x16-fotogramas/f-002.png", b"png")
        self.w(f"{L}/final/status-06-convite-9x16-fotogramas/f-001.png", b"png")
        self.w(f"{L}/prompts/status-01.prompt.md", "# prompt")
        self.w(f"{L}/video/voz-06.wav", b"RIFF")
        self.w(f"{L}/video/chave.jpg", b"jpg")
        self.w(f"{L}/video/final/x.mp4", b"mp4")
        self.w(f"{L}/video/x.params.json", "{}")
        self.w("rascunhos/whatsapp-status/2026-11-01-textos.md", f"# Textos\n\n> legenda\n\ntoken {GH}\n{BEARER}\n")
        self.w("rascunhos/whatsapp-status/2026-11-01-revisao.md", "# Revisão\n")
        self.w("rascunhos/whatsapp-status/2026-11-01-parecer-juridico.md", "# Parecer\n")
        self.w("aprov-mkt/2026-11-01-status.md", pedido(
            extra="juridico: rascunhos/whatsapp-status/2026-11-01-parecer-juridico.md\n"))

    def snap(self):
        return self.idx.get()

    def item(self, iid):
        return self.snap().por_id[iid]


# --- A · allowlist / denylist ---------------------------------------------------------------------------------------

class RegrasTest(Base):
    def test_classificar_allowlist(self):
        c = biblioteca.classificar
        for nome, tipo in (("a.JPG", "imagem"), ("a.webp", "imagem"), ("a.m4v", "video"), ("a.opus", "audio"),
                           ("a.md", "doc"), ("a.csv", "doc"), ("a.pdf", "pdf")):
            self.assertEqual(c("rascunhos", ("x", nome)), tipo, nome)
        for nome in ("a.json", "a.html", "a.htm", "a.svg", "a.js", "prazos.md.tmp-ignore", "README", "a.exe"):
            self.assertIsNone(c("rascunhos", (nome,)), nome)

    def test_classificar_denylist(self):
        c = biblioteca.classificar
        for nome in (".env", "x.env", ".env.local", "chave.key", "cert.pem", "a.p12", "b.pfx", "id_rsa", "id_rsa.pub",
                     "id_ed25519", "credentials.md", "segredo.txt", "MyPassword.txt", "passwd.md", "my-secret.md",
                     ".oculto.md", ".secao-v2b.md"):
            self.assertIsNone(c("rascunhos", (nome,)), nome)
        for pasta in (".git", ".state", "dados", "node_modules", "__pycache__", ".venv"):
            self.assertIsNone(c("rascunhos", (pasta, "a.md")), pasta)
        for parte in ("", ".", "..", "a\\b", "a\x00"):
            self.assertIsNone(c("rascunhos", ("x", parte, "a.md")), repr(parte))
        self.assertIsNone(c("rascunhos", tuple("abcdefgh") + ("a.md",)))   # profundidade > 8

    def test_media_so_em_rascunhos(self):
        self.assertIsNone(biblioteca.classificar("rnd-estudos", ("x.jpg",)))
        self.assertIsNone(biblioteca.classificar("relatorios", ("x.mp4",)))
        self.assertEqual(biblioteca.classificar("rnd-estudos", ("x.pdf",)), "pdf")

    def test_dirs_da_config(self):
        # fonte → <BIBLIOTECA_DIR>/<pasta>; pastas que não existem ficam de fora
        self.assertEqual(biblioteca.dirs(cfg.LEGADO, self.root),
                         {n: self.root / n for n in ("rascunhos", "relatorios", "mkt-knowledge", "rnd-estudos",
                                                     "propostas")})
        self.assertEqual(biblioteca.dirs(cfg.LEGADO, self.root / "nao-existe"), {})

    def test_configurar(self):
        self.assertEqual(biblioteca.RAIZES_MEDIA, ("rascunhos",))
        self.assertEqual(biblioteca.TIPO_RAIZ["rnd-radar"], "estudos")
        cfg.usar(self, cfg.MULTI)   # fonte «material» com media; sem tipo
        self.assertEqual((biblioteca.RAIZES, biblioteca.RAIZES_MEDIA, biblioteca.TIPO_RAIZ),
                         (("material",), ("material",), {}))
        self.assertEqual(biblioteca.classificar("material", ("a.jpg",)), "imagem")
        self.assertIsNone(biblioteca.classificar("rascunhos", ("a.jpg",)))
        self.assertEqual(biblioteca.citacoes("ver material/media/x/a.jpg e rascunhos/b.jpg"),
                         [("ver material/media/x/a.jpg e rascunhos/b.jpg", "material/media/x/a.jpg")])
        self.assertEqual(biblioteca.produtos_kw("loja online e blog"), ["loja", "blog"])
        cfg.usar(self, cfg.OPS)   # sem fontes: nada de media nem citações
        self.assertEqual((biblioteca.RAIZES, biblioteca.citacoes("rascunhos/a.jpg material/b.jpg")), ((), []))

    def test_varrimento_ignora_negados(self):
        for rel in ("rascunhos/a.json", "rascunhos/a.html", "rascunhos/a.svg", "rascunhos/a.js",
                    "rascunhos/prazos.md.tmp-ignore", "rascunhos/.env", "rascunhos/x.env", "rascunhos/.env.local",
                    "rascunhos/chave.key", "rascunhos/cert.pem", "rascunhos/credentials.md", "rascunhos/segredo.txt",
                    "rascunhos/.oculto.md", "rascunhos/.git/a.md", "rascunhos/.state/a.md", "rascunhos/dados/a.md",
                    "rascunhos/node_modules/a.md", "rascunhos/__pycache__/a.md", "rnd-estudos/x.jpg"):
            self.w(rel, "# x")
        self.w("rascunhos/ok.md", "# ok")
        chaves = set(self.novo_indice().get().ficheiros)
        self.assertIn("rascunhos/ok.md", chaves)
        for k in chaves:
            self.assertFalse(k.endswith((".json", ".html", ".svg", ".js", ".env", ".key", ".pem", "tmp-ignore")), k)
            self.assertNotRegex(k, r"/\.|dados/|node_modules|__pycache__|credential|segredo")
        self.assertNotIn("rnd-estudos/x.jpg", chaves)

    def test_limite_de_entradas(self):
        for i in range(6):
            self.w(f"relatorios/r{i}.md", "# r")
        with mock.patch.object(biblioteca, "ENTRADAS_MAX", 3), mock.patch("sys.stderr", new=io.StringIO()) as err:
            fs = biblioteca.varrer("relatorios", self.root / "relatorios")
        self.assertEqual(len(fs), 3)
        self.assertIn("varrimento cortado", err.getvalue())

    def test_raiz_nome_e_rotulo(self):
        for n in ("x.md", "x.copy.md", "x.juridico.md", "x.md.revisao.md", "x.md.juridico.md", "x.revisao-2.md",
                  "x.parecer-juridico.md"):
            self.assertEqual(biblioteca.raiz_nome(n), "x", n)
        self.assertEqual(biblioteca.raiz_nome("x-pt-revisao.md"), "x-pt-revisao")
        r = biblioteca.rotulo
        self.assertEqual(r("a.parecer-juridico.md"), "Parecer jurídico")
        self.assertEqual(r("a.md.revisao.md"), "Revisão")
        self.assertEqual(r("a.copy.md"), "Copy")
        self.assertEqual(r("folha.md", True), "Folha")
        self.assertEqual(r("folha.md"), "Texto")
        self.assertEqual([r(n) for n in ("SPEC.md", "PLAN.md", "QA.md")], ["Spec", "Plano", "QA"])

    def test_citacoes(self):
        t = ("![01](../rascunhos/media/l/final/a.jpg)\n`rascunhos/x/b.md`.\nver rascunhos/x/c.md, e "
             "«rascunhos/x/d.md»; e (rascunhos/x/e.md) | rascunhos/x/f.md:\nprefixo-rascunhos/x/g.md\n"
             "revisor: ok — rascunhos/x/h.md · fim\n**rascunhos/media/l/** e rascunhos/media/l")
        cs = [c for _, c in biblioteca.citacoes(t)]
        self.assertEqual(cs, ["rascunhos/media/l/final/a.jpg", "rascunhos/x/b.md", "rascunhos/x/c.md",
                              "rascunhos/x/d.md", "rascunhos/x/e.md", "rascunhos/x/f.md", "rascunhos/x/h.md",
                              "rascunhos/media/l/**", "rascunhos/media/l"])
        self.assertEqual(biblioteca.citacoes("a\nb rascunhos/x.md")[0][0], "b rascunhos/x.md")

    def test_intervalo(self):
        i = biblioteca.intervalo
        self.assertEqual(i("bytes=0-1", 10), (0, 1))
        self.assertEqual(i("bytes=5-", 10), (5, 9))
        self.assertEqual(i("bytes=-3", 10), (7, 9))
        self.assertEqual(i("bytes=-30", 10), (0, 9))
        self.assertEqual(i("bytes=2-100", 10), (2, 9))
        self.assertEqual(i("bytes=10-", 10), "416")
        self.assertEqual(i("bytes=-0", 10), "416")
        for h in (None, "", "bytes=0-1,4-5", "bytes=a-b", "bytes=-", "bytes=5-2", "items=0-1", "lixo"):
            self.assertIsNone(i(h, 10), h)
        # revisão de segurança: milhares de dígitos não podem levantar ValueError (limite do int) → Range inválido
        n = "9" * 5000
        for h in (f"bytes={n}-", f"bytes=-{n}", f"bytes=0-{n}", f"bytes={n}-{n}", "bytes=" + "9" * 20 + "-"):
            self.assertIsNone(i(h, 10), h[:20])
        self.assertEqual(i("bytes=0-" + "9" * 19, 10), (0, 9))
        self.assertEqual(i("bytes=" + "9" * 19 + "-", 10), "416")
        self.assertEqual(i("bytes=-" + "9" * 19, 10), (0, 9))

    def test_estado_pedido(self):
        e = biblioteca.estado_pedido
        self.assertEqual([e(v) for v in ("aprovado", "executado", "recusado", "pendente", "parcial (1 de 2)",
                                         "bloqueado: IVA", "Pendente", "outro", "")],
                         ["aprovado", "executado", "recusado", "pendente", "pendente", "bloqueado", None, None, None])

    def test_cabecalho(self):
        t, r, f = biblioteca.cabecalho("---\nprodutos: x\nproduto: ebook # c\n---\n\n# Título\n\n| a |\n- l\n"
                                       "> c\n1. n\n**Resposta curta:** sim.\n")
        self.assertEqual((t, r, f["produto"]), ("Título", "Resposta curta: sim.", "ebook"))
        self.assertEqual(biblioteca.cabecalho("sem título\n"), (None, "sem título", {}))

    def test_cabecalho_resumo_antes_do_titulo_e_sem_metadados(self):
        # «Adenda» real (rnd/estudos): o resumo vem antes do H1 e o 1.º parágrafo depois dele é «Data: …»
        t, r, _ = biblioteca.cabecalho(
            "**Resposta curta: sim, mas só como piloto.** O diferencial existe: não encontrámos nenhuma bilheteira.\n\n"
            "# Adenda R&D: bilhetes com QR\n\nData: 2026-11-01. Pedido do Ana (1/11): reavaliar o «Bilhete QR»\n"
            "`estudos/x.md`. O ângulo agora é outro.\n\nQuem fez o quê.\n")
        self.assertEqual((t, r), ("Adenda R&D: bilhetes com QR", "Resposta curta: sim, mas só como piloto."))
        t, r, _ = biblioteca.cabecalho("# Título\n\nData: 2026-10-25 · Autor: x · Estado: **anexo** do estudo\n"
                                       "continua o parágrafo dos metadados.\n\n**Base:** lei 22/2009\n\n"
                                       "- **Autor:** y\n  continuação da lista.\n\nO texto útil começa aqui e segue sem ponto final")
        self.assertEqual(r, "O texto útil começa aqui e segue sem ponto final")
        # rótulo curto («Pergunta.») não é frase; abreviatura seguida de minúscula ou número não corta
        self.assertEqual(biblioteca.cabecalho("# P\n\n**Pergunta.** O Ana pode publicar?\n")[1],
                         "Pergunta. O Ana pode publicar?")
        self.assertEqual(biblioteca.cabecalho("# P\n\nA Lei 22/2009, art. 24.º, n.º 1 manda. Mais.\n")[1],
                         "A Lei 22/2009, art. 24.º, n.º 1 manda.")
        longa = "x" * 200 + ". Fim."
        self.assertEqual(biblioteca.cabecalho(longa)[1], longa)   # 1.ª frase > RESUMO_MAX: corta-se no _corta

    def test_cabecalho_salta_linhas_com_dois_pontos_e_tem_reserva(self):
        # review ronda 2: «…:» só introduz uma lista; relatórios do gestor só com «Fontes: …» e listas
        r = biblioteca.cabecalho("# R\n\nConvenções deste ficheiro:\n- a\n\nO texto útil vem depois. Mais.\n")[1]
        self.assertEqual(r, "O texto útil vem depois.")
        gestor = ("# Gestor de campanhas — 2026-10-25\n\n## 09:01 — SEM ALTERAÇÕES\n"
                  "Fontes: `mkt_ads.py estado` e `mkt_data.py report --json` (dia 2026-10-24; sem avisos).\n\n- x\n- y\n")
        self.assertEqual(biblioteca.cabecalho(gestor)[1],
                         "Fontes: `mkt_ads.py estado` e `mkt_data.py report --json` (dia 2026-10-24; sem avisos).")
        self.assertEqual(biblioteca.cabecalho("# T\n\n**Estado:** lançado hoje\n\n## P\n- a\n")[1], "Estado: lançado hoje")
        self.assertEqual(biblioteca.cabecalho("# T\n\nPedido do Ana (26/10):\n- a\n")[1], "Pedido do Ana (26/10):")
        self.assertEqual(biblioteca.cabecalho("# T\n\nLista:\n\nData: x\n")[1], "Lista:")   # a 1.ª reserva ganha
        self.assertIsNone(biblioteca.cabecalho("# T\n\n- só lista\n")[1])
        # blocos de código e comentários HTML não são resumo
        self.assertEqual(biblioteca.cabecalho("# T\n\nExemplo:\n```json\n{\"a\": 1, \"b\": \"texto comprido\"}\n```\n"
                                              "<!-- TEXTO: INÍCIO -->\nCom isto o validador rejeita o resto.\n")[1],
                         "Com isto o validador rejeita o resto.")

    def test_resumo_real_da_adenda(self):
        real = Path.home() / "projects/rnd/estudos/2026-11-01-estudo-r-d-aprofundar-bilhetes-com.md"
        if not real.is_file():
            self.skipTest("dados reais indisponíveis")
        texto = real.read_bytes()[:biblioteca.CAB_BYTES].decode("utf-8", "replace")
        self.assertEqual(biblioteca.cabecalho(texto)[1], "Resposta curta: sim, mas só como piloto.")


    def test_nome_nao_utf8_fica_de_fora(self):
        self.assertFalse(biblioteca._componente_ok("caf\udce9.md"))
        self.assertTrue(biblioteca._componente_ok("café.md"))
        d = os.fsencode(self.root / "rascunhos")
        for rel in (b"/caf\xe9.md", b"/m\xe9dia/a.jpg", b"/media/2026-11-01-x/\xe9.jpg"):
            os.makedirs(os.path.dirname(d + rel), exist_ok=True)
            with open(d + rel, "wb") as fp:
                fp.write(b"# t")
        (self.tasks / "inbox").mkdir()
        with open(os.fsencode(self.tasks / "inbox") + b"/caf\xe9.md", "wb") as fp:
            fp.write(b"# t")
        with open(os.fsencode(self.aprov) + b"/caf\xe9.md", "wb") as fp:
            fp.write(pedido().encode())
        s = self.novo_indice().get()
        self.assertFalse([k for k in s.ficheiros if not biblioteca._utf8(k)])
        self.assertIn(LOTE, s.por_id)
        json.loads(s.corpo_json)

    def test_varrimento_que_rebenta_mantem_o_anterior(self):
        antes = self.snap()
        with mock.patch.object(self.idx, "_construir", side_effect=UnicodeEncodeError("utf-8", "x", 0, 1, "t")) as c, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.now += 61
            s = self.snap()
            self.snap()
            self.assertEqual(c.call_count, 1)   # não re-varre a cada pedido
            self.assertIs(s.por_id, antes.por_id)
            self.now += 61
            self.snap()
            self.assertEqual(c.call_count, 2)   # tenta outra vez só depois do TTL
        self.assertEqual(len(err.getvalue().strip().splitlines()), 2)   # uma linha por falha, sem traceback
        vazio = self.novo_indice()
        with mock.patch.object(vazio, "_construir", side_effect=RuntimeError("x")), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            s = vazio.get()
        self.assertEqual((s.itens, json.loads(s.corpo_json)["itens"]), ([], []))


# --- B · traversal / S4 · C · symlinks (HTTP) --------------------------------------------------------------------

class Http(Base):
    def setUp(self):
        super().setUp()
        self.old = server.BIBLIOTECA
        server.BIBLIOTECA = self.idx
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"
        self.p_mini = mock.patch.object(biblioteca, "MINI_DIR", self.mini_dir)
        self.p_mini.start()

    def tearDown(self):
        self.p_mini.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        server.BIBLIOTECA = self.old
        super().tearDown()

    def req(self, path, method="GET", headers=None):
        c = http.client.HTTPConnection(self.host, timeout=10)
        try:
            c.request(method, path, headers=headers or {})
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            c.close()

    def get_json(self, path):
        code, h, body = self.req(path)
        return code, json.loads(body)

    def f(self, chave, q=""):
        return "/api/biblioteca/f/" + "/".join(quote(s, safe="") for s in chave.split("/")) + q


class TraversalTest(Http):
    def test_404_unico(self):
        self.w("rascunhos/x", "x")
        self.w("rascunhos/a/b.md", "# b")
        self.w("rascunhos/grande.md", b"a" * (biblioteca.TEXTO_MAX + 1))
        maus = ("rascunhos/../x", "rascunhos/%2e%2e/x", "rascunhos/a%2f..%2fb.md", "rascunhos/a\\b.md",
                "rascunhos/a%5Cb.md", "rascunhos/%00", "rascunhos/a%00.md", "rascunhos/%ff.md", "rascunhos/a//b.md",
                "rascunhos/./a/b.md", "./rascunhos/a/b.md", "outra/a/b.md", "rascunhos/nao-existe.md",
                "rascunhos/grande.md", "rascunhos", "", "rascunhos/.git/a.md", "%2e%2e/etc/passwd",
                "rascunhos/a/b.md/")
        for base in ("/api/biblioteca/f/", "/api/biblioteca/mini/"):
            for p in maus:
                with self.subTest(p=base + p):
                    code, h, body = self.req(base + p)
                    self.assertEqual((code, json.loads(body)), (404, NAO))
                    self.assertTrue(h["content-type"].startswith("application/json"))
        self.assertEqual(self.req("/api/biblioteca/f/rascunhos/a/b.md")[0], 200)
        for q in ("", "?id=", "?id=rascunhos/..%2f", "?id=nao/existe", "?id=%ff", f"?id={LOTE}/../x"):
            with self.subTest(q=q):
                self.assertEqual(self.get_json("/api/biblioteca/zip" + q), (404, NAO))
                self.assertEqual(self.get_json("/api/biblioteca/item" + q), (404, NAO))
        self.assertEqual(self.get_json("/api/biblioteca/outra"), (404, NAO))
        self.assertEqual(self.get_json("/api/biblioteca/"), (404, NAO))


class SymlinkTest(Http):
    def test_symlinks_fora_do_indice(self):
        r = self.root / "rascunhos"
        self.w("rascunhos/sub/a.md", "# a")
        os.symlink(r / "sub" / "a.md", r / "link.md")
        os.symlink("/etc/passwd", r / "passwd.txt")
        os.symlink("/etc/hostname", r / "host.txt")
        os.symlink(r / "sub", r / "pasta-link")
        os.symlink("/etc", r / "etc")
        chaves = set(self.novo_indice().get().ficheiros)
        self.assertIn("rascunhos/sub/a.md", chaves)
        for k in ("rascunhos/link.md", "rascunhos/host.txt", "rascunhos/pasta-link/a.md"):
            self.assertNotIn(k, chaves)
            self.assertEqual(self.get_json(self.f(k)), (404, NAO))
        self.assertFalse([k for k in chaves if k.startswith("rascunhos/etc/")])

    def test_ficheiro_trocado_por_symlink_depois_do_varrimento(self):
        p = self.w("rascunhos/sub/a.md", "# a")
        self.w("rascunhos/sub/outro.md", "# o")
        self.assertEqual(self.req(self.f("rascunhos/sub/a.md"))[0], 200)
        p.unlink()
        os.symlink(self.root / "rascunhos/sub/outro.md", p)
        self.assertEqual(self.get_json(self.f("rascunhos/sub/a.md")), (404, NAO))
        self.assertEqual(self.get_json(self.f("rascunhos/sub/a.md", "?dl=1")), (404, NAO))

    def test_pasta_intermedia_trocada_por_symlink(self):
        self.w("rascunhos/d/x.md", "# igual")
        self.w("rascunhos/d2/x.md", "# igual")   # mesmo nome e tamanho, outro inode
        self.assertEqual(self.req(self.f("rascunhos/d/x.md"))[0], 200)
        os.rename(self.root / "rascunhos/d", self.root / "rascunhos/d-velha")
        os.symlink(self.root / "rascunhos/d2", self.root / "rascunhos/d")
        self.assertEqual(self.get_json(self.f("rascunhos/d/x.md")), (404, NAO))

    def test_tamanho_mudado_depois_do_varrimento(self):
        p = self.w("rascunhos/v.mp4", b"0123")
        self.assertEqual(self.req(self.f("rascunhos/v.mp4"))[0], 200)
        p.write_bytes(b"012345")
        self.assertEqual(self.get_json(self.f("rascunhos/v.mp4")), (404, NAO))


# --- D · .tasks ------------------------------------------------------------------------------------------------------

class TarefasTest(Base):
    def setUp(self):
        super().setUp()
        t = "tasks"
        for n in ("SPEC.md", "PLAN.md", "QA.md"):
            self.w(f"{t}/20261101-1000-a/{n}", f"# {n[:-3]} do A\n")
        for n in ("meta.json", "task.md", "claude.log", "heartbeat"):
            self.w(f"{t}/20261101-1000-a/{n}", "{}")
        self.w(f"{t}/20261101-1000-a/meta.json", json.dumps({"project": "MenuQR (parecer UX)", "task": "# Meta A"}))
        self.w(f"{t}/20261101-1100-b/PLAN.md", "# Plano B\n")
        self.w(f"{t}/20261101-1100-b/meta.json", json.dumps({"project": "Projeto 90", "task": "x\n# Tarefa B\nmais"}))
        self.w(f"{t}/20261101-1200-c/QA.md", "sem título\n")
        self.w(f"{t}/20261101-1300-d/meta.json", "{}")
        self.w(f"{t}/decisoes/SPEC.md", "# não")
        self.w(f"{t}/decisoes-fila/x.json", "{}")
        self.w(f"{t}/inbox/plano-x.md", "---\nproduto: joga\n---\n# Plano X\n\nPrimeira frase do plano.\n")
        self.w(f"{t}/inbox/y.json", "{}")
        self.w(f"{t}/inbox/sub/z.md", "# z")
        self.w(f"{t}/inbox/.oculto.md", "# z")
        os.symlink(self.tasks / "20261101-1000-a", self.tasks / "20261101-9999-link")

    def test_so_spec_plan_qa_e_inbox(self):
        s = self.snap()
        tar = sorted(k for k in s.ficheiros if k.startswith("tarefas/"))
        self.assertEqual(tar, ["tarefas/20261101-1000-a/PLAN.md", "tarefas/20261101-1000-a/QA.md",
                               "tarefas/20261101-1000-a/SPEC.md", "tarefas/20261101-1100-b/PLAN.md",
                               "tarefas/20261101-1200-c/QA.md", "tarefas/inbox/plano-x.md"])
        ids = {i["id"] for i in s.itens if i["raiz"] == "tarefas"}
        self.assertEqual(ids, {"tarefas/20261101-1000-a", "tarefas/20261101-1100-b", "tarefas/20261101-1200-c",
                               "tarefas/inbox/plano-x.md"})

    def test_titulos_produtos_tipos(self):
        a, b, c = (self.item(f"tarefas/{x}")["publico"] for x in ("20261101-1000-a", "20261101-1100-b",
                                                                  "20261101-1200-c"))
        self.assertEqual((a["titulo"], a["produtos"], a["genero"], a["tipos"]), ("SPEC do A", ["menuqr"], "tarefa",
                                                                                 ["planos"]))
        self.assertEqual((b["titulo"], b["produtos"]), ("Tarefa B", ["projeto90"]))   # nome do projecto da config
        self.assertEqual(c["titulo"], "20261101-1200-c")
        d = self.item("tarefas/20261101-1000-a")["detalhe"]
        self.assertEqual([(t["nome"], t["rotulo"]) for t in d["textos"]], [("SPEC.md", "Spec"), ("PLAN.md", "Plano"),
                                                                            ("QA.md", "QA")])
        self.assertTrue(a["principal"]["url"].startswith("api/biblioteca/f/tarefas/20261101-1000-a/SPEC.md?v="))
        p = self.item("tarefas/inbox/plano-x.md")["publico"]
        self.assertEqual((p["genero"], p["titulo"], p["produtos"], p["tipos"], p["resumo"], p["zip"]),
                         ("plano", "Plano X", ["joga"], ["planos"], "Primeira frase do plano.", None))


# --- E · agrupamento -------------------------------------------------------------------------------------------------

class AgrupamentoTest(Base):
    def test_lote_com_final(self):
        s = self.snap()
        lotes = [i for i in s.itens if i["genero"] == "lote"]
        self.assertEqual([i["id"] for i in lotes], [LOTE])
        it = s.por_id[LOTE]
        d, pub = it["detalhe"], it["publico"]
        self.assertEqual([x["nome"] for x in d["destaque"]],
                         ["status-01-quem-9x16.jpg", "status-01-quem-v2-9x16.jpg", "status-05-preco-9x16.jpg",
                          "status-06-convite-9x16.mp4"])
        pastas = {t["pasta"]: [x["nome"] for x in t["ficheiros"]] for t in d["trabalho"]}
        self.assertEqual(pastas, {"final/status-01-quem-9x16-semelhanca": ["parecida-1.jpg"],
                                  "final/status-06-convite-9x16-fotogramas": ["f-001.png", "f-002.png", "folha.png"],
                                  "prompts": ["status-01.prompt.md"], "video": ["chave.jpg", "x.mp4", "voz-06.wav"]})
        self.assertNotIn(".json", json.dumps(d) + json.dumps(pub) + s.corpo_json.decode())
        self.assertEqual(pub["titulo"], "Estado do WhatsApp pessoal (6 peças)")
        self.assertEqual(pub["sub"], "2026-11-01-status-ana")
        self.assertEqual(pub["conta"], {"imagem": 3, "video": 1, "audio": 0, "doc": 4})
        self.assertEqual((pub["n"], pub["mais"], len(pub["minis"])), (8, 1, 3))
        self.assertTrue(pub["minis"][0].startswith("api/biblioteca/mini/rascunhos/media/2026-11-01-status-ana/final/"
                                                   "status-01-quem-9x16.jpg?v="))
        self.assertIsNone(pub["principal"])
        self.assertIsNone(pub["resumo"])
        self.assertTrue(pub["q"].startswith("estado do whatsapp pessoal (6 pecas) 2026-11-01-status-ana "))
        self.assertIn("status-01-quem-9x16.jpg", pub["q"])

    def test_textos_do_lote_e_ligados(self):
        it = self.item(LOTE)
        textos = [(t["caminho"], t["rotulo"], t["ligado"]) for t in it["detalhe"]["textos"]]
        self.assertEqual(textos, [
            (f"{LOTE}/folha.md", "Folha", False),
            ("rascunhos/whatsapp-status/2026-11-01-textos.md", "Texto", True),          # citado na folha
            ("rascunhos/whatsapp-status/2026-11-01-revisao.md", "Revisão", True),       # frontmatter do pedido
            ("rascunhos/whatsapp-status/2026-11-01-parecer-juridico.md", "Parecer jurídico", True)])
        s = self.snap()
        for k in ("rascunhos/whatsapp-status/2026-11-01-textos.md", "rascunhos/whatsapp-status/2026-11-01-revisao.md"):
            self.assertIn(k, s.por_id)   # continuam itens próprios
            self.assertEqual(s.ficheiro_item[k], k)
        self.assertEqual(s.ficheiro_item[f"{LOTE}/final/status-01-quem-9x16.jpg"], LOTE)
        self.assertEqual(s.ficheiro_item[f"{LOTE}/prompts/status-01.prompt.md"], LOTE)

    def test_lote_sem_final_e_titulo_humanizado(self):
        L = "rascunhos/media/2026-10-26-s45-zf-mz-mascote"
        self.w(f"{L}/b.jpg")
        self.w(f"{L}/sub/a.mp4")
        self.w(f"{L}/sub/x-fotogramas/1.png")
        self.w(f"{L}/prompts/p.jpg")
        self.w(f"{L}/sub/c.prompt.md")
        self.w(f"{L}/briefing.md", "sem título")
        self.w("rascunhos/media/mascote-loja/a.png")
        it = self.item(L)
        self.assertEqual([x["caminho"].split(L + "/")[1] for x in it["detalhe"]["destaque"]], ["sub/a.mp4", "b.jpg"])
        self.assertEqual(it["publico"]["titulo"], "S45 zf mz mascote")
        self.assertEqual(self.item("rascunhos/media/mascote-loja")["publico"]["titulo"], "Mascote loja")
        self.assertEqual([t["nome"] for t in it["detalhe"]["textos"]], ["briefing.md"])
        z = {a for _, a in it["zip"]}
        self.assertEqual(z, {"b.jpg", "sub/a.mp4", "textos/briefing.md"})

    def test_titulo_do_readme_sem_folha(self):
        L = "rascunhos/media/2026-10-20-x"
        self.w(f"{L}/a.jpg")
        self.w(f"{L}/README.md", "# Lote X" + "y" * 200)
        t = self.item(L)["publico"]["titulo"]
        self.assertEqual(len(t), biblioteca.TITULO_MAX)
        self.assertTrue(t.startswith("Lote Xyy") and t.endswith("…"))

    def test_documentos_agrupados_pela_raiz_do_nome(self):
        for i, n in enumerate(("x.md.juridico.md", "x.copy.md", "x.md", "x.juridico.md", "x.md.revisao.md")):
            self.w(f"rascunhos/{n}", f"# Título {n}\n\nFrase de {n}.\n", idade=10 * H - i)
        self.w("rascunhos/y.revisao.md", "# Y rev", idade=5 * H)
        self.w("rascunhos/y.copy.md", "# Y copy", idade=6 * H)
        s = self.snap()
        it = s.por_id["rascunhos/x.md"]
        self.assertEqual(len(it["detalhe"]["textos"]), 5)
        self.assertEqual(it["detalhe"]["textos"][0]["nome"], "x.md")
        pub = it["publico"]
        self.assertEqual((pub["genero"], pub["titulo"], pub["resumo"], pub["n"]), ("documento", "Título x.md",
                                                                                   "Frase de x.md.", 5))
        self.assertEqual(pub["tipos"], ["textos", "pareceres"])
        self.assertEqual(pub["principal"]["ext"], "md")
        self.assertEqual(sorted(a for _, a in it["zip"]), sorted(["x.md", "x.copy.md", "x.juridico.md",
                                                                  "x.md.juridico.md", "x.md.revisao.md"]))
        self.assertIn("rascunhos/y.copy.md", s.por_id)   # sem x.md: principal = mtime mais antiga
        self.assertNotIn("rascunhos/y.revisao.md", s.por_id)
        for k in ("rascunhos/x.copy.md", "rascunhos/x.md.revisao.md"):
            self.assertNotIn(k, s.por_id)
            self.assertEqual(s.ficheiro_item[k], "rascunhos/x.md")

    def test_pasta_com_media_fora_de_media(self):
        self.w("rascunhos/site-blog/b.png")
        self.w("rascunhos/site-blog/a.jpg")
        self.w("rascunhos/site-blog/v.mp4")
        self.w("rascunhos/site-blog/v-fotogramas/folha.png")
        self.w("rascunhos/site-blog/v-fotogramas/2.png")
        self.w("rascunhos/site-blog/texto.md", "# Texto do site")
        self.w("rascunhos/solta.jpg")
        s = self.snap()
        it = s.por_id["rascunhos/site-blog"]
        self.assertEqual(it["publico"]["genero"], "pasta")
        self.assertEqual([x["nome"] for x in it["detalhe"]["destaque"]], ["a.jpg", "b.png", "v.mp4"])
        self.assertTrue(it["detalhe"]["destaque"][2]["mini"].startswith(
            "api/biblioteca/mini/rascunhos/site-blog/v-fotogramas/2.png"))
        self.assertEqual(it["detalhe"]["trabalho"][0]["pasta"], "v-fotogramas")
        self.assertEqual(it["publico"]["produtos"], ["blog"])
        self.assertIn("rascunhos/site-blog/texto.md", s.por_id)   # documento à parte
        self.assertEqual(s.por_id["rascunhos"]["publico"]["genero"], "pasta")   # media na raiz de rascunhos

    def test_resumo_so_em_documentos(self):
        self.w("rnd-estudos/2026-11-01-e.md", "---\nx: y\n---\n# Estudo\n\n## Sec\n\n- item\n" + "z" * 300)
        pub = self.item("rnd-estudos/2026-11-01-e.md")["publico"]
        self.assertEqual(len(pub["resumo"]), biblioteca.RESUMO_MAX)
        self.assertEqual((pub["tipos"], pub["sub"]), (["estudos"], "rnd-estudos"))


# --- F · produto e tipo ---------------------------------------------------------------------------------------------

class ProdutoTest(Base):
    # produtos = projectos da config (LEGADO), por id e nome normalizado
    def test_palavras_chave(self):
        kw = biblioteca.produtos_kw
        self.assertEqual(kw("media/2026-10-23-ebook-lote1"), ["ebook"])
        self.assertEqual(kw("rascunhos/site-blog"), ["blog"])
        self.assertEqual(kw("2026-11-01-fichas-reveillon.md"), [])
        self.assertEqual(kw("projeto-90 e Projeto 90"), ["projeto90"])
        self.assertEqual(kw("Loja e E-Book e MenuQR, joga, TicTag"), ["loja", "ebook", "menuqr", "joga", "tictag"])
        self.assertEqual(kw("jogador menuqrx lojas"), [])

    def test_valores_e_projecto(self):
        self.assertEqual(biblioteca.produtos_valor("loja, ebook"), ["loja", "ebook"])
        self.assertEqual(biblioteca.produtos_valor("blog,Projeto 90,x"), ["blog", "projeto90", "geral"])
        p = biblioteca.produto_projecto
        self.assertEqual([p(v) for v in ("MenuQR", "Loja (auditoria)", "TicTag", "joga", "E-book", "Projeto 90",
                                         "projeto90", "Fábrica", "Squad Office", "")],
                         ["menuqr", "loja", "tictag", "joga", "ebook", "projeto90", "projeto90", "fabrica", "geral",
                          "geral"])

    def test_produto_nos_itens(self):
        self.w("aprov-mkt/2026-11-02-dois.md", pedido(produto="loja, ebook", criado="2026-11-02"))
        self.w("rascunhos/media/2026-10-23-ebook-lote1/a.jpg")
        self.w("rnd-estudos/2026-11-01-fichas-reveillon.md", "# Fichas")
        self.w("rnd-estudos/z.md", "---\nproduto: menuqr\n---\n# Estudo loja")
        s = self.snap()
        self.assertEqual(s.por_id[LOTE]["publico"]["produtos"], ["loja", "ebook"])   # pedido mais recente
        self.assertEqual(s.por_id["rascunhos/media/2026-10-23-ebook-lote1"]["publico"]["produtos"], ["ebook"])
        self.assertEqual(s.por_id["rnd-estudos/2026-11-01-fichas-reveillon.md"]["publico"]["produtos"], ["geral"])
        self.assertEqual(s.por_id["rnd-estudos/z.md"]["publico"]["produtos"], ["menuqr"])   # frontmatter > palavras

    def test_tipos(self):
        self.w("rascunhos/2026-10-24-parecer-juridico-x.md", "# P")
        self.w("relatorios/diario/r.md", "# R")
        self.w("propostas/p.pdf", b"%PDF")
        self.w("mkt-knowledge/k.txt", "k")
        s = self.snap()
        self.assertEqual(s.por_id[LOTE]["publico"]["tipos"], ["imagens", "videos"])
        self.assertEqual(s.por_id["rascunhos/2026-10-24-parecer-juridico-x.md"]["publico"]["tipos"], ["pareceres"])
        self.assertEqual(s.por_id["relatorios/diario/r.md"]["publico"]["tipos"], ["relatorios"])
        self.assertEqual(s.por_id["propostas/p.pdf"]["publico"]["tipos"], ["planos"])
        self.assertEqual(s.por_id["mkt-knowledge/k.txt"]["publico"]["tipos"], ["conhecimento"])
        self.assertEqual(s.por_id["rascunhos/whatsapp-status/2026-11-01-textos.md"]["publico"]["tipos"], ["textos"])


# --- G · estado ------------------------------------------------------------------------------------------------------

class EstadoTest(Base):
    def estados(self):
        it = self.novo_indice().get().por_id[LOTE]
        return it["publico"], {x["nome"]: x["estado"] for x in it["detalhe"]["destaque"]}

    def test_aprovado_citado_e_sem_aprovacao(self):
        pub, est = self.estados()
        self.assertEqual(est, {"status-01-quem-9x16.jpg": "aprovado", "status-01-quem-v2-9x16.jpg": "sem_aprovacao",
                               "status-05-preco-9x16.jpg": "sem_aprovacao", "status-06-convite-9x16.mp4": "aprovado"})
        self.assertEqual((pub["estado"], pub["resumo_estado"]), ("aprovado", {"aprovado": 2, "bloqueado": 0,
                                                                              "sem_aprovacao": 2}))
        self.assertEqual(pub["pedido"]["fonte"], "marketing")
        self.assertTrue(pub["pedido"]["url"].startswith("api/biblioteca/f/aprovacoes-marketing/2026-11-01-status.md?v="))
        self.assertEqual({t["estado"] for t in self.item(LOTE)["detalhe"]["textos"]}, {None})

    def test_linha_bloqueada(self):
        self.w("aprov-mkt/2026-11-01-status.md", pedido(
            extra="05 BLOQUEADA — `rascunhos/media/2026-11-01-status-ana/final/status-05-preco-9x16.jpg`\n"))
        pub, est = self.estados()
        self.assertEqual(est["status-05-preco-9x16.jpg"], "bloqueado")
        self.assertEqual(pub["resumo_estado"]["bloqueado"], 1)

    def test_estados_do_pedido(self):
        casos = (("bloqueado: IVA", "bloqueado", "bloqueado"), ("pendente", "pendente", "pendente"),
                 ("parcial (1 de 2)", "pendente", "pendente"), ("recusado", "recusado", "recusado"),
                 ("executado", "executado", "executado"), ("Pendente", None, None), ("qualquer", None, None))
        for valor, lote, ficheiro in casos:
            with self.subTest(valor=valor):
                self.w("aprov-mkt/2026-11-01-status.md", pedido(estado=valor))
                pub, est = self.estados()
                self.assertEqual(pub["estado"], lote)
                self.assertEqual(est["status-01-quem-9x16.jpg"], ficheiro)
                self.assertEqual(est["status-05-preco-9x16.jpg"], "sem_aprovacao" if lote else None)

    def test_mais_recente_ganha(self):
        self.w("aprov-mkt/2026-10-30-velho.md", pedido(estado="recusado", criado="2026-10-30"))
        self.w("aprov-mkt/2026-11-01-z.md", pedido(estado="pendente", criado="2026-11-01"))   # mesmo criado, nome maior
        pub, est = self.estados()
        self.assertEqual((pub["estado"], pub["pedido"]["ficheiro"]), ("pendente", "2026-11-01-z.md"))
        it = self.novo_indice().get().por_id[LOTE]
        self.assertEqual([p["ficheiro"] for p in it["detalhe"]["pedidos"]],
                         ["2026-11-01-z.md", "2026-11-01-status.md", "2026-10-30-velho.md"])

    def test_sem_pedido(self):
        os.unlink(self.aprov / "2026-11-01-status.md")
        pub, est = self.estados()
        self.assertEqual((pub["estado"], pub["resumo_estado"], pub["pedido"]), (None, None, None))
        self.assertEqual(set(est.values()), {None})

    def test_pedido_nao_e_item_e_e_servido_mascarado(self):
        self.w("aprov-mkt/2026-11-01-status.md", pedido(extra=f"token {GH}\n"))
        self.w("aprov-mkt/README.md", "---\nestado: aprovado\n---\n")
        self.w("aprov-mkt/sem-front.md", "# nada")
        s = self.novo_indice().get()
        self.assertFalse([i for i in s.itens if i["raiz"].startswith("aprovacoes")])
        self.assertEqual(sorted(k for k in s.ficheiros if k.startswith("aprovacoes-")),
                         ["aprovacoes-marketing/2026-11-01-status.md"])
        self.assertNotIn("aprovacoes-marketing/2026-11-01-status.md", s.ficheiro_item)

    def test_documento_citado_tem_pedido(self):
        pub = self.item("rascunhos/whatsapp-status/2026-11-01-revisao.md")["publico"]
        self.assertEqual((pub["pedido"]["ficheiro"], pub["estado"], pub["resumo_estado"]),
                         ("2026-11-01-status.md", None, None))
        self.assertEqual(pub["produtos"], ["loja"])


# --- H · datas e cache ----------------------------------------------------------------------------------------------

class DatasCacheTest(Base):
    def test_novo_47h_e_49h(self):
        self.w("relatorios/a.md", "# a", idade=47 * H)
        self.w("relatorios/b.md", "# b", idade=49 * H)
        s = self.snap()
        self.assertTrue(s.por_id["relatorios/a.md"]["publico"]["novo"])
        self.assertFalse(s.por_id["relatorios/b.md"]["publico"]["novo"])
        self.assertEqual(s.por_id["relatorios/b.md"]["publico"]["criado"], int(NOW - 49 * H))

    def test_ordem_actualizado_desc(self):
        self.w("relatorios/a.md", "# a", idade=1)
        self.w("relatorios/c.md", "# c", idade=100 * H)
        self.w("relatorios/b.md", "# b", idade=100 * H)
        itens = self.snap().itens
        self.assertEqual(itens[0]["id"], "relatorios/a.md")
        ch = [(-i["actualizado"], i["id"]) for i in itens]
        self.assertEqual(ch, sorted(ch))
        ids = [i["id"] for i in itens]
        self.assertLess(ids.index("relatorios/b.md"), ids.index("relatorios/c.md"))

    def test_ligados_nao_contam_para_datas_do_lote(self):
        k = "rascunhos/whatsapp-status/2026-11-01-textos.md"   # citado na folha do lote
        self.w(k, "# Textos", idade=100 * H)
        pub = self.novo_indice().get().por_id[LOTE]["publico"]
        self.assertTrue(pub["novo"])   # o texto antigo citado não tira o «Novo» ao lote novo
        self.assertEqual((pub["criado"], pub["actualizado"]), (int(NOW - 2 * H), int(NOW - 2 * H)))
        L = "rascunhos/media/2026-10-01-velho"
        self.w(f"{L}/a.jpg", idade=200 * H)
        self.w(f"{L}/folha.md", f"# Velho\n`{k}`\n", idade=200 * H)
        self.w(k, "# Textos novos", idade=1)
        s = self.novo_indice().get()
        pub = s.por_id[L]["publico"]
        self.assertEqual((pub["actualizado"], pub["novo"]), (int(NOW - 200 * H), False))   # o lote velho não sobe
        self.assertIn(k, [t["caminho"] for t in s.por_id[L]["detalhe"]["textos"]])

    def test_cache_60s(self):
        self.snap()
        self.now += 59
        self.w("relatorios/novo.md", "# novo")
        self.assertNotIn("relatorios/novo.md", self.snap().por_id)
        self.assertEqual(self.idx.scans, 1)
        self.now += 2
        self.assertIn("relatorios/novo.md", self.snap().por_id)
        self.assertEqual(self.idx.scans, 2)

    def test_concorrencia_um_varrimento(self):
        idx = self.novo_indice()
        ts = [threading.Thread(target=idx.get) for _ in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(idx.scans, 1)

    def test_cabecalhos_em_cache(self):
        self.snap()
        lidos = []
        orig = biblioteca._ler_inicio
        with mock.patch.object(biblioteca, "_ler_inicio", side_effect=lambda *a: lidos.append(a[0]) or orig(*a)):
            self.now += 61
            self.snap()
        self.assertFalse([p for p in lidos if p.endswith(".md")])   # nada mudou: nenhum .md relido


# --- I · servir · J · Range ----------------------------------------------------------------------------------------

class ServirTest(Http):
    def test_texto_mascarado_ver_e_descarregar(self):
        k = "rascunhos/whatsapp-status/2026-11-01-textos.md"
        for q in ("", "?dl=1", "?v=1&dl=1"):
            code, h, body = self.req(self.f(k, q))
            self.assertEqual(code, 200)
            t = body.decode()
            self.assertNotIn(GH, t)
            self.assertNotIn("abcDEF123456abcDEF123456", t)
            self.assertIn("[segredo]", t)
            self.assertIn("> legenda", t)
            self.assertEqual(h["content-length"], str(len(body)))
            self.assertEqual(h["content-type"], "text/plain; charset=utf-8")
            self.assertNotIn("accept-ranges", h)
        self.assertTrue(self.req(self.f(k, "?dl=1"))[1]["content-disposition"].startswith("attachment;"))
        self.assertTrue(self.req(self.f(k))[1]["content-disposition"].startswith("inline;"))
        code, h, body = self.req(self.f(k), headers={"Range": "bytes=0-1"})   # texto ignora Range
        self.assertEqual((code, "content-range" in h), (200, False))

    def test_content_types_csp_e_cache(self):
        tipos = {"a.jpg": "image/jpeg", "a.jpeg": "image/jpeg", "a.png": "image/png", "a.webp": "image/webp",
                 "a.gif": "image/gif", "a.mp4": "video/mp4", "a.m4v": "video/mp4", "a.webm": "video/webm",
                 "a.mp3": "audio/mpeg", "a.wav": "audio/wav", "a.m4a": "audio/mp4", "a.ogg": "audio/ogg",
                 "a.opus": "audio/ogg", "a.md": "text/plain; charset=utf-8", "a.txt": "text/plain; charset=utf-8",
                 "a.csv": "text/plain; charset=utf-8", "a.pdf": "application/pdf"}
        for n in tipos:
            self.w(f"rascunhos/tipos/{n}", "<html><script>alert(1)</script>")
        for n, ct in tipos.items():
            with self.subTest(n=n):
                self.assertEqual(self.req(self.f(f"rascunhos/tipos/{n}"))[1]["cache-control"], "no-store")
                code, h, _ = self.req(self.f(f"rascunhos/tipos/{n}", "?v=1"))
                self.assertEqual((code, h["content-type"]), (200, ct))
                self.assertEqual(h["x-content-type-options"], "nosniff")
                self.assertEqual(h["cache-control"], "private, max-age=3600")
                if n.endswith(".pdf"):
                    self.assertNotIn("content-security-policy", h)
                else:
                    self.assertIn("sandbox", h["content-security-policy"])
        for p in ("/api/biblioteca", f"/api/biblioteca/item?id={quote(LOTE)}"):
            h = self.req(p)[1]
            self.assertEqual(h["cache-control"], "no-store")
            self.assertTrue(h["content-type"].startswith("application/json"))
        self.assertEqual(self.req(self.f("rascunhos/nao.md"))[1]["cache-control"], "no-store")

    def test_pedido_sem_v_nao_fica_em_cache(self):
        p = self.f("aprovacoes-marketing/2026-11-01-status.md")
        self.assertEqual(self.req(p)[1]["cache-control"], "no-store")
        self.assertEqual(self.req(p + "?dl=1")[1]["cache-control"], "no-store")
        self.assertEqual(self.req(p + "?v=")[1]["cache-control"], "no-store")
        self.assertEqual(self.req(p, "HEAD")[1]["cache-control"], "no-store")
        url = self.item(LOTE)["detalhe"]["pedidos"][0]["url"]
        self.assertEqual(self.req("/" + url)[1]["cache-control"], "private, max-age=3600")

    def test_nunca_text_html(self):
        paths = ["/api/biblioteca", "/api/biblioteca/x", "/api/biblioteca/f/x", "/api/biblioteca/mini/x",
                 f"/api/biblioteca/zip?id={quote(LOTE, safe='')}", f"/api/biblioteca/item?id={quote(LOTE, safe='')}",
                 self.f(f"{LOTE}/folha.md"), self.f(f"{LOTE}/final/status-06-convite-9x16.mp4")]
        for p in paths:
            self.assertNotIn("text/html", self.req(p)[1].get("content-type", ""), p)

    def test_content_disposition_utf8(self):
        self.w("rascunhos/peças/peça-01.jpg", b"jpg")
        code, h, body = self.req(self.f("rascunhos/peças/peça-01.jpg", "?dl=1"))
        self.assertEqual((code, body), (200, b"jpg"))
        self.assertEqual(h["content-disposition"],
                         "attachment; filename=\"pe_a-01.jpg\"; filename*=UTF-8''pe%C3%A7a-01.jpg")

    def test_range(self):
        k = f"{LOTE}/final/status-06-convite-9x16.mp4"
        code, h, body = self.req(self.f(k), headers={"Range": "bytes=0-1"})
        self.assertEqual((code, h["content-range"], body, h["content-length"]), (206, "bytes 0-1/10", b"01", "2"))
        code, h, body = self.req(self.f(k), headers={"Range": "bytes=10-"})
        self.assertEqual((code, h["content-range"], body), (416, "bytes */10", b""))
        code, h, body = self.req(self.f(k), headers={"Range": "bytes=-3"})
        self.assertEqual((code, h["content-range"], body), (206, "bytes 7-9/10", b"789"))
        for r in ("bytes=0-1,4-5", "lixo", "bytes=5-2", "bytes=" + "9" * 5000 + "-", "bytes=-" + "9" * 5000):
            code, h, body = self.req(self.f(k), headers={"Range": r})
            self.assertEqual((code, body), (200, b"0123456789"), r)
        code, h, body = self.req(self.f(k))
        self.assertEqual((code, h["accept-ranges"], h["content-length"], body), (200, "bytes", "10", b"0123456789"))

    def test_head(self):
        k = f"{LOTE}/final/status-06-convite-9x16.mp4"
        code, h, body = self.req(self.f(k), "HEAD")
        self.assertEqual((code, h["content-length"], h["accept-ranges"], body), (200, "10", "bytes", b""))
        code, h, body = self.req(self.f(k), "HEAD", {"Range": "bytes=2-3"})
        self.assertEqual((code, h["content-range"], body), (206, "bytes 2-3/10", b""))
        code, h, body = self.req(self.f("rascunhos/../x"), "HEAD")
        self.assertEqual((code, body), (404, b""))
        self.assertEqual(self.req("/", "HEAD")[0], 200)   # static continua

    def test_head_das_rotas_get_da_405_json(self):
        for p in ("/api/biblioteca", f"/api/biblioteca/item?id={quote(LOTE, safe='')}",
                  f"/api/biblioteca/zip?id={quote(LOTE, safe='')}", "/api/biblioteca/mini/" + quote(
                      f"{LOTE}/final/status-01-quem-9x16.jpg")):
            with self.subTest(p=p):
                code, h, body = self.req(p, "HEAD")
                self.assertEqual((code, h["allow"], body, h["cache-control"]), (405, "GET", b"", "no-store"))
                self.assertTrue(h["content-type"].startswith("application/json"))
        code, h, body = self.req("/api/biblioteca/x", "HEAD")
        self.assertEqual((code, body), (404, b""))
        self.assertTrue(h["content-type"].startswith("application/json"))
        self.assertEqual(self.req("/api/biblioteca")[0], 200)   # GET igual

    def test_cliente_fecha_a_meio(self):
        self.w("rascunhos/grande.mp4", os.urandom(3 * biblioteca.BLOCO))
        c = http.client.HTTPConnection(self.host, timeout=5)
        c.request("GET", self.f("rascunhos/grande.mp4"))
        r = c.getresponse()
        r.read(10)
        c.close()
        self.assertEqual(self.req(self.f("rascunhos/grande.mp4"), "HEAD")[0], 200)   # o servidor continua

    def test_indice_e_item(self):
        code, d = self.get_json("/api/biblioteca")
        self.assertEqual(code, 200)
        self.assertEqual(set(d), {"gerado", "itens"})
        self.assertEqual(d["gerado"], int(NOW))
        it = next(i for i in d["itens"] if i["id"] == LOTE)
        self.assertEqual(set(it), {"id", "raiz", "genero", "titulo", "sub", "tipos", "produtos", "actualizado",
                                   "criado", "novo", "estado", "resumo_estado", "pedido", "minis", "mais", "conta",
                                   "n", "bytes", "resumo", "principal", "zip", "q"})
        code, d = self.get_json(f"/api/biblioteca/item?id={quote(LOTE, safe='')}")
        self.assertEqual((code, d["foco"], d["item"]["id"]), (200, None, LOTE))
        self.assertEqual(set(d), {"item", "foco", "destaque", "textos", "trabalho", "pedidos"})
        f0 = d["destaque"][0]
        self.assertEqual(f0["url"], f"api/biblioteca/f/{LOTE}/final/status-01-quem-9x16.jpg?v={int(NOW - 2 * H)}")
        self.assertEqual(f0["dl"], f0["url"] + "&dl=1")
        self.assertEqual(self.req("/" + f0["url"])[0], 200)
        self.assertEqual(self.req("/" + f0["mini"])[0], 200)
        k = f"{LOTE}/final/status-05-preco-9x16.jpg"
        code, d = self.get_json(f"/api/biblioteca/item?id={quote(k, safe='')}")   # deep link de ficheiro
        self.assertEqual((code, d["foco"], d["item"]["id"]), (200, k, LOTE))
        p = d["pedidos"][0]
        self.assertEqual((p["titulo"], p["estado"], p["criado"]), ("Status do Ana", "aprovado", "2026-11-01"))
        code, h, body = self.req("/" + p["url"])
        self.assertEqual((code, h["content-type"]), (200, "text/plain; charset=utf-8"))

    def test_indice_gzip(self):
        code, h, body = self.req("/api/biblioteca", headers={"Accept-Encoding": "gzip, br"})
        self.assertEqual((code, h["content-encoding"], h["vary"], h["cache-control"]),
                         (200, "gzip", "Accept-Encoding", "no-store"))
        self.assertEqual(gzip.decompress(body), self.req("/api/biblioteca")[2])

    def test_grande(self):
        self.w("rascunhos/whatsapp-status/2026-11-01-textos.md", b"# g\n" + b"a" * biblioteca.TEXTO_MAX)
        idx = self.novo_indice()
        server.BIBLIOTECA = idx
        it = idx.get().por_id[LOTE]
        t = next(x for x in it["detalhe"]["textos"] if x["nome"] == "2026-11-01-textos.md")
        self.assertTrue(t["grande"])
        self.assertNotIn("textos/2026-11-01-textos.md", [a for _, a in it["zip"]])
        self.assertEqual(self.get_json("/" + t["url"]), (404, NAO))
        self.assertTrue(idx.get().por_id["rascunhos/whatsapp-status/2026-11-01-textos.md"]["publico"]["principal"]
                        ["grande"])


# --- K · zip --------------------------------------------------------------------------------------------------------

class ZipTest(Http):
    def zip(self, iid=LOTE):
        return self.req(f"/api/biblioteca/zip?id={quote(iid, safe='')}")

    def test_zip_do_lote(self):
        code, h, body = self.zip()
        self.assertEqual((code, h["content-type"]), (200, "application/zip"))
        self.assertNotIn("content-length", h)
        self.assertEqual(h["content-disposition"], "attachment; filename=\"2026-11-01-status-ana.zip\"; "
                                                   "filename*=UTF-8''2026-11-01-status-ana.zip")
        zf = zipfile.ZipFile(io.BytesIO(body))
        self.assertIsNone(zf.testzip())
        self.assertEqual(sorted(zf.namelist()), sorted([
            "final/status-01-quem-9x16.jpg", "final/status-01-quem-v2-9x16.jpg", "final/status-05-preco-9x16.jpg",
            "final/status-06-convite-9x16.mp4", "textos/folha.md", "textos/2026-11-01-textos.md",
            "textos/2026-11-01-revisao.md", "textos/2026-11-01-parecer-juridico.md"]))
        self.assertEqual(zf.read("final/status-06-convite-9x16.mp4"), b"0123456789")
        t = zf.read("textos/2026-11-01-textos.md").decode()
        self.assertNotIn(GH, t)
        self.assertIn("[segredo]", t)
        self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist()))
        self.assertEqual(self.idx.get().por_id[LOTE]["publico"]["zip"], {"n": 8, "bytes": sum(
            f.size for f, _ in self.idx.get().por_id[LOTE]["zip"])})

    def test_nomes_repetidos(self):
        L = "rascunhos/media/2026-10-01-r"
        self.w(f"{L}/a.jpg")
        self.w(f"{L}/folha.md", "# R\n`rascunhos/x/folha.md`\n")
        self.w("rascunhos/x/folha.md", "# outra folha")
        idx = self.novo_indice()
        self.assertEqual([a for _, a in idx.get().por_id[L]["zip"]], ["a.jpg", "textos/folha.md", "textos/2-folha.md"])

    def test_sink_sem_seek(self):
        class Sink:
            def __init__(self):
                self.b = bytearray()

            def write(self, d):
                self.b += d
                return len(d)

        s = Sink()
        biblioteca.escrever_zip(s, self.idx.get().por_id[LOTE]["zip"])
        zf = zipfile.ZipFile(io.BytesIO(bytes(s.b)))
        self.assertIsNone(zf.testzip())
        self.assertEqual(len(zf.namelist()), 8)

    def test_ficheiro_trocado_a_meio_salta(self):
        entradas = self.idx.get().por_id[LOTE]["zip"]
        p = self.root / LOTE / "final/status-01-quem-v2-9x16.jpg"
        p.unlink()
        p.write_bytes(b"outro")
        buf = io.BytesIO()
        biblioteca.escrever_zip(buf, entradas)
        nomes = zipfile.ZipFile(io.BytesIO(buf.getvalue())).namelist()
        self.assertNotIn("final/status-01-quem-v2-9x16.jpg", nomes)
        self.assertEqual(len(nomes), 7)

    def test_limites_413(self):
        with mock.patch.object(biblioteca, "ZIP_MAX_N", 3):
            code, h, body = self.zip()
        self.assertEqual((code, json.loads(body)), (413, {"error": "grande demais, descarrega um a um"}))
        with mock.patch.object(biblioteca, "ZIP_MAX_BYTES", 10):
            self.assertEqual(self.zip()[0], 413)

    def test_slots_503(self):
        self.assertTrue(biblioteca.ZIP_SLOTS.acquire(False))
        self.assertTrue(biblioteca.ZIP_SLOTS.acquire(False))
        try:
            with mock.patch.object(biblioteca, "ZIP_ESPERA_S", 0.2):
                t0 = time.monotonic()
                code, h, body = self.zip()
            self.assertGreaterEqual(time.monotonic() - t0, 0.2)   # esperou pela vaga antes do 503
        finally:
            biblioteca.ZIP_SLOTS.release()
            biblioteca.ZIP_SLOTS.release()
        self.assertEqual((code, json.loads(body)), (503, {"error": "já há downloads a decorrer, tenta daqui a pouco"}))
        self.assertEqual(self.zip()[0], 200)   # slots libertados

    def test_pedido_real_espera_pela_vaga(self):
        # um pedido abortado que ainda segura a vaga não dá 503 se ela vagar dentro de ZIP_ESPERA_S
        self.assertTrue(biblioteca.ZIP_SLOTS.acquire(False))
        self.assertTrue(biblioteca.ZIP_SLOTS.acquire(False))
        threading.Timer(0.3, biblioteca.ZIP_SLOTS.release).start()
        try:
            code, h, body = self.zip()
        finally:
            biblioteca.ZIP_SLOTS.release()
        self.assertEqual(code, 200)
        self.assertIsNone(zipfile.ZipFile(io.BytesIO(body)).testzip())

    def test_verificar(self):
        q = f"/api/biblioteca/zip?id={quote(LOTE, safe='')}&verificar=1"
        code, h, body = self.req(q)
        self.assertEqual((code, body, h["cache-control"]), (204, b"", "no-store"))
        self.assertTrue(biblioteca.ZIP_SLOTS.acquire(False))   # não ficou com nenhuma vaga
        self.assertTrue(biblioteca.ZIP_SLOTS.acquire(False))
        try:
            t0 = time.monotonic()
            code, h, body = self.req(q)
            self.assertLess(time.monotonic() - t0, 1)   # a verificação não espera
        finally:
            biblioteca.ZIP_SLOTS.release()
            biblioteca.ZIP_SLOTS.release()
        self.assertEqual((code, json.loads(body)), (503, {"error": "já há downloads a decorrer, tenta daqui a pouco"}))
        with mock.patch.object(biblioteca, "ZIP_MAX_N", 3):
            self.assertEqual(self.get_json(q), (413, {"error": "grande demais, descarrega um a um"}))
        self.assertEqual(self.get_json("/api/biblioteca/zip?id=nao&verificar=1"), (404, NAO))
        self.assertEqual(self.req(q.replace("verificar=1", "verificar=0"))[0], 200)

    def lote_grande(self):
        L = "rascunhos/media/2026-11-01-grande"
        for i in range(3):
            self.w(f"{L}/final/v{i}.mp4", os.urandom(4 * 1024 * 1024))
        server.BIBLIOTECA = self.idx = self.novo_indice()
        return L

    def cliente_lento(self, iid):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        s.settimeout(10)
        host, port = self.host.split(":")
        s.connect((host, int(port)))
        s.sendall(f"GET /api/biblioteca/zip?id={quote(iid, safe='')} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        self.assertTrue(s.recv(1024).startswith(b"HTTP/1.0 200"))
        return s

    def esperar_slots(self, segundos=10):
        fim = time.monotonic() + segundos
        while time.monotonic() < fim:
            if biblioteca.ZIP_SLOTS.acquire(False):
                if biblioteca.ZIP_SLOTS.acquire(False):
                    biblioteca.ZIP_SLOTS.release()
                    biblioteca.ZIP_SLOTS.release()
                    return True
                biblioteca.ZIP_SLOTS.release()
            time.sleep(0.05)
        return False

    def test_cliente_fecha_a_meio_liberta_o_slot(self):
        L = self.lote_grande()
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            s = self.cliente_lento(L)
            s.close()
            self.assertTrue(self.esperar_slots())
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(self.zip(L)[0], 200)

    def test_cliente_parado_liberta_o_slot_no_timeout(self):
        L = self.lote_grande()
        with mock.patch.object(biblioteca, "ZIP_TIMEOUT_S", 0.5), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            s = self.cliente_lento(L)   # lê o início e deixa de ler, sem fechar
            try:
                time.sleep(0.2)
                self.assertFalse(self.esperar_slots(0.1))   # ainda a escrever: slot ocupado
                self.assertTrue(self.esperar_slots(10))
            finally:
                s.close()
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(self.zip(L)[0], 200)

    def test_item_de_um_ficheiro(self):
        self.w("relatorios/so.md", "# so")
        self.assertIsNone(self.novo_indice().get().por_id["relatorios/so.md"]["publico"]["zip"])
        self.assertEqual(self.get_json("/api/biblioteca/zip?id=relatorios%2Fso.md"), (404, NAO))
        self.assertEqual(self.get_json("/api/biblioteca/zip?id=relatorios/so.md"), (404, NAO))


# --- L · miniaturas -------------------------------------------------------------------------------------------------

class MiniTest(Http):
    def mini(self, chave):
        return self.req("/api/biblioteca/mini/" + "/".join(quote(s, safe="") for s in chave.split("/")))

    def test_uma_miniatura_de_cada_vez(self):   # 40 MP RGBA ≈ 330 MB descodificados; 2 passavam o mem_limit 512m
        self.assertTrue(biblioteca.MINI_SLOTS.acquire(False))
        try:
            self.assertFalse(biblioteca.MINI_SLOTS.acquire(False))
        finally:
            biblioteca.MINI_SLOTS.release()

    def test_sem_pillow_serve_o_original(self):
        with mock.patch.object(biblioteca, "Image", None):
            code, h, body = self.mini(f"{LOTE}/final/status-01-quem-9x16.jpg")
            self.assertEqual((code, h["content-type"]), (200, "image/jpeg"))
            self.assertEqual(body, b"\xff\xd8JPEGstatus-01-quem-9x16.jpg")
            self.assertEqual(h["cache-control"], "private, max-age=3600")
            self.assertIn("sandbox", h["content-security-policy"])
            code, h, body = self.mini(f"{LOTE}/final/status-06-convite-9x16-fotogramas/f-001.png")
            self.assertEqual((code, h["content-type"]), (200, "image/png"))
        self.assertEqual(os.listdir(self.mini_dir), [])

    def test_so_imagens(self):
        for k in (f"{LOTE}/final/status-06-convite-9x16.mp4", f"{LOTE}/folha.md", f"{LOTE}/video/voz-06.wav"):
            code, h, body = self.mini(k)
            self.assertEqual((code, json.loads(body)), (404, NAO), k)

    def test_poster_do_video(self):
        d = self.item(LOTE)["detalhe"]
        v = next(x for x in d["destaque"] if x["tipo"] == "video")
        self.assertTrue(v["mini"].startswith(f"api/biblioteca/mini/{LOTE}/final/status-06-convite-9x16-fotogramas/"
                                             "f-001.png?v="))
        self.assertIsNone(next(x for x in d["textos"])["mini"])

    def test_falha_de_pillow_serve_o_original_e_nao_repete(self):
        fake = mock.MagicMock()
        fake.open.side_effect = OSError("estragada")
        with mock.patch.object(biblioteca, "Image", fake), mock.patch.object(biblioteca, "ImageOps", fake):
            for _ in range(2):
                code, h, body = self.mini(f"{LOTE}/final/status-01-quem-9x16.jpg")
                self.assertEqual((code, body), (200, b"\xff\xd8JPEGstatus-01-quem-9x16.jpg"))
        self.assertEqual(fake.open.call_count, 1)

    @unittest.skipUnless(biblioteca.Image, "sem Pillow")
    def test_miniatura_com_pillow(self):
        img = biblioteca.Image.frombytes("RGB", (2000, 3000), random.Random(1).randbytes(2000 * 3000 * 3))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        self.assertGreater(len(buf.getvalue()), 1_500_000)
        self.w("rascunhos/media/l/final/grande.jpg", buf.getvalue())
        idx = self.novo_indice()
        server.BIBLIOTECA = idx
        code, h, body = self.mini("rascunhos/media/l/final/grande.jpg")
        self.assertEqual((code, h["content-type"]), (200, "image/jpeg"))
        self.assertLessEqual(len(body), 100 * 1024)
        m = biblioteca.Image.open(io.BytesIO(body))
        self.assertLessEqual(m.size[0], biblioteca.MINI_W)
        self.assertEqual(len(os.listdir(self.mini_dir)), 1)
        with mock.patch.object(biblioteca, "_mini_gerar", side_effect=AssertionError("regenerou")):
            self.assertEqual(self.mini("rascunhos/media/l/final/grande.jpg")[2], body)

    def _fake_pillow(self, open_):
        fake = mock.MagicMock()
        fake.DecompressionBombError = type("DecompressionBombError", (Exception,), {})
        fake.DecompressionBombWarning = type("DecompressionBombWarning", (RuntimeWarning,), {})
        fake.open.side_effect = open_
        return fake

    def _mini_grande(self, fake):
        k = f"{LOTE}/final/status-01-quem-9x16.jpg"
        filtros = list(warnings.filters)
        with mock.patch.object(biblioteca, "Image", fake), mock.patch.object(biblioteca, "ImageOps", fake), \
                mock.patch.object(biblioteca, "_mini", {"falhas": set(), "grandes": set(), "total": None}):
            for _ in range(2):   # 404 sem o original, e a 2.ª vez nem abre o ficheiro
                code, _, body = self.mini(k)
                self.assertEqual((code, json.loads(body)), (404, NAO))
        self.assertEqual(fake.open.call_count, 1)
        self.assertEqual(warnings.filters, filtros)   # o filtro de avisos é só local
        self.assertEqual(os.listdir(self.mini_dir), [])

    def test_bomba_erro_do_pillow_da_404(self):
        def abre(fp, formats=None):
            self.assertEqual(tuple(formats), ("JPEG", "PNG", "WEBP", "GIF"))
            raise fake.DecompressionBombError("156 MP")
        fake = self._fake_pillow(abre)
        self._mini_grande(fake)

    def test_aviso_de_bomba_vira_erro_e_da_404(self):
        def abre(fp, formats=None):
            warnings.warn("41 MP", fake.DecompressionBombWarning)
            return mock.MagicMock(width=8000, height=5001)
        fake = self._fake_pillow(abre)
        self._mini_grande(fake)

    def test_pixeis_verificados_antes_de_descodificar(self):
        im = mock.MagicMock(width=8000, height=5001)
        fake = self._fake_pillow(lambda fp, formats=None: im)
        self._mini_grande(fake)
        im.draft.assert_not_called()
        im.load.assert_not_called()
        fake.exif_transpose.assert_not_called()

    def test_limite_de_pixeis_e_formatos(self):
        self.assertEqual(biblioteca.MINI_MAX_PX, 40_000_000)
        if biblioteca.Image is not None:
            self.assertEqual(biblioteca.Image.MAX_IMAGE_PIXELS, biblioteca.MINI_MAX_PX)

    @unittest.skipUnless(biblioteca.Image, "sem Pillow")
    def test_bomba_png_real(self):
        # PNG 12500×12500 de poucos KB: recusado só pelo cabeçalho (antes levava o processo a 1,25 GB)
        buf = io.BytesIO()
        biblioteca.Image.MAX_IMAGE_PIXELS = None
        try:
            biblioteca.Image.new("1", (12500, 12500)).save(buf, "PNG")
        finally:
            biblioteca.Image.MAX_IMAGE_PIXELS = biblioteca.MINI_MAX_PX
        self.assertLess(len(buf.getvalue()), 600_000)
        self.w("rascunhos/media/l/final/bomba.png", buf.getvalue())
        server.BIBLIOTECA = self.novo_indice()
        t = time.perf_counter()
        code, _, body = self.mini("rascunhos/media/l/final/bomba.png")
        self.assertEqual((code, json.loads(body)), (404, NAO))
        self.assertLess(time.perf_counter() - t, 1)
        self.assertEqual(os.listdir(self.mini_dir), [])

    @unittest.skipUnless(biblioteca.Image, "sem Pillow")
    def test_formato_fora_da_lista_serve_o_original(self):
        buf = io.BytesIO()
        biblioteca.Image.new("RGB", (10, 10)).save(buf, "BMP")
        self.w("rascunhos/media/l/final/bmp.png", buf.getvalue())
        server.BIBLIOTECA = self.novo_indice()
        code, _, body = self.mini("rascunhos/media/l/final/bmp.png")
        self.assertEqual((code, body), (200, buf.getvalue()))

    def test_cache_cheia_apaga_as_mais_antigas(self):
        for i in range(5):
            p = self.mini_dir / f"{i:064x}.jpg"
            p.write_bytes(b"a" * 100)
            os.utime(p, (NOW + i, NOW + i))
        biblioteca._mini["total"] = None
        with mock.patch.object(biblioteca, "MINI_CACHE_MAX", 450), \
                mock.patch.object(biblioteca, "MINI_CACHE_ALVO", 300):
            biblioteca._mini_guardar("f" * 64, b"b" * 100)
        biblioteca._mini["total"] = None
        self.assertEqual(sorted(os.listdir(self.mini_dir)), [f"{3:064x}.jpg", f"{4:064x}.jpg", "f" * 64 + ".jpg"])

    def test_mini_dir_sem_escrita(self):
        with mock.patch.object(biblioteca, "MINI_DIR", self.root / "nao-existe"):
            biblioteca._mini_guardar("a" * 64, b"x")   # não rebenta
        self.assertFalse((self.root / "nao-existe").exists())


# --- M · só leitura · N · regressão --------------------------------------------------------------------------------

class SoLeituraTest(Http):
    def test_nada_escrito_fora_de_mini_dir(self):
        self.w("tasks/20261101-1000-a/SPEC.md", "# A")
        self.w("tasks/inbox/p.md", "# P")
        for d, _, fs in os.walk(self.root):
            if Path(d) != self.mini_dir:
                for f in fs:
                    os.chmod(Path(d) / f, 0o444)
                os.chmod(d, 0o555)

        def listagem():
            return sorted(str(Path(d) / f) for d, ds, fs in os.walk(self.root) for f in fs + ds)

        antes = listagem()
        idx = self.novo_indice()
        server.BIBLIOTECA = idx
        s = idx.get()
        for k in s.ficheiros:
            self.req(self.f(k))
            self.req(self.f(k, "?dl=1"), "HEAD")
            self.req("/api/biblioteca/mini/" + quote(k))
        for iid in s.por_id:
            self.req(f"/api/biblioteca/item?id={quote(iid, safe='')}")
            self.req(f"/api/biblioteca/zip?id={quote(iid, safe='')}")
        self.req("/api/biblioteca")
        depois = [p for p in listagem() if not p.startswith(str(self.mini_dir) + "/")]
        self.assertEqual(depois, antes)


class RegressaoTest(Http):
    def test_state_nao_muda(self):
        old = server.BIBLIOTECA
        server.BIBLIOTECA = biblioteca.Indice({}, self.tasks, {})
        server._cache["at"] = 0.0
        sem = set(json.loads(self.req("/api/state")[2]))
        server.BIBLIOTECA = old
        server._cache["at"] = 0.0
        com = json.loads(self.req("/api/state")[2])
        self.assertEqual(set(com), sem)
        self.assertNotIn("biblioteca", json.dumps(com).lower())

    def test_ci_smoke_vazio(self):
        server.BIBLIOTECA = biblioteca.Indice({}, self.root / "vazio", {})
        code, d = self.get_json("/api/biblioteca")
        self.assertEqual((code, d["itens"]), (200, []))

    def test_so_stdlib_e_pil(self):
        import ast
        import sys
        tree = ast.parse(Path(biblioteca.__file__).read_text())
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertLessEqual(mods - {"reader", "aprovacoes", "PIL"}, set(sys.stdlib_module_names))


if __name__ == "__main__":
    unittest.main()
