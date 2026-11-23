"""Bloco `conversa` do office.yaml (ADR-023, revisão do guardião, PLAN2 §2.6): omissões, limites de cada chave
(`ativa`, `pasta`, `site`, `tls`), `pasta` obrigatória com `ativa: true`, `site` com `ativa` e `tls: caddy`, `tls: externo` por omissão (S2), as chaves
antigas (nome, permissões, voz) recusadas, `versao: 1` sem mudar e o bloco comentado no office.example.yaml."""
from pathlib import Path

import pytest

import officecfg
from tests.cfg import ler

EXEMPLO = (Path(officecfg.__file__).resolve().parent / "office.example.yaml").read_text(encoding="utf-8")
OMISSAO = {"ativa": False, "pasta": None, "site": None, "tls": "externo"}


def bloco(**k) -> str:
    return EXEMPLO + "conversa:\n" + "".join(f"  {c}: {v}\n" for c, v in k.items())


def test_sem_bloco_omissoes():
    assert ler(EXEMPLO)["conversa"] == OMISSAO
    assert ler(EXEMPLO)["versao"] == 1


def test_exemplo_tem_o_bloco_comentado_e_desligado():
    texto = (officecfg.EXEMPLO).read_text(encoding="utf-8")
    assert "# conversa:" in texto and "\nconversa:" not in texto
    for chave in ("ativa", "pasta", "site", "tls"):
        assert f"\n#   {chave}: " in texto, chave
    for antiga in ("nome", "permissoes", "voz", "voz_idioma"):
        assert f"\n#   {antiga}: " not in texto, antiga
    assert officecfg.exemplo()["conversa"] == OMISSAO
    # descomentado, o bloco do exemplo valida (com ativa: true)
    i = texto.index("# conversa:\n")
    linhas = [ln[2:] for ln in texto[i:].splitlines() if ln.startswith("# conversa:") or ln.startswith("#   ")]
    linhas = [ln for ln in linhas if not ln.strip().startswith("#")]
    c = ler(EXEMPLO + "\n".join(linhas).replace("ativa: false", "ativa: true") + "\n")["conversa"]
    assert c == {"ativa": True, "pasta": "/srv/office-conversa", "site": "conversa.exemplo.com", "tls": "externo"}


def test_ligada_completa():
    c = ler(bloco(ativa="true", pasta="/srv/office-conversa/", site="conversa.exemplo.com", tls="caddy"))["conversa"]
    assert c == {"ativa": True, "pasta": "/srv/office-conversa", "site": "conversa.exemplo.com", "tls": "caddy"}


def test_externo_sem_site():
    c = ler(bloco(ativa="true", pasta="/srv/office-conversa", tls="externo"))["conversa"]
    assert c == {"ativa": True, "pasta": "/srv/office-conversa", "site": None, "tls": "externo"}


@pytest.mark.parametrize("site", ["a", "conversa", "c.exemplo.com", "x-1.y-2.pt", "a" * 63 + ".pt",
                                  ".".join(["a" * 63] * 3 + ["a" * 61])])
def test_site_aceite(site):
    assert len(site) <= 253
    assert ler(bloco(site=site))["conversa"]["site"] == site


@pytest.mark.parametrize("k,v,campo", [
    ("ativa", "sim", "conversa.ativa"), ("ativa", '"true"', "conversa.ativa"),
    ("pasta", "srv/x", "conversa.pasta"), ("pasta", "/srv/../etc", "conversa.pasta"), ("pasta", "/", "conversa.pasta"),
    ("pasta", "~/x", "conversa.pasta"), ("pasta", '"/srv/a b"', "conversa.pasta"), ("pasta", "/srv//x", "conversa.pasta"),
    ("pasta", "/" + "a" * 200, "conversa.pasta"),
    ("site", "Conversa.exemplo.com", "conversa.site"), ("site", "c.exemplo.com:8443", "conversa.site"),
    ("site", "-c.exemplo.com", "conversa.site"), ("site", "c-.exemplo.com", "conversa.site"),
    ("site", "c..exemplo.com", "conversa.site"), ("site", "c.exemplo.com.", "conversa.site"),
    ("site", '"https://c.exemplo.com"', "conversa.site"), ("site", '"c.exemplo.com/x"', "conversa.site"),
    ("site", '"c exemplo"', "conversa.site"), ("site", "a" * 64 + ".pt", "conversa.site"),
    ("site", ".".join(["a" * 63] * 4), "conversa.site"), ("site", "5", "conversa.site"), ("site", '""', "conversa.site"),
    ("site", '"*.exemplo.com"', "conversa.site"),
    ("tls", "auto", "conversa.tls"), ("tls", "Caddy", "conversa.tls"), ("tls", "true", "conversa.tls"),
    ("nome", '"Ana"', "conversa.nome"), ("permissoes", "cartao", "conversa.permissoes"),
    ("voz", "desligada", "conversa.voz"), ("voz_idioma", "pt", "conversa.voz_idioma"), ("outra", "1", "conversa.outra")])
def test_recusas(k, v, campo):
    with pytest.raises(officecfg.ConfigErro) as e:
        ler(bloco(**{k: v}))
    assert str(e.value).startswith(campo)


def test_site_nunca_aparece_na_mensagem():
    with pytest.raises(officecfg.ConfigErro) as e:
        ler(bloco(site='"Segredo.exemplo.com"'))
    assert "Segredo" not in str(e.value)


def test_ativa_exige_pasta():
    with pytest.raises(officecfg.ConfigErro, match=r"^conversa.pasta: obrigatório"):
        ler(bloco(ativa="true", site="c.exemplo.com"))
    assert ler(bloco(ativa="false"))["conversa"]["ativa"] is False


def test_ativa_com_caddy_exige_site():
    with pytest.raises(officecfg.ConfigErro, match=r"^conversa.site: obrigatório"):
        ler(bloco(ativa="true", pasta="/srv/office-conversa", tls="caddy"))
    assert ler(bloco(ativa="false", pasta="/srv/office-conversa"))["conversa"]["site"] is None


def test_tls_externo_por_omissao():   # S2: o TLS da Conversa termina fora da fabrica salvo pedido explícito
    c = ler(bloco(ativa="true", pasta="/srv/office-conversa"))["conversa"]
    assert c == {"ativa": True, "pasta": "/srv/office-conversa", "site": None, "tls": "externo"}
    assert ler(bloco(ativa="true", pasta="/srv/office-conversa", site="c.exemplo.com",
                     tls="caddy"))["conversa"]["tls"] == "caddy"
    with pytest.raises(officecfg.ConfigErro, match=r"^conversa.tls: tem de ser externo ou caddy$"):
        ler(bloco(tls='""'))


def test_pasta_no_limite():
    p = "/" + "a" * 199
    assert ler(bloco(pasta=p))["conversa"]["pasta"] == p


def test_bloco_nao_mapa():
    with pytest.raises(officecfg.ConfigErro, match="^conversa: tem de ser um mapeamento"):
        ler(EXEMPLO + "conversa: true\n")


def test_publico_nao_leva_a_conversa():
    # /api/config: o servidor só acrescenta {"ativa": true, "url"} com a Conversa ligada (nunca a pasta nem o tls)
    cfg = ler(bloco(ativa="true", pasta="/srv/office-conversa", site="c.exemplo.com"))
    assert "conversa" not in officecfg.publico(cfg, None)


def test_sem_pastas_da_conversa_nos_dados():
    assert "conversa_prefs" not in officecfg.SUB
