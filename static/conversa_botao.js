// Botão da Conversa no cabeçalho do Office (ADR-023, revisão do guardião). A Conversa vive numa origem própria, servida pelo
// guardião: o Office nunca carrega o JS dela nem fala com a API dela. Com cfg.conversa.url (https://<site>/) o botão é uma
// ligação que a abre por navegação de topo; sem url (sem site ou sem TLS) abre a explicação. O nome e os pedidos à espera
// vêm do state.conversa (o publico.json do guardião). As funções exportadas no topo são puras (tests/conversa_botao_test.mjs).

export const NOME_OMISSAO = "Assistente";
export const LEIA_ME = "https://github.com/MozThinker/squad-office-lite#readme";
export const SEM_SITE = ["A Conversa abre num endereço próprio, com HTTPS (por exemplo conversa.<o teu domínio>), e este Office ainda não o tem.",
  "Para a ligar: cria no DNS esse nome a apontar para este servidor, põe-no em conversa.site no office.yaml e corre o install.sh com --dominio."];

// cfg.conversa → url https da Conversa, ou null (o botão explica)
export const urlDe = (c) => (c?.ativa === true && typeof c.url === "string" && /^https:\/\/[^\s"'<>\\]+$/.test(c.url) ? c.url : null);
// state.conversa → nome, contador («9+») e o rótulo para leitores de ecrã
export function rotulo(s) {
  const nome = typeof s?.nome === "string" && s.nome.trim() ? s.nome : NOME_OMISSAO, n = Number.isInteger(s?.pendentes) && s.pendentes > 0 ? s.pendentes : 0;
  return { nome, n: n > 9 ? "9+" : n ? String(n) : "",
    aria: nome + (n ? `, ${n} ${n === 1 ? "pedido" : "pedidos"} à espera` : "") + (s?.ativa === false ? ", parada" : "") };
}

// ---- DOM

let b = null, dlg = null;
const ICO = '<svg class="ico" aria-hidden="true" focusable="false"><use href="#i-conversa"/></svg><span class="lg"></span><span class="cv-n" aria-hidden="true" hidden></span>';

function css() {
  const l = document.createElement("link");
  Object.assign(l, { rel: "stylesheet", href: new URL("./conversa_botao.css", import.meta.url).href });
  document.head.append(l);
}

function explicar() {
  if (!dlg) {
    dlg = document.createElement("dialog");
    dlg.id = "cv-dlg";
    dlg.setAttribute("aria-labelledby", "cv-dh");
    const p = (t) => Object.assign(document.createElement("p"), { textContent: t }), a = document.createElement("a");
    Object.assign(a, { href: LEIA_ME, target: "_blank", rel: "noopener noreferrer", textContent: "Guia da Conversa (README)" });
    const topo = document.createElement("div"), h = Object.assign(document.createElement("h2"), { id: "cv-dh", textContent: "Conversa" });
    const x = Object.assign(document.createElement("button"), { type: "button", id: "cv-x", textContent: "fechar" });
    topo.className = "rd-top"; topo.append(h, x);
    const pa = document.createElement("p"); pa.append(a);
    dlg.append(topo, ...SEM_SITE.map(p), pa);
    x.addEventListener("click", () => dlg.close());
    dlg.addEventListener("close", () => b?.focus());
    document.body.append(dlg);
  }
  dlg.showModal();
}

// c = cfg.conversa; antes = o botão do cabeçalho antes do qual entra (o ⚙)
export function montar(c, antes) {
  if (b) return;
  css();
  const url = urlDe(c);
  b = document.createElement(url ? "a" : "button");
  b.id = "cv-b";
  if (url) Object.assign(b, { href: url, target: "_blank", rel: "noopener noreferrer" });
  else { b.type = "button"; b.setAttribute("aria-haspopup", "dialog"); b.addEventListener("click", explicar); }
  b.innerHTML = ICO; // só texto fixo; o nome entra por textContent
  antes.before(b);
}

// s = state.conversa (a cada poll do Office)
export function pintar(s) {
  if (!b) return;
  const r = rotulo(s), n = b.querySelector(".cv-n");
  b.querySelector(".lg").textContent = r.nome;
  n.textContent = r.n; n.hidden = !r.n;
  b.setAttribute("aria-label", r.aria); b.title = r.aria;
}
