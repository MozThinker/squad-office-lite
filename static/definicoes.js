// «Definições» (SPEC D15): notificações no telemóvel (Web Push, rotas api/push/*) e preferências da barra de progresso.
// Só carregado ao abrir o painel (import() no index.html), com o definicoes.css. As funções exportadas no topo são puras
// (sem window/document) para os testes em node.
// v3 (CA-1, 2, 39): por cima, música (ligar e volume), tema e notificações neste browser, que saíram do cabeçalho, e «Dia e noite»;
// o estado e as chaves do localStorage («music», «musicVol», «tema», «notify») continuam no index.html (api.musica, api.tema, api.notif).

export const EVENTOS = [["concluida", "Tarefa concluída"], ["falhada", "Tarefa falhada ou parada"], ["pr", "PR aberto"],
  ["alertas", "Alertas (tarefas paradas)"], ["aprovacoes", "Aprovações novas"], ["fases", "Progresso por fase"]];
// fases das tarefas Dev goal (progresso.FASES) e o nome visível
export const FASES = [["SPEC", "SPEC"], ["PLAN", "PLAN"], ["CODE", "código"], ["QA", "QA"], ["PR", "PR"]];
// squads da config (GET api/config) → [[id, nome]]
export const squadsDe = (cfg) => (cfg?.squads || []).map((s) => [s.id, s.nome]);
export const TEMAS = [["auto", "auto"], ["escuro", "escuro"], ["claro", "claro"], ["sepia", "sépia"], ["contraste", "contraste"]];
// notificações neste browser (api.notif.estado()): texto por baixo do interruptor
export const MSG_NB = {
  bloqueado: "Bloqueadas no browser: desbloqueia no cadeado ao lado do endereço → Notificações → Permitir, e recarrega a página.",
  ios: "No iPhone, as notificações só funcionam com a página instalada (iOS 16.4 ou mais recente): Partilhar → Adicionar ao ecrã principal, e abre a partir do ícone.",
  "sem-suporte": "Este browser não suporta notificações.", on: "", off: "",
};
// preferências de ligado/desligado em localStorage («0» = desligado; omissão ligado), como as da barra de progresso
export const AMB = [["diaNoite", "Dia e noite no escritório"]];
export const VIS = [["progBarra", "Mostrar barra de progresso"], ["progEstimativa", "Mostrar estimativa de tempo"]];
// = valores por omissão do servidor (SPEC D9); só para o formulário antes de haver resposta. As squads (todas ligadas) vêm da config: prefsOmissao(cfg)
export const PREFS_OMISSAO = { eventos: { concluida: true, falhada: true, pr: true, alertas: true, aprovacoes: false, fases: false },
  squads: {}, fases: { SPEC: false, PLAN: false, CODE: false, QA: true, PR: true },
  silencio: { ligado: false, de: "23:00", ate: "07:00" }, criticos_ignoram_silencio: true };
export const prefsOmissao = (cfg) => ({ ...PREFS_OMISSAO, squads: Object.fromEntries(squadsDe(cfg).map(([k]) => [k, true])) });
export const MSG = {
  teste: "Teste enviado. Deve chegar em segundos.", testeFalhou: "O teste falhou: ", servidor: "Não configurado no servidor.",
  semSuporte: "Este browser não suporta notificações no telemóvel.",
  ios: "No iPhone, instala primeiro o Office: Partilhar → Adicionar ao ecrã principal, e abre a partir do ícone (iOS 16.4 ou mais recente).",
  bloqueado: "As notificações estão bloqueadas para este site. Desbloqueia nas definições do browser e recarrega.",
  guardado: "Guardado.", naoGuardou: "Não foi possível guardar: ", fasesIphone: "No iPhone pode chegar um aviso por fase.",
};

