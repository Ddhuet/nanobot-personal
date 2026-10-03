# Personal nanobot changes versus upstream v0.3.5

Status: review complete. Updated October 3, 2026 (America/Boise).

## Scope and preserved state

Compare the behavior of the personal fork at `b3a023d` with the latest official
release, `v0.3.5`, before porting any changes. The original upstream base was
`v0.1.4.post6`, commit `c15f63a3207a4288fd228a762793101d22898471`.

Review items 1–5 and 7–9 from the previous customization inventory. Item 6
(Omega logging) and item 10 (deployment/recovery tooling) are deferred. No
customizations have been ported, dependencies installed, or services changed.

The current system service was inspected and reported `inactive/dead`, enabled.
This is an observed state, not a state change made during the review.

### Verified private backup

- Backup directory: `/home/hotrootsoup/.nanobot-backups/2026-10-03_035434`
- Encrypted archive: `workspace-and-config.tar.gz.enc` in that directory.
- Contents: the complete `~/.nanobot/workspace` tree and `~/.nanobot/config.json`.
- Recovery key: `/home/hotrootsoup/.nanobot-backups/recovery-keys/2026-10-03_035434.key`.
- Restore instructions and an archive checksum are stored beside the archive.
- Verification: all 50 regular-file hashes matched the stable source snapshot;
  decrypting the archive reproduced the captured compressed archive exactly.
- Encryption: OpenSSL AES-256-CBC with PBKDF2, 200,000 iterations, and a randomly
  generated key file. Backup directories are owner-only and files are private.

The backup and recovery key are outside Git. This document contains no private
configuration values, conversation contents, or recovery-key contents.

### Clean upstream branch

- Fresh clone: `/home/hotrootsoup/nanobot-upstream`
- Upstream remote: `https://github.com/HKUDS/nanobot.git`
- Local review branch: `upstream-v0.3.5-review`
- Release commit: `1bb712d3488915ca4ed9ccc1a93067ff722f5ab9`
- Official release: <https://github.com/HKUDS/nanobot/releases/tag/v0.3.5>
- Release published September 15, 2026. GitHub's latest-release API confirmed
  this was the latest non-draft, non-prerelease at clone time.

The review uses the release tag, not development `main`. The checkout was clean
after branch creation. The personal fork remains on `main`. No branch was
pushed to GitHub and no upstream code replaced the personal package.

## Port decisions

| Original item | Upstream finding | Port decision so far |
|---|---|---|
| 1. Heartbeat | Replaced dedicated service with a protected cron job; task execution precedes notification evaluation | Personal decision-first/context-aware behavior still differs substantially |
| 2. Cron | Session-bound turns and origin delivery metadata now exist | Keep upstream routing/context; adapt strict validation and failed one-shot retries; silent delivery is an optional policy change |
| 3. Concurrency/routing | Shared session locks, task-local request context, and deferred automation queues exist | Do not transplant old coordinator wholesale; exact priorities and heartbeat origin locking remain different |
| 4. Memory consolidation/reset | Runner-owned compaction, summary checkpoints, separate Dream memory processing, and cursor repair | Keep new architecture; adapt reset protection for idle compaction; keeping 20 raw messages is an optional preference |
| 5. Timestamps | Model replay omits per-message timestamps; UI timestamps are separate | Personal prompt timestamp feature still appears useful if desired |
| 7. Empty/reasoning-only responses | Native reasoning traces and empty-answer retries/finalization exist | Keep retries and streamed-answer tracking; adapt reasoning-details parsing; promoting reasoning into the answer remains optional |
| 8. Discord attachments | Downloads remain outside workspace; read tools explicitly allow the media directory | Accessibility fix appears covered through a different mechanism |
| 9. Configuration | CamelCase/snake_case models and named provider/model presets exist | Generic helpers largely superseded; personal heartbeat fields do not exist |

## 1. Heartbeat

### Personal behavior

The first model call decides whether to run anything, using `HEARTBEAT.md`,
`MEMORY.md`, the most recent external conversation, timestamped history, current
time, and the last user-message time. Decision provider/model are separately
configurable. Phase two starts from a fresh copy of that chat's context; only
explicit message-tool sends reach the user and are mirrored into chat history.
Both decision and execution participate in origin-conversation coordination.
Recent fixes reject invalid decision responses and avoid overriding the
decision provider with the main agent's reasoning setting.

### Upstream behavior found

`nanobot/cli/gateway_runtime.py:622` handles heartbeat as a protected system
cron job. It checks for nonempty tasks under `## Active Tasks`, executes them
through `process_direct(..., session_key="heartbeat")`, suppresses direct
message-tool delivery, then evaluates the final text for notification. The
evaluator fails closed. The selected external chat is a delivery target;
execution uses the independent heartbeat session's history, not a fresh copy
of the selected chat's history. `HeartbeatConfig` has only enabled/interval
fields; no separate decision model/provider.

