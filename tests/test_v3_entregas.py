"""Lite 0.3.0 (ADR-014): GET /api/entregas (CA-32…34). Só stdlib; dados falsos em pasta temporária; NOW fixo.
Os segredos montam-se por concatenação (o gitleaks corre na CI)."""
import inspect
import json
import os
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import biblioteca
import entregas
import server
from tests import cfg
from tests import tempo
from tests import test_server as base
from tests.fake import result_line as result
from tests.test_v3_servidor import EstadoBase, HttpBase, naive

OFFICE_CFG = cfg.LEGADO

NOW = base.NOW
DIA = 86400
SK = "sk-" + "SEGREDO-x2"
GHP = "ghp_" + "SEGREDO-x3"
GHP_REAL = "ghp_" + "SEGREDOx3" + "A" * 27   # forma real de um token (36 car.)
CHAVES = {"id", "squad", "projecto", "manchete", "resumo", "terminada", "biblioteca"}


class MancheteTest(unittest.TestCase):
    # CA-33: 1.ª frase, ≤ 160
    def test_markdown_e_frase(self):
        casos = (("# Separadores no `#info`. E mais coisas.", "Separadores no #info."),
                 ("> **Feito**! Resto", "Feito!"),
                 ("- lista? não", "lista?"),
                 ("\n\n   \n## Título\nsegunda linha", "Título"),
                 ("---\n* \nTexto real. Mais", "Texto real."),
                 ("v1.2 pronto", "v1.2 pronto"),
                 ("Acabou com ponto final.", "Acabou com ponto final."),
                 ("`código` e *ênfase*", "código e ênfase"))
        for txt, esperado in casos:
            with self.subTest(txt=txt):
                self.assertEqual(entregas.manchete(txt), esperado)

    def test_lista_numerada_e_abreviaturas(self):
        casos = (("1. Implementei o separador Agentes. Depois testes.", "Implementei o separador Agentes."),
                 ("2) Feito. Resto", "Feito."),
                 ("- 3. Dentro de bullet. x", "Dentro de bullet."),
                 ("Ver o ficheiro e.g. server.py. ok", "Ver o ficheiro e.g. server.py."),
                 ("Usa p.ex. isto. Mais", "Usa p.ex. isto."),
                 ("Isto i.e. aquilo! Fim", "Isto i.e. aquilo!"),
                 ("Ok. Resto", "Ok."),
                 ("A. Bem feito. x", "A. Bem feito."),
                 ("1.5 kg pronto. x", "1.5 kg pronto."),
                 ("Só e.g. sem fim", "Só e.g. sem fim"),
                 ("Criei o act.js. Depois testes.", "Criei o act.js."),
                 ("2026. Ano novo.", "2026."))
        for txt, esperado in casos:
            with self.subTest(txt=txt):
                self.assertEqual(entregas.manchete(txt), esperado)

    def test_limites(self):
        for txt in (None, "", "   \n\n", "---\n# \n> "):
            with self.subTest(txt=txt):
                self.assertIsNone(entregas.manchete(txt))
        out = entregas.manchete(" ".join(["palavra"] * 22))   # 175 car., sem frase: corta numa palavra
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 160)
        self.assertEqual(out[:-1].split(" ")[-1], "palavra")
        self.assertEqual(entregas.manchete("a" * 170), "a" * 159 + "…")
        self.assertEqual(entregas.manchete("b" * 160), "b" * 160)

    def test_linha_enorme_sem_trabalho_quadratico(self):
        self.assertLess(tempo.cpu(entregas.manchete, "e.g. " * 20000), tempo.CPU_MAX_S)
        self.assertLessEqual(len(entregas.manchete("e.g. " * 20000)), entregas.MANCHETE_MAX)


class _Bib:
    # BIBLIOTECA falsa: só o atual() (o /api/entregas nunca espera nem reconstrói)
    def __init__(self, snap):
        self.snap = snap

    def atual(self):
        return self.snap

    def get(self):
        raise AssertionError("o /api/entregas não pode reconstruir o índice")

    aquecer = get


