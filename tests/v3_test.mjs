// v3 (SPEC lite-v3): letra pixel, módulos a pedido, funções puras de topo.js e avisos.js. node --test tests/v3_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";

const STATIC = new URL("../static/", import.meta.url);
const ler = (f) => readFileSync(new URL(f, STATIC), "utf8");
const HTML = ler("index.html");
const regras = (css) => [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => [m[1].trim(), m[2]]);

test("CA-3: a letra pixel só nos títulos «Fábrica», «Biblioteca», «Quadro» e «Ajuda» (lista exacta)", () => {
  const usos = regras(ler("office.css")).filter(([, d]) => /font-family:\s*var\(--pixel\)/.test(d)).map(([s]) => s);
  assert.deepEqual(usos, [":is(#fab-h, #bib-h, #q-h, #aj-h)"]);
  for (const f of readdirSync(STATIC).filter((x) => x.endsWith(".css") && x !== "office.css"))
    assert.doesNotMatch(ler(f), /--pixel|Pixelify/, f);
  assert.match(HTML, /<h2 id="fab-h">Fábrica<\/h2>/);
  assert.match(ler("biblioteca.js"), /id="bib-h"/);
  assert.match(ler("quadro.js"), /id="q-h"/);
  assert.match(ler("ajuda.js"), /id="aj-h"/);
});

