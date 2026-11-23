// Testes de static/md.js (node --test tests/md_test.mjs): o conversor de markdown da Conversa v2 (CA-21, CA-22).
// A árvore só tem strings (texto) e nós {t, a, c}; materializar() corre sobre um DOM falso em que innerHTML rebenta.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, statSync } from "node:fs";
import { md, linkSeguro, realce, materializar } from "../static/md.js";

const URL_MD = new URL("../static/md.js", import.meta.url);
// texto plano de uma árvore (para conferir que nada se perde nem vira HTML)
const plano = (xs) => (xs || []).map((n) => (typeof n === "string" ? n : plano(n.c))).join("");
const tipos = (xs) => (xs || []).flatMap((n) => (typeof n === "string" ? [] : [n.t, ...tipos(n.c)]));
const acha = (xs, t) => { for (const n of xs || []) { if (typeof n === "string") continue; if (n.t === t) return n; const r = acha(n.c, t); if (r) return r; } return null; };

test("blocos: títulos (h4+ → h3), parágrafos com quebras, regra, citação", () => {
  const a = md("# Um\n## Dois ##\n#### Quatro\n\nlinha 1\nlinha 2\n\n---\n> citado\n> mais");
  assert.deepEqual(a.map((n) => n.t), ["h1", "h2", "h3", "p", "hr", "blockquote"]);
  assert.equal(plano(a[1].c), "Dois");
  assert.equal(plano(a[3].c), "linha 1\nlinha 2");
  assert.equal(plano(a[5].c), "citado\nmais");
  assert.deepEqual(md("#hashtag"), [{ t: "p", c: ["#hashtag"] }]);
  assert.deepEqual(md(""), []);
  assert.deepEqual(md(null), []);
});

test("listas: marcadores, numeradas, aninhamento de 1 nível e itens separados por linha vazia", () => {
  const a = md("- a\n- b\n  - b1\n  - b2\n- c\n\n1. um\n\n2. dois");
  assert.deepEqual(a.map((n) => n.t), ["ul", "ol"]);
  assert.equal(a[0].c.length, 3);
  assert.deepEqual(a[0].c[1].c.at(-1).t, "ul");
  assert.equal(plano(a[0].c[1].c.at(-1).c), "b1b2");
  assert.equal(a[1].c.length, 2, "1. e 2. separados por uma linha vazia ficam na mesma lista");
  // segundo nível e mais fundo ficam no 1.º nível de aninhamento
  assert.equal(md("- a\n    - b\n      - c")[0].c[0].c.at(-1).c.length, 2);
});

test("em linha: negrito, itálico, código, links e autolinks; snake_case não é itálico", () => {
  const [p] = md("**forte** e *leve* e _também_ e `x < y` em [site](https://ex.pt/a?b=1) ou https://ex.pt/c. nome_de_var");
  assert.deepEqual(tipos(p.c), ["b", "i", "i", "code", "a", "a"]);
  assert.equal(acha(p.c, "code").c[0], "x < y");
  const as = p.c.filter((n) => n.t === "a");
  assert.deepEqual(as.map((n) => n.a.href), ["https://ex.pt/a?b=1", "https://ex.pt/c"]);
  assert.equal(plano(as[1].c), "https://ex.pt/c", "o ponto final fica fora do autolink");
  assert.ok(plano(p.c).endsWith("nome_de_var"));
  assert.deepEqual(tipos(md("**negrito com *itálico* dentro**")[0].c), ["b", "i"]);
  assert.deepEqual(md("2 * 3 * 4")[0].c, ["2 * 3 * 4"]);
  assert.deepEqual(md("um ` solto")[0].c, ["um ` solto"]);
});

test("HTML no texto fica texto literal: <script>, <img onerror> e entidades", () => {
  for (const s of ["<script>alert(1)</script>", `<img src=x onerror="globalThis.pwned=1">`, "&lt;b&gt; e &amp;", "<a href=\"javascript:x\">y</a>"]) {
    const a = md(s);
    assert.equal(plano(a), s, s);
    assert.ok(!tipos(a).some((t) => !["p"].includes(t)), s);
  }
});

