"""Fila dos agentes no runner, acções sem IA (CA-17…20, 26, 27, 32, 33): pastas reais em tmp_path, passagem real
(`office_runner.uma_vez`) com `NOW` fixo, nos layouts dos modos VPS e Docker."""
import json
import os
import shutil
from pathlib import Path

import pytest

from tests import test_runner_agentes_comum as a
from tests.test_runner_comum import agentes_fila, lancador, office_runner, officecfg, seguro

import agentes

NOW = a.NOW
# CRLF, comentário, chaves fora da lista e corpo: tudo tem de ficar byte a byte
REVISOR = (b"---\r\nname: revisor\r\ndescription: Rev\xc3\xaa c\xc3\xb3digo\r\n# nota da casa\r\nmodel: haiku\r\n"
           b"tools: Read, Grep\r\ncor: azul\r\n---\r\nCorpo \xc3\xbanico do revisor.\r\n")


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    a.c.ambiente(monkeypatch, tmp_path)
    monkeypatch.delenv("OFFICE_MODO", raising=False)
    monkeypatch.delenv("OFFICE_SEGREDOS_DIR", raising=False)
    cfg, path = a.c.config(tmp_path)
    monkeypatch.setattr(lancador, "destacar", lambda *x, **k: 4242)
    return cfg, path, tmp_path


def passar(ctx, t=NOW) -> list:
    return office_runner.uma_vez(ctx[0], str(ctx[1]), t)


def revisor(cfg, sq="dev") -> Path:
    p = a.agente(cfg, sq, "revisor", "")
    p.write_bytes(REVISOR)
    return p


def agentes_dir(cfg, sq="dev") -> Path:
    return officecfg.caminho(cfg, "agentes") / sq / "agents"


def cand_dir(cfg, sq="dev") -> Path:
    return officecfg.caminho(cfg, "agentes_rascunhos") / "agentes" / sq


# ---------------------------------------------------------------- critério 1: modelo e skills (CA-17), dois layouts

@pytest.fixture(params=["vps", "docker"])
def layout(request, tmp_path, monkeypatch):
    """vps: office.yaml e claude.env lado a lado; docker: config em /config (só de leitura no contentor), segredos em
    OFFICE_SEGREDOS_DIR e OFFICE_MODO=docker. Os dados ficam numa raiz única como os volumes do runner."""
    a.c.ambiente(monkeypatch, tmp_path)
    monkeypatch.delenv("OFFICE_MODO", raising=False)
    monkeypatch.delenv("OFFICE_SEGREDOS_DIR", raising=False)
    cfg, path = a.c.config(tmp_path)
    if request.param == "docker":
        (tmp_path / "config").mkdir()
        (tmp_path / "segredos").mkdir(mode=0o700)
        path = Path(shutil.move(str(path), tmp_path / "config" / "office.yaml"))
        path.chmod(0o444)
        monkeypatch.setenv("OFFICE_MODO", "docker")
        monkeypatch.setenv("OFFICE_SEGREDOS_DIR", str(tmp_path / "segredos"))
    monkeypatch.setattr(lancador, "destacar", lambda *x, **k: 4242)
    a.c.repo(cfg)
    return cfg, path, tmp_path


def test_criterio1_modelo_e_skills_aplicados_e_copiados_para_a_tarefa(layout):
    cfg = layout[0]
    p = revisor(cfg)
    a.skill(cfg, "dev", "testes")
    a.skill(cfg, "dev", "lint")
    s1 = a.pedido(cfg, "modelo", {"squad": "dev", "agente": "revisor", "sha256": a.sha(REVISOR), "modelo": "opus"})
    linhas = passar(layout)
    assert a.feito(cfg, s1) == {"ok": True} and f"agentes: pedido {s1} (modelo) feito" in linhas
    depois = REVISOR.replace(b"model: haiku", b"model: opus")
    assert p.read_bytes() == depois
    assert (agentes_dir(cfg) / "revisor.md.bak-20261118-1000").read_bytes() == REVISOR
    s2 = a.pedido(cfg, "skills", {"squad": "dev", "agente": "revisor", "sha256": a.sha(depois),
                                  "skills": ["testes", "lint"]}, 1, NOW + 60)
    passar(layout, NOW + 60)
    assert a.feito(cfg, s2) == {"ok": True}
    final = depois.replace(b"---\r\nCorpo", b"skills:\r\n  - testes\r\n  - lint\r\n---\r\nCorpo")
    assert p.read_bytes() == final
    assert (agentes_dir(cfg) / "revisor.md.bak-20261118-1001").read_bytes() == depois
    # a tarefa seguinte da squad recebe o ficheiro novo (CA-14)
    a.c.pedido(cfg)
    passar(layout, NOW + 120)
    tid = a.c.feito(cfg)["task_id"]
    wt = Path(json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text("utf-8"))["worktree"])
    assert (wt / ".claude" / "agents" / "revisor.md").read_bytes() == final


