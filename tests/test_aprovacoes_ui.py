"""Testes estáticos do cartão Aprovações em static/index.html (CA21, CA24, CA25) e do md() com node.

O bloco do cartão fica entre «// ---- aprovações» e «// ---- fim aprovações». A análise das interpolações é
heurística: só olha para template strings com HTML («<» nas partes literais) e exige que cada «${…}» passe por
esc()/inline()/md() ou seja um valor que não vem do servidor (lista fechada, abaixo, com a razão).
O md() corre num processo node (salta sem node); sem rede nem relógio.
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"
SRC = HTML.read_text(encoding="utf-8")
INICIO, FIM = "// ---- aprovações", "// ---- fim aprovações"

# interpolações sem esc() aceites num template HTML do bloco, e porquê
SEGURAS = {
    "head": "HTML montado no próprio bloco só com esc(d.id)/esc(d.texto)",
    "dis": 'constante " disabled" ou ""',
    "n": "número (contagem de decisões em aberto)",
    "u.open": "booleano do estado de UI",
}
TERNARIO_LITERAL = re.compile(r'^(?:[^"\'`?+]|"[^"]*")*\?\s*"[^"]*"\s*:\s*"[^"]*"$')   # cond ? "a" : "b"
ESCAPA = re.compile(r"\b(?:esc|inline|md)\(")


def bloco() -> str:
    a, b = SRC.index(INICIO), SRC.index(FIM)
    assert a < b
    return SRC[a:b]


def templates(s: str) -> list[tuple[list[str], list[str]]]:
    """(partes literais, expressões ${…}) de cada template literal, incluindo os aninhados."""
    out = []

    def string(i):   # s[i] é ' ou "
        q, i = s[i], i + 1
        while s[i] != q:
            i += 2 if s[i] == "\\" else 1
        return i

    def tpl(i):   # s[i] é `
        lit, exprs, buf, j = [], [], "", i + 1
        while s[j] != "`":
            if s[j] == "\\":
                buf, j = buf + s[j:j + 2], j + 2
            elif s.startswith("${", j):
                lit.append(buf)
                buf, k = "", code(j + 2)
                exprs.append(s[j + 2:k])
                j = k + 1
            else:
                buf, j = buf + s[j], j + 1
        lit.append(buf)
        out.append((lit, exprs))
        return j

    def code(i):   # até ao «}» que fecha o ${
        depth = 0
        while True:
            c = s[i]
            if c in "\"'":
                i = string(i)
            elif c == "`":
                i = tpl(i)
            elif s.startswith("//", i):
                i = s.index("\n", i)
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0 and c == "}":
                    return i
                depth -= 1
            i += 1

    i = 0
    while i < len(s):
        c = s[i]
        if c in "\"'":
            i = string(i)
        elif c == "`":
            i = tpl(i)
        elif s.startswith("//", i):
            i = s.find("\n", i)
            if i < 0:
                break
        i += 1
    return out


class BlocoTest(unittest.TestCase):
    def test_marcadores_existem_uma_vez(self):
        self.assertEqual(SRC.count(INICIO), 1)
        self.assertEqual(SRC.count(FIM), 1)

    def test_sem_dialogos_nativos(self):
        # CA21: confirmação dentro da página; nada de confirm()/alert()/prompt()
        achados = re.findall(r"\b(?:confirm|alert|prompt)\s*\(", bloco())
        self.assertEqual(achados, [])

    def test_interpolacoes_html_escapadas(self):
        # CA25: cada ${…} num template com HTML passa por esc()/inline()/md() ou está na lista fechada
        tpls = templates(bloco())
        html = [e.strip() for lit, ex in tpls if any("<" in p for p in lit) for e in ex]
        self.assertGreater(len(html), 20)   # o scanner encontrou de facto os templates do cartão
        for e in html:
            with self.subTest(expr=e):
                self.assertTrue(ESCAPA.search(e) or e in SEGURAS or TERNARIO_LITERAL.match(e),
                                f"interpolação sem esc() num template HTML: ${{{e}}}")

    def test_html_so_por_caminhos_conhecidos(self):
        # innerHTML recebe só md(), o HTML do próprio cartão (h) ou um template; nada de outerHTML/document.write
        b = bloco()
        for rhs in re.findall(r"\.innerHTML\s*=\s*([^;]+);", b):
            with self.subTest(rhs=rhs):
                self.assertRegex(rhs.strip(), r"^(?:md\(|h$|\"\"$|list\.map\()")
        self.assertNotRegex(b, r"outerHTML|document\.write|insertAdjacentHTML")
        for arg in re.findall(r"setHtml\([^,]+,\s*([^;]+)\);", b):
            self.assertTrue(arg.startswith("esc("), arg)
        # título do <dialog> e estado como texto, não HTML
        self.assertIn('$("#ad-h").textContent = p.titulo', b)

    def test_caixa_de_observacao_e_alvos_moveis(self):
        b = bloco()
        self.assertIn('maxlength="2000"', b)   # CA19
        self.assertIn("escreve o motivo", b)   # CA20
        self.assertIn("Recebido · a aplicar", b)   # CA19
        self.assertRegex(SRC, r"\.apr button[^{]*\{[^}]*min-height:\s*44px")   # CA26
        self.assertRegex(SRC, r"\.md \.tw\s*\{[^}]*overflow-x:\s*auto")   # CA26: scroll só no bloco da tabela

    def test_seccao_no_fim_da_pagina(self):
        # CA1/CA4 (substitui o CA18 antigo): <section id="aprovacoes" hidden> depois da Fábrica, do escritório, da Biblioteca e
        # do Quadro, antes só do #banner; dentro, o <details id="apr-d"> aberto por defeito com o <h2> no <summary>
        i = SRC.index('<section id="aprovacoes"')
        for antes in ('<section id="fabrica"', "<main>", 'id="bib"', 'id="quadro"'):
            with self.subTest(antes=antes):
                self.assertGreater(i, SRC.index(antes))
        self.assertLess(i, SRC.index('id="banner"'))
        self.assertRegex(SRC[i:SRC.index(">", i)], r"\bhidden\b")
        self.assertIn('<details id="apr-d" open><summary><h2 id="apr-h">', SRC[i:])


class ScannerTest(unittest.TestCase):
    def test_scanner_apanha_interpolacao_sem_esc(self):
        # o próprio teste falharia com uma interpolação crua (protege contra um scanner que não vê nada)
        src = 'const x = `<b>${p.titulo}</b>` + `${esc(a)}`; // `${nao}`\nconst y = "`${z}`";'
        got = [(any("<" in p for p in lit), ex) for lit, ex in templates(src)]
        self.assertEqual(got, [(True, ["p.titulo"]), (False, ["esc(a)"])])
        self.assertFalse(ESCAPA.search("p.titulo") or "p.titulo" in SEGURAS or TERNARIO_LITERAL.match("p.titulo"))
        self.assertTrue(TERNARIO_LITERAL.match('d.feito === "aprovado" ? "s-run" : "s-bad"'))
        self.assertFalse(TERNARIO_LITERAL.match('x ? "a" + d.texto : ""'))


# --- md() em node ---
NODE_JS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const esc = src.match(/^const esc = .*$/m)[0];
const a = src.indexOf("function inline("), b = src.indexOf("\nconst rd = ", a);
const fns = vm.runInNewContext(esc + "\n" + src.slice(a, b) + "\n;({ esc, inline, md })");
const inputs = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(inputs.map((s) => fns.md(s))));
"""

