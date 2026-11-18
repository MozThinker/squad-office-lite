// v3 (CA-7…13, 20): barra de contadores, «sem ligação há N s», atalhos P e ? e os destinos de cada item. Carregado no
// arranque (import() no index.html); os painéis (paineis.js) e a ajuda (ajuda.js) só ao clicar. As funções exportadas no
// topo são puras (sem window/document) para os testes em node.
import * as av from "./avisos.js";

// ---- puras

// formatadores: «—» quando não há dado (nunca «0» nem «US$ 0,00» por falta de dado)
const ok = (n) => typeof n === "number" && Number.isFinite(n);
export const fmtNum = (n, d = 0) => (ok(n) ? n.toLocaleString("pt-PT", { minimumFractionDigits: d, maximumFractionDigits: d }) : "—");
export const fmtUsd = (n) => (ok(n) ? "≈ US$ " + fmtNum(n, 2) : "—");

// falhas = tarefas mortas (alerts.count) + execuções com erro (falhas_exec); null sem estado
export function nFalhas(s) {
  if (!s) return null;
  return (ok(s.alerts?.count) ? s.alerts.count : 0) + (Array.isArray(s.falhas_exec) ? s.falhas_exec.length : 0);
}

// só tarefas a correr: salas (squads distintas), agentes (líder + subagentes não terminados), a trabalhar, subagentes;
// à tua espera = espera.n do servidor (o cliente não a recalcula; null se faltar); falhas = nFalhas. Sem estado: tudo null
export function contadores(s, sq = (t) => t.squad) {
  const run = (s?.tasks || []).filter((t) => t.running), vivos = (t) => (t.agents || []).filter((a) => !a.finished).length;
  return {
    salas: s ? new Set(run.map(sq)).size : null,
    agentes: s ? run.reduce((n, t) => n + 1 + vivos(t), 0) : null,
    trabalhar: s ? run.reduce((n, t) => n + (t.working || []).length, 0) : null,
    subagentes: s ? run.reduce((n, t) => n + vivos(t), 0) : null,
    espera: ok(s?.espera?.n) ? s.espera.n : null,
    falhas: nFalhas(s),
  };
}

// CA-20: Bash em 1.º plano sem resultado («desde» = hora do tool_use): normal < 10 min, âmbar 10–25 min, «preso?» aos 25
export function terminalTexto(desde, agora = Date.now()) {
  const m = Math.floor((agora - Date.parse(desde ?? "")) / 60000);
  if (!(m >= 1)) return { texto: "à espera do terminal", classe: "", preso: false };
  const texto = `à espera do terminal há ${m} min`;
  return m < 10 ? { texto, classe: "", preso: false } : m < 25 ? { texto, classe: "s-warn", preso: false } : { texto, classe: "s-bad", preso: true };
}

// CA-7: texto do indicador quando o poll falha (desde = último poll bom ou a abertura da página)
export const semLigacao = (desde, agora) => `sem ligação há ${Math.max(0, Math.round((agora - desde) / 1000))} s`;

// CA-8: P e ? só fora de campos de texto (input, textarea, select, contenteditable), sem Ctrl/Meta/Alt e sem um <dialog> aberto
export function atalhoValido(e, activo, dialogoAberto) {
  if (!e || e.ctrlKey || e.metaKey || e.altKey || e.isComposing || dialogoAberto) return false;
  if (!["p", "P", "?"].includes(e.key)) return false;
  const tag = String(activo?.tagName || "").toUpperCase();
  return !(["INPUT", "TEXTAREA", "SELECT"].includes(tag) || activo?.isContentEditable === true);
}

// a tarefa a correr mais recente (destino de «salas»)
export const maisRecente = (s) => (s?.tasks || []).filter((t) => t.running).sort((a, b) => String(b.started).localeCompare(String(a.started)))[0] || null;

export const ROTULOS = [["salas", "salas"], ["agentes", "agentes"], ["trabalhar", "a trabalhar"], ["subagentes", "subagentes"],
  ["espera", "à tua espera"], ["falhas", "falhas"]];

// ---- DOM

let A = null, barra = null, ultimo = null, okEm = null, tSem = 0, pnP = null, ajP = null;
const T0 = Date.now();
const $ = (s) => document.querySelector(s);

