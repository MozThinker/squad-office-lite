"""Runner: fila do Quadro (AC-c1), tecto e paralelo (AC-c3), rotação (AC-c6), lock, CLI e config partilhada (AC-a9)."""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner, officecfg


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    lancados = []
    monkeypatch.setattr(lancador, "destacar", lambda cfg, p, tid, retomar=False: lancados.append(tid) or 4242)
    return cfg, path, lancados


def passar(cfg, path, now=None):
    return office_runner.uma_vez(cfg, str(path), now)


# ---------------------------------------------------------------- pedido válido

def test_pedido_valido_responde_task_id_e_prepara_a_tarefa(ctx):
    cfg, path, lancados = ctx
    c.pedido(cfg)
    linhas = passar(cfg, path)
    tid = c.feito(cfg)["task_id"]
    assert set(c.feito(cfg)) == {"task_id"}
    assert tid.endswith("-botao-novo") and lancados == [tid]
    assert any("lançado" in x for x in linhas)
    workdir = officecfg.caminho(cfg, "tasks") / tid
    meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
    for k in ("id", "squad", "project", "task", "mode", "status", "started_at", "finished_at", "worktree", "branch",
              "session_id", "claude_dir", "pr", "cost_usd", "turns", "duration_s"):
        assert k in meta
    assert meta["id"] == tid and meta["squad"] == "dev" and meta["project"] == "exemplo"
    assert meta["status"] == "a correr" and meta["mode"] == "task" and meta["kind"] == "feat"
    assert meta["branch"] == "feat/botao-novo" and meta["base"] == "main" and meta["repo"] is None
    assert meta["task"] == "# Botão novo\n\nPõe um botão na página.\n"
    assert meta["pedido"] == "20261106T120000-0a1b2c3d" and meta["auto_resumes"] == 0
    assert "+" in meta["started_at"] or meta["started_at"].endswith("Z") or meta["started_at"][-6] in "+-"
    wt = Path(meta["worktree"])
    assert wt == officecfg.raiz(cfg, "projectos") / "exemplo" / ".worktrees" / tid and wt.is_dir()
    assert meta["claude_dir"] == officecfg.mangle(str(wt))
    assert c.git(["rev-parse", "--abbrev-ref", "HEAD"], wt) == "feat/botao-novo"
    assert (workdir / "task.md").read_text(encoding="utf-8") == meta["task"]
    assert (workdir / "heartbeat").exists()
    excl = officecfg.raiz(cfg, "projectos") / "exemplo" / ".git" / "info" / "exclude"
    assert ".worktrees/" in excl.read_text(encoding="utf-8").split("\n")
    assert c.git(["status", "--porcelain"], officecfg.raiz(cfg, "projectos") / "exemplo") == ""


def test_projecto_com_repo_proprio_e_modo_goal(ctx):
    cfg, path, _ = ctx
    c.repo(cfg, "loja-web")
    c.pedido(cfg, projecto="loja", mode="goal", kind="fix", slug="corrige-carrinho")
    passar(cfg, path)
    tid = c.feito(cfg)["task_id"]
    meta = json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text(encoding="utf-8"))
    assert meta["branch"] == "fix/corrige-carrinho" and meta["mode"] == "goal"
    assert "/loja-web/.worktrees/" in meta["worktree"]


def test_resposta_escrita_por_tmp_e_rename(ctx):
    cfg, path, _ = ctx
    c.pedido(cfg)
    passar(cfg, path)
    nomes = os.listdir(officecfg.caminho(cfg, "quadro_lancar"))
    assert not [n for n in nomes if n.endswith(".tmp")]


# ---------------------------------------------------------------- pedidos inválidos: {"erro"} e nada lançado

INVALIDOS = [
    ({"extra": 1}, "campos inválidos"),
    ({"squad": "ops"}, "squad inválida"),            # fora da config
    ({"squad": "mkt"}, "squad inválida"),            # na config mas formato sessao: não é lançável
    ({"projecto": "outro"}, "projecto inválido"),
    ({"projecto": "Exemplo"}, "projecto inválido"),  # o nome não serve: é o id
    ({"kind": "chore"}, "tipo inválido"),
    ({"mode": None}, "modo inválido"),
    ({"slug": "-mau"}, "slug inválido"),
    ({"titulo": ""}, "título inválido"),
    ({"titulo": "x" * 121}, "título inválido"),
    ({"titulo": "a\u202eb"}, "título inválido"),
    ({"titulo": "a\x07"}, "título inválido"),
    ({"brief": "a\x00b"}, "brief inválido"),
    ({"brief": "a\U000e0041"}, "brief inválido"),
    ({"brief": "é" * 4097}, "brief inválido"),
    ({"pedido_em": "2026-11-06T12:00:00"}, "pedido_em inválido"),
    ({"v": "1"}, "versão inválida"),
    ({"v": True}, "versão inválida"),
    ({"id": "20261106T120000-ffffffff"}, "id diferente do nome"),
    ({"cartao_id": "../x"}, "campos inválidos"),
]


