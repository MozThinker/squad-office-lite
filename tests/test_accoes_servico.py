"""servico_accoes/servico.py (ADR-021): arranque recusado com credenciais ou configuração largas (CA-18b), perfil só
`lite`, `escrever-config` e `escrever-segredos` sem imprimir valores (CA-31), `verificar` com os serviços falsos,
`anular`/`desfazer`, e ponta a ponta: pedido do runner → aprovação pelo aplicador → passagem do serviço. Sem root:
o `arranque.preparar` não faz nada."""
import hashlib
import io
import json
import os
import pty
import select
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

import accoes
from servico_accoes import executor as ex
from servico_accoes import servico
from tests.accoes_falsos import AGORA, CaixaFalsa, Mundo, SMTPFalso, TelegramFalso, bloco, mid
from tests.test_runner_comum import decisoes

SERVICO = Path(servico.__file__).resolve()
pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="como root o arranque faria chown e largaria privilégios")


class Ctx:
    def __init__(self, tmp, monkeypatch):
        self.m = m = Mundo(tmp)
        self.smtp, self.tg, self.caixa = SMTPFalso(), TelegramFalso(), CaixaFalsa()
        m.registar(mid(1), "loja", "cliente@exemplo.test")
        m.config({"loja": {"accoes": list(accoes.TIPOS), "email": m.email(self.smtp.porta), "folhas": m.folha()}},
                 telegram={"url": self.tg.url, "chat_id": "42"})
        m.segredos("LOJA_EMAIL_UTILIZADOR=geral@padaria.test\nLOJA_EMAIL_SENHA=senha-falsa-123\n"
                   "TELEGRAM_TOKEN=123:token-falso\n")
        m.vista()
        self.env = {"ACCOES_DIR": str(m.r.dir), "ACCOES_SEGREDOS": str(m.r.segredos), "ACCOES_ENTRADA": str(m.entrada),
                    "ACCOES_DECISOES": str(m.decisoes), "ACCOES_AGENTES": str(m.agentes), "ACCOES_SAUDE": str(m.saude),
                    "ACCOES_PERFIL": "lite"}
        for k, v in self.env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(ex, "IMAP", self.caixa)
        monkeypatch.setattr(servico, "_agora", lambda: AGORA)
        self.mp = monkeypatch

    def fechar(self):
        self.smtp.fechar()
        self.tg.fechar()

    def main(self, *args, stdin: bytes = b"") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        self.mp.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin)))
        self.mp.setattr(sys, "stdout", out)
        self.mp.setattr(sys, "stderr", err)
        umask = os.umask(0o022)   # o main põe o umask 077 do serviço no processo: repõe-se para os testes seguintes
        os.umask(umask)
        try:
            rc = servico.main(list(args))
        finally:
            os.umask(umask)
        return rc, out.getvalue(), err.getvalue()

    def livro(self) -> list[dict]:
        return [json.loads(x) for p in sorted(self.m.r.dir.glob("*.jsonl")) for x in p.read_text().splitlines()]


@pytest.fixture
def x(tmp_path, monkeypatch):
    c = Ctx(tmp_path, monkeypatch)
    yield c
    c.fechar()


# --- arranque (CA-18b) -------------------------------------------------------------------------------------------------

def test_credenciais_largas_ou_symlink_nao_arranca(x):
    nome = x.m.pedir(bloco(tipo="aviso_dono", texto="x"))
    x.m.aprovar(nome)
    os.chmod(x.m.r.env, 0o640)
    rc, _, err = x.main("passagem")
    assert rc == 1
    assert "accoes não arranca: credenciais: legíveis ou graváveis por grupo/outros (chmod 600)" in err
    assert (len(x.tg.chamadas), x.livro()) == (0, [])
    os.chmod(x.m.r.env, 0o600)
    real = x.m.r.dir / "real.env"
    x.m.r.env.rename(real)
    x.m.r.env.symlink_to(real)
    assert x.main("verificar")[0] == 1
    x.m.r.env.unlink()
    real.rename(x.m.r.env)
    os.chmod(x.m.r.config, 0o666)
    rc, _, err = x.main("verificar")
    assert (rc, "configuração: gravável" in err) == (1, True)
    os.chmod(x.m.r.config, 0o644)
    x.mp.setattr(os, "geteuid", lambda: os.getuid() + 1)
    rc, _, err = x.main("passagem")
    assert (rc, "o ficheiro não é do utilizador que corre" in err) == (1, True)
    assert len(x.tg.chamadas) == 0


