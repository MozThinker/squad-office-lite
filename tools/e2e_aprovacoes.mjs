// E2E das Aprovações que lançam (Lite 0.6.1, ADR-024), em Chromium e WebKit (perfil iPhone a 375 px e ecrã de 1440 px).
// Sobe o server.py REAL em 127.0.0.1 com pastas temporárias (nunca as reais; nada sai da máquina). Este ficheiro faz de
// kit: escreve as decisões e o sinal `.lancar-aprovados.json` na pasta das decisões, e depois o «→ aprovado».
// Verifica: com o lançamento ligado, a linha com «[lança: loja-v2]» mostra o selo «▶ Aprovar lança a tarefa» com o slug e
// o botão «Aprovar e lançar» (nome acessível «Aprovar e lançar a tarefa loja-v2»); a marca crua não aparece; a linha sem
// marca diz «Aprovar só regista»; uma decisão que o kit não criou diz «Lançamento desligado: aprovar só regista»; aprovar
// escreve na fila v1 de sempre; depois de aprovada e lançada, «Aprovado: tarefa lançada». Sem sinal (desligado), a mesma
// linha diz «Lançamento desligado: aprovar só regista» e o botão volta a «Aprovar». Alvos ≥ 44 px a 375 px e sem scroll
// horizontal. Capturas (para o livro) em docs/capturas/aprovacoes-lanca-<browser>-<375|1440>-<ligado|desligado>.png.
//
// Como correr (Playwright fica fora do repo; não há package.json):
//   npm i --prefix /tmp/pw-music playwright@1.64.1 && npx --prefix /tmp/pw-music playwright install chromium webkit
//   PLAYWRIGHT_DIR=/tmp/pw-music node tools/e2e_aprovacoes.mjs [--sem-capturas] [--browsers chromium,webkit]   (ou E2E_BROWSER)
// Sai com 1 se algo falhar, 2 sem Playwright.
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PW = process.env.PLAYWRIGHT_DIR || "/tmp/pw-music";
let pw;
try { pw = createRequire(path.join(PW, "x.js"))("playwright"); } catch {
  console.error(`Playwright não encontrado em ${PW}. Instala com: npm i --prefix ${PW} playwright@1.64.1`);
  process.exit(2);
}
const arg = (n, omissao) => { const i = process.argv.indexOf(n); return i > 0 ? process.argv[i + 1] : omissao; };
const BROWSERS = arg("--browsers", process.env.E2E_BROWSER || "chromium,webkit").split(",");
const CAPTURAS = !process.argv.includes("--sem-capturas");
const OUT = path.join(ROOT, "docs/capturas");
const IPHONE = pw.devices["iPhone 13 Mini"] || { viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 };
let falhas = 0;
const ok = (c, m) => { console.log((c ? "ok    " : "FALHA ") + m); if (!c) falhas++; return c; };
const espera = (ms) => new Promise((r) => setTimeout(r, ms));

const HOJE = new Date().toISOString().slice(0, 10);
const KIT = `${HOJE}-lancar-loja-v2.md`, OUTRO = `${HOJE}-orcamento.md`;
const pedido = (titulo, linhas) => `---\nestado: pendente\ncriado: ${HOJE}\n---\n# ${titulo}\n\n## O quê\nPedido de teste do e2e.\n\n## Decisões\n${linhas.join("\n")}\n`;
const L1 = "- [L1] Lançar `loja-v2` pela squad dev [lança: loja-v2]", P1 = "- [P1] Registar a nota de entrega";

function dados() {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "e2e-aprov-lanca-"));
  for (const d of ["tasks", "claude", "squads", "aprov", "bib", "quadro", "quadro-lancar", "fila", "push"]) mkdirSync(path.join(tmp, d), { recursive: true });
  writeFileSync(path.join(tmp, "office.yaml"), ["versao: 1", "marca: Squad Office", "utilizador:", "  nome: Ana", "fuso: Europe/Lisbon", "squads:",
    "  - id: dev", "    nome: Dev", "    lead: tech-lead", "    tipos: [feat, fix]", "    modos: [goal, task]", "projectos:", "  - id: loja", "    nome: Loja", ""].join("\n"));
  writeFileSync(path.join(tmp, "aprov", KIT), pedido("Lançar loja-v2", [L1, P1]));
  writeFileSync(path.join(tmp, "aprov", OUTRO), pedido("Orçamento da campanha", ["- [A1] Tecto 145 € para a campanha", "- [A2] Lançar à mão [lança: campanha]"]));
  return tmp;
}
const sinal = (tmp, decisoes) => writeFileSync(path.join(tmp, "aprov", ".lancar-aprovados.json"),
  JSON.stringify({ v: 1, ligado: true, atualizado: Date.now() / 1000, decisoes }));

