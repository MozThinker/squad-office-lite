"""Runner vs contentor hostil: as filas (decisoes-fila, quadro-lancar) são escritas pelo contentor. Symlinks plantados
lá (feito/ → fora, .tmp → ficheiro do utilizador) nunca levam o runner a mover, criar ou truncar nada fora da fila."""
import json
import os
import stat
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import decisoes, lancador, office_runner, officecfg

QUANDO = datetime(2026, 11, 6, 14, 30, tzinfo=ZoneInfo("Europe/Lisbon"))
D = "20261106T140000-aaaaaaaa.json"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    fora = tmp_path / "fora"          # o que o atacante quer atingir (p.ex. ~/.claude)
    fora.mkdir()
    alvo = fora / "settings.json"
    alvo.write_text('{"hooks": "do utilizador"}', encoding="utf-8")
    return cfg, path, fora, alvo


def processar(cfg):
    return decisoes.processar(officecfg.caminho(cfg, "decisoes_fila"), officecfg.caminho(cfg, "decisoes"),
                              officecfg.caminho(cfg, "runner") / ".historico.json", "Ana", QUANDO)


def intacto(fora, alvo):
    assert sorted(os.listdir(fora)) == ["settings.json"]
    assert alvo.read_text(encoding="utf-8") == '{"hooks": "do utilizador"}'


# ---------------------------------------------------------------- decisoes-fila

def test_decisoes_feito_symlink_para_fora_nao_move_nada(ctx):
    cfg, _, fora, alvo = ctx
    fila = officecfg.caminho(cfg, "decisoes_fila")
    (fila / D).write_text("{}", encoding="utf-8")
    (fila / "feito").symlink_to(fora)
    linhas = processar(cfg)
    intacto(fora, alvo)
    assert (fila / D).exists() and (fila / "feito").is_symlink()
    assert linhas and "feito" in linhas[0]


def test_decisoes_motivo_symlink_nao_escreve_no_alvo(ctx):
    cfg, _, fora, alvo = ctx
    fila = officecfg.caminho(cfg, "decisoes_fila")
    (fila / D).write_text("{", encoding="utf-8")
    (fila / "feito").mkdir()
    motivo = fila / "feito" / "20261106T140000-aaaaaaaa.motivo.txt"
    motivo.symlink_to(alvo)
    processar(cfg)
    intacto(fora, alvo)
    st = motivo.lstat()
    assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600
    assert motivo.read_text(encoding="utf-8") == "JSON ilegível\n"
    assert (fila / "feito" / D).exists()


def test_decisoes_feito_ficheiro_ou_destino_pasta_nao_rebentam(ctx):
    cfg, _, fora, alvo = ctx
    fila = officecfg.caminho(cfg, "decisoes_fila")
    (fila / D).write_text("{", encoding="utf-8")
    (fila / "feito").write_text("x", encoding="utf-8")
    assert "feito" in processar(cfg)[0] and (fila / D).exists()
    (fila / "feito").unlink()
    (fila / "feito").mkdir()
    (fila / "feito" / D).mkdir()          # destino ocupado por uma pasta: rename falha, passagem continua
    linhas = processar(cfg)
    assert any("não foi possível" in x for x in linhas) and (fila / D).exists()
    intacto(fora, alvo)


def test_decisoes_nomes_fora_da_regex_sao_ignorados(ctx):
    cfg, _, _, _ = ctx
    fila = officecfg.caminho(cfg, "decisoes_fila")
    for n in ("com espaço.json", "-x.json", "a\nb.json"):
        (fila / n).write_text("{}", encoding="utf-8")
    assert processar(cfg) == [] and not (fila / "feito").exists()


def test_decisoes_fila_symlink_nao_e_lida(ctx):
    cfg, _, fora, _ = ctx
    fila = officecfg.caminho(cfg, "decisoes_fila")
    fila.rmdir()
    fila.symlink_to(fora)
    (fora / D).write_text("{", encoding="utf-8")
    assert processar(cfg) == []
    assert sorted(os.listdir(fora)) == [D, "settings.json"]


# ---------------------------------------------------------------- quadro-lancar: responder e rotação

def test_responder_tmp_symlink_nao_trunca_o_alvo(ctx):
    cfg, path, fora, alvo = ctx
    fila = officecfg.caminho(cfg, "quadro_lancar")
    stem = "20261106T120000-0a1b2c3d"
    c.pedido(cfg, stem=stem, squad="mkt")              # recusado → responder escreve o .feito.json
    (fila / f".{stem}.feito.tmp").symlink_to(alvo)
    office_runner.uma_vez(cfg, str(path))
    intacto(fora, alvo)
    feito = fila / f"{stem}.feito.json"
    assert stat.S_ISREG(feito.lstat().st_mode) and c.feito(cfg, stem) == {"erro": "squad inválida"}
    assert not [n for n in os.listdir(fila) if n.endswith(".feito.tmp") and not os.path.islink(fila / n)]


