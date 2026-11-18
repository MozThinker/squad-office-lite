"""Fila dos agentes, acções com IA (CA-23…25, 28, 29, 31) e os critérios 2 e 3 de ponta a ponta. `claude` falso de
tests/bin (FAKE_CLAUDE_RESULTADO, _CUSTO, _DORME, _CWD, _ARGV, _STDIN), repos locais, `NOW` fixo."""
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests import test_runner_agentes_comum as a
from tests.test_runner_comum import agentes_fila, lancador, office_runner, officecfg

import agentes

NOW = a.NOW
TOKEN = "ghp_" + "Z" * 36
SEGREDO_DESC = "Descrição secreta 7f3a: revê os PRs e nunca aprova sozinho."


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    a.c.ambiente(monkeypatch, tmp_path)
    monkeypatch.delenv("OFFICE_MODO", raising=False)
    monkeypatch.delenv("OFFICE_SEGREDOS_DIR", raising=False)
    cfg, path = a.c.config(tmp_path)
    a.c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *x, **k: 4242)
    for k, f in (("FAKE_CLAUDE_ARGV", "argv.jsonl"), ("FAKE_CLAUDE_STDIN", "stdin.txt"), ("FAKE_CLAUDE_CWD", "cwd.jsonl"),
                 ("FAKE_CLAUDE_RESULTADO", "resultado.txt")):
        monkeypatch.setenv(k, str(tmp_path / f))
    return cfg, path, tmp_path


def passar(ctx, t=NOW) -> list:
    return office_runner.uma_vez(ctx[0], str(ctx[1]), t)


def resultado(ctx, texto: str) -> None:
    (ctx[2] / "resultado.txt").write_text(texto, "utf-8")


def argvs(ctx) -> list:
    f = ctx[2] / "argv.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


def cwds(ctx) -> list:
    return [json.loads(x) for x in (ctx[2] / "cwd.jsonl").read_text("utf-8").splitlines()]


def cand_dir(cfg, sq="dev") -> Path:
    return officecfg.caminho(cfg, "agentes_rascunhos") / "agentes" / sq


def livro(cfg) -> list:
    f = officecfg.caminho(cfg, "runner") / "agentes-ia.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


RASCUNHO_IA = """Aqui está:
<<<AGENTE
---
name: outro-nome
description: Revê pull requests com cuidado
tools: Read, Grep, bash-livre, Read
model: haiku
skills: [testes, nao-ha]
color: red
hooks: x
---
És o revisor de PRs.
AGENTE>>>
"""


# ---------------------------------------------------------------- agente_rascunho (CA-23, CA-24)

def test_rascunho_argv_stdin_e_normalizacao(ctx):
    cfg = ctx[0]
    a.skill(cfg, "dev", "testes")
    resultado(ctx, RASCUNHO_IA)
    s = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "revisor-pr", "descricao": SEGREDO_DESC})
    passar(ctx)
    assert a.feito(cfg, s) == {"ok": True}
    (argv,) = argvs(ctx)
    assert argv[:12] == ["-p", "--tools", "", "--max-turns", "1", "--strict-mcp-config", "--output-format", "json",
                         "--no-session-persistence", "--max-budget-usd", "0.50"][:12]
    assert not any("7f3a" in x or "revê" in x for x in argv)
    stdin = (ctx[2] / "stdin.txt").read_text("utf-8")
    nonce = stdin.split("<<<DADOS-")[1][:16]
    assert f"<<<DADOS-{nonce}\n{SEGREDO_DESC}\nDADOS-{nonce}>>>" in stdin
    md = (cand_dir(cfg) / "revisor-pr.md").read_text("utf-8")
    assert md == ('---\nname: revisor-pr\ndescription: "Revê pull requests com cuidado"\ntools: Read, Grep\n'
                  "model: haiku\nskills: testes\n---\nÉs o revisor de PRs.\n")
    assert agentes.validar_md(md, "revisor-pr", {"testes"}) is None
    assert not (officecfg.caminho(cfg, "agentes") / "dev" / "agents" / "revisor-pr.md").exists()
    assert cwds(ctx)[0]["agentes"] is None and not os.path.exists(cwds(ctx)[0]["cwd"])   # mkdtemp vazio e apagado
    (linha,) = livro(cfg)
    assert linha["accao"] == "agente_rascunho" and linha["custo_usd"] == 0.01 and linha["tecto"] == 0.5


