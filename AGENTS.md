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

# Codebase Structure

This section maps the source package at `src/nanobot`. It is intended to let a
new developer find the right code quickly without first reading the entire
package. The source is a customized, pinned `nanobot-ai==0.1.4.post6` snapshot;
describe and extend the behavior that is present here rather than assuming a
newer upstream layout.

## Architectural overview

The gateway is an asynchronous message broker around one shared agent runtime:

```text
chat platform
  -> BaseChannel._handle_message()
  -> MessageBus.inbound
  -> AgentLoop.run() / _dispatch()
  -> session + prompt context + AgentRunner
  -> LLMProvider
  -> zero or more tool calls
  -> OutboundMessage
  -> MessageBus.outbound
  -> ChannelManager
  -> platform adapter
```

`nanobot.cli.commands.gateway()` is the composition root. It loads configuration,
creates the provider, bus, session manager, cron service, `AgentLoop`, channel
manager, and heartbeat service, wires their callbacks together, then starts
their long-running tasks. Most cross-subsystem changes therefore touch both the
owning module and `cli/commands.py`.

The main architectural boundaries are:

- `agent/`: prompt construction, LLM/tool iteration, memory, skills, and
  background subagents.
- `bus/`: in-process inbound and outbound queues plus their message types.
- `channels/`: platform adapters and outbound delivery policy.
- `providers/`: the normalized LLM interface and concrete API backends.
- `session/`: workspace-scoped JSONL conversation persistence.
- `cron/` and `heartbeat/`: two different background execution systems.
- `config/`: Pydantic schema, JSON loading, provider selection, and runtime
  path rules.
- `cli/`: process entry points and the wiring between all of the above.
- `bridge/`: the separate Node/Baileys process used only by WhatsApp.
- `templates/` and `skills/`: files shipped into or exposed to the mutable
  assistant workspace.

## Source, workspace, and instance data

Do not confuse the package source with the assistant's mutable workspace:

- `src/nanobot` is program source and bundled assets.
- The configured workspace (default `~/.nanobot/workspace`) holds bootstrap
  files, `sessions/*.jsonl`, `memory/MEMORY.md`, `memory/HISTORY.md`,
  `cron/jobs.json`, and user-installed `skills/`.
- The directory containing the active config file holds instance data such as
  downloaded `media/`, logs, and legacy cron data. Some shared compatibility
  paths still live directly below `~/.nanobot`.
- `sync_workspace_templates()` only creates missing workspace files. Editing a
  bundled template does not alter an existing workspace copy.

Session keys are normally `channel:chat_id`. Telegram topics, Slack threads,
and other adapters can provide a more specific `session_key_override`. Cron
uses `cron:<job-id>` and heartbeat execution uses the special `heartbeat`
session.

## Entrypoints and process wiring

- `src/nanobot/__init__.py` defines the pinned package version and logo.
- `src/nanobot/__main__.py` makes `python -m nanobot` invoke the Typer app in
  `cli.commands`.
- `src/nanobot/cli/commands.py` is the main CLI and runtime composition root.
  Its important areas are:
  - terminal/Rich rendering helpers for interactive mode;
  - `onboard()`, which loads/saves config, merges discovered channel defaults,
    creates the workspace, and syncs templates;
  - `_make_provider()`, which resolves registry metadata and instantiates the
    selected provider backend;
  - `gateway()`, including all cron and heartbeat callback wiring;
  - `agent()`, for one-shot or interactive local use;
  - channel status/login, plugin listing, overall status, and provider OAuth
    commands.
- `src/nanobot/cli/onboard.py` is the generic interactive Pydantic configuration
  editor. It discovers providers and channels, recursively edits a draft,
  masks sensitive values, and supports save/discard navigation.
- `src/nanobot/cli/stream.py` contains `ThinkingSpinner` and `StreamRenderer`,
  which render incremental local CLI output and pause/resume between tool-call
  rounds.
