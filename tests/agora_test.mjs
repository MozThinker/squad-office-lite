// Faixa «Agora» (fase 2 de UX, porte do Office, CA-33): as puras do static/agora.js com tarefas feitas aqui, o reconciliar num
// DOM falso que conta as escritas, e a ligação no lateral.js/lateral.css/index.html. node --test tests/agora_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import "../static/act.js";
import { terminalTexto } from "../static/topo.js";
import { MAX, cabecalho, linhas, reconciliar } from "../static/agora.js";

const AGORA = Date.parse("2026-11-23T15:30:00Z"), min = (n) => new Date(AGORA - n * 6e4).toISOString();
const ler = (f) => readFileSync(new URL("../static/" + f, import.meta.url), "utf8");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const PAP = { "tech-lead": { name: "Maestro", short: "Tech lead" }, "backend-engineer": { name: "Bia", short: "Backend" },
  "frontend-engineer": { name: "Fred", short: "Frontend" }, general: { name: "Geral", short: "Ajuda" } };
const SEATS = new Set(Object.keys(PAP));
// a mesma regra do acaoDe do lateral.js (terminal com escalões)
const acao = (n, agora) => { const tt = n.act.desde ? terminalTexto(n.act.desde, agora) : null;
  return tt ? { acao: "💻 " + tt.texto, preso: tt.preso } : { acao: ACTIVIDADE.actText(n.act), preso: false }; };
const ctx = (agora = AGORA) => ({ esc, papel: (r) => PAP[r] || PAP.general, roleOf: (r) => (SEATS.has(r) ? r : "general"),
  lider: "tech-lead", coord: "a coordenar a squad", acao: (n) => acao(n, agora) });
const tarefa = () => ({ id: "t1", running: true, status: "a correr", titulo: "Loja nova", project: "loja", started: min(95),
  working: ["tech-lead"], lead: { role: "tech-lead", act: { kind: "ler", alvo: "SPEC.md" } }, gh: { ci: "vermelho", at: min(3) },
  agents: [{ role: "backend-engineer", desc: "API das encomendas", active: true, stuck: true, act: null, start: min(30) },
    { role: "frontend-engineer", desc: "ecrã do carrinho", active: false, stuck: false, act: null, start: min(20) }] });

function falsoUl() {
  const n = { escritas: 0 };
  const ul = { children: [], insertBefore(li, ref) {
    const c = this.children, i = c.indexOf(li);
    if (i >= 0) c.splice(i, 1);
    const j = ref ? c.indexOf(ref) : -1;
    j < 0 ? c.push(li) : c.splice(j, 0, li);
    return li;
  } };
  globalThis.document = { createElement() {
    let h = "";
    const li = { dataset: {}, hidden: false, className: "", remove() { ul.children.splice(ul.children.indexOf(li), 1); } };
    Object.defineProperty(li, "innerHTML", { get: () => h, set: (v) => { h = v; n.escritas++; } });
    return li;
  } };
  return { ul, n };
}

test("CA-33: CI vermelho na 1.ª, uma li por activo, presos antes dos outros, botões com data-role", () => {
  const L = linhas(tarefa(), ctx()), ags = L.filter((l) => l.k !== "ci");
  assert.equal(L[0].k, "ci");
  assert.match(L[0].html, /data-estado="erro"/);
  assert.deepEqual(ags.map((l) => l.k), ["backend-engineer", "tech-lead"]);
  assert.match(ags[0].html, /data-estado="a-espera"/);
  assert.match(ags[0].html, /<span class="tag">preso\?<\/span>/);
  assert.match(ags[1].html, /data-estado="a-trabalhar"/);
  assert.match(ags[1].html, /<span class="ag-p">Tech lead<\/span>/);
  for (const l of ags) assert.match(l.html, /^<button type="button" data-role="[^"]+">/);
});

test("CA-33: terminal preso (sem stuck) dá a-espera; o tempo conta desde o act.desde", () => {
  const t = tarefa();
  t.gh.ci = null;
  t.lead.act = { kind: "terminal", desde: min(40) };
  const l = linhas(t, ctx()).find((x) => x.k === "tech-lead");
  assert.match(l.html, /data-estado="a-espera"/);
  assert.match(l.html, new RegExp(`data-t0="${Date.parse(min(40))}"`));
});

