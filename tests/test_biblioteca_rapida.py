"""Biblioteca rápida (porte do Office #23, ADR-010): reconstrução em fundo, /f/ e /mini/ sem lock, resolver directo,
índice invertido das citações, cache dos pedidos e ETag/304 na lista. Pasta temporária e `NOW` fixo, como o
test_biblioteca; concorrência por Events (nunca sleep), o relógio só como guarda larga."""
import json
import os
import threading
import time
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import aprovacoes
import biblioteca
import reader
import server
from tests import cfg
from tests.test_biblioteca import GH, LOTE, NAO, Base, Http, pedido
from tests.test_biblioteca_equivalencia import arvore

OFFICE_CFG = cfg.LEGADO
GUARDA_S = 0.5   # tecto largo de relógio (AC1); a prova é o Event ainda por soltar


def pedido_cita(*caminhos, criado="2026-11-01", estado="aprovado") -> str:
    linhas = "\n".join(f"- `{c}`" for c in caminhos)
    return f"---\nestado: {estado}\ncriado: {criado}\nproduto: ebook\n---\n# Pedido\n\n{linhas}\n"


class Espiao:
    """Lock que conta quem lhe toca (acquire/__enter__), para provar que /f/ e /mini/ não o usam."""

    def __init__(self):
        self.real = threading.Lock()
        self.toques = 0

    def acquire(self, *a, **kw):
        self.toques += 1
        return self.real.acquire(*a, **kw)

    def release(self):
        self.real.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()


