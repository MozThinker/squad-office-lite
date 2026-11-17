"""E2E do arquivo da Biblioteca (AC16): POST no server.py real (thread) → passagem do runner real → ficheiro em
`<fonte>/.arquivo/` → link antigo abre → POST repor → passagem → de volta ao sítio. Nos dois modos: VPS (runner no host,
`uma_vez`) e Docker (`ciclo` com OFFICE_MODO=docker).

Tudo em tmp_path (c.ambiente: HOME e git isolados); o Office lê com um relógio fixo; os ficheiros são de há 400 dias,
por isso as protecções do runner (48 h) não dependem do relógio real.
"""
import http.client
import json
import os
import threading
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.parse import quote

import pytest

import biblioteca
import server
from tests import cfg as tcfg
from tests import test_runner_arquivo as a
from tests import test_runner_comum as c
from tests.test_runner_comum import office_runner, officecfg

NOW, DIA = a.NOW, a.DIA
ITEM = "notas/rel/x.md"
LOTE = "material/media/lote-a"


class Office:
    """server.py em thread, com os índices e a fila apontados para os dados do runner (tmp_path)."""

    def __init__(self, cfg):
        self.t = [NOW]
        server.configurar(cfg)
        dirs = biblioteca.dirs(cfg, a.bib(cfg))
        aprov = {"geral": officecfg.caminho(cfg, "decisoes")}
        tasks = officecfg.caminho(cfg, "tasks")
        self.idx = biblioteca.Indice(dirs, tasks, aprov, clock=lambda: self.t[0])
        self.arq = biblioteca.Indice(dirs, tasks, aprov, clock=lambda: self.t[0], arquivo=True)
        self.patch = mock.patch.multiple(server, BIBLIOTECA=self.idx, BIBLIOTECA_ARQ=self.arq,
                                         ARQUIVO_FILA=officecfg.caminho(cfg, "arquivo_fila"))
        self.patch.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"

    def fechar(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.patch.stop()
        tcfg.repor()

    def req(self, path, method="GET", body=None, headers=None):
        con = http.client.HTTPConnection(self.host, timeout=10)
        try:
            con.request(method, path, body=body, headers=headers or {})
            r = con.getresponse()
            return r.status, r.read()
        finally:
            con.close()

    def post(self, accao, item):
        corpo = json.dumps({"token": server.TOKEN, "accao": accao, "item": item}).encode()
        code, body = self.req("/api/biblioteca/arquivo", "POST", corpo,
                              {"Content-Type": "application/json", "Origin": f"http://{self.host}",
                               "User-Agent": "e2e/1"})
        return code, json.loads(body)

    def refazer(self):
        # o runner mudou o disco: passa o TTL e espera pelo varrimento em fundo dos dois índices
        self.t[0] += biblioteca.TTL_S + 1
        for i in (self.idx, self.arq):
            i.get()
            i.aguardar(5)

    def item(self, iid):
        code, body = self.req("/api/biblioteca/item?id=" + quote(iid, safe=""))
        return json.loads(body) if code == 200 else code

    def f(self, chave):
        return self.req("/api/biblioteca/f/" + "/".join(quote(s, safe="") for s in chave.split("/")))


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    monkeypatch.delenv("OFFICE_SEGREDOS_DIR", raising=False)
    monkeypatch.delenv("OFFICE_MODO", raising=False)
    cfg, path = a.config(tmp_path)
    a.w(cfg, "notas/rel/x.md", "# Relatório velho\n\nTexto do relatório.\n", idade=400 * DIA)
    a.w(cfg, "notas/rel/x.copy.md", "# Copy\n", idade=400 * DIA)
    a.w(cfg, "notas/rel/y.md", "# Fica\n", idade=400 * DIA)
    a.w(cfg, "material/media/lote-a/final/a.jpg", "jpg", idade=400 * DIA)
    o = Office(cfg)
    yield cfg, path, o
    o.fechar()


def passagem(modo, cfg, path, monkeypatch):
    if modo == "vps":
        return office_runner.uma_vez(cfg, str(path), NOW)
    monkeypatch.setenv("OFFICE_MODO", "docker")
    try:
        assert office_runner.ciclo(str(path), intervalo=0, passagens=1) == 0
    finally:
        monkeypatch.delenv("OFFICE_MODO", raising=False)
    return None


@pytest.mark.parametrize("modo", ["vps", "docker"])
def test_arquivar_e_repor_pelo_office(ctx, monkeypatch, modo):
    cfg, path, o = ctx
    n = a.bib(cfg) / "notas"
    assert o.f(ITEM)[0] == 200
    assert o.item(ITEM)["arquivo"]["arquivavel"] is True

    code, d = o.post("arquivar", ITEM)
    assert code == 202 and d["accao"] == "arquivar"
    assert o.post("arquivar", ITEM)[0] == 409       # já há um pedido pendente para o item
    assert o.post("repor", ITEM) == (400, {"error": "item inexistente"})   # ainda não está no arquivo
    assert o.item(ITEM)["arquivo"]["pendente"] == "arquivar"
    assert (n / "rel/x.md").exists()                # o Office nunca move nada

    passagem(modo, cfg, path, monkeypatch)
    assert a.feito(cfg, d["id"]) == {"ok": True}
    assert not (n / "rel/x.md").exists() and not (n / "rel/x.copy.md").exists()
    assert (n / ".arquivo/rel/x.md").exists() and (n / ".arquivo/rel/x.copy.md").exists()
    assert (n / "rel/y.md").exists()

    o.refazer()
    ids = {i["id"] for i in json.loads(o.req("/api/biblioteca")[1])["itens"]}
    assert ITEM not in ids
    assert ITEM in {i["id"] for i in json.loads(o.req("/api/biblioteca?arquivo=1")[1])["itens"]}
    det = o.item(ITEM)
    assert det["arquivo"]["arquivado"]["motivo"] == "pedido no Office"
    assert det["arquivo"]["pendente"] is None and det["arquivo"]["erro"] is None
    code, body = o.f(ITEM)                          # link antigo continua a abrir
    assert code == 200 and body.startswith(b"# Relat")

    code, d2 = o.post("repor", ITEM)
    assert code == 202
    passagem(modo, cfg, path, monkeypatch)
    assert a.feito(cfg, d2["id"]) == {"ok": True}
    assert (n / "rel/x.md").exists() and (n / "rel/x.copy.md").exists()
    assert not (n / ".arquivo/rel/x.md").exists()

    o.refazer()
    assert ITEM in {i["id"] for i in json.loads(o.req("/api/biblioteca")[1])["itens"]}
    assert o.item(ITEM)["arquivo"]["arquivado"] is None
    assert o.f(ITEM)[0] == 200                      # o mesmo link abre de novo


@pytest.mark.parametrize("modo", ["vps", "docker"])
def test_lote_e_erro_do_runner_visivel_no_office(ctx, monkeypatch, modo):
    cfg, path, o = ctx
    m = a.bib(cfg) / "material"
    code, d = o.post("arquivar", LOTE)
    assert code == 202
    passagem(modo, cfg, path, monkeypatch)
    assert (m / ".arquivo/media/lote-a/final/a.jpg").exists() and not (m / "media/lote-a").exists()
    # protecção: um pedido de aprovação pendente cita o documento ⇒ o runner recusa e o Office mostra o motivo
    a.aprovacao(cfg, "p.md", "pendente", "notas/rel/y.md")
    code, d = o.post("arquivar", "notas/rel/y.md")
    assert code == 202
    passagem(modo, cfg, path, monkeypatch)
    assert a.feito(cfg, d["id"]) == {"erro": "protegido: citado por um pedido de aprovação pendente"}
    assert (a.bib(cfg) / "notas/rel/y.md").exists()
    assert o.item("notas/rel/y.md")["arquivo"]["erro"] == "protegido: citado por um pedido de aprovação pendente"


def test_tarefa_nao_arquivavel_nem_chega_a_fila(ctx):
    cfg, _, o = ctx
    t = officecfg.caminho(cfg, "tasks") / "20261101-1000-a"
    t.mkdir()
    (t / "SPEC.md").write_text("# A\n", encoding="utf-8")
    o.refazer()
    code, d = o.post("arquivar", "tarefas/20261101-1000-a")
    assert (code, d) == (400, {"error": "não arquivável"})
    assert os.listdir(officecfg.caminho(cfg, "arquivo_fila")) == []
