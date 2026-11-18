// v3 (CA-14, 15): avisos «precisa de ti». Carregado pelo topo.js no arranque; substitui a faixa #banner e o prBanner.
// As funções exportadas no topo são puras (sem window/document) para os testes em node; o DOM fica no fim.
// «À tua espera» = state.espera (aprovações em aberto + PRs; recusas não); falhas = alerts (tarefas mortas) + falhas_exec.

export const JUNTA_MS = 20000, INFO_MS = 5000, MAX = 4;
export const PR_URL = /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/(\d+)$/;

// fila: o mesmo aviso (chave) até 20 s depois do anterior junta-se («×N»); no máximo 4, sai primeiro o informativo
// mais antigo e só depois o alerta mais antigo
export function fila(lista, a, agora) {
  const i = lista.findIndex((x) => x.chave === a.chave && agora - x.em <= JUNTA_MS);
  const l = i >= 0 ? lista.map((x, j) => (j === i ? { ...x, ...a, n: x.n + 1, em: agora } : x)) : [...lista, { ...a, n: 1, em: agora }];
  while (l.length > MAX) {
    const tipo = l.some((x) => x.tipo === "info") ? "info" : "alerta";
    const k = l.reduce((m, x, j) => (x.tipo === tipo && (m < 0 || x.em < l[m].em) ? j : m), -1);
    l.splice(k, 1);
  }
  return l;
}
// o informativo sai aos 5 s; o alerta só com «Fechar»
export const expirar = (lista, agora) => lista.filter((x) => x.tipo !== "info" || agora - x.em < INFO_MS);

const proj = (s, id) => (s?.tasks || []).find((t) => t.id === id)?.project || id;
export const prNum = (url) => String(url ?? "").match(PR_URL)?.[1] || null;
// motivo de uma execução com erro: «api_error · HTTP 429», «erro»
export const motivo = (x) => [x.motivo && x.motivo !== "completed" ? x.motivo : x.erro ? "erro" : "", x.http ? "HTTP " + x.http : ""].filter(Boolean).join(" · ")
  || x.status || "erro";

// falhas = alerts.items («tarefa:<id>», tarefas que morreram) + falhas_exec, cada uma com chave, quem e o quê
export function falhasDe(s) {
  const out = [];
  for (const k of s?.alerts?.items || []) {
    const id = String(k).startsWith("tarefa:") ? String(k).slice(7) : null;
    if (id) out.push({ tipo: "tarefa", chave: k, id, quem: proj(s, id), oque: "morreu" });
  }
  for (const x of Array.isArray(s?.falhas_exec) ? s.falhas_exec : [])
    out.push({ tipo: "exec", chave: "exec:" + x.id, id: x.id, quem: x.project || proj(s, x.id), oque: motivo(x), finished: x.finished });
  return out;
}
// itens de espera.itens (já ordenados pelo servidor, o mais antigo primeiro) com quem pede e o quê
export function esperaDe(s) {
  return (Array.isArray(s?.espera?.itens) ? s.espera.itens : []).filter((it) => it.tipo === "aprovacao" || it.tipo === "pr").map((it) => ({ ...it,
    quem: it.tipo === "aprovacao" ? it.fonte : it.project || it.id,
    oque: it.tipo === "aprovacao" ? it.titulo : `PR #${prNum(it.pr_url) || "?"}${it.draft ? " (draft)" : ""}` }));
}

// alertas novos: presentes neste poll e ausentes no anterior; nada na 1.ª carga (sem anterior, D-10)
export function novos(prev, s) {
  if (!prev) return [];
  const antes = new Set([...esperaDe(prev), ...falhasDe(prev)].map((x) => x.chave));
  return [...esperaDe(s), ...falhasDe(s)].filter((x) => x.chave && !antes.has(x.chave)).map((item) => ({ tipo: "alerta", chave: item.chave, item }));
}
// informativos: fim de uma tarefa e PR aberto ou integrado (substituem o #banner)
export function infos(prev, s) {
  if (!prev) return [];
  const out = [];
  for (const t of s?.tasks || []) {
    const o = (prev.tasks || []).find((x) => x.id === t.id);
    if (o?.running && !t.running) out.push({ tipo: "info", chave: "fim:" + t.id, texto: `${t.status === "concluída" ? "✅" : "⚠️"} ${t.project}: ${t.mode} ${t.status}` });
    const pr = t.gh?.pr;
    if (o && (pr === "aberto" || pr === "integrado") && (o.gh?.pr !== pr || o.gh?.pr_url !== t.gh.pr_url))
      out.push({ tipo: "info", chave: `pr:${t.id}:${pr}`, texto: `${pr === "integrado" ? "✅ PR integrado" : "🎉 PR aberto"}: ${t.project}${prNum(t.gh.pr_url) ? " #" + prNum(t.gh.pr_url) : ""}` });
  }
  return out;
}
// notificação do browser de um alerta: só com o separador escondido e as notificações ligadas no ⚙ (com ele à vista chega o
// aviso); as tarefas que morreram e as que terminam já notificam pelo index.html (mesma tag), por isso aqui não
export const notificar = (a, escondido, ligadas) => !!(escondido && ligadas && a?.tipo === "alerta" && a.item?.tipo !== "tarefa");

