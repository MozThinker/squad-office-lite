# Squad Office Lite

[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

🇬🇧 **English** | 🇵🇹 [Português](README.md)

> **Note:** the Office interface is currently in Portuguese only, so this README quotes on-screen text in Portuguese,
> with the English translation in parentheses.

A pixel-art office where you follow, from your browser or your phone, a squad of Claude Code agents working on your
projects. You write a card on the Quadro (board), drag it to «Em curso» ("In progress") and the squad picks up the
work: it creates a branch, writes the code, tests it and opens a pull request on GitHub for you to review. Everything
runs on your own VPS or on your PC, with Docker Desktop.

## Installation

There are two ways to install the Lite:
- **on a Linux VPS (Ubuntu)**, following the steps in this section: the squad keeps working even with your PC switched
  off, and the Office can have a domain, HTTPS and phone notifications;
- **on your PC (Windows, macOS or Linux) with Docker Desktop**, no VPS needed: see
  [On your PC](#on-your-pc-windows-macos-linux-with-docker-desktop). The squad only works while the PC is on.

**You need:**
- a VPS running **Ubuntu 22.04 or later** (CI tests the installation on 22.04, 24.04 and 26.04) and SSH access with a
  regular user (not root; if the VPS only has root, see [New VPS with root only](#new-vps-with-root-only));
- an **x86_64** processor (tested). **arm64** is accepted by `get.sh`, with a warning, but is not tested: the image
  may fail to build;
- memory: **4 GB or more** (a Claude Code requirement); in our test, short tasks ran with 1 GB;
- **Docker** with the `docker compose` plugin ([official instructions](https://docs.docker.com/engine/install/ubuntu/)):
  recommended in **rootless** mode, with a user just for the Office and the squads, without `sudo` (see
  [Rootless Docker](#rootless-docker)); it still works with your user in the `docker` group, which is equivalent to
  root;
- **Claude Code** (`claude`) installed on that VPS and a way to authenticate it: an **API key** (recommended; the
  installer asks for it) or a **subscription token**, which you write to a file yourself (the installer does not ask
  for it; see [Claude credentials](#claude-credentials));
- the **GitHub CLI** (`gh`) installed and logged in (`gh auth login`), preferably with a
  [token limited to the Office repositories](#github-token);
- a **name and email for the squad's commits** (`git config --global user.name`/`user.email`; the installer asks for
  them if they are missing);
- optional but recommended: a **domain** (e.g. `office.exemplo.com`) with a DNS A record pointing to the VPS's IP.
  It works without a domain, but only over HTTP (no padlock and no phone notifications).

**Steps:**

1. **Prepare the VPS.** Install Docker in rootless mode for the Office user (recommended; see
   [Rootless Docker](#rootless-docker)) and log in with it. Alternative, which gives your user root: install Docker
   and add yourself to the group, `sudo usermod -aG docker $USER`; log out and back in.
2. **Install and authenticate `claude` and `gh`.**
   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   gh auth login     # depois de instalar o gh (instruções abaixo)
   ```
   With an API key, have it at hand: the installer asks for it. With the subscription token, write it to a file
   yourself after installing (the installer neither asks for it nor stores it; see
   [Claude credentials](#claude-credentials)).
   Install `gh` following the [official instructions for Ubuntu](https://github.com/cli/cli/blob/trunk/docs/install_linux.md);
   for `gh auth login`, preferably use a [token limited to the Office repositories](#github-token).
3. **Download and install the Office.** The URL is pinned to the version (`v0.5.2`), so you always read the same
   script that the book and this README describe.
   **Careful way** (recommended: you read the script before running it):
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.5.2/get.sh
   less get.sh
   bash get.sh --versao 0.5.2
   ```
   **One command:**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.5.2/get.sh | bash -s -- --versao 0.5.2
   ```
   > This command downloads a script from the internet and runs it on your server, as your user. Only do it with
   > URLs you trust, and preferably read it first. `get.sh` checks the package's sha256: that catches a corrupted
   > download, but does not protect you if the repository itself is altered by someone. Run it on a test server,
   > never as `root`.

   `get.sh` checks that the system is Ubuntu, the processor, and that you are not root; it downloads the latest
   published version (GitHub Releases), checks its sha256 against the `SHA256SUMS` published alongside it, extracts it
   to `~/squad-office-lite` and runs `install.sh`. It needs neither `git` nor `gh`. `get.sh` itself has no sha256:
   that is what the careful way is for. Options (after `bash -s --` in the one-line command, or after `bash get.sh`):
   - `--sem-perguntas` (no questions): the answers come from the environment (see the header of `install.sh`;
     `OFFICE_GIT_NOME` and `OFFICE_GIT_EMAIL` for the git identity; without them, and with no identity in git, it only
     warns). E.g.: `curl -fsSL …/get.sh | bash -s -- --versao 0.5.2 --sem-perguntas`;
   - `--versao X.Y.Z`: installs that version instead of the latest (`… | bash -s -- --versao 0.5.2`);
   - any other options are passed on to `install.sh` (e.g. `--proxy traefik --rede <rede>`).

   **Optional origin check.** The sha256 only confirms that the package is not corrupted. To confirm that it was
   built by the public repository (GitHub attestation; needs `gh` logged in), download the package manually and run:
   ```bash
   gh attestation verify squad-office-lite-0.5.2.tar.gz --repo MozThinker/squad-office-lite
   ```
   Only versions published with an attestation pass. A failure here is a reason not to install and to report it (see
   [`SECURITY.en.md`](SECURITY.en.md)).

   The installer checks what is missing (and warns, without stopping, if `gh` is not logged in: tasks would end
   without a PR), asks for the domain (Enter = no domain), the username and the password (12 or more characters; if
   the two entries do not match, it asks again, up to 3 times), the Claude credentials (see the menu in
   [Claude credentials](#claude-credentials)), the name and email for the squad's commits (only if git does not have
   them yet; existing ones are never replaced) and starts everything. With a domain, before «Pronto» ("Done") it
   checks that the DNS points to this VPS and, if it does not, says so (it only warns).
   **Time:** in our test (VPS with 1 vCPU and 1 GB, Ubuntu 26.04, 8 November 2026), `install.sh --sem-perguntas` took
   19 s; the first image build (downloading the base images `python:3.13-alpine` and `caddy:2.11.9-alpine`) may
   take longer. Do not move the `~/squad-office-lite` folder after installing.

   **Alternative with `git`:** `gh repo clone MozThinker/squad-office-lite ~/squad-office-lite`,
   `cd ~/squad-office-lite` and `./install.sh`. If the repository is private, `gh repo clone` uses the `gh` session (a
   `git clone https://…` without it asks for a username and password and fails); afterwards switch back to the
   *fine-grained* token (see [GitHub token](#github-token)).
4. **Open the address** the installer shows at the end (`https://<o teu domínio>` or `http://<IP da VPS>`) and log in
   with the username and password you chose. Until the first task is delivered, the office shows
   «Primeiros passos» ("First steps"): runner sending a heartbeat, `claude` installed, Claude credential and `gh`
   session, each with ✓ or ✗ and, on ✗, the command that fixes it.
5. **Launch the first task.** In the `▦ Quadro`, create a card in the «Exemplo» ("Example") project (for example
   «Acrescentar um ficheiro CONTRIBUTING.md», "Add a CONTRIBUTING.md file"), drag it to «Em curso» (on a phone: long-press
   the card, or use «Lançar…» ("Launch…") in the ⋯ menu) and press «Lançar» ("Launch"). The dialog asks for the type
   (new feature or fix), the size (short task, up to 3 h, or goal with a plan, up to 8 h) and «O que queres?» ("What
   do you want?"): write as you would to a colleague. The short branch name is generated from the title (you can
   change it under «Avançado», "Advanced"). Within a minute or two the squad shows up
   working in the office; at the end, the card moves to «À espera de …» ("Waiting for …") or «Feito» ("Done") (the
   move happens when someone opens the Quadro: if nobody opens it, the card stays in «Em curso» until then).

**For your own projects:** clone each repository into `~/projectos/<nome>` and add it to `~/office-lite/office.yaml`
(the `projectos` section, see [Reference](#officeyaml)). Then restart the Office:
`docker compose -f ~/office-lite/docker-compose.yml restart office`. The runner rereads the config every minute and
needs no restart. If you changed the `raizes`, run `./install.sh` again (it does not ask anything again).

### Updating

The same `get.sh`, without `--versao` (it installs the latest published version; read the warning in “Installation”):
```bash
curl -fsSL https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.5.2/get.sh | bash
```
The installed version moves to `~/squad-office-lite.anterior-<data>` and the new one goes to `~/squad-office-lite`.
`~/office-lite` (config, `.env`, credentials and data) is not touched by `get.sh`; `install.sh` only asks again for
what is missing (without `claude.env`, for example, it asks for the credentials again: choose 2 or 3 if you use the
token or the `claude` login). To roll back: `… | bash -s -- --versao <versão anterior>`. Delete the `.anterior-*`
folders when you no longer need them. See what changed in `CHANGELOG.md` (in Portuguese). If you installed with
`git`, after `git pull` run `./install.sh` again (0.1.3 mounts a new folder in the container; without it the Office
says «O runner ainda não deu sinal», "The runner has not checked in yet").

### New VPS with root only

Many VPSs come with only `root`. Create a regular user and log in with it from then on (replace `u` with any name you
like):
```bash
adduser u                                   # pede a palavra-passe do novo utilizador
usermod -aG sudo u
install -d -m 700 -o u -g u /home/u/.ssh
install -m 600 -o u -g u /root/.ssh/authorized_keys /home/u/.ssh/authorized_keys
```
Log out and log in with `ssh u@<vps>`; from then on follow the steps above with that user (not with `root`).

### Rootless Docker

The `docker` group is equivalent to root: anyone in it can, with a `docker run -v /:/host`, read and change anything
on the VPS (including other users' folders and credentials). Since the squad runs programs as the Office user, the
recommended path on the VPS is **rootless Docker**: Docker runs as that user, without root and without the `docker`
group, and the worst a squad program can do is what that user could already do. The Docker documentation
([Rootless mode](https://docs.docker.com/engine/security/rootless/), checked on 13/11/2026) is the source of these
steps.

The examples use two users: `dono` (you, with `sudo`) and `fabrica` (the Office and squads user, **without** `sudo`).
Replace the names with yours. What needs `sudo` is done by `dono`; the rest by `fabrica`.

**As `dono`** (once):
```bash
# 1. Docker Engine pelas instruções oficiais (repositório apt do Docker), o que o modo rootless precisa, git e gh
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
  docker-ce-rootless-extras uidmap dbus-user-session git gh
# 2. o Docker de sistema (root) não é preciso: desliga-o (é ele que dá root ao grupo docker)
sudo systemctl disable --now docker.service docker.socket containerd
sudo rm -f /var/run/docker.sock
# 3. o utilizador das squads, sem sudo nem palavra-passe (só entra com a tua chave SSH)
sudo adduser --disabled-password --comment "" fabrica
sudo install -d -m 700 -o fabrica -g fabrica ~fabrica/.ssh
sudo install -m 600 -o fabrica -g fabrica ~/.ssh/authorized_keys ~fabrica/.ssh/authorized_keys
grep '^fabrica:' /etc/subuid /etc/subgid     # tem de mostrar uma linha em cada (o adduser cria-as)
# 4. o Caddy precisa das portas 80 e 443 (abaixo da 80, incluindo o SSH na 22, continua só para o root)
echo net.ipv4.ip_unprivileged_port_start=80 | sudo tee /etc/sysctl.d/60-office-lite.conf && sudo sysctl --system
# 5. o Docker, o Office e o runner da fabrica continuam a correr sem sessão aberta
sudo loginctl enable-linger fabrica
# 6. só se usas a ufw: com Docker rootless as portas publicadas passam pela firewall
sudo ufw allow 80,443/tcp
```
`git` and `gh` are in step 1 because `fabrica` can't install them (`install.sh` won't go on without them). Step 2 may
warn «Stopping docker.service, but it can still be activated by: docker.socket»: it's harmless (`docker.socket` is
turned off by the same command). Step 3 creates `fabrica` without a password (its home is `drwxr-x---`; on Ubuntu
22.04 replace `--comment ""` with `--gecos ""`) and copies **your** SSH keys to it (`dono`'s
`~/.ssh/authorized_keys`). On Ubuntu 24.04 and later the `apparmor` package already ships the profile for the
apt-installed `rootlesskit` (nothing to do).
Also check that your home is not readable by `fabrica`: `ls -ld ~` must show `drwxr-x---` (or tighter); if not,
`chmod 750 ~`.

**As `fabrica`**, log in over SSH: `ssh fabrica@<vps>` (it works with your key, copied in step 3). Don't use `su` or
`sudo -iu fabrica` for what follows: without a systemd session `XDG_RUNTIME_DIR` is missing and `systemctl --user`
fails, so rootless Docker neither installs nor starts (once installed, `docker` also works in a `sudo -iu`
session).
```bash
dockerd-rootless-setuptool.sh install         # cria e liga o docker.service do utilizador e o contexto «rootless»
docker info --format '{{.SecurityOptions}}'   # tem de incluir name=rootless
```
Then follow [steps](#installation) 2 to 5 as `fabrica` (step 1 is done). `install.sh` sees that Docker is rootless
and:
- refuses if ports 80/443 are not allowed (it prints the command for `dono`);
- writes `OFFICE_UID=0` and `OFFICE_GID=0` to `.env`: in rootless mode the container's root is `fabrica` on the host,
  and that is how files written by the Office end up owned by `fabrica` (the Office container runs with no
  capabilities and with `no-new-privileges`);
- if `docker` only reaches the rootless daemon through `DOCKER_HOST`, it switches to the `rootless` context (the
  runner and the squads don't get `DOCKER_HOST`).

`doctor` says «Docker rootless a responder», checks the ports and `OFFICE_UID`, and warns (`⚠`, without changing the
exit code) if the user is in the `docker` group.

**Known limitations** (all from the Docker documentation):
- Caddy doesn't see the real client IP (it sees an internal address): the `ip` field of decisions gets that address.
  The Docker documentation describes how to keep it (another *port driver*); we haven't tested it;
- images and volumes live in `fabrica`'s `~/.local/share/docker` (it counts towards its home's disk);
- the compose `mem_limit` works with cgroup v2 and systemd, which Ubuntu 22.04 and later already have;
- `--proxy traefik` only works with a Traefik in the same rootless Docker (not tested).

**Moving an existing installation to rootless** (with the Office user, `u`): stop it while the system Docker is still
running (`docker compose -f ~/office-lite/docker-compose.yml down`); `dono` does steps 1, 2, 4, 5 and 6 above (with
`u` instead of `fabrica`); then, in a new `u` session, `dockerd-rootless-setuptool.sh install` and
`cd ~/squad-office-lite && ./install.sh` (it changes `OFFICE_UID`/`OFFICE_GID` in `.env` and builds the image in
rootless Docker; Caddy requests the certificate again). Finally `dono` removes `u` from the group:
`sudo gpasswd -d u docker`. To go back to the `docker` group, the reverse: `install.sh` restores your uid and gid in
`.env`.

**Undo** (deletes the Office, the data and `fabrica`; save what you want first, see [Backups](#backups)):
```bash
# com a fabrica: pára o Office, desinstala o Docker rootless e apaga os dados dele (o uninstall não os apaga)
docker compose -f ~/office-lite/docker-compose.yml down -v
dockerd-rootless-setuptool.sh uninstall
rootlesskit rm -rf ~/.local/share/docker
# com o dono: tira o linger, termina os processos da fabrica e apaga-a; repõe as portas só do root
sudo loginctl disable-linger fabrica
sudo loginctl terminate-user fabrica
sudo deluser --remove-home fabrica
sudo rm /etc/sysctl.d/60-office-lite.conf
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=1024
```
The data `rm` runs inside `rootlesskit` because some of the files belong to `fabrica`'s subuids and a plain `rm` can't
delete them.

**Not tested yet:** `get.sh` with `fabrica` in rootless mode (we tested `install.sh`) and published ports with `ufw`
active.

### GitHub token

`gh auth login` through the browser gives access to all your repositories. For the squad, a **fine-grained** token is
better (GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens), with:
- **Repository access:** only the repositories of the Office projects (the ones in `~/projectos`);
- **Permissions:** **Contents** and **Pull requests** set to **Read and write** (read-only *Metadata* is always
  included); optionally, **Actions** and **Commit statuses** set to **Read-only** for the squad's `gh run`/`gh pr
  checks`. No **Workflows**: the squad cannot change `.github/workflows/` (on purpose);
- **Expiration:** a date (e.g. 90 days), and renew it.

Log in to `gh` with it in one of these ways (the token never goes in command arguments):
- `gh auth login` and choose to paste a token (*Paste an authentication token*);
- or `gh auth login --with-token`, which reads the token from stdin: paste it, press Enter and `Ctrl-D`.

Then run `gh auth setup-git` (`git push` will use the same session) and confirm with `gh auth status`.

The `gh` manual recommends the `GH_TOKEN` variable for automation. In the Lite it does not work: the runner passes
only an allowlist of variables to `claude` (see [Claude credentials](#claude-credentials)), and a `GH_TOKEN` in
`runner.env` reaches neither the agent nor the runner's `git` (only the `gh` the runner uses to confirm the PR). Use
the session saved by `gh auth login`.

If `squad-office-lite` comes from a private repository and you cloned it with other access (your whole account, or a
token with write access), log in again with the *fine-grained* token (`gh auth login`) and confirm with
`gh auth status` that it is the active session: with write access to the Office repository, the squad could push to
the Office itself.

### What to do if…

Start with the diagnostics, on the VPS:
```bash
python3 ~/squad-office-lite/runner/office_runner.py doctor
```
One line per check (`office.yaml`, Docker rootless or through the `docker` group and, in rootless mode, ports
80/443 and `OFFICE_UID`; *linger*, the runner timer, `claude` on the PATH,
Claude credential, `gh` session, the domain's DNS, ports 80 and 443 and the installed version): `✓` or `✗ … → <comando
que corrige>` (the command that fixes it). It exits with 0 if everything is fine, 1 if not. A `⚠` line warns without changing the exit code (the
user is in the `docker` group). It sees what the runner
would see: the credential and the `GH_TOKEN` from `runner.env`, and `claude` also in `~/.local/bin` and on the unit's
PATH. Of the credential it only sees the format and the file mode, never the value. It works even with an invalid
`office.yaml` (it says so and carries on).

- **`get.sh` stops with an error.** Nothing was installed and the previous version (if any) stays where it was.
  «sem terminal; usa --sem-perguntas» ("no terminal; use --sem-perguntas"): the session has no terminal for the
  questions (e.g. `ssh` without `-t`); log in with `ssh -t` or add `bash -s -- --sem-perguntas`. «não corras como
  root…» ("don't run as root…"): `get.sh` first shows the 4 commands from
  [New VPS with root only](#new-vps-with-root-only). «o sha256 não confere; nada foi instalado» ("the sha256 does not
  match; nothing was installed"): the download was corrupted or tampered with; run it again and, if it happens again,
  do not install and report it (see [`SECURITY.en.md`](SECURITY.en.md)).
  «este instalador é para Ubuntu» ("this installer is for Ubuntu"), «processador não suportado: …» ("unsupported
  processor: …") and «falta curl» ("curl is missing"; or `sudo`, `tar`, `sha256sum`) say what is missing.
- **«a porta 80 (ou 443) está ocupada»** ("port 80 (or 443) is in use"). There is another web server on the VPS. See
  which one with `sudo ss -ltnp 'sport = :80'` and stop it (e.g. `sudo systemctl disable --now apache2` or `nginx`).
  If it is a **Traefik** you already use, do not stop it: install with
  `./install.sh --proxy traefik --rede <rede do Traefik>` (needs a domain; Traefik must have the `websecure`
  entrypoint and a certresolver, `letsencrypt` by default, changeable with `--certresolver`).
- **the domain does not open or gives a certificate error.** The DNS does not point to the VPS yet (it can take up to a
  few hours; the installer and `doctor` warn «o domínio … ainda não resolve» ("the domain … does not resolve yet") or
  «aponta para …, mas esta VPS tem …» ("points to …, but this VPS has …")).
  Check with `getent hosts <domínio>`; open ports 80 and 443 in the firewall (`sudo ufw allow 80,443/tcp`). Once the
  DNS is right: `docker compose -f ~/office-lite/docker-compose.yml restart caddy`.
- **I don't have a domain / I want to add one later.** Without a domain the Office is at `http://<IP>`: the password
  travels unencrypted and phone notifications do not work. To add a domain, set `OFFICE_SITE` and `OFFICE_DOMINIO` in
  `~/office-lite/.env` to the domain and run `./install.sh` again.
- **the task ends straight away in «erro»** ("error"). The card shows the task's result (masked) and, for common
  errors, the command that fixes it. It is almost always the Claude credentials: if the result mentions
  «credenciais do Claude» ("Claude credentials"), fix `~/office-lite/claude.env` as the message says (e.g.
  `chmod 600`) or replace them with `./install.sh --credenciais`. If it mentions «runner.settings inválido»
  ("invalid runner.settings"), fix the `runner.settings` file (JSON; `deny`/`allow`/`ask` must be lists of strings;
  up to 64 KB). Otherwise look at the end of `~/office-lite/dados/tasks/<id da tarefa>/claude.log` (invalid key, not
  logged in, or a permission mode your version of `claude` does not know; see
  [Runner](#runner)). Without `gh auth login` the task ends without a PR («Terminou sem PR», "Finished without a PR":
  check `gh auth status`).
- **the squad says it cannot run a command.** With `runner.permission_mode: dontAsk` (the default) it only runs what
  is in `runner.ferramentas`. Each task's prompt has a «Permissões» ("Permissions") section, generated from that list,
  which tells the squad what it can run and how (one command per call, files only through Write/Edit, no `&&`, pipes
  or heredocs). If the command is not on the list, add it in `office.yaml` (e.g. `- Bash(cargo *)`; see
  [Runner](#runner)): the following tasks will see it.
- **the commit fails with «Please tell me who you are».** The VPS's git has no name or email for commits. Run
  `git config --global user.name "O teu nome"` and `git config --global user.email "tu@exemplo.com"` (or run
  `./install.sh` again, which asks for them if missing) and launch the task again.
- **the installer rejects the Claude key.** The API key starts with `sk-ant-` (without `oat`) and goes in option 1. A
  subscription token (`sk-ant-oat01-…`) is not pasted into the installer: write it to `claude.env` yourself (see
  [Claude credentials](#claude-credentials)).
- **I want to stop a task.**
  `python3 ~/squad-office-lite/runner/office_runner.py parar <id da tarefa>` (the id is the name of the task's folder
  in `~/office-lite/dados/tasks/`). It kills that task's `claude`, closes it as «erro» with «parada pelo utilizador»
  ("stopped by the user") and leaves a marker (`parar` in the task's folder) so the runner never resumes it. A task
  that has already finished stays as it is. It works even with an invalid `office.yaml`. A SIGTERM without `parar`
  (e.g. a VPS reboot) does not close the task: the runner will resume it.
  `systemctl --user stop office-runner.timer` does not stop tasks: it only stops new passes (tasks run outside the
  unit, by design: `KillMode=process`).
- **the task finished but the card did not change column.** The card only moves to «À espera de …» or «Feito» when
  someone opens the Quadro (the move happens in `GET /api/quadro`). Open the `▦ Quadro`.
- **I press «Lançar» and nothing happens** (the card stays at «a lançar…», "launching…", and, after 3 min, says «O
  runner ainda não pegou nisto», "The runner hasn't picked this up yet"; or the office shows the banner «O runner não
  dá sinal desde HH:MM», "No heartbeat from the runner since HH:MM"). The runner (a minute-by-minute timer) is not
  running. «Tentar de novo» ("Try again") only rereads the Quadro, it does not send another request. Check
  `systemctl --user status office-runner.timer` and `journalctl --user -u office-runner -n 50`. If the installer
  warned about *linger*, run `sudo loginctl enable-linger $USER` (without it the runner stops when you leave SSH). To
  turn it on by hand: `systemctl --user enable --now office-runner.timer`. To test one pass:
  `python3 ~/squad-office-lite/runner/office_runner.py --uma-vez`.
- **I want to change the password.** `cd ~/squad-office-lite && ./install.sh --palavra-passe`.
- **I want to replace the Claude API key.** `cd ~/squad-office-lite && ./install.sh --credenciais`. The subscription
  token you replace yourself in the file (see [Claude credentials](#claude-credentials)).
- **I got «o office.yaml mudou» ("the office.yaml changed") in the journal or on Telegram.** The runner warns once
  for each change to `office.yaml`. If it wasn't you, review it right away (`ferramentas`, `permission_mode`,
  `settings`, `projectos`) and look at the recent tasks: it may have been a program run by the squad (see
  [Security](#security)).
- **the Office shows a «config inválida» ("invalid config") banner.** `office.yaml` has an error; the Office uses the
  example config until you fix it. `python3 ~/squad-office-lite/officecfg.py validar ~/office-lite/office.yaml` tells
  you the error (the line, for syntax errors; the field, for the others).

### Backups

What to keep:
- the whole of `~/office-lite/`: `office.yaml`, `.env` (password hash and proxy secret), `claude.env`, `runner.env`
  and `dados/` (tasks, cards, decisions, queues, the Biblioteca (library) and `push/`, with the notification keys and
  subscriptions; without `push/` every phone has to turn them on again). If you changed `raizes.dados`, keep that
  folder too;
- with Caddy, the Docker volume `office-lite_caddy_data` (certificates and the Let's Encrypt account key; without it
  Caddy requests new certificates, which works but is rate-limited).

`~/squad-office-lite` is just code (`get.sh` puts it back) and the projects in `~/projectos` are on GitHub.

```bash
umask 077                                       # a cópia tem segredos: só tu a lês
systemctl --user stop office-runner.timer       # sem passagens novas durante a cópia
tar -czf ~/office-lite-$(date +%F).tar.gz -C ~ office-lite
docker run --rm -v office-lite_caddy_data:/d:ro alpine tar -czf - -C /d . > ~/caddy_data-$(date +%F).tar.gz
systemctl --user start office-runner.timer
```
Take both files off the VPS (e.g. `scp` to your computer). To restore on a new VPS: install the Office, stop the
runner and the compose (`docker compose -f ~/office-lite/docker-compose.yml down`), extract the copy
(`tar -xzf office-lite-<data>.tar.gz -C ~`), restore the volume
(`docker run --rm -i -v office-lite_caddy_data:/d alpine tar -xzf - -C /d < caddy_data-<data>.tar.gz`) and run
`./install.sh` again (it does not ask for what already exists).

### Uninstalling

```bash
systemctl --user disable --now office-runner.timer
rm ~/.config/systemd/user/office-runner.service ~/.config/systemd/user/office-runner.timer
systemctl --user daemon-reload
docker compose -f ~/office-lite/docker-compose.yml down --rmi local   # contentores e a imagem do Office
rm -rf ~/squad-office-lite ~/squad-office-lite.anterior-*
```
This does not delete your data. **Only if you want to lose it for good** (make the backup above first): the Caddy
certificates (volume) and then config, credentials and data:
```bash
docker volume rm office-lite_caddy_data office-lite_caddy_config
rm -rf ~/office-lite
```
If tasks are running, stop them first (`office_runner.py parar <id>`). What remains: the projects in `~/projectos`
(with the tasks' worktrees in `.worktrees/`), `claude`, `gh` and Docker. If you don't use *linger* for anything else:
`sudo loginctl disable-linger $USER`. Also revoke the GitHub token and the Claude API key you created just for the
Office.

## On your PC (Windows, macOS, Linux) with Docker Desktop

No VPS needed: the Office, Caddy (which asks for the password) and the runner run in containers in Docker Desktop on
your PC. You don't install `claude`, `gh`, `git` or Python: they come in the runner image. The Office opens at
`http://localhost:8080`, on this PC only. The squad only works while the PC is on and Docker Desktop is open (see
[Docker mode limitations](#docker-mode-limitations)).

**You need:**
- **Docker Desktop** installed and open, with Docker Compose **2.23.1 or later** (it ships with Docker Desktop; you
  check it in step 1). Requirements according to the official pages (checked on 8 November 2026):
  - **Windows** ([page](https://docs.docker.com/desktop/setup/install/windows-install/)): Windows 11 23H2 (build
    22631) or Windows 10 22H2 (build 19045) or later; WSL 2 (version 2.1.5 or later); 8 GB of memory;
    virtualisation enabled in the BIOS/UEFI. **Windows Home:** the page's list of requirements (checked on 9 November 2026)
    only names the Pro, Enterprise and Education editions, but the same page says the Home and Education editions run
    Linux containers (only Windows containers need Pro or Enterprise). The Lite only uses Linux containers, so it
    should work on Home with WSL 2; we haven't confirmed it yet: if you try it, let us know (see
    [Community](#community));
  - **macOS** ([page](https://docs.docker.com/desktop/setup/install/mac-install/)): the current macOS version or one
    of the two previous ones, Apple Silicon or Intel; at least 4 GB of memory;
  - **Linux** ([page](https://docs.docker.com/desktop/setup/install/linux/)): the one for your distribution;
  - Claude Code asks for **4 GB or more** of memory;
- the [Docker Desktop licence](#docker-mode-limitations) to cover your use (it is free for personal use,
  education, non-commercial open source and small businesses);
- a Claude credential: an **API key** (recommended) or the **subscription token** (Pro/Max; see
  [Subscription token on the PC](#subscription-token-on-the-pc));
- a *fine-grained* **GitHub token** for the Office repositories only (see [GitHub token](#github-token));
  without it the squad works, but doesn't open PRs;
- a **name and e-mail for the squad's commits** (`configurar` asks for them).

**Steps:** open **PowerShell** (Windows) or the **Terminal** (macOS, Linux) and run the commands in this order.

1. **Install Docker Desktop** from the official page for your system (above) and open it. On Windows, the installer
   enables WSL 2 and may ask you to restart. Check the Compose version (2.23.1 or later):
   ```
   docker compose version
   ```
2. **Create a folder for the Office and go into it.** This is where the `compose.yaml` lives and, by default, the
   `projectos` folder with the repositories the squad can change. Run all the following commands inside this folder.
   In PowerShell (it ends up in `C:\Users\<nome>\office`):
   ```powershell
   mkdir $HOME\office
   cd $HOME\office
   ```
   In the Terminal (on macOS it ends up in `/Users/<nome>/office`):
   ```bash
   mkdir -p ~/office && cd ~/office
   ```
3. **Download the `compose.yaml`** for version 0.5.2. The Office and runner images are pinned by digest: what runs is
   always what was published with this version.
   In PowerShell (it's `curl.exe`, not `curl`):
   ```powershell
   curl.exe -fsSLO https://github.com/MozThinker/squad-office-lite/releases/download/v0.5.2/compose.yaml
   ```
   In the Terminal:
   ```bash
   curl -fsSLO https://github.com/MozThinker/squad-office-lite/releases/download/v0.5.2/compose.yaml
   ```
   You can also download it with the browser, from the `v0.5.2` release page, and save it in this folder as
   `compose.yaml`. To change the port or the projects folder, see
   [Changing the port, the address or the projects folder](#changing-the-port-the-address-or-the-projects-folder) (optional).
   **On Linux**, create the `.env` with your user and group now, before step 4, so that the project files belong to
   you:
   ```bash
   printf 'OFFICE_UID=%s\nOFFICE_GID=%s\n' "$(id -u)" "$(id -g)" >> .env
   ```
4. **Configure:**
   ```
   docker compose run --rm configurar
   ```
   The first time it downloads the images (a few hundred MB). It asks, in this order and only for what is still missing:
   - the Office user and password (12 or more characters; not shown on screen; you repeat it, up to 3 times);
   - the projects: it lists the git repositories in the `projectos` folder and you pick by number (Enter = all); if
     there are none, it offers to create the `exemplo` project;
   - the name and e-mail for the squad's commits;
   - the Claude credential (see [Subscription token on the PC](#subscription-token-on-the-pc)):
     ```
       1) chave de API (recomendada; paga por uso)
       2) token da subscrição (geras tu com o claude setup-token e colas aqui)
       3) agora não
     ```
     (1: API key, recommended, pay per use; 2: subscription token, which you generate with `claude setup-token` and
     paste here; 3: not now)
   - the GitHub token (not shown on screen; Enter = not now).

   It stores everything in Docker volumes, not in loose files on your PC: the password only as a bcrypt hash (the
   `proxy` volume, which the runner doesn't see), the Claude credential in the `segredos` volume (only the runner reads
   it) and the `gh` session in the runner's home (the `runner-home` volume). Secrets never appear on screen. At the end
   it shows the next steps. Running it again doesn't ask for what already exists.
5. **Start:**
   ```
   docker compose up -d
   ```
6. **Open <http://localhost:8080>** and sign in with the user and password you chose. Until the first task is
   delivered, the office shows «Primeiros passos» ("First steps"), as on the VPS, with the `docker compose …` commands
   that fix each ✗. If the browser can't connect, you probably ran `up -d` before `configurar`: Caddy only starts once
   there is a password. Run `configurar` and then `docker compose restart office caddy`.
7. **Launch the first task**, as in step 5 of [Installation](#installation): in the `▦ Quadro` (board), a card in the
   `exemplo` project, dragged to «Em curso» ("In progress"), and «Lançar» ("Launch"). `exemplo` has no remote on
   GitHub: the squad works on a branch and finishes without a PR («Terminou sem PR», "Finished without PR"). PRs show
   up in your own projects.

**For your own projects:** the `projectos` folder (or the one in `OFFICE_PROJECTOS`) is the only folder on the PC the
squad sees. Put each repository there. Without `git` on the PC, clone with the runner's `gh` (after you configure the
GitHub token) and pick the projects again:
```
docker compose run --rm runner gh repo clone <conta>/<repositório> /projectos/<repositório>
docker compose run --rm configurar --projectos
docker compose restart office
```
`--projectos` only replaces the `projectos` list in the `office.yaml`. The runner reads the config every minute; the
Office only at startup (hence the `restart office`).

### Subscription token on the PC

With option 2 of `configurar`, you generate the token and paste it yourself; the program never fetches it for you,
never runs `claude setup-token` and never reads the `claude` login. Before asking for it, `configurar` explains what
it is, where it is stored and the risk:
1. leave `configurar` waiting and open **another** PowerShell or Terminal window, in the same folder;
2. run the `claude` from the runner image:
   ```
   docker compose run --rm runner claude setup-token
   ```
   Open the link it shows in the browser, confirm with your account and copy the line that starts with `sk-ant-oat01-`
   (not the code the browser shows);
3. go back to the first window and paste it (it isn't shown on screen).

It is stored as one line in the `claude.env` of the `segredos` volume (mode 0600), which only the runner reads; the
Office never sees it. As on the VPS, the token is in `claude`'s environment while a task runs: a program the squad
runs can read it. If you suspect something, revoke it on claude.ai and paste another with
`docker compose run --rm configurar --credenciais`. It uses your subscription's limits.

### Changing the port, the address or the projects folder

Without a `.env` the defaults apply. To change them, download the release's `env.example` to a `.env` in the
`compose.yaml` folder, remove the `#` from the lines you want to change and run `docker compose up -d` (on Linux you
already have a `.env` from step 3: don't replace it, add the lines to it):
```powershell
curl.exe -fsSLo .env https://github.com/MozThinker/squad-office-lite/releases/download/v0.5.2/env.example
```
(in the Terminal, `curl` instead of `curl.exe`). The attachment is called `env.example` because GitHub renames
attachments whose names start with a dot.

| variable | what (default) |
|---|---|
| `OFFICE_PORTA` | the Office's port on the PC (`8080`); change it if 8080 is taken: `OFFICE_PORTA=8081` ⇒ `http://localhost:8081` |
| `OFFICE_ENDERECO` | where the Office listens (`127.0.0.1`, this PC only). `0.0.0.0` opens it to other devices on the local network (e.g. your phone), **over HTTP**: the password travels unencrypted; only on a network you trust |
| `OFFICE_PROJECTOS` | the projects folder (`./projectos`, relative to the `compose.yaml` folder, or absolute). On Windows with forward slashes: `OFFICE_PROJECTOS=C:/Users/<nome>/projectos`. On macOS, only folders inside `/Users`, `/Volumes`, `/private`, `/tmp` or `/var/folders` (the ones Docker Desktop shares by default) |
| `OFFICE_UID` / `OFFICE_GID` | user and group of the `office` and `runner` containers (`10001`). On Windows and macOS keep the default; on Linux, use yours (`id -u`, `id -g`) |

If you change the projects folder, put the repositories there and run `docker compose run --rm configurar --projectos`.
Choose `OFFICE_UID`/`OFFICE_GID` before the first `configurar`: changing them later leaves the files already created in
the volumes with the old owner. The volumes are mounted with `nocopy` (Docker copies nothing from the image into
them): each volume's folders start out owned by root and it is `configurar` that gives them your
`OFFICE_UID`/`OFFICE_GID`. That's why the runner only works after `configurar`; if the `doctor` shows
`✗ pastas de dados graváveis…`, run it again with the same `.env`.

### Editing the `office.yaml` and adding files to the Biblioteca

The `office.yaml` lives in the `config` volume, which the Office and the runner can only read (the squad can't change
it). The password, the credentials and the projects are changed with `configurar` (`--palavra-passe`, `--credenciais`,
`--gh`, `--projectos`). For everything else (branding, time zone, `runner.ferramentas`, see [Reference](#officeyaml)),
copy it to the PC, edit it in a plain text editor (Notepad, TextEdit in plain text) and hand it back validated:
```
docker compose cp office:/config/office.yaml office.yaml
docker compose run --rm -v ./office.yaml:/novo/office.yaml:ro configurar --office-yaml /novo/office.yaml
docker compose restart office
```
The first command copies it to the `compose.yaml` folder; edit it before the second. The second reads it straight from
your folder (never from a volume the squad writes to), already without root privileges, and only puts it in place if
it is valid (otherwise it shows the error and the old one stays). On Linux, the file must be readable by the
container's uid (`chmod 644 office.yaml`, or `OFFICE_UID` equal to yours). Don't change the `raizes`: in Docker mode
they are container paths (`/dados`, `/projectos`, `/home/office/.claude/projects`).

**Biblioteca (library):** copy files (or a folder, which is a batch) to the source's folder, inside the runner (with
the `office.example.yaml`, the «Material» source is the `material` folder):
```
docker compose cp relatorio.pdf runner:/dados/biblioteca/material/
```
They appear in `▤` without restarting anything.

### Diagnostics, stopping tasks and restarting

```
docker compose exec runner doctor
```
One line per check (`office.yaml`, `claude` and its version, the Claude credential by format and mode only, the `gh`
session, each project in the projects folder as a writable git repository, the data folders writable by the runner's
uid, the runner cycle reporting in within the last 3 min, the version): `✓` or `✗ … → <comando que corrige>`, always
a `docker compose …`. The last line reminds you that tasks only run with the PC on and Docker Desktop open. The runner
logs: `docker compose logs runner`.

- **stop a task:** `docker compose exec runner parar <id da tarefa>` (the ids:
  `docker compose exec runner ls /dados/tasks`). Same as `parar` on the VPS: it closes it as «erro» ("error") and it is
  never resumed.
- **change the password:** `docker compose run --rm configurar --palavra-passe` and `docker compose restart office
  caddy` (Caddy only reads the password at startup).
- **change the Claude credential or the GitHub token:** `docker compose run --rm configurar --credenciais` or
  `… --gh`. The next tasks already use them.
- **turning off the PC, closing Docker Desktop or `docker compose restart runner`** kills the running tasks. When the
  runner comes back, it resumes them (up to 2 times per task) about 5 minutes later. To stop everything:
  `docker compose stop`; to bring it back: `docker compose up -d`.

### Updating on the PC

Download the new version's `compose.yaml` (the command from step 3, with the new version in the URL) over the old one
and run:
```
docker compose pull
docker compose up -d
```
(or on a single line, which also works in Windows PowerShell: `docker compose pull; docker compose up -d`). The `.env`
and the volumes (config, credentials, data) stay as they are. See what changed in the `CHANGELOG.md`. **From 0.1.x to
0.2.0** run `docker compose run --rm configurar` once after `up -d`: the new `arquivo-fila` volume is created owned by
root and only `configurar` gives it the right owner (until then «Arquivar» answers «fila do arquivo indisponível» and
the runner logs that the queue is unsafe). **From 0.3.x to 0.4.0**, the same for the new `agentes`,
`agentes-rascunhos` and `agentes-fila` volumes (see [Squads and agents](#squads-and-agents)).

### Uninstalling from the PC

In the `compose.yaml` folder:
```
docker compose down
```
stops and removes the containers; the data stays in the volumes (you come back with `docker compose up -d`). **Only if
you want to lose them for good** (config, password, Claude credential, `gh` session, tasks, cards and Biblioteca):
```
docker compose down -v --rmi all
```
also removes the volumes and the images. There's no way back. What stays: the `projectos` folder (with the tasks'
worktrees in `.worktrees/`) and Docker Desktop. Also revoke the GitHub token and the Claude credential you created just
for the Office.

### Docker mode limitations

- **PC on and Docker Desktop open:** with the PC off or asleep, nothing runs (tasks halfway through are resumed later,
  up to 2 times). When Docker Desktop opens, the containers come back on their own, unless you stopped them with
  `docker compose stop`.
- **No domain, HTTPS, phone notifications or Telegram:** those are VPS only. The Office runs over HTTP on the PC itself
  (with `OFFICE_ENDERECO=0.0.0.0`, also on the local network, unencrypted).
- **Docker Desktop licence:** it is free for personal use, education, non-commercial open source projects and
  businesses with fewer than 250 employees **and** less than USD 10 million in annual revenue (both conditions);
  otherwise it needs a paid Docker subscription
  ([Docker Desktop license agreement](https://docs.docker.com/subscription/desktop-license/), checked on
  8 November 2026). Check this before using it at work.
- **No Claude Code sandbox:** in an unprivileged container, Claude Code's sandboxed commands fail (a `bwrap` error),
  except in a weaker mode (`enableWeakerNestedSandbox`) ([Sandboxing](https://code.claude.com/docs/en/sandboxing),
  checked on 8 November 2026); the runner image doesn't install it. The isolation is the container itself, as in
  Anthropic's reference container, whose page warns that, with the checks turned off, the container doesn't stop a
  malicious project from taking whatever is reachable inside it, including the Claude Code credentials, and
  recommends not mounting `~/.ssh` or cloud credentials
  ([Development containers](https://code.claude.com/docs/en/devcontainer), checked on 8 November 2026). See
  [Security](#security).
- **Windows:** projects in a folder on `C:` work, but are slower than on the WSL file system
  ([WSL best practices](https://docs.docker.com/desktop/features/wsl/best-practices/), checked on 8 November 2026).
- **Alongside VPS mode:** the PC's compose project is called `office-lite-pc` (containers and volumes
  `office-lite-pc-…`/`office-lite-pc_…`) and the VPS one `office-lite`; they can coexist on the same Linux machine
  (the VPS uses ports 80/443, the PC `127.0.0.1:8080`).
- **Tests:** the automated tests for Docker mode only run on Linux (amd64); Windows, macOS and arm64 are checked by
  hand.
- No backups or data migration between the PC and a VPS yet.

## Squads and agents

In ⚙ (settings), «Abrir «Squads e agentes»» ("Open Squads and agents") shows, per squad, the agents (name,
description, model, skills), the available skills and the model of the main session. Everything you do in this view is
a **request**: the Office only puts it in a queue and the runner writes the files, on the next pass (up to 1 min).
«Pedidos» ("Requests") lists each one (pending, done, or error with the reason); the view refreshes on its own. None
of this touches the projects or `office.yaml`.

Agents live in `<dados>/agentes/<squad>/agents/<nome>.md` (Claude Code subagents, with frontmatter), skills in
`<dados>/agentes/<squad>/skills/<skill>/SKILL.md` and the squad's instructions (optional) in
`<dados>/agentes/<squad>/CLAUDE.md`. When the runner prepares a task for the squad, and again on resume, it copies
them into the worktree, kept out of commits (see [Agents queue](#agents-queue-adr-017-to-019)). You put skills in
that folder yourself (in Docker mode, with `docker compose cp` to `runner:/dados/agentes/<squad>/skills/`, as with
the Biblioteca); the Office doesn't install skills from elsewhere.

**Changing the model or the skills:** «Mudar modelo ou skills» ("Change model or skills") on an agent. Models:
`inherit`, `sonnet`, `opus`, `haiku` or «sem campo» ("no field": uses the session's); other names only by editing the
file. Skills: only the squad's. Before you switch to a more expensive model (`haiku` < `sonnet` < `opus`), the view
warns you. The runner checks that the file is still the one you saw (otherwise «o agente mudou depois de ser visto»,
"the agent changed after it was seen", and nothing changes), changes only the `model:` line or the `skills:` block and
keeps the previous one as `<nome>.md.bak-AAAAMMDD-HHMM`. **It applies from the squad's next task** (or the resume of an
interrupted one), after that pass: a task already running keeps the copy it got. The lead's model isn't changed here:
the lead is the main session and uses `squads[].modelo` from `office.yaml` (otherwise `runner.modelo`, otherwise
`claude`'s default).

**Creating an agent from a description:** «Criar agente por descrição» ("Create agent from a description"), with a
name and what it should do.
1. **Draft:** the runner asks `claude` for an agent `.md` (no tools, 1 turn, up to 0.50 USD). It normalises it
   (`name` = the requested name; only `name`, `description`, `tools`, `model`, `skills` and `permissionMode`; skills
   the squad doesn't have are dropped; `bypassPermissions` is refused) and puts it under «Agentes por activar»
   ("Agents to activate"), never in `agents/`.
2. **Edit:** you open the whole file, change it and save. Each saved version has to be tested again.
3. **Read-only test run:** you pick a project and what to ask. The runner clones the project (`--depth 1`, no
   remotes) into a temporary folder, puts the candidate and the squad's agents and skills there and runs
   `claude --agent <nome>` (the candidate goes inline through `--agents <file>`, with `--setting-sources user`: nothing
   from the project's `.claude/` loads) with only `Read`, `Grep` and `Glob` (up to 8 turns, 2 USD and 10 min). The folder is always
   deleted. The report (with secrets masked), the cost and the untested tools show up on the candidate.
4. **Activate:** only with an ok test run of the current version. The `.md` moves to `agents/` (never over an agent
   with the same name). «Descartar» ("Discard") deletes the candidate.

**What the test run doesn't test:** tools beyond `Read`, `Grep` and `Glob` (`Bash`, `Edit`, `Write`, `WebFetch`…; the
view lists the ones the agent asks for), writing, git and PRs, MCP servers, the permissions and `runner.ferramentas` of
a real task, or more than 8 turns. An ok test run says the agent reads the project and answers what you asked, not that
it does a writing task well.

**Removing:** «Remover agente» ("Remove agent") moves the `.md` to `agents/.removidos/<nome>.md.bak-AAAAMMDD-HHMM` (to
restore it, move it back by hand). The lead can't be removed.

**Squad from a template:** «Nova squad a partir de um modelo» ("New squad from a template"), with one of the 4 generic
templates (Desenvolvimento, Investigação, Observação, Conteúdo: development, research, monitoring, content; in
`modelos/squads/`), an id and the mission. The runner asks `claude` (as for the agent draft, up to 0.50 USD) to adapt
the mission and the agents' descriptions and instructions; names, tools, models and permissions always come from the
template (all 4 only read: `Read`, `Grep`, `Glob`, `Agent`, `WebSearch`, `WebFetch`). The draft shows up on the squad:
«Criar squad» ("Create squad") or «Descartar rascunho» ("Discard draft"). Creating writes `<dados>/agentes/<id>/`
(`CLAUDE.md` and `agents/`), all or nothing, and registers the squad in `<dados>/agentes/squads.json`.
- Id of an `office.yaml` squad that has no agents yet: the template fills that squad.
- New id: the view shows a block to paste into the `squads:` list of `office.yaml` («Copiar bloco», "Copy block"), for
  example
  ```
    - id: pesquisa
      nome: "Pesquisa"
      lead: lider
  ```
  The runner never writes `office.yaml`: on the VPS edit `~/office-lite/office.yaml`; in Docker mode, see
  [Editing the `office.yaml`](#editing-the-officeyaml-and-adding-files-to-the-biblioteca). Without the block, the
  squad can't be launched and has no routines.
- «Ensaiar o lead» ("Test the lead") tests it as above. In squads created from a template, routines only run with an
  ok test run of the current lead.
- «Desactivar squad» ("Deactivate squad", only those created from a template): it leaves the register, its routines
  are cancelled and `agents/` becomes `agents.bak-AAAAMMDD-HHMM`; nothing is deleted. You remove the block from
  `office.yaml` yourself.

**Routines:** «Pedir uma rotina» ("Request a routine"; `office.yaml` squads with `formato: worktree` and the `task`
mode): project, time `HH:MM`, days of the week, instructions (1–2000 characters), cap per run (0.10–5.00 USD) and
maximum turns (1–30); up to 5 per squad. The runner creates the request `rotina-<squad>-<id>.md` in **Aprovações**
(approvals), with the exact definition. **The routine only runs after you approve line `R1`**; refused, it never runs;
with the `.md`'s definition tampered with, it doesn't either. Once approved, it runs once on each chosen day, on the
first pass from that time on (in `fuso`), as a `task` of the squad (the first of its `tipos`), with the agents copied
and its own cap and turns. It counts towards `tecto_diario` and waits for `paralelo`. If the runner is stopped until
the end of the day, that day is skipped.
- **Pause:** 3 runs in a row ending in «erro» (or that never got started) make it `pausada` (paused; a Telegram
  notice, if you have it). «Terminou sem PR» ("Finished without a PR") doesn't count as a failure.
- «Retomar» ("Resume", only paused ones) sets it back to `aprovada`, with the counter at zero. «Cancelar rotina»
  ("Cancel routine") is permanent: requesting it again means a new approval.

**Costs:** drafts and test runs use the same Claude credential as the tasks. Cap per call: 0.50 USD per draft and
2 USD per test run (a test run that goes over 2 USD ends in error). **Daily AI cap: 10 USD** (fixed, in `fuso`), added
up in `<dados>/runner/agentes-ia.jsonl`; a call with no known cost counts as its cap. Above it the request gets «tecto
diário de IA atingido» ("daily AI cap reached"); requests without AI carry on. Only 1 draft or test run per pass (the
others wait) and a test run can hold up the pass for up to 10 min. The amounts are `claude`'s own estimates; with the
subscription token they use its limits. Routines are normal tasks: their cap applies per start (with 2 resumes, up to
3×). Changing model and skills, activating and removing cost nothing.

**Docker mode:** there are three new volumes (`agentes`, `agentes-rascunhos`, `agentes-fila`). On an install older than
0.4.0 they are created owned by root on `docker compose up -d`: run `docker compose run --rm configurar` once, which
gives them your `OFFICE_UID`/`OFFICE_GID`. Until then the view says «A fila dos agentes está indisponível: corre o
`configurar` uma vez (modo Docker)» ("The agents queue is unavailable: run `configurar` once") and accepts no requests.
On the VPS, `get.sh` (or `./install.sh`) creates the new folders and generates the compose with them.

**Squad by conversation and business templates (0.5.0):** in «+ Squad», describe in your own words what the squad
should do. The AI runs up to 3 rounds of questions (1–3 each) and proposes a template and the mission, or declines
with a reason; no tools, up to 0.15 USD per turn and **1.00 USD per conversation** (it counts towards the 10 USD daily
cap); one conversation at a time, and a conversation idle for 24 h is deleted. The proposal shows up as a draft:
«Criar squad» or «Descartar». Two new templates are in `modelos/squads/`: `atendimento` (triage, writer, reviewer,
owner) and `comercial` (sales, researcher, qualifier; needs `entrada/perfil-cliente-ideal.md`). **Business squads are
read-only:** the runner ignores `runner.permission_mode` and `runner.ferramentas` for them and runs them with only
`Read`, `Grep`, `Glob` and `Agent` (`WebSearch` only in `comercial`), with no `Bash`, `Edit`, `Write`, `WebFetch` or
MCP. There are no new keys in `office.yaml`.

**Approved actions:** these squads can *propose* actions (an e-mail draft, a row in a CSV sheet, a Telegram notice
and, if you turn it on, sending a reply). Each one appears in Approvals and runs only if you approve it, one by one,
through an `accoes` service running as another system user with the credentials only it can read. How to turn it on
(Docker and VPS), the `accoes.json` and `accoes.env` formats, retention, personal data and the residual risk:
[ACCOES.en.md](ACCOES.en.md).

**API key for business squads:** they handle third-party messages and run with nobody at the keyboard, which is
automated commercial use: use an Anthropic API key (`ANTHROPIC_API_KEY` in `claude.env`), not a consumer-plan token.
`doctor` warns if that is the case.

**Docker mode:** the `accoes` service (runner image, another uid: `ACCOES_UID`/`ACCOES_GID` in `.env`, default 10002,
never equal to `OFFICE_UID`) and three new volumes (`accoes-segredos`, `accoes-estado`, `accoes-entrada`; in 0.5.1 a
fourth, `accoes-saude`, the only one the office mounts, read-only) come in the generated compose file. **VPS:**
`sudo ./instalar-accoes.sh --dados <dados>` installs it as the system user `office-accoes` with a systemd timer;
`install.sh` does not change and still never uses `sudo`.

---

## Reference

### `office.yaml`

`~/office-lite/office.yaml` (0600) is the only config. The installer creates it from `office.example.yaml` only if it
doesn't exist yet. The Office reads it at startup (if it has an error, it uses the example and shows a warning); the
runner and the installer refuse an invalid config. To validate: `python3 officecfg.py validar FICH`.

**Accepted YAML subset** (read by `officecfg.py`, no dependencies; anything else is an error with the line number):
indentation with spaces only, in multiples of 2 (tabs = error); `#` comments on their own line or after a value;
`key: value` with lowercase keys (`[a-z][a-z0-9_-]*`), no repeated or unknown keys; lists `- value` or
`- key: value` (the following keys aligned); single-line lists of simple values only (`[feat, fix]`); values `"…"`,
`'…'`, plain, integers and `true`/`false`. **Not supported:** anchors, tags, `|`/`>`, `{…}`, `null`/`~`, multi-line
values, multiple documents (`---`). `~/x` is text (expanded to the user's home directory in the roots).
Invisible characters (Unicode categories `Cf`, `Co`, `Cs`, except the emoji ZWJ) are refused in keys and values; error
messages never repeat the refused value.

| key | what (default) |
|---|---|
| `versao` | only `1` |
| `marca` | name in the page title and at the top, 1–40 («Squad Office») |
| `utilizador.nome` / `.contacto` | your name (the «À espera de …» ("Waiting for …") column, decisions) / optional e-mail for Web Push |
| `fuso` | IANA time zone, e.g. `Europe/Lisbon` (`UTC`); times, daily totals, daily cap, task ids |
| `raizes.dados` | where the Office data lives (`~/office-lite/dados`) |
| `raizes.projectos` | where the repositories are (`~/projectos`) |
| `raizes.claude_projects` | Claude Code sessions (`~/.claude/projects`) |
| `squads[]` | 1–6: `id`, `nome`, `lead` (the lead's role, `tech-lead`), `personagem`, `formato` `worktree` (launchable, default) or `sessao` (follow-only); `tipos` (`[feat, fix]`) and `modos` (`[goal, task]`) only with `worktree` |
| `squads[].modelo` | optional: model of the squad's main session (`--model`), same rules as `runner.modelo` (letters, digits, `.`, `_`, `-`, up to 64, no leading `-`); overrides `runner.modelo` for that squad's tasks, routines and drafts (see [Squads and agents](#squads-and-agents)) |
| `projectos[]` | 1–30: `id` (lowercase), `nome`, `glifo` (1–2 characters, «◆»), `repo` (folder in `raizes.projectos`, default = `id`) |
| `colunas` | labels for `ideias`, `a-fazer`, `em-curso`, `a-espera`, `feito` (fixed keys and order), 1–30; without `a-espera`: «À espera de <`utilizador.nome`>» (an empty value doesn't validate) |
| `biblioteca[]` | 0–12 sources: `id`, `rotulo`, `pasta` (relative to `<dados>/biblioteca`), `media` (`false`), optional `tipo` (`relatorios`, `estudos`, `planos`, `conhecimento`, `textos`); optional `arquivo_dias`, 7–3650: the runner archives on its own, at most once a day, the source's items unchanged for more than N days (ADR-012; without the key it never archives anything without a request; see [Biblioteca archive](#biblioteca-archive-adr-011)) |
| `runner` | `tecto_diario` 1–100 (10), `paralelo` 1–4 (1), `rotacao_dias` 1–365 (14), `permission_mode` `dontAsk` (default), `auto`, `acceptEdits`, `default` or `bypassPermissions`; `settings` (`""`, absolute path to a JSON file merged with the default denials); `ferramentas` 0–100 rules for `--allowedTools` (`Nome` or `Nome(regra)`, no spaces or commas outside the parentheses, no leading `-`; default: the list in [Runner](#runner)); optional `max_usd`, cost cap in USD per `claude` start (`--max-budget-usd`), a number > 0 and ≤ 1000 with up to 2 decimals (`5`, `2.5`); optional `modelo`, `--model` (an alias such as `sonnet` or a full name; letters, digits, `.`, `_`, `-`, up to 64, no leading `-`). Without either: no flag (`claude` uses its own defaults) |

Launchable = each `worktree` squad × each project.

### Folders

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
    arquivo-fila/               pedidos de arquivar/repor vindos da Biblioteca (JSON)
    agentes/<squad>/            agentes (agents/<nome>.md), skills (skills/<skill>/SKILL.md) e CLAUDE.md da squad;
                                squads.json e rotinas.json (registos); só o runner lá escreve
    agentes-rascunhos/          candidatos, rascunhos de squads e ensaios; só o runner lá escreve
    agentes-fila/               pedidos da vista «Squads e agentes» (JSON)
    agentes-rascunhos/squads/<id>/conversa.json  conversa de montagem (só o runner escreve; apagada às 24 h)
    accoes-entrada/<squad>/     mensagens recebidas (mensagens/m<16 hex>.txt) e vista.json (sem segredos); só o
                                serviço accoes escreve (ver ACCOES.md)
    accoes-saude/saude.json     saúde da última passagem do serviço accoes (avisos, hora); só o serviço accoes
                                escreve; o office monta accoes-saude só de leitura e lê só este ficheiro
    decisoes/.accoes/           registo de cada pedido de acções (definições canónicas e sha); só o runner escreve
    push/                       chaves e subscrições Web Push (0700)
    biblioteca/<pasta>/         fontes da Biblioteca
    biblioteca/<pasta>/.arquivo/  o que se arquivou (mesmos caminhos) e o REGISTO.tsv; só o runner lá escreve
    runner/                     lock, histórico, logs, settings/, office.yaml.sha256, gh-verificado.json e
                                agentes-ia.jsonl (custos da IA dos agentes) do runner (só no host)
    runner-estado/estado.json   sinal do runner para o Office (sem segredos; ver Runner)
~/projectos/exemplo/            repositório de exemplo criado pelo instalador
```
In the container, `tasks`, `claude_projects`, `squads`, `decisoes`, `biblioteca` and `runner-estado` are mounted
read-only, as are `agentes` and `agentes-rascunhos`; the Office only
writes to `decisoes-fila`, `quadro`, `quadro-lancar`, `push`, `arquivo-fila`, `agentes-fila` and the thumbnails (in a `tmpfs`). The container runs with
the uid/gid of whoever installed it (`OFFICE_UID`/`OFFICE_GID` in `.env`), `read_only`, with no published ports.

**In Docker mode** there is no `~/office-lite` or `~/squad-office-lite`: each `dados/` folder is a Docker volume with
the same name (`tasks`, `quadro`, `runner-estado`, …), `office.yaml` lives in the `config` volume (`/config`),
`claude.env` in `segredos` (`/segredos`), the password hash and the proxy secret in `proxy` (`/proxy`) and the runner's
home (the `gh` session, `~/.claude`) in `runner-home` (`/home/office`). In the runner, the data is under `/dados/…` and
the projects under `/projectos` (the folder on your PC). `compose.yaml` shows what each container mounts and whether
it's read-only.

### Runner

`runner/office_runner.py` runs on the host (Python ≥ 3.10, stdlib only), one pass per minute via the systemd user
timer `office-runner.timer` (`systemctl --user …`, logs in `journalctl --user -u office-runner`). It needs
*linger* to run without an open session. In Docker mode it runs in the `runner` container via `ciclo` (below), with
the same code; the `ciclo`, `doctor`, `parar` and `configurar` commands are in the image
(`docker compose exec runner doctor`).

```
office_runner.py --uma-vez [--config FICH]     uma passagem (omissão: $OFFICE_CONFIG ou ~/office-lite/office.yaml)
office_runner.py correr TASK_ID [--retomar]    interno: corre o claude numa tarefa já preparada
office_runner.py parar TASK_ID                 pára uma tarefa «a correr» (fecha em erro; nunca é retomada)
office_runner.py doctor                        diagnóstico: uma linha ✓/✗ por verificação, com o comando que corrige
office_runner.py ciclo [--intervalo S] [--passagens N]
                                               modo Docker: uma passagem a cada S segundos (60), sem fim ou N vezes
```
`correr`, `parar`, `doctor` and `ciclo` also accept `--config FICH`.
**`ciclo`** (the main process of the `runner` container, with `init: true`): does the same as the timer, without
systemd. On each round it re-reads and validates `office.yaml` (if invalid: it logs the error and tries again on the
next round), runs a pass and reaps the `correr` processes that have finished (no *zombie* processes). An exception in
a pass goes to the log (`docker compose logs runner`) and the loop carries on. It reads `runner.env` from the secrets
folder (`OFFICE_SEGREDOS_DIR`) at startup, like the unit's `EnvironmentFile`. `SIGTERM`/`SIGINT`
(`docker compose stop`) end it once the current pass finishes; running tasks die with the container and are resumed
(up to 2 times) about 5 min after the next start. In Docker mode `doctor` doesn't check *linger*, the timer, Docker,
DNS or ports: it checks `office.yaml`, `claude`, the credential, `gh`, each project in `/projectos` (git and writable)
and whether `estado.json` is less than 3 min old.
Each pass (with a lock, never two at the same time): writes `estado.json` → checks whether `office.yaml` changed →
applies decisions → resumes interrupted tasks (up to 2 times) → processes the Quadro (board) queue → processes the
Biblioteca archive queue and the automatic archive (see [Biblioteca archive](#biblioteca-archive-adr-011)) → archives
old requests (of every queue) → approved routines → the agents queue, with the AI action last (see
[Agents queue](#agents-queue-adr-017-to-019)). In the log, Biblioteca lines start with «biblioteca:» and agents queue
lines with «agentes:».

**`estado.json`** (`<dados>/runner-estado/`, 0644, atomic write): the runner's heartbeat for the Office, which reads
it read-only and uses it to draw «Primeiros passos» ("First steps") and the «O runner não dá sinal desde HH:MM» ("The
runner hasn't checked in since HH:MM") banner (more than 3 min without a pass). Only values from a known list, never
secrets, paths or command output:
```json
{"v": 1, "em": "2026-11-08T19:30:00+00:00", "versao": "0.4.0",
 "claude": "ok|falta", "credencial": "ok|falta|modo", "gh": "ok|sem sessão"}
```
`credencial` says whether there is a valid credential (`claude.env`, `runner.env` or the `claude` login), without
reading it out of the runner; `modo` = a file with open permissions. `gh auth status` runs at most every 10 min (the
last value is kept in `<dados>/runner/gh-verificado.json`). An `estado.json` that is invalid, larger than 4 KB or a
symlink is ignored (the Office says «O runner ainda não deu sinal» ("The runner hasn't checked in yet")). `/healthz`
doesn't change.

**Launching:** re-validates the request against the config; checks the daily cap (`tecto_diario`, in the config's
time zone) and `paralelo` (above it, the request waits); creates a `git worktree` in `<repo>/.worktrees/<task_id>` on a
`<kind>/<slug>` branch off `origin`'s default branch (or the current one, if there's no remote); copies the squad's
agents into it (see [Agents queue](#agents-queue-adr-017-to-019)); writes `task.md` and `meta.json`; replies `{"task_id"}` and runs `claude -p` detached (log in `<dados>/runner/logs/<task_id>.log`, claude's
output in `tasks/<id>/claude.log`; the `runner.env` secrets reach it via stdin, see
[Claude credentials](#claude-credentials)). The title and the brief only travel by file and stdin, delimited as user
data. Time limit
of 3 h (`task`) or 8 h (`goal`). Final state: `concluída`, `parcial (PR draft)`, `incompleta` or `erro` (in the
interface, `parcial (PR draft)` shows as «PR aberto em rascunho» ("Draft PR opened") and `incompleta` as «Terminou sem
PR» ("Finished without a PR"); in the data, the API and the notifications the values don't change). In the «Lançar»
("Launch") dialog, `feat`/`fix` show as «funcionalidade nova» ("new feature")/«correcção» ("fix") and `task`/`goal` as
«tarefa curta (até 3 h)» ("short task (up to 3 h)")/«objectivo com plano (até 8 h)» ("goal with a plan (up to 8 h)");
the request carries the same values as always (contract v1).

**Brakes:**
- `runner.permission_mode`: `dontAsk` (default: `claude -p` refuses, without asking, anything that isn't in
  `runner.ferramentas`), `auto` (`claude` itself decides what is safe; it may not exist in your version or account),
  `acceptEdits` (accepts file edits; commands outside `ferramentas` are refused), `default` (asks for confirmation:
  with no human around, it refuses), `bypassPermissions` (dangerous: everything without confirmation, only the
  forbidden commands and the denials stop it).
- `runner.ferramentas` (`--allowedTools`), by default: `Read`, `Edit`, `Write`, `Glob`, `Grep`, `Agent`, `TodoWrite`,
  `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate` (the task list in recent `claude` versions),
  `Bash(git *)`, `Bash(gh pr create|view|checks|edit|list|ready *)`, `Bash(gh run *)` and `Bash(<cmd> *)` for
  `python`, `python3`, `pytest`, `uv`, `pip`, `npm`, `npx`, `node`, `pnpm`, `yarn`, `make`, `ruff`, `ls`, `wc`,
  `grep`, `diff`, `mkdir`, `touch`, `pwd`, `which`, `sort`. That's enough for the squad to get all the way to the PR
  in a Python or Node project; for anything else (e.g. `cargo`, `go`), add `Bash(cargo *)`. Setting the key replaces
  the whole list; `[]` passes no `--allowedTools`. Since 0.1.2 the default has no `WebSearch`, `WebFetch`,
  `Bash(cat *)`, `Bash(head *)`, `Bash(tail *)`, `Bash(cp *)`, `Bash(mv *)` or `Bash(echo *)` (Read, Grep, Write and
  Edit do the same); if you want them, write the full list including them.
- The list is not a wall: allowing `python3`, `npm`, `make` or a project script means allowing a program that can do
  anything your user can do (see [Security](#security)).
- **Default denials** (always, in every mode): the runner passes `claude` a JSON `--settings` (in a 0600 file in
  `<dados>/runner/settings/`, not in argv, deleted when the task closes; any leftovers go with the rotation,
  `rotacao_dias` later) with `permissions.deny`, which wins over `--allowedTools`:
  - reading (`Read`) `claude.env`, `runner.env` and `.env` in the home folder (`~/office-lite`), any `.env` and
    `.env.*` (this also hides the projects' `.env.example`), `~/.config/gh/**`, `~/.ssh/**`,
    `~/.claude/.credentials.json`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`,
    `~/.config/systemd/**` and `/proc/**` (the environment of other processes);
  - writing (`Edit`, which covers Write) those three home files, `office.yaml`, `quadro-lancar/`,
    `decisoes-fila/`, `decisoes/`, `quadro/`, `runner/`, `runner-estado/`, `arquivo-fila/`, `agentes/`, `agentes-fila/`
    and `agentes-rascunhos/` inside `raizes.dados`, the Lite's code
    (`~/squad-office-lite/**`), the `runner.settings` file, the five `~` entries above (credentials and
    `~/.config/systemd/**`) and what your user executes: `~/.claude/settings.json`,
    `~/.claude/settings.local.json`, `~/.bashrc`, `~/.profile`, `~/.bash_profile` and `~/.local/bin/**`;
  - `blockWritesOutsideWorkingDirectories: true`: Write and Edit only write in the task's worktree and its folder
    (`tasks/<id>/`). Only in recent `claude` versions; the rules above apply in all of them.

  The paths come from your config, they aren't hard-coded. They apply to Claude Code's tools and to the file commands
  it recognises in Bash (`cat`, `head`, `tail`, `sed`, redirections); they do **not** apply to a `grep -r` or to
  whatever runs inside `python3`, `npm`, `node`, `make` or a script.
- Always forbidden (`--disallowedTools`, they win over `--allowedTools`): `gh pr merge`, `gh pr close`,
  `gh repo delete`, `gh release`, `git push --force`/`-f`, `git push origin main`/`master`. These are text rules on
  the start of the command: they do **not** catch `git -C . push origin main`, `git push origin HEAD:main`, a
  `+refspec` (`git push origin +x:main`) or `git -c … push`. The real protection is on GitHub: in each project's
  repository, *Settings* → *Branches* (or *Rules*), protect the default branch (require a pull request, no direct push
  or force-push). A *fine-grained* token without the *Administration* permission can't turn that rule off.
- «Marca-o pronto (`gh pr ready`)» ("Mark it ready") only appears in the prompt if `gh pr ready` is allowed (a rule
  covering it in `ferramentas`, or `auto`/`bypassPermissions`); otherwise the squad leaves the PR as a draft.
- `runner.settings`: absolute path to a Claude Code `settings.json` (hooks, your own `permissions`, sandbox).
  It is **merged** with the default denials, it doesn't replace them: the `deny`, `allow` and `ask` lists are
  concatenated (the base ones first), the other keys stay as you wrote them, and
  `blockWritesOutsideWorkingDirectories` is always `true`. Unreadable, not a regular file, over 64 KB, invalid JSON or
  `deny`/`allow`/`ask` that aren't lists of strings ⇒ the task ends in «erro» ("error") with «runner.settings
  inválido: <regra>» ("invalid runner.settings: <rule>") and `claude` doesn't start. Recommended with `auto`.
- **Claude Code sandbox (optional, recommended):** isolates Bash and the programs it launches (including
  `python3`, `npm` and scripts): writes only in the worktree, the task folder and temporary folders; reads the whole
  disk, except what you put in `sandbox.filesystem.denyRead`. On Linux it needs
  `sudo apt-get install -y bubblewrap socat`. In a `runner.settings`:
  ```json
  {"sandbox": {"enabled": true, "failIfUnavailable": true}}
  ```
  Without `failIfUnavailable: true`, if `bubblewrap` or `socat` is missing, `claude` runs **without** a sandbox. The
  Lite doesn't test the sandbox: check with a short task that `git push` and `gh pr create` still go through.
- `runner.max_usd` (`--max-budget-usd`): cost cap per `claude` start, as estimated by `claude` itself (it may go
  slightly over). Each resume is a new start: with 2 resumes, a task can spend up to 3× the cap.
  `runner.modelo` (`--model`): the squad's model (e.g. `sonnet`); the name isn't validated at startup, a wrong name
  shows up in `claude.log`. `squads[].modelo` overrides it for that squad. In routines, `--max-budget-usd` and
  `--max-turns` are the routine's own.

### Claude credentials

The runner starts `claude` with a credential on a single line of `~/office-lite/claude.env` (next to `office.yaml`).
Two options:

- **API key (recommended):** create it at <https://console.anthropic.com> (Settings → API Keys). Line
  `ANTHROPIC_API_KEY=sk-ant-…`. Pay-as-you-go, separate from the subscription. The installer asks for it (option 1)
  and writes `claude.env` for you.
- **Subscription token** (Claude Pro/Max): **the installer neither asks for it nor stores it**; you write the file
  yourself. Generate the token on the VPS itself, from your computer (`ssh -t <utilizador>@<vps> claude setup-token`:
  it opens a link that you confirm in the browser) and copy the line that starts with `sk-ant-oat01-` (not the code
  the browser shows). If `ssh` says `claude: command not found`, use the full path:
  `ssh -t <utilizador>@<vps> '~/.local/bin/claude setup-token'`. Then, on the VPS:
  ```bash
  install -m 600 /dev/null ~/office-lite/claude.env
  nano ~/office-lite/claude.env
  ```
  and write a single line, `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-…`, with your token. Never put the token in command
  arguments (e.g. `echo sk-ant-… > …`): it ends up in the shell history and in the process list. It uses your
  subscription's limits.

  Instead of `claude.env` you can put the same line in `~/office-lite/runner.env` (also 600, next to the Telegram
  settings). If both exist, `claude.env` wins. With no `claude.env` and with `ANTHROPIC_API_KEY=` or
  `CLAUDE_CODE_OAUTH_TOKEN=` in `runner.env`, the installer asks nothing (it says it's using `runner.env`; it only
  checks that the key is there, doesn't read the value, and refuses a `runner.env` that isn't a regular file owned by
  you with `chmod 600`); `--credenciais` shows the question anyway.

The installer asks which one:
```
  1) chave de API (recomendada; paga por uso)
  2) token da subscrição (escreves tu no ficheiro; o instalador mostra como)
  3) login já feito no claude (expira; só para experimentar)
```
1 is the default. 2 only shows these instructions, with the command `ssh -t <utilizador>@<domínio ou IP da VPS> claude
setup-token` already filled in, and reads nothing. 3 writes no credential: it uses the login you already did with
`claude` on the VPS.
The API key is read without being echoed on screen and must start with `sk-ant-`
(only letters, digits, `_` and `-`); a subscription token pasted there is refused. The runner reads `claude.env`
(written by the installer or by you) with the same rules plus the token rule (`sk-ant-oat01-` followed by letters,
digits, `_` or `-`): if it doesn't match the format, the task ends in «erro» with «credenciais do Claude inválidas»
("invalid Claude credentials") and the rule, never the value. The file must have mode `0600` and be owned by you;
with looser permissions or another owner the installer and the runner refuse it (the task ends in «erro» asking for
`chmod 600`), they never fix it on their own. With no `claude.env` and no credential in `runner.env`, `claude` uses
whatever login it already has. To change the API key: `./install.sh --credenciais` (with option 2 or 3, an existing
`claude.env` is left as it is).
Non-interactive: `OFFICE_CLAUDE_CRED=api|oauth|nenhuma` and, with `api`, `OFFICE_CLAUDE_CRED_FILE=<ficheiro com a
chave>` (without `OFFICE_CLAUDE_CRED` it's `nenhuma`, with a warning; with `oauth` it only shows the instructions and
ignores `OFFICE_CLAUDE_CRED_FILE`). A non-empty `OFFICE_CLAUDE_CRED` counts as `--credenciais` (it chooses even when
there's a credential in `runner.env` or an existing `claude.env`).

**The `claude` environment (allowlist):** `claude` only gets `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `USER`,
`LOGNAME`, `SHELL`, `TERM`, `TMPDIR`, `TZ` (those that exist in the runner), `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`
and one credential: the one in `claude.env`; without it, `ANTHROPIC_API_KEY` or, if there isn't one,
`CLAUDE_CODE_OAUTH_TOKEN` from `runner.env`. Never both. The runner doesn't pass `OFFICE_*` (the Telegram token) or
`GH_TOKEN`/`GITHUB_TOKEN` to `claude` (nor proxies or `NODE_OPTIONS`) and doesn't let it read its own environment in
`/proc`. For `gh`, use `gh auth login` (see [GitHub token](#github-token)). In Docker mode `claude` also gets
`DISABLE_AUTOUPDATER=1` (the version is pinned in the image) and `claude.env`/`runner.env` are read from
`OFFICE_SEGREDOS_DIR` (`/segredos`, the `segredos` volume), with the same rules; the default denials follow that
path.

**`runner.env` secrets:** the Telegram settings, the Claude credential and `GH_TOKEN`/`GITHUB_TOKEN` are removed from
the runner's environment right at startup. The `git`, `gh` and `claude` children only get the allowlist above (`git`
also gets `GIT_TERMINAL_PROMPT=0`); `claude` also gets the credential, and the runner's own `gh` calls (checking the
PR) get `GH_TOKEN`/`GITHUB_TOKEN`, if set. The detached `correr` receives these secrets through a pipe on stdin, not
through the environment. An `office_runner.py correr` started by hand only uses its own environment.

The credential value only goes into the environment of the `claude` process (never in arguments, `meta.json`,
`claude.log`, logs or Telegram). The Office container doesn't mount `claude.env` (the compose generator refuses roots
that would include it).

**Telegram (optional):** in `~/office-lite/runner.env`, `OFFICE_TELEGRAM_TOKEN=…` and `OFFICE_TELEGRAM_CHAT=…` → a
message at the end of each task, on failures and when the daily cap is hit. Without them it does nothing.

### Approvals (decisions, ADR-001)

Decision requests are `*.md` files in `<dados>/decisoes/` (written by you or by an agent outside the runner: squads
launched by the Office are denied writes to this folder by default; a regular file, named
`[\w.-]+\.md`, never `README.md`, up to 1 MiB), with frontmatter at the start:
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
- Pending = `estado` starts with `pendente` or `parcial` (lowercase). Only sections titled exactly `## Decisões`
  count; a line `- [id] texto`, id `[A-Za-z0-9][A-Za-z0-9._-]{0,19}`. No valid lines → a single `*` decision.
- ` → aprovado (AAAA-MM-DD)` / ` → recusado (AAAA-MM-DD)` at the end of the line = decision already taken.
- The body is only sent on request (`GET /api/aprovacao?fonte=geral&ficheiro=`), masked.

**Submitting** `POST /api/decisao` (`Content-Type: application/json`, same `Origin`, `token` from `/api/state`):
`{"token","fonte","ficheiro","id","accao":"aprovar|recusar|comentar","nota","sha256"}` → `202`. Errors: 400 (invalid
fields, missing note), 403 (Content-Type, origin, token), 409 (request not pending, decision already taken or
received, `sha256` different from the current one), 413 (body > 8 KB), 503 (queue not writable).

**Queue (contract v1):** one `AAAAMMDDTHHMMSS-<8 hex>.json` file per decision in `<dados>/decisoes-fila/`, atomic
write, exact keys:
```json
{"v": 1, "criado": "2026-10-31T12:00:00+00:00", "fonte": "geral", "ficheiro": "2026-10-23-x.md",
 "id": "P1", "accao": "aprovar", "nota": "", "sha256": "<sha256 do .md que o utilizador viu>",
 "ip": "<último X-Forwarded-For ou IP da ligação>", "ua": "<User-Agent, ≤ 120>"}
```
**What the Office never does:** it doesn't edit or delete the `.md` files (mounted read-only), doesn't apply anything,
doesn't run commands and doesn't delete or move queue files. The runner is what applies: it checks the `sha256` (it
only applies on the version you saw, with one exception, below), marks the line (`→ aprovado (…)`), updates `estado`,
appends `## Decisão de <nome> (…, via Office)`
and moves the JSON to `feito/`; if it can't apply it, it moves it with a `<nome>.motivo.txt` and the `.md` stays
untouched.

**The `sha256` exception:** the runner also applies a decision whose `sha256` isn't that of the current `.md` if (1)
the current `.md` is exactly the last version the runner itself wrote and (2) the `sha256` you saw is one of the
versions in that sequence of runner writes (up to 50 per request, in `<dados>/runner/.historico.json`). This lets you
decide several lines of the same request from the same page: the runner marks P1, the file changes, and P2 (sent with
the old `sha256`) is still applied. If another person or program changes the `.md` in the meantime, the sequence
starts over and the decision is refused («sha256 diferente: o pedido mudou depois de ser visto» ("sha256 differs: the
request changed after it was viewed")). Always read the whole request before approving.

### Quadro (cards, ADR-003)

A kanban board with five columns (labels come from the config). **Automatic cards** are computed on every request and
never stored: squad tasks (running → «Em curso» ("In progress"); error → «Em curso» with a red badge; PR open →
«À espera» ("Waiting"); PR merged or finished without a PR → «Feito» ("Done"); the detail view shows the task result,
masked, errors included), pending approvals («À espera») and briefs from `tasks/inbox/*.md` that are ≤ 7 days old and
not yet launched («A fazer», "To do"). **Manual cards** live in `<dados>/quadro/cartoes.json`, contract v1, exact keys:
```json
{"v": 1, "cartoes": [{"id": "m-3f9a1c2b7d", "titulo": "…", "descricao": "md curto", "projecto": "exemplo",
  "coluna": "ideias", "ordem": 10, "etiquetas": ["preço"], "criado": "2026-11-04T10:12:00+00:00",
  "actualizado": "2026-11-04T10:12:00+00:00"}]}
```
- `id` `[A-Za-z0-9_-]{1,40}`; `titulo` 1–120; `descricao` ≤ 2000; `projecto` a project id from the config or `outro`;
  `coluna` `ideias|a-fazer|em-curso|a-espera|feito`; `etiquetas` ≤ 5; ISO dates with a time zone; up to 200 cards.
- Version = sha256 of the file's bytes. `POST /api/quadro` (`op`: `criar`, `editar`, `apagar`, `mover`) only writes if
  the version the browser saw is the current one (otherwise 409); the write is tmp + `fsync` + `os.replace`. An
  unreadable file or `v` ≠ 1 → automatic cards only, and writes return 503 until the file is fixed by hand.
- **Anyone writing from outside** re-reads the file right before, writes to a temporary file in the same folder and
  does a `rename`.
- **Data, never instructions:** titles, descriptions and tags are user text; an agent that reads them does not treat
  them as requests to carry out.

### Launching from the Quadro (ADR-005)

Dragging a manual card to «Em curso» opens a confirmation; «Lançar» ("Launch") calls `POST /api/quadro/lancar`, which
writes a request to the `<dados>/quadro-lancar/` queue and moves the card, in a single operation. The Office never
launches anything: the runner does.

**Request** `<id>.json` (written by the Office; the name comes only from the clock and `secrets.token_hex(4)`), exact
keys, in this order:
```json
{"v": 1, "id": "20261106T023000-a1b2c3d4", "cartao_id": "m-0a1b2c3d4e", "squad": "dev", "projecto": "exemplo",
 "kind": "feat", "mode": "goal", "slug": "primeira-tarefa", "titulo": "…", "brief": "…", "pedido_em": "2026-11-06T02:30:00+00:00"}
```
`squad` is a `worktree` squad, `projecto` a project id, `kind` one of that squad's `tipos` and `mode` one of its
`modos`; `slug` `^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$`; `titulo` 1–120 and `brief` ≤ 8192 bytes, with no control or
invisible characters (`Cf`, `Co`, `Cs`, except the emoji ZWJ). Written with tmp + `fsync` + `os.link` (never
overwrites).

**Runner reply** `<id>.feito.json`: `{"task_id": "…"}` or `{"erro": "<texto>"}` (tmp + `rename`). **Office marker**
`<id>.movido.json` `{"v": 1, "coluna": "feito"|"a-espera"|null, "em": ISO}`: the end-of-run move has already been done.

**Runner contract** (the Office only vouches for what it writes; whoever reads the queue does not trust it):
- only names matching `^\d{8}T\d{6}-[0-9a-f]{8}\.json$` without `.feito.json` are requests; open with `O_NOFOLLOW`,
  check with `fstat` that it is a regular file, read ≤ 32 KB;
- revalidate everything against the config (keys, `id` = name, closed lists, slug, `pedido_em` with a time zone);
  on failure → `{"erro"}`;
- `titulo` and `brief` only via a file or stdin, never in argv or through a shell, and delimited in the prompt as user
  data, never as instructions;
- daily cap: above it, reply `{"erro": "tecto diário"}`;
- rotation: trios with a `.movido.json` older than `rotacao_dias` move to `feito/` in the order `<id>.json` →
  `<id>.feito.json` → `<id>.movido.json` (the Office only reads the root of the queue).

**`POST /api/quadro/lancar`**: exact keys `token, versao, cartao_id, posicao, squad, projecto, kind, mode, slug,
titulo, brief`. 413 (> 32 KB), 403 (Content-Type, origin, token), 400 (`squad inválida`, `projecto inválido`,
`tipo inválido`, `modo inválido`, `slug inválido`…), 503 (queue not writable, invalid `cartoes.json`), 409 (version
`conflito` or `já lançado`), 404 (manual card does not exist), 429 `{"error": "fila cheia"}` (10 pending requests).
Success: **202** `{id, versao, movido, cartoes}`. `GET /api/quadro` adds `lancar`, `dev_projectos` (launchable ids)
and, on each manual card, `lancamento` (`a-lancar`, `ligado` or `erro`); when the run ends it moves the card, once
only, to «À espera de …» ("Waiting for …"; error or PR to review) or «Feito».

### Biblioteca archive (ADR-011)

Archiving takes a document (with its versions: `x.md`, `x.copy.md`, `x.revisao.md`…) or a media batch out of the list
without deleting it: the whole item moves to `<fonte>/.arquivo/<the same path>`, all or nothing; nothing is deleted or
overwritten (an existing destination ⇒ error and nothing moves). Only items from the `biblioteca:` sources (not tasks,
inbox plans, approval requests or media folders outside `media/`). In the detail view: «Arquivar» ("Archive", with a
confirmation) or, for archived items, «Repor» ("Restore"); the «Ver arquivados» ("Show archived") chip shows only the
archive (`GET /api/biblioteca?arquivo=1`, a 2nd index built only on request, with the same rules, ETag and 304). The
Office never moves anything: it writes a request to a queue and the runner handles it on the next pass (up to 1 min).

**Old links:** an archived item keeps its id and paths. `/f/`, `/mini/`, `/zip` and `/item?id=` (and so
`#biblioteca/<id>`, the links from WhatsApp, approval requests and Telegram) look in the main index first and then in
the `.arquivo/` of the same source (`/f/` and `/mini/` straight on disk, without waiting for the index); the detail
view says «Arquivado em `<data>` · `<motivo>`» ("Archived on … · reason"). Once restored, it opens again from the same
link (on the next scan, ≤ 60 s).

**Protections** (for requests and the automatic archive; the runner answers with the reason): an item cited by a
pending approval request (or one with an unknown status) is never archived, nor one with any file changed less than
48 h ago. Automatic archive only (`arquivo_dias`): also not what is cited by a request changed less than 14 days ago,
nor what was ever restored (by the item or by any of its files, according to the REGISTO). Restoring has no
protections. Try `arquivo_dias` on a test source first: on an old source
the first pass may move a lot (all reversible with «Repor»).

**Request** `<dados>/arquivo-fila/<id>.json` (contract `v: 1`, written by the Office; name only from the clock and
`secrets.token_hex(4)`), exact keys in this order:
```json
{"v": 1, "id": "20261117T183400-0a1b2c3d", "accao": "arquivar", "fonte": "material", "chave": "relatorios/x.md",
 "pedido_em": "2026-11-17T18:34:00+00:00", "ip": "…", "ua": "…"}
```
`accao` `arquivar` or `repor`; `fonte` the id of a source in the config; `chave` the item id without the source
(document: the main file's path; batch: `media/<lote>`), with the Biblioteca path rules (no `..`, hidden or denied
components, ≤ 8 components); `ip` ≤ 64 and `ua` ≤ 200 characters. Written tmp + `fsync` + `os.link` (never
overwrites). **Runner answer** `<id>.feito.json`: `{"ok": true}` or `{"erro": "<reason>"}`, with a reason from a
closed list (`pedido ilegível`, `fonte desconhecida`, `item inexistente`, `não arquivável`, `destino já existe`,
`sistemas de ficheiros diferentes`, `symlink no caminho`, `nome com tab ou quebra de linha`, `não está no arquivo`,
`erro a mover`, `protegido: citado por um pedido de aprovação pendente`, `protegido: alterado há menos de 48 h`,
`alterações por commitar`, `erro git`). Each pass handles at most 50 requests, in name order (the rest wait for the
next one); a 2nd request for the same item (or for files already moved) in the same pass gets `pedido ilegível`.
**Rotation:** the request and the answer go to `feito/` once the answer is older than `runner.rotacao_dias` (the
Office only reads the root).

**Runner contract** (the queue comes from a container; the runner does not trust it): only names
`^\d{8}T\d{6}-[0-9a-f]{8}\.json$`, opened with `O_NOFOLLOW` and `fstat` (regular file, ≤ 4 KB); everything is
revalidated against the config; paths are walked folder by folder without following symlinks (the source root may be
a symlink, nothing below it) and moved with `os.rename` (same filesystem: across disks it gives `sistemas de ficheiros
diferentes` and nothing moves, no copies). `repor` does the reverse, only if the item is in `.arquivo/`.

**`<fonte>/.arquivo/REGISTO.tsv`** (only the runner writes it): tab-separated, header `data accao caminho motivo
item` and one line per moved file: date in the config's time zone, `arquivar`/`repor`, path relative to the source,
reason (`pedido no Office`, `reposto a pedido do Office`, `idade (N dias)`) and the item (= `chave`). Names with a tab
or a line break are refused before anything moves.

**Git:** if the source is in a git repository whose top is `<dados>/biblioteca` or inside it, the moved tracked files
go into a single commit per source and per pass, `chore(arquivo): N ficheiros` (restore: `chore(arquivo): repor N
ficheiros`; both in the same pass: `chore(arquivo): arquivar e repor N ficheiros`) with only those paths (passed on stdin, never on the command line; hooks, `fsmonitor` and signing off), and `.arquivo/` gets a
`.gitignore` with `*`. Then `git fetch origin` and `git push` only if the current branch is `origin/HEAD`'s and is
exactly 1 commit ahead and 0 behind (never `--force`); otherwise an `AVISO: sem push em <fonte>: <motivo>` line in the
runner log, and the push is up to you. An item with tracked files that are modified, staged or deleted (`git
status`) is not moved (`alterações por commitar`): the commit never publishes uncommitted work; nor is an item with a
submodule (a `160000` entry in `git ls-files -s`) (`não arquivável`). If `git ls-files`, `git add` or the commit
fails (e.g. `index.lock`), that source's renames in that pass are undone, the index goes back to how it was
and the requests get `erro git`. A repository that happens to contain the data (top outside
`<dados>/biblioteca`) or no git: just `os.rename`.

**`POST /api/biblioteca/arquivo`** `{"token", "accao", "item"}` (exact keys; `item` = the item id in the Biblioteca):
413 (> 4 KB), 403 (Content-Type, origin, token, proxy secret), 400 (`campos inválidos`, `acção inválida`, `item
inexistente`: archiving an item not in the list or restoring one not in the archive; `não arquivável`), 409 (`já há
um pedido em fila para este item`, for either action), 429 `fila cheia` (50 pending), 503 (`fila do arquivo
indisponível`: folder missing or not writable). Success: **202** `{"id", "accao"}`. `GET /api/biblioteca/item` adds
`arquivo`: `{"arquivavel", "arquivado": null | {"em", "motivo"}, "pendente": null | "arquivar" | "repor", "erro": null
| <reason of the last failed request>}`; in the archive list each item carries `arquivado`.

### Agents queue (ADR-017 to 019)

The «Squads e agentes» view (see [Squads and agents](#squads-and-agents)) only reads and writes requests to a queue;
the runner re-validates everything and writes. Names: squad `[a-z][a-z0-9-]{1,19}`, agent and skill
`[a-z][a-z0-9-]{1,39}`, routine `r1`…`r999` (never reused). Only the runner writes to `agentes/` and
`agentes-rascunhos/`:
```
<dados>/agentes/<squad>/agents/<nome>.md          (+ <nome>.md.bak-AAAAMMDD-HHMM e .removidos/)
<dados>/agentes/<squad>/skills/<skill>/SKILL.md
<dados>/agentes/<squad>/CLAUDE.md                 (opcional)
<dados>/agentes/squads.json  rotinas.json         (registos das squads por modelo e das rotinas)
<dados>/agentes-rascunhos/agentes/<squad>/<nome>.md  + <nome>.ensaio.json
<dados>/agentes-rascunhos/squads/<id>/rascunho.json  + ensaio.json
<dados>/agentes-fila/<id>.json  + <id>.feito.json ; feito/ (rotação)
<dados>/runner/agentes-ia.jsonl                   (livro de custos da IA)
<dados>/runner/tarefas/<tid>.json                 (estado de cada tarefa só do runner: worktree, cópias, sessão, tectos da rotina; a retoma nunca confia na meta.json)
```

**`GET /api/agentes`** (read-only, `no-store`): per squad (those in `office.yaml` and those in `squads.json`), each
agent's allowed frontmatter, the relative path and the `sha256` of the `.md`, the skills, the session model
(`squads[].modelo`, otherwise `runner.modelo`, otherwise `claude`'s default), candidates with their last test run, the
squad draft, routines and the 50 most recent requests; `escrita` says whether the queue is writable. **Never the body
of the `.md` files**: a whole candidate only comes out on request, in `GET /api/agentes/candidato?squad=&nome=`
(`{"md", "sha256"}`, 404 if there is none). A `.md` that is a symlink, larger than 80 KB, has no frontmatter or has
`---` inside it shows up as «ilegível» ("unreadable"; the file name only).

**`POST /api/agentes/pedido`** `{"token", "accao", "args"}` (exact keys; same rules as `/api/decisao`: JSON, same
origin, `token`): 413 (> 80 KB), 403, 400 (`campos inválidos` or the reason: squad, agent, skill, candidate, project
or squad template not found, model not in the list, routine limits…), 409 (`já há um pedido pendente para este alvo`,
already a pending request for this target; `fila cheia`, 50 pending), 503 (`fila dos agentes indisponível`: folder
missing or not writable). Success: **202** `{"id", "accao"}`.

**Request** `<dados>/agentes-fila/<id>.json` (contract `v: 1`, written by the Office; the name comes only from the
clock and `secrets.token_hex(4)`), exact keys in this order:
```json
{"v": 1, "id": "20261118T093000-0a1b2c3d", "accao": "modelo",
 "args": {"squad": "dev", "agente": "revisor", "sha256": "<sha256 do .md que viste>", "modelo": "haiku"},
 "pedido_em": "2026-11-18T09:30:00+00:00", "ip": "…", "ua": "…"}
```
`pedido_em` ISO with a time zone; `ip` ≤ 64 and `ua` ≤ 200 characters; file ≤ 96 KB. Multi-line texts (`descricao`,
`missao`, `tarefa`, `instrucoes`) with 1–2000 characters, no control characters (except `\n` and `\t`) or invisible
ones (`Cf`, `Co`, `Cs`, except the ZWJ). `args` has exactly the action's keys:

| `accao` | `args` | target | AI (cap) |
|---|---|---|---|
| `modelo` | `squad, agente, sha256, modelo` (`inherit`, `sonnet`, `opus`, `haiku` or `""` = remove the field) | `agente:<squad>/<agente>` | |
| `skills` | `squad, agente, sha256, skills` (0–20, unique, the squad's) | same | |
| `agente_remover` | `squad, agente, sha256` | same | |
| `agente_rascunho` | `squad, nome, descricao` | `cand:<squad>/<nome>` | 0.50 USD |
| `agente_criar` | `squad, nome, sha256` (of the candidate seen, or `null` if new), `md` (≤ 64 KB) | same | |
| `agente_ensaio` | `squad, nome, sha256, projecto, tarefa` | same | 2 USD |
| `agente_activar` | `squad, nome, sha256` | same | |
| `agente_descartar` | `squad, nome` | same | |
| `squad_rascunho` | `squad, modelo` (`desenvolvimento`, `investigacao`, `observacao`, `conteudo`), `missao` | `squad:<squad>` | 0.50 USD |
| `squad_criar` | `squad, sha256` (of `rascunho.json`) | same | |
| `squad_ensaio` | `squad, sha256` (of the lead's `.md`), `projecto, tarefa` | same | 2 USD |
| `squad_conversa` | `squad, texto` (1–1000 characters; one turn of the set-up conversation) | `squad:<squad>` | 0.15 USD |
| `squad_descartar` / `squad_desactivar` | `squad` | same | |
| `rotina_pedir` | `squad, projecto, hora` (`HH:MM`), `dias` (1–7 integers, 1 = Mon … 7 = Sun, ascending), `instrucoes`, `tecto_usd` (text `0.10`–`5.00`), `max_turnos` (1–30) | `rotina:<squad>` | |
| `rotina_retomar` / `rotina_cancelar` | `squad, rotina` | `rotina:<squad>/<rotina>` | |

**Runner response** `<id>.feito.json`: `{"ok": true}` or `{"erro": "<reason>"}`, with the reason from a closed list
(`pedido ilegível`, `versão desconhecida`, `pedido repetido nesta passagem`, `squad desconhecida`, `agente
inexistente`, `skill inexistente`, `modelo inválido`, `frontmatter inválido`, `o agente mudou depois de ser visto`, `o
rascunho mudou depois de ser visto`, `já existe`, `candidato inexistente`, `sem ensaio ok desta versão`, `o lead não
pode ser removido`, `projecto inválido`, `modelo de squad inexistente`, `squad já tem agentes`, `rascunho inexistente`,
`squad não registada`, `squad não lançável`, `limite de rotinas`, `rotina inexistente`, `rotina não pausada`, `tecto
diário de IA atingido`, `já há uma conversa em curso`, `tecto da conversa atingido`, `a conversa já
terminou`, `a IA não respondeu bem, envia de novo`, `não foi possível montar a squad`, `a IA falhou`, `tempo
esgotado`, `erro de escrita`). Invalid request: an error and nothing
written. **Rotation:** the request and the response go to `feito/` once the response is older than
`runner.rotacao_dias`.

**Runner contract** (the queue comes from a container and an agent with `Bash` can write to it; the runner doesn't
trust it): only names `^\d{8}T\d{6}-[0-9a-f]{8}\.json$`, opened with `O_NOFOLLOW` and `fstat`; it re-validates the
shape, the config, the disk and the `sha256` of what was seen; at most 50 requests per pass (in name order), 1 per
target and **1 AI action**, last (the other AI ones get no response until the next pass); writes by tmp + `rename`,
activation and the routine `.md` by exclusive creation. AI uses the launcher's credential and environment, in a
`mkdtemp` folder that is always deleted; the user's text goes only through stdin, between markers with a nonce, never in
argv. Draft: `claude -p --tools "" --max-turns 1 --strict-mcp-config --output-format json --no-session-persistence
--max-budget-usd 0.50` (plus the squad's `--model`), 120 s. Test run: `--agent <nome> --permission-mode dontAsk
--allowedTools Read Grep Glob --disallowedTools Bash Edit Write WebFetch NotebookEdit --max-turns 8 --max-budget-usd
2`, with the default denials in a `--settings` outside the clone and the agent in an `--agents <file>` also outside
the clone, `--setting-sources user`, 600 s; on failure, `motivo` carries the last stderr line (≤ 200 characters, no
environment values, secrets masked). **`ensaio.json`**: `{"v": 1, "sha256",
"estado": "ok"|"erro", "motivo", "relatorio", "nao_ensaiadas", "custo_usd", "em"}` (`relatorio` with secrets masked,
≤ 8000 characters); «ok» = `claude` exited 0, no error, in time and within the cap.

**Decision `.md` for a routine** (`<dados>/decisoes/rotina-<squad>-<id>.md`, exclusive creation, the
[Approvals](#approvals-decisions-adr-001) format):
```
---
estado: pendente
criado: 2026-11-18
---
# Rotina r1 da squad Dev
## O quê
Correr uma tarefa da squad dev no projecto exemplo às 07:30 (seg, qua, sex), no fuso Europe/Lisbon.
## Datas e orçamento
Tecto: 0.50 USD por execução · máximo 10 turnos.
## Instruções
> cada linha das instruções, citada com "> "
## Definição
    <a definição em JSON canónico, numa linha>
## Decisões
- [R1] Activar a rotina r1 (tecto 0.50 USD por execução)
```
No line coming from the user opens a section or a decision (instructions with `> `, the definition indented). The
definition is `json.dumps({squad, id, projecto, hora, dias, instrucoes, tecto_usd, max_turnos}, ensure_ascii=True,
sort_keys=True, separators=(",", ":"))` and its `sha256` is kept in `rotinas.json`. The runner only launches with
`R1 → aprovado` and the `## Definição` line equal to the register's. Task: `kind` = the first of `tipos`, `mode`
`task`, slug `rotina-<squad>-<id>-AAAAMMDD`; 3 tasks in a row in `erro` (or failures to prepare) → `pausada`.

**What the runner copies into the worktree** (when preparing the task and again on resume, also for routines and in
the test-run clone): `<dados>/agentes/<squad>/agents/*.md` → `<worktree>/.claude/agents/`, `skills/<skill>/…` →
`.claude/skills/<skill>/…` and `CLAUDE.md` → `.claude/CLAUDE.md`. `claude` reads them as the project's agents, skills
and instructions; argv and `HOME` don't change. Regular files only, up to 4 folders deep in each skill and 2 MB per
squad; a symlink in the path = that branch isn't copied. **The repo's wins:** if the project already has a file at
the same path, it stays untouched and the task records in `issues` «o repo já tem .claude/agents/x.md: ficou o do
repo» ("the repo already has …: kept the repo's"). On resume only the copies made by the runner are updated. Each
copied path goes, anchored (`/.claude/agents/x.md`), once, into the repository's `info/exclude`
(`git rev-parse --git-path info/exclude`): it never enters commits and `git status` stays clean. That file belongs to
the repository, so the patterns also apply in the main checkout.

### Other parts

- **Biblioteca** (library, `▤`): files from the `biblioteca` sources and the tasks' `SPEC/PLAN/QA.md`, to view and
  download on your phone. Only allowed extensions (image, video, audio, `md txt csv pdf`), no hidden files, secrets
  or symlinks, texts masked; thumbnails made with Pillow in a 128 MB `tmpfs`. Sources with `media: true` are grouped
  into batches (one folder = one batch; highlights in `final/`). It never waits for a scan (ADR-010): the index is
  rebuilt in a thread after 60 s and requests are answered with the previous one (only the first, right after
  start-up, waits); `/f/` and `/mini/` never wait for the index (without it, or with a stale one, they check the path
  directly on disk with the same rules). The list carries an `ETag` and `Cache-Control: private, no-cache`: the
  browser revalidates and, when nothing changed, gets a `304` with no body. Old documents and batches can be archived
  and restored (see [Biblioteca archive](#biblioteca-archive-adr-011)).
- **Phone notifications** (⚙ Definições, "Settings"): Web Push (VAPID, `cryptography`) for finished tasks, opened PRs,
  stalled tasks, approvals and phases; needs HTTPS (a domain). Keys and subscriptions in `<dados>/push/`. Messages
  carry only the squad, project, status and ids, never the task text.
- **Progress bar:** phases SPEC → PLAN → CODE → QA → PR for `goal` tasks, and an estimate from the median of the
  latest tasks completed by the same squad and mode.
- **Office (0.3.0):** a counters bar (rooms, agents, working, subagents, «à tua espera» ("waiting for you") = open
  approvals + PRs, failures) and «precisa de ti» ("needs you") notices with Reply/View; ⚙ holds music, volume, theme,
  notifications and «Dia e noite» (day and night). Side list Tarefa | Agentes | Entregas (Task | Agents | Deliveries):
  `/` searches an agent and «Seguir» (Follow) tracks it; Activity and «O meu dia» (My day: time working and waiting,
  «Ver tabela»). **Deliveries** (ADR-014, `GET /api/entregas`): what was finished in the last 7 days, "unread" in the
  last 24 h. **Day replay** (ADR-015, `GET /api/replay?dia=YYYY-MM-DD`, today and the 6 days before): the day rebuilt
  from task metas and transcripts at 60×/180×/600×, with no new writes. The task panel shows how the run ended
  (ADR-013: denied tools and API errors, read from `claude.log`). **Demo:** `/?demo=1` shows the office with made-up
  data, without touching yours. The new `/api/state` fields are additive: `espera`, `falhas_exec`,
  `tasks[].titulo/fim`, `tarefas/linhas/modelo/ramo` per agent and `today.inicio/intervalos`.

### Security

To report a vulnerability, see [`SECURITY.en.md`](SECURITY.en.md) (GitHub private reporting; never in a public issue).

- Authentication is only Caddy's (or Traefik's) **basic auth**, with a bcrypt hash in `.env` (0600). The Office has
  no login of its own; the `token` in `/api/state` only blocks CSRF.
- With Caddy, the Office sits on a compose network shared only with Caddy and publishes no ports. With
  `--proxy traefik` it sits on Traefik's shared network: any container on that network reaches the Office without
  a password.
- The proxy puts a random secret (`OFFICE_PROXY_SEGREDO` in `.env`, generated by the installer) in the
  `X-Office-Segredo` header, and the Office rejects (403) anyone who doesn't send it, except `/healthz`: reaching the
  network is not enough. With `--proxy traefik` the secret lives in a container label: Traefik's API/dashboard
  and the Docker socket must not be reachable from other containers on that network.
- **No domain means no HTTPS:** the password travels in clear text and Web Push doesn't work. Use a domain.
- **Security headers** on every proxy response, including basic auth's 401:
  `X-Frame-Options: DENY` (nobody can put the Office in a frame) and `Referrer-Policy: same-origin`; Caddy adds
  `Content-Security-Policy: frame-ancestors 'none'` (Traefik doesn't: it would replace the CSP the Office sends in the
  Biblioteca). `Strict-Transport-Security` (HSTS, 1 year) is only sent with a domain: with Caddy only over HTTPS
  (never with `OFFICE_SITE=:80`); with Traefik always (that mode requires a domain). A full CSP is left out (the
  inline JS would require hashes).
- **Images pinned by digest:** `python:3.13-alpine` (`Dockerfile`), `caddy:2.11.9-alpine` (generated compose) and
  `httpd:2.4-alpine` (only for the password hash, in `install.sh`) carry the `@sha256:` of the multi-architecture
  index; the image's optional dependencies are in `requirements.txt` with pinned versions. A `docker
  pull` doesn't change what runs without a new Lite release. In Docker mode, the release's `compose.yaml` pins by
  digest the Office and runner images (`ghcr.io/mozthinker/squad-office-lite` and `…-runner`), built and attested
  by the public repository; the runner image pins its Python base by digest and `gh` and `claude` by version and
  sha256. Optional origin check (with `gh` on your PC):
  `gh attestation verify compose.yaml --repo MozThinker/squad-office-lite`.
- The Claude credentials (`claude.env`, 0600) stay on the host only, for the runner: the Office doesn't mount, store
  or show them. The installer doesn't ask for the subscription token. The secrets in `runner.env` are removed from the
  runner's environment at startup; `git`, `gh` and `claude` get only the allowlisted environment (`claude` plus the
  credential, `gh` plus `GH_TOKEN` if set; see [Claude credentials](#claude-credentials)).
- The runner runs `claude` with `--permission-mode dontAsk`, the list of pre-authorised tools
  (`runner.ferramentas`), the fixed prohibitions and the default denials; `auto`, `acceptEdits` and
  `bypassPermissions` open up more (see [Runner](#runner)). **The denials apply to Claude Code's tools**, not to the
  programs the squad runs: an authorised interpreter or tool (`python3`, `npm`, `node`, `make`, a project script)
  reads and writes everything your user can. The strong protection is the
  [Claude Code sandbox](#runner) (in `runner.settings`) or **a Linux user just for the Office**, with no other
  keys, projects or sessions.
- **The agent and the container run with the same uid** (yours). So the runner can't tell who wrote a request in
  `quadro-lancar/` or a decision in `decisoes-fila/`: the Office or a program the squad ran. The denials stop Claude
  Code's Write and Edit from writing there, but not an authorised `python3`. The same goes for `office.yaml`. What
  the Lite does: it warns in the journal and on Telegram when `office.yaml` changes («office.yaml mudou (era
  …, é …); se não foste tu, revê-o já», "office.yaml changed (was …, is …); if it wasn't you, review it now"). What you
  do: read the whole approval request before approving, review PRs before merging them, protect the default branch
  on GitHub and, if the `office.yaml` warning shows up without you having changed it, stop the running tasks
  (`office_runner.py parar`) and review the config.
- **The container mounts the whole `raizes.claude_projects`** (by default `~/.claude/projects`, read-only): the
  container reads all of that user's Claude Code sessions, not just those of Office tasks (it counts tokens for all of
  them; it shows conversations only for the tasks and squads in the config). A security flaw in the Office could
  expose them all. If you use `claude` on the VPS for other things, use a user just for the Office (the
  recommendation above) or accept the risk.
- **Docker mode (on your PC):** the agent and the runner run in the `runner` container with the same uid (10001, or
  your `OFFICE_UID`), so the limit above still holds for the queues (`quadro-lancar/`, `decisoes-fila/`), which the
  runner mounts writable. In exchange, they no longer see your user's home, `~/.ssh` or the rest of the PC (only the
  projects folder), they don't see the Office's credentials (the `proxy` volume is only in Caddy and the Office) and
  they have `office.yaml` and `claude.env` read-only (`config` and `segredos` volumes): a squad program can no longer
  change them. The Office only reads the runner's `claude` sessions (`claude-projects` volume), not your PC's. The
  runner has no Docker socket, isn't on the Office's network (they talk only through the queues) and runs with a
  read-only container root filesystem. The Claude Code sandbox isn't supported in the container: the isolation is the
  container itself (see [Docker mode limitations](#docker-mode-limitations)). **The mounted projects folder is written
  by the agent:** put only the Office's repositories there, never your personal folder. The Claude credential is still
  in `claude`'s environment and the `gh` session in the runner's home, which an authorised interpreter can read.
  `configurar` runs as root only to set the owner of the volume folders and drops its privileges right away. **No
  extra capabilities:** the four containers run with `no-new-privileges` and `cap_drop: [ALL]`. The Office and the
  runner have none (the `tini` from `init: true`, `parar`, which only kills processes with the same uid, `git`, `gh`
  and `claude` don't need any, without the sandbox). `configurar` has only `CHOWN` (owner of the volume roots),
  `DAC_OVERRIDE` (to open the 0700 roots already handed to `OFFICE_UID` when it runs again), `SETGID` and `SETUID`
  (to drop root; `no-new-privileges` doesn't stop going down); the mode of the roots is set after root is dropped.
  Caddy has only `NET_BIND_SERVICE` (the official image's binary carries it as a *file capability*; without it in the
  bounding set startup fails) and `DAC_OVERRIDE` (it runs as root and reads the `proxy` volume, 0700 owned by
  `OFFICE_UID`). The data, config, secrets, proxy and runner-home volumes are mounted with `nocopy`: nothing from the
  image is copied into them, not even the owner, which would undo `configurar`'s on every start (observed with an
  `OFFICE_UID` other than 10001; mechanism in
  [Populate a volume using a container](https://docs.docker.com/engine/storage/volumes/#populate-a-volume-using-a-container)
  and in the [`nocopy`](https://docs.docker.com/reference/compose-file/services/#volumes) option, checked on
  8 November 2026). **The runner's home (the
  `runner-home` volume) is written by the agent, and nothing privileged executes it:** the image's commands run Python
  with `-I` (no `~/.local`, `.pth` or `usercustomize`; `PYTHONNOUSERSITE=1` in the image) and `configurar`'s `git`
  and `gh` run without the user's config (no global `~/.gitconfig`, hooks or templates; `~/.gitconfig` and
  `~/.config/gh` are only written if they aren't symlinks). **Run `configurar` with the runner stopped**
  (`docker compose stop runner` before, `docker compose up -d` after): a squad program left running could swap those
  files, or the freshly created `projectos/exemplo` folder, while `configurar` runs. With no domain or HTTPS, the
  Office listens only on `127.0.0.1` by default.
- `GET /api/config` carries no paths, contact or secrets. Briefs, cards and requests are user data, never
  instructions for the agents.
- **Remaining risks** (the denials don't close them):
  - the Claude credential is in the environment of the `claude` process itself: an authorised interpreter can read it.
    Prefer an API key with a spending limit in the Anthropic console;
  - removing `WebFetch`/`WebSearch` doesn't close the network: `git push`, `gh pr create` and the interpreters reach
    the internet;
  - `Bash(git *)` (in the default list) authorises `git -c …`, which can run commands;
  - a `.claude/settings.json` inside a project (hooks, `allow`) is applied by `claude`: only launch squads on
    repositories you own;
  - `get.sh` checks integrity (sha256 from the same release), not authenticity: whoever controls the account, the
    `main` branch or the releases on GitHub can swap both. Protect them with 2FA. The origin check
    (`gh attestation verify`, see [Installation](#installation)) is optional and up to you;
  - `estado.json` sits in a folder the agent can't edit with Edit/Write, but an authorised interpreter can write to
    it: the worst effect is a false ✓/✗ in «Primeiros passos» ("First steps").

### Server variables

Set by the generated compose: `OFFICE_CONFIG` (default `/config/office.yaml`), `TASKS_DIR`, `CLAUDE_PROJECTS_DIR`,
`SQUADS_DIR`, `APROVACOES_DIR`, `BIBLIOTECA_DIR`, `DECISOES_FILA_DIR`, `QUADRO_DIR`, `QUADRO_LANCAR_DIR`, `PUSH_DIR`,
`ARQUIVO_FILA_DIR` (default `/data/arquivo-fila`), `RUNNER_ESTADO_DIR` (default `/data/runner-estado`), `AGENTES_DIR`, `AGENTES_RASCUNHOS_DIR` and `AGENTES_FILA_DIR`
(defaults `/data/agentes`, `/data/agentes-rascunhos` and `/data/agentes-fila`), `ACCOES_SAUDE_DIR` (default
`/data/accoes-saude`, read-only; the server reads only `saude.json`), `PUSH_CONTACTO` (optional) and
`BIBLIOTECA_MINI_DIR` (default `/tmp/miniaturas`); `PORT` (8080). In Docker mode also `OFFICE_MODO=docker`
(`GET /api/config` gains `"modo": "docker"`, otherwise `"vps"`; «Primeiros passos» shows the
`docker compose …` commands) and `OFFICE_PROXY_SEGREDO_FILE=/proxy/segredo` instead of `OFFICE_PROXY_SEGREDO`: the
secret comes from that file (written by `configurar`) and, while it's missing, the Office answers 503
«Falta configurar: corre `docker compose run --rm configurar`» ("Not configured yet: run …") to everything except
`/healthz` (it is never left open).
Running locally: `OFFICE_CONFIG=office.example.yaml TASKS_DIR=/tmp/t … PORT=18080 python3 server.py`.

### Tests

```bash
python -m pytest tests -q            # precisa de pytest; dados falsos em pastas temporárias
node --test tests/*.mjs
```
Without Pillow/`cryptography` the thumbnail and encryption tests are skipped. The runner is tested with a fake
`claude` (`tests/bin/claude`). CI runs the tests, `shellcheck` on the installer and a full smoke install; it doesn't
deploy, and CI for pull requests never publishes anything. The Docker mode images are only published (to GHCR) by a
manual workflow in the public repository, when a release is prepared (see [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md),
“Preparing a release”, and ADR-008).

## Community

- **Help, questions and ideas:** in [Discussions](../../discussions).
- **Bugs:** in an [issue](../../issues/new/choose), using the **Bug** template (no tokens, keys or `.env`). Security
  vulnerabilities never go in an issue: see [`SECURITY.en.md`](SECURITY.en.md).
- **Tried it? Tell us:** open an issue with the
  [**Relatório de teste da instalação / Installation test report**](../../issues/new?template=teste-instalacao.yml)
  template: your system, how long it took to reach «Primeiros passos» ("First steps") with all 4 ✓, whether you got
  to the first task and what you didn't understand here.
- By taking part, you agree to the [code of conduct](CODE_OF_CONDUCT.en.md). To contribute code, read
  [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md).

## Contributing

How to report, propose, set up the environment, run the tests and what gets accepted in a PR:
[`CONTRIBUTING.en.md`](CONTRIBUTING.en.md) (security vulnerabilities: [`SECURITY.en.md`](SECURITY.en.md)). The
architecture decisions are in `DECISIONS.md`.

## License

[MIT](LICENSE). The Pixelify Sans typeface used in titles (`static/fonts/`) is under the SIL Open Font License
1.1 (`static/fonts/OFL.txt`).
