"""accoes.py (ADR-021): catálogo, validação das propostas com a vista, links, sha, render do pedido, célula de folha,
configuração, credenciais e verificação de arranque (CA-12, CA-13, CA-17, CA-18b, CA-22). Pastas temporárias."""
import json
import os
from pathlib import Path

import pytest

import accoes
import agentes
from tests.test_runner_comum import decisoes

REPO = Path(__file__).resolve().parent.parent
M1, M2, M4 = "m" + "1" * 16, "m" + "2" * 16, "m" + "4" * 16


def ctx(**mais) -> dict:
    base = {"v": 1, "ligadas": list(accoes.TIPOS), "dominios": ["padaria.test"],
            "folhas": {"leads": ["nome", "contacto", "nota"]}, "caixa": "geral@padaria.test",
            "mensagens": {M1: {"remetente": "cliente@exemplo.test", "autenticada": True},
                          M4: {"remetente": "forjado@exemplo.test", "autenticada": False}}}
    base.update(mais)
    return base


def bloco(**d) -> str:
    return json.dumps(d, ensure_ascii=False)


def val(b, modelo="atendimento", c=None):
    return accoes.validar(b, "loja", modelo, c or ctx())


def ok(b, modelo="atendimento", c=None):
    p, m = val(b, modelo, c)
    assert m is None, m
    return p


def recusa(b, motivo, **kw):
    p, m = val(b, **kw)
    assert p is None
    assert m == motivo


# --- partilhado com o runner e o agentes.py --------------------------------------------------------------------------

def test_igual_ao_agentes_e_ao_runner():
    assert accoes.MODELOS_NEGOCIO == agentes.MODELOS_NEGOCIO
    assert accoes.ID_SQUAD.pattern == agentes.ID_SQUAD.pattern
    assert accoes.RE_LINHA.pattern == decisoes.RE_LINHA.pattern
    assert accoes.RE_FEITA.pattern == decisoes.RE_FEITA.pattern
    d = {"squad": "loja", "id": "r1", "projecto": "x", "hora": "09:00", "dias": [1], "instrucoes": "olá «x»",
         "tecto_usd": "0.50", "max_turnos": 3}
    assert accoes.canon(d).decode() == agentes.linha_rotina(d)[0]
    assert accoes.chars_ok("a\tb\nc") and not accoes.chars_ok("a\u202eb") and not accoes.chars_ok("a\x00")


def test_decisoes_md_como_o_aplicador():
    md = ("---\nestado: pendente\n---\n## Decisões\n- [A1] x · 0 US$ → aprovado (2026-11-18)\n- [A2] y\n"
          "- [A1] repetida → recusado (2026-11-18)\n## Outra\n- [A3] fora → aprovado (2026-11-18)\n")
    assert accoes.decisoes_md(md) == {"A1": ("aprovado", "2026-11-18"), "A2": (None, None)}
    assert [(d["id"], d["estado"]) for d in decisoes.decisoes(md)] == [("A1", "aprovado"), ("A2", None)]


def test_extrair():
    assert accoes.extrair("nada aqui") == []
    t = "a\n=====ACCAO=====\n{\"x\": 1}\n=====FIM ACCAO=====\nb\n=====ACCAO=====\n{}\n=====FIM ACCAO=====\n"
    assert accoes.extrair(t) == ['{"x": 1}', "{}"]
    assert accoes.extrair("=====ACCAO=====\n{}\n") == [None]
    assert accoes.extrair("=====ACCAO=====\n{}\n=====ACCAO=====\n{}\n=====FIM ACCAO=====") == [None, "{}"]
    assert accoes.extrair(None) == []


# --- validar (CA-12, CA-17, CA-22) -----------------------------------------------------------------------------------

def test_validos_por_tipo():
    r = ok(bloco(tipo="email_responder", origem=M1, assunto="Horário", corpo="Olá\n\nAté já"))
    assert r == {"tipo": "email_responder", "origem": M1, "assunto": "Horário", "corpo": "Olá\n\nAté já"}
    ok(bloco(tipo="email_rascunho", origem=M1, assunto="a", corpo="b"))
    ok(bloco(tipo="aviso_dono", texto="chegou um pedido urgente"))
    f = ok(bloco(tipo="folha_linha", folha="leads", valores={"nota": "4", "nome": "Ana"}), "comercial")
    assert list(f["valores"]) == ["nome", "nota"]   # pela ordem das colunas


