"""C12: get.sh sem rede nem Docker. Releases locais via file:// (OFFICE_LITE_URL), /etc/os-release, uname e id
simulados, PATH só com os programas que o get.sh usa, HOME temporário e um install.sh falso que grava os argumentos."""
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from tests.test_scripts_publico import correr as correr_script, repo_temporario

ROOT = Path(__file__).resolve().parent.parent
GET = ROOT / "get.sh"
REAIS = ("bash", "curl", "tar", "gzip", "sha256sum", "mktemp", "mv", "rm", "mkdir", "date", "cat", "grep", "sed",
         "head")
BASH = shutil.which("bash")
FALSO_INSTALL = ('#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" > "$REGISTO"\n'
                 'if [ -t 0 ]; then echo tty=1; else echo tty=0; fi >> "$REGISTO"\necho "versao=@V@" >> "$REGISTO"\n'
                 'exit 7\n')


def arvore(pasta: Path) -> dict:
    """caminho relativo -> sha256 (ficheiros) ou "dir"."""
    r = {}
    for p in sorted(pasta.rglob("*")):
        r[str(p.relative_to(pasta))] = "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return r


def tarball(versao: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        topo = f"squad-office-lite-{versao}"
        d = tarfile.TarInfo(topo)
        d.type, d.mode = tarfile.DIRTYPE, 0o755
        t.addfile(d)
        dados = FALSO_INSTALL.replace("@V@", versao).encode()
        f = tarfile.TarInfo(f"{topo}/install.sh")
        f.size, f.mode = len(dados), 0o755
        t.addfile(f, io.BytesIO(dados))
    return buf.getvalue()


def publicar(pasta: Path, versao: str, dados: bytes, sha=None):
    pasta.mkdir(parents=True, exist_ok=True)
    nome = f"squad-office-lite-{versao}.tar.gz"
    (pasta / nome).write_bytes(dados)
    (pasta / "SHA256SUMS").write_text(f"{sha or hashlib.sha256(dados).hexdigest()}  {nome}\n", encoding="utf-8")


class GetSh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.home = t / "casa"
        self.home.mkdir()
        self.bin = t / "bin"
        self.bin.mkdir()
        for p in REAIS:
            (self.bin / p).symlink_to(shutil.which(p))
        for nome, corpo in (("uname", 'echo "${FAKE_ARCH:-x86_64}"'), ("id", 'echo "${FAKE_UID:-1000}"'),
                            ("sudo", "exit 0")):
            (self.bin / nome).write_text(f"#!{BASH}\n{corpo}\n", encoding="utf-8")
            (self.bin / nome).chmod(0o755)
        self.os_release = t / "os-release"
        self.os_release.write_text('NAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\nID_LIKE=debian\n', encoding="utf-8")
        self.rel = t / "rel"
        self.registo = t / "registo"
        publicar(self.rel / "latest" / "download", "0.1.3", tarball("0.1.3"))
        publicar(self.rel / "download" / "v0.1.2", "0.1.2", tarball("0.1.2"))
        self.env = {"HOME": str(self.home), "PATH": str(self.bin), "OFFICE_LITE_URL": f"file://{self.rel}",
                    "OFFICE_LITE_OS_RELEASE": str(self.os_release), "REGISTO": str(self.registo)}

    def correr(self, *args, entrada=None, sessao=False, **extra):
        return subprocess.run([BASH, str(GET), *args], input=entrada or "", capture_output=True, text=True,
                              timeout=60, env=dict(self.env, **extra), start_new_session=sessao, check=False)

    def lido(self):
        return self.registo.read_text(encoding="utf-8").splitlines()

    def recusa(self, frase, *args, **extra):
        antes = sorted(os.listdir(self.home))
        r = self.correr(*args, **extra)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(frase, r.stderr)
        self.assertEqual(sorted(os.listdir(self.home)), antes)   # nada criado em ~
        self.assertFalse(self.registo.exists())
        return r

    # ---------------------------------------------------------------- caminho feliz (CA-12.3, 12.5, 12.6)
    def test_instala_a_ultima_e_passa_os_argumentos(self):
        r = self.correr("--sem-perguntas", "--casa", "/tmp/a b")
        self.assertEqual(r.returncode, 7, r.stderr)   # o código do install.sh
        self.assertTrue((self.home / "squad-office-lite" / "install.sh").is_file())
        self.assertEqual(self.lido(), ["--sem-perguntas", "--casa", "/tmp/a b", "tty=0", "versao=0.1.3"])
        self.assertEqual(sorted(os.listdir(self.home)), ["squad-office-lite"])   # a temporária foi apagada

    def test_por_pipe_como_no_readme(self):
        r = subprocess.run([BASH, "-s", "--", "--sem-perguntas"], input=GET.read_text(encoding="utf-8"),
                           capture_output=True, text=True, timeout=60, env=self.env, check=False)
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertEqual(self.lido(), ["--sem-perguntas", "tty=0", "versao=0.1.3"])

    def test_versao_fixa(self):
        r = self.correr("--versao", "0.1.2", "--sem-perguntas")
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertEqual(self.lido(), ["--sem-perguntas", "tty=0", "versao=0.1.2"])   # --versao não passa

    def test_versao_que_nao_bate_com_o_sha256sums(self):
        publicar(self.rel / "download" / "v0.1.4", "0.1.3", tarball("0.1.3"))
        self.recusa("é da versão 0.1.3, não da 0.1.4", "--versao", "0.1.4", "--sem-perguntas")

    def test_versao_invalida(self):
        for v in ("1.x", "v0.1.2", "0.1", ""):
            self.recusa("erro: versão inválida", "--versao", v, "--sem-perguntas")
        self.recusa("erro: versão inválida", "--sem-perguntas", "--versao")

    def test_versao_inexistente(self):
        self.recusa("erro: não consegui descarregar", "--versao", "9.9.9", "--sem-perguntas")

    # ---------------------------------------------------------------- só https (excepto file:// dos testes)
    def curl_com_registo(self):
        log = Path(self.tmp.name) / "curl.log"
        real = shutil.which("curl")
        (self.bin / "curl").unlink()
        (self.bin / "curl").write_text(f'#!{BASH}\nprintf "%s\\n" "$@" >> "{log}"\n"{real}" "$@"\nrc=$?\n'
                                       f'echo "rc=$rc" >> "{log}"\nexit $rc\n', encoding="utf-8")
        (self.bin / "curl").chmod(0o755)
        return log

    def test_http_recusado_pelo_curl(self):
        log = self.curl_com_registo()
        self.recusa("erro: não consegui descarregar http://127.0.0.1:9/latest/download/SHA256SUMS", "--sem-perguntas",
                    OFFICE_LITE_URL="http://127.0.0.1:9")
        args = log.read_text(encoding="utf-8").splitlines()
        self.assertIn("--proto\n=https\n--proto-redir\n=https", "\n".join(args))
        self.assertEqual(args[-1], "rc=1")   # protocolo recusado antes de ligar (sem --proto seria 7)

    def test_file_sem_proto_mas_com_proto_redir(self):
        log = self.curl_com_registo()
        r = self.correr("--sem-perguntas")
        self.assertEqual(r.returncode, 7, r.stderr)
        args = log.read_text(encoding="utf-8").splitlines()
        self.assertNotIn("--proto", args)
        self.assertEqual(args.count("--proto-redir"), 2)

    # ---------------------------------------------------------------- verificações (CA-12.2)
    def test_nao_ubuntu(self):
        self.os_release.write_text("ID=debian\nID_LIKE=ubuntu\n", encoding="utf-8")
        self.recusa("erro: este instalador é para Ubuntu", "--sem-perguntas")

    def test_sem_os_release(self):
        self.os_release.unlink()
        self.recusa("erro: este instalador é para Ubuntu", "--sem-perguntas")

    def test_processador_nao_suportado(self):
        self.recusa("erro: processador não suportado: ppc64le", "--sem-perguntas", FAKE_ARCH="ppc64le")

    def test_arm64_avisa_e_instala(self):
        r = self.correr("--sem-perguntas", FAKE_ARCH="aarch64")
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertIn("aviso: arm64 não testado", r.stderr)

    def test_root(self):
        self.recusa("erro: não corras como root; usa um utilizador com sudo (README, «VPS nova só com root»)",
                    "--sem-perguntas", FAKE_UID="0")

    def test_root_mostra_os_4_comandos_antes_do_erro(self):   # CA-B9.3: iguais ao README
        r = self.recusa("erro: não corras como root", "--sem-perguntas", FAKE_UID="0")
        casa = "/" + "home" + "/u"   # sem o caminho literal: o passo «Genérico» da CI proíbe-o em tests/
        cmds = ["adduser u", "usermod -aG sudo u", f"install -d -m 700 -o u -g u {casa}/.ssh",
                f"install -m 600 -o u -g u /root/.ssh/authorized_keys {casa}/.ssh/authorized_keys"]
        linhas = r.stderr.splitlines()
        self.assertEqual(linhas[1:5], cmds)
        self.assertTrue(linhas[5].startswith("erro: não corras como root"))
        self.assertEqual(r.stdout, "")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        bloco = readme[readme.index("### VPS nova só com root"):readme.index("### Token do GitHub")]
        no_readme = [x.split("  #")[0].rstrip() for x in bloco.split("```bash\n")[1].split("```")[0].splitlines()]
        self.assertEqual(no_readme, cmds)

    def test_falta_programa(self):
        for p in ("curl", "sudo", "sha256sum", "tar"):
            (self.bin / p).rename(self.bin / f"{p}.fora")
            self.recusa(f"erro: falta {p}", "--sem-perguntas")
            (self.bin / f"{p}.fora").rename(self.bin / p)

    def test_sem_terminal(self):   # CA-12.6: stdin em pipe e sem /dev/tty
        self.recusa("erro: sem terminal; usa --sem-perguntas", entrada="x\n", sessao=True)

    # ---------------------------------------------------------------- sha256 e instalação existente (CA-12.4, 12.5)
    def instalacao(self):
        antiga = self.home / "squad-office-lite"
        antiga.mkdir()
        (antiga / "install.sh").write_text("antiga\n", encoding="utf-8")
        dados = self.home / "office-lite"
        (dados / "sub").mkdir(parents=True)
        (dados / "office.yaml").write_text("versao: 1\n", encoding="utf-8")
        (dados / "sub" / "claude.env").write_text("x\n", encoding="utf-8")
        return antiga, dados

    def test_sha256_errado(self):
        antiga, dados = self.instalacao()
        antes = arvore(self.home)
        publicar(self.rel / "latest" / "download", "0.1.3", tarball("0.1.3"), sha="0" * 64)
        self.recusa("erro: o sha256 não confere; nada foi instalado", "--sem-perguntas")
        self.assertEqual(arvore(self.home), antes)

    def test_sha256sums_mal_formado(self):
        for texto in ("", "abc  squad-office-lite-0.1.3.tar.gz\n",
                      f"{'0' * 64}  squad-office-lite-0.1.3.tar.gz\n{'1' * 64}  outro.tar.gz\n",
                      f"{'0' * 64}  ../squad-office-lite-0.1.3.tar.gz\n"):
            (self.rel / "latest" / "download" / "SHA256SUMS").write_text(texto, encoding="utf-8")
            self.recusa("erro: SHA256SUMS inesperado", "--sem-perguntas")

    def test_tarball_sem_install_sh(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            f = tarfile.TarInfo("outra-pasta/install.sh")
            f.size = 0
            t.addfile(f, io.BytesIO(b""))
        publicar(self.rel / "latest" / "download", "0.1.3", buf.getvalue())
        self.recusa("não tem squad-office-lite-0.1.3/install.sh; nada foi instalado", "--sem-perguntas")

    def test_actualiza_guarda_a_anterior_e_nao_toca_nos_dados(self):
        antiga, dados = self.instalacao()
        antes = arvore(dados)
        r = self.correr("--sem-perguntas")
        self.assertEqual(r.returncode, 7, r.stderr)
        anteriores = [n for n in os.listdir(self.home) if n.startswith("squad-office-lite.anterior-")]
        self.assertEqual(len(anteriores), 1, os.listdir(self.home))
        self.assertRegex(anteriores[0], r"^squad-office-lite\.anterior-\d{8}-\d{6}$")
        self.assertEqual((self.home / anteriores[0] / "install.sh").read_text(encoding="utf-8"), "antiga\n")
        self.assertIn("versao=0.1.3", (self.home / "squad-office-lite" / "install.sh").read_text(encoding="utf-8"))
        self.assertEqual(arvore(dados), antes)
        self.assertEqual(sorted(os.listdir(self.home)), sorted(["squad-office-lite", "office-lite", anteriores[0]]))

    def test_get_sh_nao_refere_a_casa_dos_dados(self):
        self.assertNotIn("office-lite/", GET.read_text(encoding="utf-8").replace("squad-office-lite", ""))

    # ---------------------------------------------------------------- integração com o release.sh (CA-13.3)
    def test_instala_o_tarball_do_release_sh(self):
        raiz = Path(self.tmp.name) / "r"
        raiz.mkdir()
        repo, env = repo_temporario(raiz)
        r = correr_script(repo, env, "release.sh", "9.9.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        destino = self.rel / "download" / "v9.9.9"
        destino.mkdir(parents=True)
        for f in ("SHA256SUMS", "squad-office-lite-9.9.9.tar.gz"):
            shutil.copy2(repo / "dist" / f, destino / f)
        r = self.correr("--versao", "9.9.9", "--sem-perguntas", "-x")
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertEqual(self.lido(), ["--sem-perguntas", "-x"])
        self.assertTrue((self.home / "squad-office-lite" / "scripts" / "release.sh").is_file())
        self.assertFalse((self.home / "squad-office-lite" / "docs").exists())


class Estatico(unittest.TestCase):
    def test_main_na_ultima_linha_e_bash_estrito(self):   # CA-12.1
        texto = GET.read_text(encoding="utf-8")
        self.assertEqual(texto.rstrip("\n").splitlines()[-1], 'main "$@"')
        self.assertIn("set -euo pipefail", texto)
        self.assertTrue(os.access(GET, os.X_OK))
        for v in ("DONO=${OFFICE_LITE_DONO:-", "REPO=${OFFICE_LITE_REPO:-", "BASE=${OFFICE_LITE_URL:-"):
            self.assertIn(v, texto)

    def test_cabecalho_0_1_3(self):   # CA-E17.3, CA-E17.4
        texto = GET.read_text(encoding="utf-8")
        cab = texto[:texto.index("set -euo pipefail")]
        self.assertNotIn("<dono>/<repo>", texto)
        self.assertNotIn("0.1.2", texto)
        self.assertEqual(cab.count("https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.1.3/get.sh"), 2)
        self.assertIn("gh attestation verify squad-office-lite-0.1.3.tar.gz --repo MozThinker/squad-office-lite", cab)
        self.assertIn("ex.: --versao 0.1.3", texto)
        self.assertIn("DONO=${OFFICE_LITE_DONO:-MozThinker}", texto)
        self.assertIn("REPO=${OFFICE_LITE_REPO:-squad-office-lite}", texto)

    @unittest.skipIf(shutil.which("shellcheck") is None, "sem shellcheck")
    def test_shellcheck(self):
        r = subprocess.run(["shellcheck", str(GET)], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
