"""Guardião da Conversa (ADR-023, revisão S2/S5), rotas com o servidor real numa porta local: a UI estática com CSP
estrita e 404 fora do manifesto, CA-4 (401 sem sessão), CA-8 (ritmo, verificados/, 64 KB, ponte), CA-14 («aprovo»
escrito é só um enviar), CA-18 (nome nas prefs do guardião), CA-21 (aviso de tokens), leitura e máscara; e o que é
novo: a assinatura (403), o `pid` (409), os `vistos` que não contam, a limpeza e o publico.json."""
import http.client
import json
import os
import time
from unittest import mock

import chat
from tests.guardiao_base import CHAVE, CID, KID, NONCE, VECTORES, WEB, GuardiaoBase, gm

SK = "sk-ant-" + "q7W3" * 8


class Estaticos(GuardiaoBase):
    def test_raiz_e_manifesto_com_cabecalhos(self):
        for path, nome, tipo in (("/", "conversa.html", "text/html; charset=utf-8"),
                                 ("/conversa.html", "conversa.html", "text/html; charset=utf-8"),
                                 ("/conversa_inicio.js", "conversa_inicio.js", "text/javascript; charset=utf-8"),
                                 ("/conversa.css", "conversa.css", "text/css; charset=utf-8"),
                                 ("/fonts/letra.woff2", "fonts/letra.woff2", "font/woff2")):
            with self.subTest(path=path):
                code, cab, corpo = self.req(path)
                self.assertEqual((code, corpo, cab["content-type"]), (200, WEB[nome], tipo))
                self.assertEqual(cab["content-security-policy"], gm.CSP)
                self.assertIn("script-src 'self';", cab["content-security-policy"])
                self.assertIn("frame-ancestors 'none'", cab["content-security-policy"])
                self.assertEqual((cab["x-frame-options"], cab["referrer-policy"], cab["x-content-type-options"],
                                  cab["permissions-policy"], cab["cache-control"]),
                                 ("DENY", "no-referrer", "nosniff", "microphone=(self)", "no-cache"))

    def test_fora_do_manifesto_404(self):
        for path in ("/index.html", "/SHA256SUMS", "/../SHA256SUMS", "/chat_host/guardiao.py", "/web/conversa.css",
                     "/fonts/", "/conversa.css/", "/%2e%2e/chat.py", "/api/conversa/outra",
                     "/api/state", "/api/config"):
            with self.subTest(path=path):
                code, cab, corpo = self.req(path)
                self.assertEqual((code, json.loads(corpo)), (404, {"error": chat.NAO_ENCONTRADO}))
                self.assertEqual(cab["x-frame-options"], "DENY")   # também nos erros

    def test_api_sem_cache_e_sem_token(self):
        code, cab, _ = self.req("/api/conversa/estado")
        self.assertEqual((code, cab["cache-control"]), (200, "no-store"))
        self.assertEqual(self.post_raw("/api/conversa/outra", {})[0], 404)


class SemSessao(GuardiaoBase):
    def test_ca4_rotas_401_sem_cookie(self):
        self.conversa()
        for p in ("/api/conversa/lista", f"/api/conversa/eventos?conversa={CID}&desde=0"):
            with self.subTest(p=p):
                self.assertEqual(self.get(p), (401, {"error": chat.SESSAO_INVALIDA}))
        for p in ("/api/conversa/pedido", "/api/conversa/prefs"):
            with self.subTest(p=p):
                code, _, d = self.post_raw(p, {"nome": "x"})
                self.assertEqual((code, d), (401, {"error": chat.SESSAO_INVALIDA}))
        self.cookie = "z" * 43   # cookie desconhecido também
        self.assertEqual(self.get("/api/conversa/lista")[0], 401)
        code, d = self.get("/api/conversa/estado")   # o estado responde sem sessão
        self.assertEqual((code, d["sessao"], d["csrf"]), (200, False, None))
        self.assertEqual(self.fila(), [])


