"""Serviço accoes no compose do modo Docker (CA-27), modo VPS intocado (CA-30) e arranque do serviço (CA-28).

Só texto e chamadas simuladas: nada aqui corre Docker. O arranque com root a sério (chown e setuid reais) só corre
como root (CI: `sudo python3 -m unittest tests.test_compose_accoes`); localmente salta.
"""
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_compose_docker import docker, servico, volumes

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("arranque", ROOT / "servico_accoes" / "arranque.py")
arranque = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arranque)

# docker-compose.example.yml (modo VPS) de 0.4.0: não muda um byte com o serviço accoes (CA-30)
VPS_SHA256 = "2e25d1729870b36df9c4953a05af31a132a1f41462afc7c195281d37d061eb80"
ACCOES_VOLUMES = {"/segredos-accoes": ("accoes-segredos", False), "/dados/accoes": ("accoes-estado", False),
                  "/dados/accoes-entrada": ("accoes-entrada", False), "/dados/decisoes": ("decisoes", True),
                  "/dados/agentes": ("agentes", True)}


class ComposeAccoes(unittest.TestCase):
    out = docker()

    def servicos(self):
        corpo = self.out[self.out.index("\nservices:\n"):self.out.index("\nconfigs:\n")]
        return re.findall(r"(?m)^  ([a-z]+):\n", corpo)

    def test_servico_accoes(self):  # CA-27
        a = servico(self.out, "accoes")
        imagem = next(x for x in servico(self.out, "runner").splitlines() if x.startswith("    image: "))
        self.assertNotIn("init:", a)   # o tini ficaria como root (user "0:0") enquanto o serviço corre
        for linha in (imagem, "    restart: unless-stopped", "    read_only: true",
                      '    entrypoint: ["accoes"]', '    user: "0:0"', "      - no-new-privileges:true",
                      "      ACCOES_PERFIL: lite", '      ACCOES_UID: "${ACCOES_UID:-10002}"',
                      '      ACCOES_GID: "${ACCOES_GID:-10002}"', '      OFFICE_UID: "${OFFICE_UID:-10001}"',
                      '      OFFICE_GID: "${OFFICE_GID:-10001}"', "    networks:\n      - accoes"):
            self.assertIn(linha + "\n", a)
        self.assertEqual(volumes(a), ACCOES_VOLUMES)
        for var, destino in (("ACCOES_DIR", "/dados/accoes"), ("ACCOES_SEGREDOS", "/segredos-accoes"),
                             ("ACCOES_ENTRADA", "/dados/accoes-entrada"), ("ACCOES_DECISOES", "/dados/decisoes"),
                             ("ACCOES_AGENTES", "/dados/agentes")):
            self.assertIn(f"      {var}: {destino}\n", a)
            self.assertIn(destino, volumes(a))
        for proibido in ("ports:", "profiles:", "/proxy", "/segredos\n", "/config", "/projectos", "runner-home"):
            self.assertNotIn(proibido, a)

    def test_so_o_accoes_ve_os_seus_volumes(self):  # CA-27: accoes-segredos e accoes-estado só nele
        quem = {}
        for s in self.servicos():
            for v, ro in volumes(servico(self.out, s)).values():
                if v.startswith("accoes-"):
                    quem.setdefault(v, set()).add((s, ro))
        self.assertEqual(quem, {"accoes-segredos": {("accoes", False)}, "accoes-estado": {("accoes", False)},
                                "accoes-entrada": {("accoes", False), ("runner", True)}})
        self.assertNotIn("accoes", servico(self.out, "configurar"))   # D9: o accoes faz o próprio chown
        for v in ("accoes-segredos", "accoes-estado", "accoes-entrada"):
            self.assertIn(f"\n  {v}:\n", self.out[self.out.index("\nvolumes:\n"):] + "\n")

    def test_configurar_sem_mudanca(self):  # D9: o configurar não monta nem prepara nada do accoes
        self.assertNotIn("accoes", (ROOT / "runner" / "configurar.py").read_text(encoding="utf-8").lower())

    def test_vps_igual_a_0_4_0(self):  # CA-30 (o test_compose.py garante que é a saída do gerador)
        raw = (ROOT / "docker-compose.example.yml").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), VPS_SHA256)
        self.assertNotIn(b"accoes", raw)


