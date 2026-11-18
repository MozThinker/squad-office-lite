// Ajuda ao utilizador (0.1.3): rótulos de estado, dicas de erro, primeiros passos e faixa do runner. Só funções puras, sem DOM;
// importado pelo quadro.js e por import() no index.html. Os textos são fixos; os comandos são os mesmos do `doctor`.
// As dicas trazem `…` (comando): quem desenha passa-as por md() (que escapa tudo antes de formatar).

const tem = (m, k) => typeof k === "string" && Object.hasOwn(m, k);

// estados novos só na UI: os dados (tasks, API, push) ficam com os valores antigos
export const ROTULOS = { "parcial (PR draft)": "PR aberto em rascunho", incompleta: "Terminou sem PR" };
export const rotuloEstado = (s) => (tem(ROTULOS, s) ? ROTULOS[s] : s);

// diálogo «Lançar»: os valores enviados continuam feat/fix e task/goal; fora destes mostra-se o valor cru da config
export const TIPOS = { feat: "funcionalidade nova", fix: "correcção" };
export const TAMANHOS = { task: "tarefa curta (até 3 h)", goal: "objectivo com plano (até 8 h)" };
export const rotuloDe = (m, k) => (tem(m, k) ? m[k] : k);

// CA-B7.3: dica pelo prefixo do resultado (o texto gravado pelo runner não muda); null = só o resultado
// modo "docker" (0.1.4, AC-o1): os comandos correm no PC, na pasta do compose.yaml
export function dicaErro(estado, resultado, modo = "vps") {
  const r = typeof resultado === "string" ? resultado : "", dk = modo === "docker";
  if (r.startsWith("credenciais do Claude")) return dk ? `Corre \`${COMANDOS_DOCKER.credencial}\` no PC (na pasta do \`compose.yaml\`).`
    : "Corre `./install.sh --credenciais` (na pasta `~/squad-office-lite`).";
  if (estado === "incompleta") return dk ? "Terminou sem PR: confirma `docker compose exec runner gh auth status`." : "Terminou sem PR: confirma `gh auth status`.";
  if (r.startsWith("runner.settings inválido")) return "Corrige o ficheiro de `runner.settings` (README, «Runner»).";
  return null;
}

export const PARADO_MS = 180_000; // 3 min sem o runner pegar no pedido / sem sinal do runner
const desde = (iso, agoraMs) => agoraMs - Date.parse(iso ?? ""); // NaN se a data não presta

// desvio do relógio do browser (ms a somar ao Date.now()) pela hora do servidor numa resposta recebida em recebidoMs;
// sem hora válida: 0 (fica o relógio do browser)
export function desvioRelogio(servidorIso, recebidoMs) {
  const d = Date.parse(servidorIso ?? "") - recebidoMs;
  return Number.isFinite(d) ? d : 0;
}

// CA-C12.1: pedido de lançamento à espera há mais de 3 min
export const lancarParado = (l, agoraMs) => l?.estado === "a-lancar" && desde(l.pedido_em, agoraMs) > PARADO_MS;

const semSinal = (runner, agoraMs) => !runner || !(desde(runner.em, agoraMs) <= PARADO_MS);