def test_campos_e_catalogo():   # CA-17
    recusa(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="b", para="outro@mal.test"),
           "campo a mais: para")
    recusa(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="b", **{"Para X": 1}), "campo a mais")
    recusa(bloco(tipo="email_responder", origem=M1, corpo="b"), "falta o campo assunto")
    recusa(bloco(tipo="email_responder", origem=M1, assunto=3, corpo="b"), "assunto tem de ser texto")
    recusa("{nada", accoes.M_JSON)
    recusa("[1, 2]", accoes.M_JSON)
    recusa('{"tipo": "aviso_dono", "texto": "a", "texto": "b"}', accoes.M_JSON)
    recusa("x" * (accoes.BLOCO_MAX + 1), accoes.M_JSON)
    recusa(None, accoes.M_SEM_FIM)
    recusa(bloco(tipo="whatsapp", texto="a"), accoes.M_CATALOGO)
    recusa(bloco(tipo="pagamento", valor=1), accoes.M_CATALOGO)
    recusa(bloco(tipo="aviso_dono", texto="a"), accoes.M_CATALOGO, modelo="desenvolvimento")


def test_tamanhos_e_linhas():   # limites exactos
    ok(bloco(tipo="email_responder", origem=M1, assunto="a" * 200, corpo="b" * 4000))
    recusa(bloco(tipo="email_responder", origem=M1, assunto="a" * 201, corpo="b"), "assunto acima de 200 caracteres")
    recusa(bloco(tipo="email_responder", origem=M1, assunto="a\nb", corpo="b"), "assunto tem de ser uma linha")
    recusa(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="b" * 4001), "corpo acima de 4000 caracteres")
    ok(bloco(tipo="aviso_dono", texto="t" * 500))
    recusa(bloco(tipo="aviso_dono", texto="t" * 501), "texto acima de 500 caracteres")
    recusa(bloco(tipo="aviso_dono", texto="  "), "texto vazio")
    recusa(bloco(tipo="aviso_dono", texto="a\u202eb"), "texto com caracteres não permitidos")
    ok(bloco(tipo="folha_linha", folha="leads", valores={"nome": "v" * 500, "nota": ""}), "comercial")
    recusa(bloco(tipo="folha_linha", folha="leads", valores={"nome": "v" * 501}), "valores acima de 500 caracteres",
           modelo="comercial")
    recusa(bloco(tipo="folha_linha", folha="leads", valores={"nome": "a\nb"}), "valores tem de ser uma linha",
           modelo="comercial")
    recusa(bloco(tipo="folha_linha", folha="leads", valores={"cor": "a"}), "coluna desconhecida: cor",
           modelo="comercial")
    recusa(bloco(tipo="folha_linha", folha="outra", valores={"nome": "a"}), accoes.M_FOLHA, modelo="comercial")
    recusa(bloco(tipo="folha_linha", folha="leads", valores={}), "valores tem de ser um objecto com colunas",
           modelo="comercial")


def test_html():
    recusa(bloco(tipo="aviso_dono", texto="olá <b>x</b>"), accoes.M_HTML)
    recusa(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="<img src=x>"), accoes.M_HTML)
    ok(bloco(tipo="aviso_dono", texto="2 < 3 e 5 > 4"))


def test_links():
    doms = ["padaria.test"]
    for bom in ("ver https://padaria.test/x", "www.sub.padaria.test/y", "HTTP://PADARIA.TEST:8080/a", "sem links",
                "padaria.test/precos", "custa 1.5 etc. e mais", "Horário: 9h às 10:30", "escreve a ana@exemplo.test",
                "Ex.mo Senhor", "ficheiro.pdf", "Sr.Manuel", "Obrigado.Cumprimentos", "e/ou 1.5/2"):
        assert accoes.erro_links(bom, doms) is None, bom
    for mau in ("http://padaria.test@mal.test", "https://mal.test", "https://padaria.test.mal.test", "www.mal.test",
                "https://[::1", "https://xpadaria.test", "https://evil.test\\@padaria.test/x", "visite evil.test/pagar",
                "ftp://evil.test", "javascript:alert(1)", "http:evil.test", "https://u@padaria.test/",
                "mailto:x@evil.test", "evil.test:8080/x", "www.p\u0430daria.test", "xn--padaria-abc.test/",
                "padaria\u3002evil.test/", "(https://evil.test)", "a ana@evil.test/x"):
        assert accoes.erro_links(mau, doms) == accoes.M_LINK, mau
    recusa(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="vê https://mal.test"), accoes.M_LINK)
    recusa(bloco(tipo="folha_linha", folha="leads", valores={"nota": "https://mal.test"}), accoes.M_LINK,
           modelo="comercial")


