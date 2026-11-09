"""Modo Docker no runner: OFFICE_SEGREDOS_DIR (AC-r5, D6), DISABLE_AUTOUPDATER só com OFFICE_MODO=docker (AC-r6) e o
ramo Docker do `doctor` (AC-d1/d2). `claude` falso de tests/bin, `gh` falso num PATH temporário, tudo em tmp_path."""
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import diagnostico, lancador, office_runner, officecfg

API = "sk-ant-api03-DOCKERapi_0123456789-abc"
OUTRA = "sk-ant-api03-AOLADOapi_0123456789-xyz"


@pytest.fixture
def limpo(monkeypatch):
    monkeypatch.delenv("OFFICE_SEGREDOS_DIR", raising=False)
    monkeypatch.delenv("OFFICE_MODO", raising=False)


def segredos(tmp: Path, monkeypatch) -> Path:
    pasta = tmp / "segredos"
    pasta.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv("OFFICE_SEGREDOS_DIR", str(pasta))
    return pasta


def privado(f: Path, texto: str, modo: int = 0o600) -> Path:
    f.write_text(texto, encoding="utf-8")
    f.chmod(modo)
    return f


# ---------------------------------------------------------------- credenciais_claude (D6)

def test_credenciais_sem_a_variavel_ao_lado_da_config(limpo):
    assert officecfg.credenciais_claude("/x/y/office.yaml") == Path("/x/y/claude.env")


def test_credenciais_com_a_variavel(limpo, monkeypatch):
    monkeypatch.setenv("OFFICE_SEGREDOS_DIR", "/segredos")
    assert officecfg.credenciais_claude("/config/office.yaml") == Path("/segredos/claude.env")


@pytest.mark.parametrize("valor", ["", "segredos", "./segredos", "~/segredos"])
def test_credenciais_variavel_vazia_ou_relativa_ignorada(limpo, monkeypatch, valor):
    monkeypatch.setenv("OFFICE_SEGREDOS_DIR", valor)
    assert officecfg.credenciais_claude("/x/y/office.yaml") == Path("/x/y/claude.env")


# ---------------------------------------------------------------- settings_base (AC-r5)

def test_deny_segue_a_pasta_dos_segredos(tmp_path, monkeypatch, limpo):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    seg = segredos(tmp_path, monkeypatch)
    deny = lancador.settings_base(cfg, str(path))["permissions"]["deny"]
    for f in (seg / "claude.env", seg / "runner.env"):
        assert lancador._regra("Read", f) in deny and lancador._regra("Edit", f) in deny
    # o .env e o office.yaml continuam ao lado da config; o claude.env de lá deixa de ser o dos segredos
    assert lancador._regra("Read", tmp_path / ".env") in deny and lancador._regra("Edit", path) in deny
    assert lancador._regra("Read", tmp_path / "claude.env") not in deny


def test_deny_sem_a_variavel_igual_ao_de_sempre(tmp_path, monkeypatch, limpo):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    deny = lancador.settings_base(cfg, str(path))["permissions"]["deny"]
    assert deny[:3] == [lancador._regra("Read", tmp_path / n) for n in ("claude.env", "runner.env", ".env")]


def test_deny_da_casa_so_no_modo_docker(tmp_path, monkeypatch, limpo):   # S2: casa = volume runner-home
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    vps = lancador.settings_base(cfg, str(path))["permissions"]["deny"]
    casa = Path.home()
    extra = [lancador._regra("Edit", casa / ".local" / "**"), lancador._regra("Edit", casa / ".gitconfig")]
    assert not set(extra) & set(vps)
    monkeypatch.setenv("OFFICE_MODO", "docker")
    docker = lancador.settings_base(cfg, str(path))["permissions"]["deny"]
    assert [x for x in docker if x not in vps] == extra and set(vps) <= set(docker)
    monkeypatch.setenv("OFFICE_MODO", "vps")
    assert lancador.settings_base(cfg, str(path))["permissions"]["deny"] == vps


