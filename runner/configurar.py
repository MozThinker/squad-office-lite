#!/usr/bin/env python3
"""Configuração do modo Docker por perguntas (stdlib, Python >= 3.10). Corre no serviço `configurar` do compose:

    docker compose run --rm configurar [--palavra-passe] [--projectos] [--credenciais] [--gh] [--sem-perguntas]
    docker compose run --rm -v ./office.yaml:/novo/office.yaml:ro configurar --office-yaml /novo/office.yaml

Pergunta, por esta ordem e só o que falta: utilizador e palavra-passe do Office, projectos, nome/e-mail dos commits,
credencial do Claude e token do gh. Com --palavra-passe, --projectos, --credenciais ou --gh refaz só essas partes.
Com --sem-perguntas lê o ambiente (para a CI): OFFICE_AUTH_USER, OFFICE_AUTH_PASS_FILE, OFFICE_PROJECTOS_IDS (nomes
das pastas em /projectos, por vírgulas; vazio = todas, ou `exemplo` se não houver nenhuma), OFFICE_GIT_NOME,
OFFICE_GIT_EMAIL, OFFICE_CLAUDE_CRED (api|oauth|nenhuma), OFFICE_CLAUDE_CRED_FILE, OFFICE_GH_TOKEN_FILE.

Arranca como root só para o chown das raízes dos volumes (lista fixa, não recursivo, sem seguir symlinks) para
OFFICE_UID:OFFICE_GID (10001); depois larga os privilégios (o modo das raízes já é dado sem root) e recusa continuar
se ainda for root. Capacidades no compose: CHOWN, DAC_OVERRIDE, SETGID e SETUID. Segredos (palavra-passe,
credencial, token) nunca em argv, no ambiente dos filhos nem na saída: entram por stdin ou ficheiros 0600 (tmp +
os.replace). Nunca corre o `claude setup-token` nem lê ~/.claude (ADR-007/008): o token é o leitor que o cola.

A casa (/home/office, volume runner-home) é escrita pelo agente: nada aqui executa config de lá. O python corre com
-I (wrapper da imagem); o git sem config global nem do sistema (o .gitconfig só por --file, recusado se for symlink),
sem hooks nem templates; o gh com GH_CONFIG_DIR fixo e o git dele só com o .gitconfig verificado.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))
import lancador

import officecfg

# caminhos do contentor (contrato do compose do modo Docker, PLAN §0); não dependem de nenhuma instalação
RAIZES = {"dados": "/dados", "projectos": "/projectos", "claude_projects": "/home/office/.claude/projects"}
CASA = Path("/home/office")
PROXY = Path("/proxy")
CONFIG_OMISSAO = "/config/office.yaml"
VOLUMES_DADOS = ("tasks", "squads", "decisoes", "biblioteca", "runner_estado", "decisoes_fila", "quadro",
                 "quadro_lancar", "push", "runner")
UID_OMISSAO = 10001
RX_UTILIZADOR = re.compile(r"[a-z][a-z0-9._-]{1,31}")   # = RX_UTILIZADOR do install.sh
ERRO_UTILIZADOR = "utilizador inválido (minúsculas, números, . _ -; 2 a 32 caracteres)"
RX_HASH = re.compile(r"\$2y\$12\$[./A-Za-z0-9]{53}")
RX_GH = re.compile(r"[A-Za-z0-9_]{20,255}")              # ghp_…, gho_…, github_pat_…
RX_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
PASS_MIN = 12
PROJECTOS_MAX = 30                                       # = _lista_de(…, "projectos", 1, 30) do officecfg
YAML_MAX = 256 * 1024
TENTATIVAS = 3
CMD_S = 120
LER_MAX = 4096
ENDERECO = "http://localhost:8080"

TEXTO_TOKEN = """O token da subscrição deixa a squad usar a tua subscrição do Claude (Pro/Max) em vez de uma chave de API.
Quem o gera és tu: abre outra janela do PowerShell ou do Terminal, na pasta do compose.yaml, e corre
    docker compose run --rm runner claude setup-token
