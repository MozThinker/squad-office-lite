"""Colector de mensagens das acções aprovadas (ADR-021). Corre no serviço `accoes` (o único com as credenciais).

Para cada squad de negócio de `agentes/squads.json` com `email` no `accoes.json` (uma caixa serve uma squad), lê a
caixa por IMAP (`BODY.PEEK[]`: não marca como lida) e escreve cada mensagem nova em
`<ACCOES_ENTRADA>/<squad>/mensagens/m<16 hex>.txt` (linha 1 = id; `De:`, `Assunto:`, `Data:`; linha vazia; só
texto: `text/plain`, ou o HTML sem etiquetas; sem anexos; ≤ 20 KB, cortado com aviso; ≤ 50 por passagem) e regista
`<ACCOES_DIR>/mensagens/<id>.json` (remetente do `From`, nunca do `Reply-To`; `autenticado` pelo 1.º
`Authentication-Results` do `authserv_id` configurado). A 1.ª passagem começa no UIDNEXT (não importa a caixa
antiga); UIDVALIDITY mudado recomeça aí. Publica a vista sem segredos `<ACCOES_ENTRADA>/<squad>/vista.json` (escrita
atómica) que o runner usa para validar as propostas. Mensagens e registos com mais de 90 dias são apagados.
"""
from __future__ import annotations

import email
import fcntl
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from email import policy
from email.utils import parseaddr
from html.parser import HTMLParser

import accoes
from servico_accoes import executor

