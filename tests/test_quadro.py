"""Testes do quadro.py (regras, briefs, cartoes.json) com dados falsos; só stdlib."""
import copy
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import quadro
from tests.fake import gh_token
from tests import cfg

OFFICE_CFG = cfg.LEGADO

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=TZ).timestamp()
SK = "sk-" + "proj-" + "Abc123XyZ" * 4
PR = "https://github.com/exemplo-org/squad-office/pull/15"


def iso(dias: float = 0, horas: float = 0) -> str:
    return datetime.fromtimestamp(NOW - dias * 86400 - horas * 3600, TZ).isoformat()


def dev(tid="20261103-2359-office-quadro-kanban", status="concluída", pr="", **kw) -> dict:
    t = {"id": tid, "project": "fabrica", "mode": "goal", "status": status, "pr": pr or None, "squad": "dev",
         "branch": "feat/x", "started": iso(1), "finished": iso(0, 2), "cost_usd": 4.1, "duration_s": 3600,
         "phases": {"SPEC": True, "PLAN": True, "QA": False}}
    t.update(kw)
    return t


def sq(squad="marketing", status="concluída", tid="20261103-0700-gestor", **kw) -> dict:
    t = {"id": tid, "project": "Marketing", "mode": "tarefa", "status": status, "pr": None, "squad": squad,
         "started": iso(1), "finished": iso(0, 23), "cost_usd": 0.3, "duration_s": 120}
    t.update(kw)
    return t


def auto(state, aux=None):
    return {c["id"]: c for c in quadro.cartoes_auto(state, aux or {}, TZ)}


class RegrasDevTest(unittest.TestCase):
    def um(self, **kw):
        out = auto({"tasks": [dev(**kw)]})
        return next(iter(out.values()), None)

    def test_1_dry_run_fora(self):
        self.assertIsNone(self.um(mode="dry-run"))
        self.assertIsNone(self.um(status="dry-run"))

    def test_2_merged_e_closed_feito(self):
        c = self.um(status="erro", pr=f"{PR} (merged)")
        self.assertEqual((c["coluna"], c["etiquetas"], c["selo"]), ("feito", [], None))
        c = self.um(status="concluída", pr=f"{PR} (closed)")
        self.assertEqual((c["coluna"], c["etiquetas"]), ("feito", ["fechado sem merge"]))
        self.assertEqual(c["links"]["pr"], PR)

    def test_3_arquivada(self):
        c = self.um(status="arquivada")
        self.assertEqual((c["coluna"], c["etiquetas"]), ("feito", ["arquivada"]))

    def test_d2_merged_ganha_a_arquivada_e_arquivada_ganha_a_pr_aberto(self):
        self.assertEqual(self.um(status="arquivada", pr=f"{PR} (merged)")["etiquetas"], [])
        c = self.um(status="arquivada", pr=f"{PR} (open)")
        self.assertEqual((c["coluna"], c["etiquetas"]), ("feito", ["arquivada"]))

    def test_4_a_correr_e_parada(self):
        c = self.um(status="a correr", finished=None)
        self.assertEqual((c["coluna"], c["selo"], c["desde"]), ("em-curso", None, iso(1)))
        c = self.um(status="parada?")
        self.assertEqual((c["coluna"], c["selo"]), ("em-curso", {"cor": "laranja", "texto": "parada?"}))

    def test_5_falhadas(self):
        for st in ("erro", "incompleta", "morreu"):
            c = self.um(status=st)
            self.assertEqual((c["coluna"], c["selo"]), ("em-curso", {"cor": "vermelho", "texto": st}))

    def test_6_pr_aberto_a_espera(self):
        c = self.um(pr=f"{PR} (open)")
        self.assertEqual((c["coluna"], c["etiquetas"], c["desde"]), ("a-espera", [], iso(0, 2)))
        c = self.um(status="parcial (PR draft)", pr=f"{PR} (open, draft)")
        self.assertEqual((c["coluna"], c["etiquetas"]), ("a-espera", ["draft"]))
        self.assertEqual(c["detalhe"]["pr_estado"], "open, draft")

    def test_7_concluida_sem_pr_a_espera(self):
        # Lite: repo sem remoto → sem PR; o trabalho fica num branch local por rever
        c = self.um(pr="")
        self.assertEqual((c["coluna"], c["desde"]), ("a-espera", c["detalhe"]["fim"]))
        self.assertEqual(self.um(status="parcial (PR draft)")["coluna"], "a-espera")
        self.assertEqual(self.um(pr=f"{PR} (merged)")["coluna"], "feito")

    def test_8_outro_estado_selo_cinzento(self):
        c = self.um(status="estado-novo " + "x" * 50)
        self.assertEqual((c["coluna"], c["selo"]["cor"]), ("em-curso", "cinzento"))
        self.assertLessEqual(len(c["selo"]["texto"]), 30)
        # concluída com PR estranho também cai aqui
        self.assertEqual(self.um(pr="https://evil.example/pull/1 (open)")["coluna"], "em-curso")

    def test_forma_do_cartao_dev(self):
        c = self.um(pr=f"{PR} (open, draft)")
        self.assertEqual(set(c), {"id", "tipo", "fonte", "squad", "projecto", "titulo", "descricao", "coluna", "ordem",
                                  "selo", "etiquetas", "desde", "antigo", "custo_usd", "links", "detalhe",
                                  "progress"})
        self.assertEqual((c["id"], c["tipo"], c["fonte"], c["squad"], c["projecto"], c["titulo"]),
                         ("dev:20261103-2359-office-quadro-kanban", "auto", "dev", "dev", "fabrica",
                          "office quadro kanban"))
        self.assertEqual(c["links"], {"pr": PR, "aprovacao": None, "tarefa": "20261103-2359-office-quadro-kanban"})
        self.assertEqual(c["detalhe"]["fases"], {"SPEC": True, "PLAN": True, "QA": False})
        self.assertEqual(c["custo_usd"], 4.1)
        self.assertIsNone(c["progress"])   # só nas tarefas a correr (test_progresso)

    def test_pr_fora_do_github_nao_sai_em_links(self):
        c = self.um(status="arquivada", pr="javascript:alert(1) (merged)")
        self.assertEqual((c["coluna"], c["links"]["pr"]), ("feito", None))

    def test_waiting_sem_tarefa_em_tasks(self):
        w = {"id": "20260901-1000-velha", "squad": "dev", "project": "Loja", "status": "concluída", "pr_url": PR, "draft": True,
             "finished": iso(40)}
        out = auto({"tasks": [], "waiting": [w]})
        c = out["dev:20260901-1000-velha"]
        self.assertEqual((c["coluna"], c["etiquetas"], c["projecto"], c["desde"]),
                         ("a-espera", ["draft"], "loja", iso(40)))
        self.assertEqual(c["links"], {"pr": PR, "aprovacao": None, "tarefa": None})   # meta fora das salas: sem link
        # se já está em tasks, não duplica
        out = auto({"tasks": [dev(tid="20260901-1000-velha", pr=f"{PR} (open)")], "waiting": [w]})
        self.assertEqual(len(out), 1)

    def test_nao_muta_state(self):
        state = {"tasks": [dev(pr=f"{PR} (open)"), sq(), sq(status="erro", tid="t2")],
                 "waiting": [{"id": "x", "pr_url": PR, "draft": False, "finished": iso(40)}],
                 "aprovacoes": [{"fonte": "marketing", "ficheiro": "a.md", "titulo": "Loja preço", "criado":
                                 "2026-11-01", "abertas": 1, "resumo": "r"}],
                 "probes": {"items": [{"id": "loja-api", "product": "loja", "state": "red", "falha": 2,
                                       "ultimo": "x", "nome": "N"}]}}
        antes = copy.deepcopy(state)
        quadro.cartoes_auto(state, {}, TZ)
        self.assertEqual(state, antes)


