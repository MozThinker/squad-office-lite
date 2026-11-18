// QA lite-v3: funções puras exportadas que não tinham teste (CA-8, 13, 14, 26). node --test tests/v3_qa_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import "../static/act.js";

const AV = await import("../static/avisos.js");
const L = await import("../static/lateral.js");

test("CA-13/14: «Responder» de um PR só com https://github.com/<dono>/<repo>/pull/<n>", () => {
  assert.equal(AV.prNum("https://github.com/exemplo/demo/pull/31"), "31");
  for (const u of [null, undefined, "", "javascript:alert(1)//github.com/a/b/pull/1", "http://github.com/a/b/pull/1",
    "https://github.com.evil.example/a/b/pull/1", "https://github.com/a/b/pull/1?x=1", "https://github.com/a/b/pull/1/files",
    "https://github.com/a/b/issues/1", "https://github.com/a/b/pull/1\n", " https://github.com/a/b/pull/1"]) {
    assert.equal(AV.PR_URL.test(u ?? ""), false, String(u));
    assert.equal(AV.prNum(u), null, String(u));
  }
});

test("CA-14: junta a mesma chave em 20 s, informativo sai aos 5 s, no máximo 4 no ecrã", () => {
  assert.deepEqual([AV.JUNTA_MS, AV.INFO_MS, AV.MAX], [20000, 5000, 4]);
});

test("CA-8: «P» usa o 1.º item de espera.itens (o servidor ordena do mais antigo), sem recusas", () => {
  const itens = [{ tipo: "recusa", chave: "r" }, { tipo: "pr", chave: "pr:a/T1", pr_url: "https://github.com/a/b/pull/2", desde: "2026-11-10T09:00:00+00:00" },
    { tipo: "aprovacao", chave: "apr:g/x.md", fonte: "g", titulo: "X", desde: "2026-11-12T00:00:00+00:00" }];
  assert.deepEqual(AV.esperaDe({ espera: { n: 2, itens } }).map((x) => x.chave), ["pr:a/T1", "apr:g/x.md"]);
  assert.deepEqual(AV.esperaDe(null), []);
  assert.deepEqual(AV.esperaDe({ espera: { n: 1, itens: "x" } }), []);
});

test("CA-26: a pesquisa compara sem acentos nem maiúsculas", () => {
  assert.equal(L.fold("Acção ÉPICA Ç"), "accao epica c");
  assert.equal(L.fold(null), "");
  assert.equal(L.fold("Revisão").includes(L.fold("REVISAO")), true);
});
