"""servico_accoes/colector.py (ADR-021): mensagens da caixa para `<accoes-entrada>/<squad>/mensagens/` e para o registo
do colector, com o remetente do From (CA-12), a vista sem segredos para o runner e a purga dos 90 dias (CA-32). Caixa
IMAP falsa, pastas temporárias, `AGORA` fixo."""
import fcntl
import json
import os
from datetime import timedelta
from email.message import EmailMessage

import pytest

import accoes
from servico_accoes import colector as col
from servico_accoes import executor as ex
from tests.accoes_falsos import AGORA, CaixaFalsa, Mundo
from tests.test_accoes_executor import CAUSAS, _estragar_registo

AR_BOM = "mx.padaria.test; dkim=pass header.d=exemplo.test; dmarc=pass header.from=exemplo.test"


def mensagem(corpo="Olá, abrem ao sábado?", de="Cliente <cliente@exemplo.test>", html=None, anexo=None, **cab) -> bytes:
    m = EmailMessage()
    m["From"] = de
    m["To"] = "geral@padaria.test"
    m["Subject"] = cab.get("assunto", "Horário")
    m["Date"] = "Wed, 18 Nov 2026 09:59:00 +0000"
    m["Message-ID"] = "<abc@exemplo.test>"
    if "reply_to" in cab:
        m["Reply-To"] = cab["reply_to"]
    for ar in cab.get("ar", [AR_BOM]):
        m["Authentication-Results"] = ar
    if html is not None:
        m.set_content(html, subtype="html")
    else:
        m.set_content(corpo)
    if anexo:
        m.add_attachment(anexo.encode(), maintype="text", subtype="plain", filename="anexo.txt")
    return m.as_bytes()


class Ctx:
    def __init__(self, tmp, monkeypatch):
        self.m = m = Mundo(tmp, {"loja": "atendimento", "venda": "comercial", "dev2": "desenvolvimento",
                                 "sem": "atendimento"})
        self.caixa = CaixaFalsa()
        m.config({"loja": {"email": m.email(), "dominios": ["padaria.test"], "folhas": m.folha()},
                  "venda": {"email": m.email(endereco="vendas@padaria.test")},
                  "dev2": {"email": m.email(endereco="dev@padaria.test")},
                  "fora": {"email": m.email(endereco="fora@padaria.test")},
                  "sem": {"accoes": ["aviso_dono"]}})
        m.segredos("".join(f"{k}_EMAIL_UTILIZADOR={k.lower()}\n{k}_EMAIL_SENHA=x\n"
                           for k in ("LOJA", "VENDA", "DEV2", "FORA", "SEM")))
        monkeypatch.setattr(ex, "IMAP", self.caixa)

    def passar(self, agora=AGORA) -> list[str]:
        return col.passagem(self.m.r, agora)

    def txts(self, sid="loja") -> dict[str, str]:
        pasta = self.m.entrada / sid / "mensagens"
        return {p.stem: p.read_text(encoding="utf-8") for p in sorted(pasta.glob("*.txt"))}

    def registo(self, mid) -> dict:
        return json.loads((self.m.r.dir / "mensagens" / f"{mid}.json").read_text())

    def vista(self, sid="loja") -> dict:
        return json.loads((self.m.entrada / sid / "vista.json").read_text())


@pytest.fixture
def x(tmp_path, monkeypatch):
    return Ctx(tmp_path, monkeypatch)


def test_comeca_no_uidnext_e_copia_as_novas(x):   # CA-32, CA-12 (Reply-To ignorado)
    x.caixa.entregar(mensagem("antiga"))
    assert x.passar() == []
    assert x.txts() == {}
    uid = x.caixa.entregar(mensagem(reply_to="ladrao@mal.test"))
    x.passar()
    [(mid, txt)] = x.txts().items()
    assert accoes.ORIGEM.fullmatch(mid)
    assert txt == (f"{mid}\nDe: cliente@exemplo.test\nAssunto: Horário\nData: Wed, 18 Nov 2026 09:59:00 +0000\n\n"
                   "Olá, abrem ao sábado?\n")
    r = x.registo(mid)
    assert (r["squad"], r["remetente"], r["message_id"], r["uidvalidity"], r["uid"], r["pasta"], r["autenticado"],
            r["recebido"]) == ("loja", "cliente@exemplo.test", "<abc@exemplo.test>", 7, uid, "INBOX", True,
                               AGORA.isoformat(timespec="seconds"))
    assert "ladrao" not in json.dumps(r) + txt
    assert accoes.mensagem(x.m.r.dir, mid, "loja")["remetente"] == "cliente@exemplo.test"
    assert accoes.mensagem(x.m.r.dir, mid, "venda") is None
    assert ("SELECT", "INBOX", True) in x.caixa.comandos
    assert ("FETCH", str(uid), "(BODY.PEEK[])") in x.caixa.comandos
    assert oct((x.m.entrada / "loja/mensagens" / f"{mid}.txt").stat().st_mode & 0o777) == "0o644"
    assert oct((x.m.r.dir / "mensagens" / f"{mid}.json").stat().st_mode & 0o777) == "0o600"
    assert oct((x.m.entrada / "loja/mensagens").stat().st_mode & 0o777) == "0o755"
    x.passar()   # nada repetido
    assert len(x.txts()) == 1