class Estado(GuardiaoBase):
    CHAVES = frozenset({"v", "tls", "sessao", "csrf", "bloqueado_ate", "entrada", "sal_office", "desafio",
                        "iteracoes", "nome", "nome_omissao", "permissoes", "permissoes_config", "voz", "voz_ponte",
                        "voz_fornecedor", "voz_idioma", "aviso_tokens", "aviso_voz", "ponte", "versoes_ok", "dia_usd",
                        "modelos"})

    def test_estado_omissoes(self):
        code, d = self.get("/api/conversa/estado")
        self.assertEqual(code, 200)
        self.assertEqual(set(d), self.CHAVES)
        self.assertEqual({k: d[k] for k in ("v", "tls", "entrada", "iteracoes", "nome", "nome_omissao", "permissoes",
                                            "permissoes_config", "voz", "voz_ponte", "voz_idioma", "aviso_tokens",
                                            "aviso_voz", "ponte", "versoes_ok", "dia_usd", "bloqueado_ate")},
                         {"v": 1, "tls": True, "entrada": True, "iteracoes": 600000, "nome": "Assistente",
                          "nome_omissao": "Assistente", "permissoes": "cartao", "permissoes_config": "cartao",
                          "voz": "desligada", "voz_ponte": "desligada", "voz_idioma": "pt", "aviso_tokens": False,
                          "aviso_voz": False, "ponte": True, "versoes_ok": True, "dia_usd": 0.0,
                          "bloqueado_ate": None})
        self.assertEqual(d["modelos"], list(chat.MODELOS))
        texto = json.dumps(d)   # nunca o verificador, o código nem a chave
        for k in ("verificador", "codigo", "chave"):
            self.assertNotIn(VECTORES[k], texto)

    def test_estado_sem_entrada_e_ponte_parada(self):
        (self.estado / "entrada.json").unlink()
        self.pulsar(idade=61, dia_usd=3.5, voz_modo="local")
        d = self.get("/api/conversa/estado")[1]
        self.assertEqual((d["entrada"], d["sal_office"], d["iteracoes"]), (False, None, None))
        self.assertEqual((d["ponte"], d["voz_ponte"], d["dia_usd"], d["versoes_ok"]), (False, None, None, True))

    def test_ponte_do_contrato_1_nao_serve(self):   # a ponte antiga ainda conferia a HMAC e lia fila/
        self.pulsar(versao=1)
        d = self.get("/api/conversa/estado")[1]
        self.assertEqual((d["ponte"], d["versoes_ok"]), (False, False))

    def test_ca16_permissoes_efectivas(self):
        for cfg_p, ponte_p, efectivo in (("cartao", "cartao", "cartao"), ("cartao", "espelho", "cartao"),
                                         ("espelho", "cartao", "cartao"), ("espelho", "espelho", "espelho")):
            with self.subTest(cfg=cfg_p, ponte=ponte_p):
                self.usar(CONVERSA_PERMISSOES=cfg_p)
                self.pulsar(permissoes=ponte_p)
                d = self.get("/api/conversa/estado")[1]
                self.assertEqual((d["permissoes"], d["permissoes_config"]), (efectivo, cfg_p))
        self.pulsar(idade=61, permissoes="espelho")   # ponte parada: na dúvida, cartao
        self.assertEqual(self.get("/api/conversa/estado")[1]["permissoes"], "cartao")

    def test_config_do_ambiente_e_limites(self):
        c = gm.config({})
        self.assertEqual((str(c.pasta), str(c.estado), str(c.opt), c.escuta, c.permissoes, c.voz, c.voz_idioma,
                          c.nome, c.tls_teste),
                         ("/srv/office-conversa", "/var/lib/office-conversa", "/opt/office-conversa",
                          "unix:/srv/office-conversa/sock/guardiao.sock", "cartao", "desligada", "pt", "Assistente",
                          False))
        self.assertEqual(str(gm.config({"STATE_DIRECTORY": "/var/lib/x:/var/lib/y"}).estado), "/var/lib/x")
        c = gm.config({"CONVERSA_PERMISSOES": "tudo", "CONVERSA_VOZ": "nuvem", "CONVERSA_VOZ_IDIOMA": "pt-BR",
                       "CONVERSA_NOME": "a\u202eb", "CONVERSA_TLS_TESTE": "sim", "CONVERSA_FUSO": "Nada/Disto"})
        self.assertEqual((c.permissoes, c.voz, c.voz_idioma, c.nome, c.tls_teste, str(c.tz)),
                         ("cartao", "desligada", "pt", "Assistente", False, "UTC"))
        c = gm.config({"CONVERSA_VOZ": "externa", "CONVERSA_VOZ_IDIOMA": "en", "CONVERSA_NOME": "Sexta"})
        self.assertEqual((c.voz, c.voz_idioma, c.nome), ("externa", "en", "Sexta"))

    def test_ca20_gasto_do_dia(self):
        self.pulsar(dia_usd=1000.25)
        self.assertEqual(self.get("/api/conversa/estado")[1]["dia_usd"], 1000.25)
        for mau in (-1, "1", True, None, 10 ** 400):   # R3: o inteiro enorme não rebenta (OverflowError)
            self.pulsar(dia_usd=mau)
            self.assertIsNone(self.get("/api/conversa/estado")[1]["dia_usd"])


