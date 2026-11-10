"""Distribuição (0.1.3, bloco A): imagens fixadas por digest, dependências pip em requirements.txt e as propostas
de CI em docs/propostas-ci/ (só texto; nada aqui corre Docker nem o GitHub Actions)."""
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
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

    @unittest.skipUnless((ROOT / ".github" / "dependabot.yml").is_file(),
                         "sem .github/dependabot.yml (árvore pública: o preparar-publico.sh tira-o)")
    def test_dependabot(self):
        d = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertRegex(d, r"(?m)^version: 2$")
        blocos = re.findall(r"  - package-ecosystem: (\S+)\n    directory: (\S+)\n    schedule:\n"
                            r"      interval: (\S+)\n", d)
        self.assertEqual(blocos, [("github-actions", "/", "weekly"), ("docker", "/", "weekly"),
                                  ("pip", "/", "weekly")])

    def test_ci_com_fumo_docker(self):   # AC-p2: o job fumo-docker, activado a 9/11/2026, é o último do ci.yml
        p = CI.read_text(encoding="utf-8")
        self.assertIn("\n\n  fumo-docker:\n", p)
        job = p[p.index("\n\n  fumo-docker:\n") + 1:]
        self.assertEqual(re.findall(r"(?m)^  ([\w-]+):$", job), ["fumo-docker"])
        self.assertIn("    needs: checks\n    runs-on: ubuntu-latest\n", job)
        usos = re.findall(r"^\s*- uses: .*$", job, re.M)
        self.assertTrue(usos)
        for u in usos:
            self.assertRegex(u.strip(), rf"^- {USES_FIXO}$")
        for c in ('docker build -t "$IMG_OFFICE" .', 'docker build -f Dockerfile.runner -t "$IMG_RUNNER" .',
                  "target: /usr/local/bin/claude", "tests/bin/claude", 'echo "OFFICE_UID=$(id -u)"',
                  "configurar --sem-perguntas", "OFFICE_AUTH_PASS_FILE=/entrada/pw", "OFFICE_CLAUDE_CRED_FILE=",
                  'git init -q -b main "$pc/projectos/exemplo"', "claude --version",
                  "docker compose up -d --pull never",
                  "http://localhost:8080/", "/api/quadro/lancar", "docker compose exec -T runner doctor",
                  'docker compose exec -T runner parar "$tid"', "docker inspect", "docker.sock",
                  "OFFICE_PROXY_SEGREDO", "docker compose -f compose.example.yaml config -q", "if: failure()",
                  "docker compose down -v"):
            self.assertIn(c, job)
        self.assertLess(job.index("test ! -e .env"), job.index("compose.example.yaml config -q"))
        # nada é publicado nem descarregado do GHCR; sem segredos (o mesmo que o test_ci_matriz exige ao activo)
        for proibido in ("secrets.", "docker push", "docker login", "docker/login-action", "push: true",
                         "packages: write", "docker pull", "    build:"):
            self.assertNotIn(proibido, job)


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

    def test_imagens(self):   # AC-p1 (ADR-008)
        r = self.ler("imagens.yml")
        self.assertRegex(r, r"(?m)^on:\n  workflow_dispatch:\n    inputs:\n      versao:\n")
        self.assertNotRegex(r, r"(?m)^  (push|pull_request|release|schedule|workflow_run):")
        perms = re.search(r"(?m)^permissions:\n((?:  .*\n)+)", r).group(1)
        self.assertEqual(sorted(perms.split()), sorted(["contents:", "write", "packages:", "write", "id-token:",
                                                        "write", "attestations:", "write"]))
        self.assertEqual(r.count("permissions:"), 1)
        accoes = set()
        for u in re.findall(r"^\s*(?:- )?uses: .*$", r, re.M):
            self.assertRegex(u.strip().removeprefix("- "), rf"^{USES_FIXO}$")
            accoes.add(re.search(r"uses: ([\w.-]+/[\w.-]+)@", u).group(1))
        self.assertEqual(accoes, {"actions/checkout", "docker/setup-qemu-action", "docker/setup-buildx-action",
                                  "docker/login-action", "docker/build-push-action", "actions/attest"})
        self.assertIn("ref: v${{ inputs.versao }}", r)
        self.assertIn("password: ${{ github.token }}", r)
        self.assertIn('imagem="ghcr.io/${GH_REPO,,}"', r)
        self.assertEqual(r.count("platforms: linux/amd64,linux/arm64\n"), 2)
        self.assertEqual(r.count("push: true\n"), 2)
        self.assertIn("file: Dockerfile\n", r)
        self.assertIn("file: Dockerfile.runner\n", r)
        for nome, imagem in (("office", "IMAGEM_OFFICE"), ("runner", "IMAGEM_RUNNER")):
            self.assertIn(f"subject-name: ${{{{ env.{imagem} }}}}\n          subject-digest: "
                          f"${{{{ steps.{nome}.outputs.digest }}}}\n          push-to-registry: true\n", r)
        self.assertIn('--imagem-office "$IMAGEM_OFFICE:$VERSAO@$DIGEST_OFFICE"', r)
        self.assertIn('--imagem-runner "$IMAGEM_RUNNER:$VERSAO@$DIGEST_RUNNER"', r)
        self.assertIn("subject-path: |\n            dist/compose.yaml\n            dist/env.example\n", r)
        self.assertTrue((ROOT / "env.example").is_file())
        self.assertGreaterEqual(r.count('gh release view "v$VERSAO" --json isDraft'), 2)
        self.assertEqual(re.findall(r"gh release upload .*", r),
                         ['gh release upload "v$VERSAO" dist/compose.yaml dist/env.example --clobber'])
        self.assertIn("GH_TOKEN: ${{ github.token }}", r)
        # nunca publica, cria nem edita a release; o SHA256SUMS é do release.yml (o get.sh exige uma só linha)
        for proibido in ("secrets.", "--draft=false", "gh release edit", "gh release create", "git push", "git tag",
                         "dist/SHA256SUMS", ":latest"):
            self.assertNotIn(proibido, r)
        for bloco in re.findall(r"run: \|\n((?:          .*\n)+)", r):
            self.assertNotIn("${{", bloco)

    def test_leia(self):   # CA-A3.4
        linhas = [x for x in self.ler("LEIA.md").splitlines() if x.strip()]
        self.assertTrue(5 <= len(linhas) <= 10, len(linhas))
        texto = "\n".join(linhas)
        for f in ("ci.yml", "dependabot.yml", "release.yml", "imagens.yml", "GHCR", "públicos", "SHA"):
            self.assertIn(f, texto)
        self.assertLess(texto.index("release.yml"), texto.index("imagens.yml"))

    def test_sem_nomes_internos(self):   # CA-G4 (a lista vem do ci.yml do repositório privado)
        m = re.search(r"^\s*nada -niE '([^']+)' -- \. ':!CHANGELOG\.md'", CI.read_text(encoding="utf-8"), re.M) \
            if CI.is_file() else None
        if m is None:
            self.skipTest("sem a lista de nomes no ci.yml (árvore pública)")
        for f in sorted(PROPOSTAS.iterdir()):
            self.assertIsNone(re.search(m.group(1), f.read_text(encoding="utf-8"), re.I), f.name)


