"""Voz da Conversa (ADR-023), lado do host: o trabalhador chat_host/stt.py e a ponte, com o faster-whisper falso
(tests/stt_falso, com av e numpy falsos; nunca carrega modelos) e o servidor falso compatível com
`/v1/audio/transcriptions` (tests/openai_falso.py; nunca sai do host). CA-24, CA-25, CA-26, CA-29: evento
`transcricao`, áudio sempre apagado (a ponte marca-o `visto` e o guardião apaga-o), auditoria sem texto, RAM livre
antes do trabalhador local, chave da voz externa só no trabalhador, avisos `voz_*` distintos, e o texto ditado nunca
aprova nem lança nada."""
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import tests.test_chat_ponte as tcp
from tests.openai_falso import ServidorOpenAI

ROOT = Path(__file__).resolve().parent.parent
STT = ROOT / "chat_host" / "stt.py"
FALSO = ROOT / "tests" / "stt_falso"
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 2044
FRASE = "frase secreta do dono para o teste"
CHAVE_VOZ = "sk-voz-" + "falsa0123456789abcdefghij"
pm = tcp.ponte_mod
gm = tcp.guardiao_mod


def venv_falso(pasta: Path, *caminhos: Path) -> str:
    # um venv sem pip cujo site-packages vê `caminhos` por um .pth: o trabalhador corre com -E -s, e o PYTHONPATH
    # não lhe chega; é assim que os falsos entram, como o faster-whisper entra no venv real
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(pasta)], check=True, timeout=60)
    [sp] = pasta.glob("lib/python3*/site-packages")
    (sp / "falso.pth").write_text("".join(f"{c}\n" for c in caminhos))
    return str(pasta / "bin" / "python")


def meminfo(pasta: Path, livre_mb: int) -> str:
    f = pasta / "meminfo"
    f.write_text(f"MemTotal:        8000000 kB\nMemFree:          100000 kB\nMemAvailable:   {livre_mb * 1024} kB\n")
    return str(f)


