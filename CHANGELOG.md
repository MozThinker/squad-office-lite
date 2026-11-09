# Changelog

## Lite 0.1.4 (2026-11-09)

Modo Docker para Windows, macOS e Linux (Docker Desktop). O modo VPS, os contratos v1 e o `office.yaml`
(`versao: 1`) não mudam. Endereços fixados passam a `MozThinker/squad-office-lite/v0.1.4`. As propostas de CI ficam em
`docs/propostas-ci/` (fora da árvore pública) e são activadas em `.github/` por quem tem permissão (ADR-008).

### Added

- **Modo Docker (no PC):** o Office, o Caddy e o runner em contentores no Docker Desktop, com um ficheiro
  (`compose.yaml` da release) e três comandos: `docker compose run --rm configurar`, `docker compose up -d` e
  `http://localhost:8080`. README, «No teu PC (Windows, macOS, Linux) com Docker Desktop»: requisitos do Docker
  Desktop (páginas oficiais consultadas a 8/11/2026), Compose ≥ 2.23.1, `.env` opcional a partir do `env.example`
  (`OFFICE_ENDERECO`, `OFFICE_PORTA`, `OFFICE_PROJECTOS`, `OFFICE_UID`/`OFFICE_GID`), editar o `office.yaml` e pôr
  ficheiros na Biblioteca com `docker compose cp`, actualizar, desinstalar e limitações.
- **`Dockerfile.runner`:** imagem do runner sobre `python:3.13-slim-trixie` fixada por digest, com `git`, `gh`
  2.103.3 e `claude` 2.1.311 (canal stable) por download directo com sha256 por arquitectura (amd64, arm64);
  utilizador `office` 10001:10001, sem `sudo`; comandos `ciclo` (omissão), `doctor`, `parar` e `configurar`.
- **`office_runner.py ciclo [--intervalo S] [--passagens N]`:** passagens a cada 60 s sem systemd; relê o
  `office.yaml` em cada volta, uma excepção não pára o ciclo, recolhe os filhos terminados (sem *zombies*) e
  `SIGTERM`/`SIGINT` acabam-no no fim da passagem em curso.
- **`runner/configurar.py`** (serviço `configurar`): pergunta, só o que falta, utilizador e palavra-passe do Office
  (hash bcrypt), projectos (repositórios git da pasta de projectos, ou o `exemplo`), nome e e-mail dos commits,
  credencial do Claude (1 chave de API, 2 token da subscrição colado pelo leitor, 3 agora não) e token do `gh` (por
  stdin ao `gh auth login --with-token`). `--palavra-passe`, `--projectos`, `--credenciais` e `--gh` refazem só essa
  parte; `--sem-perguntas` lê o ambiente (CI).
- **`gerar_compose.py --modo docker`** e **`compose.example.yaml`** (= saída do gerador): volumes nomeados, redes
  `office` e `runner`, só o Caddy com porta, Caddyfile em `configs` (`Caddyfile.docker`) com os segredos lidos por
  `{file./proxy/…}`. O `--modo vps` (omissão) dá os mesmos bytes de antes.
- **`doctor` no modo Docker** (`docker compose exec runner doctor`): `office.yaml`, `claude`, credencial, `gh`, cada
  projecto em `/projectos` (git e gravável), ciclo vivo (`estado.json` < 3 min); cada ✗ com o comando
  `docker compose …`.
- **Office:** `GET /api/config` ganha `"modo": "vps"|"docker"`; os «Primeiros passos» e a faixa «O runner não dá
  sinal» mostram os comandos `docker compose …` no modo Docker.
- **Propostas de CI:** job `fumo-docker` em `docs/propostas-ci/ci.yml` (constrói as duas imagens localmente, nada
  publicado) e `docs/propostas-ci/imagens.yml` (só no repositório público, à mão: imagens amd64+arm64 no GHCR,
  atestadas, e `compose.yaml` fixado por digest e `env.example` atestados e anexados à release em rascunho).

### Security

- **A pasta dos projectos só muda de dono se for de root e estiver vazia** (criada pelo Docker): uma pasta do host
  com conteúdo (ex.: `/srv`) apontada por `OFFICE_PROJECTOS` nunca leva `chown`.
- **Nada com privilégios executa o que a squad escreve no `runner-home`:** os comandos da imagem correm com
  `python3 -I` e `PYTHONNOUSERSITE=1`; os `git`/`gh` do `configurar` correm sem a config global do utilizador
  (`GIT_CONFIG_GLOBAL=/dev/null`, `core.hooksPath=/dev/null`, `init --template=`) e recusam symlinks em `~/.gitconfig`
  e `~/.config/gh`. Editar o `office.yaml` no PC passa por `configurar --office-yaml`, que larga os privilégios antes
  de ler e validar o ficheiro.
