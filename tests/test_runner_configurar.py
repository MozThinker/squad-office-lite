"""AC-f1…f7 e D2: `runner/configurar.py` (modo Docker). Raízes, /proxy, /config e OFFICE_SEGREDOS_DIR em tmp_path;
`htpasswd` e `gh` falsos (registam argv e um sha256 do stdin, nunca o valor); perguntas por respostas simuladas.
O root (chown + largar privilégios) só com `os.geteuid` simulado."""
import collections
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests import test_runner_comum as c
from tests.test_runner_comum import officecfg

conf = importlib.import_module("configurar")   # runner/ entra no sys.path com o test_runner_comum

SENHA = "palavra-passe-muito-boa"
API = "sk-ant-api03-CONFapi_0123456789-abc"
OAUTH = "sk-ant-oat01-CONFoauth_9876543210-xyz"
GH = "github_pat_" + "Conf1234" * 4


def sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def falsos(tmp: Path) -> Path:
    pasta = tmp / "bin-conf"
    pasta.mkdir()
    reg = tmp / "registo.jsonl"
    htpasswd = f"""#!{sys.executable}
import json, sys
entrada = sys.stdin.read()
with open({str(reg)!r}, "a") as f:
    f.write(json.dumps({{"cmd": "htpasswd", "argv": sys.argv[1:], "stdin": __import__("hashlib").sha256(
        entrada.encode()).hexdigest()}}) + "\\n")
print(sys.argv[-1] + ":$2y$12$" + "A" * 53)
"""
    gh = f"""#!{sys.executable}
import hashlib, json, os, sys
entrada = "" if sys.argv[1:3] != ["auth", "login"] else sys.stdin.read()
with open({str(reg)!r}, "a") as f:
    f.write(json.dumps({{"cmd": "gh", "argv": sys.argv[1:], "stdin": hashlib.sha256(entrada.encode()).hexdigest(),
                        "env": sorted(k for k in os.environ if "TOKEN" in k),
                        "cfg": {{k: os.environ.get(k) for k in ("GH_CONFIG_DIR", "GIT_CONFIG_GLOBAL",
                                                               "GIT_CONFIG_NOSYSTEM")}}}}) + "\\n")
sessao = {str(tmp / "gh-sessao")!r}
if sys.argv[1:3] == ["auth", "status"]:
    sys.exit(0 if os.path.exists(sessao) else 1)
if sys.argv[1:3] == ["auth", "login"]:
    if os.path.exists({str(tmp / "gh-recusa")!r}):
        sys.exit(1)
    open(sessao, "w").close()
sys.exit(0)
"""
    for nome, texto in (("htpasswd", htpasswd), ("gh", gh)):
        (pasta / nome).write_text(texto, encoding="utf-8")
        (pasta / nome).chmod(0o755)
    return pasta


def registo(tmp: Path) -> list[dict]:
    f = tmp / "registo.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


class Amb:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.proxy = tmp / "proxy"
        self.config = tmp / "config" / "office.yaml"
        self.segredos = tmp / "segredos"
        self.projectos = tmp / "projectos"
        self.respostas: collections.deque = collections.deque()
        self.segredos_r: collections.deque = collections.deque()
        self.perguntas: list[str] = []

    def responder(self, *linhas, segredos=()):
        self.respostas.extend(linhas)
        self.segredos_r.extend(segredos)


@pytest.fixture
def amb(tmp_path, monkeypatch):
    c.ambiente(monkeypatch, tmp_path, extra_bin=falsos(tmp_path))
    a = Amb(tmp_path)
    for p in (a.proxy, a.config.parent, a.segredos, a.projectos, tmp_path / "dados"):
        p.mkdir(parents=True)
    monkeypatch.setattr(conf, "RAIZES", {"dados": str(tmp_path / "dados"), "projectos": str(a.projectos),
                                         "claude_projects": str(tmp_path / "casa" / ".claude" / "projects")})
    monkeypatch.setattr(conf, "CASA", tmp_path / "casa")
    monkeypatch.setattr(conf, "PROXY", a.proxy)
    monkeypatch.setenv("OFFICE_CONFIG", str(a.config))
    monkeypatch.setenv("OFFICE_SEGREDOS_DIR", str(a.segredos))
    monkeypatch.setenv("OFFICE_MODO", "docker")
    for k in ("OFFICE_UID", "OFFICE_GID", "OFFICE_AUTH_USER", "OFFICE_AUTH_PASS_FILE", "OFFICE_PROJECTOS_IDS",
              "OFFICE_GIT_NOME", "OFFICE_GIT_EMAIL", "OFFICE_CLAUDE_CRED", "OFFICE_CLAUDE_CRED_FILE",
              "OFFICE_GH_TOKEN_FILE"):
        monkeypatch.delenv(k, raising=False)
    if os.geteuid() == 0:
        pytest.skip("os testes do configurar correm sem root")

    def ler(texto):
        a.perguntas.append(texto)
        if not a.respostas:
            raise EOFError
        return a.respostas.popleft()

    def ler_segredo(texto):
        a.perguntas.append(texto)
        if not a.segredos_r:
            raise EOFError
        return a.segredos_r.popleft()

    monkeypatch.setattr(conf, "_ler", ler)
    monkeypatch.setattr(conf, "_ler_segredo", ler_segredo)
    return a


def repo(a: Amb, nome: str) -> Path:
    r = a.projectos / nome
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    return r


def gitcfg(a: Amb, *args) -> str:
    return subprocess.run(["git", "config", "--global", *args], check=False, capture_output=True, text=True,
                          env={"HOME": str(a.tmp / "casa"), "PATH": os.environ["PATH"]}).stdout


