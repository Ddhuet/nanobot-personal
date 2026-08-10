"""Regression tests for reasoning-only model responses."""

from __future__ import annotations

import asyncio
import json
import unittest

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.providers.azure_openai_provider import AzureOpenAIProvider
from nanobot.providers.base import LLMResponse
from nanobot.providers.openai_compat_provider import OpenAICompatProvider
from nanobot.utils.helpers import strip_leading_timestamp


class _Provider:
    def __init__(self, response: LLMResponse):
        self.response = response

    async def chat_with_retry(self, **kwargs) -> LLMResponse:
        return self.response


class _MessageCleanupHook(AgentHook):
    def finalize_content(
        self,
        context: AgentHookContext,
        content: str | None,
    ) -> str | None:
        return strip_leading_timestamp(content)


def _run(response: LLMResponse):
    return asyncio.run(AgentRunner(_Provider(response)).run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=ToolRegistry(),
        model="test-model",
        max_iterations=1,
        hook=_MessageCleanupHook(),
    )))


class ReasoningFallbackTests(unittest.TestCase):
    def test_blank_completion_uses_cleaned_reasoning_content(self) -> None:
        result = _run(LLMResponse(
            content="",
            reasoning_content="[2026-08-09 12:34] The actual reply",
        ))

        self.assertEqual(result.final_content, "The actual reply")
        self.assertEqual(result.messages[-1]["content"], "The actual reply")

    def test_blank_completion_uses_all_thinking_blocks(self) -> None:
        result = _run(LLMResponse(
            content=None,
            thinking_blocks=[
                {"type": "thinking", "thinking": "First block"},
                {"type": "thinking", "thinking": "Second block"},
            ],
        ))

        self.assertEqual(result.final_content, "First block\n\nSecond block")

    def test_explicit_completion_takes_precedence(self) -> None:
        result = _run(LLMResponse(
            content="Visible reply",
            reasoning_content="Internal reasoning",
        ))

        self.assertEqual(result.final_content, "Visible reply")

    def test_openai_stream_parser_preserves_reasoning_deltas(self) -> None:
        response = OpenAICompatProvider._parse_chunks([
            {"choices": [{"delta": {"reasoning": "Part one "}}]},
            {
                "choices": [{
                    "delta": {"reasoning": "part two"},
                    "finish_reason": "stop",
                }],
            },
        ])

        self.assertIsNone(response.content)
        self.assertEqual(response.reasoning_content, "Part one part two")

    def test_openrouter_reasoning_field_becomes_fallback_content(self) -> None:
        provider = object.__new__(OpenAICompatProvider)
        parsed = provider._parse({
            "choices": [{
                "message": {
                    "content": None,
                    "reasoning": "[2026-08-09 21:21] Reasoning-only reply",
                },
                "finish_reason": "stop",
            }],
        })

        result = _run(parsed)

        self.assertEqual(
            result.final_content,
            "Reasoning-only reply",
        )

    def test_reasoning_details_text_is_normalized(self) -> None:
        provider = object.__new__(OpenAICompatProvider)
        parsed = provider._parse({
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_details": [
                        {"type": "reasoning.text", "text": "First block"},
                        {"type": "reasoning.text", "text": "Second block"},
                    ],
                },
                "finish_reason": "stop",
            }],
        })

        self.assertEqual(parsed.reasoning_content, "First block\n\nSecond block")

    def test_azure_stream_parser_preserves_reasoning_deltas(self) -> None:
        class _Response:
            async def aiter_lines(self):
                chunks = [
                    {"choices": [{"delta": {"reasoning_content": "Azure "}}]},
                    {
                        "choices": [{
                            "delta": {"reasoning_content": "reply"},
                            "finish_reason": "stop",
                        }],
                    },
                ]
                for chunk in chunks:
                    yield f"data: {json.dumps(chunk)}"
                yield "data: [DONE]"

        provider = object.__new__(AzureOpenAIProvider)
        response = asyncio.run(provider._consume_stream(_Response(), None))

        self.assertIsNone(response.content)
        self.assertEqual(response.reasoning_content, "Azure reply")


if __name__ == "__main__":
    unittest.main()
