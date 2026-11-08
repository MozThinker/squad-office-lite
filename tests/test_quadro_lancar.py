"""Lançar a partir do Quadro (lancar.py, ADR-005): validação, escrita na fila, ciclo de vida no GET e rota HTTP.

Pastas temporárias e estado falso; só stdlib.
"""
import hashlib
import http.client
import json
import os
import re
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import lancar
import quadro
import server
from tests import cfg

OFFICE_CFG = cfg.LEGADO

TZ = timezone.utc
NOW = datetime(2026, 11, 6, 2, 30, tzinfo=TZ).timestamp()

ROOT = Path(__file__).resolve().parent.parent
PEDIDO = re.compile(r"\d{8}T\d{6}-[0-9a-f]{8}\.json")
CHAVES_PEDIDO = ["v", "id", "cartao_id", "squad", "projecto", "kind", "mode", "slug", "titulo", "brief", "pedido_em"]
PR = "https://github.com/exemplo-org/squad-office/pull/21"
T = "20261106-0231-quadro-lancar"


def cartao(cid, coluna="a-fazer", ordem=10, projecto="fabrica", titulo="Quadro lançar"):
    return {"id": cid, "titulo": titulo, "descricao": "brief do cartão", "projecto": projecto, "coluna": coluna,
            "ordem": ordem, "etiquetas": [], "criado": "2026-11-05T10:00:00+00:00",
            "actualizado": "2026-11-05T10:00:00+00:00"}


def corpo(**kw):
    d = {"token": "t", "versao": "0", "cartao_id": "m-1", "posicao": 0, "squad": "dev", "projecto": "fabrica",
         "kind": "feat", "mode": "goal", "slug": "quadro-lancar", "titulo": "Quadro: lançar", "brief": "faz isto"}
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fila, self.pasta, self.inbox = self.root / "quadro-lancar", self.root / "quadro", self.root / "inbox"
        for d in (self.fila, self.pasta, self.inbox):
            d.mkdir()
        self.f = self.pasta / "cartoes.json"
        lancar._cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def cartoes(self, *cs):
        self.f.write_text(json.dumps({"v": 1, "cartoes": list(cs)}, ensure_ascii=False))

    def versao(self):
        return hashlib.sha256(self.f.read_bytes()).hexdigest() if self.f.exists() else "0"

    def disco(self):
        return {c["id"]: c for c in json.loads(self.f.read_text())["cartoes"]}

    def pedidos(self):
        return sorted(n for n in os.listdir(self.fila) if PEDIDO.fullmatch(n))

    def escrever(self, now=NOW, **kw):
        d = corpo(versao=self.versao(), **kw)
        self.assertIsNone(lancar.validar(d))
        return lancar.escrever(self.fila, self.pasta, d, quadro.chaves(), TZ, now)

    def pedido(self, stem, cid="m-1", squad="dev"):
        doc = {"v": 1, "id": stem, "cartao_id": cid, "squad": squad, "projecto": "fabrica", "kind": "feat", "mode": "task",
               "slug": "abc", "titulo": "x", "brief": "", "pedido_em": "2026-11-06T02:30:00+00:00"}
        (self.fila / f"{stem}.json").write_text(json.dumps(doc))

    def feito(self, stem, doc):
        (self.fila / f"{stem}.feito.json").write_text(doc if isinstance(doc, str) else json.dumps(doc))

    def get(self, tasks=(), waiting=(), now=NOW):
        state = {"tasks": list(tasks), "waiting": list(waiting)}
        out = quadro.resposta(state, {}, self.pasta, self.inbox, TZ, now, "tok")
        lancar.completar(out, self.fila, self.pasta, quadro.chaves(), TZ, now)
        return out

    @staticmethod
    def por_id(out):
        return {c["id"]: c for c in out["cartoes"]}


def dev(tid=T, status="a correr", pr="", progress=None):
    return {"id": tid, "squad": "dev", "status": status, "pr": pr, "project": "fabrica", "mode": "goal",
            "started": "2026-11-06T02:31:00+00:00", "finished": None if status == "a correr" else
            "2026-11-06T03:10:00+00:00", "progress": progress}


def squad(tid, squad_, status):
    return {"id": tid, "squad": squad_, "status": status, "mode": "tarefa", "started": "2026-11-06T02:31:00+00:00",
            "finished": "2026-11-06T03:10:00+00:00"}


