"""Runner: arquivo automático por idade, opt-in por fonte (`biblioteca[].arquivo_dias`, AC20, ADR-012)."""
import pytest

from tests import test_runner_arquivo as a
from tests import test_runner_arquivo_git as g
from tests import test_runner_comum as c
from tests.test_runner_comum import office_runner, officecfg

import arquivador

NOW, DIA = a.NOW, a.DIA


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    return tmp_path


def com_dias(tmp, notas=30, material=None):
    return a.config(tmp, extra_material=f"    arquivo_dias: {material}\n" if material else "",
                    extra_notas=f"    arquivo_dias: {notas}\n" if notas else "")


def marca(cfg):
    return officecfg.caminho(cfg, "runner") / "arquivo-auto.dia"


def test_sem_a_chave_nunca_arquiva(ctx):
    cfg, path = com_dias(ctx, notas=None)
    a.w(cfg, "notas/velho.md", idade=3000 * DIA)
    a.w(cfg, "material/media/l/a.jpg", idade=3000 * DIA)
    assert arquivador.automatico(cfg, NOW) == []
    assert office_runner.uma_vez(cfg, str(path), NOW) == []
    assert (a.bib(cfg) / "notas/velho.md").exists() and (a.bib(cfg) / "material/media/l/a.jpg").exists()
    assert not marca(cfg).exists()


def test_arquiva_por_idade_com_motivo(ctx):
    cfg, _ = com_dias(ctx, notas=30)
    a.w(cfg, "notas/rel/velho.md", idade=31 * DIA)
    a.w(cfg, "notas/rel/velho.copy.md", idade=40 * DIA)
    a.w(cfg, "notas/rel/misto.md", idade=60 * DIA)
    a.w(cfg, "notas/rel/misto.copy.md", idade=29 * DIA)     # o mais recente do grupo decide
    a.w(cfg, "notas/novo.md", idade=29 * DIA)
    a.w(cfg, "material/velho.md", idade=400 * DIA)          # fonte sem a chave
    linhas = arquivador.automatico(cfg, NOW)
    assert linhas == ["biblioteca: arquivado notas/rel/velho.md (2 ficheiros, idade > 30 dias)"]
    n = a.bib(cfg) / "notas"
    assert (n / ".arquivo/rel/velho.md").exists() and (n / ".arquivo/rel/velho.copy.md").exists()
    assert (n / "rel/misto.md").exists() and (n / "novo.md").exists()
    assert (a.bib(cfg) / "material/velho.md").exists()
    assert {(x[1], x[3], x[4]) for x in a.registo(cfg, "notas")} == {("arquivar", "idade (30 dias)", "rel/velho.md")}
    assert marca(cfg).read_text(encoding="ascii") == "2026-11-17\n"


def test_lote_por_idade(ctx):
    cfg, _ = com_dias(ctx, notas=None, material=7)
    a.w(cfg, "material/media/l-velho/a.jpg", idade=8 * DIA)
    a.w(cfg, "material/media/l-velho/b.jpg", idade=9 * DIA)
    a.w(cfg, "material/media/l-novo/a.jpg", idade=8 * DIA)
    a.w(cfg, "material/media/l-novo/b.jpg", idade=6 * DIA)
    a.w(cfg, "material/fotos/c.jpg", idade=100 * DIA)       # «pasta» de media: não arquivável
    assert arquivador.automatico(cfg, NOW) == [
        "biblioteca: arquivado material/media/l-velho (2 ficheiros, idade > 7 dias)"]
    m = a.bib(cfg) / "material"
    assert (m / ".arquivo/media/l-velho/a.jpg").exists() and (m / "media/l-novo").exists()
    assert (m / "fotos/c.jpg").exists()


