"""Cópia dos agentes da squad para o worktree (CA-14), squads[].modelo e tecto da rotina no argv (CA-21, CA-36).
Repos git locais, `claude` falso de tests/bin, tudo em tmp_path."""
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import agentes_copia, lancador, office_runner, officecfg

REVISOR = b"---\nname: revisor\ndescription: Revisor\nmodel: haiku\n---\nRev\xc3\xaa o c\xc3\xb3digo.\n"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(tmp_path / "argv.jsonl"))
    return cfg, path, tmp_path


def squad(cfg, sq="dev") -> Path:
    base = officecfg.caminho(cfg, "agentes") / sq
    (base / "agents").mkdir(parents=True)
    (base / "agents" / "revisor.md").write_bytes(REVISOR)
    (base / "skills" / "testes" / "ref").mkdir(parents=True)
    (base / "skills" / "testes" / "SKILL.md").write_text("---\nname: testes\ndescription: T\n---\n", "utf-8")
    (base / "skills" / "testes" / "ref" / "notas.md").write_text("notas\n", "utf-8")
    (base / "CLAUDE.md").write_text("# Instruções da squad\n", "utf-8")
    return base


def preparar(cfg, path) -> str:
    c.pedido(cfg)
    office_runner.uma_vez(cfg, str(path))
    return c.feito(cfg)["task_id"]


def meta(cfg, tid) -> dict:
    return json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text(encoding="utf-8"))


COPIADOS = [".claude/CLAUDE.md", ".claude/agents/revisor.md", ".claude/skills/testes/SKILL.md",
            ".claude/skills/testes/ref/notas.md"]


def test_preparar_copia_exclui_e_status_limpo(ctx):
    cfg, path, _ = ctx
    base = squad(cfg)
    tid = preparar(cfg, path)
    m = meta(cfg, tid)
    wt = Path(m["worktree"])
    assert m["agentes_copiados"] == COPIADOS and m["avisos_agentes"] == []
    assert (wt / ".claude" / "agents" / "revisor.md").read_bytes() == REVISOR
    assert (wt / ".claude" / "skills" / "testes" / "ref" / "notas.md").read_text("utf-8") == "notas\n"
    assert (wt / ".claude" / "CLAUDE.md").read_bytes() == (base / "CLAUDE.md").read_bytes()
    excl = Path(c.git(["rev-parse", "--path-format=absolute", "--git-path", "info/exclude"], wt))
    linhas = excl.read_text("utf-8").split("\n")
    assert all(f"/{x}" in linhas for x in COPIADOS)
    assert c.git(["status", "--porcelain"], wt) == ""
    # o claude corre com o argv e o HOME de sempre: nada de agentes no argv
    lancador.correr(cfg, tid)
    argv = json.loads((ctx[2] / "argv.jsonl").read_text("utf-8").splitlines()[0])
    assert not any("agentes" in a or "revisor" in a for a in argv)


def test_squad_sem_agentes_nao_cria_claude(ctx):
    cfg, path, _ = ctx
    tid = preparar(cfg, path)
    m = meta(cfg, tid)
    assert m["agentes_copiados"] == [] and not (Path(m["worktree"]) / ".claude").exists()


def test_repo_com_o_mesmo_caminho_ganha_e_fica_aviso(ctx):
    cfg, path, _ = ctx
    squad(cfg)
    repo = officecfg.raiz(cfg, "projectos") / "exemplo"
    (repo / ".claude" / "agents").mkdir(parents=True)
    (repo / ".claude" / "agents" / "revisor.md").write_text("do repo\n", "utf-8")
    c.git(["add", "."], repo)
    c.git(["commit", "-q", "-m", "agente do repo"], repo)
    tid = preparar(cfg, path)
    m = meta(cfg, tid)
    wt = Path(m["worktree"])
    assert (wt / ".claude" / "agents" / "revisor.md").read_text("utf-8") == "do repo\n"
    aviso = "o repo já tem .claude/agents/revisor.md: ficou o do repo"
    assert m["avisos_agentes"] == [aviso] and ".claude/agents/revisor.md" not in m["agentes_copiados"]
    assert c.git(["status", "--porcelain"], wt) == ""
    fim = lancador.correr(cfg, tid)
    assert fim["issues"][0] == aviso


