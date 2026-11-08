"""QA: E2E do push com o server.py REAL em subprocesso (pastas temporárias, PUSH_DIR temporária, porta livre).

O único falso é a saída para a rede: um invólucro carrega o server.py como __main__ (runpy) depois de trocar o
push._abrir por um que grava os pedidos num ficheiro e devolve o código de <qa>/codigo (201 por omissão), e de contar
os ciclos do vigia. Sem rede, sem relógio falso (as metas são relativas à hora actual). «Sem cryptography» simula-se
com sys.modules["cryptography"] = None no invólucro, por isso corre com qualquer python.
"""
import base64
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import push
from tests import cfg
from tests.test_push import curtas

ROOT = Path(__file__).resolve().parent.parent
SEM_CRYPTO = "cryptography não instalada (só na imagem Docker)"
FCM = "https://fcm.googleapis.com/fcm/send/qa-endpoint-SEGREDO"
OFFICE_YAML = """squads:
  - id: dev
    nome: Dev
  - id: rnd
    nome: R&D
    formato: sessao
projectos:
  - id: loja
    nome: Loja
"""
OFFICE_CFG = cfg.ler(OFFICE_YAML)   # a mesma config no servidor real e nos testes

INVOLUCRO = r'''
import base64, json, os, runpy, sys
repo, qa = sys.argv[1], sys.argv[2]
if os.environ.get("QA_SEM_CRYPTO"):
    sys.modules["cryptography"] = None
sys.path.insert(0, repo)
import push

def abrir(req, timeout):
    try:
        code = int(open(os.path.join(qa, "codigo")).read())
    except FileNotFoundError:
        code = 201
    with open(os.path.join(qa, "pedidos.jsonl"), "a") as f:
        f.write(json.dumps({"url": req.full_url, "timeout": timeout,
                            "headers": {k.lower(): v for k, v in req.header_items()},
                            "corpo": base64.b64encode(req.data).decode()}) + "\n")
    return code

push._abrir = abrir
_ciclo = push.Servico.ciclo

def ciclo(self, state, now):
    n = _ciclo(self, state, now)
    with open(os.path.join(qa, "ciclos"), "a") as f:
        f.write(f"{n}\n")
    return n

push.Servico.ciclo = ciclo
sys.argv = [os.path.join(repo, "server.py")]
runpy.run_path(os.path.join(repo, "server.py"), run_name="__main__")
'''


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def esperar(cond, segundos=15.0, msg="timeout"):
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        v = cond()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError(msg)


