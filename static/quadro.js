// Quadro (kanban), lazy em #quadro. Funções puras testadas em node; DOM só a partir de abrir(). O servidor decide colunas e ordem.
// HTML: tudo do servidor passa por esc()/md(); «…H()» devolve HTML escapado.
import { rotuloEstado, rotuloDe, TIPOS, TAMANHOS, dicaErro, lancarParado, projectoNovo, comandos, ondeCorre, desvioRelogio } from "./ajuda.js";

export const COL = {
  ideias: ["✦", "Sem ideias guardadas. Quando te lembrares de uma, carrega em + Cartão."],
  "a-fazer": ["○", "Fila vazia. Os briefs novos na inbox aparecem aqui sozinhos."],
  "em-curso": ["◐", "Ninguém a trabalhar agora. Lança uma tarefa e ela aparece aqui."],
  "a-espera": ["◷", "Nada à tua espera. Bom sinal."],
  feito: ["✓", "Ainda nada esta semana. O que terminar vem parar aqui."],
};
export const AV = { aprovacao: ["A", "aprovação"], brief: ["B", "brief da inbox"], manual: ["✎", "cartão manual"] };
// avatar da fonte: squads da config (1.ª letra do nome), senão AV; null = desconhecida
export function avDe(fonte, cfg) {
  const s = (cfg?.squads || []).find((x) => x.id === fonte);
  return s ? [[...String(s.nome)][0]?.toUpperCase() || "?", "squad " + s.nome] : Object.hasOwn(AV, fonte ?? "") ? AV[fonte] : null;
}
// cor do projecto (c0…c7 da config); desconhecido = «outro»
export const corDe = (chave, cfg) => (cfg?.projectos || []).find((p) => p.id === chave)?.cor || "outro";
export const LIM = { titulo: 120, descricao: 2000, etiquetas: 5, etiqueta: 24 }; // = servidor (SPEC §4)
export const MSG409 = "O quadro mudou noutro sítio — recarreguei; repete a acção.";
const CTRL = /[\u0000-\u001f\u007f-\u009f]/, PK = /^[a-z0-9][a-z0-9-]{0,23}$/;

// ---- funções puras

export const normaliza = (s) => String(s ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
export const manual = (c) => c?.tipo === "manual";
const porOrdem = (a, b) => (a.ordem ?? 0) - (b.ordem ?? 0);

// projectos (vazio = todos) e pesquisa em título, etiquetas, projecto e id
export function filtra(cartoes, f = {}, rot = {}) {
  const p = new Set(f.p || []), q = normaliza(f.q);
  return cartoes.filter((c) => (!p.size || p.has(c.projecto))
    && (!q || normaliza([c.titulo, c.id, c.projecto, rot[c.projecto], ...(c.etiquetas || [])].join(" ")).includes(q)));
}

// contadores por coluna (filtros) e por projecto (só pesquisa); antigos não contam
export function contagens(cartoes, f = {}, rot = {}) {
  const col = {}, proj = {};
  let total = 0;
  for (const c of filtra(cartoes, f, rot)) if (!c.antigo) col[c.coluna] = (col[c.coluna] || 0) + 1;
  for (const c of filtra(cartoes, { q: f.q }, rot)) if (!c.antigo) { proj[c.projecto] = (proj[c.projecto] || 0) + 1; total++; }
  return { col, proj, total };
}

// por coluna: manuais primeiro por «ordem», depois os automáticos pela ordem do servidor
export function porColuna(cartoes, colunas) {
  const m = Object.fromEntries(colunas.map((c) => [c.chave, []]));
  for (const c of cartoes) m[c.coluna]?.push(c);
  for (const k in m) m[k] = [...m[k].filter(manual).sort(porOrdem), ...m[k].filter((c) => !manual(c))];
  return m;
}

// «agora», «há N min», «há N h» (< 6 h ou de ontem), «hoje», «há 1 dia», «há N dias»
export function idade(desde, agora = Date.now()) {
  const t = Date.parse(desde ?? ""), s = (agora - t) / 1000;
  if (Number.isNaN(t)) return "";
  if (s < 60) return "agora";
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return s < 21600 || new Date(t).toDateString() !== new Date(agora).toDateString() ? `há ${Math.floor(s / 3600)} h` : "hoje";
  const d = Math.floor(s / 86400);
  return d === 1 ? "há 1 dia" : `há ${d} dias`;
}

export const atrasado = (c, agora = Date.now()) => c?.coluna === "a-espera" && agora - Date.parse(c.desde ?? "") > 3 * 864e5;
export const custo = (n) => (typeof n === "number" && Number.isFinite(n) ? "US$ " + n.toFixed(2).replace(".", ",") : "");

// «a, b ,A» → ["a", "b"]: sem vazios nem repetidos (sem maiúsculas)
export function parseEtiquetas(s) {
  const out = [], vistos = new Set();
  for (const e of String(s ?? "").split(",")) {
    const t = e.trim(), k = t.toLowerCase();
    if (t && !vistos.has(k)) { vistos.add(k); out.push(t); }
  }
  return out;
}

// erros por campo ({} = válido); tamanhos em code points, como o len() do Python
export function validaForm(v) {
  const e = {}, t = String(v.titulo ?? "").trim(), et = v.etiquetas || [], n = (s) => [...s].length;
  if (!t) e.titulo = "O título é obrigatório.";
  else if (n(t) > LIM.titulo) e.titulo = `Máximo ${LIM.titulo} caracteres.`;
  else if (CTRL.test(t)) e.titulo = "Sem caracteres de controlo.";
  if (n(String(v.descricao ?? "").replace(/\r\n?/g, "\n")) > LIM.descricao) e.descricao = `Máximo ${LIM.descricao} caracteres.`;
  if (et.length > LIM.etiquetas) e.etiquetas = `Máximo ${LIM.etiquetas} etiquetas.`;
  else if (et.some((x) => n(x) > LIM.etiqueta || CTRL.test(x))) e.etiquetas = `Cada etiqueta tem até ${LIM.etiqueta} caracteres.`;
  return e;
}

// texto do formulário antes de enviar: C0/C1 saem; na descrição ficam \n e \t (o servidor aceita), nos outros o tab vira espaço
export const limpaTexto = (s, nl) => (nl ? String(s ?? "").replace(/\r\n?/g, "\n").replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g, "")
  : String(s ?? "").replace(/\t/g, " ").replace(/[\u0000-\u001f\u007f-\u009f]/g, ""));

// mover optimista: array novo (o antigo reverte); renumera o destino 10, 20, 30… como o servidor
export function moverLocal(cartoes, id, coluna, posicao) {
  const c = cartoes.find((x) => x.id === id);
  if (!manual(c)) return cartoes;
  const dest = cartoes.filter((x) => manual(x) && x.coluna === coluna && x.id !== id).sort(porOrdem);
  dest.splice(Math.max(0, Math.min(posicao, dest.length)), 0, c);
  const nova = new Map(dest.map((x, i) => [x.id, (i + 1) * 10]));
  return cartoes.map((x) => (nova.has(x.id) ? { ...x, coluna, ordem: nova.get(x.id) } : x));
}

// filtro/antigos escondem manuais: a posição para o servidor é global, «antes do manual recente seguinte»;
// sem seguinte (ou a largar na zona dos antigos de Feito) = depois do último manual recente
export function posGlobal(cartoes, id, coluna, seguinte) {
  const dest = cartoes.filter((x) => manual(x) && x.coluna === coluna && x.id !== id).sort(porOrdem), i = dest.findIndex((x) => x.id === seguinte);
  return i >= 0 ? i : dest.findLastIndex((x) => !x.antigo) + 1;
}

// índice do 1.º cartão cujo meio fica abaixo do ponteiro
export function posicaoDrop(meios, y) {
  let i = 0;
  while (i < meios.length && meios[i] < y) i++;
  return i;
}

// teclado: ↑/↓ posição, ←/→ coluna; ns = manuais por coluna sem o agarrado; nos limites fica
export function alvoTeclado(cols, ns, at, tecla) {
  const n = (k) => ns[k] || 0, i = cols.indexOf(at.coluna);
  if (tecla === "ArrowUp") return { ...at, posicao: Math.max(0, at.posicao - 1) };
  if (tecla === "ArrowDown") return { ...at, posicao: Math.min(n(at.coluna), at.posicao + 1) };
  const j = i + (tecla === "ArrowLeft" ? -1 : tecla === "ArrowRight" ? 1 : 0);
  if (j === i || j < 0 || j >= cols.length) return at;
  return { coluna: cols[j], posicao: Math.min(at.posicao, n(cols[j])) };
}

// filtros em localStorage (pode lançar em modo privado ou ter lixo)
export function lerPrefs(st) {
  try {
    const v = JSON.parse(st?.getItem("quadro") || "{}");
    return { p: Array.isArray(v.p) ? [...new Set(v.p.filter((x) => typeof x === "string" && PK.test(x)))].slice(0, 20) : [],
      q: typeof v.q === "string" ? v.q.slice(0, 100) : "" };
  } catch { return { p: [], q: "" }; }
}
export function gravarPrefs(st, f) {
  try { st?.setItem("quadro", JSON.stringify({ p: f.p || [], q: f.q || "" })); return true; } catch { return false; }
}

// 2.ª barreira antes de um href: só PRs do GitHub
export const safePr = (u) => (/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+$/.test(String(u ?? "")) ? String(u) : "");

