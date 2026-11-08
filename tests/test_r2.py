"""Correcções da ronda 2 de review (1..7).

Tokens falsos construídos em runtime (concatenação) para não disparar o gitleaks.
"""
import time
import unittest

import reader
import server
from tests import fake
from tests import test_server as base
from tests.test_live import LiveBase
from tests import cfg

OFFICE_CFG = cfg.LEGADO

write, write_json = base.write, base.write_json
S = reader.SECRET
V = "Zq9" + "kLm2Xw8Rt"   # valor falso
LAUNCHED = "Async agent launched successfully.\nagentId: a1"


def fast(test, fn, *args):
    t = time.perf_counter()
    fn(*args)
    test.assertLess(time.perf_counter() - t, 0.5)


class R2Base(LiveBase):
    URL = "https://github.com/o/r/pull/7"

    def subm(self, tid, stem, aid, lines, age=0, **meta):
        d = self.pdir(tid) / stem / "subagents"
        write_json(d / f"agent-{aid}.meta.json", {"agentType": "backend-engineer", "description": "x", **meta})
        return write(d / f"agent-{aid}.jsonl", lines, age_s=age)

    def beat(self, tid="T1"):
        write(self.tasks / tid / "heartbeat", "", age_s=10)

    def bash(self, i, cmd, err=False, out="ok", age=0):
        return fake.tool_use(f"g{i}", "Bash", {"command": cmd}, age) + fake.tool_result(f"g{i}", err, out, age)

    def gh(self, lines):
        self.reset()
        self.task(lines=lines)
        return self.get()["gh"]


class BackgroundSubagentTest(R2Base):
    # 1: Agent em fundo responde logo («Async agent launched»): o subagente vive enquanto não acabar
    def lead(self, age=600):
        return (fake.tool_use("u0", "Agent", {"description": "po", "run_in_background": True}, age=age)
                + fake.tool_result("u0", content=LAUNCHED, age=age)
                + fake.tool_use("u1", "Read", age=age) + fake.tool_result("u1", age=age))

    def acts(self):
        return [(a["act"] and a["act"]["tool"], a["stuck"]) for a in self.get()["agents"]]

    def test_request_shape_background(self):
        self.task("T1", self.lead(), age=590)
        self.subm("T1", "S0", "a1", fake.tool_use("b1", "Bash", {"command": "sleep 9"}, age=700), age=700,
                  toolUseId="u0", requestShape="background")
        self.beat()
        self.assertEqual(self.acts(), [("Bash", True)])

    def test_launched_result_without_shape(self):
        self.task("T1", self.lead(), age=5)
        self.subm("T1", "S0", "a1", fake.tool_use("b1", "Grep", age=5), age=5, toolUseId="u0")
        self.assertEqual(self.acts(), [("Grep", False)])

    def test_finished_or_old_session_not_alive(self):
        self.task("T1", self.lead(), age=5)
        self.subm("T1", "S0", "a1", fake.tool_use("b1", "Bash", age=900), age=900, toolUseId="u0",
                  requestShape="background", finished=True)
        self.assertEqual(self.acts(), [(None, False)])
        self.reset()
        self.task("T1", self.lead(7200), age=7200)
        self.subm("T1", "S0", "a1", fake.tool_use("b1", "Bash", age=7200), age=7200, toolUseId="u0",
                  requestShape="background")
        self.session("T1", "S1", fake.tool_use("u2", "Read") + fake.tool_result("u2"), age=5)
        self.beat()
        self.assertEqual(self.acts(), [(None, False)])

    def test_foreground_unchanged(self):
        # foreground já respondido continua morto
        self.task("T1", fake.tool_use("u0", "Agent") + fake.tool_result("u0", content="feito"), age=5)
        self.subm("T1", "S0", "a1", fake.tool_use("b1", "Grep", age=5), age=5, toolUseId="u0",
                  requestShape="foreground")
        self.assertEqual(self.acts(), [(None, False)])


