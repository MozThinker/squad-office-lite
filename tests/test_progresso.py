"""Testes da barra de progresso (progresso.py + build_state + Quadro): AC-18, AC-19, AC-20. Só stdlib."""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import progresso
import quadro
import server
from tests import cfg

OFFICE_CFG = cfg.LEGADO

NOW = float(int(time.time()))   # perto do relógio: o corte de 30 dias usa a mtime das metas
MIN = 60


def naive(ts: float) -> str:
    # dev_task.py grava hora UTC sem fuso
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


class FuncoesTest(unittest.TestCase):
    def test_duracao(self):
        self.assertEqual(progresso.duracao(100.0, 1900.0, 5), 1800.0)
        self.assertEqual(progresso.duracao(100.0, None, 600), 600.0)       # sem fim: duration_s
        self.assertEqual(progresso.duracao(None, None, 600), 600.0)
        self.assertIsNone(progresso.duracao(100.0, 100.0, 600))            # ≤ 0 não cai para duration_s
        self.assertIsNone(progresso.duracao(200.0, 100.0, None))
        self.assertIsNone(progresso.duracao(0.0, 86401.0, None))           # > 24 h
        self.assertEqual(progresso.duracao(0.0, 86400.0, None), 86400.0)   # 24 h certas contam
        for mau in (None, True, "600", float("nan"), float("inf"), 0, -5):
            with self.subTest(mau=mau):
                self.assertIsNone(progresso.duracao(None, None, mau))

    def test_tipico_minimo_e_mediana(self):
        self.assertEqual(progresso.tipico([(i, d * MIN) for i, d in enumerate((10, 20, 30, 40))]), (None, 4))
        self.assertEqual(progresso.tipico([(i, d * MIN) for i, d in enumerate((50, 10, 40, 20, 30))]), (30 * MIN, 5))
        self.assertEqual(progresso.tipico([]), (None, 0))

    def test_tipico_so_as_30_mais_recentes(self):
        # a mais antiga (ref 0) tem 1000 min; as 30 recentes têm 10 min
        amostra = [(0.0, 1000.0 * MIN)] + [(float(i), 10.0 * MIN) for i in range(1, 31)]
        self.assertEqual(progresso.tipico(list(reversed(amostra))), (10 * MIN, 30))

    def test_progresso_com_estimativa(self):
        p = progresso.progresso(15 * MIN, 30 * MIN, 12, None)
        self.assertEqual(p, {"fases": None, "decorrido_s": 900, "tipico_s": 1800, "amostra": 12, "pct": 50,
                             "faltam_s": 900, "atrasada": False})

    def test_progresso_atrasada_teto_e_igualdade(self):
        p = progresso.progresso(31 * MIN, 30 * MIN, 5, None)
        self.assertEqual((p["atrasada"], p["pct"], p["faltam_s"], p["tipico_s"]), (True, None, None, 1800))
        p = progresso.progresso(29.9 * MIN, 30 * MIN, 5, None)   # 99 % → teto 95
        self.assertEqual((p["pct"], p["atrasada"]), (95, False))
        p = progresso.progresso(30 * MIN, 30 * MIN, 5, None)
        self.assertEqual((p["pct"], p["faltam_s"], p["atrasada"]), (95, 0, False))
        p = progresso.progresso(10, 1000, 5, None)
        self.assertEqual(p["pct"], 1)   # arredonda para baixo

    def test_progresso_sem_estimativa(self):
        p = progresso.progresso(25 * MIN + 0.9, None, 4, None)
        self.assertEqual(p, {"fases": None, "decorrido_s": 1500, "tipico_s": None, "amostra": 4, "pct": None,
                             "faltam_s": None, "atrasada": False})
        self.assertEqual(progresso.progresso(-5, None, 0, None)["decorrido_s"], 0)   # relógio adiantado

    def test_fases(self):
        def nomes(fs):
            return [f["estado"] for f in fs]
        self.assertEqual(nomes(progresso.fases({"SPEC": True, "PLAN": True, "QA": False}, ["architect"], False)),
                         ["feito", "feito", "agora", "", ""])
        self.assertEqual(nomes(progresso.fases({}, [], False)), ["agora", "", "", "", ""])
        # o test-engineer não conta para CODE
        self.assertEqual(nomes(progresso.fases({"SPEC": True, "PLAN": True}, ["test-engineer"], False))[2], "agora")
        for papel in ("engineer", "backend-engineer", "frontend-engineer", "devops-engineer", "infra-engineer",
                      "ml-engineer"):
            with self.subTest(papel=papel):
                self.assertEqual(nomes(progresso.fases({"SPEC": True, "PLAN": True}, [papel], False))[2:4],
                                 ["feito", "agora"])
        for papel in ("test-engineer", "engineering-lead", "engineers", "-engineerx", ""):
            with self.subTest(papel=papel):
                self.assertEqual(nomes(progresso.fases({"SPEC": True, "PLAN": True}, [papel], False))[2], "agora")
        fs = progresso.fases({"SPEC": True, "PLAN": True, "QA": True}, ["backend-engineer"], True)
        self.assertEqual(fs, [{"nome": n, "estado": "feito"} for n in ("SPEC", "PLAN", "CODE", "QA", "PR")])


class BuildStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tasks, self.mkt = root / "tasks", root / "squads" / "marketing"
        for d in (self.tasks, self.mkt, root / "claude"):
            d.mkdir(parents=True)
        self.globais = ("TASKS", "CLAUDE", "SQUADS_DIR", "APROVACOES")
        self.old = tuple(getattr(server, g) for g in self.globais)
        server.TASKS, server.CLAUDE, server.SQUADS_DIR, server.APROVACOES = (self.tasks, root / "claude",
                                                                             root / "squads", {})

    def tearDown(self):
        for g, v in zip(self.globais, self.old):
            setattr(server, g, v)
        self.tmp.cleanup()

    def meta(self, tid, minutos=None, status="concluída", mode="goal", squad="dev", fim_atras_h=1, **kw) -> Path:
        # concluída há fim_atras_h horas com a duração dada; a correr: começou há `minutos`
        root = self.tasks if squad == "dev" else self.mkt
        if status == "a correr":
            m = {"id": tid, "project": "joga", "mode": mode, "status": status, "pr": "",
                 "started_at": naive(NOW - minutos * MIN)}
        else:
            fim = NOW - fim_atras_h * 3600
            m = {"id": tid, "project": "joga", "mode": mode, "status": status, "pr": "",
                 "started_at": naive(fim - minutos * MIN), "finished_at": naive(fim)}
        if squad != "dev":
            m["squad"] = squad
        m.update(kw)
        p = root / tid / "meta.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(m))
        return p

    def estado(self):
        return server.build_state(NOW)

    def tarefa(self, state, tid):
        return next(t for t in state["tasks"] if t["id"] == tid)

    def cinco(self, squad="dev", mode="goal", pref="c"):
        for i, d in enumerate((10, 20, 30, 40, 50)):
            self.meta(f"2026{pref}{squad}{i}", d, squad=squad, mode=mode, fim_atras_h=i + 1)

    def test_quatro_concluidas_sem_estimativa(self):
        for i, d in enumerate((10, 20, 30, 40)):
            self.meta(f"c{i}", d, fim_atras_h=i + 1)
        self.meta("viva", 15, status="a correr")
        p = self.tarefa(self.estado(), "viva")["progress"]
        self.assertEqual((p["tipico_s"], p["pct"], p["faltam_s"], p["atrasada"], p["amostra"], p["decorrido_s"]),
                         (None, None, None, False, 4, 900))

    def test_cinco_concluidas_mediana_e_pct(self):
        self.cinco()
        self.meta("viva", 15, status="a correr")
        p = self.tarefa(self.estado(), "viva")["progress"]
        self.assertEqual((p["tipico_s"], p["pct"], p["faltam_s"], p["atrasada"], p["amostra"]),
                         (1800, 50, 900, False, 5))

    def test_atrasada(self):
        self.cinco()
        self.meta("viva", 31, status="a correr")
        p = self.tarefa(self.estado(), "viva")["progress"]
        self.assertEqual((p["atrasada"], p["pct"], p["faltam_s"], p["decorrido_s"]), (True, None, None, 31 * MIN))

    def test_nao_mistura_squads_modos_nem_conta_o_que_nao_deve(self):
        self.cinco(squad="marketing", mode="goal")   # outra squad
        self.cinco(squad="dev", mode="task")         # outro modo
        self.meta("erro1", 30, status="erro")
        self.meta("parcial1", 30, status="parcial (PR draft)")   # DONE_STATUS: conta
        self.meta("dry1", 30, mode="dry-run")
        self.meta("longa", 25 * 60)                               # > 24 h
        antiga = self.meta("antiga", 30, fim_atras_h=24 * 60)     # 60 dias: conta (amostra antes do corte de 30 dias)
        os.utime(antiga, (NOW - 60 * 86400,) * 2)
        self.meta("viva", 15, status="a correr")
        st = self.estado()
        self.assertEqual(self.tarefa(st, "viva")["progress"]["amostra"], 2)
        self.assertNotIn("antiga", [t["id"] for t in st["tasks"]])
        self.meta("viva-mkt", 15, status="a correr", squad="marketing", mode="goal")
        p = self.tarefa(self.estado(), "viva-mkt")["progress"]
        self.assertEqual((p["amostra"], p["tipico_s"], p["fases"]), (5, 1800, None))

    def test_duration_s_como_recurso(self):
        for i in range(5):
            p = self.meta(f"sem-fim{i}", 10, duration_s=1200)
            m = json.loads(p.read_text())
            del m["finished_at"]
            p.write_text(json.dumps(m))
        self.meta("viva", 5, status="a correr")
        self.assertEqual(self.tarefa(self.estado(), "viva")["progress"]["tipico_s"], 1200)

    def test_null_fora_de_running_e_fases_so_no_dev_goal(self):
        self.meta("feita", 30)
        self.meta("viva", 5, status="a correr")
        self.meta("viva-task", 5, status="a correr", mode="task")
        (self.tasks / "viva" / "SPEC.md").write_text("x")
        (self.tasks / "viva" / "PLAN.md").write_text("x")
        st = self.estado()
        self.assertIsNone(self.tarefa(st, "feita")["progress"])
        fs = self.tarefa(st, "viva")["progress"]["fases"]
        self.assertEqual([(f["nome"], f["estado"]) for f in fs],
                         [("SPEC", "feito"), ("PLAN", "feito"), ("CODE", "agora"), ("QA", ""), ("PR", "")])
        self.assertIsNone(self.tarefa(st, "viva-task")["progress"]["fases"])

    def test_pr_da_meta_conta_como_fase(self):
        self.meta("viva", 5, status="a correr", pr="https://github.com/exemplo-org/x/pull/3 (open)")
        fs = self.tarefa(self.estado(), "viva")["progress"]["fases"]
        self.assertEqual(fs[4], {"nome": "PR", "estado": "feito"})

    def test_morta_sem_progress(self):
        self.meta("morta", 30, status="a correr")
        hb = self.tasks / "morta" / "heartbeat"
        hb.write_text("")
        os.utime(hb, (NOW - 3600, NOW - 3600))
        t = self.tarefa(self.estado(), "morta")
        self.assertEqual((t["status"], t["running"], t["progress"]), ("morreu", False, None))

    def test_ac20_cartao_do_quadro_traz_o_mesmo_progress(self):
        self.cinco()
        self.meta("20261105-1000-viva", 15, status="a correr")
        self.meta("20261105-1001-mkt", 15, status="a correr", squad="marketing", mode="goal")
        self.meta("20261105-1002-rotina", 15, status="a correr", squad="marketing", mode="diario")
        self.meta("20261105-1003-rotina-feita", 15, squad="marketing", mode="diario", fim_atras_h=0.1)
        st = self.estado()
        cartoes = {c["id"]: c for c in quadro.cartoes_auto(st, {}, timezone.utc)}
        c = cartoes["dev:20261105-1000-viva"]
        self.assertEqual(c["coluna"], "em-curso")
        self.assertEqual(c["progress"], self.tarefa(st, "20261105-1000-viva")["progress"])
        self.assertIsNotNone(c["progress"])
        self.assertEqual(cartoes["marketing:20261105-1001-mkt"]["progress"],
                         self.tarefa(st, "20261105-1001-mkt")["progress"])
        grupo = next(c for c in cartoes.values() if c["id"].startswith("marketing:diario:"))
        self.assertEqual(grupo["progress"], self.tarefa(st, "20261105-1002-rotina")["progress"])
        self.assertIsNone(cartoes["dev:2026cdev0"]["progress"])


if __name__ == "__main__":
    unittest.main()
