// Faixa «Fábrica» (O meu dia, Actividade, À tua espera, Consumo), movida do index.html (v3: CA-28…31). Carregado no
// arranque (import() no index.html); tabelas.js só no 1.º «Ver tabela». As exportadas no topo são puras (testes em node).
// Tudo no fuso do office.yaml (api.ctx().TZ; fuso desconhecido do browser: hora local). Só reescreve o que mudou: o poll não
// fecha um <details> aberto, uma tabela aberta, nem tira o foco a um botão.

// ---- puras

const ms = (x) => (typeof x === "number" ? x : Date.parse(x ?? ""));
const fmtH = (tz) => { try { return new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "numeric", minute: "numeric", hourCycle: "h23" }); }
  catch { return new Intl.DateTimeFormat("en-GB", { hour: "numeric", minute: "numeric", hourCycle: "h23" }); } };
// minutos desde a meia-noite no fuso tz
export const minDoDia = (t, tz) => { const p = Object.fromEntries(fmtH(tz).formatToParts(new Date(ms(t))).map((x) => [x.type, x.value])); return +p.hour * 60 + +p.minute; };
export const hhmm = (t, tz) => { const m = minDoDia(t, tz); return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };
const act = (e) => globalThis.ACTIVIDADE?.actText(e) ?? "";
const GH = () => globalThis.ACTIVIDADE?.GH_KIND ?? /^(pr_|push$|ci_)/;

// uma linha da Actividade: hora, agente, projecto (senão o id da tarefa), acção e erro (CI vermelho ou erro fora do GitHub)
export function linhaFeed(e, s, quem = (x) => x.role || "?", tz) {
  const t = (s?.tasks || []).find((x) => x.id === e.task);
  return { hora: hhmm(e.at, tz), agente: quem(e), projecto: t?.project || e.task, acao: act(e),
    erro: e.kind === "ci_vermelho" || (!!e.error && !GH().test(e.kind)) };
}

// as horas reais do dia civil a partir da meia-noite (23, 24 ou 25): hs[i] = hora local da i-ésima hora
export function horasDoDia(inicio, tz) {
  const hs = [];
  for (let i = 0; i < 26; i++) { const h = Math.floor(minDoDia(ms(inicio) + i * 36e5, tz) / 60); if (i && h === 0) break; hs.push(h); }
  return hs;
}
// minutos de hoje com pelo menos uma tarefa (união), desde a meia-noite até agora: cob[m] = 1
function cobertos(intervalos, a0, ag, horas) {
  const n = Math.max(0, Math.min(horas * 60, Math.ceil((ag - a0) / 6e4))), cob = new Uint8Array(n);
  for (const x of intervalos || []) {
    const s = Math.max(0, Math.floor((ms(x.ini) - a0) / 6e4)), e = Math.min(n, Math.ceil(((x.fim ? ms(x.fim) : ag) - a0) / 6e4));
    for (let m = s; m < e; m++) cob[m] = 1;
  }
  return cob;
}
// minutos de cada hora com pelo menos uma tarefa (nunca passa de 60: no dia de 25 h a hora repetida junta-se); horas futuras a 0
export function trabalhoPorHora(intervalos, inicio, agora, tz) {
  const a0 = ms(inicio), hs = horasDoDia(a0, tz), cob = cobertos(intervalos, a0, ms(agora), hs.length), out = Array(24).fill(0);
  cob.forEach((c, m) => { if (c) out[hs[Math.floor(m / 60)]]++; });
  return out.map((v) => Math.min(60, v));
}
// total «a trabalhar hoje» pelos minutos cobertos (não pela soma das barras, que perde a hora repetida no dia de 25 h)
export const minutosTrabalho = (intervalos, inicio, agora, tz) =>
  cobertos(intervalos, ms(inicio), ms(agora), horasDoDia(ms(inicio), tz).length).reduce((a, c) => a + c, 0);