class EntregasBase(EstadoBase):
    def setUp(self):
        super().setUp()
        self.old_bib = server.BIBLIOTECA
        server.BIBLIOTECA = _Bib(None)

    def tearDown(self):
        server.BIBLIOTECA = self.old_bib
        super().tearDown()

    def meta(self, tid, fin_age=3600, root=None, status="concluída", **kw):
        m = {"id": tid, "project": "joga", "mode": "goal", "status": status,
             "started_at": naive((fin_age or 0) + 600), **kw}
        if fin_age is not None:
            m["finished_at"] = naive(fin_age)
        base.write_json((root or self.tasks) / tid / "meta.json", m)
        return (root or self.tasks) / tid

    def itens(self):
        return entregas.listar(server.TASKS, server.SQUADS_DIR, server.BIBLIOTECA, NOW)["itens"]


class EntregasTest(EntregasBase):
    def test_ca32_forma_e_chaves(self):
        self.meta("T1", result="Separadores no #info. E lista de agentes.")
        [it] = self.itens()
        self.assertEqual(it, {"id": "T1", "squad": "dev", "projecto": "joga", "manchete": "Separadores no #info.",
                              "resumo": "Separadores no #info. E lista de agentes.",
                              "terminada": server._iso_local(naive(3600)), "biblioteca": []})

    def test_ca32_ordem_e_maximo_40(self):
        for i in range(45):
            self.meta(f"T-{i:02d}", fin_age=60 * (i + 1), result=f"r{i}")
        self.assertEqual([i["id"] for i in self.itens()], [f"T-{i:02d}" for i in range(40)])

    def test_ca32_filtros(self):
        self.meta("T-ok", fin_age=3600, result="x")
        self.meta("T-corre", status="a correr", fin_age=10)       # a correr com finished_at antigo: não
        self.meta("T-dry", fin_age=10, mode="dry-run")
        self.meta("T-dry-st", fin_age=10, status="dry-run")
        self.meta("T-sem-fim", fin_age=None)
        self.meta("T-outra-squad", fin_age=10, squad="marketing")   # meta de TASKS de uma squad sessao: ignorada
        self.assertEqual([i["id"] for i in self.itens()], ["T-ok"])

    def test_ca32_janela_7_dias(self):
        self.meta("T-6d", fin_age=6 * DIA, result="x")
        self.meta("T-7d", fin_age=7 * DIA - 60, result="x")
        self.meta("T-8d", fin_age=8 * DIA, result="x")
        self.assertEqual([i["id"] for i in self.itens()], ["T-6d", "T-7d"])

    def test_ca32_todas_as_squads(self):
        self.meta("D", fin_age=40, result="dev.")
        self.meta("M", fin_age=30, root=self.squads / "marketing", result="Relatório diário. Detalhe")
        r = self.meta("R", fin_age=20, root=self.squads / "rnd")
        self.meta("S", fin_age=10, root=self.squads / "support", result="incidente", project=5)
        base.write(r / "claude.log", result(result="não pode sair: só as worktree lêem o claude.log"))
        itens = self.itens()
        self.assertEqual([(i["id"], i["squad"]) for i in itens],
                         [("S", "support"), ("R", "rnd"), ("M", "marketing"), ("D", "dev")])
        self.assertEqual((itens[1]["manchete"], itens[1]["resumo"]), (None, None))
        self.assertEqual(itens[2]["manchete"], "Relatório diário.")
        self.assertIsNone(itens[0]["projecto"])

    def test_ca33_fonte_result_e_claude_log(self):
        d = self.meta("T-meta", result="Da meta. Fim")
        base.write(d / "claude.log", result(result="Do log. Fim"))
        d = self.meta("T-log", fin_age=7200)
        base.write(d / "claude.log", "lixo\n" + result(result="Antiga.") + result(result="Do log. Fim"))
        d = self.meta("T-vazio", fin_age=9000, result="   ")
        base.write(d / "claude.log", result(result="Vazio na meta. x"))
        self.meta("T-sem-log", fin_age=9500)
        self.assertEqual({i["id"]: i["manchete"] for i in self.itens()},
                         {"T-meta": "Da meta.", "T-log": "Do log.", "T-vazio": "Vazio na meta.", "T-sem-log": None})

    def test_ca33_claude_log_linha_result_maior_que_64_kb(self):
        # muitas recusas com tool_input: a linha result passa dos 64 KB (relê-se com 1 MiB); > 1 MiB → sem entrega
        recusas = [{"tool_name": "Bash", "tool_use_id": f"t{i}", "tool_input": {"command": "x" * 2000}}
                   for i in range(50)]
        d = self.meta("T-grande")
        linha = result(result="Entrega grande. Fim", permission_denials=recusas)
        self.assertGreater(len(linha), 100_000)
        base.write(d / "claude.log", "lixo\n" * 10 + linha + '{"type": "system"}\n')
        d = self.meta("T-enorme", fin_age=7200)
        base.write(d / "claude.log", result(result="Enorme. Fim", permission_denials=recusas * 11))
        self.assertGreater((d / "claude.log").stat().st_size, server.LOG_TAIL_MAX)
        d = self.meta("T-antigo", fin_age=9000)   # result inteiro antes dos 64 KB finais: continua a não contar
        base.write(d / "claude.log", result(result="Antiga. Fim") + ("x" * 1000 + "\n") * 70)
        self.assertEqual({i["id"]: i["manchete"] for i in self.itens()},
                         {"T-grande": "Entrega grande.", "T-enorme": None, "T-antigo": None})

    def test_ca33_resumo_600(self):
        txt = "Primeira frase. " + "x" * 1000
        self.meta("T", result=txt)
        [it] = self.itens()
        self.assertEqual(it["resumo"], txt[:600])

    def test_id_da_pasta_e_metas_estragadas(self):
        base.write_json(self.tasks / "T-pasta" / "meta.json", {"status": "concluída", "finished_at": naive(60)})
        base.write(self.tasks / "T-lixo" / "meta.json", "{não é json")
        base.write(self.tasks / "T-lista" / "meta.json", "[1, 2]")
        base.write_json(self.tasks / "T-fim-mau" / "meta.json", {"status": "concluída", "finished_at": "ontem"})
        self.assertEqual([i["id"] for i in self.itens()], ["T-pasta"])

    def test_raizes_em_falta(self):
        nada = Path(self.tmp.name) / "nao-existe"
        self.assertEqual(entregas.listar(nada, nada, _Bib(None), NOW), {"itens": []})

    def test_ca34_biblioteca_so_do_indice_pronto(self):
        self.meta("T-com", result="x")
        self.meta("T-sem", fin_age=7200, result="x")
        self.meta("T-com", root=self.squads / "marketing", fin_age=60, result="x")   # sessao: sem anexos
        self.assertTrue(all(i["biblioteca"] == [] for i in self.itens()))   # atual() = None
        snap = SimpleNamespace(ficheiro_item={"tarefas/T-com/SPEC.md": "tarefas/T-com",
                                              "tarefas/T-com/QA.md": "tarefas/T-com"})
        server.BIBLIOTECA = _Bib(snap)
        out = {(i["squad"], i["id"]): i["biblioteca"] for i in self.itens()}
        self.assertEqual(out, {("marketing", "T-com"): [], ("dev", "T-com"): ["tarefas/T-com/SPEC.md",
                                                                             "tarefas/T-com/QA.md"],
                               ("dev", "T-sem"): []})

    def test_ca34_biblioteca_indice_real(self):
        d = self.meta("T-bib", result="x")
        base.write(d / "SPEC.md", "# spec")
        base.write(d / "PLAN.md", "# plan")
        idx = biblioteca.Indice({}, self.tasks, {})
        server.BIBLIOTECA = idx
        with mock.patch.object(idx, "get", side_effect=AssertionError("não reconstrói")), \
                mock.patch.object(idx, "aquecer", side_effect=AssertionError("não aquece")):
            self.assertEqual(self.itens()[0]["biblioteca"], [])   # índice ainda não feito: não espera
        self.assertIsNone(idx.atual())
        idx.get()
        self.assertEqual(self.itens()[0]["biblioteca"], ["tarefas/T-bib/SPEC.md", "tarefas/T-bib/PLAN.md"])


