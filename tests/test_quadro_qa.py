"""QA do Quadro: fronteiras que faltavam e os bugs encontrados na QA (só stdlib, dados falsos).

Os bugs B1–B3 (valores não-hashable/surrogates no ficheiro e no POST) estão corrigidos; os testes ficam como regressão.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import quadro
from tests.test_quadro import NOW, TZ, FicheiroBase, auto, dev, iso, manual
from tests import cfg

OFFICE_CFG = cfg.LEGADO

ROOT = Path(__file__).resolve().parent.parent


class BugsTest(FicheiroBase):
    def test_b1_projecto_nao_hashable_no_ficheiro_conta_como_invalido(self):
        # SPEC §4: cartão inválido é ignorado e contado. Hoje: TypeError em _valido → o GET /api/quadro fica sem resposta
        v = self.grava([manual(), manual(id="m-2", projecto=["loja"])])
        self.assertEqual(self.ler(), ([quadro._manual(manual(), NOW)], v, 1, True))

    def test_b1_resposta_com_projecto_dict_no_ficheiro(self):
        self.grava([manual(projecto={"x": 1})])
        out = quadro.resposta({}, {}, self.pasta, self.pasta / "inbox", TZ, NOW, "t")
        self.assertEqual(out["invalidos"], 1)

    def test_b2_validar_projecto_nao_hashable_da_400(self):
        # SPEC §6: 400 de validação. Hoje: TypeError em validar → o POST fica sem resposta (ligação fechada)
        d = {"op": "criar", "token": "t", "versao": "0", "titulo": "T", "descricao": "", "projecto": ["loja"],
             "coluna": "ideias", "etiquetas": []}
        self.assertEqual(quadro.validar(d, self.projs), "projecto desconhecido")

    def test_b3_criar_com_cartao_invalido_de_id_lista(self):
        # um cartão inválido (id lista) é preservado ao gravar, mas o criar rebenta no set dos ids existentes
        self.grava([manual(), {**manual(), "id": ["x"]}])
        self.assertEqual(self.criar()[0], 200)


class FronteirasTest(FicheiroBase):
    def test_limites_exactos_aceites(self):
        code, _ = self.criar("t" * 120, descricao="d" * 2000, etiquetas=["e" * 24, "b", "c", "d", "f"])
        self.assertEqual(code, 200)
        c = json.loads(self.f.read_text(encoding="utf-8"))["cartoes"][0]
        self.assertEqual((len(c["titulo"]), len(c["descricao"]), len(c["etiquetas"])), (120, 2000, 5))

    def test_etiquetas_repetidas_contam_depois_de_limpar(self):
        # 6 escritas com uma repetida (sem maiúsculas) = 5 etiquetas: aceite
        self.assertEqual(self.criar(etiquetas=["a", "b", "c", "d", "e", "A"])[0], 200)
        self.assertEqual(json.loads(self.f.read_text())["cartoes"][0]["etiquetas"], ["a", "b", "c", "d", "e"])

    def test_409_em_todos_os_desencontros_de_versao(self):
        d = {"token": "t", "op": "criar", "titulo": "T", "descricao": "", "projecto": "loja", "coluna": "ideias",
             "etiquetas": []}
        # ficheiro ausente e o browser viu um sha
        self.assertEqual(quadro.escrever(self.pasta, {**d, "versao": "a" * 64}, self.projs, TZ, NOW)[0], 409)
        self.assertFalse(self.f.exists())
        # ficheiro criado por fora e o browser ainda tem «0»
        v = self.grava([manual()])
        code, out = quadro.escrever(self.pasta, {**d, "versao": "0"}, self.projs, TZ, NOW)
        self.assertEqual((code, out), (409, {"error": "conflito", "versao": v}))
        # edição externa só de espaços também conta (D4) e o ficheiro fica intacto
        self.f.write_text(self.f.read_text() + "\n")
        antes = self.f.read_bytes()
        self.assertEqual(quadro.escrever(self.pasta, {**d, "versao": v}, self.projs, TZ, NOW)[0], 409)
        self.assertEqual(self.f.read_bytes(), antes)

    def test_criar_sem_ficheiro_e_apagar_o_ultimo(self):
        code, out = self.criar()
        self.assertEqual(code, 200)
        cid = out["cartoes"][0]["id"]
        self.assertRegex(cid, r"^m-[0-9a-f]{10}$")
        self.assertEqual(self.op(op="apagar", id=cid)[0], 200)
        self.assertEqual(json.loads(self.f.read_text()), {"v": 1, "cartoes": []})

    def test_mover_na_mesma_coluna_topo_e_acima_do_fim(self):
        self.grava([manual(id="a", ordem=10), manual(id="b", ordem=20), manual(id="c", ordem=30)])
        self.assertEqual(self.op(op="mover", id="c", coluna="ideias", posicao=0)[0], 200)
        ordem = lambda: [(c["id"], c["ordem"]) for c in sorted(json.loads(self.f.read_text())["cartoes"],
                                                                 key=lambda c: c["ordem"])]
        self.assertEqual(ordem(), [("c", 10), ("a", 20), ("b", 30)])
        self.assertEqual(self.op(op="mover", id="c", coluna="ideias", posicao=200)[0], 200)
        self.assertEqual(ordem(), [("a", 10), ("b", 20), ("c", 30)])

    def test_apagar_e_mover_preservam_invalidos_tal_e_qual(self):
        mau = {**manual(id="m-mau"), "extra": "do Claude Code"}
        self.grava([manual(id="a"), mau, manual(id="b", coluna="feito")])
        self.assertEqual(self.op(op="mover", id="b", coluna="ideias", posicao=0)[0], 200)
        self.assertEqual(self.op(op="apagar", id="a")[0], 200)
        lista = json.loads(self.f.read_text())["cartoes"]
        self.assertEqual([c["id"] for c in lista], ["m-mau", "b"])
        self.assertEqual(lista[0], mau)
        self.assertEqual(self.ler()[2], 1)

    def test_feito_fronteira_dos_7_dias(self):
        d7 = 7 * 86400
        pr = "https://github.com/o/r/pull/{} (merged)"   # Feito = PR integrado (sem PR fica «À espera»)
        state = {"tasks": [dev(tid="t-dentro", pr=pr.format(1), finished=_iso_s(d7 - 1)),
                           dev(tid="t-fora", pr=pr.format(2), finished=_iso_s(d7 + 1))]}
        out = quadro.resposta(state, {}, self.pasta, self.pasta / "inbox", TZ, NOW, "t")
        antigo = {c["id"]: c["antigo"] for c in out["cartoes"]}
        self.assertEqual((antigo["dev:t-dentro"], antigo["dev:t-fora"]), (False, True))
        self.grava([manual(id="m-d", coluna="feito", actualizado=_iso_s(d7 - 1)),
                    manual(id="m-f", coluna="feito", actualizado=_iso_s(d7 + 1)),
                    manual(id="m-i", coluna="ideias", actualizado=_iso_s(30 * 86400))])
        antigo = {c["id"]: c["antigo"] for c in self.ler()[0]}
        self.assertEqual(antigo, {"m-d": False, "m-f": True, "m-i": False})

    def test_ordem_a_espera_mais_antigo_primeiro_resto_mais_recente_sem_data_no_fim(self):
        pr = "https://github.com/o/r/pull/{} (open)"
        state = {"tasks": [dev(tid="w-novo", pr=pr.format(1), finished=iso(1)),
                           dev(tid="w-velho", pr=pr.format(2), finished=iso(5)),
                           dev(tid="c-velho", status="a correr", started=iso(3)),
                           dev(tid="c-novo", status="a correr", started=iso(0, 1)),
                           dev(tid="c-sem", status="a correr", started=None)],
                 "aprovacoes": [{"fonte": "geral", "ficheiro": "x.md", "titulo": "X", "criado": "2026-11-02",
                                 "abertas": 1}]}
        ids = [c["id"] for c in quadro.resposta(state, {}, self.pasta, self.pasta / "inbox", TZ, NOW,
                                                "t")["cartoes"]]
        self.assertEqual(ids, ["dev:c-novo", "dev:c-velho", "dev:c-sem", "dev:w-velho", "apr:geral/x.md", "dev:w-novo"])

    def test_c1_controlo_no_titulo_recusado_pelo_servidor(self):
        # Python conta U+0080–U+009F como controlo (Cc); o validaForm do browser não (ver QA.md, observação)
        self.assertEqual(self.criar("a\u0085b")[1], {"error": "título inválido"})


def _iso_s(segundos: float) -> str:
    return datetime.fromtimestamp(NOW - segundos, TZ).isoformat()


class BriefsFronteirasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, nome, dias=1.0):
        p = self.inbox / nome
        p.write_text("corpo", encoding="utf-8")
        os.utime(p, (NOW - dias * 86400, NOW - dias * 86400))

    def ids(self, metas=()):
        return sorted(c["id"][6:] for c in quadro.briefs(self.inbox, {"metas": list(metas)}, NOW, TZ))

    def test_nome_com_3_e_80_caracteres(self):
        for n in ("abc.md", "a" + "b" * 79 + ".md", "a" + "b" * 80 + ".md", "ab.md", "1abc.md", "-abc.md"):
            self.put(n)
        self.assertEqual(self.ids(), sorted(["abc.md", "a" + "b" * 79 + ".md"]))

    def test_curto_so_com_os_segmentos_da_lista(self):
        self.put("dev-abc.md")      # «dev» está na lista: curto = «abc» casa com o slug igual
        self.put("foo-abc.md")      # «foo» não está: curto = stem; «abc» (< 8) não casa por sufixo
        self.put("joga-quiz.md")
        self.assertEqual(self.ids([("dev", "20261101-0000-abc", frozenset(), None),
                                   ("dev", "20261101-0001-quiz", frozenset(), None)]), ["foo-abc.md"])

    def test_ref_inbox_e_por_nome_exacto(self):
        self.put("dev-alfa-beta.md")
        self.put("dev-alfa-beta-2.md")
        self.assertEqual(self.ids([("rnd", "x", frozenset({"dev-alfa-beta.md"}), None)]), ["dev-alfa-beta-2.md"])

    def test_7_dias_exactos_ainda_entram(self):
        self.put("dev-limite.md", dias=7.0)
        self.assertEqual(self.ids(), ["dev-limite.md"])


class SquadsRotinasTest(unittest.TestCase):
    def test_rotina_dry_run_fora_e_individuais_ficam_soltas(self):
        base = {"squad": "marketing", "project": "Marketing", "pr": None, "started": iso(0, 2), "finished": iso(0, 1),
                "cost_usd": 0.5}
        state = {"tasks": [{**base, "id": "g1", "mode": "gestor", "status": "concluída"},
                           {**base, "id": "g2", "mode": "gestor", "status": "dry-run"},
                           {**base, "id": "t1", "mode": "tarefa", "status": "concluída"}]}
        cs = auto(state)
        dia = iso(0, 2)[:10]
        self.assertEqual(sorted(cs), sorted([f"marketing:gestor:{dia}", "marketing:t1"]))
        self.assertEqual(cs[f"marketing:gestor:{dia}"]["detalhe"]["execucoes_n"], 1)


@unittest.skipUnless(shutil.which("node"), "sem node")
class NodeQaTest(unittest.TestCase):
    def test_testes_node_da_qa(self):
        r = subprocess.run(["node", "--test", str(ROOT / "tests" / "quadro_qa_test.mjs")], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
