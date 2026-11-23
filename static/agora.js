// Faixa «Agora» (pura ao importar)
import { html as H, fmtTempo } from "./estado.js";
export const MAX = 4;
const P = Date.parse, pc = (e, x, t0) => H(e, x, t0, NaN).replace(' aria-live="polite"', ""); // o anúncio é do ul; o tempo, do ligar()
export const cabecalho = (s, t, a = Date.now(), h = t?.running && fmtTempo((a - P(t.started)) / 1e3)) =>
  H(0, ["Agora", s, t && (t.titulo || t.project), h && "há " + h].filter(Boolean).join(" · "));
// c = { esc, papel(r) → {name, short}, roleOf, lider, coord, acao(n) → {acao, preso} }
export function linhas(t, c, o = [], v = {}, E = c.esc) {
  const um = (r, n, x, t0, e, { acao, preso } = n.act ? c.acao(n) : { acao: x }, s = n.stuck || preso, p = c.papel(r)) => (e || s) && o.push({ s,
    k: v[r] ? r + "#" + v[r]++ : (v[r] = 1, r), html: `<button type="button" data-role="${E(r)}">${pc(s ? "a-espera" : "a-trabalhar", p.name, n.act?.desde ? P(n.act.desde) : t0)}`
      + `<span class="ag-p">${E(p.short)}</span> <span class="ag-a">${E(acao)}</span>${s ? ' <span class="tag">preso?</span>' : ""}</button>` });
  if (t?.running) {
    t.lead && um(c.lider, t.lead, c.coord, P(t.started), t.working?.includes(t.lead.role));
    const q = o.length; // #i estável; o mais recente 1.º
    for (const a of (t.agents || []).filter((a) => a.active || a.stuck).sort((a, b) => P(a.start) - P(b.start))) um(c.roleOf(a.role), a, a.desc, P(a.start), 1);
    o.push(...o.splice(q).reverse());
  }
  return [...t?.gh?.ci == "vermelho" ? [{ k: "ci", html: pc("erro", "CI vermelho", P(t.gh.at)) }] : [],
    ...o.length ? o.sort((a, b) => !a.s - !b.s) : [{ k: "vazio", cls: "vazio", html: E(t?.status || "sem trabalho") }]];
}
// diff por data-k (mover não reescreve); devolve o nº de escondidas
export function reconciliar(ul, ls, max = 1 / 0, m = new Map([...ul.children].map((li) => [li.dataset.k, li]))) {
  ls.forEach((l, i, _, li = m.get(l.k)) => {
    m.delete(l.k) || ((li = document.createElement("li")).dataset.k = l.k);
    li.className = l.cls || "";
    li._h != l.html && (li.innerHTML = li._h = l.html);
    ul.children[i] != li && ul.insertBefore(li, ul.children[i]);
    li.hidden = i >= max;
  });
  m.forEach((li) => li.remove());
  return Math.max(0, ls.length - max);
}
