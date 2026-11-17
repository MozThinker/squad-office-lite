"""Runner: findings da revisão do arquivo da Biblioteca.

1. alterações locais por commitar nunca vão no commit do arquivo (recusa, nada se move);
2. falha do add/commit desfaz os renames (tudo ou nada) e tira do índice o que ficou em stage;
3. no máximo PENDENTES_MAX pedidos por passagem, um só commit e um só push por fonte; 2.º pedido do mesmo item na
   mesma passagem ⇒ «pedido ilegível»;
4. «reposto não volta a sair sozinho» também pelos ficheiros do item (não só pela chave).
"""
import os

import pytest

from tests import test_runner_arquivo as a
from tests import test_runner_arquivo_auto as au
from tests import test_runner_arquivo_git as g
from tests import test_runner_comum as c

import arquivador
import arquivo

NOW, DIA, STEM, OUTRO = a.NOW, a.DIA, a.STEM, g.OUTRO


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    return a.config(tmp_path)[0]


def stem(i: int) -> str:
    return f"20261117T{183400 + i // 60 * 100 + i % 60:06d}-0a1b2c3d"


def estado(repo) -> str:
    return g.git(repo, "status", "--porcelain", "--untracked-files=no")


def test_erros_novos_na_lista():
    assert "alterações por commitar" in arquivo.ERROS and "erro git" in arquivo.ERROS


# ---------------------------------------------------------------- 1. alterações por commitar

@pytest.mark.parametrize("como", ["modificado", "em stage", "apagado"])
def test_alteracoes_locais_recusam_arquivar(cfg, como):
    r = g.repo_fonte(cfg)
    x = r / "rel" / "x.copy.md"
    if como == "apagado":
        g.git(r, "rm", "-q", "--cached", "rel/x.copy.md")   # remoção em stage; o ficheiro fica no disco
        assert x.exists()
    else:
        a.w(cfg, "notas/rel/x.copy.md", "# segredo local\n")
        if como == "em stage":
            g.git(r, "add", "rel/x.copy.md")
    linhas = g.arquivar(cfg)
    assert a.feito(cfg) == {"erro": "alterações por commitar"}
    assert linhas == [f"biblioteca: pedido {STEM} recusado: alterações por commitar"]
    assert (r / "rel/x.md").exists() and (r / "rel/x.copy.md").exists()
    assert not (r / ".arquivo" / "rel").exists() and g.log(r) == ["init"]


def test_lote_com_seguido_apagado_recusa(cfg):
    m = a.bib(cfg) / "material"
    g.git(m, "init", "-q", "-b", "main")
    for f in ("media/l/a.jpg", "media/l/b.jpg"):
        a.w(cfg, f"material/{f}")
    g.git(m, "add", "-A")
    g.git(m, "commit", "-q", "-m", "init")
    (m / "media/l/b.jpg").unlink()
    a.pedir(cfg, "arquivar", "material", "media/l")
    arquivador.processar(cfg, NOW)
    assert a.feito(cfg) == {"erro": "alterações por commitar"}
    assert (m / "media/l/a.jpg").exists() and not (m / ".arquivo/media").exists()


def test_alteracao_noutro_ficheiro_nao_impede(cfg):
    r = g.repo_fonte(cfg)
    a.w(cfg, "notas/rel/y.md", "# outro trabalho\n")
    g.arquivar(cfg)
    assert a.feito(cfg) == {"ok": True}
    assert g.no_commit(r) == [".arquivo/rel/x.copy.md", ".arquivo/rel/x.md", "rel/x.copy.md", "rel/x.md"]
    assert estado(r) == "M rel/y.md"   # o helper faz strip


def test_repor_com_alteracao_no_arquivo_recusa(cfg):
    r = g.repo_fonte(cfg)
    g.arquivar(cfg)
    (r / ".arquivo/rel/x.md").write_text("# mexido no arquivo\n", encoding="utf-8")
    g.arquivar(cfg, stem=OUTRO, accao="repor")
    assert a.feito(cfg, OUTRO) == {"erro": "alterações por commitar"}
    assert (r / ".arquivo/rel/x.md").exists() and not (r / "rel/x.md").exists()