def test_retoma_actualiza_so_as_copias_do_runner(ctx, monkeypatch):
    cfg, path, _ = ctx
    base = squad(cfg)
    tid = preparar(cfg, path)
    wt = Path(meta(cfg, tid)["worktree"])
    (wt / ".claude" / "agents" / "outro.md").write_text("do agente\n", "utf-8")   # não é cópia do runner
    novo = REVISOR.replace(b"model: haiku", b"model: opus")
    (base / "agents" / "revisor.md").write_bytes(novo)
    (base / "agents" / "outro.md").write_text("---\nname: outro\ndescription: O\n---\n", "utf-8")
    p = officecfg.caminho(cfg, "tasks") / tid
    m = meta(cfg, tid)
    m.update(session_id="11111111-2222-3333-4444-555555555555", pid=None)
    (p / "meta.json").write_text(json.dumps(m), "utf-8")
    lancador.guardar_sessao(cfg, tid, m["session_id"])
    t = time.time() - 600
    os.utime(p / "heartbeat", (t, t))
    linhas = office_runner.uma_vez(cfg, str(path))
    assert any("retomada" in x for x in linhas)
    assert (wt / ".claude" / "agents" / "revisor.md").read_bytes() == novo
    assert (wt / ".claude" / "agents" / "outro.md").read_text("utf-8") == "do agente\n"
    assert meta(cfg, tid)["avisos_agentes"] == ["o repo já tem .claude/agents/outro.md: ficou o do repo"]


def _morta(cfg, tid, **campos):
    p = officecfg.caminho(cfg, "tasks") / tid
    m = meta(cfg, tid)
    m.update(session_id="11111111-2222-3333-4444-555555555555", pid=None, **campos)
    (p / "meta.json").write_text(json.dumps(m), "utf-8")
    lancador.guardar_sessao(cfg, tid, "11111111-2222-3333-4444-555555555555")
    t = time.time() - 600
    os.utime(p / "heartbeat", (t, t))


def test_retoma_com_worktree_adulterado_nao_escreve_fora(ctx, tmp_path):
    cfg, path, _ = ctx
    squad(cfg)
    tid = preparar(cfg, path)
    alvo = tmp_path / "casa-falsa"
    alvo.mkdir()
    _morta(cfg, tid, worktree=str(alvo), squad="mkt", agentes_copiados=[".claude/CLAUDE.md"])
    linhas = office_runner.uma_vez(cfg, str(path))
    assert f"tarefa {tid} não retomada: a meta da tarefa não bate com o runner" in linhas
    assert list(alvo.iterdir()) == [] and meta(cfg, tid)["status"] == "erro"


def test_retoma_ignora_agentes_copiados_da_meta(ctx):
    cfg, path, _ = ctx
    squad(cfg)
    repo = officecfg.raiz(cfg, "projectos") / "exemplo"
    (repo / ".claude").mkdir()
    (repo / ".claude" / "CLAUDE.md").write_text("do repo\n", "utf-8")
    c.git(["add", "."], repo)
    c.git(["commit", "-q", "-m", "claude do repo"], repo)
    tid = preparar(cfg, path)
    wt = Path(meta(cfg, tid)["worktree"])
    _morta(cfg, tid, agentes_copiados=[".claude/CLAUDE.md"])   # o agente diz que a cópia é do runner
    office_runner.uma_vez(cfg, str(path))
    assert (wt / ".claude" / "CLAUDE.md").read_text("utf-8") == "do repo\n"


def test_copia_que_o_gitignore_nao_ignora_e_apagada(ctx):
    cfg, path, _ = ctx
    squad(cfg)
    repo = officecfg.raiz(cfg, "projectos") / "exemplo"
    (repo / ".gitignore").write_text("!/.claude/agents/revisor.md\n", "utf-8")
    c.git(["add", ".gitignore"], repo)
    c.git(["commit", "-q", "-m", "gitignore"], repo)
    tid = preparar(cfg, path)
    m = meta(cfg, tid)
    wt = Path(m["worktree"])
    assert not (wt / ".claude" / "agents" / "revisor.md").exists()
    assert m["avisos_agentes"] == ["o .gitignore do repo não deixa ignorar .claude/agents/revisor.md: cópia apagada"]
    assert ".claude/agents/revisor.md" not in m["agentes_copiados"] and c.git(["status", "--porcelain"], wt) == ""


