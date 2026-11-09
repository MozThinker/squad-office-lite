"""AC-r3: `office_runner.py ciclo [--intervalo S] [--passagens N]` — passagens repetidas, config relida em cada volta,
excepção numa passagem não pára o ciclo, filhos terminados recolhidos (sem zombies), SIGTERM/SIGINT acabam o ciclo
no fim da passagem em curso, runner.env da pasta dos segredos carregado no arranque."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner, officecfg

GH_FALSO = "gho_" + "cicloteste" * 2


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    monkeypatch.delenv("OFFICE_SEGREDOS_DIR", raising=False)
    monkeypatch.delenv("OFFICE_MODO", raising=False)
    cfg, path = c.config(tmp_path)
    return cfg, path


def espiao(monkeypatch, efeito=None):
    """Troca o uma_vez por um que regista a marca da config lida e corre `efeito(n)` (n = 1, 2, …)."""
    chamadas = []

    def falso(cfg, cfg_path, now=None):
        chamadas.append(cfg["marca"])
        if efeito:
            return efeito(len(chamadas)) or []
        return []

    monkeypatch.setattr(office_runner, "uma_vez", falso)
    return chamadas


def test_passagens_e_config_relida_em_cada_volta(ctx, monkeypatch):
    _, path = ctx

    def efeito(n):
        if n == 1:   # muda a config entre passagens: a 2.ª tem de a ver
            path.write_text(path.read_text(encoding="utf-8").replace("marca: Squad Office", "marca: Outra"),
                            encoding="utf-8")
        return [f"linha {n}"]

    chamadas = espiao(monkeypatch, efeito)
    assert office_runner.ciclo(str(path), intervalo=0, passagens=2) == 0
    assert chamadas == ["Squad Office", "Outra"]


def test_imprime_as_linhas_da_passagem(ctx, monkeypatch, capsys):
    _, path = ctx
    espiao(monkeypatch, lambda n: [f"pedido {n} tratado"])
    office_runner.ciclo(str(path), intervalo=0, passagens=2)
    assert capsys.readouterr().out.splitlines() == ["pedido 1 tratado", "pedido 2 tratado"]


def test_excepcao_numa_passagem_nao_para_o_ciclo(ctx, monkeypatch, capsys):
    _, path = ctx

    def efeito(n):
        if n == 1:
            raise RuntimeError("disco cheio")
        return ["segunda ok"]

    chamadas = espiao(monkeypatch, efeito)
    assert office_runner.ciclo(str(path), intervalo=0, passagens=2) == 0
    assert len(chamadas) == 2
    saida = capsys.readouterr()
    assert "erro na passagem: RuntimeError: disco cheio" in saida.err
    assert "Traceback" in saida.err and "segunda ok" in saida.out


@pytest.mark.parametrize("modo", ["docker", None])
def test_sem_permissao_aponta_para_o_configurar_no_modo_docker(ctx, monkeypatch, capsys, modo):
    _, path = ctx
    if modo:
        monkeypatch.setenv("OFFICE_MODO", modo)
    else:
        monkeypatch.delenv("OFFICE_MODO", raising=False)

    def efeito(n):
        raise PermissionError(13, "Permission denied", "/dados/runner/.lock")

    espiao(monkeypatch, efeito)
    assert office_runner.ciclo(str(path), intervalo=0, passagens=1) == 0
    err = capsys.readouterr().err
    assert "erro na passagem: PermissionError" in err
    assert ("docker compose run --rm configurar" in err) == (modo == "docker")


def test_config_invalida_avisa_e_segue(ctx, monkeypatch, capsys):
    _, path = ctx
    bom = path.read_text(encoding="utf-8")
    path.write_text("versao: 2\n", encoding="utf-8")

    def efeito(n):
        return [f"passagem {n}"]

    chamadas = espiao(monkeypatch, efeito)
    original = office_runner.passagem

    def passagem(cfg_path):
        original(cfg_path)
        path.write_text(bom, encoding="utf-8")   # corrigida depois da 1.ª volta

    monkeypatch.setattr(office_runner, "passagem", passagem)
    assert office_runner.ciclo(str(path), intervalo=0, passagens=2) == 0
    assert chamadas == ["Squad Office"]   # a 1.ª volta não chegou ao uma_vez
    assert "erro: config inválida" in capsys.readouterr().err


@pytest.mark.parametrize("sinal", [signal.SIGTERM, signal.SIGINT])
def test_sinal_acaba_no_fim_da_passagem(ctx, monkeypatch, sinal):
    _, path = ctx
    fim = []

    def efeito(n):
        os.kill(os.getpid(), sinal)   # chega a meio da passagem: esta acaba
        fim.append(n)
        return []

    chamadas = espiao(monkeypatch, efeito)
    antes = signal.getsignal(sinal)
    t0 = time.monotonic()
    assert office_runner.ciclo(str(path), intervalo=60) == 0   # sem --passagens: só o sinal o acaba
    assert chamadas == ["Squad Office"] and fim == [1]
    assert time.monotonic() - t0 < 5   # não esperou o intervalo
    assert signal.getsignal(sinal) == antes   # handlers repostos


def test_sinal_durante_a_espera(ctx, monkeypatch):
    _, path = ctx
    chamadas = espiao(monkeypatch)
    monkeypatch.setattr(office_runner.time, "sleep", lambda s: os.kill(os.getpid(), signal.SIGTERM))
    assert office_runner.ciclo(str(path), intervalo=60) == 0
    assert len(chamadas) == 1


def test_dorme_ate_ao_intervalo_em_fatias(ctx, monkeypatch):
    _, path = ctx
    espiao(monkeypatch)
    relogio = [0.0]
    sonos = []
    monkeypatch.setattr(office_runner.time, "monotonic", lambda: relogio[0])

    def dormir(s):
        sonos.append(s)
        relogio[0] += s

    monkeypatch.setattr(office_runner.time, "sleep", dormir)
    office_runner.ciclo(str(path), intervalo=2.5, passagens=2)
    assert sonos == [1.0, 1.0, 0.5]   # só entre passagens, nunca mais de 1 s de cada vez


def test_recolhe_filhos_terminados(ctx, monkeypatch):
    pids = [os.posix_spawn(sys.executable, [sys.executable, "-c", "pass"], dict(os.environ)) for _ in range(2)]
    for pid in pids:   # espera que sejam zombies (terminados, por recolher)
        for _ in range(200):
            try:
                estado = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except OSError:
                break
            if estado == "Z":
                break
            time.sleep(0.02)
    assert office_runner.recolher() >= 2
    assert not any(Path(f"/proc/{p}").exists() for p in pids)
    assert office_runner.recolher() == 0   # sem filhos: ChildProcessError → 0


def test_ciclo_recolhe_depois_de_cada_passagem(ctx, monkeypatch):
    _, path = ctx
    espiao(monkeypatch)
    vezes = []
    monkeypatch.setattr(office_runner, "recolher", lambda: vezes.append(1) or 0)
    office_runner.ciclo(str(path), intervalo=0, passagens=3)
    assert len(vezes) == 3


def test_runner_env_da_pasta_dos_segredos(ctx, monkeypatch, tmp_path):
    _, path = ctx
    seg = tmp_path / "segredos"
    seg.mkdir(mode=0o700)
    f = seg / "runner.env"
    f.write_text(f"GH_TOKEN={GH_FALSO}\nOUTRA=x\n", encoding="utf-8")
    f.chmod(0o600)
    monkeypatch.setenv("OFFICE_SEGREDOS_DIR", str(seg))
    espiao(monkeypatch)
    office_runner.ciclo(str(path), intervalo=0, passagens=1)
    assert lancador.SEGREDOS == {"GH_TOKEN": GH_FALSO}


def test_runner_env_ao_lado_da_config_sem_a_variavel(ctx, monkeypatch, tmp_path):
    _, path = ctx
    f = tmp_path / "runner.env"
    f.write_text(f"GH_TOKEN={GH_FALSO}\n", encoding="utf-8")
    f.chmod(0o600)
    espiao(monkeypatch)
    office_runner.ciclo(str(path), intervalo=0, passagens=1)
    assert lancador.SEGREDOS == {"GH_TOKEN": GH_FALSO}


@pytest.mark.parametrize("args", [["--intervalo", "-1"], ["--intervalo", "x"], ["--intervalo", "86401"],
                                  ["--passagens", "0"], ["--passagens", "1.5"]])
def test_argumentos_invalidos(ctx, args):
    with pytest.raises(SystemExit) as e:
        office_runner.main(["ciclo", *args])
    assert e.value.code == 2


def test_main_passa_os_argumentos(ctx, monkeypatch):
    _, path = ctx
    visto = []
    monkeypatch.setattr(office_runner, "ciclo", lambda *a: visto.append(a) or 0)
    monkeypatch.setattr(office_runner.lancador, "guardar_segredos", lambda: None)
    assert office_runner.main(["ciclo", "--intervalo", "0", "--passagens", "3", "--config", str(path)]) == 0
    assert office_runner.main(["ciclo", "--config", str(path)]) == 0
    assert visto == [(str(path), 0.0, 3), (str(path), 60.0, None)]


# ---------------------------------------------------------------- em subprocesso (o binário real)

def _env(tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OFFICE_", "FAKE_"))
           and k not in lancador.SEGREDO_CHAVES}
    env.update(c.GIT_ENV, HOME=str(tmp / "casa"), GIT_CONFIG_GLOBAL=str(tmp / "casa" / ".gitconfig"),
               PATH=os.pathsep.join([str(c.gh_sem_sessao(tmp)), str(c.BIN), os.environ.get("PATH", "")]))
    return env


def test_subprocesso_uma_passagem_grava_o_estado(ctx, tmp_path):
    cfg, path = ctx
    r = subprocess.run([sys.executable, str(c.RUNNER), "ciclo", "--passagens", "1", "--intervalo", "0",
                        "--config", str(path)], check=False, env=_env(tmp_path), capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert (officecfg.caminho(cfg, "runner_estado") / "estado.json").is_file()


def test_subprocesso_sigterm_sai_depressa(ctx, tmp_path):
    cfg, path = ctx
    estado = officecfg.caminho(cfg, "runner_estado") / "estado.json"
    p = subprocess.Popen([sys.executable, str(c.RUNNER), "ciclo", "--intervalo", "30", "--config", str(path)],
                         env=_env(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(300):
            if estado.exists() or p.poll() is not None:
                break
            time.sleep(0.05)
        assert estado.exists() and p.poll() is None
        t0 = time.monotonic()
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=10) == 0
        assert time.monotonic() - t0 < 5
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
