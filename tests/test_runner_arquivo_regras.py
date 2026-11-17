"""arquivo.py: regras de caminho (iguais às da Biblioteca 0.1.4), fila do arquivo (contrato v1), REGISTO e protecções.

Corre também em Python 3.10 (glob `test_runner_*` da CI): sem imports de biblioteca/server.
"""
import io
import json
import os
from datetime import timezone
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from tests import test_runner_comum  # noqa: F401  (sys.path com a raiz do repo)

import arquivo

NOW = 1794940440.0   # 2026-11-17T18:34:00Z
FONTES = ("material", "relatorios")


# --- regras de caminho ----------------------------------------------------------------------------------------------

def test_classificar_allowlist():
    for nome, tipo in (("a.JPG", "imagem"), ("a.webp", "imagem"), ("a.m4v", "video"), ("a.opus", "audio"),
                       ("a.md", "doc"), ("a.csv", "doc"), ("a.pdf", "pdf")):
        assert arquivo.classificar(("x", nome), True) == tipo, nome
    for nome in ("a.json", "a.html", "a.htm", "a.svg", "a.js", "prazos.md.tmp-ignore", "README", "a.exe"):
        assert arquivo.classificar((nome,), True) is None, nome


def test_classificar_denylist_e_profundidade():
    for nome in (".env", "x.env", ".env.local", "chave.key", "cert.pem", "a.p12", "b.pfx", "id_rsa", "id_rsa.pub",
                 "id_ed25519", "credentials.md", "segredo.txt", "MyPassword.txt", "passwd.md", "my-secret.md",
                 ".oculto.md", "caf\udce9.md"):
        assert arquivo.classificar((nome,), True) is None, nome
    for pasta in (".git", ".arquivo", "dados", "node_modules", "__pycache__", ".venv"):
        assert arquivo.classificar((pasta, "a.md"), True) is None, pasta
    for parte in ("", ".", "..", "a\\b", "a\x00"):
        assert arquivo.classificar(("x", parte, "a.md"), True) is None, repr(parte)
    assert arquivo.classificar(tuple("abcdefg") + ("a.md",), True) == "doc"     # 8 componentes
    assert arquivo.classificar(tuple("abcdefgh") + ("a.md",), True) is None    # 9
    assert arquivo.classificar((), True) is None


def test_media_so_em_fontes_media():
    assert arquivo.classificar(("x.jpg",), False) is None
    assert arquivo.classificar(("x.mp4",), False) is None
    assert arquivo.classificar(("x.pdf",), False) == "pdf"
    assert arquivo.classificar(("x.jpg",), True) == "imagem"


def test_raiz_nome():
    for n in ("x.md", "x.copy.md", "x.juridico.md", "x.md.revisao.md", "x.md.juridico.md", "x.revisao-2.md",
              "x.parecer-juridico.md"):
        assert arquivo.raiz_nome(n) == "x", n
    assert arquivo.raiz_nome("x-pt-revisao.md") == "x-pt-revisao"


