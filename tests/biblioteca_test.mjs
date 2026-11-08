// Testes das funções puras de static/biblioteca.js (node --test tests/biblioteca_test.mjs).
// O import em si prova que o módulo não toca em window/document ao carregar.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  parseRota, rotaHash, normaliza, filtra, contagens, fmtBytes, fmtData, fmtDataHora, fmtDur, versao, humaniza,
  semFrontmatter, ficha, conteudo, resumoEstado, seccoes, umDoc, safeU, mediaRecente, zipVerificaU, zipRecente, ZIP_OK, FILTROS0, PRODUTOS, TIPOS,
  configurar, produtosCfg, fontesCfg,
} from "../static/biblioteca.js";

// produtos e fontes vêm de /api/config (configurar): os testes usam uma config com 3 projectos
const CFG = { projectos: [{ id: "loja", nome: "Loja" }, { id: "ebook", nome: "E-book" }, { id: "tictag", nome: "TicTag" }],
  biblioteca: [{ id: "material", rotulo: "Material" }, { id: "estudos", rotulo: "Estudos" }] };
configurar(CFG);

const F = (m = {}) => ({ ...FILTROS0, ...m });

test("parseRota: só hashes que começam por biblioteca; lista, filtros e item", () => {
  assert.equal(parseRota("#20261101-2037-office-biblioteca"), null);
  assert.equal(parseRota(""), null);
  assert.deepEqual(parseRota("#biblioteca"), { vista: "lista", id: null, filtros: F() });
  assert.deepEqual(parseRota("biblioteca/"), { vista: "lista", id: null, filtros: F() });
  assert.deepEqual(parseRota("#biblioteca?produto=loja&tipo=imagens&data=7d&q=mascote"),
    { vista: "lista", id: null, filtros: F({ produto: "loja", tipo: "imagens", data: "7d", q: "mascote" }) });
  assert.deepEqual(parseRota("#biblioteca/rascunhos/media/2026-11-01-status-ana"),
    { vista: "item", id: "rascunhos/media/2026-11-01-status-ana", filtros: F() });
});

test("parseRota: valores desconhecidos caem no omisso; %-codificação inválida volta à lista", () => {
  assert.deepEqual(parseRota("#biblioteca?produto=constructor&tipo=__proto__&data=48h&q=" + "x".repeat(200)).filtros,
    F({ q: "x".repeat(100) }));
  assert.deepEqual(parseRota("#biblioteca/a%ZZ/b"), { vista: "lista", id: null, filtros: F() });
});

