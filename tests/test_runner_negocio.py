"""Perfil de negócio no lançador (ADR-020, D5): argv só de leitura e prompt sem entrega por git (CA-3), mensagens do
serviço `accoes` copiadas para o worktree (CA-4), squads fora do registo ou de outro modelo como antes (CA-25) e o
gancho das acções propostas (I2; falha só nos issues). `claude` falso de tests/bin, repos locais."""
import json
import os
import sys
import time
import types

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import agentes_copia, lancador, office_runner, officecfg

TOKEN_GH = "ghp_" + "Q" * 36


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(tmp_path / "argv.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_STDIN", str(tmp_path / "stdin.txt"))
    return cfg, path, tmp_path


def registar(cfg, modelo="atendimento", sq="dev") -> None:
    base = officecfg.caminho(cfg, "agentes")
    base.mkdir(parents=True, exist_ok=True)
    (base / "squads.json").write_text(json.dumps({"v": 1, "squads": [
        {"id": sq, "modelo": modelo, "nome": "Dev", "lead": "responsavel", "criada_em": "2026-11-18T10:00:00+00:00"}]}),
        "utf-8")


def mensagens(cfg, n, sq="dev", desde=0) -> list[str]:
    pasta = officecfg.raiz(cfg, "dados") / agentes_copia.ACCOES_ENTRADA / sq / "mensagens"
    pasta.mkdir(parents=True, exist_ok=True)
    nomes = []
    for i in range(desde, desde + n):
        nome = f"m{i:016x}.txt"
        (pasta / nome).write_text(f"m{i:016x}\nDe: cliente{i}@exemplo.invalid\nAssunto: Dúvida {i}\n\nOlá.\n", "utf-8")
        t = 1_700_000_000 + i * 60   # mais antiga = menor i
        os.utime(pasta / nome, (t, t))
        nomes.append(nome)
    return nomes


def preparar(cfg, path, stem="20261106T120000-0a1b2c3d", **kw) -> str:
    c.pedido(cfg, stem, **kw)
    office_runner.uma_vez(cfg, str(path))
    return c.feito(cfg, stem)["task_id"]


def argvs(tmp) -> list:
    return [json.loads(x) for x in (tmp / "argv.jsonl").read_text(encoding="utf-8").splitlines()]


def wt_de(cfg, tid):
    return json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text("utf-8"))["worktree"]


# ---------------------------------------------------------------- CA-3

@pytest.mark.parametrize("modelo,web", [("atendimento", []), ("comercial", ["WebSearch"])])
def test_ca3_argv_so_leitura_ignora_runner(ctx, modelo, web):
    cfg, path, tmp = ctx
    assert cfg["runner"]["permission_mode"] == "auto" and cfg["runner"]["ferramentas"]
    registar(cfg, modelo)
    cfg["runner"]["max_usd"] = "2.5"
    cfg["runner"]["modelo"] = "sonnet"
    m = lancador.correr(cfg, preparar(cfg, path))
    (argv,) = argvs(tmp)
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    i, j = argv.index("--allowedTools"), argv.index("--disallowedTools")
    assert argv[i + 1:j] == ["Read", "Grep", "Glob", "Agent", *web]
    # C4: sem WebSearch no allow, nega-o (um allow das settings não o dá); o variádico continua no fim
    assert argv[j + 1:] == ["Bash", "Edit", "Write", "WebFetch", "NotebookEdit", "mcp__*",
                            *([] if web else ["WebSearch"])]
    assert any(x.startswith("Bash(") for x in cfg["runner"]["ferramentas"]) and not any(
        x.startswith("Bash(") for x in argv)
    assert "--settings" in argv and argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--max-budget-usd") + 1] == "2.5"
    texto = (tmp / "stdin.txt").read_text("utf-8")
    assert "Põe um botão na página." in texto and "=====ACCAO=====" in texto
    for proibido in ("commit", "branch", "PR", "push", "gh ", "git "):
        assert proibido not in texto, proibido
    assert m["status"] == "concluída" and m["issues"] == []


