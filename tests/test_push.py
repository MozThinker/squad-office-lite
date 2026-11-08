"""Testes do push.py (Web Push): funções puras sem dependências; cifra, VAPID e vigia só com cryptography (skip)."""
import contextlib
import io
import json
import os
import stat
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import progresso
import push
from tests import cfg

OFFICE_CFG = cfg.LEGADO

AMS = ZoneInfo("Europe/Amsterdam")
NOW = 1_800_000_000.0
DONE = ("concluída", "parcial (PR draft)", "concluido")
SEM_CRYPTO = "cryptography não instalada (só na imagem Docker)"
FCM = "https://fcm.googleapis.com/fcm/send/abc-DEF_123"

# RFC 8291, Apêndice A: vectores públicos do RFC, não são segredos
RFC_TEXTO = "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"
RFC_AS_PUB = "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"  # gitleaks:allow
RFC_AS_PRIV = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"  # gitleaks:allow
RFC_UA_PUB = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"  # gitleaks:allow
RFC_SAL = "DGv6ra1nlYgDCS1FRnbzlw"  # gitleaks:allow
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"  # gitleaks:allow
RFC_CAB = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"  # gitleaks:allow
           "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8")  # gitleaks:allow
RFC_CIFRADO = "8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEsbI_0LpXMuGvnzQ"  # gitleaks:allow


def ts(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def sub(endpoint=FCM, p256dh=None, auth=None) -> dict:
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh or push.b64u(b"\x04" + b"\x01" * 64),
                                           "auth": auth or push.b64u(b"\x02" * 16)}}


def tarefa(tid="20261105-1000-x", squad="dev", status="concluída", running=False, finished_age=60, **kw) -> dict:
    t = {"id": tid, "squad": squad, "project": "loja", "mode": "goal", "status": status, "running": running,
         "finished": None if running else datetime.fromtimestamp(NOW - finished_age, timezone.utc).isoformat(),
         "pr": None, "gh": {"pr_url": None, "pr": None}}
    t.update(kw)
    return t


def curta(chave: str) -> str:
    # fim:<sq>:<id>:<finished> → fim:<sq>:<id> (o finished é ISO e também tem «:»)
    return ":".join(chave.split(":")[:3]) if chave.startswith("fim:") else chave


def curtas(chaves) -> dict | set:
    return {curta(k): v for k, v in chaves.items()} if isinstance(chaves, dict) else {curta(k) for k in chaves}


def estado(tasks=(), alertas=(), aprovacoes=()) -> dict:
    # cada alerta tarefa:<id> sem tarefa em tasks traz a sua tarefa morta (os alertas do Lite são só estes)
    tasks = list(tasks)
    ids = {t["id"] for t in tasks}
    tasks += [tarefa(a[7:], status="morreu") for a in alertas if a.startswith("tarefa:") and a[7:] not in ids]
    return {"tasks": tasks, "alerts": {"count": len(alertas), "items": sorted(alertas)},
            "aprovacoes": list(aprovacoes)}


# --- funções puras ---
class Base64Test(unittest.TestCase):
    def test_ida_e_volta(self):
        for b in (b"", b"\x00", b"\xff" * 65, bytes(range(16))):
            self.assertEqual(push.deb64u(push.b64u(b)), b)
        self.assertNotIn("=", push.b64u(b"\x00"))
        self.assertEqual(push.deb64u("AA=="), b"\x00")   # com padding também

    def test_invalido(self):
        for mau in ("a+b/", "a b", "€", None, 5, "A"):
            with self.subTest(mau=mau), self.assertRaises(ValueError):
                push.deb64u(mau)


class ValidacaoTest(unittest.TestCase):
    def test_hosts(self):
        bons = (FCM, "https://web.push.apple.com/QGx", "https://updates.push.services.mozilla.com/wpush/v2/x",
                "https://x.push.services.mozilla.com/a", "https://wns2-db5p.notify.windows.com/w/?token=x",
                "https://fcm.googleapis.com:443/fcm/send/x", "https://FCM.googleapis.com/x")
        maus = ("http://fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.com/x",
                "https://evil.com/fcm.googleapis.com",
                "https://push.apple.com/x", "https://.push.apple.com/x", "https://user@fcm.googleapis.com/x",
                "https://fcm.googleapis.com:8443/x", "https://fcm.googleapis.com:abc/x", "https://127.0.0.1/x",
                "https://fcm.googleapis.com/x y", "https://fcm.googleapis.com/\n", FCM + "a" * 1024, "", None,
                "ftp://fcm.googleapis.com/x", "https://fcm.googleapis.com./x", "https://evilpush.apple.com/x")
        for e in bons:
            with self.subTest(bom=e):
                self.assertTrue(push.host_permitido(e))
        for e in maus:
            with self.subTest(mau=e):
                self.assertFalse(push.host_permitido(e))

    def test_subscricao(self):
        self.assertIsNone(push.validar_subscricao(sub()))
        self.assertIsNone(push.validar_subscricao({**sub(), "expirationTime": None}))   # PushSubscription.toJSON()
        casos = [
            (sub("http://fcm.googleapis.com/x"), "endpoint inválido"),
            (sub("https://evil.com/x"), "serviço de push não permitido"),
            (sub(p256dh=push.b64u(b"\x04" * 64)), "chaves da subscrição inválidas"),
            (sub(p256dh=push.b64u(b"\x03" + b"\x01" * 64)), "chaves da subscrição inválidas"),
            (sub(auth=push.b64u(b"\x02" * 15)), "chaves da subscrição inválidas"),
            (sub(auth="não b64"), "chaves da subscrição inválidas"),
            ({"endpoint": FCM, "keys": {"p256dh": sub()["keys"]["p256dh"]}}, "chaves da subscrição inválidas"),
            ({**sub(), "x": 1}, "campos desconhecidos"),
            (None, "endpoint inválido"),
            ([], "endpoint inválido"),
        ]
        for d, msg in casos:
            with self.subTest(d=d):
                self.assertEqual(push.validar_subscricao(d), msg)

    def test_prefs(self):
        self.assertIsNone(push.validar_prefs(push.prefs_omissao()))
        p = push.prefs_omissao()
        p["silencio"].update(de="07:00", ate="07:00")
        self.assertEqual(push.validar_prefs(p), "as horas de silêncio têm de ser diferentes")
        for hora in ("24:00", "7:00", "07:60", "07:00 ", "٠٧:٠٠", 700, None):
            p = push.prefs_omissao()
            p["silencio"]["de"] = hora
            with self.subTest(hora=hora):
                self.assertEqual(push.validar_prefs(p), "preferências inválidas")
        for mexe, msg in ((lambda p: p.update(x=1), "campos desconhecidos"),
                          (lambda p: p["eventos"].update(x=True), "campos desconhecidos"),
                          (lambda p: p["silencio"].update(fuso="UTC"), "campos desconhecidos"),
                          (lambda p: p["eventos"].pop("pr"), "preferências inválidas"),
                          (lambda p: p.pop("squads"), "preferências inválidas"),
                          (lambda p: p["squads"].update(dev=1), "preferências inválidas"),
                          (lambda p: p["silencio"].update(ligado="sim"), "preferências inválidas"),
                          (lambda p: p.update(criticos_ignoram_silencio=None), "preferências inválidas"),
                          (lambda p: p.update(eventos=[]), "preferências inválidas")):
            p = push.prefs_omissao()
            mexe(p)
            with self.subTest(p=p):
                self.assertEqual(push.validar_prefs(p), msg)
        self.assertEqual(push.validar_prefs(None), "preferências inválidas")

    def test_omissao_d9_e_copia_nova(self):
        p = push.prefs_omissao()
        self.assertEqual(p, {"eventos": {"concluida": True, "falhada": True, "pr": True, "alertas": True,
                                         "aprovacoes": False, "fases": False},
                             "squads": {"dev": True, "marketing": True, "rnd": True, "support": True},
                             "fases": {"SPEC": False, "PLAN": False, "CODE": False, "QA": True, "PR": True},
                             "silencio": {"ligado": False, "de": "23:00", "ate": "07:00"},
                             "criticos_ignoram_silencio": True})
        # CA9: a ordem das chaves é a do contrato (o definicoes_test.mjs compara Object.keys)
        self.assertEqual(list(p), ["eventos", "squads", "fases", "silencio", "criticos_ignoram_silencio"])
        self.assertEqual(list(p["fases"]), ["SPEC", "PLAN", "CODE", "QA", "PR"])
        p["eventos"]["pr"] = False
        self.assertTrue(push.prefs_omissao()["eventos"]["pr"])


