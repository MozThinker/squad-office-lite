#!/usr/bin/env python3
"""Verifica que nenhum serviço do compose chega às credenciais do dono nem à Conversa fora do previsto (ADR-023, CA-11 e
revisão do guardião).

  python tools/verificar_compose_conversa.py <compose.yaml> [--pasta DIR] [--dono-casa DIR]     (stdlib, sem docker)

Sai 0 se nada disto acontece; sai 1 com uma linha por achado; 2 com uso errado. Falha:
- qualquer serviço com `ANTHROPIC_*`, `CLAUDE_CODE_OAUTH_TOKEN`, `CONVERSA_STT_*` ou `CONVERSA_TLS_TESTE` em qualquer
  linha (environment, env_file, labels…), ou com `voz.env` em qualquer linha;
- um bind de qualquer serviço do estado ou da config da ponte (`.local/state/office-conversa`, `.config/office-conversa`)
  ou, com `--dono-casa`, de uma pasta dentro da casa do dono ou que a contém (o `.claude` do dono, o estado, o voz.env);
- um bind de qualquer serviço do estado do guardião (`/var/lib/office-conversa`, com a chave) ou do código copiado
  (`/opt/office-conversa`), ou de uma pasta que os contém;
- da pasta da Conversa, o `office` a montar outra coisa além de `publico/` só de leitura em /data/conversa-publico, o
  `caddy` outra coisa além de `sock/` só de leitura em /run/office-conversa, ou outro serviço qualquer coisa (nunca
  `verificados/` nem `conversas/`, nem a pasta toda, nem uma que a contém).
A pasta da Conversa é `--pasta` (omissão /srv/office-conversa) e a pasta-mãe do que o `office` monta em
/data/conversa-publico e o `caddy` em /run/office-conversa. O leitor é o do verificar_compose_accoes.py (o que não sabe
ler não passa).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verificar_compose_accoes import _caminho, _conteudo, _dentro, forma, servicos, volumes  # noqa: E402

PASTA = "/srv/office-conversa"
# o único bind permitido da pasta da Conversa por serviço: (subpasta, destino), sempre só de leitura
PERMITIDO = {"office": ("publico", "/data/conversa-publico"), "caddy": ("sock", "/run/office-conversa")}
GUARDIAO = ("/var/lib/office-conversa", "/opt/office-conversa")
_ENV = re.compile(r"\b(ANTHROPIC_[A-Z0-9_]*|CLAUDE_CODE_OAUTH_TOKEN|CONVERSA_STT_[A-Z0-9_]*|CONVERSA_TLS_TESTE)\b")
_PONTE = ("/.local/state/office-conversa", "/.config/office-conversa")


def _contem(a: str, b: str) -> bool:
    # uma dentro da outra (nos dois sentidos)
    return _dentro(a, b) or _dentro(b, a)


def achados(texto: str, pasta: str = PASTA, dono_casa: str | None = None) -> list[str]:
    linhas = texto.split("\n")
    for ln in linhas:
        if _conteudo(ln) and re.search(r"(^|\s)(<<:|extends:|include:)", ln):
            return ["compose com <<:, extends: ou include: (não sei verificar; escreve os serviços por extenso)"]
    out = forma(linhas)
    if out:
        return out
    svs = servicos(linhas)
    binds = {s: [v for v in volumes(b) if v.get("type") == "bind"] for s, b in svs.items()}
    pastas = {os.path.normpath(pasta)}
    for s, (_, destino) in PERMITIDO.items():
        for v in binds.get(s, []):
            if v.get("target") == destino:
                pastas.add(os.path.dirname(_caminho(v["source"])))
    casa = os.path.normpath(dono_casa) if dono_casa else None
    for s, bloco in svs.items():
        for ln in bloco:
            if not _conteudo(ln):
                continue
            m = _ENV.search(ln)
            if m:
                out.append(f"serviço {s} tem {m.group(1)}")
            if "voz.env" in ln:
                out.append(f"serviço {s} monta o voz.env da Conversa")
            if "volumes_from" in ln:
                out.append(f"serviço {s} usa volumes_from (não sei verificar)")
        for v in binds[s]:
            src = _caminho(v.get("source", ""))
            if any(p in src + "/" for p in (x + "/" for x in _PONTE)):
                out.append(f"serviço {s} monta o estado da ponte da Conversa (bind {v['source']})")
            if casa and _contem(src, casa):
                out.append(f"serviço {s} monta a casa do dono (bind {v['source']})")
            if any(_contem(src, g) for g in GUARDIAO):
                out.append(f"serviço {s} monta o estado ou o código do guardião da Conversa (bind {v['source']})")
            for p in pastas:
                if not _contem(src, p):
                    continue
                if s not in PERMITIDO:
                    out.append(f"serviço {s} monta a pasta da Conversa (bind {v['source']})")
                    continue
                sub, destino = PERMITIDO[s]
                if src != os.path.join(p, sub) or v.get("target") != destino:
                    out.append(f"serviço {s} monta a pasta da Conversa fora de {sub}/ (bind {v['source']})")
                elif not v["ro"]:
                    out.append(f"serviço {s} monta {sub}/ sem ser só de leitura (bind {v['source']})")
    return list(dict.fromkeys(out))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    uso = "uso: verificar_compose_conversa.py <compose.yaml> [--pasta DIR] [--dono-casa DIR]"
    opcoes, resto = {"--pasta": PASTA, "--dono-casa": None}, []
    it = iter(argv)
    for a in it:
        if a in opcoes:
            v = next(it, None)
            if v is None or not v.startswith("/"):
                print(uso, file=sys.stderr)
                return 2
            opcoes[a] = v
        else:
            resto.append(a)
    if len(resto) != 1:
        print(uso, file=sys.stderr)
        return 2
    out = achados(Path(resto[0]).read_text(encoding="utf-8"), opcoes["--pasta"], opcoes["--dono-casa"])
    for a in out:
        print(a)
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
