"""chat.py (ADR-023), contrato puro: assinatura e canónico com os vectores do PLAN §2.3 (CA-9), validar_post e
validar_fila por acção (chaves exactas, todas obrigatórias), nome do assistente (CA-18) e CA-12 por AST."""
import ast
import hashlib
import json
from pathlib import Path

import pytest

import chat

ROOT = Path(__file__).resolve().parent.parent
CID = "20261123-120000-abc123"
NONCE = "0123456789abcdef0123456789abcdef"
PID = "0123456789abcdef"
T = 1795435200000
ASS = "0" * 64
CRIADO = "2026-11-23T12:00:00+00:00"
# vectores do PLAN §2.3 (conferidos contra tests/fixtures/conversa_vectores.json)
CODIGO = "ABCDEFGHIJKLMNOPQRSTUVWX"
SAL_PONTE = "00112233445566778899aabbccddeeff"
SAL_OFFICE = "ffeeddccbbaa99887766554433221100"
CHAVE = "76edf82323601bfe7f1d78aa640513ea5a5b1fe18e02a1874649af0fa53a6d33"   # K do browser (G2)
VERIFICADOR = "3aa945cf9e2d4c8593b725a7ab1c7734a4903c27a8dbfcf5002c34e51c8c71b8"
VERIFICADOR_SHA = "1ee6fe9e07d68697ca5f3ea48bc454a66757174dfedb96e80e585fc0d2a5a479"
PEDIDO = {"acao": "enviar", "conversa": CID, "texto": "Olá «mundo»\nlinha 2", "modo": "normal", "modelo": "omissao",
          "permissoes": "cartao", "pid": PID, "t": T}
CANONICO = ('{"acao":"enviar","conversa":"20261123-120000-abc123","modelo":"omissao","modo":"normal",'
            '"permissoes":"cartao","pid":"0123456789abcdef","t":1795435200000,"texto":"Olá «mundo»\\nlinha 2"}')
ASSINATURA = "64676a3e7d4124ddc14e6f636e96f4e63794f4bf425f77d5221eeeb4177d97a6"


def post(acao, **k):
    return {"acao": acao, "pid": PID, "t": T, "assinatura": ASS, **k}


def fila(acao, **k):
    return {"v": 1, "criado": CRIADO, "ip": "1.2.3.4", "ua": "UA", **post(acao, **k)}


VALIDOS = [post("enviar", conversa=CID, texto="olá\n\ttudo", modo="normal", modelo="omissao", permissoes="cartao"),
           post("enviar", conversa=CID, texto="x", modo="plano", modelo="fable", permissoes="espelho"),
           post("parar", conversa=CID), post("arquivar", conversa=CID), post("nova", conversa=CID),
           post("responder_permissao", conversa=CID, nonce=NONCE, escolha="uma_vez"),
           post("responder_pergunta", conversa=CID, nonce=NONCE,
                respostas=[{"pergunta": 0, "opcao": [1, 0]}, {"pergunta": 3, "outra": "a" * 1000}]),
           post("aprovar_plano", conversa=CID, nonce=NONCE, escolha="aprovar", texto=""),
           post("aprovar_plano", conversa=CID, nonce=NONCE, escolha="editar", texto="novo plano")]
TRANSCREVER = post("transcrever", conversa=CID, audio=NONCE, formato="webm", bytes=2048, sha256="a" * 64,
                   idioma="pt")


# ---------------------------------------------------------------- §2.3: vectores e assinatura

def test_vectores_pbkdf2():
    # o que o browser e o chat_host/codigo.py derivam do código: só o verificador (G2: a chave K não sai dele)
    chave = hashlib.pbkdf2_hmac("sha256", CODIGO.encode(), bytes.fromhex(SAL_PONTE), 600000, 32)
    verif = hashlib.pbkdf2_hmac("sha256", CODIGO.encode(), bytes.fromhex(SAL_OFFICE), 600000, 32)
    assert chave.hex() != CHAVE and verif.hex() == VERIFICADOR
    assert hashlib.sha256(verif).hexdigest() == VERIFICADOR_SHA


