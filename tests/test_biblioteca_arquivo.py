"""Arquivo da Biblioteca do lado do Office (ADR-011): «Ver arquivados» (`?arquivo=1`), o link antigo de um item
arquivado e o POST que só escreve na fila. Quem move é o runner: aqui simula-se à mão (os.rename + REGISTO.tsv), como o
runner faria. Pasta temporária e `NOW` fixo, como o test_biblioteca."""
import io
import json
import os
import zipfile
from datetime import timezone
from unittest import mock
from urllib.parse import quote

import arquivo
import biblioteca
import server
from tests import cfg
from tests.test_biblioteca import LOTE, NAO, NOW, Http
from tests.test_biblioteca_rapida import Espiao

OFFICE_CFG = cfg.LEGADO
DOC = "relatorios/2026-10-01-velho.md"
FOTO = f"{LOTE}/final/status-01-quem-9x16.jpg"


class Base(Http):
    def setUp(self):
        super().setUp()
        self.arq = server.BIBLIOTECA_ARQ
        self.fila = server.ARQUIVO_FILA
        self.fila.mkdir()
        self.w(DOC, "# Relatório velho\n\nUma frase do relatório velho.\n", idade=400 * 3600)
        self.w("relatorios/2026-10-01-velho.revisao.md", "# Revisão\n", idade=399 * 3600)
        self.w("rascunhos/fotos/a.jpg", b"foto-solta")   # «pasta»: não arquivável
        self.w("tasks/20261101-1000-a/SPEC.md", "# A\n")

    def mover(self, item: str, para_arquivo: bool = True, motivo: str = "pedido no Office", registo: bool = True):
        # o que o runner faz (só para os testes do Office): rename do item inteiro e uma linha por ficheiro no REGISTO
        fonte, _, chave = item.partition("/")
        root = self.root / fonte
        de, para = (root / chave, root / arquivo.DIR / chave) if para_arquivo else (root / arquivo.DIR / chave,
                                                                                    root / chave)
        alvos = [de] if de.is_dir() else sorted(de.parent.glob(arquivo.raiz_nome(de.name) + ".*"))
        para.parent.mkdir(parents=True, exist_ok=True)
        linhas = []
        for a in alvos:
            fs = [a] if a.is_file() else sorted(p for p in a.rglob("*") if p.is_file())
            linhas += [str(p.relative_to(root / arquivo.DIR if not para_arquivo else root)) for p in fs]
            os.rename(a, para.parent / a.name)
        if registo:
            reg = root / arquivo.DIR / arquivo.REGISTO
            if not reg.exists():
                reg.write_text(arquivo.CABECALHO)
            with open(reg, "a") as fp:
                fp.writelines(arquivo.linha_registo("2026-11-01T10:00:00+01:00",
                                                    "arquivar" if para_arquivo else "repor", c, motivo, chave)
                              for c in linhas)
            os.utime(reg, (self.now, self.now))   # o runner escreve no REGISTO logo depois do move (relógio dos testes)

    def refazer(self, *idx):
        self.now += biblioteca.TTL_S + 1
        for i in idx or (self.idx, self.arq):
            i.get()
            i.aguardar(5)

    def item(self, iid):
        code, d = self.get_json("/api/biblioteca/item?id=" + quote(iid, safe=""))
        return d if code == 200 else code


