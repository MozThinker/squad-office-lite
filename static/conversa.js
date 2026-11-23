// Conversa (ADR-023; revisão do guardião): o Claude Code do dono no telemóvel, numa origem própria servida pelo guardião
// (conversa.html → conversa_inicio.js → iniciar()). O painel ocupa a página; «#conversa/<id>» abre essa conversa. O Office só
// tem o botão do cabeçalho, que abre esta origem por navegação de topo. As funções exportadas no topo são puras (sem
// window/document) e testadas em tests/conversa_test.mjs.
// Regra de HTML: tudo o que vem do servidor entra por textContent/createElement, nunca por innerHTML. O texto escrito na
// caixa só gera «enviar»; responder a uma permissão, pergunta ou plano é sempre um clique no cartão (acções diferentes).
// Lite: só com HTTPS. O código, de uso único, regista este browser (só existe na memória enquanto se deriva o verificador); a
// chave K nasce aqui, aleatória (conversa_chave.js, no IndexedDB desta origem, não extraível), e a fábrica não a tem. Cada
// sessão abre-se assinando o desafio do guardião e cada pedido vai assinado com K (revisão G2).
import { md, materializar } from "./md.js";
import { pintar } from "./estado.js";
import * as K from "./conversa_chave.js";

export const ID = /^\d{8}-\d{6}-[0-9a-f]{6}$/;
export const NONCE = /^[0-9a-f]{32}$/;
export const MODELOS = ["omissao", "fable", "opus", "sonnet", "haiku"];
export const ROT_MODELO = { omissao: "o do costume", fable: "Fable", opus: "Opus", sonnet: "Sonnet", haiku: "Haiku" };
export const TEXTO_MAX = 16000, OUTRA_MAX = 1000, SONDA_MS = 1500, ESPERA_MS = 20000, ENVIO_MS = 120000, CRIAR_MS = 10000;
export const NOME_OMISSAO = "Assistente", NOME_MAX = 40;
// o mesmo texto que o instalar-conversa.sh imprime (CA-21)
export const AVISO_TOKENS = "A Conversa consome tokens da tua conta Claude (a do dono, no servidor). Cada mensagem custa conforme o modelo e o tamanho da conversa; o gasto de hoje aparece no cabeçalho do painel. Nada te bloqueia: és tu que geres o consumo.";
export const SEM_TLS = "A Conversa só liga com HTTPS: põe um domínio no install.sh (--dominio).";
export const NOTA_DITADO = "O teclado do telemóvel também dita: toca no microfone do teclado.";
export const avisoVoz = (f) => `O áudio é enviado para ${f || "o fornecedor configurado"}; a privacidade e o custo passam a ser contigo.`;
// gasto do dia da pulsação: «hoje: 1,23 USD»; sem número, nada
export const gastoTxt = (usd) => (typeof usd === "number" && Number.isFinite(usd) ? `hoje: ${usd.toFixed(2).replace(".", ",")} USD` : "");
// nome do assistente (Definições): 1–40 caracteres, sem controlo nem formato (Cc/Cf) nem surrogates, não só espaços
export const nomeOk = (s) => typeof s === "string" && !!s.trim() && [...s].length <= NOME_MAX && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(s);
export const nomeDe = (est) => (nomeOk(est?.nome) ? est.nome : nomeOk(est?.nome_omissao) ? est.nome_omissao : NOME_OMISSAO);
export const ESTADO = { a_trabalhar: "a trabalhar", a_espera: "à tua espera", parado: "parado", erro: "erro", pronto: "pronto" };
export const RESOLVIDO = { aprovada_uma_vez: "✓ Aprovada uma vez", aprovada_conversa: "✓ Aprovada nesta conversa", recusada: "✗ Recusada",
  expirada: "Expirou sem resposta (10 min)", respondida: "✓ Respondida", aprovado: "✓ Plano aprovado", continuar: "↻ Continuar a planear",
  editado: "✎ Plano editado" };
export const FIM = { pronto: "Pronto.", parado: "Parado.", erro: "Terminou com erro.", tecto: "Parou no tecto (tempo ou turnos)." };

// ---- puras

export const idValido = (s) => typeof s === "string" && ID.test(s);

// hash cru (sem decodificar: «%2f» e «..» ficam inválidos) → null (não é da Conversa) | {lista} | {id} | {erro}
export function rota(hash) {
  const h = String(hash ?? "");
  if (h === "#conversa" || h === "#conversa/") return { lista: true };
  if (!h.startsWith("#conversa/")) return null;
  const id = h.slice("#conversa/".length);
  return idValido(id) ? { id } : { erro: "Conversa não encontrada." };
}

// eventos acumulados → itens a mostrar: as resoluções juntam-se ao cartão pelo nonce e os resultados à ferramenta pelo ref
export function fundir(eventos) {
  const out = [], porNonce = new Map(), porN = new Map();
  for (const e of Array.isArray(eventos) ? eventos : []) {
    if (!e || typeof e !== "object") continue;
    if (e.tipo === "ferramenta" && e.ref != null && porN.has(e.ref)) { Object.assign(porN.get(e.ref), { resultado: e.resultado, tr: e.t }); continue; }
    const alvo = e.nonce != null && porNonce.get(e.nonce);
    if (e.resolvido && alvo && alvo.tipo === e.tipo) { alvo.resolvido = e.resolvido; continue; }
    const it = { ...e };
    out.push(it);
    if (it.tipo === "ferramenta" && it.ref == null) porN.set(it.n, it);
    if (["permissao", "pergunta", "plano"].includes(it.tipo) && it.nonce != null) porNonce.set(it.nonce, it);
  }
  return out;
}

// o último evento «estado»; sem nenhum, o estado da meta
export function ultimoEstado(eventos, meta) {
  for (let i = (eventos?.length || 0) - 1; i >= 0; i--) if (eventos[i]?.tipo === "estado" && Object.hasOwn(ESTADO, eventos[i].estado)) return eventos[i].estado;
  return Object.hasOwn(ESTADO, meta ?? "") ? meta : null;
}
export const trabalha = (estado) => estado === "a_trabalhar" || estado === "a_espera";
// CA7: à espera, «Enviar» desactivado (409)
export const mostraParar = (e) => e === "a_trabalhar";
// U8: a_trabalhar → pronto/à espera com > 10 s vistos a trabalhar (30 s sem ver recomeça); m: id → {t, v}
export function respondeu(m, id, e, n) {
  const x = m.get(id), ok = x && n - x.v < 3e4;
  if (e == "a_trabalhar") return !m.set(id, { t: ok ? x.t : n, v: n });
  m.delete(id);
  return !!ok && x.v - x.t > 1e4 && (e == "pronto" || e == "a_espera");
}
// polling de 1,5 s só a trabalhar/à espera, ou até «ate» (logo a seguir a um pedido, antes de a ponte o apanhar)
export const deveSondar = (estado, ate = 0, agora = Date.now()) => trabalha(estado) || agora < ate;
// depois de um «enviar», o «a enviar» só acaba com um estado/aviso/fim novo (a própria mensagem do dono chega antes do estado)
export const envioVisto = (eventos, desde) => (eventos || []).some((e) => e?.n > desde && ["estado", "aviso", "fim"].includes(e.tipo));

export const segundosAte = (expira, agora = Date.now()) => {
  const t = Date.parse(expira ?? "");
  return Number.isFinite(t) ? Math.max(0, Math.floor((t - agora) / 1000)) : 0;
};
export const fmtSeg = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
export const pendente = (it, agora = Date.now(), arquivada = false) => !arquivada && !it?.resolvido && segundosAte(it?.expira, agora) > 0;
// numa delegação não há «Aprovar nesta conversa» (C19)
export const podeConversa = (it) => it?.delegacao !== true;

const nCar = (s) => [...s].length;
// CR → LF; fora os C0 além de \n\t e os surrogates soltos (o servidor recusa-os)
export const limpa = (s) => String(s ?? "").replace(/\r\n?/g, "\n").replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, "")
  .replace(/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g, "");
const textoOk = (s, max = TEXTO_MAX) => typeof s === "string" && s.trim() !== "" && nCar(s) <= max && limpa(s) === s;

const CHAVES = { enviar: ["conversa", "texto", "modo", "modelo", "permissoes"], parar: ["conversa"], arquivar: ["conversa"], nova: ["conversa"],
  responder_permissao: ["conversa", "nonce", "escolha"], responder_pergunta: ["conversa", "nonce", "respostas"], aprovar_plano: ["conversa", "nonce", "escolha", "texto"] };
const ESCOLHAS = { responder_permissao: ["uma_vez", "conversa", "recusar"], aprovar_plano: ["aprovar", "continuar", "editar"] };
export const PERMISSOES = ["cartao", "espelho"];

// os campos do pedido (contrato §2.4) com as chaves exactas e todas presentes (o Office não preenche omissões: quebraria a
// assinatura); null se algum for inválido. O pid, o t e a assinatura junta-os o assinado() do conversa_chave.js
export function camposPedido(acao, campos) {
  if (!Object.hasOwn(CHAVES, acao)) return null;
  const c = { ...campos };
  if (acao === "aprovar_plano" && c.escolha !== "editar") c.texto = "";
  const corpo = { acao };
  for (const k of CHAVES[acao]) { if (c[k] === undefined) return null; corpo[k] = c[k]; }
  if (!idValido(corpo.conversa)) return null;
  if (CHAVES[acao].includes("nonce") && !NONCE.test(String(corpo.nonce ?? ""))) return null;
  if (ESCOLHAS[acao] && !ESCOLHAS[acao].includes(corpo.escolha)) return null;
  if (acao === "enviar" && (!textoOk(corpo.texto) || !["normal", "plano"].includes(corpo.modo) || !MODELOS.includes(corpo.modelo)
    || !PERMISSOES.includes(corpo.permissoes))) return null;
  if (acao === "aprovar_plano" && corpo.escolha === "editar" && !textoOk(corpo.texto)) return null;
  if (acao === "responder_pergunta" && !respostasOk(corpo.respostas)) return null;
  return corpo;
}
const ind = (x) => Number.isInteger(x) && x >= 0 && x <= 3;
function respostasOk(r) {
  if (!Array.isArray(r) || r.length < 1 || r.length > 4 || new Set(r.map((x) => x?.pergunta)).size !== r.length) return false;
  return r.every((x) => x && ind(x.pergunta) && (Object.keys(x).length === 2) && (("outra" in x) ? textoOk(x.outra, OUTRA_MAX)
    : Array.isArray(x.opcao) && x.opcao.length >= 1 && x.opcao.length <= 4 && x.opcao.every(ind) && new Set(x.opcao).size === x.opcao.length));
}

// form = [{opcoes: [índices escolhidos], outra: texto}] por pergunta → respostas do contrato; null se falta alguma ou é inválida.
// «Outra resposta» preenchida vale por cima das opções
export function respostasDe(perguntas, form) {
  const n = Math.min(Array.isArray(perguntas) ? perguntas.length : 0, 4), out = [];
  for (let i = 0; i < n; i++) {
    const f = form?.[i] || {}, outra = limpa(f.outra).trim();
    if (outra) { if (nCar(outra) > OUTRA_MAX) return null; out.push({ pergunta: i, outra }); continue; }
    const max = Math.min(perguntas[i]?.opcoes?.length || 0, 4);
    const op = [...new Set(f.opcoes || [])].filter((j) => Number.isInteger(j) && j >= 0 && j < max).sort((a, b) => a - b);
    if (!op.length || (perguntas[i]?.multipla !== true && op.length > 1)) return null;
    out.push({ pergunta: i, opcao: op });
  }
  return out.length ? out : null;
}