class _Simulado:
    """os.setgroups/setgid/setuid/fchown/geteuid/getuid simulados: o processo «é root» até ao setuid."""

    def __init__(self, recusa_voltar=True):
        self.chamadas, self.uid, self.recusa_voltar = [], 0, recusa_voltar

    def setuid(self, uid):
        if self.uid != 0 and uid == 0 and self.recusa_voltar:
            raise PermissionError(1, "Operation not permitted")
        self.chamadas.append(("setuid", uid))
        self.uid = uid

    def patches(self):
        return [mock.patch.object(arranque.os, "geteuid", lambda: self.uid),
                mock.patch.object(arranque.os, "getuid", lambda: self.uid),
                mock.patch.object(arranque.os, "setgroups", lambda g: self.chamadas.append(("setgroups", g))),
                mock.patch.object(arranque.os, "setgid", lambda g: self.chamadas.append(("setgid", g))),
                mock.patch.object(arranque.os, "setuid", self.setuid),
                mock.patch.object(arranque.os, "fchown",
                                  lambda fd, u, g: self.chamadas.append(("fchown", os.readlink(f"/proc/self/fd/{fd}"),
                                                                         u, g)))]


class Arranque(unittest.TestCase):  # CA-28
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.t = Path(t.name).resolve()
        self.raizes = [self.t / "segredos", self.t / "estado", self.t / "entrada"]
        for p in self.raizes:
            p.mkdir(mode=0o755)
        self.env = {"ACCOES_UID": "10002", "ACCOES_GID": "10002", "OFFICE_UID": "10001", "OFFICE_GID": "10001",
                    "ACCOES_ENTRADA": str(self.t / "entrada")}

    def correr(self, env, sim=None, raizes=None):
        sim = sim or _Simulado()
        with mock.patch.dict(os.environ, env):
            ps = sim.patches()
            for p in ps:
                p.start()
            try:
                arranque.preparar(self.raizes if raizes is None else raizes)
            finally:
                for p in reversed(ps):
                    p.stop()
        return sim.chamadas

    def test_chown_largar_e_modos(self):
        chamadas = self.correr(self.env)
        self.assertEqual(chamadas, [("fchown", str(p), 10002, 10002) for p in self.raizes]
                         + [("setgroups", [10001]), ("setgid", 10002), ("setuid", 10002)])
        self.assertEqual([oct(p.stat().st_mode & 0o777) for p in self.raizes], ["0o700", "0o700", "0o755"])

    def test_omissoes(self):
        chamadas = self.correr({k: "" for k in ("ACCOES_UID", "ACCOES_GID", "OFFICE_UID", "OFFICE_GID")})
        self.assertEqual(chamadas[-3:], [("setgroups", [10001]), ("setgid", 10002), ("setuid", 10002)])

    def test_uid_igual_ao_do_office_recusa(self):
        for env in ({"ACCOES_UID": "10001"}, {"ACCOES_UID": "1000", "OFFICE_UID": "1000"},
                    {"ACCOES_UID": "", "OFFICE_UID": "10002"}):
            with self.subTest(env), self.assertRaises(SystemExit) as e:
                self.correr(self.env | env, sim := _Simulado())
            self.assertIn("OFFICE_UID", str(e.exception.code))
            self.assertEqual(sim.chamadas, [])

    def test_uid_invalido_ou_root_recusa(self):
        for var, v in (("ACCOES_UID", "0"), ("ACCOES_GID", "0"), ("ACCOES_UID", "-1"), ("ACCOES_UID", "x"),
                       ("ACCOES_GID", "1e3"), ("OFFICE_UID", "0")):
            with self.subTest((var, v)), self.assertRaises(SystemExit):
                self.correr(self.env | {var: v}, sim := _Simulado())
            self.assertEqual(sim.chamadas, [])

    def test_nao_segue_symlinks(self):
        fora = self.t / "fora"
        fora.mkdir()
        (self.t / "ligacao").symlink_to(fora)
        with self.assertRaises(SystemExit) as e:
            self.correr(self.env, sim := _Simulado(), [self.raizes[0], self.t / "ligacao"])
        self.assertIn("symlink", str(e.exception.code))
        self.assertFalse([c for c in sim.chamadas if c[0] != "fchown" or c[1] != str(self.raizes[0])])
        # symlink num componente do meio: o lstat vê uma pasta, a abertura com O_NOFOLLOW recusa
        (self.t / "pai").symlink_to(self.t)
        with self.assertRaises(SystemExit):
            self.correr(self.env, sim := _Simulado(), [self.t / "pai" / "estado"])
        self.assertEqual(sim.chamadas, [])

    def test_falta_ou_ficheiro_recusa(self):
        (self.t / "ficheiro").write_text("x", encoding="utf-8")
        for p in (self.t / "nada", self.t / "ficheiro"):
            with self.subTest(p.name), self.assertRaises(SystemExit):
                self.correr(self.env, sim := _Simulado(), [p])
            self.assertEqual(sim.chamadas, [])

    def test_raiz_ja_nossa_nem_se_abre(self):  # sem DAC_OVERRIDE o root não abriria uma raiz 0700 de outro uid
        uid, gid = os.getuid(), os.getgid()
        if uid == 0:   # como root (CI): as raízes passam a ser de 10002 a sério
            uid = gid = 10002
            for p in self.raizes:
                os.chown(p, uid, gid)
        env = self.env | {"ACCOES_UID": str(uid), "ACCOES_GID": str(gid), "OFFICE_UID": str(uid + 1)}
        chamadas = self.correr(env)
        self.assertFalse([c for c in chamadas if c[0] == "fchown"])

    def test_mesmo_grupo_sem_grupo_suplementar(self):
        chamadas = self.correr(self.env | {"OFFICE_GID": "10002"})
        self.assertIn(("setgroups", []), chamadas)

    def test_voltar_a_root_recusa(self):
        with self.assertRaises(SystemExit) as e:
            self.correr(self.env, _Simulado(recusa_voltar=False))
        self.assertIn("root", str(e.exception.code))

    def test_sem_root_nao_faz_nada(self):  # modo VPS: o utilizador office-accoes já é o certo
        sim = _Simulado()
        sim.uid = 1000
        antes = [p.stat().st_mode for p in self.raizes]
        self.assertEqual(self.correr(self.env | {"ACCOES_UID": "1000", "OFFICE_UID": "1000"}, sim), [])
        self.assertEqual([p.stat().st_mode for p in self.raizes], antes)