SILENCIO = {"ligado": True, "de": "23:00", "ate": "07:00"}


class SilencioTest(unittest.TestCase):

    def em(self, iso_utc: str, s=None) -> bool:
        return push.em_silencio(s or SILENCIO, datetime.fromtimestamp(ts(iso_utc), AMS))

    def test_limites(self):
        # 2026-11-05 em CET (UTC+1)
        self.assertTrue(self.em("2026-11-05T22:00:00+00:00"))    # 23:00
        self.assertTrue(self.em("2026-11-05T05:59:00+00:00"))    # 06:59
        self.assertFalse(self.em("2026-11-05T06:00:00+00:00"))   # 07:00
        self.assertFalse(self.em("2026-11-05T21:59:00+00:00"))   # 22:59
        self.assertTrue(self.em("2026-11-05T23:30:00+00:00"))    # 00:30

    def test_mudanca_de_hora(self):
        # 2026-10-25: CEST → CET às 03:00; 2027-03-28: CET → CEST às 02:00
        self.assertTrue(self.em("2026-10-25T05:59:00+00:00"))    # 06:59 CET
        self.assertFalse(self.em("2026-10-25T06:00:00+00:00"))   # 07:00 CET
        self.assertTrue(self.em("2026-10-24T21:00:00+00:00"))    # 23:00 CEST
        self.assertFalse(self.em("2026-10-24T20:59:00+00:00"))   # 22:59 CEST
        self.assertTrue(self.em("2027-03-28T04:59:00+00:00"))    # 06:59 CEST
        self.assertFalse(self.em("2027-03-28T05:00:00+00:00"))   # 07:00 CEST
        self.assertTrue(self.em("2027-03-27T22:00:00+00:00"))    # 23:00 CET

    def test_desligado_e_intervalo_no_mesmo_dia(self):
        self.assertFalse(self.em("2026-11-05T22:00:00+00:00", {**SILENCIO, "ligado": False}))
        dia = {"ligado": True, "de": "13:00", "ate": "14:00"}
        self.assertTrue(self.em("2026-11-05T12:00:00+00:00", dia))    # 13:00
        self.assertFalse(self.em("2026-11-05T13:00:00+00:00", dia))   # 14:00
        self.assertFalse(self.em("2026-11-05T11:59:00+00:00", dia))

    def test_quer(self):
        noite = datetime.fromtimestamp(ts("2026-11-05T23:00:00+00:00"), AMS)
        dia = datetime.fromtimestamp(ts("2026-11-05T12:00:00+00:00"), AMS)
        p = push.prefs_omissao()
        p["silencio"]["ligado"] = True
        ev = {"categoria": "concluida", "squad": "dev", "critico": False}
        crit = {"categoria": "alertas", "squad": None, "critico": True}
        self.assertTrue(push.quer(ev, p, dia))
        self.assertFalse(push.quer(ev, p, noite))
        self.assertTrue(push.quer(crit, p, noite))
        p["criticos_ignoram_silencio"] = False
        self.assertFalse(push.quer(crit, p, noite))
        p = push.prefs_omissao()
        p["squads"]["dev"] = False
        self.assertFalse(push.quer(ev, p, dia))
        self.assertTrue(push.quer(crit, p, dia))   # sem squad: o filtro de squads não se aplica
        p["eventos"]["alertas"] = False
        self.assertFalse(push.quer(crit, p, dia))
        self.assertFalse(push.quer({"categoria": "aprovacoes", "squad": None, "critico": False},
                                   push.prefs_omissao(), dia))   # D9: aprovações desligadas


class EventosTest(unittest.TestCase):
    def evs(self, **kw):
        return {curta(e["chave"]): e for e in push.eventos(estado(**kw), NOW, DONE)}

    def test_categorias_e_textos(self):
        e = self.evs(
            tasks=[tarefa("ok"), tarefa("par", status="parcial (PR draft)"), tarefa("err", status="erro"),
                   tarefa("mk", squad="marketing", status="cancelada", project=None),
                   tarefa("st", status="parada?", running=True, mode="task"),
                   tarefa("pr", status="a correr", running=True, gh={"pr_url": "https://github.com/o/r/pull/338"}),
                   tarefa("pr2", status="a correr", running=True, pr="https://github.com/o/r/pull/7 (open)"),
                   tarefa("vivo", status="a correr", running=True),
                   tarefa("morto", squad="rnd", status="morreu", project="joga"),
                   tarefa("dry", mode="dry-run"), tarefa("dry2", status="dry-run"), tarefa("q", status="?")],
            alertas=["tarefa:morto"],
            aprovacoes=[{"fonte": "marketing", "ficheiro": "a.md", "titulo": "x"},
                        {"fonte": "geral", "ficheiro": "b.md", "titulo": "y"}])
        self.assertEqual(set(e), {"fim:dev:ok", "fim:dev:par", "fim:dev:err", "fim:marketing:mk", "parada:dev:st",
                                  "pr:dev:pr:338", "pr:dev:pr2:7", "fase:dev:pr2:PR", "tarefa:morto", "apr:marketing/a.md", "apr:geral/b.md"})
        resumo = {k: (v["categoria"], v["squad"], v["critico"], v["payload"]["title"], v["payload"]["body"],
                      v["payload"]["tag"], v["payload"]["url"]) for k, v in e.items()}
        abrir, ver = "Toca para abrir a execução.", "Toca para ver o que aconteceu."
        self.assertEqual(resumo, {
            # Dev goal: barra no body (sem fases nos dados → tudo por fazer); o resto fica como antes (CA7)
            "fim:dev:ok": ("concluida", "dev", False, "✅ Dev · loja: goal concluído",
                           "▱▱▱▱▱ loja · ok · concluído", "ok", "./#ok"),
            "fim:dev:par": ("concluida", "dev", False, "⚠️ Dev · loja: goal parcial (PR draft)",
                            "▱▱▱▱▱ loja · par · PR em rascunho", "par", "./#par"),
            "fim:dev:err": ("falhada", "dev", False, "⚠️ Dev · loja: tarefa erro",
                            "▱▱▱▱▱ loja · err · falhou na SPEC", "err", "./#err"),
            "fim:marketing:mk": ("falhada", "marketing", False, "⚠️ Marketing · geral: tarefa cancelada", ver, "mk",
                                 "./#mk"),
            "parada:dev:st": ("falhada", "dev", False, "⏸ Dev · loja: tarefa parada?",
                              "Sem actividade há mais de 30 min.", "parada:dev:st", "./#st"),
            "pr:dev:pr:338": ("pr", "dev", False, "🎉 Dev · loja: PR #338 aberto", abrir, "pr:dev:pr:338", "./#pr"),
            "pr:dev:pr2:7": ("pr", "dev", False, "🎉 Dev · loja: PR #7 aberto", abrir, "pr:dev:pr2:7", "./#pr2"),
            "fase:dev:pr2:PR": ("fases", "dev", False, "📍 Dev · loja: PR", "▱▱▱▱▰ loja · pr2 · PR aberto",
                                "fase:pr2", "./#pr2"),
            "tarefa:morto": ("alertas", "rnd", True, "💀 R&D · joga: tarefa morreu",
                             "O lançador deixou de dar sinal há mais de 5 min.", "morto", "./#morto"),
            "apr:marketing/a.md": ("aprovacoes", "marketing", False, "📝 Aprovação nova · Marketing",
                                   "Há decisões à tua espera no Office.", "apr:marketing/a.md", "./"),
            "apr:geral/b.md": ("aprovacoes", None, False, "📝 Aprovação nova · Geral",
                               "Há decisões à tua espera no Office.", "apr:geral/b.md", "./"),
        })
        for v in e.values():
            self.assertEqual(v["payload"]["critico"], v["critico"])
            self.assertLessEqual(len(json.dumps(v["payload"], ensure_ascii=False).encode()), 1024)
        self.assertEqual(e["fim:dev:ok"]["fim_ts"], NOW - 60)

    def test_pr_so_no_dev_e_cortes(self):
        e = self.evs(tasks=[tarefa("m", squad="marketing", status="a correr", running=True,
                                   pr="https://github.com/o/r/pull/9")])
        self.assertEqual(e, {})
        e = self.evs(tasks=[tarefa("x", project="p" * 100, mode="m" * 100), tarefa("y", status="e" * 100)])
        self.assertEqual(e["fim:dev:x"]["payload"]["title"], f"✅ Dev · {'p' * 40}: {'m' * 40} concluído")
        self.assertEqual(e["fim:dev:y"]["payload"]["title"], f"⚠️ Dev · loja: tarefa {'e' * 40}")

    def test_chave_do_fim_leva_o_finished_e_a_tag_o_id(self):
        [e] = push.eventos(estado(tasks=[tarefa("ok")]), NOW, DONE)
        fin = datetime.fromtimestamp(NOW - 60, timezone.utc).isoformat()
        self.assertEqual((e["chave"], e["payload"]["tag"]), (f"fim:dev:ok:{fin}", "ok"))

    def test_url_codificado(self):
        e = self.evs(tasks=[tarefa("a b#c")])
        self.assertEqual(e["fim:dev:a b#c"]["payload"]["url"], "./#a%20b%23c")

    def test_ignora_lixo(self):
        st = {"tasks": [{"id": 5, "squad": "dev"}, {"id": "x", "squad": "outra"}, tarefa("ok")],
              "alerts": {"items": ["tarefa:inexistente", "outro:x", "cron:x", "sonda:s", 7]}, "aprovacoes": [{"fonte": 1}]}
        self.assertEqual([curta(e["chave"]) for e in push.eventos(st, NOW, DONE)], ["fim:dev:ok"])
        self.assertEqual(push.eventos({}, NOW, DONE), [])

    def test_ac17_nada_de_texto_das_tarefas(self):
        marca = "MARCADOR-SECRETO"
        st = estado(tasks=[tarefa("ok", task=marca, result=marca, cost_usd=123.45, titulo=marca),
                           tarefa("vivo", status="a correr", running=True, task=marca, issues=[marca],
                                  gh={"pr_url": "https://github.com/o/r/pull/1", "titulo": marca})],
                    alertas=["cron:x"],
                    aprovacoes=[{"fonte": "marketing", "ficheiro": "a.md", "titulo": marca, "resumo": marca,
                                 "orcamento": "123.45"}])
        texto = json.dumps([e["payload"] for e in push.eventos(st, NOW, DONE)], ensure_ascii=False)
        self.assertNotIn(marca, texto)
        self.assertNotIn("123.45", texto)


