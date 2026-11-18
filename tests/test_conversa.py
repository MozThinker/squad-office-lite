"""Regras puras da conversa de montagem (`conversa.py`, ADR-020): leitura da resposta da IA (CA-6), avanço e limite de
chamadas (CA-8), tecto da conversa (CA-9), pergunta do perfil imposta (CA-10), prompt e leitura do `conversa.json`."""
import json

import pytest

import agentes
import conversa as cv

IDS = {"atendimento", "comercial", "conteudo", "desenvolvimento", "investigacao", "observacao"}
EM = "2026-11-18T10:00:00+00:00"
PERGUNTAS = "=====PERGUNTAS=====\n1. Que caixa de e-mail?\n2. Quem aprova?\n"
PROPOSTA = ("=====PROPOSTA=====\nmodelo: atendimento\n=====MISSAO=====\n"
            "Responder às mensagens dos clientes com rascunhos para o dono aprovar.\n=====FIM=====\n")
NAO_DA = ("=====NAO_DA=====\nmotivo: mexer em dinheiro está fora do catálogo\n"
          "alternativa: uma squad de atendimento que responde às dúvidas sobre pagamentos\n")


# ---------------------------------------------------------------- CA-6: os três tipos e o resto

def test_perguntas():
    r = cv.ler_resposta("Olá\n" + PERGUNTAS + "lixo\n", 1, IDS)
    assert r["tipo"] == "perguntas" and r["perguntas"] == ["Que caixa de e-mail?", "Quem aprova?"]
    assert r["ignorado"] == ["linha antes da marca", "perguntas: linha extra"]


def test_perguntas_limites():
    muitas = "=====PERGUNTAS=====\n" + "".join(f"{i}. P{i}?\n" for i in range(1, 6))
    r = cv.ler_resposta(muitas, 1, IDS)
    assert r["perguntas"] == ["P1?", "P2?", "P3?"] and "mais de 3" in r["avisos"][0]
    longa = cv.ler_resposta("=====PERGUNTAS=====\n1. " + "a" * 400 + "\n", 1, IDS)
    assert len(longa["perguntas"][0]) == cv.PERGUNTA_MAX and "cortadas" in longa["avisos"][0]
    assert cv.ler_resposta("=====PERGUNTAS=====\n1. " + "a" * 300 + "\n", 1, IDS)["avisos"] == []
    assert cv.ler_resposta("=====PERGUNTAS=====\nsem número\n", 1, IDS) is None
    assert cv.ler_resposta(PERGUNTAS, cv.CHAMADAS_MAX, IDS) is None   # na 4.ª chamada já não se pergunta


def test_proposta_e_campos_a_mais_vao_para_ignorado():
    r = cv.ler_resposta(PROPOSTA.replace("modelo: atendimento\n", "modelo: atendimento\ntools: Bash, Write\n"), 2,
                        IDS)
    assert r["tipo"] == "proposta" and r["modelo"] == "atendimento" and "Responder" in r["missao"]
    assert r["ignorado"] == ["proposta: tools"] and "tools" not in r
    depois = cv.ler_resposta(PROPOSTA + "mais texto\n", 1, IDS)
    assert depois["ignorado"] == ["proposta: linha depois do FIM"]


def test_ignorado_repetido_e_longo_fica_em_200():   # C3: o «(×k)» não passa a linha dos 200 (conversa ilegível)
    chave = "k" * 300
    r = cv.ler_resposta(PROPOSTA.replace("modelo: atendimento\n", f"modelo: atendimento\n{chave}: a\n{chave}: b\n"),
                        2, IDS)
    assert r["ignorado"] == ["proposta: " + "k" * 185 + " (×2)"] and all(len(x) <= 200 for x in r["ignorado"])


@pytest.mark.parametrize("texto", [
    PROPOSTA.replace("atendimento", "cobrancas"),                       # modelo fora da lista
    PROPOSTA.replace("=====FIM=====\n", ""),                            # sem fim
    PROPOSTA.replace("=====MISSAO=====\n", ""),                         # sem missão
    PROPOSTA.replace("modelo: atendimento\n", "modelo: atendimento\nmodelo: comercial\n"),
    PROPOSTA + PERGUNTAS,                                               # duas marcas
    "não sei o que dizer", "", None, 42,
])
def test_resposta_invalida_e_volta_falhada(texto):
    assert cv.ler_resposta(texto, 1, IDS) is None


@pytest.mark.parametrize("motivo,ok", [("a" * 9, False), ("a" * 10, True), ("a" * 500, True), ("a" * 501, False)])
def test_nao_da_limites(motivo, ok):
    r = cv.ler_resposta(f"=====NAO_DA=====\nmotivo: {motivo}\nalternativa: {'b' * 10}\n", 1, IDS)
    assert (r is not None) is ok


