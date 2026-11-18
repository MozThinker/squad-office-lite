"""Entregas (ADR-014): GET /api/entregas, manchete e resumo das tarefas terminadas há ≤ 7 dias. Só stdlib.

Segunda saída de conteúdo do Office, só a pedido (nunca no poll do /api/state). A fonte e a máscara são as da entrega
do leitor: meta.result ou, nas squads worktree, o fim do claude.log (reader.log_delivery: só ficheiro regular, sem
symlink); reader.delivery mascara antes de qualquer corte. Só se lêem metas dentro das raízes da config (TASKS_DIR e
SQUADS_DIR/<squad> das squads sessao, pela pasta-mãe depois do resolve) e as ligações à Biblioteca vêm do índice já
pronto (Indice.atual(): nunca espera por ele nem o reconstrói). Não escreve nada e não importa o server.py.
"""
from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

import reader

MANCHETE_MAX = 160
RESUMO_MAX = 600
ENTREGAS_N = 40
JANELA_S = 7 * 86400
LOG_TAIL = 65536        # do claude.log só se lê o fim (como no leitor)
TAREFA_DOCS = ("SPEC.md", "PLAN.md", "QA.md")
# markdown e «1.»/«2)» de lista numerada (um ano «2026.» fica)
_MD_INICIO = re.compile(r"^(?:[\s#>*\-]|\d{1,2}[.)]\s)+")
_FRASE = re.compile(r"[.!?] ")
_ABREV = {"e.g.", "i.e.", "p.ex.", "etc.", "p.e.", "cf.", "vs."}   # ponto que não fecha frase (lista fechada)

# preenchidos por configurar(cfg)
WORKTREE: tuple = ()    # squads formato worktree (metas em TASKS_DIR; a 1.ª é a de quem não diz «squad»)
SESSAO: tuple = ()      # squads formato sessao (metas em SQUADS_DIR/<squad>)


def configurar(cfg: dict) -> None:
    global WORKTREE, SESSAO
    WORKTREE = tuple(s["id"] for s in cfg["squads"] if s["formato"] == "worktree")
    SESSAO = tuple(s["id"] for s in cfg["squads"] if s["formato"] == "sessao")


def manchete(txt: str | None) -> str | None:
    # 1.ª frase da 1.ª linha com texto, sem marcas de markdown; ≤ MANCHETE_MAX car. (cortada numa palavra, com «…»)
    if not txt:
        return None
    for line in txt.splitlines():
        s = _MD_INICIO.sub("", line).replace("`", "").replace("*", "").strip()
        if s:
            break
    else:
        return None
    s = s[:MANCHETE_MAX + 1]   # uma frase que acaba depois disto é cortada na mesma; evita trabalho quadrático
    for hit in _FRASE.finditer(s):
        antes = s[:hit.start() + 1]
        if len(antes) >= 3 and antes.rsplit(None, 1)[-1].lower() not in _ABREV:
            s = antes
            break
    if len(s) > MANCHETE_MAX:
        j = s.rfind(" ", 0, MANCHETE_MAX)
        s = (s[:j] if j > 0 else s[:MANCHETE_MAX - 1]).rstrip() + "…"
    return s


def _aware(s) -> datetime | None:
    # ISO → datetime com fuso; sem fuso = UTC (como o server)
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _squad_tasks(m: dict) -> str | None:
    # meta de TASKS_DIR: «squad» da meta ou, sem ela, a 1.ª squad worktree; outra squad → ignorada (regra do server)
    sq = m.get("squad", WORKTREE[0] if WORKTREE else None)
    return sq if isinstance(sq, str) and sq in WORKTREE else None


def _meta(meta_path: Path, real: Path) -> dict | None:
    # só metas na própria raiz (sem pasta ou ficheiro symlink para fora) e ficheiros regulares (um FIFO pendurava)
    try:
        if meta_path.resolve().parent.parent != real or not stat.S_ISREG(os.stat(meta_path).st_mode):
            return None
        m = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError):
        return None
    return m if isinstance(m, dict) else None


def listar(tasks: Path, squads_dir: Path, indice, now: float) -> dict:
    # {itens: [...]}: até ENTREGAS_N, terminada desc, de todas as squads, sem dry-run nem «a correr»
    cands = []
    for root_sq, root in [(None, tasks)] + [(sq, squads_dir / sq) for sq in SESSAO]:
        if not root.is_dir():
            continue
        real = root.resolve()
        for meta_path in root.glob("*/meta.json"):
            m = _meta(meta_path, real)
            if m is None:
                continue
            squad = root_sq or _squad_tasks(m)
            status, fin = m.get("status"), _aware(m.get("finished_at"))
            if squad is None or status == "a correr" or "dry-run" in (m.get("mode"), status) or fin is None:
                continue
            try:
                if now - fin.timestamp() > JANELA_S:
                    continue
            except (OverflowError, ValueError):
                continue
            cands.append((fin.timestamp(), squad, meta_path, m, root_sq is None))
    cands.sort(key=lambda c: c[0], reverse=True)
    snap = indice.atual()
    itens = []
    for _, squad, meta_path, m, wt in cands[:ENTREGAS_N]:
        result = m.get("result")
        if not (isinstance(result, str) and result.strip()) and wt:
            result = reader.log_delivery(meta_path.parent / "claude.log", LOG_TAIL)
        txt = reader.delivery(result)   # máscara antes do corte
        pasta = meta_path.parent.name   # a Biblioteca indexa as tarefas pela pasta em TASKS_DIR
        bib = ([k for d in TAREFA_DOCS if (k := f"tarefas/{pasta}/{d}") in snap.ficheiro_item]
               if wt and snap is not None else [])
        project = m.get("project")
        itens.append({"id": str(m.get("id") or pasta), "squad": squad,
                      "projecto": project if isinstance(project, str) else None,
                      "manchete": manchete(txt), "resumo": txt[:RESUMO_MAX] if txt else None,
                      "terminada": _aware(m.get("finished_at")).isoformat(), "biblioteca": bib})
    return {"itens": itens}
