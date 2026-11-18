"""Desempenho do replay (CA-44): um dia com 20 tarefas e 50 000 linhas de transcript reconstrói-se em ≤ 2 s de CPU a
frio e ≤ 0,2 s com cache. CPU da thread (tests/tempo.py), não o relógio; «agora» fixo no fim do dia."""
import json
import os
import re
import unittest
from datetime import date, datetime, timezone

import server
from tests import cfg
from tests import fake
from tests import tempo
from tests import test_server as base
from tests.test_live import WT

OFFICE_CFG = cfg.LEGADO

D = date(2026, 11, 14)
TAREFAS, LINHAS = 20, 50_000
# a frio: medido 0,82–0,96 s (17/11/2026, 4 núcleos, sozinho e 4 em paralelo; CPU da thread) depois de o replay
# contar os bytes ao gerar (pára os dif ao passar o REPLAY_MAX, sem serializar a resposta inteira outra vez); 2 s ≈ 2×
# o pior medido. Antes media 1,45–2,03 s contra o mesmo limite: um retrocesso para lá volta a falhar
FRIO_S, CACHE_S = 2.0, 0.2


def em(h: int, mi: int = 0) -> int:
    return int(datetime(D.year, D.month, D.day, h, mi, tzinfo=server.LOCAL_TZ).timestamp())


class ReplayDesempenhoTest(unittest.TestCase):
    def setUp(self):
        base.ServerTest.setUp(self)
        server.REPLAY.cache.clear()
        server.REPLAY.respostas.clear()

    def tearDown(self):
        server.REPLAY.cache.clear()
        server.REPLAY.respostas.clear()
        base.ServerTest.tearDown(self)

    def escrever(self, path, texto, mtime):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(texto)
        os.utime(path, (mtime, mtime))

    def dia(self) -> int:
        # 20 tarefas de 3 h a começar de 30 em 30 min; cada uma com a sessão principal e 4 subagentes; ferramentas,
        # resultados e thinking de 4 em 4 s (como uma squad a trabalhar), 2 500 linhas por tarefa
        por_tarefa, n = LINHAS // TAREFAS, 0
        ferramentas = ("Read", "Edit", "Bash", "Grep", "Write")
        for k in range(TAREFAS):
            tid = f"20261114-{k:02d}00-r4"
            ini, fim = em(6) + k * 1800, em(6) + k * 1800 + 3 * 3600
            m = {"id": tid, "project": "joga", "mode": "goal", "status": "concluída", "worktree": WT + tid,
                 "started_at": datetime.fromtimestamp(ini, timezone.utc).isoformat(),
                 "finished_at": datetime.fromtimestamp(fim, timezone.utc).isoformat()}
            self.escrever(self.tasks / tid / "meta.json", json.dumps(m), fim)
            pdir = self.claude / re.sub(r"[^A-Za-z0-9]", "-", WT + tid)
            for f, fase in enumerate(("SPEC", "PLAN", "QA")):
                self.escrever(self.tasks / tid / f"{fase}.md", "x", ini + 600 * (f + 1))
            for s in range(5):   # 0 = principal; 1…4 = subagentes
                linhas, t = [], ini + s * 60
                for i in range(por_tarefa // 5 // 2):
                    uid = f"u{s}-{i}"
                    nome = ferramentas[i % len(ferramentas)]
                    linhas.append(fake.tool_use(uid, nome, {"command": "make test"}, age=base.NOW - t))
                    linhas.append(fake.tool_result(uid, age=base.NOW - t - 2) if i % 7 else fake.thinking(
                        age=base.NOW - t - 2))
                    t += 4
                path = pdir / "S0.jsonl" if s == 0 else pdir / "S0" / "subagents" / f"agent-a{s}.jsonl"
                self.escrever(path, "".join(linhas), t)
                n += len(linhas)
                if s:
                    self.escrever(path.with_name(f"agent-a{s}.meta.json"),
                                  json.dumps({"agentType": "backend-engineer", "finished": True}), t)
        return n

    def test_ca44_frio_e_com_cache(self):
        self.assertEqual(self.dia(), LINHAS)
        agora = em(23, 0)
        res = []
        frio = tempo.cpu(lambda: res.append(server.replay_dia(D.isoformat(), agora)))
        self.assertTrue(res[0]["quadros"])
        self.assertEqual(len(server.REPLAY.cache), TAREFAS * 5)
        quente = tempo.cpu(lambda: res.append(server.replay_dia(D.isoformat(), agora)))
        self.assertEqual(res[1], res[0])
        print(f"\nreplay: {frio:.2f} s a frio, {quente:.3f} s com cache")
        self.assertLessEqual(frio, FRIO_S)
        self.assertLessEqual(quente, CACHE_S)


if __name__ == "__main__":
    unittest.main()