class Trabalhador(unittest.TestCase):
    """stt.py --servir (local) e o modo ficheiro, com o falso."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg_f, self.reg = self.root / "falso.json", self.root / "registo.jsonl"
        self.falso()
        self.audio = self.root / "a.webm"
        self.audio.write_bytes(WEBM)
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(FALSO),
                    "CONVERSA_STT_FALSO": str(self.cfg_f), "CONVERSA_STT_MODELOS": str(self.root / "modelos")}

    def tearDown(self):
        self.tmp.cleanup()

    def falso(self, **c):
        self.cfg_f.write_text(json.dumps({"texto": FRASE, "duracao": 3.2, "registo": str(self.reg), **c}))

    def registo(self) -> list[dict]:
        return [json.loads(ln) for ln in self.reg.read_text().splitlines()] if self.reg.exists() else []

    def servir(self, *extra, **env):
        return subprocess.Popen([sys.executable, str(STT), "--servir", *extra], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**self.env, **env})

    def pedir(self, p, id_="a" * 32, **kw):
        p.stdin.write(json.dumps({"id": id_, "ficheiro": str(self.audio), **kw}).encode() + b"\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    @staticmethod
    def fechar(p):
        p.kill()
        p.wait()
        for f in (p.stdin, p.stdout, p.stderr):
            f.close()

    def test_dois_pedidos_uma_carga_idioma_e_eof_sai(self):
        self.falso(ruido=True)
        p = self.servir()
        try:
            r1 = self.pedir(p, "1" * 32)
            r2 = self.pedir(p, "2" * 32, idioma="en")
            r3 = self.pedir(p, "3" * 32, idioma="PT; rm")   # idioma inválido → pt
            p.stdin.close()
            self.assertEqual(p.wait(10), 0)
            err = p.stderr.read().decode()
        finally:
            self.fechar(p)
        self.assertEqual(r1, {"id": "1" * 32, "ok": True, "texto": FRASE, "duracao_s": 3.2})
        self.assertEqual((r2["id"], r3["id"]), ("2" * 32, "3" * 32))
        cargas = [r for r in self.registo() if "carga" in r]
        self.assertEqual(len(cargas), 1)
        self.assertEqual({k: cargas[0][k] for k in ("carga", "device", "compute_type", "download_root")},
                         {"carga": "small", "device": "cpu", "compute_type": "int8",
                          "download_root": str(self.root / "modelos")})
        trs = [r for r in self.registo() if "transcrever" in r]
        self.assertEqual([(r["language"], r["vad_filter"]) for r in trs], [("pt", True), ("en", True), ("pt", True)])
        self.assertIn("ruído do modelo", err)
        self.assertNotIn(FRASE, err)

    def test_ocioso_sai_sozinho(self):
        p = self.servir(CONVERSA_STT_OCIOSO="0.3")
        try:
            self.assertEqual(self.pedir(p)["ok"], True)
            t0 = time.monotonic()
            self.assertEqual(p.wait(10), 0)
            self.assertLess(time.monotonic() - t0, 5)
        finally:
            self.fechar(p)

    def test_erros(self):
        casos = (({"duracao": 131}, {"ok": False, "erro": "longo", "duracao_s": 130.0}),
                 ({"duracao": 130}, {"ok": True, "texto": FRASE, "duracao_s": 130.0}),
                 ({"erro": "descodificar"}, {"ok": False, "erro": "falhou"}),
                 ({"erro": "transcrever"}, {"ok": False, "erro": "falhou"}),
                 ({"erro": "carga"}, {"ok": False, "erro": "falhou"}),
                 ({"texto": ""}, {"ok": True, "texto": "", "duracao_s": 3.2}))
        for cfg, esperado in casos:
            with self.subTest(cfg=cfg):
                self.falso(**cfg)
                r = subprocess.run([sys.executable, str(STT), str(self.audio)], capture_output=True, env=self.env,
                                   timeout=30)
                self.assertEqual(json.loads(r.stdout), esperado)
                self.assertEqual(r.returncode, 0 if esperado["ok"] else 1)

    def test_bomba_de_audio_recusada_sem_transcrever(self):
        self.falso(duracao=1e9)
        r = subprocess.run([sys.executable, str(STT), str(self.audio)], capture_output=True, env=self.env, timeout=30,
                           check=False)
        self.assertEqual(json.loads(r.stdout), {"ok": False, "erro": "longo", "duracao_s": 130.0})
        self.assertEqual([x["quadros"] for x in self.registo() if "quadros" in x], [131])
        self.assertEqual([x for x in self.registo() if "transcrever" in x], [])

    def test_sem_faster_whisper_indisponivel_e_uso(self):
        sem = self.root / "sem"
        sem.mkdir()
        (sem / "faster_whisper.py").write_text("raise ImportError('não instalado')\n")
        r = subprocess.run([sys.executable, str(STT), str(self.audio)], capture_output=True,
                           env={**self.env, "PYTHONPATH": str(sem)}, timeout=30)
        self.assertEqual(json.loads(r.stdout), {"ok": False, "erro": "indisponivel"})
        for args in ([], ["--servir", "--outra"], ["-x"]):
            r = subprocess.run([sys.executable, str(STT), *args], capture_output=True, env=self.env, timeout=30)
            self.assertEqual(r.returncode, 2)


class TrabalhadorExterna(unittest.TestCase):
    """stt.py --servir --externa contra o servidor falso (CA-26)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.srv = ServidorOpenAI(texto=f"  {FRASE}  ")
        self.addCleanup(self.srv.fechar)
        self.audio = self.root / "a.webm"
        self.audio.write_bytes(WEBM)
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CONVERSA_STT_URL": self.srv.url,
                    "CONVERSA_STT_CHAVE": CHAVE_VOZ, "CONVERSA_STT_MODELO": "modelo-x", "CONVERSA_STT_TEMPO": "3"}

    def tearDown(self):
        self.tmp.cleanup()

    def correr(self, **env):
        p = subprocess.Popen([sys.executable, "-I", str(STT), "--servir", "--externa"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**self.env, **env})
        out, err = p.communicate(json.dumps({"id": "a" * 32, "ficheiro": str(self.audio), "idioma": "en"}).encode()
                                 + b"\n", timeout=30)
        return json.loads(out), err.decode()

    def test_ok_multipart_e_bearer(self):
        r, err = self.correr()
        self.assertEqual(r, {"id": "a" * 32, "ok": True, "texto": FRASE, "duracao_s": 2.5})
        [p] = self.srv.pedidos
        self.assertEqual(p["caminho"], "/v1/audio/transcriptions")
        self.assertEqual(p["cabecalhos"]["Authorization"], f"Bearer {CHAVE_VOZ}")
        c = p["campos"]
        self.assertEqual((c["model"], c["language"], c["response_format"]), ("modelo-x", "en", "json"))
        self.assertEqual((c["file"]["nome"], c["file"]["tipo"], c["file"]["dados"]), ("audio.webm", "audio/webm", WEBM))
        self.assertNotIn(CHAVE_VOZ, err)
        self.assertNotIn(FRASE, err)

    def test_erros_distintos(self):
        for modo, erro in (("401", "chave"), ("403", "chave"), ("429", "limite"), ("500", "falhou"), ("lixo", "falhou"),
                           ("lento", "tempo")):
            with self.subTest(modo=modo):
                self.srv.modo = modo
                r, err = self.correr(CONVERSA_STT_TEMPO="1")
                self.assertEqual(r, {"id": "a" * 32, "ok": False, "erro": erro})
                self.assertNotIn(CHAVE_VOZ, err)

    def test_redirect_falha_sem_reenviar_a_chave(self):   # C2
        for modo in ("302", "307"):
            with self.subTest(modo=modo):
                self.srv.modo, self.srv.pedidos = modo, []
                r, err = self.correr()
                self.assertEqual(r, {"id": "a" * 32, "ok": False, "erro": "falhou"})
                self.assertEqual([p["caminho"] for p in self.srv.pedidos], ["/v1/audio/transcriptions"])
                self.assertNotIn(CHAVE_VOZ, err)

    def test_endpoint_e_sem_chave(self):
        stt = tcp._carregar("chat_host_stt", STT)
        self.assertEqual(stt.endpoint("https://voz.exemplo"), "https://voz.exemplo/v1/audio/transcriptions")
        self.assertEqual(stt.endpoint("https://voz.exemplo/v1/"), "https://voz.exemplo/v1/audio/transcriptions")
        self.assertEqual(stt.endpoint("https://voz.exemplo/api"), "https://voz.exemplo/api/v1/audio/transcriptions")
        self.assertEqual(stt.endpoint("http://127.0.0.1:9000"), "http://127.0.0.1:9000/v1/audio/transcriptions")
        for mau in ("http://voz.exemplo", "ftp://x", "https://u:p@voz.exemplo", "https://x/?a=1", "", "voz.exemplo"):
            with self.subTest(mau=mau):
                self.assertIsNone(stt.endpoint(mau))
        r, _ = self.correr(CONVERSA_STT_CHAVE="")
        self.assertEqual(r, {"id": "a" * 32, "ok": False, "erro": "indisponivel"})
        r, _ = self.correr(CONVERSA_STT_URL="http://voz.exemplo")
        self.assertEqual(r["erro"], "indisponivel")
        self.assertEqual(self.srv.pedidos, [])


