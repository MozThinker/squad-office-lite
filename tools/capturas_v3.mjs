// Verificações em browser e capturas da v3 (SPEC lite-v3, CA-55). Não corre na CI (D-13).
// Sobe o server.py real em 127.0.0.1 com um office.yaml FALSO numa pasta temporária (2 squads, uma worktree e uma sessão,
// 3 projectos); o /api/state, o /api/entregas e o /api/replay são injectados no browser a partir das funções puras do
// static/demo.js (estadoDemo/replayDemo, com relógio e semente fixos), como no capturas_temas.mjs.
//
// Como correr (Playwright fica fora do repo; não há package.json):
//   npm i --prefix /tmp/pw-music playwright@1.64.1
//   PLAYWRIGHT_DIR=/tmp/pw-music node tools/capturas_v3.mjs [--sem-capturas]
//
// Verifica (sai com código 1 se algo falhar), a 360 × 800 e a 1440 × 900 nos 5 temas: sem scroll horizontal; cabeçalho numa
// linha (≤ 56 px); barra de contadores com números; avisos depois do 2.º poll; separadores Tarefa | Agentes | Entregas com
// dados; Actividade e O meu dia abertos; a 1440 o #office mede 800–860 px; sem erros na consola.
// Capturas em docs/capturas/: v3-<tema>-{360,1440}.png e v3-{agentes,entregas,replay}-escuro-{360,1440}.png.
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
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
const { estadoDemo, replayDemo, diaLocal } = await import(path.join(ROOT, "static/demo.js"));
const CAPTURAS = !process.argv.includes("--sem-capturas");
const PORT = await new Promise((ok) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => ok(p)); }); });
const BASE = `http://127.0.0.1:${PORT}/`, OUT = path.join(ROOT, "docs/capturas");
const TEMAS = ["auto", "escuro", "claro", "sepia", "contraste"], LARG = [[360, 800], [1440, 900]];
const AGORA = Date.parse("2026-11-17T15:20:00Z");
let falhas = 0;
const ok = (cond, msg) => { if (!cond) { falhas++; console.error("FALHA: " + msg); } return cond; };

const tmp = mkdtempSync(path.join(os.tmpdir(), "capturas-v3-"));
for (const d of ["tasks", "squads/notas", "claude", "fila", "quadro", "aprov", "mini", "bib"]) mkdirSync(path.join(tmp, d), { recursive: true });
writeFileSync(path.join(tmp, "office.yaml"), `versao: 1
marca: Squad Office
utilizador:
  nome: Ana
fuso: Europe/Lisbon
squads:
  - id: dev
    nome: Dev
    lead: tech-lead
    personagem: Maestro
    tipos: [feat, fix]
    modos: [goal, task]
  - id: notas
    nome: Notas
    lead: cmo
    formato: sessao
projectos:
  - id: loja
    nome: Loja
    glifo: "◆"
  - id: menuqr
    nome: MenuQR
    glifo: "▲"
  - id: estudos
    nome: Estudos
    glifo: "●"
`, "utf-8");
const SERVE = `import server; from http.server import ThreadingHTTPServer; ThreadingHTTPServer(("127.0.0.1", ${PORT}), server.Handler).serve_forever()`;
const env = { ...process.env, OFFICE_CONFIG: path.join(tmp, "office.yaml"), TASKS_DIR: path.join(tmp, "tasks"),
  CLAUDE_PROJECTS_DIR: path.join(tmp, "claude"), SQUADS_DIR: path.join(tmp, "squads"), APROVACOES_DIR: path.join(tmp, "aprov"),
  DECISOES_FILA_DIR: path.join(tmp, "fila"), QUADRO_DIR: path.join(tmp, "quadro"), BIBLIOTECA_DIR: path.join(tmp, "bib"),
  BIBLIOTECA_MINI_DIR: path.join(tmp, "mini"), PORT: String(PORT) };
const srv = spawn("python3", ["-c", SERVE], { cwd: ROOT, env, stdio: "ignore" });