def modo(p: Path) -> int:
    return stat.S_IMODE(os.lstat(p).st_mode)


def sem_segredos(a: Amb, saida: str, *valores):
    """Nenhum segredo na saída, no registo dos falsos (argv) nem fora dos ficheiros que o guardam."""
    for v in valores:
        assert v not in saida
    for f in a.tmp.rglob("*"):   # o registo dos falsos incluído; as entradas do --sem-perguntas são do teste
        if (f.is_file() and not f.is_symlink() and f.name != "claude.env"
                and f.parent != a.tmp / "entradas"):
            dados = f.read_bytes()
            for v in valores:
                assert v.encode() not in dados, f


def tudo_interactivo(a: Amb):
    repo(a, "loja-web")
    repo(a, "Blog_2")
    a.responder("ana", "1,2", "Ana Squad", "ana@example.com", "1", segredos=(SENHA, SENHA, API, GH))


# ---------------------------------------------------------------- caminho feliz

def test_tudo_por_perguntas(amb, capsys):
    tudo_interactivo(amb)
    assert conf.main([]) == 0
    out = capsys.readouterr()
    # proxy: utilizador, hash e segredo, 0600 e sem \n final
    assert (amb.proxy / "utilizador").read_text() == "ana"
    assert conf.RX_HASH.fullmatch((amb.proxy / "hash").read_text())
    assert re.fullmatch(r"[0-9a-f]{64}", (amb.proxy / "segredo").read_text())
    for n in ("utilizador", "hash", "segredo"):
        assert modo(amb.proxy / n) == 0o600
    assert modo(amb.proxy) == 0o700 and modo(amb.segredos) == 0o700
    # office.yaml com as raízes do contrato e os dois projectos
    cfg, _ = officecfg.carregar(amb.config, estrito=True)
    assert cfg["raizes"] == conf.RAIZES and cfg["utilizador"]["nome"] == "ana"
    assert [(p["id"], p["repo"]) for p in cfg["projectos"]] == [("blog-2", "Blog_2"), ("loja-web", "loja-web")]
    assert modo(amb.config) == 0o600
    for _, p in officecfg.pastas(cfg):
        assert p.is_dir()
    # git: identidade e safe.directory
    assert gitcfg(amb, "user.name").strip() == "Ana Squad"
    assert gitcfg(amb, "user.email").strip() == "ana@example.com"
    assert gitcfg(amb, "--get-all", "safe.directory").splitlines() == [f"{amb.projectos}/*"]
    # credencial do Claude
    cred = amb.segredos / "claude.env"
    assert cred.read_text() == f"ANTHROPIC_API_KEY={API}\n" and modo(cred) == 0o600
    # htpasswd e gh: segredos só no stdin
    reg = registo(amb.tmp)
    (h,) = [x for x in reg if x["cmd"] == "htpasswd"]
    assert h["argv"] == ["-niBC", "12", "ana"] and h["stdin"] == sha(SENHA + "\n")
    (login,) = [x for x in reg if x["argv"][:2] == ["auth", "login"]]
    assert login["argv"] == ["auth", "login", "--hostname", "github.com", "--with-token"]
    assert login["stdin"] == sha(GH + "\n") and login["env"] == []
    assert any(x["argv"] == ["auth", "setup-git"] for x in reg)
    # próximos passos (AC-f7) e nada de segredos na saída
    assert "docker compose up -d" in out.out and "docker compose restart office" in out.out
    assert "http://localhost:8080" in out.out
    sem_segredos(amb, out.out + out.err, SENHA, API, GH)
    # ordem das perguntas (AC-f1)
    ordem = ["Utilizador", "Palavra-passe", "Repete", "Escolhe por número", "Nome para os commits", "E-mail",
             "Escolhe", "Chave de API", "Token do GitHub"]
    assert [next(o for o in ordem if p.startswith(o)) for p in amb.perguntas] == ordem


def test_idempotente_nao_pergunta_o_que_ja_existe(amb, capsys):
    tudo_interactivo(amb)
    assert conf.main([]) == 0
    antes = {n: (amb.proxy / n).read_text() for n in ("utilizador", "hash", "segredo")}
    yaml = amb.config.read_text()
    amb.perguntas.clear()
    assert conf.main([]) == 0   # sem respostas: qualquer pergunta daria EOF (130)
    assert amb.perguntas == []
    assert {n: (amb.proxy / n).read_text() for n in antes} == antes and amb.config.read_text() == yaml
    assert gitcfg(amb, "--get-all", "safe.directory").splitlines() == [f"{amb.projectos}/*"]
    assert len([x for x in registo(amb.tmp) if x["cmd"] == "htpasswd"]) == 1


# ---------------------------------------------------------------- palavra-passe

def test_palavra_passe_tres_tentativas(amb, capsys):
    repo(amb, "loja")
    amb.responder("ana", "", "N", "n@x.pt", "3",
                  segredos=("curta", "curta", SENHA, SENHA + "x", SENHA, SENHA, ""))
    assert conf.main([]) == 0
    err = capsys.readouterr().err
    assert "pelo menos 12 caracteres: tenta de novo" in err and "não coincidem: tenta de novo" in err


def test_palavra_passe_falha_a_terceira(amb, capsys):
    amb.responder("ana", segredos=(SENHA, "x" * 12) * 3)
    assert conf.main([]) == 1
    assert "erro: as palavras-passe não coincidem" in capsys.readouterr().err
    assert not any(amb.proxy.iterdir())


