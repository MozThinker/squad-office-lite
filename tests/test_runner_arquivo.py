"""Runner: fila do arquivo da Biblioteca (AC13–AC15): revalidação, mover tudo ou nada, REGISTO, protecções, rotação.

Tudo em tmp_path, `NOW` fixo, mtimes por os.utime; o contentor é hostil (symlinks, pedidos mal formados).
"""
import errno
import json
import os
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import office_runner, officecfg

import arquivador
import arquivo

NOW = 1794940440.0          # 2026-11-17T18:34:00Z
DIA = 86400
STEM = "20261117T183400-0a1b2c3d"


def config(tmp: Path, extra_material: str = "", extra_notas: str = "", rotacao: int = 14):
    """office.yaml com duas fontes: `material` (media) e `notas` (só documentos)."""
    texto = f"""versao: 1
marca: Office Teste
utilizador:
  nome: Ana
fuso: UTC
raizes:
  dados: {tmp}/dados
  projectos: {tmp}/projectos
  claude_projects: {tmp}/claude
squads:
  - id: dev
    nome: Dev
projectos:
  - id: exemplo
    nome: Exemplo
biblioteca:
  - id: material
    rotulo: Material
    pasta: material
    media: true
{extra_material}  - id: notas
    rotulo: Notas
    pasta: notas
{extra_notas}runner:
  rotacao_dias: {rotacao}
"""
    path = tmp / "office.yaml"
    path.write_text(texto, encoding="utf-8")
    cfg, _ = officecfg.carregar(path, estrito=True)
    for modo, p in officecfg.pastas(cfg):
        p.mkdir(parents=True, exist_ok=True, mode=int(modo, 8))
    return cfg, path


def bib(cfg) -> Path:
    return officecfg.caminho(cfg, "biblioteca")


def w(cfg, rel: str, texto: str = "# x\n", idade: float = 10 * DIA) -> Path:
    p = bib(cfg) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, encoding="utf-8")
    os.utime(p, (NOW - idade, NOW - idade))
    return p


def pedir(cfg, accao: str, fonte: str, chave: str, stem: str = STEM, **kw) -> Path:
    p = {"v": 1, "id": stem, "accao": accao, "fonte": fonte, "chave": chave,
         "pedido_em": "2026-11-17T18:34:00+00:00", "ip": "10.0.0.1", "ua": "Teste"}
    p.update(kw)
    path = officecfg.caminho(cfg, "arquivo_fila") / f"{stem}.json"
    path.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    return path


def feito(cfg, stem: str = STEM):
    path = officecfg.caminho(cfg, "arquivo_fila") / f"{stem}.feito.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def aprovacao(cfg, nome: str, estado: str | None, *cita: str, idade: float = 30 * DIA) -> Path:
    front = f"---\nestado: {estado}\n---\n" if estado is not None else ""
    p = officecfg.caminho(cfg, "decisoes") / nome
    p.write_text(front + "# Pedido\n\n" + "\n".join(f"- ver `{x}`" for x in cita) + "\n", encoding="utf-8")
    os.utime(p, (NOW - idade, NOW - idade))
    return p


def registo(cfg, fonte: str) -> list[list[str]]:
    texto = (bib(cfg) / fonte / ".arquivo" / "REGISTO.tsv").read_text(encoding="utf-8")
    assert texto.startswith(arquivo.CABECALHO)
    return [x.split("\t") for x in texto[len(arquivo.CABECALHO):].splitlines()]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    return config(tmp_path)[0]


def passar(cfg):
    return arquivador.processar(cfg, NOW)


# ---------------------------------------------------------------- arquivar e repor

