// Funções puras de static/definicoes.js (node --test tests/definicoes_test.mjs).
// O import em si prova que o módulo não toca em window nem document ao carregar.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { b64uBytes, mesmaChave, suporte, prefsDoForm, msgErro, PREFS_OMISSAO, EVENTOS, VIS, MSG, FASES, prefsCompletas, fasesAoLigar, squadsDe, prefsOmissao }
  from "../static/definicoes.js";

// squads da config (GET api/config): duas para os testes; a fixture é a do servidor
const CFG = { squads: [{ id: "dev", nome: "Dev" }, { id: "ops", nome: "Ops" }] };
const SQ = squadsDe(CFG);
const FX = JSON.parse(readFileSync(new URL("./fixtures/api_config_exemplo.json", import.meta.url), "utf8"));

const SRC = readFileSync(new URL("../static/definicoes.js", import.meta.url), "utf8");

test("b64uBytes: base64url sem padding (chave VAPID de 65 bytes)", () => {
  const bytes = Uint8Array.from({ length: 65 }, (_, i) => (i * 37 + 4) & 255);
  const b64u = Buffer.from(bytes).toString("base64url");
  assert.ok(!b64u.includes("=") && /[-_]/.test(b64u + "-"));
  assert.deepEqual([...b64uBytes(b64u)], [...bytes]);
  assert.deepEqual([...b64uBytes("_-8")], [0xff, 0xef]);
});

test("mesmaChave: compara o applicationServerKey (ArrayBuffer) com a chave do servidor", () => {
  const k = Uint8Array.from({ length: 65 }, (_, i) => i), s = Buffer.from(k).toString("base64url");
  assert.equal(mesmaChave(k.buffer, s), true);
  const outra = k.slice(); outra[3] = 99;
  assert.equal(mesmaChave(outra.buffer, s), false);
  assert.equal(mesmaChave(k.slice(0, 64).buffer, s), false);
  assert.equal(mesmaChave(null, s), false);
  assert.equal(mesmaChave(k.buffer, null), false);
});

test("suporte: iPhone fora da app, sem suporte, bloqueado e ok", () => {
  const ok = { sw: true, push: true, notif: true, ios: false, standalone: false, permissao: "default" };
  assert.equal(suporte(ok), "ok");
  assert.equal(suporte({ ...ok, ios: true, push: false, notif: false }), "ios-instalar");
  assert.equal(suporte({ ...ok, ios: true, standalone: true }), "ok");
  assert.equal(suporte({ ...ok, push: false }), "sem-suporte");
  assert.equal(suporte({ ...ok, sw: false }), "sem-suporte");
  assert.equal(suporte({ ...ok, notif: false }), "sem-suporte");
  assert.equal(suporte({ ...ok, permissao: "denied" }), "bloqueado");
  assert.equal(suporte({ ...ok, permissao: "granted" }), "ok");
});

test("prefsDoForm: prefs completas com as chaves exactas do contrato (SPEC §4)", () => {
  const p = prefsDoForm({ "e-concluida": "on", "e-pr": "on", "s-dev": "on", "s-x": "on", sil: "on", de: "22:30", ate: "06:00" }, SQ);
  assert.deepEqual(p, { eventos: { concluida: true, falhada: false, pr: true, alertas: false, aprovacoes: false, fases: false },
    squads: { dev: true, ops: false }, fases: { SPEC: false, PLAN: false, CODE: false, QA: false, PR: false },
    silencio: { ligado: true, de: "22:30", ate: "06:00" }, criticos_ignoram_silencio: false });
  assert.deepEqual(Object.keys(p), Object.keys(PREFS_OMISSAO));
  for (const k of ["eventos", "squads", "fases", "silencio"]) assert.deepEqual(Object.keys(p[k]), Object.keys(prefsOmissao(CFG)[k]));
  assert.deepEqual(prefsDoForm({ "s-dev": "on" }).squads, {});   // sem config: sem squads
  assert.deepEqual(prefsDoForm({}).silencio, { ligado: false, de: "", ate: "" });
});

