// Lançar a partir do Quadro: funções puras de static/quadro.js (node --test tests/quadro_lancar_test.mjs). SPEC office-quadro-lancar.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { slugDe, sugestao, corpoLancar, validaLancar, deveLancar, fundeManuais, kbFaltam, bytes, camposLancar, BRIEF_MAX, lerLancar, podeLancarMenu, verDica,
  gravarDica, blocosH, moverLocal } from "../static/quadro.js";

// config de teste (forma do GET api/config): dev e ops lançáveis, «notas» de sessão (não lançável); projectos loja e blog
const sqd = (id, nome, tipos, modos, formato = "worktree") => ({ id, nome, lead: "tech-lead", personagem: null, formato, tipos, modos });
const CFG = { squads: [sqd("dev", "Dev", ["feat", "fix"], ["goal", "task"]), sqd("ops", "Ops", ["infra"], ["task"]), sqd("notas", "Notas", [], [], "sessao")],
  projectos: [{ id: "loja", nome: "Loja", glifo: "◆", cor: "c0" }, { id: "blog", nome: "Blog", glifo: "▤", cor: "c1" }],
  lancar: { squads: ["dev", "ops"], projectos: ["loja", "blog"] } };
const FX = JSON.parse(readFileSync(new URL("./fixtures/api_config_exemplo.json", import.meta.url), "utf8"));
const man = (id, coluna, m = {}) => ({ id, tipo: "manual", fonte: "manual", projecto: "loja", titulo: id, coluna, ordem: 10, etiquetas: [], ...m });
const ok = { squad: "dev", projecto: "loja", kind: "feat", mode: "goal", slug: "quadro-lancar", titulo: "Quadro: lançar", brief: "" };
const lanc = (estado, m = {}) => ({ id: "20261106T023000-a1b2c3d4", estado, squad: "dev", task_id: null, erro: null, pedido_em: "2026-11-06T02:30:00+01:00", ...m });

test("AC7: slug sem acentos, minúsculas e «-» simples; > 40 corta numa fronteira e nunca acaba em «-»", () => {
  assert.equal(slugDe("Lançar trabalho — Quadro (v2)!"), "lancar-trabalho-quadro-v2");
  assert.equal(slugDe("  --Olá, MUNDO--  "), "ola-mundo");
  assert.equal(slugDe(""), "");
  assert.equal(slugDe(null), "");
  const longo = "Palavra ".repeat(13).slice(0, 100);   // 100 caracteres
  assert.equal(longo.length, 100);
  const s = slugDe(longo);
  assert.ok(s.length <= 40, s);
  assert.doesNotMatch(s, /-$/);
  assert.equal(s, "palavra-palavra-palavra-palavra-palavra");   // 39: recua até à última fronteira
  assert.equal(slugDe("a".repeat(100)), "a".repeat(40));              // sem fronteira: corta a 40
  assert.equal(slugDe("x".repeat(40) + " fim"), "x".repeat(40));     // «-» exactamente na posição 40
  assert.match(slugDe("Lançar trabalho — Quadro (v2)!"), /^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$/);
});

test("AC5: sugestão = 1.ª squad lançável (tipo e modo dela) e o projecto do cartão se for lançável", () => {
  assert.deepEqual(sugestao("blog", CFG), { squad: "dev", projecto: "blog", kind: "feat", mode: "goal" });
  assert.deepEqual(sugestao("exemplo", FX), { squad: "dev", projecto: "exemplo", kind: "feat", mode: "goal" });
  for (const k of ["outro", "notas", "constructor", "__proto__", undefined, "exemplo"]) assert.equal(sugestao(k, CFG).projecto, null, String(k));
  // sem squads lançáveis (config ainda não chegou): nada sugerido
  for (const c of [null, undefined, {}, { squads: CFG.squads, lancar: { squads: [], projectos: [] } }])
    assert.deepEqual(sugestao("loja", c), { squad: null, projecto: null, kind: null, mode: null });
  const s2 = sugestao("loja", { ...CFG, lancar: { squads: ["ops"], projectos: ["loja"] } });
  assert.deepEqual(s2, { squad: "ops", projecto: "loja", kind: "infra", mode: "task" });
});

