// Testes de static/conversa.js (node --test tests/conversa_test.mjs): funções puras, cartões sobre um DOM falso mínimo (V4, V7),
// porte do Office com as diferenças do Lite (nome, pedido assinado, entrada, voz por modo, gasto).
// O import em si prova que o módulo não toca em window/document ao carregar.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import {
  ID, MODELOS, ROT_MODELO, envioVisto, idValido, rota, fundir, ultimoEstado, deveSondar, trabalha, segundosAte, fmtSeg, pendente, podeConversa, limpa,
  camposPedido, respostasDe, pesquisa, ordenar, erroTxt, itemEl, cartaoActivo, lerUltima, gravarUltima,
  pedidoActivo, agrupar, resumoGrupo, fmtDuracao, teclaEnvia, dataRelativa, pecaEstado, mostraParar, modeloEmUso, respondeu, esperarCriada, conversaLinhas,
} from "../static/conversa.js";
import * as C from "../static/conversa.js";
import * as K from "../static/conversa_chave.js";

const ler = (f) => readFileSync(new URL("../static/" + f, import.meta.url), "utf8");
const CID = "20261121-235000-a1b2c3", N1 = "a".repeat(32), N2 = "b".repeat(32), XSS = `<img src=x onerror="globalThis.pwned=1">`;
const AGORA = Date.parse("2026-11-22T10:00:00Z"), EM = (min) => new Date(AGORA + min * 6e4).toISOString();

// exemplos do contrato §3.7
const EV = [
  { n: 1, t: "2026-11-22T09:50:00+00:00", tipo: "mensagem", autor: "dono", texto: "Delega à dev a correcção X" },
  { n: 2, t: "2026-11-22T09:50:01+00:00", tipo: "estado", estado: "a_trabalhar", passo: "a pensar" },
  { n: 3, t: "2026-11-22T09:50:02+00:00", tipo: "ferramenta", nome: "Write", resumo: "~/projects/.tasks/inbox/x.md" },
  { n: 4, t: "2026-11-22T09:50:03+00:00", tipo: "ferramenta", ref: 3, resultado: "ok" },
  { n: 5, t: "2026-11-22T09:50:04+00:00", tipo: "permissao", nonce: N1, ferramenta: "Bash", alvo: "python3 dev_task.py ~/projects/.tasks/inbox/x.md",
    resumo: "lançar a dev", delegacao: true, expira: EM(9), brief: { caminho: "~/projects/.tasks/inbox/x.md", inicio: ["# X"], sha256: "f".repeat(64) } },
  { n: 6, t: "2026-11-22T09:50:05+00:00", tipo: "estado", estado: "a_espera", passo: "à espera" },
  { n: 7, t: "2026-11-22T09:51:00+00:00", tipo: "permissao", nonce: N1, resolvido: "aprovada_uma_vez" },
  { n: 8, t: "2026-11-22T09:51:01+00:00", tipo: "permissao", nonce: N2, ferramenta: "Bash", alvo: "ls", resumo: "listar", delegacao: false,
    expira: EM(9), resolvido: "aprovada_conversa", auto: true },
  { n: 9, t: "2026-11-22T09:52:00+00:00", tipo: "custo", usd: 0.12, tokens: 1234, dia_usd: 3.4 },
  { n: 10, t: "2026-11-22T09:52:00+00:00", tipo: "estado", estado: "pronto", passo: "" },
  { n: 11, t: "2026-11-22T09:52:00+00:00", tipo: "fim", motivo: "pronto" },
];

test("idValido e rota: só ^\\d{8}-\\d{6}-[0-9a-f]{6}$; «..», «%2f» e maiúsculas dão «não encontrada» (S6)", () => {
  assert.ok(idValido(CID));
  for (const x of ["", "20261121-235000-A1B2C3", "20261121-235000-a1b2c", "../20261121-235000-a1b2c3", null, 5]) assert.equal(idValido(x), false);
  assert.deepEqual(rota("#conversa"), { lista: true });
  assert.deepEqual(rota("#conversa/"), { lista: true });
  assert.deepEqual(rota("#conversa/" + CID), { id: CID });
  for (const h of ["#conversa/../x", "#conversa/..%2f" + CID, "#conversa/" + CID + "%2f..", "#conversa/" + CID + "/x", "#conversa/%32" + CID.slice(1)])
    assert.deepEqual(rota(h), { erro: "Conversa não encontrada." }, h);
  for (const h of ["", "#quadro", "#conversas", "#" + CID, "#biblioteca/conversa/x"]) assert.equal(rota(h), null, h);
  assert.equal(ID.source, "^\\d{8}-\\d{6}-[0-9a-f]{6}$");
});

test("fundir: resolução por nonce e resultado por ref no próprio cartão; o já resolvido por regra fica um cartão", () => {
  const it = fundir(EV);
  assert.deepEqual(it.map((x) => x.n), [1, 2, 3, 5, 6, 8, 9, 10, 11]);
  assert.equal(it.find((x) => x.n === 3).resultado, "ok");
  assert.equal(it.find((x) => x.n === 5).resolvido, "aprovada_uma_vez");
  assert.equal(it.find((x) => x.n === 8).auto, true);
  assert.equal(EV[4].resolvido, undefined, "não muda os eventos de entrada");
  // resultado sem pedido (ref desconhecido) e resolução de outro tipo com o mesmo nonce não se fundem
  const solto = fundir([{ n: 1, tipo: "ferramenta", ref: 99, resultado: "r" }, { n: 2, tipo: "plano", nonce: N1, texto: "p", expira: EM(1) },
    { n: 3, tipo: "pergunta", nonce: N1, resolvido: "respondida" }]);
  assert.equal(solto.length, 3);
  assert.equal(solto[1].resolvido, undefined);
  assert.deepEqual(fundir(null), []);
  assert.deepEqual(fundir([null, 3, "x"]), []);
});

test("ultimoEstado/deveSondar: polling só em a_trabalhar/a_espera ou na janela depois de um pedido (V3)", () => {
  assert.equal(ultimoEstado(EV.slice(0, 6), "pronto"), "a_espera");
  assert.equal(ultimoEstado(EV, "a_trabalhar"), "pronto");
  assert.equal(ultimoEstado([], "parado"), "parado");
  assert.equal(ultimoEstado([], "inventado"), null);
  assert.equal(ultimoEstado([{ tipo: "estado", estado: "x" }], "erro"), "erro");
  for (const e of ["a_trabalhar", "a_espera"]) { assert.ok(deveSondar(e, 0, AGORA)); assert.ok(trabalha(e)); }
  for (const e of ["pronto", "parado", "erro", null]) { assert.equal(deveSondar(e, 0, AGORA), false); assert.equal(trabalha(e), false); }
  assert.ok(deveSondar("pronto", AGORA + 1, AGORA));
  assert.equal(deveSondar("pronto", AGORA, AGORA), false);
});

test("segundosAte, fmtSeg, pendente e podeConversa (V4)", () => {
  assert.equal(segundosAte(EM(10), AGORA), 600);
  assert.equal(segundosAte(EM(-1), AGORA), 0);
  assert.equal(segundosAte("lixo", AGORA), 0);
  assert.equal(fmtSeg(581), "9:41");
  assert.equal(fmtSeg(5), "0:05");
  assert.ok(pendente({ expira: EM(1) }, AGORA));
  assert.equal(pendente({ expira: EM(1), resolvido: "recusada" }, AGORA), false);
  assert.equal(pendente({ expira: EM(-1) }, AGORA), false);
  assert.equal(pendente({ expira: EM(1) }, AGORA, true), false, "arquivada");
  assert.equal(podeConversa({ delegacao: true }), false);
  assert.equal(podeConversa({ delegacao: false }), true);
});

