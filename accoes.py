"""Acções aprovadas (ADR-021): o que o runner (runner/accoes_propor.py) e o serviço `accoes` (servico_accoes/,
outro utilizador, o único com as credenciais) partilham.

Catálogo fechado, perfis, limites, extracção e validação das propostas da squad (blocos `=====ACCAO=====` com um
objecto JSON), links por lista de domínios, definição canónica e sha, render da secção do pedido (o mesmo texto para
escrever e para conferir), linhas de decisão, vista sem segredos que o serviço publica para o runner, configuração
`accoes.json` (0644), credenciais `accoes.env` (0600), verificação de arranque e registo do colector.

Só stdlib, Python >= 3.10; não importa o `officecfg`, o `agentes` nem o runner (o serviço não os lê). Sem fuso fixo:
quem chama passa datas com fuso (o runner grava o do `office.yaml` no registo do pedido).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

TIPOS = ("email_rascunho", "email_responder", "folha_linha", "aviso_dono")
EMAIL = ("email_rascunho", "email_responder")
CAMPOS = {"email_rascunho": ("tipo", "origem", "assunto", "corpo"),
          "email_responder": ("tipo", "origem", "assunto", "corpo"),
          "folha_linha": ("tipo", "folha", "valores"),
          "aviso_dono": ("tipo", "texto")}
LIMITES_DIA = {"email_rascunho": 50, "email_responder": 20, "folha_linha": 100, "aviso_dono": 20}
MODELOS_NEGOCIO = ("atendimento", "comercial")   # = agentes.MODELOS_NEGOCIO (teste de igualdade)
PERFIS = {"lite": ("email_rascunho", "folha_linha", "aviso_dono")}   # email_responder liga-se à mão (accoes.json)
PERFIL = "lite"
MAX_POR_EXECUCAO, VALIDADE, ESPERA_S, RETENCAO_DIAS = 10, timedelta(hours=48), 120, 90
ASSUNTO_MAX, CORPO_MAX, TEXTO_MAX, VALOR_MAX = 200, 4000, 500, 500
BLOCO_MAX = 16 * 1024
CONFIG_MAX, VISTA_MAX, REGISTO_MAX = 256 * 1024, 1024 * 1024, 256 * 1024
MARCA_INI, MARCA_FIM = "=====ACCAO=====", "=====FIM ACCAO====="
ID_SQUAD = re.compile(r"[a-z][a-z0-9-]{1,19}")    # = agentes.ID_SQUAD (teste de igualdade)
ORIGEM = re.compile(r"m[0-9a-f]{16}")             # o colector gera m<16 hex>
PEDIDO = re.compile(r"accoes-([a-z][a-z0-9-]{1,19})-(\d{8})-(\d{1,2})\.md")   # fullmatch (D8)
REGISTOS = ".accoes"                              # decisoes/.accoes/<pedido sem .md>.json (D7)
NOME = re.compile(r"[a-z0-9_-]{1,30}")            # folhas e colunas da configuração
_NOME_SQUAD = re.compile(r"[a-z_]{1,30}")         # só nomes assim vindos da squad entram num motivo
ENDERECO = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+")
# gramática das linhas de decisão = runner/decisoes.RE_LINHA/RE_FEITA (teste de equivalência; o serviço não importa
# o runner)
RE_LINHA = re.compile(r"- \[([A-Za-z0-9][A-Za-z0-9._-]{0,19})\] (.*)")
RE_FEITA = re.compile(r" → (aprovado|recusado) \(\d{4}-\d{2}-\d{2}\)$")
_DATA_FEITA = re.compile(r" → (?:aprovado|recusado) \((\d{4}-\d{2}-\d{2})\)$")
_HTML = re.compile(r"<\s*/?\s*[A-Za-z!][^>]*>")
_TOKEN = re.compile(r"[\s<>\"'«»()\[\]{},;|&=]+")
_ESQUEMA = re.compile(r"([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_ESQUEMAS_LINK = ("http", "https", "mailto", "javascript", "vbscript", "data", "file", "ftp")
_HOST_SOLTO = re.compile(r"([^/:@?#]+)(?::\d{1,5}(?:[/?#]|$)|[/?#])")   # x.co/…, x.co?…, x.co#…, x.co:8080(/…)
_HOST_NU = re.compile(r"[^/:@?#]+\.([A-Za-z]{2,})")          # x.co sozinho (fullmatch)
# não são hosts soltos: extensões de ficheiro e abreviaturas (só minúsculas; .zip e .mov são TLD e ficam de fora)
_NAO_HOST_EXT = ("pdf", "doc", "docx", "xls", "xlsx", "csv", "txt", "odt", "ods", "jpg", "jpeg", "png")
_NAO_HOST = ("ex.mo", "ex.ma", "ex.mos", "ex.mas", "p.ex")
_PALAVRAS = re.compile(r"[A-Z][a-zà-ÿ]*\.[A-Z][a-zà-ÿ]+")   # «Sr.Manuel», «Obrigado.Cumprimentos»: não são hosts
_EMAIL_TXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_LOCAL = ("127.0.0.1", "localhost", "::1")
ZWJ = "\u200d"

M_ORIGEM = "mensagem de origem desconhecida"
M_NAO_LIGADA = "acção não ligada"
M_CATALOGO = "tipo fora do catálogo"
M_JSON = "JSON inválido"
M_SEM_FIM = "bloco sem fim"
M_HTML = "HTML não permitido"
M_LINK = "link para domínio fora da lista"
M_NAO_AUTENTICADO = "remetente não autenticado"
M_TERCEIROS = "endereço de e-mail de terceiros no texto"
M_FOLHA = "folha desconhecida"
M_REPETIDA = "repetida"
M_DEFINICAO = "a definição não é a aprovada"
M_CADUCADA = "caducada: mais de 48 h desde a criação"
M_INACTIVA = "a squad não está activa"
M_CONECTOR = "conector não configurado"
M_DE_FORA = "ficou de fora: máximo 10 por execução"


def m_limite(n: int) -> str:
    return f"limite do dia ({n}) atingido"


class ConfigErro(ValueError):
    """Configuração ou credenciais inválidas; a mensagem nunca leva valores (só o campo)."""


@dataclass(frozen=True)
class Raizes:
    """Pastas do serviço: `dir` (accoes.json, livro, registo do colector, estado, folhas), `segredos` (accoes.env),
    `entrada` (mensagens e vista, lidas pelo runner), `decisoes` e `agentes` (do runner, só leitura)."""
    dir: Path
    segredos: Path
    entrada: Path
    decisoes: Path
    agentes: Path

    @classmethod
    def do_ambiente(cls, env=None) -> Raizes:
        env = os.environ if env is None else env
        d = Path(env.get("ACCOES_DIR") or "/dados/accoes")
        return cls(d, Path(env.get("ACCOES_SEGREDOS") or d / "segredos"),
                   Path(env.get("ACCOES_ENTRADA") or "/dados/accoes-entrada"),
                   Path(env.get("ACCOES_DECISOES") or "/dados/decisoes"),
                   Path(env.get("ACCOES_AGENTES") or "/dados/agentes"))

    @property
    def config(self) -> Path:
        return self.dir / "accoes.json"

    @property
    def env(self) -> Path:
        return self.segredos / "accoes.env"


# --- utilitários (iguais aos do agentes.py; o serviço não o importa) --------------------------------------------------
def chars_ok(s: str) -> bool:
    """Sem Cc (salvo `\\n` e `\\t`), Cf/Co/Cs (salvo ZWJ) nem U+2028/2029 (= agentes.texto_ok com linhas)."""
    for c in s:
        cat = unicodedata.category(c)
        if (cat == "Cc" and c not in "\n\t") or c in "\u2028\u2029" or (cat in ("Cf", "Co", "Cs") and c != ZWJ):
            return False
    return True


def canon(x) -> bytes:
    """JSON canónico (como agentes.linha_rotina): chaves ordenadas, ASCII, separadores compactos."""
    return json.dumps(x, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def ler_bytes(path, maximo: int) -> bytes | None:
    """Bytes de um ficheiro regular (sem seguir symlinks no último componente) com ≤ maximo bytes; senão None."""
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


def _sem_duplicados(pares):
    d = {}
    for k, v in pares:
        if k in d:
            raise ValueError("chave repetida")
        d[k] = v
    return d


def ler_json(path, maximo: int):
    raw = ler_bytes(path, maximo)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_sem_duplicados)
    except ValueError:
        return None


def escrever_atomico(path: Path, dados: bytes, modo: int) -> None:
    """tmp na mesma pasta (O_EXCL, sem seguir symlinks) + fsync + rename: quem lê nunca vê meio ficheiro."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        os.unlink(tmp)   # um symlink deixado aqui sai, não é seguido
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with open(fd, "wb") as f:
            f.write(dados)
            f.flush()
            os.fchmod(f.fileno(), modo)
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- registo de squads (agentes/squads.json do runner) ----------------------------------------------------------------
def squads_negocio(raw) -> dict[str, str]:
    """{id: modelo} das entradas de `squads.json` (já lido) com modelo de negócio; o resto ignora-se."""
    out = {}
    if isinstance(raw, dict) and raw.get("v") == 1 and isinstance(raw.get("squads"), list):
        for s in raw["squads"]:
            if isinstance(s, dict) and isinstance(s.get("id"), str) and ID_SQUAD.fullmatch(s["id"]) \
                    and s.get("modelo") in MODELOS_NEGOCIO and s["id"] not in out:
                out[s["id"]] = s["modelo"]
    return out