@pytest.mark.parametrize("skills, bloco", [([], b""), (["testes"], b"skills: testes\r\n"),
                                           (["lint", "testes"], b"skills:\r\n  - lint\r\n  - testes\r\n")])
def test_skills_0_1_2(ctx, skills, bloco):
    cfg = ctx[0]
    p = revisor(cfg)
    a.skill(cfg, "dev", "testes")
    a.skill(cfg, "dev", "lint")
    stem = a.pedido(cfg, "skills", {"squad": "dev", "agente": "revisor", "sha256": a.sha(REVISOR), "skills": skills})
    passar(ctx)
    assert a.feito(cfg, stem) == {"ok": True}
    assert p.read_bytes() == REVISOR.replace(b"---\r\nCorpo", bloco + b"---\r\nCorpo")


def test_modelo_vazio_tira_a_linha(ctx):
    cfg = ctx[0]
    p = revisor(cfg)
    a.pedido(cfg, "modelo", {"squad": "dev", "agente": "revisor", "sha256": a.sha(REVISOR), "modelo": ""})
    passar(ctx)
    assert p.read_bytes() == REVISOR.replace(b"model: haiku\r\n", b"")


@pytest.mark.parametrize("args, erro", [
    ({"sha256": "0" * 64}, "o agente mudou depois de ser visto"),
    ({"squad": "zzz"}, "squad desconhecida"),
    ({"agente": "outro"}, "agente inexistente"),
    ({"modelo": "gpt"}, "modelo inválido"),
])
def test_modelo_recusado_nada_muda(ctx, args, erro):
    cfg = ctx[0]
    p = revisor(cfg)
    stem = a.pedido(cfg, "modelo", {"squad": "dev", "agente": "revisor", "sha256": a.sha(REVISOR), "modelo": "opus",
                                    **args})
    passar(ctx)
    assert a.feito(cfg, stem) == {"erro": erro}
    assert p.read_bytes() == REVISOR and sorted(os.listdir(agentes_dir(cfg))) == ["revisor.md"]


def test_skill_inexistente_e_skill_symlink(ctx, tmp_path):
    cfg = ctx[0]
    revisor(cfg)
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "SKILL.md").write_text("x", "utf-8")
    (officecfg.caminho(cfg, "agentes") / "dev" / "skills").mkdir(parents=True)
    (officecfg.caminho(cfg, "agentes") / "dev" / "skills" / "ligada").symlink_to(fora)
    for n, sk in enumerate((["nao-ha"], ["ligada"])):
        stem = a.pedido(cfg, "skills", {"squad": "dev", "agente": "revisor", "sha256": a.sha(REVISOR), "skills": sk},
                        n, NOW + n)
        passar(ctx, NOW + n)
        assert a.feito(cfg, stem) == {"erro": "skill inexistente"}


def test_agente_symlink_e_inexistente(ctx, tmp_path):
    cfg = ctx[0]
    (tmp_path / "fora.md").write_bytes(REVISOR)
    agentes_dir(cfg).mkdir(parents=True)
    (agentes_dir(cfg) / "revisor.md").symlink_to(tmp_path / "fora.md")
    stem = a.pedido(cfg, "modelo", {"squad": "dev", "agente": "revisor", "sha256": a.sha(REVISOR), "modelo": "opus"})
    passar(ctx)
    assert a.feito(cfg, stem) == {"erro": "agente inexistente"} and (tmp_path / "fora.md").read_bytes() == REVISOR


