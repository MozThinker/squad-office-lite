"""QA da barra nas notificações e do aviso por fase (SPEC office-push-barra-fases): casos que faltavam.

CA16 fim do body por fase (FASE_FEITA do tech lead), CA14 notificadas ilegível só semeia, CA10+CA17 ficheiro antigo
não liga as fases, CA17 filtros no ciclo, CA18 silêncio com crítico no mesmo ciclo.
"""
from __future__ import annotations

import json
import unittest

import progresso
import push
from tests import test_push as tp
from tests.test_push import DONE, FCM, NOW, SEM_CRYPTO, estado, tarefa, ts
from tests.test_push import a_correr, goal
from tests import cfg

OFFICE_CFG = cfg.LEGADO

TID = "20261105-1010-suporte-caixa"


class FaseFeitaTest(unittest.TestCase):
    def test_ca16_titulo_e_body_de_cada_fase(self):
        # título com o nome visível; body termina com FASE_FEITA (concordância de género; CODE = «em curso»)
        evs = push.eventos(estado(tasks=[a_correr(n=5, project="Loja")]), NOW, DONE)
        e = {ev["fase"]: ev["payload"] for ev in evs if ev["categoria"] == "fases"}
        self.assertEqual({f: (p["title"], p["body"]) for f, p in e.items()}, {
            "SPEC": ("📍 Dev · Loja: SPEC", "▰▰▰▰▰ Loja · suporte-caixa · SPEC feita"),
            "PLAN": ("📍 Dev · Loja: PLAN", "▰▰▰▰▰ Loja · suporte-caixa · PLAN feito"),
            "CODE": ("📍 Dev · Loja: código", "▰▰▰▰▰ Loja · suporte-caixa · código em curso"),
            "QA": ("📍 Dev · Loja: QA", "▰▰▰▰▰ Loja · suporte-caixa · QA feito"),
            "PR": ("📍 Dev · Loja: PR", "▰▰▰▰▰ Loja · suporte-caixa · PR aberto"),
        })
        for p in e.values():
            self.assertEqual((p["tag"], p["url"], p["critico"], p["renotify"]),
                             (f"fase:{TID}", f"./#{TID}", False, False))

    def test_ca16_projecto_vazio_e_longo(self):
        [p] = [ev["payload"] for ev in push.eventos(estado(tasks=[a_correr(n=1, project="")]), NOW, DONE)]
        self.assertEqual((p["title"], p["body"]), ("📍 Dev · geral: SPEC", "▰▱▱▱▱ geral · suporte-caixa · SPEC feita"))
        [p] = [ev["payload"] for ev in push.eventos(estado(tasks=[a_correr(n=1, project="p" * 60)]), NOW, DONE)]
        self.assertEqual(p["title"], f"📍 Dev · {'p' * 40}: SPEC")

    def test_ca1_fase_saltada_nao_conta(self):
        # PR feito sem QA: barra ▰▰▰▱▰ e o fim falhado aponta a 1.ª fase não feita (QA)
        t = tarefa(TID, status="erro", phases={"SPEC": True, "PLAN": True, "QA": False},
                   agents=[{"role": "backend-engineer"}], gh={"pr_url": None, "pr": "aberto"})
        [ev] = push.eventos(estado(tasks=[t]), NOW, DONE)
        self.assertEqual(ev["payload"]["body"], "▰▰▰▱▰ loja · suporte-caixa · falhou no QA")