test("modelos fechados (V5, CA-18): omissao/fable/opus/sonnet/haiku; omissao não manda modelo; o texto escrito nunca responde a um pedido (S2)", () => {
  assert.deepEqual(MODELOS, ["omissao", "fable", "opus", "sonnet", "haiku"]);
  assert.equal(ROT_MODELO.fable, "Fable");
  assert.deepEqual(Object.keys(ROT_MODELO), MODELOS);
  const env = (o) => camposPedido("enviar", { conversa: CID, texto: "x", modo: "normal", modelo: "omissao", permissoes: "cartao", ...o });
  for (const m of ["inherit", "Fable", "FABLE", "fable "]) assert.equal(env({ modelo: m }), null, m);
  for (const m of MODELOS) {
    const c = env({ texto: "aprovado", modelo: m });
    assert.equal(c.modelo, m); // Lite: todas as chaves vão (a assinatura cobre-as; o Office não preenche omissões)
    assert.equal("nonce" in c || "escolha" in c, false, "enviar nunca leva nonce/escolha");
  }
  const src = ler("conversa.js");
  // o envio de texto só gera «enviar»; responder_* e aprovar_plano só saem de cliques no cartão
  assert.match(src, /enviarPedido\("enviar", \{ conversa: S\.id, texto, modo:/);
  const corpoTexto = src.slice(src.indexOf("async function enviarTexto"), src.indexOf("async function parar"));
  assert.doesNotMatch(corpoTexto, /responder_|aprovar_plano|nonce/);
});

test("respostasDe: uma por pergunta; «Outra resposta» ≤ 1000 vale por cima; escolha única só uma opção (F5)", () => {
  const P = [{ pergunta: "Tom?", opcoes: [{ rotulo: "A" }, { rotulo: "B" }, { rotulo: "C" }], multipla: false },
    { pergunta: "Canais?", opcoes: [{ rotulo: "X" }, { rotulo: "Y" }], multipla: true }];
  assert.deepEqual(respostasDe(P, [{ opcoes: [2] }, { opcoes: [1, 0, 1] }]), [{ pergunta: 0, opcao: [2] }, { pergunta: 1, opcao: [0, 1] }]);
  assert.deepEqual(respostasDe(P, [{ opcoes: [0], outra: "  à minha maneira " }, { opcoes: [0] }]), [{ pergunta: 0, outra: "à minha maneira" }, { pergunta: 1, opcao: [0] }]);
  assert.deepEqual(respostasDe(P.slice(0, 1), [{ outra: "x".repeat(1000) }]), [{ pergunta: 0, outra: "x".repeat(1000) }]);
  assert.equal(respostasDe(P.slice(0, 1), [{ outra: "x".repeat(1001) }]), null);
  assert.equal(respostasDe(P, [{ opcoes: [0, 1] }, { opcoes: [0] }]), null, "escolha única com duas");
  assert.equal(respostasDe(P, [{ opcoes: [0] }, {}]), null, "falta uma");
  assert.equal(respostasDe(P, [{ opcoes: [5] }, { opcoes: [0] }]), null, "índice fora");
  assert.equal(respostasDe([], []), null);
  // o resultado passa no corpoPedido
  assert.ok(camposPedido("responder_pergunta", { conversa: CID, nonce: N1, respostas: respostasDe(P, [{ opcoes: [1] }, { outra: "z" }]) }));
});

test("pesquisa (2–100 car.) e ordenar (não arquivadas primeiro, mais recente em cima) (V2)", () => {
  assert.equal(pesquisa(""), "");
  assert.equal(pesquisa("  "), "");
  assert.equal(pesquisa("a"), null);
  assert.equal(pesquisa(" ab "), "ab");
  assert.equal(pesquisa("x".repeat(100)), "x".repeat(100));
  assert.equal(pesquisa("x".repeat(101)), null);
  const c = (id, actualizada, arquivada = false) => ({ id: `20261121-0000${id}-aaaaaa`, actualizada, arquivada });
  assert.deepEqual(ordenar([c(10, "2026-11-20T10:00:00Z", true), c(11, "2026-11-21T10:00:00Z"), c(12, "2026-11-22T10:00:00Z"), c(13, "2026-11-23T10:00:00Z", true),
    { id: "../x", actualizada: "2030-01-01" }]).map((x) => x.id.slice(13, 15)), ["12", "11", "13", "10"]);
  assert.deepEqual(ordenar(null), []);
});

// ---- DOM falso mínimo: innerHTML/insertAdjacentHTML rebentam; o texto só por textContent ou nós de texto
class Txt { constructor(s) { this.data = String(s); } get textContent() { return this.data; } }
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.kids = []; this.attrs = {}; this.dataset = {}; this.hidden = false; }
  set innerHTML(_) { throw new Error("innerHTML usado"); }
  insertAdjacentHTML() { throw new Error("insertAdjacentHTML usado"); }
  set textContent(v) { this.kids = [new Txt(v)]; }
  get textContent() { return this.kids.map((k) => k.textContent).join(""); }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = String(v);
    if (k === "hidden") this.hidden = true;
  }
  getAttribute(k) { return Object.hasOwn(this.attrs, k) ? this.attrs[k] : null; }
  append(...xs) { for (const x of xs) this.kids.push(typeof x === "string" ? new Txt(x) : x); }
  replaceChildren(...xs) { this.kids = []; this.append(...xs); }
  *todos() { for (const k of this.kids) if (k instanceof El) { yield k; yield* k.todos(); } }
  querySelectorAll(sel) { const tags = sel.split(",").map((s) => s.trim().toUpperCase()); return [...this.todos()].filter((e) => tags.includes(e.tagName)); }
}
globalThis.document = { createElement: (t) => new El(t), createDocumentFragment: () => new El("#frag") };
const botoes = (li) => [...li.todos()].filter((e) => e.tagName === "BUTTON").map((b) => b.textContent);
const textos = (li) => [li, ...li.todos()].filter((e) => e.kids.some((k) => k instanceof Txt)).map((e) => e.textContent);

test("V7: <img onerror> em todos os campos dos eventos fica texto (textContent, nunca innerHTML)", () => {
  const ev = [
    { n: 1, tipo: "mensagem", autor: "respondeu", texto: XSS },
    { n: 2, tipo: "ferramenta", nome: XSS, resumo: XSS, resultado: XSS },
    { n: 3, tipo: "permissao", nonce: N1, ferramenta: XSS, alvo: XSS, resumo: XSS, delegacao: true, expira: new Date(Date.now() + 6e5).toISOString(),
      brief: { caminho: XSS, inicio: [XSS, XSS], sha256: XSS } },
    { n: 4, tipo: "pergunta", nonce: N2, expira: new Date(Date.now() + 6e5).toISOString(),
      perguntas: [{ pergunta: XSS, cabecalho: XSS, opcoes: [{ rotulo: XSS, descricao: XSS }], multipla: false }] },
    { n: 5, tipo: "plano", nonce: "c".repeat(32), expira: new Date(Date.now() + 6e5).toISOString(), texto: XSS },
    { n: 6, tipo: "aviso", codigo: "ocupado", texto: XSS },
    { n: 7, tipo: "permissao", nonce: "d".repeat(32), ferramenta: "Bash", alvo: "x", resumo: "y", delegacao: false, expira: "2000-01-01T00:00:00Z", brief: { erro: XSS } },
  ];
  for (const e of ev) {
    // os pedidos pendentes aparecem no cartão activo (com tudo) e no fluxo só numa linha compacta
    const li = ["permissao", "pergunta", "plano"].includes(e.tipo) && pendente(e) ? cartaoActivo(e) : itemEl(e), t = textos(li).join("\n");
    assert.ok(t.includes(XSS), `tipo ${e.tipo}: o texto tem de aparecer tal e qual`);
    assert.ok(![...li.todos()].some((x) => x.tagName === "IMG"), `tipo ${e.tipo}: nenhum <img> criado`);
  }
  assert.equal(globalThis.pwned, undefined);
  assert.equal(itemEl({ n: 9, tipo: "estado", estado: "pronto" }), null);
  assert.equal(itemEl({ n: 9, tipo: XSS }), null);
  // a única atribuição de innerHTML no módulo é a do esqueleto fixo
  const src = ler("conversa.js").split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");
  assert.deepEqual(src.match(/innerHTML|insertAdjacentHTML|outerHTML/g), ["innerHTML"]);
  assert.match(src, /root\.innerHTML = SHELL;/);
  // o esqueleto só interpola constantes (limite e a lista fechada de modelos)
  assert.doesNotMatch(src.slice(src.indexOf("const SHELL"), src.indexOf("function montar")), /\$\{(?!TEXTO_MAX\}|MODELOS\.map|m\}|ROT_MODELO\[m\]\})/);
});