class TitulosTest(unittest.TestCase):
    def test_titulo_task(self):
        self.assertEqual(quadro.titulo_task("intro\n#  Squad Office: Quadro — kanban\n# outro"),
                         "Squad Office: Quadro — kanban")
        for v in (None, 5, "", "sem título", "## só h2", "#sem espaço"):
            self.assertIsNone(quadro.titulo_task(v))
        self.assertEqual(quadro.chave_titulo("  Fábrica:   Quadro "), "fabrica: quadro")
        self.assertIsNone(quadro.chave_titulo(None))

    def test_dev_e_squad_usam_o_titulo_do_task(self):
        aux = {"titulo": {("dev", "20261016-1629-f1-supabase-cicd"): "Projeto 90: CI/CD do Supabase",
                          ("marketing", "m1"): "Campanha BF"}}
        out = auto({"tasks": [dev(tid="20261016-1629-f1-supabase-cicd"), dev(tid="20261016-1630-sem-titulo"),
                              sq(tid="m1"), sq(tid="m2")]}, aux)
        self.assertEqual(out["dev:20261016-1629-f1-supabase-cicd"]["titulo"], "Projeto 90: CI/CD do Supabase")
        self.assertEqual(out["dev:20261016-1630-sem-titulo"]["titulo"], "sem titulo")
        self.assertEqual(out["marketing:m1"]["titulo"], "Campanha BF")
        self.assertEqual(out["marketing:m2"]["titulo"], "tarefa · m2")

    def test_titulo_da_aprovacao(self):
        casos = {("Campanha X", "2026-11-01-x.md"): "Campanha X",
                 ("2026-11-01-bfpt26-variante-a-e-bn", "2026-11-01-bfpt26-variante-a-e-bn.md"):
                     "bfpt26 variante a e bn",
                 ("", "2026-11-01-sem-titulo.md"): "sem titulo", ("nota-solta", "nota-solta.md"): "nota solta"}
        for (titulo, nome), esperado in casos.items():
            with self.subTest(nome):
                a = {"fonte": "geral", "ficheiro": nome, "titulo": titulo, "criado": "2026-11-01", "abertas": 1}
                self.assertEqual(auto({"aprovacoes": [a]})[f"apr:geral/{nome}"]["titulo"], esperado)

    def test_glifos_do_bmp_e_distintos(self):
        icones = [i for _, _, i in quadro.PROJECTOS]
        self.assertEqual(len(icones), len(set(icones)))
        self.assertEqual({k: i for k, _, i in quadro.PROJECTOS}["tictag"], "◈")
        for i in icones:
            self.assertEqual(len(i), 1)
            self.assertFalse(0x2300 <= ord(i) <= 0x23FF or 0x27F0 <= ord(i) <= 0x27FF or ord(i) > 0xFFFF, i)


class RotinasTest(unittest.TestCase):
    def ex(self, tid, horas, status="concluída", mode="gestor", squad="marketing", custo=0.3):
        return sq(squad, status, tid=tid, mode=mode, started=iso(0, horas), finished=iso(0, horas - 0.01),
                  cost_usd=custo)

    def test_agrupa_por_squad_mode_dia(self):
        # NOW = 10:00 local; 2 h e 9 h atrás = hoje; 11 h atrás = ontem
        ts = [self.ex("g1", 2), self.ex("g2", 9, custo=None), self.ex("g3", 11), self.ex("a1", 3, mode="alerta"),
              self.ex("t1", 1, mode="tarefa"), self.ex("e1", 1, mode="estudo", squad="rnd"),
              self.ex("d1", 1, mode="dry-run")]
        aux = {"result": {("marketing", "g1"): "SEM ALTERAÇÕES " + "x" * 300, ("marketing", "g2"): f"t {SK}"}}
        out = auto({"tasks": ts}, aux)
        self.assertEqual(sorted(out), ["marketing:alerta:2026-11-04", "marketing:gestor:2026-11-03",
                                       "marketing:gestor:2026-11-04", "marketing:t1", "rnd:e1"])
        c = out["marketing:gestor:2026-11-04"]
        self.assertEqual((c["titulo"], c["coluna"], c["selo"], c["custo_usd"], c["squad"], c["projecto"], c["tipo"]),
                         ("Gestor · 2 execuções", "feito", None, 0.3, "marketing", "marketing", "auto"))
        self.assertEqual(c["links"]["tarefa"], "g1")
        self.assertEqual(c["desde"], datetime.fromisoformat(iso(0, 2 - 0.01)).isoformat(timespec="seconds"))
        d = c["detalhe"]
        self.assertEqual([e["hora"] for e in d["execucoes"]], ["08:00", "01:00"])
        self.assertLessEqual(len(d["execucoes"][0]["resultado"]), 100)
        self.assertNotIn(SK, json.dumps(out))
        self.assertTrue(d["resultado"].startswith("08:00 · concluída · SEM ALTERAÇÕES"))
        self.assertEqual((d["modo"], d["dia"], d["execucoes_n"], d["estado"]),
                         ("gestor", "2026-11-04", 2, "2 execuções"))
        self.assertEqual(out["marketing:alerta:2026-11-04"]["titulo"], "Alertas · 1 execução")

    def test_falha_e_a_correr(self):
        out = auto({"tasks": [self.ex("g1", 2, "erro"), self.ex("g2", 3)]})
        c = out["marketing:gestor:2026-11-04"]
        self.assertEqual((c["coluna"], c["selo"], c["detalhe"]["estado"]),
                         ("feito", {"cor": "vermelho", "texto": "1 falhada"}, "2 execuções, 1 falhada"))
        out = auto({"tasks": [self.ex("g1", 2, "a correr"), self.ex("g2", 3, "falhou"), self.ex("g3", 4, "erro")]})
        c = out["marketing:gestor:2026-11-04"]
        self.assertEqual((c["coluna"], c["selo"]["texto"]), ("em-curso", "2 falhadas"))
        self.assertEqual(c["desde"], datetime.fromisoformat(iso(0, 2)).isoformat(timespec="seconds"))

    def test_mode_desconhecido_e_muitas_execucoes(self):
        ts = [self.ex(f"x{i:02d}", 1 + i * 0.1, mode="ronda", squad="support", custo=0.1) for i in range(15)]
        c = auto({"tasks": ts})["support:ronda:2026-11-04"]
        self.assertEqual((c["titulo"], c["custo_usd"]), ("Ronda · 15 execuções", 1.5))
        self.assertEqual(len(c["detalhe"]["execucoes"]), 12)
        self.assertTrue(c["detalhe"]["resultado"].endswith("… e mais 3"))


