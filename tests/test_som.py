"""Sons e vibração (fase 4 de UX, porte do Office, CA-34): testes JS por subprocess, peso, sem ficheiros de áudio e só por
import() a pedido."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SOM = STATIC / "som.js"


def node_major() -> int:
    out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10).stdout
    m = re.match(r"v(\d+)", out.strip())
    return int(m.group(1)) if m else 0


@unittest.skipUnless(shutil.which("node"), "node não está no PATH: testes de JS saltados")
class SomNodeTest(unittest.TestCase):
    def test_node(self):
        if node_major() < 22:
            self.skipTest("node < 22: importar .js como ESM sem package.json precisa de node >= 22")
        for f in ("tests/som_test.mjs", "tests/definicoes_test.mjs"):
            with self.subTest(f):
                r = subprocess.run(["node", "--test", f], cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class SomFonteTest(unittest.TestCase):
    def test_tamanho_e_sem_audio(self):
        self.assertLessEqual(SOM.stat().st_size, 2048)
        audio = [p for p in STATIC.rglob("*") if p.suffix.lower() in (".mp3", ".ogg", ".wav", ".m4a", ".aac", ".webm", ".opus")]
        self.assertEqual(audio, [])
        src = SOM.read_text(encoding="utf-8")
        self.assertNotIn("setTimeout", src)
        self.assertNotRegex(src, r"https?://")

    def test_index_carrega_com_import(self):
        src = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('import("./som.js")', src)
        self.assertNotRegex(src, r"<script[^>]*som\.js")


if __name__ == "__main__":
    unittest.main()
