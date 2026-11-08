# Changelog

## Lite 0.1.2 (2026-11-08)

Credenciais e travões: o Lite deixa de recolher o token da subscrição e, por omissão, não deixa o agente ler segredos
nem escrever nas filas com as ferramentas do Claude Code. Primeira versão preparada para distribuição pública.

- **Instalação com o endereço fixado à versão** (`…/v0.1.2/get.sh` com `--versao 0.1.2`): o README mostra primeiro a
  forma prudente (descarregar, ler, correr) e depois o comando de uma linha, com um aviso sobre correr scripts da internet.
- **O instalador não pede nem grava o token da subscrição.** A opção 2 e `OFFICE_CLAUDE_CRED=oauth` só explicam como
  escrever tu o `claude.env` ou o `runner.env` (0600) e seguem como «nenhuma»; com `oauth`, o
  `OFFICE_CLAUDE_CRED_FILE` é ignorado (com aviso). A chave de API continua a ser pedida. O runner continua a aceitar
  um `claude.env` escrito à mão com `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-…`.
- **Negações por omissão:** o runner passa sempre `--settings` com um JSON (num ficheiro 0600 em
  `<dados>/runner/settings/`, não no argv) que nega `Read` de `claude.env`, `runner.env`, `.env` da casa, `**/.env`,
  `**/.env.*`, `~/.config/gh/**`, `~/.ssh/**`, `~/.claude/.credentials.json`, `~/.git-credentials`, `~/.netrc`,
  `~/.docker/config.json`, `~/.config/systemd/**` e `/proc/**`, e `Edit` dos três da casa, dos cinco de `~` acima
  (credenciais e `~/.config/systemd/**`), do `office.yaml`, das pastas `quadro-lancar/`, `decisoes-fila/`,
  `decisoes/`, `quadro/` e `runner/` dos dados, do código do Lite, do ficheiro de `runner.settings`, de
  `~/.claude/settings.json`, `~/.claude/settings.local.json`, `~/.bashrc`, `~/.profile`, `~/.bash_profile` e
  `~/.local/bin/**`; e liga `blockWritesOutsideWorkingDirectories`. O
  `runner.settings` deixa de substituir e passa a juntar-se à base (listas `deny`/`allow`/`ask` concatenadas); inválido
  ⇒ a tarefa fica em «erro» com «runner.settings inválido: …». Nota: `**/.env.*` também esconde o `.env.example` dos
  projectos.
- **Lista de ferramentas por omissão sem `WebSearch`, `WebFetch`, `Bash(cat *)`, `Bash(head *)`, `Bash(tail *)`,
  `Bash(cp *)`, `Bash(mv *)` e `Bash(echo *)`** (Read/Grep/Write/Edit fazem o mesmo). Para as repor, define
  `runner.ferramentas` com a lista completa que queres (a chave substitui a omissão).
