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
export function dicaErro(estado, resultado) {
  const r = typeof resultado === "string" ? resultado : "";
  if (r.startsWith("credenciais do Claude")) return "Corre `./install.sh --credenciais` (na pasta `~/squad-office-lite`).";
  if (estado === "incompleta") return "Terminou sem PR: confirma `gh auth status`.";
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
const PASSOS = { runner: "Runner a dar sinal", claude: "claude instalado", credencial: "Credencial do Claude", gh: "Sessão do gh" };

// CA-D15.7: null depois da 1.ª tarefa entregue; senão os 4 passos (runner sem sinal: os outros três ficam ✗ «desconhecido»)
export function primeirosPassos(state, agoraMs) {
  if (!state || state.tarefa_entregue === true) return null;
  const r = state.runner, vivo = !semSinal(r, agoraMs);
  const ok = { runner: vivo, claude: vivo && r.claude === "ok", credencial: vivo && r.credencial === "ok", gh: vivo && r.gh === "ok" };
  return Object.keys(PASSOS).map((chave) => ({
    chave, ok: ok[chave],
    rotulo: PASSOS[chave] + (chave !== "runner" && !vivo ? " (desconhecido)" : ""),
    comando: ok[chave] ? null : chave === "credencial" && vivo && r.credencial === "modo" ? COMANDOS.doctor : COMANDOS[chave], // modo: o doctor diz o ficheiro real
  }));
}

// CA-C11.1: projecto do cartão novo
export function projectoNovo(filtro, cfg) {
  const l = cfg?.lancar?.projectos;
  return Array.isArray(filtro) && filtro.length === 1 ? filtro[0] : Array.isArray(l) && l.length === 1 ? l[0] : "outro";
}