def test_arquivar_documento_move_o_grupo_e_regista(cfg):
    for n in ("rel/x.md", "rel/x.copy.md", "rel/x.md.revisao.md", "rel/y.md", "rel/x-outro.md"):
        w(cfg, f"notas/{n}")
    pedir(cfg, "arquivar", "notas", "rel/x.md")
    linhas = passar(cfg)
    assert feito(cfg) == {"ok": True}
    n = bib(cfg) / "notas"
    assert sorted(p.name for p in (n / ".arquivo" / "rel").iterdir()) == ["x.copy.md", "x.md", "x.md.revisao.md"]
    assert sorted(p.name for p in (n / "rel").iterdir()) == ["x-outro.md", "y.md"]
    assert linhas == ["biblioteca: arquivado notas/rel/x.md (3 ficheiros)"]
    assert registo(cfg, "notas") == [
        ["2026-11-17T18:34:00+00:00", "arquivar", f"rel/{x}", "pedido no Office", "rel/x.md"]
        for x in ("x.copy.md", "x.md", "x.md.revisao.md")]
    assert not (n / ".arquivo" / ".gitignore").exists()   # sem git, sem .gitignore


def test_repor_devolve_ao_sitio_e_regista(cfg):
    w(cfg, "notas/rel/x.md")
    pedir(cfg, "arquivar", "notas", "rel/x.md")
    passar(cfg)
    outro = "20261117T183500-0a1b2c3e"
    pedir(cfg, "repor", "notas", "rel/x.md", stem=outro)
    assert passar(cfg) == ["biblioteca: reposto notas/rel/x.md (1 ficheiros)"]
    assert feito(cfg, outro) == {"ok": True}
    assert (bib(cfg) / "notas/rel/x.md").read_text(encoding="utf-8") == "# x\n"
    assert not (bib(cfg) / "notas/.arquivo/rel/x.md").exists()
    assert [x[1:4] for x in registo(cfg, "notas")] == [["arquivar", "rel/x.md", "pedido no Office"],
                                                       ["repor", "rel/x.md", "reposto a pedido do Office"]]


def test_repor_so_do_arquivo(cfg):
    w(cfg, "notas/x.md")
    pedir(cfg, "repor", "notas", "x.md")
    passar(cfg)
    assert feito(cfg) == {"erro": "não está no arquivo"}
    assert (bib(cfg) / "notas/x.md").exists()


def test_lote_inteiro_num_so_rename(cfg):
    w(cfg, "material/media/lote-a/a.jpg")
    w(cfg, "material/media/lote-a/final/b.png")
    w(cfg, "material/media/lote-a/notas.txt")
    w(cfg, "material/media/lote-b/c.jpg")
    pedir(cfg, "arquivar", "material", "media/lote-a")
    passar(cfg)
    assert feito(cfg) == {"ok": True}
    m = bib(cfg) / "material"
    assert not (m / "media/lote-a").exists() and (m / "media/lote-b/c.jpg").exists()
    assert (m / ".arquivo/media/lote-a/final/b.png").exists()
    assert sorted(x[2] for x in registo(cfg, "material")) == [
        "media/lote-a/a.jpg", "media/lote-a/final/b.png", "media/lote-a/notas.txt"]
    assert {x[4] for x in registo(cfg, "material")} == {"media/lote-a"}


@pytest.mark.parametrize("fonte, chave, rel", [
    ("material", "media/lote-a/a.jpg", "material/media/lote-a/a.jpg"),   # dentro de um lote: só o lote inteiro
    ("material", "fotos/a.jpg", "material/fotos/a.jpg"),                 # media fora de media/ («pasta»)
    ("notas", "a.jpg", "notas/a.jpg"),                                   # media numa fonte media: false
    ("notas", "a.json", "notas/a.json"),                                 # fora da allowlist
    ("notas", "rel", "notas/rel/x.md"),                                  # pasta
    ("material", "media", "material/media/l/a.jpg"),
])
def test_nao_arquivavel(cfg, fonte, chave, rel):
    w(cfg, rel)
    pedir(cfg, "arquivar", fonte, chave)
    passar(cfg)
    assert feito(cfg) == {"erro": "não arquivável"}
    assert (bib(cfg) / rel).exists()


