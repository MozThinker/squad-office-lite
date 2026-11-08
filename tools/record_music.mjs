// Gravações da música de fundo (docs/audio/*.wav) e capturas do botão no header (docs/capturas/musica-*.png).
// Usa o MESMO static/music.js, servido pelo server.py real, num OfflineAudioContext do Chromium headless.
//
// Como correr (Playwright fica fora do repo; não há package.json):
//   npm i --prefix /tmp/pw-music playwright@1.64.1      # usa o chromium-1248 já instalado
//   source ~/.local/pwlibs/env.sh                        # neste servidor: LD_LIBRARY_PATH e fontes (emoji) do Chromium
//   PLAYWRIGHT_DIR=/tmp/pw-music node tools/record_music.mjs [--audio] [--capturas]   # sem flags: as duas
//
// WAV PCM 16-bit, mono, 22 050 Hz, 20 s, volume 100 (pior caso para clipping). Sai com código 1 se algum
// pico ≥ 0,99 (gravações e, sem gravar, cada estilo com 4 camadas + jingle) ou se a 360 px houver scroll horizontal.
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
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
const args = process.argv.slice(2), all = !args.length;
const SR = 22050, SECS = 20, PORT = 8097, BASE = `http://127.0.0.1:${PORT}/`;
const RECORDINGS = [
  { file: "dev-parada", room: "dev", layers: 0 },
  { file: "dev-2-agentes", room: "dev", layers: 2, jingleAt: 15 },
  { file: "piano-parada", room: null, layers: 0 },   // sem sala → piano (qualquer sala é lofi)
];
const STRESS = ["dev", null].map((room) => ({ room, layers: 4, jingleAt: 10 }));

