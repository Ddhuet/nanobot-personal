# Nanobot VPS structure and recovery notes

Snapshot date: 2026-08-07 UTC

This document describes the currently deployed personal nanobot. It is an
inventory and recovery plan, not an instruction to upgrade it. The live code
and systemd service were only inspected while preparing this file. Nothing was
started, stopped, reloaded, installed, upgraded, or edited other than this
document.

## Short version

- `/home/hotrootsoup/nanobot/bot-env` is a Python virtual environment, not a
  nanobot source checkout.
- The live Python package is
  `/home/hotrootsoup/nanobot/bot-env/lib/python3.12/site-packages/nanobot`.
- `lib` is a real directory. `lib64` is a symlink to `lib`, so both spellings
  reach exactly the same files.
- The installed distribution is `nanobot-ai==0.1.4.post6` and was installed by
  `pip`. It is not an editable Git installation.
- There is currently no Git repository in `/home/hotrootsoup/nanobot` or its
  inspected descendants.
- The assistant's configuration and mutable state are separate from the code,
  under `/home/hotrootsoup/.nanobot`.
- `/etc/systemd/system/nanobot.service` is a system service. Systemd controls it
  as root, but the actual nanobot process runs as `hotrootsoup`.
- The service was inactive (stopped) when inspected, but it is **enabled**, so
  it is configured to start on the next server boot.

## Filesystem map

```text
/home/hotrootsoup/
├── nanobot-deploy.tar.gz                recovery bundle; see below
├── nanoCodeBak-6-15-2026/               exact copy of live package (no Git)
├── nanoCodeBak-6-17-2026/               exact copy of live package (no Git)
├── 4-12-2026-nano/.../nanobot/          older upstream Git checkout, not live code
├── 4-14-2026-workspacebaknan/.../nanobot/
│                                         older upstream Git checkout, not live code
├── nanobot/
│   ├── bot-env/                         Python virtual environment
│   │   ├── STRUCTURE.md                 this document
│   │   ├── pyvenv.cfg                   virtualenv metadata
│   │   ├── bin/
│   │   │   ├── python -> python3
│   │   │   ├── python3 -> /usr/bin/python3
│   │   │   └── nanobot                  generated command wrapper
│   │   ├── lib/                         real directory
│   │   │   └── python3.12/site-packages/
│   │   │       ├── nanobot/             LIVE NANOBOT PYTHON CODE
│   │   │       └── nanobot_ai-0.1.4.post6.dist-info/
│   │   │           ├── METADATA
│   │   │           └── RECORD           pip's original file/hash inventory
│   │   └── lib64 -> lib                 alternate name for the same directory
│   └── data-gym-cache/                  separate cache; not nanobot source
│
└── .nanobot/                            mutable instance data (not source)
    ├── config.json                      configuration; assume it contains secrets
    ├── nanobot_omega.log                log
    ├── media/
    └── workspace/
        ├── AGENTS.md
        ├── HEARTBEAT.md
        ├── SOUL.md
        ├── TOOLS.md
        ├── USER.md
        ├── cron/jobs.json
        ├── memory/
        ├── sessions/                    conversation/session JSONL files
        └── skills/

/etc/systemd/system/
├── nanobot.service                     root-owned system unit
└── multi-user.target.wants/
    └── nanobot.service -> ../nanobot.service
```

The OS Python is `/usr/bin/python3.12`. When invoked through
`bot-env/bin/python`, Python changes its environment prefix to
`/home/hotrootsoup/nanobot/bot-env` and loads packages from that environment's
`lib/python3.12/site-packages` directory.

## What systemd runs

The installed unit is `/etc/systemd/system/nanobot.service` and currently has
these operationally important settings:

```ini
[Service]
User=hotrootsoup
Group=hotrootsoup
WorkingDirectory=/home/hotrootsoup/nanobot
ExecStart=/home/hotrootsoup/nanobot/bot-env/bin/nanobot gateway
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/home/hotrootsoup
```

The `bin/nanobot` wrapper runs the virtualenv Python and imports
`nanobot.cli.commands:app`. Consequently, edits inside the site-packages
`nanobot` directory are edits to the code used the next time the service starts.

The service status at inspection time was `inactive (dead)`. Its last stop was
a normal SIGTERM caused by a stop operation. It is also `enabled`, which means a
machine reboot can start it even while it is intentionally paused now.

### Why control requires sudo/root

This is a system unit loaded by the machine-wide systemd manager (PID 1), and
its unit file is root-owned under `/etc/systemd/system`. Changing system state
with `start`, `stop`, `restart`, `enable`, or `disable` therefore requires root
or a matching polkit authorization. This does **not** mean the bot runs as root:
the unit explicitly runs its process as `hotrootsoup`.

Read-only commands such as these normally do not require sudo:

```bash
systemctl status nanobot --no-pager
systemctl cat nanobot
systemctl is-enabled nanobot
```

State-changing commands currently require authorization:

```bash
sudo systemctl start nanobot
sudo systemctl stop nanobot
sudo systemctl restart nanobot
```

There is no `systemctl --user` nanobot unit at present. Keeping this as a system
unit is reasonable for a VPS service that should run independently of login.
The simplest safe choice is to keep using sudo. A narrowly scoped polkit rule or
a migration to a user unit could remove the repeated sudo requirement, but both
change the deployment model and should be handled separately and tested.

## Live customizations detected locally

Pip's local `RECORD` file contains the hashes written when
`nanobot-ai==0.1.4.post6` was installed. Comparing those recorded hashes with
the current files required no network access and found:

- 83 tracked nanobot files still match their installed hashes.
- 16 tracked files differ from their installed hashes.
- No tracked nanobot files are missing.
- One non-cache Python file exists that was not in pip's record:
  `nanobot/utils/omega_log.py`.

The 16 changed tracked files are:

```text
nanobot/agent/context.py
nanobot/agent/loop.py
nanobot/agent/runner.py
nanobot/agent/tools/message.py
nanobot/agent/tools/registry.py
nanobot/agent/tools/web.py
nanobot/cli/commands.py
nanobot/config/schema.py
nanobot/heartbeat/service.py
nanobot/providers/anthropic_provider.py
nanobot/providers/azure_openai_provider.py
nanobot/providers/base.py
nanobot/providers/openai_codex_provider.py
nanobot/providers/openai_compat_provider.py
nanobot/session/manager.py
nanobot/utils/helpers.py
```

This hash comparison proves that the files differ from the installed wheel; it
does not judge whether every difference was intentional. The added
`omega_log.py` and all 16 differing files must be included in the rescue
snapshot. The whole `nanobot` package should be captured as well so the snapshot
is self-contained.

## Existing recovery artifacts

There are already three valuable, non-live recovery sources under
`/home/hotrootsoup`:

### Deployment bundle

`/home/hotrootsoup/nanobot-deploy.tar.gz` has SHA-256:

```text
b3105572f389c913db21f806568b08044f7a5cc0ffe2e04f618758c8305c6a5f
```

It contains:

```text
setup.sh                 installs nanobot-ai==0.1.4.post6 and applies the patch
nanobot.service          byte-for-byte identical to the installed systemd unit
nanobot-custom.patch     1,727 lines / 76,397 bytes; SHA-256 shown below
```

The patch covers exactly the 16 modified tracked files listed above and adds
`nanobot/utils/omega_log.py`. Its SHA-256 is:

```text
54f8f842fb083ab23df7d97d4461a53f85b3d03167fa59a90e89410de50583c6
```

This bundle is the closest thing currently present to a reproducible deployment
source. Preserve it. Do **not** casually execute `setup.sh` against the live
machine: it runs `pip install --upgrade pip`, installs the pinned nanobot package,
applies a patch inside site-packages, and can remove an existing venv if it
decides that venv is incomplete. It is a recovery recipe to review and test in a
staging location, not a safe everyday update command.

### Exact package snapshots

Both of these directories are byte-for-byte/tree-for-tree identical to the live
nanobot package when `__pycache__` is excluded:

```text
/home/hotrootsoup/nanoCodeBak-6-15-2026
/home/hotrootsoup/nanoCodeBak-6-17-2026
```

They are not Git repositories. They are nevertheless safer seeds for a Git
snapshot than copying out of the active venv because reading or committing them
cannot disturb imports used by the service. Keep both until the new private
repository has been pushed and independently verified.

### Older upstream checkouts

These are clean Git checkouts of `https://github.com/HKUDS/nanobot.git` at
commit `04a41e3` on `main`:

```text
/home/hotrootsoup/4-12-2026-nano/.nanobot/workspace/nanobot
/home/hotrootsoup/4-14-2026-workspacebaknan/.nanobot/workspace/nanobot
```

They differ substantially from the deployed pip package and are **not** the
source of truth for the running customized bot. They may be useful later for
researching upstream history, but never copy either one over the live venv.

## Git recovery plan

### What to create on GitHub

Create one new repository with these settings:

- Suggested name: `nanobot-personal`
- Visibility: **Private**
- Initialize repository: **No** (do not add a README, `.gitignore`, or license)
- Description: `Private source and deployment snapshot for my customized nanobot`

An empty repository makes the first push from the VPS straightforward. Do not
put `/home/hotrootsoup/.nanobot/config.json`, session logs, memories, media, or
credentials in this repository. Private Git repositories are not a substitute
for secret storage.

### Recommended local shape

Do not make the entire virtual environment a Git repository and do not commit
`bot-env`. Virtual environments contain generated wrappers, bytecode, and many
third-party packages tied to this machine. Instead, make a separate source
repository, for example:

```text
/home/hotrootsoup/nanobot-personal/
├── README.md
├── STRUCTURE.md
├── .gitignore
├── src/
│   └── nanobot/                         snapshot of the complete live package
├── deployment/
│   └── nanobot.service                 non-secret copy for documentation
└── environment/
    ├── nanobot-version.txt             nanobot-ai==0.1.4.post6
    └── requirements-freeze.txt          versions of dependencies in this venv
```

The first priority is an exact, read-only copy of the current live `nanobot`
package into `src/nanobot`, followed immediately by a commit and private push.
Use `/home/hotrootsoup/nanoCodeBak-6-17-2026` as the copy source because it was
verified identical to the live package but is not used by systemd. Also unpack
the three files from `nanobot-deploy.tar.gz` into a `deployment/` directory and
commit them as recovery materials. That creates a rescue point without changing
what systemd imports. The deployed site-packages tree should remain untouched
during this rescue step.

After the rescue commit exists, the cleanest history would be:

1. Preserve the current live snapshot and tag it, for example
   `live-rescue-2026-08-07`.
2. Commit the existing `nanobot-custom.patch`, setup script, and systemd unit as
   the reproducible deployment record.
3. Separately obtain the **exact** original `nanobot-ai==0.1.4.post6` source or
   wheel without installing it, then reconstruct an upstream-baseline commit.
4. Put the personal changes in a later commit so Git shows the true custom diff.
5. Add packaging/deployment support and test in a second virtualenv.
6. Only after verification, deliberately switch systemd to the managed source
   installation. Do not point the live service at a refactored tree casually.

The exact original wheel is not present in the local pip cache. Downloading it
later with `pip download --no-deps` would not update the live environment, but
that should be a separate, explicit action. The rescue snapshot should happen
first because it depends only on files already on this VPS.

### Suggested `.gitignore` policy

The future source repository should at minimum ignore:

```gitignore
__pycache__/
*.py[cod]
.venv/
venv/
bot-env/
.env
.env.*
*.log
config.json
sessions/
media/
```

Review `git status` and `git diff --cached` before every initial push. Search the
staged content for API keys, bot tokens, webhook URLs, passwords, and private
conversation data.

## Backups and operational cautions

1. **Never run pip install/upgrade against this live environment as a cleanup
   step.** Reinstalling `nanobot-ai` can overwrite the in-place changes.
2. **Do not treat the virtualenv as the only copy of the source.** It is a
   deployment artifact and can be replaced by package-management commands.
3. **Back up code and state separately.** Code belongs in Git. The private
   mutable `/home/hotrootsoup/.nanobot` tree needs a private, encrypted backup,
   not a normal source commit.
4. **Remember boot enablement.** If the bot must stay stopped across a VPS
   reboot, the unit must be disabled explicitly. Disabling is reversible and
   does not delete code, but it changes service state and was not done during
   this inventory.
5. **Protect configuration permissions.** `~/.nanobot/config.json` was mode
   `0664` at inspection time. The home directory currently prevents traversal
   by unrelated users, but secret-bearing configuration is better restricted to
   the owner (commonly `0600`) as defense in depth. Make this change only after
   confirming the service still runs as `hotrootsoup`.
6. **Narrow write access later.** The service has useful hardening
   (`NoNewPrivileges=yes` and `ProtectSystem=strict`) but permits writes beneath
   all of `/home/hotrootsoup`. Once backups and tests exist, consider limiting
   writable paths to the actual nanobot data/workspace directories. This may
   constrain tools the assistant intentionally uses, so test before deploying.
7. **Use a staging virtualenv.** Future changes should be installed and tested
   in a separate environment before the systemd unit is changed.
8. **Keep a deployment rollback.** Retain the current rescue tag and a copy of
   the current unit so the known working deployment can be restored.

## Safe diagnostic commands

These commands only inspect the current setup:

```bash
readlink -f /home/hotrootsoup/nanobot/bot-env/lib64
/home/hotrootsoup/nanobot/bot-env/bin/python -c \
  'import nanobot; print(nanobot.__file__)'
/home/hotrootsoup/nanobot/bot-env/bin/python -m pip show nanobot-ai
systemctl status nanobot --no-pager
systemctl cat nanobot
```

Expected key results:

```text
lib64 resolves to /home/hotrootsoup/nanobot/bot-env/lib
nanobot.__file__ is under bot-env/lib/python3.12/site-packages/nanobot
distribution version is 0.1.4.post6
unit file is /etc/systemd/system/nanobot.service
```

## Current boundaries

As of this snapshot, the deployed package, virtualenv, systemd unit, service
enablement, and `~/.nanobot` contents have not been modified. `STRUCTURE.md` is
the only file added.