test("AC5: «outro» não tem projecto → Lançar desactivado (erro no projecto)", () => {
  const v = { ...sugestao("outro", CFG), slug: "abc", titulo: "t", brief: "" };
  assert.deepEqual(Object.keys(validaLancar(v, CFG)), ["projecto"]);
  assert.deepEqual(validaLancar({ ...v, projecto: "loja" }, CFG), {});
  assert.ok(validaLancar({ ...v, projecto: "Loja" }, CFG).projecto);   // só ids (o nome não serve)
  assert.ok(validaLancar({ ...v, projecto: "loja" }, { ...CFG, lancar: { squads: ["dev"], projectos: [] } }).projecto);
});

test("AC6: só squads lançáveis (worktree) da config, com os tipos e modos dela; o corpo leva o id do projecto", () => {
  assert.deepEqual(camposLancar(CFG), { dev: { nome: "Dev", kind: ["feat", "fix"], mode: ["goal", "task"] }, ops: { nome: "Ops", kind: ["infra"], mode: ["task"] } });
  assert.deepEqual(Object.keys(camposLancar(FX)), ["dev"]);
  assert.deepEqual(camposLancar(null), {});
  assert.deepEqual(corpoLancar(ok), ok);
  const op = corpoLancar({ ...ok, squad: "ops", kind: "infra", mode: "task" });
  assert.deepEqual(op, { squad: "ops", projecto: "loja", kind: "infra", mode: "task", slug: "quadro-lancar", titulo: "Quadro: lançar", brief: "" });
  // com token, versao, cartao_id e posicao dá exactamente as 11 chaves do servidor
  const corpo = { ...op, cartao_id: "m-0a1b2c3d4e", posicao: 0, token: "t", versao: "v" };
  assert.deepEqual(Object.keys(corpo).sort(), ["brief", "cartao_id", "kind", "mode", "posicao", "projecto", "slug", "squad", "titulo", "token", "versao"]);
  for (const [sq, c] of Object.entries(camposLancar(CFG))) for (const kind of c.kind) for (const mode of c.mode)
    assert.deepEqual(validaLancar(corpoLancar({ ...ok, squad: sq, kind, mode }), CFG), {}, `${sq}/${kind}/${mode}`);
});

test("validaLancar: mesmas regras do servidor (D3–D5)", () => {
  const e = (m) => Object.keys(validaLancar({ ...ok, ...m }, CFG));
  assert.deepEqual(e({}), []);
  assert.deepEqual(e({ squad: "notas" }), ["squad"]);   // squad de sessão não se lança
  assert.deepEqual(e({ squad: "x" }), ["squad"]);
  assert.deepEqual(e({ squad: "constructor" }), ["squad"]);
  assert.deepEqual(e({ squad: "__proto__" }), ["squad"]);
  assert.deepEqual(e({ kind: "chore" }), ["kind"]);
  assert.deepEqual(e({ squad: "ops", kind: "infra", mode: "goal" }), ["mode"]);   // os modos são os da squad
  assert.deepEqual(e({ mode: "x" }), ["mode"]);
  assert.deepEqual(e({ squad: "ops", kind: "feat", mode: "task" }), ["kind"]);
  assert.deepEqual(Object.keys(validaLancar(ok, FX)), ["projecto"]);   // a fixture só tem o projecto exemplo
  for (const slug of ["ab", "-abc", "abc-", "Abc", "a/b", "../x", "a".repeat(61), "", "a b"]) assert.deepEqual(e({ slug }), ["slug"], slug);
  for (const slug of ["abc", "a-b", "a".repeat(60), "20261106-x9"]) assert.deepEqual(e({ slug }), [], slug);
  assert.deepEqual(e({ titulo: "   " }), ["titulo"]);
  assert.deepEqual(e({ titulo: "x".repeat(120) }), []);
  assert.deepEqual(e({ titulo: "x".repeat(121) }), ["titulo"]);
  assert.deepEqual(e({ titulo: "🙂".repeat(120) }), []);   // code points, como o len() do Python
  assert.deepEqual(e({ titulo: "a\u0085b" }), ["titulo"]);
  assert.deepEqual(e({ brief: "a\n\tb\r\nc" }), []);
  assert.deepEqual(e({ brief: "a\u0001" }), ["brief"]);
  assert.deepEqual(e({ brief: "x".repeat(BRIEF_MAX) }), []);
  assert.deepEqual(e({ brief: "x".repeat(BRIEF_MAX + 1) }), ["brief"]);
  assert.deepEqual(e({ brief: "é".repeat(4097) }), ["brief"]);   // 8194 bytes UTF-8
});

