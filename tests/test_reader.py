"""v2: leitor a pedido (reader.py + /api/transcript) e regras gerais (AC-1, 2, 33..40).

Tokens falsos construídos em runtime (concatenação) para não disparar o gitleaks.
"""
import ast
import json
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import biblioteca
import reader
import server
from tests import fake
from tests import test_server as base
from tests import cfg

OFFICE_CFG = cfg.LEGADO

write, write_json = base.write, base.write_json
ROOT = Path(server.__file__).parent
WT = "/srv/projectos/.worktrees/"
S = reader.SECRET


class MaskTest(unittest.TestCase):
    def test_each_rule(self):
        # AC-39
        jwt = "ey" + "JhbGciOiJIUzI1NiJ9" + "." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "SflKxwRJSMeKKF2QT4fw"
        cases = [
            ("Authorization: Bearer " + "abc.DEF-123_xyz456", "Authorization: Bearer " + S),
            ("chave " + "sk" + "-ant-api03-" + "x" * 40 + " fim", f"chave {S} fim"),
            ("sk" + "-" + "a1B2" * 4, S),
            ("token " + fake.gh_token(), f"token {S}"),
            *[(p + "_" + "Z9" * 10, S) for p in ("gho", "ghs", "ghu", "ghr")],
            ("github" + "_pat_" + "A" * 22, S),
            ("id AK" + "IA" + "ABCDEFGHIJKLMNOP" + " ok", f"id {S} ok"),
            (f"x {jwt} y", f"x {S} y"),
            *[(p + "-" + "1234567890ab", S) for p in ("xo" + "xb", "xo" + "xp", "xo" + "xa", "xo" + "xs", "xo" + "xr")],
            ("postgres://user:" + "pa55w0rd" + "@db:5432/x", f"postgres://user:{S}@db:5432/x"),
            ("OPENAI_API_KEY=valor", f"OPENAI_API_KEY={S}"),
            ('export GH_TOKEN="valor"', f'export GH_TOKEN="{S}"'),
            ("DB_PASSWORD: valor resto", f"DB_PASSWORD: {S} resto"),
            ('{"client_secret": "valor", "a": 1}', f'{{"client_secret": "{S}", "a": 1}}'),
            ("https://x.io/?api_key=valor&b=2", f"https://x.io/?api_key={S}"),   # até ao espaço: &b=2 vai junto
            ("db_passwd='valor'", f"db_passwd='{S}'"),
            ("GH_TOKEN=" + fake.gh_token(), f"GH_TOKEN={S}"),
        ]
        for raw, want in cases:
            with self.subTest(want=want):
                self.assertEqual(reader.mask(raw), want)

    def test_normal_text_unchanged(self):
        for s in ("a chave do problema", "KEYBOARD", "pytest -k test_key", "https://github.com/o/r/pull/1",
                  "task-abcdefghijklmnopqrstuvwxyz", "Bearer curto", "eyJ curto", "AKIA123", "o token expirou",
                  "http://host:8080/x@y", "secret\n= x"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)

    def test_apikey_senha_tabela_e_aws(self):
        # revisão de segurança: apikey/senha como nome, tabelas markdown e a secret key AWS
        hx = "0123" + "abcd" * 8
        aws = "wJalr" + "XUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"   # 40 car.
        akia = "AK" + "IA" + "IOSFODNN7EXAMPLE"
        cases = [
            ("apikey: " + hx, f"apikey: {S}"),
            ('curl -H "apikey: ' + hx + '" https://x.supabase.co', f'curl -H "apikey: {S}" https://x.supabase.co'),
            ('{"apikey": "' + hx + '"}', f'{{"apikey": "{S}"}}'),
            ('{"x-api-key": "' + hx + '"}', f'{{"x-api-key": "{S}"}}'),
            ("api-key=" + hx, f"api-key={S}"),
            ("EVOLUTION_APIKEY: " + hx, f"EVOLUTION_APIKEY: {S}"),
            ("Evolution_ApiKey=" + hx, f"Evolution_ApiKey={S}"),
            ("senha: abc123 resto", f"senha: {S} resto"),
            ("ADMIN_SENHA=abc123", f"ADMIN_SENHA={S}"),
            ("| SUPABASE_KEY | " + hx + " |", f"| SUPABASE_KEY | {S} |"),
            ("|`EVOLUTION_APIKEY`|" + hx + "|nota|", f"|`EVOLUTION_APIKEY`| {S} | {S} |"),
            ("  | **senha** | abc |", f"  | **senha** | {S} |"),
            ("aws_secret_access_key = " + aws, f"aws_secret_access_key = {S}"),
            ("aws_secret_access_key " + aws, f"aws_secret_access_key {S}"),
            (akia + "," + aws, f"{S},{S}"),
            (akia + "  " + aws + " fim", f"{S}  {S} fim"),
            (akia + " ok", f"{S} ok"),
        ]
        for raw, want in cases:
            with self.subTest(want=want):
                self.assertEqual(reader.mask(raw), want)

    def test_novas_regras_sem_falsos_positivos(self):
        for s in ("| Variável | Valor |", "|---|---|", "| skill | desk |", "| KEYBOARD | 1 |", "| max_tokens | 4000 |",
                  "| chave da API | x |", "texto | key | x", "senhas fortes", "apikeys", "a senha expirou",
                  "AK" + "IA" + "IOSFODNN7EXAMPLE".lower(), "skill: 3", "desk=2", "KEYBOARD=1", "max_tokens: 10"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)

    def test_tabela_so_com_valor_com_aspecto_de_segredo(self):
        # review ronda 2: tabelas reais escondidas pela máscara (design tokens, colunas, erros, ficheiros SQL)
        uuid = "3f2b9c1e-8d4a-4b7f-9e21-0c5d6a7b8e9f"
        for s in ("| Token | Valor | Uso |", "| Token | `#158A4F` | cor principal |",
                  "| `session_id` | uuid | sim |", "| session_id | " + uuid + " |", "| auth_status | ok |",
                  "| AuthExpired | O token expirou, volta a entrar |", "| 07_session_function_test.sql | passa |",
                  "| 08_session_key_test.sql | " + "a" * 30 + " |", "| api_key | ver o cofre |", "| token | |"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)
        hx = "0123" + "abcd" * 8
        for raw, want in (("| Token | " + hx + " | nota |", f"| Token | {S} | {S} |"),
                          ("| **session_key** | `" + hx + "` |", f"| **session_key** | {S} |"),
                          ("| pwd | x1 |", f"| pwd | {S} |"),
                          ("| GH_TOKEN | " + fake.gh_token() + " |", f"| GH_TOKEN | {S} |")):
            with self.subTest(want=want):
                self.assertEqual(reader.mask(raw), want)

    def test_clip_masks_before_truncating(self):
        tok = fake.gh_token()
        out = reader.clip("a" * 995 + " " + tok, 1000)
        self.assertEqual(out, "a" * 995 + " " + S[:4] + "…")
        self.assertNotIn(tok[:12], out)
        self.assertEqual(reader.clip("curto", 10), "curto")

    def test_env_file(self):
        # AC-40
        yes = [("Read", {"file_path": "/w/apps/api/.env"}), ("Edit", {"file_path": ".env.local"}),
               ("Write", {"file_path": "/x/.env.production"}), ("Bash", {"command": "cat .env"}),
               ("Bash", {"command": "grep X apps/api/.env.production"}), ("Grep", {"pattern": "X", "path": "a/.env"}),
               ("Glob", {"pattern": "**/.env*"}), ("Bash", {"command": "source ./.env && run"})]
        no = [("Read", {"file_path": "/w/env.py"}), ("Read", {"file_path": "/w/.envy/x.py"}),
              ("Bash", {"command": "echo $ENV"}), ("Bash", {"command": "python3 -m venv .venv"}),
              ("WebFetch", {"url": "https://x/.env"}), ("Read", "x"), ("Read", {"file_path": 5})]
        for name, inp in yes:
            with self.subTest(yes=inp):
                self.assertTrue(reader.env_file(name, inp))
        for name, inp in no:
            with self.subTest(no=inp):
                self.assertFalse(reader.env_file(name, inp))


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        reader._cache.clear()

    def tearDown(self):
        reader._cache.clear()
        __import__("shutil").rmtree(self.tmp)

    def parse(self, lines, name="S.jsonl"):
        p = self.tmp / name
        p.write_text(lines)
        return reader.parse([p])

    def test_entries_and_summaries(self):
        # AC-37
        lines = (fake.prompt("faz X", 50) + fake.thinking(49) + fake.text("vou ler", 48)
                 + fake.tool_use("1", "Bash", {"command": "ls", "description": "d"}, 47) + fake.tool_result("1", False, "ok", 46)
                 + fake.tool_use("2", "Read", {"file_path": "/a/b.py", "limit": 5}, 45)
                 + fake.tool_use("3", "Grep", {"pattern": "def x", "path": "src"}, 44)
                 + fake.tool_use("4", "WebSearch", {"query": "q"}, 43) + fake.tool_use("5", "WebFetch", {"url": "https://u"}, 42)
                 + fake.tool_use("6", "Agent", {"description": "plano", "prompt": "SEGREDO-P"}, 41)
                 + fake.tool_use("7", "Edit", {"file_path": "/a/c.py", "old_string": "SEGREDO-O"}, 40)
                 + fake.tool_use("8", "mcp__x", {"a": "SEGREDO-M"}, 39)
                 + fake.tool_result("9", True, [{"type": "text", "text": "falhou"}, {"type": "image"}], 38)
                 + fake.junk())
        entries, truncated = self.parse(lines)
        self.assertFalse(truncated)
        self.assertEqual([(e["kind"], e.get("tool"), e["text"]) for e in entries], [
            ("pedido", None, "faz X"), ("resposta", None, "vou ler"), ("ferramenta", "Bash", "ls"),
            ("resultado", "Bash", "ok"), ("ferramenta", "Read", "/a/b.py"), ("ferramenta", "Grep", "def x src"),
            ("ferramenta", "WebSearch", "q"), ("ferramenta", "WebFetch", "https://u"), ("ferramenta", "Agent", "plano"),
            ("ferramenta", "Edit", "/a/c.py"), ("ferramenta", "mcp__x", ""), ("resultado", None, "falhou"),
            ("resposta", None, "tool_use")])   # do junk(): conteúdo em texto do assistente é uma resposta
        self.assertEqual(entries[0]["at"], base.iso(50))
        self.assertEqual((entries[3]["error"], entries[11]["error"]), (False, True))
        self.assertNotIn("SEGREDO", json.dumps(entries))

    def test_truncation(self):
        lines = (fake.prompt("p" * 5000) + fake.text("r" * 4000)
                 + fake.tool_use("1", "Bash", {"command": "c" * 400}) + fake.tool_result("1", False, "o" * 1500))
        e, _ = self.parse(lines)
        self.assertEqual([len(x["text"]) for x in e], [4001, 4000, 301, 1001])
        self.assertTrue(e[0]["text"].endswith("p…") and e[2]["text"].endswith("c…") and e[3]["text"].endswith("o…"))

    def test_mask_and_env(self):
        # AC-39/40: máscara em texto, resumo e resultado; .env oculta o resultado inteiro
        tok = fake.gh_token()
        lines = (fake.prompt(f"usa {tok}") + fake.tool_use("1", "Bash", {"command": f"GH_TOKEN={tok} gh pr list"})
                 + fake.tool_result("1", False, f"Bearer {tok}")
                 + fake.tool_use("2", "Bash", {"command": "cat apps/api/.env"}) + fake.tool_result("2", False, "A=1")
                 + fake.tool_use("3", "Read", {"file_path": "/w/.env.local"}) + fake.tool_result("3", True, "B=2"))
        e, _ = self.parse(lines)
        self.assertEqual([x["text"] for x in e], [
            f"usa {S}", f"GH_TOKEN={S} gh pr list", f"Bearer {S}", "cat apps/api/.env", reader.HIDDEN,
            "/w/.env.local", reader.HIDDEN])
        self.assertEqual(e[-1]["error"], True)
        self.assertNotIn(tok, json.dumps(e))

    def test_big_file_truncated(self):
        with mock.patch.object(reader, "MAX_BYTES", 300):
            e, truncated = self.parse(fake.prompt("velho" * 50) + fake.prompt("a") + fake.prompt("b"))
        self.assertTrue(truncated)
        self.assertEqual([x["text"] for x in e], ["a", "b"])

    def test_cache_lru(self):
        p = self.tmp / "S.jsonl"
        p.write_text(fake.prompt("a"))
        with mock.patch.object(reader, "_parse_file", wraps=reader._parse_file) as pf:
            reader.parse([p])
            reader.parse([p])
            self.assertEqual(pf.call_count, 1)
            p.write_text(fake.prompt("bb"))
            self.assertEqual(reader.parse([p])[0][0]["text"], "bb")
            self.assertEqual(pf.call_count, 2)
        for i in range(6):
            q = self.tmp / f"{i}.jsonl"
            q.write_text(fake.prompt(str(i)))
            reader.parse([q])
        self.assertEqual(len(reader._cache), reader.CACHE_N)

    def test_multi_session_sorted(self):
        a, b = self.tmp / "a.jsonl", self.tmp / "b.jsonl"
        a.write_text(fake.prompt("1", 30) + fake.prompt("3", 10))
        b.write_text(fake.prompt("2", 20))
        self.assertEqual([x["text"] for x in reader.parse([a, b])[0]], ["1", "2", "3"])

    def test_page_and_filters(self):
        # AC-36
        entries = [{"at": None, "kind": ("pedido", "resposta", "ferramenta", "resultado")[i % 4], "text": f"Linha {i}"}
                   for i in range(250)]
        p = reader.page(entries, "tudo", "", 0)
        self.assertEqual((p["pages"], p["total"], len(p["entries"])), (3, 250, 100))
        self.assertEqual(len(reader.page(entries, "tudo", "", 2)["entries"]), 50)
        for n in (9, -1, 3):
            self.assertEqual(reader.page(entries, "tudo", "", n)["entries"], [])
        m = reader.page(entries, "mensagens", "", 0)
        self.assertEqual((m["total"], {e["kind"] for e in m["entries"]}), (126, {"pedido", "resposta"}))
        self.assertEqual(reader.page(entries, "sem-ferramentas", "", 0)["total"], 126)
        q = reader.page(entries, "tudo", "LINHA 24", 0)
        self.assertEqual([e["text"] for e in q["entries"]], ["Linha 24"] + [f"Linha {i}" for i in range(240, 250)])
        self.assertEqual(reader.page(entries, "tudo", "x" * 101, 0)["total"], 0)
        self.assertEqual(reader.page([], "tudo", "", 0), {"entries": [], "page": 0, "pages": 1, "total": 0})

    def test_search_on_masked_text(self):
        tok = fake.gh_token()
        e, _ = self.parse(fake.prompt(f"usa {tok}"))
        self.assertEqual(reader.page(e, "tudo", tok[:12], 0)["total"], 0)
        self.assertEqual(reader.page(e, "tudo", "[segredo]", 0)["total"], 1)

    def test_delivery(self):
        self.assertIsNone(reader.delivery(None))
        self.assertIsNone(reader.delivery("  "))
        self.assertEqual(len(reader.delivery("x" * 30000)), 20001)
        self.assertEqual(reader.delivery("PR <b>ok</b> " + fake.gh_token()), f"PR <b>ok</b> {S}")