export function fmtData(s) {
  const t = Date.parse(s ?? ""), d = new Date(t), p = (n) => String(n).padStart(2, "0");
  return Number.isNaN(t) ? String(s ?? "") : `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
export const fmtDur = (s) => { s = Math.round(Number(s) || 0); return s < 60 ? `${s} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1).replace(".", ",")} h`; };

// ---- lançar (cartão manual → «Em curso»): mesmas regras do servidor (lancar.py); squads e projectos lançáveis vêm da config
// squads lançáveis → { id: { nome, kind, mode } } (ordem da config)
export function camposLancar(cfg) {
  const ok = cfg?.lancar?.squads || [];
  return Object.fromEntries((cfg?.squads || []).filter((s) => ok.includes(s.id)).map((s) => [s.id, { nome: s.nome, kind: [...s.tipos], mode: [...s.modos] }]));
}
export const BRIEF_MAX = 8192;
const SLUG = /^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$/, CTRL_BRIEF = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/;
// = servidor (o ZWJ fica)
const INV = /(?!\u200d)[\p{Cf}\p{Co}\p{Cs}]/u, INV_MSG = "Tem caracteres invisíveis; apaga-os.";

// «Lançar trabalho — Quadro (v2)!» → «lancar-trabalho-quadro-v2»; > 40 corta na última fronteira «-»
export function slugDe(titulo) {
  let s = normaliza(titulo).replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (s.length > 40) { const i = s.lastIndexOf("-", 40); s = s.slice(0, i > 0 ? i : 40); }
  return s;
}

// sugestão: 1.ª squad lançável e o projecto do cartão se for lançável (senão null = «Escolhe…»)
export function sugestao(projecto, cfg) {
  const [squad = null, c] = Object.entries(camposLancar(cfg))[0] || [];
  return { squad, projecto: (cfg?.lancar?.projectos || []).includes(projecto) ? projecto : null, kind: c?.kind[0] ?? null, mode: c?.mode[0] ?? null };
}

// corpo do POST (sem token/versao/cartao_id/posicao); projecto = id da config
export function corpoLancar(v) {
  return { squad: v.squad, projecto: v.projecto, kind: v.kind, mode: v.mode, slug: v.slug, titulo: v.titulo, brief: v.brief };
}

export const bytes = (s) => new TextEncoder().encode(String(s ?? "")).length;
export const kbFaltam = (brief) => Math.floor((BRIEF_MAX - bytes(brief)) / 102.4) / 10; // KB com uma casa, por baixo

// erros por campo ({} = pode lançar)
export function validaLancar(v, cfg) {
  const cs = camposLancar(cfg), e = {}, cp = cs[v.squad], t = String(v.titulo ?? "").replace(/\r\n?/g, "\n").trim(), b = String(v.brief ?? "").replace(/\r\n?/g, "\n");
  if (!Object.hasOwn(cs, v.squad ?? "")) e.squad = "Escolhe a squad.";
  else {
    if (!(cfg?.lancar?.projectos || []).includes(v.projecto)) e.projecto = "Escolhe o projecto.";
    if (!cp.kind.includes(v.kind)) e.kind = "Tipo inválido.";
    if (!cp.mode.includes(v.mode)) e.mode = "Modo inválido.";
  }
  if (!SLUG.test(String(v.slug ?? ""))) e.slug = "3 a 60 caracteres: a-z, 0-9 e «-» (não nas pontas).";
  if (!t) e.titulo = "O título é obrigatório.";
  else if ([...t].length > LIM.titulo) e.titulo = `Máximo ${LIM.titulo} caracteres.`;
  else if (CTRL.test(t)) e.titulo = "Sem caracteres de controlo.";
  else if (INV.test(t)) e.titulo = INV_MSG;
  if (bytes(b) > BRIEF_MAX) e.brief = "Máximo 8 KB.";
  else if (CTRL_BRIEF.test(b)) e.brief = "Sem caracteres de controlo.";
  else if (INV.test(b)) e.brief = INV_MSG;
  return e;
}

// C13: «Lançar…» no menu ⋯ com a fila ligada, manual e sem lançamento activo; ao contrário de arrastar (deveLancar),
// também um que já está em «Em curso» sem lançamento (ex.: depois de «Só mover»)
export const podeLancarMenu = (c, lancar) => !!lancar && manual(c) && (!c.lancamento || c.lancamento.estado === "erro");

// C13: dica de arrastar só em ecrã de toque e até «Percebi» (sem localStorage: uma vez por carregamento, ver rota())
export function verDica(st, toque) {
  if (!toque) return false;
  try { return st?.getItem("dicaArrastar") !== "1"; } catch { return true; }
}
export const gravarDica = (st) => { try { st?.setItem("dicaArrastar", "1"); } catch {} };

// D6/D13: só um manual que entra em «Em curso» vindo de outra coluna e sem lançamento activo (pendente/ligado)
export const deveLancar = (c, destino) => manual(c) && destino === "em-curso" && c.coluna !== "em-curso" && (!c.lancamento || c.lancamento.estado === "erro");

// o POST /api/quadro não traz «lancamento» nem o que um ligado herda do automático: fica o que o GET deu
export function fundeManuais(antigos, novos) {
  const m = new Map(antigos.map((c) => [c.id, c]));
  return novos.map((c) => {
    const a = m.get(c.id), l = "lancamento" in c ? c.lancamento : a?.lancamento ?? null;
    return a && l?.estado === "ligado" && a.lancamento?.id === l.id ? { ...c, lancamento: l, progress: a.progress, links: a.links, selo: a.selo } : { ...c, lancamento: l };
  });
}

// ---- DOM

let A = null, esc = (s) => String(s ?? ""), D = null; // D = arrasto por ponteiro em curso
const S = { d: null, f: { p: [], q: "" }, rot: {}, pj: {}, mais: 0, seq: 0, poll: 0, dr: null, menu: null, k: null, colDe: null, on: false, erro: false };
const PS = { preventScroll: true };
const q = (s) => A.root.querySelector(s), qa = (s) => [...A.root.querySelectorAll(s)];
const el = (id) => q(`.q-board .q-card[data-id="${CSS.escape(id)}"]`);
const agora = () => Date.now();
const agoraSrv = () => Date.now() + (S.desvio || 0); // para comparar com datas do servidor (desvio pelo «agora» do GET)
const doctorTxt = () => { const o = ondeCorre(A.cfg?.modo); return `${o[0].toUpperCase() + o.slice(1)}: ${comandos(A.cfg?.modo).doctor}`; }; // «No servidor: …» / «No PC: …»
const reduz = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const largo = () => matchMedia("(min-width: 900px)").matches;
const cols = () => S.d?.colunas.map((c) => c.chave) || [];
const rotCol = (k) => S.d?.colunas.find((c) => c.chave === k)?.rotulo || k;
const cartao = (id) => S.d?.cartoes.find((c) => c.id === id);
const pj = (k) => S.pj[k] || { chave: k, rotulo: k, icone: "·", cor: "outro" };
const editavel = (c) => manual(c) && S.d?.escrita;
const nMan = (coluna, sem) => S.d.cartoes.filter((x) => manual(x) && x.coluna === coluna && x.id !== sem).length;
// o que se desenha numa coluna: Feito esconde os antigos além de «Ver mais»; visCol = só os manuais, pela ordem desenhada
const vis = (l) => [...l.filter((c) => !c.antigo), ...l.filter((c) => c.antigo).slice(0, S.mais)];
const visCol = (l, col) => vis(porColuna(filtra(l, S.f, S.rot), S.d.colunas)[col] || []).filter((c) => manual(c) && !c.antigo);
const posVis = (l, id, at) => posGlobal(l, id, at.coluna, visCol(l, at.coluna).filter((x) => x.id !== id)[at.posicao]?.id ?? null);
const seg = (e) => { let n = e.nextElementSibling; while (n && (n.dataset.kind !== "manual" || "old" in n.dataset)) n = n.nextElementSibling; return n?.dataset.id ?? null; };
const LOCK = `<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M3 5V4a3 3 0 0 1 6 0v1h1v6H2V5h1zm1 0h4V4a2 2 0 0 0-4 0v1z"/></svg>`;

const SHELL = `<div class="q-top"><div class="q-bar">
<button type="button" class="q-btn q-back" data-a="sair">‹ <span>Escritório</span></button><h2 id="q-h" tabindex="-1">Quadro</h2>
<label class="q-s"><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.6" d="M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zm3.6-1.4L14 14"/></svg>
<input type="search" maxlength="100" placeholder="Pesquisar" aria-label="Pesquisar no quadro" autocomplete="off" aria-keyshortcuts="/"><kbd aria-hidden="true">/</kbd></label>
<button type="button" class="q-btn q-pri q-new" data-a="novo" aria-keyshortcuts="n">+ Cartão</button></div>
<div class="q-chips" role="group" aria-label="Filtrar por projecto"></div><div class="q-tabs" role="tablist" aria-label="Colunas"></div></div>
<p class="q-aviso" role="status" hidden></p>
<p class="q-dica" hidden>Mantém o dedo no cartão para o arrastar. <button type="button" class="q-btn" data-a="percebi">Percebi</button></p><div class="q-board"></div>
<p class="q-hint"><kbd>/</kbd> pesquisar · <kbd>n</kbd> novo · <kbd>1</kbd>–<kbd>5</kbd> coluna · num cartão teu: <kbd>Espaço</kbd> agarra, setas movem, <kbd>Esc</kbd> cancela, <kbd>m</kbd> mover, <kbd>e</kbd> editar</p>
<div class="q-scrim" hidden></div><div class="q-dr" role="dialog" aria-labelledby="q-dh" hidden></div><div class="q-menu" role="menu" hidden></div>
<button type="button" class="q-fab" data-a="novo" aria-label="Novo cartão">+</button><p class="q-toast" aria-hidden="true"></p><p class="q-sr" aria-live="polite"></p>`;

function css() {
  if (document.querySelector("link[data-quadro]")) return Promise.resolve();
  return new Promise((ok) => {
    const l = document.createElement("link");
    Object.assign(l, { rel: "stylesheet", href: new URL("./quadro.css", import.meta.url).href, onload: ok, onerror: ok });
    l.dataset.quadro = "";
    document.head.append(l);
    setTimeout(ok, 3000); // rede lenta: não fica à espera do CSS
  });
}

