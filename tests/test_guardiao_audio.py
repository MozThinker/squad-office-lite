"""Voz na Conversa (ADR-023, revisão S2/S5), a rota POST /api/conversa/audio do guardião numa porta local: 404 com a
voz desligada (CA-23), 403 da externa sem o aviso do fornecedor actual (CA-27), 503 com a voz pedida e não instalada,
403 sem a assinatura certa, 409 com um `pid` repetido, 400 quando o pedido assinado não bate com o corpo e os
cabeçalhos, limites portados (CA-28: 5 MB sem ler o corpo, magic bytes, um de cada vez, que ignora os `vistos`) e 202
com o áudio 0640 e o `transcrever` assinado em verificados/."""
import hashlib
import http.client
import json
import os
import secrets
import time

import chat
from tests.guardiao_base import CHAVE, CID, GuardiaoBase, gm

CABECAS = {"webm": b"\x1a\x45\xdf\xa3", "ogg": b"OggS", "m4a": b"\x00\x00\x00\x20ftypM4A ", "mp3": b"ID3\x04"}
TIPOS = {"webm": "audio/webm;codecs=opus", "ogg": "audio/ogg;codecs=opus", "m4a": "audio/mp4", "mp3": "audio/mpeg"}
FORN = "api.exemplo.com"


def corpo(ext="webm", n=4096) -> bytes:
    c = CABECAS[ext]
    return c + b"\x00" * (n - len(c))


class Base(GuardiaoBase):
    ambiente = {"CONVERSA_VOZ": "local"}
    voz = "local"
    idioma = "pt"

    def setUp(self):
        super().setUp()
        self.pulsar(voz_modo=self.voz, fornecedor=FORN if self.voz == "externa" else None)
        self.conversa()
        self.sessao()

    def pedido(self, dados: bytes, ext="webm", **mudar) -> dict:
        campos = {"conversa": CID, "audio": secrets.token_hex(16), "formato": ext, "bytes": len(dados),
                  "sha256": hashlib.sha256(dados).hexdigest(), "idioma": self.idioma, **mudar}
        return self.assinado("transcrever", **campos)

    def audio(self, dados=None, ext="webm", pedido=None, headers=None, **k):   # noqa: PLR0913
        dados = corpo(ext) if dados is None else dados
        p = self.pedido(dados, ext) if pedido is None else pedido
        h = {"Content-Type": TIPOS[ext], "X-Conversa-Pedido": json.dumps(p), **self.cab(**k), **(headers or {})}
        code, _, body = self.req("/api/conversa/audio", "POST", dados, {x: y for x, y in h.items() if y is not None})
        return code, json.loads(body) if body else None

    def audios(self) -> list[str]:
        p = self.ver / "audio"
        return sorted(os.listdir(p)) if p.exists() else []

    def limpar(self):
        for n in self.audios():
            (self.ver / "audio" / n).unlink()
        for n in self.fila():
            (self.ver / n).unlink()


class Desligada(Base):
    ambiente = {}
    voz = "desligada"

    def test_ca23_404_antes_de_tudo(self):
        self.assertEqual(self.audio(), (404, {"error": chat.NAO_ENCONTRADO}))
        self.assertEqual(self.audio(origem=False, csrf=False, sessao=False), (404, {"error": chat.NAO_ENCONTRADO}))
        self.assertEqual(self.get("/api/conversa/estado")[1]["voz"], "desligada")
        self.assertEqual((self.fila(), self.audios()), ([], []))


