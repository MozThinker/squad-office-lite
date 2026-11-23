"""Guardião da Conversa (ADR-023, revisão S2/S5 e G2), entrada: TLS = X-Forwarded-Proto: https vindo do socket do
proxy (CA-2), cookie `__Host-` HttpOnly/Secure/SameSite=Strict (CA-5), CSRF e Origin `https://<Host>` (CA-6), bloqueio
global com `agora` injectado (CA-7). G2: o código de uso único só regista um browser (a chave K é dele, aleatória);
as entradas seguintes são por desafio–resposta; saber o código depois do 1.º uso não dá nada. Sem `X-Office-Token`."""
import hashlib
import hmac
import json
import os
import secrets
import unittest

import chat
from tests.guardiao_base import CHAVE, CID, KID, VECTORES, VERIFICADOR, GuardiaoBase, gm

NOW = 1795435200.0


class SessaoUnidade(unittest.TestCase):
    def test_validade_12h_e_vsha(self):
        ss = chat.Sessoes()
        k, s = ss.abrir("a" * 64, NOW)
        self.assertIs(ss.obter(k, "a" * 64, NOW + chat.SESSAO_S - 1), s)
        self.assertIsNone(ss.obter(k, "a" * 64, NOW + chat.SESSAO_S))
        self.assertIsNone(ss.obter(k, "b" * 64, NOW))       # código rodado
        self.assertIsNone(ss.obter(k, None, NOW))           # sem entrada.json
        self.assertIsNone(ss.obter(None, "a" * 64, NOW))
        self.assertIsNone(ss.obter("x", "a" * 64, NOW))
        self.assertIsNone(ss.obter(k, "a" * 64, NOW - 1))   # relógio para trás: não vale

    def test_maximo_de_sessoes(self):
        ss = chat.Sessoes()
        primeira, _ = ss.abrir("a" * 64, NOW)
        for i in range(chat.SESSOES_MAX):
            ss.abrir("a" * 64, NOW + i)
        self.assertIsNone(ss.obter(primeira, "a" * 64, NOW + 30))
        self.assertEqual(len(ss._s), chat.SESSOES_MAX)

    def test_ca7_bloqueio_com_agora_injectado(self):
        b = chat.Bloqueio()
        for i in range(chat.FALHAS_MAX - 1):
            b.falhou(NOW + i)
            self.assertIsNone(b.bloqueado_ate(NOW + i))
        b.falhou(NOW + 10)
        self.assertEqual(b.bloqueado_ate(NOW + 10), NOW + 10 + 15 * 60)
        self.assertIsNotNone(b.bloqueado_ate(NOW + 10 + 15 * 60 - 1))
        self.assertIsNone(b.bloqueado_ate(NOW + 10 + 15 * 60))   # NOW + 15 min: volta a aceitar
        b.falhou(NOW + 2000)                                      # e a contagem recomeça
        self.assertIsNone(b.bloqueado_ate(NOW + 2000))

    def test_sucesso_zera(self):
        b = chat.Bloqueio()
        for _ in range(chat.FALHAS_MAX - 1):
            b.falhou(NOW)
        b.sucesso()
        b.falhou(NOW)
        self.assertIsNone(b.bloqueado_ate(NOW))

    def test_verificador_ok(self):
        e = {"verificador_sha256": hashlib.sha256(bytes.fromhex(VERIFICADOR)).hexdigest()}
        self.assertTrue(chat.verificador_ok(e, VERIFICADOR))
        for mau in (VERIFICADOR.upper(), VERIFICADOR[:-1], "0" * 64, None, 5):
            self.assertFalse(chat.verificador_ok(e, mau))