class SeleccionarTest(unittest.TestCase):
    def ev(self, chave, fim_ts=NOW):
        return {"chave": chave, "fim_ts": fim_ts if chave.startswith("fim:") else None}

    def test_primeiro_arranque_so_semeia(self):
        evs = [self.ev("fim:dev:a"), self.ev("fim:dev:b"), self.ev("fim:dev:c"), self.ev("cron:x")]
        enviar, nots = push.seleccionar(evs, None, NOW)
        self.assertEqual(enviar, [])
        self.assertEqual(nots, {e["chave"]: NOW for e in evs})

    def test_so_chaves_novas(self):
        enviar, nots = push.seleccionar([self.ev("fim:dev:a"), self.ev("fim:dev:b")], {"fim:dev:a": NOW - 10}, NOW)
        self.assertEqual([e["chave"] for e in enviar], ["fim:dev:b"])
        self.assertEqual(nots, {"fim:dev:a": NOW, "fim:dev:b": NOW})   # a presente renova-se

    def test_alerta_que_sai_e_volta(self):
        # Lite: sem crons nem sondas; a tarefa morta que sai fica notificada (não repete se voltar)
        _, nots = push.seleccionar([self.ev("tarefa:t")], {}, NOW)
        _, nots = push.seleccionar([], nots, NOW + 30)
        self.assertEqual(nots, {"tarefa:t": NOW})
        enviar, _ = push.seleccionar([self.ev("tarefa:t")], nots, NOW + 60)
        self.assertEqual(enviar, [])

    def test_fim_antigo_ou_sem_hora_so_semeia(self):
        evs = [self.ev("fim:dev:velha", NOW - 6 * 3600 - 1), self.ev("fim:dev:limite", NOW - 6 * 3600),
               self.ev("fim:dev:sem", None)]
        enviar, nots = push.seleccionar(evs, {}, NOW)
        self.assertEqual([e["chave"] for e in enviar], ["fim:dev:limite"])
        self.assertEqual(set(nots), {"fim:dev:velha", "fim:dev:limite", "fim:dev:sem"})

    def test_retencao_31_dias(self):
        nots = {"fim:dev:a": NOW - 31 * 86400 - 1, "fim:dev:b": NOW - 31 * 86400}
        _, out = push.seleccionar([], nots, NOW)
        self.assertEqual(out, {"fim:dev:b": NOW - 31 * 86400})

    def test_chave_presente_aos_31_dias_nao_reenvia(self):
        # regressão: a retenção só apaga chaves que já não estão no estado
        velho = NOW - 40 * 86400
        evs = [self.ev("tarefa:t"), self.ev("pr:dev:t:3"), self.ev("apr:marketing/a.md"), self.ev("parada:dev:t")]
        nots = {e["chave"]: velho for e in evs} | {"fim:dev:saiu:x": velho}
        enviar, out = push.seleccionar(evs, nots, NOW)
        self.assertEqual(enviar, [])
        self.assertEqual(out, {e["chave"]: NOW for e in evs})   # renova-se; a que saiu expira

    def test_ausencia_de_um_ciclo_depois_dos_31_dias_nao_reenvia(self):
        # regressão (review 2): a retenção conta desde a última vez no estado, não desde a 1.ª notificação
        evs = [self.ev("apr:marketing/x.md")]
        _, nots = push.seleccionar(evs, {"apr:marketing/x.md": NOW - 40 * 86400}, NOW)
        _, nots = push.seleccionar([], nots, NOW + 30)   # um ciclo sem a aprovação (ficheiro a meio de escrita)
        enviar, _ = push.seleccionar(evs, nots, NOW + 60)
        self.assertEqual(enviar, [])

    def test_duplicados_no_mesmo_ciclo(self):
        enviar, _ = push.seleccionar([self.ev("cron:x"), self.ev("cron:x")], {}, NOW)
        self.assertEqual(len(enviar), 1)


class AgruparTest(unittest.TestCase):
    def test_oito_dao_cinco_mais_um(self):
        pays = [{"title": str(i), "critico": i == 7} for i in range(8)]
        out = push.agrupar(pays)
        self.assertEqual([p["title"] for p in out], ["7", "0", "1", "2", "3", "Mais 3 avisos"])
        self.assertEqual(out[-1], {"title": "Mais 3 avisos", "body": "Abre o Office para ver tudo.", "tag": "mais",
                                   "url": "./", "critico": False})

    def test_ate_cinco_sem_resumo(self):
        self.assertEqual(len(push.agrupar([{"title": "x", "critico": False}] * 5)), 5)
        self.assertEqual(push.agrupar([]), [])


