"""C13: scripts/preparar-publico.sh e scripts/release.sh contra um repositório git temporário (cópia dos dois scripts,
um CHANGELOG, docs/, um ci.yml com uma lista de nomes falsa e um install.sh falso). Sem rede, sem a `~` real (HOME e
config do git temporários)."""
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ("preparar-publico.sh", "release.sh")
NOME_INTERNO = "fulano"                # nomes falsos: a lista verdadeira só existe no ci.yml do repositório privado
LISTA = "fulan[o]|beltran[o]"
CASA_ALHEIA = "/ho" + "me/x/"
CASA_EXEMPLO = "/ho" + "me/u/"
CI = Path(".github") / "workflows" / "ci.yml"
CI_FALSO = ("jobs:\n  checks:\n    steps:\n      - name: Genérico\n        run: |\n"
            "          nada -nE 'CRON_DI[R]' -- .\n"
            f"          nada -niE '{LISTA.split('|')[0]}|/hom[e]/' -- tests/\n"
            f"          nada -niE '{LISTA}' -- . ':!CHANGELOG.md' ':!.github/workflows/ci.yml'\n"
            "          exit \"$falha\"\n")


def repo_temporario(raiz: Path, changelog="# Changelog\n\n## Lite 9.9.9 (2026-11-08)\n- coisas\n") -> tuple[Path, dict]:
    casa = raiz / "casa"
    casa.mkdir()
    (casa / "gitconfig").write_text("[user]\n\tname = Teste\n\temail = teste@exemplo.org\n[init]\n"
                                    "\tdefaultBranch = main\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(casa), GIT_CONFIG_GLOBAL=str(casa / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
    repo = raiz / "repo"
    (repo / "scripts").mkdir(parents=True)
    for s in SCRIPTS:
        shutil.copy2(ROOT / "scripts" / s, repo / "scripts" / s)
    (repo / "docs").mkdir()
    (repo / "docs" / "x.txt").write_text(f"notas internas de {NOME_INTERNO}\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (repo / CI).parent.mkdir(parents=True)
    (repo / CI).write_text(CI_FALSO, encoding="utf-8")
    (repo / "README.md").write_text(f"Exemplo: {CASA_EXEMPLO}office-lite\n", encoding="utf-8")
    inst = repo / "install.sh"
    inst.write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" > "$REGISTO"\nexit 7\n', encoding="utf-8")
    inst.chmod(0o755)
    git(repo, env, "init", "-q")
    commit(repo, env)
    return repo, env


def git(repo, env, *args):
    subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True)


def commit(repo, env):
    git(repo, env, "add", "-A")
    git(repo, env, "commit", "-qm", "x")


def correr(repo, env, script, *args):
    return subprocess.run([str(repo / "scripts" / script), *args], env=env, capture_output=True, text=True,
                          timeout=60, check=False)


class PrepararPublico(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.repo, self.env = repo_temporario(self.raiz)

    def test_exporta_sem_docs_nem_git(self):   # CA-13.1
        destino = self.raiz / "pub"
        r = correr(self.repo, self.env, "preparar-publico.sh", str(destino))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(destino)), [".github", "CHANGELOG.md", "README.md", "install.sh", "scripts"])
        self.assertTrue(os.access(destino / "install.sh", os.X_OK))

    def test_ci_proposto_fica_de_fora(self):   # CA-13.1
        (self.repo / ".github").mkdir(exist_ok=True)
        (self.repo / ".github" / "ci.proposto.yml").write_text("x: 1\n", encoding="utf-8")
        (self.repo / ".github" / "outro.yml").write_text("x: 1\n", encoding="utf-8")
        commit(self.repo, self.env)
        destino = self.raiz / "pub"
        r = correr(self.repo, self.env, "preparar-publico.sh", str(destino))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(destino / ".github")), ["outro.yml", "workflows"])

    def test_destino_vazio_aceite_nao_vazio_recusado(self):   # CA-13.1
        vazio = self.raiz / "vazio"
        vazio.mkdir()
        self.assertEqual(correr(self.repo, self.env, "preparar-publico.sh", str(vazio)).returncode, 0)
        r = correr(self.repo, self.env, "preparar-publico.sh", str(vazio))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"erro: {vazio} já existe e não é uma pasta vazia", r.stderr)
        ficheiro = self.raiz / "ficheiro"
        ficheiro.write_text("", encoding="utf-8")
        self.assertNotEqual(correr(self.repo, self.env, "preparar-publico.sh", str(ficheiro)).returncode, 0)

    def isco(self, texto, nome="isco.txt"):
        dados = texto if isinstance(texto, bytes) else f"linha 1\nlinha 2 {texto}\n".encode()
        (self.repo / nome).write_bytes(dados)
        commit(self.repo, self.env)
        shutil.rmtree(self.raiz / "pub", ignore_errors=True)
        return correr(self.repo, self.env, "preparar-publico.sh", str(self.raiz / "pub"))

    def falha(self, texto, onde="isco.txt:2\n", nome="isco.txt"):
        r = self.isco(texto, nome)
        self.assertEqual(r.returncode, 1, (texto, r.stderr))
        self.assertIn(onde, r.stderr)
        self.assertIn("erro: nomes internos na árvore pública", r.stderr)

    def test_ci_publico_sem_a_lista_de_nomes(self):   # os outros padrões do passo «Genérico» ficam
        destino = self.raiz / "pub"
        r = correr(self.repo, self.env, "preparar-publico.sh", str(destino))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((destino / CI).read_text(encoding="utf-8"),
                         CI_FALSO.replace(f"          nada -niE '{LISTA}' -- . ':!CHANGELOG.md' "
                                          "':!.github/workflows/ci.yml'\n", "").replace("fulan[o]|/hom", "/hom"))
        self.assertEqual((self.repo / CI).read_text(encoding="utf-8"), CI_FALSO)   # o do repositório fica igual

    def test_lista_legivel_fora_do_ci_falha(self):
        self.falha(LISTA)
        self.falha("BELTRAN[O]")

    def test_sem_lista_no_ci_falha(self):
        (self.repo / CI).write_text(CI_FALSO.replace("':!CHANGELOG.md'", "':!README.md'"), encoding="utf-8")
        commit(self.repo, self.env)
        r = correr(self.repo, self.env, "preparar-publico.sh", str(self.raiz / "pub"))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("erro: não encontrei a lista de nomes internos no .github/workflows/ci.yml", r.stderr)

    def test_casas_sem_barra_entre_aspas_e_users(self):
        casa = "/ho" + "me/"
        for texto in (casa + "x", f'"{casa}x"', f"'{casa}ubuntu'", f"{casa}x.y", f"{casa}u/a {casa}x",
                      "/Use" + "rs/x/", "/Use" + "rs/u", f"{casa}uu", f"{casa}utilizadora"):
            self.falha(texto)

    def test_binarios_tambem(self):
        for dados in (b"\x89PNG\x00\x01" + ("/ho" + "me/x").encode() + b"\x00\xff",
                      b"\x00\x01\x02" + NOME_INTERNO.upper().encode() + b"\x00"):
            self.falha(dados, onde="isco.bin:1\n", nome="isco.bin")

    def test_nome_interno_falha_com_ficheiro_e_linha(self):   # CA-13.2
        for texto in (NOME_INTERNO.upper(), f"cd {CASA_EXEMPLO}a {CASA_ALHEIA}b"):
            self.falha(texto)

    def test_titular_do_license_passa_so_na_linha_de_copyright(self):
        r = self.isco(f"MIT License\n\nCopyright (c) 2026 {NOME_INTERNO.title()}\n\nPermission...\n".encode(),
                      nome="LICENSE")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.falha(f"MIT License\n\nCopyright (c) 2026 X\n{NOME_INTERNO}\n".encode(), onde="LICENSE:4\n",
                   nome="LICENSE")
        self.falha(f"Copyright (c) 2026 {NOME_INTERNO}\n".encode(), onde="noutro.txt:1\n", nome="noutro.txt")

    def test_casa_de_exemplo_passa(self):   # CA-13.2
        casa = "/ho" + "me/"
        r = self.isco(f"{CASA_EXEMPLO}office-lite e {casa}utilizador/x, {casa}u, \"{casa}utilizador\" e {casa} $HOME "
                      f"{casa}$USER {casa}<utilizador>")
        self.assertEqual(r.returncode, 0, r.stderr)


