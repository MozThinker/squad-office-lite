"""Rotinas das squads (ADR-019): só correm depois de aprovadas nas Aprovações. Stdlib só, Python >= 3.10.

Registo em `<dados>/agentes/rotinas.json` (só o runner escreve). `rotina_pedir` (pela fila dos agentes) escreve um
`.md` novo em `<dados>/decisoes/` (gramática do `aprovacoes.py`/`decisoes.py`) com a definição canónica e
`- [R1] …`; a rotina passa a `aprovada` só quando o R1 está `→ aprovado` e a linha da `## Definição` tem o `sha256` do
registo. Em cada passagem (`processar`): fecha as tarefas lançadas (3 seguidas em `erro` → `pausada`), lê as decisões
pendentes e lança as aprovadas à hora (dia e hora no `fuso`), uma vez por dia, como tarefa `task` da squad, dentro do
`tecto_diario` e do `paralelo`: na 1.ª passagem com hora ≥ HH:MM desse dia (runner desligado à hora lança-a quando
voltar, no mesmo dia; sem passagem até à meia-noite, esse dia salta). Rotina activa cujo lead mudou sem novo ensaio
não lança: fica com `motivo` no registo e uma linha no journal por dia.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(1, str(Path(__file__).resolve().parent))
import decisoes
import lancador
import seguro

import agentes
import officecfg

DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
FALHAS_PAUSA = 3
TAREFAS_MAX = 3
DECISAO_MAX = 1024 * 1024          # = aprovacoes.PEDIDO_MAX
REGISTO = "rotinas.json"
MORTAS_MAX = 20                    # canceladas/recusadas guardadas no registo (as mais recentes)
MOTIVO_ENSAIO = "ensaio do lead por refazer"
VIVAS = ("pendente", "aprovada", "pausada")   # contam no limite de ROTINAS_MAX por squad


class Ilegivel(OSError):
    """rotinas.json existe mas não se lê: nunca se sobrescreve (perdiam-se as rotinas)."""


def _base(cfg) -> Path:
    return officecfg.caminho(cfg, "agentes")


def ler(cfg) -> dict:
    """O registo; Ilegivel se o ficheiro existir e não for um JSON v1 legível (ex.: > REGISTO_MAX, symlink)."""
    path = _base(cfg) / REGISTO
    if os.path.lexists(path):
        raw = agentes.ler_bytes(path, agentes.REGISTO_MAX)
        try:
            d = json.loads(raw.decode("utf-8")) if raw is not None else None
        except ValueError:
            d = None
        if not (isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("rotinas"), list)):
            raise Ilegivel("rotinas.json ilegível")
    return agentes.ler_rotinas(path)


def gravar(cfg, doc: dict) -> None:
    """Grava o registo sem as canceladas/recusadas mais antigas (ficam MORTAS_MAX). Os ids nunca se repetem: o `.md`
    da decisão continua em decisoes/ e o pedir salta os nomes ocupados."""
    mortas = [r for r in doc["rotinas"] if r["estado"] not in VIVAS]
    fora = {id(r) for r in mortas[:-MORTAS_MAX]} if len(mortas) > MORTAS_MAX else set()
    gravar_json(_base(cfg), REGISTO, {"v": 1, "rotinas": [r for r in doc["rotinas"] if id(r) not in fora]})


def gravar_json(pasta: Path, nome: str, doc, modo: int = 0o600) -> None:
    """tmp + rename por fd da pasta (sem seguir symlinks). OSError sobe. `squads.json` vai a 0644: o serviço `accoes`
    (outro uid, ADR-021) lê-o para saber se a squad está activa; não tem segredos."""
    dfd = seguro.abrir_pasta(pasta)
    try:
        seguro.substituir(dfd, nome, json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"), modo)
    finally:
        os.close(dfd)


def ensaio_lead_ok(cfg, registo: dict, squad: str) -> bool:
    """Squad criada por modelo: há ensaio ok do `.md` actual do lead (CA-34). Squads só do office.yaml: sempre."""
    if squad not in {s["id"] for s in registo["squads"]}:
        return True
    lead = agentes.lead_de(cfg, registo, squad)
    raw = agentes.ler_bytes(_base(cfg) / squad / "agents" / f"{lead}.md", agentes.MD_MAX)
    rasc = officecfg.caminho(cfg, "agentes_rascunhos") / "squads" / squad
    e = agentes._ensaio(rasc / "ensaio.json") if os.path.isdir(rasc) and not os.path.islink(rasc) else None
    return raw is not None and e is not None and e["estado"] == "ok" and e["sha256"] == agentes._sha(raw)


def texto_decisao(cfg, r: dict, criado: str) -> str:
    """O `.md` da decisão (§3.3). Nenhuma linha vinda do utilizador abre secção ou decisão: as instruções vão com
    `> ` e a definição (JSON ensure_ascii, sem quebras) indentada 4 espaços."""
    nome = next(s["nome"] for s in cfg["squads"] if s["id"] == r["squad"])
    linha, _ = agentes.linha_rotina(r)
    dias = ", ".join(DIAS[d - 1] for d in r["dias"])
    instr = "\n".join("> " + ln for ln in r["instrucoes"].split("\n"))
    return (f"---\nestado: pendente\ncriado: {criado}\n---\n# Rotina {r['id']} da squad {nome}\n"
            f"## O quê\nCorrer uma tarefa da squad {r['squad']} no projecto {r['projecto']} às {r['hora']} ({dias}), "
            f"no fuso {cfg['fuso']}.\n"
            f"## Datas e orçamento\nTecto: {r['tecto_usd']} USD por execução · máximo {r['max_turnos']} turnos.\n"
            f"## Instruções\n{instr}\n## Definição\n    {linha}\n"
            f"## Decisões\n- [R1] Activar a rotina {r['id']} (tecto {r['tecto_usd']} USD por execução)\n")


def decisao(cfg, r: dict) -> str | None:
    """"aprovado" (R1 aprovado e a definição do `.md` com o sha256 do registo), "recusado" (R1 recusado) ou None."""
    raw = agentes.ler_bytes(officecfg.caminho(cfg, "decisoes") / r["decisao"], DECISAO_MAX)
    try:
        texto = raw.decode("utf-8") if raw is not None else ""
    except UnicodeDecodeError:
        return None
    r1 = next((d for d in decisoes.decisoes(texto) if d["id"] == "R1"), None)
    if r1 is None or r1["estado"] is None:
        return None
    if r1["estado"] == "recusado":
        return "recusado"
    linhas = texto.split("\n")
    if "## Definição" not in linhas:
        return None
    defn = None
    for ln in linhas[linhas.index("## Definição") + 1:]:
        if ln.startswith("## "):
            break
        if ln.startswith("    "):
            defn = ln[4:]
            break
    linha, sha = agentes.linha_rotina(r)
    return "aprovado" if defn == linha and sha == r["sha256"] else None


def _achar(doc: dict, squad: str, rid: str) -> dict | None:
    return next((r for r in doc["rotinas"] if r["squad"] == squad and r["id"] == rid and r["estado"] != "cancelada"),
                None)


# ---------------------------------------------------------------- acções da fila (devolvem None ou um de ERROS)

def pedir(cfg, a: dict, now: float) -> str | None:
    s = lancador.lancaveis(cfg).get(a["squad"])
    if s is None or "task" not in s["modos"]:
        return "squad não lançável"
    if a["projecto"] not in {p["id"] for p in cfg["projectos"]}:
        return "projecto inválido"
    if not ensaio_lead_ok(cfg, agentes.ler_registo(_base(cfg) / "squads.json"), a["squad"]):
        return "sem ensaio ok desta versão"
    doc = ler(cfg)
    minhas = [r for r in doc["rotinas"] if r["squad"] == a["squad"]]
    if sum(r["estado"] in VIVAS for r in minhas) >= agentes.ROTINAS_MAX:
        return "limite de rotinas"
    pasta = officecfg.caminho(cfg, "decisoes")
    n = max((int(r["id"][1:]) for r in minhas), default=0) + 1
    while n <= 999 and os.path.lexists(pasta / f"rotina-{a['squad']}-r{n}.md"):
        n += 1
    if n > 999:
        return "limite de rotinas"
    dt = lancador.agora(cfg, now)
    r = {"id": f"r{n}", **{k: a[k] for k in agentes.ACCOES["rotina_pedir"]}}   # squad, projecto, …, max_turnos
    r.update(sha256=agentes.linha_rotina(r)[1], decisao=f"rotina-{a['squad']}-{r['id']}.md", estado="pendente",
             falhas=0, ultima=None, tarefas=[], criada_em=dt.isoformat(timespec="seconds"))
    dfd = seguro.abrir_pasta(pasta)
    try:
        fd = os.open(r["decisao"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o640,
                     dir_fd=dfd)
        with open(fd, "wb") as f:
            f.write(texto_decisao(cfg, r, dt.date().isoformat()).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        try:
            gravar(cfg, {"v": 1, "rotinas": doc["rotinas"] + [r]})
        except OSError:
            os.unlink(r["decisao"], dir_fd=dfd)   # sem registo, a decisão não vale nada: sai
            raise
    finally:
        os.close(dfd)
    return None


def _sem_motivo(r: dict) -> None:
    r.pop("motivo", None)
    r.pop("motivo_em", None)


def retomar(cfg, a: dict) -> str | None:
    doc = ler(cfg)
    r = _achar(doc, a["squad"], a["rotina"])
    if r is None:
        return "rotina inexistente"
    if r["estado"] != "pausada":
        return "rotina não pausada"
    r.update(estado="aprovada", falhas=0, tarefas=[])
    _sem_motivo(r)
    gravar(cfg, doc)
    return None


def cancelar(cfg, a: dict) -> str | None:
    doc = ler(cfg)
    r = _achar(doc, a["squad"], a["rotina"])
    if r is None:
        return "rotina inexistente"
    r["estado"] = "cancelada"
    _sem_motivo(r)
    gravar(cfg, doc)
    return None


def cancelar_squad(cfg, squad: str) -> int:
    """Cancela todas as rotinas vivas da squad (squad_desactivar). Devolve quantas."""
    doc = ler(cfg)
    vivas = [r for r in doc["rotinas"] if r["squad"] == squad and r["estado"] != "cancelada"]
    for r in vivas:
        r["estado"] = "cancelada"
        _sem_motivo(r)
    if vivas:
        gravar(cfg, doc)
    return len(vivas)


# ---------------------------------------------------------------- passagem

def lancadas_hoje(cfg, now: float) -> int:
    """Tarefas de rotinas criadas hoje (fuso da config): contam no tecto_diario com as do Quadro."""
    hoje, n = f"{lancador.agora(cfg, now):%Y%m%d}", 0
    for m in officecfg.caminho(cfg, "tasks").glob(f"{hoje}-*/meta.json"):
        meta = lancador.ler_meta(m)
        n += bool(meta and isinstance(meta.get("rotina"), str))
    return n


def _fechar_tarefas(cfg, r: dict) -> bool:
    # tarefas fechadas saem da lista; erro soma uma falha, outro fim põe o contador a zero (CA-37)
    abertas = []
    for tid in r["tarefas"]:
        meta = lancador.ler_meta(officecfg.caminho(cfg, "tasks") / tid / "meta.json") \
            if lancador.TID.fullmatch(tid) else None
        if meta is not None and meta.get("status") == "a correr":
            abertas.append(tid)
        elif meta is not None:
            r["falhas"] = r["falhas"] + 1 if meta.get("status") == "erro" else 0
    mudou = abertas != r["tarefas"]
    r["tarefas"] = abertas
    return mudou


def _hora_de(cfg, r: dict, dt, hoje: str) -> bool:
    return dt.isoweekday() in r["dias"] and f"{dt:%H:%M}" >= r["hora"] and r["ultima"] != hoje


def _pode(cfg, r: dict, registo: dict, t: float) -> bool | str:
    """True = lança; MOTIVO_ENSAIO = o lead mudou sem novo ensaio; False = não (ainda) por outra razão."""
    s = lancador.lancaveis(cfg).get(r["squad"])
    if s is None or "task" not in s["modos"] or r["projecto"] not in {p["id"] for p in cfg["projectos"]}:
        return False
    if decisao(cfg, r) != "aprovado":
        return False
    if not ensaio_lead_ok(cfg, registo, r["squad"]):
        return MOTIVO_ENSAIO
    fila = officecfg.caminho(cfg, "quadro_lancar")
    if lancador.lancados_hoje(cfg, fila, t) + lancadas_hoje(cfg, t) >= cfg["runner"]["tecto_diario"]:
        return False
    return lancador.a_correr(cfg, t) < cfg["runner"]["paralelo"]


def _motivo(r: dict, nome: str, motivo: str | None, hoje: str, linhas: list[str]) -> bool:
    # o motivo fica no registo (GET); a linha no journal sai uma vez por dia
    if motivo is None:
        mudou = "motivo" in r
        _sem_motivo(r)
        return mudou
    if r.get("motivo") == motivo and r.get("motivo_em") == hoje:
        return False
    r.update(motivo=motivo, motivo_em=hoje)
    linhas.append(f"rotina {nome} não lançada: {motivo}")
    return True


def _lancar(cfg, cfg_path: str, r: dict, dt, t: float, linhas: list[str]) -> None:
    s = lancador.lancaveis(cfg)[r["squad"]]
    nome = f"{r['squad']}/{r['id']}"
    p = {"id": None, "squad": r["squad"], "projecto": r["projecto"], "kind": s["tipos"][0], "mode": "task",
         "slug": f"rotina-{r['squad']}-{r['id']}-{dt:%Y%m%d}", "titulo": f"Rotina {r['id']} da squad {s['nome']}",
         "brief": r["instrucoes"]}
    r["ultima"] = dt.date().isoformat()
    try:
        tid = lancador.preparar(cfg, p, t, extra={"rotina": nome, "max_usd": r["tecto_usd"],
                                                  "max_turnos": r["max_turnos"]})
    except (lancador.Falha, OSError) as e:
        e = e if isinstance(e, lancador.Falha) else "erro a preparar a tarefa no host"
        r["falhas"] += 1
        linhas.append(f"rotina {nome} não lançada: {e}")
        return
    r["tarefas"] = (r["tarefas"] + [tid])[-TAREFAS_MAX:]
    lancador.destacar(cfg, cfg_path, tid)
    linhas.append(f"rotina {nome} lançada: {tid}")


def _pausar(r: dict, nome: str, linhas: list[str]) -> bool:
    if r["estado"] != "aprovada" or r["falhas"] < FALHAS_PAUSA:
        return False
    r["estado"] = "pausada"
    linhas.append(f"rotina {nome} pausada: {FALHAS_PAUSA} falhas seguidas")
    lancador.avisar(f"Squad Office: rotina {nome} pausada ({FALHAS_PAUSA} falhas seguidas).")
    return True


def processar(cfg, cfg_path: str, now: float | None = None) -> list[str]:
    """Uma passagem pelas rotinas: fecha tarefas, lê decisões, pausa e lança. Uma linha por coisa tratada."""
    t = time.time() if now is None else now
    try:
        doc = ler(cfg)
    except Ilegivel:
        return ["rotinas: rotinas.json ilegível; nada lançado nem gravado"]
    if not doc["rotinas"]:
        return []
    dt = lancador.agora(cfg, t)
    hoje = dt.date().isoformat()
    registo = agentes.ler_registo(_base(cfg) / "squads.json")
    linhas, mudou = [], False
    for r in doc["rotinas"]:
        nome = f"{r['squad']}/{r['id']}"
        mudou |= _fechar_tarefas(cfg, r)
        mudou |= _pausar(r, nome, linhas)
        if r["estado"] == "pendente":
            e = decisao(cfg, r)
            if e:
                r["estado"] = "aprovada" if e == "aprovado" else "recusada"
                linhas.append(f"rotina {nome} {r['estado']}")
                mudou = True
        if r["estado"] == "aprovada" and _hora_de(cfg, r, dt, hoje):
            pode = _pode(cfg, r, registo, t)
            mudou |= _motivo(r, nome, pode if isinstance(pode, str) else None, hoje, linhas)
            if pode is True:
                _lancar(cfg, cfg_path, r, dt, t, linhas)
                _pausar(r, nome, linhas)
                mudou = True
    if mudou:
        try:
            gravar(cfg, doc)
        except OSError:
            linhas.append("rotinas: registo não gravado (erro de escrita)")
    return linhas