class VerArquivadosTest(Base):
    def test_lista_so_a_pedido_com_registo(self):
        self.mover(DOC)
        self.mover(LOTE, motivo="idade (180 dias)")
        self.assertEqual(self.arq.scans, 0)
        code, d = self.get_json("/api/biblioteca")
        self.assertEqual(code, 200)
        ids = {i["id"] for i in d["itens"]}
        self.assertNotIn(DOC, ids)
        self.assertNotIn(LOTE, ids)
        self.assertFalse([i for i in ids if "/.arquivo" in i])
        self.assertEqual(self.arq.scans, 0)   # a lista principal não toca no índice do arquivo
        code, h, body = self.req("/api/biblioteca?arquivo=1")
        self.assertEqual((code, h["cache-control"]), (200, "private, no-cache"))
        itens = {i["id"]: i for i in json.loads(body)["itens"]}
        self.assertEqual(set(itens), {DOC, LOTE})   # mesmos ids que antes de arquivar
        self.assertEqual(itens[DOC]["arquivado"], {"em": "2026-11-01T10:00:00+01:00", "motivo": "pedido no Office"})
        self.assertEqual(itens[LOTE]["arquivado"]["motivo"], "idade (180 dias)")
        self.assertEqual(itens[DOC]["principal"]["url"].split("?")[0], f"api/biblioteca/f/{DOC}")
        self.assertNotIn("arquivado", json.loads(self.req("/api/biblioteca")[2])["itens"][0])
        # ETag/304 iguais aos da lista principal
        code, _, body2 = self.req("/api/biblioteca?arquivo=1", headers={"If-None-Match": h["etag"]})
        self.assertEqual((code, body2), (304, b""))
        self.assertNotEqual(h["etag"], self.req("/api/biblioteca")[1]["etag"])
        self.assertEqual(self.req("/api/biblioteca?arquivo=1", "HEAD")[0], 405)

    def test_sem_registo_e_reposto(self):
        self.mover(DOC, registo=False)   # movido à mão: sem data nem motivo
        self.assertEqual(self.arq.get().por_id[DOC]["publico"]["arquivado"], {"em": None, "motivo": None})
        self.w("relatorios/.arquivo/REGISTO.tsv", arquivo.CABECALHO + arquivo.linha_registo(
            "2026-10-20T09:00:00+00:00", "arquivar", "2026-10-01-velho.md", "à mão", "outro-item"))
        self.refazer(self.arq)   # sem linha do item, vale a do 1.º ficheiro próprio
        self.assertEqual(self.arq.get().por_id[DOC]["publico"]["arquivado"],
                         {"em": "2026-10-20T09:00:00+00:00", "motivo": "à mão"})
        with open(self.root / "relatorios/.arquivo/REGISTO.tsv", "a") as fp:
            fp.write(arquivo.linha_registo("2026-10-21T09:00:00+00:00", "repor", "2026-10-01-velho.md", "x",
                                           "2026-10-01-velho.md"))
        self.refazer(self.arq)
        self.assertEqual(self.arq.get().por_id[DOC]["publico"]["arquivado"], {"em": None, "motivo": None})

    def test_mesmas_regras_de_seguranca(self):
        self.w("relatorios/.arquivo/segredo.md", "# s")
        self.w("relatorios/.arquivo/a.jpg", b"jpg")            # media em fonte media: false
        self.w("relatorios/.arquivo/.git/a.md", "# g")
        self.w("relatorios/.arquivo/ok.md", "# ok")
        fora = self.root / "fora"
        fora.mkdir()
        self.w("fora/x.md", "# fora")
        os.symlink(fora, self.root / "rascunhos" / ".arquivo")   # `.arquivo` symlink: ignorada
        os.symlink(fora / "x.md", self.root / "relatorios" / ".arquivo" / "l.md")
        ids = {i["id"] for i in self.get_json("/api/biblioteca?arquivo=1")[1]["itens"]}
        self.assertEqual(ids, {"relatorios/ok.md"})
        for k in ("relatorios/segredo.md", "relatorios/a.jpg", "relatorios/.git/a.md", "rascunhos/x.md",
                  "relatorios/l.md", "relatorios/.arquivo/ok.md", "relatorios/REGISTO.tsv"):
            with self.subTest(k=k):
                self.assertEqual(self.get_json(self.f(k)), (404, NAO))
        self.assertEqual(self.req(self.f("relatorios/ok.md"))[0], 200)

    def test_registo_grande_le_a_cauda(self):
        # maior que REGISTO_MAX: lê-se o fim (a última linha ganha) e a 1.ª linha partida da cauda descarta-se
        self.mover(DOC, registo=False)

        def linha(motivo, caminho="2026-10-01-velho.md", item="2026-10-01-velho.md"):
            return arquivo.linha_registo("2026-10-02T09:00:00+00:00", "arquivar", caminho, motivo, item)

        cortada = linha("cortada")
        meio = "".join(linha("outro", f"outro-{i}.md", f"outro-{i}.md") for i in range(50))
        ultima = linha("a última")
        n = len((cortada + meio).encode())
        casos = ((cortada + meio + ultima, n + len(ultima.encode()) - 5, "a última"),   # a última linha, já fora do início
                 (cortada + meio, n - 5, None),        # 5 bytes dentro da linha do item: partida, descartada
                 (cortada + meio, n, "cortada"),       # a cauda começa no início da linha: fica inteira
                 (cortada + meio, n + 999, "cortada"))   # cabe toda
        for texto, maximo, motivo in casos:
            with self.subTest(maximo=maximo), mock.patch.object(biblioteca, "REGISTO_MAX", maximo):
                self.w("relatorios/.arquivo/REGISTO.tsv", arquivo.CABECALHO + texto)
                self.refazer(self.arq)
                a = self.arq.get().por_id[DOC]["publico"]["arquivado"]
                self.assertEqual(a, {"em": "2026-10-02T09:00:00+00:00" if motivo else None, "motivo": motivo})

    def test_tarefas_e_pedidos_nunca_vem_do_arquivo(self):
        self.w("tasks/.arquivo/x/SPEC.md", "# x")
        self.w("aprov-mkt/.arquivo/2026-10-01-p.md", "---\nestado: aprovado\ncriado: 2026-10-01\n---\n# p\n")
        d = self.get_json("/api/biblioteca?arquivo=1")[1]
        self.assertFalse([i for i in d["itens"] if i["raiz"] == "tarefas"])
        for k in ("tarefas/x/SPEC.md", "aprovacoes-marketing/2026-10-01-p.md", "tarefas/20261101-1000-a/notas.md"):
            self.assertEqual(self.get_json(self.f(k)), (404, NAO))

    def test_sem_indice_de_arquivo(self):
        with mock.patch.object(server, "BIBLIOTECA_ARQ", None):
            self.assertEqual(self.get_json("/api/biblioteca?arquivo=1"), (404, NAO))
            self.assertEqual(self.get_json(self.f("relatorios/nao.md")), (404, NAO))


