// Biblioteca do Office: módulo carregado com import() só ao abrir #biblioteca (como o music.js).
// As funções puras exportadas são testadas em node (tests/biblioteca_test.mjs); o DOM só se toca dentro de abrir()/rota()/sair().
// Regra de HTML: tudo o que vem do servidor entra por esc()/escU() ou md() (que escapa tudo); helpers «…H()» devolvem HTML já escapado.

// produtos = projectos da config + «geral»; fontes = rótulos da config. configurar(cfg) troca-os (abrir() chama-o)
export const produtosCfg = (cfg) => [...(cfg?.projectos || []).map((p) => [p.id, p.nome]), ["geral", "Geral"]];
export const fontesCfg = (cfg) => ({ ...Object.fromEntries((cfg?.biblioteca || []).map((f) => [f.id, f.rotulo])), tarefas: "tarefas" });
export let PRODUTOS = produtosCfg(null);
// ordem do que mais se consome (criativos, estudos): a 360 px vêem-se «Imagens · Vídeos · Estudos» sem scroll
export const TIPOS = [["imagens", "Imagens"], ["videos", "Vídeos"], ["estudos", "Estudos R&D"], ["textos", "Textos/Copy"],
  ["relatorios", "Relatórios"], ["planos", "Planos/Specs"], ["pareceres", "Pareceres jurídicos"], ["audio", "Áudio"],
  ["conhecimento", "Conhecimento"]];
export const DATAS = [["hoje", "hoje"], ["7d", "7 d"], ["30d", "30 d"], ["tudo", "tudo"]];
let P = new Map(PRODUTOS), FONTE = fontesCfg(null);
const T = new Map(TIPOS), D = new Map(DATAS);
export function configurar(cfg) { PRODUTOS = produtosCfg(cfg); P = new Map(PRODUTOS); FONTE = fontesCfg(cfg); }
export const FILTROS0 = Object.freeze({ produto: null, tipo: null, data: "tudo", q: "" });
const MEDIA = new Set(["imagem", "video", "audio"]), MEDIA_EXT = /\.(jpe?g|png|webp|gif|mp4|webm|m4v|mp3|wav|m4a|ogg|opus)$/i;
const ICONE = { imagens: "▣", videos: "▶", audio: "♪", estudos: "◆", planos: "▦", pareceres: "§", relatorios: "▥" };
export const ESTADOS = { aprovado: ["✓", "Aprovado", "ok"], executado: ["✓", "Aprovado · executado", "ok"],
  pendente: ["◌", "Pendente", "warn"], bloqueado: ["⊘", "Bloqueado", "bad"], recusado: ["✗", "Recusado", "rec"],
  sem_aprovacao: ["–", "Sem aprovação", "mut"] };
const FICHA = ["estado", "produto", "mercado", "criado", "decidido", "revisor", "juridico"];

// ---- funções puras