class PonteVozBase(tcp.PonteBase):
    def setUp(self):
        super().setUp()
        self.cfg_f, self.reg = self.root / "falso.json", self.root / "registo-stt.jsonl"
        self.falso()
        self.env["CONVERSA_VOZ"] = "local"
        self.penv.update(CONVERSA_STT_FALSO=str(self.cfg_f), CONVERSA_STT_MODELOS=str(self.root / "modelos"),
                         CONVERSA_MEMINFO=meminfo(self.root, 4096))

    def falso(self, **c):
        self.cfg_f.write_text(json.dumps({"texto": FRASE, "duracao": 3.2, "registo": str(self.reg), **c}))

    def registo_stt(self) -> list[dict]:
        return [json.loads(ln) for ln in self.reg.read_text().splitlines()] if self.reg.exists() else []

    def transcrever(self, cid, dados=WEBM, ext="webm", audio=None, nbytes=None, sha=None) -> tuple[str, str]:
        import secrets
        audio = audio or secrets.token_hex(16)
        (self.root / "verificados" / "audio").mkdir(mode=0o700, exist_ok=True)
        f = self.root / "verificados" / "audio" / f"{audio}.{ext}"
        if dados is not None:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(fd, "wb") as fh:
                fh.write(dados)
        nome = self.pedido("transcrever", conversa=cid, audio=audio, formato=ext,
                           bytes=len(WEBM) if nbytes is None else nbytes,
                           sha256=sha or hashlib.sha256(WEBM if dados is None else dados).hexdigest(), idioma="pt")
        return audio, nome

    def voz(self, cid, audio):
        return next((e for e in self.ev(cid) if e.get("audio") == audio), None)

    def esperar_voz(self, cid, audio, timeout=20.0):
        return self.ate(lambda: self.voz(cid, audio), timeout=timeout, msg=f"evento de voz de {audio}")

    def audios(self):
        return sorted(os.listdir(self.root / "verificados" / "audio"))

    def apagado(self) -> bool:
        # a ponte nunca apaga o original: publica-o em `vistos` e a limpeza do guardião apaga o pedido e o áudio
        cfg = gm.config({"CONVERSA_PASTA": str(self.root), "CONVERSA_GUARDIAO_ESTADO": str(self.root / "guardiao")})
        gm.Guardiao(cfg, {}).limpar(time.time())
        return self.audios() == [] and self.copias() == []

    def copias(self):
        return sorted(os.listdir(self.root / "estado" / "stt"))

    def aud_voz(self):
        return [a for a in self.auditoria() if a["tipo"] == "transcricao"]

    def ficheiros_com(self, texto: str) -> list[str]:
        achados = []
        for base in ("estado", "verificados", "conversas"):
            for dirpath, _, nomes in os.walk(self.root / base):
                for n in nomes:
                    f = Path(dirpath) / n
                    if n != "eventos.jsonl" and f.is_file() and not f.is_symlink() \
                            and texto.encode() in f.read_bytes():
                        achados.append(str(f.relative_to(self.root)))
        return achados