const desdes = (itens) => (itens || []).map((x) => ms(x.desde)).filter(Number.isFinite); // sem «desde»: ignorado
// n.º de pedidos à espera no fim de cada hora (só os que ainda estão em aberto); horas futuras a 0
export function esperaPorHora(itens, inicio, agora, tz) {
  const a0 = ms(inicio), ag = ms(agora), ds = desdes(itens), out = Array(24).fill(0);
  horasDoDia(a0, tz).forEach((h, i) => { const ini = a0 + i * 36e5; if (ini <= ag) out[h] = Math.max(out[h], ds.filter((d) => d <= ini + 36e5).length); });
  return out;
}
// minutos de hoje com pelo menos um pedido à espera (as aprovações contam desde as 00:00 do dia em que foram criadas)
export function minutosEspera(itens, inicio, agora) {
  const ds = desdes(itens);
  return ds.length ? Math.max(0, Math.floor((ms(agora) - Math.max(ms(inicio), Math.min(...ds))) / 6e4)) : 0;
}
export const dur = (min) => (min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${min % 60} min`);
export const ha = (d) => { const m = Math.max(0, Math.floor(d / 6e4)); return m < 60 ? `${m} min` : m < 2880 ? `${Math.floor(m / 60)} h` : `${Math.floor(m / 1440)} dias`; };
const HH = (h) => String(h).padStart(2, "0") + ":00";
// linhas da «Ver tabela» das barras por hora (os mesmos números do gráfico)
export const linhas24 = (v) => ({ linhas: v.map((n, h) => [HH(h), String(n)]) });

// ---- DOM

let A = null, todos = false, tab = null, tabP = null;
const abertas = new Set();
const $ = (s) => document.querySelector(s);
// o poll troca o HTML: uma linha do feed com o foco (depois de «Mostrar mais») volta a tê-lo
const setHtml = (el, h) => {
  if (el._h === h) return;
  const f = document.activeElement, i = f?.matches?.(".fd > li") && el.contains(f) ? [...f.parentNode.children].indexOf(f) : -1;
  el.innerHTML = h; el._h = h;
  const li = i >= 0 && el.querySelector(`.fd > li:nth-child(${i + 1})`);
  if (li) { li.tabIndex = -1; li.focus({ preventScroll: true }); }
};
const rc = (x, y, w, h, f, c = "", st = "") => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${f}"${c ? ` class="${c}"` : ""}${st ? ` style="${st}"` : ""}/>`;

// api = { esc, fmtT, fmtNum, fmtUsd, fmtDT, ctx() → {ROOMS, SQS, SQC, TZ}, quem(evento), tabName(t), getState(), sala(), ir(item) }
export function iniciar(api) {
  A = api;
  $("#fabrica").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-f], button[data-w]");
    if (!b) return;
    if (b.dataset.w != null) return A.ir((A.getState()?.espera?.itens || [])[+b.dataset.w]);
    const k = b.dataset.f;
    if (k === "mais") todos = true;
    else if (!abertas.delete(k)) {
      abertas.add(k);
      tabP ||= import("./tabelas.js").then((m) => { tab = m; render(A.getState()); }, () => { tabP = null; abertas.clear(); render(A.getState()); });
    }
    render(A.getState());
    // «Mostrar mais»: o foco vai para a 11.ª linha (o botão desaparece)
    const li = k === "mais" && $('#fabrica details[data-k="feed"] .fd > li:nth-child(11)');
    if (li) { li.tabIndex = -1; li.focus(); }
  });
}