# --- validar ---
class ValidarTest(unittest.TestCase):
    def test_validos(self):
        for kw in ({}, {"kind": "fix", "mode": "task", "projecto": "ebook"}, {"projecto": "loja"},
                   {"slug": "a" * 60}, {"slug": "abc"}, {"brief": "é" * 4096}, {"brief": ""}, {"posicao": 999},
                   {"versao": "a" * 64}):
            with self.subTest(kw=kw):
                self.assertIsNone(lancar.validar(corpo(**kw)))

    def test_projectos_sao_ids_da_config(self):
        self.assertEqual(len(quadro.DEV_PROJECTOS), 12)
        self.assertEqual(lancar.PROJECTOS, quadro.DEV_PROJECTOS)
        for p in quadro.DEV_PROJECTOS:
            self.assertIsNone(lancar.validar(corpo(projecto=p)))
        self.assertEqual(lancar.validar(corpo(projecto="Projeto 90")), "projecto inválido")   # nome, não id

    def test_squads_tipos_e_modos_da_config(self):
        cfg.usar(self, cfg.MULTI)
        self.assertEqual((lancar.SQUADS, lancar.KINDS, lancar.MODES),
                         (("dev", "ops"), {"dev": ("feat", "fix"), "ops": ("infra", "fix")},
                          {"dev": ("goal", "task"), "ops": ("task",)}))
        self.assertIsNone(lancar.validar(corpo(squad="ops", projecto="blog", kind="infra", mode="task")))
        for kw, msg in (({"squad": "marketing"}, "squad inválida"),   # formato sessao: não se lança
                        ({"squad": "ops", "projecto": "blog", "kind": "feat", "mode": "task"}, "tipo inválido"),
                        ({"squad": "ops", "projecto": "blog", "kind": "infra", "mode": "goal"}, "modo inválido"),
                        ({"projecto": "fabrica"}, "projecto inválido")):
            with self.subTest(kw=kw):
                self.assertEqual(lancar.validar(corpo(**kw)), msg)

    def test_normaliza(self):
        d = corpo(titulo="  Olá\r\n ", brief="a\r\nb\tc")
        self.assertIsNone(lancar.validar(d))
        self.assertEqual((d["titulo"], d["brief"]), ("Olá", "a\nb\tc"))

    def test_invalidos(self):
        casos = [
            ({"squad": "support"}, "squad inválida"), ({"squad": None}, "squad inválida"),
            ({"squad": ["dev"]}, "squad inválida"), ({"squad": "marketing"}, "squad inválida"),
            ({"projecto": "loja-nova"}, "projecto inválido"), ({"projecto": None}, "projecto inválido"),
            ({"projecto": "../x"}, "projecto inválido"), ({"projecto": "Loja"}, "projecto inválido"),
            ({"kind": "chore"}, "tipo inválido"), ({"kind": None}, "tipo inválido"), ({"kind": "tarefa"}, "tipo inválido"),
            ({"mode": "dry-run"}, "modo inválido"), ({"mode": None}, "modo inválido"),
            ({"slug": "ab"}, "slug inválido"), ({"slug": "-abc"}, "slug inválido"), ({"slug": "abc-"}, "slug inválido"),
            ({"slug": "Abc"}, "slug inválido"), ({"slug": "a/b"}, "slug inválido"), ({"slug": "../x"}, "slug inválido"),
            ({"slug": "a" * 61}, "slug inválido"), ({"slug": "abc\n"}, "slug inválido"), ({"slug": 123}, "slug inválido"),
            ({"brief": "a" * 8193}, "brief com mais de 8 KB"), ({"brief": "é" * 4097}, "brief com mais de 8 KB"),
            ({"brief": "a\x00b"}, "brief inválido"), ({"brief": None}, "campos inválidos"),
            ({"titulo": ""}, "título obrigatório"), ({"titulo": "  \r\n "}, "título obrigatório"),
            ({"titulo": "x" * 121}, "título com mais de 120 caracteres"), ({"titulo": "a\nb"}, "título inválido"),
            ({"titulo": 1}, "campos inválidos"),
            ({"cartao_id": "../x"}, "campos inválidos"), ({"cartao_id": "a/b"}, "campos inválidos"),
            ({"cartao_id": "dev:x"}, "campos inválidos"), ({"cartao_id": ""}, "campos inválidos"),
            ({"posicao": -1}, "campos inválidos"), ({"posicao": True}, "campos inválidos"),
            ({"posicao": 1.5}, "campos inválidos"), ({"versao": "v1"}, "campos inválidos"),
            ({"titulo": "\ud800"}, "JSON inválido"),
        ]
        for kw, msg in casos:
            with self.subTest(kw=kw):
                self.assertEqual(lancar.validar(corpo(**kw)), msg)

    def test_invisiveis_rejeitados(self):
        # Cf (bidi, tags, zero-width), Co (uso privado), Cn (não atribuído); Cs já cai em «JSON inválido»
        tags = "".join(chr(0xE0000 + ord(c)) for c in "ignora")
        for ch in ("\u202e", "\u202d", "\u2066", "\u200b", "\u200e", "\u00ad", "\ufeff", "\U000e0001", tags,
                   "\U000e007f", "\ue000", "\U000f0000"):
            with self.subTest(ch=ascii(ch)):
                self.assertEqual(lancar.validar(corpo(titulo=f"ok{ch}x")), "título inválido")
                self.assertEqual(lancar.validar(corpo(brief=f"faz isto{ch}\n")), "brief inválido")
        self.assertEqual(lancar.validar(corpo(titulo="\ud800")), "JSON inválido")

    def test_zwj_e_texto_visivel_passam(self):
        for txt in ("família \U0001f468\u200d\U0001f469\u200d\U0001f467", "❤\ufe0f olá", "مرحبا", "日本語 ção",
                    "a\u200db", "emoji novo \U0001fae9 \U0001faea", "x\u0378"):
            with self.subTest(txt=ascii(txt)):
                self.assertIsNone(lancar.validar(corpo(titulo=txt, brief=f"{txt}\n\tfim")))

    def test_chaves_exactas(self):
        for extra in ("path", "cmd", "op", "id"):
            with self.subTest(extra=extra):
                self.assertEqual(lancar.validar(corpo(**{extra: "x"})), "campos inválidos")
        d = corpo()
        del d["mode"]
        self.assertEqual(lancar.validar(d), "campos inválidos")