test("valores por omissão = D9 (aprovações desligadas, silêncio 23:00–07:00 desligado, críticos furam)", () => {
  assert.deepEqual(PREFS_OMISSAO.eventos, { concluida: true, falhada: true, pr: true, alertas: true, aprovacoes: false, fases: false });
  assert.deepEqual(prefsOmissao(CFG).squads, { dev: true, ops: true });
  assert.deepEqual(PREFS_OMISSAO.silencio, { ligado: false, de: "23:00", ate: "07:00" });
  assert.equal(PREFS_OMISSAO.criticos_ignoram_silencio, true);
});

test("msgErro: 503 = não configurado; senão o error do servidor ou o código", () => {
  assert.equal(msgErro(503, { error: "x" }), "Não configurado no servidor.");
  assert.equal(msgErro(400, { error: "as horas de silêncio têm de ser diferentes" }), "as horas de silêncio têm de ser diferentes");
  assert.equal(msgErro(502, { error: "o serviço de push recusou (403)" }), "o serviço de push recusou (403)");
  assert.equal(msgErro(500, null), "erro 500");
  assert.equal(msgErro(0, null), "sem ligação ao servidor");
  assert.equal(msgErro(400, { error: "x".repeat(500) }).length, 200);
});

test("textos PT-PT da SPEC §5", () => {
  assert.deepEqual(EVENTOS.map((e) => e[1]), ["Tarefa concluída", "Tarefa falhada ou parada", "PR aberto", "Alertas (tarefas paradas)", "Aprovações novas",
    "Progresso por fase"]);
  assert.deepEqual(squadsDe(FX), [["dev", "Dev"]]);
  assert.deepEqual(squadsDe(null), []);
  assert.deepEqual(VIS.map((e) => e[1]), ["Mostrar barra de progresso", "Mostrar estimativa de tempo"]);
  assert.equal(MSG.teste, "Teste enviado. Deve chegar em segundos.");
  assert.equal(MSG.ios, "No iPhone, instala primeiro o Office: Partilhar → Adicionar ao ecrã principal, e abre a partir do ícone (iOS 16.4 ou mais recente).");
  assert.equal(MSG.bloqueado, "As notificações estão bloqueadas para este site. Desbloqueia nas definições do browser e recarrega.");
  for (const t of ["Notificações no telemóvel", "Ligadas neste dispositivo", "Enviar teste", "Horas de silêncio", ">das <", ">às <", '"Fuso: "',
    "Os alertas críticos ignoram o silêncio", "Críticos: tarefa que morreu.", "Guardar", "Barra de progresso"])
    assert.ok(SRC.includes(t), t);
  assert.doesNotMatch(SRC, /sonda|cron|Amsterd/i);
});

test("o pedido de permissão sai no gesto, antes de qualquer await (iOS)", () => {
  const f = SRC.slice(SRC.indexOf("function ligar("), SRC.indexOf("async function desligar(")).replace(/\/\/.*$/gm, "");
  assert.ok(f.indexOf("Notification.requestPermission()") > 0);
  assert.ok(f.indexOf("Notification.requestPermission()") < f.indexOf("await"));
  assert.doesNotMatch(f.slice(0, f.indexOf("(async")), /\basync\b|\bawait\b/);
});

test("subscricao enviada só com endpoint e keys (sem expirationTime: chaves a mais dão 400)", () => {
  assert.match(SRC, /return \{ endpoint: j\.endpoint, keys: \{ p256dh: j\.keys\?\.p256dh, auth: j\.keys\?\.auth \} \}/);
});

// ---- Progresso por fase (SPEC office-push-barra-fases CA9, CA23–CA26)

test("CA9: prefsOmissao(cfg) = prefs_omissao() do servidor (PLAN §3: chaves, ordem e valores; squads da config)", () => {
  const D9 = { eventos: { concluida: true, falhada: true, pr: true, alertas: true, aprovacoes: false, fases: false },
    squads: { dev: true },
    fases: { SPEC: false, PLAN: false, CODE: false, QA: true, PR: true },
    silencio: { ligado: false, de: "23:00", ate: "07:00" }, criticos_ignoram_silencio: true };
  const p = prefsOmissao(FX);
  assert.deepEqual(p, D9);
  assert.deepEqual(Object.keys(p), Object.keys(D9));
  for (const k of ["eventos", "squads", "fases", "silencio"]) assert.deepEqual(Object.keys(p[k]), Object.keys(D9[k]));
  assert.deepEqual(PREFS_OMISSAO.squads, {});   // a constante não tem squads fixas
});

