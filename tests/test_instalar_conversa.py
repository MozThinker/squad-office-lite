"""instalar-conversa.sh (modo VPS da Conversa, ADR-023 com a revisão S2/S5, CA-10, CA-21): o guardião (useradd, pastas
com ACL, /opt com o SHA256SUMS, unit de sistema), a migração da chave antiga e a ponte.

Localmente só o --verificar (sem root, com falsos no PATH que registam o argv) e o texto do script e das units; a
instalação a sério corre no fumo da CI proposta (docs/propostas-ci/). Nunca usa a `~` real: a casa do dono é uma pasta
temporária (getent falso).
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "instalar-conversa.sh"
TEXTO = SCRIPT.read_text(encoding="utf-8")
UNIT = (ROOT / "chat_host" / "office-conversa.service").read_text(encoding="utf-8")
UNIT_GUARDIAO = (ROOT / "chat_host" / "office-conversa-guardiao.service").read_text(encoding="utf-8")
BASH = shutil.which("bash")
REAIS = ("dirname", "basename", "sed", "cat", "cut", "mktemp")
FALSOS = ("setfacl", "systemctl", "loginctl", "install", "chown", "chmod", "mv", "runuser", "useradd", "rm", "find",
          "sha256sum")
FICHEIROS = ("chat.py", "reader.py", "chat_host/guardiao.py", "chat_host/ponte.py", "chat_host/mcp.py",
             "chat_host/stt.py", "chat_host/codigo.py", "chat_host/office-conversa.service",
             "chat_host/office-conversa-guardiao.service")
WEB = ("conversa.html", "conversa.js", "fonts/letra.woff2")


def arvore(raiz: Path) -> dict:
    out = {}
    for p in sorted(raiz.rglob("*")):
        st = p.lstat()
        out[str(p)] = (st.st_mode, p.read_bytes() if p.is_file() and not p.is_symlink() else None)
    return out


@unittest.skipUnless(Path("/usr/bin/python3").exists(), "o instalador usa o /usr/bin/python3 (o da unit)")
class InstalarConversa(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.t = Path(t.name)
        self.repo = self.t / "repo"
        (self.repo / "chat_host").mkdir(parents=True)
        (self.repo / "static" / "fonts").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.repo / SCRIPT.name)
        for f in FICHEIROS:
            shutil.copy2(ROOT / f, self.repo / f)
        for n in WEB:   # a UI (do W) num manifesto próprio: o instalador só lê os nomes
            (self.repo / "static" / n).write_text("x")
        self.manifesto(WEB)
        self.casa = self.t / "casa" / "dono-teste"
        (self.casa / ".local" / "bin").mkdir(parents=True)
        self.claude = self.casa / ".local" / "bin" / "claude"
        self.claude.write_text("#!/bin/sh\nexit 0\n")
        self.claude.chmod(0o755)
        self.pasta = self.t / "srv" / "office-conversa"
        self.log = self.t / "argv.log"
        self.bin = self.t / "bin"
        self.bin.mkdir()
        for c in REAIS:
            os.symlink(shutil.which(c), self.bin / c)
        for c in FALSOS:
            self.falso(c, f'printf "%s\\n" "{c} $*" >> "{self.log}"')
        self.falso("timedatectl", "echo UTC")
        # só existem estes utilizadores (o office-conversa ainda não); `id -u` diz que não somos o root
        self.falso("id", 'case "$1" in -u) echo 1000 ;; -gn|-nG) echo "$2" ;; fabrica|dono-teste|root) exit 0 ;; '
                         '*) exit 1 ;; esac')
        self.falso("getent", f'[ "$1 $2" = "passwd dono-teste" ] && '
                             f'echo "dono-teste:x:1001:1001::{self.casa}:/bin/bash"; '
                             f'[ "$1 $2" = "passwd root" ] && echo "root:x:0:0::/root:/bin/bash"; '
                             f'[ "$1" = group ] && case "$2" in fabrica|caddy|cloudflared) echo "$2:x:1000:"; exit 0 ;; '
                             f'*) exit 2 ;; esac; exit 0')

    def manifesto(self, nomes):
        (self.repo / "static" / "conversa.manifesto").write_text("".join(f"{n}\n" for n in nomes))

    def falso(self, nome, corpo):
        f = self.bin / nome
        f.write_text(f"#!{BASH}\n{corpo}\n", encoding="utf-8")
        f.chmod(0o755)

    def correr(self, *args, office="fabrica", dono="dono-teste", verificar=True, grupo="cloudflared"):
        env = {"PATH": str(self.bin), "LANG": "C.UTF-8", "HOME": str(self.t)}
        base = ["--office", office, "--dono", dono, "--pasta", str(self.pasta)] if office else []
        if office and grupo:   # tls externo (a omissão): o grupo do túnel, sem o Office
            base += ["--grupo-socket", grupo]
        return subprocess.run([BASH, str(self.repo / SCRIPT.name), *base, *args,
                               *(["--verificar"] if verificar else [])],
                              capture_output=True, text=True, env=env, timeout=60, check=False)

    def test_verificar_nao_muda_nada(self):   # useradd, pastas, ACL, /opt, SHA256SUMS, código, units e linger
        antes = arvore(self.t)
        r = self.correr()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(arvore(self.t), antes)
        self.assertFalse(self.log.exists())   # nenhum falso foi chamado
        p, c, o = str(self.pasta), str(self.casa), "/opt/office-conversa"
        est = f"{c}/.local/state/office-conversa"
        units = f"{c}/.config/systemd/user"
        sis = "/etc/systemd/system/office-conversa-guardiao.service"
        linhas = ["+ useradd --system --no-create-home --shell /usr/sbin/nologin office-conversa",
                  f"+ install -d -o root -g root -m 0755 {p}",
                  f"+ install -d -o office-conversa -g office-conversa -m 0750 {p}/verificados {p}/verificados/audio",
                  f"+ setfacl -P -m u:dono-teste:r-x {p}/verificados",
                  f"+ setfacl -P -d -m u:dono-teste:r-- {p}/verificados",
                  f"+ setfacl -P -m u:dono-teste:r-x {p}/verificados/audio",
                  f"+ setfacl -P -d -m u:dono-teste:r-- {p}/verificados/audio",
                  f"+ install -d -o dono-teste -g dono-teste -m 0750 {p}/conversas",
                  f"+ runuser -u dono-teste -- setfacl -R -P -m u:office-conversa:rX,u:fabrica:--- {p}/conversas",
                  f"+ runuser -u dono-teste -- find {p}/conversas -type d -exec setfacl -P -d -m "
                  "u:office-conversa:r-x,u:fabrica:--- {} +",
                  f"+ install -d -o office-conversa -g office-conversa -m 0755 {p}/publico",
                  f"+ install -d -o office-conversa -g cloudflared -m 2750 {p}/sock",
                  f"+ rm -rf {o}/web",
                  f"+ install -d -o root -g root -m 0755 {o} {o}/chat_host {o}/web",
                  f"+ install -o root -g root -m 0644 {self.repo}/chat.py {o}/chat.py",
                  f"+ install -o root -g root -m 0644 {self.repo}/chat_host/guardiao.py {o}/chat_host/guardiao.py",
                  f"+ install -o root -g root -m 0644 {self.repo}/static/conversa.manifesto {o}/conversa.manifesto",
                  f"+ install -o root -g root -m 0644 {self.repo}/static/conversa.html {o}/web/conversa.html",
                  f"+ install -d -o root -g root -m 0755 {o}/web/fonts",
                  f"+ install -o root -g root -m 0644 {self.repo}/static/fonts/letra.woff2 {o}/web/fonts/letra.woff2",
                  f"+ escrever {o}/SHA256SUMS (0644, root)",
                  f"+ runuser -u dono-teste -- mkdir -p {est} {units}/office-conversa.service.d",
                  f"+ runuser -u dono-teste -- chmod 0700 {est}",
                  "+ install -d -o office-conversa -g office-conversa -m 0700 /var/lib/office-conversa",
                  "+ runuser -u office-conversa -- /usr/bin/python3 -I /opt/office-conversa/chat_host/codigo.py "
                  "--estado /var/lib/office-conversa",
                  f"+ install -o root -g root -m 0644 {self.repo}/chat_host/office-conversa-guardiao.service {sis}",
                  f"+ escrever {sis}.d/instalacao.conf (0644, root)",
                  "+ systemctl daemon-reload",
                  "+ systemctl enable office-conversa-guardiao.service",
                  "+ systemctl restart office-conversa-guardiao.service",
                  f"+ escrever {units}/office-conversa.service (0644, dono-teste)",
                  f"+ escrever {units}/office-conversa.service.d/instalacao.conf (0644, dono-teste)",
                  "+ loginctl enable-linger dono-teste",
                  "+ systemctl --user -M dono-teste@ daemon-reload",
                  "+ systemctl --user -M dono-teste@ enable office-conversa.service",
                  "+ systemctl --user -M dono-teste@ restart office-conversa.service"]
        for linha in linhas:
            self.assertIn(linha + "\n", r.stdout)
        comandos = [x for x in r.stdout.splitlines() if x.startswith("+ ")]
        self.assertEqual(comandos[-1], linhas[-1])
        ordem = [comandos.index(x) for x in linhas]
        self.assertEqual(ordem, sorted(ordem))   # pela ordem: pastas antes das ACL, SHA256SUMS depois das cópias, …
        # sem voz: nem venv nem voz.env; nada da fila antiga; nenhuma migração numa instalação nova
        for fora in ("venv", "voz.env", "/fila", "rm -f", "setfacl -x"):
            self.assertNotIn(fora, r.stdout)
        self.assertIn("site: conversa.<o teu domínio>", r.stdout)
        # S2: tls externo por omissão, com os passos do túnel e sem o aviso do caddy
        self.assertIn("tls: externo)", r.stdout)
        self.assertIn(f"unix:{p}/sock/guardiao.sock", r.stdout)
        self.assertNotIn("Caddy da fabrica", r.stdout)
        self.assertNotIn("chaves.json", r.stdout)
        # o aviso de tokens (CA-21), o mesmo texto da UI quando ela existe
        aviso = re.search(r'AVISO_TOKENS="(.*?)"\n', TEXTO, re.S).group(1).replace("\\\n", "")
        self.assertIn(f"Aviso: {aviso}", r.stdout)
        ui = ROOT / "static" / "conversa.js"
        if ui.exists():
            m = re.search(r'AVISO_TOKENS = "([^"]+)"', ui.read_text(encoding="utf-8"))
            if m:
                self.assertEqual(aviso, m.group(1))

    def test_migracao_da_chave_antiga(self):   # S5: a chave sai do estado do dono e há código novo
        est = self.casa / ".local" / "state" / "office-conversa"
        est.mkdir(parents=True)
        (est / "chave").write_text("0" * 64 + "\n")
        (self.pasta / "conversas").mkdir(parents=True)
        (self.pasta / "conversas" / "entrada.json").write_text("{}")
        (self.pasta / "fila").mkdir()
        r = self.correr()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"+ runuser -u dono-teste -- rm -f {est}/chave {est}/pids.json "
                      f"{self.pasta}/conversas/entrada.json\n", r.stdout)
        self.assertIn("codigo.py --estado /var/lib/office-conversa", r.stdout)
        self.assertIn(f"A pasta {self.pasta}/fila é da versão anterior", r.stdout)
        self.assertTrue((est / "chave").exists())   # --verificar: nada apagado
        self.assertLess(r.stdout.index("rm -f"), r.stdout.index("codigo.py"))

    def test_quando_gera_o_codigo(self):   # G2: o código morre no 1.º registo; os browsers registados ficam
        bloco = TEXTO.split("\n# 6.")[1].split("\n# 7.")[0]
        self.assertIn('if [ -e "$GEST/chave" ]; then MIGRAR=1; fi', bloco)
        self.assertIn('if [ "$NOVO_CODIGO" = 1 ] || [ "$REVOGAR" = 1 ] || [ "$MIGRAR" = 1 ] || \\\n'
                      '   { [ ! -f "$GEST/entrada.json" ] && [ ! -f "$GEST/chaves.json" ]; }; then', bloco)

    def test_novo_codigo(self):
        r = self.correr("--novo-codigo")
        self.assertIn("codigo.py --estado /var/lib/office-conversa", r.stdout)
        self.assertNotIn("--conversas", r.stdout)

    def test_drop_in_do_guardiao(self):
        bloco = TEXTO.split("\n# 8.")[1].split("\n# 9.")[0]
        for v in ("CONVERSA_PASTA", "CONVERSA_ESCUTA", "CONVERSA_PERMISSOES", "CONVERSA_VOZ", "CONVERSA_VOZ_IDIOMA",
                  "CONVERSA_FUSO"):
            self.assertIn(f"Environment={v}=", bloco)
        self.assertIn("ReadWritePaths=", bloco)
        for proibida in ("CONVERSA_TLS_TESTE", "CONVERSA_CLAUDE", "ANTHROPIC", "CONVERSA_STT"):
            self.assertNotIn(proibida, bloco)
        r = self.correr("--voz-idioma", "en", "--grupo-socket", "caddy", "--python", "/usr/bin/python3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"-o office-conversa -g caddy -m 2750 {self.pasta}/sock", r.stdout)

    def test_recusa_repo_que_o_office_escreve(self):   # o código para /opt não pode vir da fabrica
        self.falso("id", 'case "$1" in -u) echo 0 ;; -gn|-nG) echo "$2" ;; fabrica|dono-teste|root) exit 0 ;; '
                         '*) exit 1 ;; esac')
        alvo = self.repo / "chat_host" / "guardiao.py"
        for u in ("fabrica", "dono-teste"):   # G1: nem o Office nem o dono (o motor corre como ele)
            with self.subTest(u=u):
                self.falso("runuser", f'[ "$1 $2 $3 $4" = "-u {u} -- find" ] && echo "{alvo}"; exit 1')
                r = self.correr()
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"o {u} consegue escrever em {alvo} (ou é dele)", r.stderr)
                self.assertIn("/usr/local/src/office-lite-<versão>", r.stderr)
                self.assertNotIn("+ ", r.stdout)
        corpo = TEXTO.split('escrevivel=$(')[1].split("\n")[0]
        self.assertIn('find "$REPO" \\( -writable -o -user "$u" \\) -print -quit', corpo)
        self.falso("runuser", "exit 1")
        self.assertEqual(self.correr().returncode, 0)

    def test_recusa_pastas_acima_do_repo_e_da_pasta(self):   # G1 e G5: a subida até /
        self.falso("id", 'case "$1" in -u) echo 0 ;; -gn|-nG) echo "$2" ;; fabrica|dono-teste|root) exit 0 ;; '
                         '*) exit 1 ;; esac')
        self.pasta.mkdir(parents=True)
        casos = ((self.t, "fabrica", "-w", f"acima de {self.repo}"),
                 (self.t.parent, "dono-teste", "-O", f"acima de {self.repo}"),
                 (Path("/"), "dono-teste", "-w", f"acima de {self.repo}"),
                 (self.pasta, "fabrica", "-O", "a --pasta e as pastas acima dela"),
                 (self.pasta.parent, "dono-teste", "-w", "a --pasta e as pastas acima dela"))
        for d, u, teste, msg in casos:
            with self.subTest(d=d, u=u, teste=teste):
                self.falso("runuser", f'[ "$1 $2 $3 $4 $5" = "-u {u} -- test {teste}" ] && [ "$6" = "{d}" ]')
                r = self.correr()
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"o {u} consegue escrever em {d} (ou é dele)", r.stderr)
                self.assertIn(msg, r.stderr)
                self.assertNotIn("+ ", r.stdout)
        # uma pasta que ainda não existe (a --pasta antes da 1.ª instalação) não conta; as de cima sim
        self.pasta = self.t / "srv" / "nova"
        self.falso("runuser", f'[ "$1 $2 $3 $4 $5" = "-u fabrica -- test -w" ] && [ "$6" = "{self.pasta}" ]')
        self.assertEqual(self.correr().returncode, 0)

    def test_grupo_do_socket_e_tls(self):   # G4 e S2
        r = self.correr(grupo=None)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--tls externo precisa de --grupo-socket", r.stderr)
        for args in (("--grupo-socket", "fabrica"), ("--tls", "caddy", "--grupo-socket", "fabrica")):
            with self.subTest(args=args):   # o Office no grupo do socket: recusa, também com caddy
                r = self.correr(*args)
                self.assertEqual(r.returncode, 1)
                self.assertIn("o fabrica (Office) pertence ao grupo fabrica", r.stderr)
                self.assertNotIn("+ ", r.stdout)
        r = self.correr("--tls", "tudo")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--tls tem de ser externo ou caddy", r.stderr)
        # caddy, só por opção: o grupo primário do Office (o do Caddy rootless) e o aviso claro
        r = self.correr("--tls", "caddy", grupo=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"-o office-conversa -g fabrica -m 2750 {self.pasta}/sock", r.stdout)
        self.assertIn("tls: caddy)", r.stdout)
        self.assertIn("Aviso: com --tls caddy o TLS da Conversa termina no Caddy da fabrica; quem a controle pode "
                      "servir JS na origem da Conversa.", r.stdout)
        self.assertNotIn("guardiao.sock com o site", r.stdout)

    def test_revogar(self):   # G2: esquece os browsers registados antes de reiniciar o guardião
        r = self.correr("--revogar")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("+ rm -f /var/lib/office-conversa/chaves.json\n", r.stdout)
        # primeiro esquece, depois o código novo (para registar um de novo), e o guardião reinicia (as sessões caem)
        self.assertLess(r.stdout.index("chaves.json"), r.stdout.index("codigo.py --estado /var/lib/office-conversa"))
        self.assertLess(r.stdout.index("codigo.py"), r.stdout.index("+ systemctl restart office-conversa-guardiao"))
        self.assertIn("Os browsers registados foram esquecidos", r.stdout)
        self.assertFalse(self.log.exists())

    def test_manifesto_e_ficheiros_a_copiar(self):
        for nomes, msg in ((("conversa.js", "conversa.html"), "a 1.ª linha tem de ser conversa.html"),
                           (("conversa.html", "../segredo.js"), "nome inválido: ../segredo.js"),
                           (("conversa.html", "Conversa.JS"), "nome inválido"),
                           (("conversa.html", "nao-ha.js"), "falta static/nao-ha.js"),
                           ((), "a 1.ª linha tem de ser conversa.html")):
            with self.subTest(nomes=nomes):
                self.manifesto(nomes)
                r = self.correr()
                self.assertEqual(r.returncode, 1)
                self.assertIn(msg, r.stderr)
                self.assertNotIn("+ ", r.stdout)
        self.manifesto(WEB)
        (self.repo / "static" / "conversa.js").unlink()
        (self.repo / "static" / "conversa.js").symlink_to(self.repo / "static" / "conversa.html")
        self.assertIn("falta static/conversa.js", self.correr().stderr)
        (self.repo / "chat_host" / "guardiao.py").unlink()
        self.assertIn("falta chat_host/guardiao.py", self.correr().stderr)

    def test_voz_local_e_externa(self):
        c = str(self.casa)
        r = self.correr("--voz", "local")
        if subprocess.run(["/usr/bin/python3", "-c", "import ensurepip"], capture_output=True,
                          check=False).returncode != 0:   # sem python3-venv: recusa com a linha do apt-get
            self.assertEqual(r.returncode, 1)
            self.assertIn("sudo apt-get install -y python3-venv", r.stderr)
            return self.voz_externa(c)
        self.assertEqual(r.returncode, 0, r.stderr)
        venv = f"{c}/.local/share/office-conversa/stt-venv"
        for linha in (f"+ runuser -u dono-teste -- /usr/bin/python3 -m venv {venv}",
                      f"+ runuser -u dono-teste -- {venv}/bin/python -m pip install -q faster-whisper==1.2.1 "
                      "av==18.1.0"):
            self.assertIn(linha + "\n", r.stdout)
        self.assertIn("WhisperModel('small', device='cpu', compute_type='int8', download_root='"
                      f"{c}/.local/share/office-conversa/stt-modelos')", r.stdout)
        self.voz_externa(c)

    def voz_externa(self, c):
        r = self.correr("--voz", "externa")
        self.assertIn(f"+ escrever {c}/.config/office-conversa/voz.env (0600, dono-teste)\n", r.stdout)
        self.assertIn("preenche", r.stdout)
        (self.casa / ".config" / "office-conversa").mkdir(parents=True)
        (self.casa / ".config" / "office-conversa" / "voz.env").write_text("CONVERSA_STT_CHAVE=x\n")
        r = self.correr("--voz", "externa")
        self.assertNotIn("escrever " + f"{c}/.config/office-conversa/voz.env", r.stdout)
        self.assertIn("voz.env já existe: fica como está", r.stdout)

    def test_recusas(self):
        casos = (((), {"office": "fabrica", "dono": "fabrica"}, "outro utilizador"),
                 ((), {"office": "fabrica", "dono": "root"}, "não pode ser o root"),
                 ((), {"office": "fabrica", "dono": "ninguem"}, "não existe"),
                 ((), {"office": None}, "falta --office"),
                 (("--permissoes", "tudo"), {}, "cartao ou espelho"),
                 (("--voz", "nuvem"), {}, "desligada, local ou externa"),
                 (("--voz-idioma", "pt-BR"), {}, "--voz-idioma"),
                 (("--grupo-socket", "nao-ha"), {}, "o grupo nao-ha não existe"),
                 (("--grupo-socket", "a b"), {}, "--grupo-socket inválido"),
                 (("--pasta", "/home" + "/dono-teste/conversa"), {}, "não pode ficar em /home"),
                 (("--lancadores", "a;rm -rf"), {}, "--lancadores"),
                 (("--fuso", "Nada/Disto"), {}, "--fuso desconhecido"),
                 (("--pasta-trabalho", "relativo"), {}, "caminho absoluto"),
                 (("--pasta-trabalho", str(self.t / "nao-ha")), {}, "não é uma pasta"),
                 (("--claude", str(self.t / "nao-ha")), {}, "não encontrei o claude"),
                 (("--outra",), {}, "opção desconhecida"))
        for args, kw, msg in casos:
            with self.subTest(args=args, kw=kw):
                r = self.correr(*args, **kw)
                self.assertEqual(r.returncode, 1)
                self.assertIn(msg, r.stderr)
                self.assertNotIn("+ ", r.stdout)
        r = subprocess.run([BASH, str(self.repo / SCRIPT.name), "--office", "fabrica", "--dono", "dono-teste",
                            "--pasta", "relativa", "--verificar"], capture_output=True, text=True, timeout=30,
                           env={"PATH": str(self.bin), "LANG": "C.UTF-8"}, check=False)
        self.assertIn("caminho absoluto", r.stderr)
        self.claude.unlink()   # sem o claude do dono
        r = self.correr()
        self.assertEqual(r.returncode, 1)
        self.assertIn("não encontrei o claude", r.stderr)

    def test_sem_setfacl_e_symlinks(self):
        (self.bin / "setfacl").unlink()
        r = self.correr()
        self.assertEqual(r.returncode, 1)
        self.assertIn("sudo apt-get install -y acl", r.stderr)
        self.falso("setfacl", "exit 0")
        self.pasta.mkdir(parents=True)
        # S1: nenhuma das pastas em que o root mexe (symlink ou ficheiro), nem a própria --pasta
        for sub in ("verificados", "conversas", "publico", "sock"):
            alvo = self.pasta / sub
            for criar, msg in ((lambda a: a.symlink_to("/etc"), f"{alvo} é um symlink"),
                               (lambda a: a.write_text("x"), f"{alvo} existe e não é uma pasta")):
                with self.subTest(sub=sub, msg=msg):
                    criar(alvo)
                    r = self.correr()
                    self.assertEqual(r.returncode, 1)
                    self.assertIn(msg, r.stderr)
                    self.assertNotIn("+ ", r.stdout)
                    alvo.unlink()
        (self.pasta / "verificados").mkdir()
        (self.pasta / "verificados" / "audio").symlink_to("/root")
        self.assertIn("verificados/audio é um symlink", self.correr().stderr)
        real = self.t / "real"
        real.mkdir()
        self.pasta = self.t / "ligacao"
        self.pasta.symlink_to(real)
        r = self.correr()
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"{self.pasta} é um symlink", r.stderr)

    def test_pasta_de_trabalho_que_o_office_escreve(self):   # S3 (como root: o runuser falso faz o test -w)
        trab = self.casa / "proj" / "app"
        trab.mkdir(parents=True)
        self.falso("id", 'case "$1" in -u) echo 0 ;; -gn|-nG) echo "$2" ;; fabrica|dono-teste|root) exit 0 ;; '
                         '*) exit 1 ;; esac')
        for escrevivel in (trab, self.casa / "proj", self.casa):
            with self.subTest(escrevivel=escrevivel):
                self.falso("runuser", f'[ "$1 $2 $3 $4 $5" = "-u fabrica -- test -w" ] && [ "$6" = "{escrevivel}" ]')
                r = self.correr("--pasta-trabalho", str(trab))
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"o fabrica (Office) consegue escrever em {escrevivel}", r.stderr)
                self.assertNotIn("+ ", r.stdout)
        self.falso("runuser", "exit 1")   # o Office não escreve em nenhuma: segue
        r = self.correr("--pasta-trabalho", str(trab))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("sem root não confirmo", r.stdout)

    def test_escreve_como_o_dono_sem_chown(self):   # S6
        corpo = TEXTO.split("\nescreve() {")[1].split("\n}\n")[0]
        self.assertIn('runuser -u "$DONO" -- sh -c', corpo)
        self.assertNotIn("chown", TEXTO)

    @unittest.skipIf(os.geteuid() == 0, "como root o script corre a sério")
    def test_sem_root_recusa(self):
        r = self.correr(verificar=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("corre com sudo", r.stderr)
        self.assertFalse(self.log.exists())

    def test_drop_in_sem_credenciais_e_codigo_so_no_ecra(self):   # CA-10, CA-11
        bloco = TEXTO.split("\n# 9.")[1]
        self.assertNotIn("CONVERSA_FILA_DIR", bloco)
        for v in ("CONVERSA_CLAUDE", "CONVERSA_CWD", "CONVERSA_VERIFICADOS_DIR", "CONVERSAS_DIR", "CONVERSA_ESTADO_DIR",
                  "CONVERSA_PERMISSOES", "CONVERSA_LANCADORES", "CONVERSA_FUSO", "CONVERSA_VOZ",
                  "CONVERSA_STT_PYTHON", "CONVERSA_STT_MODELOS"):
            self.assertIn(f"Environment={v}=", bloco)
        for proibida in ("ANTHROPIC", "CLAUDE_CODE_OAUTH_TOKEN", "CONVERSA_STT_CHAVE=%",
                         "Environment=CONVERSA_STT_URL"):
            self.assertNotIn(proibida, bloco)
        # o código só vai para uma variável e para o ecrã: nunca para um ficheiro
        usos = [ln.strip() for ln in TEXTO.splitlines() if re.search(r"(?<![A-Z_])(\$CODIGO|CODIGO=)", ln)]
        self.assertEqual(usos, ['CODIGO=""', 'CODIGO=$(runuser -u "$GUARDIAO" -- "$PY" -I "$OPT/chat_host/codigo.py" '
                                '--estado "$GEST")', 'if [ -n "$CODIGO" ]; then', 'info "    $CODIGO"'])

    def test_unit(self):
        linhas = [ln.strip() for ln in UNIT.splitlines() if ln.strip() and not ln.startswith("#")]
        for ln in ("Restart=always", "RestartSec=5", "KillMode=process", "Type=simple",
                   "EnvironmentFile=-%h/.config/office-conversa/voz.env",
                   "ExecStart=/usr/bin/python3 -I /opt/office-conversa/chat_host/ponte.py", "WantedBy=default.target"):
            self.assertIn(ln, linhas)
        self.assertFalse([ln for ln in linhas if ln.startswith(("MemoryMax", "Environment=ANTHROPIC", "User="))])

    def test_unit_do_guardiao(self):
        linhas = [ln.strip() for ln in UNIT_GUARDIAO.splitlines() if ln.strip() and not ln.startswith("#")]
        for ln in ("User=office-conversa", "Group=office-conversa",
                   "ExecStart=/usr/bin/python3 -I /opt/office-conversa/chat_host/guardiao.py",
                   "StateDirectory=office-conversa", "StateDirectoryMode=0700", "UMask=0077", "Restart=always",
                   "RestartPreventExitStatus=78", "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes",
                   "PrivateDevices=yes", "NoNewPrivileges=yes", "CapabilityBoundingSet=",
                   "RestrictAddressFamilies=AF_UNIX", "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
                   "ProtectControlGroups=yes", "RestrictNamespaces=yes", "LockPersonality=yes",
                   "SystemCallFilter=@system-service", "MemoryMax=256M", "WantedBy=multi-user.target"):
            self.assertIn(ln, linhas)
        self.assertNotIn("CONVERSA_TLS_TESTE", UNIT_GUARDIAO.replace("Nunca CONVERSA_TLS_TESTE", ""))
        self.assertFalse([ln for ln in linhas if ln.startswith(("Environment=", "ReadWritePaths"))])   # no drop-in

    @unittest.skipUnless(shutil.which("shellcheck"), "sem shellcheck (corre na CI)")
    def test_shellcheck(self):
        r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
