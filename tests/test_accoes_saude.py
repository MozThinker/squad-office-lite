"""servico_accoes/servico.py (ADR-022): `saude.json` no fim de cada passagem completa e stderr só quando os avisos
mudam (`aviso:` dos novos, `resolvido:` dos que saíram), comparando com o `saude.json` anterior (CA-1, CA-4…CA-12,
CA-21). Mesmo mundo dos testes do serviço (`Ctx` de test_accoes_servico), `AGORA` fixo, sem root."""
import json
import os
import subprocess
import sys
from datetime import datetime

import pytest

from servico_accoes import colector as col
from servico_accoes import executor as ex
from servico_accoes import servico
from tests.accoes_falsos import AGORA, mid
from tests.test_accoes_colector import mensagem
from tests.test_accoes_servico import SERVICO, Ctx

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="como root o arranque faria chown e largaria privilégios")

COL = "registo de squads ilegível; nada colhido nesta passagem"
EXE = "registo de squads ilegível; nada executado nesta passagem"


@pytest.fixture
def x(tmp_path, monkeypatch):
    c = Ctx(tmp_path, monkeypatch)
    yield c
    c.fechar()


def saude(x) -> dict:
    return json.loads((x.m.saude / servico.SAUDE).read_text(encoding="utf-8"))


def estragar(x) -> None:
    (x.m.agentes / "squads.json").write_text("{", encoding="utf-8")


def err(x) -> str:
    rc, _, e = x.main("passagem")
    assert rc == 0
    return e


def test_sem_squads_stderr_vazio_e_saude_vazio(x):   # CA-1, CA-21
    (x.m.agentes / "squads.json").unlink()
    for _ in range(3):
        assert err(x) == ""
        assert saude(x)["avisos"] == []


def test_aviso_so_na_primeira_passagem(x):   # CA-4
    estragar(x)
    assert err(x) == f"aviso: {COL}\naviso: {EXE}\n"
    assert saude(x)["avisos"] == [COL, EXE]
    assert err(x) == ""
    assert err(x) == ""
    assert saude(x)["avisos"] == [COL, EXE]


def test_resolvido_uma_vez(x):   # CA-5
    guardado = (x.m.agentes / "squads.json").read_text()
    estragar(x)
    err(x)
    (x.m.agentes / "squads.json").write_text(guardado)
    assert err(x) == f"resolvido: {COL}\nresolvido: {EXE}\n"
    assert saude(x)["avisos"] == []
    assert err(x) == ""


def test_texto_novo_conta_como_novo(x, monkeypatch):   # CA-5
    monkeypatch.setattr(col, "passagem", lambda *a: ["squad loja: OSError"])
    assert err(x) == "aviso: squad loja: OSError\n"
    monkeypatch.setattr(col, "passagem", lambda *a: ["squad loja: TimeoutError"])
    assert err(x) == "aviso: squad loja: TimeoutError\nresolvido: squad loja: OSError\n"


def test_compara_com_saude_anterior_entre_processos(x):   # CA-6: no VPS cada passagem é um processo novo
    estragar(x)

    def correr() -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(SERVICO), "passagem"], capture_output=True, text=True,
                              env={**os.environ, **x.env}, timeout=60, check=False)
    p1, p2 = correr(), correr()
    assert (p1.returncode, p1.stderr) == (0, f"aviso: {COL}\naviso: {EXE}\n")
    assert (p2.returncode, p2.stderr) == (0, "")


@pytest.mark.parametrize("conteudo", [b"{", b'{"v": 2, "avisos": []}', b'{"v": 1, "avisos": "x"}',
                                      b'{"v": 1, "avisos": [1]}', b"[]", b" " * (servico.SAUDE_MAX + 1)])
def test_saude_anterior_invalido_ou_symlink(x, conteudo):   # CA-6: inválido = «sem avisos antes»
    estragar(x)
    (x.m.saude / servico.SAUDE).write_bytes(conteudo)
    assert servico.avisos_anteriores(x.m.r) == []
    assert err(x) == f"aviso: {COL}\naviso: {EXE}\n"
    alvo = x.m.dir / "alvo.json"
    alvo.write_text(json.dumps({"v": 1, "em": "2026-11-18T10:00:00+00:00", "avisos": [COL, EXE]}))
    (x.m.saude / servico.SAUDE).unlink()
    (x.m.saude / servico.SAUDE).symlink_to(alvo)
    assert servico.avisos_anteriores(x.m.r) == []   # não segue o symlink
    assert err(x) == f"aviso: {COL}\naviso: {EXE}\n"


