# Acções aprovadas (Squad Office Lite 0.5.0)

[English](ACCOES.en.md) · [README](README.md) · decisões: ADR-020 e ADR-021 em [DECISIONS.md](DECISIONS.md)

## O que faz

As squads de negócio (modelos `atendimento` e `comercial`) só leem e escrevem texto. Quando uma tarefa acaba, o runner
procura no texto final as **acções que a squad propõe** e cria um pedido nas **Aprovações**, com uma linha
`[A1]…[A10]` por acção. Tu aprovas ou recusas **cada uma**; não há «Aprovar todas» nestes pedidos. Só o que aprovaste
é executado, e por um serviço à parte, o `accoes`, com **outro utilizador do sistema** e as credenciais só dele.

Catálogo fechado (nada fora disto):

| Acção | O que faz | Limite por dia e squad | Vem ligada |
|---|---|---|---|
| `email_rascunho` | grava um rascunho de resposta na pasta de rascunhos da caixa | 50 | sim |
| `folha_linha` | acrescenta uma linha a uma folha CSV | 100 | sim |
| `aviso_dono` | avisa-te no Telegram | 20 | sim |
| `email_responder` | envia uma resposta ao remetente de uma mensagem recebida | 20 | **não** |

`email_responder` liga-se à mão, por squad (ver abaixo): quem instala o Lite não tem outra pessoa a rever. Vai só
para o `From` da mensagem registada pelo colector, só se ela veio **autenticada**, e espera 2 minutos antes de sair
(podes anulá-la). Nunca há destinatários novos, CC/BCC, anexos nem HTML; os links só podem ser de domínios que
listaste; e-mails de terceiros no texto são recusados.

**Como corre:** o colector lê a caixa por IMAP (sem marcar como lida; começa nas mensagens novas), escreve as
mensagens em `<dados>/accoes-entrada/<squad>/mensagens/` (o runner copia-as para `entrada/mensagens/` do worktree de
cada tarefa) e publica `vista.json` (acções ligadas, domínios, folhas, endereço da caixa, remetentes; **sem segredos**)
para o runner validar as propostas. Com ela, o runner escreve também `entrada/accoes.md` no worktree (acções ligadas,
folhas com colunas e domínios; nunca remetentes nem a caixa), fora dos commits, para a squad saber o que pode propor.
Nos pedidos de acções as Aprovações mostram o texto sem a máscara de segredos dos outros pedidos (uma chave, uma
`password=…` aparecem inteiras): o que aprovas é sempre o que sai; recusa o que não deve sair. O executor corre uma passagem por minuto e só executa uma acção se:

- a squad está em `agentes/squads.json` com modelo de negócio e a acção está ligada e configurada;
- a linha tem `→ aprovado` e o texto do pedido é exactamente o do registo (`decisoes/.accoes/`), com o mesmo `sha`;
- passaram no máximo **48 h** desde a criação do pedido (uma resposta aprovada nos últimos 2 min caduca na espera);
- o limite do dia (no fuso gravado no registo do pedido) não foi atingido;
- o `sha` nunca foi executado (marcador criado com `O_EXCL` antes da chamada: não há envio a dobrar).

Cada execução fica no **livro** `AAAA-MM.jsonl` (hora, squad, tipo, `sha`, pedido, linha, `aprovado`, estado, motivo;
nunca corpo, assunto, endereços nem valores).

## Ligar no modo Docker

O serviço `accoes` já vem no `compose.yaml` gerado (volumes `accoes-segredos`, `accoes-estado` e `accoes-entrada`, rede
própria). Arranca como `root` só para dar o dono às pastas dele e passa logo a `ACCOES_UID:ACCOES_GID`.

1. No `.env`, se quiseres outro uid (omissão 10002; **tem de ser diferente do `OFFICE_UID`**, senão o serviço recusa
   arrancar, porque o runner leria as credenciais):
   ```
   ACCOES_UID=10002
   ACCOES_GID=10002
   ```
   Depois `docker compose up -d`. Numa instalação anterior à 0.5.0, volta a gerar o compose (ver «Actualizar no PC» no
   README) e corre `docker compose run --rm configurar` uma vez.
2. **Configuração** (`accoes.json`, sem segredos; formato abaixo), pelo stdin; é validada antes de gravar:
   ```sh
   docker compose exec -T accoes accoes escrever-config < accoes.json
   ```
3. A pasta das folhas (`/dados/accoes/folhas/`) é criada pelo serviço ao arrancar, com o dono dele.
4. **Credenciais** (`accoes.env`, formato abaixo), pelo teclado, sem eco (cola e termina com Ctrl-D); nunca ficam no
   histórico da shell nem são impressas:
   ```sh
   docker compose exec accoes accoes escrever-segredos
   ```
