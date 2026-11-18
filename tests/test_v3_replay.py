"""Lite 0.3.0 (ADR-015): GET /api/replay?dia= (CA-40…42, CA-47). Só stdlib; dados falsos em pasta temporária; «hoje»
e «agora» fixos (D) nos testes puros. Os segredos montam-se por concatenação."""
import gzip
import inspect
import json
import os
import re
import shutil
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import replay
import server
from tests import cfg
from tests import fake
from tests import tempo
from tests import test_server as base
from tests.test_live import WT
from tests.test_v3_servidor import HttpBase, extras

OFFICE_CFG = cfg.LEGADO   # fuso Europe/Amsterdam

NOW = base.NOW
S = "SEGREDO-"
D = date(2026, 11, 14)   # o «hoje» dos testes puros
TAREFA = {"id", "squad", "project", "mode", "status", "running", "phases", "ci", "lead", "agents"}


def em(h: int, mi: int = 0, d: date = D) -> int:
    return int(datetime(d.year, d.month, d.day, h, mi, tzinfo=server.LOCAL_TZ).timestamp())


def idade(epoch: float) -> float:
    return NOW - epoch   # fake.ts(age) = NOW - age


def utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def chave(d: dict) -> str:
    return f"{d['squad']}/{d['id']}"


def estado_em(resp: dict, t: int) -> dict:
    # o estadoEm do cliente: o último completo ≤ t e os dif por ordem até t
    qs = resp["quadros"]
    ini = max((i for i, q in enumerate(qs) if q["tipo"] == "completo" and q["t"] <= t), default=None)
    if ini is None:
        return {}
    st = {chave(d): d for d in qs[ini]["tarefas"]}
    for q in qs[ini + 1:]:
        if q["t"] > t:
            break
        for k in q["saiu"]:
            st.pop(k, None)
        for d in q["mudou"]:
            st[chave(d)] = d
    return st


class ReplayBase(unittest.TestCase):
    def setUp(self):
        base.ServerTest.setUp(self)
        self.limpar()

    def tearDown(self):
        self.limpar()
        base.ServerTest.tearDown(self)

    def limpar(self):
        server.REPLAY.cache.clear()
        server.REPLAY.respostas.clear()
        server._live.clear()
        server._seen.clear()
        server._feed.clear()

    def pdir(self, tid):
        return self.claude / re.sub(r"[^A-Za-z0-9]", "-", WT + tid)

    def ficheiro(self, path: Path, texto: str, mtime: float) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(texto)
        os.utime(path, (mtime, mtime))
        return path

    def meta(self, tid, ini, fin=None, status="concluída", root=None, **kw) -> Path:
        m = {"id": tid, "project": "joga", "mode": "goal", "status": status, "branch": "feat/x",
             "started_at": utc(ini), "worktree": WT + tid, **kw}
        if fin is not None:
            m["finished_at"] = utc(fin)
        return self.ficheiro((root or self.tasks) / tid / "meta.json", json.dumps(m), fin or ini)

    def principal(self, tid, linhas, mtime, stem="S0"):
        return self.ficheiro(self.pdir(tid) / f"{stem}.jsonl", linhas, mtime)

    def sub(self, tid, aid, linhas, mtime, role="frontend-engineer", finished=True, stem="S0"):
        d = self.pdir(tid) / stem / "subagents"
        self.ficheiro(d / f"agent-{aid}.meta.json", json.dumps({"agentType": role, "description": "x",
                                                                 "finished": finished}), mtime)
        return self.ficheiro(d / f"agent-{aid}.jsonl", linhas, mtime)

    def dia(self, tid="20261114-1000-x", d=D):
        # worktree das 10:00 às 11:30; frontend-engineer a editar de 2 em 2 min das 10:20 às 10:40; Bash em 1.º
        # plano do líder das 11:00 às 11:10
        self.meta(tid, em(10, 0, d), em(11, 30, d))
        self.ficheiro(self.tasks / tid / "SPEC.md", "x", em(10, 5, d))
        self.ficheiro(self.tasks / tid / "PLAN.md", "x", em(10, 15, d))
        main = (fake.thinking(age=idade(em(10, 0, d))) + fake.tool_use("u1", "Agent", age=idade(em(10, 19, d)))
                + fake.tool_result("u1", age=idade(em(10, 41, d)))
                + fake.tool_use("b1", "Bash", {"command": "make"}, age=idade(em(11, 0, d)))
                + fake.tool_result("b1", age=idade(em(11, 10, d))) + fake.thinking(age=idade(em(11, 20, d))))
        self.principal(tid, main, em(11, 20, d))
        sub = "".join(fake.edit(f"e{m}", "a", "b", age=idade(em(10, m, d)))
                      + fake.edit_result(f"e{m}", age=idade(em(10, m, d))) for m in range(20, 41, 2))
        self.sub(tid, "a1", sub, em(10, 40, d))
        return tid

    def replay(self, d=D, now=None):
        return server.replay_dia(d.isoformat(), em(23, 0, D) if now is None else now)


