# Personal nanobot upgrade: v0.3.5

Baseline: official upstream release `v0.3.5`, commit `1bb712d3488915ca4ed9ccc1a93067ff722f5ab9`.
Implementation branch: `codex/personal-v0.3.5`.
Checkout: `/home/hotrootsoup/nanobot-v035-personal`.
The original pinned personal fork, pristine upstream review checkout, live environment, service, and instance data are separate. Implementation is not a live deployment.

## Cron behavior

Every agent cron run is silent unless it calls `message`: no automatic final answer, progress, reasoning, streaming, completion, or error delivery. This applies to heartbeat and cron-launched background continuations. A normal immediate user turn still replies automatically. Dream remains upstream's internal memory job.

History and message routing are independent persisted selectors:

| Tool parameter | Value | Meaning at execution |
| --- | --- | --- |
| `context_mode` | `conversation` | Latest history of `context_session_key`, originating conversation by default |
| `context_mode` | `last_active` | Latest active user conversation |
| `context_mode` | `task` | Persistent `cron:<job-id>` history containing this task's earlier runs |
| `context_mode` | `none` | Fresh execution, no prior conversation or long-term memory; ordinary system/tool instructions remain |
| `context_session_key` | Exact known key or `@handle` | History source when mode is `conversation` |
| `output_session_key` | Exact known key, `@handle`, or `last_active` | Independent default destination for the message tool |

Both defaults are the originating conversation. Last-active selection considers genuine user activity on enabled, non-archived external channels; a proactive send does not make its destination the latest user chat. Each run resolves the selectors again. When history selection waits for an active turn, it resolves both selectors again after acquiring that conversation's lock, then reads its current history and summary. No history is captured when the job is created. Topic/thread addresses come from the selected conversation's routing metadata.

A run's instructions identify its execution session, history source, destination session, channel and chat ID. Its history is reference material, not a fresh user request. Omitting `message` target arguments uses that default destination. Explicit sends are recorded once as assistant messages in the actual destination conversation, including media references. Internal final text remains only in task history/audit records as applicable. Borrowed-history and `none` execution sessions are not persisted.

The cron tool supports `add`, `update`, `list`, and `remove`. Updates can change instructions, name, schedule, context, destination and `enabled`; omitted update fields stay unchanged. List includes disabled jobs and their instructions/selectors. Agent execution may update/remove jobs; upstream's guard against adding nested jobs is retained.

Example: read current developer DMs and report in a team conversation:

```json
{
  "action": "add",
  "name": "daily-project-summary",
  "message": "Summarize the user's recent project work. Use message for a useful team update; otherwise stay quiet.",
  "cron_expr": "0 17 * * *",
  "tz": "America/Boise",
  "context_mode": "conversation",
  "context_session_key": "discord:KNOWN_DM_ID",
  "output_session_key": "discord:KNOWN_TEAM_ID"
}
```

Keys above are placeholders: use real known session keys or handles. `output_session_key` sets a default, not an access restriction. The agent can deliberately choose another target with `message`.

Validation rejects zero, negative, fractional or boolean intervals; multiple/no schedules; malformed expressions/timezones; and past one-shot times. Failed/skipped one-shots remain available for a retry after 60 seconds; successful delete-after-run jobs are deleted. Editing non-schedule fields preserves the deadline.

## Heartbeat

Heartbeat is an ordinary editable/deletable agent cron job. Its initial instructions warn that it drives important periodic work, tell it to read `HEARTBEAT.md`, and require the message tool for actionable updates. Both selectors initially use `last_active`. There is no evaluator, protected deletion, dedicated heartbeat model, or special execution path.

Gateway heartbeat configuration provides the initial enable/interval settings only. Once seeded, the job owns its instructions, schedule, enabled state and selectors. A private seed marker beside `jobs.json` preserves intentional deletion across restarts; edits and disabling are preserved too. Older upstream protected heartbeat records are converted at startup. The seed marker does not prevent any tool operation.

## Session discovery

Upstream already exposes `list_sessions` with known session handles, plus `search_sessions` and `read_session`. These discover persisted conversations, not every possible platform channel or user. A never-seen destination needs a supplied ID/reference and a known conversation before selecting it as a cron session target. No new channel catalog is implemented.

## Memory, timestamps, and retained upstream behavior

Upstream memory archives, Dream, compaction checkpoints, retention and reasoning recovery remain in place. Concurrent compaction requests share a per-session lock; a second request is discarded immediately rather than queued. Idle compaction checks the cached session identity and its reset generation before writing a checkpoint, so a concurrent clear/reset/replacement cannot resurrect old messages. Concurrent appends remain after the captured archive boundary.

