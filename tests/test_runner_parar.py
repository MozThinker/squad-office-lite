"""C5: `office_runner.py parar TASK_ID` marca a tarefa (`<workdir>/parar`), manda SIGTERM ao `correr` dela (só se o
pid for mesmo um `office_runner.py correr <id>`), o `correr` mata o grupo do `claude` e fecha a meta em «erro» /
«parada pelo utilizador»; uma tarefa marcada nunca é retomada nem lançada. Tudo em pastas temporárias."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner, officecfg

PARADA = "parada pelo utilizador"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(tmp_path / "argv.jsonl"))
    c.pedido(cfg)
    office_runner.uma_vez(cfg, str(path))
    return cfg, path, tmp_path, c.feito(cfg)["task_id"]


def meta_path(cfg, tid) -> Path:
    return officecfg.caminho(cfg, "tasks") / tid / "meta.json"


def ler(cfg, tid) -> dict:
    return json.loads(meta_path(cfg, tid).read_text(encoding="utf-8"))


def esperar(cond, segundos=20.0) -> bool:
    fim = time.time() + segundos
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.05)
    return False


def morto(pid: int) -> bool:
    # sem processo ou zombie à espera do pai
    try:
        estado = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return True
    return estado == "Z"


def apanha_sigterm(pid: int) -> bool:
    # o correr só mata o grupo do claude depois de instalar o handler (SigCgt, bit 15)
    try:
        linha = next(x for x in Path(f"/proc/{pid}/status").read_text().splitlines() if x.startswith("SigCgt:"))
    except (OSError, StopIteration):
        return False
    return bool(int(linha.split()[1], 16) & (1 << (signal.SIGTERM - 1)))


# ---------------------------------------------------------------- casos sem processo

@pytest.mark.parametrize("tid", ["../x", "nao-e-id", "20261106-1200-nao-existe", ""])
def test_id_invalido_ou_inexistente(ctx, tid):
    cfg, _, _, real = ctx
    antes = meta_path(cfg, real).read_bytes()
    assert lancador.parar(cfg, tid) == (2, f"tarefa desconhecida: {tid}")
    assert meta_path(cfg, real).read_bytes() == antes
    assert not list(officecfg.caminho(cfg, "tasks").glob("*/parar"))


def test_meta_com_outro_id_e_desconhecida(ctx):
    cfg, _, _, tid = ctx
    m = ler(cfg, tid)
    m["id"] = "20261106-1200-outra"
    lancador.escrever_json(meta_path(cfg, tid), m)
    assert lancador.parar(cfg, tid)[0] == 2
    assert not (meta_path(cfg, tid).parent / "parar").exists()


def test_tarefa_terminada_fica_igual(ctx):
    cfg, path, _, tid = ctx
    assert lancador.correr(cfg, tid, cfg_path=str(path))["status"] == "concluída"
    antes = meta_path(cfg, tid).read_bytes()
    assert lancador.parar(cfg, tid) == (0, "a tarefa já terminou (concluída)")
    assert meta_path(cfg, tid).read_bytes() == antes
    assert not (meta_path(cfg, tid).parent / "parar").exists()


def test_sem_correr_vivo_fecha_a_meta(ctx):
    cfg, _, _, tid = ctx
    assert ler(cfg, tid)["pid"] is None
    assert lancador.parar(cfg, tid) == (0, f"tarefa {tid} parada")
    m = ler(cfg, tid)
    assert m["status"] == "erro" and m["result"] == PARADA and m["pid"] is None and m["finished_at"]
    assert (meta_path(cfg, tid).parent / "parar").exists()


def test_pid_vivo_que_nao_e_o_correr_nao_e_morto(ctx):
    cfg, _, _, tid = ctx
    outro = subprocess.Popen(["sleep", "60"])
    try:
        m = ler(cfg, tid)
        m["pid"] = outro.pid
        lancador.escrever_json(meta_path(cfg, tid), m)
        assert not lancador._e_correr(outro.pid, tid)
        assert lancador.parar(cfg, tid) == (0, f"tarefa {tid} parada")
        assert outro.poll() is None   # continua vivo
        m = ler(cfg, tid)
        assert m["status"] == "erro" and m["result"] == PARADA and m["pid"] is None
    finally:
        outro.kill()
        outro.wait()


def test_e_correr(ctx):
    _, _, _, tid = ctx
    assert not lancador._e_correr(os.getpid(), tid)
    assert not lancador._e_correr(2 ** 22 + 1, tid)
    assert not lancador._e_correr(None, tid)


def test_correr_com_marca_nao_lanca(ctx):
    cfg, path, tmp, tid = ctx
    (meta_path(cfg, tid).parent / "parar").touch()
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "erro" and m["result"] == PARADA and m["pid"] is None
    assert not (tmp / "argv.jsonl").exists()   # o claude nem arranca


def test_retomas_nunca_retoma_uma_tarefa_marcada(ctx, monkeypatch):
    cfg, path, _, tid = ctx
    chamadas = []
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: chamadas.append(a) or 4242)
    m = ler(cfg, tid)
    m.update(session_id="sessao-1", pid=None)   # retomável, não fosse a marca
    lancador.escrever_json(meta_path(cfg, tid), m)
    (meta_path(cfg, tid).parent / "parar").touch()
    velho = time.time() - 600
    os.utime(meta_path(cfg, tid).parent / "heartbeat", (velho, velho))
    linhas = lancador.retomas(cfg, str(path))
    assert chamadas == [] and linhas == [f"tarefa {tid} parada pelo utilizador: fechada em erro"]
    m = ler(cfg, tid)
    assert m["status"] == "erro" and m["result"] == PARADA and m["auto_resumes"] == 0
    assert lancador.retomas(cfg, str(path)) == []


# ---------------------------------------------------------------- correr real em subprocesso

def test_parar_um_correr_vivo_mata_o_grupo_do_claude(ctx):
    cfg, path, tmp, tid = ctx
    pidfile = tmp / "sleep.pid"
    lento = tmp / "bin-lento"
    lento.mkdir()
    (lento / "claude").write_text(f"#!/bin/sh\ncat >/dev/null\nsleep 60 &\necho $! > '{pidfile}'\nwait\n",
                                  encoding="utf-8")
    (lento / "claude").chmod(0o755)
    env = dict(os.environ, PATH=os.pathsep.join([str(lento), os.environ["PATH"]]))
    proc = subprocess.Popen([sys.executable, str(c.RUNNER), "correr", tid, "--config", str(path)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert esperar(lambda: pidfile.exists() and pidfile.read_text().strip()), "o claude lento não arrancou"
        assert esperar(lambda: apanha_sigterm(proc.pid))
        sleep_pid = int(pidfile.read_text())
        assert ler(cfg, tid)["pid"] == proc.pid and lancador._e_correr(proc.pid, tid)
        assert lancador.parar(cfg, tid) == (0, f"tarefa {tid} parada")
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0 and f"tarefa {tid}: erro" in out, err
    m = ler(cfg, tid)
    assert m["status"] == "erro" and m["result"] == PARADA and m["pid"] is None and m["pr"] is None
    assert esperar(lambda: morto(sleep_pid)), "o filho do claude sobreviveu"
    assert lancador.retomas(cfg, str(path)) == []   # marcada e já fechada: nada a retomar


# ---------------------------------------------------------------- CLI

def _cli(*args, env=None):
    return subprocess.run([sys.executable, str(c.RUNNER), *args], capture_output=True, text=True, timeout=60,
                          env=env or dict(os.environ), check=False)


def test_help_mostra_parar():
    r = _cli("--help")
    assert r.returncode == 0 and "parar" in r.stdout
    assert "parar TASK_ID" in office_runner.__doc__
    r = _cli("parar", "--help")
    assert r.returncode == 0 and "task_id" in r.stdout and "--config" in r.stdout


def test_cli_parar(ctx):
    _, path, _, tid = ctx
    r = _cli("parar", "20261106-1200-nao-existe", "--config", str(path))
    assert r.returncode == 2 and r.stderr.strip() == "tarefa desconhecida: 20261106-1200-nao-existe" and not r.stdout
    r = _cli("parar", tid, "--config", str(path))
    assert r.returncode == 0 and r.stdout.strip() == f"tarefa {tid} parada"
    r = _cli("parar", tid, "--config", str(path))
    assert r.returncode == 0 and r.stdout.strip() == "a tarefa já terminou (erro)"
    r = _cli("parar", tid, env=dict(os.environ, OFFICE_CONFIG=str(path)))   # config pelo ambiente
    assert r.returncode == 0 and "já terminou" in r.stdout


# ---------------------------------------------------------------- SIGTERM: parar (com marca) vs. shutdown (sem marca)

def _claude_lento(tmp: Path) -> tuple[dict, Path]:
    pidfile = tmp / "sleep.pid"
    lento = tmp / "bin-lento"
    lento.mkdir()
    (lento / "claude").write_text(f"#!/bin/sh\ncat >/dev/null\nsleep 60 &\necho $! > '{pidfile}'\nwait\n",
                                  encoding="utf-8")
    (lento / "claude").chmod(0o755)
    return dict(os.environ, PATH=os.pathsep.join([str(lento), os.environ["PATH"]])), pidfile


def test_sigterm_sem_marca_deixa_a_correr_para_a_retoma(ctx, monkeypatch):
    # shutdown/reboot/logout: o grupo do claude morre, a meta fica «a correr» e o retomas() retoma-a
    cfg, path, tmp, tid = ctx
    env, pidfile = _claude_lento(tmp)
    proc = subprocess.Popen([sys.executable, str(c.RUNNER), "correr", tid, "--config", str(path)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert esperar(lambda: pidfile.exists() and pidfile.read_text().strip()), "o claude lento não arrancou"
        assert esperar(lambda: apanha_sigterm(proc.pid))
        sleep_pid = int(pidfile.read_text())
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert proc.returncode == 0 and f"tarefa {tid}: a correr" in out, err
    assert esperar(lambda: morto(sleep_pid)), "o filho do claude sobreviveu"
    m = ler(cfg, tid)
    assert m["status"] == "a correr" and m["finished_at"] is None and m["result"] is None
    assert not (meta_path(cfg, tid).parent / "parar").exists()
    chamadas = []
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: chamadas.append(a) or 4242)
    m["session_id"] = "sessao-1"   # o claude lento não escreve init
    lancador.escrever_json(meta_path(cfg, tid), m)
    velho = time.time() - 600
    os.utime(meta_path(cfg, tid).parent / "heartbeat", (velho, velho))
    assert lancador.retomas(cfg, str(path))[0] == f"tarefa {tid} retomada (1/2)"
    assert chamadas and chamadas[0][2] == tid


def test_sigterm_sem_marca_em_processo(ctx, monkeypatch):
    # o pedido chega antes do Popen (aqui, ao gravar as settings): o claude é morto logo e a meta não fecha
    cfg, path, _, tid = ctx
    gravar = lancador.gravar_settings

    def _com_sigterm(*a):
        os.kill(os.getpid(), signal.SIGTERM)
        return gravar(*a)

    monkeypatch.setattr(lancador, "gravar_settings", _com_sigterm)
    antes = signal.getsignal(signal.SIGTERM)
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert signal.getsignal(signal.SIGTERM) is antes   # o handler é reposto
    assert m["status"] == "a correr" and ler(cfg, tid)["status"] == "a correr"
    assert ler(cfg, tid)["finished_at"] is None


def test_parar_antes_do_popen_mata_logo(ctx, monkeypatch):
    # a marca aparece depois da verificação inicial: o correr volta a vê-la logo a seguir ao Popen
    cfg, path, _, tid = ctx
    gravar = lancador.gravar_settings

    def _com_marca(*a):
        (meta_path(cfg, tid).parent / "parar").touch()
        return gravar(*a)

    monkeypatch.setattr(lancador, "gravar_settings", _com_marca)
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "erro" and m["result"] == PARADA and m["pid"] is None and m["pr"] is None
    assert ler(cfg, tid) == m


@pytest.mark.parametrize("marca", [True, False])
def test_sigterm_durante_a_entrega(ctx, monkeypatch, marca):
    # com marca: parada, mas o PR verificado fica; sem marca: o claude já tinha terminado com sucesso, a meta fecha
    # com o resultado (retomar seria gastar outra vez)
    cfg, path, _, tid = ctx

    def _entrega(meta):
        if marca:
            (meta_path(cfg, tid).parent / "parar").touch()
        os.kill(os.getpid(), signal.SIGTERM)
        return "parcial (PR draft)", "https://github.com/o/r/pull/7 (open, draft)", "https://github.com/o/r/pull/7", []

    monkeypatch.setattr(lancador, "entrega", _entrega)
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    if marca:
        assert m["status"] == "erro" and m["result"] == PARADA and m["pid"] is None
        assert m["pr"] == "https://github.com/o/r/pull/7 (open, draft)" and m["pr_url"] == "https://github.com/o/r/pull/7"
        assert ler(cfg, tid) == m
    else:
        assert m["status"] == "parcial (PR draft)" and m["result"] == "ok" and m["pid"] is None and m["finished_at"]
        assert m["pr_url"] == "https://github.com/o/r/pull/7" and ler(cfg, tid) == m
        assert lancador.retomas(cfg, str(path)) == []


def test_sigterm_sem_marca_guarda_o_session_id(ctx, monkeypatch):
    # o _pulsar ainda não o viu (1.º minuto): o init está no log e o ramo de sinal grava-o, sem fechar a meta
    cfg, path, tmp, tid = ctx
    monkeypatch.setattr(lancador, "_pulsar", lambda *a: None)
    lento = tmp / "bin-init"
    lento.mkdir()
    init = '{"type":"system","subtype":"init","session_id":"sessao-do-log"}'
    (lento / "claude").write_text(f"#!/bin/sh\ncat >/dev/null\necho '{init}'\nkill -TERM $PPID\nsleep 60\n",
                                  encoding="utf-8")
    (lento / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(lento), os.environ["PATH"]]))
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    disco = ler(cfg, tid)
    assert m == disco and disco["session_id"] == "sessao-do-log"
    assert disco["status"] == "a correr" and disco["pid"] == os.getpid() and disco["finished_at"] is None
    assert (officecfg.caminho(cfg, "runner") / "settings" / f"{tid}.json").exists()
    chamadas = []
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: chamadas.append(a) or 4242)
    velho = time.time() - 600
    os.utime(meta_path(cfg, tid).parent / "heartbeat", (velho, velho))
    assert lancador.retomas(cfg, str(path))[0] == f"tarefa {tid} retomada (1/2)"   # sem injectar o session_id
    assert chamadas and chamadas[0][2] == tid


def test_parar_recusa_marca_em_symlink(ctx):
    cfg, _, tmp, tid = ctx
    alvo = tmp / "alvo"
    marca = meta_path(cfg, tid).parent / "parar"
    marca.symlink_to(alvo)
    codigo, msg = lancador.parar(cfg, tid)
    assert codigo == 1 and "symlink" in msg and "apaga" in msg
    assert not alvo.exists() and marca.is_symlink()   # o alvo não é criado
    assert ler(cfg, tid)["status"] == "a correr"
    marca.unlink()
    existente = tmp / "existente"
    existente.write_text("x", encoding="utf-8")
    antes = existente.stat().st_mtime_ns
    os.utime(existente, ns=(antes - 10**9, antes - 10**9))
    marca.symlink_to(existente)
    assert lancador.parar(cfg, tid)[0] == 1
    assert existente.stat().st_mtime_ns == antes - 10**9 and existente.read_text(encoding="utf-8") == "x"


def test_parar_cria_a_marca_0600(ctx):
    cfg, _, _, tid = ctx
    assert lancador.parar(cfg, tid) == (0, f"tarefa {tid} parada")
    marca = meta_path(cfg, tid).parent / "parar"
    assert marca.is_file() and not marca.is_symlink() and (marca.stat().st_mode & 0o777) == 0o600


def test_parar_cria_a_marca_antes_de_ler_a_meta(ctx, monkeypatch):
    cfg, _, _, tid = ctx
    ler_meta, vista = lancador.ler_meta, []
    monkeypatch.setattr(lancador, "ler_meta", lambda p: vista.append((p.parent / "parar").exists()) or ler_meta(p))
    assert lancador.parar(cfg, tid) == (0, f"tarefa {tid} parada")
    assert vista == [True]


def test_parar_com_o_correr_a_morrer_entretanto(ctx, monkeypatch):
    # _correr_vivo diz que sim e o processo sai antes do kill: sem traceback; o retomas() fecha-a como parada
    cfg, path, _, tid = ctx
    monkeypatch.setattr(lancador, "_correr_vivo", lambda pid, t: True)

    def _kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(lancador.os, "kill", _kill)
    assert lancador.parar(cfg, tid) == (0, f"tarefa {tid} parada")
    monkeypatch.undo()
    assert ler(cfg, tid)["status"] == "a correr" and (meta_path(cfg, tid).parent / "parar").exists()
    velho = time.time() - 600
    os.utime(meta_path(cfg, tid).parent / "heartbeat", (velho, velho))
    assert lancador.retomas(cfg, str(path)) == [f"tarefa {tid} parada pelo utilizador: fechada em erro"]


# ---------------------------------------------------------------- parar com o office.yaml inválido

def test_cli_parar_com_config_invalida_usa_raizes_dados(ctx):
    cfg, path, _, tid = ctx
    path.write_text(path.read_text(encoding="utf-8").replace("tecto_diario: 10", "tecto_diario: 999"),
                    encoding="utf-8")
    assert _cli("--uma-vez", "--config", str(path)).returncode == 1   # o resto continua estrito
    r = _cli("parar", tid, "--config", str(path))
    assert r.returncode == 0 and r.stdout.strip() == f"tarefa {tid} parada", r.stderr
    assert "config inválida" in r.stderr and "raizes.dados" in r.stderr
    m = ler(cfg, tid)
    assert m["status"] == "erro" and m["result"] == PARADA and m["finished_at"].endswith("+00:00")


@pytest.mark.parametrize("estrago", ["  dados: relativo", "\tdados: x"])
def test_cli_parar_sem_raizes_dados_legivel_falha_com_erro_claro(ctx, estrago):
    cfg, path, _, tid = ctx
    texto = path.read_text(encoding="utf-8")
    linha = next(x for x in texto.splitlines() if x.startswith("  dados: "))
    path.write_text(texto.replace(linha, estrago), encoding="utf-8")
    r = _cli("parar", tid, "--config", str(path))
    assert r.returncode == 1 and not r.stdout
    assert r.stderr.startswith("erro: config inválida") and "raizes.dados" in r.stderr
    assert ler(cfg, tid)["status"] == "a correr"
