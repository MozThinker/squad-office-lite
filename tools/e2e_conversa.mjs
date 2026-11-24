// E2E da Conversa (SPEC 0.6.0, CA-37; também CA-32 e CA-35 com estilos e medidas reais; revisão do guardião, ADR-023).
// Não corre nos testes unitários. Numa pasta temporária, como o instalar-conversa.sh deixaria:
//   - o Office (server.py real) em http://127.0.0.1:P1 com um office.yaml FALSO (Conversa ligada) e só o publico/;
//   - o guardião (chat_host/guardiao.py) em http://127.0.0.1:P2, com CONVERSA_TLS_TESTE=1, atrás de um terminador TLS em
//     https://localhost:P3 (outra origem; certificado autoassinado criado com o openssl, como o Caddy à frente), a partir de
//     um /opt falso (chat.py, reader.py, guardiao.py, conversa.manifesto e web/) conferido pelo SHA256SUMS; o entrada.json
//     dos vectores (tests/fixtures/conversa_vectores.json) no estado dele, reescrito antes de cada browser novo (o código é
//     de uso único: como um --novo-codigo);
//   - a ponte real (chat_host/ponte.py) só com verificados/ e conversas/ e o `claude` falso (tests/bin/claude).
// Nada sai da máquina: o falso nunca chama a rede e não usa a ~ real (HOME temporário).
//
// Como correr (Playwright fica fora do repo; não há package.json):
//   npm i --prefix /tmp/pw-music playwright@1.64.1 && npx --prefix /tmp/pw-music playwright install chromium webkit
//   PLAYWRIGHT_DIR=/tmp/pw-music node tools/e2e_conversa.mjs [--sem-capturas] [--browsers chromium,webkit]   (ou E2E_BROWSER=webkit)
//
// Fluxo, em cada browser (perfil iPhone a 375 px e ecrã de 1440 px): o botão do Office é uma ligação de topo para o
// site da Conversa (nome do publico.json) → a página da Conversa → código → «Nova conversa» e logo a pergunta, sem
// esperar pela ponte (QA B3: o «Enviar» espera que a conversa exista) → (aviso de tokens, «Percebi») → cartão «Aprovar»
// do `ls` → resposta. Com o cartão pendente: o contador do botão do Office passa a 1, e uma página na origem do Office
// não tem a chave (IndexedDB «office-conversa» vazio) e um fetch com credenciais para a Conversa é bloqueado ou dá 403;
// o cartão continua pendente até ao clique na origem da Conversa (S2). Verifica também: o `ls` só corre depois do
// clique; a CSP do guardião sem violações (consola limpa; no WebKit, só as do <select> do motor e das capturas); a 375 px os
// alvos ≥ 44 × 44 px (CA-35); a 360 px sem scroll horizontal (escuro e claro, CA-35); com reducedMotion nenhuma animação
// ou transição > 0 (CA-32). G2 (só no Chromium): o
// 1.º registo gasta o código e outro browser com o mesmo código ouve «código já usado»; sem sessão, o browser registado entra
// pelo desafio, sem código; sem chaves.json (--revogar), volta ao ecrã do código.
// Sai com 1 se algo falhar, 2 sem Playwright. Capturas em docs/capturas/: conversa-<browser>-<375|1440>-{cartao,resposta}.png.
import { createRequire } from "node:module";
import { execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import tls from "node:tls";
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
const BROWSERS = arg("--browsers", process.env.E2E_BROWSER || "chromium,webkit").split(",");   // E2E_BROWSER: a matriz da CI
const CAPTURAS = !process.argv.includes("--sem-capturas");
const V = JSON.parse(readFileSync(path.join(ROOT, "tests/fixtures/conversa_vectores.json"), "utf-8"));
const porta = () => new Promise((ok) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => ok(p)); }); });
const P1 = await porta(), P2 = await porta(), P3 = await porta();
// duas origens: o Office em 127.0.0.1 e a Conversa em https://localhost (como atrás do Caddy/cloudflared: TLS à frente do
// guardião). O WebKit não guarda cookies «Secure» (o __Host- da sessão) vindos de http://, nem de localhost: sem o TLS, o
// registo gastava o código e a sessão nunca chegava. O certificado é autoassinado, criado aqui (ignoreHTTPSErrors só no e2e).
// GUARDIAO (http directo) só para as verificações do node, que não passam pelo browser.
const OFFICE = `http://127.0.0.1:${P1}/`, CONVERSA = `https://localhost:${P3}/`, GUARDIAO = `http://127.0.0.1:${P2}/`, SITE = "conversa.exemplo.test", OUT = path.join(ROOT, "docs/capturas");
const NOME = "Assistente E2E";
const IPHONE = pw.devices["iPhone 13 Mini"] || { viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 };
const PYTHON = process.env.PYTHON || "python3";
let falhas = 0;
const ok = (cond, msg) => { if (!cond) { falhas++; console.error("FALHA: " + msg); } else console.log("ok: " + msg); return cond; };