class Local(Base):
    def test_202_ficheiro_0640_e_pedido_assinado(self):
        for ext in ("webm", "ogg", "m4a", "mp3"):
            with self.subTest(ext=ext):
                dados = corpo(ext, 5000)
                p = self.pedido(dados, ext)
                code, d = self.audio(dados, ext, pedido=p, headers={"User-Agent": "telemovel"})
                self.assertEqual((code, d), (202, {"ok": True, "audio": p["audio"]}))
                f = self.ver / "audio" / f"{p['audio']}.{ext}"
                self.assertEqual((self.audios(), f.read_bytes()), ([f.name], dados))
                self.assertEqual(os.stat(f).st_mode & 0o777, 0o640)
                [doc] = self.ler_fila()
                self.assertIsNone(chat.validar_fila(doc))
                self.assertEqual({k: v for k, v in doc.items() if k not in chat.EXTRA_FILA}, p)
                self.assertEqual(doc["ua"], "telemovel")
                self.limpar()

    def test_relogio_desacertado_400(self):   # C1
        dados = corpo("webm", 5000)
        p = self.pedido(dados, t=int(time.time() * 1000) - 301_000)
        self.assertEqual(self.audio(dados, pedido=p), (400, {"error": chat.RELOGIO}))
        self.assertEqual((self.fila(), self.audios()), ([], []))

    def test_ca28_413_sem_ler_o_corpo(self):
        c = http.client.HTTPConnection(self.host, timeout=10)
        try:
            c.putrequest("POST", "/api/conversa/audio")
            for k, v in {**self.cab(), "Content-Type": "audio/webm",
                         "Content-Length": str(chat.AUDIO_MAX + 1)}.items():
                c.putheader(k, v)
            c.endheaders()
            self.assertEqual(c.getresponse().status, 413)
        finally:
            c.close()

    def test_ca28_tamanho_tipo_e_magic(self):
        self.assertEqual(self.audio(corpo(n=1023)), (400, {"error": chat.AUDIO_VAZIO}))
        self.assertEqual(self.audio(headers={"Content-Type": "audio/wav"}), (415, {"error": chat.AUDIO_FORMATO}))
        mau = b"RIFF" + b"\x00" * 4092
        self.assertEqual(self.audio(mau), (415, {"error": chat.AUDIO_FORMATO}))
        self.assertEqual((self.fila(), self.audios()), ([], []))

    def test_ca28_um_de_cada_vez(self):
        self.assertEqual(self.audio()[0], 202)
        self.assertEqual(self.audio(), (429, {"error": chat.AUDIO_OCUPADO}))
        self.assertEqual(len(self.audios()), 1)
        self.pulsar(voz_modo="local", vistos=self.fila())   # a ponte já o tem: o seguinte pode entrar
        self.assertEqual(self.audio()[0], 202)

    def test_assinatura_e_pid(self):   # S2: sem a chave não se grava nem o áudio
        dados = corpo()
        p = self.pedido(dados)
        mau = {**p, "assinatura": chat.assinar(bytes(32), p)}
        self.assertEqual(self.audio(dados, pedido=mau), (403, {"error": gm.ASSINATURA}))
        sem = {k: v for k, v in p.items() if k != "assinatura"}
        self.assertEqual(self.audio(dados, pedido=sem), (403, {"error": gm.ASSINATURA}))
        self.assertEqual((self.fila(), self.audios()), ([], []))
        self.assertEqual(self.audio(dados, pedido=p)[0], 202)
        self.limpar()
        self.assertEqual(self.audio(dados, pedido=p), (409, {"error": gm.REPETIDO}))
        self.assertEqual((self.fila(), self.audios()), ([], []))

    def test_pedido_que_nao_bate_com_o_corpo(self):
        dados = corpo()
        casos = {"bytes": self.pedido(dados, bytes=len(dados) + 1),
                 "formato": self.pedido(dados, formato="ogg"),
                 "idioma": self.pedido(dados, idioma="en"),
                 "sha256": self.pedido(dados, sha256="0" * 64)}
        for nome, p in casos.items():
            with self.subTest(nome=nome):
                self.assertEqual(self.audio(dados, pedido=p), (400, {"error": chat.AUDIO_NAO_BATE}))
        self.assertEqual((self.fila(), self.audios()), ([], []))

    def test_pedido_mal_formado(self):
        dados = corpo()
        sem_pid = {k: v for k, v in self.pedido(dados).items() if k not in ("pid", "assinatura")}
        sem_pid["assinatura"] = chat.assinar(CHAVE, sem_pid)
        for p in (self.pedido(dados, extra=1), sem_pid, self.assinado("parar", conversa=CID)):   # assinados
            with self.subTest(p=p):
                self.assertEqual(self.audio(dados, pedido=p)[0], 400)
        code, _, _ = self.req("/api/conversa/audio", "POST", dados,
                              {"Content-Type": "audio/webm", "X-Conversa-Pedido": "{x", **self.cab()})
        self.assertEqual(code, 400)

    def test_503_ponte_e_voz_nao_instalada(self):
        self.pulsar(voz_modo="desligada")
        self.assertEqual(self.audio(), (503, {"error": "a voz local não está instalada no servidor"}))
        self.pulsar(voz_modo="externa", fornecedor=FORN)
        self.assertEqual(self.audio()[0], 503)
        self.pulsar(idade=61, voz_modo="local")
        self.assertEqual(self.audio(), (503, {"error": chat.PONTE_PARADA}))
        self.assertEqual((self.fila(), self.audios()), ([], []))

    def test_conversa_inexistente_e_arquivada(self):
        dados = corpo()
        self.assertEqual(self.audio(dados, pedido=self.pedido(dados, conversa="20261123-101500-000000")),
                         (404, {"error": chat.NAO_ENCONTRADO}))
        self.conversa(arquivada=True)
        self.assertEqual(self.audio(), (409, {"error": chat.ARQUIVADA}))

    def test_sem_sessao_401_e_sem_csrf_403(self):
        self.assertEqual(self.audio(sessao=False), (401, {"error": chat.SESSAO_INVALIDA}))
        self.assertEqual(self.audio(csrf=False), (403, {"error": chat.CSRF_INVALIDO}))
        self.assertEqual(self.audio(origem=False), (403, {"error": chat.ORIGEM}))

    def test_verificados_cheia_apaga_o_audio(self):
        for i in range(chat.PENDENTES_MAX):
            (self.ver / f"20261123T100000-{i:08x}.json").write_text("{}")
        self.assertEqual(self.audio(), (503, {"error": chat.FILA_CHEIA}))
        self.assertEqual(self.audios(), [])


