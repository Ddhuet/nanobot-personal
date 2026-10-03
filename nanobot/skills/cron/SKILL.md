---
name: cron
description: Schedule silent agent tasks with independent conversation context and message destinations.
---

# Cron

Use the cron tool to add, update, list, disable, enable, or remove scheduled agent tasks. Every run is silent unless it explicitly calls the message tool. Include that instruction in reminders: "Use message to remind the user ...". Final answers, progress, and reasoning stay internal.

Supply exactly one schedule on add (or when replacing a schedule): a positive every_seconds, a valid cron_expr with optional IANA tz, or a future ISO at time. Naive times and cron expressions without tz use the tool's configured timezone. Successful one-time tasks auto-delete; failed runs retry after one minute.

History and delivery are independent:

- context_mode="conversation": read the newest history of context_session_key (originating chat by default).
- context_mode="last_active": read the most recently active user chat at execution time.
- context_mode="task": keep this task's own conversation across runs.
- context_mode="none": no prior conversation history, just the task instruction and normal bootstrap rules.
- output_session_key: exact known chat session key, @handle, or "last_active"; defaults to the originating chat. It sets the message tool's default target, never automatic delivery.

Use list_sessions, search_sessions, and read_session to identify known conversations. Ask for a reference/ID for a never-seen destination; do not invent one. History is read at execution time, not captured when scheduling.

Examples:

```text
cron(action="add", message="Use message to remind the user to take a break", every_seconds=1200, context_mode="none")
cron(action="add", message="Summarize the developer's recent work and send useful updates with message", cron_expr="0 9 * * *", context_mode="conversation", context_session_key="discord:developer-dm", output_session_key="discord:team-channel")
cron(action="update", job_id="abc123", context_mode="last_active", output_session_key="last_active", every_seconds=1800)
cron(action="update", job_id="abc123", enabled=false)
cron(action="list")
cron(action="remove", job_id="abc123")
```

The default heartbeat is an ordinary editable job that reads HEARTBEAT.md. It carries an importance warning, but can be modified, disabled, or deleted. Periodic checks may also be ordinary cron tasks with instructions to message only actionable changes. No evaluator gates delivery.
