"""QA da 0.2.0 (Biblioteca rápida e arquivo): o que os testes do goal não provavam directamente.

1. Biblioteca com milhares de ficheiros e reconstrução lenta: a lista, o /item, o /f/ (GET e HEAD) e o /mini/
   respondem com o instantâneo anterior, sem esperar; 1 só reconstrução para os pedidos todos (AC1, AC3).
2. Arranque sem índice e varrimento lento: o /f/ responde pelo resolver directo; só a lista espera (AC2, AC3).
3. O Office nunca escreve nas fontes (ADR-002, AC12, AC16): no e2e dos modos VPS e Docker, com a biblioteca só de
   leitura durante a fase do Office (como o mount `ro`) e um audit hook que regista as escritas das threads do Office.

Concorrência por Events (nunca sleep); datas pelo `NOW` fixo; o relógio só como guarda larga e para a medição.
"""
import os
import sys
import threading
import time
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import pytest

import biblioteca
from tests import cfg
from tests.test_arquivo_e2e import ITEM, LOTE, ctx, passagem  # noqa: F401 (ctx é fixture)
from tests import test_biblioteca_arquivo as _tab
from tests.test_biblioteca import LOTE as LOTE_BIB, Http
from tests.test_runner_comum import officecfg

OFFICE_CFG = cfg.LEGADO
N_PASTAS, N_POR_PASTA = 40, 100   # 4000 documentos + o lote do Base
GUARDA_S = 2.0


class MilharesTest(Http):
    def setUp(self):
        super().setUp()
        for p in range(N_PASTAS):
            d = self.root / "relatorios" / f"p{p:02d}"
            d.mkdir(parents=True)
            for n in range(N_POR_PASTA):
                f = d / f"2026-10-{n % 28 + 1:02d}-doc-{n:03d}.md"
                f.write_text(f"# Documento {p}-{n}\n\nTexto.\n", encoding="utf-8")
                os.utime(f, (self.now - 3 * 86400, self.now - 3 * 86400))
        self.k = "relatorios/p07/2026-10-14-doc-013.md"

    def lento(self, idx):
        orig, solta, dentro = idx._construir, threading.Event(), threading.Event()

        def construir(agora):
            dentro.set()
            solta.wait(30)
            return orig(agora)
        return mock.patch.object(idx, "_construir", side_effect=construir), solta, dentro

    def test_reconstrucao_lenta_com_milhares_nao_faz_esperar(self):
        t = time.thread_time()
        snap = self.snap()   # 1.º varrimento real dos ~4000 ficheiros (só para a evidência do QA)
        cpu = time.thread_time() - t
        self.assertGreaterEqual(len(snap.por_id), N_PASTAS * N_POR_PASTA)
        _, h0, corpo0 = self.req("/api/biblioteca")
        self.w("relatorios/novo.md", "# novo")
        self.now += biblioteca.TTL_S + 1
        p, solta, dentro = self.lento(self.idx)
        res, tempos = [], []
        with p:
            self.idx.get()               # dispara a reconstrução (presa) e volta logo
            self.assertTrue(dentro.wait(5))

            def pedir(path, method="GET"):
                t0 = time.monotonic()
                r = self.req(path, method)
                tempos.append(time.monotonic() - t0)
                res.append((path, method, r[0], r[1].get("etag"), len(r[2])))
            pedidos = [("/api/biblioteca", "GET")] * 5 + [
                (f"/api/biblioteca/item?id={quote(self.k)}", "GET"), (self.f(self.k), "GET"), (self.f(self.k), "HEAD"),
                ("/api/biblioteca/mini/" + quote(f"{LOTE_BIB}/final/status-01-quem-9x16.jpg"), "GET")]
            ts = [threading.Thread(target=pedir, args=a) for a in pedidos]
            for x in ts:
                x.start()
            for x in ts:
                x.join(10)
            self.assertFalse(solta.is_set())   # todos voltaram com a reconstrução ainda presa
            self.assertEqual(len(res), len(pedidos))
            self.assertTrue(all(c == 200 for _, _, c, _, _ in res), res)
            self.assertEqual({e for p_, m, _, e, _ in res if p_ == "/api/biblioteca"}, {h0["etag"]})
            self.assertEqual({n for p_, _, _, _, n in res if p_ == "/api/biblioteca"}, {len(corpo0)})
            self.assertLess(max(tempos), GUARDA_S)
            solta.set()
            self.idx.aguardar(30)
        self.assertEqual(self.idx.scans, 2)   # arranque + 1 só reconstrução para os 9 pedidos
        self.assertIn("relatorios/novo.md", self.snap().por_id)
        print(f"\nQA milhares: {len(snap.por_id)} itens; 1.º varrimento {cpu:.3f} s CPU; lista {len(corpo0)} B; "
              f"pior pedido durante a reconstrução presa {max(tempos) * 1000:.1f} ms", file=sys.stderr)

    def test_304_com_milhares(self):
        _, h, corpo = self.req("/api/biblioteca")
        code, h2, corpo2 = self.req("/api/biblioteca", headers={"If-None-Match": h["etag"]})
        self.assertEqual((code, corpo2, h2["etag"]), (304, b"", h["etag"]))
        print(f"\nQA 304: 200 com {len(corpo)} B, depois 304 com 0 B (ETag {h['etag']})", file=sys.stderr)

    def test_arranque_sem_indice_f_nao_espera_e_so_a_lista_espera(self):
        idx = self.novo_indice()
        server_idx = mock.patch("server.BIBLIOTECA", idx)
        p, solta, dentro = self.lento(idx)
        lista = []
        with server_idx, p:
            idx.aquecer()   # como o server.py no arranque
            self.assertTrue(dentro.wait(5))
            self.assertIsNone(idx.atual())
            code, _, body = self.req(self.f(self.k))
            self.assertEqual((code, body), (200, "# Documento 7-13\n\nTexto.\n".encode()))
            self.assertEqual(self.req(self.f(self.k), "HEAD")[0], 200)
            self.assertEqual(self.req(self.f("relatorios/p07/nao-existe.md"))[0], 404)
            t = threading.Thread(target=lambda: lista.append(self.req("/api/biblioteca")[0]))
            t.start()
            t.join(0.3)
            self.assertEqual(lista, [])   # a lista (sem índice nenhum) espera pelo 1.º varrimento: AC2
            solta.set()
            t.join(30)
            idx.aguardar(30)
        self.assertEqual((lista, idx.scans), ([200], 1))


