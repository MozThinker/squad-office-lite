// static/ajuda.js (0.1.3: CA-B7.3, C11.1, C12.1, C14.1, D15.7, D15.8) e contraste/foco das secretárias (CA-A6).
// node --test tests/ajuda_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ROTULOS, rotuloEstado, rotuloDe, TIPOS, TAMANHOS, dicaErro, lancarParado, faixaRunner, primeirosPassos, projectoNovo, COMANDOS,
  desvioRelogio, COMANDOS_DOCKER, comandos, ondeCorre } from "../static/ajuda.js";

const HTML = readFileSync(new URL("../static/index.html", import.meta.url), "utf8");
const T0 = Date.parse("2026-11-08T19:30:00+00:00");
const RUN = { v: 1, em: "2026-11-08T19:30:00+00:00", versao: "0.1.3", claude: "ok", credencial: "ok", gh: "ok" };
const min = (n) => n * 60_000;

test("C14.1: rótulos só para os dois estados; o resto passa igual (sem cair no protótipo)", () => {
  assert.equal(rotuloEstado("parcial (PR draft)"), "PR aberto em rascunho");
  assert.equal(rotuloEstado("incompleta"), "Terminou sem PR");
  for (const s of ["concluída", "erro", "a correr", "parada?", "", null, undefined, "constructor", "__proto__", "toString"]) assert.equal(rotuloEstado(s), s, String(s));
  assert.deepEqual(Object.keys(ROTULOS), ["parcial (PR draft)", "incompleta"]);
});

test("C10.1: Tipo e Tamanho em português; valores fora da lista mostram-se crus", () => {
  assert.deepEqual([rotuloDe(TIPOS, "feat"), rotuloDe(TIPOS, "fix"), rotuloDe(TIPOS, "infra"), rotuloDe(TIPOS, "constructor")],
    ["funcionalidade nova", "correcção", "infra", "constructor"]);
  assert.deepEqual([rotuloDe(TAMANHOS, "task"), rotuloDe(TAMANHOS, "goal"), rotuloDe(TAMANHOS, "sprint")],
    ["tarefa curta (até 3 h)", "objectivo com plano (até 8 h)", "sprint"]);
});

test("B7.3: dica pelo prefixo do resultado ou pelo estado; outro → null", () => {
  assert.equal(dicaErro("erro", "credenciais do Claude inválidas: o token expirou"), "Corre `./install.sh --credenciais` (na pasta `~/squad-office-lite`).");
  assert.equal(dicaErro("incompleta", "saiu sem PR"), "Terminou sem PR: confirma `gh auth status`.");
  assert.equal(dicaErro("incompleta", null), "Terminou sem PR: confirma `gh auth status`.");
  assert.equal(dicaErro("erro", "runner.settings inválido: chave x"), "Corrige o ficheiro de `runner.settings` (README, «Runner»).");
  assert.equal(dicaErro("incompleta", "credenciais do Claude em falta"), "Corre `./install.sh --credenciais` (na pasta `~/squad-office-lite`).");   // credenciais primeiro
  for (const [e, r] of [["erro", "outra coisa"], ["erro", null], ["concluída", ""], [null, undefined], ["erro", " credenciais do Claude"], ["erro", { x: 1 }]])
    assert.equal(dicaErro(e, r), null, JSON.stringify([e, r]));
});

test("C12.1: «a lançar…» há mais de 3 min desde pedido_em (relógio injectado)", () => {
  const l = { estado: "a-lancar", pedido_em: "2026-11-08T20:30:00+01:00" };   // = T0
  assert.equal(lancarParado(l, T0 + min(3)), false);
  assert.equal(lancarParado(l, T0 + min(3) + 1), true);
  assert.equal(lancarParado({ ...l, estado: "ligado" }, T0 + min(60)), false);
  assert.equal(lancarParado({ ...l, estado: "erro" }, T0 + min(60)), false);
  assert.equal(lancarParado({ ...l, pedido_em: "lixo" }, T0 + min(60)), false);
  assert.equal(lancarParado(null, T0), false);
});