def test_so_perfil_lite(x):
    x.mp.setenv("ACCOES_PERFIL", "office")
    rc, _, err = x.main("verificar")
    assert (rc, err.strip()) == (2, "accoes não arranca: ACCOES_PERFIL desconhecido (só lite)")
    x.mp.setenv("ACCOES_PERFIL", "")
    assert x.main("verificar")[0] == 0   # vazio = omissão lite


# --- escrever-config / escrever-segredos (CA-31) ----------------------------------------------------------------------

def test_escrever_config(x):
    antes = x.m.r.config.read_bytes()
    mau = json.dumps({"v": 1, "squads": {"loja": {"email": {"endereco": "segredo@x"}}}}).encode()
    rc, out, err = x.main("escrever-config", stdin=mau)
    assert rc == 1 and out == ""
    assert "accoes.json não gravado: squads.loja.email" in err and "segredo@x" not in err
    assert x.m.r.config.read_bytes() == antes
    for lixo in (b"{", b'{"v": 1}', b"x" * (accoes.CONFIG_MAX + 1)):
        assert x.main("escrever-config", stdin=lixo)[0] == 1
    novo = json.dumps({"v": 1, "squads": {"loja": {"accoes": ["aviso_dono"]}}}).encode()
    os.chmod(x.m.r.config, 0o666)   # o arranque recusaria; escrever-config é o remédio
    rc, out, _ = x.main("escrever-config", stdin=novo)
    assert (rc, out.strip()) == (0, "ok: accoes.json gravado")
    assert x.m.r.config.read_bytes() == novo
    assert oct(x.m.r.config.stat().st_mode & 0o777) == "0o644"


def test_escrever_segredos(x):
    antes = x.m.r.env.read_bytes()
    rc, out, err = x.main("escrever-segredos", stdin=b"TELEGRAM_TOKEN=ok\nOUTRA=valor-secreto\n")
    assert (rc, out) == (1, "")
    assert "accoes.env não gravado: credenciais: linha 2: chave desconhecida" in err
    assert "valor-secreto" not in err
    assert x.main("escrever-segredos", stdin=b"LOJA_EMAIL_SENHA=a\x01b\n")[0] == 1
    assert x.m.r.env.read_bytes() == antes
    os.chmod(x.m.r.env, 0o644)
    novo = b"# caixa\nLOJA_EMAIL_UTILIZADOR=u@padaria.test\nLOJA_EMAIL_SENHA=nova-senha-987\nTELEGRAM_TOKEN=9:z\n"
    rc, out, err = x.main("escrever-segredos", stdin=novo)
    assert rc == 0
    assert out.strip() == "ok: accoes.env gravado (3 chaves: LOJA_EMAIL_SENHA, LOJA_EMAIL_UTILIZADOR, TELEGRAM_TOKEN)"
    assert "nova-senha" not in out + err and "u@padaria" not in out + err
    assert x.m.r.env.read_bytes() == novo
    assert oct(x.m.r.env.stat().st_mode & 0o777) == "0o600"
    assert x.main("verificar")[0] in (0, 1)   # arranca (as permissões estão certas)


def test_escrever_segredos_sem_eco_no_terminal(x):   # CA-31: num terminal o que se cola não aparece
    mestre, escravo = pty.openpty()
    p = subprocess.Popen([sys.executable, "-I", str(SERVICO), "escrever-segredos"], stdin=escravo,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, **x.env})
    os.close(escravo)
    try:
        lido, fim = b"", time.monotonic() + 20
        while b"Ctrl-D" not in lido and time.monotonic() < fim:   # o aviso só sai depois de desligar o eco
            if select.select([p.stderr], [], [], 0.5)[0]:
                lido += os.read(p.stderr.fileno(), 4096)
        assert b"Ctrl-D" in lido, lido
        os.write(mestre, b"TELEGRAM_TOKEN=segredo-do-pty\n\x04")
        out, err = p.communicate(timeout=20)
        eco = b""
        while select.select([mestre], [], [], 0.2)[0]:
            try:
                eco += os.read(mestre, 4096)
            except OSError:
                break
    finally:
        os.close(mestre)
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, err
    assert b"segredo-do-pty" not in eco + out + err
    assert x.m.r.env.read_bytes() == b"TELEGRAM_TOKEN=segredo-do-pty\n"


