// v3 (CA-40…43): replay do dia do escritório em acelerado (60×, 180×, 600×), hoje e os 6 dias anteriores no fuso do office.yaml.
// Só carrega no 1.º clique em «Replay» (import() no index.html); o replay.css entra por <link> posto aqui.
// Só o desenho, os botões das salas e a faixa de tarefas mostram o quadro do instante; contadores, Fábrica, avisos, Aprovações,
// notificações, título e #info ficam ao vivo (e o #info di-lo). Os quadros vêm de GET /api/replay?dia= (v: 1).
// As exportadas no topo são puras (testes em node).

// ---- puras

// t (epoch s) depois de dtMs reais à velocidade vel; pára no fim
export function avancar(t, vel, dtMs, fim) {
  const n = t + (dtMs / 1000) * vel;
  return n >= fim ? { t: fim, fim: true } : { t: n, fim: false };
}

const chave = (x) => `${x.squad}/${x.id}`;
// as tarefas no instante t: o último «completo» com t ≤ t e, por ordem, os «dif» até t (mudou substitui ou acrescenta, saiu tira)
export function estadoEm(dados, t) {
  const q = Array.isArray(dados?.quadros) ? dados.quadros : [];
  let i = -1;
  for (let k = 0; k < q.length && q[k].t <= t; k++) if (q[k].tipo === "completo") i = k;
  const m = new Map(i < 0 ? [] : (q[i].tarefas || []).map((x) => [chave(x), x]));
  for (let k = i + 1; k < q.length && q[k].t <= t; k++) {
    if (q[k].tipo !== "dif") continue;
    for (const x of q[k].mudou || []) m.set(chave(x), x);
    for (const c of q[k].saiu || []) m.delete(c);
  }
  return [...m.values()];
}

// um quadro na forma das tarefas do /api/state, para o desenho, as salas e a faixa; o replay não traz títulos (CA-21, CA-42)
export function paraEstado(tarefas, t) {
  const nulo = { tarefas: null, linhas: null, modelo: null, ramo: null };
  return (tarefas || []).map((x) => {
    const ag = Array.isArray(x.agents) ? x.agents : [], L = x.lead || {}, ativos = ag.filter((a) => a.active), desde = Date.parse(L.act?.desde ?? "") / 1000;
    const working = ativos.map((a) => a.role);
    if (L.role && L.act && (!(desde > 0) || t - desde < 180) && !ativos.length) working.push(L.role);
    return { id: x.id, squad: x.squad, project: x.project, mode: x.mode, status: x.status, running: !!x.running,
      phases: x.phases || { SPEC: false, PLAN: false, QA: false }, titulo: `${x.project} · ${x.mode}`, gh: { ci: x.ci ?? null, pr: null },
      working, issues: [], pr: null, branch: null, started: undefined, finished: undefined,
      lead: { ...L, ...nulo }, agents: ag.map((a) => ({ ...a, desc: "", start: null, last: null, ...nulo })) };
  });
}

// formatador no fuso tz (fuso desconhecido do browser: hora local)
const fmt = (l, o, tz) => { try { return new Intl.DateTimeFormat(l, { ...o, timeZone: tz }); } catch { return new Intl.DateTimeFormat(l, o); } };
const SEM = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
// hh:mm no fuso tz de um epoch em segundos
export const hhmm = (s, tz) => fmt("pt-PT", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }, tz).format(new Date(s * 1000));
// hoje e os 6 dias anteriores no fuso tz: [{dia: "2026-11-15", rotulo: "hoje"}, {dia: "2026-11-14", rotulo: "sáb 14/11"}, …]
export function diasReplay(agora, tz) {
  const [y, m, d] = fmt("en-CA", {}, tz).format(new Date(agora)).split("-").map(Number), p = (n) => String(n).padStart(2, "0");
  return Array.from({ length: 7 }, (_, i) => {
    const u = new Date(Date.UTC(y, m - 1, d - i));
    return { dia: u.toISOString().slice(0, 10), rotulo: i ? `${SEM[u.getUTCDay()]} ${p(u.getUTCDate())}/${p(u.getUTCMonth() + 1)}` : "hoje" };
  });
}