class LinkAntigoTest(Base):
    def test_link_antigo_abre_e_reposto_volta(self):
        s = self.idx.get()
        url_f = "/" + s.por_id[LOTE]["detalhe"]["destaque"][0]["url"]
        url_mini = "/" + s.por_id[LOTE]["publico"]["minis"][0]
        url_doc = "/" + s.por_id[DOC]["publico"]["principal"]["url"]
        corpo_f = self.req(url_f)[2]
        self.mover(LOTE)
        self.mover(DOC)
        # o índice principal ainda é o de antes (dentro do TTL): /f/ e /mini/ caem na `.arquivo/`, sem lock
        e1, e2 = Espiao(), Espiao()
        self.idx._lock, self.arq._lock = e1, e2
        for u in (url_f, url_mini, url_doc, self.f(FOTO), self.f(DOC, "?dl=1")):
            with self.subTest(u=u):
                self.assertEqual(self.req(u)[0], 200)
                self.assertEqual(self.req(u, "HEAD")[0], 200 if "/mini/" not in u else 405)
        self.assertEqual(self.req(url_f)[2], corpo_f)
        self.assertEqual((e1.toques, e2.toques, self.arq.scans), (0, 0, 0))
        self.idx._lock, self.arq._lock = biblioteca.threading.Lock(), biblioteca.threading.Lock()
        self.refazer(self.idx)
        self.assertNotIn(LOTE, self.idx.get().por_id)
        # /item e /zip do item arquivado: o índice do arquivo (mesmo id e mesmos caminhos de ficheiro)
        d = self.item(LOTE)
        self.assertEqual((d["item"]["id"], d["foco"]), (LOTE, None))
        self.assertEqual(d["item"]["arquivado"]["motivo"], "pedido no Office")
        self.assertEqual(d["arquivo"], {"arquivavel": True, "arquivado": d["item"]["arquivado"], "pendente": None,
                                        "erro": None})
        self.assertEqual(self.req("/" + d["destaque"][0]["url"])[0], 200)
        self.assertEqual((self.item(FOTO)["foco"], self.item(FOTO)["item"]["id"]), (FOTO, LOTE))
        code, h, body = self.req("/api/biblioteca/zip?id=" + quote(LOTE, safe=""))
        self.assertEqual((code, h["content-type"], body[:2]), (200, "application/zip", b"PK"))
        self.assertEqual(self.item(DOC)["item"]["genero"], "documento")
        # repor: volta a abrir pelo mesmo link e o /item volta a ser o do índice principal
        self.mover(LOTE, para_arquivo=False, motivo="reposto a pedido do Office")
        # (dentro do TTL manda o índice principal, como para qualquer ficheiro novo: abre no varrimento seguinte)
        self.refazer()
        self.assertEqual(self.req(self.f(FOTO))[0], 200)
        d = self.item(LOTE)
        self.assertNotIn("arquivado", d["item"])
        self.assertEqual(d["arquivo"]["arquivado"], None)
        self.assertNotIn(LOTE, self.arq.get().por_id)

    def test_primeiro_pedido_com_indice_do_arquivo_velho(self):
        # o índice do arquivo já existe (alguém abriu «Ver arquivados») e passou o TTL_S: o 1.º /zip e o 1.º /item
        # de um link antigo esperam pela reconstrução em vez de darem 404
        self.assertEqual(self.req("/api/biblioteca?arquivo=1")[0], 200)
        self.mover(LOTE)
        self.refazer(self.idx)   # o principal já não o tem; o do arquivo fica velho
        code, _, body = self.req("/api/biblioteca/zip?id=" + quote(LOTE, safe=""))
        self.assertEqual((code, body[:2]), (200, b"PK"))
        self.mover(DOC)
        self.refazer(self.idx)
        self.assertEqual(self.item(DOC)["item"]["arquivado"]["motivo"], "pedido no Office")
        self.assertEqual(self.arq.scans, 3)
        # inexistente com o índice do arquivo velho: espera a reconstrução e continua 404
        self.now += biblioteca.TTL_S + 1
        self.assertEqual(self.item("relatorios/nunca.md"), 404)
        self.assertEqual(self.get_json("/api/biblioteca/zip?id=relatorios/nunca.md"), (404, NAO))
        self.assertEqual(self.arq.scans, 4)

    def test_zip_logo_depois_de_arquivar_vem_completo(self):
        # o índice principal ainda tem o lote (dentro do TTL), mas os ficheiros já estão na `.arquivo/`: o /zip usa o
        # índice do arquivo em vez de saltar em silêncio os ficheiros que sumiram
        self.idx.get()
        url = "/api/biblioteca/zip?id=" + quote(LOTE, safe="")

        def conteudo(body):
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                return {n: z.read(n) for n in z.namelist()}
        antes = conteudo(self.req(url)[2])
        self.mover(LOTE)
        code, _, body = self.req(url)
        self.assertEqual(code, 200)
        zip_ = conteudo(body)
        self.assertIn(LOTE, self.idx.get().por_id)   # o principal continua o de antes
        fonte, _, chave = LOTE.partition("/")
        proprios = {n: b for n, b in antes.items() if (self.root / fonte / arquivo.DIR / chave / n).is_file()}
        self.assertGreaterEqual(len(proprios), 4)
        self.assertEqual({n: zip_.get(n) for n in proprios}, proprios)   # todos os ficheiros do lote, os mesmos bytes
        self.refazer()
        self.assertEqual(conteudo(self.req(url)[2]), zip_)   # o mesmo zip que o link antigo dá com os índices em dia

    def test_link_antigo_com_indice_do_arquivo_fresco(self):
        # o índice do arquivo foi feito há < TTL_S, antes do move: o REGISTO.tsv mais novo que ele obriga a refazê-lo
        t0 = self.now
        self.idx.get()
        self.now = t0 + 30
        self.assertEqual(self.req("/api/biblioteca?arquivo=1")[0], 200)
        self.assertEqual(self.arq.scans, 1)
        self.assertEqual(self.item("relatorios/nunca.md"), 404)   # REGISTO não é mais novo: não refaz
        self.assertEqual(self.arq.scans, 1)
        self.now = t0 + 31
        self.mover(DOC)
        self.now = t0 + biblioteca.TTL_S + 1   # o principal passou o TTL_S, o do arquivo não
        self.idx.get()
        self.idx.aguardar(5)
        self.assertNotIn(DOC, self.idx.get().por_id)
        self.assertEqual(self.item(DOC)["item"]["arquivado"]["motivo"], "pedido no Office")
        self.assertEqual(self.arq.scans, 2)
        self.assertEqual(self.item("relatorios/nunca.md"), 404)   # já refeito depois do REGISTO: não refaz de novo
        self.assertEqual(self.arq.scans, 2)

    def test_inexistente_continua_404(self):
        self.assertEqual(self.item("relatorios/nunca.md"), 404)
        self.assertEqual(self.get_json(self.f("relatorios/nunca.md")), (404, NAO))
        self.assertEqual(self.get_json("/api/biblioteca/zip?id=relatorios/nunca.md"), (404, NAO))