- **Ambiente do `claude` em lista branca:** só `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `USER`, `LOGNAME`,
  `SHELL`, `TERM`, `TMPDIR`, `TZ`, `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` e uma credencial (a do `claude.env`; sem
  ele, `ANTHROPIC_API_KEY` ou `CLAUDE_CODE_OAUTH_TOKEN` do `runner.env`, por esta ordem). O runner deixa de passar
  `OFFICE_*`, `GH_TOKEN`, `GITHUB_TOKEN`, proxies e `NODE_OPTIONS` ao `claude` e não deixa ler o seu próprio ambiente
  em `/proc`. Os segredos do `runner.env` saem do ambiente do runner no arranque: os filhos `git`, `gh` e `claude` só
  recebem a lista branca (o `gh` também o `GH_TOKEN`/`GITHUB_TOKEN`, se lá estiver) e o `correr` destacado recebe-os
  por um pipe no stdin. As `settings/<id>.json` são apagadas quando a tarefa fecha (e pela rotação).
- **Aviso quando o `office.yaml` muda:** linha no journal e mensagem no Telegram (se configurado), uma vez por
  mudança; o sha256 fica em `<dados>/runner/office.yaml.sha256`.
- **Parar uma tarefa:** `office_runner.py parar <id>` mata o `claude` da tarefa, fecha-a em «erro» com «parada pelo
  utilizador» e o runner nunca a retoma; funciona com o `office.yaml` inválido. Um SIGTERM sem `parar` (reboot) deixa
  a tarefa para retomar (com o `session_id` já gravado), salvo se o `claude` já tinha terminado: aí fecha-a com o
  resultado. Substitui o `pkill` da 0.1.1.
- **Teto e modelo por tarefa:** `runner.max_usd` (`--max-budget-usd`, por arranque do `claude`) e `runner.modelo`
  (`--model`), opcionais. O formato continua `versao: 1`.
- **Prompt coerente com a lista:** «marca-o pronto (`gh pr ready`)» só quando `gh pr ready` está autorizado (ou com
  `auto`/`bypassPermissions`); senão a squad deixa o PR em rascunho.
- **Barra de progresso:** a fase CODE acende com `engineer` e qualquer `*-engineer` menos `test-engineer` (como no
  browser).
- **`install.sh`** procura o `claude` em `~/.local/bin` quando não está no PATH (sessão SSH sem terminal) e põe essa
  pasta no PATH da unit do runner. Sem `claude.env` e com `ANTHROPIC_API_KEY=` ou `CLAUDE_CODE_OAUTH_TOKEN=` no
  `runner.env`, não pergunta pelas credenciais (só confirma que a chave existe; o valor não é lido) e exige o
  `runner.env` como o `claude.env`: ficheiro normal, teu, `chmod 600`. Com `--sem-perguntas`, `OFFICE_CLAUDE_CRED`
  não vazio conta como `--credenciais`.
- **`get.sh`:** instalador de um comando (`curl -fsSL …/get.sh | bash`). Verifica Ubuntu, processador (x86_64; arm64
  com aviso) e utilizador não-root; descarrega a última versão das GitHub Releases (ou `--versao X.Y.Z`), confirma o
  sha256 do `SHA256SUMS` (só por https: `--proto =https`), extrai para `~/squad-office-lite` (a anterior fica em `~/squad-office-lite.anterior-<data>`)
  e corre o `install.sh` com as outras opções. `~/office-lite` não é tocada (ADR-007).
- **`scripts/preparar-publico.sh` e `scripts/release.sh`:** árvore pública sem histórico nem `docs/`, com verificação
  de nomes internos (também na forma `nom[e]` e nos binários) e de caminhos pessoais (`/home/<x>` e `/Users/<x>`,
  salvo os exemplos); a lista de nomes vem do `ci.yml` e sai da cópia pública dele. Tarball e `SHA256SUMS` em
  `dist/`. Não publicam nada.
- **Licença MIT** (`LICENSE`) e **`SECURITY.md`** (reporte privado pelo GitHub; correcções de segurança durante 12
  meses a contar da compra do livro).
- **`office.example.yaml`:** `colunas.a-espera`, `runner.max_usd` e `runner.modelo` comentados.
- **README:** instalação com `get.sh` (e a forma prudente), «Actualizar», requisitos (arquitectura, memória), tempo
  medido da instalação (19 s sem perguntas), token da subscrição escrito por ti, negações e os seus limites, sandbox opcional, lista
  branca do ambiente, limites dos proibidos (proteger o ramo no GitHub), risco das filas e de `~/.claude/projects`,
  excepção do `sha256` nas aprovações, `parar`, «Contribuir», «Segurança» (com os riscos que ficam) e «Licença».

## Lite 0.1.1 (2026-11-08)

Correcções do primeiro teste numa VPS real.

- **Prompt com «Permissões»:** cada tarefa diz à squad o que pode correr, gerado de `runner.ferramentas` e do
  `permission_mode` (um comando por chamada, ficheiros só com Write/Edit, sem redirecções, heredocs, `&&` nem pipes;
  tentar a forma permitida e nunca concluir que «o Bash está todo bloqueado» por uma recusa; tentar mesmo
  `git commit`, `git push` e `gh pr create`). Com `ferramentas: []` não promete comandos; com `auto` e
  `bypassPermissions` só lembra os proibidos. Proibidos e delimitação do pedido iguais.
- **Formato das credenciais do Claude** no instalador e no runner (mesmos padrões): o token da subscrição tem de ser
  `sk-ant-oat01-…` («copia a linha que começa por sk-ant-oat01-, não o código do browser»); a chave de API tem de
  começar por `sk-ant-` e recusa um token da subscrição («escolhe a opção 2»). Um `claude.env` fora do formato deixa a
  tarefa em «erro» com a regra, nunca o valor.
- **Identidade git no instalador:** pede `user.name`/`user.email` se faltarem (até 3 tentativas) e grava-os com
  `git config --global`, sem nunca trocar os existentes; com `--sem-perguntas` usa `OFFICE_GIT_NOME`/`OFFICE_GIT_EMAIL`
  ou só avisa.
- **`pr_url` na meta da tarefa** (chave nova, só o URL; `pr` não muda de formato). Se o `gh pr list` não achar o PR,
  o runner aceita o último URL de PR do resultado do mesmo repo do `origin` e confirma-o com `gh pr view`; não
  confirmado ⇒ issue «PR por confirmar». O aviso do fim usa `pr` ou, sem ele, `pr_url`.
- **README:** VPS nova só com root, `gh repo clone` de um repo privado, `claude setup-token` na VPS por `ssh -t`, token
  GitHub fine-grained só para os repos do Office, identidade git, como parar uma tarefa (`pkill`; o timer parado não a
  pára e o runner retoma-a até 2 vezes) e quando o cartão muda de coluna (ao abrir o Quadro).
- **CI:** Ubuntu 26.04 na matriz do fumo e passo Python 3.14 (além do 3.10) para o `officecfg` e o runner.

## Lite 0.1.0 (2026-11-06)

Primeira versão do Squad Office Lite, a partir de um Squad Office interno (ADR-006).

- **Config única** `office.yaml` (subconjunto de YAML validado por `officecfg.py`): marca, utilizador, fuso, raízes,
  squads, projectos, colunas, fontes da Biblioteca e runner. O frontend lê-a por `GET /api/config`.
- **Instalador** `install.sh` para Ubuntu: verifica dependências, cria config, `.env` e pastas, gera o compose e arranca
  o Office com Caddy (basic auth bcrypt, TLS com domínio); `--proxy traefik` para um Traefik existente;
  `--palavra-passe` para a trocar. Idempotente.
- **Runner no host** (`runner/`, timer systemd de utilizador): aplica decisões, lança a squad `dev` em `git worktree`
  com `claude -p` a partir do Quadro (tecto diário, paralelo, retomas, travões), arquiva a fila e avisa por Telegram
  (opcional).
- **Credenciais do Claude** em `~/office-lite/claude.env` (0600, só no host): chave de API (recomendada) ou token
  da subscrição (`claude setup-token`), pedidas pelo instalador (`--credenciais` para trocar) e passadas só no ambiente
  do `claude`; o runner recusa o ficheiro com permissões abertas. Runner com `permission_mode` `dontAsk` por omissão e
  `runner.ferramentas` (`--allowedTools`) pré-autorizadas.
- **Compose gerado** a partir da config, contentor com o uid/gid de quem instala; imagem construída localmente.
- Saem as sondas, os crons, os incidentes, o portfólio externo e as salas das outras squads internas.
- Contratos v1 (decisões, `cartoes.json`, pedidos de lançamento, push) inalterados, salvo `pedido.projecto` passar a
  ser o id do projecto.