test("linkSeguro: só http(s); javascript:, data:, vbscript:, maiúsculas, «//x» e espaços/controlos antes do esquema → null", () => {
  assert.equal(linkSeguro("https://ex.pt/x"), "https://ex.pt/x");
  assert.equal(linkSeguro("HTTP://ex.pt"), "http://ex.pt/");
  for (const u of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<b>", "vbscript:x", "//ex.pt", " https://ex.pt", "\u0001https://ex.pt",
    "\thttps://ex.pt", "java\nscript:x", "https:ex.pt", "http://", "ftp://ex.pt", "", null, "/rel", "#x", "mailto:a@b.pt"])
    assert.equal(linkSeguro(u), null, JSON.stringify(u));
  for (const u of ["javascript:void0", "JaVaScRiPt:void0", "data:text/html,x", "vbscript:x", "//ex.pt"]) {
    const a = md(`[clica](${u})`);
    assert.equal(acha(a, "a"), null, u);
    assert.equal(plano(a), "clica", u);
  }
});

test("imagem vira link «imagem: alt» (nunca <img>); imagem com URL perigoso fica texto", () => {
  const a = md("![gráfico](https://ex.pt/g.png)");
  assert.deepEqual(a, [{ t: "p", c: [{ t: "a", a: { href: "https://ex.pt/g.png" }, c: ["imagem: gráfico"] }] }]);
  assert.deepEqual(md("![x](javascript:alert(1))")[0].c[0], "imagem: x");
  assert.ok(!tipos(md("![a](https://x.pt/a.png) ![b](data:image/png;base64,AA)")).includes("img"));
});

test("código: bloco com realce simples; bloco por fechar fecha no fim (CA-22); dentro do bloco nada é markdown", () => {
  const a = md("antes\n```py\nx = \"s\"  # nota\n**não negrito**\n```\ndepois");
  assert.deepEqual(a.map((n) => n.t), ["p", "pre", "p"]);
  const code = a[1].c[0];
  assert.equal(code.t, "code");
  assert.equal(plano(code.c), "x = \"s\"  # nota\n**não negrito**");
  assert.deepEqual(code.c.filter((n) => typeof n !== "string").map((n) => [n.a.class, n.c[0]]), [["md-s", "\"s\""], ["md-c", "# nota"]]);
  const b = md("ok\n```\nlinha sem fim\n# não é título");
  assert.deepEqual(b.map((n) => n.t), ["p", "pre"]);
  assert.equal(plano(b[1].c), "linha sem fim\n# não é título");
  assert.deepEqual(realce("a // c\nb /* d */ 'e'").filter((n) => typeof n !== "string").map((n) => n.a.class), ["md-c", "md-c", "md-s"]);
  assert.deepEqual(realce(""), []);
});

test("tabelas GFM: cabeçalho, linhas com células a mais/menos; tabela partida fica parágrafo", () => {
  const a = md("| A | B |\n|---|:-:|\n| 1 | **2** |\n| 3 |\n| 4 | 5 | 6 |\nfim");
  assert.equal(a[0].t, "table");
  assert.deepEqual(a[0].c.map((r) => r.c.map((c) => c.t + ":" + plano(c.c))), [["th:A", "th:B"], ["td:1", "td:2"], ["td:3", "td:"], ["td:4", "td:5"]]);
  assert.deepEqual(tipos(a[0].c[1].c[1].c), ["b"]);
  assert.equal(a[1].t, "p");
  assert.deepEqual(md("| A | B |\n| sem separador |").map((n) => n.t), ["p"]);
  assert.deepEqual(md("a | b\n---").map((n) => n.t), ["p", "hr"], "separador com outro número de células não é tabela");
});

test("tempo linear: 16 000 caracteres patológicos em < 2 s de CPU cada (nunca relógio)", () => {
  const N = 16000;
  const casos = ["[".repeat(N), "![".repeat(N / 2), "`".repeat(N), "*".repeat(N), "_".repeat(N), "**a".repeat(N / 3), "[a](".repeat(N / 4),
    "http://" + ".".repeat(N), " ".repeat(N) + "x", "#".repeat(N), "|".repeat(N) + "\n" + "|-".repeat(N / 2), "- ".repeat(N / 2), "> ".repeat(N / 2),
    "```\n" + "/*'\"".repeat(N / 4), "a\n".repeat(N / 2), "1. x\n\n".repeat(N / 6), "# " + " #".repeat(N / 2) + " x", "h".repeat(N), "x h".repeat(N / 3)];
  for (const s of casos) {
    const t0 = process.cpuUsage();
    const a = md(s);
    const u = process.cpuUsage(t0), ms = (u.user + u.system) / 1000;
    assert.ok(ms < 2000, `${JSON.stringify(s.slice(0, 12))}…: ${ms} ms`);
    assert.ok(Array.isArray(a));
  }
});