// ---- DOM

const VELS = [60, 180, 600];
let A = null, salvo = null, dados = null, t = 0, vel = 180, aTocar = false, timer = 0, ult = 0, minuto = null, dia = null, hoje = null, seq = 0, cssP = null;
const $ = (s) => document.querySelector(s);
const fim = () => dados?.fim ?? t;
const inicio = () => dados?.inicio ?? t;
// 24:00 no fim de um dia passado (a barra vai até à meia-noite seguinte)
const hm = (s) => (dados && dia !== hoje && s >= dados.fim ? "24:00" : hhmm(s, A?.tz()));

// o que o index.html lê enquanto o replay está aberto (rp): hora do quadro (ms), estado do desenho, tarefa ao vivo do #info, aria-label
const rp = {
  agora: () => t * 1000,
  estado: () => ({ tasks: paraEstado(estadoEm(dados, t), t) }),
  tarefaViva: (vivo) => (vivo?.tasks || []).find((x) => x.id === salvo?.[1]) ?? null,
  sufixo: () => " · replay " + hm(t),
};

// api = { paint(animate), esc, tz(), getState(), guardar() → [sala, tarefa, pessoa], repor([sala, tarefa, pessoa]), ligar(rp), desligar() }
export async function abrir(api) {
  if (A) return $("#rp-p")?.focus();
  A = api;
  salvo = A.guardar();
  A.repor([salvo[0], salvo[1], null]); // sem #who durante o replay
  cssP ||= new Promise((ok) => { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = new URL("replay.css", import.meta.url); l.onload = l.onerror = ok; document.head.append(l); });
  await cssP;
  const agora = Date.parse(A.getState()?.now ?? "") || Date.now(), dias = diasReplay(agora, A.tz());
  hoje = dias[0].dia;
  t = agora / 1000;
  const esc = A.esc, p = document.createElement("section");
  p.id = "replay";
  p.setAttribute("aria-label", "Replay do dia");
  p.innerHTML = `<div class="rp-l"><p id="rp-et" class="mono" aria-hidden="true">REPLAY <span>--:--</span></p>`
    + `<button type="button" id="rp-p" aria-pressed="false" aria-label="Reproduzir">▶</button>`
    + `<div class="rp-v" role="radiogroup" aria-label="Velocidade">${VELS.map((v) => `<label><input type="radio" name="rp-v" value="${v}"${v === vel ? " checked" : ""}><span>${v}×</span></label>`).join("")}</div></div>`
    + `<div class="rp-l"><select id="rp-d" aria-label="Dia do replay">${dias.map((x) => `<option value="${esc(x.dia)}">${esc(x.rotulo)}</option>`).join("")}</select>`
    + `<input id="rp-t" type="range" step="60" aria-label="Hora do replay">`
    + `<button type="button" id="rp-x" aria-label="Sair do replay">Sair</button></div><p id="rp-m" class="sub" role="status"></p>`;
  $("#office").before(p);
  const nota = document.createElement("p");
  nota.id = "rp-nota";
  nota.className = "sub";
  nota.textContent = "Só o escritório está em replay; este painel, os contadores e a Fábrica estão ao vivo.";
  $("#info-s").after(nota);
  document.body.classList.add("rp-on");
  $("#rp-p").onclick = () => (aTocar ? parar() : tocar());
  $("#rp-x").onclick = sair;
  $("#rp-d").onchange = (e) => carregar(e.target.value);
  $("#rp-t").oninput = (e) => irPara(+e.target.value);
  p.querySelector(".rp-v").onchange = (e) => { vel = +e.target.value; };
  p.addEventListener("click", (e) => { if (e.target.closest("[data-a=tentar]")) carregar(dia); });
  document.addEventListener("keydown", tecla);
  A.ligar(rp);
  carregar(hoje);
  $("#rp-p").focus();
}

