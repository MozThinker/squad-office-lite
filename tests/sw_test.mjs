// static/sw.js em node:vm com um `self` falso: evento push (SPEC AC-21) e o clique que já existia (node --test tests/sw_test.mjs).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SRC = readFileSync(new URL("../static/sw.js", import.meta.url), "utf8");

function carregar() {
  const ouvintes = {}, mostradas = [], abertas = [];
  const self = {
    location: { origin: "https://squad.example" },
    addEventListener: (n, f) => { ouvintes[n] = f; },
    skipWaiting() {},
    registration: { showNotification: async (title, opts) => { mostradas.push({ title, ...opts }); } },
    clients: { claim: async () => {}, matchAll: async () => [], openWindow: async (u) => { abertas.push(u); } },
  };
  vm.runInNewContext(SRC, { self, URL });
  return { ouvintes, mostradas, abertas };
}

async function push(dados) {
  const sw = carregar();
  let p;
  const data = dados === undefined ? null
    : { json: () => { if (typeof dados !== "string") return dados; return JSON.parse(dados); } };
  sw.ouvintes.push({ data, waitUntil: (x) => { p = x; } });
  await p;
  return sw;
}

test("push válido: title, body, tag e data.task tirado do url; crítico pede interacção", async () => {
  const { mostradas } = await push({ title: "💀 Dev · joga: tarefa morreu", body: "O lançador deixou de dar sinal há mais de 5 min.",
    tag: "20261105-1200-joga-x", url: "./#" + encodeURIComponent("20261105-1200-joga x"), critico: true });
  assert.equal(mostradas.length, 1);
  const n = mostradas[0];
  assert.equal(n.title, "💀 Dev · joga: tarefa morreu");
  assert.equal(n.body, "O lançador deixou de dar sinal há mais de 5 min.");
  assert.equal(n.tag, "20261105-1200-joga-x");
  assert.equal(n.data.task, "20261105-1200-joga x");
  assert.equal(n.requireInteraction, true);
  assert.equal(n.icon, "icon-192.png");
});

test("push com url «./» (alertas, excedente): sem data.task e sem interacção obrigatória", async () => {
  const { mostradas } = await push({ title: "Mais 3 avisos", body: "Abre o Office para ver tudo.", tag: "mais", url: "./", critico: false });
  assert.equal(mostradas[0].data.task, undefined);
  assert.equal(mostradas[0].requireInteraction, false);
});

test("payload inválido (sem dados, JSON partido, sem title) mostra a notificação genérica", async () => {
  const lixo = { json: () => { throw new SyntaxError("x"); } };
  const sw = carregar();
  let p;
  sw.ouvintes.push({ data: lixo, waitUntil: (x) => { p = x; } });
  await p;
  for (const m of [sw.mostradas, (await push(undefined)).mostradas, (await push({ body: "sem título" })).mostradas,
    (await push({ title: 5 })).mostradas]) {
    assert.equal(m.length, 1);
    assert.equal(m[0].title, "Squad Office");
    assert.equal(m[0].body, "Abre o Office para ver o detalhe.");
    assert.equal(m[0].data.task, undefined);
  }
});

test("url com hash mal codificado não parte o push", async () => {
  const { mostradas } = await push({ title: "x", body: "y", tag: "t", url: "./#%E0%A4%A", critico: false });
  assert.equal(mostradas.length, 1);
  assert.equal(mostradas[0].data.task, undefined);
});

test("o clique reutiliza o notificationclick: abre ./#<id> da data.task", async () => {
  const sw = carregar();
  let p, fechada = false;
  sw.ouvintes.notificationclick({ notification: { data: { task: "a b" }, close: () => { fechada = true; } }, waitUntil: (x) => { p = x; } });
  await p;
  assert.ok(fechada);
  assert.deepEqual(sw.abertas, ["./#a%20b"]);
});

// CA22 (SPEC office-push-barra-fases): renotify só com `true` literal; o Chrome rejeita renotify sem tag (o iOS conta push sem aviso)
test("renotify: true só com p.renotify === true (e tag); \"true\", 1, ausente → false", async () => {
  const base = { title: "📍 Dev · Loja: QA", body: "▰▰▰▰▱ Loja · suporte-caixa · QA feita", tag: "fase:x", url: "./#x", critico: false };
  assert.equal((await push({ ...base, renotify: true })).mostradas[0].renotify, true);
  for (const r of [false, "true", 1, null, {}]) assert.equal((await push({ ...base, renotify: r })).mostradas[0].renotify, false, JSON.stringify(r));
  assert.equal((await push(base)).mostradas[0].renotify, false);
  const { tag, ...semTag } = base;
  assert.equal((await push({ ...semTag, renotify: true })).mostradas[0].renotify, false);
  assert.equal((await push({ ...base, tag: "", renotify: true })).mostradas[0].renotify, false);
  assert.equal((await push(undefined)).mostradas[0].renotify, false);
});