// chave pública VAPID (base64url sem padding) → bytes para o applicationServerKey
export function b64uBytes(s) {
  const b = atob(String(s).replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (String(s).length % 4)) % 4));
  return Uint8Array.from(b, (c) => c.charCodeAt(0));
}
// a subscrição do browser foi feita com esta chave? (o servidor pode ter gerado uma chave nova)
export function mesmaChave(chaveSub, chave) {
  if (!chaveSub || !chave) return false;
  const a = new Uint8Array(chaveSub), b = b64uBytes(chave);
  return a.length === b.length && a.every((x, i) => x === b[i]);
}
export function suporte({ sw, push, notif, ios, standalone, permissao }) {
  if (ios && !standalone) return "ios-instalar";
  if (!sw || !push || !notif) return "sem-suporte";
  return permissao === "denied" ? "bloqueado" : "ok";
}
// valores do formulário (FormData como objecto) → prefs completas com as chaves exactas do contrato
export function prefsDoForm(v, squads = []) {
  const on = (k) => v[k] === "on" || v[k] === true;
  return { eventos: Object.fromEntries(EVENTOS.map(([k]) => [k, on("e-" + k)])),
    squads: Object.fromEntries(squads.map(([k]) => [k, on("s-" + k)])), fases: Object.fromEntries(FASES.map(([k]) => [k, on("f-" + k)])),
    silencio: { ligado: on("sil"), de: String(v.de ?? ""), ate: String(v.ate ?? "") }, criticos_ignoram_silencio: on("crit") };
}
// prefs guardadas antes do «Progresso por fase» (sem eventos.fases nem fases) → completas com as omissões
export function prefsCompletas(p) {
  return { ...p, eventos: { ...p?.eventos, fases: p?.eventos?.fases ?? PREFS_OMISSAO.eventos.fases },
    fases: { ...PREFS_OMISSAO.fases, ...p?.fases } };
}
// ao ligar o evento: com as 5 fases desligadas, liga QA e PR (SPEC D3)
export function fasesAoLigar(fases) {
  return Object.values(fases).some(Boolean) ? fases : { ...fases, QA: true, PR: true };
}
export function msgErro(codigo, corpo) {
  if (codigo === 503) return MSG.servidor;
  const e = typeof corpo?.error === "string" ? corpo.error.slice(0, 200) : "";
  return e || (codigo ? `erro ${codigo}` : "sem ligação ao servidor");
}

// ---- DOM

let A = null, dlg = null, chave = null, sub = null, SQ = [];
const $ = (s) => dlg.querySelector(s);
const caixa = (nome, rotulo, extra = "") => `<label class="def-c"><input type="checkbox" name="${nome}"${extra}> ${A.esc(rotulo)}</label>`;

function html() {
  return `<div class="rd-top"><h2 id="def-h">Definições</h2><button type="button" data-a="fechar">fechar</button></div><div class="def-b">
<section aria-labelledby="def-mh"><h3 id="def-mh">Música</h3><label class="def-c"><input type="checkbox" id="def-mus"> Música de fundo</label>
<label class="def-r" id="def-vr">Volume <input type="range" id="def-vol" min="0" max="100" step="10"></label></section>
<section aria-labelledby="def-th"><h3 id="def-th">Tema</h3><select id="def-tema" aria-label="Tema">${TEMAS.map(([v, r]) => `<option value="${v}">${r}</option>`).join("")}</select></section>
<section aria-labelledby="def-eh"><h3 id="def-eh">Escritório</h3>${AMB.map(([k, r]) => caixa(k, r, ` data-v="${k}"`)).join("")}
<p class="sub">Desligado: sempre de dia. A hora é a do fuso da configuração.</p></section>
<section aria-labelledby="def-nh"><h3 id="def-nh">Notificações neste browser</h3>
<label class="def-c"><input type="checkbox" id="def-nb"> Avisar quando um goal termina ou há um alerta (com o separador escondido)</label>
<p class="def-msg" id="def-nm" role="status" aria-live="polite"></p></section>
<section aria-labelledby="def-ph"><h3 id="def-ph">Notificações no telemóvel</h3>
${caixa("on", "Ligadas neste dispositivo", ' id="def-on"')}
<p class="def-msg" id="def-m" role="status" aria-live="polite"></p>
<button type="button" class="def-bt" data-a="teste">Enviar teste</button>
<form id="def-f"><fieldset><legend>Eventos</legend>${EVENTOS.map(([k, r]) => caixa("e-" + k, r)).join("")}<div class="def-fases" role="group" aria-label="Fases">
${FASES.map(([k, r]) => caixa("f-" + k, r)).join("")}<p class="sub">${MSG.fasesIphone}</p></div></fieldset>
<fieldset id="def-sq"><legend>Squads</legend></fieldset>
<fieldset><legend>Horas de silêncio</legend>${caixa("sil", "Horas de silêncio")}
<div class="def-h"><label>das <input type="time" name="de" required></label><label>às <input type="time" name="ate" required></label></div>
<p class="sub" id="def-tz"></p>
${caixa("crit", "Os alertas críticos ignoram o silêncio", ' aria-describedby="def-ch"')}
<p class="sub" id="def-ch">Críticos: tarefa que morreu.</p></fieldset>
<button type="submit" class="def-bt">Guardar</button><p class="def-msg" id="def-g" role="status" aria-live="polite"></p></form></section>
<section aria-labelledby="def-vh"><h3 id="def-vh">Barra de progresso</h3>${VIS.map(([k, r]) => caixa(k, r, ` data-v="${k}"`)).join("")}</section></div>`;
}

