"""Biblioteca: material das squads (media e documentos) servido a partir de pastas só de leitura.

Índice em memória (instantâneo imutável, refeito no máximo 1× por TTL_S, um varrimento de cada vez) com as regras S1–S4:
allowlist de extensões, denylist de nomes/pastas, sem symlinks, e só se serve o que está no índice (aberto com
O_NOFOLLOW e confirmado com fstat: dev, inode e tamanho iguais ao índice). Textos saem mascarados (reader.mask),
≤ TEXTO_MAX.
Única escrita: miniaturas em MINI_DIR (tmpfs), só com Pillow (import opcional; sem ele serve-se o original).
Só stdlib + PIL opcional; o server.py só instancia o Indice e encaminha /api/biblioteca* para handle().
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import stat
import sys
import threading
import time
import unicodedata
import warnings
import zipfile
from collections import Counter, namedtuple
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

import aprovacoes
import reader

try:   # opcional: só na imagem Docker
    from PIL import Image, ImageOps
except ImportError:   # pragma: no cover - depende do ambiente
    Image = ImageOps = None

# preenchidos por configurar(cfg): ids das fontes e as que aceitam media (lotes em <fonte>/media/<lote>/)
RAIZES: tuple = ()
RAIZES_MEDIA: tuple = ()
TIPOS = {"jpg": "imagem", "jpeg": "imagem", "png": "imagem", "webp": "imagem", "gif": "imagem",
         "mp4": "video", "webm": "video", "m4v": "video",
         "mp3": "audio", "wav": "audio", "m4a": "audio", "ogg": "audio", "opus": "audio",
         "md": "doc", "txt": "doc", "csv": "doc", "pdf": "pdf"}
MEDIA = ("imagem", "video", "audio")
CONTENT_TYPE = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif",
                "mp4": "video/mp4", "m4v": "video/mp4", "webm": "video/webm", "mp3": "audio/mpeg", "wav": "audio/wav",
                "m4a": "audio/mp4", "ogg": "audio/ogg", "opus": "audio/ogg", "md": "text/plain; charset=utf-8",
                "txt": "text/plain; charset=utf-8", "csv": "text/plain; charset=utf-8", "pdf": "application/pdf"}
PASTAS_NEGADAS = ("node_modules", "dados", "__pycache__")
SUF_NEGADOS = (".env", ".key", ".pem", ".p12", ".pfx")
PALAVRAS_NEGADAS = ("credential", "secret", "segredo", "password", "passwd")

TTL_S = 60
NOVO_S = 48 * 3600
CAB_BYTES = 8192
TEXTO_MAX = 1024 * 1024
BLOCO = 64 * 1024
ZIP_MAX_N = 300
ZIP_MAX_BYTES = 1024 ** 3
ZIP_SLOTS = threading.BoundedSemaphore(2)
ZIP_TIMEOUT_S = 60           # cliente que deixa de ler não segura o slot para sempre
ZIP_ESPERA_S = 3             # o pedido real espera um pouco por um slot antes do 503
MINI_DIR = Path(os.environ.get("BIBLIOTECA_MINI_DIR") or "/tmp/miniaturas")
MINI_W, MINI_H = 320, 640
MINI_Q = 70
MINI_CACHE_MAX = 100 * 1024 * 1024   # tmpfs de 128m (conta para a memória do contentor)
MINI_CACHE_ALVO = 75 * 1024 * 1024
MINI_SLOTS = threading.BoundedSemaphore(1)   # um PNG RGBA de 40 MP descodificado ≈ 330 MB; 2 passavam o mem_limit
MINI_ESPERA_S = 20
MINI_MAX_PX = 40_000_000           # acima disto não se descodifica (bomba de descompressão) e a miniatura dá 404
MINI_FORMATOS = ("JPEG", "PNG", "WEBP", "GIF")
if Image is not None:
    Image.MAX_IMAGE_PIXELS = MINI_MAX_PX
TITULO_MAX, RESUMO_MAX = 90, 140
PROF_MAX = 8                 # componentes do caminho relativo
ENTRADAS_MAX = 20000         # por raiz (pasta errada montada)
META_MAX = 256 * 1024

NAO_ENCONTRADO = json.dumps({"error": "não encontrado"}, ensure_ascii=False).encode()
CC_FICHEIRO = "private, max-age=3600"
CSP = "default-src 'none'; sandbox"

Ficheiro = namedtuple("Ficheiro", "raiz rel abs tipo ext size mtime mtime_ns dev ino")
Snapshot = namedtuple("Snapshot", "gerado itens por_id ficheiros ficheiro_item corpo_json corpo_gz")

CITACAO = re.compile(r"(?!)")   # caminhos <fonte media>/… citados; compilado em configurar
SUFIXO = re.compile(r"\.(?:md|revisao(?:-[^.]+)?|juridico|parecer-juridico|copy)$", re.I)
DATA = re.compile(r"^\d{4}-\d{2}-\d{2}-?")
FOLHA = re.compile(r"^folha\s*:\s*", re.I)
RESUMO_FORA = re.compile(r"[#|\->!`]|\*(?!\*)|\d+\.")   # «**Negrito:** …» fica; listas «* » não
RESUMO_META = re.compile(r"(?:data|autor(?:es)?|base|pedido|estado|revisor|vers[aã]o|fontes?|lido)\s*:", re.I)
FRASE = re.compile(r"(.{20,}?[.!?])(?:\s+(?=[^a-zà-ÿ\d\s])|$)")   # 1.ª frase com ≥ 20 car. («Pergunta.» não conta)
_B = r"(?<![a-z0-9])"        # início de palavra (texto já sem acentos e em minúsculas)
_E = r"(?![a-z0-9])"
# produtos = projectos da config (por id e nome normalizado) + geral; tipo de cada fonte (opcional)
PRODUTOS_KW: tuple = ()
PRODUTO_VALOR: dict = {}
TIPO_RAIZ: dict = {}
TIPO_MEDIA = {"imagem": "imagens", "video": "videos", "audio": "audio"}
TAREFA_DOCS = ("SPEC.md", "PLAN.md", "QA.md")


# --- regras puras -------------------------------------------------------------------------------------------------

def configurar(cfg: dict) -> None:
    global RAIZES, RAIZES_MEDIA, CITACAO, PRODUTOS_KW, PRODUTO_VALOR, TIPO_RAIZ
    fontes = cfg["biblioteca"]
    RAIZES = tuple(f["id"] for f in fontes)
    RAIZES_MEDIA = tuple(f["id"] for f in fontes if f["media"])
    alt = "|".join(re.escape(r) for r in RAIZES_MEDIA)
    CITACAO = re.compile(r"(?<![\w-])(?:\.\./)*((?:" + alt + r")/[^\s`'\"()«»|]+)" if alt else r"(?!)")
    TIPO_RAIZ = {f["id"]: f["tipo"] for f in fontes if f["tipo"]}
    PRODUTO_VALOR, kw = {}, []
    for p in cfg["projectos"]:
        nomes = [p["id"]] + [n for n in (normaliza(p["nome"]), re.sub(r"\s+", "-", normaliza(p["nome"]))) if n != p["id"]]
        for n in nomes:
            PRODUTO_VALOR.setdefault(n, p["id"])
        kw.append((p["id"], re.compile(_B + "(?:" + "|".join(re.escape(n) for n in nomes) + ")" + _E)))
    PRODUTOS_KW = tuple(kw)


def dirs(cfg: dict, base: Path) -> dict[str, Path]:
    # fonte → <BIBLIOTECA_DIR>/<pasta>; pasta inexistente → ignorada
    return {f["id"]: Path(base) / f["pasta"] for f in cfg["biblioteca"] if (Path(base) / f["pasta"]).is_dir()}


def _componente_ok(c: str) -> bool:
    return bool(c) and not c.startswith(".") and "\\" not in c and "\x00" not in c and "/" not in c and _utf8(c)


def _utf8(c: str) -> bool:
    # nome não-UTF-8 vem do scandir com surrogates (caf\udce9.md) e rebentaria o json.dumps/quote do índice
    try:
        c.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _negado(c: str) -> bool:
    cf = c.casefold()
    return (any(w in cf for w in PALAVRAS_NEGADAS) or cf.endswith(SUF_NEGADOS)
            or cf.startswith(("id_rsa", "id_ed25519")))


def classificar(raiz: str, partes) -> str | None:
    # S1 (allowlist pela última extensão) + S2 (denylist por componente) → "imagem|video|audio|doc|pdf" ou None
    if not partes or len(partes) > PROF_MAX:
        return None
    if any(not _componente_ok(c) or _negado(c) for c in partes):
        return None
    if any(c in PASTAS_NEGADAS for c in partes[:-1]):
        return None
    _, ponto, ext = partes[-1].lower().rpartition(".")
    tipo = TIPOS.get(ext) if ponto else None
    if tipo in MEDIA and raiz not in RAIZES_MEDIA:   # media só nas fontes com media: true; o resto só documentos
        return None
    return tipo


def raiz_nome(nome: str) -> str:
    # x.md, x.copy.md, x.md.revisao.md, x.revisao-2.md… → x
    while True:
        novo = SUFIXO.sub("", nome)
        if novo == nome:
            return nome
        nome = novo


def citacoes(texto: str) -> list[tuple[str, str]]:
    # (linha, caminho) de cada caminho <fonte media>/… citado (também ../<fonte>/…, em crase, em ![](…), no frontmatter)
    out = []
    for linha in texto.split("\n"):
        for m in CITACAO.finditer(linha):
            out.append((linha, m.group(1).rstrip(".,;:").rstrip("/")))
    return out


def normaliza(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.casefold().split())


def produtos_kw(texto: str) -> list[str]:
    t = normaliza(texto)
    return [k for k, rx in PRODUTOS_KW if rx.search(t)]


def produtos_valor(v: str) -> list[str]:
    # "loja, blog" → ["loja", "blog"] (id ou nome do projecto); desconhecido → geral
    out = []
    for p in v.split(","):
        p = normaliza(p)
        if p:
            k = PRODUTO_VALOR.get(p, "geral")
            if k not in out:
                out.append(k)
    return out


def produto_projecto(project: str) -> str:
    # project da meta.json: id ou nome do projecto (também «Loja (auditoria)»); senão geral
    p = normaliza(re.sub(r"\s*\(.*\)$", "", project))
    return PRODUTO_VALOR.get(p) or PRODUTO_VALOR.get(re.sub(r"\s+", "-", p)) or "geral"


def rotulo(nome: str, folha: bool = False) -> str:
    cf = nome.casefold()
    if "juridico" in cf or "parecer" in cf:
        return "Parecer jurídico"
    if "revisao" in cf:
        return "Revisão"
    if "copy" in cf:
        return "Copy"
    if folha and nome in ("folha.md", "README.md"):
        return "Folha"
    return {"SPEC.md": "Spec", "PLAN.md": "Plano", "QA.md": "QA"}.get(nome, "Texto")


def estado_pedido(v: str) -> str | None:
    # estado do frontmatter (já sem comentário); com maiúsculas ou desconhecido → None
    if v.startswith(("pendente", "parcial")):
        return "pendente"
    if v.startswith("bloqueado"):
        return "bloqueado"
    return v if v in ("aprovado", "executado", "recusado") else None


def intervalo(header, size: int):
    # um só intervalo bytes=a-b | a- | -n → (a, b); fora do tamanho → "416"; vários ou inválido → None (200 inteiro)
    if not header:
        return None
    unidade, _, spec = header.strip().partition("=")
    if unidade.strip().lower() != "bytes" or "," in spec:
        return None
    m = re.fullmatch(r"\s*(\d{0,19})\s*-\s*(\d{0,19})\s*", spec)   # ≤ 19 dígitos: int() nunca falha
    if not m or not (m.group(1) or m.group(2)):
        return None
    if not m.group(1):
        n = int(m.group(2))
        if n == 0 or size == 0:
            return "416"
        return max(0, size - n), size - 1
    a = int(m.group(1))
    if a >= size:
        return "416"
    b = int(m.group(2)) if m.group(2) else size - 1
    if b < a:
        return None
    return a, min(b, size - 1)


def _corta(s: str, n: int) -> str:
    s = reader.mask(s)
    return s if len(s) <= n else s[:n - 1] + "…"


def _front(texto: str) -> tuple[dict, str]:
    # frontmatter com a gramática do aprovacoes.py → (chaves, corpo depois da linha que o fecha)
    fm = aprovacoes.FRONT.match(texto)
    if not fm:
        return {}, texto
    front = {}
    for linha in fm.group(1).splitlines():
        k, _, v = linha.partition(":")
        front[k.strip()] = v.split("#")[0].strip()
    return front, "\n".join(texto[fm.end():].split("\n")[1:])


def cabecalho(texto: str) -> tuple[str | None, str | None, dict]:
    # (1.º «# », 1.ª frase útil do corpo, antes ou depois do título, frontmatter) — sem máscara.
    # Salta títulos, listas (e linhas indentadas), tabelas, citações, parágrafos de metadados («Data: …», «Base: …»)
    # blocos de código, comentários HTML e linhas que acabam em «:».
    front, corpo = _front(texto)
    linhas = corpo.split("\n")
    titulo = next((ln[2:].strip() for ln in linhas if ln.startswith("# ") and ln[2:].strip()), None)
    resumo, reserva, meta, bloco = None, None, False, False
    for ln in linhas:
        s = ln.strip()
        if s.startswith("```"):
            bloco = not bloco
        elif bloco or s.startswith("<!--"):
            continue
        elif not s:
            meta = False
        elif meta or RESUMO_META.match(s.replace("**", "")):
            if not meta and reserva is None:
                reserva = _frase(s)
            meta = True
        elif not RESUMO_FORA.match(s) and not ln[0].isspace():   # indentada = continuação de lista
            frase = _frase(s)
            if not frase.endswith(":"):
                resumo = frase
                break
            reserva = reserva or frase   # «…:» só introduz uma lista
    # sem frase útil (relatórios só com «Fontes: …» e listas): a 1.ª linha de metadados ou de texto
    return titulo, resumo or reserva, front


def _frase(s: str) -> str:
    s = s.replace("**", "").rstrip()
    m = FRASE.match(s)
    return m.group(1) if m and len(m.group(1)) <= RESUMO_MAX else s


def _humano(nome: str) -> str:
    s = DATA.sub("", nome).replace("-", " ").strip() or nome
    return s[:1].upper() + s[1:]


def _url(tipo: str, chave: str, mtime: float) -> str:
    return f"api/biblioteca/{tipo}/" + "/".join(quote(s, safe="") for s in chave.split("/")) + f"?v={int(mtime)}"


def _chave(f: Ficheiro) -> str:
    return f"{f.raiz}/{f.rel}"


def _nome(f: Ficheiro) -> str:
    return f.rel.rsplit("/", 1)[-1]


def _grande(f: Ficheiro) -> bool:
    return f.tipo == "doc" and f.size > TEXTO_MAX


# --- leitura segura -----------------------------------------------------------------------------------------------

def _abrir(f: Ficheiro) -> int | None:
    # O_NOFOLLOW + fstat: ficheiro regular com dev/inode/tamanho iguais ao índice (symlink trocado → None)
    try:
        fd = os.open(f.abs, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino, st.st_size) == (f.dev, f.ino, f.size):
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


def _ler_fd(fd: int, n: int) -> bytes:
    partes, falta = [], n
    while falta > 0:
        b = os.read(fd, min(BLOCO, falta))
        if not b:
            break
        partes.append(b)
        falta -= len(b)
    return b"".join(partes)


def _ler_inicio(caminho: str, n: int) -> bytes | None:
    try:
        fd = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        return _ler_fd(fd, n) if stat.S_ISREG(os.fstat(fd).st_mode) else None
    except OSError:
        return None
    finally:
        os.close(fd)


def _texto(f: Ficheiro) -> bytes | None:
    # texto mascarado (≤ TEXTO_MAX), lido pelo fd validado
    fd = _abrir(f)
    if fd is None:
        return None
    try:
        raw = _ler_fd(fd, f.size)
    except OSError:
        return None
    finally:
        os.close(fd)
    return reader.mask(raw.decode("utf-8", "replace")).encode()


# --- varrimento ---------------------------------------------------------------------------------------------------

def _ficheiro(raiz: str, partes, caminho: str, st) -> Ficheiro | None:
    tipo = classificar(raiz, partes)
    if tipo is None or not stat.S_ISREG(st.st_mode):
        return None
    return Ficheiro(raiz, "/".join(partes), caminho, tipo, partes[-1].lower().rpartition(".")[2], st.st_size,
                    st.st_mtime, st.st_mtime_ns, st.st_dev, st.st_ino)


def varrer(raiz: str, root: Path) -> list[Ficheiro]:
    # S3: scandir sem seguir symlinks (ficheiros e pastas symlink ignorados), profundidade ≤ PROF_MAX
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
                print(f"biblioteca: {raiz} com mais de {ENTRADAS_MAX} entradas, varrimento cortado", file=sys.stderr)
                return out
            if not _componente_ok(e.name) or _negado(e.name):
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
            f = _ficheiro(raiz, p, e.path, st)
            if f:
                out.append(f)
    return out


def _lstat_ficheiro(raiz: str, partes, caminho: Path) -> Ficheiro | None:
    try:
        return _ficheiro(raiz, partes, str(caminho), os.lstat(caminho))
    except OSError:
        return None


def _scandir(d: Path) -> list:
    try:
        with os.scandir(d) as it:
            return sorted(it, key=lambda e: e.name)
    except OSError:
        return []


def _meta(caminho: Path) -> dict:
    raw = _ler_inicio(str(caminho), META_MAX + 1)
    if not raw or len(raw) > META_MAX:
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


# --- índice -------------------------------------------------------------------------------------------------------

class Indice:
    def __init__(self, raizes: dict[str, Path], tasks: Path, aprovacoes_dirs: dict[str, Path], clock=time.time):
        self.raizes, self.tasks, self.aprovacoes, self.clock = raizes, Path(tasks), aprovacoes_dirs, clock
        self.scans = 0
        self._lock = threading.Lock()
        self._snap: Snapshot | None = None
        self._cab: dict = {}   # (abs, mtime_ns, size, completo) → (titulo, resumo, front, citações)

    def get(self) -> Snapshot:
        with self._lock:
            agora = self.clock()
            if self._snap is None or self._snap.gerado + TTL_S < agora:
                try:
                    self._snap = self._construir(agora)
                except Exception as e:   # um ficheiro estranho não deita a Biblioteca abaixo nem re-varre a cada pedido
                    print(f"biblioteca: varrimento falhou ({e!r}); fica o índice anterior até {TTL_S} s",
                          file=sys.stderr)
                    self._snap = (self._snap or _snapshot(agora, [], {}, {}, {}))._replace(gerado=agora)
                self.scans += 1
            return self._snap

    # cabeçalhos dos .md (primeiros CAB_BYTES), em cache por (caminho, mtime, tamanho)
    def _cab_de(self, f: Ficheiro, novo: dict, completo: bool = False):
        k = (f.abs, f.mtime_ns, f.size, completo)
        if k not in self._cab:
            raw = _ler_inicio(f.abs, TEXTO_MAX if completo else CAB_BYTES) if f.ext == "md" else None
            texto = raw.decode("utf-8", "replace") if raw else ""
            self._cab[k] = (*cabecalho(texto), citacoes(texto) if completo else [])
        novo[k] = self._cab[k]
        return self._cab[k]

    def _pedidos(self) -> tuple[list[dict], list[Ficheiro]]:
        out, fich = [], []
        for fonte, root in self.aprovacoes.items():
            raiz = f"aprovacoes-{fonte}"
            for e in _scandir(root):
                nome = e.name
                if not nome.endswith(".md") or classificar(raiz, (nome,)) != "doc":
                    continue
                lido = aprovacoes._ler(root, nome)
                if lido is None:
                    continue
                raw, st = lido
                try:
                    texto = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                p = aprovacoes.parse_pedido(texto, nome)
                if p is None:
                    continue
                f = _ficheiro(raiz, (nome,), str(root / nome), st)
                fich.append(f)
                out.append({"fonte": fonte, "ficheiro": nome, "titulo": _corta(p["titulo"], TITULO_MAX),
                            "estado": estado_pedido(p["estado"]), "criado": reader.mask(p["criado"]),
                            "produto": _front(texto)[0].get("produto", ""), "citacoes": citacoes(texto),
                            "url": _url("f", _chave(f), f.mtime)})
        out.sort(key=lambda p: (p["criado"], p["ficheiro"], p["fonte"]))   # o último é o mais recente
        return out, fich

    def _tarefas(self) -> tuple[list[dict], list[Ficheiro]]:
        grupos, fich = [], []
        for e in _scandir(self.tasks):
            nome = e.name
            try:
                if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if not _componente_ok(nome) or _negado(nome) or nome.startswith("decisoes"):
                continue
            if nome == "inbox":
                for i in _scandir(e.path):
                    if i.name.endswith(".md"):
                        f = _lstat_ficheiro("tarefas", ("inbox", i.name), Path(i.path))
                        if f and f.ext == "md":
                            fich.append(f)
                            grupos.append({"genero": "plano", "id": _chave(f), "docs": [f]})
                continue
            docs = [f for d in TAREFA_DOCS if (f := _lstat_ficheiro("tarefas", (nome, d), Path(e.path) / d))]
            if not docs:
                continue
            fich += docs
            meta = _meta(Path(e.path) / "meta.json")
            grupos.append({"genero": "tarefa", "id": f"tarefas/{nome}", "docs": docs, "meta": meta})
        return grupos, fich

    def _construir(self, agora: float) -> Snapshot:
        novo_cab: dict = {}
        ficheiros: dict[str, Ficheiro] = {}
        todos: list[Ficheiro] = []
        for raiz, root in self.raizes.items():
            todos += varrer(raiz, root)
        pedidos, fich_ped = self._pedidos()
        tarefas, fich_tar = self._tarefas()
        for f in todos + fich_ped + fich_tar:
            ficheiros[_chave(f)] = f
        por_pasta: dict[str, list[Ficheiro]] = {}
        for f in todos:
            por_pasta.setdefault(f"{f.raiz}/{f.rel.rpartition('/')[0]}".rstrip("/"), []).append(f)

        # ligações: pedido mais recente por ficheiro citado (com as linhas que o citam) e pedidos por caminho citado
        cit_ficheiro: dict[str, tuple[dict, list[str]]] = {}
        for p in pedidos:   # do mais antigo para o mais recente: o último ganha
            linhas: dict[str, list[str]] = {}
            for linha, c in p["citacoes"]:
                linhas.setdefault(c, []).append(linha)
            for c, ls in linhas.items():
                cit_ficheiro[c] = (p, ls)

        def poster(f: Ficheiro) -> str | None:
            pasta = f"{f.raiz}/{f.rel.rpartition('/')[0]}".rstrip("/")
            pasta += "/" + _nome(f).rpartition(".")[0] + "-fotogramas"
            pngs = sorted((g for g in por_pasta.get(pasta, []) if g.ext == "png" and _nome(g) != "folha.png"),
                          key=_nome)
            return _url("mini", _chave(pngs[0]), pngs[0].mtime) if pngs else None

        grupos: list[dict] = []
        escondidos_fora: list[Ficheiro] = []
        lotes: dict[str, dict] = {}
        pastas: dict[str, dict] = {}
        docs: dict[tuple, list[Ficheiro]] = {}
        for f in todos:
            partes = f.rel.split("/")
            if f.raiz in RAIZES_MEDIA and partes[0] == "media" and len(partes) >= 3:
                lid = f"{f.raiz}/media/{partes[1]}"
                lotes.setdefault(lid, {"genero": "lote", "id": lid, "nome": partes[1], "fs": []})["fs"].append(f)
            elif f.tipo in MEDIA:
                if any(_escondida(c) for c in partes[:-1]):
                    escondidos_fora.append(f)
                    continue
                pid = f"{f.raiz}/{f.rel.rpartition('/')[0]}".rstrip("/")
                pastas.setdefault(pid, {"genero": "pasta", "id": pid, "nome": pid.rpartition("/")[2],
                                        "fs": []})["fs"].append(f)
            else:
                docs.setdefault((f.raiz, f.rel.rpartition("/")[0], raiz_nome(_nome(f))), []).append(f)
        for f in escondidos_fora:   # media em pastas escondidas fora de media/: trabalho da pasta-mãe, se existir
            partes = f.rel.split("/")
            i = next(i for i, c in enumerate(partes) if _escondida(c))
            pid = "/".join([f.raiz, *partes[:i]])
            if pid in pastas:
                pastas[pid].setdefault("extra", []).append(f)

        ficheiro_item: dict[str, str] = {}
        for (raiz, pasta, _), fs in sorted(docs.items()):
            # principal: o sem sufixo («x.md»), senão o de mtime mais antiga
            fs.sort(key=lambda f: (raiz_nome(_nome(f)) + ".md" != _nome(f), f.mtime_ns, _nome(f)))
            grupos.append({"genero": "documento", "id": _chave(fs[0]), "docs": fs, "pasta": pasta})
        grupos += tarefas
        grupos += [lotes[k] for k in sorted(lotes)] + [pastas[k] for k in sorted(pastas)]

        itens, por_id = [], {}
        for g in grupos:
            it = self._item(g, ficheiros, cit_ficheiro, pedidos, poster, agora, novo_cab)
            if it is None:
                continue
            por_id[g["id"]] = it
            for chave in it["donos"]:   # documentos ligados não são donos do lote: o deep link abre o documento
                ficheiro_item.setdefault(chave, g["id"])
            itens.append(it["publico"])
        self._cab = novo_cab
        itens.sort(key=lambda i: (-i["actualizado"], i["id"]))
        return _snapshot(agora, itens, por_id, ficheiros, ficheiro_item)

    def _item(self, g, ficheiros, cit_ficheiro, pedidos, poster, agora, novo_cab) -> dict | None:
        gid, genero = g["id"], g["genero"]
        raiz = gid.split("/", 1)[0]
        destaque, textos, trabalho = [], [], {}
        ligados_ped: list[dict] = []
        titulo = resumo = None
        front: dict = {}
        if genero in ("lote", "pasta"):
            base = gid.partition("/")[2]   # relativo à raiz ("" numa pasta na raiz da fonte)
            fs = sorted(g["fs"], key=lambda f: f.rel)
            rel = [_relativo(f, base).split("/") for f in fs] if genero == "lote" else [[_nome(f)] for f in fs]
            tem_final = genero == "lote" and any(p[0] == "final" and len(p) > 1 for p in rel)
            folha = None
            for f, p in zip(fs, rel):
                escondido = any(_escondida(c) for c in p[:-1]) or p[-1].endswith(".prompt.md")
                if f.tipo in MEDIA and not escondido and (len(p) == 2 and p[0] == "final" if tem_final else True):
                    destaque.append(f)
                elif len(p) == 1 and f.tipo not in MEDIA and not escondido:
                    textos.append((f, False))
                    if p[0] in ("folha.md", "README.md") and (folha is None or p[0] == "folha.md"):
                        folha = f
                else:
                    pasta = "/".join(p[:2]) if p[0] == "final" and len(p) > 2 else (p[0] if len(p) > 1 else "")
                    trabalho.setdefault(pasta, []).append(f)
            for f in g.get("extra", []):
                trabalho.setdefault(_relativo(f, base).split("/")[0], []).append(f)
            destaque.sort(key=_nome)
            # pedidos ligados: lote → algum caminho citado começa por <id>/; pasta → algum ficheiro do destaque citado
            prefixo = gid + "/"
            for p in pedidos:
                cs = [c for _, c in p["citacoes"]]
                if genero == "lote" and any(c == gid or c.startswith(prefixo) for c in cs):
                    ligados_ped.append(p)
                elif genero == "pasta" and any(_chave(f) in cs for f in destaque):
                    ligados_ped.append(p)
            # textos ligados: documentos de fontes media fora de media/ citados na folha ou nos pedidos ligados
            cit = list(self._cab_de(folha, novo_cab, completo=True)[3]) if folha else []
            for p in ligados_ped:
                cit += p["citacoes"]
            vistos = {_chave(f) for f, _ in textos}
            for _, c in cit:
                f = ficheiros.get(c)
                if f and f.raiz in RAIZES_MEDIA and not f.rel.startswith("media/") and f.tipo not in MEDIA \
                        and c not in vistos:
                    vistos.add(c)
                    textos.append((f, True))
            if folha:
                titulo = self._cab_de(folha, novo_cab, completo=True)[0]
                titulo = FOLHA.sub("", titulo).strip() if titulo else None
            titulo = titulo or _humano(g["nome"])
            sub = g["nome"] if genero == "lote" else gid
        else:
            fs = g["docs"]
            textos = [(f, False) for f in fs]
            principal = fs[0]
            if principal.ext == "md":
                titulo, resumo, front, _ = self._cab_de(principal, novo_cab)
            if genero == "tarefa":
                titulo = titulo if principal.rel.endswith("/SPEC.md") else None
                task = g["meta"].get("task")
                if not titulo and isinstance(task, str):
                    titulo = next((ln[2:].strip() for ln in task.split("\n") if ln.startswith("# ") and ln[2:].strip()),
                                  None)
                titulo, resumo, sub = titulo or gid.split("/", 1)[1], None, gid.split("/", 1)[1]
            else:
                titulo = titulo or _nome(principal)
                sub = f"{raiz}/{g.get('pasta', '')}".rstrip("/") if genero == "documento" else "tarefas/inbox"
            cs = {_chave(f) for f in fs}
            ligados_ped = [p for p in pedidos if any(c in cs for _, c in p["citacoes"])]
        titulo = _corta(titulo, TITULO_MAX)

        # estado por ficheiro do destaque (lotes/pastas com pedido)
        pedido = ligados_ped[-1] if ligados_ped else None
        estado = pedido["estado"] if pedido and genero in ("lote", "pasta") else None
        estados = {}
        for f in destaque:
            c = cit_ficheiro.get(_chave(f))
            if c and c[0] in ligados_ped:
                est = "bloqueado" if any("bloquead" in ln.casefold() for ln in c[1]) else c[0]["estado"]
            else:
                est = "sem_aprovacao" if estado else None
            estados[_chave(f)] = est
        resumo_estado = None
        if pedido and genero in ("lote", "pasta"):
            resumo_estado = {"aprovado": 0, "bloqueado": 0, "sem_aprovacao": 0}
            resumo_estado.update(Counter(e for e in estados.values() if e))

        # produto (I4)
        prods = produtos_valor(pedido["produto"]) if pedido and pedido["produto"] else []
        if not prods and genero == "tarefa" and isinstance(g["meta"].get("project"), str):
            prods = [produto_projecto(g["meta"]["project"])]
        if not prods and front.get("produto"):
            prods = produtos_valor(front["produto"])
        if not prods:
            prods = produtos_kw(f"{gid.partition('/')[2]} {titulo}")   # caminho relativo à raiz + título
        prods = prods or ["geral"]

        # tipos (I3)
        tipos = []
        for f in destaque:
            t = TIPO_MEDIA[f.tipo]
            if t not in tipos:
                tipos.append(t)
        if genero in ("tarefa", "plano"):
            tipos = ["planos"]
        elif genero == "documento":
            if any(re.search("juridico|parecer", _nome(f).casefold()) for f, _ in textos):
                tipos.append("pareceres")
            if raiz in RAIZES_MEDIA:
                if not re.search("juridico|parecer", _nome(textos[0][0]).casefold()):
                    tipos.insert(0, "textos")
            elif TIPO_RAIZ.get(raiz) and TIPO_RAIZ[raiz] not in tipos:
                tipos.insert(0, TIPO_RAIZ[raiz])
        if not tipos:
            tipos = ["textos"]

        visiveis = destaque + [f for f, _ in textos]
        todos = visiveis or [f for fs in trabalho.values() for f in fs]
        if not todos:
            return None
        # datas («Novo», ordem): só o que é do item; documentos ligados (de outras pastas) não contam
        proprios = destaque + [f for f, lig in textos if not lig] or [f for fs in trabalho.values() for f in fs]
        mts = [f.mtime for f in proprios or todos]
        conta = {"imagem": 0, "video": 0, "audio": 0, "doc": 0}
        for f in visiveis:
            conta["doc" if f.tipo == "pdf" else f.tipo] += 1

        # miniaturas do cartão: imagens do destaque por ordem, senão o poster de um vídeo
        minis = [_url("mini", _chave(f), f.mtime) for f in destaque if f.tipo == "imagem"][:4]
        if not minis:
            minis = [p for p in (poster(f) for f in destaque if f.tipo == "video") if p][:1]

        # zip: destaque (caminho relativo ao lote, ou o nome) + textos não grandes (textos/<nome>; sem destaque, o nome)
        zip_fs, usados = [], set()
        for f in destaque:
            arc = _relativo(f, gid.partition("/")[2]) if genero == "lote" else _nome(f)
            zip_fs.append((f, arc))
            usados.add(arc)
        for f, _ in textos:
            if _grande(f):
                continue
            base_arc = ("textos/" if destaque else "") + _nome(f)
            arc, i = base_arc, 2
            while arc in usados:
                arc = ("textos/" if destaque else "") + f"{i}-{_nome(f)}"
                i += 1
            usados.add(arc)
            zip_fs.append((f, arc))
        zbytes = sum(f.size for f, _ in zip_fs)
        zip_info = ({"n": len(zip_fs), "bytes": zbytes}
                    if 2 <= len(zip_fs) <= ZIP_MAX_N and zbytes <= ZIP_MAX_BYTES else None)

        principal = None
        if genero in ("documento", "plano", "tarefa"):
            f0 = textos[0][0]
            u = _url("f", _chave(f0), f0.mtime)
            principal = {"url": u, "dl": u + "&dl=1", "ext": f0.ext, "grande": _grande(f0)}

        def entrada(f: Ficheiro, mini=None, est=None) -> dict:
            u = _url("f", _chave(f), f.mtime)
            return {"caminho": _chave(f), "nome": _nome(f), "ext": f.ext, "tipo": f.tipo, "bytes": f.size,
                    "mtime": int(f.mtime), "url": u, "dl": u + "&dl=1", "mini": mini, "estado": est,
                    "grande": _grande(f)}

        def mini_de(f: Ficheiro):
            if f.tipo == "imagem":
                return _url("mini", _chave(f), f.mtime)
            return poster(f) if f.tipo == "video" else None

        nomes_q = " ".join(_nome(f) for f in visiveis)
        publico = {
            "id": gid, "raiz": raiz, "genero": genero, "titulo": titulo, "sub": reader.mask(sub),
            "tipos": tipos, "produtos": prods, "actualizado": int(max(mts)), "criado": int(min(mts)),
            "novo": min(mts) >= agora - NOVO_S, "estado": estado, "resumo_estado": resumo_estado,
            "pedido": ({"fonte": pedido["fonte"], "ficheiro": pedido["ficheiro"], "url": pedido["url"]}
                       if pedido else None),
            "minis": minis, "mais": max(0, len(destaque) - len(minis)), "conta": conta, "n": len(visiveis),
            "bytes": sum(f.size for f in visiveis),
            "resumo": _corta(resumo, RESUMO_MAX) if resumo and genero in ("documento", "plano") else None,
            "principal": principal, "zip": zip_info,
            "q": normaliza(f"{titulo} {sub} {nomes_q}"),
        }
        folha_dir = genero == "lote"
        detalhe = {
            "destaque": [entrada(f, mini_de(f), estados.get(_chave(f))) for f in destaque],
            "textos": [{**entrada(f), "rotulo": rotulo(_nome(f), folha_dir and not lig), "ligado": lig}
                       for f, lig in textos],
            "trabalho": [{"pasta": k, "ficheiros": [entrada(f, mini_de(f)) for f in sorted(v, key=lambda f: f.rel)]}
                         for k, v in sorted(trabalho.items())],
            "pedidos": [{"fonte": p["fonte"], "ficheiro": p["ficheiro"], "titulo": p["titulo"], "estado": p["estado"],
                         "criado": p["criado"], "url": p["url"]} for p in reversed(ligados_ped)],
        }
        donos = [_chave(f) for f in destaque] + [_chave(f) for f, lig in textos if not lig] + \
                [_chave(f) for v in trabalho.values() for f in v]
        return {"publico": publico, "detalhe": detalhe, "zip": zip_fs, "donos": donos}


def _snapshot(agora: float, itens, por_id, ficheiros, ficheiro_item) -> Snapshot:
    corpo = json.dumps({"gerado": int(agora), "itens": itens}, ensure_ascii=False, separators=(",", ":")).encode()
    return Snapshot(agora, itens, por_id, ficheiros, ficheiro_item, corpo, gzip.compress(corpo, 6, mtime=0))


def _relativo(f: Ficheiro, base: str) -> str:
    return f.rel[len(base) + 1:] if base else f.rel


def _escondida(c: str) -> bool:
    return c == "prompts" or c.endswith(("-semelhanca", "-fotogramas"))


# --- HTTP ---------------------------------------------------------------------------------------------------------

def _404(h, metodo: str = "GET") -> None:
    if metodo == "HEAD":
        h.send_response(404)
        h.send_header("Content-Type", "application/json; charset=utf-8")
        h.send_header("Content-Length", str(len(NAO_ENCONTRADO)))
        h.end_headers()
        return
    h._json(404, NAO_ENCONTRADO)


def _405(h) -> None:
    # resposta a HEAD: cabeçalhos do JSON de erro, sem corpo
    corpo = json.dumps({"error": "método não permitido, usa GET"}, ensure_ascii=False).encode()
    h.send_response(405)
    h.send_header("Allow", "GET")
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(corpo)))
    h.end_headers()


def _erro(h, code: int, msg: str) -> None:
    h._json(code, json.dumps({"error": msg}, ensure_ascii=False).encode())


def _resolver(snap: Snapshot, resto: str) -> Ficheiro | None:
    # S4: descodificar uma vez; componentes vazios, ., .., ocultos, \ ou NUL → None; só o que está no índice
    try:
        rel = unquote(resto, errors="strict")
    except UnicodeDecodeError:
        return None
    partes = rel.split("/")
    if len(partes) < 2 or any(not _componente_ok(c) for c in partes):
        return None
    f = snap.ficheiros.get(rel)
    return None if f is None or _grande(f) else f


def _disposicao(nome: str, anexo: bool) -> str:
    ascii_ = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in nome)
    return f"{'attachment' if anexo else 'inline'}; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nome, safe='')}"


def _escrever(h, dados) -> bool:
    try:
        h.wfile.write(dados)
        return True
    except (ConnectionError, OSError):
        return False


def _servir(h, f: Ficheiro, metodo: str, anexo: bool, cache: bool = True) -> None:
    # cache=False (URL sem ?v=<mtime>): no-store, senão um pedido/texto alterado ficava 1 h antigo no browser
    fd = _abrir(f)
    if fd is None:
        _404(h, metodo)
        return
    try:
        cab = {"Content-Type": CONTENT_TYPE[f.ext], "Content-Disposition": _disposicao(_nome(f), anexo)}
        if f.ext != "pdf":
            cab["Content-Security-Policy"] = CSP
        if f.tipo == "doc":   # S6: mascarado, sempre 200, sem Range
            try:
                corpo = reader.mask(_ler_fd(fd, f.size).decode("utf-8", "replace")).encode()
            except OSError:
                _404(h, metodo)
                return
            h._cc = CC_FICHEIRO if cache else "no-store"
            h.send_response(200)
            for k, v in cab.items():
                h.send_header(k, v)
            h.send_header("Content-Length", str(len(corpo)))
            h.end_headers()
            if metodo == "GET":
                _escrever(h, corpo)
            return
        r = intervalo(h.headers.get("Range"), f.size)
        if r == "416":
            h.send_response(416)
            h.send_header("Content-Range", f"bytes */{f.size}")
            h.send_header("Content-Length", "0")
            h.end_headers()
            return
        a, b = r if r else (0, f.size - 1)
        h._cc = CC_FICHEIRO if cache else "no-store"
        h.send_response(206 if r else 200)
        for k, v in cab.items():
            h.send_header(k, v)
        h.send_header("Accept-Ranges", "bytes")
        if r:
            h.send_header("Content-Range", f"bytes {a}-{b}/{f.size}")
        h.send_header("Content-Length", str(max(0, b - a + 1)))
        h.end_headers()
        if metodo != "GET":
            return
        os.lseek(fd, a, os.SEEK_SET)
        falta = b - a + 1
        while falta > 0:
            bloco = os.read(fd, min(BLOCO, falta))
            if not bloco or not _escrever(h, bloco):
                return
            falta -= len(bloco)
    finally:
        os.close(fd)


# miniaturas: falhas em memória (não se repetem até o ficheiro mudar) e soma da cache
_mini = {"falhas": set(), "grandes": set(), "total": None}
_mini_lock = threading.Lock()


def _mini_chave(f: Ficheiro) -> str:
    return hashlib.sha256(f"{f.raiz}/{f.rel}|{f.mtime_ns}|{f.size}".encode()).hexdigest()


def _mini_cache(chave: str) -> bytes | None:
    raw = _ler_inicio(str(MINI_DIR / f"{chave}.jpg"), MINI_CACHE_MAX)
    return raw or None


class _Grande(Exception):
    """Imagem com mais de MINI_MAX_PX píxeis: a miniatura dá 404 em vez de cair para o original (o browser também o
    teria de descodificar na grelha); o original continua disponível em /f/ para quem o abrir."""


def _mini_gerar(f: Ficheiro) -> bytes | None:
    fd = _abrir(f)
    if fd is None:
        return None
    with os.fdopen(fd, "rb") as fp, warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)   # só neste bloco: aviso de bomba → excepção
        try:
            im = Image.open(fp, formats=MINI_FORMATOS)   # só lê o cabeçalho
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
            raise _Grande from e
        if im.width * im.height > MINI_MAX_PX:
            raise _Grande
        im.draft("RGB", (MINI_W * 2, MINI_H * 2))
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((MINI_W, MINI_H))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=MINI_Q, optimize=True)
    return buf.getvalue()


def _mini_guardar(chave: str, dados: bytes) -> None:
    # escrita atómica em MINI_DIR (a única do módulo); sem escrita → só não fica em cache
    tmp = MINI_DIR / f".{chave}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        return
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(dados)
        os.replace(tmp, MINI_DIR / f"{chave}.jpg")
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return
    with _mini_lock:
        if _mini["total"] is None:
            _mini["total"] = sum(s for _, _, s in _mini_lista())
        else:
            _mini["total"] += len(dados)
        if _mini["total"] > MINI_CACHE_MAX:   # LRU aproximado: as mais antigas por mtime até MINI_CACHE_ALVO
            total = _mini["total"]
            for caminho, _, s in sorted(_mini_lista(), key=lambda x: x[1]):
                if total <= MINI_CACHE_ALVO:
                    break
                try:
                    os.unlink(caminho)
                    total -= s
                except OSError:
                    pass
            _mini["total"] = total


def _mini_lista() -> list[tuple[str, float, int]]:
    out = []
    for e in _scandir(MINI_DIR):
        try:
            if e.name.endswith(".jpg") and e.is_file(follow_symlinks=False):
                st = e.stat(follow_symlinks=False)
                out.append((e.path, st.st_mtime, st.st_size))
        except OSError:
            pass
    return out


def _servir_mini(h, f: Ficheiro) -> None:
    chave = _mini_chave(f)
    if chave in _mini["grandes"]:
        _404(h)
        return
    if Image is None or chave in _mini["falhas"]:
        _servir(h, f, "GET", False)
        return
    dados = _mini_cache(chave)
    if dados is None:
        if not MINI_SLOTS.acquire(timeout=MINI_ESPERA_S):
            _servir(h, f, "GET", False)
            return
        try:
            dados = _mini_cache(chave)
            if dados is None:
                try:
                    dados = _mini_gerar(f)
                except _Grande:
                    _mini["grandes"].add(chave)
                    _404(h)
                    return
                except Exception:   # imagem estragada, bomba de descompressão, formato desconhecido
                    dados = None
                if dados is None:
                    _mini["falhas"].add(chave)
                    _servir(h, f, "GET", False)
                    return
                _mini_guardar(chave, dados)
        finally:
            MINI_SLOTS.release()
    h._cc = CC_FICHEIRO
    h.send_response(200)
    h.send_header("Content-Type", "image/jpeg")
    h.send_header("Content-Security-Policy", CSP)
    h.send_header("Content-Length", str(len(dados)))
    h.end_headers()
    _escrever(h, dados)


class _Sink(io.RawIOBase):
    # wfile sem seek/tell: o zipfile escreve com data descriptors. Depois da 1.ª falha (cliente fechou ou timeout)
    # falha logo, para o fecho do zipfile não esperar outro timeout por cada escrita que ainda faz
    def __init__(self, wfile):
        self._w = wfile
        self._morto = False

    def writable(self):
        return True

    def write(self, b):
        if self._morto:
            raise ConnectionError("ligação já fechada")
        try:
            self._w.write(b)
        except OSError:
            self._morto = True
            raise
        return len(b)


def escrever_zip(destino, entradas) -> None:
    # entradas: [(Ficheiro, arcname)]; textos mascarados; ficheiro que falha a validação a meio → salta-se
    with zipfile.ZipFile(_Sink(destino), "w", zipfile.ZIP_STORED) as zf:
        for f, arc in entradas:
            if f.tipo == "doc":
                dados = _texto(f)
                if dados is None:
                    continue
                zf.writestr(_zinfo(arc, f), dados)
                continue
            fd = _abrir(f)
            if fd is None:
                continue
            try:
                with zf.open(_zinfo(arc, f), "w") as out:
                    while bloco := os.read(fd, BLOCO):
                        out.write(bloco)
            finally:
                os.close(fd)


def _zinfo(arc: str, f: Ficheiro) -> zipfile.ZipInfo:
    dt = time.localtime(max(f.mtime, 315532800))[:6]   # zip não guarda datas antes de 1980
    zi = zipfile.ZipInfo(arc, date_time=dt)
    zi.compress_type = zipfile.ZIP_STORED
    zi.external_attr = 0o644 << 16
    return zi


def _servir_zip(h, snap: Snapshot, query: str) -> None:
    ids = parse_qs(query).get("id")
    it = snap.por_id.get(ids[0]) if ids else None
    if it is None or len(it["zip"]) < 2:
        _404(h)
        return
    if len(it["zip"]) > ZIP_MAX_N or sum(f.size for f, _ in it["zip"]) > ZIP_MAX_BYTES:
        _erro(h, 413, "grande demais, descarrega um a um")
        return
    if "1" in parse_qs(query).get("verificar", ()):   # pré-verificação do browser: diz se há vaga sem a ocupar
        if ZIP_SLOTS.acquire(blocking=False):
            ZIP_SLOTS.release()
            h.send_response(204)
            h.end_headers()
        else:
            _erro(h, 503, "já há downloads a decorrer, tenta daqui a pouco")
        return
    if not ZIP_SLOTS.acquire(timeout=ZIP_ESPERA_S):
        _erro(h, 503, "já há downloads a decorrer, tenta daqui a pouco")
        return
    try:
        nome = it["publico"]["id"].rsplit("/", 1)[-1]
        nome = (nome.rpartition(".")[0] or nome) + ".zip"
        h.send_response(200)
        h.send_header("Content-Type", "application/zip")
        h.send_header("Content-Disposition", _disposicao(nome, True))
        h.send_header("Content-Security-Policy", CSP)
        h.end_headers()
        antes = h.connection.gettimeout()
        h.connection.settimeout(ZIP_TIMEOUT_S)
        try:
            escrever_zip(h.wfile, it["zip"])
        except (ConnectionError, OSError, ValueError):   # cliente fechou a ligação a meio ou deixou de ler
            h.close_connection = True
        finally:
            h.connection.settimeout(antes)
    finally:
        ZIP_SLOTS.release()


def handle(h, metodo: str, path: str, query: str, indice: Indice) -> None:
    # router de /api/biblioteca*; h é o Handler do server.py (usa _json, wfile, headers e _cc para o Cache-Control)
    try:
        snap = indice.get()
        if path.startswith("/api/biblioteca/f/"):
            f = _resolver(snap, path[len("/api/biblioteca/f/"):])
            if f is None:
                _404(h, metodo)
            else:
                _servir(h, f, metodo, "dl=1" in query.split("&"), cache="v" in parse_qs(query))
            return
        if metodo != "GET":   # HEAD: só /f/ (acima); as rotas JSON/miniatura/zip dizem 405, o resto 404
            rotas = ("/api/biblioteca", "/api/biblioteca/item", "/api/biblioteca/zip")
            if path in rotas or path.startswith("/api/biblioteca/mini/"):
                _405(h)
            else:
                _404(h, metodo)
        elif path == "/api/biblioteca":
            _servir_indice(h, snap)
        elif path == "/api/biblioteca/item":
            _servir_item(h, snap, query)
        elif path.startswith("/api/biblioteca/mini/"):
            f = _resolver(snap, path[len("/api/biblioteca/mini/"):])
            if f is None or f.tipo != "imagem":
                _404(h)
            else:
                _servir_mini(h, f)
        elif path == "/api/biblioteca/zip":
            _servir_zip(h, snap, query)
        else:
            _404(h)
    finally:
        h._cc = "no-store"


def _servir_indice(h, snap: Snapshot) -> None:
    # ~600 KB com os dados reais (≈ 80 KB em gzip): comprimido uma vez por varrimento, só se o browser aceitar
    if "gzip" not in h.headers.get("Accept-Encoding", ""):
        h._json(200, snap.corpo_json)
        return
    h.send_response(200)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Encoding", "gzip")
    h.send_header("Vary", "Accept-Encoding")
    h.send_header("Content-Length", str(len(snap.corpo_gz)))
    h.end_headers()
    _escrever(h, snap.corpo_gz)


def _servir_item(h, snap: Snapshot, query: str) -> None:
    ids = parse_qs(query).get("id")
    pedido = ids[0] if ids else ""
    foco = None
    it = snap.por_id.get(pedido)
    if it is None and pedido in snap.ficheiro_item:
        it, foco = snap.por_id[snap.ficheiro_item[pedido]], pedido
    if it is None:
        _404(h)
        return
    h._json(200, json.dumps({"item": it["publico"], "foco": foco, **it["detalhe"]}, ensure_ascii=False).encode())
