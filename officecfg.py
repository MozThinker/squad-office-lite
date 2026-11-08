"""Config do Squad Office Lite (`office.yaml`): leitura, validação e layout de pastas.

Stdlib só, Python >= 3.10. Importado pelo servidor (contentor) e pelo runner (host).
O YAML aceite é um subconjunto estrito (ver README, «Referência · office.yaml»).

CLI:  python3 officecfg.py validar FICH        -> exit 0 / 1 + "erro: ..." em stderr
      python3 officecfg.py pastas FICH         -> linhas "0750 /abs/caminho"
      python3 officecfg.py raiz FICH NOME      -> caminho absoluto
"""
import os
import re
import sys
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

EXEMPLO = Path(__file__).resolve().parent / "office.example.yaml"

SUB = {"tasks": "tasks", "inbox": "tasks/inbox", "quadro": "quadro", "quadro_lancar": "quadro-lancar",
       "decisoes": "decisoes", "decisoes_fila": "decisoes-fila", "push": "push", "biblioteca": "biblioteca",
       "squads": "squads", "runner": "runner"}
MOUNTS = [  # (origem, destino no contentor, modo, variável de ambiente do servidor)
    ("tasks", "/data/tasks", "ro", "TASKS_DIR"), ("claude_projects", "/data/claude", "ro", "CLAUDE_PROJECTS_DIR"),
    ("squads", "/data/squads", "ro", "SQUADS_DIR"), ("decisoes", "/data/aprovacoes", "ro", "APROVACOES_DIR"),
    ("biblioteca", "/data/biblioteca", "ro", "BIBLIOTECA_DIR"),
    ("decisoes_fila", "/data/decisoes-fila", "rw", "DECISOES_FILA_DIR"),
    ("quadro", "/data/quadro", "rw", "QUADRO_DIR"), ("quadro_lancar", "/data/quadro-lancar", "rw", "QUADRO_LANCAR_DIR"),
    ("push", "/data/push", "rw", "PUSH_DIR")]
RAIZES = ("dados", "projectos", "claude_projects")
COLUNAS = {"ideias": "Ideias", "a-fazer": "A fazer", "em-curso": "Em curso", "a-espera": None, "feito": "Feito"}


class ConfigErro(ValueError):
    """Erro de config; a mensagem nunca leva o caminho do ficheiro."""


# ---------------------------------------------------------------- YAML (subconjunto)

_CHAVE = re.compile(r"([a-z][a-z0-9_-]*):(?:\s+(.*))?")
_INT = re.compile(r"-?\d+")
_PROIBIDO_INICIO = "[]{}&*!|>%@`"


def _sem_comentario(s):
    if s.startswith("#"):
        return ""
    i = s.find(" #")
    return (s[:i] if i >= 0 else s).strip()


def _resto(s, n):
    """Depois de um escalar entre aspas ou lista em linha só pode vir espaço ou ` #…`."""
    r = s.strip()
    if r and not (r.startswith("#") and s[:1] == " "):
        raise ConfigErro(f"linha {n}: texto a mais depois do valor")


def _aspas(s, n):
    """s começa por aspas; devolve (texto, resto)."""
    q, out, i = s[0], [], 1
    while i < len(s):
        c = s[i]
        if q == "'":
            if c == "'":
                if s[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), s[i + 1:]
        else:
            if c == "\\":
                e = s[i + 1:i + 2]
                if e not in ('"', "\\", "n"):
                    raise ConfigErro(f"linha {n}: não suportado: escape \\{e}")
                out.append("\n" if e == "n" else e)
                i += 2
                continue
            if c == '"':
                return "".join(out), s[i + 1:]
        out.append(c)
        i += 1
    raise ConfigErro(f"linha {n}: aspas por fechar")


def _simples(s, n):
    if not s:
        raise ConfigErro(f"linha {n}: valor vazio")
    if s[0] in _PROIBIDO_INICIO:
        raise ConfigErro(f"linha {n}: não suportado: valor a começar por {s[0]}")
    if ": " in s or s.endswith(":"):
        raise ConfigErro(f"linha {n}: não suportado: «: » num valor sem aspas")
    if s in ("~", "null", "Null", "NULL"):
        raise ConfigErro(f"linha {n}: não suportado: null")
    if s in ("true", "false"):
        return s == "true"
    if _INT.fullmatch(s):
        return int(s)
    return s