class GitHubR2Test(R2Base):
    def test_unclosed_quote_linear(self):
        # 2: aspas por fechar + muitos \" (antes quadrático) e comando enorme só nos primeiros 64 KB
        for cmd in ('"' + '\\"' * 40000, '"' + '\\"' * 100000, "'" + "a" * 200000, ('x "' + "\\" * 3) * 50000,
                    "x=$((" * 40000, '"' + '\\"' * 40000 + "\\", '"\\' * 100000, "cat <<\\" * 30000):
            with self.subTest(cmd=cmd[:10]):
                fast(self, server._gh_cls, "Bash", {"command": cmd})
        self.assertEqual(server._gh_cls("Bash", {"command": 'echo "a\\" b\ngh pr merge 7'}), (None, False))
        self.assertEqual(server._gh_cls("Bash", {"command": "x" * server.CMD_MAX + "\ngh pr merge 7"}),
                         (None, False))
        self.assertEqual(server._gh_cls("Bash", {"command": "x" * 1000 + "\ngh pr merge 7"}), ("merge", False))

    def test_heredoc_backslash_and_arith(self):
        # 3: <<\EOF é heredoc; << dentro de $(( )) é shift
        cases = [(self.bash(1, "cat <<\\EOF\nfoo; gh pr merge 9\nEOF"), (None, None)),
                 (self.bash(1, "x=$((1<<3))\ngh pr merge 7"), ("integrado", None)),
                 (self.bash(1, "((y = 2 << z))\ngh pr merge 7"), ("integrado", None)),
                 (self.bash(1, "x=$(( (1<<3) + 1 )) && cat <<EOF\ngh pr merge 1\nEOF"), (None, None))]
        for lines, want in cases:
            with self.subTest(lines=lines[:100]):
                g = self.gh(lines)
                self.assertEqual((g["pr"], g["pr_url"]), want)

    def test_open_does_not_override_merged(self):
        # 4: gh pr edit depois do merge com corpo que fala de gh pr create → continua integrado
        edit = "gh pr edit 7 --body \"$(cat <<'EOF'\nnão usar gh pr create\nEOF\n)\""
        path = self.task(lines=fake.tool_use("u0", "Read") + fake.tool_result("u0"))
        self.get()
        self.append(path, self.bash(1, "gh pr merge 7 --squash", out="merged") + self.bash(2, edit, out=self.URL))
        st = server.build_state()
        self.assertEqual([e["kind"] for e in st["feed"]].count("pr_aberto"), 0)
        g = self.get()["gh"]
        self.assertEqual((g["pr"], g["pr_url"]), ("integrado", self.URL))
        # com URL conhecido: o mesmo número fica integrado, outro número abre
        u8 = self.URL[:-1] + "8"
        g = self.gh(self.bash(1, "gh pr create", out=self.URL) + self.bash(2, "gh pr merge 7")
                    + self.bash(3, edit, out=self.URL))
        self.assertEqual((g["pr"], g["pr_url"]), ("integrado", self.URL))
        g = self.gh(self.bash(1, "gh pr create", out=self.URL) + self.bash(2, "gh pr merge 7")
                    + self.bash(3, "gh pr create", out=u8))
        self.assertEqual((g["pr"], g["pr_url"]), ("aberto", u8))


