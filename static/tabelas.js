// «Ver tabela» dos gráficos de «O meu dia» (v3, CA-30). Só carregado no 1.º clique (import() no fabrica.js).
// tabela(tipo, {cab?, linhas}) devolve a <table> com <caption> e os mesmos números do gráfico (texto já formatado, aqui só escapado).
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const T = { trabalho: ["Trabalho por hora (minutos)", ["hora", "minutos"]], espera: ["À tua espera por hora (pedidos)", ["hora", "pedidos"]],
  custo: ["Custo dos últimos 30 dias por squad (custo equivalente)", ["dia", "total"]] };

export function tabela(tipo, dados) {
  const [cap, cab0] = T[tipo], cab = dados.cab || cab0;
  return `<div class="tw"><table><caption>${esc(cap)}</caption><tr>${cab.map((c) => `<th scope="col">${esc(c)}</th>`).join("")}</tr>`
    + dados.linhas.map(([a, ...r]) => `<tr><th scope="row">${esc(a)}</th>${r.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("") + `</table></div>`;
}
