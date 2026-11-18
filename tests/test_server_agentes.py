"""Squads e agentes do lado do Office (ADR-017…019): GET /api/agentes (só frontmatter, nunca corpos), o candidato a
pedido e o POST que só escreve pedidos v1 na fila `agentes-fila`. Servidor real numa porta livre, pastas temporárias."""
import http.client
import json
import os
import threading
from http.server import ThreadingHTTPServer
from unittest import mock

import pytest

import agentes
import server
from tests import cfg as tcfg

FRASE = "FRASE-UNICA-DO-CORPO-91c2"
SHA = "b" * 64
OFFICE_CFG = tcfg.ler("""
fuso: Europe/Lisbon
squads:
  - id: dev
    nome: Dev
    modelo: haiku
  - id: ops
    nome: Ops
    lead: chefe-ops
    modos: [goal]
  - id: obs
    nome: Observação
    lead: plantao
    modos: [task]
  - id: mkt
    nome: Mkt
    formato: sessao
projectos:
  - id: loja
    nome: Loja
  - id: blog
    nome: Blog
runner:
  modelo: sonnet
""")


def md(nome, extra="", corpo=FRASE):
    return f"---\nname: {nome}\ndescription: Agente {nome}\ntools: Read, Grep\n{extra}---\n{corpo}\n"


def escrever(p, texto):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(texto if isinstance(texto, bytes) else texto.encode())


class Amb:
    def __init__(self, tmp):
        self.base, self.rasc, self.fila, self.modelos = (tmp / "agentes", tmp / "agentes-rascunhos",
                                                         tmp / "agentes-fila", tmp / "modelos")
        for p in (self.base, self.rasc, self.fila, self.modelos):
            p.mkdir()
        dev = self.base / "dev"
        escrever(dev / "agents" / "tech-lead.md", md("tech-lead", "model: opus\n"))
        escrever(dev / "agents" / "revisor.md", md("revisor", "skills:\n  - testes\n"))
        escrever(dev / "agents" / "grande.md", md("grande") + "x" * (80 * 1024))
        escrever(dev / "agents" / "semfm.md", "só corpo " + FRASE)
        escrever(dev / "agents" / "traco.md", f"---\nname: traco\ndescription: a --- b\n---\n{FRASE}\n")
        (dev / "agents" / "ligado.md").symlink_to(dev / "agents" / "revisor.md")
        escrever(dev / "skills" / "testes" / "SKILL.md", f"---\nname: testes\ndescription: Corre testes\n---\n{FRASE}")
        escrever(dev / "skills" / "estilo" / "SKILL.md", "sem frontmatter")
        escrever(dev / "CLAUDE.md", FRASE)
        escrever(self.base / "obs" / "agents" / "plantao.md", md("plantao"))
        escrever(self.base / "nova" / "agents" / "editor.md", md("editor"))
        escrever(self.base / "squads.json", json.dumps({"v": 1, "squads": [
            {"id": "obs", "modelo": "observacao", "nome": "Obs", "lead": "plantao", "criada_em": "2026-11-18T10:00"},
            {"id": "nova", "modelo": "conteudo", "nome": "Nova", "lead": "editor", "criada_em": "2026-11-18T10:00"}]}))
        escrever(self.rasc / "agentes" / "dev" / "cand.md", md("cand"))
        escrever(self.rasc / "agentes" / "dev" / "cand.ensaio.json", json.dumps({
            "v": 1, "sha256": SHA, "estado": "ok", "motivo": None, "relatorio": "Leu o README.",
            "nao_ensaiadas": ["Bash"], "custo_usd": 0.12, "em": "2026-11-18T10:00:00+00:00"}))
        escrever(self.rasc / "squads" / "rasq" / "rascunho.json", json.dumps({
            "v": 1, "squad": "rasq", "modelo": "observacao", "nome": "Rasq", "lead": "plantao", "missao": "Vigiar",
            "ficheiros": {"CLAUDE.md": FRASE, "agents/plantao.md": md("plantao")}, "criado_em": "2026-11-18"}))
        escrever(self.modelos / "observacao" / "README.md",
                 "---\nnome: Observação\nlead: plantao\nresumo: Vigia\n---\n")
        escrever(self.modelos / "observacao" / "agents" / "plantao.md", md("plantao"))
        rot = {"id": "r1", "squad": "dev", "projecto": "loja", "hora": "09:30", "dias": [1, 3],
               "instrucoes": "Revê", "tecto_usd": "0.50", "max_turnos": 10, "sha256": SHA,
               "decisao": "rotina-dev-r1.md", "estado": "pausada", "falhas": 3, "ultima": None, "tarefas": [],
               "criada_em": "2026-11-18T10:00:00+00:00"}
        escrever(self.base / "rotinas.json", json.dumps({"v": 1, "rotinas": [rot]}))


