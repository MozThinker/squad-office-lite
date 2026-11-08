"""D16: `office_runner.py doctor` — uma linha `✓ <o quê>` ou `✗ <o quê> → <comando>` por verificação, exit 0/1,
segue com o office.yaml inválido e nunca mostra o valor da credencial. `claude` falso de tests/bin; `gh`, `docker`,
`systemctl`, `loginctl`, `getent` e `hostname` falsos num PATH temporário; HOME temporário; portas simuladas."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import diagnostico, lancador, officecfg

CHAVE = "sk-ant-api03-" + "Kk4" * 12
TOKEN = "sk-ant-oat01-" + "Jj8" * 12
GH_FALSO = "gho_" + "falso" * 3   # montado: um literal parece um token ao gitleaks
IP = "203.0.113.7"


def falsos(tmp: Path, nome="bin-falsos", **saidas) -> Path:
    """Um script /bin/sh por comando: saidas[cmd] = (rc, stdout)."""
    pasta = tmp / nome
    pasta.mkdir()
    omissao = {"docker": (0, "27.0.1"), "loginctl": (0, "yes"), "systemctl": (0, ""), "gh": (0, ""),
               "getent": (0, f"{IP}      STREAM office.example.org\n{IP}      DGRAM"), "hostname": (0, f"{IP} 10.0.0.2")}
    omissao.update(saidas)
    for cmd, (rc, out) in omissao.items():
        if rc is None:
            continue   # comando ausente
        f = pasta / cmd
        f.write_text(f"#!/bin/sh\necho \"$0 $@\" >> '{tmp / 'chamadas'}'\nprintf '%s\\n' '{out}'\nexit {rc}\n",
                     encoding="utf-8")
        f.chmod(0o755)
    return pasta


def preparar(tmp, monkeypatch, com_claude=True, dominio="office.example.org", cred=f"ANTHROPIC_API_KEY={CHAVE}\n",
             cred_modo=0o600, portas=True, **saidas):
    c.ambiente(monkeypatch, tmp)
    path_ = [str(falsos(tmp, **saidas))] + ([str(c.BIN)] if com_claude else [])
    monkeypatch.setenv("PATH", os.pathsep.join(path_))
    monkeypatch.setattr(diagnostico, "porta_aberta", lambda p: portas)
    _, path = c.config(tmp)
    if cred is not None:
        f = tmp / "claude.env"
        f.write_text(cred, encoding="utf-8")
        f.chmod(cred_modo)
    env = tmp / ".env"
    env.write_text(f"OFFICE_SITE='{dominio or ':80'}'\nOFFICE_DOMINIO='{dominio}'\nOFFICE_PROXY_SEGREDO='segredo-proxy'\n"
                   "OFFICE_AUTH_HASH='$2a$14$hash'\n", encoding="utf-8")
    env.chmod(0o600)
    return path


def doctor(path) -> tuple[int, list[str]]:
    return diagnostico.doctor(str(path))


def test_tudo_bem_sai_com_0(tmp_path, monkeypatch):
    path = preparar(tmp_path, monkeypatch)
    codigo, linhas = doctor(path)
    assert codigo == 0, linhas
    assert all(x.startswith("✓ ") and "→" not in x for x in linhas)
    texto = "\n".join(linhas)
    for oque in ("office.yaml válido", "Docker a responder (grupo docker)", "linger activo", "timer office-runner.timer",
                 "claude no PATH", "credencial do Claude (claude.env)", "sessão do gh",
                 "o domínio office.example.org aponta para esta VPS", "porta 80 à escuta", "porta 443 à escuta",
                 f"versão instalada {officecfg.VERSAO}"):
        assert oque in texto, oque
    chamadas = (tmp_path / "chamadas").read_text(encoding="utf-8")
    assert "gh auth status" in chamadas and "systemctl --user is-active --quiet office-runner.timer" in chamadas
    assert "getent ahosts office.example.org" in chamadas and "hostname -I" in chamadas


def test_falhas_com_o_comando_que_corrige(tmp_path, monkeypatch):
    path = preparar(tmp_path, monkeypatch, com_claude=False, cred_modo=0o644, portas=False,
                    docker=(1, ""), loginctl=(0, "no"), systemctl=(3, ""), gh=(1, ""), getent=(2, ""))
    codigo, linhas = doctor(path)
    assert codigo == 1
    eu = diagnostico._eu()
    esperado = {
        "✗ Docker a responder (grupo docker) → sudo usermod -aG docker " + eu,
        f"✗ linger activo (o runner continua sem sessão aberta) → sudo loginctl enable-linger {eu}",
        "✗ timer office-runner.timer activo → systemctl --user enable --now office-runner.timer",
        "✗ claude no PATH → curl -fsSL https://claude.ai/install.sh | bash",
        f"✗ credencial do Claude com permissões abertas (claude.env) → chmod 600 {tmp_path / 'claude.env'}",
        "✗ sessão do gh (gh auth status) → gh auth login",
        "✗ o domínio office.example.org ainda não resolve → cria um registo A para o IP desta VPS",
        f"✗ porta 80 à escuta → docker compose -f {tmp_path / 'docker-compose.yml'} up -d",
    }
    for e in esperado:
        assert any(x.startswith(e) for x in linhas), (e, linhas)
    assert all(x.startswith(("✓ ", "✗ ")) for x in linhas)
    assert all(" → " in x for x in linhas if x.startswith("✗"))


def test_dominio_noutro_ip(tmp_path, monkeypatch):
    path = preparar(tmp_path, monkeypatch, hostname=(0, "198.51.100.9"))
    codigo, linhas = doctor(path)
    assert codigo == 1
    assert (f"✗ o domínio office.example.org aponta para {IP}, mas esta VPS tem 198.51.100.9 "
            "(se a VPS está atrás de NAT, ignora) → corrige o registo A do domínio") in linhas


def test_sem_dominio_so_porta_80(tmp_path, monkeypatch):
    path = preparar(tmp_path, monkeypatch, dominio="")
    codigo, linhas = doctor(path)
    assert codigo == 0
    assert "✓ sem domínio: DNS não se aplica" in linhas
    assert not any("443" in x for x in linhas)
    assert "getent" not in (tmp_path / "chamadas").read_text(encoding="utf-8")


def test_sem_docker_nem_gh(tmp_path, monkeypatch):
    path = preparar(tmp_path, monkeypatch, docker=(None, ""), gh=(None, ""))
    codigo, linhas = doctor(path)
    assert codigo == 1
    assert any(x.startswith("✗ Docker não está instalado → ") for x in linhas)
    assert any(x.startswith("✗ gh não está instalado → ") for x in linhas)


@pytest.mark.parametrize("cred", [
    f"ANTHROPIC_API_KEY={CHAVE}\n",
    f"ANTHROPIC_API_KEY={TOKEN}\n",                       # formato errado: Falha com a frase, sem o valor
    f"CLAUDE_CODE_OAUTH_TOKEN={CHAVE}\n",
    f"ANTHROPIC_API_KEY={CHAVE}\nCLAUDE_CODE_OAUTH_TOKEN={TOKEN}\n",
])
def test_credencial_nunca_na_saida(tmp_path, monkeypatch, cred):
    path = preparar(tmp_path, monkeypatch, cred=cred)
    monkeypatch.setattr(lancador, "SEGREDOS", {"ANTHROPIC_API_KEY": CHAVE, "GH_TOKEN": "gh" + "o_falso1234567890"})
    _, linhas = doctor(path)
    texto = "\n".join(linhas)
    for s in (CHAVE, TOKEN, "Kk4Kk4", "Jj8Jj8", "segredo-proxy", "$2a$14$hash", "o_falso1234567890"):
        assert s not in texto, s
    linha = next(x for x in linhas if "credencial do Claude" in x)
    if cred == f"ANTHROPIC_API_KEY={CHAVE}\n":
        assert linha == "✓ credencial do Claude (claude.env)"
    else:
        assert linha.startswith("✗ credencial do Claude: credenciais do Claude inválidas (claude.env")
        assert linha.endswith(f"→ cd {lancador.RUNNER_REPO} && ./install.sh --credenciais")


def test_credencial_pelo_login_ou_nenhuma(tmp_path, monkeypatch):
    path = preparar(tmp_path, monkeypatch, cred=None)
    _, linhas = doctor(path)
    assert any(x.startswith("✗ credencial do Claude: nenhuma → ") for x in linhas)
    login = tmp_path / "casa" / ".claude" / ".credentials.json"
    login.parent.mkdir(parents=True)
    login.write_text("{}", encoding="utf-8")
    _, linhas = doctor(path)
    assert "✓ credencial do Claude (login do claude)" in linhas


def test_office_yaml_invalido_diz_e_segue(tmp_path, monkeypatch):
    path = preparar(tmp_path, monkeypatch)
    path.write_text("squads: [\n", encoding="utf-8")
    codigo, linhas = doctor(path)
    assert codigo == 1
    assert linhas[0].startswith("✗ office.yaml inválido (")
    assert linhas[0].endswith(f"→ python3 {lancador.RUNNER_REPO}/officecfg.py validar {path}")
    assert len(linhas) == 11 and all(x.startswith("✓") for x in linhas[1:])


def test_dominio_do_env_validado(tmp_path):
    env = tmp_path / ".env"
    for linha, esperado in (("OFFICE_DOMINIO='a.example.org'", "a.example.org"), ("OFFICE_DOMINIO=''", None),
                            ("OFFICE_DOMINIO='x; rm -rf /'", None), ("OFFICE_SITE=':80'", None)):
        env.write_text(linha + "\n", encoding="utf-8")
        assert diagnostico.dominio(str(tmp_path / "office.yaml")) == esperado
    assert diagnostico.dominio(str(tmp_path / "nada" / "office.yaml")) is None


def test_subcomando_em_subprocesso(tmp_path, monkeypatch):
    # o main: o doctor corre antes da validação estrita da config (inválida → diz e segue, exit 1)
    path = preparar(tmp_path, monkeypatch)
    path.write_text("versao: 2\n", encoding="utf-8")
    env = dict(os.environ, ANTHROPIC_API_KEY=CHAVE)
    r = subprocess.run([sys.executable, str(c.RUNNER), "doctor", "--config", str(path)], env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 1, r.stderr
    linhas = r.stdout.splitlines()
    assert linhas[0].startswith("✗ office.yaml inválido (squads: obrigatório) → ")
    assert any(x.startswith("✓ versão instalada ") for x in linhas)
    assert CHAVE not in r.stdout + r.stderr


@pytest.mark.parametrize("linha_env, renv_modo, esperado", [
    (f"ANTHROPIC_API_KEY={CHAVE}", 0o600, "✓ credencial do Claude (runner.env)"),
    (f"CLAUDE_CODE_OAUTH_TOKEN='{TOKEN}'", 0o600, "✓ credencial do Claude (runner.env)"),
    (f"ANTHROPIC_API_KEY={CHAVE}", 0o640, "✗ credencial do Claude com permissões abertas (runner.env)"),
    (f"ANTHROPIC_API_KEY={TOKEN}", 0o600, "✗ credencial do Claude: nenhuma"),
])
def test_credencial_so_no_runner_env(tmp_path, monkeypatch, linha_env, renv_modo, esperado):
    # o runner corre com o EnvironmentFile da unit: o doctor numa sessão SSH tem de ver o mesmo
    path = preparar(tmp_path, monkeypatch, cred=None)
    renv = tmp_path / "runner.env"
    renv.write_text(f"OFFICE_TELEGRAM_TOKEN=123:abc\n{linha_env}\nGH_TOKEN={GH_FALSO}\n", encoding="utf-8")
    renv.chmod(renv_modo)
    _, linhas = doctor(path)
    linha = next(x for x in linhas if "credencial do Claude" in x)
    assert linha.startswith(esperado), linhas
    texto = "\n".join(linhas)
    for s in (CHAVE, TOKEN, "Kk4Kk4", "Jj8Jj8", "falso1234567890", "123:abc"):
        assert s not in texto, s


def test_runner_env_so_as_chaves_do_runner_e_so_ficheiro_do_utilizador(tmp_path):
    renv = tmp_path / "runner.env"
    renv.write_text(f"OFFICE_TELEGRAM_TOKEN=1:x\n# comentário\nlixo\nANTHROPIC_API_KEY=\"{CHAVE}\"\nGH_TOKEN=abc\n",
                    encoding="utf-8")
    renv.chmod(0o600)
    assert diagnostico.runner_env(tmp_path) == {"ANTHROPIC_API_KEY": CHAVE, "GH_TOKEN": "abc"}
    outra = tmp_path / "outra"
    outra.mkdir()
    (outra / "runner.env").symlink_to(renv)
    assert diagnostico.runner_env(outra) == {}            # symlink: não se segue
    assert diagnostico.runner_env(tmp_path / "nada") == {}


def test_gh_token_do_runner_env_chega_ao_gh(tmp_path, monkeypatch):
    # gh falso que só tem sessão com o GH_TOKEN (o do runner.env, não o da sessão)
    path = preparar(tmp_path, monkeypatch)
    gh = next(p for p in os.environ["PATH"].split(os.pathsep) if p.endswith("bin-falsos")) + "/gh"
    Path(gh).write_text('#!/bin/sh\n[ "$GH_TOKEN" = "gho_do_runner_env" ]\n', encoding="utf-8")
    assert "✗ sessão do gh (gh auth status) → gh auth login" in doctor(path)[1]
    renv = tmp_path / "runner.env"
    renv.write_text("GH_TOKEN=gho_do_runner_env\n", encoding="utf-8")
    renv.chmod(0o600)
    monkeypatch.setattr(lancador, "SEGREDOS", {})
    _, linhas = doctor(path)
    assert "✓ sessão do gh (gh auth status)" in linhas
    assert "gho_do_runner_env" not in "\n".join(linhas)


def test_claude_em_local_bin_ou_no_path_da_unit(tmp_path, monkeypatch):
    path = preparar(tmp_path, monkeypatch, com_claude=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert "✗ claude no PATH → curl -fsSL https://claude.ai/install.sh | bash" in doctor(path)[1]
    local = tmp_path / "casa" / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "claude").symlink_to(c.BIN / "claude")
    assert "✓ claude no PATH" in doctor(path)[1]
    (local / "claude").unlink()
    unit = tmp_path / "casa" / ".config" / "systemd" / "user" / "office-runner.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f"[Service]\nEnvironment=PATH={c.BIN}:/usr/bin:/bin\n", encoding="utf-8")
    assert "✓ claude no PATH" in doctor(path)[1]
