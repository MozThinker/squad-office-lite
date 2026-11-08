"""Leitor de transcripts a pedido (/api/transcript/<id>): a única saída de conteúdo do Office.

Só stdlib. Cada campo é cortado, passa pela máscara de segredos e só depois é truncado e pesquisado;
resultados de ferramentas que tocam ficheiros com segredos (.env*, chaves…) ou imprimem o ambiente saem
«[ocultado]» inteiros; thinking nunca sai.
A resolução tarefa → ficheiros (só via meta.json) é do server.py.
"""
from __future__ import annotations

import json
import math
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

PAGE = 100
MAX_BYTES = 16 * 1024 * 1024   # por ficheiro; maior = só o fim (truncated)
LIMITS = {"pedido": 4000, "resposta": 4000, "ferramenta": 300, "resultado": 1000}
DELIVERY_MAX = 20000
SLACK = 4096                   # corta-se o bruto a limite + isto antes da máscara
SECRET = "[segredo]"
HIDDEN = "«[ocultado]»"
FILTERS = ("tudo", "mensagens", "sem-ferramentas")
CACHE_N = 4

# específicas antes da genérica (atribuições); um valor já [segredo] volta a dar [segredo].
# Sem regexes quadráticas: repetições depois de um prefixo fixo são limitadas ({0,200}) ou possessivas (++).
_V = r"[^\s'\"\\]+"   # valor de flag/cabeçalho: até espaço, aspas ou barra invertida
_ARG = r"(?:\"[^\"\n]{0,500}\"|'[^'\n]{0,500}'|[^\s'\"\\|]+)"   # argumento de echo/printf (o último é o valor)
_MASKS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"), SECRET),
    # DSN: password pode ter "/", mas não "porta/" (http://host:8080/x@y não é segredo); utilizador pode faltar
    (re.compile(r"(?i)(?<![A-Za-z0-9+.-])([a-z][a-z0-9+.-]{0,20}://[^\s:/@]{0,100}:)(?!\d+/)[^\s@]{1,200}(@)"),
     r"\1" + SECRET + r"\2"),
    # bases de dados com só utilizador/token antes do @ (redis://token@h, mongodb+srv://user@h): a userinfo toda
    (re.compile(r"(?i)(?<![A-Za-z0-9+.-])((?:postgres(?:ql)?|redis|rediss|mongodb(?:\+srv)?|mysql|mariadb|amqps?)://)"
                r"[^\s:/@]{1,200}(@)"), r"\1" + SECRET + r"\2"),
    (re.compile(r"(?i)\b((?:authorization|x-api-key)\\?[\"']?[ \t]*[:=][ \t]*\\?[\"']?"
                r"(?:(?:bearer|token|basic|bot|digest)[ \t]+)?)" + _V), r"\1" + SECRET),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1" + SECRET),
    (re.compile(r"(?i)\b((?:set-)?cookie\\?[\"']?[ \t]*:[ \t]*)[^\n]+"), r"\1" + SECRET),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), SECRET),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), SECRET),
    (re.compile(r"\b(?:sk_(?:live|test)_[A-Za-z0-9]{8,}|rk_(?:live_|test_)?[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9]{8,})"),
     SECRET),                                                                   # Stripe
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), SECRET),
    # AWS: a access key AKIA… e a secret key (40 car.) a seguir (CSV/linha «AKIA…,wJalr…») ou depois do nome
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b(?:([ \t]*[,;:|][ \t]*|[ \t]+)[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=]))?"),
     lambda m: SECRET + (m.group(1) + SECRET if m.group(1) is not None else "")),
    (re.compile(r"(?i)\b(aws_?secret_?access_?key\\?[\"']?[ \t]*[:=,]?[ \t]*\\?[\"']?)[A-Za-z0-9/+]{40}"
                r"(?![A-Za-z0-9/+=])"), r"\1" + SECRET),
    (re.compile(r"\bxox[bpasr]-[A-Za-z0-9-]{10,}"), SECRET),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), SECRET),
    (re.compile(r"(?<!\d)\d{6,12}:AA[A-Za-z0-9_-]{30,}"), SECRET),            # Telegram (também em /bot<token>/)
    (re.compile(r"\bEAA[A-Za-z0-9]{30,}"), SECRET),                            # Meta
    (re.compile(r"\b(?:npm_[A-Za-z0-9]{30,}|hf_[A-Za-z0-9]{30,})"), SECRET),
    # flags de linha de comando com credenciais
    (re.compile(r"(?<![\w-])((?:-u|--user)(?:[ \t]+|=)['\"]?[^\s:'\"]{1,100}:)" + _V), r"\1" + SECRET),
    (re.compile(r"(?<![\w-])(--(?:password|passwd|token|api-key|secret|client-secret)(?:[ \t]+|=)['\"]?)" + _V),
     r"\1" + SECRET),
    (re.compile(r"\b(sshpass[ \t]+-p[ \t]*['\"]?)" + _V), r"\1" + SECRET),
    (re.compile(r"\b(docker[ \t]+login\b[^\n]{0,200}?[ \t]-p[ \t]*['\"]?)" + _V), r"\1" + SECRET),
    (re.compile(r"\b(mysql(?:dump|admin)?\b[^\n]{0,200}?[ \t]-p['\"]?)" + _V), r"\1" + SECRET),
    (re.compile(r"\b(redis-cli\b[^\n]{0,200}?[ \t]-a[ \t]+['\"]?)" + _V), r"\1" + SECRET),
    (re.compile(r"\b(gh[ \t]+secret[ \t]+set\b[^\n]{0,200}?[ \t](?:--body|-b)(?:[ \t]+|=)['\"]?)" + _V),
     r"\1" + SECRET),
    # valor passado por stdin: echo V | gh secret set / docker login --password-stdin
    (re.compile(r"\b((?:echo|printf)[ \t]+(?:-[neE]+[ \t]+)?(?:" + _ARG + r"[ \t]+){0,2}?)" + _ARG +
                r"(?=[ \t]*\|[ \t]*(?:gh[ \t]+secret[ \t]+set\b|docker[ \t]+login\b[^\n|;&]{0,200}?--password-stdin))"),
     r"\1" + SECRET),
    # htpasswd com -b: a password é o último argumento (antes do fim, | ; & > ou ))
    (re.compile(r"\b(htpasswd(?=(?:[ \t]+-[A-Za-z]+){0,5}?[ \t]+-[A-Za-z]*b)(?:[ \t]+-[A-Za-z]+)*+[ \t]+"
                r"(?:[^\s-]\S{0,200}[ \t]+){1,2}?)['\"]?[^\s'\"]+(?=['\"]?[ \t]*(?:$|[|;&>)\n]))"), r"\1" + SECRET),
)
# atribuição NOME=valor / NOME: valor / "NOME": "valor" / \"NOME\": \"valor\" (JSON escapado); o nome é a palavra
# inteira (começa onde a palavra começa, possessivo) e tem de conter uma destas palavras como SEGMENTO do nome
# (separado por _ . - ou camelCase): DB_PASSWORD, apiKey, x-sk-live sim; skill, desk, KEYBOARD, max_tokens não
_SEG0 = r"(?:(?<![A-Za-z0-9])|(?<=[a-z0-9])(?=[A-Z]))"          # início de segmento
_SEG1 = r"(?:(?![A-Za-z])|(?<=[a-z])(?=[A-Z]))"                 # fim de segmento
_NAME_KW = (_SEG0 + r"(?i:token|secrets?|key|password|passwd|pass|pwd|sk|jwt|salt|private|credentials?|auth"
            r"|dsn|cookie|session|signing|service_role|apikey|senha)" + _SEG1)
