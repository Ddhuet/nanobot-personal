"""Heartbeat service - periodic agent wake-up to check for tasks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider

_HEARTBEAT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat",
            "description": "Report heartbeat decision after reviewing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skip", "run"],
                        "description": "skip = nothing to do, run = has active tasks",
                    },
                    "tasks": {
                        "type": "string",
                        "description": "Natural-language summary of active tasks (required for run)",
                    },
                },
                "required": ["action"],
            },
        },
    }
]

_PHASE1_SYSTEM_PROMPT = (
    "You are a heartbeat agent. Your ONLY job is to call the heartbeat tool to report your decision."
    " You have access to the user's conversation history and memory files."
    " Decide whether any of the recurring tasks in HEARTBEAT.md need to be run now,"
    " considering the conversation context and any relevant information in MEMORY.md."
)


class HeartbeatService:
    """
    Periodic heartbeat service that wakes the agent to check for tasks.

    Phase 1 (decision): reads HEARTBEAT.md and MEMORY.md alongside the active
    chat session's conversation history, then asks a lightweight LLM — via a
    virtual tool call — whether there are active tasks.  Uses a configurable
    cheaper model/provider (``decide_provider`` / ``decide_model``).

    Phase 2 (execution): only triggered when Phase 1 returns ``run``.  The
    ``on_execute`` callback runs the task through the full agent loop and
    returns the result to deliver.
    """

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        decide_provider: LLMProvider,
        decide_model: str,
        on_execute: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        on_notify: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        get_chat_history: Callable[[], tuple[list[dict[str, Any]], str | None, str | None]] | None = None,
        interval_s: int = 30 * 60,
        enabled: bool = True,
        timezone: str | None = None,
    ):
        self.workspace = workspace
        self.provider = provider
        self.model = model
        self.decide_provider = decide_provider
        self.decide_model = decide_model
        self.on_execute = on_execute
        self.on_notify = on_notify
        self.get_chat_history = get_chat_history
        self.interval_s = interval_s
        self.enabled = enabled
        self.timezone = timezone
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    @property
    def memory_file(self) -> Path:
        return self.workspace / "memory" / "MEMORY.md"

    def _read_heartbeat_file(self) -> str | None:
        if self.heartbeat_file.exists():
            try:
                return self.heartbeat_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    def _read_memory_file(self) -> str:
        if self.memory_file.exists():
            try:
                return self.memory_file.read_text(encoding="utf-8")
            except Exception:
                return ""
        return ""

    async def _decide(self, content: str) -> tuple[str, str]:
        """Phase 1: ask a lightweight LLM to decide skip/run via virtual tool call.

        Returns (action, tasks) where action is 'skip' or 'run'.
        """
        from datetime import datetime

        from nanobot.utils.helpers import current_time_str, format_datetime, format_message_timestamp

        chat_history: list[dict[str, Any]] = []
        last_user_ts: str | None = None
        if self.get_chat_history:
            chat_history, _, last_user_ts = self.get_chat_history()

        # Prepend compact timestamps to user and assistant messages so the
        # model can see when each message was sent.
        for msg in chat_history:
            if msg.get("role") in ("user", "assistant") and msg.get("timestamp"):
                ts_label = format_message_timestamp(msg["timestamp"], self.timezone)
                c = msg.get("content", "")
                msg["content"] = f"{ts_label} {c}" if c else ts_label
                del msg["timestamp"]

        memory_content = self._read_memory_file()

        user_parts: list[str] = []
        user_parts.append(f"Current Time: {current_time_str(self.timezone)}")

        if last_user_ts:
            try:
                last_dt = datetime.fromisoformat(last_user_ts)
                user_parts.append(f"Time of last message from user: {format_datetime(last_dt, self.timezone)}")
            except (ValueError, TypeError):
                pass

        user_parts.append(
            "Review the conversation history shown above along with the files below, and decide"
            " whether there are active tasks that need to be performed now. Consider the"
            " conversation history and the MEMORY.md contents to understand the user's context"
            "and recent interactions."
            " Note: This *is* the periodic heartbeat check. While this message shows as being from User, it is *not* a chat message from them, this is just how the heartbeat instruction appears."
        )
        if memory_content.strip():
            user_parts.append(f"\n---MEMORY.md contents---\n{memory_content}")
        user_parts.append(f"\n---HEARTBEAT.md contents---\n{content}")

        user_message = {"role": "user", "content": "\n\n".join(user_parts)}

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _PHASE1_SYSTEM_PROMPT},
        ]

        if chat_history:
            messages.extend(chat_history)
            last_role = chat_history[-1].get("role", "")
            if last_role == "user":
                messages.append({"role": "assistant", "content": "[Acknowledged]"})

        messages.append(user_message)

        response = await self.decide_provider.chat_with_retry(
            messages=messages,
            tools=_HEARTBEAT_TOOL,
            model=self.decide_model,
        )

        if not response.has_tool_calls:
            return "skip", ""

        args = response.tool_calls[0].arguments
        return args.get("action", "skip"), args.get("tasks", "")

    async def start(self) -> None:
        """Start the heartbeat service."""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (every {}s)", self.interval_s)

    def stop(self) -> None:
        """Stop the heartbeat service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: {}", e)

    async def _tick(self) -> None:
        """Execute a single heartbeat tick."""
        from nanobot.utils.evaluator import evaluate_response

        content = self._read_heartbeat_file()
        if not content:
            logger.debug("Heartbeat: HEARTBEAT.md missing or empty")
            return

        logger.info("Heartbeat: checking for tasks...")

        try:
            action, tasks = await self._decide(content)

            if action != "run":
                logger.info("Heartbeat: OK (nothing to report)")
                return

            logger.info("Heartbeat: tasks found, executing...")
            if self.on_execute:
                response = await self.on_execute(tasks)

                if response:
                    should_notify = await evaluate_response(
                        response, tasks, self.provider, self.model,
                    )
                    if should_notify and self.on_notify:
                        logger.info("Heartbeat: completed, delivering response")
                        await self.on_notify(response)
                    else:
                        logger.info("Heartbeat: silenced by post-run evaluation")
        except Exception:
            logger.exception("Heartbeat execution failed")

    async def trigger_now(self) -> str | None:
        """Manually trigger a heartbeat."""
        content = self._read_heartbeat_file()
        if not content:
            return None
        action, tasks = await self._decide(content)
        if action != "run" or not self.on_execute:
            return None
        return await self.on_execute(tasks)