@pytest.mark.parametrize("troca,motivo", INVALIDOS)
def test_pedido_invalido_da_erro_e_nao_lanca(ctx, troca, motivo):
    cfg, path, lancados = ctx
    c.pedido(cfg, **troca)
    passar(cfg, path)
    assert c.feito(cfg) == {"erro": motivo}
    assert lancados == [] and c.tarefas(cfg) == []
    assert not (officecfg.raiz(cfg, "projectos") / "exemplo" / ".worktrees").exists()


def test_chaves_fora_de_ordem_sao_recusadas(ctx):
    cfg, path, lancados = ctx
    p = c.pedido(cfg)
    d = json.loads(p.read_text(encoding="utf-8"))
    p.write_text(json.dumps(dict(reversed(list(d.items())))), encoding="utf-8")
    passar(cfg, path)
    assert c.feito(cfg) == {"erro": "campos inválidos"} and lancados == []


def test_brief_com_zwj_e_aceite(ctx):
    cfg, path, lancados = ctx
    c.pedido(cfg, brief="família 👨‍👩‍👧")
    passar(cfg, path)
    assert "task_id" in c.feito(cfg) and len(lancados) == 1


def test_symlink_e_recusado(ctx, tmp_path):
    cfg, path, lancados = ctx
    alvo = c.pedido(cfg, stem="20261106T110000-00000000")
    os.rename(alvo, tmp_path / "fora.json")
    stem = "20261106T120000-0a1b2c3d"
    (officecfg.caminho(cfg, "quadro_lancar") / f"{stem}.json").symlink_to(tmp_path / "fora.json")
    passar(cfg, path)
    assert c.feito(cfg, stem) == {"erro": "pedido ilegível"} and lancados == []


def test_mais_de_32kb_e_recusado(ctx):
    cfg, path, lancados = ctx
    p = c.pedido(cfg)
    p.write_text(p.read_text(encoding="utf-8")[:-1] + " " * (32 * 1024) + "}", encoding="utf-8")
    passar(cfg, path)
    assert c.feito(cfg) == {"erro": "pedido com mais de 32 KB"} and lancados == []


def test_json_partido_e_recusado(ctx):
    cfg, path, lancados = ctx
    c.pedido(cfg).write_text("{", encoding="utf-8")
    passar(cfg, path)
    assert c.feito(cfg) == {"erro": "pedido ilegível"} and lancados == []


def test_nomes_fora_do_contrato_e_respondidos_sao_ignorados(ctx):
    cfg, path, lancados = ctx
    fila = officecfg.caminho(cfg, "quadro_lancar")
    c.pedido(cfg)
    (fila / "20261106T120000-0a1b2c3d.feito.json").write_text('{"task_id": "x"}', encoding="utf-8")
    (fila / ".20261106T130000-0a1b2c3d.json.tmp").write_text("{}", encoding="utf-8")
    (fila / "lixo.json").write_text("{}", encoding="utf-8")
    (fila / "20261106T130000-0A1B2C3D.json").write_text("{}", encoding="utf-8")
    assert passar(cfg, path) == []
    assert lancados == [] and sorted(os.listdir(fila)) == sorted(
        ["20261106T120000-0a1b2c3d.json", "20261106T120000-0a1b2c3d.feito.json", ".20261106T130000-0a1b2c3d.json.tmp",
         "lixo.json", "20261106T130000-0A1B2C3D.json"])


def test_repositorio_inexistente(ctx):
    cfg, path, lancados = ctx
    c.pedido(cfg, projecto="loja")
    passar(cfg, path)
    assert c.feito(cfg) == {"erro": "repositório não encontrado"} and lancados == []


def test_branch_ja_existente(ctx):
    cfg, path, lancados = ctx
    c.git(["branch", "feat/botao-novo"], officecfg.raiz(cfg, "projectos") / "exemplo")
    c.pedido(cfg)
    passar(cfg, path)
    assert c.feito(cfg) == {"erro": "o branch feat/botao-novo já existe"} and lancados == [] and c.tarefas(cfg) == []


# ---------------------------------------------------------------- tecto diário e paralelo (AC-c3)

