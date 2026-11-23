"""Conversa (ADR-023, revisão do guardião, PLAN2 §2.6) no compose: com `conversa.ativa: true` o office monta só o
`publico/` (ro) e, com `tls: caddy`, o caddy só o `sock/` (ro) e o Caddyfile.conversa; `tls: externo` (a omissão)
sem nada no caddy; recusas (Traefik com `tls: caddy`, sem `--dominio`, `site` igual ao domínio, sem as pastas do instalador); o
verificador tools/verificar_compose_conversa.py (CA-11, verificados/, conversas/, estado e código do guardião,
CONVERSA_TLS_TESTE); o Caddyfile com o import e o Caddyfile.conversa. Só texto: nada aqui corre Docker."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_compose import CONFIG, ROOT, binds, gerar, servico

sys.path.insert(0, str(ROOT / "tools"))
import verificar_compose_conversa as vcc  # noqa: E402

VERIFICADOR = ROOT / "tools" / "verificar_compose_conversa.py"
CASA_DONO = str(Path("/", "home", "dono"))   # casa fictícia do dono (não é um caminho deste host)
DOMINIO = "office.exemplo.com"
SITE = "conversa.exemplo.com"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.pasta = self.t / "office-conversa"
        for d in ("publico", "sock", "verificados", "conversas"):
            (self.pasta / d).mkdir(parents=True)
        self.cfg = self.t / "office.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, bloco: str):
        self.cfg.write_text(CONFIG.format(t=self.t) + bloco, encoding="utf-8")

    def ligar(self, tls: str = "caddy", site: str | None = SITE):
        self.config(f"conversa:\n  ativa: true\n  pasta: {self.pasta}\n  tls: {tls}\n"
                    + (f"  site: {site}\n" if site else ""))

    def ligada(self, tls: str = "caddy", *args) -> str:
        self.ligar(tls, SITE if tls == "caddy" else None)
        r = gerar(self.cfg, *(args or ("--dominio", DOMINIO)))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


class Gerador(Base):
    def test_desligada_sem_mounts_nem_variaveis(self):
        self.config("")
        base = gerar(self.cfg, "--dominio", DOMINIO)
        self.assertEqual(base.returncode, 0, base.stderr)
        self.assertNotIn("conversa", base.stdout.lower())
        for bloco in (f"conversa:\n  ativa: false\n  pasta: {self.pasta}\n  site: {SITE}\n",
                      "conversa:\n  ativa: false\n  tls: externo\n"):
            with self.subTest(bloco=bloco):
                self.config(bloco)
                r = gerar(self.cfg, "--dominio", DOMINIO)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout, base.stdout)   # byte a byte

    def test_ligada_caddy(self):
        out = self.ligada()
        office, caddy = servico(out, "office"), servico(out, "caddy")
        self.assertIn("      CONVERSA_PUBLICO_DIR: /data/conversa-publico\n", office)
        conv = [(s, t, ro) for s, t, ro in binds(office) if str(self.pasta) in s]
        self.assertEqual(conv, [(str(self.pasta / "publico"), "/data/conversa-publico", True)])
        self.assertIn(f"      CONVERSA_SITE: {SITE}\n", caddy)
        b = {t: (s, ro) for s, t, ro in binds(caddy)}
        self.assertEqual(b["/run/office-conversa"], (str(self.pasta / "sock"), True))
        self.assertEqual(b["/etc/caddy/conversa/conversa.caddy"], (str(ROOT / "Caddyfile.conversa"), True))
        self.assertEqual(len([s for s, _, _ in binds(caddy) if str(self.pasta) in s]), 1)
        for proibido in ("verificados", "conversas", "/var/lib/office-conversa", "/opt/office-conversa",
                         "CONVERSA_TLS_TESTE", "conversa-fila", "conversa-prefs"):
            self.assertNotIn(proibido, out)
        self.assertIn('      - "443:443"\n', caddy)
        self.assertEqual(vcc.achados(out, str(self.pasta)), [])

    def test_ligada_externo(self):
        # tls: externo, com e sem --dominio: só o publico/ no office; o caddy fica como com a Conversa desligada
        for args in (("--dominio", DOMINIO), ()):
            with self.subTest(args=args):
                self.config("")
                base = gerar(self.cfg, *args).stdout
                out = self.ligada("externo", *args) if args else self._externo_sem_dominio()
                self.assertEqual(servico(out, "caddy"), servico(base, "caddy"))
                self.assertIn("CONVERSA_PUBLICO_DIR", servico(out, "office"))
                self.assertNotIn("sock", out)
                self.assertEqual(vcc.achados(out, str(self.pasta)), [])

    def test_sem_tls_e_externo(self):   # S2: sem `tls` no bloco, o caddy não monta o socket nem o Caddyfile.conversa
        self.config(f"conversa:\n  ativa: true\n  pasta: {self.pasta}\n  site: {SITE}\n")
        sem = gerar(self.cfg, "--dominio", DOMINIO)
        self.assertEqual(sem.returncode, 0, sem.stderr)
        self.assertEqual(sem.stdout, self.ligada("externo"))
        self.assertNotIn("Caddyfile.conversa", sem.stdout)
        self.assertNotIn("CONVERSA_SITE", sem.stdout)

    def _externo_sem_dominio(self) -> str:
        self.ligar("externo", None)
        r = gerar(self.cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_externo_com_traefik(self):
        self.ligar("externo", None)
        r = gerar(self.cfg, "--proxy", "traefik", "--rede", "web")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("caddy:", r.stdout)
        self.assertIn(str(self.pasta / "publico"), servico(r.stdout, "office"))
        self.assertEqual(vcc.achados(r.stdout, str(self.pasta)), [])

    def test_recusas(self):
        casos = (
            (("--proxy", "traefik", "--rede", "web", "--dominio", DOMINIO), SITE, "com o Traefik só externo"),
            ((), SITE, "--dominio"),
            (("--dominio", SITE), SITE, "outro nome"),
        )
        for args, site, msg in casos:
            with self.subTest(args=args):
                self.ligar("caddy", site)
                r = gerar(self.cfg, *args)
                self.assertEqual((r.returncode, r.stdout), (1, ""))
                self.assertIn(msg, r.stderr)

    def test_recusa_sem_as_pastas_do_instalador(self):
        for tls, falta in (("caddy", "publico"), ("caddy", "sock"), ("externo", "publico")):
            with self.subTest(tls=tls, falta=falta):
                (self.pasta / falta).rmdir()
                self.ligar(tls, SITE)
                r = gerar(self.cfg, "--dominio", DOMINIO)
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"falta {falta}/", r.stderr)
                self.assertIn("instalar-conversa.sh", r.stderr)
                (self.pasta / falta).mkdir()
        (self.pasta / "sock").rmdir()   # externo não precisa do sock/
        self.ligar("externo", None)
        self.assertEqual(gerar(self.cfg).returncode, 0)

    def test_ativa_sem_pasta_ou_site_recusada(self):
        for bloco, msg in (("conversa:\n  ativa: true\n  site: c.exemplo.com\n", "conversa.pasta"),
                           (f"conversa:\n  ativa: true\n  pasta: {self.pasta}\n  tls: caddy\n", "conversa.site")):
            with self.subTest(msg=msg):
                self.config(bloco)
                r = gerar(self.cfg, "--dominio", DOMINIO)
                self.assertEqual(r.returncode, 1)
                self.assertIn(msg, r.stderr)


class Verificador(Base):
    def correr(self, texto: str, *args) -> subprocess.CompletedProcess:
        f = self.t / "compose.yml"
        f.write_text(texto, encoding="utf-8")
        return subprocess.run([sys.executable, str(VERIFICADOR), str(f), *args], capture_output=True, text=True,
                              timeout=30, check=False)

    def test_exemplos_do_repo_passam(self):
        for nome in ("docker-compose.example.yml", "compose.example.yaml"):
            with self.subTest(nome=nome):
                r = self.correr((ROOT / nome).read_text(encoding="utf-8"))
                self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_gerado_passa_e_com_dono_casa(self):
        for tls in ("caddy", "externo"):
            with self.subTest(tls=tls):
                r = self.correr(self.ligada(tls), "--pasta", str(self.pasta), "--dono-casa", CASA_DONO)
                self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_ca11_recusas(self):
        out = self.ligada()
        p = str(self.pasta)
        env_office = "    environment:\n"
        ro = '        target: /data/conversa-publico\n        read_only: true'
        sock_ro = '        target: /run/office-conversa\n        read_only: true'
        casos = {
            "publico rw": (out.replace(ro, ro.replace("true", "false")), "publico/ sem ser só de leitura"),
            "sock rw": (out.replace(sock_ro, sock_ro.replace("true", "false")), "sock/ sem ser só de leitura"),
            "office monta verificados": (_bind(out, "office", f"{p}/verificados", "/data/v"), "fora de publico/"),
            "office monta conversas": (_bind(out, "office", f"{p}/conversas", "/data/conversas"), "fora de publico/"),
            "office monta a raiz": (_bind(out, "office", p, "/data/tudo"), "fora de publico/"),
            "office monta sock": (_bind(out, "office", f"{p}/sock", "/run/office-conversa"), "fora de publico/"),
            "publico noutro destino": (out.replace("target: /data/conversa-publico", "target: /data/p"),
                                       "fora de publico/"),
            "caddy monta publico": (_bind(out, "caddy", f"{p}/publico", "/x"), "fora de sock/"),
            "caddy monta verificados": (_bind(out, "caddy", f"{p}/verificados", "/x"), "fora de sock/"),
            "caddy monta uma pasta-mãe": (_bind(out, "caddy", str(self.t), "/x"), "fora de sock/"),
            "estado do guardião": (_bind(out, "office", "/var/lib/office-conversa", "/data/g"), "guardião"),
            "código do guardião": (_bind(out, "caddy", "/opt/office-conversa", "/x"), "guardião"),
            "pasta-mãe do guardião": (_bind(out, "office", "/var/lib", "/data/g"), "guardião"),
            "ANTHROPIC": (out.replace(env_office, env_office + "      ANTHROPIC_API_KEY: x\n", 1), "ANTHROPIC_API_KEY"),
            "OAUTH": (out.replace(env_office, env_office + "      CLAUDE_CODE_OAUTH_TOKEN: x\n", 1),
                      "CLAUDE_CODE_OAUTH_TOKEN"),
            "STT": (out.replace(env_office, env_office + "      CONVERSA_STT_CHAVE: x\n", 1), "CONVERSA_STT_CHAVE"),
            "TLS_TESTE": (out.replace(env_office, env_office + '      CONVERSA_TLS_TESTE: "1"\n', 1),
                          "CONVERSA_TLS_TESTE"),
            "voz.env": (out.replace(env_office, env_office + f"      X: {CASA_DONO}/.config/x/voz.env\n", 1), "voz.env"),
            "estado da ponte": (_bind(out, "office", f"{CASA_DONO}/.local/state/office-conversa", "/data/e"),
                                "estado da ponte"),
        }
        for nome, (texto, msg) in casos.items():
            with self.subTest(nome=nome):
                self.assertNotEqual(texto, out)
                r = self.correr(texto, "--pasta", p)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(msg, r.stdout)
        r = self.correr(_bind(out, "office", f"{CASA_DONO}/.claude", "/data/c"), "--pasta", p, "--dono-casa", CASA_DONO)
        self.assertEqual(r.returncode, 1)
        self.assertIn("casa do dono", r.stdout)

    def test_outro_servico_nao_monta_nada_da_pasta(self):
        out = self.ligada()
        extra = (f'  outro:\n    image: x\n    volumes:\n      - type: bind\n        source: "{self.pasta}/publico"\n'
                 "        target: /x\n        read_only: true\n")
        texto = out.replace("\nvolumes:\n", "\n" + extra + "volumes:\n", 1)
        self.assertIn("serviço outro monta a pasta da Conversa", vcc.achados(texto, str(self.pasta))[0])

    def test_pasta_por_omissao_e_uso(self):
        texto = _bind(self.ligada(), "caddy", "/srv/office-conversa/verificados", "/x")
        r = self.correr(texto)
        self.assertEqual(r.returncode, 1)
        self.assertIn("serviço caddy monta a pasta da Conversa fora de sock/", r.stdout)
        for args in ((), ("a", "b"), ("x", "--pasta"), ("x", "--pasta", "relativa")):
            with self.subTest(args=args):
                self.assertEqual(vcc.main(list(args)), 2)

    def test_o_que_nao_sabe_ler_nao_passa(self):
        out = self.ligada()
        self.assertTrue(vcc.achados(out + "x-base: &b\n  <<: *b\n"))
        self.assertTrue(vcc.achados(out.replace("  office:\n", '  "office":\n', 1)))


def _bind(out: str, svc: str, src: str, alvo: str) -> str:
    # um bind a mais no início dos volumes do serviço
    novo = f'      - type: bind\n        source: "{src}"\n        target: {alvo}\n        read_only: true\n'
    i = out.index(f"  {svc}:\n")
    j = out.index("    volumes:\n", i) + len("    volumes:\n")
    return out[:j] + novo + out[j:]


class Ficheiros(unittest.TestCase):
    def test_caddyfile_import_na_ultima_linha(self):
        c = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        self.assertEqual(c.splitlines()[-1], "import /etc/caddy/conversa/*.caddy")
        self.assertEqual(c.count("import "), 1)
        self.assertNotIn("X-Forwarded-Proto", c)
        self.assertNotIn("X-Forwarded-Proto", (ROOT / "Caddyfile.docker").read_text(encoding="utf-8"))
        self.assertNotIn("import ", (ROOT / "Caddyfile.docker").read_text(encoding="utf-8"))

    def test_caddyfile_conversa(self):
        c = (ROOT / "Caddyfile.conversa").read_text(encoding="utf-8")
        bloco = c[c.index("{$CONVERSA_SITE} {"):]
        self.assertEqual(bloco, (
            "{$CONVERSA_SITE} {\n"
            '\theader Strict-Transport-Security "max-age=31536000"\n'
            "\tbasic_auth {\n"
            "\t\t{$OFFICE_AUTH_USER} {$OFFICE_AUTH_HASH}\n"
            "\t}\n"
            "\treverse_proxy unix//run/office-conversa/guardiao.sock {\n"
            "\t\theader_up -Authorization\n"
            "\t\theader_up -X-Office-Segredo\n"
            "\t\theader_up X-Forwarded-Proto {scheme}\n"
            "\t}\n"
            "}\n"))
        self.assertTrue(all(ln.startswith("#") for ln in c[:c.index("{$CONVERSA_SITE} {")].splitlines()))

    def test_chat_fora_da_imagem(self):
        d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        copy = next(ln for ln in d.splitlines() if ln.startswith("COPY server.py "))
        self.assertNotIn("chat", copy)
