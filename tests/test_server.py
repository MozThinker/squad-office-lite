"""Testes do server.py com dados falsos em pasta temporária (só stdlib: python3 -m unittest)."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import server
from tests import cfg

OFFICE_CFG = cfg.LEGADO

NOW = int(time.time())


def write(path: Path, text: str, age_s: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (NOW - age_s, NOW - age_s))
    return path


def write_json(path: Path, obj: dict, age_s: int = 0) -> Path:
    return write(path, json.dumps(obj), age_s)


def iso(age_s: int) -> str:
    return server._iso_ts(NOW - age_s)


def asst(mid: str, age_s: int, model="claude-opus-5-5", out=10, text="olá", **usage) -> str:
    # linha de transcript como a do Claude Code (timestamp em Z)
    ts = datetime.fromtimestamp(NOW - age_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    msg = {"id": mid, "role": "assistant", "content": [{"type": "text", "text": text}],
           "usage": {"input_tokens": 1, "output_tokens": out, "cache_read_input_tokens": 100,
                     "cache_creation_input_tokens": 1000, "service_tier": "standard", **usage}}
    if model is not None:
        msg["model"] = model
    return json.dumps({"type": "assistant", "timestamp": ts, "requestId": "r", "message": msg}) + "\n"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        # squads sessao (config LEGADO): SQUADS_DIR/<squad>/<id>/meta.json
        self.tasks, self.claude, self.squads = root / "tasks", root / "claude", root / "squads"
        self.mkt, self.rnd, self.sup = self.squads / "marketing", self.squads / "rnd", self.squads / "support"
        for d in (self.tasks, self.claude, self.mkt):
            d.mkdir(parents=True)
        self.old = (server.TASKS, server.CLAUDE, server.SQUADS_DIR)
        server.TASKS, server.CLAUDE, server.SQUADS_DIR = self.tasks, self.claude, self.squads
        server._log_cache.clear()
        self.old_usage = server._usage
        server._usage = {"status": "a calcular"}
        server._files.clear()
        server._msgs.clear()

    def tearDown(self):
        server._usage = self.old_usage
        server._files.clear()
        server._msgs.clear()
        server.TASKS, server.CLAUDE, server.SQUADS_DIR = self.old
        server._log_cache.clear()
        self.tmp.cleanup()

    # --- dados falsos ---
    def dev_task(self, tid="20261023-0001-dev"):
        write_json(self.tasks / tid / "meta.json", {
            "id": tid, "project": "joga", "mode": "goal", "status": "a correr", "branch": "feat/x",
            "pr": "", "started_at": "2026-10-23T09:00:00", "auto_resumes": 1, "issues": ["#7"],
            "worktree": f"/srv/projectos/.worktrees/{tid}",
        }, age_s=600)
        write(self.tasks / tid / "SPEC.md", "x")
        write(self.tasks / tid / "PLAN.md", "x")
        pdir = self.claude / f"-srv-projectos--worktrees-{tid}"
        write(pdir / "S0.jsonl", "{}\n", age_s=100)
        sub = pdir / "S0" / "subagents"
        write_json(sub / "agent-a1.meta.json", {"agentType": "architect", "description": "plano", "finished": True})
        write(sub / "agent-a1.jsonl", '{"timestamp": "2026-10-23T09:01:00Z"}\n', age_s=300)
        return tid

    def mkt_task(self, status="a correr", session="S1", claude_dir="-srv-projectos-marketing", **extra):
        meta = {"id": "20261023-0730-mkt-diario", "mode": "diario", "project": "Marketing", "squad": "marketing",
                "status": status, "started_at": "2026-10-23T07:30:01", "pid": 1, "issues": [], **extra}
        if session is not None:
            meta["session_id"] = session
        if claude_dir is not None:
            meta["claude_dir"] = claude_dir
        write_json(self.mkt / meta["id"] / "meta.json", meta, age_s=900)

    def mkt_sessions(self, s1_age=20):
        pdir = self.claude / "-srv-projectos-marketing"
        write(pdir / "S1.jsonl", "{}\n", age_s=s1_age)
        sub = pdir / "S1" / "subagents"
        write_json(sub / "agent-b1.meta.json", {"agentType": "analista", "description": "números"})
        write(sub / "agent-b1.jsonl", '{"timestamp": "2026-10-23T07:31:00Z"}\n', age_s=30)
        write_json(sub / "agent-b2.meta.json", {"agentType": "revisor", "description": "rever", "finished": True})
        write(sub / "agent-b2.jsonl", '{"timestamp": "2026-10-23T07:30:30Z"}\n', age_s=200)
        # outra sessão na mesma pasta: não pode contar
        write(pdir / "S2.jsonl", "{}\n", age_s=1)
        write_json(pdir / "S2" / "subagents" / "agent-x.meta.json", {"agentType": "Explore", "description": "y"})
        write(pdir / "S2" / "subagents" / "agent-x.jsonl", '{"timestamp": "2026-10-23T07:00:00Z"}\n', age_s=2)

    def squad_task(self, root: Path, squad: str, tid: str, cdir: str, session="S9", **extra) -> str:
        # R&D/Support: meta como a do marketing, hora com fuso
        write_json(root / tid / "meta.json", {
            "id": tid, "mode": "estudo", "project": squad, "squad": squad, "status": "a correr",
            "started_at": "2026-10-25T23:09:11+01:00", "pid": 1, "session_id": session, "claude_dir": cdir,
            "issues": [], **extra}, age_s=900)
        return tid

    def state(self):
        return server.build_state()["tasks"]

    # --- (a) dev igual a hoje ---
    def test_dev_task_unchanged(self):
        # NOW do momento da escrita (não do import): com a suite longa, o import fica a mais de ACTIVE_S
        with mock.patch.dict(globals(), NOW=int(time.time())):
            self._dev_task_unchanged()

    def _dev_task_unchanged(self):
        tid = self.dev_task()
        now = time.time()
        tasks = server.build_state(now)["tasks"]
        decorrido = int(now - datetime(2026, 10, 23, 9, tzinfo=timezone.utc).timestamp())
        self.assertEqual(tasks, [{
            "id": tid, "project": "joga", "mode": "goal", "status": "a correr", "running": True,
            "branch": "feat/x", "pr": None, "started": "2026-10-23T09:00:00+00:00", "finished": None,
            "last_activity": iso(100),
            "phases": {"SPEC": True, "PLAN": True, "QA": False}, "auto_resumes": 1, "issues": ["#7"],
            "agents": [{"role": "architect", "desc": "plano", "start": "2026-10-23T09:01:00Z", "end": iso(300),
                        "last": iso(300), "finished": True, "active": False, "act": None, "stuck": False}],
            "working": ["tech-lead"],
            "squad": "dev",
            "cost_usd": None, "turns": None, "duration_s": None,
            "lead": {"role": "tech-lead", "act": None, "stuck": False},
            "gh": {"pr_url": None, "pr": None, "ci": None, "at": None},
            "progress": {"fases": [{"nome": "SPEC", "estado": "feito"}, {"nome": "PLAN", "estado": "feito"},
                                   {"nome": "CODE", "estado": "agora"}, {"nome": "QA", "estado": ""},
                                   {"nome": "PR", "estado": ""}],
                         "decorrido_s": decorrido, "tipico_s": None, "amostra": 0, "pct": None, "faltam_s": None,
                         "atrasada": False},
        }])

    # --- (b) marketing a correr: só a sessão da meta ---
    def test_mkt_running_counts_only_its_session(self):
        self.mkt_sessions()
        self.mkt_task()
        [t] = self.state()
        self.assertEqual([a["role"] for a in t["agents"]], ["revisor", "analista"])
        self.assertEqual(t["working"], ["analista"])
        self.assertEqual(t["last_activity"], iso(20))  # S1, não S2 (age 1)
        self.assertEqual(t["status"], "a correr")
        self.assertTrue(t["running"])
        self.assertIsNone(t["branch"])
        self.assertIsNone(t["pr"])
        self.assertEqual(t["phases"], {"SPEC": False, "PLAN": False, "QA": False})
        self.assertEqual(t["auto_resumes"], 0)
        self.assertEqual(t["squad"], "marketing")
        self.assertEqual((t["project"], t["mode"], t["started"]), ("Marketing", "diario", "2026-10-23T07:30:01+00:00"))

    def test_mkt_new_agent_types_pass_through(self):
        # copy, criativo e social (desde 23/10/2026) chegam como role tal e qual o agentType: a sala acende-os por nome
        self.mkt_sessions()
        sub = self.claude / "-srv-projectos-marketing" / "S1" / "subagents"
        for i, kind in enumerate(("copy", "criativo", "social")):
            write_json(sub / f"agent-n{i}.meta.json", {"agentType": kind, "description": kind})
            write(sub / f"agent-n{i}.jsonl", f'{{"timestamp": "2026-10-23T07:3{2 + i}:00Z"}}\n', age_s=10)
        self.mkt_task()
        [t] = self.state()
        self.assertEqual([a["role"] for a in t["agents"]], ["revisor", "analista", "copy", "criativo", "social"])
        self.assertEqual(sorted(t["working"]), ["analista", "copy", "criativo", "social"])

    def test_mkt_lead_is_cmo_without_active_subagents(self):
        pdir = self.claude / "-srv-projectos-marketing"
        write(pdir / "S1.jsonl", "{}\n", age_s=10)
        self.mkt_task()
        [t] = self.state()
        self.assertEqual((t["agents"], t["working"]), ([], ["cmo"]))

    def test_mkt_gestor_subagent_passes_through(self):
        # o gestor chamado como subagente (agentType "gestor") noutro modo acende-se por nome; o líder continua o CMO
        self.mkt_sessions()
        sub = self.claude / "-srv-projectos-marketing" / "S1" / "subagents"
        write_json(sub / "agent-g.meta.json", {"agentType": "gestor", "description": "pausar anúncio"})
        write(sub / "agent-g.jsonl", '{"timestamp": "2026-10-23T07:35:00Z"}\n', age_s=10)
        self.mkt_task()
        [t] = self.state()
        self.assertEqual(sorted(t["working"]), ["analista", "gestor"])

    def test_mkt_lead_idle_is_not_working(self):
        # CA5: a correr, sem subagentes activos e sem actividade recente (> ACTIVE_S, < STALE_S) → ninguém a trabalhar
        pdir = self.claude / "-srv-projectos-marketing"
        write(pdir / "S1.jsonl", "{}\n", age_s=server.ACTIVE_S + 60)
        self.mkt_task()  # meta com 900 s
        [t] = self.state()
        self.assertEqual((t["status"], t["running"], t["working"]), ("a correr", True, []))

    def test_mkt_failed_recent_is_shown(self):
        # CA6: terminada dentro de RECENT_S continua a aparecer, com o estado da meta
        self.mkt_task(status="falhou", session=None, finished_at="2026-10-23T07:35:00", issues=["sem dados"])
        os.utime(self.mkt / "20261023-0730-mkt-diario" / "meta.json", (NOW - 2 * 86400,) * 2)
        [t] = self.state()
        self.assertEqual((t["status"], t["running"], t["issues"], t["working"]), ("falhou", False, ["sem dados"], []))

    def test_mkt_stale_without_activity(self):
        self.mkt_task(session=None)
        os.utime(self.mkt / "20261023-0730-mkt-diario" / "meta.json", (NOW - 3600, NOW - 3600))
        [t] = self.state()
        self.assertEqual((t["status"], t["working"]), ("parada?", []))

    def test_mkt_invalid_or_missing_session(self):
        self.mkt_sessions()
        for kw in ({"session": "../x"}, {"session": None}, {"claude_dir": None}, {"claude_dir": ".."},
                   {"claude_dir": "a/b"}, {"session": 5}, {"session": "inexistente"}):
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                self.mkt_task(**kw)
                [t] = self.state()
                self.assertEqual(t["agents"], [])
                self.assertEqual(t["last_activity"], iso(900))  # só o mtime da meta

    def test_subagent_meta_wrong_types(self):
        # description/agentType de tipo errado não podem partir o /api/state
        self.mkt_sessions()
        sub = self.claude / "-srv-projectos-marketing" / "S1" / "subagents"
        write_json(sub / "agent-b1.meta.json", {"agentType": 5, "description": 7})
        self.mkt_task()
        [t] = self.state()
        self.assertEqual([(a["role"], a["desc"]) for a in t["agents"]], [("revisor", "rever"), ("general", "")])

    def test_safe(self):
        for s in ("S1", "abc-123_x.y", "-srv-projectos-marketing"):
            self.assertTrue(server._safe(s), s)
        for s in ("", "..", "a..b", "../x", "a/b", "a\\b", None, 5, "a b", "/etc"):
            self.assertFalse(server._safe(s), s)

    # --- (c) a mesma execução concluída ---
    def test_mkt_finished(self):
        self.mkt_sessions()
        sub = self.claude / "-srv-projectos-marketing" / "S1" / "subagents"
        write_json(sub / "agent-b1.meta.json", {"agentType": "analista", "description": "números", "finished": True})
        self.mkt_task(status="concluída", finished_at="2026-10-23T07:40:00")
        [t] = self.state()
        self.assertFalse(t["running"])
        self.assertEqual(t["status"], "concluída")
        self.assertEqual(t["finished"], "2026-10-23T07:40:00+00:00")
        self.assertEqual(t["working"], [])
        self.assertEqual([a["finished"] for a in t["agents"]], [True, True])

    def test_mkt_failed_old_is_hidden(self):
        # v2 (AC-30): terminadas aparecem até 30 dias; com mais de 3 dias saem sem agentes
        self.mkt_sessions()
        self.mkt_task(status="falhou")
        meta = self.mkt / "20261023-0730-mkt-diario" / "meta.json"
        os.utime(meta, (NOW - 4 * 86400,) * 2)
        [t] = self.state()
        self.assertEqual((t["status"], t["agents"], t["last_activity"]), ("falhou", [], iso(4 * 86400)))
        os.utime(meta, (NOW - 31 * 86400,) * 2)
        self.assertEqual(self.state(), [])

    # --- (d) MKT_TASKS_DIR inexistente ---
    def test_mkt_dir_missing(self):
        tid = self.dev_task()
        server.MKT = Path(self.tmp.name) / "nao-existe"
        self.assertEqual([t["id"] for t in self.state()], [tid])

    def test_tasks_dir_missing(self):
        self.mkt_task(session=None)
        server.TASKS = Path(self.tmp.name) / "nao-existe"
        self.assertEqual([t["squad"] for t in self.state()], ["marketing"])

    def test_meta_not_object_is_skipped(self):
        tid = self.dev_task()
        write(self.mkt / "x" / "meta.json", "[1, 2]")
        self.assertEqual([t["id"] for t in self.state()], [tid])

    # --- (e) ordenação sobre a lista junta ---
    def test_order_running_first(self):
        tid = self.dev_task()
        meta = self.tasks / tid / "meta.json"
        m = json.loads(meta.read_text())
        write_json(meta, {**m, "status": "concluída", "started_at": "2026-10-23T09:00:00"}, age_s=600)
        self.mkt_task()
        self.assertEqual([t["squad"] for t in self.state()], ["marketing", "dev"])

    def test_no_message_text(self):
        pdir = self.claude / "-srv-projectos-marketing"
        write(pdir / "S1.jsonl", '{"timestamp": "t", "message": "SEGREDO"}\n', age_s=10)
        self.mkt_task()
        self.assertNotIn("SEGREDO", json.dumps(server.build_state()))

    # --- squads sessao: R&D e Support em SQUADS_DIR/<squad> ---
    def test_squad_dir_missing_ignored(self):
        tid = self.dev_task()
        self.mkt.rmdir()   # pasta de uma squad sessao inexistente: ignorada
        self.assertEqual([t["id"] for t in self.state()], [tid])

    def test_rnd_task_only_its_session_lead_lider_rnd(self):
        pdir = self.claude / "-srv-projectos-rnd"
        write(pdir / "S9.jsonl", "{}\n", age_s=10)
        write(pdir / "S8.jsonl", "{}\n", age_s=1)
        write_json(pdir / "S8" / "subagents" / "agent-v.meta.json", {"agentType": "vigia", "description": "x"})
        write(pdir / "S8" / "subagents" / "agent-v.jsonl", '{"timestamp": "2026-10-25T22:10:00Z"}\n', age_s=2)
        self.squad_task(self.rnd, "rnd", "20261025-230911-rnd-estudo", "-srv-projectos-rnd")
        [t] = self.state()
        self.assertEqual((t["squad"], t["agents"], t["working"], t["last_activity"]), ("rnd", [], ["lider-rnd"], iso(10)))
        self.assertEqual((t["branch"], t["pr"], t["auto_resumes"]), (None, None, 0))

    def test_support_lead_sre_and_subagent(self):
        pdir = self.claude / "-srv-projectos-support"
        write(pdir / "S9.jsonl", "{}\n", age_s=10)
        sub = pdir / "S9" / "subagents"
        write_json(sub / "agent-i.meta.json", {"agentType": "investigador", "description": "logs"})
        write(sub / "agent-i.jsonl", '{"timestamp": "2026-10-26T08:10:00Z"}\n', age_s=5)
        self.squad_task(self.sup, "support", "20261026-0900-sup-tarefa", "-srv-projectos-support",
                        mode="tarefa")
        [t] = self.state()
        self.assertEqual((t["squad"], t["working"]), ("support", ["investigador"]))
        # sem subagentes activos, a sessão principal é o SRE
        write_json(sub / "agent-i.meta.json", {"agentType": "investigador", "description": "logs", "finished": True})
        [t] = self.state()
        self.assertEqual(t["working"], ["sre"])

    def test_started_with_offset_kept(self):
        self.squad_task(self.rnd, "rnd", "20261025-230911-rnd-estudo", "-x", status="concluída",
                        finished_at="2026-10-25T23:40:00+01:00")
        [t] = self.state()
        self.assertEqual((t["started"], t["finished"]), ("2026-10-25T23:09:11+01:00", "2026-10-25T23:40:00+01:00"))
        self.assertEqual(server._iso_local("2026-10-25T23:09:11"), "2026-10-25T23:09:11+00:00")

    # --- custo, turnos e duração ---
    def test_cost_fields_from_meta(self):
        self.squad_task(self.sup, "support", "t1", "-x", cost_usd=1.5, turns=7, duration_s=90)
        [t] = self.state()
        self.assertEqual((t["cost_usd"], t["turns"], t["duration_s"]), (1.5, 7, 90))

    def test_cost_fields_invalid_to_null(self):
        for bad in (-1, "6", True, None, [1], {"x": 1}):
            with self.subTest(bad=bad):
                self.squad_task(self.sup, "support", "t1", "-x", cost_usd=bad, turns=bad, duration_s=bad)
                [t] = self.state()
                self.assertEqual((t["cost_usd"], t["turns"], t["duration_s"]), (None, None, None))
        for v in (float("nan"), float("inf")):
            self.assertIsNone(server._num(v))
        self.assertIsNone(server._int(2.5))
        self.assertEqual((server._num(0), server._int(0)), (0, 0))

    def dev_log(self, tid: str, last_line: str, junk_kb: int = 0, age_s: int = 0):
        junk = ('{"type": "assistant", "x": "' + "a" * 1000 + '"}\n') * junk_kb
        write(self.tasks / tid / "claude.log", junk + last_line + "\n", age_s=age_s)

    def test_dev_cost_from_claude_log_tail(self):
        tid = self.dev_task()
        self.dev_log(tid, json.dumps({"type": "result", "total_cost_usd": 6.04, "num_turns": 5, "duration_ms": 715561,
                                      "result": "SEGREDO-X", "usage": {"input_tokens": 1}}), junk_kb=100)
        self.assertGreater((self.tasks / tid / "claude.log").stat().st_size, server.LOG_TAIL)
        st = server.build_state()
        [t] = st["tasks"]
        self.assertEqual((t["cost_usd"], t["turns"], t["duration_s"]), (6.04, 5, 715.561))
        self.assertNotIn("SEGREDO-X", json.dumps(st))

    def test_claude_log_not_result_or_broken(self):
        tid = self.dev_task()
        for last in ('{"type": "assistant", "total_cost_usd": 1}', '{"type": "result", "total_cost_usd": 1',
                     '{"type": "result", "total_cost_usd": "1", "num_turns": -2, "duration_ms": null}', "", "[1]"):
            with self.subTest(last=last):
                self.dev_log(tid, last)
                [t] = self.state()
                self.assertEqual((t["cost_usd"], t["turns"], t["duration_s"]), (None, None, None))

    def test_meta_cost_wins_over_log(self):
        tid = self.dev_task()
        meta = self.tasks / tid / "meta.json"
        write_json(meta, {**json.loads(meta.read_text()), "cost_usd": 2.0}, age_s=600)
        self.dev_log(tid, json.dumps({"type": "result", "total_cost_usd": 9, "num_turns": 5, "duration_ms": 1000}))
        [t] = self.state()
        self.assertEqual((t["cost_usd"], t["turns"], t["duration_s"]), (2.0, None, None))

    def test_meta_turns_kept_when_cost_from_log(self):
        tid = self.dev_task()
        meta = self.tasks / tid / "meta.json"
        write_json(meta, {**json.loads(meta.read_text()), "turns": 3}, age_s=600)
        self.dev_log(tid, json.dumps({"type": "result", "total_cost_usd": 9, "num_turns": 5, "duration_ms": 1000}))
        [t] = self.state()
        self.assertEqual((t["cost_usd"], t["turns"], t["duration_s"]), (9, 3, 1.0))

    def test_claude_log_only_dev(self):
        self.squad_task(self.sup, "support", "t1", "-x")
        write(self.sup / "t1" / "claude.log", json.dumps({"type": "result", "total_cost_usd": 9}) + "\n")
        [t] = self.state()
        self.assertIsNone(t["cost_usd"])

    def test_claude_log_cache(self):
        tid = self.dev_task()
        line = json.dumps({"type": "result", "total_cost_usd": 1, "num_turns": 1, "duration_ms": 1})
        self.dev_log(tid, line, age_s=50)
        log = self.tasks / tid / "claude.log"
        self.assertEqual(server._log_result(log), (1, 1, 0.001))
        with mock.patch.object(Path, "open", side_effect=AssertionError("não devia abrir")):
            self.assertEqual(server._log_result(log), (1, 1, 0.001))
        self.dev_log(tid, json.dumps({"type": "result", "total_cost_usd": 22, "num_turns": 1, "duration_ms": 1}),
                     age_s=50)
        self.assertEqual(server._log_result(log)[0], 22)
        self.assertEqual(server._log_result(log.with_name("nao-existe")), (None, None, None))

    # --- fuso e janelas ---
    def test_load_tz_fallback(self):
        self.assertIs(server._load_tz("Nao/Existe"), timezone.utc)
        self.assertEqual(str(server._load_tz("Europe/Amsterdam")), "Europe/Amsterdam")

    def test_windows_dst(self):
        # 25/10/2026: a hora muda às 03:00 (+02:00 → +01:00); as 00:00 desse dia e de 19/10 ainda são +02:00
        noon = datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc).timestamp()  # 12:00 em Amesterdão
        day, week = server._windows(noon)
        self.assertEqual(day.isoformat(), "2026-10-25T00:00:00+02:00")
        self.assertEqual(week.isoformat(), "2026-10-19T00:00:00+02:00")
        day, week = server._windows(datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual((day.isoformat(), week.isoformat()), ("2026-10-26T00:00:00+01:00", "2026-10-20T00:00:00+02:00"))

    # --- totais ---
    def test_totals_all_metas(self):
        day, _ = server._windows(NOW)

        def utc(dt):  # o dev_task.py grava UTC sem fuso
            return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        metas = {  # id: (início, custo na meta, idade do mtime)
            "d-hoje": (utc(day + timedelta(minutes=1)), 1.005, 0),
            "d-5dias": (utc(day - timedelta(days=4, hours=-1)), None, 5 * 86400),
            "d-8dias": (utc(day - timedelta(days=8)), 50, 8 * 86400),
            "d-sem-inicio": (None, 50, 0),
        }
        for tid, (start, cost, age) in metas.items():
            write_json(self.tasks / tid / "meta.json", {"id": tid, "status": "concluída", "started_at": start,
                                                        "cost_usd": cost}, age_s=age)
        # 1.005 + 2.004 = 3.009 → 3.01 (arredondar a cada soma dava 1.0 + 2.004 → 3.0)
        self.dev_log("d-5dias", json.dumps({"type": "result", "total_cost_usd": 2.004, "num_turns": 1, "duration_ms": 1}))
        self.squad_task(self.sup, "support", "s1", "-x", started_at=(day + timedelta(hours=1)).isoformat())
        st = server.build_state()
        # v2 (AC-30): terminadas até 30 dias aparecem
        self.assertEqual(sorted(t["id"] for t in st["tasks"]), ["d-5dias", "d-8dias", "d-hoje", "d-sem-inicio", "s1"])
        zero = {"cost_usd": 0, "tasks": 0, "sem_custo": 0}
        self.assertEqual(st["totals"], {
            "dev": {"day": {"cost_usd": 1.0, "tasks": 1, "sem_custo": 0},
                    "week": {"cost_usd": 3.01, "tasks": 2, "sem_custo": 0}},
            "marketing": {"day": zero, "week": zero},
            "rnd": {"day": zero, "week": zero},
            "support": {"day": {"cost_usd": 0, "tasks": 1, "sem_custo": 1},
                        "week": {"cost_usd": 0, "tasks": 1, "sem_custo": 1}},
        })

    # --- à tua espera ---
    def test_waiting(self):
        gh = "https://github.com/exemplo-org/joga/pull/"
        cases = {  # id: (status, pr, fim)
            "w1-open": ("concluída", gh + "1 (open)", "2026-10-20T10:00:00"),
            "w2-draft": ("parcial (PR draft)", gh + "2 (open, draft)", "2026-10-21T10:00:00"),
            "w3-closed": ("concluída", gh + "3 (closed)", "2026-10-22T10:00:00"),
            "w4-merged": ("concluída", gh + "4 (merged)", "2026-10-22T10:00:00"),
            "w5-arq": ("arquivada", gh + "5 (open)", "2026-10-22T10:00:00"),
            "w6-run": ("a correr", gh + "6 (open)", None),
            "w7-outro": ("concluída", "https://evil.example/exemplo-org/joga/pull/7 (open)", "2026-10-22T10:00:00"),
            "w8-sem": ("concluída", None, "2026-10-22T10:00:00"),
            "w9-antiga": ("concluída", gh + "9 (open)", "2026-10-10T10:00:00"),
            "w10-js": ("concluída", "javascript:alert(1)//https://github.com/a/b/pull/1 (open)", "2026-10-22T10:00:00"),
        }
        for tid, (status, pr, fin) in cases.items():
            write_json(self.tasks / tid / "meta.json", {"id": tid, "project": "joga", "status": status, "pr": pr,
                                                        "started_at": "2026-10-01T09:00:00", "finished_at": fin},
                       age_s=10 * 86400)
        # mesmo sítio no marketing: não entra
        self.mkt_task(status="concluída", session=None, pr=gh + "8 (open)")
        self.assertEqual(server.build_state()["waiting"], [
            {"id": "w2-draft", "squad": "dev", "project": "joga", "status": "parcial (PR draft)", "pr_url": gh + "2", "draft": True,
             "finished": "2026-10-21T10:00:00+00:00"},
            {"id": "w1-open", "squad": "dev", "project": "joga", "status": "concluída", "pr_url": gh + "1", "draft": False,
             "finished": "2026-10-20T10:00:00+00:00"},
            {"id": "w9-antiga", "squad": "dev", "project": "joga", "status": "concluída", "pr_url": gh + "9", "draft": False,
             "finished": "2026-10-10T10:00:00+00:00"},
        ])

    # --- tudo vazio (smoke da CI) ---
    def test_empty_everything(self):
        gone = Path(self.tmp.name) / "nao-existe"
        server.TASKS, server.CLAUDE, server.SQUADS_DIR = gone, gone, gone
        st = server.build_state()
        self.assertEqual((st["tasks"], st["waiting"]), ([], []))
        self.assertNotIn("probes", st)
        self.assertNotIn("crons", st)
        self.assertEqual(set(st["totals"]), {"dev", "marketing", "rnd", "support"})

    # --- consumo da conta (thread de fundo) ---
    def u(self, inp=1, out=10, cr=100, cc=1000, n=1):
        return {"input": inp * n, "output": out * n, "cache_read": cr * n, "cache_creation": cc * n}

    def test_usage_counts_dedup(self):
        a = self.claude / "-srv-projectos-rnd"
        write(a / "S1.jsonl", asst("m1", 60) * 3 + asst("m2", 6 * 3600, out=20)
              + json.dumps({"type": "user", "timestamp": "2026-10-26T00:00:00Z", "message": {
                  "id": "u1", "usage": {"output_tokens": 999}}}) + "\n"
              + asst("m3", 120, model=None) + asst("m4", 120, model="mau modelo!") + asst("m5", 8 * 86400))
        # o mesmo id noutro ficheiro (subagente) não conta duas vezes
        write(a / "S1" / "subagents" / "agent-x.jsonl", asst("m1", 60) + asst("m6", 30, model="claude-sonnet-4"))
        write(a / "velho.jsonl", asst("m7", 60), age_s=8 * 86400)
        server._usage_scan(NOW)
        st = server.build_state()["usage"]
        self.assertEqual((st["status"], st["computed_at"]), ("ok", iso(0)))
        self.assertEqual(st["windows"], {
            "5h": {"claude-opus-5-5": self.u(), "?": self.u(n=2), "claude-sonnet-4": self.u()},
            "7d": {"claude-opus-5-5": self.u(out=15, n=2), "?": self.u(n=2), "claude-sonnet-4": self.u()},
        })
        self.assertEqual(sorted(server._msgs), ["m1", "m2", "m3", "m4", "m6"])

    def test_usage_empty_windows(self):
        server._usage_scan(NOW)
        self.assertEqual(server._usage["windows"], {"5h": {}, "7d": {}})

    def test_usage_incremental(self):
        f = self.claude / "p" / "S.jsonl"
        write(f, asst("m1", 60))
        server._usage_scan(NOW)
        with open(f, "a") as fh:
            fh.write(asst("m2", 60) + asst("m3", 60)[:-5])   # m3 a meio de ser escrita
        with mock.patch.object(server, "_add_msg", wraps=server._add_msg) as add:
            server._usage_scan(NOW)
        self.assertEqual(add.call_count, 1)  # só a linha nova e completa
        self.assertEqual(sorted(server._msgs), ["m1", "m2"])
        with open(f, "a") as fh:
            fh.write(asst("m3", 60)[-5:])
        server._usage_scan(NOW)
        self.assertEqual(sorted(server._msgs), ["m1", "m2", "m3"])
        self.assertEqual(server._files[str(f)][1], f.stat().st_size)
        # nada de novo: nem se abre o ficheiro
        with mock.patch.object(Path, "open", side_effect=AssertionError("não devia abrir")):
            server._usage_scan(NOW)
        # truncado (mesmo inode): relê do início
        f.write_text(asst("m4", 60))
        server._usage_scan(NOW)
        self.assertIn("m4", server._msgs)
        # substituído por outro ficheiro (novo inode) maior: relê sem duplicar
        tmp = f.with_name("novo.tmp")
        tmp.write_text(asst("m4", 60) * 3 + asst("m5", 60) + asst("m6", 60))
        os.replace(tmp, f)
        server._usage_scan(NOW)
        self.assertEqual(sorted(server._msgs), ["m1", "m2", "m3", "m4", "m5", "m6"])
        self.assertEqual(server._usage["windows"]["7d"]["claude-opus-5-5"], self.u(n=6))

    def test_usage_purge_old_ids(self):
        f = self.claude / "p" / "S.jsonl"
        write(f, asst("m1", 60))
        server._usage_scan(NOW)
        later = NOW + 8 * 86400
        server._usage_scan(later)
        self.assertEqual((server._msgs, server._files), ({}, {}))
        self.assertEqual(server._usage["windows"], {"5h": {}, "7d": {}})

    def test_usage_unreadable_file_skipped(self):
        write(self.claude / "p" / "S.jsonl", asst("m1", 60) + "{lixo \"assistant\" \"usage\"\n" + asst("m2", 60))
        write(self.claude / "p" / "T.jsonl", asst("m3", 60))
        real_open = Path.open

        def flaky(path, *a, **kw):
            if path.name == "T.jsonl":
                raise PermissionError("x")
            return real_open(path, *a, **kw)
        with mock.patch.object(Path, "open", flaky):
            server._usage_scan(NOW)
        self.assertEqual(sorted(server._msgs), ["m1", "m2"])

    def test_state_usage_before_scan(self):
        self.assertEqual(server.build_state()["usage"], {"status": "a calcular"})

    def test_state_never_scans(self):
        with mock.patch.object(server, "_usage_scan", side_effect=AssertionError("o pedido não varre")):
            self.assertEqual(server.build_state()["usage"], {"status": "a calcular"})

    def test_usage_loop_survives_errors(self):
        class Stop(Exception):
            pass
        with mock.patch.object(server, "_usage_scan", side_effect=RuntimeError("SEGREDO-X")), \
                mock.patch.object(server.time, "sleep", side_effect=Stop) as sleep, \
                mock.patch("builtins.print") as out:
            with self.assertRaises(Stop):
                server._usage_loop()
        self.assertGreaterEqual(sleep.call_args[0][0], 1)
        self.assertNotIn("SEGREDO-X", str(out.call_args))

    # --- segurança: nada de texto por HTTP ---
    def test_no_secrets_anywhere(self):
        tid = self.dev_task()
        meta = self.tasks / tid / "meta.json"
        write_json(meta, {**json.loads(meta.read_text()), "prompt": "SEGREDO-X", "goal": "SEGREDO-X"}, age_s=600)
        self.dev_log(tid, json.dumps({"type": "result", "total_cost_usd": 1, "result": "SEGREDO-X",
                                      "modelUsage": {"SEGREDO-X": 1}}))
        write(self.claude / "-srv-projectos--worktrees-x" / "S.jsonl",
              asst("m1", 60, text="SEGREDO-X") + asst("m2", 60, model="SEGREDO X: texto")
              + json.dumps({"type": "user", "message": {"content": "SEGREDO-X"}}) + "\n")
        self.squad_task(self.sup, "support", "s1", "-x", relatorio="SEGREDO-X")
        server._usage_scan(NOW)
        server._cache["at"] = 0.0
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}/api/state"
            with urllib.request.urlopen(url, timeout=5) as r:
                body = r.read().decode()
        finally:
            httpd.shutdown()
            httpd.server_close()
            server._cache["at"] = 0.0
        st = json.loads(body)
        self.assertEqual(st["usage"]["status"], "ok")
        self.assertEqual(st["waiting"], [])
        self.assertNotIn("SEGREDO-X", body)
