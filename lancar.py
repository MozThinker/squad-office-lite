"""Quadro: lançar um cartão manual na squad por uma fila de pedidos JSON (ADR-005).

Puro: não importa o server. O POST /api/quadro/lancar grava `<id>.json` em QUADRO_LANCAR_DIR e move o cartão para
«Em curso» sob o lock do cartoes.json (quadro._lock). O runner do host (runner/) lança e responde em
`<id>.feito.json` ({"task_id"} ou {"erro"}); o Office grava `<id>.movido.json` quando, no GET, move o cartão no fim da
execução (uma vez só). O estado do lançamento vive só na fila: o contrato v1 do cartoes.json não muda.
Nenhum valor do cliente entra num caminho: o nome do ficheiro sai do relógio e de secrets.token_hex(4).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import quadro

# preenchidos por configurar(cfg): só as squads formato worktree são lançáveis
SQUADS: tuple = ()
KINDS: dict = {}
MODES: dict = {}
PROJECTOS: tuple = ()       # ids dos projectos (o pedido leva o id)
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{1,58}[a-z0-9]")
NOME = re.compile(r"(\d{8}T\d{6}-[0-9a-f]{8})(\.feito|\.movido)?\.json")   # fullmatch
CHAVES_POST = {"token", "versao", "cartao_id", "posicao", "squad", "projecto", "kind", "mode", "slug", "titulo",
               "brief"}
BRIEF_MAX, PEDIDO_MAX, FEITO_MAX, ERRO_MAX = 8192, 32 * 1024, 4096, 200
FILA_N, PENDENTES_MAX = 500, 10
ILEGIVEL = "resposta ilegível do host"
INVISIVEIS = ("Cf", "Co", "Cs")   # formato/bidi/tags, uso privado, surrogates; sem Cn: emojis novos (Unicode > o do Python) seriam recusados
ZWJ = "\u200d"                          # junta emojis (👨‍👩‍👧): fica

_cache: dict[str, dict] = {}   # pasta → {nome: ((ino, mtime_ns, size), json | None)}
_cache_lock = threading.Lock()


def configurar(cfg: dict) -> None:
    global SQUADS, KINDS, MODES, PROJECTOS
    ws = [s for s in cfg["squads"] if s["formato"] == "worktree"]
    SQUADS = tuple(s["id"] for s in ws)
    KINDS = {s["id"]: tuple(s["tipos"]) for s in ws}
    MODES = {s["id"]: tuple(s["modos"]) for s in ws}
    PROJECTOS = tuple(p["id"] for p in cfg["projectos"])


# --- validação do POST ---
def _invisivel(s: str) -> bool:
    # texto que o utilizador não vê no diálogo (p.ex. U+202E, tags U+E0000–E007F): prompt injection escondida
    return any(unicodedata.category(c) in INVISIVEIS and c != ZWJ for c in s)


def validar(d: dict) -> str | None:
    # forma do corpo (400); None = ok. Normaliza titulo/brief em d.
    for v in d.values():
        if isinstance(v, str) and not quadro._utf8(v):
            return "JSON inválido"
    if set(d) != CHAVES_POST:
        return "campos inválidos"
    if not isinstance(d["versao"], str) or not quadro.VERSAO.fullmatch(d["versao"]):
        return "campos inválidos"
    if not isinstance(d["cartao_id"], str) or not quadro.ID.fullmatch(d["cartao_id"]):
        return "campos inválidos"
    p = d["posicao"]
    if not isinstance(p, int) or isinstance(p, bool) or p < 0:
        return "campos inválidos"
    s = d["squad"]
    if not isinstance(s, str) or s not in SQUADS:
        return "squad inválida"
    pj = d["projecto"]
    if not (isinstance(pj, str) and pj in PROJECTOS):
        return "projecto inválido"
    if not (isinstance(d["kind"], str) and d["kind"] in KINDS[s]):
        return "tipo inválido"
    if not (isinstance(d["mode"], str) and d["mode"] in MODES[s]):
        return "modo inválido"
    if not isinstance(d["slug"], str) or not SLUG.fullmatch(d["slug"]):
        return "slug inválido"
    if not isinstance(d["titulo"], str):
        return "campos inválidos"
    d["titulo"] = d["titulo"].replace("\r\n", "\n").strip()
    if not d["titulo"]:
        return "título obrigatório"
    if len(d["titulo"]) > quadro.TITULO_MAX:
        return "título com mais de 120 caracteres"
    if quadro._controlo(d["titulo"]) or _invisivel(d["titulo"]):
        return "título inválido"
    if not isinstance(d["brief"], str):
        return "campos inválidos"
    d["brief"] = d["brief"].replace("\r\n", "\n")
    if len(d["brief"].encode("utf-8")) > BRIEF_MAX:
        return "brief com mais de 8 KB"
    if quadro._controlo(d["brief"], "\n\t") or _invisivel(d["brief"]):
        return "brief inválido"
    return None


# --- leitura da fila ---
def _abrir(path: str, maximo: int):
    # JSON de um ficheiro regular (sem seguir symlinks) com ≤ maximo bytes; senão None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with open(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            raw = f.read(maximo + 1)
    except OSError:
        return None
    if len(raw) > maximo:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def _ler(fila: Path, entry: os.DirEntry, maximo: int):
    # com cache por (ino, mtime_ns, size): os pedidos são imutáveis, o poll não abre ficheiros
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return None
    chave = (st.st_ino, st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(str(fila), {}).get(entry.name)
    if hit and hit[0] == chave:
        return hit[1]
    valor = _abrir(entry.path, maximo) if stat.S_ISREG(st.st_mode) and st.st_size <= maximo else None
    with _cache_lock:
        _cache.setdefault(str(fila), {})[entry.name] = (chave, valor)
    return valor


def _pedido_ok(p, stem: str) -> bool:
    return (isinstance(p, dict) and type(p.get("v")) is int and p["v"] == 1 and p.get("id") == stem
            and isinstance(p.get("cartao_id"), str) and bool(quadro.ID.fullmatch(p["cartao_id"]))
            and p.get("squad") in SQUADS and isinstance(p.get("pedido_em"), str))


def ler_fila(fila: Path) -> tuple[dict[str, dict], dict[str, os.DirEntry]]:
    # → ({stem: pedido válido} dos FILA_N mais recentes, por ordem desc; {nome: DirEntry} de todos os nomes NOME)
    try:
        entradas = {e.name: e for e in os.scandir(fila) if NOME.fullmatch(e.name)}
    except OSError:
        entradas = {}
    with _cache_lock:
        velho = _cache.get(str(fila), {})
        _cache[str(fila)] = {k: v for k, v in velho.items() if k in entradas}
    stems = sorted((n[:-5] for n in entradas if NOME.fullmatch(n).group(2) is None), reverse=True)[:FILA_N]
    pedidos = {}
    for stem in stems:
        p = _ler(fila, entradas[stem + ".json"], PEDIDO_MAX)
        if _pedido_ok(p, stem):
            pedidos[stem] = p
    return pedidos, entradas


def _feito(fila: Path, stem: str, entradas: dict) -> tuple[str | None, str | None] | None:
    # None = sem resposta; (task_id mascarado, None) | (None, erro)
    e = entradas.get(stem + ".feito.json")
    if e is None:
        return None
    d = _ler(fila, e, FEITO_MAX)
    if isinstance(d, dict) and set(d) == {"task_id"}:
        ids = quadro._tid(d["task_id"])
        if ids is not None:
            return ids[0], None
    elif isinstance(d, dict) and set(d) == {"erro"} and isinstance(d["erro"], str):
        return None, quadro.txt(d["erro"], ERRO_MAX) or "erro sem texto"
    return None, ILEGIVEL


def _movido(stem: str, entradas: dict) -> bool:
    e = entradas.get(stem + ".movido.json")
    try:
        return e is not None and e.is_file(follow_symlinks=False)
    except OSError:
        return False


def _lancamento(fila: Path, stem: str, p: dict, entradas: dict) -> dict:
    f = _feito(fila, stem, entradas)
    tid, erro = f if f else (None, None)
    estado = "a-lancar" if f is None else "ligado" if tid else "erro"
    return {"id": stem, "estado": estado, "squad": p["squad"], "task_id": tid, "erro": erro,
            "pedido_em": quadro.txt(p["pedido_em"], 40)}


_ANTIGO = datetime.min.replace(tzinfo=timezone.utc)


def _instante(p: dict) -> datetime:
    # pedido_em com fuso; o nome usa a hora local, que repete na mudança da hora de Verão. Ilegível → o mais antigo
    try:
        dt = datetime.fromisoformat(p["pedido_em"])
    except ValueError:
        return _ANTIGO
    return dt if dt.tzinfo is not None else _ANTIGO


def _ultimos(pedidos: dict) -> dict[str, tuple[str, dict]]:
    # cartao_id → (stem, pedido) do mais recente por pedido_em (nome como desempate)
    out = {}
    for stem, p in pedidos.items():
        hit = out.get(p["cartao_id"])
        if hit is None or (_instante(p), stem) > (_instante(hit[1]), hit[0]):
            out[p["cartao_id"]] = (stem, p)
    return out


def _pendentes(pedidos: dict, entradas: dict) -> int:
    return sum(stem + ".feito.json" not in entradas for stem in pedidos)


# --- escrita ---
def _criar(fila: Path, nome: str, doc: dict) -> None:
    # tmp na mesma pasta + fsync + os.link (atómico e nunca sobrescreve: FileExistsError); OSError sobe
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=fila, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, fila / nome)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _com_lancamento(manuais: list[dict], fila: Path, pedidos: dict, entradas: dict) -> list[dict]:
    ult = _ultimos(pedidos)
    for c in manuais:
        hit = ult.get(c["id"])
        c["lancamento"] = _lancamento(fila, hit[0], hit[1], entradas) if hit else None
    return manuais


def escrever(fila: Path, pasta: Path, d: dict, projs: set, tz, now: float) -> tuple[int, dict]:
    # d já passou por validar(); devolve (código, corpo)
    with quadro._lock:
        if not quadro._escrita(fila):
            return 503, {"error": "fila indisponível"}
        lista, versao, idx, ok = quadro._carregar(pasta, projs)
        if not ok:
            return 503, {"error": "cartoes.json inválido — corrige à mão"}
        if d["versao"] != versao:
            return 409, {"error": "conflito", "versao": versao}
        alvo = next((i for i in idx if lista[i]["id"] == d["cartao_id"]), None)
        if alvo is None:
            return 404, {"error": "cartão não encontrado"}
        pedidos, entradas = ler_fila(fila)
        ult = _ultimos(pedidos).get(d["cartao_id"])
        if ult and _lancamento(fila, ult[0], ult[1], entradas)["estado"] != "erro":
            return 409, {"error": "já lançado"}
        if _pendentes(pedidos, entradas) >= PENDENTES_MAX:
            return 429, {"error": "fila cheia"}
        dt = datetime.fromtimestamp(now, tz)
        for tentativa in range(3):
            stem = f"{dt.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
            doc = {"v": 1, "id": stem, "cartao_id": d["cartao_id"], "squad": d["squad"], "projecto": d["projecto"],
                   "kind": d["kind"], "mode": d["mode"], "slug": d["slug"], "titulo": d["titulo"], "brief": d["brief"],
                   "pedido_em": dt.isoformat(timespec="seconds")}
            try:
                _criar(fila, stem + ".json", doc)
                break
            except FileExistsError:
                if tentativa == 2:
                    return 503, {"error": "fila indisponível"}
            except OSError:
                return 503, {"error": "fila indisponível"}
        lista[alvo].update(coluna="em-curso", actualizado=quadro._agora(now, tz))
        quadro._renumerar(lista, idx, "em-curso", alvo, d["posicao"])
        movido = True
        try:
            nova = quadro._gravar(pasta, lista)
        except OSError:
            movido = False
            lista, nova, idx, _ = quadro._carregar(pasta, projs)   # o que ficou no disco
            if lista is None:
                lista, idx = [], []
        pedidos, entradas = ler_fila(fila)
        manuais = _com_lancamento(quadro._manuais(lista, idx, now), fila, pedidos, entradas)
    return 202, {"id": stem, "versao": nova, "movido": movido, "cartoes": quadro.ordenar(manuais, now)}


# --- GET ---
def _fim(lanc: dict | None, auto: dict | None) -> str | None:
    # coluna para onde o manual vai quando a execução acabou (D10); None = ainda não acabou
    if lanc is None or lanc["estado"] == "a-lancar":
        return None
    if lanc["estado"] == "erro":
        return "a-espera"
    if auto is None:
        return None
    if (auto.get("selo") or {}).get("cor") == "vermelho":
        return "a-espera"
    return auto["coluna"] if auto["coluna"] in ("a-espera", "feito") else None


def _aplicar(out: dict, fila: Path, pedidos: dict, entradas: dict, marcados: set) -> tuple[set, list]:
    # lancamento em cada manual, herança do automático ligado; → (ids a esconder, fins por marcar)
    autos = {c["id"]: c for c in out["cartoes"] if c["tipo"] != "manual"}
    manuais = [c for c in out["cartoes"] if c["tipo"] == "manual"]
    _com_lancamento(manuais, fila, pedidos, entradas)
    esconder, fins = set(), []
    for c in manuais:
        lanc = c["lancamento"]
        auto = None
        if lanc and lanc["estado"] == "ligado":
            aid = f"{lanc['squad']}:{lanc['task_id']}"
            auto = autos.get(aid)
            if auto is not None:
                esconder.add(aid)
                c["progress"] = auto.get("progress")
                c["links"] = {"pr": auto["links"].get("pr"), "aprovacao": None, "tarefa": auto["links"].get("tarefa")}
                c["selo"] = auto.get("selo")
        destino = _fim(lanc, auto)
        if destino and lanc["id"] not in marcados and not _movido(lanc["id"], entradas):
            fins.append((lanc["id"], c["id"], destino))
    return esconder, fins


def _mover_fins(fila: Path, pasta: Path, projs: set, fins: list, tz, now: float):
    # sob o lock: move os que ainda estão em «Em curso» e grava as marcas; → (lista, idx, versão) se moveu, senão None
    with quadro._lock:
        lista, versao, idx, ok = quadro._carregar(pasta, projs)
        if not ok:
            return None, set()
        agora, mudou, marcas = quadro._agora(now, tz), False, []
        for stem, cid, destino in fins:
            if os.path.lexists(fila / f"{stem}.movido.json"):   # outro GET já tratou deste fim
                marcas.append((stem, None, False))
                continue
            i = next((i for i in idx if lista[i]["id"] == cid), None)
            if i is not None and lista[i]["coluna"] == "em-curso":
                lista[i].update(coluna=destino, actualizado=agora)
                quadro._renumerar(lista, idx, destino, i, 10 ** 6)
                mudou = True
                marcas.append((stem, destino, True))
            else:
                marcas.append((stem, None, True))
        if mudou:
            try:
                versao = quadro._gravar(pasta, lista)
            except OSError:
                return None, set()   # sem marcas: repete no próximo GET
        feitos = set()
        for stem, destino, escreve in marcas:
            feitos.add(stem)
            if escreve:
                try:
                    _criar(fila, f"{stem}.movido.json", {"v": 1, "coluna": destino, "em": agora})
                except FileExistsError:
                    pass
                except OSError:
                    feitos.discard(stem)
    return ((lista, idx, versao) if mudou else None), feitos


def completar(out: dict, fila: Path, pasta: Path, projs: set, tz, now: float) -> None:
    # pós-processa o dict de quadro.resposta(): lancar, dev_projectos, lancamento dos manuais, esconde os automáticos
    # ligados e aplica o movimento automático do fim (uma vez só, marca <id>.movido.json)
    out["lancar"] = quadro._escrita(fila)
    out["dev_projectos"] = list(quadro.DEV_PROJECTOS)   # ids lançáveis
    pedidos, entradas = ler_fila(fila)
    esconder, fins = _aplicar(out, fila, pedidos, entradas, set())
    if fins and out.get("escrita") and out["lancar"]:
        relido, marcados = _mover_fins(fila, pasta, projs, fins, tz, now)
        if relido is not None:
            lista, idx, versao = relido
            out["versao"] = versao
            out["cartoes"] = [c for c in out["cartoes"] if c["tipo"] != "manual"] + quadro._manuais(lista, idx, now)
            esconder, _ = _aplicar(out, fila, pedidos, entradas, marcados)
    out["cartoes"] = quadro.ordenar([c for c in out["cartoes"] if c["id"] not in esconder], now)