def test_automatico_salta_item_com_alteracoes(ctx_auto):
    cfg = ctx_auto
    r = g.repo_fonte(cfg, ("a.md", "b.md"))
    a.w(cfg, "notas/a.md", "# local\n", idade=100 * DIA)
    a.w(cfg, "notas/b.md", idade=100 * DIA)
    linhas = arquivador.automatico(cfg, NOW)
    assert linhas[:2] == ["biblioteca: notas/a.md não arquivado: alterações por commitar",
                          "biblioteca: arquivado notas/b.md (1 ficheiros, idade > 30 dias)"]
    assert (r / "a.md").read_text(encoding="utf-8") == "# local\n"
    assert g.no_commit(r) == [".arquivo/b.md", "b.md"]


@pytest.fixture
def ctx_auto(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    return au.com_dias(tmp_path)[0]


# ---------------------------------------------------------------- 2. falha do git desfaz

def test_index_lock_desfaz_tudo(cfg):
    r = g.repo_fonte(cfg)
    (r / ".git" / "index.lock").write_text("", encoding="utf-8")
    linhas = g.arquivar(cfg)
    (r / ".git" / "index.lock").unlink()
    assert a.feito(cfg) == {"erro": "erro git"}
    assert (r / "rel/x.md").exists() and (r / "rel/x.copy.md").exists()
    assert not (r / ".arquivo/rel/x.md").exists() and not (r / ".arquivo/rel/x.copy.md").exists()
    assert not (r / ".arquivo/REGISTO.tsv").exists()
    assert g.log(r) == ["init"] and estado(r) == ""
    assert f"biblioteca: pedido {STEM} recusado: erro git" in linhas
    assert not any(x.startswith("biblioteca: arquivado") for x in linhas)


def test_commit_falhado_tira_do_indice_e_desfaz(cfg, monkeypatch):
    r = g.repo_fonte(cfg)
    real = arquivador._git

    def git(topo, *args, **kw):
        if "commit" in args:
            return arquivador.subprocess.CompletedProcess(args, 1, b"", b"falhou")
        return real(topo, *args, **kw)
    monkeypatch.setattr(arquivador, "_git", git)
    g.arquivar(cfg)
    assert a.feito(cfg) == {"erro": "erro git"}
    assert (r / "rel/x.md").exists() and not (r / ".arquivo/rel/x.md").exists()
    assert g.git(r, "diff", "--cached", "--name-only") == "" and estado(r) == ""


def test_repor_com_commit_falhado_fica_no_arquivo(cfg):
    r = g.repo_fonte(cfg)
    g.arquivar(cfg)
    (r / ".git" / "index.lock").write_text("", encoding="utf-8")
    g.arquivar(cfg, stem=OUTRO, accao="repor")
    (r / ".git" / "index.lock").unlink()
    assert a.feito(cfg, OUTRO) == {"erro": "erro git"}
    assert (r / ".arquivo/rel/x.md").exists() and not (r / "rel/x.md").exists()
    assert estado(r) == ""


def test_automatico_com_commit_falhado_desfaz(ctx_auto):
    cfg = ctx_auto
    r = g.repo_fonte(cfg, ("a.md", "b.md"))
    for f in ("a.md", "b.md"):
        a.w(cfg, f"notas/{f}", idade=100 * DIA)
    (r / ".git" / "index.lock").write_text("", encoding="utf-8")
    linhas = arquivador.automatico(cfg, NOW)
    (r / ".git" / "index.lock").unlink()
    assert linhas == ["biblioteca: notas/a.md não arquivado: erro git",
                      "biblioteca: notas/b.md não arquivado: erro git",
                      "AVISO: commit do arquivo em notas falhou (add); os ficheiros voltam ao sítio"]
    assert (r / "a.md").exists() and (r / "b.md").exists() and estado(r) == ""


# ---------------------------------------------------------------- 3. limites por passagem

def test_no_maximo_pendentes_max_por_passagem_um_commit_e_um_push(cfg, tmp_path, monkeypatch):
    n = arquivo.PENDENTES_MAX + 5
    r = g.repo_fonte(cfg, [f"d{i:03d}.md" for i in range(n)])
    remoto = g.com_remoto(tmp_path, r)
    for i in range(n):
        a.pedir(cfg, "arquivar", "notas", f"d{i:03d}.md", stem=stem(i))
    pushes, real = [], arquivador._git

    def git(topo, *args, **kw):
        if "push" in args:
            pushes.append(args)
        return real(topo, *args, **kw)
    monkeypatch.setattr(arquivador, "_git", git)
    linhas = arquivador.processar(cfg, NOW)
    feitos = [a.feito(cfg, stem(i)) for i in range(n)]
    assert feitos == [{"ok": True}] * arquivo.PENDENTES_MAX + [None] * 5     # os outros ficam, por ordem
    assert g.log(r) == [f"chore(arquivo): {arquivo.PENDENTES_MAX} ficheiros", "init"]
    assert len(pushes) == 1 and linhas[-1] == "biblioteca: push de notas para origin/main"
    assert g.git(remoto, "log", "-1", "--format=%s", "main") == f"chore(arquivo): {arquivo.PENDENTES_MAX} ficheiros"
    arquivador.processar(cfg, NOW)
    assert [a.feito(cfg, stem(i)) for i in range(n)] == [{"ok": True}] * n
    assert g.log(r)[0] == "chore(arquivo): 5 ficheiros"


def test_segundo_pedido_do_mesmo_item_na_passagem(cfg):
    r = g.repo_fonte(cfg)
    a.pedir(cfg, "arquivar", "notas", "rel/x.md")
    a.pedir(cfg, "repor", "notas", "rel/x.md", stem=OUTRO)
    linhas = arquivador.processar(cfg, NOW)
    assert a.feito(cfg) == {"ok": True} and a.feito(cfg, OUTRO) == {"erro": "pedido ilegível"}
    assert (r / ".arquivo/rel/x.md").exists() and g.log(r)[0] == "chore(arquivo): 2 ficheiros"
    assert f"biblioteca: pedido {OUTRO} recusado: pedido ilegível" in linhas


def test_item_ja_tocado_por_outra_chave_na_passagem(cfg):
    # x.copy.md é do mesmo grupo de x.md: o 2.º pedido mexeria nos mesmos ficheiros
    r = g.repo_fonte(cfg)
    a.pedir(cfg, "arquivar", "notas", "rel/x.md")
    a.pedir(cfg, "repor", "notas", "rel/x.copy.md", stem=OUTRO)
    arquivador.processar(cfg, NOW)
    assert a.feito(cfg) == {"ok": True} and a.feito(cfg, OUTRO) == {"erro": "pedido ilegível"}
    assert (r / ".arquivo/rel/x.copy.md").exists() and estado(r) == ""


def test_arquivar_e_repor_de_itens_diferentes_num_commit(cfg):
    r = g.repo_fonte(cfg)
    g.arquivar(cfg, "rel/y.md")
    a.pedir(cfg, "arquivar", "notas", "rel/x.md", stem=stem(100))
    a.pedir(cfg, "repor", "notas", "rel/y.md", stem=stem(101))
    arquivador.processar(cfg, NOW)
    assert a.feito(cfg, stem(100)) == a.feito(cfg, stem(101)) == {"ok": True}
    assert g.log(r)[:2] == ["chore(arquivo): arquivar e repor 3 ficheiros", "chore(arquivo): 1 ficheiros"]
    assert g.git(r, "ls-files").split() == [".arquivo/rel/x.copy.md", ".arquivo/rel/x.md", "rel/y.md"]


def test_sem_git_cada_pedido_responde_e_regista(cfg):
    a.w(cfg, "notas/x.md")
    a.w(cfg, "notas/y.md")
    a.pedir(cfg, "arquivar", "notas", "x.md")
    a.pedir(cfg, "arquivar", "notas", "y.md", stem=OUTRO)
    assert arquivador.processar(cfg, NOW) == ["biblioteca: arquivado notas/x.md (1 ficheiros)",
                                              "biblioteca: arquivado notas/y.md (1 ficheiros)"]
    assert [x[2] for x in a.registo(cfg, "notas")] == ["x.md", "y.md"]


# ---------------------------------------------------------------- 4. reposto pelos ficheiros

def test_reposto_por_outra_chave_do_grupo_nao_volta_a_sair(ctx_auto):
    cfg = ctx_auto
    a.w(cfg, "notas/x.md", idade=100 * DIA)
    a.w(cfg, "notas/x.copy.md", idade=100 * DIA)
    a.pedir(cfg, "arquivar", "notas", "x.copy.md")       # o REGISTO fica com item = x.copy.md
    arquivador.processar(cfg, NOW)
    a.pedir(cfg, "repor", "notas", "x.copy.md", stem=OUTRO)
    arquivador.processar(cfg, NOW)
    assert arquivador.automatico(cfg, NOW + 400 * DIA) == []
    assert (a.bib(cfg) / "notas/x.md").exists() and (a.bib(cfg) / "notas/x.copy.md").exists()


def test_lote_reposto_nao_volta_a_sair(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg = au.com_dias(tmp_path, notas=None, material=7)[0]
    a.w(cfg, "material/media/l/a.jpg", idade=100 * DIA)
    a.pedir(cfg, "arquivar", "material", "media/l")
    arquivador.processar(cfg, NOW)
    a.pedir(cfg, "repor", "material", "media/l", stem=OUTRO)
    arquivador.processar(cfg, NOW)
    # o REGISTO tem o item com outro nome (escrito à mão / versão anterior): os ficheiros chegam
    reg = a.bib(cfg) / "material/.arquivo/REGISTO.tsv"
    reg.write_text(reg.read_text(encoding="utf-8").replace("\tmedia/l\n", "\toutro\n"), encoding="utf-8")
    assert arquivador.automatico(cfg, NOW + 400 * DIA) == []
    assert (a.bib(cfg) / "material/media/l/a.jpg").exists()


# ---------------------------------------------------------------- ronda 2: ls-files falhado e submódulos

@pytest.mark.parametrize("rc", [128, 124])   # erro do git e timeout (o _git devolve 124)
def test_ls_files_falhado_desfaz(cfg, monkeypatch, rc):
    r = g.repo_fonte(cfg)
    real = arquivador._git

    def git(topo, *args, **kw):
        if "ls-files" in args and "-s" not in args:   # o do commit (o -s é o dos submódulos, no início da passagem)
            return arquivador.subprocess.CompletedProcess(args, rc, b"", b"falhou")
        return real(topo, *args, **kw)
    monkeypatch.setattr(arquivador, "_git", git)
    linhas = g.arquivar(cfg)
    assert a.feito(cfg) == {"erro": "erro git"}
    assert "AVISO: commit do arquivo em notas falhou (ls-files); os ficheiros voltam ao sítio" in linhas
    assert (r / "rel/x.md").exists() and (r / "rel/x.copy.md").exists()
    assert not (r / ".arquivo/rel/x.md").exists() and not (r / ".arquivo/REGISTO.tsv").exists()
    assert g.log(r) == ["init"] and estado(r) == ""


def test_lote_com_submodulo_nao_arquivavel(cfg):
    # um ponteiro de submódulo por commitar não aparece no status (--ignore-submodules=all): o commit do arquivo
    # levá-lo-ia e publicá-lo-ia
    m = a.bib(cfg) / "material"
    g.git(m, "init", "-q", "-b", "main")
    a.w(cfg, "material/media/l/a.jpg")
    a.w(cfg, "material/media/l/sub/b.jpg")
    sub = m / "media/l/sub"
    g.git(sub, "init", "-q", "-b", "main")
    g.git(sub, "add", "-A")
    g.git(sub, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "s1")
    g.git(m, "add", "-A")   # sub entra como gitlink (modo 160000)
    g.git(m, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
    assert g.git(m, "ls-files", "-s", "media/l/sub").startswith("160000 ")
    a.w(cfg, "material/media/l/sub/c.jpg")
    g.git(sub, "add", "-A")
    g.git(sub, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "s2")
    for f in sub.rglob("*"):   # o .git/ do submódulo é de agora: envelhece-se, para não cair na protecção das 48 h
        os.utime(f, (NOW - 10 * DIA, NOW - 10 * DIA), follow_symlinks=False)
    a.pedir(cfg, "arquivar", "material", "media/l")
    arquivador.processar(cfg, NOW)
    assert a.feito(cfg) == {"erro": "não arquivável"}
    assert (m / "media/l/a.jpg").exists() and not (m / ".arquivo/media").exists()
    assert g.log(m) == ["init"]


def test_submodulo_noutra_pasta_nao_impede(cfg):
    r = g.repo_fonte(cfg)
    g.git(r, "update-index", "--add", "--cacheinfo", f"160000,{g.git(r, 'rev-parse', 'HEAD')},outra/sub")
    g.git(r, "commit", "-q", "-m", "sub")
    g.arquivar(cfg)
    assert a.feito(cfg) == {"ok": True}
    assert g.log(r)[0] == "chore(arquivo): 2 ficheiros"