def test_uma_vez_por_dia(ctx):
    cfg, _ = com_dias(ctx)
    a.w(cfg, "notas/a.md", idade=40 * DIA)
    assert len(arquivador.automatico(cfg, NOW)) == 1
    a.w(cfg, "notas/b.md", idade=40 * DIA)
    assert arquivador.automatico(cfg, NOW + 3600) == []            # mesmo dia (fuso da config)
    assert (a.bib(cfg) / "notas/b.md").exists()
    assert arquivador.automatico(cfg, NOW + DIA) == [
        "biblioteca: arquivado notas/b.md (1 ficheiros, idade > 30 dias)"]


def test_dia_no_fuso_da_config(ctx):
    cfg, _ = com_dias(ctx)
    cfg["fuso"] = "Asia/Tokyo"       # NOW = 18:34 UTC = 03:34 do dia 18 em Tóquio
    arquivador.automatico(cfg, NOW)
    assert marca(cfg).read_text(encoding="ascii") == "2026-11-18\n"


def test_citado_ha_menos_de_14_dias_fica(ctx):
    cfg, _ = com_dias(ctx)
    a.w(cfg, "notas/recente.md", idade=100 * DIA)
    a.w(cfg, "notas/antigo.md", idade=100 * DIA)
    a.aprovacao(cfg, "r.md", "aprovado", "notas/recente.md", idade=13 * DIA)
    a.aprovacao(cfg, "v.md", "executado", "notas/antigo.md", idade=15 * DIA)
    linhas = arquivador.automatico(cfg, NOW)
    assert linhas == [
        "biblioteca: arquivado notas/antigo.md (1 ficheiros, idade > 30 dias)",
        "biblioteca: notas/recente.md não arquivado: "
        "protegido: citado por um pedido de aprovação alterado há menos de 14 dias"]
    assert (a.bib(cfg) / "notas/recente.md").exists()


def test_pendente_protege_tambem_no_automatico(ctx):
    cfg, _ = com_dias(ctx)
    a.w(cfg, "notas/x.md", idade=100 * DIA)
    a.aprovacao(cfg, "p.md", "pendente", "notas/x.md", idade=300 * DIA)
    assert arquivador.automatico(cfg, NOW) == [
        "biblioteca: notas/x.md não arquivado: protegido: citado por um pedido de aprovação pendente"]


def test_reposto_nao_volta_a_sair(ctx):
    cfg, _ = com_dias(ctx)
    a.w(cfg, "notas/x.md", idade=100 * DIA)
    a.pedir(cfg, "arquivar", "notas", "x.md")
    arquivador.processar(cfg, NOW)
    a.pedir(cfg, "repor", "notas", "x.md", stem="20261117T183500-0a1b2c3e")
    arquivador.processar(cfg, NOW)
    assert (a.bib(cfg) / "notas/x.md").exists()
    assert arquivador.automatico(cfg, NOW + 400 * DIA) == []
    assert (a.bib(cfg) / "notas/x.md").exists()


def test_na_passagem_do_runner(ctx):
    cfg, path = com_dias(ctx)
    a.w(cfg, "notas/x.md", idade=100 * DIA)
    assert "biblioteca: arquivado notas/x.md (1 ficheiros, idade > 30 dias)" in office_runner.uma_vez(
        cfg, str(path), NOW)


def test_git_um_commit_por_fonte(ctx):
    cfg, _ = com_dias(ctx)
    r = g.repo_fonte(cfg, ("a.md", "b.md", "rel/c.md"))
    for f in ("a.md", "b.md", "rel/c.md"):
        a.w(cfg, f"notas/{f}", idade=100 * DIA)
    remoto = g.com_remoto(ctx, r)
    linhas = arquivador.automatico(cfg, NOW)
    assert g.log(r)[:2] == ["chore(arquivo): 3 ficheiros", "init"]
    assert linhas[-1] == "biblioteca: push de notas para origin/main"
    assert g.git(remoto, "log", "-1", "--format=%s", "main") == "chore(arquivo): 3 ficheiros"