def test_lote_sem_nada_visivel_e_inexistente(cfg):
    w(cfg, "material/media/vazio/a.json")
    pedir(cfg, "arquivar", "material", "media/vazio")
    passar(cfg)
    assert feito(cfg) == {"erro": "item inexistente"}


def test_documento_em_media_de_fonte_media_com_dois_componentes(cfg):
    w(cfg, "material/media/leia-me.md")     # media/<x>.md não é lote: é um documento
    pedir(cfg, "arquivar", "material", "media/leia-me.md")
    passar(cfg)
    assert feito(cfg) == {"ok": True}
    assert (bib(cfg) / "material/.arquivo/media/leia-me.md").exists()


def test_item_inexistente(cfg):
    pedir(cfg, "arquivar", "notas", "nao/existe.md")
    passar(cfg)
    assert feito(cfg) == {"erro": "item inexistente"}


# ---------------------------------------------------------------- pedidos mal formados (contentor hostil)

@pytest.mark.parametrize("kw", [{"v": 2}, {"v": True}, {"id": "20261117T183400-ffffffff"}, {"accao": "apagar"},
                                {"chave": "../fora.md"}, {"chave": "a/../x.md"}, {"chave": ".x/a.md"},
                                {"chave": "x\tb.md"}, {"ua": "u" * 201}, {"extra": 1}])
def test_pedido_ilegivel(cfg, kw):
    w(cfg, "notas/x.md")
    base = {"accao": "arquivar", "fonte": "notas", "chave": "x.md"} | kw
    pedir(cfg, base.pop("accao"), base.pop("fonte"), base.pop("chave"), **base)
    linhas = passar(cfg)
    assert feito(cfg) == {"erro": "pedido ilegível"}
    assert linhas == [f"biblioteca: pedido {STEM} recusado: pedido ilegível"]
    assert (bib(cfg) / "notas/x.md").exists()


def test_pedido_chaves_fora_de_ordem_grande_ou_symlink(cfg, tmp_path):
    w(cfg, "notas/x.md")
    fila = officecfg.caminho(cfg, "arquivo_fila")
    bom = {"v": 1, "id": STEM, "accao": "arquivar", "fonte": "notas", "chave": "x.md",
           "pedido_em": "2026-11-17T18:34:00+00:00", "ip": "", "ua": ""}
    casos = {"20261117T183400-00000001": dict(reversed(list(bom.items()))),
             "20261117T183400-00000002": dict(bom, ua="u" * 5000)}
    for stem, d in casos.items():
        (fila / f"{stem}.json").write_text(json.dumps(dict(d, id=stem)), encoding="utf-8")
    alvo = tmp_path / "fora.json"
    alvo.write_text(json.dumps(dict(bom, id="20261117T183400-00000003")), encoding="utf-8")
    (fila / "20261117T183400-00000003.json").symlink_to(alvo)
    passar(cfg)
    for stem in ("20261117T183400-00000001", "20261117T183400-00000002", "20261117T183400-00000003"):
        assert feito(cfg, stem) == {"erro": "pedido ilegível"}, stem
    assert (bib(cfg) / "notas/x.md").exists()


def test_fonte_desconhecida(cfg):
    pedir(cfg, "arquivar", "outra", "x.md")
    passar(cfg)
    assert feito(cfg) == {"erro": "fonte desconhecida"}


def test_nome_invalido_e_ja_respondido_ignorados(cfg):
    w(cfg, "notas/x.md")
    fila = officecfg.caminho(cfg, "arquivo_fila")
    pedir(cfg, "arquivar", "notas", "x.md")
    (fila / f"{STEM}.feito.json").write_text('{"ok": true}', encoding="utf-8")
    (fila / "lixo.json").write_text("{}", encoding="utf-8")
    assert passar(cfg) == []
    assert (bib(cfg) / "notas/x.md").exists()
    assert not (fila / "lixo.feito.json").exists()


# ---------------------------------------------------------------- symlinks

