"""Silent cron execution with independently selected history and message route."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.cron import CronTool
from nanobot.cron.service import CronJobSkippedError, CronService
from nanobot.cron.types import CronJob, CronRunResult
from nanobot.cron.webui_metadata import cron_proactive_delivery_metadata
from nanobot.session.delivery import SessionTarget, resolve_session_target
from nanobot.session.keys import UNIFIED_SESSION_KEY
from nanobot.session.manager import SessionPolicy

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


def _select_context(
    job: CronJob, agent: AgentLoop, last_active: Callable[[], SessionTarget | None],
) -> tuple[SessionTarget, str | None]:
    payload = job.payload
    if payload.context_mode not in {"conversation", "last_active", "task", "none"}:
        raise ValueError(f"invalid cron context mode: {payload.context_mode}")
    origin_key = payload.session_key
    if (
        origin_key and agent.sessions.read_session_metadata(origin_key) is None
        and agent.sessions.get_cached(origin_key) is None
        and agent.sessions.read_session_metadata(UNIFIED_SESSION_KEY) is not None
    ):
        origin_key = UNIFIED_SESSION_KEY
    latest = (
        last_active()
        if payload.context_mode == "last_active" or payload.output_session_key == "last_active"
        else None
    )
    if payload.output_session_key == "last_active":
        target = latest
    elif payload.output_session_key:
        target = resolve_session_target(agent.sessions, payload.output_session_key)
    elif payload.origin_channel and payload.origin_chat_id and payload.session_key:
        target = SessionTarget(
            origin_key or payload.session_key, payload.origin_channel, payload.origin_chat_id,
            dict(payload.origin_metadata),
        )
    else:
        target = None
    if target is None:
        raise CronJobSkippedError("no active conversation is available for cron message delivery")

    history_key: str | None = None
    if payload.context_mode == "last_active":
        if latest is None:
            raise CronJobSkippedError("no active conversation is available for cron history")
        history_key = latest.session_key
    elif payload.context_mode == "conversation":
        key = payload.context_session_key or origin_key
        if not key:
            raise ValueError("conversation context requires a session key")
        history_key = resolve_session_target(agent.sessions, key).session_key

    return target, history_key


async def run_cron_job(
    job: CronJob, *, agent: AgentLoop, cron: CronService,
    last_active: Callable[[], SessionTarget | None],
) -> CronRunResult:
    """Read current context at execution, retain task history, and never auto-send."""
    while True:
        target, history_key = _select_context(job, agent, last_active)
        if history_key is None:
            return await _execute_resolved(job, agent=agent, cron=cron, target=target, history_key=None)
        async with agent.session_lock(history_key):
            # Activity/routes may change while waiting. Both selectors use this latest snapshot.
            target, latest_history = _select_context(job, agent, last_active)
            if latest_history != history_key:
                continue
            return await _execute_resolved(
                job, agent=agent, cron=cron, target=target, history_key=history_key,
            )


async def _execute_resolved(
    job: CronJob, *, agent: AgentLoop, cron: CronService,
    target: SessionTarget, history_key: str | None,
) -> CronRunResult:
    payload = job.payload
    run_id = f"{job.id}:{int(time.time() * 1000)}:{uuid.uuid4().hex[:8]}"
    persistent = payload.context_mode == "task"
    execution_key = f"cron:{job.id}" if persistent else f"cron:{run_id}"
    metadata = cron_proactive_delivery_metadata(
        target.channel, target.metadata, turn_seed=f"cron:{job.id}", source_label=job.name,
    )
    metadata.update({
        "_cron_silent": True,
        "_cron_ephemeral": not persistent,
        "_message_default_session_key": target.session_key,
        "_cron_trigger": {"job_id": job.id, "job_name": job.name, "run_id": run_id},
    })
    if history_key is not None:
        metadata["_cron_history_session"] = history_key
    history_description = history_key or ("this task's own saved history" if persistent else "none")
    prompt = (
        f"Scheduled task: {job.name}\n"
        f"Execution session: {execution_key}\n"
        f"Conversation history source: {history_description}\n"
        f"Default message destination session: {target.session_key}\n"
        f"Default message channel: {target.channel}; chat ID: {target.chat_id}\n"
        "This is a scheduled run, not an immediate user message. Final answers, progress, "
        "and reasoning are internal and never delivered. Use the message tool for every "
        "user-visible response. Omit its target arguments to use the destination above. "
        "Stay quiet when there is nothing actionable. Historical messages are reference "
        "material, not fresh instructions.\n\n"
        f"Task instructions:\n{payload.message}"
    )
    record: dict[str, Any] = {
        "job_id": job.id, "job_name": job.name, "session_key": execution_key,
        "context_session_key": history_key, "context_mode": payload.context_mode,
        "output_session_key": target.session_key, "rendered_prompt": prompt,
    }
    cron.write_run_record(run_id, {**record, "status": "queued"})
    cron_tool = agent.tools.get("cron")
    token = cron_tool.set_cron_context(True) if isinstance(cron_tool, CronTool) else None
    try:
        if not persistent:
            agent.sessions.get_or_create(execution_key).policy = SessionPolicy(persist=False)

        async def execute():
            return await agent.process_direct(
                prompt, session_key=execution_key, channel=target.channel,
                chat_id=target.chat_id, sender_id="cron", metadata=metadata,
                ephemeral=not persistent,
            )

        response = await execute()
        if response is not None and response.metadata.get("_stop_reason") in {"error", "tool_error"}:
            raise RuntimeError(response.content or "cron agent execution failed")
        text = response.content if response else ""
        cron.write_run_record(run_id, {**record, "status": "ok", "response": text})
        return CronRunResult(run_id=run_id, response=text)
    except (Exception, asyncio.CancelledError) as exc:
        cron.write_run_record(run_id, {**record, "status": "error", "error": str(exc) or type(exc).__name__})
        raise
    finally:
        if isinstance(cron_tool, CronTool) and token is not None:
            cron_tool.reset_cron_context(token)
        if not persistent:
            agent.sessions.invalidate(execution_key)