def test_rascunho_com_o_modelo_da_squad(tmp_path, monkeypatch):
    a.c.ambiente(monkeypatch, tmp_path)
    cfg, path = a.c.config(tmp_path)
    texto = path.read_text("utf-8").replace("    lead: tech-lead\n", "    lead: tech-lead\n    modelo: haiku\n")
    path.write_text(texto, "utf-8")
    cfg, _ = officecfg.carregar(path, estrito=True)
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(tmp_path / "argv.jsonl"))
    a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "x-y", "descricao": "d"})
    office_runner.uma_vez(cfg, str(path), NOW)
    argv = json.loads((tmp_path / "argv.jsonl").read_text("utf-8"))
    assert argv[argv.index("--model") + 1] == "haiku"


@pytest.mark.parametrize("texto", [
    RASCUNHO_IA.replace("color: red", "permissionMode: bypassPermissions"),
    RASCUNHO_IA.replace("color: red", "cor: ---"),
    "não sei fazer isso",
])
def test_rascunho_invalido_recusado(ctx, texto):
    cfg = ctx[0]
    resultado(ctx, texto)
    s = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "revisor-pr", "descricao": "x"})
    passar(ctx)
    assert a.feito(cfg, s) == {"erro": "frontmatter inválido"} and not cand_dir(cfg).exists()


def test_rascunho_ia_falha(ctx, monkeypatch):
    cfg = ctx[0]
    monkeypatch.setenv("FAKE_CLAUDE_FALHA", "1")
    s = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "revisor-pr", "descricao": "x"})
    passar(ctx)
    assert a.feito(cfg, s) == {"erro": "a IA falhou"} and not cand_dir(cfg).exists()


def test_rascunho_tempo_esgotado_apaga_a_pasta(ctx, monkeypatch):
    cfg = ctx[0]
    monkeypatch.setenv("FAKE_CLAUDE_DORME", "5")
    monkeypatch.setattr(agentes_fila, "RASCUNHO_S", 1)
    s = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "revisor-pr", "descricao": "x"})
    passar(ctx)
    assert a.feito(cfg, s) == {"erro": "tempo esgotado"}
    assert not os.path.exists(cwds(ctx)[0]["cwd"])
    assert livro(cfg)[0]["custo_usd"] is None   # sem custo conhecido: conta o tecto


def test_credenciais_do_claude_env_como_o_lancador(ctx, monkeypatch):
    cfg, _, tmp = ctx
    monkeypatch.setenv("FAKE_CLAUDE_CRED", str(tmp / "cred.jsonl"))
    cred = tmp / "claude.env"
    cred.write_text("ANTHROPIC_API_KEY=sk-ant-api03-teste_0123456789-abc\n", "utf-8")
    cred.chmod(0o600)
    a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "revisor-pr", "descricao": "x"})
    passar(ctx)
    (vista,) = [json.loads(x) for x in (tmp / "cred.jsonl").read_text("utf-8").splitlines()]
    assert list(vista) == ["ANTHROPIC_API_KEY"]
    cred.chmod(0o644)   # permissões abertas: o claude nem corre
    a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "outro-x", "descricao": "x"}, 1, NOW + 60)
    passar(ctx, NOW + 60)
    assert len(argvs(ctx)) == 1
    assert len(livro(cfg)) == 1   # o claude não arrancou: nada no livro (CR7)