def test_um_por_alvo_por_passagem(ctx):
    cfg = ctx[0]
    p = revisor(cfg)
    s1 = a.pedido(cfg, "modelo", {"squad": "dev", "agente": "revisor", "sha256": a.sha(REVISOR), "modelo": "opus"}, 1)
    s2 = a.pedido(cfg, "modelo", {"squad": "dev", "agente": "revisor", "sha256": a.sha(REVISOR), "modelo": "sonnet"}, 2)
    passar(ctx)
    assert a.feito(cfg, s1) == {"ok": True} and a.feito(cfg, s2) == {"erro": "pedido repetido nesta passagem"}
    assert b"model: opus" in p.read_bytes()


def test_pedidos_ilegiveis_e_versao(ctx):
    cfg = ctx[0]
    fila = officecfg.caminho(cfg, "agentes_fila")
    (fila / "20261118T100000-00000001.json").write_text("{x", "utf-8")
    stem = a.pedido(cfg, "squad_descartar", {"squad": "dev"}, 2)
    p = fila / f"{stem}.json"
    d = json.loads(p.read_text("utf-8"))
    p.write_text(json.dumps(dict(d, v=2)), "utf-8")
    s3 = a.pedido(cfg, "squad_descartar", {"squad": "dev", "a_mais": 1}, 3)
    s4 = a.pedido(cfg, "accao_nova", {"squad": "dev"}, 4)
    s5 = a.pedido(cfg, ["modelo"], {"squad": "dev"}, 5)
    (fila / "nao-e-pedido.json").write_text("{}", "utf-8")
    passar(ctx)
    assert a.feito(cfg, "20261118T100000-00000001") == {"erro": "pedido ilegível"}
    assert a.feito(cfg, stem) == {"erro": "versão desconhecida"}
    assert a.feito(cfg, s3) == {"erro": "pedido ilegível"} and a.feito(cfg, s4) == {"erro": "pedido ilegível"}
    assert a.feito(cfg, s5) == {"erro": "pedido ilegível"}
    assert not (fila / "nao-e-pedido.feito.json").exists()
    for f in fila.glob("*.feito.json"):   # o servidor lê-as todas: só respostas da lista fechada
        d = json.loads(f.read_text("utf-8"))
        assert d == {"ok": True} or (list(d) == ["erro"] and d["erro"] in agentes.ERROS)


def test_no_maximo_50_por_passagem(ctx):
    cfg = ctx[0]
    stems = [a.pedido(cfg, "agente_descartar", {"squad": "dev", "nome": f"n{i:02d}"}, i) for i in range(52)]
    passar(ctx)
    assert all(a.feito(cfg, s) == {"erro": "candidato inexistente"} for s in stems[:50])
    assert a.feito(cfg, stems[50]) is None and a.feito(cfg, stems[51]) is None
    passar(ctx)
    assert a.feito(cfg, stems[51]) == {"erro": "candidato inexistente"}


def test_fila_symlink_nao_e_lida(ctx, tmp_path):
    cfg = ctx[0]
    fila = officecfg.caminho(cfg, "agentes_fila")
    fila.rmdir()
    (tmp_path / "outra").mkdir()
    fila.symlink_to(tmp_path / "outra")
    a.pedido(cfg, "squad_descartar", {"squad": "dev"})
    assert passar(ctx)[-1] == "agentes: fila insegura ou ilegível (agentes-fila/)"
    assert not list((tmp_path / "outra").glob("*.feito.json"))


# ---------------------------------------------------------------- remover (CA-27)

def test_remover_move_para_removidos_e_lead_recusado(ctx):
    cfg = ctx[0]
    p = revisor(cfg)
    lead = a.agente(cfg, "dev", "tech-lead", "---\nname: tech-lead\ndescription: L\n---\n")
    s1 = a.pedido(cfg, "agente_remover", {"squad": "dev", "agente": "revisor", "sha256": a.sha(REVISOR)}, 1)
    s2 = a.pedido(cfg, "agente_remover", {"squad": "dev", "agente": "tech-lead", "sha256": a.sha(lead.read_bytes())}, 2)
    passar(ctx)
    assert a.feito(cfg, s1) == {"ok": True} and not p.exists()
    assert (agentes_dir(cfg) / ".removidos" / "revisor.md.bak-20261118-1000").read_bytes() == REVISOR
    assert a.feito(cfg, s2) == {"erro": "o lead não pode ser removido"} and lead.exists()