def _w(root, rel, texto="# x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, encoding="utf-8")
    return p


def test_varrer_ignora_negados_symlinks_e_escondidas(tmp_path):
    root = tmp_path / "fonte"
    for rel in ("a.json", ".env", "chave.key", ".oculto.md", ".git/a.md", ".arquivo/a.md", "dados/a.md",
                "node_modules/a.md", "__pycache__/a.md", "x.jpg"):
        _w(root, rel)
    _w(root, "ok.md")
    _w(root, "sub/b.pdf")
    _w(tmp_path, "fora/s.md")
    (root / "ligado.md").symlink_to(tmp_path / "fora" / "s.md")
    (root / "pasta-ligada").symlink_to(tmp_path / "fora")
    out = arquivo.varrer(root, False, "fonte")
    assert sorted((p, t) for p, _, _, t in out) == [(("ok.md",), "doc"), (("sub", "b.pdf"), "pdf")]
    _, caminho, st, _ = next(x for x in out if x[0] == ("ok.md",))
    assert caminho == str(root / "ok.md") and st.st_size == 3
    assert ("x.jpg",) in [p for p, *_ in arquivo.varrer(root, True, "fonte")]


def test_varrer_corta_nas_entradas_max(tmp_path):
    for i in range(6):
        _w(tmp_path, f"r{i}.md")
    with mock.patch.object(arquivo, "ENTRADAS_MAX", 3), mock.patch("sys.stderr", new=io.StringIO()) as err:
        out = arquivo.varrer(tmp_path, False, "relatorios")
    assert len(out) == 3
    assert "relatorios com mais de 3 entradas, varrimento cortado" in err.getvalue()


def test_varrer_raiz_inexistente(tmp_path):
    assert arquivo.varrer(tmp_path / "nao", True, "x") == []


def test_citacoes():
    rx = arquivo.citacao_re(("rascunhos",))
    t = ("![01](../rascunhos/media/l/final/a.jpg)\n`rascunhos/x/b.md`.\nver rascunhos/x/c.md, e "
         "«rascunhos/x/d.md»; e (rascunhos/x/e.md) | rascunhos/x/f.md:\nprefixo-rascunhos/x/g.md\n"
         "revisor: ok — rascunhos/x/h.md · fim\n**rascunhos/media/l/** e rascunhos/media/l")
    assert [c for _, c in arquivo.citacoes(t, rx)] == [
        "rascunhos/media/l/final/a.jpg", "rascunhos/x/b.md", "rascunhos/x/c.md", "rascunhos/x/d.md",
        "rascunhos/x/e.md", "rascunhos/x/f.md", "rascunhos/x/h.md", "rascunhos/media/l/**", "rascunhos/media/l"]
    assert arquivo.citacoes("a\nb rascunhos/x.md", rx)[0][0] == "b rascunhos/x.md"


def test_citacoes_varias_fontes_e_vazio():
    rx = arquivo.citacao_re(("material", "a.b"))
    assert [c for _, c in arquivo.citacoes("material/x.md rascunhos/y.md a.b/z.md axb/w.md", rx)] == [
        "material/x.md", "a.b/z.md"]
    assert arquivo.citacoes("material/x.md", arquivo.citacao_re(())) == []


@pytest.mark.parametrize("chave", ["x.md", "a/b/c.md", "media/2026-11-01-lote", "café.md", "a" * 1024,
                                   "/".join("abcdefg") + "/x.md"])
def test_validar_chave_aceita(chave):
    assert arquivo.validar_chave(chave)


@pytest.mark.parametrize("chave", ["", None, 3, ["x.md"], "a" * 1025, "/".join("abcdefgh") + "/x.md", "../x.md",
                                   "a/../x.md", "./x.md", ".x.md", "a/.git/x.md", "a//x.md", "/x.md", "x.md/",
                                   "a\\b.md", "a\x00.md", "a\tb.md", "a\nb.md", "a\x7f.md", "segredo.md", ".env",
                                   "dados/x.md", "node_modules/x.md", "caf\udce9.md"])
def test_validar_chave_recusa(chave):
    assert not arquivo.validar_chave(chave)


# --- fila: leitura ------------------------------------------------------------------------------------------------

def _pedido(stem="20261117T183400-0a1b2c3d", **kw):
    p = {"v": 1, "id": stem, "accao": "arquivar", "fonte": "material", "chave": "relatorios/x.md",
         "pedido_em": "2026-11-17T18:34:00+00:00", "ip": "10.0.0.1", "ua": "Teste"}
    p.update(kw)
    return p


def test_pedido_ok():
    stem = "20261117T183400-0a1b2c3d"
    assert arquivo.pedido_ok(_pedido(), stem, FONTES)
    assert arquivo.pedido_ok(_pedido(accao="repor"), stem, FONTES)
    maus = [_pedido(v=2), _pedido(v=True), _pedido(v="1"), _pedido(id="20261117T183400-ffffffff"),
            _pedido(accao="apagar"), _pedido(fonte="outra"), _pedido(fonte=["material"]), _pedido(chave="../x.md"),
            _pedido(chave=".x/a.md"), _pedido(pedido_em=1), _pedido(pedido_em="x" * 41), _pedido(ip="x" * 65),
            _pedido(ua="x" * 201), _pedido(ua=None), [], "x", None]
    for p in maus:
        assert not arquivo.pedido_ok(p, stem, FONTES), p
    fora_de_ordem = dict(reversed(list(_pedido().items())))
    assert not arquivo.pedido_ok(fora_de_ordem, stem, FONTES)
    assert not arquivo.pedido_ok(_pedido() | {"extra": 1}, stem, FONTES)
    sem = _pedido()
    del sem["ua"]
    assert not arquivo.pedido_ok(sem, stem, FONTES)


def test_feito_ok():
    assert arquivo.feito_ok({"ok": True}) == {"ok": True}
    assert arquivo.feito_ok({"erro": "destino já existe"}) == {"erro": "destino já existe"}
    for d in ({"ok": 1}, {"ok": False}, {"erro": "outro motivo"}, {"ok": True, "erro": "item inexistente"}, {}, [],
              None, {"erro": None}):
        assert arquivo.feito_ok(d) is None, d


def test_ler_json_regras(tmp_path):
    (tmp_path / "ok.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "grande.json").write_text(json.dumps({"a": "x" * 5000}), encoding="utf-8")
    (tmp_path / "lixo.json").write_bytes(b"\xff{")
    (tmp_path / "ligado.json").symlink_to(tmp_path / "ok.json")
    (tmp_path / "pasta.json").mkdir()
    os.mkfifo(tmp_path / "fifo.json")
    dfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert arquivo.ler_json(dfd, "ok.json") == {"a": 1}
        for nome in ("grande.json", "lixo.json", "ligado.json", "pasta.json", "fifo.json", "nao.json"):
            assert arquivo.ler_json(dfd, nome) is None, nome
        assert arquivo.ler_json(dfd, "grande.json", 10_000) == {"a": "x" * 5000}
    finally:
        os.close(dfd)


def _grava(fila, nome, d):
    (fila / nome).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def test_ler_fila(tmp_path):
    fila = tmp_path / "fila"
    fila.mkdir()
    a, b, c, d = ("20261117T183400-0000000a", "20261117T183401-0000000b", "20261117T183402-0000000c",
                  "20261117T183403-0000000d")
    for stem in (a, b, c, d):
        _grava(fila, stem + ".json", _pedido(stem))
    _grava(fila, a + ".feito.json", {"ok": True})
    _grava(fila, b + ".feito.json", {"erro": "não arquivável"})
    _grava(fila, c + ".feito.json", {"erro": "inventado"})
    _grava(fila, "20261117T183404-0000000e.json", _pedido("20261117T183404-0000000e", v=2))   # inválido: fora
    _grava(fila, "lixo.json", _pedido())
    (fila / "feito").mkdir()
    _grava(fila / "feito", "20261117T183405-0000000f.json", _pedido("20261117T183405-0000000f"))   # só a raiz
    out = arquivo.ler_fila(fila, FONTES)
    assert [(s, f) for s, _, f in out] == [(a, {"ok": True}), (b, {"erro": "não arquivável"}), (c, {}), (d, None)]
    assert out[0][1] == _pedido(a)
    assert arquivo.ler_fila(fila, ("outra",)) == []
    assert arquivo.ler_fila(tmp_path / "nao-existe", FONTES) == []


# --- fila: escrita ------------------------------------------------------------------------------------------------

def test_escrever_pedido_grava_v1(tmp_path):
    fila = tmp_path / "fila"
    fila.mkdir()
    code, body = arquivo.escrever_pedido(fila, "arquivar", "material", "relatorios/x.md", "10.0.0.1", "U" * 300,
                                         ZoneInfo("Europe/Lisbon"), NOW)
    assert code == 202 and body["accao"] == "arquivar"
    assert arquivo.NOME.fullmatch(body["id"] + ".json") and body["id"].startswith("20261117T183400-")
    nomes = os.listdir(fila)
    assert nomes == [body["id"] + ".json"]      # sem tmp para trás
    p = json.loads((fila / nomes[0]).read_text(encoding="utf-8"))
    assert list(p) == list(arquivo.CHAVES)
    assert p["pedido_em"] == "2026-11-17T18:34:00+00:00" and len(p["ua"]) == arquivo.UA_MAX
    assert arquivo.pedido_ok(p, body["id"], FONTES)


def test_escrever_pedido_fuso_no_nome(tmp_path):
    code, body = arquivo.escrever_pedido(tmp_path, "repor", "material", "x.md", "", "", ZoneInfo("Asia/Tokyo"), NOW)
    assert code == 202 and body["id"].startswith("20261118T033400-")


def test_escrever_pedido_409_qualquer_accao_pendente(tmp_path):
    tz = timezone.utc
    assert arquivo.escrever_pedido(tmp_path, "arquivar", "material", "x.md", "", "", tz, NOW)[0] == 202
    for accao in ("arquivar", "repor"):
        code, body = arquivo.escrever_pedido(tmp_path, accao, "material", "x.md", "", "", tz, NOW)
        assert (code, body) == (409, {"error": "já há um pedido em fila para este item"})
    assert arquivo.escrever_pedido(tmp_path, "arquivar", "relatorios", "x.md", "", "", tz, NOW)[0] == 202
    assert arquivo.escrever_pedido(tmp_path, "arquivar", "material", "y.md", "", "", tz, NOW)[0] == 202
    # respondido ⇒ deixa de estar pendente
    stem = next(s for s, p, _ in arquivo.ler_fila(tmp_path, FONTES) if (p["fonte"], p["chave"]) == ("material", "x.md"))
    _grava(tmp_path, stem + ".feito.json", {"ok": True})
    assert arquivo.escrever_pedido(tmp_path, "repor", "material", "x.md", "", "", tz, NOW)[0] == 202


def test_escrever_pedido_429_aos_50_pendentes(tmp_path):
    tz = timezone.utc
    for i in range(arquivo.PENDENTES_MAX - 1):
        assert arquivo.escrever_pedido(tmp_path, "arquivar", "material", f"x{i}.md", "", "", tz, NOW)[0] == 202
    # pendente de uma fonte que já saiu da config também conta
    _grava(tmp_path, "20261101T000000-00000000.json",
           _pedido("20261101T000000-00000000", fonte="antiga"))
    assert arquivo.escrever_pedido(tmp_path, "arquivar", "material", "z.md", "", "", tz, NOW) == (
        429, {"error": "fila cheia"})
    stem = arquivo.ler_fila(tmp_path, FONTES)[0][0]
    _grava(tmp_path, stem + ".feito.json", {"erro": "item inexistente"})
    assert arquivo.escrever_pedido(tmp_path, "arquivar", "material", "z.md", "", "", tz, NOW)[0] == 202


def test_escrever_pedido_503(tmp_path):
    tz = timezone.utc
    erro = (503, {"error": "fila do arquivo indisponível"})
    assert arquivo.escrever_pedido(tmp_path / "nao", "arquivar", "material", "x.md", "", "", tz, NOW) == erro
    (tmp_path / "ficheiro").write_text("x")
    assert arquivo.escrever_pedido(tmp_path / "ficheiro", "arquivar", "material", "x.md", "", "", tz, NOW) == erro
    if os.getuid() != 0:
        ro = tmp_path / "ro"
        ro.mkdir(mode=0o500)
        try:
            assert arquivo.escrever_pedido(ro, "arquivar", "material", "x.md", "", "", tz, NOW) == erro
        finally:
            ro.chmod(0o700)


def test_escrever_pedido_colisao_de_nome(tmp_path):
    with mock.patch.object(arquivo.secrets, "token_hex", return_value="0a1b2c3d"):
        assert arquivo.escrever_pedido(tmp_path, "arquivar", "material", "x.md", "", "", timezone.utc, NOW)[0] == 202
        code, _ = arquivo.escrever_pedido(tmp_path, "arquivar", "material", "y.md", "", "", timezone.utc, NOW)
    assert code == 503
    assert len(os.listdir(tmp_path)) == 1     # o 1.º não foi sobrescrito, sem tmp para trás


# --- REGISTO e protecções -----------------------------------------------------------------------------------------

def test_linha_registo():
    assert arquivo.linha_registo("2026-11-17T18:34:00+00:00", "arquivar", "a/x.md", "pedido no Office", "a/x.md") == (
        "2026-11-17T18:34:00+00:00\tarquivar\ta/x.md\tpedido no Office\ta/x.md\n")
    for mau in ("a\tb", "a\nb", "a\rb"):
        assert arquivo.linha_registo("d", "arquivar", mau, "m", "i") is None
        assert arquivo.linha_registo("d", "arquivar", "c", mau, "i") is None
        assert arquivo.linha_registo("d", "arquivar", "c", "m", mau) is None
    assert arquivo.linha_registo("d", "arquivar", None, "m", "i") is None


def test_ler_registo_ultima_ganha():
    texto = (arquivo.CABECALHO
             + "d1\tarquivar\tmedia/l/a.jpg\tpedido no Office\tmedia/l\n"
             + "d1\tarquivar\tmedia/l/b.jpg\tpedido no Office\tmedia/l\n"
             + "lixo sem tabs\n" + "d0\tapagar\tz.md\tm\tz.md\n" + "a\tb\n"
             + "d2\trepor\tmedia/l/a.jpg\treposto a pedido do Office\tmedia/l\n"
             + "d3\tarquivar\tx.md\tidade (30 dias)\tx.md\r\n")
    item, caminho = arquivo.ler_registo(texto)
    assert item == {"media/l": ("d2", "repor", "reposto a pedido do Office"),
                    "x.md": ("d3", "arquivar", "idade (30 dias)")}
    assert caminho["media/l/b.jpg"] == ("d1", "arquivar", "pedido no Office")
    assert caminho["media/l/a.jpg"][1] == "repor" and "z.md" not in caminho
    assert arquivo.ler_registo("") == ({}, {})


def test_fechado():
    for e in ("aprovado", "executado", "recusado", " aprovado ", "aprovado # pelo Ana"):
        assert arquivo.fechado(e), e
    for e in ("pendente", "Aprovado", "parcial (1 de 2)", "bloqueado: IVA", "", None, 1, "# aprovado"):
        assert not arquivo.fechado(e), e


def test_erros_e_constantes_do_contrato():
    assert arquivo.CHAVES == ("v", "id", "accao", "fonte", "chave", "pedido_em", "ip", "ua")
    assert arquivo.ACCOES == ("arquivar", "repor") and arquivo.GENEROS == ("documento", "lote")
    assert (arquivo.PEDIDO_MAX, arquivo.PENDENTES_MAX, arquivo.RECENTE_S, arquivo.CITADO_AUTO_S) == (
        4096, 50, 48 * 3600, 14 * 86400)
    assert len(set(arquivo.ERROS)) == len(arquivo.ERROS) == 14
    assert not arquivo.NOME.fullmatch("20261117T183400-0A1B2C3D.json")
    assert arquivo.FEITO.fullmatch("20261117T183400-0a1b2c3d.feito.json")
