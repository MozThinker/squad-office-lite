"""Credenciais do claude (`claude.env` ao lado do office.yaml): lidas pelo runner com O_NOFOLLOW, só 0600 e do
utilizador, uma de ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN, passadas só no env do `claude` (nunca argv, meta,
logs). Usa o `claude` falso em modo FAKE_CLAUDE_EXIGE_CRED / FAKE_CLAUDE_CRED (regista só sha256 do valor)."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import lancador, office_runner, officecfg

API = "sk-ant-api03-SEGREDOapi_0123456789-abc"
OAUTH = "sk-ant-oat01-SEGREDOoauth_9876543210-xyz"


def sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(tmp_path / "argv.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_CRED", str(tmp_path / "cred.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_EXIGE_CRED", "1")
    c.pedido(cfg)
    linhas = office_runner.uma_vez(cfg, str(path))
    return cfg, path, tmp_path, c.feito(cfg)["task_id"], linhas


def cred_env(tmp: Path, texto: str, modo: int = 0o600) -> Path:
    f = tmp / "claude.env"
    f.write_text(texto, encoding="utf-8")
    f.chmod(modo)
    return f


def vistos(tmp: Path) -> list:
    p = tmp / "cred.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def sem_segredo(tmp: Path, *valores: str) -> None:
    """Nenhum ficheiro (meta, claude.log, heartbeat, logs, argv, stdin, worktree) leva o valor, salvo o claude.env."""
    for f in tmp.rglob("*"):
        if f.is_file() and not f.is_symlink() and f.name != "claude.env":
            dados = f.read_bytes()
            for v in valores:
                assert v.encode() not in dados, f


def test_caminho_ao_lado_da_config(ctx):
    _, path, tmp, _, _ = ctx
    assert officecfg.credenciais_claude(path) == tmp / "claude.env"


def test_chave_de_api_so_no_env(ctx, monkeypatch):
    cfg, path, tmp, tid, _ = ctx
    cred_env(tmp, f"# chave de API\n\nANTHROPIC_API_KEY={API}\n")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "herdado-do-ambiente")   # sai: sem ambiguidade
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "concluída"
    assert vistos(tmp) == [{"ANTHROPIC_API_KEY": sha(API)}]
    (argv,) = [json.loads(x) for x in (tmp / "argv.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any(API in a for a in argv)
    sem_segredo(tmp, API)
    assert "ANTHROPIC_API_KEY" not in os.environ   # o runner não a põe no próprio ambiente


def test_token_oauth_com_aspas_so_no_env(ctx, monkeypatch):
    cfg, path, tmp, tid, _ = ctx
    cred_env(tmp, f"CLAUDE_CODE_OAUTH_TOKEN='{OAUTH}'\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "herdada-do-ambiente")
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "concluída"
    assert vistos(tmp) == [{"CLAUDE_CODE_OAUTH_TOKEN": sha(OAUTH)}]
    sem_segredo(tmp, OAUTH)


def test_retoma_usa_as_mesmas_credenciais(ctx):
    cfg, path, tmp, tid, _ = ctx
    cred_env(tmp, f'ANTHROPIC_API_KEY="{API}"\n')
    lancador.correr(cfg, tid, cfg_path=str(path))
    m = lancador.correr(cfg, tid, retomar=True, cfg_path=str(path))
    argvs = [json.loads(x) for x in (tmp / "argv.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "--resume" in argvs[-1] and m["status"] == "concluída"
    assert vistos(tmp) == [{"ANTHROPIC_API_KEY": sha(API)}] * 2
    sem_segredo(tmp, API)


def test_ausente_usa_o_login_existente_e_avisa_no_journal(ctx, monkeypatch):
    cfg, path, tmp, tid, linhas = ctx
    assert any("sem claude.env" in x for x in linhas)
    monkeypatch.delenv("FAKE_CLAUDE_EXIGE_CRED")
    assert lancador.correr(cfg, tid, cfg_path=str(path))["status"] == "concluída"
    assert vistos(tmp) == [{}]
    # o que já estiver no ambiente (ex.: runner.env) continua a passar, como antes
    monkeypatch.setenv("ANTHROPIC_API_KEY", API)
    lancador.correr(cfg, tid, cfg_path=str(path))
    assert vistos(tmp)[-1] == {"ANTHROPIC_API_KEY": sha(API)}


def test_ausente_com_claude_que_exige_credenciais_fica_em_erro(ctx):
    cfg, path, tmp, tid, _ = ctx
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "erro" and vistos(tmp) == [{}]


def test_presente_nao_avisa_no_journal(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 1)
    cred_env(tmp_path, f"ANTHROPIC_API_KEY={API}\n")
    c.pedido(cfg)
    linhas = office_runner.uma_vez(cfg, str(path))
    assert any("lançado" in x for x in linhas) and not any("claude.env" in x for x in linhas)


@pytest.mark.parametrize("modo", [0o644, 0o640, 0o604, 0o660, 0o700, 0o601])
def test_permissoes_abertas_recusadas(ctx, modo):
    cfg, path, tmp, tid, _ = ctx
    cred_env(tmp, f"ANTHROPIC_API_KEY={API}\n", modo)
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    if modo & 0o077:
        assert m["status"] == "erro" and "permissões abertas" in m["result"] and "chmod 600" in m["result"]
        assert not (tmp / "argv.jsonl").exists()   # o claude nem é lançado
    else:   # 0700: só o dono (o bit x não abre a terceiros)
        assert m["status"] == "concluída"
    sem_segredo(tmp, API)


def test_symlink_recusado(ctx):
    cfg, path, tmp, tid, _ = ctx
    alvo = tmp / "alvo.env"
    alvo.write_text(f"ANTHROPIC_API_KEY={API}\n", encoding="utf-8")
    alvo.chmod(0o600)
    (tmp / "claude.env").symlink_to(alvo)
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "erro" and "ficheiro normal" in m["result"]
    assert not (tmp / "argv.jsonl").exists()


def test_pasta_e_fifo_recusados(ctx):
    cfg, path, tmp, tid, _ = ctx
    f = tmp / "claude.env"
    f.mkdir(mode=0o700)
    assert "ficheiro normal" in lancador.correr(cfg, tid, cfg_path=str(path))["result"]
    f.rmdir()
    os.mkfifo(f, 0o600)
    with pytest.raises(lancador.Falha, match="ficheiro normal"):   # O_NONBLOCK: não fica à espera de um escritor
        lancador.credenciais(f)


def test_dono_errado_recusado(tmp_path, monkeypatch):
    f = cred_env(tmp_path, f"ANTHROPIC_API_KEY={API}\n")
    real = os.getuid()
    monkeypatch.setattr(lancador.os, "getuid", lambda: real + 1)
    with pytest.raises(lancador.Falha, match="dono errado") as e:
        lancador.credenciais(f)
    assert API not in str(e.value)


@pytest.mark.parametrize("texto", [
    f"OPENAI_API_KEY={API}\n",                                   # chave desconhecida
    f"export ANTHROPIC_API_KEY={API}\n",
    f"ANTHROPIC_API_KEY={API}\nCLAUDE_CODE_OAUTH_TOKEN={OAUTH}\n",  # as duas: ambíguo
    f"ANTHROPIC_API_KEY={API}\nANTHROPIC_API_KEY={API}\n",
    "ANTHROPIC_API_KEY=\n", "# só comentário\n", "",
    f"ANTHROPIC_API_KEY={API} resto\n", f"ANTHROPIC_API_KEY = {API}\n",
    f"ANTHROPIC_API_KEY='{API}\n", f"ANTHROPIC_API_KEY='{API}\"\n", f"ANTHROPIC_API_KEY=a'{API}\n",
    f"ANTHROPIC_API_KEY={API}\x00\n", f"ANTHROPIC_API_KEY={API}ç\n",
    f"ANTHROPIC_API_KEY={'x' * 4097}\n",
])
def test_conteudo_invalido_recusado_sem_mostrar_o_valor(tmp_path, texto):
    f = cred_env(tmp_path, texto)
    with pytest.raises(lancador.Falha, match="credenciais do Claude inválidas") as e:
        lancador.credenciais(f)
    assert API not in str(e.value) and OAUTH not in str(e.value) and "x" * 20 not in str(e.value)


def test_ficheiro_grande_recusado(tmp_path):
    f = cred_env(tmp_path, "#" * (lancador.CRED_MAX + 1) + f"\nANTHROPIC_API_KEY={API}\n")
    with pytest.raises(lancador.Falha, match="inválidas"):
        lancador.credenciais(f)


def test_formatos_aceites(tmp_path):
    for texto, esperado in ((f"ANTHROPIC_API_KEY={API}", {"ANTHROPIC_API_KEY": API}),
                            (f"  # c\r\nCLAUDE_CODE_OAUTH_TOKEN=\"{OAUTH}\"\r\n", {"CLAUDE_CODE_OAUTH_TOKEN": OAUTH}),
                            (f"\n\nANTHROPIC_API_KEY='{API}'\n# fim\n", {"ANTHROPIC_API_KEY": API})):
        assert lancador.credenciais(cred_env(tmp_path, texto)) == esperado
    assert lancador.credenciais(tmp_path / "nao-existe.env") is None


def test_env_claude_sem_ambiguidade(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "b")
    env = lancador.env_claude({"CLAUDE_CODE_OAUTH_TOKEN": "c"})
    assert "ANTHROPIC_API_KEY" not in env and env["CLAUDE_CODE_OAUTH_TOKEN"] == "c"
    # sem claude.env: a do ambiente do runner (runner.env), só uma; a chave de API ganha, como no claude -p
    env = lancador.env_claude(None)
    assert env["ANTHROPIC_API_KEY"] == "a" and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    env = lancador.env_claude(None)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "b" and "ANTHROPIC_API_KEY" not in env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")   # vazia não conta
    assert lancador.env_claude(None)["CLAUDE_CODE_OAUTH_TOKEN"] == "b"
    assert "ANTHROPIC_API_KEY" not in lancador.env_claude(None)


SEGREDOS_DO_RUNNER = {"OFFICE_TELEGRAM_TOKEN": "123:tg", "OFFICE_X": "x", "GH_TOKEN": "ghp_x", "GITHUB_TOKEN": "gho_x",
                      "QUALQUER": "1"}


def test_env_claude_lista_branca(monkeypatch):
    # C3: só ENV_PASSA (as que existirem), o travão dos processos em fundo e uma credencial
    for k, v in SEGREDOS_DO_RUNNER.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    env = lancador.env_claude(None)
    assert set(env) == {k for k in lancador.ENV_PASSA if k in os.environ} | {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"}
    assert env["TZ"] == "UTC" and "TMPDIR" not in env and env["PATH"] == os.environ["PATH"]
    assert lancador.ENV_PASSA == ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL", "TERM",
                                  "TMPDIR", "TZ")
    env = lancador.env_claude({"ANTHROPIC_API_KEY": "k"})
    assert env["ANTHROPIC_API_KEY"] == "k" and not set(SEGREDOS_DO_RUNNER) & set(env)


def test_correr_nao_passa_segredos_do_runner_ao_claude(ctx, monkeypatch):
    # C3 de ponta a ponta: um claude de teste grava os nomes do seu ambiente e corre o falso
    cfg, path, tmp, tid, _ = ctx
    for k, v in SEGREDOS_DO_RUNNER.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)   # do runner.env: perde para o claude.env
    cred_env(tmp, f"ANTHROPIC_API_KEY={API}\n")
    nomes = tmp / "env.json"
    pasta = tmp / "bin-env"
    pasta.mkdir()
    (pasta / "claude").write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"open({str(nomes)!r}, 'w').write(json.dumps(sorted(os.environ)))\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, {str(c.BIN / 'claude')!r}, *sys.argv[1:]])\n",
        encoding="utf-8")
    (pasta / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(pasta), os.environ["PATH"]]))
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "concluída", m["result"]
    vistos_env = set(json.loads(nomes.read_text(encoding="utf-8")))
    assert not set(SEGREDOS_DO_RUNNER) & vistos_env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in vistos_env and "ANTHROPIC_API_KEY" in vistos_env
    assert vistos(tmp) == [{"ANTHROPIC_API_KEY": sha(API)}]


def test_cli_correr_le_o_claude_env_da_config(ctx):
    # o processo destacado (office_runner.py correr) é quem lê: o caminho vem do --config. As FAKE_CLAUDE_* não
    # passam a lista branca: vão no claude embrulhado à frente do PATH
    _, path, tmp, tid, _ = ctx
    cred_env(tmp, f"ANTHROPIC_API_KEY={API}\n")
    falso = c.claude_com(tmp, "cred", FAKE_CLAUDE_ARGV=str(tmp / "argv.jsonl"),
                         FAKE_CLAUDE_CRED=str(tmp / "cred.jsonl"), FAKE_CLAUDE_EXIGE_CRED="1")
    env = dict(os.environ, PATH=os.pathsep.join([str(falso), os.environ["PATH"]]))
    r = subprocess.run([sys.executable, str(c.RUNNER), "correr", tid, "--config", str(path)],
                       capture_output=True, text=True, timeout=60, env=env, check=False)
    assert r.returncode == 0 and "concluída" in r.stdout, r.stderr
    assert API not in r.stdout + r.stderr
    assert vistos(tmp) == [{"ANTHROPIC_API_KEY": sha(API)}]
    sem_segredo(tmp, API)


def test_cli_correr_com_permissoes_abertas_fica_em_erro(ctx):
    cfg, path, tmp, tid, _ = ctx
    cred_env(tmp, f"ANTHROPIC_API_KEY={API}\n", 0o644)
    falso = c.claude_com(tmp, "cred", FAKE_CLAUDE_ARGV=str(tmp / "argv.jsonl"), FAKE_CLAUDE_EXIGE_CRED="1")
    env = dict(os.environ, PATH=os.pathsep.join([str(falso), os.environ["PATH"]]))
    r = subprocess.run([sys.executable, str(c.RUNNER), "correr", tid, "--config", str(path)],
                       capture_output=True, text=True, timeout=60, env=env, check=False)
    assert "erro" in r.stdout and API not in r.stdout + r.stderr
    m = json.loads((officecfg.caminho(cfg, "tasks") / tid / "meta.json").read_text(encoding="utf-8"))
    assert m["status"] == "erro" and "chmod 600 claude.env" in m["result"]
    assert not (tmp / "argv.jsonl").exists()   # o claude nem é lançado
    assert str(tmp) not in m["result"]   # a meta é lida pelo Office: sem caminhos


# ---------------------------------------------------------------- formato do valor (CA-11…CA-14; = install.sh)

FUMO = "sk-ant-fumo-0123456789abcdef"   # o da CI de fumo


@pytest.mark.parametrize("texto, esperado", [
    (f"CLAUDE_CODE_OAUTH_TOKEN={OAUTH}\n", {"CLAUDE_CODE_OAUTH_TOKEN": OAUTH}),
    (f"ANTHROPIC_API_KEY={API}\n", {"ANTHROPIC_API_KEY": API}),
    (f"ANTHROPIC_API_KEY={FUMO}\n", {"ANTHROPIC_API_KEY": FUMO}),
    ("ANTHROPIC_API_KEY=sk-ant-x\n", {"ANTHROPIC_API_KEY": "sk-ant-x"}),
])
def test_formato_valido_aceite(tmp_path, texto, esperado):
    assert lancador.credenciais(cred_env(tmp_path, texto)) == esperado


@pytest.mark.parametrize("valor", [
    "abc#def",                              # o código do browser, não o token
    "SEGREDOabc#def0123",
    API,                                    # chave de API na variável do token
    "sk-ant-oat01-",                        # sem corpo
    "sk-ant-oat02-SEGREDO0123",
    "sk-ant-oat01-SEGREDO.0123",
    "xsk-ant-oat01-SEGREDO0123",
])
def test_oauth_fora_do_formato(tmp_path, valor):
    f = cred_env(tmp_path, f"CLAUDE_CODE_OAUTH_TOKEN={valor}\n")
    with pytest.raises(lancador.Falha) as e:
        lancador.credenciais(f)
    msg = str(e.value)
    assert msg == ("credenciais do Claude inválidas (claude.env: copia a linha que começa por sk-ant-oat01-, "
                   "não o código do browser)")
    assert "SEGREDO" not in msg   # mensagem fixa: nunca o valor


@pytest.mark.parametrize("valor", [OAUTH, "sk-ant-oat01-x", "sk-ant-oatSEGREDO"])
def test_token_da_subscricao_na_chave_de_api(tmp_path, valor):
    with pytest.raises(lancador.Falha) as e:
        lancador.credenciais(cred_env(tmp_path, f"ANTHROPIC_API_KEY={valor}\n"))
    msg = str(e.value)
    assert msg.startswith("credenciais do Claude inválidas (claude.env: isso é um token da subscrição")
    assert msg.endswith("usa CLAUDE_CODE_OAUTH_TOKEN=…; ver README, «Credenciais do Claude»)") and valor not in msg
    assert "install.sh" not in msg   # o instalador já não pede o token (C1)


@pytest.mark.parametrize("valor", ["abc#def", "sk-ant-", "sk-SEGREDO0123", "sk-ant-SEGREDO.0123", "sk-ant-a:b",
                                   "SK-ANT-SEGREDO0123"])
def test_chave_de_api_fora_do_formato(tmp_path, valor):
    with pytest.raises(lancador.Falha) as e:
        lancador.credenciais(cred_env(tmp_path, f"ANTHROPIC_API_KEY={valor}\n"))
    msg = str(e.value)
    assert msg == ("credenciais do Claude inválidas (claude.env: a chave de API tem de começar por sk-ant- "
                   "(só letras, números, _ e -))")
    assert "SEGREDO" not in msg   # mensagem fixa: nunca o valor


def test_formato_errado_via_correr_fica_em_erro_sem_valor_nem_caminhos(ctx):
    cfg, path, tmp, tid, _ = ctx
    cred_env(tmp, "CLAUDE_CODE_OAUTH_TOKEN=SEGREDOcodigo#browser\n")
    m = lancador.correr(cfg, tid, cfg_path=str(path))
    assert m["status"] == "erro" and "sk-ant-oat01-" in m["result"]
    assert str(tmp) not in m["result"] and m["pr"] is None and m["pr_url"] is None
    assert not (tmp / "argv.jsonl").exists()   # o claude nem é lançado
    sem_segredo(tmp, "SEGREDOcodigo#browser")


def test_regras_iguais_as_do_install_sh():
    # C1 (parte do runner): o install.sh valida a chave de API com a mesma expressão, ancorada (o token da
    # subscrição já não passa pelo instalador: só o runner o valida, num claude.env escrito à mão)
    texto = (c.RAIZ / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^RX_CRED_API='([^']*)'$", texto, re.MULTILINE)
    assert m and m.group(1) == f"^{lancador.CRED_API.pattern}$"


AVISO = "sem claude.env nem credencial no runner.env: o claude usa o login existente"


def test_aviso_so_sem_claude_env_nem_credencial_no_ambiente(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    path = tmp_path / "office.yaml"
    assert lancador.aviso_credenciais(str(path)) == AVISO
    assert "install.sh" not in AVISO   # o token da subscrição não se cria pelo instalador
    for k in lancador.CRED_CHAVES:   # credencial no runner.env: o claude usa-a, nada a avisar
        monkeypatch.setenv(k, "x")
        assert lancador.aviso_credenciais(str(path)) is None
        monkeypatch.setenv(k, "")   # vazia não conta
        assert lancador.aviso_credenciais(str(path)) == AVISO
        monkeypatch.delenv(k)
    cred_env(tmp_path, f"ANTHROPIC_API_KEY={API}\n")
    assert lancador.aviso_credenciais(str(path)) is None


def test_lancamento_com_credencial_no_runner_env_nao_avisa(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path)
    cfg, path = c.config(tmp_path)
    c.repo(cfg)
    monkeypatch.setattr(lancador, "destacar", lambda *a, **k: 4242)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    c.pedido(cfg)
    linhas = office_runner.uma_vez(cfg, str(path))
    assert any("lançado" in x for x in linhas) and not any("sem claude.env" in x for x in linhas)