def test_symlink_num_componente(cfg, tmp_path):
    fora = tmp_path / "fora"
    (fora / "sub").mkdir(parents=True)
    (fora / "sub" / "x.md").write_text("# fora", encoding="utf-8")
    (bib(cfg) / "notas").mkdir(parents=True, exist_ok=True)
    (bib(cfg) / "notas" / "ligada").symlink_to(fora / "sub")
    pedir(cfg, "arquivar", "notas", "ligada/x.md")
    passar(cfg)
    assert feito(cfg) == {"erro": "symlink no caminho"}
    assert (fora / "sub" / "x.md").exists()


def test_symlink_no_ficheiro(cfg, tmp_path):
    alvo = tmp_path / "alvo.md"
    alvo.write_text("# alvo", encoding="utf-8")
    (bib(cfg) / "notas").mkdir(parents=True, exist_ok=True)
    (bib(cfg) / "notas" / "x.md").symlink_to(alvo)
    pedir(cfg, "arquivar", "notas", "x.md")
    passar(cfg)
    assert feito(cfg) == {"erro": "symlink no caminho"}
    assert (bib(cfg) / "notas" / "x.md").is_symlink()


def test_arquivo_trocado_por_symlink(cfg, tmp_path):
    w(cfg, "notas/x.md")
    fora = tmp_path / "fora"
    fora.mkdir()
    (bib(cfg) / "notas" / ".arquivo").symlink_to(fora)
    pedir(cfg, "arquivar", "notas", "x.md")
    passar(cfg)
    assert feito(cfg) == {"erro": "symlink no caminho"}
    assert (bib(cfg) / "notas/x.md").exists() and list(fora.iterdir()) == []


def test_raiz_da_fonte_pode_ser_symlink(cfg, tmp_path):
    real = tmp_path / "real-notas"
    (real / "rel").mkdir(parents=True)
    (real / "rel" / "x.md").write_text("# x", encoding="utf-8")
    os.utime(real / "rel" / "x.md", (NOW - 10 * DIA, NOW - 10 * DIA))
    (bib(cfg) / "notas").rmdir()   # criada pelo officecfg.pastas
    (bib(cfg) / "notas").symlink_to(real)
    pedir(cfg, "arquivar", "notas", "rel/x.md")
    passar(cfg)
    assert feito(cfg) == {"ok": True}
    assert (real / ".arquivo" / "rel" / "x.md").exists()


# ---------------------------------------------------------------- tudo ou nada

def test_destino_ja_existe_nada_mexe(cfg):
    w(cfg, "notas/rel/x.md")
    w(cfg, "notas/rel/x.copy.md")
    w(cfg, "notas/.arquivo/rel/x.md", "# antigo\n")
    pedir(cfg, "arquivar", "notas", "rel/x.md")
    passar(cfg)
    assert feito(cfg) == {"erro": "destino já existe"}
    n = bib(cfg) / "notas"
    assert (n / "rel/x.md").exists() and (n / "rel/x.copy.md").exists()
    assert (n / ".arquivo/rel/x.md").read_text(encoding="utf-8") == "# antigo\n"
    assert not (n / ".arquivo/rel/x.copy.md").exists()


def test_falha_no_segundo_rename_desfaz_o_primeiro(cfg, monkeypatch):
    w(cfg, "notas/rel/x.md")
    w(cfg, "notas/rel/x.copy.md")
    real, chamadas = os.rename, []

    def rename(a, b, **kw):
        if a.startswith("."):          # a resposta da fila (tmp + rename) não conta
            return real(a, b, **kw)
        chamadas.append(a)
        if len(chamadas) == 2:
            raise OSError(errno.EIO, "falha")
        return real(a, b, **kw)

    monkeypatch.setattr(os, "rename", rename)
    pedir(cfg, "arquivar", "notas", "rel/x.md")
    passar(cfg)
    monkeypatch.setattr(os, "rename", real)
    assert feito(cfg) == {"erro": "erro a mover"}
    assert len(chamadas) == 3     # 2 tentativas + 1 a desfazer
    n = bib(cfg) / "notas"
    assert (n / "rel/x.md").exists() and (n / "rel/x.copy.md").exists()
    assert not (n / ".arquivo/rel/x.md").exists()
    assert not (n / ".arquivo" / "REGISTO.tsv").exists()