class Sessao(GuardiaoBase):
    def test_ca5_cookie_e_csrf(self):
        code, cab, d = self.sessao()
        self.assertEqual(code, 200)
        self.assertEqual(set(d), {"ok", "csrf", "expira"})
        self.assertRegex(cab["set-cookie"], r"^__Host-conversa_sessao=[A-Za-z0-9_-]{43}; HttpOnly; Secure; "
                                            r"SameSite=Strict; Path=/; Max-Age=43200$")
        self.assertNotIn("Domain", cab["set-cookie"])
        self.cookie, self.csrf = None, d["csrf"]   # o cookie antigo do Office não serve
        h = {"Cookie": f"conversa_sessao={cab['set-cookie'].split(';')[0].split('=', 1)[1]}"}
        self.assertEqual(self.get("/api/conversa/lista", headers=h)[0], 401)
        self.cookie = cab["set-cookie"].split(";")[0].split("=", 1)[1]
        self.assertNotIn(VECTORES["chave"], cab["set-cookie"] + str(d))
        e = self.get("/api/conversa/estado")[1]
        self.assertEqual((e["sessao"], e["csrf"], e["desafio"]), (True, d["csrf"], None))   # com sessão, sem desafio
        self.assertEqual(self.get("/api/conversa/lista")[0], 200)

    def test_ca6_post_sem_csrf_ou_origem_nao_escreve(self):
        self.sessao()
        self.aceitar_tokens()
        self.conversa()
        corpo = self.enviar()
        for k in ({"csrf": False}, {"headers": {"X-Csrf": "x"}}, {"headers": {"Origin": "https://outro.example"}},
                  {"origem": False, "headers": {"Sec-Fetch-Site": "cross-site"}}):
            with self.subTest(k=k):
                self.assertEqual(self.post_raw("/api/conversa/pedido", corpo, **k)[0], 403)
        self.assertEqual(self.fila(), [])

    def test_entrada_tambem_exige_origem(self):
        for rota in ("registar", "entrar"):
            for h in ({"Origin": "https://office.exemplo.pt"}, {"Origin": "null"}):   # a origem do Office não abre
                with self.subTest(rota=rota, h=h):
                    code, _, d = self.post_raw(f"/api/conversa/{rota}", {}, sessao=False, csrf=False, headers=h)
                    self.assertEqual((code, d), (403, {"error": chat.ORIGEM}))
        self.assertEqual(self.sessao(origem=False)[0:3:2], (403, {"error": chat.ORIGEM}))
        self.assertEqual(self.g.bloqueio.falhas, 0)
        self.assertEqual(self.post_raw("/api/conversa/sessao", {"verificador": VERIFICADOR}, sessao=False,
                                       csrf=False)[0], 404)   # a rota do código-chave já não existe


