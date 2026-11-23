"""Guardião da Conversa (ADR-023, revisão S2), pedido n.º 1 do dono do projecto: um ficheiro servido pela `fabrica`
alterado não aprova sem o clique.

(a) Arranque: um byte trocado em `web/`, um ficheiro a mais, um symlink, um manifesto inválido ou o `guardiao.py` fora
    do `SHA256SUMS` fazem o guardião sair com 78 sem servir nada (nem o socket fica criado).
(b) Com um cartão `permissao` pendente (a ponte real e o `claude` falso parados no MCP), um cliente sem a chave (o JS
    do Office alterado) tenta `responder_permissao`: com o `Origin` do Office dá 403; com sessão e CSRF válidos mas a
    assinatura errada ou ausente dá 403; com um `pid` já usado dá 409. Em todos, `verificados/` fica sem resposta e o
    cartão continua pendente. Só o pedido assinado (o clique) dá 202 e a ponte resolve-o.
Nunca a `~` real nem o `claude` verdadeiro (tests/bin/claude é o falso)."""
import hashlib
import http.client
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import chat
import tests.test_chat_ponte as tcp
from tests.guardiao_base import CHAVE, VECTORES, VERIFICADOR, WEB, entrada, gm, opt_falso

ROOT = Path(__file__).resolve().parent.parent
GUARDIAO = ROOT / "chat_host" / "guardiao.py"


class HTTPUnix(http.client.HTTPConnection):
    def __init__(self, caminho: str):
        super().__init__("conversa.exemplo.pt", timeout=10)
        self.caminho = caminho

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(self.caminho)


class Arranque(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.t = Path(t.name)

    def opt(self, **k) -> Path:
        d = Path(tempfile.mkdtemp(dir=self.t))
        return opt_falso(d, **k)

    def correr(self, opt: Path, esperar=True):
        (self.t / "sock").mkdir(exist_ok=True)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CONVERSA_OPT": str(opt),
               "CONVERSA_PASTA": str(self.t), "CONVERSA_GUARDIAO_ESTADO": str(self.t / "estado"),
               "CONVERSA_ESCUTA": f"unix:{self.t / 'sock' / 'guardiao.sock'}"}
        p = subprocess.Popen([sys.executable, "-I", str(GUARDIAO)], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        if not esperar:
            return p
        try:
            out, err = p.communicate(timeout=30)
        finally:
            p.kill()
        return p.returncode, err

    def test_byte_trocado_ou_ficheiro_a_mais_sai_com_78_sem_servir(self):
        casos = {}
        o = self.opt()
        f = o / "web" / "conversa_inicio.js"
        f.write_bytes(f.read_bytes()[:-1] + b"X")   # o JS servido pela fabrica alterado num byte
        casos["byte trocado"] = (o, "web/conversa_inicio.js não bate")
        o = self.opt()
        (o / "web" / "extra.js").write_text("fetch('/api/conversa/pedido')")
        casos["ficheiro a mais"] = (o, "web/extra.js não está no manifesto")
        o = self.opt()
        (o / "web" / "fonts" / "outra.woff2").write_bytes(b"x")
        casos["ficheiro a mais numa subpasta"] = (o, "web/fonts/outra.woff2 não está no manifesto")
        for nome, (opt, msg) in casos.items():
            with self.subTest(nome=nome):
                code, err = self.correr(opt)
                self.assertEqual(code, gm.SAIDA_CONFIG, err)
                self.assertIn(msg, err)
                self.assertFalse((self.t / "sock" / "guardiao.sock").exists())   # nada servido

    def test_conferir_recusas(self):
        def recusa(opt, msg):
            with self.assertRaises(gm.Recusa) as e:
                gm.conferir(opt)
            self.assertIn(msg, str(e.exception))

        o = self.opt()
        somas = (o / "SHA256SUMS").read_text()
        (o / "SHA256SUMS").write_text("".join(ln + "\n" for ln in somas.splitlines()
                                              if not ln.endswith("chat_host/guardiao.py")))
        recusa(o, "sem chat_host/guardiao.py")
        o = self.opt()
        (o / "chat_host" / "guardiao.py").write_text("print('outro')\n")
        recusa(o, "chat_host/guardiao.py não bate")
        o = self.opt()
        (o / "SHA256SUMS").unlink()
        recusa(o, "SHA256SUMS ilegível")
        for mau in ("../chat.py", "/etc/passwd", "a b c"):
            o = self.opt()
            with open(o / "SHA256SUMS", "a") as f:
                f.write(f"{'0' * 64}  {mau}\n" if mau != "a b c" else "nao-e-uma-soma\n")
            with self.subTest(mau=mau):
                recusa(o, "linha inválida")
        for web, msg in (({"index.html": b"x"}, "conversa.manifesto inválido"),
                         ({"conversa.html": b"x", "Maiuscula.js": b"x"}, "conversa.manifesto inválido"),
                         ({"conversa.html": b"x", "a.exe": b"x"}, "conversa.manifesto inválido")):
            with self.subTest(web=list(web)):
                recusa(self.opt(web=web), msg)
        o = self.opt()
        (o / "web" / "conversa.css").unlink()
        recusa(o, "conversa.css")
        o = self.opt()
        (o / "web" / "fonts" / "letra.woff2").unlink()
        os.symlink(o / "web" / "conversa.css", o / "web" / "fonts" / "letra.woff2")
        recusa(o, "letra.woff2")
        o = self.opt()
        os.symlink("/etc", o / "web" / "etc")
        recusa(o, "web/etc é um symlink")
        self.assertEqual(gm.conferir(self.opt()), WEB)

    def test_arranque_certo_serve_pelo_socket_0660(self):
        p = self.correr(self.opt(), esperar=False)
        sock = self.t / "sock" / "guardiao.sock"
        try:
            fim = time.monotonic() + 20
            while not sock.exists() and p.poll() is None and time.monotonic() < fim:
                time.sleep(0.05)
            self.assertTrue(sock.exists(), p.stderr.read() if p.poll() is not None else "sem socket")
            self.assertEqual(os.stat(sock).st_mode & 0o777, 0o660)
            c = HTTPUnix(str(sock))
            c.request("GET", "/")
            r = c.getresponse()
            self.assertEqual((r.status, r.read(), r.getheader("X-Frame-Options")), (200, WEB["conversa.html"], "DENY"))
            c.close()
            c = HTTPUnix(str(sock))   # um pedido sem assinatura pelo socket (como a fabrica): 401/403, nada escrito
            c.request("POST", "/api/conversa/pedido", body=b"{}", headers={
                "Content-Type": "application/json", "X-Forwarded-Proto": "https",
                "Origin": "https://conversa.exemplo.pt"})
            self.assertEqual(c.getresponse().status, 401)
            c.close()
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(10), 0)
            self.assertFalse(sock.exists())
        finally:
            p.kill()
            p.wait()
            for f in (p.stdout, p.stderr):
                f.close()

    def test_escuta_invalida_78(self):
        for escuta in ("0.0.0.0:8080", "unix:relativo.sock", "10.0.0.1:80", "127.0.0.1:x"):
            with self.subTest(escuta=escuta), self.assertRaises(ValueError):
                gm.servidor(escuta, None)


