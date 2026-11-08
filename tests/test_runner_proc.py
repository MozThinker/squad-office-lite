"""O runner não é «dumpable» (prctl(PR_SET_DUMPABLE, 0), Linux): o agente não lê o ambiente do `correr` (runner.env:
Telegram, GH_TOKEN) em /proc/<pid>/environ; o `claude` filho continua a arrancar (o execve repõe-no dumpable, com o
ambiente da lista branca). Noutros sistemas, ou como root (CAP_SYS_PTRACE), estes testes não se aplicam."""
import json
import os
import subprocess
import sys

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner, officecfg

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux") or os.geteuid() == 0,
                                reason="só Linux e sem root (root lê /proc/<pid>/environ de qualquer processo)")

FILHO = ("import os\n"
         "try:\n"
         "    open(f'/proc/{os.getppid()}/environ', 'rb').read()\n"
         "    print('lido')\n"
         "except PermissionError:\n"
         "    print('recusado')\n")


def _pai(com_prctl: bool) -> str:
    codigo = (f"import subprocess, sys\nsys.path.insert(0, {str(c.RAIZ / 'runner')!r})\n"
              f"sys.path.insert(1, {str(c.RAIZ)!r})\nimport office_runner\n"
              + ("office_runner.nao_despejavel()\n" if com_prctl else "")
              + f"subprocess.run([sys.executable, '-c', {FILHO!r}], check=True)\n")
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_filho_nao_le_o_environ_do_pai():
    assert _pai(com_prctl=False) == "lido"   # controlo: sem o prctl, lê-se
    assert _pai(com_prctl=True) == "recusado"


def test_main_torna_o_processo_nao_dumpable(monkeypatch):
    chamadas = []
    monkeypatch.setattr(office_runner, "nao_despejavel", lambda: chamadas.append(1))
    assert office_runner.main([]) == 2
    assert chamadas == [1]


def test_correr_real_o_claude_arranca_e_nao_le_o_runner_env(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    c.pedido(cfg)
    office_runner.uma_vez(cfg, str(path))
    tid = c.feito(cfg)["task_id"]
    saida = tmp_path / "visto.txt"
    pasta = tmp_path / "bin-espiao"
    pasta.mkdir()
    espiao = pasta / "claude"
    # tenta ler o ambiente do pai (o correr) e depois corre o claude falso
    espiao.write_text(f"#!{sys.executable}\nimport os, sys\n"
                      f"try:\n    d = open(f'/proc/{{os.getppid()}}/environ', 'rb').read()\n"
                      f"    r = 'GH_TOKEN' if b'segredo-gh' in d else 'lido'\n"
                      f"except PermissionError:\n    r = 'recusado'\n"
                      f"open({str(saida)!r}, 'w').write(r)\n"
                      f"os.execv({sys.executable!r}, [{sys.executable!r}, {str(c.BIN / 'claude')!r}, *sys.argv[1:]])\n",
                      encoding="utf-8")
    espiao.chmod(0o755)
    env = dict(os.environ, PATH=os.pathsep.join([str(pasta), os.environ["PATH"]]), GH_TOKEN="segredo-gh")
    r = subprocess.run([sys.executable, str(c.RUNNER), "correr", tid, "--config", str(path)], env=env,
                       capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0 and f"tarefa {tid}: concluída" in r.stdout, r.stderr
    assert saida.read_text() == "recusado"
    meta = json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "concluída" and meta["result"] == "ok"