// api = { root, md, esc, cfg (GET api/config), fechar(), verAprovacao(fonte, ficheiro), abrirTarefa(id), barra(progress) }
export async function abrir(api) {
  A = api; esc = api.esc;
  await css();
  A.root.innerHTML = SHELL;
  S.f = lerPrefs(globalThis.localStorage);
  const inp = q(".q-s input");
  let t = 0;
  inp.value = S.f.q;
  inp.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => filtro({ q: inp.value.trim().slice(0, 100) }), 150); });
  A.root.addEventListener("click", clique);
  A.root.addEventListener("keydown", teclaCartao);
  A.root.addEventListener("pointerdown", pDown);
  A.root.addEventListener("contextmenu", (e) => D && e.preventDefault());
  A.root.addEventListener("submit", guardar);
  A.root.addEventListener("focusout", () => S.k && setTimeout(() => S.k && document.activeElement?.dataset?.id !== S.k.id && soltarK(false, false))); // o foco saiu do agarrado
  document.addEventListener("keydown", teclaGlobal);
  document.addEventListener("touchmove", (e) => D?.on && e.preventDefault(), { passive: false }); // agarrado: o dedo não faz scroll
  A.root.addEventListener("input", lancarInput);
  A.root.addEventListener("change", lancarInput);
  document.addEventListener("visibilitychange", () => !document.hidden && S.on && !pend && S.dr?.modo !== "lancar" && carregar(true));
  pegaFolha();
  q(".q-board").innerHTML = Object.keys(COL).map(() => `<section class="q-col q-sk" aria-hidden="true"><div class="q-ch"><i></i></div><div class="q-list">${SKEL}</div></section>`).join("");
}
const SKEL = `<div class="q-card"><i></i><i></i></div>`.repeat(3);

// cada entrada em #quadro…: poll de 15 s só com o separador visível e nada agarrado/em edição; «#quadro/novo» abre o formulário
export async function rota(h) {
  const ini = !S.on;
  S.on = true;
  clearInterval(S.poll);
  S.poll = setInterval(() => !document.hidden && !D && !S.k && !pend && !/form|apagar|lancar/.test(S.dr?.modo) && carregar(true), 15000);
  if (ini) {
    await carregar(); q("#q-h").focus(PS);
    q(".q-dica").hidden = !(S.d?.escrita && verDica(globalThis.localStorage, matchMedia("(pointer: coarse)").matches)); // uma vez por carregamento
  }
  if (h === "#quadro/novo") { history.replaceState(null, "", "#quadro"); abrirForm(null, "ideias"); }
}

// prefs da barra mudaram
export function repintar() { if (S.on && S.d && !D && !S.k) render(false); }

export function sair() {
  S.on = false;
  clearInterval(S.poll);
  if (D) largar(false);
  if (S.k) soltarK(false);
  fecharMenu();
  fecharDr();
}

async function carregar(poll) {
  const n = ++S.seq, f0 = S.fim;
  try {
    const r = await fetch("api/quadro", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const d = await r.json(), em = Date.now();
    if (n !== S.seq || f0 !== S.fim || D || S.k) return; // resposta antiga, um POST acabou entretanto, ou o utilizador está a arrastar
    d.projectos = d.projectos.map((p) => ({ ...p, cor: corDe(p.chave, A.cfg) })); // cores c0…c7 da config
    S.d = d; S.erro = false; S.desvio = desvioRelogio(d.agora, em);
    if (S.dr?.antes && !S.dr.busy) d.cartoes = moverLocal(d.cartoes, S.dr.id, "em-curso", S.dr.posicao);
    S.rot = Object.fromEntries(d.projectos.map((p) => [p.chave, p.rotulo]));
    S.pj = Object.fromEntries(d.projectos.map((p) => [p.chave, p]));
    S.f.p = S.f.p.filter((k) => S.pj[k]);
    render(poll);
  } catch {
    if (n !== S.seq) return;
    S.erro = true;
    avisos();
    if (!S.d) q(".q-board").innerHTML = "";
  }
}

// ---- desenho

const ico = (s) => String(s ?? "") + "\uFE0E"; // selector de variação de texto: ⚡ e afins nunca como emoji a cores
const tagH = (p) => `<span class="q-tag" data-p="${esc(p.cor)}"><span aria-hidden="true">${esc(ico(p.icone))}</span> ${esc(p.rotulo)}</span>`;
const avH = (c) => { const sq = (A.cfg?.squads || []).some((s) => s.id === c.fonte), [l, nome] = avDe(c.fonte, A.cfg) || AV.manual;
  return `<span class="q-av" data-s="${esc(sq ? "squad" : c.fonte)}" role="img" aria-label="${esc(nome)}" title="${esc(nome)}">${esc(l)}</span>`; };
const seloH = (s) => (s ? `<span class="q-selo" data-c="${esc(s.cor)}">${s.cor === "vermelho" ? "⚠ " : ""}${esc(rotuloEstado(s.texto))}</span>` : "");
const etiqH = (l) => (l || []).map((e) => `<span class="q-et">${esc(e)}</span>`).join("");
const colPillH = (k) => `<span class="q-cp" data-col="${esc(k)}"><span aria-hidden="true">${esc(COL[k]?.[0] || "·")}</span> ${esc(rotCol(k))}</span>`;

function cartaoH(c) {
  const ed = editavel(c), p = pj(c.projecto), w = atrasado(c, agoraSrv()), id = idade(c.desde, agoraSrv()), cu = custo(c.custo_usd), lift = S.k?.id === c.id, sel = S.dr?.id === c.id;
  const sr = [p.rotulo, avDe(c.fonte, A.cfg)?.[1], id, w && "à espera há mais de 3 dias", ed ? "manual, arrastável" : !manual(c) && "automático, muda sozinho",
    lancarParado(c.lancamento, agoraSrv()) ? PARADO : LANC[c.lancamento?.estado]].filter(Boolean).join(", ");
  return `<li class="q-card${w ? " q-late" : ""}${lift ? " q-lift" : ""}${sel ? " q-sel" : ""}" data-id="${esc(c.id)}" data-p="${esc(p.cor)}" data-kind="${ed ? "manual" : "auto"}"${c.antigo ? " data-old" : ""} tabindex="0" aria-roledescription="cartão">`
    + `<div class="q-c1"><h4 class="q-t">${esc(c.titulo)}</h4>${seloH(c.selo)}`
    + `${ed ? `<button type="button" class="q-more" data-a="menu" aria-label="Acções do cartão" aria-haspopup="menu">⋯</button>` : manual(c) ? "" : lockH()}</div>`
    + `<div class="q-meta">${tagH(p)}${avH(c)}${id ? `<span class="q-age">${w ? "⏱ " : ""}${esc(id)}</span>` : ""}${cu ? `<span>${esc(cu)}</span>` : ""}${lancH(c)}${etiqH(c.etiquetas)}</div>`
    + `${barraH(c)}${execH(c)}<span class="q-sr">${esc(sr)}</span></li>`;
}
const barraH = (c) => (c.progress && A.barra ? A.barra(c.progress) : ""); // progHtml escapa
// estado do lançamento (desenhado do GET, nunca gravado em «etiquetas»); ligado com barra não repete a etiqueta
const LANC = { "a-lancar": "a lançar…", ligado: "ligado à execução", erro: "erro ao lançar" }, PARADO = "O runner ainda não pegou nisto";
function lancH(c) {
  const l = c.lancamento;
  if (!l) return "";
  if (lancarParado(l, agoraSrv())) return `<span class="q-et q-le">${esc(PARADO)}</span><button type="button" class="q-lnk q-lx" data-a="retentar">Tentar de novo</button>`;
  if (l.estado === "a-lancar") return `<span class="q-et q-lz">a lançar…</span>`;
  if (l.estado === "erro") return `<span class="q-et q-le">erro ao lançar</span>`;
  return c.progress ? "" : `<span class="q-et">ligado · ${esc(l.task_id)}</span>`;
}
const execH = (c, cls = "q-lnk q-lx") => (manual(c) && c.lancamento?.estado === "ligado" && c.links?.tarefa
  ? `<button type="button" class="${esc(cls)}" data-a="exec">Abrir execução</button>` : "");
const lockH = () => `<button type="button" class="q-lock" data-a="lock" aria-label="Muda sozinho com o estado real">${LOCK}</button>`;

function colunaH(col, lista, n) {
  const k = col.chave, [ic, vazio] = COL[k] || ["·", "Vazio."], filtrado = S.f.p.length || S.f.q;
  const antigos = lista.filter((c) => c.antigo), extra = antigos.slice(0, S.mais);
  const maisH = k === "feito" && antigos.length
    ? (extra.length < antigos.length ? `<button type="button" class="q-btn q-vm" data-a="mais">Ver mais antigos (${esc(antigos.length - extra.length)})</button>` : `<p class="q-fim">É tudo.</p>`) : "";
  const addH = S.d.escrita ? `<button type="button" class="q-add" data-a="novo" data-col="${esc(k)}" aria-label="Novo cartão em ${esc(col.rotulo)}">+</button>` : "";
  return `<section class="q-col" id="q-col-${esc(k)}" data-col="${esc(k)}" aria-labelledby="q-ch-${esc(k)}">`
    + `<div class="q-ch"><span class="q-ci" aria-hidden="true">${esc(ic)}</span><h3 id="q-ch-${esc(k)}">${esc(col.rotulo)}</h3>`
    + `<span class="q-n" aria-label="${esc(n)} cartões">${esc(n)}</span>${addH}</div>`
    + `<ul class="q-list" role="list">${vis(lista).map(cartaoH).join("")}</ul>`
    + `<div class="q-vazio"><span aria-hidden="true">${esc(ic)}</span>${esc(filtrado ? "Nada aqui com estes filtros." : vazio)}</div>${maisH}</section>`;
}

function render(poll) {
  const d = S.d;
  if (!d) return;
  const cnt = contagens(d.cartoes, S.f, S.rot), por = porColuna(filtra(d.cartoes, S.f, S.rot), d.colunas), b = q(".q-board");
  const foc = document.activeElement?.closest?.(".q-board .q-card")?.dataset.id, x = b.scrollLeft, antes = S.colDe;
  const sc = Object.fromEntries(qa(".q-col[data-col]").map((c) => [c.dataset.col, [c.querySelector(".q-list").scrollTop, c.querySelector(".q-n").textContent]]));
  S.colDe = Object.fromEntries(d.cartoes.map((c) => [c.id, c.coluna]));
  flip(() => { b.innerHTML = d.colunas.map((c) => colunaH(c, por[c.chave], cnt.col[c.chave] || 0)).join(""); });
  b.scrollLeft = x;
  for (const c of qa(".q-col[data-col]")) {
    const [st, n] = sc[c.dataset.col] || [0];
    c.querySelector(".q-list").scrollTop = st;
    if (n && n !== c.querySelector(".q-n").textContent) c.querySelector(".q-n").classList.add("q-bump");
  }
  if (poll && antes) {
    const mud = qa(".q-board .q-card[data-id]").filter((e) => antes[e.dataset.id] && antes[e.dataset.id] !== S.colDe[e.dataset.id]);
    mud.forEach((e) => e.classList.add("q-moved"));
    if (mud.length) anuncia(mud.length === 1 ? "1 cartão mudou de coluna." : `${mud.length} cartões mudaram de coluna.`);
  }
  if (foc) el(foc)?.focus(PS);
  A.root.classList.toggle("q-ro", !d.escrita);
  chips(cnt); tabs(cnt); avisos();
  if (S.dr?.modo === "ver" && !q(".q-dr").contains(document.activeElement)) cartao(S.dr.id) ? pintaDr() : fecharDr();
  if (globalThis.IntersectionObserver) { // a 360 px a aba activa segue a coluna à vista (snap)
    S.obs ||= new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && !largo() && marcaTab(e.target.dataset.col)), { root: b, threshold: 0.6 });
    S.obs.disconnect();
    qa(".q-col[data-col]").forEach((c) => S.obs.observe(c));
  }
}

