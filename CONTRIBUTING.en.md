# Contributing to Squad Office Lite

🇬🇧 **English** | 🇵🇹 [Português](CONTRIBUTING.md)

Thanks for helping. This guide is short: read it before you open an issue or a pull request. By taking part, you
agree to the [code of conduct](CODE_OF_CONDUCT.en.md).

## Reporting a bug

1. Search the [issues](../../issues) to see whether it already exists. If it does, add your case there.
2. Otherwise, open a new issue with the **Bug** template. It asks for the mode (VPS, or Docker on Windows, macOS or
   Linux), the Lite version, the system and Docker version, the `doctor` output, and what happened versus what you
   expected. To run `doctor`:
   - in Docker mode, in the `compose.yaml` folder: `docker compose exec runner doctor`;
   - on the VPS: `python3 ~/squad-office-lite/runner/office_runner.py doctor`.
3. **Never paste tokens, API keys, passwords or the contents of your real `.env`, `claude.env`, `runner.env` or
   `office.yaml`.** Issues are public. If you need to show a file, replace the secrets with `…`.

Did you test the installation using only the README? Use the **Relatório de teste da instalação** (Installation test
report) template, even if everything went well.

**Security vulnerabilities don't go in issues:** follow [`SECURITY.en.md`](SECURITY.en.md) (GitHub private reporting).

## Suggesting an idea or asking a question

Use [Discussions](../../discussions), not issues. An idea discussed there first has a better chance of turning into
an accepted PR.

## Setting up the environment and running the tests

You need Python 3.13 (the server; the runner runs on 3.10 or later), Node.js 22 and, for the shell scripts,
`shellcheck`. You don't need Docker, Caddy or the real `claude`: the tests use temporary folders and a fake `claude`
(`tests/bin/claude`).

```bash
git clone https://github.com/MozThinker/squad-office-lite.git
cd squad-office-lite
python3 -m venv .venv && . .venv/bin/activate
python -m pip install pytest            # opcional: -r requirements.txt (Pillow e cryptography)
python -m pytest tests -q
node --test tests/*.mjs
shellcheck install.sh get.sh scripts/*.sh
```

Without Pillow and `cryptography`, the thumbnail and encryption tests are skipped, as in CI. Docker, systemd and the
full `install.sh` only run in CI. More details in the README, “Reference” → “Tests”
([English](README.en.md#tests)).

## Commits

Small and conventional, in PT-PT: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `ci:`, with a scope when
it helps. Examples: `fix(configurar): …`, `docs(readme): …`. One commit, one change.

## What a PR can include

**VPS mode is the Lite's main path;** Docker mode on the PC is newer and meant for trying it out. A PR that changes
one of them must not break the other.

We accept:
- bug fixes, with a test that failed before;
- improvements to the README and to error messages (especially where a tester got lost);
- small features discussed beforehand in Discussions.

Every PR:
- passes the three commands above;
- uses PT-PT in code, interface and texts;
- has no constants from a specific installation (names, paths, domains): everything comes from `office.yaml`;
- doesn't change the v1 contracts (decision queue, `cartoes.json`, launch requests, push) or the `office.yaml` format
  without a new version (see the README, “Reference”);
- documents a new `office.yaml` key in the README table and in `office.example.yaml`, with boundary tests;
- adds a line to `CHANGELOG.md`, under «Por publicar» (Unreleased);
- never passes user data (briefs, cards, requests) in argv or through the shell.

We don't accept new dependencies without a good reason (the server and the runner only use the standard library), nor
CI changes that publish something or use secrets. The architecture decisions are in [`DECISIONS.md`](DECISIONS.md)
(in Portuguese): a change that goes against them needs a new ADR.

## How your PR gets in

The code in this repository is **generated** for every release from the repository where the Lite is developed. So
a PR here isn't merged directly (the next publication would wipe it out), but it is still reviewed and used:

1. Open the PR (or the issue) here, as usual.
2. The maintainers review it here, with you, in the PR comments.
3. If it is accepted, the maintainers apply the change, and it ships in the next release with credit to you:
   `Co-authored-by` on the commit, with the name and e-mail you used in the PR, and, if you agree, a mention in
   `CHANGELOG.md`.
4. The PR is then **closed** with a link to the release that includes it. On GitHub it shows as «Closed» and not as
   «Merged»: that's normal, your change made it in.

For large changes, open an issue or a Discussion first so we can agree on the approach: it saves you work.

## Preparing a release (maintainers)

`scripts/preparar-publico.sh DESTINO` generates a tree without history or `docs/` and fails if it finds internal names
or personal paths (the list of names comes from the «Genérico» step of the `ci.yml` of whoever prepares the release and
doesn't go into the public copy; without it, the script refuses to run); `scripts/release.sh X.Y.Z` (requires
`## Lite X.Y.Z` in `CHANGELOG.md`) creates `dist/squad-office-lite-X.Y.Z.tar.gz` and `dist/SHA256SUMS`, the two files
that `get.sh` downloads. Neither of them publishes anything: the GitHub release (tag `vX.Y.Z` with the two files
attached) is manual.
In the public tree (which no longer has the list of names) you use `--publica` (`scripts/release.sh --publica X.Y.Z`):
it skips the names check and keeps the personal-paths check. That's how the public repository's release workflow
builds and attests the two files, which it only attaches to a draft release; publishing stays manual.
After that one, the manual images workflow (only in the public repository) builds the Office and runner images
(`Dockerfile`, `Dockerfile.runner`; amd64 and arm64), publishes them to `ghcr.io/mozthinker/squad-office-lite` and
`…-runner` with the release tag, attests them and attaches to the same draft release the `compose.yaml` (generated by
`runner/gerar_compose.py --modo docker` with the images pinned by digest) and `env.example`, also attested. The
repository's `compose.example.yaml` is the generator's output with tags and no digest.