// pesquisa: "" (sem pesquisa) | texto de 2–100 caracteres | null (curta ou longa demais: não se pede)
export function pesquisa(q) {
  const s = String(q ?? "").trim();
  return !s ? "" : nCar(s) >= 2 && nCar(s) <= 100 ? s : null;
}
// não arquivadas primeiro, mais recente em cima (o servidor já ordena; isto garante-o)
export const ordenar = (lista) => (Array.isArray(lista) ? lista : []).filter((c) => idValido(c?.id))
  .sort((a, b) => (!!a.arquivada - !!b.arquivada) || String(b.actualizada ?? "").localeCompare(String(a.actualizada ?? "")));

export const erroTxt = (r) => (r?.j?.error ? String(r.j.error).slice(0, 200) : r?.status ? `erro ${r.status}` : "sem ligação, tenta de novo");

// ---- puras do painel (v2)

// última conversa aberta neste browser (localStorage «conversa.ultima»): tudo em try/catch; o que vier torto fica null
export function lerUltima(st) {
  try { const v = st.getItem("conversa.ultima"); return idValido(v) ? v : null; } catch { return null; }
}
export function gravarUltima(st, id) {
  try { if (idValido(id)) st.setItem("conversa.ultima", id); else st.removeItem("conversa.ultima"); } catch { /* modo privado */ }
}
const PEDIDOS = ["permissao", "pergunta", "plano"];
// o pedido activo é o pendente mais antigo (menor n); «mais» = os outros à espera (CA-13)
export function pedidoActivo(itens, agora = Date.now()) {
  const ps = (Array.isArray(itens) ? itens : []).filter((it) => PEDIDOS.includes(it?.tipo) && pendente(it, agora)).sort((a, b) => a.n - b.n);
  return { activo: ps[0] || null, mais: Math.max(0, ps.length - 1) };
}
// ferramentas seguidas → um grupo; «estado» e «custo» não quebram (ficam a seguir ao grupo); o resto quebra (UX §4)
export function agrupar(itens) {
  const out = [], adiados = [];
  let g = null;
  const fecha = () => { if (g) { out.push(g, ...adiados); adiados.length = 0; g = null; } };
  for (const it of Array.isArray(itens) ? itens : []) {
    if (it?.tipo === "ferramenta") (g ||= { tipo: "grupo", n: it.n, passos: [] }).passos.push(it);
    else if (g && (it?.tipo === "estado" || it?.tipo === "custo")) adiados.push(it);
    else { fecha(); out.push(it); }
  }
  fecha();
  return out;
}
const CATEGORIAS = [[["Read"], "leu", "ficheiro", "ficheiros"], [["Grep", "Glob"], "fez", "pesquisa", "pesquisas"],
  [["Edit", "Write", "NotebookEdit"], "editou", "ficheiro", "ficheiros"], [["Bash"], "correu", "comando", "comandos"],
  [["WebSearch", "WebFetch"], "consultou a web", "vez", "vezes"], [["Task", "Agent"], "lançou", "subagente", "subagentes"]];
