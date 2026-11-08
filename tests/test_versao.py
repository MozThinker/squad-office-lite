"""CA-G5: uma só fonte da versão do produto (officecfg.VERSAO), igual à 1.ª entrada «## Lite X.Y.Z» do CHANGELOG."""
import re
import unittest
from pathlib import Path

import officecfg

ROOT = Path(officecfg.__file__).resolve().parent


class VersaoTest(unittest.TestCase):
    def test_igual_ao_changelog(self):
        texto = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        m = re.search(r"^## Lite (\d+\.\d+\.\d+)\b", texto, re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertEqual(officecfg.VERSAO, m.group(1))

    def test_formato(self):
        # o servidor só aceita no estado.json versões X.Y.Z
        self.assertRegex(officecfg.VERSAO, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
