"""Aplicador de decisões (ADR-001): fila JSON escrita pelo Office → pedidos `.md` da fonte `geral`.

Stdlib só, Python >= 3.10, corre no host. Só aplica sobre a versão que o utilizador viu (`sha256` igual ao do `.md`
actual, ou a versão seguinte se foi este aplicador a escrevê-la: «Aprovar todas», cliques seguidos). Actualiza o `.md`
com a gramática que `aprovacoes.py` lê e move o JSON para `feito/`; não aplicada → `feito/` + `<stem>.motivo.txt`
(uma linha) e o `.md` fica intacto. Sem commits, sem push, sem nada fora das pastas da config.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path

import seguro

FONTE = "geral"
CHAVES = {"v", "criado", "fonte", "ficheiro", "id", "accao", "nota", "sha256", "ip", "ua"}
ACCOES = {"aprovar": "aprovado", "recusar": "recusado"}
FILA_MAX = 16 * 1024
NOTA_MAX = 2000
HIST_N = 50
RE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}")
RE_NOME = re.compile(r"[\w.-]+\.md")
RE_LINHA = re.compile(r"- \[([A-Za-z0-9][A-Za-z0-9._-]{0,19})\] (.*)")
RE_FEITA = re.compile(r" → (aprovado|recusado) \(\d{4}-\d{2}-\d{2}\)$")
RE_FRONT = re.compile(r"---\n(.*?)\n---", re.DOTALL)
RE_FILA = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\.json")


class Recusa(ValueError):
    """Decisão não aplicada; a mensagem vai para o `.motivo.txt`."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ler(path: Path, maximo: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise Recusa("ficheiro ilegível") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Recusa("não é um ficheiro regular")
        with open(fd, "rb", closefd=False) as f:
            raw = f.read(maximo + 1)
    finally:
        os.close(fd)
    if len(raw) > maximo:
        raise Recusa("ficheiro grande demais")
    return raw


def frontmatter(texto: str) -> dict:
    m = RE_FRONT.match(texto)
    out = {}
    for linha in (m.group(1).splitlines() if m else []):
        k, _, v = linha.partition(":")
        out[k.strip()] = v.split("#")[0].strip()
    return out


def set_fm(texto: str, chave: str, valor: str) -> str:
    m = RE_FRONT.match(texto)
    if not m:
        return f"---\n{chave}: {valor}\n---\n" + texto
    linhas = m.group(1).splitlines()
    for i, linha in enumerate(linhas):
        if linha.partition(":")[0].strip() == chave:
            linhas[i] = f"{chave}: {valor}"
            break
    else:
        linhas.append(f"{chave}: {valor}")
    return "---\n" + "\n".join(linhas) + "\n---" + texto[m.end():]


def decisoes(texto: str) -> list[dict]:
    """[{id, texto, estado|None, n}] das secções «## Decisões» (1.º id repetido ganha, como o Office)."""
    out, vistos, dentro = [], set(), False
    for n, linha in enumerate(texto.split("\n")):
        if linha.startswith("## "):
            dentro = linha.strip() == "## Decisões"
            continue
        m = RE_LINHA.fullmatch(linha) if dentro else None
        if m and m.group(1) not in vistos:
            vistos.add(m.group(1))
            f = RE_FEITA.search(m.group(2))
            out.append({"id": m.group(1), "texto": RE_FEITA.sub("", m.group(2)),
                        "estado": f.group(1) if f else None, "n": n})
    return out