export function fmtDuracao(s) {
  s = Math.max(0, Math.floor(s));
  const p2 = (x) => String(x).padStart(2, "0");
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${p2(s % 60)} s` : `${Math.floor(s / 3600)} h ${p2(Math.floor(s / 60) % 60)} min`;
}
// «Leu 3 ficheiros, correu 2 comandos · 12 s»; em curso «…» e sem duração (CA-23)
export function resumoGrupo(passos) {
  const ps = Array.isArray(passos) ? passos : [], n = CATEGORIAS.map(() => 0);
  let outras = 0;
  for (const p of ps) { const k = CATEGORIAS.findIndex(([ns]) => ns.includes(p?.nome)); if (k < 0) outras++; else n[k]++; }
  const partes = CATEGORIAS.map(([, v, um, varios], k) => n[k] && `${v} ${n[k]} ${n[k] === 1 ? um : varios}`).filter(Boolean);
  if (outras) partes.push(`usou ${outras} ${outras === 1 ? "outra ferramenta" : "outras ferramentas"}`);
  const txt = partes.join(", ").replace(/^./, (c) => c.toUpperCase());
  if (ps.some((p) => p?.resultado == null)) return txt + "…";
  const fim = Math.max(...ps.map((p) => Date.parse(p.tr ?? ""))), s = (fim - Date.parse(ps[0]?.t ?? "")) / 1000;
  return Number.isFinite(s) && s >= 1 ? `${txt} · ${fmtDuracao(s)}` : txt;
}
// Enter envia; Shift+Enter, composição (IME) e ecrã de toque (D2) mudam de linha (CA-26)
export const teclaEnvia = (e, grosso) => e?.key === "Enter" && !e.shiftKey && !e.altKey && !e.isComposing && e.keyCode !== 229 && !grosso;
const mesmoDia = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
// «hoje 10:15», «ontem», «18/11» (CA-25); outro ano leva o ano
export function dataRelativa(iso, agora = Date.now()) {
  const d = new Date(iso ?? ""), h = new Date(agora);
  if (!Number.isFinite(+d)) return "";
  const p2 = (x) => String(x).padStart(2, "0");
  if (mesmoDia(d, h)) return `hoje ${p2(d.getHours())}:${p2(d.getMinutes())}`;
  if (mesmoDia(d, new Date(h.getFullYear(), h.getMonth(), h.getDate() - 1))) return "ontem";
  return `${p2(d.getDate())}/${p2(d.getMonth() + 1)}` + (d.getFullYear() !== h.getFullYear() ? "/" + d.getFullYear() : "");
}
// cabeçalho → [peça, texto, t0] (SPEC §5); act = pedido activo
const PECA = { a_trabalhar: "a-trabalhar", a_espera: "a-espera", pronto: "concluido", erro: "erro", parado: "desligado" };
export function pecaEstado(ev, estado, arq, act) {
  const u = [...(ev || [])].reverse(), a = (f, x) => u.find((e) => e?.tipo === f && (!x || e.autor === x)), est = a("estado"), fim = a("fim"),
    t = (e) => Date.parse(e?.t);
  if (arq) return [null, "· arquivada"];
  if (act || estado === "a_espera") return ["a-espera", ESTADO.a_espera, t(act || est)];
  if (estado === "a_trabalhar") return ["a-trabalhar", est?.passo ? String(est.passo) : ESTADO.a_trabalhar, t(a("mensagem", "dono"))];
  if (fim?.motivo === "tecto") return ["erro", "parou no tecto (tempo ou turnos)", t(fim)];
  return [PECA[estado], ESTADO[estado] || "", t(fim)];
}
// modelo em uso no cabeçalho (CA-19): o último escolhido neste browser para a conversa, ou «o do costume»
export function modeloEmUso(st, id) {
  try { const m = JSON.parse(st.getItem("conversa.modelos"))?.[id]; if (MODELOS.includes(m)) return ROT_MODELO[m]; } catch { /* sem localStorage */ }
  return ROT_MODELO.omissao;
}
// Definições da Conversa (vieram das Definições do Office): o que está em vigor segundo o api/conversa/estado
const PERM = { cartao: "cartão (cada acção com efeito pede um clique)", espelho: "espelho (como no terminal; os lançadores pedem um clique)" };
export function conversaLinhas(e) {
  if (!e || typeof e !== "object") return ["Sem ligação à Conversa."];
  const v = e.voz === "externa" ? `externa (${e.voz_fornecedor || "fornecedor configurado"})` : e.voz === "local" ? "local (no teu servidor)" : "desligada (o teclado do telemóvel também dita)";
  return [`Permissões: ${PERM[e.permissoes] || "?"}` + (e.permissoes_config && e.permissoes_config !== e.permissoes ? ` (o instalador pede ${e.permissoes_config}; a ponte só permite ${e.permissoes})` : ""),
    `Voz: ${v}` + (e.voz !== "desligada" && e.voz_ponte !== e.voz ? " · não instalada no servidor" : ""),
    `Aviso de consumo de tokens: ${e.aviso_tokens === true ? "aceite" : "por aceitar"}`,
    ...(e.voz === "externa" ? [`Aviso da voz externa: ${e.aviso_voz === true ? "aceite" : "por aceitar"}`] : [])];
}
// QA B3: logo a seguir à «Nova conversa» a ponte ainda não a criou e um «enviar» dava 404. O «Enviar» sonda os eventos até
// 200 (true) ou até «ate» (false); outro código que não 404 também dá false (a mensagem de erro é a do envio)
export async function esperarCriada(sonda, ate, agora = Date.now, dormir = (ms) => new Promise((ok) => setTimeout(ok, ms)), passo = 500) {
  for (;;) {
    const st = await sonda();
    if (st === 200) return true;
    if (st !== 404 || agora() >= ate) return false;
    await dormir(passo);
  }
}

// ---- puras da voz (ADR-020): o texto ditado só entra na caixa; nada aqui envia
export const VOZ_MAX_S = 120, VOZ_MAX_BYTES = 5 * 1024 * 1024, VOZ_ESPERA_MS = 90000, VOZ_ENVIO_MS = 30000;
export const VOZ_MSG = {
  explicar: "O browser vai pedir o microfone para passar a tua voz a texto. O áudio é apagado logo a seguir à transcrição.",
  sem_suporte: "Este browser não grava áudio. Usa o Safari actualizado ou escreve a mensagem.",
  sem_permissao: "Sem acesso ao microfone. Permite-o nas definições do site ou da app e toca outra vez no microfone.",
  pedir: "A pedir acesso ao microfone…",
  sem_conversa: "Abre ou cria uma conversa primeiro.",
  ponte: "A ponte não está a correr: a voz volta quando ela voltar. Podes escrever a mensagem.",
  sem_microfone: "Não encontrei microfone neste aparelho.",
  indisponivel: "A voz não está instalada no servidor. Escreve a mensagem.",
  curto: "Gravação curta demais. Fala um pouco mais.",
  falhou: "Não consegui gravar. Tenta outra vez ou escreve.",
  tempo: "A transcrição demorou demais. Tenta outra vez ou escreve.",
  vazio: "Não ouvi nada. Tenta outra vez.",
  limite: "Parou aos 2 min",
  cortado: "O texto passou dos 16 000 caracteres e foi cortado.", // TEXTO_MAX, escrito à mão (o toLocaleString não separa em todos os browsers)
};
// estados do microfone (CA-8); uma transição inválida devolve o mesmo estado. sem_permissao volta a tentar ao toque (a pessoa pode ter
// mudado a permissão nas definições); sem_microfone, sem_suporte e indisponivel ficam até recarregar
// a_pedir: o toque já foi registado e o browser ainda não respondeu ao pedido do microfone (feedback imediato, nunca um toque calado)
const MIC = {
  pronto: { pedir: "a_pedir", gravar: "a_gravar", negado: "sem_permissao", sem_mic: "sem_microfone", falha: "erro" },
  a_pedir: { gravar: "a_gravar", negado: "sem_permissao", sem_mic: "sem_microfone", falha: "erro", cancelar: "pronto" },
  a_gravar: { parar: "a_transcrever", cancelar: "pronto", falha: "erro" },
  a_transcrever: { texto: "pronto", falha: "erro", cancelar: "pronto", indisponivel: "indisponivel" },
};
MIC.erro = MIC.sem_permissao = MIC.pronto; // depois de um erro (ou de negar o microfone) tenta-se de novo como em «pronto»
export const micEstado = (estado, evento) => (Object.hasOwn(MIC, estado ?? "") && Object.hasOwn(MIC[estado], evento ?? "") ? MIC[estado][evento] : estado);
// sem permissão: no iPhone/iPad diz onde se liga o microfone (o Chrome e o Firefox do iOS têm a sua entrada nas Definições)
export function msgSemPermissao(ua, toque = 0) {
  const u = String(ua ?? ""), ios = /iPhone|iPad|iPod/.test(u) || (/Macintosh/.test(u) && toque > 1);
  if (!ios) return VOZ_MSG.sem_permissao;
  const app = /CriOS/.test(u) ? "Chrome" : /FxiOS/.test(u) ? "Firefox" : /EdgiOS/.test(u) ? "Edge" : "Safari", ap = /iPhone|iPod/.test(u) ? "iPhone" : "iPad";
  return `Sem acesso ao microfone. Nas Definições do ${ap}, abre ${app} e liga o Microfone. Depois toca outra vez no microfone.`;
}
// webm/opus (Chrome, Firefox) → ogg/opus → mp4 (Safari); null = sem_suporte
export function escolherMime(isTypeSupported) {
  if (typeof isTypeSupported !== "function") return null;
  return ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((m) => { try { return isTypeSupported(m) === true; } catch { return false; } }) || null;
}
export const FORMATOS = { "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3" }, VOZ_MIN_BYTES = 1024;
export const formatoDe = (mime) => FORMATOS[String(mime ?? "").split(";")[0].trim()] || null;
// o texto ditado junta-se ao que já está na caixa (CA-16): vazia → texto; senão com um espaço (nada se já acaba em espaço/linha)
export function juntarTranscricao(actual, texto) {
  const a = String(actual ?? ""), t = limpa(texto).trim();
  const todo = [...(!t ? a : !a ? t : /\s$/.test(a) ? a + t : a + " " + t)];
  return { texto: todo.slice(0, TEXTO_MAX).join(""), cortado: todo.length > TEXTO_MAX };
}
// só a resposta à gravação desta vez (o «audio» do 202) e só nos eventos novos: {texto} | {aviso: {codigo, texto}} | null
export function respostaVoz(novos, audio) {
  if (!NONCE.test(String(audio ?? ""))) return null;
  for (const e of Array.isArray(novos) ? novos : []) {
    if (e?.audio !== audio) continue;
    if (e.tipo === "transcricao") return { texto: str(e.texto) };
    if (e.tipo === "aviso" && /^voz_/.test(str(e.codigo))) return { aviso: { codigo: str(e.codigo), texto: str(e.texto) } };
  }
  return null;
}
// a transcrição e os avisos de voz nunca entram no fluxo (nem partem grupos, nem acabam o «a enviar») (CA-18)
export const eventoVisivel = (e) => !(e?.tipo === "transcricao" || (e?.tipo === "aviso" && /^voz_/.test(str(e.codigo))));
// erros da rota de áudio → texto por baixo da caixa (CA-19)
export function erroVoz(status, j) {
  if (status === 413) return "Áudio grande demais (máx. 5 MB).";
  if (status === 415) return "Formato de áudio não suportado.";
  if (status === 429) return "Já há um áudio a transcrever. Espera que termine.";
  return erroTxt({ status, j });
}
// gravador com tudo injectável (testes): pára sozinho aos 120 s e segue; cancelar descarta e larga o microfone (CA-12 a CA-14)
export function criarGravador(d) {
  let stream = null, rec = null, ctx = null, partes = [], fim = null, cancelado = false, iv = 0, nv = 0, tm = 0;
  const largar = () => {
    d.clearTimeout(tm); d.clearInterval(iv); d.clearInterval(nv);
    for (const t of stream?.getTracks?.() || []) t.stop();
    try { ctx?.close?.()?.catch?.(() => {}); } catch { /* já fechado */ }
    ctx = null;
  };
  const parar = (porque) => {
    if (!rec || fim) return;
    fim = porque;
    if (rec.state === "inactive") rec.onstop(); else rec.stop();
  };
  return {
    async iniciar() {
      // tudo o que precisa do gesto (iOS/WebKit) acontece antes do 1.º await: o pedido do microfone e o AudioContext retomado
      const pedido = d.getUserMedia({ audio: true });
      try { if (d.AudioContext) { ctx = new d.AudioContext(); ctx.resume?.()?.catch?.(() => {}); } } catch { ctx = null; }
      try { stream = await pedido; } catch (e) { largar(); throw e; }
      if (cancelado) { largar(); return false; }
      try {
        rec = new d.MediaRecorder(stream, { mimeType: d.mime, audioBitsPerSecond: 32000 });
        rec.ondataavailable = (e) => { if (e.data?.size) partes.push(e.data); };
        rec.onstop = () => { largar(); if (fim && !cancelado) d.aoFim({ blob: new Blob(partes, { type: d.mime }), limite: fim === "limite" }); };
        rec.start();
      } catch (e) { rec = null; largar(); throw e; }
      const t0 = d.agora();
      iv = d.setInterval(() => d.aoTempo(Math.min(VOZ_MAX_S, Math.floor((d.agora() - t0) / 1000))), 1000);
      tm = d.setTimeout(() => parar("limite"), VOZ_MAX_S * 1000);
      try { // nível de som (opcional): ≤ 10 actualizações/s
        if (ctx) {
          if (ctx.state === "suspended") ctx.resume?.()?.catch?.(() => {});
          const an = ctx.createAnalyser(), buf = new Uint8Array(256);
          an.fftSize = 256;
          ctx.createMediaStreamSource(stream).connect(an);
          nv = d.setInterval(() => { an.getByteTimeDomainData(buf); let m = 0; for (const v of buf) m = Math.max(m, Math.abs(v - 128)); d.aoNivel(Math.min(1, m / 64)); }, 100);
        }
      } catch { /* sem nível: grava na mesma */ }
      return true;
    },
    parar: () => parar("parar"),
    cancelar() { cancelado = true; partes = []; if (rec && rec.state !== "inactive") rec.stop(); else largar(); },
  };
}

// ---- DOM

let A = null, root = null, dlg = null;
const S = { aberto: false, montado: false, csrf: null, est: null, chave: null, kid: null, reentrou: 0, nome: NOME_OMISSAO, dest: undefined, g: 0, id: null, ev: [], desde: 0, titulo: "", meta: null,
  estado: null, arquivada: false, ponte: true, ate: 0, aguarda: 0, desdeEnvio: 0, criando: null, parando: false, t: 0, tq: 0, tick: 0,
  els: new Map(), sig: new Map(), enviados: new Set(), qseq: 0, actNonce: null, temAct: false,
  mic: "sem_suporte", mime: null, grav: null, voz: null, vseq: 0, tv: 0, vctl: null, tpedir: 0, tdit: 0, trab: new Map() };
const $ = (s) => root.querySelector(s);
// R6: S.criando = {id, ate} da «Nova conversa» acabada de pedir; só essa conversa espera (até «ate») que a ponte a crie
const criandoAte = (id) => (id && S.criando?.id === id ? S.criando.ate : 0);
const AINDA_A_CRIAR = "A conversa ainda não foi criada. Tenta outra vez daqui a pouco.";
const espera = (ms) => new Promise((ok) => setTimeout(ok, ms));
const str = (v) => (v == null ? "" : String(v));
const hora = (t) => { const d = new Date(t ?? ""); return Number.isFinite(+d) ? d.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" }) : ""; };
const LS = () => { try { return globalThis.localStorage; } catch { return null; } };
const grosso = () => !!globalThis.matchMedia?.("(pointer: coarse)").matches;
const dialogoAberto = () => !!document.querySelector("dialog[open]");

// elemento com texto/atributos; os filhos string viram nós de texto (nunca HTML)
function el(tag, props = {}, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "text") e.textContent = v;
    else if (k === "class") e.className = v;
    else if (/^(data-|aria-)|^(role|for|type|maxlength|rows|placeholder|href|hidden|name|id|tabindex)$/.test(k)) e.setAttribute(k, v === true ? "" : String(v));
    else e[k] = v;
  }
  e.append(...filhos.filter((f) => f != null && f !== false));
  return e;
}

async function pedir(url, init = {}) {
  try {
    const r = await fetch(url, { cache: "no-store", credentials: "same-origin", ...init });
    return { status: r.status, j: await r.json().catch(() => ({})) };
  } catch { return { status: 0, j: {} }; }
}
const subtle = () => globalThis.crypto?.subtle;
const H = (extra = {}) => ({ ...(S.csrf ? { "X-Csrf": S.csrf } : {}), ...extra });
const JSONH = { "Content-Type": "application/json" };
// GET estado (sem sessão): TLS, sessão (cookie), csrf, sal, desafio, nome, avisos aceites, voz, ponte e gasto do dia
async function lerEstado() {
  const r = await pedir("api/conversa/estado");
  if (r.status !== 200 || !r.j || typeof r.j !== "object") return null;
  S.est = r.j; S.ponte = r.j.ponte !== false && r.j.versoes_ok !== false;
  S.csrf = r.j.sessao === true && typeof r.j.csrf === "string" && r.j.csrf ? r.j.csrf : null;
  S.nome = nomeDe(r.j);
  pintarNome();
  return r.j;
}
// pronto = HTTPS, a chave deste browser (IndexedDB) e sessão (se faltar, assina o desafio); senão o painel pede o código
async function pronto() {
  if (!S.csrf || !S.est) await lerEstado();
  if (!S.est?.tls) return false;
  if (!S.chave) { const c = await K.lerChave(globalThis.indexedDB); if (c) ({ chave: S.chave, kid: S.kid } = c); }
  if (S.chave && !S.csrf) await entrarComChave();
  return !!(S.chave && S.csrf);
}
// entrada seguinte: o desafio do último GET estado (uso único) assinado com K. Um 403 de assinatura = este browser já não está
// registado (revogado ou chave de outro código): apaga a chave e pede o código
async function entrarComChave() {
  const d = S.est?.desafio;
  if (!d || S.est.bloqueado_ate) return;
  S.est.desafio = null;
  let r;
  try {
    r = await pedir("api/conversa/entrar", { method: "POST", headers: JSONH,
      body: JSON.stringify({ kid: S.kid, desafio: d, assinatura: await K.assinarEntrada(subtle(), S.chave, d) }) });
  } catch { return; }
  if (r.status === 200 && typeof r.j.csrf === "string") { S.csrf = r.j.csrf; await lerEstado(); }
  else if (r.status === 403 && /assinatura/.test(str(r.j.error))) esquecerChave();
  else if (r.status === 429) await lerEstado();
}
const esquecerChave = () => { S.chave = S.kid = null; K.apagarChave(globalThis.indexedDB); };
// sem sessão (expirou): com a chave, volta a entrar sozinho (no máx. de 30 em 30 s); senão pede o código
const semSessao = () => {
  S.csrf = null;
  if (S.aberto && S.chave && Date.now() - S.reentrou > 30000) { S.reentrou = Date.now(); S.aberto = false; abrir(S.dest, false); }
  else if (S.aberto) mostrarEntrada();
  return { status: 401, j: { error: "abre a Conversa com o código" } };
};
// GET com sessão; 401 → pede o código
async function obter(url) {
  if (!S.csrf) return semSessao();
  const r = await pedir(url);
  if (r.status === 401) semSessao();
  return r;
}
// a chave deste browser já não serve (o guardião deu 403 à assinatura): apaga-a e volta ao ecrã do código
const chaveRecusada = (r) => { esquecerChave(); semSessao(); return r; };
// POST assinado: X-Csrf da sessão; corpo = o pedido do contrato com pid, t e assinatura (o guardião confere-a antes de o aceitar)
async function enviarPedido(acao, campos, repetiu = false) {
  if (!S.csrf || !S.chave) return semSessao();
  const c = camposPedido(acao, campos);
  if (!c) return { status: 400, j: { error: "pedido inválido" } };
  let corpo;
  try { corpo = await K.assinado(subtle(), S.chave, c); } catch { S.chave = null; return semSessao(); }
  const r = await pedir("api/conversa/pedido", { method: "POST", headers: H(JSONH), body: JSON.stringify(corpo) });
  if (r.status === 403 && /assinatura/.test(str(r.j.error))) return chaveRecusada(r);
  if (r.status === 403 && !repetiu && (await lerEstado(), !!S.csrf)) return enviarPedido(acao, campos, true);
  if (r.status === 401) semSessao();
  if (r.status === 409 && /aviso/.test(str(r.j.error))) { if (S.est) S.est.aviso_tokens = false; avisoTokens(); }
  if (r.status === 503 && /ponte/.test(str(r.j.error))) { S.ponte = false; pintarTopo(); }
  return r;
}
// áudio cru: o «transcrever» assinado vai no X-Conversa-Pedido (sha256 e bytes do corpo, formato e idioma). Um 503 aqui não
// marca a ponte como parada (pode ser só «a voz não está instalada»)
async function enviarAudio(id, blob, signal) {
  if (blob.size > VOZ_MAX_BYTES) return { status: 413, j: {} };
  if (blob.size < VOZ_MIN_BYTES) return { status: 0, j: { error: VOZ_MSG.curto } };
  if (!S.csrf || !S.chave) return semSessao();
  const tipo = String(S.mime).split(";")[0];
  if (!idValido(id)) return { status: 400, j: { error: "pedido inválido" } };
  const buf = await blob.arrayBuffer(), sha256 = K.hex(await subtle().digest("SHA-256", buf));
  const p = await K.assinado(subtle(), S.chave, { acao: "transcrever", conversa: id, audio: K.aleatorioHex(32), formato: formatoDe(tipo),
    bytes: buf.byteLength, sha256, idioma: S.est?.voz_idioma || "pt" });
  const r = await pedir("api/conversa/audio", { method: "POST", headers: H({ "Content-Type": tipo, "X-Conversa-Pedido": JSON.stringify(p) }), body: buf, signal });
  if (r.status === 403 && /assinatura/.test(str(r.j.error))) return chaveRecusada(r);
  if (r.status === 403 && /aviso/.test(str(r.j.error)) && S.est) S.est.aviso_voz = false;
  if (r.status === 401) semSessao();
  if (r.status === 202 && !NONCE.test(str(r.j.audio))) r.j.audio = p.audio;
  return r;
}
// preferências (sem fila nem assinatura): nome, aviso de tokens e aviso da voz externa
async function gravarPrefs(corpo) {
  if (!S.csrf) return semSessao();
  const r = await pedir("api/conversa/prefs", { method: "POST", headers: H(JSONH), body: JSON.stringify(corpo) });
  if (r.status === 200 && S.est) {
    Object.assign(S.est, { nome: r.j.nome ?? null, aviso_tokens: r.j.aviso_tokens === true, aviso_voz: r.j.aviso_voz === true });
    S.nome = nomeDe(S.est); pintarNome();
  } else if (r.status === 401) semSessao();
  return r;
}

// diálogo modal (top layer): o aviso de tokens e o da voz externa; resolve true só com o clique em «sim»
function dialogo(titulo, texto, sim, nao) {
  dlg.replaceChildren(el("h2", { id: "cp-dh", text: titulo }), el("p", { text: texto }),
    el("div", { class: "btns" }, el("button", { type: "button", class: "cv-b cv-pri", "data-r": "1", text: sim }), nao ? el("button", { type: "button", class: "cv-b", text: nao }) : null));
  return new Promise((ok) => {
    let r = false;
    dlg.onclick = (e) => { const b = e.target.closest("button"); if (b) { r = b.dataset.r === "1"; dlg.close(); } };
    dlg.onclose = () => ok(r);
    dlg.showModal();
  });
}
// aviso de consumo de tokens: até ao clique a caixa não envia (o Office dá 409); guardado no servidor (CA-21)
async function avisoTokens() {
  if (dlg.open || S.est?.aviso_tokens === true) return;
  if (!(await dialogo("Antes de começar", AVISO_TOKENS, "Percebi"))) return;
  const r = await gravarPrefs({ aviso_tokens: true });
  if (r.status !== 200 && S.montado) $(".cv-fm").textContent = erroTxt(r);
}

// entrada: sem HTTPS, sem instalador, bloqueado ou sem sessão/chave → explica ou pede o código (nunca guardado)
function mostrarEntrada() {
  if (!S.montado) return;
  S.g++; clearTimeout(S.t); clearInterval(S.tick); cancelarVoz();
  const e = S.est, m = $(".cp-ent-m"), f = $(".cp-cod");
  $(".cv-lv").hidden = $(".cv-cv").hidden = true; $(".cp-ent").hidden = false;
  $("#cp-h").textContent = "Conversa";
  for (const s of ['[data-a="voltar"]', '.cp-top [data-a="nova"]', ".cp-sub"]) $(s).hidden = true;
  $(".cp-tls").hidden = !e || e.tls === true;
  f.hidden = !e?.tls || e.entrada !== true;
  const ate = Date.parse(e?.bloqueado_ate ?? ""), h = Number.isFinite(ate) ? hora(ate) : "";
  m.textContent = !e ? "Sem ligação à Conversa. Fecha e abre de novo daqui a pouco." : !e.tls ? SEM_TLS
    : h ? `Demasiadas tentativas: bloqueado até às ${h}.`
    : e.entrada !== true ? "Este browser não está registado e não há código por usar: gera um com sudo ./instalar-conversa.sh --novo-codigo (README, «Conversa»)."
    : "Escreve o código da Conversa (o instalar-conversa.sh mostrou-o).";
}
// registo deste browser: o código (uso único) dá o verificador; K nasce aqui e vai no corpo uma vez; depois só a CryptoKey fica
async function entrar(f) {
  const cod = K.normalizarCodigo(f.elements.codigo.value), m = $(".cp-ent-m"), e = S.est, b = f.querySelector("button[type=submit]");
  if (!cod) { m.textContent = "O código tem 24 caracteres (letras e algarismos de 2 a 7), em 6 grupos de 4."; return; }
  if (!e?.sal_office || !subtle()) { m.textContent = "Este browser não tem WebCrypto: usa um browser actualizado."; return; }
  b.disabled = true;
  pintar(m, "a-processar", "a verificar o código", Date.now());
  try {
    const v = await K.verificador(subtle(), cod, e.sal_office, e.iteracoes), n = await K.novaChave(subtle());
    const r = await pedir("api/conversa/registar", { method: "POST", headers: JSONH, body: JSON.stringify({ verificador: v, kid: n.kid, chave: n.hexa }) });
    if (r.status !== 200 || typeof r.j.csrf !== "string") {
      const ate = Date.parse(r.j.ate ?? "");
      return pintar(m, "erro", r.status === 429 && Number.isFinite(ate) ? `Demasiadas tentativas: bloqueado até às ${hora(ate)}.` : erroTxt(r));
    }
    S.csrf = r.j.csrf; S.chave = n.chave; S.kid = n.kid;
    await K.guardarChave(globalThis.indexedDB, S.chave, S.kid).catch(() => {}); // modo privado: a chave fica só nesta página
    f.reset(); m.textContent = "";
    await lerEstado();
    S.aberto = false;
    await abrir(S.dest);
  } catch { pintar(m, "erro", "Não consegui verificar o código. Tenta outra vez."); }
  finally { b.disabled = false; }
}
// o nome do assistente (Definições) no título, no cabeçalho, na caixa e nas mensagens, sem recarregar (CA-18)
function pintarNome() {
  if (!S.montado) return;
  document.title = `${S.nome} · Conversa`;
  $(".cp-nome").textContent = S.nome;
  $(".cp-gasto").textContent = gastoTxt(S.est?.dia_usd);
  $("#cv-txt").placeholder = `Escreve a ${S.nome}…`;
  $('label[for="cv-txt"]').textContent = `Mensagem para ${S.nome}`;
  if (S.els.size && S.nomeP !== S.nome) { S.sig.clear(); pintarEventos(); } // só quando o nome mudou
  S.nomeP = S.nome;
  pintarDef();
}
// Definições (vieram das do Office): o nome do assistente e o que está em vigor
function pintarDef() {
  $(".cp-defi").replaceChildren(...conversaLinhas(S.est).map((t) => el("li", { text: t })));
  const i = $("#cp-nome-in");
  if (S.est && document.activeElement !== i) i.value = S.nome;
}
async function mudarNome(nome) {
  const m = $(".cp-defm");
  if (nome !== null && !nomeOk(nome)) { m.textContent = "Nome inválido (1 a 40 caracteres, sem caracteres de controlo)."; return; }
  m.textContent = "A guardar…";
  const r = await gravarPrefs({ nome });
  m.textContent = r.status === 200 ? (nome === null ? "Reposto." : "Guardado.") : erroTxt(r);
}

// só texto fixo (nenhum dado do servidor); os modelos vêm da lista fechada
const SHELL = `<div class="cp-top"><button type="button" class="cv-b cp-i" data-a="voltar" aria-label="Voltar às conversas" hidden>‹</button>
<h1 id="cp-h" tabindex="-1">Conversas</h1><button type="button" class="cv-b cp-i" data-a="nova" aria-label="Nova conversa" hidden>+</button></div>
<p class="cp-quem sub"><b class="cp-nome"></b> <span class="cp-gasto"></span></p>
<p class="cp-sub sub" hidden><span class="cp-mod"></span> <span class="cp-rel"></span></p>
<div class="cp-ent" hidden><p class="cp-ent-m" role="status" aria-live="polite"></p>
<p class="cp-tls" hidden><a href="https://github.com/MozThinker/squad-office-lite#readme" target="_blank" rel="noopener noreferrer">Como pôr um domínio (README)</a></p>
<form class="cp-cod" hidden>
<label for="cp-codigo">Código da Conversa</label><input id="cp-codigo" name="codigo" type="text" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" maxlength="40" required>
<button type="submit" class="cv-b cv-pri">Registar</button><p class="sub">Código de uso único: regista este browser.</p></form></div>
<p class="cv-ponte" role="alert" hidden>A ponte não está a correr: o assistente não recebe mensagens até ela voltar.</p>
<div class="cv-lv"><button type="button" class="cv-b cv-pri cp-nova" data-a="nova">+ Nova conversa</button>
<input class="cv-q" type="search" maxlength="100" placeholder="Pesquisar conversas" aria-label="Pesquisar conversas (2 letras ou mais)" autocomplete="off">
<p class="cv-lm sub" role="status" aria-live="polite"></p><ul class="cv-l"></ul>
<details class="cp-def"><summary>Definições</summary><form class="cp-nomef"><label for="cp-nome-in">Nome do assistente</label>
<div class="cp-nomel"><input id="cp-nome-in" name="nome" maxlength="40" autocomplete="off" required><button type="submit" class="cv-b">Guardar</button>
<button type="button" class="cv-b" data-a="nome-repor">Repor</button></div></form><p class="cp-defm sub" role="status" aria-live="polite"></p><ul class="cp-defi sub"></ul></details></div>
<div class="cv-cv" hidden><div class="cp-flx"><ol class="cv-ev" aria-label="Mensagens da conversa"></ol>
<p class="cv-st sub" role="status" aria-live="polite"></p>
<p class="cp-arq" hidden><button type="button" class="cv-b" data-a="arquivar">Arquivar esta conversa</button></p></div>
<section class="cp-act" aria-label="Pedido à tua espera" hidden></section>
<form class="cv-f"><label class="cv-vh" for="cv-txt">Mensagem</label>
<div class="cv-cx"><textarea id="cv-txt" rows="1" maxlength="${TEXTO_MAX}" placeholder="Escreve…" aria-describedby="cv-dica"></textarea>
<button type="button" id="cv-mic" class="cv-b" data-a="mic" aria-pressed="false" aria-label="Ditar mensagem" title="Ditar mensagem"><svg class="ico" aria-hidden="true" focusable="false"><use href="#i-mic"/></svg><span></span></button></div>
<div class="cv-op"><label><span class="cp-lm">Modelo </span><select name="modelo">${MODELOS.map((m) => `<option value="${m}">${ROT_MODELO[m]}</option>`).join("")}</select></label>
<label class="cv-pl"><input type="checkbox" name="plano"> Planear</label>
<button type="submit" class="cv-b cv-pri cv-env">Enviar</button></div>
<div class="cv-voz sub" hidden><span class="cv-vponto" aria-hidden="true"></span><span class="cv-vm"></span><span class="cv-vtempo mono" aria-hidden="true"></span>
<span class="cv-vn" aria-hidden="true"><i></i></span><span class="cv-vprog" aria-hidden="true"><i></i></span><button type="button" class="cv-b" data-a="mic-cancelar">Cancelar</button></div>
<p class="cp-dita sub" hidden>O teclado do telemóvel também dita: toca no microfone do teclado.</p>
<p id="cv-dica" class="cv-dica sub">Enter envia · Shift+Enter muda de linha</p>
<p class="cv-fm sub" role="status" aria-live="polite"></p></form></div>
<p class="cv-vh cp-viva" role="status" aria-live="polite"></p><p class="cv-vh cp-alerta" role="alert"></p>`;

function montar() {
  root.innerHTML = SHELL;
  S.montado = true;
  const pt = $(".cv-ponte"); pintar(pt, "desligado", pt.textContent);
  $(".cv-q").addEventListener("input", () => { clearTimeout(S.tq); S.tq = setTimeout(() => carregarLista(), 300); });
  root.addEventListener("click", clique);
  root.addEventListener("submit", (e) => { e.preventDefault(); if (e.target.matches(".cv-f")) enviarTexto(); else if (e.target.matches(".cp-cod")) entrar(e.target); else if (e.target.matches(".cp-nomef")) mudarNome(e.target.elements.nome.value); });
  root.addEventListener("keydown", tecla);
  const ta = $("#cv-txt");
  // Enter na caixa só envia texto («enviar»); nunca responde a um cartão (CA-29)
  ta.addEventListener("keydown", (e) => {
    if (!teclaEnvia(e, grosso())) return;
    e.preventDefault();
    if (trabalha(S.estado)) $(".cv-fm").textContent = `${S.nome} ainda está a trabalhar.`;
    else enviarTexto();
  });
  ta.addEventListener("input", () => crescer(ta));
  const MR = globalThis.MediaRecorder;
  S.mime = MR && navigator.mediaDevices?.getUserMedia ? escolherMime(MR.isTypeSupported?.bind(MR)) : null;
  S.mic = S.mime ? "pronto" : "sem_suporte";
}

// a caixa cresce com o texto; o CSS limita-a a 8 linhas (depois faz scroll)
function crescer(ta) { ta.style.height = "auto"; if (ta.value) ta.style.height = ta.scrollHeight + 2 + "px"; }

// ---- voz (ADR-020): grava, manda o áudio e põe o texto na caixa; nunca envia (só «Enviar»/Enter enviam)

// o texto ditado entra na caixa (junta-se ao que lá está), a caixa cresce e o foco fica no fim; exportada para os testes (CA-16, CA-17)
export function aplicarTranscricao(ta, texto) {
  const j = juntarTranscricao(ta.value, texto);
  ta.value = j.texto; crescer(ta);
  ta.focus({ preventScroll: true }); ta.setSelectionRange(ta.value.length, ta.value.length);
  return j.cortado;
}
// chamada também a cada sondagem (pintarTopo): não mexe no relógio da gravação (este volta a 0:00 só ao começar, no micClique)
function pintarMic(nota) {
  if (!S.montado) return;
  const b = $("#cv-mic"), v = $(".cv-voz"), m = S.mic, grava = m === "a_gravar", pede = m === "a_pedir", trans = m === "a_transcrever";
  const inactivo = !S.ponte && !grava || !["pronto", "erro", "a_gravar", "sem_permissao", "a_pedir"].includes(m);
  // o nome fica o do controlo; o motivo de estar inactivo também vai em texto visível (.cv-fm) ao tocar, nunca só no title.
  // Nunca «disabled»: um botão desactivado engole o toque (no telemóvel, sem title, era um toque calado)
  const rot = grava ? "Parar a gravação" : "Ditar mensagem";
  const dica = trans ? "A transcrever…" : pede ? VOZ_MSG.pedir : !S.ponte && !grava ? VOZ_MSG.ponte : !grava && VOZ_MSG[m] ? VOZ_MSG[m] : rot;
  b.setAttribute("aria-pressed", String(grava || pede)); b.setAttribute("aria-label", rot); b.title = dica;
  if (inactivo) b.setAttribute("aria-disabled", "true"); else b.removeAttribute("aria-disabled");
  if (pede) b.setAttribute("aria-busy", "true"); else b.removeAttribute("aria-busy");
  b.disabled = false;
  b.classList.toggle("cv-rec", grava);
  b.classList.toggle("cv-pedir", pede);
  b.querySelector("span").textContent = grava ? "Parar" : "";
  b.hidden = !vozModo(); $(".cp-dita").hidden = !!vozModo();
  v.hidden = !grava && !trans && !pede;
  v.classList.toggle("cv-vgrava", grava); v.classList.toggle("cv-vtrans", trans);
  v.querySelector(".cv-vtempo").hidden = v.querySelector(".cv-vn").hidden = v.querySelector(".cv-vponto").hidden = !grava;
  v.querySelector(".cv-vprog").hidden = !trans && !pede;
  v.querySelector("button").hidden = v.hidden; // «Cancelar» a pedir, a gravar e a transcrever (também durante o envio do áudio)
  // a .cv-vm aparece no mesmo instante que o texto (dentro do .cv-voz[hidden]); o anúncio vai pela região que existe sempre
  if (nota != null) { v.querySelector(".cv-vm").textContent = nota; if (nota) $(".cp-viva").textContent = nota; }
}
// todos os bloqueios do microfone: texto visível por baixo da caixa e anunciado (nunca um toque calado)
function avisoMic(texto) {
  if (!S.montado) return;
  const fm = $(".cv-fm");
  fm.textContent = texto;
  if (!texto) return;
  $(".cp-viva").textContent = texto;
  // o formulário faz scroll e, no iPhone, o teclado tapa o fundo: a mensagem vem para a vista (só o mínimo)
  try { fm.scrollIntoView?.({ block: "nearest" }); } catch { /* browsers antigos */ }
}
// voz externa: o áudio sai para o fornecedor do dono; um clique em «Aceito» por fornecedor (guardado no servidor). O gesto perde-se
// no diálogo (iOS): depois de aceitar, toca-se outra vez no microfone
async function avisoVozExterna() {
  if (!(await dialogo("Voz por um fornecedor externo", avisoVoz(S.est?.voz_fornecedor), "Aceito", "Cancelar"))) return avisoMic("");
  const r = await gravarPrefs({ aviso_voz: true });
  avisoMic(r.status === 200 ? "Aceite. Toca outra vez no microfone para ditar." : erroTxt(r));
}
// Cancelar, Esc, trocar de conversa: pára, descarta e larga o microfone; a transcrição em curso é abandonada (D11)
function cancelarVoz() {
  S.vseq++; clearTimeout(S.tv); clearTimeout(S.tpedir); S.vctl?.abort(); S.vctl = null;
  S.grav?.cancelar(); S.grav = null; S.voz = null;
  if (S.mic === "a_pedir") { S.mic = micEstado(S.mic, "cancelar"); if (S.montado) $(".cv-fm").textContent = ""; pintarMic(""); }
  if (S.mic === "a_gravar" || S.mic === "a_transcrever") { S.mic = micEstado(S.mic, "cancelar"); pintarMic(""); }
}
const uaToque = () => [globalThis.navigator?.userAgent, globalThis.navigator?.maxTouchPoints];
// à espera há muito (ou novo toque à espera): o pedido do browser pode não ter aparecido; diz onde se liga o microfone
const DEMORA = "O browser não perguntou nada? ";
const msgPedirDemora = () => DEMORA + msgSemPermissao(...uaToque()).replace(/^Sem acesso ao microfone\. /, "");
// cada saída deixa texto visível (CA da tarefa iPhone, 22/11): sem conversa, sem suporte, ponte parada, à espera, recusado
const vozModo = () => (["local", "externa"].includes(S.est?.voz) ? S.est.voz : null);
function micClique() {
  if (S.mic === "a_gravar") return S.grav?.parar();
  if (vozModo() && S.est.voz_ponte !== S.est.voz) return avisoMic(`A voz ${S.est.voz} não está instalada no servidor. Escreve a mensagem.`);
  if (vozModo() === "externa" && S.est.aviso_voz !== true) return avisoVozExterna();
  if (!S.id) return avisoMic(VOZ_MSG.sem_conversa);
  if (criandoAte(S.id) > Date.now()) return avisoMic(AINDA_A_CRIAR); // R6: o áudio de uma conversa por criar dava 404
  if (S.mic === "a_pedir") return avisoMic(msgPedirDemora());
  if (S.mic === "a_transcrever") return avisoMic("A transcrever o áudio anterior. Espera um momento ou toca em «Cancelar».");
  if (!["pronto", "erro", "sem_permissao"].includes(S.mic)) return avisoMic(VOZ_MSG[S.mic] || VOZ_MSG.falhou);
  if (!S.ponte) return avisoMic(VOZ_MSG.ponte);
  let explicada = false;
  try { explicada = LS().getItem("conversa.voz.explicada") === "1"; if (!explicada) LS().setItem("conversa.voz.explicada", "1"); } catch { /* modo privado */ }
  const id = S.id, seq = ++S.vseq, v = $(".cv-voz");
  const g = criarGravador({ getUserMedia: (c) => navigator.mediaDevices.getUserMedia(c), MediaRecorder: globalThis.MediaRecorder,
    AudioContext: globalThis.AudioContext || globalThis.webkitAudioContext, mime: S.mime, agora: () => Date.now(),
    setTimeout: (f, ms) => setTimeout(f, ms), clearTimeout: (t) => clearTimeout(t), setInterval: (f, ms) => setInterval(f, ms), clearInterval: (t) => clearInterval(t),
    aoTempo: (s) => { v.querySelector(".cv-vtempo").textContent = fmtSeg(s); },
    aoNivel: (n) => { v.querySelector(".cv-vn i").style.transform = `scaleX(${n.toFixed(2)})`; },
    aoFim: (r) => vozParou(r, id, seq) });
  S.grav = g;
  // feedback imediato, no mesmo toque (antes de qualquer await): botão premido, «A pedir acesso…» e «Cancelar»
  S.mic = micEstado(S.mic, "pedir");
  avisoMic(explicada ? "" : VOZ_MSG.explicar);
  pintarMic(VOZ_MSG.pedir);
  // o pedido (getUserMedia + AudioContext) parte já, dentro do gesto; a espera é assíncrona
  const inicio = g.iniciar();
  // 8 s sem resposta (o pedido do browser pode não ter aparecido): diz onde se liga o microfone, sem desistir do pedido
  clearTimeout(S.tpedir);
  S.tpedir = setTimeout(() => { if (seq === S.vseq && S.mic === "a_pedir") avisoMic(msgPedirDemora()); }, 8000);
  return micIniciou(inicio, g, seq);
}
async function micIniciou(inicio, g, seq) {
  const v = $(".cv-voz");
  try { if (!(await inicio) || seq !== S.vseq) return; }
  catch (e) {
    if (seq !== S.vseq) return;
    clearTimeout(S.tpedir);
    S.grav = null;
    const ev = e?.name === "NotAllowedError" || e?.name === "SecurityError" ? "negado" : e?.name === "NotFoundError" || e?.name === "OverconstrainedError" ? "sem_mic" : "falha";
    S.mic = micEstado(S.mic, ev); ev == "negado" && A.tocar?.("erro");
    avisoMic(S.mic === "sem_permissao" ? msgSemPermissao(...uaToque()) : VOZ_MSG[S.mic] || VOZ_MSG.falhou);
    return pintarMic("");
  }
  clearTimeout(S.tpedir);
  if ($(".cv-fm").textContent.startsWith(DEMORA)) $(".cv-fm").textContent = "";
  S.mic = micEstado(S.mic, "gravar"); v.querySelector(".cv-vtempo").textContent = fmtSeg(0); pintarMic("a gravar"); A.tocar?.("gravar");
}
// parou (toque ou limite dos 2 min): manda o áudio; o texto chega pelo evento «transcricao» com o «audio» do 202 (D6)
async function vozParou({ blob, limite }, id, seq) {
  if (seq !== S.vseq) return;
  A.tocar?.("parar"); S.grav = null;
  S.mic = micEstado(S.mic, "parar"); pintarMic((limite ? VOZ_MSG.limite + " · " : "") + "a transcrever…");
  // prazo de 30 s para o envio (rede lenta); «Cancelar» aborta-o (cancelarVoz)
  const ctl = S.vctl = new AbortController(), tm = setTimeout(() => { ctl.abort(); if (seq === S.vseq) { S.vctl = null; vozFim(null, VOZ_MSG.tempo); } }, VOZ_ENVIO_MS);
  const r = await enviarAudio(id, blob, ctl.signal);
  clearTimeout(tm);
  if (seq !== S.vseq || id !== S.id) return;
  S.vctl = null;
  if (ctl.signal.aborted) return vozFim(null, VOZ_MSG.tempo);
  if (r.status === 202 && NONCE.test(str(r.j.audio))) {
    S.voz = { audio: r.j.audio, id, ate: Date.now() + VOZ_ESPERA_MS };
    S.tv = setTimeout(() => { if (seq === S.vseq) vozFim(null, VOZ_MSG.tempo); }, VOZ_ESPERA_MS);
    return sondar();
  }
  vozFim(null, erroVoz(r.status, r.j));
}
// fim da transcrição: {texto} | {aviso} (respostaVoz) | null + erro; o texto escrito na caixa nunca se perde
function vozFim(res, erro) {
  S.vseq++; clearTimeout(S.tv); S.voz = null;
  const msg = $(".cv-fm");
  if (res?.texto != null && limpa(res.texto).trim()) {
    S.mic = micEstado(S.mic, "texto"); pintarMic("");
    const ta = $("#cv-txt");
    msg.textContent = aplicarTranscricao(ta, res.texto) ? VOZ_MSG.cortado : "";
    // destaque curto na caixa quando o texto entra (com reduced-motion só a cor da borda, sem animação: ver o CSS)
    ta.classList.add("cv-ditado"); clearTimeout(S.tdit); S.tdit = setTimeout(() => ta.classList.remove("cv-ditado"), 1600);
    $(".cp-viva").textContent = "Texto ditado na caixa. Revê e toca em Enviar.";
    return;
  }
  if (res?.texto != null) { S.mic = micEstado(S.mic, "texto"); msg.textContent = VOZ_MSG.vazio; return pintarMic(""); } // P3: não mexe na caixa
  const ind = res?.aviso?.codigo === "voz_indisponivel";
  S.mic = micEstado(S.mic, ind ? "indisponivel" : "falha");
  msg.textContent = ind ? VOZ_MSG.indisponivel : res?.aviso ? res.aviso.texto || VOZ_MSG.falhou : erro;
  pintarMic("");
}

// ---- página: abrir e teclas

// foco ao abrir (CA-8, D3): título do pedido activo; senão a caixa (computador) ou o título do painel (telemóvel); nunca um botão de aprovar
function focoAoAbrir() {
  const h = $(".cp-act:not([hidden]) h3");
  if (h) return h.focus({ preventScroll: true });
  const alvo = grosso() ? $("#cp-h") : !$(".cv-cv").hidden ? (!$(".cv-f").hidden ? $("#cv-txt") : $("#cp-h")) : $(".cv-q");
  alvo.focus({ preventScroll: true });
}

// destino: "lista" | id | "erro" | nada (a última conversa aberta, ou a lista)
async function abrir(destino, foco = true) {
  const ini = !S.aberto;
  S.aberto = true;
  S.dest = destino;
  if (!(await pronto())) { mostrarEntrada(); if (foco && S.aberto) ($(".cp-cod").hidden ? $("#cp-h") : $("#cp-codigo")).focus({ preventScroll: true }); return; }
  $(".cp-ent").hidden = true;
  if (S.est.aviso_tokens !== true) avisoTokens();
  if (destino === "lista") await mostrarLista();
  else if (destino === "erro") naoEncontrada();
  else if (idValido(destino)) await abrirConversa(destino);
  else if (ini) { const id = S.id || lerUltima(LS()); await (id ? abrirConversa(id) : mostrarLista()); }
  if (foco && S.aberto) focoAoAbrir();
}

// Esc fecha o mais interior: gravação → editor do plano → «Ver tudo» (UX §5)
function tecla(e) {
  if (e.key !== "Escape" || e.isComposing || dialogoAberto()) return;
  e.preventDefault(); e.stopPropagation();
  if (S.mic === "a_gravar" || S.grav) return cancelarVoz();
  const ed = $(".cp-act .cv-ed:not([hidden])"), tudo = $(".cp-act .cv-tudo");
  if (ed) return planoEd(ed.closest(".cv-card"));
  if (tudo) return verTudo(tudo);
}
// «#conversa» e «#conversa/<id>» (ligações e o botão «voltar» do browser) abrem a lista ou essa conversa
function rotaHash() {
  const r = rota(location.hash);
  if (r) abrir(r.lista ? "lista" : r.id || "erro");
}

// ---- lista

async function mostrarLista() {
  S.g++; S.id = null; S.criando = null; clearTimeout(S.t); clearInterval(S.tick); cancelarVoz();
  S.actNonce = null; S.temAct = false; root.classList.remove("cp-pedido", "cp-tudo");
  gravarUltima(LS(), null);
  $(".cv-cv").hidden = true; $(".cv-lv").hidden = false; $(".cp-ent").hidden = true;
  pintarTopo();
  await carregarLista();
}

async function carregarLista() {
  const q = pesquisa($(".cv-q").value), msg = $(".cv-lm"), n = ++S.qseq;
  if (q === null) { msg.textContent = "Escreve entre 2 e 100 caracteres para pesquisar."; return; }
  pintar(msg, "a-processar", "a carregar", Date.now());
  const r = await obter("api/conversa/lista" + (q ? "?" + new URLSearchParams({ q }) : ""));
  if (n !== S.qseq || !S.aberto) return;
  if (r.status !== 200) { if (r.status !== 401) pintar(msg, "erro", erroTxt(r)); return; }
  S.ponte = r.j.ponte !== false;
  pintarTopo();
  const lista = ordenar(r.j.conversas);
  msg.textContent = lista.length ? "" : q ? "Nada encontrado." : "Ainda não há conversas. Carrega em «+ Nova conversa».";
  $(".cv-l").replaceChildren(...lista.map((c) => el("li", {}, el("button", { type: "button", class: "cv-it", "data-a": "abrir", "data-id": c.id },
    el("b", { text: str(c.titulo) || "Conversa" }),
    el("span", { class: "sub", text: [c.pendentes > 0 ? ESTADO.a_espera : ESTADO[c.estado] || "", dataRelativa(c.actualizada)].filter(Boolean).join(" · ") }),
    c.arquivada ? el("span", { class: "cv-tag", text: "arquivada" }) : null))));
}

// ---- conversa

function abrirConversa(id) {
  $(".cv-lv").hidden = true; $(".cv-cv").hidden = false; $(".cp-ent").hidden = true;
  gravarUltima(LS(), id);
  // a mesma conversa ao reabrir: repinta (expirados saem da zona presa) e volta a pôr o relógio de 1 s (CA-17, CA-24)
  if (id === S.id && S.ev.length) { pintarEventos(); pintarTopo(); return sondar(); }
  cancelarVoz();
  if (S.criando?.id !== id) S.criando = null; // R6: a espera é só da conversa que se está a criar
  S.g++; Object.assign(S, { id, ev: [], desde: 0, titulo: "", meta: null, estado: null, arquivada: false, ate: 0, aguarda: 0, parando: false, actNonce: null, temAct: false, t0: Date.now() });
  S.els.clear(); S.sig.clear();
  $(".cv-ev").replaceChildren(); $(".cp-act").replaceChildren(); $(".cp-act").hidden = true; root.classList.remove("cp-pedido", "cp-tudo");
  $(".cv-st").textContent = "";
  pintarTopo();
  return sondar();
}

async function carregar(g) {
  let novos = 0;
  const lote = [];
  for (let pag = 0; pag < 50;) {
    const r = await obter(`api/conversa/eventos?${new URLSearchParams({ conversa: S.id, desde: S.desde })}`);
    if (g !== S.g) return false;
    if (r.status === 404 && Date.now() < criandoAte(S.id)) { pintar($(".cv-st"), "a-processar", "a criar a conversa", criandoAte(S.id) - CRIAR_MS); await espera(1000); if (g !== S.g) return false; continue; }
    if (r.status === 404) { naoEncontrada(); return false; }
    if (r.status !== 200) { const n = $(".cv-st"); if (r.status !== 401) pintar(n, "erro", erroTxt(r)); return false; }
    if (S.criando?.id === S.id) S.criando = null;
    const j = r.j, ev = Array.isArray(j.eventos) ? j.eventos : [];
    lote.push(...ev); S.ev.push(...ev.filter(eventoVisivel)); novos += ev.length;
    if (Number.isInteger(j.seguinte) && j.seguinte >= S.desde) S.desde = j.seguinte;
    Object.assign(S, { titulo: str(j.titulo), meta: j.estado, arquivada: j.arquivada === true, ponte: j.ponte !== false });
    pag++;
    if (!j.mais || !ev.length) break;
  }
  $(".cv-st").textContent = "";
  const voz = S.voz?.id === S.id ? respostaVoz(lote, S.voz.audio) : null;
  if (voz) vozFim(voz);
  S.estado = ultimoEstado(S.ev, S.meta);
  respondeu(S.trab, S.id, S.estado, Date.now()) && A.tocar?.("respondeu");
  for (const e of lote) if (e?.tipo === "custo" && typeof e.dia_usd === "number" && S.est) { S.est.dia_usd = e.dia_usd; pintarNome(); }
  if (S.aguarda && envioVisto(S.ev, S.desdeEnvio)) S.aguarda = 0;
  if (!trabalha(S.estado)) S.parando = false;
  if (novos || !S.els.size) pintarEventos();
  pintarTopo();
  return true;
}

function naoEncontrada() {
  S.g++; S.id = null; S.criando = null; clearTimeout(S.t); cancelarVoz();
  gravarUltima(LS(), null);
  $(".cv-lv").hidden = true; $(".cv-cv").hidden = false;
  S.els.clear(); S.sig.clear(); S.ev = []; S.actNonce = null; S.temAct = false;
  $(".cp-act").replaceChildren(); $(".cp-act").hidden = true; root.classList.remove("cp-pedido", "cp-tudo");
  $(".cv-ev").replaceChildren(el("li", { class: "cv-meta" }, "Esta conversa não existe. ",
    el("button", { type: "button", class: "cv-b", "data-a": "voltar", text: "Ver as conversas" })));
  pintarTopo();
  $("#cp-h").textContent = "Conversa não encontrada.";
}

async function sondar() {
  clearTimeout(S.t);
  const g = S.g;
  if (!S.id) return;
  await carregar(g);
  if (g === S.g && S.aberto && !document.hidden && deveSondar(S.estado, Math.max(S.ate, S.aguarda, criandoAte(S.id), S.voz?.ate || 0))) S.t = setTimeout(sondar, SONDA_MS);
}

function pintarTopo() {
  if (!S.montado) return;
  $(".cv-ponte").hidden = S.ponte;
  const conv = !$(".cv-cv").hidden;
  $('[data-a="voltar"]').hidden = !conv;
  $('.cp-top [data-a="nova"]').hidden = !conv;
  $(".cp-sub").hidden = !S.id || !conv;
  if (!conv) { $("#cp-h").textContent = "Conversas"; return; }
  if (!S.id) { $(".cv-f").hidden = true; $(".cp-arq").hidden = true; return; }
  if (S.titulo || S.ev.length) $("#cp-h").textContent = S.titulo || "Conversa"; else pintar($("#cp-h"), "a-processar", "a carregar", S.t0);
  $(".cp-mod").textContent = "Modelo: " + modeloEmUso(LS(), S.id);
  pintarEstado();
  const p = mostraParar(S.estado);
  $(".cv-f").hidden = S.arquivada;
  $(".cp-arq").hidden = S.arquivada || trabalha(S.estado) || !S.ev.length;
  // a trabalhar, «Enviar» dá lugar a «Parar» (CA-24)
  const b = $(".cv-env");
  Object.assign(b, { type: p ? "button" : "submit", textContent: p ? (S.parando ? "A parar…" : "Parar") : S.aguarda ? "A enviar…" : "Enviar",
    disabled: p ? S.parando : !!S.aguarda || S.estado === "a_espera" });
  if (p) b.dataset.a = "parar"; else delete b.dataset.a;
  b.classList.toggle("cv-stop", p);
  pintarMic();
}

const pintarEstado = () => pintar($(".cp-rel"), ...pecaEstado(S.ev, S.estado, S.arquivada, S.temAct));

function pintarEventos() {
  const fl = $(".cp-flx"), ol = $(".cv-ev"), fundo = fl.scrollTop + fl.clientHeight >= fl.scrollHeight - 120, primeira = !S.els.size;
  const itens = fundir(S.ev), agora = Date.now();
  let novo = false;
  for (const it of agrupar(itens)) {
    const pend = PEDIDOS.includes(it.tipo) && pendente(it, agora, S.arquivada);
    const k = String(it.n), sig = JSON.stringify(it) + (S.arquivada ? "|a" : "") + (pend ? "|p" : "");
    const e = S.els.get(k);
    if (e && S.sig.get(k) === sig) continue;
    const n = itemEl(it, pend);
    if (!n) { e?.remove(); S.els.delete(k); continue; }
    if (e) { if (e.querySelector("details")?.open) n.querySelector("details").open = true; e.replaceWith(n); } // repintar não fecha
    else { ol.append(n); novo = true; }
    S.els.set(k, n); S.sig.set(k, sig);
  }
  const { activo, mais } = S.arquivada ? { activo: null, mais: 0 } : pedidoActivo(itens, agora);
  pintarActivo(activo, mais);
  if (novo && (fundo || primeira)) fl.scrollTop = fl.scrollHeight;
  relogio();
}

// um relógio de 1 s enquanto houver pedido activo (expira em) ou o motor a trabalhar
function relogio() {
  clearInterval(S.tick);
  const pinta = () => {
    if (!S.id) return clearInterval(S.tick);
    pintarEstado();
    const x = $(".cp-act [data-exp]");
    if (x) {
      const s = segundosAte(x.dataset.exp);
      if (s > 0) x.textContent = `expira em ${fmtSeg(s)}`;
      else { x.removeAttribute("data-exp"); pintarEventos(); return; } // expirou: sai da zona presa (CA-17)
    }
    if (!x && !trabalha(S.estado)) clearInterval(S.tick);
  };
  pinta();
  if (S.aberto) S.tick = setInterval(pinta, 1000);
}

const pre = (t, cls = "cv-pre") => el("pre", { class: cls, text: str(t) });
const botao = (txt, a, e, cls = "") => el("button", { type: "button", class: ("cv-b " + cls).trim(), "data-a": a, "data-e": e, text: txt });
const mdEl = (t, cls = "") => el("div", { class: ("cv-md " + cls).trim() }, materializar(md(str(t))));
const tituloPedido = (it) => (it.tipo === "permissao" ? (it.delegacao ? "Delegação a uma squad" : "Pedido de permissão") : it.tipo === "plano" ? "Plano proposto" : "Pergunta");
const curto = (s, n = 60) => { const t = str(s).replace(/\s+/g, " ").trim(); return t.length > n ? t.slice(0, n - 1) + "…" : t; };
const passoEl = (p) => el("li", {}, el("b", { text: str(p.nome) || "Ferramenta" }), p.resumo ? " · " + str(p.resumo) : "",
  p.resultado != null ? pre(p.resultado) : el("p", { class: "sub", text: "a correr" }));

// um item do fluxo (já fundido e agrupado) → <li>; null = não se mostra. Os pedidos no fluxo nunca têm botões: pendente =
// linha compacta (responde-se no cartão preso em baixo); resolvido/expirado = <details> só de leitura (CA-12, CA-16)
export function itemEl(it, pend = PEDIDOS.includes(it?.tipo) && pendente(it)) {
  const t = hora(it.t);
  switch (it.tipo) {
    case "mensagem": {
      const dono = it.autor === "dono";
      return el("li", { class: "cv-msg " + (dono ? "cv-dono" : "cv-as") }, el("span", { class: "sub", text: (dono ? "Tu" : S.nome) + (t ? " · " + t : "") }),
        dono ? el("div", { class: "cv-tx", text: str(it.texto) }) : mdEl(it.texto));
    }
    case "ferramenta": return itemEl({ tipo: "grupo", n: it.n, passos: [it] });
    case "grupo": // fechado por omissão, mesmo em curso (CA-23)
      return el("li", { class: "cv-fe" }, el("details", {}, el("summary", { text: resumoGrupo(it.passos) }), el("ul", {}, ...it.passos.map(passoEl))));
    case "permissao": case "pergunta": case "plano": {
      if (pend) return el("li", { class: "cv-linha" }, tituloPedido(it) + (it.tipo === "permissao" ? ": " + str(it.ferramenta) : "") + " · " + ESTADO.a_espera);
      const res = it.resolvido ? (RESOLVIDO[it.resolvido] || str(it.resolvido)) + (it.auto ? " (regra desta conversa)" : "")
        : S.arquivada ? "Conversa arquivada" : RESOLVIDO.expirada;
      const resumo = [res, it.tipo === "permissao" ? str(it.ferramenta) : tituloPedido(it), it.tipo === "permissao" ? curto(it.alvo) : "", t].filter(Boolean).join(" · ");
      return el("li", { class: "cv-card cv-feito" + (it.delegacao ? " cv-del" : "") }, el("details", {}, el("summary", { text: resumo }),
        el("h3", { text: tituloPedido(it) }), ...conteudo(it, false)));
    }
    case "aviso": return el("li", { class: "cv-av", role: "note" }, "⚠ " + str(it.texto || it.codigo));
    case "custo": return el("li", { class: "cv-meta" }, `≈ US$ ${Number(it.usd || 0).toFixed(2).replace(".", ",")} · ${Number(it.tokens || 0).toLocaleString("pt-PT")} tokens`
      + (typeof it.dia_usd === "number" ? ` · hoje ≈ US$ ${it.dia_usd.toFixed(2).replace(".", ",")}` : ""));
    case "fim": return el("li", { class: "cv-meta" }, FIM[it.motivo] || "Fim.");
    default: return null; // «estado» vai para o topo; tipos desconhecidos não aparecem
  }
}

// o conteúdo de um pedido (alvo inteiro, brief, plano, perguntas); «vivo» = no cartão activo, com os controlos da pergunta
function conteudo(it, vivo) {
  if (it.tipo === "permissao") {
    const alvo = str(it.alvo), nl = alvo.split("\n").length;
    // D1: o <pre> tem sempre o alvo inteiro; o cartão activo tem altura limitada (scroll próprio) e «Ver tudo» abre-o
    const tudo = vivo && (nl > 12 || alvo.length > 900) ? nl > 12 ? `Ver tudo (${nl} linhas)` : "Ver tudo" : null;
    return [el("p", {}, el("span", { class: "sub", text: "Ferramenta: " }), el("b", { text: str(it.ferramenta) })), pre(alvo, "cv-pre cv-alvo"),
      tudo ? el("button", { type: "button", class: "cv-b cv-vt", "data-a": "tudo", "data-t": tudo, "aria-expanded": "false", text: tudo }) : null,
      it.sha256 ? el("p", { class: "sub mono cv-sha", text: "sha256 " + str(it.sha256).slice(0, 12) }) : null,
      it.resumo ? el("p", { text: str(it.resumo) }) : null, it.brief ? briefEl(it.brief) : null];
  }
  if (it.tipo === "plano") return [mdEl(it.texto, "cv-plano")];
  return (Array.isArray(it.perguntas) ? it.perguntas.slice(0, 4) : []).map((p, i) => {
    const fs = el("fieldset", { "data-q": i, "data-multi": p?.multipla === true ? "1" : "" },
      el("legend", {}, p?.cabecalho ? el("span", { class: "cv-cab", text: str(p.cabecalho) }) : null, str(p?.pergunta)),
      vivo && p?.multipla === true ? el("p", { class: "sub", text: "Podes escolher várias." }) : null);
    const ops = Array.isArray(p?.opcoes) ? p.opcoes.slice(0, 4) : [];
    if (vivo) {
      fs.append(el("div", { class: "cv-ops" }, ...ops.map((o, j) =>
        el("button", { type: "button", class: "cv-b cv-opc", "data-a": "op", "data-o": j, "aria-pressed": "false" },
          el("b", { text: str(o?.rotulo) }), o?.descricao ? el("span", { class: "sub", text: str(o.descricao) }) : null))),
      el("button", { type: "button", class: "cv-b cv-out", "data-a": "outra", "aria-expanded": "false", text: "Outra resposta" }),
      el("textarea", { class: "cv-outx", rows: 2, maxlength: OUTRA_MAX, hidden: true, "aria-label": `Outra resposta à pergunta ${i + 1}` }));
    } else fs.append(el("ul", {}, ...ops.map((o) => el("li", { text: str(o?.rotulo) }))));
    return fs;
  });
}

const textoMais = (mais) => `Mais ${mais} à espera`;
const tituloActivo = (it, mais) => tituloPedido(it) + (mais ? ` · 1 de ${mais + 1}` : "");

// o pedido activo, preso acima da caixa: o único sítio com botões de resposta (CA-12 a CA-15; exportada para os testes)
export function cartaoActivo(it, mais = 0) {
  const env = S.enviados.has(it.nonce), corpo = conteudo(it, true), fixo = (x) => /\bcv-(vt|sha)\b/.test(x?.className || "");
  const c = el("div", { class: "cv-card cv-pend" + (it.delegacao ? " cv-del" : ""), "data-n": it.n, "data-nonce": it.nonce, "data-tipo": it.tipo },
    el("div", { class: "cp-cab" }, el("h3", { tabindex: "-1", text: tituloActivo(it, mais) }), el("span", { class: "cv-exp sub", "data-exp": it.expira })),
    el("div", { class: "cp-corpo" }, ...corpo.filter((x) => !fixo(x))));
  c.append(...corpo.filter(fixo)); // A1: «Ver tudo» e o sha256 fora do scroll do corpo, presos entre o corpo e os botões
  if (it.tipo === "permissao") c.append(el("div", { class: "btns" }, botao(podeConversa(it) ? "Aprovar" : "Aprovar delegação", "perm", "uma_vez", "ok-b"),
    podeConversa(it) ? botao("Aprovar nesta conversa", "perm", "conversa") : null, botao("Recusar", "perm", "recusar", "no-b")));
  else if (it.tipo === "plano") c.append(el("div", { class: "btns" }, botao("Aprovar plano", "plano", "aprovar", "ok-b"), botao("Continuar a planear", "plano", "continuar"),
    botao("Editar", "plano-ed")), el("div", { class: "cv-ed", hidden: true },
    el("label", { class: "cv-vh", for: "cv-ed-" + it.n, text: "Plano editado" }),
    el("textarea", { id: "cv-ed-" + it.n, rows: 8, maxlength: TEXTO_MAX, value: str(it.texto) }),
    el("div", { class: "btns" }, botao("Enviar plano editado", "plano", "editar", "ok-b"), botao("Cancelar", "plano-ed"))));
  else c.append(el("div", { class: "btns" }, botao("Responder", "resp", null, "ok-b")));
  c.append(el("p", { class: "cp-mais sub", hidden: !mais, text: mais ? textoMais(mais) : "" }),
    el("p", { class: "cv-cm sub", role: "status", text: env ? "Enviado. À espera da resposta…" : "" }));
  if (env) for (const b of c.querySelectorAll("button, textarea")) b.disabled = true;
  return c;
}

function pintarActivo(it, mais) {
  const z = $(".cp-act"), tinhaFoco = z.contains(document.activeElement);
  S.temAct = it; // o t conta na peça
  root.classList.toggle("cp-pedido", !!it);
  if (!it) {
    if (S.actNonce === null) return;
    S.actNonce = null; z.replaceChildren(); z.hidden = true; root.classList.remove("cp-tudo");
    if (tinhaFoco) focoAoAbrir();
    return;
  }
  if (it.nonce === S.actNonce) { // o mesmo pedido: só os contadores mudam (não se perde o que já se escolheu)
    z.querySelector("h3").textContent = tituloActivo(it, mais);
    const m = z.querySelector(".cp-mais");
    m.textContent = mais ? textoMais(mais) : ""; m.hidden = !mais;
    return;
  }
  S.actNonce = it.nonce;
  const c = cartaoActivo(it, mais), bs = [...c.querySelectorAll("button")].filter((b) => !b.disabled);
  z.replaceChildren(c); z.hidden = false; root.classList.remove("cp-tudo");
  // D3: o pedido que entra fica 600 ms inactivo (um duplo clique não aprova o seguinte sem ser lido)
  for (const b of bs) b.disabled = true;
  setTimeout(() => { if (S.actNonce === it.nonce && !S.enviados.has(it.nonce)) for (const b of bs) b.disabled = false; }, 600);
  $(".cp-alerta").textContent = it.tipo === "permissao" ? `${S.nome} pede aprovação: ${str(it.ferramenta)}. 1 de ${mais + 1}.`
    : it.tipo === "plano" ? `${S.nome} propõe um plano.` : `${S.nome} faz uma pergunta.`;
  if (tinhaFoco) c.querySelector("h3").focus({ preventScroll: true });
}

function verTudo(c) {
  const b = c.querySelector('[data-a="tudo"]'), on = !c.classList.contains("cv-tudo");
  c.classList.toggle("cv-tudo", on); root.classList.toggle("cp-tudo", on);
  if (!b) return;
  b.textContent = on ? "Ver menos" : b.dataset.t; b.setAttribute("aria-expanded", String(on));
  b.focus({ preventScroll: true });
}

function planoEd(c) {
  const ed = c.querySelector(".cv-ed"), abrirEd = ed.hidden;
  ed.hidden = !abrirEd;
  c.querySelector(".btns").hidden = abrirEd;
  return abrirEd ? ed.querySelector("textarea").focus() : c.querySelector('[data-a="plano-ed"]').focus();
}

function briefEl(b) {
  if (b.erro) return el("p", { class: "cv-av", text: "Brief: " + str(b.erro) });
  return el("div", { class: "cv-brief" }, el("p", {}, el("span", { class: "sub", text: "Brief: " }), el("span", { class: "mono", text: str(b.caminho) })),
    pre((Array.isArray(b.inicio) ? b.inicio.slice(0, 20) : []).map(str).join("\n")),
    b.sha256 ? el("p", { class: "sub mono", text: "sha256 " + str(b.sha256).slice(0, 12) }) : null);
}

// estado do formulário de uma pergunta lido do próprio DOM (o cartão só se refaz quando o pedido muda)
const formDe = (li) => [...li.querySelectorAll("fieldset[data-q]")].map((fs) => ({
  opcoes: [...fs.querySelectorAll('[data-a="op"][aria-pressed="true"]')].map((b) => +b.dataset.o),
  outra: fs.querySelector(".cv-outx")?.hidden ? "" : fs.querySelector(".cv-outx")?.value ?? "" }));

async function responder(li, acao, campos) {
  const msg = li.querySelector(".cv-cm"), cs = li.querySelectorAll("button, textarea"), nonce = li.dataset.nonce;
  cs.forEach((b) => (b.disabled = true));
  msg.textContent = "A enviar…";
  const r = await enviarPedido(acao, { conversa: S.id, nonce, ...campos });
  if (r.status === 202) {
    S.enviados.add(nonce); msg.textContent = "Enviado. À espera da resposta…";
    S.ate = Date.now() + ESPERA_MS; return sondar();
  }
  cs.forEach((b) => (b.disabled = false));
  msg.textContent = erroTxt(r);
  if (r.status === 409) sondar();
}

// só cliques: nenhuma tecla nem texto da caixa gera responder_*/aprovar_plano (CA-29)
function clique(e) {
  const b = e.target.closest?.("button[data-a]");
  if (!b || b.disabled) return;
  const a = b.dataset.a, li = b.closest(".cv-card");
  if (a === "nome-repor") return mudarNome(null);
  if (a === "nova") return nova(b);
  if (a === "voltar") return mostrarLista().then(focoAoAbrir);
  if (a === "abrir") return abrirConversa(b.dataset.id).then(focoAoAbrir);
  if (a === "arquivar") return arquivar(b);
  if (a === "parar") return parar(b);
  if (a === "mic") return micClique();
  if (a === "mic-cancelar") { cancelarVoz(); return $("#cv-mic").focus(); }
  if (!li) return;
  if (a === "tudo") return verTudo(li);
  if (a === "perm") return responder(li, "responder_permissao", { escolha: b.dataset.e });
  if (a === "plano") {
    const texto = b.dataset.e === "editar" ? limpa(li.querySelector(".cv-ed textarea").value) : undefined;
    if (b.dataset.e === "editar" && !texto.trim()) { li.querySelector(".cv-cm").textContent = "Escreve o plano editado."; return; }
    return responder(li, "aprovar_plano", { escolha: b.dataset.e, texto });
  }
  if (a === "plano-ed") return planoEd(li);
  const fs = b.closest("fieldset");
  if (a === "op") {
    const multi = fs.dataset.multi === "1", on = b.getAttribute("aria-pressed") !== "true";
    if (!multi) for (const x of fs.querySelectorAll('[data-a="op"]')) x.setAttribute("aria-pressed", "false");
    b.setAttribute("aria-pressed", String(multi ? on : true));
    const ox = fs.querySelector(".cv-outx");
    ox.hidden = true; ox.value = ""; fs.querySelector('[data-a="outra"]').setAttribute("aria-expanded", "false");
    // uma pergunta só, de escolha única: um toque responde
    if (!multi && li.querySelectorAll("fieldset[data-q]").length === 1) return enviarRespostas(li);
    return;
  }
  if (a === "outra") {
    const ox = fs.querySelector(".cv-outx"), abrirOx = ox.hidden;
    ox.hidden = !abrirOx; b.setAttribute("aria-expanded", String(abrirOx));
    if (abrirOx) { for (const x of fs.querySelectorAll('[data-a="op"]')) x.setAttribute("aria-pressed", "false"); ox.focus(); } else ox.value = "";
    return;
  }
  if (a === "resp") return enviarRespostas(li);
}

function enviarRespostas(li) {
  const it = fundir(S.ev).find((x) => String(x.n) === li.dataset.n), respostas = respostasDe(it?.perguntas, formDe(li));
  if (!respostas) { li.querySelector(".cv-cm").textContent = "Responde a todas as perguntas (uma opção ou «Outra resposta», até 1000 caracteres)."; return; }
  return responder(li, "responder_pergunta", { respostas });
}

function gravarModelo(id, m) {
  try {
    const st = LS(), v = JSON.parse(st.getItem("conversa.modelos")), o = v && typeof v === "object" && !Array.isArray(v) ? v : {};
    o[id] = m; st.setItem("conversa.modelos", JSON.stringify(o));
  } catch { /* modo privado: o cabeçalho fica «o do costume» */ }
}

async function enviarTexto() {
  if (!S.id || trabalha(S.estado) || S.aguarda) return;
  if (S.est?.aviso_tokens !== true) return avisoTokens();
  const ta = $("#cv-txt"), f = $(".cv-f"), msg = $(".cv-fm"), orig = ta.value, texto = limpa(orig), modelo = f.elements.modelo.value;
  if (!texto.trim()) { msg.textContent = "Escreve uma mensagem."; return; }
  if ([...texto].length > TEXTO_MAX) { msg.textContent = `Mensagem longa demais (máx. ${TEXTO_MAX} caracteres).`; return; }
  S.aguarda = Date.now() + ENVIO_MS; S.desdeEnvio = S.desde;
  pintarTopo(); msg.textContent = "";
  const id = S.id, ate = criandoAte(id);
  if (ate) { // QA B3: a ponte ainda está a criar esta conversa; envia só quando os eventos derem 200
    pintar(msg, "a-processar", "a criar a conversa", ate - CRIAR_MS);
    const pronta = await esperarCriada(async () => (await obter(`api/conversa/eventos?${new URLSearchParams({ conversa: id, desde: S.desde })}`)).status, ate);
    if (id !== S.id) return;
    msg.textContent = "";
    if (!pronta) { S.aguarda = 0; msg.textContent = AINDA_A_CRIAR; A.tocar?.("erro"); return pintarTopo(); }
    if (S.criando?.id === id) S.criando = null;
  }
  const r = await enviarPedido("enviar", { conversa: S.id, texto, modo: f.elements.plano.checked ? "plano" : "normal", modelo, permissoes: S.est?.permissoes_config });
  // tira só o que foi enviado: uma transcrição que chegou entretanto (juntada no fim) fica na caixa
  if (r.status === 202) { if (ta.value.startsWith(orig)) { ta.value = ta.value.slice(orig.length).trimStart(); crescer(ta); } gravarModelo(S.id, modelo); sondar(); }
  else { S.aguarda = 0; msg.textContent = erroTxt(r); A.tocar?.("erro"); } // o texto fica na caixa
  pintarTopo();
}

async function parar(b) {
  if (criandoAte(S.id) > Date.now()) { $(".cv-fm").textContent = AINDA_A_CRIAR; return; } // R6: nada a parar numa conversa por criar
  b.disabled = true;
  const r = await enviarPedido("parar", { conversa: S.id });
  $(".cv-fm").textContent = r.status === 202 ? "A parar…" : erroTxt(r);
  if (r.status === 202) { S.parando = true; S.ate = Date.now() + ESPERA_MS; pintarTopo(); sondar(); } else b.disabled = false;
}

async function arquivar(b) {
  if (!S.id || !confirm("Arquivar esta conversa? Deixa de aceitar mensagens.")) return;
  b.disabled = true;
  const r = await enviarPedido("arquivar", { conversa: S.id });
  b.disabled = false;
  if (r.status === 202) return mostrarLista();
  $(".cv-fm").textContent = erroTxt(r);
}

async function nova(b) {
  b.disabled = true;
  const msg = $(".cv-lv").hidden ? $(".cv-fm") : $(".cv-lm");
  pintar(msg, "a-processar", "a criar a conversa", Date.now());
  const id = K.novoId(), r = await enviarPedido("nova", { conversa: id });
  b.disabled = false;
  if (r.status === 202) { msg.textContent = ""; S.criando = { id, ate: Date.now() + CRIAR_MS }; return abrirConversa(id).then(focoAoAbrir); }
  msg.textContent = erroTxt(r);
}

// api (do conversa_inicio.js) = { tocar(som, chave) }; a página (conversa.html) já tem o #cv-painel e o .cp-dlg; o resto vem
// de api/conversa/estado
export async function iniciar(api) {
  A = api;
  root = document.getElementById("cv-painel");
  dlg = document.querySelector(".cp-dlg");
  montar();
  await lerEstado();
  addEventListener("hashchange", rotaHash);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && S.id) sondar(); });
  if (rota(location.hash)) rotaHash(); else abrir();
}