_NAME = (r"\\?[\"']?(?=[A-Za-z0-9_.-]{0,120}?" + _NAME_KW + r")[A-Za-z0-9_.-]{1,120}+\\?[\"']?")
# valor: \"…\" (JSON escapado), "…" com escapes (pode continuar nas linhas seguintes até fechar, máx. 4000 car.),
# '…' ou até espaço/aspas; "bearer|basic|token|bot|digest " antes do valor fica e mascara-se a palavra seguinte
_ASSIGN = re.compile(r"(?<![A-Za-z0-9_.-])(" + _NAME + r"[ \t]*[:=][ \t]*"
                     r"(?:(?i:bearer|basic|token|bot|digest)[ \t]+)?)"
                     r"(?![|>][-+0-9]{0,2}[ \t]*(?:\n|$))"   # indicador de bloco YAML: o valor vem abaixo (_NEXT)
                     r"(\\\"[^\"\\\n]*\\\"|\"(?:[^\"\\]|\\.){0,4000}+\"?|'[^'\n]*'|[^\s\"'\\]+)")
# valor na(s) linha(s) seguinte(s): YAML «password:» / «key: |» com o valor indentado por baixo, ou ENV «KEY=\»
# com continuação; mascaram-se as linhas indentadas (até 50) ou a linha continuada
_NEXT = re.compile(r"(?m)^([ \t]*(?:-[ \t]+|export[ \t]+)?" + _NAME
                   + r"[ \t]*(?::[ \t]*(?:[|>][-+0-9]{0,2})?|=[ \t]*\\)[ \t]*\n)"
                   r"((?:[ \t]*\n)*[ \t]+)\S[^\n]*+((?:\n\2[^\n]*+){0,49})")
