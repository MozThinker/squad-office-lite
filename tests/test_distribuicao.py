"""Distribuição (0.1.3, bloco A): imagens fixadas por digest, dependências pip em requirements.txt e as propostas
de CI em docs/propostas-ci/ (só texto; nada aqui corre Docker nem o GitHub Actions)."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runner"))
import gerar_compose  # noqa: E402

DIGEST = r"@sha256:[0-9a-f]{64}"
PROPOSTAS = ROOT / "docs" / "propostas-ci"
CI = ROOT / ".github" / "workflows" / "ci.yml"
USES_FIXO = r"uses: [\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+"


class Digests(unittest.TestCase):   # CA-A1.1–A1.3 (o teste só vê o formato; o index confirma-se no Docker Hub)
    def test_caddy(self):
        m = re.fullmatch(rf"caddy:2\.11\.(\d+)-alpine{DIGEST}", gerar_compose.CADDY_IMAGEM)
        self.assertIsNotNone(m, gerar_compose.CADDY_IMAGEM)
        self.assertGreaterEqual(int(m.group(1)), 9)

    def test_python_no_dockerfile(self):
        primeira = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()[0]
        self.assertRegex(primeira, rf"^FROM python:3\.13-alpine{DIGEST}$")

    def test_httpd_no_instalador(self):
        s = (ROOT / "install.sh").read_text(encoding="utf-8")
        usos = re.findall(r"httpd:[^\s\"')]+", s)
        self.assertTrue(usos)
        for u in usos:
            self.assertRegex(u, rf"^httpd:2\.4-alpine{DIGEST}$")


class Requirements(unittest.TestCase):   # CA-A3.3
    def test_mesmas_versoes_da_0_1_2(self):
        linhas = [x for x in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                  if x.strip() and not x.startswith("#")]
        self.assertEqual(linhas, ["pillow==12.0.0", "cryptography==46.0.3", "cffi==2.1.1", "pycparser==3.12"])

    def test_dockerfile_usa_o_ficheiro_so_com_wheels(self):
        d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY requirements.txt ./\n", d)
        self.assertIn("RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt\n", d)
        self.assertNotIn("==", d)


class CIActiva(unittest.TestCase):   # CA-A3.1, CA-A3.2, CA-A2.4: as propostas da 0.1.3, activadas a 8/11/2026
    def test_ci_fixada_shellcheck_e_cabecalhos(self):
        p = CI.read_text(encoding="utf-8")
        usos = re.findall(r"^\s*- uses: .*$", p, re.M)
        self.assertTrue(usos)
        for u in usos:
            self.assertRegex(u.strip(), rf"^- {USES_FIXO}$")
        self.assertRegex(p, rf"ghcr\.io/gitleaks/gitleaks:v8\.24\.3{DIGEST} ")
        self.assertIn("run: shellcheck install.sh get.sh scripts/*.sh\n", p)
        passo = re.search(r"      - name: Cabeçalhos de segurança[^\n]*\n(?:        .*\n)+", p)
        self.assertIsNotNone(passo)
        for c in ("content-security-policy: frame-ancestors 'none'", "x-frame-options: DENY",
                  "referrer-policy: same-origin", "strict-transport-security", "curl -sI http://localhost/"):
            self.assertIn(c, passo.group(0))
        self.assertLess(p.index("- name: Caddy em http://localhost"), passo.start())

    def test_dependabot(self):
        d = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertRegex(d, r"(?m)^version: 2$")
        blocos = re.findall(r"  - package-ecosystem: (\S+)\n    directory: (\S+)\n    schedule:\n"
                            r"      interval: (\S+)\n", d)
        self.assertEqual(blocos, [("github-actions", "/", "weekly"), ("docker", "/", "weekly"),
                                  ("pip", "/", "weekly")])


@unittest.skipUnless(PROPOSTAS.is_dir(), "sem docs/propostas-ci (árvore pública)")
class PropostasCI(unittest.TestCase):
    def ler(self, nome):
        return (PROPOSTAS / nome).read_text(encoding="utf-8")

    def test_release(self):   # CA-E17.1
        r = self.ler("release.yml")
        self.assertRegex(r, r"(?m)^on:\n  workflow_dispatch:\n    inputs:\n      versao:\n")
        self.assertNotRegex(r, r"(?m)^  (push|pull_request|release|schedule|workflow_run):")
        perms = re.search(r"(?m)^permissions:\n((?:  .*\n)+)", r).group(1)
        self.assertEqual(sorted(perms.split()), sorted(["contents:", "write", "id-token:", "write",
                                                        "attestations:", "write"]))
        self.assertEqual(r.count("permissions:"), 1)
        for u in re.findall(r"^\s*- uses: .*$", r, re.M):
            self.assertRegex(u.strip(), rf"^- {USES_FIXO}$")
        self.assertRegex(r, r"uses: actions/attest-build-provenance@")
        self.assertIn("ref: v${{ inputs.versao }}", r)
        self.assertIn('scripts/release.sh --publica "$VERSAO"', r)
        self.assertIn("dist/squad-office-lite-${{ inputs.versao }}.tar.gz\n            dist/SHA256SUMS\n", r)
        self.assertGreaterEqual(r.count('gh release view "v$VERSAO" --json isDraft'), 2)
        self.assertIn('gh release upload "v$VERSAO" "dist/squad-office-lite-$VERSAO.tar.gz" dist/SHA256SUMS --clobber',
                      r)
        self.assertIn("GH_TOKEN: ${{ github.token }}", r)
        # nunca publica, nunca cria nem edita a release, sem segredos configurados
        for proibido in ("secrets.", "--draft=false", "gh release edit", "gh release create", "git push", "git tag"):
            self.assertNotIn(proibido, r)
        # entradas do utilizador nunca interpoladas num run: (só via env)
        for bloco in re.findall(r"run: \|\n((?:          .*\n)+)", r):
            self.assertNotIn("${{", bloco)

    def test_leia(self):   # CA-A3.4
        linhas = [x for x in self.ler("LEIA.md").splitlines() if x.strip()]
        self.assertTrue(5 <= len(linhas) <= 10, len(linhas))
        texto = "\n".join(linhas)
        for f in ("ci.yml", "dependabot.yml", "release.yml", "SHA"):
            self.assertIn(f, texto)

    def test_sem_nomes_internos(self):   # CA-G4 (a lista vem do ci.yml do repositório privado)
        m = re.search(r"^\s*nada -niE '([^']+)' -- \. ':!CHANGELOG\.md'", CI.read_text(encoding="utf-8"), re.M) \
            if CI.is_file() else None
        if m is None:
            self.skipTest("sem a lista de nomes no ci.yml (árvore pública)")
        for f in sorted(PROPOSTAS.iterdir()):
            self.assertIsNone(re.search(m.group(1), f.read_text(encoding="utf-8"), re.I), f.name)


if __name__ == "__main__":
    unittest.main()
