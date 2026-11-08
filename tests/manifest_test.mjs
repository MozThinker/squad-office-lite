// Manifest, ícones, <head> iOS e peso do index.html (SPEC AC-22, AC-23, AC-27): node --test tests/manifest_test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { execFileSync } from "node:child_process";

const STATIC = new URL("../static/", import.meta.url);
const ler = (f) => readFileSync(new URL(f, STATIC));
const HTML = ler("index.html").toString("utf8");

// largura × altura pelo IHDR (sem dependências)
function medidas(buf) {
  assert.deepEqual([...buf.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], "não é PNG");
  assert.equal(buf.subarray(12, 16).toString("latin1"), "IHDR");
  return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

test("AC-22: manifest JSON válido com standalone, id, scope e ícones 192/512 any + maskable que existem", () => {
  const m = JSON.parse(ler("manifest.json"));
  assert.equal(m.display, "standalone");
  assert.equal(m.id, "./");
  assert.equal(m.scope, "./");
  assert.equal(m.start_url, "./");
  for (const purpose of ["any", "maskable"]) for (const lado of [192, 512]) {
    const i = m.icons.find((x) => x.purpose === purpose && x.sizes === `${lado}x${lado}`);
    assert.ok(i, `falta ${purpose} ${lado}`);
    assert.equal(i.type, "image/png");
    assert.deepEqual(medidas(ler(i.src)), [lado, lado], i.src);
  }
});

test("AC-a7: nomes genéricos (manifest estático, <title>, <h1> e iOS dizem «Squad Office»; a marca da config vem depois)", () => {
  const m = JSON.parse(ler("manifest.json"));
  assert.equal(m.name, "Squad Office Lite");
  assert.equal(m.short_name, "Squad Office");
  assert.match(HTML, /<title>Squad Office<\/title>/);
  assert.match(HTML, /<meta name="apple-mobile-web-app-title" content="Squad Office">/);
  assert.match(HTML, /<h1>▣ Squad Office<\/h1>/);
});

test("AC-22: apple-touch-icon 180 no <head> e metas do iOS", () => {
  const head = HTML.slice(0, HTML.indexOf("</head>"));
  const href = head.match(/<link rel="apple-touch-icon" href="([^"]+)">/)?.[1];
  assert.ok(href, "sem apple-touch-icon");
  assert.deepEqual(medidas(ler(href)), [180, 180]);
  for (const n of ["apple-mobile-web-app-capable", "apple-mobile-web-app-title", "apple-mobile-web-app-status-bar-style"])
    assert.match(head, new RegExp(`<meta name="${n}" content="[^"]+">`), n);
});

test("AC-23: definicoes.* só entram por import() e o index.html cresce ≤ 4 KB face ao origin/main", (t) => {
  assert.doesNotMatch(HTML, /<(script|link)[^>]+definicoes/);
  assert.doesNotMatch(HTML, /["']definicoes\.css/);
  assert.match(HTML, /import\("\.\/definicoes\.js"\)/);
  let base;
  try { base = execFileSync("git", ["show", "origin/main:static/index.html"], { cwd: new URL("..", import.meta.url), stdio: ["ignore", "pipe", "ignore"] }); }
  catch { return t.skip("sem origin/main no git"); }
  const d = Buffer.byteLength(HTML) - base.length;
  assert.ok(d <= 4096, `index.html cresceu ${d} bytes`);
});

test("AC-27: nenhum ficheiro servido menciona PUSH_DIR nem /data/push", () => {
  for (const f of readdirSync(STATIC)) {
    if (f.endsWith(".png")) continue;
    const s = ler(f).toString("utf8");
    assert.ok(!s.includes("PUSH_DIR") && !s.includes("/data/push"), f);
  }
});