test("validaLancar: sem invisíveis (Cf/Co/Cs, como o servidor) no título e no brief; o ZWJ fica", () => {
  const e = (m) => validaLancar({ ...ok, ...m }, CFG);
  const maus = ["a\u202eb", "a\u2066b\u2069", "\u200bx", "x\ufeffy", "\u{e0041}x", "x\ue000", "x\ud800", "\udc00x", "a\u00adb"];
  for (const s of maus) {
    assert.equal(e({ titulo: s }).titulo, "Tem caracteres invisíveis; apaga-os.", JSON.stringify(s));
    assert.equal(e({ brief: s }).brief, "Tem caracteres invisíveis; apaga-os.", JSON.stringify(s));
  }
  for (const s of ["👨\u200d👩\u200d👧", "Ação — «ok» 🙂", "linha\n\tdois", "novo \u{1fae9}\u{1faea}", "x\u0378"]) assert.deepEqual(e({ titulo: s.replace(/\s/g, " "), brief: s }), {}, JSON.stringify(s));
  assert.equal(e({ titulo: "a\u0001" }).titulo, "Sem caracteres de controlo.");   // controlo continua com a mensagem dele
});

test("AC8: bytes e KB que faltam até 8 KB", () => {
  assert.equal(bytes("é"), 2);
  assert.equal(kbFaltam(""), 8);
  assert.equal(kbFaltam("x".repeat(1024)), 7);
  assert.equal(kbFaltam("x".repeat(100)), 7.9);
  assert.equal(kbFaltam("x".repeat(BRIEF_MAX)), 0);
  assert.ok(kbFaltam("x".repeat(BRIEF_MAX + 1)) < 0);
});

test("AC1/AC24: deveLancar só para manual, de outra coluna para Em curso, sem lançamento activo", () => {
  assert.equal(deveLancar(man("m", "a-fazer"), "em-curso"), true);
  assert.equal(deveLancar(man("m", "ideias", { lancamento: null }), "em-curso"), true);
  assert.equal(deveLancar(man("m", "a-espera", { lancamento: lanc("erro", { erro: "x" }) }), "em-curso"), true);   // erro: pode relançar
  assert.equal(deveLancar(man("m", "a-fazer", { lancamento: lanc("a-lancar") }), "em-curso"), false);
  assert.equal(deveLancar(man("m", "feito", { lancamento: lanc("ligado", { task_id: "t" }) }), "em-curso"), false);
  assert.equal(deveLancar(man("m", "em-curso"), "em-curso"), false);   // reordenar dentro de Em curso
  assert.equal(deveLancar(man("m", "a-fazer"), "a-espera"), false);
  assert.equal(deveLancar({ id: "dev:x", tipo: "auto", coluna: "a-fazer" }, "em-curso"), false);
  assert.equal(deveLancar(undefined, "em-curso"), false);
});

test("fundeManuais: o POST /api/quadro sem «lancamento» mantém o do GET e o que o ligado herdou", () => {
  const herd = { progress: { pct: 40 }, links: { pr: "https://github.com/a/b/pull/1", aprovacao: null, tarefa: "t1" }, selo: { cor: "azul", texto: "a correr" } };
  const antigos = [man("m-1", "em-curso", { lancamento: lanc("ligado", { task_id: "t1" }), ...herd }),
    man("m-2", "em-curso", { lancamento: lanc("a-lancar", { id: "20261106T023100-00000000" }) }), man("m-3", "ideias", { lancamento: null })];
  const base = { progress: null, links: { pr: null, aprovacao: null, tarefa: null }, selo: null };
  const novos = [man("m-1", "a-espera", { ordem: 20, ...base }), man("m-2", "em-curso", base), man("m-3", "a-fazer", base), man("m-4", "ideias", base)];
  const [a, b, c, d] = fundeManuais(antigos, novos);
  assert.deepEqual([a.coluna, a.ordem, a.lancamento.estado, a.progress, a.links, a.selo], ["a-espera", 20, "ligado", herd.progress, herd.links, herd.selo]);
  assert.deepEqual([b.lancamento.estado, b.progress], ["a-lancar", null]);
  assert.deepEqual([c.coluna, c.lancamento], ["a-fazer", null]);
  assert.equal(d.lancamento, null);   // novo cartão
  // resposta do /lancar traz «lancamento»: ganha a do servidor
  const [e] = fundeManuais([man("m-3", "a-fazer", { lancamento: lanc("erro", { erro: "x" }) })],
    [man("m-3", "em-curso", { ...base, lancamento: lanc("a-lancar", { id: "20261106T030000-0000000f" }) })]);
  assert.equal(e.lancamento.estado, "a-lancar");
  // o ligado com outro pedido (relançado) não herda o progresso do antigo
  const [g] = fundeManuais(antigos.slice(0, 1), [man("m-1", "em-curso", { ...base, lancamento: lanc("ligado", { id: "20261107T000000-00000001", task_id: "t2" }) })]);
  assert.equal(g.progress, null);
  assert.equal(fundeManuais([], []).length, 0);
});