@pytest.mark.parametrize("utilizador", ["Ana", "a", "1ana", "ana nova", "a" * 33])
def test_utilizador_invalido(amb, capsys, utilizador):
    amb.responder(utilizador, utilizador, utilizador, segredos=(SENHA, SENHA))
    assert conf.main([]) == 1
    assert "utilizador inválido" in capsys.readouterr().err
    assert not (amb.proxy / "hash").exists()
    # C4: três vezes o utilizador, nunca a palavra-passe
    assert [p.split(" ")[0] for p in amb.perguntas] == ["Utilizador"] * 3
    assert list(amb.segredos_r) == [SENHA, SENHA]


def test_utilizador_invalido_pergunta_de_novo_antes_da_palavra_passe(amb, capsys):
    amb.responder("Ana", "ana", segredos=(SENHA, SENHA))
    assert conf.main(["--palavra-passe"]) == 0
    assert "utilizador inválido (minúsculas, números, . _ -; 2 a 32 caracteres): tenta de novo" in \
        capsys.readouterr().err
    assert [p.split(" ")[0] for p in amb.perguntas] == ["Utilizador", "Utilizador", "Palavra-passe", "Repete"]
    assert (amb.proxy / "utilizador").read_text() == "ana"


def test_hash_inesperado_recusado(amb, capsys):
    (amb.tmp / "bin-conf" / "htpasswd").write_text("#!/bin/sh\necho 'ana:$2a$10$curto'\n", encoding="utf-8")
    amb.responder("ana", segredos=(SENHA, SENHA))
    assert conf.main([]) == 1
    assert "hash bcrypt" in capsys.readouterr().err and not (amb.proxy / "hash").exists()


def test_palavra_passe_troca_so_o_hash(amb, capsys):
    tudo_interactivo(amb)
    conf.main([])
    segredo = (amb.proxy / "segredo").read_text()
    amb.perguntas.clear()
    amb.responder("rui", segredos=(SENHA + "2", SENHA + "2"))
    assert conf.main(["--palavra-passe"]) == 0
    assert (amb.proxy / "utilizador").read_text() == "rui" and (amb.proxy / "segredo").read_text() == segredo
    assert [p.split(" ")[0] for p in amb.perguntas] == ["Utilizador", "Palavra-passe", "Repete"]


# ---------------------------------------------------------------- projectos e office.yaml

def test_sem_repos_cria_o_exemplo(amb):
    amb.responder("ana", "s", "N", "n@x.pt", "3", segredos=(SENHA, SENHA, ""))
    assert conf.main([]) == 0
    ex = amb.projectos / "exemplo"
    assert subprocess.run(["git", "log", "--oneline"], check=False, cwd=ex, capture_output=True, text=True).stdout.strip()
    assert ".worktrees/" in (ex / ".git" / "info" / "exclude").read_text()
    cfg, _ = officecfg.carregar(amb.config, estrito=True)
    assert [p["id"] for p in cfg["projectos"]] == ["exemplo"]


def test_sem_repos_e_sem_exemplo_falha(amb, capsys):
    amb.responder("ana", "n", segredos=(SENHA, SENHA))
    assert conf.main([]) == 1
    assert "sem projectos" in capsys.readouterr().err and not amb.config.exists()


def test_escolha_invalida_tres_vezes(amb, capsys):
    repo(amb, "loja")
    amb.responder("ana", "7", "x", "0", segredos=(SENHA, SENHA))
    assert conf.main([]) == 1
    assert "escolha inválida" in capsys.readouterr().err


def test_projectos_troca_so_o_bloco(amb):
    tudo_interactivo(amb)
    conf.main([])
    texto = amb.config.read_text().replace("marca: Squad Office", "marca: Minha Marca")
    amb.config.write_text(texto + "# comentário final\n")
    repo(amb, "novo")
    amb.perguntas.clear()
    amb.responder("3")   # Blog_2, loja-web, novo
    assert conf.main(["--projectos"]) == 0
    assert [p.split(" ")[0] for p in amb.perguntas] == ["Escolhe"]
    cfg, _ = officecfg.carregar(amb.config, estrito=True)
    assert cfg["marca"] == "Minha Marca" and [p["id"] for p in cfg["projectos"]] == ["novo"]
    assert amb.config.read_text().endswith("# comentário final\n")


def test_office_yaml_existente_invalido_nao_e_tocado(amb, capsys):
    amb.config.write_text("versao: 2\n")
    (amb.proxy / "utilizador").write_text("ana")
    (amb.proxy / "hash").write_text("x")
    assert conf.main([]) == 1
    assert "office.yaml existente é inválido" in capsys.readouterr().err
    assert amb.config.read_text() == "versao: 2\n"


def test_trocar_bloco_limites():
    texto = "a: 1\nprojectos:\n  - id: x\n\n    nome: X\n\n# nota\ncolunas:\n  b: 2\n"
    assert conf.trocar_bloco(texto, "projectos:\n  - id: y\n") == \
        "a: 1\nprojectos:\n  - id: y\n\n# nota\ncolunas:\n  b: 2\n"
    assert conf.trocar_bloco("projectos:\n  - id: x\n", "P\n") == "P\n"
    with pytest.raises(conf.Erro):
        conf.trocar_bloco("a: 1\n", "P\n")


def test_ids_derivados_validos_e_unicos():
    nomes = ["Loja_Web", "9vidas", "x", "outro", "a" * 40, "a" * 40 + "b", "loja-web", "-.-"]
    bloco = conf.bloco_projectos(nomes)
    cfg = officecfg.validar(officecfg.ler_yaml(
        "squads:\n  - id: dev\n    nome: Dev\n" + bloco))
    ids = [p["id"] for p in cfg["projectos"]]
    assert ids[:4] == ["loja-web", "p9vidas", "x-p", "outro-p"]
    assert len(set(ids)) == len(ids) and [p["repo"] for p in cfg["projectos"]] == nomes