test("V4: botões só no cartão activo, por tipo; delegação sem «nesta conversa»; no fluxo, pendente = linha sem botões e resolvido sem botões", () => {
  const exp = new Date(Date.now() + 6e5).toISOString();
  const perm = { n: 1, tipo: "permissao", nonce: N1, ferramenta: "Bash", alvo: "ls -la", resumo: "listar", delegacao: false, expira: exp };
  assert.deepEqual(botoes(cartaoActivo(perm)), ["Aprovar", "Aprovar nesta conversa", "Recusar"]);
  assert.deepEqual(botoes(itemEl(perm)), [], "no fluxo não há botões de aprovar (um só sítio para responder)");
  assert.equal(itemEl(perm).textContent, "Pedido de permissão: Bash · à tua espera");
  const del = cartaoActivo({ ...perm, delegacao: true, brief: { caminho: "~/projects/.tasks/inbox/x.md", inicio: ["# X"], sha256: "e".repeat(64) } });
  assert.deepEqual(botoes(del), ["Aprovar delegação", "Recusar"]);
  assert.ok(textos(del).includes("Delegação a uma squad"));
  assert.ok(textos(del).includes("sha256 eeeeeeeeeeee"));
  assert.ok(textos(cartaoActivo(perm, 2)).includes("Pedido de permissão · 1 de 3"));
  assert.ok(textos(cartaoActivo(perm, 2)).includes("Mais 2 à espera"));
  assert.ok(!textos(cartaoActivo(perm, 0)).some((t) => /à espera|1 de/.test(t)));
  const pre = [...cartaoActivo(perm).todos()].find((e) => e.tagName === "PRE");
  assert.equal(pre.textContent, "ls -la", "alvo num <pre>");
  for (const r of ["aprovada_uma_vez", "aprovada_conversa", "recusada", "expirada"]) {
    const li = itemEl({ ...perm, resolvido: r });
    assert.deepEqual(botoes(li), [], r);
    assert.ok(textos(li).some((t) => /Aprovada|Recusada|Expirou/.test(t)), r);
  }
  assert.ok(textos(itemEl({ ...perm, resolvido: "aprovada_conversa", auto: true })).some((t) => t.includes("regra desta conversa")));
  assert.deepEqual(botoes(itemEl({ ...perm, expira: new Date(Date.now() - 1000).toISOString() })), [], "expirado sem botões");
  const plano = { n: 2, tipo: "plano", nonce: N2, texto: "1. fazer", expira: exp };
  assert.deepEqual(botoes(cartaoActivo(plano)), ["Aprovar plano", "Continuar a planear", "Editar", "Enviar plano editado", "Cancelar"]);
  assert.ok([...cartaoActivo(plano).todos()].some((e) => e.tagName === "OL"), "o plano aparece em markdown (CA-15)");
  assert.deepEqual(botoes(itemEl({ ...plano, resolvido: "editado" })), []);
  const perg = { n: 3, tipo: "pergunta", nonce: N2, expira: exp, perguntas: [{ pergunta: "Tom?", cabecalho: "Tom", multipla: false,
    opcoes: [{ rotulo: "Caseiro", descricao: "d" }, { rotulo: "Festivo" }] }, { pergunta: "Canais?", multipla: true, opcoes: [{ rotulo: "X" }, { rotulo: "Y" }] }] };
  assert.deepEqual(botoes(cartaoActivo(perg)), ["Caseirod", "Festivo", "Outra resposta", "X", "Y", "Outra resposta", "Responder"]);
  assert.deepEqual(botoes(itemEl(perg)), []);
  const ta = [...cartaoActivo(perg).todos()].filter((e) => e.tagName === "TEXTAREA");
  assert.deepEqual(ta.map((t) => t.attrs.maxlength), ["1000", "1000"]);
  assert.ok(ta.every((t) => t.hidden));
  assert.deepEqual(botoes(itemEl({ ...perg, resolvido: "respondida" })), []);
});

test("envioVisto: a mensagem do dono não acaba o «a enviar»; só estado/aviso/fim depois do envio (sondagem continua)", () => {
  const ev = [{ n: 4, tipo: "fim" }, { n: 5, tipo: "mensagem", autor: "dono", texto: "x" }];
  assert.equal(envioVisto(ev, 4), false);
  assert.ok(deveSondar("pronto", AGORA + 120000, AGORA), "enquanto aguarda, sonda");
  for (const tipo of ["estado", "aviso", "fim"]) assert.ok(envioVisto([...ev, { n: 6, tipo }], 4), tipo);
  assert.equal(envioVisto([...ev, { n: 6, tipo: "ferramenta" }], 4), false);
  assert.equal(envioVisto(null, 0), false);
  const src = ler("conversa.js");
  assert.match(src, /if \(S\.aguarda && envioVisto\(S\.ev, S\.desdeEnvio\)\) S\.aguarda = 0;/);
  // 409 (ou outro erro) no enviar: o texto fica na caixa; só um 202 tira o texto enviado (o resto fica: CR3)
  assert.match(src, /if \(r\.status === 202\) \{ if \(ta\.value\.startsWith\(orig\)\) \{ ta\.value = ta\.value\.slice\(orig\.length\)\.trimStart\(\); crescer\(ta\); \} [^\n]*sondar\(\); \}\n  else \{ S\.aguarda = 0; msg\.textContent = erroTxt\(r\); A\.tocar\?\.\("erro"\); \}/);
});

test("cartão de permissão: alvo longo inteiro no <pre> (sem corte) e sha256 com 12 hex", () => {
  const alvo = "x".repeat(1990) + "FIM", li = cartaoActivo({ n: 1, tipo: "permissao", nonce: N1, ferramenta: "Bash", alvo, resumo: "r", delegacao: false,
    expira: new Date(Date.now() + 6e5).toISOString(), sha256: "0123456789abcdef".repeat(4) });
  assert.equal([...li.todos()].find((e) => e.tagName === "PRE").textContent, alvo);
  assert.ok(textos(li).includes("sha256 0123456789ab"));
  assert.ok(!textos(itemEl({ n: 2, tipo: "permissao", nonce: N2, ferramenta: "Bash", alvo: "ls", expira: "2000-01-01T00:00:00Z" })).some((t) => t.startsWith("sha256")));
  assert.match(ler("conversa.css"), /\.cv-pre \{[^}]*overflow: auto/);
});

// ---- v2: painel lateral global (puras)

test("lerUltima/gravarUltima: localStorage a falhar → null, sem excepção; id torto fica null; null apaga", () => {
  const rebenta = { getItem() { throw new Error("SecurityError"); }, setItem() { throw new Error("QuotaExceeded"); }, removeItem() { throw new Error("x"); } };
  assert.equal(lerUltima(rebenta), null);
  assert.equal(lerUltima(null), null);
  assert.doesNotThrow(() => gravarUltima(rebenta, CID));
  assert.doesNotThrow(() => gravarUltima(null, null));
  const mem = new Map(), st = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
  assert.equal(lerUltima(st), null);
  gravarUltima(st, CID);
  assert.equal(mem.get("conversa.ultima"), CID);
  assert.equal(lerUltima(st), CID);
  gravarUltima(st, "../x");
  assert.equal(mem.has("conversa.ultima"), false, "id inválido apaga");
  mem.set("conversa.ultima", CID.toUpperCase());
  assert.equal(lerUltima(st), null);
});

test("pedidoActivo (CA-13): o pendente de menor n; «mais» conta os outros; resolvidos e expirados não contam", () => {
  const p = (n, extra = {}) => ({ n, tipo: "permissao", nonce: String(n).repeat(32).slice(0, 32), expira: EM(5), ...extra });
  const itens = [{ n: 1, tipo: "mensagem" }, p(9), p(4), { n: 5, tipo: "pergunta", nonce: N1, expira: EM(5) }, p(2, { resolvido: "recusada" }), p(3, { expira: EM(-1) })];
  const r = pedidoActivo(itens, AGORA);
  assert.equal(r.activo.n, 4);
  assert.equal(r.mais, 2);
  assert.deepEqual(pedidoActivo(itens.slice(0, 2), AGORA), { activo: itens[1], mais: 0 });
  assert.deepEqual(pedidoActivo([], AGORA), { activo: null, mais: 0 });
  assert.deepEqual(pedidoActivo(null, AGORA), { activo: null, mais: 0 });
  // respondido o activo (resolução fundida pelo nonce), sobe o seguinte
  const ev = [p(4), p(9), { n: 10, tipo: "permissao", nonce: p(4).nonce, resolvido: "aprovada_uma_vez" }];
  assert.equal(pedidoActivo(fundir(ev), AGORA).activo.n, 9);
});

