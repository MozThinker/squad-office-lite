# Decisões de arquitectura

Os ADR-001…005 vêm do Squad Office em que o Lite se baseia, reescritos para o Lite; o ADR-006 é a passagem a produto
instalável, o ADR-007 a distribuição pública e o ADR-008 o modo Docker (no PC, com imagens publicadas); o ADR-009
é o Docker rootless na VPS; os ADR-010…012 são a Biblioteca rápida e o arquivo dos documentos antigos. O ADR-022 é o Office a ler a saúde do serviço `accoes` (só de leitura). «Proxy» = o Caddy do compose (omissão) ou um Traefik existente (`--proxy traefik`).

## ADR-001 · O Office passa a escrever decisões numa fila JSON (2026-10-31)
**Contexto:** o Office era só leitura; os pedidos de decisão (`.md` com frontmatter e uma secção `## Decisões`) eram
respondidos fora dele, com frases exactas por mensagem.

**Decisão:** `POST /api/decisao` escreve um JSON `"v": 1` por decisão em `DECISOES_FILA_DIR` (mount `rw`). O Office
nunca edita os `.md` nem aplica nada; um aplicador no host (no Lite, o runner: `runner/decisoes.py`) consome a fila,
confirma o `sha256` (só aplica sobre a versão que o utilizador viu), actualiza o `.md` e move o JSON para `feito/`.

**Contrato v1:** chaves exactas `v`, `criado` (ISO com fuso), `fonte`, `ficheiro`, `id`, `accao`
(`aprovar|recusar|comentar`), `nota`, `sha256`, `ip`, `ua`; nome `AAAAMMDDTHHMMSS-<8 hex>.json`; escrita atómica.
Qualquer mudança de formato exige `v` novo e o aplicador actualizado antes.

**Alternativas recusadas:** editar o `.md` no Office (mounts `rw` sobre os pedidos e mistura leitura com aplicação);
continuar só por mensagens (frases exactas no telemóvel, o problema de partida).

**Consequências:** menor superfície de escrita, um ficheiro auditável por clique (com ip/ua); a decisão só tem efeito
quando o aplicador corre.

**Nota · autenticação:** a autenticação é só o basic auth do proxy; o Office não tem auth própria (o token do
`/api/state` só trava CSRF). Risco: um contentor na mesma rede do Office chega-lhe directamente, sem basic auth, e
pode escrever na fila. No Lite com Caddy a rede do compose só tem o Office e o Caddy (ADR-006); com Traefik a rede é
partilhada e o risco volta. Mitigação futura: um cabeçalho secreto injectado pelo proxy e exigido pelo Office.

## ADR-002 · Biblioteca: o Office serve ficheiros das fontes e gera miniaturas num tmpfs (2026-11-01)
**Contexto:** o utilizador não conseguia ver nem descarregar no telemóvel o material das squads (imagens, vídeos,
estudos, specs). Até aqui o Office só devolvia JSON e texto mascarado; nunca ficheiros binários.

**Decisão:** módulo `biblioteca.py` com as fontes da config montadas `:ro` (no Lite, `<dados>/biblioteca/<pasta>`),
mais as tarefas e as aprovações já montadas. Só se serve o que está no índice (allowlist de extensões, denylist de
nomes/pastas, sem symlinks, `O_NOFOLLOW` + `fstat` com dev/ino/size iguais ao índice, 404 único), com tipo fixo pela
extensão, `nosniff`, CSP `sandbox` (excepto PDF) e textos mascarados. Miniaturas com **Pillow** (versão fixa, só na
imagem Docker, import opcional) gravadas num `tmpfs` `/tmp/miniaturas`: o contentor continua `read_only` e esta é uma
escrita volátil, só de derivados. Zip em streaming `ZIP_STORED`, ≤ 300 ficheiros/1 GiB, 2 em simultâneo.

**Alternativas recusadas:** `vipsthumbnail` em subprocesso (processo externo com timeout); só `loading="lazy"` com os
originais (~2 MB por peça no telemóvel); página estática publicada à parte por lote; servir pastas inteiras com
`SimpleHTTPRequestHandler` (sem allowlist nem máscara).

**Consequências:** uma dependência fora da stdlib (Pillow) e o Office a servir binários, atrás só do basic auth do
proxy (a nota do ADR-001 aplica-se). Sem Pillow (local, testes) tudo funciona com os originais. Reverter = tirar os
mounts, o módulo e o `tmpfs`.

## ADR-003 · Quadro: o Office guarda cartões manuais num `cartoes.json` partilhado com agentes (2026-11-04)
**Contexto:** o utilizador quer um kanban com o estado real do trabalho e um sítio para cartões soltos, que o Claude
Code também possa ler e escrever. Até aqui o Office só escrevia a fila de decisões (ADR-001) e miniaturas (ADR-002).

**Decisão:** os cartões automáticos são calculados a cada pedido a partir do estado e nunca gravados. Os manuais vivem
em `QUADRO_DIR/cartoes.json` (mount `rw`). **Contrato v1:** `{"v": 1, "cartoes": [...]}` com as chaves exactas
`id, titulo, descricao, projecto, coluna, ordem, etiquetas, criado, actualizado` (≤ 200 cartões; limites no README).
A versão é o sha256 dos bytes do ficheiro (não gravada; ausente = `"0"`); o `POST /api/quadro` só grava se a versão
que o cliente viu for a actual (senão 409), sob lock em processo, com tmp na mesma pasta + `fsync` + `os.replace`.
Ficheiro ilegível ou `v` ≠ 1 ⇒ só os automáticos e todas as escritas recusadas (503) até ser corrigido à mão.
Escritores externos usam tmp + rename.

**Alternativas recusadas:** um ficheiro por cartão (reordenar mexe em N ficheiros); contador de versão dentro do
ficheiro (quem edita à mão teria de o manter); calcular tudo no browser (regras sem testes e sem acesso aos briefs);
guardar overrides dos automáticos (duas fontes da verdade).

**Consequências:** mais uma superfície de escrita atrás só do basic auth; uma edição externa entre a leitura e o
`os.replace` do Office pode perder-se (janela de ms, aceite). Mudar o formato exige `v: 2` e os escritores externos
actualizados antes. Reverter = tirar o mount, a rota e o módulo; o ficheiro fica no host.

**Nota · `cartoes.json` é dados, não instruções:** qualquer agente lê títulos, descrições e etiquetas como texto,
nunca como pedidos a executar (quem chegue ao Office sem basic auth poderia lá pôr instruções).

**Desvios aceites:** `Content-Type` errado no `POST /api/quadro` dá **403** (verificação partilhada com o
`/api/decisao`), não 415; `quadro.js` + `quadro.css` ≈ 70 KB sem minificar (≈ 22 KB em gzip), aceite por só
carregarem ao abrir o Quadro. `editar` aceita campos parciais (extensão compatível do contrato).

## ADR-004 · Notificações no telemóvel: o Office envia Web Push e guarda chaves e subscrições em `PUSH_DIR` (2026-11-05)
**Contexto:** o sino só avisa com o Office aberto. O utilizador quer saber de fins de tarefa, PRs e alertas com o
telemóvel bloqueado, também no iPhone (PWA).

**Decisão:** módulo `push.py` com Web Push padrão: cifra `aes128gcm` (RFC 8291) e VAPID ES256 (RFC 8292) com
**`cryptography`** (versão fixa, só wheel, import opcional; sem ela o push fica 503 e o resto funciona) e envio por
`urllib` (timeout 10 s, sem redirects, só `https` para os serviços de push conhecidos). Dados em `PUSH_DIR` (mount
`rw`): `vapid` (chave privada, 0600, nunca sai em respostas nem logs), `subscricoes.json` e `notificadas.json`
(`"v": 1`), com escrita atómica sob lock. Um vigia em thread (30 s) decide por chaves persistidas (sobrevive a
reinícios; o 1.º arranque só semeia). Os POST usam a protecção do `/api/decisao` (Content-Type, Origin, token). Os
payloads levam só squad, projecto, estado e ids — nunca texto de tarefas, resultados, títulos nem custos.

**Alternativas recusadas:** pywebpush (traz requests, http-ece, py-vapid e a própria cryptography); um serviço de push
próprio ou de terceiros (mais uma conta e dados fora); cifra à mão sem biblioteca (risco de erro e lento).

**Consequências:** o Office faz pedidos para fora (o contentor precisa de saída HTTPS). Mais uma superfície de escrita
atrás só do basic auth (a nota do ADR-001 aplica-se; ≤ 20 subscrições, 1 teste por 10 s por endpoint). Perder a
pasta = nova chave VAPID e cada dispositivo liga de novo. Mudar o formato exige `v: 2`. Reverter = tirar o mount, as
rotas, a thread e o módulo.

