// v3 (SPEC lite-v3): dia e noite (ambiente.js), replay (replay.js) e modo demonstração (demo.js). node --test tests/v3_ambiente_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const ler = (f) => readFileSync(new URL("../static/" + f, import.meta.url), "utf8");
const HTML = ler("index.html");
const AM = await import("../static/ambiente.js");
const RP = await import("../static/replay.js");
const D = await import("../static/demo.js");
const CHAVES = JSON.parse(readFileSync(new URL("./estado_chaves.json", import.meta.url), "utf8"));
const hm = (h, m) => h * 60 + m;

test("CA-37/D-6: faseDoDia nos limites (dia 06:00–18:30, crepúsculo ± 30 min)", () => {
  const casos = [[hm(5, 29), "noite"], [hm(5, 30), "crepusculo"], [hm(6, 0), "crepusculo"], [hm(6, 30), "crepusculo"], [hm(6, 31), "dia"],
    [hm(12, 0), "dia"], [hm(17, 59), "dia"], [hm(18, 0), "crepusculo"], [hm(18, 59), "crepusculo"], [hm(19, 0), "crepusculo"], [hm(19, 1), "noite"],
    [0, "noite"], [hm(23, 59), "noite"]];
  for (const [m, f] of casos) assert.equal(AM.faseDoDia(m), f, `${Math.floor(m / 60)}:${m % 60}`);
});

test("CA-37/CA-39: a hora vem do fuso da config; «Dia e noite» desligado = sempre dia", () => {
  const t = Date.parse("2026-11-17T21:00:00Z");
  assert.equal(AM.minutosNoFuso(t, "Africa/Maputo"), hm(23, 0));
  assert.equal(AM.minutosNoFuso(t, "America/Sao_Paulo"), hm(18, 0));
  assert.equal(AM.fase(t, "Africa/Maputo"), "noite");
  assert.equal(AM.fase(t, "America/Sao_Paulo"), "crepusculo");
  assert.equal(AM.fase(t, "Africa/Maputo", false), "dia");
  assert.equal(AM.sufixo(t, "Europe/Lisbon"), " · noite");
  assert.equal(AM.ligado({ getItem: () => "0" }), false);
  assert.equal(AM.ligado({ getItem: () => null }), true);
  assert.equal(AM.ligado({ getItem: () => { throw new Error("x"); } }), true);
  assert.equal(AM.minutosNoFuso(t, "Fuso/Inexistente"), new Date(t).getHours() * 60 + new Date(t).getMinutes());
});

