// Funções puras de static/agentes.js e agentesDemo de static/demo.js (node --test tests/agentes_test.mjs; CA-9, 38, 39).
// O import em si prova que o módulo não toca em window nem document ao carregar.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ACCOES, MODELOS, ROTINA_ROT, MSG, maisCaro, efectivo, textoOk, corpoPedido, alvo, ultimoPedido, rotuloPedido, rotuloRotina, podeActivar,
  podeRotina, rotinasVivas, sessaoTexto, msgErro, validarAgenteNovo, validarMd, validarEnsaio, validarSquadNova, validarRotina, blocoYaml, diasTexto }
  from "../static/agentes.js";
import { agentesDemo } from "../static/demo.js";

const SRC = readFileSync(new URL("../static/agentes.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../static/index.html", import.meta.url), "utf8");
const DEF = readFileSync(new URL("../static/definicoes.js", import.meta.url), "utf8");
const X = (n, c = "a") => c.repeat(n);
const S = { id: "dev", nome: "Dev", formato: "worktree", agentes: [{ nome: "revisor" }], candidatos: [{ nome: "novo" }], rotinas: [] };
const ROT = { projecto: "site", hora: "09:30", dias: ["3", "1", "1"], instrucoes: "Rever os PRs abertos.", tecto_usd: "0.50", max_turnos: "10" };

test("ACCOES: as 16 acções do contrato v1 com as chaves exactas (PLAN §3.2)", () => {
  assert.equal(Object.keys(ACCOES).length, 16);
  assert.deepEqual(ACCOES.modelo, ["squad", "agente", "sha256", "modelo"]);
  assert.deepEqual(ACCOES.agente_criar, ["squad", "nome", "sha256", "md"]);
  assert.deepEqual(ACCOES.rotina_pedir, ["squad", "projecto", "hora", "dias", "instrucoes", "tecto_usd", "max_turnos"]);
  assert.deepEqual(MODELOS, ["inherit", "sonnet", "opus", "haiku"]);
});

test("corpoPedido: {token, accao, args} com só as chaves da acção, por ordem", () => {
  const c = corpoPedido("t", "modelo", { modelo: "", agente: "x", squad: "dev", sha256: "a".repeat(64), lixo: 1 });
  assert.deepEqual(Object.keys(c), ["token", "accao", "args"]);
  assert.deepEqual(Object.keys(c.args), ["squad", "agente", "sha256", "modelo"]);
  assert.equal(c.args.modelo, "");
  assert.equal(corpoPedido(undefined, "squad_descartar", { squad: "dev" }).token, null);
  assert.deepEqual(JSON.parse(JSON.stringify(corpoPedido("t", "agente_criar", { squad: "d", nome: "n", sha256: null, md: "---" }))).args.sha256, null);
});

test("alvo: um pendente por alvo (= agentes.alvo do servidor)", () => {
  assert.equal(alvo("modelo", { squad: "dev", agente: "rev" }), "agente:dev/rev");
  assert.equal(alvo("skills", { squad: "dev", agente: "rev" }), "agente:dev/rev");
  assert.equal(alvo("agente_remover", { squad: "dev", agente: "rev" }), "agente:dev/rev");
  for (const a of ["agente_rascunho", "agente_criar", "agente_ensaio", "agente_activar", "agente_descartar"]) assert.equal(alvo(a, { squad: "dev", nome: "n" }), "cand:dev/n");
  for (const a of ["squad_rascunho", "squad_criar", "squad_ensaio", "squad_descartar", "squad_desactivar"]) assert.equal(alvo(a, { squad: "obs" }), "squad:obs");
  assert.equal(alvo("rotina_pedir", { squad: "dev" }), "rotina:dev");
  assert.equal(alvo("rotina_retomar", { squad: "dev", rotina: "r2" }), "rotina:dev/r2");
  assert.equal(alvo("rotina_cancelar", { squad: "dev", rotina: "r2" }), "rotina:dev/r2");
});