class DevDirR2Test(unittest.TestCase):
    # 5 (Lite, AC-b1): pasta derivada de qualquer worktree <…>/.worktrees/<id>, sem prefixo fixo
    def setUp(self):
        base.ServerTest.setUp(self)
        reader._cache.clear()

    def tearDown(self):
        reader._cache.clear()
        base.ServerTest.tearDown(self)

    def get(self, tid):
        return server.transcript(tid, "")

    def test_any_root_derives(self):
        tid = "20261026-1100-z"
        for wt, want in ((f"/srv/x/.worktrees/{tid}", f"-srv-x--worktrees-{tid}"),
                         (f"/tmp/xyz/.worktrees/{tid}", f"-tmp-xyz--worktrees-{tid}"),
                         (f"/srv/projectos/joga/.worktrees/{tid}", f"-srv-projectos-joga--worktrees-{tid}")):
            with self.subTest(wt=wt):
                self.assertEqual(server._dev_dir({"worktree": wt}, tid), self.claude / want)

    def test_not_a_worktree_is_none(self):
        tid = "20261026-1100-z"
        for m in ({}, {"worktree": None}, {"worktree": 5}, {"worktree": f"/srv/x/{tid}"},
                  {"worktree": f"/srv/x/.worktrees/{tid}/sub"}, {"worktree": "/srv/x/.worktrees/outro"}):
            with self.subTest(m=m):
                self.assertIsNone(server._dev_dir(m, tid))
        # sem worktree na meta: sem transcript (acabou o padrão fixo com o id)
        write(self.claude / f"-srv-projectos--worktrees-{tid}" / f"{fake.SID}.jsonl", fake.prompt("PRIVADO"))
        write_json(self.tasks / tid / "meta.json", {"id": tid, "status": "concluída"})
        self.assertIsNone(self.get(tid))


class MaskR2Test(unittest.TestCase):
    def check(self, cases):
        for raw, want in cases:
            with self.subTest(raw=raw[:60]):
                self.assertEqual(reader.mask(raw), want)

    def test_assign(self):
        # 6: sem aspas até espaço/fim; aspas com escapes; bearer/basic/token/bot/digest antes do valor
        self.check([
            (f"DB_PASSWORD={V}&TAIL{V}", f"DB_PASSWORD={S}"),
            (f'"password":"ab\\"{V}"', f'"password":"{S}"'),
            (f"API_KEY=bot {V}x", f"API_KEY=bot {S}"),
            (f"SECRET: basic {V}xyz", f"SECRET: basic {S}"),
            (f"TOKEN=Bearer {V} fim", f"TOKEN=Bearer {S} fim"),
            (f"POSTGRES_PASSWORD: {V},tail;x y", f"POSTGRES_PASSWORD: {S} y"),
            (f'secret = "a {V}', f'secret = "{S}"'),           # aspas por fechar: até ao fim da linha
            (f"secret = '{V}' z", f"secret = '{S}' z"),
            ("AUTH=token", f"AUTH={S}"),
        ])

    def test_cli_flags(self):
        # 7: gh secret set, echo | gh secret set / docker login --password-stdin, redis-cli -a, mysql -p'…'
        self.check([
            (f"gh secret set X --body {V}", f"gh secret set X --body {S}"),
            (f"gh secret set X -b {V} -R o/r", f"gh secret set X -b {S} -R o/r"),
            (f"gh secret set X --body='{V}'", f"gh secret set X --body='{S}'"),
            (f"echo {V} | gh secret set X", f"echo {S} | gh secret set X"),
            (f"echo -n '{V}' | gh secret set X", f"echo -n {S} | gh secret set X"),
            (f"echo {V} | docker login ghcr.io -u x --password-stdin", f"echo {S} | docker login ghcr.io -u x --password-stdin"),
            (f"redis-cli -h r -a {V} ping", f"redis-cli -h r -a {S} ping"),
            (f"mysql -u root -p'{V}' db", f"mysql -u root -p'{S}' db"),
            (f'mysql -u root -p"{V}"', f'mysql -u root -p"{S}"'),
        ])
        self.assertNotIn(V, reader.mask(f'printf "%s" "{V}" | docker login -u x --password-stdin'))

    def test_no_false_positives(self):
        for s in ("echo ok | grep x", "gh secret list", "redis-cli ping", "mysql -p", "ls -la",
                  "cat f | docker login --password-stdin", "echo feito"):
            with self.subTest(s=s):
                self.assertEqual(reader.mask(s), s)


