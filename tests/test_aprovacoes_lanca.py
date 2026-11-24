"""Marca «[lança: <slug>]» e sinal do lançar depois de aprovar (ADR-024): só apresentação, a linha e a fila v1 não mudam."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aprovacoes

TZ = ZoneInfo("Europe/Lisbon")
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=TZ).timestamp()


def pedido(*linhas):
    return "---\nestado: pendente\ncriado: 2026-11-23\n---\n# Lançar\n\n## Decisões\n" + "".join(f"{x}\n" for x in linhas)


class Marca(unittest.TestCase):
    def test_com_marca(self):
        self.assertEqual(aprovacoes.marca("Lançar `x-1` [lança: x-1]"), ("Lançar `x-1`", "x-1"))
        self.assertEqual(aprovacoes.marca("[lança: a]"), ("", "a"))

    def test_sem_marca(self):
        self.assertEqual(aprovacoes.marca("Gastar 30 €"), ("Gastar 30 €", None))

    def test_malformada_fica_crua(self):
        for t in ("x [lança: Maiusculas]", "x [lança: com espaço]", "x [lança: -comeca-hifen]", "x [lança:a]",
                  "x [lança: a/b]", "x [lança: " + "a" * 49 + "]", "x [lança: a] depois",   # o kit só lança no fim
                  "x [lança: a] [lança: b]", "[lança: a] x [lança: b]", "x [lanca: a]", "x [lança: ]"):
            self.assertEqual(aprovacoes.marca(t), (t, None), t)

    def test_parse_tira_a_marca_e_mantem_o_estado(self):
        p = aprovacoes.parse_pedido(pedido("- [L1] Lançar `x` [lança: x]", "- [L2] Lançar `y` [lança: y] → aprovado (2026-11-23)",
                                           "- [P1] Só registar 30 €", "- [P2] mau [lança: X]"), "a.md")
        got = [(d["id"], d["texto"], d["lanca"], d["feito"], d["valor"]) for d in p["decisoes"]]
        self.assertEqual(got, [("L1", "Lançar `x`", "x", None, None), ("L2", "Lançar `y`", "y", "aprovado", None),
                               ("P1", "Só registar 30 €", None, None, "30 €"), ("P2", "mau [lança: X]", None, None, None)])


class Sinal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root, self.fila = Path(self.tmp.name) / "dec", Path(self.tmp.name) / "fila"
        self.root.mkdir(), self.fila.mkdir()
        (self.root / "2026-11-23-lancar-x.md").write_text(pedido("- [L1] Lançar `x` [lança: x]"), encoding="utf-8")
        (self.root / "2026-11-23-lancar-y.md").write_text(pedido("- [L1] Lançar `y` [lança: y] → aprovado (2026-11-23)",
                                                                 "- [L2] outra"), encoding="utf-8")
        (self.root / "outro.md").write_text(pedido("- [A1] não é do kit [lança: z]", "- [A2] sem marca"), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def escrever(self, d):
        (self.root / aprovacoes.SINAL).write_text(json.dumps(d) if not isinstance(d, str) else d, encoding="utf-8")

    def estados(self):
        out = aprovacoes.listar({"geral": self.root}, self.fila, TZ, NOW)
        return {(a["ficheiro"], d["id"]): d["lancamento"] for a in out for d in a["decisoes"]}

    def test_sem_sinal_desligado(self):
        self.assertEqual(self.estados(), {("2026-11-23-lancar-x.md", "L1"): "desligado", ("2026-11-23-lancar-y.md", "L1"): "desligado",
                                          ("2026-11-23-lancar-y.md", "L2"): None, ("outro.md", "A1"): "desligado", ("outro.md", "A2"): None})

    def test_ligado(self):
        self.escrever({"v": 1, "ligado": True, "atualizado": NOW - 30,
                       "decisoes": {"2026-11-23-lancar-x.md": "pendente", "2026-11-23-lancar-y.md": "pendente"}})
        e = self.estados()
        self.assertEqual(e[("2026-11-23-lancar-x.md", "L1")], "ligado")
        self.assertEqual(e[("2026-11-23-lancar-y.md", "L1")], "espera")
        self.assertEqual(e[("outro.md", "A1")], "desligado")   # o kit só lança as decisões que criou
        self.assertIsNone(e[("outro.md", "A2")])
        for est in aprovacoes.SINAL_LANCADO:
            self.escrever({"v": 1, "ligado": True, "atualizado": NOW, "decisoes": {"2026-11-23-lancar-y.md": est}})
            self.assertEqual(self.estados()[("2026-11-23-lancar-y.md", "L1")], "lancada")

    def test_sinal_invalido_ou_velho_desligado(self):
        dec = {"2026-11-23-lancar-x.md": "pendente"}
        for d in ({"v": 1, "ligado": True, "atualizado": NOW - aprovacoes.SINAL_FRESCO_S - 1, "decisoes": dec},
                  {"v": 1, "ligado": True, "atualizado": NOW + 3600, "decisoes": dec},
                  {"v": 1, "ligado": False, "atualizado": NOW, "decisoes": dec},
                  {"v": 2, "ligado": True, "atualizado": NOW, "decisoes": dec},
                  {"v": 1, "ligado": "sim", "atualizado": NOW, "decisoes": dec},
                  {"v": 1, "ligado": True, "atualizado": True, "decisoes": dec},
                  {"v": 1, "ligado": True, "atualizado": NOW, "decisoes": []},
                  {"v": 1, "ligado": True, "decisoes": dec}, [], "{", "x" * (aprovacoes.SINAL_MAX + 1)):
            self.escrever(d)
            self.assertEqual(self.estados()[("2026-11-23-lancar-x.md", "L1")], "desligado", d if len(str(d)) < 200 else "grande")

    def test_symlink_nao_conta(self):
        alvo = Path(self.tmp.name) / "s.json"
        alvo.write_text(json.dumps({"v": 1, "ligado": True, "atualizado": NOW, "decisoes": {"2026-11-23-lancar-x.md": "pendente"}}))
        os.symlink(alvo, self.root / aprovacoes.SINAL)
        self.assertEqual(self.estados()[("2026-11-23-lancar-x.md", "L1")], "desligado")

    def test_sinal_nao_e_pedido(self):
        self.escrever({"v": 1, "ligado": True, "atualizado": NOW, "decisoes": {}})
        nomes = {a["ficheiro"] for a in aprovacoes.listar({"geral": self.root}, self.fila, TZ, NOW)}
        self.assertNotIn(aprovacoes.SINAL, nomes)


if __name__ == "__main__":
    unittest.main()
