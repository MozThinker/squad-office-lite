// Verificações em browser e capturas dos temas e das Aprovações recolhíveis (SPEC office-aprovacoes-fundo-temas, CA29).
// Sobe o server.py real em 127.0.0.1 com dados FALSOS numa pasta temporária (nunca a fila/quadro/biblioteca reais);
// as execuções do escritório (/api/state → tasks) são injectadas no browser, como no record_music.mjs.
//
// Como correr (Playwright fica fora do repo; não há package.json):
//   npm i --prefix /tmp/pw-music playwright@1.64.1      # usa o chromium-1248 já instalado
//   source ~/.local/pwlibs/env.sh                        # neste servidor: LD_LIBRARY_PATH e fontes (emoji) do Chromium
//   PLAYWRIGHT_DIR=/tmp/pw-music node tools/capturas_temas.mjs [--sem-capturas]
//
// Verifica (sai com código 1 se algo falhar): CA26 sem scroll horizontal a 360/1440 × 3 vistas × 5 temas e o selector na
// 2.ª linha a 360; CA22 tema sobrevive a reload; CA15/CA24 auto segue o sistema; CA23 1.º frame já com o tema (CPU 6×);
// CA25 Tab até ao selector, setas mudam o tema, foco visível, 44 px; CA8/CA11 o poll não mexe na secção, no pedido aberto,
// no texto, no cursor nem no foco; CA6/CA7/CA10 estado reposto depois de reload; CA12 «Ver em Aprovações» (Biblioteca) e
// «Abrir aprovação» (Quadro) com a secção recolhida; erros na consola.
// Capturas em docs/capturas/: tema-<t>-escritorio-{1440,360}.png, tema-<t>-{quadro,biblioteca}-1440.png,
// aprovacoes-360-{recolhida,aberta,pedido}.png, aprovacoes-1440-pedido.png.
// Push e progresso (SPEC office-push-progresso AC-23/25/26): o `progress` e as rotas api/push/* são falsos (injectados no browser,
// o PushManager também): definicoes.* só pedidos ao abrir; «Definições» a 360 px sem scroll, letra ≥ 16 px e alvos ≥ 44 px nos
// 5 temas; Esc fecha e devolve o foco; as prefs da barra repintam logo; «Progresso por fase» (office-push-barra-fases CA24/25):
// fases desactivadas até ligar o evento, QA/PR ao ligar, ligado nas capturas claro/escuro. Capturas: definicoes-<t>-360.png,
// progresso-{dev,notas}-<t>-1440.png e progresso-quadro-1440.png. Config (office.yaml) gerada na pasta temporária: squads dev
// (worktree) e notas (sessão), 8 projectos (paleta c0…c7 completa) e duas fontes da Biblioteca.
import { createRequire } from "node:module";
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
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
// porta livre escolhida pelo SO (uma porta fixa pode estar ocupada por outro server.py e o /healthz responderia na mesma)
const PORT = await new Promise((ok) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => ok(p)); }); });
const BASE = `http://127.0.0.1:${PORT}/`, OUT = path.join(ROOT, "docs/capturas");
const TEMAS = ["auto", "escuro", "claro", "sepia", "contraste"];
const VISTAS = [["escritorio", ""], ["quadro", "#quadro"], ["biblioteca", "#biblioteca"]];
let falhas = 0;
const ok = (cond, msg) => { if (!cond) { falhas++; console.error("FALHA: " + msg); } return cond; };

