// Verificação de ponta a ponta e capturas do «Lançar» no Quadro (SPEC office-quadro-lancar, AC1–13, 19–24).
// Sobe o server.py REAL em 127.0.0.1 com pastas temporárias (TASKS_DIR, CLAUDE_PROJECTS_DIR, QUADRO_DIR, QUADRO_LANCAR_DIR…
// em os.tmpdir(); nunca as pastas reais). Nada é interceptado no browser: o /api/quadro e o /api/quadro/lancar são os do
// servidor. O «script do host» é este ficheiro: escreve à mão <id>.feito.json e uma meta.json de tarefa falsa.
//
// Como correr (Playwright fica fora do repo, como em tools/capturas_temas.mjs):
//   npm i --prefix /tmp/pw-music playwright@1.64.1
//   source ~/.local/pwlibs/env.sh
//   PLAYWRIGHT_DIR=/tmp/pw-music node tools/capturas_quadro_lancar.mjs [--sem-capturas]
// Capturas em docs/capturas/: quadro-lancar-360-<tema>.png (5 temas), quadro-lancar-1440.png, quadro-lancar-estados-360.png.
// Config (office.yaml) gerada na pasta temporária: squads dev (feat/fix, goal/task) e ops (tarefa, task), ambas worktree;
// projectos office, ebook, loja e campanhas. O pedido leva o id do projecto.
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync, existsSync, utimesSync, statSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PW = process.env.PLAYWRIGHT_DIR || "/tmp/pw-music";
let chromium;
try { ({ chromium } = createRequire(path.join(PW, "x.js"))("playwright")); } catch {
  console.error(`Playwright não encontrado em ${PW}. Instala com: npm i --prefix ${PW} playwright@1.64.1`);
  process.exit(2);
}
const CAPTURAS = !process.argv.includes("--sem-capturas");
const OUT = path.join(ROOT, "docs/capturas");
const TEMAS = ["auto", "escuro", "claro", "sepia", "contraste"];
const PEDIDO = /^\d{8}T\d{6}-[0-9a-f]{8}\.json$/;
const CHAVES = "v,id,cartao_id,squad,projecto,kind,mode,slug,titulo,brief,pedido_em";
let falhas = 0;
const ok = (c, m) => { console.log((c ? "ok    " : "FALHA ") + m); if (!c) falhas++; return c; };
const espera = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- dados falsos ----
const p2 = (n) => String(n).padStart(2, "0");
const local = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}T${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`; };
const isoTz = (ms) => { const o = -new Date(ms).getTimezoneOffset(); return `${local(ms)}${o >= 0 ? "+" : "-"}${p2(Math.floor(Math.abs(o) / 60))}:${p2(Math.abs(o) % 60)}`; };
const stamp = (ms) => local(ms).replace(/[-:]/g, "");
const NOW = Date.now();
const cartao = (id, coluna, ordem, titulo, projecto = "office", descricao = "") => ({ id, titulo, descricao, projecto, coluna, ordem, etiquetas: [],
  criado: isoTz(NOW - 86400e3), actualizado: isoTz(NOW - 3600e3) });
const json = (f, d) => { mkdirSync(path.dirname(f), { recursive: true }); writeFileSync(f, JSON.stringify(d)); };
const meta = (tmp, id, m) => json(path.join(tmp, "tasks", id, "meta.json"), { id, mode: "goal", squad: "dev", project: "office", repo: "exemplo/office",
  branch: `feat/${id}`, base: "main", task: `# ${m.titulo || id}\n`, ...m });

function dados(cartoes) {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "qa-quadro-lancar-"));
  for (const d of ["tasks", "claude", "squads", "aprov", "bib", "quadro", "quadro-lancar", "fila", "push"]) mkdirSync(path.join(tmp, d), { recursive: true });
  writeFileSync(path.join(tmp, "office.yaml"), ["versao: 1", "marca: Squad Office", "utilizador:", "  nome: Ana", "fuso: Europe/Lisbon", "squads:",
    "  - id: dev", "    nome: Dev", "    lead: tech-lead", "    tipos: [feat, fix]", "    modos: [goal, task]",
    "  - id: ops", "    nome: Ops", "    lead: tech-lead", "    tipos: [tarefa]", "    modos: [task]", "projectos:",
    ...[["office", "Office"], ["ebook", "E-book"], ["loja", "Loja"], ["campanhas", "Campanhas"]].flatMap(([i, n]) => [`  - id: ${i}`, `    nome: ${n}`]), ""].join("\n"));
  json(path.join(tmp, "quadro", "cartoes.json"), { v: 1, cartoes });
  // 5 Dev goal concluídas (1 h cada) = amostra para a barra de progresso ter estimativa
  for (let i = 1; i <= 5; i++) meta(tmp, `20261101-0${i}00-amostra-${i}`, { status: "concluída", started_at: local(NOW - (i + 1) * 86400e3),
    finished_at: local(NOW - (i + 1) * 86400e3 + 3600e3), pr: `https://github.com/exemplo/office/pull/${i} (merged)` });
  return tmp;
}

