"""agentes.py (parte partilhada Office/runner): frontmatter, validação, reescrita byte a byte, tapar, pedidos v1."""
import json
import os
from pathlib import Path

import pytest

import agentes as ag

SHA = "a" * 64
MD = ("---\nname: revisor\ndescription: Revê PRs com cuidado\ntools: Read, Grep, Glob\nmodel: sonnet\n"
      "skills:\n  - testes\n  - estilo\n---\nCorpo do agente.\n")


# --- frontmatter -------------------------------------------------------------------------------------------------

def test_frontmatter_basico_e_lista_em_bloco():
    fm = ag.frontmatter(MD)
    assert fm == {"name": "revisor", "description": "Revê PRs com cuidado", "tools": "Read, Grep, Glob",
                  "model": "sonnet", "skills": ["testes", "estilo"]}


def test_frontmatter_crlf_e_aspas():
    fm = ag.frontmatter(MD.replace("\n", "\r\n").replace("Revê PRs com cuidado", '"Revê: PRs"'))
    assert fm["description"] == "Revê: PRs" and fm["skills"] == ["testes", "estilo"]


@pytest.mark.parametrize("texto", [
    "\ufeff" + MD,                                         # BOM
    "name: x\n",                                           # sem frontmatter
    "---\nname: x\n",                                      # sem fecho
    "---\nname: x\nname: y\n---\n",                        # chave repetida
    "---\nname: x\ndescription: a --- b\n---\n",           # --- dentro do bloco
    "---\nname: x\x07\n---\n",                             # controlo
    "---\nName: x\n---\n",                                 # chave fora da forma
    "---\nmodel: |\n  sonnet\n---\n",                      # bloco em chave estrutural
    "---\nname: &a x\n---\n",                              # âncora
    "---\n" + "description: " + "x" * 9000 + "\n---\n",    # > 8 KB
    None, b"---\n---\n",
])
def test_frontmatter_ilegivel(texto):
    assert ag.frontmatter(texto) is None


def test_frontmatter_bloco_texto_em_chave_livre_e_vazio():
    assert ag.frontmatter("---\ndescription: >\n  longa\n  linha\nname: x\n---\n") == {"description": "",
                                                                                        "name": "x"}
    assert ag.frontmatter("---\n---\ncorpo") == {}


def test_lista():
    assert ag.lista(None) == [] and ag.lista("") == []
    assert ag.lista("[a, 'b', \"c\"]") == ["a", "b", "c"]
    assert ag.lista("Read, Grep") == ["Read", "Grep"] and ag.lista("Read") == ["Read"]
    assert ag.lista(["a", " ", "b"]) == ["a", "b"]


# --- validar_md --------------------------------------------------------------------------------------------------

def test_validar_md_ok():
    assert ag.validar_md(MD, "revisor", {"testes", "estilo", "outra"}) is None
    sem_opcionais = "---\nname: revisor\ndescription: Revê\n---\n"
    assert ag.validar_md(sem_opcionais, "revisor", set()) is None


@pytest.mark.parametrize("troca,erro", [
    (("name: revisor", "name: outro"), "frontmatter inválido"),
    (("model: sonnet", "model: fable"), "modelo inválido"),
    (("model: sonnet", "color: blue"), "frontmatter inválido"),                     # chave fora da lista
    (("model: sonnet", "permissionMode: bypassPermissions"), "frontmatter inválido"),
    (("model: sonnet", "permissionMode: auto"), "frontmatter inválido"),
    (("model: sonnet", "# bypassPermissions"), "frontmatter inválido"),             # em qualquer sítio
    (("tools: Read, Grep, Glob", "tools: Read, Read"), "frontmatter inválido"),
    (("tools: Read, Grep, Glob", "tools: Bash(rm *)"), "frontmatter inválido"),
    (("tools: Read, Grep, Glob", "tools: " + ", ".join(f"T{chr(97 + i)}x" for i in range(21))),
     "frontmatter inválido"),
    (("  - estilo", "  - nenhuma"), "skill inexistente"),
    (("description: Revê PRs com cuidado", "description: a: b"), "frontmatter inválido"),
    (("description: Revê PRs com cuidado", "description: " + "x" * 301), "frontmatter inválido"),
    (("description: Revê PRs com cuidado", "description:"), "frontmatter inválido"),
    (("name: revisor", "name: revisor\n  continua"), "frontmatter inválido"),
    (("Corpo", "Corpo \u202e"), "frontmatter inválido"),                           # invisível no corpo
])
def test_validar_md_recusas(troca, erro):
    assert ag.validar_md(MD.replace(*troca), "revisor", {"testes", "estilo"}) == erro