def _escalar(s, n, em_lista=False):
    if s[:1] in ('"', "'"):
        v, resto = _aspas(s, n)
        _resto(resto, n)
        return v
    if s[:1] == "[" and not em_lista:
        fim = s.find("]")
        if fim < 0:
            raise ConfigErro(f"linha {n}: lista em linha por fechar")
        _resto(s[fim + 1:], n)
        dentro = s[1:fim].strip()
        if not dentro:
            return []
        return [_escalar(x.strip(), n, em_lista=True) for x in dentro.split(",")]
    v = s if em_lista else _sem_comentario(s)
    return _simples(v, n)


def ler_yaml(texto):
    """Subconjunto de YAML -> dict. ConfigErro("linha N: …") no que não for suportado."""
    linhas = []  # [nº linha, indentação, conteúdo]
    for n, bruta in enumerate(texto.split("\n"), 1):
        if n == 1 and bruta.startswith("﻿"):
            bruta = bruta[1:]
        if "\t" in bruta:
            raise ConfigErro(f"linha {n}: tabulação não permitida (usa espaços)")
        lin = bruta.rstrip(" \r")
        conteudo = lin.lstrip(" ")
        if not conteudo or conteudo.startswith("#"):
            continue
        ind = len(lin) - len(conteudo)
        if ind % 2:
            raise ConfigErro(f"linha {n}: indentação tem de ser múltiplo de 2 espaços")
        if conteudo.rstrip() == "---":
            if linhas or ind:
                raise ConfigErro(f"linha {n}: não suportado: ---")
            linhas.append(None)  # marcador do início do documento
            continue
        linhas.append([n, ind, conteudo])
    linhas = [x for x in linhas if x is not None]
    if not linhas:
        return {}
    if linhas[0][1] != 0:
        raise ConfigErro(f"linha {linhas[0][0]}: indentação inesperada")
    valor, i = _bloco(linhas, 0, 0)
    if i < len(linhas):
        raise ConfigErro(f"linha {linhas[i][0]}: indentação inesperada")
    if not isinstance(valor, dict):
        raise ConfigErro(f"linha {linhas[0][0]}: o ficheiro tem de ser um mapeamento (chave: valor)")
    return valor


def _item(conteudo):
    return conteudo == "-" or conteudo.startswith("- ")


def _bloco(linhas, i, ind):
    if _item(linhas[i][2]):
        return _lista(linhas, i, ind)
    return _mapa(linhas, i, ind)


def _mapa(linhas, i, ind):
    d = {}
    while i < len(linhas):
        n, li, conteudo = linhas[i]
        if li < ind:
            break
        if li > ind:
            raise ConfigErro(f"linha {n}: indentação inesperada")
        if _item(conteudo):
            raise ConfigErro(f"linha {n}: item de lista onde se esperava «chave: valor»")
        m = _CHAVE.fullmatch(conteudo)
        if not m:
            raise ConfigErro(f"linha {n}: não suportado")
        chave, resto = m.group(1), (m.group(2) or "")
        if chave in d:
            raise ConfigErro(f"linha {n}: chave duplicada: {chave}")
        i += 1
        if _sem_comentario(resto) == "":
            if i >= len(linhas) or linhas[i][1] <= ind:
                raise ConfigErro(f"linha {n}: não suportado: valor vazio em {chave}")
            if linhas[i][1] != ind + 2:
                raise ConfigErro(f"linha {linhas[i][0]}: indentação inesperada")
            d[chave], i = _bloco(linhas, i, ind + 2)
        else:
            d[chave] = _escalar(resto, n)
    return d, i


def _lista(linhas, i, ind):
    out = []
    while i < len(linhas):
        n, li, conteudo = linhas[i]
        if li < ind or (li == ind and not _item(conteudo)):
            break
        if li > ind:
            raise ConfigErro(f"linha {n}: indentação inesperada")
        resto = conteudo[2:].lstrip(" ") if conteudo != "-" else ""
        if not resto or resto.startswith("#"):
            raise ConfigErro(f"linha {n}: não suportado: item de lista vazio")
        if _CHAVE.fullmatch(resto):
            linhas[i] = [n, ind + 2, resto]  # mapeamento na lista: 1.ª chave alinhada a +2
            v, i = _mapa(linhas, i, ind + 2)
        else:
            v, i = _escalar(resto, n), i + 1
            if i < len(linhas) and linhas[i][1] > ind:
                raise ConfigErro(f"linha {linhas[i][0]}: indentação inesperada")
        out.append(v)
    return out, i


# ---------------------------------------------------------------- validação (esquema)

