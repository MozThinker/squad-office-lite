"""servico_accoes/executor.py (ADR-021): só executa o aprovado, uma vez, dentro das 48 h e do limite do dia (no fuso do
pedido), só para o remetente registado, com espera anulável no envio; revalida o conteúdo; livro sem conteúdo;
desfazer (CA-12…CA-16, CA-19, CA-20, CA-22, CA-26). SMTP e Telegram locais em 127.0.0.1, IMAP falso, CSV em pasta
temporária, `AGORA` fixo."""
import json
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import accoes
from servico_accoes import executor as ex
from tests.accoes_falsos import AGORA, CaixaFalsa, Mundo, SMTPFalso, TelegramFalso, bloco, mid

M1, M3 = mid(1), mid(3)
CORPO = "Olá,\n\nAbrimos às 9h.\n\nCumprimentos"
CHAVES = list(ex.CHAVES_LIVRO)


def email(tipo="email_responder", origem=M1, assunto="Horário de sábado", corpo=CORPO):
    return bloco(tipo=tipo, origem=origem, assunto=assunto, corpo=corpo)


def folha(**valores):
    return bloco(tipo="folha_linha", folha="leads", valores=valores or {"nome": "Ana", "nota": "4"})


def aviso(texto="Chegou uma reclamação."):
    return bloco(tipo="aviso_dono", texto=texto)


class Ctx:
    def __init__(self, tmp, monkeypatch):
        self.m = m = Mundo(tmp, {"loja": "atendimento", "s2": "comercial"})
        self.smtp, self.tg, self.caixa = SMTPFalso(), TelegramFalso(), CaixaFalsa()
        m.registar(M1, "loja", "cliente@exemplo.test")
        m.registar(M3, "s2", "c2@exemplo.test")
        todas = list(accoes.TIPOS)
        self.sqs = {"loja": {"accoes": todas, "dominios": ["padaria.test"], "email": m.email(self.smtp.porta),
                             "folhas": m.folha()},
                    "s2": {"accoes": todas, "email": m.email(self.smtp.porta, "geral@loja2.test"),
                           "folhas": m.folha(ficheiro="s2")}}
        self.telegram = {"url": self.tg.url, "chat_id": "42"}
        m.config(self.sqs, self.telegram)
        m.segredos("LOJA_EMAIL_UTILIZADOR=geral@padaria.test\nLOJA_EMAIL_SENHA=senha-falsa\n"
                   "S2_EMAIL_UTILIZADOR=geral@loja2.test\nS2_EMAIL_SENHA=senha-falsa\nTELEGRAM_TOKEN=123:falso\n")
        m.vista("loja")
        m.vista("s2")
        monkeypatch.setattr(ex, "IMAP", self.caixa)

    def fechar(self):
        self.smtp.fechar()
        self.tg.fechar()

    def pedir(self, texto, sid="loja", agora=AGORA) -> str:
        nome = self.m.pedir(texto, sid=sid, now=agora.timestamp())
        assert nome and self.m.reg(nome), self.m.md(nome) if nome else None
        return nome

    def passar(self, agora=AGORA):
        assert ex.passagem(self.m.r, agora) == []

    def livro(self) -> list[dict]:
        return [json.loads(x) for p in sorted(self.m.r.dir.glob("*.jsonl")) for x in p.read_text().splitlines()]

    def estados(self, sha=None) -> list[tuple]:
        return [(e["estado"], e["motivo"]) for e in self.livro() if sha is None or e["sha"] == sha]

    def ligacoes(self) -> int:
        return self.smtp.ligacoes + self.caixa.ligacoes + len(self.tg.chamadas)


@pytest.fixture
def x(tmp_path, monkeypatch):
    c = Ctx(tmp_path, monkeypatch)
    yield c
    c.fechar()


# --- destinatário (CA-12) ----------------------------------------------------------------------------------------------

