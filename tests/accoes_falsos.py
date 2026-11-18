"""Ajudas dos testes das acções aprovadas (ADR-021), sem testes aqui: SMTP e Telegram falsos como servidores locais
reais (thread, 127.0.0.1:0), uma caixa IMAP falsa injectável com respostas no formato do imaplib, e um «mundo»
temporário com o office.yaml do runner (fuso Europe/Lisbon), `agentes/squads.json`, `decisoes/`, a pasta do serviço,
a configuração e as credenciais. Nomes inventados; nenhuma credencial real; nada fora de pastas temporárias."""
from __future__ import annotations

import json
import os
import re
import socketserver
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import accoes
from servico_accoes import colector
from tests import test_runner_comum as c

accoes_propor = c.office_runner.accoes_propor   # runner/ entra no sys.path com o test_runner_comum

AGORA = datetime(2026, 11, 18, 10, 0, 7, tzinfo=ZoneInfo("Europe/Lisbon"))   # quarta-feira
NOW = AGORA.timestamp()
TID = "20261118-100007-loja-tarefa"


def mid(n: int) -> str:
    return f"m{n:016x}"


def bloco(**d) -> str:
    return f"{accoes.MARCA_INI}\n{json.dumps(d, ensure_ascii=False)}\n{accoes.MARCA_FIM}\n"


# --- SMTP --------------------------------------------------------------------------------------------------------------
class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        f = self.server.falso
        with f.lock:
            f.ligacoes += 1

        def w(s):
            self.wfile.write(s.encode() + b"\r\n")
        w("220 falso ESMTP")
        de, para = None, []
        while True:
            ln = self.rfile.readline()
            if not ln:
                return
            cmd = ln.decode("utf-8", "replace").strip()
            up = cmd.upper()
            if up.startswith(("EHLO", "HELO")):
                self.wfile.write(b"250-falso\r\n250 AUTH PLAIN\r\n")
            elif up.startswith("AUTH"):
                w("235 ok")
            elif up.startswith("MAIL FROM:"):
                de = cmd[10:].split()[0].strip("<>")
                w("250 ok")
            elif up.startswith("RCPT TO:"):
                para.append(cmd[8:].split()[0].strip("<>"))
                w("250 ok")
            elif up == "DATA":
                w("354 go")
                dados = b""
                while (x := self.rfile.readline()) not in (b".\r\n", b""):
                    dados += x
                with f.lock:
                    f.mensagens.append({"de": de, "para": para, "dados": dados})
                de, para = None, []
                w("250 ok")
            elif up == "QUIT":
                w("221 adeus")
                return
            elif up in ("RSET", "NOOP"):
                w("250 ok")
            else:
                w("502 não")


class SMTPFalso:
    def __init__(self):
        self.ligacoes, self.mensagens, self.lock = 0, [], threading.Lock()
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTPHandler)
        self.srv.daemon_threads = True
        self.srv.falso = self
        self.porta = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, args=(0.05,), daemon=True).start()

    def fechar(self):
        self.srv.shutdown()
        self.srv.server_close()


# --- Telegram ----------------------------------------------------------------------------------------------------------
class _TGHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        f = self.server.falso
        corpo = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with f.lock:
            f.chamadas.append({"caminho": self.path, "corpo": json.loads(corpo or b"{}")})
            n = len(f.chamadas)
        if f.estado != 200:
            self.send_response(f.estado)
            self.end_headers()
            return
        out = json.dumps({"ok": True, "result": {"message_id": 100 + n}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class TelegramFalso:
    def __init__(self):
        self.chamadas, self.estado, self.lock = [], 200, threading.Lock()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _TGHandler)
        self.srv.daemon_threads = True
        self.srv.falso = self
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, args=(0.05,), daemon=True).start()

    def fechar(self):
        self.srv.shutdown()
        self.srv.server_close()