class PonteVozLocal(PonteVozBase):
    def setUp(self):
        super().setUp()
        self.penv["CONVERSA_STT_PYTHON"] = venv_falso(self.root / "venv", FALSO)

    def test_transcricao_evento_apagado_e_auditoria_sem_texto(self):   # CA-24
        self.falso(texto=f"{FRASE} {tcp.SEGREDO_GH} \x07fim")
        self.nova_ponte()
        cid = self.nova()
        antes = self.meta(cid)
        audio, nome = self.transcrever(cid)
        self.assertEqual(self.tratado(nome), {"ok": True})
        e = self.esperar_voz(cid, audio)
        self.assertEqual(set(e), {"n", "t", "tipo", "audio", "texto", "duracao_s", "tempo_s"})
        self.assertEqual((e["tipo"], e["texto"], e["duracao_s"]), ("transcricao", f"{FRASE} [segredo] fim", 3.2))
        depois = self.meta(cid)
        for k in ("estado", "titulo", "pendentes", "ultimo_fim", "arquivada"):
            self.assertEqual(depois[k], antes[k], k)
        self.assertEqual(self.registos(), [])   # nenhum turno do motor
        self.ate(self.apagado, msg="áudio apagado")
        [a] = self.aud_voz()
        self.assertEqual(set(a), {"t", "tipo", "conversa", "audio", "modo", "bytes", "sha256", "duracao_s", "tempo_s",
                                  "resultado"})
        self.assertEqual((a["conversa"], a["audio"], a["modo"], a["bytes"], a["sha256"], a["duracao_s"],
                          a["resultado"]),
                         (cid, audio, "local", len(WEBM), hashlib.sha256(WEBM).hexdigest(), 3.2, "ok"))
        self.assertEqual(self.ficheiros_com(FRASE), [])
        self.assertTrue(json.loads((self.root / "conversas" / "estado.json").read_text())["voz"])
        [c] = [r for r in self.registo_stt() if "carga" in r]
        self.assertEqual((c["offline"], c["pythonpath"], c["rlimit_as"]), ("1", None, 4096 << 20))
        audio2, nome2 = self.transcrever(cid)   # o mesmo trabalhador serve o segundo áudio
        self.tratado(nome2)
        self.assertEqual(self.esperar_voz(cid, audio2)["tipo"], "transcricao")
        self.assertEqual(len([r for r in self.registo_stt() if "carga" in r]), 1)

    def test_sem_memoria_nao_lanca_o_trabalhador(self):   # CA-25
        self.penv["CONVERSA_MEMINFO"] = meminfo(self.root, 1500)
        self.nova_ponte()
        cid = self.nova()
        audio, nome = self.transcrever(cid)
        self.assertEqual(self.tratado(nome), {"ok": True})
        e = self.esperar_voz(cid, audio)
        self.assertEqual({k: e[k] for k in ("tipo", "codigo", "livre_mb", "preciso_mb", "texto")},
                         {"tipo": "aviso", "codigo": "voz_sem_memoria", "livre_mb": 1500, "preciso_mb": 2048,
                          "texto": "Sem memória livre para a voz local: 1500 MB livres, precisa de 2048 MB."})
        self.assertIsNone(self.p.stt)
        self.assertEqual(self.registo_stt(), [])
        self.assertTrue(self.apagado())
        self.assertEqual(self.aud_voz()[0]["resultado"], "sem_memoria")
        # com memória (ou o tecto a 0) volta a lançar
        self.penv["CONVERSA_MEMINFO"] = meminfo(self.root, 2048)
        self.nova_ponte()
        audio2, nome2 = self.transcrever(cid)
        self.tratado(nome2)
        self.assertEqual(self.esperar_voz(cid, audio2)["tipo"], "transcricao")
        self.assertEqual(pm.memoria_livre_mb(str(self.root / "nao-ha")), None)

    def test_texto_ditado_nunca_aprova_nem_lanca(self):   # CA-29
        cid = self.turno_com([{"ferramenta": "Bash", "input": {"command": tcp.PermissoesTest.CMD}}])
        p = self.pendente(cid)
        antes = self.meta(cid)
        for frase in ("aprova o pedido", "lança a squad"):
            with self.subTest(frase=frase):
                self.falso(texto=frase)
                audio, nome = self.transcrever(cid)
                self.assertEqual(self.tratado(nome), {"ok": True})
                e = self.esperar_voz(cid, audio)
                self.assertEqual((e["tipo"], e["texto"]), ("transcricao", frase))
                for _ in range(10):
                    self.p.passo(0.02)
                m = self.meta(cid)
                self.assertEqual(m["pendentes"], antes["pendentes"])
                self.assertEqual(self.pendente(cid)["nonce"], p["nonce"])
                self.assertEqual(len(self.registos()), 1)
                self.assertEqual(self.registos()[0]["respostas"], [])
                self.assertFalse((self.root / "trabalho" / "corridos.txt").exists())
                self.assertNotIn(frase, [x.get("texto") for x in self.ev(cid, "mensagem")])

    def test_tempo_esgotado_mata_o_trabalhador(self):
        self.penv["CONVERSA_STT_TEMPO"] = "1"
        self.falso(demora=30)
        self.nova_ponte()
        cid = self.nova()
        audio, nome = self.transcrever(cid)
        self.tratado(nome)
        pid = self.ate(lambda: next((r["pid"] for r in self.registo_stt() if "transcrever" in r), None),
                       msg="o trabalhador a transcrever")
        e = self.esperar_voz(cid, audio, timeout=10)
        self.assertEqual((e["codigo"], e["texto"]), ("voz_tempo", pm.VOZ_TEMPO))
        self.ate(lambda: not self.vivo(pid), msg="trabalhador morto")
        self.assertTrue(self.apagado())

    def test_falhas_voz_falhou_e_relanca_uma_vez(self):
        self.nova_ponte()
        cid = self.nova()
        for cfg, texto in (({"erro": "transcrever"}, pm.VOZ_FALHOU), ({"duracao": 131}, pm.VOZ_LONGO),
                           ({"sair": True}, pm.VOZ_FALHOU)):
            with self.subTest(cfg=cfg):
                self.falso(**cfg)
                audio, nome = self.transcrever(cid)
                self.tratado(nome)
                e = self.esperar_voz(cid, audio)
                self.assertEqual((e["tipo"], e["codigo"], e["texto"]), ("aviso", "voz_falhou", texto))
                self.ate(self.apagado, msg="áudio apagado")
        mortes = [r for r in self.registo_stt() if "transcrever" in r]
        self.assertEqual(len({r["pid"] for r in mortes[-2:]}), 2)
        self.falso(demora=30)
        audio, nome = self.transcrever(cid)
        self.tratado(nome)
        pid = self.ate(lambda: next((r["pid"] for r in self.registo_stt() if "transcrever" in r and r.get("pid")
                                     not in {m["pid"] for m in mortes}), None), msg="o trabalhador a transcrever")
        self.falso()
        os.kill(pid, signal.SIGKILL)
        self.assertEqual(self.esperar_voz(cid, audio)["tipo"], "transcricao")

    def test_audio_invalido_ou_trocado_e_apagado(self):
        self.nova_ponte()
        cid = self.nova()
        fora = self.root / "fora.webm"
        fora.write_bytes(WEBM)
        import secrets
        a1 = secrets.token_hex(16)
        (self.root / "verificados" / "audio").mkdir(mode=0o700, exist_ok=True)
        os.symlink(fora, self.root / "verificados" / "audio" / f"{a1}.webm")
        _, n1 = self.transcrever(cid, dados=None, audio=a1)
        a2, n2 = self.transcrever(cid, nbytes=len(WEBM) + 1)           # tamanho diferente do assinado
        a3, n3 = self.transcrever(cid, dados=None)                      # não existe
        a4, n4 = self.transcrever(cid, sha="0" * 64)                    # o Office trocou o áudio
        for a, n in ((a1, n1), (a2, n2), (a3, n3), (a4, n4)):
            with self.subTest(a=a):
                self.assertEqual(self.tratado(n), {"erro": "áudio inválido"})
                self.assertEqual(self.voz(cid, a)["codigo"], "voz_falhou")
        self.assertTrue(self.apagado())
        self.assertTrue(fora.exists())   # o guardião apaga o symlink, nunca o alvo
        self.assertEqual([a["resultado"] for a in self.aud_voz()], ["invalido"] * 4)
        self.assertIsNone(self.p.stt)

    def test_sem_venv_indisponivel_e_desligada(self):
        self.penv["CONVERSA_STT_PYTHON"] = str(self.root / "nao-ha" / "python")
        self.nova_ponte()
        cid = self.nova()
        audio, nome = self.transcrever(cid)
        self.assertEqual(self.tratado(nome), {"ok": True})
        self.assertEqual(self.voz(cid, audio)["codigo"], "voz_indisponivel")
        self.env["CONVERSA_VOZ"] = "desligada"
        self.nova_ponte()
        audio, nome = self.transcrever(cid)
        self.tratado(nome)
        self.assertEqual((self.voz(cid, audio)["codigo"], self.voz(cid, audio)["texto"]),
                         ("voz_indisponivel", pm.VOZ_INDISPONIVEL))
        self.assertEqual([a["modo"] for a in self.aud_voz()], ["local", "desligada"])
        self.assertIsNone(self.p.stt)

    def test_arranque_apaga_as_copias_e_nunca_o_original(self):
        audio_dir = self.root / "verificados" / "audio"
        audio_dir.mkdir(mode=0o700)
        (self.root / "estado" / "stt").mkdir(mode=0o700)
        (self.root / "estado" / "stt" / f"{'c' * 32}.webm").write_bytes(WEBM)
        velho = audio_dir / f"{'a' * 32}.webm"
        velho.write_bytes(WEBM)
        t = time.time() - 7200
        os.utime(velho, (t, t))
        self.nova_ponte()
        self.assertEqual(self.copias(), [])
        self.p.passo(0.01)
        self.assertEqual(self.audios(), [velho.name])   # verificados/ é do guardião: a ponte só lê

    def test_texto_voz(self):
        f = pm.texto_voz
        self.assertEqual(f("  olá\x00\x1b mundo\n\tfim \ud800"), "olá mundo\n\tfim")
        self.assertEqual(len(f("a" * 5000)), pm.TRANSCRICAO_MAX)
        self.assertEqual(f(f"chave {tcp.SEGREDO_SK}"), "chave [segredo]")