_ID_SQUAD = re.compile(r"[a-z][a-z0-9-]{1,19}")
_ID_LONGO = re.compile(r"[a-z][a-z0-9-]{1,23}")
_LEAD = re.compile(r"[a-z][a-z0-9-]{1,39}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
_REPO = re.compile(r"[A-Za-z0-9._-]+")
_FUSO = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*")
_PERMISSION = ("dontAsk", "default", "acceptEdits", "auto", "bypassPermissions")
# `--allowedTools`: Nome ou Nome(regra); sem espaços nem vírgulas fora dos parênteses (o claude separa por eles) e
# nunca a começar por - (a opção é variádica: seria lido como outra opção)
_FERRAMENTA = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}(?:\([^()]{1,200}\))?")
FERRAMENTAS = ["Read", "Edit", "Write", "Glob", "Grep", "Agent", "TodoWrite",
               "Bash(git *)", "Bash(gh pr create *)", "Bash(gh pr view *)", "Bash(gh pr checks *)",
               "Bash(gh pr edit *)", "Bash(gh pr list *)", "Bash(gh pr ready *)", "Bash(gh run *)"] + [
    f"Bash({c} *)" for c in ("python", "python3", "pytest", "uv", "pip", "npm", "npx", "node", "pnpm", "yarn",
                             "make", "ruff", "ls", "wc", "grep", "diff", "mkdir", "touch", "pwd", "which",
                             "sort")]
# runner.max_usd (texto: «5», «2.5») e runner.modelo (--model): padrões fechados, vão para o argv do claude
_MAX_USD = re.compile(r"(?:0|[1-9]\d{0,3})(?:\.\d{1,2})?")
_MODELO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
CLAUDE_ENV = "claude.env"   # credenciais do claude, ao lado do office.yaml; nunca montado no contentor
_TIPOS_BIB = ("relatorios", "estudos", "planos", "conhecimento", "textos")


def _c(campo, k):
    return f"{campo}.{k}" if campo else k


def _mapa_de(d, campo, permitidas, obrigatorias=()):
    if not isinstance(d, dict):
        raise ConfigErro(f"{campo or 'config'}: tem de ser um mapeamento (chave: valor)")
    for k in d:
        if k not in permitidas:
            raise ConfigErro(f"{_c(campo, k)}: chave desconhecida")
    for k in obrigatorias:
        if k not in d:
            raise ConfigErro(f"{_c(campo, k)}: obrigatório")
    return d


def _texto(v, campo, mn, mx):
    if not isinstance(v, str):
        raise ConfigErro(f"{campo}: tem de ser texto (usa aspas)")
    if not mn <= len(v) <= mx:
        raise ConfigErro(f"{campo}: tem de ter entre {mn} e {mx} caracteres")
    if any(unicodedata.category(ch) == "Cc" for ch in v):
        raise ConfigErro(f"{campo}: caracteres de controlo não permitidos")
    return v


def _padrao(v, campo, rx, descr):
    if not isinstance(v, str) or not rx.fullmatch(v):
        raise ConfigErro(f"{campo}: inválido ({descr})")
    return v


def _inteiro(v, campo, mn, mx):
    if type(v) is not int or not mn <= v <= mx:
        raise ConfigErro(f"{campo}: tem de ser um inteiro entre {mn} e {mx}")
    return v


def _lista_de(v, campo, mn, mx):
    if not isinstance(v, list):
        raise ConfigErro(f"{campo}: tem de ser uma lista")
    if not mn <= len(v) <= mx:
        raise ConfigErro(f"{campo}: tem de ter entre {mn} e {mx} elementos")
    return v


def _unico(ids, campo):
    vistos = set()
    for i, x in enumerate(ids):
        if x in vistos:
            raise ConfigErro(f"{campo}[{i}].id: repetido ({x})")
        vistos.add(x)


