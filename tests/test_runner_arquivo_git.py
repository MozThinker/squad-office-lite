"""Runner: arquivo numa fonte guardada em git (AC21) e push seguro (AC22), com um remoto `git init --bare` local.

HOME e git isolados (c.ambiente); nada de rede: o remoto é uma pasta.
"""
import os
import subprocess
from pathlib import Path

import pytest

from tests import test_runner_arquivo as a
from tests import test_runner_comum as c

import arquivador

NOW, STEM = a.NOW, a.STEM
OUTRO = "20261117T183500-0a1b2c3e"


def git(cwd, *args) -> str:
    return c.git(list(args), cwd)


def log(repo) -> list[str]:
    return git(repo, "log", "--format=%s").splitlines()


def no_commit(repo, ref="HEAD") -> list[str]:
    return sorted(git(repo, "show", "--no-renames", "--name-only", "--format=", ref).splitlines())


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    return a.config(tmp_path)[0]


def repo_fonte(cfg, ficheiros=("rel/x.md", "rel/x.copy.md", "rel/y.md")) -> Path:
    """<dados>/biblioteca/notas como repo git (topo = fonte), ramo main, ficheiros seguidos num commit."""
    r = a.bib(cfg) / "notas"
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "Teste")
    git(r, "config", "user.email", "teste@example.com")
    for f in ficheiros:
        a.w(cfg, f"notas/{f}")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def com_remoto(tmp_path, repo: Path, clone_head: bool = True) -> Path:
    remoto = tmp_path / "remoto.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remoto))
    git(repo, "remote", "add", "origin", str(remoto))
    git(repo, "push", "-q", "origin", "main")
    if clone_head:   # como num clone: refs/remotes/origin/HEAD existe
        git(repo, "remote", "set-head", "origin", "main")
    return remoto


def arquivar(cfg, chave="rel/x.md", stem=STEM, accao="arquivar"):
    a.pedir(cfg, accao, "notas", chave, stem=stem)
    return arquivador.processar(cfg, NOW)


# ---------------------------------------------------------------- commit (AC21)

def test_commit_so_com_os_caminhos_movidos(cfg):
    r = repo_fonte(cfg, ("rel/x.md", "rel/x.copy.md", "rel/y.md", "outro.md"))
    (r / "outro.md").write_text("# mudado\n", encoding="utf-8")
    git(r, "add", "outro.md")                       # em stage antes: fica fora do commit do arquivo
    linhas = arquivar(cfg)
    assert a.feito(cfg) == {"ok": True}
    assert log(r)[0] == "chore(arquivo): 2 ficheiros"
    assert no_commit(r) == [".arquivo/rel/x.copy.md", ".arquivo/rel/x.md", "rel/x.copy.md", "rel/x.md"]
    assert git(r, "diff", "--cached", "--name-only") == "outro.md"
    assert (r / ".arquivo" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert "biblioteca: commit em notas: chore(arquivo): 2 ficheiros" in linhas
    assert linhas[-1] == "AVISO: sem push em notas: sem origin"
    assert git(r, "status", "--porcelain", "--", "rel", ".arquivo") == ""


def test_nome_com_glob_e_literal(cfg):
    r = repo_fonte(cfg, ("x[12].md", "x1.md", "x2.md"))
    arquivar(cfg, "x[12].md")
    assert a.feito(cfg) == {"ok": True}
    assert no_commit(r) == [".arquivo/x[12].md", "x[12].md"]
    assert (r / "x1.md").exists() and git(r, "ls-files", "x1.md", "x2.md").split() == ["x1.md", "x2.md"]


def test_hook_do_repo_nao_corre(cfg, tmp_path):
    r = repo_fonte(cfg)
    marca = tmp_path / "hook-correu"
    hook = r / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marca}\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    arquivar(cfg)
    assert log(r)[0] == "chore(arquivo): 2 ficheiros"
    assert not marca.exists()


