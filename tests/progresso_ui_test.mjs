// Barra de progresso do index.html (SPEC AC-24, AC-25): as funções prog* correm em node:vm com um localStorage falso.
// node --test tests/progresso_ui_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const HTML = readFileSync(new URL("../static/index.html", import.meta.url), "utf8");
const QJS = readFileSync(new URL("../static/quadro.js", import.meta.url), "utf8");
const QCSS = readFileSync(new URL("../static/quadro.css", import.meta.url), "utf8");
const FUNCS = ["progPref", "progDur", "progTexto", "progHtml"].map((n) => {
  const i = HTML.indexOf(`\nfunction ${n}(`);
  assert.ok(i >= 0, `falta function ${n}( na coluna 0`);
  return HTML.slice(i, HTML.indexOf("\n}\n", i) + 3);
}).join("");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function ctx(prefs = {}, lanca = false) {
  const localStorage = { getItem: (k) => { if (lanca) throw new Error("SecurityError"); return prefs[k] ?? null; } };
  return vm.runInNewContext(FUNCS + "; ({ progPref, progDur, progTexto, progHtml })", { localStorage, esc });
}
const P = (o) => ({ fases: null, decorrido_s: 1500, tipico_s: 3600, amostra: 12, pct: 41, faltam_s: 2100, atrasada: false, ...o });
const FASES = [["SPEC", "feito"], ["PLAN", "agora"], ["CODE", ""], ["QA", ""], ["PR", ""]].map(([nome, estado]) => ({ nome, estado }));

test("AC-24: os textos da SPEC §5", () => {
  const { progTexto } = ctx();
  assert.equal(progTexto(P(), true), "≈ 41 % · faltam ~35 min (estimativa)");
  assert.equal(progTexto(P({ faltam_s: 4800 }), true), "≈ 41 % · faltam ~1 h 20 min (estimativa)");
  assert.equal(progTexto(P({ pct: 95, faltam_s: 30 }), true), "≈ 95 % · faltam < 1 min (estimativa)");
  assert.equal(progTexto(P({ pct: null, faltam_s: null, atrasada: true, decorrido_s: 4000 }), true), "a demorar mais do que o costume (típico ~1 h)");
  assert.equal(progTexto(P({ tipico_s: null, pct: null, faltam_s: null, amostra: 3 }), true), "a correr há 25 min");
  assert.equal(progTexto(P(), false), "a correr há 25 min");
});

test("progDur: minutos arredondam para cima; ≥ 60 min em horas", () => {
  const { progDur } = ctx();
  assert.equal(progDur(0), "< 1 min");
  assert.equal(progDur(59), "< 1 min");
  assert.equal(progDur(60), "1 min");
  assert.equal(progDur(61), "2 min");
  assert.equal(progDur(2041), "35 min");
  assert.equal(progDur(3540), "59 min");
  assert.equal(progDur(3541), "1 h");
  assert.equal(progDur(3600), "1 h");
  assert.equal(progDur(4800), "1 h 20 min");
  assert.equal(progDur(7200), "2 h");
});

test("progHtml: fases da sala (feito/agora), barra com a % e o texto", () => {
  const h = ctx().progHtml(P({ fases: FASES }));
  assert.match(h, /<div class="steps"><span class="done">SPEC<\/span><span class="now">PLAN<\/span><span class="">CODE<\/span>/);
  assert.match(h, /<div class="prog-m"><i style="width:41%"><\/i><\/div>/);
  assert.match(h, /<div class="prog-t">≈ 41 % · faltam ~35 min \(estimativa\)<\/div>/);
});

test("progHtml: sem fases (squad de sessão), atrasada cheia a laranja, sem amostra só o texto", () => {
  const { progHtml } = ctx();
  assert.doesNotMatch(progHtml(P()), /steps/);
  assert.match(progHtml(P({ pct: null, faltam_s: null, atrasada: true, decorrido_s: 4000 })), /class="prog-m late"[^>]*><i style="width:100%">/);
  const sem = progHtml(P({ tipico_s: null, pct: null, faltam_s: null }));
  assert.doesNotMatch(sem, /prog-m/);
  assert.match(sem, /a correr há 25 min/);
  assert.equal(progHtml(null), "");
});

test("AC-24: progEstimativa = 0 só mostra o decorrido; progBarra = 0 não há barra", () => {
  const h = ctx({ progEstimativa: "0" }).progHtml(P({ fases: FASES }));
  assert.doesNotMatch(h, /prog-m|estimativa|%/);
  assert.match(h, /a correr há 25 min/);
  assert.match(h, /steps/);   // as fases não são estimativa
  assert.equal(ctx({ progBarra: "0" }).progHtml(P({ fases: FASES })), "");
  assert.equal(ctx({ progBarra: "1" }).progPref("progBarra"), true);
});

test("AC-24: localStorage que lança não parte (tudo ligado)", () => {
  const c = ctx({}, true);
  assert.equal(c.progPref("progBarra"), true);
  assert.match(c.progHtml(P()), /faltam ~35 min/);
});

test("progHtml escapa o nome das fases", () => {
  assert.match(ctx().progHtml(P({ fases: [{ nome: "<b>", estado: "x" }] })), /<span class="">&lt;b&gt;<\/span>/);
});

test("AC-25: a barra não tem animação nem transição (nada a anular com reduced-motion)", () => {
  const css = [...HTML.matchAll(/<style>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join("") + QCSS;
  const regras = [...css.matchAll(/([^{}]*\.prog[^{}]*)\{([^}]*)\}/g)];
  assert.ok(regras.length >= 4);
  for (const [, sel, d] of regras) assert.doesNotMatch(d, /animation|transition/, sel);
});

test("sala: a barra substitui os .steps quando há progress; o Quadro recebe-a pelo quadroApi", () => {
  assert.match(HTML, /\$\{progHtml\(t\.progress\) \|\| \(ROOMS\[sq\(t\)\]\.fases \? `<div class="steps">/);
  assert.match(HTML, /const quadroApi = \(\) => \(\{[^}]*barra: progHtml/);
  assert.match(QJS, /const barraH = \(c\) => \(c\.progress && A\.barra \? A\.barra\(c\.progress\) : ""\);/);
  assert.match(QJS, /\$\{barraH\(c\)\}/);
  assert.match(QJS, /export function repintar\(\)/);
});