# --- propostas --------------------------------------------------------------------------------------------------------
def extrair(texto: str) -> list[str | None]:
    """Blocos entre as marcas (linhas exactas), pela ordem do texto; None = bloco sem marca de fim."""
    out, buf = [], None
    for ln in (texto or "").split("\n"):
        if ln.strip() == MARCA_INI:
            if buf is not None:
                out.append(None)
            buf = []
        elif ln.strip() == MARCA_FIM and buf is not None:
            out.append("\n".join(buf))
            buf = None
        elif buf is not None:
            buf.append(ln)
    if buf is not None:
        out.append(None)
    return out


def ligadas(cfg: dict, sid: str, perfil: str = PERFIL) -> frozenset[str]:
    """Acções ligadas na squad: a lista `accoes` da configuração, se existir; senão as do perfil (só `lite`)."""
    sq = cfg["squads"].get(sid) or {}
    if "accoes" in sq:
        return frozenset(sq["accoes"])
    return frozenset(PERFIS.get(perfil, ()))


def limite(cfg: dict, sid: str, tipo: str) -> int:
    lim = (cfg["squads"].get(sid) or {}).get("limites", {})
    return min(LIMITES_DIA[tipo], lim.get(tipo, LIMITES_DIA[tipo]))


def contexto(cfg: dict, sid: str, mensagens: dict, perfil: str = PERFIL) -> dict:
    """A vista sem segredos da squad (o que `validar` precisa): o colector publica-a em
    `<entrada>/<squad>/vista.json` para o runner; o executor monta a mesma a partir da configuração e do registo."""
    sq = cfg["squads"].get(sid) or {}
    return {"v": 1, "ligadas": sorted(ligadas(cfg, sid, perfil)), "dominios": list(sq.get("dominios", [])),
            "folhas": {n: list(f["colunas"]) for n, f in sq.get("folhas", {}).items()},
            "caixa": (sq.get("email") or {}).get("endereco"), "mensagens": mensagens}


