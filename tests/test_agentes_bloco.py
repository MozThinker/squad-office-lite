"""CA-32: o bloco YAML que a vista «Squads e agentes» mostra para colar (static/agentes.js blocoYaml, fixture igual em
tests/agentes_test.mjs) é aceite pelo officecfg na lista squads: do office.example.yaml."""
import unittest
from pathlib import Path

from officecfg import ler_yaml, validar

ROOT = Path(__file__).resolve().parent.parent
BLOCO = (ROOT / "tests" / "fixtures" / "agentes_bloco.yaml").read_text(encoding="utf-8")


class BlocoTest(unittest.TestCase):
    def test_bloco_colado_na_lista_squads_valida(self):
        ex = (ROOT / "office.example.yaml").read_text(encoding="utf-8")
        i = ex.index("\nprojectos:")
        cfg = validar(ler_yaml(ex[:i + 1] + BLOCO + ex[i + 1:]))
        s = cfg["squads"][-1]
        self.assertEqual((s["id"], s["nome"], s["lead"], s["formato"]), ("observa", 'Obs "A" \\ B', "plantao", "worktree"))


if __name__ == "__main__":
    unittest.main()
