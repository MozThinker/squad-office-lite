"""Limpeza das tarefas com PR fundido (ADR-025). Stdlib só, Python >= 3.10, corre no host.

Uma vez por dia (fuso da config) cada tarefa worktree parada é vista. Sai o worktree, o branch local e a transcrição
da sessão (a pasta do `claude_projects` com o nome do worktree, nunca se tiver `memory/`), e a meta passa a
«arquivada», só com tudo isto:
- a meta bate com o runner (`confiar`) e com o estado que só o runner escreve (`branch`); sem estado, fica;
- parada: heartbeat (só o runner o toca) parado há > MORTA_S, nenhum `office_runner.py correr <tid>` vivo e o pid da
  meta sem processo; o status da meta (que o agente edita) não chega;
- sem alterações a ficheiros seguidos e sem ficheiros novos por adicionar, salvo pastas de dependências (DEPS,
  `venv*` com `pyvenv.cfg`) e o que o runner copiou (estado); os ignorados pelo `.gitignore` saem com o worktree;
- PR fundido do mesmo repositório (não de um fork), criado depois do início da tarefa (ou o `pr_url` da meta), e o
  HEAD local igual ou antecessor do último commit do PR (assim um squash também confere).
PR aberto: fica, calado. O resto fica, com uma linha de aviso se estiver parada há mais de AVISO_DIAS. Cada passo
que falha dá uma linha no journal; falhas ou tarefas por limpar dão um `avisar()` resumido, no máximo 1 por dia.
Um passo que falhe depois de o worktree sair (branch, transcrição, meta) volta a ser tentado no dia seguinte.

O estado do PR vem do `gh pr list --head <branch>` (no máximo GH_MAX por passagem; as que faltarem ficam para a
passagem seguinte) e o último commit dos fundidos fica guardado em `<dados>/runner/limpeza.json` (só o runner
escreve), com as tarefas já vistas hoje.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import lancador

import officecfg

ESTADO = "limpeza.json"
GH_MAX = 10                 # perguntas ao gh por passagem
AVISO_DIAS = 7
AVISO_N = 10                # tarefas nomeadas na linha de aviso
ARQUIVADA = "arquivada"
ACTIVAS = ("a correr", lancador.PREPARAR, ARQUIVADA)
BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,39}/[a-z0-9][a-z0-9-]{1,58}[a-z0-9]")
OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
DEPS = frozenset({"node_modules", ".venv", "__pycache__", ".pytest_cache"})   # e venv* com pyvenv.cfg
GH_CAMPOS = "url,state,headRefName,headRefOid,createdAt,headRepositoryOwner,isCrossRepository"


def _ler_estado(cfg, dia: str) -> dict:
    try:
        d = json.loads((officecfg.caminho(cfg, "runner") / ESTADO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    if not isinstance(d, dict) or d.get("v") != 1:
        d = {}
    fundidos = d.get("fundidos") if isinstance(d.get("fundidos"), dict) else {}
    fundidos = {k: v for k, v in fundidos.items() if isinstance(v, dict) and OID.fullmatch(str(v.get("head")))
                and BRANCH.fullmatch(str(v.get("branch")))}
    vistos = [x for x in d.get("vistos") or [] if isinstance(x, str)] if d.get("dia") == dia else []
    return {"v": 1, "dia": dia, "vistos": vistos, "fundidos": fundidos,
            "avisado": d.get("avisado") if isinstance(d.get("avisado"), str) else None}


def _gravar_estado(cfg, doc: dict) -> None:
    path = officecfg.caminho(cfg, "runner") / ESTADO
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- viva?

def _correr_existe(tid: str) -> bool:
    """Há um `office_runner.py correr <tid>` vivo (Linux: /proc)? Não confia no pid da meta, que o agente edita."""
    try:
        pids = [int(n) for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
        return False
    return any(lancador._e_correr(p, tid) for p in pids)


def _viva(meta_path: Path, meta: dict, tid: str, t: float) -> bool:
    idade = lancador.idade_heartbeat(meta_path.parent, t)
    return ((idade is not None and idade < lancador.MORTA_S) or lancador.vivo(meta.get("pid"))
            or _correr_existe(tid))


# ---------------------------------------------------------------- worktree

def _status(wt: Path) -> tuple[str, list[str]]:
    """("ok", novos não seguidos) | ("sujo", []) | ("erro", []). Os ignorados não aparecem."""
    r = lancador.git(["status", "--porcelain=v1", "-z", "--untracked-files=normal"], wt)
    if r.returncode != 0:
        return "erro", []
    novos = []
    for e in r.stdout.split("\0"):
        if not e:
            continue
        if e[:3] == "?? ":
            novos.append(e[3:])
        elif e[:3] != "!! ":
            return "sujo", []
    return "ok", novos


def _dep(wt: Path, rel: str) -> bool:
    partes = [x for x in rel.split("/") if x]
    for i, p in enumerate(partes):
        if p in DEPS or (p.startswith("venv") and (wt.joinpath(*partes[:i + 1]) / "pyvenv.cfg").is_file()):
            return True
    return False


def _por_adicionar(wt: Path, novos: list[str], nossos: set[str]) -> list[str] | None:
    """Ficheiros novos que não são dependências nem cópias do runner; None se o git falhar."""
    fora = []
    for rel in novos:
        if _dep(wt, rel) or rel in nossos:
            continue
        if not rel.endswith("/"):
            fora.append(rel)
            continue
        r = lancador.git(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", rel], wt)
        if r.returncode != 0:
            return None
        fora += [f for e in r.stdout.split("\0") if e[:3] == "?? " and not _dep(wt, f := e[3:]) and f not in nossos]
    return fora


# ---------------------------------------------------------------- PR

def _origem(repo: Path) -> tuple[str, str] | None:
    r = lancador.git(["remote", "get-url", "origin"], repo)
    return lancador._repo_origin(lancador.sem_credenciais(r.stdout.strip())) if r.returncode == 0 else None


def _quando(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else None


def _pr(cfg, repo: Path, branch: str, tid: str, pr_url) -> tuple[str, dict | None]:
    """("ok", o PR mais recente do branch que é desta tarefa, ou None) ou ("erro", None). Desta tarefa: do mesmo
    repositório (não de um fork; dono = o do origin) e criado depois do início (minuto do task_id), ou com o URL do
    `pr_url` da meta. argv fixo; o branch já passou pelo BRANCH."""
    try:
        r = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "all", "--json", GH_CAMPOS], cwd=repo,
                           capture_output=True, text=True, check=False, timeout=lancador.GIT_S,
                           env=lancador.env_gh())
        prs = json.loads(r.stdout or "null") if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return "erro", None
    if not isinstance(prs, list):
        return "erro", None
    origem = _origem(repo)
    inicio = datetime.strptime(tid[:13], "%Y%m%d-%H%M").replace(tzinfo=ZoneInfo(cfg["fuso"]))
    url_meta = pr_url if isinstance(pr_url, str) and lancador.PR_URL.fullmatch(pr_url) else None
    for p in prs:
        if not isinstance(p, dict) or p.get("headRefName") != branch or p.get("isCrossRepository") is not False:
            continue
        dono = p.get("headRepositoryOwner")
        dono = dono.get("login") if isinstance(dono, dict) else None
        if origem and str(dono).lower() != origem[0]:
            continue
        criado = _quando(p.get("createdAt"))
        if (url_meta and p.get("url") == url_meta) or (criado and criado >= inicio):
            return "ok", p
    return "ok", None


# ---------------------------------------------------------------- arquivar

def _parada_ha(meta: dict, now: float) -> float:
    for k in ("finished_at", "started_at"):
        try:
            return now - datetime.fromisoformat(meta[k]).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
    return 0.0


def _transcricao(cfg, tid: str, meta: dict, wt: Path, falha) -> str:
    """Apaga a pasta do claude_projects com o nome do worktree: «apagada», «mantida» (de propósito), «sem» ou
    «erro» (a tentar outra vez)."""
    nome = officecfg.mangle(str(wt))
    if meta.get("claude_dir") not in (None, nome):
        falha(f"limpeza: tarefa {tid}: transcrição não apagada (claude_dir da meta não bate com o worktree)")
        return "mantida"
    raiz = officecfg.raiz(cfg, "claude_projects")
    pasta = raiz / nome
    if not os.path.lexists(pasta):
        return "sem"
    if os.path.islink(pasta) or not pasta.is_dir() or pasta.resolve().parent != raiz:
        falha(f"limpeza: tarefa {tid}: transcrição não apagada (não é uma pasta normal)")
        return "mantida"
    if os.path.lexists(pasta / "memory"):
        return "memory"
    try:
        shutil.rmtree(pasta)
    except OSError as e:
        falha(f"limpeza: tarefa {tid}: transcrição não apagada ({type(e).__name__}); tenta outra vez amanhã")
        return "erro"
    return "apagada"


def _arquivar(cfg, tid: str, meta_path: Path, meta: dict, wt: Path, branch: str, url, t: float,
              linhas: list[str], falha) -> bool:
    """Passos idempotentes: na passagem seguinte retoma onde parou. True = meta arquivada."""
    repo, feito = wt.parent.parent, []   # confiar(): <repo>/.worktrees/<tid>
    if os.path.lexists(wt):
        if lancador.git(["worktree", "remove", "--force", "--", str(wt)], repo).returncode != 0:
            falha(f"limpeza: tarefa {tid}: git worktree remove falhou; nada mais foi apagado")
            return False
        feito.append("worktree")
    else:
        lancador.git(["worktree", "prune"], repo)   # registo de um worktree apagado à mão prende o branch
    if lancador.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo).returncode == 0:
        if lancador.git(["branch", "-D", branch], repo).returncode != 0:
            falha(f"limpeza: tarefa {tid}: git branch -D {branch} falhou; tenta outra vez amanhã")
            return False
        feito.append("branch")
    tr = _transcricao(cfg, tid, meta, wt, falha)
    if tr == "erro":
        return False
    if tr == "memory":
        linhas.append(f"limpeza: tarefa {tid}: transcrição mantida (tem memory/)")
    elif tr == "apagada":
        feito.append("transcrição")
    url = url if isinstance(url, str) and lancador.PR_URL.fullmatch(url) else None
    meta.update(status=ARQUIVADA, pid=None, arquivada_em=lancador.agora(cfg, t).isoformat(timespec="seconds"))
    if url:
        meta.update(pr=f"{url} (merged)", pr_url=url)
    try:
        lancador.escrever_json(meta_path, meta)
    except OSError as e:
        falha(f"limpeza: tarefa {tid}: meta não gravada como arquivada ({type(e).__name__}); tenta outra vez amanhã")
        return False
    linhas.append(f"limpeza: tarefa {tid} arquivada (PR fundido; removido: {', '.join(feito) or 'nada'})")
    return True


def _ver(cfg, tid: str, meta_path: Path, meta: dict, doc: dict, gh: dict, t: float,
         linhas: list[str], ficam: list[tuple[str, str]], falha) -> bool:
    """Trata uma tarefa. False = ficou para a passagem seguinte (limite do gh); True = vista hoje."""
    def fica(motivo: str) -> bool:
        if _parada_ha(meta, t) > AVISO_DIAS * 86400:
            ficam.append((tid, motivo))
        return True

    if not lancador.confiar(cfg, tid, meta):
        falha(f"limpeza: tarefa {tid} fica: {lancador.DIFERENTE}")
        return True
    if _viva(meta_path, meta, tid, t):
        return True
    wt = Path(meta["worktree"])
    cache = doc["fundidos"].get(tid)
    if not os.path.lexists(wt):
        # já sem worktree: só se acaba uma limpeza começada (PR fundido e conferido antes); apagado à mão, fica
        if cache and _arquivar(cfg, tid, meta_path, meta, wt, cache["branch"], cache.get("url"), t, linhas, falha):
            doc["fundidos"].pop(tid, None)
        return True
    if os.path.islink(wt) or not wt.is_dir():
        falha(f"limpeza: tarefa {tid} fica: o worktree não é uma pasta")
        return True
    est = lancador.ler_estado(cfg, tid)
    if not est:
        return fica("sem o estado do runner")
    branch = meta.get("branch")
    if "branch" in est and est["branch"] != branch:   # estados de antes da 0.6.2 não têm o branch
        return fica("o branch da meta não bate com o estado do runner")
    estado, novos = _status(wt)
    if estado == "erro":
        falha(f"limpeza: tarefa {tid}: git status falhou")
        return True
    if estado == "sujo":
        return fica("alterações por commitar")
    nossos = {x for k in ("agentes_copiados", "mensagens_copiadas", "accoes_copiadas")
              for x in est.get(k) or [] if isinstance(x, str)}
    fora = _por_adicionar(wt, novos, nossos)
    if fora is None:
        falha(f"limpeza: tarefa {tid}: git status falhou")
        return True
    if fora:
        return fica(f"{len(fora)} ficheiro(s) novo(s) por adicionar")
    r = lancador.git(["symbolic-ref", "--quiet", "--short", "HEAD"], wt)
    if r.returncode != 0 or r.stdout.strip() != branch or not BRANCH.fullmatch(str(branch)):
        return fica("o worktree não está no branch da tarefa")
    if not meta.get("repo"):
        return fica("sem PR (repositório sem remoto)")
    if cache and cache["branch"] == branch:
        pr = {"url": cache.get("url"), "state": "MERGED", "headRefOid": cache["head"]}
    else:
        if gh["sem"]:
            return True
        if shutil.which("gh") is None:
            gh["sem"] = True
            falha("limpeza: gh não encontrado; estado dos PRs por confirmar")
            return True
        if gh["n"] >= GH_MAX:
            return False
        gh["n"] += 1
        res, pr = _pr(cfg, wt.parent.parent, branch, tid, meta.get("pr_url"))
        if res == "erro":
            falha(f"limpeza: tarefa {tid}: gh pr list falhou")
            return True
        if pr is None:
            return fica("sem PR")
    estado = str(pr.get("state", "")).upper()
    if estado == "OPEN":
        return True
    if estado != "MERGED":
        return fica("PR fechado sem merge")
    oid = str(pr.get("headRefOid", ""))
    if not OID.fullmatch(oid):
        falha(f"limpeza: tarefa {tid}: o gh não deu o último commit do PR")
        return True
    url = pr.get("url") if isinstance(pr.get("url"), str) else None
    doc["fundidos"][tid] = {"head": oid, "url": url, "branch": branch}
    r = lancador.git(["merge-base", "--is-ancestor", "HEAD", oid], wt)
    if r.returncode == 1:
        return fica("commits locais que não estão no PR")
    if r.returncode != 0:
        return fica("o último commit do PR não está no repositório local")
    if _arquivar(cfg, tid, meta_path, meta, wt, branch, url, t, linhas, falha):
        doc["fundidos"].pop(tid, None)
    return True


def processar(cfg, now: float | None = None) -> list[str]:
    """Uma passagem: as tarefas ainda não vistas hoje. Uma linha por coisa tratada; nada a fazer, nada impresso."""
    t = time.time() if now is None else now
    dia = lancador.agora(cfg, t).date().isoformat()
    doc = _ler_estado(cfg, dia)
    vistos, linhas, ficam, gh = set(doc["vistos"]), [], [], {"n": 0, "sem": False}
    falhas = []

    def falha(linha: str) -> None:
        falhas.append(linha)
        linhas.append(linha)

    existentes = set()
    try:
        metas = sorted(officecfg.caminho(cfg, "tasks").glob("*/meta.json"))
    except OSError:
        metas = []
    mudou = False
    for meta_path in metas:
        tid = meta_path.parent.name
        if not lancador.TID.fullmatch(tid):
            continue
        meta = lancador.ler_meta(meta_path)
        if (not meta or meta.get("id") != tid or not isinstance(meta.get("worktree"), str)
                or meta.get("status") in ACTIVAS or not isinstance(meta.get("status"), str)
                or "dry-run" in (meta.get("mode"), meta.get("status"))):
            continue
        existentes.add(tid)
        if tid in vistos:
            continue
        try:
            visto = _ver(cfg, tid, meta_path, meta, doc, gh, t, linhas, ficam, falha)
        except (OSError, ValueError) as e:   # um erro numa tarefa não pára as outras
            falha(f"limpeza: tarefa {tid}: erro ({type(e).__name__})")
            visto = True
        if visto:
            vistos.add(tid)
            mudou = True
    if ficam:
        nomes = "; ".join(f"{tid} ({m})" for tid, m in ficam[:AVISO_N])
        mais = f"; … e mais {len(ficam) - AVISO_N}" if len(ficam) > AVISO_N else ""
        linhas.append(f"limpeza: {len(ficam)} tarefa(s) parada(s) há mais de {AVISO_DIAS} dias ficam por limpar: "
                      f"{nomes}{mais}")
    if (falhas or ficam) and doc["avisado"] != dia:   # Telegram: no máximo 1 por dia; o detalhe está no journal
        partes = ([f"{len(falhas)} falha(s)"] if falhas else []) + (
            [f"{len(ficam)} tarefa(s) parada(s) há mais de {AVISO_DIAS} dias por limpar"] if ficam else [])
        lancador.avisar(f"Squad Office: limpeza das worktrees — {' e '.join(partes)} (ver o journal do runner).")
        doc["avisado"] = dia
        mudou = True
    doc["vistos"] = sorted(vistos & existentes)
    fundidos = {k: v for k, v in doc["fundidos"].items() if k in existentes}
    mudou |= fundidos != doc["fundidos"]
    doc["fundidos"] = fundidos
    if mudou:
        try:
            _gravar_estado(cfg, doc)
        except OSError:
            linhas.append("limpeza: estado não gravado (erro de escrita); as tarefas voltam a ser vistas")
    return linhas