5. **Verificar** sem enviar nada (permissões, configuração e uma linha ✓/✗ por IMAP, SMTP e Telegram, sem valores):
   ```sh
   docker compose exec accoes accoes verificar
   ```

## Ligar no VPS

O `install.sh` nunca usa `sudo`; este serviço instala-se à parte, a partir do repositório e com `sudo`:

```sh
sudo ./instalar-accoes.sh --dados ~/office-lite/dados --verificar   # só mostra o que faria
sudo ./instalar-accoes.sh --dados ~/office-lite/dados
```

Precisa de `acl` (`setfacl`), systemd e Python ≥ 3.10. Cria o utilizador de sistema `office-accoes` (sem casa nem
shell), copia o código para `/opt/office-accoes` (do root), cria `/srv/office-accoes` (`accoes.json` 0644, `segredos/`
0700) e `<dados>/accoes-entrada`, dá-lhe leitura por ACL em `decisoes/` e `agentes/` e liga o timer
`office-accoes.timer` (uma passagem por minuto). É idempotente: nunca reescreve o `accoes.json` nem o `accoes.env`. Ao
fim mostra os comandos seguintes; o ambiente é o da unit:

```sh
CMD="sudo -u office-accoes env ACCOES_PERFIL=lite ACCOES_DIR=/srv/office-accoes ACCOES_SEGREDOS=/srv/office-accoes/segredos \
  ACCOES_ENTRADA=<dados>/accoes-entrada ACCOES_DECISOES=<dados>/decisoes ACCOES_AGENTES=<dados>/agentes \
  python3 -I -u /opt/office-accoes/servico_accoes/servico.py"
$CMD escrever-config < accoes.json
$CMD escrever-segredos
$CMD verificar
```

**Depois de cada `git pull`** do repositório, volta a correr `sudo ./instalar-accoes.sh --dados …`: a cópia em
`/opt` não se actualiza sozinha.

As mensagens em `<dados>/accoes-entrada` ficam legíveis para outras contas do sistema se a tua casa e a pasta de dados
o forem: mantém a pasta de dados a `0750` ou `0700`.

## `accoes.json`

Uma entrada por squad de negócio (o id da squad); a pasta do serviço é `/dados/accoes` (Docker) ou `/srv/office-accoes`
(VPS), e as folhas só podem estar directamente em `<pasta do serviço>/folhas/*.csv`.

```json
{
  "v": 1,
  "telegram": {"url": "https://api.telegram.org", "chat_id": "123456789"},
  "squads": {
    "atendimento": {
      "dominios": ["exemplo.pt"],
      "accoes": ["email_rascunho", "email_responder", "aviso_dono"],
      "limites": {"email_responder": 5},
      "email": {
        "endereco": "geral@exemplo.pt", "pasta": "INBOX", "rascunhos": "Drafts", "authserv_id": "mail.exemplo.pt",
        "imap": {"host": "imap.exemplo.pt", "porta": 993, "tls": true},
        "smtp": {"host": "smtp.exemplo.pt", "porta": 465, "tls": true}
      }
    },
    "comercial": {
      "folhas": {"leads": {"ficheiro": "/dados/accoes/folhas/leads.csv", "colunas": ["data", "empresa", "nota"]}}
    }
  }
}
```

- `v` e `squads` são obrigatórios; nada é aceite fora das chaves documentadas, e erros dizem o campo, nunca o valor.
- `accoes`: a lista das acções ligadas da squad; sem ela vale o perfil `lite` (`email_rascunho`, `folha_linha`,
  `aviso_dono`). Para ligar `email_responder` tens de a **incluir na lista** e, se mantiveres as outras, listá-las
  também. Desligar = tirar da lista.
- `dominios`: os únicos domínios que podem aparecer em links e em e-mails no texto das acções.
- `limites`: só podem **baixar** os limites por dia do catálogo.
- `email`: uma caixa serve uma squad; `tls` só pode ser `false` em `localhost`. IMAP e SMTP usam **TLS implícito**
  (portas 993 e 465); STARTTLS (143 e 587) não é suportado. **`authserv_id`** é o nome do servidor
  da caixa no 1.º `Authentication-Results` das mensagens; sem ele nenhuma conta como autenticada e `email_responder`
  não passa. Só `dmarc=pass` ou `dkim=pass` alinhados com o domínio do remetente contam.
- `folhas`: ficheiro `.csv` e colunas (nomes `[a-z0-9_-]`, até 30). O ficheiro nasce com o cabeçalho na 1.ª linha;
  valores começados por `=`, `+`, `-` ou `@` levam `'` à frente.
- `telegram`: `url` é o endereço base da API (`https`; `http` só em `localhost`), sem o token, e `chat_id` é numérico. O
  `TELEGRAM_TOKEN` vai no `accoes.env`.

O ficheiro tem de ser regular e sem escrita para grupo/outros (`0644`); o serviço recusa arrancar se não for.

