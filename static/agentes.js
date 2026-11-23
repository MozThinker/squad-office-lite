// «Squads e agentes» (ADR-017…019): agentes de cada squad (modelo, skills), agentes por descrição (rascunho → editar →
// ensaio → activar), squads a partir de um modelo e rotinas. Só carregado ao abrir a vista no ⚙ (import() no index.html);
// o agentes.css entra por <link> posto aqui. O Office nunca escreve agentes: cada mudança é um pedido v1 na fila
// (POST api/agentes/pedido) que o runner aplica na passagem seguinte.
// As funções exportadas no topo são puras (sem window/document) e testadas em node (tests/agentes_test.mjs).
// Regra do DOM: nada do servidor nem do utilizador entra como HTML; tudo entra por textContent/value (h()).

// ---- puras

export const MODELOS = ["inherit", "sonnet", "opus", "haiku"];
const CUSTO = { haiku: 1, sonnet: 2, opus: 3 };
export const ID_SQUAD = /^[a-z][a-z0-9-]{1,19}$/, ID_AGENTE = /^[a-z][a-z0-9-]{1,39}$/, HORA = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
export const TEXTO_MAX = 2000, CAND_MAX = 64 * 1024, ROTINAS_MAX = 5;
export const DIAS = [[1, "seg"], [2, "ter"], [3, "qua"], [4, "qui"], [5, "sex"], [6, "sáb"], [7, "dom"]];
// accao -> chaves exactas de args (PLAN §3.2; = agentes.ACCOES do servidor)
export const ACCOES = {
  modelo: ["squad", "agente", "sha256", "modelo"], skills: ["squad", "agente", "sha256", "skills"], agente_remover: ["squad", "agente", "sha256"],
  agente_rascunho: ["squad", "nome", "descricao"], agente_criar: ["squad", "nome", "sha256", "md"],
  agente_ensaio: ["squad", "nome", "sha256", "projecto", "tarefa"], agente_activar: ["squad", "nome", "sha256"], agente_descartar: ["squad", "nome"],
  squad_rascunho: ["squad", "modelo", "missao"], squad_criar: ["squad", "sha256"], squad_ensaio: ["squad", "sha256", "projecto", "tarefa"],
  squad_descartar: ["squad"], squad_desactivar: ["squad"], squad_conversa: ["squad", "texto"],
  rotina_pedir: ["squad", "projecto", "hora", "dias", "instrucoes", "tecto_usd", "max_turnos"],
  rotina_retomar: ["squad", "rotina"], rotina_cancelar: ["squad", "rotina"],
};
export const ACCAO_ROT = {
  modelo: "mudar o modelo", skills: "mudar as skills", agente_remover: "remover o agente", agente_rascunho: "rascunho do agente",
  agente_criar: "guardar o agente", agente_ensaio: "ensaiar o agente", agente_activar: "activar o agente", agente_descartar: "descartar o agente",
  squad_rascunho: "rascunho da squad", squad_criar: "criar a squad", squad_ensaio: "ensaiar o lead", squad_descartar: "descartar o rascunho",
  squad_desactivar: "desactivar a squad", squad_conversa: "conversa da squad", rotina_pedir: "pedir a rotina", rotina_retomar: "retomar a rotina", rotina_cancelar: "cancelar a rotina",
};
export const ESTADO_ROT = { pendente: "pendente", ok: "feito", erro: "erro" };
export const ROTINA_ROT = { pendente: "à espera de aprovação", aprovada: "activa", recusada: "recusada", pausada: "pausada (3 falhas seguidas)",
  cancelada: "cancelada" };
export const MSG = {
  demo: "Indisponível no modo demonstração.", fila: "A fila dos agentes está indisponível: corre o `configurar` uma vez (modo Docker).",
  rede: "Sem ligação ao servidor.", token: "A sessão mudou: recarrega a página.",
};

// subir para um modelo mais caro avisa (haiku < sonnet < opus); desconhecido ou igual → sem aviso
export const maisCaro = (de, para) => Object.hasOwn(CUSTO, de ?? "") && Object.hasOwn(CUSTO, para ?? "") && CUSTO[para] > CUSTO[de];
// modelo efectivo de um agente: sem campo ou «inherit» = o da sessão principal da squad
export const efectivo = (m, sessao) => (!m || m === "inherit" ? sessao ?? null : m);

// = agentes.texto_ok do servidor: n.º de caracteres (pontos de código); sem Cc (salvo \n e \t com linhas) nem Cf/Co/Cs (salvo ZWJ)
export function textoOk(s, mn, mx, linhas = false) {
  if (typeof s !== "string") return false;
  const n = [...s].length;
  if (n < mn || n > mx || !s.trim()) return false;
  for (const c of s) {
    if (linhas && (c === "\n" || c === "\t")) continue;
    if (c !== "\u200d" && /[\p{Cc}\p{Cf}\p{Co}\p{Cs}]/u.test(c)) return false;
  }
  return true;
}
const bytes = (s) => new TextEncoder().encode(s).length;

// corpo do POST: args com as chaves exactas da acção, por ordem
export function corpoPedido(token, accao, args) {
  return { token: token ?? null, accao, args: Object.fromEntries(ACCOES[accao].map((k) => [k, args[k]])) };
}
// = agentes.alvo do servidor: um pedido pendente por alvo
export function alvo(accao, a) {
  if (accao === "rotina_pedir") return `rotina:${a.squad}`;
  if (accao.startsWith("rotina_")) return `rotina:${a.squad}/${a.rotina}`;
  if (accao.startsWith("squad_")) return `squad:${a.squad}`;
  if (["modelo", "skills", "agente_remover"].includes(accao)) return `agente:${a.squad}/${a.agente}`;
  return `cand:${a.squad}/${a.nome}`;
}
// pedido mais recente de um alvo (ids AAAAMMDDTHHMMSS-hex ordenam pelo tempo)
export function ultimoPedido(pedidos, al) {
  let r = null;
  for (const p of pedidos || []) if (p?.alvo === al && (!r || String(p.id) > String(r.id))) r = p;
  return r;
}
export function rotuloPedido(p) {
  const e = ESTADO_ROT[p?.estado] || "?";
  return `${ACCAO_ROT[p?.accao] || p?.accao}: ${e}${p?.estado === "erro" && p.erro ? ` (${p.erro})` : ""}`;
}
export const rotuloRotina = (r) => ROTINA_ROT[r?.estado] || r?.estado || "?";
export const podeActivar = (c) => c?.ensaio?.estado === "ok" && !!c.sha256 && c.ensaio.sha256 === c.sha256;
// rotinas: squad do office.yaml, em worktree e com o modo task (o servidor e o runner revalidam)
export function podeRotina(s, cfg) {
  const c = (cfg?.squads || []).find((x) => x.id === s?.id);
  return !!c && (c.formato ?? s.formato) === "worktree" && (!Array.isArray(c.modos) || c.modos.includes("task"));
}
export const rotinasVivas = (s) => (s?.rotinas || []).filter((r) => !["cancelada", "recusada"].includes(r.estado)).length;
export function sessaoTexto(x) {
  if (!x?.modelo || x.fonte === "omissao") return "omissão do claude";
  return `${x.modelo} (${x.fonte === "squad" ? "squads[].modelo" : "runner.modelo"})`;
}
export function msgErro(codigo, corpo) {
  if (codigo === 503) return corpo?.motivo === "demo" ? MSG.demo : MSG.fila;
  if (codigo === 403) return MSG.token;
  if (!codigo) return MSG.rede;
  const e = typeof corpo?.error === "string" ? corpo.error.slice(0, 200) : "";
  return e || `erro ${codigo}`;
}