class Leitura(GuardiaoBase):
    def setUp(self):
        super().setUp()
        self.sessao()

    def test_lista(self):
        self.conversa(titulo=f"Plano {SK}",
                      eventos=[{"n": 1, "tipo": "mensagem", "autor": "dono", "texto": "Olá Mundo"}])
        code, d = self.get("/api/conversa/lista")
        self.assertEqual(code, 200)
        self.assertEqual((d["v"], d["ponte"]), (1, True))
        self.assertEqual(d["conversas"][0]["titulo"], "Plano [segredo]")
        self.assertEqual([c["id"] for c in self.get("/api/conversa/lista?q=MUNDO")[1]["conversas"]], [CID])
        for q in ("a", "a" * 101):
            with self.subTest(q=q):
                self.assertEqual(self.get(f"/api/conversa/lista?q={q}"), (400, {"error": chat.Q_INVALIDA}))
        self.assertEqual(self.get(f"/api/conversa/lista?q={SK[:20]}")[1]["conversas"], [])   # segredo não se acha

    def test_ca22_eventos_mascarados(self):
        evs = [{"n": 1, "t": "2026-11-23T10:00:00+00:00", "tipo": "mensagem", "autor": "dono", "texto": f"usa {SK}"},
               {"n": 2, "t": "2026-11-23T10:00:01+00:00", "tipo": "ferramenta", "nome": "Bash", "resumo": "env"},
               {"n": 3, "t": "2026-11-23T10:00:02+00:00", "tipo": "ferramenta", "ref": 2,
                "resultado": "TOKEN ghp_" + "Z9y8" * 9}]
        self.conversa(eventos=evs)
        code, d = self.get(f"/api/conversa/eventos?conversa={CID}&desde=0")
        self.assertEqual(code, 200)
        self.assertEqual((d["seguinte"], d["mais"], d["ponte"]), (3, False, True))
        self.assertEqual(d["eventos"][0]["texto"], "usa [segredo]")
        self.assertEqual(d["eventos"][2]["resultado"], "TOKEN [segredo]")
        self.assertEqual([e["n"] for e in self.get(f"/api/conversa/eventos?conversa={CID}&desde=1")[1]["eventos"]],
                         [2, 3])
        for desde in ("-1", "x", "1.5", "", "1e3"):
            with self.subTest(desde=desde):
                self.assertEqual(self.get(f"/api/conversa/eventos?conversa={CID}&desde={desde}"),
                                 (400, {"error": chat.DESDE_INVALIDO}))

    def test_ids_e_symlinks(self):
        self.conversa()
        os.symlink(self.root / "conversas" / CID, self.root / "conversas" / "20261123-101500-ffffff")
        for cid in ("20261123-101500-abc999", "..", "%2e%2e", f"{CID}%2f..", "20261123-101500-ffffff", "",
                    "estado.json", "entrada.json"):
            with self.subTest(cid=cid):
                self.assertEqual(self.get(f"/api/conversa/eventos?conversa={cid}"), (404, {"error": "não encontrado"}))

    def test_ponte_parada_na_leitura(self):
        self.pulsar(idade=61)
        self.assertFalse(self.get("/api/conversa/lista")[1]["ponte"])