class ReplayFidelidadeTest(ReplayBase):
    def test_ca41_estado_no_momento(self):
        tid = self.dia()
        r = self.replay()
        k = "dev/" + tid
        t = estado_em(r, em(10, 30))[k]
        self.assertEqual((t["running"], t["status"]), (True, "a correr"))
        [a] = t["agents"]
        self.assertEqual((a["role"], a["active"], a["finished"], a["act"]["kind"], a["act"]["tool"]),
                         ("frontend-engineer", True, False, "editar", "Edit"))
        self.assertIsNone(t["lead"]["act"])   # o líder espera pelo Agent há 11 min (delegar não conta)
        t = estado_em(r, em(11, 5))[k]
        self.assertEqual(t["lead"]["act"], {"kind": "terminal", "tool": "Bash", "error": False,
                                            "desde": server._iso_ts(em(11, 0))})
        self.assertFalse(t["lead"]["stuck"])
        self.assertEqual([(a["finished"], a["active"]) for a in t["agents"]], [(True, False)])
        t = estado_em(r, em(12, 0))[k]
        self.assertEqual((t["running"], t["status"], t["agents"], t["lead"]["act"]), (False, "concluída", [], None))

    def test_ca41_fases_pela_mtime(self):
        tid = self.dia()
        r = self.replay()
        k = "dev/" + tid
        self.assertEqual(estado_em(r, em(10, 1))[k]["phases"], {"SPEC": False, "PLAN": False, "QA": False})
        self.assertEqual(estado_em(r, em(10, 10))[k]["phases"], {"SPEC": True, "PLAN": False, "QA": False})
        self.assertEqual(estado_em(r, em(10, 15))[k]["phases"], {"SPEC": True, "PLAN": True, "QA": False})

    def test_ca41_passa_a_meia_noite(self):
        ontem = D - timedelta(days=1)
        self.meta("20261113-2300-noite", em(23, 0, ontem), em(1, 0))
        self.principal("20261113-2300-noite", fake.thinking(age=idade(em(0, 30))), em(0, 30))
        self.meta("20261113-1000-ontem", em(10, 0, ontem), em(11, 0, ontem))   # só ontem: não entra
        tid = self.dia()
        r = self.replay()
        ids = {d["id"] for q in r["quadros"] if q["tipo"] == "completo" for d in q["tarefas"]}
        self.assertEqual(ids, {"20261113-2300-noite", tid})
        self.assertEqual(r["quadros"][0]["t"], r["inicio"])
        self.assertTrue(estado_em(r, em(0, 30))["dev/20261113-2300-noite"]["running"])
        self.assertFalse(estado_em(r, em(1, 0))["dev/20261113-2300-noite"]["running"])
        self.assertIn("dev/20261113-2300-noite", estado_em(self.replay(ontem), em(23, 59, ontem)))

    def test_ca41_a_correr_viva_ate_agora(self):
        self.meta("20261114-2000-viva", em(20, 0), status="a correr")
        self.principal("20261114-2000-viva", fake.tool_use("u1", "Read", age=idade(em(20, 1))), em(20, 1))
        self.ficheiro(self.tasks / "20261114-2000-viva" / "heartbeat", "", em(22, 59))
        r = self.replay()
        self.assertEqual(r["fim"], em(23, 0))   # hoje: agora
        t = estado_em(r, em(22, 59))["dev/20261114-2000-viva"]
        self.assertEqual((t["running"], t["status"], t["lead"]["act"]), (True, "a correr", None))
        self.assertEqual(estado_em(r, em(20, 2))["dev/20261114-2000-viva"]["lead"]["act"]["kind"], "ler")

    def test_ca41_a_correr_morta_acaba_pelo_dead_s_e_stale_s(self):
        d3 = D - timedelta(days=3)
        for tid, beat in (("20261111-1000-morta", True), ("20261111-1000-parada", False)):
            with self.subTest(tid=tid):
                self.limpar()
                self.meta(tid, em(10, 0, d3), status="a correr")
                self.principal(tid, fake.tool_use("u1", "Read", age=idade(em(10, 20, d3))), em(10, 20, d3))
                if beat:
                    self.ficheiro(self.tasks / tid / "heartbeat", "", em(10, 30, d3))
                self.assertEqual(self.replay()["quadros"], [])   # não aparece nos dias seguintes
                r = self.replay(d3)
                k = "dev/" + tid
                self.assertTrue(estado_em(r, em(10, 49, d3))[k]["running"])   # 10:20 + STALE_S = 10:50
                t = estado_em(r, em(10, 51, d3))[k]
                self.assertEqual((t["running"], t["status"], t["agents"], t["lead"]["act"]),
                                 (False, "parada?", [], None))
                shutil.rmtree(self.tasks / tid)

    def test_ca41_a_correr_morre_a_meio_do_dia(self):
        # heartbeat parado às 21:00: fim efectivo = max(20:01 + STALE_S, 21:00 + DEAD_S) = 21:05
        tid = "20261114-2000-morre"
        self.meta(tid, em(20, 0), status="a correr")
        self.principal(tid, fake.tool_use("u1", "Read", age=idade(em(20, 1))), em(20, 1))
        self.ficheiro(self.tasks / tid / "heartbeat", "", em(21, 0))
        r = self.replay()
        self.assertTrue(estado_em(r, em(21, 4))["dev/" + tid]["running"])
        self.assertFalse(estado_em(r, em(21, 6))["dev/" + tid]["running"])
        with server.REPLAY.lock:
            [tk] = server._replay_tarefas(r["inicio"], r["fim"], em(23, 0))
        self.assertEqual(tk["fin"], em(21, 5))

    def test_ca41_preso_terminal_e_ci(self):
        tid = "20261114-1400-preso"
        self.meta(tid, em(14), em(16))
        main = (fake.tool_use("w1", "Bash", {"command": "gh run watch 5"}, age=idade(em(14, 1)))
                + fake.tool_result("w1", is_error=True, age=idade(em(14, 2)))
                + fake.tool_use("g1", "Grep", age=idade(em(14, 3))))
        self.principal(tid, main, em(14, 3))
        tid2 = "20261114-1400-term"
        self.meta(tid2, em(14), em(16))
        self.principal(tid2, fake.tool_use("b1", "Bash", {"command": "make"}, age=idade(em(14, 3))), em(14, 3))
        r = self.replay()
        k, k2 = "dev/" + tid, "dev/" + tid2
        self.assertEqual(estado_em(r, em(14, 1))[k]["ci"], None)
        self.assertEqual(estado_em(r, em(14, 2))[k]["ci"], "vermelho")
        self.assertFalse(estado_em(r, em(14, 13))[k]["lead"]["stuck"])   # 10 min certos ainda não
        self.assertTrue(estado_em(r, em(14, 14))[k]["lead"]["stuck"])
        self.assertFalse(estado_em(r, em(14, 27))[k2]["lead"]["stuck"])   # terminal em 1.º plano: só aos 25 min
        self.assertTrue(estado_em(r, em(14, 29))[k2]["lead"]["stuck"])
        self.assertEqual(estado_em(r, em(14, 29))[k2]["lead"]["act"]["desde"], server._iso_ts(em(14, 3)))

    def test_ca41_squad_sessao_so_a_sessao_da_meta(self):
        sid, cdir = fake.SID, "-srv-projectos-marketing"
        self.meta("mkt-1", em(9), em(9, 30), root=self.squads / "marketing", session_id=sid, claude_dir=cdir,
                  mode="diario")
        cd = self.claude / cdir
        self.ficheiro(cd / f"{sid}.jsonl", fake.tool_use("u1", "WebSearch", age=idade(em(9, 5))), em(9, 5))
        self.ficheiro(cd / "outra.jsonl", fake.tool_use("u1", "Bash", age=idade(em(9, 5))), em(9, 5))
        self.ficheiro(cd / sid / "subagents" / "agent-c.meta.json", json.dumps({"agentType": "analista"}), em(9, 6))
        self.ficheiro(cd / sid / "subagents" / "agent-c.jsonl", fake.tool_use("s", "Read", age=idade(em(9, 6))),
                      em(9, 6))
        t = estado_em(self.replay(), em(9, 6))["marketing/mkt-1"]
        self.assertEqual((t["lead"]["role"], t["lead"]["act"]["kind"], t["mode"]), ("cmo", "web", "diario"))
        self.assertEqual([(a["role"], a["act"]["kind"]) for a in t["agents"]], [("analista", "ler")])
        self.assertEqual(t["phases"], {"SPEC": False, "PLAN": False, "QA": False})

    def test_worktree_sem_pasta_de_sessoes(self):
        self.meta("20261114-0900-sem", em(9), em(10), worktree="/fora/do/padrao")
        [q] = [q for q in self.replay()["quadros"] if q["t"] == em(9)]
        self.assertEqual([(d["id"], d["lead"]["act"], d["agents"]) for d in q["tarefas"]],
                         [("20261114-0900-sem", None, [])])


