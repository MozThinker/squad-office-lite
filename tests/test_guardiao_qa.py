"""Guardião da Conversa (ADR-023, revisão S2/S5), QA: dois browsers vêem os mesmos eventos (F6 do Office) e o guardião
nunca grava o código, o verificador nem as chaves em `verificados/`, `publico/` ou `conversas/` (CA-10): só no seu
estado (`/var/lib/office-conversa`)."""
import json
import time

from tests.guardiao_base import CID, VECTORES, GuardiaoBase


class DoisBrowsersTest(GuardiaoBase):
    def test_duas_sessoes_veem_os_mesmos_eventos(self):
        evs = [{"n": 1, "t": "2026-11-23T10:00:00+00:00", "tipo": "mensagem", "autor": "dono", "texto": "olá"},
               {"n": 2, "t": "2026-11-23T10:00:01+00:00", "tipo": "mensagem", "autor": "assistente", "texto": "boas"}]
        self.conversa(eventos=evs)
        self.sessao()
        a = self.cookie
        self.cookie = self.csrf = None   # outro browser: sem cookie, sessão nova
        self.sessao()
        b = self.cookie
        self.assertNotEqual(a, b)
        vistas = []
        for ck in (a, b):
            self.cookie = ck
            code, d = self.get(f"/api/conversa/eventos?conversa={CID}")
            self.assertEqual(code, 200)
            vistas.append(json.dumps(d["eventos"], sort_keys=True))
        self.assertEqual(vistas[0], vistas[1])
        self.assertEqual(json.loads(vistas[0])[1]["autor"], "assistente")


class SemSegredosForaDoEstadoTest(GuardiaoBase):
    def test_ca10_verificados_publico_e_conversas_sem_o_codigo_o_verificador_ou_a_chave(self):
        self.sessao()
        self.conversa()
        for corpo in ({"aviso_tokens": True}, {"nome": "Ana"}):
            self.assertEqual(self.post_raw("/api/conversa/prefs", corpo)[0], 200)
        self.assertEqual(self.post(self.enviar())[0], 202)
        self.cookie = self.csrf = None
        self.registar("0" * 64)   # uma tentativa falhada também não deixa rasto
        self.assertEqual(self.registar(chave=bytes.fromhex(VECTORES["chave"]))[0], 200)   # nem o registo com o código
        self.g.escrever_publico(time.time())
        proibidos = [VECTORES[k].encode() for k in ("codigo", "verificador", "chave")]
        proibidos.append(VECTORES["codigo_mostrado"].encode())
        vistos = 0
        for pasta in ("verificados", "publico", "conversas"):
            for f in (self.root / pasta).rglob("*"):
                if f.is_file():
                    vistos += 1
                    dados = f.read_bytes()
                    for p in proibidos:
                        self.assertNotIn(p, dados, f)
        self.assertGreaterEqual(vistos, 4)   # pedido, publico.json, meta e estado.json
        self.assertFalse((self.root / "conversas" / "entrada.json").exists())
        self.assertEqual(sorted(p.name for p in self.estado.iterdir()), ["chaves.json", "pids.json", "prefs.json"])
