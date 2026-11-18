"""Squad Office — estado das squads (Claude Code headless) para o dashboard.

Config: office.yaml (OFFICE_CONFIG, lido por officecfg.py no arranque; inválido/ausente → exemplo + aviso).
GET /api/config devolve a parte pública (officecfg.publico: sem caminhos, contacto nem segredos).

/api/state só leva metadados, nunca conteúdo de mensagens; de texto das metas só sai, mascarado e cortado, o
tasks[].titulo (≤ TITULO_MAX = 120: 1.º título do brief ou meta.titulo; separador do browser) e o tasks[].resultado
(meta.result, ≤ quadro.RESULTADO_MAX = 300):
- squads formato worktree: TASKS_DIR/<id>/meta.json (runner), os nomes SPEC/PLAN/QA.md e a mtime do heartbeat
- /data/claude/<worktree com cada não alfanumérico → "->/<sessão>.jsonl (mtime + actividade: só nome/tipo da
  ferramenta e eventos GitHub)
- .../<sessão>/subagents/agent-*.meta.json (papel, descrição curta, finished) + 1.º timestamp do .jsonl
- squads formato sessao: SQUADS_DIR/<squad>/<id>/meta.json, só a sessão indicada,
  /data/claude/<claude_dir>/<session_id>.jsonl
- worktree: fim do <tarefa>/claude.log (custo, turnos e duração das linhas "result" e, da última, um "fim" com 5 chaves
  sanitizadas: recusas, nomes das ferramentas recusadas, motivo, erro e estado HTTP; ADR-013), só ficheiro regular
- /data/claude/**/*.jsonl em fundo: só contagens de tokens das linhas "assistant" (por message.id)
- RUNNER_ESTADO_DIR/estado.json (só leitura): o sinal do runner, só enumerados ("runner"; inválido → null)
- transcripts seguidos: também contagens de TodoWrite/TaskCreate/TaskUpdate, linhas +/-, modelo e ramo por agente (só
  inteiros e valores que casam uma regex) e o run_in_background do Bash
- "espera" (aprovações em aberto + PRs à espera), "falhas_exec" e today.inicio/intervalos: só ids, estados e horas

Excepção, só a pedido: /api/transcript/<id> (reader.py) mostra a entrega e o transcript das sessões
principais de uma tarefa com meta.json, com máscara de segredos e resultados de ficheiros .env* ocultados.
/api/entregas (entregas.py, ADR-014): manchete e resumo mascarados das terminadas há ≤ 7 dias, da mesma fonte que a
entrega do leitor, só a pedido (nunca no poll).

Replay (replay.py, ADR-015): GET /api/replay?dia= reconstrói hoje ou um dos 6 dias anteriores a partir das metas e dos
transcripts, com o mesmo _live_line sobre um estado próprio; só a allowlist do contrato (sem títulos nem texto). Não
escreve.

Aprovações (aprovacoes.py): /api/state leva o resumo dos pedidos pendentes (APROVACOES_DIR, só leitura) e o token
do arranque; /api/aprovacao mostra o corpo a pedido. Única escrita do serviço: POST /api/decisao grava um JSON por
decisão em DECISOES_FILA_DIR; o Office nunca edita os pedidos nem aplica nada.

Biblioteca (biblioteca.py): /api/biblioteca* serve media e documentos das fontes da config em BIBLIOTECA_DIR, das
tarefas e dos pedidos (só o que
está no índice: allowlist, sem symlinks, textos mascarados). Única escrita: miniaturas em BIBLIOTECA_MINI_DIR (tmpfs).

Quadro (quadro.py, ADR-003): GET /api/quadro calcula os cartões automáticos do build_state() em cache (+ briefs da
inbox) e lê os manuais de QUADRO_DIR/cartoes.json; POST /api/quadro (mesmas verificações do /api/decisao) cria, edita,
apaga e move manuais nesse ficheiro (versão = sha256, escrita atómica): 3.ª escrita do serviço.

Notificações no telemóvel (push.py, ADR-004): GET /api/push/chave e POST /api/push/{subscrever,cancelar,teste} (mesmas
verificações do /api/decisao); um vigia em thread (30 s) lê o /api/state em cache e envia Web Push. Chave VAPID,
subscrições e chaves notificadas em PUSH_DIR: 4.ª escrita do serviço. Barra de progresso (progresso.py): "progress"
das tarefas a correr, calculado no build_state().

Lançar a partir do Quadro (lancar.py, ADR-005): POST /api/quadro/lancar (mesmas verificações do /api/decisao) grava um
pedido JSON em QUADRO_LANCAR_DIR e move o cartão para «Em curso» sob o lock do cartoes.json: 5.ª escrita do serviço.
O runner do host lança e responde em <id>.feito.json; o GET /api/quadro liga o cartão à execução e, no fim, move-o
uma vez só (marca <id>.movido.json). O Office nunca lança nada directamente.

Arquivo da Biblioteca (biblioteca.py + arquivo.py, ADR-011): POST /api/biblioteca/arquivo (mesmas verificações do
/api/decisao) grava um pedido «arquivar|repor» em ARQUIVO_FILA_DIR: 6.ª escrita do serviço. Quem move é o runner do
host; o Office só lê as `<fonte>/.arquivo/` («Ver arquivados» e links antigos) e nunca escreve nas fontes.
"""
from __future__ import annotations

import gzip
import hmac
import json
import math
import os
import re
import secrets
import stat
import sys
import threading
import time
from collections import deque
from datetime import date, datetime, time as dtime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

import aprovacoes
import biblioteca
import entregas
import lancar
import officecfg
import progresso
import push
import quadro
import reader
import replay

TASKS = Path(os.environ.get("TASKS_DIR", "/data/tasks"))
CLAUDE = Path(os.environ.get("CLAUDE_PROJECTS_DIR", "/data/claude"))
SQUADS_DIR = Path(os.environ.get("SQUADS_DIR", "/data/squads"))   # squads formato sessao: <squad>/<id>/meta.json
# preenchidos por configurar(cfg): ids das squads, sessão principal (lead) e formato de cada uma
SQUADS: tuple = ()
LEADS: dict = {}
FORMATO: dict = {}
STATIC = Path(__file__).parent / "static"
ACTIVE_S = 180          # actividade no transcript há menos disto = a trabalhar
STALE_S = 30 * 60       # "a correr" sem actividade há mais disto = provavelmente parada
RECENT_S = 30 * 86400   # tarefas terminadas que ainda aparecem (pela mtime da meta)
AGENTS_S = 3 * 86400    # terminadas há mais disto saem sem agentes (nem se olha para os transcripts)
CACHE_S = 5
DEAD_S = 5 * 60         # heartbeat do lançador parado há mais disto = "morreu"
STUCK_S = 10 * 60       # ferramenta sem resultado e transcript parado há mais disto = "preso?"
STUCK_TERM_S = 25 * 60  # idem para um Bash em 1.º plano (testes e builds longos são normais)
TAIL_FIRST = 256 * 1024  # 1.ª leitura de um transcript para actividade: só o fim
FEED_N = 30
HISTORY_DAYS = 30
DONE_STATUS = ("concluída", "parcial (PR draft)", "concluido")
# leitor (allowlist): squads sessao → CLAUDE/<claude_dir da meta>/<session_id>.jsonl, com claude_dir um nome simples
# (_safe) e session_id um UUID; squads worktree → pasta derivada do worktree (_dev_dir). Sessões interactivas
# (entrypoint "cli") nunca saem (_squad_session).
SESSION_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
ENTRYPOINT_LINES = 30

LOG_TAIL = 65536        # do claude.log só se lê o fim
LOG_TAIL_MAX = 1 << 20  # ... ou até 1 MiB se a linha result não cabe nos 64 KB (muitas recusas)
TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}")   # nome de ferramenta recusada que pode sair; o resto é «outra»
REASON = re.compile(r"[a-z_]{1,40}")               # terminal_reason que pode sair; o resto é «outro»
FIM_TOOLS_MAX = 5
INTERVALOS_N = 200      # today.intervalos: no máximo estes (os mais recentes)
WAIT_STATUS = quadro.WAIT_STATUS
PR_OPEN = quadro.PR_OPEN

# gen: o POST incrementa; build antigo não fica fresco
_cache: dict = {"at": 0.0, "body": b"", "gen": 0, "state": {}, "aux": {}}
_log_cache: dict = {}   # caminho → (mtime_ns, tamanho, (custo, turnos, duração, fim), parcial: sem os 1 MiB)
USAGE_EVERY_S = 60
WEEK_S = 7 * 86400
USAGE_KEYS = ("input", "output", "cache_read", "cache_creation")
USAGE_SRC = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_files: dict = {}       # transcript → (inode, offset já lido)
_msgs: dict = {}        # message.id → (ts, modelo, *tokens): só números, nunca texto
_usage: dict = {"status": "a calcular"}   # fotografia; trocada numa só atribuição
_state_lock = threading.Lock()   # um /api/state de cada vez (o rastreador abaixo não é thread-safe)
# para o Quadro, nunca no JSON: metas não dry-run de todas as idades (squad, id, {nomes inbox/<nome> do task},
# 1.º «# » do task normalizado), o result das squads e o título do task, em bruto (o quadro.py mascara o que
# emite); trocado numa só atribuição
# no fim do build_state
_aux: dict = {"metas": [], "result": {}, "titulo": {}}
INBOX_REF = re.compile(r"inbox/([a-z0-9][a-z0-9._-]*\.md)")
TITULO_MAX = 120        # tasks[].titulo (separador do browser), já mascarado

# actividade ao vivo: o último tool_use/thinking de cada transcript seguido; só rótulos, nunca texto/comando/caminho
KINDS = {"Read": "ler", "Grep": "ler", "Glob": "ler", "Edit": "editar", "Write": "editar", "NotebookEdit": "editar",
         "Bash": "terminal", "WebSearch": "web", "WebFetch": "web", "Agent": "delegar", "Task": "delegar"}
TESTS_CMD = re.compile(r"\b(?:pytest|vitest|unittest)\b|\b(?:npm|pnpm)\s+(?:run\s+)?test\b")
# comando Bash → classe GitHub (um evento por comando, por esta prioridade); só em posição de comando
# (início, ou depois de ; & | ( ou fim de linha) e fora de heredocs e strings (_strip_cmd):
# "cd x && gh pr merge" conta, grep/echo "gh pr merge" e corpos de commit/PR não
_CMD = r"(?:^|[;&|(\n])\s*"
GH_CMDS = (("merge", re.compile(_CMD + r"gh\s+pr\s+merge\b")), ("checks", re.compile(_CMD + r"gh\s+pr\s+checks\b")),
           ("watch", re.compile(_CMD + r"gh\s+run\s+watch\b")),
           ("push", re.compile(_CMD + r"git\b(?:\s+-C\s+\S+)?\s+push\b")))
