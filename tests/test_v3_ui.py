"""v3 (SPEC lite-v3): testes estáticos da interface — tecto do index.html, letra, alvos de toque, grelha D2, scroll do #info,
reduced-motion, cabeçalho numa linha e módulos a pedido. Só lêem ficheiros de static/ (sem servidor nem browser)."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
SRC = (STATIC / "index.html").read_text(encoding="utf-8")
OCSS = (STATIC / "office.css").read_text(encoding="utf-8")
LCSS = (STATIC / "lateral.css").read_text(encoding="utf-8")
ESTILO = "".join(re.findall(r"<style>(.*?)</style>", SRC, re.S))
TECTO = 95_106   # tamanho do index.html na 0.2.0 (CA-50, D-9)


def regras(css: str) -> list[tuple[str, str]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(s.strip(), d) for s, d in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def px(decl: str) -> list[float]:
    return [float(v) for v in re.findall(r"font(?:-size)?\s*:[^;]*?(\d+(?:\.\d+)?)px", decl)]


class TamanhoTest(unittest.TestCase):
    def test_ca50_index_nao_cresce(self):
        self.assertLessEqual(len((STATIC / "index.html").read_bytes()), TECTO)

    def test_ca50_css_dos_componentes_fora_e_tokens_dentro(self):
        # os tokens dos 5 temas ficam no <style> (1.º paint sem piscar); o resto vem do office.css e do lateral.css
        self.assertIn(":root {", ESTILO)
        self.assertNotIn("#fabrica", ESTILO)
        head = SRC[:SRC.index("</head>")]
        self.assertLess(head.index("</style>"), head.index('<link rel="stylesheet" href="office.css">'))
        self.assertLess(head.index('href="office.css"'), head.index('<link rel="stylesheet" href="lateral.css">'))
        self.assertIn("Squad Office", SRC)   # o fumo da CI procura-o no HTML


class LetraTest(unittest.TestCase):
    def test_ca3_fonte_local_com_licenca_e_swap(self):
        ff = re.search(r"@font-face \{([^}]*)\}", OCSS).group(1)
        self.assertIn("font-display: swap", ff)
        self.assertIn('url(fonts/pixelify-sans-600-latin.woff2) format("woff2")', ff)
        woff = STATIC / "fonts" / "pixelify-sans-600-latin.woff2"
        self.assertEqual(woff.read_bytes()[:4], b"wOF2")
        self.assertLessEqual(woff.stat().st_size, 40 * 1024)
        self.assertIn("SIL Open Font License, Version 1.1", (STATIC / "fonts" / "OFL.txt").read_text(encoding="utf-8"))

    def test_ca3_sans_no_texto_e_mono_nos_dados(self):
        self.assertRegex(OCSS, r"body \{[^}]*font: 15px/1\.5 var\(--sans\)")
        self.assertRegex(OCSS, r"\.mono, li \.t, #live, \.nums b, \.tk, code, pre[^{]*\{ font-family: var\(--mono\); \}")

    def test_ca4_nenhum_texto_abaixo_de_12px_fora_do_svg(self):
        css = {"index <style>": ESTILO, "office.css": OCSS, "lateral.css": LCSS}
        for f in ("definicoes.css", "quadro.css", "biblioteca.css", "replay.css"):
            if (STATIC / f).exists():
                css[f] = (STATIC / f).read_text(encoding="utf-8")
        for nome, c in css.items():
            for sel, d in regras(c):
                if re.search(r"#office|\bsvg\b", sel):
                    continue   # texto do pixel-art: atributos do SVG
                for v in px(d):
                    with self.subTest(f=nome, sel=sel):
                        self.assertGreaterEqual(v, 12)

    def test_ca4_alvos_de_44px_no_toque(self):
        m = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", OCSS, re.S).group(1)
        for s in ("#rooms", "#tabs", "#filt", ".btns", "#who", "#barra", "#avisos", ".pn", "details summary", "#q"):
            self.assertIn(s, m)
        self.assertIn("min-height: 44px; min-width: 44px", m)


    def test_ca4_cores_so_por_tokens(self):
        # contraste (texto 4,5:1, bordas 3:1) verificado nos tokens dos 5 temas (test_temas_ui.py); por isso o CSS novo não traz
        # cores fixas fora do desenho do escritório (#office/svg), que é igual em todos os temas, e das sombras
        for f in ("office.css", "lateral.css", "replay.css"):
            for sel, d in regras((STATIC / f).read_text(encoding="utf-8")):
                if re.search(r"#office|\bsvg\b", sel):
                    continue
                for decl in d.split(";"):
                    if "#" in decl and not decl.strip().startswith("box-shadow"):
                        self.fail(f"{f}: {sel} {{{decl.strip()}}}")


class GrelhaTest(unittest.TestCase):
    def test_ca6_d2_tres_quintos_e_uma_coluna_ate_900(self):
        self.assertIn("main { display: grid; grid-template-columns: minmax(0, 3fr) minmax(340px, 2fr);", OCSS)
        self.assertIn("@media (max-width: 900px) { main { grid-template-columns: 1fr; } #info { height: auto; max-height: 70dvh; } }", OCSS)

    def test_ca23_um_so_scroll_no_info(self):
        self.assertRegex(OCSS, r"aside \{[^}]*height: 0; min-height: 100%; overflow-y: auto;")
        self.assertIn("#info ol { max-height: none; }", OCSS)
        deliv = re.search(r"\.deliv \.md \{([^}]*)\}", OCSS).group(1)
        self.assertNotIn("overflow", deliv)
        # o poll não mexe no scrollTop; mudar de tarefa ou de sala volta ao topo
        self.assertIn("const v = roomKey + t?.id, y = box.dataset.v === v ? box.scrollTop : 0;", SRC)
        self.assertIn("box.dataset.v = v; box.scrollTop = y;", SRC)


class MovimentoTest(unittest.TestCase):
    def test_ca5_reduced_motion_e_z_parado(self):
        self.assertIn("@media (prefers-reduced-motion: reduce) { svg *, .dot.run { animation: none !important; } }", OCSS)
        self.assertIn("@media (prefers-reduced-motion: reduce) { #fly * { transition: none !important; }", OCSS)
        self.assertNotIn(".zz", OCSS)
        self.assertNotIn('class="zz"', SRC)
        i = SRC.index("function confetti()")
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)").matches', SRC[i:SRC.index("\n}\n", i)])


class CabecalhoTest(unittest.TestCase):
    def test_ca1_uma_linha_so_com_marca_biblioteca_quadro_definicoes_e_ao_vivo(self):
        h = SRC[SRC.index("<header>"):SRC.index("</header>")]
        ids = re.findall(r'id="([\w-]+)"', h)
        self.assertEqual(ids, ["bib-b", "quad-b", "def-b", "live"])
        self.assertRegex(OCSS, r"header \{ display: flex; align-items: center;")
        self.assertNotRegex(re.search(r"header \{([^}]*)\}", OCSS).group(1), r"flex-wrap: wrap")

    def test_ca2_volume_sem_musica_nao_carrega_o_music_js(self):
        self.assertIn("setVolume: (v) => { vol = v; eng?.setVolume(v); if (musicOn) musicPrefs(); } };", SRC)
        d = (STATIC / "definicoes.js").read_text(encoding="utf-8")
        self.assertIn('$("#def-vr").hidden = !m.on();', d)
        self.assertNotIn("music.js", d)


if __name__ == "__main__":
    unittest.main()
