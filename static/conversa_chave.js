// Chave da Conversa (ADR-023, contrato §2.2–2.3; revisão G2): o código, de uso único, só vive na memória do browser enquanto se
// deriva o verificador (PBKDF2 com o sal_office) que regista este browser. A chave K é aleatória, nasce aqui (32 bytes), vai uma
// vez para o guardião no registo e fica só como CryptoKey HMAC não extraível em IndexedDB, com o kid. As entradas seguintes
// assinam o desafio do guardião; os pedidos assinam o canónico. Tudo injectável (subtle, indexedDB, aleatório) para os testes.

export const CODIGO = /^[A-Z2-7]{24}$/;
export const FORA = new Set(["v", "criado", "ip", "ua", "assinatura"]);
const BD = "office-conversa", LOJA = "chaves", CHAVE = "hmac", enc = new TextEncoder();

// maiúsculas, sem «-» nem espaços; null se não for um código válido (24 base32)
export function normalizarCodigo(s) {
  const c = String(s ?? "").toUpperCase().replace(/[-\s]/g, "");
  return CODIGO.test(c) ? c : null;
}
export const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
export const deHex = (h) => new Uint8Array(String(h).match(/../g)?.map((x) => parseInt(x, 16)) || []);

// chaves ordenadas em todos os níveis (as listas mantêm a ordem); no topo sai o que não se assina
const ordenar = (v) => Array.isArray(v) ? v.map(ordenar) : v && typeof v === "object"
  ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, ordenar(v[k])])) : v;
export const canonico = (d) => JSON.stringify(ordenar(Object.fromEntries(Object.entries(d).filter(([k]) => !FORA.has(k)))));

const base = (subtle, codigo) => subtle.importKey("raw", enc.encode(codigo), "PBKDF2", false, ["deriveBits"]);
const pb = (sal, it) => ({ name: "PBKDF2", hash: "SHA-256", salt: deHex(sal), iterations: it });

// verificador = PBKDF2-HMAC-SHA256(código, sal_office, iterações, 32) em hex
export async function verificador(subtle, codigo, sal, it) {
  return hex(await subtle.deriveBits(pb(sal, it), await base(subtle, codigo), 256));
}
const rndOmissao = (a) => globalThis.crypto.getRandomValues(a);
// chave nova deste browser: {kid (16 hex), hexa (os 32 bytes em hex, só para o corpo do registo), chave (CryptoKey HMAC não
// extraível, só para assinar)}; os bytes zeram-se logo a seguir à importação
export async function novaChave(subtle, rnd = rndOmissao) {
  const bytes = rnd(new Uint8Array(32)), hexa = hex(bytes);
  try { return { kid: aleatorioHex(16, rnd), hexa, chave: await subtle.importKey("raw", bytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]) }; }
  finally { bytes.fill(0); }
}
export const assinar = async (subtle, k, d) => hex(await subtle.sign("HMAC", k, enc.encode(canonico(d))));
// entrada por desafio–resposta: HMAC-SHA256(K, "entrar\n" + desafio) em hex
export const assinarEntrada = async (subtle, k, desafio) => hex(await subtle.sign("HMAC", k, enc.encode(`entrar\n${desafio}`)));

// pid = 16 hex aleatórios; t = ms Unix; devolve o pedido com pid, t e assinatura
export const aleatorioHex = (n, rnd = rndOmissao) => hex(rnd(new Uint8Array(n / 2)));
export async function assinado(subtle, k, campos, agora = Date.now(), rnd) {
  const d = { ...campos, pid: aleatorioHex(16, rnd), t: Math.floor(agora) };
  return { ...d, assinatura: await assinar(subtle, k, d) };
}
// id de uma conversa nova (o browser gera-o): AAAAMMDD-HHMMSS-<6 hex>, na hora local
export function novoId(agora = Date.now(), rnd) {
  const d = new Date(agora), p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${aleatorioHex(6, rnd)}`;
}

// IndexedDB: {chave, kid} (a chave do código de antes, {chave, sal}, já não serve: pede-se o registo)
function bd(idb) {
  return new Promise((ok, falha) => {
    const r = idb.open(BD, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(LOJA);
    r.onsuccess = () => ok(r.result);
    r.onerror = () => falha(r.error);
  });
}
async function op(idb, modo, f) {
  const db = await bd(idb);
  try {
    return await new Promise((ok, falha) => {
      const tx = db.transaction(LOJA, modo), r = f(tx.objectStore(LOJA));
      tx.oncomplete = () => ok(r?.result);
      tx.onerror = tx.onabort = () => falha(tx.error);
    });
  } finally { db.close(); }
}
export const KID = /^[0-9a-f]{16}$/;
export const guardarChave = (idb, k, kid) => op(idb, "readwrite", (s) => s.put({ chave: k, kid }, CHAVE));
// {chave, kid} ou null
export async function lerChave(idb) {
  try { const v = await op(idb, "readonly", (s) => s.get(CHAVE)); return v?.chave && KID.test(v.kid ?? "") ? { chave: v.chave, kid: v.kid } : null; } catch { return null; }
}
export const apagarChave = (idb) => op(idb, "readwrite", (s) => s.delete(CHAVE)).catch(() => {});