test("CA-38: a camada da noite vai por baixo do quadro, das secretárias, dos balões, do «preso?» e do CI", () => {
  const room = HTML.slice(HTML.indexOf("function room(task) {"), HTML.indexOf("\n}\n", HTML.indexOf("function room(task) {")));
  const i = room.indexOf("amb.fundo(");
  assert.ok(i > 0);
  for (const depois of ["// quadro:", "desk(role", "bubble(", "stuckTag(", "ciLight()"]) assert.ok(room.indexOf(depois) > i, depois);
  const f = AM.fundo(258);
  assert.match(f, /^<g class="amb" aria-hidden="true"><rect class="amb-e" width="320" height="258" fill="#050a24"\/>/);
  assert.doesNotMatch(f, /var\(--/);   // cores fixas: o desenho é igual nos 5 temas
  assert.match(ler("office.css"), /@media \(prefers-reduced-motion: reduce\) \{ #office\[data-amb-ok\] \{ transition: none; \} \}/);
});

// ---- replay
const Q = { v: 1, dia: "2026-11-17", inicio: 1000, fim: 5000, truncado: false, quadros: [
  { t: 1000, tipo: "completo", tarefas: [{ id: "a", squad: "s", status: "a correr" }, { id: "b", squad: "s", status: "a correr" }] },
  { t: 1100, tipo: "dif", mudou: [{ id: "a", squad: "s", status: "concluída" }], saiu: [] },
  { t: 1200, tipo: "dif", mudou: [{ id: "c", squad: "t", status: "a correr" }], saiu: ["s/b"] },
  { t: 1300, tipo: "completo", tarefas: [{ id: "z", squad: "s", status: "a correr" }] }] };

test("CA-43: avancar à velocidade escolhida e pára no fim", () => {
  assert.deepEqual(RP.avancar(1000, 180, 1000, 5000), { t: 1180, fim: false });
  assert.deepEqual(RP.avancar(1000, 600, 250, 5000), { t: 1150, fim: false });
  assert.deepEqual(RP.avancar(4990, 60, 1000, 5000), { t: 5000, fim: true });
});

test("CA-40/43: estadoEm = último completo + difs até t", () => {
  const ids = (t) => RP.estadoEm(Q, t).map((x) => `${x.id}:${x.status}`);
  assert.deepEqual(ids(999), []);
  assert.deepEqual(ids(1000), ["a:a correr", "b:a correr"]);
  assert.deepEqual(ids(1150), ["a:concluída", "b:a correr"]);
  assert.deepEqual(ids(1250), ["a:concluída", "c:a correr"]);
  assert.deepEqual(ids(9999), ["z:a correr"]);
  assert.deepEqual(RP.estadoEm(null, 1), []);
});

test("CA-42/43: paraEstado dá a forma do /api/state sem títulos nem descrições", () => {
  const [x] = RP.paraEstado([{ id: "a", squad: "s", project: "p", mode: "goal", status: "a correr", running: true, phases: { SPEC: true, PLAN: false, QA: false },
    ci: "vermelho", lead: { role: "tech-lead", act: { kind: "ler" }, stuck: false }, agents: [{ role: "x", active: true, finished: false, stuck: false, act: null }] }], 1000);
  assert.equal(x.titulo, "p · goal");
  assert.deepEqual(x.working, ["x"]);
  assert.equal(x.gh.ci, "vermelho");
  assert.equal(x.agents[0].desc, "");
  assert.equal(x.lead.tarefas, null);
});

test("CA-40: diasReplay = hoje e os 6 dias anteriores no fuso da config", () => {
  const agora = Date.parse("2026-11-17T23:30:00Z");   // já dia 18 em Maputo, ainda 17 em Lisboa
  const m = RP.diasReplay(agora, "Africa/Maputo"), l = RP.diasReplay(agora, "Europe/Lisbon");
  assert.equal(m.length, 7);
  assert.deepEqual(m.map((x) => x.dia), ["2026-11-18", "2026-11-17", "2026-11-16", "2026-11-15", "2026-11-14", "2026-11-13", "2026-11-12"]);
  assert.deepEqual([m[0].rotulo, m[1].rotulo], ["hoje", "ter 17/11"]);
  assert.equal(l[0].dia, "2026-11-17");
  assert.equal(RP.hhmm(Date.parse("2026-11-17T08:05:00Z") / 1000, "Africa/Maputo"), "10:05");
});

test("CA-43: replay só a pedido, com setHtml no desenho e o #info a dizer que está ao vivo", () => {
  assert.match(HTML, /\$\("#rp-b"\)\.onclick = \(\) => \(rpP \|\|= import\("\.\/replay\.js"\)\)/);
  assert.match(HTML, /setHtml\(\$\("#office"\), room\(t\)\);/);
  assert.match(ler("replay.js"), /Só o escritório está em replay/);
  assert.doesNotMatch(ler("replay.js"), /Amsterdam|Amesterdão/);
});

// ---- demo
const CFG = { fuso: "Africa/Maputo", squads: [{ id: "dev", nome: "Dev", lead: "tech-lead", formato: "worktree" }, { id: "notas", nome: "Notas", lead: "cmo", formato: "sessao" }],
  projectos: [{ id: "loja", nome: "Loja" }, { id: "menu", nome: "Menu" }, { id: "est", nome: "Estudos" }] };
const AGORA = Date.parse("2026-11-17T12:00:00Z");

test("CA-48: prng determinístico", () => {
  const a = D.prng(7), b = D.prng(7), c = D.prng(8);
  const xa = [a(), a(), a()], xb = [b(), b(), b()];
  assert.deepEqual(xa, xb);
  assert.notDeepEqual(xa, [c(), c(), c()]);
  assert.ok(xa.every((x) => x >= 0 && x < 1));
});

test("CA-48: estadoDemo igual para os mesmos argumentos e com as chaves do servidor (tests/estado_chaves.json)", () => {
  const s = D.estadoDemo(AGORA, 3, CFG);
  assert.deepEqual(D.estadoDemo(AGORA, 3, CFG), s);
  assert.deepEqual(Object.keys(s).sort(), [...CHAVES.topo].sort());
  for (const t of s.tasks) {
    assert.deepEqual(Object.keys(t).sort(), [...CHAVES.tarefa].sort(), t.id);
    assert.deepEqual(Object.keys(t.lead).sort(), [...CHAVES.lider].sort(), t.id);
    for (const a of t.agents) assert.deepEqual(Object.keys(a).sort(), [...CHAVES.agente].sort(), t.id);
  }
  assert.deepEqual([...new Set(s.tasks.map((t) => t.squad))], ["dev", "notas"]);
  assert.ok(s.tasks.every((t) => CFG.projectos.some((p) => p.id === t.project)));
  assert.equal(s.espera.n, s.espera.itens.length);
  assert.ok(s.today.intervalos.every((x) => Object.keys(x).join() === "squad,ini,fim"));
  assert.equal(s.today.inicio, "2026-11-16T22:00:00.000Z");   // 00:00 em Maputo
});

test("CA-48: replayDemo com a forma do GET /api/replay (v: 1)", () => {
  const r = D.replayDemo("2026-11-17", 1, AGORA, CFG);
  assert.deepEqual(Object.keys(r), ["v", "dia", "inicio", "fim", "truncado", "quadros"]);
  assert.equal(r.v, 1);
  assert.equal(r.fim, AGORA / 1000);
  assert.ok(r.quadros.length > 0 && r.quadros[0].tipo === "completo");
  for (const q of r.quadros) {
    assert.ok(q.t >= r.inicio && q.t <= r.fim);
    for (const x of q.tipo === "completo" ? q.tarefas : q.mudou) {
      assert.deepEqual(Object.keys(x), ["id", "squad", "project", "mode", "status", "running", "phases", "ci", "lead", "agents"]);
      for (const a of x.agents) assert.deepEqual(Object.keys(a), ["role", "active", "finished", "stuck", "act"]);
    }
  }
});

test("CA-48/49: instalar só faz o GET api/config e só escreve chaves «demo:» no localStorage", async () => {
  const pedidos = [], dados = new Map([["tema", "claro"]]);
  class Storage { getItem(k) { return dados.has(k) ? dados.get(k) : null; } setItem(k, v) { dados.set(k, String(v)); } removeItem(k) { dados.delete(k); } }
  const store = new Storage(), el = () => ({ insertAdjacentHTML() {}, textContent: "", append() {} });
  const w = { location: { search: "?demo=1", href: "http://x/?demo=1", hash: "", pathname: "/" }, Response, Storage, localStorage: store,
    fetch: async (u) => { pedidos.push(String(u)); return new Response(JSON.stringify(CFG), { status: 200 }); },
    history: { replaceState() {} }, addEventListener() {},
    document: { createElement: el, head: { append() {} }, body: { classList: { add() {} } }, querySelector: el, addEventListener() {}, title: "Squad Office" } };
  await D.instalar(w);
  assert.deepEqual(pedidos, ["api/config"]);
  for (const u of ["api/state", "api/entregas", "api/transcript/x", "api/quadro", "api/replay?dia=2026-01-01"]) {
    const r = await w.fetch(u);
    assert.ok([200, 404, 503].includes(r.status), u);
  }
  assert.equal((await (await w.fetch("api/config")).json()).fuso, CFG.fuso);
  assert.deepEqual(pedidos, ["api/config"]);   // nada mais saiu
  assert.equal((await w.fetch("api/entregas")).status, 503);
  store.setItem("music", "1"); store.setItem("entregasLidas", "[]");
  assert.deepEqual([...dados.keys()].sort(), ["demo:entregasLidas", "demo:music", "tema"]);
  assert.equal(store.getItem("tema"), "claro");   // o tema lê o real enquanto não houver um tema demo
  assert.equal(w.document.title, "[demo] Squad Office");
});

test("CA-48/49: demo.js antes dos módulos e do 1.º poll; «Ver uma demonstração» nos primeiros passos", () => {
  const b = HTML.indexOf("const boot = /[?&]demo=1(&|$)/.test(location.search) ? import(\"./demo.js\")");
  assert.ok(b > 0);
  assert.match(HTML, /boot\.then\(lerCfg\)\.then\(\(\) => \{ bibRoute\(\); qRoute\(\); tick\(\);/);
  assert.match(HTML, /boot\.then\(\(\) => \{\n\/\/ barra de contadores/);
  assert.match(HTML, /<a href="\?demo=1">Ver uma demonstração<\/a>/);
  assert.match(ler("lateral.js"), /Entregas indisponível no modo demonstração/);
  assert.match(ler("demo.js"), /DEMO · dados fictícios/);
});