test("maisCaro: haiku < sonnet < opus; desconhecido, inherit, ausente ou igual → sem aviso", () => {
  assert.equal(maisCaro("haiku", "sonnet"), true);
  assert.equal(maisCaro("sonnet", "opus"), true);
  assert.equal(maisCaro("haiku", "opus"), true);
  assert.equal(maisCaro("opus", "haiku"), false);
  assert.equal(maisCaro("sonnet", "sonnet"), false);
  for (const [de, para] of [[null, "opus"], ["haiku", "inherit"], ["inherit", "opus"], ["haiku", "claude-x"], ["x", "opus"], ["haiku", ""]]) assert.equal(maisCaro(de, para), false, `${de}→${para}`);
  // sem campo/inherit = o modelo da sessão (para comparar)
  assert.equal(efectivo("", "haiku"), "haiku");
  assert.equal(efectivo("inherit", "sonnet"), "sonnet");
  assert.equal(efectivo("opus", "haiku"), "opus");
  assert.equal(efectivo(null, null), null);
  assert.equal(maisCaro(efectivo(null, "haiku"), efectivo("opus", "haiku")), true);
});

test("textoOk: limites em caracteres, linhas, controlos e invisíveis (ZWJ aceite)", () => {
  assert.equal(textoOk("a", 1, 2000, true), true);
  assert.equal(textoOk("", 1, 2000, true), false);
  assert.equal(textoOk("   ", 1, 2000, true), false);
  assert.equal(textoOk(X(2000), 1, 2000, true), true);
  assert.equal(textoOk(X(2001), 1, 2000, true), false);
  assert.equal(textoOk(X(2000, "é"), 1, 2000, true), true);
  assert.equal(textoOk("😀".repeat(2000), 1, 2000, true), true); // pontos de código, não UTF-16
  assert.equal(textoOk("a\nb\tc", 1, 99, true), true);
  assert.equal(textoOk("a\nb", 1, 99, false), false);
  assert.equal(textoOk("a\rb", 1, 99, true), false);
  assert.equal(textoOk("a\u0000b", 1, 99, true), false);
  assert.equal(textoOk("a​b", 1, 99, true), false); // Cf
  assert.equal(textoOk("a‮b", 1, 99, true), false); // Cf (bidi)
  assert.equal(textoOk("ab", 1, 99, true), false); // Co
  assert.equal(textoOk("👩\u200d💻", 1, 99, true), true); // ZWJ
  assert.equal(textoOk(12, 1, 99, true), false);
});

test("validarAgenteNovo: nome [a-z][a-z0-9-]{1,39}, único na squad, descrição 1–2000", () => {
  assert.deepEqual(validarAgenteNovo({ nome: " ana ", descricao: "faz x" }, S).args, { squad: "dev", nome: "ana", descricao: "faz x" });
  for (const n of ["a", "1ab", "Ab", "a_b", X(41), ""]) assert.ok(validarAgenteNovo({ nome: n, descricao: "x" }, S).erro, n);
  assert.ok(validarAgenteNovo({ nome: "a" + X(39, "b"), descricao: "x" }, S).args);
  assert.match(validarAgenteNovo({ nome: "revisor", descricao: "x" }, S).erro, /Já existe/);
  assert.match(validarAgenteNovo({ nome: "novo", descricao: "x" }, S).erro, /Já existe/);
  assert.ok(validarAgenteNovo({ nome: "ana", descricao: X(2001) }, S).erro);
  assert.ok(validarAgenteNovo({ nome: "ana", descricao: "" }, S).erro);
});

test("validarMd: começa pelo frontmatter e ≤ 64 KB (bytes)", () => {
  assert.deepEqual(validarMd("---\nname: x\n---\n"), {});
  assert.ok(validarMd("name: x").erro);
  assert.ok(validarMd(null).erro);
  assert.deepEqual(validarMd("---" + X(65536 - 3)), {});
  assert.ok(validarMd("---" + X(65536 - 2)).erro);
  assert.ok(validarMd("---" + X(40000, "é")).erro); // 40 003 caracteres mas 80 003 bytes
});

