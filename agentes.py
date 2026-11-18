"""Agentes, skills, squads por modelo e rotinas (ADR-017…019): regras partilhadas pelo Office e pelo runner.

Sem estado global e sem imports do projecto no topo (o `conversa`, que importa este, só dentro das funções): o
Office (`server.py`) e o runner (`runner/agentes_fila.py`, `runner/rotinas.py`, `runner/agentes_copia.py`, no host ou
no contentor `runner`) importam este módulo, por isso o parser de frontmatter, a validação dos pedidos e a reescrita
dos `.md` nunca divergem. Só stdlib, Python ≥ 3.10.

Pastas: `<dados>/agentes/<squad>/agents/<nome>.md`, `<dados>/agentes/<squad>/skills/<skill>/SKILL.md`,
`<dados>/agentes/<squad>/CLAUDE.md`; registos `<dados>/agentes/squads.json` e `rotinas.json` (só o runner escreve);
candidatos em `<dados>/agentes-rascunhos/`; fila em `<dados>/agentes-fila/`.

Fila (contrato v1): o Office grava `<id>.json` com as chaves CHAVES por esta ordem e `args` com as chaves exactas de
ACCOES[accao] (tmp + fsync + `os.link`); o runner revalida tudo e responde `<id>.feito.json` com `{"ok": true}` ou
`{"erro": <um de ERROS>}`. Nunca se devolve o corpo de um `.md` na leitura geral (só o frontmatter permitido).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

ID_SQUAD = re.compile(r"[a-z][a-z0-9-]{1,19}")          # = officecfg._ID_SQUAD
ID_AGENTE = re.compile(r"[a-z][a-z0-9-]{1,39}")         # = officecfg._LEAD (agentes, skills, modelos de squad)
ID_PROJECTO = re.compile(r"[a-z][a-z0-9-]{1,23}")       # = officecfg._ID_LONGO
RID = re.compile(r"r[1-9][0-9]{0,2}")
HORA = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
SHA = re.compile(r"[0-9a-f]{64}")
NOME_PEDIDO = re.compile(r"\d{8}T\d{6}-[0-9a-f]{8}\.json")             # fullmatch
FEITO = re.compile(r"(\d{8}T\d{6}-[0-9a-f]{8})\.feito\.json")          # fullmatch
TECTO = re.compile(r"[0-9]\.[0-9][0-9]")

MODELOS_AGENTE = ("inherit", "sonnet", "opus", "haiku")
MODELOS_NEGOCIO = ("atendimento", "comercial")   # squads de negócio (ADR-020); = accoes.MODELOS_NEGOCIO
CHAVES_FM = ("name", "description", "tools", "model", "skills", "permissionMode")
PERMISSION_OK = ("default", "acceptEdits", "plan", "dontAsk")
CHAVES = ("v", "id", "accao", "args", "pedido_em", "ip", "ua")         # exactas e por esta ordem
ACCOES: dict[str, tuple[str, ...]] = {
    "modelo": ("squad", "agente", "sha256", "modelo"),
    "skills": ("squad", "agente", "sha256", "skills"),
    "agente_remover": ("squad", "agente", "sha256"),
    "agente_rascunho": ("squad", "nome", "descricao"),
    "agente_criar": ("squad", "nome", "sha256", "md"),
    "agente_ensaio": ("squad", "nome", "sha256", "projecto", "tarefa"),
    "agente_activar": ("squad", "nome", "sha256"),
    "agente_descartar": ("squad", "nome"),
    "squad_rascunho": ("squad", "modelo", "missao"),
    "squad_criar": ("squad", "sha256"),
    "squad_ensaio": ("squad", "sha256", "projecto", "tarefa"),
    "squad_descartar": ("squad",),
    "squad_conversa": ("squad", "texto"),
    "squad_desactivar": ("squad",),
    "rotina_pedir": ("squad", "projecto", "hora", "dias", "instrucoes", "tecto_usd", "max_turnos"),
    "rotina_retomar": ("squad", "rotina"),
    "rotina_cancelar": ("squad", "rotina"),
}
ACCOES_IA = {"agente_rascunho": 0.50, "squad_rascunho": 0.50, "agente_ensaio": 2.0, "squad_ensaio": 2.0,
             "squad_conversa": 0.15}
IA_DIA_USD = 10.0
PENDENTES_MAX = 50
MD_MAX = 80 * 1024           # .md de um agente activo (leitura)
CAND_MAX = 64 * 1024         # candidato (md de agente_criar, rascunhos)
PEDIDO_MAX = 96 * 1024       # ficheiro da fila
FM_MAX = 8192                # frontmatter
TEXTO_MAX = 2000             # descricao, missao, tarefa, instrucoes
CONVERSA_MAX = 1000          # texto de uma volta da conversa (= conversa.TEXTO_MAX)
SKILLS_MAX = 20
DESC_MAX = 300
LISTA_MAX = 20
ROTINAS_MAX = 5              # vivas (nem canceladas nem recusadas), por squad
REGISTO_MAX = 256 * 1024     # squads.json, rotinas.json
IP_MAX, UA_MAX, PEDIDO_EM_MAX = 64, 200, 40
ESTADOS_ROTINA = ("pendente", "aprovada", "recusada", "pausada", "cancelada")
ERROS = ("pedido ilegível", "versão desconhecida", "pedido repetido nesta passagem", "squad desconhecida",
         "agente inexistente", "skill inexistente", "modelo inválido", "frontmatter inválido",
         "o agente mudou depois de ser visto", "o rascunho mudou depois de ser visto", "já existe",
         "candidato inexistente", "sem ensaio ok desta versão", "o lead não pode ser removido", "projecto inválido",
         "modelo de squad inexistente", "squad já tem agentes", "rascunho inexistente", "squad não registada",
         "squad não lançável", "limite de rotinas", "rotina inexistente", "rotina não pausada",
         "tecto diário de IA atingido", "a IA falhou", "tempo esgotado", "erro de escrita",
         "já há uma conversa em curso", "tecto da conversa atingido", "a conversa já terminou",
         "a IA não respondeu bem, envia de novo", "não foi possível montar a squad")
MODELOS_DIR = Path(__file__).resolve().parent / "modelos" / "squads"
ZWJ = "\u200d"

_CHAVE = re.compile(r"([a-z][A-Za-z0-9_-]{0,31}):(?:[ \t]+(.*?))?[ \t]*")
_ITEM = re.compile(r"[ \t]+-(?:[ \t]+(.*?))?[ \t]*")
_TOOL = re.compile(r"[A-Z][A-Za-z]{1,40}")
_MODELO_LIVRE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")   # model: de um .md existente (= officecfg._MODELO)
_ESTRUTURAIS = ("skills", "model", "tools")   # um bloco `|`/`>` ou texto encaixado nestas chaves = ilegível
_SEGREDOS = [re.compile(p) for p in (
    r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"sk-(?:ant-)?[A-Za-z0-9_-]{20,}",
    r"AKIA[0-9A-Z]{16}", r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    r"postgres(?:ql)?://[^\s:/@]+:[^\s@]+@", r"(?<![0-9])[0-9]{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])",
    r"EAA[A-Za-z0-9]{20,}")]
# `(?<![a-z0-9])` em vez de `\b`: também apanha GITHUB_TOKEN=…, API_SECRET: … (o `_` conta como separador)
_SEGREDO_CHAVE = re.compile(r"(?i)(?<![a-z0-9])(password|passwd|secret|token|api_key|apikey)(\s*[=:]\s*)"
                            r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)")


# --- texto ----------------------------------------------------------------------------------------------------------

def _invisivel(c: str) -> bool:
    # Cf/Co/Cs (bidi, privados, surrogates), salvo o ZWJ (emojis compostos); U+2028/2029 partem linhas a uns leitores
    return (c != ZWJ and unicodedata.category(c) in ("Cf", "Co", "Cs")) or c in "\u2028\u2029"


def texto_ok(s, mn: int, mx: int, linhas: bool) -> bool:
    """Texto do utilizador: mn ≤ len ≤ mx; sem Cc (salvo `\\n`/`\\t` se `linhas`) nem Cf/Co/Cs (salvo ZWJ)."""
    if not isinstance(s, str) or not mn <= len(s) <= mx:
        return False
    return not any((unicodedata.category(c) == "Cc" and not (linhas and c in "\n\t")) or _invisivel(c) for c in s)


def tapar(s: str) -> str:
    """Troca por `[tapado]` tokens, chaves privadas, JWT e `password|secret|token… = valor`."""
    for rx in _SEGREDOS:
        s = rx.sub("[tapado]", s)
    return _SEGREDO_CHAVE.sub(r"\1\2[tapado]", s)


def _uma_linha(v, n: int) -> str:
    s = " ".join(v.split()) if isinstance(v, str) else ""
    return s if len(s) <= n else s[:n - 1] + "…"


# --- frontmatter ----------------------------------------------------------------------------------------------------

def _escalar(v: str) -> str | None:
    # valor de uma linha: aspas tiradas; None se for algo que este parser recusa (âncora, alias, tag)
    v = v.strip()
    if v[:1] in ("&", "*", "!"):
        return None
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def _bloco(texto: str) -> tuple[list[str], int] | None:
    # (linhas do frontmatter sem o \r final, índice da linha do fecho) ou None
    if not isinstance(texto, str) or texto.startswith("\ufeff"):
        return None
    linhas = texto[:FM_MAX + 8].split("\n")
    if linhas[0].rstrip("\r") != "---":
        return None
    tam = len(linhas[0]) + 1
    for i in range(1, len(linhas)):
        tam += len(linhas[i]) + 1
        if tam > FM_MAX + 1:
            return None
        if linhas[i].rstrip("\r") == "---":
            return [ln.removesuffix("\r") for ln in linhas[1:i]], i
    return None


def frontmatter(texto: str) -> dict | None:
    """Chaves do frontmatter (str, ou list[str] para listas em bloco; "" sem valor); None = ilegível.

    1.ª linha exactamente `---` (CRLF aceite), fecho `---` nos primeiros FM_MAX caracteres. Recusa BOM, chave repetida
    ou fora de `[a-z][A-Za-z0-9_-]*`, controlos, `---` dentro do bloco (o Claude Code cortava aí), âncoras/aliases/
    tags e blocos `|`/`>` ou texto encaixado em skills/model/tools. Nunca levanta.
    """
    b = _bloco(texto)
    if b is None:
        return None
    out: dict = {}
    chave, bloco = None, False   # chave sem valor à espera de `- x`; bloco = `|`/`>` (linhas indentadas ignoradas)
    for ln in b[0]:
        if "---" in ln or any(unicodedata.category(c) == "Cc" and c != "\t" for c in ln):
            return None
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[0] in " \t":   # continuação: item de lista, linha de um bloco, ou mapa encaixado (ignorado)
            if chave is None or bloco:
                continue
            m = _ITEM.fullmatch(ln)
            if m and isinstance(out[chave], list):
                v = _escalar(m.group(1) or "")
                if v is None:
                    return None
                out[chave].append(v)
            elif chave in _ESTRUTURAIS:
                return None
            elif out[chave] == []:   # mapa ou texto encaixado: não é lista
                out[chave], chave = "", None
            continue
        m = _CHAVE.fullmatch(ln)
        if not m or m.group(1) in out:
            return None
        k, v = m.group(1), m.group(2)
        chave, bloco = None, False
        if not v:
            out[k], chave = [], k
            continue
        if v[:1] in ("|", ">"):
            if k in _ESTRUTURAIS:
                return None
            out[k], chave, bloco = "", k, True
            continue
        v = _escalar(v)
        if v is None:
            return None
        out[k], chave = v, k
    return {k: ("" if v == [] else v) for k, v in out.items()}


def lista(v) -> list[str]:
    """`None`/"" → []; lista em bloco; `[a, b]`; `a, b`; `a`."""
    if v is None:
        return []
    if isinstance(v, list):
        itens = v
    else:
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        itens = s.split(",")
    out = []
    for x in itens:
        x = x.strip()
        if len(x) >= 2 and x[0] == x[-1] and x[0] in "\"'":
            x = x[1:-1].strip()
        if x:
            out.append(x)
    return out


def _so_listas(linhas: list[str]) -> bool:
    # estrito: uma linha indentada só pode ser `- item` de uma chave sem valor (YAML juntava-a ao escalar anterior)
    em_lista = False
    for ln in linhas:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[0] in " \t":
            if not (em_lista and _ITEM.fullmatch(ln)):
                return False
            continue
        m = _CHAVE.fullmatch(ln)
        em_lista = bool(m) and not m.group(2)
    return True


def _desc_yaml_ok(linhas: list[str]) -> bool:
    # description sem aspas que o YAML do Claude Code leria de outra forma (`a: b`, ` #`, indicador no início)
    for ln in linhas:
        m = _CHAVE.fullmatch(ln)
        if m and m.group(1) == "description" and m.group(2):
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                return v[0] not in v[1:-1] and "\\" not in v[1:-1]
            return v[:1] not in "[]{}&*!|>'\"%@`#,?:-" and ": " not in v and " #" not in v
    return True


def validar_md(texto: str, nome: str, skills: set[str]) -> str | None:
    """Regra estrita de um `.md` de agente (candidato, activar, modelos): None se serve, senão o erro (de ERROS).

    Chaves ⊆ CHAVES_FM; name == nome; description numa linha, 1–300; tools ⊆ `[A-Z][A-Za-z]{1,40}`, ≤ 20, únicas;
    model ∈ MODELOS_AGENTE ou ausente; skills ⊆ `skills`; permissionMode ∈ PERMISSION_OK; nunca `bypassPermissions`;
    sem caracteres invisíveis ou de controlo; sem linhas indentadas fora de listas.
    """
    if not isinstance(texto, str) or len(texto.encode("utf-8", "surrogatepass")) > CAND_MAX:
        return "frontmatter inválido"
    if any((unicodedata.category(c) == "Cc" and c not in "\n\r\t") or _invisivel(c) for c in texto):
        return "frontmatter inválido"
    fm, b = frontmatter(texto), _bloco(texto)
    if fm is None or b is None or not _so_listas(b[0]) or "bypassPermissions" in "\n".join(b[0]):
        return "frontmatter inválido"
    if any(k not in CHAVES_FM for k in fm) or fm.get("name") != nome:
        return "frontmatter inválido"
    desc = fm.get("description")
    if not isinstance(desc, str) or not 1 <= len(desc.strip()) <= DESC_MAX or not _desc_yaml_ok(b[0]):
        return "frontmatter inválido"
    if "tools" in fm:
        tools = lista(fm["tools"])
        if (not tools or len(tools) > LISTA_MAX or len(set(tools)) != len(tools)
                or not all(_TOOL.fullmatch(t) for t in tools)):
            return "frontmatter inválido"
    if "model" in fm and fm["model"] not in MODELOS_AGENTE:
        return "modelo inválido"
    if "permissionMode" in fm and fm["permissionMode"] not in PERMISSION_OK:
        return "frontmatter inválido"
    if "skills" in fm:
        sk = lista(fm["skills"])
        if len(sk) > SKILLS_MAX or len(set(sk)) != len(sk) or not all(ID_AGENTE.fullmatch(s) for s in sk):
            return "frontmatter inválido"
        if any(s not in skills for s in sk):
            return "skill inexistente"
    return None


# --- reescrita (bytes → bytes; só a linha/bloco do campo muda) --------------------------------------------------------

def _campo(raw: bytes, chave: bytes) -> tuple[list[bytes], int, int, int, bytes]:
    """(linhas, início do campo ou -1, n.º de linhas do campo, índice do `---` de fecho, fim de linha)."""
    linhas = raw.split(b"\n")
    if linhas[0].rstrip(b"\r") != b"---":
        raise ValueError("frontmatter inválido")
    fecho = next((i for i in range(1, len(linhas)) if linhas[i].rstrip(b"\r") == b"---"), None)
    if fecho is None:
        raise ValueError("frontmatter inválido")
    eol = b"\r" if linhas[0].endswith(b"\r") else b""
    ini, n = -1, 0
    pat = re.compile(re.escape(chave) + rb":(?:[ \t].*)?\r?")
    for i in range(1, fecho):
        if pat.fullmatch(linhas[i]):
            # vazias e comentários não acabam o bloco; acaba na próxima chave em coluna 0; o bloco vai até à última
            # linha indentada (vazias/comentários depois dela ficam onde estão)
            ini, n = i, 1
            for j in range(i + 1, fecho):
                ln = linhas[j].rstrip(b"\r")
                if not ln.strip() or ln.lstrip().startswith(b"#"):
                    continue
                if ln[:1] not in (b" ", b"\t"):
                    break
                n = j - i + 1
            break
    return linhas, ini, n, fecho, eol


def _trocar(raw: bytes, chave: bytes, novas: list[bytes]) -> bytes:
    linhas, ini, n, fecho, eol = _campo(raw, chave)
    novas = [x + eol for x in novas]
    if ini < 0:
        linhas[fecho:fecho] = novas
    else:
        linhas[ini:ini + n] = novas
    return b"\n".join(linhas)


def reescrever_modelo(raw: bytes, modelo: str) -> bytes:
    """`model: <modelo>` no lugar da linha actual (ou antes do fecho); "" tira a linha. ValueError sem frontmatter."""
    return _trocar(raw, b"model", [b"model: " + modelo.encode()] if modelo else [])


def reescrever_skills(raw: bytes, skills: list[str]) -> bytes:
    """0 skills → sem campo; 1 → `skills: a`; ≥ 2 → lista em bloco (indentação da lista existente ou 2 espaços)."""
    linhas, ini, n, _, _ = _campo(raw, b"skills")
    ind = b"  "
    for ln in linhas[ini + 1:ini + n] if ini >= 0 else []:
        m = re.match(rb"([ \t]+)-", ln)
        if m:
            ind = m.group(1)
            break
    b = [s.encode() for s in skills]
    novas = [] if not b else [b"skills: " + b[0]] if len(b) == 1 else [b"skills:", *(ind + b"- " + s for s in b)]
    return _trocar(raw, b"skills", novas)


# --- leitura do disco -----------------------------------------------------------------------------------------------

def ler_bytes(path: Path, maximo: int) -> bytes | None:
    """Bytes de um ficheiro regular (sem seguir symlinks no último componente) com ≤ maximo bytes; senão None.
    Nunca abre `.env*`."""
    if Path(path).name.startswith(".env"):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        with open(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None
            raw = f.read(maximo + 1)
    except OSError:
        return None
    return raw if len(raw) <= maximo else None


def _pasta(p: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(p).st_mode)
    except OSError:
        return False


def _entradas(pasta: Path, maximo: int = 500) -> list[os.DirEntry]:
    if not _pasta(pasta):
        return []
    try:
        with os.scandir(pasta) as it:
            return sorted(it, key=lambda e: e.name)[:maximo]
    except OSError:
        return []


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def agente_publico(ficheiro: str, raw: bytes | None) -> dict:
    """Um agente para o browser (§3.5): só o frontmatter permitido, cortado; nunca o corpo.
    Ilegível (`{"ficheiro", "ilegivel": True}`) se não houver bytes, passar de MD_MAX, não for UTF-8 ou o
    frontmatter não se ler."""
    ileg = {"ficheiro": ficheiro, "ilegivel": True}
    if raw is None or len(raw) > MD_MAX:
        return ileg
    try:
        fm = frontmatter(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return ileg
    if fm is None:
        return ileg
    model, pm = fm.get("model") or None, fm.get("permissionMode") or None
    if (model is not None and (not isinstance(model, str) or not _MODELO_LIVRE.fullmatch(model))) \
            or (pm is not None and not isinstance(pm, str)):
        return ileg
    nome = ficheiro.rsplit("/", 1)[-1].removesuffix(".md")
    return {"nome": nome, "ficheiro": ficheiro, "sha256": _sha(raw),
            "description": _uma_linha(fm.get("description"), DESC_MAX),
            "model": model, "tools": [t[:64] for t in lista(fm.get("tools"))][:LISTA_MAX],
            "skills": [s[:64] for s in lista(fm.get("skills"))][:LISTA_MAX],
            "permissionMode": pm[:40] if pm else None, "lead": False}


def skills_de(base_squad: Path) -> list[str]:
    """Skills da squad: pastas `skills/<nome>/` (nome de ID_AGENTE, sem symlinks) com SKILL.md regular."""
    out = []
    if not _pasta(Path(base_squad)):
        return out
    for e in _entradas(Path(base_squad) / "skills"):
        if not ID_AGENTE.fullmatch(e.name) or not _pasta(Path(e.path)):
            continue
        try:
            if stat.S_ISREG(os.lstat(Path(e.path) / "SKILL.md").st_mode):
                out.append(e.name)
        except OSError:
            pass
    return out


def agentes_de(base_squad: Path) -> list[tuple[str, bytes | None]]:
    """(ficheiro "agents/x.md", bytes ou None) dos `agents/*.md` de topo com nome de ID_AGENTE; symlink, não
    regular ou > MD_MAX → None (aparece ilegível). Pasta da squad ou `agents` symlink ou ausente → []."""
    out = []
    if not _pasta(Path(base_squad)):
        return out
    for e in _entradas(Path(base_squad) / "agents"):
        if not e.name.endswith(".md") or not ID_AGENTE.fullmatch(e.name[:-3]):
            continue
        if _pasta(Path(e.path)):
            continue
        out.append((f"agents/{e.name}", ler_bytes(Path(e.path), MD_MAX)))
    return out


# --- registos do runner ---------------------------------------------------------------------------------------------

def _ler_json(path: Path, maximo: int):
    raw = ler_bytes(path, maximo)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def _squad_registo_ok(s) -> bool:
    return (isinstance(s, dict) and isinstance(s.get("id"), str) and bool(ID_SQUAD.fullmatch(s["id"]))
            and isinstance(s.get("modelo"), str) and bool(ID_AGENTE.fullmatch(s["modelo"]))
            and texto_ok(s.get("nome"), 1, 80, False) and isinstance(s.get("lead"), str)
            and bool(ID_AGENTE.fullmatch(s["lead"])) and isinstance(s.get("criada_em"), str))


def ler_registo(path: Path) -> dict:
    """`squads.json` → {"v": 1, "squads": [...]} só com entradas válidas e ids únicos; inválido/ausente → vazio."""
    d = _ler_json(Path(path), REGISTO_MAX)
    out, vistos = [], set()
    if isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("squads"), list):
        for s in d["squads"]:
            if _squad_registo_ok(s) and s["id"] not in vistos:
                vistos.add(s["id"])
                out.append({k: s[k] for k in ("id", "modelo", "nome", "lead", "criada_em")})
    return {"v": 1, "squads": out}


def _rotina_ok(r) -> bool:
    if not isinstance(r, dict):
        return False
    try:
        defn = {k: r[k] for k in ACCOES["rotina_pedir"]}
        return (bool(RID.fullmatch(r["id"])) and bool(ID_SQUAD.fullmatch(r["squad"]))
                and r["estado"] in ESTADOS_ROTINA and _args_ok("rotina_pedir", defn) is None
                and type(r["falhas"]) is int and r["falhas"] >= 0 and isinstance(r["tarefas"], list)
                and all(isinstance(t, str) for t in r["tarefas"])
                and (r["ultima"] is None or isinstance(r["ultima"], str)) and isinstance(r["sha256"], str)
                and isinstance(r["decisao"], str) and isinstance(r["criada_em"], str))
    except (KeyError, TypeError):
        return False


def ler_rotinas(path: Path) -> dict:
    """`rotinas.json` → {"v": 1, "rotinas": [...]} só com entradas válidas; inválido/ausente → vazio."""
    d = _ler_json(Path(path), REGISTO_MAX)
    out = []
    if isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("rotinas"), list):
        out = [r for r in d["rotinas"] if _rotina_ok(r)]
    return {"v": 1, "rotinas": out}


def linha_rotina(defn: dict) -> tuple[str, str]:
    """(linha canónica, sha256) da definição: squad, id, projecto, hora, dias, instrucoes, tecto_usd, max_turnos."""
    chaves = ("squad", "id", "projecto", "hora", "dias", "instrucoes", "tecto_usd", "max_turnos")
    linha = json.dumps({k: defn[k] for k in chaves}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return linha, hashlib.sha256(linha.encode()).hexdigest()


def modelos_squad(raiz: Path = MODELOS_DIR) -> list[dict]:
    """[{id, nome, lead, resumo, agentes: [nomes]}] dos modelos em `raiz/<id>/` (README.md com frontmatter `nome`,
    `lead`, `resumo`; `agents/*.md`); os incompletos (sem README legível, lead fora dos agentes) ficam de fora."""
    out = []
    for e in _entradas(Path(raiz), 100):
        if not ID_AGENTE.fullmatch(e.name) or not _pasta(Path(e.path)):
            continue
        raw = ler_bytes(Path(e.path) / "README.md", CAND_MAX)
        try:
            fm = frontmatter(raw.decode("utf-8")) if raw is not None else None
        except UnicodeDecodeError:
            fm = None
        if not fm:
            continue
        nome, lead, resumo = fm.get("nome"), fm.get("lead"), fm.get("resumo")
        agentes = [f[len("agents/"):-3] for f, r in agentes_de(Path(e.path)) if r is not None]
        if not (texto_ok(nome, 1, 80, False) and isinstance(lead, str) and lead in agentes
                and texto_ok(resumo, 1, DESC_MAX, False)):
            continue
        out.append({"id": e.name, "nome": nome, "lead": lead, "resumo": resumo, "agentes": agentes})
    return out


def modelo_negocio(registo: dict, sid: str) -> str | None:
    """Modelo da entrada `sid` de `squads.json` (já lido por `ler_registo`) se for de negócio (MODELOS_NEGOCIO);
    senão None (squad fora do registo ou de outro modelo)."""
    for s in registo.get("squads", []) if isinstance(registo, dict) else []:
        if isinstance(s, dict) and s.get("id") == sid:
            return s.get("modelo") if s.get("modelo") in MODELOS_NEGOCIO else None
    return None


def lead_de(cfg, registo: dict, squad: str) -> str | None:
    """Lead da squad: o do registo (squad criada por modelo) ganha ao do office.yaml; None se não existir."""
    for s in registo.get("squads", []):
        if s.get("id") == squad:
            return s.get("lead")
    for s in cfg["squads"]:
        if s["id"] == squad:
            return s["lead"]
    return None


# --- pedidos da fila ------------------------------------------------------------------------------------------------

def _int(v, mn: int, mx: int) -> bool:
    return type(v) is int and mn <= v <= mx


def _arg_ok(accao: str, k: str, v) -> str | None:
    # None se o valor tem a forma certa; senão o erro (de ERROS)
    if k == "squad":
        return None if isinstance(v, str) and ID_SQUAD.fullmatch(v) else "pedido ilegível"
    if k in ("agente", "nome", "modelo") and not (k == "modelo" and accao == "modelo"):
        return None if isinstance(v, str) and ID_AGENTE.fullmatch(v) else "pedido ilegível"
    if k == "modelo":
        return None if v == "" or v in MODELOS_AGENTE else "modelo inválido"
    if k == "sha256":
        if v is None and accao == "agente_criar":
            return None
        return None if isinstance(v, str) and SHA.fullmatch(v) else "pedido ilegível"
    if k == "skills":
        ok = (isinstance(v, list) and len(v) <= SKILLS_MAX and len(set(map(str, v))) == len(v)
              and all(isinstance(s, str) and ID_AGENTE.fullmatch(s) for s in v))
        return None if ok else "pedido ilegível"
    if k == "texto":
        return None if texto_ok(v, 1, CONVERSA_MAX, True) and v.strip() else "pedido ilegível"
    if k in ("descricao", "missao", "tarefa", "instrucoes"):
        return None if texto_ok(v, 1, TEXTO_MAX, True) and v.strip() else "pedido ilegível"
    if k == "md":
        ok = texto_ok(v, 1, CAND_MAX, True) and len(v.encode("utf-8")) <= CAND_MAX
        return None if ok else "pedido ilegível"
    if k == "projecto":
        return None if isinstance(v, str) and ID_PROJECTO.fullmatch(v) else "projecto inválido"
    if k == "hora":
        return None if isinstance(v, str) and HORA.fullmatch(v) else "pedido ilegível"
    if k == "dias":
        ok = (isinstance(v, list) and 1 <= len(v) <= 7 and all(_int(d, 1, 7) for d in v)
              and all(v[i] < v[i + 1] for i in range(len(v) - 1)))
        return None if ok else "pedido ilegível"
    if k == "tecto_usd":
        ok = isinstance(v, str) and TECTO.fullmatch(v) and 0.10 <= float(v) <= 5.00
        return None if ok else "pedido ilegível"
    if k == "max_turnos":
        return None if _int(v, 1, 30) else "pedido ilegível"
    if k == "rotina":
        return None if isinstance(v, str) and RID.fullmatch(v) else "pedido ilegível"
    return "pedido ilegível"   # pragma: no cover - chaves vêm de ACCOES


def _args_ok(accao: str, args) -> str | None:
    if accao not in ACCOES or not isinstance(args, dict) or set(args) != set(ACCOES[accao]) \
            or len(args) != len(ACCOES[accao]):
        return "pedido ilegível"
    for k in ACCOES[accao]:
        erro = _arg_ok(accao, k, args[k])
        if erro:
            return erro
    return None


def validar_args(accao, args) -> str | None:
    """Só a forma de `args` para `accao` (chaves exactas e limites); None ou o erro (de ERROS)."""
    return _args_ok(accao, args) if isinstance(accao, str) else "pedido ilegível"


def validar_pedido(p, stem: str) -> str | None:
    """Pedido v1 da fila: chaves exactas por ordem, `id` = stem, args exactos e nos limites; None ou o erro."""
    if isinstance(p, dict) and "v" in p and not (type(p["v"]) is int and p["v"] == 1):
        return "versão desconhecida"
    if not isinstance(p, dict) or tuple(p) != CHAVES or p["id"] != stem or not NOME_PEDIDO.fullmatch(stem + ".json"):
        return "pedido ilegível"
    if not (isinstance(p["pedido_em"], str) and len(p["pedido_em"]) <= PEDIDO_EM_MAX
            and isinstance(p["ip"], str) and len(p["ip"]) <= IP_MAX
            and isinstance(p["ua"], str) and len(p["ua"]) <= UA_MAX):
        return "pedido ilegível"
    try:
        if datetime.fromisoformat(p["pedido_em"]).tzinfo is None:
            return "pedido ilegível"
    except ValueError:
        return "pedido ilegível"
    return validar_args(p["accao"], p["args"])


def alvo(accao: str, args: dict) -> str:
    """Alvo do pedido (1 pendente por alvo): agente:, cand:, squad:, rotina: (§3.2)."""
    if accao in ("modelo", "skills", "agente_remover"):
        return f"agente:{args['squad']}/{args['agente']}"
    if accao.startswith("agente_"):
        return f"cand:{args['squad']}/{args['nome']}"
    if accao.startswith("squad_"):
        return f"squad:{args['squad']}"
    if accao == "rotina_pedir":
        return f"rotina:{args['squad']}"
    return f"rotina:{args['squad']}/{args['rotina']}"


# --- só servidor: GET /api/agentes, candidato, POST /api/agentes/pedido -----------------------------------------------
RASCUNHO_MAX = 512 * 1024    # rascunho.json de uma squad (vários .md)
ENSAIO_MAX = 32 * 1024
RELATORIO_MAX = 8000
FEITO_MAX = 4096
PEDIDOS_VISTOS = 50          # pedidos na resposta (os mais recentes)
INDISPONIVEL = "fila dos agentes indisponível"
_lock = threading.Lock()     # serializa a contagem de pendentes e a escrita na fila (um processo: o servidor)


def _texto_fm(v, n: int) -> str | None:
    return _uma_linha(v, n) or None if isinstance(v, str) else None


def _num(v) -> float | int | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def _ensaio(path: Path) -> dict | None:
    """`*.ensaio.json`/`ensaio.json` (§3.3) sem o "v"; inválido ou ausente → None."""
    d = _ler_json(path, ENSAIO_MAX)
    try:
        ok = (isinstance(d, dict) and d.get("v") == 1 and SHA.fullmatch(d["sha256"]) is not None
              and d["estado"] in ("ok", "erro") and (d["motivo"] is None or isinstance(d["motivo"], str))
              and isinstance(d["relatorio"], str) and isinstance(d["nao_ensaiadas"], list)
              and all(isinstance(t, str) for t in d["nao_ensaiadas"]) and isinstance(d["em"], str)
              and (d["custo_usd"] is None or _num(d["custo_usd"]) is not None))
    except (KeyError, TypeError):
        ok = False
    if not ok:
        return None
    return {"sha256": d["sha256"], "estado": d["estado"], "motivo": d["motivo"] and d["motivo"][:300],
            "relatorio": d["relatorio"][:RELATORIO_MAX], "nao_ensaiadas": [t[:64] for t in d["nao_ensaiadas"]][:40],
            "custo_usd": d["custo_usd"], "em": d["em"][:40]}


def _candidatos(rasc: Path, squad: str) -> list[dict]:
    pasta = Path(rasc) / "agentes" / squad
    if not _pasta(Path(rasc) / "agentes"):
        return []
    out = []
    for e in _entradas(pasta):
        if not e.name.endswith(".md") or not ID_AGENTE.fullmatch(e.name[:-3]):
            continue
        raw = ler_bytes(Path(e.path), CAND_MAX)
        if raw is None:
            continue
        nome = e.name[:-3]
        a = agente_publico(f"agents/{e.name}", raw)
        out.append({"nome": nome, "sha256": _sha(raw), "description": a.get("description") or None,
                    "model": a.get("model"), "tools": a.get("tools", []), "skills": a.get("skills", []),
                    "ensaio": _ensaio(pasta / f"{nome}.ensaio.json")})
    return out


def _rascunho(rasc: Path, squad: str) -> tuple[dict, str] | None:
    """(rascunho.json válido, sha256 do ficheiro) da squad, ou None."""
    pasta = Path(rasc) / "squads"
    if not _pasta(pasta) or not _pasta(pasta / squad):
        return None
    raw = ler_bytes(pasta / squad / "rascunho.json", RASCUNHO_MAX)
    try:
        d = json.loads(raw.decode("utf-8")) if raw is not None else None
    except ValueError:
        return None
    ok = (isinstance(d, dict) and d.get("v") == 1 and d.get("squad") == squad
          and all(isinstance(d.get(k), str) for k in ("modelo", "nome", "lead", "missao"))
          and isinstance(d.get("ficheiros"), dict)
          and all(isinstance(k, str) and isinstance(v, str) for k, v in d["ficheiros"].items()))
    return (d, _sha(raw)) if ok else None


def _rascunho_publico(r: tuple[dict, str] | None) -> dict | None:
    if r is None:
        return None
    d, sha = r
    agentes = []
    for k, v in sorted(d["ficheiros"].items()):
        if k.startswith("agents/") and k.endswith(".md") and ID_AGENTE.fullmatch(k[7:-3]):
            fm = frontmatter(v)
            agentes.append({"nome": k[7:-3], "description": _texto_fm(fm.get("description"), DESC_MAX) if fm else None})
    return {"modelo": d["modelo"][:64], "missao": d["missao"][:TEXTO_MAX], "sha256": sha, "agentes": agentes}


def _skills_publicas(base_squad: Path) -> list[dict]:
    out = []
    for nome in skills_de(base_squad):
        raw = ler_bytes(Path(base_squad) / "skills" / nome / "SKILL.md", CAND_MAX)
        try:
            fm = frontmatter(raw.decode("utf-8")) if raw is not None else None
        except UnicodeDecodeError:
            fm = None
        out.append({"nome": nome, "ficheiro": f"skills/{nome}/SKILL.md",
                    "description": _texto_fm(fm.get("description"), DESC_MAX) if fm else None})
    return out


def _instrucoes(base_squad: Path) -> bool:
    try:
        return _pasta(Path(base_squad)) and stat.S_ISREG(os.lstat(Path(base_squad) / "CLAUDE.md").st_mode)
    except OSError:
        return False


def _feito(dfd: int, stem: str) -> dict | None:
    # {"ok": True} | {"erro": e} | {} (resposta ilegível); None = sem resposta
    try:
        fd = os.open(stem + ".feito.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    except FileNotFoundError:
        return None
    except OSError:
        return {}
    try:
        with open(fd, "rb") as f:
            raw = f.read(FEITO_MAX + 1) if stat.S_ISREG(os.fstat(fd).st_mode) else b""
        d = json.loads(raw.decode("utf-8")) if len(raw) <= FEITO_MAX else None
    except (OSError, ValueError):
        return {}
    if isinstance(d, dict) and list(d) == ["ok"] and d["ok"] is True:
        return {"ok": True}
    if isinstance(d, dict) and list(d) == ["erro"] and d["erro"] in ERROS:
        return {"erro": d["erro"]}
    return {}


def ler_fila(fila: Path) -> list[tuple[str, dict, dict | None]]:
    """(stem, pedido, feito) dos pedidos válidos na raiz da fila, por nome (o mais antigo primeiro); feito None =
    pendente. Fila ausente ou ilegível → []."""
    try:
        dfd = os.open(fila, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return []
    try:
        try:
            nomes = sorted(n for n in os.listdir(dfd) if NOME_PEDIDO.fullmatch(n))
        except OSError:
            return []
        out = []
        for nome in nomes:
            stem = nome[:-5]
            try:
                fd = os.open(nome, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
            except OSError:
                continue
            try:
                with open(fd, "rb") as f:
                    raw = f.read(PEDIDO_MAX + 1) if stat.S_ISREG(os.fstat(fd).st_mode) else b""
                p = json.loads(raw.decode("utf-8")) if 0 < len(raw) <= PEDIDO_MAX else None
            except (OSError, ValueError):
                continue
            if validar_pedido(p, stem) is None:
                out.append((stem, p, _feito(dfd, stem)))
        return out
    finally:
        os.close(dfd)


def _sessao(cfg, s: dict | None) -> dict:
    if s is not None and s.get("modelo"):
        return {"modelo": s["modelo"], "fonte": "squad"}
    if cfg["runner"].get("modelo"):
        return {"modelo": cfg["runner"]["modelo"], "fonte": "runner"}
    return {"modelo": None, "fonte": "omissao"}


def _modelo_agentes(pasta: Path) -> dict:
    """{agente: model do frontmatter ou None} de um modelo de squad (CA-1)."""
    return {a["nome"]: a["model"] for a in (agente_publico(f, r) for f, r in agentes_de(pasta)) if "nome" in a}


def resposta(cfg, base: Path, rasc: Path, fila: Path, tz, now: float) -> dict:
    """Corpo do GET /api/agentes (§3.5): só frontmatter permitido, caminhos relativos e sha256; nunca corpos."""
    base, rasc, fila = Path(base), Path(rasc), Path(fila)
    registo, rotinas = ler_registo(base / "squads.json"), ler_rotinas(base / "rotinas.json")
    reg = {s["id"]: s for s in registo["squads"]}
    office = {s["id"]: s for s in cfg["squads"]}
    linhas = [(sid, "office") for sid in office] + [(sid, "registo") for sid in reg if sid not in office]
    if _pasta(rasc / "squads"):   # rascunhos de squads novas (ainda fora do office.yaml e do registo)
        conhecidas = set(office) | set(reg)
        linhas += [(e.name, "rascunho") for e in _entradas(rasc / "squads", 100)
                   if ID_SQUAD.fullmatch(e.name) and e.name not in conhecidas and _rascunho(rasc, e.name)]
    squads = []
    for sid, origem in linhas:
        cs, rs, rasc_sq = office.get(sid), reg.get(sid), _rascunho(rasc, sid)
        bs = base / sid
        lead = lead_de(cfg, registo, sid) or (rasc_sq[0]["lead"][:40] if rasc_sq else None)
        nome = cs["nome"] if cs else rs["nome"] if rs else rasc_sq[0]["nome"][:80]
        agentes = []
        for f, raw in agentes_de(bs) if _pasta(base) else []:
            a = agente_publico(f, raw)
            if not a.get("ilegivel"):
                a["lead"] = a["nome"] == lead
            agentes.append(a)
        squads.append({
            "id": sid, "nome": nome, "origem": origem, "formato": cs["formato"] if cs else None, "lead": lead,
            "sessao": _sessao(cfg, cs), "modelo_squad": rs["modelo"] if rs else None,
            "instrucoes": _pasta(base) and _instrucoes(bs), "agentes": agentes,
            "skills": _skills_publicas(bs) if _pasta(base) else [], "candidatos": _candidatos(rasc, sid),
            "rascunho": _rascunho_publico(rasc_sq),
            "ensaio_lead": _ensaio(rasc / "squads" / sid / "ensaio.json") if _pasta(rasc / "squads") else None,
            "rotinas": [{**{k: r[k] for k in ("id", "projecto", "hora", "dias", "instrucoes", "tecto_usd",
                                              "max_turnos", "estado", "falhas", "ultima")},
                         "motivo": r["motivo"][:80] if isinstance(r.get("motivo"), str) else None}
                        for r in rotinas["rotinas"] if r["squad"] == sid]})
    pedidos = []
    for stem, p, f in ler_fila(fila)[-PEDIDOS_VISTOS:]:
        estado = "pendente" if f is None else "ok" if f.get("ok") else "erro"
        pedidos.append({"id": stem, "accao": p["accao"], "alvo": alvo(p["accao"], p["args"]), "estado": estado,
                        "erro": (f or {}).get("erro"), "pedido_em": p["pedido_em"]})
    import conversa
    modelos = [{**m, "negocio": m["id"] in MODELOS_NEGOCIO, "modelo_agentes": _modelo_agentes(MODELOS_DIR / m["id"])}
               for m in modelos_squad(MODELOS_DIR)]
    return {"v": 1, "escrita": fila.is_dir() and os.access(fila, os.W_OK), "modelos": modelos,
            "projectos": [p["id"] for p in cfg["projectos"]], "squads": squads, "pedidos": pedidos,
            "conversas": conversa.lista(rasc)}


def candidato(rasc: Path, squad: str, nome: str) -> dict | None:
    """{"md", "sha256"} do candidato `<rasc>/agentes/<squad>/<nome>.md` (sem symlinks, ≤ CAND_MAX, UTF-8); None."""
    if not (isinstance(squad, str) and ID_SQUAD.fullmatch(squad) and isinstance(nome, str)
            and ID_AGENTE.fullmatch(nome)):
        return None
    pasta = Path(rasc) / "agentes"
    if not _pasta(pasta) or not _pasta(pasta / squad):
        return None
    raw = ler_bytes(pasta / squad / f"{nome}.md", CAND_MAX)
    try:
        return {"md": raw.decode("utf-8"), "sha256": _sha(raw)} if raw is not None else None
    except UnicodeDecodeError:
        return None


def _criar(fila: Path, nome: str, doc: dict) -> None:
    # tmp na mesma pasta + fsync + os.link (atómico e nunca sobrescreve: FileExistsError); OSError sobe
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=fila, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, Path(fila) / nome)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _semantica(cfg, base: Path, rasc: Path, accao: str, a: dict) -> str | None:
    """Verificações contra o disco e a config (o runner revalida tudo); None ou a mensagem do 400."""
    registo = ler_registo(base / "squads.json")
    office = {s["id"]: s for s in cfg["squads"]}
    reg = {s["id"] for s in registo["squads"]}
    sid = a["squad"]
    bs = base / sid
    conhecida = sid in office or sid in reg
    if "projecto" in a and a["projecto"] not in {p["id"] for p in cfg["projectos"]}:
        return "projecto inválido"
    if accao in ("modelo", "skills", "agente_remover"):
        if not conhecida:
            return "squad desconhecida"
        if f"agents/{a['agente']}.md" not in dict(agentes_de(bs)):
            return "agente inexistente"
        if accao == "skills" and any(s not in skills_de(bs) for s in a["skills"]):
            return "skill inexistente"
        if accao == "agente_remover" and a["agente"] == lead_de(cfg, registo, sid):
            return "o lead não pode ser removido"
    elif accao.startswith("agente_"):
        if not conhecida:
            return "squad desconhecida"
        cand = candidato(rasc, sid, a["nome"])
        if accao in ("agente_rascunho", "agente_criar") and f"agents/{a['nome']}.md" in dict(agentes_de(bs)):
            return "já existe"
        if accao == "agente_criar":
            if a["sha256"] is not None and cand is None:
                return "candidato inexistente"
            return validar_md(a["md"], a["nome"], set(skills_de(bs)))
        if accao not in ("agente_rascunho",) and cand is None:
            return "candidato inexistente"
    elif accao == "squad_rascunho":
        if a["modelo"] not in {m["id"] for m in modelos_squad(MODELOS_DIR)}:
            return "modelo de squad inexistente"
        if agentes_de(bs):
            return "squad já tem agentes"
    elif accao == "squad_criar":
        if _rascunho(rasc, sid) is None:
            return "rascunho inexistente"
    elif accao == "squad_descartar":   # o rascunho e/ou a conversa (ADR-020)
        if _rascunho(rasc, sid) is None and not os.path.lexists(rasc / "squads" / sid / "conversa.json"):
            return "rascunho inexistente"
    elif accao == "squad_conversa":
        import conversa
        if sid in reg:
            return "já existe"
        if agentes_de(bs):
            return "squad já tem agentes"
        conv = conversa.ler(rasc, sid)
        if conv is None and _rascunho(rasc, sid) is not None:
            return "já existe"
        if conv is not None and conv["estado"] != "a_perguntar":
            return "a conversa já terminou"
    elif accao in ("squad_ensaio", "squad_desactivar"):
        if sid not in reg:
            return "squad não registada"
    elif accao == "rotina_pedir":
        s = office.get(sid)
        if s is None or s["formato"] != "worktree" or "task" not in s["modos"]:
            return "squad não lançável"
        if sid in reg:   # criada por modelo: só com ensaio ok do lead actual
            lead = lead_de(cfg, registo, sid)
            raw = dict(agentes_de(bs)).get(f"agents/{lead}.md")
            e = _ensaio(rasc / "squads" / sid / "ensaio.json") if _pasta(rasc / "squads") else None
            if raw is None or e is None or e["estado"] != "ok" or e["sha256"] != _sha(raw):
                return "sem ensaio ok desta versão"
        vivas = [r for r in ler_rotinas(base / "rotinas.json")["rotinas"]
                 if r["squad"] == sid and r["estado"] not in ("cancelada", "recusada")]
        if len(vivas) >= ROTINAS_MAX:
            return "limite de rotinas"
    elif accao in ("rotina_retomar", "rotina_cancelar"):
        r = next((r for r in ler_rotinas(base / "rotinas.json")["rotinas"]
                  if r["squad"] == sid and r["id"] == a["rotina"]), None)
        if r is None or r["estado"] == "cancelada":
            return "rotina inexistente"
        if accao == "rotina_retomar" and r["estado"] != "pausada":
            return "rotina não pausada"
    return None


def _outra_conversa(rasc: Path, pendentes: list[dict], sid: str) -> bool:
    """CA-11a: uma conversa de cada vez — outra squad em `a_perguntar` ou com `squad_conversa` pendente na fila."""
    import conversa
    outras = set(conversa.em_curso(rasc)) | {p["args"]["squad"] for p in pendentes if p["accao"] == "squad_conversa"}
    return bool(outras - {sid})


def pedir(fila: Path, base: Path, cfg, data: dict, ip: str, ua: str, tz, now: float, rasc: Path | None = None
          ) -> tuple[int, dict]:
    """POST /api/agentes/pedido já com token/origem verificados: corpo {"token","accao","args"} → pedido v1 na fila.
    202 {"id","accao"}; 400 forma/semântica; 409 pendente para o mesmo alvo ou fila cheia; 503 fila sem escrita."""
    fila, base = Path(fila), Path(base)
    rasc = Path(rasc) if rasc is not None else base.parent / "agentes-rascunhos"
    if not isinstance(data, dict) or set(data) != {"token", "accao", "args"}:
        return 400, {"error": "campos inválidos"}
    accao, args = data["accao"], data["args"]
    erro = validar_args(accao, args)
    if erro:
        return 400, {"error": "campos inválidos" if erro == "pedido ilegível" else erro}
    args = {k: args[k] for k in ACCOES[accao]}
    erro = _semantica(cfg, base, rasc, accao, args)
    if erro:
        return 400, {"error": erro}
    indisponivel = 503, {"error": INDISPONIVEL}
    with _lock:
        if not fila.is_dir() or not os.access(fila, os.W_OK):
            return indisponivel
        pendentes = [p for _, p, f in ler_fila(fila) if f is None]
        meu = alvo(accao, args)
        if any(alvo(p["accao"], p["args"]) == meu for p in pendentes):
            return 409, {"error": "já há um pedido pendente para este alvo"}
        if accao == "squad_conversa" and _outra_conversa(rasc, pendentes, args["squad"]):
            return 409, {"error": "já há uma conversa em curso"}
        if len(pendentes) >= PENDENTES_MAX:
            return 409, {"error": "fila cheia"}
        dt = datetime.fromtimestamp(now, tz)
        for _ in range(3):
            stem = f"{dt.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
            doc = {"v": 1, "id": stem, "accao": accao, "args": args, "pedido_em": dt.isoformat(timespec="seconds"),
                   "ip": (ip or "")[:IP_MAX], "ua": (ua or "")[:UA_MAX]}
            try:
                _criar(fila, stem + ".json", doc)
                return 202, {"id": stem, "accao": accao}
            except FileExistsError:
                continue
            except OSError:
                return indisponivel
        return indisponivel