test("CA-33: sem activos, uma só li .vazio com o estado da tarefa ou «sem trabalho»", () => {
  assert.deepEqual(linhas({ ...tarefa(), running: false, status: "concluída", gh: {} }, ctx()), [{ k: "vazio", cls: "vazio", html: "concluída" }]);
  assert.deepEqual(linhas(undefined, ctx()), [{ k: "vazio", cls: "vazio", html: "sem trabalho" }]);
});

test("CA-33: cabeçalho com e sem «há», escapado; linhas sem aria-live e tempo aria-hidden", () => {
  const t = tarefa();
  assert.equal(cabecalho("Sala <Dev>", t, AGORA), "Agora · Sala &lt;Dev&gt; · Loja nova · há 1 h 35");
  assert.equal(cabecalho("Dev", { ...t, running: false }, AGORA), "Agora · Dev · Loja nova");
  assert.equal(cabecalho("Dev", undefined, AGORA), "Agora · Dev");
  for (const l of linhas(t, ctx())) {
    assert.ok(!l.html.includes("aria-live"), l.html);
    if (l.k !== "ci") assert.match(l.html, /<span class="estado-t" aria-hidden="true"><\/span>/);
  }
});

test("CA-33: 6 activos → 4 visíveis e 2 escondidas; chaves únicas para papéis repetidos", () => {
  const t = tarefa();
  t.gh.ci = null; t.working = [];
  t.agents = Array.from({ length: 6 }, (_, i) => ({ role: ["backend-engineer", "frontend-engineer", "Explore", "Plan", "x", "y"][i],
    desc: "d" + i, active: true, stuck: false, act: null, start: min(i) }));
  const L = linhas(t, ctx()), { ul } = falsoUl();
  assert.equal(L.length, 6);
  assert.equal(new Set(L.map((l) => l.k)).size, 6);
  assert.equal(reconciliar(ul, L, MAX), 2);
  assert.deepEqual(ul.children.map((li) => li.hidden), [false, false, false, false, true, true]);
  assert.equal(reconciliar(ul, L), 0);
});

test("CA-33: o mesmo estado 0 escritas, uma acção muda 1, o tempo a avançar 0, a que sai é removida", () => {
  const t = tarefa(), { ul, n } = falsoUl();
  reconciliar(ul, linhas(t, ctx()), MAX);
  n.escritas = 0;
  reconciliar(ul, linhas(t, ctx()), MAX);
  assert.equal(n.escritas, 0);
  t.agents[0].desc = "outra coisa";
  reconciliar(ul, linhas(t, ctx()), MAX);
  assert.equal(n.escritas, 1);
  n.escritas = 0;
  reconciliar(ul, linhas(t, ctx(AGORA + 20e3)), MAX);
  assert.equal(n.escritas, 0);
  t.agents[0].active = t.agents[0].stuck = false;
  reconciliar(ul, linhas(t, ctx()), MAX);
  assert.ok(!ul.children.some((li) => li.dataset.k === "backend-engineer"));
});

test("CA-33: ligação no lateral.js, no index.html e no lateral.css (só a ≤ 900 px, linhas de 44 px)", () => {
  const L = ler("lateral.js"), H = ler("index.html"), C = ler("lateral.css");
  assert.match(L, /import \{ MAX, cabecalho, linhas, reconciliar \} from "\.\/agora\.js";/);
  assert.match(L, /export function render\(s, t\) \{[^}]*pintarAgora\(t\);/);
  assert.match(L, /\$\("#office"\)\.insertAdjacentHTML\("afterend", `<section id="agora" aria-labelledby="agora-h">/);
  assert.match(L, /acao: \(x, r = acaoDe\(x, a\)\) => r\.tt \? \{ \.\.\.r, acao: "💻 terminal" \} : r/); // sem minutos: o relógio não reescreve
  assert.match(H, /lat\?\.render\(state, ti\);/);
  assert.match(C, /\n#agora \{ display: none; \}\n@media \(max-width: 900px\) \{\n  #agora \{ display: block;/);
  assert.match(C, /#agora button \{ width: 100%; min-height: 44px;/);
  assert.doesNotMatch(ler("agora.js"), /\bwindow\./);
});
