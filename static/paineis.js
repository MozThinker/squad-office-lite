// v3 (CA-13): painéis da barra «À tua espera» e «Falhas». Só carregado no 1.º clique (import() no topo.js).
// Um <dialog> nativo: Esc e «fechar» fecham e o foco volta ao contador; «Responder» abre a aprovação (diálogo existente) ou o
// PR; «Ver» abre a tarefa.
import { esperaDe, falhasDe, prNum, PR_URL } from "./avisos.js";

let dlg = null, A = null, itens = [];
const esc = (s) => A.esc(s);
const idade = (n) => (!(n > 0) ? "hoje" : n === 1 ? "há 1 dia" : `há ${n} dias`);
const COR = { aprovacao: "var(--accent)", pr: "var(--warn)", tarefa: "var(--bad)", exec: "var(--bad)" };

function linha(it, i) {
  let det, acc = `<button type="button" data-i="${i}">Ver</button>`;
  if (it.tipo === "aprovacao") {
    det = `${esc(it.fonte)} · ${esc(idade(it.idade_dias))} · ${esc(it.abertas)} ${it.abertas === 1 ? "decisão em aberto" : "decisões em aberto"}`;
    acc = `<button type="button" data-i="${i}">Responder</button>`;
  } else if (it.tipo === "pr") {
    det = `${esc(it.project || it.id)} · PR #${esc(prNum(it.pr_url) || "?")}${it.draft ? " · draft" : ""}`;
    acc = PR_URL.test(it.pr_url ?? "") ? `<a href="${esc(it.pr_url)}" target="_blank" rel="noopener" data-c="${esc(it.chave)}">Responder</a>` : "";
  } else det = esc(it.oque);
  const tit = it.tipo === "aprovacao" ? it.titulo : it.tipo === "pr" ? "PR à espera de merge" : it.quem;
  return `<li><i style="background:${COR[it.tipo] || "var(--muted)"}"></i><span><b>${esc(tit)}</b><br><span class="sub">${det}</span></span><span class="pn-a">${acc}</span></li>`;
}

function pinta(tipo) {
  const s = A.getState();
  itens = tipo === "espera" ? esperaDe(s) : falhasDe(s);
  const vazio = tipo === "espera" ? "Nada à tua espera." : "Sem falhas.";
  dlg.innerHTML = `<div class="rd-top"><h2 id="pn-h">${tipo === "espera" ? "À tua espera" : "Falhas"} <span class="mono">(${itens.length})</span></h2>`
    + `<button type="button" data-a="fechar">fechar</button></div>`
    + (itens.length ? `<ol class="pn-l">${itens.map(linha).join("")}</ol>` : `<p class="sub pn-v">${vazio}</p>`);
}

// api = { esc, getState(), ir(item), origem }; foco = chave do item cuja ligação recebe o foco (P num PR)
export function abrir(tipo, api, foco) {
  A = api;
  if (!dlg) {
    dlg = document.createElement("dialog");
    dlg.id = "painel";
    dlg.className = "pn";
    dlg.setAttribute("aria-labelledby", "pn-h");
    document.body.append(dlg);
    dlg.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.a === "fechar") return dlg.close();
      const it = itens[+b.dataset.i];
      dlg._ir = true; // vai para o destino: o foco não volta à barra
      dlg.close();
      A.ir(it);
    });
    dlg.addEventListener("close", () => { if (!dlg._ir) dlg._origem?.focus(); dlg._ir = false; });
  }
  dlg._origem = api.origem;
  pinta(tipo);
  if (!dlg.open) dlg.showModal();
  const f = foco && [...dlg.querySelectorAll("a[data-c]")].find((a) => a.dataset.c === foco);
  (f || dlg.querySelector('[data-a="fechar"]')).focus();
}
