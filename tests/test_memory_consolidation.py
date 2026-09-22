"""Regression coverage for token-triggered memory consolidation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nanobot.agent.memory import MemoryConsolidator
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


class MemoryConsolidationTests(unittest.IsolatedAsyncioTestCase):
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