function wav(pcm) { // cabeçalho RIFF de 44 bytes + dados PCM 16-bit mono
  const h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVE", 8);
  h.write("fmt ", 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(SR, 24); h.writeUInt32LE(SR * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
  h.write("data", 36); h.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([h, pcm]);
}

// corre no browser: motor real num OfflineAudioContext → pico, RMS e (opcional) PCM 16-bit em base64
async function render({ room, layers, jingleAt, sr, secs, pcm }) {
  const m = await import("/music.js");
  const ctx = new OfflineAudioContext(1, sr * secs, sr);
  const e = m.createEngine({ ctx });
  e.setVolume(100); e.setRoom(room); e.setLayers(layers); e.start();
  if (jingleAt) e.jingle(jingleAt);
  e.renderUntil(secs);
  const d = (await ctx.startRendering()).getChannelData(0);
  let peak = 0, sq = 0;
  for (const x of d) { peak = Math.max(peak, Math.abs(x)); sq += x * x; }
  let b64 = "";
  if (pcm) {
    const i16 = new Int16Array(d.length);
    for (let i = 0; i < d.length; i++) i16[i] = Math.max(-1, Math.min(1, d[i])) * 32767;
    const u8 = new Uint8Array(i16.buffer);
    for (let i = 0; i < u8.length; i += 32768) b64 += String.fromCharCode(...u8.subarray(i, i + 32768));
    b64 = btoa(b64);
  }
  return { peak, rms: Math.sqrt(sq / d.length), b64 };
}

async function audio(browser) {
  const page = await browser.newPage();
  await page.goto(BASE);
  mkdirSync(path.join(ROOT, "docs/audio"), { recursive: true });
  let ok = true;
  const db = (x) => (20 * Math.log10(x)).toFixed(1) + " dBFS";
  for (const c of RECORDINGS) {
    const r = await page.evaluate(render, { ...c, sr: SR, secs: SECS, pcm: true });
    const buf = wav(Buffer.from(r.b64, "base64")), out = path.join(ROOT, "docs/audio", c.file + ".wav");
    writeFileSync(out, buf);
    ok &&= r.peak < 0.99;
    console.log(`${c.file}.wav  ${buf.length} B  pico ${r.peak.toFixed(3)} (${db(r.peak)})  RMS ${r.rms.toFixed(4)} (${db(r.rms)})`);
  }
  for (const c of STRESS) { // pior caso, não se grava
    const r = await page.evaluate(render, { ...c, sr: SR, secs: SECS, pcm: false });
    ok &&= r.peak < 0.99;
    console.log(`[4 camadas + jingle] ${c.room}  pico ${r.peak.toFixed(3)} (${db(r.peak)})  RMS ${r.rms.toFixed(4)} (${db(r.rms)})`);
  }
  if (!ok) { console.error("FALHA: pico ≥ 0,99 (clipping)"); process.exitCode = 1; }
  await page.close();
}

const agent = (role, desc, active) => ({ role, desc, active, finished: !active, started: "2026-10-24T15:05:00" });
const task = (o) => ({ finished: null, last_activity: "2026-10-24T15:29:50", phases: { SPEC: false, PLAN: false, QA: false },
  auto_resumes: 0, issues: [], branch: null, pr: null, ...o });
const STATE = {
  now: "2026-10-24T15:30:00", tasks: [
    task({ id: "20261024-1500-joga-ranking", started: "2026-10-24T15:00:00", squad: "dev", project: "joga", mode: "goal",
      status: "a correr", running: true, working: ["tech-lead", "backend-engineer"], branch: "feat/ranking",
      phases: { SPEC: true, PLAN: true, QA: false },
      agents: [agent("architect", "plano do ranking", false), agent("backend-engineer", "endpoint /ranking", true)] }),
    task({ id: "diario", started: "2026-10-24T07:30:00", squad: "dev", project: "exemplo", mode: "task",
      status: "concluída", running: false, working: [], finished: "2026-10-24T07:45:00", agents: [agent("analista", "números do dia", false)] }),
  ],
};

async function capturas(browser) {
  mkdirSync(path.join(ROOT, "docs/capturas"), { recursive: true });
  for (const [name, width, height] of [["desktop", 1280, 800], ["360", 360, 740]]) {
    // contexto novo: localStorage limpo (música desligada). O 🔔 mostra «bloqueadas no browser»: o headless nega notificações
    const ctx = await browser.newContext({ viewport: { width, height } });
    const page = await ctx.newPage(), errs = [];
    page.on("pageerror", (e) => errs.push(String(e)));
    await page.route("**/api/state", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify(STATE) }));
    await page.goto(BASE);
    await page.waitForFunction(() => document.querySelector("#live span").textContent.startsWith("ao vivo"));
    const shot = async (state) => {
      const sw = await page.evaluate(() => document.documentElement.scrollWidth);
      if (sw > width) { console.error(`FALHA: ${name} ${state}: scrollWidth ${sw} > ${width}`); process.exitCode = 1; }
      const file = path.join(ROOT, "docs/capturas", `musica-${name}-${state}.png`);
      await page.locator("header").screenshot({ path: file });
      console.log(`musica-${name}-${state}.png  scrollWidth ${sw}`);
    };
    await shot("off");
    await page.click("#music");
    await page.waitForSelector("#vol:visible");
    await page.waitForFunction(() => document.querySelector("#music").getAttribute("aria-pressed") === "true");
    await shot("on");
    if (errs.length) { console.error("FALHA: erros na página", errs); process.exitCode = 1; }
    await ctx.close();
  }
}

const tmp = mkdtempSync(path.join(os.tmpdir(), "record-music-"));
// o server.py faz bind em 0.0.0.0; aqui só em 127.0.0.1 (o mesmo Handler, sem expor os dados à rede)
const SERVE = `import server; from http.server import ThreadingHTTPServer; ThreadingHTTPServer(("127.0.0.1", ${PORT}), server.Handler).serve_forever()`;
const srv = spawn("python3", ["-c", SERVE], { cwd: ROOT, env: { ...process.env, TASKS_DIR: tmp, CLAUDE_PROJECTS_DIR: tmp, SQUADS_DIR: tmp, OFFICE_CONFIG: path.join(ROOT, "office.example.yaml"), PORT: String(PORT) }, stdio: "ignore" });
let browser;
try {
  for (let i = 0; ; i++) {
    try { if ((await fetch(BASE + "healthz")).ok) break; } catch {}
    if (i > 50) throw new Error("server.py não respondeu em /healthz");
    await new Promise((r) => setTimeout(r, 100));
  }
  browser = await chromium.launch();
  if (all || args.includes("--audio")) await audio(browser);
  if (all || args.includes("--capturas")) await capturas(browser);
} finally {
  await browser?.close();
  srv.kill();
}
