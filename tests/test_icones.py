"""Ícones SVG (porte do Office, ADR-023, CA-30): um sprite em linha no index.html, com currentColor, o mesmo viewBox e o
mesmo stroke; os botões do cabeçalho e o «Replay» usam <use href="#i-…"> em vez dos glifos Unicode; 20 px, alvos ≥ 44 px,
só o ícone abaixo de 520 px (com aria-label e title), peso do bloco e cada ícone usado (HTML, JS e CSS) existe no sprite.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
OCSS = (STATIC / "office.css").read_text(encoding="utf-8")
LCSS = (STATIC / "lateral.css").read_text(encoding="utf-8")
CCSS = (STATIC / "conversa.css").read_text(encoding="utf-8")
CJS = (STATIC / "conversa.js").read_text(encoding="utf-8")
BJS = (STATIC / "conversa_botao.js").read_text(encoding="utf-8")
BCSS = (STATIC / "conversa_botao.css").read_text(encoding="utf-8")
CHTML = (STATIC / "conversa.html").read_text(encoding="utf-8")
# a página da Conversa (origem própria, ficheiros do conversa.manifesto) tem o seu sprite; o Office tem o dele
MANIFESTO = (STATIC / "conversa.manifesto").read_text(encoding="utf-8").split()

NOMES = ["office", "biblioteca", "quadro", "definicoes", "conversa", "replay"]
NOMES_CONVERSA = ["mic"]
ATTRS = ('viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
         'stroke-linejoin="round"')
GLIFOS = "▣▤▦⚙↺"


def ico(nome):
    return f'<svg class="ico" aria-hidden="true" focusable="false"><use href="#i-{nome}"/></svg>'


def sprite():
    m = re.search(r"<svg hidden[^>]*>.*?</svg>\n", HTML, re.S)
    assert m, "sem sprite"
    return m.group(0)


class SpriteTest(unittest.TestCase):
    def test_ca30_um_sprite_com_os_simbolos_iguais(self):
        self.assertEqual(len(re.findall(r"<svg hidden", HTML)), 1)
        s = sprite()
        self.assertIn('style="display:none"', s.split(">", 1)[0])   # oculto mesmo onde o [hidden] não se aplica ao SVG
        simbolos = re.findall(r'<symbol id="i-([a-z]+)" ([^>]*)>', s)
        self.assertEqual([n for n, _ in simbolos], NOMES)
        for nome, attrs in simbolos:
            self.assertEqual(attrs, ATTRS, nome)
        corpo = re.sub(r"<symbol [^>]*>", "", s.split(">", 1)[1])
        self.assertNotRegex(corpo, r"#[0-9a-fA-F]{3,8}\b|rgb|fill=|stroke=|style=|class=")
        self.assertEqual(set(re.findall(r"<(\w+)", corpo)), {"path", "rect", "circle"})

    def test_ca30_peso_sem_pedidos_novos_e_cada_icone_usado_existe(self):
        self.assertLessEqual(len(sprite().encode()), 4096)
        self.assertNotRegex(HTML, r'href="[^"#]*\.svg"|src="[^"]*\.svg"')
        usados, usados_cv = set(), set()
        for f in sorted(STATIC.glob("*.*")):
            if f.suffix not in (".js", ".css", ".html"):
                continue
            src = f.read_text(encoding="utf-8")
            if f.suffix == ".css":
                self.assertNotRegex(src, r"url\([^)]*\.svg", f.name)
            (usados_cv if f.name in MANIFESTO else usados).update(re.findall(r'#i-([a-z]+)\b', src))
        self.assertTrue(usados)
        self.assertLessEqual(usados, set(NOMES))
        self.assertEqual(usados_cv, set(NOMES_CONVERSA))

    def test_sprite_da_pagina_da_conversa(self):
        # na origem da Conversa (CSP style-src 'self'): oculto por classe (conversa.css), nunca por style=
        m = re.search(r'<svg class="cv-sprite" hidden aria-hidden="true">\n(.*?)</svg>\n', CHTML, re.S)
        self.assertTrue(m)
        self.assertEqual(re.findall(r'<symbol id="i-([a-z]+)" ([^>]*)>', m.group(1)), [(n, ATTRS) for n in NOMES_CONVERSA])
        self.assertIn(".cv-sprite { display: none; }", CCSS)
        self.assertNotIn("style=", CHTML)

    def test_ca19_sem_o_nome_antigo(self):
        self.assertNotRegex(HTML.lower(), "jarvi" + "s")


class CabecalhoTest(unittest.TestCase):
    H = HTML[HTML.index("<header>"):HTML.index("</header>")]

    def test_ca30_botoes_com_icone_aria_label_e_title(self):
        h = self.H
        self.assertIn(f"<h1>{ico('office')}<span>Squad Office</span></h1>", h)
        for bid, nome, rot in (("bib-b", "biblioteca", "Biblioteca"), ("quad-b", "quadro", "Quadro")):
            self.assertIn(f'<button id="{bid}" type="button" aria-pressed="false" aria-label="{rot}" title="{rot}">'
                          f'{ico(nome)}<span class="lg">{rot}</span></button>', h)
        self.assertIn(f'<button id="def-b" type="button" aria-label="Definições" title="Definições" aria-haspopup="dialog">'
                      f'{ico("definicoes")}</button>', h)
        for g in GLIFOS:
            self.assertNotIn(g, h)

    def test_ca30_replay_na_barra_das_salas(self):
        linha = next(ln for ln in HTML.splitlines() if 'id="rp-b"' in ln)
        self.assertIn(f'aria-label="Rever o dia em acelerado" title="Replay">{ico("replay")}<span>Replay</span></button>', linha)
        self.assertNotIn("↺", linha)

    def test_ca35_css_20px_44px_e_so_icone_abaixo_de_520(self):
        self.assertRegex(OCSS, r"\n\.ico \{ width: 20px; height: 20px; vertical-align: middle; flex: none; \}")
        self.assertRegex(OCSS, r"#quad-b, #bib-b, #def-b \{[^}]*min-height: 44px; min-width: 44px;[^}]*"
                               r"display: inline-flex; align-items: center; justify-content: center; gap: 6px; \}")
        self.assertRegex(OCSS, r"@media \(max-width: 520px\) \{[^\n]*header \.lg \{ display: none; \}")
        self.assertRegex(LCSS, r"#rooms #rp-b \{ min-height: 44px; min-width: 44px;[^}]*display: inline-flex; align-items: center;")
        self.assertRegex(LCSS, r"@media \(max-width: 520px\) \{ #rooms #rp-b span \{ display: none; \}")
        self.assertNotRegex(OCSS, r"#def-b \{[^}]*font-size: 18px")

    def test_ca30_botao_da_conversa_e_microfone(self):
        # o botão da Conversa no cabeçalho do Office: ícone i-conversa, ≥ 44 px, só o ícone abaixo de 520 px (o .lg do
        # cabeçalho; aria-label e title dizem o nome); o microfone da página ≥ 44 px
        self.assertIn('<use href="#i-conversa"/></svg><span class="lg"></span>', BJS)
        self.assertIn('b.setAttribute("aria-label", r.aria); b.title = r.aria;', BJS)
        self.assertRegex(BCSS, r"#cv-b, #cv-x \{[^}]*min-height: 44px; min-width: 44px;[^}]*display: inline-flex; align-items: center;")
        self.assertIn('<use href="#i-mic"/>', CJS)
        self.assertNotIn("#i-fechar", CJS)
        self.assertRegex(CCSS, r"#cv-mic \{[^}]*min-width: 44px")


if __name__ == "__main__":
    unittest.main()
