"""QA 0.5.0 (squads de negócio e acções aprovadas): o que os testes da implementação não cobriam.

- CA-23/CA-37: ADR-020/021 (com o risco S1), `ACCOES.md`/`ACCOES.en.md` (na raiz: o preparar-publico.sh exclui `docs/`),
  READMEs (conversa, `squad_conversa` na tabela da fila, motivos novos, pastas, serviço nos dois modos, chave de API) e
  CHANGELOG «Por publicar (0.5.0)»;
- CA-28: `ACCOES_UID`/`ACCOES_GID` (omissão 10002) documentados no `env.example`;
- CA-36: nenhuma chave nova no `office.yaml` (`versao: 1`; nada de acções no `officecfg.py` nem no exemplo);
- CA-38: o patch da CI aplica-se (`git apply --check`) e traz o que o critério pede;
- CA-1: o verificador dos modelos com a pasta explícita.
Só lê ficheiros do repositório (e corre o `git apply --check`, que não muda nada)."""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.test_qa_0_1_4 import seccao

ROOT = Path(__file__).resolve().parent.parent


def ler(nome: str) -> str:
    return (ROOT / nome).read_text(encoding="utf-8")


class Docs(unittest.TestCase):   # CA-23, CA-37
    def test_adr_020_021_e_risco_s1(self):
        d = ler("DECISIONS.md")
        adr20 = seccao(d, next(x for x in d.splitlines() if x.startswith("## ADR-020 ")))
        adr21 = seccao(d, next(x for x in d.splitlines() if x.startswith("## ADR-021 ")))
        self.assertIn("squad_conversa", adr20)
        self.assertIn("S1", adr21)
        self.assertIn("→ aprovado", adr21)

    def test_accoes_md_pt_e_en(self):
        for nome, titulos, termos in (
                ("ACCOES.md", ("## Ligar no modo Docker", "## Ligar no VPS", "## Dados pessoais: quem é o responsável",
                               "## Chave de API para squads de negócio", "## Risco residual (S1): a aprovação não está protegida"),
                 ("90 dias", "ANTHROPIC_API_KEY", "escrever-segredos", "instalar-accoes.sh", "TELEGRAM_TOKEN")),
                ("ACCOES.en.md", ("## Turning it on in Docker mode", "## Turning it on on a VPS",
                                  "## Personal data: who is responsible", "## API key for business squads",
                                  "## Residual risk (S1): approval is not protected"),
                 ("90 days", "ANTHROPIC_API_KEY", "escrever-segredos", "instalar-accoes.sh", "TELEGRAM_TOKEN"))):
            texto = ler(nome)
            for t in titulos:
                with self.subTest(nome=nome, titulo=t):
                    self.assertIn(t + "\n", texto)
            for t in termos:
                with self.subTest(nome=nome, termo=t):
                    self.assertIn(t, texto)

    def test_readmes(self):
        motivos = ("já há uma conversa em curso", "tecto da conversa atingido", "a conversa já", "terminou",
                   "não foi possível montar a squad")
        for nome, termos in (("README.md", ("Squad por conversa", "Chave de API para squads de negócio", "ACCOES.md")),
                             ("README.en.md", ("Squad by conversation", "API key for business squads",
                                               "ACCOES.en.md"))):
            texto = ler(nome)
            for t in (*termos, *motivos, "| `squad_conversa` | `squad, texto`", "accoes-entrada/<squad>/",
                      "decisoes/.accoes/", "instalar-accoes.sh", "ACCOES_UID", "`accoes`"):
                with self.subTest(nome=nome, termo=t):
                    self.assertIn(t, texto)

    def test_changelog_0_5_0(self):
        c = ler("CHANGELOG.md")
        # antes da publicação «## Por publicar (0.5.0)», depois «## Lite 0.5.0 (data)»
        titulo = next(x for x in c.splitlines() if x == "## Por publicar (0.5.0)" or x.startswith("## Lite 0.5.0 ("))
        seg = seccao(c, titulo)
        for t in ("#40", "#42", "**Adaptado ao Lite:**", "**Fica de fora**", "squad_conversa", "ADR-020", "ADR-021"):
            with self.subTest(t):
                self.assertIn(t, seg)


class Config(unittest.TestCase):
    def test_env_example_accoes_uid(self):   # CA-28
        env = ler("env.example")
        self.assertIn("# ACCOES_UID=10002\n", env)
        self.assertIn("# ACCOES_GID=10002\n", env)

    def test_office_yaml_sem_chaves_novas(self):   # CA-36
        ex = ler("office.example.yaml")
        self.assertIn("\nversao: 1\n", "\n" + ex)
        for f in ("officecfg.py", "office.example.yaml"):
            self.assertNotIn("accoes", ler(f).lower(), f)
            self.assertNotIn("conversa", ler(f).lower(), f)


_PATCH_CI = ROOT / "docs" / "propostas-ci" / "ci-accoes.patch"
_CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


@unittest.skipUnless(_PATCH_CI.exists() and "shellcheck instalar-accoes.sh" not in _CI_YML.read_text(encoding="utf-8"),
                     "proposta já aplicada ao ci.yml, ou árvore pública (sem docs/)")
class PatchCi(unittest.TestCase):   # CA-38: só faz sentido enquanto a proposta espera por quem pode mexer em workflows
    PATCH = _PATCH_CI

    def test_conteudo(self):
        p = self.PATCH.read_text(encoding="utf-8")
        for t in ("conversa.py", "accoes.py", "servico_accoes/*.py", "tools/verificar_compose_accoes.py",
                  "py_compile", "shellcheck instalar-accoes.sh", "sudo ./instalar-accoes.sh", "sudo -u",
                  "tests.test_instalar_accoes"):
            with self.subTest(t):
                self.assertIn(t, p)

    @unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "sem git")
    def test_git_apply_check(self):
        r = subprocess.run(["git", "apply", "--check", str(self.PATCH)], cwd=ROOT, capture_output=True, text=True,
                           timeout=30, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)


class VerificarModelos(unittest.TestCase):   # CA-1 (a SPEC escreve `verificar_modelos.py modelos`; ver QA.md)
    def test_pasta_explicita(self):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "verificar_modelos.py"),
                            str(ROOT / "modelos" / "squads")], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