def test_vectores_fixture_iguais():
    # o ficheiro partilhado com a ponte e o browser tem os mesmos valores que este teste usa
    v = json.loads((ROOT / "tests" / "fixtures" / "conversa_vectores.json").read_text(encoding="utf-8"))
    assert (v["codigo"], v["sal_ponte"], v["sal_office"], v["iteracoes"]) == (CODIGO, SAL_PONTE, SAL_OFFICE, 600000)
    assert (v["chave"], v["verificador"], v["verificador_sha256"]) == (CHAVE, VERIFICADOR, VERIFICADOR_SHA)
    assert (v["pedido"], v["canonico"], v["assinatura"]) == (PEDIDO, CANONICO, ASSINATURA)
    assert chat.assinar(bytes.fromhex(v["chave"]), v["pedido"]) == v["assinatura"]


def test_canonico_e_assinatura():
    assert chat.canonico(PEDIDO) == CANONICO.encode("utf-8")
    assert chat.assinar(bytes.fromhex(CHAVE), PEDIDO) == ASSINATURA
    assinado = {**PEDIDO, "assinatura": ASSINATURA}
    assert chat.assinatura_ok(bytes.fromhex(CHAVE), assinado)
    # v/criado/ip/ua (postos pelo Office) e a própria assinatura ficam fora do canónico
    na_fila = chat.doc_fila(assinado, "1.2.3.4", "UA", CRIADO)
    assert chat.canonico(na_fila) == CANONICO.encode("utf-8")
    assert chat.assinatura_ok(bytes.fromhex(CHAVE), na_fila)


def test_canonico_ordena_dentro_das_respostas():
    d = post("responder_pergunta", conversa=CID, nonce=NONCE, respostas=[{"pergunta": 0, "opcao": [1]}])
    assert b'[{"opcao":[1],"pergunta":0}]' in chat.canonico(d)


@pytest.mark.parametrize("mudar", [{"texto": "Olá «mundo»\nlinha 3"}, {"t": T + 1}, {"pid": "f" * 16},
                                   {"permissoes": "espelho"}, {"extra": 1}])
def test_assinatura_errada_se_o_pedido_mudar(mudar):
    assert not chat.assinatura_ok(bytes.fromhex(CHAVE), {**PEDIDO, **mudar, "assinatura": ASSINATURA})


@pytest.mark.parametrize("ass", [None, "", ASSINATURA.upper(), ASSINATURA[:-1], ASSINATURA + "0", 5])
def test_assinatura_mal_formada_ou_chave_errada(ass):
    d = dict(PEDIDO) if ass is None else {**PEDIDO, "assinatura": ass}
    assert not chat.assinatura_ok(bytes.fromhex(CHAVE), d)
    assert not chat.assinatura_ok(b"\x00" * 32, {**PEDIDO, "assinatura": ASSINATURA})
    assert not chat.assinatura_ok(bytes.fromhex(CHAVE), [])


def test_tempo_ok_limites():
    agora = T / 1000
    assert chat.tempo_ok({"t": T}, agora + 300) and chat.tempo_ok({"t": T}, agora - 300)
    assert not chat.tempo_ok({"t": T}, agora + 300.001) and not chat.tempo_ok({"t": T}, agora - 301)


# ---------------------------------------------------------------- validar_post

@pytest.mark.parametrize("d", VALIDOS, ids=lambda d: d["acao"])
def test_validar_post_validos(d):
    assert chat.validar_post(d) is None


def test_transcrever_so_pela_rota_de_audio():
    assert chat.validar_post(TRANSCREVER) == "acção inválida"
    assert chat.validar_post(TRANSCREVER, audio=True) is None
    assert chat.validar_post(VALIDOS[0], audio=True) == "acção inválida"