// 0.1.3 (F4): o index.html cresce no máximo 4 KB (lang, faixa do runner, primeiros passos, rótulos); o diálogo continua fora dele
test("AC12: o diálogo vive em quadro.js/quadro.css; index.html cresce ≤ 4 KB face ao origin/main", (t) => {
  const html = readFileSync(new URL("../static/index.html", import.meta.url));
  assert.doesNotMatch(html.toString("utf8"), /lancar|Lançar na squad/);
  let base;
  try { base = execFileSync("git", ["show", "origin/main:static/index.html"], { cwd: new URL("..", import.meta.url), stdio: ["ignore", "pipe", "ignore"] }); }
  catch { return t.skip("sem origin/main no git"); }
  assert.ok(html.length <= base.length + 4096, `index.html cresceu ${html.length - base.length} bytes`);
});

// ---- 0.1.3 (F4)
const Q = readFileSync(new URL("../static/quadro.js", import.meta.url), "utf8");

test("C10.4: lerLancar dá o mesmo corpo da 0.1.2 (kind/mode/slug crus; os rótulos são só texto das opções)", () => {
  // formulário falso: um select guarda o value (feat/goal), não o texto («funcionalidade nova», …)
  const f = { squad: { value: "dev" }, projecto: { value: "loja" }, kind: { value: "feat" }, mode: { value: "goal" }, slug: { value: " quadro-lancar " },
    titulo: { value: " Quadro:\tlançar\u0007 " }, brief: { value: "linha 1\r\nlinha 2\u0000" } };
  const v = lerLancar(f);
  assert.deepEqual(corpoLancar(v), { squad: "dev", projecto: "loja", kind: "feat", mode: "goal", slug: "quadro-lancar", titulo: "Quadro: lançar", brief: "linha 1\nlinha 2" });
  assert.deepEqual(Object.keys(corpoLancar(v)), ["squad", "projecto", "kind", "mode", "slug", "titulo", "brief"]);
  assert.deepEqual(validaLancar(v, CFG), {});
  for (const m of ["fix", "task"]) assert.equal(Object.values(lerLancar({ ...f, kind: { value: "fix" }, mode: { value: "task" } })).includes(m), true);
  // as opções levam o valor cru e o rótulo em texto (new Option, nunca HTML)
  assert.match(Q, /new Option\(rotuloDe\(TIPOS, k\), k\)/);
  assert.match(Q, /new Option\(rotuloDe\(TAMANHOS, m\), m\)/);
  for (const s of ['"Tipo"', '"Tamanho"', '"O que queres? Escreve como a um colega."', "<summary>Avançado</summary>", 'f.querySelector(".q-adv").open = true']) assert.ok(Q.includes(s), s);
});

test("C13.2: «Lançar…» no menu ⋯ só com a fila ligada e sem lançamento activo", () => {
  assert.equal(podeLancarMenu(man("a", "ideias"), true), true);
  assert.equal(podeLancarMenu(man("a", "ideias", { lancamento: lanc("erro") }), true), true);
  assert.equal(podeLancarMenu(man("a", "ideias", { lancamento: lanc("a-lancar") }), true), false);
  assert.equal(podeLancarMenu(man("a", "em-curso", { lancamento: lanc("ligado") }), true), false);
  assert.equal(podeLancarMenu(man("a", "ideias"), false), false);
  assert.equal(podeLancarMenu(man("a", "em-curso"), true), true);    // já em «Em curso» sem lançamento (depois de «Só mover»)
  assert.equal(podeLancarMenu(man("a", "em-curso", { lancamento: lanc("erro") }), true), true);
  assert.equal(deveLancar(man("a", "em-curso"), "em-curso"), false);   // arrastar dentro de «Em curso» continua só a reordenar
  // abrirLancar(…, 0) a partir do menu: o que já está em «Em curso» fica nela, no topo, e os outros renumerados
  const l = moverLocal([man("b", "em-curso", { ordem: 10 }), man("a", "em-curso", { ordem: 20 })], "a", "em-curso", 0);
  assert.deepEqual(l.map((x) => [x.id, x.coluna, x.ordem]), [["b", "em-curso", 20], ["a", "em-curso", 10]]);
  assert.equal(podeLancarMenu({ id: "dev:x", tipo: "dev" }, true), false);
  assert.match(Q, /if \(a === "lancar"\) return abrirLancar\(S\.d\.cartoes, id, 0\);/);   // topo de «Em curso», mesmo diálogo
});