# tabela markdown cuja 1.ª célula é só um nome de segredo (`| SUPABASE_KEY | valor |`, também em `…` ou **…**):
# mascaram-se todas as células seguintes da linha, mas só se a 1.ª célula com valor tem aspecto de segredo (≥ 16
# car. sem espaços, ou sem espaços depois de um nome de password). Nomes de ficheiro (`x.sql`) e `…_id`/`…_status`
# nunca: `| Token | Uso |`, `| session_id | uuid |`, `| AuthExpired | … |` ficam
_TABLE = re.compile(r"(?m)^([ \t]*\|[ \t]*[`*]{0,3}((?=[A-Za-z0-9_.-]{0,120}?" + _NAME_KW
                    + r")[A-Za-z0-9_.-]{1,120}+)[`*]{0,3}[ \t]*\|)([^\n]*+)")
_TABLE_NAO = re.compile(r"(?i)\.[a-z0-9]{1,5}$|_(?:id|status)$")
_TABLE_PASS = re.compile(_SEG0 + r"(?i:password|passwd|pass|pwd|senha)" + _SEG1)
# ficheiros com segredos (nome do ficheiro) e comandos que imprimem o ambiente ou credenciais
_FILES = (r"\.env[\w.*-]*|[\w*-][\w.*-]*\.env|\.dev\.vars|\.netrc|id_(?:rsa|ed25519|ecdsa)"
          r"|[\w*-][\w.*-]*\.(?:pem|key|p12|pfx)|hosts\.yml|credentials(?:\.json)?"
          r"|\.pgpass|\.git-credentials|\.npmrc|\.credentials\.json")
_FILE_NAME = re.compile(_FILES)
_PATHS = re.compile(r"/proc/[^/\s]*/environ|\.docker/config\.json")   # caminhos (o nome sozinho não chega)
_FILE_REF = re.compile(r"(?:^|[\s/'\"=<>(:*])(?:" + _FILES + r")(?![\w.-])")
_C = r"(?:^|[;&|(\n`]|\$\()[ \t]*(?:sudo[ \t]+)?"   # posição de comando
_E = r"[ \t]*(?=$|[;&|)\n`])"                           # sem argumentos
_ENV_CMD = re.compile(
    _C + r"(?:env(?:[ \t]+(?:-u[ \t]*[^\s;&|)`]+|--unset=\S+|-[\w-]+))*+|set|export(?:[ \t]+-p)?"
    r"|declare[ \t]+-[A-Za-z]*x[A-Za-z]*)" + _E
    + r"|" + _C + r"(?:printenv\b|declare[ \t]+-[A-Za-z]*p|compgen[ \t]+-[A-Za-z]*e)"
    r"|\bkubectl\b[^\n;&|]{0,200}?[ \t]get[ \t]+secrets?\b|\bsupabase[ \t]+status\b"
    r"|\bdocker[ \t]+(?:[a-z]+[ \t]+)?inspect\b"
    r"|\bdocker(?:[ \t]+|-)compose\b[^\n;&|]{0,200}?[ \t]config\b"
    r"|\bdocker[ \t]+(?:container[ \t]+)?exec\b[^\n;&|]{0,200}?[\s'\"](?:env|printenv)\b"
    r"|\bgh[ \t]+auth[ \t]+(?:token\b|status\b[^\n;&|]{0,100}?[ \t](?:-t|--show-token)\b)")