// formulários → { erro } ou { args } (os limites do PLAN §3.2; o servidor e o runner revalidam)
export function validarAgenteNovo(v, s) {
  const nome = String(v?.nome ?? "").trim();
  if (!ID_AGENTE.test(nome)) return { erro: "Nome: minúsculas, dígitos e -, 2 a 40, a começar por letra." };
  if ((s?.agentes || []).some((a) => a.nome === nome) || (s?.candidatos || []).some((c) => c.nome === nome)) return { erro: "Já existe um agente com esse nome." };
  if (!textoOk(v?.descricao, 1, TEXTO_MAX, true)) return { erro: `Descrição: 1 a ${TEXTO_MAX} caracteres, sem caracteres de controlo nem invisíveis.` };
  return { args: { squad: s.id, nome, descricao: v.descricao } };
}
export function validarMd(md) {
  if (typeof md !== "string" || !md.startsWith("---")) return { erro: "O ficheiro tem de começar pelo frontmatter (---)." };
  if (bytes(md) > CAND_MAX) return { erro: "O ficheiro passa os 64 KB." };
  return {};
}
export function validarEnsaio(v, projectos) {
  if (!(projectos || []).includes(v?.projecto)) return { erro: "Escolhe um projecto." };
  if (!textoOk(v?.tarefa, 1, TEXTO_MAX, true)) return { erro: `Tarefa de ensaio: 1 a ${TEXTO_MAX} caracteres.` };
  return { args: { projecto: v.projecto, tarefa: v.tarefa } };
}
export function validarSquadNova(v, D) {
  const id = String(v?.squad ?? "").trim();
  if (!(D?.modelos || []).some((m) => m.id === v?.modelo)) return { erro: "Escolhe um modelo." };
  if (!ID_SQUAD.test(id)) return { erro: "Id: minúsculas, dígitos e -, 2 a 20, a começar por letra." };
  const s = (D?.squads || []).find((x) => x.id === id);
  if (s?.agentes?.length) return { erro: "Essa squad já tem agentes." };
  if (!textoOk(v?.missao, 1, TEXTO_MAX, true)) return { erro: `Missão: 1 a ${TEXTO_MAX} caracteres, sem caracteres de controlo nem invisíveis.` };
  return { args: { squad: id, modelo: v.modelo, missao: v.missao } };
}
export function validarRotina(v, s, projectos) {
  if (rotinasVivas(s) >= ROTINAS_MAX) return { erro: `Limite de ${ROTINAS_MAX} rotinas por squad.` };
  if (!(projectos || []).includes(v?.projecto)) return { erro: "Escolhe um projecto." };
  if (!HORA.test(String(v?.hora ?? ""))) return { erro: "Hora no formato HH:MM." };
  const dias = [...new Set((v?.dias || []).map(Number))].sort((a, b) => a - b);
  if (!dias.length || dias.some((d) => !Number.isInteger(d) || d < 1 || d > 7)) return { erro: "Escolhe pelo menos um dia." };
  if (!textoOk(v?.instrucoes, 1, TEXTO_MAX, true)) return { erro: `Instruções: 1 a ${TEXTO_MAX} caracteres, sem caracteres de controlo nem invisíveis.` };
  const t = String(v?.tecto_usd ?? "").trim().replace(",", ".");
  const tn = /^\d+(\.\d{1,2})?$/.test(t) ? Number(t) : NaN;
  if (!(tn >= 0.1 && tn <= 5)) return { erro: "Tecto por execução: de 0.10 a 5.00 USD." };
  const m = String(v?.max_turnos ?? "").trim();
  if (!/^\d{1,2}$/.test(m) || +m < 1 || +m > 30) return { erro: "Turnos: de 1 a 30." };
  return { args: { squad: s.id, projecto: v.projecto, hora: v.hora, dias, instrucoes: v.instrucoes, tecto_usd: tn.toFixed(2), max_turnos: +m } };
}
// bloco para colar na lista squads: do office.yaml (squad registada que lá não está; PLAN §3.5)
// ---- conversa de montagem (ADR-020): «+ Squad» descreve o fim, a IA pergunta (até 3 rondas) e propõe um modelo
export const CONVERSA_MAX = 1000, CONVERSA_ROT = { a_perguntar: "a IA fez perguntas", proposta: "proposta pronta", nao_da: "isto não dá",
  falhou: "terminou sem squad" };
export const AVISO_API = "Squad de negócio: recomendamos uma chave de API (ANTHROPIC_API_KEY no claude.env) em vez da sessão da subscrição: "
  + "os termos do plano de consumidor não cobrem uso comercial automático.";