// ---- dados falsos (pasta temporária) ----
const DADOS = String.raw`
import json, os, struct, sys, zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tests.test_aprovacoes import pedido
root = Path(sys.argv[1])
for d in ("tasks", "squads/notas", "claude", "fila", "quadro", "aprov", "mini",
          "bib/material/media/2026-11-04-status-ebook/final", "bib/relatorios"):
    (root / d).mkdir(parents=True, exist_ok=True)
(root / "office.yaml").write_text("""versao: 1
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
  - id: menuqr
    nome: MenuQR
  - id: joga
    nome: Joga
  - id: ebook
    nome: E-book
  - id: projeto90
    nome: Projeto 90
  - id: loja
    nome: Loja
  - id: estudos
    nome: Estudos
  - id: tictag
    nome: TicTag
  - id: office
    nome: Office
biblioteca:
  - id: material
    rotulo: Material
    pasta: material
    media: true
  - id: relatorios
    rotulo: Relatórios
    pasta: relatorios
    tipo: relatorios
""", encoding="utf-8")
hoje = date.today()
LOTE = "material/media/2026-11-04-status-ebook"
P = [("Verba da semana 2", 1, ["- [P1] verba 145 € — subir o tecto", "- [P2] manter o público"]),
     ("Estado do WhatsApp: 6 peças do e-book", 2, ["- [P1] publicar as 6 peças", "- [P2] texto do convite"]),
     ("Calendário orgânico de Novembro", 5, ["- [P1] aprovar o calendário"]),
     ("Anúncio novo: criativo", 0, ["- [P1] aprovar o criativo", "- [P2] verba 60 €", "- [P3] público 25-45"]),
     ("Loja: preço do plano Pro", 3, ["- [P1] 29 €/mês"]),
     ("MenuQR: piloto num restaurante", 8, ["- [P1] data de arranque", "- [P2] ementa em 3 línguas"]),
     ("Joga: torneio de Dezembro", 1, ["- [P1] prémio do torneio"]),
     ("Relatório semanal: corte de anúncios", 4, ["- [P1] pausar 2 conjuntos"]),
     ("Office: limpeza de ficheiros antigos", 12, ["- [P1] apagar 3 pastas"])]
for i, (titulo, idade, decs) in enumerate(P):   # uma só fonte de aprovações: «geral»
    extra = f"\nPeças: {LOTE}/final/status-01-quem-9x16.png\n" if i == 1 else ""
    txt = pedido(criado=(hoje - timedelta(days=idade)).isoformat(), titulo=f"# {titulo}\n\n", decisoes=decs, extra=extra)
    (root / "aprov" / f"p{i}.md").write_text(txt, encoding="utf-8")

def png(path, w, h, c1, c2):   # PNG RGB com faixas (sem dependências)
    rows = b"".join(b"\0" + b"".join(bytes(c1 if (x // 30 + y // 30) % 2 else c2) for x in range(w)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))
fin = root / "bib" / LOTE / "final"
png(fin / "status-01-quem-9x16.png", 180, 320, (255, 204, 128), (66, 165, 245))
png(fin / "status-02-preco-9x16.png", 180, 320, (102, 187, 106), (38, 50, 56))
(root / "bib" / LOTE / "folha.md").write_text("# Folha: Estado do WhatsApp do e-book (2 peças)\n\nDuas peças 9:16.\n", encoding="utf-8")
(root / "bib/relatorios/2026-11-04-semana.md").write_text("# Relatório da semana\n\nVendas e anúncios da semana.\n", encoding="utf-8")

agora = datetime.now(timezone.utc).astimezone()
iso = lambda dias=0, horas=0: (agora - timedelta(days=dias, hours=horas)).isoformat(timespec="seconds")
def meta(tid, **kw):
    m = {"id": tid, "project": "loja", "mode": "goal", "status": "concluída", "pr": "", "started_at": iso(1),
         "finished_at": iso(1, -1)}
    m.update(kw)
    (root / "tasks" / tid).mkdir()
    (root / "tasks" / tid / "meta.json").write_text(json.dumps(m))
meta("20261104-0900-loja-lembretes", project="loja", status="a correr", finished_at=None)
meta("20261103-1000-joga-ranking", project="joga", pr="https://github.com/exemplo-org/joga/pull/7")
C = [("c1", "Ementa com fotos no MenuQR", "menuqr", "ideias", ["ux"]), ("c2", "Ranking semanal no Joga", "joga", "a-fazer", []),
     ("c3", "Landing Angola: testes A/B", "ebook", "em-curso", ["anúncios"]), ("c4", "Preço do Projeto 90", "projeto90", "a-espera", []),
     ("c5", "Lembretes por WhatsApp", "loja", "feito", []), ("c6", "Estudo: agentes de voz", "estudos", "ideias", ["r&d"]),
     ("c7", "Tema escuro do TicTag", "tictag", "a-fazer", []), ("c8", "Limpeza do Office", "office", "em-curso", [])]
cart = [{"id": i, "titulo": t, "descricao": "Descrição curta do cartão.", "projecto": p, "coluna": col, "ordem": n,
         "etiquetas": e, "criado": iso(2), "actualizado": iso(0, 3)} for n, (i, t, p, col, e) in enumerate(C)]
(root / "quadro" / "cartoes.json").write_text(json.dumps({"v": 1, "cartoes": cart}, ensure_ascii=False))
`;

// progress falso (forma da SPEC §4) até o servidor o calcular
const FASES = [["SPEC", "feito"], ["PLAN", "feito"], ["CODE", "agora"], ["QA", ""], ["PR", ""]].map(([nome, estado]) => ({ nome, estado }));
const PROG_DEV = { fases: FASES, decorrido_s: 1500, tipico_s: 3600, amostra: 12, pct: 41, faltam_s: 2100, atrasada: false };
const PROG_NOTAS = { fases: null, decorrido_s: 720, tipico_s: 600, amostra: 9, pct: null, faltam_s: null, atrasada: true };

// execuções falsas do escritório (como no record_music.mjs), com horas relativas ao «now» do servidor
function tarefas(now) {
  const t0 = new Date(now).getTime(), at = (min) => new Date(t0 - min * 60000).toISOString();
  const agent = (role, desc, active) => ({ role, desc, active, finished: !active, started: at(25) });
  const task = (o) => ({ finished: null, last_activity: at(0.2), phases: { SPEC: false, PLAN: false, QA: false },
    auto_resumes: 0, issues: [], branch: null, pr: null, ...o });
  return [
    task({ id: "20261105-1945-office-aprovacoes-fundo-temas", started: at(30), squad: "dev", project: "office", mode: "goal",
      status: "a correr", running: true, working: ["tech-lead", "frontend-engineer", "test-engineer"], branch: "feat/office-aprovacoes-fundo-temas",
      phases: { SPEC: true, PLAN: true, QA: false },
      agents: [agent("architect", "plano dos temas", false), agent("frontend-engineer", "tokens e selector", true),
        agent("test-engineer", "capturas e contraste", true)], progress: PROG_DEV }),
    task({ id: "20261105-2100-notas-semana", started: at(12), squad: "notas", project: "Notas", mode: "relatorio",
      status: "a correr", running: true, working: ["cmo"], agents: [agent("cmo", "relatório do dia", true)], progress: PROG_NOTAS }),
    task({ id: "notas-diario", started: at(600), squad: "notas", project: "Notas", mode: "diario",
      status: "concluída", running: false, working: [], finished: at(580), agents: [agent("analista", "números do dia", false)] }),
  ];
}

