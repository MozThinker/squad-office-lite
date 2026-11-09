"""QA da 0.1.4 (modo Docker): contratos entre ficheiros que os testes de cada área não cruzam.

- AC-r4: nenhum ramo «docker» em lancador.correr/preparar/parar.
- AC-c3/AC-f4/AC-f5: o office.yaml escrito pelo `configurar` (raízes do contentor) põe o runner a escrever nos mesmos
  volumes que o `office` lê; o `configurar` só cria pastas dentro de volumes graváveis (raiz só de leitura); o `chown`
  cobre todos os volumes do serviço; os ficheiros do volume `proxy` têm os nomes que o Caddyfile e o Office lêem;
  as variáveis da imagem batem com as constantes do `configurar`.
- D2 (risco): `chown` recusado na pasta de projectos montada (bind do Windows/macOS) não aborta o `configurar`.
- S1: o README edita o `office.yaml` pelo `configurar --office-yaml` (sem `--entrypoint sh` como root).

Só texto e funções puras; nada de Docker. Caminhos do contentor montados com Path (sem a casa literal: passo
«Genérico» da CI)."""
import os
import re
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_compose_docker import binds, servico, volumes
from tests.test_runner_comum import lancador, officecfg

import configurar as conf   # runner/ entra no sys.path com o test_runner_comum

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (ROOT / "compose.example.yaml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile.runner").read_text(encoding="utf-8")
CADDYFILE = (ROOT / "Caddyfile.docker").read_text(encoding="utf-8")
CASA = Path("/", "home", "office")


def cfg_do_configurar(repos=("exemplo",)):
    """office.yaml que o `configurar` escreve da primeira vez, com as RAIZES reais do contentor."""
    texto = conf.office_yaml_novo("ana", conf.bloco_projectos(list(repos)))
    return officecfg.validar(officecfg.ler_yaml(texto))


def dentro(p: Path, alvos) -> bool:
    return any(p == Path(a) or Path(a) in p.parents for a in alvos)


def env_imagem() -> dict:
    bloco = DOCKERFILE[DOCKERFILE.index("\nENV ") + 5:DOCKERFILE.index("\nUSER ")]
    return dict(re.findall(r"([A-Z_]+)=(\S+)", bloco))


# ---------------------------------------------------------------- AC-r4

@pytest.mark.parametrize("nome", ["correr", "preparar", "parar"])
def test_sem_ramo_docker_no_mesmo_codigo_do_vps(nome):
    import inspect
    fonte = inspect.getsource(getattr(lancador, nome))
    assert "OFFICE_MODO" not in fonte and "docker" not in fonte.lower(), nome


# ---------------------------------------------------------------- AC-c3, AC-f4: runner escreve onde o office lê

def test_runner_escreve_nos_volumes_que_o_office_le():
    cfg = cfg_do_configurar()
    office = {destino: vol for destino, (vol, _) in volumes(servico(COMPOSE, "office")).items()}
    runner = {vol: destino for destino, (vol, _) in volumes(servico(COMPOSE, "runner")).items()}
    for origem, destino_office, _, _ in officecfg.MOUNTS:
        vol = office[destino_office]
        no_runner = (officecfg.raiz(cfg, "claude_projects") if origem == "claude_projects"
                     else officecfg.caminho(cfg, origem))
        assert Path(runner[vol]) == no_runner, (origem, vol, runner[vol], no_runner)
    assert officecfg.raiz(cfg, "projectos") == Path(binds(servico(COMPOSE, "runner"))[0][1])
    assert officecfg.caminho(cfg, "runner") == Path(runner["runner"])


@pytest.mark.parametrize("servico_", ["configurar", "runner"])
def test_pastas_do_office_yaml_so_em_volumes_gravaveis(servico_):
    # os dois serviços têm read_only: true; um mkdir fora de um volume gravável dava EROFS
    b = servico(COMPOSE, servico_)
    gravaveis = [d for d, (_, ro) in volumes(b).items() if not ro] + [t for _, t in binds(b)]
    for _, p in officecfg.pastas(cfg_do_configurar()):
        assert dentro(p, gravaveis), (servico_, p)