**Nota (2026-11-06) · aviso por fase:** extensão compatível, sem `v: 2`. As prefs ganham `eventos.fases` (omissão
`false`) e o grupo `fases` (`SPEC`, `PLAN`, `CODE`, `QA`, `PR`; omissão só QA e PR); o servidor completa as prefs
antigas ao ler e devolve sempre a forma nova. O vigia acrescenta as chaves `fase:<squad>:<id>:<FASE>` e envia por
dispositivo só a fase mais adiantada que ele quer. Rollback: a versão anterior lê a forma nova como ilegível; tirar
`eventos.fases` e `fases` das prefs à mão (ou apagar `subscricoes.json`).

## ADR-005 · Quadro: o Office escreve pedidos de lançamento numa fila para a squad (2026-11-06)
**Contexto:** o utilizador quer lançar trabalho a partir do Quadro (arrastar um cartão manual para «Em curso» +
confirmação). Até aqui nenhuma escrita do Office punha agentes a trabalhar.

**Decisão:** `POST /api/quadro/lancar` grava um JSON v1 por pedido em `QUADRO_LANCAR_DIR` (mount `rw`) e move o cartão
para «Em curso» sob o lock do `cartoes.json`, numa só chamada. Um programa do host (no Lite, o runner) lê
`^\d{8}T\d{6}-[0-9a-f]{8}\.json$`, lança e responde em `<id>.feito.json` (`{"task_id"}` ou `{"erro"}`); o Office grava
`<id>.movido.json` quando, no `GET /api/quadro`, move o cartão no fim da execução (uma vez só). O Office nunca lança
nada directamente. O estado vive só na fila (o `cartoes.json` v1 não muda). Campos em listas fechadas
(squad/projecto/kind/mode, slug por regex, chaves exactas), nome do ficheiro só do relógio e de
`secrets.token_hex(4)`, escrita tmp + `fsync` + `os.link` (nunca sobrescreve), máx. 10 pedidos pendentes (429).
Contrato no README («Lançar a partir do Quadro»).

**Alternativas recusadas:** o Office lançar o agente directamente (exigiria acesso ao host e ao Docker, contra o
ADR-001); guardar o estado do lançamento no `cartoes.json` (`v: 2`); reutilizar a fila de decisões (mistura contratos
e aplicadores).

**Consequências:** o mesmo modelo do ADR-001, reversível (tirar rota, módulo e mount). O `GET /api/quadro` passa a
poder escrever (movimento do fim + marca), só com fins por marcar e sob o lock. A nota do ADR-001 pesa mais: quem
chegue ao Office sem basic auth poderia pôr agentes a gastar e escrever briefs com instruções; o brief é dados e o
runner trata-o como texto do utilizador, nunca como instruções. Mudar o formato exige `v: 2` e o runner actualizado
antes.
**Desvio aceite:** usa-se `os.link` do tmp em vez de `os.replace`, igualmente atómico e que falha em vez de
sobrescrever.
**Riscos residuais:** abuso de custo (o Office só limita a 10 pendentes; o tecto diário é do runner); `GET /api/quadro`
que escreve; confiança no runner para revalidar o pedido, não pôr `titulo`/`brief` em argv/shell e tratar o brief como
dados. Título e brief com caracteres invisíveis (`Cf`/`Co`/`Cs`, salvo ZWJ; `Cn` não, para não recusar emojis mais
novos que o Unicode do Python) são recusados no `POST`; o pedido mais recente de um cartão escolhe-se por `pedido_em`
com fuso.

## ADR-006 · Lite: config única `office.yaml`, runner no host e instalador com Caddy (2026-11-06)
**Contexto:** o Office só corria no servidor de origem (caminhos, squads, projectos, marca e scripts externos no código
e no compose). O objectivo é um comando que o instale numa VPS Ubuntu limpa, para uma pessoa e uma squad `dev`, sem
nada de fora do repo.

**Decisão:** (1) uma só config `office.yaml` lida por `officecfg.py` (stdlib, subconjunto estrito de YAML documentado,
chaves desconhecidas recusadas), importado pelo servidor (contentor) e pelo runner (host); o servidor cai no
`office.example.yaml` com aviso quando a config falta ou é inválida, o runner e o instalador recusam-na; o frontend lê
`GET /api/config` (sem caminhos nem segredos). (2) Todas as pastas derivam de `raizes.dados`/`claude_projects`/
`projectos`; a instalação vive em `~/office-lite` (`office.yaml`, `.env`, `docker-compose.yml`, `dados/`); o compose
é gerado a partir da config (`runner/gerar_compose.py`), imagem construída localmente, contentor `read_only` com o
uid/gid de quem instalou (`user: "${OFFICE_UID}:${OFFICE_GID}"`, sem `USER` fixo no `Dockerfile`). (3) Runner no host
(`runner/`, stdlib, Python ≥ 3.10) por timer systemd de utilizador (`office-runner.timer`, de minuto a minuto,
`KillMode=process` para não matar o `claude` destacado; precisa de *linger*): cumpre os contratos dos ADR-001 e
ADR-005 (aplica decisões, lança a squad em `git worktree` com `claude -p`, prompt por stdin, tecto diário, paralelo,
retoma até 2 vezes), `permission_mode` configurável com omissão `auto`, `--disallowedTools` fixo e `--settings`
opcional. (4) `install.sh` verifica Docker/`claude`/`gh` sem os instalar, gera `office.yaml` e `.env` só se não
existirem (idempotente), e põe o Caddy no mesmo compose como único serviço com portas (basic auth bcrypt; TLS
automático com domínio, HTTP na 80 com aviso sem ele); Traefik existente só com `--proxy traefik`. Saem sondas,
crons, incidentes e o portfólio externo.

**Alternativas recusadas:** PyYAML vendorizado ou num venv do runner (milhares de linhas de terceiros ou `pip` no
instalador, para 40 linhas de config); TOML/JSON (`tomllib` não existe no Python 3.10 do Ubuntu 22.04; JSON sem
comentários); o Office lançar o `claude` dentro do contentor (exigiria credenciais, git e o Docker socket no
contentor, contra o ADR-001); runner por `crontab` (sem lock nem logs do systemd e mais difícil de desligar); imagem
publicada em registry (o Lite não publica nada); Caddy instalado no host (`sudo`, apt e mais casos de erro);
`permission_mode` `acceptEdits` por omissão (a squad não conseguiria correr git nem testes na 1.ª tarefa).

**Consequências:** o código deixa de ter nomes de squads/projectos; os contratos v1 (decisões, cartões, pedidos, push)
não mudam, salvo `pedido.projecto` passar a ser o id do projecto. O Office fica numa rede de compose só com o Caddy
(atenua a nota do ADR-001; no modo Traefik a nota volta a valer). `auto` depende da versão/conta do `claude` do
utilizador; os travões por `runner.settings` são recomendados no README. Mudar o formato da config exige `versao: 2` e
o `officecfg.py` a ler as duas. Difícil de reverter: o formato do `office.yaml` e o layout de `raizes.dados` passam a
ser dados do utilizador. Reverter o runner/instalador = apagar `runner/`, `install.sh`, `Caddyfile` e as units.

**Nota · segredo do proxy (2026-11-06):** fecha o risco das notas do ADR-001/003 (e o RCE via `POST /api/quadro/lancar`
a partir de outro contentor na rede do Traefik). O `install.sh` gera `OFFICE_PROXY_SEGREDO` (32 bytes aleatórios, só
se faltar no `.env`); o proxy injecta-o em `X-Office-Segredo` depois do basic auth (Caddy `header_up`, Traefik
middleware `headers` encadeado a seguir ao `basicauth`), substituindo o valor do cliente; o Office exige-o
(`hmac.compare_digest`) em todos os pedidos excepto `GET /healthz`, senão 403. Igual nos dois modos; o compose recusa
arrancar sem a variável. Sem ela no ambiente (dev local, testes) o Office não verifica.

**Nota · credenciais do Claude e permissões fechadas por omissão (2026-11-07):** (1) Duas vias de credenciais para o
`claude` do runner, à escolha no instalador: chave de API (`ANTHROPIC_API_KEY`, recomendada) ou token da subscrição
(`CLAUDE_CODE_OAUTH_TOKEN`, de `claude setup-token`), numa linha de `<casa>/claude.env` (ao lado do `office.yaml`).
O runner abre-o com `O_NOFOLLOW` e recusa-o se não for ficheiro regular, não for do utilizador ou tiver `mode & 077`
(a tarefa fica em «erro» com motivo genérico, sem o valor); o valor vai só no ambiente do `claude` (nunca argv, meta,
logs), a outra variável herdada sai; sem ficheiro fica o login do `claude`. O instalador lê o segredo com `read -s`,
escreve-o com `umask 077` e `mv` atómico, e recusa um ficheiro existente com permissões abertas em vez de o corrigir.
O compose nunca o monta (o gerador recusa raízes que o contenham). (2) `permission_mode` passa de `auto` para
`dontAsk` por omissão, com `runner.ferramentas` (`--allowedTools`) a pré-autorizar o necessário para chegar ao PR
(leitura/edição, git, `gh pr create/view/checks/edit/list/ready`, `gh run`, testes e build comuns). Porquê: `auto`
depende da versão/conta e deixa ao modelo decidir o que é seguro; `dontAsk` é fechado (o que não está na lista é
recusado sem perguntar) e funciona sem humano. Os proibidos (`--disallowedTools`) continuam a ganhar. `auto`,
`acceptEdits` e `bypassPermissions` continuam aceites para quem quiser abrir mais (risco documentado no README).
Alternativa recusada: guardar a credencial no `.env` do compose (o contentor passaria a tê-la).