// ---- DOM falso: innerHTML rebenta; texto só por append(string)
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.kids = []; this.attrs = {}; }
  set innerHTML(_) { throw new Error("innerHTML usado"); }
  set outerHTML(_) { throw new Error("outerHTML usado"); }
  insertAdjacentHTML() { throw new Error("insertAdjacentHTML usado"); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  append(...xs) { for (const x of xs) this.kids.push(x); }
  get textContent() { return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join(""); }
  *todos() { for (const k of this.kids) if (k instanceof El) { yield k; yield* k.todos(); } }
}
const docFalso = { createElement: (t) => new El(t), createDocumentFragment: () => new El("#frag") };

test("materializar: só createElement/append; <a> com rel e target; só href e class como atributos", () => {
  const XSS = `<img src=x onerror="globalThis.pwned=1">`;
  const f = materializar(md(`# ${XSS}\n\n[ver](https://ex.pt) e \`${XSS}\`\n\n\`\`\`\n// ${XSS}\n\`\`\``), docFalso);
  const els = [...f.todos()];
  assert.ok(f.textContent.includes(XSS));
  assert.ok(!els.some((e) => e.tagName === "IMG"));
  const a = els.find((e) => e.tagName === "A");
  assert.deepEqual(a.attrs, { href: "https://ex.pt/", rel: "noopener noreferrer", target: "_blank" });
  assert.ok(els.every((e) => Object.keys(e.attrs).every((k) => ["href", "rel", "target", "class"].includes(k))));
  assert.ok(els.some((e) => e.attrs.class === "md-c"));
  assert.equal(globalThis.pwned, undefined);
});