# --- escrever (AC14, 17, 24, 27) ---
class EscreverTest(Base):
    def test_dev_cria_um_pedido_e_move(self):
        self.cartoes(cartao("m-1"), cartao("m-2", "em-curso", 10))
        code, out = self.escrever(posicao=0)
        self.assertEqual(code, 202)
        self.assertEqual(self.pedidos(), [out["id"] + ".json"])
        doc = json.loads((self.fila / (out["id"] + ".json")).read_text())
        self.assertEqual(list(doc), CHAVES_PEDIDO)
        self.assertEqual(doc, {"v": 1, "id": out["id"], "cartao_id": "m-1", "squad": "dev", "projecto": "fabrica",
                               "kind": "feat", "mode": "goal", "slug": "quadro-lancar", "titulo": "Quadro: lançar",
                               "brief": "faz isto", "pedido_em": "2026-11-06T02:30:00+00:00"})
        self.assertRegex(out["id"], r"^20261106T023000-[0-9a-f]{8}$")
        self.assertEqual((out["movido"], out["versao"]), (True, self.versao()))
        d = self.disco()
        self.assertEqual((d["m-1"]["coluna"], d["m-1"]["ordem"], d["m-2"]["ordem"]), ("em-curso", 10, 20))
        m1 = next(c for c in out["cartoes"] if c["id"] == "m-1")
        self.assertEqual(m1["lancamento"], {"id": out["id"], "estado": "a-lancar", "squad": "dev", "task_id": None,
                                            "erro": None, "pedido_em": "2026-11-06T02:30:00+00:00"})
        self.assertIsNone(next(c for c in out["cartoes"] if c["id"] == "m-2")["lancamento"])
        self.assertEqual([n for n in os.listdir(self.fila) if n.endswith(".tmp")], [])

    def test_outra_squad_worktree_grava_os_seus_valores(self):
        cfg.usar(self, cfg.MULTI)
        self.cartoes(cartao("m-1", projecto="loja"), cartao("m-2", projecto="blog"))
        self.escrever(projecto="loja")
        self.escrever(cartao_id="m-2", squad="ops", projecto="blog", kind="infra", mode="task")
        docs = [json.loads((self.fila / n).read_text()) for n in self.pedidos()]
        self.assertEqual(sorted((d["squad"], d["projecto"], d["kind"], d["mode"]) for d in docs),
                         [("dev", "loja", "feat", "goal"), ("ops", "blog", "infra", "task")])

    def test_erros_nao_escrevem(self):
        self.cartoes(cartao("m-1"))
        d = corpo(versao=self.versao(), cartao_id="m-9")
        self.assertEqual(lancar.escrever(self.fila, self.pasta, d, quadro.chaves(), TZ, NOW),
                         (404, {"error": "cartão não encontrado"}))
        d = corpo(versao="0")
        self.assertEqual(lancar.escrever(self.fila, self.pasta, d, quadro.chaves(), TZ, NOW),
                         (409, {"error": "conflito", "versao": self.versao()}))
        d = corpo(versao=self.versao())
        self.assertEqual(lancar.escrever(self.root / "nao", self.pasta, d, quadro.chaves(), TZ, NOW)[0], 503)
        self.f.write_text("{")
        self.assertEqual(lancar.escrever(self.fila, self.pasta, corpo(versao=self.versao()), quadro.chaves(), TZ, NOW)[0], 503)
        self.assertEqual(os.listdir(self.fila), [])

    def test_fila_sem_escrita(self):
        self.cartoes(cartao("m-1"))
        with mock.patch("os.access", return_value=False):
            self.assertEqual(self.escrever(), (503, {"error": "fila indisponível"}))
        self.assertEqual(os.listdir(self.fila), [])

    def test_ja_lancado_e_relancar_depois_de_erro(self):
        self.cartoes(cartao("m-1"))
        code, out = self.escrever()
        self.assertEqual(code, 202)
        self.assertEqual(self.escrever(now=NOW + 1), (409, {"error": "já lançado"}))
        self.feito(out["id"], {"task_id": T})   # ligado também conta como activo
        self.assertEqual(self.escrever(now=NOW + 2), (409, {"error": "já lançado"}))
        self.assertEqual(len(self.pedidos()), 1)

    def test_erro_deixa_relancar(self):
        self.cartoes(cartao("m-1"))
        _, out = self.escrever()
        self.feito(out["id"], {"erro": "falhou"})
        code, out2 = self.escrever(now=NOW + 60)
        self.assertEqual(code, 202)
        self.assertEqual(len(self.pedidos()), 2)
        self.assertEqual(next(c for c in out2["cartoes"] if c["id"] == "m-1")["lancamento"]["estado"], "a-lancar")
        self.assertEqual(self.escrever(now=NOW + 120), (409, {"error": "já lançado"}))   # conta o mais recente

    def test_mudanca_da_hora_nao_esconde_o_relancamento(self):
        # 25/10: 02:30 CEST (00:30Z) falha; relança às 02:10 CET (01:10Z), nome menor mas pedido_em mais recente
        ams = ZoneInfo("Europe/Amsterdam")
        t1 = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).timestamp()
        self.cartoes(cartao("m-1"))
        d = corpo(versao=self.versao())
        self.assertIsNone(lancar.validar(d))
        code, out = lancar.escrever(self.fila, self.pasta, d, quadro.chaves(), ams, t1)
        self.assertEqual((code, out["id"][:15]), (202, "20261025T023000"))
        self.feito(out["id"], {"erro": "falhou"})
        d = corpo(versao=self.versao())
        lancar.validar(d)
        code, out2 = lancar.escrever(self.fila, self.pasta, d, quadro.chaves(), ams, t1 + 40 * 60)
        self.assertEqual((code, out2["id"][:15]), (202, "20261025T021000"))
        self.assertLess(out2["id"], out["id"])
        self.assertEqual(next(c for c in out2["cartoes"] if c["id"] == "m-1")["lancamento"]["id"], out2["id"])
        d = corpo(versao=self.versao())
        lancar.validar(d)
        self.assertEqual(lancar.escrever(self.fila, self.pasta, d, quadro.chaves(), ams, t1 + 50 * 60),
                         (409, {"error": "já lançado"}))
        self.assertEqual(len(self.pedidos()), 2)

    def test_ultimos_por_pedido_em_com_fuso(self):
        a, b, c = "20261025T023000-aaaaaaaa", "20261025T021000-bbbbbbbb", "20261025T021000-cccccccc"
        p = {a: {"cartao_id": "m-1", "pedido_em": "2026-10-25T02:30:00+02:00"},
             b: {"cartao_id": "m-1", "pedido_em": "2026-10-25T02:10:00+01:00"}}
        self.assertEqual(lancar._ultimos(p)["m-1"][0], b)
        self.assertEqual(lancar._ultimos(dict(reversed(p.items())))["m-1"][0], b)
        p[c] = {"cartao_id": "m-1", "pedido_em": "2026-10-25T01:10:00+00:00"}   # mesmo instante: desempata o nome
        self.assertEqual(lancar._ultimos(p)["m-1"][0], c)
        for mau in ("lixo", "2026-10-25T09:00:00"):   # ilegível ou sem fuso conta como o mais antigo
            q = {a: {"cartao_id": "m-1", "pedido_em": mau}, b: p[b]}
            self.assertEqual(lancar._ultimos(q)["m-1"][0], b)

    def test_fila_cheia(self):
        self.cartoes(cartao("m-1"), cartao("m-2"))
        for i in range(10):
            self.pedido(f"20261105T1000{i:02d}-0000000{i}", cid=f"x-{i}")
        self.assertEqual(self.escrever(), (429, {"error": "fila cheia"}))
        self.feito("20261105T100000-00000000", {"task_id": "abc"})   # 9 pendentes: passa
        self.assertEqual(self.escrever()[0], 202)
        d = corpo(versao=self.versao(), cartao_id="m-2")
        self.assertEqual(lancar.escrever(self.fila, self.pasta, d, quadro.chaves(), TZ, NOW)[0], 429)

    def test_nunca_sobrescreve(self):
        # nome repetido (mesmo segundo e mesmo hex): os.link falha com EEXIST e tenta outro hex
        self.cartoes(cartao("m-1"))
        self.pedido("20261106T023000-aaaaaaaa", cid="x-1")
        antes = (self.fila / "20261106T023000-aaaaaaaa.json").read_bytes()
        with mock.patch("secrets.token_hex", side_effect=["aaaaaaaa", "bbbbbbbb"]):
            code, out = self.escrever()
        self.assertEqual((code, out["id"]), (202, "20261106T023000-bbbbbbbb"))
        self.assertEqual((self.fila / "20261106T023000-aaaaaaaa.json").read_bytes(), antes)
        with mock.patch("secrets.token_hex", return_value="aaaaaaaa"):
            self.cartoes(cartao("m-2"))
            self.assertEqual(self.escrever(cartao_id="m-2")[0], 503)
        self.assertEqual((self.fila / "20261106T023000-aaaaaaaa.json").read_bytes(), antes)

    def test_falha_a_mover_da_202_sem_movido(self):
        self.cartoes(cartao("m-1"))
        with mock.patch("quadro._gravar", side_effect=OSError):
            code, out = self.escrever()
        self.assertEqual((code, out["movido"]), (202, False))
        self.assertEqual(self.disco()["m-1"]["coluna"], "a-fazer")
        self.assertEqual(len(self.pedidos()), 1)
        self.assertEqual(out["cartoes"][0]["lancamento"]["estado"], "a-lancar")

    def test_falha_a_escrever_nao_move(self):
        self.cartoes(cartao("m-1"))
        with mock.patch("os.link", side_effect=OSError):
            self.assertEqual(self.escrever(), (503, {"error": "fila indisponível"}))
        self.assertEqual(self.disco()["m-1"]["coluna"], "a-fazer")
        self.assertEqual(os.listdir(self.fila), [])


