"""v2: caminho dev, actividade ao vivo, presas/mortas, alertas e PRs/CI (AC-3/4, 7..14, 16..19, 21, 24..26)."""
import json
import os
import re
import unittest
from unittest import mock

import server
from tests import fake
from tests import test_server as base
from tests import cfg

OFFICE_CFG = cfg.LEGADO

NOW, write, write_json, iso = base.NOW, base.write, base.write_json, base.iso
WT = "/srv/projectos/.worktrees/"


class LiveBase(unittest.TestCase):
    def setUp(self):
        base.ServerTest.setUp(self)
        self.reset()

    def tearDown(self):
        self.reset()
        base.ServerTest.tearDown(self)

    def reset(self):
        server._live.clear()
        server._seen.clear()
        server._feed.clear()

    def task(self, tid="T1", lines="", age=0, status="a correr", meta_age=60, **extra):
        # tarefa dev com worktree (pasta de sessões derivada dele) e uma sessão principal S0
        write_json(self.tasks / tid / "meta.json", {
            "id": tid, "project": "joga", "mode": "goal", "status": status, "branch": "feat/x",
            "started_at": "2026-10-26T09:00:00", "worktree": WT + tid, **extra}, age_s=meta_age)
        return self.session(tid, "S0", lines, age)

    def pdir(self, tid="T1"):
        return self.claude / re.sub(r"[^A-Za-z0-9]", "-", WT + tid)

    def session(self, tid, stem, lines, age=0):
        return write(self.pdir(tid) / f"{stem}.jsonl", lines, age_s=age)

    def sub(self, tid, aid, lines, age=0, finished=False, role="backend-engineer"):
        d = self.pdir(tid) / "S0" / "subagents"
        write_json(d / f"agent-{aid}.meta.json", {"agentType": role, "description": "x", "finished": finished})
        return write(d / f"agent-{aid}.jsonl", lines, age_s=age)

    def append(self, path, lines, age=0):
        with open(path, "a") as f:
            f.write(lines)
        os.utime(path, (NOW - age, NOW - age))

    def get(self, tid="T1"):
        return next(t for t in server.build_state()["tasks"] if t["id"] == tid)


class DevDirTest(LiveBase):
    def test_dev_dir_from_worktree(self):
        # AC-7
        self.assertEqual(server._dev_dir({"worktree": WT + "T1"}, "T1"),
                         server.CLAUDE / "-srv-projectos--worktrees-T1")

    def test_dev_dir_none(self):
        # AC-8 (Lite): sem worktree, worktree fora de /.worktrees/, ou de tipo errado → None (sem padrão fixo)
        for m in ({}, {"worktree": "/tmp/T9"}, {"worktree": 5}, {"worktree": ""}):
            with self.subTest(m=m):
                self.assertIsNone(server._dev_dir(m, "T9"))

    def test_dev_dir_any_root(self):
        # AC-b1: /tmp/xyz/.worktrees/<id> → -tmp-xyz--worktrees-<id>
        self.assertEqual(server._dev_dir({"worktree": "/tmp/xyz/.worktrees/20261106-0900-x"}, "20261106-0900-x"),
                         server.CLAUDE / "-tmp-xyz--worktrees-20261106-0900-x")

    def test_state_uses_worktree_dir(self):
        # worktree noutro projecto (~/projects/…/.worktrees/<id>): os agentes vêm da pasta derivada dele
        wt = "/srv/projectos/outro/.worktrees/T1"
        self.task("T1", fake.tool_use("u1", "Read"), worktree=wt)
        write(self.claude / re.sub(r"[^A-Za-z0-9]", "-", wt) / "S1.jsonl", fake.tool_use("u1", "Grep"))
        self.assertEqual(self.get()["lead"]["act"]["tool"], "Grep")