def test_links_hosts_soltos_e_nfkc():
    # S3: x.tld sozinho, com ?… ou #…, e o ponto de largura total (NFKC) contam como host
    doms = ["ok.pt"]
    for mau in ("visite evil.com", "evil.com?x=1", "evil.com#a", "evil\uff0ecom/x", "EVIL.COM", "evil.com.",
                "ficheiro.zip", "ok.pt.evil.com"):
        assert accoes.erro_links(mau, doms) == accoes.M_LINK, mau
    for bom in ("ok.pt/x", "ok.pt", "sub.ok.pt?x=1", "relatorio.pdf", "Ex.mo Senhor", "Ex.ma Senhora", "p.ex. isto",
                "Sr.Manuel", "Obrigado.Cumprimentos", "v1.5", "ok\uff0ept/x"):
        assert accoes.erro_links(bom, doms) is None, bom


def test_origem_autenticacao_e_terceiros():   # CA-12
    recusa(bloco(tipo="email_responder", origem=M4, assunto="a", corpo="b"), accoes.M_NAO_AUTENTICADO)
    ok(bloco(tipo="email_rascunho", origem=M4, assunto="a", corpo="b"))
    for o in (M2, "m1", "../" + M1, M1.upper()):
        recusa(bloco(tipo="email_responder", origem=o, assunto="a", corpo="b"), accoes.M_ORIGEM)
    recusa(bloco(tipo="email_responder", origem=5, assunto="a", corpo="b"), "origem tem de ser texto")
    recusa(bloco(tipo="email_rascunho", origem=M1, assunto="a", corpo="fala com outro@mal.test"), accoes.M_TERCEIROS)
    ok(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="Cliente@exemplo.test, escreva a geral@padaria.test"))
    ok(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="Fale com vendas@padaria.test ou a@sub.padaria.test"))
    recusa(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="ou vendas@padaria.test.evil.test"),
           accoes.M_TERCEIROS)


def test_ligadas_e_perfil_lite(tmp_path):   # CA-22
    sem = accoes.validar_config({"v": 1, "squads": {}}, tmp_path)
    assert accoes.ligadas(sem, "loja") == frozenset({"email_rascunho", "folha_linha", "aviso_dono"})
    assert accoes.ligadas(sem, "loja", "outro") == frozenset()
    um = accoes.validar_config({"v": 1, "squads": {"loja": {"accoes": ["aviso_dono"]}}}, tmp_path)
    assert accoes.ligadas(um, "loja") == frozenset({"aviso_dono"})
    c = accoes.contexto(sem, "loja", {M1: {"remetente": "c@exemplo.test", "autenticada": True}})
    recusa(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="b"), accoes.M_NAO_LIGADA, c=c)
    ok(bloco(tipo="email_rascunho", origem=M1, assunto="a", corpo="b"), c=c)
    resp = accoes.validar_config({"v": 1, "squads": {"loja": {"accoes": list(accoes.TIPOS)}}}, tmp_path)
    ok(bloco(tipo="email_responder", origem=M1, assunto="a", corpo="b"), c=accoes.contexto(resp, "loja", c["mensagens"]))
    assert accoes.contexto_omissao()["ligadas"] == sorted(accoes.PERFIS["lite"])


def test_limite(tmp_path):   # CA-16
    cfg = accoes.validar_config({"v": 1, "squads": {"loja": {"limites": {"email_responder": 5}}}}, tmp_path)
    assert accoes.limite(cfg, "loja", "email_responder") == 5
    assert {t: accoes.limite(cfg, "outra", t) for t in accoes.TIPOS} == accoes.LIMITES_DIA
    assert accoes.LIMITES_DIA == {"email_rascunho": 50, "email_responder": 20, "folha_linha": 100, "aviso_dono": 20}


