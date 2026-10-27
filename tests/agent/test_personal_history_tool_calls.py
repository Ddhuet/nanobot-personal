"""Finished turns replay as dialogue; tool calls stay visible only within their turn."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMResponse, ToolCallRequest


@pytest.mark.asyncio
async def test_tool_calls_are_visible_within_a_turn_but_not_after_it(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("secret-tool-output\n")
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()
    provider.chat_stream_with_retry = AsyncMock(side_effect=[
        LLMResponse(content="", tool_calls=[ToolCallRequest(
            id="call_1", name="read_file", arguments={"path": "notes.txt"},
        )]),
        LLMResponse(content="The notes say hi."),
        LLMResponse(content="Second answer."),
    ])
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=workspace, model="test-model")

    await loop.process_direct("Read my notes", session_key="discord:dm", channel="discord", chat_id="dm")
    await loop.process_direct("Thanks", session_key="discord:dm", channel="discord", chat_id="dm")

    calls = provider.chat_stream_with_retry.await_args_list
    within_turn = calls[1].kwargs["messages"]
    assert any(m.get("role") == "tool" for m in within_turn)
    assert "secret-tool-output" in str(within_turn)

    next_turn = calls[2].kwargs["messages"]
    assert "Read my notes" in str(next_turn)
    assert "The notes say hi." in str(next_turn)
    assert "secret-tool-output" not in str(next_turn)
    assert all(m.get("role") != "tool" and not m.get("tool_calls") for m in next_turn)
    # Storage keeps the full record; only replay omits it.
    session = loop.sessions.get_or_create("discord:dm")
    assert any(m.get("role") == "tool" for m in session.messages)
