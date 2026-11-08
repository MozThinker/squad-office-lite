"""Testes da música de fundo: funções puras em Node (por subprocess), MIME real do /music.js e tamanho."""
import re
import shutil
import subprocess
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parent.parent
MUSIC = ROOT / "static" / "music.js"
INDEX = ROOT / "static" / "index.html"


def node_major() -> int:
    out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10).stdout
    m = re.match(r"v(\d+)", out.strip())
    return int(m.group(1)) if m else 0


@unittest.skipUnless(shutil.which("node"), "node não está no PATH: testes de JS saltados")
class MusicJsTest(unittest.TestCase):
    def test_pure_functions_in_node(self):
        if node_major() < 22:
            self.skipTest("node < 22: importar .js como ESM sem package.json precisa de node >= 22")
        r = subprocess.run(["node", "--test", "tests/music_test.mjs"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class MusicServeTest(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_music_js_is_javascript(self):
        # com nosniff, o browser recusa um módulo com outro Content-Type
        port = self.httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/music.js", timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("javascript", r.headers["Content-Type"])
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")


class MusicSourceTest(unittest.TestCase):
    def test_size_and_no_network_or_per_note_timers(self):
        self.assertLessEqual(MUSIC.stat().st_size, 16 * 1024)
        src = MUSIC.read_text()
        self.assertNotRegex(src, r"https?://")
        self.assertNotIn("setTimeout", src)
        self.assertLessEqual(src.count("setInterval("), 1)


class MusicIndexTest(unittest.TestCase):
    def test_index_html_music_bytes(self):
        # critério 4: o que a música acrescenta ao index.html (bloco JS + linhas de CSS/HTML/ligação) cabe em 3 KB
        src = INDEX.read_text(encoding="utf-8")
        start = src.index("// ---- música de fundo")
        end = src.index("\n// arranque:", start)
        outside = src[:start] + src[end:]
        markers = ("#music", "#vol", 'id="music"', 'id="vol"', "jt?.update")
        lines = [l for l in outside.splitlines() if any(m in l for m in markers) or l.strip() == "musicRoom();"]
        self.assertEqual(len(lines), 7, lines)  # 3 CSS + 2 HTML + paint() + tick()
        total = len(src[start:end].encode()) + sum(len(l.encode()) + 1 for l in lines)
        self.assertLessEqual(total, 3 * 1024)

    def test_inline_pref_matches_read_prefs(self):
        # critério 21: a leitura de "music" no arranque fica no index.html (sem carregar music.js); é a mesma regra
        # do readPrefs ("music" === "1", "musicVol" 0–100 senão 30, excepção → desligada e 30), testado em tests/music_test.mjs
        src = INDEX.read_text(encoding="utf-8")
        self.assertIn('const pref = (() => { try { const v = localStorage.getItem("musicVol"); return [localStorage.getItem("music") === "1", '
                      '/^\\d{1,3}$/.test(v) && v <= 100 ? +v : 30]; } catch { return [false, 30]; } })();', src)
        music = MUSIC.read_text()
        self.assertIn('getItem("music") === "1"', music)
        self.assertIn('/^\\d{1,3}$/.test(v) && +v <= 100 ? +v : vol', music)
        self.assertIn("defaultVolume: 30,", music)


if __name__ == "__main__":
    unittest.main()
