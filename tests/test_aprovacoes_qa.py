"""QA das aprovações: o que tests/test_aprovacoes.py não cobria (CA1, CA8, CA11, CA13, CA14, CA15, CA27, CA28).

Tudo em pastas temporárias; nada de rede nem do relógio real (o «agora» é fixo).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import aprovacoes
from tests.test_aprovacoes import NOME_FILA, NOW, SECRET, TZ, Base, pedido, sha
from tests import cfg

OFFICE_CFG = cfg.LEGADO

ROOT = Path(__file__).resolve().parent.parent


def arranque(env_extra: dict) -> dict:
    # importa o server.py num processo novo (como um arranque do contentor) e devolve TOKEN e APROVACOES
    with tempfile.TemporaryDirectory() as t:
        env = {k: v for k, v in os.environ.items() if k not in ("APROVACOES_DIR", "DECISOES_FILA_DIR")}
        env.update({"TASKS_DIR": t, "CLAUDE_PROJECTS_DIR": t, **env_extra})
        code = ("import json, server; print(json.dumps({'token': server.TOKEN, "
                "'apr': {k: str(v) for k, v in server.APROVACOES.items()}, 'fila': str(server.FILA)}))")
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=30, check=True)
        return json.loads(r.stdout)


class ArranqueTest(unittest.TestCase):
    def test_sem_variavel_omissoes_do_contentor(self):
        # Lite: uma fonte «geral» em APROVACOES_DIR (omissão /data/aprovacoes)
        a = arranque({})
        self.assertEqual(a["apr"], {"geral": "/data/aprovacoes"})
        self.assertEqual(a["fila"], "/data/decisoes-fila")

    def test_token_aleatorio_muda_a_cada_arranque(self):
        # CA14: secrets.token_urlsafe(32) → 43 car. url-safe, diferente em cada arranque
        a, b = arranque({})["token"], arranque({})["token"]
        self.assertNotEqual(a, b)
        for tok in (a, b):
            self.assertRegex(tok, r"^[A-Za-z0-9_-]{43}$")

    def test_token_comparado_em_tempo_constante(self):
        src = (ROOT / "server.py").read_text()
        self.assertIn("secrets.token_urlsafe(32)", src)
        self.assertIn("hmac.compare_digest(", src)
        self.assertNotRegex(src, r"tok\w*\s*[!=]=\s*TOKEN|TOKEN\s*[!=]=")

    def test_fonte_da_variavel_e_pasta_inexistente(self):
        with tempfile.TemporaryDirectory() as t:
            existe = Path(t) / "m"
            existe.mkdir()
            (existe / "a.md").write_text(pedido())
            a = arranque({"APROVACOES_DIR": str(existe)})
            dirs = {k: Path(v) for k, v in a["apr"].items()}
            self.assertEqual(dirs, {"geral": existe})
            out = aprovacoes.listar(dirs, Path(t) / "fila-inexistente", TZ, NOW)
            self.assertEqual([(x["fonte"], x["ficheiro"]) for x in out], [("geral", "a.md")])
            a = arranque({"APROVACOES_DIR": f"{t}/nao-existe"})
            self.assertEqual(aprovacoes.listar({k: Path(v) for k, v in a["apr"].items()}, Path(t), TZ, NOW), [])


class ParseExtraTest(Base):
    def test_segredo_no_resumo_longo_fica_mascarado(self):
        # CA8: o corte a 400 car. não deixa passar um segredo (máscara antes do corte)
        texto = "palavra " * 45 + SECRET + " fim " + "x " * 300
        self.put("a.md", f"---\nestado: pendente\ncriado: 2026-10-23\n---\n## O quê\n{texto}\n")
        [a] = self.listar()
        self.assertLessEqual(len(a["resumo"]), 400)
        self.assertNotIn(SECRET, a["resumo"])
        self.assertNotIn(SECRET[:12], a["resumo"])

    def test_ficheiro_com_ponto_ponto(self):
        # CA11/CA15: nome que casa [\w.-]+\.md mas contém «..» é recusado
        self.put("x..md", pedido())
        self.assertEqual(aprovacoes.corpo(self.dirs, "marketing", "x..md"), (400, {"error": "pedido inválido"}))
        d = {"fonte": "marketing", "ficheiro": "x..md", "id": "*", "accao": "aprovar", "sha256": "0" * 64}
        self.assertEqual(aprovacoes.validar(self.dirs, d), "ficheiro inválido")
        self.assertEqual(aprovacoes.corpo(self.dirs, "marketing", None), (400, {"error": "pedido inválido"}))


class EscritaAtomicaTest(Base):
    def setUp(self):
        super().setUp()
        self.p = self.put("a.md", pedido(decisoes=["- [P1] x"]))
        self.d = {"fonte": "marketing", "ficheiro": "a.md", "id": "P1", "accao": "aprovar", "nota": "",
                  "sha256": sha(self.p)}

    def escrever(self):
        return aprovacoes.escrever(self.dirs, self.fila, self.d, "203.0.113.7", "UA", TZ, NOW)

    def test_temporario_com_ponto_na_mesma_pasta_e_os_replace(self):
        # CA13: mkstemp(prefix=".", dir=fila) → os.replace para o nome final
        real, chamadas = os.replace, []

        def espia(src, dst):
            chamadas.append((Path(src), Path(dst)))
            self.assertTrue(Path(src).exists())
            real(src, dst)

        with mock.patch.object(aprovacoes.os, "replace", espia):
            self.assertEqual(self.escrever(), (202, {"ok": True}))
        [(src, dst)] = chamadas
        self.assertEqual((src.parent, dst.parent), (self.fila, self.fila))
        self.assertTrue(src.name.startswith("."))
        self.assertRegex(dst.name, NOME_FILA)
        self.assertEqual(os.listdir(self.fila), [dst.name])
        doc = json.loads(dst.read_text())
        self.assertEqual(doc["criado"], datetime.fromtimestamp(NOW, TZ).isoformat(timespec="seconds"))
        self.assertTrue(dst.name.startswith(datetime.fromtimestamp(NOW, ZoneInfo("Europe/Amsterdam"))
                                            .strftime("%Y%m%dT%H%M%S")))

    def test_falha_no_replace_da_503_e_nao_deixa_temporario(self):
        # CA13/CA17: erro a meio → 503 e a pasta da fila fica vazia
        with mock.patch.object(aprovacoes.os, "replace", side_effect=OSError("disco cheio")):
            self.assertEqual(self.escrever(), (503, {"error": "fila indisponível"}))
        self.assertEqual(os.listdir(self.fila), [])
        self.assertEqual(sha(self.p), self.d["sha256"])

    def test_fila_e_um_ficheiro_da_503(self):
        self.fila.rmdir()
        self.fila.write_text("não sou pasta")
        self.assertEqual(self.escrever(), (503, {"error": "fila indisponível"}))
        self.assertEqual(self.fila.read_text(), "não sou pasta")


class DeployDocsTest(unittest.TestCase):
    def test_readme(self):
        # CA28: formato, JSON da fila, códigos de erro e o que o Office nunca faz
        r = (ROOT / "README.md").read_text()
        sec = r[r.index("## Aprovações"):]
        fim = sec.find("\n## ", sec.index("nunca faz"))   # a secção tem «## …» dentro do bloco de exemplo
        sec = sec if fim < 0 else sec[:fim]
        for s in ("estado:", "## Decisões", "- [P1]", "→ aprovado (", '"v": 1', '"sha256"', '"ip"', '"ua"',
                  "400", "403", "409", "413", "503", "nunca faz", "não edita", "não aplica", "não apaga"):
            with self.subTest(s=s):
                self.assertIn(s, sec)


if __name__ == "__main__":
    unittest.main()