- `src/nanobot/cli/models.py` contains the model lookup API, but its database
  functions are intentionally stubbed in this snapshot. Only token-count
  formatting is active, so onboarding autocomplete/context recommendations
  currently return no data.
- `src/nanobot/cli/__init__.py` is a package marker.

Gateway startup, in order, is roughly:

1. Load config, initialize optional omega logging, and sync missing workspace
   templates.
2. Create `MessageBus`, provider, and workspace `SessionManager`.
3. Create workspace `CronService` and pass it into `AgentLoop` so the `cron`
   tool is registered.
4. Attach the gateway's cron callback.
5. Discover and construct enabled channels through `ChannelManager`.
6. Build heartbeat target/history/execution callbacks and construct
   `HeartbeatService`.
7. Start cron and heartbeat, then run the agent and channels concurrently.
8. On shutdown, drain/close MCP, stop the recurring services and agent, and
   stop all channels.

## Agent core

- `src/nanobot/agent/loop.py` contains `AgentLoop`, the product-level processing
  engine. It registers default tools, lazily connects MCP servers, consumes
  inbound messages, handles priority slash commands, serializes work per
  session while allowing cross-session concurrency, constructs prompt/history,
  invokes `AgentRunner`, persists the new turn, and publishes progress,
  streaming segments, or the final response. `process_direct()` is the entry
  used by the CLI, cron, and heartbeat. Look here for routing context,
  concurrency, persistence sanitization, and normal-turn policy.
- `src/nanobot/agent/runner.py` is the reusable provider/tool loop without
  channel or session concerns. `AgentRunSpec` configures one run;
  `AgentRunner.run()` calls the provider until it gets a final response or hits
  an error/iteration limit; `_execute_tools()` can run one model turn's tool
  calls concurrently. `AgentRunResult` returns messages, usage, tools, stop
  reason, error, and compact tool events.
- `src/nanobot/agent/hook.py` defines `AgentHookContext` and the no-op
  `AgentHook` lifecycle used by the main loop and subagents: before iteration,
  stream delta/end, before tools, after iteration, and final-content cleanup.
- `src/nanobot/agent/context.py` contains `ContextBuilder`. It builds the system
  prompt from runtime identity, workspace `AGENTS.md`, `SOUL.md`, `USER.md`,
  `TOOLS.md`, long-term memory, always-on skills, and the available-skills
  summary. It adds untrusted time/channel metadata to the current message,
  timestamps history, and converts local image attachments to native base64
  blocks. `HEARTBEAT.md` is deliberately not a normal bootstrap file.
- `src/nanobot/agent/memory.py` implements the two-layer memory system.
  `MemoryStore` reads/writes `MEMORY.md`, appends searchable entries to
  `HISTORY.md`, and asks an LLM to call the virtual `save_memory` tool.
  `MemoryConsolidator` estimates the complete prompt, chooses safe user-turn
  boundaries, locks per session, archives old chunks, and advances
  `Session.last_consolidated`. After three failed consolidations it raw-archives
  the messages rather than silently losing them.
- `src/nanobot/agent/skills.py` contains `SkillsLoader`. Workspace skills take
  precedence over bundled skills of the same name. It parses simple frontmatter,
  checks required binaries/environment variables, loads `always` skills in
  full, and exposes all other skills as a progressive-loading XML summary.
- `src/nanobot/agent/subagent.py` contains `SubagentManager`. It runs independent
  background `AgentRunner` tasks with filesystem, shell, and web tools but no
  message/spawn tool, tracks them by originating session for `/stop`, and
  returns results to the main agent as an inbound `system` message.
- `src/nanobot/agent/__init__.py` re-exports the common agent classes.

### Tools

- `agent/tools/base.py`: abstract `Tool` contract, OpenAI-style schema
  generation, parameter casting, and recursive JSON-schema validation.