def _squads(v):
    out = []
    for i, s in enumerate(_lista_de(v, "squads", 1, 6)):
        c = f"squads[{i}]"
        _mapa_de(s, c, ("id", "nome", "lead", "personagem", "formato", "tipos", "modos"), ("id", "nome"))
        formato = s.get("formato", "worktree")
        if formato not in ("worktree", "sessao"):
            raise ConfigErro(f"{c}.formato: tem de ser worktree ou sessao")
        personagem = s.get("personagem")
        n = {"id": _padrao(s["id"], f"{c}.id", _ID_SQUAD, "minúsculas, dígitos e -, 2 a 20"),
             "nome": _texto(s["nome"], f"{c}.nome", 1, 24),
             "lead": _padrao(s.get("lead", "tech-lead"), f"{c}.lead", _LEAD, "minúsculas, dígitos e -, 2 a 40"),
             "personagem": None if personagem is None else _texto(personagem, f"{c}.personagem", 1, 24),
             "formato": formato, "tipos": [], "modos": []}
        if formato == "sessao":
            for k in ("tipos", "modos"):
                if k in s:
                    raise ConfigErro(f"{c}.{k}: só existe em squads formato worktree")
        else:
            tipos = _lista_de(s.get("tipos", ["feat", "fix"]), f"{c}.tipos", 1, 6)
            for j, t in enumerate(tipos):
                _padrao(t, f"{c}.tipos[{j}]", _ID_SQUAD, "minúsculas, dígitos e -, 2 a 20")
            modos = _lista_de(s.get("modos", ["goal", "task"]), f"{c}.modos", 1, 2)
            for j, m in enumerate(modos):
                if m not in ("goal", "task"):
                    raise ConfigErro(f"{c}.modos[{j}]: tem de ser goal ou task")
            if len(set(tipos)) != len(tipos) or len(set(modos)) != len(modos):
                raise ConfigErro(f"{c}: tipos/modos repetidos")
            n["tipos"], n["modos"] = list(tipos), list(modos)
        out.append(n)
    _unico([s["id"] for s in out], "squads")
    return out


def _projectos(v):
    out = []
    for i, p in enumerate(_lista_de(v, "projectos", 1, 30)):
        c = f"projectos[{i}]"
        _mapa_de(p, c, ("id", "nome", "glifo", "repo"), ("id", "nome"))
        pid = _padrao(p["id"], f"{c}.id", _ID_LONGO, "minúsculas, dígitos e -, 2 a 24")
        if pid == "outro":
            raise ConfigErro(f"{c}.id: «outro» é reservado")
        repo = p.get("repo", pid)
        if not isinstance(repo, str) or not _REPO.fullmatch(repo) or repo in (".", ".."):
            raise ConfigErro(f"{c}.repo: inválido (nome de pasta: letras, dígitos, . _ -)")
        out.append({"id": pid, "nome": _texto(p["nome"], f"{c}.nome", 1, 40),
                    "glifo": _texto(p.get("glifo", "◆"), f"{c}.glifo", 1, 2), "repo": repo})
    _unico([p["id"] for p in out], "projectos")
    return out


def _biblioteca(v):
    out = []
    for i, f in enumerate(_lista_de(v, "biblioteca", 0, 12)):
        c = f"biblioteca[{i}]"
        _mapa_de(f, c, ("id", "rotulo", "pasta", "media", "tipo"), ("id", "rotulo", "pasta"))
        fid = _padrao(f["id"], f"{c}.id", _ID_LONGO, "minúsculas, dígitos e -, 2 a 24")
        if fid == "tarefas" or fid.startswith("aprovacoes"):
            raise ConfigErro(f"{c}.id: reservado")
        pasta = f["pasta"]
        segs = pasta.split("/") if isinstance(pasta, str) else [""]
        if any(s in ("", ".", "..") or not _REPO.fullmatch(s) for s in segs):
            raise ConfigErro(f"{c}.pasta: inválida (relativa a dados/biblioteca, sem ..)")
        media = f.get("media", False)
        if not isinstance(media, bool):
            raise ConfigErro(f"{c}.media: tem de ser true ou false")
        tipo = f.get("tipo")
        if tipo is not None and tipo not in _TIPOS_BIB:
            raise ConfigErro(f"{c}.tipo: tem de ser um de {', '.join(_TIPOS_BIB)}")
        out.append({"id": fid, "rotulo": _texto(f["rotulo"], f"{c}.rotulo", 1, 30), "pasta": pasta,
                    "media": media, "tipo": tipo})
    _unico([f["id"] for f in out], "biblioteca")
    return out


def _raizes(v):
    omissao = {"dados": "~/office-lite/dados", "projectos": "~/projectos", "claude_projects": "~/.claude/projects"}
    _mapa_de(v, "raizes", RAIZES)
    out = {}
    for k in RAIZES:
        r = v.get(k, omissao[k])
        if not isinstance(r, str) or not r.startswith(("/", "~/")) or ".." in r.split("/"):
            raise ConfigErro(f"raizes.{k}: tem de começar por / ou ~/ e não ter ..")
        out[k] = r
    return out


