// Testes das funções puras de static/music.js (node --test tests/music_test.mjs).
// O import em si prova que o módulo não precisa de window, document nem AudioContext.
import test from "node:test";
import assert from "node:assert/strict";
import { MUSIC_CONFIG, styleFor, layersFor, createJingleTracker, readPrefs, writePrefs, createEngine } from "../static/music.js";

test("styleFor: qualquer sala lofi; sem sala piano", () => {
  for (const r of ["dev", "ops", "design", "constructor"]) assert.equal(styleFor(r), "lofi", r);
  for (const r of ["", undefined, null, 3]) assert.equal(styleFor(r), "piano", String(r));
});

const task = (squad, running, working, extra = {}) => ({ id: "t", started: "s", squad, running, working, ...extra });
const st = (...tasks) => ({ tasks });
const SALAS = ["dev", "ops"];   // salas da config (a 1.ª recebe as squads desconhecidas, como o sq() do index.html)

test("layersFor: soma working das tasks a correr da sala, máximo 4", () => {
  assert.equal(layersFor(st(), "dev", SALAS), 0);
  assert.equal(layersFor(st(task(undefined, true, ["tech-lead"])), "dev", SALAS), 1);
  assert.equal(layersFor(st(task("dev", true, ["a", "b"]), task(undefined, true, ["c"])), "dev", SALAS), 3);
  assert.equal(layersFor(st(task("dev", true, ["a", "b", "c"]), task("dev", true, ["d", "e", "f"])), "dev", SALAS), 4);
  assert.equal(layersFor(st(task("ops", true, ["a", "b", "c"])), "dev", SALAS), 0);
  assert.equal(layersFor(st(task("dev", false, ["a", "b"])), "dev", SALAS), 0);
  assert.equal(layersFor(st(task("ops", true, ["tech-lead", "architect"])), "ops", SALAS), 2);
  assert.equal(layersFor(null, "dev", SALAS), 0);
  assert.equal(layersFor(undefined, "ops", SALAS), 0);
});

test("layersFor: cada squad conta só na própria sala; squad desconhecida conta na 1.ª sala", () => {
  const s = st(task("ops", true, ["a", "b"]), task("design", true, ["x"]));
  assert.equal(layersFor(s, "ops", SALAS), 2);
  assert.equal(layersFor(s, "dev", SALAS), 1);
  assert.equal(layersFor(s, "dev", ["ops", "dev"]), 0);   // a 1.ª sala é a ops
  assert.equal(layersFor(s, "design"), 1);                // sem salas: a squad tal como vem
});

test("layersFor: nomes de squad do protótipo (constructor, __proto__, toString) contam na 1.ª sala", () => {
  const s = st(task("constructor", true, ["a"]), task("__proto__", true, ["b"]), task("toString", true, ["c"]));
  assert.equal(layersFor(s, "dev", SALAS), 3);
  for (const r of ["constructor", "__proto__", "toString"]) assert.equal(layersFor(s, r, SALAS), 0);
});

test("layersFor: paradas ou sem working não contam; o máximo vale por sala", () => {
  const s = st(task("ops", false, ["a"]), task("ops", true, undefined), task("ops", true, []));
  assert.equal(layersFor(s, "ops", SALAS), 0);
  const many = st(task("ops", true, ["a", "b", "c"]), task("ops", true, ["d", "e"]), task("dev", true, ["s"]));
  assert.equal(layersFor(many, "ops", SALAS), 4);
  assert.equal(layersFor(many, "dev", SALAS), 1);
  assert.equal(layersFor(many, "ops", SALAS, 2), 2);
});

const run = (id, started, squad = "dev") => ({ id, started, squad, running: true, status: "a correr", working: ["x"] });
const end = (t, status = "concluída") => ({ ...t, running: false, status, working: [] });