test("CA23: «Progresso por fase» é o último evento e vem desligado por omissão", () => {
  assert.deepEqual(EVENTOS.at(-1), ["fases", "Progresso por fase"]);
  assert.equal(PREFS_OMISSAO.eventos.fases, false);
});

test("CA24: 5 fases com os rótulos visíveis, grupo logo abaixo da caixa e linha do iPhone", () => {
  assert.deepEqual(FASES, [["SPEC", "SPEC"], ["PLAN", "PLAN"], ["CODE", "código"], ["QA", "QA"], ["PR", "PR"]]);
  assert.equal(MSG.fasesIphone, "No iPhone pode chegar um aviso por fase.");
  // o grupo vem a seguir às caixas de eventos (a última é e-fases), dentro do fieldset «Eventos»
  assert.match(SRC, /EVENTOS\.map\(\(\[k, r\]\) => caixa\("e-" \+ k, r\)\)\.join\(""\)\}<div class="def-fases" role="group" aria-label="Fases">/);
  assert.match(SRC, /FASES\.map\(\(\[k, r\]\) => caixa\("f-" \+ k, r\)\)/);
  assert.match(SRC, /\.disabled = !/); // sincFases: caixas desactivadas com o evento desligado (no browser: tools/capturas_temas.mjs)
});

test("CA25: fasesAoLigar liga QA e PR só se as 5 estiverem desligadas", () => {
  const off = { SPEC: false, PLAN: false, CODE: false, QA: false, PR: false };
  assert.deepEqual(fasesAoLigar(off), { SPEC: false, PLAN: false, CODE: false, QA: true, PR: true });
  assert.deepEqual(off.QA, false, "não altera o argumento");
  for (const [k] of FASES) {
    const uma = { ...off, [k]: true };
    assert.deepEqual(fasesAoLigar(uma), uma, k);
  }
  const todas = { SPEC: true, PLAN: true, CODE: true, QA: true, PR: true };
  assert.deepEqual(fasesAoLigar(todas), todas);
});

test("CA26: prefsDoForm devolve eventos.fases e fases.{SPEC..PR} (campos f-*, também true vindo de caixas desactivadas)", () => {
  const p = prefsDoForm({ "e-fases": "on", "f-SPEC": "on", "f-CODE": true, "f-PR": "on", "f-QA": false });
  assert.equal(p.eventos.fases, true);
  assert.deepEqual(p.fases, { SPEC: true, PLAN: false, CODE: true, QA: false, PR: true });
  assert.equal(prefsDoForm({}).eventos.fases, false);
});

test("CA26: prefsCompletas aceita prefs antigas (sem eventos.fases nem fases) e mantém as novas", () => {
  const antigas = { eventos: { concluida: true, falhada: true, pr: true, alertas: true, aprovacoes: false },
    squads: {}, silencio: { ligado: false, de: "23:00", ate: "07:00" },
    criticos_ignoram_silencio: true };
  assert.deepEqual(prefsCompletas(antigas), PREFS_OMISSAO);
  assert.equal(antigas.fases, undefined, "não altera o argumento");
  const novas = structuredClone(PREFS_OMISSAO);
  novas.eventos.fases = true; novas.fases.SPEC = true; novas.fases.PR = false;
  assert.deepEqual(prefsCompletas(novas), novas);
  // mistas: completa campo a campo
  assert.deepEqual(prefsCompletas({ ...antigas, fases: { SPEC: true } }).fases, { SPEC: true, PLAN: false, CODE: false, QA: true, PR: true });
  assert.equal(prefsCompletas({}).eventos.fases, false);
  assert.equal(prefsCompletas(null).fases.QA, true);
});

test("CA26: preencher usa prefsCompletas; guardar inclui as f-* desactivadas (o FormData omite-as)", () => {
  const f = SRC.slice(SRC.indexOf("function preencher("), SRC.indexOf("function estado("));
  assert.match(f, /prefsCompletas\(/);
  const g = SRC.slice(SRC.indexOf("async function guardar("), SRC.indexOf("export async function abrir("));
  assert.match(g, /FASES/);
  assert.match(g, /\.disabled\)/);
});