def test_validar_md_limites():
    assert ag.validar_md(MD.replace("Revê PRs com cuidado", "x" * 300), "revisor", {"testes", "estilo"}) is None
    grande = MD + "x" * (ag.CAND_MAX - len(MD.encode()) + 1)
    assert ag.validar_md(grande, "revisor", {"testes", "estilo"}) == "frontmatter inválido"
    zwj = MD.replace("Corpo", "Corpo \U0001f469\u200d\U0001f4bb")
    assert ag.validar_md(zwj, "revisor", {"testes", "estilo"}) is None


# --- reescrita ---------------------------------------------------------------------------------------------------

def _diff(a: bytes, b: bytes) -> list[tuple[bytes, bytes]]:
    la, lb = a.split(b"\n"), b.split(b"\n")
    return [(x, y) for x, y in zip(la, lb) if x != y]


def test_reescrever_modelo_so_a_linha():
    raw = MD.encode()
    novo = ag.reescrever_modelo(raw, "haiku")
    assert _diff(raw, novo) == [(b"model: sonnet", b"model: haiku")] and len(novo) == len(raw) - 1
    tirado = ag.reescrever_modelo(raw, "")
    assert tirado == raw.replace(b"model: sonnet\n", b"")


def test_reescrever_modelo_sem_campo_e_crlf():
    raw = b"---\r\nname: x\r\ndescription: d\r\n---\r\ncorpo\r\n"
    novo = ag.reescrever_modelo(raw, "opus")
    assert novo == b"---\r\nname: x\r\ndescription: d\r\nmodel: opus\r\n---\r\ncorpo\r\n"
    assert ag.reescrever_modelo(raw, "") == raw
    with pytest.raises(ValueError):
        ag.reescrever_modelo(b"sem frontmatter", "opus")


def test_reescrever_skills_0_1_n():
    raw = MD.encode()
    assert ag.reescrever_skills(raw, []) == raw.replace(b"skills:\n  - testes\n  - estilo\n", b"")
    um = ag.reescrever_skills(raw, ["testes"])
    assert um == raw.replace(b"skills:\n  - testes\n  - estilo\n", b"skills: testes\n")
    tres = ag.reescrever_skills(um, ["a", "b", "c"])
    assert tres == raw.replace(b"skills:\n  - testes\n  - estilo\n", b"skills:\n  - a\n  - b\n  - c\n")
    assert ag.frontmatter(tres.decode())["skills"] == ["a", "b", "c"]
    # sem campo: entra antes do fecho; o corpo fica byte a byte
    sem = b"---\nname: x\n---\ncorpo: com --- e skills:\n"
    assert ag.reescrever_skills(sem, ["a"]) == b"---\nname: x\nskills: a\n---\ncorpo: com --- e skills:\n"


def test_reescrever_skills_mantem_indentacao_e_comentarios():
    raw = b"---\nname: x\nskills:\n    - a\n    # nota\n    - b\nmodel: haiku\n---\n"
    assert ag.reescrever_skills(raw, ["c", "d"]) == b"---\nname: x\nskills:\n    - c\n    - d\nmodel: haiku\n---\n"


# --- tapar e texto -----------------------------------------------------------------------------------------------

def test_tapar():
    falso = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    s = ag.tapar(f"token {falso} e API_SECRET=abc123 e password: 'x y'")
    assert falso not in s and "abc123" not in s and "x y" not in s and s.count("[tapado]") == 3


def test_texto_ok():
    assert ag.texto_ok("a\nb\tc", 1, 10, True) and not ag.texto_ok("a\nb", 1, 10, False)
    assert not ag.texto_ok("", 1, 10, True) and not ag.texto_ok("x" * 11, 1, 10, True)
    assert not ag.texto_ok("a\rb", 1, 10, True) and not ag.texto_ok("a\u200bb", 1, 10, True)
    assert not ag.texto_ok("a\u2028b", 1, 10, True) and not ag.texto_ok(5, 0, 10, True)
    assert ag.texto_ok("a\u200db", 1, 10, False)


