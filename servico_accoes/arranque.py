"""Arranque do serviço `accoes` (stdlib): root só para o chown das raízes dos 4 volumes, depois ACCOES_UID:ACCOES_GID.

No modo Docker o serviço arranca com `user: "0:0"` e só as capacidades CHOWN, SETUID e SETGID: muda o dono das
raízes (lista fixa, não recursivo, sem seguir symlinks) e larga os privilégios para ACCOES_UID:ACCOES_GID (omissão
10002), que nunca pode ser root nem o OFFICE_UID (omissão 10001) do office e do runner. Fica com o OFFICE_GID
(omissão 10001) como grupo suplementar, só para ler os volumes decisoes e agentes (montados só de leitura; raízes
0750 do configurar). Já sem root dá o modo: 0700 em todas, salvo as pastas do ACCOES_ENTRADA (0755: o runner lê as
mensagens e a vista) e do ACCOES_SAUDE (0755: o office lê o saude.json). No modo VPS o serviço já corre com o
utilizador próprio (instalar-accoes.sh): não faz nada.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

UID_OMISSAO = 10002
OFFICE_UID_OMISSAO = 10001


def _numero(var: str, omissao: int) -> int:
    v = os.environ.get(var, "") or str(omissao)
    if not v.isdigit() or not 0 < int(v) < 2**31:
        raise SystemExit(f"accoes: {var} inválido (um número > 0)")
    return int(v)


def alvo() -> tuple[int, int, int]:
    """(uid, gid, OFFICE_GID); sai ≠ 0 se o ACCOES_UID for o OFFICE_UID (ou algum for inválido ou 0)."""
    uid, gid = _numero("ACCOES_UID", UID_OMISSAO), _numero("ACCOES_GID", UID_OMISSAO)
    if uid == _numero("OFFICE_UID", OFFICE_UID_OMISSAO):
        raise SystemExit("accoes: ACCOES_UID igual ao OFFICE_UID: o runner leria o accoes.env; recuso arrancar")
    return uid, gid, _numero("OFFICE_GID", OFFICE_UID_OMISSAO)


def _abrir(path: Path) -> int:
    """fd da pasta aberta componente a componente com O_NOFOLLOW: um symlink em qualquer ponto dá OSError."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for parte in Path(os.path.abspath(path)).parts[1:]:
            novo = os.open(parte, flags, dir_fd=fd)
            os.close(fd)
            fd = novo
    except OSError:
        os.close(fd)
        raise
    return fd


def _chown(path: Path, uid: int, gid: int) -> None:
    """Muda o dono da raiz se ainda não for uid:gid. Sem DAC_OVERRIDE o root não abre uma raiz 0700 de outro uid:
    por isso a que já é nossa (arranques seguintes) nem se abre."""
    try:
        st = os.lstat(path)
    except OSError as e:
        raise SystemExit(f"accoes: falta a pasta {path} ({type(e).__name__})") from None
    if not stat.S_ISDIR(st.st_mode):   # symlink (lstat não o segue) ou ficheiro
        raise SystemExit(f"accoes: {path} não é uma pasta (ou é um symlink): recuso arrancar")
    if (st.st_uid, st.st_gid) == (uid, gid):
        return
    try:
        fd = _abrir(path)
    except OSError as e:
        raise SystemExit(f"accoes: não consegui abrir {path} sem seguir symlinks ({type(e).__name__})") from None
    try:
        os.fchown(fd, uid, gid)
    finally:
        os.close(fd)


def _modo(path: Path, modo: int) -> None:
    fd = _abrir(path)
    try:
        os.fchmod(fd, modo)
    finally:
        os.close(fd)


def _largar(uid: int, gid: int, grupos: list[int]) -> None:
    os.setgroups(grupos)
    os.setgid(gid)
    os.setuid(uid)
    if os.geteuid() == 0 or os.getuid() == 0:
        raise SystemExit("accoes: continuo a ser root depois de largar os privilégios: recuso continuar")
    try:
        os.setuid(0)
    except PermissionError:
        return
    raise SystemExit("accoes: consegui voltar a root: recuso continuar")


def preparar(raizes: list[Path]) -> None:
    """Com root: chown das raízes, largar para ACCOES_UID:ACCOES_GID e modos (0700; 0755 nas do ACCOES_ENTRADA e do
    ACCOES_SAUDE). Sem root: nada (modo VPS). Qualquer falha sai ≠ 0 (SystemExit com a mensagem)."""
    if os.geteuid() != 0:
        return
    uid, gid, office_gid = alvo()
    raizes = [Path(p) for p in raizes]
    for p in raizes:
        _chown(p, uid, gid)
    _largar(uid, gid, [] if office_gid == gid else [office_gid])
    abertas = {Path(v) for v in (os.environ.get("ACCOES_ENTRADA"), os.environ.get("ACCOES_SAUDE")) if v}
    for p in raizes:
        try:
            _modo(p, 0o755 if p in abertas else 0o700)
        except OSError as e:
            raise SystemExit(f"accoes: não consegui dar o modo a {p} ({type(e).__name__})") from None
