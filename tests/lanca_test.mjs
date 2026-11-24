// Testes de static/lanca.js (ADR-024): selo, botão e estado depois de aprovar de uma linha de «## Decisões».
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import "../static/lanca.js";

const L = globalThis.LANCA;

test("com marca e lançamento ligado: selo ▶, «Aprovar e lançar» e o leitor de ecrã diz a tarefa", () => {
  const d = { id: "L1", texto: "Lançar `x`", lanca: "loja-v2", lancamento: "ligado", valor: null };
  assert.deepEqual(L.selo(d), { icone: "▶", texto: "Aprovar lança a tarefa", slug: "loja-v2", classe: "lanca-on" });
  assert.deepEqual(L.botao(d), { rotulo: "Aprovar e lançar", aria: "Aprovar e lançar a tarefa loja-v2" });
  assert.deepEqual(L.botao({ ...d, valor: "30 €" }), { rotulo: "Aprovar e lançar · 30 €", aria: "Aprovar e lançar a tarefa loja-v2 · 30 €" });
  assert.equal(L.lanca(d), true);
});

test("com marca e lançamento desligado (ou estado desconhecido): «Lançamento desligado: aprovar só regista» e «Aprovar»", () => {
  for (const lancamento of ["desligado", undefined, null, "outro"]) {
    const d = { id: "L1", lanca: "x", lancamento };
    assert.deepEqual(L.selo(d), { icone: "⏸\uFE0E", texto: "Lançamento desligado: aprovar só regista", slug: "x", classe: "lanca-off" });
    assert.deepEqual(L.botao(d), { rotulo: "Aprovar", aria: null });
    assert.equal(L.lanca(d), false);
    assert.equal(L.aprovado(d), "Aprovado");
  }
});

test("sem marca (ou slug inválido vindo do servidor): «Aprovar só regista», discreto, sem ícone", () => {
  for (const lanca of [null, undefined, "", "Maius", "a/b", "-x", 3]) {
    const d = { id: "P1", lanca, lancamento: "ligado" };
    assert.deepEqual(L.selo(d), { icone: "", texto: "Aprovar só regista", slug: null, classe: "lanca-reg" });
    assert.deepEqual(L.botao(d), { rotulo: "Aprovar", aria: null });
    assert.equal(L.aprovado(d), "Aprovado");
  }
  assert.deepEqual(L.botao({ valor: "1 200 MT" }), { rotulo: "Aprovar · 1 200 MT", aria: null });
});

test("depois de aprovada: tarefa lançada, à espera do lançamento ou só «Aprovado»", () => {
  assert.equal(L.aprovado({ lanca: "x", lancamento: "lancada" }), "Aprovado: tarefa lançada");
  assert.equal(L.aprovado({ lanca: "x", lancamento: "espera" }), "Aprovado: à espera do lançamento");
  assert.equal(L.aprovado({ lanca: "x", lancamento: "desligado" }), "Aprovado");
});

test("o index.html carrega o lanca.js antes do script principal e usa-o no cartão (sem a marca crua)", () => {
  const h = readFileSync(new URL("../static/index.html", import.meta.url), "utf8");
  assert.match(h, /<script src="lanca\.js"><\/script>\n<script src="act\.js"><\/script>/);
  const b = h.slice(h.indexOf("// ---- aprovações"), h.indexOf("// ---- fim aprovações"));
  assert.match(b, /LZ\.botao\(d\)/);
  assert.match(b, /aSelo\(d\)/);
  assert.doesNotMatch(b, /\[lança:/);
});