# --- leitura do disco --------------------------------------------------------------------------------------------

def test_ler_bytes(tmp_path):
    f = tmp_path / "a.md"
    f.write_bytes(b"x" * 10)
    assert ag.ler_bytes(f, 10) == b"x" * 10 and ag.ler_bytes(f, 9) is None
    (tmp_path / "l.md").symlink_to(f)
    assert ag.ler_bytes(tmp_path / "l.md", 100) is None and ag.ler_bytes(tmp_path, 100) is None
    (tmp_path / ".env").write_text("X=1")
    assert ag.ler_bytes(tmp_path / ".env", 100) is None and ag.ler_bytes(tmp_path / "nada", 10) is None


def _squad(tmp_path) -> Path:
    base = tmp_path / "dev"
    (base / "agents").mkdir(parents=True)
    (base / "agents" / "revisor.md").write_text(MD)
    (base / "agents" / "notas.txt").write_text("x")
    (base / "agents" / "Mau.md").write_text(MD)
    (base / "agents" / "longo.md").write_bytes(b"x" * (ag.MD_MAX + 1))
    (base / "agents" / "ligado.md").symlink_to(base / "agents" / "revisor.md")
    for s in ("estilo", "testes"):
        (base / "skills" / s).mkdir(parents=True)
        (base / "skills" / s / "SKILL.md").write_text("---\nname: x\n---\n")
    (base / "skills" / "vazia").mkdir()
    (tmp_path / "fora").mkdir()
    (tmp_path / "fora" / "SKILL.md").write_text("x")
    (base / "skills" / "ligada").symlink_to(tmp_path / "fora")
    return base


def test_agentes_de_e_skills_de(tmp_path):
    base = _squad(tmp_path)
    got = dict(ag.agentes_de(base))
    assert set(got) == {"agents/revisor.md", "agents/longo.md", "agents/ligado.md"}
    assert got["agents/revisor.md"] == MD.encode() and got["agents/longo.md"] is None
    assert got["agents/ligado.md"] is None
    assert ag.skills_de(base) == ["estilo", "testes"]
    assert ag.agentes_de(tmp_path / "nada") == [] and ag.skills_de(tmp_path / "nada") == []
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "agents").symlink_to(base / "agents")
    assert ag.agentes_de(tmp_path / "x") == []


def test_agente_publico_sem_corpo():
    a = ag.agente_publico("agents/revisor.md", MD.encode())
    assert a == {"nome": "revisor", "ficheiro": "agents/revisor.md", "sha256": ag._sha(MD.encode()),
                 "description": "Revê PRs com cuidado", "model": "sonnet", "tools": ["Read", "Grep", "Glob"],
                 "skills": ["testes", "estilo"], "permissionMode": None, "lead": False}
    assert "Corpo" not in json.dumps(a)


@pytest.mark.parametrize("raw", [None, b"x" * (ag.MD_MAX + 1), b"sem fm", b"\xff\xfe", b"---\nmodel: [a]\n---\n",
                                 b"---\nmodel: -x\n---\n", b"---\nname: a\ndescription: x --- y\n---\n"])
def test_agente_publico_ilegivel(raw):
    assert ag.agente_publico("agents/x.md", raw) == {"ficheiro": "agents/x.md", "ilegivel": True}


def test_agente_publico_corta():
    md = "---\nname: x\ndescription: " + "d" * 400 + "\ntools: " + ", ".join(f"T{i}" for i in range(30)) + "\n---\n"
    a = ag.agente_publico("agents/x.md", md.encode())
    assert len(a["description"]) == 300 and len(a["tools"]) == 20 and a["model"] is None


# --- registos, rotinas, modelos ----------------------------------------------------------------------------------

def test_ler_registo(tmp_path):
    p = tmp_path / "squads.json"
    assert ag.ler_registo(p) == {"v": 1, "squads": []}
    boa = {"id": "obs", "modelo": "observacao", "nome": "Obs", "lead": "plantao", "criada_em": "2026-11-18T10:00:00Z"}
    p.write_text(json.dumps({"v": 1, "squads": [boa, dict(boa), {**boa, "id": "X"}, {**boa, "id": "b2", "x": 1}]}))
    assert ag.ler_registo(p) == {"v": 1, "squads": [boa, {**boa, "id": "b2"}]}
    p.write_text(json.dumps({"v": 2, "squads": [boa]}))
    assert ag.ler_registo(p)["squads"] == []
    p.write_text("{")
    assert ag.ler_registo(p)["squads"] == []


