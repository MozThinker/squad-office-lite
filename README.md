# Squad Office Lite

[![Licença: MIT](https://img.shields.io/badge/licen%C3%A7a-MIT-blue)](LICENSE)

Um escritório em pixel art para acompanhares, no browser ou no telemóvel, uma squad de agentes do Claude Code a
trabalhar nos teus projectos. Escreves um cartão no Quadro, arrastas para «Em curso» e a squad pega no trabalho:
cria um branch, programa, testa e abre um pull request no GitHub para tu reveres. Tudo corre numa VPS tua.

## Instalação

**Precisas de:**
- uma VPS com **Ubuntu 22.04 ou mais recente** (a CI testa a instalação em 22.04, 24.04 e 26.04) e acesso por SSH
  com um utilizador normal (não root; se a VPS só tem root, ver [VPS nova só com root](#vps-nova-só-com-root));
- processador **x86_64** (testado). **arm64** é aceite pelo `get.sh`, com aviso, mas não é testado: a imagem pode não
  construir;
- memória: **4 GB ou mais** (requisito do Claude Code); no nosso teste, tarefas curtas correram em 1 GB;
- **Docker** com o plugin `docker compose`, e o teu utilizador no grupo `docker`
  ([instruções oficiais](https://docs.docker.com/engine/install/ubuntu/));
- o **Claude Code** (`claude`) instalado nessa VPS e uma forma de o autenticar: uma **chave de API** (recomendada; o
  instalador pede-a) ou um **token da subscrição**, que escreves tu num ficheiro (o instalador não o pede; ver
  [Credenciais do Claude](#credenciais-do-claude));
- o **GitHub CLI** (`gh`) instalado e com sessão iniciada (`gh auth login`), de preferência com um
  [token só para os repositórios do Office](#token-do-github);
- um **nome e e-mail para os commits** da squad (`git config --global user.name`/`user.email`; o instalador pede-os
  se faltarem);
- opcional, mas recomendado: um **domínio** (ex.: `office.exemplo.com`) com um registo DNS do tipo A a apontar para
  o IP da VPS. Sem domínio funciona, mas só em HTTP (sem cadeado e sem notificações no telemóvel).

**Passos:**

1. **Prepara a VPS.** Instala o Docker e junta-te ao grupo: `sudo usermod -aG docker $USER`; sai e volta a entrar.
2. **Instala e autentica o `claude` e o `gh`.**
   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   gh auth login     # depois de instalar o gh (instruções abaixo)
   ```
   Com uma chave de API, tem-na à mão: o instalador pede-a. Com o token da subscrição, escreve-o tu num ficheiro
   depois de instalar (o instalador não o pede nem o guarda; ver [Credenciais do Claude](#credenciais-do-claude)).
   O `gh` instala-se pelas [instruções oficiais para Ubuntu](https://github.com/cli/cli/blob/trunk/docs/install_linux.md);
   no `gh auth login` usa de preferência um [token só para os repositórios do Office](#token-do-github).
3. **Descarrega e instala o Office** (troca `<dono>/<repo>` pelo repositório do Lite). O endereço fica fixado à
   versão (`v0.1.2`), para leres sempre o mesmo script que o livro e este README descrevem.
   **Forma prudente** (recomendada: lês o script antes de o correr):
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/<dono>/<repo>/v0.1.2/get.sh
   less get.sh
   bash get.sh --versao 0.1.2
   ```
   **Com um comando:**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/<dono>/<repo>/v0.1.2/get.sh | bash -s -- --versao 0.1.2
   ```
   > Este comando descarrega um script da internet e corre-o no teu servidor, com o teu utilizador. Só o faças com
   > endereços em que confias, e de preferência lê-o primeiro. O `get.sh` confere o sha256 do pacote: isso apanha uma
   > transferência estragada, mas não te protege se o próprio repositório for alterado por alguém. Corre-o num
   > servidor de teste, nunca como `root`.

   O `get.sh` confirma que é Ubuntu, o processador e que não estás como root; descarrega a última versão publicada
   (GitHub Releases), confirma o sha256 dela com o `SHA256SUMS` publicado ao lado, extrai-a para `~/squad-office-lite`
   e corre o `install.sh`. Não precisa de `git` nem de `gh`. O `get.sh` em si não tem sha256: é para isso a forma
   prudente. Opções (depois de `bash -s --` no comando de uma linha, ou de `bash get.sh`):
   - `--sem-perguntas`: as respostas vêm do ambiente (ver o cabeçalho do `install.sh`; `OFFICE_GIT_NOME` e
     `OFFICE_GIT_EMAIL` para a identidade do git; sem elas, e sem identidade no git, só avisa). Ex.:
     `curl -fsSL …/get.sh | bash -s -- --versao 0.1.2 --sem-perguntas`;
   - `--versao X.Y.Z`: instala essa versão em vez da última (`… | bash -s -- --versao 0.1.2`);
   - as outras opções passam ao `install.sh` (ex.: `--proxy traefik --rede <rede>`).

   O instalador confirma o que falta, pergunta o domínio (Enter = sem domínio), o utilizador e a palavra-passe
   (12 ou mais caracteres), as credenciais do Claude (chave de API; token da subscrição, que só explica como o
   escreveres tu; ou nenhuma), o nome e o e-mail para os commits da squad (só se o git ainda não os tiver; os que já
   existem nunca são trocados) e arranca tudo.
   **Tempo:** no nosso teste (VPS de 1 vCPU e 1 GB, Ubuntu 26.04, 8/11/2026), `install.sh --sem-perguntas` levou
   19 s; a primeira construção da imagem (download das imagens base `python:3.13-alpine` e `caddy:2.10-alpine`) pode
   demorar mais. Não mudes a pasta `~/squad-office-lite` de sítio depois de instalar.

   **Alternativa com `git`:** `gh repo clone <dono>/<repo> ~/squad-office-lite`, `cd ~/squad-office-lite` e
   `./install.sh`. Se o repositório for privado, o `gh repo clone` usa a sessão do `gh` (um `git clone https://…` sem
   ela pede utilizador e palavra-passe e falha); depois volta ao token *fine-grained* (ver
   [Token do GitHub](#token-do-github)).
4. **Abre o endereço** que o instalador mostra no fim (`https://<o teu domínio>` ou `http://<IP da VPS>`) e entra com
   o utilizador e a palavra-passe que escolheste.
5. **Lança a primeira tarefa.** No `▦ Quadro`, cria um cartão no projecto «Exemplo» (por exemplo «Acrescentar um
   ficheiro CONTRIBUTING.md»), arrasta-o para «Em curso» e carrega em «Lançar». Em um ou dois minutos a squad aparece
   a trabalhar no escritório; no fim, o cartão passa para «À espera de …» ou «Feito» (o movimento é feito quando
   alguém abre o Quadro: se ninguém o abrir, o cartão fica em «Em curso» até lá).

**Para os teus projectos:** clona cada repositório em `~/projectos/<nome>` e acrescenta-o a `~/office-lite/office.yaml`
(secção `projectos`, ver [Referência](#officeyaml)). Depois reinicia o Office:
`docker compose -f ~/office-lite/docker-compose.yml restart office`. O runner lê a config a cada minuto, não precisa
de reinício. Se mudaste as `raizes`, corre outra vez `./install.sh` (não volta a perguntar nada).

### Actualizar

O mesmo `get.sh`, sem `--versao` (instala a última versão publicada; lê o aviso em «Instalação»):
```bash
curl -fsSL https://raw.githubusercontent.com/<dono>/<repo>/v0.1.2/get.sh | bash
```
A versão instalada passa para `~/squad-office-lite.anterior-<data>` e a nova fica em `~/squad-office-lite`.
`~/office-lite` (config, `.env`, credenciais e dados) não é tocada pelo `get.sh`; o `install.sh` só volta a
perguntar o que falta (sem `claude.env`, por exemplo, pergunta outra vez pelas credenciais: escolhe 2 ou 3 se usas o
token ou o login do `claude`). Para voltar atrás: `… | bash -s -- --versao <versão anterior>`. Apaga as pastas
`.anterior-*` quando já não precisares delas. Vê o que mudou no `CHANGELOG.md`.

### VPS nova só com root

Muitas VPS chegam só com o `root`. Cria um utilizador normal e passa a entrar com ele (troca `u` pelo nome que
quiseres):
```bash
adduser u                                   # pede a palavra-passe do novo utilizador
usermod -aG sudo u
install -d -m 700 -o u -g u /home/u/.ssh
install -m 600 -o u -g u /root/.ssh/authorized_keys /home/u/.ssh/authorized_keys
```
Sai e entra com `ssh u@<vps>`; daí em diante segue os passos acima com esse utilizador (não com o `root`).

### Token do GitHub

O `gh auth login` pelo browser dá acesso a todos os teus repositórios. Para a squad, é melhor um token
**fine-grained** (GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens) com:
- **Repository access:** só os repositórios dos projectos do Office (os de `~/projectos`);
- **Permissions:** **Contents** e **Pull requests** em **Read and write** (o *Metadata* só de leitura vem sempre);
  opcional, **Actions** e **Commit statuses** em **Read-only** para o `gh run`/`gh pr checks` da squad. Sem
  **Workflows**: a squad não consegue alterar `.github/workflows/` (de propósito);
- **Expiration:** uma data (ex.: 90 dias), e renova-o.

Entra com ele no `gh` de uma destas formas (o token nunca vai em argumentos de comandos):
- `gh auth login` e escolhe colar um token (*Paste an authentication token*);
- ou `gh auth login --with-token`, que lê o token do stdin: cola-o, Enter e `Ctrl-D`.

Depois corre `gh auth setup-git` (o `git push` passa a usar a mesma sessão) e confirma com `gh auth status`.

O manual do `gh` recomenda a variável `GH_TOKEN` para automação. No Lite não serve: o runner passa ao `claude` só uma
lista branca de variáveis (ver [Credenciais do Claude](#credenciais-do-claude)) e um `GH_TOKEN` no `runner.env` não
chega ao agente nem ao `git` do runner (só ao `gh` com que o runner confirma o PR). Usa a sessão guardada pelo
`gh auth login`.

Se o `squad-office-lite` vier de um repositório privado e o clonaste com outro acesso (a tua conta inteira, ou um
token com escrita), volta a entrar com o token *fine-grained* (`gh auth login`) e confirma com `gh auth status` que é
ele a sessão activa: com escrita no repositório do Office, a squad podia fazer push ao próprio Office.

### O que fazer se…

- **o `get.sh` pára com um erro.** Nada foi instalado e a versão anterior (se havia) fica onde estava.
  «sem terminal; usa --sem-perguntas»: a sessão não tem terminal para as perguntas (ex.: `ssh` sem `-t`); entra com
  `ssh -t` ou acrescenta `bash -s -- --sem-perguntas`. «não corras como root…»: ver
  [VPS nova só com root](#vps-nova-só-com-root). «o sha256 não confere; nada foi instalado»: o download veio
  corrompido ou alterado; corre de novo e, se repetir, não instales e reporta (ver [`SECURITY.md`](SECURITY.md)).
  «este instalador é para Ubuntu», «processador não suportado: …» e «falta curl» (ou `sudo`, `tar`, `sha256sum`) dizem
  o que falta.
- **«a porta 80 (ou 443) está ocupada».** Há outro servidor web na VPS. Vê qual é com `sudo ss -ltnp 'sport = :80'`
  e pára-o (ex.: `sudo systemctl disable --now apache2` ou `nginx`). Se for um **Traefik** que já usas, não o pares:
  instala com `./install.sh --proxy traefik --rede <rede do Traefik>` (precisa de domínio; o Traefik tem de ter o
  entrypoint `websecure` e um certresolver, por omissão `letsencrypt`, mudável com `--certresolver`).
- **o domínio não abre ou dá erro de certificado.** O DNS ainda não aponta para a VPS (pode demorar até umas horas).
  Confirma com `getent hosts <domínio>`; abre as portas 80 e 443 na firewall (`sudo ufw allow 80,443/tcp`). Quando o
  DNS estiver certo: `docker compose -f ~/office-lite/docker-compose.yml restart caddy`.
- **não tenho domínio / quero pôr um mais tarde.** Sem domínio o Office fica em `http://<IP>`: a palavra-passe passa
  sem cifra e as notificações no telemóvel não funcionam. Para acrescentar um domínio, muda `OFFICE_SITE` e
  `OFFICE_DOMINIO` em `~/office-lite/.env` para o domínio e corre `./install.sh` outra vez.
- **a tarefa termina logo em «erro».** Quase sempre são as credenciais do Claude: se o resultado falar de
  «credenciais do Claude», corrige o `~/office-lite/claude.env` como diz a mensagem (ex.: `chmod 600`) ou troca-as com
  `./install.sh --credenciais`. Se falar de «runner.settings inválido», corrige o ficheiro de `runner.settings` (JSON;
  `deny`/`allow`/`ask` têm de ser listas de textos; até 64 KB). Senão vê o fim de `~/office-lite/dados/tasks/<id da tarefa>/claude.log` (chave
  inválida, sem sessão iniciada, ou modo de permissões que a tua versão do `claude` não conhece; ver
  [Runner](#runner)). Sem `gh auth login` a tarefa acaba sem PR («incompleta»).
- **a squad diz que não pode correr um comando.** Com `runner.permission_mode: dontAsk` (omissão) só corre o que está
  em `runner.ferramentas`. O prompt de cada tarefa tem uma secção «Permissões», gerada dessa lista, que diz à squad o
  que pode correr e como (um comando por chamada, ficheiros só com Write/Edit, sem `&&`, pipes nem heredocs). Se o
  comando não estiver na lista, acrescenta-o no `office.yaml` (ex.: `- Bash(cargo *)`; ver [Runner](#runner)): as
  tarefas seguintes já o vêem.
- **o commit falha com «Please tell me who you are».** O git da VPS não tem nome nem e-mail para os commits. Corre
  `git config --global user.name "O teu nome"` e `git config --global user.email "tu@exemplo.com"` (ou outra vez
  `./install.sh`, que os pede se faltarem) e lança de novo a tarefa.
- **o instalador recusa a chave do Claude.** A chave de API começa por `sk-ant-` (sem `oat`) e vai na opção 1. Um
  token da subscrição (`sk-ant-oat01-…`) não se cola no instalador: escreve-o tu no `claude.env` (ver
  [Credenciais do Claude](#credenciais-do-claude)).
- **quero parar uma tarefa.**
  `python3 ~/squad-office-lite/runner/office_runner.py parar <id da tarefa>` (o id é o nome da pasta da tarefa em
  `~/office-lite/dados/tasks/`). Mata o `claude` dessa tarefa, fecha-a em «erro» com «parada pelo utilizador» e deixa
  uma marca (`parar` na pasta da tarefa) para o runner nunca a retomar. Uma tarefa já terminada fica como está.
  Funciona mesmo com o `office.yaml` inválido. Um SIGTERM sem `parar` (ex.: reboot da VPS) não fecha a tarefa: fica
  para o runner a retomar.
  `systemctl --user stop office-runner.timer` não pára tarefas: só deixa de haver passagens novas (as tarefas correm
  fora da unit, por desenho: `KillMode=process`).
- **a tarefa acabou mas o cartão não mudou de coluna.** O cartão só passa para «À espera de …» ou «Feito» quando
  alguém abre o Quadro (o movimento é feito no `GET /api/quadro`). Abre o `▦ Quadro`.
- **carrego em «Lançar» e nada acontece.** O runner (timer de minuto a minuto) não está a correr. Vê
  `systemctl --user status office-runner.timer` e `journalctl --user -u office-runner -n 50`. Se o instalador avisou
  sobre *linger*, corre `sudo loginctl enable-linger $USER` (sem isto o runner pára quando sais do SSH). Para o ligar à
  mão: `systemctl --user enable --now office-runner.timer`. Para testar uma passagem:
  `python3 ~/squad-office-lite/runner/office_runner.py --uma-vez`.
- **quero mudar a palavra-passe.** `cd ~/squad-office-lite && ./install.sh --palavra-passe`.
- **quero trocar a chave de API do Claude.** `cd ~/squad-office-lite && ./install.sh --credenciais`. O token da
  subscrição trocas tu no ficheiro (ver [Credenciais do Claude](#credenciais-do-claude)).
- **recebi «o office.yaml mudou» no journal ou no Telegram.** O runner avisa uma vez por cada mudança do
  `office.yaml`. Se não foste tu, revê-o já (`ferramentas`, `permission_mode`, `settings`, `projectos`) e vê as
  tarefas recentes: pode ter sido um programa corrido pela squad (ver [Segurança](#segurança)).
- **o Office mostra uma faixa «config inválida».** O `office.yaml` tem um erro; o Office usa a config de exemplo até o
  corrigires. `python3 ~/squad-office-lite/officecfg.py validar ~/office-lite/office.yaml` diz o erro (a linha, nos
  erros de sintaxe; o campo, nos outros).

---

## Referência

### `office.yaml`

`~/office-lite/office.yaml` (0600) é a única config. O instalador cria-o a partir de `office.example.yaml` só se não
existir. O Office lê-o no arranque (com erro: usa o exemplo e mostra um aviso); o runner e o instalador recusam uma
config inválida. Validar: `python3 officecfg.py validar FICH`.

**Subconjunto de YAML aceite** (lido por `officecfg.py`, sem dependências; o resto dá erro com o número da linha):
indentação só com espaços, em múltiplos de 2 (tabs = erro); comentários `#` numa linha própria ou depois de um valor;
`chave: valor` com chaves em minúsculas (`[a-z][a-z0-9_-]*`), sem chaves repetidas nem desconhecidas; listas
`- valor` ou `- chave: valor` (as chaves seguintes alinhadas); listas numa linha só de valores simples (`[feat, fix]`);
valores `"…"`, `'…'`, simples, inteiros e `true`/`false`. **Não há:** âncoras, tags, `|`/`>`, `{…}`, `null`/`~`,
valores em várias linhas, vários documentos (`---`). `~/x` é texto (expandido para a casa do utilizador nas raízes).

| chave | o quê (omissão) |
|---|---|
| `versao` | só `1` |
| `marca` | nome no título e no topo, 1–40 («Squad Office») |
| `utilizador.nome` / `.contacto` | o teu nome (coluna «À espera de …», decisões) / e-mail opcional para o Web Push |
| `fuso` | fuso IANA, ex. `Europe/Lisbon` (`UTC`); horas, totais do dia, tecto diário, ids das tarefas |
| `raizes.dados` | onde vivem os dados do Office (`~/office-lite/dados`) |
| `raizes.projectos` | onde estão os repositórios (`~/projectos`) |
| `raizes.claude_projects` | sessões do Claude Code (`~/.claude/projects`) |
| `squads[]` | 1–6: `id`, `nome`, `lead` (papel do líder, `tech-lead`), `personagem`, `formato` `worktree` (lançável, omissão) ou `sessao` (só acompanhada); `tipos` (`[feat, fix]`) e `modos` (`[goal, task]`) só em `worktree` |
| `projectos[]` | 1–30: `id` (minúsculas), `nome`, `glifo` (1–2 caracteres, «◆»), `repo` (pasta em `raizes.projectos`, omissão = `id`) |
| `colunas` | rótulos de `ideias`, `a-fazer`, `em-curso`, `a-espera`, `feito` (chaves e ordem fixas), 1–30; sem `a-espera`: «À espera de <`utilizador.nome`>» (um valor vazio não valida) |
| `biblioteca[]` | 0–12 fontes: `id`, `rotulo`, `pasta` (relativa a `<dados>/biblioteca`), `media` (`false`), `tipo` opcional (`relatorios`, `estudos`, `planos`, `conhecimento`, `textos`) |
| `runner` | `tecto_diario` 1–100 (10), `paralelo` 1–4 (1), `rotacao_dias` 1–365 (14), `permission_mode` `dontAsk` (omissão), `auto`, `acceptEdits`, `default` ou `bypassPermissions`; `settings` (`""`, caminho absoluto de um JSON juntado às negações por omissão); `ferramentas` 0–100 regras para `--allowedTools` (`Nome` ou `Nome(regra)`, sem espaços nem vírgulas fora dos parênteses, sem `-` inicial; omissão: a lista em [Runner](#runner)); `max_usd` opcional, teto de custo em USD por arranque do `claude` (`--max-budget-usd`), número > 0 e ≤ 1000 com até 2 casas (`5`, `2.5`); `modelo` opcional, `--model` (alias como `sonnet` ou nome completo; letras, dígitos, `.`, `_`, `-`, até 64, sem `-` inicial). Sem as duas: nenhuma flag (o `claude` usa as suas omissões) |

Lançáveis = cada squad `worktree` × cada projecto.

### Pastas

```
~/squad-office-lite/            o repositório (código; o compose constrói a imagem daqui)
~/office-lite/                  instalação (install.sh --casa DIR para outra)
  office.yaml  .env  docker-compose.yml (gerado)
  claude.env                    credenciais do Claude (0600, só o runner as lê; nunca montado no contentor)
  runner.env                    opcional (0600): Telegram e, em alternativa ao claude.env, a credencial do Claude
  dados/                        raizes.dados
    tasks/<id>/                 uma tarefa: meta.json, heartbeat, task.md, claude.log, SPEC/PLAN/QA.md, parar
    tasks/inbox/                briefs soltos (*.md) → cartões em «A fazer»
    squads/<squad>/<id>/        metas das squads formato sessao
    decisoes/                   pedidos de aprovação (*.md)
    decisoes-fila/              decisões vindas do Office (JSON)
    quadro/cartoes.json         cartões manuais
    quadro-lancar/              pedidos de lançamento
    push/                       chaves e subscrições Web Push (0700)
    biblioteca/<pasta>/         fontes da Biblioteca
    runner/                     lock, histórico, logs, settings/ e office.yaml.sha256 do runner (só no host)
~/projectos/exemplo/            repositório de exemplo criado pelo instalador
```
No contentor, `tasks`, `claude_projects`, `squads`, `decisoes` e `biblioteca` são montadas só de leitura; o Office só
escreve em `decisoes-fila`, `quadro`, `quadro-lancar`, `push` e nas miniaturas (num `tmpfs`). O contentor corre com o
uid/gid de quem instalou (`OFFICE_UID`/`OFFICE_GID` no `.env`), `read_only`, sem portas publicadas.

### Runner

`runner/office_runner.py` corre no host (Python ≥ 3.10, só stdlib), uma passagem por minuto pelo timer systemd de
utilizador `office-runner.timer` (`systemctl --user …`, logs em `journalctl --user -u office-runner`). Precisa de
*linger* para correr sem sessão aberta.

```
office_runner.py --uma-vez [--config FICH]     uma passagem (omissão: $OFFICE_CONFIG ou ~/office-lite/office.yaml)
office_runner.py correr TASK_ID [--retomar]    interno: corre o claude numa tarefa já preparada
office_runner.py parar TASK_ID                 pára uma tarefa «a correr» (fecha em erro; nunca é retomada)
```
`correr` e `parar` aceitam também `--config FICH`.
Cada passagem (com lock, nunca duas ao mesmo tempo): confirma se o `office.yaml` mudou → aplica decisões → retoma tarefas interrompidas (até 2 vezes) →
trata a fila do Quadro → arquiva pedidos antigos.

**Lançar:** revalida o pedido contra a config; confirma o tecto diário (`tecto_diario`, no fuso da config) e o
`paralelo` (acima dele o pedido espera); cria `git worktree` em `<repo>/.worktrees/<task_id>` num branch
`<kind>/<slug>` a partir do ramo por omissão do `origin` (ou do actual, sem remoto); grava `task.md` e `meta.json`;
responde `{"task_id"}` e corre o `claude -p` destacado (log em `<dados>/runner/logs/<task_id>.log`, saída do claude em
`tasks/<id>/claude.log`; os segredos do `runner.env` chegam-lhe pelo stdin, ver
[Credenciais do Claude](#credenciais-do-claude)). O título e o brief vão só por ficheiro e stdin, delimitados como dados do utilizador. Limite
de 3 h (`task`) ou 8 h (`goal`). Estado final: `concluída`, `parcial (PR draft)`, `incompleta` ou `erro`.

**Travões:**
- `runner.permission_mode`: `dontAsk` (omissão: o `claude -p` recusa sem perguntar tudo o que não estiver em
  `runner.ferramentas`), `auto` (o próprio `claude` decide o que é seguro; pode não existir na tua versão ou conta),
  `acceptEdits` (aceita edições de ficheiros; comandos fora de `ferramentas` são recusados), `default` (pede
  confirmação: sem humano, recusa), `bypassPermissions` (perigoso: tudo sem confirmação, só os proibidos e as
  negações travam).
- `runner.ferramentas` (`--allowedTools`), por omissão: `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Agent`, `TodoWrite`,
  `Bash(git *)`, `Bash(gh pr create|view|checks|edit|list|ready *)`, `Bash(gh run *)` e `Bash(<cmd> *)` para
  `python`, `python3`, `pytest`, `uv`, `pip`, `npm`, `npx`, `node`, `pnpm`, `yarn`, `make`, `ruff`, `ls`, `wc`,
  `grep`, `diff`, `mkdir`, `touch`, `pwd`, `which`, `sort`. Chega para a squad ir até ao PR num projecto Python ou
  Node; para outro (ex.: `cargo`, `go`), acrescenta `Bash(cargo *)`. Ao pôr a chave substituis a lista toda; `[]` não
  passa `--allowedTools`. Desde a 0.1.2 a omissão não tem `WebSearch`, `WebFetch`, `Bash(cat *)`, `Bash(head *)`,
  `Bash(tail *)`, `Bash(cp *)`, `Bash(mv *)` nem `Bash(echo *)` (o Read, o Grep, o Write e o Edit fazem o mesmo);
  quem as quiser escreve a lista completa com elas.
- A lista não é um muro: autorizar `python3`, `npm`, `make` ou um script do projecto é autorizar um programa que faz
  tudo o que o teu utilizador pode fazer (ver [Segurança](#segurança)).
- **Negações por omissão** (sempre, em qualquer modo): o runner passa ao `claude` um `--settings` em JSON (num
  ficheiro 0600 em `<dados>/runner/settings/`, não no argv, apagado quando a tarefa fecha; os que ficarem saem com
  a rotação, `rotacao_dias` depois) com `permissions.deny`, que ganha ao `--allowedTools`:
  - leitura (`Read`) de `claude.env`, `runner.env` e `.env` da casa (`~/office-lite`), de qualquer `.env` e `.env.*`
    (também esconde o `.env.example` dos projectos), de `~/.config/gh/**`, `~/.ssh/**`,
    `~/.claude/.credentials.json`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`,
    `~/.config/systemd/**` e `/proc/**` (o ambiente dos outros processos);
  - escrita (`Edit`, que cobre o Write) desses três ficheiros da casa, do `office.yaml`, de `quadro-lancar/`,
    `decisoes-fila/`, `decisoes/`, `quadro/` e `runner/` dentro de `raizes.dados`, do código do Lite
    (`~/squad-office-lite/**`), do ficheiro de `runner.settings`, dos cinco de `~` acima (credenciais e
    `~/.config/systemd/**`) e do que o teu utilizador executa: `~/.claude/settings.json`,
    `~/.claude/settings.local.json`, `~/.bashrc`, `~/.profile`, `~/.bash_profile` e `~/.local/bin/**`;
  - `blockWritesOutsideWorkingDirectories: true`: o Write e o Edit só escrevem no worktree da tarefa e na pasta dela
    (`tasks/<id>/`). Só nas versões recentes do `claude`; as regras acima valem em todas.

  Os caminhos saem da tua config, não são fixos. Valem para as ferramentas do Claude Code e para os comandos de
  ficheiros que ele reconhece no Bash (`cat`, `head`, `tail`, `sed`, redirecções); **não** valem para um `grep -r`
  nem para o que corre dentro de `python3`, `npm`, `node`, `make` ou de um script.
- Sempre proibidos (`--disallowedTools`, ganham ao `--allowedTools`): `gh pr merge`, `gh pr close`,
  `gh repo delete`, `gh release`, `git push --force`/`-f`, `git push origin main`/`master`. São regras de texto sobre
  o início do comando: **não** apanham `git -C . push origin main`, `git push origin HEAD:main`, um `+refspec`
  (`git push origin +x:main`) nem `git -c … push`. A protecção a sério é no GitHub: no repositório de cada projecto,
  *Settings* → *Branches* (ou *Rules*), protege o ramo por omissão (exigir pull request, sem push directo nem
  force-push). O token *fine-grained* sem a permissão *Administration* não consegue desligar essa regra.
- «Marca-o pronto (`gh pr ready`)» só aparece no prompt se `gh pr ready` estiver autorizado (regra que o cubra em
  `ferramentas`, ou `auto`/`bypassPermissions`); senão a squad deixa o PR em rascunho.
- `runner.settings`: caminho absoluto para um `settings.json` do Claude Code (hooks, `permissions` próprias, sandbox).
  **Junta-se** às negações por omissão, não as substitui: as listas `deny`, `allow` e `ask` são concatenadas (as da
  base primeiro), as outras chaves ficam como as escreveste, e `blockWritesOutsideWorkingDirectories` fica sempre
  `true`. Ilegível, não regular, mais de 64 KB, JSON inválido ou `deny`/`allow`/`ask` que não sejam listas de textos
  ⇒ a tarefa fica em «erro» com «runner.settings inválido: <regra>» e o `claude` não arranca. Recomendado com `auto`.
- **Sandbox do Claude Code (opcional, recomendado):** isola o Bash e os programas que ele lança (incluindo
  `python3`, `npm` e scripts): escrita só no worktree, na pasta da tarefa e em temporários; leitura do disco todo,
  salvo o que puseres em `sandbox.filesystem.denyRead`. No Linux precisa de `sudo apt-get install -y bubblewrap socat`.
  Num `runner.settings`:
  ```json
  {"sandbox": {"enabled": true, "failIfUnavailable": true}}
  ```
  Sem `failIfUnavailable: true`, se faltar o `bubblewrap` ou o `socat` o `claude` corre **sem** sandbox. O Lite não
  testa o sandbox: confirma com uma tarefa curta que o `git push` e o `gh pr create` continuam a passar.
- `runner.max_usd` (`--max-budget-usd`): teto de custo por arranque do `claude`, estimado pelo próprio `claude` (pode
  passar um pouco). Cada retoma é um arranque novo: com 2 retomas, uma tarefa pode gastar até 3× o teto.
  `runner.modelo` (`--model`): o modelo da squad (ex.: `sonnet`); o nome não é validado no arranque, um nome errado
  aparece no `claude.log`.

### Credenciais do Claude

O runner lança o `claude` com uma credencial numa linha de `~/office-lite/claude.env` (ao lado do `office.yaml`).
Duas vias:

- **Chave de API (recomendada):** cria-a em <https://console.anthropic.com> (Settings → API Keys). Linha
  `ANTHROPIC_API_KEY=sk-ant-…`. Paga por uso, à parte da subscrição. O instalador pede-a (opção 1) e escreve o
  `claude.env` por ti.
- **Token da subscrição** (Claude Pro/Max): **o instalador não o pede nem o guarda**; escreves tu o ficheiro. Gera o
  token na própria VPS, a partir do teu computador (`ssh -t <utilizador>@<vps> claude setup-token`: abre um link que
  confirmas no browser) e copia a linha que começa por `sk-ant-oat01-` (não o código que o browser mostra). Se o
  `ssh` disser `claude: command not found`, usa o caminho completo:
  `ssh -t <utilizador>@<vps> '~/.local/bin/claude setup-token'`. Depois, na VPS:
  ```bash
  install -m 600 /dev/null ~/office-lite/claude.env
  nano ~/office-lite/claude.env
  ```
  e escreve uma linha só, `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-…`, com o teu token. Nunca ponhas o token em
  argumentos de comandos (ex.: `echo sk-ant-… > …`): fica no histórico da shell e na lista de processos. Usa os
  limites da tua subscrição.

  Em vez do `claude.env` podes pôr a mesma linha no `~/office-lite/runner.env` (também 600, ao lado do Telegram). Se
  houver os dois, ganha o `claude.env`. Sem `claude.env` e com `ANTHROPIC_API_KEY=` ou `CLAUDE_CODE_OAUTH_TOKEN=` no
  `runner.env`, o instalador não pergunta nada (diz que usa o `runner.env`; só confirma que a chave existe, não lê o
  valor, e recusa um `runner.env` que não seja um ficheiro normal teu com `chmod 600`); `--credenciais` mostra a
  pergunta na mesma.

O instalador pergunta qual (1 = chave de API, omissão; 2 = token, que só mostra estas instruções e não lê nada; 3 =
nenhuma, se já entraste com `claude` na VPS). A chave de API é lida sem aparecer no ecrã e tem de começar por `sk-ant-`
(só letras, números, `_` e `-`); um token da subscrição colado ali é recusado. O runner lê o `claude.env` (escrito
pelo instalador ou por ti) com as mesmas regras e mais a do token (`sk-ant-oat01-` seguido de letras, números, `_` ou
`-`): fora do formato, a tarefa fica em «erro» com «credenciais do Claude inválidas» e a regra, nunca o valor. O
ficheiro tem de ter modo `0600` e ser teu; com permissões mais abertas ou outro dono o instalador e o runner
recusam-no (a tarefa fica em «erro» a pedir `chmod 600`), nunca o corrigem sozinhos. Sem `claude.env` nem credencial
no `runner.env`, o `claude` usa o login que já tiver. Para trocar a chave de API: `./install.sh --credenciais` (com a
opção 2 ou 3, um `claude.env` que já exista fica como está).
Sem perguntas: `OFFICE_CLAUDE_CRED=api|oauth|nenhuma` e, com `api`, `OFFICE_CLAUDE_CRED_FILE=<ficheiro com a chave>`
(sem `OFFICE_CLAUDE_CRED` fica `nenhuma`, com aviso; com `oauth` só mostra as instruções e ignora o
`OFFICE_CLAUDE_CRED_FILE`). `OFFICE_CLAUDE_CRED` não vazio vale como `--credenciais` (escolhe mesmo com credencial no
`runner.env` ou um `claude.env` já existente).

**Ambiente do `claude` (lista branca):** o `claude` recebe só `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `USER`,
`LOGNAME`, `SHELL`, `TERM`, `TMPDIR`, `TZ` (as que existirem no runner), `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` e
uma credencial: a do `claude.env`; sem ele, `ANTHROPIC_API_KEY` ou, se não houver, `CLAUDE_CODE_OAUTH_TOKEN` do
`runner.env`. Nunca as duas. O runner não passa `OFFICE_*` (o token do Telegram) nem `GH_TOKEN`/`GITHUB_TOKEN` ao
`claude` (nem proxies ou `NODE_OPTIONS`) e não deixa ler o seu próprio ambiente em `/proc`. Para o `gh`, usa
`gh auth login` (ver [Token do GitHub](#token-do-github)).

**Segredos do `runner.env`:** o Telegram, a credencial do Claude e `GH_TOKEN`/`GITHUB_TOKEN` saem do ambiente do
runner logo no arranque. Os filhos `git`, `gh` e `claude` recebem só a lista branca acima (o `git` também
`GIT_TERMINAL_PROMPT=0`); o `claude` recebe ainda a credencial e os `gh` do runner (confirmar o PR) o `GH_TOKEN`/
`GITHUB_TOKEN`, se lá estiverem. O `correr` destacado recebe estes segredos por um pipe no stdin, não pelo ambiente.
Um `office_runner.py correr` lançado à mão usa só o seu próprio ambiente.

O valor da credencial só vai no ambiente do processo `claude` (nunca em argumentos, `meta.json`, `claude.log`, logs ou
Telegram). O contentor do Office não monta o `claude.env` (o gerador do compose recusa raízes que o incluíssem).

**Telegram (opcional):** em `~/office-lite/runner.env`, `OFFICE_TELEGRAM_TOKEN=…` e `OFFICE_TELEGRAM_CHAT=…` → aviso
no fim de cada tarefa, em falhas e no tecto diário. Sem eles não faz nada.

### Aprovações (decisões, ADR-001)

Pedidos de decisão são `*.md` em `<dados>/decisoes/` (escritos por ti ou por um agente fora do runner: as squads
lançadas pelo Office têm a escrita nesta pasta negada por omissão; ficheiro regular, nome
`[\w.-]+\.md`, nunca `README.md`, até 1 MiB), com frontmatter no início:
```
---
estado: pendente
criado: 2026-10-31
---
# Título (senão o nome do ficheiro)
## O quê
1.º parágrafo = resumo
## Datas e orçamento
texto
## Decisões
- [P1] Tecto da campanha 145 €
- [P2] Publicar a 1/11 → aprovado (2026-10-31)
```
- Pendente = `estado` começa por `pendente` ou `parcial` (minúsculas). Contam só as secções com o título exacto
  `## Decisões`; linha `- [id] texto`, id `[A-Za-z0-9][A-Za-z0-9._-]{0,19}`. Sem linhas válidas → uma decisão `*`.
- ` → aprovado (AAAA-MM-DD)` / ` → recusado (AAAA-MM-DD)` no fim da linha = decisão já tomada.
- O corpo só sai a pedido (`GET /api/aprovacao?fonte=geral&ficheiro=`), mascarado.

**Envio** `POST /api/decisao` (`Content-Type: application/json`, mesmo `Origin`, `token` do `/api/state`):
`{"token","fonte","ficheiro","id","accao":"aprovar|recusar|comentar","nota","sha256"}` → `202`. Erros: 400 (campos
inválidos, nota em falta), 403 (Content-Type, origem, token), 409 (pedido não pendente, decisão já tomada ou recebida,
`sha256` diferente do actual), 413 (corpo > 8 KB), 503 (fila sem escrita).

**Fila (contrato v1):** um ficheiro `AAAAMMDDTHHMMSS-<8 hex>.json` por decisão em `<dados>/decisoes-fila/`, escrita
atómica, chaves exactas:
```json
{"v": 1, "criado": "2026-10-31T12:00:00+00:00", "fonte": "geral", "ficheiro": "2026-10-23-x.md",
 "id": "P1", "accao": "aprovar", "nota": "", "sha256": "<sha256 do .md que o utilizador viu>",
 "ip": "<último X-Forwarded-For ou IP da ligação>", "ua": "<User-Agent, ≤ 120>"}
```
**O que o Office nunca faz:** não edita nem apaga os `.md` (montados só de leitura), não aplica nada, não executa
comandos e não apaga nem move ficheiros da fila. Quem aplica é o runner: confirma o `sha256` (só aplica sobre a versão
que viste, com uma excepção, abaixo), marca a linha (`→ aprovado (…)`), actualiza o `estado`, acrescenta `## Decisão de <nome> (…, via Office)`
e move o JSON para `feito/`; se não puder aplicar, move-o com um `<nome>.motivo.txt` e o `.md` fica intacto.

**Excepção do `sha256`:** o runner também aplica uma decisão cujo `sha256` não é o do `.md` actual se (1) o `.md`
actual for exactamente a última versão que o próprio runner escreveu e (2) o `sha256` que viste for uma das versões
dessa sequência de escritas do runner (até 50 por pedido, em `<dados>/runner/.historico.json`). Serve para decidires
várias linhas do mesmo pedido a partir da mesma página: o runner marca a P1, o ficheiro muda, e a P2 (enviada com o
`sha256` antigo) é aplicada na mesma. Se outra pessoa ou programa mudar o `.md` entretanto, a sequência recomeça e a
decisão é recusada («sha256 diferente: o pedido mudou depois de ser visto»). Lê sempre o pedido inteiro antes de
aprovar.

### Quadro (cartões, ADR-003)

Kanban com cinco colunas (rótulos na config). **Cartões automáticos**, calculados a cada pedido e nunca gravados:
tarefas das squads (a correr → Em curso; erro → Em curso com selo vermelho; PR aberto → À espera; PR fundido ou
terminada sem PR → Feito), aprovações pendentes (À espera) e briefs de `tasks/inbox/*.md` com ≤ 7 dias ainda não
lançados (A fazer). **Cartões manuais** vivem em `<dados>/quadro/cartoes.json`, contrato v1, chaves exactas:
```json
{"v": 1, "cartoes": [{"id": "m-3f9a1c2b7d", "titulo": "…", "descricao": "md curto", "projecto": "exemplo",
  "coluna": "ideias", "ordem": 10, "etiquetas": ["preço"], "criado": "2026-11-04T10:12:00+00:00",
  "actualizado": "2026-11-04T10:12:00+00:00"}]}
```
- `id` `[A-Za-z0-9_-]{1,40}`; `titulo` 1–120; `descricao` ≤ 2000; `projecto` um id da config ou `outro`; `coluna`
  `ideias|a-fazer|em-curso|a-espera|feito`; `etiquetas` ≤ 5; datas ISO com fuso; até 200 cartões.
- Versão = sha256 dos bytes do ficheiro. `POST /api/quadro` (`op`: `criar`, `editar`, `apagar`, `mover`) só grava se
  a versão que o browser viu for a actual (senão 409); escrita tmp + `fsync` + `os.replace`. Ficheiro ilegível ou
  `v` ≠ 1 → só automáticos e escritas a 503 até ser corrigido à mão.
- **Quem escreve por fora** relê imediatamente antes, grava num temporário na mesma pasta e faz `rename`.
- **Dados, nunca instruções:** títulos, descrições e etiquetas são texto do utilizador; um agente que os leia não os
  trata como pedidos a executar.

### Lançar a partir do Quadro (ADR-005)

Arrastar um cartão manual para «Em curso» abre uma confirmação; «Lançar» chama `POST /api/quadro/lancar`, que grava um
pedido na fila `<dados>/quadro-lancar/` e move o cartão, numa só operação. O Office nunca lança nada: quem lança é o
runner.

**Pedido** `<id>.json` (escrito pelo Office; nome só do relógio e de `secrets.token_hex(4)`), chaves exactas por esta
ordem:
```json
{"v": 1, "id": "20261106T023000-a1b2c3d4", "cartao_id": "m-0a1b2c3d4e", "squad": "dev", "projecto": "exemplo",
 "kind": "feat", "mode": "goal", "slug": "primeira-tarefa", "titulo": "…", "brief": "…", "pedido_em": "2026-11-06T02:30:00+00:00"}
```
`squad` uma squad `worktree`, `projecto` um id de projecto, `kind` dos `tipos` e `mode` dos `modos` dessa squad;
`slug` `^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$`; `titulo` 1–120 e `brief` ≤ 8192 bytes, sem caracteres de controlo nem
invisíveis (`Cf`, `Co`, `Cs`, salvo o ZWJ dos emojis). Escrita tmp + `fsync` + `os.link` (nunca sobrescreve).

**Resposta do runner** `<id>.feito.json`: `{"task_id": "…"}` ou `{"erro": "<texto>"}` (tmp + `rename`). **Marca do
Office** `<id>.movido.json` `{"v": 1, "coluna": "feito"|"a-espera"|null, "em": ISO}`: o movimento do fim já foi feito.

**Contrato do runner** (o Office só garante o que escreve; quem lê a fila não confia nela):
- só são pedidos os nomes `^\d{8}T\d{6}-[0-9a-f]{8}\.json$` sem `.feito.json`; abre com `O_NOFOLLOW`, confirma com
  `fstat` que é ficheiro regular, lê ≤ 32 KB;
- revalida tudo contra a config (chaves, `id` = nome, listas fechadas, slug, `pedido_em` com fuso); falha → `{"erro"}`;
- `titulo` e `brief` só por ficheiro ou stdin, nunca em argv nem por shell, e no prompt delimitados como dados do
  utilizador, nunca como instruções;
- tecto diário: acima dele responde `{"erro": "tecto diário"}`;
- rotação: os trios com `.movido.json` há mais de `rotacao_dias` vão para `feito/` pela ordem `<id>.json` →
  `<id>.feito.json` → `<id>.movido.json` (o Office só lê a raiz da fila).

**`POST /api/quadro/lancar`**: chaves exactas `token, versao, cartao_id, posicao, squad, projecto, kind, mode, slug,
titulo, brief`. 413 (> 32 KB), 403 (Content-Type, origem, token), 400 (`squad inválida`, `projecto inválido`,
`tipo inválido`, `modo inválido`, `slug inválido`…), 503 (fila sem escrita, `cartoes.json` inválido), 409 (versão
`conflito` ou `já lançado`), 404 (cartão manual inexistente), 429 `{"error": "fila cheia"}` (10 pedidos pendentes).
Sucesso: **202** `{id, versao, movido, cartoes}`. O `GET /api/quadro` acrescenta `lancar`, `dev_projectos` (ids
lançáveis) e, em cada cartão manual, `lancamento` (`a-lancar`, `ligado` ou `erro`); no fim da execução move o cartão,
uma vez só, para «À espera de …» (erro ou PR por rever) ou «Feito».

### Outras partes

- **Biblioteca** (`▤`): ficheiros das fontes de `biblioteca` e os `SPEC/PLAN/QA.md` das tarefas, para ver e
  descarregar no telemóvel. Só extensões permitidas (imagem, vídeo, áudio, `md txt csv pdf`), sem ficheiros ocultos,
  segredos ou symlinks, textos mascarados; miniaturas com Pillow num `tmpfs` de 128 MB. Fontes com `media: true` são
  agrupadas em lotes (uma pasta = um lote; destaque em `final/`).
- **Notificações no telemóvel** (⚙ Definições): Web Push (VAPID, `cryptography`) para fins de tarefa, PR aberto,
  tarefas paradas, aprovações e fases; precisa de HTTPS (domínio). Chaves e subscrições em `<dados>/push/`. As
  mensagens levam só squad, projecto, estado e ids, nunca o texto das tarefas.
- **Barra de progresso:** fases SPEC → PLAN → CODE → QA → PR nos `goal` e estimativa pela mediana das últimas
  tarefas concluídas da mesma squad e modo.

### Segurança

Para reportar uma falha, ver [`SECURITY.md`](SECURITY.md) (reporte privado no GitHub; nunca numa issue pública).

- A autenticação é só o **basic auth** do Caddy (ou do Traefik), com hash bcrypt no `.env` (0600). O Office não tem
  login próprio; o `token` do `/api/state` só trava CSRF.
- Com o Caddy, o Office está numa rede do compose só com o Caddy e não publica portas. Com `--proxy traefik` fica na
  rede partilhada do Traefik: qualquer contentor nessa rede chega ao Office sem palavra-passe.
- O proxy põe um segredo aleatório (`OFFICE_PROXY_SEGREDO` no `.env`, gerado pelo instalador) no cabeçalho
  `X-Office-Segredo` e o Office recusa (403) quem não o traz, excepto `/healthz`: chegar à rede não basta. Com `--proxy traefik` o segredo fica numa label do contentor: a API/dashboard
  do Traefik e o socket do Docker não podem estar acessíveis a outros contentores dessa rede.
- **Sem domínio não há HTTPS:** a palavra-passe passa em claro e o Web Push não funciona. Usa um domínio.
- As credenciais do Claude (`claude.env`, 0600) ficam só no host, para o runner: o Office não as monta, não as
  guarda nem as mostra. O instalador não pede o token da subscrição. Os segredos do `runner.env` saem do ambiente do
  runner no arranque; o `git`, o `gh` e o `claude` recebem só a lista branca do ambiente (o `claude` mais a
  credencial, o `gh` mais o `GH_TOKEN` se lá estiver; ver [Credenciais do Claude](#credenciais-do-claude)).
- O runner corre o `claude` com `--permission-mode dontAsk`, a lista de ferramentas pré-autorizadas
  (`runner.ferramentas`), os proibidos fixos e as negações por omissão; `auto`, `acceptEdits` e `bypassPermissions`
  abrem mais (ver [Runner](#runner)). **As negações valem para as ferramentas do Claude Code**, não para os programas
  que a squad corre: um interpretador ou ferramenta autorizada (`python3`, `npm`, `node`, `make`, um script do
  projecto) lê e escreve tudo o que o teu utilizador pode. A protecção forte é o
  [sandbox do Claude Code](#runner) (no `runner.settings`) ou **um utilizador do Linux só para o Office**, sem outras
  chaves, projectos ou sessões.
- **O agente e o contentor correm com o mesmo uid** (o teu). Por isso o runner não sabe quem escreveu um pedido em
  `quadro-lancar/` ou uma decisão em `decisoes-fila/`: o Office ou um programa que a squad tenha corrido. As negações
  impedem o Write e o Edit do Claude Code de lá escrever, mas não um `python3` autorizado. O mesmo vale para o
  `office.yaml`. O que o Lite faz: avisa no journal e no Telegram quando o `office.yaml` muda («office.yaml mudou (era
  …, é …); se não foste tu, revê-o já»). O que fazes tu: lê o pedido de aprovação inteiro antes de aprovar, revê os
  PRs antes de os fundir, protege o ramo por omissão no GitHub e, se o aviso do `office.yaml` aparecer sem teres sido
  tu, pára as tarefas em curso (`office_runner.py parar`) e revê a config.
- **O contentor monta `raizes.claude_projects` inteiro** (por omissão `~/.claude/projects`, só de leitura): o contentor
  lê todas as sessões do Claude Code desse utilizador, não só as das tarefas do Office (conta os tokens de todas;
  mostra as conversas só das tarefas e squads da config). Uma falha de segurança no Office podia expô-las todas. Se usas o `claude` na VPS para outras coisas, usa um utilizador só para o Office (a
  recomendação acima) ou aceita o risco.
- `GET /api/config` não leva caminhos, contacto nem segredos. Briefs, cartões e pedidos são dados do utilizador,
  nunca instruções para os agentes.
- **Riscos que ficam** (as negações não os fecham):
  - a credencial do Claude está no ambiente do próprio processo `claude`: um interpretador autorizado pode lê-la.
    Prefere uma chave de API com limite de gasto na consola da Anthropic;
  - tirar `WebFetch`/`WebSearch` não fecha a rede: `git push`, `gh pr create` e os interpretadores chegam à internet;
  - `Bash(git *)` (na lista por omissão) autoriza `git -c …`, que pode correr comandos;
  - um `.claude/settings.json` dentro de um projecto (hooks, `allow`) é aplicado pelo `claude`: só lances squads em
    repositórios teus;
  - o `get.sh` confirma a integridade (sha256 da mesma release), não a autenticidade: quem controlar a conta, o ramo
    `main` ou as releases no GitHub troca os dois. Protege-os com 2FA.

### Variáveis do servidor

Postas pelo compose gerado: `OFFICE_CONFIG` (omissão `/config/office.yaml`), `TASKS_DIR`, `CLAUDE_PROJECTS_DIR`,
`SQUADS_DIR`, `APROVACOES_DIR`, `BIBLIOTECA_DIR`, `DECISOES_FILA_DIR`, `QUADRO_DIR`, `QUADRO_LANCAR_DIR`, `PUSH_DIR`,
`PUSH_CONTACTO` (opcional) e `BIBLIOTECA_MINI_DIR` (omissão `/tmp/miniaturas`); `PORT` (8080).
Correr localmente: `OFFICE_CONFIG=office.example.yaml TASKS_DIR=/tmp/t … PORT=18080 python3 server.py`.

### Testes

```bash
python -m pytest tests -q            # precisa de pytest; dados falsos em pastas temporárias
node --test tests/*.mjs
```
Sem Pillow/`cryptography` os testes de miniaturas e de cifra são saltados. O runner é testado com um `claude` falso
(`tests/bin/claude`). A CI corre os testes, o `shellcheck` do instalador e uma instalação completa de fumo; não faz
deploy nem publica imagens.

## Contribuir

Issues e pull requests são bem-vindos (falhas de segurança não: ver [`SECURITY.md`](SECURITY.md)). Antes de abrir um
PR, corre os testes:
```bash
python -m pytest tests -q
node --test tests/*.mjs
shellcheck install.sh get.sh scripts/*.sh
```
Regras do projecto: PT-PT no código, na interface e nos textos; nada de constantes de uma instalação concreta (tudo
vem do `office.yaml`); os contratos v1 e o formato do `office.yaml` só mudam com versão nova; uma chave nova no
`office.yaml` vai para a tabela da [Referência](#officeyaml), para o `office.example.yaml` e para os testes de limites;
entrada no `CHANGELOG.md`. As decisões de arquitectura estão em `DECISIONS.md`.

Para preparar uma versão (mantenedores): `scripts/preparar-publico.sh DESTINO` gera uma árvore sem histórico nem
`docs/` e falha se encontrar nomes internos ou caminhos pessoais (a lista de nomes vem do passo «Genérico» do
`ci.yml` de quem prepara a versão e não vai para a cópia pública; sem ela, o script recusa-se a correr);
`scripts/release.sh X.Y.Z` (exige `## Lite X.Y.Z` no `CHANGELOG.md`) cria `dist/squad-office-lite-X.Y.Z.tar.gz` e
`dist/SHA256SUMS`, os dois ficheiros que o `get.sh` descarrega. Nenhum dos dois publica nada: a release no GitHub (tag `vX.Y.Z` com os dois ficheiros anexados) é manual.

## Licença

[MIT](LICENSE).