def contexto_omissao() -> dict:
    """Sem vista do serviço (ainda não instalado ou ilegível): as acções do perfil, sem domínios, folhas nem
    mensagens."""
    return {"v": 1, "ligadas": sorted(PERFIS[PERFIL]), "dominios": [], "folhas": {}, "caixa": None, "mensagens": {}}


def validar_vista(d) -> dict | None:
    """A vista lida do disco, com a forma exacta; senão None."""
    try:
        ok = (isinstance(d, dict) and set(d) == {"v", "ligadas", "dominios", "folhas", "caixa", "mensagens"}
              and d["v"] == 1 and isinstance(d["ligadas"], list) and all(t in TIPOS for t in d["ligadas"])
              and isinstance(d["dominios"], list)
              and all(isinstance(x, str) and _DOMINIO.fullmatch(x) for x in d["dominios"])
              and isinstance(d["folhas"], dict)
              and all(NOME.fullmatch(n) and isinstance(c, list) and all(isinstance(x, str) and NOME.fullmatch(x)
                                                                       for x in c) for n, c in d["folhas"].items())
              and (d["caixa"] is None or (isinstance(d["caixa"], str) and ENDERECO.fullmatch(d["caixa"])))
              and isinstance(d["mensagens"], dict)
              and all(ORIGEM.fullmatch(k) and isinstance(v, dict) and set(v) == {"remetente", "autenticada"}
                      and isinstance(v["remetente"], str) and ENDERECO.fullmatch(v["remetente"])
                      and type(v["autenticada"]) is bool for k, v in d["mensagens"].items()))
    except TypeError:
        return None
    return d if ok else None


def _nome(n) -> str:
    return n if isinstance(n, str) and _NOME_SQUAD.fullmatch(n) else ""


def _host_ok(host: str, dominios: list[str]) -> bool:
    host = (host or "").rstrip(".").lower()
    return bool(host) and any(host == d or host.endswith("." + d) for d in dominios)


def _host_estranho(host: str) -> bool:
    return "\u3002" in host or "xn--" in host.lower() or not host.isascii()