def test_lista_so_repos_git_com_nome_aceite(amb):
    repo(amb, "bom")
    (amb.projectos / "sem-git").mkdir()
    repo(amb, ".escondido")
    repo(amb, "com espaço")
    os.symlink(amb.projectos / "bom", amb.projectos / "ligacao")
    assert conf.listar_repos() == ["bom"]


# ---------------------------------------------------------------- credencial do Claude (AC-f2)

def test_token_da_subscricao_explica_e_grava(amb, capsys):
    tudo_interactivo(amb)
    conf.main([])
    capsys.readouterr()
    amb.perguntas.clear()
    amb.responder("2", segredos=("codigo-do-browser", OAUTH))
    assert conf.main(["--credenciais"]) == 0
    out, err = capsys.readouterr()
    for frase in ("docker compose run --rm runner claude setup-token", "outra janela", "volume «segredos»",
                  "só o runner", "Risco", "ambiente do claude"):
        assert frase in out, frase
    assert "sk-ant-oat01-, não o código do browser: tenta de novo" in err
    assert (amb.segredos / "claude.env").read_text() == f"CLAUDE_CODE_OAUTH_TOKEN={OAUTH}\n"
    assert [p.split(" ")[0] for p in amb.perguntas] == ["Escolhe", "Token", "Token"]
    sem_segredos(amb, out + err, OAUTH, API)


def test_nunca_corre_setup_token_nem_le_credentials(amb, monkeypatch):
    casa = amb.tmp / "casa" / ".claude"
    casa.mkdir(parents=True)
    (casa / ".credentials.json").write_text("{}")
    lidos = []
    real = open

    def espiao(f, *a, **k):
        lidos.append(str(f))
        return real(f, *a, **k)

    monkeypatch.setattr("builtins.open", espiao)
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(amb.tmp / "claude-argv"))
    tudo_interactivo(amb)
    assert conf.main([]) == 0
    assert not any(".credentials.json" in x for x in lidos)
    assert not (amb.tmp / "claude-argv").exists()   # o claude nunca foi chamado
    texto = Path(conf.__file__).read_text(encoding="utf-8")
    assert '"setup-token"' not in texto and ".credentials.json" not in texto.split('"""', 2)[2]


def test_chave_de_api_rejeita_o_token_da_subscricao(amb, capsys):
    amb.responder("1", segredos=(OAUTH, "sk-ant-api03-mal formada", "sk-ant-x!"))
    assert conf.main(["--credenciais"]) == 1
    err = capsys.readouterr().err
    assert "token da subscrição: escolhe a opção 2" in err and not (amb.segredos / "claude.env").exists()
    assert OAUTH not in err


def test_agora_nao_deixa_por_fazer(amb, capsys):
    amb.responder("3")
    assert conf.main(["--credenciais"]) == 0
    assert "configurar --credenciais" in capsys.readouterr().out
    assert not (amb.segredos / "claude.env").exists()


def test_opcao_invalida(amb, capsys):
    amb.responder("9")
    assert conf.main(["--credenciais"]) == 1
    assert "escolhe 1, 2 ou 3" in capsys.readouterr().err


def test_credencial_substituida_por_os_replace(amb):
    cred = amb.segredos / "claude.env"
    cred.write_text("antigo")
    ino = cred.stat().st_ino
    amb.responder("1", segredos=(API,))
    assert conf.main(["--credenciais"]) == 0
    assert cred.read_text() == f"ANTHROPIC_API_KEY={API}\n" and cred.stat().st_ino != ino
    assert not list(amb.segredos.glob(".*.tmp"))


def test_symlink_no_tmp_nao_e_seguido(amb):
    alvo = amb.tmp / "alvo"
    alvo.write_text("intacto")
    os.symlink(alvo, amb.segredos / ".claude.env.tmp")
    amb.responder("1", segredos=(API,))
    assert conf.main(["--credenciais"]) == 0
    assert alvo.read_text() == "intacto"


# ---------------------------------------------------------------- gh (AC-f3)

def test_gh_explica_fine_grained_e_enter_e_agora_nao(amb, capsys):
    amb.responder(segredos=("",))
    assert conf.main(["--gh"]) == 0
    out = capsys.readouterr().out
    assert "fine-grained" in out and "«Token do GitHub»" in out and "configurar --gh" in out
    assert not any(x["argv"][:2] == ["auth", "login"] for x in registo(amb.tmp))


def test_gh_token_invalido_e_recusa(amb, capsys):
    amb.responder(segredos=("curto", GH))
    (amb.tmp / "gh-recusa").touch()
    assert conf.main(["--gh"]) == 1
    err = capsys.readouterr().err
    assert "não parece um token" in err and "o gh recusou o token" in err and GH not in err


def test_gh_sem_gh_na_imagem(amb, monkeypatch, capsys):
    monkeypatch.setattr(conf.shutil, "which", lambda n, path=None: None)
    assert conf.main(["--gh"]) == 0
    assert "o gh não está nesta imagem" in capsys.readouterr().err


# ---------------------------------------------------------------- --sem-perguntas (contrato de env)

def ficheiro(a: Amb, nome: str, texto: str) -> str:
    f = a.tmp / "entradas" / nome
    f.parent.mkdir(exist_ok=True)
    f.write_text(texto)
    return str(f)