// FLIP: mede, muda o DOM e anima da posição antiga para a nova (só transform)
function flip(fn) {
  const ant = reduz() ? null : new Map(qa(".q-board .q-card[data-id]").map((e) => [e.dataset.id, e.getBoundingClientRect()]));
  fn();
  if (!ant?.size) return;
  for (const e of qa(".q-board .q-card[data-id]")) {
    const a = ant.get(e.dataset.id), r = e.getBoundingClientRect();
    if (!a) { e.classList.add("q-in"); continue; }
    const dx = a.left - r.left, dy = a.top - r.top;
    if (dx || dy) e.animate?.([{ transform: `translate(${dx}px,${dy}px)` }, { transform: "none" }], { duration: dx ? 260 : 160, easing: "cubic-bezier(.2,.8,.2,1)" });
  }
}

// só troca o innerHTML se mudou (não rouba o foco a um chip a meio do clique)
function setHtml(e, h) {
  if (e._h === h) return;
  const k = e.contains(document.activeElement) ? document.activeElement.dataset.k : null;
  e.innerHTML = h; e._h = h;
  if (k !== null && k !== undefined) e.querySelector(`[data-k="${CSS.escape(k)}"]`)?.focus();
}

function chips(cnt) {
  const on = new Set(S.f.p);
  setHtml(q(".q-chips"), `<button type="button" class="q-chip" data-k="" aria-pressed="${esc(!on.size)}">Todos <span class="q-cn">${esc(cnt.total)}</span></button>`
    + S.d.projectos.filter((p) => cnt.proj[p.chave] || on.has(p.chave)).map((p) => `<button type="button" class="q-chip" data-k="${esc(p.chave)}" data-p="${esc(p.cor)}" aria-pressed="${esc(on.has(p.chave))}">`
      + `<span aria-hidden="true">${esc(ico(p.icone))}</span> ${esc(p.rotulo)} <span class="q-cn">${esc(cnt.proj[p.chave] || 0)}</span></button>`).join(""));
}

function tabs(cnt) {
  setHtml(q(".q-tabs"), S.d.colunas.map((c) => `<button type="button" role="tab" class="q-tab" data-k="${esc(c.chave)}" data-col="${esc(c.chave)}" aria-controls="q-col-${esc(c.chave)}" aria-selected="${esc(c.chave === (S.tab || cols()[0]))}" aria-label="${esc(c.rotulo)}, ${esc(cnt.col[c.chave] || 0)} cartões">`
    + `<span aria-hidden="true">${esc(COL[c.chave]?.[0] || "·")}</span><span class="q-tn">${esc(c.rotulo)}</span><span class="q-cn">${esc(cnt.col[c.chave] || 0)}</span></button>`).join(""));
}
const marcaSel = (id) => qa(".q-board .q-card").forEach((e) => e.classList.toggle("q-sel", e.dataset.id === id));
function marcaTab(k) { S.tab = k; qa(".q-tab").forEach((t) => t.setAttribute("aria-selected", t.dataset.col === k)); }

function irCol(k, focar) {
  const c = q(`.q-col[data-col="${CSS.escape(k)}"]`), b = q(".q-board");
  if (!c) return;
  b.scrollTo({ left: c.offsetLeft - (b.clientWidth - c.offsetWidth) / 2, behavior: reduz() ? "auto" : "smooth" });
  marcaTab(k);
  if (focar) (c.querySelector(".q-card") || c.querySelector(".q-add"))?.focus(PS);
}

function avisos() {
  const d = S.d, msgs = [S.erro && "Sem ligação ao Office. A mostrar o último estado.",
    d && !d.escrita && `Só leitura: ${d.aviso || "sem pasta do quadro"}. Não dá para criar nem mover cartões.`,
    d?.invalidos && `${d.invalidos} ${d.invalidos === 1 ? "cartão inválido ignorado" : "cartões inválidos ignorados"} no cartoes.json.`,
    S.doctor && d?.cartoes.some((c) => lancarParado(c.lancamento, agoraSrv())) && doctorTxt()].filter(Boolean);
  q(".q-aviso").hidden = !msgs.length;
  setHtml(q(".q-aviso"), msgs.map(esc).join(" ") + (S.erro ? ` <button type="button" class="q-lnk" data-a="recarregar">Tentar de novo</button>` : ""));
}

// erros fora do formulário: na faixa de aviso durante uns segundos
function aviso(msg) {
  q(".q-aviso").hidden = false;
  setHtml(q(".q-aviso"), `<strong>${esc(msg)}</strong>`);
  anuncia(msg);
  clearTimeout(S.tAv);
  S.tAv = setTimeout(avisos, 6000);
}

let tLive = 0, tToast = 0;
function anuncia(t) {
  const e = q("p.q-sr");
  e.textContent = "";
  clearTimeout(tLive);
  tLive = setTimeout(() => (e.textContent = t), 30);
}
function toast(t) {
  const e = q(".q-toast");
  e.textContent = t; e.classList.add("q-on");
  anuncia(t);
  clearTimeout(tToast);
  tToast = setTimeout(() => e.classList.remove("q-on"), 2000);
}

function filtro(m) {
  Object.assign(S.f, m);
  gravarPrefs(globalThis.localStorage, S.f);
  render();
  const n = qa(".q-board .q-card[data-id]").length;
  anuncia(`${n} ${n === 1 ? "cartão" : "cartões"}${S.f.p.length ? " de " + S.f.p.map((k) => pj(k).rotulo).join(", ") : ""}.`);
}

// ---- cliques (delegados)

function clique(e) {
  const b = e.target.closest("[data-a], .q-chip, .q-tab, [role=menuitem]"), card = e.target.closest(".q-board .q-card[data-id]"), a = b?.dataset.a;
  if (b?.matches("[role=menuitem]")) return escolheMenu(b);
  if (b?.classList.contains("q-chip")) { const k = b.dataset.k; return filtro({ p: !k ? [] : S.f.p.includes(k) ? S.f.p.filter((x) => x !== k) : [...S.f.p, k] }); }
  if (b?.classList.contains("q-tab")) return irCol(b.dataset.col);
  const acc = { sair: () => A.fechar(), novo: () => abrirForm(null, b.dataset.col), menu: () => abrirMenu(b, card.dataset.id, true),
    mais: () => { S.mais += 20; render(); }, recarregar: () => carregar(), fechar: () => fecharDr(true), editar: () => abrirForm(S.dr.id),
    mover: () => abrirMenu(b, S.dr.id, false), apagar: () => confirma(S.dr.id), sim: () => apagar(S.dr.id),
    nao: () => { S.dr.modo = "ver"; pintaDr(); q(".q-dr [data-a=apagar]")?.focus(); },
    cancelar: () => (S.dr?.id ? (S.dr.modo = "ver", pintaDr(), q("#q-dh").focus()) : fecharDr(true)),
    apr: () => { const l = cartao(S.dr.id)?.links?.aprovacao; l && A.verAprovacao(l.fonte, l.ficheiro); },
    tarefa: () => { const t = cartao(card?.dataset.id ?? S.dr.id)?.links?.tarefa; if (t && A.abrirTarefa(t) === false) aviso("Esta execução já não está no escritório."); },
    exec: () => acc.tarefa(), lock: () => {},
    percebi: () => { gravarDica(globalThis.localStorage); q(".q-dica").hidden = true; q("#q-h").focus(PS); },
    retentar: async () => { S.doctor = true; await carregar(); avisos(); anuncia(doctorTxt()); }, // C12.2: só relê, nunca POST
    lcancel: () => !S.dr.busy && fecharDr(true),
    lmover: () => { if (S.dr.busy) return; const { id, posicao } = S.dr, a = deLancar(S.dr); S.dr.enviado = true; fecharDr(); moverJa(a, id, "em-curso", posicao); } };
  if (acc[a]) return acc[a]();
  if (card && !e.target.closest("a")) return agora() - (S.tDrop || 0) > 300 && abrirDr(card.dataset.id); // não abre no fim de um arrasto
  if (S.dr && largo() && S.dr.modo !== "lancar" && !e.target.closest(".q-dr, .q-menu, .q-top")) fecharDr(true); // clique fora da gaveta (desktop)
}

// ---- gaveta (desktop) / folha de baixo (móvel)

