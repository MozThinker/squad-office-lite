"""tools/verificar_modelos.py (CA-30): verde nos modelos do repo, vermelho (ficheiro:linha: motivo) com fixtures más."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
import verificar_modelos as vm

MODELOS = RAIZ / "modelos" / "squads"
CASA = "/ho" + "me/"   # o git grep da CI não quer o caminho literal nos testes


def test_modelos_do_repo_passam():
    assert sorted(p.name for p in MODELOS.iterdir()) == ["conteudo", "desenvolvimento", "investigacao", "observacao"]
    assert vm.verificar(MODELOS, vm.nomes_internos()) == []
    r = subprocess.run([sys.executable, str(RAIZ / "tools" / "verificar_modelos.py")], capture_output=True, text=True,
                       check=False)
    assert r.returncode == 0 and r.stdout == ""


def test_nomes_internos_vem_do_ci():
    rx = vm.nomes_internos()
    assert rx is not None
    assert "nome interno" not in vm.achados_linha("uma squad genérica", rx)


def test_nomes_internos_sem_linha(tmp_path):
    (tmp_path / "ci.yml").write_text("jobs:\n", encoding="utf-8")
    assert vm.nomes_internos(tmp_path / "ci.yml") is None
    assert vm.nomes_internos(tmp_path / "nao-ha.yml") is None


@pytest.fixture
def copia(tmp_path):
    destino = tmp_path / "squads"
    shutil.copytree(MODELOS, destino)
    return destino


def _acrescentar(f: Path, linha: str) -> int:
    texto = f.read_text(encoding="utf-8")
    f.write_text(texto + linha + "\n", encoding="utf-8")
    return texto.count("\n") + 1


@pytest.mark.parametrize("linha, motivo", [
    (f"vê em {CASA}alguem/notas", "caminho pessoal"),
    ("escreve para alguem@example.org", "e-mail"),
    ("liga para +351 912 345 678", "telefone"),
    ("o servidor 10.1.2.3 responde", "IP"),
    ("lê https://example.com/x", "URL"),
    ("ou www.example.com", "URL"),
    ("token ghp_" + "a" * 36, "segredo"),
])
def test_padroes_dao_ficheiro_e_linha(copia, linha, motivo):
    f = copia / "observacao" / "CLAUDE.md"
    n = _acrescentar(f, linha)
    assert vm.verificar(copia, None) == [f"squads/observacao/CLAUDE.md:{n}: {motivo}"]


def test_url_da_documentacao_passa(copia):
    _acrescentar(copia / "observacao" / "CLAUDE.md", "ver https://docs.anthropic.com/pt/subagentes")
    assert vm.verificar(copia, None) == []


def test_nome_interno(copia):
    rx = vm.nomes_internos()
    nome = re.sub(r"\[(.)\]", r"\1", rx.pattern.split("|")[0])   # 1.º nome da lista, sem a forma nom[e]
    n = _acrescentar(copia / "conteudo" / "README.md", f"feito para {nome.upper()}")
    assert vm.verificar(copia, rx) == [f"squads/conteudo/README.md:{n}: nome interno"]


def test_frontmatter_invalido_e_lead(copia):
    f = copia / "desenvolvimento" / "agents" / "revisor.md"
    f.write_text(f.read_text(encoding="utf-8").replace("model: haiku", "permissionMode: bypassPermissions"),
                 encoding="utf-8")
    r = copia / "investigacao" / "README.md"
    r.write_text(r.read_text(encoding="utf-8").replace("lead: lider", "lead: chefe"), encoding="utf-8")
    c = copia / "conteudo" / "CLAUDE.md"
    c.write_text(c.read_text(encoding="utf-8").replace("{{MISSAO}}", "missão"), encoding="utf-8")
    assert vm.verificar(copia, None) == [
        "squads/conteudo/CLAUDE.md:0: falta o marcador {{MISSAO}}",
        "squads/desenvolvimento/agents/revisor.md:1: frontmatter inválido",
        "squads/investigacao/README.md:1: lead não é um dos agentes"]


def test_symlink_e_binario(copia, tmp_path):
    (tmp_path / "fora.md").write_text("x\n", encoding="utf-8")
    (copia / "observacao" / "ligacao.md").symlink_to(tmp_path / "fora.md")
    (copia / "observacao" / "lixo.bin").write_bytes(b"\xff\xfe")
    assert vm.verificar(copia, None) == ["squads/observacao/ligacao.md:0: symlink",
                                         "squads/observacao/lixo.bin:0: não é UTF-8"]


def test_main_sai_1_com_achados(copia, capsys):
    _acrescentar(copia / "observacao" / "CLAUDE.md", "contacto alguem@example.org")
    assert vm.main([str(copia)]) == 1
    assert capsys.readouterr().out.strip().endswith(": e-mail")
    assert vm.main([str(copia / "nao-ha")]) == 1
