// Aprovações que lançam (ADR-024): o que uma linha de «## Decisões» diz e o rótulo do botão. Script clássico sem DOM: o
// index.html carrega-o com <script src> antes do script principal; os testes em node lêem-no de globalThis.LANCA.
// O servidor já tirou a marca «[lança: <slug>]» do texto (d.lanca = slug) e diz d.lancamento: null (sem marca),
// «desligado», «ligado», «espera» ou «lancada». Desconhecido = desligado (aprovar só regista, a omissão do kit).
globalThis.LANCA = (() => {
  const SLUG = /^[a-z0-9][a-z0-9-]{0,47}$/;
  const slugDe = (d) => (typeof d?.lanca === "string" && SLUG.test(d.lanca) ? d.lanca : null);
  // estado efectivo: «ligado» só com a marca e o sinal do kit; o resto é «desligado» ou null (sem marca)
  const estado = (d) => {
    if (!slugDe(d)) return null;
    return ["ligado", "espera", "lancada"].includes(d.lancamento) ? d.lancamento : "desligado";
  };
  // o selo de uma linha em aberto: { icone, texto, slug, classe } (texto sem cor: ícone e palavras dizem tudo)
  function selo(d) {
    const e = estado(d), slug = slugDe(d);
    if (e === "ligado") return { icone: "▶", texto: "Aprovar lança a tarefa", slug, classe: "lanca-on" };
    if (e) return { icone: "⏸\uFE0E", texto: "Lançamento desligado: aprovar só regista", slug, classe: "lanca-off" };
    return { icone: "", texto: "Aprovar só regista", slug: null, classe: "lanca-reg" };
  }
  // o botão de aprovar: rótulo visível e o nome para o leitor de ecrã (null = o próprio rótulo)
  function botao(d) {
    const valor = d?.valor ? " · " + d.valor : "";
    if (estado(d) === "ligado") return { rotulo: "Aprovar e lançar" + valor, aria: `Aprovar e lançar a tarefa ${slugDe(d)}${valor}` };
    return { rotulo: "Aprovar" + valor, aria: null };
  }
  // a linha já aprovada: «Aprovado: tarefa lançada», «Aprovado: à espera do lançamento» ou só «Aprovado»
  function aprovado(d) {
    const e = estado(d);
    return e === "lancada" ? "Aprovado: tarefa lançada" : e === "espera" || e === "ligado" ? "Aprovado: à espera do lançamento" : "Aprovado";
  }
  const lanca = (d) => estado(d) === "ligado";
  return { estado, selo, botao, aprovado, lanca };
})();