export function sair() {
  if (!A) return;
  parar();
  document.removeEventListener("keydown", tecla);
  $("#replay")?.remove();
  $("#rp-nota")?.remove();
  document.body.classList.remove("rp-on");
  const a = A;
  A = null; dados = null; seq++;
  a.desligar();
  a.repor(salvo); // a sala, a tarefa e a secretária de antes do replay, numa só pintura
  a.paint(false);
  $("#rp-b")?.focus();
}

async function carregar(d) {
  parar();
  dia = d;
  dados = null;
  const n = ++seq, m = $("#rp-m");
  m.textContent = "A carregar…";
  barra(false);
  let j = null;
  try {
    const r = await fetch("api/replay?" + new URLSearchParams({ dia: d }), { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    j = await r.json();
    if (!Array.isArray(j?.quadros) || !(j.fim >= j.inicio)) throw new Error("forma");
  } catch {
    if (n !== seq) return;
    m.innerHTML = `Não foi possível carregar o replay. <button type="button" data-a="tentar">Tentar de novo</button>`;
    return A.paint(false);
  }
  if (n !== seq) return;
  dados = j;
  const q = j.quadros;
  m.textContent = !q.length ? "Sem actividade registada neste dia." : j.truncado ? "replay simplificado (de 5 em 5 min)" : "";
  t = q.length ? Math.min(Math.max(q[0].t, j.inicio), j.fim) : j.inicio;
  barra(q.length > 0);
  minuto = null;
  pintar();
}

function barra(on) {
  const r = $("#rp-t");
  r.disabled = !on;
  $("#rp-p").disabled = !on;
  if (dados) { r.min = dados.inicio; r.max = dados.fim; }
}

// etiqueta, barra e desenho; o desenho só volta a pintar quando o minuto muda (AC-46: o avanço não é uma animação CSS)
function pintar() {
  const r = $("#rp-t"), h = hm(t);
  $("#rp-et span").textContent = h;
  r.value = t;
  r.setAttribute("aria-valuetext", h);
  const mm = Math.floor(t / 60);
  if (mm !== minuto) { minuto = mm; A.paint(false); }
}

function irPara(s) {
  if (!dados?.quadros.length) return;
  t = Math.min(Math.max(s, inicio()), fim());
  pintar();
}

// performance.now(), nunca Date.now(): o relógio fixo das capturas não pára o replay
function tocar() {
  if (!dados?.quadros.length) return;
  if (t >= fim()) t = inicio();
  aTocar = true;
  $("#rp-p").setAttribute("aria-pressed", "true");
  $("#rp-p").textContent = "❚❚";
  ult = performance.now();
  timer = setInterval(() => {
    const n = performance.now(), r = avancar(t, vel, n - ult, fim());
    ult = n;
    t = r.t;
    pintar();
    if (r.fim) parar();
  }, 250);
}
function parar() {
  clearInterval(timer);
  aTocar = false;
  const b = $("#rp-p");
  if (b) { b.setAttribute("aria-pressed", "false"); b.textContent = "▶"; }
}

// Esc sai; ←/→ = 1 min e PageUp/PageDown = 15 min (fora de campos de texto, listas, rádios e separadores; nunca com um diálogo aberto)
function tecla(e) {
  if (e.ctrlKey || e.metaKey || e.altKey || document.querySelector("dialog[open]")) return;
  if (e.key === "Escape") { e.preventDefault(); return sair(); }
  const d = { ArrowLeft: -60, ArrowRight: 60, PageDown: -900, PageUp: 900 }[e.key], el = document.activeElement;
  if (!d || (el && el.id !== "rp-t" && el.matches?.("input, select, textarea, [contenteditable], [role=tab]"))) return;
  e.preventDefault();
  irPara(t + d);
}