def test_sem_perguntas_completo(amb, monkeypatch, capsys):
    monkeypatch.setenv("OFFICE_AUTH_USER", "ci")
    monkeypatch.setenv("OFFICE_AUTH_PASS_FILE", ficheiro(amb, "pass", SENHA + "\r\n"))
    monkeypatch.setenv("OFFICE_GIT_NOME", "CI")
    monkeypatch.setenv("OFFICE_GIT_EMAIL", "ci@example.com")
    monkeypatch.setenv("OFFICE_CLAUDE_CRED", "oauth")
    monkeypatch.setenv("OFFICE_CLAUDE_CRED_FILE", ficheiro(amb, "cred", OAUTH + "\n"))
    monkeypatch.setenv("OFFICE_GH_TOKEN_FILE", ficheiro(amb, "gh", GH + "\n"))
    assert conf.main(["--sem-perguntas"]) == 0
    assert amb.perguntas == []
    assert (amb.proxy / "utilizador").read_text() == "ci"
    assert next(x for x in registo(amb.tmp) if x["cmd"] == "htpasswd")["stdin"] == sha(SENHA + "\n")
    cfg, _ = officecfg.carregar(amb.config, estrito=True)
    assert [p["id"] for p in cfg["projectos"]] == ["exemplo"]   # sem repos: o exemplo
    assert (amb.segredos / "claude.env").read_text() == f"CLAUDE_CODE_OAUTH_TOKEN={OAUTH}\n"
    assert (amb.tmp / "gh-sessao").exists() and gitcfg(amb, "user.name").strip() == "CI"
    out = capsys.readouterr()
    sem_segredos(amb, out.out + out.err, SENHA, OAUTH, GH)
    # de novo, sem nada: idempotente (OFFICE_CLAUDE_CRED/GH_TOKEN_FILE são pedidos explícitos, como no install.sh)
    for k in ("OFFICE_AUTH_PASS_FILE", "OFFICE_CLAUDE_CRED", "OFFICE_GH_TOKEN_FILE"):
        monkeypatch.delenv(k)
    assert conf.main(["--sem-perguntas"]) == 0


def test_sem_perguntas_ids_escolhidos(amb, monkeypatch):
    repo(amb, "loja")
    repo(amb, "blog")
    monkeypatch.setenv("OFFICE_AUTH_USER", "ci")
    monkeypatch.setenv("OFFICE_AUTH_PASS_FILE", ficheiro(amb, "pass", SENHA))
    monkeypatch.setenv("OFFICE_PROJECTOS_IDS", " blog , exemplo ")
    assert conf.main(["--sem-perguntas"]) == 0
    cfg, _ = officecfg.carregar(amb.config, estrito=True)
    assert [p["id"] for p in cfg["projectos"]] == ["blog", "exemplo"]
    assert (amb.projectos / "exemplo" / ".git").is_dir()


@pytest.mark.parametrize("env,erro", [
    ({}, "precisa de OFFICE_AUTH_PASS_FILE"),
    ({"OFFICE_AUTH_PASS_FILE": "/nao/existe"}, "OFFICE_AUTH_PASS_FILE não se lê"),
    ({"OFFICE_AUTH_PASS_FILE": "VAZIO"}, "OFFICE_AUTH_PASS_FILE está vazio"),
    ({"OFFICE_AUTH_PASS_FILE": "CURTA"}, "pelo menos 12"),
    ({"OFFICE_AUTH_PASS_FILE": "BOA", "OFFICE_PROJECTOS_IDS": "nao-ha"}, "«nao-ha» não é um repositório"),
    ({"OFFICE_AUTH_PASS_FILE": "BOA", "OFFICE_GIT_NOME": "a\tb"}, "OFFICE_GIT_NOME: nome inválido"),
    ({"OFFICE_AUTH_PASS_FILE": "BOA", "OFFICE_GIT_EMAIL": "sem-arroba"}, "OFFICE_GIT_EMAIL: e-mail inválido"),
    ({"OFFICE_AUTH_PASS_FILE": "BOA", "OFFICE_CLAUDE_CRED": "talvez"}, "api, oauth ou nenhuma"),
    ({"OFFICE_AUTH_PASS_FILE": "BOA", "OFFICE_CLAUDE_CRED": "api"}, "precisa de OFFICE_CLAUDE_CRED_FILE"),
    ({"OFFICE_AUTH_PASS_FILE": "BOA", "OFFICE_CLAUDE_CRED": "api", "OFFICE_CLAUDE_CRED_FILE": "OAUTH"},
     "escolhe a opção 2"),
    ({"OFFICE_AUTH_PASS_FILE": "BOA", "OFFICE_GH_TOKEN_FILE": "CURTA"}, "não parece um token do GitHub"),
])
def test_sem_perguntas_erros(amb, monkeypatch, capsys, env, erro):
    conteudos = {"VAZIO": "", "CURTA": "curta", "BOA": SENHA, "OAUTH": OAUTH}
    monkeypatch.setenv("OFFICE_AUTH_USER", "ci")
    for k, v in env.items():
        monkeypatch.setenv(k, ficheiro(amb, v, conteudos[v]) if v in conteudos else v)
    assert conf.main(["--sem-perguntas"]) == 1
    err = capsys.readouterr().err
    assert erro in err, err
    for v in (SENHA, OAUTH):
        assert v not in err


def test_sem_perguntas_sem_credencial_nem_gh_avisa(amb, monkeypatch, capsys):
    monkeypatch.setenv("OFFICE_AUTH_USER", "ci")
    monkeypatch.setenv("OFFICE_AUTH_PASS_FILE", ficheiro(amb, "pass", SENHA))
    assert conf.main(["--sem-perguntas"]) == 0
    out, err = capsys.readouterr()
    assert "falta o user.name" in err and "sem OFFICE_GH_TOKEN_FILE" in err
    assert "configurar --credenciais" in out and "configurar --gh" in out