def test_erro_no_colector_vira_aviso(x, monkeypatch):   # CA-7
    def rebenta(*a):
        raise RuntimeError("não sai: /caminho/secreto")
    original = col.passagem
    monkeypatch.setattr(col, "passagem", rebenta)
    assert err(x) == "aviso: erro no colector: RuntimeError\n"
    assert saude(x)["avisos"] == ["erro no colector: RuntimeError"]
    assert err(x) == ""
    monkeypatch.setattr(col, "passagem", original)
    assert err(x) == "resolvido: erro no colector: RuntimeError\n"


def test_formato_modo_e_em(x):   # CA-8
    assert err(x) == ""
    p = x.m.saude / servico.SAUDE
    assert oct(p.stat().st_mode & 0o777) == "0o644"
    d = saude(x)
    assert list(d) == ["v", "em", "avisos"]
    assert (d["v"], d["em"], d["avisos"]) == (1, "2026-11-18T10:00:07+00:00", [])
    assert datetime.fromisoformat(d["em"]) == AGORA
    assert not (x.m.saude / f".{servico.SAUDE}.tmp").exists()


def test_symlink_substituido_alvo_intacto(x):   # CA-8
    alvo = x.m.dir / "alvo.txt"
    alvo.write_text("não mexer")
    os.chmod(alvo, 0o666)
    (x.m.saude / servico.SAUDE).symlink_to(alvo)
    assert err(x) == ""
    assert not (x.m.saude / servico.SAUDE).is_symlink()
    assert saude(x)["avisos"] == []
    assert alvo.read_text() == "não mexer"


def test_tecto_10_e_200_sem_repetidos_e_controlo(x, monkeypatch):   # CA-9
    assert servico.normalizar([]) == []
    assert servico.normalizar(["a\nb\x00c\x7fd\x9fe f", "a b c d e f", "b"]) == ["a b c d e f", "b"]
    assert servico.normalizar(["x" * 201, "x" * 200]) == ["x" * 200]
    muitos = [f"aviso {i}" for i in range(15)]
    assert servico.normalizar(muitos[:3] + muitos) == muitos[:10]
    # pior caso em bytes: 10 × 200 caracteres de 4 bytes em UTF-8 cabe nos 8 KB que o servidor lê
    grandes = [chr(0x1D11E) * 199 + str(i) for i in range(12)]
    monkeypatch.setattr(col, "passagem", lambda *a: grandes[:6])
    monkeypatch.setattr(ex, "passagem", lambda *a: ['"\\' * 150] + grandes[6:])
    err(x)
    raw = (x.m.saude / servico.SAUDE).read_bytes()
    assert len(raw) <= servico.SAUDE_MAX
    d = saude(x)
    assert d["avisos"] == grandes[:6] + ['"\\' * 100] + grandes[6:9]
    assert servico.avisos_anteriores(x.m.r) == d["avisos"]


def test_lock_ocupado_nao_grava_nem_resolve(x, monkeypatch):   # CA-10
    guardado = (x.m.agentes / "squads.json").read_text()
    estragar(x)
    err(x)
    antes = (x.m.saude / servico.SAUDE).read_bytes()
    (x.m.agentes / "squads.json").write_text(guardado)
    fd = ex.trancar(x.m.r, bloquear=True)
    try:
        assert err(x) == ""   # o colector já não avisa, mas sem resolvido: (o executor não correu)
        assert (x.m.saude / servico.SAUDE).read_bytes() == antes
        monkeypatch.setattr(col, "passagem", lambda *a: ["squad loja: OSError"])
        assert err(x) == ""   # um aviso novo só sai na passagem completa seguinte, uma vez
        assert (x.m.saude / servico.SAUDE).read_bytes() == antes
    finally:
        os.close(fd)
    saida = err(x)   # passagem completa: o aviso novo sai uma vez e os antigos resolvem-se
    assert saida.count("aviso: squad loja: OSError\n") == 1 and "resolvido: registo de squads ilegível" in saida
    assert err(x) == ""   # e não se repete
    antes = (x.m.saude / servico.SAUDE).read_bytes()
    monkeypatch.setattr(col, "passagem", lambda *a: None)   # o lock do colector ocupado
    assert err(x) == ""
    assert (x.m.saude / servico.SAUDE).read_bytes() == antes


