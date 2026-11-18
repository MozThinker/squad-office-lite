// v3 (SPEC lite-v3): lista lateral (lateral.js), Fábrica (fabrica.js), «Ver tabela» (tabelas.js) e Entregas (entregas.js).
// node --test tests/v3_lateral_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import "../static/act.js";

const STATIC = new URL("../static/", import.meta.url);
const ler = (f) => readFileSync(new URL(f, STATIC), "utf8");
const L = await import("../static/lateral.js");
const F = await import("../static/fabrica.js");
const TB = await import("../static/tabelas.js");
const E = await import("../static/entregas.js");

const AGORA = Date.parse("2026-11-17T12:00:00Z"), min = (n) => new Date(AGORA - n * 60000).toISOString();
const tarefa = (o) => ({ running: true, agents: [], working: [], ...o });

test("CA-25/CA-20: estadoAgente é a regra única (preso, a trabalhar, terminal, terminal lento, parado)", () => {
  const t = tarefa({ working: ["a"] });
  assert.equal(L.estadoAgente(t, "a", { act: { kind: "editar" } }, AGORA), "trabalhar");
  assert.equal(L.estadoAgente(t, "b", { act: null }, AGORA), "parado");
  assert.equal(L.estadoAgente(t, "b", { stuck: true }, AGORA), "preso");
  assert.equal(L.estadoAgente(t, "b", { act: { kind: "terminal", desde: min(5) } }, AGORA), "terminal");
  assert.equal(L.estadoAgente(t, "b", { act: { kind: "terminal", desde: min(12) } }, AGORA), "terminal-lento");
  assert.equal(L.estadoAgente(t, "b", { act: { kind: "terminal", desde: min(26) } }, AGORA), "preso");
  // com terminal pendente nunca «parado» (não há «z»)
  assert.notEqual(L.estadoAgente(t, "b", { act: { kind: "terminal", desde: min(2) } }, AGORA), "parado");
});

test("CA-25: agentesDe junta as tarefas a correr de todas as squads, por projecto (sem projecto no fim)", () => {
  const s = { tasks: [
    tarefa({ id: "1", squad: "b", project: "menuqr", lead: { role: "cmo", act: null }, agents: [{ role: "general", finished: false }, { role: "x", finished: true }] }),
    tarefa({ id: "2", squad: "a", project: "Loja", lead: { role: "tech-lead", act: { kind: "ler" } } }),
    tarefa({ id: "3", squad: "a", project: "menuqr", lead: { role: "tech-lead" } }),
    tarefa({ id: "4", squad: "a", project: "", lead: { role: "tech-lead" } }),
    tarefa({ id: "5", squad: "a", project: "Loja", running: false, lead: { role: "tech-lead" } }),
  ] };
  const g = L.agentesDe(s, AGORA, ["a", "b"]);
  assert.deepEqual(g.map((x) => x.project), ["Loja", "menuqr", ""]);
  assert.deepEqual(g[1].agentes.map((a) => `${a.squad}/${a.taskId}/${a.role}`), ["a/3/tech-lead", "b/1/cmo", "b/1/general"]);
  assert.equal(g[0].agentes[0].acao, "📖 a ler");
  assert.deepEqual(L.agentesDe(null, AGORA, ["a"]), []);
});

test("CA-26: a pesquisa ignora acentos e maiúsculas (papel, projecto, acção)", () => {
  const g = [{ project: "Gestão", agentes: [{ role: "tech-lead", squad: "a", project: "Gestão", acao: "✏️ a editar", taskId: "1" },
    { role: "cmo", squad: "b", project: "Gestão", acao: "📖 a ler", taskId: "2" }] }];
  const nome = (r) => ({ "tech-lead": { name: "Maestro", short: "Tech lead" }, cmo: { name: "Capitã", short: "CMO" } })[r];
  assert.equal(L.filtraAgentes(g, "GESTAO", nome)[0].agentes.length, 2);
  assert.deepEqual(L.filtraAgentes(g, "capita", nome)[0].agentes.map((a) => a.role), ["cmo"]);
  assert.deepEqual(L.filtraAgentes(g, "EDITAR", nome)[0].agentes.map((a) => a.role), ["tech-lead"]);
  assert.deepEqual(L.filtraAgentes(g, "nada", nome), []);
  assert.equal(L.filtraAgentes(g, "  ", nome), g);
});

