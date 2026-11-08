"""Correcções da ronda 1 de review (S1..S6, C1..C6).

Tokens falsos construídos em runtime (concatenação) para não disparar o gitleaks.
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
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
WT = "/srv/projectos/.worktrees/"
MKT_DIR = "-srv-projectos-marketing"

S = reader.SECRET
R = ("Zq9" + "x7Kp" * 40)   # corpo de token falso (alfanumérico, comprido)


def r(n: int) -> str:
    return R[:n]


class MaskR1Test(unittest.TestCase):
    def check(self, cases):
        for raw, want in cases:
            with self.subTest(raw=raw[:60]):
                self.assertEqual(reader.mask(raw), want)

    def test_new_token_formats(self):
        pem_h, pem_f = "-----BEGIN RSA " + "PRIVATE KEY-----", "-----END RSA " + "PRIVATE KEY-----"
        self.check([
            (f"a\n{pem_h}\nMIIE{r(60)}\n{r(64)}\n{pem_f}\nb", f"a\n{S}\nb"),
            (f"x {pem_h}\nMIIE{r(60)}", f"x {S}"),                                   # sem END: até ao fim
            ("-----BEGIN OPENSSH " + "PRIVATE KEY-----\nb3Bl\n-----END OPENSSH " + "PRIVATE KEY----- ok", f"{S} ok"),
            ("sk" + "_live_" + r(24) + " fim", f"{S} fim"),
            ("rk" + "_test_" + r(24), S),
            ("wh" + "sec_" + r(32), S),
            ("AI" + "za" + r(35), S),
            ("bot" + "123456789:AA" + r(33) + "/sendMessage", f"bot{S}/sendMessage"),
            ("https://api.telegram.org/bot" + "1234567:AA" + r(33) + "/x", f"https://api.telegram.org/bot{S}/x"),
            ("EA" + "AB" + r(80), S),
            ("np" + "m_" + r(36), S),
            ("h" + "f_" + r(34), S),
        ])

    def test_headers(self):
        self.check([
            ("Authorization: token " + r(40), f"Authorization: token {S}"),
            ("Authorization: Basic " + "dXNlcjpwYXNz" + "d29yZA==", f"Authorization: Basic {S}"),
            ("authorization: Bot " + r(30), f"authorization: Bot {S}"),
            ('Authorization: Digest username="x"', f'Authorization: Digest {S}"x"'),
            ("-H 'Authorization: Bearer " + r(30) + "'", f"-H 'Authorization: Bearer {S}'"),
            ("-H 'x-api-key: " + r(32) + "' -d x", f"-H 'x-api-key: {S}' -d x"),
            ('{\\"Authorization\\": \\"Bearer ' + r(30) + '\\"}', '{\\"Authorization\\": \\"Bearer ' + S + '\\"}'),
            ("Cookie: session=" + r(30) + "; other=1\nlinha 2", f"Cookie: {S}\nlinha 2"),
            ("< Set-Cookie: sid=" + r(30) + "; HttpOnly", f"< Set-Cookie: {S}"),
        ])

    def test_cli_flags(self):
        pw = "Pa55" + "wordXyz"
        self.check([
            (f"curl -u admin:{pw} https://x", f"curl -u admin:{S} https://x"),  # gitleaks:allow (valor falso construído em runtime)
            (f"curl --user admin:{pw} x", f"curl --user admin:{S} x"),  # gitleaks:allow (valor falso construído em runtime)
            (f"curl --user=admin:{pw} x", f"curl --user=admin:{S} x"),  # gitleaks:allow (valor falso construído em runtime)
            (f"x --password {pw} y", f"x --password {S} y"),
            (f"x --passwd={pw}", f"x --passwd={S}"),
            (f"x --token '{pw}'", f"x --token '{S}'"),
            (f"x --api-key {pw}", f"x --api-key {S}"),
            (f"x --secret {pw}", f"x --secret {S}"),
            (f"x --client-secret {pw}", f"x --client-secret {S}"),
            (f"sshpass -p {pw} ssh h", f"sshpass -p {S} ssh h"),
            (f"docker login -u x -p {pw} ghcr.io", f"docker login -u x -p {S} ghcr.io"),
            (f"mysql -u root -p{pw} db", f"mysql -u root -p{S} db"),
            (f"htpasswd -nbB ana {pw}", f"htpasswd -nbB ana {S}"),
            (f"htpasswd -b -c f ana {pw} | x", f"htpasswd -b -c f ana {S} | x"),
        ])

    def test_dsn(self):
        self.check([
            ("postgresql://:" + r(16) + "@db/x", f"postgresql://:{S}@db/x"),
            ("postgres://u:ab/cd" + r(10) + "@h/x", f"postgres://u:{S}@h/x"),
            ("redis://default:" + r(20) + "@redis:6379", f"redis://default:{S}@redis:6379"),
            ("x+postgres://u:" + r(12) + "@h", f"x+postgres://u:{S}@h"),
        ])

    def test_assign_names_and_json_escaped(self):
        v = r(30)
        self.check([
            ('{\\"SUPABASE_SERVICE_ROLE_KEY\\": \\"' + v + '\\"}', '{\\"SUPABASE_SERVICE_ROLE_KEY\\": \\"' + S + '\\"}'),
            ('{\\"db_pass\\":\\"' + v + '\\", \\"a\\": 1}', '{\\"db_pass\\":\\"' + S + '\\", \\"a\\": 1}'),
            *[(f"{n}={v}", f"{n}={S}") for n in ("DB_PASS", "SMTP_PASS", "PWD", "ANTHROPIC_AUTH", "AUTH", "CREDENTIALS",
                                                  "PRIVATE", "WEBHOOK_SIGNING", "SUPABASE_SERVICE_ROLE", "SENTRY_DSN")],
            (f"  service_role: {v}", f"  service_role: {S}"),
            ("https://x.io/?api_key=" + v + "&b=2", f"https://x.io/?api_key={S}"),
            ("author: Ana", "author: Ana"),
        ])

    def test_no_false_positives(self):
        for s in ("KEYBOARD", "a chave do problema", "pytest -k test_key", "git push -u origin feat/x",
                  "http://host:8080/x@y", "cat f | docker login --password-stdin", "set -e", "mysql -p",
                  "o curl -u falhou", "Authorization: ", "https://github.com/o/r/pull/1", "1234:AAAA"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)
        # aceitáveis (mascarados, sem perigo): key=lambda, max_tokens=4096
        self.assertEqual(reader.mask("sorted(x, key=lambda y: y)"), f"sorted(x, key={S} y: y)")

    def test_no_quadratic_regex(self):
        # 200 KB de texto adversarial por padrão: cada mask + env_file < 0,5 s
        adv = {"assign": "a.token-" * 25000, "dsn": "x://u:" * 35000, "docker login": "docker login " * 16000,
               "htpasswd": "htpasswd -b " * 17000, "htflags": "htpasswd " + "-a " * 66000, "mysql": "mysql " * 35000,
               "pem": "-----BEGIN " + "A " * 100000, "pem2": "-----BEGIN " + "PRIVATE KEY-----" * 12000,
               "authz": "authorization: " * 14000, "cookie": "cookie:" * 30000, "telegram": "1234567:AA" * 20000,
               "user": "-u a:" * 40000, "quotes": '"' * 200000, "esc": '\\"' * 100000, "kw": ("a" * 119 + "=") * 1700,
               "env": "; env" * 40000, "docker exec": "docker exec " * 18000, "docker": "docker " * 30000,
               "files": "/a.b.c" * 35000, "dots": " " + "a." * 100000, "minified": "e.exports=n.default(t.a);" * 8000,
               "words": "abc-def.ghi+jkl" * 14000, "gh auth": "gh auth status " * 14000}
        for k, v in adv.items():
            with self.subTest(k=k):
                t = time.perf_counter()
                reader.mask(v)
                reader.env_file("Bash", {"command": v})
                self.assertLess(time.perf_counter() - t, 0.5)


class ClipR1Test(unittest.TestCase):
    def test_partial_word_at_cut_removed_before_mask(self):
        n = 1000
        cut = n + reader.SLACK
        tok = fake.gh_token() + "XYZ" * 4
        raw = "Authorization: Bearer " + "A" * 5070 + " " + tok
        self.assertLess(raw.index(tok), cut)
        self.assertGreater(raw.index(tok) + len(tok), cut)   # o corte cai a meio do ghp_
        out = reader.clip(raw, n)
        self.assertEqual(out, f"Authorization: Bearer {S}…")
        jwt = "ey" + "J" + "h" * 30 + "." + "ey" + "J" + "p" * 30 + "." + "s" * 40
        raw2 = "Bearer " + "B" * (cut - 7 - 1 - 70) + " " + jwt
        self.assertEqual(reader.clip(raw2, n), f"Bearer {S}…")
        self.assertNotIn("eyJ", reader.clip("x " * 2547 + jwt, n))

    def test_long_single_word_kept(self):
        self.assertEqual(reader.clip("x" * 30000, 20000), "x" * 20000 + "…")
        self.assertEqual(reader.clip("a " * 3000, 1000), ("a " * 500) + "…")


class SensitiveToolTest(unittest.TestCase):
    def test_hidden(self):
        def B(c):
            return "Bash", {"command": c}
        yes = [B("env"), B("printenv"), B("printenv SUPABASE_SERVICE_ROLE_KEY"), B("env | grep KEY"), B("set"),
               B("export -p"), B("cd x && env"), B("sudo env"), B("cat /proc/self/environ"), B("cat /proc/1/environ"),
               B("docker inspect api"), B("docker container inspect api"), B("docker compose -f x.yml config"),
               B("docker-compose config"), B("docker exec api env"), B("docker exec -it api printenv"),
               B("gh auth token"), B("gh auth status -t"), B("gh auth status --show-token"),
               B("cat secrets.env"), B("cat config/production.env"), B("cat .dev.vars"), B("cat ~/.netrc"),
               B("cat ~/.ssh/id_rsa"), B("cat ~/.ssh/id_ed25519"), B("cat k/id_ecdsa"), B("cat tls/server.pem"),
               B("openssl x -in a.key"), B("cat c.p12"), B("cat c.pfx"), B("cat ~/.config/gh/hosts.yml"),
               B("cat ~/.aws/credentials"), B("cat credentials.json"), B("cat .envrc"),
               ("Read", {"file_path": "/proc/self/environ"}), ("Read", {"file_path": "/casa/u/app/secrets.env"}),
               ("Read", {"file_path": "/casa/u/.ssh/id_rsa"}), ("Read", {"file_path": "/casa/u/.dev.vars"}),
               ("Read", {"file_path": "/casa/u/.netrc"}), ("Edit", {"file_path": "/x/tls.key"}),
               ("Write", {"file_path": "/x/hosts.yml"}), ("Read", {"file_path": "/x/credentials"}),
               ("Read", {"file_path": "/x/cert.pfx"}), ("Grep", {"pattern": "KEY", "glob": "*.env"}),
               ("Grep", {"pattern": "x", "path": "/casa/u/.ssh/id_ed25519"}), ("Glob", {"pattern": "**/*.pem"}),
               ("Grep", {"pattern": "x", "path": "/proc/self/environ"})]
        no = [B("pytest -q"), B("python3 -m unittest"), B("ls -la"), B("cat README.md"), B("env X=1 cmd"),
              B("env -u X cmd"), B("set -e"), B("export A=1"), B("docker ps"), B("docker exec api ls"),
              B("docker compose up -d"), B("gh auth status"), B("cat id_rsa.pub"), B("pytest -k test_key"),
              B("python3 -m venv .venv"), B("echo $ENV"), ("Read", {"file_path": "/w/env.py"}),
              ("Read", {"file_path": "/w/.envy/x.py"}), ("Read", {"file_path": "/w/keys.py"}),
              ("Read", {"file_path": "/w/id_rsa.pub"}), ("Grep", {"pattern": "SERVICE_ROLE", "path": "."})]
        for name, inp in yes:
            with self.subTest(yes=inp):
                self.assertTrue(reader.env_file(name, inp))
        for name, inp in no:
            with self.subTest(no=inp):
                self.assertFalse(reader.env_file(name, inp))

    def test_parse_hides_result(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        p = tmp / "S.jsonl"
        p.write_text(fake.tool_use("1", "Bash", {"command": "printenv"}) + fake.tool_result("1", False, "A=1")
                     + fake.tool_use("2", "Bash", {"command": "ls"}) + fake.tool_result("2", False, "a.txt"))
        reader._cache.clear()
        self.addCleanup(reader._cache.clear)
        self.assertEqual([e["text"] for e in reader.parse([p])[0]], ["printenv", reader.HIDDEN, "ls", "a.txt"])


class TruncatedOrphanTest(unittest.TestCase):
    def test_orphan_result_hidden_only_when_truncated(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        reader._cache.clear()
        self.addCleanup(reader._cache.clear)
        p = tmp / "S.jsonl"
        # o tool_use (um cat .env) fica antes do corte; o resultado depois
        p.write_text(fake.tool_use("1", "Bash", {"command": "cat .env" + " " * 400}) + fake.tool_result("1", False, "A=1")
                     + fake.tool_use("2", "Read") + fake.tool_result("2", False, "visto"))
        with mock.patch.object(reader, "MAX_BYTES", p.stat().st_size - 300):
            e, truncated = reader.parse([p])
        self.assertTrue(truncated)
        self.assertEqual([x["text"] for x in e], [reader.HIDDEN, "", "visto"])
        reader._cache.clear()
        e, truncated = reader.parse([p])   # inteiro: resultado sem tool_use conhecido sai normal
        self.assertFalse(truncated)
        self.assertEqual(e[1]["text"], reader.HIDDEN)
        p.write_text(fake.tool_result("9", False, "solto"))
        reader._cache.clear()
        self.assertEqual(reader.parse([p])[0][0]["text"], "solto")


class AtExtremeTest(unittest.TestCase):
    def test_reader_at_extreme_dates(self):
        for ts in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:30:00-05:00"):
            self.assertIsNone(reader._at(ts))
        self.assertEqual(reader._at("2026-10-26T10:00:00+01:00"), "2026-10-26T09:00:00+00:00")



class AllowlistTest(unittest.TestCase):
    # S1/S6: o leitor só abre sessões headless das squads ligadas a uma meta
    def setUp(self):
        base.ServerTest.setUp(self)
        reader._cache.clear()

    def tearDown(self):
        reader._cache.clear()
        base.ServerTest.tearDown(self)

    def get(self, tid, q=""):
        return server.transcript(tid, q)

    def meta(self, tid, cdir=MKT_DIR, sid=fake.SID, lines=None, root=None):
        write_json((root or self.mkt) / tid / "meta.json", {"id": tid, "status": "concluída", "session_id": sid,
                                                            "claude_dir": cdir})
        if lines is not None:
            write(self.claude / cdir / f"{sid}.jsonl", lines)

    def test_squad_dir_from_meta(self):
        # Lite: claude_dir vem da meta (escrita pelo runner) e só tem de ser um nome simples (_safe)
        self.meta("M1", lines=fake.prompt("ok"))
        self.assertEqual(self.get("M1")["entries"][0]["text"], "ok")
        self.meta("R1", cdir="-srv-projectos-rnd", lines=fake.prompt("r"), root=self.rnd)
        self.meta("P1", cdir="-srv-projectos-support", lines=fake.prompt("p"), root=self.sup)
        self.assertEqual(self.get("R1")["squad"], "rnd")
        self.assertEqual(self.get("P1")["squad"], "support")
        write(self.claude / "-srv-privado" / f"{fake.SID}.jsonl", fake.prompt("PRIVADO"))
        for i, cdir in enumerate(("../-srv-privado", "-srv-privado/", "x/../-srv-privado", ".", "..", "", None, 5)):
            with self.subTest(cdir=cdir):
                write_json(self.mkt / f"X{i}" / "meta.json", {"id": f"X{i}", "status": "concluída",
                                                               "session_id": fake.SID, "claude_dir": cdir})
                self.assertIsNone(self.get(f"X{i}"))

    def test_session_id_must_be_uuid(self):
        for i, sid in enumerate(("S1", "lnk", fake.SID.upper(), fake.SID + "x", "." , "..")):
            with self.subTest(sid=sid):
                self.meta(f"U{i}", sid=sid, lines=fake.prompt("x") if sid not in (".", "..") else None)
                self.assertIsNone(self.get(f"U{i}"))

    def test_symlink_rejected(self):
        write(self.claude / "-srv-privado" / "h.jsonl", fake.prompt("PRIVADO"))
        (self.claude / MKT_DIR).mkdir()
        os.symlink("../-srv-privado/h.jsonl", self.claude / MKT_DIR / f"{fake.SID}.jsonl")
        self.meta("L1")
        self.assertIsNone(self.get("L1"))
        # dev: symlink na pasta do worktree também não
        write_json(self.tasks / "T1" / "meta.json", {"id": "T1", "status": "concluída", "worktree": WT + "T1"})
        d = server._dev_dir({"worktree": WT + "T1"}, "T1")
        write(d / "ok.jsonl", fake.prompt("dev"))
        os.symlink("../-srv-privado/h.jsonl", d / "lnk.jsonl")
        self.assertEqual([e["text"] for e in self.get("T1")["entries"]], ["dev"])

    def test_interactive_entrypoint_rejected(self):
        cli = fake.line({"type": "user", "entrypoint": "cli", "timestamp": fake.ts(0),
                         "message": {"role": "user", "content": "PRIVADO"}})
        noep = json.dumps({"type": "user", "timestamp": fake.ts(0), "message": {"content": "PRIVADO"}}) + "\n"
        for i, lines in enumerate((cli, noep, fake.line({"type": "summary"}) * 31 + fake.prompt("tarde"))):
            with self.subTest(i=i):
                self.meta(f"C{i}", lines=lines)
                self.assertIsNone(self.get(f"C{i}"))
        # entrypoint numa linha que não é a 1.ª (queue-operation antes) conta
        self.meta("C9", lines=fake.line({"type": "queue-operation"}) + fake.prompt("ok"))
        self.assertEqual(self.get("C9")["entries"][0]["text"], "ok")
        # dev: sessão interactiva na pasta do worktree fica de fora, a headless sai
        write_json(self.tasks / "T1" / "meta.json", {"id": "T1", "status": "concluída", "worktree": WT + "T1"})
        d = server._dev_dir({"worktree": WT + "T1"}, "T1")
        write(d / "a.jsonl", cli)
        write(d / "b.jsonl", fake.prompt("dev"))
        self.assertEqual([e["text"] for e in self.get("T1")["entries"]], ["dev"])
        write(d / "b.jsonl", cli)
        self.assertIsNone(self.get("T1"))

    def test_dev_worktree_shape(self):
        sid = "-srv-projectos--worktrees-T1"
        for wt, want in ((WT + "T1", sid), ("/srv/projectos/x/.worktrees/T1", "-srv-projectos-x--worktrees-T1"),
                         ("/srv/x/.worktrees/T1", "-srv-x--worktrees-T1"), (WT + "T1/", sid),
                         ("/tmp/xyz/.worktrees/T1", "-tmp-xyz--worktrees-T1"),
                         ("/srv/privado/.worktrees/foo", None), (WT + "T1/sub", None), ("/x/notworktrees/T1", None),
                         ("/x/.worktrees/T1/../T1", None), (WT + "T2", None)):
            with self.subTest(wt=wt):
                self.assertEqual(server._dev_dir({"worktree": wt}, "T1"), want and self.claude / want)
        # worktree a apontar para outra tarefa (com --worktrees- no nome) não é usado no leitor
        write(self.claude / "-srv-privado--worktrees-foo" / "s.jsonl", fake.prompt("PRIVADO"))
        write_json(self.tasks / "T1" / "meta.json", {"id": "T1", "status": "concluída",
                                                     "worktree": "/srv/privado/.worktrees/foo"})
        self.assertIsNone(self.get("T1"))

    def test_safe_rejects_dots(self):
        for v in (".", "..", "...", "a..b"):
            self.assertFalse(server._safe(v))
        for v in ("a.b", ".env", "T1", "-x-"):
            self.assertTrue(server._safe(v))
        write_json(self.tasks / "meta.json", {"id": ".", "status": "concluída"})
        self.assertIsNone(self.get("."))
        self.assertIsNone(self.get("%2e"))



NOW = base.NOW


class GitHubR1Test(LiveBase):
    URL = "https://github.com/o/r/pull/7"

    def bash(self, i, cmd, err=False, out="ok", age=0):
        return fake.tool_use(f"g{i}", "Bash", {"command": cmd}, age) + fake.tool_result(f"g{i}", err, out, age)

    def gh(self, lines):
        self.reset()
        self.task(lines=lines)
        return self.get()["gh"]

    def test_reviewer_scenarios(self):
        # C1: corpo de heredoc/strings não classifica; create só com o URL na última linha do resultado
        create_body = "gh pr create --title x --body \"$(cat <<'EOF'\nPassos:\ngh pr merge depois\nEOF\n)\""
        commit_push = "git add -A && git commit -F - <<'EOF'\nfix\n\ngh pr create falhava\nEOF\ngit push -u origin HEAD"
        cases = [
            (self.bash(1, create_body, out=f"Creating…\n{self.URL}\n"), ("aberto", self.URL)),
            (self.bash(1, commit_push, out="[b 1] fix\nTo github.com:o/r\n"), (None, None)),
            (self.bash(1, 'echo "feito; gh pr merge 7"'), (None, None)),
            (self.bash(1, "URL=\"$(gh pr create --fill)\"", out=self.URL), ("aberto", self.URL)),
            (self.bash(1, "gh pr create --fill", out=f"{self.URL}\nmais texto"), (None, None)),
            (self.bash(1, "gh pr create --fill", out="aviso: já existe " + self.URL), (None, None)),
            (self.bash(1, "gh pr view 7 && echo gh pr create", out=self.URL), ("aberto", self.URL)),
            (self.bash(1, "cat <<<'x'\ngh pr merge 7"), ("integrado", None)),   # here-string não é heredoc
            (self.bash(1, "cat > f <<-EOF\n  gh pr merge 1\n  EOF\ngh pr merge 7"), ("integrado", None)),
        ]
        for lines, want in cases:
            with self.subTest(lines=lines[:140]):
                g = self.gh(lines)
                self.assertEqual((g["pr"], g["pr_url"]), want)
        self.reset()
        path = self.task(lines=fake.tool_use("u0", "Read"))
        self.get()
        self.append(path, self.bash(1, commit_push))
        self.assertIn("push", [e["kind"] for e in server.build_state()["feed"]])   # o push conta, o create não
        self.assertNotIn("pr_aberto", [e["kind"] for e in server.build_state()["feed"]])
        self.assertEqual(server._gh_cls("Bash", {"command": commit_push}), ("push", True))

    def test_checks_by_content(self):
        # C2: gh pr checks pelo conteúdo; gh run watch pelo is_error
        t = "\t"
        cases = [
            (f"a{t}pass{t}1m{t}u\nb{t}fail{t}2m{t}u", False, "vermelho"),
            (f"Exit code 1\na{t}fail{t}1m{t}u", True, "vermelho"),
            (f"a{t}pass{t}1m{t}u\nb{t}pending{t}0{t}u", True, None),
            (f"a{t}pass{t}1m{t}u\nb{t}skipping{t}0{t}u", False, "verde"),
            (f"a{t}pass{t}1m{t}u\nb{t}cancel{t}0{t}u", False, None),
            ("no checks reported on the 'x' branch", True, None),
            ("ok", False, None),
        ]
        for out, err, want in cases:
            with self.subTest(out=out):
                self.assertEqual(self.gh(self.bash(1, "gh pr checks 7", err=err, out=out))["ci"], want)
        self.assertEqual(self.gh(self.bash(1, "gh run watch 9", err=True, out=f"a{t}pass"))["ci"], "vermelho")
        self.assertEqual(self.gh(self.bash(1, "gh run watch 9", out="x"))["ci"], "verde")

    def test_feed_dedup_by_pr_number(self):
        path = self.task(lines=fake.tool_use("u0", "Agent"))
        self.sub("T1", "a1", fake.tool_use("s0", "Read"))
        self.get()
        sub = self.pdir() / "S0" / "subagents" / "agent-a1.jsonl"
        self.append(sub, self.bash(1, "gh pr create --fill", out=self.URL, age=3))
        self.append(path, fake.pr_link(7, self.URL, 2) + fake.pr_link(7, self.URL, 1))
        st = server.build_state()
        self.assertEqual([e["kind"] for e in st["feed"]].count("pr_aberto"), 1)
        self.assertNotIn("_pr", json.dumps(st))
        # pr-link sem URL válido e depois o create com URL: o mesmo evento
        self.reset()
        path = self.task(lines=fake.tool_use("u0", "Read"))
        self.get()
        self.append(path, fake.pr_link(7, "https://evil.example/x", 3) + self.bash(2, "gh pr create", out=self.URL))
        st = server.build_state()
        self.assertEqual([e["kind"] for e in st["feed"]].count("pr_aberto"), 1)
        self.assertEqual(self.get()["gh"]["pr_url"], self.URL)

    def test_strip_cmd_linear(self):
        for cmd in ("cat <<A\n" * 40000, "x\n" * 100000, '"' * 200000, "'a" * 100000):
            with self.subTest(cmd=cmd[:10]):
                t = time.perf_counter()
                server._gh_cls("Bash", {"command": cmd})
                self.assertLess(time.perf_counter() - t, 0.5)



class LiveR1Test(LiveBase):
    def subm(self, tid, stem, aid, lines, age=0, **meta):
        d = self.pdir(tid) / stem / "subagents"
        write_json(d / f"agent-{aid}.meta.json", {"agentType": "backend-engineer", "description": "x", **meta})
        return write(d / f"agent-{aid}.jsonl", lines, age_s=age)

    def beat(self, tid="T1"):
        write(self.tasks / tid / "heartbeat", "", age_s=10)

    def test_old_session_subagents_not_alive(self):
        # C3: sessão S0 antiga com subagente morto a meio de um Bash; retoma em S1 activa
        self.task("T1", fake.tool_use("u0", "Agent"), age=7200)
        self.sub("T1", "a1", fake.tool_use("b1", "Bash", {"command": "sleep 1"}), age=7200)
        self.session("T1", "S1", fake.tool_use("u2", "Read") + fake.tool_result("u2"), age=5)
        self.beat()
        t = self.get()
        self.assertEqual([(a["act"], a["stuck"]) for a in t["agents"]], [(None, False)])

    def test_subagent_alive_by_tool_use_id(self):
        self.task("T1", fake.tool_use("tA", "Agent") + fake.tool_use("tB", "Agent") + fake.tool_result("tB"))
        self.subm("T1", "S0", "a1", fake.tool_use("b1", "Bash"), age=11 * 60, toolUseId="tA")   # à espera: vivo
        self.subm("T1", "S0", "a2", fake.tool_use("b2", "Bash"), age=11 * 60, toolUseId="tB")   # já respondeu
        self.subm("T1", "S0", "a3", fake.tool_use("b3", "Bash"), age=11 * 60, toolUseId="tX")   # desconhecido
        self.beat()
        got = sorted((a["act"] is not None, a["stuck"]) for a in self.get()["agents"])
        self.assertEqual(got, [(False, False), (False, False), (True, True)])

    def test_toolless_subagents_only_in_newest_session(self):
        self.task("T1", fake.tool_use("u0", "Agent"), age=600)
        self.sub("T1", "a1", fake.tool_use("b1", "Read"), age=600)
        self.session("T1", "S1", fake.tool_use("u1", "Agent"), age=5)
        self.subm("T1", "S1", "a2", fake.tool_use("b2", "Grep"), age=5)
        acts = sorted(str((a["act"] or {}).get("tool")) for a in self.get()["agents"])
        self.assertEqual(acts, ["Grep", "None"])

    def test_stuck_only_last_tool_use(self):
        # C4: Bash órfão antigo + Agent em curso → não preso; Bash último pendente → preso
        lines = (fake.tool_use("u1", "Bash", {"command": "npm run dev"}, age=3000) + fake.text("retomo", age=2000)
                 + fake.tool_use("u2", "Agent", {"description": "backend"}, age=1200))
        self.task("T1", lines, age=1200)
        self.assertFalse(self.get()["lead"]["stuck"])
        self.reset()
        self.task("T1", fake.tool_use("u1", "Read", age=3000) + fake.tool_use("u2", "Read", age=1200)
                  + fake.tool_result("u2", age=1200), age=1200)
        self.assertFalse(self.get()["lead"]["stuck"])
        self.reset()
        self.task("T1", fake.tool_use("u1", "Read", age=3000) + fake.tool_result("u1", age=3000)
                  + fake.tool_use("u2", "Bash", age=1200), age=1200)
        self.assertTrue(self.get()["lead"]["stuck"])
        self.reset()
        self.task("T1", fake.tool_use("u2", "Bash", age=1200) + fake.thinking(age=1200), age=1200)
        self.assertFalse(self.get()["lead"]["stuck"])

    def test_extreme_dates(self):
        # C5: /api/state não rebenta com started_at/timestamps extremos
        for ts in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:30:00-05:00"):
            with self.subTest(ts=ts):
                self.reset()
                bad = fake.line({"type": "assistant", "timestamp": ts, "message": {"content": [
                    {"type": "tool_use", "id": "a", "name": "Read", "input": {}}]}})
                self.task("T1", bad, started_at=ts)
                write_json(self.tasks / "T9" / "meta.json", {"id": "T9", "status": "concluída", "started_at": ts})
                for _ in range(2):
                    st = server.build_state(NOW)
                    self.assertEqual(len(st["tasks"]), 2)
                self.assertEqual(self.get()["lead"]["act"]["at"], server._iso_ts(NOW))   # mtime do ficheiro
                server._add_msg(json.loads(bad), 0)

    def test_extreme_dates_http(self):
        write_json(self.tasks / "T9" / "meta.json", {"id": "T9", "status": "concluída",
                                                     "started_at": "9999-12-31T23:30:00-05:00"})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        server._cache["at"] = 0
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/state", timeout=5) as r:
            self.assertEqual(r.status, 200)

    def test_dry_run_not_tracked(self):
        # C6: ensaio não segue transcripts
        self.task("T1", fake.tool_use("u1", "Read"), mode="dry-run")
        self.sub("T1", "a1", fake.tool_use("b1", "Read"))
        t = self.get()
        self.assertEqual((t["lead"]["act"], t["agents"]), (None, []))
        self.assertEqual(server._live, {})


if __name__ == "__main__":
    unittest.main()
