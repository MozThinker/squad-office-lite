#!/usr/bin/env python3
"""Verifica que, no compose do modo Docker, só o serviço `accoes` vê o `accoes.env` (ADR-021, CA-18c e CA-29).

  python tools/verificar_compose_accoes.py <compose.yaml>      (ex.: compose.example.yaml; stdlib, sem docker)

Sai 0 se nenhum outro serviço monta as credenciais; sai 1 com uma linha `serviço <x> monta accoes.env (<via>)` por
achado. Leitor por indentação só do bloco `services:` (e dos `secrets:` de topo); o compose do Lite tem tabs no
Caddyfile em linha, que o leitor de YAML do officecfg recusa. Falha um serviço ≠ `accoes` que: tenha `accoes.env` em
qualquer linha (`env_file`, `volumes`, `environment`…); monte o volume nomeado que o `accoes` monta na pasta do
`ACCOES_SEGREDOS`, ou um bind dessa pasta ou de uma que a contém; liste um segredo cujo `file:` seja um `accoes.env`;
use `volumes_from`; ou que veja o host (`docker.sock`, `/var/lib/docker`, `privileged: true`, `pid: host`). Os volumes
nomeados comparam-se pelo nome real (`name:` de topo, senão `<name do projecto>_<chave>`). Também falham: binds de
`/`, `/var`, `/var/lib`, `/var/run`, `/run`; `devices:` e `cap_add:` no `office` e no `runner`; e o que o leitor não
sabe ler (indentação ímpar ou com tabs, serviços ou chaves entre aspas ou em flow style, itens `- {…}`).
Conservador: `volumes:`, `secrets:`, `env_file:` ou `volumes_from:` com valor na mesma linha (flow style) falham;
âncoras (`<<:`), `extends:` e `include:` falham sempre (o que não se sabe ler não passa).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_CHAVE = re.compile(r"(\s*)(?:- )?([A-Za-z0-9_.-]+):\s*(.*)$")
_VAR = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::?-([^}]*))?\}")
_CURTO = re.compile(r"(\$\{[^}]*\}[^:]*|[^:]+):([^:]+)(?::.*)?")


def _ind(ln: str) -> int:
    return len(ln) - len(ln.lstrip(" "))


def _limpo(v: str) -> str:
    return v.strip().strip("\"'")


def _conteudo(ln: str) -> bool:
    return bool(ln.strip()) and not ln.lstrip().startswith("#")


def bloco_topo(linhas: list[str], chave: str) -> list[str]:
    """As linhas debaixo de `chave:` na coluna 0, até à próxima chave de coluna 0."""
    out, dentro = [], False
    for ln in linhas:
        if _conteudo(ln) and _ind(ln) == 0:
            dentro = ln.rstrip() == f"{chave}:"
            continue
        if dentro:
            out.append(ln)
    return out


def servicos(linhas: list[str]) -> dict[str, list[str]]:
    out, nome = {}, None
    for ln in bloco_topo(linhas, "services"):
        if not _conteudo(ln):
            continue
        if _ind(ln) == 2:
            m = re.fullmatch(r"  ([A-Za-z0-9_.-]+):\s*", ln)
            nome = m.group(1) if m else None
            if nome:
                out[nome] = []
        elif nome:
            out[nome].append(ln)
    return out


def _sub(bloco: list[str], chave: str) -> list[str]:
    """As linhas debaixo de `    chave:` de um serviço."""
    out, dentro = [], False
    for ln in bloco:
        if _ind(ln) == 4 and not ln.lstrip().startswith("- "):
            dentro = ln.strip().startswith(f"{chave}:")
            continue
        if dentro:
            out.append(ln)
    return out


def volumes(bloco: list[str]) -> list[dict]:
    """Cada item de `volumes:` como {type, source, target} (forma longa ou curta)."""
    itens, base = [], None
    for ln in _sub(bloco, "volumes"):
        s = ln.strip()
        if s.startswith("- ") and (base is None or _ind(ln) == base):
            base = _ind(ln)
            m = re.fullmatch(r"(type|source|target):\s*(.*)", s[2:])
            if m:
                itens.append({m.group(1): _limpo(m.group(2))})
                continue
            c = _CURTO.fullmatch(_limpo(s[2:]))
            src, alvo = (c.group(1), c.group(2)) if c else (_limpo(s[2:]), "")
            itens.append({"type": "bind" if src.startswith(("/", ".", "~", "$")) else "volume", "source": src,
                          "target": alvo})
        elif itens and _ind(ln) == base + 2:
            m = _CHAVE.fullmatch(ln)
            if m and m.group(2) in ("type", "source", "target"):
                itens[-1][m.group(2)] = _limpo(m.group(3))
    return itens


def _caminho(v: str) -> str:
    # relativos à pasta do compose («/compose» fictício): «.» e «..» contêm todos os relativos
    return os.path.normpath(os.path.join("/compose", _VAR.sub(lambda m: m.group(1) or "", v)))


def _dentro(p: str, pasta: str) -> bool:
    return p == pasta or p.startswith(pasta.rstrip("/") + "/")


_RAIZES_HOST = ("/", "/var", "/var/lib", "/var/run", "/run")


def forma(linhas: list[str]) -> list[str]:
    """O que o leitor não sabe ler não passa (N4): no bloco `services:` só indentação par de espaços, serviços como
    `  nome:` e chaves como `    chave:` sem aspas, sem flow style nem itens `- {…}`."""
    out, dentro = [], False
    for ln in linhas:
        if re.match(r"[^\s#]", ln):   # chave de topo (um tab no início não é: fica dentro e falha abaixo)
            dentro = ln.rstrip() == "services:"
            continue
        if not dentro or not _conteudo(ln):
            continue
        n = _ind(ln)
        if "\t" in ln[:len(ln) - len(ln.lstrip())] or n % 2:
            out.append(f"indentação que não sei ler: {ln.strip()[:40]}")
        elif n == 2 and not re.fullmatch(r"  [A-Za-z0-9_.-]+:\s*(#.*)?", ln):
            out.append(f"serviço em formato que não sei ler: {ln.strip()[:40]}")
        elif n == 4 and not re.fullmatch(r"    [A-Za-z0-9_.-]+:(\s.*)?", ln):
            out.append(f"chave em formato que não sei ler: {ln.strip()[:40]}")
        elif ln.strip().startswith("- {"):
            out.append(f"item em flow style: {ln.strip()[:40]}")
    return out


def achados(texto: str) -> list[str]:
    linhas = texto.split("\n")
    for ln in linhas:
        if _conteudo(ln) and re.search(r"(^|\s)(<<:|extends:|include:)", ln):
            return ["compose com <<:, extends: ou include: (não sei verificar; escreve os serviços por extenso)"]
    out = forma(linhas)
    if out:
        return out
    svs = servicos(linhas)
    if "accoes" not in svs:
        return ["falta o serviço accoes"]
    alvo = next((_limpo(m.group(1)) for ln in _sub(svs["accoes"], "environment")
                 if (m := re.search(r"ACCOES_SEGREDOS[:=]\s*(\S+)", ln))), None)
    seg = next((v for v in volumes(svs["accoes"]) if alvo and v.get("target") and _dentro(alvo, v["target"])), None)
    if seg is None or not seg.get("source"):
        return ["o serviço accoes não monta a pasta do ACCOES_SEGREDOS"]
    # volumes de topo: chave → nome real no docker (`name:`; sem ele `<name do projecto>_<chave>`; externo: a chave)
    proj = next((_limpo(m.group(1)) for ln in linhas if (m := re.fullmatch(r"name:\s*(\S+)\s*", ln))), "")
    nomes, nome = {}, None
    for ln in bloco_topo(linhas, "volumes"):
        if _conteudo(ln) and _ind(ln) == 2:
            nome = ln.strip().rstrip(":")
            nomes[nome] = f"{proj}_{nome}" if proj else nome
        elif nome and (m := re.fullmatch(r"\s+name:\s*(.+)", ln)):
            nomes[nome] = _limpo(m.group(1))
        elif nome and re.fullmatch(r"\s+external:\s*true\s*", ln) and nomes[nome] == f"{proj}_{nome}":
            nomes[nome] = nome
    seg_vol = nomes.get(seg["source"], seg["source"])
    ficheiros = {}
    nome = None
    for ln in bloco_topo(linhas, "secrets"):
        if _conteudo(ln) and _ind(ln) == 2:
            nome = ln.strip().rstrip(":")
        elif nome and (m := re.fullmatch(r"\s+file:\s*(.+)", ln)):
            ficheiros[nome] = _limpo(m.group(1))
    for s, bloco in svs.items():
        if s == "accoes":
            continue
        for ln in bloco:
            m = re.fullmatch(r"    (volumes|secrets|env_file|volumes_from):\s*(\S.*)", ln)
            if m and not m.group(2).startswith("#"):
                out.append(f"serviço {s}: {m.group(1)} na mesma linha (não sei verificar; escreve-o em lista)")
            for perigo in ("docker.sock", "/var/lib/docker"):
                if perigo in ln:
                    out.append(f"serviço {s} vê o host ({perigo})")
            if re.fullmatch(r"    (privileged:\s*\"?true\"?|pid:\s*\"?host\"?)\s*(#.*)?", ln):
                out.append(f"serviço {s} vê o host ({ln.strip()})")
            if s in ("office", "runner") and re.fullmatch(r"    (devices|cap_add):.*", ln):
                out.append(f"serviço {s} vê o host ({ln.strip().split(':')[0]})")
        via = None
        for ln in bloco:
            if _ind(ln) == 4 and not ln.lstrip().startswith("- "):
                via = ln.strip().split(":")[0]
            if "accoes.env" in ln:
                out.append(f"serviço {s} monta accoes.env ({via})")
                break
        if _sub(bloco, "volumes_from"):
            out.append(f"serviço {s} monta accoes.env (volumes_from)")
        for v in volumes(bloco):
            src = v.get("source", "")
            if seg.get("type") == "volume" and v.get("type") == "volume" and nomes.get(src, src) == seg_vol:
                out.append(f"serviço {s} monta accoes.env (volumes: {src})")
            elif seg.get("type") == "bind" and v.get("type") == "bind" and \
                    _dentro(_caminho(seg["source"]), _caminho(src)):
                out.append(f"serviço {s} monta accoes.env (volumes: bind {src})")
            if v.get("type") == "bind" and _caminho(src) in _RAIZES_HOST:
                out.append(f"serviço {s} vê o host (bind {src})")
        for ln in _sub(bloco, "secrets"):
            m = re.fullmatch(r"\s*-\s*(?:source:\s*)?([A-Za-z0-9_.-]+)\s*", ln)
            if m and "accoes.env" in ficheiros.get(m.group(1), ""):
                out.append(f"serviço {s} monta accoes.env (secrets: {m.group(1)})")
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("uso: verificar_compose_accoes.py <compose.yaml>", file=sys.stderr)
        return 2
    out = achados(Path(argv[0]).read_text(encoding="utf-8"))
    for a in out:
        print(a)
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