test("rotaHash ↔ parseRota: ida e volta com acentos, espaços, «/» e query", () => {
  for (const id of ["rascunhos/media/2026-11-01-status-ana/final/status-01-quem-e-loja-9x16.jpg",
    "rnd-estudos/peça é ção #1?.md", "tarefas/inbox/plano (v2) & mais.md", "aprovacoes-marketing/2026-11-01-x.md"]) {
    const h = rotaHash({ vista: "item", id });
    assert.ok(h.startsWith("#biblioteca/"));
    assert.ok(!/[ #?&]/.test(h.slice(1)), h);
    assert.equal(parseRota(h).id, id);
  }
  const f = F({ produto: "ebook", tipo: "videos", data: "hoje", q: "mascôte estado & cia" });
  assert.deepEqual(parseRota(rotaHash({ vista: "lista", filtros: f })).filtros, f);
  assert.equal(rotaHash({ vista: "lista", filtros: F() }), "#biblioteca");
  assert.equal(rotaHash({ filtros: F({ data: "30d" }) }), "#biblioteca?data=30d");
  assert.equal(rotaHash({ vista: "item", id: "rnd-estudos/a b.md" }), "#biblioteca/rnd-estudos/a%20b.md");
});

test("normaliza: sem acentos, minúsculas, espaços simples", () => {
  assert.equal(normaliza("  MASCOTE  "), "mascote");
  assert.equal(normaliza("mascôte\tÉ  Ção"), "mascote e cao");
  assert.equal(normaliza(null), "");
});

const H = 3600, agora = new Date(2026, 10, 1, 12, 0, 0).getTime(), s = agora / 1000;
const ITENS = [
  { id: "a", titulo: "Estado do WhatsApp pessoal do Ana, série do mascote Loja", sub: "2026-11-01-status-ana",
    q: "estado do whatsapp pessoal do ana serie do mascote loja 2026-11-01-status-ana status-01.jpg",
    produtos: ["loja"], tipos: ["imagens", "videos"], actualizado: s - H },
  { id: "b", titulo: "Bilhetes com QR", q: "bilhetes com qr", produtos: ["geral"], tipos: ["estudos"], actualizado: s - 3 * 24 * H },
  { id: "c", titulo: "Cookies aviso", q: "cookies aviso", produtos: ["ebook", "loja"], tipos: ["textos", "pareceres"], actualizado: s - 20 * 24 * H },
  { id: "d", titulo: "Lote BFPT", q: "lote bfpt", produtos: ["ebook"], tipos: ["imagens"], actualizado: s - 40 * 24 * H },
];
const ids = (l) => l.map((x) => x.id);

test("filtra: produto × tipo, todas as palavras sem acentos, título e nome do produto", () => {
  assert.deepEqual(ids(filtra(ITENS, F(), agora)), ["a", "b", "c", "d"]);
  assert.deepEqual(ids(filtra(ITENS, F({ produto: "loja" }), agora)), ["a", "c"]);
  assert.deepEqual(ids(filtra(ITENS, F({ produto: "ebook", tipo: "imagens" }), agora)), ["d"]);
  for (const q of ["mascote estado", "MASCOTE", "mascôte", "série", "status-01"]) assert.deepEqual(ids(filtra(ITENS, F({ q }), agora)), ["a"], q);
  assert.deepEqual(ids(filtra(ITENS, F({ q: "mascote xyz" }), agora)), []);
  assert.deepEqual(ids(filtra(ITENS, F({ q: "e-book" }), agora)), ["c", "d"]); // rótulo do produto também se pesquisa
  assert.deepEqual(filtra(null, F(), agora), []);
});

test("filtra: data hoje / 7 d / 30 d / tudo (agora injectado)", () => {
  assert.deepEqual(ids(filtra(ITENS, F({ data: "hoje" }), agora)), ["a"]);
  assert.deepEqual(ids(filtra(ITENS, F({ data: "7d" }), agora)), ["a", "b"]);
  assert.deepEqual(ids(filtra(ITENS, F({ data: "30d" }), agora)), ["a", "b", "c"]);
  assert.deepEqual(ids(filtra(ITENS, F({ data: "tudo" }), agora)), ["a", "b", "c", "d"]);
  const ontem = { id: "o", actualizado: new Date(2026, 9, 31, 23, 59).getTime() / 1000 };
  assert.deepEqual(filtra([ontem], F({ data: "hoje" }), agora), []);
});

test("contagens facetadas: o próprio grupo não conta, o outro e a pesquisa sim", () => {
  const c0 = contagens(ITENS, F(), agora);
  assert.equal(c0.produto.todos, 4);
  assert.equal(c0.produto.loja, 2);
  assert.equal(c0.produto.ebook, 2);
  assert.equal(c0.produto.tictag, 0);
  assert.equal(c0.tipo.imagens, 2);
  assert.deepEqual(Object.keys(c0.produto), ["todos", ...PRODUTOS.map(([k]) => k)]);
  assert.deepEqual(Object.keys(c0.tipo), ["todos", ...TIPOS.map(([k]) => k)]);
  // com Loja activo: Imagens conta só os Loja com imagens; o chip Loja não muda
  const c1 = contagens(ITENS, F({ produto: "loja" }), agora);
  assert.equal(c1.tipo.imagens, 1);
  assert.equal(c1.tipo.todos, 2);
  assert.equal(c1.produto.loja, c0.produto.loja);
  assert.equal(c1.produto.todos, 4);
  // tipo activo muda as contagens de produto
  const c2 = contagens(ITENS, F({ tipo: "imagens" }), agora);
  assert.equal(c2.produto.ebook, 1);
  assert.equal(c2.produto.geral, 0);
  // pesquisa e data entram nos dois grupos
  const c3 = contagens(ITENS, F({ q: "cookies", data: "30d" }), agora);
  assert.equal(c3.produto.todos, 1);
  assert.equal(c3.tipo.pareceres, 1);
  // produto repetido num item conta uma vez
  assert.equal(contagens([{ produtos: ["geral", "geral"], tipos: [] }], F(), agora).produto.geral, 1);
});

test("fmtBytes: PT-PT com vírgula", () => {
  assert.equal(fmtBytes(0), "0 B");
  assert.equal(fmtBytes(512), "512 B");
  assert.equal(fmtBytes(985 * 1024), "985 KB");
  assert.equal(fmtBytes(1782000), "1,7 MB");
  assert.equal(fmtBytes(1023.9 * 1024), "1,0 MB");
  assert.equal(fmtBytes(24117000), "23 MB");
  assert.equal(fmtBytes(1.5 * 1024 ** 3), "1,5 GB");
  assert.equal(fmtBytes("x"), "0 B");
});

test("datas e durações em hora local", () => {
  const t = new Date(2026, 10, 1, 9, 5).getTime() / 1000;
  assert.equal(fmtData(t), "1/11/2026");
  assert.equal(fmtDataHora(t), "01/11 09:05");
  assert.equal(fmtDur(21.4), "21 s");
  assert.equal(fmtDur(72), "1 min 12 s");
  assert.equal(fmtDur(120), "2 min");
});

test("versao e humaniza", () => {
  assert.equal(versao("status-03-antes-depois-v2-9x16.jpg"), "v2");
  assert.equal(versao("status-03-antes-depois-v3.jpg"), "v3");
  assert.equal(versao("status-01-quem-e-loja-9x16.jpg"), null);
  assert.equal(versao("nivel-v2x.jpg"), null);
  assert.equal(humaniza("status-01-quem-e-loja-9x16.jpg"), "status 01 quem e loja 9x16");
  assert.equal(humaniza("folha_do_lote.md"), "folha do lote");
});

test("semFrontmatter e ficha: sem frontmatter cru, só chaves conhecidas", () => {
  const doc = "---\nestado: aprovado\nproduto: \"loja\"\ncriado: 2026-11-01\nsha: abc\n__proto__: x\nlista:\n  - a\n---\n\n# Título\n\ncorpo\n";
  const { front, corpo } = semFrontmatter(doc);
  assert.equal(corpo, "# Título\n\ncorpo\n");
  assert.equal(front.estado, "aprovado");
  assert.equal(front.produto, "loja");
  assert.equal(Object.getPrototypeOf(front), Object.prototype);
  assert.deepEqual(ficha(front), [["estado", "aprovado"], ["produto", "loja"], ["criado", "2026-11-01"]]);
  assert.deepEqual(semFrontmatter("# Sem ficha\n---\nx: y\n---\n"), { front: {}, corpo: "# Sem ficha\n---\nx: y\n---\n" });
  assert.deepEqual(semFrontmatter("﻿---\r\nestado: pendente\r\n---\r\ntexto"), { front: { estado: "pendente" }, corpo: "texto" });
  assert.deepEqual(semFrontmatter("---\nsem fecho\n"), { front: {}, corpo: "---\nsem fecho\n" });
  assert.deepEqual(ficha({}), []);
  assert.deepEqual(ficha({ revisor: "  " }), []);
});

test("conteudo e resumoEstado", () => {
  assert.equal(conteudo({ conta: { imagem: 5, video: 3, audio: 0, doc: 0 }, bytes: 17.4 * 1024 ** 2 }), "5 imagens, 3 vídeos · 17 MB");
  assert.equal(conteudo({ conta: { imagem: 1, video: 1, audio: 1, doc: 1 }, bytes: 2048 }), "1 imagem, 1 vídeo, 1 áudio, 1 documento · 2 KB");
  assert.equal(conteudo({ conta: { doc: 1 }, bytes: 9100 }), "1 documento");
  assert.equal(resumoEstado({ aprovado: 5, executado: 1, bloqueado: 0, sem_aprovacao: 8 }), "6 aprovadas · 8 fora do pedido");
  assert.equal(resumoEstado({ aprovado: 1, bloqueado: 1, pendente: 2 }), "1 aprovada · 2 pendentes · 1 bloqueada");
  assert.equal(resumoEstado(null), "");
});

test("seccoes: media do destaque na grelha, textos pelas secções do UX", () => {
  const d = {
    destaque: [{ tipo: "imagem" }, { tipo: "video" }, { tipo: "doc", nome: "x.md" }],
    textos: [{ rotulo: "Copy" }, { rotulo: "Texto" }, { rotulo: "Revisão" }, { rotulo: "Parecer jurídico" }, { rotulo: "Folha" }, { rotulo: "Spec" }],
    trabalho: [{ pasta: "prompts", ficheiros: [{}, {}] }, { pasta: "", ficheiros: [{}] }],
    pedidos: [{ fonte: "marketing" }],
  };
  const x = seccoes(d);
  assert.equal(x.media.length, 2);
  assert.equal(x.docs.length, 2);
  assert.equal(x.textos.length, 2);
  assert.equal(x.revisao.length, 2);
  assert.equal(x.folha.length, 1);
  assert.equal(x.pedidos.length, 1);
  assert.equal(x.trabalho, 3);
  assert.deepEqual(seccoes(null), { media: [], docs: [], textos: [], revisao: [], folha: [], pedidos: [], trabalho: 0 });
});

test("umDoc e safeU", () => {
  assert.equal(umDoc({ n: 1, principal: { url: "u" } }), true);
  assert.equal(umDoc({ n: 2, principal: { url: "u" } }), false);
  assert.equal(umDoc({ n: 1, principal: null }), false);
  assert.equal(safeU("api/biblioteca/f/rascunhos/a%20b.jpg?v=1&dl=1"), "api/biblioteca/f/rascunhos/a%20b.jpg?v=1&dl=1");
  for (const u of ["javascript:alert(1)", "//evil/x", "/api/biblioteca/f/x", "https://x", "api/biblioteca/f/a\"onerror=x", "api/state", null])
    assert.equal(safeU(u), "", String(u));
});

test("mediaRecente: lotes e pastas com imagens/vídeos, mais recentes primeiro, até 6", () => {
  const it = (id, genero, actualizado, conta = {}) => ({ id, genero, actualizado, conta });
  const l = [
    it("tarefa", "tarefa", 100), it("plano", "plano", 99), it("doc", "documento", 98),
    it("lote-velho", "lote", 1), it("pasta-docs", "pasta", 97, { doc: 3 }), it("pasta-img", "pasta", 50, { imagem: 2 }),
    it("pasta-vid", "pasta", 40, { video: 1 }), it("lote-mascote", "lote", 90, { imagem: 10, video: 3 }),
    ...[1, 2, 3, 4, 5].map((n) => it("lote-" + n, "lote", 10 + n)),
  ];
  assert.deepEqual(ids(mediaRecente(l)), ["lote-mascote", "pasta-img", "pasta-vid", "lote-5", "lote-4", "lote-3"]);
  assert.deepEqual(ids(mediaRecente(l, 2)), ["lote-mascote", "pasta-img"]);
  assert.deepEqual(mediaRecente(l.slice(0, 3)), []);
  assert.deepEqual(mediaRecente(null), []);
  assert.equal(l[0].id, "tarefa"); // não reordena a lista original
});

test("zip: URL de verificação só para api/biblioteca e validade da verificação", () => {
  assert.equal(zipVerificaU("api/biblioteca/zip?id=rascunhos%2Fmedia%2Fx"), "api/biblioteca/zip?id=rascunhos%2Fmedia%2Fx&verificar=1");
  assert.equal(zipVerificaU("api/biblioteca/zip"), "api/biblioteca/zip?verificar=1");
  for (const u of ["https://evil/zip?id=1", "/api/biblioteca/zip?id=1", null]) assert.equal(zipVerificaU(u), "", String(u));
  const t = 1_000_000;
  assert.equal(zipRecente(String(t), t + 1), true);
  assert.equal(zipRecente(String(t), t + ZIP_OK - 1), true);
  assert.equal(zipRecente(String(t), t + ZIP_OK), false);
  for (const ok of [undefined, "", "1x", "0"]) assert.equal(zipRecente(ok, t), false, String(ok));
});

test("config: produtos = projectos da config + geral; fontes = rótulos da config + tarefas; sem config só geral", () => {
  assert.deepEqual(PRODUTOS, [["loja", "Loja"], ["ebook", "E-book"], ["tictag", "TicTag"], ["geral", "Geral"]]);
  assert.deepEqual(produtosCfg(null), [["geral", "Geral"]]);
  assert.deepEqual(produtosCfg({}), [["geral", "Geral"]]);
  assert.deepEqual(fontesCfg(CFG), { material: "Material", estudos: "Estudos", tarefas: "tarefas" });
  assert.deepEqual(fontesCfg(undefined), { tarefas: "tarefas" });
  const fx = JSON.parse(readFileSync(new URL("./fixtures/api_config_exemplo.json", import.meta.url), "utf8"));
  assert.deepEqual(produtosCfg(fx), [["exemplo", "Exemplo"], ["geral", "Geral"]]);
  assert.deepEqual(fontesCfg(fx), { material: "Material", tarefas: "tarefas" });
  // produto fora da config cai no omisso (como antes um desconhecido)
  configurar(fx);
  try { assert.equal(parseRota("#biblioteca?produto=loja").filtros.produto, null); assert.equal(parseRota("#biblioteca?produto=exemplo").filtros.produto, "exemplo"); }
  finally { configurar(CFG); }
});
