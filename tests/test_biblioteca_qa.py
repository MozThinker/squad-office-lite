"""QA da Biblioteca: lacunas da matriz do PLAN §5 que os testes do backend e do frontend não cobriam juntos.

- symlinks e caminhos inválidos nas raízes internas (`tarefas`, `aprovacoes-*`), não só em `rascunhos`;
- ficheiros negados (S1/S2/S3) dentro de um lote nunca chegam ao JSON do item, ao índice, ao zip nem ao `/f/`;
- HEAD com Range e HEAD de caminhos inválidos;
- crit. 39: um estudo R&D servido pelo `/f/` e passado por `semFrontmatter()` + `md()` sai formatado e sem frontmatter;
- crit. 4: com o JSON real do servidor e as funções do `biblioteca.js`, o lote de 1/11 é o 1.º da fila «Media recente»
  e do chip Imagens, mesmo com centenas de tarefas mais recentes à frente.
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import quote

import server
from tests.test_biblioteca import BEARER, GH, H, LOTE, NAO, Http
from tests import cfg

OFFICE_CFG = cfg.LEGADO

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "static" / "index.html"
BIBJS = ROOT / "static" / "biblioteca.js"


def node(script: str, *args, stdin: str = "") -> object:
    with tempfile.TemporaryDirectory() as d:
        js = Path(d) / "t.mjs"
        js.write_text(script)
        r = subprocess.run(["node", str(js), *map(str, args)], input=stdin, capture_output=True, text=True,
                           timeout=30, check=True)
    return json.loads(r.stdout)


class RaizesInternasTest(Http):
    """S3/S4 em `tarefas` e `aprovacoes-*` (os testes de symlink existentes só usam `rascunhos`)."""

    def test_symlinks_em_tarefas_e_aprovacoes(self):
        fora = self.w("fora/segredo-de-fora.md", "# fora")
        self.w("tasks/20261101-1000-a/PLAN.md", "# A")
        os.symlink(fora, self.tasks / "20261101-1000-a" / "SPEC.md")
        (self.tasks / "inbox").mkdir()
        os.symlink(fora, self.tasks / "inbox" / "plano-link.md")
        os.symlink("/etc/hostname", self.aprov / "2026-11-02-link.md")
        s = self.novo_indice().get()
        self.assertIn("tarefas/20261101-1000-a/PLAN.md", s.ficheiros)
        for k in ("tarefas/20261101-1000-a/SPEC.md", "tarefas/inbox/plano-link.md", "aprovacoes-marketing/2026-11-02-link.md"):
            with self.subTest(k=k):
                self.assertNotIn(k, s.ficheiros)
                self.assertEqual(self.get_json(self.f(k)), (404, NAO))
        self.assertNotIn("tarefas/inbox/plano-link.md", s.por_id)

    def test_caminhos_invalidos_em_aprovacoes(self):
        self.w("aprov-mkt/meta.json", "{}")
        self.w("aprov-mkt/sub/2026-11-01-x.md", pedido_simples())
        self.w("aprov-mkt/.oculto.md", pedido_simples())
        ok = "/api/biblioteca/f/aprovacoes-marketing/2026-11-01-status.md"
        self.assertEqual(self.req(ok)[0], 200)
        for p in ("aprovacoes-marketing/meta.json", "aprovacoes-marketing/sub/2026-11-01-x.md",
                  "aprovacoes-marketing/.oculto.md", "aprovacoes-marketing/..%2f..%2fetc%2fpasswd",
                  "aprovacoes-marketing/../tasks/x.md", "aprovacoes-geral/2026-11-01-status.md",
                  "aprovacoes-/2026-11-01-status.md"):
            with self.subTest(p=p):
                self.assertEqual(self.get_json("/api/biblioteca/f/" + p), (404, NAO))


def pedido_simples() -> str:
    return "---\nestado: aprovado\ncriado: 2026-11-01\n---\n# x\n"


class NegadosDentroDoLoteTest(Http):
    """S1–S3 dentro de um lote: nada negado aparece no destaque, nos textos, em «Detalhes», no `q`, no zip ou no `/f/`."""

    NEGADOS = ("final/.oculto-01.jpg", "final/dados/pessoa.jpg", "final/segredo-01.jpg", "final/password.md",
               "final/x.svg", "final/x.html", "final/x.js", "final/node_modules/a.png", ".state/a.jpg",
               "prompts/.env", "prompts/chave.pem", "prompts/meta.json", "folha-credentials.md", "final/id_rsa.jpg")

    def test_negados_nao_saem_por_lado_nenhum(self):
        for rel in self.NEGADOS:
            self.w(f"{LOTE}/{rel}", b"NEGADO")
        os.symlink("/etc/hostname", self.root / LOTE / "final" / "status-00-link-9x16.jpg")
        os.symlink(self.root / LOTE / "final" / "status-01-quem-9x16.jpg", self.root / LOTE / "final" / "status-09-dup.jpg")
        idx = self.novo_indice()
        server.BIBLIOTECA = idx
        nomes = [Path(r).name for r in self.NEGADOS] + ["status-00-link-9x16.jpg", "status-09-dup.jpg"]
        code, d = self.get_json(f"/api/biblioteca/item?id={quote(LOTE, safe='')}")
        self.assertEqual(code, 200)
        corpo = json.dumps(d, ensure_ascii=False) + self.req("/api/biblioteca")[2].decode()
        for n in nomes:   # como nome de ficheiro (campo «nome»), segmento de URL ou palavra do «q»
            with self.subTest(n=n):
                for forma in (f'"{n}"', f"/{quote(n, safe='')}?", f" {n.lower()}"):
                    self.assertNotIn(forma, corpo)
        self.assertEqual([x["nome"] for x in d["destaque"]],
                         ["status-01-quem-9x16.jpg", "status-01-quem-v2-9x16.jpg", "status-05-preco-9x16.jpg",
                          "status-06-convite-9x16.mp4"])
        code, h, body = self.req(f"/api/biblioteca/zip?id={quote(LOTE, safe='')}")
        self.assertEqual(code, 200)
        zf = zipfile.ZipFile(io.BytesIO(body))
        self.assertIsNone(zf.testzip())
        self.assertFalse([n for n in zf.namelist() if Path(n).name in nomes], zf.namelist())
        self.assertFalse([n for n in zf.namelist() if zf.read(n) == b"NEGADO"])
        for rel in self.NEGADOS + ("final/status-00-link-9x16.jpg", "final/status-09-dup.jpg"):
            with self.subTest(rel=rel):
                self.assertEqual(self.get_json(self.f(f"{LOTE}/{rel}")), (404, NAO))
                self.assertEqual(self.get_json("/api/biblioteca/mini/" + quote(f"{LOTE}/{rel}")), (404, NAO))


class HeadTest(Http):
    def test_head_com_range_e_head_invalido(self):
        v = self.f(f"{LOTE}/final/status-06-convite-9x16.mp4")
        code, h, body = self.req(v, "HEAD", {"Range": "bytes=2-5"})
        self.assertEqual((code, h["content-range"], h["content-length"], body), (206, "bytes 2-5/10", "4", b""))
        code, h, body = self.req(v, "HEAD", {"Range": "bytes=10-"})
        self.assertEqual((code, h["content-range"], body), (416, "bytes */10", b""))
        for p in ("/api/biblioteca/f/rascunhos/../x", "/api/biblioteca/f/rascunhos/a.json", "/api/biblioteca/f/x/y.md"):
            with self.subTest(p=p):
                code, h, body = self.req(p, "HEAD")
                self.assertEqual((code, body), (404, b""))

    def test_head_de_texto_tem_o_tamanho_do_mascarado(self):
        k = "rascunhos/whatsapp-status/2026-11-01-textos.md"
        code, h, body = self.req(self.f(k))
        self.assertNotIn(GH.encode(), body)
        self.assertNotIn(BEARER.encode(), body)
        code2, h2, body2 = self.req(self.f(k), "HEAD")
        self.assertEqual((code2, h2["content-length"], body2), (200, str(len(body)), b""))


RENDER_JS = r"""
import fs from "node:fs"; import vm from "node:vm";
const [html, bibjs] = process.argv.slice(2);
const src = fs.readFileSync(html, "utf8");
const esc = src.match(/^const esc = .*$/m)[0];
const a = src.indexOf("function inline("), b = src.indexOf("\nconst rd = ", a);
const { md } = vm.runInNewContext(esc + "\n" + src.slice(a, b) + "\n;({ md })");
const { semFrontmatter, ficha } = await import(bibjs);
const s = semFrontmatter(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify({ front: s.front, ficha: ficha(s.front), html: md(s.corpo) }));
"""

ESTUDO = f"""---
produto: loja
autor: analista
data: 2026-11-01
---
# Estudo R&D: bilhete no Zap

