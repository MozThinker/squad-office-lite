"""QA da 0.1.3: critérios sem teste próprio no PR.

- CA-C14.1: os rótulos «PR aberto em rascunho» / «Terminou sem PR» são só da UI; /api/state, /api/quadro e o código
  Python (servidor, quadro, push, runner) continuam com `parcial (PR draft)` e `incompleta`.
- CA-D15.2: também com `--proxy traefik` o compose monta só a pasta do estado, só de leitura, e nunca <dados>/runner.
- CA-A2.1: no Caddyfile os cabeçalhos estão no bloco do site, sem `defer` nem `>` (seriam aplicados só a respostas
  do upstream e o 401 do basic_auth sairia sem eles) e nunca dentro de um `handle`/`route`.
Sem rede, sem Docker/Caddy/Traefik, sem a `~` real.
"""
import json
import re
import shutil
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests.test_compose import CONFIG, ROOT, binds, gerar, servico
from tests import test_server as ts
from tests.test_server import server, write_json

ROTULOS_UI = ("PR aberto em rascunho", "Terminou sem PR")


class RotulosSoNaUI(unittest.TestCase):   # CA-C14.1 (lado da API); só o setUp/tearDown do ServerTest
    setUp, tearDown = ts.ServerTest.setUp, ts.ServerTest.tearDown

    def meta(self, tid, status, **extra):
        write_json(self.tasks / tid / "meta.json",
                   {"id": tid, "mode": "task", "status": status, "started_at": "2026-10-23T09:00:00",
                    "finished_at": "2026-10-23T10:00:00", "pr": "https://github.com/o/r/pull/7 (open, draft)",
                    **extra}, age_s=600)

    def test_api_mantem_os_valores_antigos(self):
        self.meta("20261023-0900-rascunho", "parcial (PR draft)")
        self.meta("20261023-0901-sem-pr", "incompleta", pr=None, result="saiu sem PR")
        server._cache["at"] = 0.0
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with urllib.request.urlopen(f"{base}/api/state", timeout=5) as r:
                st = r.read().decode("utf-8")
            with urllib.request.urlopen(f"{base}/api/quadro", timeout=5) as r:
                qd = r.read().decode("utf-8")
        finally:
            httpd.shutdown()
            httpd.server_close()
            server._cache["at"] = 0.0
        estados = {t["id"]: t["status"] for t in json.loads(st)["tasks"]}
        self.assertEqual(estados["20261023-0900-rascunho"], "parcial (PR draft)")
        self.assertEqual(estados["20261023-0901-sem-pr"], "incompleta")
        detalhes = {c["id"]: c["detalhe"]["estado"] for c in json.loads(qd)["cartoes"] if c["id"].startswith("dev:")}
        self.assertEqual(detalhes["dev:20261023-0900-rascunho"], "parcial (PR draft)")
        self.assertEqual(detalhes["dev:20261023-0901-sem-pr"], "incompleta")
        for texto in (st, qd):
            for r in ROTULOS_UI:
                self.assertNotIn(r, texto)

    def test_rotulos_nunca_no_codigo_python_nem_no_sw(self):
        # push (payload e sw.js) e runner ficam com os textos antigos: os rótulos vivem só em static/ajuda.js
        fontes = [p for p in ROOT.glob("*.py")] + list((ROOT / "runner").glob("*.py")) + [ROOT / "static" / "sw.js"]
        for p in fontes:
            texto = p.read_text(encoding="utf-8")
            for r in ROTULOS_UI:
                self.assertNotIn(r, texto, p.name)
        self.assertIn('"parcial (PR draft)": "PR aberto em rascunho"', (ROOT / "static" / "ajuda.js").read_text())


class EstadoNoTraefik(unittest.TestCase):   # CA-D15.2 com o outro proxy
    def test_traefik_monta_so_o_estado_so_de_leitura(self):
        t = Path(tempfile.mkdtemp())
        try:
            cfg = t / "office.yaml"
            cfg.write_text(CONFIG.format(t=t), encoding="utf-8")
            r = gerar(cfg, "--repo", str(ROOT), "--proxy", "traefik", "--rede", "web", "--dominio", "o.example.org")
            self.assertEqual(r.returncode, 0, r.stderr)
            b = {alvo: (s, ro) for s, alvo, ro in binds(r.stdout)}
            self.assertEqual(b["/data/runner-estado"], (str(t / "dados" / "runner-estado"), True))
            self.assertNotIn(str(t / "dados" / "runner"), [s for s, _ in b.values()])
            self.assertIn("      RUNNER_ESTADO_DIR: /data/runner-estado\n", servico(r.stdout, "office"))
        finally:
            shutil.rmtree(t)


class CabecalhosCaddy(unittest.TestCase):   # CA-A2.1: o 401 também os leva
    def test_sem_defer_nem_handle(self):
        c = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        site = c[c.index("{$OFFICE_SITE} {"):]
        self.assertNotRegex(site, r"\bdefer\b")
        self.assertNotRegex(site, r"(?m)^\s*header\s+(@\w+\s+)?>", "«>» = só depois do upstream (o 401 não os teria)")
        self.assertNotRegex(site, r"(?m)^\s*(handle|route)\b", "dentro de handle/route a ordem deixa de ser a do Caddy")
        # header (e o @https do HSTS) a nível do site, antes do basic_auth e do reverse_proxy
        topo = [x.split()[0] for x in site.splitlines()[1:] if re.match(r"^\t[^\s}]", x)]
        self.assertEqual(topo[:4], ["header", "@https", "header", "basic_auth"])


if __name__ == "__main__":
    unittest.main()