def test_exdev_nada_movido(cfg, monkeypatch):
    w(cfg, "notas/x.md")
    real = os.rename

    def rename(a, b, **kw):
        if a.startswith("."):          # a resposta da fila (tmp + rename) não conta
            return real(a, b, **kw)
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", rename)
    pedir(cfg, "arquivar", "notas", "x.md")
    passar(cfg)
    monkeypatch.undo()
    assert feito(cfg) == {"erro": "sistemas de ficheiros diferentes"}
    assert (bib(cfg) / "notas/x.md").exists()


def test_nome_com_tab_recusado_antes_de_mover(cfg):
    w(cfg, "material/media/lote-a/a.jpg")
    w(cfg, "material/media/lote-a/b\tc.jpg")
    pedir(cfg, "arquivar", "material", "media/lote-a")
    passar(cfg)
    assert feito(cfg) == {"erro": "nome com tab ou quebra de linha"}
    assert (bib(cfg) / "material/media/lote-a/a.jpg").exists()
    assert not (bib(cfg) / "material/.arquivo").exists()


# ---------------------------------------------------------------- protecções (AC15)

@pytest.mark.parametrize("estado", ["pendente", "parcial (1 de 2 decididas)", "outro", "", None])
def test_protegido_por_pedido_pendente_ou_desconhecido(cfg, estado):
    w(cfg, "notas/rel/x.md")
    aprovacao(cfg, "p.md", estado, "notas/rel/x.md")
    pedir(cfg, "arquivar", "notas", "rel/x.md")
    passar(cfg)
    assert feito(cfg) == {"erro": "protegido: citado por um pedido de aprovação pendente"}
    assert (bib(cfg) / "notas/rel/x.md").exists()


@pytest.mark.parametrize("estado", ["aprovado", "executado", "recusado"])
def test_pedido_fechado_nao_protege(cfg, estado):
    w(cfg, "notas/rel/x.md")
    aprovacao(cfg, "p.md", estado, "notas/rel/x.md")
    pedir(cfg, "arquivar", "notas", "rel/x.md")
    passar(cfg)
    assert feito(cfg) == {"ok": True}


@pytest.mark.parametrize("citado", ["material/media/lote-a/a.jpg", "material/media/lote-a", "material/media/lote-a/**",
                                    "../material/media/lote-a/final/"])
def test_citacao_do_lote_ou_de_dentro(cfg, citado):
    w(cfg, "material/media/lote-a/a.jpg")
    aprovacao(cfg, "p.md", "pendente", citado)
    pedir(cfg, "arquivar", "material", "media/lote-a")
    passar(cfg)
    assert feito(cfg) == {"erro": "protegido: citado por um pedido de aprovação pendente"}


def test_citacao_da_pasta_que_contem_e_de_outra_fonte(cfg):
    w(cfg, "notas/rel/x.md")
    aprovacao(cfg, "a.md", "pendente", "material/rel/x.md", "notas/rel-b/x.md", "notas/rel/x.md.bak")
    pedir(cfg, "arquivar", "notas", "rel/x.md")
    passar(cfg)
    assert feito(cfg) == {"ok": True}       # outra fonte, outra pasta e outro nome não protegem
    w(cfg, "notas/rel/y.md")
    aprovacao(cfg, "b.md", "pendente", "notas/rel")
    pedir(cfg, "arquivar", "notas", "rel/y.md", stem="20261117T183500-0a1b2c3e")
    passar(cfg)
    assert feito(cfg, "20261117T183500-0a1b2c3e") == {"erro": "protegido: citado por um pedido de aprovação pendente"}