def test_chown_cobre_todos_os_volumes_do_configurar(monkeypatch):
    monkeypatch.setenv("OFFICE_SEGREDOS_DIR", env_imagem()["OFFICE_SEGREDOS_DIR"])
    chown = {str(p) for p in conf.caminhos_chown(Path(conf.CONFIG_OMISSAO))}
    alvos = set(volumes(servico(COMPOSE, "configurar")))
    assert alvos <= chown, alvos - chown
    assert chown - alvos == {str(CASA / ".claude")}   # pasta intermédia do mount aninhado claude-projects


def test_constantes_do_configurar_iguais_a_imagem_e_ao_compose():
    env = env_imagem()
    assert env["OFFICE_CONFIG"] == conf.CONFIG_OMISSAO
    assert Path(env["HOME"]) == conf.CASA == CASA
    assert Path(conf.RAIZES["claude_projects"]) == CASA / ".claude" / "projects"
    conf_vols = volumes(servico(COMPOSE, "configurar"))
    for destino, vol in ((str(Path(conf.CONFIG_OMISSAO).parent), "config"), (env["OFFICE_SEGREDOS_DIR"], "segredos"),
                         (str(conf.PROXY), "proxy"), (str(CASA), "runner-home")):
        assert conf_vols[destino] == (vol, False), destino
    assert ("${OFFICE_PROJECTOS:-./projectos}", conf.RAIZES["projectos"]) in binds(servico(COMPOSE, "configurar"))


def test_ficheiros_do_proxy_com_os_nomes_que_o_caddy_e_o_office_leem():
    fonte = (ROOT / "runner" / "configurar.py").read_text(encoding="utf-8")
    escritos = set(re.findall(r'escrever_privado\(PROXY / "([a-z]+)"', fonte))
    assert escritos == {"utilizador", "hash", "segredo"}
    codigo = "\n".join(x for x in CADDYFILE.splitlines() if not x.lstrip().startswith("#"))
    lidos = set(re.findall(r"\{file\.([^}]+)\}", codigo))
    assert lidos == {str(conf.PROXY / n) for n in escritos}
    assert f"      OFFICE_PROXY_SEGREDO_FILE: {conf.PROXY / 'segredo'}\n" in servico(COMPOSE, "office")
    assert "{file." + str(conf.PROXY / "segredo") + "}" in CADDYFILE.split("header_up X-Office-Segredo", 1)[1]


# ---------------------------------------------------------------- D2 (risco): chown recusado na pasta de projectos

def test_chown_recusado_na_pasta_de_projectos_nao_aborta(tmp_path, monkeypatch, capsys):
    c.ambiente(monkeypatch, tmp_path)
    projectos = tmp_path / "projectos"
    projectos.mkdir()
    monkeypatch.setattr(conf, "RAIZES", {"dados": str(tmp_path / "dados"), "projectos": str(projectos),
                                         "claude_projects": str(tmp_path / "casa" / ".claude" / "projects")})
    monkeypatch.setattr(conf, "CASA", tmp_path / "casa")
    monkeypatch.setattr(conf, "PROXY", tmp_path / "proxy")
    monkeypatch.setenv("OFFICE_CONFIG", str(tmp_path / "config" / "office.yaml"))
    monkeypatch.setenv("OFFICE_SEGREDOS_DIR", str(tmp_path / "segredos"))
    for k in ("OFFICE_UID", "OFFICE_GID"):
        monkeypatch.delenv(k, raising=False)
    if os.geteuid() == 0:
        pytest.skip("corre sem root")
    for p in conf.caminhos_chown(Path(os.environ["OFFICE_CONFIG"])):
        p.mkdir(parents=True, exist_ok=True)
    real_fstat, euid = os.fstat, [0]
    alvo = os.stat(projectos)

    def fstat(fd):   # /projectos aparece de root, como um bind criado pelo Docker
        st = real_fstat(fd)
        if (st.st_dev, st.st_ino) == (alvo.st_dev, alvo.st_ino):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 0, 0, *tuple(st)[6:10]))
        return st

    def fchown(fd, uid, gid):
        st = real_fstat(fd)
        if (st.st_dev, st.st_ino) == (alvo.st_dev, alvo.st_ino):
            raise PermissionError(1, "Operation not permitted")

    def setuid(uid):
        euid[0] = uid

    monkeypatch.setattr(conf.os, "fstat", fstat)
    monkeypatch.setattr(conf.os, "fchown", fchown)
    monkeypatch.setattr(conf.os, "geteuid", lambda: euid[0])
    monkeypatch.setattr(conf.os, "getuid", lambda: euid[0])
    monkeypatch.setattr(conf.os, "setgroups", lambda g: None)
    monkeypatch.setattr(conf.os, "setgid", lambda g: None)
    monkeypatch.setattr(conf.os, "setuid", setuid)
    monkeypatch.setattr(conf, "_ler", lambda texto: "3")   # credencial: agora não
    assert conf.main(["--credenciais"]) == 0, capsys.readouterr().err
    assert f"não consegui mudar o dono de {projectos}: sigo sem isso" in capsys.readouterr().err