test("agrupar/resumoGrupo (CA-23): ferramentas seguidas num grupo; estado/custo não quebram; categorias, plurais, «…» e duração", () => {
  const f = (n, nome, s = 0, resultado = "ok") => ({ n, tipo: "ferramenta", nome, t: new Date(AGORA + s * 1000).toISOString(), resultado, tr: new Date(AGORA + (s + 1) * 1000).toISOString() });
  const itens = [{ n: 1, tipo: "mensagem" }, f(2, "Read"), { n: 3, tipo: "estado" }, f(4, "Read"), { n: 5, tipo: "custo" }, f(6, "Bash", 10),
    { n: 7, tipo: "mensagem" }, f(8, "Grep"), { n: 9, tipo: "permissao" }, f(10, "X")];
  const g = agrupar(itens);
  assert.deepEqual(g.map((x) => x.tipo === "grupo" ? "g" + x.passos.map((p) => p.n).join("+") : x.n), [1, "g2+4+6", 3, 5, 7, "g8", 9, "g10"]);
  assert.equal(g[1].n, 2, "a chave do grupo é o n do 1.º passo");
  assert.equal(resumoGrupo(g[1].passos), "Leu 2 ficheiros, correu 1 comando · 11 s");
  assert.equal(resumoGrupo([f(1, "Glob"), f(2, "Grep")]), "Fez 2 pesquisas · 1 s");
  assert.equal(resumoGrupo([f(1, "WebFetch", 0, "x")].map((p) => ({ ...p, tr: p.t }))), "Consultou a web 1 vez");
  assert.equal(resumoGrupo([f(1, "Task"), f(2, "Agent"), f(3, "Write"), f(4, "mcp__x"), f(5, "mcp__y"), f(6, "WebSearch"), f(7, "WebSearch")]),
    "Editou 1 ficheiro, consultou a web 2 vezes, lançou 2 subagentes, usou 2 outras ferramentas · 1 s");
  assert.equal(resumoGrupo([f(1, "Read", 0), f(2, "Bash", 3, null)]), "Leu 1 ficheiro, correu 1 comando…", "em curso: «…» e sem duração");
  assert.equal(resumoGrupo([f(1, "Edit", 0), { ...f(2, "Edit", 60), tr: new Date(AGORA + 65000).toISOString() }]), "Editou 2 ficheiros · 1 min 05 s");
  assert.equal(resumoGrupo([{ n: 1, tipo: "ferramenta", nome: "Read", resultado: "x" }]), "Leu 1 ficheiro", "sem horas não há duração");
  assert.deepEqual(agrupar(null), []);
  assert.equal(fmtDuracao(0.4), "0 s");
  assert.equal(fmtDuracao(59), "59 s");
  assert.equal(fmtDuracao(3725), "1 h 02 min");
});

test("fluxo (CA-23): grupo fechado com os passos lá dentro; passo sem resultado diz «a correr»", () => {
  const li = itemEl({ tipo: "grupo", n: 3, passos: [{ n: 3, tipo: "ferramenta", nome: "Bash", resumo: "ls" }, { n: 4, tipo: "ferramenta", nome: "Read", resultado: "ok" }] });
  const det = [...li.todos()].find((e) => e.tagName === "DETAILS");
  assert.ok(det && !det.open, "fechado por omissão");
  assert.ok(textos(li).includes("Leu 1 ficheiro, correu 1 comando…"));
  assert.ok(textos(li).includes("a correr"));
});

test("teclaEnvia (CA-26, D2)", () => {
  const k = (o) => ({ key: "Enter", shiftKey: false, altKey: false, isComposing: false, keyCode: 13, ...o });
  assert.equal(teclaEnvia(k(), false), true);
  assert.equal(teclaEnvia(k({ shiftKey: true }), false), false, "Shift+Enter muda de linha");
  assert.equal(teclaEnvia(k({ isComposing: true }), false), false, "IME");
  assert.equal(teclaEnvia(k({ keyCode: 229 }), false), false, "IME (Safari)");
  assert.equal(teclaEnvia(k(), true), false, "ecrã de toque: Enter muda de linha");
  assert.equal(teclaEnvia(k({ key: "a" }), false), false);
  assert.equal(teclaEnvia(null, false), false);
});

test("dataRelativa (CA-25) e modeloEmUso (CA-19)", () => {
  const ag = new Date(2026, 10, 22, 15, 0).getTime();
  assert.equal(dataRelativa(new Date(2026, 10, 22, 10, 15).toISOString(), ag), "hoje 10:15");
  assert.equal(dataRelativa(new Date(2026, 10, 21, 23, 59).toISOString(), ag), "ontem");
  assert.equal(dataRelativa(new Date(2026, 10, 18, 9, 0).toISOString(), ag), "18/11");
  assert.equal(dataRelativa(new Date(2025, 10, 18, 9, 0).toISOString(), ag), "18/11/2025");
  assert.equal(dataRelativa("lixo", ag), "");
  const st = (v) => ({ getItem: () => v });
  assert.equal(modeloEmUso(st(JSON.stringify({ [CID]: "fable" })), CID), "Fable");
  assert.equal(modeloEmUso(st(JSON.stringify({ [CID]: "gpt" })), CID), "o do costume");
  assert.equal(modeloEmUso(st(null), CID), "o do costume");
  assert.equal(modeloEmUso({ getItem() { throw new Error("x"); } }, CID), "o do costume");
  assert.equal(modeloEmUso(st(JSON.stringify({ [CID]: "__proto__" })), CID), "o do costume");
});

test("markdown nas mensagens do Assistente (CA-21): só árvore + createElement; link perigoso fica texto; o do dono fica texto cru", () => {
  const jv = itemEl({ n: 1, tipo: "mensagem", autor: "respondeu", texto: "**ok** [a](javascript:void0) [b](https://ex.pt) ![c](https://ex.pt/c.png)\n\n" + XSS });
  const as = [...jv.todos()].filter((e) => e.tagName === "A");
  assert.deepEqual(as.map((a) => [a.attrs.href, a.attrs.rel, a.attrs.target, a.textContent]),
    [["https://ex.pt/", "noopener noreferrer", "_blank", "b"], ["https://ex.pt/c.png", "noopener noreferrer", "_blank", "imagem: c"]]);
  assert.ok([...jv.todos()].some((e) => e.tagName === "B"));
  assert.ok(!([...jv.todos()].some((e) => e.tagName === "IMG")));
  assert.ok(textos(jv).some((t) => t.includes(XSS)));
  const dono = itemEl({ n: 2, tipo: "mensagem", autor: "dono", texto: "**não** é markdown" });
  assert.ok(textos(dono).includes("**não** é markdown"));
  assert.match(ler("conversa.js"), /^import \{ md, materializar \} from "\.\/md\.js";$/m);
});