PARES_EN = (("README.md", "README.en.md"), ("CONTRIBUTING.md", "CONTRIBUTING.en.md"),
            ("SECURITY.md", "SECURITY.en.md"), ("CODE_OF_CONDUCT.md", "CODE_OF_CONDUCT.en.md"))
COMUNIDADE = ("CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md", ".github/ISSUE_TEMPLATE/bug.yml",
              ".github/ISSUE_TEMPLATE/teste-instalacao.yml", ".github/ISSUE_TEMPLATE/config.yml",
              ".github/pull_request_template.md", *(en for _, en in PARES_EN))


# Contacto das queixas do código de conduta (decisão do mantenedor, 10/11/2026). O domínio está na lista de nomes
# internos da CI, por isso aqui só fica o sha256 do endereço; o endereço em claro só pode estar nos dois códigos de
# conduta (o ci.yml exclui-os do git grep e o preparar-publico.sh aceita só a linha do e-mail).
CONTACTO_SHA256 = "81f7315e573bd15d2890c3abfa1c3935020c7a6a4de164683bd8e2efbf907231"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
TESTE = ".github/ISSUE_TEMPLATE/teste-instalacao.yml"
FIXTURE_TESTE = ROOT / "tests" / "fixtures" / "issue-teste-instalacao.md"
CAMPOS_TESTE = ("sistema", "versao_sistema", "versao_lite", "memoria", "docker_antes", "verificacoes", "ajuda",
                "primeira_tarefa", "minutos", "correu_mal", "readme_confuso", "doctor")