async function novaPagina(browser, { width, height = 900, tema, scheme = "dark", storage = {} }) {
  const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, deviceScaleFactor: 1 });
  await ctx.addInitScript(([s]) => { if (!sessionStorage.getItem("__init")) { sessionStorage.setItem("__init", "1");
    for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v); } }, [{ ...storage, ...(tema ? { tema } : {}) }]);
  const page = await ctx.newPage(), errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errs.push(m.text()));
  await page.route("**/api/state", async (r) => {
    const res = await r.fetch(), j = await res.json();
    j.tasks = tarefas(j.now);
    await r.fulfill({ response: res, json: j });
  });
  await page.route("**/api/quadro", async (r) => {
    if (r.request().method() !== "GET") return r.continue();
    const res = await r.fetch(), j = await res.json();
    for (const c of j.cartoes) c.progress = c.links?.tarefa === "20261104-0900-loja-lembretes" ? PROG_DEV : null; // a única a correr
    await r.fulfill({ response: res, json: j });
  });
  await page.goto(BASE);
  await page.waitForFunction(() => document.querySelector("#live span").textContent.startsWith("ao vivo"));
  await page.waitForSelector("#aprovacoes:not([hidden])");
  return { ctx, page, errs };
}

async function irPara(page, hash) {
  await page.evaluate((h) => { location.hash = h; }, hash);
  if (hash === "#quadro") await page.waitForSelector("#quadro .q-board .q-card[data-id]");
  else if (hash === "#biblioteca") await page.waitForSelector("#bib .bib-card, #bib li", { timeout: 10000 });
  else await page.waitForSelector("#office svg, #office *");
  await page.waitForTimeout(250);
}

const sw = (page) => page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);

// ---- CA26 + capturas por tema ----
async function temas(browser) {
  for (const width of [1440, 360]) {
    for (const tema of TEMAS) {
      const scheme = tema === "auto" ? "light" : "dark";   // auto mostrado com o sistema em claro
      const { ctx, page, errs } = await novaPagina(browser, { width, tema, scheme });
      const ef = await page.evaluate(() => document.documentElement.dataset.theme);
      ok(ef === (tema === "auto" ? "claro" : tema), `${tema}: data-theme ${ef}`);
      ok(await page.inputValue("#tema") === tema, `${tema}: selector não mostra a escolha`);
      if (width === 360) {
        const [h1, sel] = await page.evaluate(() => ["header h1", "#tema"].map((s) => document.querySelector(s).getBoundingClientRect().top));
        ok(sel > h1 + 20, `360 ${tema}: o selector não desceu para a 2.ª linha (${h1} / ${sel})`);
        // review 1: música, tema e «ao vivo» na mesma linha (o header fica em 3 linhas, como antes do selector)
        const [m, t, l] = await page.evaluate(() => ["#music", "#tema", "#live"].map((s) => { const r = document.querySelector(s).getBoundingClientRect(); return r.top + r.height / 2; }));
        ok(Math.abs(m - t) < 4 && Math.abs(t - l) < 4, `360 ${tema}: música/tema/ao vivo não cabem numa linha (${m} / ${t} / ${l})`);
      }
      for (const [nome, hash] of VISTAS) {
        await irPara(page, hash);
        const [s, w] = await sw(page);
        ok(s <= w, `${width} ${tema} ${nome}: scroll horizontal ${s} > ${w}`);
        if (CAPTURAS && (width === 1440 || nome === "escritorio")) {
          const file = `tema-${tema}-${nome}-${width}.png`;
          await page.evaluate(() => scrollTo(0, 0));
          await page.screenshot({ path: path.join(OUT, file), fullPage: nome === "escritorio" });
          console.log(`${file}  scrollWidth ${s}/${w}`);
        }
      }
      ok(!errs.length, `${width} ${tema}: erros na página ${errs.join(" | ")}`);
      await ctx.close();
    }
  }
}