def test_so_para_o_remetente_registado(x):
    nome = x.pedir(email())
    x.m.aprovar(nome)
    x.passar()
    assert x.smtp.ligacoes == 0   # espera de 2 min
    x.passar(AGORA + timedelta(seconds=120))
    [msg] = x.smtp.mensagens
    assert (msg["de"], msg["para"]) == ("geral@padaria.test", ["cliente@exemplo.test"])
    cab = msg["dados"].split(b"\r\n\r\n")[0].decode()
    assert "To: cliente@exemplo.test" in cab
    assert "In-Reply-To: <orig@exemplo.test>" in cab
    assert "Cc:" not in cab and "Bcc:" not in cab
    assert x.estados() == [("a_enviar", None), ("executada", None)]


def test_origem_tirada_do_registo(x):
    nome = x.pedir(email("email_rascunho"))
    x.m.aprovar(nome)
    (x.m.r.dir / "mensagens" / f"{M1}.json").unlink()
    x.passar()
    assert (x.ligacoes(), x.estados()) == (0, [("recusada", accoes.M_ORIGEM)])


# --- definição (CA-13, CA-26) ------------------------------------------------------------------------------------------

def test_md_ou_registo_mudados(x):   # CA-13
    nomes = [x.pedir(email("email_rascunho", assunto=f"a{i}")) for i in range(5)]
    for n in nomes:
        x.m.aprovar(n)
    md = [x.m.decisoes / n for n in nomes]
    md[0].write_text(md[0].read_text().replace("> Abrimos às 9h.", "> Abrimos às 8h."))
    md[1].write_text(md[1].read_text().replace(f"sha256: {x.m.sha(nomes[1])}", "sha256: " + "0" * 64))
    regs = [x.m.decisoes / accoes.REGISTOS / f"{n[:-3]}.json" for n in nomes]
    r = json.loads(regs[2].read_text())
    r["accoes"][0]["def"]["campos"]["corpo"] += "!"
    regs[2].write_text(json.dumps(r))
    r = json.loads(regs[3].read_text())
    r["accoes"][0]["sha256"] = "f" * 64
    regs[3].write_text(json.dumps(r))
    x.passar()
    assert x.caixa.ligacoes == 1   # só o 5.º, intacto
    por = {e["pedido"]: (e["estado"], e["motivo"]) for e in x.livro()}
    for n in nomes[:4]:
        assert por[n] == ("recusada", accoes.M_DEFINICAO), n
    assert por[nomes[4]] == ("executada", None)


def test_um_caracter_em_cada_campo_do_md(x):   # CA-13: assunto, criado, origem e valor da folha
    trocas = [("> Horário de sábado", "> Horário de sábadO"),
              ("criado: 2026-11-18T10:00:07", "criado: 2026-11-18T10:00:08"),
              (f"    origem: {M1}", f"    origem: {mid(2)}"),
              ("> nota: 4", "> nota: 5")]
    nomes = [x.pedir(email("email_rascunho", corpo=f"c{i}")) for i in range(3)] + [x.pedir(folha())]
    for n, (de, para) in zip(nomes, trocas):
        x.m.aprovar(n)
        p = x.m.decisoes / n
        t = p.read_text(encoding="utf-8")
        assert t.count(de) == 1, de
        p.write_text(t.replace(de, para), encoding="utf-8")
    x.passar()
    assert x.ligacoes() == 0
    assert not (x.m.r.dir / "folhas/leads.csv").exists()
    assert {e["pedido"]: (e["estado"], e["motivo"]) for e in x.livro()} == \
        {n: ("recusada", accoes.M_DEFINICAO) for n in nomes}


def forjar(x, nome, **campos):
    """Muda a definição no registo e no .md de forma coerente (sha recalculado, secção re-renderizada): o que só o
    uid do runner pode fazer (risco S1); o executor revalida o conteúdo."""
    rp = x.m.decisoes / accoes.REGISTOS / f"{nome[:-3]}.json"
    r = json.loads(rp.read_text())
    a = r["accoes"][0]
    antiga = accoes.secao("A1", a["def"], a["sha256"])
    a["def"]["campos"].update(campos)
    a["sha256"] = accoes.sha(a["def"])
    rp.write_text(json.dumps(r, ensure_ascii=False))
    md = x.m.decisoes / nome
    md.write_text(md.read_text(encoding="utf-8").replace(antiga, accoes.secao("A1", a["def"], a["sha256"])),
                  encoding="utf-8")
    return a["sha256"]