function css() {
  if (document.querySelector("link[data-definicoes]")) return Promise.resolve();
  return new Promise((ok) => {
    const l = document.createElement("link");
    Object.assign(l, { rel: "stylesheet", href: new URL("./definicoes.css", import.meta.url).href, onload: ok, onerror: ok });
    l.dataset.definicoes = "";
    document.head.append(l);
    setTimeout(ok, 3000); // rede lenta: não fica à espera do CSS
  });
}

const msg = (id, t) => { $(id).textContent = t; };
const ler = (k) => { try { return localStorage.getItem(k) !== "0"; } catch { return true; } };

function preencher(p) {
  p = prefsCompletas(p);
  const f = $("#def-f").elements;
  for (const [k] of EVENTOS) f["e-" + k].checked = !!p.eventos?.[k];
  for (const [k] of SQ) f["s-" + k].checked = !!p.squads?.[k];
  f.sil.checked = !!p.silencio?.ligado; f.de.value = p.silencio?.de || ""; f.ate.value = p.silencio?.ate || "";
  f.crit.checked = !!p.criticos_ignoram_silencio;
  for (const [k] of FASES) f["f-" + k].checked = !!p.fases[k];
  sincFases();
}
// as 5 fases só se escolhem com «Progresso por fase» ligado
function sincFases() {
  const f = $("#def-f").elements;
  for (const [k] of FASES) f["f-" + k].disabled = !f["e-fases"].checked;
}
function ligarFases(e) {
  const f = $("#def-f").elements;
  if (e.target.checked) {
    const v = fasesAoLigar(Object.fromEntries(FASES.map(([k]) => [k, f["f-" + k].checked])));
    for (const [k] of FASES) f["f-" + k].checked = v[k];
  }
  sincFases();
}
// ligado: prefs e teste activos; sem suporte: o interruptor também fica desactivado
function estado(ligado, pode = true) {
  $("#def-on").checked = ligado;
  $("#def-on").disabled = !pode;
  $('[data-a="teste"]').disabled = !ligado;
  for (const e of $("#def-f").querySelectorAll("fieldset, button")) e.disabled = !ligado;
  sincFases();
}

async function post(rota, corpo) {
  let r = null, j = null;
  try {
    r = await fetch("api/push/" + rota, { method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({ token: A.getState()?.token, ...corpo }) });
    j = await r.json().catch(() => null);
  } catch {}
  return { ok: !!r?.ok, codigo: r?.status || 0, j };
}
const subJson = (s) => { const j = s.toJSON(); return { endpoint: j.endpoint, keys: { p256dh: j.keys?.p256dh, auth: j.keys?.auth } }; };

async function lerChave() {
  try { const r = await fetch("api/push/chave", { cache: "no-store" }); if (r.ok) return (await r.json()).chave || null; } catch {}
  return null;
}

async function carregar() {
  const s = suporte({ sw: "serviceWorker" in navigator, push: "PushManager" in window, notif: "Notification" in window, ios: A.isIOS,
    standalone: matchMedia("(display-mode: standalone)").matches || navigator.standalone === true, permissao: globalThis.Notification?.permission });
  preencher(prefsOmissao(A.cfg));
  estado(false, s === "ok");
  if (s !== "ok") return msg("#def-m", { "ios-instalar": MSG.ios, "sem-suporte": MSG.semSuporte, bloqueado: MSG.bloqueado }[s]);
  msg("#def-m", "");
  const reg = await A.swReady;
  if (!reg?.pushManager) { estado(false, false); return msg("#def-m", MSG.semSuporte); }
  chave = await lerChave();
  if (!chave) { estado(false, false); return msg("#def-m", MSG.servidor); }
  sub = await reg.pushManager.getSubscription().catch(() => null);
  if (!sub || !mesmaChave(sub.options?.applicationServerKey, chave)) return; // chave nova no servidor: liga de novo
  const r = await post("subscrever", { subscricao: subJson(sub) }); // sem prefs: devolve as guardadas
  if (r.ok) { preencher(r.j.prefs); estado(true); } else msg("#def-m", MSG.naoGuardou + msgErro(r.codigo, r.j));
}

function ligar(e) {
  if (!e.target.checked) return desligar();
  // iOS: o pedido de permissão tem de sair no próprio gesto, antes de qualquer await
  const perm = Notification.permission === "granted" ? Promise.resolve("granted") : Notification.requestPermission();
  e.target.checked = false;
  (async () => {
    if ((await perm) !== "granted") return msg("#def-m", MSG.bloqueado);
    msg("#def-m", "");
    const reg = await A.swReady;
    chave ||= await lerChave();
    if (!chave) return msg("#def-m", MSG.servidor);
    try {
      const velha = await reg.pushManager.getSubscription();
      if (velha && !mesmaChave(velha.options?.applicationServerKey, chave)) await velha.unsubscribe();
      sub = velha && mesmaChave(velha.options?.applicationServerKey, chave) ? velha
        : await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uBytes(chave) });
    } catch { return msg("#def-m", MSG.semSuporte); }
    const r = await post("subscrever", { subscricao: subJson(sub) });
    if (!r.ok) return msg("#def-m", MSG.naoGuardou + msgErro(r.codigo, r.j));
    preencher(r.j.prefs); estado(true);
  })();
}

