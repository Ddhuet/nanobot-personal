# Personal nanobot source and recovery repository

This private repository preserves the customized nanobot deployed on the VPS as
of 2026-08-07. It is based on the pip distribution
`nanobot-ai==0.1.4.post6`.

The repository is the source of truth for future code changes. It intentionally
does not contain the assistant's configuration, API credentials, conversations,
memory, media, cron state, or other files from `~/.nanobot`.

## Important safety rule

Inspect scripts before running them. The installer refuses to replace code while
the `nanobot` systemd service is active, but stopping, installing, enabling, and
starting the service remain deliberate operator actions.

## Repository layout

```text
src/nanobot/                       complete customized Python package
setup.sh                           safer installer/deployer for this snapshot
scripts/check-live-diff.sh         compares repo source with a deployed venv
deployment/setup-original.sh      original recovery script, preserved verbatim
deployment/nanobot-custom.patch   original customization patch
deployment/nanobot.service        original VPS unit, preserved verbatim
deployment/nanobot.service.template
                                    portable template used by the safer installer
environment/requirements-runtime.txt
                                    exact dependency versions from the working VPS
environment/nanobot-version.txt    pinned base distribution version
STRUCTURE.md                       detailed map of the original VPS deployment
```

The two deployment approaches are intentionally preserved:

1. `deployment/setup-original.sh` installs the pinned wheel and applies the
   historical patch. It is an archival recovery artifact and should not be run
   casually.
2. The root `setup.sh` installs the pinned dependencies when necessary and
   deploys the complete, reviewed `src/nanobot` tree. This is the recommended
   path after testing.

## Normal edit and deploy workflow

Edit files only under `src/nanobot`, not inside the virtualenv:

```bash
cd ~/nanobot-personal
git pull --ff-only

# Make and review changes under src/nanobot.
git status
git diff
git add src/nanobot
git commit
git push
```

Deploy a committed version to this VPS:

```bash
sudo systemctl stop nanobot
cd ~/nanobot-personal
./scripts/check-live-diff.sh || true
./setup.sh
sudo systemctl start nanobot
sudo systemctl status nanobot --no-pager
```

`setup.sh` copies the repository source into the virtualenv's site-packages.
Before replacement, it stores the previous package under
`~/nanobot/source-backups`. Therefore Python will continue importing from the
apparently strange but normal venv path:

```text
~/nanobot/bot-env/lib/python3.12/site-packages/nanobot
```

A Git edit does not change the running copy until `setup.sh` deploys it.

## Install on a new VPS

The preserved environment was Python 3.12. The script requires Python 3.11 or
newer and, by default, refuses a different minor version so accidental runtime
drift is visible.

```bash
git clone git@github.com:Ddhuet/nanobot-personal.git
cd nanobot-personal

# Read this repository's AGENTS.md and inspect setup.sh first.
./setup.sh --install-systemd
```

The script does not enable or start the service. After restoring or creating the
private `~/.nanobot` configuration, explicitly run:

```bash
sudo systemctl enable nanobot
sudo systemctl start nanobot
```

On a host without Python 3.12, install it first or consciously pass
`--allow-python-version-mismatch` after testing compatibility.

Private repositories also require GitHub authentication on every VPS. Prefer a
repository-specific deploy key with write access only where pushing from that
VPS is necessary.

## Rollback

Every successful source replacement saves the previous deployed package beneath
`~/nanobot/source-backups/nanobot-<UTC timestamp>/nanobot`. Stop the service
before restoring one of those directories.

Git tags should also mark known-good deployments:

```bash
git tag live-rescue-2026-08-07
git push origin live-rescue-2026-08-07
```

## Secrets and state

Never commit `~/.nanobot/config.json`, `.env` files, tokens, session JSONL
files, memory, logs, media, or backups containing them. Back up mutable nanobot
state separately with encryption.