def test_vista_sem_segredos(x):   # o que o runner lê para validar (CA-12, CA-17, CA-22)
    x.passar()
    x.caixa.entregar(mensagem())
    x.passar()
    [mid] = x.txts()
    v = x.vista()
    assert v == {"v": 1, "ligadas": sorted(accoes.PERFIS["lite"]), "dominios": ["padaria.test"],
                 "folhas": {"leads": ["nome", "contacto", "nota"]}, "caixa": "geral@padaria.test",
                 "mensagens": {mid: {"remetente": "cliente@exemplo.test", "autenticada": True}}}
    assert accoes.validar_vista(v) == v
    assert x.vista("sem") == {"v": 1, "ligadas": ["aviso_dono"], "dominios": [], "folhas": {}, "caixa": None,
                              "mensagens": {}}
    venda = x.vista("venda")["mensagens"]   # a caixa falsa é a mesma: a venda colheu-a com outro id
    assert len(venda) == 1 and mid not in venda
    assert not (x.m.entrada / "dev2").exists() and not (x.m.entrada / "fora").exists()
    assert oct((x.m.entrada / "loja/vista.json").stat().st_mode & 0o777) == "0o644"
    texto = (x.m.entrada / "loja/vista.json").read_text()
    assert "senha" not in texto and "LOJA_" not in texto and "folhas/leads.csv" not in texto


def test_autenticado(x):   # CA-12: só o 1.º Authentication-Results, do nosso servidor, alinhado com o From
    x.passar()
    casos = {"dmarc": ["mx.padaria.test; dmarc=pass header.from=exemplo.test"],
             "dkim": ["mx.padaria.test 1; dkim=pass header.d=exemplo.test; dmarc=none"],
             "sub": ["mx.padaria.test; dkim=pass (bom) header.d=mail.exemplo.test"],
             "evil": ["mx.padaria.test; dkim=pass header.d=evil.test; dmarc=fail header.from=exemplo.test"],
             "arc": ["mx.padaria.test; arc=pass (as.1.exemplo.test=pass dmarc=pass header.from=exemplo.test)"],
             "outro": ["mal.test; dmarc=pass header.from=exemplo.test"],
             "nao": ["mx.padaria.test; dkim=fail; dmarc=fail"], "sem": [],
             "forjado": ["mx.padaria.test; dmarc=fail header.from=exemplo.test",
                         "mx.padaria.test; dmarc=pass header.from=exemplo.test"]}
    for k, ar in casos.items():
        x.caixa.entregar(mensagem(k, ar=ar))
    x.passar()
    por = {t.split("\n\n", 1)[1].strip(): x.registo(mid)["autenticado"] for mid, t in x.txts().items()}
    assert por == {"dmarc": True, "dkim": True, "sub": True, "evil": False, "arc": False, "outro": False,
                   "nao": False, "sem": False, "forjado": False}
    assert {v["autenticada"] for v in x.vista()["mensagens"].values()} == {True, False}


def test_sem_authserv_nunca_autenticado(x):
    c = json.loads(x.m.r.config.read_text())
    del c["squads"]["loja"]["email"]["authserv_id"]
    x.m.config(c["squads"])
    x.passar()
    x.caixa.entregar(mensagem("x"))
    x.passar()
    [mid] = x.txts()
    assert x.registo(mid)["autenticado"] is False


def test_html_anexo_tamanho_e_sem_remetente(x):
    x.passar()
    x.caixa.entregar(mensagem(html="<p>Olá <b>Ana</b></p><script>alert(1)</script><style>x{}</style>"))
    x.caixa.entregar(mensagem("corpo", anexo="SEGREDO DO ANEXO"))
    x.caixa.entregar(mensagem("x" * 25 * 1024))
    x.caixa.entregar(mensagem("sem remetente", de="ninguém"))
    avisos = x.passar()
    corpos = [t.split("\n\n", 1)[1] for t in x.txts().values()]
    assert len(corpos) == 3
    assert next(cp for cp in corpos if "Ana" in cp).strip() == "Olá Ana"
    assert not any("SEGREDO" in cp or "<" in cp for cp in corpos)
    grande = next(cp for cp in corpos if cp.startswith("xxx"))
    assert grande.endswith("[cortado: mensagem maior que 20 KB]\n")
    assert max(len(t.encode()) for t in x.txts().values()) <= 20 * 1024 + 60
    assert any("sem remetente válido" in a for a in avisos)


def test_50_por_passagem(x):
    x.passar()
    for i in range(60):
        x.caixa.entregar(mensagem(f"n{i}"))
    x.passar()
    assert len(x.txts()) == 50
    x.passar()
    assert sorted(t.split("\n\n", 1)[1].strip() for t in x.txts().values()) == sorted(f"n{i}" for i in range(60))


