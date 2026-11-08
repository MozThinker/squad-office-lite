"""Ronda de review das aprovações: valor, cache por geração, gramática igual à do aplicador, 409 «recebida»,
surrogates e leitura limitada (python3 -m unittest)."""
import os
import unittest

import aprovacoes
from tests.test_aprovacoes import Base, pedido
from tests import cfg

OFFICE_CFG = cfg.LEGADO


class ValorTest(Base):
    def test_regra(self):
        casos = {
            # marcador ganha ao 1.º valor
            "4 conjuntos × 1,25 €/dia · 4–30/11 · tecto 145 €": "145 €",
            "Teto: 90 € e 3 € de margem": "90 €",
            "máx. 50 € · 2 € por clique": "50 €",
            "máximo 1 200 € por mês": "1 200 €",
            "TOTAL 300 USD em 3 × 100 USD": "300 USD",
            "orçamento $ 40 e 3 €": "$ 40",
            # milhares com espaço, espaço fino, sem quebra e ponto
            "tecto 1 200 €": "1 200 €",
            "Tecto 1 200 MT": "1 200 MT",
            "custo 12 500 MT": "12 500 MT",
            "teto 1.200,50 €": "1.200,50 €",
            # um só valor sem marcador
            "a 4,75€ por dia": "4,75€",
            "US$ 30 de teste": "$ 30",
            # vários sem marcador → nenhum
            "ads $12,50 e 3 €": None,
            "4 conjuntos × 1,25 €/dia e 2 €": None,
            # dentro de palavras / a meio de números
            "10 MTN": None, "2 USDT": None, "A10 €": None, "Campanha 2026 120 €": None, "x 1 2000 €": None,
            "$1 2000": None, "sem valor 12 dias": None,
            "tecto 1 200 MTN e 5 €": "5 €",   # o marcador sem valor válido não conta; fica o único
        }
        for texto, esperado in casos.items():
            with self.subTest(texto):
                self.assertEqual(aprovacoes.valor(texto), esperado)

    def test_no_listar(self):
        self.put("a.md", pedido(decisoes=["- [P2] Campanha ZFMZ26 · 4 conjuntos × 1,25 €/dia · 4–30/11 · tecto 145 €",
                                          "- [P3] tecto 1 200 €", "- [P4] 10 MTN e 2 USDT"]))
        [a] = self.listar()
        self.assertEqual([d["valor"] for d in a["decisoes"]], ["145 €", "1 200 €", None])


class GramaticaAplicadorTest(Base):
    """Contrato com runner/decisoes.py: o que o Office mostra em aberto é o que o aplicador aceita."""

    def ids(self, **kw):
        self.put("a.md", pedido(**kw))
        out = self.listar()
        return {d["id"]: (d["texto"], d["feito"]) for d in out[0]["decisoes"]} if out else None

    def test_linhas(self):
        got = self.ids(decisoes=["- [P1] ok", "  - [P2] indentada", "-\t[P3] tab", "-  [P4] dois espaços",
                                 "- [P5]  texto com espaço", "- [P6] y → aprovado (2026-10-24) ",
                                 "- [P7] z →  recusado (2026-10-24)", "- [P8] w → aprovado (2026-10-24)",
                                 "- [P9] ", "- [P10]x"])
        self.assertEqual(got, {"P1": ("ok", None), "P5": (" texto com espaço", None),
                               "P6": ("y → aprovado (2026-10-24) ", None),   # espaço no fim: ainda em aberto
                               "P7": ("z →  recusado (2026-10-24)", None),
                               "P8": ("w", "aprovado"), "P9": ("", None)})

    def test_estado_maiusculas_e_comentario(self):
        self.assertIsNone(self.ids(estado="Pendente"))
        self.assertIsNone(self.ids(estado="PARCIAL (1 de 2)"))
        self.assertIsNone(self.ids(estado="# pendente"))
        self.assertIsNotNone(self.ids(estado="pendente   # pendente | aprovado"))
        self.assertIsNotNone(self.ids(estado="parcial (1 de 2 decididas)"))

    def test_frontmatter_como_o_aplicador(self):
        base = pedido(decisoes=["- [P1] x"])
        casos = {"bom": (base, True),
                 "BOM no início": ("\ufeff" + base, False),
                 "CRLF": (base.replace("\n", "\r\n"), False),
                 "--- com espaço": (base.replace("---\n", "--- \n", 1), False),
                 "chave Estado": (base.replace("estado:", "Estado:", 1), False),
                 "estado repetido, o último ganha": (base.replace("estado: pendente\n",
                                                                  "estado: pendente\nestado: aprovado\n"), False)}
        for nome, (texto, conta) in casos.items():
            with self.subTest(nome):
                self.put("a.md", texto)
                self.assertEqual(bool(self.listar()), conta)

    def test_varias_seccoes_decisoes(self):
        got = self.ids(decisoes=["- [P1] a"], extra="## Outra\n- [X1] fora\n\n## Decisões\n- [P2] b\n- [P1] repetido\n")
        self.assertEqual(got, {"P1": ("a", None), "P2": ("b", None)})


class LerTest(Base):
    def test_maior_que_1_mib_ignorado(self):
        base = pedido(decisoes=["- [P1] x"])
        self.put("limite.md", base + "x" * (aprovacoes.PEDIDO_MAX - len(base.encode())))
        self.put("grande.md", base + "x" * (aprovacoes.PEDIDO_MAX - len(base.encode()) + 1))
        self.assertEqual([a["ficheiro"] for a in self.listar()], ["limite.md"])
        self.assertEqual(aprovacoes.corpo(self.dirs, "marketing", "grande.md")[0], 404)
        self.assertIsNone(aprovacoes._ler(self.mkt, "grande.md"))

    def test_symlink_e_fifo_ignorados(self):
        self.put("a.md", pedido())
        alvo = self.fila.parent / "fora.md"   # fora das fontes
        alvo.write_text(pedido())
        os.symlink(alvo, self.mkt / "lig.md")
        os.symlink(self.mkt / "a.md", self.mkt / "lig2.md")
        os.mkfifo(self.mkt / "fifo.md")   # sem O_NONBLOCK o open ficava à espera de um escritor
        self.assertEqual([a["ficheiro"] for a in self.listar()], ["a.md"])
        for nome in ("lig.md", "lig2.md", "fifo.md"):
            self.assertIsNone(aprovacoes._ler(self.mkt, nome))


if __name__ == "__main__":
    unittest.main()