test("D15.8: faixa do runner no fuso da config", () => {
  assert.equal(faixaRunner(null, T0, "Europe/Lisbon"), "O runner ainda não deu sinal");
  assert.equal(faixaRunner(undefined, T0, "Europe/Lisbon"), "O runner ainda não deu sinal");
  assert.equal(faixaRunner(RUN, T0 + min(3), "Europe/Lisbon"), null);
  assert.equal(faixaRunner(RUN, T0 + min(3) + 1, "Europe/Lisbon"), "O runner não dá sinal desde 19:30");
  assert.equal(faixaRunner(RUN, T0 + min(10), "Africa/Maputo"), "O runner não dá sinal desde 21:30");
  assert.equal(faixaRunner(RUN, T0 + min(10), "Asia/Kolkata"), "O runner não dá sinal desde 01:00");
  assert.match(faixaRunner(RUN, T0 + min(10), "Fuso/Inexistente"), /^O runner não dá sinal desde \d\d:\d\d$/);   // fuso desconhecido: hora local
  assert.equal(faixaRunner(RUN, T0 - min(1), "UTC"), null);   // relógio do servidor adiantado: tem sinal
});

test("D15.7: primeiros passos com os comandos do doctor; desaparecem com a 1.ª tarefa entregue", () => {
  assert.equal(primeirosPassos({ tarefa_entregue: true, runner: null }, T0), null);
  assert.equal(primeirosPassos(null, T0), null);
  const tudo = primeirosPassos({ tarefa_entregue: false, runner: RUN }, T0);
  assert.deepEqual(tudo.map((p) => [p.chave, p.ok, p.comando]), [["runner", true, null], ["claude", true, null], ["credencial", true, null], ["gh", true, null]]);
  assert.deepEqual(tudo.map((p) => p.rotulo), ["Runner a dar sinal", "claude instalado", "Credencial do Claude", "Sessão do gh"]);
  const mau = primeirosPassos({ tarefa_entregue: false, runner: { ...RUN, claude: "falta", credencial: "falta", gh: "sem sessão" } }, T0);
  assert.deepEqual(mau.map((p) => [p.ok, p.comando]), [[true, null], [false, "curl -fsSL https://claude.ai/install.sh | bash"],
    [false, "cd ~/squad-office-lite && ./install.sh --credenciais"], [false, "gh auth login"]]);
  const modo = primeirosPassos({ runner: { ...RUN, credencial: "modo" } }, T0);   // sem tarefa_entregue = ainda não houve
  // permissões erradas: o ficheiro pode ser o claude.env ou o runner.env, noutra casa; o doctor diz o caminho real
  assert.deepEqual([modo[2].ok, modo[2].comando], [false, COMANDOS.doctor]);
  assert.equal(COMANDOS.doctor, "python3 ~/squad-office-lite/runner/office_runner.py doctor");
  assert.ok(!Object.values(COMANDOS).some((c) => c.includes("chmod")));
  // runner sem sinal (null ou > 3 min): tudo ✗, os três de baixo «desconhecido», com os comandos de omissão
  for (const runner of [null, { ...RUN, em: "2026-11-08T19:20:00+00:00", credencial: "modo" }]) {
    const p = primeirosPassos({ tarefa_entregue: false, runner }, T0);
    assert.deepEqual(p.map((x) => x.ok), [false, false, false, false]);
    assert.deepEqual(p.map((x) => x.comando), [COMANDOS.runner, COMANDOS.claude, COMANDOS.credencial, COMANDOS.gh]);
    assert.equal(COMANDOS.runner, "systemctl --user enable --now office-runner.timer");
    assert.deepEqual(p.slice(1).map((x) => x.rotulo), ["claude instalado (desconhecido)", "Credencial do Claude (desconhecido)", "Sessão do gh (desconhecido)"]);
  }
});