test("validarEnsaio: projecto da lista e tarefa 1–2000", () => {
  assert.deepEqual(validarEnsaio({ projecto: "site", tarefa: "lê o README" }, ["site"]).args, { projecto: "site", tarefa: "lê o README" });
  assert.ok(validarEnsaio({ projecto: "outro", tarefa: "x" }, ["site"]).erro);
  assert.ok(validarEnsaio({ projecto: "site", tarefa: "" }, ["site"]).erro);
  assert.ok(validarEnsaio({ projecto: "site", tarefa: X(2001) }, ["site"]).erro);
});

test("validarSquadNova: modelo existente, id [a-z][a-z0-9-]{1,19}, squad sem agentes, missão 1–2000", () => {
  const D = { modelos: [{ id: "observacao" }], squads: [S, { id: "vazia", agentes: [] }] };
  assert.deepEqual(validarSquadNova({ modelo: "observacao", squad: "obs", missao: "vigiar" }, D).args, { squad: "obs", modelo: "observacao", missao: "vigiar" });
  assert.ok(validarSquadNova({ modelo: "vazia", squad: "vazia", missao: "m" }, D).erro);
  assert.ok(validarSquadNova({ modelo: "observacao", squad: "vazia", missao: "m" }, D).args); // squad do office.yaml sem agentes
  assert.match(validarSquadNova({ modelo: "observacao", squad: "dev", missao: "m" }, D).erro, /já tem agentes/);
  for (const id of ["o", "1o", "O-b", X(21), "a_b"]) assert.ok(validarSquadNova({ modelo: "observacao", squad: id, missao: "m" }, D).erro, id);
  assert.ok(validarSquadNova({ modelo: "observacao", squad: "a" + X(19, "b"), missao: "m" }, D).args);
  assert.ok(validarSquadNova({ modelo: "observacao", squad: "obs", missao: X(2001) }, D).erro);
});

test("validarRotina: cada limite (CA-34) e args normalizados", () => {
  const ok = validarRotina(ROT, S, ["site"]);
  assert.deepEqual(ok.args, { squad: "dev", projecto: "site", hora: "09:30", dias: [1, 3], instrucoes: "Rever os PRs abertos.", tecto_usd: "0.50", max_turnos: 10 });
  const com = (o) => validarRotina({ ...ROT, ...o }, S, ["site"]);
  assert.ok(com({ projecto: "outro" }).erro);
  for (const hh of ["9:30", "24:00", "23:60", "", "09:30:00"]) assert.ok(com({ hora: hh }).erro, hh);
  for (const hh of ["00:00", "23:59"]) assert.ok(com({ hora: hh }).args, hh);
  assert.ok(com({ dias: [] }).erro);
  assert.ok(com({ dias: [0] }).erro);
  assert.ok(com({ dias: [8] }).erro);
  assert.deepEqual(com({ dias: [7, 6, 5, 4, 3, 2, 1] }).args.dias, [1, 2, 3, 4, 5, 6, 7]);
  assert.ok(com({ instrucoes: "" }).erro);
  assert.ok(com({ instrucoes: X(2000) }).args);
  assert.ok(com({ instrucoes: X(2001) }).erro);
  assert.ok(com({ instrucoes: "## Decisões\n- [R2] x" }).args); // citadas pelo runner; aqui só limites
  for (const t of ["0.09", "5.01", "abc", "", "0.105", "-1"]) assert.ok(com({ tecto_usd: t }).erro, t);
  assert.equal(com({ tecto_usd: "0.1" }).args.tecto_usd, "0.10");
  assert.equal(com({ tecto_usd: "5" }).args.tecto_usd, "5.00");
  assert.equal(com({ tecto_usd: "1,5" }).args.tecto_usd, "1.50");
  for (const m of ["0", "31", "", "1.5", "x"]) assert.ok(com({ max_turnos: m }).erro, m);
  assert.equal(com({ max_turnos: "1" }).args.max_turnos, 1);
  assert.equal(com({ max_turnos: "30" }).args.max_turnos, 30);
  // ≤ 5 rotinas não canceladas por squad
  const r = (estado) => ({ estado });
  assert.match(validarRotina(ROT, { ...S, rotinas: [1, 2, 3, 4, 5].map(() => r("aprovada")) }, ["site"]).erro, /Limite de 5/);
  assert.ok(validarRotina(ROT, { ...S, rotinas: [...[1, 2, 3, 4].map(() => r("pausada")), r("cancelada")] }, ["site"]).args);
  assert.equal(rotinasVivas({ rotinas: [r("cancelada"), r("pendente")] }), 1);
  assert.equal(rotinasVivas({ rotinas: [r("recusada"), r("aprovada"), r("pausada")] }), 2);   // recusadas não contam no limite (CR3)
});