def test_definicao_forjada_com_sha_coerente(x):   # CA-26: revalida pelas regras do CA-17
    casos = [({"texto": "olá <b>x</b>"}, accoes.M_HTML), ({"texto": "paga em https://evil.test"}, accoes.M_LINK),
             ({"texto": "x" * 9000}, "texto acima de 500 caracteres")]
    shas = []
    for i, (campos, _) in enumerate(casos):
        nome = x.pedir(aviso(f"aviso {i}"))
        x.m.aprovar(nome)
        shas.append(forjar(x, nome, **campos))
    r = x.pedir(email("email_rascunho"))
    x.m.aprovar(r)
    shas.append(forjar(x, r, corpo="escreve para outro@mal.test"))
    x.passar()
    assert x.ligacoes() == 0
    for h, motivo in zip(shas, [mo for _, mo in casos] + [accoes.M_TERCEIROS]):
        assert x.estados(h) == [("recusada", motivo)]


def test_config_mudada_depois_de_pedida(x):   # CA-26: domínio tirado da lista entretanto
    nome = x.pedir(aviso("vê https://padaria.test/horario"))
    x.m.aprovar(nome)
    x.m.config({**x.sqs, "loja": {**x.sqs["loja"], "dominios": []}}, x.telegram)
    x.passar()
    assert (x.ligacoes(), x.estados()) == (0, [("recusada", accoes.M_LINK)])


def test_responder_de_origem_nao_autenticada(x):
    nome = x.pedir(email())
    x.m.aprovar(nome)
    x.m.registar(M1, "loja", "cliente@exemplo.test", autenticado=False)
    x.passar()
    x.passar(AGORA + timedelta(seconds=120))
    assert x.smtp.ligacoes == 0
    assert x.estados()[-1] == ("recusada", accoes.M_NAO_AUTENTICADO)


def test_secao_ausente_ou_cortada_espera_sem_livro(x):   # .md a meio da escrita
    a = x.pedir(aviso("x"))
    b = x.pedir(email("email_rascunho"))
    x.m.aprovar(a)
    x.m.aprovar(b)
    pa, pb = x.m.decisoes / a, x.m.decisoes / b
    ia, ib = pa.read_text(encoding="utf-8"), pb.read_text(encoding="utf-8")
    pa.write_text(ia.split("### A1 · ")[0], encoding="utf-8")
    pb.write_text(ib.split("> Abrimos")[0] + "> Abr", encoding="utf-8")
    x.passar()
    assert (x.ligacoes(), x.livro()) == (0, [])
    pa.write_text(ia, encoding="utf-8")
    pb.write_text(ib, encoding="utf-8")
    x.passar()
    assert sorted(x.estados()) == [("executada", None), ("executada", None)]


def test_registos_antigos_nem_se_leem(x, monkeypatch):
    velho = "accoes-loja-20261001-1.json"
    (x.m.decisoes / accoes.REGISTOS).mkdir(exist_ok=True)
    (x.m.decisoes / accoes.REGISTOS / velho).write_text("{}")
    lidos, real = [], accoes.ler_json
    monkeypatch.setattr(accoes, "ler_json", lambda p, n: lidos.append(p.name) or real(p, n))
    x.passar()
    assert velho not in lidos


def test_recusado_ou_pendente_nada(x):
    a = x.pedir(aviso("um"))
    x.pedir(aviso("dois"))
    x.m.aprovar(a, como="recusado")
    x.passar()
    assert (x.ligacoes(), x.livro()) == (0, [])


