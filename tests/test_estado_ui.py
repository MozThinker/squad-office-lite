"""Fase 1 de UX (porte do Office, CA-31/CA-32): peça de estado nas vistas, tokens de movimento e «Animações».

Estáticos (sem browser); o CA-32 com estilos calculados fica no e2e (tools/e2e_conversa.mjs).
"""
import re
import unittest
from pathlib import Path

ST = Path(__file__).resolve().parent.parent / "static"


def ler(nome):
    return (ST / nome).read_text(encoding="utf-8")


def tokens(bloco):
    return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", bloco)}


class PecaNasVistasTest(unittest.TestCase):
    def test_ca31_estado_css_por_link_e_estado_js_a_pedido(self):
        html = ler("index.html")
        self.assertIn('<link rel="stylesheet" href="estado.css">', html)
        self.assertIn('import("./estado.js")', html)
        self.assertNotRegex(html, r"<script[^>]*estado\.js")

    def test_ca31_cada_vista_usa_a_peca(self):
        html = ler("index.html")
        # Aprovações: o pedido completo a carregar e com erro
        self.assertIn('const adS = (e, x) => (EST ? EST.pintar($("#ad-s"), e, x, Date.now()) : ($("#ad-s").textContent = x));', html)
        self.assertIn('adS("a-processar", "a carregar");', html)
        self.assertIn('if (n === adSeq) adS("erro", m);', html)
        q = ler("quadro.js")
        self.assertIn('import { html as estH, ligar as estLigar } from "./estado.js";', q)
        self.assertIn('estH("a-processar", "a lançar", Date.parse(l.pedido_em))', q)
        self.assertIn('estH("erro", "erro ao lançar")', q)
        b = ler("biblioteca.js")
        self.assertIn('peca(q(".bib-cnt"), "a-processar", "a carregar", Date.now());', b)
        self.assertIn('estH("erro", "Não foi possível carregar a biblioteca.")', b)
        a = ler("agentes.js")   # sem imports (regra do módulo): a mesma peça feita com h()
        self.assertIn('h("span", { class: "estado", "data-estado": "a-processar", "aria-live": "polite" }', a)
        self.assertNotIn("A carregar…", a)
        c = ler("conversa.js")
        self.assertIn('import { pintar } from "./estado.js";', c)
        self.assertIn('pintar(msg, "a-processar", "a carregar", Date.now());', c)
        self.assertIn('pintar(n, "erro", ', c)

    def test_ca31_nenhum_a_carregar_solto_nas_vistas_novas(self):
        for f in ("conversa.js", "agentes.js", "quadro.js"):
            with self.subTest(f=f):
                self.assertNotRegex(ler(f), r"A carregar…|a aplicar…|A criar a conversa…")


class MovimentoTest(unittest.TestCase):
    CSS = ler("estado.css")   # no estado.css (por <link> no <head>): o :root do office.css é o tema escuro

    def test_tokens_com_os_valores_da_auditoria(self):
        t = tokens(re.search(r"^:root \{ --dur-micro[^}]*\}", self.CSS, re.M).group())
        for k, v in {"--dur-micro": "100ms", "--dur-curta": "200ms", "--dur-media": "300ms", "--dur-longa": "500ms",
                     "--dur-pulso": "1600ms", "--dur-pontos": "1200ms"}.items():
            self.assertEqual(t.get(k), v, k)
        for k in ("--ease-padrao", "--ease-entrar", "--ease-sair"):
            self.assertRegex(t.get(k, ""), r"^cubic-bezier\(", k)

    def test_ca32_reduce_e_data_anim_zeram_tokens_e_desligam_tudo(self):
        root = tokens(re.search(r"^:root \{ --dur-micro[^}]*\}", self.CSS, re.M).group())
        durs = {k for k in root if k.startswith("--dur-")}
        reduce = tokens(re.search(r"@media \(prefers-reduced-motion: reduce\) \{ :root \{([^}]*)\}", self.CSS).group(1))
        off = tokens(re.search(r':root\[data-anim="off"\] \{([^}]*)\}', self.CSS).group(1))
        for z in (reduce, off):
            self.assertEqual(set(z), durs)
            self.assertTrue(all(re.fullmatch(r"0m?s", v) for v in z.values()), z)
        regra = "animation: none !important; transition: none !important;"
        self.assertIn("@media (prefers-reduced-motion: reduce) { *, ::before, ::after { " + regra + " } }", self.CSS)
        self.assertIn('[data-anim="off"] *, [data-anim="off"] ::before, [data-anim="off"] ::after { ' + regra + " }", self.CSS)

    def test_ca32_css_novos_so_com_tokens(self):
        for f in ("estado.css", "conversa.css"):
            for padrao in (r"[0-9.]+m?s (ease|cubic)", r"(animation|transition)[^;]*\b[0-9.]+m?s"):
                with self.subTest(f=f, padrao=padrao):
                    self.assertNotRegex(ler(f), padrao)

    def test_ca32_head_aplica_data_anim_antes_da_1a_pintura(self):
        html = ler("index.html")
        head = html[: html.index("</head>")]
        i = head.find('localStorage.getItem("animacoes") === "0") document.documentElement.dataset.anim = "off"')
        self.assertGreater(i, 0)
        abre = head.rfind("<script", 0, i)
        self.assertEqual(head[abre:head.index(">", abre) + 1], "<script>")
        self.assertLess(i, head.index('<link rel="stylesheet"'))

    def test_ca32_js_respeita_animacoes_desligadas(self):
        html = ler("index.html")
        self.assertEqual(html.count('matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.anim === "off"'), 2)
        for f in ("quadro.js", "lateral.js"):
            self.assertIn('const reduz = () => matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.anim === "off";', ler(f))
        d = ler("definicoes.js")
        self.assertIn('<input type="checkbox" id="def-anim"> Animações', d)
        self.assertIn('document.documentElement.dataset.anim = on ? "on" : "off"; try { localStorage.setItem("animacoes", on ? "1" : "0"); }', d)


if __name__ == "__main__":
    unittest.main()
