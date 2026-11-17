"""Equivalência com a 0.1.4 (AC9 da Biblioteca rápida): sobre as mesmas fixtures, a lista (sem «gerado»), o /item (sem
a chave «arquivo», a única aditiva), o /zip e o /f/ dão os mesmos bytes que o código da 0.1.4.

O golden (tests/fixtures/biblioteca_0_1_4.json, sha256 por rota) foi gerado com o código da 0.1.4, antes de qualquer
mudança, com `NOW` fixo, mtimes fixos (os.utime) e datas do zip em UTC. Para o refazer (só se as fixtures mudarem e com
o código da 0.1.4): `BIBLIOTECA_GOLDEN=escrever python -m pytest tests/test_biblioteca_equivalencia.py -q`.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import biblioteca
import server
from tests import cfg
from tests.test_biblioteca import BEARER, GH, H, Http, pedido

OFFICE_CFG = cfg.LEGADO
GOLDEN = Path(__file__).parent / "fixtures" / "biblioteca_0_1_4.json"
CAB_F = ("content-type", "content-disposition", "cache-control", "content-security-policy", "content-length",
         "accept-ranges")
# caminhos que o varrimento recusa (symlink, denylist, pasta negada, media fora de fonte media, fundo demais, «..»)
NEGADOS = ("rascunhos/link-pasta/a.md", "relatorios/link.md", "relatorios/segredo.md", "relatorios/a.jpg",
           "rascunhos/node_modules/a.md", "rascunhos/a.json", "rascunhos/" + "/".join("p" * 8) + "/a.md",
           "tarefas/20261101-1000-a/notas.md", "tarefas/decisoes-x/SPEC.md", "aprovacoes-marketing/sem-front.md",
           "aprovacoes-marketing/README.md", "rascunhos/.oculta/a.md", "tarefas/20261101-9999-link/SPEC.md")


def arvore(t) -> None:
    """Fixtures da equivalência: o lote do test_biblioteca + tarefas, documentos, pastas, pedidos e negados."""
    w = t.w
    for n in ("SPEC.md", "PLAN.md", "QA.md"):
        w(f"tasks/20261101-1000-a/{n}", f"# {n[:-3]} do A\n\nFrase da tarefa A em {n}.\n", idade=30 * H)
    w("tasks/20261101-1000-a/meta.json", json.dumps({"project": "MenuQR (parecer UX)", "task": "# Meta A"}))
    w("tasks/20261101-1000-a/notas.md", "# n")
    w("tasks/20261101-1100-b/PLAN.md", "# Plano B\n", idade=20 * H)
    w("tasks/20261101-1100-b/meta.json", json.dumps({"project": "Projeto 90", "task": "x\n# Tarefa B\nmais"}))
    w("tasks/20261101-1200-c/QA.md", "sem título\n", idade=10 * H)
    w("tasks/decisoes-x/SPEC.md", "# d")
    w("tasks/inbox/plano-x.md", "---\nproduto: joga\n---\n# Plano X\n\nPrimeira frase do plano.\n", idade=5 * H)
    w("tasks/inbox/sub/z.md", "# z")
    os.symlink(t.tasks / "20261101-1000-a", t.tasks / "20261101-9999-link")
    for i, n in enumerate(("x.md.juridico.md", "x.copy.md", "x.md", "x.juridico.md", "x.md.revisao.md")):
        w(f"rascunhos/{n}", f"# Título {n}\n\nFrase de {n}.\n", idade=10 * H - i)
    w("rascunhos/y.revisao.md", "# Y rev", idade=5 * H)
    w("rascunhos/y.copy.md", "# Y copy", idade=6 * H)
    w("rascunhos/site-blog/b.png", b"png-b", idade=3 * H)
    w("rascunhos/site-blog/a.jpg", b"jpg-a", idade=4 * H)
    w("rascunhos/site-blog/v.mp4", b"mp4-v", idade=5 * H)
    w("rascunhos/site-blog/v-fotogramas/folha.png", b"png")
    w("rascunhos/site-blog/v-fotogramas/2.png", b"png-2")
    w("rascunhos/site-blog/texto.md", "# Texto do site\n\nO site do blog tem um texto curto.\n")
    w("rascunhos/solta.jpg", b"solta", idade=70 * H)
    w("rascunhos/peças/peça-01.jpg", b"jpg-peca")
    L = "rascunhos/media/2026-10-26-s45-zf-mz-mascote"
    w(f"{L}/b.jpg", b"b", idade=60 * H)
    w(f"{L}/sub/a.mp4", b"a-mp4", idade=61 * H)
    w(f"{L}/sub/x-fotogramas/1.png", b"1")
    w(f"{L}/prompts/p.jpg", b"p")
    w(f"{L}/sub/c.prompt.md", "# c")
    w(f"{L}/briefing.md", "sem título")
    w("rascunhos/media/2026-10-23-ebook-lote1/a.jpg", b"ebook", idade=100 * H)
    w("rascunhos/media/2026-11-02-outro/final/a.jpg", b"outro")
    w("rascunhos/media/2026-11-02-outro-b/final/b.jpg", b"outro-b")
    w("rascunhos/2026-10-24-parecer-juridico-x.md", "# P\n\nParecer sobre a loja.\n")
    w("relatorios/diario/r.md", "# R\n\nData: 2026-11-01\nFontes: a, b\n", idade=12 * H)
    w("relatorios/grande.md", b"# g\n" + b"a" * biblioteca.TEXTO_MAX)
    w("relatorios/tabela.csv", f"a,b\n1,{GH}\n")
    w("relatorios/notas.txt", f"auth {BEARER}\n")
    w("propostas/p.pdf", b"%PDF-1.4 x")
    w("mkt-knowledge/k.txt", "k")
    w("rnd-estudos/2026-11-01-e.md", "---\nx: y\n---\n# Estudo\n\n## Sec\n\n- item\n" + "z" * 300, idade=49 * H)
    w("rnd-estudos/z.md", "---\nproduto: menuqr\n---\n# Estudo loja", idade=47 * H)
    w("aprov-mkt/2026-11-02-dois.md", pedido(produto="loja, ebook", criado="2026-11-02"))
    w("aprov-mkt/2026-11-03-b.md", "---\nestado: pendente\ncriado: 2026-11-03\nproduto: blog\n---\n# Pedido\n\n"
      "- `rascunhos/media/2026-11-02-outro/final/a.jpg`\n- `rascunhos/site-blog/a.jpg`\n"
      "- `rascunhos/x.copy.md`\n05 BLOQUEADA — `rascunhos/site-blog/b.png`\n")
    w("aprov-mkt/sem-front.md", "# nada")
    w("aprov-mkt/README.md", "---\nestado: aprovado\n---\n")
    # negados: nunca entram no índice nem se servem
    fora = t.root / "fora"
    fora.mkdir()
    w("fora/a.md", "# fora")
    os.symlink(fora, t.root / "rascunhos" / "link-pasta")
    os.symlink(fora / "a.md", t.root / "relatorios" / "link.md")
    w("relatorios/segredo.md", "# s")
    w("relatorios/a.jpg", b"jpg")
    w("rascunhos/node_modules/a.md", "# n")
    w("rascunhos/a.json", "{}")
    w("rascunhos/" + "/".join("p" * 8) + "/a.md", "# fundo")
    w("rascunhos/.oculta/a.md", "# o")


def _h(*partes) -> str:
    s = hashlib.sha256()
    for p in partes:
        s.update(p if isinstance(p, bytes) else str(p).encode())
        s.update(b"\x00")
    return s.hexdigest()


class EquivalenciaTest(Http):
    def setUp(self):
        super().setUp()
        arvore(self)
        server.BIBLIOTECA = self.idx = self.novo_indice()
        # datas do zip (time.localtime) independentes do fuso da máquina
        self.p_tz = mock.patch("time.localtime", time.gmtime)
        self.p_tz.start()

    def tearDown(self):
        self.p_tz.stop()
        super().tearDown()

    def f_hash(self, path: str) -> str:
        code, h, body = self.req(path)
        return _h(code, *(h.get(k, "") for k in CAB_F), body)

    def caminhos_f(self, snap) -> list[str]:
        out = []
        for k in sorted(snap.ficheiros):
            f = snap.ficheiros[k]
            out += [self.f(k), self.f(k, f"?v={int(f.mtime)}"), self.f(k, f"?v={int(f.mtime)}&dl=1")]
        return out + [self.f(k) for k in NEGADOS] + ["/api/biblioteca/f/rascunhos/%2e%2e/fora/a.md"]

    def calcular(self) -> dict:
        snap = self.idx.get()
        code, h, body = self.req("/api/biblioteca")
        lista = _h(code, h["content-type"], re.sub(rb'^\{"gerado":\d+,', b"{", body))
        item, foco, zips = {}, {}, {}
        for iid in sorted(snap.por_id):
            code, _, body = self.req("/api/biblioteca/item?id=" + quote(iid, safe=""))
            d = json.loads(body)
            d.pop("arquivo", None)   # a única chave nova do /item (aditiva)
            item[iid] = _h(code, json.dumps(d, ensure_ascii=False).encode())
            if snap.por_id[iid]["publico"]["zip"]:
                code, h, body = self.req("/api/biblioteca/zip?id=" + quote(iid, safe=""))
                zips[iid] = _h(code, h.get("content-type"), h.get("content-disposition"), body)
        for k in sorted(snap.ficheiro_item):
            code, _, body = self.req("/api/biblioteca/item?id=" + quote(k, safe=""))
            d = json.loads(body)
            d.pop("arquivo", None)
            foco[k] = _h(code, json.dumps(d, ensure_ascii=False).encode())
        f = {p: self.f_hash(p) for p in self.caminhos_f(snap)}
        return {"versao": "0.1.4", "lista": lista, "item": item, "foco": foco, "zip": zips, "f": f}

    def test_igual_a_0_1_4(self):
        atual = self.calcular()
        if os.environ.get("BIBLIOTECA_GOLDEN") == "escrever":
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(atual, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        golden = json.loads(GOLDEN.read_text())
        self.assertGreaterEqual((len(golden["item"]), len(golden["zip"]), len(golden["f"])), (20, 5, 150))
        for rota in ("item", "foco", "zip", "f"):
            with self.subTest(rota=rota):
                self.assertEqual(sorted(atual[rota]), sorted(golden[rota]))
                self.assertEqual({k: v for k, v in atual[rota].items() if golden[rota][k] != v}, {})
        self.assertEqual(atual["lista"], golden["lista"])

    def test_f_sem_indice_igual_a_0_1_4(self):
        # resolver directo (sem índice; AC4): os mesmos bytes, os mesmos 404, sem varrer
        golden = json.loads(GOLDEN.read_text())["f"]
        caminhos = self.caminhos_f(self.idx.get())
        server.BIBLIOTECA = self.idx = self.novo_indice()
        difs = {p: v for p in caminhos if (v := self.f_hash(p)) != golden[p]}
        self.assertEqual(difs, {})
        self.assertEqual(self.idx.scans, 0)