class SquadsTest(unittest.TestCase):
    def test_estados(self):
        casos = {"a correr": ("em-curso", None), "parada?": ("em-curso", "laranja"), "erro": ("em-curso", "vermelho"),
                 "falhou": ("em-curso", "vermelho"), "morreu": ("em-curso", "vermelho"),
                 "incompleta": ("em-curso", "vermelho"), "concluída": ("feito", None), "concluido": ("feito", None),
                 "terminada": ("feito", None), "arquivada": ("feito", None)}
        for st, (col, cor) in casos.items():
            with self.subTest(st):
                c = auto({"tasks": [sq(status=st)]})["marketing:20261103-0700-gestor"]
                self.assertEqual((c["coluna"], c["selo"] and c["selo"]["cor"]), (col, cor))

    def test_dry_run_fora_e_forma(self):
        self.assertEqual(auto({"tasks": [sq(mode="dry-run")]}), {})
        aux = {"result": {("rnd", "r1"): "feito"}}
        c = auto({"tasks": [sq("rnd", tid="r1", mode="estudo")]}, aux)["rnd:r1"]
        self.assertEqual((c["squad"], c["projecto"], c["titulo"], c["fonte"]), ("rnd", "rnd", "estudo · r1", "rnd"))
        self.assertEqual(c["detalhe"], {"modo": "estudo", "estado": "concluída", "resultado": "feito",
                                        "duracao_s": 120})
        self.assertEqual(c["links"]["tarefa"], "r1")
        self.assertEqual(c["desde"], iso(0, 23))


class AprovacoesTest(unittest.TestCase):
    def apr(self, **kw):
        a = {"fonte": "marketing", "ficheiro": "2026-11-01-x.md", "titulo": "Campanha", "criado": "2026-11-01",
             "abertas": 2, "resumo": "resumo", "sha256": "f" * 64, "decisoes": []}
        a.update(kw)
        return auto({"aprovacoes": [a]})

    def test_a_espera_e_links(self):
        c = self.apr()["apr:marketing/2026-11-01-x.md"]
        self.assertEqual((c["coluna"], c["fonte"], c["squad"], c["projecto"]), ("a-espera", "aprovacao", None,
                                                                              "outro"))
        self.assertEqual(c["links"]["aprovacao"], {"fonte": "marketing", "ficheiro": "2026-11-01-x.md"})
        self.assertEqual(c["desde"], "2026-11-01T00:00:00+01:00")
        self.assertEqual(c["detalhe"], {"resumo": "resumo", "abertas": 2, "criado": "2026-11-01"})

    def test_projecto_pelo_titulo_e_fonte(self):
        self.assertEqual(self.apr(titulo="Preço do Loja")["apr:marketing/2026-11-01-x.md"]["projecto"], "loja")
        self.assertEqual(self.apr(titulo="Lote do E-book MZ")["apr:marketing/2026-11-01-x.md"]["projecto"], "ebook")
        self.assertEqual(self.apr(fonte="geral")["apr:geral/2026-11-01-x.md"]["projecto"], "outro")
        self.assertEqual(self.apr(fonte="outra")["apr:outra/2026-11-01-x.md"]["projecto"], "outro")

    def test_sem_abertas_nao_aparece(self):
        self.assertEqual(self.apr(abertas=0), {})

class ProjectoTest(unittest.TestCase):
    def test_id_nome_sufixo_acentos(self):
        casos = {"Loja": "loja", "Loja (auditoria)": "loja", "MenuQR (parecer UX)": "menuqr",
                 "Fábrica": "fabrica", "fabrica": "fabrica", "FABRICA": "fabrica", "E-book": "ebook", "ebook": "ebook",
                 "Blog": "blog", "Projeto 90": "projeto90", "  projeto   90 ": "projeto90", "projeto90": "projeto90",
                 "R&D": "rnd", "TicTag (revisão)": "tictag", "joga": "joga",
                 "Squad Office": "outro", "P90": "outro", "Coisa nova": "outro", None: "outro", 5: "outro"}
        for txt, k in casos.items():
            with self.subTest(txt):
                self.assertEqual(quadro.projecto_de(txt), k)

    def test_tabela_fechada(self):
        self.assertEqual([k for k, _ in quadro.COLUNAS], ["ideias", "a-fazer", "em-curso", "a-espera", "feito"])
        self.assertEqual([p["chave"] for p in quadro.projectos()],
                         ["loja", "projeto90", "ebook", "taalfiets", "menuqr", "marketing", "joga", "rnd", "blog",
                          "tictag", "support", "fabrica", "outro"])
        self.assertEqual([p["cor"] for p in quadro.projectos()],
                         ["c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c0", "c1", "c2", "c3", "outro"])

    def test_da_config(self):
        cfg.usar(self, cfg.OPS)
        self.assertEqual(quadro.projectos(), [{"chave": "loja", "rotulo": "Loja", "icone": "◆", "cor": "c0"},
                                              {"chave": "outro", "rotulo": "Outro", "icone": "·", "cor": "outro"}])
        self.assertEqual(quadro.chaves(), {"loja", "outro"})
        self.assertEqual(quadro.projecto_de("Joga"), "outro")
        self.assertEqual(quadro.COLUNAS[3], ("a-espera", "À espera de Rita"))
        self.assertEqual(quadro.DEV_PROJECTOS, ("loja",))
        self.assertEqual((quadro.WORKTREE, quadro.SESSAO), (("ops",), ()))


class BriefsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.tmp.name) / "inbox"
        self.inbox.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, nome, texto="# Título\n\ncorpo", dias=1.0):
        p = self.inbox / nome
        p.write_text(texto, encoding="utf-8")
        os.utime(p, (NOW - dias * 86400, NOW - dias * 86400))
        return p

    def ids(self, metas=()):
        return [c["id"][6:] for c in quadro.briefs(self.inbox, {"metas": list(metas)}, NOW, TZ)]

    def test_nome_e_arrumados(self):
        for n in ("dev-bom-brief.md", "x.FEITO.md", "a.SUBSTITUIDO.md", "dev-x.feito.md", "2026-11-03.md",
                  "com_underscore.md", "Maiusc.md", "ab.md", "dados.json", "img.png", "dev-y.md.bak"):
            self.put(n)
        self.assertEqual(self.ids(), ["dev-bom-brief.md"])

    def test_sem_prefixos_excluidos(self):
        # Lite: sem prefixos da fábrica fora do Quadro
        self.assertEqual(quadro.BRIEF_FORA, ())
        self.put("bateria-coisa.md")
        self.put("calibre-x.md")
        self.assertEqual(sorted(self.ids()), ["bateria-coisa.md", "calibre-x.md"])

    def test_mais_de_7_dias(self):
        self.put("dev-velho.md", dias=7.1)
        self.put("dev-novo.md", dias=6.9)
        self.assertEqual(self.ids(), ["dev-novo.md"])

    def test_casa_por_stem_curto_sufixo_e_ref(self):
        for n in ("dev-ebook-black-november-mz.md", "outro-assunto.md", "mkt-loja-curto.md",
                  "dev-texto-dos-ecras-novos.md", "loja-ref.md", "dev-nada.md"):
            self.put(n)
        metas = [("dev", "20261101-2245-ebook-black-november-mz", frozenset(), None),   # por curto
                 ("marketing", "20261101-0100-outro-assunto", frozenset(), None),        # por stem
                 ("dev", "20261101-0101-loja-curto", frozenset(), None),   # igual ao curto
                 ("dev", "20261101-0102-dos-ecras-novos", frozenset(), None),             # sufixo com fronteira, ≥ 8
                 ("support", "qualquer", frozenset({"loja-ref.md"}), None)]          # inbox/<nome> no task
        self.assertEqual(self.ids(metas), ["dev-nada.md"])

    def test_casa_pelo_titulo_do_task(self):
        self.put("loja-fix-bateria-3-11-d.md", "# Loja:  Prompt (saúde)\n\nx")
        self.put("dev-outro.md", "# Outra coisa\n")
        h1 = quadro.chave_titulo(quadro.titulo_task("# loja: prompt (SAÚDE)\n\nRelatório"))
        self.assertEqual(self.ids([("dev", "20261103-2133-fix-bateria-saude", frozenset(), h1)]), ["dev-outro.md"])

    def test_lancados_pelo_titulo_nao_ocupam_vaga(self):
        for i in range(14):
            self.put(f"dev-brief-{i:02d}.md", f"# Título {i}", dias=i * 0.1)
        metas = [("dev", f"x{i}", frozenset(), quadro.chave_titulo(f"Título {i}")) for i in (0, 1)]
        self.assertEqual(self.ids(metas), [f"dev-brief-{i:02d}.md" for i in range(2, 14)])

    def test_sufixo_curto_demais_nao_casa(self):
        self.put("dev-ab-curto.md")
        self.assertEqual(self.ids([("dev", "20261101-0000-curto", frozenset(), None)]), ["dev-ab-curto.md"])
        # sem fronteira «-» não casa
        self.put("dev-xyzcampanha-nova.md")
        self.assertIn("dev-xyzcampanha-nova.md", self.ids([("dev", "20261101-0000-campanha-nova", frozenset(), None)]))
        # meta mais longa que o brief, com fronteira
        self.put("dev-relatorio-mensal.md")
        self.assertNotIn("dev-relatorio-mensal.md",
                         self.ids([("dev", "20261101-0000-loja-relatorio-mensal", frozenset(), None)]))

    def test_meta_dry_run_ignorada_vem_do_aux(self):
        # o aux já não traz dry-run (ver test_aux_do_build_state): sem meta, o brief fica
        self.put("dev-ensaio-geral.md")
        self.assertEqual(self.ids([]), ["dev-ensaio-geral.md"])

    def test_symlink_ignorado(self):
        alvo = self.put("dev-alvo-real.md", dias=10)   # o alvo é velho; o link é novo
        os.symlink(alvo, self.inbox / "dev-link-novo.md")
        self.assertEqual(self.ids(), [])

    def test_limite_12_mais_recentes(self):
        for i in range(15):
            self.put(f"dev-brief-{i:02d}.md", dias=i * 0.1)
        self.assertEqual(self.ids(), [f"dev-brief-{i:02d}.md" for i in range(12)])

    def test_titulo_excerto_projecto(self):
        self.put("dev-loja-coisa.md", "texto antes\n# Loja: coisa\n" + "linha\n" * 600)
        self.put("landing-hero.md", "sem titulo")
        self.put("mkt-plano.md", "# M")
        self.put("marketing-blog-plano.md", "# B")
        self.put("dev-qualquer.md", "# Q")
        out = {c["id"]: c for c in quadro.briefs(self.inbox, {}, NOW, TZ)}
        c = out["brief:dev-loja-coisa.md"]
        self.assertEqual((c["titulo"], c["projecto"], c["coluna"], c["fonte"]),
                         ("Loja: coisa", "loja", "a-fazer", "brief"))
        self.assertLessEqual(len(c["descricao"]), 1500)
        self.assertTrue(c["descricao"].endswith("…"))
        self.assertEqual(c["detalhe"]["nome"], "dev-loja-coisa.md")
        self.assertEqual(c["desde"], c["detalhe"]["data"])
        self.assertEqual(out["brief:landing-hero.md"]["titulo"], "landing-hero")
        self.assertEqual(out["brief:landing-hero.md"]["projecto"], "outro")
        self.assertEqual(out["brief:mkt-plano.md"]["projecto"], "outro")
        self.assertEqual(out["brief:marketing-blog-plano.md"]["projecto"], "blog")   # <squad>-<projecto>-…
        self.assertEqual(out["brief:dev-qualquer.md"]["projecto"], "outro")

    def test_inbox_inexistente(self):
        self.assertEqual(quadro.briefs(Path(self.tmp.name) / "nao", {}, NOW, TZ), [])


