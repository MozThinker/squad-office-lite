"""Testes HTTP do /api/quadro (servidor real em porta 0, dados falsos em pasta temporária; só stdlib)."""
import hashlib
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import server
from tests.fake import gh_token
from tests import cfg

OFFICE_CFG = cfg.LEGADO

GLOBAIS = ("TASKS", "CLAUDE", "SQUADS_DIR", "APROVACOES", "FILA", "QUADRO")
PR = "https://github.com/exemplo-org/squad-office/pull/15"


class QuadroHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tasks, self.mkt, self.quadro = root / "tasks", root / "squads" / "marketing", root / "quadro"
        for d in (self.tasks, self.mkt, self.quadro, root / "claude"):
            d.mkdir(parents=True)
        self.f = self.quadro / "cartoes.json"
        self.old = tuple(getattr(server, g) for g in GLOBAIS)
        (server.TASKS, server.CLAUDE, server.SQUADS_DIR, server.APROVACOES, server.FILA, server.QUADRO) = (
            self.tasks, root / "claude", root / "squads", {}, root / "fila", self.quadro)
        server._cache["at"] = 0.0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for g, v in zip(GLOBAIS, self.old):
            setattr(server, g, v)
        server._cache["at"] = 0.0
        self.tmp.cleanup()

    # --- ajudas ---
    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection(self.host, timeout=5)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"null"), dict(r.getheaders())
        finally:
            c.close()

    def get(self):
        code, out, h = self.req("GET", "/api/quadro")
        self.assertEqual((code, h.get("Cache-Control")), (200, "no-store"))
        return out

    def versao(self):
        return hashlib.sha256(self.f.read_bytes()).hexdigest() if self.f.exists() else "0"

    def post(self, data=None, raw=None, **headers):
        h = {"Content-Type": "application/json", "Origin": f"http://{self.host}"}
        h.update({k.replace("_", "-"): v for k, v in headers.items()})
        h = {k: v for k, v in h.items() if v is not None}
        payload = raw if raw is not None else json.dumps(data).encode()
        code, out, _ = self.req("POST", "/api/quadro", payload, h)
        return code, out

    def criar(self, **kw):
        d = {"op": "criar", "token": server.TOKEN, "versao": self.versao(), "titulo": "Ideia", "descricao": "",
             "projecto": "loja", "coluna": "ideias", "etiquetas": []}
        d.update(kw)
        return self.post(d)

    def meta(self, tid, **kw):
        m = {"id": tid, "project": "Loja", "mode": "goal", "status": "concluída", "pr": "",
             "started_at": "2026-11-03T09:00:00", "finished_at": "2026-11-03T10:00:00"}
        m.update(kw)
        p = self.tasks / tid / "meta.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(m))
        return p

    # --- GET ---
    def test_get_vazio(self):
        out = self.get()
        self.assertEqual(set(out), {"v", "agora", "versao", "escrita", "invalidos", "aviso", "token", "colunas",
                                    "projectos", "cartoes", "lancar", "dev_projectos"})
        self.assertEqual((out["v"], out["versao"], out["escrita"], out["invalidos"], out["aviso"], out["cartoes"]),
                         (1, "0", True, 0, None, []))
        self.assertEqual(out["token"], server.TOKEN)
        self.assertEqual(len(out["colunas"]), 5)

    def test_get_sem_pasta(self):
        server.QUADRO = Path(self.tmp.name) / "nao"
        out = self.get()
        self.assertEqual((out["escrita"], out["versao"], out["aviso"]), (False, "0", None))

    def test_projectos_da_config_valem_nos_manuais(self):
        self.assertIn("loja", [p["chave"] for p in self.get()["projectos"]])
        self.assertEqual(self.criar(projecto="loja")[0], 200)
        self.assertEqual(self.criar(projecto="loja-nova")[1], {"error": "projecto desconhecido"})

    def test_aux_do_build_state(self):
        # task com inbox/<nome> conta (todas as idades); dry-run não; result das squads mascarado no detalhe
        inbox = self.tasks / "inbox"
        inbox.mkdir()
        for n in ("dev-lancado-por-ref.md", "dev-ensaio-so.md", "dev-velho-lancado.md"):
            (inbox / n).write_text(f"# {n}\n")
        self.meta("20261103-0900-qualquer", task="ver ~/projects/.tasks/inbox/dev-lancado-por-ref.md")
        self.meta("20261103-0901-ensaio-so", mode="dry-run")
        velha = self.meta("20260901-0900-velho-lancado")
        os.utime(velha, (time.time() - 60 * 86400,) * 2)
        mk = self.mkt / "20261103-0700-mkt-gestor" / "meta.json"
        mk.parent.mkdir()
        mk.write_text(json.dumps({"id": "20261103-0700-mkt-gestor", "mode": "gestor", "squad": "marketing",
                                  "status": "concluída", "started_at": "2026-11-03T07:00:00",
                                  "result": f"feito {gh_token()} " + "x" * 400}))
        # brief lançado com outro nome: o task da meta começa pelo mesmo «# »
        (inbox / "loja-fix-x.md").write_text("# Loja: Prompt (saúde)\n")
        self.meta("20261103-0902-outro-nome", task="#  loja: prompt (SAÚDE)\n\nrelatório")
        out = self.get()
        self.assertEqual([c["id"] for c in out["cartoes"] if c["fonte"] == "brief"], ["brief:dev-ensaio-so.md"])
        titulos = {c["id"]: c["titulo"] for c in out["cartoes"]}
        self.assertEqual(titulos["dev:20261103-0902-outro-nome"], "loja: prompt (SAÚDE)")
        self.assertEqual(titulos["dev:20261103-0900-qualquer"], "qualquer")   # sem «# » no task: o slug
        c = next(c for c in out["cartoes"] if c["id"] == "marketing:gestor:2026-11-03")   # rotina agrupada
        self.assertEqual(c["titulo"], "Gestor · 1 execução")
        res = c["detalhe"]["execucoes"][0]["resultado"]
        self.assertTrue(res.startswith("feito [segredo] x"))
        self.assertLessEqual(len(res), 100)
        self.assertNotIn("_aux", self.req("GET", "/api/state")[1])

    def test_ca8_meta_muda_e_o_cartao_muda_de_coluna(self):
        p = self.meta("20261103-0900-coisa", pr=f"{PR} (open)")
        col = {c["id"]: c["coluna"] for c in self.get()["cartoes"]}
        self.assertEqual(col["dev:20261103-0900-coisa"], "a-espera")
        p.write_text(p.read_text().replace("(open)", "(merged)"))
        server._cache["at"] = 0.0   # cache de 5 s expirada
        col = {c["id"]: c["coluna"] for c in self.get()["cartoes"]}
        self.assertEqual(col["dev:20261103-0900-coisa"], "feito")

    def test_ficheiro_invalido_get_200_e_post_503(self):
        self.meta("20261103-0900-coisa")
        self.f.write_text("{mau")
        out = self.get()
        self.assertEqual((out["escrita"], out["aviso"]), (False, "cartoes.json inválido"))
        self.assertEqual([c["id"] for c in out["cartoes"]], ["dev:20261103-0900-coisa"])
        self.assertEqual(self.criar(), (503, {"error": "cartoes.json inválido — corrige à mão"}))
        self.assertEqual(self.f.read_text(), "{mau")

    # --- POST ---
    def test_ops_200(self):
        code, out = self.criar(titulo="A", etiquetas=["x"])
        self.assertEqual(code, 200)
        self.assertEqual(out["versao"], self.versao())
        cid = out["cartoes"][0]["id"]
        self.assertEqual(self.criar(titulo="B")[0], 200)
        base = {"token": server.TOKEN}
        code, out = self.post({**base, "op": "editar", "versao": self.versao(), "id": cid, "titulo": "A2",
                               "descricao": "d", "projecto": "joga", "etiquetas": []})
        self.assertEqual((code, out["cartoes"][0]["titulo"]), (200, "A2"))
        code, out = self.post({**base, "op": "mover", "versao": self.versao(), "id": cid, "coluna": "ideias",
                               "posicao": 1})
        self.assertEqual((code, [c["titulo"] for c in out["cartoes"]]), (200, ["B", "A2"]))
        code, out = self.post({**base, "op": "apagar", "versao": self.versao(), "id": cid})
        self.assertEqual((code, [c["titulo"] for c in out["cartoes"]]), (200, ["B"]))
        self.assertEqual([c["titulo"] for c in self.get()["cartoes"]], ["B"])

    def test_403_origem_token_content_type(self):
        d = {"op": "apagar", "token": server.TOKEN, "versao": "0", "id": "m-1"}
        self.assertEqual(self.post(d, Origin="http://evil.example"), (403, {"error": "origem não permitida"}))
        self.assertEqual(self.post(d, Origin="null"), (403, {"error": "origem não permitida"}))
        self.assertEqual(self.post(d, Origin=None), (403, {"error": "origem não permitida"}))
        self.assertEqual(self.post(d, Origin=None, Sec_Fetch_Site="same-origin"),
                         (404, {"error": "cartão não encontrado"}))
        self.assertEqual(self.post({**d, "token": "x"}), (403, {"error": "token inválido, recarrega"}))
        self.assertEqual(self.post({k: v for k, v in d.items() if k != "token"}),
                         (403, {"error": "token inválido, recarrega"}))
        self.assertEqual(self.post(d, Content_Type="text/plain"), (403, {"error": "Content-Type inválido"}))
        self.assertFalse(self.f.exists())

    def test_413_e_limite_de_16k(self):
        c = http.client.HTTPConnection(self.host, timeout=5)
        try:
            c.putrequest("POST", "/api/quadro")
            for k, v in (("Content-Type", "application/json"), ("Origin", f"http://{self.host}"),
                         ("Content-Length", "16385")):
                c.putheader(k, v)
            c.endheaders()   # sem corpo: tem de responder sem o ler
            r = c.getresponse()
            self.assertEqual((r.status, json.loads(r.read())), (413, {"error": "corpo grande demais"}))
        finally:
            c.close()
        # 9 KB (acima do limite do /api/decisao) passa no quadro
        self.assertEqual(self.criar(descricao="x" * 2000, titulo="t" * 120, etiquetas=["é" * 24] * 1)[0], 200)
        raw = json.dumps({"op": "criar", "token": server.TOKEN, "versao": self.versao(), "titulo": "x",
                          "descricao": "", "projecto": "loja", "coluna": "ideias", "etiquetas": [],
                          "pad": " " * 9000}).encode()
        self.assertEqual(self.post(raw=raw), (400, {"error": "campos inválidos"}))

    def test_400_404_409(self):
        self.assertEqual(self.post(raw=b"{mau"), (400, {"error": "JSON inválido"}))
        self.assertEqual(self.criar(titulo=" "), (400, {"error": "título obrigatório"}))
        self.assertEqual(self.criar(coluna="x"), (400, {"error": "coluna desconhecida"}))
        d = {"op": "apagar", "token": server.TOKEN, "versao": "0"}
        self.assertEqual(self.post({**d, "id": "dev:x"}), (400, {"error": "cartão automático não se edita"}))
        self.assertEqual(self.post({**d, "id": "m-1"}), (404, {"error": "cartão não encontrado"}))
        self.assertEqual(self.criar()[0], 200)
        antes = self.f.read_bytes()
        self.assertEqual(self.criar(versao="0"), (409, {"error": "conflito", "versao": self.versao()}))
        self.assertEqual(self.f.read_bytes(), antes)

    def test_503_sem_pasta(self):
        server.QUADRO = Path(self.tmp.name) / "nao"
        self.assertEqual(self.criar(), (503, {"error": "sem pasta do quadro"}))
        self.assertFalse(server.QUADRO.exists())

    def test_outro_caminho_404_e_decisao_continua(self):
        self.assertEqual(self.post({}, raw=b"{}")[0], 403)   # sem token
        code, out, _ = self.req("POST", "/api/outra", b"{}", {"Content-Type": "application/json"})
        self.assertEqual((code, out), (404, {"error": "não encontrado"}))
        code, out, _ = self.req("POST", "/api/decisao", json.dumps({"token": server.TOKEN}).encode(),
                                {"Content-Type": "application/json", "Origin": f"http://{self.host}"})
        self.assertEqual((code, out), (400, {"error": "fonte desconhecida"}))


if __name__ == "__main__":
    unittest.main()