def _runner(v):
    _mapa_de(v, "runner", ("tecto_diario", "paralelo", "rotacao_dias", "permission_mode", "settings", "ferramentas",
                           "max_usd", "modelo"))
    pm = v.get("permission_mode", "dontAsk")
    if pm not in _PERMISSION:
        raise ConfigErro(f"runner.permission_mode: tem de ser um de {', '.join(_PERMISSION)}")
    settings = v.get("settings", "")
    if not isinstance(settings, str) or (settings and not settings.startswith("/")):
        raise ConfigErro("runner.settings: caminho absoluto ou \"\"")
    ferramentas = _lista_de(v.get("ferramentas", FERRAMENTAS), "runner.ferramentas", 0, 100)
    for i, f in enumerate(ferramentas):
        c = f"runner.ferramentas[{i}]"
        _texto(f, c, 1, 270)
        if not _FERRAMENTA.fullmatch(f):
            raise ConfigErro(f"{c}: inválida (Nome ou Nome(regra), ex.: Read, Bash(npm test *))")
    modelo = v.get("modelo")
    if modelo is not None:
        _padrao(modelo, "runner.modelo", _MODELO, "letras, dígitos, . _ -, até 64")
    return {"tecto_diario": _inteiro(v.get("tecto_diario", 10), "runner.tecto_diario", 1, 100),
            "paralelo": _inteiro(v.get("paralelo", 1), "runner.paralelo", 1, 4),
            "rotacao_dias": _inteiro(v.get("rotacao_dias", 14), "runner.rotacao_dias", 1, 365),
            "permission_mode": pm, "settings": settings, "ferramentas": list(ferramentas),
            "max_usd": _max_usd(v.get("max_usd")), "modelo": modelo}


def _max_usd(v):
    # inteiro (não bool) ou texto com até 2 casas, > 0 e <= 1000; guardado como texto para o argv («5», «2.5»)
    if v is None:
        return None
    if type(v) is int:
        v = str(v)
    if not isinstance(v, str) or not _MAX_USD.fullmatch(v) or not 0 < float(v) <= 1000:
        raise ConfigErro("runner.max_usd: tem de ser um número > 0 e ≤ 1000, até 2 casas decimais")
    return v


def validar(d):
    """Esquema §3.3 -> config normalizada (omissões preenchidas). ConfigErro com o campo."""
    _mapa_de(d, "", ("versao", "marca", "utilizador", "fuso", "raizes", "squads", "projectos", "colunas",
                     "biblioteca", "runner"), ("squads", "projectos"))
    if d.get("versao", 1) != 1 or type(d.get("versao", 1)) is not int:
        raise ConfigErro("versao: só a versão 1 é suportada")
    u = _mapa_de(d.get("utilizador", {}), "utilizador", ("nome", "contacto"))
    nome = _texto(u.get("nome", "Utilizador"), "utilizador.nome", 1, 40)
    contacto = u.get("contacto", "")
    if not isinstance(contacto, str) or (contacto and not _EMAIL.fullmatch(contacto)):
        raise ConfigErro("utilizador.contacto: tem de ser um e-mail ou \"\"")
    fuso = d.get("fuso", "UTC")
    try:
        ZoneInfo(fuso)
    except (TypeError, ValueError, OSError, LookupError):
        # sem base de fusos no sistema (imagem sem tzdata) aceita-se o nome pela forma: o servidor usa UTC
        if available_timezones() or not isinstance(fuso, str) or not _FUSO.fullmatch(fuso):
            raise ConfigErro("fuso: fuso horário IANA desconhecido (ex.: Europe/Lisbon)") from None
    col = _mapa_de(d.get("colunas", {}), "colunas", tuple(COLUNAS))
    colunas = {}
    for k, omissao in COLUNAS.items():
        colunas[k] = _texto(col[k], f"colunas.{k}", 1, 30) if k in col else (omissao or f"À espera de {nome}")
    return {"versao": 1, "marca": _texto(d.get("marca", "Squad Office"), "marca", 1, 40),
            "utilizador": {"nome": nome, "contacto": contacto}, "fuso": fuso,
            "raizes": _raizes(d.get("raizes", {})), "squads": _squads(d["squads"]),
            "projectos": _projectos(d["projectos"]), "colunas": colunas,
            "biblioteca": _biblioteca(d.get("biblioteca", [])), "runner": _runner(d.get("runner", {}))}


# ---------------------------------------------------------------- carregar, layout, API pública

