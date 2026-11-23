#!/usr/bin/env python3
"""Código da Conversa (ADR-023, contrato §2.2; revisão S2/S5 e G2): gera o código de uso único e o `entrada.json`.

- Código: 24 caracteres base32 RFC 4648 (A–Z2–7, ≈ 120 bits), mostrado em 6 grupos `XXXX-…`. Só aparece no ecrã de
  quem corre o instalador: nunca vai para disco. Regista um browser uma vez (o guardião apaga o `entrada.json`); a
  chave HMAC é aleatória, gerada no browser (`chaves.json` do guardião, ver o topo do guardiao.py).
- `<estado>/entrada.json` (0600): sais, iterações e o sha256 do verificador (o `sal_ponte` já não tem uso; fica pelo
  formato). A `<estado>/chave` antiga (PBKDF2 do código) apaga-se; as chaves dos browsers registados ficam.
O `<estado>` é o do guardião (`/var/lib/office-conversa`, 0700, só o utilizador `office-conversa`): nem o dono, nem
o motor, nem o Office o lêem.

Uso (como `office-conversa`): `python3 -I codigo.py --estado <dir>` escreve o `entrada.json` e imprime o código uma
vez no stdout. Só stdlib, Python ≥ 3.10.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ITERACOES = 600_000
SAL_BYTES = 16
CODIGO_BYTES = 15          # 15 bytes = 24 caracteres base32 sem «=»


def gerar_codigo() -> str:
    return base64.b32encode(secrets.token_bytes(CODIGO_BYTES)).decode("ascii")


def normalizar(codigo: str) -> str:
    return "".join(c for c in codigo.upper() if c not in "- \t")


def mostrar(codigo: str) -> str:
    c = normalizar(codigo)
    return "-".join(c[i:i + 4] for i in range(0, len(c), 4))


def derivar(codigo: str, sal_hex: str, iteracoes: int = ITERACOES) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", normalizar(codigo).encode("ascii"), bytes.fromhex(sal_hex), iteracoes, 32)


def entrada(codigo: str, sal_office: str, sal_ponte: str, criado: str, iteracoes: int = ITERACOES) -> dict:
    verificador = derivar(codigo, sal_office, iteracoes)
    return {"v": 1, "sal_office": sal_office, "sal_ponte": sal_ponte, "iteracoes": iteracoes,
            "verificador_sha256": hashlib.sha256(verificador).hexdigest(), "criado": criado}


def _gravar(path: Path, dados: bytes, modo: int) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dados)
            f.flush()
            os.fchmod(f.fileno(), modo)
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def gerar(estado: Path, codigo: str | None = None, iteracoes: int = ITERACOES) -> str:
    """Escreve o `entrada.json` com sais novos e apaga a `chave` antiga; devolve o código (normalizado)."""
    codigo = normalizar(codigo or gerar_codigo())
    sal_office, sal_ponte = secrets.token_hex(SAL_BYTES), secrets.token_hex(SAL_BYTES)
    criado = datetime.now(timezone.utc).isoformat(timespec="seconds")
    estado.mkdir(mode=0o700, parents=True, exist_ok=True)
    _gravar(estado / "entrada.json",
            json.dumps(entrada(codigo, sal_office, sal_ponte, criado, iteracoes)).encode() + b"\n", 0o600)
    (estado / "chave").unlink(missing_ok=True)
    return codigo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gera um código novo da Conversa (entrada.json do guardião).")
    ap.add_argument("--estado", required=True, type=Path)
    a = ap.parse_args(argv)
    if not a.estado.is_dir():
        print(f"codigo.py: {a.estado} não é uma pasta", file=sys.stderr)
        return 1
    print(mostrar(gerar(a.estado)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