test("CA-26: «/» só no Escritório, fora de campos de texto e sem diálogo", () => {
  const e = { key: "/" };
  assert.ok(L.barraValida(e, { tagName: "BODY" }, false, ""));
  assert.equal(L.barraValida(e, null, false, "quadro"), false);
  assert.equal(L.barraValida(e, null, false, "biblioteca"), false);
  assert.equal(L.barraValida(e, { tagName: "INPUT" }, false, ""), false);
  assert.equal(L.barraValida(e, { isContentEditable: true }, false, ""), false);
  assert.equal(L.barraValida(e, null, true, ""), false);
  assert.equal(L.barraValida({ key: "/", ctrlKey: true }, null, false, ""), false);
  assert.equal(L.barraValida({ key: "p" }, null, false, ""), false);
});

test("CA-24: arranca sempre em «Tarefa» (nada guardado decide o separador) e o poll não muda de separador", () => {
  const s = ler("lateral.js");
  assert.match(s, /separador\("tarefa"\); \/\/ arranca sempre em «Tarefa» \(CA-24\)/);
  assert.doesNotMatch(s, /infoSep/);
  const render = s.slice(s.indexOf("export function render("), s.indexOf("\n}\n", s.indexOf("export function render(")));
  assert.doesNotMatch(render, /separador\(/);
});

test("CA-45/46: «4/7», linhas («≈» se estimadas), modelo e ramo; «—» sem dados", () => {
  assert.deepEqual(L.progressoTexto({ feitas: 4, total: 7 }), { texto: "4/7", aria: "4 de 7 tarefas feitas" });
  for (const x of [null, {}, { feitas: 8, total: 7 }, { feitas: 1, total: 0 }, { feitas: "1", total: 2 }]) assert.equal(L.progressoTexto(x).texto, "—");
  assert.deepEqual(L.extrasTexto({ linhas: { mais: 12, menos: 3, aprox: true }, modelo: "m<1>", ramo: "feat/x" }),
    { linhas: "≈ +12 −3", modelo: "m&lt;1&gt;", ramo: "feat/x", ramoCompleto: "feat/x" });
  assert.deepEqual(L.extrasTexto(null), { linhas: "—", modelo: "—", ramo: "—", ramoCompleto: "—" });
  assert.equal(L.extLinha({ linhas: { mais: 1, menos: 0, aprox: false }, modelo: null, ramo: "r" }), "linhas +1 −0 · r");
  assert.equal(L.extLinha({}), "");
  assert.match(L.extLinha({ ramo: "x".repeat(40) }), /^<span title="x{40}">x{31}…<\/span>$/);
});

test("CA-35: por ler = terminadas há ≤ 24 h, sem dry-run, fora das lidas; lidas no máximo 200", () => {
  const ts = [{ id: "a", squad: "s", finished: min(60) }, { id: "b", squad: "s", finished: min(60 * 25) }, { id: "c", squad: "s", finished: min(5), mode: "dry-run" },
    { id: "d", squad: "s", finished: min(5), running: true }, { id: "e", squad: "t", finished: min(10) }];
  assert.deepEqual(L.porLer(ts, new Set(), AGORA), ["s/a", "t/e"]);
  assert.deepEqual(L.porLer(ts, new Set(["t/e"]), AGORA), ["s/a"]);
  assert.deepEqual([...L.lerLidas('["a", 1, "b"]')], ["a", "b"]);
  assert.deepEqual([...L.lerLidas("{x")], []);
  assert.deepEqual([...L.lerLidas(null)], []);
  const muitas = Array.from({ length: 250 }, (_, i) => "k" + i);
  assert.equal(L.lerLidas(JSON.stringify(muitas)).size, 200);
  assert.deepEqual(L.limpaLidas(new Set(muitas)).slice(0, 1), ["k50"]);
});

// ---- Fábrica
test("CA-28: linha da Actividade «hora · agente · projecto · acção» no fuso da config", () => {
  const s = { tasks: [{ id: "t", project: "loja" }] };
  assert.deepEqual(F.linhaFeed({ at: "2026-11-17T09:05:00Z", task: "t", role: "cmo", kind: "editar" }, s, (e) => "Capitã", "Africa/Maputo"),
    { hora: "11:05", agente: "Capitã", projecto: "loja", acao: "✏️ a editar", erro: false });
  const x = F.linhaFeed({ at: "2026-11-17T09:05:00Z", task: "z", role: "r", kind: "ci_vermelho" }, s, undefined, "Europe/Lisbon");
  assert.deepEqual([x.hora, x.agente, x.projecto, x.erro], ["09:05", "r", "z", true]);
  assert.equal(F.linhaFeed({ at: "2026-11-17T09:05:00Z", task: "t", kind: "ler", error: true }, s).erro, true);
});

test("CA-29: minutos a trabalhar = união dos intervalos (sobreposições), certo nos dias de 23 e 25 h", () => {
  const ini = "2026-11-17T00:00:00Z", tz = "Europe/Lisbon";
  const iv = [{ ini: "2026-11-17T08:00:00Z", fim: "2026-11-17T09:00:00Z" }, { ini: "2026-11-17T08:30:00Z", fim: "2026-11-17T10:00:00Z" },
    { ini: "2026-11-17T11:00:00Z", fim: null }];
  assert.equal(F.minutosTrabalho(iv, ini, "2026-11-17T11:30:00Z", tz), 150);
  const h = F.trabalhoPorHora(iv, ini, "2026-11-17T11:30:00Z", tz);
  assert.deepEqual(h.slice(7, 13), [0, 60, 60, 0, 30, 0]);
  // 25 de Outubro de 2026 (Lisboa): 25 h; 29 de Março: 23 h
  assert.equal(F.horasDoDia("2026-10-24T23:00:00Z", tz).length, 25);
  assert.equal(F.horasDoDia("2026-03-29T00:00:00Z", tz).length, 23);
  const todo = [{ ini: "2026-10-24T23:00:00Z", fim: null }];
  assert.equal(F.minutosTrabalho(todo, "2026-10-24T23:00:00Z", "2026-10-26T00:00:00Z", tz), 25 * 60);
  assert.equal(F.trabalhoPorHora(todo, "2026-10-24T23:00:00Z", "2026-10-26T00:00:00Z", tz)[1], 60); // a hora repetida não passa de 60
  assert.equal(F.minutosTrabalho([{ ini: "2026-03-29T00:00:00Z", fim: null }], "2026-03-29T00:00:00Z", "2026-03-30T00:00:00Z", tz), 23 * 60);
  assert.equal(F.minutosTrabalho([], ini, "2026-11-17T11:30:00Z", tz), 0);
});

test("CA-29: à tua espera desde o «desde» mais antigo (aprovações desde as 00:00), por hora só os abertos", () => {
  const ini = "2026-11-17T00:00:00Z", ag = "2026-11-17T10:00:00Z";
  const it = [{ desde: "2026-11-16T00:00:00Z" }, { desde: "2026-11-17T08:30:00Z" }, { desde: null }];
  assert.equal(F.minutosEspera(it, ini, ag), 600);   // de ontem: conta desde o início de hoje
  assert.equal(F.minutosEspera([{ desde: "2026-11-17T09:00:00Z" }], ini, ag), 60);
  assert.equal(F.minutosEspera([], ini, ag), 0);
  const e = F.esperaPorHora(it, ini, ag, "UTC");
  assert.deepEqual([e[0], e[7], e[8], e[10], e[11]], [1, 1, 2, 2, 0]);
  assert.equal(F.dur(59), "59 min");
  assert.equal(F.dur(125), "2 h 5 min");
});

test("CA-30: «Ver tabela» com os mesmos números das barras", () => {
  const v = Array(24).fill(0); v[9] = 45; v[10] = 60;
  const d = F.linhas24(v), h = TB.tabela("trabalho", d);
  assert.equal(d.linhas.length, 24);
  assert.deepEqual(d.linhas[9], ["09:00", "45"]);
  assert.match(h, /<caption>Trabalho por hora \(minutos\)<\/caption>/);
  assert.match(h, /<th scope="row">10:00<\/th><td>60<\/td>/);
  assert.match(TB.tabela("custo", { cab: ["dia", "<b>"], linhas: [["01/11", "1"]] }), /<th scope="col">&lt;b&gt;<\/th>/);
});

// ---- Entregas (só a pedido)
test("CA-35/36: /api/entregas só no entregas.js (ao abrir); resumo por textContent, nunca por innerHTML", () => {
  for (const f of readdirSync(STATIC).filter((x) => /\.(js|html)$/.test(x))) {
    const s = ler(f);
    if (f !== "entregas.js" && f !== "demo.js") assert.doesNotMatch(s, /api\/entregas/, f);
  }
  const s = ler("entregas.js");
  assert.equal((s.match(/fetch\(/g) || []).length, 1);
  const abrir = s.slice(s.indexOf("export function abrir("), s.indexOf("function item("));
  assert.match(abrir, /carregar\(\);/);
  assert.match(s, /p\.textContent = x\.it\.resumo;/);
  assert.doesNotMatch(s, /innerHTML\s*=[^;\n]*\.resumo|\$\{\s*(?:esc\()?[\w.]*\.resumo\s*\)?\s*\}/);   // nunca o valor num template HTML
  assert.match(ler("lateral.js"), /\(entP \|\|= import\("\.\/entregas\.js"\)\)/);
  assert.equal(E.rotaBib("tarefas/x y/SPEC.md"), "#biblioteca/tarefas/x%20y/SPEC.md");
  assert.equal(E.nomeBib("tarefas/1/PLAN.md"), "PLAN.md");
});