def test_squad_inactiva_e_conector(x):   # CA-26
    a = x.pedir(aviso("um"))
    x.m.aprovar(a)
    x.m.registo({"loja": "desenvolvimento", "s2": "comercial"})   # deixou de ser de negócio
    x.passar()
    x.m.registo({"s2": "comercial"})   # e fora do registo
    b = x.pedir(aviso("dois"), sid="s2")
    x.m.aprovar(b)
    x.m.registo({"loja": "atendimento"})
    x.passar()
    assert x.estados(x.m.sha(a)) == [("recusada", accoes.M_INACTIVA)]
    assert x.estados(x.m.sha(b)) == [("recusada", accoes.M_INACTIVA)]
    x.m.registo()
    c = x.pedir(aviso("três"))
    x.m.aprovar(c)
    x.m.segredos("")
    x.passar()   # C5: conector por configurar espera sem livro (não perde a aprovação) até caducar
    assert x.estados(x.m.sha(c)) == [] and x.ligacoes() == 0
    x.passar(AGORA + accoes.VALIDADE + timedelta(seconds=1))
    assert x.estados(x.m.sha(c)) == [("caducada", accoes.M_CADUCADA)]
    d = x.pedir(aviso("quatro"))
    x.m.aprovar(d)
    x.passar()
    assert x.estados(x.m.sha(d)) == []
    x.m.segredos("TELEGRAM_TOKEN=123:falso\n")   # o conector do aviso: a mesma aprovação executa
    x.passar()
    assert x.estados(x.m.sha(d)) == [("executada", None)]


def test_registo_de_squads_ilegivel_espera(x):
    a = x.pedir(aviso("um"))
    x.m.aprovar(a)
    reg = x.m.agentes / "squads.json"
    guardado = reg.read_text()
    reg.write_text("{")
    assert ex.passagem(x.m.r, AGORA) == ["registo de squads ilegível; nada executado nesta passagem"]
    assert (x.ligacoes(), x.livro()) == (0, [])
    reg.write_text(guardado)
    x.passar()
    assert x.estados() == [("executada", None)]


def _estragar_registo(m, causa: str) -> None:
    """Deixa `agentes/squads.json` presente mas inutilizável (CA-2)."""
    reg = m.agentes / "squads.json"
    if causa == "sem_permissao":
        os.chmod(reg, 0)
    elif causa == "json_invalido":
        reg.write_text("{", encoding="utf-8")
    elif causa == "symlink":
        real = m.agentes / "real.json"
        reg.rename(real)
        reg.symlink_to(real)
    elif causa == "grande":
        reg.write_text(json.dumps({"v": 1, "squads": [], "x": "y" * accoes.REGISTO_MAX}), encoding="utf-8")
    elif causa == "formato":
        reg.write_text(json.dumps({"squads": [{"id": "loja", "modelo": "atendimento"}]}), encoding="utf-8")


CAUSAS = [pytest.param("sem_permissao", marks=pytest.mark.skipif(os.geteuid() == 0, reason="root lê sem permissões")),
          "json_invalido", "symlink", "grande", "formato"]


def test_sem_registo_executa_sem_aviso(x):   # CA-1: sem squads.json = sem squads de negócio, não «ilegível»
    a = x.pedir(aviso("um"))
    x.m.aprovar(a)
    (x.m.agentes / "squads.json").unlink()
    assert ex.ler_squads(x.m.r) == {}
    guardado = x.m.agentes / "squads.json"
    assert ex.passagem(x.m.r, AGORA) == []
    assert (x.ligacoes(), x.livro()) == (0, [])   # nada a fazer: o aprovado não é recusado
    x.m.registo()
    assert guardado.exists()
    x.passar()
    assert x.estados() == [("executada", None)]


def test_registo_sem_squads_de_negocio_recusa(x):   # ≠ sem registo: um registo vazio continua a recusar
    a = x.pedir(aviso("um"))
    x.m.aprovar(a)
    x.m.registo({"loja": "desenvolvimento"})
    assert ex.ler_registo(x.m.r) == (True, {})
    x.passar()
    assert x.estados(x.m.sha(a)) == [("recusada", accoes.M_INACTIVA)]


def test_sem_pasta_agentes_sem_aviso(x):   # CA-3: ENOENT noutro componente do caminho também é «sem registo»
    x.m.agentes.rename(x.m.agentes.with_name("agentes-fora"))
    assert ex.ler_squads(x.m.r) == {}
    x.passar()