- `agent/tools/registry.py`: `ToolRegistry` registration, definitions, lookup,
  validated execution, and model-facing error hints.
- `agent/tools/filesystem.py`: `ReadFileTool`, `WriteFileTool`, `EditFileTool`,
  and `ListDirTool`; includes path resolution, optional workspace restriction,
  paged text reads, native image reads, exact-string edits, and directory
  listings.
- `agent/tools/shell.py`: `ExecTool`; async subprocess execution, timeout/output
  limits, dangerous-command and internal-URL guards, optional workspace
  restriction, and configurable `PATH` extension.
- `agent/tools/web.py`: `WebSearchTool` (Brave, Tavily, DuckDuckGo, SearXNG, or
  Jina) and `WebFetchTool` (HTTP fetch, redirect/SSRF checks, HTML-to-markdown or
  text extraction, and native image results).
- `agent/tools/message.py`: `MessageTool`; publishes an `OutboundMessage`,
  carries media paths, and records whether the current turn already sent its
  reply so `AgentLoop` can suppress a duplicate final response.
- `agent/tools/spawn.py`: the model-facing wrapper around
  `SubagentManager.spawn()`; captures the current channel/chat/session context.
- `agent/tools/cron.py`: model-facing add/list/remove operations over
  `CronService`; converts intervals, cron expressions, and ISO timestamps,
  defaults timezones, binds delivery to the current chat, and blocks creation
  of nested jobs from inside a cron execution.
- `agent/tools/mcp.py`: connects configured stdio, SSE, or streamable-HTTP MCP
  servers; filters enabled tools; normalizes nullable JSON Schema; wraps each
  remote tool as `mcp_<server>_<tool>` with a timeout; and registers it in the
  normal tool registry.
- `agent/tools/__init__.py` re-exports the base tool and registry.

Default tools are registered in `AgentLoop._register_default_tools()`. File,
web, message, and spawn tools are always present; exec depends on config; cron
depends on a supplied service; MCP tools are added lazily on first connection.
Subagents intentionally receive a smaller registry.

## Messages, commands, and sessions

- `src/nanobot/bus/events.py` defines `InboundMessage` and `OutboundMessage`.
  Inbound events carry source identity, content, media, metadata, and optional
  session-key override; outbound events carry destination, reply target, media,
  and control metadata.
- `src/nanobot/bus/queue.py` defines `MessageBus`, two unbounded
  `asyncio.Queue`s with publish/consume and size helpers.
- `src/nanobot/bus/__init__.py` re-exports those bus types.
- `src/nanobot/command/router.py` defines `CommandContext` and `CommandRouter`.
  Dispatch order is priority exact matches, normal exact matches, longest
  prefixes, then interceptors. Priority commands run before the per-session lock.
- `src/nanobot/command/builtin.py` implements `/stop`, `/restart`, `/status`,
  `/new`, and `/help`. `/stop` cancels active turns and session subagents;
  `/new` clears the session and archives the unconsolidated snapshot in the
  background; `/restart` replaces the process with `os.execv`.
- `src/nanobot/command/__init__.py` re-exports command types and registration.
- `src/nanobot/session/manager.py` defines `Session` and `SessionManager`.
  Sessions are cached in memory and rewritten as readable JSONL: one metadata
  line followed by messages. `get_history()` returns only unconsolidated
  messages and avoids starting with orphaned tool results. The manager stores
  them under `<workspace>/sessions`, migrates legacy global sessions on demand,
  and can list sessions by metadata without loading every conversation.
- `src/nanobot/session/__init__.py` re-exports session types.

`AgentLoop._save_turn()` is the persistence boundary: it removes injected
runtime context, replaces inline image data with placeholders, truncates large
tool results, skips empty assistant messages, strips accidental response
timestamps, and timestamps persisted entries.

## Channels

### Shared channel layer

