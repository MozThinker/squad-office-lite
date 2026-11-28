"""Runner: limpeza diária das tarefas com PR fundido (ADR-025). `git` real em pastas temporárias (remoto nu local),
`gh` falso que devolve o JSON de FAKE_GH_SAIDA e regista o argv; nada de rede."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner, officecfg

sys.path.insert(0, str(c.RAIZ / "runner"))
import limpeza  # noqa: E402

GH_FALSO = f"""#!{sys.executable}
import json, os, sys
open(os.environ["FAKE_GH_ARGV"], "a").write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ["FAKE_GH_RC_VIEW"])) if os.environ.get("FAKE_GH_RC_VIEW") else None
print(os.environ.get("FAKE_GH_SAIDA", "[]"))
"""
PR = "https://github.com/dono/exemplo/pull/7"
NOW = time.time()


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text(GH_FALSO, encoding="utf-8")
    (bin_ / "gh").chmod(0o755)
    c.ambiente(monkeypatch, tmp_path, extra_bin=bin_)
    monkeypatch.setenv("FAKE_GH_ARGV", str(tmp_path / "gh.jsonl"))
    cfg, path = c.config(tmp_path)
    origem = c.repo(cfg, "origem")
    nu = tmp_path / "remoto.git"
    c.git(["clone", "-q", "--bare", str(origem), str(nu)], tmp_path)
    c.git(["clone", "-q", str(nu), str(officecfg.raiz(cfg, "projectos") / "exemplo")], tmp_path)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 1)
    return cfg, path, tmp_path


def tarefa(cfg, slug="botao-novo", dias=1, status="concluída", push=True, minuto=0):
    """Tarefa terminada há `dias`, com um commit no branch (enviado com `push`) e uma transcrição. → (tid, meta)."""
    p = {"id": None, "squad": "dev", "projecto": "exemplo", "kind": "feat", "mode": "task", "slug": slug,
         "titulo": "T", "brief": "B"}
    tid = lancador.preparar(cfg, p, NOW - dias * 86400 + minuto * 60)
    mp = officecfg.caminho(cfg, "tasks") / tid / "meta.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    wt = Path(m["worktree"])
    (wt / "novo.txt").write_text("x\n", encoding="utf-8")
    c.git(["add", "novo.txt"], wt)
    c.git(["commit", "-q", "-m", "feat: novo"], wt)
    if push:
        c.git(["push", "-q", "-u", "origin", m["branch"]], wt)
    fim = datetime.fromtimestamp(NOW - dias * 86400 + 3600, timezone.utc).isoformat(timespec="seconds")
    m.update(status=status, finished_at=fim, pr=f"{PR} (open)", pr_url=PR)
    lancador.escrever_json(mp, m)
    tr = officecfg.raiz(cfg, "claude_projects") / m["claude_dir"]
    tr.mkdir(parents=True)
    (tr / "sessao.jsonl").write_text("{}\n", encoding="utf-8")
    velho = NOW - dias * 86400 + 3600   # o heartbeat (só o runner o toca) parou quando a tarefa acabou
    os.utime(mp.parent / "heartbeat", (velho, velho))
    return tid, m


def head(m) -> str:
    return c.git(["rev-parse", "HEAD"], m["worktree"])


def pr_json(m, state="MERGED", oid=None, url=PR, criado=None, fork=False, dono="dono"):
    criado = criado or datetime.fromtimestamp(NOW, timezone.utc).isoformat().replace("+00:00", "Z")
    return {"url": url, "state": state, "headRefName": m["branch"], "headRefOid": oid or head(m), "createdAt": criado,
            "headRepositoryOwner": {"login": dono}, "isCrossRepository": fork}


def gh_diz(monkeypatch, m, state="MERGED", oid=None, url=PR):
    monkeypatch.setenv("FAKE_GH_SAIDA", json.dumps([pr_json(m, state, oid, url)]))


def meta(cfg, tid):
    return json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text(encoding="utf-8"))


def branches(cfg) -> str:
    return c.git(["branch", "--list"], officecfg.raiz(cfg, "projectos") / "exemplo")


def gh_chamadas(tmp) -> list:
    f = tmp / "gh.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def test_pr_fundido_limpo_arquiva_tudo(ctx, monkeypatch):
    cfg, path, tmp = ctx
    tid, m = tarefa(cfg)
    (Path(m["worktree"]) / "node_modules").mkdir()   # não seguidos não contam como sujo (e saem com o worktree)
    gh_diz(monkeypatch, m)
    linhas = office_runner.uma_vez(cfg, str(path))
    assert f"limpeza: tarefa {tid} arquivada (PR fundido; removido: worktree, branch, transcrição)" in linhas
    assert not Path(m["worktree"]).exists() and "feat/botao-novo" not in branches(cfg)
    assert not (officecfg.raiz(cfg, "claude_projects") / m["claude_dir"]).exists()
    n = meta(cfg, tid)
    assert n["status"] == "arquivada" and n["pr"] == f"{PR} (merged)" and n["arquivada_em"]
    assert gh_chamadas(tmp)[-1] == ["pr", "list", "--head", "feat/botao-novo", "--state", "all", "--json",
                                    "url,state,headRefName,headRefOid,createdAt,headRepositoryOwner,isCrossRepository"]
    # a meta arquivada já não é vista: o gh não volta a ser chamado
    n_gh = len(gh_chamadas(tmp))
    assert [x for x in limpeza.processar(cfg, NOW + 86400) if "limpeza" in x] == []
    assert len(gh_chamadas(tmp)) == n_gh


def test_squash_head_antecessor_do_ultimo_commit_do_pr(ctx, monkeypatch):
    # o PR teve mais um commit (feito no GitHub) e foi fundido por squash: o HEAD local é antecessor do último
    cfg, _, tmp = ctx
    tid, m = tarefa(cfg)
    outro = tmp / "outro"
    c.git(["clone", "-q", "-b", m["branch"], str(tmp / "remoto.git"), str(outro)], tmp)
    (outro / "mais.txt").write_text("y\n", encoding="utf-8")
    c.git(["add", "mais.txt"], outro)
    c.git(["commit", "-q", "-m", "fix: revisão"], outro)
    c.git(["push", "-q", "origin", m["branch"]], outro)
    c.git(["fetch", "-q", "origin"], m["worktree"])
    gh_diz(monkeypatch, m, oid=c.git(["rev-parse", "HEAD"], outro))
    linhas = limpeza.processar(cfg, NOW)
    assert any(f"tarefa {tid} arquivada" in x for x in linhas) and meta(cfg, tid)["status"] == "arquivada"


@pytest.mark.parametrize("caso", ["sujo", "commit-local", "fechado", "sem-pr", "aberto", "commit-desconhecido"])
def test_ficam_e_avisam_depois_de_7_dias(ctx, monkeypatch, caso):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=8)
    wt = Path(m["worktree"])
    motivo = {"sujo": "alterações por commitar", "commit-local": "commits locais que não estão no PR",
              "fechado": "PR fechado sem merge", "sem-pr": "sem PR", "aberto": None,
              "commit-desconhecido": "o último commit do PR não está no repositório local"}[caso]
    gh_diz(monkeypatch, m, state="CLOSED" if caso == "fechado" else "OPEN" if caso == "aberto" else "MERGED",
           oid="0" * 40 if caso == "commit-desconhecido" else None)
    if caso == "sujo":
        (wt / "README.md").write_text("mudado\n", encoding="utf-8")
    if caso == "commit-local":
        (wt / "depois.txt").write_text("z\n", encoding="utf-8")
        c.git(["add", "depois.txt"], wt)
        c.git(["commit", "-q", "-m", "feat: depois do merge"], wt)
    if caso == "sem-pr":
        monkeypatch.setenv("FAKE_GH_SAIDA", "[]")
    linhas = limpeza.processar(cfg, NOW)
    assert wt.is_dir() and "feat/botao-novo" in branches(cfg) and meta(cfg, tid)["status"] == "concluída"
    assert (officecfg.raiz(cfg, "claude_projects") / m["claude_dir"]).is_dir()
    if motivo is None:
        assert linhas == []
    else:
        assert linhas == [f"limpeza: 1 tarefa(s) parada(s) há mais de 7 dias ficam por limpar: {tid} ({motivo})"]
    assert limpeza.processar(cfg, NOW + 60) == []   # uma vez por dia
    assert len(limpeza.processar(cfg, NOW + 86400)) == (0 if motivo is None else 1)   # no dia seguinte, de novo


def test_parada_ha_menos_de_7_dias_fica_calada(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=2)
    gh_diz(monkeypatch, m, state="CLOSED")
    assert limpeza.processar(cfg, NOW) == []
    assert Path(m["worktree"]).is_dir()


@pytest.mark.parametrize("status", ["a correr", "a preparar"])
def test_tarefa_activa_nunca_e_tocada(ctx, monkeypatch, status):
    cfg, _, tmp = ctx
    tid, m = tarefa(cfg, status=status)
    gh_diz(monkeypatch, m)
    assert limpeza.processar(cfg, NOW) == [] and gh_chamadas(tmp) == []
    assert Path(m["worktree"]).is_dir() and meta(cfg, tid)["status"] == status


def test_pid_vivo_nao_e_tocada(ctx, monkeypatch):
    cfg, _, tmp = ctx
    tid, m = tarefa(cfg)
    m["pid"] = os.getpid()
    lancador.escrever_json(officecfg.caminho(cfg, "tasks") / tid / "meta.json", m)
    gh_diz(monkeypatch, m)
    assert limpeza.processar(cfg, NOW) == [] and gh_chamadas(tmp) == []
    assert Path(m["worktree"]).is_dir()


def test_transcricao_com_memory_fica(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg)
    tr = officecfg.raiz(cfg, "claude_projects") / m["claude_dir"]
    (tr / "memory").mkdir()
    gh_diz(monkeypatch, m)
    linhas = limpeza.processar(cfg, NOW)
    assert f"limpeza: tarefa {tid}: transcrição mantida (tem memory/)" in linhas
    assert f"limpeza: tarefa {tid} arquivada (PR fundido; removido: worktree, branch)" in linhas
    assert tr.is_dir() and not Path(m["worktree"]).exists()


def test_claude_dir_da_meta_mexido_nao_apaga_outra_pasta(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg)
    alheia = officecfg.raiz(cfg, "claude_projects") / "-srv-outro-projecto"
    alheia.mkdir()
    m["claude_dir"] = alheia.name
    lancador.escrever_json(officecfg.caminho(cfg, "tasks") / tid / "meta.json", m)
    gh_diz(monkeypatch, m)
    linhas = limpeza.processar(cfg, NOW)
    assert any("claude_dir da meta não bate" in x for x in linhas) and alheia.is_dir()


def test_meta_que_nao_bate_com_o_runner_fica(ctx, monkeypatch):
    cfg, _, tmp = ctx
    tid, m = tarefa(cfg)
    gh_diz(monkeypatch, m)
    m["worktree"] = str(tmp / "noutro-lado")
    lancador.escrever_json(officecfg.caminho(cfg, "tasks") / tid / "meta.json", m)
    assert limpeza.processar(cfg, NOW) == [f"limpeza: tarefa {tid} fica: {lancador.DIFERENTE}"]
    assert gh_chamadas(tmp) == []


def test_limite_de_perguntas_ao_gh_por_passagem(ctx, monkeypatch):
    cfg, _, tmp = ctx
    monkeypatch.setattr(limpeza, "GH_MAX", 2)
    tids = [tarefa(cfg, slug=f"t-{i}", minuto=i)[0] for i in range(3)]
    monkeypatch.setenv("FAKE_GH_SAIDA", "[]")
    limpeza.processar(cfg, NOW)
    assert len(gh_chamadas(tmp)) == 2
    limpeza.processar(cfg, NOW + 60)   # a que faltou fica para a passagem seguinte
    assert len(gh_chamadas(tmp)) == 3
    limpeza.processar(cfg, NOW + 120)
    assert len(gh_chamadas(tmp)) == 3
    assert len(tids) == 3


def test_falhas_aparecem_no_journal(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg)
    monkeypatch.setenv("FAKE_GH_RC_VIEW", "1")   # o gh falha
    assert limpeza.processar(cfg, NOW) == [f"limpeza: tarefa {tid}: gh pr list falhou"]
    # worktree remove que falha (worktree trancado): nada mais é apagado
    monkeypatch.delenv("FAKE_GH_RC_VIEW")
    gh_diz(monkeypatch, m)
    c.git(["worktree", "lock", m["worktree"]], officecfg.raiz(cfg, "projectos") / "exemplo")
    linhas = limpeza.processar(cfg, NOW + 86400)
    assert linhas == [f"limpeza: tarefa {tid}: git worktree remove falhou; nada mais foi apagado"]
    assert "feat/botao-novo" in branches(cfg) and meta(cfg, tid)["status"] == "concluída"
    # o merge já ficou guardado: no dia seguinte não se pergunta outra vez ao gh
    estado = json.loads((officecfg.caminho(cfg, "runner") / "limpeza.json").read_text(encoding="utf-8"))
    assert estado["fundidos"][tid]["head"] == head(m)


def test_sem_gh_uma_linha(ctx, monkeypatch):
    cfg, _, tmp = ctx
    tarefa(cfg)
    monkeypatch.setattr(limpeza.shutil, "which", lambda _n: None)
    assert limpeza.processar(cfg, NOW) == ["limpeza: gh não encontrado; estado dos PRs por confirmar"]


def test_repo_sem_remoto_fica_sem_pr(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, _ = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 1)
    tid, m = tarefa(cfg, dias=9, push=False)
    assert limpeza.processar(cfg, NOW) == [
        f"limpeza: 1 tarefa(s) parada(s) há mais de 7 dias ficam por limpar: {tid} (sem PR (repositório sem remoto))"]
    assert Path(m["worktree"]).is_dir()


def test_estado_ilegivel_recomeca(ctx, monkeypatch):
    cfg, _, _ = ctx
    (officecfg.caminho(cfg, "runner") / "limpeza.json").write_text("{nada", encoding="utf-8")
    tid, m = tarefa(cfg)
    gh_diz(monkeypatch, m)
    assert any("arquivada" in x for x in limpeza.processar(cfg, NOW))


def test_data_sem_fuso_no_finished_at_nao_rebenta(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=10)
    m["finished_at"] = (datetime.now() - timedelta(days=10)).isoformat()   # sem fuso: hora local
    lancador.escrever_json(officecfg.caminho(cfg, "tasks") / tid / "meta.json", m)
    gh_diz(monkeypatch, m, state="CLOSED")
    assert len(limpeza.processar(cfg, NOW)) == 1
    assert subprocess.run(["git", "-C", m["worktree"], "status"], capture_output=True).returncode == 0


# ---------------------------------------------------------------- ronda 2 (revisão do PR #41)

def _aviso(tid, motivo):
    return f"limpeza: 1 tarefa(s) parada(s) há mais de 7 dias ficam por limpar: {tid} ({motivo})"


def test_ficheiro_novo_nao_adicionado_trava(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=8)
    wt = Path(m["worktree"])
    (wt / "relatorio_novo.py").write_text("print(1)\n", encoding="utf-8")
    gh_diz(monkeypatch, m)
    assert limpeza.processar(cfg, NOW) == [_aviso(tid, "1 ficheiro(s) novo(s) por adicionar")]
    assert (wt / "relatorio_novo.py").is_file() and meta(cfg, tid)["status"] == "concluída"


def test_ficheiro_novo_dentro_de_pasta_nova_trava(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=8)
    wt = Path(m["worktree"])
    (wt / "app" / "node_modules" / "x").mkdir(parents=True)
    (wt / "app" / "node_modules" / "x" / "i.js").write_text("1\n", encoding="utf-8")
    (wt / "app" / "novo.js").write_text("2\n", encoding="utf-8")
    gh_diz(monkeypatch, m)
    assert limpeza.processar(cfg, NOW) == [_aviso(tid, "1 ficheiro(s) novo(s) por adicionar")]


def test_dependencias_ignorados_e_copias_do_runner_saem(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg)
    wt = Path(m["worktree"])
    for pasta in ("node_modules/a", "web/node_modules/b", "venv-3/lib", "__pycache__", ".pytest_cache", ".venv/bin"):
        (wt / pasta).mkdir(parents=True)
        (wt / pasta / "f").write_text("x\n", encoding="utf-8")
    (wt / "venv-3" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    excl = Path(c.git(["rev-parse", "--git-path", "info/exclude"], wt))
    excl = excl if excl.is_absolute() else wt / excl
    excl.write_text(excl.read_text(encoding="utf-8") + "/dist/\n", encoding="utf-8")
    (wt / "dist").mkdir()
    (wt / "dist" / "app.js").write_text("x\n", encoding="utf-8")   # ignorado: sai com o worktree
    (wt / ".claude" / "agents").mkdir(parents=True)
    (wt / ".claude" / "agents" / "revisor.md").write_text("# a\n", encoding="utf-8")   # cópia do runner
    est = lancador.ler_estado(cfg, tid)
    est["agentes_copiados"] = [".claude/agents/revisor.md"]
    lancador.gravar_estado(cfg, tid, est)
    gh_diz(monkeypatch, m)
    linhas = limpeza.processar(cfg, NOW)
    assert any(f"tarefa {tid} arquivada" in x for x in linhas), linhas
    assert not wt.exists()


def test_venv_sem_pyvenv_cfg_trava(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=8)
    (Path(m["worktree"]) / "venvs").mkdir()
    (Path(m["worktree"]) / "venvs" / "notas.txt").write_text("x\n", encoding="utf-8")
    gh_diz(monkeypatch, m)
    assert limpeza.processar(cfg, NOW) == [_aviso(tid, "1 ficheiro(s) novo(s) por adicionar")]


def test_sem_estado_do_runner_fica_e_avisa(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=8)
    (officecfg.caminho(cfg, "runner") / "tarefas" / f"{tid}.json").unlink()
    gh_diz(monkeypatch, m)
    assert limpeza.processar(cfg, NOW) == [_aviso(tid, "sem o estado do runner")]
    assert Path(m["worktree"]).is_dir()


def test_branch_da_meta_diferente_do_estado_fica(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=8)
    est = lancador.ler_estado(cfg, tid)
    assert est["branch"] == "feat/botao-novo"
    est["branch"] = "feat/outro"
    lancador.gravar_estado(cfg, tid, est)
    gh_diz(monkeypatch, m)
    assert limpeza.processar(cfg, NOW) == [_aviso(tid, "o branch da meta não bate com o estado do runner")]


def test_meta_editada_com_heartbeat_fresco_fica(ctx, monkeypatch):
    # o agente escreve «concluída» e pid null na meta, mas o heartbeat (só o runner o toca) está fresco
    cfg, _, tmp = ctx
    tid, m = tarefa(cfg)
    os.utime(officecfg.caminho(cfg, "tasks") / tid / "heartbeat")
    gh_diz(monkeypatch, m)
    assert limpeza.processar(cfg, time.time()) == [] and gh_chamadas(tmp) == []
    assert Path(m["worktree"]).is_dir()


def test_correr_vivo_com_heartbeat_parado_fica(ctx, monkeypatch):
    cfg, _, tmp = ctx
    tid, m = tarefa(cfg)
    vivo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(lancador.RUNNER), "correr", tid])
    try:
        gh_diz(monkeypatch, m)
        assert limpeza.processar(cfg, NOW) == [] and gh_chamadas(tmp) == []
        assert Path(m["worktree"]).is_dir()
    finally:
        vivo.kill()
        vivo.wait()


@pytest.mark.parametrize("caso", ["fork", "antigo", "outro-dono"])
def test_pr_que_nao_e_da_tarefa_nao_conta(ctx, monkeypatch, caso):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=8)
    m["pr_url"] = None
    lancador.escrever_json(officecfg.caminho(cfg, "tasks") / tid / "meta.json", m)
    monkeypatch.setattr(limpeza, "_origem", lambda _r: ("dono", "exemplo"))
    kw = {"fork": {"fork": True}, "antigo": {"criado": "2020-01-01T00:00:00Z"}, "outro-dono": {"dono": "alguem"}}
    monkeypatch.setenv("FAKE_GH_SAIDA", json.dumps([pr_json(m, url=PR.replace("7", "3"), **kw[caso])]))
    assert limpeza.processar(cfg, NOW) == [_aviso(tid, "sem PR")]
    assert Path(m["worktree"]).is_dir()


def test_pr_antigo_com_o_url_da_meta_conta(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg)
    monkeypatch.setenv("FAKE_GH_SAIDA", json.dumps([pr_json(m, criado="2020-01-01T00:00:00Z")]))
    assert any(f"tarefa {tid} arquivada" in x for x in limpeza.processar(cfg, NOW))


def test_branch_d_falhado_tenta_outra_vez(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg)
    gh_diz(monkeypatch, m)
    git_real = lancador.git
    monkeypatch.setattr(lancador, "git", lambda a, cwd: subprocess.CompletedProcess(a, 1, "", "")
                        if a[:2] == ["branch", "-D"] else git_real(a, cwd))
    assert limpeza.processar(cfg, NOW) == [f"limpeza: tarefa {tid}: git branch -D feat/botao-novo falhou; "
                                           "tenta outra vez amanhã"]
    assert not Path(m["worktree"]).exists() and meta(cfg, tid)["status"] == "concluída"
    monkeypatch.setattr(lancador, "git", git_real)
    monkeypatch.setenv("FAKE_GH_RC_VIEW", "1")   # já não precisa do gh: o merge ficou guardado
    assert limpeza.processar(cfg, NOW + 86400) == [
        f"limpeza: tarefa {tid} arquivada (PR fundido; removido: branch, transcrição)"]
    assert "feat/botao-novo" not in branches(cfg) and meta(cfg, tid)["status"] == "arquivada"


def test_meta_nao_gravada_arquiva_no_dia_seguinte(ctx, monkeypatch):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg)
    gh_diz(monkeypatch, m)
    real = lancador.escrever_json

    def rebenta(path, doc):
        if doc.get("status") == "arquivada":
            raise PermissionError("x")
        real(path, doc)

    monkeypatch.setattr(lancador, "escrever_json", rebenta)
    assert limpeza.processar(cfg, NOW) == [
        f"limpeza: tarefa {tid}: meta não gravada como arquivada (PermissionError); tenta outra vez amanhã"]
    monkeypatch.setattr(lancador, "escrever_json", real)
    assert limpeza.processar(cfg, NOW + 86400) == [f"limpeza: tarefa {tid} arquivada (PR fundido; removido: nada)"]
    assert meta(cfg, tid)["status"] == "arquivada" and meta(cfg, tid)["pr"] == f"{PR} (merged)"


def test_worktree_apagado_a_mao_sem_merge_conferido_fica(ctx, monkeypatch):
    cfg, _, tmp = ctx
    tid, m = tarefa(cfg)
    gh_diz(monkeypatch, m)
    c.git(["worktree", "remove", "--force", m["worktree"]], officecfg.raiz(cfg, "projectos") / "exemplo")
    assert limpeza.processar(cfg, NOW) == [] and gh_chamadas(tmp) == []
    assert meta(cfg, tid)["status"] == "concluída"


def test_avisar_no_maximo_um_por_dia(ctx, monkeypatch):
    cfg, _, _ = ctx
    avisos = []
    monkeypatch.setattr(lancador, "avisar", avisos.append)
    tid, m = tarefa(cfg, dias=8)
    gh_diz(monkeypatch, m, state="CLOSED")
    limpeza.processar(cfg, NOW)
    tarefa(cfg, slug="outra", dias=8, minuto=1)
    limpeza.processar(cfg, NOW + 60)
    assert avisos == ["Squad Office: limpeza das worktrees — 1 tarefa(s) parada(s) há mais de 7 dias por limpar "
                      "(ver o journal do runner)."]
    limpeza.processar(cfg, NOW + 86400)
    assert len(avisos) == 2


def test_rotacao_nao_apaga_o_estado_de_tarefa_com_worktree(ctx):
    cfg, _, _ = ctx
    tid, m = tarefa(cfg, dias=30)
    estado = officecfg.caminho(cfg, "runner") / "tarefas" / f"{tid}.json"
    office_runner.limpar_settings(cfg, NOW)
    assert estado.exists()
    c.git(["worktree", "remove", "--force", m["worktree"]], officecfg.raiz(cfg, "projectos") / "exemplo")
    office_runner.limpar_settings(cfg, NOW)
    assert not estado.exists()
