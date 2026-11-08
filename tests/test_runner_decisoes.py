"""Runner: aplicador de decisões (AC-c4, ADR-001). O `.md` resultante é lido com o `aprovacoes.py` do Office."""
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import decisoes, office_runner, officecfg

# O Office (contentor, Python 3.13) lê o .md com aprovacoes.py, que importa reader.py (Python >= 3.11). No host 3.10
# os testes leem-no com o parser do próprio aplicador e saltam só as verificações que precisam do Office.
OFFICE = sys.version_info >= (3, 11)
if OFFICE:
    import aprovacoes

QUANDO = datetime(2026, 11, 6, 14, 30, tzinfo=ZoneInfo("Europe/Lisbon"))
PEDIDO = """---
estado: pendente
criado: 2026-11-01
---
# Campanha de Novembro
## O quê
Lançar a campanha.
## Decisões
- [P1] Tecto da campanha 145 €
- [P2] Publicar a 10/11
"""
INTEIRO = """---
estado: pendente
criado: 2026-11-01
---
# Contratar designer
Um pedido sem decisões com id.
"""


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    pasta = officecfg.caminho(cfg, "decisoes")
    (pasta / "campanha.md").write_text(PEDIDO, encoding="utf-8")
    (pasta / "designer.md").write_text(INTEIRO, encoding="utf-8")
    return cfg, path, pasta, officecfg.caminho(cfg, "decisoes_fila")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def decisao(fila, nome, ficheiro="campanha.md", did="P1", accao="aprovar", nota="", sha256=None, pasta=None, **kw):
    d = {"v": 1, "criado": "2026-11-06T14:00:00+00:00", "fonte": "geral", "ficheiro": ficheiro, "id": did,
         "accao": accao, "nota": nota, "sha256": sha256 or sha(pasta / ficheiro), "ip": "127.0.0.1", "ua": "teste"}
    d.update(kw)
    (fila / nome).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def processar(cfg):
    return decisoes.processar(officecfg.caminho(cfg, "decisoes_fila"), officecfg.caminho(cfg, "decisoes"),
                              officecfg.caminho(cfg, "runner") / ".historico.json", "Ana", QUANDO)


def lido(pasta, nome):
    texto = (pasta / nome).read_text(encoding="utf-8")
    if OFFICE:
        return aprovacoes.parse_pedido(texto, nome)
    linhas = texto.split("\n")
    ds = [{"id": x["id"], "feito": x["estado"], "data": linhas[x["n"]][-11:-1] if x["estado"] else None}
          for x in decisoes.decisoes(texto)] or [{"id": "*", "feito": None, "data": None}]
    return {"estado": decisoes.frontmatter(texto).get("estado", ""), "decisoes": ds}


def test_aprovar_uma_decisao_actualiza_o_md_e_move_para_feito(ctx):
    cfg, _, pasta, fila = ctx
    decisao(fila, "20261106T140000-aaaaaaaa.json", pasta=pasta)
    linhas = processar(cfg)
    assert linhas == ["aprovado: campanha.md (P1)"]
    assert os.listdir(fila / "feito") == ["20261106T140000-aaaaaaaa.json"]
    assert not (fila / "20261106T140000-aaaaaaaa.json").exists()
    p = lido(pasta, "campanha.md")
    assert p["estado"] == "parcial (1 de 2 decididas)"
    d = {x["id"]: x for x in p["decisoes"]}
    assert d["P1"]["feito"] == "aprovado" and d["P1"]["data"] == "2026-11-06"
    assert not OFFICE or d["P1"]["valor"] == "145 €"
    assert d["P2"]["feito"] is None
    texto = (pasta / "campanha.md").read_text(encoding="utf-8")
    assert "## Decisão de Ana (06/11/2026 14:30, via Office)\n- P1: **aprovado** — Tecto da campanha 145 €" in texto
    assert "decidido: 2026-11-06" in texto


def test_cliques_seguidos_com_o_mesmo_sha_aplicam_os_dois(ctx):
    # «Aprovar todas»: as duas decisões levam o sha que o utilizador viu; a 2.ª chega depois da 1.ª mudar o .md
    cfg, _, pasta, fila = ctx
    visto = sha(pasta / "campanha.md")
    decisao(fila, "20261106T140000-aaaaaaaa.json", sha256=visto)
    decisao(fila, "20261106T140001-bbbbbbbb.json", did="P2", accao="recusar", nota="Cedo demais", sha256=visto)
    processar(cfg)
    p = lido(pasta, "campanha.md")
    assert p["estado"] == "aprovado"
    assert [x["feito"] for x in p["decisoes"]] == ["aprovado", "recusado"]
    assert "- Nota: Cedo demais" in (pasta / "campanha.md").read_text(encoding="utf-8")
    assert sorted(os.listdir(fila / "feito")) == ["20261106T140000-aaaaaaaa.json", "20261106T140001-bbbbbbbb.json"]
    if OFFICE:   # o Office deixa de mostrar o pedido: não está pendente
        abertos = aprovacoes.listar({"geral": pasta}, fila, ZoneInfo("UTC"), QUANDO.timestamp())
        assert [a["ficheiro"] for a in abertos] == ["designer.md"]