class ReplayFormaTest(ReplayBase):
    def fixture(self):
        self.dia()
        tid = "20261114-1500-y"
        self.meta(tid, em(15), em(17), status="parcial (PR draft)")
        linhas = "".join(fake.tool_use(f"u{i}", ("Read", "Edit", "Bash")[i % 3], {"command": "ls"},
                                       age=idade(em(15) + 37 * i)) for i in range(120))
        self.principal(tid, linhas, em(15) + 37 * 119)
        self.sub(tid, "b1", "".join(fake.thinking(age=idade(em(15, 30) + 50 * i)) for i in range(30)),
                 em(15, 30) + 50 * 29, role="backend-engineer")
        return self.replay()

    def test_ca40_completos_de_5_em_5_e_no_maximo_1_dif_por_segundo(self):
        r = self.fixture()
        self.assertEqual((r["v"], r["dia"], r["inicio"], r["fim"], r["truncado"]),
                         (1, D.isoformat(), em(0), em(23), False))
        ts = [q["t"] for q in r["quadros"]]
        self.assertEqual(ts, sorted(set(ts)))
        self.assertEqual([q["t"] for q in r["quadros"] if q["tipo"] == "completo"],
                         list(range(em(10), em(23) + 1, 300)))
        for q in r["quadros"]:
            self.assertEqual(set(q), {"t", "tipo", "tarefas"} if q["tipo"] == "completo"
                             else {"t", "tipo", "mudou", "saiu"})

    def test_ca40_estado_em_igual_ao_calculo_directo(self):
        r = self.fixture()
        with server.REPLAY.lock:
            tarefas = server._replay_tarefas(r["inicio"], r["fim"], em(23, 0))
        instantes = set(range(em(10), r["fim"] + 1, 60)) | {q["t"] for q in r["quadros"]}
        instantes |= {t - 1 for t in instantes} | {t + 1 for t in instantes}
        for t in sorted(x for x in instantes if em(10) <= x <= r["fim"]):
            esperado = {chave(d): d for tk in tarefas if (d := server.REPLAY.tarefa_em(tk, t)) is not None}
            self.assertEqual(estado_em(r, t), esperado, t)

    def test_ca40_nenhum_dif_sem_mudanca_e_um_por_segundo(self):
        r = self.fixture()
        difs = [q for q in r["quadros"] if q["tipo"] == "dif"]
        self.assertTrue(difs)
        for q in difs:
            antes = estado_em(r, q["t"] - 1)
            self.assertTrue(q["mudou"] or q["saiu"])
            for d in q["mudou"]:
                self.assertNotEqual(antes.get(chave(d)), d)
        for tid in ("20261114-1200-a", "20261114-1200-b"):
            self.meta(tid, em(12), em(13))
            self.principal(tid, fake.tool_use("u1", "Read", age=idade(em(12, 1) + 7)), em(12, 2))
        [q] = [q for q in self.replay()["quadros"] if q["t"] == em(12, 1) + 7]
        self.assertEqual((q["tipo"], sorted(d["id"] for d in q["mudou"])), ("dif", ["20261114-1200-a",
                                                                                   "20261114-1200-b"]))

    def test_ca40_dia_sem_actividade(self):
        r = self.replay(D - timedelta(days=3))
        self.assertEqual((r["quadros"], r["truncado"], r["fim"] - r["inicio"]), ([], False, 86400))

    def test_ca42_truncado_so_completos(self):
        self.fixture()
        cheio = self.replay()
        self.assertTrue(any(q["tipo"] == "dif" for q in cheio["quadros"]))
        tam = len(replay.compacto(cheio))
        with mock.patch.object(replay, "REPLAY_MAX", tam - 1):
            r = self.replay()
        self.assertTrue(r["truncado"])
        self.assertEqual(r["quadros"], [q for q in cheio["quadros"] if q["tipo"] == "completo"])
        with mock.patch.object(replay, "REPLAY_MAX", tam):
            self.assertFalse(self.replay()["truncado"])

    def test_ca40_validacao_do_dia(self):
        hoje = em(23, 0)
        for dia in ("x", "", None, 5, "2026-13-01", "2026-11-31", "2026-11-1", "２０２６-11-14", "2026-11-14 ",
                    "2026-11-15", "2026-11-07", "2025-11-14", "../../etc"):
            with self.subTest(dia=dia):
                self.assertIsNone(server.replay_dia(dia, hoje))
        for k in range(7):
            with self.subTest(k=k):
                self.assertIsNotNone(server.replay_dia((D - timedelta(days=k)).isoformat(), hoje))

    def test_ca40_dia_de_25_h_no_fuso(self):
        d = date(2026, 10, 25)   # Europe/Amsterdam: o relógio atrasa às 03:00
        r = server.replay_dia(d.isoformat(), em(12, 0, date(2026, 10, 27)))
        self.assertEqual((r["inicio"], r["fim"] - r["inicio"]), (em(0, 0, d), 25 * 3600))