// a conversa à espera de resposta (só há uma de cada vez; CA-11a)
export const conversaActiva = (D) => (D?.conversas || []).find((c) => c?.estado === "a_perguntar") ?? null;
export const conversaDe = (D, id) => (D?.conversas || []).find((c) => c?.squad === id) ?? null;
export const ehNegocio = (D, modelo) => (D?.modelos || []).some((m) => m.id === modelo && m.negocio === true);
// CA-33: recomendação de chave de API quando a proposta (ou a escolha à mão) é um modelo de negócio
export const avisoNegocio = (D, modelo) => (ehNegocio(D, modelo) ? AVISO_API : null);
export function custoTexto(c) {
  const f = (x) => (Number(x) || 0).toFixed(2).replace(".", ",");
  return `${f(c?.custo_usd)} de ${f(c?.tecto_usd ?? 1)} USD · chamada ${c?.chamadas ?? 0} de ${c?.chamadas_max ?? 4}`;
}
export function validarConversa(v, D) {
  const id = String(v?.squad ?? "").trim();
  if (!ID_SQUAD.test(id)) return { erro: "Id: minúsculas, dígitos e -, 2 a 20, a começar por letra." };
  if ((D?.squads || []).find((x) => x.id === id)?.agentes?.length) return { erro: "Essa squad já tem agentes." };
  const outra = conversaActiva(D), minha = conversaDe(D, id);
  if (outra && outra.squad !== id) return { erro: `Já há uma conversa em curso (squad ${outra.squad}): responde-lhe ou descarta-a.` };
  if (minha && minha.estado !== "a_perguntar") return { erro: "A conversa desta squad já terminou: descarta-a para começar outra." };
  if (!minha && (D?.squads || []).some((x) => x.id === id && x.rascunho)) return { erro: "Essa squad já tem um rascunho: cria-a ou descarta-o." };
  if (!textoOk(v?.texto, 1, CONVERSA_MAX, true)) return { erro: `Texto: 1 a ${CONVERSA_MAX} caracteres, sem caracteres de controlo nem invisíveis.` };
  return { args: { squad: id, texto: v.texto } };
}
// «Escolher o modelo à mão» a partir de uma proposta: o formulário manual já preenchido (pede um rascunho novo)
export function manualDaProposta(c, s) {
  if (c?.estado !== "proposta" || !c.modelo_squad) return null;
  return { modelo: c.modelo_squad, squad: c.squad, missao: s?.rascunho?.missao ?? "" };
}

export const blocoYaml = (s) => `  - id: ${s.id}\n    nome: "${String(s.nome).replace(/[\\"]/g, (c) => "\\" + c)}"\n    lead: ${s.lead}\n`;
export const diasTexto = (ds) => (ds || []).map((d) => DIAS.find(([n]) => n === d)?.[1] ?? d).join(", ");

// ---- DOM

let A = null, dlg = null, corpo = null, D = null, carga = "", poll = 0, cssP = null;
const F = {}, errs = {}, msgs = new Map(), vistos = new Set(), enviando = new Set();
let ed = null; // candidato em edição: { squad, nome, md, sha256 } (md só do GET api/agentes/candidato)
const ensaios = new Set(); // formulários de ensaio abertos («cand:sq/nome», «squad:sq»)
const abertos = new Set(); // <details> abertos (sobrevivem ao repintar)

// elemento com atributos/propriedades e filhos em texto (nunca HTML)
function h(tag, at = {}, ...filhos) {
  const e = document.createElement(tag), props = {};
  for (const [k, v] of Object.entries(at)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (["value", "checked", "disabled"].includes(k)) props[k] = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const f of filhos.flat(Infinity)) if (f != null && f !== false) e.append(f instanceof Node ? f : String(f));
  Object.assign(e, props);
  return e;
}
const val = (fk, campo, omissao = "") => (F[fk] ||= {})[campo] ?? omissao;
// texto: só guarda (repintar a meio de uma composição do teclado do telemóvel estragava-a); select repinta (aviso de custo)
const guarda = (fk, campo, repinta) => (e) => { (F[fk] ||= {})[campo] = e.target.value; if (repinta) render(); };
// campo com rótulo; o valor vive em F (sobrevive ao repintar do poll)
function campo(rot, fk, nome, { opcoes, ...at } = {}, tag = "input") {
  const sel = tag === "select", e = h(tag, { ...at, "data-f": `${fk}:${nome}`, value: val(fk, nome, at.value ?? ""),
    [sel ? "onchange" : "oninput"]: guarda(fk, nome, sel) }, (opcoes || []).map(([v, r]) => h("option", { value: v }, r)));
  return h("label", { class: "ag-campo" }, h("span", {}, rot), e);
}
// valida no clique: erro por baixo do formulário, ou pede
function enviarSe(fk, r, fn) {
  if (r.erro) { errs[fk] = r.erro; return render(); }
  delete errs[fk];
  fn(r.args);
}
const dets = (k, resumo, ...filhos) => h("details", { class: "ag-mudar", open: abertos.has(k),
  ontoggle: (e) => { e.target.open ? abertos.add(k) : abertos.delete(k); } }, h("summary", {}, resumo), ...filhos);
const erroDe = (fk) => (errs[fk] ? h("p", { class: "ag-erro", role: "alert" }, errs[fk]) : null);
const botao = (t, fn, at = {}) => h("button", { type: "button", class: "ag-btn", ...at, onclick: fn }, t);
const p = (t, cls = "sub") => h("p", { class: cls }, t);
// peça de estado (estado.js/estado.css, fase 1 de UX) feita com h(), como o resto do módulo (sem dependências)
const aCarregar = () => h("p", { class: "sub" }, h("span", { class: "estado", "data-estado": "a-processar", "aria-live": "polite" },
  h("span", { class: "estado-x" }, "a carregar os agentes"), h("span", { class: "estado-t", "aria-hidden": "true" })));
const mostraMsg = (al) => (msgs.has(al) ? h("p", { class: "ag-erro", role: "alert" }, msgs.get(al)) : null);
const escrita = () => D?.escrita === true;

function css() {
  return (cssP ||= new Promise((ok) => {
    const l = document.createElement("link");
    Object.assign(l, { rel: "stylesheet", href: new URL("./agentes.css", import.meta.url).href, onload: ok, onerror: ok });
    document.head.append(l);
    setTimeout(ok, 3000); // rede lenta: não fica à espera do CSS
  }));
}

// estado de um alvo: pendente (de qualquer sessão) ou feito/erro dos pedidos feitos nesta sessão
function estadoAlvo(al) {
  if (enviando.has(al)) return { ocupado: true, el: p("a enviar…", "ag-pend") };
  const u = ultimoPedido(D?.pedidos, al);
  if (!u) return { ocupado: false, el: mostraMsg(al) };
  if (u.estado === "pendente") return { ocupado: true, el: h("p", { class: "ag-pend" }, rotuloPedido(u), " — o runner trata-o na próxima passagem.") };
  const el = vistos.has(u.id) ? h("p", { class: u.estado === "erro" ? "ag-erro" : "ag-ok", role: u.estado === "erro" ? "alert" : null }, rotuloPedido(u)) : null;
  return { ocupado: false, el: mostraMsg(al) || el };
}