# ---------------------------------------------------------------- root: chown e largar privilégios (D2)

class Root:
    def __init__(self, monkeypatch, cai=True):
        self.chamadas = []
        self.euid = 0
        self.cai = cai
        monkeypatch.setattr(conf.os, "geteuid", lambda: self.euid)
        monkeypatch.setattr(conf.os, "getuid", lambda: self.euid)
        monkeypatch.setattr(conf.os, "fchown", self.fchown)
        monkeypatch.setattr(conf.os, "setgroups", lambda g: self.chamadas.append(("setgroups", g)))
        monkeypatch.setattr(conf.os, "setgid", lambda g: self.chamadas.append(("setgid", g)))
        monkeypatch.setattr(conf.os, "setuid", self.setuid)

    def fchown(self, fd, uid, gid):
        self.chamadas.append(("chown", os.readlink(f"/proc/self/fd/{fd}"), uid, gid))

    def setuid(self, uid):
        self.chamadas.append(("setuid", uid))
        if self.cai:
            self.euid = uid


def volumes(amb: Amb) -> list[Path]:
    ps = conf.caminhos_chown(amb.config)
    for p in ps:
        p.mkdir(parents=True, exist_ok=True)
    return ps


def test_root_chown_lista_fixa_e_larga(amb, monkeypatch):
    ps = volumes(amb)
    root = Root(monkeypatch)
    monkeypatch.setenv("OFFICE_UID", "4242")
    monkeypatch.setenv("OFFICE_GID", "4343")
    amb.responder("3")
    assert conf.main(["--credenciais"]) == 0
    chowns = [x for x in root.chamadas if x[0] == "chown"]
    assert [x[1] for x in chowns] == [str(p) for p in ps]   # projectos não: não é de root
    assert all(x[2:] == (4242, 4343) for x in chowns)
    assert root.chamadas[len(chowns):] == [("setgroups", []), ("setgid", 4343), ("setuid", 4242)]
    nomes = {p.name for p in ps}
    assert {"tasks", "runner", "runner-estado", "push", "casa", ".claude", "projects", "config", "segredos",
            "proxy"} <= nomes


def test_root_nao_segue_symlinks(amb, monkeypatch, capsys):
    volumes(amb)
    claude = amb.tmp / "casa" / ".claude"
    (claude / "projects").rmdir()
    claude.rmdir()
    fora = amb.tmp / "fora"
    (fora / "projects").mkdir(parents=True)
    os.symlink(fora, claude)
    root = Root(monkeypatch)
    amb.responder("3")
    assert conf.main(["--credenciais"]) == 0
    tocados = [x[1] for x in root.chamadas if x[0] == "chown"]
    assert not any(str(fora) in t or ".claude" in t for t in tocados)
    assert "não é uma pasta (ou é um symlink)" in capsys.readouterr().err


def test_root_que_nao_larga_recusa(amb, monkeypatch, capsys):
    volumes(amb)
    Root(monkeypatch, cai=False)
    amb.responder("1", segredos=(API,))
    assert conf.main(["--credenciais"]) == 1
    assert "recuso continuar" in capsys.readouterr().err
    assert not (amb.segredos / "claude.env").exists()


@pytest.mark.parametrize("valor", ["0", "-1", "abc", "4294967296"])
def test_uid_invalido(amb, monkeypatch, capsys, valor):
    monkeypatch.setenv("OFFICE_UID", valor)
    assert conf.main(["--credenciais"]) == 1
    assert "OFFICE_UID inválido" in capsys.readouterr().err


def test_modo_das_raizes_sem_root(amb):
    # com nocopy as raízes nascem 0755; o configurar, já sem root, dá 0700 às privadas e 0750 às outras
    ps = volumes(amb)
    for p in ps:
        p.chmod(0o755)
    amb.responder("3")
    assert conf.main(["--credenciais"]) == 0
    privadas = {amb.proxy, amb.segredos, amb.tmp / "dados" / officecfg.SUB["push"], amb.tmp / "casa",
                amb.tmp / "casa" / ".claude"}
    assert {p: modo(p) for p in ps} == {p: 0o700 if p in privadas else 0o750 for p in ps}


def test_modo_das_raizes_nao_segue_symlinks(amb):
    volumes(amb)
    claude = amb.tmp / "casa" / ".claude"
    (claude / "projects").rmdir()
    claude.rmdir()
    fora = amb.tmp / "fora"
    (fora / "projects").mkdir(parents=True)
    fora.chmod(0o755)
    os.symlink(fora, claude)
    amb.responder("3")
    assert conf.main(["--credenciais"]) == 0
    assert modo(fora) == 0o755 and modo(fora / "projects") != 0o750


def test_sem_root_nao_mexe_em_donos(amb, monkeypatch):
    chamadas = []
    monkeypatch.setattr(conf.os, "fchown", lambda *a: chamadas.append(a))
    monkeypatch.setattr(conf.os, "setuid", lambda *a: chamadas.append(a))
    amb.responder("3")
    assert conf.main(["--credenciais"]) == 0
    assert chamadas == []


# ---------------------------------------------------------------- mais de 30 repositórios (C5)

def muitos(a: Amb, n: int) -> list[str]:
    nomes = [f"r{i:02d}" for i in range(n)]
    for x in nomes:
        (a.projectos / x / ".git").mkdir(parents=True)   # listar_repos só vê o .git
    return nomes


