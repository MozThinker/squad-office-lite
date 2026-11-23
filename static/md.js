// Markdown das mensagens do assistente (Conversa, porte do Office): conversor próprio e pequeno. md() é pura e devolve uma árvore
// {t, a?, c?} em que as strings são sempre texto (nunca HTML); materializar() cria os nós com createElement/append.
// Tudo em tempo linear (sem regexes com retrocesso em cadeia): textos de 16 000 caracteres vindos do motor.

// só http(s) absolutos; o resto (javascript:, data:, «//x», espaços ou controlos antes do esquema) fica texto
export function linkSeguro(url) {
  const s = String(url ?? "");
  if (!/^https?:\/\//i.test(s)) return null;
  try { const u = new URL(s); return u.protocol === "http:" || u.protocol === "https:" ? u.href : null; } catch { return null; }
}

// realce mínimo do código: comentários (#, //, /* */) → span.md-c; strings → span.md-s
const RE_COD = /(\/\*[\s\S]*?(?:\*\/|$)|\/\/[^\n]*|#[^\n]*)|("(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?)/g;
export function realce(cod) {
  const s = String(cod ?? ""), out = [];
  let i = 0;
  for (const m of s.matchAll(RE_COD)) {
    if (m.index > i) out.push(s.slice(i, m.index));
    out.push({ t: "span", a: { class: m[1] ? "md-c" : "md-s" }, c: [m[0]] });
    i = m.index + m[0].length;
  }
  if (i < s.length) out.push(s.slice(i));
  return out;
}

// ---- em linha: `código`, **negrito**, *itálico*, [texto](url), ![alt](url) e autolinks
const RE_AUTO = /https?:\/\/[^\s<>()]+/y;
// host que o texto de um link aparenta (forma de URL ou de domínio: sem espaços e com um ponto), ou null
const hostDoTexto = (t) => {
  const s = t.trim();
  if (!/[.\u3002\uff0e\uff61]/.test(s) || /\s/.test(s)) return null; // também os pontos que o URL normaliza (。．｡)
  try { return new URL(/^[a-z][a-z\d+.-]*:\/\//i.test(s) ? s : "http://" + s).hostname; } catch { return null; }
};
const palavra = (c) => /[\p{L}\p{N}]/u.test(c ?? "");
function linha(s, nivel = 0) {
  const out = [], prox = new Map();
  let i = 0, ini = 0;
  // índice do próximo `tok` a partir de `de`, com memória (cada pesquisa começa depois da anterior: linear)
  const ate = (tok, de) => {
    const p = prox.get(tok);
    if (p !== undefined && (p === -1 || p >= de)) return p;
    const j = s.indexOf(tok, de);
    prox.set(tok, j);
    return j;
  };
  const poe = (no, fim) => { if (i > ini) out.push(s.slice(ini, i)); out.push(no); i = ini = fim; };
  while (i < s.length) {
    const c = s[i];
    if (c === "`") {
      const j = ate("`", i + 1);
      if (j > i + 1) { poe({ t: "code", c: [s.slice(i + 1, j)] }, j + 1); continue; }
    } else if (c === "[" || (c === "!" && s[i + 1] === "[")) {
      const img = c === "!", a = i + (img ? 1 : 0), f = ate("]", a + 1);
      if (f > a && s[f + 1] === "(") {
        const g = ate(")", f + 2);
        if (g > f) {
          const txt = s.slice(a + 1, f), url = s.slice(f + 2, g).trim().split(/\s/)[0], href = linkSeguro(url);
          const rot = img ? "imagem: " + (txt || url) : txt || url;
          poe(href ? { t: "a", a: { href }, c: [rot] } : rot, g + 1);
          // S1: texto com forma de URL/domínio que aponta para outro host → mostra o host real a seguir
          const ht = href && hostDoTexto(txt), hr = ht && new URL(href).hostname;
          if (ht && ht !== hr) out.push(` (${hr})`);
          continue;
        }
      }
    } else if (nivel < 1 && (c === "*" || c === "_") && s[i + 1] === c) {
      const tok = c + c, j = ate(tok, i + 2);
      if (j > i + 2) { poe({ t: "b", c: linha(s.slice(i + 2, j), 1) }, j + 2); continue; }
    } else if (nivel < 2 && (c === "*" || (c === "_" && !palavra(s[i - 1])))) {
      const j = ate(c, i + 1);
      if (j > i + 1 && s[i + 1] !== " " && s[j - 1] !== " " && (c === "*" || !palavra(s[j + 1]))) {
        poe({ t: "i", c: linha(s.slice(i + 1, j), 2) }, j + 1); continue;
      }
    } else if (c === "h" && !palavra(s[i - 1])) {
      RE_AUTO.lastIndex = i;
      const m = RE_AUTO.exec(s);
      if (m) {
        let f = m[0].length;
        while (f && ".,;:!?'\"".includes(m[0][f - 1])) f--;
        const u = m[0].slice(0, f), href = linkSeguro(u);
        if (href) { poe({ t: "a", a: { href }, c: [u] }, i + u.length); continue; }
      }
    }
    i++;
  }
  if (i > ini) out.push(s.slice(ini, i));
  return out;
}

// ---- blocos
// sem «$» e com [^]* (as linhas já vêm partidas por \n): «.» pára em U+2028/U+2029 e o «$» falhado dava retrocesso quadrático
const RE_LISTA = /^( *)([-*+]|\d{1,9}[.)])[ \t]+([^]*)/;
const RE_TIT = /^(#{1,6})[ \t]+([^]*)/;
const celulas = (l) => {
  let s = l.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((x) => x.trim());
};
const separador = (l) => l.includes("-") && celulas(l).every((x) => /^:?-+:?$/.test(x));
// tabela GFM: linha com «|» e a seguinte é o separador com o mesmo número de células
const tabela = (l, sep) => sep !== undefined && l.includes("|") && separador(sep) && celulas(sep).length === celulas(l).length;
const regra = (l) => { const t = l.replace(/[ \t]/g, ""); return t.length >= 3 && /^(?:-+|\*+|_+)$/.test(t) && !/^ {4}/.test(l); };
const cerca = (l) => /^ {0,3}(```|~~~)/.exec(l);
const vazia = (l) => l.trim() === "";
const semCardinais = (s) => { let f = s.length; while (f && (s[f - 1] === "#" || s[f - 1] === " " || s[f - 1] === "\t")) f--; return s.slice(0, f); };

export function md(texto) {
  const ls = String(texto ?? "").replace(/\r\n?/g, "\n").split("\n"), out = [];
  let i = 0;
  const inicioDeBloco = (k) => cerca(ls[k]) || RE_TIT.test(ls[k]) || RE_LISTA.test(ls[k]) || ls[k].startsWith(">") || regra(ls[k])
    || tabela(ls[k], ls[k + 1]);
  while (i < ls.length) {
    const l = ls[i];
    let m;
    if (vazia(l)) { i++; continue; }
    if ((m = cerca(l))) { // bloco de código; por fechar = fecha no fim (CA-22)
      const corpo = [];
      for (i++; i < ls.length && !ls[i].trimStart().startsWith(m[1]); i++) corpo.push(ls[i]);
      i++;
      out.push({ t: "pre", c: [{ t: "code", c: realce(corpo.join("\n")) }] });
    } else if ((m = RE_TIT.exec(l))) {
      out.push({ t: "h" + Math.min(m[1].length, 3), c: linha(semCardinais(m[2])) });
      i++;
    } else if (regra(l)) { out.push({ t: "hr" }); i++; }
    else if (l.startsWith(">")) {
      const xs = [];
      for (; i < ls.length && ls[i].startsWith(">"); i++) xs.push(ls[i].replace(/^>+ ?/, ""));
      out.push({ t: "blockquote", c: [{ t: "p", c: linha(xs.join("\n")) }] });
    } else if (tabela(l, ls[i + 1])) {
      const cab = celulas(l), n = cab.length, linhaT = (cs, t) => ({ t: "tr", c: Array.from({ length: n }, (_, k) => ({ t, c: linha(cs[k] ?? "") })) });
      const rows = [linhaT(cab, "th")];
      for (i += 2; i < ls.length && !vazia(ls[i]) && ls[i].includes("|"); i++) rows.push(linhaT(celulas(ls[i]), "td"));
      out.push({ t: "table", c: rows });
    } else if (RE_LISTA.test(l)) { // listas, aninhamento de 1 nível
      let lista = null, li = null, sub = null;
      for (; i < ls.length; i++) {
        if (vazia(ls[i]) && i + 1 < ls.length && RE_LISTA.test(ls[i + 1])) continue; // itens separados por linhas vazias
        if (!(m = RE_LISTA.exec(ls[i]))) break;
        const tipo = /\d/.test(m[2]) ? "ol" : "ul", item = { t: "li", c: linha(m[3]) };
        // numerada que não começa em 1 (p. ex. partida por um bloco de código) guarda o número do 1.º item
        const nova = () => { const n = parseInt(m[2], 10); return tipo === "ol" && n !== 1 ? { t: tipo, a: { start: n }, c: [] } : { t: tipo, c: [] }; };
        if (m[1].length >= 2 && li) {
          if (!sub || sub.t !== tipo) li.c.push((sub = nova()));
          sub.c.push(item);
        } else {
          if (!lista || lista.t !== tipo) out.push((lista = nova()));
          lista.c.push((li = item)); sub = null;
        }
      }
    } else { // parágrafo: linhas seguidas até uma vazia ou outro bloco
      const xs = [l];
      for (i++; i < ls.length && !vazia(ls[i]) && !inicioDeBloco(i); i++) xs.push(ls[i]);
      out.push({ t: "p", c: linha(xs.join("\n")) });
    }
  }
  return out;
}

// árvore → DocumentFragment: só createElement, append (texto) e setAttribute de href/class/start; nada de innerHTML
export function materializar(nos, doc = globalThis.document) {
  const frag = doc.createDocumentFragment();
  const poe = (pai, xs) => {
    for (const n of xs || []) {
      if (typeof n === "string") { pai.append(n); continue; }
      const e = doc.createElement(n.t);
      if (n.a?.class) e.setAttribute("class", n.a.class);
      if (Number.isSafeInteger(n.a?.start) && n.a.start >= 0) e.setAttribute("start", String(n.a.start));
      if (n.a?.href) { e.setAttribute("href", n.a.href); e.setAttribute("rel", "noopener noreferrer"); e.setAttribute("target", "_blank"); }
      poe(e, n.c);
      pai.append(e);
    }
  };
  poe(frag, nos);
  return frag;
}