class Pedido(GuardiaoBase):
    def setUp(self):
        super().setUp()
        self.sessao()
        self.aceitar_tokens()

    def test_enviar_vai_para_verificados_tal_como_assinado(self):
        self.conversa()
        corpo = self.enviar("Onde estamos?")
        code, d = self.post(corpo, headers={"X-Forwarded-For": "6.6.6.6, 10.0.0.9", "User-Agent": "U" * 300})
        self.assertEqual((code, d), (202, {"ok": True, "conversa": CID}))
        nomes = self.fila()
        self.assertEqual(len(nomes), 1)
        self.assertRegex(nomes[0], r"^\d{8}T\d{6}-[0-9a-f]{8}\.json$")
        self.assertEqual(os.stat(self.ver / nomes[0]).st_mode & 0o777, 0o640)
        doc = self.ler_fila()[0]
        self.assertEqual({k: v for k, v in doc.items() if k not in chat.EXTRA_FILA}, corpo)
        self.assertEqual((doc["v"], doc["ip"], len(doc["ua"])), (1, "10.0.0.9", 120))
        self.assertIsNone(chat.validar_fila(doc))
        self.assertTrue(chat.assinatura_ok(CHAVE, doc))
        self.assertEqual(self.post(self.enviar("e agora?")), (409, {"error": chat.A_TRABALHAR}))
        # a ponte viu-o (vistos): já não conta, mesmo antes de o guardião o apagar
        self.pulsar(vistos=nomes)
        self.assertEqual(self.post(self.enviar("e agora?"))[0], 202)

    def test_assinatura_errada_ausente_ou_mal_formada_403(self):   # S2: sem a chave não se escreve nada
        self.conversa(estado="a_espera", pendentes=[self.pend()])
        certo = self.assinado("responder_permissao", conversa=CID, nonce=NONCE, escolha="uma_vez")
        sem = {k: v for k, v in certo.items() if k != "assinatura"}
        casos = {"chave errada": self.assinado("responder_permissao", chave=bytes(32), conversa=CID, nonce=NONCE,
                                               escolha="uma_vez"),
                 "sem assinatura": sem, "maiúsculas": {**certo, "assinatura": certo["assinatura"].upper()},
                 "outra escolha": {**certo, "escolha": "conversa"}, "não é hex": {**certo, "assinatura": "zz" * 32},
                 "campo a mais": {**certo, "extra": 1}, "só a assinatura": {"assinatura": certo["assinatura"]}}
        for nome, corpo in casos.items():
            with self.subTest(nome=nome):
                self.assertEqual(self.post(corpo), (403, {"error": gm.ASSINATURA}))
        self.assertEqual(self.fila(), [])
        self.assertEqual(self.post(certo)[0], 202)

    def test_revogar_e_chave_de_outro_browser(self):
        self.conversa()
        outra = bytes(range(32))
        self.gravar_chaves([(KID, CHAVE), ("0" * 16, outra)])
        # a chave de outro browser registado não assina pela sessão deste (escolhe-se pelo kid da sessão)
        self.assertEqual(self.post(self.assinado("parar", chave=outra, conversa=CID)), (403, {"error": gm.ASSINATURA}))
        for mau in ("nao-e-json", json.dumps({"v": 1, "chaves": [{"kid": KID, "chave": "x", "criada": "y"}]})):
            with self.subTest(mau=mau):
                (self.estado / "chaves.json").write_text(mau)
                self.assertEqual(self.post(self.assinado("parar", conversa=CID)),
                                 (401, {"error": chat.SESSAO_INVALIDA}))
        (self.estado / "chaves.json").unlink()   # --revogar: as sessões caem logo
        self.assertEqual(self.post(self.assinado("parar", conversa=CID)), (401, {"error": chat.SESSAO_INVALIDA}))
        self.gravar_chaves([(KID, CHAVE)])
        self.assertEqual(self.post(self.assinado("parar", conversa=CID))[0], 202)
        self.assertEqual(len(self.fila()), 1)

    def test_pid_repetido_409_e_lembrado(self):
        self.conversa()
        d = self.assinado("parar", conversa=CID)
        self.assertEqual(self.post(d)[0], 202)
        self.assertEqual(self.post(d), (409, {"error": gm.REPETIDO}))
        outro = self.assinado("arquivar", conversa=CID, pid=d["pid"])   # outro pedido assinado com o mesmo pid
        self.assertEqual(self.post(outro), (409, {"error": gm.REPETIDO}))
        self.assertEqual(len(self.fila()), 1)
        pids = json.loads((self.estado / "pids.json").read_text())
        self.assertEqual(pids, {d["pid"]: d["t"]})
        self.assertEqual(os.stat(self.estado / "pids.json").st_mode & 0o777, 0o600)
        g2 = gm.Guardiao(self.config(), {})   # um guardião reiniciado lembra-se
        self.assertTrue(g2.pid_usado(d, time.time()))
        self.assertFalse(g2.pid_usado(d, time.time() + 700))   # e esquece-o fora da janela
        self.assertEqual(g2.pids, {})

    def test_pid_so_se_gasta_quando_se_escreve(self):
        self.conversa(arquivada=True)
        d = self.assinado("parar", conversa=CID)
        self.assertEqual(self.post(d), (409, {"error": chat.ARQUIVADA}))
        self.conversa()
        self.assertEqual(self.post(d)[0], 202)

    def test_nova_com_o_id_do_browser(self):
        code, d = self.post(self.assinado("nova", conversa=CID))
        self.assertEqual((code, d), (202, {"ok": True, "conversa": CID}))
        self.assertEqual(self.post(self.assinado("nova", conversa=CID)), (409, {"error": chat.JA_EXISTE}))
        outro = "20261123-101500-abc124"
        self.conversa(cid=outro)
        self.assertEqual(self.post(self.assinado("nova", conversa=outro)), (409, {"error": chat.JA_EXISTE}))
        self.assertEqual(self.post(self.assinado("nova", conversa="../x"))[0], 400)
        self.assertEqual(len(self.fila()), 1)

    def test_ca21_sem_aviso_de_tokens_409(self):
        (self.estado / "prefs.json").unlink()
        self.conversa()
        self.assertEqual(self.post(self.enviar()), (409, {"error": chat.AVISO_TOKENS}))
        self.assertEqual(self.post(self.assinado("parar", conversa=CID))[0], 202)   # só o enviar espera pelo aviso
        code, _, d = self.post_raw("/api/conversa/prefs", {"aviso_tokens": True})
        self.assertEqual((code, d["aviso_tokens"]), (200, True))
        self.assertTrue(self.get("/api/conversa/estado")[1]["aviso_tokens"])
        self.assertEqual(self.post(self.enviar())[0], 202)

    def test_permissoes_tem_de_bater_com_a_unit(self):
        self.conversa()
        espelho = self.assinado("enviar", conversa=CID, texto="x", modo="normal", modelo="omissao",
                                permissoes="espelho")
        self.assertEqual(self.post(espelho), (400, {"error": chat.PERMISSOES_DIFEREM}))
        self.usar(CONVERSA_PERMISSOES="espelho")
        self.assertEqual(self.post(self.enviar()), (400, {"error": chat.PERMISSOES_DIFEREM}))
        self.assertEqual(self.fila(), [])

    def test_ca14_aprovo_escrito_e_so_um_enviar(self):
        self.conversa(estado="a_espera", pendentes=[self.pend()])
        for texto in ("aprovo", "aprova o pedido"):
            with self.subTest(texto=texto):
                self.assertEqual(self.post(self.enviar(texto)), (409, {"error": chat.A_TRABALHAR}))
        self.conversa(estado="pronto", pendentes=[self.pend()])
        self.assertEqual(self.post(self.enviar("aprovo"))[0], 202)
        docs = self.ler_fila()
        self.assertEqual([d["acao"] for d in docs], ["enviar"])
        self.assertNotIn("nonce", docs[0])

    def test_ca8_403_cabecalhos(self):
        self.conversa()
        corpo = self.assinado("parar", conversa=CID)
        casos = {"sem Origin": ({"origem": False}, chat.ORIGEM),
                 "Origin errado": ({"headers": {"Origin": "https://mau.example"}}, chat.ORIGEM),
                 "Origin null": ({"headers": {"Origin": "null"}}, chat.ORIGEM),
                 "sem csrf": ({"csrf": False}, chat.CSRF_INVALIDO),
                 "csrf errado": ({"headers": {"X-Csrf": "y" * 43}}, chat.CSRF_INVALIDO)}
        for nome, (k, msg) in casos.items():
            with self.subTest(nome=nome):
                code, _, d = self.post_raw("/api/conversa/pedido", corpo, **k)
                self.assertEqual((code, d), (403, {"error": msg}))
        code, _, _ = self.post_raw("/api/conversa/pedido", corpo, origem=False,
                                   headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(code, 202)

    def test_ca8_ritmo(self):
        for i in range(chat.RITMO_N):
            self.assertEqual(self.post(self.assinado("parar", conversa="20261123-101500-000000"))[0], 404, i)
        self.assertEqual(self.post(self.assinado("nova", conversa=CID)), (429, {"error": chat.RITMO}))
        s = next(iter(self.g.sessoes._s.values()))
        s["posts"][0] -= chat.RITMO_S   # janela deslizante: o mais antigo saiu
        self.assertEqual(self.post(self.assinado("nova", conversa=CID))[0], 202)

    def test_ca8_corpo(self):
        c = http.client.HTTPConnection(self.host, timeout=10)
        try:   # 413 antes de ler o corpo
            c.putrequest("POST", "/api/conversa/pedido")
            for k, v in {**self.cab(), "Content-Type": "application/json",
                         "Content-Length": str(chat.FILA_MAX + 1)}.items():
                c.putheader(k, v)
            c.endheaders()
            self.assertEqual(c.getresponse().status, 413)
        finally:
            c.close()
        code, _, _ = self.post_raw("/api/conversa/pedido", b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(code, 415)
        self.assertEqual(self.post_raw("/api/conversa/pedido", b"[1]")[0], 400)
        self.assertEqual(self.post_raw("/api/conversa/pedido", b"{nao json")[0], 400)
        self.conversa()
        for corpo in (self.assinado("transcrever", conversa=CID), self.assinado("parar", conversa=CID, pid="x"),
                      self.assinado("parar", conversa=CID, extra=1)):   # assinados, mas a forma não serve
            with self.subTest(corpo=corpo):
                self.assertEqual(self.post(corpo)[0], 400)
        self.assertEqual(self.fila(), [])

    def test_ca8_verificados_cheia_e_indisponivel(self):
        self.conversa()
        for i in range(chat.PENDENTES_MAX):
            (self.ver / f"20261123T100000-{i:08x}.json").write_text("{}")
        self.assertEqual(self.post(self.assinado("parar", conversa=CID)), (503, {"error": chat.FILA_CHEIA}))
        with mock.patch.object(self.g, "verificados", self.root / "nada"):
            self.assertEqual(self.post(self.assinado("nova", conversa="20261123-101500-000001")),
                             (503, {"error": chat.FILA_INDISPONIVEL}))

    def test_ca8_ponte_503(self):
        self.conversa()
        self.pulsar(idade=61)
        self.assertEqual(self.post(self.assinado("nova", conversa=CID)), (503, {"error": chat.PONTE_PARADA}))
        self.pulsar(versao=1)
        self.assertEqual(self.post(self.assinado("nova", conversa=CID)), (503, {"error": chat.VERSOES}))
        (self.root / "conversas" / "estado.json").unlink()
        self.assertEqual(self.post(self.assinado("parar", conversa=CID)), (503, {"error": chat.PONTE_PARADA}))
        self.assertEqual(self.fila(), [])

    def test_relogio_desacertado_400(self):   # C1
        self.conversa()
        agora = int(time.time() * 1000)
        for t in (agora - 301_000, agora + 301_000):
            self.assertEqual(self.post(self.assinado("parar", conversa=CID, t=t)), (400, {"error": chat.RELOGIO}))
        self.assertEqual(self.fila(), [])
        self.assertEqual(self.post(self.assinado("parar", conversa=CID, t=agora - 280_000))[0], 202)

    def test_nonce_e_delegacao(self):
        self.conversa(estado="a_espera", pendentes=[self.pend(), self.pend("e" * 32, expira_s=-1, n=2),
                                                    self.pend("d" * 32, delegacao=True, n=3)])
        r = {"conversa": CID, "escolha": "uma_vez"}
        for nonce in ("f" * 32, "e" * 32):   # errado, expirado
            with self.subTest(nonce=nonce):
                self.assertEqual(self.post(self.assinado("responder_permissao", nonce=nonce, **r)),
                                 (409, {"error": chat.APROVACAO_INVALIDA}))
        self.assertEqual(self.post(self.assinado("responder_permissao", nonce=NONCE, **r))[0], 202)
        self.assertEqual(self.post(self.assinado("responder_permissao", nonce=NONCE, conversa=CID,
                                                 escolha="recusar")), (409, {"error": chat.APROVACAO_INVALIDA}))
        self.assertEqual(self.post(self.assinado("responder_permissao", nonce="d" * 32, conversa=CID,
                                                 escolha="conversa")), (400, {"error": chat.DELEGACAO_CONVERSA}))

    def test_arquivada(self):
        self.conversa(arquivada=True)
        self.assertEqual(self.post(self.assinado("parar", conversa=CID)), (409, {"error": chat.ARQUIVADA}))


class Prefs(GuardiaoBase):
    def setUp(self):
        super().setUp()
        self.sessao()

    def prefs(self, corpo):
        code, _, d = self.post_raw("/api/conversa/prefs", corpo)
        return code, d

    def test_ca18_nome_e_repor(self):
        self.assertEqual(self.prefs({"nome": "Ana Lúcia"}),
                         (200, {"ok": True, "nome": "Ana Lúcia", "aviso_tokens": False, "aviso_voz": False}))
        self.assertEqual(self.get("/api/conversa/estado")[1]["nome"], "Ana Lúcia")
        p = self.estado / "prefs.json"
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["nome"], "Ana Lúcia")
        self.assertEqual(self.prefs({"nome": None})[1]["nome"], "Assistente")
        d = self.get("/api/conversa/estado")[1]
        self.assertEqual((d["nome"], d["nome_omissao"]), ("Assistente", "Assistente"))
        self.assertEqual(self.fila(), [])   # não vai para verificados/

    def test_nome_omissao_da_unit(self):
        self.usar(CONVERSA_NOME="Sexta")
        self.assertEqual(self.get("/api/conversa/estado")[1]["nome"], "Sexta")
        self.assertEqual(self.prefs({"nome": None})[1]["nome"], "Sexta")

    def test_prefs_recusadas(self):
        for corpo in ({}, {"nome": "a" * 41}, {"nome": ""}, {"nome": "a\u202eb"}, {"nome": 5},
                      {"aviso_tokens": False}, {"aviso_voz": "sim"}, {"nome": "a", "aviso_tokens": True},
                      {"outra": True}):
            with self.subTest(corpo=corpo):
                self.assertEqual(self.prefs(corpo)[0], 400)
        self.assertFalse((self.estado / "prefs.json").exists())

    def test_prefs_sem_pasta_503(self):
        self.usar(CONVERSA_GUARDIAO_ESTADO=str(self.root / "nada"))
        self.assertEqual(self.prefs({"nome": "Ana"})[0], 401)   # sem chaves.json a sessão também cai
        self.usar()
        (self.estado / "prefs.json").mkdir()   # prefs.json que não se consegue substituir
        self.assertEqual(self.prefs({"nome": "Ana"}), (503, {"error": chat.PREFS_INDISPONIVEIS}))

    def test_prefs_estragadas_voltam_a_omissao(self):
        (self.estado / "prefs.json").write_text('{"nome": "\\u202e", "aviso_tokens": "ontem"}')
        d = self.get("/api/conversa/estado")[1]
        self.assertEqual((d["nome"], d["aviso_tokens"]), ("Assistente", False))


class Limpeza(GuardiaoBase):
    def escrever(self, d: dict, idade: float = 0.0) -> str:
        nome = chat.nome_fila(time.time(), gm.timezone.utc)
        f = self.ver / nome
        f.write_text(json.dumps(chat.doc_fila(d, "1.2.3.4", "x", self.iso())))
        if idade:
            t = time.time() - idade
            os.utime(f, (t, t))
        return nome

    def test_apaga_os_vistos_e_o_audio_do_transcrever(self):
        (self.ver / "audio").mkdir()
        audio = "a" * 32
        (self.ver / "audio" / f"{audio}.webm").write_bytes(b"x" * 2048)
        t = self.escrever(self.assinado("transcrever", conversa=CID, audio=audio, formato="webm", bytes=2048,
                                        sha256="0" * 64, idioma="pt"))
        a = self.escrever(self.assinado("parar", conversa=CID))
        b = self.escrever(self.assinado("parar", conversa=CID))
        self.pulsar(vistos=[t, a, "20990101T000000-deadbeef.json"])
        self.g.limpar(time.time())
        self.assertEqual(self.fila(), [b])
        self.assertEqual(os.listdir(self.ver / "audio"), [])

    def test_com_a_ponte_morta_apaga_so_o_que_tem_mais_de_1_h(self):
        (self.ver / "audio").mkdir()
        velho = self.escrever(self.assinado("parar", conversa=CID), idade=3700)
        novo = self.escrever(self.assinado("parar", conversa=CID), idade=60)
        av, an = self.ver / "audio" / f"{'b' * 32}.webm", self.ver / "audio" / f"{'c' * 32}.webm"
        for f, idade in ((av, 3700), (an, 60)):
            f.write_bytes(b"x")
            os.utime(f, (time.time() - idade,) * 2)
        self.g.limpar(time.time())   # ponte viva: nada
        self.assertEqual(self.fila(), sorted([velho, novo]))
        self.pulsar(idade=61)
        self.g.limpar(time.time())
        self.assertEqual(self.fila(), [novo])
        self.assertEqual(os.listdir(self.ver / "audio"), [an.name])

    def test_symlink_em_verificados_nunca_segue(self):
        fora = self.root / "fora.json"
        fora.write_text("{}")
        nome = "20261123T100000-deadbeef.json"
        os.symlink(fora, self.ver / nome)
        self.pulsar(vistos=[nome])
        self.g.limpar(time.time())
        self.assertEqual((self.fila(), fora.exists()), ([], True))

    def test_ciclo_nao_rebenta(self):
        with mock.patch.object(self.g, "limpar", side_effect=RuntimeError("x")):
            self.g.passo_limpeza()   # só o stderr


class Publico(GuardiaoBase):
    def ler(self):
        return json.loads((self.root / "publico" / "publico.json").read_text())

    def test_publico_json(self):
        self.conversa(estado="a_espera", pendentes=[self.pend(), self.pend("e" * 32, n=2),
                                                    self.pend("d" * 32, expira_s=-1, n=3)])
        self.conversa(cid="20261123-101500-abc999", arquivada=True, pendentes=[self.pend("f" * 32)])
        self.pulsar(dia_usd=1.25)
        agora = time.time()
        self.g.escrever_publico(agora)
        d = self.ler()
        self.assertEqual(set(d), {"v", "em", "nome", "ativa", "pendentes", "dia_usd"})
        self.assertEqual((d["v"], d["nome"], d["ativa"], d["pendentes"], d["dia_usd"]),
                         (1, "Assistente", True, 2, 1.25))
        self.assertEqual(os.stat(self.root / "publico" / "publico.json").st_mode & 0o777, 0o644)
        self.assertLess(len(json.dumps(d)), 4096)
        texto = (self.root / "publico" / "publico.json").read_text()
        for k in ("verificador", "codigo", "chave"):
            self.assertNotIn(VECTORES[k], texto)
        # igual e com < 30 s: não se reescreve; com 30 s ou uma mudança, sim
        em = d["em"]
        self.g.escrever_publico(agora + 5)
        self.assertEqual(self.ler()["em"], em)
        self.g.escrever_publico(agora + 31)
        self.assertNotEqual(self.ler()["em"], em)
        (self.estado / "prefs.json").write_text(json.dumps({**chat.prefs_vazias(), "nome": "Sexta"}))
        self.pulsar(idade=61)
        self.g.escrever_publico(agora + 32)
        d = self.ler()
        self.assertEqual((d["nome"], d["ativa"], d["dia_usd"]), ("Sexta", False, None))

    def test_pendentes_limitados_e_sem_pasta_nao_rebenta(self):
        with mock.patch.object(gm.chat, "_vivos", return_value=50000):
            self.conversa()
            self.assertEqual(self.g.dados_publicos(time.time())["pendentes"], gm.PENDENTES_PUBLICO_MAX)
        (self.root / "publico").rmdir()
        self.g.escrever_publico(time.time())   # só o stderr
