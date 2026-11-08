// Música de fundo do Office: síntese no browser (Web Audio), sem ficheiros de áudio nem bibliotecas.
// Funções puras (sala → estilo, camadas, jingle, preferências) + motor createEngine({ ctx }).
// Importar não toca no DOM nem no áudio: o AudioContext só nasce em createEngine (ou vem injectado).

// todos os afinamentos ficam aqui; o motor não tem números de música
export const MUSIC_CONFIG = {
  room: "lofi", fallback: "piano",          // qualquer sala → room; sem sala → fallback
  maxLayers: 4, defaultVolume: 30,         // volume 0–100
  master: 0.5,                             // ganho com volume 100, antes do limitador
  limiter: { threshold: -6, knee: 0, ratio: 20, attack: 0.003, release: 0.25 },
  fades: { room: 1, toggle: 0.3, layer: 0.5 },          // segundos
  scheduler: { intervalMs: 25, lookahead: 0.1 },        // timer e janela (s)
  attack: 0.005, release: 0.08,            // envelope por defeito (s): nunca abaixo de 5 ms, para não estalar
  humanize: 0.15,                          // variação aleatória do ganho de cada nota (0–1)
  jingle: { notes: [0, 4, 7, 12], step: 0.12, tail: 0.5, gain: 0.16, oct: 1, voice: "bell" }, // semitons acima de style.key
  // FM (ratio/index): piano eléctrico e teclas; o resto é oscilador ou ruído filtrado. decay = constante do decaimento (s)
  voices: {
    ep: { ratio: 1, index: 1.8, decay: 1.4 },
    keys: { ratio: 1, index: 0.9, decay: 2.5 },
    pad: { ratio: 1, index: 0.3, decay: 8, attack: 0.8, release: 1.2 },
    bell: { ratio: 4, index: 1.2, decay: 0.5 },
    bass: { wave: "triangle", filter: ["lowpass", 400], decay: 1.2, attack: 0.008 },
    kick: { wave: "sine", sweep: [120, 45, 0.12], len: 0.3, decay: 0.12 },
    snare: { noise: 1, filter: ["bandpass", 1800, 0.8], len: 0.18, decay: 0.06 },
    rim: { wave: "triangle", freq: 1250, len: 0.05, decay: 0.015 },
    hat: { noise: 1, filter: ["highpass", 7000], len: 0.05, decay: 0.015 },
    shaker: { noise: 1, filter: ["bandpass", 5000, 1.5], len: 0.07, decay: 0.025, attack: 0.015 },
  },
  // grelha de 16 passos por compasso, um acorde por compasso; loop = chords.length compassos.
  // track: { layer 0 (base) ou 1–4, voice, gain, hits: passos, dur: passos, oct, notes: "chord"|"root"|"root5"|"arp"|[midi|null] }
  // lp = lowpass do estilo (Hz); echo = linhas de atraso com realimentação (reverb simples)
  styles: {
    lofi: {
      bpm: 72, swing: 0.15, key: 60, lp: 3000, echo: { times: [0.625], fb: 0.25, lp: 2000, mix: 0.15 },
      chords: [[53, 57, 60, 64], [52, 55, 59, 62], [50, 53, 57, 60], [48, 52, 55, 59]], roots: [41, 40, 38, 36],
      tracks: [
        { layer: 0, voice: "ep", notes: "chord", hits: [0, 10], dur: 6, gain: 0.07 },
        { layer: 0, voice: "kick", hits: [0, 7, 10], gain: 0.45 },
        { layer: 0, voice: "snare", hits: [4, 12], gain: 0.16 },
        { layer: 1, voice: "bass", notes: "root", hits: [0, 7, 10], dur: 3, gain: 0.3 },
        { layer: 2, voice: "hat", hits: [0, 2, 4, 6, 8, 10, 12, 14], gain: 0.05 },
        { layer: 2, voice: "hat", hits: [3, 7, 11, 15], gain: 0.025 },
        { layer: 3, voice: "ep", hits: [0, 3, 6, 10], dur: 4, gain: 0.1,
          notes: [76, null, 72, 69, 74, null, 71, 67, 72, null, 69, 65, 67, 71, 72, null] },
        { layer: 4, voice: "keys", notes: "arp", oct: 1, hits: [2, 6, 10, 14], dur: 4, gain: 0.05 },
      ],
    },
    bossa: {
      bpm: 120, swing: 0, key: 60, lp: 6000,
      chords: [[50, 53, 57, 60], [50, 53, 55, 59], [48, 52, 55, 59], [49, 52, 55, 57]], roots: [38, 43, 36, 45],
      tracks: [
        { layer: 0, voice: "ep", notes: "chord", hits: [0, 3, 6, 10, 13], dur: 2, gain: 0.06 },
        { layer: 0, voice: "rim", hits: [0, 3, 6, 10, 12], gain: 0.1 },
        { layer: 1, voice: "bass", notes: "root5", hits: [0, 6, 8, 14], dur: 5, gain: 0.3 },
        { layer: 2, voice: "shaker", hits: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15], gain: 0.025 },
        { layer: 2, voice: "shaker", hits: [2, 6, 10, 14], gain: 0.03 },
        { layer: 3, voice: "kick", hits: [0, 6, 8, 14], gain: 0.3 },
        { layer: 4, voice: "ep", hits: [0, 3, 6, 10, 13], dur: 3, gain: 0.1,
          notes: [77, 76, 74, null, 72, 71, 74, 77, null, 76, 76, 79, 76, null, 71, 73, 76, 79, 76, 73] },
      ],
    },
    piano: {
      bpm: 60, swing: 0, key: 60, lp: 5000, echo: { times: [0.37, 0.53, 0.79], fb: 0.45, lp: 2500, mix: 0.4 },
      chords: [[48, 52, 55, 59], [45, 52, 55, 60], [41, 48, 52, 57], [43, 50, 55, 59]], roots: [36, 45, 41, 43],
      tracks: [
        { layer: 0, voice: "keys", notes: "chord", hits: [0], dur: 16, gain: 0.06 },
        { layer: 1, voice: "keys", notes: "root", hits: [0], dur: 16, gain: 0.14 },
        { layer: 2, voice: "keys", notes: "arp", oct: 1, hits: [2, 6, 10, 14], dur: 6, gain: 0.05 },
        { layer: 3, voice: "keys", hits: [0, 6, 10], dur: 6, gain: 0.1,
          notes: [79, 76, 74, 72, 76, null, 77, 76, 72, 74, 71, null] },
        { layer: 4, voice: "pad", notes: "chord", oct: 1, hits: [0], dur: 16, gain: 0.025 },
      ],
    },
  },
};