def erro_links(texto: str, dominios: list[str]) -> str | None:
    """Links só para domínios da lista (subdomínios incluídos). Por token (separado por espaços e aspas/parênteses):
    com esquema (`://` ou http/https/mailto/javascript/…): só http(s), sem `\\` nem `@`; sem esquema: só conta como
    link se começar por `www.` ou o domínio for seguido de `/` (ou `:porta/`), e então tem de ser da lista (labels
    não-ASCII, `xn--` e `\u3002` recusados). O texto passa por NFKC antes (`evil．com` = `evil.com`). `x.tld` sozinho
    (TLD alfabético ≥ 2; `,;|&=` separam tokens; `:porta` sem `/` conta) também é host; «Ex.mo», «ficheiro.pdf», «Sr.Manuel», «1.5» não
    são links."""
    norm = unicodedata.normalize("NFKC", texto or "").replace("\u3002", ".").replace("\uff61", ".")
    for tok in _TOKEN.split(norm):
        t = tok.strip(".,;:!?")
        if not t:
            continue
        m = _ESQUEMA.match(t)
        if m and ("://" in t or m.group(1).lower() in _ESQUEMAS_LINK):
            if m.group(1).lower() not in ("http", "https") or "\\" in t or "@" in t:
                return M_LINK
            try:
                host = urlsplit(t).hostname or ""
            except ValueError:
                return M_LINK
            if _host_estranho(host) or not _host_ok(host, dominios):
                return M_LINK
            continue
        if "@" in t:
            if "/" in t:   # utilizador@x.co/… pode virar link no cliente
                return M_LINK
            continue   # endereços de e-mail: ver M_TERCEIROS
        if t.lower().startswith("www."):
            host = re.split(r"[/:?#]", t, maxsplit=1)[0]
        elif (h := _HOST_SOLTO.match(t)) and ("." in h.group(1) or "\u3002" in h.group(1)):
            host = h.group(1)
        elif ((h := _HOST_NU.fullmatch(t)) and not _PALAVRAS.fullmatch(t)
              and h.group(1).lower() not in _NAO_HOST_EXT and t.lower() not in _NAO_HOST):
            host = t
        else:
            continue
        ultimo = host.rstrip(".").replace("\u3002", ".").rsplit(".", 1)[-1]
        if _host_estranho(host):
            return M_LINK
        if ultimo.isalpha() and len(ultimo) >= 2 and not _host_ok(host, dominios):
            return M_LINK
    return None


def _erro_texto(rotulo: str, v, maximo: int, uma_linha: bool, vazio_ok: bool = False) -> str | None:
    if not isinstance(v, str):
        return f"{rotulo} tem de ser texto"
    if not v.strip() and not vazio_ok:
        return f"{rotulo} vazio"
    if len(v) > maximo:
        return f"{rotulo} acima de {maximo} caracteres"
    if uma_linha and "\n" in v:
        return f"{rotulo} tem de ser uma linha"
    if not chars_ok(v):
        return f"{rotulo} com caracteres não permitidos"
    return None


