// Rótulos da actividade ao vivo (o estado só traz kind/tool/error). Script clássico sem DOM: o index.html carrega-o com
// <script src> antes do script principal (os balões e o «Agora:» nunca ficam vazios); os módulos e os testes em node lêem-no
// de globalThis.ACTIVIDADE (sem ele, a Fábrica e a lista lateral continuam a desenhar-se, só sem rótulos).
globalThis.ACTIVIDADE = (() => {
  const ACT = { ler: ["📖", "a ler"], editar: ["✏️", "a editar"], terminal: ["💻", "terminal"], testes: ["🧪", "testes"], web: ["🌐", "web"],
    delegar: ["🤝", "a delegar"], pensar: ["💭", "a pensar"], pr_aberto: ["🎉", "PR aberto"], pr_integrado: ["✅", "PR integrado"],
    push: ["⬆️", "push"], ci_vermelho: ["🔴", "CI vermelho"], ci_verde: ["🟢", "CI verde"] };
  const GH_KIND = /^(pr_|push$|ci_)/;
  const actText = (a) => (!a ? "" : GH_KIND.test(a.kind) ? ACT[a.kind].join(" ") : a.error ? `⚠️ erro em ${a.tool || a.kind}`
    : ACT[a.kind] ? ACT[a.kind].join(" ") : `🔧 a usar ${a.tool || "outra"}`);
  return { ACT, GH_KIND, actText };
})();