# --- leitura da fila ---
class FilaTest(Base):
    def test_ignora_o_que_nao_e_pedido(self):
        stem = "20261106T023000-a1b2c3d4"
        self.pedido(stem)
        (self.fila / "20261106T023001-a1b2c3d4.json").write_text("{")                         # ilegível
        (self.fila / "20261106T023002-a1b2c3d4.json").write_text(json.dumps({"v": 1}))         # forma errada
        (self.fila / "20261106T023003-a1b2c3d4.json").write_text("x" * (lancar.PEDIDO_MAX + 1))
        os.symlink(self.fila / f"{stem}.json", self.fila / "20261106T023004-a1b2c3d4.json")
        os.mkfifo(self.fila / "20261106T023005-a1b2c3d4.json")
        (self.fila / "20261106T023006-a1b2c3d4.json.tmp").write_text("{}")
        (self.fila / "outro.json").write_text("{}")
        doc = json.loads((self.fila / f"{stem}.json").read_text())
        (self.fila / "20261106T023007-a1b2c3d4.json").write_text(json.dumps({**doc, "id": stem}))   # id ≠ nome
        pedidos, _ = lancar.ler_fila(self.fila)
        self.assertEqual(list(pedidos), [stem])

    def test_fila_ausente(self):
        self.assertEqual(lancar.ler_fila(self.root / "nao"), ({}, {}))

    def test_cache_nao_reabre(self):
        for i in range(3):
            self.pedido(f"20261106T02300{i}-a1b2c3d{i}")
        lancar.ler_fila(self.fila)
        real = os.open
        with mock.patch("os.open", side_effect=real) as m:
            self.assertEqual(len(lancar.ler_fila(self.fila)[0]), 3)
        self.assertEqual(m.call_count, 0)

    def test_so_os_mais_recentes(self):
        with mock.patch.object(lancar, "FILA_N", 2):
            for i in range(4):
                self.pedido(f"20261106T02300{i}-a1b2c3d{i}", cid=f"m-{i}")
            self.assertEqual(list(lancar.ler_fila(self.fila)[0]), ["20261106T023003-a1b2c3d3", "20261106T023002-a1b2c3d2"])