def _contacto(testcase, texto, nome):   # exactamente um endereço (o decidido), na secção das queixas
    emails = EMAIL.findall(texto)
    testcase.assertTrue(emails, nome)
    for e in emails:
        testcase.assertEqual(hashlib.sha256(e.encode()).hexdigest(), CONTACTO_SHA256, f"{nome}: e-mail inesperado")


def _campos(yml):   # [(tipo, id, rótulo, [opções])] pela ordem do formulário (sem PyYAML: o formato é o deste repo)
    campos = []
    for bloco in re.split(r"(?m)^  - type: ", yml)[1:]:
        tipo = bloco.split("\n", 1)[0].strip()
        if tipo == "markdown":
            continue
        cid = re.search(r"(?m)^    id: (\S+)$", bloco).group(1)
        rotulo = re.search(r"(?m)^      label: (.+)$", bloco).group(1)
        opcoes = re.findall(r"(?m)^        - (?:label: )?(.+)$", bloco)
        campos.append((tipo, cid, rotulo, opcoes))
    return campos


def _seccoes(corpo):   # o corpo que o GitHub gera: «### <rótulo>», linha vazia, valor
    partes = re.split(r"(?m)^### (.+)\n", corpo)
    return [(partes[i], partes[i + 1].strip()) for i in range(1, len(partes), 2)]


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


