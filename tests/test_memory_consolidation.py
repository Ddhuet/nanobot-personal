"""Regression coverage for token-triggered memory consolidation."""

from __future__ import annotations

import tempfile
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from nanobot.agent.memory import MemoryConsolidator
from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import cmd_new
from nanobot.command.router import CommandContext
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.session.manager import SessionManager


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    async def chat_with_retry(self, **_kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(
                id=f"memory-{self.calls}",
                name="save_memory",
                arguments={
                    "history_entry": f"[2026-09-22 00:00] Consolidation {self.calls}",
                    "memory_update": "# Memory\n",
                },
            )],
        )


class _BlockingProvider(_Provider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat_with_retry(self, **_kwargs) -> LLMResponse:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(
                id=f"memory-{self.calls}",
                name="save_memory",
                arguments={
                    "history_entry": "[2026-09-22 00:00] Stale consolidation",
                    "memory_update": "# Stale memory\n",
                },
            )],
        )


class MemoryConsolidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_loader_recovers_cursor_past_end_of_session(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            sessions = SessionManager(workspace)
            session = sessions.get_or_create("telegram:123")
            session.add_message("user", "visible again")
            sessions.save(session)
            path = sessions._get_session_path(session.key)
            lines = path.read_text(encoding="utf-8").splitlines()
            metadata = json.loads(lines[0])
            metadata["last_consolidated"] = 1770
            metadata["consolidation_threshold_exceeded"] = True
            lines[0] = json.dumps(metadata)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            sessions.invalidate(session.key)
            recovered = sessions.get_or_create(session.key)

            self.assertEqual(recovered.last_consolidated, 0)
            self.assertFalse(recovered.consolidation_threshold_exceeded)
            self.assertEqual(recovered.get_history(max_messages=0)[0]["content"], "visible again")

    async def test_reset_discards_in_flight_consolidation_result(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            sessions = SessionManager(workspace)
            session = sessions.get_or_create("telegram:123")
            for index in range(30):
                role = "user" if index % 2 == 0 else "assistant"
                session.add_message(role, f"message {index}")
            sessions.save(session)

            provider = _BlockingProvider()
            consolidator = MemoryConsolidator(
                workspace=workspace,
                provider=provider,  # type: ignore[arg-type]
                model="test",
                sessions=sessions,
                context_window_tokens=2000,
                keep_messages=20,
                build_messages=lambda **_kwargs: [],
                get_tool_definitions=lambda: [],
                max_completion_tokens=1,
            )
            consolidator.estimate_session_prompt_tokens = (  # type: ignore[method-assign]
                lambda _session: (1000, "test")
            )

            task = asyncio.create_task(
                consolidator.maybe_consolidate_by_tokens(session)
            )
            await provider.started.wait()

            background: list[asyncio.Task] = []
            loop = SimpleNamespace(
                sessions=sessions,
                memory_consolidator=consolidator,
                _schedule_background=lambda coroutine: background.append(
                    asyncio.create_task(coroutine)
                ),
            )
            await cmd_new(CommandContext(
                msg=InboundMessage(
                    channel="telegram",
                    sender_id="123",
                    chat_id="123",
                    content="/new",
                ),
                session=session,
                key=session.key,
                raw="/new",
                loop=loop,
            ))
            provider.release.set()
            await task
            await asyncio.gather(*background)

            reloaded = sessions.get_or_create(session.key)
            self.assertEqual(reloaded.messages, [])
            self.assertEqual(reloaded.last_consolidated, 0)
            self.assertFalse(reloaded.consolidation_threshold_exceeded)
            self.assertEqual(provider.calls, 2)
            history = (workspace / "memory" / "HISTORY.md").read_text(encoding="utf-8")
            self.assertEqual(history.count("Stale consolidation"), 1)
            self.assertEqual(
                (workspace / "memory" / "MEMORY.md").read_text(encoding="utf-8"),
                "# Stale memory\n",
            )

    async def test_threshold_crossing_consolidates_once_and_keeps_twenty_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            sessions = SessionManager(workspace)
            session = sessions.get_or_create("telegram:123")
            for index in range(30):
                role = "user" if index % 2 == 0 else "assistant"
                session.add_message(role, f"message {index}")
            sessions.save(session)

            provider = _Provider()
            consolidator = MemoryConsolidator(
                workspace=workspace,
                provider=provider,  # type: ignore[arg-type]
                model="test",
                sessions=sessions,
                context_window_tokens=2000,
                keep_messages=20,
                build_messages=lambda **_kwargs: [],
                get_tool_definitions=lambda: [],
                max_completion_tokens=1,
            )
            consolidator.estimate_session_prompt_tokens = (  # type: ignore[method-assign]
                lambda _session: (1000, "test")
            )

            await consolidator.maybe_consolidate_by_tokens(session)
            sessions.invalidate(session.key)
            session = sessions.get_or_create(session.key)
            session.add_message("user", "new message after consolidation")
            session.add_message("assistant", "new reply after consolidation")
            await consolidator.maybe_consolidate_by_tokens(session)

            self.assertEqual(provider.calls, 1)
            self.assertEqual(session.last_consolidated, 10)
            self.assertEqual(len(session.get_history(max_messages=0)), 22)
            self.assertEqual(session.get_history(max_messages=0)[0]["role"], "user")
            self.assertTrue(session.consolidation_threshold_exceeded)
            history = (workspace / "memory" / "HISTORY.md").read_text(encoding="utf-8")
            self.assertEqual(history.count("Consolidation"), 1)

    async def test_cutoff_moves_backward_to_keep_a_complete_user_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir)
            sessions = SessionManager(workspace)
            session = sessions.get_or_create("telegram:123")
            for index in range(31):
                role = "user" if index % 2 == 0 else "assistant"
                session.add_message(role, f"message {index}")

            consolidator = MemoryConsolidator(
                workspace=workspace,
                provider=_Provider(),  # type: ignore[arg-type]
                model="test",
                sessions=sessions,
                context_window_tokens=100,
                keep_messages=20,
                build_messages=lambda **_kwargs: [],
                get_tool_definitions=lambda: [],
            )

            boundary = consolidator.pick_consolidation_boundary(session)

            self.assertEqual(boundary, 10)
            self.assertEqual(len(session.messages) - boundary, 21)
            self.assertEqual(session.messages[boundary]["role"], "user")

    def test_config_accepts_camel_case_keep_count(self) -> None:
        defaults = AgentDefaults.model_validate({"consolidationKeepMessages": 35})

        self.assertEqual(defaults.consolidation_keep_messages, 35)
        self.assertEqual(defaults.model_dump(by_alias=True)["consolidationKeepMessages"], 35)


if __name__ == "__main__":
    unittest.main()