- `src/nanobot/channels/base.py` defines `BaseChannel`: lifecycle (`start`,
  `stop`), outbound `send`, optional `login` and `send_delta`, Groq audio
  transcription, ACL checks, streaming capability detection, and the common
  `_handle_message()` path into the bus. Empty `allowFrom` denies all; `"*"`
  permits all.
- `src/nanobot/channels/registry.py` discovers built-in modules with `pkgutil`
  and external adapters from the `nanobot.channels` entry-point group. Built-ins
  win name collisions.
- `src/nanobot/channels/manager.py` instantiates enabled discovered channels,
  injects the Groq transcription key, starts/stops them, and owns the outbound
  dispatcher. It filters progress/tool hints, coalesces queued stream deltas,
  routes streaming control messages to `send_delta()`, and retries ordinary
  sends with bounded exponential backoff.
- `src/nanobot/channels/__init__.py` re-exports `BaseChannel` and
  `ChannelManager`.

### Platform adapters

Each adapter owns a Pydantic config class, a `BaseChannel` subclass, platform
protocol details, inbound normalization, and outbound formatting:

- `channels/telegram.py`: `python-telegram-bot` long polling; private/group
  policy, mentions and replies, forum-topic session keys, reply context, media
  groups/downloads/transcription, typing/reactions, Telegram HTML rendering,
  attachments, and edit-based streaming keyed by `_stream_id`.
- `channels/discord.py`: direct Discord Gateway WebSocket plus REST; heartbeat
  and reconnect, guild mention policy, typing, attachment downloads/uploads,
  replies, rate limits, and 2,000-character splitting.
- `channels/slack.py`: Slack Socket Mode; DM and group policies, mentions,
  reactions, thread-scoped sessions, Slack mrkdwn conversion, thread replies,
  and file uploads.
- `channels/email.py`: consent-gated IMAP polling and SMTP replies; unread-mail
  deduplication, MIME/plain/HTML parsing, optional SPF/DKIM checks, threading
  headers, date-range fetches, and auto-reply policy. Outbound attachments are
  not implemented.
- `channels/dingtalk.py`: optional DingTalk Stream SDK for inbound callbacks and
  HTTP APIs for outbound private/group Markdown and uploaded media. Callback
  processing is detached so SDK acknowledgement remains immediate.
- `channels/feishu.py`: optional Lark SDK WebSocket running in a dedicated
  thread; many Feishu message/card/share types, media, reply context, group
  mentions, reactions, Markdown/post/interactive-card rendering, and CardKit
  streaming.
- `channels/matrix.py`: `matrix-nio` sync loop; direct/group policy, invites,
  mentions, Matrix threads, sanitized formatted HTML, typing keepalive,
  plaintext/encrypted attachments, upload limits, and E2EE store handling.
- `channels/mochat.py`: Socket.IO subscriptions with HTTP polling fallback;
  session/panel discovery, persisted cursors, event deduplication, mention
  policy, delayed group buffering, and separate session/panel send endpoints.
- `channels/qq.py`: Tencent BotPy C2C/group adapter; bounded deduplication,
  chunked attachment downloads, base64 media upload, QQ message types, and
  sequence handling.
- `channels/wecom.py`: optional Enterprise WeChat AI Bot SDK long connection;
  event/frame handlers, media download/decryption, voice transcription, welcome
  messages, and frame-bound streaming replies.
- `channels/weixin.py`: personal WeChat HTTP long polling; QR login and persisted
  account/cursor/context-token state, session pause/backoff, message/media
  normalization, CDN upload/download, and AES-ECB media encryption.
- `channels/whatsapp.py`: Python client for the local Node bridge; bridge login
  setup, authenticated WebSocket reconnect, message deduplication, WhatsApp
  JID/phone ACL handling, group-mention policy, media markers, and text/file
  commands to the bridge.

To add a channel, create one module containing a unique `BaseChannel` subclass
and config model, implement `default_config()`, and rely on registry discovery.
No central class list is required, but onboarding and runtime behavior depend on
the class name/config section agreeing with the module's channel `name`.

