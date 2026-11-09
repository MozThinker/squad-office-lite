"""Dockerfile.runner (imagem do runner e do configurar, ADR-008): só inspecção de texto, sem docker build."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import officecfg  # noqa: E402

CASA = str(Path("/", "home", "office"))
D = (ROOT / "Dockerfile.runner").read_text(encoding="utf-8")
# instruções sem comentários e com as continuações juntas
INSTR = re.sub(r"\\\n", " ", "\n".join(x for x in D.splitlines() if not x.lstrip().startswith("#")))


def instrucoes(nome):
    return re.findall(rf"(?m)^{nome} (.*)$", INSTR)


class ImagemRunner(unittest.TestCase):
    def test_base_por_digest(self):  # AC-r1
        froms = instrucoes("FROM")
        self.assertEqual(len(froms), 2)
        for f in froms:
            self.assertRegex(f, r"^python:3\.13-slim-trixie@sha256:[0-9a-f]{64}( AS [a-z]+)?$")
        self.assertEqual(len({f.split()[0] for f in froms}), 1)   # os dois estágios na mesma base

    def test_versoes_e_sha256_fixados(self):  # AC-r1: nunca inventados, nunca latest/stable
        args = dict(re.findall(r"(?m)^ARG ([A-Z0-9_]+)=(\S+)$", INSTR))
        self.assertRegex(args.pop("GH_VERSAO"), r"^\d+\.\d+\.\d+$")
        self.assertRegex(args.pop("CLAUDE_VERSAO"), r"^\d+\.\d+\.\d+$")
        self.assertEqual(set(args),
                         {"GH_SHA256_AMD64", "GH_SHA256_ARM64", "CLAUDE_SHA256_AMD64", "CLAUDE_SHA256_ARM64"})
        for k, v in args.items():
            self.assertRegex(v, r"^[0-9a-f]{64}$", k)
        self.assertEqual(len(set(args.values())), 4)
        self.assertIn("ARG TARGETARCH", INSTR)

    def test_downloads_verificados(self):  # AC-r1: sha256sum -c antes de usar os binários
        self.assertIn("https://github.com/cli/cli/releases/download/v${GH_VERSAO}/${gh_tar}", INSTR)
        self.assertIn("https://downloads.claude.ai/claude-code-releases/${CLAUDE_VERSAO}/linux-${claude_arq}/claude",
                      INSTR)
        self.assertIn("sha256sum -c -", INSTR)
        run = INSTR[INSTR.index("WORKDIR /descarga"):]
        self.assertLess(run.index("sha256sum -c -"), run.index("tar -xzf"))
        self.assertIn("set -eu", run)
        self.assertIn("COPY --from=descarregar /descarga/gh /descarga/claude /usr/local/bin/", INSTR)
        self.assertNotRegex(INSTR, r"\| *(ba)?sh\b")   # nenhum instalador por pipe

    def test_sem_latest_stable_sudo(self):  # AC-r1, AC-r2
        self.assertNotRegex(INSTR, r"\b(latest|stable|sudo)\b")

    def test_pacotes(self):
        apt = [x for x in instrucoes("RUN") if "apt-get install" in x]
        self.assertEqual(len(apt), 2)
        for x in apt:
            self.assertIn("--no-install-recommends", x)
            self.assertIn("rm -rf /var/lib/apt/lists/*", x)
        for p in ("git", "ca-certificates", "tzdata", "apache2-utils"):
            self.assertRegex(apt[1], rf"\s{re.escape(p)}\s")
        self.assertNotIn("curl", apt[1])   # curl só no estágio de descarga

    def test_utilizador(self):  # AC-r2
        self.assertEqual(instrucoes("USER"), ["10001:10001"])
        self.assertIn("groupadd -g 10001 office", INSTR)
        # -M: sem casa criada pelo useradd (seria de 10001); a casa é ponto de montagem de root, como as outras
        self.assertIn(f"useradd -u 10001 -g 10001 -d {CASA} -M", INSTR)
        self.assertTrue(INSTR.rstrip().endswith('CMD ["ciclo"]'))
        self.assertLess(INSTR.index("USER 10001:10001"), INSTR.index('CMD ["ciclo"]'))
        self.assertEqual(instrucoes("ENTRYPOINT"), [])   # o docker compose exec não passa pelo entrypoint

    def test_env_do_contrato(self):
        env = dict(re.findall(r"([A-Z_]+)=(\S+)", " ".join(instrucoes("ENV"))))
        self.assertEqual({k: env.get(k) for k in ("OFFICE_MODO", "OFFICE_CONFIG", "OFFICE_SEGREDOS_DIR",
                                                  "DISABLE_AUTOUPDATER", "HOME")},
                         {"OFFICE_MODO": "docker", "OFFICE_CONFIG": "/config/office.yaml",
                          "OFFICE_SEGREDOS_DIR": "/segredos", "DISABLE_AUTOUPDATER": "1", "HOME": CASA})

    def test_comandos(self):  # AC-r2: ciclo, doctor, parar e configurar em /usr/local/bin
        self.assertIn("""printf '#!/bin/sh\\nexec python3 -I -u /app/runner/office_runner.py %s "$@"\\n' "$c" """
                      """> "/usr/local/bin/$c";""", INSTR)
        self.assertIn("for c in ciclo doctor parar; do", INSTR)
        self.assertIn("""printf '#!/bin/sh\\nexec python3 -I -u /app/runner/configurar.py "$@"\\n' """
                      "> /usr/local/bin/configurar", INSTR)
        self.assertIn("chmod 0755 /usr/local/bin/ciclo /usr/local/bin/doctor /usr/local/bin/parar "
                      "/usr/local/bin/configurar", INSTR)

    def test_python_isolado_da_casa(self):  # S1: ~/.local (volume runner-home) é do agente; o configurar é root
        wrappers = re.findall(r"exec (python3[^/]*)/app/", INSTR)
        self.assertEqual(len(wrappers), 2)
        for w in wrappers:
            self.assertEqual(w.split(), ["python3", "-I", "-u"])   # -u: o -I ignora o PYTHONUNBUFFERED
        self.assertEqual(len(re.findall(r"\bpython3\b", INSTR)), 2)   # nenhum outro python3 na imagem
        env = dict(re.findall(r"([A-Z_]+)=(\S+)", " ".join(instrucoes("ENV"))))
        self.assertEqual(env.get("PYTHONNOUSERSITE"), "1")

    def test_scripts_correm_com_python_isolado(self):  # -I tira a pasta do script do sys.path: importam sozinhos
        for script, args in (("office_runner.py", ["--help"]), ("configurar.py", ["--help"])):
            r = subprocess.run([sys.executable, "-I", "-u", str(ROOT / "runner" / script), *args], cwd="/",
                               capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, "-I", "-u", str(ROOT / "runner" / "configurar.py"), "--help"], cwd="/",
                           capture_output=True, text=True, timeout=30, check=True)
        self.assertIn("--office-yaml", r.stdout)

    def test_codigo_copiado(self):
        self.assertIn("WORKDIR /app", INSTR)
        self.assertIn("COPY officecfg.py office.example.yaml ./", INSTR)
        self.assertIn("COPY runner/*.py ./runner/", INSTR)
        # /app/runner/office_runner.py importa officecfg de /app (pasta acima de runner/)
        self.assertIn("sys.path.insert(1, str(Path(__file__).resolve().parent.parent))",
                      (ROOT / "runner" / "office_runner.py").read_text(encoding="utf-8"))

    def test_pontos_de_montagem_de_root(self):  # nocopy: o dono das raízes vem do configurar, não da imagem
        r = subprocess.run([sys.executable, str(ROOT / "runner" / "gerar_compose.py"), "--modo", "docker"],
                           capture_output=True, text=True, timeout=30, check=True).stdout
        servicos = r[r.index("\n  runner:"):r.index("\nconfigs:")]   # runner e configurar
        destinos = set(re.findall(r"(?m)^        target: (/\S+)$", servicos))
        self.assertIn(CASA + "/.claude/projects", destinos)
        self.assertIn("/projectos", destinos)
        criadas = re.findall(r"install -d -o (\S+) -g (\S+) -m (0[0-7]{3})\s+((?:/\S+\s*)+)", INSTR)
        self.assertEqual([c[:3] for c in criadas], [("root", "root", "0755")])
        caminhos = set(criadas[0][3].split())
        # cada destino e cada pasta-pai dele (até à raiz) existe na imagem como root 0755: qualquer OFFICE_UID passa
        pais = {str(p) for d in destinos for p in Path(d).parents if str(p) != "/"} - {str(Path(CASA).parent)}
        self.assertEqual((destinos | pais) - caminhos, set())
        self.assertNotRegex(INSTR, r"-o 10001|0750|0700|chown")

    def test_shell_dos_run(self):
        for run in instrucoes("RUN"):
            self.assertEqual(subprocess.run(["sh", "-n", "-c", run]).returncode, 0, run)


if __name__ == "__main__":
    unittest.main()