// ---- pastas e configuração (como o instalar-conversa.sh deixaria, mas numa pasta temporária)
const tmp = mkdtempSync(path.join(os.tmpdir(), "e2e-conversa-"));
const P = (...x) => path.join(tmp, ...x);
for (const d of ["tasks", "squads", "claude", "fila-dec", "quadro", "aprov", "mini", "bib", "conversa/verificados/audio", "conversa/conversas",
  "conversa/publico", "trabalho", "casa", "run", "opt/chat_host", "opt/web"]) mkdirSync(P(d), { recursive: true });
mkdirSync(P("estado-guardiao"), { mode: 0o700 });
mkdirSync(P("estado-ponte"), { mode: 0o700 });
// o código dos vectores por usar (o 1.º registo apaga o entrada.json): cada browser novo precisa de um
const novoCodigo = () => writeFileSync(P("estado-guardiao", "entrada.json"), JSON.stringify({ v: 1, sal_office: V.sal_office,
  sal_ponte: V.sal_ponte, iteracoes: V.iteracoes, verificador_sha256: V.verificador_sha256, criado: "2026-11-23T10:00:00+00:00" }), { mode: 0o600 });
novoCodigo();
// /opt falso: os ficheiros que o instalador copia e o SHA256SUMS de todos
const MANIFESTO = readFileSync(path.join(ROOT, "static/conversa.manifesto"), "utf-8").split("\n").filter(Boolean);
const copias = [["chat.py", "chat.py"], ["reader.py", "reader.py"], ["chat_host/guardiao.py", "chat_host/guardiao.py"],
  ["static/conversa.manifesto", "conversa.manifesto"], ...MANIFESTO.map((n) => [`static/${n}`, `web/${n}`])];
for (const [de, para] of copias) { mkdirSync(path.dirname(P("opt", para)), { recursive: true }); copyFileSync(path.join(ROOT, de), P("opt", para)); }
writeFileSync(P("opt", "SHA256SUMS"), copias.map(([, para]) => `${createHash("sha256").update(readFileSync(P("opt", para))).digest("hex")}  ${para}\n`).join(""));
writeFileSync(P("trabalho", "LEIA-ME.txt"), "pasta de trabalho do e2e\n");
// o motor falso: diz o que vai fazer, pede o `ls` (cartão), e responde com o resultado
writeFileSync(P("trabalho", "cenario.json"), JSON.stringify({ session_id: "e2e-1", passos: [
  { texto: "Vou ver o que há na pasta de trabalho." },
  { ferramenta: "Bash", input: { command: "ls > visto.txt && cat visto.txt", description: "lista a pasta" } },
  { texto: "Feito: a pasta tem o LEIA-ME.txt." }] }));
