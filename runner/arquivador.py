"""Arquivo da Biblioteca no runner (ADR-011, ADR-012): fila `<dados>/arquivo-fila/`, arquivo automático e rotação.

Arquivar = mover o item (documento ou lote de uma fonte `biblioteca:`) para `<fonte>/.arquivo/<mesmo caminho>`, tudo
ou nada, nunca sobrescrever, nunca apagar; repor = o inverso. A fila é escrita pelo contentor do Office (hostil): o
pedido é revalidado contra a config, os caminhos descem por fds `O_NOFOLLOW` (a raiz da fonte pode ser symlink, abaixo
dela não) e os renames são relativos a esses fds. Regras de caminho, contrato da fila e REGISTO vêm do `arquivo.py`.
Fonte num repo git cujo topo é `<dados>/biblioteca` ou está dentro: os seguidos vão num commit próprio (caminhos por
stdin, hooks desligados) e o push só acontece no ramo do origin/HEAD com 0 atrás e 1 à frente.
Stdlib só, Python >= 3.10; `now` injectado, sem relógio interno além da omissão.
"""
from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(1, str(Path(__file__).resolve().parent))
import decisoes
import lancador
import seguro

import arquivo
import officecfg

_DIRFD = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC           # raiz da fonte: pode ser symlink
_DIRNF = _DIRFD | os.O_NOFOLLOW                                 # tudo abaixo dela: nunca
PEDIDO_APROVACAO_MAX = 1024 * 1024                              # = aprovacoes.PEDIDO_MAX
EMAIL = "office-lite@localhost"
MOTIVO = {"arquivar": "pedido no Office", "repor": "reposto a pedido do Office"}


class _Erro(Exception):
    """Motivo de ERROS (lista fechada) que acaba o tratamento de um item."""


# ---------------------------------------------------------------- caminhos por fds

def _motivo(dfd: int, nome: str, inexistente: str) -> str:
    try:
        st = os.lstat(nome, dir_fd=dfd)
    except OSError:
        return inexistente
    return "symlink no caminho" if stat.S_ISLNK(st.st_mode) else inexistente


def _descer(dfd: int, partes, criar: bool = False, inexistente: str = "item inexistente") -> int:
    """fd de dfd/partes… sem seguir symlinks (criar: faz as pastas que faltam). _Erro se não der."""
    fd = os.dup(dfd)
    try:
        for c in partes:
            if criar:
                try:
                    os.mkdir(c, 0o750, dir_fd=fd)
                except FileExistsError:
                    pass
                except OSError:
                    raise _Erro("erro a mover") from None
            try:
                novo = os.open(c, _DIRNF, dir_fd=fd)
            except OSError:
                raise _Erro(_motivo(fd, c, inexistente)) from None
            os.close(fd)
            fd = novo
        return fd
    except BaseException:
        os.close(fd)
        raise


def _base(root: Path, no_arquivo: bool, inexistente: str) -> int:
    try:
        rfd = os.open(root, _DIRFD)
    except OSError:
        raise _Erro(inexistente) from None
    if not no_arquivo:
        return rfd
    try:
        return _descer(rfd, [arquivo.DIR], inexistente=inexistente)
    finally:
        os.close(rfd)


def _regulares(dfd: int, nome: str, prefixo: str) -> list[tuple[str, float]]:
    """(caminho, mtime) dos ficheiros regulares dentro da pasta `nome` de dfd, sem seguir symlinks."""
    out = []
    for raiz, _, ficheiros, rfd in os.fwalk(nome, dir_fd=dfd, follow_symlinks=False):
        for f in ficheiros:
            try:
                st = os.lstat(f, dir_fd=rfd)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                out.append((f"{prefixo}/{raiz}/{f}".replace("//", "/").lstrip("/"), st.st_mtime))
    return sorted(out)