test("desvio do relógio: avisos pela hora do servidor, não pela do telemóvel", () => {
  const adiantado = T0 + min(10);   // telemóvel 10 min adiantado; o servidor diz T0
  const d = desvioRelogio("2026-11-08T19:30:00+00:00", adiantado);
  assert.equal(d, -min(10));
  assert.equal(desvioRelogio("2026-11-08T20:30:00+01:00", T0 - min(5)), min(5));   // atrasado; com fuso
  assert.equal(faixaRunner(RUN, adiantado, "UTC"), "O runner não dá sinal desde 19:30");   // sem correcção: aviso falso
  assert.equal(faixaRunner(RUN, adiantado + d, "UTC"), null);
  assert.equal(primeirosPassos({ runner: RUN }, adiantado + d)[0].ok, true);
  const l = { estado: "a-lancar", pedido_em: "2026-11-08T19:30:00+00:00" };
  assert.equal(lancarParado(l, adiantado), true);
  assert.equal(lancarParado(l, adiantado + d), false);
  assert.equal(lancarParado(l, adiantado + d + min(3) + 1), true);   // parado a sério continua a avisar
  for (const x of [undefined, null, "", "lixo"]) assert.equal(desvioRelogio(x, adiantado), 0);   // sem hora: relógio do browser
  // ligação: o index.html usa o «now» do /api/state medido à chegada
  assert.match(HTML, /stateEm = Date\.now\(\);/);
  assert.match(HTML, /const agoraS = Date\.now\(\) \+ AJ\.desvioRelogio\(state\.now, stateEm\);/);
  assert.match(HTML, /AJ\.faixaRunner\(state\.runner, agoraS, TZ\), ps = AJ\.primeirosPassos\(state, agoraS, cfg\.modo\)/);
});

test("C11.1: projecto do cartão novo", () => {
  const cfg = (projectos) => ({ lancar: { squads: ["dev"], projectos } });
  assert.equal(projectoNovo(["blog"], cfg(["loja"])), "blog");               // filtro de um projecto ganha
  assert.equal(projectoNovo([], cfg(["loja"])), "loja");                     // senão o único lançável
  assert.equal(projectoNovo(["a", "b"], cfg(["loja"])), "loja");
  assert.equal(projectoNovo([], cfg(["loja", "blog"])), "outro");
  assert.equal(projectoNovo(undefined, null), "outro");
  assert.equal(projectoNovo([], cfg([])), "outro");
});

// ---- CA-A6: língua, contraste do nome das secretárias não usadas e foco (WCAG 2.x, as mesmas fórmulas do test_temas_ui.py)
const lum = (h) => { const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };

test("CA-A6.1: <html lang=\"pt-PT\">", () => assert.match(HTML, /^<!doctype html>\n<html lang="pt-PT">/));