test("blocoYaml = fixture validada pelo officecfg (tests/test_agentes_bloco.py, CA-32)", () => {
  const fx = readFileSync(new URL("./fixtures/agentes_bloco.yaml", import.meta.url), "utf8");
  assert.equal(blocoYaml({ id: "observa", nome: 'Obs "A" \\ B', lead: "plantao" }), fx);
  assert.equal(blocoYaml({ id: "obs", nome: "Observação", lead: "plantao" }), '  - id: obs\n    nome: "Observação"\n    lead: plantao\n');
});

test("rótulos de estado: pedidos, rotinas e sessão", () => {
  assert.equal(rotuloPedido({ accao: "modelo", estado: "pendente" }), "mudar o modelo: pendente");
  assert.equal(rotuloPedido({ accao: "skills", estado: "ok" }), "mudar as skills: feito");
  assert.equal(rotuloPedido({ accao: "agente_activar", estado: "erro", erro: "sem ensaio ok desta versão" }), "activar o agente: erro (sem ensaio ok desta versão)");
  assert.equal(rotuloPedido({ accao: "nova", estado: "x" }), "nova: ?");
  assert.equal(rotuloRotina({ estado: "pendente" }), "à espera de aprovação");
  assert.equal(rotuloRotina({ estado: "aprovada" }), "activa");
  assert.match(rotuloRotina({ estado: "pausada" }), /^pausada/);
  for (const e of Object.keys(ROTINA_ROT)) assert.ok(rotuloRotina({ estado: e }));
  assert.equal(sessaoTexto({ modelo: "sonnet", fonte: "squad" }), "sonnet (squads[].modelo)");
  assert.equal(sessaoTexto({ modelo: "haiku", fonte: "runner" }), "haiku (runner.modelo)");
  assert.equal(sessaoTexto({ modelo: null, fonte: "omissao" }), "omissão do claude");
  assert.equal(diasTexto([1, 3, 7]), "seg, qua, dom");
});

test("ultimoPedido: o mais recente do alvo pelo id", () => {
  const ps = [{ id: "20261118T100000-00000001", alvo: "agente:dev/a", estado: "ok" }, { id: "20261118T110000-00000002", alvo: "agente:dev/a", estado: "pendente" },
    { id: "20261118T120000-00000003", alvo: "agente:dev/b", estado: "erro" }];
  assert.equal(ultimoPedido(ps, "agente:dev/a").estado, "pendente");
  assert.equal(ultimoPedido(ps, "agente:dev/c"), null);
  assert.equal(ultimoPedido(null, "x"), null);
});

test("podeActivar: só com ensaio ok do mesmo sha256 (CA-26, 29)", () => {
  const sha = "a".repeat(64);
  assert.equal(podeActivar({ sha256: sha, ensaio: { estado: "ok", sha256: sha } }), true);
  assert.equal(podeActivar({ sha256: sha, ensaio: { estado: "erro", sha256: sha } }), false);
  assert.equal(podeActivar({ sha256: sha, ensaio: { estado: "ok", sha256: "b".repeat(64) } }), false);
  assert.equal(podeActivar({ sha256: sha, ensaio: null }), false);
});

test("podeRotina: squad do office.yaml, worktree e com o modo task", () => {
  const cfg = { squads: [{ id: "dev", formato: "worktree", modos: ["goal", "task"] }, { id: "so", formato: "worktree", modos: ["goal"] },
    { id: "notas", formato: "sessao", modos: [] }] };
  assert.equal(podeRotina({ id: "dev" }, cfg), true);
  assert.equal(podeRotina({ id: "so" }, cfg), false);
  assert.equal(podeRotina({ id: "notas" }, cfg), false);
  assert.equal(podeRotina({ id: "registo", formato: "worktree" }, cfg), false);
});