writeFileSync(P("office.yaml"), `versao: 1
marca: Squad Office
utilizador:
  nome: Ana
fuso: Europe/Lisbon
squads:
  - id: dev
    nome: Dev
    lead: tech-lead
    tipos: [feat, fix]
    modos: [goal, task]
projectos:
  - id: loja
    nome: Loja
conversa:
  ativa: true
  pasta: ${P("conversa")}
  site: ${SITE}
  tls: externo
`, "utf-8");

const SERVE = `import server; from http.server import ThreadingHTTPServer; ThreadingHTTPServer(("127.0.0.1", ${P1}), server.Handler).serve_forever()`;
const envOffice = { ...process.env, OFFICE_CONFIG: P("office.yaml"), TASKS_DIR: P("tasks"), CLAUDE_PROJECTS_DIR: P("claude"),
  SQUADS_DIR: P("squads"), APROVACOES_DIR: P("aprov"), DECISOES_FILA_DIR: P("fila-dec"), QUADRO_DIR: P("quadro"),
  BIBLIOTECA_DIR: P("bib"), BIBLIOTECA_MINI_DIR: P("mini"), CONVERSA_PUBLICO_DIR: P("conversa/publico"), PORT: String(P1) };
// o guardião só com o ambiente do drop-in da unit (+ CONVERSA_TLS_TESTE, que o instalador nunca põe)
const envGuardiao = { PATH: process.env.PATH, LANG: "C.UTF-8", CONVERSA_PASTA: P("conversa"), CONVERSA_GUARDIAO_ESTADO: P("estado-guardiao"),
  CONVERSA_OPT: P("opt"), CONVERSA_ESCUTA: `127.0.0.1:${P2}`, CONVERSA_FUSO: "Europe/Lisbon", CONVERSA_NOME: NOME, CONVERSA_TLS_TESTE: "1" };
// a ponte só com o ambiente que a unit lhe daria (sem credenciais nem chave) e o `claude` falso
const envPonte = { PATH: process.env.PATH, LANG: "C.UTF-8", HOME: P("casa"), USER: "dono-e2e", XDG_RUNTIME_DIR: P("run"),
  CONVERSA_CLAUDE: path.join(ROOT, "tests/bin/claude"), CONVERSA_CWD: P("trabalho"), CONVERSA_VERIFICADOS_DIR: P("conversa/verificados"),
  CONVERSAS_DIR: P("conversa/conversas"), CONVERSA_ESTADO_DIR: P("estado-ponte"), CONVERSA_FUSO: "Europe/Lisbon" };
const srv = spawn(PYTHON, ["-c", SERVE], { cwd: ROOT, env: envOffice, stdio: "ignore" });
let guardErr = "", ponteErr = "";
const guard = spawn(PYTHON, ["-I", P("opt/chat_host/guardiao.py")], { cwd: P("casa"), env: envGuardiao, stdio: ["ignore", "ignore", "pipe"] });
guard.stderr.on("data", (d) => { guardErr += d; });
// terminador TLS (o papel do Caddy): TCP cru para o guardião, sem mexer nos cabeçalhos (Host e Origin ficam os do browser)
execFileSync("openssl", ["req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1",
  "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost", "-keyout", P("tls.key"), "-out", P("tls.crt")], { stdio: "ignore" });
const terminador = tls.createServer({ key: readFileSync(P("tls.key")), cert: readFileSync(P("tls.crt")) }, (c) => {
  const g = net.connect(P2, "127.0.0.1");
  c.pipe(g).pipe(c);
  c.on("error", () => g.destroy());
  g.on("error", () => c.destroy());
}).listen(P3, "127.0.0.1");
const ponte = spawn(PYTHON, ["-I", path.join(ROOT, "chat_host/ponte.py")], { cwd: P("casa"), env: envPonte, stdio: ["ignore", "ignore", "pipe"] });
ponte.stderr.on("data", (d) => { ponteErr += d; });

