// Arquivo da Biblioteca (ADR-011) no frontend: rota «arquivo=1», corpo do POST, estado do detalhe e respostas do POST.
// node --test tests/biblioteca_arquivo_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { parseRota, rotaHash, FILTROS0, corpoArquivo, estadoArquivo, fmtArquivado, respostaArquivo, FILA_TXT } from "../static/biblioteca.js";

const F = (m = {}) => ({ ...FILTROS0, ...m });

test("rota: «arquivo=1» liga o filtro; outro valor ou ausente não; ida e volta", () => {
  assert.equal(FILTROS0.arquivo, false);
  assert.deepEqual(parseRota("#biblioteca?arquivo=1"), { vista: "lista", id: null, filtros: F({ arquivo: true }) });
  for (const h of ["#biblioteca", "#biblioteca?arquivo=0", "#biblioteca?arquivo=sim", "#biblioteca?arquivo="]) assert.equal(parseRota(h).filtros.arquivo, false, h);
  assert.equal(rotaHash({ filtros: F({ arquivo: true, tipo: "imagens" }) }), "#biblioteca?tipo=imagens&arquivo=1");
  assert.equal(rotaHash({ filtros: F({ arquivo: true }) }), "#biblioteca?arquivo=1");
  assert.equal(rotaHash({ filtros: F() }), "#biblioteca");
  const f = F({ arquivo: true, data: "7d", q: "relatório" });
  assert.deepEqual(parseRota(rotaHash({ vista: "lista", filtros: f })).filtros, f);
  // o item mantém o id original (links antigos): o filtro não entra no hash do item
  assert.equal(rotaHash({ vista: "item", id: "material/x.md", filtros: f }), "#biblioteca/material/x.md");
});

test("corpoArquivo: chaves exactas token, accao, item (por esta ordem)", () => {
  const c = JSON.parse(corpoArquivo("tk", "arquivar", "material/relatorios/x.md"));
  assert.deepEqual(Object.keys(c), ["token", "accao", "item"]);
  assert.deepEqual(c, { token: "tk", accao: "arquivar", item: "material/relatorios/x.md" });
  assert.deepEqual(JSON.parse(corpoArquivo("tk", "repor", "material/media/lote")).accao, "repor");
});

test("fmtArquivado: data local, motivo e casos nulos", () => {
  const em = new Date(2026, 10, 13, 4, 30).toISOString();
  assert.equal(fmtArquivado({ em, motivo: "pedido no Office" }), "Arquivado em 13/11/2026 · pedido no Office");
  assert.equal(fmtArquivado({ em: "2026-11-17T18:34:00+00:00", motivo: "idade (180 dias)" }).split(" · ")[1], "idade (180 dias)");
  assert.equal(fmtArquivado({ em: null, motivo: "idade (30 dias)" }), "Arquivado · idade (30 dias)");
  assert.equal(fmtArquivado({ em: null, motivo: null }), "Arquivado");
  assert.equal(fmtArquivado({ em: "ontem", motivo: "" }), "Arquivado em ontem"); // texto não ISO: tal e qual
  assert.equal(fmtArquivado(null), "");
});

test("estadoArquivo: pendente > erro > repor/arquivar; sem «arquivo» nada", () => {
  const d = (a) => ({ item: { id: "material/x.md" }, arquivo: { arquivavel: true, arquivado: null, pendente: null, erro: null, ...a } });
  assert.deepEqual(estadoArquivo(d({})), { botao: "arquivar", texto: "" });
  assert.deepEqual(estadoArquivo(d({ arquivavel: false })), { botao: null, texto: "" });
  assert.deepEqual(estadoArquivo(d({ arquivado: { em: null, motivo: null } })), { botao: "repor", texto: "" });
  assert.deepEqual(estadoArquivo(d({ erro: "protegido: alterado há menos de 48 h" })),
    { botao: "arquivar", texto: "O último pedido falhou: protegido: alterado há menos de 48 h." });
  assert.deepEqual(estadoArquivo(d({ arquivado: { em: null, motivo: null }, erro: "destino já existe" })),
    { botao: "repor", texto: "O último pedido falhou: destino já existe." });
  for (const p of ["arquivar", "repor"]) assert.deepEqual(estadoArquivo(d({ pendente: p, erro: "erro a mover" })), { botao: null, texto: FILA_TXT });
  assert.equal(FILA_TXT, "Pedido em fila · o runner trata-o no próximo minuto");
  // resposta do POST que falhou (aviso) sobrepõe-se ao erro do runner
  assert.equal(estadoArquivo(d({ erro: "erro a mover", aviso: "Não foi possível pedir: a fila está cheia, tenta mais tarde." })).texto,
    "Não foi possível pedir: a fila está cheia, tenta mais tarde.");
  for (const x of [null, undefined, {}, { item: {} }]) assert.deepEqual(estadoArquivo(x), { botao: null, texto: "" });
});

test("respostaArquivo: 202 e 409 ficam em fila; 400, 403, 429, 503, outros e sem rede com mensagem PT-PT", () => {
  assert.deepEqual(respostaArquivo(202, { id: "x", accao: "arquivar" }), { fila: true, texto: FILA_TXT });
  assert.deepEqual(respostaArquivo(409, { error: "já há um pedido em fila para este item" }), { fila: true, texto: FILA_TXT });
  const t = (s, j = {}) => { const r = respostaArquivo(s, j); assert.equal(r.fila, false); return r.texto; };
  assert.equal(t(400, { error: "não arquivável" }), "Não foi possível pedir: não arquivável.");
  assert.equal(t(400, { error: "item inexistente" }), "Não foi possível pedir: item inexistente.");
  assert.equal(t(400), "Não foi possível pedir: erro 400.");
  assert.equal(t(429, { error: "fila cheia" }), "Não foi possível pedir: a fila está cheia, tenta mais tarde.");
  assert.equal(t(503, { error: "fila do arquivo indisponível" }), "Não foi possível pedir: a fila do arquivo não está disponível.");
  assert.equal(t(403, { error: "token" }), "Não foi possível pedir: a sessão mudou, recarrega a página.");
  assert.equal(t(413, null), "Não foi possível pedir: erro 413.");
  for (const s of [undefined, null, 0]) assert.equal(t(s), "Não foi possível pedir: sem ligação ao Office.");
});