// ---- CA22, CA15/CA24, CA25, CA23 ----
async function selector(browser) {
  const { ctx, page, errs } = await novaPagina(browser, { width: 1440 });
  // CA25: Tab desde o início chega ao selector; seta muda o tema logo; foco visível; 44 px
  await page.evaluate(() => document.activeElement?.blur());
  let id = "";
  for (let i = 0; i < 15 && id !== "tema"; i++) { await page.keyboard.press("Tab"); id = await page.evaluate(() => document.activeElement?.id); }
  ok(id === "tema", "Tab não chega ao selector de tema");
  const f = await page.evaluate(() => { const e = document.querySelector("#tema"), s = getComputedStyle(e);
    return { fv: e.matches(":focus-visible"), outline: s.outlineStyle, ow: s.outlineWidth, h: e.getBoundingClientRect().height }; });
  ok(f.fv && f.outline !== "none" && f.ow !== "0px", `foco do selector não visível ${JSON.stringify(f)}`);
  ok(f.h >= 44, `selector com ${f.h} px de altura`);
  await page.keyboard.press("ArrowDown");
  await page.waitForTimeout(100);
  const s1 = await page.evaluate(() => [document.documentElement.dataset.theme, document.querySelector("#tema").value,
    localStorage.getItem("tema"), document.querySelector('meta[name="theme-color"]').content]);
  ok(s1.join() === "escuro,escuro,escuro,#12141c", `ArrowDown (auto→escuro) ${s1}`);
  await page.keyboard.press("ArrowDown");
  await page.waitForTimeout(100);
  const s2 = await page.evaluate(() => [document.documentElement.dataset.theme, localStorage.getItem("tema"),
    document.querySelector('meta[name="theme-color"]').content, getComputedStyle(document.body).backgroundColor]);
  ok(s2.join() === "claro,claro,#f4f5f9,rgb(244, 245, 249)", `ArrowDown (escuro→claro) ${s2}`);
  // CA22: sobrevive a reload; valor inválido = auto
  await page.selectOption("#tema", "sepia");
  await page.reload();
  await page.waitForSelector("#aprovacoes:not([hidden])");
  const r = await page.evaluate(() => [document.documentElement.dataset.theme, document.querySelector("#tema").value]);
  ok(r.join() === "sepia,sepia", `tema não sobreviveu ao reload ${r}`);
  await page.evaluate(() => localStorage.setItem("tema", "rosa"));
  await page.reload();
  await page.waitForSelector("#aprovacoes:not([hidden])");
  const r2 = await page.evaluate(() => [document.documentElement.dataset.theme, document.querySelector("#tema").value]);
  ok(r2.join() === "escuro,auto", `tema inválido não ficou auto ${r2}`);
  // CA15/CA24: auto segue o sistema com a página aberta (theme-color incluído); tema fixo não
  await page.emulateMedia({ colorScheme: "light" });
  await page.waitForTimeout(100);
  const a1 = await page.evaluate(() => [document.documentElement.dataset.theme, document.querySelector('meta[name="theme-color"]').content]);
  ok(a1.join() === "claro,#f4f5f9", `auto + sistema claro ${a1}`);
  await page.selectOption("#tema", "contraste");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForTimeout(100);
  ok(await page.evaluate(() => document.documentElement.dataset.theme) === "contraste", "tema fixo mudou com o sistema");
  // CA16: no alto contraste o foco é amarelo, 2 px (selector, notificações, filtros)
  for (const sel of ["#tema", "#bell", "#filt button", "#q"]) {
    await page.focus("#tema"); await page.keyboard.press("Shift+Tab"); await page.keyboard.press("Tab");   // foco por teclado
    if (sel !== "#tema") await page.evaluate((s) => document.querySelector(s).focus(), sel);
    const o = await page.evaluate((s) => { const e = document.querySelector(s), c = getComputedStyle(e);
      return [e.matches(":focus-visible"), c.outlineStyle, c.outlineWidth, c.outlineColor]; }, sel);
    ok(o.join() === "true,solid,2px,rgb(255, 255, 0)", `foco no contraste ${sel}: ${o}`);
  }
  // CA24: a vista não muda o theme-color
  for (const h of ["#quadro", "#biblioteca", ""]) {
    await irPara(page, h);
    const m = await page.evaluate(() => document.querySelector('meta[name="theme-color"]').content);
    ok(m === "#000000", `theme-color mudou na vista ${h || "escritório"}: ${m}`);
  }
  ok(!errs.length, `selector: erros na página ${errs.join(" | ")}`);
  await ctx.close();

  // CA23: com CPU 6× mais lenta, o 1.º frame já tem o tema guardado (nada escuro num tema claro)
  for (const tema of ["claro", "sepia", "contraste"]) {
    const ctx2 = await browser.newContext({ viewport: { width: 360, height: 740 }, colorScheme: "dark" });
    await ctx2.addInitScript(([t]) => {
      localStorage.setItem("tema", t);
      requestAnimationFrame(() => { window.__f = { th: document.documentElement.dataset.theme || null,
        bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
        body: document.body ? getComputedStyle(document.body).backgroundColor : null,
        meta: document.querySelector('meta[name="theme-color"]')?.content }; });
    }, [tema]);
    const p = await ctx2.newPage();
    const cdp = await ctx2.newCDPSession(p);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 6 });
    await p.goto(BASE);
    await p.waitForFunction(() => window.__f);
    const fr = await p.evaluate(() => window.__f), cor = { claro: "#f4f5f9", sepia: "#f3ead6", contraste: "#000000" }[tema];
    ok(fr.th === tema && fr.bg === cor && fr.meta === cor && (!fr.body || fr.body !== "rgb(18, 20, 28)"),
      `1.º frame com ${tema}: ${JSON.stringify(fr)}`);
    console.log(`1.º frame (${tema}, CPU 6×): ${JSON.stringify(fr)}`);
    await ctx2.close();
  }
}

// ---- Aprovações: CA6–CA12 + capturas ----
const estado = (page, i) => page.evaluate((i) => {
  const a = document.activeElement, el = document.querySelector(`#apr-list .apr:nth-child(${i})`);
  return { tag: a.tagName, f: a.dataset?.f || null, v: a.value ?? null, s: a.selectionStart ?? null,
    exp: el?.querySelector('[data-f="tog"]').getAttribute("aria-expanded"), open: document.querySelector("#apr-d").open,
    n: document.querySelector("#apr-d > summary").textContent };
}, i);

