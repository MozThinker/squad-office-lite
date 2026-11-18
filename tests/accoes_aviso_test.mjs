// Alerta do serviço de ações (SPEC lite-accoes-aviso, CA-17…20): funções puras de avisos.js. node --test tests/accoes_aviso_test.mjs
import test from "node:test";
import assert from "node:assert/strict";

const AV = await import("../static/avisos.js");
const { accoesAlerta, accoesPasso, accoesHtml, fila, ACCOES_REGISTO } = AV;

const EM = "2026-11-18T13:23:00+00:00";
const st = (avisos, parado = false) => ({ tasks: [], accoes: { avisos, em: EM, parado, desde: "13:23" } });
const COL = ACCOES_REGISTO + "; nada colhido nesta passagem", EXE = ACCOES_REGISTO + "; nada executado nesta passagem";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// ---- accoesAlerta (CA-17, CA-21)

test("accoesAlerta: sem accoes / vazio e não parado → null", () => {
  assert.equal(accoesAlerta(null), null);
  assert.equal(accoesAlerta({ tasks: [] }), null);
  assert.equal(accoesAlerta(st([])), null);
});

test("accoesAlerta: parado", () => {
  assert.deepEqual(accoesAlerta(st([], true)), { chave: "accoes:parado", texto: "Serviço de ações parado desde 13:23" });
});

test("accoesAlerta: registo do colector e do executor contam como um", () => {
  const a = accoesAlerta(st([COL, EXE]));
  assert.equal(a.texto, "Serviço de ações: não consigo ler o registo das squads");
  assert.equal(a.chave, "accoes:não consigo ler o registo das squads");
  assert.equal(accoesAlerta(st([EXE])).chave, a.chave);
});

test("accoesAlerta: outro aviso com prefixo", () => {
  assert.deepEqual(accoesAlerta(st(["erro no colector: OSError"])),
    { chave: "accoes:erro no colector: OSError", texto: "Serviço de ações: erro no colector: OSError" });
});

test("accoesAlerta: (e mais N), com o registo na posição do primeiro", () => {
  const a = accoesAlerta(st(["erro no executor: ValueError", COL, "outro", EXE]));
  assert.equal(a.texto, "Serviço de ações: erro no executor: ValueError (e mais 2)");
  assert.equal(a.chave, "accoes:erro no executor: ValueError");
  assert.equal(accoesAlerta(st([COL, "outro", EXE])).texto, "Serviço de ações: não consigo ler o registo das squads (e mais 1)");
});

test("accoesAlerta: chave estável entre polls", () => {
  const a = accoesAlerta(st(["x", "y"])), b = accoesAlerta({ ...st(["x", "y", "z"]), tasks: [{ id: "t" }] });
  assert.equal(a.chave, b.chave);
  assert.notEqual(a.texto, b.texto);
  assert.equal(accoesAlerta(st([], true)).chave, accoesAlerta({ accoes: { avisos: [], em: EM, parado: true, desde: "14:00" } }).chave);
});

// ---- accoesPasso (CA-18, CA-19)

const X = { chave: "accoes:x", texto: "Serviço de ações: x" };

test("accoesPasso: aparece na 1.ª carga", () => {
  assert.deepEqual(accoesPasso(null, X, null), { op: "mostrar", fechada: null });
  assert.deepEqual(accoesPasso(null, null, null), { op: "nada", fechada: null });
});

test("accoesPasso: mantém sem duplicar", () => {
  assert.deepEqual(accoesPasso(X, { ...X }, null), { op: "manter", fechada: null });
  // fila: o item continua a ser um só
  const F = fila([], { tipo: "alerta", accoes: true, ...X }, 0);
  assert.equal(F.length, 1);
});

test("accoesPasso: actualiza quando o texto muda", () => {
  assert.deepEqual(accoesPasso(X, { ...X, texto: X.texto + " (e mais 1)" }, null), { op: "actualizar", fechada: null });
});

test("accoesPasso: sai sozinho", () => {
  assert.deepEqual(accoesPasso(X, null, null), { op: "tirar", fechada: null });
});

test("accoesPasso: fechado fica escondido com a mesma chave", () => {
  assert.deepEqual(accoesPasso(null, X, X.chave), { op: "nada", fechada: X.chave });
  assert.deepEqual(accoesPasso(null, { ...X, texto: "outro texto" }, X.chave), { op: "nada", fechada: X.chave });
});

test("accoesPasso: volta com chave nova", () => {
  assert.deepEqual(accoesPasso(null, { chave: "accoes:y", texto: "y" }, X.chave), { op: "mostrar", fechada: null });
  assert.deepEqual(accoesPasso(X, { chave: "accoes:y", texto: "y" }, null), { op: "mostrar", fechada: null });
});

test("accoesPasso: volta depois de sair e voltar", () => {
  const p1 = accoesPasso(null, null, X.chave); // fechado e o problema desapareceu
  assert.deepEqual(p1, { op: "nada", fechada: null });
  assert.deepEqual(accoesPasso(null, X, p1.fechada), { op: "mostrar", fechada: null });
});

// ---- cartão (CA-20)

test("html do alerta escapa o texto e só tem Fechar", () => {
  const [a] = fila([], { tipo: "alerta", accoes: true, chave: "accoes:<b>", texto: 'Serviço de ações: <img src=x onerror="1">' }, 0);
  assert.equal(a.tipo, "alerta");
  const h = accoesHtml(a, esc);
  assert.equal(h, '<p>Serviço de ações: &lt;img src=x onerror=&quot;1&quot;&gt;</p><button type="button" data-a="fechar">Fechar</button>');
  assert.equal((h.match(/<button/g) || []).length, 1);
  assert.doesNotMatch(h, /data-a="ir"|<a /);
});