class TranscriptHttpTest(unittest.TestCase):
    def setUp(self):
        base.ServerTest.setUp(self)
        reader._cache.clear()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
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
                return r.status, r.headers["Content-Type"], json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.headers["Content-Type"], json.loads(e.read())

    def dev(self, tid="T1", lines=None, **extra):
        write_json(self.tasks / tid / "meta.json", {"id": tid, "status": "concluída", "worktree": WT + tid, **extra})
        pdir = server._dev_dir({"worktree": WT + tid}, tid)
        if lines is not None:
            write(pdir / "S0.jsonl", lines)
        return pdir

    def mkt_meta(self, tid="T1", cdir="-srv-projectos-marketing", sid=fake.SID, lines=None, **extra):
        write_json(self.mkt_root / tid / "meta.json", {"id": tid, "status": "concluída", "session_id": sid,
                                                      "claude_dir": cdir, **extra})
        if lines is not None:
            write(self.claude / cdir / f"{sid}.jsonl", lines)

    def test_not_found_cases(self):
        # AC-35: todos 404 com o mesmo corpo
        self.mkt_root = self.mkt
        self.dev("T1", fake.prompt("x"))
        self.dev("SEM", None)                                     # meta sem transcript
        write(self.claude / "-srv-privado" / "H1.jsonl", fake.prompt("privado"))
        self.mkt_meta("H2", cdir="-srv-privado", sid="H1")   # nem com meta a apontar
        self.mkt_meta("M3", cdir="..", sid="S1")
        for path in ("/api/transcript/nao-existe", "/api/transcript/..", "/api/transcript/T1%2F..",
                     "/api/transcript/%2e%2e%2f", "/api/transcript/%2F", "/api/transcript/a/b",
                     "/api/transcript/%252e%252e", "/api/transcript/", "/api/transcript/SEM", "/api/transcript/H1",
                     "/api/transcript/H2", "/api/transcript/M3", "/api/transcript/T1?squad=outra",
                     "/api/transcript/T1?squad=marketing", "/api/transcript/..%2Ftasks%2FT1"):
            with self.subTest(path=path):
                code, ctype, body = self.get(path)
                self.assertEqual((code, body), (404, {"error": "não encontrado"}))
                self.assertTrue(ctype.startswith("application/json"))
        self.assertEqual(self.get("/api/transcript/T1")[0], 200)

    def test_dev_ok_with_delivery_from_meta(self):
        # AC-33/34/38
        tok = fake.gh_token()
        self.dev("T1", fake.prompt("pedido", 20) + fake.text(f"feito {tok}", 10), result=f"## Entregue\n{tok}")
        code, _, body = self.get("/api/transcript/T1")
        self.assertEqual(code, 200)
        self.assertEqual(set(body), {"id", "squad", "delivery", "entries", "page", "pages", "total", "truncated"})
        self.assertEqual((body["id"], body["squad"], body["delivery"]), ("T1", "dev", f"## Entregue\n{S}"))
        self.assertEqual([e["text"] for e in body["entries"]], ["pedido", f"feito {S}"])
        self.assertEqual((body["page"], body["pages"], body["total"], body["truncated"]), (0, 1, 2, False))

    def test_dev_delivery_from_claude_log_or_null(self):
        self.dev("T1", fake.prompt("x"))
        self.assertIsNone(self.get("/api/transcript/T1")[2]["delivery"])
        write(self.tasks / "T1" / "claude.log", json.dumps({"type": "result", "result": "velho"}) + "\nlixo\n"
              + json.dumps({"type": "result", "result": "Entrega " + fake.gh_token()}) + "\n")
        self.assertEqual(self.get("/api/transcript/T1")[2]["delivery"], f"Entrega {S}")

    def test_dev_dir_and_resumes(self):
        # pasta derivada do worktree; várias sessões juntas por ordem
        write_json(self.tasks / "T2" / "meta.json", {"id": "T2", "status": "concluída", "worktree": WT + "T2"})
        pdir = self.claude / "-srv-projectos--worktrees-T2"
        write(pdir / "A.jsonl", fake.prompt("1", 30) + fake.prompt("3", 10))
        write(pdir / "B.jsonl", fake.prompt("2", 20))
        write(pdir / "A" / "subagents" / "agent-x.jsonl", fake.prompt("subagente"))
        body = self.get("/api/transcript/T2")[2]
        self.assertEqual([e["text"] for e in body["entries"]], ["1", "2", "3"])

    def test_squad_disambiguation_and_meta_session_only(self):
        self.mkt_root = self.mkt
        self.dev("T1", fake.prompt("dev"))
        self.mkt_meta("T1", lines=fake.prompt("mkt"), result="relatório")
        write(self.claude / "-srv-projectos-marketing" / "S2.jsonl", fake.prompt("outra sessão"))
        self.assertEqual(self.get("/api/transcript/T1")[2]["entries"][0]["text"], "dev")
        body = self.get("/api/transcript/T1?squad=marketing")[2]
        self.assertEqual((body["squad"], body["delivery"], [e["text"] for e in body["entries"]]),
                         ("marketing", "relatório", ["mkt"]))
        server.TASKS = Path(self.tmp.name) / "nao-existe"
        self.assertEqual(self.get("/api/transcript/T1")[2]["squad"], "marketing")

    def test_rnd_via_squad_dirs(self):
        self.mkt_root = self.rnd
        self.mkt_meta("R1", cdir="-srv-projectos-rnd", lines=fake.prompt("estudo"))
        self.assertEqual(self.get("/api/transcript/R1")[2]["squad"], "rnd")
        self.assertEqual(self.get("/api/transcript/R1?squad=marketing")[0], 404)

    def test_query_params(self):
        # AC-36 por HTTP: page não-int → 0, f desconhecido → tudo, q
        lines = "".join(fake.prompt(f"p{i}") + fake.text(f"r{i}") + fake.tool_use(str(i), "Bash", {"command": "ls"})
                        + fake.tool_result(str(i)) for i in range(60))
        self.dev("T1", lines)
        b = self.get("/api/transcript/T1?page=x&f=lixo")[2]
        self.assertEqual((b["page"], b["pages"], b["total"], len(b["entries"])), (0, 3, 240, 100))
        self.assertEqual(self.get("/api/transcript/T1?page=9")[2]["entries"], [])
        b = self.get("/api/transcript/T1?f=mensagens&page=1")[2]
        self.assertEqual((b["total"], b["entries"][0]["text"]), (120, "p50"))
        b = self.get("/api/transcript/T1?q=R5")[2]
        self.assertEqual([e["text"] for e in b["entries"]], ["r5"] + [f"r{i}" for i in range(50, 60)])

    def test_delivery_never_in_state(self):
        # o pedido nunca; a entrega (result da meta) só em tasks[].resultado, mascarada e cortada (0.1.3)
        self.dev("T1", fake.prompt("SEGREDO-PEDIDO"), result="SEGREDO-ENTREGA")
        st = server.build_state()
        self.assertNotIn("SEGREDO-PEDIDO", json.dumps(st))
        self.assertEqual([t["resultado"] for t in st["tasks"]], ["SEGREDO-ENTREGA"])
        sem = dict(st, tasks=[{k: v for k, v in t.items() if k != "resultado"} for t in st["tasks"]])
        self.assertNotIn("SEGREDO", json.dumps(sem))


class RulesTest(unittest.TestCase):
    def test_stdlib_only(self):
        # AC-1
        for name in ("server.py", "reader.py", "aprovacoes.py", "quadro.py", "progresso.py", "lancar.py"):
            tree = ast.parse((ROOT / name).read_text())
            mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
            with self.subTest(name=name):
                self.assertLessEqual(mods - {"reader", "aprovacoes", "biblioteca", "quadro", "progresso", "push", "lancar", "officecfg"}, set(sys.stdlib_module_names) | {"__future__"})

    def test_static_no_external_scripts(self):
        rx = re.compile(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*[\"']?(?:https?:)?//", re.I)
        for f in (ROOT / "static").glob("*.html"):
            with self.subTest(f=f.name):
                self.assertIsNone(rx.search(f.read_text()))

    def test_mini_cache_below_tmpfs(self):
        # revisão de segurança: a cache de miniaturas fica abaixo do tmpfs de 128m (o compose é testado em test_compose)
        self.assertLess(biblioteca.MINI_CACHE_MAX, 128 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
