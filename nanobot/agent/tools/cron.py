"""Cron tool for scheduling reminders and tasks."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import datetime
from typing import Any, Literal

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronJobState, CronSchedule
from nanobot.session.keys import UNIFIED_SESSION_KEY
from nanobot.session.manager import SessionManager

_CRON_PARAMETERS = tool_parameters_schema(
    action=StringSchema("Action to perform", enum=["add", "update", "list", "remove"]),
    name=StringSchema(
        "Optional short human-readable label for the job "
        "(e.g., 'weather-monitor', 'daily-standup'). Defaults to first 30 chars of message."
    ),
    message=StringSchema(
        "REQUIRED when action='add'. Instruction for the agent to execute when the job triggers "
        "(e.g., 'Send a reminder to WeChat: xxx' or 'Check system status and report'). "
        "Not used for action='list' or action='remove'."
    ),
    every_seconds=IntegerSchema(description="Positive interval in seconds (for recurring tasks)", minimum=1),
    cron_expr=StringSchema("Cron expression like '0 9 * * *' (for scheduled tasks)"),
    tz=StringSchema(
        "Optional IANA timezone for cron expressions (e.g. 'America/Vancouver'). "
        "When omitted with cron_expr, the tool's default timezone applies."
    ),
    at=StringSchema(
        "ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00'). "
        "Naive values use the tool's default timezone."
    ),
    context_mode=StringSchema(
        "Conversation history: conversation = latest history of context_session_key (origin by default); "
        "last_active = latest active user chat resolved at execution; task = this job's own persistent "
        "conversation, including earlier runs; none = no prior conversation, instructions only. "
        "Defaults to conversation on add; omitted fields stay unchanged on update.",
        enum=["conversation", "last_active", "task", "none"],
    ),
    context_session_key=StringSchema(
        "Exact known conversation session key or @handle to read when context_mode=conversation. "
        "Read its newest history when running, never the history at scheduling time. "
        "Omit on add to use the originating conversation.",
    ),
    output_session_key=StringSchema(
        "Independent default destination for the message tool: an exact known conversation "
        "session key, @handle, or last_active. Omit on add to use the originating conversation. "
        "All cron output requires message; final answers and progress are never auto-delivered. "
        "Use search_sessions/read_session for known sessions; never invent channel IDs.",
    ),
    enabled=BooleanSchema(description="Enable or disable a job when action=update."),
    job_id=StringSchema("REQUIRED when action='remove' or action='update'. Job ID (obtain via action='list')."),
    required=["action"],
    description=(
        "Action-specific parameters: add requires a non-empty message plus one schedule "
        "(every_seconds, cron_expr, or at); update requires job_id and accepts exactly one "
        "schedule to replace it, or no schedule to keep it unchanged; "
        "remove requires job_id; list only needs action. "
        "Per-action requirements are enforced at runtime (see field descriptions) so the "
        "top-level schema stays compatible with providers (e.g. OpenAI Codex/Responses) that "
        "reject oneOf/anyOf/allOf/enum/not at the root of function parameters."
    ),
)


@tool_parameters(_CRON_PARAMETERS)
class CronTool(Tool):
    """Tool to schedule reminders and recurring tasks."""

    def __init__(self, cron_service: CronService, default_timezone: str = "UTC", sessions: SessionManager | None = None):
        self._cron = cron_service
        self._sessions = sessions
        self._default_timezone = default_timezone
        self._in_cron_context: ContextVar[bool] = ContextVar("cron_in_context", default=False)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.cron_service is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        cron_service = ctx.cron_service
        if cron_service is None:
            raise RuntimeError("CronTool requires an initialized cron service")
        return cls(cron_service=cron_service, default_timezone=ctx.timezone, sessions=ctx.sessions)

    @staticmethod
    def _request_route() -> tuple[str, str, str, dict[str, Any]]:
        """Return routing from the authoritative request snapshot."""
        ctx = current_request_context()
        if ctx is None:
            return "", "", "", {}
        raw_key = f"{ctx.channel}:{ctx.chat_id}" if ctx.channel and ctx.chat_id else ""
        session_key = (
            raw_key if ctx.session_key == UNIFIED_SESSION_KEY else (ctx.session_key or "")
        )
        return session_key, ctx.channel or "", ctx.chat_id or "", dict(ctx.metadata or {})

    def set_cron_context(self, active: bool) -> Token[bool]:
        """Mark whether the tool is executing inside a cron job callback."""
        return self._in_cron_context.set(active)

    def reset_cron_context(self, token: Token[bool]) -> None:
        """Restore previous cron context."""
        self._in_cron_context.reset(token)

    @staticmethod
    def _validate_timezone(tz: str) -> str | None:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(tz)
        except (KeyError, Exception):
            return ToolResult.error(f"Error: unknown timezone '{tz}'")
        return None

    def _display_timezone(self, schedule: CronSchedule) -> str:
        """Pick the most human-meaningful timezone for display."""
        return schedule.tz or self._default_timezone

    @staticmethod
    def _format_timestamp(ms: int, tz_name: str) -> str:
        from zoneinfo import ZoneInfo

        dt = datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(tz_name))
        return f"{dt.isoformat()} ({tz_name})"

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            "Schedule silent agent tasks. Actions: add, update, list, remove. "
            "Each run receives separately selected conversation history and message destination. "
            "Only the message tool delivers output; ordinary final text stays internal. "
            f"If tz is omitted, cron expressions and naive ISO times default to {self._default_timezone}."
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        action = params.get("action")
        if action == "add" and not str(params.get("message") or "").strip():
            errors.append("message is required when action='add'")
        if action in {"remove", "update"} and not str(params.get("job_id") or "").strip():
            errors.append(f"job_id is required when action='{action}'")
        return errors

    def _schedule(
        self, every_seconds: int | None, cron_expr: str | None,
        tz: str | None, at: str | None, *, required: bool,
    ) -> CronSchedule | None:
        supplied = sum(value is not None for value in (every_seconds, cron_expr, at))
        if supplied != 1 and (required or supplied):
            raise ValueError("supply exactly one of every_seconds, cron_expr, or at")
        if tz is not None and cron_expr is None:
            raise ValueError("tz can only be used with cron_expr")
        if not supplied:
            return None
        if every_seconds is not None:
            return CronSchedule(kind="every", every_ms=self._positive_interval(every_seconds) * 1000)
        if cron_expr is not None:
            return CronSchedule(kind="cron", expr=cron_expr, tz=tz or self._default_timezone)
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(at or "")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(self._default_timezone))
        return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))

    @staticmethod
    def _positive_interval(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("every_seconds must be a positive integer")
        return value

    async def execute(
        self, action: str, name: str | None = None, message: str | None = None,
        every_seconds: int | None = None, cron_expr: str | None = None,
        tz: str | None = None, at: str | None = None, job_id: str | None = None,
        context_mode: Literal["conversation", "last_active", "task", "none"] | None = None,
        context_session_key: str | None = None, output_session_key: str | None = None,
        enabled: bool | None = None,
    ) -> str:
        if action == "list":
            return self._list_jobs()
        if action == "remove":
            return self._remove_job(job_id)
        if action not in {"add", "update"}:
            return ToolResult.error(f"Unknown action: {action}")
        if action == "add" and self._in_cron_context.get():
            return ToolResult.error("Error: cannot schedule new jobs from within a cron job execution")
        try:
            if message is not None and not message.strip():
                raise ValueError("message must not be empty")
            if action == "add" and not message:
                raise ValueError(
                    "cron action='add' requires a non-empty 'message'. Retry including message=\"...\""
                )
            if context_mode is not None and context_mode not in {"conversation", "last_active", "task", "none"}:
                raise ValueError("context_mode must be conversation, last_active, task, or none")
            schedule = self._schedule(every_seconds, cron_expr, tz, at, required=action == "add")
            if action == "update":
                if not job_id:
                    raise ValueError("job_id is required when action=update")
                fields: dict[str, Any] = {}
                if context_session_key is not None:
                    fields["context_session_key"] = context_session_key
                if output_session_key is not None:
                    fields["output_session_key"] = output_session_key
                self._validate_targets(context_session_key, output_session_key)
                result = self._cron.update_job(
                    job_id, name=name, message=message, schedule=schedule,
                    context_mode=context_mode, enabled=enabled,
                    delete_after_run=(schedule.kind == "at") if schedule else None,
                    **fields,
                )
                if isinstance(result, str):
                    return ToolResult.error(f"Error: job {result}: {job_id}")
                return f"Updated job '{result.name}' (id: {result.id})"
            return self._add_job(
                name, message or "", every_seconds, cron_expr, tz, at,
                context_mode=context_mode or "conversation",
                context_session_key=context_session_key, output_session_key=output_session_key,
            )
        except (ValueError, TypeError, OverflowError, KeyError) as exc:
            return ToolResult.error(f"Error: {exc}")

    def _add_job(
        self, name: str | None, message: str, every_seconds: int | None,
        cron_expr: str | None, tz: str | None, at: str | None, *,
        context_mode: Literal["conversation", "last_active", "task", "none"] = "conversation",
        context_session_key: str | None = None, output_session_key: str | None = None,
    ) -> str:
        try:
            if not message.strip():
                raise ValueError("cron action='add' requires a non-empty 'message'. Retry including message=\"...\"")
            schedule = self._schedule(every_seconds, cron_expr, tz, at, required=True)
            assert schedule is not None
            session_key, channel, chat_id, metadata = self._request_route()
            if not session_key or not channel or not chat_id:
                raise ValueError("scheduled cron jobs must be created from a chat session")
            self._validate_targets(context_session_key, output_session_key)
            job = self._cron.add_job(
                name=name or message[:30], schedule=schedule, message=message,
                delete_after_run=schedule.kind == "at", session_key=session_key,
                origin_channel=channel, origin_chat_id=chat_id, origin_metadata=metadata,
                context_mode=context_mode, context_session_key=context_session_key,
                output_session_key=output_session_key,
            )
            return f"Created job '{job.name}' (id: {job.id})"
        except (ValueError, TypeError, OverflowError, KeyError) as exc:
            return ToolResult.error(f"Error: {exc}")

    def _validate_targets(self, context_key: str | None, output_key: str | None) -> None:
        if self._sessions is None:
            return
        from nanobot.session.delivery import resolve_session_target

        for key in (context_key, output_key):
            if key is None or key == "last_active" and key == output_key:
                continue
            resolve_session_target(self._sessions, key)

    def _format_timing(self, schedule: CronSchedule) -> str:
        """Format schedule as a human-readable timing string."""
        if schedule.kind == "cron":
            tz = f" ({schedule.tz})" if schedule.tz else ""
            return f"cron: {schedule.expr}{tz}"
        if schedule.kind == "every" and schedule.every_ms:
            ms = schedule.every_ms
            if ms % 3_600_000 == 0:
                return f"every {ms // 3_600_000}h"
            if ms % 60_000 == 0:
                return f"every {ms // 60_000}m"
            if ms % 1000 == 0:
                return f"every {ms // 1000}s"
            return f"every {ms}ms"
        if schedule.kind == "at" and schedule.at_ms:
            return f"at {self._format_timestamp(schedule.at_ms, self._display_timezone(schedule))}"
        return schedule.kind

    def _format_state(self, state: CronJobState, schedule: CronSchedule) -> list[str]:
        """Format job run state as display lines."""
        lines: list[str] = []
        display_tz = self._display_timezone(schedule)
        if state.last_run_at_ms:
            info = (
                f"  Last run: {self._format_timestamp(state.last_run_at_ms, display_tz)}"
                f" — {state.last_status or 'unknown'}"
            )
            if state.last_error:
                info += f" ({state.last_error})"
            lines.append(info)
        if state.next_run_at_ms:
            lines.append(f"  Next run: {self._format_timestamp(state.next_run_at_ms, display_tz)}")
        return lines

    @staticmethod
    def _system_job_purpose(job: CronJob) -> str:
        if job.name == "dream":
            return "Dream memory consolidation for long-term memory."
        return "System-managed internal job."

    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs(include_disabled=True)
        if not jobs:
            return "No scheduled jobs."
        lines: list[str] = []
        for j in jobs:
            timing = self._format_timing(j.schedule)
            parts = [f"- {j.name} (id: {j.id}, {timing})"]
            if j.payload.kind == "system_event":
                parts.append(f"  Purpose: {self._system_job_purpose(j)}")
                parts.append("  Protected: visible for inspection, but cannot be removed.")
            if j.payload.kind == "agent_turn":
                parts.append(f"  Enabled: {j.enabled}; context: {j.payload.context_mode}; "
                             f"history session: {j.payload.context_session_key or j.payload.session_key or 'dynamic'}; "
                             f"message destination: {j.payload.output_session_key or j.payload.session_key}")
                parts.append(f"  Instructions: {j.payload.message}")
            parts.extend(self._format_state(j.state, j.schedule))
            lines.append("\n".join(parts))
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return ToolResult.error("Error: job_id is required for remove")
        result = self._cron.remove_job(job_id)
        if result == "removed":
            return f"Removed job {job_id}"
        if result == "protected":
            job = self._cron.get_job(job_id)
            if job and job.name == "dream":
                return (
                    "Cannot remove job `dream`.\n"
                    "This is a system-managed Dream memory consolidation job for long-term memory.\n"
                    "It remains visible so you can inspect it, but it cannot be removed."
                )
            return (
                f"Cannot remove job `{job_id}`.\n"
                "This is a protected system-managed cron job."
            )
        return f"Job {job_id} not found"