def aplicar(d, pasta: Path, hist: dict, nome_utilizador: str, quando: datetime) -> str:
    """Aplica uma decisão ao `.md`. Devolve a linha para o journal; Recusa se não se puder aplicar."""
    if not isinstance(d, dict) or set(d) != CHAVES or type(d["v"]) is not int or d["v"] != 1:
        raise Recusa("formato inválido")
    fonte, nome, did, accao, nota = d["fonte"], d["ficheiro"], d["id"], d["accao"], d["nota"]
    if fonte != FONTE or not isinstance(nome, str) or not RE_NOME.fullmatch(nome) or nome == "README.md":
        raise Recusa("pedido inválido")
    if accao not in ("aprovar", "recusar", "comentar") or not isinstance(did, str) \
            or (did != "*" and not RE_ID.fullmatch(did)):
        raise Recusa("acção ou id inválidos")
    nota = nota.strip()[:NOTA_MAX] if isinstance(nota, str) else ""
    if accao in ("recusar", "comentar") and not nota:
        raise Recusa(f"{accao} sem nota")
    p = pasta / nome
    raw = _ler(p, 1024 * 1024)
    actual, chave = _sha(raw), f"{fonte}/{nome}"
    h = hist.get(chave, [])
    if d["sha256"] != actual and not (h and h[-1] == actual and d["sha256"] in h):
        raise Recusa("sha256 diferente: o pedido mudou depois de ser visto")
    try:
        texto = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Recusa("pedido ilegível") from None
    fm = frontmatter(texto)
    if not fm.get("estado", "").startswith(("pendente", "parcial")):
        raise Recusa("o pedido já não está pendente")
    ds = decisoes(texto)
    dia = quando.date().isoformat()
    alvo = None
    if did != "*":
        alvo = next((x for x in ds if x["id"] == did), None)
        if alvo is None:
            raise Recusa(f"a decisão {did} não existe")
        if alvo["estado"] and accao != "comentar":
            raise Recusa(f"a decisão {did} já estava {alvo['estado']}")
    elif ds and accao != "comentar":
        raise Recusa("o pedido tem decisões com id: decide uma a uma")
    carimbo = f"{quando:%d/%m/%Y %H:%M}, via Office"
    if accao == "comentar":
        texto = texto.rstrip("\n") + (f"\n\n## Observação de {nome_utilizador} ({carimbo})"
                                      f"{' · ' + did if did != '*' else ''}\n{nota}\n")
        linha = f"observação registada em {nome}"
    else:
        estado = ACCOES[accao]
        if alvo is not None:
            linhas = texto.split("\n")
            linhas[alvo["n"]] = linhas[alvo["n"]].rstrip() + f" → {estado} ({dia})"
            texto = "\n".join(linhas)
            ds = decisoes(texto)
            if all(x["estado"] for x in ds):
                novo = "recusado" if all(x["estado"] == "recusado" for x in ds) else "aprovado"
            else:
                novo = f"parcial ({sum(1 for x in ds if x['estado'])} de {len(ds)} decididas)"
        else:
            novo = estado
        texto = set_fm(set_fm(texto, "estado", novo), "decidido", dia)
        texto = texto.rstrip("\n") + (f"\n\n## Decisão de {nome_utilizador} ({carimbo})\n"
                                      f"- {did if did != '*' else 'Pedido'}: **{estado}**"
                                      + (f" — {alvo['texto']}" if alvo else "")
                                      + (f"\n- Nota: {nota}" if nota else "") + "\n")
        linha = f"{estado}: {nome} ({did})"
    novo_raw = texto.encode("utf-8")
    tmp = p.with_name(f".{nome}.tmp")
    tmp.write_bytes(novo_raw)
    os.replace(tmp, p)
    hist[chave] = ((h if h and h[-1] == actual else [actual]) + [_sha(novo_raw)])[-HIST_N:]
    return linha


def processar(fila: Path, pasta: Path, hist_path: Path, nome_utilizador: str, quando: datetime) -> list[str]:
    """Uma passagem pela fila (mais antigo primeiro). Devolve uma linha por decisão tratada.

    A fila é escrita pelo contentor: tudo relativo a fds da fila e de `feito/` (sem seguir symlinks, ver seguro.py)."""
    try:
        dfd = seguro.abrir_pasta(fila)
    except OSError:
        return []
    try:
        nomes = sorted(n for n in os.listdir(dfd) if RE_FILA.fullmatch(n))
        if not nomes:
            return []
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
            hist = hist if isinstance(hist, dict) else {}
        except (OSError, ValueError):
            hist = {}
        try:
            ffd = seguro.subpasta(dfd, "feito")
        except OSError:
            return ["decisões não processadas: decisoes-fila/feito não é uma pasta"]
        linhas, mudou = [], False
        try:
            for nome in nomes:
                try:
                    try:
                        d = json.loads(_ler(fila / nome, FILA_MAX).decode("utf-8"))
                    except ValueError:
                        raise Recusa("JSON ilegível") from None
                    linhas.append(aplicar(d, pasta, hist, nome_utilizador, quando))
                    mudou = True
                except (Recusa, OSError) as e:
                    motivo = str(e) if isinstance(e, Recusa) else "erro a ler ou escrever o pedido"
                    try:
                        seguro.escrever(ffd, f"{nome[:-5]}.motivo.txt",
                                        (motivo.replace("\n", " ") + "\n").encode("utf-8"))
                    except OSError:
                        pass
                    linhas.append(f"decisão {nome} não aplicada: {motivo}")
                try:
                    seguro.mover(dfd, nome, ffd)
                except OSError:
                    linhas.append(f"decisão {nome}: não foi possível movê-la para feito/")
        finally:
            os.close(ffd)
    finally:
        os.close(dfd)
    if mudou:
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = hist_path.with_name(f".{hist_path.name}.tmp")
        tmp.write_text(json.dumps(hist), encoding="utf-8")
        os.replace(tmp, hist_path)
    return linhas
