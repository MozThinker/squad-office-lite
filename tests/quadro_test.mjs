// Testes das funções puras de static/quadro.js (node --test tests/quadro_test.mjs).
// O import em si prova que o módulo não toca em window/document ao carregar.
process.env.TZ = "Europe/Lisbon";
import test from "node:test";
import assert from "node:assert/strict";
import {
  COL, AV, avDe, corDe, LIM, normaliza, filtra, contagens, porColuna, idade, atrasado, custo, parseEtiquetas, validaForm, moverLocal,
  posicaoDrop, posGlobal, limpaTexto, alvoTeclado, lerPrefs, gravarPrefs, safePr, fmtData, fmtDur,
} from "../static/quadro.js";

const COLUNAS = [["ideias", "Ideias"], ["a-fazer", "A fazer"], ["em-curso", "Em curso"], ["a-espera", "À espera de Ana"], ["feito", "Feito"]]
  .map(([chave, rotulo]) => ({ chave, rotulo }));
const ROT = { loja: "Loja", ebook: "E-book", office: "Escritório", blog: "Blog" };
const man = (id, coluna, ordem, m = {}) => ({ id, tipo: "manual", fonte: "manual", projecto: "loja", titulo: id, coluna, ordem, etiquetas: [], antigo: false, ...m });
const auto = (id, coluna, m = {}) => ({ id, tipo: "auto", fonte: "dev", projecto: "office", titulo: id, coluna, ordem: null, etiquetas: [], antigo: false, ...m });
const AGORA = Date.parse("2026-11-04T15:00:00+00:00");
const ha = (ms) => new Date(AGORA - ms).toISOString();
const H = 3600e3, DIA = 24 * H;

test("listas fechadas: um ícone e estado vazio por coluna, avatar por fonte", () => {
  assert.deepEqual(Object.keys(COL), COLUNAS.map((c) => c.chave));
  for (const [ic, vazio] of Object.values(COL)) { assert.ok(ic); assert.ok(vazio.length > 10); }
  for (const f of ["aprovacao", "brief", "manual"]) assert.equal(AV[f].length, 2);
  assert.deepEqual(Object.keys(AV), ["aprovacao", "brief", "manual"]);   // as squads vêm da config (avDe)
});

test("config: avatar por squad da config e cor c0…c7 do projecto (desconhecido = outro)", () => {
  const cfg = { squads: [{ id: "dev", nome: "Dev" }, { id: "ops", nome: "ôps" }], projectos: [{ id: "loja", cor: "c0" }, { id: "blog", cor: "c1" }] };
  assert.deepEqual(avDe("dev", cfg), ["D", "squad Dev"]);
  assert.deepEqual(avDe("ops", cfg), ["Ô", "squad ôps"]);
  assert.deepEqual(avDe("brief", cfg), AV.brief);
  for (const f of ["marketing", "incidente", "constructor", "__proto__", undefined]) assert.equal(avDe(f, cfg), null, String(f));
  assert.equal(avDe("dev", null), null);
  assert.equal(corDe("blog", cfg), "c1");
  for (const k of ["outro", "x", "constructor", undefined]) assert.equal(corDe(k, cfg), "outro", String(k));
  assert.equal(corDe("loja", null), "outro");
});

test("normaliza: sem acentos, sem maiúsculas, espaços simples", () => {
  assert.equal(normaliza("  À  Espera   de ÂNGELA "), "a espera de angela");
  assert.equal(normaliza(null), "");
});

test("filtra: multi-projecto e pesquisa em título, etiquetas, projecto (chave/rótulo) e id, sem acentos", () => {
  const l = [man("m-1", "ideias", 10, { titulo: "Preço anual", etiquetas: ["Black Friday"] }), auto("dev:20261103-x", "feito"),
    man("m-2", "ideias", 20, { projecto: "ebook", titulo: "Capa nova" })];
  assert.equal(filtra(l).length, 3);
  assert.deepEqual(filtra(l, { p: ["loja", "ebook"] }).map((c) => c.id), ["m-1", "m-2"]);
  assert.deepEqual(filtra(l, { q: "PRECO" }, ROT).map((c) => c.id), ["m-1"]);
  assert.deepEqual(filtra(l, { q: "black fri" }, ROT).map((c) => c.id), ["m-1"]);
  assert.deepEqual(filtra(l, { q: "escritorio" }, ROT).map((c) => c.id), ["dev:20261103-x"]);   // rótulo «Escritório» sem acento
  assert.deepEqual(filtra(l, { q: "20261103" }, ROT).map((c) => c.id), ["dev:20261103-x"]);  // id
  assert.deepEqual(filtra(l, { q: "e-book" }, ROT).map((c) => c.id), ["m-2"]);
  assert.deepEqual(filtra(l, { p: ["ebook"], q: "preco" }, ROT), []);
});