async function subir(tmp) {
  const port = await new Promise((r) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => r(p)); }); });
  const env = { ...process.env, OFFICE_CONFIG: path.join(tmp, "office.yaml"), TASKS_DIR: path.join(tmp, "tasks"), CLAUDE_PROJECTS_DIR: path.join(tmp, "claude"),
    SQUADS_DIR: path.join(tmp, "squads"), APROVACOES_DIR: path.join(tmp, "aprov"), BIBLIOTECA_DIR: path.join(tmp, "bib"),
    DECISOES_FILA_DIR: path.join(tmp, "fila"), QUADRO_DIR: path.join(tmp, "quadro"), QUADRO_LANCAR_DIR: path.join(tmp, "quadro-lancar"),
    PUSH_DIR: path.join(tmp, "push"), PORT: String(port) };
  const srv = spawn("python3", ["-c", `import server; from http.server import ThreadingHTTPServer; ThreadingHTTPServer(("127.0.0.1", ${port}), server.Handler).serve_forever()`],
    { cwd: ROOT, env, stdio: "ignore" });
  const base = `http://127.0.0.1:${port}/`;
  for (let i = 0; ; i++) {
    try { if ((await fetch(base + "healthz")).ok) break; } catch {}
    if (i > 80) throw new Error("server.py não respondeu em /healthz");
    await espera(100);
  }
  return { base, srv };
}

const fila = (tmp) => readdirSync(path.join(tmp, "quadro-lancar")).sort();
const disco = (tmp) => Object.fromEntries(JSON.parse(readFileSync(path.join(tmp, "quadro", "cartoes.json"), "utf8")).cartoes.map((c) => [c.id, c]));
// tudo o que existe na árvore temporária (para provar que nada é escrito fora de quadro-lancar/ e quadro/)
const arvore = (d, pre = "") => readdirSync(d, { withFileTypes: true }).flatMap((e) => e.isDirectory() ? arvore(path.join(d, e.name), pre + e.name + "/") : [pre + e.name]);

async function pagina(browser, base, { w = 1440, h = 900, tema = "escuro", touch = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: touch, isMobile: false, deviceScaleFactor: 1,
    colorScheme: tema === "auto" ? "light" : "dark", reducedMotion: "reduce" });
  await ctx.addInitScript((t) => { if (!sessionStorage.getItem("__i")) { sessionStorage.setItem("__i", "1"); localStorage.setItem("tema", t); } }, tema);
  const page = await ctx.newPage(), posts = [], errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errs.push(m.text()));
  page.on("request", (r) => r.method() === "POST" && posts.push({ path: new URL(r.url()).pathname, b: JSON.parse(r.postData() || "null") }));
  await page.goto(base + "#quadro");
  await page.waitForSelector("#quadro .q-board .q-card[data-id]");
  return { ctx, page, posts, errs };
}

