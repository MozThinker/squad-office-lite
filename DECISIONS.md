# Decisões de arquitectura

Os ADR-001…005 vêm do Squad Office em que o Lite se baseia, reescritos para o Lite; o ADR-006 é a passagem a produto
instalável e o ADR-007 a distribuição pública. «Proxy» = o Caddy do compose (omissão) ou um Traefik existente (`--proxy traefik`).

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