def test_sem_identidade_usa_a_marca(cfg):
    r = repo_fonte(cfg)
    git(r, "config", "--unset", "user.name")
    git(r, "config", "--unset", "user.email")
    # o runner só passa a lista branca do ambiente: GIT_AUTHOR_* dos testes não chegam ao git
    arquivar(cfg)
    assert git(r, "log", "-1", "--format=%an <%ae>") == "Office Teste <office-lite@localhost>"


def test_nao_seguidos_movem_sem_commit(cfg):
    r = repo_fonte(cfg, ("rel/y.md",))
    a.w(cfg, "notas/rel/x.md")                      # não seguido
    linhas = arquivar(cfg)
    assert a.feito(cfg) == {"ok": True}
    assert log(r) == ["init"] and (r / ".arquivo/rel/x.md").exists()
    assert not any("commit" in x or "push" in x for x in linhas)


def test_repor_faz_commit_proprio(cfg):
    r = repo_fonte(cfg)
    arquivar(cfg)
    arquivar(cfg, stem=OUTRO, accao="repor")
    assert a.feito(cfg, OUTRO) == {"ok": True}
    assert log(r)[:2] == ["chore(arquivo): repor 2 ficheiros", "chore(arquivo): 2 ficheiros"]
    assert git(r, "ls-files", "rel").split() == ["rel/x.copy.md", "rel/x.md", "rel/y.md"]
    assert git(r, "ls-files", ".arquivo") == ""


