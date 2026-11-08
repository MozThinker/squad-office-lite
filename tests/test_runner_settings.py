"""C2: o runner passa sempre ao `claude` um `--settings` com as negações por omissão (credenciais, /proc, filas,
config, código do runner e o que o utilizador executa), juntado com o `runner.settings` do utilizador (listas
concatenadas; a base nunca sai), gravado em <dados>/runner/settings/<tid>.json (0600): em argv só vai o caminho.
Inválido ⇒ a tarefa fecha em «erro» sem o conteúdo do ficheiro e o `claude` não arranca."""
import json
import os
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner


def abs_(p) -> str:
    return "//" + str(p)[1:]


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(tmp_path / "argv.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_SETTINGS", str(tmp_path / "settings.jsonl"))
    return tmp_path


def preparar(tmp, **kw):
    cfg, path = c.config(tmp, **kw)
    c.repo(cfg)
    c.pedido(cfg)
    office_runner.uma_vez(cfg, str(path))
    return cfg, path, c.feito(cfg)["task_id"]


def argvs(tmp) -> list:
    p = tmp / "argv.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


CRED_HOME = [".claude/.credentials.json", ".git-credentials", ".netrc", ".docker/config.json", ".config/systemd/**"]
EXECUTA = [".claude/settings.json", ".claude/settings.local.json", ".bashrc", ".profile", ".bash_profile",
           ".local/bin/**"]


def test_base_tem_exactamente_as_negacoes(ctx):
    cfg, path = c.config(ctx)
    casa, dados, home = ctx, ctx / "dados", ctx / "casa"
    base = json.loads(lancador.settings_juntos(cfg, str(path)))
    assert base == {"permissions": {"deny": [
        f"Read({abs_(casa / 'claude.env')})", f"Read({abs_(casa / 'runner.env')})", f"Read({abs_(casa / '.env')})",
        "Read(//**/.env)", "Read(//**/.env.*)", f"Read({abs_(home / '.config/gh/**')})",
        f"Read({abs_(home / '.ssh/**')})",
        *[f"Read({abs_(home / x)})" for x in CRED_HOME], "Read(//proc/**)",
        f"Edit({abs_(casa / 'claude.env')})", f"Edit({abs_(casa / 'runner.env')})", f"Edit({abs_(casa / '.env')})",
        f"Edit({abs_(path)})",
        *[f"Edit({abs_(home / x)})" for x in CRED_HOME + EXECUTA],
        f"Edit({abs_(dados / 'quadro-lancar/**')})", f"Edit({abs_(dados / 'decisoes-fila/**')})",
        f"Edit({abs_(dados / 'decisoes/**')})", f"Edit({abs_(dados / 'quadro/**')})",
        f"Edit({abs_(dados / 'runner/**')})", f"Edit({abs_(c.RAIZ / '**')})",
    ], "blockWritesOutsideWorkingDirectories": True}}
    regras = base["permissions"]["deny"]
    assert len(regras) == len(set(regras)) and not any(r.startswith("Write(") for r in regras)
    assert all(r.startswith(("Read(//", "Edit(//")) for r in regras)


def test_base_sem_config_e_com_settings(ctx):
    cfg, _ = c.config(ctx, settings=str(ctx / "s.json"))
    regras = lancador.settings_base(cfg, None)["permissions"]["deny"]
    assert not any("claude.env" in r or "office.yaml" in r for r in regras)   # sem casa
    assert regras[-1] == f"Edit({abs_(ctx / 's.json')})"                      # o próprio runner.settings
    assert f"Edit({abs_(ctx / 'dados' / 'quadro-lancar' / '**')})" in regras


def test_base_segue_a_config(tmp_path, monkeypatch):
    # nada fixo: outra casa e outros dados ⇒ outras regras
    c.ambiente(monkeypatch, tmp_path)
    outra = tmp_path / "outra"
    outra.mkdir()
    cfg, path = c.config(outra)
    regras = lancador.settings_base(cfg, str(path))["permissions"]["deny"]
    assert f"Edit({abs_(outra / 'office.yaml')})" in regras
    assert f"Edit({abs_(outra / 'dados' / 'quadro' / '**')})" in regras
    assert not any(abs_(tmp_path / "dados") in r for r in regras)


