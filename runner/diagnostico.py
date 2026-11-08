"""Diagnóstico do runner (stdlib, Python >= 3.10): `estado.json` a cada passagem e o subcomando `doctor`.

estado.json (<dados>/runner-estado/, montado só de leitura no Office): só enumerados, nunca valores de segredos,
caminhos nem stderr de comandos. O `doctor` diz, uma linha por verificação, `✓ <o quê>` ou `✗ <o quê> → <comando>`;
nunca mostra o valor de uma credencial (só formato e modo do ficheiro).
"""
from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import socket
import stat
import subprocess
import time
from pathlib import Path

import lancador

import officecfg

ESTADO = "estado.json"
GH_CACHE = "gh-verificado.json"   # em <dados>/runner/ (privado do runner, fora do mount)
GH_S = 10                         # timeout do `gh auth status`
GH_CADA_S = 10 * 60               # no máximo uma verificação do gh a cada 10 min
CMD_S = 10                        # timeout dos comandos do doctor
TIMER = "office-runner.timer"
_DOMINIO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")


# ---------------------------------------------------------------- verificações partilhadas

def estado_claude() -> str:
    return "ok" if shutil.which("claude") else "falta"


def _aberto(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and bool(st.st_mode & 0o077)


def credencial(cfg_path: str) -> tuple[str, Path | None, str | None]:
    """(ok|falta|modo, ficheiro a corrigir ou None, motivo sem valores ou None). Ordem do runner: claude.env, a
    credencial do runner.env, o login do claude (~/.claude/.credentials.json, só se existe; nunca é lido)."""
    cred = officecfg.credenciais_claude(cfg_path)
    if os.path.lexists(cred):
        try:
            if lancador.credenciais(cred):
                return "ok", cred, None
        except lancador.Falha as e:   # a mensagem nunca leva o valor (lancador.credenciais)
            return ("modo" if _aberto(cred) else "falta"), cred, str(e)
        return "falta", cred, None
    renv = cred.parent / "runner.env"
    for k, rx in (("ANTHROPIC_API_KEY", lancador.CRED_API), ("CLAUDE_CODE_OAUTH_TOKEN", lancador.CRED_OAUTH)):
        v = lancador.segredo(k)
        if v and rx.fullmatch(v) and not (k == "ANTHROPIC_API_KEY" and v.startswith("sk-ant-oat")):
            return ("modo" if _aberto(renv) else "ok"), renv, None
    if os.path.exists(Path.home() / ".claude" / ".credentials.json"):
        return "ok", None, None
    return "falta", None, None


def estado_credencial(cfg_path: str) -> str:
    return credencial(cfg_path)[0]


def _corre(args: list[str], env: dict | None = None, timeout: float = CMD_S) -> subprocess.CompletedProcess | None:
    """Corre sem shell e sem stdin; None se o comando não existir, falhar a arrancar ou passar do timeout."""
    if env is None:   # o do runner sem os segredos (systemctl --user precisa de XDG_RUNTIME_DIR, …)
        env = {k: v for k, v in os.environ.items() if k not in lancador.SEGREDO_CHAVES}
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
                              timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def gh_sessao() -> str:
    """`gh auth status` (mesmo ambiente do `gh pr list` do runner): ok | sem sessão (também sem gh ou timeout)."""
    if shutil.which("gh") is None:
        return "sem sessão"
    r = _corre(["gh", "auth", "status"], env=lancador.env_gh(), timeout=GH_S)
    return "ok" if r is not None and r.returncode == 0 else "sem sessão"


def estado_gh(cfg, now: float) -> str:
    """O último valor, se tem menos de GH_CADA_S; senão verifica e guarda em <dados>/runner/gh-verificado.json."""
    cache = officecfg.caminho(cfg, "runner") / GH_CACHE
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if (isinstance(d, dict) and d.get("gh") in ("ok", "sem sessão") and type(d.get("em")) in (int, float)
                and 0 <= now - d["em"] < GH_CADA_S):
            return d["gh"]
    except (OSError, ValueError):
        pass
    valor = gh_sessao()
    try:
        lancador.escrever_json(cache, {"em": now, "gh": valor})
    except OSError:
        pass
    return valor


def gravar_estado(cfg, cfg_path: str, now: float | None = None) -> dict:
    """Grava <dados>/runner-estado/estado.json (tmp na mesma pasta + os.replace, 0644). Devolve o documento.
    OSError sobe: quem chama (uma_vez) engole-o, a passagem continua."""
    now = time.time() if now is None else now
    doc = {"v": 1, "em": lancador.agora(cfg, now).isoformat(timespec="seconds"), "versao": officecfg.VERSAO,
           "claude": estado_claude(), "credencial": estado_credencial(cfg_path), "gh": estado_gh(cfg, now)}
    pasta = officecfg.caminho(cfg, "runner_estado")
    pasta.mkdir(mode=0o750, parents=True, exist_ok=True)
    if not stat.S_ISDIR(os.lstat(pasta).st_mode):   # symlink (ou outra coisa): não se escreve através dele
        raise OSError("runner-estado não é uma pasta")
    tmp = pasta / f".{ESTADO}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    try:
        os.fchmod(fd, 0o644)   # o umask não decide: o Office (mesmo uid) lê-o
        os.write(fd, (json.dumps(doc, ensure_ascii=False) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, pasta / ESTADO)
    return doc


# ---------------------------------------------------------------- doctor

def _eu() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def porta_aberta(porta: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=2):
            return True
    except OSError:
        return False


def dominio(cfg_path: str) -> str | None:
    """OFFICE_DOMINIO do .env ao lado do office.yaml (escrito pelo install.sh); só essa linha é lida."""
    try:
        linhas = (Path(os.path.abspath(os.path.expanduser(cfg_path))).parent / ".env").read_text(
            encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for linha in linhas:
        m = re.fullmatch(r"OFFICE_DOMINIO='([^']*)'", linha)
        if m:
            return m.group(1) if _DOMINIO.fullmatch(m.group(1)) else None
    return None


def _ips(r: subprocess.CompletedProcess | None, getent: bool) -> list[str]:
    if r is None or r.returncode != 0:
        return []
    campos = [x.split()[0] for x in r.stdout.splitlines() if x.split()] if getent else r.stdout.split()
    return sorted(set(campos))


def _dns(d: str) -> tuple[bool, str]:
    # as mesmas frases do install.sh (sem «aviso:»); nunca usa serviços de terceiros
    resolve = _ips(_corre(["getent", "ahosts", d]), getent=True)
    meus = _ips(_corre(["hostname", "-I"]), getent=False)
    if not resolve:
        return False, f"o domínio {d} ainda não resolve → cria um registo A para o IP desta VPS"
    if set(resolve) & set(meus):
        return True, f"o domínio {d} aponta para esta VPS"
    return False, (f"o domínio {d} aponta para {' '.join(resolve)}, mas esta VPS tem {' '.join(meus) or '?'} "
                   "(se a VPS está atrás de NAT, ignora) → corrige o registo A do domínio")


def runner_env(casa: Path) -> dict[str, str]:
    """Do <casa>/runner.env (o EnvironmentFile da unit) só a credencial do claude e o GH_TOKEN/GITHUB_TOKEN, para o
    doctor ver o que o runner veria. Só um ficheiro normal do utilizador (o modo diz-o a credencial()); linhas que não
    sejam CHAVE=valor ignoradas. Os valores nunca saem daqui para a saída."""
    try:
        fd = os.open(casa / "runner.env", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return {}
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return {}
        raw = os.read(fd, lancador.SEGREDOS_MAX + 1)
    except OSError:
        return {}
    finally:
        os.close(fd)
    if len(raw) > lancador.SEGREDOS_MAX:
        return {}
    achadas: dict[str, str] = {}
    for linha in raw.decode("utf-8", "replace").splitlines():
        m = lancador._CRED_LINHA.fullmatch(linha.strip())
        if m and m.group(1) in (*lancador.CRED_CHAVES, *lancador.GH_CHAVES):
            achadas[m.group(1)] = m.group(3)
    return achadas


def _unit_path() -> str:
    """O PATH da unit instalada (Environment=PATH=…), ou "" se não houver."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    try:
        texto = (base / "systemd" / "user" / "office-runner.service").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    m = re.search(r"^Environment=PATH=(\S+)$", texto, re.MULTILINE)
    return m.group(1) if m else ""


def claude_do_runner() -> bool:
    """claude no PATH da sessão, no da unit do runner ou em ~/.local/bin (onde o instalador oficial o põe)."""
    caminhos = [os.environ.get("PATH", ""), _unit_path(), str(Path.home() / ".local" / "bin")]
    return shutil.which("claude", path=os.pathsep.join(x for x in caminhos if x)) is not None


def doctor(cfg_path: str) -> tuple[int, list[str]]:
    """(0 se tudo ✓ senão 1, linhas). Funciona com o office.yaml inválido: nenhuma verificação depende dele."""
    linhas: list[str] = []

    def linha(ok: bool, oque: str, corrige: str = "") -> None:
        linhas.append(f"✓ {oque}" if ok else f"✗ {oque} → {corrige}")

    repo, eu = lancador.RUNNER_REPO, _eu()
    casa = Path(os.path.abspath(os.path.expanduser(cfg_path))).parent
    # o runner corre com o runner.env da unit, não com o ambiente desta sessão SSH
    lancador.SEGREDOS.update(runner_env(casa))
    try:
        officecfg.carregar(cfg_path, estrito=True)
        linha(True, "office.yaml válido")
    except officecfg.ConfigErro as e:
        linha(False, f"office.yaml inválido ({e})", f"python3 {repo}/officecfg.py validar {cfg_path}")
    # Docker e o grupo docker: `docker info` só responde a quem tem acesso ao socket
    if shutil.which("docker") is None:
        linha(False, "Docker não está instalado", "https://docs.docker.com/engine/install/ubuntu/")
    else:
        r = _corre(["docker", "info", "--format", "{{.ServerVersion}}"])
        linha(r is not None and r.returncode == 0, "Docker a responder (grupo docker)",
              f"sudo usermod -aG docker {eu} (e volta a entrar); sudo systemctl start docker")
    r = _corre(["loginctl", "show-user", eu, "-p", "Linger", "--value"])
    linha(r is not None and r.stdout.strip() == "yes", "linger activo (o runner continua sem sessão aberta)",
          f"sudo loginctl enable-linger {eu}")
    r = _corre(["systemctl", "--user", "is-active", "--quiet", TIMER])
    linha(r is not None and r.returncode == 0, f"timer {TIMER} activo", f"systemctl --user enable --now {TIMER}")
    linha(claude_do_runner(), "claude no PATH", "curl -fsSL https://claude.ai/install.sh | bash")
    valor, ficheiro, motivo = credencial(cfg_path)
    if valor == "ok":
        linha(True, f"credencial do Claude ({ficheiro.name if ficheiro else 'login do claude'})")
    elif valor == "modo":
        linha(False, f"credencial do Claude com permissões abertas ({ficheiro.name})", f"chmod 600 {ficheiro}")
    else:
        linha(False, f"credencial do Claude: {motivo or 'nenhuma'}", f"cd {repo} && ./install.sh --credenciais")
    if shutil.which("gh") is None:
        linha(False, "gh não está instalado", "https://github.com/cli/cli/blob/trunk/docs/install_linux.md")
    else:
        linha(gh_sessao() == "ok", "sessão do gh (gh auth status)", "gh auth login")
    d = dominio(cfg_path)
    if d:
        ok, frase = _dns(d)
        linhas.append(f"{'✓' if ok else '✗'} {frase}")
    else:
        linha(True, "sem domínio: DNS não se aplica")
    for p in (80, 443) if d else (80,):
        linha(porta_aberta(p), f"porta {p} à escuta", f"docker compose -f {casa / 'docker-compose.yml'} up -d")
    linha(True, f"versão instalada {officecfg.VERSAO}")
    return (1 if any(x.startswith("✗") for x in linhas) else 0), linhas