class EntregasSegurancaTest(EntregasBase):
    def test_ca33_segredos_mascarados(self):
        self.meta("T-res", result=f"Feito. OPENAI_API_KEY={SK} no fim")
        d = self.meta("T-log", fin_age=7200)
        base.write(d / "claude.log", result(result=f"GITHUB_TOKEN={GHP}\nToken {GHP_REAL} usado."))
        d = self.meta("T-1a", fin_age=9000)
        base.write(d / "claude.log", result(result=f"{GHP_REAL} logo na manchete. x"))
        d = self.meta("T-env", fin_age=9500, result="Li o .env: DB_PASSWORD=" + "Zq9x7Kp" * 4 + " e acabei.")
        body = json.dumps(self.itens(), ensure_ascii=False)
        for s in (SK, "SEGREDO-x2", GHP, "SEGREDO-x3", GHP_REAL, "SEGREDOx3", "Zq9x7Kp"):
            self.assertNotIn(s, body)
        out = {i["id"]: i for i in self.itens()}
        self.assertIn("[segredo]", out["T-res"]["resumo"])
        self.assertIn("[segredo]", out["T-log"]["manchete"])
        self.assertEqual(out["T-1a"]["manchete"], "[segredo] logo na manchete.")
        self.assertIn("[segredo]", out["T-env"]["resumo"])

    def test_ca33_mascara_antes_do_corte(self):
        # o segredo atravessa os 600 e os 160 car.: não pode sair a 1.ª metade em claro
        self.meta("T600", result="a " * 290 + f"OPENAI_API_KEY={SK}")
        self.meta("T160", fin_age=7200, result="a " * 70 + f"OPENAI_API_KEY={SK} fim")
        for it in self.itens():
            with self.subTest(id=it["id"]):
                for campo in ("manchete", "resumo"):
                    self.assertNotIn("SEGREDO", it[campo])
                    self.assertNotIn("sk-SEG", it[campo])

    def test_ca34_meta_por_symlink_para_fora(self):
        fora = Path(self.tmp.name) / "fora"
        base.write_json(fora / "T-fora" / "meta.json", {"id": "T-fora", "status": "concluída",
                                                        "finished_at": naive(60), "result": "fora da raiz"})
        os.symlink(fora / "T-fora", self.tasks / "T-dir-link")             # pasta da tarefa é symlink
        (self.tasks / "T-file-link").mkdir()
        os.symlink(fora / "T-fora" / "meta.json", self.tasks / "T-file-link" / "meta.json")   # meta.json é symlink
        self.meta("T-ok", result="ok")
        self.assertEqual([i["id"] for i in self.itens()], ["T-ok"])

    def test_ca34_meta_symlink_entre_raizes_e_fifo(self):
        # uma meta do marketing ligada dentro das worktree não conta como dev (nem lê o claude.log de lá)
        self.meta("M1", root=self.squads / "marketing", result="do marketing")
        os.symlink(self.squads / "marketing" / "M1", self.tasks / "M1")
        (self.tasks / "T-fifo").mkdir()
        os.mkfifo(self.tasks / "T-fifo" / "meta.json")
        out = []
        th = threading.Thread(target=lambda: out.append(self.itens()), daemon=True)
        th.start()
        th.join(5)
        self.assertFalse(th.is_alive(), "uma meta FIFO pendurou o pedido")
        self.assertEqual([(i["id"], i["squad"]) for i in out[0]], [("M1", "marketing")])

    def test_ca34_claude_log_fifo_symlink_pasta_e_falta(self):
        d = self.meta("T-fifo")
        os.mkfifo(d / "claude.log")
        fora = base.write(Path(self.tmp.name) / "fora" / "claude.log", result(result="não pode sair. x"))
        d = self.meta("T-link", fin_age=7200)
        os.symlink(fora, d / "claude.log")
        self.meta("T-falta", fin_age=9000)
        d = self.meta("T-dir", fin_age=9500)
        (d / "claude.log").mkdir()
        t0 = time.monotonic()
        itens = self.itens()
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual({i["id"]: (i["manchete"], i["resumo"]) for i in itens},
                         {k: (None, None) for k in ("T-fifo", "T-link", "T-falta", "T-dir")})


