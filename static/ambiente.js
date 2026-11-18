// v3 (CA-37…39): dia e noite no desenho do escritório, pela hora no fuso do office.yaml com horas fixas (D-6: dia 06:00–18:30,
// crepúsculo ± 30 min em cada ponta; sem latitude na config). Carregado no arranque (import() no index.html). Só ambiente: a
// janela, uma camada de escurecimento e os candeeiros ficam por baixo de toda a informação (secretárias, balões, «preso?», CI,
// quadro da sala). A fase vive em #office[data-fase]; o SVG é o mesmo em todas as fases e a mudança é só CSS (office.css:
// --amb-n/--amb-c, com transição só depois da 1.ª pintura). «Dia e noite» desligado no ⚙ = sempre dia.
// As exportadas no topo são puras (testes em node).

// ---- puras

export const NASCER = 360, POR = 1110, CREPUSCULO = 30; // minutos desde a meia-noite: 06:00 e 18:30; ± 30 min

// "dia" | "crepusculo" | "noite" para os minutos desde a meia-noite (0–1439)
export function faseDoDia(min) {
  if (Math.abs(min - NASCER) <= CREPUSCULO || Math.abs(min - POR) <= CREPUSCULO) return "crepusculo";
  return min > NASCER && min < POR ? "dia" : "noite";
}
// minutos desde a meia-noite no fuso tz (fuso desconhecido do browser: hora local)
export function minutosNoFuso(data, tz) {
  const o = { hour: "numeric", minute: "numeric", hourCycle: "h23" };
  let f;
  try { f = new Intl.DateTimeFormat("en-GB", { ...o, timeZone: tz }); } catch { f = new Intl.DateTimeFormat("en-GB", o); }
  const p = {};
  for (const x of f.formatToParts(new Date(data))) p[x.type] = x.value;
  return +p.hour * 60 + +p.minute;
}
// fase de um instante; desligado (⚙) → sempre dia
export const fase = (data, tz, ligado = true) => (ligado ? faseDoDia(minutosNoFuso(data, tz)) : "dia");
export const rotulo = (f) => ({ dia: "dia", crepusculo: "crepúsculo", noite: "noite" })[f] || "dia";
// fim do aria-label do SVG: « · dia», « · crepúsculo» ou « · noite»
export const sufixo = (data, tz, ligado = true) => " · " + rotulo(fase(data, tz, ligado));
// preferência do ⚙ (localStorage["diaNoite"]; «0» = desligado, omissão ligado)
export const ligado = (ls) => { try { return ls?.getItem("diaNoite") !== "0"; } catch { return true; } };

// ---- SVG (cores fixas: o desenho não depende do tema)

const rc = (x, y, w, h, f) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${f}"/>`;
// dois candeeiros de pé por sala, nos corredores entre as colunas de secretárias (x, y do topo do abajur)
const CANDEEIROS = [[59, 111], [251, 111]];

// céus empilhados (dia por baixo; crepúsculo e noite por cima, com a opacidade dada pela fase) e caixilho por cima
function janela(dx) {
  const ceu = (cs) => cs.map((c, i) => rc(6, 6 + i * 6 + (i ? 1 : 0), 34, i ? 6 : 7, c)).join("");
  const dia = ceu(["#42a5f5", "#64b5f6", "#90caf9"]) + rc(31, 8, 5, 5, "#fff59d") + rc(32, 9, 3, 3, "#ffee58") + rc(9, 11, 8, 2, "#e3f2fd") + rc(11, 10, 4, 1, "#e3f2fd");
  const tarde = ceu(["#5c6bc0", "#f48fb1", "#ffab91"]) + rc(11, 21, 7, 4, "#ffb74d") + rc(12, 20, 5, 1, "#ffb74d") + rc(26, 10, 6, 1, "#fce4ec");
  const noite = ceu(["#0d1240", "#1a237e", "#283593"]) + rc(31, 8, 2, 1, "#fff9c4") + rc(30, 9, 1, 2, "#fff9c4") + rc(31, 11, 2, 1, "#fff9c4")
    + [[9, 9], [16, 13], [26, 18], [12, 20], [36, 15]].map(([x, y]) => rc(x, y, 1, 1, "#ffffff")).join("");
  return `<g transform="translate(${dx} 0)">` + rc(4, 4, 38, 23, "#3e2723") + dia + `<g class="amb-c">${tarde}</g><g class="amb-n">${noite}</g>`
    + rc(22, 6, 2, 19, "#3e2723") + rc(6, 15, 34, 1, "#3e2723") + rc(3, 26, 40, 2, "#8d6e63") + `</g>`;
}

// candeeiro de pé: abajur, haste e base; aceso = abajur amarelo e um halo quente estático (sem animação)
function candeeiro(x, y) {
  const apagado = rc(x + 1, y, 5, 1, "#8d7b68") + rc(x, y + 1, 7, 3, "#8d7b68") + rc(x + 3, y + 4, 1, 12, "#4e4a46") + rc(x + 1, y + 16, 5, 2, "#4e4a46");
  const cx = x + 3.5, cy = y + 2, q = (w, h, o) => `<rect x="${cx - w}" y="${cy - h}" width="${2 * w}" height="${2 * h}" fill="#ffd54f" fill-opacity="${o}"/>`;
  const halo = [[18, ".06"], [12, ".08"], [7, ".1"]].map(([r, o]) => q(r, Math.round(r * .6), o) + q(Math.round(r * .6), r, o)).join("");
  return apagado + `<g class="amb-l">${halo}${rc(x + 1, y, 5, 1, "#fff3c4")}${rc(x, y + 1, 7, 3, "#ffe082")}</g>`;
}

// <g class="amb">: camada de escurecimento (chão e paredes), janela e candeeiros; vai antes de toda a informação no SVG
export function fundo(altura = 258) {
  return `<g class="amb" aria-hidden="true"><rect class="amb-e" width="320" height="${altura}" fill="#050a24"/>`
    + janela(8) + CANDEEIROS.map(([x, y]) => candeeiro(x, y)).join("") + `</g>`;
}

// ---- DOM

// #office[data-fase] em cada pintura; a transição (office.css) só fica activa depois da 1.ª pintura (nada a animar ao carregar)
export function aplicar(el, data, tz) {
  const f = fase(data, tz, ligado(globalThis.localStorage));
  if (el.dataset.fase !== f) el.dataset.fase = f;
  if (!("ambOk" in el.dataset) && !el._ambP) {
    el._ambP = 1;
    requestAnimationFrame(() => requestAnimationFrame(() => { el.dataset.ambOk = ""; }));
  }
}
