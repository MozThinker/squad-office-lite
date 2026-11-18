"""Conversa de montagem no runner (`squad_conversa`, ADR-020): argv e stdin (CA-5), `conversa.json` só do runner e lido
pelo Office (CA-7), 4 chamadas (CA-8), tecto da conversa e livro (CA-9), recusa e perfil imposto (CA-10), proposta →
rascunho (CA-11), uma conversa de cada vez e limpeza (CA-11a) e ponta a ponta (CA-11b). `claude` falso de tests/bin
(FAKE_CLAUDE_RESULTADOS, _CUSTO, _ARGV, _STDIN), `NOW` fixo."""
import json
import os
from zoneinfo import ZoneInfo

import pytest

import agentes
import conversa
from tests import test_runner_agentes_comum as a
from tests.test_runner_comum import agentes_fila, lancador, office_runner, officecfg

NOW = a.NOW
PERGUNTAS = "=====PERGUNTAS=====\n1. Que caixa de e-mail recebe as mensagens?\n2. Quem aprova as respostas?\n"
PROPOSTA = ("=====PROPOSTA=====\nmodelo: atendimento\ntools: Bash\n=====MISSAO=====\n"
            "Ler as mensagens dos clientes e deixar um rascunho de resposta por mensagem para o dono aprovar.\n"
            "=====FIM=====\n")
NAO_DA = ("=====NAO_DA=====\nmotivo: mexer em dinheiro está fora do catálogo das squads\n"
          "alternativa: uma squad de atendimento que responde às dúvidas sobre pagamentos\n")
TEXTO = "Quero responder aos e-mails dos clientes 5e1d"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    a.c.ambiente(monkeypatch, tmp_path)
    monkeypatch.delenv("OFFICE_MODO", raising=False)
    monkeypatch.delenv("OFFICE_SEGREDOS_DIR", raising=False)
    cfg, path = a.c.config(tmp_path)
    monkeypatch.setattr(lancador, "destacar", lambda *x, **k: 4242)
    (tmp_path / "respostas").mkdir()
    for k, f in (("FAKE_CLAUDE_ARGV", "argv.jsonl"), ("FAKE_CLAUDE_STDIN", "stdin.txt"),
                 ("FAKE_CLAUDE_RESULTADOS", "respostas")):
        monkeypatch.setenv(k, str(tmp_path / f))
    return cfg, path, tmp_path


def respostas(ctx, *textos: str) -> None:
    """As respostas da IA, uma por chamada, por ordem (o falso consome 1.txt, 2.txt, …)."""
    pasta = ctx[2] / "respostas"
    ja = len(os.listdir(pasta))
    for i, t in enumerate(textos, ja + 1):
        (pasta / f"{i}.txt").write_text(t, "utf-8")


def passar(ctx, t=NOW) -> list:
    return office_runner.uma_vez(ctx[0], str(ctx[1]), t)


def argvs(ctx) -> list:
    f = ctx[2] / "argv.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


def livro(cfg) -> list:
    f = officecfg.caminho(cfg, "runner") / "agentes-ia.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


def rasc(cfg):
    return officecfg.caminho(cfg, "agentes_rascunhos")


def conv(cfg, sq="apoio"):
    return conversa.ler(rasc(cfg), sq)


def volta(ctx, texto, n, t, sq="apoio"):
    s = a.pedido(ctx[0], "squad_conversa", {"squad": sq, "texto": texto}, n, t)
    passar(ctx, t)
    return a.feito(ctx[0], s)


def resposta(cfg, t=NOW) -> dict:
    return agentes.resposta(cfg, officecfg.caminho(cfg, "agentes"), rasc(cfg), officecfg.caminho(cfg, "agentes_fila"),
                            ZoneInfo(cfg["fuso"]), t)


def pedir(cfg, accao, args, t=NOW):
    return agentes.pedir(officecfg.caminho(cfg, "agentes_fila"), officecfg.caminho(cfg, "agentes"), cfg,
                         {"token": "x", "accao": accao, "args": args}, "127.0.0.1", "teste", ZoneInfo(cfg["fuso"]), t,
                         rasc=rasc(cfg))


# ---------------------------------------------------------------- CA-5, CA-7