test("contagens: colunas com filtros, Feito sem os antigos; chips por projecto só com a pesquisa", () => {
  const l = [man("m-1", "ideias", 10), auto("a", "feito"), auto("b", "feito", { antigo: true }), auto("c", "feito", { projecto: "loja" })];
  assert.deepEqual(contagens(l, {}, ROT), { col: { ideias: 1, feito: 2 }, proj: { loja: 2, office: 1 }, total: 3 });
  const f = contagens(l, { p: ["loja"] }, ROT);
  assert.deepEqual(f.col, { ideias: 1, feito: 1 });
  assert.deepEqual(f.proj, { loja: 2, office: 1 });   // o chip de outro projecto continua a mostrar quantos tem
});

test("porColuna: manuais primeiro por ordem, depois automáticos na ordem do servidor", () => {
  const l = [auto("a1", "ideias"), man("m-2", "ideias", 20), auto("a2", "ideias"), man("m-1", "ideias", 10), man("m-3", "feito", 10), auto("x", "outra")];
  const m = porColuna(l, COLUNAS);
  assert.deepEqual(m.ideias.map((c) => c.id), ["m-1", "m-2", "a1", "a2"]);
  assert.deepEqual(m.feito.map((c) => c.id), ["m-3"]);
  assert.deepEqual(m["a-fazer"], []);
  assert.equal(Object.keys(m).length, 5);   // coluna desconhecida não cria coluna
});

test("idade: agora, minutos, horas, hoje, 1 dia, N dias; vazio sem data", () => {
  assert.equal(idade(ha(30e3), AGORA), "agora");
  assert.equal(idade(ha(-60e3), AGORA), "agora");   // relógios desacertados: nunca negativo
  assert.equal(idade(ha(35 * 60e3), AGORA), "há 35 min");
  assert.equal(idade(ha(2 * H), AGORA), "há 2 h");
  assert.equal(idade(ha(7 * H), AGORA), "hoje");                                            // 08:00 do mesmo dia
  assert.equal(idade("2026-11-03T23:00:00+00:00", AGORA), "há 16 h");                     // ontem, < 24 h
  assert.equal(idade(ha(DIA + H), AGORA), "há 1 dia");
  assert.equal(idade(ha(5 * DIA), AGORA), "há 5 dias");
  assert.equal(idade(null, AGORA), "");
  assert.equal(idade("lixo", AGORA), "");
});

test("atrasado: só À espera com mais de 3 dias", () => {
  assert.equal(atrasado(auto("a", "a-espera", { desde: ha(3 * DIA + 60e3) }), AGORA), true);
  assert.equal(atrasado(auto("a", "a-espera", { desde: ha(3 * DIA - 60e3) }), AGORA), false);
  assert.equal(atrasado(auto("a", "em-curso", { desde: ha(9 * DIA) }), AGORA), false);
  assert.equal(atrasado(auto("a", "a-espera", { desde: null }), AGORA), false);
});

test("custo: US$ com vírgula; nada sem número", () => {
  assert.equal(custo(4.1), "US$ 4,10");
  assert.equal(custo(0), "US$ 0,00");
  assert.equal(custo(35.386), "US$ 35,39");
  for (const v of [null, undefined, "4", NaN, Infinity]) assert.equal(custo(v), "");
});

test("parseEtiquetas: separa por vírgula, tira espaços, vazios e repetidos", () => {
  assert.deepEqual(parseEtiquetas(" preço, teste ,,Preço, TESTE , a11y"), ["preço", "teste", "a11y"]);
  assert.deepEqual(parseEtiquetas(""), []);
});