Segue o browser e copia a linha que começa por sk-ant-oat01- (não o código do browser). Depois cola-a aqui.
Fica guardado no volume «segredos» (claude.env, só leitura para o dono), que só o runner lê.
Risco: o token vai no ambiente do claude enquanto corre uma tarefa; um agente desviado pode lê-lo e usá-lo.
Revoga-o em claude.ai se suspeitares."""

TEXTO_GH = """Token do GitHub para a squad fazer push e abrir PRs. Cria um token fine-grained só para os repositórios
do Office (ver README, «Token do GitHub»). Fica na sessão do gh, no volume da casa do runner."""


class Erro(Exception):
    """Configuração impossível; a mensagem nunca leva segredos."""


def info(msg: str) -> None:
    print(msg, flush=True)


def aviso(msg: str) -> None:
    print(f"aviso: {msg}", file=sys.stderr, flush=True)


def _ler(texto: str) -> str:
    return input(texto)


def _ler_segredo(texto: str) -> str:
    return getpass.getpass(texto)


def perguntar(texto: str, omissao: str = "") -> str:
    r = _ler(f"{texto}{f' [{omissao}]' if omissao else ''}: ").strip()
    return r or omissao


def ler_ficheiro(var: str) -> str:
    """Primeira linha (sem \\r\\n) do ficheiro em $var, para o --sem-perguntas. Erro se faltar ou estiver vazio."""
    caminho = os.environ.get(var, "")
    if not caminho:
        raise Erro(f"--sem-perguntas precisa de {var}")
    try:
        with open(caminho, "rb") as f:
            raw = f.read(LER_MAX)
    except OSError:
        raise Erro(f"{var} não se lê") from None
    valor = raw.decode("utf-8", "replace").splitlines()[0].strip("\r") if raw else ""
    if not valor:
        raise Erro(f"{var} está vazio")
    return valor


def escrever_privado(path: Path, texto: str) -> None:
    """tmp 0600 (O_EXCL|O_NOFOLLOW) na mesma pasta + fsync + os.replace: quem lê nunca vê meio ficheiro."""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, texto.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def env_git_isolado() -> dict:
    """A lista branca do runner sem config global nem do sistema: o ~/.gitconfig é do agente (hooksPath, …)."""
    return dict(lancador.env_git(), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


GIT = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "safe.directory=*"]   # sem hooks; sem «dubious ownership»


def _corre(args: list[str], entrada: str | None = None, env: dict | None = None,
           cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Sem shell; o segredo, se houver, só pelo stdin. Ambiente: a lista branca do runner (sem GH_TOKEN, …), sem
    config do git."""
    try:
        return subprocess.run(args, input=entrada, capture_output=True, text=True, check=False, timeout=CMD_S,
                              env=env if env is not None else env_git_isolado(), cwd=cwd,
                              stdin=None if entrada is not None else subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(args, 127, "", "")


# ---------------------------------------------------------------- root: chown das raízes e largar privilégios

def alvo() -> tuple[int, int]:
    """(uid, gid) de OFFICE_UID/OFFICE_GID (omissão 10001). Nunca 0."""
    out = []
    for var in ("OFFICE_UID", "OFFICE_GID"):
        v = os.environ.get(var, "") or str(UID_OMISSAO)
        if not v.isdigit() or not 0 < int(v) < 2**31:
            raise Erro(f"{var} inválido (um número > 0)")
        out.append(int(v))
    return out[0], out[1]


def caminhos_chown(config: Path) -> list[Path]:
    """Raízes dos volumes, por esta ordem: a lista fixa do compose (nada recursivo)."""
    dados = Path(RAIZES["dados"])
    return ([dados / officecfg.SUB[k] for k in VOLUMES_DADOS]
            + [CASA, CASA / ".claude", Path(RAIZES["claude_projects"]), config.parent,
               officecfg.credenciais_claude(config).parent, PROXY])


def _abrir_pasta(path: Path) -> int:
    """fd de `path` aberta componente a componente com O_NOFOLLOW: um symlink em qualquer ponto dá OSError."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for parte in path.parts[1:]:
            novo = os.open(parte, flags, dir_fd=fd)
            os.close(fd)
            fd = novo
    except OSError:
        os.close(fd)
        raise
    return fd


def preparar_volumes(config: Path, uid: int, gid: int) -> list[Path]:
    """chown uid:gid de cada raiz (sem seguir symlinks; as que faltam ou não são pastas ficam) e da pasta de
    projectos só se for de root (criada pelo Docker). Devolve as que mudaram."""
    feitos = []
    projectos = Path(RAIZES["projectos"])
    for p in [*caminhos_chown(config), projectos]:
        try:
            fd = _abrir_pasta(p)
        except FileNotFoundError:
            continue
        except OSError:
            aviso(f"{p} não é uma pasta (ou é um symlink): não lhe mudo o dono")
            continue
        try:
            st = os.fstat(fd)
            if p == projectos and (st.st_uid != 0 or os.listdir(fd)):
                if st.st_uid == 0:   # pasta do host com conteúdo (ex.: /srv): nunca lhe mudar o dono
                    aviso(f"{p} é de root e não está vazia: não lhe mudo o dono")
                continue
            if (st.st_uid, st.st_gid) != (uid, gid):
                try:
                    os.fchown(fd, uid, gid)
                except OSError:   # bind do Docker Desktop (Windows/macOS) pode recusar: segue
                    aviso(f"não consegui mudar o dono de {p}: sigo sem isso")
                    continue
                feitos.append(p)
        finally:
            os.close(fd)
    return feitos


def modos_volumes(config: Path) -> None:
    """Já sem root: modo das raízes (0700 nas privadas, 0750 nas outras, como no VPS), sem seguir symlinks. Com
    nocopy, a raiz de um volume novo nasce 0755 (de root, até ao chown acima); as que não são nossas ficam."""
    privadas = {PROXY, officecfg.credenciais_claude(config).parent, Path(RAIZES["dados"]) / officecfg.SUB["push"],
                CASA, CASA / ".claude"}
    for p in caminhos_chown(config):
        try:
            fd = _abrir_pasta(p)
        except OSError:
            continue
        try:
            os.fchmod(fd, 0o700 if p in privadas else 0o750)
        except OSError:
            pass
        finally:
            os.close(fd)


def largar_privilegios(uid: int, gid: int) -> None:
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    if os.geteuid() == 0 or os.getuid() == 0:
        raise Erro("continuo a ser root depois de largar os privilégios: recuso continuar")


# ---------------------------------------------------------------- 1. utilizador e palavra-passe do Office

def gerar_hash(utilizador: str, senha: str) -> str:
    """bcrypt $2y$12$ pelo htpasswd, com a palavra-passe só no stdin."""
    r = _corre(["htpasswd", "-niBC", "12", utilizador], entrada=senha + "\n", env=lancador.env_base())
    linha = (r.stdout.splitlines() or [""])[0] if r.returncode == 0 else ""
    h = linha[len(utilizador) + 1:] if linha.startswith(f"{utilizador}:") else ""
    if not RX_HASH.fullmatch(h):
        raise Erro("não consegui gerar o hash bcrypt da palavra-passe (htpasswd)")
    return h


def pedir_auth(sem_perguntas: bool) -> tuple[str, str]:
    if sem_perguntas:
        utilizador = os.environ.get("OFFICE_AUTH_USER", "")
        senha = ler_ficheiro("OFFICE_AUTH_PASS_FILE")
    else:
        for t in range(1, TENTATIVAS + 1):
            utilizador = perguntar("Utilizador do Office", "office")
            if RX_UTILIZADOR.fullmatch(utilizador):
                break
            if t == TENTATIVAS:
                raise Erro(ERRO_UTILIZADOR)
            aviso(f"{ERRO_UTILIZADOR}: tenta de novo")
        senha = ""
        for t in range(1, TENTATIVAS + 1):
            senha = _ler_segredo(f"Palavra-passe ({PASS_MIN} ou mais caracteres; não aparece no ecrã): ")
            repetida = _ler_segredo("Repete a palavra-passe: ")
            if len(senha) < PASS_MIN:
                problema = f"a palavra-passe tem de ter pelo menos {PASS_MIN} caracteres"
            elif senha != repetida:
                problema = "as palavras-passe não coincidem"
            else:
                break
            if t == TENTATIVAS:
                raise Erro(problema)
            aviso(f"{problema}: tenta de novo")
    if not RX_UTILIZADOR.fullmatch(utilizador):
        raise Erro(ERRO_UTILIZADOR)
    if len(senha) < PASS_MIN:
        raise Erro(f"a palavra-passe tem de ter pelo menos {PASS_MIN} caracteres")
    return utilizador, senha


def passo_auth(forcar: bool, sem_perguntas: bool) -> bool:
    """Utilizador, hash e (se faltar) o segredo do proxy no volume proxy. True se algo mudou."""
    mudou = False
    if forcar or not all(os.path.lexists(PROXY / n) for n in ("utilizador", "hash")):
        utilizador, senha = pedir_auth(sem_perguntas)
        h = gerar_hash(utilizador, senha)
        del senha
        escrever_privado(PROXY / "utilizador", utilizador)
        escrever_privado(PROXY / "hash", h)
        info(f"Utilizador «{utilizador}» e palavra-passe do Office gravados.")
        mudou = True
    if not os.path.lexists(PROXY / "segredo"):
        escrever_privado(PROXY / "segredo", secrets.token_hex(32))
        info("Segredo entre o Caddy e o Office gerado.")
        mudou = True
    return mudou


# ---------------------------------------------------------------- 2. projectos e office.yaml

def listar_repos() -> list[str]:
    """Pastas de /projectos com .git e nome aceite pelo office.yaml (repo), por ordem alfabética."""
    raiz = Path(RAIZES["projectos"])
    try:
        nomes = sorted(e.name for e in os.scandir(raiz) if e.is_dir(follow_symlinks=False))
    except OSError:
        return []
    return [n for n in nomes if not n.startswith(".") and officecfg._REPO.fullmatch(n)
            and os.path.lexists(raiz / n / ".git")]


def criar_exemplo() -> None:
    """Repositório de exemplo em /projectos/exemplo (como o install.sh)."""
    r = Path(RAIZES["projectos"]) / "exemplo"
    r.mkdir(mode=0o750)
    if _corre([*GIT, "init", "-q", "--template=", "-b", "main"], cwd=r).returncode != 0:
        raise Erro("não consegui criar o repositório de exemplo")
    (r / "README.md").write_text(
        "# Exemplo\n\nProjecto de exemplo do Squad Office Lite: lança aqui a primeira tarefa.\n", encoding="utf-8")
    (r / ".git" / "info").mkdir(exist_ok=True)   # --template= não a cria
    with open(r / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
        f.write(".worktrees/\n")
    for cmd in ([*GIT, "add", "README.md"],
                [*GIT, "-c", "user.name=Squad Office", "-c", "user.email=office@localhost", "commit", "-q", "-m",
                 "Primeiro commit"]):
        if _corre(cmd, cwd=r).returncode != 0:
            raise Erro("não consegui criar o repositório de exemplo")
    info("Criado o repositório de exemplo (exemplo).")


def _numeros(texto: str, n: int) -> list[int] | None:
    partes = [x for x in re.split(r"[\s,]+", texto.strip()) if x]
    if not partes:
        return list(range(n))
    if not all(x.isdigit() and 1 <= int(x) <= n for x in partes):
        return None
    return list(dict.fromkeys(int(x) - 1 for x in partes))


def escolher_projectos(sem_perguntas: bool) -> list[str]:
    repos = listar_repos()
    if sem_perguntas:
        pedidos = [x.strip() for x in os.environ.get("OFFICE_PROJECTOS_IDS", "").split(",") if x.strip()]
        if not pedidos:
            if not repos:
                criar_exemplo()
                return ["exemplo"]
            if len(repos) > PROJECTOS_MAX:
                raise Erro(f"há {len(repos)} repositórios na pasta de projectos e o office.yaml aceita no máximo "
                           f"{PROJECTOS_MAX}: escolhe-os com OFFICE_PROJECTOS_IDS")
            return repos
        if len(set(pedidos)) > PROJECTOS_MAX:
            raise Erro(f"OFFICE_PROJECTOS_IDS: no máximo {PROJECTOS_MAX} projectos")
        for x in pedidos:
            if x not in repos:
                if x != "exemplo" or os.path.lexists(Path(RAIZES["projectos"]) / x):
                    raise Erro(f"OFFICE_PROJECTOS_IDS: «{x}» não é um repositório git na pasta de projectos")
                criar_exemplo()
                repos.append(x)
        return list(dict.fromkeys(pedidos))
    if not repos:
        info("Não há repositórios git na pasta de projectos (OFFICE_PROJECTOS no PC; ./projectos por omissão).")
        if perguntar("Crio o projecto de exemplo? (s/n)", "s").lower() not in ("s", "sim"):
            raise Erro("sem projectos: põe um repositório git na pasta de projectos e corre de novo")
        criar_exemplo()
        return ["exemplo"]
    info("Repositórios na pasta de projectos:")
    for i, n in enumerate(repos, 1):
        info(f"  {i}) {n}")
    todos = "Enter = todos" if len(repos) <= PROJECTOS_MAX else f"no máximo {PROJECTOS_MAX}"
    for t in range(1, TENTATIVAS + 1):
        escolha = _numeros(_ler(f"Escolhe por número, separados por vírgulas ({todos}): "), len(repos))
        if escolha and len(escolha) <= PROJECTOS_MAX:
            return [repos[i] for i in escolha]
        if t == TENTATIVAS:
            raise Erro(f"escolha inválida: números de 1 a {len(repos)}, no máximo {PROJECTOS_MAX}")
        aviso("escolha inválida: tenta de novo")
    return []   # inalcançável


def _id(nome: str, usados: set[str]) -> str:
    """id do office.yaml (minúsculas, dígitos e -, 2 a 24, começa por letra) a partir do nome da pasta."""
    base = re.sub(r"[^a-z0-9-]+", "-", nome.lower()).strip("-")
    if not base[:1].isalpha():
        base = "p" + base
    base = base[:24].rstrip("-")
    if len(base) < 2 or base == "outro":
        base = (base + "-p")[:24]
    pid, i = base, 2
    while pid in usados:
        pid = f"{base[:24 - len(str(i)) - 1]}-{i}"
        i += 1
    usados.add(pid)
    return pid


def bloco_projectos(repos: list[str]) -> str:
    usados: set[str] = set()
    linhas = ["projectos:"]
    for r in repos:
        linhas += [f"  - id: {_id(r, usados)}", f'    nome: "{r[:40]}"', f'    repo: "{r}"']
    return "\n".join(linhas) + "\n"


def trocar_bloco(texto: str, bloco: str) -> str:
    """Troca só o bloco `projectos:` (até à próxima linha não indentada) de um office.yaml."""
    linhas = texto.splitlines(keepends=True)
    ini = next((i for i, x in enumerate(linhas) if re.fullmatch(r"projectos:\s*(#.*)?", x.rstrip("\r\n"))), None)
    if ini is None:
        raise Erro("o office.yaml não tem o bloco projectos:")
    fim = ini + 1
    while fim < len(linhas) and (not linhas[fim].strip() or linhas[fim][0] in " \t"):
        fim += 1
    while fim > ini + 1 and not linhas[fim - 1].strip():
        fim -= 1
    return "".join(linhas[:ini]) + bloco + "".join(linhas[fim:])


def office_yaml_novo(utilizador: str, bloco: str) -> str:
    texto = officecfg.EXEMPLO.read_text(encoding="utf-8")
    trocas = [("  nome: Utilizador\n", f'  nome: "{utilizador}"\n')]
    trocas += [(f"  {k}: {v}\n", f"  {k}: {RAIZES[k]}\n") for k, v in
               (("dados", "~/office-lite/dados"), ("projectos", "~/projectos"),
                ("claude_projects", "~/.claude/projects"))]
    for antes, depois in trocas:
        if texto.count(antes) != 1:
            raise Erro("office.example.yaml inesperado")
        texto = texto.replace(antes, depois)
    return trocar_bloco(texto, bloco)


def gravar_config(config: Path, texto: str, oque: str = "office.yaml gerado") -> dict:
    """Valida (modo estrito) num tmp 0600 e só então o põe no lugar."""
    tmp = config.with_name(f".{config.name}.tmp")
    escrever_privado(tmp, texto)
    try:
        cfg, _ = officecfg.carregar(tmp, estrito=True)
    except officecfg.ConfigErro as e:
        tmp.unlink(missing_ok=True)
        raise Erro(f"{oque} inválido ({e}); o antigo fica") from None
    os.replace(tmp, config)
    return cfg


def passo_config(config: Path, forcar: bool, sem_perguntas: bool) -> bool:
    """office.yaml (só se faltar; --projectos troca só o bloco projectos:) e as pastas dele. True se mudou."""
    existe = os.path.lexists(config)
    cfg = None
    if existe:
        try:
            cfg, _ = officecfg.carregar(config, estrito=True)
        except officecfg.ConfigErro as e:
            raise Erro(f"o office.yaml existente é inválido ({e}): corrige-o (README, «No teu PC») e corre de novo") \
                from None
    mudou = False
    if forcar or not existe:
        bloco = bloco_projectos(escolher_projectos(sem_perguntas))
        if existe:
            texto = trocar_bloco(config.read_text(encoding="utf-8"), bloco)
        else:
            try:
                utilizador = (PROXY / "utilizador").read_text(encoding="utf-8").strip()
            except OSError:
                utilizador = ""
            texto = office_yaml_novo(utilizador if RX_UTILIZADOR.fullmatch(utilizador) else "Utilizador", bloco)
        cfg = gravar_config(config, texto)
        info("office.yaml gravado (" + ", ".join(p["id"] for p in cfg["projectos"]) + ").")
        mudou = True
    for modo, p in officecfg.pastas(cfg):
        p.mkdir(mode=int(modo, 8), parents=True, exist_ok=True)
    return mudou


# ---------------------------------------------------------------- 3. git

def gitconfig() -> Path:
    """~/.gitconfig da casa do runner; recusa se for symlink (o git escreveria no alvo)."""
    f = CASA / ".gitconfig"
    if os.path.islink(f):
        raise Erro(f"{f} é um symlink: recuso escrever nele (apaga-o e corre de novo)")
    return f


def _git_global(*args: str) -> subprocess.CompletedProcess:
    # --file em vez de --global: o ambiente não tem config global (env_git_isolado)
    return _corre(["git", "config", "--file", str(gitconfig()), *args])


def passo_git(sem_perguntas: bool) -> None:
    """user.name/user.email só se faltarem (argv com --, nunca por shell) e safe.directory da pasta de projectos."""
    for chave, var, oque, valido, regra in (
            ("user.name", "OFFICE_GIT_NOME", "Nome para os commits da squad (git user.name)",
             lambda v: 1 <= len(v) <= 100 and v.isprintable(), "nome inválido (1 a 100 caracteres, sem controlo)"),
            ("user.email", "OFFICE_GIT_EMAIL", "E-mail para os commits da squad (git user.email)",
             lambda v: len(v) <= 254 and v.isprintable() and RX_EMAIL.fullmatch(v) is not None,
             "e-mail inválido (um @, sem espaços, até 254 caracteres)")):
        if _git_global("--get", chave).stdout.strip():
            continue
        if sem_perguntas:
            v = os.environ.get(var, "")
            if not v:
                aviso(f"a squad não consegue fazer commits: falta o {chave} do git (define {var})")
                continue
            if not valido(v):
                raise Erro(f"{var}: {regra}")
        else:
            for t in range(1, TENTATIVAS + 1):
                v = perguntar(oque)
                if valido(v):
                    break
                if t == TENTATIVAS:
                    raise Erro(regra)
                aviso(f"{regra}: tenta de novo")
        if _git_global("--", chave, v).returncode != 0:
            raise Erro(f"não consegui gravar o {chave} do git")
    seguro = RAIZES["projectos"].rstrip("/") + "/*"
    if (seguro not in _git_global("--get-all", "safe.directory").stdout.splitlines()
            and _git_global("--add", "safe.directory", seguro).returncode != 0):
        raise Erro("não consegui gravar o safe.directory do git")


# ---------------------------------------------------------------- 4. credencial do Claude

def valida_credencial(via: str, valor: str) -> str | None:
    """None se válido; senão a frase do problema (nunca o valor)."""
    if via == "oauth":
        return None if lancador.CRED_OAUTH.fullmatch(valor) else \
            "copia a linha que começa por sk-ant-oat01-, não o código do browser"
    if valor.startswith("sk-ant-oat"):
        return "isso é um token da subscrição: escolhe a opção 2"
    return None if lancador.CRED_API.fullmatch(valor) else \
        "a chave de API tem de começar por sk-ant- (só letras, números, _ e -)"


def passo_credencial(config: Path, forcar: bool, sem_perguntas: bool) -> str:
    """claude.env na pasta dos segredos. Devolve "ok" (já havia ou gravada) ou "falta"."""
    cred = officecfg.credenciais_claude(config)
    pedido = forcar or (sem_perguntas and bool(os.environ.get("OFFICE_CLAUDE_CRED")))
    if os.path.lexists(cred) and not pedido:
        return "ok"
    if sem_perguntas:
        via = os.environ.get("OFFICE_CLAUDE_CRED", "") or "nenhuma"
        if via not in ("api", "oauth", "nenhuma"):
            raise Erro("OFFICE_CLAUDE_CRED tem de ser api, oauth ou nenhuma")
    else:
        info("Credencial do Claude para a squad (fica no volume «segredos», 0600; só o runner a lê):")
        info("  1) chave de API (recomendada; paga por uso)")
        info("  2) token da subscrição (geras tu com o claude setup-token e colas aqui)")
        info("  3) agora não")
        via = {"1": "api", "2": "oauth", "3": "nenhuma"}.get(perguntar("Escolhe", "1"))
        if via is None:
            raise Erro("opção inválida: escolhe 1, 2 ou 3")
    if via == "nenhuma":
        return "ok" if os.path.lexists(cred) else "falta"
    if via == "oauth" and not sem_perguntas:
        info(TEXTO_TOKEN)
    chave = "CLAUDE_CODE_OAUTH_TOKEN" if via == "oauth" else "ANTHROPIC_API_KEY"
    texto = "Token da subscrição (sk-ant-oat01-…" if via == "oauth" else "Chave de API (sk-ant-…"
    for t in range(1, TENTATIVAS + 1):
        valor = (ler_ficheiro("OFFICE_CLAUDE_CRED_FILE") if sem_perguntas
                 else _ler_segredo(f"{texto}; não aparece no ecrã): ")).strip()
        problema = valida_credencial(via, valor) if valor else "credencial vazia"
        if problema is None:
            break
        if sem_perguntas or t == TENTATIVAS:
            raise Erro(f"credencial do Claude inválida: {problema}")
        aviso(f"{problema}: tenta de novo")
    escrever_privado(cred, f"{chave}={valor}\n")
    del valor
    info(f"Credencial do Claude gravada em {cred.name} (volume «segredos»; só o runner a lê).")
    return "ok"


# ---------------------------------------------------------------- 5. gh

def env_gh() -> dict:
    """GH_CONFIG_DIR fixo e o git do `gh auth setup-git` só com o ~/.gitconfig verificado (nunca symlinks)."""
    for p in (CASA / ".config", CASA / ".config" / "gh", CASA / ".config" / "gh" / "hosts.yml",
              CASA / ".config" / "gh" / "config.yml"):
        if os.path.islink(p):
            raise Erro(f"{p} é um symlink: recuso usar a sessão do gh (apaga-o e corre de novo com --gh)")
    return dict(lancador.env_base(), GH_CONFIG_DIR=str(CASA / ".config" / "gh"), GIT_CONFIG_NOSYSTEM="1",
                GIT_CONFIG_GLOBAL=str(gitconfig()), GIT_TERMINAL_PROMPT="0")


def gh_ok() -> bool:
    return shutil.which("gh") is not None and _corre(["gh", "auth", "status"], env=env_gh()).returncode == 0


def passo_gh(forcar: bool, sem_perguntas: bool) -> str:
    """Sessão do gh: token por stdin ao `gh auth login --with-token`, depois `gh auth setup-git`. "ok" ou "falta"."""
    if shutil.which("gh") is None:
        aviso("o gh não está nesta imagem: actualiza com docker compose pull")
        return "falta"
    env = env_gh()
    pedido = forcar or (sem_perguntas and bool(os.environ.get("OFFICE_GH_TOKEN_FILE")))
    if not pedido and gh_ok():
        return "ok"
    if sem_perguntas:
        if not os.environ.get("OFFICE_GH_TOKEN_FILE"):
            aviso("sem OFFICE_GH_TOKEN_FILE: o gh fica sem sessão (as tarefas acabam sem PR)")
            return "falta"
        token = ler_ficheiro("OFFICE_GH_TOKEN_FILE")
        if not RX_GH.fullmatch(token):
            raise Erro("OFFICE_GH_TOKEN_FILE: não parece um token do GitHub")
    else:
        info(TEXTO_GH)
        for t in range(1, TENTATIVAS + 1):
            token = _ler_segredo("Token do GitHub (não aparece no ecrã; Enter = agora não): ").strip()
            if not token:
                return "falta"
            if RX_GH.fullmatch(token):
                break
            if t == TENTATIVAS:
                raise Erro("isso não parece um token do GitHub (ghp_…, github_pat_…)")
            aviso("isso não parece um token do GitHub: tenta de novo")
    r = _corre(["gh", "auth", "login", "--hostname", "github.com", "--with-token"], entrada=token + "\n", env=env)
    del token
    if r.returncode != 0:
        raise Erro("o gh recusou o token (gh auth login): confirma-o e corre de novo com --gh")
    if _corre(["gh", "auth", "setup-git"], env=env).returncode != 0:
        aviso("gh auth setup-git falhou: o git pode não conseguir fazer push")
    info("Sessão do gh gravada.")
    return "ok"


# ---------------------------------------------------------------- --office-yaml (já sem privilégios)

def ler_office_yaml(origem: str) -> str:
    """Texto do office.yaml do leitor: sem seguir symlinks, só ficheiro regular, até YAML_MAX, UTF-8."""
    try:
        fd = os.open(origem, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise Erro(f"não consigo ler {origem} (não existe, é um symlink ou não tem leitura para o uid do "
                   "contentor; no Linux: chmod 644)") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Erro(f"{origem} não é um ficheiro")
        raw = b""
        while len(raw) <= YAML_MAX:
            bloco = os.read(fd, YAML_MAX + 1 - len(raw))
            if not bloco:
                break
            raw += bloco
    finally:
        os.close(fd)
    if len(raw) > YAML_MAX:
        raise Erro(f"{origem} tem mais de {YAML_MAX // 1024} KB")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Erro(f"{origem} não está em UTF-8 (grava-o em texto simples UTF-8)") from None


def passo_office_yaml(config: Path, origem: str) -> None:
    """Valida (modo estrito) e põe no lugar por os.replace; inválido ⇒ erro e o antigo fica."""
    cfg = gravar_config(config, ler_office_yaml(origem), origem)
    for modo, p in officecfg.pastas(cfg):
        p.mkdir(mode=int(modo, 8), parents=True, exist_ok=True)
    info("office.yaml validado e gravado. Aplica-o com: docker compose restart office")


# ---------------------------------------------------------------- principal

def proximos_passos(cred: str | None, gh: str | None) -> None:
    info("")
    info("Próximos passos:")
    info("  docker compose up -d                  (primeira vez)")
    info("  docker compose restart office caddy   (se já estava a correr e mudaste a palavra-passe ou os projectos)")
    info(f"  abre {ENDERECO} (ou a porta que puseste em OFFICE_PORTA)")
    if cred == "falta":
        info("  falta a credencial do Claude: docker compose run --rm configurar --credenciais")
    if gh == "falta":
        info("  falta a sessão do gh (sem ela não há PRs): docker compose run --rm configurar --gh")
    info("  diagnóstico: docker compose exec runner doctor")


def configurar(a: argparse.Namespace) -> int:
    config = Path(os.environ.get("OFFICE_CONFIG") or CONFIG_OMISSAO)
    uid, gid = alvo()
    if os.geteuid() == 0:
        for p in preparar_volumes(config, uid, gid):
            info(f"dono de {p} → {uid}:{gid}")
        largar_privilegios(uid, gid)
    if os.geteuid() == 0 or os.getuid() == 0:
        raise Erro("recuso correr como root")
    if a.office_yaml:   # só isto, e só depois de largar os privilégios
        passo_office_yaml(config, a.office_yaml)
        return 0
    modos_volumes(config)
    parcial = a.palavra_passe or a.projectos or a.credenciais or a.gh
    cred = gh = None
    if not parcial or a.palavra_passe:
        passo_auth(a.palavra_passe, a.sem_perguntas)
    if not parcial or a.projectos:
        passo_config(config, a.projectos, a.sem_perguntas)
    if not parcial:
        passo_git(a.sem_perguntas)
    if not parcial or a.credenciais:
        cred = passo_credencial(config, a.credenciais, a.sem_perguntas)
    if not parcial or a.gh:
        gh = passo_gh(a.gh, a.sem_perguntas)
    proximos_passos(cred, gh)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="configurar", description="Configuração do Squad Office Lite (modo Docker).")
    ap.add_argument("--palavra-passe", action="store_true", help="troca o utilizador e a palavra-passe do Office")
    ap.add_argument("--projectos", action="store_true", help="escolhe de novo os projectos (só o bloco projectos:)")
    ap.add_argument("--credenciais", action="store_true", help="troca a credencial do Claude")
    ap.add_argument("--gh", action="store_true", help="troca o token do gh")
    ap.add_argument("--sem-perguntas", action="store_true", help="lê tudo do ambiente (OFFICE_AUTH_USER, …)")
    ap.add_argument("--office-yaml", metavar="FICHEIRO",
                    help="valida o FICHEIRO e põe-no no lugar do office.yaml (só isto; o antigo fica se for inválido)")
    a = ap.parse_args(argv)
    if a.office_yaml and (a.palavra_passe or a.projectos or a.credenciais or a.gh or a.sem_perguntas):
        ap.error("--office-yaml não se junta às outras opções")
    try:
        return configurar(a)
    except Erro as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    except OSError as e:   # sem segredos: só o erro do sistema e o caminho
        print(f"erro: {e.strerror or e}{f' ({e.filename})' if e.filename else ''}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\ncancelado", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
