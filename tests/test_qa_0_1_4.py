"""QA da 0.1.4 (docs do modo Docker): endereços fixados em MozThinker/squad-office-lite/v0.1.4 (AC-g3), secção
«No teu PC» (AC-g2, D9) com comandos que existem no código e no compose, fontes com data (AC-g5), «Segurança» do modo
Docker (AC-s4), `ciclo` na «Referência · Runner», CHANGELOG, ADR-008 e CLAUDE.md (AC-p3). Só lê ficheiros do
repositório."""
import re
import unittest
from pathlib import Path

from tests.test_compose_docker import servico, volumes

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SECURITY = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
DECISIONS = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
CLAUDE = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
GET = (ROOT / "get.sh").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose.example.yaml").read_text(encoding="utf-8")
ENV = (ROOT / "env.example").read_text(encoding="utf-8")
CONFIGURAR = (ROOT / "runner" / "configurar.py").read_text(encoding="utf-8")
BASE = "https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.1.4/get.sh"
RELEASE = "https://github.com/MozThinker/squad-office-lite/releases/download/v0.1.4/"
PC = "## No teu PC (Windows, macOS, Linux) com Docker Desktop"
LICENCA = "https://docs.docker.com/subscription/desktop-license/"
DEVCONTAINER = "https://code.claude.com/docs/en/devcontainer"


def seccao(texto: str, titulo: str) -> str:
    """Texto de uma secção `## `/`### ` até ao título seguinte do mesmo nível ou superior."""
    nivel = titulo.split(" ", 1)[0]
    i = texto.index(titulo + "\n")
    m = re.compile(rf"^#{{1,{len(nivel)}}} ", re.MULTILINE).search(texto, i + len(titulo) + 1)
    return texto[i:m.start() if m else len(texto)]


class Enderecos(unittest.TestCase):   # AC-g3 (o CA-12.9 da 0.1.2 e o D6 da 0.1.3, com o endereço da 0.1.4)
    def test_um_comando_e_forma_prudente(self):
        inst = seccao(README, "## Instalação")
        um = f"curl -fsSL {BASE} | bash -s -- --versao 0.1.4"
        self.assertIn(um, inst)
        for x in (f"curl -fsSLO {BASE}", "less get.sh", "bash get.sh --versao 0.1.4",
                  "gh repo clone MozThinker/squad-office-lite", "nunca como `root`",
                  "não te protege se o próprio repositório for alterado"):
            self.assertIn(x, inst)
        self.assertLess(inst.index("**Forma prudente**"), inst.index(um))
        self.assertIn(f"curl -fsSL {BASE} | bash", seccao(README, "### Actualizar"))
        self.assertIn(f"curl -fsSL {BASE} | bash", SECURITY)
        self.assertNotIn("/main/get.sh", README)

    def test_sem_marcadores_nem_versao_antiga(self):
        for nome, texto in (("README.md", README), ("SECURITY.md", SECURITY), ("get.sh", GET)):
            self.assertNotIn("<dono>/<repo>", texto, nome)
            for v in ("0.1.2", "0.1.3"):
                self.assertNotIn(f"v{v}/get.sh", texto, nome)
                self.assertNotIn(f"squad-office-lite-{v}.tar.gz", texto, nome)
            self.assertNotIn("--versao 0.1.2", texto, nome)
        self.assertNotIn("--versao 0.1.3", README)

    def test_atestacao_igual_ao_get_sh(self):
        cmd = "gh attestation verify squad-office-lite-0.1.4.tar.gz --repo MozThinker/squad-office-lite"
        self.assertIn(cmd, GET)
        self.assertIn(cmd, seccao(README, "## Instalação"))