@pytest.mark.parametrize("causa", CAUSAS)
def test_registo_inutilizavel_espera(x, causa):   # CA-2
    a = x.pedir(aviso("um"))
    x.m.aprovar(a)
    _estragar_registo(x.m, causa)
    assert ex.ler_squads(x.m.r) is None
    assert ex.passagem(x.m.r, AGORA) == ["registo de squads ilegível; nada executado nesta passagem"]
    assert (x.ligacoes(), x.livro()) == (0, [])


def test_lock_ocupado_devolve_none(x):   # outra passagem a correr ≠ passagem sem avisos
    fd = ex.trancar(x.m.r, bloquear=True)
    try:
        assert ex.passagem(x.m.r, AGORA) is None
    finally:
        os.close(fd)
    x.passar()


def test_perfil_lite_responder_nao_ligada(x):   # CA-22
    nome = x.pedir(email() + aviso())
    x.m.aprovar(nome, "A1", "A2")
    sem_lista = {k: {kk: vv for kk, vv in v.items() if kk != "accoes"} for k, v in x.sqs.items()}
    x.m.config(sem_lista, x.telegram)   # perfil lite: rascunho, folha e aviso
    x.passar()
    x.passar(AGORA + timedelta(seconds=120))
    assert x.estados(x.m.sha(nome, "A1")) == [("recusada", accoes.M_NAO_LIGADA)]
    assert x.estados(x.m.sha(nome, "A2")) == [("executada", None)]
    assert (x.smtp.ligacoes, len(x.tg.chamadas)) == (0, 1)


def test_desligada_depois_de_pedida(x):
    a = x.pedir(aviso("um"))
    x.m.aprovar(a)
    x.m.config({**x.sqs, "loja": {**x.sqs["loja"], "accoes": ["folha_linha"]}}, x.telegram)
    x.passar()
    assert (x.ligacoes(), x.estados()) == (0, [("recusada", accoes.M_NAO_LIGADA)])


# --- uma vez (CA-14) ---------------------------------------------------------------------------------------------------

def test_duas_passagens_seguidas(x):
    nome = x.pedir(aviso())
    x.m.aprovar(nome)
    x.passar()
    x.passar(AGORA + timedelta(minutes=1))
    [c] = x.tg.chamadas
    assert (c["caminho"], c["corpo"]["chat_id"], c["corpo"]["text"]) == \
        ("/bot123:falso/sendMessage", "42", "Chegou uma reclamação.")
    assert x.estados() == [("executada", None)]