def test_ca3_ambiente_do_claude_sem_gh_token(ctx, monkeypatch):
    monkeypatch.setattr(lancador, "SEGREDOS", {"GH_TOKEN": TOKEN_GH, "GITHUB_TOKEN": TOKEN_GH,
                                               "ANTHROPIC_API_KEY": "sk-ant-api03-" + "x" * 30})
    monkeypatch.setenv("GH_TOKEN", TOKEN_GH)
    env = lancador.env_claude(None)
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env and TOKEN_GH not in env.values()
    assert env["ANTHROPIC_API_KEY"].startswith("sk-ant-api03-")


def test_ca3_retoma_mantem_o_perfil(ctx):
    cfg, path, tmp = ctx
    registar(cfg)
    tid = preparar(cfg, path)
    lancador.guardar_sessao(cfg, tid, "11111111-2222-3333-4444-555555555555")
    lancador.correr(cfg, tid, retomar=True)
    (argv,) = argvs(tmp)
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk" and "--resume" in argv
    assert argv[argv.index("--disallowedTools") + 1:] == [*lancador.NEGOCIO_NEGADAS, "WebSearch"]


def test_ca3_entrega_sem_pr_mesmo_com_remoto(ctx, tmp_path):
    wt = tmp_path / "wt"
    c.git(["init", "-q", "-b", "feat/x", str(wt)], tmp_path)
    meta = {"worktree": str(wt), "branch": "feat/x", "repo": "git@github.com:o/r.git", "result": "x"}
    assert lancador.entrega(meta, True) == ("concluída", None, None, [])
    assert lancador.entrega(meta)[0] == "incompleta"
    (wt / "a.txt").write_text("a", "utf-8")
    c.git(["add", "a.txt"], wt)
    assert lancador.entrega(meta, True)[0] == "incompleta"


# ---------------------------------------------------------------- CA-4

def test_ca4_cinco_mensagens_no_worktree_exclude_e_status_limpo(ctx):
    cfg, path, tmp = ctx
    registar(cfg)
    nomes = mensagens(cfg, 5)
    pasta = officecfg.raiz(cfg, "dados") / agentes_copia.ACCOES_ENTRADA / "dev" / "mensagens"
    (tmp / "fora.txt").write_text("segredo", "utf-8")
    (pasta / "m00000000000000ff.txt").symlink_to(tmp / "fora.txt")   # symlink: não entra
    (pasta / "outro.txt").write_text("x", "utf-8")                    # nome fora do padrão: não entra
    tid = preparar(cfg, path)
    wt = wt_de(cfg, tid)
    assert sorted(os.listdir(os.path.join(wt, "entrada", "mensagens"))) == nomes
    est = lancador.ler_estado(cfg, tid)
    assert est["mensagens_copiadas"] == [f"entrada/mensagens/{n}" for n in nomes]
    excl = c.git(["rev-parse", "--git-path", "info/exclude"], wt).strip()
    with open(os.path.join(wt, excl), encoding="utf-8") as f:
        texto = f.read()
    assert all(f"/entrada/mensagens/{n}" in texto.split("\n") for n in nomes)
    m = lancador.correr(cfg, tid)
    assert c.git(["status", "--porcelain"], wt) == "" and m["status"] == "concluída"
    entregues = json.loads((officecfg.caminho(cfg, "runner") / "accoes-entregues" / "dev.json").read_text("utf-8"))
    assert sorted(entregues) == [n[:-4] for n in nomes]


def test_ca4_no_maximo_20_as_mais_antigas_e_depois_as_seguintes(ctx):
    cfg, path, _ = ctx
    registar(cfg)
    nomes = mensagens(cfg, 25)
    tid = preparar(cfg, path)
    wt = wt_de(cfg, tid)
    assert sorted(os.listdir(os.path.join(wt, "entrada", "mensagens"))) == nomes[:20]
    lancador.correr(cfg, tid)   # paralelo 1: a seguinte só lança com esta terminada
    wt2 = wt_de(cfg, preparar(cfg, path, stem="20261106T120100-0a1b2c3e", slug="outro-botao"))
    assert sorted(os.listdir(os.path.join(wt2, "entrada", "mensagens"))) == nomes[20:]


