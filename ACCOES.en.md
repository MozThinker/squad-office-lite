# Approved actions (Squad Office Lite 0.6.1)

[Português](ACCOES.md) · [README](README.en.md) · decisions: ADR-020 and ADR-021 in [DECISIONS.md](DECISIONS.md) (in
Portuguese)

## What it does

Business squads (the `atendimento` and `comercial` templates) only read and write text. When a task ends, the runner
looks in the final text for the **actions the squad proposes** and creates a request in **Approvals**, with one
`[A1]…[A10]` line per action. You approve or reject **each one**; there is no «Approve all» on these requests. Only
what you approved is executed, and by a separate service, `accoes`, running as **another system user** with the
credentials only it can read.

Closed catalogue (nothing outside it):

| Action | What it does | Daily limit per squad | On by default |
|---|---|---|---|
| `email_rascunho` | saves a reply draft in the mailbox's drafts folder | 50 | yes |
| `folha_linha` | appends a row to a CSV sheet | 100 | yes |
| `aviso_dono` | notifies you on Telegram | 20 | yes |
| `email_responder` | sends a reply to the sender of a received message | 20 | **no** |

`email_responder` is turned on by hand, per squad (see below): whoever installs Lite has nobody else to review. It
goes only to the `From` of the message recorded by the collector, only if that message was **authenticated**, and
waits 2 minutes before leaving (you can cancel it). Never new recipients, CC/BCC, attachments or HTML; links can only
use domains you listed; third-party e-mail addresses in the text are rejected.

**How it runs:** the collector reads the mailbox over IMAP (without marking messages as read; it starts at new
messages), writes the messages to `<dados>/accoes-entrada/<squad>/mensagens/` (the runner copies them to
`entrada/mensagens/` in each task's worktree) and publishes `vista.json` (enabled actions, domains, sheets, mailbox
address, senders; **no secrets**) so the runner can validate proposals. From it, the runner also writes
`entrada/accoes.md` in the worktree (enabled actions, sheets with columns and domains; never senders nor the mailbox),
kept out of commits, so the squad knows what it may propose. In action requests, Approvals show the text without
the secret masking used elsewhere (a key or a `password=…` shows in full): what you approve is always what goes out;
refuse what must not leave. The
executor runs one pass per minute and only
executes an action if:

- the squad is in `agentes/squads.json` with a business template, and the action is enabled and configured;
- the line says `→ aprovado` and the request text is exactly the one in the record (`decisoes/.accoes/`), with the
  same `sha`;