# ---------------------------------------------------------------- candidatos: criar, activar, descartar (CA-25…27)

NOVO = "---\nname: novo\ndescription: Um agente novo\ntools: Read\n---\nFaz coisas.\n"


def ensaio(cfg, nome, raw: bytes, estado="ok"):
    (cand_dir(cfg) / f"{nome}.ensaio.json").write_text(json.dumps({
        "v": 1, "sha256": a.sha(raw), "estado": estado, "motivo": None, "relatorio": "", "nao_ensaiadas": [],
        "custo_usd": 0.1, "em": "2026-11-18T09:00:00+00:00"}), "utf-8")


def test_criar_grava_candidato_e_activar_exige_ensaio(ctx):
    cfg = ctx[0]
    s1 = a.pedido(cfg, "agente_criar", {"squad": "dev", "nome": "novo", "sha256": None, "md": NOVO})
    passar(ctx)
    assert a.feito(cfg, s1) == {"ok": True} and (cand_dir(cfg) / "novo.md").read_text("utf-8") == NOVO
    assert not (agentes_dir(cfg) / "novo.md").exists()
    sha = a.sha(NOVO.encode())
    s2 = a.pedido(cfg, "agente_activar", {"squad": "dev", "nome": "novo", "sha256": sha}, 1, NOW + 60)
    passar(ctx, NOW + 60)
    assert a.feito(cfg, s2) == {"erro": "sem ensaio ok desta versão"} and not (agentes_dir(cfg) / "novo.md").exists()
    ensaio(cfg, "novo", b"outra versao")
    s3 = a.pedido(cfg, "agente_activar", {"squad": "dev", "nome": "novo", "sha256": sha}, 2, NOW + 120)
    passar(ctx, NOW + 120)
    assert a.feito(cfg, s3) == {"erro": "sem ensaio ok desta versão"}
    ensaio(cfg, "novo", NOVO.encode(), "erro")
    s4 = a.pedido(cfg, "agente_activar", {"squad": "dev", "nome": "novo", "sha256": sha}, 3, NOW + 180)
    passar(ctx, NOW + 180)
    assert a.feito(cfg, s4) == {"erro": "sem ensaio ok desta versão"}
    ensaio(cfg, "novo", NOVO.encode())
    s5 = a.pedido(cfg, "agente_activar", {"squad": "dev", "nome": "novo", "sha256": sha}, 4, NOW + 240)
    passar(ctx, NOW + 240)
    assert a.feito(cfg, s5) == {"ok": True}
    assert (agentes_dir(cfg) / "novo.md").read_text("utf-8") == NOVO and list(cand_dir(cfg).iterdir()) == []


def test_editar_depois_do_ensaio_apaga_o_ensaio(ctx):
    cfg = ctx[0]
    a.pedido(cfg, "agente_criar", {"squad": "dev", "nome": "novo", "sha256": None, "md": NOVO})
    passar(ctx)
    ensaio(cfg, "novo", NOVO.encode())
    editado = NOVO.replace("Faz coisas.", "Faz outras coisas.")
    s = a.pedido(cfg, "agente_criar", {"squad": "dev", "nome": "novo", "sha256": a.sha(NOVO.encode()), "md": editado},
                 1, NOW + 60)
    passar(ctx, NOW + 60)
    assert a.feito(cfg, s) == {"ok": True} and not (cand_dir(cfg) / "novo.ensaio.json").exists()


