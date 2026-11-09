"""Compose do modo Docker (runner/gerar_compose.py --modo docker), compose.example.yaml e Caddyfile.docker.

Só texto: nada aqui corre Docker nem Caddy (o `docker compose config -q` e o arranque real ficam no fumo da CI).
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GERADOR = ROOT / "runner" / "gerar_compose.py"
sys.path.insert(0, str(ROOT))
import officecfg  # noqa: E402

CASA = str(Path("/", "home", "office"))   # HOME do utilizador da imagem runner
DIGEST = "sha256:" + "ab" * 32
UID_GID = '"${OFFICE_UID:-10001}:${OFFICE_GID:-10001}"'
SEGREDOS_FICH = (officecfg.CLAUDE_ENV, "runner.env")


def gerar(*args, cwd=None, home=None):
    env = dict(os.environ, HOME=home or os.environ.get("HOME", "/tmp"))
    return subprocess.run([sys.executable, str(GERADOR), "--modo", "docker", *args], capture_output=True, text=True,
                          env=env, cwd=cwd, timeout=30)


def docker(*args):
    r = gerar(*args)
    assert r.returncode == 0, r.stderr
    return r.stdout


def servico(texto, nome):
    m = re.search(rf"^  {nome}:\n((?:    .*\n)*)", texto, re.M)
    return m.group(1) if m else None


def itens_volumes(bloco):
    """[(volume, destino, só leitura, nocopy)] dos volumes nomeados de um serviço (sintaxe curta e longa)."""
    m = re.search(r"(?m)^    volumes:\n((?:      .*\n)+)", bloco)
    out = []
    for item in re.split(r"(?m)^      - ", m.group(1))[1:] if m else []:
        curta = re.fullmatch(r"([a-z][a-z-]*):(/\S+?)(:ro)?\n", item)
        if curta:
            out.append((curta[1], curta[2], curta[3] == ":ro", False))
        elif item.startswith("type: volume\n"):
            campos = dict(re.findall(r"(?m)^ +(source|target|read_only|nocopy): (\S+)$", item))
            out.append((campos["source"], campos["target"], campos.get("read_only") == "true",
                        campos.get("nocopy") == "true"))
    return out


def volumes(bloco):
    """{destino: (volume, só leitura)} dos volumes nomeados de um serviço."""
    return {d: (v, ro) for v, d, ro, _ in itens_volumes(bloco)}


def binds(bloco):
    return re.findall(r'- type: bind\n\s+source: "(.*)"\n\s+target: (\S+)', bloco)


def topo(texto, chave):
    m = re.search(rf"(?m)^{chave}:\n((?:  .*\n|\n)*)", texto)
    return m.group(1)


class ModoDocker(unittest.TestCase):
    out = docker()

    def test_servicos(self):  # AC-c1
        self.assertIn("\nname: office-lite-pc\nservices:\n", self.out)
        servicos = self.out[self.out.index("services:"):self.out.index("\nconfigs:")]
        nomes = re.findall(r"(?m)^  ([a-z]+):\n    ", servicos)
        self.assertEqual(nomes, ["office", "caddy", "runner", "configurar"])
        conf = servico(self.out, "configurar")
        self.assertIn("    profiles:\n      - configurar\n", conf)
        for s in ("office", "caddy", "runner"):
            self.assertNotIn("profiles:", servico(self.out, s))

    def test_imagens_por_omissao(self):  # AC-c5: image com a tag da versão, nunca build
        v = officecfg.VERSAO
        self.assertIn(f"    image: ghcr.io/mozthinker/squad-office-lite:{v}\n", servico(self.out, "office"))
        for s in ("runner", "configurar"):
            self.assertIn(f"    image: ghcr.io/mozthinker/squad-office-lite-runner:{v}\n", servico(self.out, s))
        self.assertNotIn("build:", self.out)
        self.assertIn("image: caddy:2.11.9-alpine@sha256:", servico(self.out, "caddy"))

    def test_imagens_por_digest(self):
        o, r = f"ghcr.io/dono/office:1.2.3@{DIGEST}", f"registo.example.org:5000/a/runner@{DIGEST}"
        out = docker("--imagem-office", o, "--imagem-runner", r)
        self.assertIn(f"    image: {o}\n", servico(out, "office"))
        self.assertIn(f"    image: {r}\n", servico(out, "runner"))
        self.assertIn(f"    image: {r}\n", servico(out, "configurar"))

    def test_imagens_invalidas(self):
        for ref in ("ghcr.io/dono/office", "ghcr.io/dono/office:latest", "ghcr.io/dono/office:stable",
                    "ghcr.io/Dono/office:1", "ghcr.io/dono/office:1\n  x: y", "ghcr.io/dono/office:${X}",
                    "ghcr.io/dono/office:1@sha256:abc", "office:1", "", "-ghcr.io/dono/o:1", "ghcr.io/dono/o:1 #"):
            for opcao in ("--imagem-office", "--imagem-runner"):
                r = gerar(opcao, ref)
                self.assertEqual((r.returncode, r.stdout), (2, ""), (opcao, ref))
                self.assertIn(opcao, r.stderr)

    def test_opcoes_do_vps_recusadas(self):  # sem --config: o compose do PC não depende do office.yaml
        for args in (("--config", "office.yaml"), ("--proxy", "caddy"), ("--rede", "web"),
                     ("--dominio", "a.example.org"), ("--certresolver", "le"), ("--repo", ".")):
            r = gerar(*args)
            self.assertEqual((r.returncode, r.stdout), (2, ""), args)

    def test_deterministico_e_independente_do_host(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(gerar(cwd=t, home=t).stdout, self.out)
        self.assertNotIn(str(ROOT), self.out)
        self.assertNotRegex(self.out, r"(?m)^\s+source: \"?/")   # nenhum caminho absoluto do host

    def test_volumes_do_office(self):  # AC-c3: mesmo modo ro/rw que no VPS
        office = volumes(servico(self.out, "office"))
        esperado = {destino: ("claude-projects" if o == "claude_projects" else officecfg.SUB[o], modo == "ro")
                    for o, destino, modo, _ in officecfg.MOUNTS}
        esperado |= {"/config": ("config", True), "/proxy": ("proxy", True)}
        self.assertEqual(office, esperado)
        env = servico(self.out, "office")
        for _, destino, _, var in officecfg.MOUNTS:
            self.assertIn(f"      {var}: {destino}\n", env)
        for linha in ("OFFICE_MODO: docker", "OFFICE_CONFIG: /config/office.yaml",
                      "OFFICE_PROXY_SEGREDO_FILE: /proxy/segredo"):
            self.assertIn(f"      {linha}\n", env)

    def test_volumes_do_runner(self):  # AC-c3: tudo rw salvo config e segredos (D5)
        runner = volumes(servico(self.out, "runner"))
        esperado = {f"/dados/{officecfg.SUB[o]}": (officecfg.SUB[o], False)
                    for o, _, _, _ in officecfg.MOUNTS if o != "claude_projects"}
        esperado |= {f"{CASA}/.claude/projects": ("claude-projects", False), "/dados/runner": ("runner", False),
                     CASA: ("runner-home", False), "/config": ("config", True), "/segredos": ("segredos", True)}
        self.assertEqual(runner, esperado)
        conf = volumes(servico(self.out, "configurar"))
        self.assertEqual(conf, {d: (v, False) for d, (v, _) in esperado.items()} | {"/proxy": ("proxy", False)})

    def test_volumes_declarados(self):
        usados = set()
        for s in ("office", "caddy", "runner", "configurar"):
            usados |= {v for v, _ in volumes(servico(self.out, s)).values()}
        self.assertEqual(set(re.findall(r"(?m)^  ([a-z-]+):$", topo(self.out, "volumes"))), usados)

    def test_projectos(self):  # AC-c4
        for s in ("runner", "configurar"):
            b = servico(self.out, s)
            self.assertEqual(binds(b), [("${OFFICE_PROJECTOS:-./projectos}", "/projectos")])
            self.assertIn("        bind:\n          create_host_path: true\n", b)
        for s in ("office", "caddy"):
            self.assertNotIn("/projectos", servico(self.out, s))
        self.assertEqual(self.out.count("type: bind"), 2)

    def test_so_o_caddy_publica_portas(self):  # AC-c5
        self.assertEqual(self.out.count("ports:"), 1)
        self.assertIn('    ports:\n      - "${OFFICE_ENDERECO:-127.0.0.1}:${OFFICE_PORTA:-8080}:80"\n',
                      servico(self.out, "caddy"))
        self.assertNotIn("expose:", self.out)

    def test_redes(self):  # AC-c6
        redes = {s: re.findall(r"(?m)^    networks:\n((?:      - .*\n)+)", servico(self.out, s)) for s in
                 ("office", "caddy", "runner", "configurar")}
        self.assertEqual(redes, {"office": ["      - office\n"], "caddy": ["      - office\n"],
                                 "runner": ["      - runner\n"], "configurar": ["      - runner\n"]})
        self.assertEqual(topo(self.out, "networks"), "  office:\n  runner:\n")

    def test_endurecimento(self):  # AC-c7
        office, runner = servico(self.out, "office"), servico(self.out, "runner")
        for b in (office, runner):
            self.assertIn("    read_only: true\n", b)
            self.assertIn(f"    user: {UID_GID}\n", b)
            self.assertRegex(b, r"    tmpfs:\n      - /tmp\S*\n")
        self.assertIn("    init: true\n", runner)
        self.assertIn("      - /tmp:exec,mode=1777\n", runner)
        self.assertIn("/tmp/miniaturas:size=128m,uid=${OFFICE_UID:-10001},gid=${OFFICE_GID:-10001},mode=0700", office)
        for s in ("office", "caddy", "runner"):
            self.assertIn("    restart: unless-stopped\n", servico(self.out, s))

    def test_configurar(self):  # D2: root só para o chown; opções de `run --rm configurar --gh` chegam ao programa
        c = servico(self.out, "configurar")
        for linha in ('entrypoint: ["configurar"]', 'user: "0:0"', "stdin_open: true", "tty: true", "read_only: true"):
            self.assertIn(f"    {linha}\n", c)
        self.assertIn('      OFFICE_UID: "${OFFICE_UID:-10001}"\n      OFFICE_GID: "${OFFICE_GID:-10001}"\n', c)
        self.assertNotIn("restart:", c)
        self.assertNotIn("command:", c)
        self.assertNotIn("entrypoint:", self.out.replace(c, ""))   # a imagem não tem ENTRYPOINT (exec runner …)

    def test_nome_do_projecto(self):  # o modo VPS (office-lite) e o PC (office-lite-pc) coexistem no mesmo Linux
        nome = re.findall(r"(?m)^name: (\S+)$", self.out)
        vps = re.findall(r"(?m)^name: (\S+)$", (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8"))
        self.assertEqual((nome, vps), (["office-lite-pc"], ["office-lite"]))
        self.assertNotEqual(nome, vps)

    def test_nocopy(self):  # o copy-up desfazia o chown do configurar a cada arranque (OFFICE_UID ≠ 10001)
        vistos = set()
        for s in ("office", "caddy", "runner", "configurar"):
            for v, d, _, nocopy in itens_volumes(servico(self.out, s)):
                vistos.add(v)
                # os do Caddy precisam do conteúdo da imagem; todos os outros sem cópia
                self.assertEqual(nocopy, v not in ("caddy-data", "caddy-config"), (s, v, d))
        self.assertEqual(vistos, set(re.findall(r"(?m)^  ([a-z-]+):$", topo(self.out, "volumes"))))
        self.assertIn("      - caddy-data:/data\n      - caddy-config:/config\n", servico(self.out, "caddy"))
        self.assertNotIn("nocopy: false", self.out)

    def test_capacidades(self):  # AC-s1: tudo sem capacidades salvo o mínimo justificado do configurar e do Caddy
        esperado = {"office": [], "runner": [], "caddy": ["NET_BIND_SERVICE", "DAC_OVERRIDE"],
                    "configurar": ["CHOWN", "DAC_OVERRIDE", "SETGID", "SETUID"]}
        for s, caps in esperado.items():
            b = servico(self.out, s)
            self.assertIn("    security_opt:\n      - no-new-privileges:true\n    cap_drop:\n      - ALL\n", b)
            m = re.search(r"(?m)^    cap_add:\n((?:      .*\n)+)", b)
            self.assertEqual(re.findall(r"(?m)^      - (\S+)$", m.group(1)) if m else [], caps, s)
            if m:   # cada capacidade com o porquê num comentário
                self.assertRegex(m.group(1), r"^(      # \S.*\n      - [A-Z_]+\n)+$")
        self.assertEqual(self.out.count("security_opt:"), 4)
        self.assertEqual(self.out.count("cap_drop:"), 4)
        self.assertEqual(self.out.count("cap_add:"), 2)

    def test_sem_privilegios(self):  # AC-s1
        for proibido in ("docker.sock", "privileged", "network_mode", "pid:", "ipc:", "devices:", "/var/run",
                         "seccomp", "apparmor", "SYS_ADMIN"):
            self.assertNotIn(proibido, self.out)

    def test_separacao_de_segredos(self):  # AC-s2
        runner = servico(self.out, "runner")
        self.assertNotIn("proxy", runner)
        self.assertNotIn("OFFICE_PROXY_SEGREDO", runner)
        self.assertNotIn("OFFICE_AUTH_", runner)
        self.assertNotIn("environment:", runner)   # o ambiente do runner vem só da imagem
        for s in ("office", "caddy"):
            vs = {v for v, _ in volumes(servico(self.out, s)).values()}
            self.assertFalse(vs & {"segredos", "runner-home", "runner"}, (s, vs))
            self.assertNotIn("/projectos", servico(self.out, s))
            self.assertNotIn("/segredos", servico(self.out, s))
        self.assertEqual(volumes(servico(self.out, "caddy")),
                         {"/proxy": ("proxy", True), "/data": ("caddy-data", False),
                          "/config": ("caddy-config", False)})
        for f in SEGREDOS_FICH:
            self.assertNotIn(f, self.out)
        self.assertNotIn("OFFICE_AUTH_", self.out)

    def test_variaveis_com_omissao(self):  # AC-c8: funciona sem .env
        sem_comentarios = "\n".join(x for x in self.out.splitlines() if not x.lstrip().startswith("#"))
        vs = re.findall(r"(?<!\$)\$\{([^}]*)\}", sem_comentarios)
        self.assertTrue(vs)
        for v in vs:
            self.assertRegex(v, r"^[A-Z_]+:-[^}]+$", v)
        self.assertEqual({v.split(":-")[0] for v in vs},
                         {"OFFICE_UID", "OFFICE_GID", "OFFICE_ENDERECO", "OFFICE_PORTA", "OFFICE_PROJECTOS"})
        self.assertNotRegex(sem_comentarios, r"(?<!\$)\$[A-Za-z_]")   # nada de $VAR sem chavetas

    def test_caddyfile_em_linha(self):  # D3: configs.content = Caddyfile.docker com os $ escapados
        c = (ROOT / "Caddyfile.docker").read_text(encoding="utf-8")
        esperado = "".join(f"      {x}\n" if x else "\n" for x in c.replace("$", "$$").splitlines())
        self.assertIn(f"configs:\n  caddyfile:\n    content: |\n{esperado}networks:\n", self.out)
        self.assertIn("    configs:\n      - source: caddyfile\n        target: /etc/caddy/Caddyfile\n",
                      servico(self.out, "caddy"))

    def test_caddyfile_com_dolar_escapado(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("gerar_compose", GERADOR)
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "Caddyfile.docker"
            f.write_text(":80 {\n\trespond \"$x ${Y}\"\n}\n", encoding="utf-8")
            g.CADDYFILE_DOCKER = f
            self.assertIn('      \trespond "$$x $${Y}"\n', g.gerar_docker())
            with self.assertRaises(ValueError):
                g.gerar_docker("ghcr.io/dono/office:latest")


class Ficheiros(unittest.TestCase):
    def test_exemplo_igual_ao_gerador(self):  # AC-c1
        self.assertEqual((ROOT / "compose.example.yaml").read_text(encoding="utf-8"), docker())

    def test_modo_vps_por_omissao(self):  # AC-c2: --modo vps = sem --modo
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t) / "office.yaml"
            cfg.write_text((ROOT / "office.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
            corre = [sys.executable, str(GERADOR), "--config", str(cfg)]
            a = subprocess.run(corre, capture_output=True, text=True, timeout=30)
            b = subprocess.run([*corre, "--modo", "vps"], capture_output=True, text=True, timeout=30)
            self.assertEqual(a.returncode, 0, a.stderr)
            self.assertEqual(a.stdout, b.stdout)
            self.assertNotIn("OFFICE_MODO", a.stdout)
            r = subprocess.run([*corre, "--imagem-office", f"ghcr.io/d/o:1@{DIGEST}"], capture_output=True, text=True,
                               timeout=30)
            self.assertEqual((r.returncode, r.stdout), (2, ""))
        r = subprocess.run([sys.executable, str(GERADOR)], capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("--config", r.stderr)

    def test_env_example(self):  # D9: sem ponto inicial; as 5 variáveis, todas comentadas, com as omissões do compose
        e = (ROOT / "env.example").read_text(encoding="utf-8")
        self.assertFalse(any(x.strip() and not x.startswith("#") for x in e.splitlines()))
        valores = dict(re.findall(r"(?m)^# (OFFICE_[A-Z]+)=(\S+)$", e))
        self.assertEqual(valores, {"OFFICE_ENDERECO": "127.0.0.1", "OFFICE_PORTA": "8080",
                                   "OFFICE_PROJECTOS": "./projectos", "OFFICE_UID": "10001", "OFFICE_GID": "10001"})
        out = docker()
        for k, v in valores.items():
            self.assertIn(f"${{{k}:-{v}}}", out)

    def test_caddyfile_docker(self):  # D3
        c = (ROOT / "Caddyfile.docker").read_text(encoding="utf-8")
        self.assertIn("\n:80 {\n", c)
        self.assertNotIn("{$", c)   # nada de variáveis de ambiente: segredos só por {file.*}
        self.assertNotIn("$", c)
        self.assertRegex(c, r"basic_auth \{\s+\{file\./proxy/utilizador\} \{file\./proxy/hash\}\s+\}")
        self.assertRegex(c, r"reverse_proxy office:8080 \{\s+header_up X-Office-Segredo \{file\./proxy/segredo\}\s+\}")
        self.assertNotIn("Strict-Transport-Security", c)   # nunca há HTTPS no PC
        self.assertNotIn("import ", c)

    def test_caddyfile_docker_cabecalhos_iguais(self):  # os mesmos cabeçalhos do Caddyfile do VPS, antes do basic_auth
        def bloco(texto):
            return re.search(r"\n\theader \{\n(.*?)\n\t\}", texto, re.S).group(1)
        vps = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        c = (ROOT / "Caddyfile.docker").read_text(encoding="utf-8")
        self.assertEqual(bloco(c), bloco(vps))
        self.assertLess(c.index("\theader {"), c.index("basic_auth {"))


if __name__ == "__main__":
    unittest.main()