test("validaForm: limites iguais aos do servidor, em code points", () => {
  assert.deepEqual(validaForm({ titulo: "Ok", descricao: "", etiquetas: [] }), {});
  assert.ok(validaForm({ titulo: "   " }).titulo);
  assert.deepEqual(validaForm({ titulo: "x".repeat(LIM.titulo) }), {});
  assert.ok(validaForm({ titulo: "x".repeat(LIM.titulo + 1) }).titulo);
  assert.deepEqual(validaForm({ titulo: "😀".repeat(LIM.titulo) }), {});   // 120 emojis = 240 UTF-16, 120 para o Python
  assert.ok(validaForm({ titulo: "a\u0007b" }).titulo);
  assert.ok(validaForm({ titulo: "a", descricao: "d".repeat(LIM.descricao + 1) }).descricao);
  assert.deepEqual(validaForm({ titulo: "a", descricao: "d\r\n".repeat(1000) }), {});   // \r\n conta como \n
  assert.ok(validaForm({ titulo: "a", etiquetas: ["1", "2", "3", "4", "5", "6"] }).etiquetas);
  assert.ok(validaForm({ titulo: "a", etiquetas: ["e".repeat(LIM.etiqueta + 1)] }).etiquetas);
  assert.deepEqual(validaForm({ titulo: "a", etiquetas: ["e".repeat(LIM.etiqueta)] }), {});
});

test("moverLocal: renumera só o destino 10/20/30, não muta o original (serve para reverter)", () => {
  const l = [man("a", "ideias", 10), man("b", "ideias", 20), man("c", "ideias", 30), man("x", "a-fazer", 10), auto("d", "a-fazer")];
  const copia = structuredClone(l);
  const n = moverLocal(l, "c", "ideias", 0);
  assert.deepEqual(l, copia);
  assert.deepEqual(porColuna(n, COLUNAS).ideias.map((c) => [c.id, c.ordem]), [["c", 10], ["a", 20], ["b", 30]]);
  const m = moverLocal(l, "a", "a-fazer", 1);
  assert.deepEqual(porColuna(m, COLUNAS)["a-fazer"].map((c) => [c.id, c.ordem]), [["x", 10], ["a", 20], ["d", null]]);
  assert.deepEqual(porColuna(m, COLUNAS).ideias.map((c) => [c.id, c.ordem]), [["b", 20], ["c", 30]]);   // origem não renumera (como o servidor)
  assert.deepEqual(porColuna(moverLocal(l, "a", "feito", 99), COLUNAS).feito.map((c) => [c.id, c.ordem]), [["a", 10]]);   // posição acima do fim = fim
  assert.equal(moverLocal(l, "d", "ideias", 0), l);   // automático não mexe
  assert.equal(moverLocal(l, "nada", "ideias", 0), l);
});

test("posicaoDrop: índice do primeiro meio abaixo do ponteiro", () => {
  assert.equal(posicaoDrop([], 50), 0);
  assert.equal(posicaoDrop([100, 200, 300], 50), 0);
  assert.equal(posicaoDrop([100, 200, 300], 150), 1);
  assert.equal(posicaoDrop([100, 200, 300], 999), 3);
});

test("alvoTeclado: setas dentro dos limites; ←/→ mantêm a posição até ao fim do destino", () => {
  const cols = COLUNAS.map((c) => c.chave), ns = { ideias: 2, "a-fazer": 0, "em-curso": 5 };
  assert.deepEqual(alvoTeclado(cols, ns, { coluna: "ideias", posicao: 0 }, "ArrowUp"), { coluna: "ideias", posicao: 0 });
  assert.deepEqual(alvoTeclado(cols, ns, { coluna: "ideias", posicao: 0 }, "ArrowDown"), { coluna: "ideias", posicao: 1 });
  assert.deepEqual(alvoTeclado(cols, ns, { coluna: "ideias", posicao: 2 }, "ArrowDown"), { coluna: "ideias", posicao: 2 });
  assert.deepEqual(alvoTeclado(cols, ns, { coluna: "ideias", posicao: 2 }, "ArrowLeft"), { coluna: "ideias", posicao: 2 });
  assert.deepEqual(alvoTeclado(cols, ns, { coluna: "ideias", posicao: 2 }, "ArrowRight"), { coluna: "a-fazer", posicao: 0 });
  assert.deepEqual(alvoTeclado(cols, ns, { coluna: "a-fazer", posicao: 0 }, "ArrowRight"), { coluna: "em-curso", posicao: 0 });
  assert.deepEqual(alvoTeclado(cols, ns, { coluna: "feito", posicao: 0 }, "ArrowRight"), { coluna: "feito", posicao: 0 });
  assert.deepEqual(alvoTeclado(cols, ns, { coluna: "ideias", posicao: 1 }, "Enter"), { coluna: "ideias", posicao: 1 });
});