// «#biblioteca[/<seg>/<seg>…][?produto=&tipo=&data=&q=]» → { vista: "lista"|"item", id, filtros }; outro hash → null
export function parseRota(hash) {
  const h = String(hash ?? "").replace(/^#/, "");
  if (!h.startsWith("biblioteca")) return null;
  const i = h.indexOf("?"), path = h.slice(10, i < 0 ? undefined : i), p = new URLSearchParams(i < 0 ? "" : h.slice(i + 1));
  const f = { ...FILTROS0 };
  if (P.has(p.get("produto"))) f.produto = p.get("produto");
  if (T.has(p.get("tipo"))) f.tipo = p.get("tipo");
  if (D.has(p.get("data"))) f.data = p.get("data");
  f.q = (p.get("q") || "").trim().slice(0, 100);
  const segs = path.split("/").filter(Boolean);
  if (!segs.length || !path.startsWith("/")) return { vista: "lista", id: null, filtros: f };
  try { return { vista: "item", id: segs.map(decodeURIComponent).join("/"), filtros: f }; }
  catch { return { vista: "lista", id: null, filtros: f }; } // %-codificação inválida
}

// inverso de parseRota: cada segmento com encodeURIComponent, «/» literal; filtros por omissão não entram
export function rotaHash(r = {}) {
  if (r.vista === "item" && r.id) return "#biblioteca/" + String(r.id).split("/").map(encodeURIComponent).join("/");
  const f = { ...FILTROS0, ...r.filtros }, p = new URLSearchParams();
  if (f.produto) p.set("produto", f.produto);
  if (f.tipo) p.set("tipo", f.tipo);
  if (f.data && f.data !== "tudo") p.set("data", f.data);
  if (f.q) p.set("q", f.q);
  const s = p.toString();
  return "#biblioteca" + (s ? "?" + s : "");
}

// sem acentos nem maiúsculas, espaços simples
export const normaliza = (s) => String(s ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();

const pesquisavel = (it) => normaliza([it.titulo, it.sub, it.q, ...(it.produtos || []).map((k) => P.get(k) || k)].join(" "));
function passaData(it, data, agora) {
  const t = (Number(it.actualizado) || 0) * 1000;
  if (data === "hoje") return new Date(t).toDateString() === new Date(agora).toDateString();
  if (data === "7d" || data === "30d") return agora - t <= (data === "7d" ? 7 : 30) * 864e5;
  return true;
}
// itens que passam os filtros; «salta» ignora um grupo (para as contagens facetadas)
export function filtra(itens, f = FILTROS0, agora = Date.now(), salta = null) {
  const w = normaliza(f.q).split(" ").filter(Boolean);
  return (itens || []).filter((it) => (salta === "produto" || !f.produto || (it.produtos || []).includes(f.produto))
    && (salta === "tipo" || !f.tipo || (it.tipos || []).includes(f.tipo))
    && passaData(it, f.data, agora)
    && (!w.length || ((h) => w.every((x) => h.includes(x)))(pesquisavel(it))));
}
// contagem de cada chip = itens que passam os OUTROS filtros (o próprio grupo não conta); «todos» = total sem o grupo
export function contagens(itens, f = FILTROS0, agora = Date.now()) {
  const conta = (grupo, lista, campo) => {
    const base = filtra(itens, f, agora, grupo), o = { todos: base.length };
    for (const [k] of lista) o[k] = 0;
    for (const it of base) for (const k of new Set(it[campo] || [])) if (Object.hasOwn(o, k) && k !== "todos") o[k]++;
    return o;
  };
  return { produto: conta("produto", PRODUTOS, "produtos"), tipo: conta("tipo", TIPOS, "tipos") };
}

// tamanhos PT-PT: «985 KB», «1,7 MB», «23 MB»
export function fmtBytes(n) {
  n = Math.max(0, Math.round(Number(n) || 0));
  if (n < 1024) return `${n} B`;
  if (n < 1023.5 * 1024) return `${Math.round(n / 1024)} KB`;
  const [v, u] = n < 1023.95 * 1024 ** 2 ? [n / 1024 ** 2, "MB"] : [n / 1024 ** 3, "GB"];
  return `${v < 9.95 ? v.toFixed(1).replace(".", ",") : Math.round(v)} ${u}`;
}
const pad = (n) => String(n).padStart(2, "0");
// epoch em segundos → hora local do browser
export const fmtData = (s) => { const d = new Date(Number(s) * 1000); return `${d.getDate()}/${d.getMonth() + 1}/${d.getFullYear()}`; };
export const fmtDataHora = (s) => { const d = new Date(Number(s) * 1000); return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
export const fmtDur = (s) => { s = Math.round(Number(s) || 0); return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`; };
export const versao = (nome) => { const m = /-v(\d+)(?=[-_.])/i.exec(String(nome ?? "")); return m ? "v" + m[1] : null; };
// «status-01-quem-e-loja-9x16.jpg» → «status 01 quem e loja 9x16»
export const humaniza = (nome) => String(nome ?? "").replace(/\.[^.\/]+$/, "").replace(/[-_]+/g, " ").trim();

// frontmatter YAML simples no topo («---» … «---»): só pares «chave: valor» de 1 linha; o corpo sai sem ele
export function semFrontmatter(txt) {
  const s = String(txt ?? "").replace(/^﻿/, "");
  const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/.exec(s);
  if (!m) return { front: {}, corpo: s };
  const front = {};
  for (const l of m[1].split(/\r?\n/)) {
    const k = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(l);
    if (k && k[1] !== "__proto__") front[k[1].toLowerCase()] = k[2].trim().replace(/^([\x22\x27])(.*)\1$/, "$2");
  }
  return { front, corpo: s.slice(m[0].length).replace(/^(?:[ \t]*\r?\n)+/, "") };
}
// «Ficha»: só chaves conhecidas, por esta ordem, sem valores vazios
export const ficha = (front) => FICHA.filter((k) => Object.hasOwn(front || {}, k) && String(front[k]).trim()).map((k) => [k, String(front[k])]);

// «5 imagens, 3 vídeos · 17 MB» / «1 documento»
const pl = (n, um, muitos) => `${n} ${n === 1 ? um : muitos}`;
export function conteudo(it) {
  const c = it.conta || {}, out = [];
  if (c.imagem) out.push(pl(c.imagem, "imagem", "imagens"));
  if (c.video) out.push(pl(c.video, "vídeo", "vídeos"));
  if (c.audio) out.push(pl(c.audio, "áudio", "áudios"));
  if (c.doc) out.push(pl(c.doc, "documento", "documentos"));
  const media = c.imagem || c.video || c.audio;
  return out.join(", ") + (media && it.bytes ? ` · ${fmtBytes(it.bytes)}` : "");
}
// «5 aprovadas · 8 fora do pedido» (peças que o pedido não cita: factual, não parece um chumbo)
export function resumoEstado(r) {
  if (!r) return "";
  const n = (k) => Number(r[k]) || 0, out = [], ap = n("aprovado") + n("executado");
  if (ap) out.push(pl(ap, "aprovada", "aprovadas"));
  if (n("pendente")) out.push(pl(n("pendente"), "pendente", "pendentes"));
  if (n("bloqueado")) out.push(pl(n("bloqueado"), "bloqueada", "bloqueadas"));
  if (n("recusado")) out.push(pl(n("recusado"), "recusada", "recusadas"));
  if (n("sem_aprovacao")) out.push(`${n("sem_aprovacao")} fora do pedido`);
  return out.join(" · ");
}
// distribui os ficheiros do item pelas secções do UX (rótulo dado pelo servidor)
export function seccoes(d) {
  const s = { media: [], docs: [], textos: [], revisao: [], folha: [], pedidos: d?.pedidos || [], trabalho: 0 };
  for (const f of d?.destaque || []) (MEDIA.has(f.tipo) ? s.media : s.docs).push(f);
  for (const f of d?.textos || []) {
    const r = f.rotulo;
    (r === "Folha" ? s.folha : r === "Revisão" || r === "Parecer jurídico" ? s.revisao : ["Spec", "Plano", "QA"].includes(r) ? s.docs : s.textos).push(f);
  }
  for (const g of d?.trabalho || []) s.trabalho += (g.ficheiros || []).length;
  return s;
}
// fila «Media recente» do início: lotes e pastas com imagens/vídeos, mais recentes primeiro
export function mediaRecente(itens, n = 6) {
  return (itens || []).filter((it) => it.genero === "lote" || (it.genero === "pasta" && (it.conta?.imagem || it.conta?.video)))
    .sort((a, b) => (Number(b.actualizado) || 0) - (Number(a.actualizado) || 0)).slice(0, n);
}
// item de 1 só documento: o cartão abre logo o leitor
export const umDoc = (it) => it?.n === 1 && !!it.principal;
// só URLs da própria API (relativos); qualquer outra coisa vira ""
export const safeU = (u) => (/^api\/biblioteca\/[^\s\x22\x27<>\\]*$/.test(String(u ?? "")) ? String(u) : "");
const dlDe = (u) => (u ? u + (u.includes("?") ? "&" : "?") + "dl=1" : "");

// ---- DOM (só depois de abrir())

let A = null, esc = (s) => String(s ?? "");
const S = { itens: null, carregado: 0, f: { ...FILTROS0 }, limite: 20, seq: 0, lseq: 0, cur: null, vista: null, scroll: 0,
  focoId: null, focoRec: false, refrescar: false, chips: {} };
const V = { lista: [], i: 0, pushed: false, prog: false, de: null };
const q = (s) => A.root.querySelector(s);
const escU = (u) => esc(safeU(u));
const desktop = () => matchMedia("(min-width: 900px)").matches;

const SHELL = `<div class="bib-lista">
<div class="bib-top"><button type="button" class="bib-btn" data-a="escritorio">‹ Escritório</button><h2 id="bib-h" tabindex="-1">Biblioteca</h2>
<button type="button" class="bib-btn" data-a="actualizar">↻ Actualizar</button></div>
<p class="bib-off" role="status" hidden></p>
<section class="bib-rec" aria-labelledby="bib-rech" hidden><h3 id="bib-rech">Media recente</h3><ul class="bib-recl" role="list"></ul></section>
<div class="bib-lay"><div class="bib-side">
<input class="bib-q" type="search" maxlength="100" placeholder="pesquisar na biblioteca" aria-label="Pesquisar na biblioteca por título, nome do ficheiro ou produto" autocomplete="off">
<div class="bib-g"><span class="bib-gl" aria-hidden="true">Produto</span><div class="bib-chips" role="group" aria-label="Produto" data-g="produto"></div></div>
<div class="bib-g"><span class="bib-gl" aria-hidden="true">Tipo</span><div class="bib-chips" role="group" aria-label="Tipo" data-g="tipo"></div></div>
<div class="bib-g"><span class="bib-gl" aria-hidden="true">Data</span><div class="bib-chips bib-datas" role="group" aria-label="Data" data-g="data"></div></div>
</div><div class="bib-main"><p class="bib-cnt" aria-live="polite"></p><ul class="bib-cards"></ul><div class="bib-vazio"></div>
<button type="button" class="bib-btn bib-mais" data-a="mais" hidden></button></div></div></div>
<div class="bib-item" hidden></div>
<div class="bib-ler" hidden></div>
<p class="bib-sr" role="status" aria-live="polite"></p>
<dialog class="bib-v" aria-labelledby="bib-vh"><div class="bib-vt"><p id="bib-vh" aria-live="polite"></p><button type="button" class="bib-btn" data-vn="x">✕ Fechar</button></div>
<div class="bib-vs"></div>
<div class="bib-vb"><button type="button" class="bib-btn" data-vn="-1">‹ Anterior</button><a class="bib-btn bib-vdl" download></a><button type="button" class="bib-btn" data-vn="1">Seguinte ›</button></div>
<p class="bib-vm"></p></dialog>`;

const SKEL = `<li class="bib-card bib-sk" aria-hidden="true"><i></i><i></i><i></i></li>`.repeat(6);

function css() {
  if (document.querySelector("link[data-bib]")) return Promise.resolve();
  return new Promise((ok) => {
    const l = document.createElement("link");
    Object.assign(l, { rel: "stylesheet", href: new URL("./biblioteca.css", import.meta.url).href, onload: ok, onerror: ok });
    l.dataset.bib = "";
    document.head.append(l);
    setTimeout(ok, 3000); // rede lenta: não fica preso à espera do CSS
  });
}

// api = { root, md, esc, cfg (GET api/config), getState, verAprovacao(fonte, ficheiro), fechar() }
export async function abrir(api) {
  A = api; esc = api.esc;
  configurar(api.cfg);
  await css();
  A.root.innerHTML = SHELL;
  for (const [g, lista] of [["produto", PRODUTOS], ["tipo", TIPOS], ["data", DATAS]]) {
    const box = q(`[data-g="${g}"]`);
    for (const [k, nome] of g === "data" ? lista : [["todos", "Todos"], ...lista]) {
      const b = document.createElement("button");
      Object.assign(b, { type: "button", textContent: nome });
      b.dataset.k = k; b.dataset.nome = nome;
      box.append(b);
      (S.chips[g] ||= []).push(b);
    }
  }
  A.root.addEventListener("click", clique);
  A.root.addEventListener("error", imgFalhou, true);
  let t = 0;
  q(".bib-q").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => mudaFiltro({ q: e.target.value.trim().slice(0, 100) }), 150);
  });
  const dlg = q(".bib-v");
  dlg.addEventListener("close", verFechou);
  dlg.addEventListener("keydown", (e) => {
    if (/^(VIDEO|AUDIO|INPUT)$/.test(e.target.tagName)) return;
    const n = V.lista.length, k = { ArrowLeft: V.i - 1, ArrowRight: V.i + 1, Home: 0, End: n - 1 }[e.key];
    if (k === undefined) return;
    e.preventDefault();
    verIr(k);
  });
  let x0 = null;
  const st = q(".bib-vs");
  st.addEventListener("touchstart", (e) => { // nos controlos do vídeo/áudio (barra de progresso, volume) arrastar não muda de peça
    x0 = e.touches.length === 1 && !e.target.closest?.("video,audio") ? [e.touches[0].clientX, e.touches[0].clientY] : null;
  }, { passive: true });
  st.addEventListener("touchend", (e) => {
    if (!x0 || e.touches.length || (window.visualViewport?.scale || 1) > 1.05) return (x0 = null); // pinch/zoom: não muda de peça
    const t1 = e.changedTouches[0], dx = t1.clientX - x0[0], dy = t1.clientY - x0[1];
    x0 = null;
    if (Math.abs(dx) >= 40 && Math.abs(dx) > Math.abs(dy)) verIr(V.i + (dx < 0 ? 1 : -1));
  });
}

// o index.html chama ao sair da Biblioteca: fecha o visualizador sem mexer no histórico; na próxima entrada a lista recarrega
export function sair() {
  const d = A && q(".bib-v");
  if (d?.open) { V.prog = true; d.close(); }
  S.refrescar = true;
  S.cur = S.rend = null;
}

function mostrar(v) {
  const mudou = S.vista !== v;
  if (mudou && S.vista === "lista") S.scroll = scrollY;
  S.vista = v;
  q(".bib-lista").hidden = v !== "lista";
  q(".bib-item").hidden = v !== "item";
  q(".bib-ler").hidden = v !== "ler";
  if (!mudou) return;
  if (v === "lista") {
    scrollTo(0, S.scroll);
    const a = [...A.root.querySelectorAll(S.focoRec ? ".bib-rc" : ".bib-card .bib-a")].find((x) => x.dataset.id === S.focoId);
    a?.focus({ preventScroll: true });
  } else scrollTo(0, 0);
}

const ir = (hash) => { history.pushState({ bib: 1 }, "", hash); rota(hash); };
const voltar = () => (history.state?.bib ? history.back() : ir(rotaHash({ vista: "lista", filtros: S.f })));

// chamado pelo index.html em cada hashchange enquanto o hash começa por «biblioteca»
export async function rota(hash) {
  const r = parseRota(hash) || { vista: "lista", id: null, filtros: { ...FILTROS0 } }, n = ++S.seq;
  const dlg = q(".bib-v");
  if (r.vista === "lista") {
    if (dlg.open) { V.prog = true; dlg.close(); }
    S.f = r.filtros;
    S.cur = S.rend = null; // o item só se reaproveita dentro dele (visualizador, leitor); da lista abre-se sempre de novo
    if (q(".bib-q").value.trim() !== S.f.q) q(".bib-q").value = S.f.q;
    if (S.itens && !S.refrescar && S.pintado !== rotaHash({ filtros: S.f })) pintarLista(); // antes de mostrar: o foco volta ao cartão
    mostrar("lista");
    if (!S.itens || S.refrescar) { S.refrescar = false; await carregar(); }
    return;
  }
  const a = S.cur && acha(S.cur, r.id);
  if (a) return abrirAlvo(a);
  if (dlg.open) { V.prog = true; dlg.close(); }
  const it = (S.itens || []).find((x) => x.id === r.id);
  mostrar(it && umDoc(it) ? "ler" : "item");
  q(it && umDoc(it) ? ".bib-ler" : ".bib-item").innerHTML = aCarregarH(it);
  S.focoId = r.id;
  if (/^aprovacoes-[^/]+\/[^/]+\.md$/.test(r.id)) { // pedido de aprovação: não é item, abre-se directamente pelo /f/
    const pi = (S.itens || []).find((x) => x.pedido && `aprovacoes-${x.pedido.fonte}/${x.pedido.ficheiro}` === r.id); // URL do índice (com ?v=)
    const url = pi?.pedido.url || "api/biblioteca/f/" + r.id.split("/").map(encodeURIComponent).join("/"); // link directo sem índice
    return ler({ caminho: r.id, nome: r.id.split("/")[1], ext: "md", tipo: "doc", url, dl: dlDe(url) });
  }
  try {
    const res = await fetch("api/biblioteca/item?id=" + encodeURIComponent(r.id), { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();
    if (n !== S.seq) return;
    S.cur = d; S.rend = null;
    abrirAlvo(acha(d, r.id) || acha(d, d.foco) || { k: "item" });
  } catch (e) {
    if (n !== S.seq) return;
    mostrar("item");
    q(".bib-item").innerHTML = barraH() + `<p class="bib-msg">${esc(e.message !== "404" ? "Não foi possível carregar a biblioteca." : `Este ${/\.(md|txt|csv|pdf)$/i.test(r.id) ? "documento" : MEDIA_EXT.test(r.id) ? "ficheiro" : "lote"} já não está na biblioteca.`)}</p>`;
  }
}

// o que abre um id/caminho dentro do item já carregado
function acha(d, id) {
  if (!id) return null;
  if (id === d.item.id) return { k: "item" };
  const s = seccoes(d), i = s.media.findIndex((f) => f.caminho === id);
  if (i >= 0) return { k: "ver", i };
  const todos = [...(d.destaque || []), ...(d.textos || []), ...(d.trabalho || []).flatMap((g) => g.ficheiros || [])];
  const f = todos.find((x) => x.caminho === id);
  if (f) return MEDIA.has(f.tipo) || MEDIA_EXT.test(f.nome || id) ? { k: "item" } : { k: "ler", f }; // media de trabalho: nunca como texto
  const p = (d.pedidos || []).find((x) => `aprovacoes-${x.fonte}/${x.ficheiro}` === id);
  return p ? { k: "ler", f: { caminho: id, nome: p.ficheiro, titulo: p.titulo, ext: "md", tipo: "doc", url: p.url, dl: dlDe(p.url) } } : null;
}

function abrirAlvo(a) {
  const d = S.cur, dlg = q(".bib-v");
  if (a.k === "ler") { if (dlg.open) { V.prog = true; dlg.close(); } return ler(a.f); }
  if (a.k === "item" && d && umDoc(d.item)) {
    const f = [...(d.destaque || []), ...(d.textos || [])][0];
    if (f) return ler({ ...f, titulo: d.item.titulo });
  }
  const novo = S.vista !== "item" || S.rend !== d.item.id;
  if (S.rend !== d.item.id) { q(".bib-item").innerHTML = itemH(d); S.rend = d.item.id; V.lista = seccoes(d).media; }
  mostrar("item");
  if (a.k === "ver") { if (!dlg.open) verAbrir(a.i, false); else verIr(a.i); }
  else {
    if (dlg.open) { V.prog = true; dlg.close(); }
    if (novo) q(".bib-item h2")?.focus({ preventScroll: true });
  }
}

// ---- lista

async function carregar() {
  const n = ++S.lseq;
  S.cur = S.rend = null;
  if (!S.itens) { q(".bib-cards").innerHTML = SKEL; q(".bib-cnt").textContent = "A carregar…"; q(".bib-vazio").textContent = ""; }
  try {
    const r = await fetch("api/biblioteca", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    if (n !== S.lseq) return;
    S.itens = Array.isArray(d.itens) ? d.itens : [];
    S.carregado = Date.now();
    q(".bib-off").hidden = true;
  } catch {
    if (n !== S.lseq) return;
    if (S.itens) {
      const min = Math.round((Date.now() - S.carregado) / 6e4);
      q(".bib-off").textContent = `Sem ligação. Mostramos a última lista que carregou (lista de há ${min} min).`;
      q(".bib-off").hidden = false;
    } else {
      q(".bib-cards").innerHTML = "";
      q(".bib-cnt").textContent = "";
      q(".bib-vazio").innerHTML = `<p class="bib-msg">Não foi possível carregar a biblioteca.</p><button type="button" class="bib-btn" data-a="actualizar">Tentar de novo</button>`;
      return;
    }
  }
  pintarLista();
}

function pintarChips(c) {
  for (const g of ["produto", "tipo"]) for (const b of S.chips[g]) {
    const k = b.dataset.k, on = k === "todos" ? !S.f[g] : S.f[g] === k, n = c[g][k] ?? 0;
    b.textContent = `${b.dataset.nome} (${n})`;
    b.setAttribute("aria-pressed", on);
    b.disabled = !on && n === 0;
  }
  for (const b of S.chips.data) b.setAttribute("aria-pressed", b.dataset.k === S.f.data);
}

function pintarLista() {
  if (!S.itens) return;
  const agora = Date.now(), vis = filtra(S.itens, S.f, agora);
  S.pintado = rotaHash({ filtros: S.f });
  pintarChips(contagens(S.itens, S.f, agora));
  q(".bib-cnt").textContent = vis.length === 1 ? "1 resultado" : `${vis.length} resultados`;
  const rec = S.pintado === "#biblioteca" ? mediaRecente(S.itens) : []; // só no início, sem filtros nem pesquisa
  q(".bib-rec").hidden = !rec.length;
  q(".bib-recl").innerHTML = recentesH(rec);
  q(".bib-cards").innerHTML = vis.slice(0, S.limite).map(cartaoH).join("");
  const resto = vis.length - S.limite, mais = q(".bib-mais");
  mais.hidden = resto <= 0;
  mais.textContent = `Mostrar mais (${Math.max(0, resto)})`;
  q(".bib-vazio").innerHTML = vis.length ? "" : vazioH();
}

function vazioH() {
  if (!S.itens.length) return `<p class="bib-msg">Ainda não há nada na biblioteca.</p>`;
  const outros = S.f.produto || S.f.tipo || S.f.data !== "tudo";
  const limpaF = `<button type="button" class="bib-btn" data-a="limpar-f">Limpar filtros</button>`;
  if (!S.f.q) return `<p class="bib-msg">Nada com estes filtros.</p>${limpaF}`;
  return `<p class="bib-msg">${esc(`Nada para «${S.f.q}»${S.f.tipo ? " em " + T.get(S.f.tipo) : ""}.`)}</p>`
    + `<button type="button" class="bib-btn" data-a="limpar-q">Limpar pesquisa</button>${outros ? limpaF : ""}`;
}

function mudaFiltro(m) {
  S.f = { ...S.f, ...m };
  S.limite = 20;
  history.replaceState(history.state, "", rotaHash({ vista: "lista", filtros: S.f }));
  pintarLista();
}

const estadoH = (e, peq) => {
  const x = ESTADOS[e];
  return x ? `<span class="bib-est bib-e-${esc(x[2])}${peq ? " bib-peq" : ""}"><span aria-hidden="true">${esc(x[0])}</span> ${esc(x[1])}</span>` : "";
};
const novoH = (it) => (it.novo ? `<span class="bib-novo">Novo</span>` : "");
const fonteDe = (raiz) => FONTE[raiz] || (String(raiz).startsWith("aprovacoes-") ? "aprovações" : raiz);
const produtosDe = (it) => (it.produtos || []).map((k) => P.get(k) || k).join(", ");
function pedidoH(it) {
  const p = it.pedido;
  if (!p) return "";
  const aberto = (A.getState?.()?.aprovacoes || []).some((x) => x.fonte === p.fonte && x.ficheiro === p.ficheiro);
  return aberto ? `<button type="button" class="bib-lnk" data-a="aprov" data-fonte="${esc(p.fonte)}" data-fich="${esc(p.ficheiro)}">Ver em Aprovações</button>`
    : `<a class="bib-lnk" href="${esc(rotaHash({ vista: "item", id: `aprovacoes-${p.fonte}/${p.ficheiro}` }))}">Ver pedido ›</a>`;
}

// «· Ver pedido ›» sem quebra: o «·» muda de linha junto com o link
const pedNwH = (it) => (it.pedido ? `<span class="bib-nw">· ${pedidoH(it)}</span>` : "");

function cartaoH(it) {
  const um = umDoc(it), lote = it.genero === "lote" || it.genero === "pasta", href = esc(rotaHash({ vista: "item", id: it.id }));
  const tipoL = lote ? "" : T.get((it.tipos || [])[0]) || "";
  const metaH = [estadoH(it.estado), esc(tipoL), esc(fmtData(it.actualizado)), esc(conteudo(it))].filter(Boolean).join(" · ");
  const minisH = (it.minis || []).length ? `<div class="bib-minis" aria-hidden="true">${it.minis.map((u) => `<img src="${escU(u)}" alt="" width="72" height="128" loading="lazy" decoding="async">`).join("")}`
    + (it.mais > 0 ? `<span class="bib-maisn">+${esc(it.mais)}</span>` : "") + `</div>`
    : it.conta?.video ? `<div class="bib-minis" aria-hidden="true"><span class="bib-tile">▶</span></div>` : "";
  const pr = it.principal, pdf = um && pr?.ext === "pdf";
  const priH = pdf ? `<a class="bib-btn bib-pri" href="${escU(pr.url)}" target="_blank" rel="noopener">Abrir PDF ›</a>`
    : `<a class="bib-btn bib-pri" href="${href}">${esc(um ? "Ler" : lote ? "Abrir lote" : "Abrir")}</a>`;
  const dlH = it.zip ? `<a class="bib-btn" href="${esc("api/biblioteca/zip?id=" + encodeURIComponent(it.id))}" download data-zip>⤓ Descarregar tudo (zip)</a>`
    : pr?.grande ? `<button type="button" class="bib-btn" disabled>⤓ Descarregar</button>`
    : pr?.dl ? `<a class="bib-btn" href="${escU(pr.dl)}" download>⤓ Descarregar</a>` : "";
  return `<li><article class="bib-card"><p class="bib-src"><span aria-hidden="true">${esc(ICONE[(it.tipos || [])[0]] || "▤")}</span> ${esc(fonteDe(it.raiz))}`
    + `${esc(produtosDe(it) ? " · " + produtosDe(it) : "")} ${novoH(it)}</p>`
    + `<h3><a class="bib-a" href="${href}" data-id="${esc(it.id)}">${esc(it.titulo || it.sub || it.id)}</a></h3>`
    + `<p class="bib-meta">${metaH} ${pedNwH(it)}</p>`
    + (it.resumo_estado ? `<p class="sub">${esc(resumoEstado(it.resumo_estado))}</p>` : "")
    + (it.resumo ? `<p class="bib-res">${esc(it.resumo)}</p>` : "")
    + `${minisH}<div class="bib-acts">${priH}${dlH}</div></article></li>`;
}

// cartão compacto da fila: o cartão inteiro é o link (1 toque abre o lote)
const recentesH = (l) => l.map((it) => {
  const u = (it.minis || [])[0];
  const thH = u ? `<img src="${escU(u)}" alt="" width="54" height="96" loading="lazy" decoding="async">`
    : `<span class="bib-tile">${it.conta?.video ? "▶" : "▣"}</span>`;
  return `<li><a class="bib-rc" href="${esc(rotaHash({ vista: "item", id: it.id }))}" data-id="${esc(it.id)}"><span class="bib-rt" aria-hidden="true">${thH}</span>`
    + `<span class="bib-rb"><span class="bib-clamp">${esc(it.titulo || it.sub || it.id)}</span>${estadoH(it.estado, true)} ${novoH(it)}</span></a></li>`;
}).join("");

// ---- item (lote, pasta, tarefa, documento com revisões)

const barraH = () => `<div class="bib-bar"><button type="button" class="bib-btn" data-a="voltar">‹ Biblioteca</button></div>`;
function aCarregarH(it) {
  return barraH() + (it ? `<p class="sub">${esc(fonteDe(it.raiz))}</p><h2 tabindex="-1">${esc(it.titulo)}</h2>` : "")
    + `<p class="bib-msg" role="status">A carregar…</p><ul class="bib-grid bib-sk" aria-hidden="true"><li><i></i></li><li><i></i></li><li><i></i></li><li><i></i></li></ul>`;
}
const tamH = (f) => `<span class="sub">${esc(fmtBytes(f.bytes))}</span>`;
function dlBtnH(f) {
  if (f.grande) return `<button type="button" class="bib-dl" disabled aria-label="Descarregar (ficheiro grande demais)">⤓</button>`;
  return `<a class="bib-dl" href="${escU(f.dl)}" download aria-label="${esc(`Descarregar ${f.nome} · ${fmtBytes(f.bytes)}`)}">⤓</a>`;
}
function lerBtnH(f) {
  if (f.grande) return "";
  if (f.tipo === "pdf" || f.ext === "pdf") return `<a class="bib-btn" href="${escU(f.url)}" target="_blank" rel="noopener">Abrir PDF ›</a>`;
  if (MEDIA.has(f.tipo)) return `<a class="bib-btn" href="${escU(f.url)}" target="_blank" rel="noopener">Abrir</a>`;
  return `<a class="bib-btn" href="${esc(rotaHash({ vista: "item", id: f.caminho }))}">Ler</a>`;
}
const docH = (f, rot, data) => `<li class="bib-doc"><p><span class="bib-rot">${esc(rot || f.rotulo || "Texto")}</span> <span class="bib-nm" title="${esc(f.nome)}">${esc(f.nome)}</span></p>`
  + `<p class="bib-row">${data ? `<span class="sub">${esc(fmtDataHora(f.mtime))}</span>` : ""}${f.grande ? `<span class="sub">ficheiro grande demais</span>` : tamH(f)}`
  + `<span class="bib-acts">${lerBtnH(f)}${dlBtnH(f)}</span></p></li>`;
const pedidoDocH = (p) => {
  const f = { caminho: `aprovacoes-${p.fonte}/${p.ficheiro}`, nome: p.ficheiro, url: p.url, dl: dlDe(p.url), tipo: "doc" };
  return `<li class="bib-doc"><p><span class="bib-rot">Pedido de aprovação</span> ${estadoH(p.estado, true)} <span class="bib-nm">${esc(p.titulo || p.ficheiro)}</span></p>`
    + `<p class="bib-row"><span class="sub">${esc(p.criado || "")}</span><span class="bib-acts">${lerBtnH(f)}${dlBtnH(f)}</span></p></li>`;
};
const secH = (titulo, corpoH, aberto) => `<details${aberto ? " open" : ""}><summary>${esc(titulo)}</summary>${corpoH}</details>`;

function pecaH(f, i) {
  const v = versao(f.nome), nome = humaniza(f.nome);
  const thH = f.tipo === "imagem" ? `<img src="${escU(f.mini || f.url)}" alt="${esc(nome)}" width="180" height="320" loading="lazy" decoding="async">`
    : f.tipo === "video" ? (f.mini ? `<img src="${escU(f.mini)}" alt="" width="180" height="320" loading="lazy" decoding="async">` : "") + `<span class="bib-tile">▶ Vídeo</span>`
    : `<span class="bib-tile">♪ Áudio</span>`;
  const lab = f.tipo === "imagem" ? "" : ` aria-label="${esc(`${nome} (${f.tipo === "video" ? "vídeo" : "áudio"})`)}"`;
  return `<li><button type="button" class="bib-th" data-v="${esc(i)}"${lab}>${thH}${v ? `<span class="bib-ver">${esc(v)}</span>` : ""}</button>`
    + `${f.estado !== "sem_aprovacao" ? estadoH(f.estado, true) : ""}<p class="bib-cap" title="${esc(f.nome)}">${esc(nome)}</p><p class="bib-row">${tamH(f)}${dlBtnH(f)}</p></li>`;
}

function itemH(d) {
  const it = d.item, s = seccoes(d), dk = desktop();
  const linhaH = [estadoH(it.estado), esc(resumoEstado(it.resumo_estado))].filter(Boolean).join(" · ") + " " + pedNwH(it);
  let h = barraH() + `<div class="bib-ih"><p class="sub">${esc([fonteDe(it.raiz), produtosDe(it), fmtData(it.actualizado)].filter(Boolean).join(" · "))} ${novoH(it)}</p>`
    + `<h2 tabindex="-1">${esc(it.titulo || it.sub)}</h2>${it.sub && it.sub !== it.titulo ? `<p class="sub">${esc(it.sub)}</p>` : ""}`
    + (linhaH.trim() ? `<p class="bib-meta">${linhaH}</p>` : "")
    + (it.zip ? `<a class="bib-btn bib-pri bib-zip" href="${esc("api/biblioteca/zip?id=" + encodeURIComponent(it.id))}" download data-zip>${esc(`⤓ Descarregar tudo (zip) · ${fmtBytes(it.zip.bytes)}`)}</a>` : "")
    + `</div><div class="bib-cols">`;
  if (s.media.length) h += `<section class="bib-pecas"><h3>${esc(`Peças finais (${s.media.length})`)}</h3><ul class="bib-grid">${s.media.map(pecaH).join("")}</ul></section>`;
  let secsH = "";
  if (s.docs.length) secsH += secH(`Documentos (${s.docs.length})`, `<ul>${s.docs.map((f) => docH(f)).join("")}</ul>`, true);
  const nt = s.textos.length + s.pedidos.length;
  if (nt) secsH += secH(`Textos e legendas (${nt})`, `<ul>${s.textos.map((f) => docH(f)).join("")}${s.pedidos.map(pedidoDocH).join("")}</ul>`, dk);
  if (s.revisao.length) secsH += secH(`Revisão e parecer jurídico (${s.revisao.length})`, `<ul>${s.revisao.map((f) => docH(f)).join("")}</ul>`, false);
  if (s.folha.length) secsH += secH("Folha do lote", `<ul>${s.folha.map((f) => docH(f, "Folha")).join("")}</ul>`, false);
  const so = !s.media.length && !secsH;
  if (s.trabalho) secsH += secH(`Ficheiros de trabalho (${s.trabalho})`, (d.trabalho || []).map((g) =>
    `<h4>${esc(g.pasta || "outros")}</h4><ul>${(g.ficheiros || []).map((f) => docH(f, g.pasta ? "" : "Trabalho", true)).join("")}</ul>`).join(""), so);
  if (so) secsH = `<p class="bib-msg">Este lote só tem ficheiros de trabalho.</p>` + secsH;
  return h + `<div class="bib-secs">${secsH}</div></div>`;
}

// ---- visualizador de ecrã inteiro (<dialog>)

function verAbrir(i, push) {
  const dlg = q(".bib-v");
  V.de = document.activeElement?.closest?.(".bib-th") || null;
  V.pushed = push;
  if (push) history.pushState({ bib: 1 }, "", rotaHash({ vista: "item", id: V.lista[i].caminho }));
  dlg.showModal();
  verIr(i);
}
function verIr(i) {
  if (!V.lista.length || i < 0 || i >= V.lista.length) return;
  V.i = i;
  verPinta();
  history.replaceState(history.state, "", rotaHash({ vista: "item", id: V.lista[i].caminho }));
}
function verPinta() {
  const f = V.lista[V.i], n = V.lista.length, st = q(".bib-vs"), meta = q(".bib-vm"), est = f.estado !== "sem_aprovacao" && ESTADOS[f.estado];
  q("#bib-vh").textContent = `${V.i + 1} de ${n} · ${humaniza(f.nome)}`;
  q('[data-vn="-1"]').disabled = V.i === 0;
  q('[data-vn="1"]').disabled = V.i === n - 1;
  const dl = q(".bib-vdl");
  dl.href = safeU(f.dl);
  dl.textContent = `⤓ Descarregar · ${fmtBytes(f.bytes)}`;
  const base = [versao(f.nome) || "v1"], fim = est ? [`${est[0]} ${est[1]}`] : [];
  meta.textContent = [...base, ...fim].join(" · ");
  const falhou = () => {
    const p = document.createElement("p"), b = document.createElement("button"), a = document.createElement("a");
    p.textContent = "Não foi possível abrir este ficheiro.";
    Object.assign(b, { type: "button", className: "bib-btn", textContent: "Tentar de novo", onclick: verPinta });
    Object.assign(a, { className: "bib-btn", href: safeU(f.dl), textContent: "⤓ Descarregar" });
    a.setAttribute("download", "");
    const box = document.createElement("div");
    box.className = "bib-verr";
    box.append(p, b, a);
    st.replaceChildren(box);
  };
  let el;
  if (f.tipo === "imagem") {
    el = document.createElement("img");
    el.alt = humaniza(f.nome);
    el.onload = () => (meta.textContent = [...base, `${el.naturalWidth}×${el.naturalHeight}`, ...fim].join(" · "));
    el.onerror = falhou;
    el.src = safeU(f.url);
  } else {
    el = document.createElement(f.tipo === "video" ? "video" : "audio");
    Object.assign(el, { controls: true, preload: "metadata" });
    el.setAttribute("aria-label", humaniza(f.nome));
    if (f.tipo === "video") { el.playsInline = true; el.setAttribute("playsinline", ""); if (f.mini) el.poster = safeU(f.mini); }
    el.onloadedmetadata = () => (meta.textContent = [...base, fmtDur(el.duration), ...(el.videoWidth ? [`${el.videoWidth}×${el.videoHeight}`] : []), ...fim].join(" · "));
    el.onerror = falhou;
    el.src = safeU(f.url);
    if (f.tipo === "audio") { const w = document.createElement("div"), p = document.createElement("p"); p.textContent = f.nome; w.className = "bib-aud"; w.append(p, el); el = w; }
  }
  st.replaceChildren(el);
  const nx = V.lista[V.i + 1];
  if (nx?.tipo === "imagem") new Image().src = safeU(nx.url); // só a seguinte
}
function verFechou() {
  q(".bib-vs").replaceChildren(); // pára o vídeo/áudio
  const alvo = V.de?.isConnected ? V.de : q(`.bib-th[data-v="${V.i}"]`);
  alvo?.focus();
  if (V.prog) { V.prog = false; return; }
  if (V.pushed) { V.pushed = false; history.back(); }
  else if (S.cur) history.replaceState(history.state, "", rotaHash({ vista: "item", id: S.cur.item.id }));
}

// ---- leitor (página)

async function ler(f) {
  mostrar("ler");
  const box = q(".bib-ler"), n = ++S.seq, raiz = String(f.caminho || "").split("/")[0], pdf = f.tipo === "pdf" || f.ext === "pdf";
  const dlH = f.grande ? `<button type="button" class="bib-btn" disabled>⤓</button>` : `<a class="bib-btn" href="${escU(f.dl)}" download aria-label="Descarregar">⤓</a>`;
  box.innerHTML = `<div class="bib-bar bib-fix"><button type="button" class="bib-btn" data-a="voltar">‹ Biblioteca</button><span class="bib-sp"></span>${dlH}`
    + (pdf || f.grande ? "" : `<button type="button" class="bib-btn" data-a="copiar-tudo" disabled>Copiar texto</button>`) + `</div>`
    + `<p class="sub">${esc([fonteDe(raiz), f.mtime ? fmtData(f.mtime) : "", f.bytes ? fmtBytes(f.bytes) : ""].filter(Boolean).join(" · "))}</p>`
    + `<h2 class="bib-lh" tabindex="-1">${esc(f.titulo || f.nome)}</h2>`
    + `<div class="bib-rd"><div class="bib-rside"></div><article class="md bib-md"><div class="bib-sk bib-skl" aria-hidden="true">${"<i></i>".repeat(8)}</div></article></div>`
    + `<div class="bib-bar"><button type="button" class="bib-btn" data-a="voltar">‹ Biblioteca</button><button type="button" class="bib-btn" data-a="topo">↑ Topo</button></div>`;
  q(".bib-lh").focus({ preventScroll: true });
  const art = q(".bib-md");
  if (f.grande) return (art.innerHTML = `<p class="bib-msg">Ficheiro grande demais para mostrar aqui (mais de 1 MB de texto).</p>`);
  if (pdf) return (art.innerHTML = `<p><a class="bib-btn bib-pri" href="${escU(f.url)}" target="_blank" rel="noopener">Abrir PDF ›</a></p>`);
  try {
    const r = await fetch(safeU(f.url));
    if (!r.ok) throw new Error(r.status);
    const txt = await r.text();
    if (n !== S.seq) return;
    const { front, corpo } = semFrontmatter(txt);
    const fi = ficha(front);
    q(".bib-rside").innerHTML = fi.length ? `<dl class="bib-ficha" aria-label="Ficha">${fi.map(([k, v]) => `<dt>${esc(k)}</dt><dd${v.length > 120 ? ` class="bib-clamp"` : ""}>${esc(v)}</dd>`
      + (v.length > 120 ? `<dd><button type="button" class="bib-lnk" data-a="mais-ficha">… mais</button></dd>` : "")).join("")}</dl>` : "";
    art.innerHTML = corpo.trim() ? A.md(corpo) : `<p class="bib-msg">Documento vazio.</p>`;
    enfeita(art);
    const c = q('[data-a="copiar-tudo"]');
    if (c) c.disabled = !corpo.trim();
  } catch (e) {
    if (n === S.seq) art.innerHTML = `<p class="bib-msg">${esc(e.message === "404" ? "Este documento já não está na biblioteca." : "Não foi possível carregar o documento.")}</p>`;
  }
}
// depois do md(): «Copiar» em cada citação e bloco de código; índice das secções (##) se houver 2 ou mais
function enfeita(art) {
  for (const el of art.querySelectorAll("blockquote, pre")) {
    const b = document.createElement("button");
    Object.assign(b, { type: "button", className: "bib-btn bib-cp", textContent: "Copiar" });
    b.dataset.a = "copiar";
    el.after(b);
  }
  const hs = [...art.querySelectorAll("h4")];
  if (hs.length < 2) return;
  const det = document.createElement("details"), sum = document.createElement("summary"), ol = document.createElement("ol");
  sum.textContent = `Índice (${hs.length} secções)`;
  hs.forEach((h, i) => {
    const li = document.createElement("li"), b = document.createElement("button");
    Object.assign(b, { type: "button", className: "bib-lnk", textContent: h.textContent });
    b.dataset.a = "secao"; b.dataset.i = i;
    li.append(b); ol.append(li);
  });
  det.className = "bib-ind";
  det.append(sum, ol);
  q(".bib-rside").append(det);
}

async function copiar(texto, btn, el) {
  const sr = q(".bib-sr");
  try {
    await navigator.clipboard.writeText(texto);
    const t = btn.textContent;
    btn.textContent = "✓ Copiado";
    setTimeout(() => (btn.textContent = t), 2000);
    sr.textContent = "Texto copiado.";
  } catch { // sem a API (http, browser antigo): selecciona para copiar à mão
    const r = document.createRange();
    r.selectNodeContents(el);
    getSelection().removeAllRanges();
    getSelection().addRange(r);
    sr.textContent = "Não foi possível copiar. Selecciona o texto e copia à mão.";
  }
}

// zip: o servidor recusa com 413/503/404 + JSON, que um <a download> esconderia (o browser só diz «falhou»). No clique pergunta-se
// antes ao servidor com «&verificar=1» (204 = pode, sem ocupar vaga); se der, segue o link normal (gestor de downloads nativo,
// sem guardar o zip em memória no telemóvel); se não, mostra-se a mensagem. Limitação iOS Safari: o .click() depois do await
// pode já não contar como gesto do utilizador e o download não arrancar; por isso a verificação vale ZIP_OK ms e um 2.º toque
// segue directo. Se o fetch falhar por rede, não se bloqueia: o link segue e o browser mostra o que vier.
export const ZIP_OK = 60_000;
export const zipVerificaU = (href) => { const u = safeU(href); return u && u + (u.includes("?") ? "&" : "?") + "verificar=1"; };
export const zipRecente = (ok, agora) => Number(ok) > 0 && agora - Number(ok) < ZIP_OK;
async function zipTesta(zip) {
  const txt = zip.textContent, sr = q(".bib-sr");
  let erro = zip.parentElement.querySelector(".bib-zerr");
  zip.setAttribute("aria-busy", "true");
  zip.textContent = "A preparar…";
  erro?.remove();
  let msg = "";
  try {
    const r = await fetch(zipVerificaU(zip.getAttribute("href")), { cache: "no-store" });
    if (!r.ok) { const j = await r.json().catch(() => ({})); msg = `Não foi possível preparar o zip${j.error ? ": " + j.error : ""}.`; }
  } catch { /* rede: não bloqueia, o link segue */ }
  zip.removeAttribute("aria-busy");
  zip.textContent = txt;
  if (!msg) { zip.dataset.ok = String(Date.now()); zip.click(); return; }
  erro = document.createElement("p");
  Object.assign(erro, { className: "bib-msg bib-zerr", textContent: msg });
  zip.after(erro);
  sr.textContent = msg;
}

// miniatura que falha (ex.: 404 acima de 40 MP): troca-se o <img> por uma tile do mesmo tamanho (nunca imagem partida)
function imgFalhou(e) {
  const img = e.target;
  if (img.tagName !== "IMG" || !img.closest(".bib-minis, .bib-rt, .bib-th")) return;
  const vid = img.nextElementSibling?.classList.contains("bib-tile");
  if (vid) { img.nextElementSibling.classList.add("bib-imgx"); return img.remove(); } // vídeo: fica a tile «▶ Vídeo» a cheio
  const t = document.createElement("span");
  t.className = "bib-tile bib-imgx";
  t.textContent = img.closest(".bib-th") ? "▣ Imagem" : "▣";
  if (img.alt) { t.setAttribute("role", "img"); t.setAttribute("aria-label", img.alt); }
  img.replaceWith(t);
}

// ---- cliques (delegação: sobrevive a cada repintura)

function clique(e) {
  const t = e.target;
  const chip = t.closest?.(".bib-chips button");
  if (chip) {
    const g = chip.closest("[data-g]").dataset.g, k = chip.dataset.k;
    return mudaFiltro({ [g]: g === "data" ? k : k === "todos" || S.f[g] === k ? null : k });
  }
  const th = t.closest?.(".bib-th");
  if (th) return verAbrir(Number(th.dataset.v), true);
  const vb = t.closest?.("[data-vn]");
  if (vb) return vb.dataset.vn === "x" ? q(".bib-v").close() : verIr(V.i + Number(vb.dataset.vn));
  const zip = t.closest?.("a[data-zip]");
  if (zip) {
    if (zipRecente(zip.dataset.ok, Date.now())) return; // verificado há pouco (o nosso .click() ou 2.º toque no iOS): link normal
    e.preventDefault();
    if (!zip.hasAttribute("aria-busy")) zipTesta(zip);
    return;
  }
  const a = t.closest?.('a[href^="#biblioteca"]');
  if (a && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0) {
    e.preventDefault();
    if (S.vista === "lista") {
      S.focoRec = a.classList.contains("bib-rc");
      S.focoId = (S.focoRec ? a : a.closest(".bib-card")?.querySelector(".bib-a"))?.dataset.id ?? S.focoId;
    }
    return ir(a.getAttribute("href"));
  }
  const b = t.closest?.("[data-a]");
  if (!b) return;
  const act = b.dataset.a;
  if (act === "escritorio") A.fechar();
  else if (act === "actualizar") carregar();
  else if (act === "mais") { S.limite += 20; pintarLista(); }
  else if (act === "voltar") voltar();
  else if (act === "limpar-q") { q(".bib-q").value = ""; mudaFiltro({ q: "" }); q(".bib-q").focus(); }
  else if (act === "limpar-f") { q(".bib-q").value = ""; mudaFiltro({ ...FILTROS0 }); }
  else if (act === "aprov") A.verAprovacao(b.dataset.fonte, b.dataset.fich);
  else if (act === "topo") { scrollTo(0, 0); q(".bib-lh")?.focus({ preventScroll: true }); }
  else if (act === "copiar-tudo") { // texto já renderizado (sem «##», «**», «|») para colar no WhatsApp; sem os botões «Copiar» nem a Ficha
    const art = q(".bib-md"), cps = [...art.querySelectorAll(".bib-cp")];
    cps.forEach((x) => (x.hidden = true));
    const txt = art.innerText.trim();
    cps.forEach((x) => (x.hidden = false));
    copiar(txt, b, art);
  }
  else if (act === "copiar") { const el = b.previousElementSibling; copiar(el.innerText.trim(), b, el); }
  else if (act === "secao") q(".bib-md").querySelectorAll("h4")[b.dataset.i]?.scrollIntoView({ block: "start" });
  else if (act === "mais-ficha") { b.closest("dd").previousElementSibling.classList.remove("bib-clamp"); b.closest("dd").remove(); }
}