def test_rascunho_so_com_ferramentas_de_leitura_e_sem_permission_mode(ctx):
    cfg = ctx[0]
    resultado(ctx, RASCUNHO_IA.replace("tools: Read, Grep, bash-livre, Read", "tools: Bash, WebFetch, Edit")
              .replace("color: red", "permissionMode: acceptEdits"))
    s = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "revisor-pr", "descricao": "x"})
    passar(ctx)
    assert a.feito(cfg, s) == {"ok": True}
    md = (cand_dir(cfg) / "revisor-pr.md").read_text("utf-8")
    assert "tools: Read, Grep, Glob\n" in md and "permissionMode" not in md and "Bash" not in md


def test_sem_claude_no_path_nada_no_livro(ctx, monkeypatch):
    cfg = ctx[0]
    monkeypatch.setattr(agentes_fila.shutil, "which", lambda x: None)
    s = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "revisor-pr", "descricao": "x"})
    passar(ctx)
    assert a.feito(cfg, s) == {"erro": "a IA falhou"} and livro(cfg) == []


# ---------------------------------------------------------------- travões (CA-28)

def test_uma_accao_com_ia_por_passagem_e_as_outras_seguem(ctx):
    cfg = ctx[0]
    resultado(ctx, RASCUNHO_IA)
    s1 = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "um-a", "descricao": "x"}, 1)
    s2 = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "dois-b", "descricao": "x"}, 2)
    s3 = a.pedido(cfg, "agente_descartar", {"squad": "dev", "nome": "tres-c"}, 3)
    passar(ctx)
    assert a.feito(cfg, s1) == {"ok": True} and a.feito(cfg, s2) is None
    assert a.feito(cfg, s3) == {"erro": "candidato inexistente"} and len(argvs(ctx)) == 1
    passar(ctx, NOW + 60)
    assert a.feito(cfg, s2) == {"ok": True} and len(argvs(ctx)) == 2


def test_tecto_diario_de_ia(ctx):
    cfg = ctx[0]
    runner = officecfg.caminho(cfg, "runner")
    runner.mkdir(parents=True, exist_ok=True)
    ontem = {"em": "2026-11-17T23:30:00+00:00", "accao": "agente_ensaio", "custo_usd": 5.0, "tecto": 2.0}
    hoje = [{"em": "2026-11-18T08:00:00+00:00", "accao": "agente_ensaio", "custo_usd": None, "tecto": 2.0}] * 4 + \
        [{"em": "2026-11-18T09:00:00+00:00", "accao": "agente_ensaio", "custo_usd": 1.6, "tecto": 2.0}]
    (runner / "agentes-ia.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [ontem, *hoje]) + "lixo\n", "utf-8")
    s1 = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "um-a", "descricao": "x"}, 1)   # 9.60 + 0.50
    s2 = a.pedido(cfg, "agente_descartar", {"squad": "dev", "nome": "tres-c"}, 2)
    passar(ctx)
    assert a.feito(cfg, s1) == {"erro": "tecto diário de IA atingido"} and argvs(ctx) == []
    assert a.feito(cfg, s2) == {"erro": "candidato inexistente"}
    # amanhã (no fuso) volta a haver tecto
    resultado(ctx, RASCUNHO_IA)
    s3 = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "um-a", "descricao": "x"}, 3, NOW + a.DIA)
    passar(ctx, NOW + a.DIA)
    assert a.feito(cfg, s3) == {"ok": True}


def test_livro_symlink_bloqueia_a_ia(ctx, tmp_path):
    cfg = ctx[0]
    (tmp_path / "outro.jsonl").write_text("", "utf-8")
    (officecfg.caminho(cfg, "runner") / "agentes-ia.jsonl").symlink_to(tmp_path / "outro.jsonl")
    s = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "um-a", "descricao": "x"})
    passar(ctx)
    assert a.feito(cfg, s) == {"erro": "tecto diário de IA atingido"} and argvs(ctx) == []
    assert (tmp_path / "outro.jsonl").read_text("utf-8") == ""


# ---------------------------------------------------------------- ensaio (CA-25, CA-29)