def exemplo():
    return validar(ler_yaml(EXEMPLO.read_text(encoding="utf-8")))


def _ler(caminho):
    try:
        return Path(caminho).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigErro("config ausente") from None
    except (OSError, UnicodeDecodeError):
        raise ConfigErro("config ilegível") from None


def raiz_dados(caminho):
    """Só `raizes.dados` (com a omissão), validado como no modo estrito; o resto do ficheiro não é validado. Para o
    `office_runner.py parar` com o office.yaml inválido. ConfigErro se o YAML não se ler ou o campo for inválido."""
    d = ler_yaml(_ler(caminho))
    r = d.get("raizes", {}) if isinstance(d, dict) else None
    if not isinstance(r, dict):
        raise ConfigErro("raizes: tem de ser um mapeamento (chave: valor)")
    return _raizes({"dados": r["dados"]} if "dados" in r else {})["dados"]


def carregar(caminho, estrito=False):
    """(cfg, avisos). Não estrito: nunca levanta, cai no exemplo com aviso. Estrito: ConfigErro."""
    try:
        d = ler_yaml(_ler(caminho))
        if not d:
            raise ConfigErro("config vazia")
        return validar(d), []
    except ConfigErro as e:
        if estrito:
            raise
        return exemplo(), [str(e)]


def raiz(cfg, nome):
    if nome not in RAIZES:
        raise ConfigErro(f"raiz desconhecida: {nome} (usa {', '.join(RAIZES)})")
    return Path(os.path.expanduser(cfg["raizes"][nome])).resolve()


def caminho(cfg, nome):
    return raiz(cfg, "dados") / SUB[nome]


def pastas(cfg):
    """(modo octal, caminho) a criar no host, sem repetidos, por esta ordem."""
    out = [("0700" if k == "push" else "0750", caminho(cfg, k)) for k in SUB]
    out += [("0750", caminho(cfg, "biblioteca") / f["pasta"]) for f in cfg["biblioteca"]]
    out += [("0750", raiz(cfg, "claude_projects")), ("0750", raiz(cfg, "projectos"))]
    vistos, unicos = set(), []
    for modo, p in out:
        if p not in vistos:
            vistos.add(p)
            unicos.append((modo, p))
    return unicos


def credenciais_claude(cfg_path):
    """Ficheiro das credenciais do claude (ANTHROPIC_API_KEY ou CLAUDE_CODE_OAUTH_TOKEN): ao lado do office.yaml."""
    return Path(os.path.abspath(os.path.expanduser(str(cfg_path)))).parent / CLAUDE_ENV


def mangle(caminho):
    return re.sub(r"[^A-Za-z0-9]", "-", caminho)


def publico(cfg, aviso):
    """Corpo do GET /api/config: sem raizes, runner, contacto, repo nem pasta."""
    lancar = [s["id"] for s in cfg["squads"] if s["formato"] == "worktree"]
    return {
        "v": 1, "marca": cfg["marca"], "utilizador": {"nome": cfg["utilizador"]["nome"]}, "fuso": cfg["fuso"],
        "squads": [{"id": s["id"], "nome": s["nome"], "lead": s["lead"], "personagem": s["personagem"],
                    "formato": s["formato"], "tipos": list(s["tipos"]), "modos": list(s["modos"])}
                   for s in cfg["squads"]],
        "projectos": [{"id": p["id"], "nome": p["nome"], "glifo": p["glifo"], "cor": f"c{i % 8}"}
                      for i, p in enumerate(cfg["projectos"])],
        "colunas": [{"chave": k, "rotulo": v} for k, v in cfg["colunas"].items()],
        "biblioteca": [{"id": f["id"], "rotulo": f["rotulo"]} for f in cfg["biblioteca"]],
        "lancar": {"squads": lancar, "projectos": [p["id"] for p in cfg["projectos"]] if lancar else []},
        "aviso": aviso}


def main(argv):
    uso = "uso: officecfg.py validar FICH | pastas FICH | raiz FICH NOME"
    if len(argv) < 2 or (argv[0], len(argv)) not in (("validar", 2), ("pastas", 2), ("raiz", 3)):
        print(uso, file=sys.stderr)
        return 2
    try:
        cfg, _ = carregar(argv[1], estrito=True)
        if argv[0] == "pastas":
            for modo, p in pastas(cfg):
                print(f"{modo} {p}")
        elif argv[0] == "raiz":
            print(raiz(cfg, argv[2]))
    except ConfigErro as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