@pytest.fixture
def amb(tmp_path):
    a = Amb(tmp_path)
    p = mock.patch.multiple(server, AGENTES_DIR=a.base, AGENTES_RASCUNHOS_DIR=a.rasc, AGENTES_FILA_DIR=a.fila)
    pm = mock.patch.object(agentes, "MODELOS_DIR", a.modelos)
    p.start()
    pm.start()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    a.host = f"127.0.0.1:{httpd.server_address[1]}"
    yield a
    httpd.shutdown()
    httpd.server_close()
    pm.stop()
    p.stop()


def req(a, path, method="GET", headers=None, body=None):
    c = http.client.HTTPConnection(a.host, timeout=10)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        c.close()


def get(a, path="/api/agentes"):
    code, h, body = req(a, path)
    return code, h, body


def post(a, data=None, raw=None, **headers):
    h = {"Content-Type": "application/json", "Origin": f"http://{a.host}", "User-Agent": "teste/1"}
    h.update({k.replace("_", "-"): v for k, v in headers.items()})
    code, _, body = req(a, "/api/agentes/pedido", "POST", {k: v for k, v in h.items() if v is not None},
                        raw if raw is not None else json.dumps(data).encode())
    return code, json.loads(body or b"null")


def corpo(accao, **args):
    return {"token": server.TOKEN, "accao": accao, "args": args}


def sha_de(p):
    return agentes._sha(p.read_bytes())


def pedidos(a):
    return sorted(n for n in os.listdir(a.fila) if agentes.NOME_PEDIDO.fullmatch(n))


# --- GET ---------------------------------------------------------------------------------------------------------

def test_get_sem_corpos_e_ilegiveis(amb):
    code, h, body = get(amb)
    assert code == 200 and h["cache-control"] == "no-store"
    assert FRASE.encode() not in body   # CA-12: nunca o corpo (agentes, skills, CLAUDE.md, rascunhos)
    d = json.loads(body)
    assert d["v"] == 1 and d["escrita"] is True and d["projectos"] == ["loja", "blog"]
    assert d["modelos"] == [{"id": "observacao", "nome": "Observação", "lead": "plantao", "resumo": "Vigia",
                             "agentes": ["plantao"]}]
    dev = d["squads"][0]
    assert dev["id"] == "dev" and dev["origem"] == "office" and dev["formato"] == "worktree"
    assert dev["lead"] == "tech-lead" and dev["sessao"] == {"modelo": "haiku", "fonte": "squad"}
    assert dev["instrucoes"] is True and dev["modelo_squad"] is None
    por = {a["ficheiro"]: a for a in dev["agentes"]}
    for f in ("grande", "semfm", "traco", "ligado"):   # CA-13
        assert por[f"agents/{f}.md"] == {"ficheiro": f"agents/{f}.md", "ilegivel": True}
    assert por["agents/tech-lead.md"]["lead"] is True and por["agents/tech-lead.md"]["model"] == "opus"
    rev = por["agents/revisor.md"]
    assert rev == {"nome": "revisor", "ficheiro": "agents/revisor.md",
                   "sha256": sha_de(amb.base / "dev" / "agents" / "revisor.md"), "description": "Agente revisor",
                   "model": None, "tools": ["Read", "Grep"], "skills": ["testes"], "permissionMode": None,
                   "lead": False}
    assert dev["skills"] == [{"nome": "estilo", "ficheiro": "skills/estilo/SKILL.md", "description": None},
                             {"nome": "testes", "ficheiro": "skills/testes/SKILL.md", "description": "Corre testes"}]
    cand = dev["candidatos"][0]
    assert cand["nome"] == "cand" and cand["sha256"] == sha_de(amb.rasc / "agentes" / "dev" / "cand.md")
    assert cand["ensaio"]["estado"] == "ok" and "v" not in cand["ensaio"]
    assert dev["rotinas"] == [{"id": "r1", "projecto": "loja", "hora": "09:30", "dias": [1, 3], "instrucoes": "Revê",
                               "tecto_usd": "0.50", "max_turnos": 10, "estado": "pausada", "falhas": 3,
                               "ultima": None, "motivo": None}]