test("md.js: sem innerHTML/insertAdjacentHTML/outerHTML/eval, sem dependências e < 10 240 bytes", () => {
  const src = readFileSync(URL_MD, "utf8").split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");
  assert.doesNotMatch(src, /innerHTML|insertAdjacentHTML|outerHTML|\beval\(|new Function|^import /m);
  assert.ok(statSync(URL_MD).size < 10240, `${statSync(URL_MD).size} bytes`);
});

// ---- QA (Conversa v2): mais casos de markdown seguro e mensagens a meio

test("QA CA-21: esquemas perigosos com parênteses, espaços e dentro de negrito/tabela/lista ficam texto; aspas no URL ficam codificadas", () => {
  for (const s of ["[x](javascript:alert(1))", "[x]( javascript:alert(1))", "[x](JAVASCRIPT:alert(1))", "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "**[x](javascript:alert(1))**", "- [x](vbscript:msgbox)", "| a |\n|---|\n| [x](javascript:x) |", "javascript:alert(1)", "data:text/html,<b>x</b>",
    "<javascript:alert(1)>", "![x](data:image/svg+xml,<svg onload=alert(1)>)", "[x](file:///etc/passwd)"])
    assert.equal(acha(md(s), "a"), null, s);
  // o texto mantém-se (nada desaparece em silêncio)
  assert.ok(plano(md("**[x](javascript:alert(1))**")).includes("x"));
  const a = acha(md(`[x](https://ex.pt/"onmouseover=alert(1))`), "a");
  assert.ok(a && !a.a.href.includes('"'), a?.a.href);
  // imagens em listas e tabelas também não viram <img>
  assert.ok(!tipos(md("- ![a](https://x.pt/a.png)\n\n| i |\n|---|\n| ![b](https://x.pt/b.png) |")).includes("img"));
});

test("QA CA-21: HTML cru em título, lista, citação, tabela e código em linha fica texto literal", () => {
  const H = `<img src=x onerror=alert(1)>`, a = md(`# ${H}\n\n- ${H}\n\n> ${H}\n\n| ${H} |\n|---|\n| <script>x</script> |\n\n\`${H}\``);
  assert.deepEqual(a.map((n) => n.t), ["h1", "ul", "blockquote", "table", "p"]);
  assert.ok(!tipos(a).some((t) => ["img", "script", "iframe"].includes(t)));
  assert.equal(plano(a).split(H).length - 1, 5);
  assert.ok(plano(a).includes("<script>x</script>"));
});

test("QA CA-22: mensagem a meio (negrito, link, tabela, lista por acabar) desenha-se sem partir e volta a desenhar-se completa", () => {
  const fim = "Resumo **importante** com [link](https://ex.pt).\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- um\n- dois\n\n```js\nconst x = 1; // ok\n```";
  for (let i = 0; i <= fim.length; i++) {
    const a = md(fim.slice(0, i));
    assert.ok(Array.isArray(a));
    assert.equal(plano(a).replace(/\s/g, "").length <= fim.replace(/\s/g, "").length, true);
  }
  assert.deepEqual(md("Resumo **impor").map((n) => n.t), ["p"]);
  assert.deepEqual(md("Resumo **impor")[0].c, ["Resumo **impor"], "negrito por fechar fica texto");
  // link por fechar: o «[link](» fica texto (o URL solto pode virar autolink http(s), como em qualquer texto)
  assert.equal(plano(md("[link](https://ex")), "[link](https://ex");
  assert.ok(!md("[link](https://ex")[0].c.some((n) => n.t === "a" && plano(n.c) === "link"));
  assert.deepEqual(md(fim).map((n) => n.t), ["p", "table", "ul", "pre"]);
});

// ---- correcções da revisão (C1, C4, S1)

test("C1: lista/título com 32 000 espaços e U+2028/U+2029 no fim em tempo linear (< 300 ms de CPU, nunca relógio)", () => {
  const N = 32000;
  for (const fim of [" ", " ", " x"]) for (const ini of ["- ", "1. ", "# ", "+\t"]) {
    const s = ini + " \t".repeat(N / 2) + fim, t0 = process.cpuUsage();
    const a = md(s);
    const u = process.cpuUsage(t0), ms = (u.user + u.system) / 1000;
    assert.ok(ms < 300, `${JSON.stringify(ini + fim)}: ${ms} ms`);
    assert.ok(Array.isArray(a) && a.length === 1);
  }
  // o U+2028 fica no texto do item (nada se perde)
  assert.equal(plano(md("- a b")), "a b");
});

test("C4: lista numerada partida por um bloco de código continua no número certo (start); start só inteiro no materializar", () => {
  const a = md("1. um\n2. dois\n```\nx\n```\n3. três\n4. quatro");
  assert.deepEqual(a.map((n) => n.t), ["ol", "pre", "ol"]);
  assert.equal(a[0].a, undefined, "a que começa em 1 não leva start");
  assert.deepEqual(a[2].a, { start: 3 });
  const f = materializar(a, docFalso), ols = [...f.todos()].filter((e) => e.tagName === "OL");
  assert.deepEqual(ols.map((e) => e.attrs), [{}, { start: "3" }]);
  // valores tortos na árvore não chegam ao atributo
  for (const start of ["3", 1.5, -1, NaN, `1" onclick="x`]) assert.deepEqual([...materializar([{ t: "ol", a: { start }, c: [] }], docFalso).todos()][0].attrs, {});
});

test("S1: link cujo texto tem forma de URL/domínio de outro host mostra o host real a seguir", () => {
  const p = (s) => md(s)[0].c;
  assert.equal(plano(p("[https://banco.pt/login](https://phish.example/x)")), "https://banco.pt/login (phish.example)");
  assert.equal(plano(p("ver [banco.pt](https://phish.example) já")), "ver banco.pt (phish.example) já");
  assert.equal(plano(p("[www.banco.pt/a](http://banco.pt.phish.example)")), "www.banco.pt/a (banco.pt.phish.example)");
  assert.equal(plano(p("[good\u3002com](https://evil.com)")), "good\u3002com (evil.com)"); // ponto ideográfico (ronda 2)
  // o host real fica fora do <a> e é texto
  const c = p("[banco.pt](https://phish.example)");
  assert.equal(c[0].t, "a"); assert.equal(c[1], " (phish.example)");
  // mesmo host, texto normal ou link perigoso: nada a acrescentar
  for (const s of ["[https://ex.pt/a](https://ex.pt/b)", "[ex.pt](https://EX.pt)", "[o site](https://phish.example)", "[banco.pt](javascript:x)",
    "[a b.pt](https://phish.example)"]) assert.ok(!/\(phish\.example\)|\(ex\.pt\)/.test(plano(md(s))), s);
});