async function desligar() {
  estado(false);
  const s = sub;
  sub = null;
  if (!s) return;
  const r = await post("cancelar", { endpoint: s.endpoint });
  await s.unsubscribe().catch(() => {});
  msg("#def-m", r.ok ? "" : MSG.naoGuardou + msgErro(r.codigo, r.j));
}

async function teste(b) {
  if (!sub) return;
  b.disabled = true;
  const r = await post("teste", { endpoint: sub.endpoint });
  b.disabled = false;
  msg("#def-m", r.ok ? MSG.teste : MSG.testeFalhou + msgErro(r.codigo, r.j));
  if (r.codigo === 410 || r.codigo === 404) { await sub?.unsubscribe().catch(() => {}); sub = null; estado(false); } // o servidor já a esqueceu
}

async function guardar(e) {
  e.preventDefault();
  if (!sub) return;
  const v = Object.fromEntries(new FormData(e.target));
  for (const [k] of FASES) { const c = e.target.elements["f-" + k]; if (c.disabled) v["f-" + k] = c.checked; } // o FormData omite as desactivadas
  const r = await post("subscrever", { subscricao: subJson(sub), prefs: prefsDoForm(v, SQ) });
  if (r.ok) preencher(r.j.prefs);
  msg("#def-g", r.ok ? MSG.guardado : MSG.naoGuardou + msgErro(r.codigo, r.j));
}

// música, tema e notificações deste browser (o estado vive no index.html)
function sincPrefs() {
  const m = A.musica, n = A.notif?.estado();
  if (m) { $("#def-mus").checked = m.on(); $("#def-vol").value = m.volume(); $("#def-vr").hidden = !m.on(); }
  if (A.tema) $("#def-tema").value = A.tema.valor();
  if (n) { $("#def-nb").checked = n === "on"; $("#def-nb").disabled = !["on", "off"].includes(n); msg("#def-nm", MSG_NB[n] ?? ""); }
}

// api = { esc, cfg (GET api/config), swReady, isIOS, getState(), repintar(), musica, tema, notif }
export async function abrir(api) {
  A = api;
  SQ = squadsDe(api.cfg);
  if (!dlg) {
    await css();
    dlg = document.createElement("dialog");
    dlg.id = "def-dlg";
    dlg.setAttribute("aria-labelledby", "def-h");
    dlg.innerHTML = html();
    document.body.append(dlg);
    $("#def-on").addEventListener("change", ligar);
    $("#def-f").addEventListener("submit", guardar);
    $("#def-f").elements["e-fases"].addEventListener("change", ligarFases);
    dlg.addEventListener("click", (e) => { const a = e.target.closest("[data-a]")?.dataset.a; a === "fechar" ? dlg.close() : a === "teste" && teste(e.target.closest("[data-a]")); });
    dlg.addEventListener("change", (e) => { const k = e.target.dataset.v; if (!k) return;
      try { localStorage.setItem(k, e.target.checked ? "1" : "0"); } catch {}
      A.repintar(); });
    dlg.addEventListener("close", () => document.getElementById("def-b")?.focus());
    // alternar() no próprio clique: o AudioContext e o pedido de permissão têm de nascer no gesto (iOS)
    $("#def-mus").addEventListener("change", () => A.musica?.alternar().then(sincPrefs, sincPrefs));
    $("#def-vol").addEventListener("input", (e) => A.musica?.setVolume(+e.target.value));
    $("#def-tema").addEventListener("change", (e) => A.tema?.aplicar(e.target.value));
    $("#def-nb").addEventListener("change", () => { const p = A.notif?.alternar(); sincPrefs(); p?.then(sincPrefs, sincPrefs); });
  }
  sincPrefs();
  $("#def-sq").innerHTML = `<legend>Squads</legend>${SQ.map(([k, r]) => caixa("s-" + k, r)).join("")}`;
  $("#def-tz").textContent = "Fuso: " + (A.cfg?.fuso || "o do browser");
  for (const [k] of [...VIS, ...AMB]) $(`[data-v="${k}"]`).checked = ler(k);
  msg("#def-g", "");
  if (!dlg.open) dlg.showModal();
  await carregar();
}