class PonteVozExterna(PonteVozBase):
    """CA-26: a chave só no trabalhador; 401, 429 e tempo esgotado viram avisos distintos."""

    def setUp(self):
        super().setUp()
        self.srv = ServidorOpenAI(texto=FRASE)
        self.addCleanup(self.srv.fechar)
        self.env["CONVERSA_VOZ"] = "externa"
        self.penv.update(CONVERSA_STT_URL=self.srv.url, CONVERSA_STT_CHAVE=CHAVE_VOZ, CONVERSA_STT_TEMPO="2",
                         CONVERSA_MEMINFO=meminfo(self.root, 10))   # a RAM só conta na voz local

    def test_transcricao_e_chave_em_lado_nenhum(self):
        self.cenario(passos=[{"texto": "respondi"}])
        self.nova_ponte()
        cid = self.nova()
        audio, nome = self.transcrever(cid)
        self.assertEqual(self.tratado(nome), {"ok": True})
        e = self.esperar_voz(cid, audio)
        self.assertEqual((e["tipo"], e["texto"], e["duracao_s"]), ("transcricao", FRASE, 2.5))
        self.assertEqual(self.srv.pedidos[0]["cabecalhos"]["Authorization"], f"Bearer {CHAVE_VOZ}")
        self.assertEqual(self.srv.pedidos[0]["campos"]["file"]["dados"], WEBM)
        pulso = json.loads((self.root / "conversas" / "estado.json").read_text())
        self.assertEqual((pulso["voz"], pulso["voz_modo"], pulso["voz_fornecedor"]), (True, "externa", "127.0.0.1"))
        self.assertEqual(self.aud_voz()[0]["modo"], "externa")
        self.tratado(self.enviar(cid, "olá"))   # o motor nunca vê a chave
        self.fim(cid)
        self.assertNotIn("CONVERSA_STT_CHAVE", self.registos()[0]["env"])
        for f in self.root.rglob("*"):
            if f.is_file() and not f.is_symlink():
                self.assertNotIn(CHAVE_VOZ.encode(), f.read_bytes(), str(f))
        self.assertEqual(self.ficheiros_com(FRASE), [])

    def test_avisos_distintos(self):
        self.nova_ponte()
        cid = self.nova()
        for modo, codigo, texto in (("401", "voz_chave", pm.VOZ_CHAVE), ("429", "voz_limite", pm.VOZ_LIMITE),
                                    ("lento", "voz_tempo", pm.VOZ_TEMPO), ("500", "voz_falhou", pm.VOZ_FALHOU)):
            with self.subTest(modo=modo):
                self.srv.modo = modo
                audio, nome = self.transcrever(cid)
                self.tratado(nome)
                e = self.esperar_voz(cid, audio, timeout=15)
                self.assertEqual((e["tipo"], e["codigo"], e["texto"]), ("aviso", codigo, texto))
                self.ate(self.apagado, msg="áudio apagado")
        self.assertEqual([a["resultado"] for a in self.aud_voz()], ["chave", "limite", "tempo", "falhou"])
        self.assertNotIn(CHAVE_VOZ, (self.root / "estado" / "stderr" / "stt.log").read_text())

    def test_voz_local_nao_recebe_a_chave(self):
        self.env["CONVERSA_VOZ"] = "local"
        self.penv.update(CONVERSA_STT_PYTHON=venv_falso(self.root / "venv", FALSO),
                         CONVERSA_MEMINFO=meminfo(self.root, 4096))
        self.nova_ponte()
        p = self.p
        env = {}
        with mock.patch.object(pm.subprocess, "Popen", side_effect=lambda argv, **kw: env.update(kw["env"])
                                        or (_ for _ in ()).throw(OSError("parado"))):
            p._stt_lancar()
        self.assertNotIn("CONVERSA_STT_CHAVE", env)
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")


if __name__ == "__main__":
    unittest.main()