def test_ca4_retoma_recopia_as_mesmas(ctx):
    cfg, path, _ = ctx
    registar(cfg)
    nomes = mensagens(cfg, 3)
    tid = preparar(cfg, path)
    wt = wt_de(cfg, tid)
    os.unlink(os.path.join(wt, "entrada", "mensagens", nomes[0]))
    mensagens(cfg, 2, desde=10)   # novas: ficam para a próxima tarefa
    est = lancador.ler_estado(cfg, tid)
    copiados, avisos = agentes_copia.copiar_mensagens(cfg, "dev", wt, est["mensagens_copiadas"])
    assert avisos == [] and copiados == [f"entrada/mensagens/{n}" for n in nomes]
    assert sorted(os.listdir(os.path.join(wt, "entrada", "mensagens"))) == nomes


def test_ca4_o_do_repo_ganha_e_registo_antigo_sai(ctx):
    cfg, path, _ = ctx
    registar(cfg)
    (nome,) = mensagens(cfg, 1)
    repo = officecfg.raiz(cfg, "projectos") / "exemplo"
    (repo / "entrada" / "mensagens").mkdir(parents=True)
    (repo / "entrada" / "mensagens" / nome).write_text("do repo", "utf-8")
    c.git(["add", "."], repo)
    c.git(["commit", "-q", "-m", "msg"], repo)
    reg = officecfg.caminho(cfg, "runner") / "accoes-entregues"
    reg.mkdir(parents=True)
    (reg / "dev.json").write_text(json.dumps({"m00000000000000aa": "2020-01-01T00:00:00+00:00"}), "utf-8")
    tid = preparar(cfg, path)
    wt = wt_de(cfg, tid)
    with open(os.path.join(wt, "entrada", "mensagens", nome), encoding="utf-8") as f:
        assert f.read() == "do repo"
    assert lancador.ler_estado(cfg, tid)["avisos"] == [f"o repo já tem entrada/mensagens/{nome}: ficou o do repo"]
    assert nome[:-4] not in json.loads((reg / "dev.json").read_text("utf-8"))   # o do repo não foi entregue
    agentes_copia.copiar_mensagens(cfg, "dev", wt, [])
    mensagens(cfg, 1, desde=7)
    agentes_copia.copiar_mensagens(cfg, "dev", wt, [])
    assert sorted(json.loads((reg / "dev.json").read_text("utf-8"))) == ["m0000000000000007"]   # o de 2020 saiu


def test_ca4_pasta_de_entrada_symlink_nada_copiado(ctx, tmp_path):
    cfg, path, _ = ctx
    registar(cfg)
    fora = tmp_path / "fora" / "mensagens"
    fora.mkdir(parents=True)
    (fora / "m0000000000000001.txt").write_text("x", "utf-8")
    entrada = officecfg.raiz(cfg, "dados") / agentes_copia.ACCOES_ENTRADA
    entrada.mkdir(parents=True)
    (entrada / "dev").symlink_to(tmp_path / "fora")
    wt = wt_de(cfg, preparar(cfg, path))
    assert not os.path.exists(os.path.join(wt, "entrada", "mensagens"))


# ---------------------------------------------------------------- C2: entrada/accoes.md

def test_c2_accoes_md_da_vista_sem_remetentes_nem_caixa(ctx):
    cfg, path, _ = ctx
    registar(cfg)
    pasta = officecfg.raiz(cfg, "dados") / agentes_copia.ACCOES_ENTRADA / "dev"
    pasta.mkdir(parents=True)
    (pasta / "vista.json").write_text(json.dumps({
        "v": 1, "ligadas": ["aviso_dono", "folha_linha"], "dominios": ["padaria.test"],
        "folhas": {"leads": ["nome", "nota"]}, "caixa": "loja@padaria.test",
        "mensagens": {"m0000000000000001": {"remetente": "cliente@exemplo.test", "autenticada": True}}}), "utf-8")
    tid = preparar(cfg, path)
    wt = wt_de(cfg, tid)
    with open(os.path.join(wt, "entrada", "accoes.md"), encoding="utf-8") as f:
        texto = f.read()
    assert "Acções ligadas: aviso_dono, folha_linha." in texto
    assert "Domínios permitidos nos links: padaria.test." in texto and "- leads: nome, nota" in texto
    assert "cliente@exemplo.test" not in texto and "loja@padaria.test" not in texto
    excl = c.git(["rev-parse", "--git-path", "info/exclude"], wt).strip()
    with open(os.path.join(wt, excl), encoding="utf-8") as f:
        assert "/entrada/accoes.md" in f.read().split("\n")
    m = lancador.correr(cfg, tid)
    assert c.git(["status", "--porcelain"], wt) == "" and m["status"] == "concluída"


