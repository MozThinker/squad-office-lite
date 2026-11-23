// Botão da Conversa no cabeçalho do Office (static/conversa_botao.js; ADR-023, revisão do guardião). node --test tests/conversa_botao_test.mjs
// O import em si prova que o módulo não toca em window/document ao carregar.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { urlDe, rotulo, montar, pintar, SEM_SITE, NOME_OMISSAO } from "../static/conversa_botao.js";

const ler = (f) => readFileSync(new URL("../static/" + f, import.meta.url), "utf8");

test("urlDe: só https e só com a Conversa ligada; o resto (null, http, javascript:, aspas) dá a explicação", () => {
  assert.equal(urlDe({ ativa: true, url: "https://conversa.exemplo.pt/" }), "https://conversa.exemplo.pt/");
  for (const c of [null, {}, { ativa: true, url: null }, { ativa: false, url: "https://c.exemplo.pt/" }, { ativa: true, url: "http://c.exemplo.pt/" },
    { ativa: true, url: "javascript:alert(1)" }, { ativa: true, url: 'https://c.pt/"><img' }, { ativa: true, url: "https://c.pt/ x" }, { ativa: "true", url: "https://c.pt/" }])
    assert.equal(urlDe(c), null, JSON.stringify(c));
});

test("rotulo: nome do publico.json (omissão «Assistente»), contador até «9+» e o estado para leitores de ecrã", () => {
  assert.deepEqual(rotulo(undefined), { nome: NOME_OMISSAO, n: "", aria: "Assistente" });
  assert.deepEqual(rotulo({ ativa: true, nome: "Zé", pendentes: 1 }), { nome: "Zé", n: "1", aria: "Zé, 1 pedido à espera" });
  assert.deepEqual(rotulo({ ativa: true, nome: "Zé", pendentes: 12 }), { nome: "Zé", n: "9+", aria: "Zé, 12 pedidos à espera" });
  assert.equal(rotulo({ ativa: false, nome: "Zé", pendentes: 0 }).aria, "Zé, parada");
  for (const p of [-1, 1.5, "3", null]) assert.equal(rotulo({ ativa: true, pendentes: p }).n, "", String(p));
  assert.equal(rotulo({ nome: "   " }).nome, "Assistente");
});

// DOM falso mínimo: o suficiente para montar() e pintar()
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.attrs = {}; this.kids = []; this.ouvintes = {}; this.hidden = false; this._html = ""; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(n, f) { this.ouvintes[n] = f; }
  set innerHTML(v) { this._html = v; this.kids = [new El("svg"), Object.assign(new El("span"), { className: "lg" }), Object.assign(new El("span"), { className: "cv-n" })]; }
  querySelector(s) { return this.kids.find((k) => "." + k.className === s) || null; }
  before(x) { this.antes = x; }
  append(...x) { this.kids.push(...x); }
}
const head = new El("head");
globalThis.document = { createElement: (t) => new El(t), head, body: new El("body") };

test("montar/pintar: com url é uma ligação de topo (_blank, noopener noreferrer) antes do ⚙; o nome entra por textContent", () => {
  const def = new El("button");
  montar({ ativa: true, url: "https://conversa.exemplo.pt/" }, def);
  const b = def.antes;
  assert.equal(b.tagName, "A");
  assert.deepEqual([b.href, b.target, b.rel, b.id], ["https://conversa.exemplo.pt/", "_blank", "noopener noreferrer", "cv-b"]);
  assert.ok(!b.ouvintes.click, "a ligação navega; não há JS da Conversa no Office");
  assert.match(b._html, /<use href="#i-conversa"\/>/);
  assert.ok(head.kids.some((l) => /conversa_botao\.css$/.test(l.href)));
  pintar({ ativa: true, nome: '<img src=x onerror="x()">', pendentes: 3 });
  assert.equal(b.querySelector(".lg").textContent, '<img src=x onerror="x()">');
  assert.equal(b.querySelector(".cv-n").textContent, "3");
  assert.equal(b.querySelector(".cv-n").hidden, false);
  assert.equal(b.getAttribute("aria-label"), '<img src=x onerror="x()">, 3 pedidos à espera');
  pintar({ ativa: true, pendentes: 0 });
  assert.equal(b.querySelector(".cv-n").hidden, true);
  montar({ ativa: true, url: null }, new El("button")); // uma vez só
});

test("sem url: o botão abre a explicação do site e do DNS (texto fixo, sem dados do servidor)", () => {
  const src = ler("conversa_botao.js");
  assert.match(src, /else \{ b\.type = "button"; b\.setAttribute\("aria-haspopup", "dialog"\); b\.addEventListener\("click", explicar\); \}/);
  assert.match(SEM_SITE.join(" "), /DNS/);
  assert.match(SEM_SITE.join(" "), /conversa\.site/);
  assert.match(SEM_SITE.join(" "), /--dominio/);
  assert.doesNotMatch(src, /fetch\(|api\/conversa|indexedDB|localStorage/);
  // o index.html só importa este módulo com a Conversa ligada e repinta-o a cada poll
  const h = ler("index.html");
  assert.match(h, /cfg\.conversa\?\.ativa === true\) cvB \|\|= import\("\.\/conversa_botao\.js"\)/);
  assert.match(h, /cvB\?\.then\(\(m\) => m\?\.pintar\(state\.conversa\)\);/);
});