class Release(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)

    def test_tarball_e_sha256sums(self):   # CA-13.3
        repo, env = repo_temporario(self.raiz)
        r = correr(repo, env, "release.sh", "9.9.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        dist = repo / "dist"
        self.assertEqual(sorted(os.listdir(dist)), ["SHA256SUMS", "squad-office-lite-9.9.9.tar.gz"])
        linha = (dist / "SHA256SUMS").read_text(encoding="utf-8")
        self.assertRegex(linha, r"^[0-9a-f]{64}  squad-office-lite-9\.9\.9\.tar\.gz\n$")
        c = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=dist, capture_output=True, text=True, check=False)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        with tarfile.open(dist / "squad-office-lite-9.9.9.tar.gz") as t:
            nomes = t.getnames()
            self.assertTrue(all(n == "squad-office-lite-9.9.9" or n.startswith("squad-office-lite-9.9.9/")
                                for n in nomes), nomes)
            self.assertIn("squad-office-lite-9.9.9/install.sh", nomes)
            self.assertNotIn("squad-office-lite-9.9.9/docs", nomes)
            self.assertTrue(all(m.uid == 0 and m.gid == 0 for m in t.getmembers()))

    def test_sem_entrada_no_changelog(self):   # CA-13.3
        repo, env = repo_temporario(self.raiz, changelog="# Changelog\n\n## Lite 9.9.90 (x)\n## Lite 9.9.8\n")
        r = correr(repo, env, "release.sh", "9.9.9")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("erro: falta a entrada 9.9.9 no CHANGELOG", r.stderr)
        self.assertFalse((repo / "dist").exists())

    def test_versao_invalida(self):
        repo, env = repo_temporario(self.raiz)
        for v in ("9.9", "v9.9.9", "9.9.9-rc1", ""):
            r = correr(repo, env, "release.sh", v)
            self.assertNotEqual(r.returncode, 0, v)
            self.assertIn("erro: versão inválida", r.stderr)

    def test_nomes_internos_impedem_a_versao(self):
        repo, env = repo_temporario(self.raiz)
        (repo / "isco.txt").write_text(NOME_INTERNO + "\n", encoding="utf-8")
        commit(repo, env)
        r = correr(repo, env, "release.sh", "9.9.9")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((repo / "dist" / "SHA256SUMS").exists())