## WhatsApp bridge

The bridge is a standalone Node package copied/built into the runtime bridge
directory by the WhatsApp login flow:

- `src/nanobot/bridge/package.json`: pinned Baileys, WebSocket, QR, and logging
  dependencies; Node 20+; build/start scripts.
- `src/nanobot/bridge/tsconfig.json`: compiles TypeScript `src/` into `dist/`.
- `src/nanobot/bridge/src/types.d.ts`: local declaration for `qrcode-terminal`.
- `src/nanobot/bridge/src/index.ts`: applies the Node crypto polyfill, reads
  port/auth/token environment variables, starts `BridgeServer`, and handles
  termination signals.
- `src/nanobot/bridge/src/server.ts`: loopback-only WebSocket server with
  optional first-message token authentication. It broadcasts QR/status/inbound
  events and accepts `send` and `send_media` commands.
- `src/nanobot/bridge/src/whatsapp.ts`: Baileys connection/auth/reconnect logic,
  inbound content and mention extraction, media download, and outbound
  text/media payloads.

The Python/Node protocol is JSON over WebSocket. If it changes, update
`channels/whatsapp.py` and `bridge/src/server.ts` together; media/content fields
also originate in `bridge/src/whatsapp.ts`.

## Providers

- `src/nanobot/providers/base.py` defines normalized `ToolCallRequest`,
  `LLMResponse`, `GenerationSettings`, and abstract `LLMProvider`. It sanitizes
  messages and implements retry wrappers for transient API failures, streaming
  fallback, and a retry without images for otherwise non-transient failures.
- `src/nanobot/providers/registry.py` is the single metadata registry for
  provider matching and construction: names/keywords, backend type, env keys,
  gateway/local/OAuth/direct flags, default bases, prefix handling, parameter
  overrides, and prompt-cache support. Registry order controls matching and
  fallback. Add a provider spec here and a field in `ProvidersConfig`.
- `src/nanobot/providers/openai_compat_provider.py` is the shared `AsyncOpenAI`
  backend for most providers. It applies registry behavior and headers,
  sanitizes provider-specific fields, supports prompt caching/reasoning/model
  overrides, parses repaired tool-call JSON, and reconstructs streamed calls.
- `src/nanobot/providers/anthropic_provider.py` is a native Anthropic Messages
  backend. It converts OpenAI-shaped history/tools/images/tool results, merges
  roles, handles prompt caching and extended thinking, and supports native
  streaming.
- `src/nanobot/providers/azure_openai_provider.py` directly calls Azure chat
  completions API version `2024-10-21`, treating the model as a deployment name
  and handling Azure headers, parameter differences, and SSE parsing.
- `src/nanobot/providers/openai_codex_provider.py` gets OAuth credentials through
  `oauth_cli_kit`, converts messages/tools to the Codex Responses API, consumes
  SSE, preserves encrypted reasoning content, and uses prompt-cache keys.
- `src/nanobot/providers/transcription.py` wraps Groq Whisper transcription and
  returns an empty string on missing credentials/files or API failure.
- `src/nanobot/providers/__init__.py` eagerly exports only the base types and
  lazily imports concrete backends to avoid loading every SDK.

Provider selection starts in `Config._match_provider()` and construction occurs
in `cli.commands._make_provider()`. A new backend implementation therefore
usually needs registry metadata, a schema field, and a construction branch;
another OpenAI-compatible endpoint normally needs only registry/schema entries.

## Cron and heartbeat

These solve different problems: cron is a persisted schedule for a specific
future/recurring agent turn; heartbeat periodically asks whether work described
in `HEARTBEAT.md` is currently due.

### Cron

- `src/nanobot/cron/types.py` defines `CronSchedule` (`at`, `every`, or cron
  expression), payload, run record/state, job, and store dataclasses.