// serviço de ações (state.accoes, CA-17…19): um estado, não um evento; aparece também na 1.ª carga (D10). Os avisos do
// registo de squads (colector e executor) contam como um só, na posição do primeiro
export const ACCOES_REGISTO = "registo de squads ilegível";
export function accoesAlerta(s) {
  const a = s?.accoes;
  if (!a) return null;
  if (a.parado) return { chave: "accoes:parado", texto: `Serviço de ações parado desde ${a.desde ?? "?"}` };
  const l = [];
  for (const x of Array.isArray(a.avisos) ? a.avisos : []) {
    const t = String(x).startsWith(ACCOES_REGISTO) ? "não consigo ler o registo das squads" : String(x);
    if (!l.includes(t)) l.push(t);
  }
  if (!l.length) return null;
  return { chave: "accoes:" + l[0], texto: `Serviço de ações: ${l[0]}${l.length > 1 ? ` (e mais ${l.length - 1})` : ""}` };
}
// ant = alerta no ecrã (ou null), act = accoesAlerta deste poll, fechada = chave fechada pelo utilizador
export function accoesPasso(ant, act, fechada) {
  if (!act) return { op: ant ? "tirar" : "nada", fechada: null };
  if (act.chave === fechada) return { op: "nada", fechada };
  if (!ant || ant.chave !== act.chave) return { op: "mostrar", fechada: null };
  return { op: ant.texto === act.texto ? "manter" : "actualizar", fechada };
}
export const accoesHtml = (a, esc) => `<p>${esc(a.texto)}</p><button type="button" data-a="fechar">Fechar</button>`;

// ---- DOM

let A = null, box = null, F = [], tm = 0, accAnt = null, accFechada = null;
const esc = (s) => A.esc(s);

// api = { esc, ir(item), notificar?(titulo, corpo) → só chamado quando notificar() deixa }
export function iniciar(api) {
  A = api;
  box = document.createElement("div");
  box.id = "avisos";
  document.body.append(box);
  box.addEventListener("click", (e) => {
    const b = e.target.closest("[data-a]"), el = b?.closest(".av");
    if (!el) return;
    const a = F.find((x) => x.chave === el.dataset.c);
    F = F.filter((x) => x !== a);
    pinta();
    if (b.dataset.a === "ir" && a?.item) A.ir(a.item);
  });
}
export function mostrar(a) {
  if (!box) return;
  F = fila(F, a, Date.now());
  pinta();
}
export function poll(prev, s) {
  for (const a of [...infos(prev, s), ...novos(prev, s)]) {
    mostrar(a);
    if (A.notificar && notificar(a, document.hidden, A.ligadas?.())) A.notificar(`${a.item.quem} precisa de ti`, corpoNotif(a.item));
  }
}
// alerta do serviço de ações (sem notificação do browser nem Web Push, CA-20); fechado ou tirado pelo tecto conta como fechado
export function accoes(s) {
  if (!box) return;
  if (accAnt && !F.some((x) => x.chave === accAnt.chave)) { accFechada = accAnt.chave; accAnt = null; }
  const act = accoesAlerta(s), p = accoesPasso(accAnt, act, accFechada);
  accFechada = p.fechada;
  if (p.op === "nada") return;
  if (p.op === "mostrar") F = fila(F.filter((x) => x.chave !== accAnt?.chave), { tipo: "alerta", accoes: true, ...act }, Date.now());
  else if (p.op === "actualizar") F = F.map((x) => (x.chave === act.chave ? { ...x, texto: act.texto } : x));
  else if (p.op === "tirar") F = F.filter((x) => x.chave !== accAnt.chave);
  accAnt = p.op === "tirar" ? null : act;
  pinta();
}
// corpo da notificação do sistema (ecrã bloqueado): nunca o título de uma aprovação, só o tipo (como o Web Push, ADR-004)
export const corpoNotif = (it) => (it?.tipo === "aprovacao" ? "Aprovação à tua espera" : String(it?.oque ?? ""));

const html = (a) => {
  const n = a.n > 1 ? ` <span class="av-n">×${a.n}</span>` : "";
  if (a.accoes) return accoesHtml(a, esc);
  if (a.tipo === "info") return `<p>${esc(a.texto)}${n}</p><button type="button" data-a="fechar">Fechar</button>`;
  const it = a.item, url = it.tipo === "pr" && PR_URL.test(it.pr_url ?? "") ? it.pr_url : null;
  const acc = url ? `<a class="av-r" href="${esc(url)}" target="_blank" rel="noopener" data-a="fechar">Responder</a>`
    : `<button type="button" data-a="ir">${it.tipo === "aprovacao" ? "Responder" : "Ver"}</button>`;
  return `<p><b>${esc(it.quem)}</b> precisa de ti: ${esc(it.oque)}${n}</p><div class="btns">${acc}<button type="button" data-a="fechar">Fechar</button></div>`;
};

// incremental: um cartão já no ecrã só muda o conteúdo (o role="alert" não volta a ser anunciado por cada poll)
function pinta() {
  F = expirar(F, Date.now());
  const vivos = new Set(F.map((a) => a.chave));
  for (const el of [...box.children]) if (!vivos.has(el.dataset.c)) el.remove();
  for (const a of F) {
    let el = [...box.children].find((x) => x.dataset.c === a.chave);
    if (!el) {
      el = document.createElement("div");
      el.className = "av av-" + (a.tipo === "info" ? "i" : "a");
      el.dataset.c = a.chave;
      el.setAttribute("role", a.tipo === "info" ? "status" : "alert");
      box.append(el);
    }
    const h = html(a);
    if (el._h !== h) { el.innerHTML = h; el._h = h; }
  }
  clearTimeout(tm);
  const info = F.filter((a) => a.tipo === "info");
  if (info.length) tm = setTimeout(pinta, Math.max(50, INFO_MS - (Date.now() - Math.min(...info.map((a) => a.em)))));
}
