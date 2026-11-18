"""Modelos de negócio `atendimento` e `comercial` (ADR-020): listados no GET /api/agentes com agentes, lead e modelo de
cada agente; `tools/verificar_modelos.py` a 0 e os 4 modelos antigos sem um byte mudado (CA-1); só leitura, `## Nunca`
e o validador dos agentes (CA-2); blocos de acção documentados nos `CLAUDE.md`."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import agentes
from tests import cfg as tcfg

RAIZ = Path(__file__).resolve().parent.parent
MODELOS = RAIZ / "modelos" / "squads"
# sha256 de cada modelo antigo (caminho relativo + bytes de cada ficheiro, por ordem): fixados na 0.4.0
ANTIGOS = {
    "conteudo": "875df39bef739ba149ff06db635f671075e14bb8412e1523d74c117e34dda41d",
    "desenvolvimento": "a993963312c61218a5c6549c4054333194e8c330c8072ff94ef3d2ee4bb8a749",
    "investigacao": "7b4cf46fc381d8e701e6b6a115eba1a5be9ef6178185e5df41836812bb12b301",
    "observacao": "3eac0d179928d867ec495418a05290e72319ef4ab411b482cd921b3a75bdeef2",
}
NEGOCIO = {
    "atendimento": ("responsavel", {"responsavel": "sonnet", "triagem": "haiku", "redactor": "sonnet",
                                    "revisor": "haiku"}),
    "comercial": ("comercial", {"comercial": "sonnet", "pesquisador": "haiku", "qualificador": "haiku"}),
}
PROIBIDAS = ("Bash", "Edit", "Write", "WebFetch", "NotebookEdit")


def _sha_modelo(pasta: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in pasta.rglob("*") if x.is_file()):
        h.update(str(p.relative_to(pasta)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def _agentes(mid: str) -> dict:
    return {f[len("agents/"):-3]: raw.decode("utf-8") for f, raw in agentes.agentes_de(MODELOS / mid)}


def test_ca1_verificar_modelos_sai_0_e_antigos_iguais():
    r = subprocess.run([sys.executable, str(RAIZ / "tools" / "verificar_modelos.py")], capture_output=True, text=True,
                       check=False)
    assert (r.returncode, r.stdout) == (0, "")
    assert {m: _sha_modelo(MODELOS / m) for m in ANTIGOS} == ANTIGOS
    assert sorted(p.name for p in MODELOS.iterdir()) == sorted([*ANTIGOS, *NEGOCIO])


def test_ca1_get_api_agentes_lista_os_modelos_de_negocio(tmp_path):
    cfg = tcfg.ler("fuso: Europe/Lisbon\nsquads:\n  - id: dev\n    nome: Dev\nprojectos:\n  - id: loja\n    nome: Loja\n")
    d = agentes.resposta(cfg, tmp_path / "agentes", tmp_path / "rasc", tmp_path / "fila", ZoneInfo("Europe/Lisbon"),
                         0)
    por_id = {m["id"]: m for m in d["modelos"]}
    for mid, (lead, modelos) in NEGOCIO.items():
        m = por_id[mid]
        assert m["lead"] == lead and m["negocio"] is True and sorted(m["agentes"]) == sorted(modelos)
        assert m["modelo_agentes"] == modelos
    assert all(not por_id[m]["negocio"] for m in ANTIGOS)
    json.dumps(d)


@pytest.mark.parametrize("mid", sorted(NEGOCIO))
def test_ca2_so_leitura_nunca_e_validador(mid):
    ags = _agentes(mid)
    assert set(ags) == set(NEGOCIO[mid][1])
    for nome, texto in ags.items():
        assert agentes.validar_md(texto, nome, set()) is None, nome
        tools = agentes.lista(agentes.frontmatter(texto)["tools"])
        assert not any(t in PROIBIDAS or t.startswith("mcp__") for t in tools), nome
        assert ("WebSearch" in tools) is (mid == "comercial" and nome == "pesquisador"), nome
        assert set(tools) <= {"Read", "Grep", "Glob", "Agent", "WebSearch"}
        assert ("Agent" in tools) is (nome == NEGOCIO[mid][0]), nome   # só o lead chama os outros
        nunca = re.search(r"^## Nunca\n\n((?:- .+\n)+)", texto, re.MULTILINE)
        assert nunca and len(nunca.group(1).splitlines()) >= 3, nome
        assert "permissionMode" not in texto


@pytest.mark.parametrize("mid", sorted(NEGOCIO))
def test_claude_md_missao_entradas_e_blocos_de_accao(mid):
    texto = (MODELOS / mid / "CLAUDE.md").read_text("utf-8")
    assert texto.count("{{MISSAO}}") == 1
    assert "=====ACCAO=====" in texto and "=====FIM ACCAO=====" in texto
    for campo in ('"tipo"', '"origem"', '"assunto"', '"corpo"', "`folha`", "`valores`", "`texto`"):
        assert campo in texto, campo
    for tipo in ("email_rascunho", "email_responder", "folha_linha", "aviso_dono"):
        assert tipo in texto
    assert "entrada/mensagens/" in texto and "Lê primeiro `entrada/accoes.md`" in texto   # C2
    assert ("entrada/conhecimento/" in texto) is (mid == "atendimento")
    assert ("entrada/perfil-cliente-ideal.md" in texto) is (mid == "comercial")
    readme = agentes.frontmatter((MODELOS / mid / "README.md").read_text("utf-8"))
    assert readme["lead"] == NEGOCIO[mid][0] and not (MODELOS / mid / ".claude").exists()
