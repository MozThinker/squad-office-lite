// Arranque da página da Conversa (conversa.html, origem própria servida pelo guardião; CSP «script-src 'self'», sem JS
// inline): os sons do som.js e o iniciar() do conversa.js. O AudioContext só nasce num gesto (iOS). As preferências do som
// são as desta origem (a do Office é outra): sem nada guardado, ligado e volume médio.
import { iniciar } from "./conversa.js";
import { criarSom, lerPrefs } from "./som.js";

let actx = null;
const ls = () => { try { return globalThis.localStorage; } catch { return null; } };
const prefs = () => lerPrefs(ls());
const desbloquear = () => {
  if (!prefs().on) return;
  try { actx ||= new (globalThis.AudioContext || globalThis.webkitAudioContext)(); actx.resume().catch(() => {}); } catch { /* sem áudio */ }
};
for (const n of ["click", "touchend", "keydown"]) addEventListener(n, desbloquear, true);
const tocar = criarSom({ ctx: () => actx, agora: Date.now, visivel: () => !document.hidden, prefs, vibrar: (p) => navigator.vibrate?.(p) });
iniciar({ tocar });
