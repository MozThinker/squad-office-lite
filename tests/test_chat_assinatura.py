"""Conversa (ADR-023, contrato §2.2–2.3; revisão S2/S5 e G2), lado do host: código, sais e `entrada.json`
(chat_host/codigo.py, só no estado do guardião) contra os vectores de tests/fixtures/conversa_vectores.json (os mesmos
que o guardião e o browser usam). A chave K já não vem do código (G2): é do browser, e a assinatura confere-se no
guardião (tests/test_guardiao_*.py)."""
import hashlib
import hmac
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VECTORES = json.loads((ROOT / "tests" / "fixtures" / "conversa_vectores.json").read_text(encoding="utf-8"))


def _carregar(nome, caminho):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


codigo = _carregar("chat_host_codigo", ROOT / "chat_host" / "codigo.py")


class CodigoTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.t = Path(t.name)

    def test_vectores(self):
        v = VECTORES
        self.assertEqual(codigo.derivar(v["codigo"], v["sal_office"]).hex(), v["verificador"])
        for sal in ("sal_ponte", "sal_office"):   # G2: a chave K não sai do código
            self.assertNotEqual(codigo.derivar(v["codigo"], v[sal]).hex(), v["chave"])
        k = bytes.fromhex(v["chave"])
        self.assertEqual(hmac.new(k, v["canonico"].encode(), "sha256").hexdigest(), v["assinatura"])
        self.assertEqual(hmac.new(k, v["entrar"].encode(), "sha256").hexdigest(), v["assinatura_entrar"])
        self.assertRegex(v["kid"], r"^[0-9a-f]{16}$")
        e = codigo.entrada(v["codigo"], v["sal_office"], v["sal_ponte"], "2026-11-23T12:00:00+00:00")
        self.assertEqual(e, {"v": 1, "sal_office": v["sal_office"], "sal_ponte": v["sal_ponte"], "iteracoes": 600000,
                             "verificador_sha256": v["verificador_sha256"], "criado": "2026-11-23T12:00:00+00:00"})
        self.assertEqual(hashlib.sha256(bytes.fromhex(v["verificador"])).hexdigest(), v["verificador_sha256"])

    def test_normalizar_e_mostrar(self):
        v = VECTORES
        self.assertEqual(codigo.mostrar(v["codigo"]), v["codigo_mostrado"])
        self.assertEqual(codigo.normalizar(v["codigo_mostrado"].lower().replace("-", " - ")), v["codigo"])
        # o código mostrado (com hífens, minúsculas) deriva o mesmo verificador
        self.assertEqual(codigo.derivar(v["codigo_mostrado"].lower(), v["sal_office"]).hex(), v["verificador"])

    def test_codigo_gerado(self):
        cs = {codigo.gerar_codigo() for _ in range(50)}
        self.assertEqual(len(cs), 50)
        for c in cs:
            self.assertRegex(c, r"^[A-Z2-7]{24}$")

    def test_gerar_escreve_so_a_entrada_sem_o_codigo(self):   # CA-10, G2
        est = self.t / "estado"
        est.mkdir(mode=0o700)
        (est / "chave").write_text("ab" * 32 + "\n")   # a chave antiga (PBKDF2 do código) sai
        (est / "chaves.json").write_text("{}")   # as dos browsers registados ficam (só o --revogar as apaga)
        c = codigo.gerar(est, iteracoes=1000)
        e = json.loads((est / "entrada.json").read_text())
        self.assertEqual(set(e), {"v", "sal_office", "sal_ponte", "iteracoes", "verificador_sha256", "criado"})
        self.assertRegex(e["sal_office"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(e["sal_office"], e["sal_ponte"])
        self.assertEqual(e["verificador_sha256"],
                         hashlib.sha256(codigo.derivar(c, e["sal_office"], 1000)).hexdigest())
        self.assertEqual(os.stat(est / "entrada.json").st_mode & 0o777, 0o600)   # só o guardião
        self.assertEqual(os.stat(est).st_mode & 0o777, 0o700)
        bruto = (est / "entrada.json").read_bytes()
        for segredo in (c, codigo.mostrar(c)):
            self.assertNotIn(segredo.encode(), bruto)
        self.assertEqual(sorted(p.name for p in est.iterdir()), ["chaves.json", "entrada.json"])   # sem temporários
        self.assertEqual(sorted(p.name for p in self.t.iterdir()), ["estado"])   # nada fora do estado
        # rodar o código: sais e verificador novos
        c2 = codigo.gerar(est, iteracoes=1000)
        self.assertNotEqual(c, c2)
        e2 = json.loads((est / "entrada.json").read_text())
        self.assertNotEqual((e2["sal_office"], e2["verificador_sha256"]), (e["sal_office"], e["verificador_sha256"]))

    def test_cli_imprime_o_codigo_uma_vez(self):
        est = self.t / "estado"
        est.mkdir(mode=0o700)
        r = subprocess.run([sys.executable, "-I", str(ROOT / "chat_host" / "codigo.py"), "--estado", str(est)],
                           capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^([A-Z2-7]{4}-){5}[A-Z2-7]{4}\n$")
        e = json.loads((est / "entrada.json").read_text())
        self.assertEqual(e["iteracoes"], 600000)
        c = r.stdout.strip()
        self.assertEqual(e["verificador_sha256"], hashlib.sha256(codigo.derivar(c, e["sal_office"])).hexdigest())
        self.assertEqual(sorted(p.name for p in est.iterdir()), ["entrada.json"])
        r = subprocess.run([sys.executable, "-I", str(ROOT / "chat_host" / "codigo.py"), "--estado",
                            str(self.t / "nao-ha")], capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertFalse(re.search(r"[A-Z2-7]{24}", r.stderr))


    def test_cli_sem_o_conversas(self):   # o --conversas de antes já não existe (o entrada.json é do guardião)
        r = subprocess.run([sys.executable, "-I", str(ROOT / "chat_host" / "codigo.py"), "--conversas",
                            str(self.t), "--estado", str(self.t)], capture_output=True, text=True, check=False,
                           timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