def test_get_origens_e_sessao(amb):
    d = json.loads(get(amb)[2])
    s = {x["id"]: x for x in d["squads"]}
    assert [x["id"] for x in d["squads"]] == ["dev", "ops", "obs", "mkt", "nova", "rasq"]
    assert s["ops"]["sessao"] == {"modelo": "sonnet", "fonte": "runner"} and s["ops"]["agentes"] == []
    assert s["obs"]["origem"] == "office" and s["obs"]["modelo_squad"] == "observacao"
    assert s["nova"]["origem"] == "registo" and s["nova"]["formato"] is None and s["nova"]["lead"] == "editor"
    assert s["rasq"]["origem"] == "rascunho" and s["rasq"]["rascunho"]["agentes"] == [
        {"nome": "plantao", "description": "Agente plantao"}]
    assert s["rasq"]["rascunho"]["sha256"] == sha_de(amb.rasc / "squads" / "rasq" / "rascunho.json")
    server.configurar(tcfg.ler("squads:\n  - id: dev\n    nome: Dev\nprojectos:\n  - id: loja\n    nome: L\n"))
    assert json.loads(get(amb)[2])["squads"][0]["sessao"] == {"modelo": None, "fonte": "omissao"}


def test_get_pedidos_e_estados(amb):
    assert post(amb, corpo("agente_descartar", squad="dev", nome="cand"))[0] == 202
    assert post(amb, corpo("rotina_retomar", squad="dev", rotina="r1"))[0] == 202
    por = {json.loads((amb.fila / n).read_text())["accao"]: n for n in pedidos(amb)}
    a, b = por["agente_descartar"], por["rotina_retomar"]
    escrever(amb.fila / a.replace(".json", ".feito.json"), json.dumps({"erro": "candidato inexistente"}))
    d = json.loads(get(amb)[2])
    est = {p["accao"]: p for p in d["pedidos"]}
    assert est["agente_descartar"]["estado"] == "erro" and est["agente_descartar"]["erro"] == "candidato inexistente"
    assert est["agente_descartar"]["alvo"] == "cand:dev/cand"
    assert est["rotina_retomar"]["estado"] == "pendente" and est["rotina_retomar"]["erro"] is None
    escrever(amb.fila / b.replace(".json", ".feito.json"), json.dumps({"ok": True}))
    assert {p["estado"] for p in json.loads(get(amb)[2])["pedidos"]} == {"erro", "ok"}


def test_get_sem_pastas(amb, tmp_path):
    with mock.patch.multiple(server, AGENTES_DIR=tmp_path / "x", AGENTES_RASCUNHOS_DIR=tmp_path / "y",
                             AGENTES_FILA_DIR=tmp_path / "z"):
        code, _, body = get(amb)
    d = json.loads(body)
    assert code == 200 and d["escrita"] is False and d["pedidos"] == []
    assert [s["agentes"] for s in d["squads"]] == [[], [], [], []]