test("C13.1: dica de arrastar só em toque e até «Percebi»; sem localStorage aparece (uma vez por carregamento)", () => {
  const st = () => { const m = {}; return { getItem: (k) => m[k] ?? null, setItem: (k, v) => { m[k] = String(v); } }; };
  const a = st();
  assert.equal(verDica(a, false), false);
  assert.equal(verDica(a, true), true);
  gravarDica(a);
  assert.equal(verDica(a, true), false);
  const parte = { getItem: () => { throw new Error("SecurityError"); }, setItem: () => { throw new Error("SecurityError"); } };
  assert.equal(verDica(parte, true), true);
  assert.doesNotThrow(() => gravarDica(parte));
  assert.equal(verDica(undefined, true), true);
  assert.match(Q, /if \(ini\) \{[^}]*\n[^\n]*q\("\.q-dica"\)\.hidden = /);   // só na 1.ª entrada do carregamento
});

test("B7.2/B7.4: o detalhe de um cartão dev em erro mostra o resultado escapado e a dica", () => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const md = (s) => `<p>${esc(s).replace(/`([^`]+)`/g, "<code>$1</code>")}</p>`;
  const h = blocosH({ estado: "erro", resultado: "credenciais do Claude inválidas <img src=x onerror=alert(1)>" }, esc, md);
  assert.match(h, /<h3>Resultado<\/h3><p class="q-blk">credenciais do Claude inválidas &lt;img src=x onerror=alert\(1\)&gt;<\/p>/);
  assert.match(h, /<div class="q-dica2 md"><p>Corre <code>\.\/install\.sh --credenciais<\/code>/);
  assert.doesNotMatch(h, /<img/);
  assert.match(blocosH({ estado: "incompleta", resultado: null }, esc, md), /confirma <code>gh auth status<\/code>/);
  assert.equal(blocosH({ estado: "erro", resultado: "outra coisa" }, esc, md), `<h3>Resultado</h3><p class="q-blk">outra coisa</p>`);
  assert.equal(blocosH({ estado: "concluída" }, esc, md), "");
  // AC-o1: modo docker (o quadro passa A.cfg?.modo)
  assert.match(blocosH({ estado: "erro", resultado: "credenciais do Claude em falta" }, esc, md, "docker"), /Corre <code>docker compose run --rm configurar --credenciais<\/code> no PC/);
  assert.match(Q, /\+ blocosH\(dt, esc, A\.md, A\.cfg\?\.modo\)/);
});

test("C12.2: «Tentar de novo» só relê o quadro e aponta para o doctor (nunca POST)", () => {
  const i = Q.indexOf("retentar: async () =>"), linha = Q.slice(i, Q.indexOf("\n", i));
  assert.match(linha, /await carregar\(\)/);
  assert.doesNotMatch(linha, /post\(|fetch\(|method/);
  assert.match(Q, /anuncia\(doctorTxt\(\)\)/);
  assert.match(Q, /const doctorTxt = \(\) => \{ const o = ondeCorre\(A\.cfg\?\.modo\); return `\$\{o\[0\]\.toUpperCase\(\) \+ o\.slice\(1\)\}: \$\{comandos\(A\.cfg\?\.modo\)\.doctor\}`; \};/);
});

test("C12.1: «parado» e idades no quadro pela hora do servidor (o «agora» do GET), não pelo relógio do telemóvel", () => {
  assert.match(Q, /const d = await r\.json\(\), em = Date\.now\(\);/);
  assert.match(Q, /S\.desvio = desvioRelogio\(d\.agora, em\);/);
  assert.match(Q, /const agoraSrv = \(\) => Date\.now\(\) \+ \(S\.desvio \|\| 0\);/);
  assert.doesNotMatch(Q, /(lancarParado|idade|atrasado)\([^)]*agora\(\)\)/);   // datas do servidor nunca com o Date.now() cru
  assert.equal((Q.match(/lancarParado\([^)]*agoraSrv\(\)\)/g) || []).length, 3);
});
