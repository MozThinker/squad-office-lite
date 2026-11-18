"""Executor das acções aprovadas (ADR-021). Corre no serviço `accoes` (outro utilizador, o único com o `accoes.env`).

Em cada passagem (flock não bloqueante: a 2.ª em paralelo sai logo), para cada linha `[An] → aprovado` de um pedido
`decisoes/accoes-*.md` com registo `decisoes/.accoes/*.json` de há ≤ 7 dias, só chama o serviço se: a squad está em
`agentes/squads.json` com modelo de negócio; a acção está ligada e o conector configurado; o sha do registo bate com a
definição e a secção do `.md` é byte a byte a re-renderizada; ≤ 48 h desde a criação; a mensagem de origem está no
registo do colector; abaixo do limite do dia (tentativas, no fuso gravado no registo do pedido); o conteúdo volta a
passar pelas regras do runner com a configuração de agora; o sha nunca foi executado (marcador O_EXCL criado ANTES da
chamada). `email_responder` fica `a_enviar` 2 min (anulável) e vai só para o remetente registado pelo colector.
Livro `<ACCOES_DIR>/AAAA-MM.jsonl` sem corpo, assunto, endereços nem valores.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import imaplib
import io
import json
import os
import re
import smtplib
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import accoes

IMAP = None   # nos testes, uma classe falsa com a interface do imaplib
TERMINAIS = ("executada", "erro", "caducada", "anulada", "desfeita", "recusada")
PEDIDO_DIAS = 7
SHA = re.compile(r"[0-9a-f]{64}")
LIVRO = re.compile(r"\d{4}-\d{2}\.jsonl")
CHAVES_LIVRO = ("hora", "squad", "tipo", "sha", "pedido", "linha", "aprovado", "estado", "motivo", "id")


class ErroServico(Exception):
    """Erro com motivo fixo (sem texto do serviço nem da squad)."""


def _dt(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None
    return d if d is not None and d.tzinfo is not None else None


def _fuso(nome) -> ZoneInfo | None:
    try:
        return ZoneInfo(nome) if isinstance(nome, str) and nome else None
    except (ValueError, KeyError, OSError):
        return None


def trancar(r: accoes.Raizes, bloquear: bool) -> int | None:
    fd = os.open(r.dir / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if bloquear else fcntl.LOCK_NB))
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


# --- livro -------------------------------------------------------------------------------------------------------------
def _ler(p: Path) -> list[dict]:
    raw = accoes.ler_bytes(p, 64 * 1024 * 1024)
    out = []
    for ln in (raw or b"").decode("utf-8", "replace").splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("sha"), str):
            out.append(d)
    return out


def ler_livro(r: accoes.Raizes, meses: int | None = 2) -> list[dict]:
    """Os `meses` ficheiros mais recentes (o corrente e o anterior chegam para 7 dias de pedidos); None = todos."""
    try:
        nomes = sorted(e.name for e in os.scandir(r.dir) if LIVRO.fullmatch(e.name))
    except OSError:
        return []
    return [d for n in (nomes[-meses:] if meses else nomes) for d in _ler(r.dir / n)]


def escrever_livro(r: accoes.Raizes, livro: list, agora: datetime, base: dict, estado: str, motivo=None,
                   ident=None) -> None:
    """`agora` já no fuso do pedido: a hora e o mês do ficheiro saem nele."""
    linha = {"hora": agora.isoformat(timespec="seconds"), **base, "estado": estado, "motivo": motivo, "id": ident}
    fd = os.open(r.dir / f"{agora:%Y-%m}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False) + "\n")
    livro.append(linha)


def _limite_dia(e: dict) -> bool:
    return e.get("estado") == "recusada" and str(e.get("motivo") or "").startswith("limite do dia")


def _terminal(livro: list, sha: str) -> bool:
    return any(e["sha"] == sha and e.get("estado") in TERMINAIS and not _limite_dia(e) for e in livro)


# --- conectores --------------------------------------------------------------------------------------------------------
def _q(pasta: str) -> str:
    return '"' + pasta + '"'   # a configuração recusa `"` e `\` nas pastas


def ligar_imap(conta: dict):
    s = conta["imap"]
    if IMAP is not None:
        return IMAP(s["host"], s["porta"])
    return (imaplib.IMAP4_SSL if s["tls"] else imaplib.IMAP4)(s["host"], s["porta"], timeout=30)


def login(sid: str, seg: dict) -> tuple[str, str]:
    k = accoes.chave_sq(sid)
    return seg[f"{k}_EMAIL_UTILIZADOR"], seg[f"{k}_EMAIL_SENHA"]


def _email(conta: dict, defn: dict, msg_reg: dict, sha: str, agora: datetime) -> EmailMessage:
    m = EmailMessage()
    m["From"] = conta["endereco"]
    m["To"] = msg_reg["remetente"]   # o From registado pelo colector, nunca do texto da squad nem do Reply-To
    m["Subject"] = defn["campos"]["assunto"]
    m["Date"] = format_datetime(agora)
    m["Message-ID"] = f"<{sha[:16]}@office-accoes>"
    mid = msg_reg.get("message_id")
    if isinstance(mid, str) and re.fullmatch(r"<[^<>\s]{1,250}>", mid):
        m["In-Reply-To"] = mid
        m["References"] = mid
    m.set_content(defn["campos"]["corpo"])
    return m


def smtp_ligar(conta: dict):
    s = conta["smtp"]
    return (smtplib.SMTP_SSL if s["tls"] else smtplib.SMTP)(s["host"], s["porta"], timeout=30)


def smtp_enviar(conta: dict, sid: str, seg: dict, m: EmailMessage, para: str) -> str:
    with smtp_ligar(conta) as c:
        c.login(*login(sid, seg))
        c.send_message(m, from_addr=conta["endereco"], to_addrs=[para])
    return m["Message-ID"]


def imap_rascunho(conta: dict, sid: str, seg: dict, m: EmailMessage) -> str:
    c = ligar_imap(conta)
    try:
        c.login(*login(sid, seg))
        _, dados = c.append(_q(conta["rascunhos"]), r"(\Draft)", imaplib.Time2Internaldate(time.time()), m.as_bytes())
    finally:
        c.logout()
    r = re.search(rb"\[APPENDUID (\d+) (\d+)\]", dados[0] if dados and isinstance(dados[0], bytes) else b"")
    if not r:
        raise ErroServico("servidor sem UIDPLUS")
    return f"{int(r.group(1))}:{int(r.group(2))}"


def imap_apagar(conta: dict, sid: str, seg: dict, ident: str) -> None:
    uv, uid = ident.split(":")
    c = ligar_imap(conta)
    try:
        c.login(*login(sid, seg))
        c.select(_q(conta["rascunhos"]))
        _, d = c.response("UIDVALIDITY")
        if not d or not d[0] or d[0].decode() != uv:
            raise ErroServico("a pasta de rascunhos mudou (UIDVALIDITY)")
        for cmd in (("STORE", uid, "+FLAGS", r"(\Deleted)"), ("EXPUNGE", uid)):
            typ, _ = c.uid(*cmd)
            if typ != "OK":
                raise ErroServico(f"o servidor recusou o {cmd[0]}")
    finally:
        c.logout()


def _linha_csv(valores: list[str]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_ALL).writerow(valores)
    return buf.getvalue().encode("utf-8")


def _ler_folha(p: Path) -> tuple[bytes, int]:
    """(bytes, modo) da folha, sem seguir symlinks; ausente → (b"", 0o644)."""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return b"", 0o644
    with open(fd, "rb") as f:
        return f.read(), os.fstat(f.fileno()).st_mode & 0o777


def csv_acrescentar(nome: str, folha: dict, valores: dict) -> str:
    p = Path(folha["ficheiro"])
    dados, _ = _ler_folha(p)
    pre = b"" if not dados or dados.endswith(b"\n") else b"\n"
    cab = b"" if dados else _linha_csv(folha["colunas"])
    linha = _linha_csv([accoes.celula(valores.get(c, "")) for c in folha["colunas"]])
    n = (dados + pre + cab).count(b"\n")
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    with open(fd, "wb") as f:
        f.write(pre + cab + linha)
    return f"{nome}:{n}:{hashlib.sha256(linha).hexdigest()}"


def csv_remover(folha: dict, n: int, h: str) -> None:
    """Remove a linha com o hash registado: a da posição registada se casar, senão a última ocorrência acima dela
    (desfazer linhas anteriores sobe-a); nenhuma → recusa sem mexer."""
    p = Path(folha["ficheiro"])
    dados, modo = _ler_folha(p)
    partes = dados.split(b"\n")
    achadas = [i for i in range(min(n, len(partes) - 2) + 1) if hashlib.sha256(partes[i] + b"\n").hexdigest() == h]
    if not achadas:
        raise ErroServico("a linha foi mudada à mão")
    del partes[achadas[-1]]   # a da posição n, se casar, é a última
    accoes.escrever_atomico(p, b"\n".join(partes), modo)


def telegram(tg: dict, token: str, metodo: str, dados: dict | None = None) -> dict:
    req = urllib.request.Request(f"{tg['url'].rstrip('/')}/bot{token}/{metodo}",
                                 data=json.dumps(dados or {}).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read(64 * 1024))
    except urllib.error.HTTPError as e:
        e.close()
        raise ErroServico(f"HTTPError {e.code}") from None
    if not isinstance(d, dict) or not d.get("ok"):
        raise ErroServico("o Telegram recusou")
    return d


def telegram_enviar(tg: dict, token: str, texto: str) -> str:
    d = telegram(tg, token, "sendMessage", {"chat_id": tg["chat_id"], "text": texto, "disable_web_page_preview": True})
    return str(d["result"]["message_id"])


def motivo_erro(e: Exception) -> str:
    if isinstance(e, ErroServico):
        return str(e)
    cod = getattr(e, "smtp_code", None) or getattr(e, "code", None)
    return f"{type(e).__name__} {cod}" if isinstance(cod, int) else type(e).__name__


# --- passagem ----------------------------------------------------------------------------------------------------------
def ler_squads(r: accoes.Raizes) -> dict[str, str] | None:
    """{id: modelo} das squads de negócio do registo do runner; None se ilegível (sem permissões: espera)."""
    raw = accoes.ler_bytes(r.agentes / "squads.json", accoes.REGISTO_MAX)
    if raw is None:
        return None
    try:
        return accoes.squads_negocio(json.loads(raw.decode("utf-8")))
    except ValueError:
        return None


def _pedidos(r: accoes.Raizes, agora: datetime):
    """(registo do pedido, texto do .md) dos pedidos criados há ≤ 7 dias; os mais antigos nem se lêem."""
    pasta = r.decisoes / accoes.REGISTOS
    try:
        nomes = sorted(e.name for e in os.scandir(pasta) if e.name.endswith(".json"))
    except OSError:
        return
    desde = (agora - timedelta(days=PEDIDO_DIAS + 1)).strftime("%Y%m%d")
    for n in nomes:
        m = accoes.PEDIDO.fullmatch(n[:-5] + ".md")
        if not m or m.group(2) < desde:
            continue
        rp = accoes.ler_json(pasta / n, 1024 * 1024)
        if not isinstance(rp, dict) or rp.get("v") != 1 or rp.get("pedido") != n[:-5] + ".md" \
                or rp.get("squad") != m.group(1) or not isinstance(rp.get("accoes"), list):
            continue
        criado, tz = _dt(rp.get("criado")), _fuso(rp.get("fuso"))
        if criado is None or tz is None or agora - criado > timedelta(days=PEDIDO_DIAS):
            continue
        dados = accoes.ler_bytes(r.decisoes / rp["pedido"], 1024 * 1024)
        if dados is None:
            continue
        yield rp, tz, dados.decode("utf-8", "replace")


def _conector_ok(cfg: dict, seg: dict, sid: str, defn: dict) -> bool:
    sq = cfg["squads"].get(sid) or {}
    if defn["tipo"] in accoes.EMAIL:
        k = accoes.chave_sq(sid)
        return "email" in sq and f"{k}_EMAIL_UTILIZADOR" in seg and f"{k}_EMAIL_SENHA" in seg
    if defn["tipo"] == "aviso_dono":
        return "telegram" in cfg and bool(seg.get("TELEGRAM_TOKEN"))
    folha = (defn.get("campos") or {}).get("folha")
    return isinstance(folha, str) and folha in sq.get("folhas", {})


def _secao_ok(secao_md: str, aid: str, defn: dict, sha: str) -> bool:
    try:
        return secao_md == accoes.secao(aid, defn, sha)
    except (KeyError, TypeError, AttributeError):
        return False


def _vista_msg(msg: dict | None) -> dict:
    if msg is None:
        return {}
    return {msg["id"]: {"remetente": msg["remetente"], "autenticada": msg.get("autenticado") is True}}


def _revalidar(defn: dict, sid: str, modelo: str, cfg: dict, perfil: str, msg: dict | None) -> str | None:
    """O conteúdo volta a passar pelas regras do runner (catálogo, acção ligada, campos exactos, tamanhos, HTML,
    links, origem autenticada, terceiros) com a configuração e o registo de agora; um registo forjado com sha
    coerente não passa. Motivo da recusa ou None."""
    c = defn.get("campos")
    if not isinstance(c, dict):
        return accoes.M_DEFINICAO
    p = {"tipo": defn["tipo"], **c}
    if defn["tipo"] in accoes.EMAIL:
        p["origem"] = defn.get("origem")
    prop, motivo = accoes.validar(json.dumps(p, ensure_ascii=False), sid, modelo,
                                  accoes.contexto(cfg, sid, _vista_msg(msg), perfil))
    if motivo:
        return motivo
    return None if accoes.definicao(sid, prop, defn.get("criado")) == defn else accoes.M_DEFINICAO


def _executar(ctx: dict, rp: dict, tz: ZoneInfo, acc: dict, aprovado: str | None, md: str) -> None:
    r, cfg, seg, livro, perfil = ctx["r"], ctx["cfg"], ctx["seg"], ctx["livro"], ctx["perfil"]
    agora = ctx["agora"].astimezone(tz)
    sha, defn, aid, sid = acc.get("sha256"), acc.get("def"), acc.get("id"), rp["squad"]
    if not isinstance(sha, str) or not SHA.fullmatch(sha) or not isinstance(defn, dict) or \
            not isinstance(aid, str) or not re.fullmatch(r"A\d{1,2}", aid) or _terminal(livro, sha):
        return
    tipo = defn.get("tipo") if defn.get("tipo") in accoes.TIPOS else None
    base = {"squad": sid, "tipo": tipo, "sha": sha, "pedido": rp["pedido"], "linha": aid, "aprovado": aprovado}

    def fim(estado, motivo=None, ident=None):
        escrever_livro(r, livro, agora, base, estado, motivo, ident)

    modelo = ctx["squads"].get(sid)
    if modelo is None:
        return fim("recusada", accoes.M_INACTIVA)
    if tipo is None or tipo not in accoes.ligadas(cfg, sid, perfil):
        return fim("recusada", accoes.M_NAO_LIGADA)
    secao_md = accoes.secao_do_md(md, aid)
    if secao_md is None:   # .md a meio da escrita ou sem a secção: espera, sem livro
        return None
    if defn.get("squad") != sid or accoes.sha(defn) != sha or not _secao_ok(secao_md, aid, defn, sha):
        if f"{secao_md}\n### " not in md and f"{secao_md}\n## " not in md:
            return None   # secção sem fim (o .md acaba nela): pode estar a meio da escrita; espera sem livro
        return fim("recusada", accoes.M_DEFINICAO)
    estado = r.dir / "estado"
    criado = _dt(defn.get("criado"))
    if criado is None or agora - criado > accoes.VALIDADE:
        (estado / "a_enviar" / f"{sha}.json").unlink(missing_ok=True)
        return fim("caducada", accoes.M_CADUCADA)
    if not _conector_ok(cfg, seg, sid, defn):   # C5: aprovada antes do conector configurado espera, sem livro
        return None
    msg = None
    if tipo in accoes.EMAIL:
        msg = accoes.mensagem(r.dir, defn.get("origem"), sid)
        if msg is None:
            return fim("recusada", accoes.M_ORIGEM)
    lim = accoes.limite(cfg, sid, tipo)
    hoje = [e for e in livro if e.get("squad") == sid and e.get("tipo") == tipo
            and (d := _dt(e.get("hora"))) is not None and d.astimezone(tz).date() == agora.date()]
    if sum(e.get("estado") in ("executada", "erro") for e in hoje) >= lim:
        if not any(e["sha"] == sha and _limite_dia(e) for e in hoje):
            fim("recusada", accoes.m_limite(lim))
        return None
    if tipo == "email_responder":
        espera = estado / "a_enviar" / f"{sha}.json"
        if (estado / "anular" / sha).exists():
            espera.unlink(missing_ok=True)
            return fim("anulada")
        if not espera.exists():
            espera.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(espera, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with open(fd, "w") as f:
                f.write(json.dumps({"hora": agora.isoformat(timespec="seconds")}))
            return fim("a_enviar")
        desde = _dt((accoes.ler_json(espera, 4096) or {}).get("hora"))
        if desde is None or (agora - desde).total_seconds() < accoes.ESPERA_S:
            return None
        espera.unlink(missing_ok=True)
    motivo = _revalidar(defn, sid, modelo, cfg, perfil, msg)
    if motivo:
        return fim("recusada", motivo)
    (estado / "executados").mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(estado / "executados" / sha, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
    except FileExistsError:
        return fim("erro", "interrompida")
    sq = cfg["squads"].get(sid) or {}
    try:
        if tipo == "email_responder":
            ident = smtp_enviar(sq["email"], sid, seg, _email(sq["email"], defn, msg, sha, agora), msg["remetente"])
        elif tipo == "email_rascunho":
            ident = imap_rascunho(sq["email"], sid, seg, _email(sq["email"], defn, msg, sha, agora))
        elif tipo == "folha_linha":
            c = defn["campos"]
            ident = csv_acrescentar(c["folha"], sq["folhas"][c["folha"]], c["valores"])
        else:
            ident = telegram_enviar(cfg["telegram"], seg["TELEGRAM_TOKEN"], defn["campos"]["texto"])
    except Exception as e:  # noqa: BLE001 - qualquer falha do serviço fica no livro (e conta para o limite)
        return fim("erro", motivo_erro(e))
    return fim("executada", ident=ident)


def passagem(r: accoes.Raizes, agora: datetime, perfil: str = accoes.PERFIL) -> list[str]:
    """Uma passagem do executor; devolve avisos (sem valores) para o stderr."""
    fd = trancar(r, bloquear=False)
    if fd is None:   # outra passagem a correr
        return []
    try:
        squads = ler_squads(r)
        if squads is None:   # sem permissões ou a meio de uma escrita: não se recusa nada, espera
            return ["registo de squads ilegível; nada executado nesta passagem"]
        ctx = {"r": r, "agora": agora, "cfg": accoes.ler_config(r.config), "seg": accoes.ler_segredos(r.env),
               "livro": ler_livro(r), "squads": squads, "perfil": perfil}
        for rp, tz, md in _pedidos(r, agora):
            decs = accoes.decisoes_md(md)
            for acc in rp["accoes"]:
                estado, data = decs.get(acc.get("id"), (None, None)) if isinstance(acc, dict) else (None, None)
                if estado == "aprovado":
                    _executar(ctx, rp, tz, acc, data, md)
    finally:
        os.close(fd)
    return []


# --- comandos ----------------------------------------------------------------------------------------------------------
def anular(r: accoes.Raizes, sha: str) -> tuple[int, str]:
    """Marca uma `email_responder` em espera para não sair; o executor regista `anulada` na passagem seguinte."""
    if not SHA.fullmatch(sha or ""):
        return 1, "sha inválido"
    fd = trancar(r, bloquear=True)
    try:
        if any(e["sha"] == sha and e.get("estado") == "executada" for e in ler_livro(r, None)):
            return 1, "já foi enviada: não se anula"
        (r.dir / "estado/anular").mkdir(parents=True, exist_ok=True)
        (r.dir / "estado/anular" / sha).touch()
    finally:
        os.close(fd)
    return 0, "anulada: não sai na próxima passagem"


def desfazer(r: accoes.Raizes, sha: str, agora: datetime) -> tuple[int, str]:
    """Apaga o rascunho criado (UID do livro) ou remove a linha acrescentada à folha; regista `desfeita`."""
    if not SHA.fullmatch(sha or ""):
        return 1, "sha inválido"
    fd = trancar(r, bloquear=True)
    try:
        livro = ler_livro(r, None)
        es = [e for e in livro if e["sha"] == sha]
        if any(e.get("estado") == "desfeita" for e in es):
            return 0, "já estava desfeita: nada a fazer"
        ex = next((e for e in es if e.get("estado") == "executada"), None)
        if ex is None:
            return 1, "nada a desfazer: a acção não foi executada"
        if ex.get("tipo") not in ("email_rascunho", "folha_linha"):
            return 1, "esta acção não se desfaz"
        cfg, sid = accoes.ler_config(r.config), ex["squad"]
        sq = cfg["squads"].get(sid) or {}
        try:
            if ex["tipo"] == "email_rascunho":
                imap_apagar(sq["email"], sid, accoes.ler_segredos(r.env), ex["id"])
            else:
                nome, n, h = ex["id"].split(":")
                csv_remover(sq["folhas"][nome], int(n), h)
        except Exception as e:  # noqa: BLE001 - qualquer falha do serviço não regista «desfeita»
            return 1, f"não desfeita: {motivo_erro(e)}"
        tz = (_dt(ex.get("hora")) or agora).tzinfo
        base = {k: ex.get(k) for k in ("squad", "tipo", "sha", "pedido", "linha", "aprovado")}
        escrever_livro(r, livro, agora.astimezone(tz), base, "desfeita", ident=ex["id"])
    finally:
        os.close(fd)
    return 0, "desfeita"