Historical messages receive the personal fork's compact `[YYYY-MM-DD HH:MM]` label only in the model-facing copy. Synthetic compaction checkpoints retain upstream's required structure without a label. Copied leading labels are removed from final answers, streaming/progress/reasoning output, persisted assistant text and message-tool sends, including invisible prefix characters. Ordinary bracketed text is preserved. Stored user content is not rewritten merely to display timestamps.

Discord delivery/attachment handling, configuration schema, provider behavior and reasoning-only recovery use upstream. The personal logging toggle, custom heartbeat model and old memory-tail policy are not ported.

## Quick start on this machine

Only Discord is enabled on this instance, so the unused WebUI can be omitted for a faster upgrade:

```bash
cd ~/nanobot-v035-personal &&
systemctl stop nanobot &&
./deploy.sh --skip-webui-build &&
systemctl start nanobot &&
systemctl status nanobot --no-pager
```

The deploy step backs up config/workspace and migrates sessions automatically. It must finish successfully before the start command runs. Run these in your terminal so systemd authentication can be handled there.

On a new VPS, restore the private config/workspace and run `./setup.sh --install-systemd`; enable/start the unit deliberately afterward. Setup can create a missing environment; deploy requires an existing installation. Unit installation prefers graphical polkit (`pkexec`) when available, with terminal sudo authentication as a fallback. No existing unit is overwritten.