POR_PASSAGEM, MAX_BYTES = 50, 20 * 1024
CORTADO = "[cortado: mensagem maior que 20 KB]"
MSG = re.compile(r"(m[0-9a-f]{16})\.(txt|json)")
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class _SoTexto(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes, self.fora = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.fora += 1
        elif tag in ("br", "p", "div", "li", "tr"):
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.fora:
            self.fora -= 1

    def handle_data(self, data):
        if not self.fora:
            self.partes.append(data)


def sem_html(html: str) -> str:
    p = _SoTexto()
    p.feed(html)
    p.close()
    return re.sub(r"\n{3,}", "\n\n", "".join(p.partes)).strip()


def _linha(s: str, n: int) -> str:
    return "".join(c if accoes.chars_ok(c) and c not in "\n\t" else " " for c in s)[:n].strip()


def texto(msg) -> str:
    """O corpo `text/plain` (ou o HTML sem etiquetas); anexos nunca."""
    try:
        parte = msg.get_body(preferencelist=("plain",))
        if parte is not None:
            return parte.get_content().replace("\r\n", "\n")
        parte = msg.get_body(preferencelist=("html",))
        return sem_html(parte.get_content()) if parte is not None else ""
    except (LookupError, ValueError, AttributeError):
        return ""


def autenticado(ar: list, authserv: str | None, remetente: str) -> bool:
    """Só o 1.º Authentication-Results (o de cima), e só se vier do nosso servidor (`authserv_id` da configuração);
    sem comentários `(...)`; `dmarc=pass` com `header.from` = domínio do remetente, ou `dkim=pass` com `header.d`
    alinhado (o mesmo domínio ou um sub/super-domínio dele)."""
    if not authserv or not ar:
        return False
    txt, antes = str(ar[0]), None
    while antes != txt:
        antes, txt = txt, re.sub(r"\([^()]*\)", " ", txt)
    partes = [p.strip() for p in txt.split(";")]
    cab = partes[0].split()
    if not cab or cab[0].lower() != authserv.lower():
        return False
    dom = remetente.rsplit("@", 1)[1].lower()
    for p in partes[1:]:
        toks = p.split()
        if not toks:
            continue
        props = {k.lower(): v.strip('"').lower() for k, _, v in (t.partition("=") for t in toks[1:]) if v}
        if toks[0].lower() == "dmarc=pass" and props.get("header.from") == dom:
            return True
        d = props.get("header.d", "")
        if toks[0].lower() == "dkim=pass" and "." in d and (d == dom or dom.endswith("." + d) or d.endswith("." + dom)):
            return True
    return False


def _novo_em(dfd: int, nome: str, dados: bytes, modo: int) -> bool:
    try:
        fd = os.open(nome, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, modo, dir_fd=dfd)
    except FileExistsError:
        return False
    with open(fd, "wb") as f:
        os.fchmod(f.fileno(), modo)   # o umask do serviço (077) não decide: o runner lê as mensagens
        f.write(dados)
    return True


def guardar(r: accoes.Raizes, sid: str, conta: dict, uv: int, uid: int, raw: bytes, dfd: int,
            agora: datetime) -> str | None:
    msg = email.message_from_bytes(raw, policy=policy.default)
    rem = parseaddr(str(msg.get("From", "")))[1]
    if not accoes.ENDERECO.fullmatch(rem):
        return None
    mid = "m" + hashlib.sha256(f"{sid}:{uv}:{uid}".encode()).hexdigest()[:16]
    cab = (f"{mid}\nDe: {rem}\nAssunto: {_linha(str(msg.get('Subject', '')), 200)}\n"
           f"Data: {_linha(str(msg.get('Date', '')), 100)}\n\n")
    dados = (cab + texto(msg)).encode("utf-8")
    if len(dados) > MAX_BYTES:
        dados = dados[:MAX_BYTES].decode("utf-8", "ignore").encode("utf-8") + f"\n{CORTADO}\n".encode()
    _novo_em(dfd, f"{mid}.txt", dados, 0o644)
    msg_id = str(msg.get("Message-ID", "")).strip()
    reg = {"v": 1, "id": mid, "squad": sid, "remetente": rem,
           "message_id": msg_id if re.fullmatch(r"<[^<>\s]{1,250}>", msg_id) else None, "pasta": conta["pasta"],
           "uidvalidity": uv, "uid": uid, "recebido": agora.isoformat(timespec="seconds"),
           "autenticado": autenticado(msg.get_all("Authentication-Results") or [], conta.get("authserv_id"), rem)}
    rfd = os.open(r.dir / "mensagens", _DIR)
    try:
        _novo_em(rfd, f"{mid}.json", json.dumps(reg, ensure_ascii=False).encode("utf-8"), 0o600)
    finally:
        os.close(rfd)
    return mid


def _num(c, codigo: str) -> int:
    _, d = c.response(codigo)
    return int(d[0])


def colher(r: accoes.Raizes, sid: str, conta: dict, seg: dict, dfd: int, agora: datetime) -> tuple[int, list[str]]:
    """(novas, avisos). A pasta de destino já vem aberta (dir_fd): trocá-la depois não desvia a escrita."""
    est_path = r.dir / "estado" / f"colector-{sid}.json"
    est = accoes.ler_json(est_path, 4096)
    avisos, novas = [], 0
    c = executor.ligar_imap(conta)   # nos testes, executor.IMAP é a caixa falsa
    try:
        c.login(*executor.login(sid, seg))
        c.select('"' + conta["pasta"] + '"', readonly=True)
        uv, prox = _num(c, "UIDVALIDITY"), _num(c, "UIDNEXT")
        if not isinstance(est, dict) or est.get("uidvalidity") != uv or type(est.get("ultimo_uid")) is not int:
            if isinstance(est, dict):
                avisos.append(f"UIDVALIDITY mudou na caixa da squad {sid}: recomeça nas mensagens novas")
            est = {"uidvalidity": uv, "ultimo_uid": prox - 1}
        else:
            _, d = c.uid("SEARCH", None, f"UID {est['ultimo_uid'] + 1}:*")
            uids = sorted(int(x) for x in (d[0] or b"").split() if int(x) > est["ultimo_uid"])[:POR_PASSAGEM]
            for uid in uids:
                _, d = c.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next((x[1] for x in d if isinstance(x, tuple)), None)
                if raw is not None:
                    if guardar(r, sid, conta, uv, uid, raw, dfd, agora):
                        novas += 1
                    else:
                        avisos.append(f"mensagem {uid} da squad {sid} sem remetente válido: ignorada")
                est["ultimo_uid"] = uid
    finally:
        try:
            c.logout()
        finally:
            if isinstance(est, dict):
                accoes.escrever_atomico(est_path, json.dumps(est).encode(), 0o600)
    return novas, avisos


def _subpasta(dfd: int, nome: str, modo: int) -> int:
    """fd de `nome` dentro de `dfd` (criada com `modo` se faltar), sem seguir symlinks."""
    try:
        os.mkdir(nome, 0o700, dir_fd=dfd)
        nova = True
    except FileExistsError:
        nova = False
    fd = os.open(nome, _DIR, dir_fd=dfd)
    if nova:
        os.fchmod(fd, modo)   # o umask do serviço (077) não decide
    return fd


def _mensagens(r: accoes.Raizes, sid: str) -> dict:
    """{id: {remetente, autenticada}} das mensagens registadas desta squad (para a vista)."""
    out = {}
    try:
        nomes = sorted(e.name for e in os.scandir(r.dir / "mensagens") if MSG.fullmatch(e.name))
    except OSError:
        return out
    for n in nomes:
        d = accoes.mensagem(r.dir, n[:-5], sid) if n.endswith(".json") else None
        if d is not None:
            out[d["id"]] = {"remetente": d["remetente"], "autenticada": d.get("autenticado") is True}
    return out


def purgar(r: accoes.Raizes, agora: datetime) -> int:
    """Registos do colector e mensagens com mais de 90 dias (pelo `recebido`; as mensagens sem registo pela data do
    ficheiro) apagados. Devolve quantos ficheiros saíram."""
    limite = agora - timedelta(days=accoes.RETENCAO_DIAS)
    n = 0
    try:
        regs = [e.name for e in os.scandir(r.dir / "mensagens") if MSG.fullmatch(e.name)]
    except OSError:
        regs = []
    for nome in regs:
        d = accoes.ler_json(r.dir / "mensagens" / nome, 64 * 1024)
        try:
            quando = datetime.fromisoformat(d["recebido"])
        except (TypeError, KeyError, ValueError):
            continue
        if quando.tzinfo is not None and quando < limite:
            sid = d.get("squad")
            if isinstance(sid, str) and accoes.ID_SQUAD.fullmatch(sid):
                try:
                    os.unlink(r.entrada / sid / "mensagens" / f"{nome[:-5]}.txt")
                    n += 1
                except OSError:
                    pass
            (r.dir / "mensagens" / nome).unlink(missing_ok=True)
            n += 1
    try:
        sids = [e.name for e in os.scandir(r.entrada) if accoes.ID_SQUAD.fullmatch(e.name)
                and e.is_dir(follow_symlinks=False)]
    except OSError:
        sids = []
    for sid in sids:
        pasta = r.entrada / sid / "mensagens"
        try:
            velhas = [e.name for e in os.scandir(pasta) if MSG.fullmatch(e.name)
                      and e.stat(follow_symlinks=False).st_mtime < limite.timestamp()]
        except OSError:
            continue
        for nome in velhas:
            (pasta / nome).unlink(missing_ok=True)
            n += 1
    return n


def publicar_vista(r: accoes.Raizes, cfg: dict, sid: str, sfd: int, perfil: str) -> None:
    vista = accoes.contexto(cfg, sid, _mensagens(r, sid), perfil)
    dados = json.dumps(vista, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        os.unlink(".vista.json.tmp", dir_fd=sfd)
    except FileNotFoundError:
        pass
    fd = os.open(".vista.json.tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=sfd)
    with open(fd, "wb") as f:
        f.write(dados)
        os.fchmod(f.fileno(), 0o644)
    os.replace(".vista.json.tmp", "vista.json", src_dir_fd=sfd, dst_dir_fd=sfd)


def _squad(r: accoes.Raizes, cfg: dict, seg: dict, sid: str, efd: int, agora: datetime, perfil: str) -> list[str]:
    """Colhe a caixa da squad (se tiver `email` e credenciais) e publica a vista dela."""
    avisos = []
    sq = cfg["squads"].get(sid) or {}
    sfd = _subpasta(efd, sid, 0o755)
    try:
        if "email" in sq:
            k = accoes.chave_sq(sid)
            if f"{k}_EMAIL_UTILIZADOR" not in seg or f"{k}_EMAIL_SENHA" not in seg:
                avisos.append(f"squad {sid} sem credenciais de e-mail")
            else:
                mfd = _subpasta(sfd, "mensagens", 0o755)
                try:
                    avisos += colher(r, sid, sq["email"], seg, mfd, agora)[1]
                except Exception as e:  # noqa: BLE001 - uma caixa em falha não pára as outras
                    avisos.append(f"squad {sid}: {type(e).__name__}")
                finally:
                    os.close(mfd)
        publicar_vista(r, cfg, sid, sfd, perfil)
    finally:
        os.close(sfd)
    return avisos


def passagem(r: accoes.Raizes, agora: datetime, perfil: str = accoes.PERFIL) -> list[str] | None:
    """Uma passagem do colector; devolve avisos (sem valores) para o stderr, ou None se outra passagem está a correr."""
    for p in ("estado", "mensagens"):
        (r.dir / p).mkdir(mode=0o700, exist_ok=True)
    fd = os.open(r.dir / "estado/colector.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    avisos = []
    try:
        squads = executor.ler_squads(r)
        if squads is None:
            return ["registo de squads ilegível; nada colhido nesta passagem"]
        cfg, seg = accoes.ler_config(r.config), accoes.ler_segredos(r.env)
        purgar(r, agora)   # antes da vista: o que sai aos 90 dias já não aparece nela
        efd = os.open(r.entrada, _DIR)
        try:
            for sid in sorted(squads):
                try:
                    avisos += _squad(r, cfg, seg, sid, efd, agora, perfil)
                except OSError as e:   # pasta da squad trocada por um symlink, sem espaço…: as outras seguem
                    avisos.append(f"squad {sid}: pasta de entrada inutilizável ({type(e).__name__})")
        finally:
            os.close(efd)
    finally:
        os.close(fd)
    return avisos