# ---------------------------------------------------------------- o Office nunca escreve nas fontes

_REG = {"on": False, "ev": []}
_ESCRITA = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_EVENTOS = {"os.rename", "os.replace", "os.link", "os.symlink", "os.remove", "os.rmdir", "os.mkdir", "os.truncate",
            "os.chmod", "os.chown", "os.utime", "shutil.rmtree", "shutil.move", "shutil.copyfile"}


def _hook(ev, args):
    if not _REG["on"] or threading.current_thread() is threading.main_thread():
        return
    if ev == "open":
        path, mode, flags = (tuple(args) + (None, None))[:3]
        escreve = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                  (mode is None and isinstance(flags, int) and flags & _ESCRITA)
        if escreve and not isinstance(path, int):   # open(fd) embrulha um fd cuja criação já ficou registada
            _REG["ev"].append((ev, path))
    elif ev in _EVENTOS:
        _REG["ev"].append((ev, args[0] if args else None))


sys.addaudithook(_hook)   # não se pode remover: fica inerte fora destes testes (_REG["on"] falso)


def _so_leitura(raiz: Path, ligar: bool):
    # como o mount `ro` do contentor: tira a escrita a pastas e ficheiros da biblioteca (e repõe para o runner)
    for d, ps, fs in os.walk(raiz):
        for n in fs:
            p = Path(d) / n
            if not p.is_symlink():
                m = p.stat().st_mode
                os.chmod(p, (m & ~0o222) if ligar else (m | 0o200))
        m = os.stat(d).st_mode
        os.chmod(d, (m & ~0o222) if ligar else (m | 0o700))


def _escritas_fora_da_fila(cfg, mini):
    fila = str(officecfg.caminho(cfg, "arquivo_fila"))
    ok = (fila + os.sep, str(mini) + os.sep)
    return [(e, p) for e, p in _REG["ev"] if not (isinstance(p, (str, bytes, os.PathLike))
                                                    and os.fspath(p).startswith(ok))]