TAGS = {"p", "br", "ul", "ol", "li", "h3", "h4", "h5", "code", "pre", "strong", "a", "div", "table", "tr", "th", "td"}
ATTRS = {"class", "scope", "href", "target", "rel"}
XSS = ["<script>alert(1)</script>", "<img src=x onerror=alert(2)>", '"><svg onload=alert(3)>',
       "<a href=javascript:alert(4)>x</a>", "<iframe src=//evil></iframe>"]


class Elementos(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs, self.hrefs = [], set(), []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for k, v in attrs:
            self.attrs.add(k)
            if k == "href":
                self.hrefs.append(v)


def elementos(html: str) -> Elementos:
    p = Elementos()
    p.feed(html)
    p.close()
    return p


@unittest.skipUnless(shutil.which("node"), "sem node")
class MdNodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.js = Path(cls.tmp.name) / "md.js"
        cls.js.write_text(NODE_JS)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def md(self, *inputs: str) -> list[str]:
        r = subprocess.run(["node", str(self.js), str(HTML)], input=json.dumps(inputs), capture_output=True,
                           text=True, timeout=30, check=True)
        return json.loads(r.stdout)

    def test_tabela(self):
        [out] = self.md("antes\n| Campanha | Tecto |\n|---|:---:|\n| MZ | **145 €** |\n| a \\| b | `c` |\ndepois")
        self.assertEqual(out, '<p>antes</p><div class="tw"><table>'
                              '<tr><th scope="col">Campanha</th><th scope="col">Tecto</th></tr>'
                              '<tr><td>MZ</td><td><strong>145 €</strong></td></tr>'
                              '<tr><td>a | b</td><td><code>c</code></td></tr></table></div><p>depois</p>')

    def test_linha_com_barras_sem_separador_nao_e_tabela(self):
        [out] = self.md("| a | b |\n| c | d |")
        self.assertNotIn("<table", out)
        self.assertEqual(out, "<p>| a | b |<br>| c | d |</p>")

    def test_xss_em_todos_os_blocos(self):
        # CA25: título, parágrafo, lista, negrito, código, tabela (cabeçalho e células), link
        for x in XSS:
            doc = (f"# {x}\n\n{x}\n\n- {x}\n1. {x}\n\n**{x}**\n\n`{x}`\n\n```\n{x}\n```\n\n"
                   f"| {x} | b |\n|---|---|\n| {x} | https://ex.com/{x} |\n")
            with self.subTest(x=x):
                [out] = self.md(doc)
                e = elementos(out)
                self.assertLessEqual(set(e.tags), TAGS)
                self.assertLessEqual(e.attrs, ATTRS)
                self.assertTrue(all(h.startswith("https://") for h in e.hrefs), e.hrefs)
                self.assertNotRegex(out, r"<(?:script|img|svg|iframe)\b")   # «onerror=» só como texto escapado
                # texto literal: o «<» aparece escapado em cada um dos 10 sítios
                self.assertGreaterEqual(out.count("&lt;"), 10)

    def test_links_so_https_escapados_com_noopener(self):
        [out] = self.md('ver https://ex.com/a?b=1&c="x" e javascript:alert(1) e http://ex.com')
        e = elementos(out)
        self.assertEqual(e.hrefs, ["https://ex.com/a?b=1&c="])
        self.assertIn('rel="noopener noreferrer"', out)
        self.assertIn("&amp;", out)
        self.assertNotIn('href="javascript', out)
        self.assertNotIn('href="http://', out)

    def test_vazio_e_nulo(self):
        self.assertEqual(self.md("", "\n\n"), ["", ""])


if __name__ == "__main__":
    unittest.main()


SEND_JS = r"""
// corre o aprSend() verdadeiro do index.html com fetch/estado falsos; imprime u.sent, u.err e se recarregou
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const a = src.indexOf("async function aprSend(k) {"), b = src.indexOf("\n}\n", a);
const resp = JSON.parse(fs.readFileSync(0, "utf8"));
const p = { fonte: "marketing", ficheiro: "a.md", sha256: "s1", decisoes: [{ id: "P1", aberta: true }] };
const u = { confirm: { id: "P1", accao: "aprovar", sha: "s1" }, notes: { P1: "x" }, sent: {}, busy: false, err: null };
let reloaded = false;
const env = { aFind: () => p, aUi: () => u, aOpen: () => p.decisoes, renderApr: () => {}, tick: () => { reloaded = true; },
  state: { token: "t" }, fetch: async () => ({ status: resp.status, json: async () => resp.body }) };
const fn = new Function(...Object.keys(env), src.slice(a, b + 2) + "\nreturn aprSend;")(...Object.values(env));
fn("k").then(() => console.log(JSON.stringify({ sent: u.sent, err: u.err, reloaded, notes: u.notes })));
"""


@unittest.skipUnless(shutil.which("node"), "sem node")
class SendNodeTest(unittest.TestCase):
    def send(self, status, body):
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "send.js"
            js.write_text(SEND_JS)
            r = subprocess.run(["node", str(js), str(HTML)], input=json.dumps({"status": status, "body": body}),
                               capture_output=True, text=True, timeout=30, check=True)
        return json.loads(r.stdout)

    def test_409_recebida_fica_como_enviada(self):
        out = self.send(409, {"error": "decisão já recebida", "code": "recebida"})
        self.assertEqual(out["sent"], {"P1": {"accao": "aprovar", "sha": "s1"}})
        self.assertIsNone(out["err"])
        self.assertTrue(out["reloaded"])

    def test_outro_409_mostra_erro(self):
        out = self.send(409, {"error": "o pedido mudou, recarrega"})
        self.assertEqual(out["sent"], {})
        self.assertEqual(out["err"], {"id": "P1", "msg": "o pedido mudou, recarrega"})
        self.assertEqual(out["notes"], {"P1": "x"})

    def test_202(self):
        out = self.send(202, {"ok": True})
        self.assertEqual(out["sent"], {"P1": {"accao": "aprovar", "sha": "s1"}})
        self.assertEqual(out["notes"], {})


# --- secção recolhível e pedidos abertos (SPEC office-aprovacoes-fundo-temas, CA5–CA12) ---
def funcao(nome: str) -> str:
    """Corpo de «function nome(…) {…}» no index.html (até ao «}» na coluna 0)."""
    a = SRC.index(f"function {nome}(")
    return SRC[a:SRC.index("\n}\n", a) + 2]


class SecaoTest(unittest.TestCase):
    def test_summary_com_titulo_e_contagem(self):
        # CA5: <summary> com o <h2 id="apr-h"> e o <span id="apr-n"> (M); alvo de toque de 44 px
        self.assertIn('<details id="apr-d" open><summary><h2 id="apr-h">Aprovações</h2> <span id="apr-n"></span></summary>'
                      '<div id="apr-list"></div></details></section>', SRC)
        self.assertRegex(SRC, r"#apr-d > summary\s*\{[^}]*min-height:\s*44px")
        r = funcao("renderApr")
        self.assertIn('setHtml($("#apr-h"), esc(`Aprovações (${list.length})`));', r)
        self.assertIn('setHtml($("#apr-n"), esc(`· ${m} ${m === 1 ? "decisão em aberto" : "decisões em aberto"}`));', r)
        self.assertIn("const m = aTotal(list);", r)

    def test_poll_nunca_toca_no_details(self):
        # CA8: renderApr (chamado pelo poll) não referencia o <details> nem grava estado; tick também não
        for nome in ("renderApr", "tick"):
            with self.subTest(nome):
                corpo = funcao(nome)
                self.assertNotIn("apr-d", corpo)
                self.assertNotIn("aGravarAbertos", corpo)
                self.assertNotIn("aprSecao", corpo)

    def test_gravacao_so_no_clique_e_em_verAprovacao(self):
        # D5/CA10: aGravarAbertos é chamado só no clique «tog» e em verAprovacao (nunca no poll)
        chamadas = [m.start() for m in re.finditer(r"(?<!function )aGravarAbertos\(\)", SRC)]
        self.assertEqual(len(chamadas), 2)
        linhas = [SRC[SRC.rindex("\n", 0, i):SRC.index("\n", i)] for i in chamadas]
        self.assertIn('if (a === "tog") { u.open = !u.open; aGravarAbertos(); }', linhas[0])
        self.assertIn("aUi(k).open = true; aGravarAbertos();", linhas[1])
        self.assertIn(linhas[1].strip(), funcao("verAprovacao"))

    def test_storage_sempre_em_try(self):
        # CA7/CA10/CA22: cada acesso a localStorage das chaves novas está numa linha com try { … } catch
        for chave in ("aprSecao", "aprAbertos", '"tema"'):
            linhas = [l for l in SRC.splitlines() if "localStorage" in l and chave in l and not l.lstrip().startswith("//")]
            with self.subTest(chave):
                self.assertGreaterEqual(len(linhas), 1)
                for l in linhas:
                    self.assertRegex(l, r"try \{.*localStorage.*\} catch", l)
        self.assertIn(".slice(0, 100)", funcao("aGravarAbertos"))

    def test_secao_reposta_e_gravada_no_toggle(self):
        # CA6/CA7: só "0" recolhe; o toggle grava "1"/"0"
        b = bloco()
        self.assertIn('try { if (localStorage.getItem("aprSecao") === "0") $("#apr-d").open = false; } catch {}', b)
        self.assertIn('$("#apr-d").addEventListener("toggle", (e) => { try { localStorage.setItem("aprSecao", '
                      'e.target.open ? "1" : "0"); } catch {} });', b)

    def test_verAprovacao_abre_secao_e_pedido_antes_do_scroll(self):
        # CA12: abre o <details>, o pedido (gravado), repinta, faz scroll e foca o .apr-h
        v = funcao("verAprovacao")
        passos = ['$("#apr-d").open = true;', "aUi(k).open = true; aGravarAbertos();", "renderApr();",
                  "scrollIntoView(", "[data-f=\"tog\"]')?.focus("]
        pos = [v.index(p) for p in passos]
        self.assertEqual(pos, sorted(pos))


ABERTOS_JS = r"""
// corre aLerAbertos/aTotal/aGravarAbertos verdadeiros do index.html com estado e localStorage falsos
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const linha = (re) => src.match(re)[0];
const fn = (nome) => { const a = src.indexOf(`function ${nome}(`); return src.slice(a, src.indexOf("\n}\n", a) + 2); };
const code = [linha(/^const aLerAbertos = .*$/m), "let aAbertos = new Set();", linha(/^const aprUi = .*$/m),
  linha(/^const aUi = .*$/m), fn("aGravarAbertos"), linha(/^const aSent = .*$/m), linha(/^const aOpen = .*$/m),
  linha(/^const aTotal = .*$/m), "({ aLerAbertos, aTotal, aGravarAbertos, aprUi, aUi, get aAbertos() { return aAbertos; }, set aAbertos(v) { aAbertos = v; } })"].join("\n");
const caso = JSON.parse(fs.readFileSync(0, "utf8"));
const gravado = [];
const ctx = { state: caso.state, localStorage: { setItem: (k, v) => { if (caso.lancar) throw new Error("quota"); gravado.push([k, v]); } } };
const f = vm.runInNewContext(code, ctx);
const out = {};
if (caso.ler) out.ler = caso.ler.map((raw) => [...f.aLerAbertos(raw)]);
if (caso.abertos) f.aAbertos = new Set(caso.abertos);
if (caso.estados) out.estados = caso.state.aprovacoes.map((p) => f.aUi(p.fonte + "/" + p.ficheiro).open);
if (caso.ui) for (const [k, u] of Object.entries(caso.ui)) Object.assign(f.aUi(k), u);
if (caso.total) out.total = f.aTotal(caso.state.aprovacoes);
if (caso.gravar) { let erro = null; try { f.aGravarAbertos(); } catch (e) { erro = String(e); } out.gravar = { erro, gravado, abertos: [...f.aAbertos] }; }
process.stdout.write(JSON.stringify(out));
"""


def ped(fonte, ficheiro, *decs, sha="s1"):
    return {"fonte": fonte, "ficheiro": ficheiro, "sha256": sha,
            "decisoes": [{"id": i, "aberta": aberta} for i, aberta in decs]}


@unittest.skipUnless(shutil.which("node"), "sem node")
class AbertosNodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.js = Path(cls.tmp.name) / "abertos.js"
        cls.js.write_text(ABERTOS_JS)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_caso(self, **caso):
        caso.setdefault("state", {"aprovacoes": []})
        r = subprocess.run(["node", str(self.js), str(HTML)], input=json.dumps(caso), capture_output=True,
                           text=True, timeout=30, check=True)
        return json.loads(r.stdout)

    def test_aLerAbertos(self):
        # CA10: só array de strings; JSON inválido, null, objecto, números = nenhum aberto (sem excepção)
        raws = ['["marketing/a.md","geral/b.md"]', "[]", "{mau", None, "null", '{"a":1}', "[1,2]", '["x",3]', '"x"', ""]
        out = self.run_caso(ler=raws)["ler"]
        self.assertEqual(out, [["marketing/a.md", "geral/b.md"], [], [], [], [], [], [], [], [], []])

    def test_estado_inicial_vem_do_storage(self):
        # CA9/CA10: por defeito recolhidos; as chaves guardadas abrem
        st = {"aprovacoes": [ped("marketing", "a.md", ("P1", True)), ped("geral", "b.md", ("P1", True))]}
        out = self.run_caso(state=st, abertos=["geral/b.md", "geral/sumiu.md"], estados=True, gravar=True)
        self.assertEqual(out["estados"], [False, True])
        self.assertEqual(out["gravar"]["abertos"], ["geral/b.md"])   # ao gravar, só as chaves da lista actual

    def test_aTotal_conta_como_o_cartao(self):
        # CA5/D4: M = soma de aOpen; não conta fechadas, «Recebido» (aberta=false) nem enviadas daqui na mesma versão
        st = {"aprovacoes": [ped("marketing", "a.md", ("P1", True), ("P2", True), ("P3", False)),
                             ped("geral", "b.md", ("P1", True), ("P2", True), sha="s2"),
                             ped("geral", "c.md", ("*", False))]}
        ui = {"geral/b.md": {"sent": {"P1": {"accao": "aprovar", "sha": "s2"}, "P2": {"accao": "comentar", "sha": "s2"}}}}
        self.assertEqual(self.run_caso(state=st, total=True)["total"], 4)
        self.assertEqual(self.run_caso(state=st, ui=ui, total=True)["total"], 3)   # comentar não fecha
        velho = {"geral/b.md": {"sent": {"P1": {"accao": "recusar", "sha": "s-antigo"}}}}
        self.assertEqual(self.run_caso(state=st, ui=velho, total=True)["total"], 4)   # outra versão do pedido
        self.assertEqual(self.run_caso(total=True)["total"], 0)

    def test_gravar_poda_a_lista_actual_e_limita_a_100(self):
        # CA10: ficam só as chaves presentes e abertas; máx. 100; grava JSON em aprAbertos
        st = {"aprovacoes": [ped("geral", f"p{i:03}.md", ("P1", True)) for i in range(130)]}
        ui = {f"geral/p{i:03}.md": {"open": True} for i in range(0, 130)}
        ui["geral/p000.md"] = {"open": False}
        ui["geral/sumiu.md"] = {"open": True}   # estado de UI de um pedido que já não está na lista
        out = self.run_caso(state=st, ui=ui, gravar=True)["gravar"]
        self.assertIsNone(out["erro"])
        [(k, v)] = out["gravado"]
        self.assertEqual(k, "aprAbertos")
        ks = json.loads(v)
        self.assertEqual(len(ks), 100)
        self.assertEqual(ks[0], "geral/p001.md")
        self.assertNotIn("geral/sumiu.md", ks)
        self.assertEqual(out["abertos"], ks)

    def test_gravar_sem_storage_nao_parte(self):
        st = {"aprovacoes": [ped("geral", "a.md", ("P1", True))]}
        out = self.run_caso(state=st, ui={"geral/a.md": {"open": True}}, gravar=True, lancar=True)["gravar"]
        self.assertIsNone(out["erro"])
        self.assertEqual(out["abertos"], ["geral/a.md"])

    def test_gravar_sem_state(self):
        # antes do 1.º poll (state null) não parte e não guarda chaves
        out = self.run_caso(state=None, gravar=True)["gravar"]
        self.assertEqual((out["erro"], out["gravado"]), (None, [["aprAbertos", "[]"]]))
