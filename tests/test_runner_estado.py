"""D15: o runner grava a cada passagem <dados>/runner-estado/estado.json (chaves exactas, só enumerados, sem segredos,
atómico, 0644); o gh é verificado no máximo de 10 em 10 min; a pasta é montada só de leitura no Office e negada ao
Edit do agente. Tudo em pastas temporárias, com `gh` falsos: nunca o gh real nem a casa real."""
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import diagnostico, lancador, office_runner, officecfg

CHAVE = "sk-ant-api03-" + "Zz9" * 12          # credencial falsa (formato válido)
TOKEN = "sk-ant-oat01-" + "Qq7" * 12
TELEGRAM = "123456:TELEGRAM-falso-" + "x" * 20
GH_TOKEN = "gh" + "o_" + "Ww5" * 12


def gh_falso(tmp: Path, rc: int = 0, dorme: float = 0) -> Path:
    """Pasta com um `gh` que conta as chamadas em tmp/gh-chamadas e sai com rc."""
    pasta = tmp / f"bin-gh-{rc}-{dorme}"
    pasta.mkdir()
    f = pasta / "gh"
    f.write_text(f"#!/bin/sh\necho \"$@\" >> '{tmp / 'gh-chamadas'}'\nsleep {dorme}\nexit {rc}\n", encoding="utf-8")
    f.chmod(0o755)
    return pasta


def chamadas(tmp: Path) -> list[str]:
    p = tmp / "gh-chamadas"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def ler(cfg) -> dict:
    return json.loads((officecfg.caminho(cfg, "runner_estado") / "estado.json").read_text(encoding="utf-8"))


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path, extra_bin=gh_falso(tmp_path))
    return tmp_path


NOW = datetime(2026, 11, 8, 19, 30).timestamp()


def test_chaves_exactas_modo_e_atomico(ctx):
    cfg, path = c.config(ctx)
    antigo = os.umask(0o077)   # o modo não depende do umask do timer
    try:
        doc = diagnostico.gravar_estado(cfg, str(path), NOW)
    finally:
        os.umask(antigo)
    pasta = officecfg.caminho(cfg, "runner_estado")
    assert pasta == ctx / "dados" / "runner-estado"
    assert list(ler(cfg)) == ["v", "em", "versao", "claude", "credencial", "gh"]
    assert ler(cfg) == doc
    assert doc["v"] == 1 and doc["versao"] == officecfg.VERSAO
    em = datetime.fromisoformat(doc["em"])
    assert em.tzinfo is not None and em.timestamp() == NOW and re.fullmatch(r"[\d\-T:]{19}[+-]\d\d:\d\d", doc["em"])
    assert (doc["claude"], doc["credencial"], doc["gh"]) == ("ok", "falta", "ok")
    assert (pasta / "estado.json").stat().st_mode & 0o777 == 0o644
    assert os.listdir(pasta) == ["estado.json"]   # sem .tmp


def test_pasta_criada_pelo_runner(ctx):
    cfg, path = c.config(ctx)
    pasta = officecfg.caminho(cfg, "runner_estado")
    pasta.rmdir()
    diagnostico.gravar_estado(cfg, str(path), NOW)
    assert pasta.is_dir() and ler(cfg)["v"] == 1