def test_c2_sem_vista_omissao_e_o_do_repo_ganha(ctx, tmp_path):
    cfg, path, _ = ctx
    registar(cfg)
    wt = wt_de(cfg, preparar(cfg, path))
    with open(os.path.join(wt, "entrada", "accoes.md"), encoding="utf-8") as f:
        texto = f.read()
    assert "Acções ligadas: aviso_dono, email_rascunho, folha_linha." in texto
    assert "nenhum (não ponhas links)" in texto and "- nenhuma" in texto
    outro = tmp_path / "wt2"
    (outro / "entrada").mkdir(parents=True)
    (outro / "entrada" / "accoes.md").write_text("do repo", "utf-8")
    assert agentes_copia.copiar_accoes(cfg, "dev", outro) == ([], ["o repo já tem entrada/accoes.md: ficou o do repo"])
    assert (outro / "entrada" / "accoes.md").read_text("utf-8") == "do repo"


# ---------------------------------------------------------------- S2: leitura só no worktree

def test_s2_settings_negam_dados_transcricoes_e_outros_projectos(ctx, monkeypatch):
    cfg, path, tmp = ctx
    monkeypatch.setenv("FAKE_CLAUDE_SETTINGS", str(tmp / "settings.jsonl"))
    registar(cfg)
    tid = preparar(cfg, path)
    wt = wt_de(cfg, tid)
    os.makedirs(os.path.join(os.path.dirname(wt), "outra-tarefa"))
    lancador.correr(cfg, tid)
    (copia,) = [json.loads(x) for x in (tmp / "settings.jsonl").read_text("utf-8").splitlines()]
    deny = json.loads(copia["texto"])["permissions"]["deny"]
    def regra(p):
        return lancador._regra("Read", p)
    dados, projs = officecfg.raiz(cfg, "dados"), officecfg.raiz(cfg, "projectos")
    for r in (dados / "**", officecfg.raiz(cfg, "claude_projects") / "**", projs / "loja-web" / "**",
              os.path.join(os.path.dirname(wt), "outra-tarefa", "**")):
        assert regra(r) in deny, r
    assert not any(x.startswith(regra(wt)) or x in (regra(projs / "**"), regra(os.path.dirname(wt) + "/**"))
                   for x in deny)
    assert "Read(//proc/**)" in deny   # a base continua


def test_s2_worktree_dentro_dos_dados_nega_as_irmas(tmp_path):
    dados = tmp_path / "dados"
    wt = dados / "projectos" / "r" / ".worktrees" / "t1"
    wt.mkdir(parents=True)
    for p in (dados / "tasks", dados / "decisoes", dados / "projectos" / "outro", dados / "projectos" / "r" / "src"):
        p.mkdir(parents=True)
    regras = lancador._negar_leitura(dados, wt)
    def r(p):
        return lancador._regra("Read", p)
    for p in ("tasks", "decisoes", "projectos/outro", "projectos/r/src"):
        assert r(dados / p) in regras and r(dados / p / "**") in regras, p
    assert not any(x.startswith(r(dados / "projectos" / "r" / ".worktrees")) for x in regras)
    assert r(dados / "**") not in regras
    assert lancador._negar_leitura(tmp_path / "fora", wt) == [r(tmp_path / "fora" / "**")]