@unittest.skipUnless(os.geteuid() == 0, "só como root (CI)")
class ArranqueRoot(unittest.TestCase):  # CA-28 a sério: chown, setuid e modos reais, num processo filho
    def test_root_larga_para_accoes_uid(self):
        with tempfile.TemporaryDirectory() as t:
            os.chmod(t, 0o755)
            raizes = [Path(t, n) for n in ("segredos", "estado", "entrada")]
            for p in raizes:
                p.mkdir(mode=0o755)
            env = dict(os.environ, ACCOES_UID="10002", ACCOES_GID="10002", OFFICE_UID="10001", OFFICE_GID="10001",
                       ACCOES_ENTRADA=str(raizes[2]))
            fich = str(ROOT / "servico_accoes" / "arranque.py")
            prog = (f"import importlib.util, os, sys\n"
                    f"s = importlib.util.spec_from_file_location('a', {fich!r})\n"
                    "a = importlib.util.module_from_spec(s); s.loader.exec_module(a)\n"
                    f"a.preparar({[str(p) for p in raizes]!r})\n"
                    "print(os.getuid(), os.geteuid(), os.getgid(), sorted(os.getgroups()))\n")
            r = subprocess.run([sys.executable, "-I", "-c", prog], env=env, capture_output=True, text=True,
                               timeout=30, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), ["10002", "10002", "10002", "[10001]"])
            self.assertEqual([(p.stat().st_uid, p.stat().st_gid, p.stat().st_mode & 0o777) for p in raizes],
                             [(10002, 10002, 0o700), (10002, 10002, 0o700), (10002, 10002, 0o755)])
            r = subprocess.run([sys.executable, "-I", "-c", prog], env=env | {"ACCOES_UID": "10001"},
                               capture_output=True, text=True, timeout=30, check=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("OFFICE_UID", r.stderr)


if __name__ == "__main__":
    unittest.main()
