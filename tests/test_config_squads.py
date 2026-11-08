"""Squads, projectos e colunas vêm da config (AC-a4, AC-a5): com ops/loja nada depende de dev/exemplo."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import biblioteca
import lancar
import push
import quadro
import server
from tests import cfg

PR = "https://github.com/o/loja/pull/4"
GLOBAIS = ("TASKS", "CLAUDE", "SQUADS_DIR", "APROVACOES", "FILA", "QUADRO", "LANCAR")


def iso(age_s: float) -> str:
    return datetime.fromtimestamp(time.time() - age_s, timezone.utc).isoformat()


class Base(unittest.TestCase):
    CFG = cfg.OPS

    def setUp(self):
        cfg.usar(self, self.CFG)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = Path(self.tmp.name)
        self.tasks, self.squads, self.quadro, self.fila = r / "tasks", r / "squads", r / "quadro", r / "fila"
        for d in (self.tasks, self.squads, self.quadro, self.fila, r / "claude"):
            d.mkdir()
        old = tuple(getattr(server, g) for g in GLOBAIS)
        self.addCleanup(lambda: [setattr(server, g, v) for g, v in zip(GLOBAIS, old)])
        (server.TASKS, server.CLAUDE, server.SQUADS_DIR, server.APROVACOES, server.FILA, server.QUADRO,
         server.LANCAR) = (self.tasks, r / "claude", self.squads, {}, r / "decisoes", self.quadro, self.fila)
        server._cache["at"] = 0.0
        self.addCleanup(server._cache.__setitem__, "at", 0.0)

    def meta(self, tid, root=None, **kw):
        m = {"id": tid, "project": "loja", "mode": "task", "status": "concluída", "started_at": iso(3600),
             "finished_at": iso(60), **kw}
        p = (root or self.tasks) / tid / "meta.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(m))


class SemConstantesTest(Base):
    def test_globais_de_todos_os_modulos(self):
        self.assertEqual((server.SQUADS, server.LEADS, server.FORMATO),
                         (("ops",), {"ops": "chefe-ops"}, {"ops": "worktree"}))
        self.assertEqual(quadro.COLUNAS[3], ("a-espera", "À espera de Rita"))
        self.assertEqual((quadro.chaves(), quadro.DEV_PROJECTOS, quadro.WORKTREE), ({"loja", "outro"}, ("loja",),
                                                                                   ("ops",)))
        self.assertEqual((lancar.SQUADS, lancar.KINDS, lancar.MODES, lancar.PROJECTOS),
                         (("ops",), {"ops": ("infra",)}, {"ops": ("task",)}, ("loja",)))
        self.assertEqual((push.SQUADS, push.PREFS["squads"]), ({"ops": "Ops"}, ("ops",)))
        self.assertEqual(biblioteca.RAIZES, ())
        self.assertEqual(server.PUSH.contacto, "mailto:office@localhost")
        self.assertEqual(str(server.LOCAL_TZ), "UTC")

    def test_state_so_squads_da_config(self):
        self.meta("T1", squad="ops", pr=f"{PR} (open)")
        self.meta("T2")                       # sem squad: a 1.ª squad worktree (ops)
        self.meta("T3", squad="dev")          # squad fora da config: ignorada
        self.meta("T4", squad="marketing")
        self.meta("S1", root=self.squads / "dev")   # pasta de squad que não existe na config: não se lê
        st = server.build_state()
        self.assertEqual(sorted((t["id"], t["squad"], t["lead"]["role"]) for t in st["tasks"]),
                         [("T1", "ops", "chefe-ops"), ("T2", "ops", "chefe-ops")])
        self.assertEqual(set(st["totals"]), {"ops"})
        self.assertEqual(set(st["history"][-1]) - {"date"}, {"ops"})
        self.assertEqual(st["waiting"][0]["squad"], "ops")
        self.assertEqual(server.transcript("T3", ""), None)
        self.assertEqual(server.transcript("T1", "squad=dev"), None)


class HttpTest(Base):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"

    def req(self, method, path, data=None):
        c = http.client.HTTPConnection(self.host, timeout=5)
        try:
            h = {"Content-Type": "application/json", "Origin": f"http://{self.host}"} if data is not None else {}
            c.request(method, path, body=json.dumps(data).encode() if data is not None else None, headers=h)
            r = c.getresponse()
            return r.status, json.loads(r.read())
        finally:
            c.close()

    def test_quadro_e_lancar_aceitam_ops_loja_e_recusam_dev_exemplo(self):
        self.meta("T1", squad="ops", pr=f"{PR} (open)")
        code, q = self.req("GET", "/api/quadro")
        self.assertEqual(code, 200)
        self.assertEqual(q["colunas"][3], {"chave": "a-espera", "rotulo": "À espera de Rita"})
        self.assertEqual([p["chave"] for p in q["projectos"]], ["loja", "outro"])
        self.assertEqual(q["dev_projectos"], ["loja"])
        c = next(c for c in q["cartoes"] if c["id"] == "ops:T1")
        self.assertEqual((c["squad"], c["fonte"], c["projecto"], c["coluna"]), ("ops", "ops", "loja", "a-espera"))
        criar = {"op": "criar", "token": server.TOKEN, "versao": "0", "titulo": "x", "descricao": "",
                 "coluna": "a-fazer", "etiquetas": []}
        self.assertEqual(self.req("POST", "/api/quadro", {**criar, "projecto": "exemplo"}),
                         (400, {"error": "projecto desconhecido"}))
        code, out = self.req("POST", "/api/quadro", {**criar, "projecto": "loja"})
        self.assertEqual(code, 200)
        base = {"token": server.TOKEN, "versao": out["versao"], "cartao_id": out["cartoes"][0]["id"], "posicao": 0,
                "squad": "ops", "projecto": "loja", "kind": "infra", "mode": "task", "slug": "abc",
                "titulo": "t", "brief": "b"}
        self.assertEqual(self.req("POST", "/api/quadro/lancar", {**base, "squad": "dev"}),
                         (400, {"error": "squad inválida"}))
        self.assertEqual(self.req("POST", "/api/quadro/lancar", {**base, "projecto": "exemplo"}),
                         (400, {"error": "projecto inválido"}))
        code, out = self.req("POST", "/api/quadro/lancar", base)
        self.assertEqual(code, 202)
        doc = json.loads((self.fila / f"{out['id']}.json").read_text())
        self.assertEqual((doc["squad"], doc["projecto"], doc["kind"], doc["mode"]), ("ops", "loja", "infra", "task"))

    def test_state_e_config(self):
        self.meta("T1", squad="ops")
        code, st = self.req("GET", "/api/state")
        self.assertEqual((code, [t["squad"] for t in st["tasks"]]), (200, ["ops"]))
        self.assertNotIn("probes", st)
        self.assertNotIn("crons", st)
        code, c = self.req("GET", "/api/config")
        self.assertEqual((c["squads"][0]["id"], c["lancar"], c["aviso"]),
                         ("ops", {"squads": ["ops"], "projectos": ["loja"]}, None))


class PrefsTest(Base):
    def test_prefs_com_as_squads_da_config(self):
        p = push.prefs_omissao()
        self.assertEqual(p["squads"], {"ops": True})
        self.assertIsNone(push.validar_prefs(p))
        self.assertEqual(push.validar_prefs({**p, "squads": {"dev": True}}), "campos desconhecidos")
        self.assertEqual(push.validar_prefs({**p, "squads": {}}), "preferências inválidas")
        # prefs guardadas com outra config: squads novas entram ligadas, as que saíram caem
        velho = {**p, "squads": {"dev": False, "ops": False}}
        self.assertEqual(push.normalizar_prefs(velho)["squads"], {"ops": False})
        self.assertEqual(push.normalizar_prefs({**p, "squads": {"dev": False}})["squads"], {"ops": True})


class MultiTest(Base):
    CFG = cfg.MULTI   # dev + ops worktree, marketing sessao

    def test_metas_das_duas_formas_e_cartoes(self):
        self.meta("T1", squad="ops", mode="goal", status="concluída", pr=f"{PR} (open)")
        self.meta("T2", project="Loja Online")                      # sem squad → dev; projecto pelo nome
        self.meta("M1", root=self.squads / "marketing", project="Marketing", mode="tarefa")
        self.meta("M2", root=self.squads / "marketing", squad="ops")  # a pasta manda nas squads sessao
        st = server.build_state()
        self.assertEqual(sorted((t["id"], t["squad"], t["lead"]["role"]) for t in st["tasks"]),
                         [("M1", "marketing", "cmo"), ("M2", "marketing", "cmo"), ("T1", "ops", "chefe-ops"),
                          ("T2", "dev", "tech-lead")])
        cartoes = {c["id"]: c for c in quadro.cartoes_auto(st, {}, timezone.utc)}
        self.assertEqual({k: (c["squad"], c["projecto"]) for k, c in cartoes.items()},
                         {"ops:T1": ("ops", "loja"), "dev:T2": ("dev", "loja"), "marketing:M1": ("marketing", "outro"),
                          "marketing:M2": ("marketing", "outro")})
        self.assertEqual(cartoes["ops:T1"]["coluna"], "a-espera")

    def test_push_por_squad(self):
        t = {"id": "T1", "squad": "ops", "project": "loja", "mode": "goal", "status": "a correr", "running": True,
             "finished": None, "pr": f"{PR} (open)", "gh": {}, "phases": {"SPEC": True}}
        evs = {e["chave"]: e for e in push.eventos({"tasks": [t]}, time.time(), server.DONE_STATUS)}
        self.assertIn("pr:ops:T1:4", evs)
        fase = evs["fase:ops:T1:SPEC"]
        self.assertEqual((fase["squad"], fase["payload"]["title"]), ("ops", "📍 Ops · loja: SPEC"))
        m = {**t, "squad": "marketing", "id": "M1"}
        self.assertEqual([k for k in (e["chave"] for e in push.eventos({"tasks": [m]}, time.time(), ()))], [])


class ColunaEsperaTest(unittest.TestCase):
    # AC-a5
    def test_rotulo_por_omissao_e_proprio(self):
        cfg.usar(self, cfg.EXEMPLO)
        self.assertEqual(dict(quadro.COLUNAS)["a-espera"], "À espera de Utilizador")
        proprio = cfg.ler("utilizador:\n  nome: Rita\nsquads:\n  - id: dev\n    nome: Dev\nprojectos:\n"
                          "  - id: loja\n    nome: Loja\ncolunas:\n  a-espera: Para rever\n  feito: Pronto\n")
        cfg.usar(self, proprio)
        self.assertEqual(quadro.COLUNAS, (("ideias", "Ideias"), ("a-fazer", "A fazer"), ("em-curso", "Em curso"),
                                          ("a-espera", "Para rever"), ("feito", "Pronto")))


if __name__ == "__main__":
    unittest.main()
