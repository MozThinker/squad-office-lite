// QA das funções puras de static/quadro.js: fronteiras que faltavam (node --test tests/quadro_qa_test.mjs).
process.env.TZ = "Europe/Lisbon";
import test from "node:test";
import assert from "node:assert/strict";
import { idade, atrasado, validaForm, limpaTexto, moverLocal, porColuna, contagens, filtra, MSG409 } from "../static/quadro.js";

const COLUNAS = ["ideias", "a-fazer", "em-curso", "a-espera", "feito"].map((chave) => ({ chave, rotulo: chave }));
const man = (id, coluna, ordem, m = {}) => ({ id, tipo: "manual", fonte: "manual", projecto: "loja", titulo: id, coluna, ordem, etiquetas: [], antigo: false, ...m });
const AGORA = Date.parse("2026-11-04T15:00:00+00:00");
const ha = (ms) => new Date(AGORA - ms).toISOString();
const S = 1e3, H = 3600e3, DIA = 24 * H;

test("idade: fronteiras de minuto, hora, 6 h no mesmo dia e dias", () => {
  assert.equal(idade(ha(59 * S), AGORA), "agora");
  assert.equal(idade(ha(60 * S), AGORA), "há 1 min");
  assert.equal(idade(ha(H - S), AGORA), "há 59 min");
  assert.equal(idade(ha(H), AGORA), "há 1 h");
  assert.equal(idade(ha(6 * H - S), AGORA), "há 5 h");
  assert.equal(idade(ha(6 * H), AGORA), "hoje");               // 09:00 do mesmo dia (Lisboa = UTC em Novembro)
  assert.equal(idade(ha(DIA - S), AGORA), "há 23 h");          // ontem às 15:00:01
  assert.equal(idade(ha(DIA), AGORA), "há 1 dia");
  assert.equal(idade(ha(2 * DIA - S), AGORA), "há 1 dia");
  assert.equal(idade(ha(2 * DIA), AGORA), "há 2 dias");
});

test("atrasado: 3 dias exactos ainda não; manual em À espera conta pelo desde (= actualizado)", () => {
  assert.equal(atrasado(man("m", "a-espera", 10, { desde: ha(3 * DIA) }), AGORA), false);
  assert.equal(atrasado(man("m", "a-espera", 10, { desde: ha(3 * DIA + 1) }), AGORA), true);
  assert.equal(atrasado(man("m", "ideias", 10, { desde: ha(30 * DIA) }), AGORA), false);
  assert.equal(atrasado(null, AGORA), false);
});

test("moverLocal: posição negativa vai para o topo; mover para o mesmo sítio só renumera", () => {
  const l = [man("a", "ideias", 5), man("b", "ideias", 7), man("c", "ideias", 100)];
  assert.deepEqual(porColuna(moverLocal(l, "c", "ideias", -3), COLUNAS).ideias.map((c) => [c.id, c.ordem]), [["c", 10], ["a", 20], ["b", 30]]);
  assert.deepEqual(porColuna(moverLocal(l, "b", "ideias", 1), COLUNAS).ideias.map((c) => [c.id, c.ordem]), [["a", 10], ["b", 20], ["c", 30]]);
});

test("contagens e filtra: «antigos» de Feito ficam fora do contador mas a pesquisa encontra-os", () => {
  const l = [man("m-1", "feito", 10, { antigo: true, titulo: "Velho" }), man("m-2", "feito", 20, { titulo: "Novo" })];
  assert.deepEqual(contagens(l, { q: "velho" }).col, {});
  assert.deepEqual(filtra(l, { q: "velho" }).map((c) => c.id), ["m-1"]);
});

test("mensagem do 409 é a da SPEC (CA-15)", () => {
  assert.equal(MSG409, "O quadro mudou noutro sítio — recarreguei; repete a acção.");
});

// B4 (corrigido): o formulário limpa o texto antes de enviar e o validaForm recusa o mesmo controlo que o servidor
test("formulário: tab na descrição fica (o servidor aceita); C0 sai antes de enviar", () => {
  const d = limpaTexto("col1\tcol2\u0001", true);
  assert.equal(d, "col1\tcol2");
  assert.deepEqual(validaForm({ titulo: "a", descricao: d, etiquetas: [] }), {});
});
test("validaForm recusa o mesmo que o servidor: controlo C1 (U+0085) no título; limpaTexto tira-o", () => {
  assert.ok(validaForm({ titulo: "a\u0085b", descricao: "", etiquetas: [] }).titulo);
  assert.equal(limpaTexto("a\u0085b"), "ab");
});