CAND = "---\nname: leitor\ndescription: Lê o repo\ntools: Read, Grep, WebSearch\n---\nLê.\n"


def candidato(cfg, texto=CAND, nome="leitor") -> str:
    cand_dir(cfg).mkdir(parents=True, exist_ok=True)
    (cand_dir(cfg) / f"{nome}.md").write_text(texto, "utf-8")
    return a.sha(texto.encode())


def test_ensaio_clone_so_de_leitura_relatorio_tapado(tmp_path, monkeypatch):
    a.c.ambiente(monkeypatch, tmp_path)
    monkeypatch.delenv("OFFICE_MODO", raising=False)
    monkeypatch.delenv("OFFICE_SEGREDOS_DIR", raising=False)
    (tmp_path / "s.json").write_text('{"permissions": {"allow": ["Bash(*)"]}}', "utf-8")
    cfg, path = a.c.config(tmp_path, settings=str(tmp_path / "s.json"))
    a.c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *x, **k: 4242)
    for k, f in (("FAKE_CLAUDE_ARGV", "argv.jsonl"), ("FAKE_CLAUDE_STDIN", "stdin.txt"), ("FAKE_CLAUDE_CWD", "cwd.jsonl"),
                 ("FAKE_CLAUDE_RESULTADO", "resultado.txt"), ("FAKE_CLAUDE_SETTINGS", "settings.jsonl")):
        monkeypatch.setenv(k, str(tmp_path / f))
    ctx = (cfg, path, tmp_path)
    repo = officecfg.raiz(cfg, "projectos") / "exemplo"
    a.c.git(["remote", "add", "origin", "https://example.com/x.git"], repo)
    a.agente(cfg, "dev", "revisor", "---\nname: revisor\ndescription: R\n---\n")
    sha = candidato(cfg)
    resultado(ctx, f"Li o README. Encontrei token={TOKEN} no ficheiro x.")
    s = a.pedido(cfg, "agente_ensaio", {"squad": "dev", "nome": "leitor", "sha256": sha, "projecto": "exemplo",
                                        "tarefa": "Resume o README 9b1e."})
    passar(ctx)
    assert a.feito(cfg, s) == {"ok": True}
    (argv,) = argvs(ctx)
    i, j = argv.index("--allowedTools"), argv.index("--disallowedTools")
    assert argv[argv.index("--agent") + 1] == "leitor" and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[i + 1:j] == ["Read", "Grep", "Glob"] and argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert argv[j + 1:j + 10] == ["Bash", "Edit", "Write", "WebFetch", "WebSearch", "NotebookEdit", "Agent", "Task",
                                  "Skill"]
    (vistas,) = [json.loads(x) for x in (tmp_path / "settings.jsonl").read_text("utf-8").splitlines()]
    assert json.loads(vistas["texto"]) == lancador.settings_base(cfg, str(path))   # sem o allow do runner.settings
    assert argv[argv.index("--max-turns") + 1] == "8" and argv[argv.index("--max-budget-usd") + 1] == "2"
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert not any("9b1e" in x for x in argv)
    settings = argv[argv.index("--settings") + 1]
    (vista,) = cwds(ctx)
    assert vista["remotes"] == [] and vista["agentes"] == ["leitor.md", "revisor.md"]
    assert not Path(settings).is_relative_to(vista["cwd"])   # settings fora do clone
    assert not os.path.exists(vista["cwd"]) and not os.path.exists(settings)
    e = json.loads((cand_dir(cfg) / "leitor.ensaio.json").read_text("utf-8"))
    assert e["estado"] == "ok" and e["sha256"] == sha and e["motivo"] is None and e["custo_usd"] == 0.01
    assert TOKEN not in e["relatorio"] and "[tapado]" in e["relatorio"] and e["nao_ensaiadas"] == ["WebSearch"]
    assert agentes._ensaio(cand_dir(cfg) / "leitor.ensaio.json") is not None   # o servidor lê-o


