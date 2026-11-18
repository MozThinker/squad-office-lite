"""Ajudas dos testes da fila dos agentes e das rotinas (sem testes aqui): pedidos v1 na fila, respostas, decisões das
Aprovações e um `NOW` fixo (quarta-feira, 18/11/2026, 10:00 em Lisboa)."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import agentes
from tests import test_runner_comum as c
from tests.test_runner_comum import officecfg

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=ZoneInfo("Europe/Lisbon")).timestamp()
DIA = 86400


def pedido(cfg, accao: str, args: dict, n: int = 0, t: float = NOW) -> str:
    """Pedido v1 válido (chaves exactas, por ordem) em agentes-fila; o `n` ordena os da mesma passagem."""
    dt = datetime.fromtimestamp(t, ZoneInfo(cfg["fuso"]))
    stem = f"{dt:%Y%m%dT%H%M%S}-{n:08x}"
    doc = {"v": 1, "id": stem, "accao": accao, "args": args, "pedido_em": dt.isoformat(timespec="seconds"),
           "ip": "127.0.0.1", "ua": "teste"}
    (officecfg.caminho(cfg, "agentes_fila") / f"{stem}.json").write_text(json.dumps(doc, ensure_ascii=False), "utf-8")
    return stem


def feito(cfg, stem: str):
    p = officecfg.caminho(cfg, "agentes_fila") / f"{stem}.feito.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def agente(cfg, sq: str, nome: str, texto: str) -> Path:
    p = officecfg.caminho(cfg, "agentes") / sq / "agents" / f"{nome}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(texto.encode("utf-8"))
    return p


def skill(cfg, sq: str, nome: str) -> None:
    p = officecfg.caminho(cfg, "agentes") / sq / "skills" / nome
    p.mkdir(parents=True, exist_ok=True)
    (p / "SKILL.md").write_text(f"---\nname: {nome}\ndescription: {nome}\n---\n", "utf-8")


def decidir(cfg, ficheiro: str, accao: str = "aprovar", did: str = "R1", n: int = 0) -> None:
    """Uma decisão do Office (contrato v1 das Aprovações) na decisoes-fila, sobre o `.md` como está agora."""
    raw = (officecfg.caminho(cfg, "decisoes") / ficheiro).read_bytes()
    doc = {"v": 1, "criado": "2026-11-18T10:00:00+00:00", "fonte": "geral", "ficheiro": ficheiro, "id": did,
           "accao": accao, "nota": "não" if accao == "recusar" else "", "sha256": sha(raw), "ip": "", "ua": ""}
    (officecfg.caminho(cfg, "decisoes_fila") / f"d{n}-{ficheiro[:-3]}.json").write_text(json.dumps(doc), "utf-8")


def registo(cfg) -> dict:
    return agentes.ler_registo(officecfg.caminho(cfg, "agentes") / "squads.json")


def rotinas_de(cfg) -> list:
    return agentes.ler_rotinas(officecfg.caminho(cfg, "agentes") / "rotinas.json")["rotinas"]


__all__ = ["DIA", "NOW", "agente", "c", "decidir", "feito", "pedido", "registo", "rotinas_de", "sha", "skill"]
