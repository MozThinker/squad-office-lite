"""Pedidos de acções propostas pelas squads de negócio (ADR-021). Corre no runner, sem IA nem credenciais:

- `propor`: no fim de cada execução (tarefa ou rotina) de uma squad de `agentes/squads.json` com modelo de negócio,
  extrai os blocos `=====ACCAO=====` do texto final, valida-os (accoes.validar com a vista sem segredos que o serviço
  `accoes` publica em `<dados>/accoes-entrada/<squad>/vista.json`), numera as válidas [A1]…[A10] pela ordem do texto
  e escreve com O_EXCL o registo `decisoes/.accoes/accoes-<squad>-<AAAAMMDD>-<n>.json` (definições canónicas e sha) e
  depois o pedido `decisoes/accoes-<squad>-<AAAAMMDD>-<n>.md` (formato das Aprovações; se o `.md` falhar, o registo
  sai). Recusadas e excedentes ficam listadas no pedido, sem linha [An] nem registo. Sem blocos, nada.
- `purgar`: pedidos de acções com mais de 90 dias perdem o texto citado, o remetente e o assunto (ids, marcas e sha
  ficam) e o registo `.accoes/` sai.

O dia do nome, o `criado` e a validade vêm no fuso do `office.yaml`, gravado no registo (o serviço não lê o
`office.yaml`).
"""
from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import agentes_copia

import accoes
import agentes
import officecfg

APAGADO = "[apagado aos 90 dias]"
PEDIDOS_DIA = 99
_DECISAO = re.compile(r"- \[(A\d{1,2})\] .*? · (e-mail|folha|aviso) · 0 US\$((?: → (?:aprovado|recusado) "
                      r"\(\d{4}-\d{2}-\d{2}\))?)")
_APLICADOR = ("## Decisão de ", "## Observação de ")   # o que runner/decisoes.aplicar acrescenta
_DEC_APLICADA = re.compile(r"- ((?:A\d{1,2}|Pedido): \*\*(?:aprovado|recusado)\*\*)(?: — .*)?")


def _vista(cfg, sid: str) -> dict:
    p = officecfg.raiz(cfg, "dados") / agentes_copia.ACCOES_ENTRADA / sid / "vista.json"
    return accoes.validar_vista(accoes.ler_json(p, accoes.VISTA_MAX)) or accoes.contexto_omissao()