@pytest.mark.parametrize("env, motivo, erro", [({"FAKE_CLAUDE_FALHA": "1"}, "a IA falhou", "a IA falhou"),
                                               ({"FAKE_CLAUDE_CUSTO": "2.5"}, "custo acima do tecto", "a IA falhou"),
                                               ({"FAKE_CLAUDE_DORME": "5"}, "tempo esgotado", "tempo esgotado")])
def test_ensaio_que_falha_fica_em_erro(ctx, monkeypatch, env, motivo, erro):
    cfg = ctx[0]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(agentes_fila, "ENSAIO_S", 1)
    sha = candidato(cfg)
    s = a.pedido(cfg, "agente_ensaio", {"squad": "dev", "nome": "leitor", "sha256": sha, "projecto": "exemplo",
                                        "tarefa": "x"})
    passar(ctx)
    assert a.feito(cfg, s) == {"erro": erro}
    e = json.loads((cand_dir(cfg) / "leitor.ensaio.json").read_text("utf-8"))
    assert e["estado"] == "erro" and e["motivo"] == motivo
    assert not os.path.exists(cwds(ctx)[0]["cwd"])
    s = a.pedido(cfg, "agente_activar", {"squad": "dev", "nome": "leitor", "sha256": sha}, 1, NOW + 60)
    passar(ctx, NOW + 60)
    assert a.feito(cfg, s) == {"erro": "sem ensaio ok desta versão"}


def test_ensaio_sha_desactualizado_ou_projecto_invalido(ctx):
    cfg = ctx[0]
    candidato(cfg)
    s1 = a.pedido(cfg, "agente_ensaio", {"squad": "dev", "nome": "leitor", "sha256": "0" * 64, "projecto": "exemplo",
                                         "tarefa": "x"})
    passar(ctx)
    assert a.feito(cfg, s1) == {"erro": "o rascunho mudou depois de ser visto"}
    s2 = a.pedido(cfg, "agente_ensaio", {"squad": "dev", "nome": "leitor", "sha256": a.sha(CAND.encode()),
                                         "projecto": "loja", "tarefa": "x"}, 1, NOW + 60)
    passar(ctx, NOW + 60)   # loja está na config mas o repo não existe
    assert a.feito(cfg, s2) == {"erro": "projecto inválido"} and argvs(ctx) == []


# ---------------------------------------------------------------- critério 2 de ponta a ponta

def test_criterio2_rascunho_criar_ensaio_activar(ctx):
    cfg = ctx[0]
    resultado(ctx, RASCUNHO_IA)
    s = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "revisor-pr", "descricao": "Revê PRs."})
    passar(ctx)
    assert a.feito(cfg, s) == {"ok": True}
    visto = (cand_dir(cfg) / "revisor-pr.md").read_bytes()
    editado = visto.decode().replace("És o revisor de PRs.", "És o revisor de PRs. Sê breve.")
    s = a.pedido(cfg, "agente_criar", {"squad": "dev", "nome": "revisor-pr", "sha256": a.sha(visto), "md": editado},
                 1, NOW + 60)
    passar(ctx, NOW + 60)
    assert a.feito(cfg, s) == {"ok": True}
    sha = a.sha(editado.encode())
    s = a.pedido(cfg, "agente_activar", {"squad": "dev", "nome": "revisor-pr", "sha256": sha}, 2, NOW + 120)
    passar(ctx, NOW + 120)
    assert a.feito(cfg, s) == {"erro": "sem ensaio ok desta versão"}
    resultado(ctx, "Relatório: tudo bem.")
    s = a.pedido(cfg, "agente_ensaio", {"squad": "dev", "nome": "revisor-pr", "sha256": sha, "projecto": "exemplo",
                                        "tarefa": "Revê o último commit."}, 3, NOW + 180)
    passar(ctx, NOW + 180)
    assert a.feito(cfg, s) == {"ok": True}
    s = a.pedido(cfg, "agente_activar", {"squad": "dev", "nome": "revisor-pr", "sha256": sha}, 4, NOW + 240)
    passar(ctx, NOW + 240)
    assert a.feito(cfg, s) == {"ok": True}
    activo = officecfg.caminho(cfg, "agentes") / "dev" / "agents" / "revisor-pr.md"
    assert activo.read_text("utf-8") == editado and not cand_dir(cfg).joinpath("revisor-pr.md").exists()