class Comunidade(unittest.TestCase):   # documentação e modelos para os testers (9/11/2026)
    def ler(self, nome):
        return (ROOT / nome).read_text(encoding="utf-8")

    def test_modelo_bug(self):
        b = self.ler(".github/ISSUE_TEMPLATE/bug.yml")
        for x in ("VPS (Ubuntu)", "Docker no PC (Windows)", "Docker no PC (macOS)", "Docker no PC (Linux)",
                  "Versão do Lite", "docker compose version", "docker compose exec runner doctor",
                  "office_runner.py doctor", "O que aconteceu", "O que esperavas", "[!WARNING]",
                  "**Não coles tokens, chaves de API, palavras-passe nem o conteúdo do `.env`"):
            self.assertIn(x, b)
        self.assertLess(b.index("[!WARNING]"), b.index("id: modo"))   # o aviso vem antes de qualquer campo
        self.assertIn("docker compose exec runner doctor", self.ler("README.md"))   # o comando é o do README

    def test_modelo_teste(self):   # relatório legível por script (10/11/2026): ids e rótulos estáveis e únicos
        t = self.ler(TESTE)
        self.assertRegex(t, r"(?m)^labels: \[teste-instalacao\]$")
        self.assertRegex(t, r'(?m)^title: "\[Teste\] "$')
        campos = _campos(t)
        self.assertEqual(tuple(c[1] for c in campos), CAMPOS_TESTE)
        rotulos = [c[2] for c in campos]
        self.assertEqual(len(set(rotulos)), len(rotulos))
        for r in rotulos:
            self.assertNotRegex(r, r"^[\"'\[{&*!|>%@`#]|: | #")   # escalar simples: o rótulo do YAML é o do corpo
        tipos = {c[1]: (c[0], c[3]) for c in campos}
        self.assertEqual(tipos["sistema"], ("dropdown", ["Windows", "macOS", "Linux"]))
        self.assertEqual(tipos["memoria"][1], ["8 GB", "16 GB", "Mais de 16 GB / More than 16 GB"])
        self.assertEqual(tipos["docker_antes"][1], ["Sim / Yes", "Não / No"])
        self.assertEqual([o.split(" / ")[0] for o in tipos["ajuda"][1]],
                         ["Não", "Sim, nas Discussions", "Sim, por outro meio"])
        self.assertEqual([o.split(" / ")[0] for o in tipos["primeira_tarefa"][1]],
                         ["Sim, chegou a «Feito» ou «À espera de …»", "Não", "Não cheguei a lançar"])
        for cid, tipo in (("versao_sistema", "input"), ("versao_lite", "input"), ("minutos", "input"),
                          ("correu_mal", "textarea"), ("readme_confuso", "textarea"), ("doctor", "textarea")):
            self.assertEqual(tipos[cid][0], tipo, cid)
        self.assertEqual(tipos["verificacoes"][0], "checkboxes")
        doctor = t[t.index("    id: doctor\n"):]
        self.assertIn("      render: text\n", doctor)
        for x in ("«Primeiros passos»", "4 ✓", "edição", "[!WARNING]", "**Este relatório é público.**", "e-mails",
                  "tapa-os", "SECURITY.md"):
            self.assertIn(x, t)
        self.assertLess(t.index("[!WARNING]"), t.index("    id: sistema"))   # o aviso vem antes de qualquer campo

    def test_modelo_teste_verificacoes_do_ecra(self):   # as caixas são os 4 «Primeiros passos» do static/ajuda.js
        js = self.ler("static/ajuda.js")
        passos = re.search(r"const PASSOS = \{([^}]*)\}", js).group(1)
        ecra = re.findall(r'\w+: "([^"]+)"', passos)
        self.assertEqual(len(ecra), 4)
        caixas = {c[1]: c[3] for c in _campos(self.ler(TESTE))}["verificacoes"]
        self.assertEqual([c.split(" / ")[0] for c in caixas], ecra)

    def test_fixture_do_relatorio(self):   # contrato do script que conta os relatórios: rótulos do YAML = «### …»
        campos = _campos(self.ler(TESTE))
        seccoes = _seccoes(FIXTURE_TESTE.read_text(encoding="utf-8"))
        self.assertEqual([s[0] for s in seccoes], [c[2] for c in campos])
        for (tipo, cid, _, opcoes), (_, valor) in zip(campos, seccoes):
            if valor == "_No response_":
                continue
            if tipo == "dropdown":
                self.assertIn(valor, opcoes, cid)
            elif tipo == "checkboxes":
                self.assertEqual(re.findall(r"(?m)^- \[[ x]\] (.+)$", valor), opcoes)
                self.assertEqual(len(valor.splitlines()), len(opcoes))
            elif cid == "doctor":
                self.assertRegex(valor, r"\A```text\n(?s:.*)\n```\Z")
        vazios = [cid for (_, cid, _, _), (_, v) in zip(campos, seccoes) if v == "_No response_"]
        self.assertTrue(vazios)   # a fixture mostra também um campo vazio
        self.assertNotRegex(FIXTURE_TESTE.read_text(encoding="utf-8"), EMAIL)

    def test_config_sem_issues_em_branco(self):
        c = self.ler(".github/ISSUE_TEMPLATE/config.yml")
        self.assertRegex(c, r"(?m)^blank_issues_enabled: false$")
        self.assertIn("/discussions/categories/ajuda-help\n", c)
        self.assertIn("/discussions/categories/ideias-ideas\n", c)
        self.assertIn("/security/advisories/new\n", c)
        self.assertIn("SECURITY.md", c)

    def test_codigo_de_conduta(self):
        c = self.ler("CODE_OF_CONDUCT.md")
        for x in ("Contributor Covenant", "versão 2.1", "contributor-covenant.org/pt/version/2/1/",
                  "consultada a 9/11/2026", "Report content", "vai para o suporte do GitHub"):
            self.assertIn(x, c)
        _contacto(self, c, "CODE_OF_CONDUCT.md")
        sec = c[c.index("## Cumprimento\n"):]
        self.assertRegex(sec[:sec.index("\n## ", 1)], r"em privado por e-mail para \S+@\S+\.")
        self.assertNotIn("reportar aos mantenedores", c)
        self.assertNotIn("[INSIRA", c)
        self.assertNotIn("[CONTACTO A INDICAR", c)

    def test_contributing_e_readme(self):
        c = self.ler("CONTRIBUTING.md")
        for x in ("SECURITY.md", "CODE_OF_CONDUCT.md", "Discussions", "python -m pytest tests -q",
                  "node --test tests/*.mjs", "shellcheck install.sh get.sh scripts/*.sh", "modo VPS é o caminho principal",
                  "`feat:`", "Relatório de teste da instalação"):
            self.assertIn(x, c)
        r = self.ler("README.md")
        com = r[r.index("## Comunidade\n"):r.index("## Contribuir\n")]
        for x in ("Discussions", "issues/new/choose", "Testaste? Conta-nos", "template=teste-instalacao.yml",
                  "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "SECURITY.md"):
            self.assertIn(x, com)
        contrib = r[r.index("## Contribuir\n"):r.index("## Licença\n")]
        self.assertIn("CONTRIBUTING.md", contrib)
        self.assertNotIn("preparar-publico.sh", contrib)   # sem duplicar o CONTRIBUTING

    def test_contribuicoes_em_ingles(self):   # 10/11/2026: issues, discussões e PRs nas duas línguas
        self.assertIn("Issues, discussões e PRs em português ou em inglês são bem-vindos.", self.ler("CONTRIBUTING.md"))
        self.assertIn("Issues, discussions and PRs in English or in Portuguese are welcome.",
                      self.ler("CONTRIBUTING.en.md"))

    def test_contributing_explica_como_entra_um_pr(self):   # a árvore pública é gerada: PRs aplicados pelos mantenedores
        c = self.ler("CONTRIBUTING.md")
        self.assertIn("## Como entra o teu PR\n", c)
        sec = c[c.index("## Como entra o teu PR\n"):]
        sec = sec[:sec.index("\n## ", 1)]
        for x in ("**gerado** a cada versão", "Abre o PR (ou a issue) aqui", "revêem-no aqui",
                  "são os mantenedores que aplicam a alteração", "`Co-authored-by`", "nome e o e-mail que usaste no PR",
                  "se concordares", "`CHANGELOG.md`", "**fechado** com uma ligação à versão", "«Closed»", "«Merged»",
                  "alterações grandes", "issue ou uma Discussion"):
            self.assertIn(x, sec)
        self.assertNotIn("-dev", sec)   # sem o nome do repositório de desenvolvimento
        self.assertNotRegex(sec, r"github\.com/")

    def test_modelo_de_pr(self):
        t = self.ler(".github/pull_request_template.md")
        for x in ("Como entra o teu PR", "«Closed»", "Uma só alteração", "python -m pytest tests -q",
                  "node --test tests/*.mjs", "Sem segredos", "- [ ]"):
            self.assertIn(x, t)

    def test_readme_windows_home(self):
        r = self.ler("README.md")
        req = r[r.index("  - **Windows** ([página]"):r.index("  - **macOS** ([página]")]
        for x in ("**Windows Home:**", "consultada a 9/11/2026", "Home e Education correm\n    contentores Linux",
                  "só usa contentores Linux"):
            self.assertIn(x, req)
        self.assertNotIn("nas edições que a página lista", req)
        for f in ("bug.yml", "teste-instalacao.yml"):
            self.assertIn("edição", self.ler(f".github/ISSUE_TEMPLATE/{f}"))

    def test_sem_nomes_internos(self):
        m = re.search(r"^\s*nada -niE '([^']+)' -- \. ':!CHANGELOG\.md'", CI.read_text(encoding="utf-8"), re.M) \
            if CI.is_file() else None
        for nome in COMUNIDADE:
            texto = self.ler(nome)
            if nome.startswith("CODE_OF_CONDUCT"):   # o e-mail das queixas é a única excepção (test_codigo_de_conduta)
                texto = EMAIL.sub("", texto)
            if m:
                self.assertIsNone(re.search(m.group(1), texto, re.I), nome)
            for x in re.findall(r"/(?:hom[e]|Users)/[A-Za-z0-9._-]+", texto):
                self.assertRegex(x, r"^/hom[e]/(u|utilizador|office)$", nome)

    @unittest.skipUnless(shutil.which("git") and shutil.which("bash") and _git("rev-parse", "HEAD").returncode == 0,
                         "sem git (árvore pública num tarball)")
    def test_arvore_publica_leva_os_ficheiros(self):
        em_head = [n for n in COMUNIDADE if _git("cat-file", "-e", f"HEAD:{n}").returncode == 0]
        if em_head != list(COMUNIDADE):
            self.skipTest("ficheiros da comunidade ainda não estão no HEAD")
        publica = ["--publica"] if not re.search(r"(?m)^\s*nada -niE '[^']+' -- \. ':!CHANGELOG\.md'",
                                                  CI.read_text(encoding="utf-8")) else []
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "pub"
            r = subprocess.run(["bash", str(ROOT / "scripts" / "preparar-publico.sh"), *publica, str(destino)],
                               cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            for nome in COMUNIDADE:
                self.assertEqual((destino / nome).read_bytes(), _git_show(nome), nome)
            self.assertTrue((destino / ".github" / "pull_request_template.md").is_file())
            self.assertEqual(sorted(x.name for x in (destino / ".github" / "ISSUE_TEMPLATE").iterdir()),
                             ["bug.yml", "config.yml", "teste-instalacao.yml"])


_FENCE = re.compile(r"(?ms)^[ \t]*```.*?^[ \t]*```[^\n]*$")


def _blocos(texto):
    return _FENCE.findall(texto)


def _titulos(texto, nivel):
    return re.findall(rf"(?m)^{'#' * nivel} (.+)$", _FENCE.sub("", texto))


def _ancora(titulo):   # como o GitHub: minúsculas, sem pontuação (fica \w, - e espaço), espaços por hífens
    return re.sub(r"[^\w\- ]", "", titulo.strip().lower()).replace(" ", "-")


class Ingles(unittest.TestCase):   # documentação em inglês, fase A (9/11/2026): só docs e modelos
    def ler(self, nome):
        return (ROOT / nome).read_text(encoding="utf-8")

    def test_paridade_de_seccoes_e_blocos(self):
        for pt, en in PARES_EN:
            a, b = self.ler(pt), self.ler(en)
            self.assertEqual(len(_titulos(a, 2)), len(_titulos(b, 2)), en)
            self.assertTrue(_blocos(a) or pt == "CODE_OF_CONDUCT.md", pt)
            self.assertEqual(_blocos(a), _blocos(b), en)   # os mesmos blocos, pela mesma ordem, byte a byte

    def test_readme_mesma_estrutura(self):   # o README.md (PT) mantém secções e âncoras; o EN segue-o nível a nível
        a, b = self.ler("README.md"), self.ler("README.en.md")
        for nivel in (2, 3):
            self.assertEqual(len(_titulos(a, nivel)), len(_titulos(b, nivel)), nivel)
        for titulo in ("Instalação", "No teu PC (Windows, macOS, Linux) com Docker Desktop", "Referência",
                       "Credenciais do Claude", "Segurança", "Comunidade", "Contribuir", "Licença"):
            self.assertIn(titulo, _titulos(a, 2) + _titulos(a, 3))

    def test_ligacoes_cruzadas(self):
        for pt, en in PARES_EN:
            a, b = self.ler(pt), self.ler(en)
            topo_a, topo_b = a[:a.index("\n## ")], b[:b.index("\n## ")]
            self.assertIn(f"🇬🇧 [English]({en}) | 🇵🇹 **Português**\n", topo_a, pt)
            self.assertIn(f"🇬🇧 **English** | 🇵🇹 [Português]({pt})\n", topo_b, en)

    def test_ancoras_internas_existem(self):
        for nome in ("README.md", "README.en.md"):
            texto = self.ler(nome)
            ancoras = {_ancora(t) for n in range(1, 7) for t in _titulos(texto, n)}
            for alvo in set(re.findall(r"\]\(#([^)]+)\)", texto)):
                self.assertIn(alvo, ancoras, f"{nome}: #{alvo}")
            for alvo in set(re.findall(r"\]\(README\.en\.md#([^)]+)\)", texto)):
                self.assertIn(alvo, {_ancora(t) for n in range(1, 7) for t in _titulos(self.ler("README.en.md"), n)})

    def test_ingles_aponta_para_ingles(self):
        for _, en in PARES_EN:
            texto = self.ler(en)
            for pt_ in ("SECURITY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"):
                if en != pt_.replace(".md", ".en.md"):
                    self.assertNotIn(f"]({pt_})", texto, en)
        self.assertIn("interface is currently in Portuguese only", self.ler("README.en.md"))
        self.assertIn("«Primeiros passos» (\"First steps\")", self.ler("README.en.md"))

    def test_codigo_de_conduta_original(self):
        c = self.ler("CODE_OF_CONDUCT.en.md")
        for x in ("# Contributor Covenant Code of Conduct\n", "version 2.1",
                  "contributor-covenant.org/version/2/1/code_of_conduct/code_of_conduct.md",
                  "retrieved on 9 November 2026", "*Report content*", "## Enforcement Guidelines\n",
                  "Community leaders will follow these Community Impact Guidelines", "goes to GitHub Support"):
            self.assertIn(x, c)
        self.assertNotIn("[INSERT", c)
        _contacto(self, c, "CODE_OF_CONDUCT.en.md")
        sec = c[c.index("## Enforcement\n"):]
        self.assertRegex(sec[:sec.index("\n## ", 1)], r"privately .* by email at \S+@\S+\.")
        self.assertNotIn("report it to the repository maintainers", c)
        self.assertEqual(EMAIL.findall(c), EMAIL.findall(self.ler("CODE_OF_CONDUCT.md")))

    def test_modelos_bilingues(self):
        for f in ("bug.yml", "teste-instalacao.yml"):
            t = self.ler(f".github/ISSUE_TEMPLATE/{f}")
            rotulos = re.findall(r"(?m)^      label: (.+)$", t)
            self.assertTrue(rotulos)
            for r in rotulos:
                self.assertIn(" / ", r, f)
            for d in re.findall(r"(?m)^      description: (.+)$", t):
                if d != "|":   # os blocos têm as duas línguas em linhas separadas
                    self.assertIn(" / ", d, f)
            for o in re.findall(r"(?m)^        - (.+)$", t):   # iguais nas duas línguas: nomes e tamanhos
                self.assertTrue(" / " in o or o in ("VPS (Ubuntu)", "Windows", "macOS", "Linux", "8 GB", "16 GB"), o)
            self.assertIn("SECURITY.en.md", t)
            self.assertEqual(t.count("[!WARNING]"), 1, f)   # um só aviso, nas duas línguas
            self.assertIn("**Don't paste tokens, API keys, passwords", t)
        self.assertIn("In Docker mode, in the `compose.yaml` folder", self.ler(".github/ISSUE_TEMPLATE/bug.yml"))
        c = self.ler(".github/ISSUE_TEMPLATE/config.yml")
        for linha in re.findall(r"(?m)^\s+(?:- name|about): (.+)$", c):
            self.assertIn(" / ", linha)
        p = self.ler(".github/pull_request_template.md")
        for x in ("## O quê e porquê / What and why", "## Lista de verificação / Checklist", "CONTRIBUTING.en.md",
                  "How your PR gets in", "A single change in this PR."):
            self.assertIn(x, p)


def _git_show(nome):
    return subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{nome}"], capture_output=True, check=True).stdout


if __name__ == "__main__":
    unittest.main()