class Registo(GuardiaoBase):
    """G2: o código regista um browser uma vez; a chave K vem do browser e fica em chaves.json (0600)."""

    def setUp(self):
        super().setUp()
        (self.estado / "chaves.json").unlink()   # instalação nova: código por usar e nenhum browser registado

    def chaves(self) -> list:
        return json.loads((self.estado / "chaves.json").read_text())["chaves"]

    def test_o_codigo_so_serve_uma_vez(self):
        k = secrets.token_bytes(32)
        code, cab, d = self.registar(kid="1" * 16, chave=k)
        self.assertEqual((code, set(d)), (200, {"ok", "csrf", "expira"}))
        self.assertIn("HttpOnly; Secure; SameSite=Strict", cab["set-cookie"])
        self.assertEqual(self.get("/api/conversa/lista")[0], 200)
        [c] = self.chaves()
        self.assertEqual((c["kid"], c["chave"]), ("1" * 16, k.hex()))
        self.assertEqual(os.stat(self.estado / "chaves.json").st_mode & 0o777, 0o600)
        self.assertFalse((self.estado / "entrada.json").exists())   # o código morreu
        e = self.get("/api/conversa/estado", sessao=False, csrf=False)[1]
        self.assertEqual((e["entrada"], e["sal_office"]), (False, None))
        # os pedidos assinam-se com K (a do browser), não com nada que venha do código
        self.aceitar_tokens()
        self.conversa()
        self.assertEqual(self.post(self.assinado("parar", chave=k, conversa=CID))[0], 202)

    def test_o_registo_seguinte_com_o_mesmo_codigo_e_recusado(self):
        self.assertEqual(self.registar()[0], 200)
        self.cookie = self.csrf = None
        code, cab, d = self.registar()
        self.assertEqual((code, d), (403, {"error": gm.CODIGO_USADO}))
        self.assertEqual(d["error"], gm.CODIGO_USADO)
        self.assertNotIn("set-cookie", cab)
        self.assertEqual(len(self.chaves()), 1)
        self.assertEqual(self.g.bloqueio.falhas, 1)   # conta para o bloqueio
        # --novo-codigo: entrada.json novo; o browser antigo continua e regista-se mais um
        self.escrever_entrada()
        self.assertEqual(self.registar()[0], 200)
        self.assertEqual(len(self.chaves()), 2)

    def test_saber_o_codigo_depois_do_1o_uso_nao_assina_nem_entra(self):
        self.assertEqual(self.registar(kid="2" * 16)[0], 200)
        self.cookie = self.csrf = None
        # quem sabe o código só consegue o verificador e as chaves derivadas dele (PBKDF2 com os sais do entrada.json)
        derivadas = [hashlib.pbkdf2_hmac("sha256", VECTORES["codigo"].encode(), bytes.fromhex(VECTORES[s]), 600000, 32)
                     for s in ("sal_ponte", "sal_office")]
        self.assertEqual(self.registar()[0:3:2], (403, {"error": gm.CODIGO_USADO}))
        for chave in derivadas:
            with self.subTest(chave=chave.hex()[:8]):
                self.assertEqual(self.sessao(kid="2" * 16, chave=chave)[0:3:2], (403, {"error": gm.ASSINATURA}))
        self.assertIsNone(self.cookie)
        self.assertEqual(self.get("/api/conversa/lista")[0], 401)
        # e com uma sessão (a de outro), o pedido assinado com a chave derivada do código também cai
        self.assertEqual(self.registar(kid="3" * 16)[0], 403)
        self.escrever_entrada()
        self.assertEqual(self.registar(kid="3" * 16)[0], 200)
        self.aceitar_tokens()
        self.conversa()
        for chave in derivadas:
            self.assertEqual(self.post(self.assinado("parar", chave=chave, conversa=CID)),
                             (403, {"error": gm.ASSINATURA}))
        self.assertEqual(self.fila(), [])

    def test_codigo_errado_registo_mal_formado_e_sem_entrada(self):
        code, cab, d = self.registar("0" * 64)
        self.assertEqual((code, d), (403, {"error": chat.CODIGO_ERRADO}))
        self.assertNotIn("set-cookie", cab)
        self.assertTrue((self.estado / "entrada.json").exists())   # o errado não gasta o código
        bom = {"verificador": VERIFICADOR, "kid": "4" * 16, "chave": "5" * 64}
        for corpo in ({**bom, "verificador": VERIFICADOR.upper()}, {**bom, "kid": "4" * 15}, {**bom, "kid": "G" * 16},
                      {**bom, "chave": "5" * 63}, {**bom, "chave": 5}, {"verificador": VERIFICADOR}, {},
                      {**bom, "x": 1}):
            with self.subTest(corpo=corpo):
                code, _, d = self.post_raw("/api/conversa/registar", corpo, sessao=False, csrf=False)
                self.assertEqual((code, d), (400, {"error": "registo inválido"}))
        self.assertEqual(self.g.bloqueio.falhas, 1)   # só o código errado conta
        (self.estado / "entrada.json").unlink()   # sem código nem chaves: falta o instalador (não conta)
        self.assertEqual(self.registar()[0:3:2], (503, {"error": chat.SEM_ENTRADA}))
        self.assertEqual(self.g.bloqueio.falhas, 1)

    def test_no_maximo_5_chaves_sai_a_mais_antiga(self):
        for i in range(gm.CHAVES_MAX + 1):
            self.escrever_entrada()
            self.assertEqual(self.registar(kid=f"{i:016x}")[0], 200)
        self.assertEqual([c["kid"] for c in self.chaves()], [f"{i:016x}" for i in range(1, gm.CHAVES_MAX + 1)])
        self.cookie = None
        self.assertEqual(self.sessao(kid=f"{0:016x}")[0], 403)   # o mais antigo já não entra

    def test_estado_sem_permissao_503_e_o_codigo_nao_fica_vivo(self):
        os.mkdir(self.estado / "chaves.json")   # não se consegue substituir
        self.assertEqual(self.registar()[0:3:2], (503, {"error": gm.ESTADO_INDISPONIVEL}))
        self.assertFalse((self.estado / "entrada.json").exists())
        self.assertIsNone(self.cookie)


