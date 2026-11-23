// Peça de estado: static/estado.js (node --test tests/estado_test.mjs).
// O import em si prova que o módulo não toca em window nem document ao carregar.
import test from "node:test";
import assert from "node:assert/strict";
import { statSync } from "node:fs";
import { ESTADOS, fmtTempo, cadencia, tempo, html, pintar, ligar } from "../static/estado.js";

const hhmm = (t) => { const d = new Date(t); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };

test("CA1: estado.js + estado.css ≤ 4096 bytes (Lite: + os tokens de movimento e o desligar do CA-32)", () => {
  const n = ["estado.js", "estado.css"].reduce((s, f) => s + statSync(new URL(`../static/${f}`, import.meta.url)).size, 0);
  assert.ok(n <= 4096, `${n} bytes`);
});

test("ESTADOS: os 6 da §7.1, pela ordem", () => {
  assert.deepEqual(ESTADOS, ["a-trabalhar", "a-processar", "a-espera", "concluido", "erro", "desligado"]);
});

test("fmtTempo: <3 s vazio, s, min, h mm, dias", () => {
  for (const s of [0, 2, 2.99, -5, NaN, Infinity, undefined, null]) assert.equal(fmtTempo(s), "", String(s));
  assert.equal(fmtTempo(3), "3 s");
  assert.equal(fmtTempo(12), "12 s");
  assert.equal(fmtTempo(59.9), "59 s");
  assert.equal(fmtTempo(250), "4 min");
  assert.equal(fmtTempo(3599), "59 min");
  assert.equal(fmtTempo(7500), "2 h 05");
  assert.equal(fmtTempo(3600), "1 h 00");
  assert.equal(fmtTempo(86399), "23 h 59");
  assert.equal(fmtTempo(90000), "1 d");
  assert.equal(fmtTempo(3 * 86400 + 5), "3 d");
});

test("cadencia: 1 s abaixo de 1 min, 30 s depois", () => {
  assert.equal(cadencia(0), 1000);
  assert.equal(cadencia(59), 1000);
  assert.equal(cadencia(60), 30000);
  assert.equal(cadencia(7500), 30000);
});

test("tempo: «há» na espera, hh:mm no fim, nada desligado ou sem t0", () => {
  const agora = Date.UTC(2026, 10, 22, 20, 0, 0), t0 = agora - 190e3;
  assert.equal(tempo("a-espera", t0, agora), "há 3 min");
  assert.equal(tempo("a-espera", agora - 1000, agora), "");
  assert.equal(tempo("a-trabalhar", agora - 42e3, agora), "42 s");
  assert.equal(tempo("a-processar", t0, agora), "3 min");
  assert.equal(tempo("concluido", t0, agora), hhmm(t0));
  assert.equal(tempo("erro", t0, agora), hhmm(t0));
  assert.match(tempo("concluido", t0, agora), /^\d\d:\d\d$/);
  assert.equal(tempo("desligado", t0, agora), "");
  for (const x of [undefined, null, NaN, Infinity, "123"]) assert.equal(tempo("a-trabalhar", x, agora), "", String(x));
  assert.equal(tempo("a-trabalhar", Date.now() - 5000), "5 s");
});

test("html: peça com sinal, texto escapado, tempo aria-hidden e aria-live", () => {
  const agora = 1_800_000_000_000;
  const h = html("a-espera", "<b>à tua \"espera\" & 'já'</b>", agora - 190e3, agora);
  assert.equal(h, `<span class="estado" data-estado="a-espera" aria-live="polite" data-t0="${agora - 190e3}">` +
    `<span class="estado-x">&lt;b&gt;à tua &quot;espera&quot; &amp; &#39;já&#39;&lt;/b&gt;</span>` +
    `<span class="estado-t" aria-hidden="true">há 3 min</span></span>`);
  assert.ok(!h.includes("<b>"));
  for (const e of ESTADOS) assert.match(html(e, "x"), new RegExp(`^<span class="estado" data-estado="${e}" aria-live="polite"><span`));
  assert.ok(html("desligado", "parado").includes('<span class="estado-t" aria-hidden="true"></span>'));
});

test("html: estado desconhecido cai em texto simples (sem data-estado)", () => {
  for (const e of ["a_trabalhar", "", undefined, "x\" onclick=\"1"]) {
    const h = html(e, "<i>arquivada</i>", 1);
    assert.equal(h, "&lt;i&gt;arquivada&lt;/i&gt;");
    assert.ok(!h.includes("data-estado"));
  }
  assert.equal(html("x", undefined), "");
});