class ReplayExclusoesTest(ReplayBase):
    def test_ca41_dry_run_e_meta_por_symlink(self):
        tid = self.dia()
        self.meta("20261114-1000-ensaio", em(10), em(11), mode="dry-run")
        self.principal("20261114-1000-ensaio", fake.thinking(age=idade(em(10, 5))), em(10, 5))
        fora = Path(self.tmp.name) / "fora"
        self.meta("20261114-1000-fora", em(10), em(11), root=fora)
        (self.tasks / "20261114-1000-fora").symlink_to(fora / "20261114-1000-fora")
        (self.tasks / "20261114-1000-ln").mkdir()
        (self.tasks / "20261114-1000-ln" / "meta.json").symlink_to(fora / "20261114-1000-fora" / "meta.json")
        r = self.replay()
        self.assertEqual({d["id"] for q in r["quadros"] if q["tipo"] == "completo" for d in q["tarefas"]}, {tid})

    def test_ca42_transcript_symlink_ou_fifo_nao_se_le(self):
        tid = "20261114-1000-z"
        self.meta(tid, em(10), em(11))
        alvo = self.ficheiro(Path(self.tmp.name) / "fora.jsonl", fake.tool_use("u", "Read", age=idade(em(10, 5))),
                             em(10, 5))
        self.pdir(tid).mkdir(parents=True)
        (self.pdir(tid) / "S0.jsonl").symlink_to(alvo)
        os.mkfifo(self.pdir(tid) / "S1.jsonl")
        os.utime(self.pdir(tid) / "S1.jsonl", (em(10, 6), em(10, 6)))
        fora = Path(self.tmp.name) / "subs"   # pasta de subagentes por symlink para fora da pasta do Claude
        self.ficheiro(fora / "subagents" / "agent-q.jsonl", fake.tool_use("q", "Read", age=idade(em(10, 7))),
                      em(10, 7))
        self.ficheiro(fora / "subagents" / "agent-q.meta.json", json.dumps({"agentType": "x"}), em(10, 7))
        self.principal(tid, fake.thinking(age=idade(em(10, 1))), em(10, 1), stem="S2")
        (self.pdir(tid) / "S2").symlink_to(fora)
        t = estado_em(self.replay(), em(10, 8))["dev/" + tid]
        self.assertEqual((t["lead"]["act"], t["agents"]), (None, []))
        self.assertEqual(set(server.REPLAY.cache), {str(self.pdir(tid) / "S2.jsonl")})

    def test_ca42_allowlist_exacta(self):
        self.dia()
        r = self.replay()
        self.assertEqual(set(r), {"v", "dia", "inicio", "fim", "truncado", "quadros"})
        vistos = set()
        for q in r["quadros"]:
            for d in q.get("tarefas", []) + q.get("mudou", []):
                self.assertEqual(set(d), TAREFA)
                self.assertEqual(set(d["lead"]), {"role", "act", "stuck"})
                self.assertEqual(set(d["phases"]), {"SPEC", "PLAN", "QA"})
                for a in d["agents"]:
                    self.assertEqual(set(a), {"role", "active", "finished", "stuck", "act"})
                for act in [d["lead"]["act"]] + [a["act"] for a in d["agents"]]:
                    if act is not None:
                        self.assertEqual(set(act), {"kind", "tool", "error", "desde"})
                        vistos.add(act["kind"])
        self.assertEqual(vistos, {"editar", "terminal", "pensar", "delegar"})

    def test_feed_do_ao_vivo_intacto(self):
        # o replay segue PRs e CI com o mesmo _live_line, mas nunca escreve no feed nem no _live
        tid = "20261114-1000-pr"
        self.meta(tid, em(10), em(11))
        self.principal(tid, fake.pr_link(7, "https://github.com/exemplo-org/app/pull/7", age=idade(em(10, 2)))
                       + fake.tool_use("w", "Bash", {"command": "gh run watch"}, age=idade(em(10, 3)))
                       + fake.tool_result("w", age=idade(em(10, 4))), em(10, 4))
        r = self.replay()
        self.assertEqual(estado_em(r, em(10, 5))["dev/" + tid]["ci"], "verde")
        self.assertEqual((list(server._feed), server._live), ([], {}))