class NoTeuPc(unittest.TestCase):   # AC-g2, D9
    def setUp(self):
        self.pc = seccao(README, PC)

    def test_topo_diz_os_dois_modos(self):
        inst = seccao(README, "## Instalação")
        self.assertNotIn("não se instala", inst)
        self.assertIn("**numa VPS Linux (Ubuntu)**", inst)
        self.assertIn("(#no-teu-pc-windows-macos-linux-com-docker-desktop)", inst)
        self.assertIn("ou no\nteu PC, com o Docker Desktop", README.split("## Instalação")[0])

    def test_passos_pela_ordem(self):
        ordem = ["docker compose version", r"C:\Users\<nome>\office", "/Users/<nome>/office",
                 f"curl.exe -fsSLO {RELEASE}compose.yaml", f"curl -fsSLO {RELEASE}compose.yaml",
                 "docker compose run --rm configurar\n", "docker compose up -d\n", "**Abre <http://localhost:8080>**",
                 "**Lança a primeira tarefa**"]
        pos = [self.pc.index(x) for x in ordem]
        self.assertEqual(pos, sorted(pos))

    def test_requisitos_e_fontes_com_data(self):   # AC-g5
        for x in ("**2.23.1 ou mais recente**", "https://docs.docker.com/desktop/setup/install/windows-install/",
                  "https://docs.docker.com/desktop/setup/install/mac-install/", "consultadas a 8/11/2026",
                  "build\n    22631", "build 19045", "8 GB", "4 GB de memória no mínimo", "**4 GB ou mais**"):
            self.assertIn(x, self.pc)
        lim = seccao(README, "### Limitações do modo Docker")
        self.assertRegex(lim, rf"\({re.escape(LICENCA)}\), consultada a\n?\s*8/11/2026")
        self.assertRegex(lim, rf"\({re.escape(DEVCONTAINER)}\), consultada a\s+8/11/2026")
        for x in ("uso pessoal", "educação", "open source não comerciais", "menos de 250 pessoas **e** menos de 10",
                  "PC ligado e Docker Desktop aberto", "Telegram", "HTTPS", "notificações no telemóvel"):
            self.assertIn(x, lim)

    def test_linux_uid_e_variaveis_do_env_example(self):
        self.assertIn("""printf 'OFFICE_UID=%s\\nOFFICE_GID=%s\\n' "$(id -u)" "$(id -g)" >> .env""", self.pc)
        tabela = seccao(README, "### Mudar a porta, o endereço ou a pasta dos projectos")
        variaveis = re.findall(r"^# (OFFICE_[A-Z_]+)=", ENV, re.MULTILINE)
        self.assertEqual(len(variaveis), 5)
        for v in variaveis:
            self.assertIn(f"`{v}`", tabela)
            self.assertIn(f"${{{v}:-", COMPOSE)
        self.assertIn(f"curl.exe -fsSLo .env {RELEASE}env.example", tabela)
        self.assertIn("OFFICE_PROJECTOS=C:/Users/<nome>/projectos", tabela)   # barras para a frente

    def test_menu_e_flags_iguais_ao_configurar(self):
        menu = re.findall(r'info\("  ([123]\) [^"]+)"\)', CONFIGURAR)
        self.assertEqual(len(menu), 3)
        for linha in menu:
            self.assertIn(f"  {linha}\n", self.pc)
        flags = set(re.findall(r'add_argument\("--([a-z-]+)"', CONFIGURAR))
        usadas = set(re.findall(r"configurar --([a-z-]+)", README))
        self.assertTrue(usadas)
        self.assertLessEqual(usadas, flags)

    def test_token_da_subscricao(self):   # D10, D-L1
        tok = seccao(README, "### Token da subscrição no PC")
        self.assertIn("docker compose run --rm runner claude setup-token", tok)
        self.assertIn("docker compose run --rm runner claude setup-token", CONFIGURAR)
        self.assertIn("nunca corre o\n`claude setup-token`", tok)
        self.assertIn("sk-ant-oat01-", tok)

    def test_comandos_do_dia_a_dia(self):
        for x in ("docker compose exec runner doctor", "docker compose exec runner parar <id da tarefa>",
                  "docker compose logs runner", "docker compose pull\ndocker compose up -d",
                  "docker compose down\n", "docker compose down -v --rmi all",
                  "docker compose cp office:/config/office.yaml office.yaml",
                  "docker compose cp relatorio.pdf runner:/dados/biblioteca/material/"):
            self.assertIn(x, self.pc)
        for cmd in ("docker compose run --rm configurar", "docker compose pull; docker compose up -d"):
            self.assertIn(cmd, (ROOT / "runner" / "diagnostico.py").read_text(encoding="utf-8"))
            self.assertIn(cmd, self.pc)

    def test_caminhos_do_compose_existem(self):
        # editar o office.yaml: lido da pasta do leitor (bind :ro) e posto no config pelo configurar; Biblioteca pelo runner
        servicos = {s: volumes(servico(COMPOSE, s)) for s in ("office", "runner", "configurar")}
        self.assertEqual(servicos["office"]["/config"], ("config", True))
        self.assertEqual(servicos["runner"]["/dados/runner"], ("runner", False))
        self.assertEqual(servicos["runner"]["/dados/biblioteca"], ("biblioteca", False))
        for destino, vol in (("/dados/runner", "runner"), ("/config", "config")):
            self.assertEqual(servicos["configurar"][destino], (vol, False))
        self.assertIn("    pasta: material\n", (ROOT / "office.example.yaml").read_text(encoding="utf-8"))
        for vol in ("proxy", "segredos", "config", "runner-home", "claude-projects"):
            self.assertIn(f"  {vol}:\n", COMPOSE[COMPOSE.index("\nvolumes:\n"):])

    def test_sem_caminhos_pessoais(self):   # como o preparar-publico.sh: /hom[e]/<x> só nos exemplos
        for x in re.findall(r"/(?:hom[e]|Users)/[A-Za-z0-9._-]+", README + SECURITY + CHANGELOG + DECISIONS):
            self.assertRegex(x, r"^/hom[e]/(u|utilizador|office)$")