def test_mais_de_30_enter_nao_e_todos(amb, capsys):
    muitos(amb, 31)
    amb.responder("", "", "")
    with pytest.raises(conf.Erro, match="no máximo 30"):
        conf.escolher_projectos(False)
    assert all("no máximo 30" in p and "Enter = todos" not in p for p in amb.perguntas)
    assert "escolha inválida: tenta de novo" in capsys.readouterr().err


def test_mais_de_30_escolhe_por_numeros(amb):
    nomes = muitos(amb, 31)
    amb.responder(",".join(str(i) for i in range(1, 32)), "1,31")   # 31 números: inválido; depois 2
    assert conf.escolher_projectos(False) == [nomes[0], nomes[30]]


def test_30_enter_e_todos(amb):
    nomes = muitos(amb, 30)
    amb.responder("")
    assert conf.escolher_projectos(False) == nomes
    assert "Enter = todos" in amb.perguntas[0]
    officecfg.validar(officecfg.ler_yaml("squads:\n  - id: dev\n    nome: Dev\n" + conf.bloco_projectos(nomes)))


def test_mais_de_30_sem_perguntas(amb, monkeypatch):
    nomes = muitos(amb, 31)
    with pytest.raises(conf.Erro, match="31 repositórios .* no máximo 30: escolhe-os com OFFICE_PROJECTOS_IDS"):
        conf.escolher_projectos(True)
    monkeypatch.setenv("OFFICE_PROJECTOS_IDS", ",".join(nomes))
    with pytest.raises(conf.Erro, match="OFFICE_PROJECTOS_IDS: no máximo 30"):
        conf.escolher_projectos(True)
    monkeypatch.setenv("OFFICE_PROJECTOS_IDS", ",".join(nomes[:30]))
    assert conf.escolher_projectos(True) == nomes[:30]


# ---------------------------------------------------------------- git e gh sem config do agente (S2, C3)

GIT_SEGURO = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "safe.directory=*"]


def espiar(monkeypatch) -> list[tuple[list[str], dict]]:
    chamadas = []
    real = conf._corre

    def corre(args, entrada=None, env=None, cwd=None):
        chamadas.append((list(args), dict(env if env is not None else conf.env_git_isolado())))
        return real(args, entrada=entrada, env=env, cwd=cwd)

    monkeypatch.setattr(conf, "_corre", corre)
    return chamadas


def armadilha(a: Amb) -> Path:
    """~/.gitconfig do agente com hooksPath e templateDir que deixam uma marca se forem executados."""
    marca = a.tmp / "executado"
    hooks = a.tmp / "hooks-do-agente"
    hooks.mkdir()
    for h in ("pre-commit", "post-commit", "post-checkout"):
        (hooks / h).write_text(f"#!/bin/sh\necho {h} >> {marca}\n")
        (hooks / h).chmod(0o755)
    tpl = a.tmp / "template-do-agente" / "hooks"
    tpl.mkdir(parents=True)
    (tpl / "pre-commit").write_text(f"#!/bin/sh\necho template >> {marca}\n")
    (tpl / "pre-commit").chmod(0o755)
    (a.tmp / "casa" / ".gitconfig").write_text(
        f"[core]\n\thooksPath = {hooks}\n[init]\n\ttemplateDir = {tpl.parent}\n")
    return marca


def test_git_do_configurar_sem_config_global_nem_hooks(amb, monkeypatch):
    marca = armadilha(amb)
    chamadas = espiar(monkeypatch)
    amb.responder("ana", "s", "N", "n@x.pt", "3", segredos=(SENHA, SENHA, ""))
    assert conf.main([]) == 0
    assert not marca.exists()
    assert not (amb.projectos / "exemplo" / ".git" / "hooks").exists()   # --template=: nada copiado
    gits = [(a, e) for a, e in chamadas if a[0] == "git"]
    assert gits
    for args, env in gits:
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_NOSYSTEM"] == "1", args
        assert args[:5] == GIT_SEGURO or args[:3] == ["git", "config", "--file"], args
    cfgs = [a for a, _ in gits if a[1] == "config"]
    assert cfgs and all(a[3] == str(amb.tmp / "casa" / ".gitconfig") for a in cfgs)
    (init,) = [a for a, _ in gits if "init" in a]
    assert "--template=" in init
    (commit,) = [a for a, _ in gits if "commit" in a]
    assert "user.name=Squad Office" in commit and "user.email=office@localhost" in commit
    # o .gitconfig do agente continua lá; o configurar só acrescentou identidade e safe.directory
    assert gitcfg(amb, "core.hooksPath").strip() and gitcfg(amb, "user.name").strip() == "N"


def test_exemplo_com_dono_diferente(amb, monkeypatch):   # C3: «dubious ownership» antes do safe.directory
    monkeypatch.setattr(conf.lancador, "ENV_PASSA", (*conf.lancador.ENV_PASSA, "GIT_TEST_ASSUME_DIFFERENT_OWNER"))
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    conf.criar_exemplo()
    ex = amb.projectos / "exemplo"
    sem = subprocess.run(["git", "status"], cwd=ex, capture_output=True, text=True, check=False,
                         env=conf.env_git_isolado())
    assert sem.returncode != 0 and "dubious ownership" in sem.stderr   # o teste reproduz mesmo o problema
    log = subprocess.run([*GIT_SEGURO, "log", "--oneline"], cwd=ex, capture_output=True, text=True, check=True,
                         env=conf.env_git_isolado())
    assert "Primeiro commit" in log.stdout