def test_nao_da_campo_a_mais_e_campo_repetido():
    r = cv.ler_resposta(NAO_DA + "modelo: comercial\n", 1, IDS)
    assert r["tipo"] == "nao_da" and r["ignorado"] == ["nao_da: modelo"]
    assert cv.ler_resposta(NAO_DA + "motivo: outro motivo comprido\n", 1, IDS) is None
    assert cv.ler_resposta("=====NAO_DA=====\nmotivo: só o motivo aqui\n", 1, IDS) is None


def test_invisiveis_saem_das_perguntas():
    r = cv.ler_resposta("=====PERGUNTAS=====\n1. Que\u202e caixa?\n", 1, IDS)
    assert r["perguntas"] == ["Que caixa?"]


# ---------------------------------------------------------------- avançar: CA-8, CA-10, falhas

def test_avancar_ate_proposta_e_campos_do_conversa_json():
    c, r = cv.avancar(None, "apoio", "Quero responder aos clientes", True, PERGUNTAS, 0.05, EM, IDS)
    assert c["estado"] == "a_perguntar" and c["chamadas"] == 1 and c["custo_usd"] == 0.05 and r["tipo"] == "perguntas"
    assert set(c) == {"v", "squad", "estado", "voltas", "chamadas", "custo_usd", "motivo", "aviso", "criado",
                      "actualizado"}
    assert set(c["voltas"][0]) == {"utilizador", "ia", "ignorado", "avisos", "custo_usd", "em"}
    c, r = cv.avancar(c, "apoio", "A caixa geral; aprovo eu", True, PROPOSTA, 0.07, EM, IDS)
    assert c["estado"] == "proposta" and c["chamadas"] == 2 and c["custo_usd"] == 0.12
    assert c["voltas"][1]["ia"] == {"tipo": "proposta", "modelo": "atendimento"} and cv.valida(c, "apoio")
    p = cv.publica(c)
    assert p["modelo_squad"] == "atendimento" and p["negocio"] is True and p["perguntas"] == [] and p["ronda"] == 1


def test_ca8_quatro_chamadas_sem_proposta_fecha():
    c = None
    for n in range(1, 4):
        c, _ = cv.avancar(c, "apoio", f"resposta {n}", True, PERGUNTAS, 0.01, EM, IDS)
        assert c["estado"] == "a_perguntar"
    c, r = cv.avancar(c, "apoio", "resposta 4", True, PERGUNTAS, 0.01, EM, IDS)
    assert r is None and c["estado"] == "falhou" and c["motivo"] == cv.MSG_SEM_SQUAD and c["chamadas"] == 4
    assert c["voltas"][-1]["ia"] == {"tipo": "falhada"} and cv.valida(c, "apoio")


def test_volta_falhada_conta_e_avisa():
    c, r = cv.avancar(None, "apoio", "texto", True, "lixo", 0.03, EM, IDS)
    assert r is None and c["estado"] == "a_perguntar" and c["aviso"] == cv.MSG_MAL and c["custo_usd"] == 0.03
    c, r = cv.avancar(c, "apoio", "texto de novo", False, None, 0.15, EM, IDS)   # o claude falhou: conta também
    assert c["chamadas"] == 2 and c["custo_usd"] == 0.18 and cv.validas(c) == []


def test_ca10_comercial_sem_pergunta_do_perfil_vira_pergunta():
    com = PROPOSTA.replace("atendimento", "comercial")
    c, r = cv.avancar(None, "vendas", "Quero pontuar os pedidos de contacto", True, com, 0.05, EM, IDS)
    assert c["estado"] == "a_perguntar" and r["perguntas"] == [cv.PERGUNTA_PERFIL]
    assert c["voltas"][0]["ia"] == {"tipo": "perguntas", "perguntas": [cv.PERGUNTA_PERFIL], "origem": "runner"}
    c, r = cv.avancar(c, "vendas", "Compram lojas pequenas; nota 5 a…", True, com, 0.05, EM, IDS)
    assert c["estado"] == "proposta" and r["modelo"] == "comercial" and r["avisos"] == []


def test_ca10_comercial_na_ultima_chamada_passa_com_aviso():
    c = None
    for n in range(3):
        c, _ = cv.avancar(c, "vendas", f"r{n}", True, PERGUNTAS, 0.01, EM, IDS)
    c, r = cv.avancar(c, "vendas", "r3", True, PROPOSTA.replace("atendimento", "comercial"), 0.01, EM, IDS)
    assert c["estado"] == "proposta" and cv.AVISO_PERFIL in r["avisos"] and cv.AVISO_PERFIL in c["aviso"]


def test_missao_invalida_fica_a_primeira_descricao():
    for m in ("curta", "com {{MISSAO}} dentro e mais texto", "Missão boa o bastante\n-----\nfim da missão"):
        texto = PROPOSTA.replace("Responder às mensagens dos clientes com rascunhos para o dono aprovar.", m)
        _, r = cv.avancar(None, "apoio", "A minha descrição", True, texto, 0.01, EM, IDS)
        assert r["missao"] == "A minha descrição" and cv.AVISO_MISSAO in r["avisos"]