def validar(bloco: str | None, sid: str, modelo: str, ctx: dict) -> tuple[dict | None, str | None]:
    """(proposta normalizada, None) ou (None, motivo). `ctx` = vista da squad (`contexto`). Ordem: JSON objecto →
    tipo no catálogo do modelo → acção ligada → campos exactos → tipos/tamanhos/linhas/caracteres → sem HTML → links
    → origem/folha."""
    if bloco is None:
        return None, M_SEM_FIM
    if len(bloco) > BLOCO_MAX:
        return None, M_JSON
    try:
        p = json.loads(bloco, object_pairs_hook=_sem_duplicados)
    except ValueError:
        return None, M_JSON
    if not isinstance(p, dict):
        return None, M_JSON
    tipo = p.get("tipo")
    if tipo not in TIPOS or modelo not in MODELOS_NEGOCIO:
        return None, M_CATALOGO
    if tipo not in ctx["ligadas"]:
        return None, M_NAO_LIGADA
    campos = CAMPOS[tipo]
    for k in p:
        if k not in campos:
            n = _nome(k)
            return None, f"campo a mais: {n}" if n else "campo a mais"
    for k in campos:
        if k not in p:
            return None, f"falta o campo {k}"
    dominios = ctx["dominios"]
    if tipo in EMAIL:
        e = (_erro_texto("assunto", p["assunto"], ASSUNTO_MAX, True)
             or _erro_texto("corpo", p["corpo"], CORPO_MAX, False))
        if not isinstance(p["origem"], str):
            e = e or "origem tem de ser texto"
        textos = [p["assunto"], p["corpo"]] if not e else []
    elif tipo == "aviso_dono":
        e = _erro_texto("texto", p["texto"], TEXTO_MAX, False)
        textos = [p["texto"]] if not e else []
    else:
        v = p["valores"]
        e = None if isinstance(p["folha"], str) else "folha tem de ser texto"
        if not e and (not isinstance(v, dict) or not v):
            e = "valores tem de ser um objecto com colunas"
        for val in (v.values() if not e else ()):
            e = e or _erro_texto("valores", val, VALOR_MAX, True, vazio_ok=True)
        textos = [p["folha"], *v.values()] if not e else []
    if e:
        return None, e
    if any(_HTML.search(t) for t in textos):
        return None, M_HTML
    if any(erro_links(t, dominios) for t in textos):
        return None, M_LINK
    if tipo in EMAIL:
        msg = ctx["mensagens"].get(p["origem"]) if ORIGEM.fullmatch(p["origem"]) else None
        if msg is None:
            return None, M_ORIGEM
        if tipo == "email_responder" and msg.get("autenticada") is not True:
            return None, M_NAO_AUTENTICADO
        proprios = (msg["remetente"].lower(), (ctx.get("caixa") or "").lower())
        if any(e.lower() not in proprios and not _host_ok(e.rsplit("@", 1)[1], dominios)   # endereços da empresa
               for t in (p["assunto"], p["corpo"]) for e in _EMAIL_TXT.findall(t)):
            return None, M_TERCEIROS
        return {"tipo": tipo, "origem": p["origem"], "assunto": p["assunto"], "corpo": p["corpo"]}, None
    if tipo == "aviso_dono":
        return {"tipo": tipo, "texto": p["texto"]}, None
    colunas = ctx["folhas"].get(p["folha"])
    if colunas is None:
        return None, M_FOLHA
    for col in p["valores"]:
        if col not in colunas:
            n = _nome(col)
            return None, f"coluna desconhecida: {n}" if n else "coluna desconhecida"
    return {"tipo": tipo, "folha": p["folha"], "valores": {c: p["valores"][c] for c in colunas if c in p["valores"]}}, None


def tipo_de(bloco: str | None) -> str | None:
    """O tipo de um bloco (para o rótulo das recusadas), só se for do catálogo."""
    try:
        t = json.loads(bloco).get("tipo") if bloco else None
    except (ValueError, AttributeError):
        return None
    return t if t in TIPOS else None


def definicao(sid: str, proposta: dict, criado_iso: str) -> dict:
    """A definição canónica (o que o sha cobre): squad, tipo, campos, origem (None fora do e-mail) e criado."""
    campos = {k: v for k, v in proposta.items() if k not in ("tipo", "origem")}
    return {"squad": sid, "tipo": proposta["tipo"], "campos": campos, "origem": proposta.get("origem"),
            "criado": criado_iso}


def sha(defn: dict) -> str:
    return hashlib.sha256(canon(defn)).hexdigest()


def citar(t: str) -> str:
    # texto livre nunca abre secção, decisão nem linha da definição
    return "\n".join(f"> {ln}" if ln else ">" for ln in t.split("\n"))


def secao(aid: str, defn: dict, sha_: str) -> str:
    """O bloco `### A1 · tipo` do pedido: o mesmo texto para escrever (runner) e para conferir (executor)."""
    c = defn["campos"]
    out = [f"### {aid} · {defn['tipo']}", f"    sha256: {sha_}", f"    criado: {defn['criado']}"]
    if defn["tipo"] in EMAIL:
        out += [f"    origem: {defn['origem']}", "assunto:", citar(c["assunto"]), "corpo:", citar(c["corpo"])]
    elif defn["tipo"] == "folha_linha":
        out += [f"    folha: {c['folha']}", "valores:", citar("\n".join(f"{k}: {v}" for k, v in c["valores"].items()))]
    else:
        out += ["texto:", citar(c["texto"])]
    return "\n".join(out)


def secao_do_md(texto: str, aid: str) -> str | None:
    """A única secção `### <aid> · ` da única `## Definição das acções`; None se não houver exactamente uma de cada."""
    linhas = texto.split("\n")
    defs = [i for i, ln in enumerate(linhas) if ln == "## Definição das acções"]
    if len(defs) != 1:
        return None
    fim = next((j for j in range(defs[0] + 1, len(linhas)) if linhas[j].startswith("## ")), len(linhas))
    ini = [i for i in range(defs[0] + 1, fim) if linhas[i].startswith(f"### {aid} · ")]
    if len(ini) != 1:
        return None
    ate = next((j for j in range(ini[0] + 1, fim) if linhas[j].startswith("### ")), fim)
    return "\n".join(linhas[ini[0]:ate])