def test_aprovacao_symlink_ou_grande_ignorada(cfg, tmp_path):
    w(cfg, "notas/x.md")
    fora = tmp_path / "p.md"
    fora.write_text("---\nestado: pendente\n---\nnotas/x.md\n", encoding="utf-8")
    (officecfg.caminho(cfg, "decisoes") / "ligado.md").symlink_to(fora)
    grande = officecfg.caminho(cfg, "decisoes") / "grande.md"
    grande.write_text("---\nestado: pendente\n---\nnotas/x.md\n" + "x" * (1024 * 1024), encoding="utf-8")
    pedir(cfg, "arquivar", "notas", "x.md")
    passar(cfg)
    assert feito(cfg) == {"ok": True}


def test_protegido_alterado_ha_menos_de_48h(cfg):
    w(cfg, "notas/rel/x.md")
    w(cfg, "notas/rel/x.copy.md", idade=47 * 3600)    # o mais recente do grupo conta
    pedir(cfg, "arquivar", "notas", "rel/x.md")
    passar(cfg)
    assert feito(cfg) == {"erro": "protegido: alterado há menos de 48 h"}
    w(cfg, "notas/rel/x.copy.md", idade=49 * 3600)
    pedir(cfg, "arquivar", "notas", "rel/x.md", stem="20261117T183500-0a1b2c3e")
    passar(cfg)
    assert feito(cfg, "20261117T183500-0a1b2c3e") == {"ok": True}


def test_lote_alterado_ha_menos_de_48h_dentro(cfg):
    w(cfg, "material/media/lote-a/a.jpg")
    w(cfg, "material/media/lote-a/fundo/b.jpg", idade=3600)
    pedir(cfg, "arquivar", "material", "media/lote-a")
    passar(cfg)
    assert feito(cfg) == {"erro": "protegido: alterado há menos de 48 h"}


def test_repor_sem_proteccoes(cfg):
    w(cfg, "notas/x.md")
    pedir(cfg, "arquivar", "notas", "x.md")
    passar(cfg)
    aprovacao(cfg, "p.md", "pendente", "notas/x.md")
    os.utime(bib(cfg) / "notas/.arquivo/x.md", (NOW, NOW))
    pedir(cfg, "repor", "notas", "x.md", stem="20261117T183500-0a1b2c3e")
    passar(cfg)
    assert feito(cfg, "20261117T183500-0a1b2c3e") == {"ok": True}
    assert (bib(cfg) / "notas/x.md").exists()


def test_protegido_funcao_publica(cfg):
    w(cfg, "notas/x.md", idade=3600)
    root = bib(cfg) / "notas"
    assert arquivador.protegido(cfg, root, "notas", ["x.md"], NOW) == "protegido: alterado há menos de 48 h"
    assert arquivador.protegido(cfg, root, "notas", ["x.md"], NOW + 2 * DIA) is None
    aprovacao(cfg, "p.md", "aprovado", "notas/x.md", idade=DIA)
    assert arquivador.protegido(cfg, root, "notas", ["x.md"], NOW + 2 * DIA) is None
    assert arquivador.protegido(cfg, root, "notas", ["x.md"], NOW + 2 * DIA, auto=True) == (
        "protegido: citado por um pedido de aprovação alterado há menos de 14 dias")


def test_resolver_item(cfg):
    w(cfg, "notas/rel/x.md")
    w(cfg, "notas/rel/x.copy.md")
    w(cfg, "material/media/l/a.jpg")
    assert arquivador.resolver_item(bib(cfg) / "notas", "rel/x.md", False) == (
        "documento", ["rel/x.copy.md", "rel/x.md"])
    assert arquivador.resolver_item(bib(cfg) / "material", "media/l", True) == ("lote", ["media/l"])
    assert arquivador.resolver_item(bib(cfg) / "material", "media/l", False) == "não arquivável"
    assert arquivador.resolver_item(bib(cfg) / "notas", "segredo.md", False) == "não arquivável"


