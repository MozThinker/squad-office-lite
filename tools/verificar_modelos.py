#!/usr/bin/env python3
"""Verifica que os modelos de squad (`modelos/squads/`) são genéricos e válidos (ADR-019, CA-30).

    python tools/verificar_modelos.py [pasta]          (omissão: modelos/squads ao lado desta pasta)

Falha (exit 1, uma linha `ficheiro:linha: motivo` por achado) em: caminhos de casas (`/home/…`, `/root`), nomes
internos, e-mails, telefones, IPs, URLs fora da documentação da Anthropic, segredos (`agentes.tapar`), symlinks,
ficheiros que não sejam UTF-8 e, por modelo, `README.md` sem frontmatter `nome`/`lead`/`resumo`, `lead` que não é um
dos agentes, `CLAUDE.md` sem `{{MISSAO}}` ou `agents/*.md` que não passam o `agentes.validar_md` (sem skills).
A lista de nomes internos não está aqui: vem da linha do `git grep` do passo «Genérico» do `.github/workflows/ci.yml`
(como `scripts/preparar-publico.sh`); sem essa linha (árvore pública) essa verificação salta. Só stdlib.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import agentes

CI = RAIZ / ".github" / "workflows" / "ci.yml"
_LINHA_NOMES = re.compile(r"\s*nada -niE '([^']+)' -- \. ':!CHANGELOG\.md'")
_CAMINHO = re.compile(r"/(?:ho[m]e|root)(?:/|\b)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TELEFONE = re.compile(r"(?<![\w.+-])\+?\d(?:[ .-]?\d){8,}(?![\w.])")    # ≥ 9 dígitos
_IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_URL = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_URL_SEM = re.compile(r"(?<![\w.@/-])www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.IGNORECASE)
_URL_OK = "https://docs.anthropic.com/"


def nomes_internos(ci: Path = CI) -> re.Pattern | None:
    """Padrão (sem maiúsculas) da linha de nomes do ci.yml, ou None se não houver ficheiro ou linha."""
    try:
        linhas = ci.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return None
    for ln in linhas:
        m = _LINHA_NOMES.match(ln)
        if m:
            return re.compile(m.group(1), re.IGNORECASE)
    return None


def achados_linha(ln: str, internos: re.Pattern | None) -> list[str]:
    out = []
    if _CAMINHO.search(ln):
        out.append("caminho pessoal")
    if internos is not None and internos.search(ln):
        out.append("nome interno")
    if _EMAIL.search(ln):
        out.append("e-mail")
    if _IP.search(ln):
        out.append("IP")
    elif _TELEFONE.search(ln):
        out.append("telefone")
    if any(not u.startswith(_URL_OK) for u in _URL.findall(ln)) or _URL_SEM.search(ln):
        out.append("URL")
    if agentes.tapar(ln) != ln:
        out.append("segredo")
    return out


def _modelo(pasta: Path, rel) -> list[str]:
    """Estrutura de um modelo: README (nome, lead, resumo), CLAUDE.md com {{MISSAO}}, agentes válidos."""
    out = []
    raw = agentes.ler_bytes(pasta / "README.md", agentes.CAND_MAX)
    try:
        fm = agentes.frontmatter(raw.decode("utf-8")) if raw is not None else None
    except UnicodeDecodeError:
        fm = None
    if fm is None:
        out.append(f"{rel(pasta / 'README.md')}:1: README.md sem frontmatter legível")
        fm = {}
    for k in ("nome", "resumo"):
        if not agentes.texto_ok(fm.get(k), 1, 80 if k == "nome" else agentes.DESC_MAX, False):
            out.append(f"{rel(pasta / 'README.md')}:1: {k} inválido ou em falta")
    nomes = []
    for f, raw_ag in agentes.agentes_de(pasta):
        nome = f[len("agents/"):-3]
        try:
            erro = agentes.validar_md(raw_ag.decode("utf-8"), nome, set()) if raw_ag is not None else "ilegível"
        except UnicodeDecodeError:
            erro = "não é UTF-8"
        if erro:
            out.append(f"{rel(pasta / f)}:1: {erro}")
        else:
            nomes.append(nome)
    if not nomes:
        out.append(f"{rel(pasta / 'agents')}:0: sem agentes válidos")
    if fm.get("lead") not in nomes:
        out.append(f"{rel(pasta / 'README.md')}:1: lead não é um dos agentes")
    claude = agentes.ler_bytes(pasta / "CLAUDE.md", agentes.CAND_MAX)
    if claude is None or b"{{MISSAO}}" not in claude:
        out.append(f"{rel(pasta / 'CLAUDE.md')}:0: falta o marcador {{{{MISSAO}}}}")
    return out


def verificar(pasta: Path, internos: re.Pattern | None) -> list[str]:
    pasta = Path(pasta)

    def rel(p: Path) -> str:
        return str(p.relative_to(pasta.parent))

    out = []
    for raiz, dirs, fs in os.walk(pasta, followlinks=False):
        dirs.sort()
        for n in sorted(fs + [d for d in dirs if os.path.islink(os.path.join(raiz, d))]):
            p = Path(raiz) / n
            if p.is_symlink():
                out.append(f"{rel(p)}:0: symlink")
                continue
            try:
                texto = p.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                out.append(f"{rel(p)}:0: não é UTF-8")
                continue
            for i, ln in enumerate(texto.split("\n"), 1):
                out += [f"{rel(p)}:{i}: {c}" for c in achados_linha(ln, internos)]
    for e in sorted(pasta.iterdir()):
        if e.is_dir() and not e.is_symlink():
            out += _modelo(e, rel)
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    pasta = Path(argv[0]) if argv else RAIZ / "modelos" / "squads"
    if not pasta.is_dir() or pasta.is_symlink():
        print(f"verificar_modelos.py: {pasta} não é uma pasta", file=sys.stderr)
        return 1
    achados = verificar(pasta, nomes_internos())
    for x in achados:
        print(x)
    return 1 if achados else 0


if __name__ == "__main__":
    sys.exit(main())
