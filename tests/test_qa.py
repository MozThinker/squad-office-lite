"""QA v2: casos em falta nos testes da implementação (AC-3, 4, 8, 24, 35, 37..41) e fumo da UI.

Tokens falsos construídos em runtime (concatenação) para não disparar o gitleaks.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import reader
import server
from tests import fake
from tests import test_server as base
from tests.test_live import LiveBase
from tests import cfg

OFFICE_CFG = cfg.LEGADO

write, write_json = base.write, base.write_json
ROOT = Path(server.__file__).parent
INDEX = ROOT / "static" / "index.html"
WT = "/srv/projectos/.worktrees/"
S = reader.SECRET
MARK = "SEGREDO-" + "XYZ"


class DevDirSafeTest(LiveBase):
    def test_derived_name_failing_safe_is_none(self):
        # AC-8 (3.º caso): nome derivado que falhe _safe → sem pasta (sem padrão fixo)
        real = server._safe
        with mock.patch.object(server, "_safe", side_effect=lambda s: False if "worktrees-T1" in str(s) else real(s)):
            self.assertIsNone(server._dev_dir({"worktree": WT + "T1"}, "T1"))


class StateNoContentTest(LiveBase):
    def test_marker_everywhere_never_in_state(self):
        # AC-3: marcador em pedido, texto, thinking, input de várias ferramentas, resultados (ok e erro),
        # subagente e pr-link com lixo à volta → nunca em json.dumps(build_state())
        lines = (fake.prompt(MARK) + fake.text(MARK)
                 + fake.assistant([{"type": "thinking", "thinking": MARK, "signature": "x"}])
                 + fake.tool_use("a", "Agent", {"description": MARK, "prompt": MARK})
                 + fake.tool_use("b", "mcp__x__y", {"q": MARK}) + fake.tool_result("b", True, MARK)
                 + fake.tool_use("c", "Bash", {"command": f"cd /{MARK} && gh pr create --title {MARK}"})
                 + fake.tool_result("c", False, f"{MARK}\nhttps://github.com/o/r/pull/7\n"))
        path = self.task(lines=lines)
        self.sub("T1", "a1", fake.tool_use("s", "Read", {"file_path": f"/{MARK}/x"}) + fake.tool_result("s", False, MARK))
        st1 = json.dumps(server.build_state(), ensure_ascii=False)
        self.append(path, fake.tool_use("d", "Bash", {"command": f"git push {MARK}"}) + fake.tool_result("d", False, MARK)
                    + fake.tool_use("e", "WebFetch", {"url": f"https://{MARK}.io"}))
        st2 = server.build_state()
        self.assertTrue(st2["feed"])
        for s in (st1, json.dumps(st2, ensure_ascii=False)):
            self.assertNotIn(MARK, s)
        self.assertEqual(self.get()["gh"]["pr_url"], "https://github.com/o/r/pull/7")


class IncrementalSeekTest(LiveBase):
    def spy(self, path):
        seeks, orig = [], Path.open

        def opener(p, *a, **kw):
            f = orig(p, *a, **kw)
            if str(p) == str(path):
                real = f.seek
                f.seek = lambda off, *w: (seeks.append(off), real(off, *w))[1]
            return f
        return seeks, mock.patch.object(Path, "open", opener)

    def test_second_read_starts_at_saved_offset(self):
        # AC-4: acrescento → lê a partir do offset guardado (não do 0); só a parte nova
        path = self.task(lines=fake.tool_use("u1", "Read") * 5)
        self.get()
        old = path.stat().st_size
        self.assertEqual(server._live[str(path)].off, old)
        self.append(path, fake.tool_use("u2", "Edit"))
        seeks, patch = self.spy(path)
        with patch:
            self.assertEqual(self.get()["lead"]["act"]["tool"], "Edit")
        self.assertEqual(seeks, [old])
        self.assertEqual(server._live[str(path)].off, path.stat().st_size)

    def test_rotation_restarts_from_zero(self):
        path = self.task(lines=fake.tool_use("u1", "Read") * 5)
        self.get()
        tmp = path.with_name("x.tmp")
        tmp.write_text(fake.tool_use("u2", "Edit"))
        os.replace(tmp, path)
        seeks, patch = self.spy(path)
        with patch:
            self.assertEqual(self.get()["lead"]["act"]["tool"], "Edit")
        self.assertEqual(seeks, [0])

    def test_first_read_seeks_to_last_256kb(self):
        pad = fake.line({"type": "attachment", "x": "a" * 1000}) * 400
        path = self.task(lines=pad + fake.tool_use("u1", "Grep"))
        size = path.stat().st_size
        seeks, patch = self.spy(path)
        with patch:
            self.get()
        self.assertEqual(seeks, [size - server.TAIL_FIRST])


class GitHubExtraTest(LiveBase):
    def bash(self, i, cmd, err=False, out="ok"):
        return fake.tool_use(f"g{i}", "Bash", {"command": cmd}) + fake.tool_result(f"g{i}", err, out)

    def test_push_error_no_event_and_chained_ci(self):
        # AC-24: push com erro → sem evento; push ok → evento; CI encadeado reconhecido
        path = self.task(lines=fake.tool_use("u0", "Read"))
        self.get()
        self.append(path, self.bash(1, "git push origin feat/x", err=True))
        feed = server.build_state()["feed"]
        self.assertNotIn("push", [e["kind"] for e in feed])   # só as mudanças de actividade/erro
        self.assertIsNone(self.get()["gh"]["at"])
        self.append(path, self.bash(2, "cd apps/api && git push -u origin feat/x")
                    + self.bash(3, "cd x && gh pr checks 5 --watch", err=True, out="ci\tfail\t1m\thttps://x"))
        kinds = [e["kind"] for e in server.build_state()["feed"]]
        self.assertIn("push", kinds)
        self.assertIn("ci_vermelho", kinds)
        self.assertEqual(self.get()["gh"]["ci"], "vermelho")


class QaHttpBase(unittest.TestCase):
    def setUp(self):
        base.ServerTest.setUp(self)
        reader._cache.clear()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)   # porta livre
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        reader._cache.clear()
        base.ServerTest.tearDown(self)

    def get(self, path):
        url = f"http://127.0.0.1:{self.httpd.server_address[1]}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def dev(self, tid="T1", lines=None, **extra):
        write_json(self.tasks / tid / "meta.json", {"id": tid, "status": "concluída", "worktree": WT + tid, **extra})
        if lines is not None:
            write(server._dev_dir({"worktree": WT + tid}, tid) / "S0.jsonl", lines)


class NotFoundEachCaseTest(QaHttpBase):
    NF = {"error": "não encontrado"}

    def assert404(self, path):
        code, body = self.get(path)
        self.assertEqual(code, 404, path)
        self.assertEqual(json.loads(body), self.NF)
        self.assertEqual(body, json.dumps(self.NF, ensure_ascii=False).encode())   # sem detalhe

    def test_inexistent_id(self):
        self.dev("T1", fake.prompt("x"))
        self.assert404("/api/transcript/T2")
        self.assertEqual(self.get("/api/transcript/T1")[0], 200)

    def test_traversal_ids(self):
        # AC-35: .., /, %2e%2e%2f, %2F (e variantes em maiúsculas / duplamente codificadas)
        self.dev("T1", fake.prompt("x"))
        write_json(self.tasks / ".." / "meta.json", {"id": "x", "status": "concluída"})
        for p in ("..", "%2e%2e", "%2E%2E", "%2e%2e%2f", "%2E%2E%2F", "%2F", "%2f", "T1%2F", "T1/", "a/b",
                  "..%2FT1", "%2e%2e%2fT1", "%252e%252e%252f", "T1%00", ".%2e"):
            with self.subTest(p=p):
                self.assert404("/api/transcript/" + p)

    def test_meta_without_transcript(self):
        self.dev("T1", None)
        self.assert404("/api/transcript/T1")
        write_json(self.mkt / "M1" / "meta.json", {"id": "M1", "status": "concluída", "session_id": "S9",
                                                   "claude_dir": "-srv-projectos-marketing"})
        self.assert404("/api/transcript/M1")

    def test_interactive_session_without_meta(self):
        # .jsonl em CLAUDE/-srv-privado/ nunca sai: nem pelo nome da sessão, nem com meta dev do mesmo id
        priv = self.claude / "-srv-privado"
        write(priv / "H1.jsonl", fake.prompt(MARK))
        self.assert404("/api/transcript/H1")
        self.assert404("/api/transcript/-srv-privado")
        write_json(self.tasks / "H1" / "meta.json", {"id": "H1", "status": "concluída"})   # sem pasta de worktree
        self.assert404("/api/transcript/H1")
        write_json(self.mkt / "H2" / "meta.json", {"id": "H2", "status": "concluída", "session_id": "H1",
                                                   "claude_dir": "-srv-privado"})
        self.assert404("/api/transcript/H2")

    def test_unknown_squad(self):
        self.dev("T1", fake.prompt("x"))
        # squad= vazio é tratado como ausente (parse_qs ignora valores vazios): não entra aqui
        for q in ("outra", "DEV", "dev/..", "..%2Fdev"):
            with self.subTest(q=q):
                self.assert404("/api/transcript/T1?squad=" + q)
        self.assertEqual(self.get("/api/transcript/T1?squad=dev")[0], 200)

    def test_no_path_or_session_param(self):
        # AC-34: parâmetros de caminho/sessão são ignorados (resolve só pela meta)
        self.dev("T1", fake.prompt("certo"))
        write(self.claude / "-srv-privado" / "H1.jsonl", fake.prompt(MARK))
        code, body = self.get("/api/transcript/T1?path=../-srv-privado/H1.jsonl&session=H1&file=H1"
                              "&claude_dir=-srv-privado&session_id=H1")
        self.assertEqual(code, 200)
        self.assertNotIn(MARK.encode(), body)
        self.assertEqual([e["text"] for e in json.loads(body)["entries"]], ["certo"])


class ReaderHttpContentTest(QaHttpBase):
    def test_env_hidden_thinking_absent_and_masked(self):
        # AC-37/39/40 por HTTP real
        tok = fake.gh_token()
        aws = "AK" + "IA" + "QWERTYUIOPASDFGH"
        lines = (fake.prompt(f"usa {tok}")
                 + fake.assistant([{"type": "thinking", "thinking": MARK, "signature": "x"}])
                 + fake.tool_use("1", "Read", {"file_path": "/w/apps/api/.env.production"})
                 + fake.tool_result("1", False, f"OPENAI_API_KEY={MARK}\nAWS={aws}")
                 + fake.tool_use("2", "Bash", {"command": f"grep KEY apps/api/.env.production # {tok}"})
                 + fake.tool_result("2", False, f"KEY={MARK}")
                 + fake.tool_use("3", "Grep", {"pattern": "X", "path": "apps/.env"}) + fake.tool_result("3", False, MARK)
                 + fake.tool_use("4", "Bash", {"command": "cat README.md"}) + fake.tool_result("4", False, f"id {aws} ok"))
        self.dev("T1", lines)
        code, body = self.get("/api/transcript/T1")
        self.assertEqual(code, 200)
        self.assertNotIn(MARK.encode(), body)
        self.assertNotIn(tok.encode(), body)
        self.assertNotIn(aws.encode(), body)
        e = json.loads(body)["entries"]
        self.assertEqual([(x["kind"], x["text"]) for x in e], [
            ("pedido", f"usa {S}"),
            ("ferramenta", "/w/apps/api/.env.production"), ("resultado", reader.HIDDEN),
            ("ferramenta", f"grep KEY apps/api/.env.production # {S}"), ("resultado", reader.HIDDEN),
            ("ferramenta", "X apps/.env"), ("resultado", reader.HIDDEN),
            ("ferramenta", "cat README.md"), ("resultado", f"id {S} ok")])

    def test_delivery_from_end_of_claude_log_only(self):
        # AC-38: só os últimos 64 KB do claude.log; truncado a 20 000 e mascarado
        self.dev("T1", fake.prompt("x"))
        log = self.tasks / "T1" / "claude.log"
        pad = ("x" * 1000 + "\n") * 70
        write(log, json.dumps({"type": "result", "result": "antigo"}) + "\n" + pad)
        self.assertIsNone(json.loads(self.get("/api/transcript/T1")[1])["delivery"])
        big = "y" * 25000 + " " + fake.gh_token()
        write(log, pad + json.dumps({"type": "result", "result": "Bearer " + "abcdefgh12345 " + big}) + "\n")
        d = json.loads(self.get("/api/transcript/T1")[1])["delivery"]
        self.assertEqual(len(d), 20001)
        self.assertTrue(d.startswith(f"Bearer {S} yyy") and d.endswith("…"))


class MaskExtraTest(unittest.TestCase):
    def test_short_or_partial_forms_unchanged(self):
        # AC-39: falsos positivos (formas curtas/parecidas não mudam)
        for s in ("scikit sk-learn", "gh" + "p_short", "postgres://host:5432/db", "AKIA" + "abcdefghijklmnop",
                  "xo" + "xb-short", "the-secret-garden", "KEY_POINTS do relatório", "primary key (id)",
                  "github" + "_pat_curto", "eyJabc.eyJdef.x"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)

    def test_rest_of_line_kept(self):
        tok = "sk" + "-" + "Q" * 20
        self.assertEqual(reader.mask(f"antes {tok} depois e postgres://u:" + "pw123456" + "@h/x fim"),
                         f"antes {S} depois e postgres://u:{S}@h/x fim")


@unittest.skipUnless(shutil.which("node"), "node não está no PATH")
class UiSmokeTest(unittest.TestCase):
    def setUp(self):
        self.html = INDEX.read_text()

    def test_inline_script_parses(self):
        scripts = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        self.assertTrue(scripts)
        with tempfile.TemporaryDirectory() as d:
            for i, js in enumerate(scripts):
                p = Path(d) / f"s{i}.js"
                p.write_text(js)
                r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_external_resources(self):
        # AC-1: nenhum src/href http(s) externo em script/link/img/iframe nem import/fetch absoluto
        self.assertIsNone(re.search(r"\b(?:src|href)\s*=\s*[\"']?(?:https?:)?//", self.html, re.I))
        self.assertIsNone(re.search(r"\bimport\s*\(\s*[\"']https?:", self.html))
        self.assertIsNone(re.search(r"fetch\(\s*[`\"']https?:", self.html))

    def test_transcript_not_polled(self):
        # AC-41: /api/transcript só em fetchT (chamada a partir de clique), nunca em tick()
        tick = re.search(r"async function tick\(\) \{(.*?)\n\}", self.html, re.S).group(1)
        self.assertNotIn("transcript", tick)
        self.assertNotIn("fetchT", tick)
        self.assertEqual(len(re.findall(r"api/transcript", self.html)), 1)

    def test_markdown_escapes_and_renders(self):
        # AC-41: md() sem lib; HTML do result aparece literal
        start = self.html.index("function inline(")
        end = self.html.index("\nconst rd = ")
        esc = re.search(r"^const esc = .*$", self.html, re.M).group(0)
        cases = {
            "xss": '<img src=x onerror=alert(1)> **<b>n</b>** `<i>c</i>` [a](javascript:alert(1))',
            "md": "# T1\n## T2\n### T3\n- a\n- b\n1. um\n2. dois\n```\n<script>x</script>\n```\nver https://github.com/o/r/pull/1.",
        }
        js = (esc + "\n" + self.html[start:end] + "\nconsole.log(JSON.stringify(Object.fromEntries("
              + "Object.entries(" + json.dumps(cases) + ").map(([k, v]) => [k, md(v)]))));")
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        x = out["xss"]
        self.assertNotIn("<img", x)
        self.assertNotIn("<b>", x)
        self.assertNotIn("<i>", x)
        self.assertNotIn('href="javascript', x)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", x)
        self.assertIn("<strong>&lt;b&gt;n&lt;/b&gt;</strong>", x)
        self.assertIn("<code>&lt;i&gt;c&lt;/i&gt;</code>", x)
        m = out["md"]
        for tag in ("<h3>T1</h3>", "<h4>T2</h4>", "<h5>T3</h5>", "<ul><li>a</li><li>b</li></ul>",
                    "<ol><li>um</li><li>dois</li></ol>", "<pre><code>&lt;script&gt;x&lt;/script&gt;</code></pre>",
                    '<a href="https://github.com/o/r/pull/1" target="_blank" rel="noopener noreferrer">'):
            self.assertIn(tag, m)
        self.assertNotIn("<script>", m)

    def test_title_and_default_filter(self):
        # AC-22/32 (estático): título "(N) " + texto actual; filtro por omissão 7d guardado em localStorage
        self.assertIn('document.title = (n > 0 ? `(${n}) ` : "") + cfg.marca', self.html)
        self.assertIn("<title>Squad Office</title>", self.html)
        self.assertRegex(self.html, r'localStorage\.getItem\("periodo"\).*: "7d"')


if __name__ == "__main__":
    unittest.main()
