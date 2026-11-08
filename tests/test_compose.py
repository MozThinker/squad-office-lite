"""Compose gerado (runner/gerar_compose.py), Dockerfile, Caddyfile e install.sh (só como texto + bash -n).

Nada aqui corre Docker, Caddy, Traefik ou systemd: o install.sh só é exercitado no job de fumo do GitHub Actions.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GERADOR = ROOT / "runner" / "gerar_compose.py"
# HOME fictício do docker-compose.example.yml (não é um caminho deste host)
HOME_EXEMPLO = str(Path("/", "home", "utilizador"))
CASA_EXEMPLO = f"{HOME_EXEMPLO}/office-lite"
REPO_EXEMPLO = f"{HOME_EXEMPLO}/squad-office-lite"
SEGREDO = "${OFFICE_PROXY_SEGREDO:?falta no .env, corre o install.sh}"
RW = {"/data/decisoes-fila", "/data/quadro", "/data/quadro-lancar", "/data/push"}

CONFIG = """versao: 1
raizes:
  dados: {t}/dados
  projectos: {t}/projectos
  claude_projects: {t}/claude
squads:
  - id: dev
    nome: Dev
projectos:
  - id: loja
    nome: Loja
utilizador:
  nome: Ana
  contacto: ana@example.org
"""


def gerar(config, *args, home=None):
    env = dict(os.environ, HOME=home or os.environ.get("HOME", "/tmp"))
    return subprocess.run([sys.executable, str(GERADOR), "--config", str(config), *args],
                          capture_output=True, text=True, env=env, timeout=30)


def binds(texto):
    """[(source, target, read_only)] dos mounts bind (sintaxe longa gerada)."""
    rx = r'- type: bind\n\s+source: "(.*)"\n\s+target: (\S+)\n\s+read_only: (true|false)'
    return [(s, t, ro == "true") for s, t, ro in re.findall(rx, texto)]


def servico(texto, nome):
    m = re.search(rf"^  {nome}:\n((?:    .*\n|      .*\n)*)", texto, re.M)
    return m.group(1) if m else None


def exemplo():
    """Saída do gerador para o office.example.yaml, como se instalado em HOME_EXEMPLO."""
    with tempfile.TemporaryDirectory() as t:
        cfg = Path(t) / "office.yaml"
        shutil.copy(ROOT / "office.example.yaml", cfg)
        r = gerar(cfg, "--repo", REPO_EXEMPLO, home=HOME_EXEMPLO)
        assert r.returncode == 0, r.stderr
        return r.stdout.replace(str(cfg), f"{CASA_EXEMPLO}/office.yaml")


class ComposeGerado(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.cfg = self.t / "office.yaml"
        self.cfg.write_text(CONFIG.format(t=self.t), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.t)

    def caddy(self, *args):
        r = gerar(self.cfg, "--repo", str(ROOT), *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_mounts_so_nas_raizes(self):  # AC-b2
        out = self.caddy()
        b = binds(out)
        office = [x for x in b if x[1] != "/etc/caddy/Caddyfile"]
        self.assertEqual(len(office), 11)
        for s, alvo, _ in office:
            dentro = s == str(self.cfg) or any(s == str(self.t / r) or s.startswith(str(self.t / r) + "/")
                                               for r in ("dados", "claude"))
            self.assertTrue(dentro, s)
        self.assertIn((str(self.cfg), "/config/office.yaml", True), office)
        self.assertEqual({a for _, a, ro in office if not ro}, RW)
        self.assertIn((str(ROOT / "Caddyfile"), "/etc/caddy/Caddyfile", True), b)

    def test_office_endurecido(self):  # AC-b3
        office = servico(self.caddy(), "office")
        self.assertIn(f'build: "{ROOT}"', office)
        self.assertIn("    read_only: true\n", office)
        self.assertIn("    mem_limit: 768m\n", office)
        self.assertIn('    user: "${OFFICE_UID}:${OFFICE_GID}"\n', office)
        self.assertIn("/tmp/miniaturas:size=128m,uid=${OFFICE_UID},gid=${OFFICE_GID},mode=0700", office)
        self.assertIn("OFFICE_CONFIG: /config/office.yaml", office)
        self.assertIn('PUSH_CONTACTO: "mailto:ana@example.org"', office)
        self.assertNotIn("ports:", office)
        self.assertNotIn("image:", office)  # sem registry: imagem construída localmente
        self.assertNotIn("1001", office)

    def test_caddy_unico_com_portas(self):  # AC-d3
        sem = self.caddy()
        self.assertEqual(sem.count("ports:"), 1)
        self.assertIn('- "80:80"', servico(sem, "caddy"))
        self.assertNotIn("443", sem)
        self.assertNotIn("networks:", sem)  # sem rede externa obrigatória (AC-b5)
        com = self.caddy("--dominio", "office.example.org")
        self.assertIn('- "443:443"', servico(com, "caddy"))
        self.assertIn('PUSH_CONTACTO: "https://office.example.org"', com)
        for v in ("OFFICE_SITE", "OFFICE_AUTH_USER", "OFFICE_AUTH_HASH"):
            self.assertIn(f"{v}: ${{{v}}}", servico(com, "caddy"))
        self.assertIn("caddy_data:/data", com)

    def test_segredo_caddy(self):  # segredo do proxy (ADR-006)
        out = self.caddy()
        for nome in ("office", "caddy"):
            self.assertIn(f"      OFFICE_PROXY_SEGREDO: {SEGREDO}\n", servico(out, nome))

    def test_traefik(self):  # AC-d4
        r = gerar(self.cfg, "--proxy", "traefik", "--rede", "web", "--certresolver", "le", "--dominio", "o.example.org")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIsNone(servico(out, "caddy"))
        self.assertNotIn("ports:", out)
        self.assertIn("networks:\n  web:\n    external: true\n", out)
        for lab in ('"traefik.enable=true"', '"traefik.docker.network=web"',
                    '"traefik.http.routers.office-lite.rule=Host(`${OFFICE_DOMINIO}`)"',
                    '"traefik.http.routers.office-lite.entrypoints=websecure"',
                    '"traefik.http.routers.office-lite.tls.certresolver=le"',
                    '"traefik.http.middlewares.office-lite-auth.basicauth.users=${OFFICE_AUTH_USER}:${OFFICE_AUTH_HASH}"',
                    '"traefik.http.services.office-lite.loadbalancer.server.port=8080"'):
            self.assertIn(lab, out)
        # segredo do proxy (ADR-006): no Office e num middleware de cabeçalho encadeado depois do basic auth
        self.assertIn(f"      OFFICE_PROXY_SEGREDO: {SEGREDO}\n", servico(out, "office"))
        # cabeçalhos de resposta (CA-A2.3): middleware próprio, antes do basic auth (o 401 também os leva)
        self.assertIn('"traefik.http.routers.office-lite.middlewares=office-lite-cab,office-lite-auth,'
                      'office-lite-segredo"', out)
        self.assertIn('"traefik.http.middlewares.office-lite-segredo.headers.customrequestheaders.X-Office-Segredo='
                      f'{SEGREDO}"', out)
        self.assertEqual(out.count("middlewares="), 1)
        cab = sorted(re.findall(r'"traefik\.http\.middlewares\.office-lite-cab\.headers\.(\w+)=(.*)"', out))
        # sem CSP: substituiria a do Office (a da Biblioteca leva sandbox)
        self.assertEqual(cab, [("framedeny", "true"), ("referrerpolicy", "same-origin"), ("stsseconds", "31536000")])
        self.assertNotIn("contentsecuritypolicy", out)

    def test_deterministico(self):
        self.assertEqual(self.caddy("--dominio", "a.example.org"), self.caddy("--dominio", "a.example.org"))

    def test_dolar_escapado(self):
        cfg = self.t / "c$x.yaml"
        shutil.copy(self.cfg, cfg)
        r = gerar(cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("c$$x.yaml", r.stdout)

    def test_claude_env_nunca_montado(self):  # D10: credenciais do claude só no host
        cred = self.t / "claude.env"
        cred.write_text("ANTHROPIC_API_KEY=sk-ant-x\n", encoding="utf-8")
        cred.chmod(0o600)
        for args in ((), ("--proxy", "traefik", "--rede", "web", "--dominio", "o.example.org")):
            r = gerar(self.cfg, "--repo", str(ROOT), *args)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("claude.env", r.stdout)
            self.assertNotIn("sk-ant-x", r.stdout)
            for origem, _, _ in binds(r.stdout):
                self.assertFalse(origem == str(cred) or str(cred).startswith(origem.rstrip("/") + "/"), origem)
        # o exemplo instalado: ~/office-lite/claude.env fora de todos os mounts
        cred_ex = f"{CASA_EXEMPLO}/claude.env"
        for origem, _, _ in binds(exemplo()):
            self.assertFalse(cred_ex == origem or cred_ex.startswith(origem.rstrip("/") + "/"), origem)

    def test_raiz_que_montaria_o_claude_env_recusada(self):
        # claude_projects = a pasta da config montaria o claude.env: recusado
        mau = self.t / "mau.yaml"
        mau.write_text(CONFIG.format(t=self.t).replace(f"claude_projects: {self.t}/claude",
                                                       f"claude_projects: {self.t}"), encoding="utf-8")
        r = gerar(mau)
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("claude.env", r.stderr)
        # dados = a pasta da config: só as subpastas (tasks/, quadro/, …) são montadas, o claude.env fica de fora
        ok = self.t / "ok.yaml"
        ok.write_text(CONFIG.format(t=self.t).replace(f"dados: {self.t}/dados", f"dados: {self.t}"), encoding="utf-8")
        self.assertEqual(gerar(ok).returncode, 0)

    def test_erros(self):
        self.assertEqual(gerar(self.cfg, "--proxy", "traefik").returncode, 2)
        self.assertEqual(gerar(self.cfg, "--dominio", "nao valido").returncode, 2)
        mau = self.t / "mau.yaml"
        mau.write_text("versao: 2\n", encoding="utf-8")
        r = gerar(mau)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("erro: "), r.stderr)
        self.assertEqual(r.stdout, "")


class Ficheiros(unittest.TestCase):
    def test_exemplo_igual_ao_gerador(self):  # AC-b5
        self.assertEqual((ROOT / "docker-compose.example.yml").read_text(encoding="utf-8"), exemplo())

    def test_compose_da_fabrica_removido(self):  # AC-b5, AC-d6
        self.assertFalse((ROOT / "docker-compose.yml").exists())
        self.assertFalse((ROOT / "set-password.sh").exists())

    def test_dockerfile(self):  # AC-b3
        d = (ROOT / "Dockerfile").read_text()
        self.assertNotIn("1001", d)
        self.assertNotRegex(d, r"(?m)^USER ")
        self.assertIn("tzdata", d)
        copy = re.search(r"(?m)^COPY (.*\.py.*) \./$", d).group(1).split()
        for f in ("server.py", "reader.py", "aprovacoes.py", "biblioteca.py", "quadro.py", "push.py", "progresso.py",
                  "lancar.py", "officecfg.py", "office.example.yaml"):
            self.assertIn(f, copy)

    def test_caddyfile(self):  # AC-d3
        c = (ROOT / "Caddyfile").read_text()
        self.assertIn("{$OFFICE_SITE} {", c)
        self.assertRegex(c, r"basic_auth \{\s+\{\$OFFICE_AUTH_USER\} \{\$OFFICE_AUTH_HASH\}\s+\}")
        self.assertIn("reverse_proxy office:8080", c)
        # o cabeçalho só é posto depois do basic_auth e substitui o do cliente (header_up = set)
        self.assertRegex(c, r"reverse_proxy office:8080 \{\s+header_up X-Office-Segredo \{\$OFFICE_PROXY_SEGREDO\}\s+\}")

    def test_caddyfile_cabecalhos(self):  # CA-A2.1, CA-A2.2
        c = (ROOT / "Caddyfile").read_text()
        site = c[c.index("{$OFFICE_SITE} {"):]
        # dentro do site e antes do basic_auth (a ordem do Caddy já o garante; assim lê-se como funciona)
        self.assertLess(site.index("header {"), site.index("basic_auth {"))
        bloco = re.search(r"\n\theader \{\n(.*?)\n\t\}", site, re.S).group(1)
        self.assertEqual([x.strip() for x in bloco.splitlines()],
                         ['Content-Security-Policy "frame-ancestors \'none\'"', "X-Frame-Options DENY",
                          "Referrer-Policy same-origin"])
        # HSTS só em HTTPS (com OFFICE_SITE=:80 nunca há HTTPS) e nunca fora do matcher
        self.assertIn("\t@https protocol https\n\theader @https Strict-Transport-Security \"max-age=31536000\"\n", site)
        self.assertEqual(c.count("Strict-Transport-Security"), 1)


class InstaladorEstatico(unittest.TestCase):
    s = (ROOT / "install.sh").read_text()

    def test_bash_n(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / "install.sh")]).returncode, 0)
        self.assertTrue(os.access(ROOT / "install.sh", os.X_OK))

    def test_opcoes(self):  # §3.10
        self.assertIn("set -euo pipefail", self.s)
        for o in ("--sem-perguntas", "--proxy", "--rede", "--certresolver", "--casa", "--palavra-passe",
                  "OFFICE_DOMINIO", "OFFICE_UTILIZADOR", "OFFICE_PASSWORD_FILE"):
            self.assertIn(o, self.s)

    def test_dependencias_antes_de_criar(self):  # AC-d1
        verif = self.s.index("precisa docker")
        for c in ("precisa claude", "precisa gh", "precisa git", "precisa python3", "docker compose version",
                  "sys.version_info < (3, 10)"):
            self.assertLess(self.s.index(c), self.s.index("umask 077"))
        cria = min(self.s.index(x) for x in ("install -d", "mv ", "> \"$"))
        self.assertLess(verif, cria)
        self.assertLess(self.s.index('[ "$falta" = 0 ] || exit 1'), cria)

    def test_segredos(self):  # AC-d2, AC-d6
        self.assertIn("read -rsp", self.s)
        self.assertIn("htpasswd -niBC 12", self.s)  # palavra-passe por stdin (-i), nunca em argv
        self.assertIn("umask 077", self.s)
        self.assertIn('chmod 0600 "$ENVF.tmp"', self.s)
        self.assertNotIn("set -x", self.s)
        self.assertNotRegex(self.s, r"echo[^\n]*\$PASS")
        self.assertRegex(self.s, r"\[ ! -f \"\$YAML\" \]")  # office.yaml só se não existir
        self.assertRegex(self.s, r"\[ ! -f \"\$ENVF\" \]")

    def test_segredo_do_proxy(self):  # ADR-006: gerado só se faltar, ≥ 32 bytes, sem argv/logs, 0600
        bloco = re.search(r"(?ms)^if ! grep -q '\^OFFICE_PROXY_SEGREDO=' \"\$ENVF\"; then\n.*?^fi$", self.s)
        self.assertIsNotNone(bloco)
        b = bloco.group(0)
        self.assertIn("od -An -tx1 -N32 /dev/urandom", b)
        self.assertIn("^[0-9a-f]{64}$", b)
        self.assertIn('chmod 0600 "$ENVF.tmp"', b)
        self.assertNotRegex(b, r"(info|aviso|erro|printf)[^\n]*\$SEGREDO")
        # depois da criação do .env e da troca de palavra-passe (que não lhe toca), antes do compose
        self.assertLess(self.s.index("Palavra-passe actualizada"), bloco.start())
        self.assertLess(bloco.end(), self.s.index("docker compose -f"))
        self.assertIn("grep -vE '^OFFICE_AUTH_(USER|HASH)='", self.s)
        self.assertRegex(self.s, r"unset OFFICE_UID [^\n]*OFFICE_PROXY_SEGREDO")

    def bloco_cred(self):
        i = self.s.index("# credenciais do claude para o runner")
        return self.s[i:self.s.index("# ---------------------------------------------------------------- (3)")]

    def test_credenciais_opcoes(self):  # D10
        for o in ("--credenciais", "OFFICE_CLAUDE_CRED", "OFFICE_CLAUDE_CRED_FILE", "ANTHROPIC_API_KEY",
                  "CLAUDE_CODE_OAUTH_TOKEN", 'CRED="$CASA/claude.env"'):
            self.assertIn(o, self.s)
        self.assertIn("--credenciais", self.s.splitlines()[3])   # linha do uso (-h)
        b = self.bloco_cred()
        # sem OFFICE_CLAUDE_CRED no --sem-perguntas: nenhuma, com aviso (o fumo da CI não muda)
        self.assertRegex(b, r'if \[ -z "\$CRED_VIA" \]; then\n\s+aviso "sem OFFICE_CLAUDE_CRED[^\n]*"\n\s+CRED_VIA=nenhuma')
        self.assertIn("1) chave de API (recomendada; paga por uso)", b)
        self.assertIn('perguntar "Escolhe" 1', b)   # API por omissão
        self.assertIn("RX_CRED_API", b)      # formato da chave de API (o runner usa o mesmo padrão)
        self.assertNotIn("RX_CRED_OAUTH", b)  # o token da subscrição não é lido (C1 da 0.1.2)
        self.assertIn("sk-ant-oat*", b)      # token da subscrição na opção da chave de API: recusado
        self.assertIn('[ -n "$CRED_VALOR" ] || erro', b)
        # idempotente: só pergunta se faltar ou com --credenciais (ou OFFICE_CLAUDE_CRED com --sem-perguntas)
        self.assertIn('CRED_PEDIDO=$CREDENCIAIS\n', b)
        self.assertIn('if [ "$SEM_PERGUNTAS" = 1 ] && [ -n "${OFFICE_CLAUDE_CRED:-}" ]; then CRED_PEDIDO=1; fi', b)
        self.assertIn('if [ ! -e "$CRED" ] || [ "$CRED_PEDIDO" = 1 ]; then', b)

    def test_credenciais_segredo_sem_argv_nem_ecra(self):  # D10
        b = self.bloco_cred()
        self.assertEqual(len(re.findall(r'read -rsp "[^"]*" CRED_VALOR', b)), 1)   # só a chave de API, sem eco
        self.assertIn('IFS= read -r CRED_VALOR < "$OFFICE_CLAUDE_CRED_FILE"', b)
        usos = [x.strip() for x in self.s.splitlines() if "$CRED_VALOR" in x or "${CRED_VALOR" in x]
        permitidas = ('CRED_VALOR=${CRED_VALOR%', '[ -n "$CRED_VALOR" ] || erro', '[[ "$CRED_VALOR" =~',
                      '[[ "$CRED_VALOR" != sk-ant-oat* ]]', 'if [ -n "$CRED_VALOR" ]; then',
                      "( umask 077; printf '%s=%s\\n' \"$CRED_CHAVE\" \"$CRED_VALOR\" > \"$CRED.tmp\" )")
        for u in usos:
            self.assertTrue(u.startswith(permitidas), u)
        self.assertNotRegex(self.s, r"(echo|info|aviso|erro)[^\n]*CRED_VALOR")

    def test_credenciais_escrita_atomica_0600(self):  # D10
        i = self.s.index('if [ -n "$CRED_VALOR" ]; then')
        b = self.s[i:self.s.index("\nfi\n", i)]
        for x in ('rm -f "$CRED.tmp"', "( umask 077; printf", 'unset CRED_VALOR', 'chmod 0600 "$CRED.tmp"',
                  'mv -f "$CRED.tmp" "$CRED"'):
            self.assertIn(x, b)
        self.assertLess(b.index("umask 077"), b.index("mv -f"))
        self.assertLess(self.s.index("umask 077\n"), i)   # depois das dependências e do resto do passo (2)

    def test_credenciais_permissoes_abertas_recusadas(self):  # D10: recusa, nunca corrige em silêncio
        b = self.bloco_cred()
        self.assertIn("stat -c '%u %a' \"$f\"", b)
        self.assertIn("(8#$modo & 8#077) == 0", b)
        self.assertIn('[ "$dono" = "$(id -u)" ] || erro', b)
        self.assertIn('[ -L "$f" ] || [ ! -f "$f" ]', b)
        self.assertIn('segredo_seguro "$CRED"\n', b)
        self.assertIn('  segredo_seguro "$CASA/runner.env"\n', b)   # o runner.env com a credencial, igual
        self.assertRegex(b, r"\|\| erro \"[^\n]*permissões abertas[^\n]*chmod 600")
        self.assertNotRegex(self.s, r'chmod [0-7]+ "\$CRED"')   # só o tmp leva chmod
        self.assertNotIn('chown', b.replace("corrige com chown", ""))
        # a verificação vem antes de qualquer criação (como as dependências)
        self.assertLess(self.s.index('segredo_seguro "$CRED"\n'), self.s.index("umask 077\n"))

    def test_sudo_so_em_mensagens(self):  # AC-d1
        for n, linha in enumerate(self.s.splitlines(), 1):
            codigo = linha.split("#", 1)[0] if not linha.lstrip().startswith("#") else ""
            if re.search(r"\bsudo\b", codigo):
                self.assertRegex(codigo, r"(printf|aviso|erro|precisa) ", f"linha {n}: {linha}")

    def test_aviso_sem_https(self):  # AC-d3
        self.assertIn("sem HTTPS: Web Push e palavra-passe ficam expostos, usa um domínio", self.s)


if __name__ == "__main__":
    unittest.main()
