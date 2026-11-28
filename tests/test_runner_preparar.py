"""Runner: a meta «a preparar» escrita antes do worktree (o cartão aparece logo no Quadro), o erro com motivo quando a
preparação falha depois disso, e o que a retoma, o parar, as rotinas e o paralelo fazem com ela. Pastas temporárias,
`git` local, sem rede."""
import json
import os
import subprocess
import sys
import time

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner, officecfg, rotinas


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    return cfg, path, tmp_path


def _p(**kw):
    p = {"id": "20261106T120000-0a1b2c3d", "squad": "dev", "projecto": "exemplo", "kind": "feat", "mode": "task",
         "slug": "botao-novo", "titulo": "Botão novo", "brief": "Põe um botão."}
    p.update(kw)
    return p


def _meta(cfg, tid) -> dict:
    return json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text(encoding="utf-8"))


NOW = time.time()


def test_meta_a_preparar_existe_durante_o_worktree_add(ctx, monkeypatch):
    cfg, _, _ = ctx
    vistas, git_real = [], lancador.git

    def git_espia(args, cwd):
        if args[:2] == ["worktree", "add"]:   # o que o Office vê enquanto o git copia o repositório
            metas = list(officecfg.caminho(cfg, "tasks").glob("*/meta.json"))
            vistas.append([json.loads(m.read_text(encoding="utf-8")) for m in metas])
        return git_real(args, cwd)

    monkeypatch.setattr(lancador, "git", git_espia)
    tid = lancador.preparar(cfg, _p(), NOW)
    (antes,) = vistas
    assert len(antes) == 1 and antes[0]["id"] == tid
    assert antes[0]["status"] == "a preparar" and antes[0]["pid"] == os.getpid()
    assert antes[0]["issues"] == [] and antes[0]["finished_at"] is None
    m = _meta(cfg, tid)
    assert m["status"] == "a correr" and m["pid"] is None and m["worktree"] == antes[0]["worktree"]
    assert (officecfg.caminho(cfg, "tasks") / tid / "task.md").read_text(encoding="utf-8").startswith("# Botão novo")
    assert lancador.ler_estado(cfg, tid)["worktree"] == m["worktree"]   # o estado do runner já estava gravado


def test_validacoes_falhadas_nao_deixam_meta(ctx):
    cfg, _, _ = ctx
    repo = officecfg.raiz(cfg, "projectos") / "exemplo"
    c.git(["branch", "feat/botao-novo"], repo)
    with pytest.raises(lancador.Falha, match="já existe"):
        lancador.preparar(cfg, _p(), NOW)
    assert c.tarefas(cfg) == []


def test_worktree_add_falhado_fica_em_erro_com_motivo(ctx, monkeypatch):
    cfg, path, _ = ctx
    git_real = lancador.git
    monkeypatch.setattr(lancador, "git", lambda a, cwd: subprocess.CompletedProcess(a, 128, "", "x")
                        if a[:2] == ["worktree", "add"] else git_real(a, cwd))
    c.pedido(cfg)
    linhas = office_runner.uma_vez(cfg, str(path))
    assert c.feito(cfg) == {"erro": "git worktree add falhou"}
    assert any("não lançado: git worktree add falhou" in x for x in linhas)
    (tid,) = c.tarefas(cfg)
    m = _meta(cfg, tid)
    assert m["status"] == "erro" and m["issues"] == ["git worktree add falhou"] and m["pid"] is None
    assert m["finished_at"]


def test_oserror_depois_da_meta_fica_em_erro_e_sobe(ctx, monkeypatch):
    cfg, _, _ = ctx

    def rebenta(*a, **k):
        raise PermissionError("sem permissão")

    monkeypatch.setattr(lancador, "gravar_estado", rebenta)
    with pytest.raises(PermissionError):
        lancador.preparar(cfg, _p(), NOW)
    (tid,) = c.tarefas(cfg)
    m = _meta(cfg, tid)
    assert m["status"] == "erro" and m["issues"] == ["erro a preparar a tarefa no host (PermissionError)"]


