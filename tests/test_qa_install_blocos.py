"""QA (CA-7…CA-9, CA-12 da 0.1.1; C1 e CA-12.7 da 0.1.2): corre em bash os blocos do install.sh das credenciais do
Claude, da identidade git e do claude em ~/.local/bin, isolados (sem Docker nem o instalador inteiro). O git usa um
ficheiro de config temporário e o HOME é uma pasta temporária, nunca a `~` real."""
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")

STUBS = ('set -euo pipefail\n'
         'erro() { printf "erro: %s\\n" "$*" >&2; exit 1; }\n'
         'aviso() { printf "aviso: %s\\n" "$*" >&2; }\n'
         'info() { printf "%s\\n" "$*"; }\n'
         'perguntar() { local r; read -r r || true; printf "%s" "${r:-$2}"; }\n')


def _rx(nome):
    return re.search(rf"^{nome}='.*'$", INSTALL, re.M).group(0)


def _entre(inicio, fim):
    i = INSTALL.index(inicio)
    return INSTALL[i:INSTALL.index(fim, i) + len(fim)]


def bloco_credenciais():
    # escolha + validação (entre os marcadores) e a escrita do claude.env, como o instalador as corre
    escrita = _entre("# credenciais do claude: tmp 0600", "\nfi\n")
    return (STUBS + _rx("RX_CRED_API") + "\n" + _entre("# credenciais do claude: início\n", "# credenciais do claude: fim\n")
            + escrita)


def bloco_identidade():
    i = INSTALL.index("# identidade git: início\n")
    fim = 'if [ -z "$GIT_EMAIL_ACTUAL" ]; then\n'
    j = INSTALL.index("\nfi\n", INSTALL.index(fim)) + len("\nfi\n")
    grava = [x for x in INSTALL.splitlines() if x.startswith('[ -z "$GIT_') and "git config --global --" in x]
    assert len(grava) == 2, grava
    return STUBS + INSTALL[i:j] + "\n".join(grava) + "\n"


def bloco_claude():
    # ~/.local/bin + precisa claude + o PATH da unit do runner
    precisa = _entre("precisa() {", "\n}\n")
    caminho = _entre('CAMINHO_EXEC=""\n', 'CAMINHO_EXEC="$CAMINHO_EXEC:/usr/local/bin:/usr/bin:/bin"\n')
    return (STUBS + "falta=0\n" + precisa + _entre("# claude em ~/.local/bin: início\n", "# claude em ~/.local/bin: fim\n")
            + [x for x in INSTALL.splitlines(keepends=True) if x.startswith("precisa claude ")][0]
            + caminho + 'printf "falta=%s\\nCAMINHO_EXEC=%s\\n" "$falta" "$CAMINHO_EXEC"\n')


TOKEN = "sk-ant-oat01-" + secrets.token_hex(10)   # token falso, aleatório: procura-se em toda a casa


