"""Docker rootless no install.sh: os blocos «docker rootless» (detecção, contexto, portas 80/443, mensagem do que
falta) e a troca de OFFICE_UID/OFFICE_GID no .env correm em bash, isolados, com um `docker`/`getcap` falsos num PATH
temporário e o sysctl num ficheiro (OFFICE_LITE_PORTA_SYSCTL). Nunca toca no Docker nem na `~` real."""
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_qa_install_blocos import INSTALL, STUBS, _entre

BLOCO = _entre("# docker rootless: início\n", "# docker rootless: fim\n")
TROCA_UID = _entre("# uid/gid dos contentores:", "\nfi\n")
ENV_VALOR = _entre("env_valor() {", "\n}\n")
PORTA = [x for x in INSTALL.splitlines() if x.startswith("PORTA_SYSCTL=")][0]


class Rootless(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.bin = self.t / "bin"
        self.bin.mkdir()
        self.chamadas = self.t / "chamadas"

    def falso(self, nome, corpo):
        f = self.bin / nome
        f.write_text(f"#!/bin/sh\necho \"{nome} $*\" >> '{self.chamadas}'\n{corpo}\n", encoding="utf-8")
        f.chmod(0o755)

    def correr(self, fim, sysctl="1024", env=None):
        (self.t / "sysctl").write_text(sysctl + "\n", encoding="utf-8")
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.t), "OFFICE_LITE_PORTA_SYSCTL": str(self.t / "sysctl"),
             "XDG_RUNTIME_DIR": str(self.t / "run")}
        e.update(env or {})
        return subprocess.run(["/bin/bash", "-c", STUBS + PORTA + "\n" + BLOCO + fim], capture_output=True, text=True,
                              env=e, timeout=30)

    # ------------------------------------------------ detecção
    def test_detecta_rootless_pelas_opcoes_de_seguranca(self):
        self.falso("docker", "echo 'name=seccomp,profile=builtin name=rootless name=cgroupns '")
        r = self.correr('docker_rootless; echo "R=$ROOTLESS"')
        self.assertIn("R=1", r.stdout, r.stderr)
        self.assertIn("docker info --format {{range .SecurityOptions}}{{.}} {{end}}",
                      self.chamadas.read_text(encoding="utf-8"))

    def test_daemon_root_nao_e_rootless(self):
        for saida in ("name=apparmor name=seccomp,profile=builtin ", "name=rootlessx ", ""):
            with self.subTest(saida=saida):
                self.falso("docker", f"echo '{saida}'")
                self.assertIn("R=0", self.correr('docker_rootless; echo "R=$ROOTLESS"').stdout)
        self.falso("docker", "exit 1")
        self.assertIn("R=0", self.correr('docker_rootless; echo "R=$ROOTLESS"').stdout)

    # ------------------------------------------------ portas 80/443
    def test_portas_pelo_sysctl(self):
        for valor, ok in (("80", True), ("0", True), ("79", True), ("81", False), ("1024", False), ("x", False),
                          ("", False)):
            with self.subTest(valor=valor):
                r = self.correr("portas_rootless && echo SIM || echo NAO", sysctl=valor)
                self.assertIn("SIM" if ok else "NAO", r.stdout, r.stderr)

    def test_portas_pelo_setcap_no_rootlesskit(self):
        self.falso("rootlesskit", "exit 0")
        self.falso("getcap", "echo \"$1 cap_net_bind_service=ep\"")
        self.assertIn("SIM", self.correr("portas_rootless && echo SIM || echo NAO").stdout)
        self.falso("getcap", "exit 0")
        self.assertIn("NAO", self.correr("portas_rootless && echo SIM || echo NAO").stdout)

    def test_sysctl_do_install_e_do_doctor_iguais(self):
        from tests.test_runner_comum import diagnostico
        cmd = [x for x in INSTALL.splitlines() if "ip_unprivileged_port_start=80 |" in x][0].split("'")[1].strip().removesuffix("\\n")
        self.assertTrue(diagnostico.SYSCTL_CMD.endswith(cmd), (cmd, diagnostico.SYSCTL_CMD))
        self.assertIn("ip_unprivileged_port_start=80 ", cmd)   # 80, não 0: as portas abaixo (o ssh na 22) continuam do root

    # ------------------------------------------------ o que falta
    def test_sem_acesso_recomenda_rootless(self):
        (self.bin / "id").symlink_to(shutil.which("id"))   # PATH só com os falsos: o setuptool do host não conta
        r = self.correr("falta_docker", env={"PATH": str(self.bin)})
        self.assertIn("falta: acesso ao Docker. Recomendado: Docker rootless", r.stderr)
        self.assertIn("docker-ce-rootless-extras, uidmap e dbus-user-session", r.stderr)
        self.assertIn("equivale a dar root", r.stderr)
        self.falso("dockerd-rootless-setuptool.sh", "exit 0")
        r = self.correr("falta_docker", env={"PATH": str(self.bin)})
        self.assertIn("dockerd-rootless-setuptool.sh install (README, «Docker rootless»)", r.stderr)
        self.assertNotIn("usermod", r.stderr)

    # ------------------------------------------------ contexto
    def _socket(self):
        run = self.t / "run"
        run.mkdir()
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(run / "docker.sock"))
        self.addCleanup(s.close)
        return run / "docker.sock"

    def test_contexto_rootless_activado_quando_so_o_socket_responde(self):
        sock = self._socket()
        # sem --host o docker falha (contexto default); com --host responde; context inspect falha (não existe)
        self.falso("docker", 'case "$1" in --host) exit 0 ;; info) exit 1 ;; context) [ "$2" != inspect ] ;; esac')
        r = self.correr("docker_contexto", env={"DOCKER_HOST": "unix:///outro"})
        self.assertEqual(r.returncode, 0, r.stderr)
        ch = self.chamadas.read_text(encoding="utf-8")
        self.assertIn(f"docker context create rootless --docker host=unix://{sock}", ch)
        self.assertIn("docker context use rootless", ch)
        self.assertIn("contexto «rootless»", r.stdout)

    def test_contexto_nao_muda_se_o_docker_ja_responde(self):
        self._socket()
        self.falso("docker", "exit 0")
        self.correr("docker_contexto")
        self.assertNotIn("context", self.chamadas.read_text(encoding="utf-8"))

    def test_contexto_sem_socket_nao_faz_nada(self):
        self.falso("docker", "exit 1")
        self.correr("docker_contexto")
        self.assertFalse(self.chamadas.exists())


