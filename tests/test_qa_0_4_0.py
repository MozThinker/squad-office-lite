"""QA 0.4.0 (squads e agentes): o que os testes da implementação não cobriam.

- critérios 2 (CA-26) e 3 (CA-32/36) de ponta a ponta também na disposição de pastas do modo Docker (config só de
  leitura em /config, segredos em OFFICE_SEGREDOS_DIR, OFFICE_MODO=docker); o critério 1 (CA-17) já corre nos dois;
- «no fuso» (CA-28, CA-36) com um fuso que não é o UTC: o `office.yaml` dos testes usa Europe/Lisbon, que em novembro
  coincide com o UTC, por isso um engano de fuso passava despercebido;
- CA-22: um surrogate isolado (categoria Cs) e o ZWJ sozinho.
`NOW` fixo, `claude` falso, repos locais; nada de rede nem do relógio."""
import json
import shutil
from pathlib import Path

import pytest

from tests import test_runner_agentes_comum as a
from tests import test_runner_agentes_ia as ia
from tests.test_officecfg import com
from tests.test_runner_comum import lancador, office_runner, officecfg

from officecfg import ConfigErro, validar

NOW = a.NOW   # quarta-feira 18/11/2026, 10:00 UTC (= 02:00 em Los Angeles)
HORA = 3600


def _fake(monkeypatch, tmp: Path) -> None:
    for k, f in (("FAKE_CLAUDE_ARGV", "argv.jsonl"), ("FAKE_CLAUDE_STDIN", "stdin.txt"), ("FAKE_CLAUDE_CWD", "cwd.jsonl"),
                 ("FAKE_CLAUDE_RESULTADO", "resultado.txt")):
        monkeypatch.setenv(k, str(tmp / f))


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """Disposição do modo Docker (como o fixture `layout` de test_runner_agentes), com o claude falso das acções IA."""
    a.c.ambiente(monkeypatch, tmp_path)
    cfg, path = a.c.config(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "segredos").mkdir(mode=0o700)
    path = Path(shutil.move(str(path), tmp_path / "config" / "office.yaml"))
    path.chmod(0o444)
    monkeypatch.setenv("OFFICE_MODO", "docker")
    monkeypatch.setenv("OFFICE_SEGREDOS_DIR", str(tmp_path / "segredos"))
    monkeypatch.setattr(lancador, "destacar", lambda *x, **k: 4242)
    a.c.repo(cfg)
    _fake(monkeypatch, tmp_path)
    return cfg, path, tmp_path


def test_criterio2_no_modo_docker(docker):
    ia.test_criterio2_rascunho_criar_ensaio_activar(docker)


def test_criterio3_no_modo_docker(docker):
    ia.test_criterio3_squad_por_modelo_e_rotina_so_depois_de_aprovada(docker)


# ---------------------------------------------------------------- «no fuso» com um fuso diferente do UTC

@pytest.fixture
def la(tmp_path, monkeypatch):
    a.c.ambiente(monkeypatch, tmp_path)
    monkeypatch.delenv("OFFICE_MODO", raising=False)
    monkeypatch.delenv("OFFICE_SEGREDOS_DIR", raising=False)
    _, path = a.c.config(tmp_path)
    path.write_text(path.read_text("utf-8").replace("fuso: Europe/Lisbon", "fuso: America/Los_Angeles"), "utf-8")
    cfg, _ = officecfg.carregar(path, estrito=True)
    assert cfg["fuso"] == "America/Los_Angeles"
    a.c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *x, **k: 4242)
    _fake(monkeypatch, tmp_path)
    return cfg, path, tmp_path


def passar(ctx, t):
    return office_runner.uma_vez(ctx[0], str(ctx[1]), t)


def test_rotina_hora_e_dia_no_fuso_da_config(la):
    """CA-36: 09:00 de quarta em Los Angeles é 17:00 UTC; às 10:00 UTC (02:00 lá) ainda não é hora."""
    cfg = la[0]
    args = {"squad": "dev", "projecto": "exemplo", "hora": "09:00", "dias": [3], "instrucoes": "Revê o README.",
            "tecto_usd": "0.50", "max_turnos": 5}
    s = a.pedido(cfg, "rotina_pedir", args, 0, NOW)
    passar(la, NOW)
    assert a.feito(cfg, s) == {"ok": True}
    a.decidir(cfg, "rotina-dev-r1.md")
    passar(la, NOW + 60)
    assert a.rotinas_de(cfg)[0]["estado"] == "aprovada" and a.c.tarefas(cfg) == []   # 02:01 em LA
    passar(la, NOW + 7 * HORA)   # 09:00 de quarta em LA
    assert len(a.c.tarefas(cfg)) == 1 and a.rotinas_de(cfg)[0]["ultima"] == "2026-11-18"
    passar(la, NOW + 15 * HORA)  # 01:00 UTC de quinta, ainda quarta (17:00) em LA: uma vez por dia
    assert len(a.c.tarefas(cfg)) == 1


@pytest.mark.parametrize("em, ok", [("2026-11-18T07:30:00+00:00", True),    # 23:30 de 17/11 em LA: ontem
                                    ("2026-11-18T09:00:00+00:00", False)])  # 01:00 de 18/11 em LA: hoje
def test_tecto_diario_de_ia_no_fuso_da_config(la, em, ok):
    """CA-28: o dia do tecto de IA é o do fuso (NOW = 02:00 de 18/11 em LA)."""
    cfg = la[0]
    livro = officecfg.caminho(cfg, "runner") / "agentes-ia.jsonl"
    livro.parent.mkdir(parents=True, exist_ok=True)
    livro.write_text(json.dumps({"em": em, "accao": "agente_ensaio", "custo_usd": 9.8, "tecto": 2.0}) + "\n", "utf-8")
    ia.resultado(la, ia.RASCUNHO_IA)
    s = a.pedido(cfg, "agente_rascunho", {"squad": "dev", "nome": "um-a", "descricao": "x"}, 0, NOW)
    passar(la, NOW)
    assert a.feito(cfg, s) == ({"ok": True} if ok else {"erro": "tecto diário de IA atingido"})


# ---------------------------------------------------------------- CA-22: Cs e ZWJ sozinho

def test_surrogate_isolado_recusado_e_zwj_aceite():
    with pytest.raises(ConfigErro) as e:
        validar(com(marca="Marca\ud800"))
    assert str(e.value) == "marca: caracteres invisíveis não permitidos"
    assert validar(com(marca="a‍b"))["marca"] == "a‍b"