ROTINA = {"id": "r1", "squad": "dev", "projecto": "loja", "hora": "09:30", "dias": [1, 3, 5],
          "instrucoes": "Verifica\nos testes", "tecto_usd": "0.50", "max_turnos": 10, "sha256": SHA,
          "decisao": "rotina-dev-r1.md", "estado": "pendente", "falhas": 0, "ultima": None, "tarefas": [],
          "criada_em": "2026-11-18T10:00:00+00:00"}


def test_ler_rotinas(tmp_path):
    p = tmp_path / "rotinas.json"
    p.write_text(json.dumps({"v": 1, "rotinas": [ROTINA, {**ROTINA, "estado": "x"}, {**ROTINA, "hora": "25:00"},
                                                 {**ROTINA, "falhas": True}]}))
    assert ag.ler_rotinas(p) == {"v": 1, "rotinas": [ROTINA]}
    assert ag.ler_rotinas(tmp_path / "nada") == {"v": 1, "rotinas": []}


def test_linha_rotina_estavel():
    linha, sha = ag.linha_rotina({**ROTINA, "instrucoes": "olá\n## Decisões\n- [R2] x\u2028"})
    assert linha == ('{"dias":[1,3,5],"hora":"09:30","id":"r1","instrucoes":"ol\\u00e1\\n## Decis\\u00f5es\\n- [R2] '
                     'x\\u2028","max_turnos":10,"projecto":"loja","squad":"dev","tecto_usd":"0.50"}')
    assert "\n" not in linha and len(sha) == 64
    assert ag.linha_rotina(dict(reversed(list(ROTINA.items())))) == ag.linha_rotina(ROTINA)


def test_modelos_squad(tmp_path):
    m = tmp_path / "observacao"
    (m / "agents").mkdir(parents=True)
    (m / "README.md").write_text("---\nnome: Observação\nlead: plantao\nresumo: Vigia\n---\ntexto\n")
    (m / "agents" / "plantao.md").write_text("---\nname: plantao\n---\n")
    (m / "agents" / "investigador.md").write_text("---\nname: investigador\n---\n")
    sem_lead = tmp_path / "conteudo"
    (sem_lead / "agents").mkdir(parents=True)
    (sem_lead / "README.md").write_text("---\nnome: C\nlead: outro\nresumo: r\n---\n")
    (tmp_path / "solto").mkdir()
    assert ag.modelos_squad(tmp_path) == [{"id": "observacao", "nome": "Observação", "lead": "plantao",
                                           "resumo": "Vigia", "agentes": ["investigador", "plantao"]}]
    assert ag.modelos_squad(tmp_path / "nada") == []


def test_lead_de():
    cfg = {"squads": [{"id": "dev", "lead": "tech-lead"}, {"id": "obs", "lead": "chefe"}]}
    reg = {"squads": [{"id": "obs", "lead": "plantao"}, {"id": "nova", "lead": "editor"}]}
    assert ag.lead_de(cfg, reg, "dev") == "tech-lead" and ag.lead_de(cfg, reg, "obs") == "plantao"
    assert ag.lead_de(cfg, reg, "nova") == "editor" and ag.lead_de(cfg, reg, "x") is None


# --- pedidos -----------------------------------------------------------------------------------------------------