- **Segredo do proxy por ficheiro:** com `OFFICE_PROXY_SEGREDO_FILE` (modo Docker), o Office lê o segredo do volume
  `proxy` e, enquanto falta, responde 503 «Falta configurar: corre `docker compose run --rm configurar`» a tudo
  excepto `/healthz`: nunca fica aberto.
- **Isolamento do modo Docker:** o runner e o agente não vêem a casa do utilizador, o `~/.ssh` nem as credenciais do
  Office (volume `proxy` só no Caddy e no Office); `office.yaml` e `claude.env` só de leitura no runner; sem socket do
  Docker, `privileged` nem `network_mode: host`; o runner fora da rede do Office. O `configurar` corre como
  root só para o `chown` das raízes dos volumes e larga os privilégios. Segredos nunca em argv, ambiente de filhos nem
  saída.
- **Capacidades mínimas no modo Docker:** `no-new-privileges` e `cap_drop: [ALL]` nos quatro serviços; o Office e
  o runner sem nenhuma; `cap_add` só no `configurar` (`CHOWN`, `DAC_OVERRIDE`, `SETGID`, `SETUID`: dono das raízes
  dos volumes e largar o root) e no Caddy (`NET_BIND_SERVICE`, file capability do binário da imagem oficial, e
  `DAC_OVERRIDE`, para ler o volume `proxy` sendo root), cada uma justificada num comentário do compose.
- **`DISABLE_AUTOUPDATER=1`** no ambiente do `claude` no modo Docker (versão fixada na imagem); no modo VPS a lista
  branca não muda. `claude.env`/`runner.env` lêem-se de `OFFICE_SEGREDOS_DIR` quando definido, com as mesmas regras e
  as negações por omissão nesse caminho.
- **Sandbox do Claude Code não suportado no contentor:** o isolamento é o contentor, como no contentor de referência
  da Anthropic ([Development containers](https://code.claude.com/docs/en/devcontainer) e
  [Sandboxing](https://code.claude.com/docs/en/sandboxing), consultadas a 8/11/2026). README, «Segurança», com o
  parágrafo do modo Docker.

### Fixed

- **Modo Docker com `OFFICE_UID`/`OFFICE_GID` diferentes de 10001** (teste numa VPS Linux): o runner falhava
  (`Permission denied` em `/dados/tasks`, `PermissionError` em `/dados/runner/.lock`) e o Office respondia 503 «sem
  pasta do quadro». As pastas da imagem do runner eram `0750` de 10001 e o Docker voltava a copiar para cada volume
  vazio o dono do ponto de montagem da imagem a cada arranque, desfazendo o `chown` do `configurar` (observado;
  mecanismo em [Populate a volume using a container](https://docs.docker.com/engine/storage/volumes/#populate-a-volume-using-a-container),
  consultada a 8/11/2026). Agora todos os volumes, salvo `caddy-data`/`caddy-config`, montam-se com `nocopy`
  ([`nocopy`](https://docs.docker.com/reference/compose-file/services/#volumes), consultada a 8/11/2026), a imagem
  do runner tem as pastas-pai e os pontos de montagem de root `0755`, e o `configurar` dá o dono (como root) e o modo
  (já sem root: `0700` em `proxy`, `segredos`, `push` e na casa, `0750` nas outras) às raízes. O `doctor` mostra
  `✗ pastas de dados graváveis…` e o ciclo sugere `docker compose run --rm configurar` quando falta escrita.
- **Nome do projecto do compose do PC:** `office-lite-pc` (o VPS continua `office-lite`); os dois modos podem
  coexistir no mesmo Linux.

### Changed

- **Publicação de imagens (ADR-008):** a CI dos PRs continua sem publicar; a única excepção é o workflow manual
  `imagens.yml` do repositório público (desvio ao ADR-006/007, registado no `CLAUDE.md`).
- **`scripts/preparar-publico.sh`:** a árvore pública deixa de levar o `.github/dependabot.yml` e aceita
  `/home/office` (casa do utilizador da imagem) nos exemplos.
- **README:** o Lite instala-se numa VPS ou no PC (a frase «No Windows ou no macOS não se instala» sai);
  «Referência · Runner» descreve o `ciclo`; endereços da 0.1.4 (também no `get.sh` e no `SECURITY.md`).
- **Licença do Docker Desktop** (README, «Limitações do modo Docker»): grátis para uso pessoal, educação, open source
  não comercial e empresas com menos de 250 pessoas **e** menos de 10 milhões de USD de receita anual
  ([Docker Desktop license agreement](https://docs.docker.com/subscription/desktop-license/), consultada a
  8/11/2026).
- `DECISIONS.md`: ADR-008.

### Não verificado nesta versão

- Nada de Docker correu nos testes locais: o fumo do modo Docker (Linux amd64) fica proposto. Windows 11, macOS
  arm64 e as imagens arm64 verificam-se à mão antes da release pública.

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