class ReplayCacheTest(ReplayBase):
    def aberturas(self, fn):
        reais, abertos = os.open, []

        def conta(path, *a, **kw):
            if str(path).endswith(".jsonl"):
                abertos.append(str(path))
            return reais(path, *a, **kw)
        with mock.patch.object(replay.os, "open", side_effect=conta):
            out = fn()
        return out, abertos

    def test_ca42_segundo_pedido_nao_abre_transcripts_e_append_le_so_o_novo(self):
        tid = self.dia()
        r1, ab1 = self.aberturas(self.replay)
        self.assertEqual(len(ab1), 2)
        r2, ab2 = self.aberturas(self.replay)
        self.assertEqual((ab2, r2), ([], r1))
        main = self.pdir(tid) / "S0.jsonl"
        off = server.REPLAY.cache[str(main)].off
        with open(main, "a") as f:
            f.write(fake.tool_use("n1", "Grep", age=idade(em(11, 25))))
        os.utime(main, (em(11, 25), em(11, 25)))
        with mock.patch.object(server.REPLAY, "live_line", wraps=server.REPLAY.live_line) as ll:
            r3, ab3 = self.aberturas(self.replay)
        self.assertEqual((ab3, ll.call_count), ([str(main)], 1))
        self.assertGreater(server.REPLAY.cache[str(main)].off, off)
        self.assertEqual(estado_em(r3, em(11, 26))["dev/" + tid]["lead"]["act"]["tool"], "Grep")

    def test_resposta_guardada_so_sem_mudancas(self):
        tid = self.dia()
        r1 = self.replay()
        self.assertIs(self.replay(), r1)   # nada mudou: a mesma resposta, sem recalcular
        self.meta(tid, em(10, 0), em(11, 0))   # a meta mudou (acabou mais cedo)
        r2 = self.replay()
        self.assertIsNot(r2, r1)
        self.assertFalse(estado_em(r2, em(11, 5))["dev/" + tid]["running"])
        self.assertIsNot(self.replay(D - timedelta(days=1)), r2)

    def test_ficheiro_trocado_recomeca(self):
        tid = self.dia()
        self.replay()
        main = self.pdir(tid) / "S0.jsonl"
        main.unlink()
        self.principal(tid, fake.tool_use("x", "WebFetch", age=idade(em(10, 2))), em(10, 2))
        self.assertEqual(estado_em(self.replay(), em(10, 3))["dev/" + tid]["lead"]["act"]["kind"], "web")

    def test_despejo_ao_fim_de_8_dias(self):
        self.dia()
        self.replay()
        self.assertEqual(len(server.REPLAY.cache), 2)
        d = D + timedelta(days=7)
        server.replay_dia(d.isoformat(), em(23, 0, d))   # a mtime ainda não passou dos 8 dias
        self.assertEqual(len(server.REPLAY.cache), 2)
        d = D + timedelta(days=9)
        server.replay_dia(d.isoformat(), em(23, 0, d))
        self.assertEqual(server.REPLAY.cache, {})

    def test_linhas_estranhas(self):
        tid = "20261114-1000-j"
        self.meta(tid, em(10), em(11))
        linhas = ('"result"' + "[" * 100000 + "\n" + "[1, \"tool_use\"]\n" + "não é json tool_use\n"
                  + fake.tool_use("u", "Read", age=idade(em(10, 3)))
                  + json.dumps({"type": "assistant", "timestamp": "x", "message": {"content": [
                      {"type": "tool_use", "id": "v", "name": "Grep", "input": {}}]}}) + "\n"
                  + fake.junk()
                  + '{"type": "assistant", "timestamp": "' + utc(em(10, 9)) + '"')   # sem \n: fica para depois
        self.principal(tid, linhas, em(10, 9))
        t = estado_em(self.replay(), em(10, 4))["dev/" + tid]
        self.assertEqual(t["lead"]["act"]["tool"], "Grep")   # a linha sem hora fica com a da anterior


