// Conversa de montagem na vista «Squads e agentes» (ADR-020; CA-7, CA-11a, CA-33): funções puras de static/agentes.js.
// node --test tests/agentes_conversa_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ACCOES, ACCAO_ROT, CONVERSA_MAX, AVISO_API, alvo, corpoPedido, conversaActiva, conversaDe, ehNegocio, avisoNegocio,
  custoTexto, validarConversa, manualDaProposta } from "../static/agentes.js";

const SRC = readFileSync(new URL("../static/agentes.js", import.meta.url), "utf8");
const MODELOS = [{ id: "atendimento", negocio: true }, { id: "comercial", negocio: true }, { id: "observacao", negocio: false }];
const conv = (squad, estado, extra = {}) => ({ squad, estado, ronda: 1, rondas_max: 3, chamadas: 1, chamadas_max: 4, custo_usd: 0.05,
  tecto_usd: 1, voltas: [], perguntas: [], ...extra });
const D = (conversas = [], squads = []) => ({ modelos: MODELOS, conversas, squads });

test("squad_conversa no contrato v1: chaves exactas, rótulo e alvo squad:<id>", () => {
  assert.deepEqual(ACCOES.squad_conversa, ["squad", "texto"]);
  assert.equal(ACCAO_ROT.squad_conversa, "conversa da squad");
  assert.equal(alvo("squad_conversa", { squad: "apoio" }), "squad:apoio");
  assert.deepEqual(corpoPedido("t", "squad_conversa", { texto: "x", squad: "apoio", extra: 1 }),
    { token: "t", accao: "squad_conversa", args: { squad: "apoio", texto: "x" } });
});

test("validarConversa: id, texto 1–1000, squad com agentes e rascunho", () => {
  assert.deepEqual(validarConversa({ squad: " apoio ", texto: "Responder\naos clientes" }, D()).args, { squad: "apoio", texto: "Responder\naos clientes" });
  assert.match(validarConversa({ squad: "A", texto: "x" }, D()).erro, /^Id:/);
  assert.match(validarConversa({ squad: "apoio", texto: "" }, D()).erro, /^Texto: 1 a 1000/);
  assert.ok(validarConversa({ squad: "apoio", texto: "a".repeat(CONVERSA_MAX) }, D()).args);
  assert.match(validarConversa({ squad: "apoio", texto: "a".repeat(CONVERSA_MAX + 1) }, D()).erro, /^Texto/);
  assert.match(validarConversa({ squad: "apoio", texto: "bidi‮" }, D()).erro, /^Texto/);
  assert.match(validarConversa({ squad: "dev", texto: "x" }, D([], [{ id: "dev", agentes: [{ nome: "a" }] }])).erro, /já tem agentes/);
  assert.match(validarConversa({ squad: "nova", texto: "x" }, D([], [{ id: "nova", agentes: [], rascunho: { sha256: "a" } }])).erro, /rascunho/);
});

test("CA-11a: uma conversa de cada vez; a mesma squad responde; terminada não continua", () => {
  const d = D([conv("apoio", "a_perguntar"), conv("pagar", "nao_da")]);
  assert.equal(conversaActiva(d).squad, "apoio");
  assert.equal(conversaDe(d, "pagar").estado, "nao_da");
  assert.equal(conversaActiva(D([conv("pagar", "nao_da")])), null);
  assert.match(validarConversa({ squad: "vendas", texto: "x" }, d).erro, /Já há uma conversa em curso \(squad apoio\)/);
  assert.ok(validarConversa({ squad: "apoio", texto: "a resposta" }, d).args);
  assert.match(validarConversa({ squad: "pagar", texto: "x" }, D([conv("pagar", "nao_da")])).erro, /já terminou/);
});

test("CA-33: recomendação de chave de API só para modelos de negócio", () => {
  assert.equal(ehNegocio(D(), "atendimento"), true);
  assert.equal(ehNegocio(D(), "observacao"), false);
  assert.equal(avisoNegocio(D(), "comercial"), AVISO_API);
  assert.equal(avisoNegocio(D(), "observacao"), null);
  assert.equal(avisoNegocio(D(), undefined), null);
  assert.match(AVISO_API, /chave de API/);
  assert.ok(SRC.includes("avisoNegocio(D, c.modelo_squad)") && SRC.includes("avisoNegocio(D, v.modelo)"));
});

test("custoTexto e manualDaProposta (o formulário à mão a partir da proposta)", () => {
  assert.equal(custoTexto(conv("apoio", "a_perguntar", { custo_usd: 0.3, chamadas: 2 })), "0,30 de 1,00 USD · chamada 2 de 4");
  assert.equal(custoTexto(null), "0,00 de 1,00 USD · chamada 0 de 4");
  const c = conv("apoio", "proposta", { modelo_squad: "atendimento" });
  assert.deepEqual(manualDaProposta(c, { rascunho: { missao: "Responder" } }), { modelo: "atendimento", squad: "apoio", missao: "Responder" });
  assert.deepEqual(manualDaProposta(c, null), { modelo: "atendimento", squad: "apoio", missao: "" });
  assert.equal(manualDaProposta(conv("apoio", "nao_da"), null), null);
});

test("DOM: conversa e recusa só por textContent; formulário manual atrás de «Escolher o modelo à mão»", () => {
  assert.ok(SRC.includes('dets("nova", "Escolher o modelo à mão", manualEl())'));
  assert.ok(SRC.includes('pedir("squad_conversa", args'));
  assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML/.test(SRC));
});