class Referencia(unittest.TestCase):
    def test_runner_descreve_o_ciclo(self):   # AC-g2
        r = seccao(README, "### Runner")
        for x in ("office_runner.py ciclo [--intervalo S] [--passagens N]", "**`ciclo`**", "`init: true`",
                  "*zombie*", "`SIGTERM`/`SIGINT`", "OFFICE_SEGREDOS_DIR", "menos de 3 min"):
            self.assertIn(x, r)

    def test_seguranca_modo_docker(self):   # AC-s4
        seg = seccao(README, "### Segurança")
        bloco = seg[seg.index("- **Modo Docker (no PC):**"):]
        bloco = bloco[:bloco.index("\n- ", 1)]
        for x in ("mesmo uid", "filas", "`~/.ssh`", "credenciais do Office",
                  "O sandbox do Claude Code não é suportado no contentor", "o isolamento é o próprio contentor",
                  "**A pasta de projectos montada é escrita pelo agente:**", "socket do Docker",
                  "nada com privilégios a executa", "`-I`", "`PYTHONNOUSERSITE=1`", "sem a config do utilizador"):
            self.assertIn(x, bloco)
        self.assertIn("gh attestation verify compose.yaml --repo MozThinker/squad-office-lite", seg)

    def test_variaveis_do_servidor(self):
        v = seccao(README, "### Variáveis do servidor")
        self.assertIn("OFFICE_PROXY_SEGREDO_FILE=/proxy/segredo", v)
        frase = "Falta configurar: corre `docker compose run --rm configurar`"
        self.assertIn(frase, v)
        self.assertIn(frase, (ROOT / "server.py").read_text(encoding="utf-8"))


class ChangelogAdrClaude(unittest.TestCase):
    def test_entrada_0_1_4(self):
        m = re.search(r"^## Lite 0\.1\.4 [^\n]*\n(.*?)^## Lite ", CHANGELOG, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m)
        e = m.group(1)
        for x in ("### Added", "### Security", "### Changed", "office_runner.py ciclo", "configurar",
                  "Dockerfile.runner", "imagens.yml", "consultadas a 8/11/2026", "consultada a\n  8/11/2026",
                  LICENCA, DEVCONTAINER, "v0.1.4"):
            self.assertIn(x, e)

    def test_adr_008(self):   # AC-p3
        self.assertIn("e o ADR-008 o modo Docker", DECISIONS.split("## ADR-001")[0])
        adr = DECISIONS[DECISIONS.index("## ADR-008"):]
        for x in ("workflow_dispatch", "**Desvios:**", "ao ADR-006", "ADR-007 (1)", "A CI dos PRs nunca publica",
                  "**Consequências:**", "Reverter"):
            self.assertIn(x, adr)

    def test_claude_md(self):   # AC-p3
        self.assertIn("A CI dos PRs nunca\n  publica; a única excepção é o workflow manual de imagens "
                      "(`imagens.yml`) do repositório público", CLAUDE)
        self.assertIn("(ADR-008)", CLAUDE)
        self.assertIn("ADR-001…008", CLAUDE)
        for x in ("Dockerfile.runner", "compose.example.yaml", "configurar.py", "--modo docker"):
            self.assertIn(x, CLAUDE)
        self.assertNotIn("nunca publica imagens em registries", CLAUDE)


if __name__ == "__main__":
    unittest.main()