GitHub branch: [codex/personal-v0.3.5](https://github.com/Ddhuet/nanobot-personal/tree/codex/personal-v0.3.5).

## Setup and deployment

`setup.sh` and `deploy.sh` invoke `scripts/personal_upgrade.py`. Setup creates a missing environment for a restored/onboarded instance; deploy requires an existing one. `setup.sh --install-systemd` optionally installs a missing system service unit, preserving existing units, then reloads systemd unit definitions. Neither script starts/stops/restarts or enables a service. The existing service's fixed `bot-env/bin/nanobot` path continues to work through the new environment symlink.

Read-only preflight:

```bash
cd /home/hotrootsoup/nanobot-v035-personal
./deploy.sh --check
```

For an authorized actual upgrade, deliberately leave the service inactive and run `./deploy.sh`; review the resulting manifests before starting it separately. Optional path flags are shown by `./deploy.sh --help`. Use `--service none` only for an installation without a systemd service. `--skip-webui-build` deliberately creates a headless package; normal installation retains upstream's WebUI build.

Upgrade sequence:

1. Refuse an active/unknown service and unsafe migration paths. Hold an exclusive upgrade lock.
2. Build the local customized release in a new permanent environment under `~/nanobot/release-environments`; install API and configured-channel dependencies using upstream's installer. Existing environment packages are not modified.
3. Copy and hash-verify instance/workspace data, validate the candidate config, and migrate that private copy. Rebind copied namespace markers so repeated-upgrade previews exercise the correct sessions.
4. Back up the entire instance directory and any external workspace to a verified encrypted archive outside Git. AES-256-CBC/PBKDF2 with 200,000 iterations; separate recovery key, private directory/file permissions, archive and per-file checksums. Remove temporary plaintext archives.
5. Check the service and data hashes again. Drop only obsolete root `omegaLogging`/`omega_logging` settings, which upstream rejects; retain all other supplied config fields and credentials. Migrate actual state only after backup verification.
6. Retain the old environment or symlink target, switch `bot-env` to the new permanent environment, and validate its installed version and config. Save installed requirements and deployment/migration reports.
7. On a migration/switch failure, restore both executable and verified state while the service is still inactive. Preserve failed state privately for inspection. If recovery cannot complete or the service becomes active, leave explicit recovery instructions rather than racing a running process.

The backup includes `state.tar.gz.enc`, `state-manifest.json`, `RESTORE.md`, `deployment.json`, and migration reports; the recovery key lives in the sibling `recovery-keys` directory. Keep both archive and key. Old environments are retained, never automatically pruned. Nothing from the instance or backup is committed.

## Storage migration

`scripts/migrate_personal_state.py` uses upstream's session/memory migrations rather than inventing a conversation format. Workspace `sessions/*.jsonl` moves to a workspace namespace below the config directory's `sessions/`. Byte-identical originals or conflict archives must survive; unresolved source files stop the upgrade. Legacy global root JSONL files are imported through verified copies, with originals retained. A per-namespace import record prevents repeated upgrades from importing the same old global history again. Repeated initialization must use the same namespace.

Legacy `memory/HISTORY.md` converts to upstream `history.jsonl`; its original content must survive in the retained backup. Config and the full instance backup cover both old and new storage locations. The migration helper itself writes state: run it only on an isolated copy or after verified backup. `--check` is the read-only operation.

## Validation evidence

The detailed final test results are recorded below after the regression run. No real provider calls, platform sends, service changes or live deployment are performed by these checks.

Current-data preview: a private, hash-verified copy of all 53 original instance files/links passed candidate config validation. All 34 workspace sessions migrated with zero conflicts; namespace initialization was stable. Upstream converted legacy memory into 394 archive entries and preserved the original. Original hashes were unchanged afterward; temporary copied private data was removed.

A disposable installation exercised real package/environment building, encryption/decryption verification, migration, the executable symlink switch and post-switch validation. Tests also simulate post-switch failure and require exact old state/executable restoration. These use synthetic data and explicit `--service none`.

### Final results (2026-10-03)

- 1,989 backend regression tests passed: agent, cron, session, message tools, request context, gateway runtime, personal upgrade, WebUI session automations, and compact command tests.
- 13 focused cron/gateway/local-trigger CLI tests passed. Removed tests for the deleted heartbeat parser/protected execution path were replaced by ordinary-heartbeat and generalized routing tests.
- Strict basedpyright checks passed on every changed runtime module; lint checks, shell syntax checks and `git diff --check` passed.
- Dedicated regression cases cover all four context modes, fresh history after lock waits, last-active changes during waits, independent thread destinations, one-time message persistence, silent detached continuations, editable/deleted heartbeat persistence, invalid schedule rejection, failed one-shot retry and successful deletion, duplicate compaction dropping, stale checkpoint rejection, and copied timestamp cleanup.
- Eight upgrade tests passed, including preserving both workspace/global sessions, repeated migration, verified encrypted backup, unsafe symlink rejection, active-service refusal, and a simulated post-switch failure that restores exact instance bytes and the previous executable.
- A real disposable wheel installation/upgrade completed twice. The second deployment retained one canonical session, the same workspace namespace, the old environment symlink target and the encrypted backup. Both deployment manifests finished with `stage=complete`.
- The final private-copy preview retained all 34 current sessions, with zero conflicts and stable repeated initialization. All 53 original files/links matched their original hashes afterward.
- Original deployment is still `nanobot-ai==0.1.4.post6`. Service remains inactive/dead and enabled; no service operations were performed.

The full unrelated frontend/CLI suite is not claimed as passing: broader CLI tests invoked upstream WebUI dependency downloads and were stopped. Backend tests and focused changed CLI paths completed separately. A Dream CLI test that failed in the combined run passed when run alone, consistent with test-state interference; Dream implementation is unchanged. No real LLM/provider calls or platform delivery were exercised.

### Review and recovery locations

- Customized release checkout: `/home/hotrootsoup/nanobot-v035-personal`, branch `codex/personal-v0.3.5`. The implementation is committed and published on the GitHub branch linked below. No live deployment was performed.
- Pristine release checkout: `/home/hotrootsoup/nanobot-upstream`, branch `upstream-v0.3.5-review`, unchanged from the release commit.
- Preserved pinned fork: `/home/hotrootsoup/nanobot-personal`; its package source and deployment scripts are unchanged.
- Initial verified workspace/config backup: `/home/hotrootsoup/.nanobot-backups/2026-10-03_035434/workspace-and-config.tar.gz.enc`.
- Initial recovery instructions: `/home/hotrootsoup/.nanobot-backups/2026-10-03_035434/RESTORE.txt`; retain the separately stored recovery key referenced there.

The initial backup is retained even though the live upgrade has not occurred. A future deploy creates another full verified state backup immediately before actual migration. Session bootstrap files and user-installed skills are preserved; bundled template edits do not overwrite an existing workspace's customized Markdown files.

### Setup/systemd follow-up

Restored the optional `--install-systemd` setup behavior after the owner pointed out the omission. It creates a portable system unit with the current user/group, explicit config, executable and writable paths; preserves an existing local/vendor unit; reloads unit definitions only after installing a new one; and never enables or starts it. Deploy cannot use this option. Existing-installation upgrade/migration behavior is unchanged. Unit tests use mocked privileged calls and disposable paths; no host service/unit was changed while implementing this correction.

Follow-up verification: all 14 upgrade/setup tests passed; the generated unit passed `systemd-analyze verify`. Shell syntax, lint and diff checks passed. Host service and deployed environment remain unchanged.