Target selection excludes archived chats and supports unified-session last
channel metadata. Successful notifications use the gateway's delivery recorder.
Upstream also permits a workspace-local evaluator prompt override.

References: `nanobot/cli/gateway_runtime.py:175`, `:200`, `:622`, `:735`, `:835`;
`nanobot/config/schema.py:332`; `nanobot/utils/evaluator.py`;
`docs/automations.md:104`. Relevant history includes `053722a2`, `dcb37259`,
`63a6d5d0`, `a7a6c26e`.

Implication: preserve upstream's scheduling/delivery infrastructure, but a new
adaptation is required if the personal decision-first behavior, independent
decision model, current-chat context, or explicit-message delivery is wanted.

## 2. Cron

Personal cron uses a silent scratch session seeded from current origin history,
records only message-tool sends to that origin, rejects ambiguous/invalid
schedules, and retries a failed one-shot after 60 seconds.

Upstream persists exact `session_key`, origin channel/chat, and origin metadata.
`run_bound_cron_job()` submits a normal turn to the actual origin session and
defers it while that session is active. This naturally supplies current history
and records the conversation. It also supplies durable per-run records and
legacy delivery migration. Its intended policy is to report scheduled runs;
quiet conditional checks belong to heartbeat.

Source review and isolated probes confirmed these differences:

- `_add_job()` selects the first truthy schedule parameter rather than rejecting
  multiple supplied schedule modes.
- `_validate_schedule_for_add()` validates cron expressions/timezones, but does
  not reject nonpositive interval or past one-shot schedules.
- `_execute_job()` deletes `delete_after_run` one-shots regardless of whether
  their callback succeeded.

The validator accepted zero/negative intervals and a past one-shot. The tool
accepted both an interval and cron expression and selected the interval. A
callback that raised an exception was marked error and its one-shot was still
deleted. Invalid cron expression syntax was correctly rejected. Note that the
tool treats a zero interval as a missing mode; explicit zero acceptance concerns
the service validator. Negative intervals and past times can pass the tool's
schedule selection.

References: `nanobot/cron/bound_runner.py:65`;
`nanobot/agent/tools/cron.py:157`; `nanobot/cron/service.py:72`, `:601`;
`nanobot/templates/agent/cron_reminder.md`; `docs/automations.md:55`.
Relevant history: `dac4e39b`, `b24b5f19`, `73a00804`, `60b7c8cf`, `02ca8f65`.

## 3. Concurrency and routing

Upstream's `RequestContext` uses `ContextVar`; message, spawn, and cron consume
the authoritative request snapshot. It has explicit concurrent-task isolation
and thread-session tests. Bus dispatch and `process_direct()` share a lock for
the same session key. Scheduled automations wait outside live follow-up
injection queues until the origin session is idle.

This covers much of the personal cross-chat routing/session-lock work. It does
not implement the personal user > cron > heartbeat priority queue. User
follow-ups may instead enter the active turn through mid-turn injection.
Heartbeat still locks `heartbeat`, not its delivery target's conversation.

References: `nanobot/agent/tools/context.py`, `nanobot/agent/tools/message.py`;
`nanobot/agent/loop.py:1297`, `:1391`, `:2318`;
`nanobot/agent/automation_turns.py`; `nanobot/agent/cron_turns.py`;
`tests/test_tool_contextvars.py`; `tests/agent/test_loop_tool_context.py`.
Relevant history: `e29c9c39`, `0cc58a80`, `84428136`.

## 4. Memory consolidation and reset races

The personal policy keeps at least 20 recent raw messages by default, aligns to
user-turn boundaries, and consolidates once per threshold crossing. A reset
generation prevents stale in-flight consolidation from updating memory/cursors.
Invalid persisted cursors reset to zero.

Upstream now summarizes accepted context through runner-owned governance,
stores cumulative session-summary checkpoints, archives to `history.jsonl`,
and uses Dream separately for long-term memory edits. Idle compaction is on by
default after 15 minutes. Its compatibility `max_suffix` argument explicitly
no longer retains archived messages. The personal keep-count and threshold
latch are not the current architecture.

Invalid cursor repair exists, with regression tests. `/new` cancels and awaits
active session tasks, saves the cleared session, and archives a detached
snapshot. Background idle compaction is separately scheduled and is not
cancelled by that active-task cancellation method. A source-method probe
reproduced a remaining stale-commit race in this path:

1. `compact_idle_session()` captures the session and waits for an archive result.
2. `/new` clears/saves the session and invalidates its cache entry.
3. A new user message is saved in the fresh session.
4. The old idle-compaction result returns, commits its summary to the old
   session object, and saves that object over the fresh session.

The fresh message was lost and the stale summary was committed in the isolated
probe. `SessionManager.save()` has no session-identity/generation check, and
`compact_idle_session()` does not revalidate the session after its await. Its
consolidation lock is separate from the turn lock; `/new` does not participate
in it. The personal reset protection is therefore still relevant, but needs a
new implementation for this architecture.

Test scope: the probe executed upstream's actual `compact_idle_session()`,
`cmd_new()`, `Session.clear()`, and `Session.commit_summary_checkpoint()` methods
and `AgentLoop._cancel_active_tasks()` with in-memory session storage and a
paused fake archiver. The compaction task was in the separate background-task
set, and the active-task cancellation method did not cancel it. This is a
focused reproduction of the commit ordering, not a full gateway integration
test.

References: `nanobot/agent/context_governance.py`;
`nanobot/agent/memory.py:1072`, `:1220`;
`nanobot/agent/autocompact.py`; `nanobot/command/builtin.py:314`;
`nanobot/session/manager.py:289`;
`tests/session/test_consolidated_offset_clamp.py`.
Relevant history: `d81aa5a4`, `cace42af`, `3c61fef7`, `888d5479` (historical
session-refresh guard; its old consolidation path has since changed).

## 5. Timestamp-aware model context

The personal fork preserves each historical message's timestamp in replay and
adds a timezone-aware inline label. It strips copied labels from output and
persistence, including progress/reasoning paths.

Upstream persists timestamps for storage/UI, but `Session.get_history()` copies
role/content/tool/reasoning fields without timestamp. `ContextBuilder` inserts
that history directly without per-message annotation. No equivalent
`strip_leading_timestamp()` was found. UI timestamp improvements do not provide
the same information to the model. Runtime-context extensions and summary
last-active information are separate from per-message timestamps.

References: `nanobot/session/manager.py:385–443`;
`nanobot/agent/context.py:276`; `nanobot/runtime_context.py`.

## 7. Empty answers and reasoning-only provider responses

Upstream extracts reasoning separately and emits a reasoning trace. Empty
answers trigger retries and then an explicit finalization request. It does not
generally promote reasoning/thinking text into the final answer as the personal
runner does. Provider-specific `reasoning_as_content` exceptions exist.

`OpenAICompatProvider` understands both `reasoning_content` and `reasoning`,
including streaming. Isolated probes confirmed that a response containing only
OpenRouter-style `reasoning_details` produced no normalized reasoning text in
either streamed or nonstreamed parsing. The same probes correctly preserved a
plain `reasoning` field. The personal text/summary extraction remains useful.
The newer loop tracks actually streamed answer content, covering the personal
streaming-delivery correction.

References: `nanobot/agent/runner.py:468`, `:585`;
`nanobot/providers/openai_compat_provider.py:1578`, `:1764`;
`nanobot/agent/loop.py:1665`, `:1728`;
`tests/agent/test_runner_reasoning.py`;
`tests/providers/test_reasoning_content.py`.

## 8. Discord attachments

Upstream Discord still downloads to instance-level `media/discord`, not
`workspace/media/discord`. However, filesystem read resolution now explicitly
adds the instance media directory as an allowed read root when workspace access
is restricted. Writes do not receive that exception. This addresses the reason
for the personal attachment relocation through a different design.

References: `nanobot/channels/discord/runtime.py:730`;
`nanobot/agent/tools/filesystem.py:152`;
`nanobot/agent/tools/path_utils.py:9`.
Related history: `4490f8cf` adds the media exception for WebUI previews too.
An isolated policy probe confirmed media reads outside a restricted workspace
are allowed while writes are rejected. `ExecTool` also accepts the media root
in its application-level path guard (`shell.py:894`). This does not certify
access under every optional operating-system sandbox backend; that is a
deployment-specific integration check, not a reason to copy the old attachment
relocation now.

## 9. Configuration

Upstream's shared config base accepts camelCase/snake_case. Root `modelPresets`
has explicit aliases. Named model presets independently select provider/model
and generation settings, replacing much of the personal override plumbing.

The personal heartbeat `model`/`provider`, Omega flag, and
`consolidationKeepMessages` are absent. A generic alias patch should not be
copied blindly; decide which specific behavior/settings to retain first.

References: `nanobot/config_base.py`; `nanobot/config/schema.py:99`, `:332`,
`:422`; `nanobot/providers/factory.py`.

## Validation, limits, and recommended next work