def test_claude_symlink_no_repo_nada_copiado(ctx, tmp_path):
    cfg, _, _ = ctx
    squad(cfg)
    wt = tmp_path / "wt"
    (wt / "fora").mkdir(parents=True)
    (wt / ".claude").symlink_to(wt / "fora")
    copiados, avisos = agentes_copia.copiar(cfg, "dev", wt, [])
    assert copiados == [] and avisos and "symlink" in avisos[0]
    assert list((wt / "fora").iterdir()) == []


def test_pastas_symlink_nos_dados_nao_contam(ctx, tmp_path):
    cfg, _, _ = ctx
    fora = tmp_path / "fora"
    (fora / "agents").mkdir(parents=True)
    (fora / "agents" / "x.md").write_text("---\nname: x\ndescription: X\n---\n", "utf-8")
    (officecfg.caminho(cfg, "agentes") / "dev").symlink_to(fora)
    assert agentes_copia.fontes(officecfg.caminho(cfg, "agentes") / "dev") == []
    base = officecfg.caminho(cfg, "agentes") / "ops"
    (base / "agents").mkdir(parents=True)
    (base / "agents" / "ligado.md").symlink_to(fora / "agents" / "x.md")
    (base / "skills").symlink_to(fora)
    assert agentes_copia.fontes(base) == []


def test_tecto_de_2_mb(ctx, tmp_path, monkeypatch):
    cfg, _, _ = ctx
    squad(cfg)
    monkeypatch.setattr(agentes_copia, "TOTAL_MAX", len(REVISOR) + 5)
    (tmp_path / "wt").mkdir()
    copiados, avisos = agentes_copia.copiar(cfg, "dev", tmp_path / "wt", [])
    assert copiados == [".claude/agents/revisor.md"] and "2 MB" in avisos[0]


def test_squad_invalida_nao_copia(ctx, tmp_path):
    cfg, _, _ = ctx
    assert agentes_copia.copiar(cfg, "../x", tmp_path, []) == ([], [])
    assert agentes_copia.copiar(cfg, None, tmp_path, ["a"]) == ([], [])


# ---------------------------------------------------------------- argv: squads[].modelo e tecto da rotina

def _cfg(**squad) -> dict:
    return officecfg.validar({"squads": [{"id": "dev", "nome": "Dev", **squad}, {"id": "ops", "nome": "Ops"}],
                              "projectos": [{"id": "exemplo", "nome": "Exemplo"}],
                              "runner": {"modelo": "sonnet", "max_usd": "3"}})


def test_modelo_da_squad_ganha_ao_do_runner():
    argv = lancador.comando(_cfg(modelo="haiku"), {"workdir": "/w", "squad": "dev"}, "claude", None, "s")
    assert argv[argv.index("--model") + 1] == "haiku" and argv.count("--model") == 1
    argv = lancador.comando(_cfg(modelo="haiku"), {"workdir": "/w", "squad": "ops"}, "claude", None, "s")
    assert argv[argv.index("--model") + 1] == "sonnet"
    argv = lancador.comando(_cfg(), {"workdir": "/w", "squad": "dev"}, "claude", None, "s")
    assert argv[argv.index("--model") + 1] == "sonnet"


TID = "20261118-1000-rotina-dev-r1-20261118"


def test_tecto_e_turnos_da_rotina_vem_do_estado_do_runner(monkeypatch):
    est = {"squad": "dev", "rotina": "dev/r1", "max_usd": "0.50", "max_turnos": 10}
    monkeypatch.setattr(lancador, "ler_estado", lambda cfg, tid: est if tid == TID else {})
    argv = lancador.comando(_cfg(), {"workdir": "/w", "id": TID}, "claude", None, "s")
    assert argv[argv.index("--max-budget-usd") + 1] == "0.50" and argv[argv.index("--max-turns") + 1] == "10"
    assert argv.index("--max-turns") < argv.index("--disallowedTools")