**Nota · permissões explicadas ao agente, formato das credenciais e paragem de tarefas (2026-11-08):** não muda a
nota de 2026-11-07, explica-a. (1) O prompt de cada tarefa ganha uma secção «Permissões» que depende do modo:
`dontAsk`/`acceptEdits`/`default` com lista ⇒ as ferramentas e os comandos do Bash gerados de `runner.ferramentas`
(nunca uma lista fixa: a config é a verdade) e a forma permitida (um comando por chamada, Write/Edit para ficheiros,
tentar de novo antes de desistir); `ferramentas: []` ⇒ sem promessas, regista o que foi recusado; `auto`/
`bypassPermissions` ⇒ só os proibidos. O brief nunca entra nessa secção. (2) Formato das credenciais igual no
instalador e no runner: `sk-ant-oat01-[A-Za-z0-9_-]+` para o token, `sk-ant-[A-Za-z0-9_-]+` sem `sk-ant-oat` para a
chave de API (o prefixo `sk-ant-` e não `sk-ant-api` por compatibilidade com o `sk-ant-fumo-…` da CI). (3) Parar uma
tarefa fica só documentado (`pkill`; retoma até 2 vezes). Alternativa recusada: lançar com `systemd-run` (dependência
do D-Bus de utilizador, muda o pai do processo, as retomas e os testes, por um caso raro).

**Nota · estado do runner (2026-11-08):** o runner grava a cada passagem `<dados>/runner-estado/estado.json`
(`v: 1`; só enumerados, sem segredos, caminhos nem saída de comandos; escrita atómica, 0644). A pasta é mais uma de
`officecfg.SUB`/`MOUNTS`, montada `ro` no contentor (`RUNNER_ESTADO_DIR`), criada pelo instalador e pelo runner e
negada ao `Edit` do agente; fica fora de `<dados>/runner/` para os logs e as settings não entrarem no contentor. O
servidor lê-a com `O_NOFOLLOW`, ≤ 4 KB e chaves exactas (senão `null`). `/api/state` ganha `runner` e
`tarefa_entregue`; o `/healthz` não muda. Alternativa recusada: montar `<dados>/runner` (`ro`).

## ADR-007 · Distribuição pública por `get.sh` e GitHub Releases com `SHA256SUMS`; negações por omissão (2026-11-08)
**Contexto:** o Lite passa a ser instalado por leitores de um livro, que colam um comando por SSH numa VPS sem `git`
nem `gh` configurados. A revisão da 0.1.1 encontrou ainda: o instalador recolhia o token da subscrição do leitor, o
agente podia ler credenciais e escrever nas filas que lançam e aprovam, e o `claude` herdava o ambiente todo do
runner (incluindo o token do Telegram).

**Decisão (distribuição):** `get.sh` na raiz do repositório público
(`curl -fsSL https://raw.githubusercontent.com/<dono>/<repo>/v0.1.2/get.sh | bash`) verifica Ubuntu, processador
(`x86_64`; `aarch64` com aviso) e utilizador não-root, descarrega `SHA256SUMS` e `squad-office-lite-X.Y.Z.tar.gz` de
`…/releases/latest/download/` (ou `…/releases/download/vX.Y.Z/` com `--versao`), confirma o sha256, extrai para
`~/squad-office-lite` (a anterior fica em `~/squad-office-lite.anterior-<data>`) e corre o `install.sh` com as outras
opções; a casa (`~/office-lite`) nunca é tocada. Todo o trabalho está numa função chamada na última linha (um download
interrompido não executa nada). As versões são feitas por `scripts/release.sh` a partir de uma árvore gerada por
`scripts/preparar-publico.sh` (sem histórico, sem `docs/`, com verificação de nomes internos e de caminhos pessoais).
Publicar é manual, pelo mantenedor; a CI continua sem publicar nada (ADR-006).

**Decisão (travões por omissão):** (1) o instalador deixa de pedir e de gravar o token da subscrição: o leitor
escreve-o ele próprio no `claude.env` ou no `runner.env` (0600); a chave de API continua a poder ser pedida. (2) O
runner passa sempre ao `claude` um `--settings` em JSON em linha (nenhum ficheiro que o agente possa alterar entre
retomas) com `permissions.deny` para `Read` das credenciais (`claude.env`, `runner.env`, `.env`, `**/.env`,
`**/.env.*`, `~/.config/gh`, `~/.ssh`) e `Edit` dessas, do `office.yaml`, das filas e dos pedidos
(`quadro-lancar/`, `decisoes-fila/`, `decisoes/`, `quadro/`), de `<dados>/runner/`, do código do Lite e do ficheiro de
`runner.settings`, mais `blockWritesOutsideWorkingDirectories: true`; o `runner.settings` do utilizador junta-se
(listas concatenadas), não substitui. (3) A lista de ferramentas por omissão perde a web e os `Bash` de ficheiros que
as ferramentas do Claude Code substituem. (4) O ambiente do `claude` passa a lista branca. (5) `runner.max_usd` e
`runner.modelo` opcionais; `office_runner.py parar`.

**Limites aceites:** as negações valem para as ferramentas do Claude Code e para os comandos de ficheiros que ele
reconhece no Bash; um interpretador autorizado (`python3`, `npm`, `make`) lê e escreve tudo o que o utilizador pode.
O contentor e o `claude` correm com o mesmo uid, por isso o runner não sabe quem escreveu um pedido nas filas e um
programa do agente pode escrever nelas e no `office.yaml`. Um uid próprio no contentor não chega (o contentor precisa
de ler `~/.claude/projects`, 0700, e um agente com o uid do runner pode alterar o próprio runner); a solução é correr
o `claude` com outro utilizador, numa versão futura. Nesta: aviso no journal e no Telegram quando o `office.yaml`
muda, o risco escrito no README e, como protecção forte, o sandbox do Claude Code ou um utilizador só para o Office.

**Alternativas recusadas:** `git clone` do `main` (versão não fixada, precisa de `git`); pacote `.deb`/npm
(dependências e manutenção por distribuição); imagem pré-construída num registry (a CI não publica imagens, ADR-006);
`--settings` num ficheiro gerado (o agente podia alterá-lo entre retomas); manter o pedido do token com aviso (o
programa continuaria a recolher uma credencial da conta do leitor).

**Consequências:** um comando, versão fixada e verificada, sem dependências novas. Os nomes dos ficheiros, o
`SHA256SUMS` e o caminho do `get.sh` passam a ser um contrato público (impressos no livro): mudar exige manter os
antigos. O `get.sh` em si não é verificado (o README mostra a forma prudente: descarregar, ler, correr). Projectos que
precisem de variáveis de ambiente próprias (proxy, `NODE_OPTIONS`) deixam de as receber do runner; a web volta
acrescentando-a a `runner.ferramentas`. `blockWritesOutsideWorkingDirectories` só existe nas versões recentes do `claude`; as negações
explícitas valem em todas.

**Nota · atestação (2026-11-08):** o tarball e o `SHA256SUMS` passam a ser construídos e atestados
(`actions/attest-build-provenance`) por um workflow `workflow_dispatch` no repositório público, que só os anexa a uma
release em rascunho; publicar continua manual. `scripts/release.sh --publica` (e `preparar-publico.sh --publica`)
servem a árvore pública, que já não tem a lista de nomes; a verificação de caminhos pessoais mantém-se. A verificação
(`gh attestation verify … --repo MozThinker/squad-office-lite`) é opcional (README e `get.sh`). Nesta versão o
workflow fica proposto em `docs/propostas-ci/release.yml`, a activar depois do merge. Alternativa recusada: atestar
na dev (a atestação tem de ser do repositório que o leitor usa).

## ADR-008 · Modo Docker: imagens publicadas no GHCR, runner em contentor e `configurar` por perguntas (2026-11-08)
**Contexto:** leitores com Windows ou macOS, sem VPS, sem bash nem `git`, querem experimentar a squad no próprio PC.
O modo VPS (ADR-006/007) exige Ubuntu, systemd e o `claude`, o `gh` e o `git` no host; um porte nativo esbarra no
`fcntl`, no `killpg` e no `/proc` do runner.

