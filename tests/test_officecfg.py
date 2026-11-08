"""officecfg: parser do subconjunto YAML, esquema/omissões, layout, /api/config e CLI (AC-a1, a2, a3, a9)."""
import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import officecfg
from officecfg import ConfigErro, ler_yaml, validar

ROOT = Path(officecfg.__file__).resolve().parent
EXEMPLO_TXT = (ROOT / "office.example.yaml").read_text(encoding="utf-8")
MINIMO = {"squads": [{"id": "dev", "nome": "Dev"}], "projectos": [{"id": "loja", "nome": "Loja"}]}


def com(**extra):
    d = json.loads(json.dumps(MINIMO))
    d.update(extra)
    return d


class ParserTest(unittest.TestCase):
    def erro(self, texto, *trechos):
        with self.assertRaises(ConfigErro) as cm:
            ler_yaml(texto)
        for t in trechos:
            self.assertIn(t, str(cm.exception))
        return str(cm.exception)

    def test_exemplo(self):
        d = ler_yaml(EXEMPLO_TXT)
        self.assertEqual(d["squads"][0]["tipos"], ["feat", "fix"])
        self.assertEqual(d["projectos"][0]["glifo"], "◆")
        self.assertIs(d["biblioteca"][0]["media"], True)
        self.assertEqual(d["runner"]["tecto_diario"], 10)
        self.assertEqual(d["utilizador"]["contacto"], "")
        self.assertEqual(d["raizes"]["dados"], "~/office-lite/dados")

    def test_vazio_e_so_comentarios(self):
        self.assertEqual(ler_yaml(""), {})
        self.assertEqual(ler_yaml("# nada\n\n   # x\n"), {})

    def test_escalares(self):
        d = ler_yaml('a: 12\nb: -3\nc: true\nd: false\ne: texto livre\nf: "x \\"y\\" \\\\ \\n"\n'
                     "g: 'it''s'\nh: ~/x\ni: \"\"\nj: '# não é comentário'\nk: a#b\n")
        self.assertEqual(d, {"a": 12, "b": -3, "c": True, "d": False, "e": "texto livre", "f": 'x "y" \\ \n',
                             "g": "it's", "h": "~/x", "i": "", "j": "# não é comentário", "k": "a#b"})

    def test_comentarios_depois_do_valor(self):
        d = ler_yaml('a: x # c\nb: "y" # c\nc: [p, q] # c\nd:  # c\n  e: 1\n')
        self.assertEqual(d, {"a": "x", "b": "y", "c": ["p", "q"], "d": {"e": 1}})

    def test_listas(self):
        d = ler_yaml("a: [feat, fix]\nb: []\nc: ['x y', \"z\"]\nl:\n  - um\n  - 2\n"
                     "m:\n  - id: a\n    n: 1\n  - id: b\n    sub:\n      - x\n")
        self.assertEqual(d["a"], ["feat", "fix"])
        self.assertEqual(d["b"], [])
        self.assertEqual(d["l"], ["um", 2])
        self.assertEqual(d["m"], [{"id": "a", "n": 1}, {"id": "b", "sub": ["x"]}])
        self.assertEqual(d["c"], ["x y", "z"])

    def test_lista_em_linha_aspas_com_virgula_recusada(self):  # conservador: erro, nunca partir em silêncio
        self.erro("c: ['x, y']\n", "linha 1")

    def test_numero_de_linha(self):
        self.erro("a: 1\n\n# c\nb: {x: 1}\n", "linha 4", "não suportado")

    def test_tab(self):
        self.erro("a:\n\tb: 1\n", "linha 2", "tabulação")
        self.erro("a: x\t\n", "linha 1")

    def test_duplicados(self):
        self.erro("a: 1\nb: 2\na: 3\n", "linha 3", "duplicada")
        self.erro("l:\n  - id: x\n    id: y\n", "linha 3", "duplicada")

    def test_indentacao(self):
        self.erro("a:\n   b: 1\n", "linha 2", "múltiplo de 2")
        self.erro("a:\n    b: 1\n", "linha 2", "indentação")
        self.erro("a: 1\n  b: 2\n", "linha 2", "indentação")
        self.erro("  a: 1\n", "linha 1")
        self.erro("l:\n  - x\n    y\n", "linha 3")

    def test_nao_suportado(self):
        for texto, linha in [("a: &x 1\n", 1), ("a: *x\n", 1), ("a: !tag x\n", 1), ("a: |\n  x\n", 1),
                             ("a: >\n  x\n", 1), ("a: {b: 1}\n", 1), ("a: null\n", 1), ("a: ~\n", 1),
                             ("a: x: y\n", 1), ("A: 1\n", 1), ("a:b\n", 1), ('"a": 1\n', 1),
                             ("a: 1\n---\nb: 2\n", 2), ("a:\n", 1), ("l:\n  -\n", 2), ("l:\n  - [a, [b]]\n", 2),
                             ("- a\n", 1), ("a: 'x\n", 1), ('a: "x\\t"\n', 1), ('a: "x" y\n', 1),
                             ("a: [x\n", 1), ("a: [x,]\n", 1), ("a:\n  b: 1\n  - c\n", 3)]:
            with self.subTest(texto=texto):
                self.erro(texto, f"linha {linha}")

    def test_documento_inicial_e_bom(self):
        self.assertEqual(ler_yaml("---\na: 1\n"), {"a": 1})
        self.assertEqual(ler_yaml("\ufeffa: 1\r\nb: 2\r\n"), {"a": 1, "b": 2})