def test_meta_adulterada_nao_sobe_o_tecto(monkeypatch):
    # SEC1: o agente escreve a meta.json; sem estado de rotina no runner vale o runner.max_usd
    meta = {"workdir": "/w", "id": TID, "squad": "dev", "rotina": "dev/r1", "max_usd": "5.00", "max_turnos": 30}
    argv = lancador.comando(_cfg(), meta, "claude", None, "s")
    assert argv[argv.index("--max-budget-usd") + 1] == "3" and "--max-turns" not in argv


@pytest.mark.parametrize("usd, turnos", [("9.99", 31), ("abc", 0), (5, "10"), ("0.09", True), (None, None)])
def test_tecto_da_rotina_fora_dos_limites_e_ignorado(monkeypatch, usd, turnos):
    est = {"squad": "dev", "rotina": "dev/r1", "max_usd": usd, "max_turnos": turnos}
    monkeypatch.setattr(lancador, "ler_estado", lambda cfg, tid: est)
    argv = lancador.comando(_cfg(), {"workdir": "/w", "id": TID}, "claude", None, "s")
    assert argv[argv.index("--max-budget-usd") + 1] == "3" and "--max-turns" not in argv


@pytest.mark.parametrize("campo", ["workdir", "worktree"])
def test_correr_com_meta_adulterada_nao_corre(ctx, tmp_path, campo):
    cfg, path, _ = ctx
    tid = preparar(cfg, path)
    p = officecfg.caminho(cfg, "tasks") / tid / "meta.json"
    m = meta(cfg, tid)
    m[campo] = str(tmp_path / "outro")
    p.write_text(json.dumps(m), "utf-8")
    fim = lancador.correr(cfg, tid)
    assert fim["status"] == "erro" and fim["result"] == "a meta da tarefa não bate com o runner"
    assert not (tmp_path / "argv.jsonl").exists() and not (tmp_path / "outro").exists()


def test_tarefa_antiga_sem_estado_com_worktree_adulterado_nao_retoma(ctx, tmp_path):
    cfg, path, _ = ctx
    tid = preparar(cfg, path)
    (officecfg.caminho(cfg, "runner") / "tarefas" / f"{tid}.json").unlink()   # como antes da 0.4.0
    alvo = tmp_path / "casa-falsa"
    alvo.mkdir()
    _morta(cfg, tid, worktree=str(alvo))
    linhas = office_runner.uma_vez(cfg, str(path))
    assert f"tarefa {tid} não retomada: a meta da tarefa não bate com o runner" in linhas


def test_session_id_vem_do_estado(ctx, monkeypatch):
    cfg, path, _ = ctx
    tid = preparar(cfg, path)
    lancador.correr(cfg, tid)   # o claude falso escreve init: o session_id vai para o estado
    sid = lancador.ler_estado(cfg, tid)["session_id"]
    assert sid == meta(cfg, tid)["session_id"]
    m = meta(cfg, tid)
    m.update(session_id="outra-sessao", status="a correr")
    (officecfg.caminho(cfg, "tasks") / tid / "meta.json").write_text(json.dumps(m), "utf-8")
    assert lancador.sessao_de(cfg, tid, m) == sid


def test_estado_das_tarefas_rodado_com_as_settings(ctx):
    cfg, path, _ = ctx
    tid = preparar(cfg, path)
    lancador.correr(cfg, tid)
    est = officecfg.caminho(cfg, "runner") / "tarefas" / f"{tid}.json"
    assert est.exists()
    office_runner.limpar_settings(cfg, time.time() - 3600)
    assert est.exists()
    office_runner.limpar_settings(cfg, time.time() + 3600)
    assert est.exists()   # o worktree ainda existe: a limpeza (ADR-025) precisa do estado
    m = json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text(encoding="utf-8"))
    shutil.rmtree(m["worktree"])
    office_runner.limpar_settings(cfg, time.time() + 3600)
    assert not est.exists()