def test_candidato(amb):
    code, _, body = get(amb, "/api/agentes/candidato?squad=dev&nome=cand")
    d = json.loads(body)
    assert code == 200 and d == {"md": md("cand"), "sha256": sha_de(amb.rasc / "agentes" / "dev" / "cand.md")}
    (amb.rasc / "agentes" / "dev" / "liga.md").symlink_to(amb.rasc / "agentes" / "dev" / "cand.md")
    for q in ("squad=dev&nome=nada", "squad=dev&nome=liga", "squad=dev&nome=../x", "squad=X&nome=cand", "",
              "squad=dev"):
        assert get(amb, f"/api/agentes/candidato?{q}")[0] == 404, q


# --- POST --------------------------------------------------------------------------------------------------------

def test_post_202_grava_v1(amb):
    sha = sha_de(amb.base / "dev" / "agents" / "revisor.md")
    code, out = post(amb, corpo("modelo", modelo="opus", sha256=sha, agente="revisor", squad="dev"))
    assert code == 202 and out["accao"] == "modelo"
    (nome,) = pedidos(amb)
    p = json.loads((amb.fila / nome).read_text())
    assert nome == out["id"] + ".json" and tuple(p) == agentes.CHAVES
    assert list(p["args"]) == ["squad", "agente", "sha256", "modelo"]   # ordem do contrato, não a do corpo
    assert p["v"] == 1 and p["ua"] == "teste/1" and p["ip"] == "127.0.0.1"
    assert p["pedido_em"].endswith(("+00:00", "+01:00"))   # fuso da config (Europe/Lisbon)
    assert agentes.validar_pedido(p, out["id"]) is None
    assert not [n for n in os.listdir(amb.fila) if n.endswith(".tmp")]


def test_post_todas_as_accoes_validas(amb):
    sha = sha_de(amb.base / "dev" / "agents" / "revisor.md")
    casos = [
        ("skills", {"squad": "dev", "agente": "revisor", "sha256": sha, "skills": ["testes", "estilo"]}),
        ("agente_remover", {"squad": "dev", "agente": "traco", "sha256": SHA}),
        ("agente_rascunho", {"squad": "dev", "nome": "novo", "descricao": "Faz\ncoisas"}),
        ("agente_criar", {"squad": "dev", "nome": "outro", "sha256": None, "md": md("outro", "skills: testes\n")}),
        ("agente_ensaio", {"squad": "dev", "nome": "cand", "sha256": SHA, "projecto": "blog", "tarefa": "Lê"}),
        ("squad_rascunho", {"squad": "ops", "modelo": "observacao", "missao": "Vigiar"}),
        ("squad_criar", {"squad": "rasq", "sha256": SHA}),
        ("squad_desactivar", {"squad": "nova"}),
        ("rotina_pedir", {"squad": "dev", "projecto": "loja", "hora": "07:00", "dias": [1], "instrucoes": "x",
                          "tecto_usd": "1.00", "max_turnos": 5}),
        ("rotina_cancelar", {"squad": "dev", "rotina": "r1"}),
    ]
    for accao, args in casos:
        code, out = post(amb, corpo(accao, **args))
        assert code == 202, (accao, out)
    assert len(pedidos(amb)) == len(casos)