def test_vista():
    assert accoes.validar_vista(ctx()) == ctx()
    assert accoes.validar_vista(accoes.contexto_omissao()) is not None
    for mau in (None, [], {**ctx(), "x": 1}, ctx(ligadas=["pagar"]), ctx(dominios=["Mal"]),
                ctx(mensagens={"m1": {"remetente": "a@b.test", "autenticada": True}}),
                ctx(mensagens={M1: {"remetente": "a@b.test", "autenticada": 1}}), ctx(caixa="x"),
                ctx(folhas={"leads": "nome"})):
        assert accoes.validar_vista(mau) is None, mau


# --- sha, secção, linhas (CA-13) ---------------------------------------------------------------------------------------

def defn(**c):
    p = {"tipo": "email_responder", "origem": M1, "assunto": "Sábado", "corpo": "Olá\n\n> citado\n## Decisões"}
    p.update(c)
    return accoes.definicao("loja", p, "2026-11-18T10:00:07+00:00")


def test_sha_estavel_e_sensivel_a_um_caracter():
    d = defn()
    assert accoes.sha(d) == accoes.sha(json.loads(json.dumps(d)))
    for mudar in ({"assunto": "Sabado"}, {"corpo": "Olá"}, {"origem": M2}, {"assunto": "Sábadoo"}):
        assert accoes.sha(defn(**mudar)) != accoes.sha(d), mudar
    assert accoes.sha({**d, "criado": "2026-11-18T10:00:08+00:00"}) != accoes.sha(d)
    assert accoes.sha({**d, "squad": "outra"}) != accoes.sha(d)


def test_secao_ida_e_volta():
    d = defn()
    s = accoes.secao("A1", d, accoes.sha(d))
    assert all(ln.startswith(("### ", "    ", "assunto:", "corpo:", ">")) for ln in s.split("\n"))
    md = f"## Decisões\n- [A1] x\n## Definição das acções\n{s}\n### A2 · aviso_dono\n    x\n## Recusadas e de fora\n"
    assert accoes.secao_do_md(md, "A1") == s
    assert accoes.secao_do_md(md, "A3") is None
    assert accoes.secao_do_md(md.replace("### A2", "### A1"), "A1") is None
    assert accoes.secao_do_md(md + "## Definição das acções\n", "A1") is None
    f = accoes.definicao("loja", {"tipo": "folha_linha", "folha": "leads", "valores": {"nome": "Ana", "nota": "4"}}, "x")
    assert "valores:\n> nome: Ana\n> nota: 4" in accoes.secao("A2", f, "0" * 64)


def test_linha_decisao():
    assert accoes.linha_decisao("A1", defn(), "cliente@exemplo.test") == \
        "- [A1] Responder a cliente@exemplo.test sobre «Sábado» · e-mail · 0 US$"
    assert accoes.linha_decisao("A2", accoes.definicao("loja", {"tipo": "aviso_dono", "texto": "x"}, "c"), None) == \
        "- [A2] Aviso ao dono no Telegram · aviso · 0 US$"


def test_celula():
    for ch in ("=", "+", "-", "@", "\r"):
        assert accoes.celula(ch + "1") == "'" + ch + "1"
    assert accoes.celula("\t=1") == " '=1"
    assert accoes.celula("quer A; B") == "quer A; B"
    assert accoes.celula("a;=1; -2\tx;@b") == "a;'=1; '-2 x;'@b"
    assert accoes.celula("") == ""


# --- configuração e credenciais ----------------------------------------------------------------------------------------

def erro_cfg(d, texto, raiz):
    with pytest.raises(accoes.ConfigErro) as e:
        accoes.validar_config(d, raiz)
    assert texto in str(e.value)