**Decisão:** (1) Segundo modo, só com o Docker Desktop: serviços `office`, `caddy`, `runner` e `configurar` num
compose gerado por `runner/gerar_compose.py --modo docker` (o `--modo vps` continua a omissão, com os mesmos bytes),
independente do `office.yaml` (nome `office-lite-pc`; redes `office` e `runner`; só o Caddy publica a porta, por omissão
em `127.0.0.1:8080`). O `runner` corre `office_runner.py ciclo` (o mesmo código do timer, em `init: true`) com o uid
10001 (`OFFICE_UID`), raiz só de leitura, sem socket do Docker, e monta `config` e `segredos` só de leitura. O
`configurar` (perfil próprio, mesma imagem) corre como root só para o `chown` das raízes dos volumes (lista fixa, sem
seguir symlinks) e larga logo os privilégios; pergunta palavra-passe, projectos, identidade do git, credencial do
Claude e token do `gh`, e escreve em volumes: o hash bcrypt (`htpasswd`, palavra-passe por stdin), o utilizador e o
segredo do proxy no `proxy`; o `office.yaml` (validado antes de ir para o lugar) no `config`; o `claude.env` no
`segredos` (`OFFICE_SEGREDOS_DIR`). O Caddyfile vai no próprio compose (`configs.content`, Compose ≥ 2.23.1) e lê os
segredos com `{file./proxy/…}`; o Office lê o segredo de `OFFICE_PROXY_SEGREDO_FILE` e responde 503 «Falta
configurar» enquanto falta (nunca fica aberto). Dados em volumes nomeados, um por pasta de `officecfg.MOUNTS`, com o
mesmo `ro`/`rw` do modo VPS; só os projectos são uma pasta do PC (bind em `/projectos`). O modo vem de
`OFFICE_MODO=docker` (imagem e compose): o `office.yaml` não ganha chaves. (2) Imagens `linux/amd64` e `linux/arm64`
do Office e do runner publicadas em `ghcr.io/<dono>/squad-office-lite` e `…-runner` só por um workflow
`workflow_dispatch` (`imagens.yml`) do repositório público, depois do `release.yml` e sobre a release em rascunho:
atesta as imagens (`actions/attest`, guardado também no registry) e gera o `compose.yaml` com as imagens fixadas por
`tag@sha256:`, que atesta e anexa à release com o `env.example`; publicar a release continua manual. Usa só o token
do próprio workflow (`packages: write`), sem segredos configurados. A CI dos PRs nunca publica. (3) O token da
subscrição é gerado pelo leitor (`docker compose run --rm runner claude setup-token`, numa janela à parte) e colado
no `configurar`, que o explica antes, valida o formato e o grava `0600` no volume `segredos`; o programa nunca corre o
`setup-token` nem lê `~/.claude`.

**Desvios:** ao ADR-006 («imagem publicada em registry» recusada; «o Lite não publica nada») e ao ADR-007
(alternativa recusada «imagem pré-construída num registry»): sem bash nem `git` no PC, construir localmente não é
opção; o modo VPS continua a construir localmente. Ao ADR-007 (1) («o instalador deixa de pedir e de gravar o token da
subscrição»): no PC o leitor não chega ao volume para escrever o ficheiro ele próprio; mantém-se que o programa nunca
obtém o token por si. Nas regras do `CLAUDE.md`, «a CI nunca publica imagens» passa a «a CI dos PRs nunca publica;
única excepção, o `imagens.yml` do repositório público».

**Alternativas recusadas:** construir as imagens no PC (exige `git`/bash e minutos de build); WSL2 à mão; porte
nativo do runner; o Office sem Caddy em `localhost` (uma rota que lança agentes sem palavra-passe na rede local);
snippet `import` ou entrypoint `sh` no Caddy para os segredos (validação e escrita a mais, segredos no ambiente);
`bcrypt` por pip (dependência nova); `configurar` como `run runner configurar` (o runner, onde corre o agente, não
pode montar o `proxy`); o instalador oficial do `claude` no build (vai sempre buscar o `latest` primeiro): o binário
vem por download directo da versão fixada (`CLAUDE_VERSAO`), conferido com o sha256 do `manifest.json`, como o `gh`
(sha256 do `checksums.txt`); pastas do PC (bind mounts) para os dados (lentos e com donos estranhos no Windows).

**Consequências:** um ficheiro (`compose.yaml`) e três comandos (`configurar`, `up -d`, abrir o browser), o mesmo
código do runner nos dois modos e nenhuma dependência Python nova. Os nomes das imagens, do `compose.yaml`, do
`env.example`, dos serviços e dos volumes passam a contrato público: mudar exige manter os antigos. O `compose.yaml`
não entra no `SHA256SUMS` (o `get.sh` exige uma só linha): a integridade vem da atestação dele e dos digests lá
dentro. Os pacotes GHCR têm de ser tornados públicos uma vez. As versões do `gh`, do `claude` e da base Python ficam
fixadas à mão no `Dockerfile.runner` (o Dependabot não lê os `ARG`). No modo Docker o agente deixa de ver a casa do
utilizador, o `~/.ssh` e as credenciais do Office, e o `office.yaml` e o `claude.env` ficam só de leitura para ele; o
limite do ADR-007 (agente e runner com o mesmo uid) mantém-se para as filas. O sandbox do Claude Code não é suportado
no contentor (o isolamento é o contentor). `docker compose restart runner` mata as tarefas em curso, que são retomadas
(até 2 vezes) uns 5 min depois. Domínio, HTTPS, Web Push e Telegram ficam só no modo VPS. Nada de Docker corre nos testes locais: o fumo do modo Docker (`fumo-docker`, Linux amd64)
fica proposto em `docs/propostas-ci/ci.yml`; Windows, macOS e arm64 verificam-se à mão. Reverter = deixar de publicar
e retirar `--modo docker`, `Dockerfile.runner`, `Caddyfile.docker`, `configurar.py` e o `ciclo` (o modo VPS não muda).

**Aprovação:** as decisões (2) imagens no GHCR do repositório público só pelo workflow manual da release e (3) token
da subscrição colado pelo leitor foram aprovadas pelo mantenedor a 8/11/2026.