def test_ca5_argv_exacto_texto_so_no_stdin_e_nonce_novo(ctx):
    respostas(ctx, PERGUNTAS, PERGUNTAS)
    assert volta(ctx, TEXTO, 0, NOW) == {"ok": True}
    stdin1 = (ctx[2] / "stdin.txt").read_text("utf-8")
    assert volta(ctx, "A caixa geral 7c4a", 1, NOW + 60) == {"ok": True}
    stdin2 = (ctx[2] / "stdin.txt").read_text("utf-8")
    (a1, a2) = argvs(ctx)
    assert a1 == a2 == ["-p", "--tools", "", "--max-turns", "1", "--strict-mcp-config", "--no-session-persistence",
                        "--output-format", "json", "--disallowedTools", "mcp__*", "--max-budget-usd", "0.15"]
    i = a1.index("--disallowedTools")
    assert a1[i + 2].startswith("--")   # variádico: nunca o último, seguido de outra opção
    assert not any("5e1d" in x or "7c4a" in x for x in a1 + a2)
    n1 = stdin1.split("<utilizador-")[1].split(">")[0]
    n2 = stdin2.split("<utilizador-")[1].split(">")[0]
    assert n1 != n2 and len(n1) == 16
    assert f"<utilizador-{n2}>\n{TEXTO}\n</utilizador-{n2}>" in stdin2
    assert f"<assistente-{n2}>\n1. Que caixa" in stdin2 and stdin2.rstrip().endswith(f"A caixa geral 7c4a\n</utilizador-{n2}>")


def test_ca5_modelo_da_sessao_no_fim(ctx, monkeypatch):
    cfg = ctx[0]
    cfg["runner"]["modelo"] = "sonnet"
    respostas(ctx, PERGUNTAS)
    volta(ctx, TEXTO, 0, NOW)
    assert argvs(ctx)[0][-4:] == ["--max-budget-usd", "0.15", "--model", "sonnet"]


def test_ca7_conversa_no_rascunhos_e_vista_so_le(ctx):
    cfg = ctx[0]
    respostas(ctx, PERGUNTAS)
    volta(ctx, TEXTO, 0, NOW)
    f = rasc(cfg) / "squads" / "apoio" / conversa.NOME
    c = json.loads(f.read_text("utf-8"))
    assert c["v"] == 1 and c["squad"] == "apoio" and c["estado"] == "a_perguntar" and c["chamadas"] == 1
    assert c["voltas"][0]["utilizador"] == TEXTO and c["custo_usd"] == 0.01
    antes = f.read_bytes()
    r = resposta(cfg)
    assert f.read_bytes() == antes
    (cv,) = r["conversas"]
    assert cv["squad"] == "apoio" and cv["perguntas"] == ["Que caixa de e-mail recebe as mensagens?",
                                                          "Quem aprova as respostas?"]
    assert {m["id"]: m["negocio"] for m in r["modelos"]} == {
        "atendimento": True, "comercial": True, "conteudo": False, "desenvolvimento": False, "investigacao": False,
        "observacao": False}


# ---------------------------------------------------------------- CA-8, CA-9

def test_ca8_quatro_chamadas_e_nao_ha_quinta(ctx):
    cfg = ctx[0]
    respostas(ctx, *[PERGUNTAS] * 5)
    for n in range(3):
        assert volta(ctx, f"resposta {n}", n, NOW + 60 * n) == {"ok": True}
    assert volta(ctx, "resposta 3", 3, NOW + 180) == {"erro": "não foi possível montar a squad"}
    c = conv(cfg)
    assert c["estado"] == "falhou" and c["chamadas"] == 4 and c["motivo"] == "não foi possível montar a squad"
    assert volta(ctx, "resposta 4", 4, NOW + 240) == {"erro": "a conversa já terminou"}
    assert len(argvs(ctx)) == 4


def test_ca9_045_por_volta_terceira_recusada_e_livro(ctx, monkeypatch):
    cfg = ctx[0]
    monkeypatch.setenv("FAKE_CLAUDE_CUSTO", "0.45")
    respostas(ctx, *[PERGUNTAS] * 3)
    assert volta(ctx, "um", 0, NOW) == {"ok": True}
    assert volta(ctx, "dois", 1, NOW + 60) == {"ok": True}
    assert volta(ctx, "três", 2, NOW + 120) == {"erro": "tecto da conversa atingido"}
    assert len(argvs(ctx)) == 2
    c = conv(cfg)
    assert c["estado"] == "falhou" and c["motivo"] == "tecto da conversa atingido" and c["custo_usd"] == 0.9
    assert [(x["accao"], x["custo_usd"], x["tecto"]) for x in livro(cfg)] == [("squad_conversa", 0.45, 0.15)] * 2