@pytest.mark.parametrize("d", [
    post("enviar", conversa=CID, texto="x", modo="normal", modelo="omissao"),   # sem permissoes
    post("enviar", conversa=CID, texto="x"),                                     # omissões não se preenchem
    post("aprovar_plano", conversa=CID, nonce=NONCE, escolha="aprovar"),          # texto obrigatório
    post("nova"), post("parar", conversa=CID, texto="x"), post("arquivar", conversa=CID, nonce=NONCE),
    {k: v for k, v in post("parar", conversa=CID).items() if k != "assinatura"},
    {k: v for k, v in post("parar", conversa=CID).items() if k != "pid"},
    {k: v for k, v in post("parar", conversa=CID).items() if k != "t"},
    {**post("parar", conversa=CID), "token": "x"}, {**post("parar", conversa=CID), "csrf": "x"},
    {**post("parar", conversa=CID), "v": 1}, {**post("parar", conversa=CID), "criado": CRIADO}])
def test_validar_post_chaves_exactas(d):
    assert chat.validar_post(d) == "campos inválidos"


@pytest.mark.parametrize("d", [post("apagar"), post(None), {"pid": PID}, [], None, "x"])
def test_validar_post_acao_invalida(d):
    assert chat.validar_post(d) == "acção inválida"


@pytest.mark.parametrize("campo,valor,msg", [
    ("pid", "0123456789abcde", "pid inválido"), ("pid", "0123456789ABCDEF", "pid inválido"),
    ("pid", "0123456789abcdef0", "pid inválido"), ("pid", 5, "pid inválido"),
    ("t", 0, "t inválido"), ("t", -1, "t inválido"), ("t", 1.5, "t inválido"), ("t", True, "t inválido"),
    ("t", "1795435200000", "t inválido"), ("t", 2 ** 53, "t inválido"),
    ("assinatura", "A" * 64, "assinatura inválida"), ("assinatura", "0" * 63, "assinatura inválida"),
    ("assinatura", None, "assinatura inválida")])
def test_validar_post_comuns(campo, valor, msg):
    assert chat.validar_post({**post("parar", conversa=CID), campo: valor}) == msg


def test_validar_post_t_limite_superior():
    assert chat.validar_post({**post("parar", conversa=CID), "t": 2 ** 53 - 1}) is None


@pytest.mark.parametrize("t", ["", "a" * 16001, "a\rb", "a\x00b", "a\x1bb", "a\ud800b", 5, None])
def test_validar_post_texto(t):
    d = post("enviar", conversa=CID, texto=t, modo="normal", modelo="omissao", permissoes="cartao")
    assert chat.validar_post(d) == "texto inválido"


def test_validar_post_texto_limites():
    base = {"conversa": CID, "modo": "normal", "modelo": "omissao", "permissoes": "cartao"}
    assert chat.validar_post(post("enviar", texto="a" * 16000, **base)) is None
    assert chat.validar_post(post("enviar", texto="😀 \x7f", **base)) is None


@pytest.mark.parametrize("campo,valor,msg", [
    ("modo", "auto", "modo inválido"), ("modelo", "gpt", "modelo inválido"), ("modelo", ["opus"], "modelo inválido"),
    ("modelo", "Fable", "modelo inválido"), ("permissoes", "auto", "permissões inválidas"),
    ("permissoes", None, "permissões inválidas"), ("conversa", 5, "conversa inválida")])
def test_validar_post_enviar(campo, valor, msg):
    d = post("enviar", conversa=CID, texto="x", modo="normal", modelo="omissao", permissoes="cartao")
    assert chat.validar_post({**d, campo: valor}) == msg


def test_validar_post_id_so_se_confere_depois_menos_no_nova():
    # o id de uma conversa existente confere-se em forma depois (404, como um inexistente); o do `nova` já (400)
    assert chat.validar_post(post("parar", conversa="../x")) is None
    for mau in ("../x", "20261123-120000-ABC123", CID + "\n", 5):
        assert chat.validar_post(post("nova", conversa=mau)) == "conversa inválida"