test("msgErro: 503 da fila → configurar (CA-38); 503 demo; 403; rede; error do servidor", () => {
  assert.equal(msgErro(503, { error: "fila dos agentes indisponível" }), MSG.fila);
  assert.match(MSG.fila, /Corre o `configurar` uma vez|corre o `configurar` uma vez/);
  assert.equal(msgErro(503, { error: "x", motivo: "demo" }), MSG.demo);
  assert.equal(msgErro(403, null), MSG.token);
  assert.equal(msgErro(0, null), MSG.rede);
  assert.equal(msgErro(409, { error: "já há um pedido pendente" }), "já há um pedido pendente");
  assert.equal(msgErro(400, null), "erro 400");
  assert.equal(msgErro(400, { error: X(300) }).length, 200);
});

test("agentesDemo: só as chaves do GET /api/agentes (PLAN §3.5), squads da config, 2 agentes, sem candidatos", () => {
  const cfg = { squads: [{ id: "dev", nome: "Dev", lead: "tech-lead", formato: "worktree" }, { id: "notas", nome: "Notas", lead: "revisor", formato: "sessao" }],
    projectos: [{ id: "site", nome: "Site" }] };
  const d = agentesDemo(cfg);
  assert.deepEqual(Object.keys(d), ["v", "escrita", "modelos", "projectos", "squads", "pedidos"]);
  assert.equal(d.v, 1);
  assert.deepEqual(d.projectos, ["site"]);
  assert.deepEqual(d.pedidos, []);
  for (const m of d.modelos) assert.deepEqual(Object.keys(m).sort(), ["agentes", "id", "lead", "nome", "resumo"]);
  assert.deepEqual(d.squads.map((s) => s.id), ["dev", "notas"]);
  const SQK = ["agentes", "candidatos", "ensaio_lead", "formato", "id", "instrucoes", "lead", "modelo_squad", "nome", "origem", "rascunho", "rotinas", "sessao", "skills"];
  const AGK = ["description", "ficheiro", "lead", "model", "nome", "permissionMode", "sha256", "skills", "tools"];
  for (const s of d.squads) {
    assert.deepEqual(Object.keys(s).sort(), SQK);
    assert.deepEqual(Object.keys(s.sessao).sort(), ["fonte", "modelo"]);
    assert.equal(s.agentes.length, 2);
    assert.deepEqual(s.candidatos, []);
    assert.equal(new Set(s.agentes.map((a) => a.nome)).size, 2);
    assert.equal(s.agentes.filter((a) => a.lead).length, 1);
    assert.equal(s.agentes.find((a) => a.lead).nome, s.lead);
    for (const a of s.agentes) {
      assert.deepEqual(Object.keys(a).sort(), AGK);
      assert.match(a.sha256, /^[0-9a-f]{64}$/);
      assert.ok(a.skills.every((k) => s.skills.some((x) => x.nome === k)));
    }
    for (const k of s.skills) assert.deepEqual(Object.keys(k).sort(), ["description", "ficheiro", "nome"]);
  }
  assert.equal(agentesDemo({}).squads.length, 1); // sem config: a squad de exemplo
});

test("CA-9, 38: agentes.js só por import() a partir do ⚙; nada de innerHTML; alvos de 44 px", () => {
  assert.doesNotMatch(HTML, /<(script|link)[^>]+agentes\.(js|css)/);
  assert.match(HTML, /import\("\.\/agentes\.js"\)/);
  assert.match(DEF, /data-a="agentes"/);
  assert.match(DEF, /Squads e agentes/);
  assert.doesNotMatch(SRC, /innerHTML|insertAdjacentHTML|outerHTML/);
  assert.doesNotMatch(SRC, /^import /m); // sem dependências
  const CSS = readFileSync(new URL("../static/agentes.css", import.meta.url), "utf8");
  assert.match(CSS, /\.ag-btn \{[^}]*min-height: 44px/);
  assert.match(CSS, /\.ag-campo :is\(input, select, textarea\) \{[^}]*min-height: 44px/);
  assert.match(CSS, /\.ag-c \{[^}]*min-height: 44px/);
  assert.match(CSS, /\.ag-sq summary \{[^}]*min-height: 44px/);
});
