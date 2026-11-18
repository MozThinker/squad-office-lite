// v3 (CA-24…27, 35, 45, 46): separadores «Tarefa | Agentes | Entregas» no #info, lista de agentes de todas as squads com
// pesquisa «/» e «Seguir», «4/7», linhas, modelo e ramo por agente e o contador das entregas por ler. Carregado no arranque
// (import() no index.html); entregas.js só no 1.º clique em «Entregas». As exportadas no topo são puras (testes em node).
import { contadores, terminalTexto } from "./topo.js";

// ---- puras

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fold = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const actText = (a) => globalThis.ACTIVIDADE?.actText(a) ?? "";

// texto de «Agora:» (terminal com escalões) e se o terminal já conta como preso
function acaoDe(n, agora) {
  if (!n?.act) return { acao: "—", preso: false, tt: null };
  const tt = n.act.desde ? terminalTexto(n.act.desde, agora) : null;
  return tt ? { acao: "💻 " + tt.texto, preso: tt.preso, tt } : { acao: actText(n.act), preso: false, tt: null };
}

// estado de um agente (líder ou subagente, n = {act, stuck}) numa tarefa: preso | trabalhar | terminal | terminal-lento | parado.
// Uma só regra para a lista «Agentes» e para o desenho («z», «preso?»): um Bash em 1.º plano sem resultado é «à espera do
// terminal», nunca «parado» (CA-20, CA-25)
export function estadoAgente(t, role, n, agora = Date.now()) {
  const { preso, tt } = acaoDe(n, agora);
  return n?.stuck || preso ? "preso" : (t?.working || []).includes(role) ? "trabalhar" : tt ? (tt.classe === "s-warn" ? "terminal-lento" : "terminal") : "parado";
}

// agentes das tarefas a correr de todas as squads (líder + subagentes não terminados), agrupados por projecto; projectos por
// ordem alfabética, sem projecto no fim; dentro do projecto pela ordem das salas (salas = ids das squads da config). Cada linha
// leva o seu nó ({act, stuck, tarefas…}) em «n», não enumerável
export function agentesDe(s, agora = Date.now(), salas = []) {
  const grupos = new Map(), sala = (t) => (salas.includes(t?.squad) ? t.squad : salas[0] ?? t?.squad);
  for (const t of (s?.tasks || []).filter((x) => x.running)) {
    const squad = sala(t), base = { squad, taskId: t.id, project: t.project || "", titulo: t.titulo || "" };
    const um = (role, n) => Object.defineProperty({ ...base, role, estado: estadoAgente(t, role, n, agora), acao: acaoDe(n, agora).acao }, "n", { value: n });
    if (!grupos.has(base.project)) grupos.set(base.project, { project: base.project, agentes: [] });
    const g = grupos.get(base.project).agentes;
    if (t.lead?.role) g.push(um(t.lead.role, t.lead));
    for (const a of t.agents || []) if (!a.finished) g.push(um(a.role, a));
  }
  const ord = (x) => salas.indexOf(x.squad);
  return [...grupos.values()].map((g) => ({ ...g, agentes: g.agentes.slice().sort((a, b) => ord(a) - ord(b)) }))
    .sort((a, b) => (!a.project - !b.project) || a.project.localeCompare(b.project, "pt", { sensitivity: "base" }));
}