class ValidarTest(unittest.TestCase):
    def erro(self, d, trecho):
        with self.assertRaises(ConfigErro) as cm:
            validar(d)
        self.assertIn(trecho, str(cm.exception))

    def test_exemplo(self):
        cfg = officecfg.exemplo()  # AC-a1
        self.assertEqual([s["id"] for s in cfg["squads"]], ["dev"])
        self.assertEqual(cfg["projectos"][0], {"id": "exemplo", "nome": "Exemplo", "glifo": "◆", "repo": "exemplo"})
        self.assertEqual(cfg["colunas"]["a-espera"], "À espera de Utilizador")
        self.assertEqual(list(cfg["colunas"]), ["ideias", "a-fazer", "em-curso", "a-espera", "feito"])
        self.assertEqual(cfg["runner"], {"tecto_diario": 10, "paralelo": 1, "rotacao_dias": 14,
                                         "permission_mode": "dontAsk", "settings": "",
                                         "ferramentas": officecfg.FERRAMENTAS, "max_usd": None, "modelo": None})

    def test_omissoes(self):
        cfg = validar(com())
        self.assertEqual(cfg["versao"], 1)
        self.assertEqual(cfg["marca"], "Squad Office")
        self.assertEqual(cfg["utilizador"], {"nome": "Utilizador", "contacto": ""})
        self.assertEqual(cfg["fuso"], "UTC")
        self.assertEqual(cfg["raizes"], {"dados": "~/office-lite/dados", "projectos": "~/projectos",
                                         "claude_projects": "~/.claude/projects"})
        self.assertEqual(cfg["squads"], [{"id": "dev", "nome": "Dev", "lead": "tech-lead", "personagem": None,
                                          "formato": "worktree", "tipos": ["feat", "fix"], "modos": ["goal", "task"]}])
        self.assertEqual(cfg["projectos"], [{"id": "loja", "nome": "Loja", "glifo": "◆", "repo": "loja"}])
        self.assertEqual(cfg["colunas"], {"ideias": "Ideias", "a-fazer": "A fazer", "em-curso": "Em curso",
                                          "a-espera": "À espera de Utilizador", "feito": "Feito"})
        self.assertEqual(cfg["biblioteca"], [])
        self.assertEqual(cfg["runner"]["permission_mode"], "dontAsk")
        self.assertEqual(cfg["runner"]["ferramentas"], officecfg.FERRAMENTAS)

    def test_a_espera_usa_nome_e_rotulo_proprio(self):
        self.assertEqual(validar(com(utilizador={"nome": "Ana"}))["colunas"]["a-espera"], "À espera de Ana")
        cfg = validar(com(colunas={"a-espera": "Bloqueado", "feito": "Pronto"}))
        self.assertEqual(cfg["colunas"]["a-espera"], "Bloqueado")
        self.assertEqual(cfg["colunas"]["feito"], "Pronto")
        self.assertEqual(cfg["colunas"]["ideias"], "Ideias")

    def test_obrigatorios_e_desconhecidas(self):
        self.erro({"projectos": MINIMO["projectos"]}, "squads: obrigatório")
        self.erro({"squads": MINIMO["squads"]}, "projectos: obrigatório")
        self.erro(com(marcaa="x"), "marcaa: chave desconhecida")
        self.erro(com(utilizador={"nome": "A", "email": "a@b"}), "utilizador.email: chave desconhecida")
        self.erro(com(squads=[{"id": "dev", "nome": "D", "tipo": ["feat"]}]), "squads[0].tipo: chave desconhecida")
        self.erro(com(colunas={"revisao": "R"}), "colunas.revisao: chave desconhecida")
        self.erro(com(runner={"tecto": 3}), "runner.tecto: chave desconhecida")
        self.erro(com(squads=[{"id": "dev"}]), "squads[0].nome: obrigatório")
        self.erro([], "config")

    def test_versao_marca_utilizador_fuso(self):
        self.erro(com(versao=2), "versao")
        self.erro(com(versao=True), "versao")
        self.erro(com(marca=""), "marca")
        self.erro(com(marca="x" * 41), "marca")
        self.assertEqual(validar(com(marca="x" * 40))["marca"], "x" * 40)
        self.erro(com(marca=123), "marca: tem de ser texto")
        self.erro(com(utilizador={"nome": "A\x07"}), "utilizador.nome: caracteres de controlo")
        self.erro(com(utilizador={"contacto": "nao-email"}), "utilizador.contacto")
        self.erro(com(utilizador={"contacto": "a @b"}), "utilizador.contacto")
        self.assertEqual(validar(com(utilizador={"contacto": "a@b.pt"}))["utilizador"]["contacto"], "a@b.pt")
        self.erro(com(fuso="Marte/Base"), "fuso")
        self.erro(com(fuso=5), "fuso")
        self.assertEqual(validar(com(fuso="Africa/Maputo"))["fuso"], "Africa/Maputo")

    def test_raizes(self):
        for mau in ("relativo/x", "~x", "/a/../b", "~/..", 3):
            with self.subTest(mau=mau):
                self.erro(com(raizes={"dados": mau}), "raizes.dados")
        cfg = validar(com(raizes={"dados": "/srv/d"}))
        self.assertEqual(cfg["raizes"]["dados"], "/srv/d")
        self.assertEqual(cfg["raizes"]["projectos"], "~/projectos")

    def test_squads(self):
        sq = {"id": "dev", "nome": "Dev"}
        self.erro(com(squads=[]), "squads: tem de ter entre 1 e 6")
        self.erro(com(squads=[dict(sq, id=f"s{i}x") for i in range(7)]), "squads: tem de ter entre 1 e 6")
        self.erro(com(squads=[sq, dict(sq)]), "squads[1].id: repetido")
        for mau in ("d", "Dev", "1dev", "dev_x", "a" * 21):
            with self.subTest(id=mau):
                self.erro(com(squads=[dict(sq, id=mau)]), "squads[0].id")
        self.erro(com(squads=[dict(sq, nome="x" * 25)]), "squads[0].nome")
        self.erro(com(squads=[dict(sq, lead="Tech")]), "squads[0].lead")
        self.erro(com(squads=[dict(sq, personagem="")]), "squads[0].personagem")
        self.erro(com(squads=[dict(sq, formato="outro")]), "squads[0].formato")
        self.erro(com(squads=[dict(sq, tipos=[])]), "squads[0].tipos")
        self.erro(com(squads=[dict(sq, tipos=["a1", "b2", "c3", "d4", "e5", "f6", "g7"])]), "squads[0].tipos")
        self.erro(com(squads=[dict(sq, tipos=["Feat"])]), "squads[0].tipos[0]")
        self.erro(com(squads=[dict(sq, tipos=["feat", "feat"])]), "repetidos")
        self.erro(com(squads=[dict(sq, modos=[])]), "squads[0].modos")
        self.erro(com(squads=[dict(sq, modos=["run"])]), "squads[0].modos[0]")
        self.erro(com(squads=[dict(sq, formato="sessao", tipos=["feat"])]), "squads[0].tipos: só existe")
        self.erro(com(squads=[dict(sq, formato="sessao", modos=["task"])]), "squads[0].modos: só existe")
        cfg = validar(com(squads=[sq, {"id": "marketing", "nome": "Marketing", "formato": "sessao", "lead": "cmo",
                                       "personagem": "Lia"}], ))
        self.assertEqual(cfg["squads"][1], {"id": "marketing", "nome": "Marketing", "lead": "cmo",
                                            "personagem": "Lia", "formato": "sessao", "tipos": [], "modos": []})
        self.assertEqual(validar(com(squads=[dict(sq, modos=["task"])]))["squads"][0]["modos"], ["task"])

    def test_projectos(self):
        pr = {"id": "loja", "nome": "Loja"}
        self.erro(com(projectos=[]), "projectos: tem de ter entre 1 e 30")
        self.assertEqual(len(validar(com(projectos=[dict(pr, id=f"p{i}x") for i in range(30)]))["projectos"]), 30)
        self.erro(com(projectos=[dict(pr, id=f"p{i}x") for i in range(31)]), "projectos")
        self.erro(com(projectos=[dict(pr, id="outro")]), "reservado")
        self.erro(com(projectos=[pr, dict(pr)]), "projectos[1].id: repetido")
        self.erro(com(projectos=[dict(pr, id="a" * 25)]), "projectos[0].id")
        self.erro(com(projectos=[dict(pr, nome="x" * 41)]), "projectos[0].nome")
        self.erro(com(projectos=[dict(pr, glifo="abc")]), "projectos[0].glifo")
        self.erro(com(projectos=[dict(pr, glifo="\n")]), "projectos[0].glifo")
        for mau in (".", "..", "a/b", "a b", ""):
            with self.subTest(repo=mau):
                self.erro(com(projectos=[dict(pr, repo=mau)]), "projectos[0].repo")
        self.assertEqual(validar(com(projectos=[dict(pr, repo="Loja.v2_x-1", glifo="🛒")]))["projectos"][0]["repo"],
                         "Loja.v2_x-1")

    def test_biblioteca(self):
        f = {"id": "notas", "rotulo": "Notas", "pasta": "notas"}
        cfg = validar(com(biblioteca=[f]))
        self.assertEqual(cfg["biblioteca"], [dict(f, media=False, tipo=None)])
        self.erro(com(biblioteca=[dict(f, id=f"f{i}x") for i in range(13)]), "biblioteca: tem de ter entre 0 e 12")
        self.erro(com(biblioteca=[dict(f, id="tarefas")]), "reservado")
        self.erro(com(biblioteca=[dict(f, id="aprovacoes-x")]), "reservado")
        self.erro(com(biblioteca=[f, dict(f)]), "biblioteca[1].id: repetido")
        for mau in ("/abs", "../x", "a/../b", "a//b", "a/", ".", "a b", "", 1):
            with self.subTest(pasta=mau):
                self.erro(com(biblioteca=[dict(f, pasta=mau)]), "biblioteca[0].pasta")
        self.assertEqual(validar(com(biblioteca=[dict(f, pasta="a/b.c")]))["biblioteca"][0]["pasta"], "a/b.c")
        self.erro(com(biblioteca=[dict(f, media="sim")]), "biblioteca[0].media")
        self.erro(com(biblioteca=[dict(f, tipo="outros")]), "biblioteca[0].tipo")
        self.assertEqual(validar(com(biblioteca=[dict(f, tipo="planos")]))["biblioteca"][0]["tipo"], "planos")
        self.erro(com(biblioteca=[dict(f, rotulo="x" * 31)]), "biblioteca[0].rotulo")

    def test_runner(self):
        for k, lo, hi in (("tecto_diario", 1, 100), ("paralelo", 1, 4), ("rotacao_dias", 1, 365)):
            with self.subTest(k=k):
                self.assertEqual(validar(com(runner={k: lo}))["runner"][k], lo)
                self.assertEqual(validar(com(runner={k: hi}))["runner"][k], hi)
                self.erro(com(runner={k: lo - 1}), f"runner.{k}")
                self.erro(com(runner={k: hi + 1}), f"runner.{k}")
                self.erro(com(runner={k: True}), f"runner.{k}")
        self.erro(com(runner={"permission_mode": "yolo"}), "runner.permission_mode")
        self.erro(com(runner={"settings": "rel.json"}), "runner.settings")
        self.erro(com(runner={"settings": "~/s.json"}), "runner.settings")
        self.assertEqual(validar(com(runner={"settings": "/etc/s.json"}))["runner"]["settings"], "/etc/s.json")

    def test_max_usd(self):
        self.assertIsNone(validar(com())["runner"]["max_usd"])
        for v, esperado in ((5, "5"), ("5", "5"), ("2.5", "2.5"), (1000, "1000"), ("1000.00", "1000.00"),
                            ("0.01", "0.01"), ("999.99", "999.99")):
            with self.subTest(v=v):
                self.assertEqual(validar(com(runner={"max_usd": v}))["runner"]["max_usd"], esperado)
        for mau in (0, "0", "0.00", -1, "-1", 1001, "1000.01", "abc", True, False, "2.555", "05", "1e3", " 5",
                    "5.", ".5", "", 2.5, [], {}):
            with self.subTest(mau=mau):
                self.erro(com(runner={"max_usd": mau}),
                          "runner.max_usd: tem de ser um número > 0 e ≤ 1000, até 2 casas decimais")

    def test_max_usd_em_yaml(self):
        # sem aspas: o parser lê 5 como inteiro e 2.5 como texto; true como booleano (recusado)
        base = "squads:\n  - id: dev\n    nome: Dev\nprojectos:\n  - id: loja\n    nome: Loja\nrunner:\n"
        self.assertEqual(validar(ler_yaml(base + "  max_usd: 5\n"))["runner"]["max_usd"], "5")
        self.assertEqual(validar(ler_yaml(base + "  max_usd: 2.5\n"))["runner"]["max_usd"], "2.5")
        self.assertEqual(validar(ler_yaml(base + "  modelo: claude-sonnet-4-5\n"))["runner"]["modelo"],
                         "claude-sonnet-4-5")
        for mau in ("true", "0", "-1", "1000.01", "abc", "2.555"):
            with self.subTest(mau=mau):
                self.erro(ler_yaml(base + f"  max_usd: {mau}\n"), "runner.max_usd")

    def test_modelo(self):
        self.assertIsNone(validar(com())["runner"]["modelo"])
        for v in ("sonnet", "opus", "claude-sonnet-4-5", "a", "x.y_z-1", "A" * 64):
            with self.subTest(v=v):
                self.assertEqual(validar(com(runner={"modelo": v}))["runner"]["modelo"], v)
        for mau in ("son net", "-x", ".x", "_x", "A" * 65, "", "sonnet[1m]", "a/b", "a\n", 5, True, []):
            with self.subTest(mau=mau):
                self.erro(com(runner={"modelo": mau}), "runner.modelo: inválido (letras, dígitos, . _ -, até 64)")

    def test_versao_continua_so_1(self):
        self.erro(com(versao=2, runner={"max_usd": 5}), "versao")

    def test_permission_mode(self):
        for pm in ("dontAsk", "default", "acceptEdits", "auto", "bypassPermissions"):
            self.assertEqual(validar(com(runner={"permission_mode": pm}))["runner"]["permission_mode"], pm)
        self.erro(com(runner={"permission_mode": "dontask"}), "runner.permission_mode")

    def test_ferramentas(self):
        f = officecfg.FERRAMENTAS
        for x in ("Read", "Edit", "Agent", "Bash(git *)", "Bash(gh pr create *)", "Bash(gh pr ready *)",
                  "Bash(pytest *)", "Bash(uv *)", "Bash(npm *)"):
            self.assertIn(x, f)
        # 0.1.2: sem web nem os Bash de ficheiros que Read/Grep/Glob/Write/Edit substituem
        for x in ("WebSearch", "WebFetch", "Bash(cat *)", "Bash(head *)", "Bash(tail *)", "Bash(cp *)", "Bash(mv *)",
                  "Bash(echo *)"):
            self.assertNotIn(x, f)
        self.assertEqual(len(f), len(set(f)))
        self.assertFalse([x for x in f if "merge" in x or "release" in x or "delete" in x])
        ok = ["Read", "Bash(npm run test *)", "mcp__srv__ferramenta", "WebFetch(domain:example.com)"]
        self.assertEqual(validar(com(runner={"ferramentas": ok}))["runner"]["ferramentas"], ok)
        self.assertEqual(validar(com(runner={"ferramentas": []}))["runner"]["ferramentas"], [])
        self.assertEqual(len(validar(com(runner={"ferramentas": ["Read"] * 100}))["runner"]["ferramentas"]), 100)
        self.erro(com(runner={"ferramentas": ["Read"] * 101}), "runner.ferramentas")
        self.erro(com(runner={"ferramentas": "Read"}), "runner.ferramentas")
        for mau in ("", "-p", "--dangerously-skip-permissions", "Read Edit", "Read,Edit", "Bash(git *", "Bash()",
                    "Bash((x))", "Bash(git\n*)", "Bash(a\x00)", "1Read", "Bash(" + "x" * 201 + ")", 3, True, None):
            with self.subTest(mau=mau):
                self.erro(com(runner={"ferramentas": ["Read", mau]}), "runner.ferramentas[1]")

    def test_ferramentas_em_yaml(self):
        d = ler_yaml("runner:\n  ferramentas:\n    - Read\n    - Bash(git *)\n    - \"Bash(gh pr view *)\"\n")
        self.assertEqual(d["runner"]["ferramentas"], ["Read", "Bash(git *)", "Bash(gh pr view *)"])
        self.assertEqual(ler_yaml("runner:\n  ferramentas: [Read, Bash(git *)]\n")["runner"]["ferramentas"],
                         ["Read", "Bash(git *)"])

    def test_credenciais_claude_ao_lado_da_config(self):
        self.assertEqual(officecfg.credenciais_claude("/x/y/office.yaml"), Path("/x/y/claude.env"))

    def test_erro_sem_caminho_de_ficheiro(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "office.yaml"
            p.write_text("squads: {x: 1}\n", encoding="utf-8")
            with self.assertRaises(ConfigErro) as cm:
                officecfg.carregar(p, estrito=True)
            self.assertNotIn(tmp, str(cm.exception))
            self.assertTrue(str(cm.exception).startswith("linha 1:"))


class CarregarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fich(self, texto, nome="office.yaml"):
        p = self.dir / nome
        p.write_text(texto, encoding="utf-8")
        return p

    def test_valido(self):
        cfg, avisos = officecfg.carregar(self.fich(EXEMPLO_TXT.replace("nome: Utilizador", "nome: Ana")))
        self.assertEqual(avisos, [])
        self.assertEqual(cfg["utilizador"]["nome"], "Ana")

    def test_cai_no_exemplo_com_aviso(self):  # AC-a2
        casos = {"ausente": (self.dir / "nao-existe.yaml", "config ausente"),
                 "vazio": (self.fich("# só comentário\n", "v.yaml"), "config vazia"),
                 "partido": (self.fich("squads: [\n", "p.yaml"), "linha 1"),
                 "invalido": (self.fich("squads: []\nprojectos: []\n", "i.yaml"), "squads"),
                 "pasta": (self.dir, "config ilegível")}
        for nome, (p, trecho) in casos.items():
            with self.subTest(nome):
                cfg, avisos = officecfg.carregar(p)
                self.assertEqual(cfg, officecfg.exemplo())
                self.assertEqual(len(avisos), 1)
                self.assertIn(trecho, avisos[0])
                self.assertNotIn(self.tmp.name, avisos[0])
                with self.assertRaises(ConfigErro):
                    officecfg.carregar(p, estrito=True)

    def test_aviso_nao_leva_o_conteudo_da_linha(self):  # o aviso vai para /api/config
        p = self.fich("marca: x\n/srv/segredo/repo-privado sem chave\n", "c.yaml")
        _, avisos = officecfg.carregar(p)
        self.assertEqual(avisos, ["linha 2: não suportado"])
        self.assertNotIn("segredo", officecfg.publico(officecfg.exemplo(), avisos[0])["aviso"])

    def test_utf8_invalido(self):
        p = self.dir / "office.yaml"
        p.write_bytes(b"marca: \xff\n")
        self.assertEqual(officecfg.carregar(p)[1], ["config ilegível"])

    def test_sem_base_de_fusos_nao_rebenta(self):
        # imagem sem tzdata: o exemplo (Europe/Lisbon) tem de carregar na mesma; nomes mal formados continuam a falhar
        env = {**os.environ, "PYTHONTZPATH": "/nao/existe"}
        code = ("import officecfg, zoneinfo\n"
                "if zoneinfo.available_timezones(): print('com-tzdata'); raise SystemExit\n"
                "cfg, avisos = officecfg.carregar('/nao/existe.yaml')\n"
                "print(cfg['fuso'], avisos)\n"
                "try:\n    officecfg.validar({**officecfg.ler_yaml('squads: []'), 'squads': cfg['squads'],"
                " 'projectos': cfg['projectos'], 'fuso': '../x'})\nexcept officecfg.ConfigErro as e:\n    print(e)\n")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                             timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        if out.stdout.strip() == "com-tzdata":   # pacote tzdata instalado no Python: não dá para simular
            self.skipTest("tzdata do pip presente")
        self.assertEqual(out.stdout.splitlines(), ["Europe/Lisbon ['config ausente']",
                                                   "fuso: fuso horário IANA desconhecido (ex.: Europe/Lisbon)"])


class LayoutTest(unittest.TestCase):
    def cfg(self, **raizes):
        return validar(com(raizes=raizes, biblioteca=[{"id": "notas", "rotulo": "N", "pasta": "a/notas"},
                                                       {"id": "media", "rotulo": "M", "pasta": "a/notas"}]))

    def test_raiz_e_caminho(self):
        cfg = self.cfg(dados="/srv/x/dados", projectos="/srv/p", claude_projects="/srv/c")
        self.assertEqual(officecfg.raiz(cfg, "dados"), Path("/srv/x/dados"))
        self.assertEqual(officecfg.caminho(cfg, "inbox"), Path("/srv/x/dados/tasks/inbox"))
        self.assertEqual(officecfg.caminho(cfg, "quadro_lancar"), Path("/srv/x/dados/quadro-lancar"))
        with self.assertRaises(ConfigErro):
            officecfg.raiz(cfg, "tasks")

    def test_raiz_expande_home(self):
        antigo = os.environ.get("HOME")
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            try:
                cfg = validar(com())
                self.assertEqual(officecfg.raiz(cfg, "dados"), Path(home).resolve() / "office-lite" / "dados")
                self.assertTrue(officecfg.raiz(cfg, "claude_projects").is_absolute())
            finally:
                os.environ["HOME"] = antigo

    def test_pastas(self):
        cfg = self.cfg(dados="/d", projectos="/p", claude_projects="/c")
        ps = officecfg.pastas(cfg)
        caminhos = [str(p) for _, p in ps]
        self.assertEqual(caminhos[:len(officecfg.SUB)], [f"/d/{s}" for s in officecfg.SUB.values()])
        self.assertEqual(caminhos[len(officecfg.SUB):], ["/d/biblioteca/a/notas", "/c", "/p"])  # sem repetidos
        modos = {str(p): m for m, p in ps}
        self.assertEqual(modos["/d/push"], "0700")
        self.assertEqual({m for c, m in modos.items() if c != "/d/push"}, {"0750"})

    def test_mounts_cobrem_sub(self):
        for origem, destino, modo, env in officecfg.MOUNTS:
            self.assertTrue(origem in officecfg.SUB or origem == "claude_projects", origem)
            self.assertTrue(destino.startswith("/data/"))
            self.assertIn(modo, ("ro", "rw"))
        self.assertEqual(sorted(o for o, _, m, _ in officecfg.MOUNTS if m == "rw"),
                         ["decisoes_fila", "push", "quadro", "quadro_lancar"])

    def test_mangle(self):  # AC-b1
        self.assertEqual(officecfg.mangle("/tmp/xyz/.worktrees/20261106-x"), "-tmp-xyz--worktrees-20261106-x")


class PublicoTest(unittest.TestCase):
    def test_fixture_do_exemplo(self):  # AC-a3
        fixture = json.loads((ROOT / "tests" / "fixtures" / "api_config_exemplo.json").read_text(encoding="utf-8"))
        self.assertEqual(officecfg.publico(officecfg.exemplo(), None), fixture)

    def valores(self, x):
        if isinstance(x, dict):
            for v in x.values():
                yield from self.valores(v)
        elif isinstance(x, list):
            for v in x:
                yield from self.valores(v)
        else:
            yield x

    def test_sem_caminhos_nem_segredos(self):
        cfg = validar(com(utilizador={"nome": "Ana", "contacto": "ana@x.pt"}, raizes={"dados": "/srv/segredo"},
                          biblioteca=[{"id": "notas", "rotulo": "N", "pasta": "pasta-secreta"}],
                          runner={"settings": "/etc/s.json"}))
        corpo = officecfg.publico(cfg, "config ausente")
        texto = json.dumps(corpo, ensure_ascii=False)
        for segredo in ("ana@x.pt", "/srv/segredo", "pasta-secreta", "/etc/s.json", "raizes", "runner", "repo"):
            self.assertNotIn(segredo, texto)
        for v in self.valores(corpo):
            if isinstance(v, str):
                self.assertFalse(v.startswith(("/", "~")), v)
        self.assertEqual(corpo["aviso"], "config ausente")

    def test_cores_e_lancar(self):
        cfg = validar(com(projectos=[{"id": f"p{i}x", "nome": f"P{i}"} for i in range(10)],
                          squads=[{"id": "ops", "nome": "Ops"},
                                  {"id": "mkt", "nome": "Mkt", "formato": "sessao"}]))
        corpo = officecfg.publico(cfg, None)
        self.assertEqual([p["cor"] for p in corpo["projectos"]], [f"c{i % 8}" for i in range(10)])
        self.assertEqual(corpo["lancar"]["squads"], ["ops"])
        self.assertEqual(len(corpo["lancar"]["projectos"]), 10)
        self.assertEqual(corpo["squads"][1]["formato"], "sessao")
        so_sessao = officecfg.publico(validar(com(squads=[{"id": "mkt", "nome": "M", "formato": "sessao"}])), None)
        self.assertEqual(so_sessao["lancar"], {"squads": [], "projectos": []})


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        env = dict(os.environ, HOME=str(self.home))
        return subprocess.run([sys.executable, str(ROOT / "officecfg.py"), *map(str, args)],
                              check=False, capture_output=True, text=True, env=env, cwd=self.tmp.name, timeout=30)

    def test_validar(self):
        r = self.run_cli("validar", ROOT / "office.example.yaml")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        mau = self.home / "mau.yaml"
        mau.write_text("marca: x\nmarca: y\n", encoding="utf-8")
        r = self.run_cli("validar", mau)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.strip(), "erro: linha 2: chave duplicada: marca")
        r = self.run_cli("validar", self.home / "nao-existe.yaml")
        self.assertEqual((r.returncode, r.stderr.strip()), (1, "erro: config ausente"))

    def test_pastas(self):
        r = self.run_cli("pastas", ROOT / "office.example.yaml")
        self.assertEqual(r.returncode, 0, r.stderr)
        linhas = r.stdout.splitlines()
        h = self.home.resolve()
        self.assertIn(f"0750 {h}/office-lite/dados/tasks", linhas)
        self.assertIn(f"0700 {h}/office-lite/dados/push", linhas)
        self.assertIn(f"0750 {h}/office-lite/dados/biblioteca/material", linhas)
        self.assertIn(f"0750 {h}/.claude/projects", linhas)
        self.assertIn(f"0750 {h}/projectos", linhas)
        self.assertTrue(all(len(x.split(" ", 1)[0]) == 4 and x.split(" ", 1)[1].startswith("/") for x in linhas))

    def test_raiz(self):
        r = self.run_cli("raiz", ROOT / "office.example.yaml", "projectos")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, f"{self.home.resolve()}/projectos"))
        r = self.run_cli("raiz", ROOT / "office.example.yaml", "tasks")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("erro: raiz desconhecida"))

    def test_uso(self):
        for args in ((), ("validar",), ("raiz", "x"), ("outro", "x"), ("validar", "a", "b")):
            with self.subTest(args=args):
                r = self.run_cli(*args)
                self.assertEqual(r.returncode, 2)
                self.assertIn("uso:", r.stderr)