class FundoTest(Http):
    def lento(self):
        # _construir que só acaba quando o teste deixar (reconstrução lenta simulada, ≥ 2 s se ninguém soltar)
        orig, solta, dentro = self.idx._construir, threading.Event(), threading.Event()

        def construir(agora):
            dentro.set()
            solta.wait(10)
            return orig(agora)
        return mock.patch.object(self.idx, "_construir", side_effect=construir), solta, dentro

    def test_pedidos_durante_reconstrucao_lenta_respondem_com_o_anterior(self):
        antes = self.snap()
        _, h0, _ = self.req("/api/biblioteca")
        self.w("relatorios/novo.md", "# novo")
        self.now += biblioteca.TTL_S + 1
        p, solta, dentro = self.lento()
        with p:
            t0 = time.monotonic()
            self.assertIs(self.idx.get(), antes)   # dispara a reconstrução e não espera por ela
            self.assertTrue(dentro.wait(5))
            res = []

            def lista():
                res.append(self.req("/api/biblioteca"))
            ts = [threading.Thread(target=lista) for _ in range(5)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(5)
            item = self.req(f"/api/biblioteca/item?id={quote(LOTE)}")[0]
            self.assertFalse(solta.is_set())   # todos voltaram com a reconstrução ainda presa
            self.assertLess(time.monotonic() - t0, GUARDA_S * 4)
            self.assertEqual(sorted((c, h["etag"]) for c, h, _ in res), [(200, h0["etag"])] * 5)
            self.assertEqual(item, 200)
            self.assertNotIn("relatorios/novo.md", [i["id"] for i in json.loads(res[0][2])["itens"]])
            self.assertTrue(self.idx._thread.is_alive())
            solta.set()
            self.idx.aguardar(5)
        self.assertEqual(self.idx.scans, 2)   # 1 arranque + 1 só reconstrução para os 6 pedidos
        self.assertIn("relatorios/novo.md", self.snap().por_id)
        self.assertNotEqual(self.req("/api/biblioteca")[1]["etag"], h0["etag"])

    def test_f_head_e_mini_nao_tocam_no_lock(self):
        self.snap()
        espiao = Espiao()
        self.idx._lock = espiao
        self.now += biblioteca.TTL_S + 1   # índice velho: a lista refaria, o /f/ não
        k = "rascunhos/whatsapp-status/2026-11-01-textos.md"
        espiao.real.acquire()   # «reconstrução» a correr noutra thread: se o /f/ esperasse, ficava aqui
        try:
            self.assertEqual(self.req(self.f(k))[0], 200)
            self.assertEqual(self.req(self.f(k), "HEAD")[0], 200)
            self.assertEqual(self.req("/api/biblioteca/mini/" + quote(f"{LOTE}/final/status-01-quem-9x16.jpg"))[0], 200)
            self.assertEqual(self.get_json(self.f("rascunhos/nao-existe.md")), (404, NAO))
        finally:
            espiao.real.release()
        self.assertEqual((espiao.toques, self.idx.scans), (0, 1))

    def test_indice_velho_serve_o_disco_sem_lock(self):
        # pedido novo e ficheiro alterado depois do varrimento, índice velho, ninguém abre a lista: /f/ dá 200 como
        # no código antigo (que refazia o índice dentro do pedido), sem tocar no lock nem varrer
        self.snap()
        espiao = Espiao()
        self.idx._lock = espiao
        self.w("aprov-mkt/2026-11-09-novo.md", pedido(criado="2026-11-09"))
        k = "rascunhos/whatsapp-status/2026-11-01-revisao.md"
        self.w(k, "# Revisão alterada, maior\n")
        novo = self.f("aprovacoes-marketing/2026-11-09-novo.md")
        self.assertEqual(self.req(novo)[0], 404)   # dentro do TTL manda o índice, como antes
        self.now += 100 * biblioteca.TTL_S
        self.assertEqual(self.req(novo)[0], 200)
        code, _, body = self.req(self.f(k))
        self.assertEqual((code, body), (200, "# Revisão alterada, maior\n".encode()))
        self.assertEqual(self.get_json(self.f("rascunhos/segredo.md")), (404, NAO))
        self.assertEqual((espiao.toques, self.idx.scans), (0, 1))

    def test_primeiro_arranque_espera_um_so_varrimento(self):
        idx = self.novo_indice()
        idx.aquecer()
        ts = [threading.Thread(target=idx.get) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        idx.aguardar()
        self.assertEqual(idx.scans, 1)
        self.assertIn(LOTE, idx.get().por_id)
        idx.aquecer()   # já com índice: não refaz
        idx.aguardar()
        self.assertEqual(idx.scans, 1)

    def test_sem_threads_fica_o_anterior(self):
        antes = self.snap()
        self.now += biblioteca.TTL_S + 1
        with mock.patch("threading.Thread.start", side_effect=RuntimeError("sem threads")), \
                mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()) as err:
            self.assertIs(self.idx.get(), antes)
        self.assertIn("não arrancou a reconstrução", err.getvalue())
        self.assertTrue(self.idx._lock.acquire(blocking=False))   # o lock foi libertado
        self.idx._lock.release()


class ResolverDiretoTest(Http):
    """Sem índice, /f/ e /mini/ validam directamente no disco, com as regras do varrimento (AC3/AC4)."""

    def setUp(self):
        super().setUp()
        self.assertIsNone(self.idx.atual())

    def test_serve_o_que_o_varrimento_aceitaria(self):
        self.w("tasks/20261101-1000-a/SPEC.md", "# A")
        self.w("tasks/inbox/plano.md", "# P")
        self.w("relatorios/x.md", "# x")   # criado depois do Indice: sem índice, o disco manda
        for k in ("rascunhos/whatsapp-status/2026-11-01-textos.md", f"{LOTE}/final/status-01-quem-9x16.jpg",
                  "relatorios/x.md", "tarefas/20261101-1000-a/SPEC.md", "tarefas/inbox/plano.md",
                  "aprovacoes-marketing/2026-11-01-status.md"):
            with self.subTest(k=k):
                self.assertEqual(self.req(self.f(k))[0], 200)
                self.assertEqual(self.req(self.f(k), "HEAD")[0], 200)
        self.assertEqual(self.req("/api/biblioteca/mini/" + quote(f"{LOTE}/final/status-01-quem-9x16.jpg"))[0], 200)
        self.assertIsNone(self.idx.atual())   # não varreu
        self.assertEqual(self.idx.scans, 0)

    def test_recusa_o_que_o_varrimento_recusaria(self):
        fora = self.root / "fora"
        fora.mkdir()
        self.w("fora/a.md", "# fora")
        self.w("fora/b.jpg", b"jpg")
        os.symlink(fora, self.root / "rascunhos" / "link")                 # pasta symlink
        os.symlink(fora / "a.md", self.root / "relatorios" / "l.md")      # ficheiro symlink
        self.w("relatorios/segredo.md", "# s")                             # denylist (nome)
        self.w("relatorios/a.jpg", b"jpg")                                 # media em fonte media: false
        self.w("rascunhos/node_modules/a.md", "# n")                       # pasta negada
        self.w("rascunhos/credentials/a.md", "# n")                        # pasta com nome negado
        self.w("rascunhos/a.json", "{}")                                   # fora da allowlist
        self.w("rascunhos/" + "/".join("p" * 8) + "/a.md", "# fundo")      # mais fundo que PROF_MAX
        self.w("rascunhos/.oculta/a.md", "# o")                            # pasta escondida (e .arquivo)
        self.w("tasks/20261101-1000-a/notas.md", "# n")                    # só SPEC/PLAN/QA
        self.w("tasks/decisoes-x/SPEC.md", "# d")
        os.symlink(self.tasks / "20261101-1000-a", self.tasks / "20261101-9999-link")
        self.w("aprov-mkt/sem-front.md", "# sem frontmatter")
        self.w("aprov-mkt/README.md", "---\nestado: aprovado\n---\n# r\n")
        for k in ("rascunhos/link/a.md", "rascunhos/link/b.jpg", "relatorios/l.md", "relatorios/segredo.md",
                  "relatorios/a.jpg", "rascunhos/node_modules/a.md", "rascunhos/credentials/a.md", "rascunhos/a.json",
                  "rascunhos/" + "/".join("p" * 8) + "/a.md", "tarefas/20261101-1000-a/notas.md",
                  "tarefas/decisoes-x/SPEC.md", "tarefas/20261101-9999-link/SPEC.md",
                  "aprovacoes-marketing/sem-front.md", "aprovacoes-marketing/README.md",
                  "aprovacoes-geral/2026-11-01-status.md", "fora/a.md", "rascunhos/..%2f..%2ffora%2fa.md",
                  "rascunhos/%2e%2e/fora/a.md", "rascunhos/../fora/a.md", "rascunhos/.oculta/a.md"):
            with self.subTest(k=k):
                p = self.f(k) if "%" not in k and ".." not in k else "/api/biblioteca/f/" + k
                self.assertEqual(self.get_json(p), (404, NAO))
                self.assertEqual(self.req(p, "HEAD")[0], 404)
        self.assertEqual(self.get_json("/api/biblioteca/mini/rascunhos/link/b.jpg"), (404, NAO))
        self.assertEqual(self.idx.scans, 0)

    def test_dev_inode_confirmados(self):
        # o _abrir confirma o lstat do caminho validado: trocar o ficheiro por um symlink depois → 404
        p = self.w("relatorios/t.md", "# t")
        f = self.idx.direto(["relatorios", "t.md"])
        self.assertIsNotNone(f)
        os.unlink(p)
        os.symlink(self.root / "rascunhos" / "whatsapp-status" / "2026-11-01-textos.md", p)
        self.assertIsNone(biblioteca._abrir(f))


class EquivalenciaDiretoTest(Base):
    """AC4: sobre todas as fixtures, o resolver directo devolve exactamente o mesmo Ficheiro que o varrimento, e nada
    que o varrimento não tenha (cada ficheiro do disco, incluindo os negados)."""

    def test_mesmo_conjunto_que_o_varrimento(self):
        arvore(self)
        self.w("rascunhos/.arquivo/velho.md", "# arquivado")
        snap = self.novo_indice().get()
        idx = self.novo_indice()
        origens = [(r, root) for r, root in self.raizes.items()] + [("tarefas", self.tasks),
                                                                     ("aprovacoes-marketing", self.aprov)]
        vistos = 0
        for raiz, root in origens:
            for d, pastas, fs in os.walk(root, followlinks=True):
                for n in fs + [p for p in pastas if os.path.islink(os.path.join(d, p))]:
                    rel = os.path.relpath(os.path.join(d, n), root)
                    partes = [raiz, *Path(rel).parts]
                    with self.subTest(k="/".join(partes)):
                        self.assertEqual(idx.direto(partes), snap.ficheiros.get("/".join(partes)))
                    vistos += 1
        self.assertGreater(vistos, 80)
        self.assertEqual(idx.scans, 0)


class InvertidoTest(Base):
    """O índice invertido liga cada item aos mesmos pedidos que o ciclo antigo (item × pedido × citação), AC6."""

    def arvore(self):
        self.w("rascunhos/media/2026-11-02-outro/final/a.jpg", b"jpg")
        self.w("rascunhos/media/2026-11-02-outro-b/final/b.jpg", b"jpg")
        self.w("rascunhos/fotos/c.jpg", b"jpg")
        self.w("rascunhos/fotos/d.png", b"png")
        self.w("rascunhos/copy/texto.md", "# Texto\n")
        self.w("rascunhos/copy/texto.revisao.md", "# Revisão\n")
        self.w("relatorios/r.md", "# R\n")
        self.w("tasks/20261101-1000-a/SPEC.md", "# A\n")
        self.w("aprov-mkt/2026-11-02-a.md", pedido_cita("rascunhos/media/2026-11-02-outro", criado="2026-11-02"))
        self.w("aprov-mkt/2026-11-03-b.md", pedido_cita("rascunhos/media/2026-11-02-outro/final/a.jpg",
                                                        "rascunhos/media/2026-11-02-outro/final/a.jpg",
                                                        "rascunhos/fotos/c.jpg", criado="2026-11-03"))
        self.w("aprov-mkt/2026-11-04-c.md", pedido_cita("rascunhos/copy/texto.revisao.md", "rascunhos/fotos/d.png",
                                                        criado="2026-11-04", estado="pendente"))
        self.w("aprov-mkt/2026-11-05-d.md", pedido_cita("rascunhos/media/2026-11-02-outro-bx/x.jpg",
                                                        "rascunhos/media", "rascunhos/media/", criado="2026-11-05"))
        self.w("aprov-geral/2026-11-01-e.md", pedido_cita(f"{LOTE}/folha.md", "rascunhos/fotos/c.jpg",
                                                          criado="2026-11-01"))

    def test_mesmas_ligacoes_que_o_ciclo_antigo(self):
        self.arvore()
        idx = biblioteca.Indice(self.raizes, self.tasks, {"marketing": self.aprov, "geral": self.root / "aprov-geral"},
                                clock=lambda: self.now)
        s = idx.get()
        pedidos, _ = idx._pedidos()
        self.assertGreaterEqual(len(pedidos), 6)
        ligacoes = 0
        for gid, it in s.por_id.items():
            g, d = it["publico"]["genero"], it["detalhe"]
            if g == "lote":   # ciclo antigo, à letra
                esperado = [p for p in pedidos if any(c == gid or c.startswith(gid + "/") for _, c in p["citacoes"])]
            elif g == "pasta":
                cs_dest = [f["caminho"] for f in d["destaque"]]
                esperado = [p for p in pedidos if any(k in [c for _, c in p["citacoes"]] for k in cs_dest)]
            else:
                cs = {t["caminho"] for t in d["textos"] if not t["ligado"]}
                esperado = [p for p in pedidos if any(c in cs for _, c in p["citacoes"])]
            with self.subTest(gid=gid):
                self.assertEqual([(p["fonte"], p["ficheiro"]) for p in d["pedidos"]],
                                 [(p["fonte"], p["ficheiro"]) for p in reversed(esperado)])
            ligacoes += len(esperado)
        self.assertGreaterEqual(ligacoes, 6)
        # fronteira do prefixo: «…-outro-b» não é ligado pelo pedido que cita «…-outro»
        self.assertEqual([p["ficheiro"] for p in s.por_id["rascunhos/media/2026-11-02-outro-b"]["detalhe"]["pedidos"]],
                         [])
        self.assertEqual([p["ficheiro"] for p in s.por_id["rascunhos/media/2026-11-02-outro"]["detalhe"]["pedidos"]],
                         ["2026-11-03-b.md", "2026-11-02-a.md"])
        estados = {f["caminho"]: f["estado"] for f in s.por_id["rascunhos/fotos"]["detalhe"]["destaque"]}
        self.assertEqual(estados, {"rascunhos/fotos/c.jpg": "aprovado", "rascunhos/fotos/d.png": "pendente"})

    def test_fonte_media_com_outro_nome(self):
        # nada preso a «rascunhos»: as fontes media vêm da config (MULTI: «material»)
        cfg.usar(self, cfg.MULTI)
        root = self.root / "material"
        self.w("material/media/l1/final/a.jpg", b"a")
        self.w("material/media/l1-b/final/b.jpg", b"b")
        self.w("aprov-mkt/2026-11-06-m.md", pedido_cita("material/media/l1/final/a.jpg", criado="2026-11-06"))
        s = biblioteca.Indice({"material": root}, self.tasks, {"marketing": self.aprov}, clock=lambda: self.now).get()
        self.assertEqual([p["ficheiro"] for p in s.por_id["material/media/l1"]["detalhe"]["pedidos"]],
                         ["2026-11-06-m.md"])
        self.assertEqual(s.por_id["material/media/l1-b"]["detalhe"]["pedidos"], [])


class PedidosCacheTest(Base):
    def test_so_rele_o_que_mudou(self):
        self.w("aprov-mkt/2026-11-02-b.md", pedido(criado="2026-11-02"))
        self.snap()
        lidos = []
        orig = aprovacoes._ler
        with mock.patch.object(aprovacoes, "_ler", side_effect=lambda r, n: lidos.append(n) or orig(r, n)):
            self.now += biblioteca.TTL_S + 1
            self.snap()
            self.idx.aguardar()
            self.assertEqual((lidos, self.idx.scans), ([], 2))   # nada mudou: nenhum pedido relido
            self.w("aprov-mkt/2026-11-02-b.md", pedido(criado="2026-11-02", estado="recusado"), idade=60)
            self.now += biblioteca.TTL_S + 1
            self.snap()
            self.idx.aguardar()
        self.assertEqual(lidos, ["2026-11-02-b.md"])   # só o que mudou
        ped = self.snap().por_id[LOTE]["detalhe"]["pedidos"]
        self.assertEqual([(p["ficheiro"], p["estado"]) for p in ped],
                         [("2026-11-02-b.md", "recusado"), ("2026-11-01-status.md", "aprovado")])

    def test_pedido_trocado_por_symlink_sai(self):
        self.snap()
        p = self.aprov / "2026-11-01-status.md"
        texto = p.read_bytes()
        p.unlink()
        (self.root / "fora.md").write_bytes(texto)
        os.symlink(self.root / "fora.md", p)
        self.now += biblioteca.TTL_S + 1
        self.snap()
        self.idx.aguardar()
        self.assertEqual(self.snap().por_id[LOTE]["detalhe"]["pedidos"], [])


class EtagTest(Http):
    def test_304_com_if_none_match(self):
        for ae in ({}, {"Accept-Encoding": "gzip"}):
            with self.subTest(ae=ae):
                code, h, body = self.req("/api/biblioteca", headers=ae)
                etag = h["etag"]
                self.assertEqual((code, h["cache-control"], h["vary"]), (200, "private, no-cache", "Accept-Encoding"))
                self.assertRegex(etag, r'^W/"[0-9a-f]{32}(-gz)?"$')
                self.assertEqual(etag.endswith('-gz"'), bool(ae))
                for inm in (etag, etag.removeprefix("W/"), f'"outro", {etag}', "*"):
                    code, h2, body2 = self.req("/api/biblioteca", headers={**ae, "If-None-Match": inm})
                    self.assertEqual((code, body2, h2["etag"], h2["cache-control"]),
                                     (304, b"", etag, "private, no-cache"))
                code, _, body3 = self.req("/api/biblioteca", headers={**ae, "If-None-Match": '"outro"'})
                self.assertEqual((code, body3), (200, body))
        e_id = self.req("/api/biblioteca")[1]["etag"]
        e_gz = self.req("/api/biblioteca", headers={"Accept-Encoding": "gzip"})[1]["etag"]
        self.assertNotEqual(e_id, e_gz)   # variantes diferentes, ETags diferentes
        self.assertEqual(self.req("/api/biblioteca", headers={"Accept-Encoding": "gzip", "If-None-Match": e_id})[0],
                         200)
        # muda um item → novo ETag depois da reconstrução; o antigo já não dá 304
        self.w("relatorios/novo.md", "# novo")
        self.now += biblioteca.TTL_S + 1
        self.req("/api/biblioteca")
        self.idx.aguardar()
        code, h, _ = self.req("/api/biblioteca", headers={"If-None-Match": e_id})
        self.assertEqual(code, 200)
        self.assertNotEqual(h["etag"], e_id)
        self.assertEqual(self.req("/api/biblioteca", "HEAD")[0], 405)

    def test_etag_estavel_sem_mudancas(self):
        _, h, body = self.req("/api/biblioteca")
        self.now += biblioteca.TTL_S + 1   # refaz: «gerado» muda, os itens não → mesmo ETag (fraco) e 304
        self.req("/api/biblioteca")
        self.idx.aguardar()
        self.assertEqual(self.idx.scans, 2)
        _, h2, body2 = self.req("/api/biblioteca")
        self.assertNotEqual(json.loads(body)["gerado"], json.loads(body2)["gerado"])
        self.assertEqual(h2["etag"], h["etag"])
        self.assertEqual(self.req("/api/biblioteca", headers={"If-None-Match": h["etag"]})[0], 304)

    def test_item_e_f_sem_v_continuam_no_store(self):
        k = "rascunhos/whatsapp-status/2026-11-01-textos.md"
        for p in (f"/api/biblioteca/item?id={quote(LOTE)}", self.f(k), self.f(k, "?dl=1")):
            h = self.req(p, headers={"If-None-Match": "*"})[1]
            self.assertEqual(h["cache-control"], "no-store", p)
            self.assertNotIn("etag", h)
        self.assertEqual(self.req(self.f(k, "?v=1"))[1]["cache-control"], "private, max-age=3600")

    def test_corpo_igual_ao_json_dumps(self):
        s = self.snap()
        self.assertEqual(s.corpo_json, json.dumps({"gerado": int(s.gerado), "itens": s.itens}, ensure_ascii=False,
                                                  separators=(",", ":")).encode())


class MascaraTest(Base):
    def test_mascara_curta_igual_ao_reader(self):
        biblioteca._mascara_curta.cache_clear()
        for s in ("Status do Ana", f"token {GH}", "", "a" * 512, "Bearer " + "x" * 40):
            self.assertEqual(biblioteca._mascara_curta(s), reader.mask(s))
        self.snap()
        n = biblioteca._mascara_curta.cache_info().hits
        self.now += biblioteca.TTL_S + 1
        self.snap()
        self.idx.aguardar()
        self.assertGreater(biblioteca._mascara_curta.cache_info().hits, n)   # o 2.º varrimento reaproveita
        longo = "x " * 400 + GH
        self.assertEqual(biblioteca._corta(longo, 10_000), reader.mask(longo))   # > 512: fora da cache, igual


class ServidorTest(Http):
    def test_aquecer_no_arranque(self):
        self.assertIn("BIBLIOTECA.aquecer()", Path(server.__file__).read_text())
