// index.html a partir de GET /api/config (SPEC AC-a5/a6/a7): salas, personagens e omissões correm em node:vm.
// node --test tests/config_ui_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const HTML = readFileSync(new URL("../static/index.html", import.meta.url), "utf8");
const FX = JSON.parse(readFileSync(new URL("./fixtures/api_config_exemplo.json", import.meta.url), "utf8"));
const linha = (ini) => { const i = HTML.indexOf("\n" + ini); assert.ok(i >= 0, `falta ${ini}`); return HTML.slice(i, HTML.indexOf("\n", i + 1)); };
const bloco = (ini, fim) => { const i = HTML.indexOf("\n" + ini); assert.ok(i >= 0, `falta ${ini}`); return HTML.slice(i, HTML.indexOf(fim, i) + fim.length); };
const ROLES_SRC = bloco("const ROLES = {", "\n};");
const SRC = ROLES_SRC + bloco("const rl = ", "\n}\n") + linha("const CFG0 = ");

// salas da config; R() = sala visível (a 1.ª, como na 1.ª pintura sem tarefas)
function ctx(cfg) {
  const c = vm.runInNewContext(SRC + "\n({ ROLES, rl, label, salasDe, CFG0 })", {});
  const ROOMS = structuredClone(c.salasDe(cfg)), k0 = Object.keys(ROOMS)[0];
  return { ...c, ROOMS, R: ROOMS[k0], rl: (r, s = ROOMS[k0]) => c.rl(r, s), label: (r, s = ROOMS[k0]) => c.label(r, s) };
}
const DIR = "jar" + "vis"; // nome do director da origem, montado para o grep de genérico da CI
const sqd = (id, nome, lead, personagem = null, formato = "worktree") => ({ id, nome, lead, personagem, formato, tipos: [], modos: [] });

test("AC-a7: uma sala por squad da config, pela ordem dela; sem director nem salas fixas", () => {
  const { ROOMS, ROLES } = ctx({ squads: [sqd("ops", "Ops", "tech-lead"), sqd("notas", "Notas", "cmo", "Funil", "sessao")] });
  assert.deepEqual(Object.keys(ROOMS), ["ops", "notas"]);
  assert.deepEqual([ROOMS.ops.name, ROOMS.ops.fases, ROOMS.notas.fases], ["Ops", true, false]);
  for (const r of Object.values(ROOMS)) assert.ok(!(DIR in r.seats));
  assert.ok(!(DIR in ROLES));
  assert.doesNotMatch(HTML, /j[a]rvis|mktRoom|rndRoom|supRoom|probesHtml|cronsHtml|data-k="crons"|Europe\/Amsterdam|state\.probes|state\.crons/i);
});

test("AC-a7: o líder senta-se no lugar do tech-lead e a «personagem» dá-lhe o nome; papel desconhecido → general", () => {
  const c = ctx({ squads: [sqd("notas", "Notas", "cmo", "Funil", "sessao")] }), s = c.ROOMS.notas;
  assert.deepEqual(s.seats.cmo, [155, 62]);
  assert.ok(!("tech-lead" in s.seats));
  assert.equal(c.rl("cmo").name, "Funil");
  assert.equal(c.label("cmo"), "Funil (CMO)");
  const roleOf = (r) => (s.seats[r] ? r : "general");
  for (const r of ["explore", DIR, "sre"]) assert.equal(roleOf(r), "general", r);
  assert.ok(s.seats.general);
  // líder sem ficha no frontend: a do tech-lead, com o nome do papel (ou a personagem)
  const d = ctx({ squads: [sqd("lab", "Lab", "cientista")] });
  assert.equal(d.rl("cientista").name, "cientista");
  assert.equal(d.rl("cientista").shirt, d.ROLES["tech-lead"].shirt);
  assert.equal(ctx({ squads: [sqd("lab", "Lab", "cientista", "Eureka")] }).rl("cientista").name, "Eureka");
  // personagem null → nome do frontend; um não-líder nunca é renomeado
  assert.equal(ctx({ squads: [sqd("dev", "Dev", "tech-lead")] }).rl("tech-lead").name, "Maestro");
  assert.equal(c.rl("architect").name, "Planta");
});

test("AC-a7: com a fixture do servidor, uma sala Dev com o Maestro e a planta completa", () => {
  const c = ctx(FX), s = c.ROOMS.dev;
  assert.deepEqual(Object.keys(c.ROOMS), ["dev"]);
  assert.equal(c.rl("tech-lead").name, "Maestro");
  assert.equal(s.fases, true);
  assert.deepEqual([...s.optional].sort(), ["ajudante", "general"]);
  assert.equal(Object.keys(s.seats).length, 12);
});

test("config ausente: omissões sensatas (marca genérica, uma sala Dev)", () => {
  const c = ctx(c0());
  function c0() { return structuredClone(vm.runInNewContext(linha("const CFG0 = ") + "\nCFG0")); }
  assert.equal(c0().marca, "Squad Office");
  assert.deepEqual(c0().squads, []);
  assert.equal(c0().aviso, null);
  assert.deepEqual(Object.keys(c.ROOMS), ["dev"]);
  for (const x of [null, undefined, {}, { squads: "x" }]) assert.deepEqual(Object.keys(ctx(x).ROOMS), ["dev"], JSON.stringify(x));
});

test("AC-a5/a7: título, cabeçalho, fuso e aviso vêm da config; a config é lida antes do 1.º estado", () => {
  assert.match(HTML, /document\.title = \(n > 0 \? `\(\$\{n\}\) ` : ""\) \+ cfg\.marca;/);
  assert.match(HTML, /\$\("header h1"\)\.textContent = "▣ " \+ cfg\.marca;/);
  assert.match(HTML, /<p id="aviso" role="status" hidden><\/p>/);
  assert.match(HTML, /fetch\("api\/config", \{ cache: "no-store" \}\)/);
  assert.match(HTML, /catch \{ setTimeout\(lerCfg, 5000\); \}/);
  assert.match(HTML, /lerCfg\(\)\.then\(\(\) => \{ bibRoute\(\); qRoute\(\); tick\(\); setInterval\(tick, 8000\); \}\);/);
  // todos os Intl com o fuso da config (TZ); nenhum fuso fixo
  for (const m of HTML.matchAll(/toLocale(?:Time|Date)?String\("[^"]+", \{([^}]*)\}/g)) if (!/FractionDigits/.test(m[1])) assert.match(m[1], /timeZone: TZ/, m[0]);
  assert.doesNotMatch(HTML, /toLocale(?:Time|Date)?String\("pt-PT"\)/);   // sem opções = sem fuso
  for (const m of HTML.matchAll(/new Intl\.DateTimeFormat\("en-GB", \{([^}]*)\}/g)) assert.match(m[1], /timeZone: TZ/, m[0]);
  // a config chega aos módulos
  assert.match(HTML, /const bibApi = \(\) => \(\{[^}]*\bcfg\b/);
  assert.match(HTML, /const quadroApi = \(\) => \(\{[^}]*\bcfg\b/);
  assert.match(HTML, /\.abrir\(\{ esc, cfg, /);
});

test("AC-a6: sem secções de crons/sondas e alertas só de tarefas", () => {
  assert.doesNotMatch(HTML, /\.probes|--probe-off|Relógio da fábrica|\bsondas?\b|\bcrons?\b/i);
  const f = HTML.slice(HTML.indexOf("async function notifyAlert("), HTML.indexOf("\n}\n", HTML.indexOf("async function notifyAlert(")));
  assert.match(f, /if \(!k\.startsWith\("tarefa:"\)\) return;/);
});