// «4/7» (CA-45): só a contagem do TodoWrite/TaskCreate/TaskUpdate do agente, nunca o texto das tarefas
export function progressoTexto(x) {
  const f = x?.feitas, n = x?.total;
  return Number.isInteger(f) && Number.isInteger(n) && n > 0 && f >= 0 && f <= n ? { texto: `${f}/${n}`, aria: `${f} de ${n} tarefas feitas` }
    : { texto: "—", aria: "sem lista de tarefas" };
}
// linhas alteradas («≈» quando estimadas), modelo e ramo de um agente (CA-46), já escapados; «—» no que falta; ramo > 32 cortado
export function extrasTexto(n) {
  const l = n?.linhas, ok = (v) => Number.isInteger(v) && v >= 0, ramo = typeof n?.ramo === "string" && n.ramo ? n.ramo : null;
  return { linhas: l && ok(l.mais) && ok(l.menos) ? esc(`${l.aprox ? "≈ " : ""}+${l.mais} −${l.menos}`) : "—",
    modelo: typeof n?.modelo === "string" && n.modelo ? esc(n.modelo) : "—",
    ramo: ramo ? esc(ramo.length > 32 ? ramo.slice(0, 31) + "…" : ramo) : "—", ramoCompleto: ramo ? esc(ramo) : "—" };
}
// só as partes que existem, « · » só entre elas; "" se faltam as três (a linha não aparece)
export function extLinha(n) {
  const x = extrasTexto(n), cortado = x.ramo !== x.ramoCompleto;
  return [x.linhas !== "—" && `linhas ${x.linhas}`, x.modelo !== "—" && x.modelo,
    x.ramo !== "—" && (cortado ? `<span title="${x.ramoCompleto}">${x.ramo}</span>` : x.ramo)].filter(Boolean).join(" · ");
}
const pgH = (x) => { const p = progressoTexto(x); return `<span class="pg" role="img" aria-label="${esc(p.aria)}">${esc(p.texto)}</span>`; };
const extH = (n) => { const x = extLinha(n); return x ? `<span class="ext">${x}</span>` : ""; };

// pesquisa por nome, papel, projecto, título ou id da tarefa, sem maiúsculas nem acentos (nome(role, squad) → {name, short, full})
export function filtraAgentes(grupos, q, nome = () => ({})) {
  const f = fold(q).trim();
  if (!f) return grupos;
  return grupos.map((g) => ({ ...g, agentes: g.agentes.filter((a) => {
    const r = nome(a.role, a.squad) || {};
    return fold([r.name, r.short, r.full, a.role, a.project, a.titulo, a.taskId, a.acao].join(" ")).includes(f);
  }) })).filter((g) => g.agentes.length);
}

// «/» foca a pesquisa de «Agentes» (CA-26): só no Escritório (no Quadro e na Biblioteca não), fora de campos de texto, sem
// modificadores e sem diálogo aberto
export function barraValida(e, activo, dialogo, vista) {
  if (!e || e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.isComposing || dialogo) return false;
  if (vista === "quadro" || vista === "biblioteca") return false;
  const tag = String(activo?.tagName || "").toUpperCase();
  return !(["INPUT", "TEXTAREA", "SELECT"].includes(tag) || activo?.isContentEditable === true);
}

// entregas «por ler» (CA-35): terminadas há ≤ 24 h, sem dry-run e fora das lidas; o número vem do /api/state, não da rota
export const chaveT = (t) => `${t.squad}/${t.id}`;
export const recentes = (tasks, agora) => (tasks || []).filter((t) => !t.running && t.finished && t.mode !== "dry-run"
  && agora - Date.parse(t.finished) <= 864e5);
export const porLer = (tasks, lidas, agora) => recentes(tasks, agora).map(chaveT).filter((k) => !lidas.has(k));
export const MAX_LIDAS = 200;
// lidas no localStorage: só strings, no máximo 200 (as mais recentes ficam no fim)
export const lerLidas = (raw) => { try { const a = JSON.parse(raw); return new Set(Array.isArray(a) ? a.filter((x) => typeof x === "string").slice(-MAX_LIDAS) : []); } catch { return new Set(); } };
export const limpaLidas = (lidas) => [...lidas].slice(-MAX_LIDAS);

// ---- DOM