async function pedir(accao, args, depois) {
  const al = alvo(accao, args);
  if (enviando.has(al)) return;
  enviando.add(al); msgs.delete(al); render();
  let r = null, j = null;
  try {
    r = await fetch("api/agentes/pedido", { method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify(corpoPedido(A.getState?.()?.token, accao, args)) });
    j = await r.json().catch(() => null);
  } catch {}
  enviando.delete(al);
  if (r?.status === 202) { if (j?.id) vistos.add(j.id); depois?.(); return carregar(); }
  msgs.set(al, msgErro(r?.status || 0, j));
  if (r?.status === 409) return carregar();
  render();
}

async function carregar() {
  clearTimeout(poll);
  try {
    const r = await fetch("api/agentes", { cache: "no-store" });
    const j = await r.json().catch(() => null);
    if (r.ok && j?.v === 1) { D = j; carga = ""; } else carga = msgErro(r.status, j);
  } catch { carga = MSG.rede; }
  if (ed && !D?.squads?.some((s) => s.id === ed.squad && (s.candidatos || []).some((c) => c.nome === ed.nome))) ed = null;
  render();
  if (dlg?.open && (D?.pedidos || []).some((x) => x.estado === "pendente")) poll = setTimeout(carregar, 15000);
}

// repinta tudo e devolve o foco ao mesmo campo (o poll não rouba o cursor)
function render() {
  if (!corpo) return;
  const a = document.activeElement, fk = a && corpo.contains(a) ? a.dataset.f || a.dataset.k : null, sel = a?.selectionStart, top = corpo.scrollTop;
  corpo.replaceChildren(...conteudo().filter(Boolean));
  corpo.scrollTop = top;
  if (!fk) return;
  const n = corpo.querySelector(`[data-f="${CSS.escape(fk)}"], [data-k="${CSS.escape(fk)}"]`);
  if (!n) return;
  n.focus({ preventScroll: true });
  try { if (sel != null) n.setSelectionRange(sel, sel); } catch {}
}

function conteudo() {
  if (!D) return [carga ? h("div", { class: "ag-erro", role: "alert" }, carga, " ", botao("Tentar de novo", carregar, { "data-k": "repetir" })) : aCarregar()];
  return [
    carga ? h("p", { class: "ag-erro", role: "alert" }, carga) : null,
    escrita() ? null : h("p", { class: "ag-aviso" }, "Só leitura: ", MSG.fila),
    ...(D.squads || []).map(squadEl),
    D.squads?.length ? null : p("Sem squads."),
    novaSquadEl(), pedidosEl(),
  ];
}

function squadEl(s) {
  const projs = D.projectos || [];
  return h("section", { class: "ag-sq", "aria-labelledby": `ag-h-${s.id}` },
    h("h3", { id: `ag-h-${s.id}` }, s.nome, " ", h("span", { class: "sub" }, `(${s.id})`)),
    p(`Sessão principal: ${sessaoTexto(s.sessao)} · lead ${s.lead}. O modelo da sessão muda-se em squads[].modelo do office.yaml.`),
    s.instrucoes ? p("Tem instruções da squad (CLAUDE.md).") : null,
    s.origem === "registo" ? blocoEl(s) : null,
    h("h4", {}, "Agentes"),
    s.agentes?.length ? h("ul", { class: "ag-l" }, s.agentes.map((a) => agenteEl(s, a))) : p("Ainda sem agentes."),
    candidatosEl(s, projs),
    novoAgenteEl(s),
    s.rascunho ? rascunhoEl(s) : null,
    s.modelo_squad ? ensaioLeadEl(s, projs) : null,
    rotinasEl(s, projs),
    s.modelo_squad ? desactivarEl(s) : null);
}

function blocoEl(s) {
  const txt = blocoYaml(s);
  return h("div", { class: "ag-bloco" },
    p(`Squad criada a partir de um modelo${s.modelo_squad ? ` (${(D.modelos || []).find((m) => m.id === s.modelo_squad)?.nome || s.modelo_squad})` : ""}. Se ainda não está no office.yaml, cola este bloco na lista squads: (as rotinas só funcionam depois).`, ""),
    h("pre", { class: "ag-pre" }, txt),
    botao("Copiar bloco", (e) => { navigator.clipboard?.writeText(txt).then(() => { e.target.textContent = "Copiado"; }, () => {}); }, { "data-k": `copiar:${s.id}` }));
}

