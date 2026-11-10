# Security

🇬🇧 **English** | 🇵🇹 [Português](SECURITY.md)

## How to report a vulnerability

Use GitHub's private reporting: in the repository, the **Security** tab → **Report a vulnerability**. Only you and the
maintainers can see the report.

**Never** report a security vulnerability in a public issue, a pull request or a comment: anyone can read them before
there is a fix.

## What to include

- the Lite version (the most recent entry in your installation's `CHANGELOG.md`) and the Ubuntu version (in Docker
  mode: the PC's operating system and the Docker Desktop version);
- what happens and what should happen;
- the steps to reproduce it, preferably on a test installation;
- the impact you see (what an attacker can read, write or execute, and from where).

Don't send credentials, tokens, or your real `office.yaml`, `.env`, `claude.env` or `runner.env` (nor the contents of
the Docker mode `segredos` and `proxy` volumes), nor third-party data. If you need to show a file, replace the secrets
with `…`.

## What you can expect

- We confirm we've received the report and reply as soon as we can. We don't promise fixed deadlines: we're a small
  team.
- We tell you whether we accept the vulnerability and, if so, when we expect to have a fix.
- We ask you not to disclose the vulnerability before a fixed version is out. We agree on the date with you.
- With your consent, we thank you in the `CHANGELOG.md` of the release that fixes it.

## Fix period

Security fixes are released on the latest version of the Lite, with the fix described in `CHANGELOG.md`. Book
purchasers are guaranteed them for **at least 12 months from the date of purchase**.

## How to update

The same command as the installation (see the README, “Updating”):

```bash
curl -fsSL https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.1.4/get.sh | bash
```

It downloads the latest published version, checks its sha256 and runs `install.sh`. The previous version stays in
`~/squad-office-lite.anterior-<data>`; `~/office-lite` (config, credentials and data) is not touched. To also confirm
where the package came from (optional), see the README, “Installation” (`gh attestation verify`).

In Docker mode (on the PC), download the new version's `compose.yaml` and run `docker compose pull` and
`docker compose up -d` (see the README, “Updating on the PC”).

## Scope

**In scope for the Lite:** the code in this repository (`server.py` and modules, `static/`, `runner/`, `install.sh`,
`get.sh`, `scripts/`, `Dockerfile`, `Dockerfile.runner`, `Caddyfile`, `Caddyfile.docker`, the generated compose file
and the release's `compose.yaml`, with the images published on GHCR) and what the README says it does: authentication
at the proxy, decision and launch queues, default denials passed to Claude Code, the `claude` environment on an
allowlist, Claude credentials kept outside the Office container.

**Not in scope for the Lite** (report it to whoever maintains them):
- **Claude Code** (`claude`): the model, how `permissions.deny` rules are enforced, the sandbox and login;
- the **GitHub CLI** (`gh`) and GitHub itself;
- **Docker** (and Docker Desktop), **Caddy**, **Traefik**, Ubuntu and the base images;
- the configuration of your VPS (firewall, SSH, users, DNS) or of your PC.

The Lite's known limits are in the README, “Security” section (for example: the agent runs as your user, and a program
it runs can read whatever you can read). A report about an already documented limit is welcome if it shows an impact
that isn't described there.

See the [English README](README.en.md) for the sections mentioned above.