# ---------------------------------------------------------------- DISABLE_AUTOUPDATER (AC-r6)

def test_autoupdater_desligado_so_no_modo_docker(tmp_path, monkeypatch, limpo):
    c.ambiente(monkeypatch, tmp_path)
    vps = lancador.env_claude(None)
    assert "DISABLE_AUTOUPDATER" not in vps
    monkeypatch.setenv("OFFICE_MODO", "docker")
    docker = lancador.env_claude(None)
    assert docker.pop("DISABLE_AUTOUPDATER") == "1"
    assert docker == vps   # o resto da lista branca não muda
    monkeypatch.setenv("OFFICE_MODO", "vps")
    assert "DISABLE_AUTOUPDATER" not in lancador.env_claude(None)


def test_modo_nao_passa_ao_claude(tmp_path, monkeypatch, limpo):
    c.ambiente(monkeypatch, tmp_path)
    monkeypatch.setenv("OFFICE_MODO", "docker")
    segredos(tmp_path, monkeypatch)
    env = lancador.env_claude(None)
    assert not any(k.startswith("OFFICE_") for k in env)


# ---------------------------------------------------------------- correr com a credencial dos segredos (AC-r5)

def test_correr_le_o_claude_env_dos_segredos(tmp_path, monkeypatch, limpo):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    monkeypatch.setenv("FAKE_CLAUDE_CRED", str(tmp_path / "cred.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_EXIGE_CRED", "1")
    seg = segredos(tmp_path, monkeypatch)
    privado(seg / "claude.env", f"ANTHROPIC_API_KEY={API}\n")
    privado(tmp_path / "claude.env", f"ANTHROPIC_API_KEY={OUTRA}\n")   # ao lado da config: ignorado
    c.pedido(cfg)
    linhas = office_runner.uma_vez(cfg, str(path))
    assert not any("sem claude.env" in x for x in linhas)
    m = lancador.correr(cfg, c.feito(cfg)["task_id"], cfg_path=str(path))
    assert m["status"] == "concluída"
    vistos = [json.loads(x) for x in (tmp_path / "cred.jsonl").read_text(encoding="utf-8").splitlines()]
    assert vistos == [{"ANTHROPIC_API_KEY": hashlib.sha256(API.encode()).hexdigest()}]


def test_claude_env_dos_segredos_aberto_recusado(tmp_path, monkeypatch, limpo):
    c.ambiente(monkeypatch, tmp_path)
    seg = segredos(tmp_path, monkeypatch)
    _, path = c.config(tmp_path)
    privado(seg / "claude.env", f"ANTHROPIC_API_KEY={API}\n", 0o644)
    valor, ficheiro, motivo = diagnostico.credencial(str(path))
    assert (valor, ficheiro) == ("modo", seg / "claude.env") and API not in (motivo or "")


def test_runner_env_dos_segredos_no_diagnostico(tmp_path, monkeypatch, limpo):
    c.ambiente(monkeypatch, tmp_path)
    seg = segredos(tmp_path, monkeypatch)
    _, path = c.config(tmp_path)
    privado(seg / "runner.env", f"ANTHROPIC_API_KEY={API}\n")
    lancador.SEGREDOS.update(diagnostico.runner_env(officecfg.credenciais_claude(path).parent))
    assert diagnostico.credencial(str(path))[:2] == ("ok", seg / "runner.env")


# ---------------------------------------------------------------- doctor no modo Docker (AC-d1/d2)

def gh_falso(tmp: Path, rc: int) -> Path:
    pasta = tmp / "bin-gh-doctor"
    pasta.mkdir(exist_ok=True)
    f = pasta / "gh"
    f.write_text(f"#!/bin/sh\necho \"gh $@\" >> '{tmp / 'chamadas'}'\nexit {rc}\n", encoding="utf-8")
    f.chmod(0o755)
    return pasta


def claude_versao(tmp: Path) -> Path:
    """`claude` que responde a --version (o falso de tests/bin lê o stdin todo)."""
    pasta = tmp / "bin-claude-doctor"
    pasta.mkdir(exist_ok=True)
    f = pasta / "claude"
    f.write_text("#!/bin/sh\n[ \"$1\" = --version ] && echo '2.1.311 (Claude Code)' && exit 0\nexit 1\n",
                 encoding="utf-8")
    f.chmod(0o755)
    return pasta


def docker(tmp: Path, monkeypatch, gh_rc=0, com_claude=True, cred=True, estado=True, repos=("exemplo", "loja-web")):
    c.ambiente(monkeypatch, tmp)
    monkeypatch.setenv("OFFICE_MODO", "docker")
    seg = segredos(tmp, monkeypatch)
    caminhos = [str(gh_falso(tmp, gh_rc))] + ([str(claude_versao(tmp))] if com_claude else [])
    monkeypatch.setenv("PATH", os.pathsep.join(caminhos + ["/usr/bin", "/bin"]))
    cfg, path = c.config(tmp)
    for r in repos:
        c.repo(cfg, r)
    if cred:
        privado(seg / "claude.env", f"ANTHROPIC_API_KEY={API}\n")
    if estado:
        diagnostico.gravar_estado(cfg, str(path))
    return cfg, path


def test_doctor_docker_tudo_bem(tmp_path, monkeypatch, limpo):
    _, path = docker(tmp_path, monkeypatch)
    codigo, linhas = diagnostico.doctor(str(path))
    assert codigo == 0, linhas
    texto = "\n".join(linhas)
    for oque in ("✓ office.yaml válido", "✓ claude no PATH (2.1.311 (Claude Code))",
                 "✓ credencial do Claude (claude.env)", "✓ sessão do gh (gh auth status)",
                 f"✓ projecto exemplo em {tmp_path / 'projectos' / 'exemplo'} (git, gravável)",
                 f"✓ projecto loja em {tmp_path / 'projectos' / 'loja-web'} (git, gravável)",
                 "✓ ciclo do runner vivo", f"✓ versão instalada {officecfg.VERSAO}"):
        assert oque in texto, oque
    assert linhas[-1] == "ℹ as tarefas só correm com o PC ligado e o Docker Desktop aberto"
    for vps in ("linger", "timer", "Docker a responder", "porta", "domínio", "DNS"):
        assert vps not in texto, vps
    assert API not in texto


def test_doctor_docker_falhas_com_comandos_docker(tmp_path, monkeypatch, limpo):
    cfg, path = docker(tmp_path, monkeypatch, gh_rc=1, com_claude=False, cred=False, estado=False,
                       repos=("exemplo",))
    (officecfg.raiz(cfg, "projectos") / "loja-web").mkdir()   # pasta sem .git
    codigo, linhas = diagnostico.doctor(str(path))
    assert codigo == 1
    falhas = [x for x in linhas if x.startswith("✗")]
    esperado = {"claude no PATH": "docker compose pull; docker compose up -d",
                "credencial do Claude: nenhuma": "docker compose run --rm configurar --credenciais",
                "sessão do gh": "docker compose run --rm configurar --gh",
                "projecto loja:": "docker compose run --rm configurar --projectos",
                "ciclo do runner vivo": "docker compose up -d"}
    for oque, cmd in esperado.items():
        (linha,) = [x for x in falhas if oque in x]
        assert linha.split(" → ", 1)[1].count(cmd) == 1, linha
    assert len(falhas) == len(esperado)
    for x in falhas:
        assert "install.sh" not in x and "systemctl" not in x and "sudo" not in x
        assert "docker compose" in x


def test_doctor_docker_gh_ausente(tmp_path, monkeypatch, limpo):
    _, path = docker(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", os.pathsep.join([str(claude_versao(tmp_path)), "/nao-existe"]))
    _, linhas = diagnostico.doctor(str(path))
    assert "✗ gh não está instalado → docker compose pull; docker compose up -d" in linhas


def test_doctor_docker_estado_velho(tmp_path, monkeypatch, limpo):
    _, path = docker(tmp_path, monkeypatch)
    assert diagnostico.doctor_docker(str(path), now=time.time() + 170)[0] == 0
    codigo, linhas = diagnostico.doctor_docker(str(path), now=time.time() + 190)
    assert codigo == 1 and any(x.startswith("✗ ciclo do runner vivo") for x in linhas)


def test_doctor_docker_projecto_sem_escrita(tmp_path, monkeypatch, limpo):
    cfg, path = docker(tmp_path, monkeypatch)
    real = os.access
    alvo = officecfg.raiz(cfg, "projectos") / "loja-web"
    monkeypatch.setattr(diagnostico.os, "access", lambda p, m: False if Path(p) == alvo else real(p, m))
    _, linhas = diagnostico.doctor(str(path))
    (linha,) = [x for x in linhas if "projecto loja" in x]
    assert linha.startswith("✗") and "OFFICE_UID/OFFICE_GID" in linha and "docker compose up -d" in linha


def test_doctor_docker_pastas_de_dados_sem_escrita(tmp_path, monkeypatch, limpo):
    # nocopy: antes do configurar (ou com outro OFFICE_UID) as raízes dos volumes são de root
    cfg, path = docker(tmp_path, monkeypatch)
    _, linhas = diagnostico.doctor(str(path))
    assert f"✓ pastas de dados graváveis pelo uid {os.getuid()}" in linhas
    real = os.access
    alvo = officecfg.caminho(cfg, "runner")
    monkeypatch.setattr(diagnostico.os, "access", lambda p, m: False if Path(p) == alvo else real(p, m))
    codigo, linhas = diagnostico.doctor(str(path))
    (linha,) = [x for x in linhas if "pastas de dados" in x]
    assert codigo == 1
    assert linha == (f"✗ pastas de dados graváveis pelo uid {os.getuid()} ({alvo} não) → docker compose run --rm "
                     "configurar (com o mesmo OFFICE_UID/OFFICE_GID do .env)")


def test_doctor_docker_sem_office_yaml(tmp_path, monkeypatch, limpo):
    _, path = docker(tmp_path, monkeypatch)
    path.unlink()
    codigo, linhas = diagnostico.doctor(str(path))
    assert codigo == 1
    assert linhas[0] == "✗ office.yaml ainda não existe → docker compose run --rm configurar"
    assert not any("projecto" in x for x in linhas)


def test_doctor_docker_office_yaml_invalido(tmp_path, monkeypatch, limpo):
    _, path = docker(tmp_path, monkeypatch)
    path.write_text("versao: 2\n", encoding="utf-8")
    _, linhas = diagnostico.doctor(str(path))
    assert linhas[0].startswith("✗ office.yaml inválido (squads:") and "install.sh" not in linhas[0]


def test_doctor_docker_credencial_aberta(tmp_path, monkeypatch, limpo):
    _, path = docker(tmp_path, monkeypatch)
    (tmp_path / "segredos" / "claude.env").chmod(0o644)
    _, linhas = diagnostico.doctor(str(path))
    assert ("✗ credencial do Claude com permissões abertas (claude.env) → "
            "docker compose run --rm configurar --credenciais") in linhas


def test_doctor_docker_pelo_main(tmp_path, monkeypatch, limpo, capsys):
    _, path = docker(tmp_path, monkeypatch)
    monkeypatch.setattr(lancador, "guardar_segredos", lambda: None)
    assert office_runner.main(["doctor", "--config", str(path)]) == 0
    assert "ℹ as tarefas só correm com o PC ligado e o Docker Desktop aberto" in capsys.readouterr().out