function agenteEl(s, a) {
  if (a.ilegivel) return h("li", { class: "ag-ag" }, h("strong", {}, a.ficheiro), " — ilegível (frontmatter inválido, grande demais ou ligação simbólica).");
  const al = alvo("modelo", { squad: s.id, agente: a.nome }), st = estadoAlvo(al), fk = al, dis = st.ocupado || !escrita();
  const sel = val(fk, "modelo", a.model ?? ""), sks = val(fk, "skills", null) ?? (a.skills || []);
  const de = efectivo(a.model, s.sessao?.modelo), para = efectivo(sel, s.sessao?.modelo);
  const mudouSk = [...sks].sort().join() !== [...(a.skills || [])].sort().join();
  const marcar = (n) => (e) => { const v = new Set(val(fk, "skills", null) ?? a.skills ?? []); e.target.checked ? v.add(n) : v.delete(n); F[fk].skills = [...v]; };
  return h("li", { class: "ag-ag" },
    h("p", { class: "ag-nome" }, h("strong", {}, a.nome), a.lead ? h("span", { class: "ag-tag" }, "lead") : null),
    a.description ? p(a.description, "ag-desc") : null,
    p(`Modelo: ${a.model || "sem campo (o da sessão)"} · skills: ${a.skills?.length ? a.skills.join(", ") : "nenhuma"}${a.tools?.length ? ` · ferramentas: ${a.tools.join(", ")}` : ""}`),
    st.el,
    dets(`${fk}:mudar`, "Mudar modelo ou skills",
      a.lead ? p("O lead corre como sessão principal: o modelo dele é o de squads[].modelo do office.yaml.")
        : h("div", { class: "ag-f" },
          campo("Modelo", fk, "modelo", { value: a.model ?? "", disabled: dis,
            opcoes: [["", "sem campo (o da sessão)"], ...MODELOS.map((m) => [m, m])] }, "select"),
          maisCaro(de, para) ? h("p", { class: "ag-aviso", role: "status" }, `${para} é um modelo mais caro do que ${de}: cada tarefa desta squad passa a custar mais.`) : null,
          botao("Pedir mudança de modelo", () => pedir("modelo", { squad: s.id, agente: a.nome, sha256: a.sha256, modelo: sel }, () => delete F[fk]),
            { disabled: dis || sel === (a.model ?? ""), "data-k": `${fk}:bm` })),
      s.skills?.length
        ? h("fieldset", { class: "ag-f", disabled: dis }, h("legend", {}, "Skills"),
          s.skills.map((k) => h("label", { class: "ag-c" }, h("input", { type: "checkbox", checked: sks.includes(k.nome), "data-f": `${fk}:sk:${k.nome}`,
            onchange: (e) => { marcar(k.nome)(e); render(); } }), " ", k.nome, k.description ? h("span", { class: "sub" }, ` — ${k.description}`) : null)),
          botao("Pedir mudança de skills", () => pedir("skills", { squad: s.id, agente: a.nome, sha256: a.sha256, skills: [...sks] }, () => delete F[fk]),
            { disabled: dis || !mudouSk, "data-k": `${fk}:bs` }))
        : p("Esta squad não tem skills (pasta skills/<nome>/SKILL.md).")),
    a.lead ? null : h("div", { class: "ag-bts" }, botao("Remover agente", () => confirm(`Remover o agente ${a.nome}? Fica uma cópia em agents/.removidos/.`)
      && pedir("agente_remover", { squad: s.id, agente: a.nome, sha256: a.sha256 }), { class: "ag-btn ag-perigo", disabled: dis, "data-k": `${fk}:rm` })));
}

function ensaioTexto(k, e, sha) {
  if (!e) return p("Ainda sem ensaio.");
  if (e.sha256 !== sha) return p("O ensaio é de outra versão: ensaia de novo.", "ag-aviso");
  return h("div", {},
    h("p", { class: e.estado === "ok" ? "ag-ok" : "ag-erro" }, e.estado === "ok" ? "Ensaio ok." : `Ensaio falhou${e.motivo ? `: ${e.motivo}` : "."}`,
      e.custo_usd != null ? ` (${Number(e.custo_usd).toFixed(2)} USD)` : ""),
    e.nao_ensaiadas?.length ? p(`Ferramentas não ensaiadas (o ensaio é só de leitura): ${e.nao_ensaiadas.join(", ")}.`) : null,
    e.relatorio ? dets(`${k}:rel`, "Relatório do ensaio", h("pre", { class: "ag-pre" }, e.relatorio)) : null);
}

function ensaioForm(k, projs, dis, enviar) {
  if (!ensaios.has(k)) return null;
  return h("div", { class: "ag-f" },
    p("O ensaio corre num clone descartável do projecto, só de leitura, até 10 minutos e 2 USD."),
    campo("Projecto", k, "projecto", { opcoes: [["", "—"], ...projs.map((x) => [x, x])], disabled: dis }, "select"),
    campo("O que pedir no ensaio", k, "tarefa", { rows: 3, maxlength: TEXTO_MAX, disabled: dis }, "textarea"),
    erroDe(k),
    botao("Ensaiar", () => enviarSe(k, validarEnsaio({ projecto: val(k, "projecto"), tarefa: val(k, "tarefa") }, projs),
      (args) => enviar(args, () => { ensaios.delete(k); delete F[k]; })), { disabled: dis, "data-k": `${k}:be` }));
}

function candidatosEl(s, projs) {
  const pend = (D.pedidos || []).filter((x) => x.estado === "pendente" && x.accao === "agente_rascunho" && x.alvo?.startsWith(`cand:${s.id}/`)
    && !(s.candidatos || []).some((c) => x.alvo === `cand:${s.id}/${c.nome}`));
  if (!s.candidatos?.length && !pend.length) return null;
  return h("div", {}, h("h4", {}, "Agentes por activar"),
    pend.map((x) => p(`${x.alvo.split("/")[1]}: a gerar o rascunho… (na próxima passagem do runner)`, "ag-pend")),
    h("ul", { class: "ag-l" }, (s.candidatos || []).map((c) => {
      const al = alvo("agente_activar", { squad: s.id, nome: c.nome }), st = estadoAlvo(al), dis = st.ocupado || !escrita();
      const aEditar = ed?.squad === s.id && ed.nome === c.nome, emEnsaio = ensaios.has(al);
      return h("li", { class: "ag-ag" },
        h("p", { class: "ag-nome" }, h("strong", {}, c.nome), h("span", { class: "ag-tag" }, "candidato")),
        c.description ? p(c.description, "ag-desc") : null,
        p(`Modelo: ${c.model || "sem campo"}${c.tools?.length ? ` · ferramentas: ${c.tools.join(", ")}` : ""}${c.skills?.length ? ` · skills: ${c.skills.join(", ")}` : ""}`),
        ensaioTexto(al, c.ensaio, c.sha256), st.el,
        aEditar ? editorEl(s, c, dis) : null,
        ensaioForm(al, projs, dis, (args, ok) => pedir("agente_ensaio", { squad: s.id, nome: c.nome, sha256: c.sha256, ...args }, ok)),
        h("div", { class: "ag-bts" },
          aEditar ? null : botao("Editar", () => editar(s, c), { disabled: dis, "data-k": `${al}:ed` }),
          emEnsaio ? null : botao("Ensaiar", () => { ensaios.add(al); render(); }, { disabled: dis || !projs.length, "data-k": `${al}:en` }),
          botao("Activar", () => pedir("agente_activar", { squad: s.id, nome: c.nome, sha256: c.sha256 }),
            { disabled: dis || !podeActivar(c), title: podeActivar(c) ? null : "Precisa de um ensaio ok desta versão", "data-k": `${al}:ac` }),
          botao("Descartar", () => confirm(`Descartar o candidato ${c.nome}?`) && pedir("agente_descartar", { squad: s.id, nome: c.nome }),
            { class: "ag-btn ag-perigo", disabled: dis, "data-k": `${al}:ds` })));
    })));
}