def test_gitconfig_symlink_recusado(amb, capsys):
    alvo = amb.tmp / "alvo"
    alvo.write_text("intacto\n")
    os.symlink(alvo, amb.tmp / "casa" / ".gitconfig")
    repo(amb, "loja")
    amb.responder("ana", "", segredos=(SENHA, SENHA))
    assert conf.main([]) == 1
    assert "symlink: recuso escrever" in capsys.readouterr().err
    assert alvo.read_text() == "intacto\n"


def test_gh_com_config_fixa_e_gitconfig_verificado(amb):
    amb.responder(segredos=(GH,))
    assert conf.main(["--gh"]) == 0
    casa = amb.tmp / "casa"
    gh = [x for x in registo(amb.tmp) if x["cmd"] == "gh"]
    assert {tuple(x["argv"][:2]) for x in gh} >= {("auth", "login"), ("auth", "setup-git")}
    for x in gh:
        assert x["cfg"] == {"GH_CONFIG_DIR": str(casa / ".config" / "gh"),
                            "GIT_CONFIG_GLOBAL": str(casa / ".gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}, x


@pytest.mark.parametrize("ligacao", [".config", ".config/gh", ".config/gh/hosts.yml", ".gitconfig"])
def test_gh_recusa_symlinks_na_casa(amb, capsys, ligacao):
    casa = amb.tmp / "casa"
    (casa / ligacao).parent.mkdir(parents=True, exist_ok=True)
    fora = amb.tmp / "fora"
    fora.mkdir()
    os.symlink(fora, casa / ligacao)
    amb.responder(segredos=(GH,))
    assert conf.main(["--gh"]) == 1
    assert "é um symlink: recuso" in capsys.readouterr().err
    assert not any(x["cmd"] == "gh" for x in registo(amb.tmp))


# ---------------------------------------------------------------- --office-yaml (S1)

def office_yaml_valido(a: Amb) -> str:
    return conf.office_yaml_novo("ana", conf.bloco_projectos(["loja"])).replace("marca: Squad Office",
                                                                              "marca: Nova Marca")


def test_office_yaml_valido_substitui(amb, capsys):
    amb.config.write_text("antigo\n")
    ino = amb.config.stat().st_ino
    novo = amb.tmp / "novo.yaml"
    novo.write_text(office_yaml_valido(amb))
    assert conf.main(["--office-yaml", str(novo)]) == 0
    assert amb.config.read_text() == novo.read_text() and amb.config.stat().st_ino != ino
    assert modo(amb.config) == 0o600 and not list(amb.config.parent.glob(".*.tmp"))
    assert officecfg.carregar(amb.config, estrito=True)[0]["marca"] == "Nova Marca"
    assert "docker compose restart office" in capsys.readouterr().out
    assert amb.perguntas == [] and registo(amb.tmp) == []   # só isto: nada de perguntas, htpasswd ou gh


@pytest.mark.parametrize("caso,erro", [
    ("invalido", "inválido"),
    ("symlink", "é um symlink"),
    ("pasta", "não é um ficheiro"),
    ("falta", "não consigo ler"),
    ("grande", "mais de 256 KB"),
    ("binario", "não está em UTF-8"),
])
def test_office_yaml_recusado_o_antigo_fica(amb, capsys, caso, erro):
    amb.config.write_text("antigo\n")
    novo = amb.tmp / "novo.yaml"
    if caso == "invalido":
        novo.write_text("versao: 2\n")
    elif caso == "symlink":
        bom = amb.tmp / "bom.yaml"
        bom.write_text(office_yaml_valido(amb))
        os.symlink(bom, novo)
    elif caso == "pasta":
        novo.mkdir()
    elif caso == "grande":
        novo.write_text(office_yaml_valido(amb) + "#" * (conf.YAML_MAX + 1))
    elif caso == "binario":
        novo.write_bytes(b"\xff\xfe")
    assert conf.main(["--office-yaml", str(novo)]) == 1
    assert erro in capsys.readouterr().err
    assert amb.config.read_text() == "antigo\n" and not list(amb.config.parent.glob(".*.tmp"))


def test_office_yaml_fifo_nao_bloqueia(amb, capsys):
    novo = amb.tmp / "fifo"
    os.mkfifo(novo)
    assert conf.main(["--office-yaml", str(novo)]) == 1
    assert "não é um ficheiro" in capsys.readouterr().err


def test_office_yaml_so_sozinho(amb, capsys):
    with pytest.raises(SystemExit):
        conf.main(["--office-yaml", "/x", "--gh"])
    assert "não se junta" in capsys.readouterr().err


def test_office_yaml_larga_privilegios_antes_de_ler(amb, monkeypatch):
    volumes(amb)
    root = Root(monkeypatch)
    vistos = []
    ler, carregar = conf.ler_office_yaml, officecfg.carregar

    def ler_espiao(origem):
        vistos.append(("ler", root.euid))
        return ler(origem)

    def carregar_espiao(*a, **k):
        vistos.append(("validar", root.euid))
        return carregar(*a, **k)

    monkeypatch.setattr(conf, "ler_office_yaml", ler_espiao)
    monkeypatch.setattr(officecfg, "carregar", carregar_espiao)
    novo = amb.tmp / "novo.yaml"
    novo.write_text(office_yaml_valido(amb))
    assert conf.main(["--office-yaml", str(novo)]) == 0
    assert vistos == [("ler", 10001), ("validar", 10001)]
    i = root.chamadas.index(("setuid", 10001))
    assert any(x[0] == "chown" for x in root.chamadas[:i])   # o chown normal vem antes
    assert amb.config.read_text() == novo.read_text()
