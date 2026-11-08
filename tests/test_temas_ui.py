"""Testes do selector de tema (SPEC CA14–CA25): HTML/CSS estáticos, script do <head> em node e contraste dos tokens.

O contraste (CA19–CA21) lê os blocos de tokens de static/index.html (`:root` e `[data-theme="x"]`) e do static/quadro.css
(bloco base do Quadro, aliases, `[data-theme="x"] body[data-view="quadro"]`, `[data-col]`, `[data-p]`), resolve `var()` e
mede os pares de texto/fundo do TEMAS.md com a fórmula WCAG 2.x. Só stdlib; o script do <head> corre em node (salta sem node).
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
SRC = (STATIC / "index.html").read_text(encoding="utf-8")
QCSS = (STATIC / "quadro.css").read_text(encoding="utf-8")
BCSS = (STATIC / "biblioteca.css").read_text(encoding="utf-8")
TEMAS = ["escuro", "claro", "sepia", "contraste"]   # temas efectivos (auto resolve para escuro/claro)
# rótulos curtos (review 1): música + tema + «ao vivo» numa linha a 360 px; o contexto vem do aria-label="Tema"
OPCOES = [("auto", "auto"), ("escuro", "escuro"), ("claro", "claro"), ("sepia", "sépia"), ("contraste", "contraste")]
COR = {"escuro": "#12141c", "claro": "#f4f5f9", "sepia": "#f3ead6", "contraste": "#000000"}   # CA24
PROJ = ["outro"] + [f"c{i}" for i in range(8)]   # paleta genérica (a config dá c{índice % 8} a cada projecto)
COLS = ["ideias", "a-fazer", "em-curso", "a-espera", "feito"]


def head_script() -> str:
    head = SRC[:SRC.index("</head>")]
    a = head.index("<script>") + len("<script>")
    return head[a:head.index("</script>", a)]


def estilo() -> str:
    """Todos os <style> do index.html (o CSS inline)."""
    return "".join(re.findall(r"<style>(.*?)</style>", SRC, re.S))


# --- contraste (copiado do contraste.py do TEMAS) ---
def lum(h: str) -> float:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4   # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# --- parser mínimo de CSS (regras planas; o que está dentro de @media aparece como regra simples) ---
REGRA = re.compile(r"([^{}]+)\{([^{}]*)\}")
DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def regras(css: str) -> list[tuple[str, dict]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(" ".join(s.split()), dict(DECL.findall(d))) for s, d in REGRA.findall(css)]


def temas_do_seletor(sel: str) -> set | None:
    """Temas em que o seletor vale (None = todos: não depende de data-theme)."""
    t = set(re.findall(r'\[data-theme="(\w+)"\]', sel))
    return t or None


def especificidade(sel: str) -> int:
    # aproximação suficiente para estes ficheiros: tema (+1) e um [data-p…]/[data-col…] fora de :where (+1)
    fora = re.sub(r":where\([^)]*\)", "", sel)
    return (1 if temas_do_seletor(sel) else 0) + (1 if re.search(r"\[data-(?:p|col)\b", fora) else 0)


def tokens_globais() -> dict[str, dict]:
    """{tema: {--x: valor}} do index.html; os temas não escuros têm de redefinir todas as chaves do :root."""
    out = {}
    for sel, d in regras(estilo()):
        if sel == ":root":
            out["escuro"] = d
        m = re.fullmatch(r'\[data-theme="(\w+)"\]', sel)
        if m:
            out[m.group(1)] = d
    return out


def resolver(v: str, *ctx: dict, prof=0) -> str:
    v = v.strip()
    m = re.fullmatch(r"var\((--[\w-]+)\)", v)
    if not m:
        return v
    assert prof < 10, v
    for c in ctx:
        if m.group(1) in c:
            return resolver(c[m.group(1)], *ctx, prof=prof + 1)
    raise KeyError(m.group(1))


def tokens_quadro(tema: str, g: dict) -> dict:
    """--q-* do Quadro no tema (regras com body[data-view="quadro"]), resolvidas contra os globais g."""
    q = {}
    for sel, d in regras(QCSS):
        if not sel.endswith('body[data-view="quadro"]'):
            continue
        ts = temas_do_seletor(sel)
        if ts is None or tema in ts:
            q.update(d)   # a ordem no ficheiro é base → aliases → tema
    return {k: resolver(v, q, g) for k, v in q.items() if k.startswith("--q-") and not k.startswith("--q-sh")
            and k not in ("--q-sans", "--q-mono", "--q-ease")}


def por_atributo(tema: str, attr: str, valor: str) -> dict:
    """Variáveis que um elemento com [attr="valor"] recebe no tema (cascata por especificidade e ordem)."""
    cand = []
    for i, (sel, d) in enumerate(regras(QCSS)):
        if f'[{attr}="{valor}"]' not in sel and not re.search(rf"\[{attr}\](?!=)", sel):
            continue
        if re.search(rf'\[{attr}="(?!{re.escape(valor)}")', sel):
            continue
        ts = temas_do_seletor(sel)
        if ts is None or tema in ts:
            cand.append((especificidade(sel), i, d))
    out = {}
    for _, _, d in sorted(cand, key=lambda x: (x[0], x[1])):
        out.update(d)
    return out


def pares(tema: str) -> list[tuple[str, str, str, float]]:
    """(nome, frente, fundo, mínimo) a verificar no tema; texto 4,5 (7 no contraste), UI 3."""
    g = tokens_globais()[tema]
    q = tokens_quadro(tema, g)
    txt, ui = (7.0 if tema == "contraste" else 4.5), 3.0
    c = lambda k: resolver(g[k], g)   # noqa: E731
    P = []
    for fg in ("--text", "--muted", "--accent", "--ok", "--warn", "--bad"):
        for bg in ("--bg", "--panel"):
            P.append((f"{fg}/{bg}", c(fg), c(bg), txt))
    for fg in ("--text", "--muted"):
        P.append((f"{fg}/--sunk", c(fg), c("--sunk"), txt))
        P.append((f"{fg}/--skel", c(fg), c("--skel"), txt))
    for s in ("ok", "warn", "bad"):
        P.append((f"--{s}/--{s}-bg", c(f"--{s}"), c(f"--{s}-bg"), txt))
        P.append((f"--text/--{s}-bg", c("--text"), c(f"--{s}-bg"), txt))
    P += [("--on-accent/--accent (botões preenchidos)", c("--on-accent"), c("--accent"), txt),
          ("--on-warn/--warn (.tag)", c("--on-warn"), c("--warn"), txt),
          ("--focus/--bg", c("--focus"), c("--bg"), ui), ("--focus/--panel", c("--focus"), c("--panel"), ui),
          ("--accent/--panel (activo/pressionado)", c("--accent"), c("--panel"), ui),
          ("--well-focus/--well (foco no pixel-art)", c("--well-focus"), c("--well"), ui),
          ("--well-focus/#2a2f3e (foco sobre o chão do escritório)", c("--well-focus"), "#2a2f3e", ui),
          ("--warn/--now-bg (.steps .now)", c("--warn"), resolver(g["--now-bg"], g), txt),
          ("--text/--act-bg (li.act)", c("--text"), resolver(g["--act-bg"], g), txt)]
    if tema != "escuro":   # CA19: no escuro --ctl = --line (decisão do tech lead)
        for bg in ("--bg", "--panel"):
            P.append((f"--ctl/{bg}", c("--ctl"), c(bg), ui))
        for bg in ("--q-col", "--q-card"):
            P.append((f"--ctl/{bg}", c("--ctl"), q[bg], ui))
    for fg, bg in (("--q-text", "--q-card"), ("--q-text", "--q-col"), ("--q-text", "--q-bg"), ("--q-muted", "--q-card"),
                   ("--q-muted", "--q-col"), ("--q-muted", "--q-pill"), ("--q-accent", "--q-card"),
                   ("--q-on-accent", "--q-accent"), ("--q-warn", "--q-warn-bg"), ("--q-warn", "--q-card"),
                   ("--q-on-bad", "--q-bad"), ("--q-ok", "--q-card"), ("--q-bad", "--q-card")):
        P.append((f"{fg}/{bg}", q[fg], q[bg], txt))
    for fg, bg in (("--q-focus", "--q-bg"), ("--q-focus", "--q-card"), ("--q-m2", "--q-card"), ("--q-ph", "--q-col")):
        P.append((f"{fg}/{bg}", q[fg], q[bg], ui))
    for col in COLS:
        cc = resolver(por_atributo(tema, "data-col", col)["--cc"], q, g)
        P.append((f"--cc {col}/--q-col", cc, q["--q-col"], txt))
    for p in PROJ:
        d = por_atributo(tema, "data-p", p)
        pc, pbg, ptx = (resolver(d[k], q, g) for k in ("--pc", "--pc-bg", "--pc-tx"))
        P.append((f"--pc-tx/--pc-bg {p}", ptx, pbg, txt))
        P.append((f"--pc/--q-card {p} (faixa)", pc, q["--q-card"], ui))
    return P


class SelectorTest(unittest.TestCase):
    def test_select_no_header_entre_volume_e_live(self):
        # CA14: <select id="tema" aria-label="Tema"> nativo, depois de #music/#vol e antes de #live, dentro do <header>
        h = SRC[SRC.index("<header>"):SRC.index("</header>")]
        i = h.index('<select id="tema" aria-label="Tema">')
        self.assertLess(h.index('id="music"'), i)
        self.assertLess(h.index('id="vol"'), i)
        self.assertLess(i, h.index('id="live"'))
        self.assertEqual(SRC.count('id="tema"'), 1)

    def test_opcoes_e_ordem(self):
        sel = re.search(r'<select id="tema"[^>]*>(.*?)</select>', SRC, re.S).group(1)
        self.assertEqual(re.findall(r'<option value="([^"]*)">([^<]*)</option>', sel), OPCOES)
        self.assertNotIn("selected", sel)   # defeito auto vem da 1.ª opção / do script

    def test_alvo_de_toque_e_foco(self):
        # CA25: 44 px; o foco usa o contorno global (--focus) ou o do browser
        self.assertRegex(estilo(), r"#tema\s*\{[^}]*min-height:\s*44px")
        self.assertRegex(estilo(), r"#tema\s*\{[^}]*font-size:\s*16px")   # o iOS faz zoom em selects < 16 px
        self.assertRegex(estilo(), r"#bell, #music, #tema \{[^}]*border: 1px solid var\(--ctl\)")
        # CA16: no alto contraste o foco é um contorno de 2 px na cor --focus (amarelo) em todos os controlos
        self.assertIn('[data-theme="contraste"] :focus-visible { outline: 2px solid var(--focus);', estilo())

    def test_change_aplica_e_grava_em_try(self):
        # CA22/CA25: change → temaAplicar + localStorage["tema"] em try/catch
        m = re.search(r'\$\("#tema"\)\.onchange = \(e\) => \{(.*)\};', SRC)
        self.assertIsNotNone(m)
        self.assertIn("temaAplicar(e.target.value)", m.group(1))
        self.assertRegex(m.group(1), r'try \{ localStorage\.setItem\("tema", temaEscolha\); \} catch')
        self.assertIn('$("#tema").value = temaEscolha;', SRC)


class HeadTest(unittest.TestCase):
    def test_script_antes_do_style(self):
        # CA23: <meta theme-color> < <script> < <style>, tudo no <head>
        head = SRC[:SRC.index("</head>")]
        meta, scr, sty = head.index('<meta name="theme-color"'), head.index("<script>"), head.index("<style>")
        self.assertLess(meta, scr)
        self.assertLess(scr, sty)
        self.assertIn("function temaAplicar(", head_script())
        self.assertNotIn("?.", head_script())   # ES2017: o <head> não pode partir browsers velhos

    def test_css_sem_prefers_color_scheme(self):
        # CA15/CA17 (nota do arquitecto em D10): o auto é resolvido pelo script, o CSS só tem :root + [data-theme]
        for nome, css in (("index <style>", estilo()), ("quadro.css", QCSS), ("biblioteca.css", BCSS)):
            with self.subTest(nome):
                self.assertNotIn("prefers-color-scheme", css)

    def test_theme_color_so_no_head(self):
        # CA24: bibRoute/qRoute já não mexem no theme-color
        corpo = SRC[SRC.index("</head>"):]
        self.assertNotIn("theme-color", corpo)
        self.assertEqual(SRC.count("theme-color"), 2)   # o <meta> e o querySelector do script

    def test_cada_tema_tem_color_scheme_e_todas_as_chaves(self):
        # CA15: color-scheme por tema; cada tema redefine todos os tokens do :root (nada fica com o valor do escuro por engano)
        css = re.sub(r"/\*.*?\*/", "", estilo(), flags=re.S)
        blocos = {":root": "dark", '[data-theme="claro"]': "light", '[data-theme="sepia"]': "light",
                  '[data-theme="contraste"]': "dark"}
        for sel, cs in blocos.items():
            with self.subTest(sel):
                corpo = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", css).group(1)
                self.assertRegex(corpo, rf"color-scheme:\s*{cs}\s*;")
        g = tokens_globais()
        self.assertEqual(set(g), set(TEMAS))
        for t in TEMAS[1:]:
            with self.subTest(t):
                self.assertEqual(set(g[t]), set(g["escuro"]))

    def test_paletas_de_partida(self):
        # CA16 (com os desvios aceites em D10 para o contraste) e theme-color do script = CA24
        g = tokens_globais()
        self.assertEqual({k: g["claro"][k] for k in ("--bg", "--panel", "--text", "--muted", "--accent", "--bad")},
                         {"--bg": "#f4f5f9", "--panel": "#ffffff", "--text": "#171a24", "--muted": "#5c6577",
                          "--accent": "#2460d0", "--bad": "#c62828"})
        self.assertEqual({k: g["sepia"][k] for k in ("--bg", "--panel", "--text", "--accent")},
                         {"--bg": "#f3ead6", "--panel": "#faf4e6", "--text": "#3d3224", "--accent": "#1f5a96"})
        self.assertEqual({k: g["contraste"][k] for k in ("--bg", "--text", "--accent", "--focus")},
                         {"--bg": "#000000", "--text": "#ffffff", "--accent": "#5ad1ff", "--focus": "#ffff00"})
        for t in TEMAS:
            self.assertEqual(g[t]["--bg"], COR[t], t)   # theme-color = fundo da página
        self.assertIn('TEMA_COR = { escuro: "#12141c", claro: "#f4f5f9", sepia: "#f3ead6", contraste: "#000000" }',
                      head_script())

    def test_escritorio_igual_em_todos_os_temas(self):
        # CA18: a moldura do pixel-art é --well (escuro em todos os temas, preto no contraste)
        g = tokens_globais()
        self.assertEqual({g[t]["--well"] for t in ("escuro", "claro", "sepia")}, {"#0d0f15"})
        self.assertRegex(estilo(), r"#office\s*\{[^}]*background:\s*var\(--well\)")

    def test_cores_fixas_da_moldura_viraram_tokens(self):
        # CA18: as cores fixas citadas na SPEC já não aparecem na moldura
        moldura = estilo()[estilo().index("* { box-sizing"):]   # tudo depois dos blocos de tokens
        self.assertNotIn("#2e2716", moldura)
        self.assertNotIn("#232838", moldura)
        self.assertNotRegex(moldura, r"color:\s*#0d0f15")
        for sel in ("#reader::backdrop", "#apr-dlg::backdrop"):
            self.assertRegex(moldura, re.escape(sel) + r"\s*\{[^}]*var\(--backdrop\)")


class ContrasteTest(unittest.TestCase):
    def test_parser_le_os_blocos(self):
        # protege contra um parser que não encontra nada (e passaria sempre)
        for t in TEMAS:
            with self.subTest(t):
                ps = pares(t)
                self.assertGreater(len(ps), 70)
                maus = [p for p in ps if not all(re.fullmatch(r"#[0-9a-f]{6}", x) for x in p[1:3])]
                self.assertEqual(maus, [])
        # cascata: no sépia o neutro escurece; no contraste a etiqueta é texto/painel; os temas claros usam as cores claras
        self.assertEqual(por_atributo("sepia", "data-p", "outro")["--pc"], "#7c8290")
        self.assertEqual(por_atributo("claro", "data-p", "outro")["--pc"], "#8a90a0")
        self.assertEqual(por_atributo("contraste", "data-p", "c3")["--pc-tx"], "var(--text)")
        self.assertEqual(por_atributo("claro", "data-p", "c3")["--pc-tx"], "#4b1769")
        self.assertEqual(por_atributo("escuro", "data-p", "c3")["--pc-tx"], "#d4acec")
        self.assertEqual(tokens_quadro("claro", tokens_globais()["claro"])["--q-text"], "#171a24")
        self.assertEqual(tokens_quadro("escuro", tokens_globais()["escuro"])["--q-text"], "#e8eaf2")

    def test_ratio(self):
        self.assertAlmostEqual(ratio("#000000", "#ffffff"), 21.0, places=2)
        self.assertAlmostEqual(ratio("#777", "#fff"), 4.48, places=2)

    def test_todos_os_temas_aa_e_contraste_aaa(self):
        # CA19/CA20/CA21: texto ≥ 4,5 (≥ 7 no contraste), foco/activo/bordos de controlo/faixas ≥ 3
        for t in TEMAS:
            falhas = [f"{n}: {a} / {b} = {ratio(a, b):.2f} (mín {m})" for n, a, b, m in pares(t) if ratio(a, b) < m]
            with self.subTest(tema=t):
                self.assertEqual(falhas, [])

    def test_chip_de_projecto_premido_distingue_se_no_contraste(self):
        # review 1: no contraste a etiqueta é texto/painel, logo o chip premido leva a marca de «Todos» (texto/fundo invertidos)
        self.assertIn('[data-theme="contraste"] .q-chip[data-p][aria-pressed="true"] { background: var(--q-text); color: var(--q-bg); '
                      'border-color: var(--q-text); }', QCSS)
        q = tokens_quadro("contraste", tokens_globais()["contraste"])
        self.assertGreaterEqual(ratio(q["--q-bg"], q["--q-text"]), 7)
        self.assertGreaterEqual(ratio(q["--q-text"], q["--q-col"]), 3)   # premido vs solto (fundo da pílula)

    def test_escuro_igual_ao_de_origem(self):
        # review 1: tons próprios do escuro repostos (passo «agora», linha activa); as sondas saíram (sem --probe-off)
        g = tokens_globais()["escuro"]
        self.assertEqual((g["--now-bg"], g["--act-bg"], g["--well-focus"]), ("#3a3220", "#1f2a1f", "#64b5f6"))
        self.assertNotIn("--probe-off", SRC)
        self.assertRegex(estilo(), r"#office \.seat:focus-visible > rect:first-of-type \{ stroke: var\(--well-focus\)")

    def test_escuro_ctl_igual_a_line(self):
        # CA19: decisão do tech lead (aspecto actual no escuro)
        g = tokens_globais()["escuro"]
        self.assertEqual(g["--ctl"], g["--line"])


# --- script do <head> em node, com document/localStorage/matchMedia falsos ---
HEAD_JS = r"""
const fs = require("fs"), vm = require("vm");
const code = fs.readFileSync(process.argv[2], "utf8");
const casos = JSON.parse(fs.readFileSync(0, "utf8"));
const out = casos.map((c) => {
  const attrs = {}, listeners = [], meta = { content: "#12141c" };
  const mq = c.mq === "none" ? null : { matches: !!c.light, ...(c.mq === "old"
    ? { addListener: (f) => listeners.push(f) } : { addEventListener: (ev, f) => ev === "change" && listeners.push(f) }) };
  const ls = c.ls === "throw" ? null : { getItem: (k) => (k === "tema" && "tema" in c ? c.tema : null) };
  const ctx = {
    document: { documentElement: { setAttribute: (k, v) => { attrs[k] = v; } },
                querySelector: (s) => (c.semMeta ? null : s === 'meta[name="theme-color"]' ? meta : null) },
  };
  if (mq) ctx.matchMedia = (q) => (q === "(prefers-color-scheme: light)" ? mq : { matches: false });
  if (ls) ctx.localStorage = ls;
  else Object.defineProperty(ctx, "localStorage", { get() { throw new Error("SecurityError"); } });
  ctx.window = ctx;
  let erro = null;
  try { vm.runInNewContext(code, ctx); } catch (e) { erro = String(e); }
  const r = () => ({ theme: attrs["data-theme"] || null, meta: meta.content, escolha: ctx.temaEscolha });
  const res = { erro, inicio: r(), globais: { cor: ctx.TEMA_COR, mq: ctx.temaMq === mq, aplicar: typeof ctx.temaAplicar } };
  if (c.sistema !== undefined && mq) { mq.matches = c.sistema; listeners.forEach((f) => f({ matches: c.sistema })); res.depois = r(); }
  if (c.aplicar !== undefined) { ctx.temaAplicar(c.aplicar); res.aplicado = r(); }
  return res;
});
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "sem node")
class HeadNodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.js, cls.code = d / "head.js", d / "head-script.js"
        cls.js.write_text(HEAD_JS)
        cls.code.write_text(head_script())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_casos(self, *casos: dict) -> list[dict]:
        r = subprocess.run(["node", str(self.js), str(self.code)], input=json.dumps(casos), capture_output=True,
                           text=True, timeout=30, check=True)
        return json.loads(r.stdout)

    def test_globais(self):
        [o] = self.run_casos({})
        self.assertIsNone(o["erro"])
        self.assertEqual(o["globais"], {"cor": COR, "mq": True, "aplicar": "function"})

    def test_tema_guardado_aplica_tema_e_theme_color(self):
        # CA22/CA23/CA24: cada tema guardado → data-theme e meta antes do 1.º paint (independente do sistema)
        casos = [{"tema": t, "light": light} for t in TEMAS for light in (False, True)]
        for c, o in zip(casos, self.run_casos(*casos)):
            with self.subTest(**c):
                self.assertIsNone(o["erro"])
                self.assertEqual(o["inicio"], {"theme": c["tema"], "meta": COR[c["tema"]], "escolha": c["tema"]})

    def test_sem_chave_ou_lixo_e_auto(self):
        # CA22: valor fora da lista = auto (inclui chaves herdadas de Object, que um «in» deixaria passar)
        casos = [{}, {"tema": None}, {"tema": ""}, {"tema": "rosa"}, {"tema": "AUTO"}, {"tema": "constructor"},
                 {"tema": "__proto__"}, {"tema": "toString"}, {"tema": "hasOwnProperty"}]
        for c, o in zip(casos, self.run_casos(*casos)):
            with self.subTest(**c):
                self.assertIsNone(o["erro"])
                self.assertEqual(o["inicio"], {"theme": "escuro", "meta": "#12141c", "escolha": "auto"})

    def test_localstorage_a_lancar_nao_parte(self):
        # CA22: storage indisponível (ex.: SecurityError) → auto, sem excepção
        [o1, o2] = self.run_casos({"ls": "throw"}, {"ls": "throw", "light": True})
        self.assertIsNone(o1["erro"])
        self.assertEqual(o1["inicio"], {"theme": "escuro", "meta": "#12141c", "escolha": "auto"})
        self.assertEqual(o2["inicio"], {"theme": "claro", "meta": "#f4f5f9", "escolha": "auto"})

    def test_auto_segue_o_sistema(self):
        # CA15/CA24: auto resolve pelo prefers-color-scheme e acompanha a mudança com a página aberta
        [a, b, c] = self.run_casos({"tema": "auto", "light": True}, {"light": False, "sistema": True},
                                   {"light": True, "sistema": False, "mq": "old"})   # Safari < 14: addListener
        self.assertEqual(a["inicio"], {"theme": "claro", "meta": "#f4f5f9", "escolha": "auto"})
        self.assertEqual(b["inicio"]["theme"], "escuro")
        self.assertEqual(b["depois"], {"theme": "claro", "meta": "#f4f5f9", "escolha": "auto"})
        self.assertEqual(c["inicio"]["theme"], "claro")
        self.assertEqual(c["depois"], {"theme": "escuro", "meta": "#12141c", "escolha": "auto"})

    def test_tema_fixo_ignora_o_sistema(self):
        [a, b] = self.run_casos({"tema": "escuro", "light": False, "sistema": True},
                                {"tema": "sepia", "light": True, "sistema": False})
        self.assertEqual(a["depois"], {"theme": "escuro", "meta": "#12141c", "escolha": "escuro"})
        self.assertEqual(b["depois"], {"theme": "sepia", "meta": "#f3ead6", "escolha": "sepia"})

    def test_temaAplicar_depois_do_arranque(self):
        # o selector chama temaAplicar(v): passa de auto para tema fixo e volta, lixo = auto
        [a, b, c] = self.run_casos({"aplicar": "contraste"}, {"tema": "claro", "light": True, "aplicar": "auto"},
                                   {"tema": "claro", "aplicar": "xpto"})
        self.assertEqual(a["aplicado"], {"theme": "contraste", "meta": "#000000", "escolha": "contraste"})
        self.assertEqual(b["aplicado"], {"theme": "claro", "meta": "#f4f5f9", "escolha": "auto"})
        self.assertEqual(c["aplicado"], {"theme": "escuro", "meta": "#12141c", "escolha": "auto"})

    def test_sem_matchmedia_nem_meta(self):
        # browsers sem matchMedia → escuro; sem <meta> → não parte
        [a, b] = self.run_casos({"mq": "none", "tema": "auto"}, {"semMeta": True, "tema": "sepia"})
        self.assertIsNone(a["erro"])
        self.assertEqual(a["inicio"], {"theme": "escuro", "meta": "#12141c", "escolha": "auto"})
        self.assertIsNone(b["erro"])
        self.assertEqual(b["inicio"]["theme"], "sepia")


if __name__ == "__main__":
    unittest.main()