class ActTest(LiveBase):
    CASES = [("Read", {}, "ler"), ("Grep", {}, "ler"), ("Glob", {}, "ler"), ("Edit", {}, "editar"),
             ("Write", {}, "editar"), ("NotebookEdit", {}, "editar"), ("Bash", {"command": "ls -la"}, "terminal"),
             ("Bash", {"command": "uv run pytest -q"}, "testes"), ("Bash", {"command": "npx vitest run"}, "testes"),
             ("Bash", {"command": "python3 -m unittest"}, "testes"), ("Bash", {"command": "npm test"}, "testes"),
             ("Bash", {"command": "cd a && npm run test"}, "testes"), ("Bash", {"command": "pnpm test"}, "testes"),
             ("Bash", {"command": "echo mypytest"}, "terminal"), ("WebSearch", {}, "web"),
             ("WebFetch", {}, "web"), ("Agent", {}, "delegar"), ("Task", {}, "delegar"),
             ("mcp__github__x", {}, "outra")]

    def test_kinds(self):
        # AC-9/10: só o último tool_use conta
        for name, inp, kind in self.CASES:
            with self.subTest(name=name, inp=inp):
                self.reset()
                self.task(lines=fake.tool_use("u0", "WebSearch", age=9) + fake.tool_use("u1", name, inp, age=5))
                self.assertEqual(self.get()["lead"]["act"],
                                 {"kind": kind, "tool": name, "error": False, "at": iso(5)})

    def test_invalid_tool_name(self):
        for name in ("nome com espaço", "a" * 41, 5, None, "x/y"):
            with self.subTest(name=name):
                self.reset()
                self.task(lines=fake.tool_use("u1", name))
                act = self.get()["lead"]["act"]
                self.assertEqual((act["kind"], act["tool"]), ("outra", "outra"))

    def test_thinking_and_text(self):
        self.task(lines=fake.tool_use("u1", "Read", age=9) + fake.thinking(age=5) + fake.text("olá", age=1))
        self.assertEqual(self.get()["lead"]["act"], {"kind": "pensar", "tool": None, "error": False, "at": iso(5)})

    def test_error_on_last_tool(self):
        # AC-11
        self.task(lines=fake.tool_use("u1", "Bash", {"command": "make"}) + fake.tool_result("u1", True))
        act = self.get()["lead"]["act"]
        self.assertEqual((act["kind"], act["tool"], act["error"]), ("terminal", "Bash", True))

    def test_error_cleared_by_next_tool_and_only_for_last(self):
        path = self.task(lines=fake.tool_use("u1", "Bash") + fake.tool_result("u1", True) + fake.tool_use("u2", "Read"))
        self.assertFalse(self.get()["lead"]["act"]["error"])
        self.append(path, fake.tool_result("u1", True))   # erro de uma ferramenta anterior: não conta
        self.assertEqual(self.get()["lead"]["act"]["kind"], "ler")
        self.assertFalse(self.get()["lead"]["act"]["error"])
        self.append(path, fake.tool_result("u2", False))
        self.assertFalse(self.get()["lead"]["act"]["error"])

    def test_junk_lines_do_not_change_act(self):
        # AC-13
        self.task(lines=fake.tool_use("u1", "Read", age=3) + fake.junk())
        self.assertEqual(self.get()["lead"]["act"], {"kind": "ler", "tool": "Read", "error": False, "at": iso(3)})

    def test_subagents_have_act(self):
        self.task(lines=fake.tool_use("u1", "Agent"))
        self.sub("T1", "a1", fake.tool_use("s1", "Edit"))
        t = self.get()
        self.assertEqual(t["lead"], {"role": "tech-lead", "stuck": False, "act": t["lead"]["act"]})
        self.assertEqual(t["lead"]["act"]["kind"], "delegar")
        self.assertEqual([(a["role"], a["act"]["kind"]) for a in t["agents"]], [("backend-engineer", "editar")])

    def test_not_running_no_act(self):
        self.task(lines=fake.tool_use("u1", "Read"), status="concluída")
        self.sub("T1", "a1", fake.tool_use("s1", "Edit"))
        t = self.get()
        self.assertEqual((t["lead"]["act"], t["agents"][0]["act"], t["agents"][0]["stuck"]), (None, None, False))

    def test_mkt_session_act(self):
        meta = {"id": "M1", "status": "a correr", "started_at": "2026-10-26T07:30:01", "session_id": "S1",
                "claude_dir": "-srv-projectos-marketing", "mode": "diario"}
        write_json(self.mkt / "M1" / "meta.json", meta)
        write(self.claude / meta["claude_dir"] / "S1.jsonl", fake.tool_use("u1", "WebFetch"))
        t = self.get("M1")
        self.assertEqual((t["lead"]["role"], t["lead"]["act"]["kind"]), ("cmo", "web"))

    def test_no_text_in_state(self):
        # AC-3 e AC-12: texto, comando, caminho e resultado nunca saem; o comando só classifica
        s = "SEGREDO-XYZ"
        path = self.task(lines=fake.prompt(s) + fake.text(s) + fake.tool_use("u0", "Read", {"file_path": f"/x/{s}"})
                         + fake.tool_result("u0", False, s))
        self.get()   # 1.ª leitura (sem feed); o resto entra no feed
        self.append(path, fake.tool_use("u1", "Bash", {"command": f"pytest tests/ --token={s}"})
                    + fake.tool_result("u1", True, [{"type": "text", "text": s}])
                    + fake.tool_use("u2", "Bash", {"command": f"gh pr create --title {s}"})
                    + fake.tool_result("u2", False, f"{s}\nhttps://github.com/o/r/pull/3\n"))
        st = server.build_state()
        self.assertEqual([e["kind"] for e in st["feed"]], ["pr_aberto", "terminal", "testes", "testes"])
        self.assertNotIn(s, json.dumps(st))


