"""QA da 0.1.2: o office.example.yaml com as chaves comentadas activas valida (CA-6.4, CA-9.6) e o README diz o que o
código faz (CA-2.6, CA-5.1, CA-5.7, CA-9.2, CA-9.4, CA-13.5). Só lê ficheiros do repositório. O CA-12.9 (endereço
fixado à versão) passou para o tests/test_qa_0_1_3.py, com o endereço da 0.1.3."""
import re
import unittest
from pathlib import Path

from officecfg import ler_yaml, validar

ROOT = Path(__file__).resolve().parent.parent
EXEMPLO = (ROOT / "office.example.yaml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def seccao(titulo: str) -> str:
    """Texto de uma secção `## `/`### ` do README até ao título seguinte do mesmo nível ou superior."""
    nivel = titulo.split(" ", 1)[0]
    i = README.index(titulo + "\n")
    fim = re.compile(rf"^#{{1,{len(nivel)}}} ", re.M)
    m = fim.search(README, i + len(titulo) + 1)
    return README[i:m.start() if m else len(README)]


class ExemploComentado(unittest.TestCase):
    def test_chaves_comentadas_validam_ao_descomentar(self):
        texto = EXEMPLO
        for chave in ("a-espera", "max_usd", "modelo"):
            texto, n = re.subn(rf"^(\s*)# ({re.escape(chave)}: )", r"\1\2", texto, flags=re.M)
            self.assertEqual(n, 1, chave)
        cfg = validar(ler_yaml(texto))
        self.assertEqual(cfg["colunas"]["a-espera"], "À espera de revisão")
        self.assertEqual(cfg["runner"]["max_usd"], "5")
        self.assertEqual(cfg["runner"]["modelo"], "sonnet")

    def test_exemplo_tal_como_esta_nao_passa_flags(self):
        cfg = validar(ler_yaml(EXEMPLO))
        self.assertIsNone(cfg["runner"]["max_usd"])
        self.assertIsNone(cfg["runner"]["modelo"])


class Readme(unittest.TestCase):
    def test_lista_de_ferramentas_nao_e_fechada(self):   # CA-2.6
        # «listas fechadas» no Lançar (valores enumerados) não é a lista de ferramentas
        for titulo in ("### Runner", "### Segurança"):
            self.assertNotRegex(seccao(titulo), r"(?i)\bfechad[ao]s?\b", titulo)
        seg = seccao("### Segurança")
        self.assertIn("As negações valem para as ferramentas do Claude Code", seg)
        self.assertIn("python3", seg)
        self.assertIn("sandbox do Claude Code", seg)

    def test_parar_no_uso_e_no_o_que_fazer_se(self):   # CA-5.1, CA-5.7
        self.assertIn("office_runner.py parar TASK_ID", seccao("### Runner"))
        item = README[README.index("- **quero parar uma tarefa.**"):]
        item = item[:item.index("\n- **", 1)]
        self.assertIn("office_runner.py parar", item)
        self.assertNotIn("pkill", item)

    def test_proibidos_e_proteccao_do_ramo(self):   # CA-9.2
        runner = seccao("### Runner")
        for x in ("git -C . push origin main", "HEAD:main", "+refspec", "git -c"):
            self.assertIn(x, runner)
        self.assertIn("protege o ramo por omissão", runner)

    def test_requisitos(self):   # CA-9.4
        inst = seccao("## Instalação")
        self.assertIn("**Ubuntu 22.04 ou mais recente**", inst)
        self.assertIn("**x86_64**", inst)
        self.assertIn("não é testado", inst)
        self.assertIn("**4 GB ou mais** (requisito do Claude Code); no nosso teste, tarefas curtas correram em 1 GB",
                      inst)

    def test_badge_contribuir_e_seguranca(self):   # CA-13.5
        badge = README.splitlines()[2]
        self.assertRegex(badge, r"^\[!\[Licença: MIT\]\(https://img\.shields\.io/badge/[^)?]+\)\]\(LICENSE\)$")
        self.assertIn("SECURITY.md", seccao("## Contribuir"))
        self.assertIn("SECURITY.md", seccao("### Segurança"))
        self.assertIn("[MIT](LICENSE)", seccao("## Licença"))


if __name__ == "__main__":
    unittest.main()