function abrirDr(id, modo = "ver") {
  soltarK(false, false);
  fecharMenu();
  S.dr = { id, modo, volta: id };
  marcaSel(id);
  pintaDr();
  mostraDr();
  (modo === "apagar" ? q(".q-dr [data-a=nao]") : q("#q-dh"))?.focus(PS);
}
const confirma = (id) => abrirDr(id, "apagar");

function mostraDr() {
  const dr = q(".q-dr"), sc = q(".q-scrim"), modal = !largo() || S.dr.modo === "lancar"; // lançar: modal também no desktop
  dr.setAttribute("aria-modal", modal);
  const on = () => { dr.classList.add("q-on"); sc.classList.add("q-on"); };
  sc.hidden = !modal;
  if (!dr.hidden) return on(); // reaberta a meio do fecho: volta a entrar
  dr.hidden = false;
  requestAnimationFrame(() => requestAnimationFrame(on));
}

function fecharDr(focar) {
  if (!S.dr) return;
  const volta = S.dr.volta, dr = q(".q-dr"), sc = q(".q-scrim"), rev = S.dr.antes && !S.dr.enviado && deLancar(S.dr);
  movPend.delete(S.dr);
  S.dr = null;
  if (rev) { // lançar cancelado (Esc, «Cancelar», ×, sair): o cartão volta à coluna e posição de origem, sem POST
    S.d.cartoes = rev;
    render();
    if (!largo()) irCol(rev.find((c) => c.id === volta)?.coluna);
    anuncia("Cancelado.");
  }
  marcaSel();
  dr.classList.remove("q-on"); sc.classList.remove("q-on");
  setTimeout(() => { if (!S.dr) { dr.hidden = sc.hidden = true; dr.innerHTML = ""; dr.style.transform = ""; } }, reduz() ? 0 : 230);
  if (focar) (volta && el(volta) || q(".q-new"))?.focus(PS);
}

const LAB = { estado: "Estado", modo: "Modo", branch: "Branch", pr_estado: "PR", inicio: "Início", fim: "Fim", duracao_s: "Duração",
  abertas: "Decisões abertas", criado: "Criado", sonda: "Sonda", falha: "Falhas seguidas", nome: "Ficheiro", data: "Data" };
const BLOCO = { resultado: "Resultado", resumo: "Resumo", ultimo: "Último erro" };
// blocos de texto do detalhe (resultado já mascarado no servidor, sempre escapado) e a dica com o comando que resolve (B7)
export function blocosH(dt, esc, md, modo = "vps") {
  const dica = dicaErro(dt.estado, dt.resultado, modo);
  return Object.entries(BLOCO).filter(([k]) => typeof dt[k] === "string" && dt[k]).map(([k, l]) => `<h3>${esc(l)}</h3><p class="q-blk">${esc(dt[k])}</p>`).join("")
    + (dica ? `<div class="q-dica2 md">${md(dica)}</div>` : "");
}
const linhaH = (k, v) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`;

function detalheH(c) {
  const dt = c.detalhe || {}, ls = [], fs = dt.fases && typeof dt.fases === "object" ? Object.entries(dt.fases) : [];
  for (const [k, l] of Object.entries(LAB)) {
    const v = dt[k];
    if (v !== null && v !== undefined && v !== "" && typeof v !== "object") ls.push(linhaH(l, k === "duracao_s" ? fmtDur(v) : /^(inicio|fim|criado|data)$/.test(k) ? fmtData(v) : k === "estado" ? rotuloEstado(String(v)) : String(v)));
  }
  if (manual(c)) ls.push(linhaH("Criado", fmtData(c.criado)), linhaH("Actualizado", fmtData(c.actualizado)));
  const l = c.lancamento;
  if (l) ls.push(linhaH("Lançamento", [LANC[l.estado], camposLancar(A.cfg)[l.squad]?.nome || l.squad, l.task_id, fmtData(l.pedido_em)].filter(Boolean).join(" · ")));
  if (c.desde) ls.push(linhaH(c.coluna === "a-espera" ? "À espera" : "Idade", idade(c.desde, agoraSrv())));
  if (custo(c.custo_usd)) ls.push(linhaH("Custo", custo(c.custo_usd)));
  ls.push(linhaH("Id", c.id));
  return `<dl class="q-dl">${ls.join("")}</dl>`
    + (fs.length ? `<p class="q-fases" aria-label="Fases">${fs.map(([k, v]) => `<span class="q-fa${v ? " q-ok" : ""}">${v ? "✓" : "○"} ${esc(k)}</span>`).join("")}</p>` : "")
    + blocosH(dt, esc, A.md, A.cfg?.modo)
    + (l?.estado === "erro" ? `<h3>Erro ao lançar</h3><p class="q-blk">${esc(l.erro || "sem detalhe")}</p>` : "");
}

function accoesH(c) {
  if (S.dr.modo === "apagar") return `<div class="q-conf" role="group" aria-label="Confirmar apagar"><p>Apagar «${esc(c.titulo)}»? Não dá para desfazer.</p>`
    + `<div class="q-df"><button type="button" class="q-btn q-bad" data-a="sim">Apagar</button><button type="button" class="q-btn" data-a="nao">Cancelar</button></div></div>`;
  const pr = safePr(c.links?.pr);
  const prH = () => (pr ? `<a class="q-btn q-pri" href="${esc(pr)}" target="_blank" rel="noopener noreferrer">Abrir PR ↗</a>` : "");
  if (editavel(c)) return `<div class="q-df"><button type="button" class="q-btn q-pri" data-a="editar" aria-keyshortcuts="e">Editar</button>`
    + `<button type="button" class="q-btn" data-a="mover" aria-haspopup="menu">Mover para… ▾</button>`
    + `${execH(c, "q-btn")}${prH()}`
    + `<button type="button" class="q-btn q-del" data-a="apagar">Apagar</button></div>`;
  return `<div class="q-df">${prH()}`
    + `${c.links?.aprovacao ? `<button type="button" class="q-btn q-pri" data-a="apr">Abrir aprovação</button>` : ""}`
    + `${c.links?.tarefa ? `<button type="button" class="q-btn" data-a="tarefa">Abrir no escritório</button>` : ""}`
    + `${manual(c) ? "" : `<span class="q-nota">${LOCK} Muda sozinho com o estado real</span>`}</div>`;
}

function pintaDr() {
  if (S.dr.modo === "form") return formH();
  const c = cartao(S.dr.id), dr = q(".q-dr");
  if (!c) return fecharDr();
  dr.dataset.p = pj(c.projecto).cor;
  dr.innerHTML = `<div class="q-grab" aria-hidden="true"></div><div class="q-dhd"><div class="q-dtags">${tagH(pj(c.projecto))}${avH(c)}${colPillH(c.coluna)}</div>`
    + `<button type="button" class="q-x" data-a="fechar" aria-label="Fechar">×</button></div>`
    + `<div class="q-dbody"><h2 id="q-dh" tabindex="-1">${esc(c.titulo)}</h2>${c.selo ? `<p>${seloH(c.selo)}</p>` : ""}`
    + `${c.etiquetas?.length ? `<p class="q-ets">${etiqH(c.etiquetas)}</p>` : ""}${detalheH(c)}`
    + `${c.descricao ? `<div class="q-md md">${A.md(c.descricao)}</div>` : ""}</div>${accoesH(c)}`;
}

// criar/editar na mesma gaveta; os valores entram por .value (nunca no HTML)
function abrirForm(id, coluna) {
  if (!S.d?.escrita) return;
  soltarK(false, false);
  fecharMenu();
  const c = id ? cartao(id) : null;
  S.dr = { id, modo: "form", volta: id };
  formH();
  const f = q(".q-form");
  f.titulo.value = c?.titulo || "";
  f.descricao.value = c?.descricao || "";
  f.etiquetas.value = (c?.etiquetas || []).join(", ");
  f.projecto.value = c?.projecto || projectoNovo(S.f.p, A.cfg);
  if (!f.projecto.value) f.projecto.value = "outro"; // id que o quadro não conhece
  if (f.coluna) f.coluna.value = coluna || "ideias";
  mostraDr();
  f.titulo.focus(PS);
}

function formH() {
  const id = S.dr.id, dr = q(".q-dr"), optH = (l) => l.map(([k, r]) => `<option value="${esc(k)}">${esc(r)}</option>`).join("");
  const atH = (k) => `id="q-f-${esc(k)}" name="${esc(k)}" aria-describedby="q-e-${esc(k)}"`;
  const campoH = (k, rot, dica, ctlH) => `<label for="q-f-${esc(k)}">${esc(rot)}${dica ? ` <span>${esc(dica)}</span>` : ""}</label>${ctlH}<p class="q-err" id="q-e-${esc(k)}"></p>`;
  dr.innerHTML = `<div class="q-grab" aria-hidden="true"></div><div class="q-dhd"><h2 id="q-dh" tabindex="-1">${id ? "Editar cartão" : "Novo cartão"}</h2>`
    + `<button type="button" class="q-x" data-a="cancelar" aria-label="Fechar">×</button></div><form class="q-form" novalidate><div class="q-dbody"><p class="q-ferr" role="alert" hidden></p>`
    + campoH("titulo", "Título", "", `<input ${atH("titulo")} maxlength="120" required autocomplete="off">`)
    + `<div class="q-2"><div>${campoH("projecto", "Projecto", "", `<select ${atH("projecto")}>${optH(S.d.projectos.map((p) => [p.chave, ico(p.icone) + " " + p.rotulo]))}</select>`)}</div>`
    + (id ? "" : `<div>${campoH("coluna", "Coluna", "", `<select ${atH("coluna")}>${optH(S.d.colunas.map((c) => [c.chave, (COL[c.chave]?.[0] || "") + " " + c.rotulo]))}</select>`)}</div>`)
    + `</div>${campoH("etiquetas", "Etiquetas", "separadas por vírgula, até 5", `<input ${atH("etiquetas")} autocomplete="off">`)}`
    + `${campoH("descricao", "Descrição", "markdown simples", `<textarea ${atH("descricao")} rows="6" maxlength="2000"></textarea>`)}</div>`
    + `<div class="q-df"><button type="submit" class="q-btn q-pri">Guardar</button><button type="button" class="q-btn" data-a="cancelar">Cancelar</button>`
    + `${id ? `<button type="button" class="q-btn q-del" data-a="apagar">Apagar</button>` : ""}</div></form>`;
  if (id) dr.dataset.p = pj(cartao(id)?.projecto).cor; else delete dr.dataset.p;
}

// ---- lançar: a gaveta em modo «lancar»; o cartão aparece em «Em curso» sem gravar até «Lançar» ou «Só mover»
const LCAMPOS = ["projecto", "slug", "titulo", "brief"]; // os que podem ter erro à vista (squad/tipo/modo são selects fechados)

function abrirLancar(antes, id, posicao) {
  const c = antes.find((x) => x.id === id), s = sugestao(c.projecto, A.cfg);
  fecharMenu();
  S.d.cartoes = moverLocal(antes, id, "em-curso", posicao);
  S.seq++;
  render();
  if (!largo()) irCol("em-curso");
  S.dr = { id, modo: "lancar", volta: id, antes, posicao, coluna: "em-curso", slugMexido: false };
  movPend.add(S.dr);
  marcaSel(id);
  lancarH(c);
  const f = q(".q-lf");
  f.squad.value = s.squad || "";
  f.projecto.value = s.projecto || "";
  f.titulo.value = c.titulo || "";
  f.brief.value = c.descricao || "";
  f.slug.value = slugDe(c.titulo);
  lancarSync(f); // tipo e modo por omissão da squad = os da sugestão
  mostraDr();
  anuncia("Confirma o lançamento ou escolhe Só mover.");
  q("#q-dh").focus(PS);
}

// D7: só este cartão volta à origem
const deLancar = ({ antes, id }, c = antes.find((x) => x.id === id)) =>
  moverLocal(S.d.cartoes, id, c.coluna, antes.filter((x) => manual(x) && x.coluna === c.coluna && porOrdem(x, c) < 0).length);

function lancarH(c) {
  const dr = q(".q-dr"), optH = (l) => l.map(([k, r]) => `<option value="${esc(k)}">${esc(r)}</option>`).join("");
  const cs = camposLancar(A.cfg), nomeP = (id) => (A.cfg?.projectos || []).find((p) => p.id === id)?.nome || id;
  const atH = (k) => `id="q-l-${esc(k)}" name="${esc(k)}" aria-describedby="q-l-${esc(k)}-e"`;
  const campoH = (k, rot, dica, ctlH) => `<label for="q-l-${esc(k)}">${esc(rot)}${dica ? ` <span>${esc(dica)}</span>` : ""}</label>${ctlH}<p class="q-err" id="q-l-${esc(k)}-e"></p>`;
  const filaH = () => (S.d.lancar ? "" : `<p class="q-ferr">Fila de lançamento indisponível. Podes só mover o cartão.</p>`);
  dr.dataset.p = pj(c.projecto).cor;
  dr.innerHTML = `<div class="q-grab" aria-hidden="true"></div><div class="q-dhd"><h2 id="q-dh" tabindex="-1">Lançar na squad?</h2>`
    + `<button type="button" class="q-x" data-a="lcancel" aria-label="Cancelar e devolver o cartão">×</button></div>`
    + `<form class="q-form q-lf" novalidate><div class="q-dbody"><p class="q-lnota">«Cancelar» devolve o cartão a ${esc(rotCol(c.coluna))}.</p>`
    + `<p class="q-ferr" role="alert" hidden></p>${filaH()}`
    + `<div class="q-2"><div>${campoH("squad", "Squad", "", `<select ${atH("squad")}>${optH(Object.entries(cs).map(([k, x]) => [k, x.nome]))}</select>`)}</div>`
    + `<div>${campoH("projecto", "Projecto", "", `<select ${atH("projecto")}><option value="">Escolhe…</option>${optH((A.cfg?.lancar?.projectos || []).map((p) => [p, nomeP(p)]))}</select>`)}</div>`
    + `<div>${campoH("kind", "Tipo", "", `<select ${atH("kind")}></select>`)}</div>`
    + `<div>${campoH("mode", "Tamanho", "", `<select ${atH("mode")}></select>`)}</div></div>`
    + campoH("titulo", "Título", "", `<input ${atH("titulo")} maxlength="120" required autocomplete="off">`)
    + campoH("brief", "O que queres? Escreve como a um colega.", "vem da descrição do cartão; editar aqui não muda o cartão", `<textarea ${atH("brief")} rows="6"></textarea>`)
    + `<p class="q-lnota" id="q-l-kb"></p>`
    + `<details class="q-adv"><summary>Avançado</summary>${campoH("slug", "Nome curto (slug)", "gerado do título; a-z, 0-9 e «-»", `<input ${atH("slug")} maxlength="60" pattern="[a-z0-9][a-z0-9\\-]{1,58}[a-z0-9]" autocomplete="off" autocapitalize="none" spellcheck="false">`)}</details></div>`
    + `<div class="q-df"><button type="submit" class="q-btn q-pri">Lançar</button><button type="button" class="q-btn" data-a="lmover">Só mover</button>`
    + `<button type="button" class="q-btn" data-a="lcancel">Cancelar</button></div></form>`;
  q("#q-l-brief").setAttribute("aria-describedby", "q-l-brief-e q-l-kb");
}

export const lerLancar = (f) => ({ squad: f.squad.value, projecto: f.projecto.value, kind: f.kind.value, mode: f.mode.value, slug: f.slug.value.trim(),
  titulo: limpaTexto(f.titulo.value).trim(), brief: limpaTexto(f.brief.value, true) });

// squad mudou → tipos e modos dela, o 1.º de cada por omissão; erros, contador e «Lançar» sempre reavaliados
function lancarSync(f) {
  const sq = f.squad.value, cp = camposLancar(A.cfg)[sq] || { kind: [], mode: [] };
  if (f.dataset.sq !== sq) {
    f.dataset.sq = sq;
    f.kind.replaceChildren(...cp.kind.map((k) => new Option(rotuloDe(TIPOS, k), k))); // rótulo em texto, valor igual (contrato v1)
    f.mode.replaceChildren(...cp.mode.map((m) => new Option(rotuloDe(TAMANHOS, m), m)));
  }
  const v = lerLancar(f), err = validaLancar(v, A.cfg), kb = kbFaltam(v.brief), n = (x) => String(Math.abs(x)).replace(".", ",");
  for (const k of LCAMPOS) { q(`#q-l-${k}-e`).textContent = err[k] || ""; f[k].setAttribute("aria-invalid", !!err[k]); }
  if (err.slug) f.querySelector(".q-adv").open = true; // C10.3: o erro do slug nunca fica escondido
  q("#q-l-kb").textContent = `Isto vai ser enviado à squad · ${kb >= 0 ? `faltam ${n(kb)} KB de 8 KB` : `${n(kb)} KB acima do limite de 8 KB`}`;
  f.querySelector("[type=submit]").disabled = !S.d.lancar || !!S.dr.busy || Object.keys(err).length > 0;
}