class FeitoOrdemTest(unittest.TestCase):
    def test_antigo_e_ordem(self):
        state = {"tasks": [dev(tid="a-recente", status="arquivada", finished=iso(1)),
                           dev(tid="b-velha", status="arquivada", finished=iso(8)),
                           dev(tid="c-sem-fim", status="arquivada", finished=None, started=iso(9)),
                           dev(tid="d-espera-nova", pr=f"{PR} (open)", finished=iso(1)),
                           dev(tid="e-espera-velha", pr=f"{PR} (open)", finished=iso(5)),
                           dev(tid="f-corre-velha", status="a correr", started=iso(3)),
                           dev(tid="g-corre-nova", status="a correr", started=iso(0, 1))]}
        with tempfile.TemporaryDirectory() as d:
            out = quadro.resposta(state, {}, Path(d) / "q", Path(d) / "inbox", TZ, NOW, "tok")
        ids = [(c["coluna"], c["id"][4:], c["antigo"]) for c in out["cartoes"]]
        self.assertEqual(ids, [("em-curso", "g-corre-nova", False), ("em-curso", "f-corre-velha", False),
                               ("a-espera", "e-espera-velha", False), ("a-espera", "d-espera-nova", False),
                               ("feito", "a-recente", False), ("feito", "b-velha", True),
                               ("feito", "c-sem-fim", True)])

    def test_resposta_topo(self):
        with tempfile.TemporaryDirectory() as d:
            out = quadro.resposta({}, {}, Path(d), Path(d) / "inbox", TZ, NOW, "tok")
        self.assertEqual(set(out), {"v", "agora", "versao", "escrita", "invalidos", "aviso", "token", "colunas",
                                    "projectos", "cartoes"})
        self.assertEqual((out["v"], out["versao"], out["escrita"], out["invalidos"], out["aviso"], out["cartoes"]),
                         (1, "0", True, 0, None, []))
        self.assertEqual(out["agora"], "2026-11-04T10:00:00+01:00")


class SegredosTest(unittest.TestCase):
    def test_segredos_mascarados(self):
        gh = gh_token()
        with tempfile.TemporaryDirectory() as d:
            pasta, inbox = Path(d) / "q", Path(d) / "inbox"
            pasta.mkdir()
            inbox.mkdir()
            (inbox / "dev-segredo.md").write_text(f"# Brief {gh}\n\ntoken {SK}\n")
            os.utime(inbox / "dev-segredo.md", (NOW, NOW))
            (pasta / "cartoes.json").write_text(json.dumps({"v": 1, "cartoes": [manual(titulo=f"cola {gh}",
                                                                                      descricao=f"x {SK}",
                                                                                      etiquetas=[SK[:20]])]}))
            state = {"tasks": [dev(tid=f"20261101-1000-x-{gh}"), sq(status="erro"),
                               dev(tid="t2", status=f"estado {SK}")],
                     "aprovacoes": [{"fonte": "marketing", "ficheiro": "a.md", "titulo": f"Pedido {gh}",
                                     "criado": "2026-11-01", "abertas": 1, "resumo": f"r {SK}"}]}
            aux = {"result": {("marketing", "20261103-0700-gestor"): "ok"}}
            out = json.dumps(quadro.resposta(state, aux, pasta, inbox, TZ, NOW, "tok"), ensure_ascii=False)
        self.assertNotIn(gh, out)
        self.assertNotIn(SK, out)
        self.assertIn("[segredo]", out)
        c = next(c for c in json.loads(out)["cartoes"] if c["titulo"] == "x [segredo]")
        self.assertIsNone(c["links"]["tarefa"])   # id com segredo: mascarado e sem link


def manual(**kw) -> dict:
    c = {"id": "m-0000000001", "titulo": "Ideia", "descricao": "", "projecto": "loja", "coluna": "ideias",
         "ordem": 10, "etiquetas": [], "criado": "2026-11-01T10:00:00+01:00", "actualizado": "2026-11-01T10:00:00+01:00"}
    c.update(kw)
    return c


class FicheiroBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pasta = Path(self.tmp.name) / "quadro"
        self.pasta.mkdir()
        self.f = self.pasta / "cartoes.json"
        self.projs = quadro.chaves()

    def tearDown(self):
        self.tmp.cleanup()

    def grava(self, cartoes, **doc):
        self.f.write_text(json.dumps({"v": 1, "cartoes": cartoes, **doc}, ensure_ascii=False), encoding="utf-8")
        return self.versao()

    def versao(self):
        return hashlib.sha256(self.f.read_bytes()).hexdigest() if self.f.exists() else "0"

    def ler(self):
        return quadro.ler(self.pasta, self.projs, NOW)

    def op(self, **kw):
        d = {"token": "t", "versao": self.versao(), **kw}
        msg = quadro.validar(d, self.projs)
        if msg:
            return 400, {"error": msg}
        return quadro.escrever(self.pasta, d, self.projs, TZ, NOW)

    def criar(self, titulo="Nova", coluna="ideias", **kw):
        return self.op(op="criar", titulo=titulo, descricao=kw.pop("descricao", ""), projecto=kw.pop("projecto",
                       "loja"), coluna=coluna, etiquetas=kw.pop("etiquetas", []), **kw)