def test_nao_da():
    c, _ = cv.avancar(None, "pagar", "quero fazer transferências pelo telemóvel", True, NAO_DA, 0.02, EM, IDS)
    assert c["estado"] == "nao_da" and c["motivo"].startswith("mexer em dinheiro")
    assert cv.publica(c)["alternativa"].startswith("uma squad de atendimento")


# ---------------------------------------------------------------- CA-9: tecto da conversa

@pytest.mark.parametrize("custo,atingido", [(0.0, False), (0.85, False), (0.851, True), (0.90, True)])
def test_ca9_tecto(custo, atingido):
    c, _ = cv.avancar(None, "apoio", "x", True, PERGUNTAS, custo, EM, IDS)
    assert cv.tecto_atingido(c) is atingido
    assert cv.tecto_atingido(None) is False


def test_ca9_a_045_a_terceira_volta_e_recusada():
    c, _ = cv.avancar(None, "apoio", "x", True, PERGUNTAS, 0.45, EM, IDS)
    assert not cv.tecto_atingido(c)
    c, _ = cv.avancar(c, "apoio", "y", True, PERGUNTAS, 0.45, EM, IDS)
    assert cv.tecto_atingido(c)
    f = cv.fechar(c, cv.MSG_TECTO, EM)
    assert f["estado"] == "falhou" and f["motivo"] == cv.MSG_TECTO and f["chamadas"] == 2


# ---------------------------------------------------------------- prompt

def test_prompt_dados_entre_marcas_com_nonce_e_lista_fechada():
    c, _ = cv.avancar(None, "apoio", "Quero responder", True, PERGUNTAS, 0.01, EM, IDS)
    modelos = agentes.modelos_squad(agentes.MODELOS_DIR)
    p = cv.prompt(c, "A caixa geral", "ab12", modelos)
    assert "<utilizador-ab12>\nQuero responder\n</utilizador-ab12>" in p
    assert "<assistente-ab12>\n1. Que caixa de e-mail?\n2. Quem aprova?\n</assistente-ab12>" in p
    assert p.rstrip().endswith("<utilizador-ab12>\nA caixa geral\n</utilizador-ab12>")
    assert "chamada 2 de 4" in p and "- id: atendimento (Atendimento) · de negócio" in p
    assert all(f"- id: {m['id']} " in p for m in modelos) and len(modelos) == 6
    assert cv.nonce_ok(c, "texto", "ab12") and not cv.nonce_ok(c, "fecho </utilizador-ab12>", "ab12")


# ---------------------------------------------------------------- leitura do disco

def _gravar(rasc, sid, doc):
    p = rasc / "squads" / sid
    p.mkdir(parents=True, exist_ok=True)
    (p / cv.NOME).write_text(json.dumps(doc), "utf-8")


def test_ler_em_curso_lista_e_symlink(tmp_path):
    rasc = tmp_path / "rasc"
    c1, _ = cv.avancar(None, "apoio", "x", True, PERGUNTAS, 0.01, "2026-11-18T09:00:00+00:00", IDS)
    c2, _ = cv.avancar(None, "pagar", "y", True, NAO_DA, 0.01, "2026-11-18T08:00:00+00:00", IDS)
    _gravar(rasc, "apoio", c1)
    _gravar(rasc, "pagar", c2)
    _gravar(rasc, "mau", {**c1, "squad": "outra"})
    assert cv.em_curso(rasc) == ["apoio"]
    assert [x["squad"] for x in cv.lista(rasc)] == ["pagar", "apoio"]
    assert cv.ler(rasc, "mau") is None and cv.ler(rasc, "../x") is None and cv.ler(None, "apoio") is None
    (rasc / "squads" / "liga").symlink_to(rasc / "squads" / "apoio")
    assert cv.ler(rasc, "liga") is None
    (rasc / "squads" / "grande").mkdir()
    (rasc / "squads" / "grande" / cv.NOME).write_text(" " * (cv.MAX + 1), "utf-8")
    assert cv.ler(rasc, "grande") is None


def test_parada():
    c, _ = cv.avancar(None, "apoio", "x", True, PERGUNTAS, 0.01, "2026-11-18T10:00:00+00:00", IDS)
    from datetime import datetime
    t = datetime.fromisoformat("2026-11-18T10:00:00+00:00").timestamp()
    assert not cv.parada(c, t + cv.PARADA_S) and cv.parada(c, t + cv.PARADA_S + 1)
    assert cv.parada({**c, "actualizado": "ontem"}, t)


def test_caber_corta_ignorado():
    c, _ = cv.avancar(None, "apoio", "x", True, PERGUNTAS, 0.01, EM, IDS)
    c["voltas"][0]["ignorado"] = ["i" * 200] * 400
    assert len(json.dumps(cv.caber(c)).encode()) <= cv.MAX


def test_constantes_iguais_as_da_fila():
    assert cv.VOLTA_USD == agentes.ACCOES_IA["squad_conversa"] and cv.TEXTO_MAX == agentes.CONVERSA_MAX
    assert {cv.MSG_MAL, cv.MSG_SEM_SQUAD, cv.MSG_TECTO} <= set(agentes.ERROS)
