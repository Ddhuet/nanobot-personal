"""Shared phase-two execution for silent heartbeat and cron wakeups."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from loguru import logger

from nanobot.agent.tools.message import MessageTool

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


def _attachment_note(media: list[str]) -> str:
    if not media:
        return ""
    names = [Path(item).name or item for item in media]
    return f"[Sent attachments: {', '.join(names)}]"


async def execute_silent_wakeup(
    agent: "AgentLoop",
    *,
    instruction: str | Callable[[], str],
    scratch_session_key: str,
    chat_session_key: str | None,
    channel: str,
    chat_id: str,
    priority: int,
) -> str:
    """Run a full agent turn from fresh chat history without auto-delivery.

    Only successful ``message`` tool calls to the originating conversation are
    mirrored into that conversation's history. Plain final content remains in
    the scratch session for logging and is returned only to the caller.
    """
    coordination_key = chat_session_key or f"{channel}:{chat_id}"

    async with agent.session_turn(coordination_key, priority):
        scratch = agent.sessions.get_or_create(scratch_session_key)
        scratch.clear()

        if chat_session_key:
            chat_session = agent.sessions.get_or_create(chat_session_key)
            scratch.messages = [dict(message) for message in chat_session.messages]
            scratch.last_consolidated = chat_session.last_consolidated
            logger.debug(
                "Wakeup {}: seeded {} messages from {}",
                scratch_session_key,
                len(scratch.messages),
                chat_session_key,
            )

        agent.sessions.save(scratch)

        async def _silent(*_args, **_kwargs) -> None:
            return None

        current_instruction = instruction() if callable(instruction) else instruction
        response = await agent.process_direct(
            current_instruction,
            session_key=scratch_session_key,
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
            routing_session_key=coordination_key,
            turn_priority=None,
        )

        message_tool = agent.tools.get("message")
        if chat_session_key and isinstance(message_tool, MessageTool):
            chat_session = agent.sessions.get_or_create(chat_session_key)
            for sent in message_tool.sent_messages_in_turn():
                if sent.channel != channel or sent.chat_id != chat_id:
                    continue
                content = sent.content.strip()
                attachment_note = _attachment_note(sent.media)
                if attachment_note:
                    content = f"{content}\n{attachment_note}".strip()
                if content:
                    chat_session.add_message("assistant", content)
            agent.sessions.save(chat_session)

        return response.content if response else ""