# --- ciclo de vida no GET (AC19–23, 25, 26) ---
class CicloTest(Base):
    def lancado(self, cid="m-1", **kw):
        self.cartoes(*kw.pop("cs", [cartao(cid)]))
        code, out = self.escrever(cartao_id=cid, **kw)
        self.assertEqual(code, 202)
        return out["id"]

    def test_a_lancar(self):
        self.lancado()
        out = self.get()
        self.assertEqual((out["lancar"], out["dev_projectos"]), (True, list(quadro.DEV_PROJECTOS)))
        c = self.por_id(out)["m-1"]
        self.assertEqual((c["coluna"], c["lancamento"]["estado"], c["lancamento"]["task_id"]), ("em-curso", "a-lancar", None))
        self.assertEqual(os.listdir(self.fila), [c["lancamento"]["id"] + ".json"])   # o GET não escreve

    def test_sem_pasta_lancar_false(self):
        self.cartoes(cartao("m-1"))
        out = quadro.resposta({}, {}, self.pasta, self.inbox, TZ, NOW, "tok")
        lancar.completar(out, self.root / "nao", self.pasta, quadro.chaves(), TZ, NOW)
        self.assertEqual((out["lancar"], self.por_id(out)["m-1"]["lancamento"]), (False, None))

    def test_ligado_herda_e_esconde(self):
        stem = self.lancado()
        self.feito(stem, {"task_id": T})
        prog = {"fase": "PLAN", "pct": 40}
        out = self.get([dev(progress=prog)])
        ids = self.por_id(out)
        self.assertNotIn(f"dev:{T}", ids)
        c = ids["m-1"]
        self.assertEqual(c["lancamento"]["estado"], "ligado")
        self.assertEqual((c["lancamento"]["task_id"], c["progress"], c["links"]["tarefa"], c["coluna"]),
                         (T, prog, T, "em-curso"))
        self.assertFalse((self.fila / f"{stem}.movido.json").exists())

    def test_ligado_sem_automatico(self):
        stem = self.lancado()
        self.feito(stem, {"task_id": T})
        c = self.por_id(self.get())["m-1"]
        self.assertEqual((c["lancamento"]["estado"], c["progress"], c["links"]["tarefa"]), ("ligado", None, None))

    def fim(self, task, coluna, squad_="dev"):
        stem = self.lancado(squad=squad_, **({} if squad_ == "dev" else
                                              {"projecto": "loja", "kind": "infra", "mode": "task",
                                               "cs": [cartao("m-1", projecto="loja")]}))
        self.feito(stem, {"task_id": T})
        out = self.get([task])
        c = self.por_id(out)["m-1"]
        self.assertEqual(c["coluna"], coluna)
        self.assertEqual(self.disco()["m-1"]["coluna"], coluna)
        self.assertEqual(out["versao"], self.versao())
        marca = json.loads((self.fila / f"{stem}.movido.json").read_text())
        self.assertEqual(set(marca), {"v", "coluna", "em"})
        self.assertEqual((marca["v"], marca["coluna"]), (1, coluna))
        self.assertNotIn(f"{squad_}:{T}", self.por_id(out))
        return stem

    def test_pr_aberto_vai_para_a_espera(self):
        self.fim(dev(status="concluída", pr=f"{PR} (open)"), "a-espera")

    def test_merged_vai_para_feito(self):
        self.fim(dev(status="concluída", pr=f"{PR} (merged)"), "feito")

    def test_selo_vermelho_vai_para_a_espera(self):
        self.fim(dev(status="erro"), "a-espera")

    def test_outra_squad_falhou_vai_para_a_espera(self):
        cfg.usar(self, cfg.MULTI)
        self.fim({**dev(status="incompleta"), "squad": "ops"}, "a-espera", "ops")

    def test_outra_squad_concluida_vai_para_feito(self):
        cfg.usar(self, cfg.MULTI)
        self.fim({**dev(status="concluída", pr=f"{PR} (merged)"), "squad": "ops"}, "feito", "ops")

    def test_concluida_sem_pr_vai_para_a_espera(self):  # Lite: repo sem remoto, trabalho num branch local por rever
        self.fim(dev(status="concluída"), "a-espera")

    def test_a_correr_ou_parada_nao_move(self):
        stem = self.lancado()
        self.feito(stem, {"task_id": T})
        for st in ("a correr", "parada?", "desconhecido"):
            self.assertEqual(self.por_id(self.get([dev(status=st)]))["m-1"]["coluna"], "em-curso")
        self.assertFalse((self.fila / f"{stem}.movido.json").exists())

    def test_move_uma_vez_so(self):
        stem = self.fim(dev(status="concluída", pr=f"{PR} (merged)"), "feito")
        # o Ana volta a pôr o cartão em «Em curso» à mão: o GET seguinte não o move
        code, _ = quadro.escrever(self.pasta, {"op": "mover", "versao": self.versao(), "id": "m-1", "coluna": "em-curso",
                                               "posicao": 0}, quadro.chaves(), TZ, NOW)
        self.assertEqual(code, 200)
        marca = (self.fila / f"{stem}.movido.json").read_bytes()
        for _ in range(2):
            self.assertEqual(self.por_id(self.get([dev(status="concluída", pr=f"{PR} (merged)")]))["m-1"]["coluna"],
                             "em-curso")
        self.assertEqual((self.fila / f"{stem}.movido.json").read_bytes(), marca)

    def test_tirado_de_em_curso_antes_do_fim_fica(self):
        stem = self.lancado()
        self.feito(stem, {"task_id": T})
        quadro.escrever(self.pasta, {"op": "mover", "versao": self.versao(), "id": "m-1", "coluna": "ideias",
                                     "posicao": 0}, quadro.chaves(), TZ, NOW)
        v = self.versao()
        out = self.get([dev(status="concluída", pr=f"{PR} (merged)")])
        self.assertEqual((self.por_id(out)["m-1"]["coluna"], self.versao()), ("ideias", v))
        self.assertIsNone(json.loads((self.fila / f"{stem}.movido.json").read_text())["coluna"])

    def test_erro_vai_para_a_espera_uma_vez_e_relanca(self):
        stem = self.lancado()
        self.feito(stem, {"erro": "sem créditos " + "x" * 300})
        c = self.por_id(self.get())["m-1"]
        self.assertEqual((c["coluna"], c["lancamento"]["estado"]), ("a-espera", "erro"))
        self.assertLessEqual(len(c["lancamento"]["erro"]), 200)
        self.assertTrue(c["lancamento"]["erro"].startswith("sem créditos"))
        self.assertEqual(json.loads((self.fila / f"{stem}.movido.json").read_text())["coluna"], "a-espera")
        code, out = self.escrever(cartao_id="m-1", now=NOW + 60)
        self.assertEqual(code, 202)
        c = self.por_id(self.get(now=NOW + 61))["m-1"]
        self.assertEqual((c["coluna"], c["lancamento"]["estado"], c["lancamento"]["id"]), ("em-curso", "a-lancar", out["id"]))

    def test_feito_ilegivel_conta_como_erro(self):
        for doc in ("{", json.dumps({"task_id": "../x"}), json.dumps({"task_id": T, "mais": 1}), json.dumps([1]),
                    json.dumps({"erro": 1}), json.dumps({"erro": "x" * 5000})):
            with self.subTest(doc=doc[:30]):
                for n in os.listdir(self.fila):
                    os.unlink(self.fila / n)
                lancar._cache.clear()
                stem = self.lancado()
                self.feito(stem, doc)
                lanc = self.por_id(self.get())["m-1"]["lancamento"]
                self.assertEqual((lanc["estado"], lanc["erro"]), ("erro", lancar.ILEGIVEL))

    def test_feito_symlink_e_ilegivel(self):
        stem = self.lancado()
        alvo = self.root / "fora.json"
        alvo.write_text(json.dumps({"task_id": T}))
        os.symlink(alvo, self.fila / f"{stem}.feito.json")
        self.assertEqual(self.por_id(self.get())["m-1"]["lancamento"]["erro"], lancar.ILEGIVEL)

    def test_movido_symlink_nao_conta(self):
        stem = self.lancado()
        self.feito(stem, {"erro": "x"})
        os.symlink(self.root / "nada", self.fila / f"{stem}.movido.json")
        self.assertEqual(self.por_id(self.get())["m-1"]["coluna"], "em-curso")   # existe (lexists): não move nem marca

    def test_sem_escrita_no_quadro_nao_move(self):
        stem = self.lancado()
        self.feito(stem, {"erro": "x"})
        with mock.patch("quadro._escrita", side_effect=lambda p: p != self.pasta):
            out = self.get()
        self.assertEqual(self.por_id(out)["m-1"]["coluna"], "em-curso")
        self.assertFalse((self.fila / f"{stem}.movido.json").exists())

    def test_gravar_falha_sem_marca(self):
        stem = self.lancado()
        self.feito(stem, {"erro": "x"})
        with mock.patch("quadro._gravar", side_effect=OSError):
            self.assertEqual(self.por_id(self.get())["m-1"]["coluna"], "em-curso")
        self.assertFalse((self.fila / f"{stem}.movido.json").exists())
        self.assertEqual(self.por_id(self.get())["m-1"]["coluna"], "a-espera")   # repete no GET seguinte

    def test_dois_manuais_ligados_e_apagar(self):
        T2 = "20261106-0232-outro"
        self.cartoes(cartao("m-1"), cartao("m-2"))
        _, o1 = self.escrever(cartao_id="m-1")
        _, o2 = self.escrever(cartao_id="m-2", now=NOW + 1)
        self.feito(o1["id"], {"task_id": T})
        self.feito(o2["id"], {"task_id": T2})
        tasks = [dev(T), dev(T2)]
        ids = self.por_id(self.get(tasks))
        self.assertFalse({f"dev:{T}", f"dev:{T2}"} & set(ids))
        quadro.escrever(self.pasta, {"op": "apagar", "versao": self.versao(), "id": "m-1"}, quadro.chaves(), TZ, NOW)
        ids = self.por_id(self.get(tasks))
        self.assertIn(f"dev:{T}", ids)
        self.assertNotIn(f"dev:{T2}", ids)

    def test_esconde_o_da_tua_espera(self):
        stem = self.lancado()
        self.feito(stem, {"task_id": T})
        out = self.get(waiting=[{"id": T, "squad": "dev", "pr_url": PR, "project": "fabrica", "status": "concluída"}])
        self.assertNotIn(f"dev:{T}", self.por_id(out))
        self.assertEqual(self.por_id(out)["m-1"]["coluna"], "a-espera")

    def test_automaticos_sem_lancamento_e_ordem(self):
        stem = self.lancado()
        self.feito(stem, {"task_id": "outra"})
        out = self.get([dev()])
        auto = self.por_id(out)[f"dev:{T}"]
        self.assertNotIn("lancamento", auto)
        self.assertEqual(out["cartoes"], quadro.ordenar(out["cartoes"], NOW))