async function subir(tmp) {
  const port = await new Promise((r) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => r(p)); }); });
  const env = { ...process.env, OFFICE_CONFIG: path.join(tmp, "office.yaml"), TASKS_DIR: path.join(tmp, "tasks"), CLAUDE_PROJECTS_DIR: path.join(tmp, "claude"),
    SQUADS_DIR: path.join(tmp, "squads"), APROVACOES_DIR: path.join(tmp, "aprov"), BIBLIOTECA_DIR: path.join(tmp, "bib"),
    DECISOES_FILA_DIR: path.join(tmp, "fila"), QUADRO_DIR: path.join(tmp, "quadro"), QUADRO_LANCAR_DIR: path.join(tmp, "quadro-lancar"),
    PUSH_DIR: path.join(tmp, "push"), PORT: String(port) };
  const srv = spawn(process.env.PYTHON || "python3", ["-c", `import server; from http.server import ThreadingHTTPServer; ThreadingHTTPServer(("127.0.0.1", ${port}), server.Handler).serve_forever()`],
    { cwd: ROOT, env, stdio: "ignore" });
  const base = `http://127.0.0.1:${port}/`;
  for (let i = 0; ; i++) {
    try { if ((await fetch(base + "healthz")).ok) break; } catch {}
    if (i > 80) throw new Error("server.py não respondeu em /healthz");
    await espera(100);
  }
  return { base, srv };
}

async function pagina(browser, base, movel) {
  const ctx = await browser.newContext(movel ? { ...IPHONE, colorScheme: "dark", reducedMotion: "reduce" }
    : { viewport: { width: 1440, height: 1000 }, colorScheme: "dark", reducedMotion: "reduce" });
  // os dois pedidos abertos desde o início (o mesmo localStorage que o clique em cada pedido grava)
  await ctx.addInitScript((ks) => { if (!sessionStorage.getItem("__i")) { sessionStorage.setItem("__i", "1"); localStorage.setItem("aprAbertos", JSON.stringify(ks)); } },
    [`geral/${KIT}`, `geral/${OUTRO}`]);
  const page = await ctx.newPage(), errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.goto(base);
  await page.waitForSelector("#apr-list .dec .lanca");
  return { ctx, page, errs };
}
const dec = (page, ficheiro, id) => page.locator(`#apr-list .apr`).filter({ hasText: ficheiro === KIT ? "Lançar loja-v2" : "Orçamento da campanha" })
  .locator(".dec").filter({ has: page.locator(`b:text-is("${id}")`) });
const captura = async (page, nome) => {
  if (!CAPTURAS) return;
  mkdirSync(OUT, { recursive: true });
  await page.locator("#aprovacoes").scrollIntoViewIfNeeded();
  await page.locator("#aprovacoes").screenshot({ path: path.join(OUT, nome) });
};

// o /api/state pode vir de uma cache curta do servidor: recarrega até a linha L1 mudar (≤ 15 s)
async function ate(page, rx) {
  let t = "";
  for (let i = 0; i < 15; i++) {
    await page.reload();
    await page.waitForSelector("#apr-list .dec .lanca");
    t = await dec(page, KIT, "L1").innerText();
    if (rx.test(t)) break;
    await espera(1000);
  }
  return t;
}

