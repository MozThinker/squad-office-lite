"""Testes HTTP das rotas /api/push/* (servidor real em porta 0, PUSH_DIR temporária; só stdlib + cryptography opcional)."""
import contextlib
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import push
import server
from tests import cfg

OFFICE_CFG = cfg.LEGADO

GLOBAIS = ("TASKS", "CLAUDE", "SQUADS_DIR", "APROVACOES", "PUSH")
FCM = "https://fcm.googleapis.com/fcm/send/abc"
SEM_CRYPTO = "cryptography não instalada (só na imagem Docker)"


class PushHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pasta = root / "push"
        self.pasta.mkdir()
        for d in ("tasks", "claude", "squads"):
            (root / d).mkdir()
        self.codigos = [201]
        self.old = tuple(getattr(server, g) for g in GLOBAIS)
        server.TASKS, server.CLAUDE, server.SQUADS_DIR, server.APROVACOES = (root / "tasks", root / "claude",
                                                                             root / "squads", {})
        server.PUSH = push.Servico(self.pasta, "https://squad.example", server.LOCAL_TZ, server.DONE_STATUS,
                                   abrir=lambda req, timeout: self.codigos[0])
        server._cache["at"] = 0.0
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for g, v in zip(GLOBAIS, self.old):
            setattr(server, g, v)
        server._cache["at"] = 0.0
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection(self.host, timeout=5)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            raw = r.read()
            return r.status, (json.loads(raw) if raw and path.startswith("/api/") else raw), dict(r.getheaders())
        finally:
            c.close()

    def post(self, rota, data=None, raw=None, **headers):
        h = {"Content-Type": "application/json", "Origin": f"http://{self.host}"}
        h.update({k.replace("_", "-"): v for k, v in headers.items()})
        h = {k: v for k, v in h.items() if v is not None}
        corpo = raw if raw is not None else json.dumps(data).encode()
        code, out, _ = self.req("POST", f"/api/push/{rota}", corpo, h)
        return code, out

    def subscricao(self, endpoint=FCM):
        from cryptography.hazmat.primitives.asymmetric import ec
        ua = ec.generate_private_key(ec.SECP256R1()).public_key()
        return {"endpoint": endpoint, "keys": {"p256dh": push.b64u(push._ponto(ua)), "auth": push.b64u(b"a" * 16)}}

    # --- AC-4 ---
    def test_ac4_sem_cryptography(self):
        with mock.patch.object(push, "ec", None):
            code, out, h = self.req("GET", "/api/push/chave")
            self.assertEqual((code, out), (503, {"error": "notificações no telemóvel não configuradas no servidor",
                                                 "motivo": "sem_cryptography"}))
            self.assertEqual((h["Content-Type"], h["Cache-Control"]), ("application/json; charset=utf-8", "no-store"))
            for rota in ("subscrever", "cancelar", "teste"):
                with self.subTest(rota=rota):
                    code, out = self.post(rota, {"token": server.TOKEN, "endpoint": FCM})
                    self.assertEqual((code, out["motivo"]), (503, "sem_cryptography"))
            self.assertEqual(self.req("GET", "/api/state")[0], 200)
            self.assertEqual(self.req("GET", "/healthz")[:2], (200, b"ok"))
            self.assertEqual(self.req("GET", "/")[0], 200)
        self.assertEqual(os.listdir(self.pasta), [])

    @unittest.skipUnless(push.ec, SEM_CRYPTO)
    def test_ac4_sem_pasta(self):
        server.PUSH = push.Servico(self.pasta / "nao", "x", server.LOCAL_TZ, server.DONE_STATUS)
        code, out, _ = self.req("GET", "/api/push/chave")
        self.assertEqual((code, out["motivo"]), (503, "sem_pasta"))
        self.assertEqual(self.post("subscrever", {"token": server.TOKEN, "subscricao": self.subscricao()})[0], 503)
        self.assertEqual(self.req("GET", "/api/state")[0], 200)

    # --- AC-5 (antes do 503: corre também sem cryptography) ---
    def test_ac5_protecao_dos_post(self):
        for rota in ("subscrever", "cancelar", "teste"):
            with self.subTest(rota=rota):
                d = {"token": server.TOKEN, "endpoint": FCM}
                self.assertEqual(self.post(rota, d, Content_Type="text/plain")[0], 403)
                self.assertEqual(self.post(rota, {"endpoint": FCM})[0], 403)
                self.assertEqual(self.post(rota, {**d, "token": "x"})[0], 403)
                self.assertEqual(self.post(rota, d, Origin="https://evil.example")[0], 403)
                self.assertEqual(self.post(rota, d, Origin=None)[0], 403)   # sem Origin nem Sec-Fetch-Site
                self.assertEqual(self.post(rota, raw=b"{" + b" " * 4095 + b"}")[0], 413)
                self.assertEqual(self.post(rota, raw=b"[1]")[0], 400)
        self.assertEqual(self.post("outra", {"token": server.TOKEN})[0], 404)

    # --- AC-6/7/8 e teste, ponta a ponta ---
    @unittest.skipUnless(push.ec, SEM_CRYPTO)
    def test_chave(self):
        code, out, _ = self.req("GET", "/api/push/chave")
        self.assertEqual(code, 200)
        self.assertEqual(set(out), {"chave"})
        self.assertEqual(len(push.deb64u(out["chave"])), 65)
        self.assertNotIn("=", out["chave"])
        self.assertEqual(self.req("GET", "/api/push/chave")[1], out)
        self.assertNotIn(b"PRIVATE", json.dumps(out).encode())

    @unittest.skipUnless(push.ec, SEM_CRYPTO)
    def test_ac6_ac7_ac8_ponta_a_ponta(self):
        sub, tok = self.subscricao(), server.TOKEN
        code, out = self.post("subscrever", {"token": tok, "subscricao": sub})
        self.assertEqual((code, out), (200, {"prefs": push.prefs_omissao()}))
        p = push.prefs_omissao()
        p["silencio"].update(ligado=True, de="22:00", ate="08:00")
        self.assertEqual(self.post("subscrever", {"token": tok, "subscricao": sub, "prefs": p}), (200, {"prefs": p}))
        self.assertEqual(self.post("subscrever", {"token": tok, "subscricao": sub}), (200, {"prefs": p}))
        maus = [
            ({**sub, "endpoint": "http://fcm.googleapis.com/x"}, None, "endpoint inválido"),
            ({**sub, "endpoint": "https://evil.example/x"}, None, "serviço de push não permitido"),
            ({**sub, "keys": {**sub["keys"], "p256dh": push.b64u(b"\x04" * 64)}}, None,
             "chaves da subscrição inválidas"),
            ({**sub, "keys": {**sub["keys"], "auth": push.b64u(b"a" * 17)}}, None, "chaves da subscrição inválidas"),
            (sub, {**p, "silencio": {"ligado": True, "de": "07:00", "ate": "07:00"}},
             "as horas de silêncio têm de ser diferentes"),
            (sub, {**p, "silencio": {"ligado": True, "de": "7h", "ate": "08:00"}}, "preferências inválidas"),
            (sub, {**p, "extra": True}, "campos desconhecidos"),
        ]
        for s, pr, msg in maus:
            d = {"token": tok, "subscricao": s, **({"prefs": pr} if pr else {})}
            with self.subTest(msg=msg):
                self.assertEqual(self.post("subscrever", d), (400, {"error": msg}))
        self.assertEqual(self.post("subscrever", {"token": tok, "subscricao": sub, "x": 1}),
                         (400, {"error": "campos desconhecidos"}))
        for i in range(push.MAX_SUBS - 1):
            self.assertEqual(self.post("subscrever", {"token": tok, "subscricao": {**sub, "endpoint": f"{FCM}{i}"}})[0],
                             200)
        self.assertEqual(self.post("subscrever", {"token": tok, "subscricao": {**sub, "endpoint": f"{FCM}x"}}),
                         (409, {"error": "demasiados dispositivos"}))
        self.assertEqual(self.post("cancelar", {"token": tok, "endpoint": FCM}), (200, {"ok": True}))
        self.assertEqual(self.post("cancelar", {"token": tok, "endpoint": FCM}), (200, {"ok": True}))
        subs = json.loads((self.pasta / "subscricoes.json").read_text())["subscricoes"]
        self.assertEqual(len(subs), push.MAX_SUBS - 1)
        self.assertNotIn(FCM, [s["endpoint"] for s in subs])

    @unittest.skipUnless(push.ec, SEM_CRYPTO)
    def test_ca11_prefs_antigas_e_novas(self):
        # SPEC barra/fases CA11: a forma antiga completa-se; a resposta vem sempre na forma nova
        sub, tok = self.subscricao(), server.TOKEN
        antiga = push.prefs_omissao()
        del antiga["eventos"]["fases"], antiga["fases"]
        self.assertEqual(self.post("subscrever", {"token": tok, "subscricao": sub, "prefs": antiga}),
                         (200, {"prefs": push.prefs_omissao()}))
        nova = push.prefs_omissao()
        nova["eventos"]["fases"] = True
        nova["fases"].update(SPEC=True, PR=False)
        self.assertEqual(self.post("subscrever", {"token": tok, "subscricao": sub, "prefs": nova}),
                         (200, {"prefs": nova}))
        for grupo, k, v, msg in (("fases", "X", True, "campos desconhecidos"),
                                 ("eventos", "fases", 1, "preferências inválidas"),
                                 ("fases", "QA", "sim", "preferências inválidas")):
            p = json.loads(json.dumps(nova))
            p[grupo][k] = v
            with self.subTest(grupo=grupo, k=k):
                self.assertEqual(self.post("subscrever", {"token": tok, "subscricao": sub, "prefs": p}),
                                 (400, {"error": msg}))
        self.assertEqual(json.loads((self.pasta / "subscricoes.json").read_text())["v"], 1)

    @unittest.skipUnless(push.ec, SEM_CRYPTO)
    def test_teste_404_200_429_502_410(self):
        tok, sub = server.TOKEN, self.subscricao()
        d = {"token": tok, "endpoint": FCM}
        self.assertEqual(self.post("teste", d), (404, {"error": "dispositivo não subscrito"}))
        self.post("subscrever", {"token": tok, "subscricao": sub})
        self.assertEqual(self.post("teste", d), (200, {"ok": True}))
        self.assertEqual(self.post("teste", d), (429, {"error": "espera uns segundos antes de outro teste"}))
        server.PUSH._testes.clear()
        self.codigos = [503]
        self.assertEqual(self.post("teste", d), (502, {"error": "o serviço de push recusou (503)"}))
        server.PUSH._testes.clear()
        self.codigos = [410]
        self.assertEqual(self.post("teste", d), (410, {"error": "subscrição expirada, liga de novo"}))
        self.assertEqual(self.post("teste", d)[0], 404)
        self.assertEqual(self.post("teste", {**d, "x": 1}), (400, {"error": "campos desconhecidos"}))


if __name__ == "__main__":
    unittest.main()