class LerTest(FicheiroBase):
    def test_ausente(self):
        self.assertEqual(self.ler(), ([], "0", 0, True))

    def test_invalidos_contados(self):
        maus = [manual(id="a:b"), manual(id="m-2", titulo=""), manual(id="m-3", titulo="x" * 121),
                manual(id="m-4", descricao="x" * 2001), manual(id="m-5", projecto="nao"), manual(id="m-6", coluna="z"),
                manual(id="m-7", ordem=-1), manual(id="m-8", ordem=True), manual(id="m-9", etiquetas=["a"] * 2),
                manual(id="m-10", etiquetas=list("abcdef")), manual(id="m-11", etiquetas=["x" * 25]),
                manual(id="m-12", criado="2026-11-01T10:00:00"), {**manual(id="m-13"), "extra": 1},
                {k: v for k, v in manual(id="m-14").items() if k != "ordem"}, "texto", manual(),
                manual(id="m-15", titulo="a\tb"), manual(id="m-16", descricao="a\x00")]
        v = self.grava([manual()] + maus)   # o manual() repetido conta como inválido (id duplicado)
        cartoes, versao, inv, ok = self.ler()
        self.assertEqual(([c["id"] for c in cartoes], versao, inv, ok), (["m-0000000001"], v, len(maus), True))

    def test_ilegivel_ou_v_errado(self):
        for raw in ("{mau", json.dumps({"v": 2, "cartoes": []}), json.dumps({"v": True, "cartoes": []}),
                    json.dumps([1]), json.dumps({"v": 1, "cartoes": {}}), json.dumps({"v": 1, "cartoes": [], "x": 1}),
                    b"\xff\xfe"):
            with self.subTest(raw=raw):
                (self.f.write_bytes if isinstance(raw, bytes) else self.f.write_text)(raw)
                cartoes, versao, _inv, ok = self.ler()
                self.assertEqual((cartoes, versao, ok), ([], self.versao(), False))

    def test_resposta_com_ficheiro_invalido(self):
        self.f.write_text("{mau")
        out = quadro.resposta({}, {}, self.pasta, self.pasta / "inbox", TZ, NOW, "t")
        self.assertEqual((out["escrita"], out["aviso"], out["versao"]), (False, "cartoes.json inválido", self.versao()))

    def test_sem_pasta_escrita_false(self):
        out = quadro.resposta({}, {}, self.pasta / "nao", self.pasta / "inbox", TZ, NOW, "t")
        self.assertEqual((out["escrita"], out["aviso"], out["versao"]), (False, None, "0"))

    def test_forma_do_manual_no_get(self):
        self.grava([manual(coluna="feito", actualizado=iso(8), etiquetas=["preço"], descricao="**md**")])
        c = self.ler()[0][0]
        self.assertEqual(c, {"id": "m-0000000001", "tipo": "manual", "fonte": "manual", "squad": None,
                             "projecto": "loja", "titulo": "Ideia", "descricao": "**md**", "coluna": "feito",
                             "ordem": 10, "selo": None, "etiquetas": ["preço"], "desde": iso(8), "antigo": True,
                             "custo_usd": None, "links": {"pr": None, "aprovacao": None, "tarefa": None},
                             "detalhe": {}, "progress": None, "criado": "2026-11-01T10:00:00+01:00",
                             "actualizado": iso(8)})

    def test_manuais_primeiro_por_ordem(self):
        self.grava([manual(id="m-b", coluna="a-espera", ordem=20), manual(id="m-a", coluna="a-espera", ordem=10)])
        state = {"tasks": [dev(pr=f"{PR} (open)")]}
        out = quadro.resposta(state, {}, self.pasta, self.pasta / "inbox", TZ, NOW, "t")
        self.assertEqual([c["id"] for c in out["cartoes"]],
                         ["m-a", "m-b", "dev:20261103-2359-office-quadro-kanban"])


class ValidarTest(FicheiroBase):
    def base(self, op="criar", **kw):
        d = {"criar": {"titulo": "T", "descricao": "", "projecto": "loja", "coluna": "ideias", "etiquetas": []},
             "editar": {"id": "m-1", "titulo": "T", "descricao": "", "projecto": "loja", "etiquetas": []},
             "apagar": {"id": "m-1"}, "mover": {"id": "m-1", "coluna": "feito", "posicao": 0}}[op]
        return {"op": op, "token": "t", "versao": "0", **d, **kw}

    def msg(self, op_base="criar", **kw):
        return quadro.validar({**self.base(op_base), **kw}, self.projs)

    def test_ok_e_normaliza(self):
        d = self.base(titulo="  Olá\r\n ".strip() + "  ", descricao="a\r\nb", etiquetas=[" x ", "X", "y"])
        self.assertIsNone(quadro.validar(d, self.projs))
        self.assertEqual((d["titulo"], d["descricao"], d["etiquetas"]), ("Olá", "a\nb", ["x", "y"]))
        d = self.base(descricao=None)
        self.assertIsNone(quadro.validar(d, self.projs))
        self.assertEqual(d["descricao"], "")
        for op in ("editar", "apagar", "mover"):
            self.assertIsNone(self.msg(op))

    def test_erros(self):
        casos = [({"titulo": "  "}, "título obrigatório"), ({"titulo": "x" * 121}, "título com mais de 120 caracteres"),
                 ({"titulo": "a\nb"}, "título inválido"), ({"descricao": "x" * 2001},
                                                          "descrição com mais de 2000 caracteres"),
                 ({"descricao": "a\x07"}, "descrição inválida"), ({"etiquetas": list("abcdef")}, "máx. 5 etiquetas"),
                 ({"etiquetas": ["x" * 25]}, "etiqueta com 1 a 24 caracteres"),
                 ({"etiquetas": [" "]}, "etiqueta com 1 a 24 caracteres"), ({"etiquetas": "a"}, "campos inválidos"),
                 ({"etiquetas": [1]}, "campos inválidos"), ({"coluna": "nada"}, "coluna desconhecida"),
                 ({"projecto": "nada"}, "projecto desconhecido"), ({"extra": 1}, "campos inválidos"),
                 ({"titulo": 5}, "campos inválidos"), ({"versao": "abc"}, "campos inválidos"),
                 ({"versao": None}, "campos inválidos"), ({"titulo": "a\ud800"}, "JSON inválido"),
                 ({"etiquetas": ["\udfff"]}, "JSON inválido"), ({"op": ["criar"]}, "campos inválidos"),
                 ({"op": "outra"}, "campos inválidos")]
        for kw, msg in casos:
            with self.subTest(kw=kw):
                self.assertEqual(self.msg(**kw), msg)
        sem = self.base()
        del sem["etiquetas"]
        self.assertEqual(quadro.validar(sem, self.projs), "campos inválidos")
        self.assertEqual(self.msg("editar", id="dev:x"), "cartão automático não se edita")
        self.assertEqual(self.msg("apagar", id="x" * 41), "id inválido")
        self.assertEqual(self.msg("apagar", id="a b"), "id inválido")
        self.assertEqual(self.msg("apagar", id=5), "id inválido")
        self.assertEqual(self.msg("mover", posicao="0"), "campos inválidos")
        self.assertEqual(self.msg("mover", posicao=True), "campos inválidos")
        self.assertEqual(self.msg("mover", posicao=-1), "campos inválidos")
        self.assertEqual(self.msg("editar", coluna="ideias"), "campos inválidos")   # editar não move

    def test_falhas_nao_mexem_no_ficheiro(self):
        self.grava([manual()])
        antes = self.f.read_bytes()
        for kw in ({"titulo": ""}, {"etiquetas": list("abcdef")}, {"coluna": "x"}, {"versao": "f" * 64}):
            self.assertNotEqual(self.criar(**kw)[0], 200)
        self.assertEqual(self.op(op="apagar", id="m-nao")[0], 404)
        self.assertEqual(self.f.read_bytes(), antes)
        self.assertEqual(sorted(os.listdir(self.pasta)), ["cartoes.json"])