def _resolver(root: Path, chave: str, media: bool, no_arquivo: bool):
    """(género, caminhos a mover, [(ficheiro, mtime)]) relativos à fonte; _Erro com o motivo."""
    inexistente = "não está no arquivo" if no_arquivo else "item inexistente"
    if not arquivo.validar_chave(chave):
        raise _Erro("não arquivável")
    partes = chave.split("/")
    bfd = _base(root, no_arquivo, inexistente)
    try:
        pfd = _descer(bfd, partes[:-1], inexistente=inexistente)
    finally:
        os.close(bfd)
    try:
        nome = partes[-1]
        try:
            st = os.lstat(nome, dir_fd=pfd)
        except OSError:
            raise _Erro(inexistente) from None
        if stat.S_ISLNK(st.st_mode):
            raise _Erro("symlink no caminho")
        if media and partes[0] == "media" and len(partes) >= 2 and (len(partes) > 2 or stat.S_ISDIR(st.st_mode)):
            if len(partes) > 2:
                raise _Erro("não arquivável")      # dentro de um lote: só o lote inteiro
            fs = _regulares(pfd, nome, "media")
            if not any(arquivo.classificar(tuple(f.split("/")), media) for f, _ in fs):
                raise _Erro(inexistente)           # pasta sem nada que a Biblioteca mostre não é um lote
            return "lote", [chave], fs
        tipo = arquivo.classificar(partes, media)
        if tipo not in ("doc", "pdf") or not stat.S_ISREG(st.st_mode):
            raise _Erro("não arquivável")
        # documento = os ficheiros da mesma pasta com a mesma raiz do nome (x.md, x.copy.md, x.md.revisao.md…)
        pasta, rn, fs = partes[:-1], arquivo.raiz_nome(nome), []
        for n in sorted(os.listdir(pfd)):
            if arquivo.classificar((*pasta, n), media) not in ("doc", "pdf") or arquivo.raiz_nome(n) != rn:
                continue
            try:
                s = os.lstat(n, dir_fd=pfd)
            except OSError:
                continue
            if stat.S_ISREG(s.st_mode):
                fs.append(("/".join((*pasta, n)), s.st_mtime))
        return "documento", [f for f, _ in fs], fs
    finally:
        os.close(pfd)


def resolver_item(root: Path, chave: str, media: bool) -> tuple[str, list[str]] | str:
    """(género, caminhos relativos a mover) do item `chave` na fonte, ou um erro de ERROS."""
    try:
        genero, caminhos, _ = _resolver(Path(root), chave, media, False)
    except _Erro as e:
        return str(e)
    return genero, caminhos


def mover(root: Path, caminhos: list[str], para_arquivo: bool) -> str | None:
    """Move os caminhos entre a fonte e a `.arquivo/` (tudo ou nada); None ou um erro de ERROS."""
    abertos = []
    try:
        try:
            rfd = os.open(root, _DIRFD)
        except OSError:
            return "item inexistente"
        abertos.append(rfd)
        afd = _descer(rfd, [arquivo.DIR], criar=para_arquivo, inexistente="não está no arquivo")
        abertos.append(afd)
        src, dst = (rfd, afd) if para_arquivo else (afd, rfd)
        pares = []
        for c in caminhos:
            partes = c.split("/")
            s = _descer(src, partes[:-1])
            abertos.append(s)
            d = _descer(dst, partes[:-1], criar=True)
            abertos.append(d)
            if _existe(d, partes[-1]):
                return "destino já existe"
            pares.append((s, d, partes[-1]))
        feitos = []
        for s, d, n in pares:
            try:
                os.rename(n, n, src_dir_fd=s, dst_dir_fd=d)
            except OSError as e:
                for s2, d2, n2 in reversed(feitos):   # desfaz os já feitos: tudo ou nada
                    try:
                        os.rename(n2, n2, src_dir_fd=d2, dst_dir_fd=s2)
                    except OSError:
                        pass
                return "sistemas de ficheiros diferentes" if e.errno == errno.EXDEV else "erro a mover"
            feitos.append((s, d, n))
        return None
    except _Erro as e:
        return str(e)
    finally:
        for fd in abertos:
            os.close(fd)


def _existe(dfd: int, nome: str) -> bool:
    try:
        os.lstat(nome, dir_fd=dfd)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


# ---------------------------------------------------------------- REGISTO.tsv