let A = null, st = null, sep = "tarefa", seg = null, q = "", entP = null, lidas = new Set();
const $ = (s) => document.querySelector(s);
const SEPS = [["tarefa", "Tarefa", "info-t"], ["agentes", "Agentes", "info-a"], ["entregas", "Entregas", "info-e"]];
const ls = (k, v) => { try { return v === undefined ? localStorage.getItem(k) : localStorage.setItem(k, v); } catch { return null; } };
const setHtml = (el, h) => { if (el._h !== h) { el.innerHTML = h; el._h = h; } };
const reduz = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const salas = () => Object.keys(A.ROOMS());

// api = { esc, ROOMS(), cfg(), rl(role, sala), label(role, sala), sq, roleOf, leadOf, nowOf, agoraH, fmtT, fmtDur, demo(), replay(),
//         getState(), sala(), tarefa(), picked(), abrirTarefa(id), seguir(squad, id, role), md }
export function iniciar(api) {
  A = api;
  const s = $("#info-s"), info = $("#info");
  s.innerHTML = `<div role="tablist" aria-label="Painel lateral" class="sep">${SEPS.map(([k, r, p]) =>
    `<button type="button" role="tab" id="sep-${k}" data-s="${k}" aria-controls="${p}">${r}</button>`).join("")}</div>`;
  $("#info-t").setAttribute("aria-labelledby", "sep-tarefa");
  info.insertAdjacentHTML("beforeend", `<div id="info-a" role="tabpanel" aria-labelledby="sep-agentes" hidden>`
    + `<input id="ag-q" type="search" aria-keyshortcuts="/" aria-label="Pesquisar agentes por nome, papel, projecto ou acção" placeholder="pesquisar agente ( / )" autocomplete="off">`
    + `<div id="ag-l"></div></div><div id="info-e" role="tabpanel" aria-labelledby="sep-entregas" hidden></div>`);
  s.addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) separador(b.dataset.s); });
  s.addEventListener("keydown", (e) => {
    const i = SEPS.findIndex(([k]) => k === sep), d = { ArrowLeft: -1, ArrowRight: 1 }[e.key], n = e.key === "Home" ? 0 : e.key === "End" ? 2 : d ? (i + d + 3) % 3 : -1;
    if (n < 0) return;
    e.preventDefault();
    separador(SEPS[n][0]);
    $(`#sep-${sep}`).focus();
  });
  $("#ag-q").addEventListener("input", (e) => { q = e.target.value; pintarAgentes(); });
  // «Seguir» não responde durante o replay (o desenho é do passado)
  $("#ag-l").addEventListener("click", (e) => { const b = e.target.closest("[data-k]"); if (b && !A.replay?.()) seguir(b.dataset.k); });
  // «Tarefa» só por acção do utilizador (o poll nunca muda de separador): outra sala, outra tarefa ou uma secretária
  $("#rooms").addEventListener("click", (e) => { const b = e.target.closest("[data-room]"); if (b && b.dataset.room !== A.sala()) separador("tarefa"); }, true);
  $("#tabs").addEventListener("click", (e) => e.target.closest("[data-id]") && separador("tarefa"));
  $("#office").addEventListener("click", (e) => e.target.closest(".seat") && separador("tarefa"));
  $("#office").addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && e.target.closest(".seat") && separador("tarefa"));
  document.addEventListener("click", () => setTimeout(anel));
  document.addEventListener("keydown", (e) => {
    if (barraValida(e, document.activeElement, !!document.querySelector("dialog[open]"), document.body.dataset.view)) { e.preventDefault(); abrirAgentes(); }
  });
  lidas = lerLidas(ls("entregasLidas"));
  separador("tarefa"); // arranca sempre em «Tarefa» (CA-24)
}

export function render(s) {
  if (!A || !s) return;
  st = s;
  rotulos();
  if (sep === "agentes") pintarAgentes();
  anel();
}
export const separadorActual = () => sep;

// o «z» do desenho (desk no index.html): só com o mesmo estado «parado» da lista «Agentes»
export const parado = (t, role, agora) => estadoAgente(t, role, A?.nowOf(t, role), agora) === "parado";