@pytest.mark.skipif(os.geteuid() == 0, reason="como root o chmod não impede a escrita")
@pytest.mark.parametrize("modo", ["vps", "docker"])
def test_office_nunca_escreve_nas_fontes(ctx, monkeypatch, tmp_path, modo):  # noqa: F811
    cfg, path, o = ctx
    mini = tmp_path / "mini"
    mini.mkdir()
    monkeypatch.setattr(biblioteca, "MINI_DIR", mini)
    bib = officecfg.caminho(cfg, "biblioteca")

    def fase_office(fn):
        _so_leitura(bib, True)
        _REG.update(on=True, ev=[])
        try:
            return fn()
        finally:
            _REG["on"] = False
            _so_leitura(bib, False)
            fora = _escritas_fora_da_fila(cfg, mini)
            assert fora == [], fora

    def office_arquivar():
        assert o.f(ITEM)[0] == 200
        assert o.req("/api/biblioteca")[0] == 200
        assert o.req("/api/biblioteca/zip?id=" + quote(ITEM, safe=""))[0] == 200
        assert o.req("/api/biblioteca/mini/" + quote(f"{LOTE}/final/a.jpg"))[0] in (200, 404)
        code, d = o.post("arquivar", ITEM)
        assert code == 202
        assert sorted(os.listdir(officecfg.caminho(cfg, "arquivo_fila"))) == [f"{d['id']}.json"]
        return d

    antes = sorted(p.relative_to(bib).as_posix() for p in bib.rglob("*"))
    d = fase_office(office_arquivar)
    assert sorted(p.relative_to(bib).as_posix() for p in bib.rglob("*")) == antes   # o POST não mexeu em nada
    passagem(modo, cfg, path, monkeypatch)
    assert (bib / "notas/.arquivo/rel/x.md").exists()

    def office_link_antigo_e_repor():
        o.refazer()
        code, body = o.f(ITEM)
        assert code == 200 and body.startswith(b"# Relat")
        assert o.item(ITEM)["arquivo"]["arquivado"]["motivo"] == "pedido no Office"
        assert o.req("/api/biblioteca?arquivo=1")[0] == 200
        code, d2 = o.post("repor", ITEM)
        assert code == 202
        return d2

    d2 = fase_office(office_link_antigo_e_repor)
    passagem(modo, cfg, path, monkeypatch)
    assert (bib / "notas/rel/x.md").exists() and not (bib / "notas/.arquivo/rel/x.md").exists()
    assert d["id"] != d2["id"]


# ---------------------------------------------------------------- git: nomes nunca em argv, push seguro (AC21, AC22)

def test_git_sem_nomes_em_argv_e_push_seguro(tmp_path, monkeypatch):
    import subprocess

    import arquivador
    from tests import test_runner_arquivo as a
    from tests import test_runner_arquivo_git as g
    from tests import test_runner_comum as c
    c.ambiente(monkeypatch, tmp_path)
    cfg_ = a.config(tmp_path)[0]
    nomes = ("rel/rel atório; $(touch x).md", "rel/rel atório; $(touch x).copy.md", "rel/y.md")
    r = g.repo_fonte(cfg_, nomes)
    remoto = g.com_remoto(tmp_path, r)
    chamadas, real, on = [], subprocess.run, [False]

    def espiao(cmd, *args, **kw):
        if on[0]:   # só as chamadas do runner (os helpers do teste também usam o subprocess.run)
            chamadas.append((list(cmd), kw))
        return real(cmd, *args, **kw)
    monkeypatch.setattr(arquivador.subprocess, "run", espiao)

    def passar(*args, **kw):
        on[0] = True
        try:
            return g.arquivar(cfg_, *args, **kw)
        finally:
            on[0] = False
    linhas = passar(nomes[0])
    assert linhas[-1] == "biblioteca: push de notas para origin/main", linhas
    linhas = passar(nomes[0], stem=g.OUTRO, accao="repor")
    assert linhas[-1] == "biblioteca: push de notas para origin/main", linhas
    assert g.git(remoto, "log", "--format=%s", "main").splitlines() == [
        "chore(arquivo): repor 2 ficheiros", "chore(arquivo): 2 ficheiros", "init"]
    assert not (r / "x").exists() and not (tmp_path / "x").exists()   # nada correu por shell
    gits = [(cmd, kw) for cmd, kw in chamadas if cmd[0] == "git"]
    assert gits and len(gits) == len(chamadas)
    for cmd, kw in gits:
        assert not any("atório" in x for x in cmd), cmd          # nomes só por stdin (pathspec-from-file)
        assert cmd[:2] == ["git", "--literal-pathspecs"] and "core.hooksPath=/dev/null" in cmd
        assert kw.get("timeout") == 120 and kw["env"]["GIT_TERMINAL_PROMPT"] == "0" and not kw.get("shell")
        assert "--force" not in cmd
    pushes = [cmd for cmd, _ in gits if "push" in cmd]
    assert len(pushes) == 2 and all("-f" not in p and not any(x.startswith("+") for x in p) for p in pushes)
    assert any("--pathspec-from-file=-" in cmd and "--pathspec-file-nul" in cmd for cmd, _ in gits)


# ------------------------------------------------------------ toques repetidos no telemóvel (AC12: 409 sem corridas)


class CorridaPostTest(_tab.Base):
    post, req, bom, pedidos = _tab.PostTest.post, _tab.PostTest.req, _tab.PostTest.bom, _tab.PostTest.pedidos

    def test_posts_em_paralelo_para_o_mesmo_item(self):
        comeca, res = threading.Barrier(10), []

        def um():
            comeca.wait(5)
            res.append(self.post(self.bom())[0])
        ts = [threading.Thread(target=um) for _ in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(sorted(res), [202] + [409] * 9)
        self.assertEqual(len(self.pedidos()), 1)