// ---- funções puras

export function styleFor(room) {
  return typeof room === "string" && room ? MUSIC_CONFIG.room : MUSIC_CONFIG.fallback;
}

// agentes a trabalhar nas tasks a correr da sala, com máximo; rooms = salas da config (mesmo critério do sq() do index.html:
// squad desconhecida → 1.ª sala)
export function layersFor(state, room, rooms = [], max = MUSIC_CONFIG.maxLayers) {
  let n = 0;
  for (const t of state?.tasks || []) if (t.running === true && (rooms.includes(t.squad) ? t.squad : rooms[0] ?? t.squad) === room) n += t.working?.length || 0;
  return Math.min(n, max);
}

// jingle uma vez por execução: chave id + started (nas squads de sessão os ids podem repetir-se todos os dias)
export function createJingleTracker() {
  const played = new Set(), key = (t) => `${t.id}|${t.started}`;
  return {
    update(prev, next, enabled) {
      const done = [];
      if (!prev || !enabled) return done;
      for (const t of next?.tasks || []) {
        const k = key(t), old = prev.tasks?.find((x) => key(x) === k);
        if (old?.running && !t.running && t.status === "concluída" && !played.has(k)) { played.add(k); done.push(t); }
      }
      return done;
    },
  };
}

export function readPrefs(storage) {
  const vol = MUSIC_CONFIG.defaultVolume;
  try {
    const v = storage.getItem("musicVol");
    return { on: storage.getItem("music") === "1", vol: /^\d{1,3}$/.test(v) && +v <= 100 ? +v : vol };
  } catch { return { on: false, vol }; }
}

export function writePrefs(storage, { on, vol }) {
  try { storage.setItem("music", on ? "1" : "0"); storage.setItem("musicVol", String(vol)); } catch {}
}