def test_resposta_que_falha_nao_para_a_fila(ctx, monkeypatch):
    # destino da resposta impossível de gravar (ex.: pasta criada pelo contentor): o pedido seguinte é tratado na mesma
    cfg, path, _, _ = ctx
    fila = officecfg.caminho(cfg, "quadro_lancar")
    a, b = "20261106T120000-0a1b2c3d", "20261106T120001-0a1b2c3e"
    c.pedido(cfg, stem=a, squad="mkt")
    c.pedido(cfg, stem=b, squad="mkt")
    real = lancador.responder

    def falha_no_primeiro(fila_, stem, corpo):
        if stem == a:
            raise IsADirectoryError(stem)
        real(fila_, stem, corpo)

    monkeypatch.setattr(lancador, "responder", falha_no_primeiro)
    linhas = lancador.processar_fila(cfg, str(path))
    assert any("resposta não gravada" in x for x in linhas)
    assert c.feito(cfg, b) == {"erro": "squad inválida"}
    assert not [n for n in os.listdir(fila) if n.endswith(".feito.tmp")]


def test_lancados_hoje_ignora_symlinks_e_fifos(ctx):
    cfg, _, _, alvo = ctx
    fila = officecfg.caminho(cfg, "quadro_lancar")
    alvo.write_text('{"task_id": "x"}', encoding="utf-8")
    (fila / "20261106T090000-11111111.feito.json").symlink_to(alvo)
    os.mkfifo(fila / "20261106T090000-22222222.feito.json")
    (fila / "20261106T090000-33333333.feito.json").write_text('{"task_id": "y"}', encoding="utf-8")
    assert lancador.lancados_hoje(cfg, fila, time.time()) == 1


def _trio_velho(fila, stem):
    for suf in office_runner.SUFIXOS_ROTACAO:
        (fila / f"{stem}{suf}").write_text("{}", encoding="utf-8")
    t = time.time() - 30 * 86400
    os.utime(fila / f"{stem}.movido.json", (t, t))


def test_rotacao_feito_symlink_para_fora_nao_move_nada(ctx):
    cfg, _, fora, alvo = ctx
    fila = officecfg.caminho(cfg, "quadro_lancar")
    _trio_velho(fila, "20261001T100000-aaaaaaaa")
    (fila / "feito").symlink_to(fora)
    linhas = office_runner.rodar(cfg)
    intacto(fora, alvo)
    assert linhas == ["pedido 20261001T100000-aaaaaaaa não arquivado: erro a mover para feito/"]
    assert (fila / "20261001T100000-aaaaaaaa.movido.json").exists()


def test_rotacao_move_symlinks_da_fila_sem_tocar_no_alvo(ctx):
    cfg, _, fora, alvo = ctx
    fila = officecfg.caminho(cfg, "quadro_lancar")
    stem = "20261001T100000-bbbbbbbb"
    _trio_velho(fila, stem)
    (fila / f"{stem}.feito.json").unlink()
    (fila / f"{stem}.feito.json").symlink_to(alvo)
    assert office_runner.rodar(cfg) == [f"pedido {stem} arquivado em feito/"]
    intacto(fora, alvo)
    assert (fila / "feito" / f"{stem}.feito.json").is_symlink()


# ---------------------------------------------------------------- timeout: o grupo inteiro morre

def _morto(pid):
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:   # já saiu (ESRCH a meio da leitura incluído)
        return True


def test_timeout_mata_o_grupo_do_claude(tmp_path, monkeypatch):
    falso = tmp_path / "bin"
    falso.mkdir()
    pidf = tmp_path / "filho.pid"
    (falso / "claude").write_text(f"#!/bin/sh\nsleep 300 &\necho $! > {pidf}\nsleep 300\n", encoding="utf-8")
    (falso / "claude").chmod(0o755)
    c.ambiente(monkeypatch, tmp_path, extra_bin=falso)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    monkeypatch.setattr(lancador, "TIMEOUT_S", {"task": 1, "goal": 1})
    c.pedido(cfg)
    office_runner.uma_vez(cfg, str(path))
    m = lancador.correr(cfg, c.feito(cfg)["task_id"])
    assert m["status"] == "erro" and m["result"].startswith("tempo esgotado")
    filho = int(pidf.read_text(encoding="utf-8"))
    fim = time.time() + 10
    while not _morto(filho) and time.time() < fim:
        time.sleep(0.1)
    assert _morto(filho), "o filho do claude sobreviveu ao timeout"


def test_formato_do_feito_json(ctx):
    # o Office lê o .feito.json: continua JSON válido depois do escrever seguro
    cfg, _, _, _ = ctx
    fila = officecfg.caminho(cfg, "quadro_lancar")
    lancador.responder(fila, "20261106T120000-0a1b2c3d", {"erro": "x ç"})
    assert json.loads((fila / "20261106T120000-0a1b2c3d.feito.json").read_text(encoding="utf-8")) == {"erro": "x ç"}