def test_ca9_tecto_diario_conta(ctx):
    cfg = ctx[0]
    runner = officecfg.caminho(cfg, "runner")
    runner.mkdir(parents=True, exist_ok=True)
    (runner / "agentes-ia.jsonl").write_text(json.dumps({"em": "2026-11-18T08:00:00+00:00", "accao": "agente_ensaio",
                                                         "custo_usd": 9.9, "tecto": 2.0}) + "\n", "utf-8")
    respostas(ctx, PERGUNTAS)
    assert volta(ctx, TEXTO, 0, NOW) == {"erro": "tecto diário de IA atingido"}
    assert argvs(ctx) == [] and conv(cfg) is None


def test_volta_falhada_conta_e_pede_de_novo(ctx, monkeypatch):
    cfg = ctx[0]
    respostas(ctx, "não percebi", PERGUNTAS)
    assert volta(ctx, TEXTO, 0, NOW) == {"erro": "a IA não respondeu bem, envia de novo"}
    c = conv(cfg)
    assert c["estado"] == "a_perguntar" and c["chamadas"] == 1 and c["aviso"] == conversa.MSG_MAL
    assert volta(ctx, TEXTO, 1, NOW + 60) == {"ok": True} and conv(cfg)["chamadas"] == 2
    monkeypatch.setenv("FAKE_CLAUDE_FALHA", "1")
    assert volta(ctx, "x", 2, NOW + 120) == {"erro": "a IA não respondeu bem, envia de novo"}
    assert conv(cfg)["chamadas"] == 3 and len(livro(cfg)) == 3


def test_sem_claude_nada_muda(ctx, monkeypatch):
    cfg = ctx[0]
    monkeypatch.setattr(agentes_fila.shutil, "which", lambda _: None)
    assert volta(ctx, TEXTO, 0, NOW) == {"erro": "a IA falhou"}
    assert conv(cfg) is None and livro(cfg) == []


# ---------------------------------------------------------------- CA-10, CA-11

def test_ca10_recusa_com_motivo_e_alternativa(ctx):
    cfg = ctx[0]
    respostas(ctx, NAO_DA)
    assert volta(ctx, "quero fazer transferências M-Pesa", 0, NOW, "pagar") == {"ok": True}
    c = conversa.publica(conv(cfg, "pagar"))
    assert c["estado"] == "nao_da" and c["motivo"].startswith("mexer em dinheiro")
    assert c["alternativa"].startswith("uma squad de atendimento")
    assert agentes._rascunho(rasc(cfg), "pagar") is None


def test_ca10_comercial_sem_perfil_leva_a_pergunta_fixa(ctx):
    cfg = ctx[0]
    com = PROPOSTA.replace("atendimento", "comercial")
    respostas(ctx, com, com)
    assert volta(ctx, "quero pontuar os pedidos de contacto", 0, NOW, "vendas") == {"ok": True}
    c = conversa.publica(conv(cfg, "vendas"))
    assert c["estado"] == "a_perguntar" and c["perguntas"] == [conversa.PERGUNTA_PERFIL]
    assert agentes._rascunho(rasc(cfg), "vendas") is None
    assert volta(ctx, "compram lojas pequenas", 1, NOW + 60, "vendas") == {"ok": True}
    assert conv(cfg, "vendas")["estado"] == "proposta"
    assert agentes._rascunho(rasc(cfg), "vendas")[0]["modelo"] == "comercial"