- at most **48 h** have passed since the request was created (a reply approved in the last 2 min expires while waiting);
- the daily limit (in the time zone saved in the request's record) has not been reached;
- the `sha` was never executed (a marker is created with `O_EXCL` before the call: no double sends).

Each execution goes in the **ledger** `AAAA-MM.jsonl` (time, squad, type, `sha`, request, line, `aprovado`, state,
reason; never body, subject, addresses or values).

## Turning it on in Docker mode

The `accoes` service already comes in the generated `compose.yaml` (volumes `accoes-segredos`, `accoes-estado`,
`accoes-entrada` and `accoes-saude`, its own network). It starts as `root` only to set ownership of its folders, then
immediately drops to `ACCOES_UID:ACCOES_GID`.

1. In `.env`, if you want another uid (default 10002; **it must differ from `OFFICE_UID`**, otherwise the service
   refuses to start, because the runner would be able to read the credentials):
   ```
   ACCOES_UID=10002
   ACCOES_GID=10002
   ```
   Then `docker compose up -d`. On an install older than 0.5.0, regenerate the compose file (see «Updating on the PC»
   in the README) and run `docker compose run --rm configurar` once.
2. **Configuration** (`accoes.json`, no secrets; format below), through stdin; it is validated before saving:
   ```sh
   docker compose exec -T accoes accoes escrever-config < accoes.json
   ```
3. The sheets folder (`/dados/accoes/folhas/`) is created by the service on start, owned by it.
4. **Credentials** (`accoes.env`, format below), typed at the keyboard with no echo (paste, then Ctrl-D); they never
   reach shell history and are never printed:
   ```sh
   docker compose exec accoes accoes escrever-segredos
   ```
5. **Verify** without sending anything (permissions, configuration, and one ✓/✗ line each for IMAP, SMTP and Telegram,
   no values):
   ```sh
   docker compose exec accoes accoes verificar
   ```

## Turning it on on a VPS

`install.sh` never uses `sudo`; this service is installed separately, from the repository, with `sudo`:

```sh
sudo ./instalar-accoes.sh --dados ~/office-lite/dados --verificar   # only shows what it would do
sudo ./instalar-accoes.sh --dados ~/office-lite/dados
```

It needs `acl` (`setfacl`), systemd and Python ≥ 3.10. It creates the system user `office-accoes` (no home, no
shell), copies the code to `/opt/office-accoes` (owned by root), creates `/srv/office-accoes` (`accoes.json` 0644,
`segredos/` 0700), `<dados>/accoes-entrada` and `<dados>/accoes-saude` (`0755`, also in the unit's `ReadWritePaths`),
gives the user read access by ACL on `decisoes/` and `agentes/`, and enables `office-accoes.timer` (one pass per
minute). It is idempotent: it never rewrites `accoes.json` or
`accoes.env`. At the end it prints the next commands; the environment is the unit's:

```sh
CMD="sudo -u office-accoes env ACCOES_PERFIL=lite ACCOES_DIR=/srv/office-accoes ACCOES_SEGREDOS=/srv/office-accoes/segredos \
  ACCOES_ENTRADA=<dados>/accoes-entrada ACCOES_SAUDE=<dados>/accoes-saude ACCOES_DECISOES=<dados>/decisoes \
  ACCOES_AGENTES=<dados>/agentes \
  python3 -I -u /opt/office-accoes/servico_accoes/servico.py"
$CMD escrever-config < accoes.json
$CMD escrever-segredos
$CMD verificar
```

**After every `git pull`** of the repository, run `sudo ./instalar-accoes.sh --dados …` again: the copy in `/opt`
does not update itself.

Messages in `<dados>/accoes-entrada` are readable by other accounts on the system if your home and data folder are:
keep the data folder at `0750` or `0700`. The `office` container does not mount it: of the service it only sees
`<dados>/accoes-saude` (read-only, see «Service health»), which `install.sh` creates (`0755`) if missing and
`instalar-accoes.sh` gives to `office-accoes`.

## `accoes.json`

One entry per business squad (the squad id); the service folder is `/dados/accoes` (Docker) or `/srv/office-accoes`
(VPS), and sheets can only live directly in `<service folder>/folhas/*.csv`.

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

- `v` and `squads` are required; nothing outside the documented keys is accepted, and errors name the field, never
  the value.
- `accoes`: the list of the squad's enabled actions; without it the `lite` profile applies (`email_rascunho`,
  `folha_linha`, `aviso_dono`). To turn on `email_responder` you must **put it in the list** and, to keep the others,
  list them too. Turning one off = removing it from the list.
- `dominios`: the only domains that may appear in links and e-mail addresses in an action's text.
- `limites`: can only **lower** the catalogue's daily limits.
- `email`: one mailbox serves one squad; `tls` may only be `false` on `localhost`. IMAP and SMTP use **implicit
  TLS** (ports 993 and 465); STARTTLS (143 and 587) is not supported. **`authserv_id`** is the mailbox
  server's name in the first `Authentication-Results` header of messages; without it no message counts as
  authenticated and `email_responder` cannot pass. Only `dmarc=pass` or `dkim=pass` aligned with the sender's domain
  count.
- `folhas`: a `.csv` file and its columns (names `[a-z0-9_-]`, up to 30). The file starts with the header on line 1;
  values starting with `=`, `+`, `-` or `@` get a `'` in front.
- `telegram`: `url` is the API base address (`https`; `http` only on `localhost`), without the token, and `chat_id`
  is numeric. `TELEGRAM_TOKEN` goes in `accoes.env`.

The file must be regular and not writable by group/others (`0644`); the service refuses to start otherwise.

## `accoes.env`

`KEY=value` lines, `#` for comments, and only these keys:

```
ATENDIMENTO_EMAIL_UTILIZADOR=geral@exemplo.pt
ATENDIMENTO_EMAIL_SENHA=app-password
TELEGRAM_TOKEN=123456:abc...
```

`<SQUAD>_` is the squad id in upper case, with `-` turned into `_`. Use a mailbox **app password** (accounts with 2FA
do not accept the normal password). The file is `0600`, owned by the service's uid, and lives in the
`accoes-segredos` volume (Docker) or in `/srv/office-accoes/segredos` (VPS). The service refuses to start if it has
group/others permissions, belongs to another uid or is a symlink. **Never in `claude.env`** or in files the runner can
read. Turning connectors on and off is just editing these two files (and running `escrever-config` again).

## Service health

At the end of each complete pass (in Docker, each turn of the `ciclo`; on a VPS, each timer pass) the service writes
`<dados>/accoes-saude/saude.json` (in Docker, `/dados/accoes-saude/saude.json` in the `accoes-saude` volume, variable
`ACCOES_SAUDE`). Only the service writes to this folder:

```json
{"v":1,"em":"2026-11-18T13:23:00+00:00","avisos":[]}
```

`em` is the UTC time of the pass; `avisos` are that pass's warnings (from the collector, then the executor), without
duplicates, at most 10 of 200 characters each, only fixed texts, squad ids, error type names and numbers (never
addresses, servers, users, credentials or message content). With no warnings it holds `"avisos": []`: the file is
written anyway, as proof of life. Mode `0644`, atomic write, symlinks not followed. If the pass was skipped because
another one was running, the file does not change.

**No business squads, no warning:** a missing `agentes/squads.json` is the normal case. Only a registry that exists
and cannot be used (no permission, invalid JSON, symlink, too large or wrong format) gives the warning
`registo de squads ilegível; nada colhido|executado nesta passagem`. With no registry, the executor neither runs nor refuses
anything: an approved request waits for the registry to come back (within the usual 48 h).

**In the log** (stderr; `journalctl -u office-accoes.service` on a VPS, `docker compose logs accoes` in Docker) only
what changed since the previous `saude.json` is written: `aviso: <text>` on the first pass where it appears and
`resolvido: <text>` once, when it goes away. A different text counts as a new warning. An unexpected failure of the
collector or executor shows as `aviso: erro no colector|executor: <Type>`. If `saude.json` cannot be written, the pass
does not fail and `aviso: saude.json não gravado (<Type>)` is written; without that file there is no memory between
passes and the warnings repeat until it is fixed.

**On the dashboard:** the `office` mounts `accoes-saude` **read-only** (the only thing of the service it sees; it does
not mount `accoes-entrada`) and the server reads only `saude.json` (`ACCOES_SAUDE_DIR`, default `/data/accoes-saude`; it
never lists the folder). With warnings an alert «Serviço de ações: …» appears (an unreadable squad registry reads «não
consigo ler o registo das squads»; with several warnings, the first one and «(e mais N)»). If the last pass is more than
10 minutes old, the alert is «Serviço de ações parado desde HH:MM» (time in the `office.yaml` time zone). The alert goes
away by itself when the problem is gone; «Fechar» hides it while it stays the same, and it shows again if it changes or
if the problem goes away and comes back (not kept across page reloads). It does not trigger a browser notification or
Web Push. With no service installed (no `saude.json`) nothing appears.

`saude.json` is only the state of the last pass: the on-demand check is still `accoes verificar`.

**Turning the service off:** the last `saude.json` stays, and the dashboard shows «Serviço de ações parado» until it is
deleted. On a VPS, after `sudo systemctl disable --now office-accoes.timer`, delete it too:
`sudo rm -f <dados>/accoes-saude/saude.json`. In Docker, stopping the `accoes` service shows «parado» (as intended);
if you remove it for good, delete the volume: `docker volume rm office-lite-pc_accoes-saude` (the project name is the
`name:` in `compose.yaml`).

## Cancel and undo

Both commands take the action's `sha`, which you see in the ledger `AAAA-MM.jsonl` (`/dados/accoes/` in Docker,
`/srv/office-accoes/` on a VPS) and in the request. In Docker: `docker compose exec accoes accoes anular <sha>`; on a
VPS, the `$CMD anular <sha>` above.

