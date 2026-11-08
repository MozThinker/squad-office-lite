"""QA da 0.1.3 (docs): endereços fixados em MozThinker/squad-office-lite/v0.1.3 (D6), só VPS Linux (D4), «Backups»,
«Desinstalar», `doctor`, `estado.json`, menu das credenciais igual ao do instalador, Task* na omissão, cabeçalhos e
digests; CHANGELOG com a entrada completa e notas aos ADR-006/007. Só lê ficheiros do repositório."""
import re
import unittest
from pathlib import Path

import officecfg

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SECURITY = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
DECISIONS = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")
GET = (ROOT / "get.sh").read_text(encoding="utf-8")
BASE = "https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.1.3/get.sh"


def seccao(texto: str, titulo: str) -> str:
    """Texto de uma secção `## `/`### ` até ao título seguinte do mesmo nível ou superior."""
    nivel = titulo.split(" ", 1)[0]
    i = texto.index(titulo + "\n")
    m = re.compile(rf"^#{{1,{len(nivel)}}} ", re.M).search(texto, i + len(titulo) + 1)
    return texto[i:m.start() if m else len(texto)]


class Enderecos(unittest.TestCase):
    def test_um_comando_e_forma_prudente(self):   # CA-12.9 da 0.1.2, com o endereço da 0.1.3
        inst = seccao(README, "## Instalação")
        um = f"curl -fsSL {BASE} | bash -s -- --versao 0.1.3"
        self.assertIn(um, inst)
        for x in (f"curl -fsSLO {BASE}", "less get.sh", "bash get.sh --versao 0.1.3",
                  "gh repo clone MozThinker/squad-office-lite", "nunca como `root`",
                  "não te protege se o próprio repositório for alterado"):
            self.assertIn(x, inst)
        self.assertLess(inst.index("**Forma prudente**"), inst.index(um))
        self.assertIn(f"curl -fsSL {BASE} | bash", seccao(README, "### Actualizar"))
        self.assertIn(f"curl -fsSL {BASE} | bash", SECURITY)
        self.assertNotIn("/main/get.sh", README)

    def test_sem_marcadores_nem_versao_antiga(self):   # D6
        for nome, texto in (("README.md", README), ("SECURITY.md", SECURITY), ("get.sh", GET)):
            self.assertNotIn("<dono>/<repo>", texto, nome)
            self.assertNotIn("v0.1.2/get.sh", texto, nome)
            self.assertNotIn("--versao 0.1.2", texto, nome)

    def test_atestacao_igual_ao_get_sh(self):   # CA-E17
        cmd = "gh attestation verify squad-office-lite-0.1.3.tar.gz --repo MozThinker/squad-office-lite"
        self.assertIn(cmd, GET)
        self.assertIn(cmd, seccao(README, "## Instalação"))


class Readme(unittest.TestCase):
    def test_so_vps_linux(self):   # D4
        inst = seccao(README, "## Instalação")
        self.assertIn("só numa VPS Linux", inst)
        self.assertIn("Windows ou no macOS", inst)

    def test_backups_e_desinstalar(self):
        b = seccao(README, "### Backups")
        for x in ("~/office-lite/", "push/", "office-lite_caddy_data", "tar -czf", "umask 077"):
            self.assertIn(x, b)
        d = seccao(README, "### Desinstalar")
        for x in ("systemctl --user disable --now office-runner.timer", "down --rmi local", "docker volume rm office-lite_caddy_data", "rm -rf ~/office-lite"):
            self.assertIn(x, d)
        # o volume tem o nome do projecto do compose gerado
        compose = (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8")
        self.assertIn("name: office-lite\n", compose)
        self.assertIn("  caddy_data:", compose)

    def test_doctor_a_cabeca_do_o_que_fazer_se(self):
        s = seccao(README, "### O que fazer se…")
        cmd = "python3 ~/squad-office-lite/runner/office_runner.py doctor"
        self.assertIn(cmd, s)
        self.assertLess(s.index(cmd), s.index("- **"))
        self.assertIn("office_runner.py doctor", seccao(README, "### Runner"))

    def test_estado_json_e_montagem(self):
        r = seccao(README, "### Runner")
        for k in ('"v": 1', '"claude": "ok|falta"', '"credencial": "ok|falta|modo"', '"gh": "ok|sem sessão"'):
            self.assertIn(k, r)
        self.assertIn("runner-estado/", seccao(README, "### Pastas"))
        self.assertIn("`runner-estado` são montadas só de\nleitura", seccao(README, "### Pastas"))
        self.assertIn(("runner_estado", "/data/runner-estado", "ro", "RUNNER_ESTADO_DIR"), officecfg.MOUNTS)
        self.assertIn("RUNNER_ESTADO_DIR", seccao(README, "### Variáveis do servidor"))

    def test_menu_das_credenciais_igual_ao_instalador(self):
        cred = seccao(README, "### Credenciais do Claude")
        for linha in re.findall(r'^\s*info "(  [123]\) [^"]+)"', INSTALL, re.M):
            self.assertIn(linha + "\n", cred)
        self.assertEqual(len(re.findall(r'^\s*info "  [123]\) ', INSTALL, re.M)), 3)

    def test_ferramentas_task_na_omissao(self):
        r = seccao(README, "### Runner")
        for t in ("TaskCreate", "TaskGet", "TaskList", "TaskUpdate"):
            self.assertIn(t, officecfg.FERRAMENTAS)
            self.assertIn(f"`{t}`", r)

    def test_cabecalhos_digests_e_rotulos(self):
        seg = seccao(README, "### Segurança")
        for x in ("frame-ancestors 'none'", "X-Frame-Options: DENY", "Referrer-Policy: same-origin",
                  "Strict-Transport-Security", "caddy:2.11.9-alpine", "@sha256:"):
            self.assertIn(x, seg)
        self.assertNotIn("caddy:2.10", README)
        r = seccao(README, "### Runner")
        self.assertIn("«PR aberto em rascunho»", r)
        self.assertIn("«Terminou sem PR»", r)


class ChangelogEAdr(unittest.TestCase):
    def test_entrada_0_1_3(self):
        m = re.search(r"^## Lite 0\.1\.3 \(2026-11-08\)\n(.*?)^## Lite ", CHANGELOG, re.M | re.S)
        self.assertIsNotNone(m)
        e = m.group(1)
        self.assertNotIn("(em preparação)", e)
        self.assertIn("### Security", e)
        for x in ("docs/propostas-ci/", "digest", "2.11.9", "Strict-Transport-Security", "attest-build-provenance",
                  "dependabot", "office_runner.py doctor", "Primeiros passos", "Terminou sem PR"):
            self.assertIn(x, e)
        self.assertEqual(re.search(r"^## Lite (\d+\.\d+\.\d+)", CHANGELOG, re.M).group(1), officecfg.VERSAO)

    def test_notas_adr(self):
        adr6 = DECISIONS[DECISIONS.index("## ADR-006"):DECISIONS.index("## ADR-007")]
        self.assertIn("**Nota · estado do runner (2026-11-08):**", adr6)
        self.assertIn("<dados>/runner-estado/estado.json", adr6)
        adr7 = DECISIONS[DECISIONS.index("## ADR-007"):]
        self.assertIn("**Nota · atestação (2026-11-08):**", adr7)
        self.assertIn("--repo MozThinker/squad-office-lite", adr7)


if __name__ == "__main__":
    unittest.main()