def test_tecto_diario(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path, tecto=1, paralelo=4)
    c.repo(cfg)
    lancados, avisos = [], []
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: lancados.append(a[2]) or 1)
    monkeypatch.setattr(lancador, "avisar", avisos.append)
    fila = officecfg.caminho(cfg, "quadro_lancar")
    ontem = fila / "20261105T120000-11111111.feito.json"
    ontem.write_text('{"task_id": "velho"}', encoding="utf-8")
    os.utime(ontem, (time.time() - 2 * 86400,) * 2)
    (fila / "20261106T090000-22222222.feito.json").write_text('{"erro": "x"}', encoding="utf-8")
    c.pedido(cfg, stem="20261106T100000-33333333")
    c.pedido(cfg, stem="20261106T110000-44444444", slug="outro-botao")
    passar(cfg, path)
    assert "task_id" in c.feito(cfg, "20261106T100000-33333333")   # ontem e erros não contam
    assert c.feito(cfg, "20261106T110000-44444444") == {"erro": "tecto diário"}
    assert len(lancados) == 1 and len(avisos) == 1 and "tecto" in avisos[0]


def _a_correr(cfg, tid, idade_s):
    w = officecfg.caminho(cfg, "tasks") / tid
    w.mkdir(parents=True)
    (w / "meta.json").write_text(json.dumps({"id": tid, "status": "a correr", "pid": None}), encoding="utf-8")
    (w / "heartbeat").touch()
    t = time.time() - idade_s
    os.utime(w / "heartbeat", (t, t))


def test_paralelo_cheio_deixa_pendente(ctx):
    cfg, path, lancados = ctx
    _a_correr(cfg, "20261106-1000-outra", 10)
    c.pedido(cfg)
    linhas = passar(cfg, path)
    assert c.feito(cfg) is None and lancados == [] and any("pendente" in x for x in linhas)
    # quando a outra acaba, o mesmo pedido é lançado na passagem seguinte
    m = officecfg.caminho(cfg, "tasks") / "20261106-1000-outra" / "meta.json"
    m.write_text(json.dumps({"id": "20261106-1000-outra", "status": "concluída"}), encoding="utf-8")
    passar(cfg, path)
    assert "task_id" in c.feito(cfg) and len(lancados) == 1


def test_execucao_com_heartbeat_parado_nao_conta_para_o_paralelo(ctx):
    cfg, path, _ = ctx
    _a_correr(cfg, "20261106-1000-outra", 10 * 60)
    meta = officecfg.caminho(cfg, "tasks") / "20261106-1000-outra" / "meta.json"
    meta.write_text(json.dumps({"id": "20261106-1000-outra", "status": "a correr", "pid": None,
                                "auto_resumes": 2}), encoding="utf-8")
    c.pedido(cfg)
    passar(cfg, path)
    assert "task_id" in c.feito(cfg)


def test_paralelo_dois_lanca_dois(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path, paralelo=2)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 1)
    c.pedido(cfg, stem="20261106T100000-33333333", slug="um-botao")
    c.pedido(cfg, stem="20261106T110000-44444444", slug="dois-botoes")
    c.pedido(cfg, stem="20261106T120000-55555555", slug="tres-botoes")
    passar(cfg, path)
    assert "task_id" in c.feito(cfg, "20261106T100000-33333333")
    assert "task_id" in c.feito(cfg, "20261106T110000-44444444")
    assert c.feito(cfg, "20261106T120000-55555555") is None


# ---------------------------------------------------------------- rotação (AC-c6)

def _trio(fila, stem, idade_dias, movido=True):
    for suf in (".json", ".feito.json") + ((".movido.json",) if movido else ()):
        (fila / f"{stem}{suf}").write_text("{}", encoding="utf-8")
    if movido:
        t = time.time() - idade_dias * 86400
        os.utime(fila / f"{stem}.movido.json", (t, t))


def test_rotacao_move_trios_antigos_pela_ordem(ctx, monkeypatch):
    cfg, _, _ = ctx
    fila = officecfg.caminho(cfg, "quadro_lancar")
    _trio(fila, "20261001T100000-aaaaaaaa", 15)
    _trio(fila, "20261101T100000-bbbbbbbb", 13)
    _trio(fila, "20261001T100000-cccccccc", 30, movido=False)
    ordem = []
    real = os.rename
    monkeypatch.setattr(office_runner.seguro.os, "rename", lambda a, b, **k: ordem.append(Path(a).name) or real(a, b, **k))
    linhas = office_runner.rodar(cfg)
    assert ordem == ["20261001T100000-aaaaaaaa.json", "20261001T100000-aaaaaaaa.feito.json",
                     "20261001T100000-aaaaaaaa.movido.json"]
    assert sorted(os.listdir(fila / "feito")) == sorted(ordem) and len(linhas) == 1
    assert (fila / "20261101T100000-bbbbbbbb.movido.json").exists()
    assert (fila / "20261001T100000-cccccccc.json").exists()


