"""Configs de teste (office.yaml já validado) e troca da config do servidor.

- EXEMPLO: office.example.yaml (uma squad dev, projecto exemplo).
- MULTI: dev + ops (worktree) + marketing (sessao), projectos loja e blog.
- LEGADO: a forma dos testes antigos (dev worktree; marketing, rnd e support sessao; projectos e fontes da
  biblioteca com os nomes que os testes usam). Os módulos de teste que a querem por omissão declaram
  `OFFICE_CFG = cfg.LEGADO` (o tests/conftest.py aplica-a antes de cada teste e repõe a do arranque no fim).
"""
import officecfg
import server


def ler(texto: str) -> dict:
    return officecfg.validar(officecfg.ler_yaml(texto))


EXEMPLO = officecfg.exemplo()

MULTI = ler("""
utilizador:
  nome: Rita
fuso: Europe/Lisbon
squads:
  - id: dev
    nome: Dev
  - id: ops
    nome: Ops
    lead: chefe-ops
    tipos: [infra, fix]
    modos: [task]
  - id: marketing
    nome: Marketing
    lead: cmo
    formato: sessao
projectos:
  - id: loja
    nome: Loja Online
    glifo: "▣"
  - id: blog
    nome: Blog
biblioteca:
  - id: material
    rotulo: Material
    pasta: material
    media: true
""")

# só ops lançável e só loja: prova que nada depende de dev/exemplo
OPS = ler("""
utilizador:
  nome: Rita
squads:
  - id: ops
    nome: Ops
    lead: chefe-ops
    tipos: [infra]
    modos: [task]
projectos:
  - id: loja
    nome: Loja
""")

LEGADO = officecfg.validar({
    "utilizador": {"nome": "Ana"}, "fuso": "Europe/Amsterdam",
    "squads": [{"id": "dev", "nome": "Dev", "lead": "tech-lead"},
               {"id": "marketing", "nome": "Marketing", "lead": "cmo", "formato": "sessao"},
               {"id": "rnd", "nome": "R&D", "lead": "lider-rnd", "formato": "sessao"},
               {"id": "support", "nome": "Support", "lead": "sre", "formato": "sessao"}],
    "projectos": [{"id": i, "nome": n, "glifo": g} for i, n, g in (
        ("loja", "Loja", "◴"), ("projeto90", "Projeto 90", "⚡"), ("ebook", "E-book", "▤"),
        ("taalfiets", "Taalfiets", "↻"), ("menuqr", "MenuQR", "▣"), ("marketing", "Marketing", "◆"),
        ("joga", "Joga", "◉"), ("rnd", "R&D", "⚗"), ("blog", "Blog", "▦"), ("tictag", "TicTag", "◈"),
        ("support", "Support", "☎"), ("fabrica", "Fábrica", "⚙"))],
    "biblioteca": [{"id": i, "rotulo": i, "pasta": i, "media": i == "rascunhos", **({"tipo": t} if t else {})}
                   for i, t in (("rascunhos", None), ("relatorios", "relatorios"), ("mkt-knowledge", "conhecimento"),
                                ("rnd-estudos", "estudos"), ("rnd-radar", "estudos"),
                                ("rnd-knowledge", "conhecimento"), ("propostas", "planos"),
                                ("ebook-ai-factory", "conhecimento"))]})


def repor() -> None:
    server.configurar(server.CONFIG, server.AVISOS[0] if server.AVISOS else None)


def usar(tc, cfg: dict) -> None:
    """No setUp: troca a config do servidor (e de todos os módulos) e repõe a do arranque no fim do teste."""
    server.configurar(cfg)
    tc.addCleanup(repor)