def test_validar_config(tmp_path):
    srv = {"host": "imap.exemplo.test", "porta": 993, "tls": True}
    email = {"endereco": "geral@padaria.test", "pasta": "INBOX", "rascunhos": "Drafts", "imap": srv, "smtp": srv}
    accoes.validar_config({"v": 1, "squads": {"loja": {"email": email}}}, tmp_path)
    erro_cfg({"v": 1, "squads": {}, "office_yaml": "/x"}, "chave desconhecida", tmp_path)
    erro_cfg({"v": 2, "squads": {}}, "v: tem de ser 1", tmp_path)
    erro_cfg({"v": 1, "squads": {"Loja": {}}}, "id inválido", tmp_path)
    erro_cfg({"v": 1, "squads": {"loja": {"limites": {"email_responder": 21}}}}, "só podem baixar", tmp_path)
    accoes.validar_config({"v": 1, "squads": {"loja": {"limites": {"email_responder": 0}}}}, tmp_path)
    erro_cfg({"v": 1, "squads": {"loja": {"email": {**email, "imap": {**srv, "tls": False}}}}}, "localhost", tmp_path)
    erro_cfg({"v": 1, "squads": {"a1": {"email": email}, "b1": {"email": email}}}, "uma caixa serve uma squad", tmp_path)
    erro_cfg({"v": 1, "squads": {}, "telegram": {"url": "http://api.exemplo.test", "chat_id": "1"}}, "telegram.url",
             tmp_path)
    erro_cfg({"v": 1, "squads": {"loja": {"accoes": ["pagar"]}}}, "accoes", tmp_path)
    erro_cfg({"v": 1, "squads": {"loja": {"email": {**email, "pasta": 'a"b'}}}}, "pasta", tmp_path)
    for fora in (tmp_path / "x.csv", tmp_path / "folhas/sub/x.csv", tmp_path / "folhas/../x.csv",
                 tmp_path / "folhas/x.txt"):
        erro_cfg({"v": 1, "squads": {"loja": {"folhas": {"l": {"ficheiro": str(fora), "colunas": ["a"]}}}}},
                 "folhas/", tmp_path)
    (tmp_path / "folhas").mkdir()
    (tmp_path / "folhas/l.csv").symlink_to(tmp_path / "x.csv")
    erro_cfg({"v": 1, "squads": {"loja": {"folhas": {"l": {"ficheiro": str(tmp_path / "folhas/l.csv"),
                                                          "colunas": ["a"]}}}}}, "folhas/", tmp_path)
    accoes.validar_config({"v": 1, "squads": {"loja": {"folhas": {"l": {"ficheiro": str(tmp_path / "folhas/n.csv"),
                                                                         "colunas": ["a"]}}}}}, tmp_path)


def test_ler_config(tmp_path):
    os.chmod(tmp_path, 0o755)
    p = tmp_path / "accoes.json"
    assert accoes.ler_config(p) == {"v": 1, "squads": {}}
    p.write_text('{"v": 1, "squads": {}}')
    os.chmod(p, 0o644)
    assert accoes.ler_config(p)["squads"] == {}
    os.chmod(p, 0o664)
    with pytest.raises(accoes.ConfigErro, match="gravável"):
        accoes.ler_config(p)
    os.chmod(p, 0o644)
    (tmp_path / "l.json").symlink_to(p)
    with pytest.raises(accoes.ConfigErro, match="regular"):
        accoes.ler_config(tmp_path / "l.json")
    p.write_text('{"v": 1, "squads": {}, "squads": {}}')
    with pytest.raises(accoes.ConfigErro, match="JSON inválido"):
        accoes.ler_config(p)


def test_segredos(tmp_path):
    p = tmp_path / "accoes.env"
    p.write_text("# comentário\nLOJA_EMAIL_UTILIZADOR=geral@padaria\nLOJA_EMAIL_SENHA= a=b \n\nTELEGRAM_TOKEN=1:AA\n")
    assert accoes.ler_segredos(p) == {"LOJA_EMAIL_UTILIZADOR": "geral@padaria", "LOJA_EMAIL_SENHA": "a=b",
                                      "TELEGRAM_TOKEN": "1:AA"}
    assert accoes.ler_segredos(tmp_path / "nao") == {}
    for mau, linha in (("minuscula=1\n", 1), ("OUTRA_CHAVE=1\n", 1), ("TELEGRAM_TOKEN=\n", 1),
                       ("# x\nLOJA_EMAIL_SENHA=a\x01b\n", 2)):
        p.write_text(mau)
        with pytest.raises(accoes.ConfigErro) as e:
            accoes.ler_segredos(p)
        assert f"linha {linha}" in str(e.value)
        assert "a\x01b" not in str(e.value)
    assert accoes.chave_sq("loja-2") == "LOJA_2"


# --- arranque (CA-18b) -------------------------------------------------------------------------------------------------