function rotulos() {
  const n = contadores(st).agentes, m = porLer(st?.tasks, lidas, Date.now()).length;
  setHtml($("#sep-agentes"), n == null ? "Agentes" : `Agentes <span class="mono">(${n})</span>`);
  setHtml($("#sep-entregas"), m ? `Entregas <span class="mono">(${m} por ler)</span>` : "Entregas");
}

export function separador(k) {
  const mudou = sep !== k;
  sep = k;
  for (const [s, , p] of SEPS) {
    const b = $(`#sep-${s}`), on = s === k;
    b.setAttribute("aria-selected", on);
    b.tabIndex = on ? 0 : -1;
    $("#" + p).hidden = !on;
  }
  if (mudou) $("#info").scrollTop = 0;
  if (k === "agentes") pintarAgentes();
  if (k === "tarefa" && mudou) A.repintar?.();
  if (k === "entregas" && A.demo?.()) $("#info-e").innerHTML = `<p class="sub">Entregas indisponível no modo demonstração.</p>`; // CA-49: sem pedido
  else if (k === "entregas") (entP ||= import("./entregas.js")).then((m) => m.abrir($("#info-e"), entApi()), () => { entP = null; $("#info-e").textContent = "Não foi possível carregar as entregas."; });
}

// contadores da barra («agentes», «a trabalhar», «subagentes») e «/»: abre «Agentes» com o foco na pesquisa
export function abrirAgentes() {
  separador("agentes");
  $("#info").scrollIntoView({ block: "nearest" });
  $("#ag-q").focus({ preventScroll: true });
}

const EST = { trabalhar: ["s-run", "a trabalhar"], parado: ["ag-p", "parado"], preso: ["s-bad", "preso?"],
  terminal: ["s-run", "à espera do terminal"], "terminal-lento": ["s-warn", "à espera do terminal"] };
function pintarAgentes() {
  if (!st) return;
  const k = document.activeElement?.closest?.("#ag-l [data-k]")?.dataset.k; // o poll não tira o foco a um «Seguir»
  const todos = agentesDe(st, Date.now(), salas()), g = filtraAgentes(todos, q, A.rl), P = A.cfg().projectos || [];
  const tit = (p) => { const c = P.find((x) => x.id === p); return p ? `${c?.glifo ? c.glifo + " " : ""}${c?.nome || p}` : "sem projecto"; };
  const h = !todos.length ? `<p class="sub">Nenhum agente a trabalhar agora.</p>` : !g.length ? `<p class="sub">Nenhum agente com esta pesquisa.</p>`
    : g.map((x) => `<section class="ag-g"><h3>${esc(tit(x.project))}</h3><ul>${x.agentes.map((a) => {
      const r = A.rl(a.role, A.ROOMS()[a.squad]), e = EST[a.estado];
      return `<li><span class="ag-e ${e[0]}"><i></i>${e[1]}</span><span class="ag-n"><b>${esc(r.name)}</b> <span class="sub">${esc(r.short)} · ${esc(A.ROOMS()[a.squad]?.name)}</span> ${pgH(a.n?.tarefas)}`
        + `<span class="ag-a">${esc(a.acao)}</span></span><button type="button" data-k="${esc(`${a.squad}/${a.taskId}/${a.role}`)}" `
        + `aria-label="${esc(`Seguir ${r.name}, ${tit(x.project)}`)}">Seguir</button></li>`;
    }).join("")}</ul></section>`).join("");
  setHtml($("#ag-l"), h);
  if (k && !document.activeElement?.closest?.("#ag-l")) [...$("#ag-l").querySelectorAll("[data-k]")].find((b) => b.dataset.k === k)?.focus({ preventScroll: true });
}