def test_negacoes_da_casa_e_de_proc(ctx):
    # credenciais lidas e alteradas; hooks, arranque da shell e ~/.local/bin só alterados (execução de código)
    cfg, _ = c.config(ctx)
    regras = lancador.settings_base(cfg, None)["permissions"]["deny"]
    home = Path.home()
    for x in CRED_HOME:
        assert f"Read({abs_(home / x)})" in regras and f"Edit({abs_(home / x)})" in regras
    for x in EXECUTA:
        assert f"Edit({abs_(home / x)})" in regras and f"Read({abs_(home / x)})" not in regras
    assert "Read(//proc/**)" in regras and not any(r.startswith("Edit(//proc") for r in regras)


def vistas(tmp) -> list:
    # o que o claude falso viu no ficheiro de --settings, por corrida: {"texto", "modo", "uid"}
    p = tmp / "settings.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def settings_vistas(tmp) -> dict:
    return json.loads(vistas(tmp)[-1]["texto"])


def test_correr_passa_a_base_num_ficheiro(ctx):
    cfg, path, tid = preparar(ctx)
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "concluída"
    (argv,) = argvs(ctx)
    f = Path(argv[argv.index("--settings") + 1])
    assert f == ctx / "dados" / "runner" / "settings" / f"{tid}.json"
    (vista,) = vistas(ctx)
    assert vista["texto"] == lancador.settings_juntos(cfg, str(path))
    assert json.loads(vista["texto"]) == lancador.settings_base(cfg, str(path))
    assert vista["modo"] == 0o600 and vista["uid"] == os.getuid()
    assert not os.path.lexists(f)   # apagado ao fechar a meta
    assert not any(a.startswith("{") for a in argv)   # nenhum JSON em argv
    assert f"Edit({abs_(ctx / 'dados' / 'runner' / '**')})" in settings_vistas(ctx)["permissions"]["deny"]


def test_settings_com_env_nunca_em_argv_e_reescrito_na_retoma(ctx):
    s = ctx / "travoes.json"
    s.write_text(json.dumps({"env": {"SEGREDO_X": "valor-secreto-1"}}), encoding="utf-8")
    cfg, path, tid = preparar(ctx, settings=str(s))
    lancador.correr(cfg, tid, cfg_path=str(path))
    f = ctx / "dados" / "runner" / "settings" / f"{tid}.json"
    assert settings_vistas(ctx)["env"] == {"SEGREDO_X": "valor-secreto-1"} and not os.path.lexists(f)
    # no lugar do ficheiro (já apagado) há um symlink para fora e o runner.settings mudou: a retoma recria-o, sem o
    # seguir, e apaga-o no fim
    fora = ctx / "fora.json"
    fora.write_text("intacto", encoding="utf-8")
    f.symlink_to(fora)
    s.write_text(json.dumps({"env": {"SEGREDO_X": "valor-secreto-2"}}), encoding="utf-8")
    lancador.correr(cfg, tid, retomar=True, cfg_path=str(path))
    assert fora.read_text(encoding="utf-8") == "intacto" and not os.path.lexists(f)
    assert settings_vistas(ctx)["env"] == {"SEGREDO_X": "valor-secreto-2"}
    assert vistas(ctx)[-1]["modo"] == 0o600
    for argv in argvs(ctx):
        assert not any("valor-secreto" in a for a in argv)
        assert argv[argv.index("--settings") + 1] == str(f)


def test_settings_por_gravar_fica_em_erro_sem_lancar(ctx):
    cfg, path, tid = preparar(ctx)
    (ctx / "dados" / "runner" / "settings").write_text("não é pasta", encoding="utf-8")
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "erro" and m["result"] == "erro a gravar as settings do claude no host"
    assert argvs(ctx) == []


