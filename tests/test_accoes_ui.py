"""«Aprovar todas» nunca num pedido de acções `accoes-*.md` (CA-21, ADR-021): o botão não aparece e o JS não envia
«todas». Corre as funções verdadeiras do static/index.html em node (salta sem node); sem rede nem relógio."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"

JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const corte = (ini, fim) => { const a = src.indexOf(ini); if (a < 0) throw new Error(ini); return src.slice(a, src.indexOf(fim, a) + fim.length); };
const todas = src.match(/^const aTodas = .*$/m)[0];
const aprHtml = corte("function aprHtml(p) {", "\n}\n");
const aprSend = corte("async function aprSend(k) {", "\n}\n");
const casos = JSON.parse(fs.readFileSync(0, "utf8"));
(async () => {
  const out = [];
  for (const c of casos) {
    const p = { fonte: "geral", ficheiro: c.ficheiro, titulo: "t", idade_dias: 0, resumo: "", datas: null, sha256: "s1",
      decisoes: c.ids.map((id) => ({ id, texto: "x", aberta: true })) };
    const u = { open: true, notes: {}, sent: {}, busy: false, err: null, confirm: { accao: "aprovar", id: "*todas", sha: "s1" } };
    const enviados = [];
    const env = { esc: (s) => String(s), md: () => "", aKey: () => "k", aUi: () => u, aIdade: () => "",
      aOpen: (uu, pp) => pp.decisoes.filter((d) => d.aberta), aDecHtml: () => "", aCf: () => "", aFind: () => p,
      renderApr: () => {}, tick: () => {}, state: { token: "t" },
      fetch: async (url, o) => { enviados.push(JSON.parse(o.body).id); return { status: 202, json: async () => ({}) }; } };
    const f = new Function(...Object.keys(env), `${todas}\n${aprHtml}\n${aprSend}\nreturn { aTodas, aprHtml, aprSend };`)(...Object.values(env));
    u.confirm = null;
    const html = f.aprHtml(p);
    u.confirm = { accao: "aprovar", id: "*todas", sha: "s1" };
    await f.aprSend("k");
    out.push({ todas: f.aTodas(p, c.ids.length), botao: html.includes('data-a="todas"'), enviados, confirm: u.confirm });
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


@unittest.skipUnless(shutil.which("node"), "sem node")
class AprovarTodasTest(unittest.TestCase):
    def correr(self, casos):
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "todas.js"
            js.write_text(JS)
            r = subprocess.run(["node", str(js), str(HTML)], input=json.dumps(casos), capture_output=True, text=True,
                               timeout=30, check=True)
        return json.loads(r.stdout)

    def test_pedido_de_accoes_sem_aprovar_todas(self):
        accoes, rotina, um, forjado = self.correr([
            {"ficheiro": "accoes-loja-20261118-1.md", "ids": ["A1", "A2", "A3"]},
            {"ficheiro": "2026-11-18-campanha.md", "ids": ["P1", "P2"]},
            {"ficheiro": "2026-11-18-um.md", "ids": ["P1"]},
            {"ficheiro": "accoes-x.md", "ids": ["P1", "P2"]}])   # D8: um forjado com o prefixo só perde o botão
        self.assertEqual(accoes, {"todas": False, "botao": False, "enviados": [], "confirm": None})
        self.assertEqual(forjado, {"todas": False, "botao": False, "enviados": [], "confirm": None})
        self.assertEqual(rotina, {"todas": True, "botao": True, "enviados": ["P1", "P2"], "confirm": None})
        self.assertEqual((um["todas"], um["botao"]), (False, False))

    def test_handler_do_clique_usa_a_mesma_regra(self):
        src = HTML.read_text(encoding="utf-8")
        linha = next(ln for ln in src.splitlines() if 'else if (a === "todas")' in ln)
        self.assertIn("aTodas(p, aOpen(u, p).length)", linha)
        self.assertIn('if (aTodas(p, n)) h += `<div class="dec"><div class="btns"><button type="button" class="ok-b" '
                      'data-a="todas"', src)


if __name__ == "__main__":
    unittest.main()