def test_sha_diferente_nao_altera_o_md_e_regista_motivo(ctx):
    cfg, _, pasta, fila = ctx
    antes = (pasta / "campanha.md").read_bytes()
    decisao(fila, "20261106T140000-aaaaaaaa.json", sha256="0" * 64)
    linhas = processar(cfg)
    assert (pasta / "campanha.md").read_bytes() == antes
    assert sorted(os.listdir(fila / "feito")) == ["20261106T140000-aaaaaaaa.json", "20261106T140000-aaaaaaaa.motivo.txt"]
    motivo = (fila / "feito" / "20261106T140000-aaaaaaaa.motivo.txt").read_text(encoding="utf-8")
    assert "sha256" in motivo and motivo.count("\n") == 1 and "não aplicada" in linhas[0]


def test_pedido_inteiro_aprovar_e_comentar(ctx):
    cfg, _, pasta, fila = ctx
    decisao(fila, "20261106T140000-aaaaaaaa.json", ficheiro="designer.md", did="*", accao="comentar",
            nota="Vejo amanhã", pasta=pasta)
    processar(cfg)
    texto = (pasta / "designer.md").read_text(encoding="utf-8")
    assert "## Observação de Ana (06/11/2026 14:30, via Office)\nVejo amanhã" in texto
    assert lido(pasta, "designer.md")["estado"] == "pendente"   # comentar não fecha
    decisao(fila, "20261106T140100-bbbbbbbb.json", ficheiro="designer.md", did="*", pasta=pasta)
    processar(cfg)
    p = lido(pasta, "designer.md")
    assert p["estado"] == "aprovado" and "- Pedido: **aprovado**" in (pasta / "designer.md").read_text(encoding="utf-8")


INVALIDAS = [
    ({"fonte": "marketing"}, "pedido inválido"),
    ({"ficheiro": "../campanha.md"}, "pedido inválido"),
    ({"ficheiro": "nao-existe.md"}, "ficheiro ilegível"),
    ({"accao": "apagar"}, "acção ou id inválidos"),
    ({"id": "P9"}, "não existe"),
    ({"accao": "recusar"}, "sem nota"),
    ({"id": "*"}, "uma a uma"),
    ({"v": 2}, "formato inválido"),
    ({"extra": 1}, "formato inválido"),
]


@pytest.mark.parametrize("troca,motivo", INVALIDAS)
def test_decisoes_invalidas_vao_para_feito_com_motivo(ctx, troca, motivo):
    cfg, _, pasta, fila = ctx
    antes = (pasta / "campanha.md").read_bytes()
    kw = dict(troca)
    decisao(fila, "20261106T140000-aaaaaaaa.json", sha256=sha(pasta / "campanha.md"), **kw)
    processar(cfg)
    assert (pasta / "campanha.md").read_bytes() == antes
    assert motivo in (fila / "feito" / "20261106T140000-aaaaaaaa.motivo.txt").read_text(encoding="utf-8")
    assert (fila / "feito" / "20261106T140000-aaaaaaaa.json").exists()


def test_decisao_ja_tomada_e_pedido_nao_pendente(ctx):
    cfg, _, pasta, fila = ctx
    decisao(fila, "20261106T140000-aaaaaaaa.json", pasta=pasta)
    processar(cfg)
    decisao(fila, "20261106T140100-bbbbbbbb.json", pasta=pasta)
    processar(cfg)
    assert "já estava aprovado" in (fila / "feito" / "20261106T140100-bbbbbbbb.motivo.txt").read_text(encoding="utf-8")
    (pasta / "fechado.md").write_text(PEDIDO.replace("pendente", "aprovado"), encoding="utf-8")
    decisao(fila, "20261106T140200-cccccccc.json", ficheiro="fechado.md", pasta=pasta)
    processar(cfg)
    assert "pendente" in (fila / "feito" / "20261106T140200-cccccccc.motivo.txt").read_text(encoding="utf-8")


def test_lixo_symlink_e_ocultos(ctx, tmp_path):
    cfg, _, _, fila = ctx
    (fila / "partido.json").write_text("{", encoding="utf-8")
    (tmp_path / "fora.json").write_text("{}", encoding="utf-8")
    (fila / "ligacao.json").symlink_to(tmp_path / "fora.json")
    (fila / ".a-escrever.json").write_text("{}", encoding="utf-8")
    processar(cfg)
    feitos = sorted(os.listdir(fila / "feito"))
    assert feitos == ["ligacao.json", "ligacao.motivo.txt", "partido.json", "partido.motivo.txt"]
    assert (fila / ".a-escrever.json").exists() and (tmp_path / "fora.json").exists()


def test_fila_vazia_nao_faz_nada(ctx):
    cfg, _, _, fila = ctx
    assert processar(cfg) == [] and not (fila / "feito").exists()


def test_uma_vez_aplica_decisoes_com_o_nome_do_utilizador(ctx):
    cfg, path, pasta, fila = ctx
    decisao(fila, "20261106T140000-aaaaaaaa.json", pasta=pasta)
    linhas = office_runner.uma_vez(cfg, str(path))
    assert "aprovado: campanha.md (P1)" in linhas
    assert "## Decisão de Ana (" in (pasta / "campanha.md").read_text(encoding="utf-8")
    assert json.loads((officecfg.caminho(cfg, "runner") / ".historico.json").read_text(encoding="utf-8"))
