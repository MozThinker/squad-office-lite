"""Notificações no telemóvel (Web Push, ADR-004): cifra aes128gcm (RFC 8291), VAPID ES256 (RFC 8292) e o vigia.

Dados em PUSH_DIR (só o Office escreve, escrita atómica sob lock, ficheiros 0600):
- vapid: chave privada P-256 (PEM PKCS8); nunca sai em respostas nem no log
- subscricoes.json: {"v": 1, "subscricoes": [{"endpoint", "keys", "prefs", "criado", "actualizado"}]}
- notificadas.json: {"v": 1, "chaves": {"<chave>": <epoch s>}} (última vez no estado; expira 31 dias depois de sair)
As prefs ganharam `eventos.fases` e o grupo `fases` (SPEC..PR) sem mudar o `v: 1`: as antigas são completadas com
as omissões ao ler o ficheiro e ao receber o POST (extensão compatível).
Aviso por fase de um goal das squads worktree (chave `fase:<squad>:<id>:<FASE>`, tag `fase:<id>`): só tarefas a correr, nunca crítico.
Os payloads só levam squad, projecto, slug, fase, estado/mode e ids: nunca task, result, títulos, resumos nem custos.
O log leva o código HTTP e o host, nunca o endpoint completo.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

import progresso

try:   # opcional, como a Pillow: sem ela o push fica desligado (503) e o resto funciona
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    ec = None

TIMEOUT = 10
TTL = 21600
MAX_SUBS = 20
MAX_CICLO = 5
RETER_S = 31 * 86400
RECENTE_S = 6 * 3600
TESTE_S = 10
CORPO_MAX = 4096
RS = 4096
ENDPOINT_MAX = 1024
JWT_S = 12 * 3600
TXT_MAX = 40
HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com")
SUFIXOS = (".push.apple.com", ".push.services.mozilla.com", ".notify.windows.com")
HORA = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
B64U = re.compile(r"[A-Za-z0-9_-]*={0,2}")
# preenchidos por configurar(cfg): {id: nome} das squads e as que são formato worktree (PR e fases)
SQUADS: dict = {}
WORKTREE: tuple = ()
NAO_TERMINAL = ("a correr", "parada?", "morreu", "?")
PR_N = re.compile(r"/pull/(\d+)")
PREFS = {"eventos": ("concluida", "falhada", "pr", "alertas", "aprovacoes", "fases"),
         "squads": (), "fases": progresso.FASES,
         "silencio": ("ligado", "de", "ate")}
NOME_FASE = {"SPEC": "SPEC", "PLAN": "PLAN", "CODE": "código", "QA": "QA", "PR": "PR"}
# fim do body do aviso de fase (CODE feito = já houve engenheiro, por isso «em curso»)
FASE_FEITA = {"SPEC": "SPEC feita", "PLAN": "PLAN feito", "CODE": "código em curso", "QA": "QA feito",
              "PR": "PR aberto"}
PREFIXO_ID = re.compile(r"^[0-9]{8}-[0-9]{4}-")
ERRO_503 = "notificações no telemóvel não configuradas no servidor"
TESTE = {"title": "🔔 Teste do Office", "body": "Se estás a ver isto, as notificações no telemóvel funcionam.",
         "tag": "teste", "url": "./", "critico": False}


def configurar(cfg: dict) -> None:
    global SQUADS, WORKTREE
    SQUADS = {s["id"]: s["nome"] for s in cfg["squads"]}
    WORKTREE = tuple(s["id"] for s in cfg["squads"] if s["formato"] == "worktree")
    PREFS["squads"] = tuple(SQUADS)


# --- base64url ---
def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def deb64u(s: str) -> bytes:
    if not isinstance(s, str) or not B64U.fullmatch(s):
        raise ValueError("base64url inválido")
    s = s.rstrip("=")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (ValueError, TypeError) as e:   # binascii.Error é ValueError
        raise ValueError("base64url inválido") from e


# --- validação ---
def _endpoint_ok(endpoint) -> bool:
    # forma: https, ASCII visível, ≤ 1024, sem userinfo, porta ausente ou 443
    if not (isinstance(endpoint, str) and 0 < len(endpoint) <= ENDPOINT_MAX and re.fullmatch(r"[!-~]+", endpoint)):
        return False
    u = urlsplit(endpoint)
    try:
        port = u.port
    except ValueError:
        return False
    return u.scheme == "https" and bool(u.hostname) and "@" not in u.netloc and port in (None, 443)


def host_permitido(endpoint: str) -> bool:
    if not _endpoint_ok(endpoint):
        return False
    host = urlsplit(endpoint).hostname
    return host in HOSTS or any(host.endswith(s) and len(host) > len(s) for s in SUFIXOS)


def validar_subscricao(d) -> str | None:
    # expirationTime (do PushSubscription.toJSON()) aceita-se e ignora-se
    if not isinstance(d, dict):
        return "endpoint inválido"
    if set(d) - {"endpoint", "keys", "expirationTime"}:
        return "campos desconhecidos"
    if not _endpoint_ok(d.get("endpoint")):
        return "endpoint inválido"
    if not host_permitido(d["endpoint"]):
        return "serviço de push não permitido"
    k = d.get("keys")
    if not isinstance(k, dict) or set(k) != {"p256dh", "auth"}:
        return "chaves da subscrição inválidas"
    try:
        p256dh, auth = deb64u(k["p256dh"]), deb64u(k["auth"])
    except ValueError:
        return "chaves da subscrição inválidas"
    if len(p256dh) != 65 or p256dh[0] != 4 or len(auth) != 16:
        return "chaves da subscrição inválidas"
    return None


def validar_prefs(p) -> str | None:
    if not isinstance(p, dict):
        return "preferências inválidas"
    topo = {"eventos", "squads", "fases", "silencio", "criticos_ignoram_silencio"}
    if set(p) - topo or any(isinstance(p.get(g), dict) and set(p[g]) - set(ks) for g, ks in PREFS.items()):
        return "campos desconhecidos"
    if set(p) != topo or not isinstance(p["criticos_ignoram_silencio"], bool):
        return "preferências inválidas"
    for g, ks in PREFS.items():
        if not isinstance(p[g], dict) or set(p[g]) != set(ks):
            return "preferências inválidas"
    if not all(isinstance(p[g][k], bool) for g in ("eventos", "squads", "fases") for k in PREFS[g]):
        return "preferências inválidas"
    s = p["silencio"]
    horas_ok = all(isinstance(s[k], str) and HORA.fullmatch(s[k]) for k in ("de", "ate"))
    if not isinstance(s["ligado"], bool) or not horas_ok:
        return "preferências inválidas"
    if s["de"] == s["ate"]:
        return "as horas de silêncio têm de ser diferentes"
    return None


def prefs_omissao() -> dict:
    # D9: tudo ligado menos as aprovações e as fases (só QA/PR quando se liga); silêncio desligado com 23:00–07:00;
    # críticos furam o silêncio
    return {"eventos": {"concluida": True, "falhada": True, "pr": True, "alertas": True, "aprovacoes": False,
                        "fases": False},
            "squads": {sq: True for sq in SQUADS},
            "fases": {"SPEC": False, "PLAN": False, "CODE": False, "QA": True, "PR": True},
            "silencio": {"ligado": False, "de": "23:00", "ate": "07:00"},
            "criticos_ignoram_silencio": True}


def normalizar_prefs(p):
    # prefs da forma antiga (sem eventos.fases nem fases) → completadas com as omissões; squads da config que
    # faltam entram ligadas e as que já não existem saem; não valida
    if not isinstance(p, dict):
        return p
    p = dict(p)
    if isinstance(p.get("eventos"), dict):
        p["eventos"] = dict(p["eventos"])
        p["eventos"].setdefault("fases", False)
    if isinstance(p.get("squads"), dict):
        p["squads"] = {sq: p["squads"].get(sq, True) for sq in SQUADS}
    p.setdefault("fases", prefs_omissao()["fases"])
    return p


# --- filtros ---
def _minutos(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def em_silencio(silencio: dict, quando: datetime) -> bool:
    # quando já no fuso da config; de incluído, ate excluído; pode atravessar a meia-noite
    if not silencio.get("ligado"):
        return False
    de, ate, m = _minutos(silencio["de"]), _minutos(silencio["ate"]), quando.hour * 60 + quando.minute
    return de <= m < ate if de < ate else (m >= de or m < ate)


def quer(evento: dict, prefs: dict, quando: datetime) -> bool:
    if not prefs["eventos"].get(evento["categoria"]):
        return False
    if evento["squad"] is not None and not prefs["squads"].get(evento["squad"]):
        return False
    if evento["categoria"] == "fases" and not prefs["fases"].get(evento["fase"]):
        return False
    if em_silencio(prefs["silencio"], quando):
        return evento["critico"] and prefs["criticos_ignoram_silencio"]
    return True


# --- eventos (D4, D6, D7; textos da SPEC §5) ---
def _corta(v) -> str:
    return str(v)[:TXT_MAX]


def _ts(iso) -> float | None:
    try:
        return datetime.fromisoformat(iso).timestamp() if isinstance(iso, str) else None
    except ValueError:
        return None


def _url(tid: str) -> str:
    return "./#" + quote(tid, safe="-_.!~*'()")


def fases_tarefa(t: dict) -> list[dict]:
    # a mesma regra do server.py (progress.fases), também para tarefas terminadas (progress null)
    ph = t.get("phases") if isinstance(t.get("phases"), dict) else {}
    ags = t.get("agents") if isinstance(t.get("agents"), list) else []
    gh = t.get("gh") if isinstance(t.get("gh"), dict) else {}
    papeis = [a["role"] for a in ags if isinstance(a, dict) and isinstance(a.get("role"), str)]
    return progresso.fases(ph, papeis, bool(t.get("pr") or gh.get("pr")))


def barra(fs: list[dict]) -> str:
    return "".join("▰" if f["estado"] == "feito" else "▱" for f in fs)


def slug(tid: str) -> str:
    # id sem o prefixo AAAAMMDD-HHMM- (D9); sem prefixo (ou só prefixo), o id inteiro
    return (PREFIXO_ID.sub("", tid, count=1) or tid)[:TXT_MAX]


def estado_fase(fs: list[dict], status: str, concluida: bool, tem_pr: bool) -> str:
    # texto do fim de uma tarefa Dev goal; falhada/morreu/parada? usam a 1.ª fase não feita
    if concluida:
        return "PR em rascunho" if status == "parcial (PR draft)" else "PR pronto" if tem_pr else "concluído"
    pre = "parada" if status == "parada?" else "falhou"
    f = next((f["nome"] for f in fs if f["estado"] != "feito"), None)
    if f is None:
        return f"{pre} depois do PR"
    return f"{pre} {'na' if f == 'SPEC' else 'no'} {NOME_FASE[f]}"


def _ev(chave, categoria, squad, critico, title, body, tag, url, fim_ts=None) -> dict:
    return {"chave": chave, "categoria": categoria, "squad": squad, "critico": critico, "fim_ts": fim_ts,
            "payload": {"title": title, "body": body, "tag": tag, "url": url, "critico": critico}}


def _corpo(t: dict, tid: str, status, concluida: bool, omissao: str) -> str:
    # CA3–CA7: só o goal das squads worktree leva a barra; as outras squads e modes ficam byte a byte como antes
    if t.get("squad") not in WORKTREE or t.get("mode") != "goal":
        return omissao
    fs = fases_tarefa(t)
    est = estado_fase(fs, status, concluida, fs[-1]["estado"] == "feito")
    return f"{barra(fs)} {_corta(t.get('project') or 'geral')} · {slug(tid)} · {est}"


def eventos(state: dict, now: float, concluidas: tuple) -> list[dict]:
    out, por_id = [], {}
    for t in state.get("tasks") or []:
        sq, tid = t.get("squad"), t.get("id")
        if sq not in SQUADS or not isinstance(tid, str) or not tid:
            continue
        por_id.setdefault(tid, t)
        status, mode = t.get("status"), t.get("mode")
        if "dry-run" in (mode, status):
            continue
        cab = f"{SQUADS[sq]} · {_corta(t.get('project') or 'geral')}"
        # o fim leva o finished: um follow-up (dev_task.py continue) reutiliza o id e volta a notificar
        fim = f"fim:{sq}:{tid}:{t.get('finished') or ''}"
        if not t.get("running") and status not in NAO_TERMINAL:
            if status in concluidas:
                parcial = status == "parcial (PR draft)"
                title = (f"⚠️ {cab}: {_corta(mode)} parcial (PR draft)" if parcial
                         else f"✅ {cab}: {_corta(mode)} concluído")
                body = _corpo(t, tid, status, True, "Toca para abrir a execução.")
                out.append(_ev(fim, "concluida", sq, False, title, body, tid, _url(tid), _ts(t.get("finished"))))
            else:
                body = _corpo(t, tid, status, False, "Toca para ver o que aconteceu.")
                out.append(_ev(fim, "falhada", sq, False, f"⚠️ {cab}: tarefa {_corta(status)}", body, tid, _url(tid),
                               _ts(t.get("finished"))))
        if status == "parada?":
            chave = f"parada:{sq}:{tid}"
            body = _corpo(t, tid, status, False, "Sem actividade há mais de 30 min.")
            out.append(_ev(chave, "falhada", sq, False, f"⏸ {cab}: tarefa parada?", body, chave, _url(tid)))
        if sq in WORKTREE:
            gh = t.get("gh") if isinstance(t.get("gh"), dict) else {}
            n = next((h.group(1) for v in (gh.get("pr_url"), t.get("pr")) if isinstance(v, str)
                      for h in [PR_N.search(v)] if h), None)
            if n:
                chave = f"pr:{sq}:{tid}:{n}"
                out.append(_ev(chave, "pr", sq, False, f"🎉 {cab}: PR #{n} aberto", "Toca para abrir a execução.", chave,
                               _url(tid)) | {"tarefa": tid})
        if sq in WORKTREE and mode == "goal" and t.get("running"):
            # CA13–CA16: um aviso por fase feita; a tag por tarefa substitui o anterior (renotify false)
            fs, proj = fases_tarefa(t), _corta(t.get("project") or "geral")
            for f in (f["nome"] for f in fs if f["estado"] == "feito"):
                e = _ev(f"fase:{sq}:{tid}:{f}", "fases", sq, False, f"📍 {SQUADS[sq]} · {proj}: {NOME_FASE[f]}",
                        f"{barra(fs)} {proj} · {slug(tid)} · {FASE_FEITA[f]}", f"fase:{tid}", _url(tid))
                e["payload"]["renotify"] = False
                out.append(e | {"tarefa": tid, "fase": f})
    # alertas: só tarefas mortas (heartbeat parado)
    for chave in (state.get("alerts") or {}).get("items") or []:
        tipo, _, nome = chave.partition(":") if isinstance(chave, str) else ("", "", "")
        if tipo == "tarefa" and nome in por_id:
            t = por_id[nome]
            sq = t["squad"]
            out.append(_ev(chave, "alertas", sq, True,
                           f"💀 {SQUADS[sq]} · {_corta(t.get('project') or 'geral')}: tarefa morreu",
                           _corpo(t, nome, "morreu", False, "O lançador deixou de dar sinal há mais de 5 min."), nome,
                           _url(nome)))
    for a in state.get("aprovacoes") or []:
        fonte, ficheiro = a.get("fonte"), a.get("ficheiro")
        if isinstance(fonte, str) and isinstance(ficheiro, str):
            chave = f"apr:{fonte}/{ficheiro}"
            rotulo = SQUADS.get(fonte) or _corta(fonte).capitalize()
            out.append(_ev(chave, "aprovacoes", fonte if fonte in SQUADS else None, False,
                           f"📝 Aprovação nova · {rotulo}", "Há decisões à tua espera no Office.", chave, "./"))
    return out


def colapsar(evs: list[dict]) -> list[dict]:
    # D6: por tarefa, só o aviso de fase mais adiantado; os outros eventos passam inalterados e pela ordem.
    # Se no mesmo lote vai o «PR #n aberto» dessa tarefa, não sai nenhum aviso de fase dela (o mesmo PR duas vezes)
    com_pr = {e.get("tarefa") for e in evs if e["categoria"] == "pr"}
    melhor = {}
    for e in evs:
        if e["categoria"] == "fases" and e["tarefa"] not in com_pr:
            m = melhor.get(e["tarefa"])
            if m is None or progresso.FASES.index(e["fase"]) > progresso.FASES.index(m["fase"]):
                melhor[e["tarefa"]] = e
    fica = {id(e) for e in melhor.values()}
    return [e for e in evs if e["categoria"] != "fases" or id(e) in fica]


def seleccionar(eventos: list, notificadas: dict | None, now: float) -> tuple[list[dict], dict]:
    # (a enviar, chaves notificadas actualizadas); notificadas None = 1.º arranque (só semeia)
    primeiro = notificadas is None
    presentes = {e["chave"] for e in eventos}
    # só expira por idade o que já não está no estado (senão uma chave ainda presente voltava a notificar)
    nots = {k: v for k, v in (notificadas or {}).items() if k in presentes or now - v <= RETER_S}
    for k in presentes & nots.keys():
        nots[k] = now   # a retenção conta desde a última vez que a chave esteve no estado
    enviar = []
    for e in eventos:
        if e["chave"] in nots:
            continue
        nots[e["chave"]] = now   # conta como notificada antes do envio, haja ou não destinatário
        if primeiro:
            continue
        if e["chave"].startswith("fim:") and (e["fim_ts"] is None or now - e["fim_ts"] > RECENTE_S):
            continue   # D5: acabou com o Office em baixo há muito (ou sem hora de fim): só semeia
        enviar.append(e)
    return enviar, nots


def agrupar(payloads: list[dict]) -> list[dict]:
    # críticos primeiro; no máximo MAX_CICLO e o excedente numa só
    ordem = sorted(payloads, key=lambda p: not p["critico"])
    if len(ordem) <= MAX_CICLO:
        return ordem
    n = len(ordem) - MAX_CICLO
    return ordem[:MAX_CICLO] + [{"title": f"Mais {n} avisos", "body": "Abre o Office para ver tudo.", "tag": "mais",
                                 "url": "./", "critico": False}]


# --- cifra e VAPID ---
def _hmac(chave: bytes, dados: bytes) -> bytes:
    return hmac.new(chave, dados, hashlib.sha256).digest()


def _ponto(chave) -> bytes:
    return chave.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def cifrar(texto: bytes, p256dh: bytes, auth: bytes, sal: bytes | None = None, efemera: bytes | None = None) -> bytes:
    # RFC 8291 (aes128gcm, RFC 8188): um só registo com delimitador 0x02; sal/efemera fixos só nos testes
    sal = sal if sal is not None else secrets.token_bytes(16)
    priv = (ec.derive_private_key(int.from_bytes(efemera, "big"), ec.SECP256R1()) if efemera is not None
            else ec.generate_private_key(ec.SECP256R1()))
    as_pub = _ponto(priv.public_key())
    segredo = priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh))
    ikm = _hmac(_hmac(auth, segredo), b"WebPush: info\x00" + p256dh + as_pub + b"\x01")
    prk = _hmac(sal, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(texto) + 1 + 16 > RS:
        raise ValueError("payload grande demais")
    return sal + struct.pack("!IB", RS, len(as_pub)) + as_pub + AESGCM(cek).encrypt(nonce, texto + b"\x02", None)


def jwt_vapid(chave_privada, endpoint: str, contacto: str, agora: float) -> str:
    # RFC 8292: aud = origem do endpoint, exp ≤ 24 h, sub = contacto; assinatura ES256 em r‖s
    u = urlsplit(endpoint)
    cab = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    corpo = b64u(json.dumps({"aud": f"https://{u.hostname}", "exp": int(agora) + JWT_S, "sub": contacto},
                            separators=(",", ":")).encode())
    r, s = decode_dss_signature(chave_privada.sign(f"{cab}.{corpo}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{cab}.{corpo}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# --- ficheiros e envio ---
class _SemRedireccao(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):   # 3xx não se segue: fica como erro
        return None


_OPENER = urllib.request.build_opener(_SemRedireccao)


def _abrir(req, timeout) -> int:
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        e.close()
        return e.code


def _escrever(path: Path, dados: bytes) -> None:
    # tmp na mesma pasta (0600, O_EXCL) + fsync + os.replace
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dados)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _log(msg: str) -> None:
    print(f"push: {msg}", flush=True)


class Servico:
    def __init__(self, pasta, contacto: str, tz, concluidas, abrir=None):
        self.pasta, self.contacto, self.tz, self.concluidas = Path(pasta), contacto, tz, tuple(concluidas)
        self.abrir = abrir or _abrir
        self._lock = threading.Lock()
        self._chave = None
        self._motivo = None
        self._testes: dict = {}   # endpoint → hora do último teste
        self._aviso = None        # último aviso de ficheiro ilegível (só se repete quando muda)

    # --- arranque ---
    def iniciar(self) -> bool:
        # idempotente; cria ou lê a chave VAPID; sem cryptography ou sem pasta com escrita = desligado
        with self._lock:
            if self._chave is not None:
                return True
            motivo = "sem_cryptography" if ec is None else None
            if motivo is None and not (self.pasta.is_dir() and os.access(self.pasta, os.W_OK | os.X_OK)):
                motivo = "sem_pasta"
            if motivo is None:
                try:
                    self._chave = self._vapid()
                except (OSError, ValueError, TypeError) as e:
                    _log(f"chave VAPID indisponível ({type(e).__name__})")
                    motivo = "sem_pasta"
            if motivo != self._motivo:
                if motivo:
                    _log(f"desligado ({motivo})")
                self._motivo = motivo
            return self._chave is not None

    def _vapid(self):
        f = self.pasta / "vapid"
        try:
            chave = serialization.load_pem_private_key(f.read_bytes(), password=None)
        except FileNotFoundError:
            chave = ec.generate_private_key(ec.SECP256R1())
            _escrever(f, chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
            return chave
        if not isinstance(chave, ec.EllipticCurvePrivateKey) or not isinstance(chave.curve, ec.SECP256R1):
            raise TypeError("chave VAPID não é P-256")
        return chave

    def estado(self) -> tuple[bool, str | None]:
        ok = self.iniciar()
        return ok, None if ok else self._motivo

    def chave_publica(self) -> str:
        return b64u(_ponto(self._chave.public_key()))

    # --- ficheiros (chamar com o lock) ---
    def _avisa(self, msg: str | None) -> None:
        if msg and msg != self._aviso:
            _log(msg)
        self._aviso = msg

    def _ler_subs(self) -> list | None:
        # None = ilegível (v ≠ 1, JSON mau ou entrada inválida): não se escreve nem se envia até corrigir à mão
        try:
            d = json.loads((self.pasta / "subscricoes.json").read_bytes())
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            return None
        subs = d.get("subscricoes") if isinstance(d, dict) and d.get("v") == 1 else None
        if not isinstance(subs, list):
            return None
        for s in subs:
            if isinstance(s, dict):   # prefs antigas completadas em memória; o ficheiro só muda na próxima gravação
                s["prefs"] = normalizar_prefs(s.get("prefs"))
            if not (isinstance(s, dict) and validar_subscricao({"endpoint": s.get("endpoint"), "keys": s.get("keys")})
                    is None and validar_prefs(s.get("prefs")) is None):
                return None
        return subs

    def _gravar_subs(self, subs: list) -> None:
        _escrever(self.pasta / "subscricoes.json",
                  json.dumps({"v": 1, "subscricoes": subs}, ensure_ascii=False, indent=1).encode())

    def _ler_notificadas(self) -> dict | None:
        try:
            d = json.loads((self.pasta / "notificadas.json").read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            d = None
        ch = d.get("chaves") if isinstance(d, dict) and d.get("v") == 1 else None
        if not (isinstance(ch, dict) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                             for v in ch.values())):
            _log("notificadas.json ilegível: tratado como 1.º arranque")
            return None
        return ch

    def _agora_iso(self, now: float) -> str:
        return datetime.fromtimestamp(now, self.tz).isoformat(timespec="seconds")

    def _remover(self, endpoints: set) -> None:
        with self._lock:
            subs = self._ler_subs()
            if subs is None:
                return
            resto = [s for s in subs if s["endpoint"] not in endpoints]
            if len(resto) != len(subs):
                self._gravar_subs(resto)

    # --- rotas (o handler já fez _post_json e estado()) ---
    def subscrever(self, d: dict, now: float | None = None) -> tuple[int, dict]:
        now = time.time() if now is None else now
        if set(d) - {"token", "subscricao", "prefs"}:
            return 400, {"error": "campos desconhecidos"}
        msg = validar_subscricao(d.get("subscricao"))
        prefs = normalizar_prefs(d.get("prefs"))
        if msg is None and prefs is not None:
            msg = validar_prefs(prefs)
        if msg:
            return 400, {"error": msg}
        sub = d["subscricao"]
        with self._lock:
            subs = self._ler_subs()
            if subs is None:
                return 503, {"error": "ficheiro de subscrições ilegível"}
            s = next((s for s in subs if s["endpoint"] == sub["endpoint"]), None)
            if s is None:
                if len(subs) >= MAX_SUBS:
                    return 409, {"error": "demasiados dispositivos"}
                s = {"endpoint": sub["endpoint"], "keys": {}, "prefs": prefs_omissao(),
                     "criado": self._agora_iso(now), "actualizado": None}
                subs.append(s)
            s["keys"] = {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}
            if prefs is not None:
                s["prefs"] = prefs
            s["actualizado"] = self._agora_iso(now)
            try:
                self._gravar_subs(subs)
            except OSError as e:
                _log(f"não gravou subscricoes.json ({type(e).__name__})")
                return 503, {"error": "não foi possível gravar"}
            return 200, {"prefs": s["prefs"]}

    def _endpoint_pedido(self, d: dict) -> str | None:
        if set(d) - {"token", "endpoint"}:
            return "campos desconhecidos"
        e = d.get("endpoint")
        return None if isinstance(e, str) and 0 < len(e) <= ENDPOINT_MAX else "endpoint inválido"

    def cancelar(self, d: dict) -> tuple[int, dict]:
        msg = self._endpoint_pedido(d)
        if msg:
            return 400, {"error": msg}
        with self._lock:
            subs = self._ler_subs()
            if subs is None:
                return 503, {"error": "ficheiro de subscrições ilegível"}
            resto = [s for s in subs if s["endpoint"] != d["endpoint"]]
            if len(resto) != len(subs):
                try:
                    self._gravar_subs(resto)
                except OSError as e:
                    _log(f"não gravou subscricoes.json ({type(e).__name__})")
                    return 503, {"error": "não foi possível gravar"}
        return 200, {"ok": True}

    def teste(self, d: dict, now: float) -> tuple[int, dict]:
        # não respeita filtros nem silêncio; 1 por TESTE_S por endpoint
        msg = self._endpoint_pedido(d)
        if msg:
            return 400, {"error": msg}
        with self._lock:
            subs = self._ler_subs()
            if subs is None:
                return 503, {"error": "ficheiro de subscrições ilegível"}
            s = next((s for s in subs if s["endpoint"] == d["endpoint"]), None)
            if s is None:
                return 404, {"error": "dispositivo não subscrito"}
            self._testes = {e: t for e, t in self._testes.items() if now - t < TESTE_S}
            if s["endpoint"] in self._testes:
                return 429, {"error": "espera uns segundos antes de outro teste"}
            self._testes[s["endpoint"]] = now
        code = self._enviar(s, TESTE, now)
        if code is not None and 200 <= code < 300:
            return 200, {"ok": True}
        if code in (404, 410):
            self._remover({s["endpoint"]})
            return 410, {"error": "subscrição expirada, liga de novo"}
        if code is None:
            return 502, {"error": "o serviço de push não respondeu"}
        return 502, {"error": f"o serviço de push recusou ({code})"}

    def _enviar(self, s: dict, payload: dict, now: float) -> int | None:
        host = urlsplit(s["endpoint"]).hostname
        try:
            corpo = cifrar(json.dumps(payload, ensure_ascii=False).encode(), deb64u(s["keys"]["p256dh"]),
                           deb64u(s["keys"]["auth"]))
            req = urllib.request.Request(s["endpoint"], data=corpo, method="POST", headers={
                "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(TTL),
                "Authorization": f"vapid t={jwt_vapid(self._chave, s['endpoint'], self.contacto, now)}, "
                                 f"k={self.chave_publica()}"})
            code = self.abrir(req, TIMEOUT)
        except Exception as e:   # rede, timeout, chave do browser inválida: só o tipo e o host
            _log(f"{type(e).__name__} {host}")
            return None
        _log(f"{code} {host}")
        return code

    # --- vigia ---
    def ciclo(self, state: dict, now: float) -> int:
        # um ciclo do vigia: decide por chaves persistidas e envia; devolve quantas o serviço aceitou
        if not self.iniciar():
            return 0
        evs = eventos(state, now, self.concluidas)
        with self._lock:
            antes = self._ler_notificadas()
            enviar, nots = seleccionar(evs, antes, now)
            if nots != antes:
                _escrever(self.pasta / "notificadas.json", json.dumps({"v": 1, "chaves": nots}).encode())
            subs = self._ler_subs()
            self._avisa("subscricoes.json ilegível: nada enviado" if subs is None else None)
        if not enviar or not subs:
            return 0
        quando = datetime.fromtimestamp(now, self.tz)
        n, mortas = 0, set()
        for s in subs:
            for p in agrupar([e["payload"] for e in colapsar([e for e in enviar if quer(e, s["prefs"], quando)])]):
                code = self._enviar(s, p, now)
                if code in (404, 410):
                    mortas.add(s["endpoint"])
                    break
                n += code is not None and 200 <= code < 300
        if mortas:
            self._remover(mortas)
        return n