@unittest.skipUnless(push.ec, SEM_CRYPTO)
class FasesCicloQaTest(unittest.TestCase):
    S = tp.ServicoTest
    setUp, tearDown, subscricao, subscreve, payloads = S.setUp, S.tearDown, S.subscricao, S.subscreve, S.payloads

    def prefs(self, *fases, evento=True, dev=True) -> dict:
        p = push.prefs_omissao()
        p["eventos"]["fases"] = evento
        p["squads"]["dev"] = dev
        p["fases"] = {f: f in fases for f in progresso.FASES}
        return p

    def test_ca14_notificadas_ilegivel_so_semeia(self):
        self.subscreve(prefs=self.prefs(*progresso.FASES))
        (self.pasta / "notificadas.json").write_text("{lixo")
        self.assertEqual(self.s.ciclo(estado(tasks=[a_correr(n=2)]), NOW), 0)
        chaves = json.loads((self.pasta / "notificadas.json").read_text())["chaves"]
        self.assertEqual(set(chaves), {f"fase:dev:{TID}:SPEC", f"fase:dev:{TID}:PLAN"})
        self.assertEqual(self.s.ciclo(estado(tasks=[a_correr(n=3)]), NOW + 30), 1)   # só a nova (CODE)
        self.assertEqual([p["title"] for p in self.payloads()], ["📍 Dev · loja: código"])

    def test_ca10_ca17_ficheiro_antigo_nao_recebe_fases_mas_recebe_o_resto(self):
        self.s.iniciar()
        p = push.prefs_omissao()
        del p["eventos"]["fases"], p["fases"]
        (self.pasta / "subscricoes.json").write_text(json.dumps(
            {"v": 1, "subscricoes": [{**self.subscricao(), "prefs": p, "criado": "x", "actualizado": None}]}))
        self.s.ciclo(estado(), NOW)
        st = estado(tasks=[a_correr(n=4), goal("20261105-1011-feita", n=5, status="concluída")])
        self.assertEqual(self.s.ciclo(st, NOW + 30), 1)
        self.assertEqual([(p["title"], p["body"]) for p in self.payloads()],
                         [("✅ Dev · loja: goal concluído", "▰▰▰▰▰ loja · feita · PR pronto")])
        self.assertNotIn("ilegível", self.out.getvalue())

    def test_ca17_filtros_no_ciclo(self):
        self.subscreve(f"{FCM}/sim", prefs=self.prefs("QA"))
        self.subscreve(f"{FCM}/evento-off", prefs=self.prefs("QA", evento=False))
        self.subscreve(f"{FCM}/fase-off", prefs=self.prefs("SPEC", "PLAN", "CODE", "PR"))
        self.subscreve(f"{FCM}/dev-off", prefs=self.prefs("QA", dev=False))
        self.s.ciclo(estado(tasks=[a_correr(n=3)]), NOW)
        self.assertEqual(self.s.ciclo(estado(tasks=[a_correr(n=4)]), NOW + 30), 1)
        [(r, _)] = self.abrir.pedidos
        self.assertEqual(r.full_url, f"{FCM}/sim")
        self.assertEqual(self.payloads()[0]["title"], "📍 Dev · loja: QA")

    def test_ca18_silencio_descarta_fase_mas_deixa_passar_o_critico(self):
        p = self.prefs(*progresso.FASES)
        p["silencio"]["ligado"] = True
        self.assertTrue(p["criticos_ignoram_silencio"])
        self.subscreve(prefs=p)
        noite = ts("2026-11-05T23:30:00+00:00")
        self.s.ciclo(estado(), noite)
        self.assertEqual(self.s.ciclo(estado(tasks=[a_correr(n=4)], alertas=["tarefa:morta"]), noite + 30), 1)
        self.assertEqual([p["title"] for p in self.payloads()], ["💀 Dev · loja: tarefa morreu"])
        chaves = json.loads((self.pasta / "notificadas.json").read_text())["chaves"]
        self.assertTrue({f"fase:dev:{TID}:{f}" for f in ("SPEC", "PLAN", "CODE", "QA")} <= set(chaves))
        manha = ts("2026-11-06T08:00:00+00:00")
        self.assertEqual(self.s.ciclo(estado(tasks=[a_correr(n=4)]), manha), 0)   # não chega depois do silêncio
        self.assertEqual(len(self.abrir.pedidos), 1)


if __name__ == "__main__":
    unittest.main()