def test_so_squads_de_negocio_do_registo_com_email(x):
    x.m.registo({"loja": "atendimento", "dev2": "desenvolvimento", "sem": "atendimento"})   # venda saiu
    x.passar()
    assert x.caixa.logins == ["loja"]   # dev2 não é de negócio, fora não está no registo, sem não tem e-mail


def test_sem_credenciais_avisa(x):
    x.m.segredos("LOJA_EMAIL_UTILIZADOR=loja\n")
    avisos = x.passar()
    assert "squad loja sem credenciais de e-mail" in avisos
    assert x.vista()["caixa"] == "geral@padaria.test"   # a vista sai na mesma


def test_uidvalidity_mudado_nao_reimporta(x):
    x.passar()
    x.caixa.entregar(mensagem("a"))
    x.passar()
    x.caixa.uidvalidity = 8
    assert any("UIDVALIDITY mudou" in a for a in x.passar())
    assert len(x.txts()) == 1
    est = json.loads((x.m.r.dir / "estado/colector-loja.json").read_text())
    assert est == {"uidvalidity": 8, "ultimo_uid": x.caixa.proximo}


def test_symlink_na_pasta_da_squad(x):
    fora = x.m.dir / "fora"
    fora.mkdir()
    (x.m.entrada / "loja").symlink_to(fora)
    avisos = x.passar()
    assert any(a.startswith("squad loja: pasta de entrada inutilizável") for a in avisos)
    assert list(fora.iterdir()) == []
    assert (x.m.entrada / "venda/vista.json").exists()   # as outras seguem


def test_registo_ilegivel_espera(x):
    (x.m.agentes / "squads.json").write_text("{")
    assert x.passar() == ["registo de squads ilegível; nada colhido nesta passagem"]
    assert x.caixa.ligacoes == 0


def test_sem_registo_sem_aviso(x):   # CA-1: sem squads.json não há squads de negócio, nem aviso
    (x.m.agentes / "squads.json").unlink()
    assert x.passar() == []
    assert x.caixa.ligacoes == 0
    assert list(x.m.entrada.iterdir()) == []


@pytest.mark.parametrize("causa", CAUSAS)
def test_registo_inutilizavel_nao_colhe(x, causa):   # CA-2
    _estragar_registo(x.m, causa)
    assert x.passar() == ["registo de squads ilegível; nada colhido nesta passagem"]
    assert x.caixa.ligacoes == 0
    assert list(x.m.entrada.iterdir()) == []


def test_lock_ocupado_devolve_none(x):   # outra passagem a correr ≠ passagem sem avisos
    x.passar()
    fd = os.open(x.m.r.dir / "estado/colector.lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert x.passar() is None
    finally:
        os.close(fd)
    assert x.passar() == []


def test_saude_na_raiz_ignorado(x):   # saude.json (e o temporário) na raiz não são squads nem mensagens
    x.passar()
    x.caixa.entregar(mensagem("a"))
    x.passar()
    antes = (x.txts(), x.vista(), sorted(p.name for p in x.m.entrada.iterdir()))
    velho = (AGORA - timedelta(days=200)).timestamp()
    for nome in ("saude.json", ".saude.json.tmp"):
        (x.m.entrada / nome).write_text('{"v": 1, "em": "2026-01-01T00:00:00+00:00", "avisos": []}')
        os.utime(x.m.entrada / nome, (velho, velho))
    assert col.purgar(x.m.r, AGORA) == 0
    assert x.passar() == []
    assert (x.txts(), x.vista()) == antes[:2]
    assert sorted(p.name for p in x.m.entrada.iterdir()) == sorted(antes[2] + [".saude.json.tmp", "saude.json"])


def test_purga_aos_90_dias(x):   # CA-32 (D14)
    x.passar()
    x.caixa.entregar(mensagem("velha"))
    x.passar(AGORA - timedelta(days=91))
    x.caixa.entregar(mensagem("nova"))
    x.passar(AGORA - timedelta(days=89))
    assert len(x.txts()) == 2
    orfa = x.m.entrada / "loja/mensagens" / ("m" + "e" * 16 + ".txt")
    orfa.write_text("sem registo")
    velho = (AGORA - timedelta(days=91)).timestamp()
    os.utime(orfa, (velho, velho))
    x.passar()
    assert [t.split("\n\n", 1)[1].strip() for t in x.txts().values()] == ["nova"]
    [mid] = x.txts()
    regs = [json.loads(p.read_text()) for p in (x.m.r.dir / "mensagens").glob("*.json")]
    assert sorted(r["id"] for r in regs if r["squad"] == "loja") == [mid]
    assert len(regs) == 2   # a «nova» da venda (a caixa falsa é a mesma)
    assert list(x.vista()["mensagens"]) == [mid]
    assert col.purgar(x.m.r, AGORA) == 0   # idempotente
