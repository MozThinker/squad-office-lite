"""v2: «O meu dia», histórico de 30 dias e retenção de 30 dias (AC-28..30)."""
import json
import unittest
from datetime import datetime, timedelta, timezone

import server
from tests import test_server as base
from tests import cfg

OFFICE_CFG = cfg.LEGADO

NOW, write, write_json = base.NOW, base.write, base.write_json


def utc_naive(dt: datetime) -> str:
    # o dev_task.py grava UTC sem fuso
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


class HistoryTest(unittest.TestCase):
    squad_task = base.ServerTest.squad_task

    def setUp(self):
        base.ServerTest.setUp(self)
        server._live.clear()
        server._feed.clear()
        self.day0, _ = server._windows(NOW)   # 00:00 de hoje em Amesterdão
        self.today = self.day0.date().isoformat()

    def tearDown(self):
        server._live.clear()
        server._feed.clear()
        base.ServerTest.tearDown(self)

    def meta(self, tid, start, status="concluída", cost=None, dur=None, age=0, **extra):
        m = {"id": tid, "project": f"p-{tid}", "status": status, "started_at": start, **extra}
        if cost is not None:
            m["cost_usd"] = cost
        if dur is not None:
            m["duration_s"] = dur
        write_json(self.tasks / tid / "meta.json", m, age_s=age)

    def test_today_and_history(self):
        d0 = self.day0
        self.meta("a", utc_naive(d0 + timedelta(minutes=1)), cost=1.0, dur=1800,
                  finished_at=utc_naive(d0 + timedelta(minutes=30)))
        self.meta("b", utc_naive(d0 + timedelta(minutes=3)), status="parcial (PR draft)", cost=2.5, dur=3600)
        self.meta("c", utc_naive(d0 + timedelta(minutes=2)), status="falhou", cost=0.254, dur=360)
        self.meta("r", utc_naive(d0 + timedelta(minutes=4)), status="a correr")
        # 1 minuto antes da meia-noite de Amesterdão: ontem (em UTC já pode ser o mesmo dia)
        self.meta("ontem", utc_naive(d0 - timedelta(minutes=1)), cost=7, dur=36)
        self.meta("29d", utc_naive(d0 - timedelta(days=29) + timedelta(hours=12)), cost=3, age=29 * 86400)
        self.meta("31d", utc_naive(d0 - timedelta(days=31)), cost=100, age=31 * 86400)
        self.squad_task(self.sup, "support", "s1", "-x", status="concluido", cost_usd=0.5, duration_s=180,
                        started_at=(d0 + timedelta(hours=1)).isoformat())
        st = server.build_state()
        hist = st["history"]
        self.assertEqual(len(hist), 30)
        self.assertEqual(hist[-1]["date"], self.today)
        self.assertEqual(hist[0]["date"], (self.day0.date() - timedelta(days=29)).isoformat())
        self.assertEqual([h["date"] for h in hist], sorted(h["date"] for h in hist))
        self.assertEqual(hist[-1]["dev"], {"cost_usd": 3.75, "done": 2, "hours": 1.6})
        self.assertEqual(hist[-1]["support"], {"cost_usd": 0.5, "done": 1, "hours": 0.1})
        self.assertEqual(hist[-2]["dev"], {"cost_usd": 7, "done": 1, "hours": 0.0})
        self.assertEqual(hist[0]["dev"]["cost_usd"], 3)
        self.assertEqual(hist[-1]["marketing"], {"cost_usd": 0, "done": 0, "hours": 0})
        self.assertEqual(sum(h["dev"]["cost_usd"] for h in hist), 13.75)   # a de 31 dias não conta
        today = st["today"]
        self.assertEqual((today["cost_usd"], today["done"], today["hours"]), (4.25, 3, 1.7))
        self.assertEqual(today["top"], {"id": "b", "project": "p-b", "cost_usd": 2.5})
        self.assertEqual([(t["id"], t["squad"]) for t in today["timeline"]],
                         [("a", "dev"), ("c", "dev"), ("b", "dev"), ("r", "dev"), ("s1", "support")])
        a = today["timeline"][0]
        self.assertEqual(a, {"id": "a", "squad": "dev", "project": "p-a",
                             "start": (d0 + timedelta(minutes=1)).astimezone(timezone.utc).isoformat(),
                             "end": (d0 + timedelta(minutes=30)).astimezone(timezone.utc).isoformat()})
        self.assertIsNone(today["timeline"][3]["end"])   # a correr

    def test_empty(self):
        st = server.build_state()
        self.assertEqual(st["today"], {"cost_usd": 0, "done": 0, "hours": 0, "top": None, "timeline": []})
        self.assertEqual(len(st["history"]), 30)
        self.assertTrue(all(h[sq] == {"cost_usd": 0, "done": 0, "hours": 0} for h in st["history"] for sq in server.SQUADS))

    def test_today_without_costs_has_no_top(self):
        self.meta("a", utc_naive(self.day0 + timedelta(minutes=1)))
        self.assertIsNone(server.build_state()["today"]["top"])

    def test_300_finished_under_1mb(self):
        # AC-30: 300 terminadas (até 30 dias) cabem em < 1 MB; > 3 dias saem sem agentes
        for i in range(300):
            tid = f"20261001-{i:04d}-t"
            age = (i % 29 + 1) * 86400 - 3600
            self.meta(tid, utc_naive(datetime.fromtimestamp(NOW - age, timezone.utc)), cost=1.23, dur=600, age=age,
                      worktree=f"/srv/projectos/.worktrees/{tid}", branch="feat/" + "x" * 40,
                      issues=["#1"], pr="https://github.com/o/r/pull/1 (open)")
            pdir = server._dev_dir({"worktree": f"/srv/projectos/.worktrees/{tid}"}, tid)
            write(pdir / "S0.jsonl", "{}\n", age_s=age)
            write_json(pdir / "S0" / "subagents" / "agent-a.meta.json", {"agentType": "architect", "finished": True})
        st = server.build_state()
        body = json.dumps(st, ensure_ascii=False).encode()
        self.assertEqual(len(st["tasks"]), 300)
        self.assertLess(len(body), 1024 * 1024)
        old = [t for t in st["tasks"] if t["started"] < utc_naive(datetime.fromtimestamp(NOW - 3 * 86400, timezone.utc))]
        self.assertTrue(old)
        self.assertTrue(all(t["agents"] == [] for t in old))
        self.assertTrue(any(t["agents"] for t in st["tasks"]))


if __name__ == "__main__":
    unittest.main()