class CompatTest(unittest.TestCase):
    def test_python_310(self):  # sintaxe aceite pelo Python 3.10 (runner no host Ubuntu 22.04)
        ast.parse((ROOT / "officecfg.py").read_text(encoding="utf-8"), feature_version=(3, 10))
        texto = (ROOT / "officecfg.py").read_text(encoding="utf-8")
        for proibido in ("tomllib", "datetime.UTC", "import yaml"):
            self.assertNotIn(proibido, texto)

    def test_exemplo_exacto_do_plan(self):
        linhas = EXEMPLO_TXT.splitlines()
        self.assertEqual(linhas[0][:2], "# ")
        self.assertEqual(sum(1 for x in linhas if x == "  nome: Utilizador"), 1)  # o instalador troca esta linha


if __name__ == "__main__":
    unittest.main()


class RaizDadosTest(unittest.TestCase):
    """Só raizes.dados, para o `office_runner.py parar` com o resto do office.yaml inválido."""

    def ler(self, texto):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "office.yaml"
            p.write_text(texto, encoding="utf-8")
            return officecfg.raiz_dados(p)

    def test_resto_invalido_nao_importa(self):
        self.assertEqual(self.ler("raizes:\n  dados: /srv/dados\nrunner:\n  tecto_diario: 999\nxpto: 1\n"),
                         "/srv/dados")
        self.assertEqual(self.ler("raizes:\n  dados: ~/d\n  projectos: relativo\n"), "~/d")

    def test_omissao(self):
        self.assertEqual(self.ler("squads: 1\n"), "~/office-lite/dados")
        self.assertEqual(self.ler("raizes:\n  projectos: /p\n"), "~/office-lite/dados")

    def test_erros(self):
        for texto, trecho in (("raizes:\n  dados: relativo\n", "raizes.dados"),
                              ("raizes:\n  dados: /a/../b\n", "raizes.dados"),
                              ("raizes:\n  dados: 3\n", "raizes.dados"),
                              ("raizes: 1\n", "raizes: tem de ser um mapeamento"),
                              ("raizes:\n\tdados: /x\n", "tabulação")):
            with self.subTest(texto=texto), self.assertRaises(ConfigErro) as cm:
                self.ler(texto)
            self.assertIn(trecho, str(cm.exception))
        with self.assertRaises(ConfigErro) as cm:
            officecfg.raiz_dados("/nao/existe/office.yaml")
        self.assertEqual(str(cm.exception), "config ausente")
