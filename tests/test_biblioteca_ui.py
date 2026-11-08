"""Testes estáticos da Biblioteca no frontend: entrada no header, guarda do hash, citações/itálico no md(),
escape de tudo o que vem do servidor em static/biblioteca.js, atalho do manifest e as funções puras em node.

A análise das interpolações reutiliza o scanner de test_aprovacoes_ui: cada «${…}» de um template com HTML tem de passar
por esc()/escU()/md(), por um helper «…H()» (que devolve HTML já escapado) ou ser um valor que não vem do servidor (lista
fechada, abaixo, com a razão).
"""
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests.test_aprovacoes_ui import TERNARIO_LITERAL, templates

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "static" / "index.html"
JS = ROOT / "static" / "biblioteca.js"
CSS = ROOT / "static" / "biblioteca.css"
SRC, BIB = HTML.read_text(encoding="utf-8"), JS.read_text(encoding="utf-8")

ESCAPA = re.compile(r"\b(?:esc|escU|md)\(|\b\w+H\(|\.map\(\w+H\)")
SEGURAS = {
    "href": "rotaHash() já passado por esc() na mesma função",
    "lab": "atributo aria-label montado com esc() em pecaH",
    "dlH": "HTML de botões montado com esc()/escU()",
    "priH": "idem",
    "thH": "idem",
    "minisH": "idem",
    "metaH": "idem",
    "linhaH": "idem",
    "corpoH": "parâmetro de secH, sempre HTML montado com esc()",
    "secsH": "idem",
    "limpaF": "constante HTML sem dados",
    '"<i></i>".repeat(8)': "constante",
}
TERNARIO_HTML = re.compile(r"^[\w.?]+(?: [<>=]+ \d+)?\s*\?\s*(?:\w+H|\"[^\"]*\"|`[^`$]*`|limpaF)\s*:\s*(?:\w+H|\"[^\"]*\")$")   # cond ? xH|literal : ""