def test_sem_segredos_nem_caminhos(ctx, monkeypatch):
    # CA-D15.4: claude.env e runner.env com valores falsos conhecidos: nenhum chega ao estado.json
    cfg, path = c.config(ctx)
    cred = ctx / "claude.env"
    cred.write_text(f"ANTHROPIC_API_KEY={CHAVE}\n", encoding="utf-8")
    cred.chmod(0o600)
    renv = ctx / "runner.env"
    renv.write_text(f"OFFICE_TELEGRAM_TOKEN={TELEGRAM}\nGH_TOKEN={GH_TOKEN}\nCLAUDE_CODE_OAUTH_TOKEN={TOKEN}\n",
                    encoding="utf-8")
    renv.chmod(0o600)
    monkeypatch.setattr(lancador, "SEGREDOS", {"OFFICE_TELEGRAM_TOKEN": TELEGRAM, "GH_TOKEN": GH_TOKEN,
                                               "CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
    diagnostico.gravar_estado(cfg, str(path), NOW)
    texto = (officecfg.caminho(cfg, "runner_estado") / "estado.json").read_text(encoding="utf-8")
    assert json.loads(texto)["credencial"] == "ok"
    for segredo in (CHAVE, TOKEN, TELEGRAM, GH_TOKEN, "sk-ant", str(ctx), "claude.env", "runner.env"):
        assert segredo not in texto, segredo
    cache = (officecfg.caminho(cfg, "runner") / diagnostico.GH_CACHE).read_text(encoding="utf-8")
    assert GH_TOKEN not in cache and set(json.loads(cache)) == {"em", "gh"}


def _cred(ctx, conteudo=None, modo=0o600, renv=None, renv_modo=0o600, login=False):
    _, path = c.config(ctx)
    if conteudo is not None:
        f = ctx / "claude.env"
        f.write_text(conteudo, encoding="utf-8")
        f.chmod(modo)
    if renv is not None:
        f = ctx / "runner.env"
        f.write_text("# só para o modo\n", encoding="utf-8")
        f.chmod(renv_modo)
    if login:
        f = ctx / "casa" / ".claude" / ".credentials.json"
        f.parent.mkdir(parents=True)
        f.write_text("{}", encoding="utf-8")
        f.chmod(0)   # nunca é lido: só se confirma que existe
    return diagnostico.estado_credencial(str(path))


@pytest.mark.parametrize("caso, esperado", [
    ({"conteudo": f"ANTHROPIC_API_KEY={CHAVE}\n"}, "ok"),
    ({"conteudo": f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}\n"}, "ok"),
    ({"conteudo": f"ANTHROPIC_API_KEY={CHAVE}\n", "modo": 0o644}, "modo"),
    ({"conteudo": "ANTHROPIC_API_KEY=nao-e-chave\n"}, "falta"),
    ({"conteudo": "ANTHROPIC_API_KEY=nao-e-chave\n", "login": True}, "falta"),   # o claude.env manda
    ({}, "falta"),
    ({"login": True}, "ok"),
])
def test_credencial(ctx, caso, esperado):
    assert _cred(ctx, **caso) == esperado


@pytest.mark.parametrize("segredos, renv_modo, esperado", [
    ({"ANTHROPIC_API_KEY": CHAVE}, 0o600, "ok"),
    ({"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}, 0o600, "ok"),
    ({"ANTHROPIC_API_KEY": CHAVE}, 0o640, "modo"),
    ({"ANTHROPIC_API_KEY": TOKEN}, 0o600, "falta"),          # token da subscrição na variável da API
    ({"CLAUDE_CODE_OAUTH_TOKEN": "codigo-do-browser"}, 0o600, "falta"),
])
def test_credencial_do_runner_env(ctx, monkeypatch, segredos, renv_modo, esperado):
    monkeypatch.setattr(lancador, "SEGREDOS", dict(segredos))
    assert _cred(ctx, renv=True, renv_modo=renv_modo) == esperado


def test_gh_no_maximo_de_10_em_10_min(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path, extra_bin=gh_falso(tmp_path, rc=0))
    cfg, path = c.config(tmp_path)
    assert diagnostico.gravar_estado(cfg, str(path), NOW)["gh"] == "ok"
    assert chamadas(tmp_path) == ["auth status"]
    for dt in (60, 599):
        assert diagnostico.gravar_estado(cfg, str(path), NOW + dt)["gh"] == "ok"
    assert len(chamadas(tmp_path)) == 1                      # entre verificações repete o último valor
    diagnostico.gravar_estado(cfg, str(path), NOW + 600)
    assert len(chamadas(tmp_path)) == 2
    diagnostico.gravar_estado(cfg, str(path), NOW - 3600)    # relógio para trás: verifica de novo
    assert len(chamadas(tmp_path)) == 3


def test_gh_sem_sessao_e_cache_estragada(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path, extra_bin=gh_falso(tmp_path, rc=1))
    cfg, path = c.config(tmp_path)
    (officecfg.caminho(cfg, "runner") / diagnostico.GH_CACHE).write_text("{lixo", encoding="utf-8")
    assert diagnostico.gravar_estado(cfg, str(path), NOW)["gh"] == "sem sessão"
    assert diagnostico.gravar_estado(cfg, str(path), NOW + 1)["gh"] == "sem sessão"
    assert len(chamadas(tmp_path)) == 1


def test_gh_timeout(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path, extra_bin=gh_falso(tmp_path, rc=0, dorme=5))
    monkeypatch.setattr(diagnostico, "GH_S", 0.3)
    cfg, path = c.config(tmp_path)
    assert diagnostico.gravar_estado(cfg, str(path), NOW)["gh"] == "sem sessão"


def test_sem_claude_nem_gh(ctx, monkeypatch):
    vazio = ctx / "vazio"
    vazio.mkdir()
    monkeypatch.setenv("PATH", str(vazio))
    cfg, path = c.config(ctx)
    doc = diagnostico.gravar_estado(cfg, str(path), NOW)
    assert (doc["claude"], doc["gh"]) == ("falta", "sem sessão")


def test_uma_vez_grava_o_estado(ctx):
    cfg, path = c.config(ctx)
    assert office_runner.uma_vez(cfg, str(path), NOW) == []
    assert ler(cfg)["em"] == lancador.agora(cfg, NOW).isoformat(timespec="seconds")


def test_erro_de_io_nao_derruba_a_passagem(ctx, monkeypatch):
    cfg, path = c.config(ctx)
    pasta = officecfg.caminho(cfg, "runner_estado")
    pasta.rmdir()
    pasta.write_text("não é pasta", encoding="utf-8")
    c.repo(cfg)
    c.pedido(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    office_runner.uma_vez(cfg, str(path))
    assert c.feito(cfg)["task_id"] in c.tarefas(cfg)   # a fila foi tratada na mesma
    assert pasta.is_file()


def test_pasta_symlink_recusada_sem_escrever(ctx, monkeypatch):
    # <dados>/runner-estado trocada por um symlink: nada se escreve através dele e a passagem segue
    cfg, path = c.config(ctx)
    pasta = officecfg.caminho(cfg, "runner_estado")
    pasta.rmdir()
    alvo = ctx / "alvo"
    alvo.mkdir()
    pasta.symlink_to(alvo)
    with pytest.raises(OSError):
        diagnostico.gravar_estado(cfg, str(path), NOW)
    assert list(alvo.iterdir()) == []
    c.repo(cfg)
    c.pedido(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    office_runner.uma_vez(cfg, str(path))
    assert c.feito(cfg)["task_id"] in c.tarefas(cfg)
    assert list(alvo.iterdir()) == []


def test_compose_monta_so_o_estado_e_so_leitura(ctx):
    # CA-D15.2: bind read_only da pasta do estado; <dados>/runner (logs, settings, cache do gh) nunca é montada
    import gerar_compose  # em runner/, no sys.path depois do test_runner_comum

    cfg, path = c.config(ctx)
    out = gerar_compose.gerar(cfg, str(path))
    dados = ctx / "dados"
    linhas = out.splitlines()
    i = linhas.index("        target: /data/runner-estado")
    assert linhas[i - 1] == f'        source: "{dados / "runner-estado"}"'
    assert linhas[i + 1] == "        read_only: true"
    assert "      RUNNER_ESTADO_DIR: /data/runner-estado" in linhas
    assert f'"{dados / "runner"}"' not in out