- Compared source behavior and searched relevant post-v0.1.4.post6 history.
- Did not import or run the new gateway against the real workspace/config.
- The preserved venv lacks pytest and some new dependencies (including
  filelock). No packages were installed into it.
- Ran isolated probes against actual upstream method definitions extracted
  through Python AST. All ten observations completed and assertions passed.
  These avoid importing the new package or touching the real config, workspace,
  session store, or service.
- Probe script: `/tmp/nanobot-v035-behavior-probes.py`. Run with
  `/home/hotrootsoup/nanobot/bot-env/bin/python -B /tmp/nanobot-v035-behavior-probes.py`.
  It reads the clean upstream checkout and writes only transient media-policy
  fixtures beneath `/tmp`.
- This does not establish that upstream's full test suite passes in this old
  environment. Integration tests need an isolated environment if authorized
  later.
- Final verification: upstream checkout has no tracked/untracked changes and
  its tree equals `v0.3.5`. The personal checkout remains on `main`; its only
  addition is this review document. Package source, deployment files, and
  environment pins are unchanged. All 50 backed-up regular files still match
  the source at review completion; archive/key permissions and the saved
  encrypted-archive checksum were verified again.

Suggested port order, if implementation is requested in a subsequent task:

1. Protect background compaction commits against reset/replaced sessions.
2. Add strict cron schedule validation and successful-only one-shot deletion
   with an explicit retry policy.
3. Adapt personal heartbeat decision/context/model behavior to upstream's
   protected system-job mechanism and new request/turn APIs.
4. Restore per-message timestamps and matching output cleanup if desired.
5. Add OpenRouter reasoning-details normalization; separately decide whether
   reasoning becomes a final answer or remains a visible reasoning trace.

Keep already-covered task-local routing, session-bound cron context, cursor
repair, streamed-answer tracking, Discord readability, and generic model
preset/config handling in their upstream form. Strict user > cron > heartbeat
ordering and retention of at least 20 raw messages remain personal policies;
their absence alone is not proof of a bug in the newer runtime.

Before future deployment, review upstream's release migration notes: session
storage moves to the config directory's `sessions/<workspace-id>/` tree and
the first upgraded startup performs migration. The new memory format also
uses `history.jsonl` and Dream. This investigation has not invoked those
migrations or tested them on the private instance data.

## Implementation follow-up (2026-10-03)

The review above records the earlier investigation. The owner subsequently chose a smaller port with different heartbeat policy; the earlier suggested protected heartbeat/evaluator/model behavior is not the implementation plan.

The completed implementation is on local branch `codex/personal-v0.3.5` in `/home/hotrootsoup/nanobot-v035-personal`, based directly on the official release commit. Detailed design, cron parameters, validation evidence, migration behavior and upgrade/recovery instructions are in [personal-upgrade.md](/home/hotrootsoup/nanobot-v035-personal/docs/personal-upgrade.md).

Final decisions:

- Items 1–3: ordinary editable/deletable heartbeat cron; independent latest conversation/task/no-history context and explicit/last-active message destinations; every cron requires the message tool for visible output. Destination sends are recorded once in that conversation. No evaluator or heartbeat-specific model.
- Item 4: upstream memory architecture, with duplicate compactions discarded and stale reset/replacement checkpoints rejected.
- Item 5: personal timestamp labels and copied-label cleanup ported.
- Items 7–9: upstream reasoning recovery, Discord behavior and config schema retained. Only the obsolete personal Omega logging toggle is removed by a future upgrade after verified backup.
- Items 6 and 10 remain excluded from this port.

Verification completed: 1,989 backend tests plus 13 focused cron/gateway CLI tests passed; changed runtime modules passed strict type/lint checks. Real disposable deployments completed twice, and rollback tests restored exact original state and executable after a simulated failure. The private-copy migration preview retained all 34 current sessions with zero conflicts and preserved legacy memory; all 53 original instance files/links were unchanged afterward.

This is not a live upgrade. The old installed distribution is still `0.1.4.post6`; service remains inactive/dead and enabled. Original fork source and scripts, live environment, config and workspace are untouched. The clean upstream review checkout is unchanged. Implementation changes are uncommitted for review, with no push.

## Publication and setup follow-up

The implementation and its review/design documentation are published on [codex/personal-v0.3.5](https://github.com/Ddhuet/nanobot-personal/tree/codex/personal-v0.3.5) in the owner's private repository. The pinned main branch is unchanged. Setup now supports `--install-systemd` for a restored new-VPS instance, preserving existing units and installing a missing unit without starting/enabling it. The 14 setup/upgrade tests and native systemd unit validation passed. No live deployment was performed.
