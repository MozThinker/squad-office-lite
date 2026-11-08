"""`.github/workflows/ci.yml`: a matriz cobre as versões que o README promete (Ubuntu 22.04 ou mais recente)."""
import unittest
from pathlib import Path

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


class CiMatriz(unittest.TestCase):
    def setUp(self):
        self.ci = CI.read_text(encoding="utf-8")

    def test_fumo_em_22_24_26(self):
        self.assertIn("        os: [ubuntu-22.04, ubuntu-24.04, ubuntu-26.04]\n", self.ci)

    def test_python_310_e_314(self):
        self.assertIn('python-version: "3.10"', self.ci)
        self.assertIn('python-version: "3.14"', self.ci)

    def test_sem_proposta_pendente(self):
        self.assertFalse((CI.parent.parent / "ci.proposto.yml").exists())

    def test_sem_segredos_nem_registries(self):
        for proibido in ("secrets.", "docker push", "docker login", "docker/login-action", "push: true",
                         "packages: write"):
            self.assertNotIn(proibido, self.ci, proibido)


if __name__ == "__main__":
    unittest.main()