class SemCryptoTest(unittest.TestCase):
    def test_estado_503(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(push, "ec", None), \
                contextlib.redirect_stdout(io.StringIO()):
            s = push.Servico(Path(d), "https://x", AMS, DONE)
            self.assertFalse(s.iniciar())
            self.assertEqual(s.estado(), (False, "sem_cryptography"))
            self.assertEqual(s.ciclo(estado(tasks=[tarefa()]), NOW), 0)
            self.assertEqual(os.listdir(d), [])


# --- barra nas notificações e aviso por fase (SPEC 20261106 CA1–CA21) ---
def fs_feitas(n: int) -> list[dict]:
    # as n primeiras fases feitas, pela regra do progresso.fases()
    return progresso.fases({"SPEC": n > 0, "PLAN": n > 1, "QA": n > 3}, ["backend-engineer"] if n > 2 else [], n > 4)


def goal(tid="20261105-1010-suporte-caixa", n=0, **kw) -> dict:
    # tarefa Dev goal com as n primeiras fases feitas nos campos do estado (como o server.py as expõe)
    return tarefa(tid, phases={"SPEC": n > 0, "PLAN": n > 1, "QA": n > 3},
                  agents=[{"role": "backend-engineer"}] if n > 2 else [{"role": "test-engineer"}],
                  gh={"pr_url": None, "pr": "aberto" if n > 4 else None}, **kw)


def a_correr(tid="20261105-1010-suporte-caixa", n=0, **kw) -> dict:
    return goal(tid, n, status="a correr", running=True, **kw)


class BarraTest(unittest.TestCase):
    def test_ca1_ca8_barra_de_0_a_5(self):
        for n in range(6):
            with self.subTest(n=n):
                b = push.barra(fs_feitas(n))
                self.assertEqual(b, "▰" * n + "▱" * (5 - n))
                self.assertEqual(len(b.encode()), 15)

    def test_ca2_fases_da_tarefa_terminada(self):
        t = tarefa("t", phases={"SPEC": True, "PLAN": True, "QA": False}, agents=[{"role": "test-engineer"}],
                   gh={"pr_url": None, "pr": "integrado"}, progress=None)
        self.assertEqual([f["estado"] for f in push.fases_tarefa(t)], ["feito", "feito", "agora", "", "feito"])
        t["agents"] = [{"role": "test-engineer"}, {"role": "frontend-engineer"}]
        self.assertEqual(push.barra(push.fases_tarefa(t)), "▰▰▰▱▰")
        self.assertEqual(push.barra(push.fases_tarefa({"pr": "https://github.com/o/r/pull/1"})), "▱▱▱▱▰")

    def test_ca2_tolera_campos_em_falta_ou_errados(self):
        for t in ({}, {"phases": None, "agents": None, "gh": None}, {"phases": [], "agents": "x", "gh": 5},
                  {"agents": [None, 3, {"x": 1}]}):
            with self.subTest(t=t):
                self.assertEqual(push.barra(push.fases_tarefa(t)), "▱▱▱▱▱")

    def test_ca3_slug(self):
        for tid, esperado in (("20261105-1010-suporte-caixa", "suporte-caixa"), ("suporte-caixa", "suporte-caixa"),
                              ("2026110-1010-x", "2026110-1010-x"), ("x-20261105-1010-y", "x-20261105-1010-y"),
                              ("20261105-1010-" + "a" * 50, "a" * 40), ("b" * 50, "b" * 40),
                              ("20261105-1010-", "20261105-1010-")):
            with self.subTest(tid=tid):
                self.assertEqual(push.slug(tid), esperado)

    def test_ca4_ca5_ca8_estados(self):
        casos = [((5, "concluída", True, True), "PR pronto"), ((3, "concluída", True, False), "concluído"),
                 ((4, "parcial (PR draft)", True, True), "PR em rascunho"),
                 ((0, "erro", False, False), "falhou na SPEC"), ((1, "erro", False, False), "falhou no PLAN"),
                 ((2, "cancelada", False, False), "falhou no código"), ((3, "erro", False, False), "falhou no QA"),
                 ((4, "erro", False, False), "falhou no PR"), ((5, "erro", False, True), "falhou depois do PR"),
                 ((3, "parada?", False, False), "parada no QA"), ((0, "parada?", False, False), "parada na SPEC"),
                 ((5, "parada?", False, True), "parada depois do PR"),
                 ((2, "morreu", False, False), "falhou no código")]
        for (n, status, concluida, tem_pr), esperado in casos:
            with self.subTest(n=n, status=status):
                self.assertEqual(push.estado_fase(fs_feitas(n), status, concluida, tem_pr), esperado)


class EventosFasesTest(unittest.TestCase):
    def evs(self, *tasks, alertas=()):
        return {curta(e["chave"]): e for e in push.eventos(estado(tasks=tasks, alertas=alertas), NOW, DONE)}

    def test_ca6_exemplo_exacto(self):
        e = self.evs(goal(n=3, status="erro", project="Loja"))
        [ev] = e.values()
        self.assertEqual(ev["payload"]["title"], "⚠️ Dev · Loja: tarefa erro")
        self.assertEqual(ev["payload"]["body"], "▰▰▰▱▱ Loja · suporte-caixa · falhou no QA")

    def test_ca3_ca4_corpos_do_fim_parada_e_morreu(self):
        e = self.evs(goal("20261105-1010-a", n=5), goal("20261105-1010-b", n=3),
                     goal("20261105-1010-c", n=4, status="parcial (PR draft)"),
                     goal("20261105-1010-d", n=3, status="parada?", running=True, project=""),
                     goal("20261105-1010-e", n=1, status="morreu", running=True), alertas=["tarefa:20261105-1010-e"])
        corpo = {k: (v["payload"]["title"], v["payload"]["body"]) for k, v in e.items()
                 if k.startswith(("fim:", "parada:", "tarefa:"))}
        self.assertEqual(corpo, {
            "fim:dev:20261105-1010-a": ("✅ Dev · loja: goal concluído", "▰▰▰▰▰ loja · a · PR pronto"),
            "fim:dev:20261105-1010-b": ("✅ Dev · loja: goal concluído", "▰▰▰▱▱ loja · b · concluído"),
            "fim:dev:20261105-1010-c": ("⚠️ Dev · loja: goal parcial (PR draft)",
                                        "▰▰▰▰▱ loja · c · PR em rascunho"),
            "parada:dev:20261105-1010-d": ("⏸ Dev · geral: tarefa parada?", "▰▰▰▱▱ geral · d · parada no QA"),
            "tarefa:20261105-1010-e": ("💀 Dev · loja: tarefa morreu", "▰▱▱▱▱ loja · e · falhou no PLAN"),
        })
        self.assertTrue(e["tarefa:20261105-1010-e"]["critico"])

    def test_ca7_outras_squads_e_dev_task_sem_barra(self):
        fases = {"phases": {"SPEC": True, "PLAN": True, "QA": True}, "agents": [{"role": "backend-engineer"}]}
        e = self.evs(tarefa("mk", squad="marketing", **fases), tarefa("tk", mode="task", status="erro", **fases),
                     tarefa("vm", squad="marketing", status="a correr", running=True, **fases),
                     tarefa("vt", mode="task", status="parada?", running=True, **fases),
                     tarefa("rn", squad="rnd", status="morreu", **fases), alertas=["tarefa:rn"])
        self.assertEqual({k: (v["payload"]["title"], v["payload"]["body"]) for k, v in e.items()}, {
            "fim:marketing:mk": ("✅ Marketing · loja: goal concluído", "Toca para abrir a execução."),
            "fim:dev:tk": ("⚠️ Dev · loja: tarefa erro", "Toca para ver o que aconteceu."),
            "parada:dev:vt": ("⏸ Dev · loja: tarefa parada?", "Sem actividade há mais de 30 min."),
            "tarefa:rn": ("💀 R&D · loja: tarefa morreu", "O lançador deixou de dar sinal há mais de 5 min."),
        })
        for v in e.values():
            self.assertNotIn("renotify", v["payload"])

    def test_ca13_so_dev_goal_a_correr_sem_dry_run(self):
        e = self.evs(goal("fim", n=3), a_correr("tk", n=3, mode="task"), a_correr("dr", n=3, mode="dry-run"),
                     goal("ds", n=3, status="dry-run", running=True),
                     a_correr("mk", n=3, squad="marketing"), a_correr("rd", n=3, squad="rnd"))
        self.assertEqual([k for k in e if k.startswith("fase:")], [])
        e = self.evs(a_correr("vivo", n=3), goal("parada", n=2, status="parada?", running=True))
        self.assertEqual([k for k in e if k.startswith("fase:")], ["fase:dev:vivo:SPEC", "fase:dev:vivo:PLAN",
                                                                   "fase:dev:vivo:CODE", "fase:dev:parada:SPEC",
                                                                   "fase:dev:parada:PLAN"])

    def test_ca14_ca16_chaves_e_payload(self):
        e = self.evs(a_correr(n=4, project="Loja"))
        tid = "20261105-1010-suporte-caixa"
        self.assertEqual(list(e), [f"fase:dev:{tid}:{f}" for f in ("SPEC", "PLAN", "CODE", "QA")])
        qa = e[f"fase:dev:{tid}:QA"]
        self.assertEqual(qa["payload"], {"title": "📍 Dev · Loja: QA",
                                         "body": "▰▰▰▰▱ Loja · suporte-caixa · QA feito", "tag": f"fase:{tid}",
                                         "url": f"./#{tid}", "critico": False, "renotify": False})
        self.assertEqual((qa["categoria"], qa["squad"], qa["critico"], qa["tarefa"], qa["fase"]),
                         ("fases", "dev", False, tid, "QA"))
        self.assertEqual(e[f"fase:dev:{tid}:CODE"]["payload"]["title"], "📍 Dev · Loja: código")
        self.assertEqual(self.evs(a_correr(n=0)), {})   # nada feito, nada a avisar

    def test_ca21_sem_texto_da_tarefa_e_ate_1kb(self):
        marca = "MARCADOR-SECRETO"
        st = estado(tasks=[a_correr("20261105-1010-" + "i" * 200, n=5, project="p" * 200, task=marca, result=marca,
                                    cost_usd=123.45, titulo=marca),
                           goal("20261105-1011-" + "j" * 200, n=3, status="erro", project="ç" * 200, task=marca,
                                result=marca, cost_usd=123.45)])
        pays = [e["payload"] for e in push.eventos(st, NOW, DONE)]
        self.assertTrue(any(p["tag"].startswith("fase:") for p in pays))
        texto = json.dumps(pays, ensure_ascii=False)
        self.assertNotIn(marca, texto)
        self.assertNotIn("123.45", texto)
        for p in pays:
            self.assertLessEqual(len(json.dumps(p, ensure_ascii=False).encode()), 1024)


class PrefsFasesTest(unittest.TestCase):
    def antigas(self) -> dict:
        p = push.prefs_omissao()
        del p["eventos"]["fases"], p["fases"]
        return p

    def test_ca10_normalizar_prefs_antigas(self):
        a = self.antigas()
        copia = json.loads(json.dumps(a))
        n = push.normalizar_prefs(a)
        self.assertEqual(n, push.prefs_omissao())
        self.assertIsNone(push.validar_prefs(n))
        self.assertEqual(a, copia)   # não mexe no que recebeu
        self.assertEqual(push.validar_prefs(a), "preferências inválidas")   # a forma antiga só passa normalizada

    def test_normalizar_mistas_e_lixo(self):
        p = self.antigas()
        p["fases"] = {"SPEC": True, "PLAN": False, "CODE": False, "QA": False, "PR": False}
        n = push.normalizar_prefs(p)
        self.assertEqual((n["eventos"]["fases"], n["fases"]["SPEC"]), (False, True))
        p = push.prefs_omissao()
        p["eventos"]["fases"] = True
        self.assertEqual(push.normalizar_prefs(p), p)   # forma nova fica igual
        for lixo in (None, [], "x", 5):
            self.assertIs(push.normalizar_prefs(lixo), lixo)
        self.assertEqual(push.normalizar_prefs({"eventos": []})["eventos"], [])   # validar_prefs é que recusa

    def test_ca11_validar_forma_nova(self):
        for mexe, msg in ((lambda p: p["fases"].update(X=True), "campos desconhecidos"),
                          (lambda p: p["eventos"].update(fases=1), "preferências inválidas"),
                          (lambda p: p["fases"].update(QA="sim"), "preferências inválidas"),
                          (lambda p: p["fases"].pop("PR"), "preferências inválidas"),
                          (lambda p: p.update(fases=[]), "preferências inválidas")):
            p = push.prefs_omissao()
            mexe(p)
            with self.subTest(p=p):
                self.assertEqual(push.validar_prefs(push.normalizar_prefs(p)), msg)

    def test_ca17_ca18_quer(self):
        dia = datetime.fromtimestamp(ts("2026-11-05T12:00:00+00:00"), AMS)
        noite = datetime.fromtimestamp(ts("2026-11-05T23:00:00+00:00"), AMS)
        ev = {"categoria": "fases", "squad": "dev", "critico": False, "fase": "QA"}
        p = push.prefs_omissao()
        self.assertFalse(push.quer(ev, p, dia))   # desligado por omissão
        p["eventos"]["fases"] = True
        self.assertTrue(push.quer(ev, p, dia))
        self.assertFalse(push.quer({**ev, "fase": "SPEC"}, p, dia))   # fase desligada
        p["squads"]["dev"] = False
        self.assertFalse(push.quer(ev, p, dia))
        p["squads"]["dev"] = True
        p["silencio"]["ligado"] = True
        self.assertTrue(p["criticos_ignoram_silencio"])
        self.assertFalse(push.quer(ev, p, noite))

    def test_ca19_colapsar(self):
        def ev(tarefa_, fase):
            return {"categoria": "fases", "tarefa": tarefa_, "fase": fase, "chave": f"fase:dev:{tarefa_}:{fase}"}
        fim = {"categoria": "concluida", "chave": "fim:dev:a"}
        evs = [ev("a", "SPEC"), fim, ev("a", "PLAN"), ev("b", "QA"), ev("b", "CODE"), ev("a", "CODE")]
        self.assertEqual([e["chave"] for e in push.colapsar(evs)], ["fim:dev:a", "fase:dev:b:QA", "fase:dev:a:CODE"])
        self.assertEqual(push.colapsar([ev("a", "SPEC")]), [ev("a", "SPEC")])
        self.assertEqual(push.colapsar([]), [])

    def test_colapsar_pr_no_mesmo_lote(self):
        # o «PR #n aberto» da mesma tarefa no mesmo lote cala os avisos de fase dela; outra tarefa não é afectada
        def ev(tarefa_, fase):
            return {"categoria": "fases", "tarefa": tarefa_, "fase": fase, "chave": f"fase:dev:{tarefa_}:{fase}"}
        pr = {"categoria": "pr", "tarefa": "a", "chave": "pr:dev:a:9"}
        evs = [ev("a", "QA"), ev("a", "PR"), pr, ev("b", "PR")]
        self.assertEqual([e["chave"] for e in push.colapsar(evs)], ["pr:dev:a:9", "fase:dev:b:PR"])
        self.assertEqual([e["chave"] for e in push.colapsar([ev("a", "QA"), ev("a", "PR")])], ["fase:dev:a:PR"])

    def test_fases_tarefa_role_nao_string(self):
        # um role que não é string (nunca vem do server.py) não rebenta o vigia: ignora-se
        t = {"phases": {"SPEC": True}, "agents": [{"role": ["x"]}, {"role": "backend-engineer"}, {"role": None}]}
        self.assertEqual([f["estado"] for f in push.fases_tarefa(t)], ["feito", "agora", "feito", "", ""])


# --- com cryptography ---
def decifrar(corpo: bytes, ua_priv, auth: bytes) -> bytes:
    # lado do browser (RFC 8291), escrito à parte para verificar o formato
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    sal, rs, idlen = corpo[:16], *struct.unpack("!IB", corpo[16:21])
    as_pub, cifrado = corpo[21:21 + idlen], corpo[21 + idlen:]
    assert rs == 4096 and idlen == 65
    segredo = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ua_pub = push._ponto(ua_priv.public_key())
    ikm = push._hmac(push._hmac(auth, segredo), b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")
    prk = push._hmac(sal, ikm)
    cek = push._hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = push._hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    texto = AESGCM(cek).decrypt(nonce, cifrado, None)
    assert texto.endswith(b"\x02")
    return texto[:-1]


@unittest.skipUnless(push.ec, SEM_CRYPTO)
class CifraTest(unittest.TestCase):
    def test_ac1_vector_do_rfc8291(self):
        out = push.cifrar(push.deb64u(RFC_TEXTO), push.deb64u(RFC_UA_PUB), push.deb64u(RFC_AUTH),
                          sal=push.deb64u(RFC_SAL), efemera=push.deb64u(RFC_AS_PRIV))
        self.assertEqual(out[:86], push.deb64u(RFC_CAB))
        self.assertEqual(out[21:86], push.deb64u(RFC_AS_PUB))
        self.assertEqual(out, push.deb64u(RFC_CAB) + push.deb64u(RFC_CIFRADO))

    def test_ida_e_volta_com_sal_aleatorio(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        ua = ec.generate_private_key(ec.SECP256R1())
        auth = os.urandom(16)
        a = push.cifrar(b'{"title":"x"}', push._ponto(ua.public_key()), auth)
        b = push.cifrar(b'{"title":"x"}', push._ponto(ua.public_key()), auth)
        self.assertNotEqual(a[:16], b[:16])
        self.assertEqual(decifrar(a, ua, auth), b'{"title":"x"}')

    def test_payload_grande_demais(self):
        with self.assertRaises(ValueError):
            push.cifrar(b"x" * 4080, push.deb64u(RFC_UA_PUB), push.deb64u(RFC_AUTH))


class Abridor:
    # falso do urllib: guarda os pedidos e devolve os códigos pela ordem (o último repete-se)
    def __init__(self, *codigos):
        self.codigos, self.pedidos = list(codigos) or [201], []

    def __call__(self, req, timeout):
        self.pedidos.append((req, timeout))
        c = self.codigos.pop(0) if len(self.codigos) > 1 else self.codigos[0]
        if isinstance(c, Exception):
            raise c
        return c


@unittest.skipUnless(push.ec, SEM_CRYPTO)
class ServicoTest(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.tmp = tempfile.TemporaryDirectory()
        self.pasta = Path(self.tmp.name)
        self.abrir = Abridor()
        self.out = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.out))
        self.s = push.Servico(self.pasta, "https://squad.example", AMS, DONE, abrir=self.abrir)
        self.ua = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)

    def tearDown(self):
        self.tmp.cleanup()

    def subscricao(self, endpoint=FCM) -> dict:
        return {"endpoint": endpoint, "keys": {"p256dh": push.b64u(push._ponto(self.ua.public_key())),
                                               "auth": push.b64u(self.auth)}}

    def subscreve(self, endpoint=FCM, prefs=None):
        d = {"token": "t", "subscricao": self.subscricao(endpoint)}
        if prefs is not None:
            d["prefs"] = prefs
        return self.s.subscrever(d, NOW)

    def payloads(self):
        return [json.loads(decifrar(r.data, self.ua, self.auth)) for r, _ in self.abrir.pedidos]

    def test_ac3_chave_0600_e_reutilizada(self):
        self.assertTrue(self.s.iniciar())
        f = self.pasta / "vapid"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertIn(b"BEGIN PRIVATE KEY", f.read_bytes())
        chave = self.s.chave_publica()
        self.assertEqual(len(push.deb64u(chave)), 65)
        self.assertTrue(self.s.iniciar())   # idempotente
        outro = push.Servico(self.pasta, "x", AMS, DONE)
        self.assertTrue(outro.iniciar())
        self.assertEqual(outro.chave_publica(), chave)
        self.assertEqual(sorted(os.listdir(self.pasta)), ["vapid"])   # sem .tmp
        self.assertNotIn("PRIVATE", self.out.getvalue())

    def test_ac2_jwt(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        self.s.iniciar()
        jwt = push.jwt_vapid(self.s._chave, FCM, "https://squad.example", NOW)
        cab, corpo, assin = jwt.split(".")
        self.assertEqual(json.loads(push.deb64u(cab)), {"typ": "JWT", "alg": "ES256"})
        c = json.loads(push.deb64u(corpo))
        self.assertEqual((c["aud"], c["sub"]), ("https://fcm.googleapis.com", "https://squad.example"))
        self.assertTrue(NOW < c["exp"] <= NOW + 24 * 3600)
        rs = push.deb64u(assin)
        self.assertEqual(len(rs), 64)
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.deb64u(self.s.chave_publica()))
        pub.verify(encode_dss_signature(int.from_bytes(rs[:32], "big"), int.from_bytes(rs[32:], "big")),
                   f"{cab}.{corpo}".encode(), ec.ECDSA(hashes.SHA256()))
        self.assertEqual(json.loads(push.deb64u(push.jwt_vapid(self.s._chave, "https://x.push.apple.com:443/a", "s",
                                                               NOW).split(".")[1]))["aud"], "https://x.push.apple.com")

    def test_sem_pasta(self):
        s = push.Servico(self.pasta / "nao", "x", AMS, DONE)
        self.assertEqual(s.estado(), (False, "sem_pasta"))

    @unittest.skipIf(os.geteuid() == 0, "root escreve em pastas 0500")
    def test_pasta_sem_escrita(self):
        os.chmod(self.pasta, 0o500)
        try:
            self.assertEqual(self.s.estado(), (False, "sem_pasta"))
        finally:
            os.chmod(self.pasta, 0o700)
        self.assertEqual(self.s.estado(), (True, None))   # volta sem reiniciar

    def test_chave_ilegivel_nao_e_substituida(self):
        (self.pasta / "vapid").write_bytes(b"lixo")
        self.assertEqual(self.s.estado(), (False, "sem_pasta"))
        self.assertEqual((self.pasta / "vapid").read_bytes(), b"lixo")

    def test_ac7_subscrever_prefs(self):
        self.s.iniciar()
        self.assertEqual(self.subscreve(), (200, {"prefs": push.prefs_omissao()}))
        p = push.prefs_omissao()
        p["eventos"]["pr"] = False
        self.assertEqual(self.subscreve(prefs=p), (200, {"prefs": p}))
        self.assertEqual(self.subscreve(), (200, {"prefs": p}))
        d = json.loads((self.pasta / "subscricoes.json").read_text())
        self.assertEqual(d["v"], 1)
        self.assertEqual(len(d["subscricoes"]), 1)
        self.assertEqual(set(d["subscricoes"][0]), {"endpoint", "keys", "prefs", "criado", "actualizado"})
        self.assertEqual(stat.S_IMODE((self.pasta / "subscricoes.json").stat().st_mode), 0o600)
        self.assertFalse([n for n in os.listdir(self.pasta) if n.endswith(".tmp")])

    def test_ac6_limite_e_erros(self):
        self.s.iniciar()
        for i in range(push.MAX_SUBS):
            self.assertEqual(self.subscreve(f"{FCM}{i}")[0], 200)
        self.assertEqual(self.subscreve(f"{FCM}x"), (409, {"error": "demasiados dispositivos"}))
        self.assertEqual(self.subscreve(f"{FCM}0")[0], 200)   # o mesmo endpoint actualiza
        self.assertEqual(self.s.subscrever({"token": "t", "subscricao": self.subscricao(), "x": 1}),
                         (400, {"error": "campos desconhecidos"}))
        p = push.prefs_omissao()
        del p["eventos"]["pr"]
        self.assertEqual(self.subscreve(prefs=p), (400, {"error": "preferências inválidas"}))

    def test_subscricoes_ilegiveis(self):
        self.s.iniciar()
        for lixo in (b"{", b'{"v": 2, "subscricoes": []}', b'{"v": 1, "subscricoes": [{"endpoint": "http://x"}]}'):
            (self.pasta / "subscricoes.json").write_bytes(lixo)
            with self.subTest(lixo=lixo):
                self.assertEqual(self.subscreve(), (503, {"error": "ficheiro de subscrições ilegível"}))
                self.assertEqual(self.s.cancelar({"token": "t", "endpoint": FCM})[0], 503)
                self.assertEqual((self.pasta / "subscricoes.json").read_bytes(), lixo)

    def test_ac8_cancelar(self):
        self.s.iniciar()
        self.subscreve()
        self.assertEqual(self.s.cancelar({"token": "t", "endpoint": FCM}), (200, {"ok": True}))
        self.assertEqual(json.loads((self.pasta / "subscricoes.json").read_text())["subscricoes"], [])
        self.assertEqual(self.s.cancelar({"token": "t", "endpoint": FCM}), (200, {"ok": True}))
        self.assertEqual(self.s.cancelar({"token": "t", "endpoint": 5}), (400, {"error": "endpoint inválido"}))
        self.assertEqual(self.s.cancelar({"token": "t", "endpoint": FCM, "x": 1}),
                         (400, {"error": "campos desconhecidos"}))

    def test_teste(self):
        self.s.iniciar()
        d = {"token": "t", "endpoint": FCM}
        self.assertEqual(self.s.teste(d, NOW), (404, {"error": "dispositivo não subscrito"}))
        self.subscreve()
        self.assertEqual(self.s.teste(d, NOW), (200, {"ok": True}))
        self.assertEqual(self.s.teste(d, NOW + 9), (429, {"error": "espera uns segundos antes de outro teste"}))
        [(req, timeout)] = self.abrir.pedidos
        self.assertEqual(timeout, 10)
        self.assertEqual(self.payloads(), [push.TESTE])
        h = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual((h["content-encoding"], h["content-type"], h["ttl"]),
                         ("aes128gcm", "application/octet-stream", "21600"))
        self.assertTrue(h["authorization"].startswith("vapid t="))
        self.assertTrue(h["authorization"].endswith(f", k={self.s.chave_publica()}"))
        self.assertEqual((req.get_method(), req.full_url), ("POST", FCM))
        self.abrir.codigos = [500]
        self.assertEqual(self.s.teste(d, NOW + 10), (502, {"error": "o serviço de push recusou (500)"}))
        self.abrir.codigos = [OSError("timed out")]
        self.assertEqual(self.s.teste(d, NOW + 20), (502, {"error": "o serviço de push não respondeu"}))
        self.abrir.codigos = [410]
        self.assertEqual(self.s.teste(d, NOW + 30), (410, {"error": "subscrição expirada, liga de novo"}))
        self.assertEqual(self.s.teste(d, NOW + 40)[0], 404)
        self.assertNotIn("/fcm/send", self.out.getvalue())

    # --- vigia ---
    def test_ac9_ac10_primeiro_arranque_e_reinicio(self):
        self.subscreve()
        st = estado(tasks=[tarefa("a"), tarefa("b", status="erro"), tarefa("c"),
                           tarefa("d", status="a correr", running=True)], alertas=["tarefa:x"])
        self.assertEqual(self.s.ciclo(st, NOW), 0)
        self.assertEqual(self.abrir.pedidos, [])
        nots = json.loads((self.pasta / "notificadas.json").read_text())
        self.assertEqual(nots["v"], 1)
        self.assertEqual(curtas(nots["chaves"]), {"fim:dev:a": NOW, "fim:dev:b": NOW, "fim:dev:c": NOW,
                                                  "tarefa:x": NOW})
        self.assertEqual(stat.S_IMODE((self.pasta / "notificadas.json").stat().st_mode), 0o600)
        reiniciado = push.Servico(self.pasta, "https://squad.example", AMS, DONE, abrir=self.abrir)
        self.assertEqual(reiniciado.ciclo(st, NOW + 30), 0)
        st["tasks"][3] = tarefa("d", finished_age=-30)
        self.assertEqual(reiniciado.ciclo(st, NOW + 30), 1)
        self.assertEqual([p["title"] for p in self.payloads()], ["✅ Dev · loja: goal concluído"])
        self.assertEqual(reiniciado.ciclo(st, NOW + 60), 0)
        self.assertEqual(len(self.abrir.pedidos), 1)

    def test_notificadas_ilegivel_e_primeiro_arranque(self):
        self.subscreve()
        (self.pasta / "notificadas.json").write_text("{lixo")
        self.assertEqual(self.s.ciclo(estado(tasks=[tarefa("a")]), NOW), 0)
        self.assertIn("notificadas.json ilegível", self.out.getvalue())
        self.assertEqual(curtas(json.loads((self.pasta / "notificadas.json").read_text())["chaves"]),
                         {"fim:dev:a": NOW})

    def test_ac11_alerta_sai_e_volta(self):
        # tarefa morta: uma notificação; se sai e volta (o mesmo id) não repete
        self.subscreve()
        self.s.ciclo(estado(), NOW)
        self.assertEqual(self.s.ciclo(estado(alertas=["tarefa:x"]), NOW + 30), 1)
        self.assertEqual(self.s.ciclo(estado(alertas=["tarefa:x"]), NOW + 60), 0)
        self.assertEqual(self.s.ciclo(estado(), NOW + 90), 0)
        self.assertEqual(self.s.ciclo(estado(alertas=["tarefa:x"]), NOW + 120), 0)
        self.assertEqual(len(self.abrir.pedidos), 1)

    def test_follow_up_com_o_mesmo_id_notifica_o_2o_fim(self):
        # regressão: dev_task.py continue reutiliza o id; o 2.º fim tem outro finished
        self.subscreve()
        self.s.ciclo(estado(tasks=[tarefa("t", finished_age=3600)]), NOW)
        self.assertEqual(self.s.ciclo(estado(tasks=[tarefa("t", status="a correr", running=True)]), NOW + 30), 0)
        self.assertEqual(self.s.ciclo(estado(tasks=[tarefa("t", finished_age=-60)]), NOW + 60), 1)
        self.assertEqual([(p["tag"], p["url"]) for p in self.payloads()], [("t", "./#t")])
        self.assertEqual(self.s.ciclo(estado(tasks=[tarefa("t", finished_age=-60)]), NOW + 90), 0)

    def test_ac12_fim_antigo_semeia(self):
        self.subscreve()
        self.s.ciclo(estado(), NOW)
        self.assertEqual(self.s.ciclo(estado(tasks=[tarefa("velha", finished_age=6 * 3600 + 60)]), NOW), 0)
        self.assertIn("fim:dev:velha", curtas(json.loads((self.pasta / "notificadas.json").read_text())["chaves"]))

    def test_ac13_filtros_por_subscricao(self):
        p = push.prefs_omissao()
        p["eventos"]["pr"] = False
        p["squads"]["marketing"] = False
        self.subscreve(prefs=p)
        self.s.ciclo(estado(), NOW)
        st = estado(tasks=[tarefa("pr", status="a correr", running=True, pr="https://github.com/o/r/pull/3"),
                           tarefa("mk", squad="marketing"), tarefa("ok")])
        self.assertEqual(self.s.ciclo(st, NOW), 1)
        self.assertEqual([p["tag"] for p in self.payloads()], ["ok"])

    def test_ac14_silencio_e_criticos(self):
        p = push.prefs_omissao()
        p["silencio"]["ligado"] = True
        self.subscreve(prefs=p)
        noite = ts("2026-11-05T23:30:00+00:00")   # 00:30 em Amesterdão
        self.s.ciclo(estado(), noite)
        st = estado(tasks=[tarefa("ok", finished_age=0)], alertas=["tarefa:x"])
        st["tasks"][0]["finished"] = datetime.fromtimestamp(noite, timezone.utc).isoformat()
        self.assertEqual(self.s.ciclo(st, noite), 1)
        self.assertEqual([p["tag"] for p in self.payloads()], ["x"])
        p["criticos_ignoram_silencio"] = False
        self.subscreve(prefs=p)
        self.assertEqual(self.s.ciclo(estado(alertas=["tarefa:y"]), noite), 0)
        self.assertEqual(self.s.ciclo(estado(alertas=["tarefa:y"]), noite + 3600), 0)   # descartado, não fica em fila

    def test_ac15_404_410_apagam_500_mantem(self):
        self.subscreve(f"{FCM}a")
        self.subscreve(f"{FCM}b")
        self.subscreve(f"{FCM}c")
        self.s.ciclo(estado(), NOW)
        self.abrir.codigos = [404, 410, 500]
        self.assertEqual(self.s.ciclo(estado(alertas=["tarefa:x"]), NOW), 0)
        subs = json.loads((self.pasta / "subscricoes.json").read_text())["subscricoes"]
        self.assertEqual([s["endpoint"] for s in subs], [f"{FCM}c"])
        log = self.out.getvalue()
        self.assertIn("push: 404 fcm.googleapis.com", log)
        self.assertIn("push: 500 fcm.googleapis.com", log)
        self.assertNotIn("/fcm/send", log)
        self.assertNotIn(self.subscricao()["keys"]["auth"], log)

    def test_ac16_oito_eventos(self):
        self.subscreve()
        self.s.ciclo(estado(), NOW)
        st = estado(tasks=[tarefa(f"t{i}") for i in range(7)], alertas=["tarefa:x"])
        self.assertEqual(self.s.ciclo(st, NOW), 6)
        pays = self.payloads()
        self.assertEqual(len(pays), 6)
        self.assertEqual(pays[0]["tag"], "x")   # críticos primeiro
        self.assertEqual(pays[-1]["title"], "Mais 3 avisos")

    def test_ac17_payloads_cifrados_sem_texto(self):
        marca = "MARCADOR-SECRETO"
        p = push.prefs_omissao()
        p["eventos"]["aprovacoes"] = True
        self.subscreve(prefs=p)
        self.s.ciclo(estado(), NOW)
        st = estado(tasks=[tarefa("ok", task=marca, result=marca, cost_usd=9.87)],
                    aprovacoes=[{"fonte": "marketing", "ficheiro": "a.md", "titulo": marca, "resumo": marca}])
        self.assertEqual(self.s.ciclo(st, NOW), 2)
        texto = json.dumps(self.payloads(), ensure_ascii=False)
        self.assertNotIn(marca, texto)
        self.assertNotIn("9.87", texto)

    def test_subscricoes_ilegiveis_no_vigia(self):
        self.subscreve()
        self.s.ciclo(estado(), NOW)
        (self.pasta / "subscricoes.json").write_text('{"v": 2}')
        self.assertEqual(self.s.ciclo(estado(alertas=["tarefa:x"]), NOW), 0)
        self.assertEqual(self.abrir.pedidos, [])
        self.assertIn("tarefa:x", json.loads((self.pasta / "notificadas.json").read_text())["chaves"])
        self.s.ciclo(estado(alertas=["tarefa:x"]), NOW + 30)
        self.assertEqual(self.out.getvalue().count("subscricoes.json ilegível"), 1)   # não repete

    def test_chave_do_browser_invalida_so_loga(self):
        self.s.iniciar()
        self.s.subscrever({"token": "t", "subscricao": sub()}, NOW)   # ponto fora da curva
        self.assertEqual(self.s.teste({"token": "t", "endpoint": FCM}, NOW),
                         (502, {"error": "o serviço de push não respondeu"}))
        self.assertIn("push: ValueError fcm.googleapis.com", self.out.getvalue())

    def test_abridor_por_omissao_nao_segue_redireccoes(self):
        h = push._SemRedireccao()
        self.assertIsNone(h.redirect_request(None, None, 302, "Found", {}, "https://evil.com/"))


@unittest.skipUnless(push.ec, SEM_CRYPTO)
class FasesServicoTest(unittest.TestCase):
    setUp, tearDown, subscricao, subscreve = (ServicoTest.setUp, ServicoTest.tearDown, ServicoTest.subscricao,
                                              ServicoTest.subscreve)
    payloads = ServicoTest.payloads

    def prefs(self, *fases) -> dict:
        p = push.prefs_omissao()
        p["eventos"]["fases"] = True
        p["fases"] = {f: f in fases for f in progresso.FASES}
        return p

    def por_endpoint(self) -> dict:
        out = {}
        for (r, _), pay in zip(self.abrir.pedidos, self.payloads()):
            out.setdefault(r.full_url, []).append(pay)
        return out

    def test_ca10_ca12_ficheiro_antigo_valido_e_completado(self):
        self.s.iniciar()
        p = push.prefs_omissao()
        del p["eventos"]["fases"], p["fases"]
        antigo = json.dumps({"v": 1, "subscricoes": [{**self.subscricao(), "prefs": p, "criado": "x",
                                                      "actualizado": None}]}).encode()
        f = self.pasta / "subscricoes.json"
        f.write_bytes(antigo)
        self.s.ciclo(estado(), NOW)
        self.assertEqual(self.s.ciclo(estado(alertas=["tarefa:x"]), NOW + 30), 1)
        self.assertNotIn("ilegível", self.out.getvalue())
        self.assertEqual(f.read_bytes(), antigo)   # não é reescrito só por ler
        self.assertEqual(self.subscreve(), (200, {"prefs": push.prefs_omissao()}))
        d = json.loads(f.read_text())
        self.assertEqual((d["v"], d["subscricoes"][0]["prefs"]), (1, push.prefs_omissao()))

    def test_ca11_subscrever_forma_antiga_e_nova(self):
        self.s.iniciar()
        p = push.prefs_omissao()
        p["eventos"]["pr"] = False
        del p["eventos"]["fases"], p["fases"]
        esperado = push.normalizar_prefs(p)
        self.assertEqual(esperado["fases"], push.prefs_omissao()["fases"])
        self.assertEqual(self.subscreve(prefs=p), (200, {"prefs": esperado}))
        nova = self.prefs("SPEC")
        self.assertEqual(self.subscreve(prefs=nova), (200, {"prefs": nova}))
        for mexe, msg in ((lambda p: p["fases"].update(X=True), "campos desconhecidos"),
                          (lambda p: p["eventos"].update(fases=1), "preferências inválidas"),
                          (lambda p: p["fases"].update(QA="sim"), "preferências inválidas")):
            m = push.prefs_omissao()
            mexe(m)
            with self.subTest(p=m):
                self.assertEqual(self.subscreve(prefs=m), (400, {"error": msg}))
        self.assertEqual(self.subscreve(), (200, {"prefs": nova}))   # os 400 não mexeram no guardado

    def test_ca15_reinicio_nao_repete_a_fase(self):
        self.subscreve(prefs=self.prefs("PLAN"))
        self.s.ciclo(estado(), NOW)
        st = estado(tasks=[a_correr(n=2)])
        self.assertEqual(self.s.ciclo(st, NOW + 30), 1)
        self.assertEqual([p["title"] for p in self.payloads()], ["📍 Dev · loja: PLAN"])
        nots = json.loads((self.pasta / "notificadas.json").read_text())["chaves"]
        self.assertEqual(set(nots), {"fase:dev:20261105-1010-suporte-caixa:SPEC",
                                     "fase:dev:20261105-1010-suporte-caixa:PLAN"})
        reiniciado = push.Servico(self.pasta, "https://squad.example", AMS, DONE, abrir=self.abrir)
        self.assertEqual(reiniciado.ciclo(st, NOW + 60), 0)
        self.assertEqual(len(self.abrir.pedidos), 1)

    def test_ca14_primeiro_arranque_so_semeia(self):
        self.subscreve(prefs=self.prefs(*progresso.FASES))
        self.assertEqual(self.s.ciclo(estado(tasks=[a_correr(n=3)]), NOW), 0)
        self.assertEqual(len(json.loads((self.pasta / "notificadas.json").read_text())["chaves"]), 3)

    def test_ca18_silencio_descarta_e_marca(self):
        p = self.prefs(*progresso.FASES)
        p["silencio"]["ligado"] = True
        self.subscreve(prefs=p)
        noite = ts("2026-11-05T23:30:00+00:00")
        self.s.ciclo(estado(), noite)
        self.assertEqual(self.s.ciclo(estado(tasks=[a_correr(n=1)]), noite + 30), 0)
        self.assertIn("fase:dev:20261105-1010-suporte-caixa:SPEC",
                      json.loads((self.pasta / "notificadas.json").read_text())["chaves"])
        self.assertEqual(self.s.ciclo(estado(tasks=[a_correr(n=1)]), noite + 8 * 3600), 0)   # não chega depois
        self.assertEqual(self.abrir.pedidos, [])

    def test_ca19_colapso_por_dispositivo(self):
        self.subscreve(f"{FCM}/todas", prefs=self.prefs("SPEC", "PLAN"))
        self.subscreve(f"{FCM}/spec", prefs=self.prefs("SPEC"))
        self.subscreve(f"{FCM}/nada", prefs=self.prefs("QA"))
        self.s.ciclo(estado(), NOW)
        self.assertEqual(self.s.ciclo(estado(tasks=[a_correr(n=2)]), NOW + 30), 2)
        out = {u: [p["title"] for p in ps] for u, ps in self.por_endpoint().items()}
        self.assertEqual(out, {f"{FCM}/todas": ["📍 Dev · loja: PLAN"], f"{FCM}/spec": ["📍 Dev · loja: SPEC"]})
        nots = json.loads((self.pasta / "notificadas.json").read_text())["chaves"]
        self.assertTrue({"fase:dev:20261105-1010-suporte-caixa:SPEC",
                         "fase:dev:20261105-1010-suporte-caixa:PLAN"} <= set(nots))

    def test_ca20_fases_contam_para_o_limite(self):
        self.subscreve(prefs=self.prefs("SPEC"))
        self.s.ciclo(estado(), NOW)
        st = estado(tasks=[tarefa(f"f{i}") for i in range(4)] + [a_correr(f"v{i}", n=1) for i in range(3)])
        self.assertEqual(self.s.ciclo(st, NOW + 30), 6)
        pays = self.payloads()
        self.assertEqual(sum(p["tag"].startswith("fase:") for p in pays[:5]), 1)
        self.assertEqual(pays[-1]["title"], "Mais 2 avisos")


if __name__ == "__main__":
    unittest.main()
