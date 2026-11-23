// Chave da Conversa (contrato §2.2–2.3; revisão G2): os vectores da Parte A/B (tests/fixtures/conversa_vectores.json) com o WebCrypto do
// node, o canónico igual ao do Python e a IndexedDB num falso em memória. node --test tests/conversa_chave_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as K from "../static/conversa_chave.js";

const V = JSON.parse(readFileSync(new URL("./fixtures/conversa_vectores.json", import.meta.url), "utf8"));
const subtle = globalThis.crypto.subtle;

test("código: normaliza (maiúsculas, sem «-» nem espaços) e recusa o que não é base32 de 24", () => {
  assert.equal(K.normalizarCodigo(V.codigo_mostrado), V.codigo);
  assert.equal(K.normalizarCodigo(" abcd-efgh ijkl-mnop-qrst-uvwx "), V.codigo);
  for (const m of ["", "ABCD", V.codigo + "A", V.codigo.slice(0, 23) + "1", V.codigo.slice(0, 23) + "0", null]) assert.equal(K.normalizarCodigo(m), null, m);
});

test("canónico: igual ao do Python (chaves ordenadas, sem v/criado/ip/ua/assinatura, UTF-8 sem escapes)", () => {
  assert.equal(K.canonico(V.pedido), V.canonico);
  assert.equal(K.canonico({ ...V.pedido, v: 1, criado: "x", ip: "1", ua: "u", assinatura: "a" }), V.canonico);
  // ordenação recursiva dentro das respostas (as listas mantêm a ordem)
  assert.equal(K.canonico({ respostas: [{ pergunta: 0, opcao: [2, 1] }], acao: "r" }), '{"acao":"r","respostas":[{"opcao":[2,1],"pergunta":0}]}');
});

test("vectores: verificador do código; a chave K não sai do código (G2); assinaturas do pedido e do desafio", async () => {
  assert.equal(await K.verificador(subtle, V.codigo, V.sal_office, V.iteracoes), V.verificador);
  const vs = K.hex(await subtle.digest("SHA-256", K.deHex(V.verificador)));
  assert.equal(vs, V.verificador_sha256);
  assert.notEqual(await K.verificador(subtle, V.codigo, V.sal_ponte, V.iteracoes), V.chave);
  assert.equal(K.chave, undefined, "a chave derivada do código deixou de existir");
  const k = await subtle.importKey("raw", K.deHex(V.chave), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  assert.equal(await K.assinar(subtle, k, V.pedido), V.assinatura);
  assert.equal(await K.assinarEntrada(subtle, k, V.desafio), V.assinatura_entrar);
});

test("novaChave: 32 bytes aleatórios → kid de 16 hex, o hex para o registo e uma CryptoKey não extraível; os bytes zeram-se", async () => {
  const visto = [];
  const rnd = (a) => { a.set(a.length === 32 ? K.deHex(V.chave) : K.deHex(V.kid)); visto.push(a); return a; };
  const n = await K.novaChave(subtle, rnd);
  assert.equal(n.kid, V.kid);
  assert.equal(n.hexa, V.chave);
  assert.equal(n.chave.extractable, false);
  assert.deepEqual(n.chave.usages, ["sign"]);
  await assert.rejects(subtle.exportKey("raw", n.chave));
  assert.equal(await K.assinar(subtle, n.chave, V.pedido), V.assinatura);
  assert.ok(visto[0].every((b) => b === 0), "os bytes de K não ficam em memória");
  const a = await K.novaChave(subtle), b = await K.novaChave(subtle);
  assert.match(a.kid, /^[0-9a-f]{16}$/);
  assert.match(a.hexa, /^[0-9a-f]{64}$/);
  assert.notEqual(a.hexa, b.hexa);
});

test("assinado: pid de 16 hex, t inteiro e a assinatura do canónico com pid e t", async () => {
  const k = await subtle.importKey("raw", K.deHex(V.chave), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const { pid, t, ...campos } = V.pedido;
  const rnd = (a) => a.set(K.deHex(pid)) || a;
  const d = await K.assinado(subtle, k, campos, t + 0.7, rnd);
  assert.deepEqual(d, { ...V.pedido, assinatura: V.assinatura });
  assert.match(K.aleatorioHex(16), /^[0-9a-f]{16}$/);
});

test("novoId: AAAAMMDD-HHMMSS-<6 hex> na hora local", () => {
  const d = new Date(2026, 10, 23, 9, 5, 7).getTime();
  assert.equal(K.novoId(d, (a) => a.fill(0xab)), "20261123-090507-ababab");
  assert.match(K.novoId(), /^\d{8}-\d{6}-[0-9a-f]{6}$/);
});

// IndexedDB falsa: open/transaction/objectStore com respostas assíncronas
function falsaIdb() {
  const dados = new Map();
  const req = (f) => { const r = {}; queueMicrotask(() => { r.result = f(); r.onsuccess?.(); }); return r; };
  return { dados, open() {
    const r = {};
    queueMicrotask(() => {
      r.result = { createObjectStore() {}, close() {}, transaction() {
        const tx = {}, store = { get: (k) => req(() => dados.get(k)), put: (v, k) => req(() => dados.set(k, v)), delete: (k) => req(() => dados.delete(k)) };
        setTimeout(() => tx.oncomplete?.());
        return Object.assign(tx, { objectStore: () => store });
      } };
      if (!dados.size) r.onupgradeneeded?.();
      r.onsuccess?.();
    });
    return r;
  } };
}

test("IndexedDB: guarda {chave, kid}; a de antes ({chave, sal}) não serve; apagar limpa; erro → null", async () => {
  const idb = falsaIdb(), k = { tipo: "CryptoKey" };
  assert.equal(await K.lerChave(idb), null);
  await K.guardarChave(idb, k, V.kid);
  assert.deepEqual(idb.dados.get("hmac"), { chave: k, kid: V.kid });
  assert.deepEqual(await K.lerChave(idb), { chave: k, kid: V.kid });
  idb.dados.set("hmac", { chave: k, sal: V.sal_ponte }); // a chave derivada do código (antes do G2): pede-se o registo
  assert.equal(await K.lerChave(idb), null);
  await K.guardarChave(idb, k, V.kid);
  await K.apagarChave(idb);
  assert.equal(await K.lerChave(idb), null);
  assert.equal(await K.lerChave({ open() { throw new Error("modo privado"); } }), null);
});

test("o módulo não guarda o código nem a chave em localStorage nem na rede", () => {
  const src = readFileSync(new URL("../static/conversa_chave.js", import.meta.url), "utf8");
  assert.doesNotMatch(src, /localStorage|sessionStorage|fetch\(|XMLHttpRequest|exportKey/);
  assert.match(src, /false, \["sign"\]/);
});