STEM = "20261118T100000-0123abcd"
ARGS = {
    "modelo": {"squad": "dev", "agente": "revisor", "sha256": SHA, "modelo": ""},
    "skills": {"squad": "dev", "agente": "revisor", "sha256": SHA, "skills": ["testes", "estilo"]},
    "agente_remover": {"squad": "dev", "agente": "revisor", "sha256": SHA},
    "agente_rascunho": {"squad": "dev", "nome": "novo", "descricao": "Faz\ncoisas"},
    "agente_criar": {"squad": "dev", "nome": "novo", "sha256": None, "md": MD},
    "agente_ensaio": {"squad": "dev", "nome": "novo", "sha256": SHA, "projecto": "loja", "tarefa": "Lê o README"},
    "agente_activar": {"squad": "dev", "nome": "novo", "sha256": SHA},
    "agente_descartar": {"squad": "dev", "nome": "novo"},
    "squad_rascunho": {"squad": "obs", "modelo": "observacao", "missao": "Vigiar"},
    "squad_criar": {"squad": "obs", "sha256": SHA},
    "squad_ensaio": {"squad": "obs", "sha256": SHA, "projecto": "loja", "tarefa": "x"},
    "squad_descartar": {"squad": "obs"},
    "squad_desactivar": {"squad": "obs"},
    "squad_conversa": {"squad": "obs", "texto": "Quero responder\naos clientes"},
    "rotina_pedir": {"squad": "dev", "projecto": "loja", "hora": "09:30", "dias": [1, 7], "instrucoes": "x",
                     "tecto_usd": "0.10", "max_turnos": 1},
    "rotina_retomar": {"squad": "dev", "rotina": "r1"},
    "rotina_cancelar": {"squad": "dev", "rotina": "r999"},
}


def pedido(accao, args):
    return {"v": 1, "id": STEM, "accao": accao, "args": args, "pedido_em": "2026-11-18T10:00:00+00:00",
            "ip": "1.2.3.4", "ua": "x"}


def test_accoes_cobertas():
    assert set(ARGS) == set(ag.ACCOES) and set(ag.ACCOES_IA) <= set(ag.ACCOES)
    assert len(ag.ERROS) == len(set(ag.ERROS)) == 32


@pytest.mark.parametrize("texto,ok", [("x", True), ("a" * 1000, True), ("a" * 1001, False), ("", False),
                                      ("   ", False), ("linha\ncom\ttab", True), ("bidi\u202e", False)])
def test_squad_conversa_texto_limites(texto, ok):
    erro = ag.validar_args("squad_conversa", {"squad": "obs", "texto": texto})
    assert (erro is None) is ok and (ok or erro == "pedido ilegível")
    assert ag.alvo("squad_conversa", {"squad": "obs", "texto": "x"}) == "squad:obs"
    assert ag.ACCOES_IA["squad_conversa"] == 0.15


def test_modelo_negocio():
    reg = {"v": 1, "squads": [{"id": "apoio", "modelo": "atendimento"}, {"id": "vendas", "modelo": "comercial"},
                              {"id": "obs", "modelo": "observacao"}]}
    assert ag.MODELOS_NEGOCIO == ("atendimento", "comercial")
    assert [ag.modelo_negocio(reg, s) for s in ("apoio", "vendas", "obs", "outra")] == [
        "atendimento", "comercial", None, None]
    assert ag.modelo_negocio({}, "apoio") is None and ag.modelo_negocio(None, "apoio") is None


@pytest.mark.parametrize("accao", sorted(ARGS))
def test_validar_pedido_ok_e_chave_a_mais_ou_a_menos(accao):
    args = ARGS[accao]
    assert tuple(args) == ag.ACCOES[accao]
    assert ag.validar_pedido(pedido(accao, args), STEM) is None
    assert ag.validar_pedido(pedido(accao, {**args, "extra": 1}), STEM) == "pedido ilegível"
    menos = dict(args)
    menos.popitem()
    assert ag.validar_pedido(pedido(accao, menos), STEM) == "pedido ilegível"


def test_validar_pedido_forma():
    p = pedido("modelo", ARGS["modelo"])
    assert ag.validar_pedido(p, "outro") == "pedido ilegível"
    assert ag.validar_pedido({**p, "v": 2}, STEM) == "versão desconhecida"
    assert ag.validar_pedido({**p, "v": True}, STEM) == "versão desconhecida"
    assert ag.validar_pedido(dict(reversed(list(p.items()))), STEM) == "pedido ilegível"
    assert ag.validar_pedido({**p, "accao": "x"}, STEM) == "pedido ilegível"
    assert ag.validar_pedido({**p, "pedido_em": "2026-11-18T10:00:00"}, STEM) == "pedido ilegível"
    assert ag.validar_pedido({**p, "ua": "u" * 201}, STEM) == "pedido ilegível"
    assert ag.validar_pedido({**p, "ip": "i" * 64, "ua": "u" * 200}, STEM) is None
    assert ag.validar_pedido([], STEM) == "pedido ilegível" and ag.validar_pedido(None, STEM) == "pedido ilegível"