// HH:MM no fuso da config (fuso desconhecido do browser: hora local)
function horaMin(iso, fuso) {
  const o = { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
  try { return new Date(iso).toLocaleTimeString("pt-PT", { ...o, timeZone: fuso }); } catch { return new Date(iso).toLocaleTimeString("pt-PT", o); }
}

// CA-D15.8: null (tudo bem) ou a frase da faixa
export function faixaRunner(runner, agoraMs, fuso) {
  if (!runner || Number.isNaN(Date.parse(runner.em ?? ""))) return "O runner ainda não deu sinal";
  return semSinal(runner, agoraMs) ? `O runner não dá sinal desde ${horaMin(runner.em, fuso)}` : null;
}

export const COMANDOS = {
  runner: "systemctl --user enable --now office-runner.timer",
  claude: "curl -fsSL https://claude.ai/install.sh | bash",
  credencial: "cd ~/squad-office-lite && ./install.sh --credenciais",
  gh: "gh auth login",
  doctor: "python3 ~/squad-office-lite/runner/office_runner.py doctor",
};
// modo Docker (0.1.4, AC-o1): no PC, na pasta do compose.yaml
export const COMANDOS_DOCKER = {
  runner: "docker compose up -d",
  claude: "docker compose pull; docker compose up -d",
  credencial: "docker compose run --rm configurar --credenciais",
  gh: "docker compose run --rm configurar --gh",
  doctor: "docker compose exec runner doctor",
};
// modo do GET api/config; ausente ou outro: vps
export const comandos = (modo) => (modo === "docker" ? COMANDOS_DOCKER : COMANDOS);
// onde se correm os comandos (para frases «Corre … no servidor»)
export const ondeCorre = (modo) => (modo === "docker" ? "no PC" : "no servidor");
const PASSOS = { runner: "Runner a dar sinal", claude: "claude instalado", credencial: "Credencial do Claude", gh: "Sessão do gh" };

// CA-D15.7: null depois da 1.ª tarefa entregue; senão os 4 passos (runner sem sinal: os outros três ficam ✗ «desconhecido»)
export function primeirosPassos(state, agoraMs, modo = "vps") {
  if (!state || state.tarefa_entregue === true) return null;
  const r = state.runner, vivo = !semSinal(r, agoraMs), C = comandos(modo);
  const ok = { runner: vivo, claude: vivo && r.claude === "ok", credencial: vivo && r.credencial === "ok", gh: vivo && r.gh === "ok" };
  return Object.keys(PASSOS).map((chave) => ({
    chave, ok: ok[chave],
    rotulo: PASSOS[chave] + (chave !== "runner" && !vivo ? " (desconhecido)" : ""),
    comando: ok[chave] ? null : chave === "credencial" && vivo && r.credencial === "modo" ? C.doctor : C[chave], // modo: o doctor diz o ficheiro real
  }));
}

// CA-C11.1: projecto do cartão novo
export function projectoNovo(filtro, cfg) {
  const l = cfg?.lancar?.projectos;
  return Array.isArray(filtro) && filtro.length === 1 ? filtro[0] : Array.isArray(l) && l.length === 1 ? l[0] : "outro";
}

// ---- v3 (CA-17): como acabou a execução. «N recusas: Bash, …» e a dica (a mesma do README «O que fazer se…»)
export const DICA_RECUSAS = "Para o runner deixar a squad usar estas ferramentas, acrescenta a regra a `runner.ferramentas` no `office.yaml` (README, «O que fazer se…»).";
export function recusasTexto(fim) {
  const n = fim?.recusas;
  if (!(Number.isInteger(n) && n > 0)) return null;
  const f = Array.isArray(fim.ferramentas) ? fim.ferramentas.filter((x) => typeof x === "string") : [];
  return `${n} ${n === 1 ? "recusa" : "recusas"}${f.length ? ": " + f.join(", ") : ""}`;
}
// erro da API no fim da execução: «erro da API (HTTP 429, api_error)»; null sem erro
export function erroApiTexto(fim) {
  if (fim?.erro !== true) return null;
  const d = [Number.isInteger(fim.http) ? "HTTP " + fim.http : "", typeof fim.motivo === "string" && fim.motivo !== "completed" ? fim.motivo : ""].filter(Boolean);
  return "a execução acabou com erro" + (d.length ? ` (${d.join(", ")})` : "");
}

// ---- v3 (CA-8): ajuda do «?» com a legenda dos estados (ROTULOS), os sinais, os contadores e os atalhos. Só o abrir() mexe no DOM
const COR = [["var(--ok)", "a correr"], ["var(--accent)", "concluída"], ["var(--warn)", `parcial, à espera (${ROTULOS["parcial (PR draft)"]}) ou a demorar`],
  ["var(--bad)", `falhou, morreu, interrompida ou ${ROTULOS.incompleta.toLowerCase()}`]];
const SINAIS = [["z", "a secretária já trabalhou nesta execução e está parada"], ["preso?", "sem sinal há 10 min (num terminal, há 25 min)"],
  ["à espera do terminal", "um comando em primeiro plano sem resposta: a partir de 10 min a âmbar, aos 25 min «preso?»"],
  ["CI vermelho", "os testes do PR desta tarefa falharam no GitHub"]];
const CONT = [["salas", "squads com trabalho a correr"], ["agentes", "líderes e subagentes vivos nas tarefas a correr"],
  ["a trabalhar", "quem está a trabalhar agora"], ["subagentes", "subagentes vivos"],
  ["à tua espera", "aprovações em aberto e PRs à espera de merge"], ["falhas", "tarefas que morreram e execuções com erro (3 dias)"]];
const TECLAS = [["P", "abre o pedido mais antigo à tua espera"], ["?", "abre esta ajuda"], ["/", "no Escritório: pesquisa em «Agentes»"],
  ["Esc", "fecha um painel ou diálogo"]];
let dlg = null;
export function abrir({ esc }) {
  if (!dlg) {
    const dl = (l, k) => `<dl class="aj">${l.map(([a, b]) => `<dt>${k ? `<kbd>${esc(a)}</kbd>` : esc(a)}</dt><dd>${esc(b)}</dd>`).join("")}</dl>`;
    dlg = document.createElement("dialog");
    dlg.id = "ajuda";
    dlg.className = "pn";
    dlg.setAttribute("aria-labelledby", "aj-h");
    dlg.innerHTML = `<div class="rd-top"><h2 id="aj-h">Ajuda</h2><button type="button" data-a="fechar">fechar</button></div><div class="aj-b">`
      + `<h3>Cores de estado</h3><ul class="aj-c">${COR.map(([c, t]) => `<li><i style="background:${c}"></i><span>${esc(t)}</span></li>`).join("")}</ul>`
      + `<h3>Sinais no escritório</h3>${dl(SINAIS)}<h3>Contadores do topo</h3>${dl(CONT)}`
      + `<h3>Atalhos</h3>${dl(TECLAS, true)}<p class="sub">Os atalhos não actuam dentro de campos de texto, com um diálogo aberto nem com Ctrl, Alt ou Cmd.</p></div>`;
    document.body.append(dlg);
    dlg.addEventListener("click", (e) => e.target.closest('[data-a="fechar"]') && dlg.close());
    dlg.addEventListener("close", () => dlg._origem?.focus?.());
  }
  dlg._origem = document.activeElement;
  dlg.showModal();
  dlg.querySelector('[data-a="fechar"]').focus();
}