// estado 1 (1.º poll) e estado 2 (2.º poll: um PR novo e uma falha nova → avisos); entregas das terminadas
function fixtures(cfg) {
  const s1 = estadoDemo(AGORA, 1, cfg), s2 = estadoDemo(AGORA + 8000, 1, cfg);
  const t = s2.tasks.find((x) => x.running);
  s2.espera = { n: s2.espera.n + 1, itens: [...s2.espera.itens, { tipo: "pr", chave: `pr:${t.squad}/${t.id}-x`, id: t.id, squad: t.squad, project: t.project,
    pr_url: "https://github.com/exemplo/demo/pull/31", draft: true, finished: s2.now, desde: s2.now }] };
  s2.falhas_exec = [...s2.falhas_exec, { id: "demo-x", squad: t.squad, project: t.project, status: "erro", motivo: "api_error", http: 429, erro: true, finished: s2.now }];
  const itens = s2.tasks.filter((x) => !x.running).map((x) => ({ id: x.id, squad: x.squad, projecto: x.project,
    manchete: "Entrega de demonstração com a correcção feita.", resumo: "Resumo de demonstração.\n- testes verdes\n- PR aberto", terminada: x.finished,
    biblioteca: [`tarefas/${x.id}/SPEC.md`] }));
  return { s1, s2, entregas: { itens } };
}

async function pagina(browser, cfg, F, [w, h], tema) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: tema === "auto" ? "light" : "dark", reducedMotion: "reduce" });
  await ctx.addInitScript((t) => { try { localStorage.setItem("tema", t); } catch {} }, tema);
  const page = await ctx.newPage(), errs = [];
  await page.clock.setFixedTime(new Date(AGORA)); // o relógio do browser = o «now» das fixtures
  page.on("pageerror", (e) => errs.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errs.push(m.text()));
  let n = 0;
  await page.route("**/api/state", (r) => r.fulfill({ json: n++ ? F.s2 : F.s1 }));
  await page.route("**/api/entregas", (r) => r.fulfill({ json: F.entregas }));
  await page.route("**/api/replay?*", (r) => r.fulfill({ json: replayDemo(new URL(r.request().url()).searchParams.get("dia"), 1, AGORA, cfg) }));
  await page.goto(BASE);
  await page.waitForSelector("#barra b");
  await page.waitForFunction(() => document.querySelector("#sep-agentes"));
  await page.evaluate(() => tick()); // 2.º poll sem esperar 8 s: avisos
  await page.waitForSelector("#avisos .av");
  return { ctx, page, errs };
}

const semScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);