@pytest.mark.parametrize("accao,k,v,erro", [
    ("modelo", "modelo", "fable", "modelo inválido"),
    ("modelo", "modelo", None, "modelo inválido"),
    ("modelo", "sha256", "A" * 64, "pedido ilegível"),
    ("modelo", "squad", "D", "pedido ilegível"),
    ("modelo", "agente", "a" * 41, "pedido ilegível"),
    ("skills", "skills", ["aa", "aa"], "pedido ilegível"),
    ("skills", "skills", [f"s{i:02d}" for i in range(21)], "pedido ilegível"),
    ("agente_rascunho", "descricao", "x" * 2001, "pedido ilegível"),
    ("agente_rascunho", "descricao", "  \n ", "pedido ilegível"),
    ("agente_rascunho", "descricao", "a\u202eb", "pedido ilegível"),
    ("agente_criar", "md", "x" * (ag.CAND_MAX + 1), "pedido ilegível"),
    ("agente_activar", "sha256", None, "pedido ilegível"),
    ("agente_ensaio", "projecto", "Loja", "projecto inválido"),
    ("rotina_pedir", "hora", "24:00", "pedido ilegível"),
    ("rotina_pedir", "hora", "9:30", "pedido ilegível"),
    ("rotina_pedir", "dias", [], "pedido ilegível"),
    ("rotina_pedir", "dias", [3, 1], "pedido ilegível"),
    ("rotina_pedir", "dias", [1, 1], "pedido ilegível"),
    ("rotina_pedir", "dias", [0], "pedido ilegível"),
    ("rotina_pedir", "dias", [8], "pedido ilegível"),
    ("rotina_pedir", "dias", [True], "pedido ilegível"),
    ("rotina_pedir", "tecto_usd", "0.09", "pedido ilegível"),
    ("rotina_pedir", "tecto_usd", "5.01", "pedido ilegível"),
    ("rotina_pedir", "tecto_usd", "0.5", "pedido ilegível"),
    ("rotina_pedir", "tecto_usd", 1, "pedido ilegível"),
    ("rotina_pedir", "max_turnos", 0, "pedido ilegível"),
    ("rotina_pedir", "max_turnos", 31, "pedido ilegível"),
    ("rotina_pedir", "max_turnos", "5", "pedido ilegível"),
    ("rotina_pedir", "instrucoes", "x" * 2001, "pedido ilegível"),
    ("rotina_retomar", "rotina", "r0", "pedido ilegível"),
    ("rotina_retomar", "rotina", "r1000", "pedido ilegível"),
])
def test_validar_pedido_limites(accao, k, v, erro):
    assert ag.validar_pedido(pedido(accao, {**ARGS[accao], k: v}), STEM) == erro


@pytest.mark.parametrize("k,v", [("hora", "00:00"), ("hora", "23:59"), ("dias", [1, 2, 3, 4, 5, 6, 7]),
                                 ("tecto_usd", "5.00"), ("max_turnos", 30), ("instrucoes", "x" * 2000)])
def test_validar_pedido_limites_aceites(k, v):
    assert ag.validar_pedido(pedido("rotina_pedir", {**ARGS["rotina_pedir"], k: v}), STEM) is None


def test_alvo():
    assert ag.alvo("modelo", ARGS["modelo"]) == "agente:dev/revisor"
    assert ag.alvo("agente_remover", ARGS["agente_remover"]) == "agente:dev/revisor"
    assert ag.alvo("agente_ensaio", ARGS["agente_ensaio"]) == "cand:dev/novo"
    assert ag.alvo("squad_criar", ARGS["squad_criar"]) == "squad:obs"
    assert ag.alvo("rotina_pedir", ARGS["rotina_pedir"]) == "rotina:dev"
    assert ag.alvo("rotina_cancelar", ARGS["rotina_cancelar"]) == "rotina:dev/r999"


def test_so_stdlib_e_officecfg():
    src = Path(ag.__file__).read_text(encoding="utf-8")
    proibidos = ("import server", "import biblioteca", "import arquivo", "from runner")
    assert not any(p in src for p in proibidos)
    assert os.path.basename(ag.__file__) == "agentes.py"