def conteudo(tarball):
    """{nome: (modo, bytes)} de cada membro do tarball (pastas com bytes vazios)."""
    with tarfile.open(tarball) as t:
        return {m.name: (m.mode, t.extractfile(m).read() if m.isfile() else b"") for m in t.getmembers()}


class Publica(unittest.TestCase):   # CA-E17.2: release.sh --publica na árvore pública
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.repo, self.env = repo_temporario(self.raiz)

    def arvore_publica(self):
        """Repositório novo (outra raiz) com o conteúdo do tarball da dev, como o repositório público."""
        r = correr(self.repo, self.env, "release.sh", "9.9.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        tarball = self.repo / "dist" / "squad-office-lite-9.9.9.tar.gz"
        pub = self.raiz / "publico"
        pub.mkdir()
        with tarfile.open(tarball) as t:
            t.extractall(pub, filter="data")
        pub = pub / "squad-office-lite-9.9.9"
        git(pub, self.env, "init", "-q")
        commit(pub, self.env)
        return tarball, pub

    def test_mesma_arvore_que_na_dev(self):
        tarball, pub = self.arvore_publica()
        r = correr(pub, self.env, "release.sh", "--publica", "9.9.9")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(conteudo(pub / "dist" / "squad-office-lite-9.9.9.tar.gz"), conteudo(tarball))

    def test_sem_flag_na_publica_falha_como_antes(self):
        _, pub = self.arvore_publica()
        r = correr(pub, self.env, "release.sh", "9.9.9")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("erro: não encontrei a lista de nomes internos", r.stderr)
        self.assertFalse((pub / "dist" / "SHA256SUMS").exists())

    def test_publica_na_dev_recusada(self):
        r = correr(self.repo, self.env, "preparar-publico.sh", "--publica", str(self.raiz / "pub"))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("erro: --publica só na árvore pública", r.stderr)
        r = correr(self.repo, self.env, "release.sh", "--publica", "9.9.9")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("erro: --publica só na árvore pública", r.stderr)
        self.assertFalse((self.repo / "dist" / "SHA256SUMS").exists())

    def test_publica_mantem_os_caminhos_pessoais(self):
        _, pub = self.arvore_publica()
        (pub / "isco.txt").write_text(f"linha 1\nlinha 2 {CASA_ALHEIA}\n", encoding="utf-8")
        commit(pub, self.env)
        r = correr(pub, self.env, "preparar-publico.sh", "--publica", str(self.raiz / "pub2"))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("isco.txt:2\n", r.stderr)
        self.assertIn("erro: nomes internos na árvore pública", r.stderr)


class Estatico(unittest.TestCase):
    def test_lista_de_nomes_so_no_ci(self):
        ci = ROOT / CI
        m = re.search(r"^\s*nada -niE '([^']+)' -- \. ':!CHANGELOG\.md'", ci.read_text(encoding="utf-8"), re.M) \
            if ci.is_file() else None
        if m is None:
            self.skipTest("sem a lista de nomes no ci.yml (árvore pública)")
        script = (ROOT / "scripts" / "preparar-publico.sh").read_text(encoding="utf-8").lower()
        for alt in m.group(1).split("|"):
            self.assertNotIn(alt.lower(), script)
            self.assertNotIn(alt.replace("[", "").replace("]", "").lower(), script)

    def test_nada_e_publicado(self):   # CA-13.4
        for f in [ROOT / "scripts" / s for s in SCRIPTS] + [ROOT / "get.sh"]:
            # comentários não correm (o cabeçalho do get.sh ensina o `gh attestation verify`, CA-E17.3)
            texto = "\n".join(x for x in f.read_text(encoding="utf-8").splitlines() if not x.lstrip().startswith("#"))
            for proibido in (r"\bgh ", r"git push", r"git tag", r"gh release"):
                self.assertIsNone(re.search(proibido, texto), (f.name, proibido))

    def test_bash_estrito_e_executavel(self):
        for s in SCRIPTS:
            f = ROOT / "scripts" / s
            self.assertIn("set -euo pipefail", f.read_text(encoding="utf-8"))
            self.assertTrue(os.access(f, os.X_OK), s)

    @unittest.skipIf(shutil.which("shellcheck") is None, "sem shellcheck")
    def test_shellcheck(self):
        r = subprocess.run(["shellcheck", *[str(ROOT / "scripts" / s) for s in SCRIPTS]], capture_output=True,
                           text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