## `accoes.env`

Chaves `CHAVE=valor`, uma por linha, `#` para comentários, e só estas:

```
ATENDIMENTO_EMAIL_UTILIZADOR=geral@exemplo.pt
ATENDIMENTO_EMAIL_SENHA=senha-de-aplicação
TELEGRAM_TOKEN=123456:abc...
```

`<SQUAD>_` é o id da squad em maiúsculas, com `-` a `_`. Usa uma **senha de aplicação** da caixa (contas com 2FA não
aceitam a senha normal). É `0600`, do uid do serviço, e vive no volume `accoes-segredos` (Docker) ou em
`/srv/office-accoes/segredos` (VPS). O serviço recusa arrancar se tiver permissões de grupo/outros, for de outro uid ou
for um symlink. **Nunca no `claude.env`** nem em ficheiros que o runner leia. Ligar e desligar conectores é só editar
estes dois ficheiros (e `escrever-config` de novo).

## Anular e desfazer

Os comandos levam o `sha` da acção, que vês no livro `AAAA-MM.jsonl` (`/dados/accoes/` no Docker,
`/srv/office-accoes/` no VPS) e no pedido. No Docker: `docker compose exec accoes accoes anular <sha>`; no VPS, o
`$CMD anular <sha>` acima.

- `anular <sha>`: só uma `email_responder` ainda **em espera** (os 2 min) não sai. Depois de enviada não se anula.
- `desfazer <sha>`: apaga o rascunho criado ou remove a linha acrescentada à folha (só se ainda for a que a acção
  escreveu). Uma resposta enviada não se desfaz.

Não há botões para isto nas Aprovações nesta versão.

## Verificador do compose

No modo Docker, só o serviço `accoes` pode ver o `accoes.env`. Verifica-o sem Docker:

```sh
python3 tools/verificar_compose_accoes.py compose.yaml
```

Sai 0 se nenhum outro serviço o monta; senão imprime `serviço <x> monta accoes.env (<via>)` por cada achado (por
`env_file`, `volumes`, `secrets`, `volumes_from`, acesso ao host…). O que não souber ler (âncoras, `extends`, flow
style) falha. Corre-o depois de mexer à mão no `compose.yaml`.

## Dados pessoais: quem é o responsável

Quem instala o Lite é o **responsável pelo tratamento** dos dados pessoais que as squads lêem e que as acções
escrevem (mensagens de clientes, e-mails, nomes e contactos nas folhas). O Squad Office não é subcontratante: tudo
corre na tua máquina (o texto vai, porém, para a Anthropic pelo `claude`, nos termos da tua conta). Antes de ligar uma
caixa:

- confirma a base legal e informa os teus clientes de que as mensagens são lidas por um assistente;
- não ligues caixas com dados de menores, de saúde ou outros dados sensíveis;
- **retenção: 90 dias.** Os pedidos perdem o texto citado, o remetente e o assunto; o registo `decisoes/.accoes/` sai;
  as mensagens e os registos do colector com mais de 90 dias apagam-se. O livro não guarda conteúdo. As cópias das
  mensagens que o runner pôs nos worktrees das tarefas não são purgadas por este serviço, nem a pasta de cada tarefa
  em `<dados>/tasks/<tid>/` (`meta.json` e logs, com o texto que a squad leu e escreveu) nem as transcrições do
  `claude` (`~/.claude/projects`, ou a raiz `claude_projects`): apaga-as tu quando deixarem de ser precisas.

## Chave de API para squads de negócio

Squads que tratam mensagens de terceiros e correm sem ninguém ao teclado são **uso comercial automático**. Usa uma
**chave de API** da Anthropic (Console, faturação por uso, limites de gasto teus), em `ANTHROPIC_API_KEY` no
`claude.env`. Os planos de consumidor (Pro/Max, token da subscrição) são para uso pessoal e interactivo, têm limites de
utilização que param as squads a meio e não dão tecto por projecto. O `doctor` avisa (sem falhar) se há squads de
negócio com a credencial de um plano de consumidor. Corrige-a com `./install.sh --credenciais` (VPS) ou como em
«Credenciais do Claude» (Docker).

## Risco residual (S1): a aprovação não está protegida

A fronteira do serviço protege as **credenciais**, as regras de **conteúdo** e os **destinatários**. **Não protege a
aprovação:** quem consiga escrever como o utilizador do runner (a conta que escreve em `decisoes/`) pode forjar um
pedido, o registo dele e a marca `→ aprovado`, e o serviço executa-o dentro das regras (só destinatários e domínios
permitidos, nos limites do dia). Por isso: protege essa conta e o acesso ao host como protegerias a tua caixa de
e-mail. Uma âncora fora do host (assinatura ou confirmação por outro canal) **ainda não existe**; fica por decidir
pelo dono do projecto (ADR-021).