async function esperar(cond, msg, ms = 15000) {
  const fim = Date.now() + ms;
  for (;;) {
    try { if (await cond()) return; } catch {}
    if (Date.now() > fim) throw new Error("timeout: " + msg);
    await new Promise((r) => setTimeout(r, 100));
  }
}

// uma página com a consola vigiada (erros, violações da CSP e respostas ≥ 400)
async function pagina(ctx, url) {
  const page = await ctx.newPage(), errs = [];
  // as violações da CSP com o elemento que as causou (a consola só diz «Refused to apply a stylesheet…»)
  await page.addInitScript(() => addEventListener("securitypolicyviolation", (e) => {
    const t = e.target, h = t?.host;   // numa shadow root, o elemento que a tem
    (globalThis.__cspv ||= []).push({ cap: !!globalThis.__captura, d: e.violatedDirective, uri: e.blockedURI, el: t?.tagName || "", no: t?.nodeName || "", host: h?.tagName || "",
      src: `${e.sourceFile || ""}:${e.lineNumber || 0}`, amostra: (e.sample || "").slice(0, 60), html: (h || t)?.outerHTML?.slice(0, 80) || "" });
  }, true));
  page.on("pageerror", (e) => errs.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errs.push(m.text()));
  // o GET dos eventos dá 404 enquanto a ponte ainda não criou a conversa nova (o browser espera, CRIAR_MS): esperado
  page.on("response", (r) => r.status() >= 400 && !(r.status() === 404 && /\/api\/conversa\/eventos$/.test(new URL(r.url()).pathname)) && errs.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`));
  await page.goto(url);
  return { page, errs };
}
const contexto = (browser, perfil, esquema = "light") => browser.newContext({ ...perfil, colorScheme: esquema, reducedMotion: "reduce", ignoreHTTPSErrors: true });

// Violações da CSP que não são da página, no WebKit (o motor do Safari e do Chrome do iPhone; no Chromium não aparecem):
//  - «style-src-attr 'inline'» em qualquer <select> com «style-src 'self'», mesmo num <select></select> estático: o estilo
//    da árvore interna do próprio motor, não um style= da página;
//  - «style-src-elem 'inline'» durante uma captura: no WebKit o Playwright injecta sempre um <style>body {}</style> para
//    sincronizar as animações (shouldToggleStyleSheetToSyncAnimations), sem opção para o desligar.
// Só essas saem da lista, uma a uma (cada uma leva um «Refused to apply a stylesheet…» da consola); qualquer outra
// violação, ou mais avisos de estilo do que essas, continua a contar como erro.
const doMotor = (x) => x.uri === "inline" && ((x.d === "style-src-attr" && x.el === "SELECT") || (x.d === "style-src-elem" && x.cap));
async function semDoMotor(page, errs) {
  const v = await page.evaluate(() => globalThis.__cspv || []).catch(() => []);
  const outras = v.filter((x) => !doMotor(x)).map((x) => `CSP ${x.d} ${x.uri} ${x.no} host=${x.host} ${x.src} «${x.amostra}» ${x.html}`);
  let resto = v.filter(doMotor).length;
  return [...errs.filter((e) => !(/^Refused to apply a stylesheet because/.test(e) && resto-- > 0)), ...outras];
}
// captura marcada na página: as violações dela são do Playwright (acima); a espera deixa chegar os eventos em fila
async function captura(page, ficheiro) {
  if (!CAPTURAS) return;
  await page.evaluate(() => { globalThis.__captura = true; });
  try { await page.screenshot({ path: path.join(OUT, ficheiro) }); await page.waitForTimeout(200); }
  finally { await page.evaluate(() => { globalThis.__captura = false; }); }
}

// a página da Conversa e, se pedir, o código (o mostrado, com hífenes, como o dono o copia)
async function entrar(page) {
  const cod = page.locator("#cp-codigo");
  await page.waitForFunction(() => {
    const f = document.querySelector(".cp-cod"), n = document.querySelector(".cp-nova");
    return (f && !f.hidden) || (n && n.offsetParent);
  });
  if (await cod.isVisible()) {
    await cod.fill(V.codigo_mostrado);
    await page.click(".cp-cod button[type=submit]");
  }
  await page.waitForSelector(".cp-nova", { state: "visible" });
  await page.waitForTimeout(300);
  return percebi(page);
}

// o aviso de tokens (1.ª abertura): devolve o texto do aviso se estava aberto e carrega em «Percebi»
async function percebi(page) {
  const d = page.locator("dialog[open]");
  if (!(await d.isVisible())) return null;
  const t = await d.innerText();
  await d.locator("button[data-r='1']").click();
  await page.waitForSelector("dialog[open]", { state: "detached" });
  return t;
}

const visto = () => existsSync(P("trabalho", "visto.txt"));

async function fluxo(nome, browser, perfil, largura) {
  const ctx = await contexto(browser, perfil);
  const { page: off, errs: errsOff } = await pagina(ctx, OFFICE);
  let page, errs = [];
  try {
    // Office: o botão do cabeçalho é uma ligação de topo para o site da Conversa, com o nome do publico.json
    await off.waitForSelector("#cv-b");
    const b = await off.evaluate(() => { const a = document.querySelector("#cv-b"); return [a.tagName, a.getAttribute("href"), a.target, a.rel]; });
    ok(JSON.stringify(b) === JSON.stringify(["A", `https://${SITE}/`, "_blank", "noopener noreferrer"]), `${nome}: botão do Office abre a origem da Conversa (${b})`);
    await off.waitForFunction((n) => document.querySelector("#cv-b .lg")?.textContent === n, NOME, { timeout: 20000 });
    ok(true, `${nome}: nome do assistente no botão do Office (publico.json)`);
    ok(!(await off.evaluate(() => [...document.scripts].some((s) => /conversa\.js|conversa_chave/.test(s.src)) || !!document.querySelector("#cv-painel"))),
      `${nome}: o Office não carrega a UI da Conversa`);

    novoCodigo();
    ({ page, errs } = await pagina(ctx, CONVERSA));
    const aviso = await entrar(page);
    if (aviso !== null) ok(/tokens/.test(aviso), `${nome}: aviso de tokens antes do 1.º envio`);
    // QA B3: «Nova conversa» e logo a pergunta, sem esperar pela ponte; o «Enviar» espera que a conversa exista
    rmSync(P("trabalho", "visto.txt"), { force: true });
    await page.click(".cp-nova");
    await page.waitForSelector("#cv-txt", { state: "visible" });
    await page.fill("#cv-txt", "O que há na pasta de trabalho?");
    await page.click(".cv-env");
    const dlg = page.locator("dialog[open] button[data-r='1']");
    const card = page.locator(".cp-act:not([hidden]) button", { hasText: /^Aprovar$/ });
    await Promise.race([dlg.waitFor({ timeout: 20000 }).catch(() => {}), card.waitFor({ timeout: 20000 }).catch(() => {})]);
    const aviso2 = await percebi(page);
    if (aviso2 !== null) {
      ok(/tokens/.test(aviso2), `${nome}: aviso de tokens antes do 1.º envio`);
      if (await page.inputValue("#cv-txt")) await page.click(".cv-env");
    }
    await card.waitFor({ timeout: 25000 });
    ok(!(await page.locator(".cv-fm").innerText()).trim(), `${nome}: B3 sem erro ao enviar logo a seguir à «Nova conversa»`);
    await page.waitForSelector(".cp-act button.ok-b:not([disabled])");   // 600 ms inactivo (D3) antes da captura
    ok(!visto(), `${nome}: o ls não correu antes do clique`);
    const alvo = await page.locator(".cp-act").innerText();
    ok(alvo.includes("ls > visto.txt"), `${nome}: o cartão mostra o comando`);
    ok(/Aprovar executa agora: /.test(alvo), `${nome}: ADR-024: o cartão diz «Aprovar executa agora: <acção>»`);
    ok(await page.locator('.cp-act button.ok-b[aria-describedby^="cv-ap-"]').count() === 1, `${nome}: ADR-024: o «Aprovar» descrito pelo aviso (leitor de ecrã)`);

    // S2: com o cartão pendente, o contador do Office passa a 1; a origem do Office não tem a chave nem consegue responder
    await off.waitForFunction(() => document.querySelector("#cv-b .cv-n")?.textContent === "1", null, { timeout: 25000 });
    ok(true, `${nome}: contador do botão do Office com 1 pedido à espera`);
    const s2 = await off.evaluate(async (base) => {
      const bd = await new Promise((r) => { const q = indexedDB.open("office-conversa"); q.onupgradeneeded = () => q.result.createObjectStore("chaves"); q.onsuccess = () => r(q.result); q.onerror = () => r(null); });
      let chaves = -1;
      if (bd) { chaves = await new Promise((r) => { const q = bd.transaction("chaves").objectStore("chaves").count(); q.onsuccess = () => r(q.result); q.onerror = () => r(-1); }); bd.close(); }
      const tenta = async (init) => { try { return (await fetch(base + "api/conversa/pedido", { method: "POST", credentials: "include", ...init })).status; } catch { return "bloqueado"; } };
      const corpo = JSON.stringify({ acao: "responder_permissao", escolha: "uma_vez" });
      return { chaves, json: await tenta({ headers: { "Content-Type": "application/json", "X-Csrf": "x" }, body: corpo }),
        simples: await tenta({ headers: { "Content-Type": "text/plain" }, body: corpo }) };
    }, CONVERSA);
    ok(s2.chaves === 0, `${nome}: IndexedDB «office-conversa» vazio na origem do Office (${s2.chaves})`);
    ok([s2.json, s2.simples].every((x) => x === "bloqueado" || x === 403), `${nome}: fetch da origem do Office para a Conversa bloqueado ou 403 (${s2.json}, ${s2.simples})`);
    await page.waitForTimeout(2500);   // mais que uma volta da ponte
    ok(!visto() && (await card.isVisible()), `${nome}: o cartão continua pendente depois das tentativas da origem do Office`);

    if (largura === 375) {
      const pequenos = await page.evaluate(() => [...document.querySelectorAll("#cv-painel :is(button, a[href], select, summary, [role=button])")]
        .filter((e) => e.offsetParent !== null && getComputedStyle(e).visibility !== "hidden")
        .map((e) => [e.textContent.trim().slice(0, 30) || e.getAttribute("aria-label") || e.className, e.getBoundingClientRect()])
        .filter(([, r]) => r.width > 0 && (r.width < 43.5 || r.height < 43.5))
        .map(([n, r]) => `${n} ${Math.round(r.width)}×${Math.round(r.height)}`));
      ok(!pequenos.length, `${nome}: alvos ≥ 44 px a 375 (pequenos: ${pequenos.join("; ")})`);
    }
    const mov = await page.evaluate(() => [...document.querySelectorAll("#cv-painel, #cv-painel *")].filter((e) => {
      const s = getComputedStyle(e), d = (v) => v.split(",").some((x) => parseFloat(x) > 0);
      return (s.animationName !== "none" && d(s.animationDuration)) || d(s.transitionDuration);
    }).map((e) => e.className || e.tagName));
    ok(!mov.length, `${nome}: reducedMotion sem animações nem transições (${mov.slice(0, 5).join(", ")})`);
    await captura(page, `conversa-${nome}-cartao.png`);
    await card.click();
    await page.waitForFunction(() => /Feito: a pasta tem o LEIA-ME/.test(document.querySelector(".cv-ev")?.textContent || ""), null, { timeout: 20000 });
    ok(visto(), `${nome}: o ls correu depois do clique`);
    ok(await page.locator(".cp-act").isHidden(), `${nome}: o cartão saiu depois de aprovar`);
    await captura(page, `conversa-${nome}-resposta.png`);
    const errsC = await semDoMotor(page, errs);
    ok(!errsC.length, `${nome}: sem erros na consola da Conversa, nem violações da CSP (${errsC.join(" | ")})`);
    // na consola do Office só os bloqueios de CORS das duas tentativas acima (o guardião não responde a outras origens).
    // O Chromium escreve 1 linha por bloqueio; o WebKit 2 (a razão — preflight 501 ou origem recusada com 403 — e o «cannot load»)
    const cors = [/api\/conversa\/pedido' from origin .* blocked by CORS policy/, /^Fetch API cannot load .*\/api\/conversa\/pedido due to access control checks/,
      /^Preflight response is not successful\. Status code: 501$/, /^Origin \S+ is not allowed by Access-Control-Allow-Origin\. Status code: 403$/];
    const outros = errsOff.filter((e) => !cors.some((re) => re.test(e)));
    const max = browser.browserType().name() === "webkit" ? 4 : 2;
    ok(!outros.length && errsOff.length <= max, `${nome}: sem erros na consola do Office além dos 2 bloqueios de CORS (${outros.join(" | ")})`);
  } catch (e) {
    const diag = page ? await page.evaluate(() => document.querySelector("#cv-painel")?.innerText).catch(() => "") : "";
    console.error(`diagnóstico ${nome}:\n${diag}\nconsola: ${errs.join(" | ")} | Office: ${errsOff.join(" | ")}`);
    throw e;
  } finally { await ctx.close(); }
}