test("lerPrefs/gravarPrefs: storage que lança, lixo e valores fora do formato", () => {
  const mau = { getItem() { throw new Error("x"); }, setItem() { throw new Error("x"); } };
  assert.deepEqual(lerPrefs(mau), { p: [], q: "" });
  assert.equal(gravarPrefs(mau, { p: ["loja"] }), false);
  assert.deepEqual(lerPrefs(undefined), { p: [], q: "" });
  const mem = new Map(), st = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
  assert.equal(gravarPrefs(st, { p: ["loja", "ebook"], q: "preço" }), true);
  assert.deepEqual(lerPrefs(st), { p: ["loja", "ebook"], q: "preço" });
  mem.set("quadro", "{nope");
  assert.deepEqual(lerPrefs(st), { p: [], q: "" });
  mem.set("quadro", JSON.stringify({ p: ["ok", "__proto__", "<x>", 3, "ok", "A"], q: "q".repeat(300) }));
  assert.deepEqual(lerPrefs(st), { p: ["ok"], q: "q".repeat(100) });
});

test("safePr: só https://github.com/<o>/<r>/pull/<n>", () => {
  assert.equal(safePr("https://github.com/exemplo-org/squad-office/pull/15"), "https://github.com/exemplo-org/squad-office/pull/15");
  for (const u of ["javascript:alert(1)", "https://github.com/a/b/pull/1\"><x", "http://github.com/a/b/pull/1", "https://evil.com/a/b/pull/1",
    "https://github.com/a/b/pull/1/files", "https://github.com.evil.com/a/b/pull/1", null, ""]) assert.equal(safePr(u), "", u);
});

test("fmtData/fmtDur", () => {
  assert.equal(fmtData("2026-11-03T23:59:00+00:00"), "03/11 23:59");
  assert.equal(fmtData("ontem"), "ontem");
  assert.equal(fmtDur(42), "42 s");
  assert.equal(fmtDur(120), "2 min");
  assert.equal(fmtDur(5400), "1,5 h");
});

test("posGlobal: com filtro ou antigos escondidos, a posição é «antes do manual visível seguinte» no índice global (CR3)", () => {
  // ideias: a(loja) b(ebook, escondido pelo filtro) c(loja) d(ebook, escondido)
  const l = [man("a", "ideias", 10), man("b", "ideias", 20, { projecto: "ebook" }), man("c", "ideias", 30), man("d", "ideias", 40, { projecto: "ebook" }),
    man("x", "a-fazer", 10)];
  // com só Loja à vista, largar «x» entre a e c (visível 1) = antes de c → índice global 2
  assert.equal(posGlobal(l, "x", "ideias", "c"), 2);
  assert.deepEqual(porColuna(moverLocal(l, "x", "ideias", posGlobal(l, "x", "ideias", "c")), COLUNAS).ideias.map((c) => c.id), ["a", "b", "x", "c", "d"]);
  assert.equal(posGlobal(l, "x", "ideias", null), 4);                 // sem seguinte visível = fim da coluna (depois do escondido d)
  assert.equal(posGlobal(l, "a", "ideias", "c"), 1);                  // o próprio não conta (mesma coluna)
  assert.equal(posGlobal(l, "a", "ideias", "a"), 3);                  // seguinte inválido = fim
  // Feito com «Ver mais»: sem seguinte recente = depois do último manual recente (antes dos antigos)
  const f = [man("r1", "feito", 10), man("o1", "feito", 20, { antigo: true }), man("r2", "feito", 30), man("o2", "feito", 40, { antigo: true }), man("y", "ideias", 10)];
  assert.equal(posGlobal(f, "y", "feito", null), 3);
  assert.equal(posGlobal(f, "y", "feito", "r2"), 2);
  assert.equal(posGlobal([man("o", "feito", 10, { antigo: true }), man("y", "ideias", 10)], "y", "feito", null), 0);
  // «mesmo sítio» no mesmo referencial: a origem calculada da mesma forma dá o mesmo número
  assert.equal(posGlobal(l, "c", "ideias", "d"), posGlobal(l, "c", "ideias", "d"));
});

test("limpaTexto: C0/C1 saem; a descrição (nl) mantém \\n e \\t, os outros campos passam o tab a espaço (B4)", () => {
  assert.equal(limpaTexto("col1\tcol2\r\nlinha\u0007\u0085\u007f", true), "col1\tcol2\nlinha");
  assert.equal(limpaTexto("título\ncom\tlixo\u0000"), "títulocom lixo");
  // editar só o título não pode reenviar a descrição: a original com tab é igual à limpa
  const orig = "| a\t| b |\n[segredo]";
  assert.equal(limpaTexto(orig, true), orig);
  assert.equal(limpaTexto(null), "");
  assert.deepEqual(validaForm({ titulo: limpaTexto("a\u0085b"), descricao: limpaTexto("x\ty", true), etiquetas: [] }), {});
});