# --- verificar, anular, desfazer ---------------------------------------------------------------------------------------

def test_verificar_com_os_falsos(x):
    rc, out, _ = x.main("verificar")
    assert (rc, out.splitlines()) == (0, ["✓ permissões, configuração e credenciais", "✓ loja: IMAP", "✓ loja: SMTP",
                                          "✓ telegram"])
    assert x.tg.chamadas[0]["caminho"] == "/bot123:token-falso/getMe"
    assert "senha" not in out and "token" not in out
    x.tg.estado = 401
    x.m.segredos("TELEGRAM_TOKEN=1:x\n")
    rc, out, _ = x.main("verificar")
    assert rc == 1
    assert out.splitlines()[1:] == ["✗ loja: faltam LOJA_EMAIL_UTILIZADOR/LOJA_EMAIL_SENHA no accoes.env",
                                    "✗ telegram: HTTPError 401"]


def test_verificar_como_script_isolado(x):   # o wrapper da imagem corre `python3 -I -u servico.py`
    x.m.config({"loja": {"folhas": x.m.folha()}})
    r = subprocess.run([sys.executable, "-I", "-u", str(SERVICO), "verificar"], capture_output=True, text=True,
                       env={**os.environ, **x.env}, timeout=60, check=False)
    assert (r.returncode, r.stdout.strip()) == (0, "✓ permissões, configuração e credenciais"), r.stderr


def test_anular_e_desfazer_pelo_comando(x):
    nome = x.m.pedir(bloco(tipo="email_responder", origem=mid(1), assunto="a", corpo="b")
                     + bloco(tipo="folha_linha", folha="leads", valores={"nome": "Ana"}))
    x.m.aprovar(nome, "A1", "A2")
    assert x.main("passagem")[0] == 0
    rc, out, _ = x.main("anular", x.m.sha(nome))
    assert (rc, out.strip()) == (0, "anulada: não sai na próxima passagem")
    rc, out, _ = x.main("desfazer", x.m.sha(nome, "A2"))
    assert (rc, out.strip()) == (0, "desfeita")
    assert x.main("anular", "nao-e-sha")[0] == 1
    x.mp.setattr(servico, "_agora", lambda: AGORA + timedelta(minutes=3))
    x.main("passagem")
    assert x.smtp.ligacoes == 0
    assert [e["estado"] for e in x.livro()] == ["a_enviar", "executada", "desfeita", "anulada"]


# --- ponta a ponta -----------------------------------------------------------------------------------------------------

def test_ponta_a_ponta_com_o_aplicador(x):
    """Gancho do runner → pedido nas Aprovações → decisão (JSON v1 aplicado por runner/decisoes.py) → serviço."""
    nome = x.m.pedir("Resumo.\n" + bloco(tipo="aviso_dono", texto="Há uma reclamação.")
                     + bloco(tipo="folha_linha", folha="leads", valores={"nome": "Ana"}))
    fila = x.m.decisoes.parent / "decisoes-fila"
    raw = (x.m.decisoes / nome).read_bytes()
    doc = {"v": 1, "criado": AGORA.isoformat(), "fonte": "geral", "ficheiro": nome, "id": "A1", "accao": "aprovar",
           "nota": "", "sha256": hashlib.sha256(raw).hexdigest(), "ip": "", "ua": ""}
    (fila / "d1.json").write_text(json.dumps(doc), "utf-8")
    assert decisoes.processar(fila, x.m.decisoes, x.m.dir / "hist.json", "Ana", AGORA) == [f"aprovado: {nome} (A1)"]
    assert x.main("passagem")[0] == 0
    assert [c["corpo"]["text"] for c in x.tg.chamadas] == ["Há uma reclamação."]
    assert not (x.m.r.dir / "folhas/leads.csv").exists()   # A2 não foi aprovada
    [e] = x.livro()
    assert (e["linha"], e["estado"], e["aprovado"]) == ("A1", "executada", AGORA.date().isoformat())
    assert x.main("passagem")[0] == 0
    assert len(x.tg.chamadas) == 1


def test_ciclo_com_passagens(x):
    rc, _, err = x.main("ciclo", "--intervalo", "0", "--passagens", "2")
    assert (rc, err) == (0, "")
    assert (x.m.entrada / "loja/vista.json").exists()