def _a_preparar(cfg, tid, pid, idade_s):
    workdir = officecfg.caminho(cfg, "tasks") / tid
    workdir.mkdir(parents=True)
    lancador.escrever_json(workdir / "meta.json", {"id": tid, "status": "a preparar", "pid": pid,
                                                   "finished_at": None, "issues": []})
    (workdir / "heartbeat").touch()
    t = time.time() - idade_s
    os.utime(workdir / "heartbeat", (t, t))
    return workdir


def test_retomas_deixa_a_preparar_com_lancador_vivo(ctx):
    cfg, path, _ = ctx
    tid = "20261106-1200-viva"
    _a_preparar(cfg, tid, os.getpid(), 10 * 60)   # heartbeat parado, mas quem prepara ainda existe
    _a_preparar(cfg, "20261106-1200-fresca", None, 0)   # heartbeat fresco
    assert lancador.retomas(cfg, str(path)) == []
    assert _meta(cfg, tid)["status"] == "a preparar"
    assert _meta(cfg, "20261106-1200-fresca")["status"] == "a preparar"


def test_retomas_fecha_a_preparar_orfa(ctx, monkeypatch):
    cfg, path, _ = ctx
    morto = subprocess.Popen([sys.executable, "-c", "pass"])
    morto.wait()
    _a_preparar(cfg, "20261106-1200-morta", morto.pid, 10 * 60)
    _a_preparar(cfg, "20261106-1200-velha", os.getpid(), 2 * 3600)   # pid reaproveitado: passou o PREPARAR_S
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: pytest.fail("nunca se retoma uma preparação"))
    avisos = []
    monkeypatch.setattr(lancador, "avisar", avisos.append)
    linhas = lancador.retomas(cfg, str(path))
    assert sorted(avisos) == [f"Squad Office: 20261106-1200-{x} — erro (a preparação foi interrompida)"
                              for x in ("morta", "velha")]
    assert sorted(linhas) == [f"tarefa 20261106-1200-{x} interrompida na preparação: fechada em erro"
                              for x in ("morta", "velha")]
    for x in ("morta", "velha"):
        m = _meta(cfg, f"20261106-1200-{x}")
        assert m["status"] == "erro" and m["issues"] == ["a preparação da tarefa foi interrompida"]


def test_a_preparar_conta_no_paralelo(ctx):
    cfg, _, _ = ctx
    _a_preparar(cfg, "20261106-1200-x", os.getpid(), 0)
    assert lancador.a_correr(cfg, time.time()) == 1


def test_parar_a_preparar_deixa_a_marca_e_o_correr_nao_lanca(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid = None

    git_real = lancador.git

    def git_espia(args, cwd):
        nonlocal tid
        if args[:2] == ["worktree", "add"]:   # o utilizador pára a meio da preparação
            tid = c.tarefas(cfg)[0]
            assert lancador.parar(cfg, tid)[0] == 0
        return git_real(args, cwd)

    monkeypatch.setattr(lancador, "git", git_espia)
    assert lancador.preparar(cfg, _p(), NOW) == tid
    assert (officecfg.caminho(cfg, "tasks") / tid / "parar").exists()
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(ctx[2] / "argv.jsonl"))
    m = lancador.correr(cfg, tid)
    assert m["status"] == "erro" and m["result"] == lancador.PARADA
    assert not (ctx[2] / "argv.jsonl").exists()   # o claude não arrancou


def test_rotinas_contam_a_preparar_como_aberta(ctx):
    cfg, _, _ = ctx
    _a_preparar(cfg, "20261106-1200-r", os.getpid(), 0)
    r = {"tarefas": ["20261106-1200-r"], "falhas": 2}
    assert rotinas._fechar_tarefas(cfg, r) is False and r == {"tarefas": ["20261106-1200-r"], "falhas": 2}