def test_runner_settings_e_juntado(ctx):
    s = ctx / "travoes.json"
    s.write_text(json.dumps({"permissions": {"deny": ["Bash(rm:*)"], "allow": ["Bash(cargo *)"],
                                             "blockWritesOutsideWorkingDirectories": False, "defaultMode": "x"},
                             "hooks": {"PreToolUse": []}, "model": "y"}), encoding="utf-8")
    cfg, path, tid = preparar(ctx, settings=str(s))
    lancador.correr(cfg, tid, cfg_path=str(path))
    j = settings_vistas(ctx)
    base = lancador.settings_base(cfg, str(path))["permissions"]["deny"]
    assert j["permissions"]["deny"] == base + ["Bash(rm:*)"]
    assert j["permissions"]["allow"] == ["Bash(cargo *)"] and "ask" not in j["permissions"]
    assert j["permissions"]["blockWritesOutsideWorkingDirectories"] is True   # nunca desliga
    assert j["permissions"]["defaultMode"] == "x" and j["hooks"] == {"PreToolUse": []} and j["model"] == "y"
    assert f"Edit({abs_(s)})" in base


def test_runner_settings_vazio_e_igual_a_base(ctx):
    s = ctx / "vazio.json"
    s.write_text("{}", encoding="utf-8")
    cfg, path = c.config(ctx, settings=str(s))
    assert json.loads(lancador.settings_juntos(cfg, str(path))) == lancador.settings_base(cfg, str(path))


SEGREDO = "SEGREDOdoficheiro"


@pytest.mark.parametrize("conteudo, regra", [
    (None, "ficheiro ilegível"),
    ("pasta", "ficheiro ilegível"),
    ("symlink", "ficheiro ilegível"),
    (b"x" * (64 * 1024 + 1), "mais de 64 KB"),
    (f'{{"a": "{SEGREDO}",'.encode(), "JSON inválido"),
    (b"\xff\xfe", "JSON inválido"),
    (f'["{SEGREDO}"]'.encode(), "tem de ser um objecto JSON"),
    (f'{{"permissions": ["{SEGREDO}"]}}'.encode(), "permissions tem de ser um objecto"),
    (f'{{"permissions": {{"deny": "{SEGREDO}"}}}}'.encode(), "permissions.deny tem de ser uma lista de textos"),
    (f'{{"permissions": {{"allow": ["{SEGREDO}", 1]}}}}'.encode(), "permissions.allow tem de ser uma lista de textos"),
    (b'{"permissions": {"ask": [null]}}', "permissions.ask tem de ser uma lista de textos"),
])
def test_runner_settings_invalido_fica_em_erro_sem_lancar(ctx, conteudo, regra):
    s = ctx / "travoes.json"
    if conteudo == "pasta":
        s.mkdir()
    elif conteudo == "symlink":
        (ctx / "real.json").write_text("{}", encoding="utf-8")
        s.symlink_to(ctx / "real.json")
    elif conteudo is not None:
        s.write_bytes(conteudo)
    cfg, path, tid = preparar(ctx, settings=str(s))
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "erro" and m["result"] == f"runner.settings inválido: {regra}"
    assert SEGREDO not in m["result"] and str(ctx) not in m["result"]
    assert argvs(ctx) == []   # o claude nem arranca
    with pytest.raises(lancador.Falha):
        lancador.settings_juntos(cfg, str(path))


def test_64_kb_exactos_aceites(ctx):
    s = ctx / "grande.json"
    corpo = '{"x": "' + "a" * (64 * 1024 - 9) + '"}'
    s.write_text(corpo, encoding="utf-8")
    assert os.path.getsize(s) == 64 * 1024
    cfg, path = c.config(ctx, settings=str(s))
    assert json.loads(lancador.settings_juntos(cfg, str(path)))["x"].startswith("a")


def test_base_nao_usa_a_casa_real(ctx):
    cfg, path = c.config(ctx)
    texto = lancador.settings_juntos(cfg, str(path))
    assert str(Path(os.path.expanduser("~"))) == str(ctx / "casa") and abs_(ctx / "casa") in texto