@pytest.mark.parametrize("args, erro", [
    ({"md": NOVO.replace("tools: Read", "permissionMode: bypassPermissions")}, "frontmatter inválido"),
    ({"md": NOVO.replace("tools: Read", "tools: Read\n---x")}, "frontmatter inválido"),
    ({"md": NOVO.replace("name: novo", "name: outro")}, "frontmatter inválido"),
    ({"md": NOVO.replace("tools: Read", "skills: nao-ha")}, "skill inexistente"),
    ({"sha256": "0" * 64}, "candidato inexistente"),
    ({"squad": "zzz"}, "squad desconhecida"),
])
def test_criar_recusado(ctx, args, erro):
    cfg = ctx[0]
    stem = a.pedido(cfg, "agente_criar", {"squad": "dev", "nome": "novo", "sha256": None, "md": NOVO, **args})
    passar(ctx)
    assert a.feito(cfg, stem) == {"erro": erro} and not (cand_dir(cfg) / "novo.md").exists()


def test_criar_com_candidato_ja_existente_e_sha_nulo(ctx):
    cfg = ctx[0]
    cand_dir(cfg).mkdir(parents=True)
    (cand_dir(cfg) / "novo.md").write_text(NOVO, "utf-8")
    stem = a.pedido(cfg, "agente_criar", {"squad": "dev", "nome": "novo", "sha256": None, "md": NOVO + "x\n"})
    passar(ctx)
    assert a.feito(cfg, stem) == {"erro": "o rascunho mudou depois de ser visto"}


def test_activar_nunca_sobrescreve(ctx):
    cfg = ctx[0]
    cand_dir(cfg).mkdir(parents=True)
    (cand_dir(cfg) / "revisor.md").write_text(NOVO.replace("novo", "revisor"), "utf-8")
    raw = (cand_dir(cfg) / "revisor.md").read_bytes()
    ensaio(cfg, "revisor", raw)
    p = revisor(cfg)
    s = a.pedido(cfg, "agente_activar", {"squad": "dev", "nome": "revisor", "sha256": a.sha(raw)})
    passar(ctx)
    assert a.feito(cfg, s) == {"erro": "já existe"} and p.read_bytes() == REVISOR
    s = a.pedido(cfg, "agente_criar", {"squad": "dev", "nome": "revisor", "sha256": None, "md": NOVO}, 1, NOW + 1)
    passar(ctx, NOW + 1)
    assert a.feito(cfg, s) == {"erro": "já existe"}


def test_descartar(ctx):
    cfg = ctx[0]
    cand_dir(cfg).mkdir(parents=True)
    (cand_dir(cfg) / "novo.md").write_text(NOVO, "utf-8")
    ensaio(cfg, "novo", NOVO.encode())
    s = a.pedido(cfg, "agente_descartar", {"squad": "dev", "nome": "novo"})
    passar(ctx)
    assert a.feito(cfg, s) == {"ok": True} and list(cand_dir(cfg).iterdir()) == []


# ---------------------------------------------------------------- squads por modelo sem IA (CA-32, CA-33)

def rascunho(cfg, sq: str, modelo="observacao", nome="Vigia", **mexer) -> str:
    m = agentes.MODELOS_DIR / modelo
    fic = {"CLAUDE.md": (m / "CLAUDE.md").read_text("utf-8").replace("{{MISSAO}}", "Vigiar o site.")}
    for f in sorted((m / "agents").iterdir()):
        fic[f"agents/{f.name}"] = f.read_text("utf-8")
    fic.update(mexer)
    lead = agentes.frontmatter((m / "README.md").read_text("utf-8"))["lead"]
    pasta = officecfg.caminho(cfg, "agentes_rascunhos") / "squads" / sq
    pasta.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"v": 1, "squad": sq, "modelo": modelo, "nome": nome, "lead": lead, "missao": "Vigiar o site.",
                      "ficheiros": fic, "criado_em": "2026-11-18T09:00:00+00:00"}).encode()
    (pasta / "rascunho.json").write_bytes(raw)
    return a.sha(raw)


def test_squad_criar_nova_fora_do_office_yaml(ctx):
    cfg = ctx[0]
    sha = rascunho(cfg, "vigia")
    s = a.pedido(cfg, "squad_criar", {"squad": "vigia", "sha256": sha})
    passar(ctx)
    assert a.feito(cfg, s) == {"ok": True}
    base = officecfg.caminho(cfg, "agentes") / "vigia"
    assert sorted(os.listdir(base / "agents")) == ["investigador.md", "plantao.md"]
    assert "Vigiar o site." in (base / "CLAUDE.md").read_text("utf-8")
    assert a.registo(cfg)["squads"] == [{"id": "vigia", "modelo": "observacao", "nome": "Vigia", "lead": "plantao",
                                         "criada_em": "2026-11-18T10:00:00+00:00"}]
    assert not (officecfg.caminho(cfg, "agentes_rascunhos") / "squads" / "vigia" / "rascunho.json").exists()
    assert not [n for n in os.listdir(officecfg.caminho(cfg, "agentes")) if n.startswith(".novo-")]