def test_duas_em_paralelo(x):
    nome = x.pedir(aviso("paralelo"))
    x.m.aprovar(nome)
    barreira, erros = threading.Barrier(2), []

    def correr():
        try:
            barreira.wait()
            ex.passagem(x.m.r, AGORA)
        except Exception as e:   # noqa: BLE001
            erros.append(e)
    ts = [threading.Thread(target=correr) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert erros == []
    assert len(x.tg.chamadas) == 1
    assert [e["estado"] for e in x.livro()] == ["executada"]


def test_marcador_sem_livro_e_erro_interrompida(x):
    nome = x.pedir(aviso("x"))
    x.m.aprovar(nome)
    (x.m.r.dir / "estado/executados").mkdir(parents=True)
    (x.m.r.dir / "estado/executados" / x.m.sha(nome)).touch()
    x.passar()
    assert (len(x.tg.chamadas), x.estados()) == (0, [("erro", "interrompida")])


# --- validade (CA-15) --------------------------------------------------------------------------------------------------

def test_48_horas(x):
    t = AGORA + timedelta(days=2)
    velho = x.pedir(aviso("velho"), agora=t - timedelta(hours=48, minutes=1))
    novo = x.pedir(aviso("novo"), agora=t - timedelta(hours=47, minutes=59))
    x.m.aprovar(velho)
    x.m.aprovar(novo)
    x.passar(t)
    assert x.estados(x.m.sha(velho)) == [("caducada", accoes.M_CADUCADA)]
    assert x.estados(x.m.sha(novo)) == [("executada", None)]
    assert [c["corpo"]["text"] for c in x.tg.chamadas] == ["novo"]


# --- limites do dia (CA-16) --------------------------------------------------------------------------------------------

def encher(x, tipo, n, sid="loja", hora=AGORA):
    linhas = [{"hora": hora.isoformat(), "squad": sid, "tipo": tipo, "sha": f"{i:064x}", "pedido": "x", "linha": "A1",
               "aprovado": "2026-11-18", "estado": "erro" if i % 3 == 0 else "executada", "motivo": None, "id": None}
              for i in range(n)]
    (x.m.r.dir / f"{hora:%Y-%m}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in linhas))


def chamadas(x, tipo) -> int:
    if tipo == "email_responder":
        return x.smtp.ligacoes
    if tipo == "email_rascunho":
        return x.caixa.ligacoes
    if tipo == "aviso_dono":
        return len(x.tg.chamadas)
    fs = [x.m.r.dir / "folhas" / n for n in ("leads.csv", "s2.csv")]
    return sum(max(0, len(f.read_text().splitlines()) - 1) for f in fs if f.exists())


@pytest.mark.parametrize("tipo,n", list(accoes.LIMITES_DIA.items()))
def test_limite_do_dia(x, tipo, n):
    encher(x, tipo, n)
    prop = {"email_responder": lambda o: email(origem=o), "email_rascunho": lambda o: email("email_rascunho", o),
            "folha_linha": lambda o: folha(), "aviso_dono": lambda o: aviso(f"aviso {o}")}[tipo]
    a = x.pedir(prop(M1))
    b = x.pedir(prop(M3), sid="s2")
    x.m.aprovar(a)
    x.m.aprovar(b)
    for k in range(3):
        x.passar(AGORA + timedelta(minutes=k * 3))
    assert x.estados(x.m.sha(a)) == [("recusada", f"limite do dia ({n}) atingido")]
    assert x.estados(x.m.sha(b))[-1] == ("executada", None)
    assert chamadas(x, tipo) == 1   # só a da s2: a da loja não ligou ao serviço
    x.passar(AGORA + timedelta(days=1))
    x.passar(AGORA + timedelta(days=1, seconds=120))
    assert x.estados(x.m.sha(a))[-1] == ("executada", None)
    assert chamadas(x, tipo) == 2


def test_limite_da_configuracao_e_erro_conta(x):
    x.m.config({**x.sqs, "loja": {**x.sqs["loja"], "limites": {"aviso_dono": 1}}}, x.telegram)
    x.tg.estado = 500
    a = x.pedir(aviso("um"))
    b = x.pedir(aviso("dois"))
    x.m.aprovar(a)
    x.m.aprovar(b)
    x.passar()
    assert x.estados(x.m.sha(a)) == [("erro", "HTTPError 500")]
    assert x.estados(x.m.sha(b)) == [("recusada", "limite do dia (1) atingido")]
    assert len(x.tg.chamadas) == 1


def test_dia_no_fuso_do_pedido(x):   # D4: o dia conta-se no fuso gravado no registo do pedido
    utc = ZoneInfo("UTC")
    encher(x, "aviso_dono", 20, hora=datetime(2026, 11, 18, 14, 30, tzinfo=utc))   # 23:30 em Tóquio, dia 18
    t = datetime(2026, 11, 18, 15, 30, tzinfo=utc)   # 00:30 do dia 19 em Tóquio; ainda dia 18 em Lisboa
    lisboa = x.pedir(aviso("lisboa"), agora=t)
    x.m.cfg = {**x.m.cfg, "fuso": "Asia/Tokyo"}
    toquio = x.pedir(aviso("tóquio"), agora=t)
    x.m.aprovar(lisboa)
    x.m.aprovar(toquio)
    x.passar(t + timedelta(minutes=1))
    assert x.estados(x.m.sha(lisboa)) == [("recusada", "limite do dia (20) atingido")]
    assert x.estados(x.m.sha(toquio)) == [("executada", None)]
    [e] = [e for e in x.livro() if e["sha"] == x.m.sha(toquio)]
    assert e["hora"] == "2026-11-19T00:31:00+09:00"


# --- livro (CA-19) -----------------------------------------------------------------------------------------------------

def test_livro_campos_e_sem_conteudo(x):
    nome = x.pedir(email(assunto="Assunto secreto") + email("email_rascunho", corpo="Corpo secreto rascunho")
                   + folha(nome="Valor Secreto", contacto="+351 900 000 000") + aviso("Aviso secreto"))
    x.m.aprovar(nome, "A1", "A2", "A3", "A4")
    x.passar()
    x.passar(AGORA + timedelta(seconds=120))
    execs = [e for e in x.livro() if e["estado"] == "executada"]
    assert sorted(e["tipo"] for e in execs) == sorted(accoes.TIPOS)
    for e in x.livro():
        assert list(e) == CHAVES
        assert (e["pedido"], e["aprovado"]) == (nome, "2026-11-18")
        assert e["hora"].endswith("+00:00")
    ids = {e["tipo"]: e["id"] for e in execs}
    assert ids["email_responder"].startswith("<") and ids["email_responder"].endswith("@office-accoes>")
    assert ids["email_rascunho"].startswith("7:")
    assert ids["folha_linha"].startswith("leads:1:")
    assert ids["aviso_dono"] == "101"
    [livro] = list(x.m.r.dir.glob("*.jsonl"))
    assert livro.name == "2026-11.jsonl"
    assert oct(livro.stat().st_mode & 0o777) == "0o600"
    for p in [livro, *(x.m.r.dir / "estado").rglob("*")]:
        if p.is_file():
            txt = p.read_text(encoding="utf-8")
            for fora in ("Assunto secreto", "Abrimos", "Corpo secreto", "cliente@exemplo.test", "Valor Secreto",
                         "+351", "Aviso secreto", "geral@padaria"):
                assert fora not in txt, (p, fora)


# --- desfazer e anular (CA-20) -----------------------------------------------------------------------------------------

def test_desfazer_rascunho(x):
    nome = x.pedir(email("email_rascunho"))
    x.m.aprovar(nome)
    x.passar()
    [uid] = x.caixa.pastas["Drafts"]
    assert ex.desfazer(x.m.r, x.m.sha(nome), AGORA) == (0, "desfeita")
    assert x.caixa.pastas["Drafts"] == {}
    assert ("STORE", str(uid), "+FLAGS", r"(\Deleted)") in x.caixa.comandos
    assert ("EXPUNGE", str(uid)) in x.caixa.comandos
    assert x.estados() == [("executada", None), ("desfeita", None)]
    n = len(x.caixa.comandos)
    assert ex.desfazer(x.m.r, x.m.sha(nome), AGORA) == (0, "já estava desfeita: nada a fazer")
    assert (len(x.caixa.comandos), len(x.livro())) == (n, 2)


def test_desfazer_folha(x):
    nome = x.pedir(folha(nome="A") + folha(nome="=B") + folha(nome="C"))
    x.m.aprovar(nome, "A1", "A2", "A3")
    x.passar()
    f = x.m.r.dir / "folhas/leads.csv"
    antes = f.read_bytes().split(b"\n")
    assert antes == [b'"nome","contacto","nota"', b'"A","",""', b'"\'=B","",""', b'"C","",""', b""]
    assert ex.desfazer(x.m.r, x.m.sha(nome, "A2"), AGORA)[0] == 0
    assert f.read_bytes().split(b"\n") == [antes[0], antes[1], antes[3], b""]
    f.write_bytes(f.read_bytes().replace(b'"C","",""', b'"C","","5"'))   # mudada à mão
    guardado = f.read_bytes()
    assert ex.desfazer(x.m.r, x.m.sha(nome, "A3"), AGORA) == (1, "não desfeita: a linha foi mudada à mão")
    assert f.read_bytes() == guardado
    assert [e["estado"] for e in x.livro()].count("desfeita") == 1


def test_desfazer_folha_linhas_iguais_e_anterior(x):
    nome = x.pedir(folha(nome="Igual"))
    outro = x.pedir(folha(nome="Igual"), agora=AGORA + timedelta(seconds=1))
    x.m.aprovar(nome)
    x.m.aprovar(outro)
    x.passar()
    f = x.m.r.dir / "folhas/leads.csv"
    linha = b'"Igual","",""'
    assert f.read_bytes().split(b"\n") == [b'"nome","contacto","nota"', linha, linha, b""]
    assert ex.desfazer(x.m.r, x.m.sha(outro), AGORA)[0] == 0
    assert ex.desfazer(x.m.r, x.m.sha(nome), AGORA)[0] == 0
    assert f.read_bytes() == b'"nome","contacto","nota"\n'


def test_desfazer_folha_sem_seguir_symlinks(x):
    nome = x.pedir(folha(nome="A") + folha(nome="B"))
    x.m.aprovar(nome, "A1", "A2")
    x.passar()
    fora = x.m.dir / "fora.txt"
    fora.write_text("intacto")
    (x.m.r.dir / "folhas/.leads.csv.tmp").symlink_to(fora)
    assert ex.desfazer(x.m.r, x.m.sha(nome, "A1"), AGORA)[0] == 0
    assert fora.read_text() == "intacto"
    assert oct((x.m.r.dir / "folhas/leads.csv").stat().st_mode & 0o777) == "0o644"


def test_folha_celula_segura(x):
    nome = x.pedir(folha(nome="quer A; B", nota="a;=1\tb"))
    x.m.aprovar(nome)
    x.passar()
    assert (x.m.r.dir / "folhas/leads.csv").read_bytes().split(b"\n")[1] == b'"quer A; B","","a;\'=1 b"'


def test_desfazer_servidor_recusa_e_casos(x):
    nome = x.pedir(email("email_rascunho"))
    x.m.aprovar(nome)
    x.passar()
    x.caixa.falha_store = True
    assert ex.desfazer(x.m.r, x.m.sha(nome), AGORA) == (1, "não desfeita: o servidor recusou o STORE")
    assert "desfeita" not in [e["estado"] for e in x.livro()]
    assert ex.desfazer(x.m.r, "x", AGORA) == (1, "sha inválido")
    assert ex.desfazer(x.m.r, "0" * 64, AGORA) == (1, "nada a desfazer: a acção não foi executada")
    r = x.pedir(aviso())
    x.m.aprovar(r)
    x.passar()
    assert ex.desfazer(x.m.r, x.m.sha(r), AGORA) == (1, "esta acção não se desfaz")


def test_espera_de_2_minutos(x):
    nome = x.pedir(email())
    x.m.aprovar(nome)
    x.passar()
    assert x.estados() == [("a_enviar", None)]
    x.passar(AGORA + timedelta(seconds=119))
    assert x.smtp.ligacoes == 0
    x.passar(AGORA + timedelta(seconds=120))
    assert x.smtp.ligacoes == 1
    assert x.estados()[-1] == ("executada", None)
    assert ex.anular(x.m.r, x.m.sha(nome)) == (1, "já foi enviada: não se anula")


def test_anular(x):
    nome = x.pedir(email())
    x.m.aprovar(nome)
    x.passar()
    assert ex.anular(x.m.r, x.m.sha(nome)) == (0, "anulada: não sai na próxima passagem")
    x.passar(AGORA + timedelta(seconds=180))
    x.passar(AGORA + timedelta(seconds=240))
    assert x.smtp.ligacoes == 0
    assert x.estados() == [("a_enviar", None), ("anulada", None)]
    assert ex.anular(x.m.r, "abc") == (1, "sha inválido")


def test_caduca_durante_a_espera(x):
    nome = x.pedir(email(), agora=AGORA - timedelta(hours=47, minutes=59))
    x.m.aprovar(nome)
    x.passar()
    x.passar(AGORA + timedelta(seconds=120))
    assert x.smtp.ligacoes == 0
    assert x.estados() == [("a_enviar", None), ("caducada", accoes.M_CADUCADA)]
    assert not (x.m.r.dir / "estado/a_enviar" / f"{x.m.sha(nome)}.json").exists()