function lancarInput(e) {
  const f = e.target.form;
  if (S.dr?.modo !== "lancar" || !f?.classList.contains("q-lf")) return;
  if (e.target.name === "slug") S.dr.slugMexido = f.slug.value !== ""; // apagado = volta a seguir o título
  if (e.target.name === "titulo" && !S.dr.slugMexido) f.slug.value = slugDe(f.titulo.value);
  lancarSync(f);
}

// uma só chamada (D8): grava o pedido e move o cartão; 400/429/503 ficam na gaveta; 409/403 recarregam e fecham
async function lancar(f) {
  const dr = S.dr, v = lerLancar(f);
  if (dr.busy || !S.d.lancar || Object.keys(validaLancar(v, A.cfg)).length) return;
  dr.busy = true;
  lancarSync(f);
  const r = await post({ ...corpoLancar(v), cartao_id: dr.id, posicao: dr.posicao });
  dr.busy = false;
  if (S.dr !== dr) return render(); // fechada entretanto (sair)
  if (r.ok || r.recarregado) {
    dr.enviado = true;
    render();
    fecharDr(true);
    if (r.ok) return r.j.movido === false ? aviso("Lançado, mas não consegui mover o cartão.") : toast("Lançado.");
    return aviso(r.j?.error === "já lançado" ? "Este cartão já tinha sido lançado. Recarreguei o quadro." : r.msg);
  }
  const p = q(".q-lf .q-ferr[role=alert]");
  p.hidden = false;
  p.textContent = r.j?.error ? `Não lancei: ${r.j.error}.` : r.msg;
  lancarSync(f);
}

async function guardar(e) {
  e.preventDefault();
  const f = e.target, id = S.dr?.id, dr = S.dr;
  if (S.dr?.modo === "lancar") return lancar(f);
  if (S.dr?.modo !== "form" || f.dataset.busy) return;
  const v = { titulo: limpaTexto(f.titulo.value).trim(), descricao: limpaTexto(f.descricao.value, true), projecto: f.projecto.value,
    etiquetas: parseEtiquetas(limpaTexto(f.etiquetas.value)) }, c = id && cartao(id);
  const err = validaForm(v), mau = ["titulo", "etiquetas", "descricao"].filter((k) => err[k]);
  for (const k of ["titulo", "etiquetas", "descricao"]) { q(`#q-e-${k}`).textContent = err[k] || ""; f[k].setAttribute("aria-invalid", !!err[k]); }
  if (mau.length) return f[mau[0]].focus();
  // editar só envia o que mudou (um «[segredo]» mascarado que não se tocou não é regravado)
  const era = c && { ...c, descricao: limpaTexto(c.descricao, true) }; // compara no mesmo formato em que se envia
  const mud = c && Object.fromEntries(Object.entries(v).filter(([k, x]) => JSON.stringify(x) !== JSON.stringify(era[k] ?? null)));
  if (c && !Object.keys(mud).length) { S.dr.modo = "ver"; pintaDr(); return q("#q-dh").focus(); }
  f.dataset.busy = 1;
  const r = await post(id ? { op: "editar", id, ...mud } : { op: "criar", ...v, coluna: f.coluna.value });
  delete f.dataset.busy;
  if (S.dr !== dr) return r.ok ? (render(), toast("Guardado.")) : aviso(r.msg);
  if (!r.ok) { const p = q(".q-ferr"); if (p) { p.hidden = false; p.textContent = r.msg; p.focus?.(); } return; } // 409: recarregado, o texto fica
  const novo = id || r.novo;
  render();
  if (!cartao(novo)) { fecharDr(); return toast("Guardado."); }
  if (!id && !largo()) irCol(cartao(novo)?.coluna);
  el(novo)?.scrollIntoView({ block: "nearest" });
  S.dr = { id: novo, modo: "ver", volta: novo };
  pintaDr();
  q("#q-dh")?.focus(PS);
  toast("Guardado.");
}