class SensitiveR2Test(unittest.TestCase):
    def test_hidden(self):
        def B(c):
            return "Bash", {"command": c}
        yes = [B("cat ~/.pgpass"), B("cat ~/.git-credentials"), B("cat .npmrc"), B("cat ~/.claude/.credentials.json"),
               B("cat ~/.docker/config.json"), B("cat /proc/$$/environ"), B("cat /proc/self/environ"),
               B("env -0"), B("env -0 | sort"), B("env -u X"), B("env --unset=X"), B("declare -p"), B("declare -x"),
               B("declare -px"), B("declare -p DB_PASSWORD"), B("compgen -e"), B("x; compgen -e"),
               B("kubectl get secret x -o yaml"), B("kubectl -n p get secrets"), B("supabase status -o env"),
               B("npx supabase status"),
               ("Read", {"file_path": "/casa/u/.pgpass"}), ("Read", {"file_path": "/casa/u/.git-credentials"}),
               ("Read", {"file_path": "/casa/u/.npmrc"}), ("Read", {"file_path": "/casa/u/.claude/.credentials.json"}),
               ("Read", {"file_path": "/casa/u/.docker/config.json"}), ("Read", {"file_path": "/proc/$$/environ"}),
               ("Grep", {"pattern": "x", "path": "/casa/u/.git-credentials"})]
        no = [B("ls"), B("pytest"), B("pytest -q"), B("cat README.md"), B("env X=1 cmd"), B("env -u X cmd"),
              B("env -i PATH=/bin sh"), B("declare -a arr"), B("declare x=1"), B("compgen -c"),
              B("kubectl get pods"), B("supabase start"), B("cat config.json"), B("cat .npmrc.md"),
              ("Read", {"file_path": "/w/config.json"}), ("Read", {"file_path": "/w/npmrc.py"})]
        for name, inp in yes:
            with self.subTest(yes=inp):
                self.assertTrue(reader.env_file(name, inp))
        for name, inp in no:
            with self.subTest(no=inp):
                self.assertFalse(reader.env_file(name, inp))


class LinearR2Test(unittest.TestCase):
    def test_no_quadratic_regex(self):
        # 200 KB adversarial nas regexes alteradas: mask + env_file < 0,5 s cada
        adv = {"assign quote": 'key="' + '\\"' * 100000, "assign quotes": 'key="a ' * 28000,
               "assign bearer": "key=bearer " * 18000, "assign esc": 'key="\\' * 33000,
               "redis": "redis-cli " * 20000, "redis -a": ("redis-cli" + " -a" * 60 + "\n") * 1000,
               "gh secret": "gh secret set " * 14000, "gh secret -b": ("gh secret set" + " -x" * 60 + "\n") * 1000,
               "echo": "echo " * 40000, "echo pipe": "echo x | " * 25000, "echo quote": 'echo "' * 33000,
               "echo docker": ("echo x | docker login " + "y" * 150 + "\n") * 1100, "mysql q": "mysql -p'" * 25000,
               "env flags": "env" + " -0" * 66000, "env -u": "env" + " -u x" * 40000, "declare": "declare -" * 25000,
               "kubectl": "kubectl " * 25000, "kubectl get": ("kubectl" + " get" * 45 + "\n") * 1100,
               "proc": "/proc/" * 33000, "proc2": "/proc/" + "a" * 200000, "docker cfg": ".docker/" * 25000,
               "files": ".git-credentials" * 12000,
               "md table": "| " * 100000, "md table key": ("|" + "a_key" * 30 + "|" + "x|" * 50 + "\n") * 600,
               "md table name": ("| " + "k" * 150 + "_key\n") * 1300, "apikey": "apikey=" * 28000,
               "akia": ("AK" + "IA" + "A" * 16 + " ") * 9000, "akia aws": ("AK" + "IA" + "A" * 16 + " " + "a" * 39 + " ") * 3000,
               "aws key": "aws_secret_access_key " * 9000}
        for k, v in adv.items():
            with self.subTest(k=k):
                fast(self, lambda s: (reader.mask(s), reader.env_file("Bash", {"command": s}),
                                      reader.env_file("Read", {"file_path": s})), v)