> Resposta curta: sim, como piloto.

## 1. Opções

| Opção | Custo |
|---|---|
| A | 0 |
| B | 20 USD |

- primeira
- segunda

1. passo um
2. passo dois

token {GH}
"""


@unittest.skipUnless(shutil.which("node"), "sem node")
class EstudoRenderizadoTest(Http):
    """Crit. 39: o leitor recebe text/plain mascarado do /f/ e renderiza com semFrontmatter() + md()."""

    def test_estudo_rnd_formatado_sem_frontmatter(self):
        self.w("rnd-estudos/2026-11-01-bilhete-no-zap.md", ESTUDO, idade=H)
        idx = self.novo_indice()
        server.BIBLIOTECA = idx
        code, d = self.get_json("/api/biblioteca")
        it = next(i for i in d["itens"] if i["id"] == "rnd-estudos/2026-11-01-bilhete-no-zap.md")
        self.assertEqual((it["genero"], it["tipos"], it["produtos"], it["titulo"]),
                         ("documento", ["estudos"], ["loja"], "Estudo R&D: bilhete no Zap"))
        code, h, body = self.req("/" + it["principal"]["url"])
        self.assertEqual((code, h["content-type"]), (200, "text/plain; charset=utf-8"))
        self.assertIn("sandbox", h["content-security-policy"])
        out = node(RENDER_JS, HTML, BIBJS, stdin=body.decode())
        html = out["html"]
        self.assertEqual(out["front"], {"produto": "loja", "autor": "analista", "data": "2026-11-01"})
        self.assertTrue(out["ficha"])
        self.assertTrue(html.startswith("<h3>Estudo R&amp;D: bilhete no Zap</h3>"), html[:80])
        for tag in ("<h4>", "<table>", "<th", "<td>", "<ul>", "<ol>", "<blockquote>"):
            self.assertIn(tag, html)
        self.assertNotIn("produto: loja", html)
        self.assertNotIn("---", html)
        self.assertNotIn(GH, html)


MEDIA_RECENTE_JS = r"""
import fs from "node:fs";
const [bibjs, lote] = process.argv.slice(2);
const b = await import(bibjs);
const { itens } = JSON.parse(fs.readFileSync(0, "utf8"));
const pos = (l) => l.findIndex((x) => x.id === lote);
const id = b.parseRota(b.rotaHash({ vista: "item", id: lote })).id;
process.stdout.write(JSON.stringify({
  geral: pos(b.filtra(itens, b.FILTROS0)),
  recente: b.mediaRecente(itens).map((x) => x.id),
  imagens: pos(b.filtra(itens, { ...b.FILTROS0, tipo: "imagens" })),
  zfImagens: pos(b.filtra(itens, { ...b.FILTROS0, produto: "loja", tipo: "imagens" })),
  pesquisa: pos(b.filtra(itens, { ...b.FILTROS0, q: "MASCÔTE estado" })),
  id,
}));
"""


@unittest.skipUnless(shutil.which("node"), "sem node")
class TresToquesTest(Http):
    """Crit. 4 / goal: com 300 tarefas e 50 documentos mais recentes, o lote de 1/11 continua a 2 toques
    («Biblioteca» → 1.º cartão de «Media recente») e a 3 pelo chip Imagens ou Loja+Imagens."""

    def test_lote_de_1_11_primeiro_na_fila_e_no_chip_imagens(self):
        self.w(f"{LOTE}/folha.md", "# Folha: Criativos do mascote · Estado WhatsApp\n", idade=3 * H)
        self.w("rascunhos/media/2026-10-26-s45-zf-mz-mascote/final/a.jpg", b"jpg", idade=6 * 24 * H)
        self.w("rascunhos/media/2026-10-31-codigo-teste/v.mp4", b"mp4", idade=30 * H)
        for n in range(300):
            self.w(f"tasks/20261101-{n:04d}-x/SPEC.md", f"# Spec {n} mascote", idade=60 + n)
        for n in range(50):
            self.w(f"rascunhos/docs/2026-11-01-copy-{n}.md", "# Copy", idade=60 + n)
        idx = self.novo_indice()
        server.BIBLIOTECA = idx
        code, h, body = self.req("/api/biblioteca")
        self.assertEqual(code, 200)
        r = node(MEDIA_RECENTE_JS, BIBJS, LOTE, stdin=body.decode())
        self.assertGreater(r["geral"], 300)                 # na lista geral fica lá em baixo…
        self.assertEqual(r["recente"][0], LOTE)             # …mas é o 1.º cartão da fila (toque 2)
        self.assertEqual(r["recente"][1:], ["rascunhos/media/2026-10-31-codigo-teste",
                                            "rascunhos/media/2026-10-26-s45-zf-mz-mascote"])
        self.assertEqual((r["imagens"], r["zfImagens"], r["pesquisa"]), (0, 0, 0))
        self.assertEqual(r["id"], LOTE)
        code, d = self.get_json(f"/api/biblioteca/item?id={quote(r['id'], safe='')}")
        imgs = [f for f in d["destaque"] if f["tipo"] == "imagem"]
        self.assertTrue(imgs and all(f["mini"].startswith("api/biblioteca/mini/") for f in imgs))
        self.assertEqual(self.req("/" + imgs[0]["mini"])[0], 200)


if __name__ == "__main__":
    unittest.main()