class TrocaUid(unittest.TestCase):
    def correr(self, antes, rootless, uid="1001", gid="1001"):
        t = Path(tempfile.mkdtemp())
        env = t / ".env"
        env.write_text(antes, encoding="utf-8")
        env.chmod(0o600)
        fixo = "0" if rootless else uid
        fixg = "0" if rootless else gid
        script = (STUBS + f'ENVF="{env}"\nROOTLESS={rootless}\nOFFICE_UID_ENV={fixo}\nOFFICE_GID_ENV={fixg}\n'
                  + ENV_VALOR + "\n" + TROCA_UID + "\n")
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return env.read_text(encoding="utf-8"), oct(env.stat().st_mode & 0o777), r.stdout

    def test_passa_a_rootless(self):
        texto, modo, out = self.correr("OFFICE_UID='1001'\nOFFICE_GID='1001'\nOFFICE_AUTH_HASH='$2y$12$x'\n", 1)
        self.assertEqual(texto, "OFFICE_AUTH_HASH='$2y$12$x'\nOFFICE_UID='0'\nOFFICE_GID='0'\n")
        self.assertEqual(modo, "0o600")
        self.assertIn("Docker rootless", out)

    def test_volta_ao_grupo_docker(self):
        texto, _, _ = self.correr("OFFICE_UID='0'\nOFFICE_GID='0'\n", 0)
        self.assertEqual(texto, "OFFICE_UID='1001'\nOFFICE_GID='1001'\n")

    def test_igual_ou_valor_a_mao_fica(self):
        for antes in ("OFFICE_UID='1001'\nOFFICE_GID='1001'\n", "OFFICE_UID='2000'\nOFFICE_GID='2000'\n"):
            with self.subTest(antes=antes):
                texto, _, out = self.correr(antes, 0)
                self.assertEqual(texto, antes)
                self.assertEqual(out, "")
        texto, _, out = self.correr("OFFICE_UID='0'\nOFFICE_GID='0'\n", 1)
        self.assertEqual(out, "")

    def test_env_novo_usa_o_uid_do_modo(self):
        self.assertIn("echo \"OFFICE_UID='$OFFICE_UID_ENV'\"", INSTALL)
        self.assertIn("echo \"OFFICE_GID='$OFFICE_GID_ENV'\"", INSTALL)
        self.assertNotIn("OFFICE_UID='$(id -u)'", INSTALL)


if __name__ == "__main__":
    unittest.main()