class EntregasHttpTest(HttpBase):
    def setUp(self):
        super().setUp()
        self.old_bib = server.BIBLIOTECA
        server.BIBLIOTECA = _Bib(None)

    def tearDown(self):
        server.BIBLIOTECA = self.old_bib
        super().tearDown()

    def test_ca32_rota_no_store_e_query_ignorada(self):
        self.dev("T1", result(result="Do log. x"))
        self.dev("T2", None, fin_age=7200, result="Da meta. y")
        code, raw, h = self.req("GET", "/api/entregas")
        self.assertEqual((code, h["Content-Type"], h["Cache-Control"], h["X-Content-Type-Options"]),
                         (200, "application/json; charset=utf-8", "no-store", "nosniff"))
        out = json.loads(raw)
        self.assertEqual([(i["id"], i["manchete"]) for i in out["itens"]], [("T1", "Do log."), ("T2", "Da meta.")])
        self.assertTrue(all(set(i) == CHAVES for i in out["itens"]))
        for q in ("?x=1", "?squad=marketing&n=1", "?id=../../etc"):
            with self.subTest(q=q):
                code, raw_q, _ = self.req("GET", "/api/entregas" + q)
                self.assertEqual((code, raw_q), (200, raw))

    def test_ca32_vazio(self):
        self.assertEqual(json.loads(self.get("/api/entregas")), {"itens": []})

    def test_ca33_segredos_nao_saem_no_corpo(self):
        self.dev("T-res", None, result=f"Feito. OPENAI_API_KEY={SK}")
        self.dev("T-log", result(result=f"GITHUB_TOKEN={GHP} e {GHP_REAL}"), fin_age=7200)
        body = self.get("/api/entregas")
        self.assertIn("[segredo]", body)
        for s in ("SEGREDO-x2", "SEGREDO-x3", "SEGREDOx3"):
            self.assertNotIn(s, body)

    def test_ca35_state_sem_o_texto_do_claude_log(self):
        # o /api/state (poll) não leva o texto da entrega do claude.log
        self.dev("T-res", result(result="entrega-do-log-unica"))
        self.assertNotIn("entrega-do-log-unica", self.get("/api/state"))

    def test_ca32_segredo_do_proxy(self):
        self.dev("T1", result(result="Do log. x"))
        old = server.PROXY_SEGREDO
        server.PROXY_SEGREDO = "segredo-do-proxy"
        try:
            code, raw, _ = self.req("GET", "/api/entregas")
            self.assertEqual((code, raw), (403, b""))
            code, raw, _ = self.req("GET", "/api/entregas", headers={"X-Office-Segredo": "outro"})
            self.assertEqual(code, 403)
            code, raw, _ = self.req("GET", "/api/entregas", headers={"X-Office-Segredo": "segredo-do-proxy"})
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(raw)["itens"][0]["id"], "T1")
        finally:
            server.PROXY_SEGREDO = old

    def test_post_nao_existe(self):
        code, _, _ = self.req("POST", "/api/entregas", b"{}", {"Content-Type": "application/json",
                                                              "Origin": f"http://{self.host}"})
        self.assertEqual(code, 404)
        self.assertIn('"/api/entregas"', inspect.getsource(server.Handler.do_GET))
        self.assertNotIn("entregas", inspect.getsource(server.Handler.do_POST))


if __name__ == "__main__":
    unittest.main()


def test_log_delivery_result_antigo_que_acaba_no_inicio_da_janela(tmp_path):
    # review ronda 2: a janela começa no "\n" final de uma linha result antiga → essa linha está toda antes (AC-38)
    import json as _json
    import reader as _reader
    p = tmp_path / "claude.log"
    res = _json.dumps({"type": "result", "result": "Antiga"}).encode()
    for enchimento in (998, 999):
        p.write_bytes(b"x" * 50 + b"\n" + res + b"\n" + b"y" * enchimento + b"\n")
        assert _reader.log_delivery(p, 1000, 1 << 20) is None
