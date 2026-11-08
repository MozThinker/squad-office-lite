"""Operações em pastas que o contentor escreve (decisoes-fila, quadro-lancar): o contentor é tratado como hostil.

Tudo por descritor de pasta (`dir_fd`), sem seguir symlinks: uma `feito/` ou um `.tmp` trocados por symlinks nunca
levam o runner a mover, criar ou truncar ficheiros fora da fila. Stdlib só, Python >= 3.10.
"""
from __future__ import annotations

import os
import stat

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_NOVO = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def abrir_pasta(path, dir_fd: int | None = None) -> int:
    """fd de uma pasta real (não symlink) do próprio utilizador; OSError se não for."""
    fd = os.open(path, _DIR, dir_fd=dir_fd)
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)
        raise OSError(f"pasta insegura: {os.fspath(path)}")
    return fd


def subpasta(dfd: int, nome: str) -> int:
    """Cria (se faltar) e abre `nome` dentro de `dfd`, recusando symlinks e não-pastas."""
    try:
        os.mkdir(nome, 0o750, dir_fd=dfd)
    except FileExistsError:
        pass
    return abrir_pasta(nome, dir_fd=dfd)


def escrever(dfd: int, nome: str, dados: bytes) -> None:
    """Cria `nome` de novo (0600, O_EXCL|O_NOFOLLOW) dentro de `dfd`; o que lá estivesse (symlink incluído) sai antes."""
    try:
        os.unlink(nome, dir_fd=dfd)
    except FileNotFoundError:
        pass
    fd = os.open(nome, _NOVO, 0o600, dir_fd=dfd)
    try:
        vista = memoryview(dados)
        while vista:
            vista = vista[os.write(fd, vista):]
        os.fsync(fd)
    finally:
        os.close(fd)


def mover(src_fd: int, nome: str, dst_fd: int, destino: str | None = None) -> None:
    """rename relativo aos dois fds (o rename não segue symlinks: move a entrada, nunca o alvo)."""
    os.rename(nome, destino or nome, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
