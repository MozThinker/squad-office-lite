#!/usr/bin/env python3
"""Guardião da Conversa (ADR-023, revisão S2/S5): a origem própria da Conversa, com as chaves HMAC só dele.

Unit de sistema `office-conversa-guardiao.service`, uid `office-conversa` (sem casa nem shell), código em
`/opt/office-conversa` (do root) conferido pelo `SHA256SUMS` no arranque. Serve a UI (`web/`, carregada para memória,
CSP estrita) e a API `/api/conversa/*` num socket Unix (`<pasta>/sock/guardiao.sock`, 0660, grupo do proxy) atrás do
Caddy. Confere sessão (cookie `__Host-`), CSRF, `Origin`, assinatura HMAC, `t` e `pid` de cada pedido; só os aceites
vão para `<pasta>/verificados/` (só ele escreve; a ponte lê por ACL). Nem o `dono`, nem o motor, nem a `fabrica` lêem
as chaves (`<estado>/chaves.json`, 0600, em `/var/lib/office-conversa`).

Código de uso único (revisão G2): o código só regista um browser; não é a chave.
- Registo: `POST /api/conversa/registar` `{verificador, kid, chave}`. O browser gerou a chave K (32 bytes aleatórios,
  `chave` = hex) e o `kid` (16 hex); o verificador é o de sempre (PBKDF2 do código com o `sal_office`). Com o código por
  usar, o guardião grava K em `chaves.json`, apaga o `entrada.json` (o código morre) e abre a sessão. Já usado: 403.
- Entrada seguinte: o `GET estado` sem sessão traz um `desafio` (64 hex, uso único, 2 min); `POST /api/conversa/entrar`
  `{kid, desafio, assinatura: HMAC-SHA256(K, "entrar\\n" + desafio) em hex}`. A sessão fica presa a esse `kid`.
- Pedidos: assinados com K (o canónico do `chat.canonico`); a chave escolhe-se pelo `kid` da sessão.
- As falhas de registo (código errado ou já usado) e de entrada (desafio, `kid` ou assinatura) contam para o bloqueio
  (5 seguidas → 15 min, global).

Ficheiros em `<estado>` (`/var/lib/office-conversa`, 0700, dono `office-conversa`; relidos a cada pedido):
- `entrada.json` (0600; escreve-o o `codigo.py --estado <estado>`, como antes): `{"v": 1, "sal_office": <32 hex>,
  "sal_ponte": <32 hex, já sem uso>, "iteracoes": 600000, "verificador_sha256": <64 hex>, "criado": <ISO>}`.
  Existir = há um código por usar. O guardião apaga-o no 1.º registo.
- `chaves.json` (0600; só o guardião escreve): `{"v": 1, "chaves": [{"kid": <16 hex>, "chave": <64 hex>,
  "criada": <ISO>}, ...]}`, no máx. 5 (o 6.º registo tira o mais antigo). Inválido ou de outro dono = sem chaves.
- `pids.json`, `prefs.json`: do guardião. A `chave` antiga (PBKDF2 do código) já não se lê; o `codigo.py` apaga-a.
Instalador: `--novo-codigo` = `codigo.py --estado <estado>` (as chaves ficam: os browsers registados continuam);
`--revogar` = o mesmo e `rm -f <estado>/chaves.json` (todas as sessões caem logo). Sem `entrada.json` nem
`chaves.json` (ou com a `chave` antiga), a instalação gera um código; com `chaves.json` e sem `entrada.json`, não.

- Ambiente: CONVERSA_PASTA, CONVERSA_GUARDIAO_ESTADO (omissão $STATE_DIRECTORY), CONVERSA_OPT, CONVERSA_ESCUTA
  (`unix:<caminho>` ou, só nos testes, `127.0.0.1:<porta>`), CONVERSA_PERMISSOES, CONVERSA_VOZ, CONVERSA_VOZ_IDIOMA,
  CONVERSA_FUSO, CONVERSA_NOME e CONVERSA_TLS_TESTE=1 (só testes: tudo é TLS e aceita `Origin: http://<Host>`).
- Limpeza (thread, de 2 em 2 s): apaga de `verificados/` o que a ponte publicou em `vistos` (e o áudio de um
  `transcrever`), e o que tenha mais de 1 h com a ponte morta; escreve `<pasta>/publico/publico.json` (o Office lê-o).
- Arranque: `SHA256SUMS` que não bate, `web/` com um ficheiro a mais ou escuta inválida: sai com 78 (a unit não
  reinicia).
Só stdlib, Python ≥ 3.11 (importa o `chat.py` e o `reader.py` de `/opt/office-conversa`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import signal
import socketserver
import stat
import sys
import threading
import time
from collections import namedtuple
from datetime import timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))   # chat.py e reader.py (com -I a pasta do script não entra no sys.path)

import chat  # noqa: E402
import reader  # noqa: E402

CONTRATO = 2                  # o da ponte (`versao` na pulsação): lê `verificados/` e publica `vistos`
SAIDA_CONFIG = 78             # EX_CONFIG: RestartPreventExitStatus=78
COOKIE = "__Host-conversa_sessao"
RX_SOMA = re.compile(r"([0-9a-f]{64}) [ *](\S(?:.*\S)?)", re.ASCII)
RX_MANIFESTO = re.compile(r"(?:fonts/)?[a-z0-9_-]+\.(?:html|js|css|woff2|svg|png)", re.ASCII)
TIPOS = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
         "woff2": "font/woff2", "svg": "image/svg+xml", "png": "image/png"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; media-src 'self' blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SEGURANCA = (("Content-Security-Policy", CSP),
             ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"), ("X-Content-Type-Options", "nosniff"),
             ("Permissions-Policy", "microphone=(self)"))
GET_API = ("/api/conversa/estado", "/api/conversa/lista", "/api/conversa/eventos")
SESSAO_POST_MAX = PREFS_POST_MAX = 1024
DESDE = re.compile(r"\d{1,15}", re.ASCII)
PULSACAO_MAX = 16 * 1024      # a pulsação leva até 200 nomes em `vistos`
VISTOS_MAX = 200
PIDS_S = 600                  # um `pid` fica lembrado enquanto o seu `t` estiver a menos disto do relógio
LIMPEZA_S = 2.0
ORFAO_S = 3600                # com a ponte morta, o que está em verificados/ há mais disto apaga-se
PUBLICO_S = 30                # o publico.json reescreve-se pelo menos com esta idade
PENDENTES_PUBLICO_MAX = 10000
ASSINATURA = "assinatura inválida"
REPETIDO = "pedido repetido"
CODIGO_USADO = "código já usado: se não foste tu, alguém registou outro browser; corre sudo ./instalar-conversa.sh --revogar"
DESAFIO_INVALIDO = "desafio inválido ou expirado"
ESTADO_INDISPONIVEL = "o estado do guardião não está disponível"
ENTRADA_INVALIDA = "pedido de entrada inválido"
KID = re.compile(r"[0-9a-f]{16}", re.ASCII)
CHAVES_MAX = 5
CHAVES_FICH_MAX = 4096
DESAFIO_S = 120
DESAFIOS_MAX = 64
NOME_OMISSAO = "Assistente"

Cfg = namedtuple("Cfg", "pasta estado opt escuta permissoes voz voz_idioma tz nome tls_teste")


class Recusa(Exception):
    """O arranque não pode seguir (sai com 78)."""


def _fuso(nome: str):
    try:
        return ZoneInfo(nome)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def config(env=os.environ) -> Cfg:
    pasta = Path(env.get("CONVERSA_PASTA") or "/srv/office-conversa")
    estado = env.get("CONVERSA_GUARDIAO_ESTADO") or (env.get("STATE_DIRECTORY") or "").split(":")[0] \
        or "/var/lib/office-conversa"
    nome = env.get("CONVERSA_NOME") or NOME_OMISSAO
    idioma = env.get("CONVERSA_VOZ_IDIOMA") or "pt"
    return Cfg(pasta, Path(estado), Path(env.get("CONVERSA_OPT") or "/opt/office-conversa"),
               env.get("CONVERSA_ESCUTA") or f"unix:{pasta}/sock/guardiao.sock",
               env.get("CONVERSA_PERMISSOES") if env.get("CONVERSA_PERMISSOES") in chat.PERMISSOES else "cartao",
               env.get("CONVERSA_VOZ") if env.get("CONVERSA_VOZ") in chat.VOZES else "desligada",
               idioma if chat.IDIOMA.fullmatch(idioma) else "pt", _fuso(env.get("CONVERSA_FUSO") or "UTC"),
               nome if chat.nome_ok(nome) else NOME_OMISSAO, env.get("CONVERSA_TLS_TESTE") == "1")


# --- arranque: SHA256SUMS e web/ -------------------------------------------------------------------------------------
def _ler_fich(p: Path) -> bytes:
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise Recusa(f"{p} não é um ficheiro normal")
        return f.read()


def conferir(opt: Path) -> dict[str, bytes]:
    """Confere todos os ficheiros do `SHA256SUMS` (o guardiao.py e o conversa.manifesto têm de lá estar) e devolve a
    UI (nome do manifesto → bytes). Nada em `web/` fora do manifesto e do `SHA256SUMS`; nunca symlinks."""
    try:
        linhas = _ler_fich(opt / "SHA256SUMS").decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise Recusa(f"SHA256SUMS ilegível ({e})") from None
    somas: dict[str, str] = {}
    for ln in linhas:
        m = RX_SOMA.fullmatch(ln)
        if not m or m.group(2).startswith("/") or ".." in Path(m.group(2)).parts or m.group(2) in somas:
            raise Recusa(f"SHA256SUMS: linha inválida: {ln[:120]!r}")
        somas[m.group(2)] = m.group(1)
    for obrigatorio in ("chat_host/guardiao.py", "conversa.manifesto"):
        if obrigatorio not in somas:
            raise Recusa(f"SHA256SUMS sem {obrigatorio}")
    conteudo = {}
    for rel, soma in somas.items():
        try:
            dados = _ler_fich(opt / rel)
        except OSError as e:
            raise Recusa(f"{rel}: {e.strerror}") from None
        if not hmac.compare_digest(hashlib.sha256(dados).hexdigest(), soma):
            raise Recusa(f"{rel} não bate com o SHA256SUMS")
        conteudo[rel] = dados
    nomes = [n.strip() for n in conteudo["conversa.manifesto"].decode("utf-8", "replace").splitlines() if n.strip()]
    if not nomes or nomes[0] != "conversa.html" or len(set(nomes)) != len(nomes) \
            or not all(RX_MANIFESTO.fullmatch(n) for n in nomes):
        raise Recusa("conversa.manifesto inválido")
    falta = [n for n in nomes if f"web/{n}" not in conteudo]
    if falta:
        raise Recusa(f"web/ sem {falta[0]} (ou fora do SHA256SUMS)")
    web = opt / "web"
    for raiz, pastas, ficheiros in os.walk(web):
        for n in pastas + ficheiros:
            p = Path(raiz) / n
            rel = p.relative_to(web).as_posix()
            if p.is_symlink():
                raise Recusa(f"web/{rel} é um symlink")
            if n in ficheiros and rel not in nomes:
                raise Recusa(f"web/{rel} não está no manifesto")
    return {n: conteudo[f"web/{n}"] for n in nomes}


# --- pulsação da ponte e pedidos ainda por ver ------------------------------------------------------------------------
def ler_chaves(estado: Path) -> list[dict]:
    """`<estado>/chaves.json` (ver o topo): a lista das chaves, da mais antiga para a mais nova. Ficheiro regular nosso,
    relido a cada pedido (o --revogar vale logo); falta, inválido ou de outro dono: []."""
    try:
        fd = os.open(estado / "chaves.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return []
        raw = f.read(CHAVES_FICH_MAX + 1)
    try:
        d = json.loads(raw) if len(raw) <= CHAVES_FICH_MAX else None
    except ValueError:
        return []
    cs = d.get("chaves") if isinstance(d, dict) and d.get("v") == 1 and set(d) == {"v", "chaves"} else None
    if not isinstance(cs, list) or len(cs) > CHAVES_MAX:
        return []
    for c in cs:
        if not isinstance(c, dict) or set(c) != {"kid", "chave", "criada"} \
                or not all(isinstance(x, str) for x in c.values()) \
                or not KID.fullmatch(c["kid"]) or not chat.HEX64.fullmatch(c["chave"]):
            return []
    return cs


def chave_de(chaves: list[dict], kid) -> bytes | None:
    return next((bytes.fromhex(c["chave"]) for c in chaves if c["kid"] == kid), None)


def _usd(v) -> float | None:
    # dia_usd da pulsação: número finito ≥ 0 (um inteiro enorme não passa a float: inválido, R3)
    if type(v) not in (int, float):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) and f >= 0 else None


def pulsacao(conversas: Path) -> dict | None:
    """`conversas/estado.json` da ponte, como o `chat.pulsacao`, mais `vistos` (os nomes de `verificados/` que a ponte
    já tratou). Um campo inválido fica None (`vistos` vazio); sem ficheiro ou sem `pulsacao`: None."""
    d = chat.ler_json(Path(conversas) / chat.ESTADO, PULSACAO_MAX)
    if not isinstance(d, dict) or chat._ts(d.get("pulsacao")) is None:
        return None
    usd, forn, vs = d.get("dia_usd"), d.get("voz_fornecedor"), d.get("vistos")
    return {"pulsacao": chat._ts(d["pulsacao"]),
            "versao": d["versao"] if type(d.get("versao")) is int else None,
            "voz_modo": d["voz_modo"] if d.get("voz_modo") in chat.VOZES else None,
            "voz_fornecedor": forn if isinstance(forn, str) and chat.ANFITRIAO.fullmatch(forn) else None,
            "permissoes": d["permissoes"] if d.get("permissoes") in chat.PERMISSOES else None,
            "dia_usd": _usd(usd),
            "vistos": frozenset(n for n in vs[:VISTOS_MAX] if isinstance(n, str) and chat.NOME_FILA.fullmatch(n))
            if isinstance(vs, list) else frozenset()}


def estado_ponte(p: dict | None, agora: float) -> str | None:
    # None = a ponte está viva e com o mesmo contrato; senão a mensagem do 503
    if p is None or agora - p["pulsacao"] > chat.PULSACAO_S:
        return chat.PONTE_PARADA
    return chat.VERSOES if p["versao"] != CONTRATO else None


def nomes(pasta: Path) -> list[str]:
    try:
        return sorted(e.name for e in os.scandir(pasta) if chat.NOME_FILA.fullmatch(e.name))
    except OSError:
        return []


def por_ver(verificados: Path, vistos: frozenset) -> list[dict]:
    # os pedidos válidos em verificados/ que a ponte ainda não viu (os vistos não contam: o guardião apaga-os já)
    out = []
    for nome in nomes(verificados):
        if nome not in vistos:
            d = chat.ler_json(verificados / nome, chat.FILA_MAX)
            if chat.validar_fila(d) is None:
                out.append(d)
    return out


def _na_fila(pend: list[dict], conversa: str, acao: str | None = None, nonce: str | None = None) -> bool:
    return any(d["conversa"] == conversa and (acao is None or d["acao"] == acao)
               and (nonce is None or d.get("nonce") == nonce) for d in pend)


def verificar_pedido(meta: dict, d: dict, pend: list[dict], agora: float) -> tuple[int, str] | None:
    # o chat.verificar_pedido contra os pedidos por ver (sem os vistos)
    acao = d["acao"]
    if meta["arquivada"]:
        return 409, chat.ARQUIVADA
    if acao in ("enviar", "arquivar"):
        if meta["estado"] in chat.OCUPADO or _na_fila(pend, meta["id"], "enviar"):
            return 409, chat.A_TRABALHAR
    elif acao in chat.TIPO_DE:
        p = chat.pendente(meta, d["nonce"], chat.TIPO_DE[acao], agora)
        if p is None or _na_fila(pend, meta["id"], nonce=d["nonce"]):
            return 409, chat.APROVACAO_INVALIDA
        if acao == "responder_permissao" and d["escolha"] == "conversa" and p["delegacao"]:
            return 400, chat.DELEGACAO_CONVERSA
    return None


# --- o guardião -------------------------------------------------------------------------------------------------------
class Guardiao:
    """O estado partilhado pelos pedidos HTTP e pela limpeza. `agora` vem sempre de fora (testes)."""

    def __init__(self, cfg: Cfg, web: dict[str, bytes], relogio=time.time):
        self.cfg, self.web, self.relogio = cfg, web, relogio
        self.verificados = cfg.pasta / "verificados"
        self.conversas = cfg.pasta / "conversas"
        self.publico = cfg.pasta / "publico" / "publico.json"
        self.sessoes, self.bloqueio = chat.Sessoes(), chat.Bloqueio()
        self.prefs_lock = threading.Lock()
        self.registo_lock = threading.Lock()    # dois registos com o mesmo código: só um passa
        self.desafios_lock = threading.Lock()
        self.desafios: dict[str, float] = {}     # desafio → até quando vale (uso único)
        d = chat.ler_json(cfg.estado / "pids.json", 1024 * 1024)
        self.pids = {k: v for k, v in d.items() if isinstance(k, str) and type(v) is int} \
            if isinstance(d, dict) else {}
        self._publico = (None, 0.0)   # (conteúdo sem `em`, quando se escreveu)

    @property
    def conv(self) -> dict:
        c = self.cfg
        return {"nome": c.nome, "permissoes": c.permissoes, "voz": c.voz, "voz_idioma": c.voz_idioma}

    def iso(self, ts: float) -> str:
        return chat.agora_iso(ts, self.cfg.tz)

    def prefs(self) -> dict:
        return chat.ler_prefs(self.cfg.estado)

    def nome(self, prefs: dict) -> str:
        return prefs["nome"] or self.cfg.nome

    # --- desafios da entrada (só em memória) ---
    def novo_desafio(self, agora: float) -> str:
        with self.desafios_lock:
            self.desafios = {k: t for k, t in self.desafios.items() if agora < t}
            while len(self.desafios) >= DESAFIOS_MAX:
                del self.desafios[next(iter(self.desafios))]
            d = os.urandom(32).hex()
            self.desafios[d] = agora + DESAFIO_S
            return d

    def gastar_desafio(self, desafio: str, agora: float) -> bool:
        # vale uma vez, dentro dos 2 min; usado ou não, sai
        with self.desafios_lock:
            ate = self.desafios.pop(desafio, None)
        return ate is not None and agora < ate

    # --- registo de um browser (dentro do registo_lock) ---
    def registar(self, kid: str, chave: str, agora: float) -> None:
        """Apaga o `entrada.json` (o código morre primeiro: uma falha a seguir nunca deixa o código vivo) e junta a
        chave (o mesmo `kid` substitui-se; além de CHAVES_MAX sai a mais antiga). OSError se não der."""
        os.unlink(self.cfg.estado / "entrada.json")
        chaves = [c for c in ler_chaves(self.cfg.estado) if c["kid"] != kid]
        chaves = (chaves + [{"kid": kid, "chave": chave, "criada": self.iso(agora)}])[-CHAVES_MAX:]
        gravar(self.cfg.estado / "chaves.json", json.dumps({"v": 1, "chaves": chaves}).encode() + b"\n", 0o600)

    # --- pid (dentro do chat.LOCK) ---
    def pid_usado(self, d: dict, agora: float) -> bool:
        self.pids = {p: t for p, t in self.pids.items() if abs(t / 1000 - agora) <= PIDS_S}
        return d["pid"] in self.pids

    def gastar_pid(self, d: dict) -> None:
        self.pids[d["pid"]] = d["t"]
        try:
            gravar(self.cfg.estado / "pids.json", json.dumps(self.pids).encode(), 0o600)
        except OSError as e:   # fica em memória; no reinício o `t` já está quase fora da janela
            print(f"guardiao: pids.json não gravado ({e.strerror})", file=sys.stderr, flush=True)

    def estado_publico(self, agora: float, tls: bool, sessao: dict | None, bloqueado_ate: str | None) -> dict:
        # o corpo do GET /api/conversa/estado (o do chat.estado_publico, com a ponte do contrato 2): `entrada` = há um
        # código por usar; `desafio` só com TLS e sem sessão (para o POST entrar)
        entrada = chat.ler_entrada(self.cfg.estado)
        prefs = self.prefs()
        p = pulsacao(self.conversas)
        viva = estado_ponte(p, agora) is None
        forn = p["voz_fornecedor"] if viva else None
        return {"v": 1, "tls": tls, "sessao": sessao is not None, "csrf": sessao["csrf"] if sessao else None,
                "bloqueado_ate": bloqueado_ate, "entrada": entrada is not None,
                "sal_office": entrada["sal_office"] if entrada else None,
                "desafio": self.novo_desafio(agora) if tls and sessao is None else None,
                "iteracoes": entrada["iteracoes"] if entrada else None,
                "nome": self.nome(prefs), "nome_omissao": self.cfg.nome,
                "permissoes": chat.permissoes_efectivas(self.cfg.permissoes, p["permissoes"] if viva else None),
                "permissoes_config": self.cfg.permissoes, "voz": self.cfg.voz,
                "voz_ponte": p["voz_modo"] if viva else None, "voz_fornecedor": forn,
                "voz_idioma": self.cfg.voz_idioma, "aviso_tokens": prefs["aviso_tokens"] is not None,
                "aviso_voz": chat.aviso_voz_ok(prefs, forn), "ponte": viva,
                "versoes_ok": p is not None and p["versao"] == CONTRATO,
                "dia_usd": p["dia_usd"] if viva else None, "modelos": list(chat.MODELOS)}

    # --- limpeza e publico.json ---
    def limpar(self, agora: float) -> None:
        p = pulsacao(self.conversas)
        vistos = p["vistos"] if p else frozenset()
        morta = estado_ponte(p, agora) is not None
        for nome in nomes(self.verificados):
            f = self.verificados / nome
            if nome not in vistos and not (morta and _idade(f, agora) > ORFAO_S):
                continue
            d = chat.ler_json(f, chat.FILA_MAX)
            if isinstance(d, dict) and d.get("acao") == "transcrever" and isinstance(d.get("audio"), str) \
                    and isinstance(d.get("formato"), str):
                chat.apagar_audio(self.verificados, f"{d['audio']}.{d['formato']}")
            try:
                f.unlink()
            except OSError:
                pass
        if morta:
            pasta_audio = self.verificados / chat.AUDIO_DIR
            for nome in _listar(pasta_audio):
                if chat.NOME_AUDIO.fullmatch(nome) and _idade(pasta_audio / nome, agora) > ORFAO_S:
                    chat.apagar_audio(self.verificados, nome)

    def dados_publicos(self, agora: float) -> dict:
        p = pulsacao(self.conversas)
        viva = estado_ponte(p, agora) is None
        pend = 0
        for cid in chat.ids(self.conversas):
            m = chat.ler_meta(self.conversas, cid)
            if m is not None and not m["arquivada"]:
                pend += chat._vivos(m["pendentes"], agora)
        return {"nome": self.nome(self.prefs()), "ativa": viva, "pendentes": min(pend, PENDENTES_PUBLICO_MAX),
                "dia_usd": p["dia_usd"] if viva else None}

    def escrever_publico(self, agora: float) -> None:
        # §2.5: só quando muda ou quando tem mais de 30 s (o Office trata como parada uma com mais de 120 s)
        d = self.dados_publicos(agora)
        antes, quando = self._publico
        if d == antes and agora - quando < PUBLICO_S:
            return
        try:
            gravar(self.publico, json.dumps({"v": 1, "em": self.iso(agora), **d}, ensure_ascii=False).encode(),
                   0o644)
            self._publico = (d, agora)
        except OSError as e:
            print(f"guardiao: publico.json não gravado ({e.strerror})", file=sys.stderr, flush=True)

    def ciclo(self, parar: threading.Event) -> None:
        while not parar.wait(LIMPEZA_S):
            self.passo_limpeza()

    def passo_limpeza(self) -> None:
        agora = self.relogio()
        try:
            self.limpar(agora)
            self.escrever_publico(agora)
        except Exception as e:   # noqa: BLE001 - a limpeza nunca derruba o guardião
            print(f"guardiao: limpeza falhou ({type(e).__name__})", file=sys.stderr, flush=True)


def _idade(p: Path, agora: float) -> float:
    try:
        return agora - os.lstat(p).st_mtime
    except OSError:
        return 0.0


def _listar(p: Path) -> list[str]:
    try:
        return os.listdir(p)
    except OSError:
        return []


def gravar(path: Path, dados: bytes, modo: int) -> None:
    # tmp + fsync + os.replace na mesma pasta (quem lê nunca vê meio ficheiro)
    tmp = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
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


# --- HTTP -------------------------------------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "office-conversa"
    sys_version = ""
    timeout = 30

    @property
    def g(self) -> Guardiao:
        return self.server.g

    def log_message(self, fmt, *args):   # sem IPs nem caminhos no journal
        pass

    def end_headers(self):
        for k, v in SEGURANCA:
            self.send_header(k, v)
        self.send_header("Cache-Control", getattr(self, "_cc", "no-store"))
        super().end_headers()

    def _json(self, code: int, body: bytes, cookie: str | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _out(self, code: int, d: dict) -> None:
        self._json(code, json.dumps(d, ensure_ascii=False).encode())

    def _err(self, code: int, msg: str) -> None:
        self._out(code, {"error": msg})

    def _ip(self) -> str:
        ip = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
        return ip or (self.client_address[0] if isinstance(self.client_address, tuple) else "")

    # --- entrada: TLS, origem, sessão, CSRF, ritmo ---
    def _tls(self) -> bool:
        # o socket só é alcançável pelo grupo do proxy: o X-Forwarded-Proto dele conta sem segredo
        return self.g.cfg.tls_teste or self.headers.get("X-Forwarded-Proto", "").strip().lower() == "https"

    def _origem_ok(self) -> bool:
        host, origin = self.headers.get("Host", "").strip().lower(), self.headers.get("Origin")
        if origin is None:
            return self.headers.get("Sec-Fetch-Site") == "same-origin"
        aceites = {f"https://{host}"} | ({f"http://{host}"} if self.g.cfg.tls_teste else set())
        return bool(host) and origin.strip().lower() in aceites

    def _sessao(self, agora: float) -> dict | None:
        # a sessão vale enquanto o `kid` dela estiver em chaves.json (o --revogar fecha-as todas)
        k = chat.cookie(self.headers.get("Cookie"), COOKIE)
        for c in ler_chaves(self.g.cfg.estado):
            s = self.g.sessoes.obter(k, c["kid"], agora)
            if s is not None:
                return s
        return None

    def _abrir_sessao(self, kid: str, agora: float) -> None:
        g = self.g
        g.bloqueio.sucesso()
        k, s = g.sessoes.abrir(kid, agora)
        body = json.dumps({"ok": True, "csrf": s["csrf"], "expira": g.iso(agora + chat.SESSAO_S)}).encode()
        self._json(200, body, f"{COOKIE}={k}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age={chat.SESSAO_S}")

    def _bloqueado(self, agora: float) -> bool:
        ate = self.g.bloqueio.bloqueado_ate(agora)
        if ate is not None:
            self._out(429, {"error": chat.BLOQUEADO, "ate": self.g.iso(ate)})
        return ate is not None

    def _falhou(self, agora: float, code: int, msg: str) -> None:
        self.g.bloqueio.falhou(agora)
        self._err(code, msg)

    def _base(self, com_sessao: bool = True) -> tuple[dict | None, float] | None:
        # POST: 403 TLS → 403 origem → 401 sessão → 403 CSRF → 429 ritmo; None = já respondeu
        if not self._tls():
            self._err(403, chat.SEM_TLS)
            return None
        if not self._origem_ok():
            self._err(403, chat.ORIGEM)
            return None
        agora = self.g.relogio()
        if not com_sessao:
            return None, agora
        s = self._sessao(agora)
        if s is None:
            self._err(401, chat.SESSAO_INVALIDA)
            return None
        csrf = self.headers.get("X-Csrf", "")
        if not hmac.compare_digest(csrf.encode("utf-8", "surrogateescape"), s["csrf"].encode()):
            self._err(403, chat.CSRF_INVALIDO)
            return None
        if not self.g.sessoes.ritmo(s, agora):
            self._err(429, chat.RITMO)
            return None
        return s, agora

    def _corpo(self, max_size: int):
        # 400 tamanho → 413 → 415 tipo → 400 JSON; None = já respondeu
        cl = self.headers.get("Content-Length", "")
        size = int(cl) if cl.isascii() and cl.isdigit() else -1
        if size < 0:
            self._err(400, "JSON inválido")
            return None
        if size > max_size:
            self._err(413, "corpo grande demais")
            return None
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._err(415, "Content-Type inválido")
            return None
        try:
            data = json.loads(self.rfile.read(size))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._err(400, "JSON inválido")
            return None
        return data

    def _assinado(self, s: dict, data) -> bool:
        # 403 assinatura com a chave do `kid` da sessão (antes da forma: nada fica escrito)
        chave = chave_de(ler_chaves(self.g.cfg.estado), s["vsha"])
        if chave is None or not chat.assinatura_ok(chave, data):
            self._err(403, ASSINATURA)
            return False
        return True

    # --- GET ---
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path.startswith("/api/conversa/"):
            self._get_api(path, query)
            return
        nome = "conversa.html" if path == "/" else path[1:]
        corpo = self.g.web.get(nome) if path.startswith("/") else None
        if corpo is None:
            self._err(404, chat.NAO_ENCONTRADO)
            return
        self._cc = "no-cache"
        self.send_response(200)
        self.send_header("Content-Type", TIPOS[nome.rsplit(".", 1)[1]])
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _get_api(self, path: str, query: str) -> None:
        # 404 → estado (sem sessão) | 403 TLS → 401 sessão → 400 parâmetros → 404 conversa
        if path not in GET_API:
            self._err(404, chat.NAO_ENCONTRADO)
            return
        g = self.g
        agora, tls = g.relogio(), self._tls()
        s = self._sessao(agora) if tls else None
        if path == "/api/conversa/estado":
            ate = g.bloqueio.bloqueado_ate(agora)
            self._out(200, g.estado_publico(agora, tls, s, None if ate is None else g.iso(ate)))
            return
        if not tls:
            self._err(403, chat.SEM_TLS)
            return
        if s is None:
            self._err(401, chat.SESSAO_INVALIDA)
            return
        qs = {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()}
        viva = estado_ponte(pulsacao(g.conversas), agora) is None
        if path == "/api/conversa/lista":
            q = qs.get("q") or None
            if q is not None and not chat.Q_MIN <= len(q) <= chat.Q_MAX:
                self._err(400, chat.Q_INVALIDA)
                return
            out = {"v": 1, "ponte": viva, "conversas": chat.listar(g.conversas, q, agora)}
        else:
            desde = qs.get("desde", "0")
            if not DESDE.fullmatch(desde):
                self._err(400, chat.DESDE_INVALIDO)
                return
            cid = qs.get("conversa")
            meta = chat.ler_meta(g.conversas, cid)
            if meta is None:
                self._err(404, chat.NAO_ENCONTRADO)
                return
            evs, seguinte, mais = chat.ler_eventos(g.conversas / cid / "eventos.jsonl", int(desde))
            out = {"v": 1, "conversa": cid, "titulo": reader.mask(meta["titulo"]), "estado": meta["estado"],
                   "arquivada": meta["arquivada"], "ponte": viva,
                   "eventos": [chat.mascarar_evento(e) for e in evs], "seguinte": seguinte, "mais": mais}
        self._out(200, out)

    # --- POST ---
    def do_POST(self):
        path = self.path.partition("?")[0]
        rotas = {"/api/conversa/registar": self._post_registar, "/api/conversa/entrar": self._post_entrar,
                 "/api/conversa/pedido": self._post_pedido,
                 "/api/conversa/audio": self._post_audio, "/api/conversa/prefs": self._post_prefs}
        if path not in rotas or (path == "/api/conversa/audio" and self.g.cfg.voz == "desligada"):
            self._err(404, chat.NAO_ENCONTRADO)
            return
        rotas[path]()

    def _post_registar(self):
        # base sem sessão → 429 bloqueado → 400 corpo → 503 sem código nem chaves → 403 código já usado ou errado
        # (+1 falha) → 503 estado → 200 (chave gravada, código morto, sessão aberta)
        r = self._base(com_sessao=False)
        if r is None:
            return
        g, agora = self.g, r[1]
        if self._bloqueado(agora):
            return
        data = self._corpo(SESSAO_POST_MAX)
        if data is None:
            return
        if set(data) != {"verificador", "kid", "chave"} or not all(isinstance(v, str) for v in data.values()) \
                or not chat.HEX64.fullmatch(data["verificador"]) or not KID.fullmatch(data["kid"]) \
                or not chat.HEX64.fullmatch(data["chave"]):
            self._err(400, "registo inválido")
            return
        with g.registo_lock:
            entrada = chat.ler_entrada(g.cfg.estado)
            if entrada is None:
                if ler_chaves(g.cfg.estado):
                    self._falhou(agora, 403, CODIGO_USADO)
                else:
                    self._err(503, chat.SEM_ENTRADA)
                return
            if not chat.verificador_ok(entrada, data["verificador"]):
                self._falhou(agora, 403, chat.CODIGO_ERRADO)
                return
            try:
                g.registar(data["kid"], data["chave"], agora)
            except OSError as e:
                print(f"guardiao: registo não gravado ({e.strerror})", file=sys.stderr, flush=True)
                self._err(503, ESTADO_INDISPONIVEL)
                return
        self._abrir_sessao(data["kid"], agora)

    def _post_entrar(self):
        # base sem sessão → 429 bloqueado → 400 corpo → 403 desafio (+1 falha) → 403 kid ou assinatura (+1 falha) → 200
        r = self._base(com_sessao=False)
        if r is None:
            return
        g, agora = self.g, r[1]
        if self._bloqueado(agora):
            return
        data = self._corpo(SESSAO_POST_MAX)
        if data is None:
            return
        if set(data) != {"kid", "desafio", "assinatura"} or not all(isinstance(v, str) for v in data.values()) \
                or not KID.fullmatch(data["kid"]) or not chat.HEX64.fullmatch(data["desafio"]) \
                or not chat.HEX64.fullmatch(data["assinatura"]):
            self._err(400, ENTRADA_INVALIDA)
            return
        if not g.gastar_desafio(data["desafio"], agora):
            self._falhou(agora, 403, DESAFIO_INVALIDO)
            return
        chave = chave_de(ler_chaves(g.cfg.estado), data["kid"])
        certa = hmac.new(chave or b"", f"entrar\n{data['desafio']}".encode(), "sha256").hexdigest()
        if chave is None or not hmac.compare_digest(certa, data["assinatura"]):
            self._falhou(agora, 403, ASSINATURA)
            return
        self._abrir_sessao(data["kid"], agora)

    def _post_pedido(self):
        # base → 400/413/415 corpo → 403 assinatura → 400 forma, relógio e permissões → 409 aviso de
        # tokens → 503 ponte → 404 conversa → 409 pid repetido → 409/400 contra o meta e os pedidos por ver → 503
        # verificados/ → 202
        r = self._base()
        if r is None:
            return
        g, agora = self.g, r[1]
        data = self._corpo(chat.FILA_MAX)
        if data is None or not self._assinado(r[0], data):
            return
        msg = chat.validar_post(data)
        if msg:
            self._err(400, msg)
            return
        if not chat.tempo_ok(data, agora):
            self._err(400, chat.RELOGIO)
            return
        if data["acao"] == "enviar":
            if data["permissoes"] != g.cfg.permissoes:
                self._err(400, chat.PERMISSOES_DIFEREM)
                return
            if g.prefs()["aviso_tokens"] is None:
                self._err(409, chat.AVISO_TOKENS)
                return
        p = pulsacao(g.conversas)
        msg = estado_ponte(p, agora)
        if msg:
            self._err(503, msg)
            return
        cid = data["conversa"]
        with chat.LOCK:   # as verificações contra verificados/ e a escrita de uma vez (dois POST iguais: um 409)
            meta = None
            if data["acao"] != "nova":
                meta = chat.ler_meta(g.conversas, cid)
                if meta is None:
                    self._err(404, chat.NAO_ENCONTRADO)
                    return
            if g.pid_usado(data, agora):
                self._err(409, REPETIDO)
                return
            pend = por_ver(g.verificados, p["vistos"])
            if meta is None:
                falha = (409, chat.JA_EXISTE) if chat.existe(g.conversas, cid) or _na_fila(pend, cid, "nova") \
                    else None
            else:
                falha = verificar_pedido(meta, data, pend, agora)
            if falha:
                self._err(*falha)
                return
            doc = chat.doc_fila(data, self._ip(), self.headers.get("User-Agent", ""), g.iso(agora))
            code, out = chat.escrever_pedido(g.verificados, doc, agora, g.cfg.tz)
            if code == 202:
                g.gastar_pid(data)
        self._out(code, {"ok": True, "conversa": cid} if code == 202 else out)

    def _post_prefs(self):
        # base → corpo → 400 forma → 409 sem fornecedor (aviso_voz) → 503 sem pasta → 200. Não vai para verificados/
        r = self._base()
        if r is None:
            return
        g, agora = self.g, r[1]
        data = self._corpo(PREFS_POST_MAX)
        if data is None:
            return
        msg = chat.validar_prefs(data)
        if msg:
            self._err(400, msg)
            return
        p = pulsacao(g.conversas)
        forn = p["voz_fornecedor"] if estado_ponte(p, agora) is None else None
        (chave, valor), = data.items()
        with g.prefs_lock:
            prefs = g.prefs()
            if chave == "nome":
                prefs["nome"] = valor
            elif chave == "aviso_tokens":
                prefs["aviso_tokens"] = g.iso(agora)
            elif forn is None:
                self._err(409, chat.SEM_FORNECEDOR)
                return
            else:
                prefs["aviso_voz"] = {"fornecedor": forn, "em": g.iso(agora)}
            if not chat.gravar_prefs(g.cfg.estado, prefs):
                self._err(503, chat.PREFS_INDISPONIVEIS)
                return
        self._out(200, {"ok": True, "nome": g.nome(prefs), "aviso_tokens": prefs["aviso_tokens"] is not None,
                        "aviso_voz": chat.aviso_voz_ok(prefs, forn)})

    def _post_audio(self):
        # base → 400/413/400 tamanho → 415 tipo → 400 cabeçalho → 403 assinatura → 400 forma, relógio
        # e o que não bate → 503 ponte / voz não instalada → 403 aviso da externa → 404/409 conversa → 400 corpo curto
        # ou sha256 → 415 magic bytes → 409 pid repetido → 429 um de cada vez → 503 verificados/ → 202
        r = self._base()
        if r is None:
            return
        g, agora, conv = self.g, r[1], self.g.conv
        cl = self.headers.get("Content-Length", "")
        size = int(cl) if cl.isascii() and cl.isdigit() else -1
        if size < 0:
            self._err(400, chat.AUDIO_TAMANHO)
            return
        if size > chat.AUDIO_MAX:   # sem ler o corpo (HTTP/1.0: a ligação fecha)
            self._err(413, chat.AUDIO_GRANDE)
            return
        if size < chat.AUDIO_MIN:
            self._err(400, chat.AUDIO_VAZIO)
            return
        ext = chat.tipo_audio(self.headers.get("Content-Type"))
        if ext is None:
            self._err(415, chat.AUDIO_FORMATO)
            return
        try:
            data = json.loads(self.headers.get("X-Conversa-Pedido", ""))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._err(400, "acção inválida")
            return
        if not self._assinado(r[0], data):
            return
        msg = chat.validar_post(data, audio=True)
        if msg:
            self._err(400, msg)
            return
        if not chat.tempo_ok(data, agora):
            self._err(400, chat.RELOGIO)
            return
        if data["formato"] != ext or data["bytes"] != size or data["idioma"] != conv["voz_idioma"]:
            self._err(400, chat.AUDIO_NAO_BATE)
            return
        p = pulsacao(g.conversas)
        msg = estado_ponte(p, agora)
        if msg:
            self._err(503, msg)
            return
        if p["voz_modo"] != conv["voz"]:
            self._err(503, chat.sem_voz(conv["voz"]))
            return
        if conv["voz"] == "externa" and not chat.aviso_voz_ok(g.prefs(), p["voz_fornecedor"]):
            self._err(403, chat.AVISO_VOZ)
            return
        cid = data["conversa"]
        meta = chat.ler_meta(g.conversas, cid)
        if meta is None:
            self._err(404, chat.NAO_ENCONTRADO)
            return
        if meta["arquivada"]:
            self._err(409, chat.ARQUIVADA)
            return
        dados = self.rfile.read(size)
        if len(dados) < size:
            self._err(400, chat.AUDIO_TAMANHO)
            return
        if not hmac.compare_digest(hashlib.sha256(dados).hexdigest(), data["sha256"]):
            self._err(400, chat.AUDIO_NAO_BATE)
            return
        if not chat.magic_ok(ext, dados[:16]):
            self._err(415, chat.AUDIO_FORMATO)
            return
        nome = f"{data['audio']}.{ext}"
        with chat.LOCK:
            if g.pid_usado(data, agora):
                self._err(409, REPETIDO)
                return
            # um de cada vez: um `transcrever` ainda por ver pela ponte (os vistos já não contam)
            if any(d["acao"] == "transcrever" for d in por_ver(g.verificados, p["vistos"])):
                self._err(429, chat.AUDIO_OCUPADO)
                return
            if not chat.gravar_audio(g.verificados, nome, dados):
                self._err(503, chat.FILA_INDISPONIVEL)
                return
            doc = chat.doc_fila(data, self._ip(), self.headers.get("User-Agent", ""), g.iso(agora))
            code, out = chat.escrever_pedido(g.verificados, doc, agora, g.cfg.tz)
            if code == 202:
                g.gastar_pid(data)
            else:
                chat.apagar_audio(g.verificados, nome)
        self._out(code, {"ok": True, "audio": data["audio"]} if code == 202 else out)


class ServidorUnix(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class ServidorTcp(ThreadingHTTPServer):
    daemon_threads = True


def servidor(escuta: str, g: Guardiao):
    """`unix:<caminho absoluto>` (socket 0660; o grupo vem da pasta setgid) ou `127.0.0.1:<porta>` (testes)."""
    if escuta.startswith("unix:"):
        caminho = escuta[5:]
        if not caminho.startswith("/"):
            raise ValueError(f"CONVERSA_ESCUTA: {escuta}")
        try:
            if stat.S_ISSOCK(os.lstat(caminho).st_mode):
                os.unlink(caminho)
        except FileNotFoundError:
            pass
        srv = ServidorUnix(caminho, Handler)
        os.chmod(caminho, 0o660)
    else:
        host, _, porta = escuta.rpartition(":")
        if host not in ("127.0.0.1", "localhost") or not porta.isascii() or not porta.isdigit():
            raise ValueError(f"CONVERSA_ESCUTA: {escuta} (só unix:<caminho> ou 127.0.0.1:<porta>)")
        srv = ServidorTcp((host, int(porta)), Handler)
    srv.g = g
    return srv


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["--help"], ["-h"]):
        print(__doc__.split("\n\n", 1)[0])
        return 0
    cfg = config()
    try:
        web = conferir(cfg.opt)
        g = Guardiao(cfg, web)
        srv = servidor(cfg.escuta, g)
    except (Recusa, ValueError, OSError) as e:
        print(f"guardiao: {e}", file=sys.stderr, flush=True)
        return SAIDA_CONFIG
    parar = threading.Event()
    threading.Thread(target=g.ciclo, args=(parar,), daemon=True).start()

    def fim(*_):
        parar.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, fim)
    signal.signal(signal.SIGINT, fim)
    g.passo_limpeza()
    print(f"guardiao: a ouvir em {cfg.escuta}", file=sys.stderr, flush=True)
    srv.serve_forever()
    srv.server_close()
    if cfg.escuta.startswith("unix:"):
        try:
            os.unlink(cfg.escuta[5:])
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