**Nota (teste numa VPS Linux, 8/11/2026):** com `OFFICE_UID` diferente de 10001 o runner não escrevia: as pastas da
imagem eram `0750` de 10001 e o Docker copiava para cada volume vazio o ponto de montagem da imagem, voltando a dar a
raiz a 10001 a cada arranque (observado; mecanismo em
https://docs.docker.com/engine/storage/volumes/#populate-a-volume-using-a-container). Daí: (a) `nocopy` em todos os
volumes salvo `caddy-data`/`caddy-config` (precisam do conteúdo da imagem) e, na imagem do runner, pastas-pai e pontos
de montagem de root `0755`; o dono e o modo das raízes dá-os só o `configurar` (chown como root, modo já sem root);
(b) o projecto do compose passa a `office-lite-pc` (o VPS fica `office-lite`): os dois modos coexistem no mesmo Linux;
(c) todos os serviços com `no-new-privileges` e `cap_drop: [ALL]`; `cap_add` só no `configurar` (`CHOWN`,
`DAC_OVERRIDE`, `SETGID`, `SETUID`) e no Caddy (`NET_BIND_SERVICE`, file capability do binário da imagem oficial, e
`DAC_OVERRIDE`, para ler o volume `proxy` 0700 do `OFFICE_UID` sendo root), cada uma justificada no compose.

## ADR-009 · VPS: Docker rootless como caminho recomendado; portas 80/443 pelo `ip_unprivileged_port_start` (2026-11-13)
**Contexto:** o modo VPS pedia o utilizador do Office no grupo `docker`, que equivale a root (`docker run -v /:/host`).
A squad corre programas com esse utilizador: um programa dela chegava a tudo na VPS, incluindo a pasta e as
credenciais de outro utilizador (ex.: quem administra a VPS com `sudo`).

**Decisão:** o README recomenda o Docker rootless (documentação do Docker, «Rootless mode», consultada a 13/11/2026)
para um utilizador do Office sem `sudo`; quem tem `sudo` instala os pacotes (`docker-ce-rootless-extras`, `uidmap`,
`dbus-user-session`), desliga o Docker de sistema, liga o *linger* e o sysctl; o utilizador do Office corre
`dockerd-rootless-setuptool.sh install`. O `install.sh` detecta o rootless (`docker info`: `name=rootless`), recusa
sem as portas 80/443 permitidas, grava `OFFICE_UID=0`/`OFFICE_GID=0` no `.env` (no rootless o uid 0 do contentor é o
utilizador no host; qualquer outro uid cai nos subuids e não escreve nas pastas `0700` dele) e, se o `docker` só chega
ao daemon pelo `DOCKER_HOST`, activa o contexto `rootless` (o runner e as squads não recebem o `DOCKER_HOST`). O
serviço `office` do compose passa a ter `cap_drop: ALL` e `no-new-privileges` (como no modo Docker). O `doctor` diz
qual dos Dockers responde, verifica portas e `OFFICE_UID` no rootless e avisa (`⚠`, sem mudar o código de saída)
quem está no grupo `docker`. O modo do grupo `docker` continua a funcionar; o modo Docker (PC) não muda.

**Portas 80/443:** `net.ipv4.ip_unprivileged_port_start=80` em `/etc/sysctl.d/`. É uma das duas formas da
documentação do Docker, uma linha que sobrevive a actualizações, e com 80 (não 0) as portas abaixo, incluindo o SSH
na 22, continuam só do root. O `install.sh` e o `doctor` aceitam também o `setcap` no `rootlesskit`.

**Alternativas recusadas:** `setcap cap_net_bind_service=ep` no `rootlesskit` (dá a capacidade a qualquer utilizador
que corra esse binário, e uma actualização do pacote substitui o binário e perde-a em silêncio: o Office deixa de
abrir as portas depois de um `apt upgrade`); portas altas com redirecção na `ufw`/`nftables` (regras NAT à mão no
`before.rules`, o tráfego local precisa de outra regra no `OUTPUT`, as portas altas ficam abertas se a firewall falhar,
e o Caddy pede certificados nas 80/443 que não são as dele); `userns-remap` no Docker de sistema (o daemon continua
root e o grupo `docker` continua a valer root).

**Consequências:** qualquer utilizador da VPS pode abrir portas 80–1023 que estejam livres (com o Caddy a correr, a
80 e a 443 estão ocupadas). O Caddy deixa de ver o IP real do cliente (o *port driver* `builtin` por omissão não o
preserva; o campo `ip` das decisões fica com um endereço interno). As portas publicadas passam pela `ufw` (no Docker
de sistema saltavam-na). Reverter = voltar ao grupo `docker`: o `install.sh` repõe o uid/gid no `.env`.

## ADR-010 · Biblioteca: índice em fundo, `/f/` sem lock e ETag com `private, no-cache` (2026-11-17)
**Contexto:** com milhares de ficheiros o varrimento corria dentro do pedido e com lock: a lista, os `/f/` e as
miniaturas esperavam por ele (centenas de ms a segundos no telemóvel) e a lista descarregava inteira a cada abertura.
Porte do Squad Office (Biblioteca rápida, 13/11/2026), sem as constantes da casa.

**Decisão:** o instantâneo do índice é refeito numa thread passados os 60 s do `TTL_S` (uma de cada vez,
`acquire(blocking=False)`); os pedidos respondem com o anterior e só o 1.º, logo depois do arranque, espera (o
`server.py` chama `aquecer()` ao arrancar). `/f/`, `HEAD /f/` e `/mini/` nunca tocam no lock: usam o instantâneo
actual e, sem ele ou com ele mais velho que o TTL, resolvem o caminho directamente no disco com as mesmas regras do
varrimento (allowlist, denylist, profundidade, pastas escondidas, descida pasta a pasta por fds `O_NOFOLLOW`, `lstat`
do ficheiro; o `_abrir` continua a confirmar dev/inode/tamanho). A lista leva `ETag` fraco (hash dos itens, sem
`gerado`; `-gz` na variante comprimida) e `Cache-Control: private, no-cache`: `If-None-Match` igual dá `304` sem
corpo. Os pedidos de aprovação só se relêem quando o `lstat` muda e as ligações item ↔ pedido saem de um índice
invertido. As regras de caminho passam para `arquivo.py`, partilhado com o runner (ADR-011): uma só fonte.

**Alternativas recusadas:** varrimento síncrono com TTL maior (continua a bloquear, só menos vezes); `inotify` (fora
da stdlib portátil e não vê montagens de rede); `max-age` na lista (mostraria itens velhos sem revalidar).

**Consequências:** muda cabeçalhos públicos (`ETag`, `304`, `private, no-cache` na lista); `/item` e `/f/` sem `?v=`
continuam `no-store`. Com o índice velho, o `/f/` serve um ficheiro novo antes do índice seguinte (como o código
antigo, que refazia o índice dentro do pedido). A lista, o `/item`, o `/zip` e o `/f/` dão os mesmos bytes que a 0.1.4
sobre as mesmas fixtures (teste de equivalência). Reverter = voltar ao `get()` síncrono e tirar os cabeçalhos.

## ADR-011 · Biblioteca: arquivo pela fila, o runner move, links antigos e push seguro (2026-11-17)
**Contexto:** quem usa a Biblioteca no telemóvel quer tirar da vista o material antigo sem o apagar; o contentor do
Office não pode escrever nas fontes (ADR-002: `read_only`, `biblioteca` só de leitura).

**Decisão:** arquivar = mover o item (documento com as suas versões, ou lote de media, de uma fonte `biblioteca:`)
para `<fonte>/.arquivo/<o mesmo caminho>`, tudo ou nada, nunca sobrescrever, nunca apagar; repor = o inverso. O
Office só grava pedidos v1 em `<dados>/arquivo-fila/` (mount `rw`; 50 pendentes; 409 para um item que já tenha um
pedido pendente, de qualquer acção); o runner, a cada passagem, revalida tudo contra a config, aplica as protecções
(pedido de aprovação pendente ou de estado desconhecido que cite o item, ficheiro alterado há < 48 h), move com
`os.rename` por fds sem seguir symlinks, responde em `<id>.feito.json` com um erro de uma lista fechada e regista uma
linha por ficheiro no `<fonte>/.arquivo/REGISTO.tsv`. Um 2.º índice sobre as `.arquivo/`, feito só a pedido, serve
«Ver arquivados» e os links antigos (mesmo id e mesmos caminhos; `/f/` e `/mini/` pelo resolver directo, sem lock).
Se o topo do repositório git da fonte for `<dados>/biblioteca` ou estiver dentro dele, os ficheiros seguidos vão num
só commit por fonte e por passagem `chore(arquivo): N ficheiros` (caminhos por stdin, hooks desligados) e o push só
acontece no ramo do `origin/HEAD` com 0 atrás e 1 à frente; senão uma linha `AVISO` no log do runner. O commit leva o
conteúdo do disco: um item com seguidos modificados, em stage ou apagados não se move (`alterações por commitar`), nem
um com um submódulo (gitlink: o ponteiro por commitar não se vê no status; `não arquivável`); se o ls-files, o add ou o
commit falhar, os renames dessa fonte e passagem são desfeitos (tudo ou nada) e os pedidos recebem `erro
git`, para nada ficar fora do git escondido pelo `.gitignore` da `.arquivo/`. O runner trata no máximo 50 pedidos por
passagem (um Office comprometido não o põe a fazer milhares de commits e pushes) e recusa um 2.º pedido do mesmo item
na mesma passagem (`pedido ilegível`).

**Alternativas recusadas:** o Office mover os ficheiros (o contentor passava a escrever nas fontes); `git mv` com os
nomes na linha de comandos; push sempre (publica trabalho alheio e cria merges no `pull` seguinte); sem push (o `pull`
seguinte cria um merge); arquivar tarefas, planos e pedidos de aprovação (o Quadro, o runner e as aprovações lêem
essas pastas directamente: mudava contratos v1).

**Consequências:** mesmo modelo dos ADR-001/005 (o Office pede, o runner faz); uma 6.ª escrita do Office, limitada a
itens existentes das fontes, às protecções e a 50 pendentes, e reversível pelo «Repor». Contrato novo `v: 1` no
README («Referência»); mudar o formato exige `v: 2` e o runner a ler os dois. Um item reposto volta a abrir pelo mesmo
link no varrimento seguinte (≤ 60 s). Instalações Docker existentes: o volume novo nasce do root e o «Arquivar» dá 503
até se correr o `configurar` uma vez. Reverter = tirar a rota, o mount e a passagem do runner; o «Repor» devolve os
ficheiros.

## ADR-012 · Arquivo automático por idade, opt-in por fonte (`biblioteca[].arquivo_dias`) (2026-11-17)
**Contexto:** quem instala quer que o material velho saia sozinho da Biblioteca, sem as regras próprias de uma casa
(prefixos, calendários, pastas fixas).

**Decisão:** chave opcional `arquivo_dias` (7–3650) por fonte; sem ela o runner nunca arquiva nada sem pedido. O
runner corre o automático no máximo 1× por dia (marca em `<dados>/runner/arquivo-auto.dia`) sobre os itens cujo
ficheiro mais recente tem mais de N dias, com as protecções do arquivo a pedido mais duas: não sai o que é citado por
um pedido de aprovação alterado há < 14 dias, nem o que já foi reposto alguma vez (pelo item ou por algum dos seus
ficheiros no REGISTO). Motivo no REGISTO: `idade (N
dias)`. A chave é aditiva: a `versao` do `office.yaml` continua `1`.

**Alternativas recusadas:** chave global (uma fonte de estudos e uma de rascunhos têm ritmos diferentes); ligado por
omissão (moveria ficheiros em instalações existentes sem aviso); regras por prefixo ou calendário (são de uma casa,
não do Lite); mínimo abaixo de 7 dias (arquivos por engano).

**Consequências:** reversível pela chave e pelo «Repor»; a 1.ª passagem numa fonte antiga pode mover muito (o README
manda experimentar numa fonte de teste). O que já saiu só volta pelo «Repor» (o REGISTO diz o quê e quando).

## ADR-013 · Como acabou a execução: o servidor lê o `fim` do `claude.log` (2026-11-17)
**Contexto:** uma tarefa pode acabar «concluída» com ferramentas recusadas, ou com um erro da API, e o escritório só
mostrava o estado da meta. O Squad Office resolveu isto lendo a última linha `result` do `claude.log`.

**Decisão:** na mesma leitura do fim de `<tasks>/<id>/claude.log` que já dá custo e turnos, o servidor tira da última
linha `result` um `fim` com 5 chaves fixas (`recusas`, `ferramentas` por regex e no máximo 5, `motivo`, `erro`,
`http`), só para tarefas `worktree` terminadas há ≤ 3 dias; `falhas_exec` junta-as às tarefas com estado em falha
(onde `interrompida` passa a contar). Só ficheiros regulares, sem symlinks e sem bloquear; nada mais da linha sai.

**Alternativas recusadas:** o runner gravar o `fim` na meta (mexe no runner e no formato da meta e não serve as
tarefas anteriores à 0.3.0).

**Consequências:** o runner e os contratos v1 não mudam; o `claude.log` já estava montado só de leitura nos dois
modos. Reverter = tirar `fim`/`falhas_exec` (o cliente mostra «—»).

## ADR-014 · Entregas: `GET /api/entregas`, segunda saída de conteúdo, só a pedido (2026-11-17)
**Contexto:** quem chega ao escritório quer ver o que ficou pronto sem abrir o leitor tarefa a tarefa.

**Decisão:** rota só de leitura com manchete (≤ 160) e resumo (≤ 600) de `meta.result` ou do fim do `claude.log`,
mascarados por `reader.delivery` antes do corte; até 40 entregas de 7 dias; metas só dentro das raízes da config;
ligações à Biblioteca pelo índice já pronto (nunca o constrói). O cliente só a pede ao abrir o separador «Entregas»;
as «lidas» ficam no `localStorage` do browser.

**Alternativas recusadas:** os resumos no `/api/state` (texto a cada 8 s para todos); só o leitor, uma a uma.

**Consequências:** a segunda saída de conteúdo além do leitor, com a mesma máscara; as «lidas» não passam entre
dispositivos. Reverter = tirar a rota e o separador.

## ADR-015 · Replay do dia reconstruído das metas e dos transcripts, sem escrita nova (2026-11-17)
**Contexto:** ver como correu o dia sem gravar fotografias do estado (escrita nova, volume novo, mais um mount).

**Decisão:** `GET /api/replay?dia=AAAA-MM-DD` (hoje e os 6 dias anteriores no `fuso`) reconstrói o escritório só das
metas, dos transcripts e do `mtime` dos `SPEC/PLAN/QA.md`, com a mesma regra do ao vivo (`_live_line`) sobre estado
próprio: quadro completo a cada 5 min e diferenças (≤ 1/s), allowlist de chaves, tecto de 4 MiB (`truncado`), cache
por transcript e das respostas (uma por dia, até 3; o fim de hoje desce ao minuto para a cache acertar), em `replay.py`. Tectos
contados enquanto se constrói: no máximo 12 subagentes por tarefa (os maiores), 8 MiB lidos por transcript (o fim) e
4 MiB de resposta. Sem escrita, sem thread nova, sem mount.

**Alternativas recusadas:** gravar fotografias do `/api/state`; replay no cliente a partir do `feed` (30 eventos não
chegam).

**Consequências:** o replay só mostra o que os transcripts guardam (sem as subagentes antigas que já não existam);
uma tarefa retomada pelo runner depois de uma paragem longa conta como «a trabalhar» desde o `started_at` original
(o runner não o reescreve na retoma), no replay e em «O meu dia»;
custo limitado por teste de CPU (`tests/test_r4.py`). Reverter = tirar a rota e o botão.

## ADR-016 · Interface em módulos: `index.html` com tecto e módulos a pedido (2026-11-17)
**Contexto:** o `index.html` da 0.2.0 tinha 95 106 B e a v3 traz mais do dobro de código de interface.

**Decisão:** o `index.html` não pode passar os 95 106 B (teste); o código novo vive em módulos estáticos, sem build
nem bibliotecas: no arranque `topo.js`, `avisos.js`, `lateral.js`, `act.js`, `fabrica.js`, `ambiente.js`, `office.css`
e `lateral.css`; por `import()`, quando são precisos, `paineis.js`, `entregas.js`, `tabelas.js`, `replay.js` e
`demo.js` (só com `?demo=1`). A letra Pixelify Sans (SIL OFL 1.1, `static/fonts/OFL.txt`) só nos títulos.

**Alternativas recusadas:** um bundler (dependência e passo de build novos); tudo no `index.html` (já no limite).

**Consequências:** o telemóvel só descarrega o que usa e cada função pura testa-se em node. Reverter é caro (muitos
ficheiros e testes), por isso fica registado.

## ADR-017 · Agentes das squads em `<dados>/agentes/`, fila `agentes-fila` aplicada pelo runner (2026-11-18)
**Contexto:** o Squad Office (vista «Agentes», o seu ADR-011) mostra e muda as skills e o modelo dos agentes de cada squad
por uma fila aplicada por um script do host. O Lite não tinha nenhum conceito de agentes: o runner corre `claude -p`
num `git worktree` do projecto. No modo Docker o `office.yaml` é só de leitura para o runner (ADR-008).

**Decisão:** padrão dos ADR-001/005/011. (1) Agentes, skills e instruções de cada squad vivem em
`<dados>/agentes/<squad>/` (`agents/<nome>.md`, `skills/<skill>/SKILL.md`, `CLAUDE.md`); `SUB` ganha `agentes` (`ro` no
Office, `AGENTES_DIR`), `agentes-fila` (`rw`, `AGENTES_FILA_DIR`) e `agentes-rascunhos` (`ro`, `AGENTES_RASCUNHOS_DIR`).
Só o runner escreve em `agentes/` e `agentes-rascunhos/`; as três ficam negadas ao `Edit`/`Write` do agente.
(2) `agentes.py` (stdlib, raiz do repo) tem o parser de frontmatter próprio (sem YAML, lista fechada de chaves,
`bypassPermissions` proibido, nenhum `---` dentro do frontmatter), a validação dos pedidos e a reescrita byte a byte de
`model:`/`skills:`; é usado pelo servidor e outra vez pelo runner, que revalida tudo (a fila é escrita pelo contentor e
o agente tem o mesmo uid). (3) `GET /api/agentes` devolve só frontmatter e caminhos relativos; `POST
/api/agentes/pedido` grava um pedido **v1** (contrato novo, README «Referência»): ≤ 50 pendentes, 1 por alvo, `sha256`
do ficheiro visto, comparado pelo runner antes de escrever (backup `.bak-AAAAMMDD-HHMM`, resposta em `feito.json`).
(4) Na preparação e na retoma de cada tarefa o runner copia os agentes, as skills e o `CLAUDE.md` da squad para
`<worktree>/.claude/`, nunca por cima de um ficheiro do repo (o do repo ganha, com aviso em `issues`), com os caminhos
copiados no `info/exclude`: o `claude` lê-os como agentes do projecto nos dois modos, sem mudar argv nem `HOME`.
(5) Chave opcional `squads[].modelo` (modelo da sessão principal da squad; ganha ao `runner.modelo`), mostrada na vista
como modelo do lead (Office #36); `versao: 1` mantém-se. Do esquema do Office (#33) volta só o que não muda o formato:
recusa de caracteres invisíveis (Cf/Co/Cs, salvo ZWJ) e mensagens de erro sem o valor recusado.
(6) O ADR-012 do Office (estado numa raiz única) não se aplica: o Lite já lê as squads `sessao` de uma raiz única
(`<dados>/squads`, um mount) desde o ADR-006.

**Alternativas recusadas:** um `HOME` ou `CLAUDE_CONFIG_DIR` por squad (muda o ambiente e as credenciais do `claude`, e
difere entre modos); `--agents` com JSON (definições no argv); o Office a escrever nos agentes (quebra o ADR-001);
instalar skills de fora (quarentena do Office, fica para outra versão); salas, lugares, `papeis` e `lancador` do esquema
do Office (são do desenho e dos scripts de lá).

**Consequências:** 6.ª pasta em que o serviço escreve, só atrás do basic auth e do segredo do proxy (ADR-006). No modo Docker os três
volumes novos nascem de root numa instalação antiga: a fila responde 503 até se correr o `configurar` uma vez. O
`info/exclude` é do repo (partilhado pelos worktrees), por isso os caminhos excluídos valem também no checkout
principal. A `meta.json` está numa pasta onde o agente escreve: o runner guarda em `<dados>/runner/tarefas/<tid>.json`
(negado ao agente) o worktree, as cópias, a sessão e os tectos da rotina, e a retoma recusa uma meta cujo `workdir` ou
`worktree` não bata com o que ele calcula. Reverter = tirar as rotas, as pastas do `SUB` e a cópia no `preparar`; os
ficheiros dos agentes ficam.

## ADR-018 · Agentes por descrição: rascunho de IA, ensaio só de leitura e activação por `sha256` (2026-11-18)
**Contexto:** porte do Office #34 (o seu ADR-013). Criar um agente pedia editar `.md` à mão.

**Decisão:** acções `agente_rascunho`, `agente_criar`, `agente_ensaio`, `agente_activar`, `agente_descartar` e
`agente_remover` na fila do ADR-017. O rascunho usa o `claude` do utilizador, nas mesmas condições do lançador
(credenciais do `claude.env`, ambiente da lista branca), **sem ferramentas** (`--tools ""`, `--max-turns 1`,
`--strict-mcp-config`, `--max-budget-usd 0.50`, sem sessão guardada), com a descrição pelo stdin entre marcas com nonce,
declarada como dado. A saída é normalizada (nome, ferramentas de leitura, modelo da lista) e tem de passar o validador;
fica em `agentes-rascunhos/`, nunca numa pasta de agentes. O ensaio corre a versão editada **só de leitura** num clone
`--depth 1 file://` de um projecto do `office.yaml` em `mkdtemp`, sem remotes (`dontAsk`, `Read`/`Grep`/`Glob`,
`--disallowedTools` de escrita, shell e rede, 8 turnos, 2 USD, 600 s; pasta apagada sempre; segredos tapados no
relatório). «Activar» só aceita o `sha256` de um ensaio `ok` e grava com `O_EXCL`; «Remover» move o `.md` para
`agents/.removidos/` com backup (nunca o lead). Travões: uma acção com IA por passagem e 10 USD por dia (constante
`agentes.IA_DIA_USD`, livro em `<dados>/runner/agentes-ia.jsonl`).

**Alternativas recusadas:** chave nova `runner.tecto_ia` (mais esquema sem pedido; pode vir depois sem `versao: 2`);
rascunho com ferramentas (o texto livre podia mandar ler ou escrever); ensaio num `git worktree` (partilha remotes e
config com o clone); ensaio pelo GitHub (rede e tokens); registar projectos pela fila (os projectos são do
`office.yaml`, que o runner não escreve); desactivar por `permissions.deny` (o Lite não tem settings por squad).

**Consequências:** uma passagem do runner pode durar até ≈ 10 min durante um ensaio (o lock impede passagens
sobrepostas). As ferramentas de escrita, shell e rede do agente não são ensaiadas, e a vista di-lo.

## ADR-019 · Squads a partir de modelos genéricos e rotinas só por aprovação (2026-11-18)
**Contexto:** porte do Office #37 (o seu ADR-014). No Lite não há agendador externo: quem agenda é o runner.

**Decisão:** quatro modelos genéricos no repo (`modelos/squads/{desenvolvimento,investigacao,observacao,conteudo}/`,
verificados por `tools/verificar_modelos.py`, também teste). `squad_rascunho` (IA sem ferramentas, só descrições, corpos
e missão; `name`, `tools`, `model`, `skills` e `permissionMode` vêm sempre do modelo), `squad_criar` (tudo-ou-nada em
`<dados>/agentes/<id>/`, registo em `agentes/squads.json`), `squad_ensaio`, `squad_descartar` e `squad_desactivar`. O
runner nunca escreve o `office.yaml`: se o id já é uma squad da config sem agentes, o modelo povoa-a; senão a vista
mostra o bloco YAML a colar. Rotinas por formulário fechado (projecto, hora, dias, instruções, tecto 0.10–5.00 USD,
1–30 turnos; ≤ 5 por squad): `rotina_pedir` faz o runner escrever em `decisoes/` um `.md` no formato das Aprovações
(ADR-001) com a definição, o seu `sha256` e `- [R1] …`; a rotina só fica activa com `R1 → aprovado` e o `sha256` igual.
O runner lança-a à hora como tarefa `task` da squad, dentro de `tecto_diario`/`paralelo`; 3 erros seguidos pausam-na;
`rotina_retomar`/`rotina_cancelar` pela fila.

**Alternativas recusadas:** cron do sistema (fora do runner, diferente entre modos); rascunho de rotina por IA (sem
pedido, custo); o runner a editar o `office.yaml` (só de leitura no modo Docker).

**Consequências:** com o runner parado à hora, a rotina corre na 1.ª passagem desse dia depois da hora; sem passagem até à meia-noite, esse dia perde-se (não há recuperação). O formato do `.md` de rotina passa a ser
contrato. Uma squad criada que não está no `office.yaml` aparece na vista mas não lança nem tem rotinas até se colar o
bloco.

## ADR-020 · Squad montada por conversa e modelos de negócio, com perfil só de leitura forçado pelo lançador (2026-11-18)
**Contexto:** porte do Office #40 (o seu ADR-015). Os 4 modelos do ADR-019 servem trabalho técnico e pedem a missão
escrita de uma vez; um negócio quer atendimento e qualificação de pedidos, e tem de poder descrever o que quer por
palavras.

**Decisão:**
- Modelos `atendimento` (triagem, redactor, revisor, responsável) e `comercial` (comercial, pesquisador, qualificador)
  em `modelos/squads/`, só de leitura: sem `Bash`, `Edit`, `Write`, `WebFetch`, `NotebookEdit` nem MCP; `WebSearch` só no
  `comercial/pesquisador`, que recebe nome e domínio; cada agente com `## Nunca`. O `comercial` exige
  `entrada/perfil-cliente-ideal.md`.
- **Perfil de negócio no lançador, não no modelo:** o runner, ao lançar uma tarefa de uma squad de `squads.json` com
  modelo de negócio, ignora `runner.permission_mode` e `runner.ferramentas` e corre com `--permission-mode dontAsk`,
  `--allowedTools Read Grep Glob Agent` (e `WebSearch` no `comercial`) e `--disallowedTools Bash Edit Write WebFetch
  NotebookEdit mcp__*` (mais `WebSearch` fora do `comercial`), com um prompt próprio que trata tudo o que vem de fora
  como dado. A leitura fica limitada por negações `Read(//…)` à pasta de dados, às transcrições do `claude`, aos
  outros projectos e aos outros worktrees existentes no arranque (o próprio worktree e a `entrada/` ficam legíveis).
  A squad recebe `entrada/accoes.md` (acções ligadas, folhas, domínios; nunca remetentes). Uma squad de negócio não
  sai deste perfil por configuração.
- **Conversa de montagem:** acção `squad_conversa` (`squad`, `texto` de 1–1000 caracteres) na fila `agentes-fila`,
  **contrato v1 sem «v» novo** (uma acção nova com chaves exactas, validada como as outras).
  Cada volta é uma chamada `claude -p` sem ferramentas, sem sessão, `--disallowedTools mcp__*`, **0,15 US$**; o runner
  guarda `agentes-rascunhos/squads/<id>/conversa.json` (só ele escreve) e reenvia a conversa como dados, entre marcas
  com nonce novo. Máx. 3 rondas de perguntas (1–3 cada) e 4 chamadas; **tecto de 1,00 US$ por conversa**, somado pelo
  runner; uma conversa em curso de cada vez; **limpeza às 24 h** sem mudança; conta para o tecto diário de 10 US$. A IA
  escolhe de uma lista fechada: pergunta, propõe (modelo e missão) ou recusa com motivo.
- A proposta só escreve a missão: os agentes são sempre os do modelo (a «Nunca» não vem de texto livre). Segue o
  rascunho, ensaio e `squad_criar` do ADR-019. Se a missão do `comercial` não pediu o perfil de cliente ideal, a proposta
  troca-se por essa pergunta.
- Motivos novos na resposta do runner: `já há uma conversa em curso`, `tecto da conversa atingido`, `a conversa já
  terminou`, `a IA não respondeu bem, envia de novo`, `não foi possível montar a squad`.
- `squads.json` passa a `0644` (o serviço `accoes`, outro uid, lê-o: ADR-021). O `doctor` avisa (sem falhar) se há
  squads de negócio com a credencial de um plano de consumidor.

**Alternativas recusadas:** `settings.json` por modelo como no Office (o perfil ficaria num ficheiro da squad, e o
runner monta os argumentos de cada tarefa: é aí que ele não se contorna); só o formulário de perguntas fixas (não
escolhe o modelo nem recusa); `--resume` (sessões em disco, o tecto não soma, as voltas deixam de ser dados); a IA
reescrever os agentes (podia apagar a «Nunca»).

**Consequências:** até 4 chamadas por squad nova, dentro do tecto diário; livro de IA com `squad_conversa`; módulo
novo `conversa.py`, partilhado pelo Office e pelo runner. O formato do `conversa.json` passa a contrato. **Reverter:**
tirar `squad_conversa` do runner (recusa-a) e os dois modelos; as squads criadas com eles ficam, com o registo.

## ADR-021 · Acções aprovadas uma a uma, executadas por um serviço com outro uid (2026-11-18)
**Contexto:** porte do Office #42 (o seu ADR-016). As squads de negócio só entregam texto; o dono copia cada
rascunho à mão. O ADR-020 fixou que nenhuma squad escreve ficheiros nem corre comandos, e isso mantém-se.

**Decisão:**
- A squad **propõe** acções no texto final (`=====ACCAO=====` … `=====FIM ACCAO=====`, um objecto JSON). **Catálogo
  fechado:** `email_rascunho`, `email_responder`, `folha_linha`, `aviso_dono`, com campos exactos, tamanhos (assunto
  200, corpo 4000, texto e valores 500), links só de domínios da squad, sem HTML nem anexos. Limites por dia e squad
  50/20/100/20 (tentativas), que a configuração só pode baixar.
- O runner (`runner/accoes_propor.py`, no fim de cada tarefa ou rotina da squad, sem IA nem credenciais) valida e
  escreve, com `O_EXCL`, o registo `decisoes/.accoes/accoes-<squad>-<AAAAMMDD>-<n>.json` (definições canónicas, sha,
  fuso) e depois o pedido `decisoes/accoes-<squad>-<AAAAMMDD>-<n>.md` (formato das Aprovações, ADR-001; máx. 10 linhas
  `[A1]…[A10]`, texto citado com `> `, recusadas listadas). Aprova-se linha a linha; **sem «Aprovar todas»** nestes
  pedidos (as outras aprovações não mudam).
- **Serviço `accoes`, com outro uid e o `accoes.env` só dele** (`servico_accoes/`: colector, executor, arranque), sem
  segredos no runner, no office nem no compose. O executor só executa com squad activa, acção ligada, `→ aprovado`,
  secção do `.md` igual à re-renderizada do registo, sha igual, ≤ 48 h desde a criação, abaixo do limite do dia (no
  fuso gravado no registo, porque o serviço não lê o `office.yaml`) e sha nunca executado (marcador `O_EXCL` antes da
  chamada). Volta a passar o conteúdo pelas regras do gerador. `email_responder` vai só para o `From` registado pelo
  colector, de mensagens autenticadas (`authserv_id` + `dmarc=pass` ou `dkim=pass` alinhados), espera 2 min com «anular».
  Rascunho e linha de folha desfazem-se. Folhas só CSV em `<dir>/folhas/`, valores com fórmulas neutralizadas.
- **Docker:** o serviço arranca `root` só para fazer `chown` das raízes dos 3 volumes (`accoes-segredos`,
  `accoes-estado`, `accoes-entrada`; um 4.º, `accoes-saude`, desde o ADR-022) com `CHOWN`, `SETUID` e `SETGID`, e
  larga os privilégios para `ACCOES_UID:ACCOES_GID`
  (omissão 10002; recusa arrancar se for o `OFFICE_UID`). `setgroups` só com o `OFFICE_GID`, para ler `decisoes` e
  `agentes` (só de leitura). Sem `init`: o `tini` ficaria root a vida toda. Rede própria. `tools/verificar_compose_accoes.py`
  falha na CI se outro serviço ver o `accoes.env`.
- **VPS:** `sudo ./instalar-accoes.sh --dados <pasta>` cria o utilizador de sistema `office-accoes` (sem casa nem
  shell), copia o código para `/opt/office-accoes` (do root), cria `/srv/office-accoes` (segredos `0700`), dá-lhe leitura
  por ACL em `decisoes` e `agentes` e instala `office-accoes.timer` (uma passagem por minuto). O `install.sh` nunca usa sudo.
- **Vista sem segredos:** o serviço publica `<dados>/accoes-entrada/<squad>/vista.json` (acções ligadas, domínios,
  folhas e colunas, endereço da caixa e remetente de cada mensagem; nunca servidores, utilizadores nem credenciais) e
  as mensagens recebidas em `…/mensagens/`. É o que o runner lê para validar propostas: o runner não monta `accoes-estado`.
- **Perfil `lite`:** vêm ligadas 3 acções (`email_rascunho`, `folha_linha`, `aviso_dono`); `email_responder` liga-se à
  mão, por squad, no `accoes.json`: quem instala o Lite não tem revisão por outra pessoa.
- **Retenção:** pedidos e registos com mais de 90 dias perdem o texto citado, remetente e assunto (o `.md`) e o
  registo `.accoes/`; mensagens e registos do colector com mais de 90 dias apagam-se. O livro mensal
  `AAAA-MM.jsonl` não guarda corpo, assunto, endereços nem valores e leva o campo `aprovado`.

**Aprovações sem máscara nos pedidos de acções:** um pedido `accoes-…md` com o registo `.accoes/` do runner é mostrado
sem a máscara de segredos (o executor envia o texto inteiro; o dono tem de ver o que sai). Os outros pedidos, incluindo
um `accoes-…md` sem registo, mantêm a máscara.

**Risco residual S1:** quem escreve como o utilizador do runner pode forjar pedido + registo + marca `→ aprovado`
(o runner escreve em `decisoes/`); a fronteira do serviço protege as credenciais e as regras de
conteúdo e de destinatário, **não a aprovação**. Uma âncora fora do host (assinatura ou confirmação por outro canal)
fica por decidir pelo dono do projecto; esta versão não a inclui.

**Continua proibido:** squads com escrita, comandos ou MCP; «aprovar todas» nas acções; acções fora do catálogo;
destinatários novos, CC/BCC, anexos, HTML, apagar no serviço (salvo o próprio «desfazer»), WhatsApp, redes sociais, CRM,
pagamentos; o Office a ligar acções ou a editar o texto de uma acção.

**Alternativas recusadas:** o runner a executar (teria as credenciais e o ambiente das squads); o `accoes-estado`
montado no runner para ele ler a vista (daria ao runner o volume do serviço; por isso a vista vai para
`accoes-entrada`); um serviço de preparação só para o `chown` (mais um serviço e uma imagem; o `root` curto e com três
capacidades no próprio serviço chega); `init: true` (o `tini` ficava `root`); parser do texto citado em vez de
re-render (ambíguo); estado num ficheiro único com contadores (o marcador `O_EXCL` e o livro chegam).

**Consequências:** primeira escrita para fora da casa (e-mail, Telegram, folha), sempre com um clique por acção; um
serviço e um uid novos (modo Docker: `ACCOES_UID/GID` no `.env`), e `acl` no VPS; no VPS, a cópia em `/opt/office-accoes`
tem de acompanhar cada `git pull` (voltar a correr o `instalar-accoes.sh`). **Reverter:** parar o serviço (nada mais
sai; no VPS `systemctl disable --now office-accoes.timer`); os pedidos e o livro ficam como registo.

## ADR-022 · A saúde do serviço `accoes` num volume próprio, `accoes-saude`, só de leitura no Office (2026-11-18)
**Contexto:** o serviço `accoes` (ADR-021) só falava pelo registo; um problema real (registo das squads ilegível,
falha de um conector) não chegava ao dono, que usa o Office no telemóvel, e o caso normal (sem squads de negócio)
enchia o registo de avisos.

**Decisão:** no fim de cada passagem completa o serviço grava `<accoes-saude>/saude.json` (`v: 1`, hora, ≤ 10 avisos
de ≤ 200 caracteres, só textos fixos; `0644`, atómico, sem seguir symlinks) e só escreve no stderr quando os avisos
mudam (`aviso:`/`resolvido:`), comparando com esse ficheiro. Um `squads.json` que não existe passa a ser «sem squads»,
sem aviso. O `accoes-saude` é um **volume próprio**, que só o serviço escreve: Docker, volume `accoes-saude` montado
no `accoes` em `/dados/accoes-saude` (`ACCOES_SAUDE`) e no `office` em `/data/accoes-saude` **só de leitura**; VPS,
pasta `<dados>/accoes-saude` (o `install.sh` cria-a `0755` se faltar e o compose faz bind só de leitura dela; o
`instalar-accoes.sh` dá-a ao `office-accoes` e junta-a ao `ReadWritePaths` da unit). O runner não o monta. É a
**única** coisa do serviço que o office vê: não monta `accoes-entrada`, `accoes-estado` nem `accoes-segredos`, e a
fronteira do ADR-021 mantém-se (o office não vê mensagens, vistas nem segredos). O servidor lê **só** `saude.json`
(`ACCOES_SAUDE_DIR`, omissão `/data/accoes-saude`; `O_NOFOLLOW`, ≤ 8 KB, chaves exactas), publicando `state.accoes`
com «parado» aos 10 min. O dashboard mostra um alerta com ciclo de vida próprio (sai sozinho; sem notificação do
browser nem Web Push). `tools/verificar_compose_accoes.py` passa a falhar se o `office` montar `accoes-entrada`,
`accoes-estado` ou `accoes-segredos`, ou o `accoes-saude` com escrita.

**Alternativas recusadas:** o `office` a montar o `accoes-entrada` só de leitura (sem volume novo, mas as mensagens
recebidas dos clientes e as `vista.json` de cada squad ficariam ao alcance de um RCE no office); estado do stderr em
memória (no VPS cada passagem é um processo); o runner a reencaminhar a saúde (mistura o uid do serviço com o das
squads).

**Consequências:** um 4.º volume no Docker (e uma pasta a mais no VPS), cuja raiz o `accoes` também passa para o seu
uid (`chown` ao arrancar, `0755`). Se o `saude.json` não se puder gravar, os avisos voltam a repetir-se a cada
passagem e o alerta não aparece. Tirar o serviço deixa o último `saude.json`, e o dashboard mostra «Serviço de ações
parado» até se apagar (ver `ACCOES.md`). **Reverter:** tirar o mount e a variável do office no `gerar_compose.py` (o
servidor sem ficheiro não mostra nada); o serviço pode continuar a gravar o ficheiro.