@pytest.mark.parametrize("accao,args,erro", [
    ("modelo", {"squad": "dev", "agente": "revisor", "sha256": SHA, "modelo": "fable"}, "modelo inválido"),
    ("modelo", {"squad": "dev", "agente": "nada", "sha256": SHA, "modelo": "opus"}, "agente inexistente"),
    ("modelo", {"squad": "zzz", "agente": "revisor", "sha256": SHA, "modelo": "opus"}, "squad desconhecida"),
    ("modelo", {"squad": "dev", "agente": "revisor", "sha256": SHA}, "campos inválidos"),
    ("modelo", {"squad": "dev", "agente": "revisor", "sha256": SHA, "modelo": "", "x": 1}, "campos inválidos"),
    ("skills", {"squad": "dev", "agente": "revisor", "sha256": SHA, "skills": ["nada"]}, "skill inexistente"),
    ("agente_remover", {"squad": "dev", "agente": "tech-lead", "sha256": SHA}, "o lead não pode ser removido"),
    ("agente_rascunho", {"squad": "dev", "nome": "revisor", "descricao": "x"}, "já existe"),
    ("agente_criar", {"squad": "dev", "nome": "novo", "sha256": None,
                      "md": md("novo", "permissionMode: bypassPermissions\n")}, "frontmatter inválido"),
    ("agente_criar", {"squad": "dev", "nome": "novo", "sha256": SHA, "md": md("novo")}, "candidato inexistente"),
    ("agente_activar", {"squad": "dev", "nome": "nada", "sha256": SHA}, "candidato inexistente"),
    ("agente_ensaio", {"squad": "dev", "nome": "cand", "sha256": SHA, "projecto": "outro", "tarefa": "x"},
     "projecto inválido"),
    ("squad_rascunho", {"squad": "dev", "modelo": "observacao", "missao": "x"}, "squad já tem agentes"),
    ("squad_rascunho", {"squad": "zz", "modelo": "nenhum", "missao": "x"}, "modelo de squad inexistente"),
    ("squad_criar", {"squad": "zz", "sha256": SHA}, "rascunho inexistente"),
    ("squad_desactivar", {"squad": "dev"}, "squad não registada"),
    ("rotina_pedir", {"squad": "mkt", "projecto": "loja", "hora": "07:00", "dias": [1], "instrucoes": "x",
                      "tecto_usd": "1.00", "max_turnos": 5}, "squad não lançável"),
    ("rotina_pedir", {"squad": "ops", "projecto": "loja", "hora": "07:00", "dias": [1], "instrucoes": "x",
                      "tecto_usd": "1.00", "max_turnos": 5}, "squad não lançável"),
    ("rotina_pedir", {"squad": "obs", "projecto": "loja", "hora": "07:00", "dias": [1], "instrucoes": "x",
                      "tecto_usd": "1.00", "max_turnos": 5}, "sem ensaio ok desta versão"),
    ("rotina_pedir", {"squad": "dev", "projecto": "loja", "hora": "07:00", "dias": [1], "instrucoes": "x",
                      "tecto_usd": "5.01", "max_turnos": 5}, "campos inválidos"),
    ("rotina_retomar", {"squad": "dev", "rotina": "r2"}, "rotina inexistente"),
    ("nada", {"squad": "dev"}, "campos inválidos"),
])
def test_post_400(amb, accao, args, erro):
    code, out = post(amb, corpo(accao, **args))
    assert (code, out) == (400, {"error": erro})
    assert pedidos(amb) == []


def test_post_400_forma_do_corpo(amb):
    assert post(amb, {**corpo("squad_descartar", squad="rasq"), "extra": 1}) == (400, {"error": "campos inválidos"})
    assert post(amb, {"token": server.TOKEN, "accao": "squad_descartar"}) == (400, {"error": "campos inválidos"})
    assert post(amb, raw=b"[1]")[0] == 400