class ServidorReal:
    def __init__(self, raiz: Path, pasta_push: Path, sem_crypto=False):
        self.raiz, self.qa = raiz, raiz / "qa"
        self.qa.mkdir(exist_ok=True)
        inv = raiz / "involucro.py"
        inv.write_text(INVOLUCRO)
        self.port = porta_livre()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PUSH_", "QA_"))}
        (raiz / "office.yaml").write_text(OFFICE_YAML)
        env.update(TASKS_DIR=str(raiz / "tasks"), CLAUDE_PROJECTS_DIR=str(raiz / "claude"),
                   SQUADS_DIR=str(raiz / "squads"), QUADRO_DIR=str(raiz / "quadro"),
                   DECISOES_FILA_DIR=str(raiz / "fila"), APROVACOES_DIR=str(raiz / "aprovacoes"),
                   BIBLIOTECA_DIR=str(raiz / "biblioteca"), OFFICE_CONFIG=str(raiz / "office.yaml"),
                   PUSH_DIR=str(pasta_push),
                   PUSH_CONTACTO="https://squad.example", PORT=str(self.port), PYTHONUNBUFFERED="1")
        if sem_crypto:
            env["QA_SEM_CRYPTO"] = "1"
        self.log = open(raiz / f"server-{self.port}.log", "wb")
        self.proc = subprocess.Popen([sys.executable, str(inv), str(ROOT), str(self.qa)], env=env, cwd=str(raiz),
                                     stdout=self.log, stderr=subprocess.STDOUT)
        self.base = f"http://127.0.0.1:{self.port}"
        esperar(self._vivo, 15, "server.py não arrancou")

    def _vivo(self):
        if self.proc.poll() is not None:
            raise AssertionError(f"server.py saiu ({self.proc.returncode}): {self.saida()}")
        try:
            return self.get("/healthz")[0] == 200
        except OSError:
            return False

    def parar(self):
        self.proc.terminate()
        self.proc.wait(10)
        self.log.close()

    def saida(self) -> str:
        if not self.log.closed:
            self.log.flush()
        return (self.raiz / f"server-{self.port}.log").read_text(errors="replace")

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read(), dict(e.headers)

    def get_json(self, path):
        code, corpo, _ = self.get(path)
        return code, json.loads(corpo)

    def token(self) -> str:
        return self.get_json("/api/state")[1]["token"]

    def post(self, rota, data, **h):
        hdr = {"Content-Type": "application/json", "Origin": self.base}
        hdr.update(h)
        req = urllib.request.Request(f"{self.base}/api/push/{rota}", data=json.dumps(data).encode(), method="POST",
                                     headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"null")

    def ciclos(self) -> list[int]:
        f = self.qa / "ciclos"
        return [int(x) for x in f.read_text().split()] if f.exists() else []

    def pedidos(self) -> list[dict]:
        f = self.qa / "pedidos.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        for d in ("tasks", "claude", "squads"):
            (self.raiz / d).mkdir()
        self.pasta = self.raiz / "push"
        self.pasta.mkdir()
        self.srv = []

    def tearDown(self):
        for s in self.srv:
            if s.proc.poll() is None:
                s.parar()
        self.tmp.cleanup()

    def arranca(self, pasta=None, **kw) -> ServidorReal:
        s = ServidorReal(self.raiz, pasta or self.pasta, **kw)
        self.srv.append(s)
        return s

    def meta(self, tid, status="concluída", fim_ha_s=60, mode="goal", project="loja", **kw):
        agora = datetime.now(timezone.utc)
        m = {"id": tid, "project": project, "mode": mode, "status": status, "pr": "",
             "started_at": iso(agora - timedelta(seconds=fim_ha_s + 600))}
        if status != "a correr":
            m["finished_at"] = iso(agora - timedelta(seconds=fim_ha_s))
        m.update(kw)
        p = self.raiz / "tasks" / tid / "meta.json"
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(m))