class IncrementalTest(LiveBase):
    def key(self, path):
        return server._live[str(path)]

    def test_offset_and_no_reread(self):
        # AC-4
        path = self.task(lines=fake.tool_use("u1", "Read"))
        self.get()
        self.assertEqual(self.key(path).off, path.stat().st_size)
        with mock.patch.object(server, "_live_read", side_effect=AssertionError("não devia ler")):
            self.get()
        self.append(path, fake.tool_use("u2", "Edit") + fake.text("x") + fake.tool_use("u3", "Bash")[:-9])
        with mock.patch.object(server, "_live_line", wraps=server._live_line) as ln:
            self.assertEqual(self.get()["lead"]["act"]["tool"], "Edit")
        self.assertEqual(ln.call_count, 1)   # só a linha nova e completa com marcador
        self.append(path, fake.tool_use("u3", "Bash")[-9:])
        self.assertEqual(self.get()["lead"]["act"]["tool"], "Bash")
        self.assertEqual(self.key(path).off, path.stat().st_size)

    def test_rotation_and_truncation_restart(self):
        path = self.task(lines=fake.tool_use("u1", "Read") * 3)
        self.get()
        tmp = path.with_name("novo.tmp")
        tmp.write_text(fake.tool_use("u2", "Edit") * 2)
        os.replace(tmp, path)   # novo inode
        self.assertEqual(self.get()["lead"]["act"]["tool"], "Edit")
        path.write_text(fake.tool_use("u3", "Bash"))   # mesmo inode, mais curto
        self.assertEqual(self.get()["lead"]["act"]["tool"], "Bash")

    def test_first_read_only_tail(self):
        # 600 KB: o pr-link do início fica fora dos últimos 256 KB
        pad = fake.line({"type": "attachment", "x": "a" * 1000}) * 600
        path = self.task(lines=fake.pr_link(1, "https://github.com/o/r/pull/1") + pad + fake.tool_use("u1", "Grep"))
        self.assertGreater(path.stat().st_size, 2 * server.TAIL_FIRST)
        t = self.get()
        self.assertEqual((t["lead"]["act"]["tool"], t["gh"]["pr"]), ("Grep", None))
        self.assertEqual(self.key(path).off, path.stat().st_size)

    def test_forgets_files_of_hidden_tasks(self):
        path = self.task(lines=fake.tool_use("u1", "Read"))
        self.get()
        self.assertIn(str(path), server._live)
        os.remove(self.tasks / "T1" / "meta.json")
        self.assertEqual(server.build_state()["tasks"], [])
        self.assertEqual(server._live, {})


class FeedTest(LiveBase):
    def test_first_read_no_feed_then_30_desc(self):
        # AC-14
        path = self.task(lines=fake.tool_use("u0", "Read", age=200) + fake.tool_use("u1", "Edit", age=199))
        self.assertEqual(server.build_state()["feed"], [])
        self.append(path, "".join(fake.tool_use(f"v{i}", ("Read", "Edit")[i % 2], age=100 - i) for i in range(50)))
        feed = server.build_state()["feed"]
        self.assertEqual(len(feed), 30)
        self.assertEqual([e["at"] for e in feed], [iso(100 - i) for i in range(49, 19, -1)])
        self.assertEqual(feed[0], {"at": iso(51), "squad": "dev", "task": "T1", "role": "tech-lead",
                                   "kind": "editar", "tool": "Edit", "error": False})

    def test_same_kind_is_not_a_change(self):
        path = self.task(lines=fake.tool_use("u0", "Read"))
        self.get()
        self.append(path, fake.tool_use("u1", "Grep") + fake.tool_use("u2", "Glob") + fake.tool_result("u2", True))
        feed = server.build_state()["feed"]
        self.assertEqual([(e["kind"], e["tool"], e["error"]) for e in feed], [("ler", "Glob", True)])

    def test_new_subagent_feed_role(self):
        self.task(lines=fake.tool_use("u0", "Agent"))
        p = self.sub("T1", "a1", "")
        self.get()
        self.append(p, fake.tool_use("s1", "Bash", {"command": "pytest"}))
        [e] = server.build_state()["feed"]
        self.assertEqual((e["role"], e["kind"]), ("backend-engineer", "testes"))


