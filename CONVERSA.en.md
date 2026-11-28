# The Conversa (Squad Office Lite 0.6.2)

[Português](CONVERSA.md) · [README](README.en.md)

> **Note:** the interface is in Portuguese only, so on-screen texts are quoted in Portuguese.

The Conversa is your Claude Code on your phone: a chat with its own address (e.g. `https://conversa.your-domain`)
that talks to the `claude` of your user on the VPS. The Office only shows a button with the name and the pending
requests; the Conversa opens separately, never inside the Office. It only exists in **VPS mode** (it needs HTTPS, a DNS
record and dedicated system users); in the PC's Docker mode it stays off. It is off by default and nothing changes for
anyone who does not turn it on.

There are three pieces (ADR-023, guardian revision): the **guardian** (`guardião`, system user `office-conversa`)
serves the Conversa page, keeps the key that checks every request and writes the accepted ones to a folder only it
writes; the **bridge** (`ponte`, your user, the `dono`) reads that folder and runs your `claude`; the **Office** only
reads a public summary (name, pending requests, today's spend). Neither the Office, nor the bridge, nor the assistant
itself can reach the key.

## What it does and does not do

It does:
- Chat with your Claude Code on the server, with history, on mobile and desktop; open it with the button in the
  Office header (in its own tab) or directly at its address.
- Ask for your **approval card** before every action with an effect (running a command, writing or editing files,
  opening a URL), showing exactly what would be done; it also relays the assistant's questions and plans.
- Launch squads of the kit (`lancar`, `continuar`, `diretor`, `squad`; the list is the `--lancadores` one), always
  with a card and never with «Aprovar nesta conversa» ("Approve in this conversation").
- Voice dictation (optional, see below).

It does not:
- Run anything by itself: without your answer a card expires after 10 minutes and the action is denied.
- Block on spend: it shows it (`hoje: X USD` in the panel header) and nothing else.
- Hand your Claude account to the Office or to the container: the bridge runs as your user (`dono`, "owner"), the
  Office never sees the credentials nor the conversations, and only the public summary is mounted into the container.
- Exist in the PC's Docker mode, or without HTTPS.

## Warning: the Conversa consumes tokens from your account

> A Conversa consome tokens da tua conta Claude (a do dono, no servidor). Cada mensagem custa conforme o modelo e o
> tamanho da conversa; o gasto de hoje aparece no cabeçalho do painel. Nada te bloqueia: és tu que geres o consumo.
>
> (The Conversa consumes tokens from your Claude account, the owner's, on the server. Each message costs according
> to the model and the length of the conversation; today's spend shows in the panel header. Nothing blocks you: you
> manage the consumption.)

This warning shows the first time you send a message and you must accept it (the guardian refuses messages without
it).
With a Claude subscription it counts against your usage limit; with an API key you pay API prices.

**Example cost per message (ESTIMATE, 23 Nov 2026).** At Sonnet API prices, 3 USD per million input tokens and 15 USD
per million output tokens (source: <https://www.anthropic.com/pricing>; prices change, check there): a message with
20,000 tokens of context and a 1,000-token answer costs 20,000 × 3 / 1,000,000 + 1,000 × 15 / 1,000,000 =
0.06 + 0.015 ≈ **0.08 USD**. This is an order of magnitude: a long conversation, several tool steps, another model
(Opus costs more, Haiku less) or prompt caching change it. The number that counts is the one in the header, which adds
up the cost `claude` itself reports, in the installation's time zone.

## Installing

Prerequisites: the Office already installed by `install.sh`, and Claude Code installed and logged in (`claude` →
`/login`) for the user who will be the `dono` (your everyday user; never root nor the Office user: they must be two
different users). Packages: `acl` and `systemd`.

1. **Address and DNS:** pick the Conversa address, preferably on a **different registrable domain** from the
   Office's (e.g. `conversa.other-domain.example` instead of `conversa.your-domain`): that way neither cookies nor the
   password manager mix the two origins. With the tunnel (the default, below) DNS is the tunnel's; with `tls: caddy`
   create an A/AAAA record pointing at the VPS.
2. **A Lite copy owned by root only.** The installer copies the code and the Conversa page to `/opt/office-conversa`
   and refuses to run from a copy (or from any folder above it, up to `/`) that the Office user **or the `dono`** can
   write to, or that belongs to them: the assistant runs as the `dono`, and whoever changed that copy would choose
   what the guardian runs. Download the release **as root**, check it against `SHA256SUMS` (as `get.sh` does) and
   extract it into a root-owned folder:
   ```bash
   sudo -i
   mkdir -p /usr/local/src && cd "$(mktemp -d)"
   curl -fLO https://github.com/MozThinker/squad-office-lite/releases/download/v0.6.2/squad-office-lite-0.6.2.tar.gz
   curl -fLO https://github.com/MozThinker/squad-office-lite/releases/download/v0.6.2/SHA256SUMS
   sha256sum -c SHA256SUMS && tar xzf squad-office-lite-0.6.2.tar.gz --no-same-owner -C /usr/local/src
   mv /usr/local/src/squad-office-lite-0.6.2 /usr/local/src/office-lite-0.6.2
   ```
3. **The tunnel group** (with `tls: externo`, the default): create a group just for whoever reaches the guardian
   socket (the Office user must not belong to it), e.g. `sudo groupadd cloudflared`, and run `cloudflared` in it (see
   «Tunnel» below).
4. In the root-owned folder, see what would be done and then install:
   ```bash
   cd /usr/local/src/office-lite-0.6.2
   ./instalar-conversa.sh --office <Office user> --dono <your user> --grupo-socket cloudflared --verificar
   sudo ./instalar-conversa.sh --office <Office user> --dono <your user> --grupo-socket cloudflared
   ```
   Options: `--tls externo|caddy` (default `externo`; the same as `conversa.tls`), `--grupo-socket` (who reaches the
   socket; required with `externo` and without the Office user; with `caddy`, default the Office user's group),
   `--pasta` (default `/srv/office-conversa`; never inside `/home`; it and the folders above it owned by root only),
   `--pasta-trabalho` (where the assistant works; default your home), `--permissoes cartao|espelho`, `--lancadores`,
   `--fuso`, `--voz desligada|local|externa`, `--voz-idioma` (default `pt`), `--claude <path>`, `--python <path>`,
   `--novo-codigo` and `--revogar`. The installer creates the `office-conversa` user and the folders with ACLs, copies
   the code and the page to `/opt/office-conversa` (owned by root) with a `SHA256SUMS`, generates the **Conversa
   code** and installs the system unit `office-conversa-guardiao.service` and the user unit `office-conversa.service`
   (with linger). Run it again without fear: what exists stays.
5. **Use the code** shown on screen (24 letters and digits, in 6 groups) in the browser where you will use the
   Conversa: it is shown only once, is not stored and **dies on first use** (it registers that browser).
6. In `office.yaml`, turn the Conversa on and run `./install.sh` again (it regenerates the compose):
   ```yaml
   conversa:
     ativa: true
     pasta: /srv/office-conversa       # the one from instalar-conversa.sh
     site: conversa.other-domain.example   # the address from step 1 (the Office button's)
     tls: externo                      # the default; caddy only on purpose (see «TLS and the domain»)
   ```
   The assistant's name, permissions and voice no longer go in `office.yaml` (the Office writes it): permissions and
   voice live in the units, set by the installer, and the name is changed in the Conversa itself.
7. Open `https://<the Conversa address>` (or the Conversa button in the Office) and type the code.

**Ubuntu 22.04:** the guardian and the bridge need Python ≥ 3.11 and 22.04 ships 3.10. Install it and tell the
installer:
```bash
sudo apt-get install -y python3.11
sudo ./instalar-conversa.sh --office <Office user> --grupo-socket cloudflared --python /usr/bin/python3.11
```
The installer still checks the version and refuses an older Python. With `--python`, both units start with that
Python (and so does the local voice venv). Ubuntu 24.04 already ships 3.12.

## TLS and the domain

The Conversa only opens over HTTPS: the guardian refuses everything (403) if the request did not come through the
proxy over HTTPS, and never opens the session over HTTP. Its address must differ from the Office's (`conversa.site`),
preferably on another registrable domain. Without `conversa.site`, the Office button explains what is missing.

- **`tls: externo` (default):** TLS terminates **outside** the Office user, in a tunnel or proxy of yours connected to
  the guardian socket. The Office's Caddy mounts nothing of the Conversa. This is what closes the risk of whoever
  controls the Office serving JS on the Conversa origin (ADR-023, «Revisão: guardião»), and the only option with the
  Traefik proxy.
- **`tls: caddy` (only when chosen explicitly):** the Office's Caddy serves the `site` and gets the certificate by
  itself (needs `./install.sh --dominio` and an A/AAAA record for the VPS; the browser also asks for the Office user
  and password). **Warning: TLS terminates in the `fabrica`'s Caddy; whoever controls it can serve JS on the
  Conversa origin** (and read the traffic). The installer shows this warning.

## Tunnel (`tls: externo`, the default)

Example with a Cloudflare Tunnel of the `dono` (another tunnel or proxy works, as long as it runs outside the Office
user):
1. `sudo groupadd cloudflared` and a system user for the tunnel in that group
   (`sudo useradd --system --gid cloudflared --shell /usr/sbin/nologin cloudflared`). The Office user does **not** join
   the group (the installer refuses).
2. Install `cloudflared` (Cloudflare's package), run `cloudflared tunnel login` and `cloudflared tunnel create
   conversa`, and point the Conversa address's DNS at the tunnel (`cloudflared tunnel route dns conversa <address>`).
3. In the tunnel configuration (`/etc/cloudflared/config.yml`, owned by root, `0644`):
   ```yaml
   tunnel: conversa
   credentials-file: /etc/cloudflared/conversa.json
   ingress:
     - hostname: conversa.other-domain.example
       service: unix:/srv/office-conversa/sock/guardiao.sock
       originRequest:
         httpHostHeader: conversa.other-domain.example
     - service: http_status:404
   ```
   The tunnel credentials stay with root/`cloudflared` (`0600`), never in the Office user's home.
4. `sudo cloudflared service install` and, in its unit, `User=cloudflared` and `Group=cloudflared`
   (`sudo systemctl edit cloudflared`); then `sudo systemctl restart cloudflared`.
5. The tunnel must set `X-Forwarded-Proto: https` and keep the `Host` (the guardian only accepts
   `Origin: https://<Host>`; otherwise it answers 403): check it by opening the Conversa. You can put Cloudflare Access
   in front as a second gate.

## The code and the session

- The code is **single-use** and only **registers one browser**: at that moment the browser generates its own random
  key, which stays in it, only on the Conversa origin and **non-extractable**, and the guardian stores it in
  `/var/lib/office-conversa/chaves.json` (`0700`, only `office-conversa`). The code dies there: caught later (by
  phishing, on a screen, in a password manager), it is useless. The browser signs every request with the key; the
  guardian only accepts signed, recent (5 min) and never-seen requests (403 without the right signature, 409 when
  repeated) and only those reach the bridge.
- **Later sign-ins** use **challenge–response**: the guardian sends a fresh challenge and the browser answers it with
  the key, with no code at all. A **12-hour session** opens (`__Host-` cookie, `HttpOnly`, `Secure`,
  `SameSite=Strict`). Five failures (wrong or already used code, wrong challenge or signature) block new attempts for
  15 minutes.
- **Another browser** (another phone, another computer): `sudo ./instalar-conversa.sh --office <u> --grupo-socket
  <g> --novo-codigo` generates another single-use code; browsers already registered keep working.
- **Lost a device or suspicious:** `sudo ./instalar-conversa.sh --office <u> --grupo-socket <g> --revogar` deletes
  every registered browser (open sessions drop) and shows a new code to register yours.
- A **different registrable domain** from the Office's is recommended for the Conversa (see «Installing», step 1).

## Permissions: `cartao` and `espelho`

- **`cartao`** (card, default): every `Bash`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit` and `WebFetch` action
  reaches you as a card to approve (once, in this conversation, or refuse). «Nesta conversa» only matches the same
  command, URL or file with the same content.
- **`espelho`** (mirror): the assistant uses your own Claude Code permissions (`auto` mode) and only the kit's
  launchers ask for a card. More convenient and **much less safe**. It only applies if the installer
  (`--permissoes espelho`, in both units) and the message itself both say `espelho`; either one saying `cartao` wins.

**Residual risk.** In either mode, what you approve runs as your user, with your `claude` and your files. A card
approved in a hurry, text from a page or file that tricks the assistant into asking for something dangerous, or your
phone in someone else's hands, are risks the design reduces but does not remove. Always read the card (the full
command or content), use `espelho` only if you accept the assistant doing almost anything without asking, and point
`--pasta-trabalho` at a folder you can afford to lose. Commands shaped like secrets, with invisible characters, or
too long never reach a card: they are denied.

The cards for the kit's launchers (the `ask` rules) match the command text: they are **best effort**, and an
obfuscated command (`l\ancar`, a script that calls them) gets through without a card, especially in `espelho`, where
they are the only card.

What the guardian closes and what remains (ADR-023, «Revisão: guardião»):
- Swapping an Office file no longer approves anything: the Conversa page comes from the guardian (checked against
  `SHA256SUMS` at start-up; one byte changed and it does not start), on another origin, and the Office JS cannot reach
  the browser key. A request without the key is refused (403) and is not written anywhere.
- The assistant cannot read the key: it runs as the `dono` and the keys belong to `office-conversa`.
- With `tls: externo` (the default) the Conversa's TLS terminates outside the Office user, who cannot reach the
  socket: whoever controls the Office cannot serve JS on the Conversa origin nor read its traffic.
- **Remains, only with `tls: caddy`:** Caddy, which terminates TLS, runs in the Office user's rootless Docker; whoever
  controls it can reconfigure it and serve their own JS at the Conversa address, or read the traffic.
- **Remains:** whoever reaches the Conversa address can fill the 15-minute lockout (denial of service of the login);
  they cannot guess the code (≈ 120 bits) nor a browser's key.
- **Remains:** the launcher cards are best effort (above) and external voice only has the 5 MB cap on the server.
- **Remains:** the assistant writes what the `dono` writes (the conversations, the bridge state); in `cartao` only
  after a click on a `Bash`, in `espelho` without one.
- **Remains:** the installation trusts once the Lite copy root extracted and checked (step 2).
- The code dies on first use, but until then anyone who sees it (terminal, shared `tmux` session, phishing) can register their browser first. Use it right away. If the Conversa says "code already used" and it was not you, run `sudo ./instalar-conversa.sh --revogar` (deletes every registered browser and issues a new code).
- A failed sign-in counts towards the global 15-minute lockout: anyone who reaches the site can lock re-entry (denial of service, no access).

## Spend

The day's cost (what `claude` reports, summed in the installation's time zone) shows in the panel header. **It never
blocks:** the Conversa has no daily limit; there are only per-conversation ceilings of 30 minutes and 100 turns
(`CONVERSA_DURACAO_MAX_MIN`, `CONVERSA_TURNOS_MAX` in the unit).

## The assistant's name

By default it is called «Assistente» (1 to 40 characters). Change it in the Conversa itself; it is stored by the
guardian (`/var/lib/office-conversa/prefs.json`), applies to whoever opens it and also shows on the Office button.
Resetting goes back to «Assistente».

## Voice

Three modes (`--voz` in the installer, which puts it in both units; the language is `--voz-idioma`, default `pt`):
- **`desligada`** (off, default): no microphone in the Conversa. Your phone keyboard can dictate too.
- **`local`**: `sudo ./instalar-conversa.sh --office <u> --voz local` creates a virtual environment for the owner with
  `faster-whisper` (the `small` model, on CPU) and downloads the model (needs `python3-venv`, ≈ 1 GB of disk and 2 GB
  of free memory per transcription). Audio never leaves the server. With no free memory the bridge warns instead of
  trying.
- **`externa`** (external): the audio is sent to a provider compatible with `POST /v1/audio/transcriptions` (the
  OpenAI protocol). **Privacy and cost become yours:** the provider hears your voice and bills per minute. The panel
  shows the warning and asks you to accept it once per provider. The key lives only in
  `~dono/.config/office-conversa/voz.env` (`0600`; `CONVERSA_STT_URL`, `CONVERSA_STT_MODELO`, `CONVERSA_STT_CHAVE`),
  never in `office.yaml`, the Office, the guardian, the events or the logs; after filling it in:
  `systemctl --user restart office-conversa`.

Dictated text goes into the message box: it is never an instruction, only your «Enviar» (Send) sends it. Audio up to
2 minutes and 5 MB; it is always deleted after transcription (the guardian deletes it once the bridge has read it).
With external voice the server only enforces the 5 MB (the browser checks the 2 minutes).

## Cloudflare Access (optional, documented only)

You can put Cloudflare Access in front of both addresses as a second gate (e-mail/SSO login before anyone even reaches
the Office or the Conversa); with the tunnel (`tls: externo`, the default) put it in front of the Conversa. The Lite
does **not read** the Access token: it does not replace the Conversa code; it is only an extra barrier. It is not tested
by the CI or the installer.

## Updating (0.5.2 → 0.6.0)

1. Update as usual with `get.sh` (see the README, «Updating»); `~/office-lite` (config, data, credentials) is not
   touched and `office.yaml` `versao: 1` stays valid. The Conversa stays off until you turn it on.
2. If you already have it (or want to enable it), run `sudo ./instalar-conversa.sh` again from a **new Lite copy
   owned by root only**, extracted and checked as in step 2 of «Installing» (the `get.sh` folder, owned by the Office
   user, is refused): it updates `/opt/office-conversa` and its `SHA256SUMS` and restarts the guardian and the
   bridge, keeping the registered browsers and `voz.env`. Then run `./install.sh` again. `get.sh` warns if it finds
   the Conversa installed. **Every Lite update requires running the installer again** (the guardian checks `/opt` at
   start-up).
3. **Coming from the Conversa without the guardian** (before the ADR-023 revision): the installer deletes the old key
   from the `dono` state, the `pids` and the `entrada.json` in `conversas/`, removes the Office's read access to
   `conversas/` and generates a **new code** (shown at the end). Change `office.yaml` as in step 6 of «Installing»
   (`nome`, `permissoes`, `voz` and `voz_idioma` go; `site` and `tls` come in) and create the DNS record for `site`.
   The Conversa moves to another address: the browser registers once with the code. The old `<pasta>/fila` folder is
   no longer used and holds the request texts: delete it.
4. **Coming from the first guardian revision** (a code that derived the key): the guardian's old `chave` is deleted
   and the installer shows a new code; each browser registers again. Without `tls` in `office.yaml`, the Conversa
   becomes `externo`: set up the tunnel (above) or write `tls: caddy` if you accept the warning.
5. The v1 contracts (decision queue, `cartoes.json`, requests, push) did not change; the bridge one moved to 2.

## Reverting

1. In `office.yaml`, set `conversa.ativa: false` and run `./install.sh`: the button disappears (and, with
   `tls: caddy`, Caddy stops serving the `site`; with the tunnel, stop it: `sudo systemctl disable --now cloudflared`).
2. `sudo systemctl disable --now office-conversa-guardiao`: the Conversa page and API stop answering.
3. As the `dono` user: `systemctl --user disable --now office-conversa`. The bridge stops and no longer spends tokens.
4. Local voice: delete the voice venv; the bridge then answers "voice unavailable".
5. The guardian state (`/var/lib/office-conversa`), the audit log and the conversation history stay where they are, as
   a record. Delete them by hand if you don't want them (and the user with `sudo userdel office-conversa`).

## Contract

The format of the events and routes is in [`docs/conversa-contrato.md`](docs/conversa-contrato.md) (in Portuguese).