const sel = (id) => `.q-board .q-card[data-id="${id}"]`;
const col = (page, id) => page.evaluate((s) => document.querySelector(s)?.closest(".q-col")?.dataset.col ?? null, sel(id));
const ordem = (page, c) => page.evaluate((c) => [...document.querySelectorAll(`#q-col-${c} .q-card[data-kind="manual"]`)].map((e) => e.dataset.id), c);
const drLancar = (page) => page.evaluate(() => !document.querySelector(".q-dr").hidden && !!document.querySelector(".q-dr .q-lf"));
const foco = (page) => page.evaluate(() => document.activeElement?.id || document.activeElement?.dataset?.id || document.activeElement?.tagName);
const fechado = (page) => page.waitForFunction(() => !document.querySelector(".q-dr .q-lf"), null, { timeout: 3000 }).then(() => true, () => false);
async function viaMenu(page, id, coluna = "em-curso") {
  await page.locator(sel(id)).hover();
  await page.locator(`${sel(id)} .q-more`).click();
  await page.locator(`.q-menu [data-col="${coluna}"]`).click();
  await page.waitForTimeout(300);
}
async function arrastaRato(page, id, coluna) {
  const s = await page.locator(sel(id)).boundingBox(), d = await page.locator(`#q-col-${coluna} .q-list`).boundingBox();
  await page.mouse.move(s.x + 40, s.y + 15); await page.mouse.down(); await page.mouse.move(s.x + 60, s.y + 30, { steps: 3 });
  await page.mouse.move(d.x + 40, d.y + 10, { steps: 12 }); await page.mouse.up();
  await page.waitForTimeout(400);
}
// toque: pressão longa de 400 ms, arrasta para a aba da coluna, larga (eventos de toque reais pelo CDP)
async function arrastaToque(page, id, coluna) {
  await page.locator(`.q-tab[data-col="${await col(page, id)}"]`).click();   // a 360 px vê-se uma coluna de cada vez
  await page.waitForTimeout(400);
  const cdp = await page.context().newCDPSession(page);
  const s = await page.locator(sel(id)).boundingBox(), t = await page.locator(`.q-tab[data-col="${coluna}"]`).boundingBox();
  const pt = (x, y) => [{ x, y, id: 1 }];
  const x0 = s.x + 30, y0 = s.y + 20, x1 = t.x + t.width / 2, y1 = t.y + t.height / 2;
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: pt(x0, y0) });
  await page.waitForTimeout(450);
  for (let i = 1; i <= 12; i++) { await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: pt(x0 + (x1 - x0) * i / 12, y0 + (y1 - y0) * i / 12) }); await page.waitForTimeout(16); }
  await page.waitForTimeout(150);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await page.waitForTimeout(450);
  await cdp.detach();
}
// força o GET /api/quadro (o poll é de 15 s; o /api/state tem cache de 5 s)
async function recarrega(page) {
  await espera(5300);
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await page.waitForTimeout(700);
}
const medeDialogo = (page) => page.evaluate(() => {
  const lum = (c) => { const [r, g, b] = c.match(/[\d.]+/g).slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const bg = (e) => { while (e) { const c = getComputedStyle(e).backgroundColor; if (c && !/rgba\(.*, 0\)|transparent/.test(c)) return c; e = e.parentElement; } return "rgb(0,0,0)"; };
  const dr = document.querySelector(".q-dr"), btns = [...dr.querySelectorAll(".q-df .q-btn")];
  const ts = [...dr.querySelectorAll("h2, .q-lnota, label, .q-err:not(:empty), .q-df .q-btn:not([disabled]), input, select, textarea")]
    .map((e) => [e.id || e.className || e.tagName, +ratio(getComputedStyle(e).color, bg(e)).toFixed(2)]);
  return { sw: document.scrollingElement.scrollWidth, iw: innerWidth, drw: [dr.scrollWidth, dr.clientWidth], hs: btns.map((b) => Math.round(b.getBoundingClientRect().height)),
    maus: ts.filter(([, r]) => r < 4.5), n: ts.length, rect: dr.getBoundingClientRect().toJSON() };
});

// ---- 1. ponta a ponta com o backend real (1440, escuro) ----
async function pontaAPonta(browser) {
  const A = "m-qa-lancar", B = "m-qa-mover", C = "m-qa-mkt", Dd = "m-qa-outro", Z = "m-qa-apagar";
  const tmp = dados([cartao(A, "a-fazer", 10, "Lançar trabalho — Quadro (v2)!", "office", "Brief do cartão: arrastar lança."),
    cartao(B, "a-fazer", 20, "Só mover este"), cartao(C, "ideias", 10, "Campanha de Natal", "campanhas", "Três peças."),
    cartao(Dd, "ideias", 20, "Sem projecto lançável", "outro"), cartao(Z, "ideias", 30, "Para apagar")]);
  const { base, srv } = await subir(tmp);
  const antesArv = new Set(arvore(tmp));
  try {
    const { ctx, page, posts, errs } = await pagina(browser, base);
    const cj = () => readFileSync(path.join(tmp, "quadro", "cartoes.json"));
    const cj0 = cj(), ordem0 = await ordem(page, "a-fazer");

    // AC1/AC3: arrastar com o rato → diálogo sem POST; Esc → volta à coluna e posição, foco no cartão, disco igual
    await arrastaRato(page, A, "em-curso");
    ok(await drLancar(page) && posts.length === 0, "rato: largar em Em curso abre o diálogo, sem POST");
    ok(await col(page, A) === "em-curso", "rato: o cartão aparece em Em curso enquanto o diálogo está aberto");
    ok(await foco(page) === "q-dh", "AC2: foco no título da gaveta");
    const labels = await page.evaluate(() => [...document.querySelectorAll(".q-lf input, .q-lf select, .q-lf textarea")].every((x) => document.querySelector(`label[for="${x.id}"]`)));
    ok(labels, "AC2: todos os campos com <label for>");
    for (let i = 0; i < 20; i++) await page.keyboard.press("Tab");
    const t1 = await page.evaluate(() => document.querySelector(".q-dr").contains(document.activeElement));
    for (let i = 0; i < 20; i++) await page.keyboard.press("Shift+Tab");
    ok(t1 && await page.evaluate(() => document.querySelector(".q-dr").contains(document.activeElement)), "AC2: Tab e Shift+Tab não saem da gaveta");
    const v = await page.evaluate(() => { const f = document.querySelector(".q-lf"); return [f.squad.value, f.projecto.value, f.kind.value, f.mode.value, f.slug.value]; });
    ok(v.join() === "dev,office,feat,goal,lancar-trabalho-quadro-v2", "AC5/AC7: sugestão office + slug " + v.join());
    ok(/Isto vai ser enviado à squad · faltam [\d,]+ KB de 8 KB/.test(await page.locator("#q-l-kb").textContent()), "AC8: aviso e KB que faltam");
    await page.keyboard.press("Escape");
    ok(await fechado(page), "Esc fecha o diálogo");
    ok(await col(page, A) === "a-fazer" && (await ordem(page, "a-fazer")).join() === ordem0.join(), "AC3 rato+Esc: volta à coluna e posição de origem");
    ok(await foco(page) === A, "AC3: o foco volta ao cartão (" + await foco(page) + ")");
    ok(posts.length === 0 && cj().equals(cj0) && fila(tmp).length === 0, "AC3: sem POST, cartoes.json e fila intactos");

    // AC1/AC3: teclado (Espaço, →, Espaço) → diálogo; «Cancelar»
    await page.locator(sel(A)).focus();
    await page.keyboard.press(" "); await page.keyboard.press("ArrowRight"); await page.keyboard.press(" ");
    await page.waitForTimeout(300);
    ok(await drLancar(page) && posts.length === 0, "teclado: Espaço→Espaço em Em curso abre o diálogo, sem POST");
    await page.locator('.q-lf [data-a="lcancel"]').click();
    ok(await fechado(page) && await col(page, A) === "a-fazer" && (await ordem(page, "a-fazer")).join() === ordem0.join() && await foco(page) === A && posts.length === 0,
      "AC3 teclado+Cancelar: volta, foco no cartão, sem POST");

    // AC1/AC3: menu «Mover para… → Em curso»; Esc
    await viaMenu(page, A);
    ok(await drLancar(page) && posts.length === 0, "menu: abre o diálogo, sem POST");
    await page.keyboard.press("Escape");
    ok(await fechado(page) && await col(page, A) === "a-fazer" && posts.length === 0 && cj().equals(cj0), "AC3 menu+Esc: volta, sem POST");

    // AC9 (desktop): clique no fundo não fecha
    await viaMenu(page, A);
    await page.mouse.click(10, 450);
    ok(await drLancar(page), "D7: clicar fora não fecha o diálogo (não perde edições)");
    await page.keyboard.press("Escape"); await fechado(page);

    // D7: com o diálogo aberto, nem um GET em voo nem a resposta de um mover/apagar anterior devolvem o cartão à origem ou fecham
    // o diálogo; Cancelar só mexe nele. Só aqui o browser atrasa pedidos (2,5 s); seguem na mesma para o server.py.
    const atrasa = (m) => page.route("**/api/quadro", async (r) => { if (r.request().method() === m) await espera(2500); await r.continue(); });
    await atrasa("GET");
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await viaMenu(page, A);
    await page.waitForTimeout(3000);
    ok(await drLancar(page) && await col(page, A) === "em-curso", "D7: um GET em voo não devolve o cartão por baixo do diálogo");
    await page.keyboard.press("Escape"); await fechado(page);
    await page.unroute("**/api/quadro");
    await atrasa("POST");
    await viaMenu(page, B, "a-espera");
    await viaMenu(page, A);
    await page.waitForTimeout(3000);
    ok(await drLancar(page) && await col(page, A) === "em-curso" && await col(page, B) === "a-espera", "D7: a resposta de um mover anterior não desfaz o do diálogo");
    await page.keyboard.press("Escape"); await fechado(page);
    await page.locator(sel(Z)).click(); await page.locator('.q-dr [data-a="apagar"]').click(); await page.locator('.q-dr [data-a="sim"]').click();
    await viaMenu(page, A);
    await page.waitForTimeout(3000);
    ok(await drLancar(page) && await col(page, A) === "em-curso", "D7: a resposta do apagar não fecha o diálogo de outro cartão");
    await page.keyboard.press("Escape"); await fechado(page);
    await page.unroute("**/api/quadro");
    ok(await col(page, A) === "a-fazer" && await col(page, B) === "a-espera" && await col(page, Z) === null && !disco(tmp)[Z] && disco(tmp)[B].coluna === "a-espera"
      && fila(tmp).length === 0, "D7: Cancelar só devolve este cartão (B fica em À espera, o apagado não volta)");
    posts.length = 0;

    // AC4: «Só mover» = um só POST op mover, nada na fila
    await viaMenu(page, B);
    await page.locator('.q-lf [data-a="lmover"]').click();
    await page.waitForTimeout(600);
    ok(posts.length === 1 && posts[0].path === "/api/quadro" && posts[0].b.op === "mover" && posts[0].b.coluna === "em-curso", "AC4: Só mover = 1 POST /api/quadro op mover");
    ok(disco(tmp)[B].coluna === "em-curso" && fila(tmp).length === 0, "AC4: B em Em curso no disco, fila vazia");

    // AC5: «outro» → Dev sem projecto, Lançar desactivado
    await viaMenu(page, Dd);
    ok(await page.locator("#q-l-squad").inputValue() === "dev" && await page.locator("#q-l-projecto").inputValue() === "" && await page.locator(".q-lf [type=submit]").isDisabled(),
      "AC5: outro → Dev, sem projecto, Lançar desactivado");
    await page.keyboard.press("Escape"); await fechado(page);

    // AC14: «Lançar» (arrastar) → 202, um <id>.json com as 12 chaves, cartão «a lançar…» em Em curso
    posts.length = 0;
    await arrastaRato(page, A, "em-curso");
    await page.locator("#q-l-brief").fill("Brief editado no diálogo.\nSegunda linha.");
    await page.locator(".q-lf [type=submit]").click();
    ok(await fechado(page), "Lançar fecha o diálogo");
    await page.waitForTimeout(300);
    const pedidos = fila(tmp).filter((n) => PEDIDO.test(n));
    ok(pedidos.length === 1 && fila(tmp).length === 1, "AC14: exactamente um <id>.json na fila " + fila(tmp).join());
    const ped = JSON.parse(readFileSync(path.join(tmp, "quadro-lancar", pedidos[0]), "utf8"));
    ok(Object.keys(ped).join() === CHAVES && ped.id + ".json" === pedidos[0] && ped.cartao_id === A && ped.squad === "dev" && ped.projecto === "office"
      && ped.kind === "feat" && ped.mode === "goal" && ped.slug === "lancar-trabalho-quadro-v2" && ped.brief === "Brief editado no diálogo.\nSegunda linha.",
      "AC14: pedido com as chaves por ordem e os valores do diálogo");
    ok(posts.length === 1 && posts[0].path === "/api/quadro/lancar", "Lançar = um só POST /api/quadro/lancar");
    ok(disco(tmp)[A].coluna === "em-curso" && disco(tmp)[A].descricao === "Brief do cartão: arrastar lança.", "D5/D8: cartão em Em curso no disco e a descrição não mudou");
    ok(/a lançar…/.test(await page.locator(sel(A)).textContent()) && await col(page, A) === "em-curso", "AC11/AC19: etiqueta «a lançar…» em Em curso");

    // AC24 (pendente): tirar e voltar a pôr em Em curso → só op mover, sem diálogo
    posts.length = 0;
    await viaMenu(page, A, "a-fazer"); await page.waitForTimeout(300);
    await viaMenu(page, A, "em-curso"); await page.waitForTimeout(300);
    ok(!(await drLancar(page)) && posts.length === 2 && posts.every((p) => p.path === "/api/quadro" && p.b.op === "mover"), "AC24: a lançar → Em curso só move, sem diálogo");
    ok(fila(tmp).length === 1, "AC24: não há segundo pedido");

    // host: lança → meta da tarefa falsa a correr + <id>.feito.json {task_id}
    const T = `${stamp(NOW).slice(0, 8)}-${stamp(NOW).slice(9, 13)}-lancar-trabalho-quadro-v2`;
    meta(tmp, T, { status: "a correr", started_at: local(NOW - 20 * 60e3), titulo: "Lançar trabalho — Quadro (v2)!" });
    writeFileSync(path.join(tmp, "tasks", T, "heartbeat"), ""); writeFileSync(path.join(tmp, "tasks", T, "SPEC.md"), "x");
    json(path.join(tmp, "quadro-lancar", ped.id + ".feito.json"), { task_id: T });
    await recarrega(page);
    const lig = page.locator(sel(A));
    ok(await lig.locator(".prog").count() === 1 && await lig.locator('[data-a="exec"]').count() === 1, "AC11/AC20: ligado com a barra de progresso e «Abrir execução»");
    ok(await page.locator(sel(`dev:${T}`)).count() === 0, "AC20/D12: o automático dev:T não aparece (sem duplicado)");
    ok(await page.locator(".q-board .q-card", { hasText: "Lançar trabalho — Quadro (v2)!" }).count() === 1, "D12: um só cartão com este título no quadro");
    if (CAPTURAS) { /* a barra real fica na captura dos estados */ }
    // AC24 (ligado): sair e voltar a Em curso → sem diálogo
    posts.length = 0;
    await viaMenu(page, A, "a-espera"); await viaMenu(page, A, "em-curso");
    ok(!(await drLancar(page)) && posts.every((p) => p.b?.op === "mover") && fila(tmp).length === 2, "AC24: ligado → Em curso só move, sem diálogo nem pedido novo");
    // «Abrir execução» abre a tarefa no escritório
    await lig.locator('[data-a="exec"]').click();
    await page.waitForTimeout(500);
    ok(!errs.length, "Abrir execução sem erros JS");
    await page.goto(base + "#quadro"); await page.waitForSelector(sel(A));

    // fim: PR merged → o cartão vai sozinho para Feito, uma vez, sem duplicado
    meta(tmp, T, { status: "concluída", started_at: local(NOW - 20 * 60e3), finished_at: local(Date.now()), titulo: "Lançar trabalho — Quadro (v2)!",
      pr: "https://github.com/exemplo-org/squad-office/pull/99 (merged)" });
    await recarrega(page);
    ok(await col(page, A) === "feito" && disco(tmp)[A].coluna === "feito", "AC21: execução merged → Feito (UI e disco)");
    ok(await page.locator(".q-board .q-card", { hasText: "Lançar trabalho — Quadro (v2)!" }).count() === 1 && await page.locator(sel(`dev:${T}`)).count() === 0,
      "AC21/D12: uma só vez na coluna Feito, automático escondido");
    const mv = path.join(tmp, "quadro-lancar", ped.id + ".movido.json");
    ok(existsSync(mv) && JSON.parse(readFileSync(mv, "utf8")).coluna === "feito", "D11: <id>.movido.json com coluna feito");
    // AC22: voltar a pôr em Em curso → não volta a ser movido nem lançado
    posts.length = 0;
    await viaMenu(page, A, "em-curso");
    ok(!(await drLancar(page)), "AC22: ligado e acabado → Em curso sem diálogo");
    await recarrega(page);
    ok(await col(page, A) === "em-curso" && disco(tmp)[A].coluna === "em-curso", "AC22: o GET seguinte não o move outra vez");

    // AC23: erro do host → À espera uma vez; detalhe mostra o erro; pode relançar (diálogo abre); Esc devolve
    posts.length = 0;
    await viaMenu(page, C);
    ok(await page.locator("#q-l-squad").inputValue() === "dev" && await page.locator("#q-l-projecto").inputValue() === "campanhas"
      && await page.locator("#q-l-kind").inputValue() === "feat" && await page.locator("#q-l-mode").inputValue() === "goal", "AC5: 1.ª squad lançável e o projecto do cartão");
    await page.locator("#q-l-squad").selectOption("ops");
    ok((await page.locator("#q-l-kind option").evaluateAll((o) => o.map((x) => x.value))).join() === "tarefa"
      && (await page.locator("#q-l-mode option").evaluateAll((o) => o.map((x) => x.value))).join() === "task", "AC6: Ops → tipos e modos da config");
    await page.locator(".q-lf [type=submit]").click(); await fechado(page);
    const pc = posts.find((p) => p.path.endsWith("/lancar"))?.b;
    ok(pc && pc.squad === "ops" && pc.projecto === "campanhas" && pc.mode === "task" && pc.kind === "tarefa", "AC6: corpo Ops com o id do projecto");
    const idC = fila(tmp).filter((n) => PEDIDO.test(n)).map((n) => JSON.parse(readFileSync(path.join(tmp, "quadro-lancar", n), "utf8"))).find((p) => p.cartao_id === C).id;
    json(path.join(tmp, "quadro-lancar", idC + ".feito.json"), { erro: "sem créditos na conta" });
    await recarrega(page);
    ok(await col(page, C) === "a-espera" && /erro ao lançar/.test(await page.locator(sel(C)).textContent()), "AC23: erro → À espera com «erro ao lançar»");
    await page.locator(`${sel(C)} .q-t`).click(); await page.waitForTimeout(300);
    ok(/Erro ao lançar/.test(await page.locator(".q-dr").textContent()) && /sem créditos na conta/.test(await page.locator(".q-dr").textContent()), "AC11: erro no detalhe");
    await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    await viaMenu(page, C);
    ok(await drLancar(page), "AC23: depois de erro, Em curso abre de novo o diálogo");
    await page.keyboard.press("Escape"); await fechado(page);
    ok(await col(page, C) === "a-espera", "AC3: Esc devolve à coluna de origem (À espera)");
    await recarrega(page);
    ok(await col(page, C) === "a-espera", "AC23: o erro só move uma vez");

    // AC18: só ficaram ficheiros novos em quadro-lancar/ (nomes do contrato) e o cartoes.json
    const novos = arvore(tmp).filter((f) => !antesArv.has(f) && !f.startsWith("tasks/"));
    ok(novos.every((f) => /^quadro-lancar\/\d{8}T\d{6}-[0-9a-f]{8}(\.feito|\.movido)?\.json$/.test(f)), "AC18: nada escrito fora de quadro-lancar/ " + novos.join(" "));
    ok(!errs.length, "sem erros JS na página: " + errs.join(" | "));
    await ctx.close();
  } finally { srv.kill(); rmSync(tmp, { recursive: true, force: true }); }
}

// ---- 2. 360 px × 5 temas (toque), 1440 e estados no cartão; capturas ----
async function capturas(browser) {
  const A = "m-qa-lancar", E = "m-qa-pend", F = "m-qa-lig", G = "m-qa-erro";
  const tmp = dados([cartao(A, "a-fazer", 10, "Lançar trabalho — Quadro (v2)!", "office", "Arrastar um cartão para Em curso abre esta confirmação."),
    cartao("m-qa-x", "a-fazer", 20, "Rever textos do e-book", "ebook"),
    cartao(E, "em-curso", 10, "Lembretes por WhatsApp", "loja"), cartao(F, "em-curso", 20, "Quadro: lançar com confirmação", "office"),
    cartao(G, "em-curso", 30, "Calendário orgânico de Dezembro", "campanhas")]);
  const fl = path.join(tmp, "quadro-lancar"), ped = (id, cid, squad, extra = {}) => json(path.join(fl, id + ".json"), { v: 1, id, cartao_id: cid, squad,
    projecto: "office", kind: squad === "dev" ? "feat" : "tarefa", mode: squad === "dev" ? "goal" : "task", slug: "abc", titulo: "x",
    brief: "", pedido_em: isoTz(NOW - 25 * 60e3), ...extra });
  const s = stamp(NOW - 25 * 60e3), T = `${s.slice(0, 8)}-${s.slice(9, 13)}-quadro-lancar`;
  ped(`${s}-0000000e`, E, "dev"); ped(`${s}-0000000f`, F, "dev"); ped(`${s}-00000010`, G, "ops");
  json(path.join(fl, `${s}-0000000f.feito.json`), { task_id: T });
  json(path.join(fl, `${s}-00000010.feito.json`), { erro: "slug já existe" });
  json(path.join(fl, `${s}-00000010.movido.json`), { v: 1, coluna: null, em: isoTz(NOW) });   // o utilizador tirou-o e voltou a pô-lo: fica em Em curso
  meta(tmp, T, { status: "a correr", started_at: local(NOW - 25 * 60e3), titulo: "Quadro: lançar com confirmação" });
  for (const f of ["heartbeat", "SPEC.md", "PLAN.md"]) writeFileSync(path.join(tmp, "tasks", T, f), "x");
  const { base, srv } = await subir(tmp);
  try {
    for (const tema of TEMAS) {
      const { ctx, page, posts, errs } = await pagina(browser, base, { w: 360, h: 760, tema, touch: true });
      const ordem0 = await ordem(page, "a-fazer");
      await arrastaToque(page, A, "em-curso");
      ok(await drLancar(page) && posts.length === 0, `360 ${tema}: toque (pressão longa + aba Em curso) abre o diálogo, sem POST`);
      const m = await medeDialogo(page);
      ok(m.sw <= m.iw && m.drw[0] <= m.drw[1], `AC9 360 ${tema}: sem scroll horizontal ${JSON.stringify([m.sw, m.iw, m.drw])}`);
      ok(m.hs.length === 3 && m.hs.every((h) => h >= 44), `AC9 360 ${tema}: botões ≥ 44 px ${m.hs}`);
      ok(!m.maus.length && m.n > 10, `AC10 360 ${tema}: contraste ≥ 4.5 em ${m.n} elementos ${JSON.stringify(m.maus)}`);
      if (CAPTURAS) await page.screenshot({ path: path.join(OUT, `quadro-lancar-360-${tema}.png`) });
      // puxar a pega para baixo não fecha (D7)
      const g = await page.locator(".q-dr .q-grab").boundingBox();
      await page.mouse.move(g.x + g.width / 2, g.y + 3); await page.mouse.down(); await page.mouse.move(g.x + g.width / 2, g.y + 200, { steps: 6 }); await page.mouse.up();
      await page.waitForTimeout(300);
      ok(await drLancar(page), `360 ${tema}: puxar a pega não fecha`);
      await page.locator('.q-lf [data-a="lcancel"]').click();
      ok(await fechado(page) && await col(page, A) === "a-fazer" && (await ordem(page, "a-fazer")).join() === ordem0.join() && posts.length === 0,
        `AC3 360 ${tema}: Cancelar devolve à origem, sem POST`);
      // estados no cartão: contraste e alvo
      const est = await page.evaluate(() => {
        const lum = (c) => { const [r, g, b] = c.match(/[\d.]+/g).slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
        const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
        const bg = (e) => { while (e) { const c = getComputedStyle(e).backgroundColor; if (c && !/rgba\(.*, 0\)|transparent/.test(c)) return c; e = e.parentElement; } return "rgb(0,0,0)"; };
        return [...document.querySelectorAll(".q-board .q-lz, .q-board .q-le, .q-board .q-lx")].map((e) => [e.className, +ratio(getComputedStyle(e).color, bg(e)).toFixed(2)]);
      });
      ok(est.length === 3 && est.every(([, r]) => r >= 4.5), `AC10 360 ${tema}: estados no cartão ${JSON.stringify(est)}`);
      if (tema === "escuro") {
        await page.locator(`.q-tab[data-col="em-curso"]`).click(); await page.waitForTimeout(400);
        const e = page.locator(sel(E)), f = page.locator(sel(F)), gg = page.locator(sel(G));
        ok(/a lançar…/.test(await e.textContent()) && await f.locator(".prog").count() === 1 && await f.locator('[data-a="exec"]').count() === 1
          && /erro ao lançar/.test(await gg.textContent()) && await page.locator(sel(`dev:${T}`)).count() === 0, "AC11: a lançar / ligado com barra / erro, sem duplicado");
        if (CAPTURAS) await page.screenshot({ path: path.join(OUT, "quadro-lancar-estados-360.png") });
      }
      ok(!errs.length, `360 ${tema}: sem erros JS ${errs.join(" | ")}`);
      await ctx.close();
    }
    const { ctx, page, errs } = await pagina(browser, base, { tema: "escuro" });
    await arrastaRato(page, A, "em-curso");
    const m = await medeDialogo(page);
    ok(await drLancar(page) && m.rect.width <= 460 && m.rect.x > 900, `AC9 1440: painel lateral ${JSON.stringify(m.rect)}`);
    ok(!m.maus.length, `AC10 1440 escuro: contraste ${JSON.stringify(m.maus)}`);
    if (CAPTURAS) await page.screenshot({ path: path.join(OUT, "quadro-lancar-1440.png") });
    await page.keyboard.press("Escape"); await fechado(page);
    ok(await col(page, A) === "a-fazer" && !errs.length, "1440: Esc devolve, sem erros JS");
    await ctx.close();
  } finally { srv.kill(); rmSync(tmp, { recursive: true, force: true }); }
}

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
try {
  await pontaAPonta(browser);
  await capturas(browser);
} finally { await browser.close(); }
if (CAPTURAS) for (const f of readdirSync(OUT).filter((f) => f.startsWith("quadro-lancar-"))) console.log(`${f} ${Math.round(statSync(path.join(OUT, f)).size / 1024)} KB`);
console.log(falhas ? `${falhas} falha(s)` : "tudo ok");
process.exit(falhas ? 1 : 0);