try {
  for (let i = 0; ; i++) {
    try { if ((await fetch(BASE + "healthz")).ok) break; } catch {}
    if (i > 50) throw new Error("server.py não respondeu em /healthz");
    await new Promise((r) => setTimeout(r, 100));
  }
  const cfg = await (await fetch(BASE + "api/config")).json(), F = fixtures(cfg);
  ok(cfg.squads.length === 2 && cfg.projectos.length === 3, "config de teste com 2 squads e 3 projectos");
  if (CAPTURAS) mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  { // o /api/state e o /api/entregas reais (pastas vazias): a página desenha-se sem erros e o separador Entregas responde
    const ctx = await browser.newContext({ viewport: { width: 360, height: 800 } }), page = await ctx.newPage(), errs = [];
    page.on("pageerror", (e) => errs.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errs.push(m.text()));
    await page.goto(BASE);
    await page.waitForSelector("#barra b");
    await page.waitForFunction(() => /ao vivo/.test(document.querySelector("#live").textContent));
    await page.click("#sep-entregas");
    await page.waitForSelector("#info-e :is(.en-top, [role=alert])");
    const woff = await page.evaluate(async () => (await fetch("fonts/pixelify-sans-600-latin.woff2")).headers.get("content-type"));
    ok(woff === "font/woff2", `fonte servida como ${woff}`);
    ok(!errs.length, `servidor real: erros ${errs.join(" | ")}`);
    await ctx.close();
  }
  for (const tema of TEMAS) for (const L of LARG) {
    const nome = `${tema} ${L[0]}`, { ctx, page, errs } = await pagina(browser, cfg, F, L, tema);
    ok(await semScroll(page), `${nome}: scroll horizontal`);
    const hh = await page.evaluate(() => document.querySelector("header").getBoundingClientRect().height);
    ok(L[0] > 360 || hh <= 56, `${nome}: cabeçalho com ${hh} px`);
    const nums = await page.$$eval("#barra button", (l) => l.map((b) => b.textContent));
    ok(nums.length === 6 && !nums.some((x) => x.includes("—")), `${nome}: contadores ${nums.join(" | ")}`);
    ok(await page.$$eval("#avisos .av", (l) => l.length) >= 2, `${nome}: avisos`);
    if (CAPTURAS && L[0] === 360) await page.screenshot({ path: path.join(OUT, `v3-avisos-${tema}-360.png`) });
    for (let k = 0; k < 8 && await page.$("#avisos [data-a=fechar]"); k++) await page.click("#avisos [data-a=fechar]"); // os avisos tapam a Fábrica a 360 px
    ok(/Agentes \(\d+\)/.test(await page.textContent("#sep-agentes")) && /por ler/.test(await page.textContent("#sep-entregas")), `${nome}: separadores`);
    if (L[0] === 1440) {
      const ow = await page.evaluate(() => document.querySelector("#office").getBoundingClientRect().width);
      ok(ow >= 800 && ow <= 860, `${nome}: #office com ${ow} px`);
      const [ih, oh] = await page.evaluate(() => ["#info", "#office"].map((s) => document.querySelector(s).getBoundingClientRect().height));
      ok(Math.abs(ih - oh) <= 2, `${nome}: #info com ${ih} px e #office com ${oh} px (CA-23)`);
    }
    // CA-23: o poll de 8 s não mexe no scroll do #info (só quando há scroll: a 360 px o #info tem max-height 70dvh)
    const st = await page.evaluate(() => { const i = document.querySelector("#info"), m = i.scrollHeight - i.clientHeight;
      if (m < 10) return "sem scroll"; i.scrollTop = Math.min(40, m); const y = i.scrollTop; tick();
      return new Promise((r) => setTimeout(() => r(i.scrollTop === y ? "ok" : `${y} → ${i.scrollTop}`), 300)); });
    ok(st !== "sem scroll" || L[0] > 360, `${nome}: #info sem scroll a 360 px`);
    ok(st === "ok" || st === "sem scroll", `${nome}: o poll mexeu no scroll do #info (${st})`);
    for (const k of ["day", "feed", "wait"]) await page.click(`#fabrica details[data-k="${k}"] > summary`);
    ok(await page.$$eval("#fabrica .fd li", (l) => l.length) === 10, `${nome}: Actividade com 10 linhas`);
    ok(/a trabalhar hoje: \d/.test(await page.textContent('#fabrica details[data-k="day"]')), `${nome}: O meu dia`);
    ok(await semScroll(page), `${nome}: scroll horizontal com a Fábrica aberta`);
    if (CAPTURAS) { await page.screenshot({ path: path.join(OUT, `v3-${tema}-${L[0]}.png`), fullPage: true }); console.log(`v3-${tema}-${L[0]}.png`); }
    if (tema === "escuro") {
      await page.click("#sep-agentes");
      ok(await page.$$eval("#ag-l li", (l) => l.length) >= 3, `${nome}: lista de agentes`);
      if (CAPTURAS) await page.locator("#info").screenshot({ path: path.join(OUT, `v3-agentes-escuro-${L[0]}.png`) });
      await page.click("#sep-entregas");
      await page.waitForSelector("#info-e .en li");
      ok(await semScroll(page), `${nome}: scroll horizontal nas Entregas`);
      if (CAPTURAS) await page.locator("#info").screenshot({ path: path.join(OUT, `v3-entregas-escuro-${L[0]}.png`) });
      await page.click("#rp-b");
      await page.waitForSelector("#replay #rp-t:not([disabled])");
      ok(/REPLAY \d\d:\d\d/.test(await page.textContent("#rp-et")), `${nome}: etiqueta do replay`);
      ok(await semScroll(page), `${nome}: scroll horizontal no replay`);
      if (CAPTURAS) await page.screenshot({ path: path.join(OUT, `v3-replay-escuro-${L[0]}.png`) });
    }
    ok(!errs.length, `${nome}: erros ${errs.join(" | ")}`);
    await ctx.close();
  }
  await browser.close();
  console.log(`dia do relógio fixo: ${diaLocal(AGORA, cfg.fuso)}`);
} finally {
  srv.kill();
}
console.log(falhas ? `${falhas} falha(s)` : "tudo bem");
process.exit(falhas ? 1 : 0);