def linha_decisao(aid: str, defn: dict, remetente: str | None) -> str:
    """A linha `- [A1] …` do pedido; acaba sempre em `· 0 US$` (a marca `→ aprovado (data)` não se forja)."""
    c = defn["campos"]
    if defn["tipo"] == "email_responder":
        return f"- [{aid}] Responder a {remetente} sobre «{c['assunto']}» · e-mail · 0 US$"
    if defn["tipo"] == "email_rascunho":
        return f"- [{aid}] Rascunho de resposta a {remetente} sobre «{c['assunto']}» · e-mail · 0 US$"
    if defn["tipo"] == "folha_linha":
        return f"- [{aid}] Linha na folha {c['folha']} · folha · 0 US$"
    return f"- [{aid}] Aviso ao dono no Telegram · aviso · 0 US$"


def decisoes_md(texto: str) -> dict[str, tuple[str | None, str | None]]:
    """{id: (estado, data)} das secções `## Decisões` (1.º id repetido ganha), como runner/decisoes.decisoes."""
    out, dentro = {}, False
    for linha in texto.split("\n"):
        if linha.startswith("## "):
            dentro = linha.strip() == "## Decisões"
            continue
        m = RE_LINHA.fullmatch(linha) if dentro else None
        if m and m.group(1) not in out:
            f, d = RE_FEITA.search(m.group(2)), _DATA_FEITA.search(m.group(2))
            out[m.group(1)] = (f.group(1), d.group(1)) if f else (None, None)
    return out


def celula(v: str) -> str:
    """Valor de folha seguro contra injecção de fórmulas: tab passa a espaço; `'` à frente se começar por
    = + - @ ou CR, e à frente de cada segmento depois de `;` que (sem os espaços) comece por = + - @."""
    v = v.replace("\t", " ")
    partes = v.split(";")
    for i, seg in enumerate(partes):
        corpo = seg.lstrip(" ")
        if corpo[:1] in ("=", "+", "-", "@", "\r"):
            partes[i] = seg[:len(seg) - len(corpo)] + "'" + corpo
    return ";".join(partes)


# --- configuração (accoes.json, 0644) ---------------------------------------------------------------------------------
_TOPO = {"v", "telegram", "squads"}
_SQUAD = {"accoes", "dominios", "limites", "email", "folhas"}
_EMAIL = {"endereco", "pasta", "rascunhos", "imap", "smtp"}
_SERVIDOR = {"host", "porta", "tls"}
_DOMINIO = re.compile(r"[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+")


def _exige(ok: bool, msg: str) -> None:
    if not ok:
        raise ConfigErro(msg)


def _chaves(d, permitidas: set, rotulo: str, obrigatorias=()) -> None:
    _exige(isinstance(d, dict), f"{rotulo}: tem de ser um objecto")
    for k in d:
        _exige(k in permitidas, f"{rotulo}: chave desconhecida")
    for k in obrigatorias:
        _exige(k in d, f"{rotulo}.{k}: obrigatório")


def _servidor(d, rotulo: str) -> None:
    _chaves(d, _SERVIDOR, rotulo, _SERVIDOR)
    _exige(isinstance(d["host"], str) and 0 < len(d["host"]) <= 253, f"{rotulo}.host: inválido")
    _exige(type(d["porta"]) is int and 0 < d["porta"] < 65536, f"{rotulo}.porta: inválida")
    _exige(type(d["tls"]) is bool, f"{rotulo}.tls: true ou false")
    _exige(d["tls"] or d["host"] in _LOCAL, f"{rotulo}.tls: só pode ser false em localhost")


def _caminho_folha(v, raiz: Path, rotulo: str) -> None:
    _exige(isinstance(v, str) and v.startswith("/"), f"{rotulo}: caminho absoluto")
    pasta = Path(os.path.realpath(Path(raiz) / "folhas"))
    real = Path(os.path.realpath(v))
    _exige(real.parent == pasta and real.suffix == ".csv", f"{rotulo}: só ficheiros .csv directamente em <raiz>/folhas/")