test("jingle: conclusão toca uma vez, e o mesmo poll repetido não volta a tocar", () => {
  const jt = createJingleTracker(), a = run("a", "1"), b = end(a);
  assert.deepEqual(jt.update(st(a), st(b), true), [b]);
  assert.deepEqual(jt.update(st(a), st(b), true), []);
  assert.deepEqual(jt.update(st(b), st(b), true), []);
});

test("jingle: outros estados de fim não tocam", () => {
  const jt = createJingleTracker();
  for (const s of ["falhou", "parada?", "incompleta", "erro"]) {
    const a = run("a" + s, "1");
    assert.deepEqual(jt.update(st(a), st(end(a, s)), true), []);
  }
});

test("jingle: conta tasks de qualquer sala", () => {
  const jt = createJingleTracker(), a = run("diario", "1", "ops");
  assert.equal(jt.update(st(a), st(end(a)), true).length, 1);
});

test("jingle: 1.º estado (sem prev) não toca nada", () => {
  const jt = createJingleTracker();
  assert.deepEqual(jt.update(null, st(end(run("a", "1"))), true), []);
});

test("jingle: terminada com a música desligada não se recupera depois", () => {
  const jt = createJingleTracker(), a = run("a", "1"), b = end(a);
  assert.deepEqual(jt.update(st(a), st(b), false), []);
  assert.deepEqual(jt.update(st(b), st(b), true), []);
});

test("jingle: mesmo id com outro started é outra execução e volta a tocar", () => {
  const jt = createJingleTracker(), a1 = run("mkt-diario", "dia1"), a2 = run("mkt-diario", "dia2");
  assert.equal(jt.update(st(a1), st(end(a1)), true).length, 1);
  assert.equal(jt.update(st(end(a1)), st(a2), true).length, 0);
  assert.equal(jt.update(st(a2), st(end(a2)), true).length, 1);
});

