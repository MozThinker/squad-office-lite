"""Ajudas partilhadas pelos testes do runner (sem testes aqui). Tudo em pastas temporárias, `git` local e o
`claude` falso de tests/bin: nada de rede, docker, systemd nem a casa real do utilizador."""
import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BIN = RAIZ / "tests" / "bin"
RUNNER = RAIZ / "runner" / "office_runner.py"
for _p in (str(RAIZ / "runner"), str(RAIZ)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import decisoes
import diagnostico
import lancador
import office_runner

import officecfg

# sem aprovacoes/server aqui: importam reader.py (re `*+`, Python >= 3.11) e estes testes correm também em 3.10
__all__ = ["decisoes", "diagnostico", "lancador", "office_runner", "officecfg"]

GIT_ENV = {"GIT_AUTHOR_NAME": "Teste", "GIT_AUTHOR_EMAIL": "teste@example.com", "GIT_COMMITTER_NAME": "Teste",
           "GIT_COMMITTER_EMAIL": "teste@example.com", "GIT_CONFIG_NOSYSTEM": "1"}
CHAVES = ["v", "id", "cartao_id", "squad", "projecto", "kind", "mode", "slug", "titulo", "brief", "pedido_em"]
# variáveis do claude e do gh falsos: entram na lista branca do runner só nos testes em processo (monkeypatch em
# ambiente()); nos testes em subprocesso usa-se claude_com()
FAKE = ("FAKE_CLAUDE_ARGV", "FAKE_CLAUDE_STDIN", "FAKE_CLAUDE_FALHA", "FAKE_CLAUDE_EXIGE_CRED", "FAKE_CLAUDE_CRED",
        "FAKE_CLAUDE_SETTINGS", "FAKE_GH_ARGV", "FAKE_GH_SAIDA", "FAKE_GH_SAIDA_VIEW", "FAKE_GH_RC_VIEW")


def gh_sem_sessao(tmp: Path) -> Path:
    """Pasta `tmp/bin-gh` com um `gh` falso que falha sempre (exit 1, sem saída): o `gh auth status` do estado.json e
    o `gh pr list` nunca chamam o gh real nos testes."""
    pasta = tmp / "bin-gh"
    if not (pasta / "gh").exists():
        pasta.mkdir(exist_ok=True)
        (pasta / "gh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (pasta / "gh").chmod(0o755)
    return pasta


def ambiente(monkeypatch, tmp: Path, extra_bin: Path | None = None) -> None:
    """HOME e git isolados, claude falso no PATH (com as FAKE_CLAUDE_* na lista branca), gh falso sem sessão (depois
    do extra_bin), sem Telegram."""
    monkeypatch.setattr(lancador, "ENV_PASSA", lancador.ENV_PASSA + FAKE)
    monkeypatch.setenv("HOME", str(tmp / "casa"))
    (tmp / "casa").mkdir(exist_ok=True)
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp / "casa" / ".gitconfig"))
    caminhos = ([str(extra_bin)] if extra_bin else []) + [str(gh_sem_sessao(tmp)), str(BIN),
                                                         os.environ.get("PATH", "")]
    monkeypatch.setenv("PATH", os.pathsep.join(caminhos))
    for k in ("OFFICE_TELEGRAM_TOKEN", "OFFICE_TELEGRAM_CHAT", "OFFICE_TELEGRAM_API", "FAKE_CLAUDE_FALHA",
              "FAKE_CLAUDE_ARGV", "FAKE_CLAUDE_STDIN", "OFFICE_CONFIG", "FAKE_CLAUDE_EXIGE_CRED", "FAKE_CLAUDE_CRED",
              "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "FAKE_CLAUDE_SETTINGS", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(lancador, "SEGREDOS", {})   # os segredos guardados no arranque, como os do processo


def claude_com(tmp: Path, nome: str, **variaveis: str) -> Path:
    """Pasta `tmp/bin-<nome>` com um `claude` que acrescenta `variaveis` ao ambiente e corre o falso de tests/bin.
    Para pôr à frente do PATH nos testes em subprocesso (o PATH passa a lista branca; as FAKE_CLAUDE_* não)."""
    pasta = tmp / f"bin-{nome}"
    pasta.mkdir()
    f = pasta / "claude"
    f.write_text(f"#!{sys.executable}\nimport os, sys\nos.environ.update({variaveis!r})\n"
                 f"os.execv({sys.executable!r}, [{sys.executable!r}, {str(BIN / 'claude')!r}, *sys.argv[1:]])\n",
                 encoding="utf-8")
    f.chmod(0o755)
    return pasta


def config(tmp: Path, tecto=10, paralelo=1, rotacao=14, settings="", permission="auto"):
    """office.yaml com raízes em tmp (squads dev/worktree e mkt/sessao; projectos exemplo e loja). → (cfg, caminho)."""
    texto = f"""versao: 1
marca: Squad Office
utilizador:
  nome: Ana
  contacto: ""
fuso: Europe/Lisbon
raizes:
  dados: {tmp}/dados
  projectos: {tmp}/projectos
  claude_projects: {tmp}/claude
squads:
  - id: dev
    nome: Dev
    lead: tech-lead
    tipos: [feat, fix]
    modos: [goal, task]
  - id: mkt
    nome: Marketing
    formato: sessao
projectos:
  - id: exemplo
    nome: Exemplo
  - id: loja
    nome: Loja
    repo: loja-web
runner:
  tecto_diario: {tecto}
  paralelo: {paralelo}
  rotacao_dias: {rotacao}
  permission_mode: {permission}
  settings: "{settings}"
"""
    path = tmp / "office.yaml"
    path.write_text(texto, encoding="utf-8")
    cfg, _ = officecfg.carregar(path, estrito=True)
    for modo, p in officecfg.pastas(cfg):
        p.mkdir(parents=True, exist_ok=True, mode=int(modo, 8))
    return cfg, path


def git(args, cwd) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def repo(cfg, nome="exemplo") -> Path:
    """Repo git local com um commit em <projectos>/<nome>, branch main."""
    r = officecfg.raiz(cfg, "projectos") / nome
    r.mkdir(parents=True)
    git(["init", "-q", "-b", "main"], r)
    (r / "README.md").write_text("# exemplo\n", encoding="utf-8")
    git(["add", "README.md"], r)
    git(["commit", "-q", "-m", "init"], r)
    return r


def pedido(cfg, stem="20261106T120000-0a1b2c3d", **kw) -> Path:
    """Pedido v1 válido (chaves exactas, por ordem) na fila; kw troca campos."""
    p = {"v": 1, "id": stem, "cartao_id": "m-0a1b2c3d4e", "squad": "dev", "projecto": "exemplo", "kind": "feat",
         "mode": "task", "slug": "botao-novo", "titulo": "Botão novo", "brief": "Põe um botão na página.",
         "pedido_em": "2026-11-06T12:00:00+00:00"}
    p.update(kw)
    path = officecfg.caminho(cfg, "quadro_lancar") / f"{stem}.json"
    path.write_text(json.dumps({k: p[k] for k in CHAVES if k in p} | {k: v for k, v in p.items() if k not in CHAVES},
                               ensure_ascii=False), encoding="utf-8")
    return path


def feito(cfg, stem="20261106T120000-0a1b2c3d"):
    path = officecfg.caminho(cfg, "quadro_lancar") / f"{stem}.feito.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def tarefas(cfg) -> list[str]:
    return sorted(p.name for p in officecfg.caminho(cfg, "tasks").iterdir() if p.name != "inbox")