def validar_config(d, raiz: Path) -> dict:
    """`raiz`: a pasta do serviço (a do `accoes.json`); as folhas só podem estar em `<raiz>/folhas/*.csv`."""
    _chaves(d, _TOPO, "accoes.json", ("v", "squads"))
    _exige(d["v"] == 1, "v: tem de ser 1")
    if "telegram" in d:
        t = d["telegram"]
        _chaves(t, {"url", "chat_id"}, "telegram", ("url", "chat_id"))
        _exige(isinstance(t["chat_id"], str) and re.fullmatch(r"-?\d{1,20}", t["chat_id"]) is not None,
               "telegram.chat_id: inválido")
        try:
            u = urlsplit(t["url"]) if isinstance(t["url"], str) else None
        except ValueError:
            u = None
        _exige(u is not None and (u.scheme == "https" or (u.scheme == "http" and u.hostname in _LOCAL)),
               "telegram.url: https (http só em localhost)")
    _exige(isinstance(d["squads"], dict), "squads: tem de ser um objecto")
    enderecos = set()
    for sid, sq in d["squads"].items():
        _exige(ID_SQUAD.fullmatch(sid) is not None, "squads: id inválido")
        r = f"squads.{sid}"
        _chaves(sq, _SQUAD, r)
        if "accoes" in sq:
            _exige(isinstance(sq["accoes"], list) and all(a in TIPOS for a in sq["accoes"]), f"{r}.accoes: inválido")
        doms = sq.get("dominios", [])
        _exige(isinstance(doms, list) and all(isinstance(x, str) and _DOMINIO.fullmatch(x) for x in doms),
               f"{r}.dominios: inválido")
        lim = sq.get("limites", {})
        _exige(isinstance(lim, dict) and all(k in TIPOS and type(v) is int and 0 <= v <= LIMITES_DIA[k]
                                             for k, v in lim.items()), f"{r}.limites: só podem baixar")
        if "email" in sq:
            e = sq["email"]
            _chaves(e, _EMAIL | {"authserv_id"}, f"{r}.email", _EMAIL)
            asv = e.get("authserv_id", "x")
            _exige(isinstance(asv, str) and re.fullmatch(r"[A-Za-z0-9.-]{1,253}", asv) is not None,
                   f"{r}.email.authserv_id: inválido")
            _exige(isinstance(e["endereco"], str) and ENDERECO.fullmatch(e["endereco"]) is not None,
                   f"{r}.email.endereco: inválido")
            _exige(e["endereco"].lower() not in enderecos, f"{r}.email.endereco: uma caixa serve uma squad")
            enderecos.add(e["endereco"].lower())
            for k in ("pasta", "rascunhos"):
                _exige(isinstance(e[k], str) and 0 < len(e[k]) <= 100 and chars_ok(e[k])
                       and "\n" not in e[k] and '"' not in e[k] and "\\" not in e[k], f"{r}.email.{k}: inválido")
            _servidor(e["imap"], f"{r}.email.imap")
            _servidor(e["smtp"], f"{r}.email.smtp")
        fs = sq.get("folhas", {})
        _exige(isinstance(fs, dict), f"{r}.folhas: tem de ser um objecto")
        for nome, f in fs.items():
            _exige(NOME.fullmatch(nome) is not None, f"{r}.folhas: nome inválido")
            _chaves(f, {"ficheiro", "colunas"}, f"{r}.folhas.{nome}", ("ficheiro", "colunas"))
            _caminho_folha(f["ficheiro"], raiz, f"{r}.folhas.{nome}.ficheiro")
            cols = f["colunas"]
            _exige(isinstance(cols, list) and 0 < len(cols) <= 30 and len(set(cols)) == len(cols)
                   and all(isinstance(c, str) and NOME.fullmatch(c) for c in cols), f"{r}.folhas.{nome}.colunas")
    return d


def config_de_texto(dados: bytes, raiz: Path) -> dict:
    _exige(len(dados) <= CONFIG_MAX, "accoes.json: grande demais")
    try:
        d = json.loads(dados.decode("utf-8"), object_pairs_hook=_sem_duplicados)
    except ValueError:
        raise ConfigErro("accoes.json: JSON inválido") from None
    return validar_config(d, raiz)


def ler_config(path) -> dict:
    """`accoes.json`: ausente → sem squads (o perfil decide); symlink, não regular, gravável por grupo/outros ou
    inválido → ConfigErro."""
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return {"v": 1, "squads": {}}
    except OSError:
        raise ConfigErro("accoes.json ilegível") from None
    _exige(stat.S_ISREG(st.st_mode), "accoes.json: tem de ser um ficheiro regular (sem symlink)")
    _exige(not st.st_mode & 0o022, "accoes.json: gravável por grupo/outros (chmod 644)")
    dados = ler_bytes(path, CONFIG_MAX)
    _exige(dados is not None, "accoes.json ilegível")
    return config_de_texto(dados, path.parent)


