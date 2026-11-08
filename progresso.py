"""Barra de progresso das tarefas em curso (SPEC D13/D14): estimativa pelo histórico e fases do Dev goal.

Funções puras; o server.py junta as amostras no ciclo do build_state() que já percorre todas as metas.
Só números e nomes de fase: nunca texto da tarefa.
"""
from __future__ import annotations

import math
from statistics import median

AMOSTRA_N = 30      # concluídas mais recentes da mesma squad e mode
MINIMO = 5          # menos do que isto = sem estimativa
PCT_MAX = 95
DUR_MAX = 86400     # > 24 h ignora-se
FASES = ("SPEC", "PLAN", "CODE", "QA", "PR")


def engenheiro(papel: str) -> bool:
    # CODE = já houve um engenheiro: `engineer` ou `*-engineer`, menos o test-engineer (como o phases() do index.html)
    return papel == "engineer" or (papel.endswith("-engineer") and papel != "test-engineer")


def duracao(inicio: float | None, fim: float | None, duration_s) -> float | None:
    # tempo de relógio fim − início (comparável com o decorrido); sem os dois, duration_s; ≤ 0 ou > 24 h = None
    if inicio is not None and fim is not None:
        d = fim - inicio
    elif isinstance(duration_s, (int, float)) and not isinstance(duration_s, bool):
        d = duration_s
    else:
        return None
    if not math.isfinite(d) or d <= 0 or d > DUR_MAX:
        return None
    return float(d)


def tipico(amostra: list[tuple[float, float]]) -> tuple[float | None, int]:
    # (mediana das AMOSTRA_N mais recentes por ref_ts, n.º usado); com menos de MINIMO não há típico
    usadas = [d for _, d in sorted(amostra, key=lambda x: x[0], reverse=True)[:AMOSTRA_N]]
    return (median(usadas) if len(usadas) >= MINIMO else None), len(usadas)


def fases(ficheiros: dict[str, bool], papeis: list[str], pr: bool) -> list[dict]:
    feito = [bool(ficheiros.get("SPEC")), bool(ficheiros.get("PLAN")), any(engenheiro(p) for p in papeis),
             bool(ficheiros.get("QA")), bool(pr)]
    agora = next((i for i, f in enumerate(feito) if not f), None)
    return [{"nome": n, "estado": "feito" if f else "agora" if i == agora else ""}
            for i, (n, f) in enumerate(zip(FASES, feito))]


def progresso(decorrido_s: float, tipico_s: float | None, amostra: int, fases: list | None) -> dict:
    dec = max(0, int(decorrido_s))
    out = {"fases": fases, "decorrido_s": dec, "tipico_s": None, "amostra": amostra, "pct": None, "faltam_s": None,
           "atrasada": False}
    if tipico_s is None:
        return out
    out["tipico_s"] = int(tipico_s)
    if dec > tipico_s:
        out["atrasada"] = True
    else:
        out["pct"] = min(PCT_MAX, math.floor(100 * dec / tipico_s))
        out["faltam_s"] = max(0, int(tipico_s - dec))
    return out