class Credenciais(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.casa = self.raiz / "casa"
        self.casa.mkdir()
        self.isco = self.raiz / "isco"
        self.isco.write_text(TOKEN + "\n", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("OFFICE_")}
        self.env.update(HOME=str(self.raiz), CASA=str(self.casa), CREDENCIAIS="0")

    def correr(self, sem_perguntas, entrada="", depois="", **extra):
        return subprocess.run(["bash", "-c", bloco_credenciais() + depois], input=entrada, capture_output=True,
                              text=True, timeout=30, env=dict(self.env, SEM_PERGUNTAS=sem_perguntas, **extra),
                              check=False)

    def sem_token_na_casa(self):
        for f in self.casa.rglob("*"):
            if f.is_file():
                self.assertNotIn(TOKEN, f.read_text(encoding="utf-8", errors="replace"), f)

    def explicou(self, r):
        self.assertIn("O instalador não pede nem guarda o token da subscrição. Escreve-o tu em "
                      f"{self.casa}/claude.env (uma linha\nCLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-…) ou em "
                      f"{self.casa}/runner.env, e corre chmod 600 no ficheiro. Ver README,\n«Credenciais do Claude».",
                      r.stdout)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)

    def test_oauth_sem_perguntas_explica_e_ignora_o_ficheiro(self):   # CA-1.2, CA-1.3
        r = self.correr("1", depois='printf "valor=[%s] chave=[%s]\\n" "$CRED_VALOR" "$CRED_CHAVE"\n',
                        OFFICE_CLAUDE_CRED="oauth", OFFICE_CLAUDE_CRED_FILE=str(self.isco))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.explicou(r)
        self.assertIn("aviso: OFFICE_CLAUDE_CRED_FILE ignorado com oauth", r.stderr)
        self.assertIn("valor=[] chave=[]", r.stdout)
        self.assertFalse((self.casa / "claude.env").exists())
        self.sem_token_na_casa()

    def test_oauth_sem_perguntas_sem_ficheiro_nao_avisa(self):
        r = self.correr("1", OFFICE_CLAUDE_CRED="oauth")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.explicou(r)
        self.assertNotIn("ignorado", r.stderr)

    def test_opcao_2_interactiva_nao_le_mais_nada(self):   # CA-1.1
        resto = self.raiz / "resto"
        r = self.correr("0", entrada=f"2\n{TOKEN}\n", depois=f'cat > "{resto}"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2) token da subscrição (escreves tu no ficheiro; o instalador mostra como)", r.stdout)
        self.explicou(r)
        self.assertEqual(resto.read_text(encoding="utf-8"), TOKEN + "\n")   # o token ficou por ler no stdin
        self.assertFalse((self.casa / "claude.env").exists())
        self.sem_token_na_casa()

    def test_credenciais_com_opcao_2_deixa_o_claude_env_intacto(self):   # CA-1.5
        cred = self.casa / "claude.env"
        cred.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-escritoamao\n", encoding="utf-8")
        cred.chmod(0o600)
        antes = hashlib.sha256(cred.read_bytes()).hexdigest()
        for sem, entrada, extra in (("0", "2\n", {}),
                                    ("1", "", {"OFFICE_CLAUDE_CRED": "oauth",
                                               "OFFICE_CLAUDE_CRED_FILE": str(self.isco)})):
            r = self.correr(sem, entrada=entrada, CREDENCIAIS="1", **extra)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"{cred} fica como está.", r.stdout)
            self.assertEqual(hashlib.sha256(cred.read_bytes()).hexdigest(), antes)
            self.assertEqual(cred.stat().st_mode & 0o777, 0o600)

    def runner_env(self, texto):
        f = self.casa / "runner.env"
        f.write_text(texto, encoding="utf-8")
        f.chmod(0o600)

    def test_credencial_no_runner_env_salta_o_menu(self):   # sem ler nem mostrar o valor
        linha = f"Credenciais do Claude: uso as do {self.casa}/runner.env (--credenciais para escolher outras).\n"
        for chave in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
            self.runner_env(f"OFFICE_TELEGRAM_CHAT=1\n{chave}={TOKEN}\n")
            resto = self.raiz / "resto"
            for sem, entrada in (("0", "1\n"), ("1", "")):
                r = self.correr(sem, entrada=entrada, depois=f'cat > "{resto}"\n')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout, linha)
                self.assertEqual(r.stderr, "")   # nem o aviso de «sem OFFICE_CLAUDE_CRED»
                self.assertEqual(resto.read_text(encoding="utf-8"), entrada)   # nada lido do stdin
                self.assertFalse((self.casa / "claude.env").exists())

    def test_runner_env_sem_credencial_mostra_o_menu(self):
        for texto in ("OFFICE_TELEGRAM_CHAT=1\n", "ANTHROPIC_API_KEY=\n", "#ANTHROPIC_API_KEY=x\n",
                      " CLAUDE_CODE_OAUTH_TOKEN=x\n"):
            self.runner_env(texto)
            r = self.correr("0", entrada="3\n")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("3) login já feito no claude (expira; só para experimentar)", r.stdout, texto)
            self.assertNotIn("runner.env (--credenciais", r.stdout)

    def test_credenciais_mostra_o_menu_mesmo_com_runner_env(self):
        self.runner_env(f"ANTHROPIC_API_KEY={TOKEN}\n")
        r = self.correr("0", entrada="3\n", CREDENCIAIS="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3) login já feito no claude (expira; só para experimentar)", r.stdout)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)

    def test_runner_env_inseguro_e_recusado_com_a_correccao(self):
        f = self.casa / "runner.env"
        for modo in (0o644, 0o640, 0o604):
            self.runner_env(f"ANTHROPIC_API_KEY={TOKEN}\n")
            f.chmod(modo)
            for sem, entrada in (("0", "3\n"), ("1", "")):
                r = self.correr(sem, entrada=entrada)
                self.assertEqual(r.returncode, 1, (oct(modo), r.stdout))
                self.assertIn(f"erro: {f} tem permissões abertas ({modo:o}): corrige com chmod 600 \"{f}\"", r.stderr)
                self.assertNotIn("uso as do", r.stdout)
                self.assertNotIn(TOKEN, r.stdout + r.stderr)
        f.unlink()
        alvo = self.raiz / "alvo.env"
        alvo.write_text(f"ANTHROPIC_API_KEY={TOKEN}\n", encoding="utf-8")
        alvo.chmod(0o600)
        f.symlink_to(alvo)
        r = self.correr("1")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"erro: {f} tem de ser um ficheiro normal (não um link)", r.stderr)
        self.assertNotIn("uso as do", r.stdout)

    def test_runner_env_0600_ou_0700_aceite(self):
        f = self.casa / "runner.env"
        for modo in (0o600, 0o400, 0o700):
            self.runner_env(f"ANTHROPIC_API_KEY={TOKEN}\n")
            f.chmod(modo)
            r = self.correr("1")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("uso as do", r.stdout)
            f.chmod(0o600)

    def test_runner_env_sem_credencial_nao_e_verificado(self):
        # só se verifica antes de dizer que se usa: um runner.env só com o Telegram não pára a instalação aqui
        self.runner_env("OFFICE_TELEGRAM_CHAT=1\n")
        (self.casa / "runner.env").chmod(0o644)
        r = self.correr("1")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_office_claude_cred_sem_perguntas_e_pedido_explicito(self):
        # com OFFICE_CLAUDE_CRED não vazio, como com --credenciais: o runner.env não salta a escolha
        self.runner_env(f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}\n")
        chave = self.raiz / "chave"
        chave.write_text("sk-ant-api03-Nova_1\n", encoding="utf-8")
        r = self.correr("1", OFFICE_CLAUDE_CRED="api", OFFICE_CLAUDE_CRED_FILE=str(chave))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("uso as do", r.stdout)
        self.assertEqual((self.casa / "claude.env").read_text(encoding="utf-8"),
                         "ANTHROPIC_API_KEY=sk-ant-api03-Nova_1\n")
        # com claude.env já existente também troca a chave (como --credenciais)
        chave.write_text("sk-ant-api03-Outra_2\n", encoding="utf-8")
        r = self.correr("1", OFFICE_CLAUDE_CRED="api", OFFICE_CLAUDE_CRED_FILE=str(chave))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.casa / "claude.env").read_text(encoding="utf-8"),
                         "ANTHROPIC_API_KEY=sk-ant-api03-Outra_2\n")
        # interactivo, a variável não conta (as respostas vêm do terminal)
        (self.casa / "claude.env").unlink()
        r = self.correr("0", entrada="1\n", OFFICE_CLAUDE_CRED="api")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("uso as do", r.stdout)

    def test_office_claude_cred_vazio_sem_perguntas_usa_o_runner_env(self):
        self.runner_env(f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}\n")
        r = self.correr("1", OFFICE_CLAUDE_CRED="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("uso as do", r.stdout)

    def api(self, valor):
        f = self.raiz / "chave"
        f.write_text(valor + "\n", encoding="utf-8")
        return self.correr("1", OFFICE_CLAUDE_CRED="api", OFFICE_CLAUDE_CRED_FILE=str(f))

    def test_api_aceite_grava_0600(self):   # CA-1.4
        for valor in ("sk-ant-api03-Ab_c-9", "sk-ant-fumo-" + "0123456789abcdef" * 2):
            r = self.api(valor)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn(valor, r.stdout + r.stderr)
            cred = self.casa / "claude.env"
            self.assertEqual(cred.read_text(encoding="utf-8"), f"ANTHROPIC_API_KEY={valor}\n")
            self.assertEqual(cred.stat().st_mode & 0o777, 0o600)
            cred.unlink()

    def recusa(self, valor, frase):
        r = self.api(valor)
        self.assertEqual(r.returncode, 1, (valor, r.stderr))
        self.assertIn(frase, r.stderr)
        self.assertNotIn(valor, r.stdout + r.stderr)   # o valor nunca aparece
        self.assertFalse((self.casa / "claude.env").exists())

    def test_api_com_token_da_subscricao(self):   # CA-1.4
        self.recusa(TOKEN, "erro: isso é um token da subscrição: não o colas aqui; ver README, «Credenciais do Claude»")
        self.recusa("sk-ant-oatSEGREDO", "isso é um token da subscrição: não o colas aqui")
        self.recusa("sk-SEGREDO", "a chave de API tem de começar por sk-ant-")
        self.sem_token_na_casa()

    def test_estatico_sem_leitura_do_token(self):   # CA-1.3
        self.assertNotIn("RX_CRED_OAUTH", INSTALL)
        self.assertNotIn("setup-token' (não aparece", INSTALL)
        self.assertEqual(len(re.findall(r"read -rsp [^\n]*CRED_VALOR", INSTALL)), 1)   # só a chave de API


class ClaudeEmLocalBin(unittest.TestCase):   # CA-12.7
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raiz = Path(self.tmp.name)
        self.home = raiz / "casa"
        self.sis = raiz / "sis"   # PATH mínimo, sem claude
        self.sis.mkdir()
        (self.sis / "dirname").symlink_to(shutil.which("dirname"))
        self.bash = shutil.which("bash")

    def correr(self):
        return subprocess.run([self.bash, "-c", bloco_claude()], capture_output=True, text=True, timeout=30,
                              env={"HOME": str(self.home), "PATH": str(self.sis)}, check=False)

    def test_usa_o_claude_de_local_bin(self):
        local = self.home / ".local" / "bin"
        local.mkdir(parents=True)
        (local / "claude").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (local / "claude").chmod(0o755)
        r = self.correr()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("falta: claude", r.stderr)
        self.assertIn("falta=0", r.stdout)
        caminho = re.search(r"^CAMINHO_EXEC=(.*)$", r.stdout, re.M).group(1).split(":")
        self.assertIn(str(local), caminho)

    def test_local_bin_nao_executavel_nao_conta(self):
        local = self.home / ".local" / "bin"
        local.mkdir(parents=True)
        (local / "claude").write_text("x", encoding="utf-8")
        r = self.correr()
        self.assertIn("falta: claude", r.stderr)

    def test_sem_claude_falta(self):
        r = self.correr()
        self.assertIn("falta: claude", r.stderr)
        self.assertIn("falta=1", r.stdout)


class IdentidadeGit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "gitconfig"
        self.cfg.write_text("", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "OFFICE_GIT_"))}
        self.env.update(HOME=self.tmp.name, GIT_CONFIG_GLOBAL=str(self.cfg), GIT_CONFIG_NOSYSTEM="1")

    def git(self, chave):
        r = subprocess.run(["git", "config", "--global", chave], capture_output=True, text=True, env=self.env,
                           check=False)
        return r.stdout.rstrip("\n")

    def correr(self, sem_perguntas, entrada="", **extra):
        return subprocess.run(["bash", "-c", bloco_identidade()], input=entrada, capture_output=True, text=True,
                              timeout=30, env=dict(self.env, SEM_PERGUNTAS=sem_perguntas, **extra), check=False)

    def test_sem_perguntas_sem_variaveis_avisa_e_continua(self):   # CA-9
        r = self.correr("1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.count("aviso: a squad não consegue fazer commits: "), 2)
        self.assertEqual((self.git("user.name"), self.git("user.email")), ("", ""))

    def test_sem_perguntas_grava_das_variaveis_mesmo_comecadas_por_hifen(self):   # CA-7, CA-9
        r = self.correr("1", OFFICE_GIT_NOME="-Maria $(id) da Silva", OFFICE_GIT_EMAIL="maria@exemplo.org")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual((self.git("user.name"), self.git("user.email")), ("-Maria $(id) da Silva", "maria@exemplo.org"))

    def test_valores_existentes_nunca_mudam(self):   # CA-7
        subprocess.run(["git", "config", "--global", "user.name", "Já Existo"], env=self.env, check=True)
        r = self.correr("1", OFFICE_GIT_NOME="Outro", OFFICE_GIT_EMAIL="eu@exemplo.org")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.git("user.name"), self.git("user.email")), ("Já Existo", "eu@exemplo.org"))

    def test_sem_perguntas_invalido_da_erro(self):   # CA-8
        r = self.correr("1", OFFICE_GIT_NOME="Maria", OFFICE_GIT_EMAIL="maria @exemplo.org")
        self.assertEqual(r.returncode, 1)
        self.assertIn("erro: OFFICE_GIT_EMAIL inválido", r.stderr)
        self.assertEqual(self.git("user.name"), "")   # nada gravado antes de validar tudo

    def test_interactivo_pede_so_o_que_falta_e_repete_se_invalido(self):   # CA-7, CA-8
        subprocess.run(["git", "config", "--global", "user.name", "Já Existo"], env=self.env, check=True)
        r = self.correr("0", entrada="sem-arroba\nmaria@exemplo.org\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.count("e-mail inválido"), 1)
        self.assertEqual((self.git("user.name"), self.git("user.email")), ("Já Existo", "maria@exemplo.org"))


if __name__ == "__main__":
    unittest.main()