# --- HTTP (AC14–18) ---
class LancarHttpTest(Base):
    GLOBAIS = ("TASKS", "CLAUDE", "SQUADS_DIR", "APROVACOES", "FILA", "QUADRO", "LANCAR")

    def setUp(self):
        super().setUp()
        r = self.root
        tasks = r / "tasks"
        tasks.mkdir()
        self.old = tuple(getattr(server, g) for g in self.GLOBAIS)
        (server.TASKS, server.CLAUDE, server.SQUADS_DIR, server.APROVACOES, server.FILA, server.QUADRO,
         server.LANCAR) = (tasks, r / "claude", r / "squads", {}, r / "fila", self.pasta, self.fila)
        server._cache["at"] = 0.0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"
        self.cartoes(cartao("m-1"))

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for g, v in zip(self.GLOBAIS, self.old):
            setattr(server, g, v)
        server._cache["at"] = 0.0
        super().tearDown()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection(self.host, timeout=5)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"null")
        finally:
            c.close()

    def post(self, data=None, raw=None, **headers):
        h = {"Content-Type": "application/json", "Origin": f"http://{self.host}"}
        h.update({k.replace("_", "-"): v for k, v in headers.items()})
        h = {k: v for k, v in h.items() if v is not None}
        return self.req("POST", "/api/quadro/lancar", raw if raw is not None else json.dumps(data).encode(), h)

    def bom(self, **kw):
        d = corpo(token=server.TOKEN, versao=self.versao())
        d.update(kw)
        return d

    def arvore(self):
        return {os.path.join(d, f) for d, _, fs in os.walk(self.root) for f in fs}

    def test_202_e_get(self):
        code, out = self.post(self.bom())
        self.assertEqual(code, 202)
        self.assertEqual(set(out), {"id", "versao", "movido", "cartoes"})
        self.assertEqual(self.pedidos(), [out["id"] + ".json"])
        code, q = self.req("GET", "/api/quadro")
        self.assertEqual((code, q["lancar"], q["versao"]), (200, True, out["versao"]))
        self.assertIn("fabrica", q["dev_projectos"])
        c = next(c for c in q["cartoes"] if c["id"] == "m-1")
        self.assertEqual((c["coluna"], c["lancamento"]["estado"]), ("em-curso", "a-lancar"))
        self.assertEqual(self.post(self.bom(versao=out["versao"])), (409, {"error": "já lançado"}))

    def test_hostis_nao_escrevem_fora_da_fila(self):
        antes = self.arvore()
        casos = [
            (400, self.bom(squad="support")), (400, self.bom(projecto="loja-nova")),
            (400, self.bom(squad="marketing", projecto="Loja", kind="tarefa", mode=None)),
            (400, self.bom(kind="chore")), (400, self.bom(squad="rnd", projecto=None, kind="estudo", mode="goal")),
            *[(400, self.bom(slug=s)) for s in ("ab", "-abc", "Abc", "a/b", "../x", "a" * 61, "..", "/etc/passwd")],
            (400, self.bom(brief="a" * 8193)), (400, self.bom(titulo="")), (400, self.bom(path="/tmp/x")),
            (400, self.bom(cmd="rm -rf /")), (400, self.bom(cartao_id="../x")), (400, self.bom(cartao_id="/etc")),
            (400, self.bom(cartao_id="..")), (400, self.bom(id="../../x")),
            (403, self.bom(token="errado")), (403, {k: v for k, v in self.bom().items() if k != "token"}),
        ]
        for code, d in casos:
            with self.subTest(d={k: str(v)[:20] for k, v in d.items() if k != "token"}):
                self.assertEqual(self.post(d)[0], code)
        self.assertEqual(self.post(self.bom(), Origin="https://evil.example")[0], 403)
        self.assertEqual(self.post(self.bom(), Origin="null")[0], 403)
        self.assertEqual(self.post(self.bom(), Origin=None, Sec_Fetch_Site="cross-site")[0], 403)
        self.assertEqual(self.post(self.bom(), Content_Type="text/plain")[0], 403)
        self.assertEqual(self.post(raw=b"{" + b" " * 32768 + b"}")[0], 413)
        self.assertEqual(self.post(raw=b"nao json")[0], 400)
        self.assertEqual(self.post(raw=b"[]")[0], 400)
        self.assertEqual(self.arvore(), antes)
        self.assertEqual(self.post(self.bom(cartao_id="m-9")), (404, {"error": "cartão não encontrado"}))
        self.assertEqual(self.post(self.bom(versao="0"))[0], 409)
        self.assertEqual(self.arvore(), antes)
        # um corpo válido acrescenta só o pedido (nome do servidor) e o cartoes.json
        self.assertEqual(self.post(self.bom())[0], 202)
        novos = self.arvore() - antes
        self.assertEqual(len(novos), 1)
        n = Path(novos.pop())
        self.assertEqual(n.parent, self.fila)
        self.assertRegex(n.name, PEDIDO)

    def test_corpo_ate_32k_passa_o_tamanho(self):
        d = self.bom(brief="é" * 4096)   # 8 KB de brief, JSON ≈ 8 KB (> QUADRO_POST_MAX não importa aqui)
        self.assertEqual(self.post(d)[0], 202)

    def test_sem_pasta_503(self):
        server.LANCAR = self.root / "nao"
        self.assertEqual(self.post(self.bom()), (503, {"error": "fila indisponível"}))
        self.assertFalse((self.root / "nao").exists())
        code, q = self.req("GET", "/api/quadro")
        self.assertEqual((code, q["lancar"]), (200, False))

    def test_fila_cheia_429(self):
        for i in range(10):
            self.pedido(f"20261105T1000{i:02d}-0000000{i}", cid=f"x-{i}")
        self.assertEqual(self.post(self.bom()), (429, {"error": "fila cheia"}))

    def test_rota_get_404(self):
        # a rota nova é só POST
        c = http.client.HTTPConnection(self.host, timeout=5)
        try:
            c.request("GET", "/api/quadro/lancar")
            self.assertEqual(c.getresponse().status, 404)
        finally:
            c.close()


