"""Conversa (ADR-023, revisão do guardião): o que o guardião (`chat_host/guardiao.py`, uid `office-conversa`) e a ponte
(`chat_host/ponte.py`, `dono`) partilham. O Office (contentor, `fabrica`) não o importa: lê só o `publico.json`.

Porte do `chat.py` do Squad Office (ADR-019/020 de lá), sem o JWT do Access. Contrato em docs/conversa-contrato.md (a
versão, `CONTRATO = 2`, vive no guardião e na ponte; aqui fica a forma dos pedidos, dos eventos e do `meta.json`).
- Ids: conversa `AAAAMMDD-HHMMSS-<6 hex>` (gerado pelo browser no `nova`), nonce de 128 bits em hex, pedidos em
  `verificados/` `AAAAMMDDTHHMMSS-<8 hex>.json` (≤ 64 KB, chaves exactas por acção, todas obrigatórias: `validar_post`
  no guardião, `validar_fila` na ponte, a mesma regra).
- Assinatura (§2.3): cada pedido leva `pid` (16 hex), `t` (ms Unix) e `assinatura` = HMAC-SHA256 do `canonico`. O
  guardião confere a forma, a assinatura, o `t` e o `pid` antes de escrever; a ponte confere de novo a forma.
- Pastas: `verificados/` (só o guardião escreve, pedidos e áudios `0640`; a ponte lê por ACL e publica os `vistos`) e
  CONVERSAS_DIR (só a ponte escreve; o guardião lê por ACL): `<id>/meta.json`, `<id>/eventos.jsonl` e `estado.json`
  (pulsação).
Só stdlib, Python ≥ 3.11 (corre no host): sem subprocess, urllib nem sockets.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import threading
import unicodedata
from collections import deque
from datetime import datetime
from pathlib import Path

ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
NONCE = re.compile(r"[0-9a-f]{32}")
PID = re.compile(r"[0-9a-f]{16}")
HEX64 = re.compile(r"[0-9a-f]{64}")
IDIOMA = re.compile(r"[a-z]{2}")
NOME_FILA = re.compile(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{8}\.json")
MODELOS = ("omissao", "fable", "opus", "sonnet", "haiku")
MODOS = ("normal", "plano")
PERMISSOES = ("cartao", "espelho")
ESTADOS = ("a_trabalhar", "a_espera", "parado", "erro", "pronto")
OCUPADO = ("a_trabalhar", "a_espera")
TIPOS_PENDENTE = ("permissao", "pergunta", "plano")
ESCOLHAS = {"responder_permissao": ("uma_vez", "conversa", "recusar"),
            "aprovar_plano": ("aprovar", "continuar", "editar")}
# chaves de cada acção além das comuns (todas obrigatórias: o guardião não preenche omissões, quebraria a assinatura)
ACOES = {"enviar": frozenset({"conversa", "texto", "modo", "modelo", "permissoes"}),
         "parar": frozenset({"conversa"}),
         "arquivar": frozenset({"conversa"}),
         "responder_permissao": frozenset({"conversa", "nonce", "escolha"}),
         "responder_pergunta": frozenset({"conversa", "nonce", "respostas"}),
         "aprovar_plano": frozenset({"conversa", "nonce", "escolha", "texto"}),
         "nova": frozenset({"conversa"}),
         "transcrever": frozenset({"conversa", "audio", "formato", "bytes", "sha256", "idioma"})}
SO_AUDIO = frozenset({"transcrever"})   # só pela rota de áudio, nunca pelo POST /pedido
# acção de resposta → tipo do pendente em meta.pendentes
TIPO_DE = {"responder_permissao": "permissao", "responder_pergunta": "pergunta", "aprovar_plano": "plano"}
BASE_POST = frozenset({"acao", "pid", "t", "assinatura"})   # corpo do POST = o pedido assinado
EXTRA_FILA = frozenset({"v", "criado", "ip", "ua"})         # postas pelo guardião ao gravar
BASE_FILA = BASE_POST | EXTRA_FILA
NAO_ASSINADAS = EXTRA_FILA | {"assinatura"}                 # fora do canónico

TEXTO_MAX = 16000
OUTRA_MAX = 1000
PERGUNTAS_MAX = 4
OPCOES_MAX = 4
NOME_MAX = 40
T_MAX_S = 300                 # |t/1000 − agora| aceite pela ponte
T_LIMITE = 2 ** 53            # `t` tem de caber num Number do JS
FILA_MAX = 64 * 1024          # bytes de um pedido na fila (e corpo do POST)
IP_MAX, UA_MAX, MOTIVO_MAX = 64, 120, 200
# voz: Content-Type → extensão; o áudio vai para conversa-fila/audio/ e a ponte transcreve-o
FORMATOS = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3"}
AUDIO_MIN, AUDIO_MAX = 1024, 5 * 1024 * 1024
AUDIO_DIR = "audio"
NOME_AUDIO = re.compile(r"[0-9a-f]{32}\.(?:webm|ogg|m4a|mp3)")
VOZES = ("desligada", "local", "externa")
MODO_FICHEIRO = 0o640         # pedidos e áudios: a máscara da ACL tem de deixar o `r` ao dono (§2.1)

PENDENTES_MAX = 200           # pedidos na raiz da fila
META_MAX = 256 * 1024
ENTRADA_MAX = 4096
PREFS_MAX = 4096
EVENTOS_MAX = 500             # por resposta
EVENTOS_BYTES = 256 * 1024    # por resposta (pelo menos um evento sai sempre)
LINHA_MAX = 1024 * 1024       # linhas maiores no eventos.jsonl ignoram-se
LISTA_MAX = 200
Q_MIN, Q_MAX = 2, 100
PESQUISA_MAX = 1024 * 1024    # texto pesquisável por conversa
PULSACAO_S = 60
SESSAO_S = 12 * 3600
SESSOES_MAX = 20
RITMO_N, RITMO_S = 30, 60
FALHAS_MAX, BLOQUEIO_S = 5, 15 * 60
ITERACOES = 600000
SAL = re.compile(r"[0-9a-f]{32}")
ANFITRIAO = re.compile(r"(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*"
                       r"(:[0-9]{1,5})?")
ESTADO = "estado.json"
ENTRADA = "entrada.json"
PREFS = "prefs.json"

# mensagens (o UI conhece-as)
NAO_ENCONTRADO = "não encontrado"
SEM_TLS = "a Conversa só liga com HTTPS"
ORIGEM = "origem não permitida"
SESSAO_INVALIDA = "sessão da Conversa inválida, abre de novo"
CSRF_INVALIDO = "pedido sem CSRF válido, recarrega"
RITMO = "demasiados pedidos, espera um pouco"
CODIGO_ERRADO = "código errado"
BLOQUEADO = "bloqueado durante 15 min"
SEM_ENTRADA = "falta correr o instalar-conversa.sh"
AVISO_TOKENS = "aceita primeiro o aviso de consumo de tokens"
AVISO_VOZ = "aceita primeiro o aviso da voz externa"
PERMISSOES_DIFEREM = "permissões não batem certo; recarrega"
PONTE_PARADA = "a ponte não está a correr"
VERSOES = "versões do Office e da ponte diferem; recarrega"
A_TRABALHAR = "o assistente está a trabalhar"
APROVACAO_INVALIDA = "pedido de aprovação inválido ou já respondido"
DELEGACAO_CONVERSA = "numa delegação não há «Aprovar nesta conversa»"
ARQUIVADA = "conversa arquivada"
JA_EXISTE = "a conversa já existe"
FILA_CHEIA = "fila cheia"
FILA_INDISPONIVEL = "fila da Conversa indisponível"
PREFS_INDISPONIVEIS = "preferências da Conversa indisponíveis"
SEM_FORNECEDOR = "a ponte não anuncia o fornecedor da voz externa"
Q_INVALIDA = f"a pesquisa tem de ter entre {Q_MIN} e {Q_MAX} caracteres"
DESDE_INVALIDO = "desde inválido"
AUDIO_VAZIO = "áudio vazio"
AUDIO_TAMANHO = "tamanho do áudio inválido"
AUDIO_GRANDE = "áudio grande demais (máx. 5 MB)"
AUDIO_FORMATO = "formato de áudio não suportado"
AUDIO_OCUPADO = "já há um áudio a transcrever; espera que termine"
AUDIO_NAO_BATE = "o pedido do áudio não bate certo com o corpo"
RELOGIO = "relógio do dispositivo desacertado (mais de 5 min)"

LOCK = threading.RLock()   # o guardião segura-o entre as verificações contra verificados/ (409) e a escrita


def sem_voz(modo: str) -> str:
    return f"a voz {modo} não está instalada no servidor"


# --- ids --------------------------------------------------------------------------------------------------------------
def id_valido(c) -> bool:
    return isinstance(c, str) and ID.fullmatch(c) is not None


def nome_fila(now: float, tz) -> str:
    return f"{datetime.fromtimestamp(now, tz).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}.json"


# --- assinatura (§2.3) ------------------------------------------------------------------------------------------------
def canonico(d: dict) -> bytes:
    # o que se assina: o pedido sem v/criado/ip/ua/assinatura, chaves ordenadas (também dentro de `respostas`), sem
    # espaços, UTF-8 sem escapes. Em JS: ordenação recursiva das chaves + JSON.stringify
    return json.dumps({k: v for k, v in d.items() if k not in NAO_ASSINADAS}, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def assinar(chave: bytes, d: dict) -> str:
    return hmac.new(chave, canonico(d), "sha256").hexdigest()


def assinatura_ok(chave: bytes, d) -> bool:
    # a ponte: a `assinatura` (64 hex minúsculo) é o HMAC-SHA256 do canónico com a chave da Conversa
    if not isinstance(d, dict) or not isinstance(d.get("assinatura"), str) or not HEX64.fullmatch(d["assinatura"]):
        return False
    return hmac.compare_digest(assinar(chave, d), d["assinatura"])


def tempo_ok(d: dict, agora: float) -> bool:
    # a ponte: |t/1000 − agora| ≤ 5 min (t já validado em forma)
    return abs(d["t"] / 1000 - agora) <= T_MAX_S


# --- validação dos pedidos --------------------------------------------------------------------------------------------
def _int(v) -> bool:
    return type(v) is int


def texto_ok(s, mn: int = 1, mx: int = TEXTO_MAX) -> bool:
    # mn ≤ len ≤ mx, sem surrogates soltos, C0 só \n e \t
    if not isinstance(s, str) or not mn <= len(s) <= mx:
        return False
    return not any((c < " " and c not in "\n\t") or "\ud800" <= c <= "\udfff" for c in s)


def nome_ok(s) -> bool:
    # nome do assistente: 1–40 caracteres, sem controlo (Cc), formato (Cf) nem surrogates; não só espaços
    if not isinstance(s, str) or not 1 <= len(s) <= NOME_MAX or not s.strip():
        return False
    return not any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in s)


def _respostas(rs) -> bool:
    if not isinstance(rs, list) or not 1 <= len(rs) <= PERGUNTAS_MAX:
        return False
    vistas = set()
    for r in rs:
        if not isinstance(r, dict) or set(r) not in ({"pergunta", "opcao"}, {"pergunta", "outra"}):
            return False
        p = r["pergunta"]
        if not _int(p) or not 0 <= p < PERGUNTAS_MAX or p in vistas:
            return False
        vistas.add(p)
        if "opcao" in r:
            o = r["opcao"]
            if not isinstance(o, list) or not 1 <= len(o) <= OPCOES_MAX or len(set(o)) != len(o) \
                    or not all(_int(i) and 0 <= i < OPCOES_MAX for i in o):
                return False
        elif not texto_ok(r["outra"], 1, OUTRA_MAX):
            return False
    return True


def _comuns(d: dict) -> str | None:
    if not isinstance(d["pid"], str) or not PID.fullmatch(d["pid"]):
        return "pid inválido"
    if not _int(d["t"]) or not 0 < d["t"] < T_LIMITE:
        return "t inválido"
    if not isinstance(d["assinatura"], str) or not HEX64.fullmatch(d["assinatura"]):
        return "assinatura inválida"
    return None


def _acao(d: dict, fila: bool) -> str | None:
    # regras de cada acção, iguais no POST e na fila (no POST o id de uma conversa existente só se confere depois, 404
    # como um inexistente; o do `nova`, que o browser gera, confere-se já)
    acao = d["acao"]
    c = d["conversa"]
    if not isinstance(c, str) or ((fila or acao == "nova") and not id_valido(c)):
        return "conversa inválida"
    if "nonce" in d and not (isinstance(d["nonce"], str) and NONCE.fullmatch(d["nonce"])):
        return "nonce inválido"
    if acao == "enviar":
        if not texto_ok(d["texto"]):
            return "texto inválido"
        if d["modo"] not in MODOS:
            return "modo inválido"
        if d["modelo"] not in MODELOS:
            return "modelo inválido"
        if d["permissoes"] not in PERMISSOES:
            return "permissões inválidas"
    elif acao in ESCOLHAS:
        if d["escolha"] not in ESCOLHAS[acao]:
            return "escolha inválida"
        if acao == "aprovar_plano":
            if d["escolha"] == "editar":
                if not texto_ok(d["texto"]):
                    return "texto inválido"
            elif d["texto"] != "":
                return "texto só em «editar»"
    elif acao == "responder_pergunta" and not _respostas(d["respostas"]):
        return "respostas inválidas"
    elif acao == "transcrever":
        if not (isinstance(d["audio"], str) and NONCE.fullmatch(d["audio"])):
            return "áudio inválido"
        if d["formato"] not in FORMATOS.values():
            return "formato inválido"
        if not _int(d["bytes"]) or not AUDIO_MIN <= d["bytes"] <= AUDIO_MAX:
            return "bytes inválidos"
        if not isinstance(d["sha256"], str) or not HEX64.fullmatch(d["sha256"]):
            return "sha256 inválido"
        if not isinstance(d["idioma"], str) or not IDIOMA.fullmatch(d["idioma"]):
            return "idioma inválido"
    return None


def validar_post(d, audio: bool = False) -> str | None:
    # corpo do POST /api/conversa/pedido (ou o X-Conversa-Pedido da rota de áudio, com audio=True): as chaves exactas
    # da acção; None ou a mensagem do 400. A assinatura só se confere em forma (o guardião confere-a à parte, com a chave)
    if not isinstance(d, dict) or not isinstance(d.get("acao"), str) or d["acao"] not in ACOES \
            or (d["acao"] in SO_AUDIO) != audio:
        return "acção inválida"
    if set(d) != BASE_POST | ACOES[d["acao"]]:
        return "campos inválidos"
    return _comuns(d) or _acao(d, fila=False)


def validar_fila(d) -> str | None:
    # um pedido lido da fila (a ponte): as chaves exactas da acção mais v/criado/ip/ua; None ou o motivo (≤ 200). Não
    # confere a assinatura (`assinatura_ok`), o `t` (`tempo_ok`) nem o `pid` repetido: isso é da ponte
    if not isinstance(d, dict) or not isinstance(d.get("acao"), str) or d["acao"] not in ACOES:
        return "acção inválida"
    if set(d) != BASE_FILA | ACOES[d["acao"]]:
        return "campos inválidos"
    if not _int(d["v"]) or d["v"] != 1:
        return "versão inválida"
    if not isinstance(d["criado"], str) or _ts(d["criado"]) is None:
        return "criado inválido"
    if not isinstance(d["ip"], str) or len(d["ip"]) > IP_MAX or not isinstance(d["ua"], str) \
            or len(d["ua"]) > UA_MAX:
        return "ip ou ua inválidos"
    return _comuns(d) or _acao(d, fila=True)


def doc_fila(d: dict, ip: str, ua: str, criado: str) -> dict:
    # o pedido que vai para a fila: o corpo assinado tal como veio, mais v/criado/ip/ua (fora do canónico)
    out = {"v": 1, "criado": criado, "ip": ip[:IP_MAX], "ua": ua[:UA_MAX]}
    out.update(d)
    return out


def _ts(iso) -> float | None:
    # ISO com fuso → epoch; sem fuso ou mal formado → None
    if not isinstance(iso, str):
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return dt.timestamp() if dt.tzinfo is not None else None


# --- ficheiros --------------------------------------------------------------------------------------------------------
def _ler(path: Path, n: int) -> bytes | None:
    # ≤ n + 1 bytes de um ficheiro regular (sem symlinks, sem pendurar num FIFO); None se não der
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with open(fd, "rb") as f:
            return f.read(n + 1) if stat.S_ISREG(os.fstat(f.fileno()).st_mode) else None
    except OSError:
        return None


def ler_json(path: Path, n: int):
    raw = _ler(path, n)
    if raw is None or len(raw) > n:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def pasta(conversas: Path, cid) -> Path | None:
    # a pasta de uma conversa: id bem formado e um directório de verdade (lstat: nunca um symlink)
    if not id_valido(cid):
        return None
    p = Path(conversas) / cid
    try:
        return p if stat.S_ISDIR(os.lstat(p).st_mode) else None
    except OSError:
        return None


def existe(conversas: Path, cid: str) -> bool:
    # qualquer entrada com este nome (pasta, ficheiro, symlink): o `nova` não a reaproveita
    try:
        os.lstat(Path(conversas) / cid)
    except OSError:
        return False
    return True


def _meta_ok(m, cid: str) -> bool:
    if not isinstance(m, dict) or not _int(m.get("v")) or m["v"] != 1 or m.get("id") != cid:
        return False
    if not all(isinstance(m.get(k), str) for k in ("titulo", "criada", "actualizada")):
        return False
    if m.get("estado") not in ESTADOS or not isinstance(m.get("arquivada"), bool) or not _int(m.get("n")):
        return False
    if m.get("ultimo_fim") is not None and not _int(m["ultimo_fim"]):
        return False
    ps = m.get("pendentes")
    return isinstance(ps, list) and all(
        isinstance(p, dict) and _int(p.get("n")) and isinstance(p.get("nonce"), str) and NONCE.fullmatch(p["nonce"])
        and p.get("tipo") in TIPOS_PENDENTE and isinstance(p.get("delegacao"), bool)
        and isinstance(p.get("expira"), str)
        for p in ps)


def ler_meta(conversas: Path, cid) -> dict | None:
    # meta.json de uma conversa (id e pasta conferidos, ficheiro regular ≤ 256 KB, forma do contrato); None = não existe
    p = pasta(conversas, cid)
    if p is None:
        return None
    m = ler_json(p / "meta.json", META_MAX)
    return m if _meta_ok(m, cid) else None


def ids(conversas: Path) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(conversas) if ID.fullmatch(e.name))
    except OSError:
        return []


def _linhas(path: Path):
    # (bytes da linha com o \n) de um .jsonl regular, em fluxo; linhas > LINHA_MAX saltam-se; a última sem \n (a ponte
    # ainda a escrever) não sai
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return
    try:
        f = open(fd, "rb")   # noqa: SIM115 - fechado no with abaixo
    except OSError:
        os.close(fd)
        return
    with f:
        try:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return
            while True:
                ln = f.readline(LINHA_MAX + 1)
                if not ln:
                    return
                if ln.endswith(b"\n"):
                    yield ln
                    continue
                if len(ln) <= LINHA_MAX:
                    return   # linha parcial no fim
                while ln and not ln.endswith(b"\n"):   # linha grande demais: salta até ao \n
                    ln = f.readline(LINHA_MAX + 1)
        except OSError:
            return


_PREFIXO_N = re.compile(rb'\{"n": ?(\d{1,15})[,}]')


def ler_eventos(path: Path, desde: int, maximo: int = EVENTOS_MAX,
                max_bytes: int = EVENTOS_BYTES) -> tuple[list[dict], int, bool]:
    # (eventos com n > desde, por ordem, ≤ maximo e ≤ max_bytes (pelo menos 1), seguinte, mais). Linhas que não são
    # JSON ou sem `n` inteiro ignoram-se
    out, total, mais = [], 0, False
    for ln in _linhas(path):
        m = _PREFIXO_N.match(ln)   # a ponte escreve sempre `{"n": K` primeiro: as já vistas saltam-se sem parse
        if m and int(m.group(1)) <= desde:
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(ev, dict) or not _int(ev.get("n")) or ev["n"] <= desde:
            continue
        if out and (len(out) >= maximo or total + len(ln) > max_bytes):
            mais = True
            break
        out.append(ev)
        total += len(ln)
    return out, (out[-1]["n"] if out else desde), mais


# campos estruturais (ids, enums, números) que não passam pela máscara
_SEM_MASCARA = frozenset({"n", "t", "tipo", "nonce", "autor", "estado", "codigo", "motivo", "resolvido", "ref",
                          "expira", "delegacao", "auto", "sha256", "multipla", "usd", "tokens", "dia_usd",
                          "audio", "duracao_s", "tempo_s", "livre_mb", "preciso_mb"})


def mask(s: str) -> str:
    # a máscara do reader (importado só quando se usa): o reader.py usa sintaxe de regex do Python ≥ 3.11, por isso a
    # ponte exige o Python ≥ 3.11 (o instalar-conversa.sh confere-o)
    import reader
    return reader.mask(s)


def _mascarar(v):
    if isinstance(v, str):
        return mask(v)
    if isinstance(v, list):
        return [_mascarar(x) for x in v]
    if isinstance(v, dict):
        return {k: _mascarar(x) for k, x in v.items()}
    return v


def mascarar_evento(ev: dict) -> dict:
    # todo o texto do evento (também dentro de listas/objectos: perguntas) pela máscara do reader
    return {k: (v if k in _SEM_MASCARA else _mascarar(v)) for k, v in ev.items()}


# --- lista e pesquisa -------------------------------------------------------------------------------------------------
def norm(s: str) -> str:
    # sem acentos (NFKD sem as marcas) e sem maiúsculas (casefold)
    if s.isascii():
        return s.lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


_pesq_lock = threading.Lock()
_pesq: dict[str, list] = {}   # id → [ino, tamanho, mtime_ns, texto, texto normalizado, mascarado normalizado | None]


def _texto_pesquisa(p: Path, cid: str) -> list | None:
    # as mensagens da conversa (≤ PESQUISA_MAX), em cache por (inode, tamanho, mtime); a versão mascarada só se
    # calcula quando é precisa (a máscara é a parte cara)
    ev = p / "eventos.jsonl"
    try:
        st = os.lstat(ev)
    except OSError:
        return None
    chave = [st.st_ino, st.st_size, st.st_mtime_ns]
    with _pesq_lock:
        c = _pesq.get(cid)
    if c is not None and c[:3] == chave:
        return c
    partes, n = [], 0
    for ln in _linhas(ev):
        if b'"mensagem"' not in ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("tipo") == "mensagem" and isinstance(e.get("texto"), str):
            partes.append(e["texto"])
            n += len(e["texto"]) + 1
            if n >= PESQUISA_MAX:
                break
    texto = "\n".join(partes)[:PESQUISA_MAX]
    c = chave + [texto, norm(texto), None]
    with _pesq_lock:
        if len(_pesq) > 4 * LISTA_MAX:
            _pesq.clear()
        _pesq[cid] = c
    return c


def pesquisar(conversas: Path, metas: dict[str, dict], q: str) -> set[str]:
    # ids (de `metas`) cujo título ou mensagens contêm q (substring, sem acentos nem maiúsculas, sem regex), conferido
    # sobre o texto mascarado (um segredo não se encontra). Só se mascara onde o texto bruto já contém q, por isso o
    # próprio «[segredo]» da máscara não se procura
    alvo = norm(q)
    out = set()
    for cid, m in metas.items():
        if alvo in norm(m["titulo"]) and alvo in norm(mask(m["titulo"])):
            out.add(cid)
            continue
        p = pasta(conversas, cid)
        c = _texto_pesquisa(p, cid) if p is not None else None
        if c is None or alvo not in c[4]:
            continue
        if c[5] is None:
            c[5] = norm(mask(c[3]))
        if alvo in c[5]:
            out.add(cid)
    return out


def _vivos(pendentes: list, agora: float) -> int:
    # só os pedidos ainda no prazo: com a ponte parada um expirado não acende o contador
    return sum(1 for p in pendentes if (e := _ts(p.get("expira"))) is not None and e > agora)


def listar(conversas: Path, q: str | None, agora: float) -> list[dict]:
    # não arquivadas primeiro, `actualizada` mais recente primeiro, ≤ LISTA_MAX; títulos mascarados
    metas = {}
    for cid in ids(conversas):
        m = ler_meta(conversas, cid)
        if m is not None:
            metas[cid] = m
    if q is not None:
        achados = pesquisar(conversas, metas, q)
        metas = {k: v for k, v in metas.items() if k in achados}
    ordem = sorted(metas.values(), key=lambda m: (m["arquivada"], -(_ts(m["actualizada"]) or 0.0), m["id"]))
    return [{"id": m["id"], "titulo": mask(m["titulo"]), "criada": m["criada"], "actualizada": m["actualizada"],
             "estado": m["estado"], "arquivada": m["arquivada"], "pendentes": _vivos(m["pendentes"], agora),
             "ultimo_fim": m.get("ultimo_fim")} for m in ordem[:LISTA_MAX]]


# --- verificados (lado do guardião) -----------------------------------------------------------------------------------
def _nomes(fila: Path) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(fila) if NOME_FILA.fullmatch(e.name))
    except OSError:
        return []


def pendente(meta: dict, nonce: str, tipo: str, agora: float) -> dict | None:
    # o pendente com este nonce e tipo, ainda dentro do prazo
    for p in meta["pendentes"]:
        if p["nonce"] == nonce and p["tipo"] == tipo:
            t = _ts(p["expira"])
            return p if t is not None and t > agora else None
    return None


def escrever_pedido(fila: Path, doc: dict, now: float, tz) -> tuple[int, dict]:
    # 202 {"nome"} | 503 sem pasta / sem escrita / ≥ PENDENTES_MAX na raiz. Tmp + os.link (nunca sobrescreve), 0640
    with LOCK:
        if not Path(fila).is_dir():
            return 503, {"error": FILA_INDISPONIVEL}
        if len(_nomes(fila)) >= PENDENTES_MAX:
            return 503, {"error": FILA_CHEIA}
        nome, tmp = nome_fila(now, tz), None
        try:
            fd, tmp = tempfile.mkstemp(dir=fila, prefix=".", suffix=".tmp")
            os.fchmod(fd, MODO_FICHEIRO)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.link(tmp, Path(fila) / nome)
        except OSError:
            return 503, {"error": FILA_INDISPONIVEL}
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return 202, {"nome": nome}


# --- voz (lado do guardião) -------------------------------------------------------------------------------------------
def tipo_audio(ct) -> str | None:
    # Content-Type (sem parâmetros) → extensão, ou None
    return FORMATOS.get(ct.split(";")[0].strip().lower()) if isinstance(ct, str) else None


def magic_ok(ext: str, cab: bytes) -> bool:
    # os primeiros bytes casam com o tipo declarado
    if ext == "webm":
        return cab[:4] == b"\x1a\x45\xdf\xa3"
    if ext == "ogg":
        return cab[:4] == b"OggS"
    if ext == "m4a":
        return cab[4:8] == b"ftyp"
    if ext == "mp3":
        return cab[:3] == b"ID3" or (len(cab) >= 2 and cab[0] == 0xFF and cab[1] & 0xE0 == 0xE0)
    return False


def _pasta_audio(fila: Path) -> int | None:
    # fd da conversa-fila/audio/ (criada 0700 se faltar; nunca um symlink) ou None
    p = Path(fila) / AUDIO_DIR
    try:
        os.mkdir(p, 0o700)
    except FileExistsError:
        pass
    except OSError:
        return None
    try:
        return os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None


def gravar_audio(fila: Path, nome: str, dados: bytes) -> bool:
    # O_CREAT|O_EXCL|O_NOFOLLOW, 0640, só um nome que casa com NOME_AUDIO
    if not NOME_AUDIO.fullmatch(nome):
        return False
    dfd = _pasta_audio(fila)
    if dfd is None:
        return False
    try:
        fd = os.open(nome, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, MODO_FICHEIRO, dir_fd=dfd)
        try:
            os.fchmod(fd, MODO_FICHEIRO)
            with os.fdopen(fd, "wb") as f:
                f.write(dados)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            os.unlink(nome, dir_fd=dfd)
            return False
    except OSError:
        return False
    finally:
        os.close(dfd)
    return True


def apagar_audio(fila: Path, nome: str) -> None:
    if not NOME_AUDIO.fullmatch(nome):
        return
    try:
        dfd = os.open(Path(fila) / AUDIO_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return
    try:
        os.unlink(nome, dir_fd=dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


# --- entrada (código da Conversa, §2.2) -------------------------------------------------------------------------------
def ler_entrada(conversas: Path) -> dict | None:
    # conversas/entrada.json (escrito pelo instalar-conversa.sh): chaves exactas e forma; None = falta ou inválido
    d = ler_json(Path(conversas) / ENTRADA, ENTRADA_MAX)
    if not isinstance(d, dict) or set(d) != {"v", "sal_office", "sal_ponte", "iteracoes", "verificador_sha256",
                                              "criado"}:
        return None
    if not _int(d["v"]) or d["v"] != 1 or not _int(d["iteracoes"]) or d["iteracoes"] != ITERACOES:
        return None
    if not all(isinstance(d[k], str) and SAL.fullmatch(d[k]) for k in ("sal_office", "sal_ponte")):
        return None
    if not isinstance(d["verificador_sha256"], str) or not HEX64.fullmatch(d["verificador_sha256"]) \
            or _ts(d["criado"]) is None:
        return None
    return d


def verificador_ok(entrada: dict, verificador) -> bool:
    # sha256(bytes do verificador) == verificador_sha256, em tempo constante
    if not isinstance(verificador, str) or not HEX64.fullmatch(verificador):
        return False
    return hmac.compare_digest(hashlib.sha256(bytes.fromhex(verificador)).hexdigest(), entrada["verificador_sha256"])


# --- sessões e bloqueio (só em memória; `agora` vem sempre de fora) ---------------------------------------------------
class Sessoes:
    """cookie → {"csrf", "criada", "vsha", "posts"}. Válida: < 12 h e o `vsha` igual ao verificador_sha256 actual
    (rodar o código invalida-as). No máximo SESSOES_MAX: a mais antiga sai primeiro."""

    def __init__(self):
        self._lock = threading.Lock()
        self._s: dict[str, dict] = {}

    def abrir(self, vsha: str, agora: float) -> tuple[str, dict]:
        with self._lock:
            for k in [k for k, s in self._s.items() if agora - s["criada"] >= SESSAO_S]:
                del self._s[k]
            while len(self._s) >= SESSOES_MAX:
                del self._s[next(iter(self._s))]
            k = secrets.token_urlsafe(32)
            s = {"csrf": secrets.token_urlsafe(32), "criada": agora, "vsha": vsha, "posts": deque()}
            self._s[k] = s
            return k, s

    def obter(self, cookie: str | None, vsha: str | None, agora: float) -> dict | None:
        with self._lock:
            s = self._s.get(cookie or "")
            if s is None or vsha is None or not 0 <= agora - s["criada"] < SESSAO_S \
                    or not hmac.compare_digest(s["vsha"], vsha):
                return None
            return s

    def ritmo(self, s: dict, agora: float) -> bool:
        # janela deslizante de RITMO_S: no máx. RITMO_N POST por sessão (os recusados não contam)
        with self._lock:
            p = s["posts"]
            while p and agora - p[0] >= RITMO_S:
                p.popleft()
            if len(p) >= RITMO_N:
                return False
            p.append(agora)
            return True


class Bloqueio:
    """Global (o Caddy rootless não vê o IP real): FALHAS_MAX códigos errados seguidos → BLOQUEIO_S sem aceitar nenhum,
    nem o certo. Um sucesso zera; o fim do bloqueio também."""

    def __init__(self):
        self._lock = threading.Lock()
        self.falhas, self.ate = 0, None

    def bloqueado_ate(self, agora: float) -> float | None:
        with self._lock:
            if self.ate is not None and agora >= self.ate:
                self.falhas, self.ate = 0, None
            return self.ate

    def falhou(self, agora: float) -> None:
        with self._lock:
            self.falhas += 1
            if self.falhas >= FALHAS_MAX:
                self.ate = agora + BLOQUEIO_S

    def sucesso(self) -> None:
        with self._lock:
            self.falhas, self.ate = 0, None


def cookie(cab: str | None, nome: str) -> str | None:
    for parte in (cab or "").split(";"):
        k, _, v = parte.strip().partition("=")
        if k == nome:
            return v
    return None


# --- preferências (prefs.json no estado do guardião, só ele) ----------------------------------------------------------
def prefs_vazias() -> dict:
    return {"v": 1, "nome": None, "aviso_tokens": None, "aviso_voz": None}


def ler_prefs(pasta_prefs: Path) -> dict:
    # campo a campo: um inválido volta à omissão (nunca rebenta a Conversa)
    d = ler_json(Path(pasta_prefs) / PREFS, PREFS_MAX)
    out = prefs_vazias()
    if not isinstance(d, dict):
        return out
    if nome_ok(d.get("nome")):
        out["nome"] = d["nome"]
    if _ts(d.get("aviso_tokens")) is not None:
        out["aviso_tokens"] = d["aviso_tokens"]
    av = d.get("aviso_voz")
    if isinstance(av, dict) and set(av) == {"fornecedor", "em"} and isinstance(av["fornecedor"], str) \
            and ANFITRIAO.fullmatch(av["fornecedor"]) and _ts(av["em"]) is not None:
        out["aviso_voz"] = {"fornecedor": av["fornecedor"], "em": av["em"]}
    return out


def gravar_prefs(pasta_prefs: Path, prefs: dict) -> bool:
    # tmp 0600 + os.replace na mesma pasta; False se não der
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=pasta_prefs, prefix=".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, Path(pasta_prefs) / PREFS)
        tmp = None
    except OSError:
        return False
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return True


def validar_prefs(d) -> str | None:
    # exactamente uma de {"nome": str|None}, {"aviso_tokens": true}, {"aviso_voz": true}
    if not isinstance(d, dict) or len(d) != 1:
        return "campos inválidos"
    (k, v), = d.items()
    if k == "nome":
        return None if v is None or nome_ok(v) else "nome inválido (1 a 40 caracteres, sem invisíveis)"
    if k in ("aviso_tokens", "aviso_voz"):
        return None if v is True else "campos inválidos"
    return "campos inválidos"


def aviso_voz_ok(prefs: dict, fornecedor: str | None) -> bool:
    # o aviso da voz externa só vale para o fornecedor que a ponte anuncia agora
    av = prefs.get("aviso_voz")
    return fornecedor is not None and isinstance(av, dict) and av.get("fornecedor") == fornecedor


# --- GET estado -------------------------------------------------------------------------------------------------------
def permissoes_efectivas(config: str, ponte: str | None) -> str:
    # espelho só se o office.yaml e o tecto da ponte o disserem (CA-16); na dúvida, cartao
    return "espelho" if config == "espelho" and ponte == "espelho" else "cartao"


def agora_iso(agora: float, tz) -> str:
    return datetime.fromtimestamp(agora, tz).isoformat(timespec="seconds")
