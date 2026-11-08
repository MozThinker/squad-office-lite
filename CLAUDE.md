# Squad Office Lite — notas para quem desenvolve

Produto instalável numa VPS (ver README). Decisões em `DECISIONS.md` (ADR-001…006). PT-PT em código, UI e docs.

## Stack
- `server.py` + módulos (`quadro.py`, `lancar.py`, `aprovacoes.py`, `biblioteca.py`, `push.py`, `progresso.py`,
  `reader.py`): Python 3.13 stdlib no contentor; Pillow e `cryptography` opcionais (só na imagem).
- `static/`: HTML/JS/CSS sem build; funções puras exportadas e testadas em node.
- `officecfg.py`: lê e valida `office.yaml` (subconjunto de YAML), partilhado por servidor e runner.
- `runner/`: corre no host (Python ≥ 3.10, só stdlib), timer systemd de utilizador; `gerar_compose.py` gera o compose.
- `install.sh` (bash, `shellcheck`), `Caddyfile`, `Dockerfile`.

## Testes
```bash
python -m pytest tests -q
node --test tests/*.mjs
```
Os testes nunca usam a `~` real nem chamam o `claude` verdadeiro (`tests/bin/claude` é um falso). Docker, Caddy,
systemd e o `install.sh` só correm na CI (job de fumo), não localmente.
`docker-compose.example.yml` tem de ser igual à saída do gerador (`tests/test_compose.py`).

## Regras
- **Sem constantes no código:** squads, projectos, marca, fuso, colunas, fontes e caminhos vêm todos do
  `office.yaml` (via `configurar(cfg)` em cada módulo). Nenhum nome, caminho ou domínio de uma instalação concreta
  no repo: a CI faz um `git grep` que falha se aparecerem.
- **CI sem deploy:** a CI testa e faz a instalação de fumo; nunca faz deploy, nunca publica imagens em registries e
  não usa segredos.
- **Contratos v1** (fila de decisões, `cartoes.json`, pedidos de lançamento, push; ver README «Referência») não mudam
  sem `"v"` novo e o runner actualizado para ler as duas versões. Mudar o formato do `office.yaml` exige `versao: 2`.
- Briefs, cartões e pedidos são dados do utilizador: nunca em argv, nunca por shell, sempre delimitados no prompt.
- Novas chaves no `office.yaml`: documentar na tabela do README e no `office.example.yaml`, com testes de limites.
- Commits pequenos e convencionais; actualizar o `CHANGELOG.md` em cada versão.
