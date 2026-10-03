"""One-time default job creation; heartbeat has no special execution policy."""

from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule

HEARTBEAT_INSTRUCTIONS = """IMPORTANT: This cron job drives essential periodic work. Do not delete or disable it casually; doing so stops heartbeat checks. You may modify its instructions, schedule, conversation context, and delivery destination when requested.

Read HEARTBEAT.md in your workspace and carry out any active tasks that are due.
Use the message tool only when there is an actionable update, completion, failure, or a decision needed from the user. Stay quiet for unchanged, routine, or non-actionable results.
Your final answer is internal and is never sent. Use the message tool if the user should receive anything.
"""


def seed_heartbeat(cron: CronService, *, enabled: bool, interval_s: int) -> None:
    """Seed once, or convert a legacy protected heartbeat, preserving later edits/deletion."""
    cron.seed_default_job(
        "heartbeat", enabled=enabled,
        schedule=CronSchedule(kind="every", every_ms=interval_s * 1000),
        message=HEARTBEAT_INSTRUCTIONS, context_mode="last_active",
        output_session_key="last_active",
    )
