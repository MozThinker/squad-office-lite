"""Segredos do runner.env (Telegram, credencial do claude, GH_TOKEN/GITHUB_TOKEN): saem do ambiente no arranque do
runner (guardar_segredos) e nenhum filho os herda. O git recebe só a lista branca; o gh também o GH_TOKEN; o `correr`
destacado recebe-os por um pipe no stdin (uma linha JSON), nunca no ambiente. Tudo em pastas temporárias, com um
servidor de Telegram falso em 127.0.0.1."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner, officecfg

OAUTH = "sk-ant-oat01-SEGREDOoauth_9876543210-xyz"
TG = "123:SEGREDOtelegram"
GH = "ghp_SEGREDOgh"


@pytest.fixture
def telegram():
    recebidos = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            corpo = self.rfile.read(int(self.headers["Content-Length"]))
            recebidos.append((self.path, json.loads(corpo)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", recebidos
    srv.shutdown()
    srv.server_close()


def espiao(tmp, nome: str, real: str | None, saida: str = "") -> None:
    """`tmp/bin/<nome>` grava {variável: valor} do seu ambiente em tmp/<nome>.env.json e corre o `real` (ou imprime
    `saida`)."""
    pasta = tmp / "bin"
    pasta.mkdir(exist_ok=True)
    corpo = (f"#!{sys.executable}\nimport json, os, sys\n"
             f"open({str(tmp / (nome + '.env.json'))!r}, 'w').write(json.dumps(dict(os.environ)))\n")
    corpo += (f"os.execv({real!r}, [{real!r}, *sys.argv[1:]])\n" if real else f"print({saida!r})\n")
    (pasta / nome).write_text(corpo, encoding="utf-8")
    (pasta / nome).chmod(0o755)


def visto(tmp, nome: str) -> dict:
    return json.loads((tmp / f"{nome}.env.json").read_text(encoding="utf-8"))


def com_segredos(monkeypatch, url: str = "http://127.0.0.1:1") -> None:
    # como o systemd os põe (EnvironmentFile=runner.env), e depois o arranque do runner
    for k, v in {"OFFICE_TELEGRAM_TOKEN": TG, "OFFICE_TELEGRAM_CHAT": "42", "OFFICE_TELEGRAM_API": url,
                 "CLAUDE_CODE_OAUTH_TOKEN": OAUTH, "GH_TOKEN": GH, "GITHUB_TOKEN": GH + "2"}.items():
        monkeypatch.setenv(k, v)
    lancador.guardar_segredos()


def test_guardar_segredos_tira_do_ambiente_e_o_telegram_continua(tmp_path, monkeypatch, telegram):
    url, recebidos = telegram
    c.ambiente(monkeypatch, tmp_path)
    com_segredos(monkeypatch, url)
    assert not set(lancador.SEGREDO_CHAVES) & set(os.environ)
    assert lancador.SEGREDOS["OFFICE_TELEGRAM_TOKEN"] == TG and lancador.SEGREDOS["GH_TOKEN"] == GH
    lancador.avisar("olá")
    assert recebidos == [(f"/bot{TG}/sendMessage", {"chat_id": "42", "text": "olá"})]
    env = lancador.env_claude(None)   # a credencial do runner.env continua a chegar ao claude, só a ele
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == OAUTH and "GH_TOKEN" not in env and "OFFICE_TELEGRAM_TOKEN" not in env
    assert lancador.aviso_credenciais(str(tmp_path / "office.yaml")) is None


def test_main_tira_os_segredos_do_ambiente(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    _, path = c.config(tmp_path)
    for k in ("OFFICE_TELEGRAM_TOKEN", "GH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "x")
    assert office_runner.main(["--uma-vez", "--config", str(path)]) == 0
    assert not {"OFFICE_TELEGRAM_TOKEN", "GH_TOKEN", "ANTHROPIC_API_KEY"} & set(os.environ)
    assert lancador.SEGREDOS == {"OFFICE_TELEGRAM_TOKEN": "x", "GH_TOKEN": "x", "ANTHROPIC_API_KEY": "x"}


def test_git_e_gh_so_recebem_a_lista_branca(tmp_path, monkeypatch):
    espiao(tmp_path, "git", shutil.which("git"))
    espiao(tmp_path, "gh", None, saida="[]")
    passa = lancador.ENV_PASSA
    c.ambiente(monkeypatch, tmp_path, extra_bin=tmp_path / "bin")
    monkeypatch.setattr(lancador, "ENV_PASSA", passa)   # sem as FAKE_* dos testes
    monkeypatch.setenv("QUALQUER", "1")
    com_segredos(monkeypatch)
    monkeypatch.setenv("OFFICE_TELEGRAM_TOKEN", TG)   # mesmo que ainda estivessem no ambiente do processo
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SEGREDOapi")
    cfg, _ = c.config(tmp_path)
    repo = c.repo(cfg)
    assert lancador.git(["rev-parse", "--git-dir"], repo).returncode == 0
    g = visto(tmp_path, "git")
    assert set(g) <= set(passa) | {"GIT_TERMINAL_PROMPT"}, set(g)
    assert g["GIT_TERMINAL_PROMPT"] == "0" and g["HOME"] == str(tmp_path / "casa") and "PATH" in g
    assert lancador._pr({"branch": "feat/x", "worktree": str(repo)}) is None   # gh falso: lista vazia
    h = visto(tmp_path, "gh")
    assert h["GH_TOKEN"] == GH and h["GITHUB_TOKEN"] == GH + "2"
    assert set(h) - {"GH_TOKEN", "GITHUB_TOKEN"} <= set(passa)
    for env in (g, h):
        assert not {"OFFICE_TELEGRAM_TOKEN", "OFFICE_TELEGRAM_CHAT", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                    "QUALQUER"} & set(env)
        assert TG not in json.dumps(env) and OAUTH not in json.dumps(env)


def test_git_encontra_a_identidade_pelo_home(tmp_path, monkeypatch):
    # sem GIT_AUTHOR_*/GIT_CONFIG_GLOBAL (não passam): a identidade vem do ~/.gitconfig, pelo HOME
    c.ambiente(monkeypatch, tmp_path)
    for k in list(c.GIT_ENV) + ["GIT_CONFIG_GLOBAL"]:
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "casa" / ".gitconfig").write_text("[user]\n\tname = Casa\n\temail = casa@example.com\n",
                                                  encoding="utf-8")
    r = lancador.git(["config", "user.email"], tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == "casa@example.com"


def _destacar_real(tmp_path, monkeypatch):
    # tarefa preparada sem lançar; a seguir o destacar é o verdadeiro
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    real = lancador.destacar
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    c.pedido(cfg)
    office_runner.uma_vez(cfg, str(path))
    monkeypatch.setattr(lancador, "destacar", real)
    falso = c.claude_com(tmp_path, "cred", FAKE_CLAUDE_CRED=str(tmp_path / "cred.jsonl"), FAKE_CLAUDE_EXIGE_CRED="1")
    monkeypatch.setenv("PATH", os.pathsep.join([str(falso), os.environ["PATH"]]))
    return cfg, path, c.feito(cfg)["task_id"]


def _esperar_fim(cfg, tid, recebidos, segundos=30.0) -> dict:
    meta = officecfg.caminho(cfg, "tasks") / tid / "meta.json"
    fim = time.time() + segundos
    while time.time() < fim:
        m = lancador.ler_meta(meta) or {}
        if m.get("status") not in (None, "a correr") and recebidos:
            return m
        time.sleep(0.05)
    raise AssertionError(f"o correr destacado não terminou: {lancador.ler_meta(meta)}")


def test_correr_destacado_recebe_os_segredos_pelo_stdin(tmp_path, monkeypatch, telegram):
    url, recebidos = telegram
    cfg, path, tid = _destacar_real(tmp_path, monkeypatch)
    com_segredos(monkeypatch, url)
    monkeypatch.setenv("OFFICE_TELEGRAM_TOKEN", TG)   # ainda no ambiente do processo: o destacar não o passa
    chamadas, popen = [], subprocess.Popen

    def _popen(cmd, **kw):
        chamadas.append((cmd, kw))
        return popen(cmd, **kw)

    monkeypatch.setattr(lancador.subprocess, "Popen", _popen)
    pid = lancador.destacar(cfg, str(path), tid)
    ((cmd, kw),) = chamadas
    assert kw["stdin"] == subprocess.PIPE and kw["start_new_session"]
    assert not set(lancador.SEGREDO_CHAVES) & set(kw["env"])   # o correr não os tem no ambiente
    assert not any(TG in a or OAUTH in a or GH in a for a in cmd)
    m = _esperar_fim(cfg, tid, recebidos)
    assert m["status"] == "concluída", m
    sha = hashlib.sha256(OAUTH.encode()).hexdigest()
    assert [json.loads(x) for x in (tmp_path / "cred.jsonl").read_text(encoding="utf-8").splitlines()] == [
        {"CLAUDE_CODE_OAUTH_TOKEN": sha}]
    assert recebidos[-1][0] == f"/bot{TG}/sendMessage" and tid in recebidos[-1][1]["text"]
    log = (officecfg.caminho(cfg, "runner") / "logs" / f"{tid}.log").read_text(encoding="utf-8")
    assert TG not in log and OAUTH not in log and "aviso" not in log
    assert pid > 0


def test_correr_a_mao_sem_pipe_usa_o_seu_ambiente(tmp_path, monkeypatch, telegram):
    url, recebidos = telegram
    _, path, tid = _destacar_real(tmp_path, monkeypatch)
    env = dict(os.environ, OFFICE_TELEGRAM_TOKEN=TG, OFFICE_TELEGRAM_CHAT="42", OFFICE_TELEGRAM_API=url,
               CLAUDE_CODE_OAUTH_TOKEN=OAUTH)
    r = subprocess.run([sys.executable, str(c.RUNNER), "correr", tid, "--config", str(path)], env=env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0 and "concluída" in r.stdout, r.stderr
    assert len((tmp_path / "cred.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert [p for p, _ in recebidos] == [f"/bot{TG}/sendMessage"]


@pytest.mark.parametrize("linha", [b"nao e json\n", b"[1, 2]\n"])
def test_stdin_ilegivel_e_ignorado(tmp_path, monkeypatch, telegram, linha):
    _, recebidos = telegram
    _, path, tid = _destacar_real(tmp_path, monkeypatch)
    r = subprocess.run([sys.executable, str(c.RUNNER), "correr", tid, "--config", str(path)], input=linha,
                       capture_output=True, timeout=60, check=False)
    assert r.returncode == 0 and b"tarefa " in r.stdout
    assert b"segredos no stdin ileg" in r.stderr and linha.strip() not in r.stderr
    assert recebidos == []


def test_ler_segredos_stdin_so_aceita_as_chaves_conhecidas(monkeypatch, tmp_path):
    monkeypatch.setattr(lancador, "SEGREDOS", {})
    monkeypatch.setattr(lancador, "_fechar_stdin", lambda: None)   # não mexe no fd 0 do pytest
    f = tmp_path / "in"
    f.write_bytes(json.dumps({"OFFICE_TELEGRAM_TOKEN": TG, "PATH": "/mau", "GH_TOKEN": 1}).encode() + b"\nresto\n")
    with open(f, encoding="utf-8") as entrada:
        monkeypatch.setattr(sys, "stdin", entrada)
        lancador.ler_segredos_stdin()
        assert entrada.buffer.read() == b"resto\n"   # só a primeira linha
    assert lancador.SEGREDOS == {"OFFICE_TELEGRAM_TOKEN": TG}
    assert os.environ.get("PATH") != "/mau"