class CartaoSemClique(tcp.PonteBase):
    """(b): a ponte real com o `claude` falso parado num cartão e o guardião real numa porta local."""

    OFFICE = "https://office.exemplo.pt"

    def setUp(self):
        super().setUp()
        self.chave = CHAVE   # os pedidos que o PonteBase escreve (não usados aqui) com a chave dos vectores
        est = self.root / "guardiao"
        est.mkdir(mode=0o700)
        (est / "entrada.json").write_text(json.dumps(entrada()))
        (self.root / "publico").mkdir()
        cfg = gm.config({"CONVERSA_PASTA": str(self.root), "CONVERSA_GUARDIAO_ESTADO": str(est),
                         "CONVERSA_TLS_TESTE": "1", "CONVERSA_FUSO": "UTC"})
        self.g = gm.Guardiao(cfg, dict(WEB))
        self.srv = gm.servidor("127.0.0.1:0", self.g)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.srv.server_address[1]}"
        self.cookie = self.csrf = None

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def http(self, method, path, corpo=None, origem=None, **cab):
        h = {"Origin": origem or f"http://{self.host}", "Content-Type": "application/json", **cab}
        if self.cookie:
            h["Cookie"] = f"{gm.COOKIE}={self.cookie}"
        if self.csrf:
            h["X-Csrf"] = self.csrf
        c = http.client.HTTPConnection(self.host, timeout=10)
        try:
            c.request(method, path, body=None if corpo is None else json.dumps(corpo).encode(), headers=h)
            r = c.getresponse()
            return r.status, r.getheader("Set-Cookie"), json.loads(r.read() or b"null")
        finally:
            c.close()

    def assinado(self, acao, chave=CHAVE, **campos):
        d = {"acao": acao, "pid": secrets.token_hex(8), "t": int(time.time() * 1000), **campos}
        d["assinatura"] = chat.assinar(chave, d)
        return d

    def post(self, corpo, **k):
        code, _, d = self.http("POST", "/api/conversa/pedido", corpo, **k)
        return code, d

    def respostas_em_verificados(self) -> list[dict]:
        out = []
        for f in (self.root / "verificados").glob("*.json"):
            d = json.loads(f.read_text())
            if d["acao"] == "responder_permissao":
                out.append(d)
        return out

    def test_s2_so_o_clique_assinado_aprova(self):
        self.cenario(passos=[{"ferramenta": "Bash", "input": {"command": "echo corri >> corridos.txt"}}])
        self.nova_ponte()
        # o browser regista-se com o código (uso único) e a sua chave K (a dos vectores)
        code, ck, d = self.http("POST", "/api/conversa/registar",
                                {"verificador": VERIFICADOR, "kid": VECTORES["kid"], "chave": VECTORES["chave"]})
        self.assertEqual(code, 200)
        self.cookie, self.csrf = ck.split(";")[0].split("=", 1)[1], d["csrf"]
        self.assertEqual(self.http("POST", "/api/conversa/prefs", {"aviso_tokens": True})[0], 200)
        cid = tcp.novo_id()
        self.assertEqual(self.post(self.assinado("nova", conversa=cid))[0], 202)
        self.ate(lambda: (self.root / "conversas" / cid / "meta.json").exists(), msg="conversa criada")
        enviar = self.assinado("enviar", conversa=cid, texto="corre o comando", modo="normal", modelo="omissao",
                               permissoes="cartao")
        self.assertEqual(self.post(enviar)[0], 202)
        p = self.pendente(cid)
        campos = {"conversa": cid, "nonce": p["nonce"], "escolha": "uma_vez"}
        certo = self.assinado("responder_permissao", **campos)
        sem = {k: v for k, v in certo.items() if k != "assinatura"}
        derivada = hashlib.pbkdf2_hmac("sha256", VECTORES["codigo"].encode(), bytes.fromhex(VECTORES["sal_ponte"]),
                                       600000, 32)   # a chave de antes (G2): saber o código já não assina
        ataques = {
            "Origin do Office": (self.post(certo, origem=self.OFFICE), (403, {"error": chat.ORIGEM})),
            "chave errada": (self.post(self.assinado("responder_permissao", chave=bytes(32), **campos)),
                             (403, {"error": gm.ASSINATURA})),
            "sem assinatura": (self.post(sem), (403, {"error": gm.ASSINATURA})),
            "assinatura de outro pedido": (self.post({**certo, "escolha": "conversa"}),
                                           (403, {"error": gm.ASSINATURA})),
            "pid já usado": (self.post(self.assinado("responder_permissao", pid=enviar["pid"], **campos)),
                             (409, {"error": gm.REPETIDO})),
            "chave derivada do código (G2)": (self.post(self.assinado("responder_permissao", chave=derivada, **campos)),
                                              (403, {"error": gm.ASSINATURA})),
            "registar outro browser com o código já usado (G2)":
                (self.http("POST", "/api/conversa/registar", {"verificador": VERIFICADOR, "kid": "0" * 16,
                                                              "chave": "1" * 64})[0::2],
                 (403, {"error": gm.CODIGO_USADO})),
        }
        for nome, (obtido, esperado) in ataques.items():
            with self.subTest(nome=nome):
                self.assertEqual(obtido, esperado)
        for _ in range(10):
            self.p.passo(0.02)
        self.g.limpar(time.time())
        self.assertEqual(self.respostas_em_verificados(), [])
        self.assertEqual(self.meta(cid)["pendentes"][0]["nonce"], p["nonce"])   # o cartão continua pendente
        self.assertEqual(self.registos()[0]["respostas"], [])
        self.assertFalse((self.root / "trabalho" / "corridos.txt").exists())
        # o clique (o pedido assinado na origem da Conversa) aprova
        self.assertEqual(self.post(certo), (202, {"ok": True, "conversa": cid}))
        self.fim(cid)
        self.assertEqual(self.registos()[0]["respostas"][0]["resposta"]["behavior"], "allow")
        self.assertTrue((self.root / "trabalho" / "corridos.txt").exists())
        self.g.limpar(time.time())   # a ponte viu tudo: o guardião apaga
        self.assertEqual(list((self.root / "verificados").glob("*.json")), [])
        self.assertEqual(self.post(certo), (409, {"error": gm.REPETIDO}))   # o mesmo clique não se repete


if __name__ == "__main__":
    unittest.main()
