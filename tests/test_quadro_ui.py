"""Testes estáticos do Quadro no frontend: entrada no header, guarda do hash, carregamento lazy, ligações à Biblioteca/
Aprovações/salas, escape de tudo o que vem do servidor em static/quadro.js, md() com XSS (CA-23), CSS (temas, paleta
por projecto igual à do quadro.py, reduced-motion) e peso. As funções puras correm em node (tests/quadro_test.mjs).

O scanner de interpolações é o de test_aprovacoes_ui: cada «${…}» de um template com HTML passa por esc()/md(), por um
helper «…H()» (devolve HTML já escapado) ou é um valor que não vem do servidor (lista fechada abaixo, com a razão).
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_aprovacoes_ui import TERNARIO_LITERAL, templates
from tests.test_biblioteca_ui import MD_JS, TERNARIO_HTML

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "static" / "index.html"
JS = ROOT / "static" / "quadro.js"
CSS = ROOT / "static" / "quadro.css"
SRC, Q, QCSS = HTML.read_text(encoding="utf-8"), JS.read_text(encoding="utf-8"), CSS.read_text(encoding="utf-8")

ESCAPA = re.compile(r"\b(?:esc|md)\(|\b\w+H\(|\.map\(\w+H\)")
SEGURAS = {
    "SKEL": "constante HTML sem dados (esqueleto)",
    "LOCK": "constante SVG sem dados (cadeado)",
    "addH": "botão montado com esc() em colunaH",
    "maisH": "idem",
    'ls.join("")': "linhas <dt>/<dd> de linhaH(), com esc()",
    'manual(c) ? "" : `<span class="q-nota">${LOCK} Muda sozinho com o estado real</span>`': "constantes",
    "ctlH": "controlo do formulário montado com atH()/optH() (esc())",
    "extraH": "atributos montados com esc() em abrirMenu",
    "txtH": "texto do item montado com esc() em abrirMenu",
}


class EntradaTest(unittest.TestCase):
    def test_botao_no_header_a_seguir_a_biblioteca(self):
        h = SRC[SRC.index("<header>"):SRC.index("</header>")]
        self.assertIn('<button id="quad-b" type="button" aria-pressed="false">▦ Quadro</button>', h)
        self.assertLess(h.index('id="bib-b"'), h.index('id="quad-b"'))
        self.assertLess(h.index('id="quad-b"'), h.index('id="bell"'))
        self.assertRegex(SRC, r"#quad-b, #bib-b \{[^}]*min-height:\s*44px")   # mesmo estilo e alvo que a Biblioteca
        self.assertIn('$("#quad-b").setAttribute("aria-pressed", on);', SRC)

    def test_seccao_esconde_o_escritorio_e_as_aprovacoes(self):
        self.assertIn('<section id="quadro" aria-labelledby="q-h" hidden></section>', SRC)
        self.assertIn('body[data-view="quadro"] :is(#aprovacoes, #rooms, #filt, #tabs, #totals, main, #fabrica) { display: none; }', SRC)
        self.assertIn('id="q-h"', Q)

    def test_modulo_e_css_so_a_pedido(self):
        self.assertIn('import("./quadro.js")', SRC)
        self.assertNotRegex(SRC, r"<script[^>]+quadro|<link[^>]+quadro\.css")
        self.assertIn('new URL("./quadro.css", import.meta.url)', Q)
        for f in ("abrir", "rota", "sair"):
            self.assertRegex(Q, rf"export (?:async )?function {f}\(")

    def test_ligacoes_aprovacao_tarefa_e_saida(self):
        # verAprovacao é uma função nomeada partilhada pela Biblioteca e pelo Quadro (que se fecha antes, CA-4)
        self.assertIn("function verAprovacao(fonte, ficheiro) {", SRC)
        self.assertIn("fechar: bibFechar, verAprovacao });", SRC)
        self.assertIn("verAprovacao: (f, fi) => { qFechar(); verAprovacao(f, fi); }", SRC)
        self.assertIn("abrirTarefa: qTarefa });", SRC)
        self.assertIn("if (!t || !shown(t)) { period = p0; $(\"#q\").value = q0; return false; }", SRC)
        self.assertIn('A.abrirTarefa(t) === false) aviso("Esta execução já não está no escritório.")', Q)
        i = SRC.index("function selectTask(id) {")
        self.assertIn("if (isQuadro()) qFechar();", SRC[i:i + 120])
        # o hashchange do Quadro regista-se depois do da Biblioteca e só limpa o data-view se ainda for dele
        self.assertLess(SRC.index('addEventListener("hashchange", () => { if (isBib()'), SRC.index('addEventListener("hashchange", qRoute);'))
        self.assertIn('if (document.body.dataset.view === "quadro") document.body.dataset.view = "";', SRC)
        self.assertIn("A.verAprovacao(l.fonte, l.ficheiro)", Q)
        self.assertIn("A.abrirTarefa(t)", Q)

    def test_index_cresce_pouco(self):
        # CA-27 do Quadro: ≤ 3 KB sobre 101 021 B. Tecto subido para 111 000 B (tech lead, 5/11/2026: Aprovações no fim e
        # recolhíveis, 4 paletas de tema, script do <head> e selector); é guarda de regressão, não requisito de produto
        self.assertLessEqual(len(HTML.read_bytes()), 111_000)


@unittest.skipUnless(shutil.which("node"), "sem node")
class HashNodeTest(unittest.TestCase):
    def test_hash_do_quadro_nunca_e_tarefa(self):
        m = re.search(r"selected = (location\.hash[^,]+\|\| null),", SRC)
        self.assertIsNotNone(m)
        js = ("const f = (hash) => { const location = { hash }; return " + m.group(1) + "; };\n"
              "console.log(JSON.stringify(['#quadro', '#quadro?x', '#biblioteca', '#20261103-2359-office-quadro-kanban', ''].map(f)));")
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30, check=True)
        self.assertEqual(json.loads(r.stdout), [None, None, None, "20261103-2359-office-quadro-kanban", None])

    def test_testes_node_das_funcoes_puras(self):
        r = subprocess.run(["node", "--test", str(ROOT / "tests" / "quadro_test.mjs"), str(ROOT / "tests" / "quadro_lancar_test.mjs")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])


@unittest.skipUnless(shutil.which("node"), "sem node")
class MdXssTest(unittest.TestCase):
    """CA-23: as descrições e excertos passam pelo md() da página (escape total antes de formatar)."""

    def md(self, *inputs):
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "md.js"
            js.write_text(MD_JS)
            r = subprocess.run(["node", str(js), str(HTML)], input=json.dumps(inputs), capture_output=True, text=True, timeout=30, check=True)
        return json.loads(r.stdout)

    def test_img_onerror_javascript_e_fecho_de_atributo(self):
        a, b, c, d = self.md('<img src=x onerror=alert(1)>', "[x](javascript:alert(1)) e javascript:alert(2)",
                             '"><script>alert(3)</script>', "- **<b onmouseover=alert(4)>** https://ex.com/a\"onmouseover=x")
        for out in (a, b, c, d):
            with self.subTest(out=out):
                self.assertNotRegex(out, r"<(?:img|script|b|svg)\b")
                self.assertNotRegex(out, r'href="(?!https://)')
                self.assertNotRegex(out, r"<[^>]+\son\w+=")
        self.assertIn("&lt;img", a)
        self.assertIn("&quot;&gt;&lt;script&gt;", c)


class QuadroJsTest(unittest.TestCase):
    def test_interpolacoes_html_escapadas(self):
        html = [e.strip() for lit, ex in templates(Q) if any("<" in p for p in lit) for e in ex]
        self.assertGreater(len(html), 80)   # o scanner encontrou de facto os templates
        for e in html:
            with self.subTest(expr=e):
                self.assertTrue(ESCAPA.search(e) or e in SEGURAS or TERNARIO_LITERAL.match(e) or TERNARIO_HTML.match(e),
                                f"interpolação sem esc() num template HTML: ${{{e}}}")

    def test_innerhtml_so_por_caminhos_conhecidos(self):
        for rhs in re.findall(r"\.innerHTML\s*=\s*([^;]+);", Q):
            with self.subTest(rhs=rhs[:60]):
                self.assertRegex(rhs.strip(), r'^(?:SHELL|""|`|h$|\(completo \? itemH\(|d\.colunas\.map\(\(c\) => colunaH\(|Object\.keys\(COL\)\.map\(\(\) => `)')
        self.assertNotRegex(Q, r"outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|\.href\s*=")
        # o único href vem de safePr() (PR do GitHub) e passa por esc()
        self.assertEqual(re.findall(r'href="\$\{([^}]+)\}"', Q), ["esc(pr)"])
        self.assertIn("const pr = safePr(c.links?.pr);", Q)

    def test_sem_dialogos_nativos(self):
        self.assertEqual(re.findall(r"\b(?:confirm|alert|prompt)\s*\(", Q), [])   # CA-12: confirmação na página
        self.assertIn("Não dá para desfazer.", Q)

    def test_post_com_chaves_exactas(self):
        # o servidor recusa chaves a mais/a menos: o corpo é sempre op + campos + token + versao
        self.assertIn('body: JSON.stringify({ ...corpo, token: S.d.token, versao: S.d.versao })', Q)
        self.assertIn('{ op: "criar", ...v, coluna: f.coluna.value }', Q)
        self.assertIn('post({ op: "apagar", id })', Q)
        self.assertIn('const corpo = { op: "mover", id, coluna, posicao };', Q)
        self.assertIn("signal: ac.signal", Q)   # timeout de 15 s; a fila continua
        # o texto vai limpo (B4) e o editar leva só o que mudou (CR8); a fila serializa os POST (CR7) e cada um descarta GETs em voo (CR6)
        self.assertIn("titulo: limpaTexto(f.titulo.value).trim(), descricao: limpaTexto(f.descricao.value, true)", Q)
        self.assertIn('{ op: "editar", id, ...mud }', Q)
        self.assertIn("const p = fila.then(() => postAgora(corpo))", Q)
        self.assertRegex(Q, r"async function postAgora\(corpo\) \{\n[^\n]*\n  S\.seq\+\+;")
        self.assertIn('fetch("api/quadro", { cache: "no-store" })', Q)

    def test_conflito_e_poll(self):
        self.assertIn("O quadro mudou noutro sítio — recarreguei; repete a acção.", Q)   # CA-15
        self.assertIn("r.status === 409 || r.status === 403", Q)
        # poll de 15 s só visível e sem arrasto/edição (CA-8)
        self.assertRegex(Q, r"setInterval\(\(\) => !document\.hidden && !D && !S\.k && [^\n]+, 15000\)")

    def test_acessibilidade(self):
        for s in ('aria-live="polite"', 'role="tablist"', 'role="tab"', 'aria-selected=', 'aria-pressed=', 'role="menu"', 'role="menuitem"',
                  'aria-roledescription="cartão"', 'aria-labelledby="q-ch-', 'role="dialog"', 'aria-modal', 'aria-label="Muda sozinho com o estado real"',
                  'role="alert"', 'aria-keyshortcuts="/"', 'aria-label="Novo cartão"', 'aria-describedby="q-e-'):
            with self.subTest(s=s):
                self.assertIn(s, Q)
        for s in ("Setas para mover, Espaço para largar, Esc para cancelar.", "Movido para ", "Cancelado.", "Guardado.", "mudaram de coluna."):
            self.assertIn(s, Q)
        # toque: pressão longa sem mexer antes de agarrar; o dedo não faz scroll só depois de agarrado
        self.assertIn("setTimeout(agarrar, 350)", Q)
        self.assertIn('document.addEventListener("touchmove", (e) => D?.on && e.preventDefault(), { passive: false });', Q)

    def test_microcopy(self):
        for s in ("Sem ideias guardadas.", "Nada à tua espera. Bom sinal.", "Ver mais antigos (", "É tudo.", "Nada aqui com estes filtros.",
                  "Sem ligação ao Office. A mostrar o último estado.", "Tentar de novo", "Abrir PR ↗", "Abrir aprovação", "Abrir no escritório",
                  "Mover para… ▾", "Topo da coluna", "Fim da coluna", "Novo cartão", "Editar cartão", "markdown simples", "+ Cartão", "‹ <span>Escritório"):
            with self.subTest(s=s):
                self.assertIn(s, Q)


class QuadroCssTest(unittest.TestCase):
    def test_paleta_de_todos_os_projectos_nos_dois_temas(self):
        # paleta genérica c0…c7 (GET /api/config dá «cor» = c{índice % 8}); nenhuma cor por projecto da fábrica
        claro = QCSS[QCSS.index(':is([data-theme="claro"], [data-theme="sepia"]) :where([data-p])'):]   # claro e sépia
        self.assertEqual(sorted(set(re.findall(r'\[data-p="([^"]+)"\]', QCSS))), [f"c{i}" for i in range(8)])
        for chave in (f"c{i}" for i in range(8)):
            with self.subTest(chave=chave):
                self.assertRegex(QCSS, rf'\[data-p="{chave}"\] \{{ --pc: #[0-9a-f]{{6}}; --pc-bg: #[0-9a-f]{{6}}; --pc-tx: #[0-9a-f]{{6}}; \}}')
                self.assertIn(f'[data-p="{chave}"]', claro)
        self.assertIn(":where([data-p]) { --pc:", QCSS)   # «outro» e chaves desconhecidas caem no neutro

    def test_temas_movimento_e_foco(self):
        self.assertIn('body[data-view="quadro"] {', QCSS)
        self.assertNotIn("prefers-color-scheme", QCSS)   # o tema vem de <html data-theme> (script do <head>)
        self.assertIn('[data-theme="contraste"] body[data-view="quadro"]', QCSS)
        self.assertIn("@media (prefers-reduced-motion: no-preference)", QCSS)
        self.assertRegex(QCSS, r"@media \(prefers-reduced-motion: reduce\) \{[^\n]*transition: none !important; animation: none !important;")
        self.assertIn("#quadro :focus-visible { outline: 2px solid var(--q-focus); outline-offset: 2px; }", QCSS)
        self.assertIn("scroll-snap-type: x mandatory", QCSS)
        self.assertNotRegex(QCSS, r"@import|url\(|@font-face")   # sem letra web nem imagens
        # nunca animar width/height/top/left
        for bloco in re.findall(r"transition:[^;]+;", QCSS):
            self.assertNotRegex(bloco, r"\b(?:width|height|top|left)\b", bloco)

    def test_alvos_de_toque_a_360(self):
        movel = QCSS[QCSS.index("@media (max-width: 760px)"):]
        for sel in (".q-back {", ".q-s input {", ".q-tab {", ".q-more, .q-x {", ".q-add {"):
            with self.subTest(sel=sel):
                self.assertRegex(movel, re.escape(sel) + r"[^}]*(?:min-height: 44px|height: (?:4[4-9]|5\d)px)")
        self.assertRegex(movel, r"\.q-fab \{[^}]*width: 56px; height: 56px")
        self.assertIn(".q-chip::before { content: \"\"; position: absolute; inset: -4px 0; }", movel)   # 36 + 2×4 = 44


class PesoTest(unittest.TestCase):
    def test_peso(self):
        # CA-27 pedia ≤ 50 KB juntos sem minificar; o tech lead aceitou ~70 KB (~22 KB gzip): estes tectos impedem que cresça mais
        # 49 500 → 63 500: diálogo «Lançar» (ADR-005), só carrega com o Quadro; o index.html não cresce (tech lead, 6/11/2026)
        # 63 500 → 66 500: 0.1.3 (F4: Tipo/Tamanho/Avançado, «Lançar…» no menu, lançamento parado, dica de arrastar, dica de erro)
        self.assertLess(len(JS.read_bytes()), 66_500)
        self.assertLess(len(CSS.read_bytes()), 27_800)   # era 25 000; +2 KB temas (5/11/2026); +0,8 KB diálogo «Lançar» (6/11/2026)
        self.assertNotRegex(Q, r"^import |\brequire\(")   # sem dependências


if __name__ == "__main__":
    unittest.main()