async function editar(s, c) {
  const al = alvo("agente_criar", { squad: s.id, nome: c.nome });
  msgs.delete(al);
  try {
    const r = await fetch(`api/agentes/candidato?squad=${encodeURIComponent(s.id)}&nome=${encodeURIComponent(c.nome)}`, { cache: "no-store" });
    const j = await r.json().catch(() => null);
    if (r.ok && typeof j?.md === "string") ed = { squad: s.id, nome: c.nome, md: j.md, sha256: j.sha256 };
    else msgs.set(al, r.status === 404 ? "O candidato já não existe." : msgErro(r.status, j));
  } catch { msgs.set(al, MSG.rede); }
  render();
}

function editorEl(s, c, dis) {
  const mudou = ed.sha256 !== c.sha256, fk = `ed:${s.id}/${c.nome}`;
  return h("div", { class: "ag-f" },
    h("label", { class: "ag-campo" }, h("span", {}, `Ficheiro agents/${c.nome}.md`),
      h("textarea", { class: "ag-md", rows: 14, spellcheck: "false", "data-f": fk, value: ed.md, disabled: dis,
        oninput: (e) => { ed.md = e.target.value; } })),
    mudou ? p("O candidato mudou entretanto: fecha e abre de novo antes de guardar.", "ag-aviso") : null,
    p("Guardar cria uma versão nova: tem de ser ensaiada outra vez antes de activar."), erroDe(fk),
    h("div", { class: "ag-bts" },
      botao("Guardar", () => enviarSe(fk, validarMd(ed.md), () => pedir("agente_criar", { squad: s.id, nome: c.nome, sha256: ed.sha256 ?? null, md: ed.md },
        () => { ed = null; })),
      { disabled: dis || mudou, "data-k": `ed:${s.id}:g` }),
      botao("Fechar", () => { ed = null; render(); }, { "data-k": `ed:${s.id}:x` })));
}

function novoAgenteEl(s) {
  const fk = `novo:${s.id}`, nome = String(val(fk, "nome")).trim(), al = alvo("agente_rascunho", { squad: s.id, nome }), st = estadoAlvo(al);
  const dis = !escrita() || st.ocupado;
  return dets(fk, "Criar agente por descrição",
    h("div", { class: "ag-f" },
      p("Descreve o que o agente deve fazer; o runner pede um rascunho ao claude (sem ferramentas, até 0,50 USD) que depois podes editar e ensaiar."),
      campo("Nome do agente", fk, "nome", { autocomplete: "off", autocapitalize: "none", spellcheck: "false", maxlength: 40, disabled: dis }),
      campo("Descrição", fk, "descricao", { rows: 4, maxlength: TEXTO_MAX, disabled: dis }, "textarea"),
      erroDe(fk), nome ? st.el : null,
      botao("Pedir rascunho", () => enviarSe(fk, validarAgenteNovo({ nome: val(fk, "nome"), descricao: val(fk, "descricao") }, s),
        (args) => pedir("agente_rascunho", args, () => { F[fk] = {}; abertos.delete(fk); })), { disabled: dis, "data-k": `${fk}:b` })));
}

function rascunhoEl(s) {
  const r = s.rascunho, al = alvo("squad_criar", { squad: s.id }), st = estadoAlvo(al), dis = st.ocupado || !escrita();
  return h("div", { class: "ag-bloco" }, h("h4", {}, `Rascunho da squad (modelo ${r.modelo})`),
    h("p", { class: "ag-desc" }, r.missao),
    h("ul", { class: "ag-l" }, (r.agentes || []).map((a) => h("li", {}, h("strong", {}, a.nome), a.description ? ` — ${a.description}` : ""))),
    st.el,
    h("div", { class: "ag-bts" },
      botao("Criar squad", () => pedir("squad_criar", { squad: s.id, sha256: r.sha256 }), { disabled: dis, "data-k": `${al}:cr` }),
      botao("Descartar rascunho", () => confirm("Descartar o rascunho desta squad?") && pedir("squad_descartar", { squad: s.id }),
        { class: "ag-btn ag-perigo", disabled: dis, "data-k": `${al}:ds` })));
}

function ensaioLeadEl(s, projs) {
  const lead = (s.agentes || []).find((a) => a.lead && !a.ilegivel);
  if (!lead) return null;
  const al = alvo("squad_ensaio", { squad: s.id }), st = estadoAlvo(al), dis = st.ocupado || !escrita();
  return h("div", {}, h("h4", {}, "Ensaio do lead"), p("As rotinas desta squad só correm com um ensaio ok do lead actual."),
    ensaioTexto(al, s.ensaio_lead, lead.sha256), st.el,
    ensaioForm(al, projs, dis, (args, ok) => pedir("squad_ensaio", { squad: s.id, sha256: lead.sha256, ...args }, ok)),
    ensaios.has(al) ? null : botao("Ensaiar o lead", () => { ensaios.add(al); render(); }, { disabled: dis || !projs.length, "data-k": `${al}:en` }));
}

function desactivarEl(s) {
  const al = alvo("squad_desactivar", { squad: s.id }), st = estadoAlvo(al);
  return h("div", { class: "ag-bts" },
    botao("Desactivar squad", () => confirm(`Desactivar a squad ${s.nome}? As rotinas dela são canceladas e os agentes ficam numa cópia de segurança.`)
      && pedir("squad_desactivar", { squad: s.id }), { class: "ag-btn ag-perigo", disabled: st.ocupado || !escrita() || !!s.rascunho, "data-k": `${al}:dx` }));
}