class EntradaTest(unittest.TestCase):
    def test_botao_no_header_antes_das_notificacoes(self):
        h = SRC[SRC.index("<header>"):SRC.index("</header>")]
        self.assertIn('<button id="bib-b" type="button" aria-pressed="false">▤ Biblioteca</button>', h)
        self.assertLess(h.index('id="bib-b"'), h.index('id="bell"'))
        self.assertLess(h.index("<h1>"), h.index('id="bib-b"'))
        self.assertRegex(SRC, r"#bib-b \{[^}]*min-height:\s*44px")
        # em ecrã estreito o resto do header desce para a 2.ª linha (o botão fica ao lado do título)
        self.assertRegex(SRC, r"header > :not\(h1, #bib-b\) \{ order: 2; \}")

    def test_seccao_escondida_e_escritorio_escondido_na_biblioteca(self):
        i = SRC.index('<section id="bib"')
        self.assertRegex(SRC[i:SRC.index(">", i)], r'\bhidden\b.*|aria-labelledby="bib-h"')
        self.assertLess(i, SRC.index('<section id="aprovacoes"'))   # Aprovações por baixo (fim da página)
        self.assertLess(i, SRC.index('<nav id="rooms"'))
        self.assertIn('body[data-view="biblioteca"] :is(#rooms, #filt, #tabs, #totals, main, #fabrica) { display: none; }', SRC)

    def test_modulo_e_css_so_a_pedido(self):
        self.assertIn('import("./biblioteca.js")', SRC)
        self.assertNotRegex(SRC, r"<script[^>]+biblioteca|<link[^>]+biblioteca\.css")
        self.assertIn('"./biblioteca.css"', BIB)   # o próprio módulo injecta o CSS

    def test_manifest_tem_atalho(self):
        m = json.loads((ROOT / "static" / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn({"name": "Biblioteca", "url": "./#biblioteca"}, m["shortcuts"])


@unittest.skipUnless(shutil.which("node"), "sem node")
class HashNodeTest(unittest.TestCase):
    def test_hash_biblioteca_nunca_e_tarefa(self):
        m = re.search(r"selected = (location\.hash[^,]+\|\| null),", SRC)
        self.assertIsNotNone(m)
        js = ("const f = (hash) => { const location = { hash }; return " + m.group(1) + "; };\n"
              "console.log(JSON.stringify(['#biblioteca', '#biblioteca/rnd-estudos/x.md', '#biblioteca?q=a',"
              " '#20261101-2037-office-biblioteca', '#20261101%2D1', ''].map(f)));")
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30, check=True)
        self.assertEqual(json.loads(r.stdout), [None, None, None, "20261101-2037-office-biblioteca", "20261101-1", None])


MD_JS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const esc = src.match(/^const esc = .*$/m)[0];
const a = src.indexOf("function inline("), b = src.indexOf("\nconst rd = ", a);
const fns = vm.runInNewContext(esc + "\n" + src.slice(a, b) + "\n;({ md })");
process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0, "utf8")).map((s) => fns.md(s))));
"""


@unittest.skipUnless(shutil.which("node"), "sem node")
class MdCitacoesTest(unittest.TestCase):
    def md(self, *inputs):
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "md.js"
            js.write_text(MD_JS)
            r = subprocess.run(["node", str(js), str(HTML)], input=json.dumps(inputs), capture_output=True, text=True,
                               timeout=30, check=True)
        return json.loads(r.stdout)

    def test_citacoes_juntam_linhas_seguidas(self):
        [out] = self.md("antes\n> Apresento a **personagem**\n> segunda linha\n>\n> terceira\ndepois")
        self.assertEqual(out, "<p>antes</p><blockquote>Apresento a <strong>personagem</strong><br>segunda linha<br><br>terceira"
                              "</blockquote><p>depois</p>")

    def test_citacao_entre_lista_e_titulo(self):
        [out] = self.md("- a\n> b\n# T\n> c\n\n> d")
        self.assertEqual(out, "<ul><li>a</li></ul><blockquote>b</blockquote><h3>T</h3><blockquote>c</blockquote><blockquote>d</blockquote>")

    def test_italico(self):
        [a, b, c, d] = self.md("um *dois* e **três**", "5 * 3 * 2", "*a*", "`*x*` e https://ex.com/a*b e a*b*c")
        self.assertEqual(a, "<p>um <em>dois</em> e <strong>três</strong></p>")
        self.assertEqual(b, "<p>5 * 3 * 2</p>")
        self.assertEqual(c, "<p><em>a</em></p>")
        self.assertNotIn("<em>", d)

    def test_xss_dentro_de_citacao_e_italico(self):
        [out] = self.md('> <img src=x onerror=alert(1)>\n> *<script>alert(2)</script>*\n\n*"><svg onload=alert(3)>*')
        self.assertNotRegex(out, r"<(?:img|script|svg)\b")
        self.assertEqual(out.count("&lt;"), 4)
        self.assertNotRegex(out, r"<(?!/?(?:p|br|blockquote|em)>)")   # só estas tags


class BibliotecaJsTest(unittest.TestCase):
    def test_interpolacoes_html_escapadas(self):
        tpls = templates(BIB)
        html = [e.strip() for lit, ex in tpls if any("<" in p for p in lit) for e in ex]
        self.assertGreater(len(html), 60)   # o scanner encontrou de facto os templates
        for e in html:
            with self.subTest(expr=e):
                self.assertTrue(ESCAPA.search(e) or e in SEGURAS or TERNARIO_LITERAL.match(e) or TERNARIO_HTML.match(e),
                                f"interpolação sem esc() num template HTML: ${{{e}}}")

    def test_innerhtml_so_por_caminhos_conhecidos(self):
        for rhs in re.findall(r"\.innerHTML\s*=\s*([^;]+);", BIB):
            with self.subTest(rhs=rhs[:60]):
                self.assertRegex(rhs.strip(), r'^(?:SHELL|SKEL|""|`|\w+H\(|barraH\(\)|corpo\.trim\(\) \? A\.md\(|fi\.length \?|vis\.length \? ""|vis\.slice\(0, S\.limite\)\.map\(cartaoH\))')
        self.assertNotRegex(BIB, r"outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function")

    def test_urls_do_servidor_so_por_safeU_nos_sinks_do_dom(self):
        # .href/.src/.poster recebem sempre safeU(…) (só «api/biblioteca/…»); nunca um URL cru do servidor
        for alvo, rhs in re.findall(r"\.(href|src|poster)\s*=\s*([^;]+);", BIB):
            with self.subTest(rhs=rhs):
                self.assertTrue(rhs.strip().startswith("safeU("), f".{alvo} = {rhs}")
        for rhs in re.findall(r"\bhref: ([^,}]+)", BIB):
            self.assertRegex(rhs.strip(), r"^(?:safeU\(|new URL\(\"\./biblioteca\.css\")")

    def test_media_leve_e_acessivel(self):
        imgs = re.findall(r"<img [^>]*>", BIB)
        self.assertGreaterEqual(len(imgs), 3)
        for tag in imgs:
            with self.subTest(tag=tag):
                for a in ('loading="lazy"', 'decoding="async"', "width=", "height=", "alt="):
                    self.assertIn(a, tag)
        self.assertIn('preload: "metadata"', BIB)
        self.assertIn('setAttribute("playsinline", "")', BIB)
        self.assertIn("controls: true", BIB)
        self.assertNotIn("autoplay", BIB)
        # miniatura partida (404 acima de 40 MP) vira tile: listener delegado em captura, nunca onerror inline
        self.assertIn('A.root.addEventListener("error", imgFalhou, true);', BIB)
        self.assertIn('"▣ Imagem"', BIB)
        self.assertNotRegex(BIB, r"<img [^>]*\bon\w+=")  # nada de handlers inline nos templates

    def test_zip_verifica_sem_ocupar_vaga(self):
        # verificação com «&verificar=1» (204 sem vaga); nada de fetch do zip a sério abortado a meio
        self.assertIn('fetch(zipVerificaU(zip.getAttribute("href"))', BIB)
        self.assertNotIn("AbortController", BIB)
        self.assertIn("if (zipRecente(zip.dataset.ok, Date.now())) return;", BIB)

    def test_microcopy_e_acessibilidade(self):
        for s in ("▤ Biblioteca", "‹ Escritório", "‹ Biblioteca", "↻ Actualizar", "Mostrar mais (", "Limpar filtros", "Limpar pesquisa",
                  "pesquisar na biblioteca", "Pesquisar na biblioteca por título, nome do ficheiro ou produto", "Abrir lote",
                  "⤓ Descarregar tudo (zip)", "A preparar…", "Peças finais (", "Textos e legendas (", "Revisão e parecer jurídico (",
                  "Folha do lote", "Ficheiros de trabalho (", "✕ Fechar", "‹ Anterior", "Seguinte ›", "Copiar texto", "✓ Copiado",
                  "Texto copiado.", "Não foi possível copiar. Selecciona o texto e copia à mão.", "Ainda não há nada na biblioteca.",
                  "Nada com estes filtros.", "Não foi possível carregar a biblioteca.", "Sem ligação. Mostramos a última lista que carregou",
                  "Este lote só tem ficheiros de trabalho.", "Abrir PDF ›", "Ver pedido ›", "Ver em Aprovações", "Novo", "↑ Topo"):
            with self.subTest(s=s):
                self.assertIn(s, BIB + SRC)
        self.assertIn('role="group" aria-label="Produto"', BIB)
        self.assertIn('class="bib-cnt" aria-live="polite"', BIB)
        self.assertIn('<dialog class="bib-v" aria-labelledby="bib-vh">', BIB)
        # fila «Media recente»: lista com role e cabeçalho; só no início sem filtros nem pesquisa
        self.assertIn('<section class="bib-rec" aria-labelledby="bib-rech" hidden><h3 id="bib-rech">Media recente</h3><ul class="bib-recl" role="list">', BIB)
        self.assertIn('S.pintado === "#biblioteca" ? mediaRecente(S.itens) : []', BIB)
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.bib-btn, \.bib-chips button, \.bib-dl \{[^}]*min-height: 44px[^}]*min-width: 44px")
        self.assertIn("prefers-reduced-motion", css)
        self.assertRegex(css, r"\.bib-recl \{[^}]*overflow-x: auto; scroll-snap-type: x mandatory")
        self.assertRegex(css, r"\.bib-rc \{[^}]*min-height: 44px")
        self.assertNotIn("prefers-color-scheme", css)   # o tema é global (<html data-theme>), não só da Biblioteca
        self.assertNotRegex(css, r"outline:\s*none(?![^}]*\}\s*/\*)")  # o único outline:none tem substituto (cartão)

    def test_peso(self):
        self.assertLess(len(BIB.encode()), 48_000)
        self.assertLess(len(CSS.read_bytes()), 16_000)


@unittest.skipUnless(shutil.which("node"), "sem node")
class BibliotecaNodeTest(unittest.TestCase):
    def test_funcoes_puras_em_node(self):
        r = subprocess.run(["node", "--test", "tests/biblioteca_test.mjs"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class ServeTest(unittest.TestCase):
    def test_modulo_e_css_com_mime_certo(self):
        import server
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            for nome, tipo in (("biblioteca.js", "javascript"), ("biblioteca.css", "text/css")):
                with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/{nome}") as r:
                    self.assertEqual(r.status, 200)
                    self.assertIn(tipo, r.headers["Content-Type"])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