async function aprovacoes(browser) {
  const { ctx, page, errs } = await novaPagina(browser, { width: 360, height: 740 });
  const sumario = await page.textContent("#apr-d > summary");
  ok(/^Aprovações \(9\) · 14 decisões em aberto$/.test(sumario), `summary: «${sumario}»`);
  // o <details open> do HTML dispara um «toggle» ao carregar (grava "1"); o que conta é ficar aberta
  ok(await page.evaluate(() => document.querySelector("#apr-d").open && [null, "1"].includes(localStorage.getItem("aprSecao"))),
    "secção não abre por defeito");
  ok(await page.evaluate(() => [...document.querySelectorAll("#apr-list .apr-h")].every((b) => b.getAttribute("aria-expanded") === "false")),
    "pedidos não vêm recolhidos por defeito");
  const ord = await page.evaluate(() => { const b = document.body.children, i = [...b].indexOf(document.querySelector("#aprovacoes"));
    return [...b].slice(i + 1).map((e) => e.id || e.tagName).join(","); });
  ok(ord === "banner,reader,apr-dlg" || ord.startsWith("banner,"), `depois de #aprovacoes vem: ${ord}`);
  const shot = async (file) => { if (!CAPTURAS) return;
    await page.evaluate(() => document.querySelector("#aprovacoes").scrollIntoView({ block: "start" }));
    await page.screenshot({ path: path.join(OUT, file) }); console.log(file); };
  await shot("aprovacoes-360-aberta.png");

  // CA11: pedido aberto, nota a meio com cursor, foco na textarea; 3 polls
  await page.click('#apr-list .apr:nth-child(2) [data-f="tog"]');
  const ta = page.locator("#apr-list .apr:nth-child(2) textarea").first();
  await ta.fill("nota a meio de escrever");
  await ta.evaluate((t) => t.setSelectionRange(5, 5));
  for (let i = 0; i < 3; i++) { await page.evaluate(() => tick()); await page.waitForTimeout(300); }
  let st = await estado(page, 2);
  ok(st.tag === "TEXTAREA" && st.v === "nota a meio de escrever" && st.s === 5 && st.exp === "true",
    `CA11 textarea depois de 3 polls ${JSON.stringify(st)}`);
  // CA11: foco num botão (Recusar, activo porque há nota)
  await page.focus('#apr-list .apr:nth-child(2) [data-f="recusar:P1"]');
  for (let i = 0; i < 3; i++) { await page.evaluate(() => tick()); await page.waitForTimeout(300); }
  st = await estado(page, 2);
  ok(st.f === "recusar:P1" && st.exp === "true", `CA11 botão depois de 3 polls ${JSON.stringify(st)}`);
  ok(await ta.inputValue() === "nota a meio de escrever", "CA11 nota perdida");
  ok(JSON.parse(await page.evaluate(() => localStorage.getItem("aprAbertos"))).length === 1, "aprAbertos não gravou o pedido aberto");
  if (CAPTURAS) { await page.evaluate(() => document.querySelector("#apr-list .apr:nth-child(2)").scrollIntoView({ block: "start" }));
    await page.screenshot({ path: path.join(OUT, "aprovacoes-360-pedido.png") }); console.log("aprovacoes-360-pedido.png"); }

  // CA6/CA8: Enter no summary recolhe; o poll mantém recolhida e o foco no summary
  await page.focus("#apr-d > summary");
  await page.keyboard.press("Enter");
  for (let i = 0; i < 3; i++) { await page.evaluate(() => tick()); await page.waitForTimeout(300); }
  st = await estado(page, 2);
  const sec = await page.evaluate(() => localStorage.getItem("aprSecao"));
  ok(!st.open && st.tag === "SUMMARY" && sec === "0", `CA6/CA8 secção recolhida + poll ${JSON.stringify(st)} aprSecao=${sec}`);
  ok(!(await page.evaluate(() => document.querySelector("#apr-list").checkVisibility())), "recolhida, a lista continua visível");
  await shot("aprovacoes-360-recolhida.png");
  await page.keyboard.press("Space");
  await page.waitForTimeout(100);
  ok(await page.evaluate(() => document.querySelector("#apr-d").open && localStorage.getItem("aprSecao") === "1"), "Espaço não expande");
  await page.keyboard.press("Space");
  await page.waitForTimeout(100);

  // CA7/CA10: reload repõe secção recolhida e o pedido aberto
  await page.reload();
  await page.waitForSelector("#aprovacoes:not([hidden])");
  st = await estado(page, 2);
  ok(st.open === false && st.exp === "true", `CA7/CA10 depois do reload ${JSON.stringify(st)}`);

  // CA12 a partir da Biblioteca («Ver em Aprovações» do lote), com a secção recolhida
  await page.evaluate(() => { location.hash = "biblioteca"; });
  const ver = page.locator('#bib [data-a="aprov"]').first();
  await ver.waitFor({ timeout: 10000 }).catch(() => {});
  if (ok(await ver.count(), "Biblioteca sem «Ver em Aprovações» (lote ligado ao pedido p1)")) {
    await ver.click();
    await page.waitForTimeout(300);
    const v = await page.evaluate(() => [document.querySelector("#apr-d").open, document.activeElement.closest(".apr")?.dataset.k,
      document.activeElement.getAttribute("aria-expanded"), localStorage.getItem("aprSecao"), localStorage.getItem("aprAbertos"),
      document.body.dataset.view]);
    ok(v[0] && v[1] === "geral/p1.md" && v[2] === "true" && v[3] === "1" && v[4].includes("geral/p1.md") && v[5] === "biblioteca",
      `CA12 Biblioteca ${JSON.stringify(v)}`);
    const r = await page.evaluate(() => { const e = document.activeElement.getBoundingClientRect(); return [e.top, innerHeight]; });
    ok(r[0] >= 0 && r[0] < r[1], `CA12 pedido fora do ecrã ${r}`);
  }
  // CA12 a partir do Quadro (cartão da aprovação → «Abrir aprovação»), com a secção recolhida
  await page.evaluate(() => { document.querySelector("#apr-d").open = false; });
  await page.waitForTimeout(100);
  await irPara(page, "#quadro");
  const card = page.locator('#quadro .q-card[data-id^="apr:"]').filter({ hasText: "MenuQR" }).first();
  ok(await page.evaluate(() => getComputedStyle(document.querySelector("#aprovacoes")).display === "none"), "CA2 secção visível no Quadro");
  if (ok(await card.count(), "Quadro sem cartão da aprovação do MenuQR")) {
    await card.click();
    await page.locator('#quadro [data-a="apr"]').click();
    await page.waitForTimeout(400);
    const v = await page.evaluate(() => [document.querySelector("#apr-d").open, document.activeElement.closest(".apr")?.dataset.k,
      document.body.dataset.view || "", location.hash]);
    ok(v[0] && v[1] === "geral/p5.md" && v[2] === "" && v[3] === "", `CA12 Quadro ${JSON.stringify(v)}`);
  }
  // CA2: sem pedidos a secção fica hidden (e volta no poll seguinte)
  const vazio = await page.evaluate(() => { const l = state.aprovacoes; state.aprovacoes = []; renderApr();
    const h = document.querySelector("#aprovacoes").hidden; state.aprovacoes = l; renderApr(); return h; });
  ok(vazio, "CA2 secção visível sem pedidos");
  ok(!errs.length, `aprovações: erros na página ${errs.join(" | ")}`);
  await ctx.close();

  if (CAPTURAS) { // desktop: secção no fim, um pedido aberto
    const d = await novaPagina(browser, { width: 1440, storage: { aprAbertos: JSON.stringify(["geral/p3.md"]) } });
    await d.page.locator("#aprovacoes").screenshot({ path: path.join(OUT, "aprovacoes-1440-pedido.png") });
    console.log("aprovacoes-1440-pedido.png");
    ok(!d.errs.length, `aprovações desktop: erros ${d.errs.join(" | ")}`);
    await d.ctx.close();
  }
}

