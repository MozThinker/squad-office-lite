"""Quadro (kanban): cartões automáticos calculados do estado e cartões manuais num cartoes.json (ADR-003).

Puro: não importa o server. Os automáticos saem do `state` já calculado pelo build_state() (+ `aux`: metas de todas
as idades com as refs `inbox/<nome>` e o `result` mascarado das squads) e nunca são gravados. Os manuais vivem em
QUADRO_DIR/cartoes.json (contrato v1); a versão é o sha256 dos bytes e a escrita é atómica sob lock.
Tudo o que vem de ficheiros passa por reader.mask/clip. Só stdlib; o fuso e o «agora» vêm por parâmetro.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import threading
import unicodedata
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path

import reader

# preenchidos por configurar(cfg) (officecfg): chaves e ordem das colunas fixas, só os rótulos vêm da config
COL_KEYS = ("ideias", "a-fazer", "em-curso", "a-espera", "feito")
COLUNAS: tuple = ()
# (chave, rótulo, glifo) dos projectos da config + «outro»; a cor é c<índice % 8> (paleta em quadro.css)
PROJECTOS: tuple = ()
DEV_PROJECTOS: tuple = ()   # ids lançáveis (lancar.py, ADR-005): todos os projectos se houver squad worktree
BRIEF_SEG: dict = {}        # 1.º segmento do nome de um brief → projecto
WORKTREE: tuple = ()        # squads formato worktree (cartão por tarefa em TASKS_DIR)
SESSAO: tuple = ()          # squads formato sessao (cartão por tarefa ou grupo de rotina)

# «À tua espera» (o server usa estes mesmos nomes)
WAIT_STATUS = ("concluída", "parcial (PR draft)")
PR_OPEN = re.compile(r"(https://github\.com/[\w.-]+/[\w.-]+/pull/\d+) \((open(?:, draft)?)\)")
PR_ANY = re.compile(r"(https://github\.com/[\w.-]+/[\w.-]+/pull/\d+)(?: \(([a-z, ]{1,30})\))?")
TID = re.compile(r"[A-Za-z0-9._-]{1,120}")
SLUG_PREFIX = re.compile(r"^\d{8}-\d{4}-")
FAIL = ("erro", "incompleta", "morreu")
SQUAD_FAIL = FAIL + ("falhou",)
# execuções de rotina das squads: um cartão por squad + mode + dia local; estes modes ficam individuais
INDIVIDUAIS = ("task", "goal", "tarefa", "estudo")
ROTINAS = {"alerta": "Alertas", "diario": "Relatório diário", "semanal": "Relatório semanal", "radar": "Radar"}
EXEC_N, EXEC_RES = 12, 100     # execuções listadas no detalhe do grupo; resultado de cada uma
H1 = re.compile(r"# +(\S.*)")
APR_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}-")

DAY_S = 86400
FEITO_S = 7 * DAY_S         # Feito: visível; mais antigo = «Ver mais» (antigo)
BRIEF_S = 7 * DAY_S
BRIEF_N = 12
BRIEF_READ = 4096
BRIEF_NOME = re.compile(r"[a-z][a-z0-9-]{2,79}\.md")
BRIEF_FORA: tuple = ()      # prefixos de briefs que não são cartões
BRIEF_CURTO: tuple = ()     # 1.º segmento que se tira antes de comparar com o slug da tarefa (projectos e squads)
TITULO_MAX, RESULTADO_MAX, EXCERTO_MAX, ESTADO_MAX = 120, 300, 1500, 30

# cartoes.json (contrato v1)
FICHEIRO = "cartoes.json"
ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
VERSAO = re.compile(r"0|[0-9a-f]{64}")
CHAVES = ("id", "titulo", "descricao", "projecto", "coluna", "ordem", "etiquetas", "criado", "actualizado")
DESC_MAX, ETQ_N, ETQ_MAX, CARTOES_MAX = 2000, 5, 24, 200
LER_MAX = 1024 * 1024
OPS = {"criar": {"op", "token", "versao", "titulo", "descricao", "projecto", "coluna", "etiquetas"},
       "editar": {"op", "token", "versao", "id", "titulo", "descricao", "projecto", "etiquetas"},
       "apagar": {"op", "token", "versao", "id"},
       "mover": {"op", "token", "versao", "id", "coluna", "posicao"}}
EDITAR_OPCIONAIS = {"titulo", "descricao", "projecto", "etiquetas"}
INVALIDO = "cartoes.json inválido"

_lock = threading.Lock()


# --- texto e datas ---
def txt(s, n: int) -> str:
    # máscara e corte a n (o «…» conta); uma só passagem da máscara, memorizada (os mesmos textos voltam a cada GET)
    return _txt(s, n) if isinstance(s, str) else ""


@functools.lru_cache(maxsize=2048)
def _txt(s: str, n: int) -> str:
    if len(s) > n:
        return reader.clip(s, n - 1)
    m = reader.mask(s)
    return m if len(m) <= n else m[:n - 1] + "…"   # a máscara pode alongar um texto curto


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return " ".join(s.split())


def _ts(iso) -> float | None:
    if not isinstance(iso, str) or not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt.timestamp() if dt.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def _agora(now: float, tz) -> str:
    return datetime.fromtimestamp(now, tz).isoformat(timespec="seconds")


def _controlo(s: str, permite: str = "") -> bool:
    return any(unicodedata.category(c) == "Cc" and c not in permite for c in s)


# --- config ---
def configurar(cfg: dict) -> None:
    # troca as globais acima a partir da config normalizada (officecfg.validar)
    global COLUNAS, PROJECTOS, DEV_PROJECTOS, BRIEF_SEG, BRIEF_CURTO, BRIEF_FORA, WORKTREE, SESSAO
    COLUNAS = tuple((k, cfg["colunas"][k]) for k in COL_KEYS)
    PROJECTOS = tuple((p["id"], p["nome"], p["glifo"]) for p in cfg["projectos"]) + (("outro", "Outro", "·"),)
    WORKTREE = tuple(s["id"] for s in cfg["squads"] if s["formato"] == "worktree")
    SESSAO = tuple(s["id"] for s in cfg["squads"] if s["formato"] == "sessao")
    DEV_PROJECTOS = tuple(p["id"] for p in cfg["projectos"]) if WORKTREE else ()
    BRIEF_SEG = {p["id"]: p["id"] for p in cfg["projectos"]}
    BRIEF_CURTO = tuple(p["id"] for p in cfg["projectos"]) + tuple(s["id"] for s in cfg["squads"])
    BRIEF_FORA = ()


# --- projectos ---
def _conhecidos() -> dict[str, str]:
    out = {_norm(r): k for k, r, _ in PROJECTOS}
    out.update({k: k for k, _, _ in PROJECTOS})
    return out


def projectos() -> list[dict]:
    return [{"chave": k, "rotulo": r, "icone": i, "cor": "outro" if k == "outro" else f"c{n % 8}"}
            for n, (k, r, i) in enumerate(PROJECTOS)]


def chaves() -> set[str]:
    return {k for k, _, _ in PROJECTOS}


def projecto_de(texto) -> str:
    # id ou nome do projecto (também «Loja (auditoria)»), sem maiúsculas/acentos; desconhecido → outro
    if not isinstance(texto, str):
        return "outro"
    return _conhecidos().get(_norm(re.sub(r"\s*\(.*\)$", "", texto)), "outro")


def _projecto_titulo(titulo: str) -> str:
    n = f" {re.sub(r'[^0-9a-z&]+', ' ', _norm(titulo))} "
    for k, r, _ in PROJECTOS[:-1]:
        if f" {re.sub(r'[^0-9a-z&]+', ' ', _norm(r)).strip()} " in n:
            return k
    return "outro"


def titulo_task(task) -> str | None:
    # 1.ª linha «# …» do campo task da meta (só os primeiros 4 KB), sem o «#»; texto bruto (mascarar ao mostrar)
    if not isinstance(task, str):
        return None
    for ln in task[:BRIEF_READ].splitlines():
        m = H1.fullmatch(ln.rstrip())
        if m:
            return m.group(1).strip()
    return None


def chave_titulo(h1) -> str | None:
    # para comparar títulos de briefs e tasks: sem maiúsculas, acentos nem espaços repetidos
    return _norm(h1) or None if isinstance(h1, str) else None


# --- cartões automáticos ---
def _cartao(**kw) -> dict:
    c = {"id": None, "tipo": "auto", "fonte": None, "squad": None, "projecto": "outro", "titulo": "",
         "descricao": None, "coluna": None, "ordem": None, "selo": None, "etiquetas": [], "desde": None,
         "antigo": False, "custo_usd": None, "links": {"pr": None, "aprovacao": None, "tarefa": None}, "detalhe": {},
         "progress": None}
    c.update(kw)
    return c


def _selo(cor: str, texto: str) -> dict:
    return {"cor": cor, "texto": texto}


def _tid(v) -> tuple[str, str | None] | None:
    # (id para o cartão, id para o link); um id com aspecto de segredo sai mascarado e sem link
    if not (isinstance(v, str) and TID.fullmatch(v) and ".." not in v):
        return None
    m = reader.mask(v)
    return (v, v) if m == v else (m, None)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _titulo_dev(tid: str) -> str:
    return txt(SLUG_PREFIX.sub("", tid).replace("-", " "), TITULO_MAX)


def _dev(t: dict, aux: dict) -> dict | None:
    # squads worktree: um cartão por tarefa (PR, fases, «À espera de …»)
    squad, status = t["squad"], t.get("status")
    pr = t.get("pr") if isinstance(t.get("pr"), str) else ""
    if "dry-run" in (t.get("mode"), status):
        return None
    ids = _tid(t.get("id"))
    if ids is None:
        return None
    tid, link = ids
    started, finished = t.get("started"), t.get("finished")
    etq, selo = [], None
    if pr.endswith(("(merged)", "(closed)")):
        col = "feito"
        if pr.endswith("(closed)"):
            etq.append("fechado sem merge")
    elif status == "arquivada":
        col, etq = "feito", ["arquivada"]
    elif status == "a correr":
        col = "em-curso"
    elif status == "parada?":
        col, selo = "em-curso", _selo("laranja", "parada?")
    elif status in FAIL:
        col, selo = "em-curso", _selo("vermelho", status)
    elif status in WAIT_STATUS and PR_OPEN.fullmatch(pr):
        col = "a-espera"
        if PR_OPEN.fullmatch(pr).group(2) != "open":
            etq.append("draft")
    elif status in WAIT_STATUS and not pr:
        col = "a-espera"   # sem PR (ex.: repo sem remoto): o trabalho está num branch por rever
    else:
        col, selo = "em-curso", _selo("cinzento", txt(str(status), ESTADO_MAX))
    desde = finished if col == "a-espera" else (finished or started) if col == "feito" else started
    hit = PR_ANY.fullmatch(pr)
    phases = t.get("phases") if isinstance(t.get("phases"), dict) else {}
    return _cartao(
        id=f"{squad}:{tid}", fonte=squad, squad=squad, projecto=projecto_de(t.get("project")),
        titulo=txt((aux.get("titulo") or {}).get((squad, t["id"])), TITULO_MAX) or _titulo_dev(tid), coluna=col,
        selo=selo,
        etiquetas=etq, desde=desde, custo_usd=_num(t.get("cost_usd")),
        links={"pr": hit.group(1) if hit else None, "aprovacao": None, "tarefa": link},
        detalhe={"estado": txt(str(status), ESTADO_MAX), "branch": txt(t.get("branch"), TITULO_MAX) or None,
                 "pr_estado": hit.group(2) if hit else None, "inicio": started, "fim": finished,
                 "duracao_s": _num(t.get("duration_s")),
                 "fases": {k: phases.get(k) is True for k in ("SPEC", "PLAN", "QA")},
                 "resultado": txt((aux.get("result") or {}).get((squad, t["id"])), RESULTADO_MAX) or None},
        progress=t.get("progress"))


def _dev_espera(w: dict) -> dict | None:
    # «À espera» sem tarefa em tasks (meta com > 30 dias, PR ainda aberto na meta)
    # sem link para a tarefa: a meta tem > 30 dias e já não está nas salas (o #<id> não abria nada)
    ids, url = _tid(w.get("id")), w.get("pr_url")
    hit = PR_ANY.fullmatch(url) if isinstance(url, str) else None
    squad = w.get("squad")
    if ids is None or not hit or squad not in WORKTREE:
        return None
    tid = ids[0]
    return _cartao(
        id=f"{squad}:{tid}", fonte=squad, squad=squad, projecto=projecto_de(w.get("project")),
        titulo=_titulo_dev(tid), coluna="a-espera", etiquetas=["draft"] if w.get("draft") is True else [],
        desde=w.get("finished"), links={"pr": hit.group(1), "aprovacao": None, "tarefa": None},
        detalhe={"estado": txt(str(w.get("status")), ESTADO_MAX), "branch": None,
                 "pr_estado": "open, draft" if w.get("draft") is True else "open", "inicio": None,
                 "fim": w.get("finished"), "duracao_s": None, "fases": None, "resultado": None})


def _squad(t: dict, aux: dict) -> dict | None:
    squad, status = t.get("squad"), t.get("status")
    ids = _tid(t.get("id"))
    if "dry-run" in (t.get("mode"), status) or ids is None or squad not in SESSAO:
        return None
    tid, link = ids
    selo = None
    if status == "a correr":
        col = "em-curso"
    elif status == "parada?":
        col, selo = "em-curso", _selo("laranja", "parada?")
    elif status in SQUAD_FAIL:
        col, selo = "em-curso", _selo("vermelho", status)
    else:
        col = "feito"
    started, finished = t.get("started"), t.get("finished")
    modo = txt(str(t.get("mode") or "?"), 40)
    return _cartao(
        id=f"{squad}:{tid}", fonte=squad, squad=squad, projecto=squad if squad in chaves() else "outro",
        titulo=txt((aux.get("titulo") or {}).get((squad, t["id"])), TITULO_MAX) or txt(f"{modo} · {tid}", TITULO_MAX),
        coluna=col, selo=selo, desde=(finished or started) if col == "feito" else started,
        custo_usd=_num(t.get("cost_usd")), links={"pr": None, "aprovacao": None, "tarefa": link},
        detalhe={"modo": modo, "estado": txt(str(status), ESTADO_MAX),
                 "resultado": txt((aux.get("result") or {}).get((squad, t["id"])), RESULTADO_MAX) or None,
                 "duracao_s": _num(t.get("duration_s"))},
        progress=t.get("progress"))


def _rotina(t: dict) -> bool:
    return t.get("squad") in SESSAO and t.get("mode") not in INDIVIDUAIS \
        and "dry-run" not in (t.get("mode"), t.get("status")) and _tid(t.get("id")) is not None


def _grupos(execs: list[dict], aux: dict, tz) -> list[dict]:
    # execuções de rotina → um cartão por squad + mode + dia local (de started; senão finished)
    grupos: dict = {}
    for t in execs:
        quando = _ts(t.get("started")) or _ts(t.get("finished"))
        dia = datetime.fromtimestamp(quando, tz).date().isoformat() if quando is not None else "sem-data"
        mode = re.sub(r"[^a-z0-9_-]", "", str(t.get("mode") or "").lower())[:30] or "outro"
        grupos.setdefault((t["squad"], mode, dia), []).append((quando or 0, t))
    out = []
    for (squad, mode, dia), lst in grupos.items():
        lst.sort(key=lambda x: x[0], reverse=True)
        ts = [t for _, t in lst]
        st = [t.get("status") for t in ts]
        n = len(ts)
        correr = any(s in ("a correr", "parada?") for s in st)
        falhas = sum(s in SQUAD_FAIL for s in st)
        selo = None if falhas or "parada?" not in st else _selo("laranja", "parada?")
        custos = [c for c in (_num(t.get("cost_usd")) for t in ts) if c is not None]
        durs = [d for d in (_num(t.get("duration_s")) for t in ts) if d is not None]
        fins = [x for x in (_ts(t.get("finished") or t.get("started")) for t in ts) if x is not None]
        inicios = [x for x in (_ts(t.get("started")) for t in ts) if x is not None]
        ref = max(inicios) if correr and inicios else max(fins) if fins else None
        execucoes, linhas = [], []
        for quando, t in lst[:EXEC_N]:
            hora = datetime.fromtimestamp(quando, tz).strftime("%H:%M") if quando else "?"
            res = (aux.get("result") or {}).get((squad, t["id"]))
            res = txt(res, EXEC_RES) if res else None
            estado = txt(str(t.get("status")), ESTADO_MAX)
            ids = _tid(t["id"])
            execucoes.append({"hora": hora, "estado": estado, "resultado": res, "tarefa": ids[1]})
            linhas.append(f"{hora} · {estado}" + (f" · {res}" if res else ""))
        if n > EXEC_N:
            linhas.append(f"… e mais {n - EXEC_N}")
        rotulo = ROTINAS.get(mode, mode.capitalize())
        execs_txt = f"{n} execuç" + ("ões" if n > 1 else "ão")
        falhas_txt = f"{falhas} falhada" + ("s" if falhas > 1 else "")
        out.append(_cartao(
            id=f"{squad}:{mode}:{dia}", fonte=squad, squad=squad, projecto=squad if squad in chaves() else "outro",
            titulo=txt(f"{rotulo} · {execs_txt}", TITULO_MAX),
            coluna="em-curso" if correr else "feito", selo=_selo("vermelho", falhas_txt) if falhas else selo,
            desde=datetime.fromtimestamp(ref, tz).isoformat(timespec="seconds") if ref is not None else None,
            custo_usd=round(sum(custos), 4) if custos else None,
            links={"pr": None, "aprovacao": None, "tarefa": execucoes[0]["tarefa"]},
            detalhe={"modo": txt(mode, 40), "dia": dia, "execucoes_n": n,
                     "estado": execs_txt + (f", {falhas_txt}" if falhas else ""),
                     "resultado": "\n".join(linhas), "duracao_s": round(sum(durs), 1) if durs else None,
                     "execucoes": execucoes},
            progress=next((t["progress"] for t in ts if t.get("progress")), None)))   # a mais recente a correr
    return out


def _titulo_aprovacao(a: dict) -> str:
    # sem «# » no pedido, o aprovacoes.py devolve o nome do ficheiro: aí, sem data nem extensão
    titulo, nome = a.get("titulo"), a.get("ficheiro")
    stem = nome.removesuffix(".md") if isinstance(nome, str) else ""
    if not isinstance(titulo, str) or not titulo.strip() or titulo.strip() == stem:
        titulo = APR_DATA.sub("", stem).replace("-", " ").strip() or stem
    return txt(titulo, TITULO_MAX)


def _aprovacao(a: dict, tz) -> dict | None:
    fonte, nome = a.get("fonte"), a.get("ficheiro")
    if not (isinstance(fonte, str) and isinstance(nome, str)) or not (a.get("abertas") or 0) > 0:
        return None
    titulo = _titulo_aprovacao(a)
    try:
        criado = date.fromisoformat(a.get("criado"))
        desde = datetime.combine(criado, dtime(0), tz).isoformat()
    except (TypeError, ValueError):
        desde = None
    return _cartao(
        id=f"apr:{fonte}/{nome}", fonte="aprovacao", projecto=_projecto_titulo(titulo), titulo=titulo,
        coluna="a-espera", desde=desde,
        links={"pr": None, "aprovacao": {"fonte": fonte, "ficheiro": nome}, "tarefa": None},
        detalhe={"resumo": txt(a.get("resumo"), 400), "abertas": a.get("abertas"), "criado": a.get("criado")})


def cartoes_auto(state: dict, aux: dict, tz) -> list[dict]:
    # não muta state; sem ordenar (ver ordenar)
    out, dev_ids, rotinas = [], set(), []
    for t in state.get("tasks") or []:
        if not isinstance(t, dict):
            continue
        if t.get("squad") in WORKTREE:
            dev_ids.add(t.get("id"))
            c = _dev(t, aux)
        elif _rotina(t):
            rotinas.append(t)
            continue
        else:
            c = _squad(t, aux)
        if c:
            out.append(c)
    out += _grupos(rotinas, aux, tz)
    for w in state.get("waiting") or []:
        if isinstance(w, dict) and w.get("id") not in dev_ids:
            c = _dev_espera(w)
            if c:
                out.append(c)
    for a in state.get("aprovacoes") or []:
        c = _aprovacao(a, tz) if isinstance(a, dict) else None
        if c:
            out.append(c)
    return out


# --- briefs da inbox ---
def _slug(tid: str) -> str:
    return SLUG_PREFIX.sub("", tid)


def _casa(a: str, b: str) -> bool:
    # iguais, ou um termina no outro numa fronteira «-» com o mais curto ≥ 8
    if a == b:
        return True
    curto, longo = sorted((a, b), key=len)
    return len(curto) >= 8 and longo.endswith("-" + curto)


def _lancado(nome: str, stem: str, curto: str, metas) -> bool:
    for _squad, tid, refs, _h1 in metas:
        if nome in refs:
            return True
        s = _slug(tid)
        if _casa(s, stem) or _casa(s, curto):
            return True
    return False


def _ler_brief(path: str) -> tuple[str, bool] | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with open(fd, "rb") as f:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return None
            raw = f.read(BRIEF_READ)
    except OSError:
        return None
    return raw.decode("utf-8", "ignore"), st.st_size > BRIEF_READ


def _projecto_brief(stem: str) -> str:
    segs = stem.split("-")
    if segs[0] in WORKTREE + SESSAO and len(segs) > 1:   # <squad>-<projecto>-…: o projecto é o 2.º segmento
        segs = segs[1:]
    return BRIEF_SEG.get(segs[0], "outro")


def briefs(inbox: Path, aux: dict, now: float, tz) -> list[dict]:
    try:
        entries = [e for e in os.scandir(inbox) if BRIEF_NOME.fullmatch(e.name) and e.is_file(follow_symlinks=False)]
    except OSError:
        return []
    metas = aux.get("metas") or []
    cand = []
    for e in entries:
        if e.name.startswith(BRIEF_FORA):
            continue
        try:
            mtime = e.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        if now - mtime > BRIEF_S:
            continue
        stem = e.name[:-3]
        seg, _, resto = stem.partition("-")
        curto = resto if seg in BRIEF_CURTO and resto else stem
        if not _lancado(e.name, stem, curto, metas):
            cand.append((mtime, e.name, e.path, stem))
    cand.sort(reverse=True)
    out = []
    titulos = {m[3] for m in metas if m[3]}
    for mtime, nome, path, stem in cand:   # lê pela ordem até ter BRIEF_N (os lançados pelo título não contam)
        if len(out) >= BRIEF_N:
            break
        lido = _ler_brief(path)
        if lido is None:
            continue
        texto, cortado = lido
        h1 = next((ln[2:].strip() for ln in texto.splitlines() if ln.startswith("# ") and ln[2:].strip()), None)
        if h1 and chave_titulo(h1) in titulos:   # lançado com outro nome: o task começa pelo mesmo título
            continue
        excerto = txt(texto, EXCERTO_MAX)
        if cortado and not excerto.endswith("…"):
            excerto += "…"
        quando = datetime.fromtimestamp(mtime, tz).isoformat(timespec="seconds")
        out.append(_cartao(id=f"brief:{nome}", fonte="brief", projecto=_projecto_brief(stem),
                           titulo=txt(h1 or stem, TITULO_MAX), descricao=excerto, coluna="a-fazer", desde=quando,
                           detalhe={"nome": nome, "data": quando}))
    return out


# --- cartoes.json ---
def _iso_tz(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        return datetime.fromisoformat(v).tzinfo is not None
    except ValueError:
        return False


def _etiquetas_ok(v) -> bool:
    if not isinstance(v, list) or len(v) > ETQ_N:
        return False
    vistos = set()
    for e in v:
        if not isinstance(e, str) or not (1 <= len(e) <= ETQ_MAX) or e != e.strip() or _controlo(e) or not _utf8(e):
            return False
        if e.casefold() in vistos:
            return False
        vistos.add(e.casefold())
    return True


def _utf8(*ss) -> bool:
    # surrogate solto passa no json.loads mas rebenta ao gravar/enviar
    try:
        for s in ss:
            s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valido(c, projs: set) -> bool:
    if not isinstance(c, dict) or set(c) != set(CHAVES):
        return False
    t, d, o = c["titulo"], c["descricao"], c["ordem"]
    return (isinstance(c["id"], str) and bool(ID.fullmatch(c["id"]))
            and isinstance(t, str) and 1 <= len(t) <= TITULO_MAX and t == t.strip() and not _controlo(t)
            and isinstance(d, str) and len(d) <= DESC_MAX and not _controlo(d, "\n\t")
            and isinstance(c["projecto"], str) and c["projecto"] in projs
            and isinstance(c["coluna"], str) and c["coluna"] in COL_KEYS
            and isinstance(o, int) and not isinstance(o, bool) and o >= 0
            and _etiquetas_ok(c["etiquetas"]) and _iso_tz(c["criado"]) and _iso_tz(c["actualizado"])
            and _utf8(t, d, c["criado"], c["actualizado"]))


def _carregar(pasta: Path, projs: set) -> tuple[list | None, str, list[int], bool]:
    # → (lista bruta, versão, índices dos cartões válidos, ok). Ausente = ([], "0", [], True)
    try:
        fd = os.open(pasta / FICHEIRO, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return [], "0", [], True
    except OSError:
        return None, "0", [], False
    try:
        with open(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None, "0", [], False
            raw = f.read(LER_MAX + 1)
    except OSError:
        return None, "0", [], False
    versao = hashlib.sha256(raw).hexdigest()
    try:
        doc = json.loads(raw.decode("utf-8")) if len(raw) <= LER_MAX else None
    except ValueError:
        doc = None
    if (not isinstance(doc, dict) or set(doc) != {"v", "cartoes"} or type(doc["v"]) is not int or doc["v"] != 1
            or not isinstance(doc["cartoes"], list)):
        return None, versao, [], False
    lista, idx, ids = doc["cartoes"], [], set()
    for i, c in enumerate(lista):
        if _valido(c, projs) and c["id"] not in ids:
            ids.add(c["id"])
            idx.append(i)
    return lista, versao, idx, True


def _escrita(pasta: Path) -> bool:
    return pasta.is_dir() and os.access(pasta, os.W_OK)


def _manual(c: dict, now: float) -> dict:
    feito = c["coluna"] == "feito"
    ts = _ts(c["actualizado"])
    return _cartao(id=c["id"], tipo="manual", fonte="manual", projecto=c["projecto"],
                   titulo=txt(c["titulo"], TITULO_MAX), descricao=txt(c["descricao"], DESC_MAX), coluna=c["coluna"],
                   ordem=c["ordem"], etiquetas=[txt(e, ETQ_MAX) for e in c["etiquetas"]], desde=c["actualizado"],
                   antigo=feito and ts is not None and now - ts > FEITO_S, detalhe={},
                   criado=c["criado"], actualizado=c["actualizado"])


def _manuais(lista: list, idx: list[int], now: float) -> list[dict]:
    return [_manual(lista[i], now) for i in idx]


def ler(pasta: Path, projs: set, now: float) -> tuple[list[dict], str, int, bool]:
    # → (manuais na forma do GET, versão, inválidos, ok)
    lista, versao, idx, ok = _carregar(pasta, projs)
    if not ok:
        return [], versao, 0, False
    return _manuais(lista, idx, now), versao, len(lista) - len(idx), True


def ordenar(cartoes: list[dict], now: float) -> list[dict]:
    # por coluna; manuais primeiro por ordem; À espera do mais antigo; resto do mais recente; sem data no fim
    def chave(ic):
        i, c = ic
        col = COL_KEYS.index(c["coluna"])
        if c["tipo"] == "manual":
            return (col, 0, c["ordem"], i, "")
        ts = _ts(c["desde"])
        if ts is None:
            return (col, 2, 0, 0, c["id"])
        return (col, 1, ts if c["coluna"] == "a-espera" else -ts, 0, c["id"])
    return [c for _, c in sorted(enumerate(cartoes), key=chave)]


def _antigo(c: dict, now: float) -> None:
    if c["coluna"] == "feito" and c["tipo"] == "auto":
        ts = _ts(c["desde"])
        c["antigo"] = ts is not None and now - ts > FEITO_S


def resposta(state: dict, aux: dict, pasta: Path, inbox: Path, tz, now: float, token: str) -> dict:
    projs = chaves()
    manuais, versao, invalidos, ok = ler(pasta, projs, now)
    auto = cartoes_auto(state, aux, tz) + briefs(inbox, aux, now, tz)
    for c in auto:
        _antigo(c, now)
    return {"v": 1, "agora": _agora(now, tz), "versao": versao, "escrita": ok and _escrita(pasta),
            "invalidos": invalidos, "aviso": None if ok else INVALIDO, "token": token,
            "colunas": [{"chave": k, "rotulo": r} for k, r in COLUNAS], "projectos": projectos(),
            "cartoes": ordenar(manuais + auto, now)}


# --- POST ---
def _norm_txt(s: str) -> str:
    return s.replace("\r\n", "\n")


def validar(d: dict, projs: set) -> str | None:
    # forma do corpo (400); None = ok. Normaliza titulo/descricao/etiquetas em d.
    # editar: titulo, descricao, projecto e etiquetas são opcionais (os ausentes ficam como estão)
    for v in [*d.values(), *(d.get("etiquetas") if isinstance(d.get("etiquetas"), list) else [])]:
        if isinstance(v, str) and not _utf8(v):
            return "JSON inválido"
    op = d.get("op")
    if not isinstance(op, str) or op not in OPS:
        return "campos inválidos"
    obrig = OPS[op] - EDITAR_OPCIONAIS if op == "editar" else OPS[op]
    if not obrig <= set(d) <= OPS[op]:
        return "campos inválidos"
    if not isinstance(d["versao"], str) or not VERSAO.fullmatch(d["versao"]):
        return "campos inválidos"
    if "id" in d:
        if isinstance(d["id"], str) and ":" in d["id"]:
            return "cartão automático não se edita"
        if not isinstance(d["id"], str) or not ID.fullmatch(d["id"]):
            return "id inválido"
    if "coluna" in d and not (isinstance(d["coluna"], str) and d["coluna"] in COL_KEYS):
        return "coluna desconhecida"
    if "posicao" in d and (not isinstance(d["posicao"], int) or isinstance(d["posicao"], bool)
                           or not 0 <= d["posicao"] <= CARTOES_MAX):
        return "campos inválidos"
    if "titulo" in d:
        if not isinstance(d["titulo"], str):
            return "campos inválidos"
        d["titulo"] = _norm_txt(d["titulo"]).strip()
        if not d["titulo"]:
            return "título obrigatório"
        if len(d["titulo"]) > TITULO_MAX:
            return "título com mais de 120 caracteres"
        if _controlo(d["titulo"]):
            return "título inválido"
    if "descricao" in d:
        if d["descricao"] is None:
            d["descricao"] = ""
        if not isinstance(d["descricao"], str):
            return "campos inválidos"
        d["descricao"] = _norm_txt(d["descricao"])
        if len(d["descricao"]) > DESC_MAX:
            return "descrição com mais de 2000 caracteres"
        if _controlo(d["descricao"], "\n\t"):
            return "descrição inválida"
    if "projecto" in d and not (isinstance(d["projecto"], str) and d["projecto"] in projs):
        return "projecto desconhecido"
    if "etiquetas" in d:
        etq = d["etiquetas"]
        if not isinstance(etq, list) or not all(isinstance(e, str) for e in etq):
            return "campos inválidos"
        limpas, vistos = [], set()
        for e in etq:
            e = e.strip()
            if not 1 <= len(e) <= ETQ_MAX or _controlo(e):
                return "etiqueta com 1 a 24 caracteres"
            if e.casefold() not in vistos:
                vistos.add(e.casefold())
                limpas.append(e)
        if len(limpas) > ETQ_N:
            return "máx. 5 etiquetas"
        d["etiquetas"] = limpas
    return None


def _mantem(recebido: str, original: str, n: int) -> str:
    # o browser só viu txt(original) (mascarado/cortado): devolvê-lo igual não estraga o original
    return original if recebido == txt(original, n) else recebido


def _gravar(pasta: Path, lista: list) -> str:
    # tmp na mesma pasta + fsync + os.replace; devolve a versão nova (OSError sobe)
    raw = (json.dumps({"v": 1, "cartoes": lista}, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=pasta, prefix=".cartoes-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, pasta / FICHEIRO)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return hashlib.sha256(raw).hexdigest()


def _renumerar(lista: list, idx: list[int], coluna: str, mover: int | None, posicao: int) -> None:
    # coluna destino: manuais por ordem (sem o que se move), o movido entra em posicao; 10, 20, 30…
    na = sorted((i for i in idx if lista[i]["coluna"] == coluna and i != mover), key=lambda i: (lista[i]["ordem"], i))
    if mover is not None:
        na.insert(min(posicao, len(na)), mover)
    for n, i in enumerate(na, 1):
        lista[i]["ordem"] = n * 10


def escrever(pasta: Path, d: dict, projs: set, tz, now: float) -> tuple[int, dict]:
    # d já passou por validar(); devolve (código, corpo)
    with _lock:
        if not _escrita(pasta):
            return 503, {"error": "sem pasta do quadro"}
        lista, versao, idx, ok = _carregar(pasta, projs)
        if not ok:
            return 503, {"error": "cartoes.json inválido — corrige à mão"}
        if d["versao"] != versao:
            return 409, {"error": "conflito", "versao": versao}
        agora = _agora(now, tz)
        op = d["op"]
        alvo = None
        if op != "criar":
            alvo = next((i for i in idx if lista[i]["id"] == d["id"]), None)
            if alvo is None:
                return 404, {"error": "cartão não encontrado"}
        if op == "criar":
            if len(lista) >= CARTOES_MAX:
                return 503, {"error": "limite de 200 cartões"}
            ids = {c["id"] for c in lista if isinstance(c, dict) and isinstance(c.get("id"), str)}
            cid = "m-" + secrets.token_hex(5)
            while cid in ids:
                cid = "m-" + secrets.token_hex(5)
            fim = max((lista[i]["ordem"] for i in idx if lista[i]["coluna"] == d["coluna"]), default=0)
            lista.append({"id": cid, "titulo": d["titulo"], "descricao": d["descricao"], "projecto": d["projecto"],
                          "coluna": d["coluna"], "ordem": fim + 10, "etiquetas": d["etiquetas"], "criado": agora,
                          "actualizado": agora})
            idx.append(len(lista) - 1)
        elif op == "editar":
            c = lista[alvo]
            if "titulo" in d:
                c["titulo"] = _mantem(d["titulo"], c["titulo"], TITULO_MAX)
            if "descricao" in d:
                c["descricao"] = _mantem(d["descricao"], c["descricao"], DESC_MAX)
            if "projecto" in d:
                c["projecto"] = d["projecto"]
            if "etiquetas" in d:
                vistas = {txt(e, ETQ_MAX): e for e in c["etiquetas"]}
                novas, vistos = [], set()
                for e in d["etiquetas"]:
                    e = vistas.get(e, e)
                    if e.casefold() not in vistos:
                        vistos.add(e.casefold())
                        novas.append(e)
                c["etiquetas"] = novas
            c["actualizado"] = agora
        elif op == "apagar":
            del lista[alvo]
            idx = [i - (i > alvo) for i in idx if i != alvo]
        else:   # mover
            lista[alvo].update(coluna=d["coluna"], actualizado=agora)
            _renumerar(lista, idx, d["coluna"], alvo, d["posicao"])
        try:
            nova = _gravar(pasta, lista)
        except OSError:
            return 503, {"error": "não consegui gravar o quadro"}
    return 200, {"versao": nova, "cartoes": ordenar(_manuais(lista, idx, now), now)}
