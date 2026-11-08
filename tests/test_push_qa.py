"""QA: casos que faltavam nos testes do push.py e do progresso.py (encontrados por mutação).

- mediana com amostra assimétrica e par (a média passava nos testes com 10/20/30/40/50 min);
- sonda que sai e volta notifica de novo; tarefa:/parada:/pr:/apr: não (D4);
- tecto de 20 subscrições com o número literal (os testes usavam push.MAX_SUBS);
- filtros: desligar uma categoria tira só essa (cada evento de D6 na categoria certa);
- silêncio na hora repetida do dia da mudança de hora;
- 404 a meio do ciclo: não se envia mais nada a essa subscrição e as outras recebem.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import progresso
import push
from tests.test_push import AMS, DONE, FCM, NOW, SEM_CRYPTO, Abridor, curta, estado, tarefa, ts
from tests import cfg

OFFICE_CFG = cfg.LEGADO

MIN = 60


class MedianaTest(unittest.TestCase):
    def test_mediana_e_nao_media_com_amostra_assimetrica(self):
        amostra = [(float(i), d * MIN) for i, d in enumerate((10, 10, 10, 10, 100))]
        self.assertEqual(progresso.tipico(amostra), (10 * MIN, 5))
        p = progresso.progresso(5 * MIN, progresso.tipico(amostra)[0], 5, None)
        self.assertEqual((p["pct"], p["faltam_s"], p["atrasada"]), (50, 300, False))

    def test_mediana_com_amostra_par(self):
        amostra = [(float(i), d * MIN) for i, d in enumerate((10, 20, 30, 40, 50, 60))]
        self.assertEqual(progresso.tipico(amostra), (35 * MIN, 6))


class ChavesTest(unittest.TestCase):
    def ev(self, chave):
        return {"chave": chave, "fim_ts": NOW if chave.startswith("fim:") else None}

    def test_nada_reaparece(self):
        outras = ["tarefa:t", "parada:dev:t", "pr:dev:t:3", "apr:marketing/a.md", "fim:dev:t"]
        _, nots = push.seleccionar([self.ev(k) for k in outras], {}, NOW)
        _, nots = push.seleccionar([], nots, NOW + 30)
        enviar, _ = push.seleccionar([self.ev(k) for k in outras], nots, NOW + 60)
        self.assertEqual(enviar, [])


class FiltroPorCategoriaTest(unittest.TestCase):
    def test_desligar_uma_categoria_tira_so_essa(self):
        st = estado(tasks=[tarefa("ok"), tarefa("err", status="erro"),
                           tarefa("st", status="parada?", running=True),
                           tarefa("pr", status="a correr", running=True, pr="https://github.com/o/r/pull/3"),
                           tarefa("morto", status="morreu")],
                    alertas=["tarefa:morto"],
                    aprovacoes=[{"fonte": "marketing", "ficheiro": "a.md"}])
        evs = [{**e, "chave": curta(e["chave"])} for e in push.eventos(st, NOW, DONE)]
        dia = datetime.fromtimestamp(ts("2026-11-05T12:00:00+00:00"), AMS)
        esperado = {"concluida": {"fim:dev:ok"}, "falhada": {"fim:dev:err", "parada:dev:st"},
                    "pr": {"pr:dev:pr:3"}, "fases": {"fase:dev:pr:PR"}, "alertas": {"tarefa:morto"},
                    "aprovacoes": {"apr:marketing/a.md"}}
        tudo = set().union(*esperado.values())
        self.assertEqual({e["chave"] for e in evs}, tudo)
        for cat, chaves in esperado.items():
            p = push.prefs_omissao()
            p["eventos"] = {k: True for k in p["eventos"]}
            p["eventos"][cat] = False
            with self.subTest(categoria=cat):
                self.assertEqual({e["chave"] for e in evs if push.quer(e, p, dia)}, tudo - chaves)

    def test_squad_desligada_tira_os_eventos_dela(self):
        # a tarefa morta (único alerta do Lite) é da squad dela
        st = estado(tasks=[tarefa("d"), tarefa("m", squad="marketing"), tarefa("r", squad="rnd"),
                           tarefa("s", squad="support"), tarefa("x", squad="rnd", status="morreu")],
                    alertas=["tarefa:x"])
        evs = [{**e, "chave": curta(e["chave"])} for e in push.eventos(st, NOW, DONE)]
        dia = datetime.fromtimestamp(ts("2026-11-05T12:00:00+00:00"), AMS)
        for sq in ("dev", "marketing", "rnd", "support"):
            p = push.prefs_omissao()
            p["squads"][sq] = False
            with self.subTest(squad=sq):
                self.assertEqual({e["chave"] for e in evs if not push.quer(e, p, dia)},
                                 {f"fim:{sq}:{sq[0]}"} | ({"tarefa:x"} if sq == "rnd" else set()))


class SilencioMudancaDeHoraTest(unittest.TestCase):
    def test_hora_repetida_e_hora_que_falta(self):
        s = {"ligado": True, "de": "02:00", "ate": "03:00"}
        # 2026-10-25: 02:30 acontece duas vezes (CEST e CET); as duas estão em silêncio, 03:00 CET não
        for utc, dentro in (("2026-10-25T00:30:00+00:00", True), ("2026-10-25T01:30:00+00:00", True),
                            ("2026-10-25T02:00:00+00:00", False)):
            with self.subTest(utc=utc):
                self.assertEqual(push.em_silencio(s, datetime.fromtimestamp(ts(utc), AMS)), dentro)
        # 2027-03-28: das 02:00 salta para 03:00 CEST (o silêncio 02:00–03:00 não apanha nenhum instante)
        for utc in ("2027-03-28T00:59:00+00:00", "2027-03-28T01:00:00+00:00"):
            with self.subTest(utc=utc):
                self.assertFalse(push.em_silencio(s, datetime.fromtimestamp(ts(utc), AMS)))


@unittest.skipUnless(push.ec, SEM_CRYPTO)
class ServicoQaTest(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.tmp = tempfile.TemporaryDirectory()
        self.pasta = Path(self.tmp.name)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.abrir = Abridor()
        self.s = push.Servico(self.pasta, "https://squad.example", AMS, DONE, abrir=self.abrir)
        self.ua = ec.generate_private_key(ec.SECP256R1())

    def tearDown(self):
        self.tmp.cleanup()

    def sub(self, endpoint):
        return {"token": "t", "subscricao": {"endpoint": endpoint, "keys": {
            "p256dh": push.b64u(push._ponto(self.ua.public_key())), "auth": push.b64u(os.urandom(16))}}}

    def test_vinte_subscricoes_e_a_21a_da_409(self):
        self.s.iniciar()
        for i in range(20):
            self.assertEqual(self.s.subscrever(self.sub(f"{FCM}/{i}"), NOW)[0], 200)
        self.assertEqual(self.s.subscrever(self.sub(f"{FCM}/x"), NOW), (409, {"error": "demasiados dispositivos"}))
        self.assertEqual(self.s.subscrever(self.sub(f"{FCM}/3"), NOW)[0], 200)   # actualizar uma existente passa

    def test_404_a_meio_do_ciclo_para_essa_subscricao(self):
        self.s.iniciar()
        self.s.subscrever(self.sub(f"{FCM}/morta"), NOW)
        self.s.subscrever(self.sub(f"{FCM}/viva"), NOW)
        self.s.ciclo(estado(), NOW)
        self.abrir.codigos = [404, 201]
        st = estado(tasks=[tarefa(f"t{i}") for i in range(3)])
        self.assertEqual(self.s.ciclo(st, NOW), 3)
        urls = [r.full_url for r, _ in self.abrir.pedidos]
        self.assertEqual(urls, [f"{FCM}/morta"] + [f"{FCM}/viva"] * 3)
        subs = json.loads((self.pasta / "subscricoes.json").read_text())["subscricoes"]
        self.assertEqual([s["endpoint"] for s in subs], [f"{FCM}/viva"])


if __name__ == "__main__":
    unittest.main()