def _registar(root: Path, linhas: list[str]) -> None:
    """Acrescenta as linhas ao <fonte>/.arquivo/REGISTO.tsv (cabeçalho se novo); por fds, sem seguir symlinks."""
    rfd = os.open(root, _DIRFD)
    try:
        afd = _descer(rfd, [arquivo.DIR], criar=True)
    finally:
        os.close(rfd)
    try:
        fd = os.open(arquivo.REGISTO, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC, 0o640, dir_fd=afd)
    finally:
        os.close(afd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return
        texto = ("" if st.st_size else arquivo.CABECALHO) + "".join(linhas)
        vista = memoryview(texto.encode("utf-8", "surrogateescape"))
        while vista:
            vista = vista[os.write(fd, vista):]
        os.fsync(fd)
    finally:
        os.close(fd)


def ler_registo(root: Path) -> tuple[dict, dict]:
    try:
        rfd = os.open(root, _DIRFD)
    except OSError:
        return {}, {}
    try:
        afd = _descer(rfd, [arquivo.DIR])
    except _Erro:
        return {}, {}
    finally:
        os.close(rfd)
    try:
        fd = os.open(arquivo.REGISTO, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=afd)
    except OSError:
        return {}, {}
    finally:
        os.close(afd)
    with open(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {}, {}
        return arquivo.ler_registo(f.read().decode("utf-8", "replace"))


# ---------------------------------------------------------------- protecções

def _pedidos(cfg) -> list[tuple[str | None, float, set]]:
    """(estado, mtime, citações) de cada <dados>/decisoes/*.md (sem seguir symlinks, ≤ 1 MB)."""
    rx = arquivo.citacao_re([f["id"] for f in cfg["biblioteca"]])
    try:
        dfd = os.open(officecfg.caminho(cfg, "decisoes"), _DIRFD)
    except OSError:
        return []
    out = []
    try:
        for nome in sorted(n for n in os.listdir(dfd) if n.endswith(".md")):
            try:
                fd = os.open(nome, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
            except OSError:
                continue
            with open(fd, "rb") as f:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_size > PEDIDO_APROVACAO_MAX:
                    continue
                texto = f.read(PEDIDO_APROVACAO_MAX).decode("utf-8", "replace")
            out.append((decisoes.frontmatter(texto).get("estado"), st.st_mtime,
                        {c for _, c in arquivo.citacoes(texto, rx)}))
    finally:
        os.close(dfd)
    return out


def _cita(citacoes: set, fonte: str, caminhos: list[str]) -> bool:
    # citado = caminho igual, algo dentro da pasta do lote, ou uma pasta que o contém
    for c in citacoes:
        if not c.startswith(fonte + "/"):
            continue
        r = c[len(fonte) + 1:]
        if any(r == k or r.startswith(k + "/") or k.startswith(r + "/") for k in caminhos):
            return True
    return False


def _protegido(cfg, fonte: str, caminhos: list[str], ficheiros, now: float, auto: bool) -> str | None:
    citantes = [(e, m) for e, m, cs in _pedidos(cfg) if _cita(cs, fonte, caminhos)]
    if any(not arquivo.fechado(e) for e, _ in citantes):
        return "protegido: citado por um pedido de aprovação pendente"
    if any(m > now - arquivo.RECENTE_S for _, m in ficheiros):
        return "protegido: alterado há menos de 48 h"
    if auto and any(m > now - arquivo.CITADO_AUTO_S for _, m in citantes):
        return "protegido: citado por um pedido de aprovação alterado há menos de 14 dias"
    return None


def protegido(cfg, root: Path, fonte: str, caminhos: list[str], now: float, auto: bool = False) -> str | None:
    """Motivo «protegido: …» se o item (caminhos relativos à fonte) não pode ser arquivado; senão None."""
    ficheiros = []
    try:
        rfd = os.open(root, _DIRFD)
    except OSError:
        return None
    try:
        for c in caminhos:
            partes = c.split("/")
            try:
                pfd = _descer(rfd, partes[:-1])
            except _Erro:
                continue
            try:
                st = os.lstat(partes[-1], dir_fd=pfd)
                if stat.S_ISDIR(st.st_mode):
                    ficheiros += _regulares(pfd, partes[-1], "/".join(partes[:-1]))
                elif stat.S_ISREG(st.st_mode):
                    ficheiros.append((c, st.st_mtime))
            except OSError:
                pass
            finally:
                os.close(pfd)
    finally:
        os.close(rfd)
    return _protegido(cfg, fonte, caminhos, ficheiros, now, auto)


# ---------------------------------------------------------------- git

def _git(topo: Path, *args: str, entrada: bytes | None = None) -> subprocess.CompletedProcess:
    # --literal-pathspecs: «x[12].md» é um nome, nunca um glob; sem hooks, fsmonitor nem gpg (código do repo da fonte
    # não corre); GIT_TERMINAL_PROMPT=0 (lancador.env_git); nunca pendura a passagem
    cmd = ["git", "--literal-pathspecs", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
           "-c", "commit.gpgSign=false", "-C", str(topo), *args]
    try:
        return subprocess.run(cmd, input=entrada, capture_output=True, check=False, timeout=lancador.GIT_S,
                              env=lancador.env_git())
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, b"", b"timeout")
    except OSError:
        return subprocess.CompletedProcess(cmd, 127, b"", b"git em falta")


def _nul(caminhos) -> bytes:
    return b"".join(os.fsencode(c) + b"\0" for c in caminhos)


def git_topo(root: Path, base_biblioteca: Path) -> Path | None:
    """Topo do repo git da fonte se for `<dados>/biblioteca` ou estiver dentro dela; senão (ou sem git) None."""
    r = _git(Path(root), "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        return None
    topo = Path(os.fsdecode(r.stdout.rstrip(b"\n"))).resolve()
    base = Path(base_biblioteca).resolve()
    return topo if topo == base or base in topo.parents else None


class _Repo(NamedTuple):
    """Repo git de uma fonte numa passagem: topo, pasta da fonte no repo ("" se é o topo) e os caminhos (relativos ao
    topo) com alterações seguidas por commitar no início da passagem (None se o `git status` falhou); `gitlinks`: os
    submódulos (modo 160000) da pasta da fonte (None se o `git ls-files -s` falhou)."""
    topo: Path
    prefixo: str
    alterados: set | None
    gitlinks: set | None = None

    def rel(self, c: str) -> str:
        return f"{self.prefixo}/{c}" if self.prefixo else c


def _alterados(topo: Path, prefixo: str) -> set | None:
    # seguidos modificados, em stage ou apagados (sem os não seguidos nem os ignorados); o status não lê pathspecs do
    # stdin: só a pasta da fonte (vem da config) vai em argv e os itens filtram-se aqui
    r = _git(topo, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=no", "--no-renames",
             "--ignore-submodules=all", "--", *([prefixo] if prefixo else []))
    if r.returncode != 0:
        return None
    return {os.fsdecode(x[3:]) for x in r.stdout.split(b"\0") if len(x) > 3}


def _gitlinks(topo: Path, prefixo: str) -> set | None:
    # entradas de submódulo (o status com --ignore-submodules=all não vê um ponteiro por commitar, que o commit do
    # arquivo levaria e publicaria); «<modo> <sha> <stage>\t<caminho>», filtrado aqui (só a pasta da fonte em argv)
    r = _git(topo, "ls-files", "-s", "-z", "--", *([prefixo] if prefixo else []))
    if r.returncode != 0:
        return None
    out = set()
    for x in r.stdout.split(b"\0"):
        meta, _, c = x.partition(b"\t")
        if meta.startswith(b"160000 "):
            out.add(os.fsdecode(c))
    return out


def _repo(root: Path, base_biblioteca: Path) -> _Repo | None:
    topo = git_topo(root, base_biblioteca)
    if topo is None:
        return None
    prefixo = os.path.relpath(Path(root).resolve(), topo)
    prefixo = "" if prefixo == "." else prefixo
    return _Repo(topo, prefixo, _alterados(topo, prefixo), _gitlinks(topo, prefixo))


def _sobrepoe(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def git_commit(topo: Path, antigos: list[str], novos: list[str], msg: str, marca: str = "Squad Office",
               prefixo: str = "") -> tuple[str, list[str]]:
    """Commit só com os ficheiros seguidos movidos (antigos → novos, relativos ao topo; os ficheiros já foram movidos
    com os.rename). `prefixo`: pasta da fonte no repo. `msg` leva {n}. → ("commit" | "nada" (nada seguido) |
    "falhou", linhas); ao falhar tira do índice o que o add tiver posto (o chamador desfaz os renames)."""
    # o ls-files não lê pathspecs do stdin: lista os seguidos (só a pasta da fonte, que vem da config) e filtra aqui
    r = _git(topo, "ls-files", "-z", "--", *([prefixo] if prefixo else []))
    if r.returncode != 0:   # sem a lista não se sabe o que está seguido: «nada» deixava os moves fora do git
        return "falhou", [f"AVISO: commit do arquivo em {topo.name} falhou (ls-files); os ficheiros voltam ao sítio"]
    todos = [os.fsdecode(x) for x in r.stdout.split(b"\0") if x]
    seguidos = [s for s in todos if any(s == a or s.startswith(a + "/") for a in antigos)]
    if not seguidos:
        return "nada", []
    destino = []
    for s in seguidos:
        a, n = next((a, n) for a, n in zip(antigos, novos, strict=True) if s == a or s.startswith(a + "/"))
        destino.append(n + s[len(a):])
    ident = []
    if any(_git(topo, "config", k).stdout.strip() == b"" for k in ("user.name", "user.email")):
        ident = [f"user.name={marca}", f"user.email={EMAIL}"]
    # os novos entram no índice (-f: a .arquivo/ tem um .gitignore com «*»); os antigos já não existem e o commit só
    # com estes caminhos (--only) regista a remoção; o que já estava em stage fica fora
    r = _git(topo, "add", "-f", "--pathspec-from-file=-", "--pathspec-file-nul", entrada=_nul(destino))
    fase = "add"
    if r.returncode == 0:
        texto = msg.format(n=len(seguidos))
        extra = [x for i in ident for x in ("-c", i)]
        r = _git(topo, *extra, "commit", "-q", "-m", texto, "--pathspec-from-file=-", "--pathspec-file-nul",
                 entrada=_nul(seguidos + destino))
        if r.returncode == 0:
            return "commit", [f"biblioteca: commit em {topo.name}: {texto}"]
        fase = "commit"
    linhas = [f"AVISO: commit do arquivo em {topo.name} falhou ({fase}); os ficheiros voltam ao sítio"]
    # o índice destes caminhos volta ao HEAD (os novos saem, os antigos ficam como estavam)
    if _git(topo, "reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul",
            entrada=_nul(seguidos + destino)).returncode != 0 and fase == "commit":
        linhas.append(f"AVISO: {topo.name}: o índice ficou com ficheiros do arquivo em stage (git reset falhou)")
    return "falhou", linhas


def _ramo_integracao(topo: Path) -> str | None:
    # o ramo de integração é o HEAD do origin: refs/remotes/origin/HEAD se existir, senão pergunta ao remoto
    r = _git(topo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    s = r.stdout.decode(errors="replace").strip()
    if r.returncode == 0 and s.startswith("origin/"):
        return s[len("origin/"):]
    r = _git(topo, "ls-remote", "--symref", "origin", "HEAD")
    for linha in r.stdout.decode(errors="replace").splitlines():
        if r.returncode == 0 and linha.startswith("ref: refs/heads/") and linha.endswith("\tHEAD"):
            return linha[len("ref: refs/heads/"):-len("\tHEAD")]
    return None


def push_seguro(topo: Path, nome: str) -> str:
    """Push do commit do arquivo só no ramo de integração e com 0 atrás / 1 à frente; senão a linha de AVISO.
    Nunca --force; as mensagens não levam o stderr do git (pode ter o URL do remoto)."""
    aviso = f"AVISO: sem push em {nome}: "
    if _git(topo, "remote", "get-url", "origin").returncode != 0:
        return aviso + "sem origin"
    if _git(topo, "fetch", "-q", "origin").returncode != 0:
        return aviso + "fetch falhou"
    ramo = _ramo_integracao(topo)
    if not ramo:
        return aviso + "sem ramo de integração no origin"
    r = _git(topo, "symbolic-ref", "-q", "--short", "HEAD")
    actual = r.stdout.decode(errors="replace").strip() if r.returncode == 0 else ""
    if not actual:
        return aviso + "HEAD solto"
    if actual != ramo:
        return aviso + f"outro ramo («{actual}», não «{ramo}»)"
    r = _git(topo, "rev-list", "--left-right", "--count", f"refs/remotes/origin/{ramo}...HEAD")
    try:
        atras, frente = (int(x) for x in r.stdout.split())
    except ValueError:
        return aviso + f"sem origin/{ramo}"
    if (atras, frente) != (0, 1):
        return aviso + f"{atras} atrás e {frente} à frente do origin (contando com o commit do arquivo)"
    if _git(topo, "push", "-q", "origin", f"refs/heads/{ramo}:refs/heads/{ramo}").returncode != 0:
        return aviso + "push falhou"
    return f"biblioteca: push de {nome} para origin/{ramo}"


def _fechar(cfg, root: Path, repo: _Repo | None, nome: str,
            movs: list[tuple[str, list[str]]]) -> tuple[bool, list[str]]:
    """Um só commit (+ push seguro) para os itens (acção, caminhos relativos à fonte) movidos na passagem. Se o add ou
    o commit falhar, desfaz todos os renames (tudo ou nada) e devolve False: nada fica fora do git escondido na
    `.arquivo/`."""
    if repo is None or not movs:
        return True, []
    pares = []
    for accao, caminhos in movs:
        dentro = [f"{arquivo.DIR}/{c}" for c in caminhos]
        pares += zip(dentro, caminhos) if accao == "repor" else zip(caminhos, dentro)
    accoes = {accao for accao, _ in movs}
    msg = ("chore(arquivo): arquivar e repor {n} ficheiros" if len(accoes) > 1 else
           "chore(arquivo): repor {n} ficheiros" if accoes == {"repor"} else "chore(arquivo): {n} ficheiros")
    estado, linhas = git_commit(repo.topo, [repo.rel(a) for a, _ in pares], [repo.rel(n) for _, n in pares], msg,
                                cfg["marca"], repo.prefixo)
    if estado == "commit":
        linhas.append(push_seguro(repo.topo, nome))
    if estado != "falhou":
        return True, linhas
    for accao, caminhos in reversed(movs):
        if mover(root, caminhos, accao == "repor"):
            linhas.append(f"AVISO: {nome}: {', '.join(caminhos)} não voltou ao sítio depois do commit falhado")
    return False, linhas


def _gitignore(root: Path) -> None:
    # o que não era seguido (media ignorada…) continua fora do git dentro da .arquivo/
    rfd = os.open(root, _DIRFD)
    try:
        afd = _descer(rfd, [arquivo.DIR], criar=True)
    finally:
        os.close(rfd)
    try:
        fd = os.open(".gitignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o640,
                     dir_fd=afd)
        try:
            os.write(fd, b"*\n")
        finally:
            os.close(fd)
    except FileExistsError:
        pass
    finally:
        os.close(afd)


# ---------------------------------------------------------------- tratar um item

def _tratar(cfg, fonte: dict, chave: str, accao: str, motivo: str, now: float, repo: _Repo | None,
            auto: bool = False, ocupados: list[str] = ()) -> tuple[str | None, list[str], list[str], int]:
    """Arquiva ou repõe `chave` (só os renames; o REGISTO e o git ficam para o fim da passagem).
    → (erro de ERROS ou None, caminhos movidos relativos à fonte, linhas do REGISTO, nº de ficheiros).
    `ocupados`: caminhos já movidos nesta passagem na mesma fonte (mexer-lhes outra vez ⇒ «pedido ilegível»)."""
    root = officecfg.caminho(cfg, "biblioteca") / fonte["pasta"]
    repor = accao == "repor"
    try:
        genero, caminhos, ficheiros = _resolver(root, chave, fonte["media"], repor)
    except _Erro as e:
        return str(e), [], [], 0
    if any(_sobrepoe(c, o) for c in caminhos for o in ocupados):
        return "pedido ilegível", [], [], 0
    data = lancador.agora(cfg, now).isoformat(timespec="seconds")
    linhas = [arquivo.linha_registo(data, accao, f, motivo, chave) for f, _ in ficheiros]
    if any(x is None for x in linhas) or any(arquivo.linha_registo("", "", c, "", "") is None for c in caminhos):
        return "nome com tab ou quebra de linha", [], [], 0
    if not repor:
        p = _protegido(cfg, fonte["id"], caminhos, ficheiros, now, auto)
        if p:
            return p, [], [], 0
    if repo is not None:
        # o commit do arquivo leva o conteúdo do disco: alterações locais dos seguidos do item seriam publicadas
        if repo.alterados is None or repo.gitlinks is None:
            return "erro git", [], [], 0
        origem = [repo.rel(f"{arquivo.DIR}/{c}" if repor else c) for c in caminhos]
        if any(_sobrepoe(s, o) for s in repo.gitlinks for o in origem):   # submódulo dentro do item
            return "não arquivável", [], [], 0
        if any(_sobrepoe(s, o) for s in repo.alterados for o in origem):
            return "alterações por commitar", [], [], 0
        try:
            _gitignore(root)
        except (OSError, _Erro):
            pass
    erro = mover(root, caminhos, not repor)
    if erro:
        return erro, [], [], 0
    return None, caminhos, linhas, len(ficheiros)


def _registar_seguro(root: Path, linhas: list[str]) -> None:
    try:
        _registar(root, linhas)
    except (OSError, _Erro):
        pass   # o REGISTO é informativo; os ficheiros já estão no sítio certo


# ---------------------------------------------------------------- fila

def processar(cfg, now: float | None = None) -> list[str]:
    """Uma passagem pela fila do arquivo: até PENDENTES_MAX pedidos pendentes (por nome; os outros ficam para a
    passagem seguinte) são revalidados e tratados; um só commit (+ push) por fonte no fim; cada um é respondido em
    <id>.feito.json (erro git ⇒ os renames dessa fonte foram desfeitos)."""
    t = time.time() if now is None else now
    fila, linhas = officecfg.caminho(cfg, "arquivo_fila"), []
    try:
        fila.mkdir(mode=0o750, exist_ok=True)
        dfd = seguro.abrir_pasta(fila)
    except OSError:
        return [f"biblioteca: fila do arquivo insegura ou ilegível ({fila.name}/)"] if os.path.lexists(fila) else []
    try:
        nomes = set(os.listdir(dfd))
        pendentes = [n for n in sorted(nomes) if arquivo.NOME.fullmatch(n) and n[:-5] + ".feito.json" not in nomes]
        pedidos = [(n[:-5], arquivo.ler_json(dfd, n)) for n in pendentes[:arquivo.PENDENTES_MAX]]
    finally:
        os.close(dfd)
    fontes = {f["id"]: f for f in cfg["biblioteca"]}
    base = officecfg.caminho(cfg, "biblioteca")
    repos, vistos, movs, resultados = {}, set(), {}, []
    for stem, p in pedidos:
        caminhos, reg, n = [], [], 0
        if arquivo.pedido_ok(p, stem, fontes):
            fid = p["fonte"]
            if (fid, p["chave"]) in vistos:
                erro = "pedido ilegível"         # 2.º pedido do mesmo item na mesma passagem
            else:
                vistos.add((fid, p["chave"]))
                if fid not in repos:
                    repos[fid] = _repo(base / fontes[fid]["pasta"], base)
                ocupados = [c for m in movs.get(fid, []) for c in m[1]]
                erro, caminhos, reg, n = _tratar(cfg, fontes[fid], p["chave"], p["accao"], MOTIVO[p["accao"]], t,
                                                 repos[fid], ocupados=ocupados)
                if not erro:
                    movs.setdefault(fid, []).append((p["accao"], caminhos, stem))
        elif isinstance(p, dict) and isinstance(p.get("fonte"), str) and arquivo.pedido_ok(p, stem, (p["fonte"],)):
            erro = "fonte desconhecida"
        else:
            erro = "pedido ilegível"
        resultados.append((stem, p, erro, reg, n))
    fecho, falhados = [], set()
    for fid, ms in movs.items():
        ok, ls = _fechar(cfg, base / fontes[fid]["pasta"], repos[fid], fid, [(acc, cs) for acc, cs, _ in ms])
        fecho += ls
        if not ok:
            falhados |= {s for _, _, s in ms}
    for stem, p, erro, reg, n in resultados:
        if stem in falhados:
            erro = "erro git"
        elif not erro:
            _registar_seguro(base / fontes[p["fonte"]]["pasta"], reg)
        try:
            lancador.responder(fila, stem, {"erro": erro} if erro else {"ok": True})
        except OSError as e:
            linhas.append(f"biblioteca: pedido {stem}: resposta não gravada ({type(e).__name__})")
        if erro:
            linhas.append(f"biblioteca: pedido {stem} recusado: {erro}")
            continue
        feito = "arquivado" if p["accao"] == "arquivar" else "reposto"
        linhas.append(f"biblioteca: {feito} {p['fonte']}/{p['chave']} ({n} ficheiros)")
    return linhas + fecho


# ---------------------------------------------------------------- automático (opt-in, ADR-012)

def _itens(root: Path, media: bool, nome: str) -> dict[str, tuple[float, list[str]]]:
    """chave → (mtime do ficheiro mais recente, ficheiros) dos itens arquiváveis da fonte (documentos e lotes)."""
    lotes: dict[str, tuple[float, list[str]]] = {}
    docs: dict[tuple, list] = {}
    for partes, _, st, tipo in arquivo.varrer(root, media, nome):
        if media and partes[0] == "media" and len(partes) >= 3:
            k = f"media/{partes[1]}"
            m, fs = lotes.get(k, (0.0, []))
            lotes[k] = (max(m, st.st_mtime), [*fs, "/".join(partes)])
        elif tipo in ("doc", "pdf"):
            docs.setdefault((partes[:-1], arquivo.raiz_nome(partes[-1])), []).append((partes, st))
    out = dict(lotes)
    for fs in docs.values():
        # principal como na Biblioteca: o sem sufixo («x.md»), senão o de mtime mais antiga
        fs.sort(key=lambda x: (arquivo.raiz_nome(x[0][-1]) + ".md" != x[0][-1], x[1].st_mtime_ns, x[0][-1]))
        out["/".join(fs[0][0])] = (max(st.st_mtime for _, st in fs), ["/".join(p) for p, _ in fs])
    return out


def _auto_fonte(cfg, fonte: dict, now: float) -> list[str]:
    dias, fid = fonte["arquivo_dias"], fonte["id"]
    base = officecfg.caminho(cfg, "biblioteca")
    root = base / fonte["pasta"]
    if not root.is_dir():
        return []
    repo = _repo(root, base)
    por_item, por_caminho = ler_registo(root)
    repostos = {c for c, (_, accao, _) in por_caminho.items() if accao == "repor"}
    saida, movs = [], []
    for chave, (mtime, fs) in sorted(_itens(root, fonte["media"], fid).items()):
        if mtime > now - dias * 86400:
            continue
        # reposto alguma vez (pelo item ou por algum dos seus ficheiros): nunca volta a sair sozinho
        if (por_item.get(chave, ("", "", ""))[1] == "repor" or any(f in repostos for f in fs)
                or any(r.startswith(chave + "/") for r in repostos)):
            continue
        erro, caminhos, reg, n = _tratar(cfg, fonte, chave, "arquivar", f"idade ({dias} dias)", now, repo, auto=True)
        if erro:
            saida.append(f"biblioteca: {fid}/{chave} não arquivado: {erro}")
            continue
        movs.append(("arquivar", caminhos))
        saida.append((chave, reg, n))
    ok, fecho = _fechar(cfg, root, repo, fid, movs)
    linhas = []
    for x in saida:
        if isinstance(x, str):
            linhas.append(x)
        elif not ok:
            linhas.append(f"biblioteca: {fid}/{x[0]} não arquivado: erro git")
        else:
            _registar_seguro(root, x[1])
            linhas.append(f"biblioteca: arquivado {fid}/{x[0]} ({x[2]} ficheiros, idade > {dias} dias)")
    return linhas + fecho


def automatico(cfg, now: float | None = None) -> list[str]:
    """Arquivo por idade nas fontes com `arquivo_dias`, no máximo 1× por dia (marca em <dados>/runner/)."""
    fontes = [f for f in cfg["biblioteca"] if f.get("arquivo_dias")]
    if not fontes:
        return []
    t = time.time() if now is None else now
    dia = lancador.agora(cfg, t).date().isoformat()
    marca = officecfg.caminho(cfg, "runner") / "arquivo-auto.dia"
    try:
        if marca.read_text(encoding="ascii").strip() == dia:
            return []
    except (OSError, UnicodeDecodeError):
        pass
    linhas = []
    for f in fontes:
        linhas += _auto_fonte(cfg, f, t)
    try:
        tmp = marca.with_name(f".{marca.name}.tmp")
        tmp.write_text(dia + "\n", encoding="ascii")
        os.replace(tmp, marca)
    except OSError:
        linhas.append("biblioteca: marca do arquivo automático não gravada")
    return linhas


# ---------------------------------------------------------------- rotação

def rodar(cfg, now: float | None = None) -> list[str]:
    """Move para feito/ o <id>.json e o <id>.feito.json cuja resposta tem mais de rotacao_dias (o Office só lê a
    raiz). Por fds da fila e de feito/, sem seguir symlinks."""
    fila = officecfg.caminho(cfg, "arquivo_fila")
    limite = (time.time() if now is None else now) - cfg["runner"]["rotacao_dias"] * 86400
    linhas = []
    try:
        dfd = seguro.abrir_pasta(fila)
    except OSError:
        return linhas
    ffd = None
    try:
        for nome in sorted(os.listdir(dfd)):
            m = arquivo.FEITO.fullmatch(nome)
            if not m:
                continue
            stem = m.group(1)
            try:
                if os.lstat(nome, dir_fd=dfd).st_mtime > limite:
                    continue
                if ffd is None:
                    ffd = seguro.subpasta(dfd, "feito")
                for suf in (".json", ".feito.json"):   # o pedido primeiro: o Office nunca vê uma resposta sozinha
                    try:
                        seguro.mover(dfd, stem + suf, ffd)
                    except FileNotFoundError:
                        pass
            except OSError:
                linhas.append(f"biblioteca: pedido {stem} não rodado: erro a mover para feito/")
                continue
            linhas.append(f"biblioteca: pedido {stem} rodado para feito/")
    finally:
        if ffd is not None:
            os.close(ffd)
        os.close(dfd)
    return linhas
