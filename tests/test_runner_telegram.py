"""Runner: avisos opcionais por Telegram (AC-c8) contra um servidor HTTP falso em 127.0.0.1."""
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner, officecfg


@pytest.fixture
def telegram(monkeypatch):
    recebidos = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            corpo = self.rfile.read(int(self.headers["Content-Length"]))
            recebidos.append((self.path, json.loads(corpo)))
            self.send_response(500 if "falha" in self.path else 200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", recebidos
    srv.shutdown()
    srv.server_close()


def test_com_token_e_chat_envia(monkeypatch, telegram):
    url, recebidos = telegram
    monkeypatch.setenv("OFFICE_TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("OFFICE_TELEGRAM_CHAT", "42")
    monkeypatch.setenv("OFFICE_TELEGRAM_API", url)
    lancador.avisar("olá")
    assert recebidos == [("/bot123:abc/sendMessage", {"chat_id": "42", "text": "olá"})]


@pytest.mark.parametrize("envs", [{}, {"OFFICE_TELEGRAM_TOKEN": "t"}, {"OFFICE_TELEGRAM_CHAT": "42"}])
def test_sem_os_dois_nao_faz_nada(monkeypatch, telegram, envs):
    url, recebidos = telegram
    for k in ("OFFICE_TELEGRAM_TOKEN", "OFFICE_TELEGRAM_CHAT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in envs.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("OFFICE_TELEGRAM_API", url)
    lancador.avisar("olá")
    assert recebidos == []


def test_erros_sao_engolidos(monkeypatch, telegram):
    url, recebidos = telegram
    monkeypatch.setenv("OFFICE_TELEGRAM_TOKEN", "falha")
    monkeypatch.setenv("OFFICE_TELEGRAM_CHAT", "42")
    monkeypatch.setenv("OFFICE_TELEGRAM_API", url)
    lancador.avisar("x")   # HTTP 500
    monkeypatch.setenv("OFFICE_TELEGRAM_API", "http://127.0.0.1:1")
    lancador.avisar("x")   # ligação recusada
    assert len(recebidos) == 1


def test_avisa_fim_falha_e_tecto(tmp_path, monkeypatch, telegram):
    url, recebidos = telegram
    c.ambiente(monkeypatch, tmp_path)
    monkeypatch.setenv("OFFICE_TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("OFFICE_TELEGRAM_CHAT", "42")
    monkeypatch.setenv("OFFICE_TELEGRAM_API", url)
    cfg, path = c.config(tmp_path, tecto=2, paralelo=4)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 1)
    c.pedido(cfg, stem="20261106T100000-aaaaaaaa", slug="um-botao")
    c.pedido(cfg, stem="20261106T110000-bbbbbbbb", slug="dois-botoes")
    c.pedido(cfg, stem="20261106T120000-cccccccc", slug="tres-botoes")
    office_runner.uma_vez(cfg, str(path))
    um, dois = c.feito(cfg, "20261106T100000-aaaaaaaa")["task_id"], c.feito(cfg, "20261106T110000-bbbbbbbb")["task_id"]
    assert c.feito(cfg, "20261106T120000-cccccccc") == {"erro": "tecto diário"}
    lancador.correr(cfg, um)
    monkeypatch.setenv("FAKE_CLAUDE_FALHA", "1")
    lancador.correr(cfg, dois)
    textos = [d["text"] for _, d in recebidos]
    assert len(textos) == 3
    assert "tecto diário" in textos[0]
    assert um in textos[1] and "concluída" in textos[1]
    assert dois in textos[2] and "erro" in textos[2]


# ---------------------------------------------------------------- C4: aviso quando o office.yaml muda

MUDOU = "Squad Office: o office.yaml mudou. Se não foste tu, revê-o já."


def _com_telegram(tmp_path, monkeypatch, url):
    c.ambiente(monkeypatch, tmp_path)
    monkeypatch.setenv("OFFICE_TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("OFFICE_TELEGRAM_CHAT", "42")
    monkeypatch.setenv("OFFICE_TELEGRAM_API", url)
    return c.config(tmp_path)


def test_office_yaml_mudado_avisa_uma_vez(tmp_path, monkeypatch, telegram):
    url, recebidos = telegram
    cfg, path = _com_telegram(tmp_path, monkeypatch, url)
    marca = officecfg.caminho(cfg, "runner") / "office.yaml.sha256"
    antes = hashlib.sha256(path.read_bytes()).hexdigest()
    assert office_runner.uma_vez(cfg, str(path)) == []           # 1.ª passagem: só grava
    assert marca.read_text(encoding="ascii") == antes + "\n" and recebidos == []
    assert office_runner.uma_vez(cfg, str(path)) == []           # sem mudança: nada
    path.write_text(path.read_text(encoding="utf-8").replace("nome: Ana", "nome: Outra"), encoding="utf-8")
    depois = hashlib.sha256(path.read_bytes()).hexdigest()
    linhas = office_runner.uma_vez(cfg, str(path))
    assert linhas == [f"office.yaml mudou (era {antes[:8]}, é {depois[:8]}); se não foste tu, revê-o já"]
    assert re.fullmatch(r"office\.yaml mudou \(era [0-9a-f]{8}, é [0-9a-f]{8}\); se não foste tu, revê-o já",
                        linhas[0])
    assert [d["text"] for _, d in recebidos] == [MUDOU]
    assert marca.read_text(encoding="ascii") == depois + "\n"
    assert office_runner.uma_vez(cfg, str(path)) == [] and len(recebidos) == 1   # uma vez por mudança


def test_office_yaml_sem_telegram_so_journal(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    office_runner.uma_vez(cfg, str(path))
    path.write_text(path.read_text(encoding="utf-8") + "# mudado\n", encoding="utf-8")
    (linha,) = office_runner.uma_vez(cfg, str(path))
    assert linha.startswith("office.yaml mudou (era ")


def test_office_yaml_erros_de_io_nao_derrubam(tmp_path, monkeypatch, telegram):
    url, recebidos = telegram
    cfg, path = _com_telegram(tmp_path, monkeypatch, url)
    marca = officecfg.caminho(cfg, "runner") / "office.yaml.sha256"
    marca.mkdir()                                                 # destino ocupado por uma pasta
    assert office_runner.uma_vez(cfg, str(path)) == []
    assert office_runner.vigiar_config(cfg, str(tmp_path / "nao-existe.yaml")) == []
    assert recebidos == []
