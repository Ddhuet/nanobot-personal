"""Regression tests for accidental message-history timestamps in replies."""

from __future__ import annotations

import asyncio
import unittest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import OutboundMessage
from nanobot.channels.manager import ChannelManager
from nanobot.session.manager import Session
from nanobot.utils.helpers import strip_leading_timestamp


class TimestampHelperTests(unittest.TestCase):
    def test_strips_exact_history_label(self) -> None:
        self.assertEqual(
            strip_leading_timestamp("[2026-08-12 23:28] Good morning!"),
            "Good morning!",
        )

    def test_strips_label_after_invisible_leading_characters(self) -> None:
        self.assertEqual(
            strip_leading_timestamp("\n\ufeff\u200b[2026-08-12 23:28] Good morning!"),
            "Good morning!",
        )

    def test_does_not_strip_nonleading_or_non_history_timestamp(self) -> None:
        self.assertEqual(
            strip_leading_timestamp("Status: [2026-08-12 23:28] Good morning!"),
            "Status: [2026-08-12 23:28] Good morning!",
        )
        self.assertEqual(
            strip_leading_timestamp("[2026-08-12] Good morning!"),
            "[2026-08-12] Good morning!",
        )


class _RecordingChannel:
    def __init__(self) -> None:
        self.messages: list[OutboundMessage] = []

    async def send(self, msg: OutboundMessage) -> None:
        self.messages.append(msg)

    async def send_delta(self, chat_id: str, delta: str, metadata: dict) -> None:
        raise AssertionError("ordinary Discord-style delivery must not stream")


class OutboundTimestampTests(unittest.TestCase):
    def test_channel_boundary_cleans_progress_message(self) -> None:
        channel = _RecordingChannel()
        msg = OutboundMessage(
            channel="discord",
            chat_id="123",
            content="\n[2026-08-12 23:28] Good morning!",
            metadata={"_progress": True},
        )

        asyncio.run(ChannelManager._send_once(channel, msg))  # type: ignore[arg-type]

        self.assertEqual(channel.messages[0].content, "Good morning!")


class PersistedTimestampTests(unittest.TestCase):
    def test_save_turn_cleans_visible_and_reasoning_content(self) -> None:
        loop = object.__new__(AgentLoop)
        session = Session(key="discord:123")

        loop._save_turn(session, [{
            "role": "assistant",
            "content": "[2026-08-12 23:28] Good morning!",
            "reasoning_content": "[2026-08-12 23:28] Internal response",
        }], 0)

        self.assertEqual(session.messages[0]["content"], "Good morning!")
        self.assertEqual(session.messages[0]["reasoning_content"], "Internal response")


if __name__ == "__main__":
    unittest.main()