def test_post_rotinas_limites_e_ensaio_do_lead(amb):
    rot = json.loads((amb.base / "rotinas.json").read_text())
    base = rot["rotinas"][0]
    rot["rotinas"] = [dict(base, id=f"r{i}", estado="aprovada") for i in range(1, 6)]
    rot["rotinas"].append(dict(base, id="r9", estado="cancelada"))
    escrever(amb.base / "rotinas.json", json.dumps(rot))
    args = {"squad": "dev", "projecto": "loja", "hora": "07:00", "dias": [1, 2], "instrucoes": "x",
            "tecto_usd": "0.10", "max_turnos": 30}
    assert post(amb, corpo("rotina_pedir", **args)) == (400, {"error": "limite de rotinas"})
    assert post(amb, corpo("rotina_retomar", squad="dev", rotina="r1")) == (400, {"error": "rotina não pausada"})
    assert post(amb, corpo("rotina_cancelar", squad="dev", rotina="r9")) == (400, {"error": "rotina inexistente"})
    # squad criada por modelo: rotina só com ensaio ok do lead actual (mesmo sha256)
    lead = sha_de(amb.base / "obs" / "agents" / "plantao.md")
    ensaio = {"v": 1, "sha256": lead, "estado": "ok", "motivo": None, "relatorio": "", "nao_ensaiadas": [],
              "custo_usd": None, "em": "2026-11-18T10:00:00+00:00"}
    escrever(amb.rasc / "squads" / "obs" / "ensaio.json", json.dumps(dict(ensaio, sha256=SHA)))
    assert post(amb, corpo("rotina_pedir", **dict(args, squad="obs")))[0] == 400
    escrever(amb.rasc / "squads" / "obs" / "ensaio.json", json.dumps(ensaio))
    assert post(amb, corpo("rotina_pedir", **dict(args, squad="obs")))[0] == 202


def test_post_403(amb):
    b = corpo("squad_descartar", squad="rasq")
    assert post(amb, {**b, "token": "x"}) == (403, {"error": "token inválido, recarrega"})
    assert post(amb, b, Origin="http://outro.example")[0] == 403
    assert post(amb, b, Content_Type="text/plain")[0] == 403
    assert pedidos(amb) == []


def test_post_409_mesmo_alvo_e_fila_cheia(amb):
    b = corpo("agente_descartar", squad="dev", nome="cand")
    assert post(amb, b)[0] == 202
    # o mesmo alvo com outra acção (cand:dev/cand) também espera
    assert post(amb, corpo("agente_activar", squad="dev", nome="cand", sha256=SHA)) == (
        409, {"error": "já há um pedido pendente para este alvo"})
    (p,) = pedidos(amb)
    escrever(amb.fila / p.replace(".json", ".feito.json"), json.dumps({"ok": True}))
    assert post(amb, b)[0] == 202   # respondido: deixa de estar pendente
    for i in range(49):   # 1 + 49 = 50 pendentes
        assert post(amb, corpo("agente_rascunho", squad="dev", nome=f"n{i:02d}", descricao="x"))[0] == 202
    cheia = post(amb, corpo("agente_rascunho", squad="dev", nome="n99", descricao="x"))
    assert cheia == (409, {"error": "fila cheia"})


def test_post_413(amb):
    grande = corpo("agente_rascunho", squad="dev", nome="novo", descricao="x" * (server.AGENTES_POST_MAX + 1))
    assert post(amb, grande)[0] == 413
    assert pedidos(amb) == []


def test_post_503(amb, tmp_path):
    b = corpo("squad_descartar", squad="rasq")
    with mock.patch.object(server, "AGENTES_FILA_DIR", tmp_path / "nada"):
        assert post(amb, b) == (503, {"error": "fila dos agentes indisponível"})
    if os.geteuid() == 0:
        pytest.skip("root escreve em pastas só de leitura")
    amb.fila.chmod(0o555)
    try:
        assert post(amb, b) == (503, {"error": "fila dos agentes indisponível"})
        assert json.loads(get(amb)[2])["escrita"] is False
    finally:
        amb.fila.chmod(0o755)


def test_post_nao_toca_fora_da_fila(amb):
    antes = {p: p.stat().st_mtime_ns for p in amb.base.rglob("*")} | {p: p.stat().st_mtime_ns
                                                                     for p in amb.rasc.rglob("*")}
    sha = sha_de(amb.base / "dev" / "agents" / "revisor.md")
    assert post(amb, corpo("modelo", squad="dev", agente="revisor", sha256=sha, modelo=""))[0] == 202
    assert post(amb, corpo("agente_criar", squad="dev", nome="cand", sha256=SHA, md=md("cand")))[0] == 202
    depois = {p: p.stat().st_mtime_ns for p in amb.base.rglob("*")} | {p: p.stat().st_mtime_ns
                                                                      for p in amb.rasc.rglob("*")}
    assert antes == depois
