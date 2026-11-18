"""instalar-accoes.sh (modo VPS do serviço accoes, ADR-021, CA-30 e CA-18a).

Localmente só o --verificar (sem root, com falsos no PATH que registam o argv) e o texto do script; a instalação a
sério e o CA-18a correm no fumo da CI (docs/propostas-ci/ci-accoes.patch).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "instalar-accoes.sh"
TEXTO = SCRIPT.read_text(encoding="utf-8")
BASH = shutil.which("bash")
REAIS = ("dirname", "basename", "sed", "cat", "id", "mktemp")
FALSOS = ("useradd", "setfacl", "systemctl", "install", "chown", "chmod", "mv")
SEGREDOS = Path("/srv/office-accoes/segredos/accoes.env")


def arvore(raiz: Path) -> dict:
    """{caminho: (modo, conteúdo)} de tudo debaixo de raiz (sem seguir symlinks)."""
    out = {}
    for p in sorted(raiz.rglob("*")):
        st = p.lstat()
        out[str(p)] = (st.st_mode, st.st_uid, p.read_bytes() if p.is_file() and not p.is_symlink() else None)
    return out


class InstalarAccoes(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.t = Path(t.name)
        # cópia do script num «repositório» com o código do serviço (só os nomes importam)
        self.repo = self.t / "repo"
        (self.repo / "servico_accoes").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.repo / SCRIPT.name)
        for f in ("accoes.py", "servico_accoes/servico.py", "servico_accoes/arranque.py"):
            (self.repo / f).write_text("# vazio\n", encoding="utf-8")
        self.dados = self.t / "casa" / "dados"
        for p in ("decisoes", "agentes"):
            (self.dados / p).mkdir(parents=True)
        (self.dados / "decisoes" / "pedido.md").write_text("x\n", encoding="utf-8")
        self.log = self.t / "argv.log"
        self.bin = self.t / "bin"
        self.bin.mkdir()
        for c in REAIS:
            os.symlink(shutil.which(c), self.bin / c)
        os.symlink(sys.executable, self.bin / "python3")
        for c in FALSOS:
            self.falso(c)

    def falso(self, nome):
        f = self.bin / nome
        f.write_text(f'#!{BASH}\nprintf "%s\\n" "{nome} $*" >> "{self.log}"\n', encoding="utf-8")
        f.chmod(0o755)

    def correr(self, *args):
        env = {"PATH": str(self.bin), "LANG": "C.UTF-8", "HOME": str(self.t)}
        return subprocess.run([BASH, str(self.repo / SCRIPT.name), *args], capture_output=True, text=True,
                              env=env, timeout=30, check=False)

    def test_verificar_nao_muda_nada(self):  # CA-30: --verificar mostra o que faria, sem root e sem mudar nada
        antes = arvore(self.t)
        r = self.correr("--dados", str(self.dados), "--verificar")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(arvore(self.t), antes)
        self.assertFalse(self.log.exists())   # nenhum falso foi chamado
        d = str(self.dados)
        for linha in (f"+ install -d -o root -g root -m 0755 /opt/office-accoes /opt/office-accoes/servico_accoes",
                      f"+ install -o root -g root -m 0644 {self.repo}/accoes.py /opt/office-accoes/accoes.py",
                      "+ install -o root -g root -m 0644 "
                      f"{self.repo}/servico_accoes/servico.py /opt/office-accoes/servico_accoes/servico.py",
                      "+ install -d -o office-accoes -g office-accoes -m 0750 /srv/office-accoes",
                      "+ install -d -o office-accoes -g office-accoes -m 0700 /srv/office-accoes/segredos",
                      f"+ install -d -o office-accoes -g office-accoes -m 0755 {d}/accoes-entrada",
                      f"+ install -d -o office-accoes -g office-accoes -m 0755 {d}/accoes-saude",
                      f"+ setfacl -R -P -m u:office-accoes:rX {d}/decisoes",
                      f"+ setfacl -R -P -d -m u:office-accoes:rX {d}/decisoes",
                      f"+ setfacl -R -P -m u:office-accoes:rX {d}/agentes",
                      f"+ setfacl -R -P -d -m u:office-accoes:rX {d}/agentes",
                      f"+ setfacl -m u:office-accoes:--x {d}",
                      f"+ setfacl -m u:office-accoes:--x {self.t}",
                      "+ escrever /etc/systemd/system/office-accoes.service (0644, root)",
                      "+ escrever /etc/systemd/system/office-accoes.timer (0644, root)",
                      "+ systemctl daemon-reload", "+ systemctl enable --now office-accoes.timer"):
            self.assertIn(linha + "\n", r.stdout)
        # na CI este teste corre depois da instalação real: o accoes.json existente nunca é reescrito
        if Path("/srv/office-accoes/accoes.json").exists():
            self.assertIn("/srv/office-accoes/accoes.json já existe: fica como está", r.stdout + r.stderr)
        else:
            self.assertIn("+ escrever /srv/office-accoes/accoes.json (0644, office-accoes)\n", r.stdout)
        if subprocess.run(["id", "office-accoes"], capture_output=True, check=False).returncode != 0:
            self.assertIn("+ useradd --system --no-create-home --shell /usr/sbin/nologin office-accoes\n", r.stdout)
        self.assertNotIn("+ setfacl -m u:office-accoes:--x /\n", r.stdout)
        # a ordem: utilizador e pastas antes das ACL; o timer só no fim
        self.assertLess(r.stdout.index("/srv/office-accoes/segredos"), r.stdout.index("setfacl"))
        comandos = [x for x in r.stdout.splitlines() if x.startswith("+ ")]
        self.assertEqual(comandos[-1], "+ systemctl enable --now office-accoes.timer")

    def test_sem_setfacl_recusa(self):  # CA-30: a linha apt-get a correr
        (self.bin / "setfacl").unlink()
        r = self.correr("--dados", str(self.dados), "--verificar")
        self.assertEqual(r.returncode, 1)
        self.assertIn("sudo apt-get install -y acl", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_argumentos(self):
        for args, msg in (((), "falta --dados"), (("--dados", "dados"), "caminho absoluto"),
                          (("--dados", str(self.t / "nada")), "não é uma pasta"),
                          (("--dados", str(self.t / "casa" / "a b")), "caminho absoluto"),
                          (("--outra",), "opção desconhecida")):
            with self.subTest(args):
                r = self.correr(*args, "--verificar")
                self.assertEqual(r.returncode, 1)
                self.assertIn(msg, r.stderr)
        shutil.rmtree(self.dados / "agentes")
        r = self.correr("--dados", str(self.dados), "--verificar")
        self.assertIn("falta a pasta", r.stderr)

    def test_symlinks_recusados(self):
        for p in ("accoes-entrada", "accoes-saude"):
            with self.subTest(p):
                (self.dados / p).symlink_to("/etc")
                r = self.correr("--dados", str(self.dados), "--verificar")
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"{self.dados}/{p} é um symlink", r.stderr)
                (self.dados / p).unlink()
        os.symlink(self.dados, self.t / "ligacao")
        r = self.correr("--dados", str(self.t / "ligacao"), "--verificar")
        self.assertEqual(r.returncode, 1)

    @unittest.skipIf(os.geteuid() == 0, "como root o script corre a sério")
    def test_sem_root_recusa(self):
        r = self.correr("--dados", str(self.dados))
        self.assertEqual(r.returncode, 1)
        self.assertIn("corre com sudo", r.stderr)
        self.assertFalse(self.log.exists())

    def test_units(self):  # endurecimento e caminhos da unit (o texto; a unit a sério só na CI)
        for linha in ("set -euo pipefail", "Type=oneshot", "User=$UTILIZADOR", "Group=$UTILIZADOR",
                      "Environment=ACCOES_PERFIL=lite", "Environment=ACCOES_DIR=$SRV",
                      "Environment=ACCOES_SEGREDOS=$SRV/segredos", "Environment=ACCOES_ENTRADA=$DADOS/accoes-entrada",
                      "Environment=ACCOES_DECISOES=$DADOS/decisoes", "Environment=ACCOES_AGENTES=$DADOS/agentes",
                      "ExecStart=$PY -I -u $OPT/servico_accoes/servico.py passagem", "NoNewPrivileges=yes",
                      "Environment=ACCOES_SAUDE=$DADOS/accoes-saude",
                      "ProtectSystem=strict", "ReadWritePaths=$SRV $DADOS/accoes-entrada $DADOS/accoes-saude",
                      "OnCalendar=*-*-* *:*:00", "WantedBy=timers.target", "UTILIZADOR=office-accoes"):
            self.assertIn(f"\n{linha}\n", TEXTO)
        self.assertNotIn("accoes.env", TEXTO.split("\n# 6.")[1].split("\nfaz systemctl")[0])   # nada no ambiente
        # o comando à mão leva o mesmo ambiente da unit (também o ACCOES_SAUDE)
        self.assertIn('\nCMD="$CMD ACCOES_SAUDE=$DADOS/accoes-saude"\n', TEXTO)

    @unittest.skipUnless(shutil.which("shellcheck"), "sem shellcheck (corre na CI)")
    def test_shellcheck(self):
        r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stdout)


@unittest.skipUnless(SEGREDOS.exists() and shutil.which("sudo")
                     and subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False).returncode == 0,
                     "só depois do instalar-accoes.sh a sério, com sudo sem palavra-passe (CI)")
class CredenciaisSoDoAccoes(unittest.TestCase):  # CA-18a
    def test_outros_utilizadores_nao_lem_o_accoes_env(self):
        for quem in ("nobody", os.environ.get("SUDO_USER") or os.environ.get("USER") or "nobody"):
            r = subprocess.run(["sudo", "-n", "-u", quem, "cat", str(SEGREDOS)], capture_output=True, check=False)
            self.assertNotEqual(r.returncode, 0, quem)
        r = subprocess.run(["sudo", "-n", "-u", "office-accoes", "cat", str(SEGREDOS)], capture_output=True,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