# o que resume cada ferramenta (o resto do input nunca sai)
_SUMMARY = {"Bash": ("command",), "Read": ("file_path",), "Edit": ("file_path",), "Write": ("file_path",),
            "NotebookEdit": ("notebook_path", "file_path"), "Grep": ("pattern", "path"), "Glob": ("pattern", "path"),
            "WebSearch": ("query",), "WebFetch": ("url",), "Agent": ("description",), "Task": ("description",)}

_cache: OrderedDict = OrderedDict()   # caminho → ((mtime_ns, tamanho), (entradas, truncated))
_lock = threading.Lock()


def _assign(m: re.Match) -> str:
    v = m.group(2)
    q = "\\\"" if v.startswith("\\\"") else (v[0] if v[0] in "\"'" else "")
    return m.group(1) + q + SECRET + q


def _next(m: re.Match) -> str:
    rest = re.sub(r"(?m)(\n[ \t]*)\S[^\n]*", r"\1" + SECRET, m.group(3))
    return m.group(1) + m.group(2) + SECRET + rest


def _table(m: re.Match) -> str:
    nome, celulas = m.group(2), m.group(3).split("|")
    valor = next((c.strip().strip("`*'\"") for c in celulas if c.strip()), "")
    if (_TABLE_NAO.search(nome) or not valor or re.search(r"\s", valor)
            or (len(valor) < 16 and SECRET not in valor and not _TABLE_PASS.search(nome))):
        return m.group(0)
    return m.group(1) + "|".join(f" {SECRET} " if c.strip() else c for c in celulas)


def mask(s: str) -> str:
    for rx, rep in _MASKS:
        s = rx.sub(rep, s)
    s = _TABLE.sub(_table, s)
    return _ASSIGN.sub(_assign, _NEXT.sub(_next, s))


def clip(s: str, n: int) -> str:
    # cortar → mascarar → truncar (um segredo a meio do limite fica mascarado inteiro). Se o corte apanha uma
    # palavra a meio (≤ SLACK car.: um token cortado deixaria de ser reconhecido), ela sai antes da máscara
    cut = len(s) > n + SLACK
    if cut:
        s = s[:n + SLACK]
        j = max(s.rfind(" "), s.rfind("\n"), s.rfind("\t"))
        if j >= n:
            s = s[:j]
    s = mask(s)
    return s if len(s) <= n and not cut else s[:n] + "…"


def delivery(text) -> str | None:
    return clip(text, DELIVERY_MAX) if isinstance(text, str) and text.strip() else None


def env_file(name, inp) -> bool:
    # ferramenta que toca ficheiros com segredos (.env*, chaves, credenciais, /proc/*/environ) ou um Bash que
    # imprime o ambiente/credenciais: o resultado sai inteiro como «[ocultado]»
    if not isinstance(inp, dict):
        return False
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        return any(isinstance(p := inp.get(k), str) and (_FILE_NAME.fullmatch(p.rsplit("/", 1)[-1]) or _PATHS.search(p))
                   for k in ("file_path", "notebook_path"))
    if name in ("Bash", "Grep", "Glob"):
        if name == "Bash" and isinstance(c := inp.get("command"), str) and _ENV_CMD.search(c):
            return True
        return any(isinstance(v := inp.get(k), str) and (_FILE_REF.search(v) or _PATHS.search(v))
                   for k in ("command", "path", "pattern", "glob"))
    return False


def _tool(name) -> str:
    return name if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name) else "outra"


def _summary(name, inp) -> str:
    if not isinstance(inp, dict):
        return ""
    return " ".join(v for k in _SUMMARY.get(name, ()) if isinstance(v := inp.get(k), str))


