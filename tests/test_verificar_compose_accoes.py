"""tools/verificar_compose_accoes.py (ADR-021, CA-18c, CA-29; ADR-022, CA-24): no compose do modo Docker só o serviço
`accoes` vê o `accoes.env` e o estado (`accoes-estado`); o `office` nunca monta o `accoes-entrada` e monta o
`accoes-saude` só de leitura. Fixtures em tests/fixtures/compose-accoes/ (bom.yaml =
saída do gerador congelada, com `/casa/` no lugar da casa do contentor por causa do filtro da CI; mau-*.yaml = uma
via cada); as outras variantes são substituições de texto sobre o bom.yaml. Só texto: nada aqui corre Docker."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "verificar_compose_accoes.py"
FIXTURES = ROOT / "tests" / "fixtures" / "compose-accoes"
FIX = (FIXTURES / "bom.yaml").read_text(encoding="utf-8")


def depois(texto: str, servico: str, ancora: str, inserir: str) -> str:
    """Insere `inserir` logo a seguir à 1.ª linha `ancora` dentro do bloco do serviço."""
    i = texto.index(f"\n  {servico}:\n")
    j = texto.index(ancora, i) + len(ancora)
    return texto[:j] + inserir + texto[j:]


VOL_SEGREDOS = "      - type: volume\n        source: accoes-segredos\n        target: /x\n        read_only: true\n"
BIND_ACCOES = FIX.replace("      - type: volume\n        source: accoes-segredos\n",
                          "      - type: bind\n        source: ./dados/accoes-segredos\n")


def correr_ficheiro(path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(TOOL), str(path)], capture_output=True, text=True, timeout=30,
                       check=False)
    return r.returncode, r.stdout


class VerificarCompose(unittest.TestCase):
    def correr(self, texto: str) -> tuple[int, str]:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            return correr_ficheiro(f.name)

    def falha(self, texto: str, servico: str) -> str:
        rc, out = self.correr(texto)
        self.assertEqual(rc, 1)
        self.assertIn(f"serviço {servico} monta accoes.env", out)
        return out

    def test_exemplo_e_gerador_passam(self):  # CA-18c
        self.assertEqual(correr_ficheiro(ROOT / "compose.example.yaml"), (0, ""))
        gerado = subprocess.run([sys.executable, str(ROOT / "runner" / "gerar_compose.py"), "--modo", "docker"],
                                capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertEqual(self.correr(gerado), (0, ""))

    def test_fixture_boa(self):
        self.assertEqual(self.correr(FIX), (0, ""))
        self.assertEqual(self.correr(BIND_ACCOES), (0, ""))
        self.assertIn("\t\theader_up X-Office-Segredo {file./proxy/segredo}", FIX)   # tabs do Caddyfile em linha
        self.assertIn("\n  accoes:\n", FIX)

    def test_fixtures_mas(self):  # CA-29: sai ≠ 0 e diz serviço e via
        esperado = {"mau-volume.yaml": "serviço runner monta accoes.env (volumes: accoes-segredos)",
                    "mau-bind.yaml": "serviço office monta accoes.env (volumes: bind ./dados)",
                    "mau-env_file.yaml": "serviço runner monta accoes.env (env_file)",
                    "mau-secrets.yaml": "serviço configurar monta accoes.env (secrets: accoes)",
                    "mau-estado.yaml": "serviço office monta o estado do accoes (volumes: accoes-estado)",
                    "mau-entrada.yaml": "serviço office monta o accoes-entrada (volumes: accoes-entrada)",
                    "mau-saude-rw.yaml": "serviço office monta o accoes-saude sem ser só de leitura "
                                         "(volumes: accoes-saude)"}
        self.assertEqual({p.name for p in FIXTURES.glob("mau-*.yaml")}, set(esperado))
        for nome, linha in esperado.items():
            with self.subTest(nome):
                self.assertEqual(correr_ficheiro(FIXTURES / nome), (1, linha + "\n"))

    def test_office_monta_accoes_estado(self):  # CA-24 (ADR-022)
        self.assertEqual(correr_ficheiro(FIXTURES / "mau-estado.yaml"),
                         (1, "serviço office monta o estado do accoes (volumes: accoes-estado)\n"))
        rc, out = self.correr(depois(FIX, "runner", "    volumes:\n", "      - accoes-estado:/e:ro\n"))
        self.assertEqual((rc, out), (1, "serviço runner monta o estado do accoes (volumes: accoes-estado)\n"))
        rc, out = self.correr(FIX.replace("ACCOES_DIR: /dados/accoes", "ACCOES_DIR: /nada"))
        self.assertEqual((rc, out.strip()), (1, "o serviço accoes não monta a pasta do ACCOES_DIR"))

    def test_bind_do_estado(self):  # CA-24: bind da pasta do estado ou de uma que a contém
        t = FIX.replace("      - type: volume\n        source: accoes-estado\n",
                        "      - type: bind\n        source: ./dados/accoes\n")
        self.assertEqual(self.correr(t), (0, ""))
        rc, out = self.correr(depois(t, "office", "    volumes:\n", "      - ./dados/accoes:/e:ro\n"))
        self.assertEqual((rc, out), (1, "serviço office monta o estado do accoes (volumes: bind ./dados/accoes)\n"))
        rc, out = self.correr(depois(t, "runner", "    volumes:\n",
                                     "      - type: bind\n        source: ./dados\n        target: /tudo\n"))
        self.assertEqual((rc, out), (1, "serviço runner monta o estado do accoes (volumes: bind ./dados)\n"))
        # comparação por componente: ./dados/accoes-outra não está dentro de ./dados/accoes
        irma = depois(t, "office", "    volumes:\n", "      - ./dados/accoes-outra:/data/outra:ro\n")
        self.assertEqual(self.correr(irma), (0, ""))

    def test_office_monta_accoes_entrada(self):  # ADR-022: mensagens dos clientes e vistas; nem só de leitura
        entrada = "serviço office monta o accoes-entrada (volumes: accoes-entrada)\n"
        for linha in ("      - accoes-entrada:/data/accoes-entrada:ro\n", "      - accoes-entrada:/x\n",
                      "      - type: volume\n        source: accoes-entrada\n        target: /x\n"):
            with self.subTest(linha):
                self.assertEqual(self.correr(depois(FIX, "office", "    volumes:\n", linha)), (1, entrada))
        # o runner lê as mensagens e as vistas: continua a poder montá-lo (só de leitura, como no gerador)
        runner = FIX[FIX.index("\n  runner:\n"):FIX.index("\n  configurar:\n")]
        self.assertIn("        source: accoes-entrada\n        target: /dados/accoes-entrada\n"
                      "        read_only: true\n", runner)
        # outro nome de topo para o mesmo volume do docker
        t = FIX.replace("\n  accoes-entrada:\n", "\n  accoes-entrada:\n    name: entrada-real\n")
        t = t.replace("\n  proxy:\n", "\n  proxy:\n  copia:\n    name: entrada-real\n")
        self.assertEqual(self.correr(t), (0, ""))
        self.assertEqual(self.correr(depois(t, "office", "    volumes:\n", "      - copia:/x:ro\n")),
                         (1, "serviço office monta o accoes-entrada (volumes: copia)\n"))
        # bind da pasta ou de uma que a contém
        b = FIX.replace("      - type: volume\n        source: accoes-entrada\n        target: /dados/accoes-entrada\n",
                        "      - type: bind\n        source: ./dados/accoes-entrada\n"
                        "        target: /dados/accoes-entrada\n")
        self.assertNotEqual(b, FIX)
        self.assertEqual(self.correr(b), (0, ""))
        for src in ("./dados/accoes-entrada", "./dados"):
            with self.subTest(src):
                self.assertEqual(self.correr(depois(b, "office", "    volumes:\n", f"      - {src}:/e:ro\n")),
                                 (1, f"serviço office monta o accoes-entrada (volumes: bind {src})\n"))
        rc, out = self.correr(FIX.replace("ACCOES_ENTRADA: /dados/accoes-entrada", "ACCOES_ENTRADA: /nada"))
        self.assertEqual((rc, out.strip()), (1, "o serviço accoes não monta a pasta do ACCOES_ENTRADA"))

    def test_office_monta_accoes_saude_so_de_leitura(self):  # ADR-022
        rw = "serviço office monta o accoes-saude sem ser só de leitura (volumes: {})\n"
        sem_ro = FIX.replace("        target: /data/accoes-saude\n        read_only: true\n",
                             "        target: /data/accoes-saude\n")
        self.assertEqual(self.correr(sem_ro), (1, rw.format("accoes-saude")))
        falso = FIX.replace("        target: /data/accoes-saude\n        read_only: true\n",
                            "        target: /data/accoes-saude\n        read_only: false\n")
        self.assertEqual(self.correr(falso), (1, rw.format("accoes-saude")))
        # 1.ª linha do item: read_only (forma longa noutra ordem)
        primeiro = FIX.replace("      - type: volume\n        source: accoes-saude\n"
                               "        target: /data/accoes-saude\n        read_only: true\n",
                               "      - read_only: true\n        type: volume\n        source: accoes-saude\n"
                               "        target: /data/accoes-saude\n")
        self.assertNotEqual(primeiro, FIX)
        self.assertEqual(self.correr(primeiro), (0, ""))
        for linha, ok in (("      - accoes-saude:/s:ro\n", True), ("      - accoes-saude:/s:ro,nocopy\n", True),
                          ("      - accoes-saude:/s\n", False), ("      - accoes-saude:/s:rw\n", False),
                          ("      - accoes-saude:/s:nocopy\n", False)):
            with self.subTest(linha):
                self.assertEqual(self.correr(depois(FIX, "office", "    volumes:\n", linha)),
                                 (0, "") if ok else (1, rw.format("accoes-saude")))
        # bind da pasta (accoes com bind): só de leitura passa, sem ele falha (também a pasta mãe)
        b = FIX.replace("      - type: volume\n        source: accoes-saude\n        target: /dados/accoes-saude\n",
                        "      - type: bind\n        source: ./dados/accoes-saude\n"
                        "        target: /dados/accoes-saude\n")
        self.assertNotEqual(b, FIX)
        self.assertEqual(self.correr(depois(b, "office", "    volumes:\n", "      - ./dados/accoes-saude:/s:ro\n")),
                         (0, ""))
        for src in ("./dados/accoes-saude", "./dados"):
            with self.subTest(src):
                self.assertEqual(self.correr(depois(b, "office", "    volumes:\n", f"      - {src}:/s\n")),
                                 (1, rw.format(f"bind {src}")))
        # só o office: o runner não tem esta regra (não o monta no gerador)
        self.assertEqual(self.correr(depois(FIX, "runner", "    volumes:\n", "      - accoes-saude:/s:ro\n")), (0, ""))
        rc, out = self.correr(FIX.replace("ACCOES_SAUDE: /dados/accoes-saude", "ACCOES_SAUDE: /nada"))
        self.assertEqual((rc, out.strip()), (1, "o serviço accoes não monta a pasta do ACCOES_SAUDE"))

    def test_env_file_no_office(self):
        out = self.falha(depois(FIX, "office", "    restart: unless-stopped\n", "    env_file: accoes.env\n"), "office")
        self.assertIn("(env_file)", out)

    def test_volume_dos_segredos(self):
        out = self.falha(depois(FIX, "runner", "    volumes:\n", VOL_SEGREDOS), "runner")
        self.assertIn("(volumes: accoes-segredos)", out)
        self.falha(depois(FIX, "caddy", "    volumes:\n", "      - accoes-segredos:/s:ro\n"), "caddy")
        self.falha(depois(FIX, "configurar", "    volumes:\n", VOL_SEGREDOS), "configurar")

    def test_bind_da_pasta_mae(self):
        mae = depois(BIND_ACCOES, "runner", "    volumes:\n",
                     "      - type: bind\n        source: ./dados\n        target: /tudo\n")
        self.falha(mae, "runner")
        self.falha(depois(BIND_ACCOES, "office", "    volumes:\n", "      - .:/repo:ro\n"), "office")
        outra = depois(BIND_ACCOES, "runner", "    volumes:\n",
                       "      - type: bind\n        source: ./dados/outra\n        target: /outra\n")
        self.assertEqual(self.correr(outra), (0, ""))

    def test_bind_de_uma_subpasta(self):   # uma subpasta do estado ou dos segredos também é proibida
        sub = depois(BIND_ACCOES, "office", "    volumes:\n", "      - ./dados/accoes-segredos/x:/x:ro\n")
        self.assertIn("monta accoes.env (volumes: bind ./dados/accoes-segredos/x)", self.falha(sub, "office"))

    def test_flow_style_falha(self):
        for chave, valor in (("volumes", "[accoes-segredos:/s]"), ("env_file", "accoes.env"), ("secrets", "[x]"),
                             ("volumes_from", "[accoes]"), ("env_file", "./outro.env")):
            with self.subTest(chave=chave, valor=valor):
                rc, out = self.correr(depois(FIX, "runner", "    init: true\n", f"    {chave}: {valor}\n"))
                self.assertEqual(rc, 1)
                self.assertIn(f"serviço runner: {chave} na mesma linha", out)

    def test_name_dos_volumes(self):   # dois nomes de topo para o mesmo volume do docker
        t = FIX.replace("\n  accoes-segredos:\n", "\n  accoes-segredos:\n    name: segredos-reais\n")
        t = t.replace("\n  proxy:\n", "\n  proxy:\n  outro:\n    name: segredos-reais\n")
        self.assertEqual(self.correr(t), (0, ""))
        out = self.falha(depois(t, "runner", "    volumes:\n", "      - outro:/x:ro\n"), "runner")
        self.assertIn("(volumes: outro)", out)

    def test_volume_sem_name_tem_o_nome_do_projecto(self):   # <name do compose>_accoes-segredos
        proj = next(x.split()[1] for x in FIX.splitlines() if x.startswith("name: "))
        t = FIX.replace("\n  proxy:\n", f"\n  proxy:\n  copia:\n    name: {proj}_accoes-segredos\n    external: true\n")
        out = self.falha(depois(t, "runner", "    volumes:\n", "      - copia:/x:ro\n"), "runner")
        self.assertIn("(volumes: copia)", out)

    def test_host_visivel(self):
        for linha in ("    privileged: true\n", '    pid: "host"\n', "    volumes_from:\n      - x\n"):
            rc, out = self.correr(depois(FIX, "office", "    restart: unless-stopped\n", linha))
            self.assertEqual(rc, 1, linha)
            self.assertIn("serviço office", out)
        rc, out = self.correr(depois(FIX, "runner", "    volumes:\n",
                                     "      - /var/run/docker.sock:/var/run/docker.sock\n"))
        self.assertIn("serviço runner vê o host (docker.sock)", out)
        rc, out = self.correr(depois(FIX, "runner", "    volumes:\n", "      - /var/lib/docker/volumes:/v:ro\n"))
        self.assertIn("serviço runner vê o host (/var/lib/docker)", out)

    def test_binds_da_raiz_devices_e_cap_add(self):
        for src in ("/", "/var", "/var/lib", "/var/run", "/run"):
            with self.subTest(src):
                rc, out = self.correr(depois(FIX, "caddy", "    volumes:\n", f"      - {src}:/h:ro\n"))
                self.assertIn(f"serviço caddy vê o host (bind {src})", out)
        for linha in ("    devices:\n      - /dev/sda:/dev/sda\n", "    cap_add:\n      - SYS_ADMIN\n"):
            for sv in ("office", "runner"):
                rc, out = self.correr(depois(FIX, sv, "    read_only: true\n", linha))
                self.assertEqual(rc, 1, (sv, linha))
                self.assertIn(f"serviço {sv} vê o host", out)

    def test_o_que_nao_se_le_nao_passa(self):
        variantes = {
            "flow": FIX.replace("\n  caddy:\n", "\n  extra: {image: x, volumes: [accoes-segredos:/s]}\n  caddy:\n"),
            "aspas servico": FIX.replace("\n  caddy:\n", '\n  "caddy":\n'),
            "aspas chave": depois(FIX, "runner", "    init: true\n", '    "volumes":\n      - accoes-segredos:/s\n'),
            "indentacao": depois(FIX, "runner", "    init: true\n", "     user: x\n"),
            "tab": depois(FIX, "runner", "    init: true\n", "\tvolumes:\n"),
            "item flow": depois(FIX, "runner", "    volumes:\n", "      - {type: volume, source: accoes-segredos}\n"),
        }
        for k, t in variantes.items():
            with self.subTest(k):
                self.assertEqual(self.correr(t)[0], 1)

    def test_conservador(self):
        rc, out = self.correr(FIX.replace("    restart: unless-stopped\n", "    <<: *base\n", 1))
        self.assertEqual(rc, 1)
        self.assertIn("não sei verificar", out)
        rc, out = self.correr(FIX.replace("\n  accoes:\n", "\n  outro:\n"))
        self.assertEqual((rc, out.strip()), (1, "falta o serviço accoes"))
        rc, out = self.correr(FIX.replace("ACCOES_SEGREDOS: /segredos-accoes", "ACCOES_SEGREDOS: /nada"))
        self.assertIn("não monta a pasta do ACCOES_SEGREDOS", out)
        self.falha(depois(FIX, "runner", "    init: true\n", "    volumes_from:\n      - accoes\n"), "runner")

    def test_uso(self):
        r = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("uso:", r.stderr)


if __name__ == "__main__":
    unittest.main()