function rotinasEl(s, projs) {
  const rs = s.rotinas || [], fk = `rot:${s.id}`, al = alvo("rotina_pedir", { squad: s.id }), st = estadoAlvo(al), pode = podeRotina(s, A.cfg);
  const dis = !escrita() || st.ocupado, dias = val(fk, "dias", null) ?? [];
  const v = { projecto: val(fk, "projecto"), hora: val(fk, "hora"), dias, instrucoes: val(fk, "instrucoes"), tecto_usd: val(fk, "tecto_usd", "0.50"),
    max_turnos: val(fk, "max_turnos", "10") };
  return h("div", {}, h("h4", {}, "Rotinas"),
    rs.length ? h("ul", { class: "ag-l" }, rs.map((x) => rotinaEl(s, x))) : p("Sem rotinas."),
    !pode ? p("Só squads do office.yaml em worktree e com o modo task podem ter rotinas.")
      : dets(fk, "Pedir uma rotina",
        h("div", { class: "ag-f" },
          p("A rotina corre como tarefa task da squad, à hora marcada nos dias escolhidos (fuso da configuração), e só depois de a aprovares em Aprovações."),
          campo("Projecto", fk, "projecto", { opcoes: [["", "—"], ...projs.map((x) => [x, x])], disabled: dis }, "select"),
          campo("Hora", fk, "hora", { type: "time", disabled: dis }),
          h("fieldset", { class: "ag-dias", disabled: dis }, h("legend", {}, "Dias"),
            DIAS.map(([n, rot]) => h("label", { class: "ag-c" }, h("input", { type: "checkbox", checked: dias.includes(n), "data-f": `${fk}:d${n}`,
              onchange: (e) => { const d = new Set(val(fk, "dias", null) ?? []); e.target.checked ? d.add(n) : d.delete(n); F[fk].dias = [...d]; render(); } }), " ", rot))),
          campo("Instruções", fk, "instrucoes", { rows: 4, maxlength: TEXTO_MAX, disabled: dis }, "textarea"),
          h("div", { class: "ag-par" },
            campo("Tecto por execução (USD)", fk, "tecto_usd", { inputmode: "decimal", value: "0.50", maxlength: 5, disabled: dis }),
            campo("Máximo de turnos", fk, "max_turnos", { inputmode: "numeric", value: "10", maxlength: 2, disabled: dis })),
          erroDe(fk), st.el,
          botao("Pedir aprovação", () => enviarSe(fk, validarRotina({ ...v, hora: val(fk, "hora"), instrucoes: val(fk, "instrucoes"),
            tecto_usd: val(fk, "tecto_usd", "0.50"), max_turnos: val(fk, "max_turnos", "10"), projecto: val(fk, "projecto") }, s, projs),
          (args) => pedir("rotina_pedir", args, () => { F[fk] = {}; abertos.delete(fk); })), { disabled: dis, "data-k": `${fk}:b` }))));
}

function rotinaEl(s, x) {
  const al = alvo("rotina_retomar", { squad: s.id, rotina: x.id }), st = estadoAlvo(al), dis = st.ocupado || !escrita();
  return h("li", { class: "ag-ag" },
    h("p", { class: "ag-nome" }, h("strong", {}, x.id), h("span", { class: `ag-tag ag-r-${x.estado}` }, rotuloRotina(x))),
    p(`${x.projecto} · ${x.hora} (${diasTexto(x.dias)}) · tecto ${x.tecto_usd} USD · ${x.max_turnos} turnos${x.ultima ? ` · última ${x.ultima}` : ""}${x.falhas ? ` · ${x.falhas} falha(s)` : ""}`),
    dets(`${al}:instr`, "Instruções", h("p", { class: "ag-desc" }, x.instrucoes)),
    x.estado === "pendente" ? p("Aprova ou recusa em Aprovações.") : null,
    x.motivo && x.estado === "aprovada" ? p(`Não lança: ${x.motivo}. Ensaia o lead outra vez para a retomar.`) : null,
    st.el,
    h("div", { class: "ag-bts" },
      x.estado === "pausada" ? botao("Retomar", () => pedir("rotina_retomar", { squad: s.id, rotina: x.id }), { disabled: dis, "data-k": `${al}:rt` }) : null,
      ["cancelada", "recusada"].includes(x.estado) ? null
        : botao("Cancelar rotina", () => confirm(`Cancelar a rotina ${x.id}? Para voltar a tê-la é preciso pedir e aprovar de novo.`)
          && pedir("rotina_cancelar", { squad: s.id, rotina: x.id }), { class: "ag-btn ag-perigo", disabled: dis, "data-k": `${al}:cx` })));
}

function novaSquadEl() {
  return h("section", { class: "ag-sq", "aria-labelledby": "ag-h-nova" }, h("h3", { id: "ag-h-nova" }, "+ Squad"),
    ...(D.conversas || []).filter((c) => c.estado !== "a_perguntar").map(conversaFimEl),
    conversaEl(),
    dets("nova", "Escolher o modelo à mão", manualEl()));
}

// uma volta: o que a pessoa escreveu e o que a IA respondeu (só texto)
function voltaEl(v) {
  const ia = v?.ia || {};
  return h("li", { class: "ag-ag" }, h("p", { class: "ag-desc" }, v.utilizador),
    ia.tipo === "perguntas" ? h("ol", {}, (ia.perguntas || []).map((q) => h("li", {}, q))) : null,
    ia.tipo === "falhada" ? p("A IA não respondeu bem nesta volta.", "ag-erro") : null);
}

function conversaEl() {
  const c = conversaActiva(D), fk = "conv", id = c ? c.squad : String(val(fk, "squad")).trim();
  const al = alvo("squad_conversa", { squad: id }), st = estadoAlvo(al), dis = !escrita() || st.ocupado;
  const enviar = () => enviarSe(fk, validarConversa({ squad: id, texto: val(fk, "texto") }, D),
    (args) => pedir("squad_conversa", args, () => { F[fk] = {}; }));
  if (!c) return h("div", { class: "ag-f" },
    p("Descreve por palavras o que a squad deve fazer. A IA faz até 3 rondas de perguntas e propõe um modelo e a missão (sem ferramentas, até 0,15 USD por volta e 1,00 USD por conversa)."),
    campo("Id da squad", fk, "squad", { autocomplete: "off", autocapitalize: "none", spellcheck: "false", maxlength: 20, disabled: dis }),
    campo("O que queres que a squad faça?", fk, "texto", { rows: 4, maxlength: CONVERSA_MAX, disabled: dis }, "textarea"),
    erroDe(fk), id ? st.el : null,
    botao("Começar a conversa", enviar, { disabled: dis, "data-k": `${fk}:b` }));
  const dal = alvo("squad_descartar", { squad: c.squad });
  return h("div", { class: "ag-bloco" }, h("h4", {}, `Conversa da squad ${c.squad} · ronda ${c.ronda} de ${c.rondas_max}`),
    p(custoTexto(c)),
    h("ol", { class: "ag-l" }, (c.voltas || []).map(voltaEl)),
    c.aviso ? h("p", { class: "ag-aviso" }, c.aviso) : null,
    campo("Resposta", fk, "texto", { rows: 4, maxlength: CONVERSA_MAX, disabled: dis }, "textarea"),
    erroDe(fk), st.el,
    h("div", { class: "ag-bts" }, botao("Responder", enviar, { disabled: dis, "data-k": `${fk}:b` }),
      botao("Descartar conversa", () => confirm("Descartar esta conversa?") && pedir("squad_descartar", { squad: c.squad }),
        { class: "ag-btn ag-perigo", disabled: dis || estadoAlvo(dal).ocupado, "data-k": `${dal}:dc` })));
}

