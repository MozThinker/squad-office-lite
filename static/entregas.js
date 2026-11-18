// v3 (CA-32…36): caixa de Entregas. Só carregado no 1.º clique em «Entregas» (import() no lateral.js). Pede /api/entregas a
// cada abertura do separador (nunca no poll); manchete e resumo entram por textContent/esc (texto simples, nunca HTML do
// servidor). As «lidas» vivem no lateral.js (localStorage, máx. 200); o «por ler» do separador vem só do /api/state.
import { ha } from "./fabrica.js";

let A = null, box = null, dados = null, erro = false, seq = 0;
const abertos = new Set();
// chave de ficheiro do índice da Biblioteca (ex.: «tarefas/<id>/SPEC.md») → rota do leitor
export const rotaBib = (chave) => "#biblioteca/" + String(chave).split("/").map(encodeURIComponent).join("/");
export const nomeBib = (chave) => String(chave).split("/").pop();

// api = { esc, getState(), ROOMS(), cfg(), abrirTarefa(id), separador(k), lida(k), porLer(), marcar(k), marcarTodas() }
export function abrir(el, api) {
  A = api;
  if (box !== el) { box = el; el.addEventListener("click", clique); }
  carregar();
}

async function carregar() {
  const n = ++seq;
  if (!dados || erro) box.innerHTML = `<p class="sub" role="status">A carregar…</p>`;
  erro = false;
  try {
    const r = await fetch("api/entregas", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const j = await r.json();
    if (n !== seq) return;
    dados = Array.isArray(j.itens) ? j.itens : [];
  } catch {
    if (n !== seq) return;
    erro = true;
  }
  pintar();
}

function item({ it, k, t, nova }, agora) {
  const esc = A.esc, o = abertos.has(k), P = A.cfg().projectos || [], pj = P.find((p) => p.id === it.projecto)?.nome || it.projecto;
  const fim = Date.parse(it.terminada ?? "");
  const meta = [A.ROOMS()[it.squad]?.name || it.squad, pj, fim > 0 && "terminou há " + ha(agora - fim), t?.status].filter(Boolean).map(esc).join(" · ");
  const bib = Array.isArray(it.biblioteca) ? it.biblioteca.filter((x) => typeof x === "string") : [];
  return `<li class="${nova ? "en-nova" : ""}" data-k="${esc(k)}"><p class="sub">${nova ? `<b class="en-pl">por ler</b> · ` : ""}${meta}</p>`
    + `<h3>${esc(t?.titulo || (t ? `${t.project} · ${t.mode}` : it.id))}</h3>`
    + (it.manchete ? `<p class="en-m">${esc(it.manchete)}</p>` : `<p class="sub">Sem entrega registada.</p>`)
    + `<div class="btns">${it.resumo ? `<button type="button" data-a="resumo" aria-expanded="${o}">${o ? "Esconder resumo" : "Ver resumo"}</button>` : ""}`
    + (t ? `<button type="button" data-a="completa">Ver entrega completa</button>` : "")
    + bib.map((c) => `<a href="${esc(rotaBib(c))}">${esc(nomeBib(c))} na Biblioteca</a>`).join("") + `</div>`
    + (o && it.resumo ? `<p class="en-r"></p>` : "") + `</li>`;
}

function pintar() {
  if (erro) {
    box.innerHTML = `<p class="sub s-bad" role="alert">Não foi possível carregar as entregas.</p><div class="btns"><button type="button" data-a="tentar">Tentar de novo</button></div>`;
    return;
  }
  const ae = document.activeElement, fk = box.contains(ae) ? [ae.closest("[data-k]")?.dataset.k, ae.dataset.a] : null;
  const ts = A.getState()?.tasks || [], agora = Date.now(), pl = new Set(A.porLer());
  const l = dados.map((it) => { const k = `${it.squad}/${it.id}`; return { it, k, t: ts.find((x) => `${x.squad}/${x.id}` === k), nova: pl.has(k) }; })
    .sort((a, b) => b.nova - a.nova); // por ler primeiro (a ordem do servidor fica dentro de cada grupo)
  const n = pl.size; // o mesmo número do separador (vem do /api/state)
  box.innerHTML = `<div class="en-top"><p class="sub">${n ? `${n} por ler (últimas 24 h)` : "Nada por ler"} · terminadas nos últimos 7 dias</p>`
    + (n ? `<div class="btns"><button type="button" data-a="todas">Marcar todas como lidas</button></div>` : "") + `</div>`
    + (l.length ? `<ul class="en">${l.map((x) => item(x, agora)).join("")}</ul>` : `<p class="sub">Sem entregas nos últimos 7 dias.</p>`);
  // o resumo é texto do servidor: entra por textContent (nunca HTML)
  for (const x of l) { const p = abertos.has(x.k) && [...box.querySelectorAll("li[data-k]")].find((e) => e.dataset.k === x.k)?.querySelector(".en-r"); if (p) p.textContent = x.it.resumo; }
  if (!fk) return;
  const li = [...box.querySelectorAll("li[data-k]")].find((e) => e.dataset.k === fk[0]);
  ((li || box).querySelector(`[data-a="${fk[1]}"]`) || box.querySelector("button"))?.focus({ preventScroll: true });
}

function clique(e) {
  const b = e.target.closest("[data-a]"), k = b?.closest("[data-k]")?.dataset.k, a = b?.dataset.a;
  if (!b) return;
  if (a === "tentar") return carregar();
  if (a === "todas") { A.marcarTodas(); return pintar(); }
  if (a === "resumo") {
    if (abertos.delete(k)) return pintar();
    abertos.add(k);
    A.marcar(k);
    return pintar();
  }
  if (a === "completa") { // a tarefa e a «Entrega» que já existe no #info
    A.marcar(k);
    const id = k.slice(k.indexOf("/") + 1);
    if (A.abrirTarefa(id) === false) return pintar();
    A.separador("tarefa");
    const d = document.querySelector('#info-t [data-act="entrega"]');
    if (d && d.getAttribute("aria-expanded") !== "true") d.click();
    document.querySelector('#info-t [data-act="entrega"]')?.focus({ preventScroll: true });
  }
}