class TxtTest(unittest.TestCase):
    def test_uma_so_mascara_e_memorizada(self):
        quadro._txt.cache_clear()
        with mock.patch.object(quadro.reader, "mask", wraps=quadro.reader.mask) as m:
            self.assertEqual(quadro.txt("curto", 10), "curto")
            self.assertEqual(m.call_count, 1)
            m.reset_mock()
            self.assertEqual(len(quadro.txt("x " * 100, 10)), 10)
            self.assertEqual(m.call_count, 1)
            quadro.txt("x " * 100, 10)
            self.assertEqual(m.call_count, 1)   # da cache

    def test_mascara_que_alonga_respeita_o_limite(self):
        s = f"t={SK[:3]}"
        out = quadro.txt("pw=ab12cd34ef56gh78ij90", 22)
        self.assertLessEqual(len(out), 22)
        self.assertEqual(quadro.txt(s, 120), quadro.reader.mask(s))
        self.assertEqual(quadro.txt(None, 5), "")


class EditarTest(FicheiroBase):
    def editar(self, **kw):
        return self.op(op="editar", id="m-0000000001", **kw)

    def test_parcial_mantem_os_ausentes(self):
        self.grava([manual(descricao="d", etiquetas=["a"], projecto="joga")])
        self.assertEqual(self.editar(titulo="Novo")[0], 200)
        c = json.loads(self.f.read_text())["cartoes"][0]
        self.assertEqual((c["titulo"], c["descricao"], c["etiquetas"], c["projecto"], c["actualizado"]),
                         ("Novo", "d", ["a"], "joga", "2026-11-04T10:00:00+01:00"))
        self.assertEqual(self.editar(etiquetas=[])[0], 200)
        self.assertEqual(json.loads(self.f.read_text())["cartoes"][0]["etiquetas"], [])
        self.assertEqual(self.editar(projecto="nada"), (400, {"error": "projecto desconhecido"}))
        self.assertEqual(self.editar(titulo=""), (400, {"error": "título obrigatório"}))
        self.assertEqual(self.editar(coluna="feito"), (400, {"error": "campos inválidos"}))
        self.assertEqual(self.op(op="editar", titulo="x"), (400, {"error": "campos inválidos"}))   # sem id

    def test_texto_mascarado_devolvido_igual_nao_estraga_o_original(self):
        gh = gh_token()
        titulo, desc = f"cola {gh}", f"linha 1\n{SK}\n" + "x " * 950
        self.grava([manual(titulo=titulo, descricao=desc, etiquetas=[SK[:20], "ok"])])
        visto = self.ler()[0][0]
        self.assertNotIn(gh, json.dumps(visto))
        code, _ = self.editar(titulo=visto["titulo"], descricao=visto["descricao"],
                              etiquetas=visto["etiquetas"] + ["nova"], projecto="joga")
        self.assertEqual(code, 200)
        c = json.loads(self.f.read_text())["cartoes"][0]
        self.assertEqual((c["titulo"], c["descricao"], c["etiquetas"], c["projecto"]),
                         (titulo, desc, [SK[:20], "ok", "nova"], "joga"))
        # texto mudado: grava o que veio (o [segredo] do browser fica, como no PLAN §6)
        self.assertEqual(self.editar(titulo=visto["titulo"] + "!")[0], 200)
        self.assertEqual(json.loads(self.f.read_text())["cartoes"][0]["titulo"], "cola [segredo]!")

    def test_tab_na_descricao(self):
        code, _ = self.criar(descricao="a\tb\nc")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(self.f.read_text())["cartoes"][0]["descricao"], "a\tb\nc")
        self.assertEqual(self.ler()[2], 0)   # o cartão gravado é válido ao ler
        for mau in ("a\x0bb", "a\x00", "a\rb", "a\x1b[0m"):
            self.assertEqual(self.criar(descricao=mau), (400, {"error": "descrição inválida"}))
        self.assertEqual(self.criar(titulo="a\tb"), (400, {"error": "título inválido"}))

    def test_surrogate_no_ficheiro_conta_como_invalido(self):
        raw = json.dumps({"v": 1, "cartoes": [manual(), manual(id="m-2", descricao="a\ud800"),
                                              manual(id="m-3", etiquetas=["\udfff"])]})
        self.f.write_text(raw)
        cartoes, _, inv, ok = self.ler()
        self.assertEqual(([c["id"] for c in cartoes], inv, ok), (["m-0000000001"], 2, True))