def test_ca11_proposta_vira_rascunho_e_editar_muda_o_sha(ctx):
    cfg = ctx[0]
    respostas(ctx, PROPOSTA)
    assert volta(ctx, TEXTO, 0, NOW) == {"ok": True}
    r, sha = agentes._rascunho(rasc(cfg), "apoio")
    m = next(x for x in agentes.modelos_squad() if x["id"] == "atendimento")
    assert (r["modelo"], r["lead"], r["nome"]) == ("atendimento", "responsavel", "Atendimento")
    assert set(r["ficheiros"]) == {"CLAUDE.md", *(f"agents/{n}.md" for n in m["agentes"])}
    for n in m["agentes"]:   # agentes sempre os do modelo; o «tools: Bash» da IA ficou em ignorado
        assert r["ficheiros"][f"agents/{n}.md"] == (agentes.MODELOS_DIR / "atendimento" / "agents" / f"{n}.md").read_text(
            "utf-8")
    assert "deixar um rascunho de resposta por mensagem" in r["ficheiros"]["CLAUDE.md"]
    assert "{{MISSAO}}" not in r["ficheiros"]["CLAUDE.md"] and agentes_fila._validar_rascunho(r)["id"] == "atendimento"
    assert conv(cfg)["voltas"][0]["ignorado"] == ["proposta: tools"]
    vista = next(x for x in resposta(cfg)["squads"] if x["id"] == "apoio")
    assert vista["origem"] == "rascunho" and vista["rascunho"]["sha256"] == sha
    # editar o rascunho (o diff editável volta a pedir squad_rascunho, ou alguém mexe no ficheiro) muda o sha
    f = rasc(cfg) / "squads" / "apoio" / "rascunho.json"
    d = json.loads(f.read_text("utf-8"))
    d["missao"] += " Editado."
    f.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    assert agentes._rascunho(rasc(cfg), "apoio")[1] != sha
    s = a.pedido(cfg, "squad_criar", {"squad": "apoio", "sha256": sha}, 1, NOW + 60)
    passar(ctx, NOW + 60)
    assert a.feito(cfg, s) == {"erro": "o rascunho mudou depois de ser visto"}


# ---------------------------------------------------------------- CA-11a

def test_ca11a_uma_conversa_de_cada_vez_no_servidor_e_no_runner(ctx):
    cfg = ctx[0]
    respostas(ctx, PERGUNTAS, PERGUNTAS)
    assert pedir(cfg, "squad_conversa", {"squad": "apoio", "texto": TEXTO})[0] == 202
    # pendente na fila para outra squad → 409
    assert pedir(cfg, "squad_conversa", {"squad": "vendas", "texto": "x"}) == (
        409, {"error": "já há uma conversa em curso"})
    passar(ctx)
    # em curso (a_perguntar) noutra squad → 409; a mesma squad pode responder
    assert pedir(cfg, "squad_conversa", {"squad": "vendas", "texto": "x"}) == (
        409, {"error": "já há uma conversa em curso"})
    assert pedir(cfg, "squad_conversa", {"squad": "apoio", "texto": "resposta"}, NOW + 60)[0] == 202
    passar(ctx, NOW + 60)
    assert conv(cfg)["chamadas"] == 2
    # o runner volta a recusar um pedido que lá chegue por outra via
    assert volta(ctx, "x", 9, NOW + 120, "vendas") == {"erro": "já há uma conversa em curso"}
    assert conv(cfg, "vendas") is None


def test_ca11a_recusas_do_servidor(ctx):
    cfg = ctx[0]
    a.agente(cfg, "dev", "revisor", "---\nname: revisor\ndescription: R\n---\n")
    assert pedir(cfg, "squad_conversa", {"squad": "dev", "texto": "x"}) == (400, {"error": "squad já tem agentes"})
    assert pedir(cfg, "squad_conversa", {"squad": "apoio", "texto": "a" * 1001}) == (
        400, {"error": "campos inválidos"})
    respostas(ctx, NAO_DA)
    volta(ctx, "pagamentos", 0, NOW, "pagar")
    assert pedir(cfg, "squad_conversa", {"squad": "pagar", "texto": "e agora?"}) == (
        400, {"error": "a conversa já terminou"})
    assert volta(ctx, "e agora?", 1, NOW + 60, "pagar") == {"erro": "a conversa já terminou"}