// CA-50: no arranque topo, avisos, lateral, act, fabrica e ambiente; os outros só por import() num clique (ou com ?demo=1)
const ARRANQUE = ["topo.js", "avisos.js", "lateral.js", "act.js", "fabrica.js", "ambiente.js"];
const A_PEDIDO = ["paineis.js", "entregas.js", "tabelas.js", "replay.js", "demo.js"];
test("CA-50: nenhum módulo a pedido é importado estaticamente nem no arranque", () => {
  for (const f of ["index.html", ...ARRANQUE]) {
    const s = ler(f);
    for (const m of A_PEDIDO) {
      const n = m.replace(".", "\\.");
      assert.doesNotMatch(s, new RegExp(`import\\s*(?:[\\w*{}\\s,]+from\\s*)?["']\\./${n}["']`), `${f}: import estático de ${m}`);
      assert.doesNotMatch(s, new RegExp(`<script[^>]+${n}|<link[^>]+${m.replace(".js", "\\.css")}`), `${f}: ${m} no HTML`);
      for (const l of s.split("\n").filter((x) => x.includes(`import("./${m}")`)))
        assert.match(l, m === "demo.js" ? /demo=1/ : /\|\|= import\(/, `${f}: ${m} fora de um carregamento preguiçoso: ${l.trim()}`);
    }
  }
  for (const m of ["topo.js", "lateral.js", "fabrica.js", "ambiente.js"]) assert.match(HTML, new RegExp(`import\\("\\./${m.replace(".", "\\.")}"\\)`), m);
  assert.match(HTML, /<script src="act\.js"><\/script>\n<script>\n"use strict";/);
});

// ---- topo.js (CA-7…13, 20)
const T = await import("../static/topo.js");
const AV = await import("../static/avisos.js");
const tarefa = (o = {}) => ({ id: "t", squad: "a", running: true, agents: [], working: [], ...o });

test("CA-9/CA-7: sem /api/state todos os contadores são null e mostram «—»", () => {
  assert.deepEqual(T.contadores(null), { salas: null, agentes: null, trabalhar: null, subagentes: null, espera: null, falhas: null });
  assert.equal(T.fmtNum(null), "—");
  assert.equal(T.fmtNum(undefined), "—");
  assert.equal(T.fmtUsd(null), "—");
  assert.equal(T.fmtUsd(NaN), "—");
  assert.equal(T.fmtNum(0), "0");
  assert.equal(T.fmtUsd(1.5), "≈ US$ 1,50");
});

test("CA-9: salas, agentes, a trabalhar e subagentes só das tarefas a correr", () => {
  const s = { tasks: [
    tarefa({ squad: "a", agents: [{ finished: false }, { finished: true }], working: ["tech-lead", "x"] }),
    tarefa({ id: "u", squad: "b", agents: [{ finished: false }] }),
    tarefa({ id: "v", squad: "a" }),
    tarefa({ id: "w", squad: "c", running: false, agents: [{ finished: false }], working: ["y"] }),
  ], espera: { n: 3, itens: [] }, alerts: { count: 1 }, falhas_exec: [{ id: "f" }, { id: "g" }] };
  assert.deepEqual(T.contadores(s), { salas: 2, agentes: 5, trabalhar: 2, subagentes: 2, espera: 3, falhas: 3 });
  // a squad desconhecida conta pela sala do cliente (sq)
  assert.equal(T.contadores(s, () => "uma").salas, 1);
});

test("CA-10/CA-12: espera só de state.espera.n (o cliente não recalcula); falhas = alerts.count + falhas_exec", () => {
  assert.equal(T.contadores({ tasks: [], waiting: [{}, {}], aprovacoes: [{}] }).espera, null);
  assert.equal(T.contadores({ tasks: [], espera: { n: 0, itens: [] } }).espera, 0);
  assert.equal(T.nFalhas({ alerts: { count: 2 }, falhas_exec: [{}] }), 3);
  assert.equal(T.nFalhas({}), 0);
  assert.equal(T.nFalhas(null), null);
});

test("CA-8: atalhos fora de campos de texto, sem modificadores e sem diálogo aberto", () => {
  const k = (key, o = {}) => ({ key, ...o });
  assert.ok(T.atalhoValido(k("p"), { tagName: "BODY" }, false));
  assert.ok(T.atalhoValido(k("P"), null, false));
  assert.ok(T.atalhoValido(k("?"), { tagName: "button" }, false));
  for (const tag of ["INPUT", "TEXTAREA", "SELECT", "input"]) assert.equal(T.atalhoValido(k("p"), { tagName: tag }, false), false, tag);
  assert.equal(T.atalhoValido(k("p"), { tagName: "DIV", isContentEditable: true }, false), false);
  assert.equal(T.atalhoValido(k("p"), null, true), false);   // <dialog> aberto
  for (const m of ["ctrlKey", "metaKey", "altKey", "isComposing"]) assert.equal(T.atalhoValido(k("p", { [m]: true }), null, false), false, m);
  for (const key of ["/", "x", "Enter"]) assert.equal(T.atalhoValido(k(key), null, false), false, key);
});

test("CA-7: «sem ligação há N s»", () => {
  assert.equal(T.semLigacao(1000, 9400), "sem ligação há 8 s");
  assert.equal(T.semLigacao(5000, 1000), "sem ligação há 0 s");
});

test("CA-20: terminal em 1.º plano: normal < 10 min, âmbar 10–25, «preso?» aos 25", () => {
  const d = "2026-11-17T10:00:00Z", m = (n) => Date.parse(d) + n * 60000;
  assert.deepEqual(T.terminalTexto(d, m(0.5)), { texto: "à espera do terminal", classe: "", preso: false });
  assert.deepEqual(T.terminalTexto(d, m(9.9)), { texto: "à espera do terminal há 9 min", classe: "", preso: false });
  assert.deepEqual(T.terminalTexto(d, m(10)), { texto: "à espera do terminal há 10 min", classe: "s-warn", preso: false });
  assert.deepEqual(T.terminalTexto(d, m(24.9)).preso, false);
  assert.deepEqual(T.terminalTexto(d, m(25)), { texto: "à espera do terminal há 25 min", classe: "s-bad", preso: true });
  assert.equal(T.terminalTexto("lixo", m(30)).preso, false);
});

test("CA-13: «salas» leva à tarefa a correr mais recente", () => {
  const s = { tasks: [tarefa({ id: "a", started: "2026-11-17T08:00" }), tarefa({ id: "b", started: "2026-11-17T09:00" }), tarefa({ id: "c", running: false, started: "2026-11-17T10:00" })] };
  assert.equal(T.maisRecente(s).id, "b");
  assert.equal(T.maisRecente({ tasks: [] }), null);
});

// ---- avisos.js (CA-14, 15)
const ESPERA = { n: 2, itens: [
  { tipo: "aprovacao", chave: "apr:f/x.md", fonte: "f", ficheiro: "x.md", titulo: "Pedido X", abertas: 2, idade_dias: 1, desde: "2026-11-16T00:00:00+02:00" },
  { tipo: "pr", chave: "pr:a/t1", id: "t1", squad: "a", project: "loja", pr_url: "https://github.com/o/r/pull/7", draft: true, finished: "2026-11-17T09:00:00+02:00", desde: "2026-11-17T09:00:00+02:00" }] };

test("CA-14: fila junta a mesma chave em 20 s («×N»), máximo 4 e sai primeiro o informativo mais antigo", () => {
  let l = AV.fila([], { tipo: "alerta", chave: "a" }, 0);
  l = AV.fila(l, { tipo: "alerta", chave: "a" }, 20000);
  assert.equal(l.length, 1); assert.equal(l[0].n, 2);
  l = AV.fila(l, { tipo: "alerta", chave: "a" }, 40001);   // > 20 s depois do último: outro
  assert.equal(l.length, 2);
  l = AV.fila([], { tipo: "info", chave: "i1" }, 1);
  for (const [c, t] of [["a1", 2], ["i2", 3], ["a2", 4], ["a3", 5]]) l = AV.fila(l, { tipo: c[0] === "i" ? "info" : "alerta", chave: c }, t);
  assert.deepEqual(l.map((x) => x.chave), ["a1", "i2", "a2", "a3"]);
  l = AV.fila(l, { tipo: "alerta", chave: "a4" }, 6);
  assert.deepEqual(l.map((x) => x.chave), ["a1", "a2", "a3", "a4"]);
  l = AV.fila(l, { tipo: "alerta", chave: "a5" }, 7);   // sem informativos: sai o alerta mais antigo
  assert.deepEqual(l.map((x) => x.chave), ["a2", "a3", "a4", "a5"]);
});

test("CA-14: o informativo sai aos 5 s; o alerta fica até ser fechado", () => {
  const l = [{ tipo: "info", chave: "i", em: 0 }, { tipo: "alerta", chave: "a", em: 0 }];
  assert.deepEqual(AV.expirar(l, 4999).map((x) => x.chave), ["i", "a"]);
  assert.deepEqual(AV.expirar(l, 5000).map((x) => x.chave), ["a"]);
  assert.deepEqual(AV.expirar(l, 9e9).map((x) => x.chave), ["a"]);
});

test("CA-15/D-10: nada no 1.º poll; depois só o que mudou (aprovação nova, PR, falha, fim)", () => {
  const s0 = { tasks: [tarefa({ id: "t1", project: "loja", mode: "goal" })], espera: { n: 0, itens: [] }, alerts: { count: 0, items: [] }, falhas_exec: [] };
  const s1 = { tasks: [tarefa({ id: "t1", project: "loja", mode: "goal", running: false, status: "concluída", gh: { pr: "aberto", pr_url: "https://github.com/o/r/pull/7" } })],
    espera: ESPERA, alerts: { count: 1, items: ["tarefa:t9"] }, falhas_exec: [{ id: "t2", project: "site", motivo: "api_error", http: 429, erro: true }] };
  assert.deepEqual(AV.novos(null, s1), []);
  assert.deepEqual(AV.infos(null, s1), []);
  assert.deepEqual(AV.novos(s0, s1).map((a) => a.chave), ["apr:f/x.md", "pr:a/t1", "tarefa:t9", "exec:t2"]);
  assert.deepEqual(AV.novos(s1, s1), []);
  assert.deepEqual(AV.infos(s0, s1).map((a) => a.texto), ["✅ loja: goal concluída", "🎉 PR aberto: loja #7"]);
  assert.equal(AV.falhasDe(s1)[1].oque, "api_error · HTTP 429");
  assert.deepEqual(AV.esperaDe({ espera: { itens: [{ tipo: "recusa", chave: "r" }, ...ESPERA.itens] } }).map((x) => x.oque), ["Pedido X", "PR #7 (draft)"]);
});

test("CA-15: notificação do browser só com o separador escondido e as notificações ligadas", () => {
  const a = { tipo: "alerta", item: { tipo: "aprovacao" } };
  assert.equal(AV.notificar(a, true, true), true);
  assert.equal(AV.notificar(a, false, true), false);
  assert.equal(AV.notificar(a, true, false), false);
  assert.equal(AV.notificar({ tipo: "info" }, true, true), false);
  assert.equal(AV.notificar({ tipo: "alerta", item: { tipo: "tarefa" } }, true, true), false);   // já notificada pelo index.html
});

// ---- tarefa e separador (CA-17, 21, 22, 11)
const AJ = await import("../static/ajuda.js");
test("CA-17: «N recusas: Bash, …» e a dica do runner.ferramentas; erro da API", () => {
  assert.equal(AJ.recusasTexto({ recusas: 2, ferramentas: ["Bash", "WebFetch"] }), "2 recusas: Bash, WebFetch");
  assert.equal(AJ.recusasTexto({ recusas: 1, ferramentas: ["outra"] }), "1 recusa: outra");
  assert.equal(AJ.recusasTexto({ recusas: 3, ferramentas: null }), "3 recusas");
  for (const f of [null, undefined, { recusas: 0 }, { recusas: "2" }]) assert.equal(AJ.recusasTexto(f), null);
  assert.match(AJ.DICA_RECUSAS, /acrescenta a regra a `runner\.ferramentas`/);
  assert.equal(AJ.erroApiTexto({ erro: true, http: 429, motivo: "api_error" }), "a execução acabou com erro (HTTP 429, api_error)");
  assert.equal(AJ.erroApiTexto({ erro: true, http: null, motivo: null }), "a execução acabou com erro");
  assert.equal(AJ.erroApiTexto({ erro: false, http: 500 }), null);
  assert.equal(AJ.erroApiTexto(null), null);
  assert.equal(AJ.dicaErro("incompleta", ""), "Terminou sem PR: confirma `gh auth status`.");
  assert.match(HTML, /const r = AJ\?\.recusasTexto\(t\.fim\), e = AJ\?\.erroApiTexto\(t\.fim\);/);
  assert.match(HTML, /\$\{esc\(r\)\}<\/b><div class="md">\$\{md\(AJ\.DICA_RECUSAS\)\}/);
});

test("CA-21/CA-22: título no separador (40 car.) e «interrompida» a vermelho", async () => {
  const { default: vm } = await import("node:vm");
  const fn = (n) => { const i = HTML.indexOf(`\nfunction ${n}(`); return HTML.slice(i, HTML.indexOf("\n}\n", i) + 3); };
  const { statusClass } = vm.runInNewContext(fn("statusClass") + "; ({ statusClass })");
  assert.equal(statusClass({ status: "interrompida" }), "s-bad");
  assert.equal(statusClass({ status: "concluída" }), "s-ok");
  assert.equal(statusClass({ status: "parcial (PR draft)" }), "s-warn");
  const i = HTML.indexOf("const cut = "), j = HTML.indexOf("const tabName = "), k = HTML.indexOf("\n\n", j);
  const tabName = vm.runInNewContext(HTML.slice(i, HTML.indexOf("\n", i)) + "\n" + HTML.slice(j, k) + "; tabName",
    { ROOMS: { a: { fases: true } }, sq: () => "a", TZ: "UTC" });
  assert.equal(tabName({ titulo: "x".repeat(50), id: "20261117-1000-abc" }), "x".repeat(39) + "…");
  assert.equal(tabName({ titulo: "Curto", id: "20261117-1000-abc" }), "Curto");
  assert.equal(tabName({ titulo: null, id: "20261117-1000-abc" }), "abc");
});

test("CA-11: a coluna «À espera» do Quadro diz o mesmo espera.n da barra", async () => {
  const Q = await import("../static/quadro.js");
  assert.equal(Q.nRotulo("a-espera", 2, 5), "5 à tua espera, 2 cartões teus");
  assert.equal(Q.nRotulo("a-espera", 2, null), "2 cartões");
  assert.equal(Q.nRotulo("feito", 2, 5), "2 cartões");
  assert.match(HTML, /espera: \(\) => state\?\.espera\?\.n \?\? null/);
});

test("notificação do sistema sem o título da aprovação (review de segurança)", () => {
  assert.equal(AV.corpoNotif({ tipo: "aprovacao", titulo: "Segredo", oque: "Segredo" }), "Aprovação à tua espera");
  assert.equal(AV.corpoNotif({ tipo: "pr", oque: "PR #7" }), "PR #7");
});