class EscreverTest(FicheiroBase):
    def test_criar_formato_exacto(self):
        code, out = self.criar("Preço novo", etiquetas=["preço"], descricao="linha\nduas")
        self.assertEqual(code, 200)
        raw = self.f.read_text(encoding="utf-8")
        self.assertIn("Preço novo", raw)   # UTF-8 sem escapes
        doc = json.loads(raw)
        self.assertEqual(raw, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
        self.assertEqual(list(doc), ["v", "cartoes"])
        c = doc["cartoes"][0]
        self.assertEqual(list(c), list(quadro.CHAVES))
        self.assertRegex(c["id"], r"^m-[0-9a-f]{10}$")
        self.assertEqual((c["ordem"], c["criado"], c["actualizado"], c["coluna"]),
                         (10, "2026-11-04T10:00:00+01:00", "2026-11-04T10:00:00+01:00", "ideias"))
        self.assertEqual(out["versao"], self.versao())
        self.assertEqual([x["id"] for x in out["cartoes"]], [c["id"]])
        self.assertEqual(out["cartoes"][0]["tipo"], "manual")
        # o 2.º vai para o fim da coluna
        self.assertEqual(self.criar("Outra")[0], 200)
        self.assertEqual([x["ordem"] for x in json.loads(self.f.read_text())["cartoes"]], [10, 20])

    def test_editar_mantem_criado(self):
        self.grava([manual()])
        code, _ = self.op(op="editar", id="m-0000000001", titulo="Novo", descricao="d", projecto="joga",
                            etiquetas=["a"])
        self.assertEqual(code, 200)
        c = json.loads(self.f.read_text())["cartoes"][0]
        self.assertEqual((c["titulo"], c["projecto"], c["criado"], c["actualizado"], c["coluna"], c["ordem"]),
                         ("Novo", "joga", "2026-11-01T10:00:00+01:00", "2026-11-04T10:00:00+01:00", "ideias", 10))

    def test_apagar(self):
        self.grava([manual(id="m-1"), manual(id="m-2")])
        code, out = self.op(op="apagar", id="m-1")
        self.assertEqual((code, [c["id"] for c in out["cartoes"]]), (200, ["m-2"]))
        self.assertEqual([c["id"] for c in json.loads(self.f.read_text())["cartoes"]], ["m-2"])

    def test_mover_renumera_so_o_destino(self):
        self.grava([manual(id="a1", coluna="a-fazer", ordem=5), manual(id="a2", coluna="a-fazer", ordem=7),
                    manual(id="b1", coluna="feito", ordem=3), manual(id="b2", coluna="feito", ordem=99),
                    manual(id="x", coluna="a-fazer", ordem=6)])
        code, _ = self.op(op="mover", id="x", coluna="feito", posicao=1)
        self.assertEqual(code, 200)
        doc = {c["id"]: c for c in json.loads(self.f.read_text())["cartoes"]}
        self.assertEqual({k: (c["coluna"], c["ordem"]) for k, c in doc.items()},
                         {"a1": ("a-fazer", 5), "a2": ("a-fazer", 7), "b1": ("feito", 10), "x": ("feito", 20),
                          "b2": ("feito", 30)})
        self.assertEqual(doc["x"]["actualizado"], "2026-11-04T10:00:00+01:00")
        self.assertEqual(doc["b2"]["actualizado"], "2026-11-01T10:00:00+01:00")   # renumerado não muda
        # reordenar na mesma coluna; posição acima de n vai para o fim
        self.assertEqual(self.op(op="mover", id="b1", coluna="feito", posicao=200)[0], 200)
        doc = {c["id"]: c["ordem"] for c in json.loads(self.f.read_text())["cartoes"]}
        self.assertEqual((doc["x"], doc["b2"], doc["b1"]), (10, 20, 30))
        self.assertEqual(self.op(op="mover", id="b1", coluna="feito", posicao=0)[0], 200)
        doc = {c["id"]: c["ordem"] for c in json.loads(self.f.read_text())["cartoes"]}
        self.assertEqual((doc["b1"], doc["x"], doc["b2"]), (10, 20, 30))

    def test_invalidos_preservados_ao_gravar(self):
        mau = {"id": "?", "lixo": True}
        self.grava([manual(id="m-1"), mau])
        self.assertEqual(self.op(op="apagar", id="m-1")[0], 200)
        self.assertEqual(json.loads(self.f.read_text())["cartoes"], [mau])

    def test_conflito_409(self):
        self.grava([manual()])
        velha = self.versao()
        self.f.write_text(self.f.read_text() + " ")   # edição externa
        d = {"op": "apagar", "token": "t", "versao": velha, "id": "m-0000000001"}
        self.assertEqual(quadro.escrever(self.pasta, d, self.projs, TZ, NOW),
                         (409, {"error": "conflito", "versao": self.versao()}))
        # ausente: "0" é a versão; outra coisa dá 409
        self.f.unlink()
        self.assertEqual(self.criar(versao="f" * 64)[0], 409)
        self.assertEqual(self.criar()[0], 200)

    def test_ficheiro_invalido_503_e_intacto(self):
        self.f.write_text("{mau")
        antes = self.f.read_bytes()
        self.assertEqual(self.criar(), (503, {"error": "cartoes.json inválido — corrige à mão"}))
        self.assertEqual(self.f.read_bytes(), antes)

    def test_sem_pasta_503(self):
        self.pasta = self.pasta / "nao"
        self.assertEqual(self.criar(), (503, {"error": "sem pasta do quadro"}))
        self.assertFalse(self.pasta.exists())

    def test_limite_200(self):
        self.grava([manual(id=f"m-{i}") for i in range(199)])
        self.assertEqual(self.criar()[0], 200)
        antes = self.f.read_bytes()
        self.assertEqual(self.criar(), (503, {"error": "limite de 200 cartões"}))
        self.assertEqual(self.f.read_bytes(), antes)

    def test_replace_a_falhar_deixa_o_antigo_e_sem_tmp(self):
        self.grava([manual()])
        antes = self.f.read_bytes()
        vistos = []

        def falha(src, dst):
            vistos.append((Path(src).parent, Path(dst)))
            raise OSError("disco")
        with mock.patch.object(quadro.os, "replace", falha):
            self.assertEqual(self.criar(), (503, {"error": "não consegui gravar o quadro"}))
        self.assertEqual(vistos, [(self.pasta, self.f)])   # tmp na mesma pasta
        self.assertEqual(self.f.read_bytes(), antes)
        self.assertEqual(os.listdir(self.pasta), ["cartoes.json"])

    def test_fsync_antes_do_replace(self):
        ordem = []
        real_fsync, real_replace = os.fsync, os.replace
        with mock.patch.object(quadro.os, "fsync", lambda fd: (ordem.append("fsync"), real_fsync(fd))), \
                mock.patch.object(quadro.os, "replace", lambda a, b: (ordem.append("replace"), real_replace(a, b))):
            self.assertEqual(self.criar()[0], 200)
        self.assertEqual(ordem, ["fsync", "replace"])

    def test_dois_threads_mesma_versao(self):
        self.grava([])
        v = self.versao()
        res, barreira = [], threading.Barrier(2)

        def corre(t):
            d = {"op": "criar", "token": "t", "versao": v, "titulo": t, "descricao": "", "projecto": "loja",
                 "coluna": "ideias", "etiquetas": []}
            quadro.validar(d, self.projs)
            barreira.wait()
            res.append(quadro.escrever(self.pasta, d, self.projs, TZ, time.time())[0])
        ts = [threading.Thread(target=corre, args=(t,)) for t in ("a", "b")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(res), [200, 409])
        self.assertEqual(len(json.loads(self.f.read_text())["cartoes"]), 1)

    def test_segredo_no_manual_sai_mascarado(self):
        code, out = self.criar(f"token {gh_token()}")
        self.assertEqual(code, 200)
        self.assertNotIn(gh_token(), json.dumps(out))


if __name__ == "__main__":
    unittest.main()