def test_ca11a_parada_24h_apagada_e_descartar_apaga(ctx):
    cfg = ctx[0]
    respostas(ctx, PERGUNTAS, PROPOSTA)
    volta(ctx, TEXTO, 0, NOW)
    assert passar(ctx, NOW + conversa.PARADA_S) == [] and conv(cfg) is not None
    linhas = passar(ctx, NOW + conversa.PARADA_S + 61)
    assert "agentes: conversa da squad apoio apagada (parada há mais de 24 h)" in linhas
    assert conv(cfg) is None and not (rasc(cfg) / "squads" / "apoio").exists()
    t = NOW + 2 * a.DIA
    volta(ctx, TEXTO, 1, t)
    assert conv(cfg)["estado"] == "proposta" and agentes._rascunho(rasc(cfg), "apoio") is not None
    s = a.pedido(cfg, "squad_descartar", {"squad": "apoio"}, 2, t + 60)
    passar(ctx, t + 60)
    assert a.feito(cfg, s) == {"ok": True} and conv(cfg) is None and agentes._rascunho(rasc(cfg), "apoio") is None


def test_ca11a_parada_com_pedido_pendente_nao_se_apaga(ctx):
    cfg = ctx[0]
    respostas(ctx, PERGUNTAS)
    volta(ctx, TEXTO, 0, NOW)
    t = NOW + conversa.PARADA_S + 61
    pend = {"apoio"}
    assert agentes_fila.limpar_conversas(cfg, t, pend) == [] and conv(cfg) is not None


def test_c3_conversa_ilegivel_parada_apaga_se(ctx):
    cfg = ctx[0]
    pasta = rasc(cfg) / "squads" / "apoio"
    pasta.mkdir(parents=True)
    (pasta / conversa.NOME).write_text("{ilegível", "utf-8")
    os.utime(pasta / conversa.NOME, (NOW, NOW))
    assert conv(cfg) is None
    assert agentes_fila.limpar_conversas(cfg, NOW + conversa.PARADA_S, set()) == []
    assert agentes_fila.limpar_conversas(cfg, NOW + conversa.PARADA_S + 61, {"apoio"}) == []
    assert (pasta / conversa.NOME).exists()
    assert agentes_fila.limpar_conversas(cfg, NOW + conversa.PARADA_S + 61, set()) == [
        "agentes: conversa da squad apoio apagada (parada há mais de 24 h)"]
    assert not pasta.exists()


def test_descartar_so_com_conversa(ctx):
    cfg = ctx[0]
    respostas(ctx, PERGUNTAS)
    volta(ctx, TEXTO, 0, NOW)
    assert pedir(cfg, "squad_descartar", {"squad": "apoio"})[0] == 202
    passar(ctx, NOW + 60)
    assert conv(cfg) is None
    assert pedir(cfg, "squad_descartar", {"squad": "apoio"}, NOW + 120) == (400, {"error": "rascunho inexistente"})


# ---------------------------------------------------------------- CA-11b ponta a ponta

def test_ca11b_ponta_a_ponta_atendimento(ctx, monkeypatch):
    cfg = ctx[0]
    monkeypatch.setenv("FAKE_CLAUDE_CUSTO", "0.12")
    respostas(ctx, PERGUNTAS, "=====PERGUNTAS=====\n1. Há alguma coisa que a squad nunca pode prometer?\n", PROPOSTA)
    t = NOW
    for n, texto in enumerate((TEXTO, "A caixa geral; aprovo eu nas Aprovações.", "Nunca prometer descontos.")):
        assert pedir(cfg, "squad_conversa", {"squad": "apoio", "texto": texto}, t)[0] == 202
        passar(ctx, t)
        t += 60
    c = conversa.publica(conv(cfg))
    assert c["estado"] == "proposta" and c["modelo_squad"] == "atendimento" and c["ronda"] == 2
    assert c["custo_usd"] <= conversa.TECTO_USD and c["chamadas"] == 3 and c["negocio"] is True
    sha = next(x for x in resposta(cfg, t)["squads"] if x["id"] == "apoio")["rascunho"]["sha256"]
    assert pedir(cfg, "squad_criar", {"squad": "apoio", "sha256": sha}, t)[0] == 202
    passar(ctx, t)
    (s,) = a.registo(cfg)["squads"]
    assert (s["id"], s["modelo"], s["lead"]) == ("apoio", "atendimento", "responsavel")
    assert agentes.modelo_negocio(a.registo(cfg), "apoio") == "atendimento"
    assert sorted(os.listdir(officecfg.caminho(cfg, "agentes") / "apoio" / "agents")) == [
        "redactor.md", "responsavel.md", "revisor.md", "triagem.md"]
    assert conv(cfg) is None and resposta(cfg, t)["conversas"] == []
    assert sum(x["custo_usd"] for x in livro(cfg)) <= 1.00
