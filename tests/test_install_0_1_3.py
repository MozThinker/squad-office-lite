"""Instalador 0.1.3 (CA-B8, CA-B9.2, CA-A1.2): corre em bash os blocos novos do install.sh, isolados (sem Docker nem o
instalador inteiro). `gh`, `getent` e `hostname` são falsos num PATH temporário; o HOME é uma pasta temporária."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_qa_install_blocos import INSTALL, STUBS, bloco_credenciais

BASH = shutil.which("bash")


def _bloco(nome):
    i = INSTALL.index(f"# {nome}: início\n")
    fim = f"# {nome}: fim\n"
    return INSTALL[i:INSTALL.index(fim, i) + len(fim)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.bin = self.raiz / "bin"
        self.bin.mkdir()
        self.env = {"HOME": str(self.raiz), "PATH": str(self.bin)}

    def falso(self, nome, corpo):
        f = self.bin / nome
        f.write_text(f"#!{BASH}\n{corpo}\n", encoding="utf-8")
        f.chmod(0o755)

    def correr(self, script, entrada="", **extra):
        return subprocess.run([BASH, "-c", STUBS + script], input=entrada, capture_output=True, text=True,
                              timeout=30, env=dict(self.env, **extra), check=False)


class GhSemSessao(Base):   # CA-B8.1
    AVISO = "aviso: o gh não tem sessão: as tarefas acabam sem PR. Corre gh auth login\n"

    def setUp(self):
        super().setUp()
        (self.bin / "timeout").symlink_to(shutil.which("timeout"))
        self.registo = self.raiz / "gh.args"
        # o gh verdadeiro mostra parte do token no stderr: o instalador nunca o mostra
        self.falso("gh", f'printf "%s\\n" "$@" > "{self.registo}"\necho "Token: gho_SEGREDO" >&2\n'
                         'exit "${GH_RC:-0}"')

    def test_sem_sessao_avisa_e_continua(self):
        r = self.correr(_bloco("gh com sessão") + "echo continua\n", GH_RC="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, self.AVISO)
        self.assertEqual(r.stdout, "continua\n")
        self.assertEqual(self.registo.read_text(encoding="utf-8").split(), ["auth", "status"])

    def test_com_sessao_nao_diz_nada(self):
        r = self.correr(_bloco("gh com sessão"), GH_RC="0")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_sem_gh_nao_diz_nada(self):   # a falta do gh já é dita pelo «precisa gh»
        (self.bin / "gh").unlink()
        r = self.correr(_bloco("gh com sessão"))
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_depois_das_dependencias_e_antes_de_criar(self):
        i = INSTALL.index("# gh com sessão: início")
        self.assertLess(INSTALL.index('[ "$falta" = 0 ] || exit 1'), i)
        self.assertLess(i, INSTALL.index("umask 077"))


class PalavraPasseRepetida(Base):   # CA-B8.2
    DEPOIS = 'printf "PASS=[%s] PASS2=[%s]\\n" "$PASS" "${PASS2-unset}"\n'
    AVISO = "aviso: as palavras-passe não coincidem: tenta de novo\n"

    def correr_bloco(self, entrada):
        return self.correr(_bloco("palavra-passe repetida") + self.DEPOIS, entrada=entrada)

    def test_a_primeira(self):
        r = self.correr_bloco("segredo-longo-1\nsegredo-longo-1\n")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertIn("PASS=[segredo-longo-1] PASS2=[unset]", r.stdout)

    def test_a_terceira(self):
        r = self.correr_bloco("um-segredo-1\noutro-segr-2\nx\ny\nfinal-segredo\nfinal-segredo\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, self.AVISO * 2)
        self.assertIn("PASS=[final-segredo] PASS2=[unset]", r.stdout)

    def test_tres_falhas_dao_erro(self):
        r = self.correr_bloco("a1\nb1\na2\nb2\na3\nb3\nnao-lido\n")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr, self.AVISO * 2 + "erro: as palavras-passe não coincidem\n")
        self.assertNotIn("PASS=", r.stdout)
        for s in ("a1", "b1", "a3", "b3"):   # nunca no ecrã
            self.assertNotIn(s, r.stdout + r.stderr)

    def test_sem_perguntas_igual(self):
        # o ciclo só existe no ramo interactivo; com --sem-perguntas a palavra-passe continua a vir só do ficheiro
        i = INSTALL.index("# palavra-passe repetida: início")
        ramo = INSTALL[INSTALL.index('if [ "$SEM_PERGUNTAS" = 1 ]; then\n    UTILIZADOR=${OFFICE_UTILIZADOR'):i]
        self.assertIn('IFS= read -r PASS < "$OFFICE_PASSWORD_FILE"', ramo)
        self.assertEqual(ramo.count("read -r"), 1)
        self.assertEqual(INSTALL.count("as palavras-passe não coincidem"), 2)
        self.assertEqual(len(re.findall(r"read -rsp [^\n]*PASS", INSTALL)), 2)


class SetupToken(Base):   # CA-B9.1, CA-B9.2
    def setUp(self):
        super().setUp()
        self.casa = self.raiz / "casa"
        self.casa.mkdir()
        for p in ("id", "grep", "stat"):
            (self.bin / p).symlink_to(shutil.which(p))
        self.eu = subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()

    def correr_cred(self, entrada="2\n", **extra):
        return subprocess.run([BASH, "-c", bloco_credenciais()], input=entrada, capture_output=True, text=True,
                              timeout=30, env={**self.env, "CASA": str(self.casa), "CREDENCIAIS": "0",
                                               "SEM_PERGUNTAS": "0", **extra}, check=False)

    def instrucoes(self, vps):
        return (f"Para o gerar, no teu computador:\n  ssh -t {self.eu}@{vps} claude setup-token\n"
                "e copia a linha que começa por sk-ant-oat01-, não o código do browser.\n")

    def test_menu(self):
        r = self.correr_cred("3\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("  1) chave de API (recomendada; paga por uso)\n"
                      "  2) token da subscrição (escreves tu no ficheiro; o instalador mostra como)\n"
                      "  3) login já feito no claude (expira; só para experimentar)\n", r.stdout)
        self.assertNotIn("setup-token", r.stdout)

    def test_com_dominio(self):
        self.falso("hostname", "echo 10.0.0.9")
        r = self.correr_cred(DOMINIO="office.exemplo.com")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("«Credenciais do Claude».\n" + self.instrucoes("office.exemplo.com"), r.stdout)
        self.assertFalse((self.casa / "claude.env").exists())

    def test_sem_dominio_usa_o_primeiro_ip(self):
        self.falso("hostname", '[ "$1" = -I ] && echo "203.0.113.7 10.0.0.2 2001:db8::1 "')
        r = self.correr_cred()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.instrucoes("203.0.113.7"), r.stdout)

    def test_sem_ip(self):
        self.falso("hostname", "exit 1")
        r = self.correr_cred(DOMINIO="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.instrucoes("<IP da VPS>"), r.stdout)

    def test_sem_perguntas_oauth_tambem_mostra(self):
        self.falso("hostname", "echo 203.0.113.7")
        r = self.correr_cred("", SEM_PERGUNTAS="1", OFFICE_CLAUDE_CRED="oauth")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.instrucoes("203.0.113.7"), r.stdout)


    def test_claude_com_caminho_absoluto(self):   # no ssh não interactivo o ~/.local/bin não está no PATH
        self.falso("hostname", "echo 203.0.113.7")
        self.falso("claude", "exit 0")
        r = self.correr_cred()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"  ssh -t {self.eu}@203.0.113.7 {self.bin / 'claude'} setup-token\n", r.stdout)


class Dns(Base):   # CA-B8.3: só getent e hostname no PATH (nada de serviços de terceiros)
    def setUp(self):
        super().setUp()
        self.registo = self.raiz / "getent.args"
        self.falso("getent", f'printf "%s\\n" "$@" > "{self.registo}"\n[ -n "${{RESOLVE:-}}" ] || exit 2\n'
                             'for ip in $RESOLVE; do printf "%-15s STREAM %s\\n%-15s DGRAM\\n%-15s RAW\\n" '
                             '"$ip" "$2" "$ip" "$ip"; done')
        self.falso("hostname", '[ "$1" = -I ] || exit 1\n[ -z "${LOCAIS:-}" ] || echo "$LOCAIS "')

    def correr_dns(self, dominio="office.exemplo.com", **extra):
        return self.correr(_bloco("DNS do domínio") + "echo Pronto\n", DOMINIO=dominio, **extra)

    def test_nao_resolve(self):
        r = self.correr_dns(LOCAIS="203.0.113.7")
        self.assertEqual((r.returncode, r.stdout), (0, "Pronto\n"))
        self.assertEqual(r.stderr, "aviso: o domínio office.exemplo.com ainda não resolve: cria um registo A para o "
                                   "IP desta VPS\n")
        self.assertEqual(self.registo.read_text(encoding="utf-8").split(), ["ahosts", "office.exemplo.com"])

    def test_outro_ip(self):
        r = self.correr_dns(RESOLVE="198.51.100.1 198.51.100.2", LOCAIS="203.0.113.7 10.0.0.2")
        self.assertEqual((r.returncode, r.stdout), (0, "Pronto\n"))
        self.assertEqual(r.stderr, "aviso: o domínio office.exemplo.com aponta para 198.51.100.1 198.51.100.2, mas "
                                   "esta VPS tem 203.0.113.7 10.0.0.2 (se a VPS está atrás de NAT, ignora)\n")

    def test_igual(self):
        for resolve in ("203.0.113.7", "2001:db8::1 198.51.100.1", "198.51.100.1 10.0.0.2"):
            r = self.correr_dns(RESOLVE=resolve, LOCAIS="203.0.113.7 10.0.0.2 2001:db8::1")
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "Pronto\n", ""), resolve)

    def test_sem_ips_locais(self):
        r = self.correr_dns(RESOLVE="198.51.100.1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("aponta para 198.51.100.1, mas esta VPS tem nenhum IP", r.stderr)

    def test_sem_dominio_nao_verifica(self):
        r = self.correr_dns("")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "Pronto\n", ""))
        self.assertFalse(self.registo.exists())

    def test_antes_do_pronto(self):
        self.assertLess(INSTALL.index("# DNS do domínio: fim"), INSTALL.index('info "Pronto: https://'))
        self.assertLess(INSTALL.index('systemctl --user enable --now'), INSTALL.index("# DNS do domínio: início"))


class HttpdFixado(unittest.TestCase):   # CA-A1.2
    def test_digest(self):
        self.assertRegex(INSTALL, r"(?m)^HTTPD_IMAGEM=httpd:2\.4-alpine@sha256:[0-9a-f]{64}$")
        self.assertEqual(re.findall(r"httpd:[^\s)\"]*", INSTALL), [re.search(r"httpd:\S+", INSTALL).group(0)])
        self.assertIn('docker run --rm -i "$HTTPD_IMAGEM" htpasswd -niBC 12', INSTALL)
        self.assertIn("(docker run $HTTPD_IMAGEM)", INSTALL)


class BashN(unittest.TestCase):
    def test_bash_n(self):
        for f in ("install.sh", "get.sh"):
            r = subprocess.run([BASH, "-n", str(Path(__file__).resolve().parent.parent / f)], capture_output=True,
                               text=True, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
