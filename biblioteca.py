"""Biblioteca: material das squads (media e documentos) servido a partir de pastas só de leitura.

Índice em memória (instantâneo imutável com ETag; passado o TTL_S é refeito numa thread, um varrimento de cada vez, e os
pedidos respondem com o anterior enquanto isso; só o 1.º arranque espera) com as regras S1–S4:
allowlist de extensões, denylist de nomes/pastas, sem symlinks, e só se serve o que está no índice (aberto com
O_NOFOLLOW e confirmado com fstat: dev, inode e tamanho iguais ao índice). Textos saem mascarados (reader.mask),
≤ TEXTO_MAX.
Única escrita: miniaturas em MINI_DIR (tmpfs), só com Pillow (import opcional; sem ele serve-se o original).
Arquivo (ADR-011): um 2.º Indice(arquivo=True) varre as `<fonte>/.arquivo/` só quando é pedido («Ver arquivados», links
antigos); os pedidos de arquivar/repor vão para a fila (arquivo.escrever_pedido); quem move é o runner, nunca o Office.
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
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

import aprovacoes
import arquivo
import reader

try:   # opcional: só na imagem Docker
    from PIL import Image, ImageOps
except ImportError:   # pragma: no cover - depende do ambiente
    Image = ImageOps = None

# preenchidos por configurar(cfg): ids das fontes e as que aceitam media (lotes em <fonte>/media/<lote>/)
RAIZES: tuple = ()
RAIZES_MEDIA: tuple = ()
ARQ = arquivo.DIR            # <fonte>/.arquivo/: pasta escondida, fora do índice principal
REGISTO_MAX = 4 * 1024 * 1024   # REGISTO.tsv maior: lê-se só a cauda (a última linha ganha)
# regras de caminho partilhadas com o runner (arquivo.py, ADR-010): os mesmos valores, numa só fonte
TIPOS = arquivo.TIPOS
MEDIA = arquivo.MEDIA
CONTENT_TYPE = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif",
                "mp4": "video/mp4", "m4v": "video/mp4", "webm": "video/webm", "mp3": "audio/mpeg", "wav": "audio/wav",
                "m4a": "audio/mp4", "ogg": "audio/ogg", "opus": "audio/ogg", "md": "text/plain; charset=utf-8",
                "txt": "text/plain; charset=utf-8", "csv": "text/plain; charset=utf-8", "pdf": "application/pdf"}
PASTAS_NEGADAS = arquivo.PASTAS_NEGADAS
SUF_NEGADOS = arquivo.SUF_NEGADOS
PALAVRAS_NEGADAS = arquivo.PALAVRAS_NEGADAS

TTL_S = 60
ARQ_ESPERA_S = 5   # /item e /zip de um link antigo: espera pela reconstrução do índice do arquivo velho
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
PROF_MAX = arquivo.PROF_MAX            # componentes do caminho relativo
ENTRADAS_MAX = arquivo.ENTRADAS_MAX    # por raiz (pasta errada montada)
META_MAX = 256 * 1024

NAO_ENCONTRADO = json.dumps({"error": "não encontrado"}, ensure_ascii=False).encode()
CC_FICHEIRO = "private, max-age=3600"
CSP = "default-src 'none'; sandbox"

Ficheiro = namedtuple("Ficheiro", "raiz rel abs tipo ext size mtime mtime_ns dev ino")
Snapshot = namedtuple("Snapshot", "gerado itens por_id ficheiros ficheiro_item corpo_json corpo_gz etag")

CITACAO = re.compile(r"(?!)")   # caminhos <fonte media>/… citados; compilado em configurar
SUFIXO = arquivo.SUFIXO
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
    CITACAO = arquivo.citacao_re(RAIZES_MEDIA)
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


_componente_ok = arquivo.componente_ok
_utf8 = arquivo._utf8
_negado = arquivo.negado
raiz_nome = arquivo.raiz_nome   # x.md, x.copy.md, x.md.revisao.md, x.revisao-2.md… → x


def classificar(raiz: str, partes) -> str | None:
    # S1 (allowlist) + S2 (denylist) do arquivo.py; media só nas fontes com media: true, o resto só documentos
    return arquivo.classificar(partes, raiz in RAIZES_MEDIA)


def citacoes(texto: str) -> list[tuple[str, str]]:
    # (linha, caminho) de cada caminho <fonte media>/… citado (também ../<fonte>/…, em crase, em ![](…), no frontmatter)
    return arquivo.citacoes(texto, CITACAO)


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


@lru_cache(maxsize=8192)
def _mascara_curta(s: str) -> str:
    # títulos, subtítulos e datas (curtos, quase sempre iguais de um varrimento para o outro): a máscara é pura
    return reader.mask(s)


def _corta(s: str, n: int) -> str:
    s = _mascara_curta(s) if len(s) <= 512 else reader.mask(s)   # texto comprido (resumo sem frase) fora da cache
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


def _ler_cauda(caminho: str, n: int) -> bytes | None:
    # os últimos n bytes (sem a 1.ª linha, se vier partida); lê-se mais 1 byte antes para saber se a linha começa ali
    try:
        fd = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_size <= n:
            return _ler_fd(fd, n)
        os.lseek(fd, st.st_size - n - 1, os.SEEK_SET)
        return _ler_fd(fd, n + 1).partition(b"\n")[2]
    except OSError:
        return None
    finally:
        os.close(fd)


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
    # S3 (arquivo.varrer): scandir sem seguir symlinks, profundidade ≤ PROF_MAX, ≤ ENTRADAS_MAX entradas
    return [Ficheiro(raiz, "/".join(p), caminho, tipo, p[-1].lower().rpartition(".")[2], st.st_size, st.st_mtime,
                     st.st_mtime_ns, st.st_dev, st.st_ino)
            for p, caminho, st, tipo in arquivo.varrer(root, raiz in RAIZES_MEDIA, raiz)]


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


def _pedido(fonte: str, raiz: str, root: Path, nome: str) -> tuple[dict, Ficheiro] | None:
    # lê (pelo mesmo fd validado do aprovacoes) e analisa um pedido; o dict não muda depois (partilhado entre índices)
    lido = aprovacoes._ler(root, nome)
    if lido is None:
        return None
    raw, st = lido
    try:
        texto = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    p = aprovacoes.parse_pedido(texto, nome)
    if p is None:
        return None
    f = _ficheiro(raiz, (nome,), str(root / nome), st)
    if f is None:
        return None
    return {"fonte": fonte, "ficheiro": nome, "titulo": _corta(p["titulo"], TITULO_MAX),
            "estado": estado_pedido(p["estado"]), "criado": reader.mask(p["criado"]),
            "produto": _front(texto)[0].get("produto", ""), "citacoes": citacoes(texto),
            "url": _url("f", _chave(f), f.mtime)}, f


# --- índice -------------------------------------------------------------------------------------------------------

class Indice:
    def __init__(self, raizes: dict[str, Path], tasks: Path, aprovacoes_dirs: dict[str, Path], clock=time.time,
                 arquivo: bool = False):
        # arquivo=True: o mesmo índice sobre as `<fonte>/.arquivo/` (mesmos ids e caminhos que antes de arquivar), sem
        # tarefas; os pedidos de aprovação vivos entram só para as ligações (um lote arquivado com o pedido ainda vivo)
        self.tasks, self.aprovacoes, self.clock, self.arquivo = Path(tasks), aprovacoes_dirs, clock, arquivo
        self.fontes = dict(raizes)   # raiz de cada fonte (no modo arquivo, a `.arquivo/` desce-se com O_NOFOLLOW)
        self.raizes = {r: Path(p) / ARQ for r, p in raizes.items()} if arquivo else raizes
        self._reg: dict = {}   # modo arquivo: fonte → (por item, por caminho) do REGISTO.tsv, refeito em cada varrimento
        self.scans = 0
        self._lock = threading.Lock()   # só quem reconstrói o segura; os pedidos nunca esperam por ele (excepto o 1.º)
        self._snap: Snapshot | None = None
        self._thread: threading.Thread | None = None
        self._cab: dict = {}   # (abs, mtime_ns, size, completo) → (titulo, resumo, front, citações)
        self._ped: dict = {}   # (pasta, nome, mtime_ns, size, dev, ino, mode) → (pedido, Ficheiro) | None

    def atual(self) -> Snapshot | None:
        # o instantâneo actual, sem lock nem reconstrução (/f/ e /mini/)
        return self._snap

    def get(self) -> Snapshot:
        # stale-while-revalidate: devolve sempre o instantâneo actual; passado o TTL_S, uma thread refá-lo
        snap = self._snap
        if snap is None:   # 1.º arranque: espera (e quem chegar entretanto espera pelo mesmo varrimento)
            with self._lock:
                if self._snap is None:
                    self._refazer()
                return self._snap
        if snap.gerado + TTL_S < self.clock():
            self._lancar()
        return snap

    def aquecer(self) -> None:
        # no arranque do servidor: o 1.º varrimento começa logo, em fundo (quem pedir a lista entretanto espera por ele)
        if self._snap is None:
            self._lancar()

    def direto(self, partes) -> Ficheiro | None:
        # /f/ e /mini/ sem índice (ou com ele velho): o mesmo que o varrimento aceitaria para este caminho, sem varrer.
        # O _abrir confirma depois dev/inode/tamanho contra este lstat (feito sem seguir symlinks)
        raiz, sub = partes[0], tuple(partes[1:])
        if not sub:
            return None
        if raiz in self.raizes:   # S1–S3: allowlist, denylist e profundidade, como no varrer()
            if classificar(raiz, sub) is None:
                return None
            pastas = (ARQ, *sub[:-1]) if self.arquivo else sub[:-1]
            r = _lstat_seguro(self.fontes[raiz], pastas, sub[-1])
            return _ficheiro(raiz, sub, *r) if r else None
        if raiz == "tarefas" and len(sub) == 2 and not self.arquivo:   # como o _tarefas()
            pasta, nome = sub
            if pasta == "inbox":
                ok = nome.endswith(".md")
            else:
                ok = nome in TAREFA_DOCS and not _negado(pasta) and not pasta.startswith("decisoes")
            r = _lstat_seguro(self.tasks, (pasta,), nome) if ok else None
            f = _ficheiro(raiz, sub, *r) if r else None
            return f if f and f.ext == "md" else None
        fonte = raiz.removeprefix("aprovacoes-")
        # como o _pedidos(); no modo arquivo não (os pedidos de aprovação nunca se arquivam)
        if raiz.startswith("aprovacoes-") and fonte in self.aprovacoes and len(sub) == 1 and not self.arquivo:
            nome = sub[0]
            if not nome.endswith(".md") or classificar(raiz, sub) != "doc":
                return None
            lido = _pedido(fonte, raiz, self.aprovacoes[fonte], nome)   # só o que tem frontmatter de pedido
            return lido[1] if lido else None
        return None

    def aguardar(self, timeout: float | None = None) -> None:
        # espera pela reconstrução em curso, se houver (testes e medições)
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _lancar(self) -> None:
        if not self._lock.acquire(blocking=False):   # já há uma reconstrução a correr
            return
        try:
            t = threading.Thread(target=self._fundo, name="biblioteca-indice", daemon=True)
            self._thread = t
            t.start()
        except RuntimeError as e:   # sem threads (limite do contentor): fica o instantâneo actual, tenta no próximo pedido
            self._lock.release()
            print(f"biblioteca: não arrancou a reconstrução ({e!r})", file=sys.stderr)

    def _fundo(self) -> None:
        try:
            self._refazer()
        finally:
            self._lock.release()

    def _refazer(self) -> None:
        # com self._lock na mão; troca o instantâneo de uma vez (os leitores vêem o antigo ou o novo, nunca a meio)
        agora = self.clock()
        try:
            self._snap = self._construir(agora)
        except Exception as e:   # um ficheiro estranho não deita a Biblioteca abaixo nem re-varre a cada pedido
            print(f"biblioteca: varrimento falhou ({e!r}); fica o índice anterior até {TTL_S} s", file=sys.stderr)
            self._snap = (self._snap or _snapshot(agora, [], {}, {}, {}))._replace(gerado=agora)
        self.scans += 1

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
        # cada pedido só é relido e analisado quando muda (lstat: mtime, tamanho, inode, tipo), como o _cab
        out, fich, novo = [], [], {}
        for fonte, root in self.aprovacoes.items():
            raiz = f"aprovacoes-{fonte}"
            for e in _scandir(root):
                nome = e.name
                if not nome.endswith(".md") or classificar(raiz, (nome,)) != "doc":
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                k = (str(root), nome, st.st_mtime_ns, st.st_size, st.st_dev, st.st_ino, st.st_mode)
                lido = self._ped[k] if k in self._ped else _pedido(fonte, raiz, root, nome)
                novo[k] = lido
                if lido is not None:
                    out.append(lido[0])
                    fich.append(lido[1])
        self._ped = novo
        out.sort(key=lambda p: (p["criado"], p["ficheiro"], p["fonte"]))   # o último é o mais recente
        return out, fich

    def _tarefas(self) -> tuple[list[dict], list[Ficheiro]]:
        grupos, fich = [], []
        for e in () if self.arquivo else _scandir(self.tasks):   # tarefas nunca se arquivam
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
        reg: dict = {}
        for raiz, root in self.raizes.items():
            if self.arquivo:   # a `.arquivo/` tem de ser uma pasta, não um symlink (a raiz da fonte pode sê-lo)
                try:
                    if not stat.S_ISDIR(os.lstat(root).st_mode):
                        continue
                except OSError:
                    continue
                raw = _ler_cauda(str(root / arquivo.REGISTO), REGISTO_MAX)
                reg[raiz] = arquivo.ler_registo(raw.decode("utf-8", "replace") if raw else "")
            todos += varrer(raiz, root)
        self._reg = reg
        pedidos, fich_ped = self._pedidos()
        tarefas, fich_tar = self._tarefas()
        for f in todos + fich_ped + fich_tar:
            ficheiros[_chave(f)] = f
        por_pasta: dict[str, list[Ficheiro]] = {}
        for f in todos:
            por_pasta.setdefault(f"{f.raiz}/{f.rel.rpartition('/')[0]}".rstrip("/"), []).append(f)

        # ligações: pedido mais recente por ficheiro citado (com as linhas que o citam) e pedidos por caminho citado
        cit_ficheiro: dict[str, tuple[dict, list[str]]] = {}
        # índice invertido: caminho citado → posições dos pedidos que o citam; lote → pedidos que citam algo dentro dele
        cit_pedidos: dict[str, list[int]] = {}
        cit_lote: dict[str, list[int]] = {}
        for i, p in enumerate(pedidos):   # do mais antigo para o mais recente: o último ganha
            linhas: dict[str, list[str]] = {}
            for linha, c in p["citacoes"]:
                linhas.setdefault(c, []).append(linha)
            for c, ls in linhas.items():
                cit_ficheiro[c] = (p, ls)
                cit_pedidos.setdefault(c, []).append(i)
                partes = c.split("/", 3)   # «<fonte media>/media/<lote>» ou «…/<lote>/…» (prefixo exacto «<lote>/»)
                if len(partes) >= 3 and partes[0] in RAIZES_MEDIA and partes[1] == "media" and partes[2]:
                    lote = cit_lote.setdefault("/".join(partes[:3]), [])
                    if not lote or lote[-1] != i:
                        lote.append(i)

        def ligados(chaves=(), lote=None) -> list[dict]:
            # pedidos (do mais antigo para o mais recente) que citam alguma das chaves, ou algo dentro do lote
            ii = {i for c in chaves for i in cit_pedidos.get(c, ())}
            if lote is not None:
                ii.update(cit_lote.get(lote, ()))
            return [pedidos[i] for i in sorted(ii)]

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
            it = self._item(g, ficheiros, cit_ficheiro, ligados, poster, agora, novo_cab)
            if it is None:
                continue
            por_id[g["id"]] = it
            for chave in it["donos"]:   # documentos ligados não são donos do lote: o deep link abre o documento
                ficheiro_item.setdefault(chave, g["id"])
            itens.append(it["publico"])
        self._cab = novo_cab
        itens.sort(key=lambda i: (-i["actualizado"], i["id"]))
        return _snapshot(agora, itens, por_id, ficheiros, ficheiro_item)

    def _item(self, g, ficheiros, cit_ficheiro, ligados, poster, agora, novo_cab) -> dict | None:
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
            ligados_ped = ligados(lote=gid) if genero == "lote" else ligados([_chave(f) for f in destaque])
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
            ligados_ped = ligados([_chave(f) for f in fs])
        titulo = _corta(titulo, TITULO_MAX)

        # estado por ficheiro do destaque (lotes/pastas com pedido)
        pedido = ligados_ped[-1] if ligados_ped else None
        estado = pedido["estado"] if pedido and genero in ("lote", "pasta") else None
        estados = {}
        ids_ligados = {id(p) for p in ligados_ped}
        for f in destaque:
            c = cit_ficheiro.get(_chave(f))
            if c and id(c[0]) in ids_ligados:
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
            "id": gid, "raiz": raiz, "genero": genero, "titulo": titulo, "sub": _mascara_curta(sub),
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
        if self.arquivo:   # «Arquivado em <data> · <motivo>»: pelo item no REGISTO, senão pelo 1.º ficheiro próprio
            publico["arquivado"] = self._arquivado(raiz, gid, proprios)
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

    def _arquivado(self, raiz: str, gid: str, proprios: list[Ficheiro]) -> dict:
        por_item, por_caminho = self._reg.get(raiz, ({}, {}))
        e = por_item.get(gid.partition("/")[2])
        if e is None and proprios:
            e = por_caminho.get(proprios[0].rel)
        if e is None or e[1] != "arquivar":   # sem registo (movido à mão) ou o último foi uma reposição
            return {"em": None, "motivo": None}
        return {"em": e[0], "motivo": e[2]}


def _snapshot(agora: float, itens, por_id, ficheiros, ficheiro_item) -> Snapshot:
    # o mesmo que json.dumps({"gerado": …, "itens": …}); o ETag é só dos itens (fraco: «gerado» muda a cada
    # varrimento, mas um índice igual ao anterior continua a dar 304)
    js = json.dumps(itens, ensure_ascii=False, separators=(",", ":")).encode()
    corpo = b'{"gerado":%d,"itens":%s}' % (int(agora), js)
    etag = hashlib.sha256(js).hexdigest()[:32]
    return Snapshot(agora, itens, por_id, ficheiros, ficheiro_item, corpo, gzip.compress(corpo, 6, mtime=0), etag)


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


def _resolver(snap: Snapshot | None, resto: str, indice: Indice | None = None) -> Ficheiro | None:
    # S4: descodificar uma vez; componentes vazios, ., .., ocultos, \ ou NUL → None; só o que está no índice.
    # Sem índice (1.º arranque) ou com ele mais velho que o TTL_S (a reconstrução ainda não acabou): directamente no
    # disco, com as mesmas regras do varrimento — o ficheiro novo ou alterado serve-se como antes, sem esperar pelo lock
    try:
        rel = unquote(resto, errors="strict")
    except UnicodeDecodeError:
        return None
    partes = rel.split("/")
    if len(partes) < 2 or any(not _componente_ok(c) for c in partes):
        return None
    if snap is not None and (indice is None or snap.gerado + TTL_S >= indice.clock()):
        f = snap.ficheiros.get(rel)
    else:
        f = indice.direto(partes) if indice is not None else None
    return None if f is None or _grande(f) else f


def _lstat_seguro(root: Path, pastas, nome: str):
    # desce pasta a pasta por fds com O_NOFOLLOW (nenhuma pasta abaixo da raiz pode ser symlink) → (caminho, lstat)
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        for c in pastas:
            novo = os.open(c, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = novo
        st = os.stat(nome, dir_fd=fd, follow_symlinks=False)
    except OSError:
        return None
    finally:
        os.close(fd)
    return os.path.join(str(root), *pastas, nome), st


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


def _servir_zip(h, snap: Snapshot, query: str, arq: Indice | None = None) -> None:
    ids = parse_qs(query).get("id")
    it = snap.por_id.get(ids[0]) if ids else None
    if it is not None and arq is not None and it["donos"]:   # arquivado depois do varrimento: o zip sairia incompleto
        f = snap.ficheiros.get(it["donos"][0])
        if f is not None and _sumiu(f):
            it = None
    if it is None and ids and arq is not None:   # item arquivado (link antigo)
        it = _snap_arq(arq, ids[0]).por_id.get(ids[0])
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


def _sumiu(f: Ficheiro) -> bool:
    # o ficheiro do índice já não está no caminho (arquivado depois do varrimento): tenta-se a `.arquivo/`
    try:
        os.lstat(f.abs)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _registo_depois(arq: Indice, gerado: float) -> bool:
    # o runner escreve no `<fonte>/.arquivo/REGISTO.tsv` depois de cada move: mais novo que o instantâneo ⇒ arquivou-se
    # depois do varrimento. Só o lstat (sem seguir symlinks nem abrir o ficheiro)
    for root in arq.fontes.values():
        try:
            if os.lstat(Path(root) / ARQ / arquivo.REGISTO).st_mtime > gerado:
                return True
        except OSError:
            pass
    return False


def _snap_arq(arq: Indice, pedido: str) -> Snapshot:
    # /item e /zip: o instantâneo do arquivo; sem o pedido (id ou caminho) e velho (passou o TTL_S ou é anterior ao
    # REGISTO.tsv de alguma fonte: arquivado depois do varrimento), espera até ARQ_ESPERA_S pela reconstrução e usa o
    # novo (nunca no /f/ e /mini/: esses sem lock)
    snap = arq.get()
    if pedido in snap.por_id or pedido in snap.ficheiro_item:
        return snap
    if snap.gerado + TTL_S >= arq.clock():   # fresco: só se o runner escreveu no REGISTO depois (o get() não relança)
        if not _registo_depois(arq, snap.gerado):
            return snap
        arq._lancar()
    arq.aguardar(ARQ_ESPERA_S)
    return arq.atual() or snap


def _resolver_arq(indice: Indice, arq: Indice | None, resto: str) -> Ficheiro | None:
    # o índice principal; se lá não estiver (ou já não estiver no disco), o mesmo caminho na `.arquivo/` da mesma
    # fonte, sempre pelo resolver directo (o índice do arquivo só se faz a pedido): link antigo, sem lock
    f = _resolver(indice.atual(), resto, indice)
    if f is not None and _sumiu(f):
        f = None
    if f is None and arq is not None:
        f = _resolver(None, resto, arq)
    return f


def handle(h, metodo: str, path: str, query: str, indice: Indice, arq: Indice | None = None,
           fila: Path | None = None) -> None:
    # router de /api/biblioteca*; h é o Handler do server.py (usa _json, wfile, headers e _cc para o Cache-Control).
    # arq: o índice das `.arquivo/` (ADR-011); fila: a fila do arquivo (estado do último pedido no /item)
    try:
        if path.startswith("/api/biblioteca/f/"):   # /f/ e /mini/ nunca reconstroem nem esperam pelo lock
            f = _resolver_arq(indice, arq, path[len("/api/biblioteca/f/"):])
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
        elif path.startswith("/api/biblioteca/mini/"):
            f = _resolver_arq(indice, arq, path[len("/api/biblioteca/mini/"):])
            if f is None or f.tipo != "imagem":
                _404(h)
            else:
                _servir_mini(h, f)
        elif path == "/api/biblioteca":
            if "1" in parse_qs(query).get("arquivo", ()):   # «Ver arquivados»: o índice das `.arquivo/`, só a pedido
                if arq is None:
                    _404(h)
                else:
                    _servir_indice(h, arq.get())
            else:
                _servir_indice(h, indice.get())
        elif path == "/api/biblioteca/item":
            _servir_item(h, indice.get(), query, arq, fila)
        elif path == "/api/biblioteca/zip":
            _servir_zip(h, indice.get(), query, arq)
        else:
            _404(h)
    finally:
        h._cc = "no-store"


def _servir_indice(h, snap: Snapshot) -> None:
    # comprimido uma vez por varrimento, só se o browser aceitar. ETag fraco por variante (hash dos itens; «-gz» na
    # comprimida) e private, no-cache: o browser guarda e revalida → 304 sem corpo
    gz = "gzip" in h.headers.get("Accept-Encoding", "")
    etag = f'W/"{snap.etag}-gz"' if gz else f'W/"{snap.etag}"'
    h._cc = "private, no-cache"
    if _etag_igual(h.headers.get("If-None-Match"), etag):
        h.send_response(304)
        h.send_header("ETag", etag)
        h.send_header("Vary", "Accept-Encoding")
        h.end_headers()
        return
    corpo = snap.corpo_gz if gz else snap.corpo_json
    h.send_response(200)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    if gz:
        h.send_header("Content-Encoding", "gzip")
    h.send_header("Vary", "Accept-Encoding")
    h.send_header("ETag", etag)
    h.send_header("Content-Length", str(len(corpo)))
    h.end_headers()
    _escrever(h, corpo)


def _etag_igual(cabecalho_inm: str | None, etag: str) -> bool:
    # If-None-Match: lista separada por vírgulas, «*» ou ETags (comparação fraca: W/ ignorado dos dois lados)
    if not cabecalho_inm:
        return False
    etag = etag.removeprefix("W/")
    for t in cabecalho_inm.split(","):
        t = t.strip()
        if t == "*" or t.removeprefix("W/") == etag:
            return True
    return False


def _procura(snap: Snapshot, pedido: str) -> tuple[dict | None, str | None]:
    it = snap.por_id.get(pedido)
    if it is None and pedido in snap.ficheiro_item:
        return snap.por_id[snap.ficheiro_item[pedido]], pedido
    return it, None


def _servir_item(h, snap: Snapshot, query: str, arq: Indice | None = None, fila: Path | None = None) -> None:
    # sem o item no índice principal procura-se o mesmo id/caminho nas `.arquivo/` (o item vem com «arquivado»)
    ids = parse_qs(query).get("id")
    pedido = ids[0] if ids else ""
    it, foco = _procura(snap, pedido)
    presente = None   # o 1.º ficheiro do item do índice principal ainda está no sítio (None: veio do arquivo)
    if it is not None and it["donos"]:
        f = snap.ficheiros.get(it["donos"][0])
        presente = f is not None and not _sumiu(f)
    if it is None and arq is not None and pedido:
        it, foco = _procura(_snap_arq(arq, pedido), pedido)
    if it is None:
        _404(h)
        return
    corpo = {"item": it["publico"], "foco": foco, **it["detalhe"],
             "arquivo": estado_arquivo(it["publico"], fila, presente)}
    h._json(200, json.dumps(corpo, ensure_ascii=False).encode())


# --- arquivo (ADR-011): o Office só escreve pedidos na fila; quem move é o runner ----------------------------------

_fila_lock = threading.Lock()   # serializa a leitura dos pendentes e a escrita (409/429 sem corridas)


def arquivavel(pub: dict) -> bool:
    # documentos e lotes das fontes `biblioteca:` (tarefas, planos, pedidos e pastas de media soltas não)
    return pub["genero"] in arquivo.GENEROS and pub["raiz"] in RAIZES


def estado_arquivo(pub: dict, fila: Path | None, presente: bool | None = None) -> dict:
    # {"arquivavel", "arquivado": None|{em, motivo}, "pendente": None|accao, "erro": None|motivo} do último pedido.
    # Último pedido «arquivar» feito (ok) e o item já fora do sítio (presente False): o índice principal ainda não
    # o sabe (até TTL_S) e «Arquivar» não se oferece de novo
    pendente = erro = None
    arquivado_ok = False
    if fila is not None and arquivavel(pub):
        chave = pub["id"].partition("/")[2]
        for _, p, feito in arquivo.ler_fila(fila, RAIZES):   # por nome: o último ganha
            if p["fonte"] == pub["raiz"] and p["chave"] == chave:
                pendente, erro = (p["accao"], None) if feito is None else (None, feito.get("erro"))
                arquivado_ok = p["accao"] == "arquivar" and feito == {"ok": True}
    oferece = arquivavel(pub) and not (arquivado_ok and presente is False and not pub.get("arquivado"))
    return {"arquivavel": oferece, "arquivado": pub.get("arquivado"), "pendente": pendente, "erro": erro}


def pedir_arquivo(fila: Path, indice: Indice, arq: Indice | None, data: dict, ip: str, ua: str, tz,
                  now: float) -> tuple[int, dict]:
    # POST /api/biblioteca/arquivo (já com _post_json): forma (400) → o item existe (arquivar: no índice principal;
    # repor: no do arquivo) e é arquivável (400) → pedido v1 na fila (202, 409, 429, 503). Nunca move nada
    if set(data) != {"token", "accao", "item"} or not isinstance(data["item"], str):
        return 400, {"error": "campos inválidos"}
    accao, item = data["accao"], data["item"]
    if not isinstance(accao, str) or accao not in arquivo.ACCOES:
        return 400, {"error": "acção inválida"}
    fonte, _, chave = item.partition("/")
    if fonte not in RAIZES:
        return 400, {"error": "não arquivável"}
    idx = indice if accao == "arquivar" else arq
    it = idx.get().por_id.get(item) if idx is not None else None
    if it is None:
        return 400, {"error": "item inexistente"}
    if not arquivavel(it["publico"]) or not arquivo.validar_chave(chave):
        return 400, {"error": "não arquivável"}
    if accao == "arquivar":   # já arquivado pelo runner, mas o índice principal ainda não o sabe (até TTL_S)
        try:
            os.lstat(Path(indice.raizes[fonte]) / chave)
        except OSError:
            return 400, {"error": "item inexistente"}
    with _fila_lock:
        return arquivo.escrever_pedido(fila, accao, fonte, chave, ip, ua, tz, now)