# --- credenciais (accoes.env, 0600) e arranque (CA-18b) ---------------------------------------------------------------
_CHAVE_SEG = re.compile(r"TELEGRAM_TOKEN|[A-Z][A-Z0-9_]{1,19}_EMAIL_(?:UTILIZADOR|SENHA)")


def _erro_pasta(p: Path, rotulo: str) -> str | None:
    try:
        st = os.stat(p)
    except OSError:
        return f"{rotulo}: pasta ilegível"
    return f"{rotulo}: pasta gravável por grupo/outros" if st.st_mode & 0o022 else None


def erro_arranque(cfg_path, segredos, codigo) -> str | None:
    """Mensagem clara (e o serviço sai 1) se: as credenciais forem symlink, não regulares, de outro utilizador,
    tiverem algum bit de grupo/outros ou estiverem numa pasta gravável por outros; a configuração for symlink ou
    gravável por grupo/outros (ou a pasta dela); o código não for do root nem do próprio utilizador, ou for gravável
    por outros. Credenciais ausentes: só as acções sem conta (folha) funcionam."""
    segredos, cfg_path = Path(segredos), Path(cfg_path)
    try:
        st = os.lstat(segredos)
    except FileNotFoundError:
        st = None
    except OSError:
        return "credenciais: ilegíveis"
    if st is not None:
        if stat.S_ISLNK(st.st_mode):
            return "credenciais: o ficheiro é um symlink"
        if not stat.S_ISREG(st.st_mode):
            return "credenciais: tem de ser um ficheiro regular"
        if st.st_uid != os.geteuid():
            return "credenciais: o ficheiro não é do utilizador que corre"
        if st.st_mode & 0o077:
            return "credenciais: legíveis ou graváveis por grupo/outros (chmod 600)"
        e = _erro_pasta(segredos.parent, "credenciais")
        if e:
            return e
    try:
        st = os.lstat(cfg_path)
    except FileNotFoundError:
        st = None
    except OSError:
        return "configuração: ilegível"
    if st is not None:
        if not stat.S_ISREG(st.st_mode):
            return "configuração: tem de ser um ficheiro regular (sem symlink)"
        if st.st_mode & 0o022:
            return "configuração: gravável por grupo/outros (chmod 644)"
        e = _erro_pasta(cfg_path.parent, "configuração")
        if e:
            return e
    try:
        st = os.stat(codigo)
    except OSError:
        return "código: pasta ilegível"
    if st.st_uid not in (0, os.geteuid()):
        return "código: a pasta não é do root nem do utilizador que corre"
    if st.st_mode & 0o002:
        return "código: pasta gravável por outros"
    return None


def segredos_de_texto(texto: str) -> dict[str, str]:
    """`CHAVE=valor` por linha, `#` comentários. Só `TELEGRAM_TOKEN`, `<SQUAD>_EMAIL_UTILIZADOR` e
    `<SQUAD>_EMAIL_SENHA`; valor numa linha, sem caracteres de controlo. ConfigErro só com o n.º da linha."""
    out = {}
    for n, ln in enumerate(texto.splitlines(), 1):
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        k, sep, v = ln.partition("=")
        k, v = k.strip(), v.strip()
        _exige(bool(sep) and _CHAVE_SEG.fullmatch(k) is not None, f"credenciais: linha {n}: chave desconhecida")
        _exige(bool(v) and len(v) <= 1000 and v.isprintable(), f"credenciais: linha {n}: valor inválido")
        out[k] = v
    return out


def ler_segredos(path) -> dict[str, str]:
    """O `accoes.env`; ausente → {}."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return {}
    except OSError:
        raise ConfigErro("credenciais: ilegíveis") from None
    with open(fd, encoding="utf-8") as f:
        return segredos_de_texto(f.read(64 * 1024))


def chave_sq(sid: str) -> str:
    return sid.upper().replace("-", "_")


# --- registo do colector (<dir>/mensagens/<id>.json) ------------------------------------------------------------------
def mensagem(raiz: Path, origem, sid: str) -> dict | None:
    """O registo da mensagem `origem`, só se for desta squad e tiver um remetente válido."""
    if not isinstance(origem, str) or not ORIGEM.fullmatch(origem):
        return None
    d = ler_json(Path(raiz) / "mensagens" / f"{origem}.json", 64 * 1024)
    if not isinstance(d, dict) or d.get("id") != origem or d.get("squad") != sid:
        return None
    if not isinstance(d.get("remetente"), str) or not ENDERECO.fullmatch(d["remetente"]):
        return None
    return d
