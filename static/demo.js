// v3 (CA-48, 49): modo demonstração em /?demo=1. Só carrega com demo=1 (import() no index.html), antes dos outros módulos e do
// 1.º poll. O único pedido a /api/ é o GET /api/config (público e sem dados: as salas e os projectos vêm do office.yaml, D-7);
// o resto do fetch e o localStorage são trocados aqui (só chaves «demo:»). Tarefas, textos e números são inventados.
// As exportadas no topo são puras (testes em node): sem Date.now, Math.random, fetch nem localStorage (relógio, semente e
// config entram como argumentos).

// ---- puras

// PRNG pequeno e determinístico (mulberry32)
export function prng(semente) {
  let a = semente >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const MIN = 6e4, HORA = 36e5, DIA_MS = 864e5;
const iso = (ms) => new Date(ms).toISOString();
const fmt = (tz) => { const o = { year: "numeric", month: "2-digit", day: "2-digit", hour: "numeric", minute: "numeric", second: "numeric", hourCycle: "h23" };
  try { return new Intl.DateTimeFormat("en-GB", { ...o, timeZone: tz }); } catch { return new Intl.DateTimeFormat("en-GB", o); } };
const partes = (ms, tz) => { const p = {}; for (const x of fmt(tz).formatToParts(new Date(ms))) p[x.type] = +x.value; return p; };
// meia-noite no fuso tz do dia de ms (epoch ms)
export function meiaNoite(ms, tz) {
  const p = partes(ms, tz);
  return ms - ((p.hour * 60 + p.minute) * 60 + p.second) * 1000 - (((ms % 1000) + 1000) % 1000);
}
export const diaLocal = (ms, tz) => { const p = partes(ms, tz); return `${p.year}-${String(p.month).padStart(2, "0")}-${String(p.day).padStart(2, "0")}`; };
// meia-noite de um dia «AAAA-MM-DD» no fuso tz (a partir do meio-dia UTC: certo também nos dias de mudança de hora)
function inicioDia(dia, tz) {
  const [y, m, d] = dia.split("-").map(Number);
  return meiaNoite(Date.UTC(y, m - 1, d, 12), tz);
}

const ACTS = [["editar", "Edit"], ["ler", "Read"], ["testes", "Bash"], ["web", "WebFetch"], ["pensar", null], ["delegar", "Task"]];
const EQUIPA = ["architect", "backend-engineer", "frontend-engineer", "test-engineer"];
const SQ0 = [{ id: "dev", nome: "Dev", lead: "tech-lead", formato: "worktree" }];
const P0 = [{ id: "exemplo", nome: "Exemplo" }];

// /api/state fictício e determinístico para as squads e os projectos da config; mesmas 15 chaves de topo do servidor
// (tests/estado_chaves.json)
export function estadoDemo(agora, semente = 1, cfg = {}) {
  const sqs = Array.isArray(cfg.squads) && cfg.squads.length ? cfg.squads : SQ0, ps = Array.isArray(cfg.projectos) && cfg.projectos.length ? cfg.projectos : P0;
  const tz = cfg.fuso, at = (min) => iso(agora - min * MIN), slot = Math.floor(agora / (3 * MIN)), r = prng(semente * 7919 + slot), r0 = prng(semente);
  const pj = (i) => ps[i % ps.length].id, n2 = (x) => Math.round(x * 100) / 100;
  // com desde: Bash em 1.º plano sem resultado há «desde» min (à espera do terminal)
  const act = (k, desde) => (desde ? { kind: "terminal", tool: "Bash", error: false, at: at(desde), desde: at(desde) } : { kind: ACTS[k][0], tool: ACTS[k][1], error: false, at: at(0.5) });
  const nulo = { tarefas: null, linhas: null, modelo: null, ramo: null };
  const ag = (role, desc, o = {}) => ({ role, desc, start: at(o.ini ?? 60), end: o.finished ? at(o.fim ?? 20) : null, last: at(o.finished ? o.fim ?? 20 : 0.3),
    finished: !!o.finished, active: !!o.active, act: o.act ?? null, stuck: !!o.stuck, ...nulo, ...o.x });
  const base = (x) => ({ titulo: null, branch: null, pr: null, finished: null, last_activity: at(0.3), auto_resumes: 0, issues: [], cost_usd: n2(0.4 + r0() * 3),
    turns: 20 + Math.floor(r0() * 40), duration_s: null, gh: { pr_url: null, pr: null, ci: null, at: null }, progress: null, fim: null, resultado: null,
    phases: { SPEC: false, PLAN: false, QA: false }, working: [], agents: [], ...x });
  const tasks = [];
  sqs.forEach((s, i) => {
    const wt = s.formato === "worktree", lead = s.lead || "tech-lead", activo = Math.floor(r() * 3) !== 0, id = (k) => `demo-${s.id}-${k}`;
    const extra = { tarefas: { feitas: 2 + (i % 3), total: 7 }, linhas: { mais: 40 + i * 13, menos: 6 + i, aprox: i % 2 === 1 }, modelo: "modelo-demo", ramo: `feat/demo-${i + 1}` };
    tasks.push(base({ id: id(1), squad: s.id, project: pj(i), mode: wt ? "goal" : "sessao", status: "a correr", running: true, started: at(40 + i * 25),
      titulo: wt ? `Demonstração ${i + 1}: agenda partilhada` : null, branch: wt ? extra.ramo : null, phases: { SPEC: true, PLAN: wt, QA: false },
      working: activo ? [wt ? "frontend-engineer" : "general"] : [], duration_s: (40 + i * 25) * 60,
      lead: { role: lead, act: act(2, i === 0 ? 12 : 0), stuck: false, ...nulo, tarefas: extra.tarefas },
      agents: wt ? [ag(EQUIPA[0], "plano", { finished: true, ini: 35, fim: 25 }), ag(EQUIPA[1], "rotas da API", { ini: 30, act: act(0), x: extra }),
        ag(EQUIPA[2], "ecrã no telemóvel", { ini: 20, active: activo, act: act(activo ? 0 : 4), x: { ...extra, tarefas: { feitas: 4, total: 7 } } })]
        : [ag("general", "pesquisa", { ini: 15, active: activo, act: act(activo ? 3 : 1) })] }));
    tasks.push(base({ id: id(2), squad: s.id, project: pj(i + 1), mode: wt ? "task" : "sessao", status: i % 2 ? "erro" : "concluída", running: false,
      started: at(300 + i * 60), finished: at(240 + i * 60), duration_s: 3600, titulo: wt ? `Demonstração ${i + 1}: correcção` : null,
      phases: { SPEC: true, PLAN: true, QA: !(i % 2) }, pr: wt && !(i % 2) ? "https://github.com/exemplo/demo/pull/12 (open)" : null,
      gh: { pr_url: wt && !(i % 2) ? "https://github.com/exemplo/demo/pull/12" : null, pr: wt && !(i % 2) ? "aberto" : null, ci: wt ? "verde" : null, at: null },
      fim: wt ? { recusas: 1, ferramentas: ["Bash"], motivo: "completed", erro: !!(i % 2), http: i % 2 ? 529 : null } : null,
      lead: { role: lead, act: null, stuck: false, ...nulo }, agents: [ag(wt ? EQUIPA[1] : "general", "trabalho feito", { finished: true, ini: 290 + i * 60, fim: 250 + i * 60, x: extra })] }));
  });
  const dia0 = meiaNoite(agora, tz), ids = sqs.map((s) => s.id);
  const totals = Object.fromEntries(ids.map((s, i) => [s, { day: { cost_usd: n2(1 + i + r0() * 4), tasks: 2 + i, sem_custo: 0 }, week: { cost_usd: n2(20 + i * 5 + r0() * 10), tasks: 12 + i * 3, sem_custo: 1 } }]));
  const history = Array.from({ length: 30 }, (_, i) => ({ date: diaLocal(dia0 - (29 - i) * DIA_MS + 12 * HORA, tz),
    ...Object.fromEntries(ids.map((s) => [s, { cost_usd: n2(r0() * 6), done: Math.floor(r0() * 4), hours: n2(r0() * 5) }])) }));
  const tok = (k) => ({ input: 4000 * k, output: 9000 * k, cache_read: 250000 * k, cache_creation: 30000 * k });
  const usage = { status: "ok", computed_at: at(1), windows: { "5h": { "modelo-demo": tok(3) }, "7d": { "modelo-demo": tok(40) } } };
  const vivas = tasks.filter((t) => t.running);
  const feed = Array.from({ length: 14 }, (_, i) => { const t = vivas[i % vivas.length], a = ACTS[(i + slot) % ACTS.length];
    return { at: at(1 + i * 4), squad: t.squad, task: t.id, role: i % 2 ? t.lead.role : t.agents[0]?.role || t.lead.role, kind: a[0], tool: a[1], error: false }; });
  const hoje = tasks.filter((t) => Date.parse(t.started) >= dia0);
  const today = { cost_usd: n2(ids.reduce((a, s) => a + totals[s].day.cost_usd, 0)), done: 3, hours: 6.4, top: { id: tasks[0].id, project: tasks[0].project, cost_usd: tasks[0].cost_usd },
    timeline: hoje.map((t) => ({ id: t.id, squad: t.squad, project: t.project, start: t.started, end: t.finished })), inicio: iso(dia0),
    intervalos: tasks.filter((t) => !t.finished || Date.parse(t.finished) > dia0).map((t) => ({ squad: t.squad, ini: iso(Math.max(dia0, Date.parse(t.started))), fim: t.finished }))
      .sort((a, b) => a.ini.localeCompare(b.ini)) };
  const comPr = tasks.filter((t) => t.gh.pr === "aberto"), falhas = tasks.filter((t) => !t.running && (t.status === "erro" || t.fim?.erro));
  const espera = { n: comPr.length, itens: comPr.map((t) => ({ tipo: "pr", chave: `pr:${t.squad}/${t.id}`, id: t.id, squad: t.squad, project: t.project,
    pr_url: t.gh.pr_url, draft: false, finished: t.finished, desde: t.finished })) };
  const falhas_exec = falhas.map((t) => ({ id: t.id, squad: t.squad, project: t.project, status: t.status, motivo: t.fim?.motivo ?? null, http: t.fim?.http ?? null,
    erro: !!t.fim?.erro, finished: t.finished }));
  return { alerts: { count: 0, items: [] }, aprovacoes: [], espera, falhas_exec, feed, history, now: iso(agora),
    runner: { v: 1, em: at(0.5), versao: "demo", claude: "ok", credencial: "ok", gh: "ok" }, tarefa_entregue: true, tasks, today, token: "demo", totals, usage,
    waiting: comPr.map((t) => ({ id: t.id, project: t.project, pr_url: t.gh.pr_url, draft: false, finished: t.finished })) };
}

// tarefa de um quadro (allowlist do GET /api/replay, v: 1): sem títulos, descrições, custos nem as 4 chaves por agente
const actQ = (a) => ({ kind: a.kind, tool: a.tool, error: a.error, desde: a.desde ?? null });
const quadroDe = (x) => ({ id: x.id, squad: x.squad, project: x.project, mode: x.mode, status: x.status, running: x.running, phases: x.phases,
  ci: x.gh?.ci ?? null, lead: { role: x.lead.role, act: x.running && x.lead.act ? actQ(x.lead.act) : null, stuck: !!x.lead.stuck },
  agents: x.running ? x.agents.map((a) => ({ role: a.role, active: a.active, finished: a.finished, stuck: a.stuck, act: a.act ? actQ(a.act) : null })) : [] });

// GET /api/replay?dia= fictício: estadoDemo amostrado de 3 em 3 min a partir das 06:00; «completo» de 5 em 5 min e «dif» só quando muda;
// cada sala acorda à sua hora (06:00 + 1,5 h × a ordem na config)
export function replayDemo(dia, semente = 1, agora = Infinity, cfg = {}) {
  const tz = cfg.fuso, inicio = inicioDia(dia, tz), seguinte = inicioDia(diaLocal(inicio + 30 * HORA, tz), tz), fim = Math.min(agora, seguinte);
  const ordem = (cfg.squads || SQ0).map((s) => s.id), quadros = [], visto = new Map(), chave = (x) => `${x.squad}/${x.id}`;
  for (let t = inicio + 6 * HORA; t <= fim; t += MIN) {
    const s = Math.floor(t / 1000), completo = s % 300 === 0;
    if (!completo && s % 180) continue;
    const ts = estadoDemo(t, semente, cfg).tasks.filter((x) => !x.running || t >= inicio + (6 + 1.5 * Math.max(0, ordem.indexOf(x.squad))) * HORA).map(quadroDe);
    if (completo) { quadros.push({ t: s, tipo: "completo", tarefas: ts }); visto.clear(); ts.forEach((x) => visto.set(chave(x), JSON.stringify(x))); continue; }
    const mudou = ts.filter((x) => visto.get(chave(x)) !== JSON.stringify(x)), agoraK = new Set(ts.map(chave)), saiu = [...visto.keys()].filter((k) => !agoraK.has(k));
    if (!mudou.length && !saiu.length) continue;
    quadros.push({ t: s, tipo: "dif", mudou, saiu });
    mudou.forEach((x) => visto.set(chave(x), JSON.stringify(x)));
    saiu.forEach((k) => visto.delete(k));
  }
  return { v: 1, dia, inicio: Math.floor(inicio / 1000), fim: Math.floor(fim / 1000), truncado: false, quadros };
}

// ---- DOM: instalar() corre antes dos outros módulos e do 1.º poll (o index.html espera por ela)

export const MSG = "Indisponível no modo demonstração.";
const BLOQ = "#quad-b, #bib-b, [data-act=entrega], [data-act=ler], #def-nb, #def-on, #def-f button, .apr button";
const ROTA_BLOQ = /^#(quadro|biblioteca)/;

// w = window (nos testes, um falso com fetch, Storage, localStorage, location, history, document e Response)
export async function instalar(w = globalThis) {
  const q = new URLSearchParams(w.location.search), fixo = Date.parse(q.get("agora") ?? ""), semente = Number.parseInt(q.get("semente") ?? "", 10);
  const sem = Number.isFinite(semente) ? semente : 1, relogio = () => (Number.isFinite(fixo) ? fixo : Date.now());
  const f0 = w.fetch.bind(w);
  // o único pedido real: GET api/config (sem resposta: as omissões do index.html)
  let cfg = null;
  try { const r = await f0("api/config", { cache: "no-store" }); if (r.ok) cfg = await r.json(); } catch {}
  const C = cfg && typeof cfg === "object" ? cfg : {};
  const json = (corpo, status = 200) => new w.Response(JSON.stringify(corpo), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
  w.fetch = (input, init) => {
    let u;
    try { u = new URL(typeof input === "string" || input instanceof URL ? input : input.url, w.location.href); } catch { return f0(input, init); }
    if (!/\/api\//.test(u.pathname)) return f0(input, init);
    if (/\/api\/state$/.test(u.pathname)) return Promise.resolve(json(estadoDemo(relogio(), sem, C)));
    if (/\/api\/config$/.test(u.pathname)) return Promise.resolve(cfg ? json(cfg) : json({ error: MSG }, 503));
    if (/\/api\/replay$/.test(u.pathname)) {
      const dia = u.searchParams.get("dia") ?? "", hoje = diaLocal(relogio(), C.fuso), ok = /^\d{4}-\d{2}-\d{2}$/.test(dia) && dia <= hoje
        && (Date.parse(hoje) - Date.parse(dia)) / DIA_MS <= 6;
      return Promise.resolve(ok ? json(replayDemo(dia, sem, relogio(), C)) : json({ error: "não encontrado" }, 404));
    }
    return Promise.resolve(json({ error: MSG, motivo: "demo" }, 503));
  };
  // localStorage: escritas e leituras em «demo:<chave>»; o tema lê o real enquanto não houver um tema demo
  const S = w.Storage.prototype, get = S.getItem, set = S.setItem, rm = S.removeItem;
  const local = (o) => { try { return o === w.localStorage; } catch { return false; } };
  S.getItem = function (k) { if (!local(this)) return get.call(this, k); const v = get.call(this, "demo:" + k); return v === null && k === "tema" ? get.call(this, k) : v; };
  S.setItem = function (k, v) { return set.call(this, local(this) ? "demo:" + k : k, v); };
  S.removeItem = function (k) { return rm.call(this, local(this) ? "demo:" + k : k); };
  // selo fixo no cabeçalho e uma zona para os avisos de indisponível
  const d = w.document, st = d.createElement("style");
  st.textContent = `body.demo header { position: sticky; top: 0; z-index: 20; background: var(--bg); flex-wrap: wrap; }
#demo-selo { margin: 0; padding: 2px 8px; background: var(--warn); color: var(--on-warn); font-size: 14px; font-weight: 700; white-space: nowrap; }
#demo-msg { order: 10; flex-basis: 100%; margin: 0; font-size: 14px; } #demo-msg:empty { display: none; }
@media (max-width: 520px) { #demo-selo { order: 9; } }`;
  d.head.append(st);
  d.body.classList.add("demo");
  d.querySelector("header h1").insertAdjacentHTML("afterend", `<p id="demo-selo" role="status">DEMO · dados fictícios</p>`);
  d.querySelector("header").insertAdjacentHTML("beforeend", `<p id="demo-msg" class="sub" role="status"></p>`);
  let tm = 0;
  const avisar = () => { const m = d.querySelector("#demo-msg"); m.textContent = MSG; clearTimeout(tm); tm = setTimeout(() => { m.textContent = ""; }, 6000); };
  // Quadro, Biblioteca, leitor, entrega, aprovações e notificações: nunca respondem (e nada pede /api/)
  d.addEventListener("click", (e) => {
    if (!e.target.closest?.(BLOQ)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    avisar();
  }, true);
  const rota = () => { if (!ROTA_BLOQ.test(w.location.hash)) return false; w.history.replaceState(null, "", w.location.pathname + w.location.search); avisar(); return true; };
  w.addEventListener("hashchange", (e) => { if (rota()) e.stopImmediatePropagation(); }, true);
  rota();
  d.title = "[demo] " + d.title;
}