# ---------------------------------------------------------------- squad_rascunho (CA-31) e critério 3

SQUAD_IA = """<<<SQUAD
{"missao": "Vigiar a loja online e dizer o que falhou.",
 "agentes": {"plantao": {"description": "Faz a triagem dos alertas da loja", "corpo": "Triagem da loja.\\n---\\nfim",
                         "name": "chefe", "tools": "Bash", "model": "opus", "permissionMode": "bypassPermissions"},
             "investigador": {"description": "", "corpo": ""},
             "intruso": {"description": "Agente a mais", "corpo": "x"}},
 "tools": "Bash"}
SQUAD>>>"""


def test_squad_rascunho_so_muda_descricoes_corpos_e_missao(ctx):
    cfg = ctx[0]
    resultado(ctx, SQUAD_IA)
    s = a.pedido(cfg, "squad_rascunho", {"squad": "vigia", "modelo": "observacao", "missao": "Vigiar a loja 4c2d."})
    passar(ctx)
    assert a.feito(cfg, s) == {"ok": True}
    stdin = (ctx[2] / "stdin.txt").read_text("utf-8")
    assert "Vigiar a loja 4c2d." in stdin and not any("4c2d" in x for x in argvs(ctx)[0])
    r, _ = agentes._rascunho(officecfg.caminho(cfg, "agentes_rascunhos"), "vigia")
    assert set(r["ficheiros"]) == {"CLAUDE.md", "agents/plantao.md", "agents/investigador.md"}
    modelo = agentes.MODELOS_DIR / "observacao"
    plantao = r["ficheiros"]["agents/plantao.md"]
    fm, fm_modelo = agentes.frontmatter(plantao), agentes.frontmatter((modelo / "agents/plantao.md").read_text("utf-8"))
    assert fm.pop("description") == "Faz a triagem dos alertas da loja"
    fm_modelo.pop("description")
    assert fm == fm_modelo and plantao.endswith("---\nTriagem da loja.\n---\nfim\n")
    assert r["ficheiros"]["agents/investigador.md"] == (modelo / "agents/investigador.md").read_text("utf-8")
    assert "Vigiar a loja online e dizer o que falhou." in r["ficheiros"]["CLAUDE.md"]
    assert (r["modelo"], r["lead"], r["nome"]) == ("observacao", "plantao", "Observação")
    assert agentes_fila._validar_rascunho(r)["id"] == "observacao"


def test_squad_rascunho_recusas(ctx):
    cfg = ctx[0]
    a.agente(cfg, "dev", "revisor", "---\nname: revisor\ndescription: R\n---\n")
    s1 = a.pedido(cfg, "squad_rascunho", {"squad": "dev", "modelo": "observacao", "missao": "x"}, 1)
    s2 = a.pedido(cfg, "squad_rascunho", {"squad": "nova", "modelo": "nao-ha", "missao": "x"}, 2)
    passar(ctx)
    assert a.feito(cfg, s1) == {"erro": "squad já tem agentes"} and a.feito(cfg, s2) is None   # 1 com IA por passagem
    passar(ctx, NOW + 30)
    assert a.feito(cfg, s2) == {"erro": "modelo de squad inexistente"} and argvs(ctx) == []
    resultado(ctx, "isto não é JSON")
    s3 = a.pedido(cfg, "squad_rascunho", {"squad": "nova", "modelo": "observacao", "missao": "x"}, 3, NOW + 60)
    passar(ctx, NOW + 60)
    assert a.feito(cfg, s3) == {"erro": "a IA falhou"}


