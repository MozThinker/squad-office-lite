// Peça de estado (pura ao importar)
export const ESTADOS = "a-trabalhar a-processar a-espera concluido erro desligado".split(" ");
const E = { "&": "amp", "<": "lt", ">": "gt", '"': "quot", "'": "#39" }, F = Math.floor, N = Number.isFinite;
export const fmtTempo = (s) => !(s >= 3 && s < 1 / 0) ? "" : s < 60 ? F(s) + " s" : s < 3600 ? F(s / 60) + " min"
  : s < 86400 ? F(s / 3600) + " h " + (F(s / 60) % 60 + "").padStart(2, 0) : F(s / 86400) + " d";
export const cadencia = (s) => s < 60 ? 1e3 : 3e4;
export const tempo = (e, t0, agora = Date.now(), t = fmtTempo((agora - t0) / 1e3), i = ESTADOS.indexOf(e)) =>
  i > 4 || !N(t0) ? "" : i > 2 ? new Date(t0).toTimeString().slice(0, 5) : i == 2 && t ? "há " + t : t;
export const html = (e, x, t0, agora) => (x = ((x ?? "") + "").replace(/[&<>"']/g, (c) => `&${E[c]};`), ESTADOS.includes(e)
  ? `<span class="estado" data-estado="${e}" aria-live="polite"${N(t0) ? ` data-t0="${t0}"` : ""}><span class="estado-x">${x}</span><span class="estado-t" aria-hidden="true">${tempo(e, t0, agora)}</span></span>` : x);
export function pintar(el, e, x, t0) {
  const p = el.querySelector(".estado"), c = p?.classList, s = p?.dataset;
  if (!p || !ESTADOS.includes(e)) el.innerHTML = html(e, x, t0);
  else {
    if (s.estado != e) { s.estado = e; c.remove("est-novo"); p.offsetWidth; c.add("est-novo"); }
    const n = p.firstChild; n.textContent != x && (n.textContent = x);
    N(t0) ? (s.t0 = t0) : delete s.t0;
    p.lastChild.textContent = tempo(e, t0);
  }
  ligar();
}
let tm, vis;
export function ligar(d = globalThis.document) {
  if (!d) return;
  vis ||= !d.addEventListener("visibilitychange", () => ligar());
  clearTimeout(tm);
  let c = 0, a = Date.now();
  if (!d.hidden) for (const p of d.querySelectorAll(".estado[data-t0]")) {
    const e = p.dataset.estado, t0 = +p.dataset.t0;
    p.lastChild.textContent = tempo(e, t0, a);
    if (ESTADOS.indexOf(e) < 3) c = Math.min(c || 3e4, cadencia((a - t0) / 1e3));
  }
  if (c) tm = setTimeout(ligar, c);
}