// api = { esc, getState(), sq(t), abrirTarefa(id), verAprovacao(fonte, ficheiro), agentes(), sair(), notificar?, ligadas? }
export function iniciar(api) {
  A = api;
  barra = document.createElement("nav");
  barra.id = "barra";
  barra.setAttribute("aria-label", "Resumo do escritório");
  barra.innerHTML = ROTULOS.map(([k, r]) => `<button type="button" data-k="${k}"${k === "falhas" ? ' aria-live="polite"' : ""}><b>—</b><span>${r}</span></button>`).join("");
  $("header").after(barra);
  barra.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b && b.getAttribute("aria-disabled") !== "true") clique(b); });
  document.addEventListener("keydown", tecla);
  av.iniciar({ esc: A.esc, ir, notificar: A.notificar, ligadas: A.ligadas });
}

export function render(s) {
  if (!barra || !s) return;
  if (s !== ultimo) { if (ultimo) av.poll(ultimo, s); av.accoes(s); ultimo = s; okEm = Date.now(); ligacao(true); }
  const c = contadores(s, A.sq), parado = !maisRecente(s);
  for (const b of barra.querySelectorAll("button")) {
    const k = b.dataset.k, n = c[k], rot = ROTULOS.find((r) => r[0] === k)[1];
    const zero = n === 0 && (k === "espera" || k === "falhas");
    const h = zero ? `<span>${k === "espera" ? "nada à tua espera" : "sem falhas"}</span>`
      : `<b>${n == null ? "—" : (k === "falhas" && n > 0 ? "⚠ " : "") + fmtNum(n)}</b><span>${rot}</span>`;
    if (b._h !== h) { b.innerHTML = h; b._h = h; }
    b.className = n > 0 ? { espera: "warn", falhas: "bad" }[k] || "on" : "";
    if (!["espera", "falhas"].includes(k)) b.setAttribute("aria-disabled", parado);
  }
}

// poll falhado: «sem ligação há N s» no indicador (a cada segundo), até ao 1.º poll bom
export function ligacao(liga) {
  clearInterval(tSem);
  if (liga) return;
  const sp = $("#live span"), pinta = () => { if (sp) sp.textContent = semLigacao(okEm ?? T0, Date.now()); };
  pinta();
  tSem = setInterval(pinta, 1000);
}

// ---- destinos

function focoInfo() {
  const i = $("#info");
  if (!i) return;
  i.tabIndex = -1;
  i.focus({ preventScroll: true });
  i.scrollIntoView({ block: "nearest" });
}
function tarefa(id) {
  if (A.abrirTarefa(id) === false) return av.mostrar({ tipo: "info", chave: "sem:" + id, texto: "Esta tarefa já não está no Escritório." });
  focoInfo();
}
// destino de um item de espera ou de falha (painéis, avisos e P)
export function ir(it) {
  if (!it) return;
  if (it.tipo === "aprovacao") return A.verAprovacao(it.fonte, it.ficheiro);
  if (it.tipo === "pr") return painel("espera", it.chave);
  return tarefa(it.id); // tarefa que morreu, execução com erro
}

function painel(tipo, foco) {
  const b = barra.querySelector(`[data-k="${tipo}"]`);
  (pnP ||= import("./paineis.js")).then((m) => m.abrir(tipo, { esc: A.esc, getState: A.getState, ir, origem: b }, foco), () => { pnP = null; });
}
function clique(b) {
  const k = b.dataset.k;
  if (k === "espera" || k === "falhas") return painel(k);
  if (k !== "salas") { A.sair(); return A.agentes(); } // agentes, a trabalhar e subagentes: o separador «Agentes» do #info
  const t = maisRecente(A.getState());
  if (t) tarefa(t.id);
}
// a ajuda (legenda e atalhos), também do «?»
export function ajuda() { (ajP ||= import("./ajuda.js")).then((m) => m.abrir({ esc: A.esc }), () => { ajP = null; }); }
const dialogo = () => !!document.querySelector("dialog[open], #quadro .q-dr:not([hidden])"); // o painel lateral do Quadro conta como diálogo
function tecla(e) {
  if (!atalhoValido(e, document.activeElement, dialogo())) return;
  e.preventDefault();
  if (e.key === "?") return ajuda();
  const it = av.esperaDe(A.getState())[0];
  if (!it) return av.mostrar({ tipo: "info", chave: "nada", texto: "Nada à tua espera" });
  ir(it);
}
