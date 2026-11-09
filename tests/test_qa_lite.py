"""QA do Lite: coerência estática entre install.sh, o job «fumo» (.github/workflows/ci.yml), o compose gerado, as units
do runner e o passo «Python 3.10» da CI. Nada aqui corre o instalador, Docker, Caddy ou systemd."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import officecfg  # noqa: E402

INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def gerar(*args, home):
    env = dict(os.environ, HOME=home)
    return subprocess.run([sys.executable, str(ROOT / "runner" / "gerar_compose.py"), *args],
                          capture_output=True, text=True, env=env, timeout=30, check=False)


class InstaladorVsCompose(unittest.TestCase):
    def test_env_do_instalador_cobre_as_variaveis_do_compose_e_do_caddyfile(self):  # AC-d2, AC-d3, AC-d4
        escritas = set(re.findall(r"""echo "(OFFICE_[A-Z_]+)='""", INSTALL))
        self.assertTrue(escritas, "install.sh não escreve variáveis no .env?")
        with tempfile.TemporaryDirectory() as t:
            cfg = ROOT / "office.example.yaml"
            usadas = set(re.findall(r"\{\$(OFFICE_[A-Z_]+)\}", (ROOT / "Caddyfile").read_text(encoding="utf-8")))
            for args in ((), ("--dominio", "x.example.org"),
                         ("--proxy", "traefik", "--rede", "t", "--dominio", "x.example.org")):
                r = gerar("--config", str(cfg), *args, home=t)
                self.assertEqual(r.returncode, 0, r.stderr)
                usadas |= set(re.findall(r"\$\{(OFFICE_[A-Z_]+)\}", r.stdout))
        self.assertLessEqual(usadas, escritas, f"o compose/Caddyfile usa variáveis que o .env não tem: {usadas - escritas}")

    def test_unset_antes_do_compose_cobre_o_env(self):  # o ambiente não pode sobrepor-se ao .env
        escritas = set(re.findall(r"""echo "(OFFICE_[A-Z_]+)='""", INSTALL))
        m = re.search(r"^unset (OFFICE_.*)$", INSTALL, re.M)
        self.assertIsNotNone(m)
        self.assertLessEqual(escritas, set(m.group(1).split()))
        self.assertLess(INSTALL.index(m.group(0)), INSTALL.index("docker compose -f"))

    def test_placeholders_das_units_sao_todos_substituidos(self):  # AC-c5
        substituidos = set(re.findall(r"conteudo//(@[A-Z]+@)/", INSTALL))
        for u in ("office-runner.service", "office-runner.timer"):
            usados = set(re.findall(r"@[A-Z]+@", (ROOT / "runner" / u).read_text(encoding="utf-8")))
            self.assertLessEqual(usados, substituidos, u)

    def test_service_corre_o_runner_com_uma_vez_e_o_timer_e_de_minuto(self):  # AC-c5
        s = (ROOT / "runner" / "office-runner.service").read_text(encoding="utf-8")
        t = (ROOT / "runner" / "office-runner.timer").read_text(encoding="utf-8")
        self.assertRegex(s, r"(?m)^ExecStart=.*runner/office_runner\.py --uma-vez --config @CONFIG@$")
        self.assertRegex(s, r"(?m)^Type=oneshot$")
        self.assertRegex(t, r"(?m)^OnCalendar=\*-\*-\* \*:\*:00$")
        self.assertRegex(t, r"(?m)^WantedBy=timers\.target$")