class StuckDeadTest(LiveBase):
    def test_stuck_pending_tool(self):
        # AC-19: Bash pendurado (sem resultado) e transcript parado > 10 min
        self.task(lines=fake.tool_use("u1", "Bash", {"command": "sleep 9999"}), age=11 * 60)
        self.assertTrue(self.get()["lead"]["stuck"])

    def test_not_stuck(self):
        for lines, age in ((fake.tool_use("u1", "Bash") + fake.tool_result("u1"), 11 * 60),
                           (fake.tool_use("u1", "Bash"), 5 * 60),
                           (fake.tool_use("u1", "Agent"), 20 * 60)):   # delegar não conta
            with self.subTest(lines=lines[:60], age=age):
                self.reset()
                self.task(lines=lines, age=age)
                self.assertFalse(self.get()["lead"]["stuck"])

    def test_stuck_subagent_and_finished(self):
        self.task(lines=fake.tool_use("u1", "Agent"))
        self.sub("T1", "a1", fake.tool_use("s1", "Read"), age=11 * 60)
        self.sub("T1", "a2", fake.tool_use("s2", "Read"), age=11 * 60, finished=True, role="architect")
        t = self.get()
        self.assertEqual({a["role"]: a["stuck"] for a in t["agents"]}, {"backend-engineer": True, "architect": False})
        self.assertFalse(t["lead"]["stuck"])

    def beat(self, age, tid="T1"):
        write(self.tasks / tid / "heartbeat", "", age_s=age)

    def test_dead_heartbeat(self):
        # AC-16
        self.task(lines=fake.tool_use("u1", "Read"))
        self.beat(6 * 60)
        st = server.build_state()
        [t] = st["tasks"]
        self.assertEqual((t["status"], t["running"], t["finished"], t["working"], t["lead"]["act"]),
                         ("morreu", False, None, [], None))
        self.assertEqual(st["alerts"], {"count": 1, "items": ["tarefa:T1"]})

    def test_fresh_heartbeat_never_stale(self):
        # AC-17: transcript e meta parados há 40 min, heartbeat de há 1 min → "a correr"
        self.task(lines=fake.tool_use("u1", "Bash"), age=40 * 60, meta_age=40 * 60)
        self.beat(60)
        t = self.get()
        self.assertEqual((t["status"], t["running"], t["lead"]["stuck"]), ("a correr", True, True))

    def test_no_heartbeat_old_rule(self):
        # AC-18
        self.task(lines=fake.tool_use("u1", "Read"), age=40 * 60, meta_age=40 * 60)
        self.assertEqual(self.get()["status"], "parada?")

    def test_heartbeat_ignored_when_finished(self):
        self.task(lines=fake.tool_use("u1", "Read"), status="concluída")
        self.beat(3600)
        self.assertEqual(self.get()["status"], "concluída")

    def test_alerts_only_dead_tasks(self):
        # AC-21 (Lite: sem crons nem sondas, só tarefas mortas)
        self.task()
        self.beat(3600)
        self.assertEqual(server.build_state()["alerts"], {"count": 1, "items": ["tarefa:T1"]})

    def test_alerts_empty(self):
        self.assertEqual(server.build_state()["alerts"], {"count": 0, "items": []})


