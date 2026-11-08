"""Casos-limite do server.py (QA): tipos errados, fronteiras das janelas, leitura incremental e arranque.

Reutiliza o setUp/tearDown e os helpers de test_server (dados falsos em pasta temporária, só stdlib).
Os testes marcados com expectedFailure provam bugs reportados no QA.md: quando forem corrigidos,
o unittest acusa «unexpected success» e o decorador tem de sair.
"""
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import server
from tests import test_server as base
from tests import cfg

OFFICE_CFG = cfg.LEGADO

NOW, write, write_json, asst = base.NOW, base.write, base.write_json, base.asst
ROOT = Path(server.__file__).parent
# 26/10/2026 10:00 UTC = 11:00 em Amesterdão (+01:00, já depois da mudança de hora)
FIXED = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc).timestamp()


class EdgeTest(unittest.TestCase):
    setUp, tearDown = base.ServerTest.setUp, base.ServerTest.tearDown
    dev_task, squad_task, dev_log = base.ServerTest.dev_task, base.ServerTest.squad_task, base.ServerTest.dev_log

    def result(self, cost=1.5, turns=2, ms=3000, **extra) -> str:
        return json.dumps({"type": "result", "total_cost_usd": cost, "num_turns": turns, "duration_ms": ms, **extra})

    # --- CA1: pasta de uma squad sessao ---
    def test_squad_dir_is_file_ignored(self):
        write(self.rnd, "x")
        self.assertEqual(server.build_state()["tasks"], [])

    # --- CA4: custo da meta com tipos errados ---
    def test_meta_cost_json_specials_and_bools(self):
        cases = ['NaN', 'Infinity', '-Infinity', '1e400', 'false', 'true', '"1.5"', '-0.01', '{}']
        for lit in cases:
            with self.subTest(lit=lit):
                p = self.rnd / "t" / "meta.json"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('{"id": "t", "status": "a correr", "started_at": "2026-10-25T23:09:11+01:00", '
                             f'"cost_usd": {lit}, "turns": {lit}, "duration_s": {lit}}}')
                [t] = server.build_state()["tasks"]
                self.assertEqual((t["cost_usd"], t["turns"], t["duration_s"]), (None, None, None))
                json.dumps(server.build_state(), allow_nan=False)   # o corpo nunca leva NaN/Infinity
        self.assertIsNone(server._int(2.0))
        self.assertEqual(server._num(0.0), 0.0)

    # --- CA5: claude.log ---
    def test_claude_log_result_line_bigger_than_tail(self):
        # linha result > 64 KB: o fim lido é JSON cortado → null, e nada do texto sai
        tid = self.dev_task()
        self.dev_log(tid, self.result(result="SEGREDO-X" * 10000))
        st = server.build_state()
        self.assertEqual((st["tasks"][0]["cost_usd"], st["tasks"][0]["turns"]), (None, None))
        self.assertNotIn("SEGREDO", json.dumps(st))

    def test_claude_log_reads_only_tail(self):
        tid = self.dev_task()
        self.dev_log(tid, self.result(), junk_kb=300)
        log, calls, real_open = self.tasks / tid / "claude.log", [], Path.open

        class Spy:
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

            def seek(self, n):
                calls.append(("seek", n))
                return self.f.seek(n)

            def read(self, n=-1):
                calls.append(("read", n))
                return self.f.read(n)
        with mock.patch.object(Path, "open", lambda p, *a, **kw: Spy(real_open(p, *a, **kw))):
            self.assertEqual(server._log_result(log), (1.5, 2, 3.0))
        self.assertEqual(calls, [("seek", log.stat().st_size - server.LOG_TAIL), ("read", server.LOG_TAIL)])

    def test_claude_log_cache_same_size_new_mtime(self):
        tid = self.dev_task()
        log = self.tasks / tid / "claude.log"
        self.dev_log(tid, self.result(cost=1.1), age_s=100)
        self.assertEqual(server._log_result(log)[0], 1.1)
        self.dev_log(tid, self.result(cost=2.2), age_s=50)   # mesmo tamanho, outro mtime
        self.assertEqual(server._log_result(log)[0], 2.2)

    def test_claude_log_edge_files(self):
        tid = self.dev_task()
        log = self.tasks / tid / "claude.log"
        for content in (b"", b"\n\n\n", b"\xff\xfe\x00 lixo binario\n", b'{"type": "result"}\n'):
            with self.subTest(content=content):
                log.write_bytes(content)
                os.utime(log, (NOW - len(content), NOW - len(content)))
                self.assertEqual(server._log_result(log), (None, None, None))
        log.unlink()
        log.mkdir()   # claude.log que é pasta: não rebenta
        self.assertEqual(server._log_result(log), (None, None, None))

    def test_claude_log_trailing_blank_lines_ok(self):
        tid = self.dev_task()
        self.dev_log(tid, self.result() + "\n\n")
        self.assertEqual(server._log_result(self.tasks / tid / "claude.log"), (1.5, 2, 3.0))

    def test_claude_log_broken_last_line_uses_earlier_result(self):
        # BUG-1 (QA.md), CA5 alterado na revisão: linhas partidas/não-dict ignoram-se, conta o result anterior
        tid = self.dev_task()
        for last in ('{"type": "result", "total_cost_usd": 9', "[1]", "123"):
            with self.subTest(last=last):
                self.dev_log(tid, self.result(cost=5) + "\n" + last)
                server._log_cache.clear()
                self.assertEqual(server._log_result(self.tasks / tid / "claude.log"), (5, 2, 3.0))

    def test_claude_log_resume_sums_turns_and_duration(self):
        # --resume: um result por run; custo = o da última (acumulado), turnos e duração = soma
        tid = self.dev_task()
        lines = [self.result(cost=1, turns=2, ms=1000, result="SEGREDO-A"), "texto solto", "[1]",
                 json.dumps({"type": "assistant", "num_turns": 50}),
                 self.result(cost=3, turns="x", ms=500, result="SEGREDO-B"),
                 self.result(cost=4.5, turns=5, ms=2500, result="SEGREDO-C")]
        self.dev_log(tid, "\n".join(lines))
        self.assertEqual(server._log_result(self.tasks / tid / "claude.log"), (4.5, 7, 4.0))
        st = server.build_state()
        self.assertEqual((st["tasks"][0]["cost_usd"], st["tasks"][0]["turns"], st["tasks"][0]["duration_s"]), (4.5, 7, 4.0))
        self.assertNotIn("SEGREDO", json.dumps(st))

    def test_claude_log_last_result_without_cost(self):
        # o custo é sempre o da última linha result, mesmo que esta não o traga
        tid = self.dev_task()
        self.dev_log(tid, self.result(cost=2) + "\n" + self.result(cost=None))
        self.assertEqual(server._log_result(self.tasks / tid / "claude.log"), (None, 4, 6.0))

    # --- CA6: fronteiras das janelas ---
    def test_totals_window_boundaries(self):
        day, week = server._windows(FIXED)
        self.assertEqual((day.isoformat(), week.isoformat()), ("2026-10-26T00:00:00+01:00", "2026-10-20T00:00:00+02:00"))
        starts = {"a-dia": day, "b-antes-dia": day - timedelta(seconds=1),
                  "c-semana": week, "d-antes-semana": week - timedelta(seconds=1)}
        for tid, s in starts.items():
            self.squad_task(self.rnd, "rnd", tid, "-x", status="concluída", started_at=s.isoformat(), cost_usd=1)
        # dev grava UTC sem fuso: 23:30Z de 25/10 = 00:30 de 26/10 em Amesterdão (hoje); 22:59:59Z é ontem
        for tid, s in (("e-dev-hoje", "2026-10-25T23:30:00"), ("f-dev-ontem", "2026-10-25T22:59:59")):
            write_json(self.tasks / tid / "meta.json", {"id": tid, "status": "concluída", "started_at": s, "cost_usd": 2})
        t = server.build_state(FIXED)["totals"]
        self.assertEqual(t["rnd"], {"day": {"cost_usd": 1, "tasks": 1, "sem_custo": 0},
                                    "week": {"cost_usd": 3, "tasks": 3, "sem_custo": 0}})
        self.assertEqual(t["dev"], {"day": {"cost_usd": 2, "tasks": 1, "sem_custo": 0},
                                    "week": {"cost_usd": 4, "tasks": 2, "sem_custo": 0}})

    def test_aware_bad_inputs(self):
        self.assertEqual(server._aware("2026-10-26T09:00:00Z").isoformat(), "2026-10-26T09:00:00+00:00")
        for s in (None, "", "ontem", 5, ["x"], "2026-13-01T00:00:00"):
            self.assertIsNone(server._aware(s))

    def test_totals_meta_without_start_not_counted(self):
        for i, s in enumerate((None, "", "ontem")):
            write_json(self.tasks / f"x{i}" / "meta.json", {"id": f"x{i}", "status": "concluída", "started_at": s,
                                                            "cost_usd": 1})
        self.assertEqual(server.build_state()["totals"]["dev"]["week"], {"cost_usd": 0, "tasks": 0, "sem_custo": 0})

    def test_totals_rounding_only_at_the_end(self):
        # BUG-4 (QA.md): soma sem arredondar, arredonda a 2 casas só no fim
        tot, wins = server._empty_totals(), server._windows(FIXED)
        for c in (0.1, 0.2, 0.004, 0.004):   # soma real 0.308 → 0.31 (a arredondar a cada soma dava 0.3)
            server._add_total(tot, "dev", datetime.fromtimestamp(FIXED, timezone.utc), c, wins)
        server._round_totals(tot)
        self.assertEqual(tot["dev"]["day"], {"cost_usd": 0.31, "tasks": 4, "sem_custo": 0})

    def test_non_string_dates_do_not_crash(self):
        # BUG-2 (QA.md): started_at/finished_at que não são texto davam erro no /api/state
        gh = "https://github.com/exemplo-org/joga/pull/1 (open)"
        write_json(self.tasks / "a" / "meta.json", {"id": "a", "status": "concluída", "pr": gh, "finished_at": 123,
                                                    "started_at": "2026-10-01T09:00:00"}, age_s=10 * 86400)
        st = server.build_state()
        self.assertEqual(st["waiting"][0]["finished"], None)
        for v in (123, 1.5, True, ["x"], {"a": 1}):
            self.assertIsNone(server._iso_local(v))

    def test_meta_not_utf8_is_skipped(self):
        # BUG-3 (QA.md): meta.json que não é UTF-8 dava erro no /api/state; agora salta-se
        p = self.sup / "x" / "meta.json"
        p.parent.mkdir(parents=True)
        p.write_bytes(b'{"id": "x", "project": "\xff"}')
        self.assertEqual(server.build_state()["tasks"], [])

    def test_other_files_not_utf8_are_skipped(self):
        # dev meta e meta de subagente com bytes inválidos: saltam-se sem erro
        bad = b'{"x": "\xff"}'
        tid = self.dev_task()
        sub = self.claude / f"-srv-projectos--worktrees-{tid}" / "S0" / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "agent-mau.meta.json").write_bytes(bad)
        (sub / "agent-mau2.jsonl").write_bytes(b'[1]\n{"timestamp": 5}\n\xff\n')
        (sub / "agent-mau2.meta.json").write_text('{"agentType": "x"}')
        (self.tasks / "zz-mau").mkdir()
        (self.tasks / "zz-mau" / "meta.json").write_bytes(bad)
        st = server.build_state()
        self.assertEqual([t["id"] for t in st["tasks"]], [tid])
        self.assertEqual([a["start"] for a in st["tasks"][0]["agents"] if a["role"] == "x"], [None])

    def test_issues_only_short_strings(self):
        tid = self.dev_task()
        meta = self.tasks / tid / "meta.json"
        for v, want in ((["#7", 5, None, {"a": 1}, "x" * 400], ["#7", "x" * 300]),
                        ("texto", []), ({"a": "b"}, []), (7, []), (None, []), ([], [])):
            with self.subTest(v=v):
                write_json(meta, {**json.loads(meta.read_text()), "issues": v})
                self.assertEqual(server.build_state()["tasks"][0]["issues"], want)

    def test_waiting_pr_format_edges(self):
        gh = "https://github.com/exemplo-org/joga/pull/"
        cases = {"a": gh + "1 (open) extra", "b": gh + "2 (open,draft)", "c": gh + "3(open)", "d": gh + "x (open)",
                 "e": " " + gh + "5 (open)", "f": gh + "6 (OPEN)", "g": "http://github.com/a/b/pull/7 (open)",
                 "h": gh + "8 (open, draft)", "i": ["x"], "j": gh + "10 (open)\n"}
        for tid, pr in cases.items():
            write_json(self.tasks / tid / "meta.json", {"id": tid, "status": "concluída", "pr": pr,
                                                        "finished_at": "2026-10-20T10:00:00"})
        self.assertEqual([(w["id"], w["draft"]) for w in server.build_state()["waiting"]], [("h", True)])

    def test_waiting_order_ties_and_missing_finished(self):
        gh = "https://github.com/exemplo-org/joga/pull/"
        for tid, start, fin in (("a", "2026-10-20T08:00:00", "2026-10-20T10:00:00"),
                                ("b", "2026-10-20T09:00:00", "2026-10-20T10:00:00"),
                                ("c", "2026-10-21T09:00:00", None), ("d", None, "2026-10-19T10:00:00")):
            write_json(self.tasks / tid / "meta.json", {"id": tid, "status": "parcial (PR draft)", "pr": gh + "1 (open)",
                                                        "started_at": start, "finished_at": fin})
        w = server.build_state()["waiting"]
        self.assertEqual([x["id"] for x in w], ["b", "a", "d", "c"])
        self.assertEqual(w[-1]["finished"], None)

    # --- CA10/11: consumo ---
    def test_usage_window_boundaries(self):
        f = self.claude / "p" / "S.jsonl"
        write(f, asst("m5h", 5 * 3600) + asst("m5h+1", 5 * 3600 + 1, out=20)
              + asst("m7d", server.WEEK_S, out=30) + asst("m7d+1", server.WEEK_S + 1, out=40))
        server._usage_scan(NOW)
        w = server._usage["windows"]
        self.assertEqual(w["5h"]["claude-opus-5-5"]["output"], 10)
        self.assertEqual(w["7d"]["claude-opus-5-5"]["output"], 60)
        self.assertEqual(sorted(server._msgs), ["m5h", "m5h+1", "m7d"])
        server._usage_scan(NOW + 1)   # um segundo depois, m7d sai da janela e da memória
        self.assertEqual(sorted(server._msgs), ["m5h", "m5h+1"])
        self.assertEqual(server._usage["windows"]["5h"], {})

    def test_usage_file_mtime_boundary(self):
        write(self.claude / "p" / "velho.jsonl", asst("a", 60), age_s=server.WEEK_S + 1)
        write(self.claude / "p" / "limite.jsonl", asst("b", 60), age_s=server.WEEK_S)
        server._usage_scan(NOW)
        self.assertEqual(sorted(server._msgs), ["b"])
        self.assertEqual(list(server._files), [str(self.claude / "p" / "limite.jsonl")])

    def test_usage_bad_lines(self):
        ok = asst("ok", 60)
        lines = [
            asst(5, 60), asst(None, 60), asst("ts-mau", 60).replace('"timestamp": "', '"timestamp": "x'),
            json.dumps({"type": "assistant", "timestamp": "2026-10-26T00:00:00Z", "message": "texto"}) + "\n",
            json.dumps({"type": "assistant", "message": {"id": "sem-usage"}}) + "\n",
            json.dumps({"type": "assistant", "message": {"id": "usage-lista", "usage": [1]}}) + "\n",
            '{"type": "assistant", "usage": , "x"}\n', "[\"assistant\", \"usage\"]\n", "\n",
            json.dumps({"type": "summary", "text": "\"assistant\" \"usage\""}) + "\n",
            asst("tokens-maus", 60, input_tokens="5", output_tokens=-3, cache_read_input_tokens=True,
                 cache_creation_input_tokens=2.5),
        ]
        write(self.claude / "p" / "S.jsonl", "".join(lines) + ok)
        server._usage_scan(NOW)
        self.assertEqual(sorted(server._msgs), ["ok"])   # tokens-maus: todos inválidos = 0 → ignorada

    def test_usage_naive_timestamp_is_utc(self):
        line = asst("n", 3600).replace(".000Z", "")
        write(self.claude / "p" / "S.jsonl", line)
        server._usage_scan(NOW)
        self.assertEqual(server._msgs["n"][0], NOW - 3600)

    def test_usage_partial_line_only_then_completed(self):
        f = self.claude / "p" / "S.jsonl"
        line = asst("m1", 60)
        write(f, line[:40])
        server._usage_scan(NOW)
        self.assertEqual((server._msgs, server._files[str(f)][1]), ({}, 0))
        with open(f, "a") as fh:
            fh.write(line[40:])
        server._usage_scan(NOW)
        self.assertEqual((list(server._msgs), server._files[str(f)][1]), (["m1"], len(line.encode())))

    def test_usage_rotated_same_size_new_inode(self):
        f = self.claude / "p" / "S.jsonl"
        write(f, asst("m1", 60))
        server._usage_scan(NOW)
        tmp = f.with_name("x.tmp")
        tmp.write_text(asst("m2", 60))   # mesmo tamanho, inode novo
        os.replace(tmp, f)
        self.assertEqual(f.stat().st_size, server._files[str(f)][1])
        server._usage_scan(NOW)
        self.assertEqual(sorted(server._msgs), ["m1", "m2"])

    def test_usage_deleted_file_forgotten_ids_kept(self):
        f = self.claude / "p" / "S.jsonl"
        write(f, asst("m1", 60))
        server._usage_scan(NOW)
        f.unlink()
        server._usage_scan(NOW)
        self.assertEqual((server._files, sorted(server._msgs)), ({}, ["m1"]))

    def test_usage_dedup_max_per_field_across_files(self):
        write(self.claude / "a" / "S.jsonl", asst("m1", 60, out=10, input_tokens=7))
        write(self.claude / "b" / "S" / "subagents" / "agent-1.jsonl", asst("m1", 60, out=30) + asst("m1", 60, out=20))
        server._usage_scan(NOW)
        server._usage_scan(NOW)   # 2.º varrimento sem novidades não volta a somar
        w = server._usage["windows"]["7d"]["claude-opus-5-5"]
        self.assertEqual((w["input"], w["output"], w["cache_read"]), (7, 30, 100))

    def test_usage_ignores_synthetic_and_zero(self):
        z = {k: 0 for k in server.USAGE_SRC}
        write(self.claude / "p" / "S.jsonl", asst("syn", 60, model="<synthetic>") + asst("zero", 60, out=0, **z)
              + asst("ok", 60))
        server._usage_scan(NOW)
        self.assertEqual(sorted(server._msgs), ["ok"])
        self.assertEqual(list(server._usage["windows"]["7d"]), ["claude-opus-5-5"])

    def test_usage_loop_paces_60s(self):
        class Stop(Exception):
            pass
        with mock.patch.object(server, "_usage_scan"), \
                mock.patch.object(server.time, "monotonic", side_effect=[100.0, 100.5, 200.0, 290.0]), \
                mock.patch.object(server.time, "sleep", side_effect=[None, Stop]) as sleep:
            with self.assertRaises(Stop):
                server._usage_loop()
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [59.5, 1])

    def test_state_does_not_wait_for_running_scan(self):
        write(self.claude / "p" / "S.jsonl", asst("m1", 60))
        started, release = threading.Event(), threading.Event()
        real = server._read_usage

        def slow(*a):
            started.set()
            release.wait(10)
            return real(*a)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        server._cache["at"] = 0.0
        try:
            with mock.patch.object(server, "_read_usage", slow):
                scan = threading.Thread(target=server._usage_scan, args=(NOW,))
                scan.start()
                self.assertTrue(started.wait(5))
                url = f"http://127.0.0.1:{httpd.server_address[1]}/api/state"
                with urllib.request.urlopen(url, timeout=3) as r:
                    self.assertEqual(json.loads(r.read())["usage"], {"status": "a calcular"})
                release.set()
                scan.join(5)
        finally:
            release.set()
            httpd.shutdown()
            httpd.server_close()
            server._cache["at"] = 0.0
        self.assertEqual(server._usage["status"], "ok")

    # --- CA16: sem fuso na imagem ---
    def test_no_tzdata_falls_back_to_utc(self):
        env = {**os.environ, "PYTHONTZPATH": "/nao/existe", "TASKS_DIR": self.tmp.name + "/x",
               "CLAUDE_PROJECTS_DIR": self.tmp.name + "/x", "SQUADS_DIR": self.tmp.name + "/x",
               "OFFICE_CONFIG": self.tmp.name + "/x/office.yaml"}
        code = "import server, json; s = server.build_state(); print(server.LOCAL_TZ, json.dumps(sorted(s)))"
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("UTC "), out.stdout)


if __name__ == "__main__":
    unittest.main()