async function verificar(page, tam, browser, ligado) {
  const t = `${browser} ${tam} ${ligado ? "ligado" : "desligado"}`;
  const l1 = dec(page, KIT, "L1"), p1 = dec(page, KIT, "P1"), a2 = dec(page, OUTRO, "A2");
  const todo = await page.locator("#apr-list").innerText();
  ok(!todo.includes("[lança:"), `${t}: a marca crua não aparece no texto`);
  ok((await l1.locator("p").first().innerText()).trim().endsWith("Lançar `loja-v2` pela squad dev"), `${t}: o texto da linha fica sem a marca`);
  const selo = await l1.locator(".lanca").innerText();
  if (ligado) {
    ok(/▶\s*Aprovar lança a tarefa\s+loja-v2/.test(selo), `${t}: selo «▶ Aprovar lança a tarefa loja-v2» (${selo})`);
    ok(await l1.getByRole("button", { name: "Aprovar e lançar a tarefa loja-v2" }).isVisible(), `${t}: botão «Aprovar e lançar» com o nome acessível da tarefa`);
    ok((await l1.locator('button[data-a="aprovar"]').innerText()).trim() === "Aprovar e lançar", `${t}: rótulo visível «Aprovar e lançar»`);
  } else {
    ok(/Lançamento desligado: aprovar só regista\s+loja-v2/.test(selo), `${t}: selo «Lançamento desligado: aprovar só regista» (${selo})`);
    ok((await l1.locator('button[data-a="aprovar"]').innerText()).trim() === "Aprovar", `${t}: o botão volta a «Aprovar»`);
  }
  ok((await l1.locator('button[data-a="recusar"]').innerText()).trim() === "Recusar", `${t}: «Recusar» não muda`);
  ok((await p1.locator(".lanca").innerText()).trim() === "Aprovar só regista", `${t}: linha sem marca diz «Aprovar só regista»`);
  ok(/Lançamento desligado: aprovar só regista/.test(await a2.locator(".lanca").innerText()), `${t}: decisão que o kit não criou não lança`);
  if (tam === "375") {
    const pequenos = await page.evaluate(() => [...document.querySelectorAll("#apr-list button")].filter((b) => b.offsetParent)
      .map((b) => b.getBoundingClientRect()).filter((r) => r.height < 44).length);
    ok(pequenos === 0, `${t}: botões das Aprovações com ≥ 44 px de altura`);
    ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${t}: sem scroll horizontal`);
  }
}

async function correr(browserName) {
  const tmp = dados();
  sinal(tmp, { [KIT]: "pendente" });
  const { base, srv } = await subir(tmp);
  const browser = await pw[browserName].launch();
  try {
    for (const movel of [true, false]) {
      const tam = movel ? "375" : "1440";
      const { ctx, page, errs } = await pagina(browser, base, movel);
      await verificar(page, tam, browserName, true);
      await captura(page, `aprovacoes-lanca-${browserName}-${tam}-ligado.png`);
      ok(errs.length === 0, `${browserName} ${tam}: sem erros na página (${errs.join(" | ")})`);
      await ctx.close();
    }
    // aprovar e lançar (só no 1.º contexto): a confirmação nomeia a tarefa e a fila v1 recebe o JSON de sempre
    {
      const { ctx, page } = await pagina(browser, base, false);
      const l1 = dec(page, KIT, "L1");
      await l1.locator('button[data-a="aprovar"]').click();
      ok(/Aprovar L1 e lançar a tarefa loja-v2\?/.test(await l1.locator(".cf").innerText()), `${browserName}: a confirmação diz que lança a tarefa`);
      await l1.locator('button[data-a="ok"]').click();
      await page.waitForSelector("text=depois lança a tarefa loja-v2", { timeout: 5000 }).then(() => ok(true, `${browserName}: recebido · depois lança a tarefa`),
        () => ok(false, `${browserName}: recebido · depois lança a tarefa`));
      const fila = readdirSync(path.join(tmp, "fila")).filter((f) => f.endsWith(".json"));
      const j = fila.length === 1 ? JSON.parse(readFileSync(path.join(tmp, "fila", fila[0]), "utf8")) : {};
      ok(j.v === 1 && j.accao === "aprovar" && j.id === "L1" && j.ficheiro === KIT && !("lanca" in j), `${browserName}: fila v1 sem campos novos`);
      // o aplicador e o kit: «→ aprovado» na linha e o lancar arrancou
      writeFileSync(path.join(tmp, "aprov", KIT), pedido("Lançar loja-v2", [`${L1} → aprovado (${HOJE})`, P1]));
      sinal(tmp, { [KIT]: "lançado" });
      const tl = await ate(page, /✓ Aprovado: tarefa lançada/);
      ok(/✓ Aprovado: tarefa lançada/.test(tl), `${browserName}: «Aprovado: tarefa lançada» (${tl})`);
      sinal(tmp, { [KIT]: "pendente" });
      const te = await ate(page, /✓ Aprovado: à espera do lançamento/);
      ok(/✓ Aprovado: à espera do lançamento/.test(te), `${browserName}: «Aprovado: à espera do lançamento» (${te})`);
      await ctx.close();
    }
    // sem sinal: desligado (a omissão do kit); a fila fica vazia, como depois de o aplicador a consumir
    for (const f of readdirSync(path.join(tmp, "fila"))) rmSync(path.join(tmp, "fila", f), { recursive: true });
    writeFileSync(path.join(tmp, "aprov", KIT), pedido("Lançar loja-v2", [L1, P1]));
    rmSync(path.join(tmp, "aprov", ".lancar-aprovados.json"));
    for (const movel of [true, false]) {
      const tam = movel ? "375" : "1440";
      const { ctx, page } = await pagina(browser, base, movel);
      await verificar(page, tam, browserName, false);
      await captura(page, `aprovacoes-lanca-${browserName}-${tam}-desligado.png`);
      await ctx.close();
    }
  } finally {
    await browser.close();
    srv.kill();
    rmSync(tmp, { recursive: true, force: true });
  }
}

for (const b of BROWSERS) {
  try { await correr(b); } catch (e) { ok(false, `${b}: ${e.stack || e}`); }
}
console.log(falhas ? `${falhas} falha(s)` : "tudo ok");
process.exit(falhas ? 1 : 0);
