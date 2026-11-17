"""Regras partilhadas da Biblioteca e do arquivo (Office e runner): caminhos, fila do arquivo, REGISTO e protecções.

Sem estado global e sem imports do projecto: o Office (`biblioteca.py`) e o runner (`runner/arquivador.py`, no host
ou no contentor `runner`) importam este módulo, por isso as regras de caminho nunca divergem. Só stdlib, Python ≥ 3.10.

Fila `<dados>/arquivo-fila/` (contrato v1, ADR-011): o Office grava `<id>.json` com as chaves CHAVES por esta ordem
(nome só do relógio e de `token_hex(4)`; tmp + fsync + `os.link`); o runner responde `<id>.feito.json` com
`{"ok": true}` ou `{"erro": <um de ERROS>}`.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# --- regras de caminho (eram de biblioteca.py; valores iguais) -----------------------------------------------------
TIPOS = {"jpg": "imagem", "jpeg": "imagem", "png": "imagem", "webp": "imagem", "gif": "imagem",
         "mp4": "video", "webm": "video", "m4v": "video",
         "mp3": "audio", "wav": "audio", "m4a": "audio", "ogg": "audio", "opus": "audio",
         "md": "doc", "txt": "doc", "csv": "doc", "pdf": "pdf"}
MEDIA = ("imagem", "video", "audio")
PASTAS_NEGADAS = ("node_modules", "dados", "__pycache__")
SUF_NEGADOS = (".env", ".key", ".pem", ".p12", ".pfx")
PALAVRAS_NEGADAS = ("credential", "secret", "segredo", "password", "passwd")
PROF_MAX = 8                 # componentes do caminho relativo
ENTRADAS_MAX = 20000         # por raiz (pasta errada montada)
SUFIXO = re.compile(r"\.(?:md|revisao(?:-[^.]+)?|juridico|parecer-juridico|copy)$", re.I)

# --- arquivo ------------------------------------------------------------------------------------------------------
DIR = ".arquivo"
REGISTO = "REGISTO.tsv"
CABECALHO = "data\taccao\tcaminho\tmotivo\titem\n"
ACCOES = ("arquivar", "repor")
GENEROS = ("documento", "lote")      # «pasta» de media fora de media/: não
NOME = re.compile(r"\d{8}T\d{6}-[0-9a-f]{8}\.json")                  # fullmatch
FEITO = re.compile(r"(\d{8}T\d{6}-[0-9a-f]{8})\.feito\.json")       # fullmatch
CHAVES = ("v", "id", "accao", "fonte", "chave", "pedido_em", "ip", "ua")   # exactas e por esta ordem
PEDIDO_MAX = 4096
PENDENTES_MAX = 50
CHAVE_MAX = 1024
IP_MAX = 64
UA_MAX = 200
PEDIDO_EM_MAX = 40
FECHADOS = ("aprovado", "executado", "recusado")
RECENTE_S = 48 * 3600
CITADO_AUTO_S = 14 * 86400
ERROS = ("pedido ilegível", "fonte desconhecida", "item inexistente", "não arquivável", "destino já existe",
         "sistemas de ficheiros diferentes", "symlink no caminho", "nome com tab ou quebra de linha",
         "não está no arquivo", "erro a mover",
         "protegido: citado por um pedido de aprovação pendente", "protegido: alterado há menos de 48 h",
         "alterações por commitar", "erro git")

_DIRFD = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC


def _utf8(c: str) -> bool:
    # nome não-UTF-8 vem do scandir com surrogates (caf\udce9.md) e rebentaria o json.dumps/quote do índice
    try:
        c.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def componente_ok(c: str) -> bool:
    return bool(c) and not c.startswith(".") and "\\" not in c and "\x00" not in c and "/" not in c and _utf8(c)


def negado(c: str) -> bool:
    cf = c.casefold()
    return (any(w in cf for w in PALAVRAS_NEGADAS) or cf.endswith(SUF_NEGADOS)
            or cf.startswith(("id_rsa", "id_ed25519")))


def classificar(partes, media: bool) -> str | None:
    # S1 (allowlist pela última extensão) + S2 (denylist por componente) → "imagem|video|audio|doc|pdf" ou None
    if not partes or len(partes) > PROF_MAX:
        return None
    if any(not componente_ok(c) or negado(c) for c in partes):
        return None
    if any(c in PASTAS_NEGADAS for c in partes[:-1]):
        return None
    _, ponto, ext = partes[-1].lower().rpartition(".")
    tipo = TIPOS.get(ext) if ponto else None
    if tipo in MEDIA and not media:   # media só nas fontes com media: true; o resto só documentos
        return None
    return tipo


def raiz_nome(nome: str) -> str:
    # x.md, x.copy.md, x.md.revisao.md, x.revisao-2.md… → x
    while True:
        novo = SUFIXO.sub("", nome)
        if novo == nome:
            return nome
        nome = novo


def varrer(root, media: bool, nome: str) -> list[tuple[tuple[str, ...], str, os.stat_result, str]]:
    # S3: scandir sem seguir symlinks (ficheiros e pastas symlink ignorados), profundidade ≤ PROF_MAX
    # → (partes, caminho, lstat, tipo) de cada ficheiro regular aceite por classificar; `nome` só na mensagem do corte
    out, n, pilha = [], 0, [(str(root), ())]
    while pilha:
        d, partes = pilha.pop()
        try:
            with os.scandir(d) as it:
                entradas = list(it)
        except OSError:
            continue
        for e in entradas:
            n += 1
            if n > ENTRADAS_MAX:
                print(f"biblioteca: {nome} com mais de {ENTRADAS_MAX} entradas, varrimento cortado", file=sys.stderr)
                return out
            if not componente_ok(e.name) or negado(e.name):
                continue
            p = partes + (e.name,)
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if len(p) < PROF_MAX and e.name not in PASTAS_NEGADAS:
                        pilha.append((e.path, p))
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            tipo = classificar(p, media)
            if tipo is not None and stat.S_ISREG(st.st_mode):
                out.append((p, e.path, st, tipo))
    return out


def citacao_re(fontes) -> re.Pattern:
    # caminhos <fonte>/… citados (também ../<fonte>/…, em crase, em ![](…), no frontmatter)
    alt = "|".join(re.escape(r) for r in fontes)
    return re.compile(r"(?<![\w-])(?:\.\./)*((?:" + alt + r")/[^\s`'\"()«»|]+)" if alt else r"(?!)")


def citacoes(texto: str, rx: re.Pattern) -> list[tuple[str, str]]:
    # (linha, caminho) de cada caminho citado
    out = []
    for linha in texto.split("\n"):
        for m in rx.finditer(linha):
            out.append((linha, m.group(1).rstrip(".,;:").rstrip("/")))
    return out


def _controlo(s: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7f for c in s)


def validar_chave(chave) -> bool:
    # caminho relativo do item na fonte (sem o id da fonte): os componentes do índice, sem pastas negadas
    if not isinstance(chave, str) or not chave or len(chave) > CHAVE_MAX or _controlo(chave):
        return False
    partes = chave.split("/")
    if len(partes) > PROF_MAX:
        return False
    return all(componente_ok(c) and not negado(c) and c not in PASTAS_NEGADAS for c in partes)


# --- fila ---------------------------------------------------------------------------------------------------------

def ler_json(dfd: int, nome: str, maximo: int = PEDIDO_MAX):
    # JSON de um ficheiro regular `nome` dentro de `dfd` (sem seguir symlinks) com ≤ maximo bytes; senão None
    try:
        fd = os.open(nome, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
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


def _texto(v, maximo: int) -> bool:
    return isinstance(v, str) and len(v) <= maximo


def pedido_ok(p, stem: str, fontes) -> bool:
    return (isinstance(p, dict) and tuple(p) == CHAVES and type(p["v"]) is int and p["v"] == 1
            and p["id"] == stem and p["accao"] in ACCOES and isinstance(p["fonte"], str) and p["fonte"] in fontes
            and validar_chave(p["chave"]) and _texto(p["pedido_em"], PEDIDO_EM_MAX) and _texto(p["ip"], IP_MAX)
            and _texto(p["ua"], UA_MAX))


def feito_ok(d) -> dict | None:
    if isinstance(d, dict) and list(d) == ["ok"] and d["ok"] is True:
        return {"ok": True}
    if isinstance(d, dict) and list(d) == ["erro"] and d["erro"] in ERROS:
        return {"erro": d["erro"]}
    return None


def ler_fila(fila: Path, fontes) -> list[tuple[str, dict, dict | None]]:
    """(stem, pedido, feito) dos pedidos válidos na raiz da fila, por nome (o mais antigo primeiro).

    feito: None = sem resposta (pendente); `feito_ok(…)` quando a resposta é válida; `{}` quando há resposta mas
    ilegível (já não está pendente, mas não há motivo a mostrar). Fila ausente ou ilegível ⇒ [].
    """
    try:
        dfd = os.open(fila, _DIRFD)
    except OSError:
        return []
    try:
        try:
            nomes = set(os.listdir(dfd))
        except OSError:
            return []
        out = []
        for nome in sorted(n for n in nomes if NOME.fullmatch(n)):
            stem = nome[:-5]
            p = ler_json(dfd, nome)
            if not pedido_ok(p, stem, fontes):
                continue
            feito = None
            if stem + ".feito.json" in nomes:
                feito = feito_ok(ler_json(dfd, stem + ".feito.json")) or {}
            out.append((stem, p, feito))
        return out
    finally:
        os.close(dfd)


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


def escrever_pedido(fila: Path, accao: str, fonte: str, chave: str, ip: str, ua: str, tz,
                    now: float) -> tuple[int, dict]:
    """Grava um pedido v1 na fila. Sem lock: o chamador serializa. accao/fonte/chave já validados pelo chamador."""
    fila = Path(fila)
    indisponivel = 503, {"error": "fila do arquivo indisponível"}
    if not fila.is_dir() or not os.access(fila, os.W_OK):
        return indisponivel
    pendentes = [p for _, p, f in ler_fila(fila, _TODAS) if f is None]
    if any(p["fonte"] == fonte and p["chave"] == chave for p in pendentes):
        return 409, {"error": "já há um pedido em fila para este item"}
    if len(pendentes) >= PENDENTES_MAX:
        return 429, {"error": "fila cheia"}
    dt = datetime.fromtimestamp(now, tz)
    for tentativa in range(3):
        stem = f"{dt.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
        doc = {"v": 1, "id": stem, "accao": accao, "fonte": fonte, "chave": chave,
               "pedido_em": dt.isoformat(timespec="seconds"), "ip": (ip or "")[:IP_MAX], "ua": (ua or "")[:UA_MAX]}
        try:
            _criar(fila, stem + ".json", doc)
            return 202, {"id": stem, "accao": accao}
        except FileExistsError:
            if tentativa == 2:
                return indisponivel
        except OSError:
            return indisponivel
    return indisponivel   # pragma: no cover - o ciclo devolve sempre


class _Todas:
    # «qualquer fonte» para contar pendentes (a fonte de um pedido antigo pode já não estar na config)
    def __contains__(self, x) -> bool:
        return isinstance(x, str)


_TODAS = _Todas()


# --- REGISTO.tsv --------------------------------------------------------------------------------------------------

def linha_registo(data: str, accao: str, caminho: str, motivo: str, item: str) -> str | None:
    campos = (data, accao, caminho, motivo, item)
    if any(not isinstance(c, str) or any(x in c for x in "\t\n\r") for c in campos):
        return None
    return "\t".join(campos) + "\n"


def ler_registo(texto: str) -> tuple[dict, dict]:
    # (item → (data, accao, motivo), caminho → idem); linhas mal formadas e o cabeçalho ignorados; a última ganha
    por_item, por_caminho = {}, {}
    for linha in texto.split("\n"):
        campos = linha.rstrip("\r").split("\t")
        if len(campos) != 5 or campos[1] not in ACCOES:
            continue
        data, accao, caminho, motivo, item = campos
        por_item[item] = (data, accao, motivo)
        por_caminho[caminho] = (data, accao, motivo)
    return por_item, por_caminho


def fechado(estado) -> bool:
    # estado de um pedido de aprovação (sem «#…» de comentário); desconhecido ou pendente ⇒ protege
    if not isinstance(estado, str):
        return False
    return estado.split("#", 1)[0].strip() in FECHADOS