def test_sem_sentinelas(x, monkeypatch):   # CA-11
    sentinelas = ["SENTINELA-SENHA", "sentinela-utilizador@padaria.test", "sentinela-servidor.test",
                  "sentinela-endereco@padaria.test", "999:SENTINELA-TOKEN", "-1009876543210", "sentinela-remetente",
                  "SENTINELA-ASSUNTO", "SENTINELA-CORPO"]
    email = x.m.email(x.smtp.porta, "sentinela-endereco@padaria.test")
    email["authserv_id"] = "sentinela-servidor.test"   # o host tem de ser 127.0.0.1 (tls false só em localhost)
    x.m.config({"loja": {"accoes": ["aviso_dono"], "email": email}},
               telegram={"url": x.tg.url, "chat_id": "-1009876543210"})
    x.m.segredos("LOJA_EMAIL_UTILIZADOR=sentinela-utilizador@padaria.test\nLOJA_EMAIL_SENHA=SENTINELA-SENHA\n"
                 "TELEGRAM_TOKEN=999:SENTINELA-TOKEN\n")
    x.m.registar(mid(2), "loja", "sentinela-remetente@exemplo.test")
    vistos = []

    def passar():
        rc, out, e = x.main("passagem")
        assert rc == 0, e
        vistos.append(out + e + (x.m.saude / servico.SAUDE).read_text(encoding="utf-8"))
    passar()   # a 1.ª passagem só guarda o UIDNEXT
    x.caixa.entregar(mensagem("SENTINELA-CORPO", de="Sentinela <sentinela-remetente@exemplo.test>",
                              assunto="SENTINELA-ASSUNTO"))
    x.caixa.entregar(mensagem("sem remetente", de="SENTINELA-ASSUNTO sem endereço"))
    passar()
    assert any(p.name.endswith(".txt") for p in (x.m.entrada / "loja/mensagens").iterdir())   # colheu

    def falha(host, porta):
        raise OSError(f"ligação a {host} recusada para sentinela-utilizador@padaria.test SENTINELA-SENHA")
    monkeypatch.setattr(ex, "IMAP", falha)
    passar()
    assert saude(x)["avisos"] == ["squad loja: OSError"]   # há avisos, só com textos fixos
    for texto in vistos:
        for s in sentinelas:
            assert s not in texto, (s, texto)


def test_gravacao_falhada_nao_falha_a_passagem(x):   # CA-12
    os.chmod(x.m.saude, 0o555)
    try:
        rc, _, e = x.main("passagem")
    finally:
        os.chmod(x.m.saude, 0o755)
    assert (rc, e) == (0, "aviso: saude.json não gravado (PermissionError)\n")
    assert str(x.m.saude) not in e
    assert not (x.m.saude / servico.SAUDE).exists()


def test_saude_json_fora_do_accoes_entrada(x):   # ADR-022: o office monta só o accoes-saude
    estragar(x)
    err(x)
    assert saude(x)["avisos"] == [COL, EXE]
    assert oct((x.m.saude / servico.SAUDE).stat().st_mode & 0o777) == "0o644"
    assert not (x.m.entrada / servico.SAUDE).exists()


def test_sem_pasta_da_saude_como_gravacao_falhada(x):   # CA-12: a pasta em falta não pára a passagem
    estragar(x)
    os.rmdir(x.m.saude)
    assert err(x) == f"aviso: {COL}\naviso: {EXE}\naviso: saude.json não gravado (FileNotFoundError)\n"
    assert not x.m.saude.exists() and not (x.m.entrada / servico.SAUDE).exists()


def test_ciclo_grava_em_cada_volta(x, monkeypatch):   # CA-8: cada volta do ciclo
    estragar(x)
    gravadas = []
    original = servico.gravar_saude
    monkeypatch.setattr(servico, "gravar_saude", lambda *a: (gravadas.append(a[2]), original(*a)))
    rc, _, e = x.main("ciclo", "--intervalo", "0", "--passagens", "3")
    assert (rc, e) == (0, f"aviso: {COL}\naviso: {EXE}\n")
    assert gravadas == [[COL, EXE]] * 3
    assert saude(x)["avisos"] == [COL, EXE]