class PostTest(Base):
    def post(self, data=None, raw=None, **headers):
        h = {"Content-Type": "application/json", "Origin": f"http://{self.host}", "User-Agent": "teste/1"}
        h.update({k.replace("_", "-"): v for k, v in headers.items()})
        h = {k: v for k, v in h.items() if v is not None}
        code, _, body = self.req("/api/biblioteca/arquivo", "POST", h, raw if raw is not None else
                                 json.dumps(data).encode())
        return code, json.loads(body or b"null")

    def req(self, path, method="GET", headers=None, body=None):
        import http.client
        c = http.client.HTTPConnection(self.host, timeout=10)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            c.close()

    def bom(self, item=DOC, accao="arquivar", **kw):
        return {"token": server.TOKEN, "accao": accao, "item": item, **kw}

    def pedidos(self):
        return sorted(n for n in os.listdir(self.fila) if arquivo.NOME.fullmatch(n))

    def arvore(self):
        # tudo o que não é a fila: nomes, tamanhos e mtimes (o POST não pode mexer em nada disto)
        out = {}
        for d, _, fs in os.walk(self.root):
            if not d.startswith(str(self.fila)):
                for f in fs:
                    st = os.lstat(os.path.join(d, f))
                    out[os.path.join(d, f)] = (st.st_size, st.st_mtime_ns, st.st_ino)
        return out

    def test_202_so_escreve_na_fila(self):
        antes = self.arvore()
        code, out = self.post(self.bom(), X_Forwarded_For="1.2.3.4, 10.0.0.9")
        self.assertEqual(code, 202)
        self.assertEqual(set(out), {"id", "accao"})
        self.assertEqual(self.pedidos(), [out["id"] + ".json"])
        p = json.loads((self.fila / (out["id"] + ".json")).read_text())
        self.assertEqual(list(p), list(arquivo.CHAVES))
        self.assertEqual((p["v"], p["id"], p["accao"], p["fonte"], p["chave"], p["ip"], p["ua"]),
                         (1, out["id"], "arquivar", "relatorios", "2026-10-01-velho.md", "10.0.0.9", "teste/1"))
        self.assertEqual(self.arvore(), antes)   # nada mexeu fora da fila
        self.assertEqual(os.listdir(self.fila), [out["id"] + ".json"])   # sem temporários
        # o /item mostra o pedido em fila
        self.assertEqual(self.item(DOC)["arquivo"], {"arquivavel": True, "arquivado": None, "pendente": "arquivar",
                                                     "erro": None})
        # o lote também é arquivável
        self.assertEqual(self.post(self.bom(LOTE))[0], 202)

    def test_409_com_pendente_de_qualquer_accao(self):
        self.assertEqual(self.post(self.bom())[0], 202)
        self.assertEqual(self.post(self.bom()), (409, {"error": "já há um pedido em fila para este item"}))
        self.assertEqual(len(self.pedidos()), 1)
        # resposta do runner: já não está pendente
        stem = self.pedidos()[0][:-5]
        (self.fila / f"{stem}.feito.json").write_text(json.dumps({"erro": "protegido: alterado há menos de 48 h"}))
        self.assertEqual(self.item(DOC)["arquivo"], {"arquivavel": True, "arquivado": None, "pendente": None,
                                                     "erro": "protegido: alterado há menos de 48 h"})
        self.assertEqual(self.post(self.bom())[0], 202)
        # um «repor» pendente do mesmo item também bloqueia o «arquivar» (e vice-versa)
        self.w("relatorios/novo.md", "# novo")
        self.refazer()
        code, _ = self.post(self.bom("relatorios/novo.md"))
        self.assertEqual(code, 202)
        arquivo.escrever_pedido(self.fila, "repor", "relatorios", "novo.md", "", "", timezone.utc, NOW)
        self.assertEqual(self.post(self.bom("relatorios/novo.md"))[0], 409)

    def test_ultimo_pedido_ok_e_erro_ilegivel(self):
        _, out = self.post(self.bom())
        (self.fila / f"{out['id']}.feito.json").write_text(json.dumps({"ok": True}))
        self.assertEqual(self.item(DOC)["arquivo"]["pendente"], None)
        self.assertEqual(self.item(DOC)["arquivo"]["erro"], None)
        _, out = self.post(self.bom())
        (self.fila / f"{out['id']}.feito.json").write_text(json.dumps({"erro": "<script>fora da lista</script>"}))
        self.assertEqual(self.item(DOC)["arquivo"], {"arquivavel": True, "arquivado": None, "pendente": None,
                                                     "erro": None})   # erro fora da lista fechada não aparece

    def test_arquivado_pelo_runner_antes_do_varrimento(self):
        # o runner já arquivou (feito ok) mas o índice principal ainda tem o item (dentro do TTL_S)
        _, out = self.post(self.bom())
        self.mover(DOC)
        (self.fila / f"{out['id']}.feito.json").write_text(json.dumps({"ok": True}))
        self.assertIn(DOC, self.idx.get().por_id)
        self.assertEqual(self.item(DOC)["arquivo"], {"arquivavel": False, "arquivado": None, "pendente": None,
                                                     "erro": None})
        self.assertEqual(self.post(self.bom()), (400, {"error": "item inexistente"}))
        _, out = self.post(self.bom(LOTE))
        self.mover(LOTE)
        self.assertEqual(self.post(self.bom(LOTE)), (400, {"error": "item inexistente"}))
        self.assertEqual(len(self.pedidos()), 2)
        # sem resposta ok do runner, «Arquivar» continua oferecido (o POST é que recusa)
        self.assertEqual(self.item(LOTE)["arquivo"]["arquivavel"], True)
        # reposto à mão (fora do Office): volta a oferecer-se e a aceitar
        self.mover(DOC, para_arquivo=False)
        self.assertEqual(self.item(DOC)["arquivo"]["arquivavel"], True)
        self.assertEqual(self.post(self.bom())[0], 202)

    def test_429_fila_cheia(self):
        for i in range(arquivo.PENDENTES_MAX):
            self.assertEqual(arquivo.escrever_pedido(self.fila, "arquivar", "relatorios", f"x{i}.md", "", "",
                                                     timezone.utc, NOW)[0], 202)
        self.assertEqual(self.post(self.bom()), (429, {"error": "fila cheia"}))
        self.assertEqual(len(self.pedidos()), arquivo.PENDENTES_MAX)

    def test_503_sem_fila_ou_sem_escrita(self):
        os.chmod(self.fila, 0o555)
        try:
            if not os.access(self.fila, os.W_OK):   # root escreve sempre: aí só o caso «fila ausente»
                self.assertEqual(self.post(self.bom()), (503, {"error": "fila do arquivo indisponível"}))
        finally:
            os.chmod(self.fila, 0o755)
        os.rmdir(self.fila)
        self.assertEqual(self.post(self.bom()), (503, {"error": "fila do arquivo indisponível"}))
        self.assertEqual(self.item(DOC)["arquivo"]["pendente"], None)   # o /item continua sem a fila

    def test_400(self):
        casos = (({"token": server.TOKEN, "accao": "arquivar"}, "campos inválidos"),
                 (self.bom(extra=1), "campos inválidos"),
                 (self.bom(item=["x"]), "campos inválidos"),
                 (self.bom(accao="apagar"), "acção inválida"),
                 (self.bom(accao=["arquivar"]), "acção inválida"),
                 (self.bom("relatorios/nao-existe.md"), "item inexistente"),
                 (self.bom(DOC, "repor"), "item inexistente"),          # repor: não está no arquivo
                 (self.bom("relatorios/2026-10-01-velho.revisao.md"), "item inexistente"),   # ficheiro, não item
                 (self.bom("tarefas/20261101-1000-a"), "não arquivável"),
                 (self.bom("aprovacoes-marketing/2026-11-01-status.md"), "não arquivável"),
                 (self.bom("rascunhos/fotos"), "não arquivável"),         # pasta de media fora de media/
                 (self.bom("relatorios"), "item inexistente"),
                 (self.bom(""), "não arquivável"),
                 (self.bom("../relatorios/x.md"), "não arquivável"))
        for data, msg in casos:
            with self.subTest(data=data):
                self.assertEqual(self.post(data), (400, {"error": msg}))
        self.assertEqual(self.pedidos(), [])

    def test_repor_de_um_arquivado(self):
        self.mover(DOC)
        self.assertEqual(self.post(self.bom(DOC, "repor"))[0], 202)
        self.assertEqual(self.item(DOC)["arquivo"]["pendente"], "repor")

    def test_verificacoes_dos_post(self):
        self.assertEqual(self.post(self.bom(), Content_Type="text/plain")[0], 403)
        self.assertEqual(self.post(self.bom(), Origin="https://outro.exemplo")[0], 403)
        self.assertEqual(self.post(self.bom(), Origin=None)[0], 403)   # sem Origin nem Sec-Fetch-Site
        self.assertEqual(self.post(self.bom(token="x")), (403, {"error": "token inválido, recarrega"}))
        self.assertEqual(self.post(raw=b"[]")[0], 400)
        self.assertEqual(self.post(raw=b"{" + b" " * arquivo.PEDIDO_MAX + b"}")[0], 413)
        with mock.patch.object(server, "PROXY_SEGREDO", "s3gredo-do-proxy"):
            self.assertEqual(self.post(self.bom())[0], 403)
            self.assertEqual(self.post(self.bom(), X_Office_Segredo="s3gredo-do-proxy")[0], 202)
        self.assertEqual(len(self.pedidos()), 1)

    def test_arquivavel_no_item(self):
        self.assertEqual(self.item(LOTE)["arquivo"]["arquivavel"], True)
        self.assertEqual(self.item(DOC)["arquivo"]["arquivavel"], True)
        self.assertEqual(self.item("tarefas/20261101-1000-a")["arquivo"]["arquivavel"], False)
        self.assertEqual(self.item("rascunhos/fotos")["arquivo"], {"arquivavel": False, "arquivado": None,
                                                                    "pendente": None, "erro": None})