def test_validar_post_nonce_escolha():
    for n in ("0123", NONCE.upper(), NONCE + "0", 5):
        assert chat.validar_post(post("responder_permissao", conversa=CID, nonce=n, escolha="uma_vez")) \
            == "nonce inválido"
    assert chat.validar_post(post("responder_permissao", conversa=CID, nonce=NONCE, escolha="aprovar")) \
        == "escolha inválida"
    assert chat.validar_post(post("aprovar_plano", conversa=CID, nonce=NONCE, escolha="uma_vez", texto="")) \
        == "escolha inválida"


def test_validar_post_plano_texto():
    base = {"conversa": CID, "nonce": NONCE}
    assert chat.validar_post(post("aprovar_plano", escolha="editar", texto="", **base)) == "texto inválido"
    for e in ("aprovar", "continuar"):
        assert chat.validar_post(post("aprovar_plano", escolha=e, texto="x", **base)) == "texto só em «editar»"


@pytest.mark.parametrize("r", [
    [], [{"pergunta": i, "opcao": [0]} for i in range(5)], [{"pergunta": 0, "opcao": [0]}] * 2,
    [{"pergunta": 4, "opcao": [0]}], [{"pergunta": True, "opcao": [0]}], [{"pergunta": 0, "opcao": []}],
    [{"pergunta": 0, "opcao": [0, 0]}], [{"pergunta": 0, "opcao": [4]}], [{"pergunta": 0, "opcao": 1}],
    [{"pergunta": 0, "opcao": [0, 1, 2, 3, 0]}], [{"pergunta": 0, "outra": ""}],
    [{"pergunta": 0, "outra": "a" * 1001}], [{"pergunta": 0, "opcao": [0], "outra": "x"}], [{"pergunta": 0}],
    ["x"], "x"])
def test_validar_post_respostas(r):
    assert chat.validar_post(post("responder_pergunta", conversa=CID, nonce=NONCE, respostas=r)) \
        == "respostas inválidas"


@pytest.mark.parametrize("campo,valor,msg", [
    ("audio", "x" * 32, "áudio inválido"), ("formato", "wav", "formato inválido"), ("bytes", 1023, "bytes inválidos"),
    ("bytes", 5 * 1024 * 1024 + 1, "bytes inválidos"), ("bytes", True, "bytes inválidos"),
    ("sha256", "A" * 64, "sha256 inválido"), ("sha256", None, "sha256 inválido"), ("idioma", "PT", "idioma inválido"),
    ("idioma", "por", "idioma inválido"), ("idioma", "p", "idioma inválido"), ("idioma", 1, "idioma inválido")])
def test_validar_post_transcrever(campo, valor, msg):
    assert chat.validar_post({**TRANSCREVER, campo: valor}, audio=True) == msg


def test_validar_post_transcrever_limites_bytes():
    for b in (1024, 5 * 1024 * 1024):
        assert chat.validar_post({**TRANSCREVER, "bytes": b}, audio=True) is None


# ---------------------------------------------------------------- validar_fila

@pytest.mark.parametrize("d", VALIDOS + [TRANSCREVER], ids=lambda d: d["acao"])
def test_doc_fila_e_validar_fila_coincidem(d):
    doc = chat.doc_fila(d, "1.2.3.4", "u" * 500, CRIADO)
    assert len(doc["ua"]) == 120 and doc["v"] == 1 and doc["criado"] == CRIADO
    assert {k: v for k, v in doc.items() if k not in chat.EXTRA_FILA} == d
    assert chat.validar_fila(doc) is None