// DOM mínimo para o pintar: um elemento com a peça (filhos .estado-x e .estado-t)
function falso() {
  const no = (cls) => ({ textContent: "", cls });
  const el = {
    _p: null,
    set innerHTML(v) {
      this._h = v;
      const m = /data-estado="([^"]+)"(?: aria-live="polite")?(?: data-t0="([^"]+)")?><span class="estado-x">([^<]*)<\/span><span class="estado-t" aria-hidden="true">([^<]*)/.exec(v);
      if (!m) { this._p = null; return; }
      const x = no("estado-x"), t = no("estado-t"); x.textContent = m[3]; t.textContent = m[4];
      const cl = new Set();
      this._p = { dataset: { estado: m[1], ...(m[2] ? { t0: m[2] } : {}) }, firstChild: x, lastChild: t, offsetWidth: 1,
        classList: { add: (c) => cl.add(c), remove: (c) => cl.delete(c), has: (c) => cl.has(c) } };
    },
    get innerHTML() { return this._h; },
    querySelector(q) { assert.equal(q, ".estado"); return this._p; },
  };
  return el;
}

test("pintar: 1.ª vez desenha; depois só muda estado, texto e t0 (no mesmo nó)", () => {
  const el = falso();
  pintar(el, "a-trabalhar", "a pensar", Date.now() - 12e3);
  const p = el._p, x = p.firstChild;
  assert.equal(p.dataset.estado, "a-trabalhar");
  assert.equal(x.textContent, "a pensar");
  assert.equal(p.lastChild.textContent, "12 s");
  assert.ok(!p.classList.has("est-novo"));
  // mesmo estado e texto: nada muda (o aria-live não volta a anunciar)
  let escritas = 0; let v = x.textContent;
  Object.defineProperty(x, "textContent", { get: () => v, set: (n) => { escritas++; v = n; } });
  pintar(el, "a-trabalhar", "a pensar", Date.now() - 13e3);
  assert.equal(escritas, 0);
  assert.equal(el._p, p);
  assert.ok(!p.classList.has("est-novo"));
  // muda o estado: marca est-novo, troca o texto, tira o t0
  pintar(el, "concluido", "pronto");
  assert.equal(el._p, p);
  assert.equal(p.dataset.estado, "concluido");
  assert.ok(p.classList.has("est-novo"));
  assert.equal(v, "pronto");
  assert.equal(escritas, 1);
  assert.equal("t0" in p.dataset, false);
  assert.equal(p.lastChild.textContent, "");
  // estado desconhecido: texto simples
  pintar(el, "arquivada", "<arquivada>");
  assert.equal(el.innerHTML, "&lt;arquivada&gt;");
  assert.equal(el._p, null);
});

test("ligar: sem document (node) não faz nada", () => {
  assert.equal(globalThis.document, undefined);
  assert.doesNotThrow(() => ligar());
});

test("ligar: um só temporizador, cadência mais curta das que contam; pára escondido e sem nenhuma", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_800_000_000_000 });
  const peca = (estado, t0) => ({ dataset: { estado, t0: String(t0) }, lastChild: { textContent: "?" } });
  const agora = Date.now();
  const a = peca("a-trabalhar", agora - 120e3), b = peca("a-espera", agora - 10e3), c = peca("concluido", agora - 5e3);
  let lista = [a, b, c], ouvintes = 0, vis;
  const doc = { hidden: false, querySelectorAll: (q) => (assert.equal(q, ".estado[data-t0]"), lista),
    addEventListener: (ev, f) => { assert.equal(ev, "visibilitychange"); ouvintes++; vis = f; } };
  globalThis.document = doc; t.after(() => delete globalThis.document);
  ligar();
  assert.equal(a.lastChild.textContent, "2 min");
  assert.equal(b.lastChild.textContent, "há 10 s");
  assert.equal(c.lastChild.textContent, hhmm(agora - 5e3));
  ligar(); // de novo: continua um só ouvinte e um só temporizador
  assert.equal(ouvintes, 1);
  t.mock.timers.tick(1000);
  assert.equal(b.lastChild.textContent, "há 11 s");
  // a-espera sai: só a de 2 min conta, passa a 30 s
  lista = [a, c];
  t.mock.timers.tick(1000);
  b.lastChild.textContent = "x";
  a.lastChild.textContent = "?";
  t.mock.timers.tick(29000);
  assert.equal(a.lastChild.textContent, "?");
  t.mock.timers.tick(1000);
  assert.equal(a.lastChild.textContent, "2 min");
  // escondido: pára; visível de novo: volta a escrever
  doc.hidden = true;
  a.lastChild.textContent = "?";
  t.mock.timers.tick(60000);
  assert.equal(a.lastChild.textContent, "?");
  doc.hidden = false; vis();
  assert.equal(a.lastChild.textContent, "3 min");
  // só concluídas: escreve uma vez e não agenda
  lista = [c]; ligar(); a.lastChild.textContent = "?"; c.lastChild.textContent = "?";
  t.mock.timers.tick(120000);
  assert.equal(c.lastChild.textContent, "?");
});