def test_s2_squad_normal_sem_negacoes_de_leitura(ctx, monkeypatch):
    cfg, path, tmp = ctx
    monkeypatch.setenv("FAKE_CLAUDE_SETTINGS", str(tmp / "settings.jsonl"))
    lancador.correr(cfg, preparar(cfg, path))
    (copia,) = [json.loads(x) for x in (tmp / "settings.jsonl").read_text("utf-8").splitlines()]
    deny = json.loads(copia["texto"])["permissions"]["deny"]
    assert f"Read(//{str(officecfg.raiz(cfg, 'dados')).lstrip('/')}/**)" not in deny


# ---------------------------------------------------------------- CA-25

@pytest.mark.parametrize("modelo", [None, "observacao"])
def test_ca25_fora_do_registo_ou_outro_modelo_argv_normal(ctx, modelo, monkeypatch):
    cfg, path, tmp = ctx
    if modelo:
        registar(cfg, modelo)
    mensagens(cfg, 2)
    chamadas = []
    monkeypatch.setitem(sys.modules, "accoes_propor", types.SimpleNamespace(
        propor=lambda *a: chamadas.append(a) or ["não devia"]))
    tid = preparar(cfg, path)
    m = lancador.correr(cfg, tid)
    (argv,) = argvs(tmp)
    assert argv[argv.index("--permission-mode") + 1] == "auto"
    assert argv[argv.index("--disallowedTools") + 1:] == lancador.DISALLOWED
    assert "Write" in argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
    assert "commit" in (tmp / "stdin.txt").read_text("utf-8")
    assert not os.path.exists(os.path.join(m["worktree"], "entrada")) and chamadas == []
    assert "mensagens_copiadas" in lancador.ler_estado(cfg, tid) and m["issues"] == []


# ---------------------------------------------------------------- gancho das acções (I2, CA-24)

BLOCO = '=====ACCAO=====\n{"tipo": "aviso_dono", "texto": "Há 2 reclamações."}\n=====FIM ACCAO=====\n'


def test_gancho_chama_propor_com_o_resultado(ctx, monkeypatch):
    cfg, path, tmp = ctx
    registar(cfg)
    (tmp / "resultado.txt").write_text("Entrega.\n" + BLOCO, "utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_RESULTADO", str(tmp / "resultado.txt"))
    chamadas = []
    monkeypatch.setitem(sys.modules, "accoes_propor", types.SimpleNamespace(
        propor=lambda *a: chamadas.append(a) or ["acções: 1 recusada"]))
    tid = preparar(cfg, path)
    antes = time.time()
    m = lancador.correr(cfg, tid)
    ((cfg_, squad, tid_, texto, now),) = chamadas
    assert (cfg_, squad, tid_, texto) == (cfg, "dev", tid, "Entrega.\n" + BLOCO) and now >= antes
    assert m["status"] == "concluída" and m["issues"] == ["acções: 1 recusada"]


@pytest.mark.parametrize("modulo,erro", [(None, "ModuleNotFoundError"), ("rebenta", "RuntimeError")])
def test_gancho_falha_fica_nos_issues_e_estado_igual(ctx, monkeypatch, modulo, erro):
    cfg, path, _ = ctx
    registar(cfg)

    def rebenta(*_):
        raise RuntimeError("disco")
    monkeypatch.setitem(sys.modules, "accoes_propor",
                        None if modulo is None else types.SimpleNamespace(propor=rebenta))
    m = lancador.correr(cfg, preparar(cfg, path))
    assert m["status"] == "concluída" and m["issues"] == [f"acções: pedido não gravado ({erro})"]


def test_gancho_nao_corre_se_o_claude_falhou(ctx, monkeypatch):
    cfg, path, _ = ctx
    registar(cfg)
    chamadas = []
    monkeypatch.setitem(sys.modules, "accoes_propor", types.SimpleNamespace(propor=lambda *a: chamadas.append(a)))
    monkeypatch.setenv("FAKE_CLAUDE_FALHA", "1")
    m = lancador.correr(cfg, preparar(cfg, path))
    assert m["status"] == "erro" and chamadas == []