test("D1: alvo longo inteiro no <pre> com «Ver tudo (N linhas)»; a altura limita-se no CSS, o texto nunca", () => {
  const alvo = Array.from({ length: 30 }, (_, i) => "linha " + i).join("\n");
  const c = cartaoActivo({ n: 1, tipo: "permissao", nonce: N1, ferramenta: "Bash", alvo, expira: EM(5) });
  assert.equal([...c.todos()].find((e) => e.tagName === "PRE").textContent, alvo);
  assert.deepEqual(botoes(c), ["Ver tudo (30 linhas)", "Aprovar", "Aprovar nesta conversa", "Recusar"]);
  // A1: o alvo sem scroll próprio (um só, o do corpo); o cartão até 60dvh
  assert.match(ler("conversa.css"), /\.cp-act \.cv-alvo \{ max-height: none; overflow: visible; \}/);
  assert.match(ler("conversa.css"), /\.cp-act \{ flex: 0 1 auto; min-height: 0; max-height: 60dvh;/);
  // resolvido no fluxo: <details> só de leitura com o alvo inteiro
  const r = itemEl({ n: 1, tipo: "permissao", nonce: N1, ferramenta: "Bash", alvo, expira: EM(5), resolvido: "aprovada_uma_vez" });
  assert.equal([...r.todos()].find((e) => e.tagName === "PRE").textContent, alvo);
  assert.ok([...r.todos()].find((e) => e.tagName === "SUMMARY").textContent.startsWith("✓ Aprovada uma vez · Bash · linha 0 linha 1"));
});

test("segurança do painel (CA-29, D3): Enter só envia texto; foco nunca num botão de aprovar; 600 ms inactivos", () => {
  const src = ler("conversa.js");
  // a tecla na caixa só chama enviarTexto (que só gera «enviar»)
  const tk = src.slice(src.indexOf('ta.addEventListener("keydown"'), src.indexOf('ta.addEventListener("input"'));
  assert.match(tk, /teclaEnvia\(e, grosso\(\)\)/);
  assert.doesNotMatch(tk, /responder|aprovar|clique|click/);
  // responder_* e aprovar_plano só a partir do handler de cliques
  const decl = (i) => { const a = src.slice(0, i), k = Math.max(...["\nfunction ", "\nasync function ", "\nexport function ", "\nconst "].map((x) => a.lastIndexOf(x)));
    return a.slice(k + 1).match(/^(?:export |async )?(?:function|const) (\w+)/)[1]; };
  assert.deepEqual([...new Set([...src.matchAll(/"(responder_\w+|aprovar_plano)"/g)].map((m) => decl(m.index)))].sort(),
    ["camposPedido", "clique", "enviarRespostas"], "só o validador e os cliques nos cartões");
  const foco = src.slice(src.indexOf("function focoAoAbrir"), src.indexOf("// destino:"));
  assert.doesNotMatch(foco, /button|\.btns|ok-b/);
  assert.match(src, /setTimeout\(\(\) => \{ if \(S\.actNonce === it\.nonce && !S\.enviados\.has\(it\.nonce\)\) for \(const b of bs\) b\.disabled = false; \}, 600\)/);
  assert.match(src, /addEventListener\("hashchange", rotaHash\)/);
  // a página não tem um sondar de fundo da lista (o contador está no Office, a partir do publico.json do guardião)
  assert.doesNotMatch(src, /setInterval\([^)]*actualizarContador|conversa\.vistos|atalho/);
});

test("QA CA-13/CA-16: fila de 3 pedidos responde-se pela ordem; «1 de N» e «Mais N» descem; Aprovar=uma_vez, Recusar=recusar", () => {
  const p = (n, c) => ({ n, t: EM(0), tipo: "permissao", nonce: c.repeat(32), ferramenta: "Bash", alvo: "cmd " + n, delegacao: false, expira: EM(5) });
  const ev = [p(3, "a"), p(5, "b"), p(7, "c")];
  const passo = (evs) => { const { activo, mais } = pedidoActivo(fundir(evs), AGORA); return activo && [activo.n, mais, textos(cartaoActivo(activo, mais))]; };
  let [n, mais, t] = passo(ev);
  assert.deepEqual([n, mais], [3, 2]);
  assert.ok(t.includes("Pedido de permissão · 1 de 3") && t.includes("Mais 2 à espera"));
  ev.push({ n: 8, tipo: "permissao", nonce: "a".repeat(32), resolvido: "aprovada_uma_vez" });
  [n, mais, t] = passo(ev);
  assert.deepEqual([n, mais], [5, 1]);
  assert.ok(t.includes("Pedido de permissão · 1 de 2") && t.includes("Mais 1 à espera"));
  ev.push({ n: 9, tipo: "permissao", nonce: "b".repeat(32), resolvido: "recusada" });
  [n, mais, t] = passo(ev);
  assert.deepEqual([n, mais], [7, 0]);
  assert.ok(t.includes("Pedido de permissão") && !t.some((x) => /1 de|Mais/.test(x)));
  ev.push({ n: 10, tipo: "permissao", nonce: "c".repeat(32), resolvido: "aprovada_conversa" });
  assert.equal(passo(ev), null, "todos respondidos: zona presa vazia");
  // no fluxo, os resolvidos ficam no seu ponto, só de leitura, com o texto de RESOLVIDO
  const flx = fundir(ev).filter((x) => x.tipo === "permissao").map((x) => [...itemEl(x, false).todos()].find((e) => e.tagName === "SUMMARY").textContent);
  assert.deepEqual(flx.map((s) => s.split(" · ")[0]), ["✓ Aprovada uma vez", "✗ Recusada", "✓ Aprovada nesta conversa"]);
  // cada botão do cartão leva a escolha certa, e o corpo do POST sai com o nonce do pedido
  const c = cartaoActivo(p(3, "a"));
  const bs = [...c.todos()].filter((e) => e.tagName === "BUTTON").map((b) => [b.textContent, b.dataset.a, b.dataset.e]);
  assert.deepEqual(bs, [["Aprovar", "perm", "uma_vez"], ["Aprovar nesta conversa", "perm", "conversa"], ["Recusar", "perm", "recusar"]]);
  assert.equal(c.dataset.nonce, "a".repeat(32));
  for (const [, , e] of bs) assert.deepEqual(camposPedido("responder_permissao", { conversa: CID, nonce: c.dataset.nonce, escolha: e }),
    { acao: "responder_permissao", conversa: CID, nonce: "a".repeat(32), escolha: e });
});

test("QA CA-17: pedido expirado sai da zona presa e fica no fluxo «Expirou sem resposta (10 min)», sem botões", () => {
  const it = { n: 4, t: EM(-11), tipo: "permissao", nonce: N1, ferramenta: "Bash", alvo: "ls", delegacao: false, expira: EM(-1) };
  assert.deepEqual(pedidoActivo([it], AGORA), { activo: null, mais: 0 });
  const li = itemEl(it, pendente(it, AGORA));
  assert.deepEqual(botoes(li), []);
  assert.ok([...li.todos()].find((e) => e.tagName === "SUMMARY").textContent.startsWith("Expirou sem resposta (10 min) · Bash · ls"));
});

test("QA CA-23: mensagem, aviso, fim e pedidos quebram o grupo; ferramenta sozinha também é grupo; resultado no <pre> do passo", () => {
  const f = (n) => ({ n, tipo: "ferramenta", nome: "Read", resumo: "a.md", resultado: "ok" });
  const g = agrupar([f(1), { n: 2, tipo: "aviso" }, f(3), { n: 4, tipo: "fim" }, f(5), { n: 6, tipo: "plano" }, f(7), { n: 8, tipo: "pergunta" },
    f(9), { n: 10, tipo: "mensagem", autor: "dono" }, f(11), f(12)]);
  assert.deepEqual(g.map((x) => x.tipo === "grupo" ? x.passos.map((p) => p.n).join("+") : x.tipo),
    ["1", "aviso", "3", "fim", "5", "plano", "7", "pergunta", "9", "mensagem", "11+12"]);
  const so = itemEl({ n: 1, tipo: "ferramenta", nome: "Bash", resumo: "ls", resultado: XSS });
  assert.ok([...so.todos()].some((e) => e.tagName === "DETAILS"), "um passo solto também fica numa linha fechada");
  assert.equal([...so.todos()].find((e) => e.tagName === "PRE").textContent, XSS);
  assert.ok(textos(so).includes("Correu 1 comando"));
});

test("A1: «Ver tudo» e o sha256 ficam fora do .cp-corpo, entre o corpo e os botões; o alvo continua inteiro no corpo", () => {
  const alvo = Array.from({ length: 30 }, (_, i) => "linha " + i).join("\n");
  const c = cartaoActivo({ n: 1, tipo: "permissao", nonce: N1, ferramenta: "Bash", alvo, sha256: "a".repeat(64), expira: EM(5) });
  const cls = c.kids.map((k) => k.className || k.tagName);
  assert.deepEqual(cls.slice(0, 5), ["cp-cab", "cp-corpo", "cv-b cv-vt", "sub mono cv-sha", "btns"]);
  const corpo = c.kids[1];
  assert.equal([...corpo.todos()].find((e) => e.tagName === "PRE").textContent, alvo);
  assert.ok(![...corpo.todos()].some((e) => /cv-(vt|sha)/.test(e.className || "")));
});

test("C2/C5: abrirConversa repinta ao reabrir a mesma (o relógio de 1 s volta) e numa conversa nova repõe temAct e cp-pedido/cp-tudo", () => {
  const src = ler("conversa.js"), f = src.slice(src.indexOf("function abrirConversa"), src.indexOf("async function carregar(g)"));
  assert.match(f, /if \(id === S\.id && S\.ev\.length\) \{ pintarEventos\(\);/);
  assert.match(src.slice(src.indexOf("function pintarEventos"), src.indexOf("function relogio()")), /\n  relogio\(\);\n\}/);
  assert.match(f, /temAct: false/);
  assert.match(f, /root\.classList\.remove\("cp-pedido", "cp-tudo"\)/);
});

test("M1/B2: «Modelo» só para leitores a < 900 px; sem anel no #cp-h (agora o <h1> da página)", () => {
  const js = ler("conversa.js"), css = ler("conversa.css");
  assert.match(js, /<label><span class="cp-lm">Modelo <\/span><select name="modelo">/);
  assert.match(js, /<h1 id="cp-h" tabindex="-1">Conversas<\/h1>/);
  assert.match(css, /#cv-painel #cp-h:focus-visible \{ outline: 0; \}/);
  assert.doesNotMatch(js + css, /--cp-w|cp-pega|cp-tam|cp-inteira|#cp-b/);
});

test("mostraParar (CA7): «Parar» só com a_trabalhar", () => {
  assert.equal(mostraParar("a_trabalhar"), true);
  for (const e of ["a_espera", "pronto", "parado", "erro", undefined]) assert.equal(mostraParar(e), false, String(e));
});

test("pecaEstado (CA2, SPEC §5): estado do backend → peça, texto e t0 do cabeçalho", () => {
  const T = (min) => Date.parse(EM(min)), dono = { tipo: "mensagem", autor: "dono", t: EM(-5) }, jv = { tipo: "mensagem", autor: "respondeu", t: EM(-1) };
  const est = (estado, passo) => ({ tipo: "estado", estado, passo, t: EM(-2) }), fim = (motivo) => ({ tipo: "fim", motivo, t: EM(-1) });
  assert.deepEqual(pecaEstado([dono, est("a_trabalhar"), jv], "a_trabalhar"), ["a-trabalhar", "a trabalhar", T(-5)]);
  assert.deepEqual(pecaEstado([dono, est("a_trabalhar", "a ler <b>x</b>")], "a_trabalhar"), ["a-trabalhar", "a ler <b>x</b>", T(-5)]);
  assert.deepEqual(pecaEstado([dono, est("a_espera")], "a_espera"), ["a-espera", "à tua espera", T(-2)]);
  assert.deepEqual(pecaEstado([dono, est("a_trabalhar")], "a_trabalhar", false, { t: EM(-3) }), ["a-espera", "à tua espera", T(-3)], "pedido activo");
  assert.deepEqual(pecaEstado([dono, fim("pronto")], "pronto"), ["concluido", "pronto", T(-1)]);
  assert.deepEqual(pecaEstado([fim("erro")], "erro"), ["erro", "erro", T(-1)]);
  assert.deepEqual(pecaEstado([fim("tecto")], "parado"), ["erro", "parou no tecto (tempo ou turnos)", T(-1)]);
  assert.deepEqual(pecaEstado([fim("parado")], "parado").slice(0, 2), ["desligado", "parado"]);
  assert.deepEqual(pecaEstado([fim("pronto")], "pronto", true), [null, "· arquivada"]);
  const [e, x, t0] = pecaEstado([], null);
  assert.deepEqual([e, x, Number.isNaN(t0)], [undefined, "", true]);
});

test("sons (fase 4): gravar, parar e erro (negado, envio ≠ 202) pelo A.tocar; cancelar e o áudio não tocam", () => {
  const src = ler("conversa.js"), fn = (n) => src.slice(src.indexOf(n), src.indexOf("\n}\n", src.indexOf(n)));
  assert.equal(src.match(/A\.tocar\?\.\(/g).length, 6); // + o do «respondeu» (U8) e o do B3 (a conversa não chegou a nascer)
  assert.match(fn("async function micIniciou"), /ev == "negado" && A\.tocar\?\.\("erro"\)[^]*pintarMic\("a gravar"\); A\.tocar\?\.\("gravar"\);$/);
  assert.match(fn("async function vozParou"), /^async function vozParou[^\n]*\n  if \(seq !== S\.vseq\) return;\n  A\.tocar\?\.\("parar"\);/);
  assert.match(fn("async function enviarTexto"), /else \{[^}]*A\.tocar\?\.\("erro"\); \}/);
  for (const n of ["function cancelarVoz", "async function enviarAudio"]) assert.doesNotMatch(fn(n), /tocar/, n);
});

test("U8 respondeu(): toca ao sair de a_trabalhar para pronto/à espera depois de > 10 s vistos a trabalhar", () => {
  const corre = (passos) => { const m = new Map(); return passos.map(([est, t, id = "a"]) => respondeu(m, id, est, t)); };
  assert.deepEqual(corre([["a_trabalhar", 0], ["a_trabalhar", 10001], ["pronto", 11000]]), [false, false, true]);
  assert.deepEqual(corre([["a_trabalhar", 0], ["a_trabalhar", 12000], ["a_espera", 12500]]), [false, false, true]); // pedido de aprovação
  assert.deepEqual(corre([["a_trabalhar", 0], ["a_trabalhar", 10000], ["pronto", 10500]]), [false, false, false]); // 10 s não chega
  assert.deepEqual(corre([["a_trabalhar", 0], ["pronto", 25000]]), [false, false]);  // só um poll a trabalhar: não se sabe quanto durou
  assert.deepEqual(corre([["a_trabalhar", 0], ["a_trabalhar", 12000], ["erro", 13000]]), [false, false, false]);
  assert.deepEqual(corre([["a_trabalhar", 0], ["a_trabalhar", 12000], ["pronto", 43000]]), [false, false, false]); // buraco de 31 s (escondido)
  assert.deepEqual(corre([["a_trabalhar", 0], ["a_trabalhar", 40000], ["a_trabalhar", 45000], ["pronto", 46000]]), [false, false, false, false]); // recomeçou aos 40 s
  assert.deepEqual(corre([["pronto", 0], ["pronto", 20000]]), [false, false]);
  assert.deepEqual(corre([["a_trabalhar", 0, "a"], ["a_trabalhar", 12000, "b"], ["pronto", 12500, "b"], ["a_trabalhar", 12000, "a"], ["pronto", 13000, "a"]]),
    [false, false, false, false, true]); // por conversa
  const m = new Map();
  for (const [e, t] of [["a_trabalhar", 0], ["a_trabalhar", 12000]]) respondeu(m, "a", e, t);
  assert.equal(respondeu(m, "a", "pronto", 13000), true);
  assert.equal(respondeu(m, "a", "pronto", 14000), false, "uma vez só");
  const src = ler("conversa.js");
  assert.match(src, /S\.estado = ultimoEstado\(S\.ev, S\.meta\);\n  respondeu\(S\.trab, S\.id, S\.estado, Date\.now\(\)\) && A\.tocar\?\.\("respondeu"\);/);
});

// ---- Lite (ADR-023): pedido assinado, entrada por código, nome, gasto, avisos e voz por modo

const V = JSON.parse(readFileSync(new URL("./fixtures/conversa_vectores.json", import.meta.url), "utf8"));

test("Lite §2.4: camposPedido com todas as chaves obrigatórias; nova leva o id do browser; texto \"\" fora de «editar»", () => {
  const { pid, t, ...envio } = V.pedido;
  assert.deepEqual(C.camposPedido("enviar", envio), envio);
  for (const k of ["modo", "modelo", "permissoes", "texto", "conversa"]) assert.equal(C.camposPedido("enviar", { ...envio, [k]: undefined }), null, k);
  assert.equal(C.camposPedido("enviar", { ...envio, permissoes: "tudo" }), null);
  assert.deepEqual(C.camposPedido("nova", { conversa: CID }), { acao: "nova", conversa: CID });
  assert.equal(C.camposPedido("nova", {}), null);
  assert.deepEqual(C.camposPedido("aprovar_plano", { conversa: CID, nonce: N1, escolha: "aprovar", texto: "ignorado" }),
    { acao: "aprovar_plano", conversa: CID, nonce: N1, escolha: "aprovar", texto: "" });
  assert.equal(C.camposPedido("transcrever", { conversa: CID }), null, "o transcrever só pela rota de áudio");
  assert.equal(C.camposPedido("parar", { conversa: "../x" }), null);
});

test("Lite §2.3: o pedido do browser assina igual à ponte (vectores)", async () => {
  const k = await globalThis.crypto.subtle.importKey("raw", K.deHex(V.chave), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const { pid, t, ...envio } = V.pedido;
  const d = await K.assinado(globalThis.crypto.subtle, k, C.camposPedido("enviar", envio), t, (a) => a.set(K.deHex(pid)) || a);
  assert.equal(d.assinatura, V.assinatura);
  assert.equal(K.canonico(d), V.canonico);
});

test("Lite CA-18: nome do assistente (omissão «Assistente», 1–40, sem Cc/Cf) nas mensagens", () => {
  assert.equal(C.NOME_OMISSAO, "Assistente");
  assert.equal(C.nomeDe({}), "Assistente");
  assert.equal(C.nomeDe({ nome: null, nome_omissao: "Ana" }), "Ana");
  assert.equal(C.nomeDe({ nome: "Zé", nome_omissao: "Ana" }), "Zé");
  for (const m of ["", "   ", "x".repeat(41), "a\u0007", "a​b", "a\ud800"]) assert.equal(C.nomeOk(m), false, JSON.stringify(m));
  for (const m of ["Zé", "x".repeat(40), "Assistente 2 🤖"]) assert.equal(C.nomeOk(m), true, m);
  const li = itemEl({ n: 1, t: EM(0), tipo: "mensagem", autor: "assistente", texto: "olá" });
  assert.match(textos(li).join("\n"), /^Assistente · /m);
  assert.match(textos(itemEl({ n: 2, t: EM(0), tipo: "mensagem", autor: "dono", texto: "x" })).join("\n"), /^Tu · /m);
});

test("Lite CA-20: «hoje: X,XX USD» do dia_usd (sem limite: 1000 USD mostra-se e nada bloqueia)", () => {
  assert.equal(C.gastoTxt(1.234), "hoje: 1,23 USD");
  assert.equal(C.gastoTxt(1000), "hoje: 1000,00 USD");
  assert.equal(C.gastoTxt(null), "");
  const src = ler("conversa.js");
  assert.doesNotMatch(src, /limite_dia/);
  assert.match(src, /\$\("\.cp-gasto"\)\.textContent = gastoTxt\(S\.est\?\.dia_usd\);/);
});

test("Lite CA-2/CA-5/CA-21 e G2: sem HTTPS explica o domínio; código de uso único (one-time-code); aviso de tokens antes do 1.º envio", () => {
  const src = ler("conversa.js");
  assert.equal(C.SEM_TLS, "A Conversa só liga com HTTPS: põe um domínio no install.sh (--dominio).");
  assert.match(src, /<a href="https:\/\/github\.com\/MozThinker\/squad-office-lite#readme" target="_blank" rel="noopener noreferrer">/);
  assert.match(src, /<input id="cp-codigo" name="codigo" type="text" autocomplete="one-time-code"/);
  assert.match(src, /<p class="sub">Código de uso único: regista este browser\.<\/p>/);
  assert.doesNotMatch(src, /current-password|autocomplete="username"/, "o gestor de palavras-passe não guarda o código");
  assert.match(C.AVISO_TOKENS, /^A Conversa consome tokens da tua conta/);
  assert.match(src, /if \(S\.est\?\.aviso_tokens !== true\) return avisoTokens\(\);/); // enviarTexto
  assert.match(src, /avisoTokens\(\) \{\n  if \(dlg\.open \|\| S\.est\?\.aviso_tokens === true\) return;\n  if \(!\(await dialogo\("Antes de começar", AVISO_TOKENS, "Percebi"\)\)\) return;\n  const r = await gravarPrefs\(\{ aviso_tokens: true \}\);/);
  // o código só passa pelo verificador; a chave K é aleatória (novaChave) e vai uma vez no registo; nunca em localStorage
  const ent = src.slice(src.indexOf("async function entrar(f)"), src.indexOf("// o nome do assistente"));
  assert.match(ent, /const v = await K\.verificador\(subtle\(\), cod, e\.sal_office, e\.iteracoes\), n = await K\.novaChave\(subtle\(\)\);/);
  assert.match(ent, /pedir\("api\/conversa\/registar", \{ method: "POST", headers: JSONH, body: JSON\.stringify\(\{ verificador: v, kid: n\.kid, chave: n\.hexa \}\) \}\)/);
  assert.match(ent, /S\.csrf = r\.j\.csrf; S\.chave = n\.chave; S\.kid = n\.kid;\n    await K\.guardarChave\(globalThis\.indexedDB, S\.chave, S\.kid\)/);
  assert.doesNotMatch(ent, /K\.chave\(|sal_ponte/);
  assert.doesNotMatch(src, /api\/conversa\/sessao/);
  assert.match(ent, /f\.reset\(\)/);
  // entradas seguintes: o desafio do GET estado assinado com K; um 403 de assinatura apaga a chave
  const dr = src.slice(src.indexOf("async function entrarComChave"), src.indexOf("const esquecerChave"));
  assert.match(dr, /pedir\("api\/conversa\/entrar", \{ method: "POST", headers: JSONH,\n      body: JSON\.stringify\(\{ kid: S\.kid, desafio: d, assinatura: await K\.assinarEntrada\(subtle\(\), S\.chave, d\) \}\) \}\)/);
  assert.match(dr, /S\.est\.desafio = null;/, "o desafio é de uso único");
  assert.match(dr, /else if \(r\.status === 403 && \/assinatura\/\.test\(str\(r\.j\.error\)\)\) esquecerChave\(\);/);
  assert.doesNotMatch(src, /localStorage\.setItem\([^)]*cod/);
});

test("Lite §2.7 (guardião): POST só com X-Csrf (sem X-Office-Token); pedido assinado; permissões do instalador; nova com id do browser", () => {
  const src = ler("conversa.js");
  assert.match(src, /const H = \(extra = \{\}\) => \(\{ \.\.\.\(S\.csrf \? \{ "X-Csrf": S\.csrf \} : \{\}\), \.\.\.extra \}\);/);
  assert.doesNotMatch(src, /X-Office-Token|tokenNovo|getState|MSG_TOKEN/);
  assert.match(src, /corpo = await K\.assinado\(subtle\(\), S\.chave, c\)/);
  assert.match(src, /permissoes: S\.est\?\.permissoes_config \}\);/);
  assert.match(src, /const id = K\.novoId\(\), r = await enviarPedido\("nova", \{ conversa: id \}\);/);
  assert.match(src, /"X-Conversa-Pedido": JSON\.stringify\(p\)/);
  assert.match(src, /\{ acao: "transcrever", conversa: id, audio: K\.aleatorioHex\(32\), formato: formatoDe\(tipo\),\n    bytes: buf\.byteLength, sha256, idioma:/);
  assert.equal(C.formatoDe("audio/webm;codecs=opus"), "webm");
  assert.equal(C.formatoDe("audio/mp4"), "m4a");
  assert.equal(C.formatoDe("audio/wav"), null);
});

test("Lite CA-23/CA-27: voz desligada sem microfone e com a nota do teclado; externa pede o aviso por fornecedor; erros por baixo da caixa", () => {
  const src = ler("conversa.js");
  assert.match(src, /b\.hidden = !vozModo\(\); \$\("\.cp-dita"\)\.hidden = !!vozModo\(\);/);
  assert.match(src, /<p class="cp-dita sub" hidden>O teclado do telemóvel também dita/);
  assert.equal(C.avisoVoz("api.exemplo.pt"), "O áudio é enviado para api.exemplo.pt; a privacidade e o custo passam a ser contigo.");
  assert.match(src, /if \(vozModo\(\) === "externa" && S\.est\.aviso_voz !== true\) return avisoVozExterna\(\);/);
  assert.match(src, /if \(vozModo\(\) && S\.est\.voz_ponte !== S\.est\.voz\) return avisoMic\(/);
  // feedback no mesmo toque (antes de qualquer await) e o texto ditado só para a caixa
  assert.match(src, /S\.mic = micEstado\(S\.mic, "pedir"\);\n  avisoMic\(explicada \? "" : VOZ_MSG\.explicar\);\n  pintarMic\(VOZ_MSG\.pedir\);/);
  const fim = src.slice(src.indexOf("function vozFim"), src.indexOf("// ---- página: abrir"));
  assert.doesNotMatch(fim, /enviarPedido|enviarTexto/);
});

test("Lite CA-19: nenhum nome antigo nem «Director» como marca na Conversa", () => {
  for (const f of ["conversa.js", "conversa.css", "conversa_chave.js", "md.js", "som.js", "estado.js", "agora.js", "index.html", "definicoes.js"]) {
    assert.doesNotMatch(ler(f), new RegExp("jarvi" + "s", "i"), f);
    assert.doesNotMatch(ler(f), /\bDirector\b/, f);
  }
});

test("Lite CA-35: alvos de 44 px na Conversa; o Office não carrega o JS nem o CSS dela", () => {
  const css = ler("conversa.css");
  assert.match(css, /\.cv-b \{ min-height: 44px;/);
  assert.match(css, /#cv-mic \{[^}]*min-width: 44px/);
  assert.match(css, /\.cp-cod input \{ min-height: 44px;/);
  assert.match(css, /\.cp-tls a \{[^}]*min-height: 44px;/);
  assert.match(css, /\.cp-def summary \{ min-height: 44px;/);
  assert.match(css, /#cp-nome-in \{[^}]*min-height: 44px;/);
  const h = ler("index.html");
  assert.match(h, /cfg\.conversa\?\.ativa === true\) cvB \|\|= import\("\.\/conversa_botao\.js"\)/);
  assert.doesNotMatch(h, /conversa\.js|conversa\.css|conversa_chave/);
});

test("QA B3: esperarCriada sonda os eventos até 200 (true) ou até ao prazo (false); outro erro pára logo", async () => {
  const corre = async (codigos, ate = 10) => {
    let t = 0, n = 0;
    const r = await esperarCriada(async () => codigos[Math.min(n++, codigos.length - 1)], ate, () => t, async (ms) => { t += ms / 500; });
    return [r, n];
  };
  assert.deepEqual(await corre([200]), [true, 1], "já existe: envia logo");
  assert.deepEqual(await corre([404, 404, 200]), [true, 3], "espera que a ponte a crie");
  assert.deepEqual(await corre([404], 3), [false, 4], "a ponte não a criou no prazo");
  assert.deepEqual(await corre([401]), [false, 1], "sem sessão: não fica à espera");
  assert.deepEqual(await corre([0, 200]), [false, 1], "sem ligação: não fica à espera");
  // no «Enviar»: com a conversa a nascer, primeiro espera e só depois manda (e mostra «a criar a conversa»)
  const src = ler("conversa.js"), f = src.slice(src.indexOf("async function enviarTexto"), src.indexOf("async function parar"));
  const i = f.indexOf("if (ate) {"), j = f.indexOf('enviarPedido("enviar"');
  assert.ok(i > 0 && i < j, "a espera vem antes do envio");
  assert.match(f, /const id = S\.id, ate = criandoAte\(id\);/);
  assert.match(f.slice(i, j), /pintar\(msg, "a-processar", "a criar a conversa", ate - CRIAR_MS\);\n    const pronta = await esperarCriada\(/);
  assert.match(f.slice(i, j), /api\/conversa\/eventos\?/);
  assert.match(f.slice(i, j), /if \(!pronta\) \{ S\.aguarda = 0;/);
});

test("guardião S2: a página própria sem JS nem CSS inline (CSP script-src/style-src 'self'); o manifesto tem tudo o que ela carrega", () => {
  const h = ler("conversa.html"), man = ler("conversa.manifesto").trim().split("\n");
  assert.equal(man[0], "conversa.html");
  for (const n of man) assert.match(n, /^(fonts\/)?[a-z0-9_-]+\.(html|js|css|woff2|svg|png)$/, n);
  assert.equal(new Set(man).size, man.length);
  // sem <script> com corpo, sem handlers on*=, sem style= nem <style>, sem javascript:
  assert.doesNotMatch(h, /<script(?![^>]*\bsrc=)[^>]*>|<style|\sstyle=|\son[a-z]+=|javascript:/i);
  assert.match(h, /<script type="module" src="conversa_inicio\.js"><\/script>/);
  // cada ficheiro que a página, os módulos e o CSS pedem está no manifesto (o guardião só serve esses)
  const pedidos = new Set([...h.matchAll(/(?:src|href)="([a-z0-9_-]+\.[a-z0-9]+)"/g)].map((m) => m[1]));
  for (const f of man.filter((n) => n.endsWith(".js"))) for (const m of ler(f).matchAll(/from "\.\/([^"]+)"|import\("\.\/([^"]+)"\)/g)) pedidos.add(m[1] || m[2]);
  for (const f of man.filter((n) => n.endsWith(".css"))) for (const m of ler(f).matchAll(/url\(([^)]+)\)/g)) pedidos.add(m[1]);
  for (const f of pedidos) assert.ok(man.includes(f), `${f} fora do manifesto`);
  assert.ok(!man.includes("index.html") && !man.includes("conversa_botao.js"), "nada do Office na origem da Conversa");
  // o sprite do microfone vem na página (o do Office fica noutra origem) e o CSS é autónomo (tokens dos temas incluídos)
  assert.match(h, /<svg class="cv-sprite" hidden aria-hidden="true">\n<symbol id="i-mic" /);
  assert.match(ler("conversa.js"), /<use href="#i-mic"\/>/);
  assert.match(ler("conversa.css"), /^:root \{\n  --bg: #12141c;/m);
  assert.match(ler("conversa.css"), /@media \(prefers-color-scheme: light\) \{ :root \{/);
});

test("arranque sem token: conversa_inicio.js chama iniciar({ tocar }) com o som.js; nada do estado do Office", () => {
  const ini = ler("conversa_inicio.js"), src = ler("conversa.js");
  assert.match(ini, /^import \{ iniciar \} from "\.\/conversa\.js";$/m);
  assert.match(ini, /^import \{ criarSom, lerPrefs \} from "\.\/som\.js";$/m);
  assert.match(ini, /^iniciar\(\{ tocar \}\);$/m);
  assert.doesNotMatch(ini + src, /api\/state|getState|X-Office-Token|"token"/);
  // a API é a desta origem (caminhos relativos à raiz servida pelo guardião), sempre com o cookie da própria origem
  assert.match(src, /credentials: "same-origin"/);
  assert.doesNotMatch(src, /https?:\/\/(?!github\.com\/MozThinker)/);
  // a chave fica no IndexedDB desta origem; uma assinatura recusada (403) apaga-a e pede o código
  assert.match(src, /const esquecerChave = \(\) => \{ S\.chave = S\.kid = null; K\.apagarChave\(globalThis\.indexedDB\); \};/);
  assert.match(src, /const chaveRecusada = \(r\) => \{ esquecerChave\(\); semSessao\(\); return r; \};/);
  assert.equal(src.match(/if \(r\.status === 403 && \/assinatura\/\.test\(str\(r\.j\.error\)\)\) return chaveRecusada\(r\);/g).length, 2);
});

test("Definições na página (vieram das do Office): conversaLinhas e o nome com Guardar/Repor pelas prefs", () => {
  assert.deepEqual(conversaLinhas(null), ["Sem ligação à Conversa."]);
  assert.deepEqual(conversaLinhas({ permissoes: "cartao", permissoes_config: "cartao", voz: "desligada", aviso_tokens: true }),
    ["Permissões: cartão (cada acção com efeito pede um clique)", "Voz: desligada (o teclado do telemóvel também dita)", "Aviso de consumo de tokens: aceite"]);
  const l = conversaLinhas({ permissoes: "cartao", permissoes_config: "espelho", voz: "externa", voz_fornecedor: "api.x.pt", voz_ponte: "desligada", aviso_voz: false });
  assert.match(l[0], /o instalador pede espelho; a ponte só permite cartao/);
  assert.equal(l[1], "Voz: externa (api.x.pt) · não instalada no servidor");
  assert.equal(l[3], "Aviso da voz externa: por aceitar");
  const src = ler("conversa.js");
  assert.match(src, /<input id="cp-nome-in" name="nome" maxlength="40" autocomplete="off" required>/);
  assert.match(src, /if \(a === "nome-repor"\) return mudarNome\(null\);/);
  assert.match(src, /const r = await gravarPrefs\(\{ nome \}\);/);
  assert.doesNotMatch(ler("definicoes.js"), /onversa/);
});

test("R6: a espera da «Nova conversa» é só da conversa criada ({id, ate}); limpa ao mudar; microfone e «Parar» esperam", () => {
  const src = ler("conversa.js");
  assert.match(src, /const criandoAte = \(id\) => \(id && S\.criando\?\.id === id \? S\.criando\.ate : 0\);/);
  assert.match(src, /S\.criando = \{ id, ate: Date\.now\(\) \+ CRIAR_MS \}; return abrirConversa\(id\)/);
  assert.doesNotMatch(src, /S\.criando = 0|Date\.now\(\) < S\.criando|S\.criando - CRIAR_MS/);
  // ao mudar de conversa (outra, a lista ou «não existe») a espera acaba
  assert.match(src, /if \(S\.criando\?\.id !== id\) S\.criando = null;/);
  assert.match(src, /async function mostrarLista\(\) \{\n  S\.g\+\+; S\.id = null; S\.criando = null;/);
  assert.match(src, /function naoEncontrada\(\) \{\n  S\.g\+\+; S\.id = null; S\.criando = null;/);
  // microfone e «Parar» com a conversa ainda por criar: texto visível, nada enviado
  const mic = src.slice(src.indexOf("function micClique"), src.indexOf("async function micIniciou"));
  assert.ok(mic.indexOf("if (criandoAte(S.id) > Date.now()) return avisoMic(AINDA_A_CRIAR);") < mic.indexOf("criarGravador("));
  const pa = src.slice(src.indexOf("async function parar"), src.indexOf("async function arquivar"));
  assert.ok(pa.indexOf("if (criandoAte(S.id) > Date.now())") < pa.indexOf("enviarPedido("));
});

test("R5: os textos da página da Conversa falam da Conversa e do browser, não do Office", () => {
  const src = ler("conversa.js");
  assert.match(C.VOZ_MSG.explicar, /^O browser vai pedir o microfone/);
  assert.match(src, /"Sem ligação à Conversa\. Fecha e abre de novo daqui a pouco\."/);
  assert.doesNotMatch(src, /Sem ligação ao Office|O Office vai pedir/);
});