// conversa terminada: proposta (o rascunho aparece na squad, para criar ou descartar), recusa ou falha
function conversaFimEl(c) {
  const s = (D.squads || []).find((x) => x.id === c.squad), al = alvo("squad_descartar", { squad: c.squad }), st = estadoAlvo(al);
  const m = (D.modelos || []).find((x) => x.id === c.modelo_squad), manual = manualDaProposta(c, s);
  return h("div", { class: "ag-bloco" }, h("h4", {}, `Conversa da squad ${c.squad}: ${CONVERSA_ROT[c.estado] || c.estado}`),
    p(custoTexto(c)),
    c.estado === "proposta" ? p(`Modelo proposto: ${m?.nome || c.modelo_squad}. O rascunho está na squad ${c.squad}, acima: revê a missão e os agentes e cria-a ou descarta-a.`, "") : null,
    c.estado === "proposta" && avisoNegocio(D, c.modelo_squad) ? h("p", { class: "ag-aviso" }, avisoNegocio(D, c.modelo_squad)) : null,
    c.estado === "nao_da" ? h("p", { class: "ag-desc" }, `Porquê: ${c.motivo || "?"}`) : null,
    c.estado === "nao_da" && c.alternativa ? h("p", { class: "ag-desc" }, `Alternativa: ${c.alternativa}`) : null,
    c.estado === "falhou" ? h("p", { class: "ag-erro" }, c.motivo || "A conversa terminou sem squad.") : null,
    c.aviso ? h("p", { class: "ag-aviso" }, c.aviso) : null,
    st.el,
    h("div", { class: "ag-bts" },
      manual ? botao("Mudar à mão", () => { F.nova = { ...manual }; abertos.add("nova"); render(); }, { "data-k": `${al}:mm` }) : null,
      botao(c.estado === "proposta" ? "Descartar conversa e rascunho" : "Descartar conversa",
        () => confirm("Descartar esta conversa?") && pedir("squad_descartar", { squad: c.squad }),
        { class: "ag-btn ag-perigo", disabled: st.ocupado || !escrita(), "data-k": `${al}:dc` })));
}

function manualEl() {
  const fk = "nova", ms = D.modelos || [], v = { modelo: val(fk, "modelo"), squad: String(val(fk, "squad")).trim(), missao: val(fk, "missao") };
  const al = alvo("squad_rascunho", { squad: v.squad }), st = estadoAlvo(al), dis = !escrita() || st.ocupado;
  return !ms.length ? p("Não há modelos de squad neste servidor.") : h("div", { class: "ag-f" },
      h("fieldset", { disabled: dis }, h("legend", {}, "Modelo"),
        ms.map((m) => h("label", { class: "ag-c ag-mod" }, h("input", { type: "radio", name: "ag-modelo", value: m.id, checked: v.modelo === m.id,
          "data-f": `${fk}:m:${m.id}`, onchange: () => { F[fk] = { ...F[fk], modelo: m.id }; render(); } }),
        h("span", {}, h("strong", {}, m.nome), m.resumo ? ` — ${m.resumo}` : "", h("span", { class: "sub" }, ` (lead ${m.lead}; ${(m.agentes || []).join(", ")})`))))),
      avisoNegocio(D, v.modelo) ? h("p", { class: "ag-aviso" }, avisoNegocio(D, v.modelo)) : null,
      p("Id: o de uma squad do office.yaml ainda sem agentes, ou um novo (depois colas o bloco no office.yaml)."),
      campo("Id da squad", fk, "squad", { autocomplete: "off", autocapitalize: "none", spellcheck: "false", maxlength: 20, disabled: dis }),
      campo("Missão", fk, "missao", { rows: 4, maxlength: TEXTO_MAX, disabled: dis }, "textarea"),
      erroDe(fk), v.squad ? st.el : null,
      p("O runner pede ao claude uma adaptação do modelo à missão (sem ferramentas, até 0,50 USD); o rascunho aparece na squad para criares ou descartares."),
      botao("Pedir rascunho", () => enviarSe(fk, validarSquadNova({ modelo: val(fk, "modelo"), squad: val(fk, "squad"), missao: val(fk, "missao") }, D),
        (args) => pedir("squad_rascunho", args, () => { F[fk] = {}; })), { disabled: dis, "data-k": `${fk}:b` }));
}

function pedidosEl() {
  const ps = [...(D.pedidos || [])].sort((a, b) => String(b.id).localeCompare(String(a.id))), n = ps.filter((x) => x.estado === "pendente").length;
  return h("section", { class: "ag-sq", "aria-labelledby": "ag-h-ped" }, h("h3", { id: "ag-h-ped" }, `Pedidos${n ? ` (${n} pendente${n > 1 ? "s" : ""})` : ""}`),
    ps.length ? h("ul", { class: "ag-l ag-ped" }, ps.map((x) => h("li", { class: `ag-e-${x.estado}` },
      h("span", { class: "mono" }, x.alvo), " — ", rotuloPedido(x), x.pedido_em ? h("span", { class: "sub" }, ` · ${String(x.pedido_em).slice(0, 16).replace("T", " ")}`) : null)))
      : p("Sem pedidos recentes."),
    n ? p("Os pedidos pendentes são tratados na próxima passagem do runner; esta vista actualiza-se sozinha.") : null);
}

// api = { cfg (GET api/config), getState() (token) }
export async function abrir(api) {
  A = api;
  if (!dlg) {
    await css();
    dlg = h("dialog", { id: "ag-dlg", "aria-labelledby": "ag-h" },
      h("div", { class: "ag-top" }, h("h2", { id: "ag-h" }, "Squads e agentes"),
        botao("Actualizar", () => carregar(), { "data-k": "act" }), botao("Fechar", () => dlg.close(), { "data-k": "fechar" })),
      corpo = h("div", { class: "ag-b" }));
    dlg.addEventListener("close", () => { clearTimeout(poll); document.getElementById("def-b")?.focus(); });
    document.body.append(dlg);
  }
  render();
  if (!dlg.open) dlg.showModal();
  await carregar();
}