# ---------------------------------------------------------------- fila: pasta, resposta e rotação

def test_fila_criada_se_faltar_e_insegura_avisada(cfg, tmp_path):
    fila = officecfg.caminho(cfg, "arquivo_fila")
    fila.rmdir()
    assert passar(cfg) == [] and fila.is_dir()
    fila.rmdir()
    (tmp_path / "outra").mkdir()
    fila.symlink_to(tmp_path / "outra")
    assert passar(cfg) == ["biblioteca: fila do arquivo insegura ou ilegível (arquivo-fila/)"]


def test_resposta_nao_gravada_nao_para_a_fila(cfg):
    w(cfg, "notas/x.md")
    w(cfg, "notas/y.md")
    pedir(cfg, "arquivar", "notas", "x.md")
    pedir(cfg, "arquivar", "notas", "y.md", stem="20261117T183500-0a1b2c3e")
    (officecfg.caminho(cfg, "arquivo_fila") / f"{STEM}.feito.json").mkdir()   # ocupado por uma pasta
    linhas = passar(cfg)
    assert feito(cfg, "20261117T183500-0a1b2c3e") == {"ok": True}
    assert linhas == ["biblioteca: arquivado notas/y.md (1 ficheiros)"]


def test_rotacao(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, _ = config(tmp_path, rotacao=7)
    fila = officecfg.caminho(cfg, "arquivo_fila")
    velho, novo, pendente = "20261101T000000-0000000a", "20261116T000000-0000000b", "20261101T000000-0000000c"
    for stem in (velho, novo, pendente):
        (fila / f"{stem}.json").write_text("{}", encoding="utf-8")
    for stem, idade in ((velho, 8 * DIA), (novo, 6 * DIA)):
        (fila / f"{stem}.feito.json").write_text('{"ok": true}', encoding="utf-8")
        os.utime(fila / f"{stem}.feito.json", (NOW - idade, NOW - idade))
    assert arquivador.rodar(cfg, NOW) == [f"biblioteca: pedido {velho} rodado para feito/"]
    assert sorted(p.name for p in (fila / "feito").iterdir()) == [f"{velho}.feito.json", f"{velho}.json"]
    assert sorted(p.name for p in fila.iterdir() if p.is_file()) == [
        f"{pendente}.json", f"{novo}.feito.json", f"{novo}.json"]


def test_rotacao_com_feito_symlink(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, _ = config(tmp_path, rotacao=1)
    fila = officecfg.caminho(cfg, "arquivo_fila")
    (tmp_path / "fora").mkdir()
    (fila / "feito").symlink_to(tmp_path / "fora")
    (fila / f"{STEM}.json").write_text("{}", encoding="utf-8")
    (fila / f"{STEM}.feito.json").write_text("{}", encoding="utf-8")
    os.utime(fila / f"{STEM}.feito.json", (NOW - 9 * DIA, NOW - 9 * DIA))
    assert arquivador.rodar(cfg, NOW) == [f"biblioteca: pedido {STEM} não rodado: erro a mover para feito/"]
    assert list((tmp_path / "fora").iterdir()) == []


# ---------------------------------------------------------------- na passagem do runner

def test_uma_vez_trata_a_fila_do_arquivo(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = config(tmp_path)
    w(cfg, "notas/x.md")
    pedir(cfg, "arquivar", "notas", "x.md")
    linhas = office_runner.uma_vez(cfg, str(path), NOW)
    assert "biblioteca: arquivado notas/x.md (1 ficheiros)" in linhas
    assert feito(cfg) == {"ok": True}


def test_uma_vez_sem_nada_nao_imprime(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = config(tmp_path)
    w(cfg, "notas/x.md", idade=400 * DIA)
    assert office_runner.uma_vez(cfg, str(path), NOW) == []
    assert (bib(cfg) / "notas/x.md").exists()     # sem arquivo_dias nada sai sem pedido