def test_squad_criar_povoa_squad_do_office_yaml_sem_agentes(ctx):
    cfg = ctx[0]
    base = officecfg.caminho(cfg, "agentes") / "dev"
    (base / "agents").mkdir(parents=True)
    (base / "CLAUDE.md").write_text("as minhas instruções\n", "utf-8")
    sha = rascunho(cfg, "dev", nome="Dev")
    s = a.pedido(cfg, "squad_criar", {"squad": "dev", "sha256": sha})
    passar(ctx)
    assert a.feito(cfg, s) == {"ok": True}
    assert sorted(os.listdir(base / "agents")) == ["investigador.md", "plantao.md"]
    assert (base / "CLAUDE.md").read_text("utf-8") == "as minhas instruções\n"
    assert [x["id"] for x in a.registo(cfg)["squads"]] == ["dev"]


def test_squad_criar_recusas(ctx):
    cfg = ctx[0]
    revisor(cfg)
    sha = rascunho(cfg, "dev")
    s1 = a.pedido(cfg, "squad_criar", {"squad": "dev", "sha256": sha}, 1)
    s2 = a.pedido(cfg, "squad_criar", {"squad": "vigia", "sha256": "0" * 64}, 2)
    passar(ctx)
    assert a.feito(cfg, s1) == {"erro": "squad já tem agentes"} and a.feito(cfg, s2) == {"erro": "rascunho inexistente"}
    sha = rascunho(cfg, "vigia")
    s3 = a.pedido(cfg, "squad_criar", {"squad": "vigia", "sha256": "1" * 64}, 3, NOW + 1)
    passar(ctx, NOW + 1)
    assert a.feito(cfg, s3) == {"erro": "o rascunho mudou depois de ser visto"}
    assert a.registo(cfg)["squads"] == []


@pytest.mark.parametrize("ficheiro, de, para", [
    ("agents/plantao.md", "tools: Read, Grep, Glob, Agent", "tools: Read, Grep, Glob, Agent, Bash"),
    ("agents/plantao.md", "model: sonnet", "model: opus"),
    ("agents/plantao.md", "name: plantao", "name: outro"),
])
def test_squad_criar_revalida_o_rascunho_contra_o_modelo(ctx, ficheiro, de, para):
    cfg = ctx[0]
    texto = (agentes.MODELOS_DIR / "observacao" / ficheiro).read_text("utf-8").replace(de, para)
    sha = rascunho(cfg, "vigia", **{ficheiro: texto})
    s = a.pedido(cfg, "squad_criar", {"squad": "vigia", "sha256": sha})
    passar(ctx)
    assert a.feito(cfg, s) == {"erro": "frontmatter inválido"}
    assert not (officecfg.caminho(cfg, "agentes") / "vigia").exists() and a.registo(cfg)["squads"] == []


@pytest.mark.parametrize("sq", ["vigia", "dev"])
def test_squad_criar_falha_a_meio_nao_deixa_nada(ctx, monkeypatch, sq):
    cfg = ctx[0]
    base = officecfg.caminho(cfg, "agentes")
    if sq == "dev":
        (base / "dev").mkdir()
    sha = rascunho(cfg, sq)
    real = seguro.mover

    def mover(src, nome, dst, destino=None):
        if nome == "agents" or destino == sq:
            raise OSError("disco cheio")
        return real(src, nome, dst, destino)
    monkeypatch.setattr(seguro, "mover", mover)
    s = a.pedido(cfg, "squad_criar", {"squad": sq, "sha256": sha})
    passar(ctx)
    assert a.feito(cfg, s) == {"erro": "erro de escrita"}
    assert sorted(os.listdir(base)) == (["dev"] if sq == "dev" else []) + ["squads.json"]
    assert not (base / "dev").exists() or os.listdir(base / "dev") == []
    assert a.registo(cfg)["squads"] == []
    assert (officecfg.caminho(cfg, "agentes_rascunhos") / "squads" / sq / "rascunho.json").exists()