@pytest.mark.parametrize("d,msg", [
    ({**fila("parar", conversa=CID), "token": "t"}, "campos inválidos"),
    ({k: v for k, v in fila("parar", conversa=CID).items() if k != "pid"}, "campos inválidos"),
    ({k: v for k, v in fila("parar", conversa=CID).items() if k != "ip"}, "campos inválidos"),
    ({**fila("parar", conversa=CID), "v": 2}, "versão inválida"),
    ({**fila("parar", conversa=CID), "v": True}, "versão inválida"),
    ({**fila("parar", conversa=CID), "criado": "ontem"}, "criado inválido"),
    ({**fila("parar", conversa=CID), "criado": "2026-11-23T12:00:00"}, "criado inválido"),
    ({**fila("parar", conversa=CID), "ip": "x" * 65}, "ip ou ua inválidos"),
    ({**fila("parar", conversa=CID), "ua": 5}, "ip ou ua inválidos"),
    ({**fila("parar", conversa=CID), "pid": "x"}, "pid inválido"),
    ({**fila("parar", conversa=CID), "t": 1.0}, "t inválido"),
    ({**fila("parar", conversa=CID), "assinatura": ""}, "assinatura inválida"),
    (fila("parar", conversa="../x"), "conversa inválida"),
    (fila("aprovar_plano", conversa=CID, nonce=NONCE, escolha="aprovar", texto="x"), "texto só em «editar»"),
    (fila("responder_permissao", conversa=CID, nonce="x" * 32, escolha="uma_vez"), "nonce inválido"),
    ([], "acção inválida"), (fila("apagar"), "acção inválida")])
def test_validar_fila_recusas(d, msg):
    assert chat.validar_fila(d) == msg


# ---------------------------------------------------------------- nome do assistente (CA-18)

@pytest.mark.parametrize("nome", ["Assistente", "A", "a" * 40, "Ana Lúcia", "助手", "😀"])
def test_nome_ok(nome):
    assert chat.nome_ok(nome)


@pytest.mark.parametrize("nome", ["", "a" * 41, "   ", "a\nb", "a\x00", "a\u200bb", "a\u202eb", "a\ufeff",
                                  "a\ud800", None, 5])
def test_nome_recusado(nome):
    assert not chat.nome_ok(nome)


# ---------------------------------------------------------------- CA-12: sem subprocess, urllib nem sockets

PROIBIDOS = {"subprocess", "urllib", "socket", "http.client", "asyncio", "ssl"}


def _imports(src: str) -> set:
    out = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out.add(n.module or "")
        elif isinstance(n, ast.Call) and getattr(n.func, "id", None) == "__import__":
            out.add("__import__")
    return out


def test_ca12_chat_sem_rede_nem_processos():
    mods = _imports((ROOT / "chat.py").read_text(encoding="utf-8"))
    assert not {m for m in mods if m.split(".")[0] in PROIBIDOS or m in PROIBIDOS or m == "__import__"}
    assert "os.system" not in (ROOT / "chat.py").read_text(encoding="utf-8")


def test_canonico_igual_ao_json_do_js():
    # JSON.stringify não escapa não-ASCII nem «/»; inteiros sem «.0»
    d = post("parar", conversa=CID)
    assert json.loads(chat.canonico(d)) == {k: v for k, v in d.items() if k != "assinatura"}
    assert b" " not in chat.canonico(d)


def test_chat_importa_so_stdlib_no_topo():
    # a ponte (Python 3.10 do Ubuntu 22.04) importa o chat.py: o reader.py (regex do Python ≥ 3.11) só dentro de mask()
    import sys
    topo = set()
    for n in ast.parse((ROOT / "chat.py").read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Import):
            topo |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module != "__future__":
            topo.add(n.module.split(".")[0])
    assert topo <= sys.stdlib_module_names, topo - sys.stdlib_module_names


def test_ids_so_com_digitos_ascii():   # C4: «٢» (árabe-índico) casa com \d sem re.ASCII
    assert chat.ID.fullmatch("20261123-101500-abc123") and chat.NOME_FILA.fullmatch("20261123T101500-0123abcd.json")
    assert not chat.ID.fullmatch("٢0261123-101500-abc123")
    assert not chat.NOME_FILA.fullmatch("20261123T10150٠-0123abcd.json")