class SemCryptographyE2E(Base):
    # AC-4 com o server.py real: push desligado (503 sem_cryptography) e o resto igual; nada escrito em PUSH_DIR
    def test_ac4_server_real_sem_cryptography(self):
        self.meta("20261105-0900-a")
        s = self.arranca(sem_crypto=True)
        code, out = s.get_json("/api/push/chave")
        self.assertEqual((code, out), (503, {"error": "notificações no telemóvel não configuradas no servidor",
                                             "motivo": "sem_cryptography"}))
        tok = s.token()
        sub = {"endpoint": FCM, "keys": {"p256dh": push.b64u(b"\x04" + b"\x01" * 64), "auth": push.b64u(b"a" * 16)}}
        for rota, d in (("subscrever", {"token": tok, "subscricao": sub}), ("cancelar", {"token": tok, "endpoint": FCM}),
                        ("teste", {"token": tok, "endpoint": FCM})):
            with self.subTest(rota=rota):
                self.assertEqual(s.post(rota, d), (503, {"error": push.ERRO_503, "motivo": "sem_cryptography"}))
                self.assertEqual(s.post(rota, {**d, "token": "errado"})[0], 403)   # 403 antes do 503
        code, st = s.get_json("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual([t["id"] for t in st["tasks"]], ["20261105-0900-a"])
        self.assertEqual(s.get("/healthz")[:2], (200, b"ok"))
        for f in ("/", "/definicoes.js", "/definicoes.css", "/sw.js", "/manifest.json", "/icon-192.png"):
            with self.subTest(f=f):
                self.assertEqual(s.get(f)[0], 200)
        time.sleep(1.5)   # o vigia já correu pelo menos uma vez
        self.assertEqual(os.listdir(self.pasta), [])
        self.assertEqual(s.pedidos(), [])
        self.assertIn("push: desligado (sem_cryptography)", s.saida())


@unittest.skipUnless(push.ec, SEM_CRYPTO)
class ComCryptographyE2E(Base):
    def setUp(self):
        super().setUp()
        from cryptography.hazmat.primitives.asymmetric import ec
        self.ua = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)

    def subscricao(self, endpoint=FCM):
        return {"endpoint": endpoint, "keys": {"p256dh": push.b64u(push._ponto(self.ua.public_key())),
                                               "auth": push.b64u(self.auth)}}

    def payload(self, pedido) -> dict:
        from tests.test_push import decifrar
        return json.loads(decifrar(base64.b64decode(pedido["corpo"]), self.ua, self.auth))

    def test_ac3_ac4_chave_0600_reutilizada_e_sem_pasta(self):
        s = self.arranca()
        code, out = s.get_json("/api/push/chave")
        self.assertEqual(code, 200)
        chave = out["chave"]
        self.assertEqual(len(push.deb64u(chave)), 65)
        self.assertEqual(stat.S_IMODE((self.pasta / "vapid").stat().st_mode), 0o600)
        self.assertNotIn(b"PRIVATE", s.get("/api/push/chave")[1])
        pem = (self.pasta / "vapid").read_text()
        s.parar()
        s2 = self.arranca()
        self.assertEqual(s2.get_json("/api/push/chave"), (200, {"chave": chave}))
        self.assertEqual((self.pasta / "vapid").read_text(), pem)
        self.assertNotIn("PRIVATE", s2.saida() + s.saida())
        s2.parar()
        s3 = self.arranca(pasta=self.raiz / "nao-existe")
        self.assertEqual(s3.get_json("/api/push/chave"),
                         (503, {"error": push.ERRO_503, "motivo": "sem_pasta"}))
        self.assertEqual(s3.post("subscrever", {"token": s3.token(), "subscricao": self.subscricao()})[0], 503)
        self.assertEqual(s3.get("/healthz")[0], 200)

    def test_ac5_a_ac8_rotas_no_server_real(self):
        s = self.arranca()
        tok = s.token()
        d = {"token": tok, "subscricao": self.subscricao()}
        self.assertEqual(s.post("subscrever", d, **{"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(s.post("subscrever", d, Origin="https://evil.example")[0], 403)
        self.assertEqual(s.post("subscrever", {**d, "token": "x"})[0], 403)
        self.assertEqual(s.post("subscrever", {**d, "lixo": "x" * 4100})[0], 413)
        self.assertEqual(s.post("subscrever", d), (200, {"prefs": push.prefs_omissao()}))
        p = push.prefs_omissao()
        p["squads"]["rnd"] = False
        self.assertEqual(s.post("subscrever", {**d, "prefs": p}), (200, {"prefs": p}))
        self.assertEqual(s.post("subscrever", d), (200, {"prefs": p}))
        self.assertEqual(s.post("subscrever", {**d, "subscricao": self.subscricao("http://fcm.googleapis.com/x")}),
                         (400, {"error": "endpoint inválido"}))
        self.assertEqual(s.post("subscrever", {**d, "subscricao": self.subscricao("https://127.0.0.1/x")}),
                         (400, {"error": "serviço de push não permitido"}))
        subs = json.loads((self.pasta / "subscricoes.json").read_text())
        self.assertEqual((subs["v"], [x["endpoint"] for x in subs["subscricoes"]]), (1, [FCM]))
        self.assertEqual(stat.S_IMODE((self.pasta / "subscricoes.json").stat().st_mode), 0o600)
        self.assertEqual(s.post("cancelar", {"token": tok, "endpoint": FCM}), (200, {"ok": True}))
        self.assertEqual(s.post("cancelar", {"token": tok, "endpoint": FCM}), (200, {"ok": True}))
        self.assertEqual(json.loads((self.pasta / "subscricoes.json").read_text())["subscricoes"], [])
        self.assertEqual([f for f in os.listdir(self.pasta) if f.endswith(".tmp")], [])

    def test_rota_teste_200_429_410_502_no_server_real(self):
        s = self.arranca()
        tok = s.token()
        a, b, c = f"{FCM}-a", f"{FCM}-b", f"{FCM}-c"
        for e in (a, b, c):
            self.assertEqual(s.post("subscrever", {"token": tok, "subscricao": self.subscricao(e)})[0], 200)
        self.assertEqual(s.post("teste", {"token": tok, "endpoint": a}), (200, {"ok": True}))
        self.assertEqual(s.post("teste", {"token": tok, "endpoint": a})[0], 429)
        (s.qa / "codigo").write_text("410")
        self.assertEqual(s.post("teste", {"token": tok, "endpoint": b}),
                         (410, {"error": "subscrição expirada, liga de novo"}))
        (s.qa / "codigo").write_text("500")
        self.assertEqual(s.post("teste", {"token": tok, "endpoint": c}),
                         (502, {"error": "o serviço de push recusou (500)"}))
        subs = [x["endpoint"] for x in json.loads((self.pasta / "subscricoes.json").read_text())["subscricoes"]]
        self.assertEqual(subs, [a, c])   # 410 apaga, 500 mantém
        ped = s.pedidos()
        self.assertEqual(len(ped), 3)
        self.assertEqual(self.payload(ped[0]), push.TESTE)
        h = ped[0]["headers"]
        self.assertEqual((ped[0]["url"], ped[0]["timeout"], h["ttl"], h["content-encoding"]),
                         (a, 10, "21600", "aes128gcm"))
        self.assertTrue(h["authorization"].endswith(f", k={s.get_json('/api/push/chave')[1]['chave']}"))
        self.assertNotIn("qa-endpoint-SEGREDO", s.saida())   # log só com código e host
        self.assertIn("push: 410 fcm.googleapis.com", s.saida())

    def test_ac9_ac10_ac12_vigia_sobrevive_a_reinicios(self):
        # A: 1.º arranque com 3 terminadas → semeia 3 chaves e não envia nada
        self.meta("20261105-0900-a")
        self.meta("20261105-0901-b", status="erro")
        self.meta("20261105-0902-c")
        self.meta("20261105-0903-d", status="a correr")
        s = self.arranca()
        esperar(lambda: s.ciclos(), msg="o vigia não correu")
        self.assertEqual(s.ciclos(), [0])
        nots = json.loads((self.pasta / "notificadas.json").read_text())
        self.assertEqual(curtas(nots["chaves"].keys()), {"fim:dev:20261105-0900-a", "fim:dev:20261105-0901-b",
                                               "fim:dev:20261105-0902-c"})
        self.assertEqual(stat.S_IMODE((self.pasta / "notificadas.json").stat().st_mode), 0o600)
        self.assertEqual(s.post("subscrever", {"token": s.token(), "subscricao": self.subscricao()})[0], 200)
        s.parar()
        self.assertEqual(s.pedidos(), [])
        (s.qa / "ciclos").unlink()
        # B: reinício; a que corria acabou agora e outra acabou há 7 h (D5) → exactamente 1 envio
        self.meta("20261105-0903-d")
        self.meta("20261105-0904-velha", fim_ha_s=7 * 3600)
        s = self.arranca()
        esperar(lambda: s.ciclos(), msg="o vigia não correu")
        self.assertEqual(s.ciclos(), [1])
        ped = s.pedidos()
        self.assertEqual(len(ped), 1)
        self.assertEqual(self.payload(ped[0]), {"title": "✅ Dev · loja: goal concluído",
                                                "body": "▱▱▱▱▱ loja · d · concluído", "tag": "20261105-0903-d",
                                                "url": "./#20261105-0903-d", "critico": False})
        chaves = json.loads((self.pasta / "notificadas.json").read_text())["chaves"]
        self.assertIn("fim:dev:20261105-0904-velha", curtas(chaves))   # semeada sem envio
        s.parar()
        (s.qa / "ciclos").unlink()
        # C: novo reinício com o mesmo estado → 0
        s = self.arranca()
        esperar(lambda: s.ciclos(), msg="o vigia não correu")
        self.assertEqual(s.ciclos(), [0])
        self.assertEqual(len(s.pedidos()), 1)
        self.assertNotIn("SEGREDO", s.saida())


if __name__ == "__main__":
    unittest.main()