@pytest.fixture
def arr(tmp_path):
    os.chmod(tmp_path, 0o755)
    (tmp_path / "segredos").mkdir(mode=0o700)
    seg = tmp_path / "segredos/accoes.env"
    seg.write_text("TELEGRAM_TOKEN=x\n")
    os.chmod(seg, 0o600)
    cfg = tmp_path / "accoes.json"
    cfg.write_text('{"v": 1, "squads": {}}')
    os.chmod(cfg, 0o644)
    return tmp_path, cfg, seg


def test_arranque_ok(arr):
    _, cfg, seg = arr
    assert accoes.erro_arranque(cfg, seg, REPO) is None
    seg.unlink()
    assert accoes.erro_arranque(cfg, seg, REPO) is None   # sem credenciais: só a folha funciona


def test_arranque_credenciais(arr):
    d, cfg, seg = arr
    for modo in (0o640, 0o604, 0o620, 0o602, 0o644, 0o610):
        os.chmod(seg, modo)
        assert "grupo/outros" in (accoes.erro_arranque(cfg, seg, REPO) or ""), oct(modo)
    os.chmod(seg, 0o600)
    os.chmod(seg.parent, 0o777)
    assert "pasta gravável" in (accoes.erro_arranque(cfg, seg, REPO) or "")
    os.chmod(seg.parent, 0o700)
    real = d / "real.env"
    seg.rename(real)
    seg.symlink_to(real)
    assert "symlink" in (accoes.erro_arranque(cfg, seg, REPO) or "")


@pytest.mark.skipif(os.geteuid() == 0, reason="como root o teste perde sentido")
def test_arranque_credenciais_de_outro_utilizador(arr, monkeypatch):
    _, cfg, seg = arr
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)
    assert "não é do utilizador" in (accoes.erro_arranque(cfg, seg, REPO) or "")


def test_arranque_configuracao_e_codigo(arr):
    d, cfg, seg = arr
    for modo in (0o664, 0o646):
        os.chmod(cfg, modo)
        assert "configuração: gravável" in (accoes.erro_arranque(cfg, seg, REPO) or "")
    os.chmod(cfg, 0o644)
    os.chmod(d, 0o777)
    assert "configuração: pasta gravável" in (accoes.erro_arranque(cfg, seg, REPO) or "")
    os.chmod(d, 0o755)
    cod = d / "codigo"
    cod.mkdir()
    os.chmod(cod, 0o777)
    assert "código" in (accoes.erro_arranque(cfg, seg, cod) or "")
    os.chmod(cod, 0o755)
    assert accoes.erro_arranque(cfg, seg, cod) is None
    cfg.unlink()
    cfg.symlink_to(seg)
    assert "regular" in (accoes.erro_arranque(cfg, seg, REPO) or "")


def test_raizes_do_ambiente():
    r = accoes.Raizes.do_ambiente({"ACCOES_DIR": "/a", "ACCOES_ENTRADA": "/e", "ACCOES_DECISOES": "/d",
                                   "ACCOES_AGENTES": "/g", "ACCOES_SAUDE": "/s"})
    assert (r.dir, r.segredos, r.entrada, r.decisoes, r.agentes, r.saude) == tuple(map(Path, (
        "/a", "/a/segredos", "/e", "/d", "/g", "/s")))
    assert (r.config, r.env) == (Path("/a/accoes.json"), Path("/a/segredos/accoes.env"))
    assert accoes.Raizes.do_ambiente({}).dir == Path("/dados/accoes")
    assert accoes.Raizes.do_ambiente({}).saude == Path("/dados/accoes-saude")


def test_links_ronda2():   # porta sem barra, separadores, TLD misto, U+3002 sozinho; «Palavra.Palavra» não é host
    for t in ("evil.Com", "eViL.CoM", "evil.com:8080", "evil.com,foo", "evil.com;x", "evil.com|x", "evil.com&x=1",
              "evil.com=x", "evil\u3002com", "https://ok.pt/?r=evil.com"):
        assert accoes.erro_links(t, ["ok.pt"]) == accoes.M_LINK, t
    for t in ("ok.pt/x", "https://ok.pt/?a=b&c=d", "ok.pt:443", "Sr.Manuel", "Obrigado.Cumprimentos", "1.5 kg",
              "Olá, tudo bem; até já", "a=b"):
        assert accoes.erro_links(t, ["ok.pt"]) is None, t