async function semScroll360(browser) {
  for (const esquema of ["dark", "light"]) {
    const ctx = await contexto(browser, { viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true }, esquema);
    try {
      novoCodigo();
      const { page } = await pagina(ctx, CONVERSA);
      await entrar(page);
      await page.click(".cv-l li button").catch(() => {});   // uma conversa aberta, se houver
      const sem = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
      ok(sem, `360 px, esquema ${esquema}: sem scroll horizontal na página da Conversa`);
    } finally { await ctx.close(); }
  }
}

// G2: código de uso único, entrada por desafio e revogação
async function g2(browser) {
  const perfil = { viewport: { width: 1440, height: 900 } }, a = await contexto(browser, perfil), b = await contexto(browser, perfil);
  const msg = (pg) => pg.evaluate(() => document.querySelector(".cp-ent-m")?.textContent || "");
  try {
    novoCodigo();
    const { page: pb } = await pagina(b, CONVERSA);
    await pb.waitForSelector("#cp-codigo", { state: "visible" });
    ok(await pb.getAttribute("#cp-codigo", "autocomplete") === "one-time-code", "G2: o campo do código é one-time-code");
    const { page: pa } = await pagina(a, CONVERSA);
    await entrar(pa);
    ok(!existsSync(P("estado-guardiao", "entrada.json")) && existsSync(P("estado-guardiao", "chaves.json")), "G2: o 1.º registo gasta o código e grava a chave do browser");
    await pb.fill("#cp-codigo", V.codigo_mostrado);
    await pb.click(".cp-cod button[type=submit]");
    await pb.waitForFunction(() => /código já usado/.test(document.querySelector(".cp-ent-m")?.textContent || ""), null, { timeout: 15000 });
    ok(true, `G2: outro browser com o mesmo código ouve «${await msg(pb)}»`);
    await a.clearCookies();
    await pa.reload();
    await pa.waitForSelector(".cp-nova", { state: "visible" });
    ok(await pa.locator(".cp-cod").isHidden(), "G2: sem sessão, o browser registado entra pelo desafio (sem código)");
    rmSync(P("estado-guardiao", "chaves.json"));   // --revogar
    await a.clearCookies();
    await pa.reload();
    await pa.waitForFunction(() => /não há código por usar/.test(document.querySelector(".cp-ent-m")?.textContent || ""), null, { timeout: 15000 });
    ok(true, "G2: revogado, o browser volta ao ecrã do código");
  } finally { await a.close(); await b.close(); }
}