async function apagar(id) {
  const dr = S.dr, e = el(id), t = cartao(id)?.titulo || "", r = await post({ op: "apagar", id });
  if (!r.ok) { aviso(r.msg); if (S.dr === dr) { dr.modo = "ver"; pintaDr(); } return; }
  const viz = e?.nextElementSibling?.dataset.id || e?.previousElementSibling?.dataset.id;
  if (S.dr === dr) { dr.volta = viz; fecharDr(); }
  if (e && !reduz()) { e.classList.add("q-out"); await new Promise((ok) => setTimeout(ok, 140)); }
  render();
  S.dr || (viz && el(viz) || q(".q-new"))?.focus(PS);
  toast(`Cartão «${t}» apagado.`);
}

// POST com as chaves exactas, em fila (cada um usa a «versao» do anterior); ++S.seq e S.fim descartam GETs em voo;
// sem poll com POSTs pendentes; timeout de 15 s; 409/403 recarregam (403 = token novo depois de um reinício)
let fila = Promise.resolve(), pend = 0;
const movPend = new Set(); // mover optimistas ainda na fila: reaplicam-se por cima de cada resposta
function post(corpo) {
  pend++;
  const p = fila.then(() => postAgora(corpo)).finally(() => { pend--; S.fim = (S.fim || 0) + 1; movPend.delete(corpo); });
  fila = p.catch(() => {});
  return p;
}
async function postAgora(corpo) {
  const ids = new Set(S.d.cartoes.filter(manual).map((c) => c.id)), url = "op" in corpo ? "api/quadro" : "api/quadro/lancar"; // lançar não tem «op»
  S.seq++;
  const ac = new AbortController(), to = setTimeout(() => ac.abort(), 15000);
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, signal: ac.signal,
      body: JSON.stringify({ ...corpo, token: S.d.token, versao: S.d.versao }) });
    const j = await r.json().catch(() => ({}));
    if (r.ok && Array.isArray(j.cartoes)) {
      S.d.versao = j.versao;
      movPend.delete(corpo);
      S.d.cartoes = [...S.d.cartoes.filter((c) => !manual(c)), ...fundeManuais(S.d.cartoes.filter(manual), j.cartoes)];
      for (const m of movPend) if (m.id !== corpo.cartao_id) S.d.cartoes = moverLocal(S.d.cartoes, m.id, m.coluna, m.posicao);
      return { ok: true, novo: j.cartoes.find((c) => !ids.has(c.id))?.id, j };
    }
    if (r.status === 409 || r.status === 403) {
      await carregar();
      return { ok: false, recarregado: true, j, msg: r.status === 409 ? MSG409 : "A sessão do Office mudou. Recarreguei; repete a acção." };
    }
    return { ok: false, j, msg: j.error ? `Não gravou: ${j.error}.` : `Não gravou (erro ${r.status}).` };
  } catch { return { ok: false, msg: "Sem ligação ao Office. Não gravou; tenta de novo." }; } finally { clearTimeout(to); }
}

// arrastar, teclado e menu passam todos aqui: um manual que entra em «Em curso» sem lançamento activo abre o diálogo
function mover(antes, id, coluna, posicao) {
  if (deveLancar(antes.find((x) => x.id === id), coluna)) return abrirLancar(antes, id, posicao);
  return moverJa(antes, id, coluna, posicao);
}

// o mesmo «op: mover»: optimista, reverte em erro
async function moverJa(antes, id, coluna, posicao) {
  S.d.cartoes = moverLocal(antes, id, coluna, posicao);
  render();
  anuncia(`Movido para ${rotCol(coluna)}, posição ${posicao + 1} de ${nMan(coluna)}.`);
  if (!largo()) irCol(coluna);
  el(id)?.focus(PS);
  const corpo = { op: "mover", id, coluna, posicao };
  movPend.add(corpo);
  const r = await post(corpo);
  if (r.ok) return render();
  if (!r.recarregado) { S.d.cartoes = antes; render(); }
  aviso(r.msg);
}

// ---- menu «⋯» / «Mover para…» (popover no desktop, folha curta no móvel)

function abrirMenu(btn, id, completo) {
  const c = cartao(id), m = q(".q-menu"), sc = q(".q-scrim");
  if (!editavel(c)) return;
  soltarK(false, false);
  S.menu = { id, btn };
  const itemH = (a, txtH, extraH = "") => `<button type="button" role="menuitem" tabindex="-1" data-m="${esc(a)}"${extraH}>${txtH}</button>`;
  m.innerHTML = (completo ? itemH("editar", "✎ Editar") + (podeLancarMenu(c, S.d.lancar) ? itemH("lancar", "▷ Lançar…") : "") + itemH("apagar", "✕ Apagar…") + `<div role="separator"></div>` : "") + `<p class="q-ml" aria-hidden="true">Mover para</p>`
    + S.d.colunas.map((k) => itemH("col", `<span aria-hidden="true">${esc(COL[k.chave]?.[0] || "·")}</span> ${esc(k.rotulo)}${k.chave === c.coluna ? " ✓" : ""}`,
      ` data-col="${esc(k.chave)}"${k.chave === c.coluna ? " aria-disabled='true'" : ""}`)).join("")
    + `<div role="separator"></div>` + itemH("topo", "⤒ Topo da coluna") + itemH("fim", "⤓ Fim da coluna");
  m.setAttribute("aria-label", "Acções de " + c.titulo);
  m.hidden = false;
  if (largo()) {
    const r = btn.getBoundingClientRect(), h = m.offsetHeight, w = m.offsetWidth;
    m.style.left = Math.max(8, Math.min(r.right - w, innerWidth - w - 8)) + "px";
    m.style.top = (r.bottom + h + 8 > innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + "px";
  } else { sc.hidden = false; requestAnimationFrame(() => sc.classList.add("q-on")); }
  m.querySelector("[role=menuitem]:not([aria-disabled])").focus();
  setTimeout(() => document.addEventListener("pointerdown", foraMenu, true));
}
function foraMenu(e) { if (!e.target.closest(".q-menu")) { fecharMenu(); S.tMenu = agora(); } }
function fecharMenu(focar) {
  if (!S.menu) return;
  const btn = S.menu.btn, sc = q(".q-scrim");
  S.menu = null;
  document.removeEventListener("pointerdown", foraMenu, true);
  q(".q-menu").hidden = true;
  if (!S.dr || largo()) { sc.classList.remove("q-on"); sc.hidden = true; }
  if (focar && btn?.isConnected) btn.focus();
}
function escolheMenu(b) {
  if (b.getAttribute("aria-disabled")) return;
  const id = S.menu.id, a = b.dataset.m, coluna = a === "col" ? b.dataset.col : cartao(id).coluna;
  fecharMenu();
  if (a === "editar") return abrirForm(id);
  if (a === "apagar") return confirma(id);
  if (a === "lancar") return abrirLancar(S.d.cartoes, id, 0); // topo de «Em curso», mesmo diálogo que arrastar
  mover(S.d.cartoes, id, coluna, a === "topo" ? 0 : nMan(coluna, id));
}

// ---- teclado

function teclaGlobal(e) {
  if (!S.on || A.root.hidden || e.ctrlKey || e.metaKey || e.altKey) return;
  if (S.menu) return teclaMenu(e);
  if (e.key === "Escape") {
    if (D) return largar(false);
    if (S.dr && !S.k) { e.preventDefault(); if (S.dr.busy) return; S.dr.modo === "apagar" ? (S.dr.modo = "ver", pintaDr(), q("#q-dh").focus()) : fecharDr(true); }
    return;
  }
  if (e.key === "Tab" && S.dr && (!largo() || S.dr.modo === "lancar")) return prende(e);
  if (e.target.closest?.("input, textarea, select, .q-dr") || S.k) return;
  if (e.key === "/") { e.preventDefault(); q(".q-s input").focus(); }
  else if (e.key === "n" && S.d?.escrita) { e.preventDefault(); abrirForm(null, "ideias"); }
  else if (/^[1-5]$/.test(e.key)) { e.preventDefault(); irCol(cols()[+e.key - 1], true); }
}

function teclaMenu(e) {
  const it = [...q(".q-menu").querySelectorAll("[role=menuitem]")], i = it.indexOf(document.activeElement), d = { ArrowDown: 1, ArrowUp: -1 }[e.key];
  if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); fecharMenu(true); }
  else if (d) { e.preventDefault(); it[(i + d + it.length) % it.length].focus(); }
  else if (e.key === "Home" || e.key === "End") { e.preventDefault(); it[e.key === "Home" ? 0 : it.length - 1].focus(); }
}

// folha de baixo modal: o Tab não sai dela
function prende(e) {
  const f = [...q(".q-dr").querySelectorAll("button, a[href], input, select, textarea")].filter((x) => x.offsetParent), a = f[0], z = f[f.length - 1];
  if (e.shiftKey ? document.activeElement === a : document.activeElement === z) { e.preventDefault(); (e.shiftKey ? z : a)?.focus(); }
}