# --- IMAP (classe falsa com as respostas do imaplib) -------------------------------------------------------------------
class CaixaFalsa:
    """`executor.IMAP = caixa`: cada chamada `caixa(host, porta)` é uma ligação nova à mesma caixa."""

    def __init__(self, uidvalidity: int = 7, uidplus: bool = True):
        self.uidvalidity, self.uidplus = uidvalidity, uidplus
        self.pastas: dict[str, dict[int, bytes]] = {"INBOX": {}, "Drafts": {}}
        self.proximo = 1000
        self.ligacoes, self.comandos, self.logins = 0, [], []
        self.falha_store = False

    def entregar(self, raw: bytes, pasta: str = "INBOX") -> int:
        self.proximo += 1
        self.pastas[pasta][self.proximo] = raw
        return self.proximo

    def __call__(self, host, porta):
        self.ligacoes += 1
        return _Sessao(self)


class _Sessao:
    def __init__(self, caixa: CaixaFalsa):
        self.c, self.pasta = caixa, None

    def login(self, u, p):
        self.c.logins.append(u)
        return "OK", [b"ok"]

    def select(self, pasta, readonly=False):
        pasta = pasta.strip('"')
        self.c.comandos.append(("SELECT", pasta, readonly))
        self.pasta = pasta
        return "OK", [str(len(self.c.pastas[pasta])).encode()]

    def response(self, code):
        v = {"UIDVALIDITY": self.c.uidvalidity, "UIDNEXT": self.c.proximo + 1}[code]
        return code, [str(v).encode()]

    def append(self, pasta, flags, data, msg):
        pasta = pasta.strip('"')
        self.c.comandos.append(("APPEND", pasta, flags))
        uid = self.c.entregar(msg, pasta)
        txt = f"[APPENDUID {self.c.uidvalidity} {uid}] ok" if self.c.uidplus else "ok"
        return "OK", [txt.encode()]

    def uid(self, cmd, *args):
        self.c.comandos.append((cmd, *args))
        caixa = self.c.pastas[self.pasta]
        if cmd == "SEARCH":
            a = int(re.fullmatch(r"UID (\d+):\*", args[1]).group(1))
            uids = sorted(u for u in caixa if u >= a) or ([max(caixa)] if caixa else [])   # como o IMAP: n:* ≥ 1
            return "OK", [" ".join(map(str, uids)).encode()]
        if cmd == "FETCH":
            raw = caixa[int(args[0])]
            return "OK", [(f"1 (UID {args[0]} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        if cmd == "STORE":
            return ("NO", [b"nao"]) if self.c.falha_store else ("OK", [b"ok"])
        if cmd == "EXPUNGE":
            caixa.pop(int(args[0]), None)
            return "OK", [b"ok"]
        raise AssertionError(cmd)

    def logout(self):
        return "BYE", [b""]


# --- mundo temporário --------------------------------------------------------------------------------------------------
class Mundo:
    """O runner (office.yaml de teste, `<dados>/agentes/squads.json`, `<dados>/decisoes`) e o serviço (pasta própria,
    configuração 0644, credenciais 0600, `<dados>/accoes-entrada`) numa pasta temporária."""

    def __init__(self, tmp: Path, sqs: dict[str, str] | None = None):
        self.dir = Path(tmp)
        self.cfg, self.cfg_path = c.config(self.dir)
        self.sqs = sqs if sqs is not None else {"loja": "atendimento"}
        dados = Path(self.cfg["raizes"]["dados"])
        self.decisoes = dados / "decisoes"
        self.agentes = dados / "agentes"
        self.entrada = dados / "accoes-entrada"
        self.entrada.mkdir(mode=0o755)
        servico = self.dir / "accoes"
        for p in ("mensagens", "estado", "folhas"):
            (servico / p).mkdir(parents=True)
        os.chmod(servico, 0o755)
        (servico / "segredos").mkdir(mode=0o700)
        self.r = accoes.Raizes(servico, servico / "segredos", self.entrada, self.decisoes, self.agentes)
        self.registo()

    def registo(self, sqs: dict[str, str] | None = None) -> None:
        sqs = self.sqs if sqs is None else sqs
        reg = {"v": 1, "squads": [{"id": sid, "modelo": m, "nome": sid.capitalize(), "lead": "responsavel",
                                   "criada_em": "2026-11-01T10:00:00+00:00"} for sid, m in sqs.items()]}
        (self.agentes / "squads.json").write_text(json.dumps(reg), encoding="utf-8")

    def config(self, sqs: dict, telegram: dict | None = None) -> dict:
        d = {"v": 1, "squads": sqs, **({"telegram": telegram} if telegram else {})}
        self.r.config.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.chmod(self.r.config, 0o644)
        return d

    def segredos(self, texto: str) -> None:
        self.r.env.write_text(texto, encoding="utf-8")
        os.chmod(self.r.env, 0o600)

    def registar(self, m: str, sid: str, rem: str, message_id: str = "<orig@exemplo.test>",
                 autenticado: bool = True) -> None:
        (self.r.dir / "mensagens" / f"{m}.json").write_text(json.dumps(
            {"v": 1, "id": m, "squad": sid, "remetente": rem, "message_id": message_id, "pasta": "INBOX",
             "uidvalidity": 7, "uid": 1, "recebido": AGORA.isoformat(), "autenticado": autenticado}))

    def folha(self, nome: str = "leads", colunas=("nome", "contacto", "nota"), ficheiro: str | None = None) -> dict:
        return {nome: {"ficheiro": str(self.r.dir / "folhas" / f"{ficheiro or nome}.csv"), "colunas": list(colunas)}}

    def email(self, smtp_porta: int = 1, endereco: str = "geral@padaria.test") -> dict:
        return {"endereco": endereco, "pasta": "INBOX", "rascunhos": "Drafts", "authserv_id": "mx.padaria.test",
                "imap": {"host": "127.0.0.1", "porta": 1, "tls": False},
                "smtp": {"host": "127.0.0.1", "porta": smtp_porta, "tls": False}}

    def vista(self, sid: str = "loja") -> None:
        """Publica a vista como o colector (sem ligar a nenhuma caixa)."""
        cfg = accoes.ler_config(self.r.config)
        efd = os.open(self.entrada, os.O_RDONLY | os.O_DIRECTORY)
        try:
            sfd = colector._subpasta(efd, sid, 0o755)
            try:
                colector.publicar_vista(self.r, cfg, sid, sfd, accoes.PERFIL)
            finally:
                os.close(sfd)
        finally:
            os.close(efd)

    def pedir(self, texto: str, sid: str = "loja", now: float = NOW, tid: str = TID) -> str | None:
        """O gancho do runner; devolve o nome do pedido novo (ou None)."""
        antes = set(os.listdir(self.decisoes))
        self.avisos = accoes_propor.propor(self.cfg, sid, tid, texto, now)
        novos = sorted(set(os.listdir(self.decisoes)) - antes - {accoes.REGISTOS})
        assert len(novos) <= 1, novos
        # C1: com recusadas ou de fora, uma linha para os issues da tarefa a apontar o pedido
        assert all(re.fullmatch(rf"acções: \d+ recusadas ou de fora \(ver decisoes/{re.escape(novos[0])}\)", a)
                   for a in self.avisos) and len(self.avisos) <= 1, self.avisos
        return novos[0] if novos else None

    def md(self, nome: str) -> str:
        return (self.decisoes / nome).read_text(encoding="utf-8")

    def reg(self, nome: str) -> dict | None:
        p = self.decisoes / accoes.REGISTOS / f"{nome[:-3]}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def aprovar(self, nome: str, *aids: str, como: str = "aprovado", dia: str = "2026-11-18") -> None:
        p = self.decisoes / nome
        linhas = p.read_text(encoding="utf-8").split("\n")
        for aid in aids or ("A1",):
            i = next(k for k, ln in enumerate(linhas) if ln.startswith(f"- [{aid}] "))
            linhas[i] += f" → {como} ({dia})"
        p.write_text("\n".join(linhas), encoding="utf-8")

    def sha(self, nome: str, aid: str = "A1") -> str:
        return next(a["sha256"] for a in self.reg(nome)["accoes"] if a["id"] == aid)