test("CA-A6.2: nome das secretárias não usadas ≥ 4,5:1 contra os dois ladrilhos do chão (o escritório é igual em todos os temas)", () => {
  assert.ok(Math.abs(ratio("#777777", "#ffffff") - 4.48) < 0.01);
  const [, usado, livre] = HTML.match(/fill="\$\{active \? "#ffffff" : used \? "(#[0-9a-f]{6})" : "(#[0-9a-f]{6})"\}"/);
  const chao = HTML.match(/fill="\$\{\(i \+ j\) % 2 \? "(#[0-9a-f]{6})" : "(#[0-9a-f]{6})"\}"/).slice(1);
  assert.deepEqual(chao, ["#2b2f3a", "#262a34"]);
  for (const bg of chao) {
    assert.ok(ratio(livre, bg) >= 4.5, `${livre} / ${bg} = ${ratio(livre, bg).toFixed(2)}`);   // #90a4ae: 5,16 e 5,54 (era #607d8b: 3,06)
    assert.ok(ratio(usado, bg) >= 4.5);
  }
});

test("CA-A6.3: foco das secretárias com 2 px de ecrã e cor ≥ 3:1 contra o chão em todos os temas", () => {
  assert.match(HTML, /#office \.seat:focus-visible > rect:first-of-type \{ stroke: var\(--well-focus\); stroke-width: 2px; vector-effect: non-scaling-stroke; \}/);
  const focos = [...HTML.matchAll(/--well-focus: (#[0-9a-f]{6})/g)].map((m) => m[1]);
  assert.equal(focos.length, 4);   // escuro, claro, sépia, contraste
  for (const f of focos) for (const bg of ["#2b2f3a", "#262a34", "#0d0f15"]) assert.ok(ratio(f, bg) >= 3, `${f} / ${bg} = ${ratio(f, bg).toFixed(2)}`);
});

// ---- escritório (index.html): rótulos, resultado escapado com dica, faixa e primeiros passos (B7.4, C14.1, D15.7)
test("C14.1/B7.4: o painel da tarefa mostra o rótulo e o resultado escapado com a dica", async () => {
  const { default: vm } = await import("node:vm");
  const fn = (n) => { const i = HTML.indexOf(`\nfunction ${n}(`); assert.ok(i >= 0, n); return HTML.slice(i, HTML.indexOf("\n}\n", i) + 3); };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const AJ = await import("../static/ajuda.js");
  const { erroHtml } = vm.runInNewContext(fn("erroHtml") + "; ({ erroHtml })", { AJ, esc, md: (s) => `<p>${esc(s)}</p>`, cfg: {} });   // cfg sem «modo»: vps
  const h = erroHtml({ status: "incompleta", resultado: "sem PR <script>x</script>" });
  assert.match(h, /^<div class="issues">sem PR &lt;script&gt;x&lt;\/script&gt;<\/div><div class="md"><p>Terminou sem PR: confirma `gh auth status`\.<\/p><\/div>$/);
  assert.equal(erroHtml({ status: "concluída", resultado: "tudo bem" }), "");   // concluída: a entrega diz o resto
  assert.equal(erroHtml({ status: "erro", resultado: null }), "");
  assert.match(HTML, /<span class="badge \$\{statusClass\(t\)\}">\$\{esc\(rot\(t\.status\)\)\}<\/span>/);
  assert.match(HTML, /const st = task \? rot\(task\.status\) : "sem trabalho"/);
  assert.match(HTML, /"terminou: " \+ rot\(t\.status\)/);
  assert.match(HTML, /import\("\.\/ajuda\.js"\)\.then\(\(m\) => \{ AJ = m; paint\(false\); \}/);
  assert.match(HTML, /<p id="runner-f" role="status" hidden><\/p>/);
  assert.match(HTML, /#aviso, #runner-f \{/);   // mesmo padrão visual da faixa de config inválida
  assert.match(HTML, /<a href="#quadro\/novo">Criar o primeiro cartão<\/a>/);
  assert.match(HTML, /\$\{esc\(p\.rotulo\)\}\$\{p\.comando \? `<code>\$\{esc\(p\.comando\)\}<\/code>` : ""\}/);
});

// ---- 0.1.4 modo Docker (AC-o1): GET api/config traz «modo»; ausente ou desconhecido ⇒ vps
test("AC-o1: comandos e «onde» por modo; omissão vps", () => {
  assert.deepEqual(COMANDOS_DOCKER, { runner: "docker compose up -d", claude: "docker compose pull; docker compose up -d",
    credencial: "docker compose run --rm configurar --credenciais", gh: "docker compose run --rm configurar --gh", doctor: "docker compose exec runner doctor" });
  assert.deepEqual(Object.keys(COMANDOS_DOCKER), Object.keys(COMANDOS));
  assert.equal(comandos("docker"), COMANDOS_DOCKER);
  for (const m of ["vps", undefined, null, "", "Docker", "constructor"]) { assert.equal(comandos(m), COMANDOS, String(m)); assert.equal(ondeCorre(m), "no servidor"); }
  assert.equal(ondeCorre("docker"), "no PC");
});

test("AC-o1: primeiros passos no modo docker mostram os comandos docker compose", () => {
  const mau = primeirosPassos({ runner: { ...RUN, claude: "falta", credencial: "falta", gh: "sem sessão" } }, T0, "docker");
  assert.deepEqual(mau.map((p) => [p.ok, p.comando]), [[true, null], [false, COMANDOS_DOCKER.claude], [false, COMANDOS_DOCKER.credencial], [false, COMANDOS_DOCKER.gh]]);
  assert.equal(primeirosPassos({ runner: { ...RUN, credencial: "modo" } }, T0, "docker")[2].comando, "docker compose exec runner doctor");
  const sem = primeirosPassos({ runner: null }, T0, "docker");
  assert.deepEqual(sem.map((x) => x.comando), ["docker compose up -d", "docker compose pull; docker compose up -d",
    "docker compose run --rm configurar --credenciais", "docker compose run --rm configurar --gh"]);
  assert.deepEqual(primeirosPassos({ runner: RUN }, T0, "docker").map((p) => p.comando), [null, null, null, null]);
  assert.equal(primeirosPassos({ tarefa_entregue: true, runner: null }, T0, "docker"), null);
  // modo explícito vps = omissão
  assert.deepEqual(primeirosPassos({ runner: null }, T0, "vps"), primeirosPassos({ runner: null }, T0));
});

test("AC-o1: dicas de erro no modo docker; vps igual à omissão", () => {
  assert.equal(dicaErro("erro", "credenciais do Claude inválidas", "docker"),
    "Corre `docker compose run --rm configurar --credenciais` no PC (na pasta do `compose.yaml`).");
  assert.equal(dicaErro("incompleta", null, "docker"), "Terminou sem PR: confirma `docker compose exec runner gh auth status`.");
  assert.equal(dicaErro("erro", "runner.settings inválido: x", "docker"), dicaErro("erro", "runner.settings inválido: x"));
  assert.equal(dicaErro("erro", "outra coisa", "docker"), null);
  for (const [e, r] of [["erro", "credenciais do Claude em falta"], ["incompleta", "x"]]) assert.equal(dicaErro(e, r, "vps"), dicaErro(e, r));
  assert.ok(!dicaErro("erro", "credenciais do Claude x", "docker").includes("install.sh"));
});

test("AC-o1: faixa «runner sem sinal» e primeiros passos no escritório, nos dois modos", async () => {
  const { default: vm } = await import("node:vm");
  const fn = (n) => { const i = HTML.indexOf(`\nfunction ${n}(`); assert.ok(i >= 0, n); return HTML.slice(i, HTML.indexOf("\n}\n", i) + 3); };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const AJ = await import("../static/ajuda.js");
  const pinta = (cfg) => {
    const el = { "#runner-f": { textContent: "", hidden: true }, "#passos": { hidden: true, html: "" } };
    const ctx = { AJ, esc, cfg, TZ: "UTC", stateEm: T0 + min(10), Date: { now: () => T0 + min(10) },
      state: { now: "2026-11-08T19:40:00+00:00", tarefa_entregue: false, runner: null },
      $: (s) => el[s], setHtml: (e, h) => { e.html = h; } };
    vm.runInNewContext(fn("renderAjuda") + "; renderAjuda()", ctx);
    return el;
  };
  const vps = pinta({ marca: "x" });   // sem «modo» (servidor antigo): vps
  assert.equal(vps["#runner-f"].textContent, "O runner ainda não deu sinal. Corre o diagnóstico no servidor: " + COMANDOS.doctor);
  assert.match(vps["#passos"].html, /<code>systemctl --user enable --now office-runner\.timer<\/code>/);
  const dk = pinta({ marca: "x", modo: "docker" });
  assert.equal(dk["#runner-f"].hidden, false);
  assert.equal(dk["#runner-f"].textContent, "O runner ainda não deu sinal. Corre o diagnóstico no PC: docker compose exec runner doctor");
  for (const c of Object.values(COMANDOS_DOCKER).slice(0, 4)) assert.ok(dk["#passos"].html.includes(`<code>${esc(c)}</code>`), c);
  assert.doesNotMatch(dk["#passos"].html, /systemctl|install\.sh|squad-office-lite/);
  assert.match(HTML, /AJ\?\.dicaErro\(t\.status, t\.resultado, cfg\.modo\)/);
});