// ---- «Definições» e barra de progresso (push falso: a API e o PushManager) ----
const CHAVE = Buffer.from(Uint8Array.from({ length: 65 }, (_, i) => (i * 7 + 4) & 255)).toString("base64url");
const D9 = { eventos: { concluida: true, falhada: true, pr: true, alertas: true, aprovacoes: false, fases: false },
  squads: { dev: true, notas: true }, fases: { SPEC: false, PLAN: false, CODE: false, QA: true, PR: true },
  silencio: { ligado: false, de: "23:00", ate: "07:00" }, criticos_ignoram_silencio: true };
const fasesForm = (page) => page.evaluate(() => { const f = document.querySelector("#def-f").elements;
  return ["SPEC", "PLAN", "CODE", "QA", "PR"].map((k) => (f["f-" + k].checked ? "1" : "0") + (f["f-" + k].disabled ? "d" : "")).join(); });

async function pushFalso(page, pedidos) {
  await page.addInitScript(([chave]) => {
    const b = Uint8Array.from(atob(chave.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
    const sub = { endpoint: "https://fcm.googleapis.com/fcm/send/falso", options: { applicationServerKey: b.buffer },
      toJSON: () => ({ endpoint: sub.endpoint, expirationTime: null, keys: { p256dh: "p", auth: "a" } }), unsubscribe: async () => true };
    let ligada = true;
    if (window.Notification) Object.defineProperty(Notification, "permission", { get: () => "granted" }); // headless: sempre «denied»
    if (window.PushManager) Object.assign(PushManager.prototype, { getSubscription: async () => (ligada ? sub : null),
      subscribe: async () => { ligada = true; return sub; } });
    sub.unsubscribe = async () => { ligada = false; return true; };
  }, [CHAVE]);
  let prefs = structuredClone(D9);
  await page.route("**/api/push/**", async (r) => {
    const rota = new URL(r.request().url()).pathname.split("/").pop(), corpo = r.request().postDataJSON?.() ?? null;
    pedidos.push([rota, corpo]);
    if (rota === "chave") return r.fulfill({ json: { chave: CHAVE } });
    if (rota === "subscrever") { if (corpo.prefs) prefs = corpo.prefs; return r.fulfill({ json: { prefs } }); }
    return r.fulfill({ json: { ok: true } });
  });
}

async function definicoes(browser) {
  for (const tema of TEMAS) {
    const ctx = await browser.newContext({ viewport: { width: 360, height: 740 }, colorScheme: tema === "auto" ? "light" : "dark" });
    const page = await ctx.newPage(), errs = [], pedidos = [], ficheiros = [];
    page.on("pageerror", (e) => errs.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errs.push(m.text()));
    page.on("request", (q) => /definicoes\.(js|css)/.test(q.url()) && ficheiros.push(q.url().split("/").pop()));
    await ctx.addInitScript(([t]) => localStorage.setItem("tema", t), [tema]);
    await pushFalso(page, pedidos);
    await page.route("**/api/state", async (r) => { const res = await r.fetch(), j = await res.json(); j.tasks = tarefas(j.now); await r.fulfill({ response: res, json: j }); });
    await page.goto(BASE);
    await page.waitForFunction(() => document.querySelector("#live span").textContent.startsWith("ao vivo"));
    ok(!ficheiros.length, `${tema}: definicoes.* pedidos no load (${ficheiros})`);   // AC-23
    // a 360 px o ⚙ sobe para a 1.ª linha (ao lado da Biblioteca): música, tema e «ao vivo» continuam numa linha
    const lin = await page.evaluate(() => ["#bib-b", "#def-b"].map((s) => { const r = document.querySelector(s).getBoundingClientRect(); return Math.round(r.top + r.height / 2); }));
    ok(Math.abs(lin[0] - lin[1]) < 4, `${tema}: ⚙ fora da 1.ª linha (${lin})`);
    await page.click("#def-b");
    await page.waitForSelector("#def-dlg[open]");
    await page.waitForFunction(() => document.querySelector("#def-on")?.checked || document.querySelector("#def-m")?.textContent, null, { timeout: 10000 });
    ok(await page.evaluate(() => document.querySelector("#def-on").checked), `${tema}: interruptor desligado: «${await page.textContent("#def-m")}»`);
    ok(ficheiros.sort().join() === "definicoes.css,definicoes.js", `${tema}: ficheiros pedidos ao abrir ${ficheiros}`);
    ok(pedidos.some(([r, c]) => r === "subscrever" && !c.prefs && Object.keys(c.subscricao).join() === "endpoint,keys"), `${tema}: subscrever sem prefs ${JSON.stringify(pedidos)}`);
    // CA24/CA25 (office-push-barra-fases): fases desactivadas com o evento desligado; ao ligar, QA/PR ligadas e as 5 activas
    let fs = await fasesForm(page);
    ok(fs === "0d,0d,0d,1d,1d", `${tema}: fases com o evento desligado ${fs}`);
    if (tema === "claro" || tema === "escuro") {
      await page.locator('#def-f input[name="e-fases"]').check();
      fs = await fasesForm(page);
      ok(fs === "0,0,0,1,1", `${tema}: fases depois de ligar ${fs}`);
      await page.locator(".def-fases").scrollIntoViewIfNeeded();
    }
    // AC-26: sem scroll horizontal, ≥ 16 px e ≥ 44 px em todos os controlos
    const m = await page.evaluate(() => { const d = document.querySelector("#def-dlg"), r = d.getBoundingClientRect();
      const ctl = [...d.querySelectorAll("button, input[type=time], label.def-c")].map((e) => { const b = e.getBoundingClientRect();
        return [e.tagName + (e.textContent.trim().slice(0, 20) || e.name), b.height, parseFloat(getComputedStyle(e).fontSize), b.right]; });
      return { sw: document.documentElement.scrollWidth, iw: innerWidth, dsw: [...d.querySelectorAll("*")].filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== "visible").map((e) => e.className),
        right: r.right, ctl }; });
    ok(m.sw <= m.iw && m.right <= m.iw && !m.dsw.length, `${tema}: scroll horizontal no painel ${JSON.stringify([m.sw, m.iw, m.right, m.dsw])}`);
    for (const [n, h, f, rt] of m.ctl) ok(h >= 44 && f >= 16 && rt <= m.iw, `${tema}: controlo «${n}» ${h} px de alto, letra ${f} px, direita ${rt}`);
    ok(await page.evaluate(() => document.querySelector("#def-dlg").contains(document.activeElement)), `${tema}: foco fora do painel`);
    if (CAPTURAS) { await page.screenshot({ path: path.join(OUT, `definicoes-${tema}-360.png`) }); console.log(`definicoes-${tema}-360.png`); }
    if (tema === "escuro") {
      await page.click('[data-a="teste"]');
      await page.waitForFunction(() => document.querySelector("#def-m").textContent);
      ok(await page.textContent("#def-m") === "Teste enviado. Deve chegar em segundos.", "teste: mensagem");
      await page.locator('#def-f input[name="sil"]').check();
      await page.locator('#def-f input[name="de"]').fill("22:30");
      await page.click('#def-f button[type="submit"]');
      await page.waitForFunction(() => document.querySelector("#def-g").textContent);
      const g = pedidos.filter(([r, c]) => r === "subscrever" && c.prefs).pop()?.[1].prefs;
      ok(await page.textContent("#def-g") === "Guardado." && g?.silencio.ligado && g.silencio.de === "22:30" && g.eventos.aprovacoes === false,
        `guardar: ${await page.textContent("#def-g")} ${JSON.stringify(g)}`);
      ok(g?.eventos.fases === true && JSON.stringify(g.fases) === JSON.stringify(D9.fases), `guardar fases: ${JSON.stringify(g)}`);
      // evento desligado: as fases (desactivadas, fora do FormData) continuam a ir com a escolha feita
      await page.locator('#def-f input[name="e-fases"]').uncheck();
      const n = pedidos.length;
      await page.click('#def-f button[type="submit"]');
      for (let i = 0; i < 50 && pedidos.length === n; i++) await page.waitForTimeout(50);
      const g2 = pedidos.filter(([r, c]) => r === "subscrever" && c.prefs).pop()?.[1].prefs;
      ok(g2?.eventos.fases === false && JSON.stringify(g2.fases) === JSON.stringify(D9.fases), `guardar fases desactivadas: ${JSON.stringify(g2)}`);
      // prefs da barra aplicam-se logo (o #info por trás repinta)
      await page.locator('[data-v="progEstimativa"]').uncheck();
      ok(/a correr há 25 min/.test(await page.textContent("#info .prog-t")) && !(await page.$("#info .prog-m")), "progEstimativa=0 não repintou");
      await page.locator('[data-v="progBarra"]').uncheck();
      ok(!(await page.$("#info .prog")) && !!(await page.$("#info .steps")), "progBarra=0 não tirou a barra");
      ok(await page.evaluate(() => [localStorage.getItem("progBarra"), localStorage.getItem("progEstimativa")].join()) === "0,0", "prefs não gravadas");
      await page.locator('[data-v="progBarra"]').check(); await page.locator('[data-v="progEstimativa"]').check();
      // desligar: cancelar + interruptor desligado e prefs desactivadas
      await page.locator("#def-on").uncheck();
      await page.waitForTimeout(200);
      ok(pedidos.some(([r]) => r === "cancelar") && await page.evaluate(() => document.querySelector('[data-a="teste"]').disabled), "desligar");
      await page.keyboard.press("Escape");
      await page.waitForTimeout(100);
      ok(await page.evaluate(() => !document.querySelector("#def-dlg").open && document.activeElement.id === "def-b"), "Esc não fechou ou o foco não voltou ao ⚙");
    }
    ok(!errs.length, `definições ${tema}: erros ${errs.join(" | ")}`);
    await ctx.close();
  }
}

async function progresso(browser) {
  for (const tema of TEMAS) {
    const { ctx, page, errs } = await novaPagina(browser, { width: 1440, tema, scheme: tema === "auto" ? "light" : "dark" });
    const dev = await page.textContent("#info .prog-t");
    ok(dev === "≈ 41 % · faltam ~35 min (estimativa)" && await page.$$eval("#info .prog .steps span", (l) => l.map((e) => e.className).join()) === "done,done,now,,",
      `${tema}: barra do Dev «${dev}»`);
    if (CAPTURAS) { await page.locator("#info").screenshot({ path: path.join(OUT, `progresso-dev-${tema}-1440.png`) }); console.log(`progresso-dev-${tema}-1440.png`); }
    await page.click('#rooms [data-room="notas"]');
    const nt = await page.textContent("#info .prog-t");
    ok(nt === "a demorar mais do que o costume (típico ~10 min)" && !(await page.$("#info .prog .steps")), `${tema}: barra das Notas «${nt}»`);
    if (CAPTURAS) { await page.locator("#info").screenshot({ path: path.join(OUT, `progresso-notas-${tema}-1440.png`) }); console.log(`progresso-notas-${tema}-1440.png`); }
    if (tema === "escuro") {
      await irPara(page, "#quadro");
      const c = page.locator("#quadro .q-card:has(.prog)").first();
      ok(await c.count() && /faltam ~35 min/.test(await c.textContent()), "Quadro: cartão em curso sem barra");
      if (CAPTURAS && await c.count()) { await page.locator("#q-col-em-curso").screenshot({ path: path.join(OUT, "progresso-quadro-1440.png") }); console.log("progresso-quadro-1440.png"); }
    }
    ok(!errs.length, `progresso ${tema}: erros ${errs.join(" | ")}`);
    await ctx.close();
  }
}

const tmp = mkdtempSync(path.join(os.tmpdir(), "capturas-temas-"));
execFileSync("python3", ["-c", DADOS, tmp], { cwd: ROOT, stdio: "inherit" });
const SERVE = `import server; from http.server import ThreadingHTTPServer; ThreadingHTTPServer(("127.0.0.1", ${PORT}), server.Handler).serve_forever()`;
const env = { ...process.env, OFFICE_CONFIG: path.join(tmp, "office.yaml"), TASKS_DIR: path.join(tmp, "tasks"),
  CLAUDE_PROJECTS_DIR: path.join(tmp, "claude"), SQUADS_DIR: path.join(tmp, "squads"), APROVACOES_DIR: path.join(tmp, "aprov"),
  DECISOES_FILA_DIR: path.join(tmp, "fila"), QUADRO_DIR: path.join(tmp, "quadro"), BIBLIOTECA_DIR: path.join(tmp, "bib"),
  BIBLIOTECA_MINI_DIR: path.join(tmp, "mini"), PORT: String(PORT) };
const srv = spawn("python3", ["-c", SERVE], { cwd: ROOT, env, stdio: "ignore" });
let browser;
try {
  for (let i = 0; ; i++) {
    try { if ((await fetch(BASE + "healthz")).ok) break; } catch {}
    if (i > 50) throw new Error("server.py não respondeu em /healthz");
    await new Promise((r) => setTimeout(r, 100));
  }
  mkdirSync(OUT, { recursive: true });
  browser = await chromium.launch();
  await selector(browser);
  await aprovacoes(browser);
  await temas(browser);
  await definicoes(browser);
  await progresso(browser);
} finally {
  await browser?.close();
  srv.kill();
  rmSync(tmp, { recursive: true, force: true });
}
console.log(falhas ? `${falhas} falha(s)` : "tudo OK");
if (falhas) process.exitCode = 1;