// ---- motor: um só agendador com lookahead sobre o relógio do ctx ("A Tale of Two Clocks").
// ctx injectado: AudioContext da página (criado no gesto, por causa do iOS) ou OfflineAudioContext (gravação + renderUntil)
export function createEngine({ ctx, destination } = {}) {
  ctx ||= new (globalThis.AudioContext || globalThis.webkitAudioContext)();
  const C = MUSIC_CONFIG, offline = typeof ctx.startRendering === "function", now = () => ctx.currentTime;
  const master = ctx.createGain(), lim = ctx.createDynamicsCompressor();
  for (const k in C.limiter) lim[k].value = C.limiter[k];
  master.gain.value = 0;
  master.connect(lim).connect(destination || ctx.destination);
  const noise = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate), nd = noise.getChannelData(0); // 1 s, partilhado
  for (let i = 0; i < nd.length; i++) nd[i] = Math.random() * 2 - 1;
  const offUntil = [0, 0, 0, 0, 0]; // camada em fade-out continua a ser agendada até aqui
  let players = [], cur = null, layers = 0, vol = C.defaultVolume, running = false, paused = false, stopping = null, timer = null;
  const level = () => (vol / 100) * C.master;
  // rampa a partir do valor actual: nunca um salto num nó audível
  const ramp = (p, v, d) => { const t = now(); p.cancelScheduledValues(t); p.setValueAtTime(p.value, t); p.linearRampToValueAtTime(v, t + d); };

  // uma nota: nós novos que fazem stop() no fim (o GC limpa-os)
  function note(name, t, midi, d, g, out) {
    const v = C.voices[name], a = v.attack || C.attack, r = v.release || C.release;
    const len = Math.max(v.len || d, a + 0.01), end = t + len, k = Math.max(Math.exp(-(len - a) / v.decay), 1e-3);
    const f = v.freq || 440 * 2 ** ((midi - 69) / 12), amp = ctx.createGain();
    let src;
    if (v.noise) { src = ctx.createBufferSource(); src.buffer = noise; src.start(t, Math.random() * 0.5); }
    else {
      src = ctx.createOscillator(); src.type = v.wave || "sine"; src.frequency.value = f;
      if (v.sweep) { src.frequency.setValueAtTime(v.sweep[0], t); src.frequency.exponentialRampToValueAtTime(v.sweep[1], t + v.sweep[2]); }
      if (v.ratio) { // FM: o índice decai mais depressa do que o volume (ataque brilhante, cauda redonda)
        const m = ctx.createOscillator(), mg = ctx.createGain();
        m.frequency.value = f * v.ratio;
        mg.gain.setValueAtTime(f * v.index, t); mg.gain.exponentialRampToValueAtTime(f * v.index * k * k + 0.01, end);
        m.connect(mg).connect(src.frequency); m.start(t); m.stop(end + r + 0.05);
      }
      src.start(t);
    }
    let node = src;
    if (v.filter) { const fl = ctx.createBiquadFilter(); fl.type = v.filter[0]; fl.frequency.value = v.filter[1]; fl.Q.value = v.filter[2] || 0.7; node = node.connect(fl); }
    const G = amp.gain;
    G.setValueAtTime(0, t); G.linearRampToValueAtTime(g, t + a); G.exponentialRampToValueAtTime(g * k, end); G.linearRampToValueAtTime(0, end + r);
    node.connect(amp).connect(out);
    src.stop(end + r + 0.05);
    src.onended = () => amp.disconnect();
  }

  // um player por estilo a soar: camadas → lowpass (+ eco) → out (fade da sala) → master
  function player(style, g0) {
    const S = C.styles[style], out = ctx.createGain(), lp = ctx.createBiquadFilter();
    lp.frequency.value = S.lp; out.gain.value = g0;
    lp.connect(out).connect(master);
    const lg = [0, 1, 2, 3, 4].map((L) => { const g = ctx.createGain(); g.gain.value = L <= layers ? 1 : 0; g.connect(lp); return g; });
    for (const tm of S.echo?.times || []) {
      const dl = ctx.createDelay(2), fb = ctx.createGain(), f = ctx.createBiquadFilter(), w = ctx.createGain();
      dl.delayTime.value = tm; fb.gain.value = S.echo.fb; f.frequency.value = S.echo.lp; w.gain.value = S.echo.mix / S.echo.times.length;
      lp.connect(dl).connect(f).connect(fb).connect(dl); f.connect(w).connect(out);
    }
    return { style, S, out, lp, lg, step: 0, nextTime: now() + 0.05, until: Infinity };
  }

  function playStep(p, s, t) {
    const S = p.S, i = s % (16 * S.chords.length), bar = i >> 4, pos = i & 15, sd = 60 / S.bpm / 4;
    for (const tr of S.tracks) {
      const L = tr.layer, h = tr.hits.indexOf(pos), n = tr.notes, ch = S.chords[bar], root = S.roots[bar];
      if (h < 0 || (L > layers && now() >= offUntil[L])) continue; // camadas mudas não se agendam
      const ms = !n ? [0] : n === "chord" ? ch : n === "root" ? [root] : n === "root5" ? [root + (h & 1) * 7]
        : n === "arp" ? [ch[h % ch.length]] : [n[bar * tr.hits.length + h]];
      for (const m of ms) if (m != null) note(tr.voice, t, m + 12 * (tr.oct || 0), (tr.dur || 1) * sd, tr.gain * (1 - C.humanize * Math.random()), p.lg[L]);
    }
  }

  // a única função que agenda notas
  function schedule(horizon) {
    for (const p of players) {
      const sd = 60 / p.S.bpm / 4;
      while (p.nextTime < Math.min(horizon, p.until)) {
        if (p.nextTime >= now()) playStep(p, p.step, p.nextTime + (p.step & 1) * p.S.swing * sd); // atrasado (timer travado): salta
        p.step++; p.nextTime += sd;
      }
    }
    for (const p of players) if (p.until <= now()) { p.lp.disconnect(); p.out.disconnect(); }
    players = players.filter((p) => p.until > now());
    if (stopping !== null && now() >= stopping) { stopping = null; disarm(); ctx.suspend(); }
  }
  const arm = () => { if (!offline && !timer) timer = setInterval(() => schedule(now() + C.scheduler.lookahead), C.scheduler.intervalMs); };
  const disarm = () => { clearInterval(timer); timer = null; };
  // o timer só arranca quando o ctx anda de facto (sem gesto válido, o resume() nunca resolve e não há timer à toa)
  const wake = () => { paused = false; if (!offline) ctx.resume().then(() => { if (running && !paused) arm(); }, () => {}); };

  return {
    start() {
      if (running) return;
      running = true; stopping = null;
      for (const p of players) p.nextTime = Math.max(p.nextTime, now() + 0.05);
      ramp(master.gain, level(), C.fades.toggle);
      wake();
    },
    stop() { // fade de saída; o agendador limpa o timer e suspende o ctx no fim
      if (!running) return;
      running = false;
      ramp(master.gain, 0, C.fades.toggle);
      stopping = now() + C.fades.toggle;
      if (ctx.state !== "running") { stopping = null; disarm(); } // relógio parado: o fim do fade nunca chegaria
    },
    setRoom(room) { // crossfade: o antigo desce e o novo sobe na mesma janela, sem silêncio no meio
      const style = styleFor(room), f = C.fades.room;
      if (cur?.style === style) return;
      if (!running && (offline || ctx.state !== "running" || stopping === null)) { // relógio quieto: sem crossfade, o antigo sai já (no fade do stop() mantém-se, sem clique)
        for (const q of players) { q.lp.disconnect(); q.out.disconnect(); }
        players = []; cur = null;
      }
      const p = player(style, cur ? 0 : 1);
      if (cur) { ramp(cur.out.gain, 0, f); cur.until = now() + f; ramp(p.out.gain, 1, f); }
      players.push(p); cur = p;
    },
    setLayers(n) { // fade por camada; o loop não reinicia porque o passo continua
      n = Math.max(0, Math.min(C.maxLayers, n | 0));
      if (n === layers) return;
      for (let L = 1; L < 5; L++) {
        if (L > n && L <= layers) offUntil[L] = now() + C.fades.layer;
        for (const p of players) ramp(p.lg[L].gain, L <= n ? 1 : 0, C.fades.layer);
      }
      layers = n;
    },
    setVolume(v) { vol = v; if (running) ramp(master.gain, level(), 0.05); },
    jingle(when = now() + 0.05) { // arpejo na tonalidade do estilo visível, por cima da música
      if (!offline && ctx.state !== "running") return; // relógio parado: não empilha jingles para tocarem todos ao arrancar
      const J = C.jingle, key = (cur?.S.key ?? 60) + 12 * J.oct, last = J.notes.length - 1;
      J.notes.forEach((s, i) => note(J.voice, when + i * J.step, key + s, (last - i) * J.step + J.tail, J.gain, master));
    },
    suspend() { paused = true; disarm(); if (!offline) ctx.suspend(); },
    resume() { if (running) wake(); },
    renderUntil(t) { schedule(t); }, // offline: agenda tudo até t (s) de uma vez
  };
}
