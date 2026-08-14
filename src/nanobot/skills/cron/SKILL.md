---
name: cron
description: Schedule exact-time reminders and agent wakeups with one-time, interval, or cron-expression timing.
---

# Cron

Use the `cron` tool to schedule exact-time reminders or wake the full assistant for a task.

At execution time, the assistant receives the current conversation context from the chat that created the job. The saved `message` is an instruction, not text that is delivered directly.

The assistant's normal wakeup response is log-only. To show the user anything, call the `message` tool. It is valid to complete a scheduled task without messaging the user.

Use exactly one schedule parameter when adding a job:

- `every_seconds`: positive recurring interval
- `cron_expr`: recurring calendar schedule; optionally include `tz`
- `at`: future ISO datetime for one execution

Make `message` a self-contained task instruction. The assistant will also have current conversation history and memory when it runs.

## Examples

Fixed reminder wakeup:
```
cron(action="add", message="Tell the user it is time to take a break by using the message tool.", every_seconds=1200)
```

Dynamic task (agent executes each time):
```
cron(action="add", message="Check HKUDS/nanobot GitHub stars and report", every_seconds=600)
```

One-time scheduled task (compute ISO datetime from current time):
```
cron(action="add", message="Use the message tool to remind the user about the meeting.", at="<ISO datetime>")
```

Timezone-aware cron:
```
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver")
```

List/remove:
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

## Time Expressions

| User says | Parameters |
|-----------|------------|
| every 20 minutes | every_seconds: 1200 |
| every hour | every_seconds: 3600 |
| every day at 8am | cron_expr: "0 8 * * *" |
| weekdays at 5pm | cron_expr: "0 17 * * 1-5" |
| 9am Vancouver time daily | cron_expr: "0 9 * * *", tz: "America/Vancouver" |
| at a specific time | at: ISO datetime string (compute from current time) |

## Timezone

Use `tz` only with `cron_expr`. Without it, cron expressions and naive ISO datetimes use the configured agent timezone. Prefer an explicit IANA timezone when the user's wording identifies one.

Jobs cannot create new cron jobs while they are executing. They may list or remove existing jobs.
