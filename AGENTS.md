# Instructions for AI agents working in this repository

## Scope and purpose

This is the private source-of-truth repository for the owner's customized
personal nanobot. It is intentionally based on the older pinned distribution
`nanobot-ai==0.1.4.post6`; preserving that customized version is a requirement,
not technical debt to clean up without permission.

The repository root is normally `/home/hotrootsoup/nanobot-personal`.
`STRUCTURE.md` records the original VPS inventory and `README.md` gives the
human workflow. Read both before changing deployment behavior.

`src/nanobot/templates/AGENTS.md` is a template shipped as part of nanobot's
runtime package. It is not the operational instruction file for this repository.

## Non-negotiable safety rules

1. Do not upgrade nanobot, replace it with current upstream, pull upstream code
   over it, or run an unpinned `pip install nanobot-ai`.
2. Do not run `setup.sh`, `deploy.sh`, or
   `deployment/setup-original.sh` unless the user explicitly asks for an
   installation/deployment in the current task.
3. In particular, `deployment/setup-original.sh` is an archival recovery
   artifact. It upgrades pip, installs into a venv, and applies a historical
   patch directly to site-packages. Never run it merely to test or inspect it.
4. Do not start, stop, restart, enable, disable, or reload the nanobot systemd
   service unless the user explicitly authorizes that state change. Read-only
   `systemctl status/show/cat` checks are fine.
5. Do not edit the deployed site-packages copy directly. Make source changes
   under `src/nanobot`, review and commit them, then deploy only when requested.
6. Do not commit anything from `~/.nanobot`. It contains private
   configuration, credentials, conversations, memory, cron state, logs, and
   media. It is backed up separately.
7. Never commit API keys, bot tokens, passwords, private keys, webhook secrets,
   session data, or generated `.env`/configuration files.
8. Preserve unrelated user changes. Do not force-push, reset destructively, or
   rewrite known-good Git history unless the user explicitly requests it.

## What is source and what is deployed

The Git-managed source is:

```text
/home/hotrootsoup/nanobot-personal/src/nanobot
```

On the original VPS, systemd executes:

```text
/home/hotrootsoup/nanobot/bot-env/bin/nanobot gateway
```

That command imports the deployed copy from:

```text
/home/hotrootsoup/nanobot/bot-env/lib/python3.12/site-packages/nanobot
```

`lib64` is only a symlink to `lib`. The virtualenv path is a deployment
target, not a Git working tree. Editing Git source does not affect the deployed
bot until an authorized `./deploy.sh` copies it into the virtualenv.

The base interpreter for the preserved installation is Python 3.12, and the
installed distribution metadata remains `nanobot-ai==0.1.4.post6`.

Mutable instance data lives separately under `/home/hotrootsoup/.nanobot`.

## Systemd model

The service is a machine-wide unit at:

```text
/etc/systemd/system/nanobot.service
```

It is controlled with sudo because it belongs to the system systemd manager,
but the process itself runs as user/group `hotrootsoup`. The unit uses:

```text
WorkingDirectory=/home/hotrootsoup/nanobot
ExecStart=/home/hotrootsoup/nanobot/bot-env/bin/nanobot gateway
```

Always inspect current state instead of assuming it:

```bash
systemctl status nanobot --no-pager
systemctl show nanobot -p ActiveState -p SubState -p UnitFileState
```

`deploy.sh` refuses to replace code while the service is active.

## Scripts and when they are used

- `deploy.sh`: routine code deployment to an already-created, exact-version
  virtualenv. It verifies the version, refuses an active service, compiles a
  staged copy, saves the old package under `~/nanobot/source-backups`, and swaps
  in `src/nanobot`. It does not use pip or control systemd.
- `setup.sh`: first installation/recovery on a new machine. It creates a
  missing venv, installs the exact dependency snapshot, calls `deploy.sh`, and
  can optionally install (but not enable/start) a portable systemd unit.
- `scripts/check-live-diff.sh`: read-only comparison between Git source and the
  deployed package.
- `deployment/setup-original.sh`: dangerous historical artifact; preserve it
  but do not run it.
- `deployment/nanobot-custom.patch`: historical patch proving the original
  customizations against the pinned wheel.
- `deployment/nanobot.service`: exact original VPS unit.
- `deployment/nanobot.service.template`: portable unit used by `setup.sh`.

## Normal development workflow

For ordinary implementation work:

1. Check `git status` and read relevant source under `src/nanobot`.
2. Modify only the repository source and documentation required by the task.
3. Use the existing venv interpreter for checks while forcing imports from the
   repository:

   ```bash
   cd /home/hotrootsoup/nanobot-personal
   PYTHONPATH="$PWD/src" /home/hotrootsoup/nanobot/bot-env/bin/python -c \
     'import nanobot; print(nanobot.__file__)'
   ```

4. Run targeted tests or static checks appropriate to the change. Do not run an
   installer as a test. For shell changes, at least run `bash -n`.
5. Inspect `git diff`, scan newly added material for secrets, and make a scoped
   commit if the user asked for a commit.
6. Do not deploy merely because tests pass. Deployment is a separate, explicit
   operation.

The exact source snapshot contains some pre-existing whitespace and historical
quirks. Avoid drive-by formatting because it obscures the owner's custom diff.

## Authorized deployment workflow

Only when the user asks to deploy:

```bash
sudo systemctl stop nanobot
cd /home/hotrootsoup/nanobot-personal
./scripts/check-live-diff.sh || true
./deploy.sh
sudo systemctl start nanobot
sudo systemctl status nanobot --no-pager
```

The operator may stop/start the service themselves. Confirm it is inactive
before deployment. `deploy.sh` creates a rollback copy but that does not replace
reviewing the Git diff first.

For a new VPS, clone this private repository, restore GitHub authentication,
inspect `AGENTS.md` and the scripts, then explicitly run:

```bash
./setup.sh --install-systemd
```

The setup script deliberately does not enable or start the service.

## Git and recovery facts

- Remote: `git@github.com:Ddhuet/nanobot-personal.git`
- Primary branch: `main`
- Initial verified rescue commit: `0bdaf82`
- Known-good rescue tag: `live-rescue-2026-08-07`
- The original VPS repository has a repository-specific SSH deploy key configured
  through local `core.sshCommand`. Do not overwrite unrelated SSH keys.
- Before the first push, the complete source and high-confidence credential
  patterns were checked; future additions still require review.

When in doubt, preserve the pinned source, leave the service and live venv alone,
and ask the user before taking deployment or package-management action.
