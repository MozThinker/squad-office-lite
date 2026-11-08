"""GET /api/config e arranque com config ausente/vazia/partida (AC-a2, AC-a3): server.py real em porta alta."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api_config_exemplo.json"


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def valores(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from valores(v)
    elif isinstance(x, list):
        for v in x:
            yield from valores(v)
    else:
        yield x


class Servidor:
    def __init__(self, raiz: Path, config: Path, extra: dict | None = None):
        self.port = porta_livre()
        env = {k: v for k, v in os.environ.items() if k not in ("PUSH_CONTACTO",)}
        env.update(PORT=str(self.port), OFFICE_CONFIG=str(config), PYTHONUNBUFFERED="1",
                   **{v: str(raiz / v.lower()) for v in ("TASKS_DIR", "CLAUDE_PROJECTS_DIR", "SQUADS_DIR",
                                                          "APROVACOES_DIR", "BIBLIOTECA_DIR", "DECISOES_FILA_DIR",
                                                          "QUADRO_DIR", "QUADRO_LANCAR_DIR", "PUSH_DIR")})
        env.update(extra or {})
        self.log = raiz / f"server-{self.port}.log"
        with open(self.log, "wb") as out:
            self.proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=str(raiz), env=env,
                                         stdout=out, stderr=subprocess.STDOUT)
        fim = time.time() + 15
        while time.time() < fim:
            if self.proc.poll() is not None:
                raise AssertionError(f"server.py saiu: {self.log.read_text()}")
            try:
                if self.get("/healthz")[0] == 200:
                    return
            except OSError:
                time.sleep(0.1)
        raise AssertionError("server.py não arrancou")

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return r.status, r.read(), dict(r.headers)

    def parar(self):
        self.proc.terminate()
        self.proc.wait(10)


class ApiConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.srv = None

    def tearDown(self):
        if self.srv:
            self.srv.parar()
        self.tmp.cleanup()

    def arranca(self, config: Path, extra: dict | None = None) -> Servidor:
        self.srv = Servidor(self.raiz, config, extra)
        return self.srv

    def test_exemplo_igual_a_fixture_sem_caminhos_e_no_store(self):
        # AC-a3
        s = self.arranca(ROOT / "office.example.yaml")
        code, corpo, h = s.get("/api/config")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(corpo), json.loads(FIXTURE.read_text()))
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertTrue(h["Content-Type"].startswith("application/json"))
        self.assertIn("À espera de Utilizador".encode(), corpo)   # ensure_ascii=False
        for v in valores(json.loads(corpo)):
            if isinstance(v, str):
                self.assertFalse(v.startswith(("/", "~")), v)
        for k in ("raizes", "runner", "contacto", "repo", "pasta", "settings"):
            self.assertNotIn(f'"{k}"'.encode(), corpo)
        st = json.loads(s.get("/api/state")[1])
        self.assertNotIn("probes", st)
        self.assertNotIn("crons", st)
        self.assertEqual(json.loads(s.get("/api/quadro")[1])["dev_projectos"], ["exemplo"])

    def test_config_ausente_vazia_ou_partida_arranca_com_aviso(self):
        # AC-a2: nunca rebenta; cai no exemplo e o aviso (sem caminhos) sai no /api/config e no log
        casos = {"ausente": None, "vazia": "", "partida": "squads:\n\t- id: x\n", "invalida": "squads: []\n",
                 "binario": b"\xff\xfe\x00"}
        for nome, texto in casos.items():
            with self.subTest(nome):
                f = self.raiz / f"{nome}.yaml"
                if isinstance(texto, bytes):
                    f.write_bytes(texto)
                elif texto is not None:
                    f.write_text(texto)
                s = self.arranca(f)
                self.assertEqual(s.get("/healthz")[0], 200)
                cfg = json.loads(s.get("/api/config")[1])
                self.assertIsInstance(cfg["aviso"], str)
                self.assertNotIn(str(self.raiz), cfg["aviso"])
                self.assertEqual({**cfg, "aviso": None}, json.loads(FIXTURE.read_text()))
                self.assertEqual(s.get("/api/state")[0], 200)
                s.parar()
                self.srv = None
                self.assertIn("config:", s.log.read_text())

    def test_credenciais_do_claude_nunca_expostas(self):
        # D10: com ~/office-lite/claude.env (ao lado da config) o Office não o lê nem o mostra
        casa = self.raiz / "casa"
        (casa / "office-lite").mkdir(parents=True)
        cfg = casa / "office-lite" / "office.yaml"
        cfg.write_text((ROOT / "office.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        valor = "sk-ant-api03-NUNCAnoBOARD_0123456789"
        cred = casa / "office-lite" / "claude.env"
        cred.write_text(f"ANTHROPIC_API_KEY={valor}\n", encoding="utf-8")
        cred.chmod(0o600)
        s = self.arranca(cfg, {"HOME": str(casa)})
        for rota in ("/api/config", "/api/state", "/api/quadro", "/api/biblioteca"):
            corpo = s.get(rota)[1]
            self.assertNotIn(valor.encode(), corpo, rota)
            self.assertNotIn(b"claude.env", corpo, rota)
            self.assertNotIn(b"ANTHROPIC_API_KEY", corpo, rota)
        self.assertNotIn(valor, s.log.read_text())

    def test_config_propria(self):
        f = self.raiz / "office.yaml"
        f.write_text("marca: Escritório da Rita\nutilizador:\n  nome: Rita\n  contacto: rita@exemplo.pt\n"
                     "fuso: America/Sao_Paulo\nsquads:\n  - id: ops\n    nome: Ops\n"
                     "projectos:\n  - id: loja\n    nome: Loja\n")
        cfg = json.loads(self.arranca(f).get("/api/config")[1])
        self.assertEqual((cfg["marca"], cfg["utilizador"], cfg["fuso"], cfg["aviso"]),
                         ("Escritório da Rita", {"nome": "Rita"}, "America/Sao_Paulo", None))
        self.assertEqual(cfg["colunas"][3], {"chave": "a-espera", "rotulo": "À espera de Rita"})
        self.assertEqual(cfg["lancar"], {"squads": ["ops"], "projectos": ["loja"]})
        self.assertNotIn("rita@exemplo.pt", json.dumps(cfg))


if __name__ == "__main__":
    unittest.main()
