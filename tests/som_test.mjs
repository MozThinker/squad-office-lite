// Testes de static/som.js (node --test tests/som_test.mjs): tabela, envelope, limitador, dedupe, preferências e o
// mapeamento poll → [som, chave] (porte do Office; no Lite o push não traz som). O import em si prova que o módulo não precisa de window, document nem AudioContext.
import test from "node:test";
import assert from "node:assert/strict";
import { SONS, VIB, VOL, lerPrefs, criarSom, sonsDoPoll } from "../static/som.js";

// contexto de áudio falso: regista osciladores, ganhos e cada chamada às AudioParam
function falso(state = "running", resume = () => Promise.resolve()) {
  const param = (log) => new Proxy({}, { get: (_, m) => (...a) => log.push([m, ...a]) });
  const c = { state, currentTime: 10, destination: "dest", osc: [], gains: [], resumes: 0 };
  c.resume = () => (c.resumes++, resume());
  c.createOscillator = () => {
    const o = { f: [], frequency: null, connect: (g) => (o.to = g, g), start: (t) => (o.t0 = t), stop: (t) => (o.t1 = t) };
    o.frequency = param(o.f);
    c.osc.push(o);
    return o;
  };
  c.createGain = () => {
    const g = { g: [], gain: null, connect: (d) => (g.to = d, d) };
    g.gain = param(g.g);
    c.gains.push(g);
    return g;
  };
  return c;
}
function montar({ c = falso(), on = true, vol = "medio", vib = true, vis = true } = {}) {
  const r = { c, t: 100000, vibs: [], vis, p: { on, vol, vib } };
  r.tocar = criarSom({ ctx: () => r.c, agora: () => r.t, visivel: () => r.vis, prefs: () => r.p, vibrar: (x) => r.vibs.push(x) });
  return r;
}
const tick = () => new Promise((ok) => setTimeout(ok, 0));
const perto = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} ≉ ${b}`);

test("SONS: os 6 sons da §7.3 (frequências, onda, duração, pico)", () => {
  assert.deepEqual(Object.keys(SONS).sort(), ["aprovacao", "concluido", "erro", "gravar", "parar", "respondeu"]);
  assert.deepEqual(SONS.gravar, [[0, .09, 660, 880, "sine", .08]]);
  assert.deepEqual(SONS.parar, [[0, .09, 880, 660, "sine", .08]]);
  assert.deepEqual(SONS.concluido, [[0, .11, 523, 523, "sine", .07], [.11, .11, 784, 784, "sine", .07]]);
  assert.deepEqual(SONS.aprovacao, [[0, .07, 740, 740, "triangle", .08], [.13, .07, 740, 740, "triangle", .08]]);
  assert.deepEqual(SONS.erro, [[0, .2, 220, 180, "sine", .07]]);
  assert.deepEqual(SONS.respondeu, [[0, .07, 988, 988, "sine", .05]]);
  assert.deepEqual(VOL, { baixo: .5, medio: .75, alto: 1 });
});

test("VIB: padrões por evento, respondeu sem vibração, somas ≤ 120 ms", () => {
  assert.deepEqual(VIB, { gravar: [10], parar: [10], concluido: [20, 40, 20], aprovacao: [30, 60, 30], erro: [60] });
  for (const p of Object.values(VIB)) assert.ok(p.reduce((a, b) => a + b) <= 120);
});

for (const ev of Object.keys(SONS)) for (const vol of Object.keys(VOL)) {
  test(`tocar ${ev} (${vol}): nós, onda, frequências, ataque 5 ms, fim 1e-4 em t0+dur`, () => {
    const r = montar({ vol });
    assert.equal(r.tocar(ev), true);
    assert.equal(r.c.osc.length, SONS[ev].length);
    assert.equal(r.c.gains.length, SONS[ev].length);
    SONS[ev].forEach(([t0, d, f0, f1, onda, pico], i) => {
      const o = r.c.osc[i], g = r.c.gains[i], t = 10 + t0;
      assert.equal(o.type, onda);
      assert.equal(o.to, g);
      assert.equal(g.to, "dest");
      perto(o.t0, t); perto(o.t1, t + d);
      assert.deepEqual(o.f[0], ["setValueAtTime", f0, t]);
      if (f1 === f0) assert.equal(o.f.length, 1);
      else { assert.equal(o.f[1][0], "exponentialRampToValueAtTime"); assert.equal(o.f[1][1], f1); perto(o.f[1][2], t + d); }
      assert.deepEqual(g.g[0], ["setValueAtTime", 0, t]);
      assert.equal(g.g[1][0], "linearRampToValueAtTime"); perto(g.g[1][1], pico * VOL[vol]); perto(g.g[1][2], t + .005);
      assert.equal(g.g[2][0], "exponentialRampToValueAtTime"); assert.ok(g.g[2][1] <= 1e-4); perto(g.g[2][2], t + d);
    });
  });
}

test("aprovacao: 2.º toque aos 0,13 s (70 + 60 de pausa + 70 ms)", () => {
  const r = montar();
  r.tocar("aprovacao");
  perto(r.c.osc[1].t0 - r.c.osc[0].t0, .13);
});

test("limite de 1 som por 2 s: 1999 false, 2000 true; o descartado não regista chave nem conta", () => {
  const r = montar();
  assert.equal(r.tocar("erro"), true);
  r.t += 1999;
  assert.equal(r.tocar("aprovacao", "apr:x"), false);
  assert.equal(r.c.osc.length, 1);
  r.t += 1;
  assert.equal(r.tocar("aprovacao", "apr:x"), true);   // 2000 ms depois do 1.º (o descartado não reinicia os 2 s)
  assert.equal(r.c.osc.length, 3);
  const s = montar();
  s.tocar("erro"); s.t += 500;
  assert.equal(s.tocar("erro", "k"), false);
  s.t += 1500;
  assert.equal(s.tocar("erro", "k"), true);           // a chave do descartado não ficou registada
});

test("chave: a mesma em < 60 s não toca; aos 60 s toca de novo; outra chave toca", () => {
  const r = montar();
  assert.equal(r.tocar("erro", "tarefa:a"), true);
  r.t += 59999;
  assert.equal(r.tocar("erro", "tarefa:a"), false);
  assert.equal(r.tocar("erro", "tarefa:b"), true);
  r.t += 2001;                                          // 62 000 depois da 1.ª
  assert.equal(r.tocar("erro", "tarefa:a"), true);
  const s = montar();
  s.tocar("erro", "k"); s.t += 60000;
  assert.equal(s.tocar("erro", "k"), true);
  s.t += 3000;
  assert.equal(s.tocar("erro"), true);                  // sem chave: só o limite dos 2 s
  s.t += 3000;
  assert.equal(s.tocar("erro"), true);
});

test("a) Sons desligado, b) escondido, c) sem AudioContext, evento desconhecido: false, 0 nós, 0 vibrações", () => {
  for (const r of [montar({ on: false }), montar({ vis: false }), montar({ c: null })]) {
    assert.equal(r.tocar("concluido", "k"), false);
    assert.equal(r.vibs.length, 0);
  }
  const r = montar();
  assert.equal(r.tocar("nada"), false);
  assert.equal(r.c.osc.length + r.vibs.length, 0);
  assert.equal(r.tocar("erro"), true);                  // nada disto contou para os 2 s
  const q = montar({ vis: false });
  q.tocar("erro", "k"); q.vis = true;
  assert.equal(q.tocar("erro", "k"), true);             // nem registou a chave
});

test("preferências lidas a cada chamada (desligar a meio corta)", () => {
  const r = montar();
  assert.equal(r.tocar("erro"), true);
  r.p = { on: false, vol: "alto", vib: true }; r.t += 5000;
  assert.equal(r.tocar("erro"), false);
  r.p = { on: true, vol: "alto", vib: true };
  assert.equal(r.tocar("erro"), true);
  perto(r.c.gains[1].g[1][1], .07);
});

test("vibração: VIB do evento só quando toca e com Vibrar ligado; respondeu não vibra", () => {
  const r = montar();
  for (const ev of ["gravar", "parar", "concluido", "aprovacao", "erro", "respondeu"]) { r.tocar(ev); r.t += 2000; }
  assert.deepEqual(r.vibs, [[10], [10], [20, 40, 20], [30, 60, 30], [60]]);
  const s = montar({ vib: false });
  assert.equal(s.tocar("erro"), true);
  assert.equal(s.vibs.length, 0);
  s.t += 1000;
  s.p.vib = true;
  assert.equal(s.tocar("erro"), false);
  assert.equal(s.vibs.length, 0);
});

test("contexto suspended: resume e toca; resume rejeitado = silêncio (0 nós)", async () => {
  const r = montar({ c: falso("suspended") });
  assert.equal(r.tocar("concluido"), true);
  assert.equal(r.c.resumes, 1);
  assert.equal(r.c.osc.length, 0);
  await tick();
  assert.equal(r.c.osc.length, 2);
  const s = montar({ c: falso("interrupted", () => Promise.reject(new Error("não"))) });
  assert.equal(s.tocar("erro"), true);
  await tick();
  assert.equal(s.c.osc.length, 0);
  const q = montar();
  q.tocar("erro");
  assert.equal(q.c.resumes, 0);
  assert.equal(q.c.osc.length, 1);                     // running: toca já, sem resume
});

test("lerPrefs: omissões, valores inválidos e excepção", () => {
  const ls = (o) => ({ getItem: (k) => (k in o ? o[k] : null) });
  const om = { on: true, vol: "medio", vib: true };
  assert.deepEqual(lerPrefs(ls({})), om);
  assert.deepEqual(lerPrefs(ls({ som: "0", somVol: "baixo", vibrar: "0" })), { on: false, vol: "baixo", vib: false });
  assert.deepEqual(lerPrefs(ls({ som: "1", somVol: "alto", vibrar: "1" })), { on: true, vol: "alto", vib: true });
  for (const v of ["", "MEDIO", "x", "toString", "__proto__", "constructor", "1"]) assert.equal(lerPrefs(ls({ somVol: v })).vol, "medio");
  assert.deepEqual(lerPrefs(ls({ som: "nao", vibrar: "" })), om);
  assert.deepEqual(lerPrefs({ getItem() { throw new Error("SecurityError"); } }), om);
  assert.deepEqual(lerPrefs(null), om);
});

// estado como o /api/state (tasks, alerts.items, aprovacoes sempre presentes)
const T = (id, running, status, extra = {}) => ({ id, squad: "dev", running, status, finished: running ? null : "2026-11-22T10:00:00", ...extra });
const E = (tasks = [], items = [], aprovacoes = []) => ({ tasks, alerts: { count: items.length, items }, aprovacoes });
const fimK = (id, fin = "2026-11-22T10:00:00", sq = "dev") => `fim:${sq}:${id}:${fin}`;

test("sonsDoPoll: fins (concluída, parcial, falhada) com a chave do vigia; não-terminais e sem antes a correr nada", () => {
  const prev = E([T("a", true, "a correr"), T("b", true, "a correr"), T("c", true, "a correr"), T("d", true, "a correr"),
    T("e", true, "a correr"), T("f", false, "concluída"), T("g", true, "a correr", { squad: "marketing" })]);
  const next = E([T("a", false, "concluída"), T("b", false, "parcial (PR draft)"), T("c", false, "falhada"),
    T("d", false, "parada?"), T("e", false, "morreu"), T("f", false, "concluída"), T("g", false, "erro", { squad: "marketing", finished: null }),
    T("h", false, "concluída")]);
  assert.deepEqual(sonsDoPoll(prev, next, false), [["concluido", fimK("a")], ["concluido", fimK("b")], ["erro", fimK("c")],
    ["erro", "fim:marketing:g:"]]);
  assert.deepEqual(sonsDoPoll(next, next, false), []);
  for (const st of ["a correr", "?"]) assert.deepEqual(sonsDoPoll(prev, E([T("a", false, st)]), false), []);
  assert.deepEqual(sonsDoPoll(prev, E([T("a", true, "a correr")]), false), []);   // ainda a correr
});

test("sonsDoPoll: com o jingle só o «concluída» se cala (é o que o jingle cobre); parcial, concluido e erro tocam", () => {
  const prev = E([T("a", true, "a correr"), T("b", true, "a correr"), T("c", true, "a correr"), T("d", true, "a correr")]);
  const next = E([T("a", false, "concluída"), T("b", false, "parcial (PR draft)"), T("c", false, "falhada"), T("d", false, "concluido")]);
  assert.deepEqual(sonsDoPoll(prev, next, true), [["concluido", fimK("b")], ["erro", fimK("c")], ["concluido", fimK("d")]]);
});

test("sonsDoPoll: alertas tarefa:<id> novos (crons e sondas não) e aprovações novas; ordem alertas, fins, aprovações", () => {
  const prev = E([T("a", true, "a correr")], ["tarefa:x"], [{ fonte: "geral", ficheiro: "velha.md" }]);
  const next = E([T("a", false, "falhada")], ["cron:backup", "sonda:api", "tarefa:x", "tarefa:y"],
    [{ fonte: "geral", ficheiro: "velha.md" }, { fonte: "marketing", ficheiro: "post.md" }]);
  assert.deepEqual(sonsDoPoll(prev, next, false),
    [["erro", "tarefa:y"], ["erro", fimK("a")], ["aprovacao", "apr:marketing/post.md"]]);
});

import { readFileSync, readdirSync } from "node:fs";
import { createJingleTracker } from "../static/music.js";
const fonte = (f) => readFileSync(new URL("../static/" + f, import.meta.url), "utf8");
test("QA vibração: descartado (2 s, chave, escondido, Sons desligado) não vibra; cada padrão efectivo ≤ 120 ms e da tabela", () => {
  const r = montar();
  r.tocar("aprovacao", "apr:x"); r.t += 1000;
  r.tocar("erro"); r.t += 5000;                                          // 2 s
  r.tocar("aprovacao", "apr:x");                                         // chave
  r.vis = false; r.t += 5000; r.tocar("erro"); r.vis = true;             // escondido
  r.p = { ...r.p, on: false }; r.t += 5000; r.tocar("erro");             // Sons desligado (desliga também a vibração)
  assert.deepEqual(r.vibs, [[30, 60, 30]]);
  for (const v of r.vibs) assert.ok(v.reduce((a, b) => a + b) <= 120);
});

test("review: resume que só resolve depois de 500 ms não toca (não empilha sons para o próximo toque)", async () => {
  let ok; const r = montar({ c: falso("interrupted", () => new Promise((f) => { ok = f; })) });
  assert.equal(r.tocar("aprovacao"), true);
  r.t += 600; ok(); await tick();
  assert.equal(r.c.osc.length, 0);
});

test("feedback: gravar e parar ficam fora do limite de 2 s (e contam para ele); o respondeu respeita-o", () => {
  const r = montar();
  assert.equal(r.tocar("concluido"), true);
  r.t += 500; assert.equal(r.tocar("gravar"), true);    // logo a seguir a uma conclusão: o bip de gravar não se perde
  r.t += 800; assert.equal(r.tocar("parar"), true);     // gravação curta: o bip de parar também não
  r.t += 500; assert.equal(r.tocar("respondeu"), false);   // o resto continua no limite (2 s desde o «parar»)
  r.t += 1500; assert.equal(r.tocar("respondeu"), true);
  r.t += 100; assert.equal(r.tocar("aprovacao", "apr:y"), false);
  const s = montar({ on: false }), h = montar({ vis: false });
  assert.equal(s.tocar("gravar"), false); assert.equal(h.tocar("parar"), false); // o interruptor e a visibilidade valem sempre
  assert.equal(s.tocar("respondeu"), false); assert.equal(h.tocar("respondeu"), false);
  assert.deepEqual(Object.keys(SONS).filter((k) => /^[gp][ra]/.test(k)), ["gravar", "parar"]); // o regex do som.js não apanha chaves novas
});

test("Lite: poll pelo tocar(som, chave) com jg(); contexto = actx da música; gestos; Definições com «Ouvir exemplo»", () => {
  const src = fonte("index.html");
  assert.match(src, /if \(prev && S\) for \(const \[s, k\] of S\.sonsDoPoll\(prev, next, jg\(\)\)\) tocar\(s, k\);/);
  assert.match(src, /jg = \(\) => musicOn && !!eng/);
  assert.match(src, /criarSom\(\{ ctx: \(\) => actx, agora: Date\.now, visivel: \(\) => !document\.hidden, prefs: \(\) => m\.lerPrefs\(lsS\(\)\)/);
  assert.match(src, /\["click", "touchend", "keydown"\]\.forEach\(\(n\) => addEventListener\(n, \(\) => unlock\(musicOn \|\| somOn\(\)\)/);
  assert.equal(src.match(/new \(window\.AudioContext/g).length, 1, "um só sítio cria o AudioContext (no gesto)");
  assert.match(src, /eng = M\.createEngine\(\{ ctx: actx, manter: somOn \}\)/);
  assert.match(fonte("music.js"), /manter\?\.\(\) \|\| ctx\.suspend\(\)/);
  assert.match(src, /musica, tema, agentes, tocar,/);
  const d = fonte("definicoes.js");
  assert.match(d, /caixa\("som", "Sons", ' data-v="som"'\)/);
  assert.match(d, /<select id="def-svol" data-v="somVol">/);
  assert.match(d, /navigator\?\.vibrate \? caixa\("vibrar", "Vibrar"/);
  assert.match(d, /a === "exemplo" \? A\.tocar\?\.\("concluido"\)/);
});

test("CA-34: «respondeu» só pela Conversa (no fim de um turno visto a trabalhar), nunca pelo poll", () => {
  const ficheiros = readdirSync(new URL("../static/", import.meta.url)).filter((f) => /\.(js|html)$/.test(f) && !["som.js", "conversa.js"].includes(f));
  for (const f of ficheiros) assert.doesNotMatch(fonte(f), /tocar\??\.?\(\s*["'`]respondeu/, f);
  assert.equal(fonte("conversa.js").match(/respondeu\(S\.trab[^\n]*?\) && A\.tocar\?\.\("respondeu"\)/g).length, 1); // só a conversa aberta (a página não sonda a lista)
});