def test_pasta_de_projectos_de_root_com_conteudo_nao_muda_de_dono(tmp_path, monkeypatch, capsys):
    """I-2: OFFICE_PROJECTOS numa pasta do host de root e com conteúdo (ex.: /srv) nunca leva chown."""
    c.ambiente(monkeypatch, tmp_path)
    projectos = tmp_path / "projectos"
    (projectos / "algo").mkdir(parents=True)
    monkeypatch.setattr(conf, "RAIZES", {"dados": str(tmp_path / "dados"), "projectos": str(projectos),
                                         "claude_projects": str(tmp_path / "casa" / ".claude" / "projects")})
    monkeypatch.setattr(conf, "CASA", tmp_path / "casa")
    monkeypatch.setattr(conf, "PROXY", tmp_path / "proxy")
    monkeypatch.setenv("OFFICE_CONFIG", str(tmp_path / "config" / "office.yaml"))
    monkeypatch.setenv("OFFICE_SEGREDOS_DIR", str(tmp_path / "segredos"))
    for k in ("OFFICE_UID", "OFFICE_GID"):
        monkeypatch.delenv(k, raising=False)
    if os.geteuid() == 0:
        pytest.skip("corre sem root")
    for p in conf.caminhos_chown(Path(os.environ["OFFICE_CONFIG"])):
        p.mkdir(parents=True, exist_ok=True)
    real_fstat, euid, chowns = os.fstat, [0], []
    alvo = os.stat(projectos)

    def fstat(fd):
        st = real_fstat(fd)
        if (st.st_dev, st.st_ino) == (alvo.st_dev, alvo.st_ino):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 0, 0, *tuple(st)[6:10]))
        return st

    def fchown(fd, uid, gid):
        st = real_fstat(fd)
        chowns.append((st.st_dev, st.st_ino))

    def setuid(uid):
        euid[0] = uid

    monkeypatch.setattr(conf.os, "fstat", fstat)
    monkeypatch.setattr(conf.os, "fchown", fchown)
    monkeypatch.setattr(conf.os, "geteuid", lambda: euid[0])
    monkeypatch.setattr(conf.os, "getuid", lambda: euid[0])
    monkeypatch.setattr(conf.os, "setgroups", lambda g: None)
    monkeypatch.setattr(conf.os, "setgid", lambda g: None)
    monkeypatch.setattr(conf.os, "setuid", setuid)
    monkeypatch.setattr(conf, "_ler", lambda texto: "3")
    assert conf.main(["--credenciais"]) == 0, capsys.readouterr().err
    assert (alvo.st_dev, alvo.st_ino) not in chowns
    assert f"{projectos} é de root e não está vazia: não lhe mudo o dono" in capsys.readouterr().err

# ---------------------------------------------------------------- S1: editar o office.yaml sem shell como root

def test_readme_edita_o_office_yaml_pelo_configurar():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    sec = readme[readme.index("### Editar o `office.yaml`"):]
    sec = sec[:sec.index("\n### ", 1)]
    cmd = "docker compose run --rm -v ./office.yaml:/novo/office.yaml:ro configurar --office-yaml /novo/office.yaml"
    assert cmd in sec
    assert "--entrypoint" not in readme and "cp /novo/office.yaml" not in readme
    assert 'entrypoint: ["configurar"]' in servico(COMPOSE, "configurar")   # o argumento vai ao wrapper -I