# --- QA: concorrência, máscara e pior caso do tamanho do corpo ---
class QaConcorrenciaTest(Base):
    def test_posts_simultaneos_lancam_uma_vez(self):
        # AC24: oito POST /lancar ao mesmo tempo para o mesmo cartão → um só 202 e um só pedido na fila
        self.cartoes(cartao("m-1"))
        v, codes, ini = self.versao(), [], threading.Barrier(8)

        def um():
            d = corpo(versao=v)
            self.assertIsNone(lancar.validar(d))
            ini.wait()
            codes.append(lancar.escrever(self.fila, self.pasta, d, quadro.chaves(), TZ, NOW)[0])
        ts = [threading.Thread(target=um) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(codes), [202] + [409] * 7)
        self.assertEqual(len(self.pedidos()), 1)

    def test_relancar_com_versao_nova_continua_409(self):
        # AC24: mesmo com a versão certa (sem conflito), um cartão com pedido activo não lança outra vez
        self.cartoes(cartao("m-1"))
        self.assertEqual(self.escrever()[0], 202)
        for i in range(3):
            self.assertEqual(self.escrever(now=NOW + i + 1), (409, {"error": "já lançado"}))
        self.assertEqual(len(self.pedidos()), 1)

    def test_gets_simultaneos_no_fim_movem_uma_vez(self):
        # D11: vários GET em paralelo no fim da execução → o cartão move uma vez e há uma só marca
        self.cartoes(cartao("m-1"), cartao("m-2", "a-espera", 10))
        _, out = self.escrever()
        self.feito(out["id"], {"task_id": T})
        tasks, ini, res = [dev(status="concluída", pr=f"{PR} (merged)")], threading.Barrier(6), []

        def um():
            ini.wait()
            res.append(self.por_id(self.get(tasks))["m-1"]["coluna"])
        ts = [threading.Thread(target=um) for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        # quem perde a corrida devolve a fotografia lida antes do movimento (em-curso); o GET seguinte já converge
        self.assertIn("feito", res)
        self.assertLessEqual(set(res), {"feito", "em-curso"})
        self.assertEqual(self.por_id(self.get(tasks))["m-1"]["coluna"], "feito")
        self.assertEqual(sorted(n for n in os.listdir(self.fila) if n.endswith(".movido.json")),
                         [out["id"] + ".movido.json"])
        d = self.disco()
        self.assertEqual((d["m-1"]["coluna"], d["m-1"]["ordem"], d["m-2"]["coluna"]), ("feito", 10, "a-espera"))

    def test_erro_do_host_sai_mascarado(self):
        # §4: o {erro} do host é mostrado mascarado (e cortado a 200)
        self.cartoes(cartao("m-1"))
        _, out = self.escrever()
        self.feito(out["id"], {"erro": "gh falhou: ghp_" + "a" * 36})
        lanc = self.por_id(self.get())["m-1"]["lancamento"]
        self.assertEqual(lanc["estado"], "erro")
        self.assertNotIn("ghp_", lanc["erro"])
        self.assertIn("[segredo]", lanc["erro"])

    def test_cartao_apagado_com_pedido_pendente_nao_parte_o_get(self):
        # o Ana apaga um cartão com pedido pendente: o GET continua bom e o pedido só conta para o limite da fila
        self.cartoes(cartao("m-1"), cartao("m-2"))
        _, out = self.escrever()
        self.feito(out["id"], {"task_id": T})
        quadro.escrever(self.pasta, {"op": "apagar", "versao": self.versao(), "id": "m-1"}, quadro.chaves(), TZ, NOW)
        ids = self.por_id(self.get([dev()]))
        self.assertNotIn("m-1", ids)
        self.assertIn(f"dev:{T}", ids)   # sem manual ligado, o automático volta
        self.assertIsNone(ids["m-2"]["lancamento"])


class QaHttpTest(LancarHttpTest):
    def test_brief_de_8k_no_pior_caso_de_escape_cabe_no_limite(self):
        # AC16/§4: 8 KB de brief com o JSON mais inchado (aspas, \u escapado, emoji em surrogates) ainda passa os 32 KB
        briefs = ['"' * 8192, "\\" * 8192, "\t" * 8192, "é" * 4096, "\U0001F600" * 2048]
        self.cartoes(*[cartao(f"m-{i}") for i in range(len(briefs))])
        for i, b in enumerate(briefs):
            with self.subTest(i=i):
                raw = json.dumps(self.bom(cartao_id=f"m-{i}", brief=b), ensure_ascii=True).encode()
                self.assertLessEqual(len(raw), server.LANCAR_POST_MAX)
                self.assertEqual(self.post(raw=raw)[0], 202)
        self.assertEqual(len(self.pedidos()), len(briefs))

    def test_brief_acima_de_8k_por_http_400(self):
        self.assertEqual(self.post(self.bom(brief="\U0001F600" * 2049)), (400, {"error": "brief com mais de 8 KB"}))
        self.assertEqual(self.pedidos(), [])

    def test_versao_desactualizada_devolve_a_versao_actual(self):
        # AC17: o 409 traz a versão do disco para o cliente recarregar; nada escrito
        self.assertEqual(self.post(self.bom(versao="0")), (409, {"error": "conflito", "versao": self.versao()}))
        self.assertEqual(self.pedidos(), [])


# --- infra e docs (AC28, 29) ---
class InfraTest(unittest.TestCase):
    def test_ci_compila(self):
        self.assertRegex((ROOT / ".github/workflows/ci.yml").read_text(), r"py_compile .*\blancar\.py\b")

    def test_docs(self):
        r, adr = (ROOT / "README.md").read_text(), (ROOT / "DECISIONS.md").read_text()
        for s in ("/api/quadro/lancar", ".feito.json", ".movido.json", r"^\d{8}T\d{6}-[0-9a-f]{8}\.json$", "429"):
            self.assertIn(s, r)
        self.assertIn("## ADR-005", adr)


if __name__ == "__main__":
    unittest.main()