- `src/nanobot/cron/service.py` persists `<workspace>/cron/jobs.json`, notices
  external mtime changes, computes next runs with `croniter`, arms one asyncio
  timer, runs due jobs sequentially, records the last 20 outcomes, and exposes
  list/add/remove/enable/run/status APIs.
- `src/nanobot/cron/__init__.py` re-exports the public cron types.
- `agent/tools/cron.py` is the LLM-facing interface described above.
- `cli.commands.gateway()` supplies `on_cron_job()`. It runs the instruction via
  `AgentLoop.process_direct()` in `cron:<id>`, prevents nested job creation,
  respects an explicit `message` tool send, and otherwise uses
  `utils.evaluator.evaluate_response()` before optional delivery.

### Heartbeat

- `src/nanobot/heartbeat/service.py` contains `HeartbeatService` and the virtual
  `heartbeat` decision tool. `start()` waits one full configured interval before
  the first automatic check. `_decide()` gives a configurable decision provider
  the active `HEARTBEAT.md`, long-term memory, current time, and newest active
  chat history. `_tick()` skips or calls the injected phase-two executor.
  `trigger_now()` performs decision/execution but not the normal notification
  gate.
- `src/nanobot/heartbeat/__init__.py` re-exports the service.
- `cli.commands.gateway()` implements the other half of the subsystem. It
  selects the most recently updated session on an enabled external channel,
  creates a fresh `heartbeat` session seeded from that chat, builds the task
  instruction, and runs the full agent. The phase-two agent must use the
  `message` tool for user-visible delivery; its plain final text is suppressed.
  Sent message content is mirrored into the real chat session so future context
  remains accurate.
- `src/nanobot/utils/evaluator.py` is a fail-open LLM notification gate shared by
  heartbeat and cron. It suppresses routine/no-change results but defaults to
  notifying if evaluation fails.

When changing heartbeat behavior, inspect all of `heartbeat/service.py`, the
heartbeat block inside `cli/commands.py`, `utils/evaluator.py`, session/history
handling in `agent/loop.py`, and the workspace `HEARTBEAT.md` template.

## Configuration, paths, security, and utilities

- `src/nanobot/config/schema.py`: all Pydantic configuration models. Models
  accept camelCase and snake_case; root settings also accept nested
  `NANOBOT_...` environment variables. Channel-specific config is stored as
  allowed extra fields. `Config._match_provider()` implements forced, prefix,
  keyword, local, and configured-key fallback order; OAuth providers do not
  become implicit fallbacks.
- `src/nanobot/config/loader.py`: active config-path global, JSON load/save,
  validation fallback, and small config migrations. `load_config(path)` alone
  does not set the active global path; CLI callers set it explicitly.
- `src/nanobot/config/paths.py`: paths derived from the active config directory,
  workspace creation/default detection, media/cron/log directories, CLI
  history, WhatsApp bridge, and legacy sessions.
- `src/nanobot/config/__init__.py`: re-exports config and path APIs.
- `src/nanobot/security/network.py`: URL scheme/DNS/IP validation against
  loopback, private, link-local, metadata, CGNAT, and unique-local networks;
  redirect validation; and internal-URL detection in shell commands.
- `src/nanobot/security/__init__.py`: package marker.
- `src/nanobot/utils/helpers.py`: timestamp cleanup, thinking-tag cleanup,
  image MIME/base64 helpers, paths, timezone formatting, safe filenames,
  message splitting, assistant-message construction, prompt/message token
  estimates, status formatting, and create-only workspace template sync.
- `src/nanobot/utils/evaluator.py`: background notification gate described
  above.
- `src/nanobot/utils/omega_log.py`: optional firehose log next to the active
  config, including warnings and detailed agent/provider/tool payloads. It
  redacts inline base64 images but can contain message content and provider
  authorization headers; treat the log as sensitive. Logging failures never
  stop the application.
