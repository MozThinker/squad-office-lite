#!/usr/bin/env python3
"""Ponte da Conversa (ADR-023): liga os pedidos do guardião a um `claude -p` stream-json por turno, como o `dono`.

Unit systemd de utilizador do dono (`office-conversa.service`, instalada pelo `instalar-conversa.sh`), um processo, uma
thread, `flock` em `<ESTADO>/ponte.lock`, sem porta TCP. Porte do `scripts/chat_ponte.py` do Squad Office, sem o
contexto da fábrica, o livro, as `decisoes/` nem o brief. Cada `Ponte.passo()` faz, por esta ordem: stdout dos motores
→ socket das permissões → verificados/ e depois o tique (expirações de 10 min, tectos, SIGKILL atrasado, pulsação).

- Pedidos (contrato §2.4, revisão S2/S5): o guardião (`chat_host/guardiao.py`, uid `office-conversa`) confere a
  assinatura HMAC, o `t` e o `pid` e escreve os aceites em `CONVERSA_VERIFICADOS_DIR`, onde só ele escreve. A ponte
  não tem a chave: lê essa pasta por ordem de nome, recusa um `t` fora de 5 min (auditoria `fora_de_tempo`), junta o
  nome a `<ESTADO>/vistos.json` e publica-o na pulsação (`vistos`); o guardião apaga o que foi visto. Nunca apaga nada.
- Motor: `claude -p --input-format stream-json --output-format stream-json --verbose [--resume <sessão>] --max-turns N
  --permission-prompt-tool mcp__ponte__permissao --mcp-config <RUN>/turno-<id>.json --settings <ESTADO>/settings-<modo>
  .json [--model …] [--permission-mode plan|auto]`, cwd CONVERSA_CWD, grupo de processos próprio, ambiente por lista
  branca (nunca ANTHROPIC_*, CLAUDE_CODE_* nem CONVERSA_STT_*): usa as credenciais que o `claude` do dono já tem.
- Permissões (§2.4): `espelho` só se a unit (CONVERSA_PERMISSOES) **e** o `enviar` disserem `espelho`; senão `cartao`.
  Em `espelho` o motor corre com `--permission-mode auto` e só os lançadores do kit (CONVERSA_LANCADORES) pedem cartão;
  em `cartao` também toda a acção de Bash/Write/Edit/MultiEdit/NotebookEdit/WebFetch. Um comando que invoca um
  lançador é uma delegação: cartão sempre, sem «Aprovar nesta conversa». Nunca `allow` por omissão.
- Escreve: `conversas/<id>/{eventos.jsonl, meta.json}` e `conversas/estado.json` (0640, o guardião lê pela ACL) e,
  fora de qualquer mount, `<ESTADO>/` (vistos, gasto, sessões, regras, turnos, stderr, auditoria só de acrescento).
- Voz (CONVERSA_VOZ): `local` (faster-whisper no venv do dono, só com CONVERSA_STT_RAM_MB livres) ou `externa`
  (`/v1/audio/transcriptions` de um fornecedor; a chave só passa ao trabalhador). O texto nunca é instrução: não
  arranca turno nem responde a pendentes.
Só stdlib; Python ≥ 3.11 (o reader.py usa quantificadores possessivos). Variáveis: ver o `office-conversa.service` e OMISSAO.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import resource
import secrets
import selectors
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ))   # chat.py e reader.py (com -I a pasta do script não entra no sys.path)

import chat  # noqa: E402
import reader  # noqa: E402

H = Path.home()
OMISSAO = {
    "CONVERSA_CLAUDE": shutil.which("claude") or str(H / ".local/bin/claude"),
    "CONVERSA_CWD": str(H),
    "CONVERSA_VERIFICADOS_DIR": "/srv/office-conversa/verificados",
    "CONVERSAS_DIR": "/srv/office-conversa/conversas",
    "CONVERSA_ESTADO_DIR": str(H / ".local/state/office-conversa"),
    "CONVERSA_PERMISSOES": "cartao",
    "CONVERSA_LANCADORES": "lancar:continuar:diretor:squad",
    "CONVERSA_FUSO": "UTC",
    "CONVERSA_VOZ": "desligada",
}
ENV_MOTOR = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "XDG_RUNTIME_DIR")
MCP_TOOL_TIMEOUT = "660000"
EXPIRA_S = 600
MATAR_S = 5.0
PULSACAO_S = 5.0
CONTRATO = 2            # o guardião confere a assinatura; a ponte lê `verificados/` e publica `vistos`
VISTOS_MAX = 200        # nomes de `vistos` na pulsação
MENSAGEM_MAX = 32000
RESUMO_MAX, ALVO_MAX, RESULTADO_MAX, PASSO_MAX = 300, 2000, 2000, 120
AUDIT_ARGS_MAX = 2000
PEDIDO_MAX = 1024 * 1024
TITULO_MAX = 80
DURACAO_MAX_MIN, TURNOS_MAX = 30, 100
PERMISSOES = ("cartao", "espelho")
VOZES = ("desligada", "local", "externa")
ASK_CARTAO = ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch")
FERRAMENTAS_FICHEIRO = ("Edit", "Write", "MultiEdit", "NotebookEdit")
RX_NOME_LANCADOR = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")
FIM_S = 10.0            # depois do fim do turno, o motor tem isto para sair antes de se matar o grupo
LEITURA = frozenset({"Read", "Glob", "Grep", "LS", "NotebookRead"})   # só não podem tocar no estado da ponte
SEM_EFEITO = frozenset({"TodoWrite", "AskUserQuestion", "ExitPlanMode"})
SEM_RESPOSTA = "sem resposta do dono em 10 min"
SEM_PONTE = "a ponte não respondeu"
RECUSOU = "o dono recusou"
PAROU = "o dono parou o assistente"
TURNO_ACABOU = "o turno acabou antes da resposta"
CONTINUA = "continua a planear"
EDITOU = "O dono editou o plano. Segue esta versão e volta a apresentá-la com ExitPlanMode:\n\n"
ALVO_LONGO = "comando demasiado longo para mostrar no cartão; divide-o"
ALVO_SEGREDO = "o comando tem forma de segredo; usa uma variável já definida no ambiente"
ALVO_INVISIVEL = "o comando tem caracteres invisíveis, linhas em branco seguidas ou linhas a mais para o cartão; " \
    "reescreve-o sem isso"
ALVO_LINHAS_MAX, ALVO_BRANCAS_MAX = 80, 2
FORA_DE_TEMPO = "pedido fora de tempo (relógio do telemóvel?)"
# ficheiros e textos do contrato
ESTADO_PONTE = "estado.json"
AUDIO_DIR = "audio"
AUDIO_MIME = {"webm", "ogg", "m4a", "mp3"}
TRANSCRICAO_MAX = 4000
VOZ_INDISPONIVEL = "A voz não está instalada no servidor. Escreve a mensagem."
VOZ_TEMPO = "A transcrição demorou demais. Tenta outra vez ou escreve."
VOZ_FALHOU = "Não consegui transcrever o áudio. Tenta outra vez ou escreve."
VOZ_LONGO = "O áudio passa dos 2 min."
VOZ_SEM_MEMORIA = "Sem memória livre para a voz local: {livre} MB livres, precisa de {preciso} MB."
VOZ_CHAVE = "O fornecedor da voz recusou a chave."
VOZ_LIMITE = "O fornecedor da voz pediu para esperar (429)."
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# voz
STT_PYTHON = str(H / ".local/share/office-conversa/stt-venv/bin/python")
STT_TEMPO_S = 60.0
STT_RAM_MB = 2048       # MemAvailable mínimo para lançar o trabalhador local (o modelo `small` ocupa ≈ 1 GB)
STT_MEMORIA_MB = 4096   # RLIMIT_AS (memória virtual: conta as arenas e as pilhas das threads, não só o modelo)
STT_LOG_MAX = 1 << 20   # o stt.log acumula entre relançamentos e só se trunca acima disto
ENV_STT = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")

Cfg = namedtuple("Cfg", "claude cwd verificados conversas estado run lancadores permissoes tz voz duracao_max_min "
                        "turnos_max quem")


def _inteiro(env, k: str, omissao: int, mn: int, mx: int) -> int:
    try:
        v = int(env.get(k) or omissao)
    except ValueError:
        return omissao
    return v if mn <= v <= mx else omissao


def _fuso(nome: str):
    try:
        return ZoneInfo(nome)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def lancadores(valor: str) -> tuple[str, ...]:
    # nomes separados por «:» (os comandos do kit, não caminhos); os mal formados ignoram-se
    nomes = tuple(x.strip() for x in valor.split(":") if RX_NOME_LANCADOR.fullmatch(x.strip()))
    return nomes or tuple(OMISSAO["CONVERSA_LANCADORES"].split(":"))


def config(env=os.environ) -> Cfg:
    def v(k):
        return env.get(k) or OMISSAO[k]
    xdg = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Cfg(v("CONVERSA_CLAUDE"), Path(v("CONVERSA_CWD")), Path(v("CONVERSA_VERIFICADOS_DIR")),
               Path(v("CONVERSAS_DIR")), Path(v("CONVERSA_ESTADO_DIR")), Path(xdg) / "office-conversa",
               lancadores(v("CONVERSA_LANCADORES")),
               v("CONVERSA_PERMISSOES") if v("CONVERSA_PERMISSOES") in PERMISSOES else "cartao",
               _fuso(v("CONVERSA_FUSO")), v("CONVERSA_VOZ") if v("CONVERSA_VOZ") in VOZES else "desligada",
               _inteiro(env, "CONVERSA_DURACAO_MAX_MIN", DURACAO_MAX_MIN, 1, 24 * 60),
               _inteiro(env, "CONVERSA_TURNOS_MAX", TURNOS_MAX, 1, 10000),
               env.get("USER") or env.get("LOGNAME") or "dono")


# --- ficheiros --------------------------------------------------------------------------------------------------------
def _gravar(path: Path, dados: bytes, modo: int = 0o640) -> None:
    # tmp + fsync + os.replace na mesma pasta (quem lê nunca vê meio ficheiro)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
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


def _acrescentar(path: Path, linha: dict, modo: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, modo)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False) + "\n")


def _ler_json(path: Path, n: int = 1024 * 1024):
    d = chat.ler_json(path, n)
    return d if isinstance(d, dict) else None


def ultimo_n(path: Path) -> int:
    # o maior `n` válido no eventos.jsonl (uma meta atrasada nunca repete um n)
    maior = 0
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return 0
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return 0
        for ln in f:
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if isinstance(ev, dict) and type(ev.get("n")) is int and ev["n"] > maior:
                maior = ev["n"]
    return maior


def limitar_memoria(mb: int):
    # preexec_fn do trabalhador da voz: RLIMIT_AS de `mb` MiB (defesa em profundidade contra a bomba de áudio)
    if mb <= 0:
        return None

    def f() -> None:
        duro = resource.getrlimit(resource.RLIMIT_AS)[1]
        n = mb << 20 if duro == resource.RLIM_INFINITY else min(mb << 20, duro)
        resource.setrlimit(resource.RLIMIT_AS, (n, n))
    return f


def memoria_livre_mb(meminfo: str) -> int | None:
    # MemAvailable em MB (None se o ficheiro não disser: a ponte não bloqueia a voz por não saber)
    try:
        with open(meminfo, encoding="ascii", errors="replace") as f:
            for ln in f:
                if ln.startswith("MemAvailable:"):
                    return int(ln.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def fornecedor(url: str) -> str | None:
    # o anfitrião do URL da voz externa (o que a UI mostra no aviso); nunca o caminho nem credenciais
    try:
        u = urllib.parse.urlsplit(url.strip())
        return u.hostname if u.scheme in ("https", "http") and u.hostname else None
    except ValueError:
        return None


def texto_voz(t: str) -> str:
    # o texto de uma transcrição: sem C0 (salvo \n e \t) nem surrogates, mascarado, ≤ 4 000 caracteres
    t = "".join(c for c in t if not ((c < " " and c not in "\n\t") or "\ud800" <= c <= "\udfff"))
    return reader.mask(t.strip())[:TRANSCRICAO_MAX]


def _pasta_privada(p: Path) -> None:
    # 0700, nossa, directório de verdade (nunca um symlink)
    try:
        os.mkdir(p, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(p)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise OSError(errno.EPERM, f"{p}: não é uma pasta nossa")
    if st.st_mode & 0o077:
        os.chmod(p, 0o700)


def starttime(pid: int) -> str | None:
    # campo 22 de /proc/<pid>/stat (depois do «)» do nome): distingue um pid reutilizado
    try:
        s = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    campos = s[s.rfind(")") + 2:].split()
    return campos[19] if len(campos) > 19 else None


def _grupo_vivo(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --- delegação --------------------------------------------------------------------------------------------------------
def rx_lancadores(nomes) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(n) for n in nomes) + r")\b")


def e_delegacao(nome, entrada, rx: re.Pattern) -> bool:
    # um Bash que invoca um lançador do kit, depois de tirar aspas e barras invertidas (`"lancar"`, `lan\\car`)
    return nome == "Bash" and isinstance(entrada, dict) and isinstance(entrada.get("command"), str) \
        and rx.search(re.sub(r"[\"'\\\\]", "", entrada["command"])) is not None


# --- texto dos eventos ------------------------------------------------------------------------------------------------
def canonico(entrada) -> str:
    return json.dumps(entrada, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _s(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def alvo_recusa(texto: str) -> str | None:
    """O cartão tem de mostrar exactamente o que corre. Recusa (sem cartão) o que a máscara mudaria, o que não cabe,
    caracteres Unicode de formatação (bidi, largura zero: categoria Cf) e linhas em branco seguidas ou linhas a mais,
    que escondem a cauda do comando na caixa com scroll."""
    if reader.mask(texto) != texto:
        return ALVO_SEGREDO
    if len(texto) > ALVO_MAX:
        return ALVO_LONGO
    linhas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if any(unicodedata.category(c) == "Cf" for c in texto) or len(linhas) > ALVO_LINHAS_MAX or \
            re.search(r"(?:\n[ \t]*){%d,}\n" % (ALVO_BRANCAS_MAX + 1), "\n".join(linhas)):
        return ALVO_INVISIVEL
    return None


def alvo_bruto(nome, entrada) -> str:
    # o que o dono tem de ver inteiro no cartão: comando; ficheiro + conteúdo; URL; ou o input todo
    if not isinstance(entrada, dict):
        return ""
    if nome in FERRAMENTAS_FICHEIRO:
        caminho = _s(entrada.get("file_path") or entrada.get("notebook_path") or "")
        if nome == "Write":
            return f"{caminho}\n--- conteúdo ---\n{_s(entrada.get('content', ''))}"
        if nome == "Edit":
            todas = " (todas)" if entrada.get("replace_all") is True else ""
            return (f"{caminho}\n--- substitui{todas} ---\n{_s(entrada.get('old_string', ''))}\n--- por ---\n"
                    f"{_s(entrada.get('new_string', ''))}")
        if nome == "MultiEdit":
            eds = entrada.get("edits") if isinstance(entrada.get("edits"), list) else []
            partes = [caminho]
            for i, e in enumerate(eds, 1):
                e = e if isinstance(e, dict) else {}
                todas = " (todas)" if e.get("replace_all") is True else ""
                partes.append(f"--- {i}: substitui{todas} ---\n{_s(e.get('old_string', ''))}\n--- por ---\n"
                              f"{_s(e.get('new_string', ''))}")
            return "\n".join(partes)
        return (f"{caminho}\n--- célula {_s(entrada.get('cell_id', ''))} ({_s(entrada.get('edit_mode', 'replace'))})"
                f" ---\n{_s(entrada.get('new_source', ''))}")
    for k in ("command", "file_path", "notebook_path", "url"):
        if isinstance(entrada.get(k), str):
            return entrada[k]
    return json.dumps(entrada, ensure_ascii=False)


def alvo(nome, entrada) -> str:
    return reader.clip(alvo_bruto(nome, entrada), ALVO_MAX)


def resumo(nome, entrada) -> str:
    d = entrada.get("description") if isinstance(entrada, dict) else None
    return reader.clip(d if isinstance(d, str) and d.strip() else f"{nome}: {alvo(nome, entrada)}", RESUMO_MAX)


def _texto_resultado(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b["text"] for b in c if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _perguntas_ok(qs) -> bool:
    return isinstance(qs, list) and 1 <= len(qs) <= chat.PERGUNTAS_MAX and all(
        isinstance(q, dict) and isinstance(q.get("question"), str) and isinstance(q.get("options"), list)
        and 1 <= len(q["options"]) <= chat.OPCOES_MAX
        and all(isinstance(o, dict) and isinstance(o.get("label"), str) for o in q["options"]) for q in qs)


def _perguntas_evento(qs) -> list[dict]:
    return [{"pergunta": reader.clip(q["question"], 1000), "cabecalho": reader.clip(str(q.get("header") or ""), 120),
             "opcoes": [{"rotulo": reader.clip(o["label"], 200),
                         "descricao": reader.clip(str(o.get("description") or ""), 500)} for o in q["options"]],
             "multipla": q.get("multiSelect") is True} for q in qs]


# --- estruturas -----------------------------------------------------------------------------------------------------
class Pendente:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Turno:
    def __init__(self, cid, proc, token, inicio, mcp_path, ip, ua):
        self.cid, self.proc, self.token, self.inicio, self.mcp_path = cid, proc, token, inicio, mcp_path
        self.ip, self.ua = ip, ua
        self.buf = b""
        self.pend: dict[str, Pendente] = {}
        self.usos: dict[str, dict] = {}        # tool_use_id → {n, nome, input}
        self.cliques: set[str] = set()         # tool_use_ids de delegações aprovadas com clique
        self.fim = False
        self.fim_em = 0.0                      # monotónico do fim do turno
        self.morto = False                     # já se mandou SIGTERM ao grupo
        self.resume = False                    # arrancou com --resume
        self.init = False                      # viu o system/init
        self.lido = False                      # stdout fechado


class Ponte:
    def __init__(self, cfg: Cfg, relogio=time.time, env=os.environ):
        self.cfg, self.relogio, self.env = cfg, relogio, env
        self.rx_lanc = rx_lancadores(cfg.lancadores)
        self.sel = selectors.DefaultSelector()
        self.turnos: dict[str, Turno] = {}
        self.por_token: dict[str, Turno] = {}
        self.a_matar: list[tuple[int, float]] = []   # (pgid, monotónico do SIGKILL)
        self.restos: list[subprocess.Popen] = []      # motores esquecidos ainda por recolher
        self.metas: dict[str, dict] = {}
        self.vistos: dict[str, dict] = {}   # nome em verificados/ → {"em", "res"}
        self._pulso = 0.0
        self.sock = None
        # voz: o trabalhador (Popen), a tarefa em curso e as que esperam (uma de cada vez)
        self.stt, self.stt_buf, self.stt_atual, self.stt_fila = None, b"", None, []
        self.stt_python = env.get("CONVERSA_STT_PYTHON") or STT_PYTHON
        try:
            self.stt_tempo = float(env.get("CONVERSA_STT_TEMPO") or STT_TEMPO_S)
        except ValueError:
            self.stt_tempo = STT_TEMPO_S
        self.stt_memoria = _inteiro(env, "CONVERSA_STT_MEMORIA_MB", STT_MEMORIA_MB, 0, 1 << 20)
        self.stt_ram = _inteiro(env, "CONVERSA_STT_RAM_MB", STT_RAM_MB, 0, 1 << 20)
        self.meminfo = env.get("CONVERSA_MEMINFO") or "/proc/meminfo"
        self.voz_fornecedor = fornecedor(env.get("CONVERSA_STT_URL") or "") if cfg.voz == "externa" else None
        self.arrancar()

    def _iso(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.cfg.tz).isoformat(timespec="seconds")

    # --- arranque ---
    def arrancar(self) -> None:
        c = self.cfg
        c.estado.parent.mkdir(parents=True, exist_ok=True)
        _pasta_privada(c.estado)
        for sub in ("turnos", "stderr", "auditoria", "stt"):
            _pasta_privada(c.estado / sub)
        c.run.parent.mkdir(parents=True, exist_ok=True)
        _pasta_privada(c.run)
        self._arrancar_voz()
        self.settings = {}
        for modo in (("cartao", "espelho") if c.permissoes == "espelho" else ("cartao",)):
            self.settings[modo] = c.estado / f"settings-{modo}.json"
            _gravar(self.settings[modo], json.dumps(self._settings(modo), ensure_ascii=False, indent=1).encode(),
                    0o600)
        v = _ler_json(c.estado / "vistos.json")
        self.vistos = {k: x for k, x in v.items() if isinstance(k, str) and chat.NOME_FILA.fullmatch(k)
                       and isinstance(x, dict)} if isinstance(v, dict) else {}
        self._orfaos()
        agora = self.relogio()
        for cid in chat.ids(c.conversas):
            m = chat.ler_meta(c.conversas, cid)
            if m is None or m["estado"] not in chat.OCUPADO:
                continue
            m["n"] = max(m["n"], ultimo_n(c.conversas / cid / "eventos.jsonl"))
            self.metas[cid] = m
            for p in list(m["pendentes"]):
                self.evento(cid, p["tipo"], nonce=p["nonce"], resolvido="expirada")
            m["pendentes"] = []
            self.evento(cid, "aviso", codigo="ponte_reiniciou",
                        texto="A ponte reiniciou a meio do turno; o pedido em curso parou.")
            self._estado(cid, "parado", "a ponte reiniciou")
            self._fim(cid, "parado")
            self.auditar("arranque", conversa=cid, texto="turno interrompido pelo reinício da ponte")
        sp = c.run / "ponte.sock"
        try:
            os.unlink(sp)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(sp))
        os.chmod(sp, 0o600)
        self.sock.listen(16)
        self.sock.setblocking(False)
        self.sel.register(self.sock, selectors.EVENT_READ, ("escuta", None))
        self._pulsar(agora)

    def _settings(self, modo: str) -> dict:
        # nunca escrever nas pastas da Conversa nem ler o estado da ponte
        deny = [f"{f}(/{p}/**)" for p in (self.cfg.verificados, self.cfg.conversas, self.cfg.estado)
                for f in ("Write", "Edit", "NotebookEdit")] + [f"Read(/{self.cfg.estado}/**)"]
        ask = [f"Bash(*{x}*)" for x in self.cfg.lancadores]
        if modo == "cartao":   # toda a acção destas ferramentas pede cartão (ask ganha às allow)
            ask += list(ASK_CARTAO)
        return {"permissions": {"ask": ask, "deny": deny}}

    def _orfaos(self) -> None:
        # motores de uma ponte morta com SIGKILL (KillMode=process): mata o grupo se o starttime ainda bater
        pasta = self.cfg.estado / "turnos"
        for f in sorted(pasta.glob("*.json")):
            d = _ler_json(f, 4096)
            if d and type(d.get("pid")) is int and type(d.get("pgid")) is int and d["pgid"] > 1 \
                    and d.get("starttime") and starttime(d["pid"]) == d["starttime"]:
                self._matar_grupo(d["pgid"])
                self.auditar("orfao", pid=d["pid"], pgid=d["pgid"])
            try:
                f.unlink()
            except OSError:
                pass

    # --- passo ---
    def passo(self, espera: float = 0.5) -> None:
        prontos = self.sel.select(espera)
        ordem = {"motor": 0, "escuta": 1, "cliente": 2, "stt": 3}
        for key, _ in sorted(prontos, key=lambda kv: ordem[kv[0].data[0]]):
            tipo, obj = key.data
            if tipo == "motor":
                self._ler_motor(obj)
            elif tipo == "stt":
                self._ler_stt()
            elif tipo == "escuta":
                self._aceitar()
            else:
                self._ler_cliente(key.fileobj, obj)
        self.tique()

    def tique(self) -> None:
        agora = self.relogio()
        for t in list(self.turnos.values()):
            self._vigiar(t, agora)
        self.restos = [pr for pr in self.restos if pr.poll() is None]
        mono = time.monotonic()
        for pgid, quando in list(self.a_matar):
            if mono >= quando:
                self.a_matar.remove((pgid, quando))
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        self._vigiar_stt(mono, agora)
        self._fila(agora)
        if mono - self._pulso >= PULSACAO_S:
            self._pulsar(agora)

    def _pulsar(self, agora: float) -> None:
        try:
            _gravar(self.cfg.conversas / ESTADO_PONTE, json.dumps({
                "v": 1, "pulsacao": self._iso(agora), "versao": CONTRATO, "voz": self.cfg.voz != "desligada",
                "voz_modo": self.cfg.voz, "voz_fornecedor": self.voz_fornecedor, "permissoes": self.cfg.permissoes,
                "dia_usd": self._gasto_hoje(agora), "vistos": sorted(self.vistos)[-VISTOS_MAX:]}).encode())
            self._pulso = time.monotonic()
        except OSError:
            pass

    # --- gasto do dia (CONVERSA_FUSO) ---
    def _gasto_hoje(self, agora: float) -> float:
        g = _ler_json(self.cfg.estado / "gasto.json", 4096) or {}
        usd = g.get("usd")
        if g.get("dia") != self._iso(agora)[:10] or not isinstance(usd, (int, float)) or isinstance(usd, bool):
            return 0.0
        return round(float(usd), 4)

    def _somar_gasto(self, usd: float, agora: float) -> float:
        dia = round(self._gasto_hoje(agora) + usd, 4)
        try:
            _gravar(self.cfg.estado / "gasto.json", json.dumps({"dia": self._iso(agora)[:10], "usd": dia}).encode(),
                    0o600)
        except OSError:
            self.auditar("erro", motivo="gasto.json não gravado")
        return dia

    # --- conversas: meta e eventos ---
    def meta(self, cid: str) -> dict | None:
        if cid not in self.metas:
            m = chat.ler_meta(self.cfg.conversas, cid)
            if m is None:
                return None
            m["n"] = max(m["n"], ultimo_n(self.cfg.conversas / cid / "eventos.jsonl"))
            self.metas[cid] = m
        return self.metas[cid]

    def _gravar_meta(self, cid: str) -> None:
        _gravar(self.cfg.conversas / cid / "meta.json", json.dumps(self.metas[cid], ensure_ascii=False).encode())

    def evento(self, cid: str, tipo: str, **campos) -> int:
        m = self.metas[cid]
        agora = self.relogio()
        n = m["n"] + 1
        _acrescentar(self.cfg.conversas / cid / "eventos.jsonl",
                     {"n": n, "t": self._iso(agora), "tipo": tipo, **campos}, 0o640)
        m["n"], m["actualizada"] = n, self._iso(agora)
        if tipo == "fim":
            m["ultimo_fim"] = n
        self._gravar_meta(cid)
        return n

    def _estado(self, cid: str, estado: str, passo: str) -> None:
        m = self.metas[cid]
        if m["estado"] == estado:
            return
        m["estado"] = estado
        self.evento(cid, "estado", estado=estado, passo=passo[:PASSO_MAX])

    def _fim(self, cid: str, motivo: str) -> None:
        self.evento(cid, "fim", motivo=motivo)

    def _conv_estado(self, cid: str) -> dict:
        d = _ler_json(self.cfg.estado / f"{cid}.json") or {}
        return {"sessao": d.get("sessao") if isinstance(d.get("sessao"), str) else None,
                "regras": [r for r in d.get("regras", []) if isinstance(r, dict)]}

    def _guardar_conv(self, cid: str, d: dict) -> None:
        _gravar(self.cfg.estado / f"{cid}.json", json.dumps(d, ensure_ascii=False).encode(), 0o600)

    # --- auditoria ---
    def auditar(self, tipo: str, **campos) -> None:
        agora = self.relogio()
        try:
            _acrescentar(self.cfg.estado / "auditoria" / f"{self._iso(agora)[:7]}.jsonl",
                         {"t": self._iso(agora), "tipo": tipo, **campos}, 0o600)
        except OSError as e:
            print(f"ponte: auditoria falhou ({e.strerror})", file=sys.stderr, flush=True)

    # --- verificados/ (§2.4) ---
    def _fila(self, agora: float) -> None:
        # os pedidos que o guardião aceitou e a ponte ainda não viu, por ordem de nome; cada um entra em `vistos`
        # (gravado logo: um reinício não o repete) e a pulsação publica-os para o guardião os apagar
        try:
            nomes = sorted(e.name for e in os.scandir(self.cfg.verificados) if chat.NOME_FILA.fullmatch(e.name))
        except OSError:
            return
        existem = set(nomes)
        novos = [n for n in nomes if n not in self.vistos]
        if not novos:
            if not existem.issuperset(self.vistos):   # o guardião apagou-os: saem de `vistos` e da pulsação
                self.vistos = {k: x for k, x in self.vistos.items() if k in existem}
                self._gravar_vistos()
                self._pulsar(agora)
            return
        for nome in novos:
            try:
                res = self._pedido_fila(nome)
            except Exception as e:   # noqa: BLE001 - o pedido fica visto na mesma
                print(f"ponte: {nome}: erro interno ({type(e).__name__})", file=sys.stderr, flush=True)
                res = {"erro": "erro interno da ponte"}
            if "erro" in res and not res.pop("recusado", False):
                self.auditar("fila_recusada", ficheiro=nome, motivo=res["erro"])
            self.vistos = {k: x for k, x in self.vistos.items() if k in existem}
            self.vistos[nome] = {"em": self._iso(self.relogio()), "res": res}
            self._gravar_vistos()
        self._pulsar(agora)

    def _gravar_vistos(self) -> None:
        try:
            _gravar(self.cfg.estado / "vistos.json", json.dumps(self.vistos, ensure_ascii=False).encode(), 0o600)
        except OSError as e:
            print(f"ponte: vistos.json não gravado ({e.strerror})", file=sys.stderr, flush=True)

    def _ler_pedido(self, nome: str):
        try:
            dfd = os.open(self.cfg.verificados, _DIR)
        except OSError:
            return None, "verificados/ ilegível"
        try:
            fd = os.open(nome, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except OSError:
            return None, "não é um ficheiro normal"
        finally:
            os.close(dfd)
        try:
            with os.fdopen(fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    return None, "não é um ficheiro normal"
                raw = f.read(chat.FILA_MAX + 1)
        except OSError:
            return None, "ilegível"
        if len(raw) > chat.FILA_MAX:
            return None, "grande demais"
        try:
            d = json.loads(raw)
        except ValueError:
            return None, "JSON inválido"
        erro = chat.validar_fila(d)
        return (None, erro) if erro else (d, None)

    def _pedido_fila(self, nome: str) -> dict:
        d, erro = self._ler_pedido(nome)
        if erro:
            return {"erro": erro[:200]}
        if not chat.tempo_ok(d, self.relogio()):   # sem evento nem efeito: só `vistos` e a auditoria
            self.auditar("fora_de_tempo", acao=d["acao"], ficheiro=nome)
            return {"erro": FORA_DE_TEMPO, "recusado": True}
        acao, cid = d["acao"], d["conversa"]
        if acao == "nova":
            return self._nova(cid)
        if acao == "transcrever":   # nunca chega ao _libertar nem ao _responder: o áudio não é instrução
            return self._transcrever(d)
        m = self.meta(cid) if chat.pasta(self.cfg.conversas, cid) else None
        if m is None:
            return {"erro": "conversa inexistente"}
        if acao == "parar":
            return self._parar(cid, d)
        if m["arquivada"]:
            return {"erro": chat.ARQUIVADA}
        if acao in ("enviar", "arquivar"):
            self._libertar(cid)
        if acao == "enviar":
            return self._enviar(cid, d)
        if acao == "arquivar":
            if cid in self.turnos or m["estado"] in chat.OCUPADO:
                return {"erro": chat.A_TRABALHAR}
            m["arquivada"] = True
            m["actualizada"] = self._iso(self.relogio())
            self._gravar_meta(cid)
            return {"ok": True}
        return self._responder(cid, d)

    def _nova(self, cid: str) -> dict:
        p = self.cfg.conversas / cid
        try:
            os.mkdir(p, 0o750)
        except FileExistsError:
            return {"erro": "conversa já existe"}
        agora = self._iso(self.relogio())
        self.metas[cid] = {"v": 1, "id": cid, "titulo": "Conversa nova", "criada": agora, "actualizada": agora,
                           "estado": "pronto", "arquivada": False, "n": 0, "ultimo_fim": None, "pendentes": []}
        self._gravar_meta(cid)
        return {"ok": True}

    def _parar(self, cid: str, d: dict) -> dict:
        t = self.turnos.get(cid)
        self.auditar("paragem", conversa=cid, ip=d["ip"], ua=d["ua"], havia_turno=t is not None and not t.fim)
        if t is not None and t.fim and not t.morto and t.proc.poll() is None:
            t.morto = True   # o turno acabou mas o motor ficou: o Parar mata o grupo
            self._matar_grupo(t.proc.pid)
        if t is None or t.fim:
            return {"ok": True}
        self._terminar(t, "parado", PAROU, matar=True)
        return {"ok": True}

    # --- enviar → motor ---
    def efectivo(self, d: dict) -> str:
        # §2.4: espelho só se a unit e o pedido (o office.yaml) o disserem
        return "espelho" if self.cfg.permissoes == "espelho" and d["permissoes"] == "espelho" else "cartao"

    def _enviar(self, cid: str, d: dict) -> dict:
        m = self.metas[cid]
        if cid in self.turnos or m["estado"] in chat.OCUPADO:
            self.evento(cid, "aviso", codigo="ocupado", texto="O assistente ainda está a trabalhar; espera pelo fim.")
            return {"erro": chat.A_TRABALHAR}
        texto = d["texto"]
        self.auditar("mensagem", conversa=cid, sha256=hashlib.sha256(texto.encode()).hexdigest(), tamanho=len(texto),
                     permissoes=self.efectivo(d), ip=d["ip"], ua=d["ua"])
        if m["titulo"] == "Conversa nova":
            m["titulo"] = reader.mask(texto).replace("\n", " ").strip()[:TITULO_MAX] or "Conversa nova"
        self.evento(cid, "mensagem", autor="dono", texto=reader.mask(texto))
        self._arrancar_motor(cid, d, self._conv_estado(cid), texto)
        return {"ok": True}

    def _env_motor(self) -> dict:
        e = {k: self.env[k] for k in ENV_MOTOR if self.env.get(k)}
        e["TERM"] = "dumb"
        e["MCP_TOOL_TIMEOUT"] = MCP_TOOL_TIMEOUT
        return e

    def _argv(self, ce: dict, d: dict, mcp_path: Path) -> list[str]:
        modo = self.efectivo(d)
        a = [self.cfg.claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
        if ce["sessao"]:
            a += ["--resume", ce["sessao"]]
        a += ["--max-turns", str(self.cfg.turnos_max),
              "--permission-prompt-tool", "mcp__ponte__permissao", "--mcp-config", str(mcp_path),
              "--settings", str(self.settings[modo])]
        if d["modelo"] != "omissao":
            a += ["--model", d["modelo"]]
        if d["modo"] == "plano":
            a += ["--permission-mode", "plan"]
        elif modo == "espelho":
            a += ["--permission-mode", "auto"]
        return a

    def _arrancar_motor(self, cid: str, d: dict, ce: dict, entrada: str) -> None:
        token = secrets.token_hex(16)
        mcp_path = self.cfg.run / f"turno-{cid}.json"
        mcp = {"mcpServers": {"ponte": {"type": "stdio", "command": sys.executable,
                                        "args": ["-I", str(AQUI / "mcp.py")],
                                        "env": {"CONVERSA_PONTE_SOCK": str(self.cfg.run / "ponte.sock"),
                                                "CONVERSA_PONTE_TURNO": token}}}}
        try:
            _gravar(mcp_path, json.dumps(mcp).encode(), 0o600)
            fd = os.open(self.cfg.estado / "stderr" / f"{cid}.log",
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            try:
                proc = subprocess.Popen(self._argv(ce, d, mcp_path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=fd, cwd=self.cfg.cwd, env=self._env_motor(), start_new_session=True)
            finally:
                os.close(fd)
        except OSError as e:
            self.evento(cid, "aviso", codigo="motor_falhou", texto=f"O motor não arrancou ({e.strerror or e}).")
            self.auditar("erro", conversa=cid, motivo="motor não arrancou")
            self._estado(cid, "erro", "o motor não arrancou")
            self._fim(cid, "erro")
            try:
                mcp_path.unlink()
            except OSError:
                pass
            return
        t = Turno(cid, proc, token, self.relogio(), mcp_path, d["ip"], d["ua"])
        t.resume = bool(ce["sessao"])
        self.turnos[cid], self.por_token[token] = t, t
        _gravar(self.cfg.estado / "turnos" / f"{cid}.json",
                json.dumps({"pid": proc.pid, "pgid": proc.pid, "starttime": starttime(proc.pid)}).encode(), 0o600)
        os.set_blocking(proc.stdout.fileno(), False)
        self.sel.register(proc.stdout, selectors.EVENT_READ, ("motor", t))
        self._estado(cid, "a_trabalhar", "o assistente está a pensar")
        linha = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": entrada}]}}
        try:
            proc.stdin.write(json.dumps(linha, ensure_ascii=False).encode() + b"\n")
            proc.stdin.flush()
        except OSError:
            pass   # o motor morreu logo: o _vigiar trata

    # --- stdout do motor ---
    def _ler_motor(self, t: Turno) -> None:
        # tudo o que houver agora (até EAGAIN ou EOF); um neto que herdou o stdout pode adiar o EOF para sempre
        while not t.lido:
            try:
                bloco = os.read(t.proc.stdout.fileno(), 1 << 16)
            except BlockingIOError:
                return
            except OSError:
                bloco = b""
            if not bloco:
                self._fechar_stdout(t)
                return
            self._linhas_motor(t, bloco)

    def _linhas_motor(self, t: Turno, bloco: bytes) -> None:
        t.buf += bloco
        while b"\n" in t.buf:
            ln, t.buf = t.buf.split(b"\n", 1)
            if ln.strip() and not t.fim:
                try:
                    msg = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    self._mensagem_motor(t, msg)

    def _fechar_stdout(self, t: Turno) -> None:
        if not t.lido:
            t.lido = True
            try:
                self.sel.unregister(t.proc.stdout)
            except (KeyError, ValueError):
                pass

    def _mensagem_motor(self, t: Turno, msg: dict) -> None:
        cid, tipo = t.cid, msg.get("type")
        if tipo == "system" and msg.get("subtype") == "init":
            t.init = True
            sid = msg.get("session_id")
            if isinstance(sid, str) and sid:
                ce = self._conv_estado(cid)
                ce["sessao"] = sid
                self._guardar_conv(cid, ce)
            srv = msg.get("mcp_servers")
            ok = isinstance(srv, list) and any(isinstance(s, dict) and s.get("name") == "ponte"
                                               and s.get("status") == "connected" for s in srv)
            if not ok:
                self.evento(cid, "aviso", codigo="motor_falhou",
                            texto="O servidor de permissões da ponte não ligou; o turno parou.")
                self.auditar("erro", conversa=cid, motivo="mcp ponte não ligado")
                self._terminar(t, "erro", TURNO_ACABOU, matar=True)
        elif tipo == "assistant":
            for b in (msg.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or t.fim:
                    continue
                if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"]:
                    for i in range(0, len(b["text"]), MENSAGEM_MAX):
                        self.evento(cid, "mensagem", autor="assistente",
                                    texto=reader.mask(b["text"][i:i + MENSAGEM_MAX]))
                elif b.get("type") == "tool_use":
                    self._tool_use(t, b)
        elif tipo == "user":
            for b in (msg.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self._tool_result(t, b)
        elif tipo == "result":
            self._result(t, msg)

    def _toca_conversa(self, nome, entrada) -> bool:
        # uma ferramenta que escreve (ou pode escrever) nas pastas da Conversa ou mexe no estado da ponte
        if nome in SEM_EFEITO:
            return False
        txt = json.dumps(entrada, ensure_ascii=False)
        marcas = []
        pastas = (self.cfg.estado,) if nome in LEITURA else (self.cfg.verificados, self.cfg.conversas, self.cfg.estado)
        for p in pastas:
            s = str(p)
            marcas.append(s)
            h = str(H)
            if s.startswith(h + "/"):
                marcas += ["~" + s[len(h):], "$HOME" + s[len(h):], "${HOME}" + s[len(h):]]
        return any(mk in txt for mk in marcas)

    def _tool_use(self, t: Turno, b: dict) -> None:
        nome = b.get("name") if isinstance(b.get("name"), str) else "?"
        entrada = b.get("input") if isinstance(b.get("input"), dict) else {}
        n = self.evento(t.cid, "ferramenta", nome=nome[:80], resumo=resumo(nome, entrada))
        if isinstance(b.get("id"), str):
            t.usos[b["id"]] = {"n": n, "nome": nome, "input": entrada}
        self.auditar("ferramenta", conversa=t.cid, nome=nome[:80],
                     args=reader.clip(json.dumps(entrada, ensure_ascii=False), AUDIT_ARGS_MAX))
        if self._toca_conversa(nome, entrada):
            self._fila_tocada(t, nome)

    def _fila_tocada(self, t: Turno, nome: str) -> None:
        if t.fim:
            return
        self.evento(t.cid, "aviso", codigo="fila_tocada",
                    texto="O assistente tentou mexer nas pastas da Conversa; o turno foi cortado.")
        self.auditar("fila_tocada", conversa=t.cid, nome=nome[:80])
        self._terminar(t, "erro", TURNO_ACABOU, matar=True)

    def _tool_result(self, t: Turno, b: dict) -> None:
        uso = t.usos.get(b.get("tool_use_id"))
        if uso is None:
            return
        if reader.env_file(uso["nome"], uso["input"]):
            res = reader.HIDDEN
        else:
            res = reader.clip(_texto_resultado(b.get("content")), RESULTADO_MAX)
        self.evento(t.cid, "ferramenta", ref=uso["n"], nome=uso["nome"][:80], resultado=res)
        if e_delegacao(uso["nome"], uso["input"], self.rx_lanc) and b.get("is_error") is not True \
                and b.get("tool_use_id") not in t.cliques and uso["input"]["command"] not in t.cliques:
            self.evento(t.cid, "aviso", codigo="delegacao_sem_clique",
                        texto="Um lançador correu sem o clique do dono na Conversa.")
            self.auditar("delegacao_sem_clique", conversa=t.cid,
                         comando=reader.clip(uso["input"].get("command", ""), AUDIT_ARGS_MAX))

    def _result(self, t: Turno, r: dict) -> None:
        cid, agora = t.cid, self.relogio()
        u = r.get("usage") if isinstance(r.get("usage"), dict) else {}
        tokens = sum(v for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                 "cache_read_input_tokens") if type(v := u.get(k)) is int)
        custo = r.get("total_cost_usd")
        usd = round(float(custo), 4) if isinstance(custo, (int, float)) and not isinstance(custo, bool) \
            and 0 <= custo < 1e6 else 0.0
        ok = r.get("subtype") == "success" and r.get("is_error") is not True
        dia = self._somar_gasto(usd, agora)
        self.evento(cid, "custo", usd=usd, tokens=tokens, dia_usd=dia)
        self.auditar("custo", conversa=cid, usd=usd, tokens=tokens, dia_usd=dia)
        self._pulsar(agora)   # o «hoje: X USD» do painel muda já
        if r.get("subtype") == "error_max_turns":
            self.evento(cid, "aviso", codigo="tecto_turnos",
                        texto=f"O turno chegou ao tecto de {self.cfg.turnos_max} passos.")
            self._terminar(t, "tecto", TURNO_ACABOU)
        else:
            self._terminar(t, "pronto" if ok else "erro", TURNO_ACABOU)

    # --- fim de turno, parar, tectos ---
    def _terminar(self, t: Turno, motivo: str, msg_pendentes: str, matar: bool = False) -> None:
        if t.fim:
            return
        t.fim, t.fim_em = True, time.monotonic()
        for p in list(t.pend.values()):
            self._resolver(t, p, {"behavior": "deny", "message": msg_pendentes}, "expirada", estado=False)
        estado = {"pronto": "pronto", "erro": "erro"}.get(motivo, "parado")
        self._estado(t.cid, estado, {"pronto": "pronto", "erro": "parou com erro", "tecto": "tecto atingido",
                                     "parado": "parado pelo dono"}[motivo])
        self._fim(t.cid, motivo)
        if motivo != "pronto":
            self.auditar("fim", conversa=t.cid, motivo=motivo)
        try:
            t.proc.stdin.close()
        except OSError:
            pass
        if matar:
            t.morto = True
            self._matar_grupo(t.proc.pid)

    def _matar_grupo(self, pgid: int) -> None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        self.a_matar.append((pgid, time.monotonic() + MATAR_S))

    def _vigiar(self, t: Turno, agora: float) -> None:
        for p in list(t.pend.values()):
            if p.expira <= agora:
                self.auditar("expirada", conversa=t.cid, nonce=p.nonce)
                self._resolver(t, p, {"behavior": "deny", "message": SEM_RESPOSTA}, "expirada")
        if not t.fim and agora - t.inicio > self.cfg.duracao_max_min * 60:
            self.evento(t.cid, "aviso", codigo="tecto_duracao",
                        texto=f"O turno passou de {self.cfg.duracao_max_min} min e foi parado.")
            self._terminar(t, "tecto", TURNO_ACABOU, matar=True)
        if t.proc.poll() is None:
            if t.fim and not t.morto and time.monotonic() - t.fim_em >= FIM_S:
                t.morto = True   # acabou o turno mas o motor não sai
                self._matar_grupo(t.proc.pid)
            return
        self._ler_motor(t)   # o resto do stdout antes de fechar (o result pode vir no fim)
        if not t.fim and t.resume and not t.init:
            # o --resume não pegou (sessão apagada/expirada): a próxima mensagem começa sessão nova
            ce = self._conv_estado(t.cid)
            ce["sessao"] = None
            self._guardar_conv(t.cid, ce)
            self.evento(t.cid, "aviso", codigo="sessao_perdida",
                        texto="A sessão anterior do motor não abriu; a próxima mensagem começa uma sessão nova.")
            self.auditar("erro", conversa=t.cid, motivo=f"--resume falhou (código {t.proc.returncode})")
            self._terminar(t, "erro", TURNO_ACABOU)
        elif not t.fim:
            self.evento(t.cid, "aviso", codigo="motor_falhou",
                        texto=f"O motor saiu sem resposta (código {t.proc.returncode}).")
            self.auditar("erro", conversa=t.cid, motivo=f"motor saiu com {t.proc.returncode}")
            self._terminar(t, "erro", TURNO_ACABOU)
        self._limpar(t)

    def _libertar(self, cid: str) -> None:
        # um turno já com `fim` cujo motor ainda não saiu não ocupa a conversa: mata-se o grupo e esquece-se
        t = self.turnos.get(cid)
        if t is None or not t.fim:
            return
        if t.proc.poll() is None or _grupo_vivo(t.proc.pid):
            self._matar_grupo(t.proc.pid)
            self.restos.append(t.proc)
        self._limpar(t)

    def _limpar(self, t: Turno) -> None:
        self._fechar_stdout(t)
        for f in (t.proc.stdout, t.proc.stdin):
            try:
                f.close()
            except OSError:
                pass
        if _grupo_vivo(t.proc.pid) and not any(g == t.proc.pid for g, _ in self.a_matar):
            self._matar_grupo(t.proc.pid)   # filhos que ficaram para trás no grupo do motor
        for f in (t.mcp_path, self.cfg.estado / "turnos" / f"{t.cid}.json"):
            try:
                f.unlink()
            except OSError:
                pass
        if self.turnos.get(t.cid) is t:
            self.turnos.pop(t.cid, None)
        self.por_token.pop(t.token, None)

    def encerrar(self) -> None:
        """SIGTERM da ponte: mata todos os grupos, `fim parado` e fecha o socket."""
        for t in list(self.turnos.values()):
            self._terminar(t, "parado", "a ponte foi parada", matar=True)
        prazo = time.monotonic() + MATAR_S
        while self.turnos and time.monotonic() < prazo:
            for t in list(self.turnos.values()):
                self._vigiar(t, self.relogio())
            time.sleep(0.05)
        self._encerrar_voz()
        for pgid, _ in self.a_matar:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.a_matar.clear()
        if self.sock:
            self.sel.unregister(self.sock)
            self.sock.close()
            self.sock = None

    # --- voz ---
    def _arrancar_voz(self) -> None:
        # as cópias privadas de uma ponte anterior apagam-se (os originais em verificados/audio/ apaga-os o guardião)
        for f in (self.cfg.estado / "stt").iterdir():
            try:
                f.unlink()
            except OSError:
                pass

    def _ler_audio(self, nome: str, n: int, sha: str) -> bytes | None:
        # o áudio de verificados/audio/: pasta e ficheiro sem symlinks, regular, com `n` bytes e o sha256 assinado
        try:
            dfd = os.open(self.cfg.verificados / AUDIO_DIR, _DIR)
        except OSError:
            return None
        try:
            fd = os.open(nome, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except OSError:
            return None
        finally:
            os.close(dfd)
        try:
            with os.fdopen(fd, "rb") as f:
                st = os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_size != n:
                    return None
                raw = f.read(n + 1)
        except OSError:
            return None
        return raw if len(raw) == n and hashlib.sha256(raw).hexdigest() == sha else None

    def _transcrever(self, d: dict) -> dict:
        cid, nome = d["conversa"], f"{d['audio']}.{d['formato']}"
        t = {"cid": cid, "audio": d["audio"], "nome": nome, "bytes": d["bytes"], "sha256": None, "copia": None,
             "inicio": None, "tentativas": 0, "idioma": d["idioma"]}
        m = self.meta(cid) if chat.pasta(self.cfg.conversas, cid) else None
        if m is None or m["arquivada"]:
            self._stt_fim(t, "invalido", evento=False)
            return {"erro": "conversa inexistente" if m is None else chat.ARQUIVADA}
        raw = self._ler_audio(nome, d["bytes"], d["sha256"])
        if raw is None:
            self._stt_fim(t, "invalido")
            return {"erro": "áudio inválido"}
        t["sha256"] = d["sha256"]
        if self.cfg.voz == "desligada" or (self.cfg.voz == "local" and not (
                os.path.isfile(self.stt_python) and os.access(self.stt_python, os.X_OK))):
            self._stt_fim(t, "indisponivel")
            return {"ok": True}
        copia = self.cfg.estado / "stt" / nome
        try:   # o trabalhador só lê a cópia privada; o original apaga-o o guardião quando o pedido fica visto
            fd = os.open(copia, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            t["copia"] = copia
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
        except OSError:
            self._stt_fim(t, "falhou")
            return {"ok": True}
        self.stt_fila.append(t)
        self._stt_proximo()
        return {"ok": True}

    def _stt_proximo(self) -> None:
        while self.stt_atual is None and self.stt_fila:
            t = self.stt_fila.pop(0)
            t["inicio"] = time.monotonic()
            falha = self._stt_enviar(t)
            if falha is None:
                self.stt_atual = t
            else:
                self._stt_fim(t, falha)

    def _stt_enviar(self, t: dict) -> str | None:
        # None: entregue ao trabalhador; senão o resultado da falha (sem_memoria, falhou)
        t["tentativas"] += 1
        if self.stt is None:
            if self.cfg.voz == "local" and self.stt_ram:
                livre = memoria_livre_mb(self.meminfo)
                if livre is not None and livre < self.stt_ram:
                    t["livre_mb"] = livre
                    return "sem_memoria"
            if not self._stt_lancar():
                return "falhou"
        try:   # uma linha < 1 KB num pipe vazio: não bloqueia
            os.write(self.stt.stdin.fileno(), json.dumps({"id": t["audio"], "ficheiro": str(t["copia"]),
                                                          "idioma": t["idioma"]}).encode() + b"\n")
        except OSError:
            pass   # o trabalhador acabou de sair: o EOF do stdout relança-o
        return None

    def _stt_lancar(self) -> bool:
        externa = self.cfg.voz == "externa"
        env = {k: v for k, v in self.env.items() if k in ENV_STT or k.startswith("CONVERSA_STT_")}
        if not externa:
            env.pop("CONVERSA_STT_CHAVE", None)   # a chave só vai para quem a usa
        env["HF_HUB_OFFLINE"] = "1"   # nunca descarrega no trabalhador: o modelo pré-carrega-se no instalador
        if externa:   # stdlib do sistema, isolado
            argv = [sys.executable, "-I", str(AQUI / "stt.py"), "--servir", "--externa"]
        else:         # -E -s: nem PYTHON* do ambiente nem o site do utilizador, só o venv
            argv = [self.stt_python, "-E", "-s", str(AQUI / "stt.py"), "--servir"]
        log = self.cfg.estado / "stderr" / "stt.log"
        try:
            try:   # acrescenta (o stderr de um crash fica para o relançamento ler); trunca só acima do tecto
                grande = os.lstat(log).st_size > STT_LOG_MAX
            except FileNotFoundError:
                grande = False
            fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | (os.O_TRUNC if grande else 0),
                         0o600)
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=fd,
                                        cwd=self.cfg.estado / "stt", env=env, start_new_session=True,
                                        preexec_fn=limitar_memoria(self.stt_memoria))  # noqa: PLW1509 - 1 thread
            finally:
                os.close(fd)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"ponte: o trabalhador da voz não arrancou ({getattr(e, 'strerror', None) or type(e).__name__})",
                  file=sys.stderr, flush=True)
            return False
        os.set_blocking(proc.stdout.fileno(), False)
        self.sel.register(proc.stdout, selectors.EVENT_READ, ("stt", None))
        self.stt, self.stt_buf = proc, b""
        return True

    def _stt_fechar(self, matar: bool) -> None:
        p = self.stt
        if p is None:
            return
        self.stt, self.stt_buf = None, b""
        try:
            self.sel.unregister(p.stdout)
        except (KeyError, ValueError):
            pass
        for f in (p.stdout, p.stdin):
            try:
                f.close()
            except OSError:
                pass
        if matar:   # o trabalhador não guarda nada: SIGKILL ao grupo, sem a espera do SIGTERM
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.restos.append(p)   # recolhido no tique

    def _ler_stt(self) -> None:
        while self.stt is not None:
            try:
                bloco = os.read(self.stt.stdout.fileno(), 1 << 16)
            except BlockingIOError:
                return
            except OSError:
                bloco = b""
            if not bloco:
                self._stt_saiu()
                return
            self.stt_buf += bloco
            while b"\n" in self.stt_buf:
                ln, self.stt_buf = self.stt_buf.split(b"\n", 1)
                self._stt_resposta(ln)
            if len(self.stt_buf) > 1 << 16:
                self.stt_buf = b""

    def _stt_saiu(self) -> None:
        # EOF: o trabalhador saiu (ocioso, erro, morto). Com uma tarefa em curso relança e reenvia uma vez
        self._stt_fechar(matar=False)
        t = self.stt_atual
        if t is None:
            return
        falha = self._stt_enviar(t) if t["tentativas"] < 2 else "falhou"
        if falha is None:
            return
        self.stt_atual = None
        self._stt_fim(t, falha)
        self._stt_proximo()

    def _stt_resposta(self, ln: bytes) -> None:
        try:
            r = json.loads(ln)
        except ValueError:
            return
        t = self.stt_atual
        if not isinstance(r, dict) or t is None or r.get("id") != t["audio"]:
            return
        self.stt_atual = None
        dur = r.get("duracao_s")
        dur = round(float(dur), 1) if isinstance(dur, (int, float)) and not isinstance(dur, bool) \
            and 0 <= dur < 1e6 else None
        if r.get("ok") is True and isinstance(r.get("texto"), str):
            self._stt_fim(t, "ok", texto=r["texto"], duracao=dur)
        elif r.get("erro") in ("indisponivel", "chave", "limite", "tempo"):
            self._stt_fim(t, r["erro"], duracao=dur)
        else:
            self._stt_fim(t, "falhou", duracao=dur, aviso=VOZ_LONGO if r.get("erro") == "longo" else None)
        self._stt_proximo()

    def _vigiar_stt(self, mono: float, agora: float) -> None:
        t = self.stt_atual
        if t is not None and mono - t["inicio"] > self.stt_tempo:
            self.stt_atual = None
            self._stt_fechar(matar=True)
            self._stt_fim(t, "tempo")
            self._stt_proximo()

    def _stt_fim(self, t: dict, resultado: str, texto: str | None = None, duracao: float | None = None,
                 aviso: str | None = None, evento: bool = True) -> None:
        # todos os desfechos: apaga a cópia, escreve o evento (se a conversa existe) e a auditoria
        tempo = round(time.monotonic() - t["inicio"], 1) if t["inicio"] is not None else 0.0
        if t["copia"] is not None:
            try:
                os.unlink(t["copia"])
            except OSError:
                pass
        if evento and t["cid"] in self.metas:
            if resultado == "ok":
                self.evento(t["cid"], "transcricao", audio=t["audio"], texto=texto_voz(texto), duracao_s=duracao,
                            tempo_s=tempo)
            elif resultado == "sem_memoria":
                livre, preciso = t.get("livre_mb", 0), self.stt_ram
                self.evento(t["cid"], "aviso", codigo="voz_sem_memoria", audio=t["audio"], livre_mb=livre,
                            preciso_mb=preciso, texto=VOZ_SEM_MEMORIA.format(livre=livre, preciso=preciso))
            else:
                codigo, msg = {"indisponivel": ("voz_indisponivel", VOZ_INDISPONIVEL),
                               "tempo": ("voz_tempo", VOZ_TEMPO),
                               "chave": ("voz_chave", VOZ_CHAVE),
                               "limite": ("voz_limite", VOZ_LIMITE)}.get(resultado,
                                                                         ("voz_falhou", aviso or VOZ_FALHOU))
                self.evento(t["cid"], "aviso", codigo=codigo, texto=msg, audio=t["audio"])
        self.auditar("transcricao", conversa=t["cid"], audio=t["audio"], modo=self.cfg.voz, bytes=t["bytes"],
                     sha256=t["sha256"], duracao_s=duracao, tempo_s=tempo, resultado=resultado)

    def _encerrar_voz(self) -> None:
        pend = ([self.stt_atual] if self.stt_atual else []) + self.stt_fila
        self.stt_atual, self.stt_fila, proc = None, [], self.stt
        self._stt_fechar(matar=True)
        for t in pend:
            self._stt_fim(t, "falhou")
        if proc is not None:
            try:
                proc.wait(MATAR_S)
            except subprocess.TimeoutExpired:
                pass

    # --- socket das permissões ---
    def _aceitar(self) -> None:
        while True:
            try:
                c, _ = self.sock.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            try:
                _pid, uid, _gid = struct.unpack("3i", c.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                                   struct.calcsize("3i")))
            except OSError:
                uid = -1
            if uid != os.getuid():
                c.close()
                continue
            c.setblocking(False)
            self.sel.register(c, selectors.EVENT_READ, ("cliente", {"buf": b""}))

    def _ler_cliente(self, c: socket.socket, st: dict) -> None:
        try:
            bloco = c.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            bloco = b""
        if bloco:
            st["buf"] += bloco
            if b"\n" not in st["buf"] and len(st["buf"]) <= PEDIDO_MAX:
                return
        self.sel.unregister(c)
        c.setblocking(True)
        c.settimeout(2)
        if b"\n" not in st["buf"] or len(st["buf"].split(b"\n", 1)[0]) > PEDIDO_MAX:
            self._responder_socket(c, {"behavior": "deny", "message": SEM_PONTE})
            return
        try:
            req = json.loads(st["buf"].split(b"\n", 1)[0])
        except ValueError:
            req = None
        self._pedido_socket(c, req)

    @staticmethod
    def _responder_socket(c: socket.socket, resposta: dict) -> None:
        try:
            c.sendall(json.dumps(resposta, ensure_ascii=False).encode() + b"\n")
        except OSError:
            pass
        finally:
            c.close()

    def _pedido_socket(self, c: socket.socket, req) -> None:
        ok = isinstance(req, dict) and req.get("v") == 1 and isinstance(req.get("turno"), str) \
            and isinstance(req.get("tool_name"), str) and isinstance(req.get("input"), dict) \
            and (req.get("tool_use_id") is None or isinstance(req.get("tool_use_id"), str))
        t = self.por_token.get(req["turno"]) if ok else None
        if t is None or t.fim:
            self._responder_socket(c, {"behavior": "deny", "message": SEM_PONTE})
            return
        nome, entrada, tid = req["tool_name"][:80], req["input"], req.get("tool_use_id")
        if self._toca_conversa(nome, entrada):   # antes de qualquer allow (regra ou clique)
            self._responder_socket(c, {"behavior": "deny", "message": "as pastas da Conversa estão fora de alcance"})
            self._fila_tocada(t, nome)
            return
        agora = self.relogio()
        nonce, expira = secrets.token_hex(16), agora + EXPIRA_S
        base = {"nonce": nonce, "expira": self._iso(expira)}
        sha_entrada = hashlib.sha256(canonico(entrada).encode()).hexdigest()
        deleg = False
        if nome == "AskUserQuestion" and _perguntas_ok(entrada.get("questions")):
            tipo = "pergunta"
            n = self.evento(t.cid, "pergunta", **base, perguntas=_perguntas_evento(entrada["questions"]))
        elif nome == "ExitPlanMode" and isinstance(entrada.get("plan"), str):
            tipo = "plano"
            n = self.evento(t.cid, "plano", **base, texto=reader.clip(entrada["plan"], 32000))
        else:
            tipo = "permissao"
            deleg = e_delegacao(nome, entrada, self.rx_lanc)
            # o dono aprova o que vê, inteiro e sem máscara; o que não cabe ou tem forma de segredo não se pede
            texto = alvo_bruto(nome, entrada)
            recusa = alvo_recusa(texto)
            if recusa:
                self.auditar("recusa_ponte", conversa=t.cid, ferramenta=nome, motivo=recusa,
                             alvo=alvo(nome, entrada))
                self._responder_socket(c, {"behavior": "deny", "message": recusa})
                return
            if not deleg and self._casa_regra(t.cid, nome, entrada):
                self.evento(t.cid, "permissao", **base, ferramenta=nome, alvo=texto,
                            resumo=resumo(nome, entrada), delegacao=False, sha256=sha_entrada,
                            resolvido="aprovada_conversa", auto=True)
                self.auditar("auto_conversa", conversa=t.cid, nonce=nonce, ferramenta=nome, alvo=texto)
                self._responder_socket(c, {"behavior": "allow", "updatedInput": entrada})
                return
            n = self.evento(t.cid, "permissao", **base, ferramenta=nome, alvo=texto, resumo=resumo(nome, entrada),
                            delegacao=deleg, sha256=sha_entrada)
        p = Pendente(nonce=nonce, tipo=tipo, n=n, expira=expira, conn=c, nome=nome, input=entrada, tid=tid,
                     delegacao=deleg, sha=sha_entrada)
        t.pend[nonce] = p
        self.metas[t.cid]["pendentes"].append({"n": n, "nonce": nonce, "tipo": tipo, "delegacao": deleg,
                                               "expira": base["expira"]})
        self._gravar_meta(t.cid)
        self.auditar("pedido", conversa=t.cid, nonce=nonce, pedido=tipo, ferramenta=nome,
                     alvo=alvo(nome, entrada), delegacao=deleg)
        self._estado(t.cid, "a_espera", "à espera do dono")

    def _casa_regra(self, cid: str, nome: str, entrada: dict) -> bool:
        chave = self._chave_regra(nome, entrada)
        return chave is not None and any(r.get("ferramenta") == nome and r.get("alvo") == chave
                                         for r in self._conv_estado(cid)["regras"])

    @staticmethod
    def _chave_regra(nome: str, entrada: dict):
        # Bash → comando exacto; WebFetch → URL exacto; resto (ficheiros incluídos) → input exacto
        if nome == "Bash":
            return entrada.get("command")
        if nome == "WebFetch":
            return entrada.get("url")
        return canonico(entrada)

    def _permitir(self, t: Turno, p: Pendente, resolvido: str) -> None:
        # o input que vai no allow é o do cartão (sha256 do JSON canónico conferido)
        if hashlib.sha256(canonico(p.input).encode()).hexdigest() != p.sha:
            self._resolver(t, p, {"behavior": "deny", "message": "o pedido mudou depois do cartão"}, "recusada")
            return
        self._resolver(t, p, {"behavior": "allow", "updatedInput": p.input}, resolvido)

    def _resolver(self, t: Turno, p: Pendente, resposta: dict, resolvido: str, estado: bool = True) -> None:
        t.pend.pop(p.nonce, None)
        self._responder_socket(p.conn, resposta)
        m = self.metas[t.cid]
        m["pendentes"] = [x for x in m["pendentes"] if x["nonce"] != p.nonce]
        self.evento(t.cid, p.tipo, nonce=p.nonce, resolvido=resolvido)
        if estado and not t.pend and not t.fim:
            self._estado(t.cid, "a_trabalhar", "o assistente continua")

    # --- respostas do dono (fila) ---
    def _responder(self, cid: str, d: dict) -> dict:
        t = self.turnos.get(cid)
        p = t.pend.get(d["nonce"]) if t and not t.fim else None
        if p is None or p.tipo != chat.TIPO_DE[d["acao"]] or p.expira <= self.relogio():
            return {"erro": chat.APROVACAO_INVALIDA}
        escolha = d.get("escolha")
        if d["acao"] == "responder_permissao" and escolha == "conversa" and p.delegacao:
            return {"erro": chat.DELEGACAO_CONVERSA}
        self.auditar("resposta", conversa=cid, nonce=p.nonce, pedido=p.tipo,
                     escolha=escolha if escolha else "respostas", quem=self.cfg.quem,
                     quando=self._iso(self.relogio()), ip=d["ip"], ua=d["ua"])
        if d["acao"] == "responder_permissao":
            if escolha == "recusar":
                self._resolver(t, p, {"behavior": "deny", "message": RECUSOU}, "recusada")
            elif escolha == "conversa":
                ce = self._conv_estado(cid)
                ce["regras"].append({"ferramenta": p.nome, "alvo": self._chave_regra(p.nome, p.input)})
                self._guardar_conv(cid, ce)
                self._permitir(t, p, "aprovada_conversa")
            else:
                if p.delegacao:
                    self.auditar("delegacao", conversa=cid, nonce=p.nonce,
                                 comando=reader.clip(p.input["command"], AUDIT_ARGS_MAX))
                    t.cliques.add(p.tid or p.input["command"])   # sem tool_use_id no pedido, vale o comando
                self._permitir(t, p, "aprovada_uma_vez")
        elif d["acao"] == "responder_pergunta":
            qs = p.input["questions"]
            answers = {}
            for r in d["respostas"]:
                if r["pergunta"] >= len(qs):
                    return {"erro": "resposta a uma pergunta que não existe"}
                q = qs[r["pergunta"]]
                if "outra" in r:
                    answers[q["question"]] = r["outra"]
                    continue
                if any(i >= len(q["options"]) for i in r["opcao"]) or (len(r["opcao"]) > 1
                                                                       and q.get("multiSelect") is not True):
                    return {"erro": "opção que não existe"}
                answers[q["question"]] = ", ".join(q["options"][i]["label"] for i in r["opcao"])
            self._resolver(t, p, {"behavior": "allow", "updatedInput": {"questions": qs, "answers": answers}},
                           "respondida")
        else:
            if escolha == "aprovar":
                self._resolver(t, p, {"behavior": "allow", "updatedInput": p.input}, "aprovado")
            elif escolha == "continuar":
                self._resolver(t, p, {"behavior": "deny", "message": CONTINUA}, "continuar")
            else:
                self._resolver(t, p, {"behavior": "deny", "message": EDITOU + d["texto"]}, "editado")
        return {"ok": True}


# --- main -------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["--help"], ["-h"]):
        print(__doc__.split("\n\n", 1)[0])
        return 0
    cfg = config()
    for p in (cfg.verificados, cfg.conversas):
        if not p.is_dir():
            print(f"ponte.py: {p} não existe (corre o sudo ./instalar-conversa.sh)", file=sys.stderr)
            return 1
    try:
        cfg.estado.parent.mkdir(parents=True, exist_ok=True)
        _pasta_privada(cfg.estado)
        fd = os.open(cfg.estado / "ponte.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError:
        print("ponte.py: ponte.lock inacessível", file=sys.stderr)
        return 1
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        print("ponte.py: ponte.lock não é um ficheiro normal", file=sys.stderr)
        return 1
    with os.fdopen(fd, "w") as trinco:
        try:
            fcntl.flock(trinco, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("ponte.py: outra ponte já está a correr", file=sys.stderr)
            return 1
        parar = []
        signal.signal(signal.SIGTERM, lambda *_: parar.append(1))
        signal.signal(signal.SIGINT, lambda *_: parar.append(1))
        ponte = Ponte(cfg)
        while not parar:
            ponte.passo()
        ponte.encerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
