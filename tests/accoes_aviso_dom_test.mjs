// Alerta do serviço de ações no DOM (SPEC lite-accoes-aviso, CA-18…CA-21): avisos.accoes(s) com um DOM mínimo falso,
// sem browser. Cada teste importa uma instância nova do módulo (estado do módulo isolado). node --test tests/accoes_aviso_dom_test.mjs
import test from "node:test";
import assert from "node:assert/strict";

const REG = "registo de squads ilegível; nada colhido nesta passagem";
const st = (avisos, parado = false, desde = "13:23") => ({ tasks: [], accoes: { avisos, em: "2026-11-18T13:23:00+00:00", parado, desde } });

function domFalso() {
  const el = (tag) => {
    const e = { tag, children: [], dataset: {}, attrs: {}, innerHTML: "", parent: null, ouvintes: {},
      setAttribute(k, v) { this.attrs[k] = v; },
      append(c) { c.parent = this; this.children.push(c); },
      remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; },
      addEventListener(t, f) { this.ouvintes[t] = f; } };
    return e;
  };
  const body = el("body");
  globalThis.document = { hidden: true, body, createElement: el };
  return body;
}

let n = 0;
async function montar() {
  const body = domFalso();
  const av = await import(`../static/avisos.js?dom=${++n}`);
  const notificacoes = [];
  av.iniciar({ esc: (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`), ir() {},
    notificar: (...a) => notificacoes.push(a), ligadas: () => true });
  const box = body.children[0];
  const cartoes = () => box.children.map((c) => ({ c: c.dataset.c, role: c.attrs.role, classe: c.className, html: c.innerHTML }));
  // «Fechar»: o mesmo caminho que um clique no botão do cartão
  const fechar = (chave) => {
    const cartao = box.children.find((c) => c.dataset.c === chave);
    const botao = { dataset: { a: "fechar" }, closest: (sel) => (sel === "[data-a]" ? botao : cartao) };
    box.ouvintes.click({ target: botao });
  };
  return { av, box, cartoes, fechar, notificacoes };
}

test("aparece na 1.ª carga, um só cartão em polls seguidos, sem notificação (CA-18, CA-20)", async () => {
  const { av, box, cartoes, notificacoes } = await montar();
  av.accoes(st([REG]));
  assert.equal(box.id, "avisos");
  assert.deepEqual(cartoes(), [{ c: "accoes:não consigo ler o registo das squads", role: "alert", classe: "av av-a",
    html: '<p>Serviço de ações: não consigo ler o registo das squads</p><button type="button" data-a="fechar">Fechar</button>' }]);
  const el = box.children[0];
  for (let i = 0; i < 5; i++) av.accoes(st([REG]));
  assert.equal(box.children.length, 1);
  assert.equal(box.children[0], el);   // o mesmo elemento: não volta a ser anunciado
  assert.doesNotMatch(el.innerHTML, /×/);
  assert.deepEqual(notificacoes, []);
});

test("muda de texto quando o conteúdo muda e sai sozinho (CA-18)", async () => {
  const { av, box, cartoes } = await montar();
  av.accoes(st(["x"]));
  const el = box.children[0];
  av.accoes(st(["x", "y"]));
  assert.equal(box.children[0], el);
  assert.match(cartoes()[0].html, /Serviço de ações: x \(e mais 1\)/);
  av.accoes(st(["z"]));   // chave nova: troca o cartão
  assert.deepEqual(cartoes().map((c) => c.c), ["accoes:z"]);
  av.accoes(st([]));
  assert.deepEqual(cartoes(), []);
  av.accoes({ tasks: [] });
  assert.deepEqual(cartoes(), []);
});

test("parado e sem problemas (CA-17, CA-21)", async () => {
  const { av, cartoes } = await montar();
  av.accoes(st([]));
  av.accoes({ tasks: [] });
  assert.deepEqual(cartoes(), []);
  av.accoes(st([], true, "09:41"));
  assert.match(cartoes()[0].html, /Serviço de ações parado desde 09:41/);
});

test("Fechar esconde enquanto a chave se mantém; volta com chave nova ou depois de sair (CA-19)", async () => {
  const { av, cartoes, fechar } = await montar();
  av.accoes(st(["x"]));
  fechar("accoes:x");
  assert.deepEqual(cartoes(), []);
  av.accoes(st(["x"]));
  av.accoes(st(["x", "y"]));   // mesma chave, texto diferente: continua fechado
  assert.deepEqual(cartoes(), []);
  av.accoes(st(["w"]));   // chave nova
  assert.deepEqual(cartoes().map((c) => c.c), ["accoes:w"]);
  fechar("accoes:w");
  av.accoes(st([]));   // o problema sai…
  av.accoes(st(["w"]));   // …e volta
  assert.deepEqual(cartoes().map((c) => c.c), ["accoes:w"]);
});

test("tirado pelo tecto de 4 cartões conta como fechado (CA-19)", async () => {
  const { av, cartoes } = await montar();
  av.accoes(st(["x"]));
  for (let i = 0; i < 4; i++) av.mostrar({ tipo: "alerta", chave: `exec:${i}`, item: { tipo: "exec", quem: "p", oque: "erro" } });
  assert.ok(!cartoes().some((c) => c.c === "accoes:x"));
  av.accoes(st(["x"]));
  assert.ok(!cartoes().some((c) => c.c === "accoes:x"));
  av.accoes(st(["y"]));
  assert.ok(cartoes().some((c) => c.c === "accoes:y"));
});

test("texto por esc (CA-20)", async () => {
  const { av, cartoes } = await montar();
  av.accoes(st(["<img src=x>"]));
  assert.match(cartoes()[0].html, /&#60;img src=x&#62;/);
  assert.doesNotMatch(cartoes()[0].html, /<img/);
});