class DesafioResposta(GuardiaoBase):
    def test_entrar_com_a_chave_registada(self):
        d1 = self.desafio()
        self.assertRegex(d1, r"^[0-9a-f]{64}$")
        self.assertNotEqual(d1, self.desafio())
        self.assertEqual(self.sessao(desafio=d1)[0], 200)
        self.assertEqual(self.get("/api/conversa/lista")[0], 200)
        self.assertTrue((self.estado / "entrada.json").exists())   # entrar não gasta o código

    def test_vectores(self):
        a = hmac.new(CHAVE, VECTORES["entrar"].encode(), "sha256").hexdigest()
        self.assertEqual(a, VECTORES["assinatura_entrar"])
        self.assertEqual(VECTORES["entrar"], "entrar\n" + VECTORES["desafio"])
        self.assertEqual(VECTORES["kid"], KID)

    def test_o_desafio_e_de_uso_unico(self):
        d = self.desafio()
        self.assertEqual(self.sessao(desafio=d)[0], 200)
        self.cookie = self.csrf = None
        self.assertEqual(self.sessao(desafio=d)[0:3:2], (403, {"error": gm.DESAFIO_INVALIDO}))
        # um desafio que falhou (assinatura errada) também se gasta
        d = self.desafio()
        self.assertEqual(self.sessao(desafio=d, chave=bytes(32))[0:3:2], (403, {"error": gm.ASSINATURA}))
        self.assertEqual(self.sessao(desafio=d)[0:3:2], (403, {"error": gm.DESAFIO_INVALIDO}))
        self.assertEqual(self.sessao(desafio=secrets.token_hex(32))[0:3:2], (403, {"error": gm.DESAFIO_INVALIDO}))

    def test_o_desafio_expira_aos_2_min(self):
        agora = [1795435200.0]
        self.g.relogio = lambda: agora[0]
        d = self.desafio()
        agora[0] += gm.DESAFIO_S
        self.assertEqual(self.sessao(desafio=d)[0:3:2], (403, {"error": gm.DESAFIO_INVALIDO}))
        d = self.desafio()
        agora[0] += gm.DESAFIO_S - 1
        self.assertEqual(self.sessao(desafio=d)[0], 200)

    def test_desafios_limitados_em_memoria(self):
        primeiro = self.desafio()
        for _ in range(gm.DESAFIOS_MAX):
            self.desafio()
        self.assertEqual(len(self.g.desafios), gm.DESAFIOS_MAX)
        self.assertEqual(self.sessao(desafio=primeiro)[0], 403)   # saiu o mais antigo

    def test_kid_desconhecido_ou_entrada_mal_formada(self):
        self.assertEqual(self.sessao(kid="f" * 16)[0:3:2], (403, {"error": gm.ASSINATURA}))
        bom = {"kid": KID, "desafio": "a" * 64, "assinatura": "b" * 64}
        for corpo in ({**bom, "kid": KID.upper()}, {**bom, "desafio": "a" * 63}, {**bom, "assinatura": 5}, {},
                      {**bom, "x": 1}):
            with self.subTest(corpo=corpo):
                code, _, d = self.post_raw("/api/conversa/entrar", corpo, sessao=False, csrf=False)
                self.assertEqual((code, d), (400, {"error": gm.ENTRADA_INVALIDA}))
        self.assertEqual(self.g.bloqueio.falhas, 1)

    def test_sem_tls_nao_ha_desafio(self):
        self.usar(CONVERSA_TLS_TESTE="")
        d = self.get("/api/conversa/estado", sessao=False, csrf=False)[1]
        self.assertEqual((d["tls"], d["desafio"]), (False, None))
        self.assertEqual(self.g.desafios, {})