- `src/nanobot/utils/__init__.py`: re-exports `ensure_dir`.

## Bundled templates and skills

### Workspace templates

- `templates/AGENTS.md`: default runtime agent rules, especially cron versus
  heartbeat usage.
- `templates/SOUL.md`: default identity, values, and communication style.
- `templates/USER.md`: editable user profile and preferences.
- `templates/TOOLS.md`: non-obvious tool constraints and cron pointer.
- `templates/HEARTBEAT.md`: periodic-task list read by `HeartbeatService`.
- `templates/memory/MEMORY.md`: initial long-term memory structure.
- `templates/__init__.py` and `templates/memory/__init__.py`: package markers.

`utils.helpers.sync_workspace_templates()` copies the top-level Markdown files,
the memory template, creates empty `HISTORY.md`, and ensures `skills/`, but only
when each destination is missing.

### Built-in skills

- `skills/README.md`: bundled skill format and inventory.
- `skills/clawhub/SKILL.md`: find/install/update workspace skills through
  ClawHub.
- `skills/cron/SKILL.md`: model guidance and examples for the `cron` tool.
- `skills/github/SKILL.md`: GitHub CLI workflows; availability requires `gh`.
- `skills/memory/SKILL.md`: always-on guidance for long-term memory and history
  recall.
- `skills/summarize/SKILL.md`: external `summarize` CLI workflows.
- `skills/tmux/SKILL.md`: isolated tmux control and monitoring guidance.
- `skills/weather/SKILL.md`: curl-based wttr.in/Open-Meteo guidance.
- `skills/skill-creator/SKILL.md`: how to design, validate, and package skills.
- `skills/skill-creator/scripts/init_skill.py`: scaffolds a normalized skill
  directory and optional resource folders.
- `skills/skill-creator/scripts/quick_validate.py`: validates frontmatter,
  naming, description, `always`, and allowed root contents.
- `skills/skill-creator/scripts/package_skill.py`: validates and produces a
  `.skill` ZIP while rejecting unsafe paths/symlinks and excluding build/VCS
  clutter.
- `skills/tmux/scripts/find-sessions.sh`: lists/filter tmux sessions across
  selected sockets.
- `skills/tmux/scripts/wait-for-text.sh`: polls a pane for fixed or regex text
  with timeout controls.

Bundled skill bodies are not copied into the workspace. `SkillsLoader` reads
them directly, while workspace skills override them by directory name.

## Common change locations

- Change normal conversational behavior: start with `agent/loop.py`,
  `agent/context.py`, and `agent/runner.py`.
- Add or change a tool: `agent/tools/`, registration in `AgentLoop`, and any
  config in `config/schema.py`.
- Add an LLM endpoint: `providers/registry.py` plus `ProvidersConfig`; add a new
  provider class and `_make_provider()` branch only for a new wire protocol.
- Add a chat platform: a new `channels/<name>.py`; discovery is automatic.
- Change delivery, progress, retry, or stream routing across all platforms:
  `channels/manager.py`, `channels/base.py`, and stream metadata created in
  `agent/loop.py`.
- Change sessions or context-window pressure behavior: `session/manager.py`,
  `agent/memory.py`, persistence in `agent/loop.py`, and token helpers.
- Change cron: `cron/`, `agent/tools/cron.py`, and the cron callback in
  `cli/commands.py`.
- Change heartbeat: `heartbeat/service.py`, its gateway callbacks, evaluator,
  and the heartbeat template.
- Change mutable workspace defaults: `templates/` plus
  `sync_workspace_templates()`; remember existing workspaces are not overwritten.
- Change WhatsApp protocol behavior: update both Python `channels/whatsapp.py`
  and the TypeScript bridge.

There is no test suite in this preserved source snapshot. For changes, use
targeted import/compile checks and focused behavior tests with repository source
forced onto `PYTHONPATH`; do not use installation or deployment scripts as test
commands.