class ReplayTectosTest(ReplayBase):
    # tectos de memória/CPU do replay: cache de hoje, LRU, bytes contados ao gerar, subagentes e leitura por ficheiro
    def test_hoje_fim_ao_minuto_e_cache_acerta(self):
        self.dia()
        agora = em(23, 0)
        r1 = self.replay(now=agora)
        self.assertEqual(r1["fim"], agora)
        with mock.patch.object(server.REPLAY, "quadros", wraps=server.REPLAY.quadros) as q:
            r2 = self.replay(now=agora + 5)
            r3 = self.replay(now=agora + 59.9)
        self.assertIs(r2, r1)
        self.assertIs(r3, r1)
        self.assertEqual(q.call_count, 0)
        self.assertLess(tempo.cpu(self.replay, now=agora + 30), 0.05)
        self.assertEqual(self.replay(now=agora + 60)["fim"], agora + 60)   # minuto seguinte: recalcula
        self.assertEqual(replay.janela(D.isoformat(), em(0, 0) + 30), (em(0, 0), em(0, 0)))   # nunca antes do início

    def test_lru_uma_por_dia(self):
        self.dia()
        agora = em(23, 0)
        dias = [(D - timedelta(days=k)).isoformat() for k in range(replay.REPLAY_RESPOSTAS)]
        rs = [server.replay_dia(d, agora) for d in dias]
        self.assertEqual(len(server.REPLAY.respostas), replay.REPLAY_RESPOSTAS)
        for d, r in zip(dias, rs):
            self.assertIs(server.replay_dia(d, agora), r)
        server.replay_dia(dias[0], agora + 120)   # hoje noutro minuto: substitui a de hoje, não acumula
        self.assertEqual(len(server.REPLAY.respostas), replay.REPLAY_RESPOSTAS)
        self.assertEqual(sum(1 for k in server.REPLAY.respostas if k[0] == dias[0]), 1)
        server.REPLAY.respostas.clear()
        with mock.patch.object(replay, "REPLAY_RESPOSTAS", 2):
            r0 = server.replay_dia(dias[0], agora)
            server.replay_dia(dias[1], agora)
            self.assertIs(server.replay_dia(dias[0], agora), r0)   # usado: passa a mais recente
            server.replay_dia(dias[2], agora)                      # sai o dias[1]
            self.assertEqual(len(server.REPLAY.respostas), 2)
            self.assertIs(server.replay_dia(dias[0], agora), r0)
            self.assertEqual(len(server.REPLAY.respostas), 2)

    def test_truncado_para_de_gerar_difs(self):
        tid = "20261114-1000-t"
        self.meta(tid, em(10), em(12))
        linhas = "".join(fake.tool_use(f"u{i}", ("Read", "Edit", "Bash")[i % 3], age=idade(em(10) + 7 * i))
                         for i in range(800))
        self.principal(tid, linhas, em(10) + 7 * 799)
        cheio = self.replay()
        n_difs = sum(q["tipo"] == "dif" for q in cheio["quadros"])
        self.assertGreater(n_difs, 500)
        self.limpar()
        with mock.patch.object(replay, "REPLAY_MAX", 20_000), \
                mock.patch.object(server.REPLAY, "tarefa_em", wraps=server.REPLAY.tarefa_em) as te:
            r = self.replay()
        self.assertTrue(r["truncado"])
        self.assertEqual(r["quadros"], [q for q in cheio["quadros"] if q["tipo"] == "completo"])
        self.assertLess(te.call_count, n_difs // 2)   # parou de avaliar instantes ao passar o tecto
        self.assertEqual(server.REPLAY.quadros([], 0, 10, 0), ([], False))

    def test_no_maximo_12_subagentes_os_de_mais_bytes(self):
        tid = "20261114-1000-s"
        self.meta(tid, em(10), em(12))
        self.principal(tid, fake.thinking(age=idade(em(10))), em(10))
        n = replay.REPLAY_AGENTES + 3
        for k in range(n):   # o a00 tem 1 linha, o a14 tem 15: ficam os 12 maiores (a03…a14)
            linhas = "".join(fake.thinking(age=idade(em(10, 1) + 60 * k + i)) for i in range(k + 1))
            self.sub(tid, f"a{k:02d}", linhas, em(11), role="be" if k >= 3 else "pequeno")
        ontem = em(9, 0, D - timedelta(days=1))
        self.sub(tid, "zz", fake.thinking(age=idade(ontem)) * 50, ontem, role="ontem")   # não tocado no dia
        with server.REPLAY.lock:
            [tk] = server._replay_tarefas(em(0), em(23), em(23))
        self.assertEqual([r for r, _, _ in tk["agentes"]], ["be"] * replay.REPLAY_AGENTES)
        self.assertEqual(len(server.REPLAY.cache), 1 + replay.REPLAY_AGENTES)   # os outros nem se abrem
        agentes = estado_em(self.replay(), em(11, 30))["dev/" + tid]["agents"]
        self.assertLessEqual(len(agentes), replay.REPLAY_AGENTES)

    def test_transcript_grande_le_so_o_fim(self):
        tid = "20261114-1000-g"
        self.meta(tid, em(10), em(12))
        linhas = [fake.tool_use(f"u{i}", "Read", age=idade(em(10) + 10 * i)) for i in range(400)]
        path = self.principal(tid, "".join(linhas), em(10) + 10 * 399)
        tecto = sum(len(x.encode()) for x in linhas[-100:]) + 7   # 100 linhas e 7 bytes da anterior
        with mock.patch.object(replay, "REPLAY_LER_MAX", tecto):
            with server.REPLAY.lock:
                c = server.REPLAY.cronologia(path)
            self.assertEqual((c.off, c.vistos[0], len(c.vistos)), (path.stat().st_size, em(10) + 3000, 100))
            with path.open("a") as f:   # o append pequeno lê-se todo, sem saltar
                f.write(fake.tool_use("n", "Grep", age=idade(em(11, 30))))
            with server.REPLAY.lock:
                c = server.REPLAY.cronologia(path)
            self.assertEqual((len(c.vistos), c.vistos[-1]), (101, em(11, 30)))
        self.limpar()
        with mock.patch.object(replay, "REPLAY_LER_MAX", sum(len(x.encode()) for x in linhas[-50:])):
            with server.REPLAY.lock:   # o tecto cai no início de uma linha: não se perde nenhuma
                self.assertEqual(len(server.REPLAY.cronologia(path).vistos), 50)

    def test_linhas_sem_marca_sem_json_loads(self):
        # só a hora das linhas sem tool_use/tool_result/thinking/pr-link: contam como actividade, sem json.loads
        tid = "20261114-1000-m"
        self.meta(tid, em(10), em(12))
        texto = "".join(fake.text("a resposta " + "x" * 50, age=idade(em(10, 30) + i)) for i in range(20))
        self.principal(tid, fake.tool_use("u", "Read", age=idade(em(10))) + texto
                       + '{"x": {"timestamp": "2026-11-14T09:59:00Z"}, "timestamp": "' + utc(em(10, 40)) + '"}\n'
                       + '["timestamp", "' + utc(em(10, 50)) + '"]\n'
                       + '{"timestamp": 5}\n', em(10, 40))
        with mock.patch.object(replay.json, "loads", wraps=json.loads) as loads:
            with server.REPLAY.lock:
                c = server.REPLAY.cronologia(self.pdir(tid) / "S0.jsonl")
        self.assertEqual(loads.call_count, 1)
        self.assertEqual(list(c.vistos), [em(10)] + [em(10, 30) + i for i in range(20)] + [em(10, 40)])


# --- HTTP, segredos nos 7 dias, não-escrita (CA-40, CA-41, CA-42, CA-47) ---
class ReplayHttpTest(HttpBase):
    pdir, ficheiro = ReplayBase.pdir, ReplayBase.ficheiro

    def setUp(self):
        super().setUp()
        ReplayBase.limpar(self)

    def tearDown(self):
        ReplayBase.limpar(self)
        super().tearDown()

    def hoje(self, k=0) -> str:
        return (datetime.fromtimestamp(time.time(), server.LOCAL_TZ).date() - timedelta(days=k)).isoformat()

    def segredos(self, age):
        return (fake.todo_write("t1", ["completed", "pending"], content=S + "x4", age=age)
                + fake.task_create("c1", subject=S + "x5", age=age)
                + fake.edit("e1", "a", S + "x6", file_path="/x/" + S + "x7/a.py", age=age,
                            git_branch="feat/$(rm -rf)", model="<script>")
                + fake.tool_result("e1", age=age, tool_use_result={
                    "filePath": "/x/a.py", "originalFile": S + "x8",
                    "structuredPatch": [{"oldStart": 1, "lines": ["+" + S + "x9", "-a"]}]})
                + fake.tool_use("z", "Read", age=age, model="<script>", git_branch="feat/$(rm -rf)"))

    def fixture_7_dias(self, por_dia=3, linhas=400, subs=3):
        # tarefas a começar há k dias (a de hoje a correr, para o /api/state seguir os transcripts)
        for k in range(7):
            for j in range(por_dia):
                tid = f"2026111{k}-{j:02d}00-t{k}{j}"
                ini = NOW - k * 86400 - 3 * 3600 - j * 600
                viva = k == 0 and j == 0
                m = {"id": tid, "project": "joga", "mode": "goal", "status": "a correr" if viva else "concluída",
                     "started_at": utc(ini), "worktree": WT + tid, "branch": "feat/x",
                     "titulo": S + "x4-titulo", "task": "# " + S + "x4-titulo\n"}
                if not viva:
                    m["finished_at"] = utc(ini + 2 * 3600)
                fim = NOW - 5 if viva else ini + 2 * 3600
                self.ficheiro(self.tasks / tid / "meta.json", json.dumps(m), fim)
                corpo = "".join(fake.tool_use(f"u{i}", ("Read", "Edit", "Bash", "Grep")[i % 4],
                                              {"command": "echo " + S + "x4-input"}, age=idade(ini + 15 * i))
                                + fake.tool_result(f"u{i}", age=idade(ini + 15 * i + 5)) for i in range(linhas))
                self.ficheiro(self.pdir(tid) / "S0.jsonl", corpo + self.segredos(idade(fim)), fim)
                for a in range(subs):
                    d = self.pdir(tid) / "S0" / "subagents"
                    self.ficheiro(d / f"agent-a{a}.meta.json", json.dumps(
                        {"agentType": "backend-engineer", "description": S + "x4-desc", "finished": not viva}), fim)
                    self.ficheiro(d / f"agent-a{a}.jsonl", "".join(
                        fake.thinking(age=idade(ini + 600 + 20 * i)) for i in range(linhas // 4))
                        + self.segredos(idade(fim)), fim)

    def replay_raw(self, dia, headers=None, extra=""):
        return self.req("GET", f"/api/replay?dia={dia}{extra}", headers=headers)

    def test_ca47_segredos_em_nenhum_corpo_nos_7_dias(self):
        self.fixture_7_dias(por_dia=1, linhas=20, subs=1)
        for path in ("/api/state", "/api/quadro"):
            with self.subTest(path=path):
                body = self.get(path)
                for x in ("x4-input", "x5", "x6", "x7", "x8", "x9"):
                    self.assertNotIn(S + x, body)
                for x in ("rm -rf", "<script>", "structuredPatch", "old_string", "file_path"):
                    self.assertNotIn(x, body)
        st = json.loads(self.get("/api/state"))
        t = next(t for t in st["tasks"] if t["running"])
        self.assertEqual(extras(t["lead"])["tarefas"], {"feitas": 0, "total": 1})   # vale o último mecanismo
        n = 0
        for k in range(7):
            code, raw, _ = self.replay_raw(self.hoje(k))
            self.assertEqual(code, 200)
            body = raw.decode()
            n += body.count('"tipo":"completo"')
            for x in ("SEGREDO", "rm -rf", "<script>", "feat/x", '"tarefas":{', '"tarefas":null', '"linhas"',
                      '"modelo"', '"ramo"', '"titulo"', '"desc"', '"branch"', '"pr"'):
                self.assertTrue(x not in body, (k, x))   # sem assertNotIn: o corpo inteiro no erro é ilegível
        self.assertGreater(n, 0)

    def test_ca40_http_404_no_store_e_gzip(self):
        self.fixture_7_dias(por_dia=1, linhas=10, subs=0)
        amanha = (date.fromisoformat(self.hoje()) + timedelta(days=1)).isoformat()
        for dia in ("x", "2026-02-30", amanha, self.hoje(7), "", "2026-1-01"):
            with self.subTest(dia=dia):
                code, raw, h = self.replay_raw(dia)
                self.assertEqual((code, json.loads(raw)), (404, {"error": "não encontrado"}))
                self.assertEqual(h["Cache-Control"], "no-store")
        self.assertEqual(self.req("GET", "/api/replay")[0], 404)
        code, raw, h = self.replay_raw(self.hoje(6))
        self.assertEqual(code, 200)
        code, raw, h = self.replay_raw(self.hoje())
        self.assertEqual((code, h["Content-Type"], h["Cache-Control"]), (200, "application/json; charset=utf-8",
                                                                         "no-store"))
        self.assertNotIn("Content-Encoding", h)
        self.assertEqual(int(h["Content-Length"]), len(raw))
        d = json.loads(raw)
        self.assertEqual((d["v"], d["dia"]), (1, self.hoje()))
        self.assertNotIn(b": ", raw)   # JSON compacto
        _, raw2, _ = self.replay_raw(self.hoje(), extra="&x=1&dia=2020-01-01&agora=1")
        self.assertEqual(json.loads(raw2)["quadros"], d["quadros"])
        code, gz, h = self.replay_raw(self.hoje(), {"Accept-Encoding": "gzip, deflate"})
        self.assertEqual((code, h["Content-Encoding"], h["Vary"]), (200, "gzip", "Accept-Encoding"))
        self.assertEqual(json.loads(gzip.decompress(gz))["quadros"], d["quadros"])

    def test_ca41_nao_escreve(self):
        self.fixture_7_dias(por_dia=2, linhas=100, subs=2)
        raiz = Path(self.tmp.name)

        def arvore():
            return {str(p): (p.lstat().st_size, p.lstat().st_mtime_ns) for p in raiz.rglob("*")}
        antes = arvore()
        for k in range(7):
            with self.subTest(k=k):
                self.assertEqual(self.replay_raw(self.hoje(k))[0], 200)
        self.assertEqual(arvore(), antes)


class RotasTest(unittest.TestCase):
    def test_ca41_do_post_nao_muda(self):
        # a v3 não acrescentou rotas de escrita; a 0.4.0 acrescenta só a fila dos agentes (ADR-017)
        src = inspect.getsource(server.Handler.do_POST)
        self.assertEqual(set(re.findall(r'path [!=]= "([^"]+)"', src)),
                         {"/api/quadro", "/api/quadro/lancar", "/api/biblioteca/arquivo", "/api/decisao",
                          "/api/agentes/pedido"})
        self.assertIn("path in PUSH_ROTAS", src)
        self.assertEqual(server.PUSH_ROTAS, ("/api/push/subscrever", "/api/push/cancelar", "/api/push/teste"))
        for m in ("do_PUT", "do_DELETE", "do_PATCH"):
            self.assertNotIn(m, vars(server.Handler))
        self.assertNotIn("replay", src)
        self.assertNotIn("entregas", src)

    def test_modulos_sem_server(self):
        # replay.py e entregas.py recebem o que precisam por injecção: não importam o server.py
        for nome in ("replay.py", "entregas.py"):
            with self.subTest(nome=nome):
                txt = (Path(__file__).resolve().parent.parent / nome).read_text(encoding="utf-8")
                self.assertNotRegex(txt, r"(?m)^\s*(?:import|from)\s+server\b")


if __name__ == "__main__":
    unittest.main()