- `anular <sha>`: only an `email_responder` still **waiting** (the 2 min) is stopped. Once sent it cannot be cancelled.
- `desfazer <sha>`: deletes the draft created or removes the row appended to the sheet (only if it is still the one
  the action wrote). A sent reply cannot be undone.

There are no buttons for this in Approvals in this version.

## Compose verifier

In Docker mode only the `accoes` service may see `accoes.env`. Check it without Docker:

```sh
python3 tools/verificar_compose_accoes.py compose.yaml
```

It also checks the service state (what `accoes` mounts at `ACCOES_DIR`, the `accoes-estado` volume): no other service
may mount it. And `office` may not mount `accoes-entrada`, `accoes-estado` or `accoes-segredos`, nor `accoes-saude` with
write access (one line per finding). Exit 0 with no findings; otherwise it prints `serviço <x> monta accoes.env (<via>)`
or `serviço <x> monta o estado do accoes (volumes: <source>)` for each finding (through `env_file`, `volumes`,
`secrets`, `volumes_from`, host access…). Anything it cannot read (anchors, `extends`, flow style) fails. Run it after
editing `compose.yaml` by hand.

## Personal data: who is responsible

Whoever installs Lite is the **controller** of the personal data the squads read and the actions write (customer
messages, e-mails, names and contacts in sheets). Squad Office is not a processor: everything runs on your machine
(the text does go to Anthropic through `claude`, under the terms of your account). Before connecting a mailbox:

- confirm the legal basis and tell your customers that an assistant reads their messages;
- the Office (the web process) does not read the received messages, the `vista.json` files, the credentials or the
  service state: of the service it only mounts `accoes-saude`, read-only, and reads only `saude.json`, which holds no
  personal data (ADR-022);
- do not connect mailboxes with data about minors, health or other sensitive data;
- **retention: 90 days.** Requests lose the quoted text, the sender and the subject; the `decisoes/.accoes/` record
  is removed; collector messages and records older than 90 days are deleted. The ledger holds no content. The copies
  of messages the runner put in task worktrees are not purged by this service, nor is each task's folder in
  `<dados>/tasks/<tid>/` (`meta.json` and logs, with the text the squad read and wrote) nor the `claude` transcripts
  (`~/.claude/projects`, or the `claude_projects` root): delete them when no longer needed.

## API key for business squads

Squads that handle third-party messages and run with nobody at the keyboard are **automated commercial use**. Use an
Anthropic **API key** (Console, pay-per-use billing, spending limits you set), as `ANTHROPIC_API_KEY` in
`claude.env`. Consumer plans (Pro/Max, subscription token) are for personal, interactive use, have usage limits that
stop squads halfway, and give no per-project cap. `doctor` warns (without failing) if there are business squads using
a consumer-plan credential. Fix it with `./install.sh --credenciais` (VPS) or as in «Claude credentials» (Docker).

## Residual risk (S1): approval is not protected

The service boundary protects the **credentials**, the **content** rules and the **recipients**. It **does not
protect approval:** anyone who can write as the runner's user (the account that writes to `decisoes/`) can forge a
request, its record and the `→ aprovado` mark, and the service will execute it within the rules (only allowed
recipients and domains, within the daily limits). So protect that account and host access the way you would protect
your mailbox. An anchor outside the host (a signature or confirmation over another channel) **does not exist yet**;
it is left for the project owner to decide (ADR-021).