def test_topo_fora_de_biblioteca_so_rename(cfg, tmp_path):
    # <dados> inteiro em git: o topo está acima de <dados>/biblioteca ⇒ nunca commits nesse repo
    dados = a.bib(cfg).parent
    git(dados, "init", "-q", "-b", "main")
    a.w(cfg, "notas/rel/x.md")
    git(dados, "add", "-A")
    git(dados, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    linhas = arquivar(cfg)
    assert a.feito(cfg) == {"ok": True}
    assert log(dados) == ["init"] and not (a.bib(cfg) / "notas/.arquivo/.gitignore").exists()
    assert linhas == ["biblioteca: arquivado notas/rel/x.md (1 ficheiros)"]


def test_fonte_symlink_para_repo_fora_so_rename(cfg, tmp_path):
    fora = tmp_path / "fora-repo"
    fora.mkdir()
    git(fora, "init", "-q", "-b", "main")
    (fora / "x.md").write_text("# x\n", encoding="utf-8")
    os.utime(fora / "x.md", (NOW - 10 * a.DIA, NOW - 10 * a.DIA))
    git(fora, "add", "-A")
    git(fora, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    (a.bib(cfg) / "notas").rmdir()
    (a.bib(cfg) / "notas").symlink_to(fora)
    arquivar(cfg, "x.md")
    assert a.feito(cfg) == {"ok": True}
    assert log(fora) == ["init"] and (fora / ".arquivo/x.md").exists()


def test_topo_em_biblioteca_com_varias_fontes(cfg):
    b = a.bib(cfg)
    git(b, "init", "-q", "-b", "main")
    git(b, "config", "user.name", "T")
    git(b, "config", "user.email", "t@t")
    a.w(cfg, "notas/rel/x.md")
    a.w(cfg, "material/m.md")
    git(b, "add", "-A")
    git(b, "commit", "-q", "-m", "init")
    arquivar(cfg)
    assert no_commit(b) == ["notas/.arquivo/rel/x.md", "notas/rel/x.md"]
    assert (b / "material/m.md").exists()


# ---------------------------------------------------------------- push seguro (AC22)

def test_em_dia_faz_push(cfg, tmp_path):
    r = repo_fonte(cfg)
    remoto = com_remoto(tmp_path, r)
    linhas = arquivar(cfg)
    assert linhas[-1] == "biblioteca: push de notas para origin/main"
    assert git(remoto, "log", "-1", "--format=%s", "main") == "chore(arquivo): 2 ficheiros"


def test_ramo_pelo_ls_remote_sem_origin_head(cfg, tmp_path):
    r = repo_fonte(cfg)
    remoto = com_remoto(tmp_path, r, clone_head=False)
    assert subprocess.run(["git", "-C", str(r), "symbolic-ref", "-q", "refs/remotes/origin/HEAD"]).returncode != 0
    assert arquivar(cfg)[-1] == "biblioteca: push de notas para origin/main"
    assert git(remoto, "log", "-1", "--format=%s", "main") == "chore(arquivo): 2 ficheiros"


def test_origin_a_frente_nao_faz_push(cfg, tmp_path):
    r = repo_fonte(cfg)
    remoto = com_remoto(tmp_path, r)
    outro = tmp_path / "outro-clone"
    git(tmp_path, "clone", "-q", str(remoto), str(outro))
    (outro / "novo.md").write_text("x\n", encoding="utf-8")
    git(outro, "add", "novo.md")
    git(outro, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", "alheio no remoto")
    git(outro, "push", "-q", "origin", "main")
    linhas = arquivar(cfg)
    assert linhas[-1] == "AVISO: sem push em notas: 1 atrás e 1 à frente do origin (contando com o commit do arquivo)"
    assert git(remoto, "log", "-1", "--format=%s", "main") == "alheio no remoto"


def test_commit_alheio_por_publicar_nao_faz_push(cfg, tmp_path):
    r = repo_fonte(cfg)
    remoto = com_remoto(tmp_path, r)
    (r / "rel" / "y.md").write_text("# local\n", encoding="utf-8")
    git(r, "commit", "-q", "-am", "trabalho local")
    linhas = arquivar(cfg)
    assert linhas[-1] == "AVISO: sem push em notas: 0 atrás e 2 à frente do origin (contando com o commit do arquivo)"
    assert git(remoto, "log", "-1", "--format=%s", "main") == "init"


def test_outro_ramo_nao_faz_push(cfg, tmp_path):
    r = repo_fonte(cfg)
    remoto = com_remoto(tmp_path, r)
    git(r, "checkout", "-q", "-b", "trabalho")
    linhas = arquivar(cfg)
    assert linhas[-1] == "AVISO: sem push em notas: outro ramo («trabalho», não «main»)"
    assert git(remoto, "for-each-ref", "--format=%(refname)") == "refs/heads/main"


def test_repor_faz_push(cfg, tmp_path):
    r = repo_fonte(cfg)
    remoto = com_remoto(tmp_path, r)
    arquivar(cfg)
    linhas = arquivar(cfg, stem=OUTRO, accao="repor")
    assert linhas[-1] == "biblioteca: push de notas para origin/main"
    assert git(remoto, "log", "-1", "--format=%s", "main") == "chore(arquivo): repor 2 ficheiros"


def test_head_solto(cfg, tmp_path):
    r = repo_fonte(cfg)
    com_remoto(tmp_path, r)
    git(r, "checkout", "-q", "--detach")
    assert arquivar(cfg)[-1] == "AVISO: sem push em notas: HEAD solto"


def test_fetch_falhado(cfg, tmp_path):
    r = repo_fonte(cfg)
    git(r, "remote", "add", "origin", str(tmp_path / "nao-existe.git"))
    assert arquivar(cfg)[-1] == "AVISO: sem push em notas: fetch falhou"
    assert log(r)[0] == "chore(arquivo): 2 ficheiros"     # o commit fica local


def test_aviso_sem_stderr_do_git(cfg, tmp_path):
    # o URL do remoto pode levar credenciais: nunca entra no log
    r = repo_fonte(cfg)
    git(r, "remote", "add", "origin", f"file://utilizador:segredo@{tmp_path}/nao-existe.git")
    linhas = arquivar(cfg)
    assert not any("segredo" in x for x in linhas)