function teclaCartao(e) {
  const card = e.target.closest?.(".q-board .q-card[data-id]");
  if (!card || e.target !== card || e.ctrlKey || e.metaKey || e.altKey) return;
  const id = card.dataset.id, c = cartao(id), ed = editavel(c), k = e.key;
  if (S.k) {
    if (k === "Tab") return soltarK(false); // cancela e devolve o foco ao cartão; o Tab segue dali
    e.preventDefault();
    if (k === " " || k === "Enter") return soltarK(true);
    if (k === "Escape") return soltarK(false);
    if (!k.startsWith("Arrow")) return;
    const ns = Object.fromEntries(cols().map((col) => [col, visCol(S.k.antes, col).filter((x) => x.id !== id).length])); // só manuais visíveis
    const at = alvoTeclado(cols(), ns, S.k.at, k);
    if (at.coluna === S.k.at.coluna && at.posicao === S.k.at.posicao) return anuncia("Limite do quadro.");
    S.k.at = at;
    S.d.cartoes = moverLocal(S.k.antes, id, at.coluna, posVis(S.k.antes, id, at));
    render();
    if (!largo()) irCol(at.coluna);
    el(id)?.focus(PS);
    el(id)?.scrollIntoView({ block: "nearest", inline: "nearest" });
    return anuncia(`${rotCol(at.coluna)}, posição ${at.posicao + 1} de ${(ns[at.coluna] || 0) + 1}.`);
  }
  if (k === "Enter" || k === " " && !ed) { e.preventDefault(); return abrirDr(id); }
  if (k === " ") {
    e.preventDefault();
    const vc = visCol(S.d.cartoes, c.coluna), v0 = c.antigo ? vc.length : vc.findIndex((x) => x.id === id);
    S.k = { id, antes: S.d.cartoes, c0: c.coluna, v0, at: { coluna: c.coluna, posicao: v0 } };
    card.classList.add("q-lift");
    return anuncia(`Cartão «${c.titulo}» agarrado. Setas para mover, Espaço para largar, Esc para cancelar.`);
  }
  if (k === "ArrowDown" || k === "ArrowUp") { e.preventDefault(); return (k === "ArrowDown" ? card.nextElementSibling : card.previousElementSibling)?.focus(); }
  if (!ed) return;
  if (k === "m") { e.preventDefault(); abrirMenu(card.querySelector(".q-more"), id, false); }
  else if (k === "e") { e.preventDefault(); abrirForm(id); }
  else if (k === "Delete") { e.preventDefault(); confirma(id); }
}

// largar/cancelar o agarrado do teclado; «mesmo sítio» compara-se nos índices visíveis; foco=false quando o foco já foi para outro lado
function soltarK(ok, foco = true) {
  const k = S.k;
  if (!k) return;
  S.k = null;
  if (ok && (k.at.coluna !== k.c0 || k.at.posicao !== k.v0)) return mover(k.antes, k.id, k.at.coluna, posVis(k.antes, k.id, k.at));
  S.d.cartoes = k.antes;
  render();
  if (foco) el(k.id)?.focus(PS);
  anuncia(ok ? "Largado no mesmo sítio." : "Cancelado.");
}

// ---- arrastar com ponteiro: rato ao mexer 4 px; toque/caneta só com pressão longa de 350 ms parada (senão é scroll)

function pDown(e) {
  const card = e.target.closest(".q-board .q-card[data-kind=manual]");
  if (e.button || D || S.k || !card || e.target.closest("button, a")) return;
  D = { id: card.dataset.id, card, x: e.clientX, y: e.clientY, px: e.clientX, py: e.clientY, pid: e.pointerId, toque: e.pointerType !== "mouse" };
  if (D.toque) D.t = setTimeout(agarrar, 350);
  addEventListener("pointermove", pMove);
  addEventListener("pointerup", pUp);
  addEventListener("pointercancel", pUp);
}
function pMove(e) {
  if (e.pointerId !== D?.pid) return;
  D.px = e.clientX; D.py = e.clientY;
  if (!D.on) {
    if (Math.hypot(D.px - D.x, D.py - D.y) <= (D.toque ? 8 : 4)) return;
    if (D.toque) return limpaD(); // mexeu antes da pressão longa: é scroll
    agarrar();
  }
  e.preventDefault();
  arrastar();
}
function pUp(e) {
  if (e.pointerId !== D?.pid) return;
  if (!D.on) return limpaD();
  largar(e.type === "pointerup");
}
function limpaD() {
  clearTimeout(D?.t);
  removeEventListener("pointermove", pMove);
  removeEventListener("pointerup", pUp);
  removeEventListener("pointercancel", pUp);
  if (!D?.on) D = null;
}

function agarrar() {
  if (!D?.card.isConnected) return limpaD();
  fecharMenu();
  const r = D.card.getBoundingClientRect(), c = cartao(D.id), g = D.card.cloneNode(true);
  Object.assign(D, { on: true, r, orig: [D.card.parentNode, D.card.nextSibling], c0: c.coluna, p0: posGlobal(S.d.cartoes, D.id, c.coluna, seg(D.card)), antes: S.d.cartoes, g });
  g.classList.add("q-ghost");
  g.removeAttribute("tabindex");
  g.setAttribute("aria-hidden", "true");
  Object.assign(g.style, { width: r.width + "px", height: r.height + "px", left: r.left + "px", top: r.top + "px" });
  A.root.append(g);
  D.card.classList.add("q-ph");
  A.root.classList.add("q-drag");
  requestAnimationFrame(() => g.classList.add("q-up"));
  navigator.vibrate?.(10);
  anuncia(`Cartão «${c.titulo}» agarrado.`);
  D.raf = requestAnimationFrame(passo);
}

// o fantasma segue o ponteiro; o placeholder vai para onde o cartão vai cair (sobre uma aba = fim dessa coluna)
function arrastar() {
  D.g.style.transform = `translate(${D.px - D.x}px,${D.py - D.y}px)`;
  const p = document.elementFromPoint(D.px, D.py), tab = p?.closest?.(".q-tab"), col = tab ? q(`.q-col[data-col="${CSS.escape(tab.dataset.col)}"]`) : p?.closest?.(".q-col[data-col]");
  qa(".q-alvo").forEach((x) => x !== tab && x !== col && x.classList.remove("q-alvo"));
  if (!col) return;
  tab?.classList.add("q-alvo");
  col.classList.add("q-alvo");
  const l = col.querySelector(".q-list"), man = [...l.children].filter((x) => x.dataset.kind === "manual" && x !== D.card);
  const i = tab ? man.length : posicaoDrop(man.map((x) => { const r = x.getBoundingClientRect(); return r.top + r.height / 2; }), D.py);
  const ref = man[i] || [...l.children].find((x) => x.dataset.kind !== "manual" && x !== D.card) || null;
  if (D.card.parentNode !== l || D.card.nextSibling !== ref) flip(() => l.insertBefore(D.card, ref));
}

// auto-scroll perto das margens: faixa de colunas na horizontal, coluna na vertical
function passo() {
  if (!D?.on) return;
  const b = q(".q-board"), rb = b.getBoundingClientRect(), l = document.elementFromPoint(D.px, D.py)?.closest?.(".q-list"), x = b.scrollLeft, y = l?.scrollTop;
  if (D.px < rb.left + 40) b.scrollLeft -= 12; else if (D.px > rb.right - 40) b.scrollLeft += 12;
  if (l) { const r = l.getBoundingClientRect(); if (D.py < r.top + 32) l.scrollTop -= 12; else if (D.py > r.bottom - 32) l.scrollTop += 12; }
  if (b.scrollLeft !== x || l?.scrollTop !== y) arrastar();
  D.raf = requestAnimationFrame(passo);
}

function largar(ok) {
  const d = D, g = d.g;
  limpaD();
  D = null;
  cancelAnimationFrame(d.raf);
  S.tDrop = agora();
  if (!ok) flip(() => d.orig[0].insertBefore(d.card, d.orig[1]?.isConnected ? d.orig[1] : null));
  qa(".q-alvo").forEach((x) => x.classList.remove("q-alvo"));
  A.root.classList.remove("q-drag");
  const r = d.card.getBoundingClientRect(), col = d.card.closest(".q-col")?.dataset.col, pos = col && posGlobal(d.antes, d.id, col, seg(d.card));
  let feito = false;
  const fim = () => {
    if (feito) return;
    feito = true;
    g.remove(); d.card.classList.remove("q-ph");
    if (ok && col && (col !== d.c0 || pos !== d.p0)) return mover(d.antes, d.id, col, pos);
    anuncia(ok ? "Largado no mesmo sítio." : "Cancelado.");
    d.card.focus(PS);
  };
  if (reduz()) return fim();
  g.classList.replace("q-up", "q-drop");
  g.style.transform = `translate(${r.left - d.r.left}px,${r.top - d.r.top}px)`;
  g.addEventListener("transitionend", fim, { once: true });
  setTimeout(fim, 260);
}

// folha: puxar a pega > 80 px fecha; toque no fundo fecha (não com formulário)
function pegaFolha() {
  const dr = q(".q-dr");
  let y0 = null, dy = 0;
  dr.addEventListener("pointerdown", (e) => { if (!largo() && e.target.closest(".q-grab, .q-dhd") && !e.target.closest("button")) { y0 = e.clientY; dy = 0; dr.setPointerCapture(e.pointerId); } });
  dr.addEventListener("pointermove", (e) => { if (y0 !== null) { dy = Math.max(0, e.clientY - y0); dr.style.transform = `translateY(${dy}px)`; } });
  const fim = () => { if (y0 === null) return; y0 = null; dr.style.transform = ""; if (dy > 80 && S.dr?.modo !== "lancar") fecharDr(true); };
  dr.addEventListener("pointerup", fim);
  dr.addEventListener("pointercancel", fim);
  q(".q-scrim").addEventListener("click", () => agora() - (S.tMenu || 0) > 400 && S.dr && !/form|lancar/.test(S.dr.modo) && fecharDr(true));
}