class GitHubTest(LiveBase):
    URL = "https://github.com/exemplo-org/joga/pull/282"
    FAIL = "build\tpass\t1m\thttps://x/1\ntest\tfail\t2m\thttps://x/2\n"   # gh pr checks sem TTY

    def bash(self, i, cmd, err=False, out="ok", age=0):
        return fake.tool_use(f"g{i}", "Bash", {"command": cmd}, age) + fake.tool_result(f"g{i}", err, out, age)

    def test_events(self):
        # AC-24/25
        cases = [
            (fake.pr_link(282, self.URL, 5), {"pr_url": self.URL, "pr": "aberto", "ci": None, "at": iso(5)}),
            (self.bash(1, "cd apps/api && gh pr create --fill", out=f"Creating…\n{self.URL}\n", age=5),
             {"pr_url": self.URL, "pr": "aberto", "ci": None, "at": iso(5)}),
            (self.bash(1, "gh pr create", err=True, out=self.URL), {"pr_url": None, "pr": None, "ci": None, "at": None}),
            (self.bash(1, "gh pr create", out=self.URL, age=9) + self.bash(2, "gh pr merge 282 --squash", age=5),
             {"pr_url": self.URL, "pr": "integrado", "ci": None, "at": iso(5)}),
            (self.bash(1, "gh pr merge 1", err=True), {"pr_url": None, "pr": None, "ci": None, "at": None}),
            (self.bash(1, "gh pr checks 282 --watch", err=True, age=9) + self.bash(2, "gh run watch 7", age=5),
             {"pr_url": None, "pr": None, "ci": "verde", "at": iso(5)}),
            (self.bash(1, "gh run watch 7", age=9) + self.bash(2, "gh pr checks", err=True, out=self.FAIL, age=5),
             {"pr_url": None, "pr": None, "ci": "vermelho", "at": iso(5)}),
            (fake.pr_link(1, "https://evil.example/o/r/pull/1", 5) + fake.pr_link(2, self.URL + "/files", 5),
             {"pr_url": None, "pr": "aberto", "ci": None, "at": iso(5)}),
            (self.bash(1, "git push -u origin feat/x"), {"pr_url": None, "pr": None, "ci": None, "at": None}),
        ]
        for lines, gh in cases:
            with self.subTest(lines=lines[:120]):
                self.reset()
                self.task(lines=lines)
                self.assertEqual(self.get()["gh"], gh)

    def test_command_position_only(self):
        # o texto "gh pr merge" dentro de grep/echo/heredoc não é um evento; encadeado e noutra linha é
        for cmd, pr in (('grep -n "gh pr merge" README.md', None), ("echo 'usa `gh pr create`'", None),
                        ("cat > x <<'EOF'\ntexto gh pr merge\nEOF", None), ("ls; gh pr merge 1", "integrado"),
                        ("cd a &&\ngh pr merge 1", "integrado"), ("(gh pr merge 1)", "integrado"),
                        ("ls | gh pr merge 1", "integrado")):
            with self.subTest(cmd=cmd):
                self.reset()
                self.task(lines=self.bash(1, cmd))
                self.assertEqual(self.get()["gh"]["pr"], pr)

    def test_events_from_subagent(self):
        self.task(lines=fake.tool_use("u0", "Agent"))
        self.sub("T1", "a1", self.bash(1, "gh pr create", out=self.URL, age=5))
        self.assertEqual(self.get()["gh"]["pr"], "aberto")

    def test_feed_events(self):
        # AC-26: entram no feed; pr-link a seguir ao gh pr create não repete
        path = self.task(lines=fake.tool_use("u0", "Read", age=20))
        self.get()
        self.append(path, self.bash(1, "git -C /x push", age=9) + self.bash(2, "gh pr create", out=self.URL, age=8)
                    + fake.pr_link(282, self.URL, 8) + self.bash(3, "gh pr checks", err=True, out=self.FAIL, age=7))
        feed = [(e["kind"], e["tool"], e["error"]) for e in server.build_state()["feed"] if e["kind"] not in
                ("terminal",)]
        self.assertEqual(feed, [("ci_vermelho", "Bash", True), ("pr_aberto", "Bash", False), ("push", "Bash", False)])

    def test_ended_task_reads_main_session(self):
        # terminada há ≤ 3 dias: 1.ª leitura do fim da sessão principal; > 3 dias: nada
        self.task(lines=fake.pr_link(282, self.URL, 5), status="concluída", meta_age=86400)
        self.assertEqual(self.get()["gh"]["pr"], "aberto")
        self.reset()
        self.task(lines=fake.pr_link(282, self.URL, 5), status="concluída", meta_age=4 * 86400)
        t = self.get()
        self.assertEqual((t["gh"]["pr"], t["agents"]), (None, []))
        self.assertEqual(server._live, {})


if __name__ == "__main__":
    unittest.main()