// «Tarefa»: o líder e os subagentes (mais recentes primeiro), cada um com «4/7» e, a correr ou terminado há ≤ 3 dias, a linha
// mono «linhas · modelo · ramo» (CA-45, 46); o index.html chama-a no infoHtml (sem este módulo, a lista de antes)
export function agentesTarefaH(t, agora = Date.now()) {
  const recente = (fim) => !fim || !(agora - Date.parse(fim) > 3 * 864e5), L = A.leadOf(t), R = A.ROOMS()[A.sq(t)];
  const li = (role, n, desc, extra, hora, act, fim) => `<li class="${act ? "act" : ""}"><i style="background:${A.rl(role, R).shirt}"></i><span><b>${esc(A.label(role, R))}</b> — ${esc(desc)}`
    + `${extra}${recente(fim) ? extH(n) : ""}</span><span class="t">${pgH(n?.tarefas)} · ${hora}</span></li>`;
  const lr = A.roleOf(L.lead);
  let h = li(lr, t.lead, t.running ? L.coord : "líder", "", `${A.fmtT(t.started)} · ${t.running ? "agora" : A.fmtDur(t.started, t.finished)}`,
    t.running && (t.working || []).includes(L.lead), t.running ? null : t.finished);
  const subs = (t.agents || []).slice().reverse().map((a) => {
    const now = t.running && !a.finished && (a.act || a.stuck) ? `<br><span class="sub">Agora: ${A.agoraH(a)}</span>` : "";
    return li(A.roleOf(a.role), a, a.desc, now, `${A.fmtT(a.start)}${a.active ? " · agora" : " · " + A.fmtDur(a.start, a.end || a.last)}`, a.active, a.finished ? a.end || a.last : null);
  }).join("");
  h += subs || `<li><i></i><span class="sub">${t.mode === "task" ? "Tarefa de um só engenheiro (sem subagentes)." : "Ainda sem passagens."}</span><span></span></li>`;
  return `<ol>${h}</ol>`;
}

// «Seguir» (CA-27): o mesmo que clicar na secretária (sala, tarefa, #who), mais um anel e a secretária ao centro
function seguir(k) {
  const [squad, ...r] = k.split("/"), role = r.pop(), id = r.join("/");
  A.seguir(squad, id, role);
  seg = { s: A.sala(), id: A.tarefa(), p: A.picked() };
  separador("tarefa");
  anel();
  const g = $(`#office .seat[data-role="${CSS.escape(seg.p)}"]`);
  g?.scrollIntoView({ block: "center", behavior: reduz() ? "auto" : "smooth" });
  ($("#who button") || g)?.focus({ preventScroll: true });
}
// o anel só existe enquanto a sala, a tarefa e a secretária escolhida forem as do agente seguido
function anel() {
  if (seg && (A.picked() !== seg.p || A.sala() !== seg.s || A.tarefa() !== seg.id)) seg = null;
  const g = seg && $(`#office .seat[data-role="${CSS.escape(seg.p)}"]`), tem = $("#office .seguir");
  if (tem && tem.parentNode === g) return;
  tem?.remove();
  if (!g) return;
  const base = g.querySelector(":scope > rect"), n = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  for (const a of ["x", "y", "width", "height"]) n.setAttribute(a, base.getAttribute(a));
  n.setAttribute("class", "seguir");
  n.innerHTML = "<title>a seguir</title>";
  g.append(n);
}

// ---- entregas por ler (o entregas.js usa estas para marcar)
function entApi() {
  return { esc, getState: () => st, ROOMS: A.ROOMS, cfg: A.cfg, fmtT: A.fmtT, abrirTarefa: A.abrirTarefa, separador,
    lida: (k) => lidas.has(k), porLer: () => porLer(st?.tasks, lidas, Date.now()), marcar, marcarTodas };
}
function guardarLidas() { lidas = new Set(limpaLidas(lidas)); ls("entregasLidas", JSON.stringify([...lidas])); rotulos(); }
function marcar(k) { lidas.delete(k); lidas.add(k); guardarLidas(); }
function marcarTodas() { for (const k of porLer(st?.tasks, lidas, Date.now())) lidas.add(k); guardarLidas(); }
