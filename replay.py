"""Replay do dia (ADR-015): GET /api/replay?dia=AAAA-MM-DD reconstrói o escritório de hoje ou de um dos 6 dias
anteriores (no fuso da config) só a partir das metas, dos transcripts e da mtime dos SPEC/PLAN/QA.md. Só stdlib.

Os transcripts lêem-se com o mesmo _live_line do /api/state, injectado pelo server.py (Cronologias), sobre um estado
próprio que nunca toca no do ao vivo: abertos com O_NOFOLLOW, só ficheiros regulares, e guardados numa cache
incremental por ficheiro sob um lock próprio (um 2.º pedido sem mudanças não volta a abrir nada). Sai um quadro
completo de REPLAY_PASSO em REPLAY_PASSO segundos e, pelo meio, no máximo uma diferença por segundo, só com as chaves da
allowlist (sem títulos nem texto); os bytes contam-se ao gerar e, ao passar REPLAY_MAX, tiram-se as diferenças e não
se geram mais. Tectos: REPLAY_LER_MAX bytes lidos por transcript (maior: só o fim) e REPLAY_AGENTES subagentes por
tarefa (os de mais bytes, escolhidos pelo server.py). Não escreve nada nem importa o server.py (as metas e a regra das
tarefas «a correr» vêm de lá).
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
from array import array
from collections import OrderedDict
from bisect import bisect_right
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPLAY_MAX = 4 * 1024 * 1024   # bytes sem gzip; acima disto só ficam os completos
REPLAY_DIAS = 7                # hoje e os 6 anteriores, no fuso da config
REPLAY_PASSO = 300             # um quadro completo de 5 em 5 min
REPLAY_CACHE_S = 8 * 86400     # entradas da cache com a mtime mais antiga do que isto saem
REPLAY_LER_MAX = 8 << 20       # bytes lidos por transcript; maior → só os últimos (o início do ficheiro perde-se)
REPLAY_AGENTES = 12            # subagentes por tarefa no replay (server.py: os de mais bytes)
REPLAY_RESPOSTAS = 3           # respostas guardadas (uma por dia pedido, LRU; ≈ 14 MB cada no pior caso)
HOJE_ARREDONDA = 60            # o fim de hoje desce ao minuto: pedidos no mesmo minuto acertam na cache
TIMESTAMP = re.compile(rb'"timestamp"\s*:\s*"([^"\\]{1,64})"')
DIA = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
CI = {"ci_vermelho": "vermelho", "ci_verde": "verde"}

TZ = timezone.utc   # configurar(cfg)


def configurar(cfg: dict) -> None:
    global TZ
    try:
        TZ = ZoneInfo(cfg["fuso"])
    except Exception:   # sem tzdata: UTC, como o server
        TZ = timezone.utc


def janela(dia, now: float) -> tuple[int, int] | None:
    # (início, fim) em epoch do dia pedido; None fora de hoje−6…hoje ou dia inválido. fim = min(agora descido ao
    # minuto, 24:00): o fim entra na firma da cache e o segundo mudava-a a cada pedido
    if not (isinstance(dia, str) and DIA.fullmatch(dia)):
        return None
    try:
        d = date.fromisoformat(dia)
    except ValueError:
        return None
    hoje = datetime.fromtimestamp(now, TZ).date()
    if not hoje - timedelta(days=REPLAY_DIAS - 1) <= d <= hoje:
        return None
    inicio = int(datetime.combine(d, dtime(0), TZ).timestamp())
    agora = max(inicio, int(now) // HOJE_ARREDONDA * HOJE_ARREDONDA)
    return inicio, min(agora, int(datetime.combine(d + timedelta(days=1), dtime(0), TZ).timestamp()))


def _aware(s) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class _Crono:
    # cronologia de um transcript: segundos das linhas e os pontos onde o «visível» mudou
    __slots__ = ("ino", "off", "mtime_ns", "size", "mtime", "fs", "vistos", "pts_t", "pts", "sig")

    def __init__(self, ino: int, estado):
        self.ino, self.off, self.mtime_ns, self.size, self.mtime = ino, 0, -1, -1, 0.0
        self.fs = estado(ino)       # primed = False: o _live_line não toca no feed do ao vivo
        self.vistos = array("q")    # epoch (s) das linhas, crescente e sem repetidos
        self.pts_t = array("q")     # epoch (s) de cada ponto, crescente
        self.pts = []               # (act[:3] | None, desde | None, kind do pendente | None, ci | None)
        self.sig = None


def _sinal(fs) -> tuple:
    # o que o replay mostra de um transcript: o act, o Bash em 1.º plano pendente, o pendente (para o «preso?») e o CI
    p = fs.pending.get(fs.last_id) if fs.last_id else None
    return (fs.act[:3] if fs.act else None, p[3] if p else None, p[0] if p else None, fs.ci)


def _ponto(c: _Crono, t: int):
    i = bisect_right(c.pts_t, t)
    return c.pts[i - 1] if i else None


def _act_r(s) -> dict | None:
    return {"kind": s[0][0], "tool": s[0][1], "error": s[0][2], "desde": s[1]} if s and s[0] else None


class Cronologias:
    """Cache dos transcripts do replay. live_line, estado e marcas são o _live_line, o _FileState e as LIVE_MARKS do
    server.py; os limiares são os do ao vivo. Quem chama segura o lock enquanto usa cronologia/do_dia/quadros."""

    def __init__(self, live_line, estado, marcas: tuple, active_s: int, stuck_s: int, stuck_term_s: int):
        self.live_line, self.estado, self.marcas = live_line, estado, marcas
        self.active_s, self.stuck_s, self.stuck_term_s = active_s, stuck_s, stuck_term_s
        self.lock = threading.Lock()
        self.cache: dict = {}   # transcript → _Crono
        self.respostas: OrderedDict = OrderedDict()   # firma das entradas → resposta (LRU, REPLAY_RESPOSTAS)

    def resposta(self, chave: tuple, calcular) -> dict:
        # o mesmo pedido sem mudanças (até REPLAY_RESPOSTAS dias diferentes) não se recalcula
        if chave in self.respostas:
            self.respostas.move_to_end(chave)
            return self.respostas[chave]
        for k in [k for k in self.respostas if k[:1] == chave[:1]]:   # uma só por dia: a de hoje muda e não acumula
            del self.respostas[k]
        out = self.respostas[chave] = calcular()
        while len(self.respostas) > REPLAY_RESPOSTAS:
            self.respostas.popitem(last=False)
        return out

    def despejar(self, now: float) -> None:
        for k in [k for k, c in self.cache.items() if now - c.mtime > REPLAY_CACHE_S]:
            del self.cache[k]

    def cronologia(self, path: Path) -> _Crono | None:
        # só ficheiros regulares, sem seguir symlinks (um FIFO pendurava o pedido); com o mesmo (inode, mtime_ns,
        # tamanho) não se abre o ficheiro; com o mesmo inode e maior, lê só os bytes novos, no máximo REPLAY_LER_MAX
        # (mais do que isso: salta para o fim e deita fora a linha cortada). Só as linhas com uma das marcas passam
        # pelo json.loads e pelo live_line; das outras só se tira a hora (o último "timestamp" da linha)
        key = str(path)
        try:
            st = os.lstat(path)
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        c = self.cache.get(key)
        if c is not None and (c.ino, c.mtime_ns, c.size) == (st.st_ino, st.st_mtime_ns, st.st_size):
            return c
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return None
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                return None
            if c is None or c.ino != st.st_ino or st.st_size < c.off:
                c = self.cache[key] = _Crono(st.st_ino, self.estado)
            f.seek(c.off)
            if st.st_size - c.off > REPLAY_LER_MAX:
                c.off = st.st_size - REPLAY_LER_MAX
                f.seek(c.off - 1)
                c.off += len(f.readline()) - 1   # a linha cortada (ou só o \n anterior, se começa ali)
            ctx = ("", "", "")
            for line in f:
                if not line.endswith(b"\n"):
                    break
                c.off += len(line)
                marcada = any(mark in line for mark in self.marcas)
                if marcada:
                    try:
                        d = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(d, dict):
                        continue
                    ts = _aware(d.get("timestamp"))
                else:
                    if not line.lstrip().startswith(b"{"):
                        continue
                    i = line.rfind(b'"timestamp"')
                    m = TIMESTAMP.match(line, i) if i >= 0 else None
                    ts = _aware(m.group(1).decode("ascii", "replace")) if m else None
                try:
                    t = int(ts.timestamp()) if ts else None
                except (OverflowError, ValueError, OSError):
                    t = None
                last = c.vistos[-1] if c.vistos else None   # linha sem hora ou fora de ordem: a da anterior
                t = last if t is None else t if last is None else max(t, last)
                if t is None:
                    continue
                if t != last:
                    c.vistos.append(t)
                if marcada:
                    self.live_line(d, c.fs, ctx, st.st_mtime)
                    sig = _sinal(c.fs)
                    if sig != c.sig:
                        c.sig = sig
                        if c.pts_t and c.pts_t[-1] == t:   # várias mudanças no mesmo segundo: fica a última
                            c.pts[-1] = sig
                        else:
                            c.pts_t.append(t)
                            c.pts.append(sig)
            c.mtime_ns, c.size, c.mtime = st.st_mtime_ns, st.st_size, st.st_mtime
        return c

    def do_dia(self, path: Path, inicio: int, claude_real: Path) -> _Crono | None:
        # transcript tocado no dia, dentro da pasta do Claude (sem pastas symlink para fora) e com linhas
        try:
            if os.lstat(path).st_mtime < inicio or not Path(os.path.realpath(path)).is_relative_to(claude_real):
                return None
        except OSError:
            return None
        c = self.cronologia(path)
        return c if c is not None and c.vistos else None

    def _preso(self, s, last: int, t: int) -> bool:
        # as regras do _stuck do ao vivo, com a última linha ≤ t no papel da mtime
        return (bool(s) and s[2] is not None and s[2] != "delegar"
                and t - last > (self.stuck_term_s if s[1] else self.stuck_s))

    def tarefa_em(self, tk: dict, t: int) -> dict | None:
        # a TAREFA do contrato no instante t (só as chaves da allowlist); None = ainda não começou
        if tk["ini"] > t:
            return None
        running = tk["fin"] is None or t < tk["fin"]
        ci = None
        for c in tk["principais"] + [a[2] for a in tk["agentes"]]:
            s = _ponto(c, t)
            if s and s[3] and (ci is None or s[3][1] > ci[1]):
                ci = s[3]
        lead, agents = {"role": tk["lead"], "act": None, "stuck": False}, []
        if running:   # terminada = sem agentes e o líder sem act
            melhor = None   # o líder worktree é a sessão principal com a última linha ≤ t mais recente
            for c in tk["principais"]:
                i = bisect_right(c.vistos, t)
                if i and (melhor is None or c.vistos[i - 1] >= melhor[0]):
                    melhor = (c.vistos[i - 1], c)
            if melhor:
                s = _ponto(melhor[1], t)
                if s and s[0] and (t - melhor[0] < self.active_s or s[1]):
                    lead["act"] = _act_r(s)
                lead["stuck"] = self._preso(s, melhor[0], t)
            for role, fin, c in tk["agentes"]:
                i = bisect_right(c.vistos, t)
                if not i:
                    continue
                last, finished = c.vistos[i - 1], fin and i == len(c.vistos)
                s = None if finished else _ponto(c, t)
                agents.append((c.vistos[0], {"role": role, "active": not finished and t - last < self.active_s,
                                             "finished": finished, "stuck": not finished and self._preso(s, last, t),
                                             "act": _act_r(s)}))
            agents.sort(key=lambda a: a[0])
        return {"id": tk["id"], "squad": tk["squad"], "project": tk["project"], "mode": tk["mode"],
                "status": "a correr" if running else ("parada?" if tk["status"] == "a correr" else tk["status"]),
                "running": running,
                "phases": {f: v is not None and v <= t for f, v in tk["fases"].items()},
                "ci": CI.get(ci[0]) if ci else None,
                "lead": lead, "agents": [a[1] for a in agents]}

    def instantes(self, tk: dict) -> set:
        # onde o tarefa_em pode mudar: início/fim, fases, pontos, linhas que (re)activam, e linha + ACTIVE_S ou
        # + o tecto do «preso?» quando a seguinte vem depois disso
        out = {tk["ini"]} | ({tk["fin"]} if tk["fin"] is not None else set())
        out.update(v for v in tk["fases"].values() if v is not None)
        varias = len(tk["principais"]) > 1   # o líder pode mudar de sessão em qualquer linha
        for c in tk["principais"] + [a[2] for a in tk["agentes"]]:
            out.update(c.pts_t)
            vs, n = c.vistos, len(c.vistos)
            out.add(vs[-1])
            for i, v in enumerate(vs):
                gap = vs[i + 1] - v if i + 1 < n else math.inf
                if varias or i == 0 or v - vs[i - 1] >= self.active_s:
                    out.add(v)
                if gap > self.active_s:
                    out.add(v + self.active_s)
                    s = _ponto(c, v)
                    if s and s[2] is not None and s[2] != "delegar":
                        thr = self.stuck_term_s if s[1] else self.stuck_s
                        if gap > thr:
                            out.add(v + thr + 1)
        return out

    def quadros(self, tarefas: list, inicio: int, fim: int, orcamento: float = math.inf) -> tuple[list, bool]:
        # completo de REPLAY_PASSO em REPLAY_PASSO desde a 1.ª actividade; entre eles, um dif por segundo com as
        # tarefas que mudaram (inteiras), avaliando só as tarefas donas de cada instante. Conta os bytes de cada
        # quadro (compacto, com as vírgulas): ao passar o orçamento tiram-se os dif e não se geram mais (truncado)
        if not tarefas:
            return [], False
        t0 = max(inicio, min(tk["ini"] for tk in tarefas))
        eventos: dict = {}
        for i, tk in enumerate(tarefas):
            for t in self.instantes(tk):
                if t0 < t <= fim:
                    eventos.setdefault(t, []).append(i)
        completos = set(range(t0, fim + 1, REPLAY_PASSO))
        quadros, ultimo, total, truncado = [], {}, -1, False
        for t in sorted(completos | set(eventos)):
            if t in completos:
                lista = [d for tk in tarefas if (d := self.tarefa_em(tk, t)) is not None]
                ultimo = {f"{d['squad']}/{d['id']}": d for d in lista}
                q = {"t": t, "tipo": "completo", "tarefas": lista}
            elif truncado:
                continue
            else:
                mudou, saiu = [], []
                for i in eventos[t]:
                    tk = tarefas[i]
                    d, k = self.tarefa_em(tk, t), f"{tk['squad']}/{tk['id']}"
                    if d is None:
                        if ultimo.pop(k, None) is not None:
                            saiu.append(k)
                    elif ultimo.get(k) != d:
                        ultimo[k] = d
                        mudou.append(d)
                if not (mudou or saiu):
                    continue
                q = {"t": t, "tipo": "dif", "mudou": mudou, "saiu": saiu}
            quadros.append(q)
            total += len(compacto(q)) + 1
            if not truncado and total > orcamento:
                truncado = True
                quadros = [x for x in quadros if x["tipo"] == "completo"]
        return quadros, truncado


def firma(dia: str, inicio: int, fim: int, tarefas: list, maximo: int) -> tuple:
    # tudo o que decide a resposta: a janela, as metas e o ponto onde ficou a leitura de cada transcript
    def cr(c: _Crono) -> tuple:
        return c.ino, c.off, c.mtime_ns
    return (dia, inicio, fim, maximo, tuple(
        (tk["id"], tk["squad"], tk["project"], tk["mode"], tk["status"], tk["ini"], tk["fin"],
         tuple(tk["fases"].items()), tk["lead"], tuple(cr(c) for c in tk["principais"]),
         tuple((r, f, *cr(c)) for r, f, c in tk["agentes"])) for tk in tarefas))


def compacto(d) -> bytes:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()