def test_criterio3_squad_por_modelo_e_rotina_so_depois_de_aprovada(ctx):
    cfg = ctx[0]
    resultado(ctx, SQUAD_IA)
    t = NOW
    s = a.pedido(cfg, "squad_rascunho", {"squad": "dev", "modelo": "observacao", "missao": "Vigiar a loja."}, 0, t)
    passar(ctx, t)
    assert a.feito(cfg, s) == {"ok": True}
    _, sha = agentes._rascunho(officecfg.caminho(cfg, "agentes_rascunhos"), "dev")
    t += 60
    s = a.pedido(cfg, "squad_criar", {"squad": "dev", "sha256": sha}, 1, t)
    passar(ctx, t)
    assert a.feito(cfg, s) == {"ok": True}
    base = officecfg.caminho(cfg, "agentes") / "dev"
    assert sorted(os.listdir(base / "agents")) == ["investigador.md", "plantao.md"]
    assert [x["id"] for x in a.registo(cfg)["squads"]] == ["dev"]
    rotina = {"squad": "dev", "projecto": "exemplo", "hora": "09:00", "dias": [1, 2, 3, 4, 5, 6, 7],
              "instrucoes": "Revê os alertas.", "tecto_usd": "0.50", "max_turnos": 5}
    t += 60
    s = a.pedido(cfg, "rotina_pedir", rotina, 2, t)
    passar(ctx, t)
    assert a.feito(cfg, s) == {"erro": "sem ensaio ok desta versão"}   # squad por modelo: ensaio do lead primeiro
    lead = (base / "agents" / "plantao.md").read_bytes()
    resultado(ctx, "Ensaio do plantão: ok.")
    t += 60
    s = a.pedido(cfg, "squad_ensaio", {"squad": "dev", "sha256": a.sha(lead), "projecto": "exemplo",
                                       "tarefa": "Procura os registos de erros."}, 3, t)
    passar(ctx, t)
    assert a.feito(cfg, s) == {"ok": True}
    t += 60
    s = a.pedido(cfg, "rotina_pedir", rotina, 4, t)
    passar(ctx, t)
    assert a.feito(cfg, s) == {"ok": True}
    t += 60
    passar(ctx, t)
    assert a.c.tarefas(cfg) == [] and a.rotinas_de(cfg)[0]["estado"] == "pendente"   # sem R1 aprovado não lança
    a.decidir(cfg, "rotina-dev-r1.md")
    t += 60
    linhas = passar(ctx, t)
    (tid,) = a.c.tarefas(cfg)
    assert f"rotina dev/r1 lançada: {tid}" in linhas
    meta = json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text("utf-8"))
    wt = Path(meta["worktree"])
    assert (wt / ".claude" / "agents" / "plantao.md").read_bytes() == lead and meta["rotina"] == "dev/r1"
    # o que o Office lê (GET /api/agentes) bate com o que o runner escreveu
    resp = agentes.resposta(cfg, officecfg.caminho(cfg, "agentes"), officecfg.caminho(cfg, "agentes_rascunhos"),
                            officecfg.caminho(cfg, "agentes_fila"), ZoneInfo(cfg["fuso"]), t)
    dev = next(x for x in resp["squads"] if x["id"] == "dev")
    assert dev["lead"] == "plantao" and dev["modelo_squad"] == "observacao" and dev["instrucoes"] is True
    assert [(x["nome"], x["lead"]) for x in dev["agentes"]] == [("investigador", False), ("plantao", True)]
    assert dev["ensaio_lead"]["estado"] == "ok" and dev["ensaio_lead"]["sha256"] == a.sha(lead)
    assert [(r["id"], r["estado"], r["ultima"]) for r in dev["rotinas"]] == [("r1", "aprovada", "2026-11-18")]
    assert [p["estado"] for p in resp["pedidos"]] == ["ok", "ok", "erro", "ok", "ok"]
    assert resp["pedidos"][2]["erro"] == "sem ensaio ok desta versão"