def test_rotacao_respeita_rotacao_dias(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, _ = c.config(tmp_path, rotacao=1)
    fila = officecfg.caminho(cfg, "quadro_lancar")
    _trio(fila, "20261101T100000-bbbbbbbb", 2)
    office_runner.rodar(cfg)
    assert (fila / "feito" / "20261101T100000-bbbbbbbb.movido.json").exists()


# ---------------------------------------------------------------- lock, CLI, config

def test_lock_impede_duas_passagens(ctx):
    cfg, path, lancados = ctx
    c.pedido(cfg)
    runner = officecfg.caminho(cfg, "runner")
    with open(runner / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert passar(cfg, path) == []
    assert c.feito(cfg) is None and lancados == []


def _cli(args, env):
    return subprocess.run([sys.executable, str(c.RUNNER), *args], capture_output=True, text=True, env=env, timeout=60,
                          check=False)


def test_cli_config_invalida_sai_1_e_nao_lanca(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.pedido(cfg)
    path.write_text(path.read_text(encoding="utf-8") + "inventada: 1\n", encoding="utf-8")
    r = _cli(["--uma-vez", "--config", str(path)], dict(os.environ))
    assert r.returncode == 1 and "erro" in r.stderr and str(tmp_path) not in r.stderr
    assert c.feito(cfg) is None
    r = _cli(["--uma-vez", "--config", str(tmp_path / "nao-existe.yaml")], dict(os.environ))
    assert r.returncode == 1


def test_cli_le_office_config_do_ambiente_e_sem_nada_nao_imprime(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    _, path = c.config(tmp_path)
    r = _cli(["--uma-vez"], dict(os.environ, OFFICE_CONFIG=str(path)))
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_cli_sem_accao_da_uso(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    assert _cli([], dict(os.environ)).returncode == 2


def test_config_partilhada_com_o_servidor():
    # AC-a9: o runner usa o mesmo officecfg.py da raiz do repo (o que o servidor importa)
    assert Path(lancador.officecfg.__file__).resolve() == c.RAIZ / "officecfg.py"
    assert lancador.officecfg is officecfg is office_runner.officecfg
    cfg, avisos = officecfg.carregar(c.RAIZ / "office.example.yaml", estrito=True)
    assert avisos == [] and list(lancador.lancaveis(cfg)) == ["dev"]


def test_rotacao_apaga_settings_de_tarefas_fechadas_ha_mais_de_rotacao_dias(tmp_path, monkeypatch):
    # o correr apaga-as ao fechar; estas ficaram (ex.: fechada pelo retomas()); as «a correr» e as recentes ficam
    c.ambiente(monkeypatch, tmp_path)
    cfg, _ = c.config(tmp_path, rotacao=2)
    tasks, pasta = officecfg.caminho(cfg, "tasks"), officecfg.caminho(cfg, "runner") / "settings"
    pasta.mkdir(parents=True)
    agora = time.time()

    def tarefa(tid, status, dias):
        (tasks / tid).mkdir()
        fim = None if dias is None else lancador.agora(cfg, agora - dias * 86400).isoformat(timespec="seconds")
        (tasks / tid / "meta.json").write_text(json.dumps({"id": tid, "status": status, "finished_at": fim}),
                                               encoding="utf-8")
        (pasta / f"{tid}.json").write_text("{}", encoding="utf-8")

    tarefa("20261001-1000-velha", "erro", 3)
    tarefa("20261001-1000-velha-ok", "concluída", 30)
    tarefa("20261101-1000-recente", "erro", 1)
    tarefa("20261001-1000-a-correr", "a correr", None)
    (pasta / "20261001-1000-sem-meta.json").write_text("{}", encoding="utf-8")
    os.utime(pasta / "20261001-1000-sem-meta.json", (agora - 3 * 86400,) * 2)
    (pasta / "20261001-1000-sem-meta-nova.json").write_text("{}", encoding="utf-8")
    (pasta / "nao-e-tid.json").write_text("{}", encoding="utf-8")
    fora = tmp_path / "fora.json"
    fora.write_text("intacto", encoding="utf-8")
    (pasta / "20261001-1000-link.json").symlink_to(fora)
    os.utime(pasta / "20261001-1000-link.json", (agora - 3 * 86400,) * 2, follow_symlinks=False)
    assert office_runner.rodar(cfg, agora) == []   # sem linhas no journal
    assert sorted(os.listdir(pasta)) == ["20261001-1000-a-correr.json", "20261001-1000-sem-meta-nova.json",
                                         "20261101-1000-recente.json", "nao-e-tid.json"]
    assert fora.read_text(encoding="utf-8") == "intacto"