class Externa(Base):
    ambiente = {"CONVERSA_VOZ": "externa", "CONVERSA_VOZ_IDIOMA": "en"}
    voz = "externa"
    idioma = "en"

    def test_ca27_403_sem_aviso_e_200_depois(self):
        self.assertEqual(self.audio(), (403, {"error": chat.AVISO_VOZ}))
        self.assertFalse(self.get("/api/conversa/estado")[1]["aviso_voz"])
        code, _, d = self.post_raw("/api/conversa/prefs", {"aviso_voz": True})
        self.assertEqual((code, d["aviso_voz"]), (200, True))
        prefs = json.loads((self.estado / "prefs.json").read_text(encoding="utf-8"))
        self.assertEqual(prefs["aviso_voz"]["fornecedor"], FORN)
        e = self.get("/api/conversa/estado")[1]
        self.assertEqual((e["aviso_voz"], e["voz_fornecedor"], e["voz_idioma"]), (True, FORN, "en"))
        self.assertEqual(self.audio()[0], 202)

    def test_ca27_outro_fornecedor_pede_o_aviso_de_novo(self):
        self.post_raw("/api/conversa/prefs", {"aviso_voz": True})
        self.pulsar(voz_modo="externa", fornecedor="outro.exemplo.org")
        self.assertFalse(self.get("/api/conversa/estado")[1]["aviso_voz"])
        self.assertEqual(self.audio(), (403, {"error": chat.AVISO_VOZ}))

    def test_aviso_sem_fornecedor_anunciado_409(self):
        self.pulsar(voz_modo="externa", fornecedor=None)
        code, _, d = self.post_raw("/api/conversa/prefs", {"aviso_voz": True})
        self.assertEqual((code, d), (409, {"error": chat.SEM_FORNECEDOR}))

    def test_fornecedor_mal_formado_nao_conta(self):
        self.pulsar(voz_modo="externa", fornecedor="https://x/../y")
        self.assertIsNone(self.get("/api/conversa/estado")[1]["voz_fornecedor"])


class Rota(Base):
    def test_ca12_guardiao_sem_rede_de_saida_nem_processos(self):
        # o guardião só ouve: nada de subprocess, urllib.request, ssl nem sockets de saída
        import ast
        mods = set()
        for n in ast.walk(ast.parse(gm.Path(gm.__file__).read_text(encoding="utf-8"))):
            if isinstance(n, ast.Import):
                mods |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add(n.module or "")
        self.assertFalse({m for m in mods if m.split(".")[0] in ("subprocess", "socket", "ssl")
                          or m.startswith("urllib.request")}, mods)

    def test_estado_mostra_a_voz(self):
        e = self.get("/api/conversa/estado")[1]
        self.assertEqual((e["voz"], e["voz_ponte"], e["voz_fornecedor"]), ("local", "local", None))
        self.usar(CONVERSA_VOZ="externa")
        self.assertEqual(self.get("/api/conversa/estado")[1]["voz"], "externa")
