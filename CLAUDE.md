# Squad Office Lite — notas para quem desenvolve

Produto instalável numa VPS ou num PC com Docker Desktop (modo Docker; ver README). Decisões em `DECISIONS.md`
(ADR-001…008). PT-PT em código, UI e docs.

## Stack
- `server.py` + módulos (`quadro.py`, `lancar.py`, `aprovacoes.py`, `biblioteca.py`, `push.py`, `progresso.py`,
  `reader.py`): Python 3.13 stdlib no contentor; Pillow e `cryptography` opcionais (só na imagem).
- `static/`: HTML/JS/CSS sem build; funções puras exportadas e testadas em node.
- `officecfg.py`: lê e valida `office.yaml` (subconjunto de YAML), partilhado por servidor e runner.
- `runner/`: corre no host (Python ≥ 3.10, só stdlib), timer systemd de utilizador; `gerar_compose.py` gera o compose
  (`--modo vps`, omissão, ou `--modo docker`). No modo Docker corre no contentor `runner` (`Dockerfile.runner`) pelo
  `office_runner.py ciclo`; `configurar.py` é a configuração por perguntas desse modo (serviço `configurar`).
- `install.sh` (bash, `shellcheck`), `Caddyfile`, `Dockerfile`; modo Docker: `Dockerfile.runner`, `Caddyfile.docker`,
  `compose.example.yaml` e `env.example`.

## Testes
```bash
python -m pytest tests -q                     # suite completa: ≈ 2,5 min (≈ 150 s; medido a 16/11/2026, 4 núcleos)
python -m pytest tests/test_biblioteca.py -q  # um ficheiro: ≈ 2–25 s (o test_biblioteca é dos maiores: ≈ 24 s)
node --test tests/*.mjs                       # ≈ 1 s
```
A suite completa passa os 2 min do timeout por omissão do Bash no Claude Code: correr com `timeout` maior (ex.:
600000 ms) ou durante o trabalho só os ficheiros tocados. Com 3 suites em paralelo leva o mesmo (≈ 150–155 s cada): o
tempo é sobretudo espera (servidores HTTP e subprocessos), não CPU.
Testes de desempenho (`test_r1`/`r2`/`r3`) medem CPU da thread (`tests/tempo.py`), não o relógio; datas e «hoje» vêm
de um `NOW` fixo passado ao código (`build_state(NOW)`), nunca do relógio a meio do teste.
Os testes nunca usam a `~` real nem chamam o `claude` verdadeiro (`tests/bin/claude` é um falso). Docker, Caddy,
systemd e o `install.sh` só correm na CI (job de fumo), não localmente.
`docker-compose.example.yml` tem de ser igual à saída do gerador (`tests/test_compose.py`) e `compose.example.yaml` à
de `gerar_compose.py --modo docker` (`tests/test_compose_docker.py`).

## Regras
- **Sem constantes no código:** squads, projectos, marca, fuso, colunas, fontes e caminhos vêm todos do
  `office.yaml` (via `configurar(cfg)` em cada módulo). Nenhum nome, caminho ou domínio de uma instalação concreta
  no repo: a CI faz um `git grep` que falha se aparecerem.
- **CI sem deploy:** a CI testa e faz a instalação de fumo; nunca faz deploy e não usa segredos. A CI dos PRs nunca
  publica; a única excepção é o workflow manual de imagens (`imagens.yml`) do repositório público, que publica as
  imagens no GHCR com o token do próprio workflow (ADR-008).
- **Contratos v1** (fila de decisões, `cartoes.json`, pedidos de lançamento, push; ver README «Referência») não mudam
  sem `"v"` novo e o runner actualizado para ler as duas versões. Mudar o formato do `office.yaml` exige `versao: 2`.
- Briefs, cartões e pedidos são dados do utilizador: nunca em argv, nunca por shell, sempre delimitados no prompt.
- Novas chaves no `office.yaml`: documentar na tabela do README e no `office.example.yaml`, com testes de limites.
- Commits pequenos e convencionais; actualizar o `CHANGELOG.md` em cada versão.