class Bloqueio(GuardiaoBase):
    def test_ca7_conta_falhas_de_registo_e_de_entrada(self):
        self.assertEqual(self.registar("0" * 64)[0], 403)                        # código errado
        self.assertEqual(self.sessao(chave=bytes(32))[0], 403)                    # assinatura errada
        self.assertEqual(self.sessao(desafio=secrets.token_hex(32))[0], 403)      # desafio inventado
        self.assertEqual(self.sessao(kid="e" * 16)[0], 403)                       # kid desconhecido
        self.assertEqual(self.g.bloqueio.falhas, 4)
        (self.estado / "entrada.json").unlink()
        self.assertEqual(self.registar()[0:3:2], (403, {"error": gm.CODIGO_USADO}))   # código já usado: a 5.ª
        for passo in (self.sessao, self.registar):   # bloqueado: nem a chave certa nem o código certo
            code, _, d = passo()
            self.assertEqual((code, d["error"]), (429, chat.BLOQUEADO))
            self.assertRegex(d["ate"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertEqual(self.get("/api/conversa/estado")[1]["bloqueado_ate"], d["ate"])
        self.g.bloqueio.ate -= 15 * 60   # passaram os 15 min
        self.assertEqual(self.sessao()[0], 200)
        self.assertEqual(self.g.bloqueio.falhas, 0)

    def test_um_sucesso_zera(self):
        for _ in range(chat.FALHAS_MAX - 1):
            self.sessao(chave=bytes(32))
        self.assertEqual(self.sessao()[0], 200)
        self.assertEqual(self.g.bloqueio.falhas, 0)


class SemTls(GuardiaoBase):
    """Sem CONVERSA_TLS_TESTE: TLS = X-Forwarded-Proto: https (só o proxy chega ao socket) e Origin https://<Host>."""

    tls_teste = False

    def test_ca2_sem_https_nunca_ha_tls(self):
        for h in ({}, {"X-Forwarded-Proto": "http"}):
            with self.subTest(h=h):
                code, d = self.get("/api/conversa/estado", headers=h)
                self.assertEqual((code, d["tls"], d["sessao"]), (200, False, False))
                self.assertEqual(self.get("/api/conversa/lista", headers=h), (403, {"error": chat.SEM_TLS}))
                for p in ("/api/conversa/registar", "/api/conversa/entrar", "/api/conversa/pedido",
                          "/api/conversa/prefs"):
                    code, _, d = self.post_raw(p, {"verificador": VERIFICADOR}, headers=h)
                    self.assertEqual((code, d), (403, {"error": chat.SEM_TLS}))
        self.assertEqual(self.fila(), [])

    def test_com_https_e_origem_https(self):
        h = {"X-Forwarded-Proto": "https"}
        self.assertTrue(self.get("/api/conversa/estado", headers=h)[1]["tls"])
        # com TLS a sério o Origin tem de ser https://<Host> (o http:// só com CONVERSA_TLS_TESTE)
        self.assertEqual(self.sessao(headers=h)[0:3:2], (403, {"error": chat.ORIGEM}))
        o = {**h, "Origin": f"https://{self.host}"}
        self.assertEqual(self.sessao(headers=o)[0], 200)
        self.aceitar_tokens()
        self.conversa()
        code, _, d = self.post_raw("/api/conversa/pedido", self.enviar(cid=CID), headers=o)
        self.assertEqual((code, d), (202, {"ok": True, "conversa": CID}))


class SemTlsComVoz(GuardiaoBase):
    """CA-2 (QA): sem TLS a rota de áudio também recusa com 403, mesmo com a voz ligada, e não escreve na fila."""

    tls_teste = False
    ambiente = {"CONVERSA_VOZ": "local"}

    def test_ca2_audio_sem_tls_403(self):
        self.pulsar(voz_modo="local")
        h = {"Content-Type": "audio/webm", "Origin": f"http://{self.host}", "X-Forwarded-Proto": "http",
             "X-Conversa-Pedido": "{}"}
        code, _, body = self.req("/api/conversa/audio", "POST", b"\x1a\x45\xdf\xa3" + b"\x00" * 60, h)
        self.assertEqual(code, 403, body)
        self.assertIn(chat.SEM_TLS.encode(), body)
        self.assertEqual(self.fila(), [])
        self.assertFalse((self.ver / "audio").exists() and any((self.ver / "audio").iterdir()))