GH_EVENT = {"merge": "pr_integrado", "push": "push"}
# gh pr create: em qualquer sítio do comando, mas só conta se a última linha do resultado for o URL do PR
PR_CREATE = re.compile(r"gh\s+pr\s+create\b")
PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*\\?(['\"]?)(\w+)\1")
ARITH = re.compile(r"\(\((?:[^()\n]|\([^()\n]*\))*\)\)")   # $(( … )) / (( … )): o << aí é shift, não heredoc
# aspas por fechar vão até ao fim, mesmo com \ final (sem isto, muitos \" depois de uma " sem par dão tempo quadrático)
QUOTED = re.compile(r"'[^']*+(?:'|\Z)|\"(?:\\.|[^\"\\])*+(?:\"|\\?\Z)", re.DOTALL)
CMD_MAX = 64 * 1024     # só se classifica o início do comando
BG_LAUNCHED = "Async agent launched"   # resultado do Agent lançado em fundo
CHECKS = ("pass", "fail", "pending", "skipping", "cancel")
LIVE_MARKS = (b'"tool_use"', b'"tool_result"', b'"thinking"', b'"pr-link"')
PENDING_N = 32
# por agente: progresso, linhas alteradas, modelo e ramo; só inteiros e valores que casam a regex
MODELO = re.compile(r"[\w.:-]{1,60}")
RAMO = re.compile(r"[A-Za-z0-9._/-]{1,80}")
TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
TAREFAS_MAX = 999
LINHAS_MAX = 10 ** 7
EDIT_TOOLS = ("Edit", "MultiEdit", "Write")


class _FileState:
    __slots__ = ("ino", "off", "act", "last_id", "pending", "bg", "pr", "ci", "primed",
                 "todo", "tk_n", "tk", "mec", "mais", "menos", "edit_ok", "aprox", "modelo", "ramo")

    def __init__(self, ino: int):
        self.ino, self.off = ino, 0
        self.act = None        # (kind, tool, error, at)
        self.last_id = None    # id do último tool_use (o erro só conta no resultado dele)
        self.pending = {}      # tool_use_id → (kind, classe GitHub | None, gh pr create?, desde | None, mais, menos),
        #                        à espera do resultado; desde = at do tool_use de um Bash em 1.º plano; mais/menos =
        #                        linhas estimadas pelo input de Edit/MultiEdit/Write (inteiros), senão None
        self.bg = {}           # tool_use_ids de Agents lançados em fundo (resultado "Async agent launched…")
        self.pr = None         # ("pr_aberto"|"pr_integrado", url | None, at)
        self.ci = None         # ("ci_vermelho"|"ci_verde", at)
        self.primed = False    # a 1.ª leitura não enche o feed (e o replay nunca o põe a True)
        self.todo = None       # (feitas, total) do último TodoWrite; None = todos não era lista
        self.tk_n = 0          # TaskCreate vistos
        self.tk = {}           # taskId → "completed" | "deleted" | "outro" (≤ TAREFAS_MAX ids)
        self.mec = None        # último mecanismo usado: "todo" | "task" | None
        self.mais = self.menos = 0   # linhas alteradas (tecto LINHAS_MAX)
        self.edit_ok = False   # houve pelo menos uma edição com sucesso
        self.aprox = False     # estimativa pelo input, ou a 1.ª leitura começou pelo fim (TAIL_FIRST)
        self.modelo = None     # message.model da última linha assistant seguida, se casa MODELO
        self.ramo = None       # gitBranch da última linha user/assistant seguida, se casa RAMO


_live: dict = {}        # transcript → _FileState
_seen: set = set()      # transcripts vistos nesta passagem do build_state
_feed: deque = deque(maxlen=FEED_N)


APROVACOES = {"geral": Path(os.environ.get("APROVACOES_DIR", "/data/aprovacoes"))}   # uma fonte, «geral»
FILA = Path(os.environ.get("DECISOES_FILA_DIR", "/data/decisoes-fila"))
TOKEN = secrets.token_urlsafe(32)   # muda a cada arranque; só sai no /api/state (atrás do basic auth)
# posto pelo proxy (X-Office-Segredo) depois do basic auth; vazio = sem verificação (dev local, testes). Nunca sai.
PROXY_SEGREDO = os.environ.get("OFFICE_PROXY_SEGREDO", "")
# modo Docker: o segredo vem de um ficheiro do volume proxy (escrito pelo configurar); a variável directa ganha.
# Com _FILE e o ficheiro em falta/inválido: 503 a tudo excepto GET /healthz (nunca aberto); relê no máximo 1×/s até o
# encontrar (o configurar pode criá-lo depois do arranque); depois de lido não muda até reiniciar.
PROXY_SEGREDO_FILE = "" if PROXY_SEGREDO else os.environ.get("OFFICE_PROXY_SEGREDO_FILE", "")
PROXY_SEGREDO_MAX = 1024
PROXY_FALTA = json.dumps({"error": "Falta configurar: corre `docker compose run --rm configurar`"},
                         ensure_ascii=False).encode()
_proxy = {"tentado": float("-inf")}
_proxy_lock = threading.Lock()
POST_MAX = 8192
QUADRO = Path(os.environ.get("QUADRO_DIR", "/data/quadro"))
QUADRO_POST_MAX = 16384
LANCAR = Path(os.environ.get("QUADRO_LANCAR_DIR", "/data/quadro-lancar"))
LANCAR_POST_MAX = 32768
BIBLIOTECA_DIR = Path(os.environ.get("BIBLIOTECA_DIR", "/data/biblioteca"))
ARQUIVO_FILA = Path(os.environ.get("ARQUIVO_FILA_DIR") or "/data/arquivo-fila")   # ADR-011: pedidos de arquivo
ARQUIVO_POST_MAX = 4096
# estado.json do runner (só leitura, sem segredos): chaves exactas, só enumerados; inválido ⇒ "runner": null
RUNNER_ESTADO_DIR = Path(os.environ.get("RUNNER_ESTADO_DIR", "/data/runner-estado"))
RUNNER_ESTADO_MAX = 4096
RUNNER_ENUM = {"claude": ("ok", "falta"), "credencial": ("ok", "falta", "modo"), "gh": ("ok", "sem sessão")}
RUNNER_CHAVES = ("v", "em", "versao", "claude", "credencial", "gh")
RUNNER_VERSAO = re.compile(r"\d+\.\d+\.\d+")


def _ler_segredo(caminho: str) -> str:
    # O_NOFOLLOW, ficheiro regular, ≤ 1 KiB, ASCII visível sem espaços; um \n final sai. Falta ou inválido ⇒ ""
    try:
        fd = os.open(caminho, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        dados = os.read(fd, PROXY_SEGREDO_MAX + 1)
    except OSError:
        return ""
    finally:
        os.close(fd)
    if len(dados) > PROXY_SEGREDO_MAX:
        return ""
    dados = dados.removesuffix(b"\n")
    return dados.decode("ascii") if re.fullmatch(rb"[\x21-\x7e]+", dados) else ""


def _segredo_proxy() -> str | None:
    # segredo exigido em X-Office-Segredo: "" = sem verificação (dev local, testes), None = falta configurar (503)
    global PROXY_SEGREDO
    if PROXY_SEGREDO or not PROXY_SEGREDO_FILE:
        return PROXY_SEGREDO
    with _proxy_lock:
        if not PROXY_SEGREDO and time.monotonic() - _proxy["tentado"] >= 1.0:
            _proxy["tentado"] = time.monotonic()
            PROXY_SEGREDO = _ler_segredo(PROXY_SEGREDO_FILE)
    return PROXY_SEGREDO or None


def _load_tz(name: str):
    # sem tzdata na imagem: UTC em vez de rebentar
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


LOCAL_TZ = timezone.utc
PUSH = push.Servico(Path(os.environ.get("PUSH_DIR", "/data/push")), "mailto:office@localhost", LOCAL_TZ, DONE_STATUS)
PUSH_EVERY_S = 30
PUSH_ROTAS = ("/api/push/subscrever", "/api/push/cancelar", "/api/push/teste")
CONFIG_BODY = b"{}"   # corpo do GET /api/config, pré-serializado em configurar
BIBLIOTECA = biblioteca.Indice({}, TASKS, APROVACOES)
BIBLIOTECA_ARQ = biblioteca.Indice({}, TASKS, APROVACOES, arquivo=True)


def configurar(cfg: dict, aviso: str | None = None) -> None:
    # troca as globais que vêm da config (arranque e testes); cada módulo troca só as suas
    global SQUADS, LEADS, FORMATO, LOCAL_TZ, CONFIG_BODY, BIBLIOTECA, BIBLIOTECA_ARQ
    SQUADS = tuple(s["id"] for s in cfg["squads"])
    LEADS = {s["id"]: s["lead"] for s in cfg["squads"]}
    FORMATO = {s["id"]: s["formato"] for s in cfg["squads"]}
    LOCAL_TZ = _load_tz(cfg["fuso"])
    modo = "docker" if os.environ.get("OFFICE_MODO") == "docker" else "vps"
    CONFIG_BODY = json.dumps({**officecfg.publico(cfg, aviso), "modo": modo}, ensure_ascii=False).encode()
    contacto = cfg["utilizador"]["contacto"]
    PUSH.tz = LOCAL_TZ
    PUSH.contacto = os.environ.get("PUSH_CONTACTO") or (f"mailto:{contacto}" if contacto else "mailto:office@localhost")
    quadro.configurar(cfg)
    lancar.configurar(cfg)
    push.configurar(cfg)
    biblioteca.configurar(cfg)
    entregas.configurar(cfg)
    replay.configurar(cfg)
    BIBLIOTECA = biblioteca.Indice(biblioteca.dirs(cfg, BIBLIOTECA_DIR), TASKS, APROVACOES)
    # o índice das `<fonte>/.arquivo/` só varre quando é pedido («Ver arquivados», link antigo de um item arquivado)
    BIBLIOTECA_ARQ = biblioteca.Indice(biblioteca.dirs(cfg, BIBLIOTECA_DIR), TASKS, APROVACOES, arquivo=True)


def _num(v) -> float | int | None:
    # número finito ≥ 0 (bool não conta)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return v


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _aware(s) -> datetime | None:
    # ISO → datetime com fuso; sem fuso = UTC
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fim(d: dict) -> dict:
    # ADR-013: da linha "result" só saem estas 5 chaves, sanitizadas; dos permission_denials só o nome da ferramenta
    pd = d.get("permission_denials")
    pd = pd if isinstance(pd, list) else []
    tools = []
    for x in pd:
        name = x.get("tool_name") if isinstance(x, dict) else None
        name = name if isinstance(name, str) and TOOL_NAME.fullmatch(name) else "outra"
        if name not in tools and len(tools) < FIM_TOOLS_MAX:
            tools.append(name)
    why = d.get("terminal_reason")
    http = d.get("api_error_status")
    return {"recusas": len(pd), "ferramentas": tools, "erro": d.get("is_error") is True,
            "motivo": None if why is None else why if isinstance(why, str) and REASON.fullmatch(why) else "outro",
            "http": http if isinstance(http, int) and not isinstance(http, bool) and 100 <= http <= 599 else None}


def _log_scan(path: Path, correr: bool = False) -> tuple:
    # linhas {"type": "result"} do fim do claude.log (uma por run; --resume acrescenta outra):
    # custo = total_cost_usd da última (já acumulado na sessão), turnos e duração = soma de todas,
    # fim = _fim da última (um --resume sem recusas limpa as anteriores); nada mais sai daqui.
    # correr: o log cresce a cada build e a linha result ainda não existe → só os 64 KB (sem releitura de 1 MiB)
    try:
        st = path.lstat()   # só ficheiro regular: um FIFO bloqueava o open com o _state_lock na mão; symlink não
    except OSError:
        return None, None, None, None
    if not stat.S_ISREG(st.st_mode):
        return None, None, None, None
    hit = _log_cache.get(str(path))
    if hit and hit[:2] == (st.st_mtime_ns, st.st_size) and (correr or not hit[3]):
        return hit[2]
    res = (None, None, None, None)
    results, last, parcial = [], None, False
    for janela in (LOG_TAIL, LOG_TAIL_MAX):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    return res
                f.seek(max(0, st.st_size - janela))
                tail = f.read(janela)
        except OSError:
            return res
        lines = tail.splitlines()
        cortada = st.st_size > janela and bool(lines) and b'"result"' in lines[0]
        results, last = [], None
        for line in lines:
            if b'"result"' not in line:
                continue
            try:
                d = json.loads(line)
            except (ValueError, RecursionError):   # JSON muito aninhado não pode deitar abaixo o /api/state
                continue
            if isinstance(d, dict) and d.get("type") == "result":
                results.append((_num(d.get("total_cost_usd")), _int(d.get("num_turns")), _num(d.get("duration_ms"))))
                last = d
        # a última linha result pode ter ficado cortada no início da janela: tenta uma vez com 1 MiB
        if st.st_size <= janela or (results and not cortada):
            break
        if correr:
            parcial = True
            break
    if results:
        turns = [t for _, t, _ in results if t is not None]
        ms = [x for _, _, x in results if x is not None]
        res = (results[-1][0], sum(turns) if turns else None, sum(ms) / 1000 if ms else None, _fim(last))
    _log_cache[str(path)] = (st.st_mtime_ns, st.st_size, res, parcial)
    return res


def _log_result(path: Path, correr: bool = False) -> tuple:
    # (custo, turnos, duração) do fim do claude.log
    return _log_scan(path, correr)[:3]


def _log_fim(path: Path) -> dict | None:
    # como acabou a última execução (_fim) ou None sem claude.log/linha "result"
    return _log_scan(path)[3]


def _windows(now: float) -> tuple[datetime, datetime]:
    # 00:00 de hoje e de há 6 dias, no fuso da config (certo nos dias de mudança de hora)
    today = datetime.fromtimestamp(now, LOCAL_TZ).date()
    return (datetime.combine(today, dtime(0), LOCAL_TZ),
            datetime.combine(today - timedelta(days=6), dtime(0), LOCAL_TZ))


def _empty_totals() -> dict:
    return {sq: {w: {"cost_usd": 0, "tasks": 0, "sem_custo": 0} for w in ("day", "week")} for sq in SQUADS}


def _add_total(totals: dict, squad: str, start: datetime | None, cost, wins: tuple) -> None:
    if start is None:
        return
    for w, since in zip(("day", "week"), wins):
        if start >= since:
            t = totals[squad][w]
            t["tasks"] += 1
            if cost is None:
                t["sem_custo"] += 1
            else:
                t["cost_usd"] += cost   # arredonda-se só no fim (_round_totals)


def _round_totals(totals: dict) -> None:
    for wins in totals.values():
        for t in wins.values():
            t["cost_usd"] = round(t["cost_usd"], 2)


def _waiting_item(m: dict, tid: str, squad: str) -> dict | None:
    # PR aberto no fim de uma tarefa worktree concluída (estado gravado na meta, não o real)
    pr = m.get("pr")
    hit = PR_OPEN.fullmatch(pr) if isinstance(pr, str) else None
    if m.get("status") not in WAIT_STATUS or not hit:
        return None
    return {"id": tid, "squad": squad, "project": m.get("project"), "status": m["status"], "pr_url": hit.group(1),
            "draft": hit.group(2) != "open", "finished": _iso_local(m.get("finished_at"))}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _add_msg(d, cut: float) -> None:
    if not isinstance(d, dict) or d.get("type") != "assistant" or not isinstance(d.get("message"), dict):
        return
    msg = d["message"]
    mid, u, model = msg.get("id"), msg.get("usage"), msg.get("model")
    if not isinstance(mid, str) or not isinstance(u, dict) or model == "<synthetic>":
        return
    tokens = tuple(_int(u.get(k)) or 0 for k in USAGE_SRC)
    if not any(tokens):
        return
    old = _msgs.get(mid)
    if old:   # o mesmo id em várias linhas/ficheiros: fica o máximo de cada campo
        _msgs[mid] = (*old[:2], *map(max, old[2:], tokens))
        return
    ts = _aware(d.get("timestamp"))
    try:
        if ts is None or ts.timestamp() < cut:
            return
    except (OverflowError, ValueError):
        return
    model = sys.intern(model) if isinstance(model, str) and re.fullmatch(r"[\w.:<>-]{1,60}", model) else "?"
    _msgs[sys.intern(mid)] = (ts.timestamp(), model, *tokens)


def _read_usage(path: Path, offset: int, cut: float) -> int:
    # lê a partir do offset, linha a linha; uma linha sem \n (a meio de ser escrita) fica para a próxima
    try:
        with path.open("rb") as f:
            f.seek(offset)
            for line in f:
                if not line.endswith(b"\n"):
                    break
                offset += len(line)
                if b'"assistant"' not in line or b'"usage"' not in line:
                    continue
                try:
                    _add_msg(json.loads(line), cut)
                except ValueError:
                    continue
    except OSError:
        pass
    return offset


def _usage_scan(now: float) -> None:
    global _usage
    cut, seen = now - WEEK_S, set()
    for path in CLAUDE.rglob("*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime < cut:
            continue
        key = str(path)
        seen.add(key)
        ino, offset = _files.get(key, (st.st_ino, 0))
        if ino != st.st_ino or st.st_size < offset:
            offset = 0   # substituído ou truncado: reler
        if st.st_size != offset:
            offset = _read_usage(path, offset, cut)
        _files[key] = (st.st_ino, offset)
    for key in set(_files) - seen:
        del _files[key]
    for mid in [k for k, v in _msgs.items() if v[0] < cut]:
        del _msgs[mid]
    windows: dict = {"5h": {}, "7d": {}}
    for ts, model, *tokens in _msgs.values():
        for name, since in (("5h", now - 5 * 3600), ("7d", cut)):
            if ts >= since:
                acc = windows[name].setdefault(model, [0, 0, 0, 0])
                for i, n in enumerate(tokens):
                    acc[i] += n
    _usage = {"status": "ok", "computed_at": _iso_ts(now),
              "windows": {w: {m: dict(zip(USAGE_KEYS, v)) for m, v in ms.items()} for w, ms in windows.items()}}


def _usage_loop() -> None:
    # thread de fundo: no máximo um varrimento por minuto; o pedido HTTP só lê _usage
    while True:
        t0 = time.monotonic()
        try:
            _usage_scan(time.time())
        except Exception as e:
            print(f"consumo: {type(e).__name__}", flush=True)
        time.sleep(max(1, USAGE_EVERY_S - (time.monotonic() - t0)))


def _push_loop() -> None:
    # vigia das notificações no telemóvel: lê o estado em cache (sem um 2.º build_state concorrente)
    while True:
        t0 = time.monotonic()
        try:
            if PUSH.estado()[0]:
                PUSH.ciclo(_state_cached()[1], time.time())
        except Exception as e:
            print(f"push: {type(e).__name__}", flush=True)
        time.sleep(max(1, PUSH_EVERY_S - (time.monotonic() - t0)))


def _costs(m: dict, squad: str, workdir: Path, correr: bool = False) -> tuple:
    cost, turns, dur = _num(m.get("cost_usd")), _int(m.get("turns")), _num(m.get("duration_s"))
    if cost is None and FORMATO.get(squad) == "worktree":
        cost, t, d = _log_result(workdir / "claude.log", correr)
        turns = turns if turns is not None else t
        dur = dur if dur is not None else d
    return cost, turns, dur


def _iso_local(naive) -> str | None:
    # hora sem fuso (lançadores antigos) = UTC
    if not isinstance(naive, str) or not naive:
        return None
    try:
        dt = datetime.fromisoformat(naive)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _issues(v) -> list[str]:
    # só texto curto; outra coisa não sai
    return [s[:300] for s in v if isinstance(s, str)] if isinstance(v, list) else []


def _iso_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _first_timestamp(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for _ in range(5):
                line = f.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = d.get("timestamp") if isinstance(d, dict) else None
                if ts and isinstance(ts, str):
                    return ts
    except OSError:
        pass
    return None


def _safe(s) -> bool:
    # nome de ficheiro/pasta vindo da meta: sem barras, sem ".." e não só pontos (".")
    return (isinstance(s, str) and bool(re.fullmatch(r"[A-Za-z0-9._-]+", s)) and ".." not in s
            and s.strip(".") != "")


def _dev_dir(m: dict, tid: str) -> Path | None:
    # pasta de sessões do worktree: o Claude Code troca cada carácter não alfanumérico do cwd por "-"
    # (officecfg.mangle); só se o worktree da meta for <…>/.worktrees/<id>, senão None (sem transcripts)
    wt = m.get("worktree")
    if not isinstance(wt, str):
        return None
    p = PurePosixPath(wt)
    name = officecfg.mangle(str(p))   # str(p): sem "/" final nem "//"
    if p.name == tid and p.parent.name == ".worktrees" and _safe(name):
        return CLAUDE / name
    return None


def _line_at(d: dict, mtime: float) -> str:
    ts = _aware(d.get("timestamp"))
    try:
        return _iso_ts(int(ts.timestamp() if ts else mtime))
    except (OverflowError, ValueError, OSError):   # datas extremas: a mtime do ficheiro
        return _iso_ts(int(mtime))


def _local_date(dt: datetime | None):
    # dia no fuso da config; datas extremas (ano 1 / 9999 com fuso) → None
    try:
        return dt.astimezone(LOCAL_TZ).date() if dt else None
    except (OverflowError, ValueError):
        return None


def _kind(name, inp) -> tuple[str, str]:
    # (kind, tool); o comando Bash só se lê para distinguir testes e é logo descartado
    if not (isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name)):
        return "outra", "outra"
    kind = KINDS.get(name, "outra")
    cmd = inp.get("command") if isinstance(inp, dict) else None
    if kind == "terminal" and isinstance(cmd, str) and TESTS_CMD.search(cmd):
        kind = "testes"
    return kind, name


def _strip_cmd(cmd: str) -> str:
    # tira corpos de heredoc (linha a linha, linear) e strings entre aspas
    out, end = [], None
    for ln in cmd.split("\n"):
        if end is not None:
            if ln.strip() == end:
                end = None
            continue
        m = HEREDOC.search(ARITH.sub("", ln))
        if m:
            end = m.group(2)
        out.append(ln)
    return QUOTED.sub("''", "\n".join(out))


def _gh_cls(name, inp) -> tuple:
    # (classe de GH_CMDS | None, tem "gh pr create")
    cmd = inp.get("command") if name == "Bash" and isinstance(inp, dict) else None
    if not isinstance(cmd, str):
        return None, False
    cmd = cmd[:CMD_MAX]
    bare = _strip_cmd(cmd)
    return next((cls for cls, rx in GH_CMDS if rx.search(bare)), None), bool(PR_CREATE.search(cmd))


def _created_url(text: str) -> str | None:
    # gh pr create escreve o URL do PR na última linha
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines and PR_URL.fullmatch(lines[-1]) else None


def _checks_event(text: str) -> str | None:
    # gh pr checks (sem TTY: nome\testado\ttempo\turl): fail → vermelho; pending ou nada reconhecível → nada
    st = [p[1].strip() for ln in text.splitlines() if len(p := ln.split("\t")) >= 2 and p[1].strip() in CHECKS]
    if "fail" in st:
        return "ci_vermelho"
    if st and all(x in ("pass", "skipping") for x in st):
        return "ci_verde"
    return None


def _result_text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b["text"] for b in c if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _emit(fs: _FileState, ctx: tuple, kind: str, tool, error: bool, at: str, pr_key=None) -> None:
    if fs.primed:
        e = {"at": at, "squad": ctx[0], "task": ctx[1], "role": ctx[2], "kind": kind, "tool": tool, "error": error}
        if pr_key:
            e["_pr"] = pr_key   # só para a deduplicação; nunca sai no /api/state
        _feed.append(e)


def _set_act(fs: _FileState, ctx: tuple, act: tuple) -> None:
    old, fs.act = fs.act, act
    if old is None or (old[0], old[2]) != (act[0], act[2]):
        _emit(fs, ctx, act[0], act[1], act[2], act[3])


def _pr_num(url) -> str | None:
    return url.rsplit("/", 1)[1] if url else None


def _gh_event(fs: _FileState, ctx: tuple, event: str, url, at: str, tool) -> None:
    key = None
    if event in ("pr_aberto", "pr_integrado"):
        url = url or (fs.pr[1] if fs.pr else None)   # merge sem URL mantém o anterior
        if (event == "pr_aberto" and fs.pr and fs.pr[0] == "pr_integrado"
                and (fs.pr[1] is None or _pr_num(fs.pr[1]) == _pr_num(url))):
            # PR já integrado (ex.: gh pr edit cujo corpo fala de gh pr create) não volta a aberto;
            # merge sem URL conhecido = assume-se o mesmo PR
            fs.pr = (fs.pr[0], url, fs.pr[2])
            return
        if fs.pr and fs.pr[0] == event and (fs.pr[1] is None or _pr_num(fs.pr[1]) == _pr_num(url)):
            fs.pr = (event, url, fs.pr[2])   # pr-link depois do gh pr create (ou vice-versa): o mesmo evento
            return
        fs.pr = (event, url, at)
        # o mesmo PR visto noutro transcript da tarefa (subagente + principal) não repete no feed
        key = (ctx[0], ctx[1], event, _pr_num(url)) if url else None
        # sem primed o _emit não faz nada: o replay (outra thread) nunca lê o _feed
        if key and fs.primed and any(e.get("_pr") == key for e in _feed):
            return
    elif event.startswith("ci_"):
        fs.ci = (event, at)
    _emit(fs, ctx, event, tool, event == "ci_vermelho", at, key)


def _modelo_ramo(d: dict, msg: dict, fs: _FileState, kind: str) -> None:
    # valem os da última linha seguida; o que não casa a regex fica None (nunca sai texto livre)
    if kind == "assistant":
        m = msg.get("model")
        fs.modelo = m if isinstance(m, str) and m != "<synthetic>" and MODELO.fullmatch(m) else None
    r = d.get("gitBranch")
    fs.ramo = (r if isinstance(r, str) and RAMO.fullmatch(r) and ".." not in r and r[0] not in "-/" else None)


def _nlinhas(s) -> int:
    # "a\nb\n" e "a\nb" = 2; vazio = 0
    return s.count("\n") + (not s.endswith("\n")) if isinstance(s, str) and s else 0


def _estimativa(name, inp) -> tuple:
    # (mais, menos) pelo input de uma edição, só como inteiros; as strings saem logo de âmbito
    if name not in EDIT_TOOLS or not isinstance(inp, dict):
        return None, None
    if name == "Write":
        return _nlinhas(inp.get("content")), 0
    edits = inp.get("edits") if name == "MultiEdit" else [inp]
    pares = [e for e in edits if isinstance(e, dict)] if isinstance(edits, list) else []
    return (sum(_nlinhas(e.get("new_string")) for e in pares), sum(_nlinhas(e.get("old_string")) for e in pares))


def _linhas(fs: _FileState, tur, e_mais: int, e_menos: int) -> None:
    # edição com sucesso: +/- do structuredPatch (só o 1.º carácter de cada linha); sem patch, a estimativa
    patch = tur.get("structuredPatch") if isinstance(tur, dict) else None
    mais = menos = 0
    if isinstance(tur, dict) and tur.get("type") == "create" and isinstance(tur.get("content"), str):
        mais = _nlinhas(tur["content"])   # Write de um ficheiro novo: structuredPatch [], o content é exacto
    elif isinstance(patch, list) and patch:
        for h in patch:
            for ln in (h.get("lines") if isinstance(h, dict) and isinstance(h.get("lines"), list) else ()):
                c = ln[:1] if isinstance(ln, str) else ""
                mais += c == "+"
                menos += c == "-"
    else:
        mais, menos, fs.aprox = e_mais, e_menos, True
    fs.mais, fs.menos = min(fs.mais + mais, LINHAS_MAX), min(fs.menos + menos, LINHAS_MAX)
    fs.edit_ok = True


def _progresso(fs: _FileState, name, inp) -> None:
    # TodoWrite: só todos[].status (vale a última); TaskCreate: +1; TaskUpdate: só taskId e status
    if name == "TodoWrite":
        todos = inp.get("todos") if isinstance(inp, dict) else None
        fs.todo = ((sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed"), len(todos))
                   if isinstance(todos, list) else None)
        fs.mec = "todo"
    elif name == "TaskCreate":
        fs.tk_n = min(fs.tk_n + 1, LINHAS_MAX)
        fs.mec = "task"
    elif name == "TaskUpdate":
        fs.mec = "task"
        tid = inp.get("taskId") if isinstance(inp, dict) else None
        st = inp.get("status") if isinstance(inp, dict) else None
        if not (isinstance(tid, str) and TASK_ID.fullmatch(tid) and isinstance(st, str)):
            return
        if tid in fs.tk or len(fs.tk) < TAREFAS_MAX:
            fs.tk[tid] = st if st in ("completed", "deleted") else "outro"


def _extras(fs: _FileState | None) -> dict:
    # as 4 chaves por agente e do líder no /api/state; sem transcript seguido, tudo None
    if fs is None:
        return {"tarefas": None, "linhas": None, "modelo": None, "ramo": None}
    feitas = total = 0
    if fs.mec == "todo" and fs.todo:
        feitas, total = fs.todo
    elif fs.mec == "task":
        estados = list(fs.tk.values())
        total, feitas = fs.tk_n - estados.count("deleted"), estados.count("completed")
    total = max(0, min(total, TAREFAS_MAX))
    feitas = max(0, min(feitas, total))
    return {"tarefas": {"feitas": feitas, "total": total} if total else None,
            "linhas": {"mais": fs.mais, "menos": fs.menos, "aprox": fs.aprox} if fs.edit_ok else None,
            "modelo": fs.modelo, "ramo": fs.ramo}


def _live_line(d: dict, fs: _FileState, ctx: tuple, mtime: float) -> None:
    kind = d.get("type")
    if kind == "pr-link":
        url = d.get("prUrl")
        _gh_event(fs, ctx, "pr_aberto", url if isinstance(url, str) and PR_URL.fullmatch(url) else None,
                  _line_at(d, mtime), None)
        return
    msg = d.get("message")
    if kind in ("assistant", "user") and isinstance(msg, dict):
        _modelo_ramo(d, msg, fs, kind)
    content = msg.get("content") if kind in ("assistant", "user") and isinstance(msg, dict) else None
    if not isinstance(content, list):
        return
    at = _line_at(d, mtime)
    for b in content:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if kind == "assistant" and bt == "tool_use":
            act_kind, tool = _kind(b.get("name"), b.get("input"))
            uid = b.get("id") if isinstance(b.get("id"), str) else None
            fs.last_id = uid
            if uid:
                # do input do Bash só se lê, além do comando (descartado), o booleano run_in_background
                inp = b.get("input")
                fg = b.get("name") == "Bash" and not (isinstance(inp, dict) and inp.get("run_in_background") is True)
                fs.pending[uid] = (act_kind, *_gh_cls(b.get("name"), inp), at if fg else None,
                                   *_estimativa(b.get("name"), inp))
                while len(fs.pending) > PENDING_N:
                    fs.pending.pop(next(iter(fs.pending)))
            _progresso(fs, b.get("name"), b.get("input"))
            _set_act(fs, ctx, (act_kind, tool, False, at))
        elif kind == "assistant" and bt == "thinking":
            fs.last_id = None
            _set_act(fs, ctx, ("pensar", None, False, at))
        elif kind == "user" and bt == "tool_result":
            rid, err = b.get("tool_use_id"), b.get("is_error") is True
            nada = (None, None, False, None, None, None)
            was, cls, create, _, e_mais, e_menos = fs.pending.pop(rid, nada) if isinstance(rid, str) else nada
            if e_mais is not None and not err:
                _linhas(fs, d.get("toolUseResult"), e_mais, e_menos)
            if was == "delegar" and _result_text(b.get("content")).startswith(BG_LAUNCHED):
                fs.bg[rid] = True
                while len(fs.bg) > PENDING_N:
                    fs.bg.pop(next(iter(fs.bg)))
            if err and rid is not None and rid == fs.last_id and fs.act:
                _set_act(fs, ctx, (*fs.act[:2], True, at))
            if cls == "checks":
                if ev := _checks_event(_result_text(b.get("content"))):
                    _gh_event(fs, ctx, ev, None, at, "Bash")
            elif cls == "watch":
                _gh_event(fs, ctx, "ci_vermelho" if err else "ci_verde", None, at, "Bash")
            elif not err:
                url = _created_url(_result_text(b.get("content"))) if create else None
                if url:
                    _gh_event(fs, ctx, "pr_aberto", url, at, "Bash")
                elif cls in GH_EVENT:
                    _gh_event(fs, ctx, GH_EVENT[cls], None, at, "Bash")


def _live_read(path: Path, fs: _FileState, st, ctx: tuple) -> None:
    # só os bytes novos; na 1.ª leitura só os últimos TAIL_FIRST; linha sem \n fica para a próxima
    try:
        with path.open("rb") as f:
            if not fs.primed and st.st_size > TAIL_FIRST:
                f.seek(st.st_size - TAIL_FIRST)
                f.readline()   # linha cortada
                fs.off = f.tell()
                fs.aprox = True   # o que ficou antes não se contou
            else:
                f.seek(fs.off)
            for line in f:
                if not line.endswith(b"\n"):
                    break
                fs.off += len(line)
                if not any(mark in line for mark in LIVE_MARKS):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    _live_line(d, fs, ctx, st.st_mtime)
    except OSError:
        pass


def _track(path: Path, st, ctx: tuple) -> _FileState:
    # ctx = (squad, tarefa, papel); inode novo ou ficheiro mais curto = recomeça
    key = str(path)
    fs = _live.get(key)
    if fs is None or fs.ino != st.st_ino or st.st_size < fs.off:
        fs = _live[key] = _FileState(st.st_ino)
    _seen.add(key)
    if st.st_size != fs.off:
        _live_read(path, fs, st, ctx)
    fs.primed = True
    return fs


def _act(fs: _FileState | None) -> dict | None:
    if fs is None or fs.act is None:
        return None
    out = dict(zip(("kind", "tool", "error", "at"), fs.act))
    p = fs.pending.get(fs.last_id) if fs.last_id else None
    if p is not None and p[3]:   # Bash em 1.º plano à espera do resultado: o cliente mostra «terminal há N min»
        out["desde"] = p[3]
    return out


def _stuck(fs: _FileState | None, mtime: float, now: float) -> bool:
    # o ÚLTIMO tool_use sem resultado (delegar não conta: quem trabalha é o subagente) e transcript parado;
    # pendentes antigos (interrompidos e retomados) não contam; um Bash em 1.º plano só aos STUCK_TERM_S
    p = fs.pending.get(fs.last_id) if fs is not None and fs.last_id else None
    return p is not None and p[0] != "delegar" and now - mtime > (STUCK_TERM_S if p[3] else STUCK_S)


def _gh_task(states: list) -> dict:
    prs = sorted((fs.pr for fs in states if fs.pr), key=lambda p: p[2])
    cis = sorted((fs.ci for fs in states if fs.ci), key=lambda c: c[1])
    url = next((p[1] for p in reversed(prs) if p[1]), None)
    ats = [x[-1] for x in prs[-1:] + cis[-1:]]
    return {"pr_url": url, "pr": {"pr_aberto": "aberto", "pr_integrado": "integrado"}[prs[-1][0]] if prs else None,
            "ci": {"ci_vermelho": "vermelho", "ci_verde": "verde"}[cis[-1][0]] if cis else None,
            "at": max(ats) if ats else None}


def _session(project_dir: Path, stem: str, now: float, track, ctx: tuple, states: list, latest: bool = True) -> tuple:
    # uma sessão: mtime do <stem>.jsonl + os seus subagentes
    # track: "all" (a correr: principal + subagentes), "ended" (só a principal e os já seguidos) ou None
    # latest: é a sessão principal mais recente (subagentes sem toolUseId só estão vivos nesta)
    main = project_dir / f"{stem}.jsonl"
    st = main.stat()
    agents, last = [], st.st_mtime
    main_fs = _track(main, st, ctx) if track else None
    if main_fs:
        states.append(main_fs)
    for meta in (project_dir / stem / "subagents").glob("agent-*.meta.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # ValueError inclui UnicodeDecodeError
            continue
        if not isinstance(m, dict):
            continue
        log = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
        mtime = log.stat().st_mtime if log.exists() else meta.stat().st_mtime
        finished = bool(m.get("finished"))
        role = m.get("agentType") if _safe(m.get("agentType")) else "general"
        # vivo = o Agent que o lançou ainda espera resultado na sessão-mãe (subagentes de sessões antigas não);
        # lançado em fundo (o Agent responde logo): vivo enquanto não acabar, só na sessão principal mais recente
        tuid = m.get("toolUseId")
        bg = m.get("requestShape") == "background" or (isinstance(tuid, str) and main_fs is not None
                                                        and tuid in main_fs.bg)
        if bg:
            alive = track == "all" and latest and not finished
        else:
            alive = track == "all" and (tuid in main_fs.pending if isinstance(tuid, str) and main_fs else latest)
        fs = None
        if log.exists() and (track == "all" or (track == "ended" and str(log) in _live)):
            fs = _track(log, log.stat(), (ctx[0], ctx[1], role))
            states.append(fs)
        agents.append({
            "role": role,
            "desc": d[:80] if isinstance(d := m.get("description"), str) else "",
            "start": _first_timestamp(log) if log.exists() else None,
            "end": _iso_ts(mtime) if finished else None,
            "last": _iso_ts(mtime),
            "finished": finished,
            "active": not finished and now - mtime < ACTIVE_S,
            "act": _act(fs) if alive else None,
            "stuck": alive and not finished and _stuck(fs, mtime, now),
            **_extras(fs),
        })
        last = max(last, mtime)
    agents.sort(key=lambda a: a["start"] or "")
    return agents, last, (main_fs, st.st_mtime)


def _agents(project_dir: Path, now: float, track, ctx: tuple, states: list) -> tuple:
    # worktree: todas as sessões do worktree; a principal (lead) é a mais recente
    agents, last, lead = [], 0.0, (None, 0.0)
    sessions = [(p.stat().st_mtime, p.stem) for p in project_dir.glob("*.jsonl")]
    newest = max(sessions, default=(0, None))[1]
    for _, stem in sessions:
        a, t, main = _session(project_dir, stem, now, track, ctx, states, stem == newest)
        agents += a
        last = max(last, t)
        if main[1] >= lead[1]:
            lead = main
    agents.sort(key=lambda a: a["start"] or "")
    return agents, last, lead


def _meta_agents(m: dict, now: float, track, ctx: tuple, states: list) -> tuple:
    # squads sessao: só a sessão indicada na meta (a pasta do repo tem todas)
    sid, cdir = m.get("session_id"), m.get("claude_dir")
    if not (_safe(sid) and _safe(cdir)) or not (CLAUDE / cdir / f"{sid}.jsonl").is_file():
        return [], 0.0, (None, 0.0)
    return _session(CLAUDE / cdir, sid, now, track, ctx, states)


def _heartbeat_age(workdir: Path, now: float) -> float | None:
    # ficheiro tocado pelo lançador a cada 60 s; sem ficheiro (lançadores antigos) = None
    try:
        return now - (workdir / "heartbeat").stat().st_mtime
    except OSError:
        return None


def _empty_days(now: float) -> dict:
    today = datetime.fromtimestamp(now, LOCAL_TZ).date()
    return {(today - timedelta(days=i)).isoformat(): {sq: {"cost_usd": 0, "done": 0, "hours": 0} for sq in SQUADS}
            for i in range(HISTORY_DAYS - 1, -1, -1)}


def _add_day(days: dict, squad: str, start: datetime | None, done: bool, cost, dur) -> None:
    d = _local_date(start)
    day = days.get(d.isoformat()) if d else None
    if day is None:
        return
    t = day[squad]
    t["cost_usd"] += cost or 0
    t["done"] += done
    t["hours"] += (dur or 0) / 3600


def _history(days: dict, today_tasks: list) -> tuple[list, dict]:
    # arredonda-se só no fim; today_tasks = [(início, custo, id, squad, project, start, end)]
    today = list(days.values())[-1]
    top = max((t for t in today_tasks if t[1] is not None), key=lambda t: t[1], default=None)
    summary = {
        "cost_usd": round(sum(v["cost_usd"] for v in today.values()), 2),
        "done": sum(v["done"] for v in today.values()),
        "hours": round(sum(v["hours"] for v in today.values()), 1),
        "top": {"id": top[2], "project": top[4], "cost_usd": round(top[1], 2)} if top else None,
        "timeline": [dict(zip(("id", "squad", "project", "start", "end"), t[2:]))
                     for t in sorted(today_tasks, key=lambda t: t[0])],
    }
    history = [{"date": d, **{sq: {"cost_usd": round(v["cost_usd"], 2), "done": v["done"],
                                   "hours": round(v["hours"], 1)} for sq, v in sqs.items()}}
               for d, sqs in days.items()]
    return history, summary


def _intervalo(m: dict, squad: str, vivo: bool, inicio: datetime, now: float) -> dict | None:
    # {squad, ini, fim} de uma tarefa que toca hoje («O meu dia»); só squad e horas, nunca texto.
    # A correr: o finished_at é de uma execução anterior, conta só se viva (fim null)
    start = _aware(m.get("started_at"))
    if start is None or start.timestamp() > now:
        return None
    fim = None if m.get("status") == "a correr" else _aware(m.get("finished_at"))
    if fim is None:
        if not vivo:
            return None
    elif fim < inicio:
        return None
    return {"squad": squad, "ini": _iso_local(m.get("started_at")),
            "fim": _iso_local(m.get("finished_at")) if fim else None}


def _ordem(iso) -> tuple:
    # mais antigo primeiro, sem data no fim
    d = _aware(iso)
    return (d is None, d.timestamp() if d else 0.0)


def _espera(aprov: list, waiting: list) -> dict:
    # regra única de «à tua espera» (D-3): aprovações em aberto + PRs à espera (drafts contam); recusas não
    itens = []
    for a in aprov:
        fonte, nome = a.get("fonte"), a.get("ficheiro")
        if not (isinstance(fonte, str) and isinstance(nome, str)) or not (a.get("abertas") or 0) > 0:
            continue
        try:   # a aprovação espera desde as 00:00 do dia em que foi criada, no fuso da config
            desde = datetime.combine(date.fromisoformat(a.get("criado")), dtime(0), LOCAL_TZ).isoformat()
        except (TypeError, ValueError):
            desde = None
        itens.append({"tipo": "aprovacao", "chave": f"apr:{fonte}/{nome}", "fonte": fonte, "ficheiro": nome,
                      "titulo": a.get("titulo"), "criado": a.get("criado"), "idade_dias": a.get("idade_dias"),
                      "abertas": a["abertas"], "desde": desde})
    for w in waiting:
        itens.append({"tipo": "pr", "chave": f"pr:{w['squad']}/{w['id']}", "id": w["id"], "squad": w["squad"],
                      "project": w["project"], "pr_url": w["pr_url"], "draft": w["draft"], "finished": w["finished"],
                      "desde": w["finished"]})
    itens.sort(key=lambda i: _ordem(i["desde"]))
    return {"n": len(itens), "itens": itens}


def _alerts(dead: list) -> dict:
    items = [f"tarefa:{tid}" for tid in dead]
    return {"count": len(items), "items": sorted(items)}


def _metas() -> list:
    # (meta.json, squad da pasta ou None); worktree: TASKS/<id>, a squad vem da meta (_meta_squad)
    roots = [(None, TASKS)] + [(sq, SQUADS_DIR / sq) for sq in SQUADS if FORMATO[sq] == "sessao"]
    return [(p, squad) for squad, root in roots if root.is_dir()
            for p in sorted(root.glob("*/meta.json"), reverse=True)]


def _meta_squad(m: dict) -> str | None:
    # meta de TASKS: "squad" da meta ou, sem ela, a 1.ª squad worktree; squad fora das worktree → None (ignorada)
    sq = m.get("squad", next((s for s in SQUADS if FORMATO[s] == "worktree"), None))
    return sq if isinstance(sq, str) and FORMATO.get(sq) == "worktree" else None


def build_state(now: float | None = None) -> dict:
    global _aux
    now = time.time() if now is None else now
    aux_metas, aux_result, aux_titulo = [], {}, {}
    entregue = False   # «Primeiros passos»: há uma tarefa dev entregue (com PR), de qualquer idade
    tasks, waiting, totals, wins = [], [], _empty_totals(), _windows(now)
    days, today_tasks, dead_ids = _empty_days(now), [], []
    amostras, vivas = {}, []   # barra de progresso (progresso.py): durações das concluídas e tarefas a preencher
    falhas = []   # falhas_exec: (fim em epoch, item) das terminadas há ≤ AGENTS_S em falha ou com erro da API
    ivs = []      # today.intervalos: {squad, ini, fim} das tarefas sem dry-run que tocam hoje
    today = datetime.fromtimestamp(now, LOCAL_TZ).date()
    _seen.clear()
    for meta_path, squad in _metas():
        try:
            m = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):   # ValueError inclui UnicodeDecodeError
            continue
        if not isinstance(m, dict):
            continue
        squad = squad or _meta_squad(m)
        if squad is None:
            continue
        tid = str(m.get("id") or meta_path.parent.name)
        dev = FORMATO[squad] == "worktree"
        workdir = meta_path.parent
        status = m.get("status", "?")
        running = status == "a correr"
        # totais, histórico e "à tua espera" contam todas as metas, antes do corte
        cost, turns, dur = _costs(m, squad, workdir, running)
        start = _aware(m.get("started_at"))
        _add_total(totals, squad, start, cost, wins)
        _add_day(days, squad, start, status in DONE_STATUS, cost, dur)
        if status in DONE_STATUS and m.get("mode") != "dry-run":
            fim = _aware(m.get("finished_at"))
            d = progresso.duracao(start.timestamp() if start else None, fim.timestamp() if fim else None, dur)
            if d is not None:
                ref = fim or start
                amostras.setdefault((squad, m.get("mode", "task")), []).append((ref.timestamp() if ref else 0.0, d))
        if start and _local_date(start) == today:
            today_tasks.append((start.timestamp(), cost, tid, squad, m.get("project"), _iso_local(m.get("started_at")),
                                None if running else _iso_local(m.get("finished_at"))))
        task = m.get("task")
        h1 = quadro.titulo_task(task)
        h1_tab = h1 or (m["titulo"] if isinstance(m.get("titulo"), str) else None)
        titulo = (" ".join(reader.mask(h1_tab).split())[:TITULO_MAX] or None) if h1_tab else None   # separador
        if "dry-run" not in (m.get("mode"), status):
            aux_metas.append((squad, tid, frozenset(INBOX_REF.findall(task)) if isinstance(task, str) else frozenset(),
                              quadro.chave_titulo(h1)))
            if h1:   # em bruto: o quadro.py mascara só o que emite
                aux_titulo[(squad, tid)] = h1
            if isinstance(m.get("result"), str):
                aux_result[(squad, tid)] = m["result"]
            entregue = entregue or (dev and status in ("concluída", "parcial (PR draft)"))
        dry = "dry-run" in (m.get("mode"), status)
        item = _waiting_item(m, tid, squad) if dev else None
        if item:
            waiting.append((item["finished"] or "", _iso_local(m.get("started_at")) or "", item, dry))
        # ADR-013: como acabou, só worktree terminadas há ≤ AGENTS_S (a correr: o result é de uma execução anterior)
        fin_at = None if running else _aware(m.get("finished_at"))
        recente = fin_at is not None and now - fin_at.timestamp() <= AGENTS_S
        fim = _log_fim(workdir / "claude.log") if dev and recente else None
        if recente and not dry and (status in (quadro.FAIL if dev else quadro.SQUAD_FAIL) or (fim and fim["erro"])):
            falhas.append((fin_at.timestamp(), {
                "id": tid, "squad": squad, "project": m.get("project"), "status": quadro.txt(str(status), 30),
                "motivo": fim["motivo"] if fim else None, "http": fim["http"] if fim else None,
                "erro": bool(fim and fim["erro"]), "finished": _iso_local(m.get("finished_at"))}))
        meta_age = now - meta_path.stat().st_mtime
        if not running and meta_age > RECENT_S:
            continue
        beat = _heartbeat_age(workdir, now) if running else None
        dead = beat is not None and beat > DEAD_S
        live = running and not dead
        lead = LEADS[squad]   # sessão principal
        track = "all" if live else ("ended" if running or meta_age <= AGENTS_S else None)
        if m.get("mode") == "dry-run":   # ensaio: não segue transcripts
            track = None
        ctx, states = (squad, tid, lead), []
        if track is None:
            agents, last, main = [], 0.0, (None, 0.0)
        elif dev:
            project_dir = _dev_dir(m, tid)
            agents, last, main = (_agents(project_dir, now, track, ctx, states)
                                  if project_dir is not None and project_dir.is_dir() else ([], 0.0, (None, 0.0)))
        else:
            agents, last, main = _meta_agents(m, now, track, ctx, states)
        last = max(last, meta_path.stat().st_mtime)
        idle = now - last
        working = [a["role"] for a in agents if a["active"]] if not dead else []
        lead_active = live and not working and idle < ACTIVE_S
        if dead:
            dead_ids.append(tid)
            shown = "morreu"
        else:   # heartbeat fresco: o processo está vivo, nunca "parada?"
            shown = "parada?" if running and beat is None and idle > STALE_S else status
        if not dry:
            iv = _intervalo(m, squad, live and shown != "parada?", wins[0], now)
            if iv:
                ivs.append(iv)
        tasks.append({
            "id": tid,
            "project": m.get("project"),
            "mode": m.get("mode", "task"),
            "titulo": titulo,
            "status": shown,
            "running": live,
            "branch": m.get("branch") if dev else None,
            "pr": (m.get("pr") or None) if dev else None,
            "started": _iso_local(m.get("started_at")),
            "finished": _iso_local(m.get("finished_at")) if not running else None,
            "last_activity": _iso_ts(last),
            "phases": {d: dev and (workdir / f"{d}.md").exists() for d in ("SPEC", "PLAN", "QA")},
            "auto_resumes": m.get("auto_resumes", 0) if dev else 0,
            "issues": _issues(m.get("issues")),
            "agents": agents,
            "working": working + ([lead] if lead_active else []),
            "squad": squad,
            "cost_usd": cost,
            "turns": turns,
            "duration_s": dur,
            "lead": {"role": lead, "act": _act(main[0]) if live else None,
                     "stuck": live and _stuck(main[0], main[1], now), **_extras(main[0])},
            "gh": _gh_task(states),
            "progress": None,
            "resultado": quadro.txt(m.get("result"), quadro.RESULTADO_MAX) or None,
            "fim": fim,
        })
        if live:
            t = tasks[-1]
            fs = (progresso.fases(t["phases"], [a["role"] for a in agents], bool(t["pr"] or t["gh"]["pr"]))
                  if dev and t["mode"] == "goal" else None)
            vivas.append((t, start.timestamp() if start else now, (squad, t["mode"]), fs))
    for t, ini, chave, fs in vivas:   # depois do ciclo: a amostra já tem todas as metas
        tip, n = progresso.tipico(amostras.get(chave, []))
        t["progress"] = progresso.progresso(now - ini, tip, n, fs)
    for key in set(_live) - _seen:   # memória só para tarefas vivas/recentes
        del _live[key]
    _round_totals(totals)
    tasks.sort(key=lambda t: (not t["running"], t["started"] or ""), reverse=False)
    running = [t for t in tasks if t["running"]]
    rest = sorted((t for t in tasks if not t["running"]), key=lambda t: t["started"] or "", reverse=True)
    waiting.sort(key=lambda w: w[:2], reverse=True)
    history, today_sum = _history(days, today_tasks)
    today_sum["inicio"] = wins[0].isoformat()
    today_sum["intervalos"] = sorted(ivs, key=lambda iv: _aware(iv["ini"]))[-INTERVALOS_N:]
    _aux = {"metas": aux_metas, "result": aux_result, "titulo": aux_titulo}
    aprov = aprovacoes.listar(APROVACOES, FILA, LOCAL_TZ, now)
    return {"now": _iso_ts(now), "tasks": running + rest, "totals": totals,
            "waiting": [w[2] for w in waiting], "usage": _usage,
            "feed": sorted(({k: v for k, v in e.items() if k != "_pr"} for e in reversed(_feed)),
                           key=lambda e: e["at"], reverse=True),
            "alerts": _alerts(dead_ids), "history": history, "today": today_sum,
            "aprovacoes": aprov, "token": TOKEN,
            "runner": runner_estado(), "tarefa_entregue": entregue,
            "espera": _espera(aprov, [w[2] for w in waiting if not w[3]]),
            "falhas_exec": [f for _, f in sorted(falhas, key=lambda f: f[0], reverse=True)]}


def runner_estado() -> dict | None:
    # <RUNNER_ESTADO_DIR>/estado.json: ficheiro regular (sem symlink), <= 4 KB, objecto com as chaves exactas e só
    # valores conhecidos; qualquer falha ⇒ None (o painel diz «o runner ainda não deu sinal»)
    try:
        fd = os.open(RUNNER_ESTADO_DIR / "estado.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, RUNNER_ESTADO_MAX + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        d = json.loads(raw.decode("utf-8")) if len(raw) <= RUNNER_ESTADO_MAX else None
    except ValueError:
        return None
    if not isinstance(d, dict) or sorted(d) != sorted(RUNNER_CHAVES) or type(d["v"]) is not int or d["v"] != 1:
        return None
    if not isinstance(d["versao"], str) or not RUNNER_VERSAO.fullmatch(d["versao"]):
        return None
    if any(d[k] not in vals for k, vals in RUNNER_ENUM.items()):
        return None
    try:
        if datetime.fromisoformat(d["em"]).tzinfo is None:
            return None
    except (TypeError, ValueError):
        return None
    return {k: d[k] for k in RUNNER_CHAVES}


def _log_delivery(path: Path) -> str | None:
    # leitor e /api/entregas: campo "result" da última linha result do fim do claude.log (reader.log_delivery)
    return reader.log_delivery(path, LOG_TAIL, LOG_TAIL_MAX)


def _entrypoint(path: Path):
    # 1.º "entrypoint" nas primeiras linhas do transcript ("sdk-cli" = headless; "cli" = sessão interactiva)
    try:
        with path.open("rb") as f:
            for _ in range(ENTRYPOINT_LINES):
                line = f.readline()
                if not line:
                    break
                if b'"entrypoint"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and "entrypoint" in d:
                    return d["entrypoint"]
    except OSError:
        pass
    return None


def _squad_session(path: Path, folder: Path) -> bool:
    # ficheiro real (symlink não), directamente na pasta da squad, de uma sessão headless
    try:
        if path.is_symlink() or not path.is_file() or path.resolve().parent != folder.resolve():
            return False
    except OSError:
        return False
    return _entrypoint(path) == "sdk-cli"


def _transcript_files(m: dict, squad: str, tid: str) -> list[Path]:
    # só via meta.json: nunca há parâmetro com caminho ou nome de sessão
    if FORMATO.get(squad) == "worktree":
        d = _dev_dir(m, tid)
        return [p for p in sorted(d.glob("*.jsonl")) if _squad_session(p, d)] if d is not None and d.is_dir() else []
    sid, cdir = m.get("session_id"), m.get("claude_dir")
    if not (_safe(cdir) and isinstance(sid, str) and SESSION_ID.fullmatch(sid)):
        return []
    path = CLAUDE / cdir / f"{sid}.jsonl"
    return [path] if _squad_session(path, CLAUDE / cdir) else []


def transcript(seg: str, query: str) -> dict | None:
    # /api/transcript/<id>?squad=&page=&f=&q= ; None = 404 (sem dizer porquê)
    qs = {k: v[0] for k, v in parse_qs(query).items()}
    tid, want = unquote(seg), qs.get("squad")
    if not _safe(tid) or (want is not None and want not in SQUADS):
        return None
    found = []
    for sq in [want] if want else SQUADS:
        path = (TASKS if FORMATO[sq] == "worktree" else SQUADS_DIR / sq) / tid / "meta.json"
        m = _read_json(path) if path.is_file() else None
        if isinstance(m, dict) and (FORMATO[sq] == "sessao" or _meta_squad(m) == sq):
            found.append((sq, path, m))
    if not found:
        return None
    squad, meta_path, m = found[0]
    files = _transcript_files(m, squad, tid)
    if not files:
        return None
    entries, truncated = reader.parse(files)
    result = m.get("result")
    if not (isinstance(result, str) and result.strip()) and FORMATO[squad] == "worktree":
        result = _log_delivery(meta_path.parent / "claude.log")
    try:
        page = int(qs.get("page", "0"))
    except ValueError:
        page = 0
    f = qs.get("f") if qs.get("f") in reader.FILTERS else "tudo"
    return {"id": tid, "squad": squad, "delivery": reader.delivery(result),
            **reader.page(entries, f, qs.get("q", ""), page), "truncated": truncated}


# replay (ADR-015): o mesmo _live_line sobre um estado próprio, nunca o _live; cache e lock dentro do objecto
REPLAY = replay.Cronologias(_live_line, _FileState, LIVE_MARKS, ACTIVE_S, STUCK_S, STUCK_TERM_S)


def _epoch(dt: datetime | None) -> int | None:
    try:
        return int(dt.timestamp()) if dt else None
    except (OverflowError, ValueError, OSError):
        return None


def _fim_a_correr(meta_path: Path, pdir: Path | None, stems: list, now: float) -> int | None:
    # meta «a correr»: a regra do ao vivo diz se ainda está viva (None); morta (heartbeat parado há > DEAD_S) ou
    # parada (sem heartbeat e sem actividade há > STALE_S), acaba no maior entre heartbeat + DEAD_S e a última
    # actividade (meta e transcripts) + STALE_S, nunca depois de agora
    def mtime(p: Path) -> float | None:
        try:
            return os.lstat(p).st_mtime
        except OSError:
            return None
    paths = [meta_path] + [q for st in stems for q in (pdir / f"{st}.jsonl",
                                                      *(pdir / st / "subagents").glob("agent-*.jsonl"))]
    last = max((m for q in paths if (m := mtime(q)) is not None), default=0.0)
    beat = mtime(meta_path.parent / "heartbeat")
    if (now - beat <= DEAD_S) if beat is not None else (now - last <= STALE_S):
        return None
    return int(min(now, max(last + STALE_S, beat + DEAD_S if beat is not None else 0)))


def _replay_subagentes(pdir: Path, stems: list, inicio: int) -> list:
    # metas dos subagentes da tarefa com o .jsonl tocado no dia: no máximo REPLAY_AGENTES, os de mais bytes (os mais
    # activos), pela ordem de antes (sessão, nome); o tecto põe limite à leitura e ao tamanho de cada quadro
    cands = []
    for stem in stems:
        for meta in sorted((pdir / stem / "subagents").glob("agent-*.meta.json")):
            try:
                st = os.lstat(meta.with_name(meta.name.replace(".meta.json", ".jsonl")))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_mtime >= inicio:
                cands.append((len(cands), st.st_size, meta))
    escolhidos = sorted(cands, key=lambda c: (-c[1], c[0]))[:replay.REPLAY_AGENTES]
    return [meta for _, _, meta in sorted(escolhidos, key=lambda c: c[0])]


def _replay_tarefas(inicio: int, fim: int, now: float) -> list:
    # metas sem dry-run das raízes (só dentro delas: resolve + pasta-mãe, ficheiro regular) que tocam [inicio, fim];
    # chamar com REPLAY.lock
    claude_real = CLAUDE.resolve()
    out = []
    roots = [(None, TASKS)] + [(sq, SQUADS_DIR / sq) for sq in SQUADS if FORMATO[sq] == "sessao"]
    for root_sq, root in roots:
        if not root.is_dir():
            continue
        real = root.resolve()
        for meta_path in sorted(root.glob("*/meta.json")):
            try:
                if meta_path.resolve().parent.parent != real or not stat.S_ISREG(meta_path.stat().st_mode):
                    continue
            except (OSError, RuntimeError):
                continue
            m = _read_json(meta_path)
            if not isinstance(m, dict) or "dry-run" in (m.get("mode"), m.get("status")):
                continue
            squad = root_sq or _meta_squad(m)
            if squad is None:
                continue
            status = m.get("status") if isinstance(m.get("status"), str) else "?"
            ini = _epoch(_aware(m.get("started_at")))
            fin = None if status == "a correr" else _epoch(_aware(m.get("finished_at")))
            if ini is None or ini >= fim or (fin < inicio if fin is not None else status != "a correr"):
                continue
            tid = str(m.get("id") or meta_path.parent.name)
            dev = root_sq is None
            fases = {}
            for f in ("SPEC", "PLAN", "QA"):
                try:
                    fases[f] = math.ceil((meta_path.parent / f"{f}.md").stat().st_mtime) if dev else None
                except OSError:
                    fases[f] = None
            if dev:
                pdir = _dev_dir(m, tid)
                stems = [p.stem for p in pdir.glob("*.jsonl")] if pdir is not None and pdir.is_dir() else []
            else:
                sid, cdir = m.get("session_id"), m.get("claude_dir")
                pdir = CLAUDE / cdir if _safe(cdir) else CLAUDE
                stems = [sid] if _safe(sid) and _safe(cdir) else []
            if status == "a correr":
                fin = _fim_a_correr(meta_path, pdir, stems, now)
                if fin is not None and fin < inicio:
                    continue
            principais, agentes = [], []
            for stem in sorted(stems):
                c = REPLAY.do_dia(pdir / f"{stem}.jsonl", inicio, claude_real)
                if c is not None:
                    principais.append(c)
            for meta in _replay_subagentes(pdir, sorted(stems), inicio):
                c = REPLAY.do_dia(meta.with_name(meta.name.replace(".meta.json", ".jsonl")), inicio, claude_real)
                if c is None:
                    continue
                try:
                    sm = _read_json(meta) if stat.S_ISREG(meta.lstat().st_mode) else None
                except OSError:
                    sm = None
                sm = sm if isinstance(sm, dict) else {}
                agentes.append((sm.get("agentType") if _safe(sm.get("agentType")) else "general",
                                bool(sm.get("finished")), c))
            out.append({"id": tid, "squad": squad,
                        "project": m.get("project") if isinstance(m.get("project"), str) else None,
                        "mode": m.get("mode") if isinstance(m.get("mode"), str) else "task", "status": status,
                        "ini": ini, "fin": fin, "fases": fases, "principais": principais, "agentes": agentes,
                        "lead": LEADS[squad]})
    out.sort(key=lambda tk: (tk["ini"], tk["squad"], tk["id"]))
    return out


def replay_dia(dia, now: float | None = None) -> dict | None:
    # GET /api/replay?dia=AAAA-MM-DD (ADR-015): só leitura; None = 404
    now = time.time() if now is None else now
    j = replay.janela(dia, now)
    if j is None:
        return None
    inicio, fim = j
    with REPLAY.lock:
        REPLAY.despejar(now)
        tarefas = _replay_tarefas(inicio, fim, now)
        maximo = replay.REPLAY_MAX

        def calcular() -> dict:
            out = {"v": 1, "dia": dia, "inicio": inicio, "fim": fim, "truncado": False, "quadros": []}
            out["quadros"], out["truncado"] = REPLAY.quadros(tarefas, inicio, fim, maximo - len(replay.compacto(out)))
            return out
        return REPLAY.resposta(replay.firma(dia, inicio, fim, tarefas, maximo), calcular)


def _state_cached() -> tuple[bytes, dict, dict]:
    # (corpo do /api/state, o mesmo dict, _aux desse build); refeito no máximo a cada CACHE_S
    with _state_lock:
        if time.time() - _cache["at"] > CACHE_S:
            gen = _cache["gen"]
            state = build_state()
            _cache["body"] = json.dumps(state, ensure_ascii=False).encode()
            _cache["state"], _cache["aux"] = state, _aux
            # um POST a meio do build: estes dados podem ser anteriores a ele, o próximo GET refaz
            _cache["at"] = time.time() if _cache["gen"] == gen else 0.0
        return _cache["body"], _cache["state"], _cache["aux"]


class Handler(SimpleHTTPRequestHandler):
    # com nosniff o tipo tem de ser certo; a imagem python:3.13-alpine não tem /etc/mime.types
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".woff2": "font/woff2"}

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def log_message(self, *_):
        pass

    def parse_request(self):
        # o único sítio da verificação do segredo do proxy (GET, HEAD e POST); só o /healthz do HEALTHCHECK passa sem ele
        if not super().parse_request():
            return False
        if self.command == "GET" and self.path.partition("?")[0] == "/healthz":
            return True
        segredo = _segredo_proxy()
        if segredo == "":
            return True
        if segredo is None:
            self.close_connection = True
            self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(PROXY_FALTA)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(PROXY_FALTA)
            return False
        recebido = self.headers.get("X-Office-Segredo", "").encode("latin-1", "replace")
        if hmac.compare_digest(recebido, segredo.encode()):
            return True
        self.close_connection = True
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def end_headers(self):
        self.send_header("Cache-Control", getattr(self, "_cc", "no-store"))   # biblioteca: ficheiros em cache
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def _json(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/biblioteca" or path.startswith("/api/biblioteca/"):
            biblioteca.handle(self, "GET", path, query, BIBLIOTECA, BIBLIOTECA_ARQ, ARQUIVO_FILA)
            return
        if path == "/api/state":
            self._json(200, _state_cached()[0])
            return
        if path == "/api/config":
            self._json(200, CONFIG_BODY)
            return
        if path == "/api/quadro":
            _, state, aux = _state_cached()
            now = time.time()
            out = quadro.resposta(state, aux, QUADRO, TASKS / "inbox", LOCAL_TZ, now, TOKEN)
            lancar.completar(out, LANCAR, QUADRO, quadro.chaves(), LOCAL_TZ, now)
            self._json(200, json.dumps(out, ensure_ascii=False).encode())
            return
        if path == "/api/replay":   # só leitura (ADR-015); os outros parâmetros ignoram-se
            out = replay_dia(parse_qs(query).get("dia", [None])[0])
            if out is None:
                self._json(404, json.dumps({"error": "não encontrado"}, ensure_ascii=False).encode())
                return
            body, gz = replay.compacto(out), "gzip" in self.headers.get("Accept-Encoding", "")
            if gz:
                body = gzip.compress(body, 6, mtime=0)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if gz:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/entregas":   # só leitura (ADR-014); a query é ignorada
            out = entregas.listar(TASKS, SQUADS_DIR, BIBLIOTECA, time.time())
            self._json(200, json.dumps(out, ensure_ascii=False).encode())
            return
        if path.startswith("/api/transcript/"):
            out = transcript(path[len("/api/transcript/"):], query)
            if out is None:
                self._json(404, json.dumps({"error": "não encontrado"}, ensure_ascii=False).encode())
            else:
                self._json(200, json.dumps(out, ensure_ascii=False).encode())
            return
        if path == "/api/aprovacao":
            qs = {k: v[0] for k, v in parse_qs(query).items()}
            code, out = aprovacoes.corpo(APROVACOES, qs.get("fonte"), qs.get("ficheiro"))
            self._json(code, json.dumps(out, ensure_ascii=False).encode())
            return
        if path == "/api/push/chave":
            ok, motivo = PUSH.estado()
            out = {"chave": PUSH.chave_publica()} if ok else {"error": push.ERRO_503, "motivo": motivo}
            self._json(200 if ok else 503, json.dumps(out, ensure_ascii=False).encode())
            return
        if path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        super().do_GET()

    def do_HEAD(self):
        path, _, query = self.path.partition("?")
        if path == "/api/biblioteca" or path.startswith("/api/biblioteca/"):
            biblioteca.handle(self, "HEAD", path, query, BIBLIOTECA, BIBLIOTECA_ARQ, ARQUIVO_FILA)
            return
        super().do_HEAD()

    def _err(self, code: int, msg: str) -> None:
        self._json(code, json.dumps({"error": msg}, ensure_ascii=False).encode())

    def _post_json(self, max_size: int) -> dict | None:
        # verificações partilhadas dos POST; None = já respondeu com o erro
        try:   # tamanho pelo Content-Length, antes de ler o corpo
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            size = -1
        if size < 0:
            self._err(400, "JSON inválido")
            return None
        if size > max_size:
            self._err(413, "corpo grande demais")
            return None
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._err(403, "Content-Type inválido")
            return None
        host, origin = self.headers.get("Host", "").strip().lower(), self.headers.get("Origin")
        if origin is not None:   # Origin só contra o Host (X-Forwarded-Host é forjável); "null" nunca casa
            bad = not host or urlsplit(origin.strip()).netloc.lower() != host
        else:
            bad = self.headers.get("Sec-Fetch-Site") != "same-origin"
        if bad:
            self._err(403, "origem não permitida")
            return None
        try:
            data = json.loads(self.rfile.read(size))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._err(400, "JSON inválido")
            return None
        tok = data.get("token")
        if not isinstance(tok, str) or not hmac.compare_digest(tok.encode("utf-8", "surrogatepass"), TOKEN.encode()):
            self._err(403, "token inválido, recarrega")
            return None
        return data

    def do_POST(self):
        path = self.path.partition("?")[0]
        if path == "/api/quadro":
            self._post_quadro()
            return
        if path == "/api/quadro/lancar":
            self._post_lancar()
            return
        if path in PUSH_ROTAS:
            self._post_push(path)
            return
        if path == "/api/biblioteca/arquivo":
            self._post_arquivo()
            return
        if path != "/api/decisao":
            self._err(404, "não encontrado")
            return
        data = self._post_json(POST_MAX)
        if data is None:
            return
        msg = aprovacoes.validar(APROVACOES, data)
        if msg:
            self._err(400, msg)
            return
        # o último valor é o que o Traefik acrescentou (os anteriores vêm do cliente e podem ser forjados)
        ip = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or self.client_address[0]
        code, out = aprovacoes.escrever(APROVACOES, FILA, data, ip, self.headers.get("User-Agent", ""),
                                        LOCAL_TZ, time.time())
        if code in (202, 409):   # o próximo /api/state já mostra «recebido» ou o pedido novo
            _cache["gen"] += 1
            _cache["at"] = 0.0
        self._json(code, json.dumps(out, ensure_ascii=False).encode())

    def _post_quadro(self):
        data = self._post_json(QUADRO_POST_MAX)
        if data is None:
            return
        projs = quadro.chaves()
        msg = quadro.validar(data, projs)
        if msg:
            self._err(400, msg)
            return
        code, out = quadro.escrever(QUADRO, data, projs, LOCAL_TZ, time.time())
        self._json(code, json.dumps(out, ensure_ascii=False).encode())

    def _post_lancar(self):
        # _post_json (400/413/403) → forma (400) → fila, versão, cartão, já lançado, fila cheia → pedido + mover (202)
        data = self._post_json(LANCAR_POST_MAX)
        if data is None:
            return
        msg = lancar.validar(data)
        if msg:
            self._err(400, msg)
            return
        code, out = lancar.escrever(LANCAR, QUADRO, data, quadro.chaves(), LOCAL_TZ, time.time())
        self._json(code, json.dumps(out, ensure_ascii=False).encode())

    def _post_arquivo(self):
        # _post_json (413/403/400) → forma, item e arquivável (400) → pedido na fila (202, 409, 429, 503)
        data = self._post_json(ARQUIVO_POST_MAX)
        if data is None:
            return
        ip = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or self.client_address[0]
        code, out = biblioteca.pedir_arquivo(ARQUIVO_FILA, BIBLIOTECA, BIBLIOTECA_ARQ, data, ip,
                                             self.headers.get("User-Agent", ""), LOCAL_TZ, time.time())
        self._json(code, json.dumps(out, ensure_ascii=False).encode())

    def _post_push(self, path: str):
        # _post_json (413/403/400) → 503 sem push → validação (400) → acção
        data = self._post_json(push.CORPO_MAX)
        if data is None:
            return
        ok, motivo = PUSH.estado()
        if not ok:
            code, out = 503, {"error": push.ERRO_503, "motivo": motivo}
        elif path.endswith("/subscrever"):
            code, out = PUSH.subscrever(data)
        elif path.endswith("/cancelar"):
            code, out = PUSH.cancelar(data)
        else:
            code, out = PUSH.teste(data, time.time())
        self._json(code, json.dumps(out, ensure_ascii=False).encode())


CONFIG, AVISOS = officecfg.carregar(os.environ.get("OFFICE_CONFIG", "/config/office.yaml"))
for _aviso in AVISOS:
    print(f"config: {_aviso} (a usar office.example.yaml)", file=sys.stderr, flush=True)
configurar(CONFIG, AVISOS[0] if AVISOS else None)


if __name__ == "__main__":
    if _segredo_proxy() is None:
        print("proxy: falta o segredo (OFFICE_PROXY_SEGREDO_FILE); 503 até correr o configurar", file=sys.stderr,
              flush=True)
    threading.Thread(target=_usage_loop, daemon=True).start()
    threading.Thread(target=_push_loop, daemon=True).start()   # sem push configurado só verifica a cada 30 s
    BIBLIOTECA.aquecer()   # 1.º índice da Biblioteca em fundo; até lá /f/ e /mini/ validam directamente no disco
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