class InstaladorVsFumo(unittest.TestCase):
    def test_mensagem_de_falta_casa_com_o_grep_do_fumo(self):  # AC-d1
        self.assertIn('grep -q "^falta: claude"', CI)
        self.assertIn("printf 'falta: %s. Instala com: %s\\n' \"$1\" \"$2\" >&2", INSTALL)
        self.assertIn('precisa claude "', INSTALL)

    def test_sed_do_nome_casa_uma_linha_do_exemplo_e_a_config_fica_valida(self):  # AC-d2, fumo: nome == "ci"
        m = re.search(r'sed "s/\^  nome: Utilizador\\\$/  nome: \\"\$UTILIZADOR\\"/"', INSTALL)
        self.assertIsNotNone(m, "o sed do nome mudou: rever este teste e o passo do fumo")
        ex = (ROOT / "office.example.yaml").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^  nome: Utilizador$", ex)), 1)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "office.yaml"
            p.write_text(re.sub(r"(?m)^  nome: Utilizador$", '  nome: "ci"', ex), encoding="utf-8")
            cfg, aviso = officecfg.carregar(str(p), estrito=True)
            self.assertFalse(aviso)
            self.assertEqual(cfg["utilizador"]["nome"], "ci")
            self.assertEqual(officecfg.publico(cfg, None)["utilizador"], {"nome": "ci"})

    def test_utilizador_do_fumo_passa_na_regex_do_instalador(self):
        rx = re.search(r"^RX_UTILIZADOR='(.*)'$", INSTALL, re.M).group(1)
        self.assertRegex("ci", rx)
        self.assertIn("OFFICE_UTILIZADOR=ci", CI)

    def test_caminhos_do_fumo_sao_os_da_config_de_exemplo(self):
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, HOME=t)
            r = subprocess.run([sys.executable, "-c", "import officecfg,sys; cfg,_ = officecfg.carregar(sys.argv[1]);"
                                " print(officecfg.caminho(cfg, 'quadro_lancar')); print(officecfg.caminho(cfg, 'quadro'));"
                                " print(officecfg.caminho(cfg, 'runner'))", str(ROOT / "office.example.yaml")],
                               capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=30, check=True)
            h = str(Path(t).resolve())
            fila, quadro, runner = r.stdout.split()
        self.assertEqual(fila, f"{h}/office-lite/dados/quadro-lancar")   # fila=~/office-lite/dados/quadro-lancar
        self.assertEqual(quadro, f"{h}/office-lite/dados/quadro")        # .../quadro/cartoes.json
        self.assertTrue(runner.startswith(f"{h}/office-lite/dados/"))   # logs em falha: dados/runner/logs
        self.assertIn('CASA="$HOME/office-lite"', INSTALL)                # ~/office-lite/office.yaml, .env, compose
        for f in ("~/office-lite/office.yaml", "~/office-lite/.env", "~/office-lite/docker-compose.yml",
                  "~/office-lite/dados/quadro-lancar", "~/office-lite/dados/quadro/cartoes.json"):
            self.assertIn(f, CI)

    def test_comandos_do_fumo_existem(self):
        self.assertTrue(os.access(ROOT / "tests" / "bin" / "claude", os.X_OK))
        self.assertIn('echo "$GITHUB_WORKSPACE/tests/bin" >> "$GITHUB_PATH"', CI)
        for f in re.search(r"python -m py_compile (.*)", CI).group(1).split():
            if "*" not in f:
                self.assertTrue((ROOT / f).is_file(), f)
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t) / "office.yaml"
            cfg.write_text((ROOT / "office.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
            r = gerar("--config", str(cfg), "--proxy", "traefik", "--rede", "t", "--dominio", "x.example.org", home=t)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("caddy", r.stdout)
            self.assertNotIn("ports:", r.stdout)


class Python310(unittest.TestCase):
    """O passo «Python 3.10» da CI corre tests/test_officecfg.py e tests/test_runner_*.py com Python 3.10 (o do Ubuntu
    22.04). reader.py usa quantificadores possessivos (`*+`, Python >= 3.11): nada nesse passo pode importá-lo."""

    def _modulos(self, codigo):
        r = subprocess.run([sys.executable, "-c", f"import sys; sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'runner')!r}]; "
                            f"{codigo}; print(' '.join(sorted(sys.modules)))"],
                           capture_output=True, text=True, cwd=str(ROOT), timeout=60, check=True)
        return set(r.stdout.split())

    def test_runner_no_host_nao_importa_codigo_3_11(self):  # AC-c1 (Python >= 3.10 do Ubuntu)
        mods = self._modulos("import office_runner, gerar_compose")
        self.assertFalse({"reader", "aprovacoes", "server"} & mods)

    def test_testes_do_passo_3_10_nao_importam_reader(self):  # test_runner_comum não pode importar aprovacoes
        mods = self._modulos("import tests.test_runner_comum")
        self.assertNotIn("reader", mods, "tests/test_runner_comum.py importa aprovacoes -> reader (re `*+`): "
                         "o passo «Python 3.10» da CI falha com re.error: multiple repeat")

    def test_testes_do_passo_3_10_nao_arrancam_o_server(self):  # o server.py importa reader
        # cada teste do runner que arranca o server.py tem de saltar em < 3.11 (o servidor corre no contentor, 3.13)
        sem_skip = []
        for p in sorted((ROOT / "tests").glob("test_runner_*.py")):
            texto = p.read_text(encoding="utf-8")
            for m in re.finditer(r"(?m)^def (test_\w+)\(", texto):
                fim = texto.find("\ndef ", m.end())
                corpo = texto[m.start():fim if fim != -1 else None]
                antes = texto[texto.rfind("\n\n", 0, m.start()):m.start()]
                if re.search(r'RAIZ / "server\.py"', corpo) and "sys.version_info < (3, 11)" not in antes:
                    sem_skip.append(f"{p.name}::{m.group(1)}")
        self.assertEqual(sem_skip, [], "estes testes do runner arrancam o server.py com o Python do passo (3.10), "
                         "que falha ao importar reader.py")


def bash(script, *args):
    return subprocess.run(["bash", "-c", script, "bash", *args], capture_output=True, text=True, timeout=30,
                          check=False)


class InstaladorCredenciais(unittest.TestCase):
    """Formato das credenciais do Claude no install.sh (o runner aplica os mesmos padrões ao claude.env)."""

    def rx(self, nome):
        m = re.search(rf"^{nome}='(.*)'$", INSTALL, re.M)
        self.assertIsNotNone(m, nome)
        return m.group(1)

    def casa(self, valor, nome):
        return bash('[[ $1 =~ $2 ]]', valor, self.rx(nome)).returncode == 0

    def test_fumo_da_ci_passa_na_regra_da_chave_de_api(self):
        self.assertIn("printf 'sk-ant-fumo-%s\\n' \"$(openssl rand -hex 16)\"", CI)
        self.assertIn("OFFICE_CLAUDE_CRED=api", CI)
        self.assertTrue(self.casa("sk-ant-fumo-" + "0123456789abcdef" * 2, "RX_CRED_API"))
        self.assertTrue(self.casa("sk-ant-api03-Ab_c-9", "RX_CRED_API"))

    def test_chave_de_api_mal_formada(self):
        for mau in ("abc#def", "sk-ant-", "sk-ant-a.b", "sk-ant-a=b", "x-sk-ant-a"):
            self.assertFalse(self.casa(mau, "RX_CRED_API"), mau)

    def test_mensagens_e_ordem(self):
        i = INSTALL.index('CRED_VALOR=${CRED_VALOR%')
        b = INSTALL[i:INSTALL.index("# credenciais do claude: fim", i)]
        self.assertIn("isso é um token da subscrição: não o colas aqui; ver README, «Credenciais do Claude»", b)
        # na api, o sk-ant-oat é apanhado antes da regra geral (que o aceitaria)
        self.assertLess(b.index('!= sk-ant-oat*'), b.index("$RX_CRED_API"))
        self.assertTrue(self.casa("sk-ant-oat01-x", "RX_CRED_API"))
        self.assertNotRegex(b, r"(erro|aviso|info)[^\n]*CRED_VALOR")


class InstaladorIdentidadeGit(unittest.TestCase):
    """Identidade git (user.name/email) pedida pelo install.sh só quando falta."""

    def bloco(self):
        m = re.search(r"(?ms)^# identidade git: início\n(.*?)^# identidade git: fim$", INSTALL)
        self.assertIsNotNone(m, "falta o bloco «identidade git» no install.sh")
        return m.group(1)

    def ok(self, funcao, valor):
        return bash(self.bloco() + f'\n{funcao} "$1"', valor).returncode == 0

    def test_nome(self):
        self.assertFalse(self.ok("nome_ok", ""))
        self.assertTrue(self.ok("nome_ok", "A"))
        self.assertTrue(self.ok("nome_ok", "Á" * 100))
        self.assertFalse(self.ok("nome_ok", "a" * 101))
        self.assertTrue(self.ok("nome_ok", "Maria da Silva"))
        for c in ("\t", "\x1b", "\x7f", "\n"):
            self.assertFalse(self.ok("nome_ok", f"Maria{c}Silva"), repr(c))

    def test_email(self):
        self.assertTrue(self.ok("email_ok", "eu@exemplo.org"))
        self.assertTrue(self.ok("email_ok", "a" * 242 + "@exemplo.org"))    # 254
        self.assertFalse(self.ok("email_ok", "a" * 243 + "@exemplo.org"))   # 255
        for mau in ("", "eu", "eu@", "@exemplo.org", "eu@a@b", "eu @exemplo.org", "eu@exemplo.org\x1b", "eu@x\ty"):
            self.assertFalse(self.ok("email_ok", mau), repr(mau))

    def test_grava_so_o_que_falta_por_argv(self):
        self.assertRegex(INSTALL, r"(?m)^GIT_NOME_ACTUAL=\$\(cd / && git config user\.name \|\| true\)$")
        self.assertRegex(INSTALL, r"(?m)^GIT_EMAIL_ACTUAL=\$\(cd / && git config user\.email \|\| true\)$")
        self.assertIn('[ -z "$GIT_NOME" ] || git config --global -- user.name "$GIT_NOME" || erro', INSTALL)
        self.assertIn('[ -z "$GIT_EMAIL" ] || git config --global -- user.email "$GIT_EMAIL" || erro', INSTALL)
        self.assertEqual(len(re.findall(r"git config --global (?:-- )?user\.(?:name|email) \"", INSTALL)), 2)
        self.assertNotRegex(INSTALL, r"\beval\b|sh -c|bash -c")
        self.assertNotRegex(INSTALL, r"(echo|info|aviso|erro|printf)[^\n]*\$GIT_(NOME|EMAIL)\b")
        # pedida antes de criar seja o que for, gravada antes do repo de exemplo
        self.assertLess(INSTALL.index("GIT_NOME_ACTUAL="), INSTALL.index("umask 077"))
        self.assertLess(INSTALL.index('git config --global -- user.name'), INSTALL.index("git init -q -b main"))

    def test_sem_perguntas_avisa_e_interactivo_tenta_3_vezes(self):
        for o in ("OFFICE_GIT_NOME", "OFFICE_GIT_EMAIL"):
            self.assertIn(o, "\n".join(INSTALL.splitlines()[:12]))   # cabeçalho
        self.assertIn('aviso "a squad não consegue fazer commits: falta o user.email do git (define OFFICE_GIT_EMAIL '
                      'ou corre: git config --global user.email \\"…\\")"', INSTALL)
        self.assertIn('erro "OFFICE_GIT_EMAIL inválido (um @, sem espaços, até 254 caracteres)"', INSTALL)
        self.assertIn("for t in 1 2 3; do", INSTALL)
        fumo_vps = CI.split("\n\n  fumo-docker:\n")[0]   # o fumo do modo Docker (configurar) define-as; o do install.sh não
        self.assertNotIn("OFFICE_GIT_", fumo_vps)   # a CI de fumo não as define: só aviso, a instalação continua

    def test_pedir_git_desiste_a_terceira(self):
        i = INSTALL.index("pedir_git() {")
        funcao = INSTALL[i:INSTALL.index("\n}\n", i) + 3]
        script = ('erro() { printf "erro: %s\\n" "$*" >&2; exit 1; }\naviso() { printf "aviso: %s\\n" "$*" >&2; }\n'
                  'perguntar() { local r; read -r r; printf "%s" "$r"; }\n'
                  + self.bloco() + funcao + 'pedir_git "Nome" nome_ok "nome inválido"')
        r = subprocess.run(["bash", "-c", script], input="\n\nMaria\n", capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout, r.stderr.count("aviso:")), (0, "Maria", 2))
        r = subprocess.run(["bash", "-c", script], input="", capture_output=True, text=True, timeout=30)   # EOF
        self.assertEqual(r.returncode, 1)
        self.assertIn("erro: nome inválido", r.stderr)


if __name__ == "__main__":
    unittest.main()