const fmtTok = (n) => (n >= 999500 ? A.fmtNum(n / 1e6, 1) + " M" : n >= 1e3 ? A.fmtNum(n / 1e3, 0) + " k" : A.fmtNum(n));
function usageHtml(u) {
  const esc = A.esc, note = `<p class="sub">Estimativa a partir dos registos locais; o limite da conta não é conhecido.</p>`;
  if (u?.status !== "ok") return `<p class="sub">A calcular…</p>` + note;
  const tot = (x) => x.input + x.output + x.cache_read + x.cache_creation;
  const table = (k, cap) => {
    const rows = Object.entries(u.windows[k] || {}).sort((a, b) => tot(b[1]) - tot(a[1]))
      .map(([m, x]) => `<tr><td>${esc(m)}</td><td>${esc(fmtTok(tot(x)))}</td><td>${esc(fmtTok(x.output))}</td></tr>`).join("");
    return rows ? `<table><caption>${cap}</caption><tr><th scope="col">modelo</th><th scope="col">total</th><th scope="col">saída</th></tr>${rows}</table>`
      : `<p class="sub"><b>${cap}</b>: sem registos.</p>`;
  };
  return table("5h", "Últimas 5 h") + table("7d", "Últimos 7 dias") + note + `<p class="sub">calculado às ${A.fmtT(u.computed_at)}</p>`;
}
// «À tua espera»: a mesma lista do painel da barra (state.espera: aprovações em aberto + PRs; recusas não)
const PR_RE = /^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/pull\/(\d+)$/;
function waitHtml(s) {
  const esc = A.esc, it = s.espera?.itens;
  if (!Array.isArray(it)) return `<p class="sub">—</p>`;
  const li = it.map((w, i) => {
    if (w.tipo === "aprovacao") return `<li><i style="background:var(--accent)"></i><span><b>${esc(w.titulo)}</b><br><span class="sub">${esc(w.fonte)} · `
      + `${esc(w.abertas)} ${w.abertas === 1 ? "decisão em aberto" : "decisões em aberto"}</span></span><span class="t"><button type="button" class="vt" data-w="${i}">Responder</button></span></li>`;
    const m = String(w.pr_url).match(PR_RE);
    return `<li><i style="background:${w.draft ? "var(--warn)" : "var(--accent)"}"></i><span><b>${esc(w.project ?? w.id)}</b> · `
      + (m ? `<a href="${esc(w.pr_url)}" target="_blank" rel="noopener noreferrer">${esc(`${m[1]}#${m[2]}`)}</a>` : "PR sem link") + (w.draft ? " · rascunho" : "")
      + `</span><span class="t">${esc(A.fmtDT(w.finished))}</span></li>`;
  }).join("");
  return (li ? `<ol>${li}</ol>` : `<p class="sub">Nada à tua espera.</p>`)
    + `<p class="sub">Estado do PR gravado no fim da tarefa; pode já ter sido integrado ou fechado.</p>`;
}
// «Ver tabela» com os mesmos números do gráfico (a tabela só existe depois do 1.º clique: tabelas.js)
function vt(k, dados) {
  const o = abertas.has(k);
  return `<button type="button" class="vt" data-f="${k}" aria-expanded="${o}">${o ? "Esconder tabela" : "Ver tabela"}</button>` + (o && tab ? tab.tabela(k, dados) : "");
}
// custo de 30 dias empilhado por squad
function barsSvg(hist) {
  const { ROOMS, SQS, SQC } = A.ctx(), esc = A.esc, fmtUsd = A.fmtUsd, tot = (d) => SQS.reduce((a, k) => a + (d[k]?.cost_usd || 0), 0);
  const max = Math.max(0.01, ...hist.map(tot)), H = 80, dia = (d) => `${d.date.slice(8)}/${d.date.slice(5, 7)}`;
  let s = "";
  hist.forEach((d, i) => {
    let y = 8 + H;
    const tip = `${dia(d)}: ` + SQS.map((k) => `${ROOMS[k].name} ${fmtUsd(d[k]?.cost_usd || 0)}`).join(" · ");
    s += `<g><title>${esc(tip)}</title>${rc(i * 10 + 1, 8, 8, H, "#232838", "", "fill:var(--sunk)")}`;
    for (const k of SQS) { const h = ((d[k]?.cost_usd || 0) / max) * H; if (h > 0) { y -= h; s += rc(i * 10 + 1, y.toFixed(2), 8, Math.max(.5, h).toFixed(2), SQC[k]); } }
    s += `</g>`;
  });
  const sum = hist.reduce((a, d) => a + tot(d), 0), t = (x, a, v) => `<text x="${x}" y="${a}" font-size="7" font-family="ui-monospace,monospace"${x > 150 ? ` text-anchor="end"` : ""}>${esc(v)}</text>`;
  return `<svg class="chart" viewBox="0 0 300 100" role="img" aria-label="${esc(`Custo dos últimos 30 dias: ${fmtUsd(sum)}, máximo num dia ${fmtUsd(max)}`)}">`
    + s + t(1, 6, "máx. " + fmtUsd(max)) + t(1, 98, hist[0] ? dia(hist[0]) : "") + t(299, 98, "hoje") + `</svg>`
    + vt("custo", { cab: ["dia", ...SQS.map((k) => ROOMS[k].name), "total"], linhas: hist.map((d) => [dia(d), ...SQS.map((k) => fmtUsd(d[k]?.cost_usd || 0)), fmtUsd(tot(d))]) });
}
// 24 barras (uma por hora no fuso)
function barras24(v, cls, rotulo, un) {
  const esc = A.esc, max = Math.max(1, ...v), t = (x, s) => `<text x="${x}" y="68" font-size="7" font-family="ui-monospace,monospace">${s}</text>`;
  return `<svg class="chart" viewBox="0 0 300 70" role="img" aria-label="${esc(rotulo)}">`
    + v.map((n, h) => `<g><title>${esc(`${HH(h)}: ${n} ${un}`)}</title>${rc(h * 12.5 + 1, 4, 10, 54, "#232838", "", "fill:var(--sunk)")}`
      + (n ? rc(h * 12.5 + 1, (58 - (n / max) * 54).toFixed(2), 10, ((n / max) * 54).toFixed(2), "#64b5f6", cls) : "") + `</g>`).join("")
    + t(1, "0 h") + t(76, "6") + t(151, "12") + t(226, "18") + `</svg>`;
}
// tempo a trabalhar e à tua espera, por hora (today.intervalos e espera.itens); servidor sem esses campos: «—»
function meuDia(td, s) {
  const esc = A.esc, tz = A.ctx().TZ, a0 = Date.parse(td.inicio ?? ""), ag = Date.now();
  if (!Array.isArray(td.intervalos) || !(a0 > 0)) return `<p class="dia">a trabalhar hoje: —</p><p class="dia">à tua espera hoje: —</p>`;
  const it = s.espera?.itens || [], th = trabalhoPorHora(td.intervalos, a0, ag, tz), eh = esperaPorHora(it, a0, ag, tz), tw = minutosTrabalho(td.intervalos, a0, ag, tz);
  const me = minutosEspera(it, a0, ag), ds = desdes(it), n = it.length;
  const esp = `${dur(me)} · ${n} ${n === 1 ? "pedido" : "pedidos"}` + (ds.length ? `, o mais antigo há ${ha(ag - Math.min(...ds))}` : "");
  return `<p class="dia">a trabalhar hoje: <b class="mono">${esc(dur(tw))}</b></p>`
    + `<p class="sub">Trabalho por hora (min)</p>` + barras24(th, "b-ok", `Trabalho por hora, em minutos: ${dur(tw)} hoje`, "min") + vt("trabalho", linhas24(th))
    + `<p class="dia">à tua espera hoje: <b class="mono">${esc(esp)}</b></p>`
    + `<p class="sub">À tua espera por hora (pedidos)</p>` + barras24(eh, "b-warn", `Pedidos à tua espera por hora: ${dur(me)} hoje, até ${Math.max(0, ...eh)} ao mesmo tempo`, "pedidos")
    + vt("espera", linhas24(eh)) + `<p class="sub">Só conta o que ainda está à tua espera; as aprovações contam desde as 00:00 do dia em que foram criadas.</p>`;
}
function timelineHtml(tl) {
  const esc = A.esc, { ROOMS, SQS, SQC, TZ } = A.ctx();
  if (!tl?.length) return `<p class="sub">Nenhuma tarefa começou hoje.</p>`;
  const now = minDoDia(Date.now(), TZ);
  return tl.map((x) => {
    const a = minDoDia(x.start, TZ), b = x.end ? minDoDia(x.end, TZ) : now, e = b < a ? 1440 : b, sqk = SQC[x.squad] ? x.squad : SQS[0];
    const when = `${hhmm(x.start, TZ)}–${x.end ? hhmm(x.end, TZ) : "agora"}`;
    return `<div class="tl"><span>${esc(x.project)}<br><span class="sub">${esc(when)}</span></span><span class="tl-t" role="img" aria-label="${esc(`${ROOMS[sqk].name}: ${x.project} ${when}`)}">`
      + `<i style="left:${(a / 14.4).toFixed(2)}%;width:${(Math.max(0, e - a) / 14.4).toFixed(2)}%;background:${SQC[sqk]}"></i></span></div>`;
  }).join("") + `<div class="tl" aria-hidden="true"><span></span><span class="tk"><span>0 h</span><span>6</span><span>12</span><span>18</span><span>24</span></span></div>`;
}
function dayHtml(s) {
  const esc = A.esc, td = s.today, hist = s.history, k = A.sala(), fmtUsd = A.fmtUsd, { ROOMS, SQS, SQC, TZ } = A.ctx();
  // total da sala visível (dia e 7 dias civis, pela hora de início), antes na faixa #totals
  const x = s.totals?.[k], win = (w) => fmtUsd(w.cost_usd) + (w.sem_custo > 0 ? ` (${w.sem_custo} sem custo)` : "");
  const tot = x && ROOMS[k] ? `<p class="sub">${esc(`${ROOMS[k].name}: hoje ${win(x.day)} · 7 dias ${win(x.week)} · custo equivalente`)}</p>` : "";
  if (!td) return tot + `<p class="sub">Sem dados do dia.</p>`;
  const num = (v, l) => `<div><b>${esc(v)}</b><span>${l}</span></div>`;
  return meuDia(td, s) + tot + `<div class="nums">${num(fmtUsd(td.cost_usd), "custo hoje")}${num(td.done ?? "—", "tarefas concluídas")}`
    + `${num(td.hours == null ? "—" : A.fmtNum(td.hours, 1) + " h", "horas de trabalho")}${num(td.top ? `${td.top.project} ${fmtUsd(td.top.cost_usd)}` : "—", "tarefa mais cara")}</div>`
    + (hist?.length ? barsSvg(hist) : "") + `<div class="leg">${SQS.map((q) => `<span><i style="background:${SQC[q]}"></i>${esc(ROOMS[q].name)}</span>`).join("")}</div>`
    + `<p class="sub">Linha do tempo de hoje (${esc(TZ || "hora local")})</p>` + timelineHtml(td.timeline);
}
// Actividade: uma linha por evento «hora · agente · projecto · acção»; 10 e «Mostrar mais (K)»
function feedHtml(s) {
  const esc = A.esc, l = s.feed || [], { SQC, TZ } = A.ctx();
  if (!l.length) return `<p class="sub">Sem actividade recente.</p>`;
  return `<ol class="fd">${(todos ? l : l.slice(0, 10)).map((e) => {
    const x = linhaFeed(e, s, A.quem, TZ);
    const acao = (x.erro && !x.acao.startsWith("⚠") ? "⚠ " : "") + x.acao, txt = `${x.hora} · ${x.agente} · ${x.projecto} · ${acao}`; // title: a ≥ 900 px corta com «…»
    return `<li class="${x.erro ? "bad" : ""}"><i style="background:${SQC[e.squad] || "var(--muted)"}"></i><span title="${esc(txt)}"><span class="mono">${esc(x.hora)}</span> · <b>${esc(x.agente)}</b>`
      + ` · ${esc(x.projecto)} · ${esc(acao)}</span></li>`;
  }).join("")}</ol>` + (!todos && l.length > 10 ? `<button type="button" class="vt" data-f="mais">Mostrar mais (${l.length - 10})</button>` : "");
}

export function render(s) {
  if (!A || !s) return;
  const part = (k) => $(`#fabrica details[data-k="${k}"]`), esc = A.esc;
  const f = document.activeElement?.closest?.("#fabrica [data-f]")?.dataset.f;
  const n = s.espera?.n;
  // [título, valores]: o título na sans e os valores (custos, contagens) em mono
  const sums = { day: ["O meu dia", s.today ? `(${A.fmtUsd(s.today.cost_usd)} hoje)` : ""], feed: ["Actividade", `(${(s.feed || []).length})`],
    wait: ["À tua espera", `(${n ?? "—"})`], usage: ["Consumo da conta", "(estimativa)"] };
  const bodies = { day: dayHtml(s), feed: feedHtml(s), wait: waitHtml(s), usage: usageHtml(s.usage) };
  for (const k in sums) { setHtml(part(k).querySelector("summary"), esc(sums[k][0]) + (sums[k][1] ? ` <span class="mono">${esc(sums[k][1])}</span>` : "")); setHtml(part(k).querySelector("div"), bodies[k]); }
  if (f && !document.activeElement?.closest?.("#fabrica [data-f]")) $(`#fabrica [data-f="${f}"]`)?.focus({ preventScroll: true });
}