def _text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b["text"] for b in c if isinstance(b, dict) and b.get("type") == "text"
                         and isinstance(b.get("text"), str))
    return ""


def _at(ts) -> str | None:
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (ValueError, OverflowError):   # datas extremas (ano 1 com fuso)
        return None


def _entry(out: list, at, kind: str, raw: str, **extra) -> None:
    out.append({"at": at, "kind": kind, "text": clip(raw, LIMITS[kind]), **extra})


def _line(d: dict, out: list, tools: dict, hidden: set, truncated: bool = False) -> None:
    role, msg = d.get("type"), d.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    at = _at(d.get("timestamp"))
    if isinstance(content, str):
        _entry(out, at, "pedido" if role == "user" else "resposta", content)
        return
    if not isinstance(content, list):
        return
    for b in content:
        bt = b.get("type") if isinstance(b, dict) else None
        if bt == "text" and isinstance(b.get("text"), str):
            _entry(out, at, "pedido" if role == "user" else "resposta", b["text"])
        elif bt == "tool_use" and role == "assistant":
            name, inp = b.get("name"), b.get("input")
            if isinstance(b.get("id"), str):
                tools[b["id"]] = _tool(name)
                if env_file(name, inp):
                    hidden.add(b["id"])
            _entry(out, at, "ferramenta", _summary(name, inp), tool=_tool(name))
        elif bt == "tool_result" and role == "user":
            rid = b.get("tool_use_id")
            entry = {"tool": tools.get(rid) if isinstance(rid, str) else None, "error": b.get("is_error") is True}
            # num transcript truncado, um resultado sem o tool_use visto pode ser de um .env: oculto
            if (isinstance(rid, str) and rid in hidden) or (truncated and rid not in tools):
                out.append({"at": at, "kind": "resultado", "text": HIDDEN, **entry})
            else:
                _entry(out, at, "resultado", _text(b.get("content")), **entry)


def _parse_file(path: Path, size: int) -> tuple[list, bool]:
    with path.open("rb") as f:
        truncated = size > MAX_BYTES
        if truncated:
            f.seek(size - MAX_BYTES)
            f.readline()   # linha cortada
        data = f.read(MAX_BYTES)
    out, tools, hidden = [], {}, set()
    for raw in data.splitlines():
        if b'"user"' not in raw and b'"assistant"' not in raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") in ("user", "assistant"):
            _line(d, out, tools, hidden, truncated)
    return out, truncated


def _parsed(path: Path) -> tuple[list, bool]:
    # LRU de CACHE_N ficheiros por (mtime, tamanho), com as entradas já mascaradas e truncadas
    st = path.stat()
    key, sig = str(path), (st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == sig:
            _cache.move_to_end(key)
            return hit[1]
    res = _parse_file(path, st.st_size)
    with _lock:
        _cache[key] = (sig, res)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_N:
            _cache.popitem(last=False)
    return res


def parse(paths: list[Path]) -> tuple[list, bool]:
    # várias sessões (dev com retomas): juntar e ordenar por hora (estável)
    entries, truncated = [], False
    for p in paths:
        try:
            e, t = _parsed(p)
        except OSError:
            continue
        entries += e
        truncated = truncated or t
    if len(paths) > 1:
        entries.sort(key=lambda e: e["at"] or "")
    return entries, truncated


def page(entries: list, f: str = "tudo", q: str = "", n: int = 0) -> dict:
    # filtros e pesquisa (no texto já mascarado) antes de paginar
    if f == "mensagens":
        entries = [e for e in entries if e["kind"] in ("pedido", "resposta")]
    elif f == "sem-ferramentas":
        entries = [e for e in entries if e["kind"] not in ("ferramenta", "resultado")]
    q = (q or "")[:100].casefold()
    if q:
        entries = [e for e in entries if q in e["text"].casefold()]
    total = len(entries)
    pages = max(1, math.ceil(total / PAGE))
    return {"entries": entries[n * PAGE:(n + 1) * PAGE] if 0 <= n < pages else [], "page": n, "pages": pages,
            "total": total}