try {
  await esperar(async () => (await fetch(OFFICE + "healthz")).ok, "server.py em /healthz");
  await esperar(async () => (await fetch(GUARDIAO)).ok, "guardião em /", 15000).catch((e) => { throw new Error(`${e.message}\n${guardErr.slice(-1500)}`); });
  await esperar(async () => existsSync(P("conversa/conversas/estado.json")), "pulsação da ponte (estado.json)");
  const r = await fetch(GUARDIAO), csp = r.headers.get("content-security-policy") || "";
  ok(/script-src 'self'/.test(csp) && /style-src 'self'/.test(csp) && /frame-ancestors 'none'/.test(csp) && !/unsafe-inline/.test(csp), `CSP da página da Conversa (${csp})`);
  ok((await r.text()).includes('src="conversa_inicio.js"'), "GET / do guardião dá o conversa.html");
  ok((await fetch(OFFICE + "api/conversa/estado")).status === 404, "o Office não tem as rotas da Conversa (404)");
  ok((await fetch(GUARDIAO + "index.html")).status === 404, "o guardião não serve o Office (404)");
  const est = await (await fetch(GUARDIAO + "api/conversa/estado")).json();
  ok(est.tls === true && est.ponte !== false, `estado da Conversa com TLS de teste e ponte viva (${JSON.stringify(est).slice(0, 120)})`);
  if (CAPTURAS) mkdirSync(OUT, { recursive: true });
  for (const b of BROWSERS) {
    let browser;
    try { browser = await pw[b].launch(); } catch (e) {
      ok(false, `${b} não arrancou: ${String(e).split("\n").find((l) => /missing|error/i.test(l)) || e}`);
      continue;
    }
    try {
      await fluxo(`${b}-375`, browser, IPHONE, 375);
      await fluxo(`${b}-1440`, browser, { viewport: { width: 1440, height: 900 } }, 1440);
      if (b === "chromium") { await semScroll360(browser); await g2(browser); }
    } catch (e) { ok(false, `${b}: ${e.message}`); } finally { await browser.close(); }
  }
} catch (e) {
  ok(false, e.message);
} finally {
  terminador.close();
  ponte.kill("SIGTERM");
  guard.kill("SIGTERM");
  srv.kill();
  await new Promise((r) => setTimeout(r, 300));
  if (falhas && guardErr) console.error("stderr do guardião:\n" + guardErr.slice(-2000));
  if (falhas && ponteErr) console.error("stderr da ponte:\n" + ponteErr.slice(-2000));
  rmSync(tmp, { recursive: true, force: true });
}
// 0.6.1 (ADR-024): o e2e das Aprovações que lançam corre aqui, no mesmo browser, até o job da CI ter um passo próprio
// (docs/propostas-ci/ci-aprovacoes-lanca.patch, que põe E2E_APROVACOES=0 neste passo)
if (process.env.E2E_APROVACOES !== "0" && existsSync(path.join(ROOT, "tools/e2e_aprovacoes.mjs"))) {
  const args = [path.join(ROOT, "tools/e2e_aprovacoes.mjs"), "--browsers", BROWSERS.join(","), ...(CAPTURAS ? [] : ["--sem-capturas"])];
  try { execFileSync(process.execPath, args, { stdio: "inherit", env: process.env }); } catch { ok(false, "e2e das Aprovações que lançam (tools/e2e_aprovacoes.mjs)"); }
}
console.log(falhas ? `${falhas} falha(s)` : "tudo ok");
process.exit(falhas ? 1 : 0);
