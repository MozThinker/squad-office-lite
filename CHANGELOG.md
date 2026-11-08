# Changelog

## Lite 0.1.3 (2026-11-08)

Segurança barata, erros com saída e primeira tarefa sem jargão. Os contratos v1 (decisões, `cartoes.json`, pedidos
de lançamento, push) e o formato do `office.yaml` (`versao: 1`) não mudam. Endereços fixados passam a
`MozThinker/squad-office-lite/v0.1.3`. As propostas de CI ficam em `docs/propostas-ci/` (fora da árvore pública) e
são activadas em `.github/` depois do merge, por quem tem permissão.

### Security

- **Imagens fixadas por digest** (índice multi-arquitectura): `python:3.13-alpine` no `Dockerfile`,
  `caddy:2.11.9-alpine` no compose gerado (era `caddy:2.10-alpine`) e `httpd:2.4-alpine` no `install.sh`. As
  dependências opcionais da imagem passam para `requirements.txt` (mesmas versões da 0.1.2).
- **Cabeçalhos de segurança** no Caddyfile e nas labels do Traefik: `X-Frame-Options: DENY`,
  `Referrer-Policy: same-origin`, `Strict-Transport-Security` (1 ano) só com domínio (no Caddy só em HTTPS) e, só no
  Caddy, `Content-Security-Policy: frame-ancestors 'none'` (no Traefik substituiria a CSP `sandbox` da Biblioteca).
  O 401 do basic auth também os leva: no Traefik o middleware `office-lite-cab` é o primeiro da cadeia.
- **CI** (activada depois do merge; era `docs/propostas-ci/ci.yml`): actions fixadas por SHA, gitleaks por digest, `shellcheck` também
  do `get.sh` e dos `scripts/*.sh`, e no fumo um `curl -sI` que confirma os cabeçalhos e a ausência de HSTS sem domínio.
- **Dependabot** (activado depois do merge; era `docs/propostas-ci/dependabot.yml`): `github-actions`, `docker` e `pip`, semanal. Caddy
  e httpd continuam à mão.
- **Proposta de release atestada** (`docs/propostas-ci/release.yml`, só para o repositório público): constrói o
  tarball e o `SHA256SUMS` com `scripts/release.sh --publica`, atesta-os com `actions/attest-build-provenance` e só
  os anexa a uma release em rascunho; publicar continua manual. Verificação opcional:
  `gh attestation verify squad-office-lite-X.Y.Z.tar.gz --repo MozThinker/squad-office-lite` (README e `get.sh`).
- **`scripts/preparar-publico.sh --publica`** e **`scripts/release.sh --publica`**: para correr na árvore pública,
  sem a lista de nomes (recusado na árvore de desenvolvimento); mantém a verificação de caminhos pessoais.
- **`runner-estado/` negada ao `Edit`** do agente (como as filas).

### Erros com saída

- **O cartão mostra o resultado da tarefa** (mascarado), também nos cartões `dev` do Quadro (`detalhe.resultado`) e
  no escritório (`tasks[].resultado` no `/api/state`). Nos erros comuns, a frase vem com o comando: credenciais do
  Claude ⇒ `./install.sh --credenciais`; «Terminou sem PR» ⇒ `gh auth status`; `runner.settings` inválido ⇒ README.
- **`office_runner.py doctor`:** uma linha `✓`/`✗ … → <comando>` por verificação (`office.yaml`, Docker e grupo,
  *linger*, timer, `claude` no PATH, credencial só pelo formato e modo, sessão do `gh`, DNS, portas 80 e 443, versão);
  exit 0/1; funciona com o `office.yaml` inválido.
- **`estado.json` do runner** (`<dados>/runner-estado/`, gravado a cada passagem, só valores de uma lista conhecida e
  sem segredos), montado só de leitura no contentor (`RUNNER_ESTADO_DIR`). O `/api/state` ganha `runner` e
  `tarefa_entregue`; o `/healthz` não muda. `officecfg.VERSAO` é a única fonte da versão.

### Primeira tarefa sem jargão

- **«Primeiros passos»** no escritório até à primeira tarefa entregue (runner, `claude`, credencial, `gh`, com ✓/✗ e
  o comando que corrige) e a faixa «O runner não dá sinal desde HH:MM» (mais de 3 min sem passagem).
- **Diálogo «Lançar»:** «Tipo: funcionalidade nova / correcção», «Tamanho: tarefa curta (até 3 h) / objectivo com
  plano (até 8 h)», «O que queres? Escreve como a um colega.»; o slug é gerado e fica em «Avançado». Os valores
  enviados à API não mudam. Cartão novo com o único projecto lançável já escolhido. «a lançar…» passa, ao fim de
  3 min, a «O runner ainda não pegou nisto» com «Tentar de novo» (só relê). No telemóvel: dica de arrastar na 1.ª vez
  e «Lançar…» no menu ⋯.
- **Rótulos de estado na UI:** «PR aberto em rascunho» e «Terminou sem PR» em vez de `parcial (PR draft)` e
  `incompleta` (os dados não mudam).
- **Acessibilidade:** `lang="pt-PT"`, contraste do nome das secretárias livres e foco mais visível.
- **`runner.ferramentas` por omissão** ganha `TaskCreate`, `TaskGet`, `TaskList` e `TaskUpdate`.

### Instalador e documentação

- **Pré-voo do `gh`:** sem sessão avisa («as tarefas acabam sem PR») e continua.
- **Palavra-passe:** se as duas não coincidem, pede de novo (até 3 vezes).
- **Menu das credenciais:** «1) chave de API (recomendada; paga por uso) · 2) token da subscrição (escreves tu no
  ficheiro; o instalador mostra como) · 3) login já feito no claude (expira; só para experimentar)». A opção 2 mostra
  o `ssh -t <utilizador>@<vps> claude setup-token` já preenchido.
- **DNS:** com domínio, antes do «Pronto», avisa se não resolve ou se aponta para outro IP.
- **`get.sh`:** como root, mostra os 4 comandos de «VPS nova só com root» antes do erro; cabeçalho com o endereço
  público e a verificação opcional da atestação.
- **README:** só VPS Linux (Windows/macOS: pelo browser), endereços da 0.1.3, verificação da origem, `doctor` à cabeça
  de «O que fazer se…», «Backups» (`~/office-lite/`, com `push/`, e o volume `caddy_data`), «Desinstalar»,
  `estado.json`, cabeçalhos e digests.

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