def _novo(path: Path, dados: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with open(fd, "wb") as f:
        os.fchmod(f.fileno(), 0o644)   # o umask não decide: o serviço accoes (outro uid) lê-o
        f.write(dados)


def texto_pedido(sid: str, tid: str, agora: datetime, fuso: str, linhas: list[str], secoes: list[str],
                 recusadas: list[str]) -> str:
    n = len(linhas)
    ate = agora + accoes.VALIDADE
    estado = "pendente" if linhas else "informativo"
    return (f"---\nestado: {estado}\ncriado: {agora:%Y-%m-%d}\n---\n"
            f"# Acções propostas pela squad {sid}\n"
            f"## O quê\nA squad {sid} propôs {n} {'acção' if n == 1 else 'acções'} na tarefa {tid}, criadas a "
            f"{agora:%Y-%m-%d %H:%M} (no fuso {fuso}).\n"
            f"Valem até {ate:%Y-%m-%d %H:%M} (48 h) e só são executadas se as aprovares aqui, uma a uma. Para mudar o "
            f"texto, recusa e pede de novo à squad.\n"
            f"## Decisões\n" + "".join(f"{ln}\n" for ln in linhas)
            + "## Definição das acções\n" + "".join(f"{s}\n" for s in secoes)
            + "## Recusadas e de fora\n" + ("".join(f"{r}\n" for r in recusadas) or "- nenhuma\n"))


def propor(cfg: dict, squad: str, tid: str, texto: str | None, now: float) -> list[str]:
    """Escreve o pedido de acções da execução `tid`. Devolve avisos para os `issues` da tarefa ([] se nada a fazer
    ou tudo bem). Squad fora do registo, ou com modelo que não é de negócio, ou texto sem blocos: nada."""
    registo = agentes.ler_registo(officecfg.caminho(cfg, "agentes") / "squads.json")
    modelo = agentes.modelo_negocio(registo, squad)
    if modelo is None:
        return []
    blocos = accoes.extrair(texto or "")
    if not blocos:
        return []
    ctx = _vista(cfg, squad)
    agora = datetime.fromtimestamp(now, ZoneInfo(cfg["fuso"])).replace(microsecond=0)
    criado = agora.isoformat()
    validas, recusadas, shas = [], [], set()
    for i, b in enumerate(blocos, 1):
        p, motivo = accoes.validar(b, squad, modelo, ctx)
        t = accoes.tipo_de(b)
        rotulo = f"- proposta {i}" + (f" ({t})" if t else "")
        if motivo:
            recusadas.append(f"{rotulo}: {motivo}")
            continue
        d = accoes.definicao(squad, p, criado)
        h = accoes.sha(d)
        if h in shas:
            recusadas.append(f"{rotulo}: {accoes.M_REPETIDA}")
        elif len(validas) >= accoes.MAX_POR_EXECUCAO:
            recusadas.append(f"{rotulo}: {accoes.M_DE_FORA}")
        else:
            shas.add(h)
            validas.append((d, h))
    linhas, secoes, defs = [], [], []
    for n, (d, h) in enumerate(validas, 1):
        aid = f"A{n}"
        rem = ctx["mensagens"][d["origem"]]["remetente"] if d["origem"] else None
        linhas.append(accoes.linha_decisao(aid, d, rem))
        secoes.append(accoes.secao(aid, d, h))
        defs.append({"id": aid, "sha256": h, "def": d})
    md = texto_pedido(squad, tid, agora, cfg["fuso"], linhas, secoes, recusadas).encode("utf-8")
    pasta = officecfg.caminho(cfg, "decisoes")
    regs = pasta / accoes.REGISTOS
    if defs and not regs.is_dir():
        regs.mkdir()
        os.chmod(regs, 0o755)   # o serviço accoes (outro uid) lê os registos
    # o registo primeiro (O_EXCL), depois o .md: o executor nunca vê um pedido aprovável sem registo; se o .md
    # falhar, o registo sai
    for n in range(1, PEDIDOS_DIA + 1):
        nome = f"accoes-{squad}-{agora:%Y%m%d}-{n}.md"
        reg = regs / f"{nome[:-3]}.json"
        if os.path.lexists(pasta / nome):
            continue
        if defs:
            doc = {"v": 1, "pedido": nome, "squad": squad, "tarefa": tid, "criado": criado, "fuso": cfg["fuso"],
                   "accoes": defs}
            try:
                _novo(reg, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
            except FileExistsError:
                continue
        try:
            _novo(pasta / nome, md)
            return [f"acções: {len(recusadas)} recusadas ou de fora (ver decisoes/{nome})"] if recusadas else []
        except BaseException as e:
            if defs:
                reg.unlink(missing_ok=True)
            if isinstance(e, FileExistsError):
                continue
            raise
    return [f"acções: mais de {PEDIDOS_DIA} pedidos de acções hoje; pedido não gravado"]


def _purgar_md(texto: str) -> str:
    """Texto citado, remetente/assunto das linhas [An] e o que o aplicador acrescentou (texto da decisão, notas e
    observações) passam a «[apagado aos 90 dias]»; ids, estados, marcas e sha ficam."""
    out, secao = [], ""
    for ln in texto.split("\n"):
        if ln.startswith("## "):
            secao = ln
            out.append(ln)
            continue
        apag = None
        if ln.startswith(">"):
            apag = f"> {APAGADO}"
        elif secao.startswith(_APLICADOR) and ln.strip():
            m = _DEC_APLICADA.fullmatch(ln)
            if m:
                out.append(f"- {m.group(1)} — {APAGADO}" if " — " in ln else ln)
                continue
            apag = f"- Nota: {APAGADO}" if ln.startswith("- Nota:") else APAGADO
        if apag is not None:
            if not (out and out[-1] == apag):
                out.append(apag)
            continue
        m = _DECISAO.fullmatch(ln) if secao == "## Decisões" else None
        out.append(f"- [{m.group(1)}] {APAGADO} · {m.group(2)} · 0 US${m.group(3)}" if m else ln)
    return "\n".join(out)


def registo_legivel(cfg: dict) -> None:
    """`agentes/squads.json` a 0644 (instalações 0.4.0 gravavam-no a 0600): o serviço `accoes`, outro uid, lê-o para
    saber se a squad está activa (ADR-021). Sem seguir symlinks; um erro não pára a passagem."""
    try:
        fd = os.open(officecfg.caminho(cfg, "agentes") / "squads.json", os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode) and stat.S_IMODE(os.fstat(fd).st_mode) != 0o644:
            os.fchmod(fd, 0o644)
    except OSError:
        pass
    finally:
        os.close(fd)


def purgar(cfg: dict, now: float) -> list[str]:
    """Pedidos de acções com mais de 90 dias (pela data do nome, no fuso do office.yaml): texto apagado no `.md` e
    registo `.accoes/` removido. Idempotente; uma linha de log se mudou alguma coisa."""
    pasta = officecfg.caminho(cfg, "decisoes")
    limite = datetime.fromtimestamp(now, ZoneInfo(cfg["fuso"])).date() - timedelta(days=accoes.RETENCAO_DIAS)
    n = 0
    for sub, ext in ((pasta, ".md"), (pasta / accoes.REGISTOS, ".json")):
        try:
            nomes = sorted(e.name for e in os.scandir(sub) if e.is_file(follow_symlinks=False))
        except OSError:
            continue
        for nome in nomes:
            m = accoes.PEDIDO.fullmatch(nome[:-len(ext)] + ".md") if nome.endswith(ext) else None
            try:
                velho = m is not None and datetime.strptime(m.group(2), "%Y%m%d").date() < limite
            except ValueError:
                velho = False
            if not velho:
                continue
            if ext == ".json":
                (sub / nome).unlink(missing_ok=True)
                n += 1
                continue
            dados = accoes.ler_bytes(sub / nome, 1024 * 1024)
            if dados is None:
                continue
            novo = _purgar_md(dados.decode("utf-8", "replace")).encode("utf-8")
            if novo != dados:
                accoes.escrever_atomico(sub / nome, novo, os.stat(sub / nome).st_mode & 0o777)
                n += 1
    return [f"acções: {n} pedidos ou registos purgados (mais de {accoes.RETENCAO_DIAS} dias)"] if n else []