def test_squad_descartar_e_desactivar(ctx):
    cfg = ctx[0]
    rascunho(cfg, "outra")
    s0 = a.pedido(cfg, "squad_descartar", {"squad": "outra"})
    sha = rascunho(cfg, "vigia")
    s1 = a.pedido(cfg, "squad_criar", {"squad": "vigia", "sha256": sha}, 1)
    s2 = a.pedido(cfg, "squad_desactivar", {"squad": "dev"}, 2)
    passar(ctx)
    assert a.feito(cfg, s0) == {"ok": True} and a.feito(cfg, s1) == {"ok": True}
    assert not (officecfg.caminho(cfg, "agentes_rascunhos") / "squads" / "outra").exists()
    assert a.feito(cfg, s2) == {"erro": "squad não registada"}
    s3 = a.pedido(cfg, "squad_desactivar", {"squad": "vigia"}, 3, NOW + 60)
    passar(ctx, NOW + 60)
    assert a.feito(cfg, s3) == {"ok": True} and a.registo(cfg)["squads"] == []
    base = officecfg.caminho(cfg, "agentes") / "vigia"
    assert not (base / "agents").exists()
    assert sorted(os.listdir(base / "agents.bak-20261118-1001")) == ["investigador.md", "plantao.md"]


def test_desactivar_cancela_as_rotinas(ctx):
    cfg = ctx[0]
    a.c.repo(cfg)
    sha = rascunho(cfg, "dev", nome="Dev")
    a.pedido(cfg, "squad_criar", {"squad": "dev", "sha256": sha})
    passar(ctx)
    base = officecfg.caminho(cfg, "agentes")
    (base / "rotinas.json").write_text(json.dumps({"v": 1, "rotinas": [{
        "id": "r1", "squad": "dev", "projecto": "exemplo", "hora": "09:00", "dias": [1], "instrucoes": "x",
        "tecto_usd": "0.50", "max_turnos": 1, "sha256": "0" * 64, "decisao": "rotina-dev-r1.md", "estado": "aprovada",
        "falhas": 0, "ultima": None, "tarefas": [], "criada_em": "2026-11-18"}]}), "utf-8")
    a.pedido(cfg, "squad_desactivar", {"squad": "dev"}, 1, NOW + 60)
    passar(ctx, NOW + 60)
    assert a.rotinas_de(cfg)[0]["estado"] == "cancelada"


# ---------------------------------------------------------------- rotação

def test_rodar_move_os_antigos_para_feito(ctx):
    cfg = ctx[0]
    s = a.pedido(cfg, "squad_descartar", {"squad": "dev"})
    passar(ctx)
    fila = officecfg.caminho(cfg, "agentes_fila")
    velho = NOW - 15 * a.DIA
    os.utime(fila / f"{s}.feito.json", (velho, velho))
    assert agentes_fila.rodar(cfg, NOW) == [f"agentes: pedido {s} rodado para feito/"]
    assert sorted(os.listdir(fila / "feito")) == [f"{s}.feito.json", f"{s}.json"]
    assert agentes_fila.rodar(cfg, NOW) == []


def test_desactivar_com_rotinas_ilegiveis_nao_mexe_em_nada(ctx):
    cfg = ctx[0]
    sha = rascunho(cfg, "vigia")
    a.pedido(cfg, "squad_criar", {"squad": "vigia", "sha256": sha})
    passar(ctx)
    base = officecfg.caminho(cfg, "agentes")
    (base / "rotinas.json").write_text("{lixo", "utf-8")
    s = a.pedido(cfg, "squad_desactivar", {"squad": "vigia"}, 1, NOW + 60)
    passar(ctx, NOW + 60)
    assert a.feito(cfg, s) == {"erro": "erro de escrita"}
    assert [x["id"] for x in a.registo(cfg)["squads"]] == ["vigia"] and (base / "vigia" / "agents").is_dir()