const fakeStorage = (init = {}) => {
  const m = new Map(Object.entries(init));
  return { m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
};

test("prefs: vazio → desligada e volume 30; gravar e ler dá o mesmo", () => {
  assert.deepEqual(readPrefs(fakeStorage()), { on: false, vol: 30 });
  const s = fakeStorage();
  writePrefs(s, { on: true, vol: 70 });
  assert.equal(s.m.get("music"), "1");
  assert.equal(s.m.get("musicVol"), "70");
  assert.deepEqual(readPrefs(s), { on: true, vol: 70 });
  writePrefs(s, { on: false, vol: 0 });
  assert.deepEqual(readPrefs(s), { on: false, vol: 0 });
});

test("prefs: volume inválido ou fora de 0–100 → 30", () => {
  for (const v of ["abc", "-5", "101", "50.5", "", "1000"]) assert.equal(readPrefs(fakeStorage({ music: "1", musicVol: v })).vol, 30, v);
  assert.equal(readPrefs(fakeStorage({ musicVol: "100" })).vol, 100);
});

test("prefs: storage que lança excepção → desligada, 30, sem erro", () => {
  const bad = { getItem() { throw new Error("SecurityError"); }, setItem() { throw new Error("QuotaExceeded"); } };
  assert.deepEqual(readPrefs(bad), { on: false, vol: 30 });
  assert.doesNotThrow(() => writePrefs(bad, { on: true, vol: 50 }));
  assert.deepEqual(readPrefs(undefined), { on: false, vol: 30 });
});

test("MUSIC_CONFIG: cada estilo tem exactamente as camadas 1–4, e layer de 0 a 4", () => {
  assert.deepEqual(Object.keys(MUSIC_CONFIG.styles).sort(), ["bossa", "lofi", "piano"]);
  assert.equal(MUSIC_CONFIG.maxLayers, 4);
  for (const [name, s] of Object.entries(MUSIC_CONFIG.styles)) {
    for (const t of s.tracks) {
      assert.ok(Number.isInteger(t.layer) && t.layer >= 0 && t.layer <= 4, name);
      assert.ok(t.voice in MUSIC_CONFIG.voices, `${name}: voz ${t.voice}`);
      assert.ok(t.hits.every((h) => h >= 0 && h < 16), name);
      if (Array.isArray(t.notes)) assert.equal(t.notes.length, t.hits.length * s.chords.length, `${name}: melodia`);
    }
    assert.deepEqual([...new Set(s.tracks.map((t) => t.layer).filter(Boolean))].sort(), [1, 2, 3, 4], name);
    assert.equal(s.roots.length, s.chords.length, name);
  }
  assert.ok(!MUSIC_CONFIG.styles.piano.tracks.some((t) => ["kick", "snare", "rim", "hat", "shaker"].includes(t.voice)), "piano sem bateria");
});

// ---- motor com um ctx falso (só conta nós): sem Web Audio no Node
function fakeCtx({ offline = true, resume = () => Promise.resolve() } = {}) {
  const ctx = { currentTime: 0, sampleRate: 100, state: "suspended", started: 0, disconnected: 0, resume, suspend: () => Promise.resolve() };
  const param = () => ({ value: 0, setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {}, cancelScheduledValues() {} });
  const node = () => {
    const n = { connect: (x) => x, disconnect: () => ctx.disconnected++, start: () => ctx.started++, stop() {} };
    for (const k of ["gain", "frequency", "Q", "delayTime", "threshold", "knee", "ratio", "attack", "release"]) n[k] = param();
    return n;
  };
  for (const k of ["createGain", "createDynamicsCompressor", "createBiquadFilter", "createDelay", "createOscillator", "createBufferSource"]) ctx[k] = node;
  ctx.destination = node();
  ctx.createBuffer = (c, n) => ({ getChannelData: () => new Float32Array(n) });
  if (offline) ctx.startRendering = () => {};
  return ctx;
}

test("motor parado: trocar de sala não acumula players (só a última sala toca ao ligar)", () => {
  const render = (rooms) => {
    const ctx = fakeCtx(), e = createEngine({ ctx });
    for (const r of rooms) e.setRoom(r);
    e.start(); e.renderUntil(4);
    return ctx;
  };
  const one = render(["dev"]), many = render(["", "ops", null, "dev"]);   // piano → lofi → piano → lofi
  assert.ok(one.started > 0);
  assert.equal(many.started, one.started);
  assert.ok(many.disconnected >= 3 * 2, "players antigos desligados");
});

test("motor: o timer só arranca com o ctx a andar, e stop() limpa-o com o relógio parado", async () => {
  const si = globalThis.setInterval, ci = globalThis.clearInterval, timers = new Set();
  globalThis.setInterval = () => { const id = {}; timers.add(id); return id; };
  globalThis.clearInterval = (id) => timers.delete(id);
  try {
    const stuck = createEngine({ ctx: fakeCtx({ offline: false, resume: () => new Promise(() => {}) }) });
    stuck.setRoom("dev"); stuck.start(); await new Promise((r) => setImmediate(r));
    assert.equal(timers.size, 0, "sem gesto válido não há timer");

    const ctx = fakeCtx({ offline: false }), e = createEngine({ ctx });
    e.setRoom("dev"); e.start(); await new Promise((r) => setImmediate(r));
    assert.equal(timers.size, 1);
    e.suspend(); assert.equal(timers.size, 0, "suspend limpa o timer");
    e.resume(); await new Promise((r) => setImmediate(r));
    assert.equal(timers.size, 1);
    ctx.state = "suspended"; e.stop();
    assert.equal(timers.size, 0, "stop com o relógio parado limpa logo o timer");
  } finally { globalThis.setInterval = si; globalThis.clearInterval = ci; }
});

test("motor: jingle com o relógio parado não fica agendado para tocar ao arrancar", () => {
  const ctx = fakeCtx({ offline: false }), e = createEngine({ ctx });
  const before = ctx.started;
  e.jingle(); e.jingle();
  assert.equal(ctx.started, before, "suspenso: nada agendado");
  ctx.state = "running"; e.jingle();
  assert.ok(ctx.started > before, "a andar: toca");
});
