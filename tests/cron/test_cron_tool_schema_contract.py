"""Regression tests for the cron tool's JSON-schema / runtime contract (#3113).

The schema advertised ``required=["action"]`` while ``_add_job`` rejected empty
``message``; LLMs rationally omitted ``message`` and looped on the runtime
error. The fix keeps ``required=["action"]`` (so ``list``/``remove`` stay
callable) but states the per-action requirement in each field's description
and tightens the runtime error for ``add`` without ``message``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule
from nanobot.providers.openai_responses.converters import convert_tools


class _SvcStub:
    """Minimal CronService stand-in; we only exercise schema/dispatch paths."""

    def list_jobs(self, include_disabled=False):
        return []

    def get_job(self, _job_id):
        return None

    def remove_job(self, _job_id):
        return "not-found"

    def add_job(self, **kwargs):
        class _J:
            pass

        j = _J()
        j.id = "id1"
        j.name = kwargs.get("name", "x")
        return j


@pytest.fixture
def registry() -> Iterator[ToolRegistry]:
    tool = CronTool(_SvcStub(), default_timezone="UTC")
    reg = ToolRegistry()
    reg.register(tool)
    with request_context(
        RequestContext(channel="channel", chat_id="chat-id", session_key="channel:chat-id")
    ):
        yield reg


class TestSchemaContract:
    def test_responses_keeps_schedule_fields_optional(self, registry: ToolRegistry) -> None:
        wire_tool = convert_tools(registry.get_definitions())[0]
        assert wire_tool["strict"] is False
        assert wire_tool["parameters"]["required"] == ["action"]
        _, _, err = registry.prepare_call(
            "cron", {"action": "update", "job_id": "abc", "at": "2030-01-01T00:00:00"}
        )
        assert err is None

    @pytest.mark.asyncio
    async def test_expired_one_off_can_be_rescheduled_with_only_at(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone

        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        monkeypatch.setattr("nanobot.cron.service._now_ms", lambda: now_ms - 120_000)
        service = CronService(tmp_path / "jobs.json")
        job = service.add_job(
            name="reminder", message="Original reminder",
            schedule=CronSchedule(kind="at", at_ms=now_ms - 60_000),
            session_key="discord:test", origin_channel="discord", origin_chat_id="test",
            delete_after_run=True,
        )
        monkeypatch.setattr("nanobot.cron.service._now_ms", lambda: now_ms)
        registry = ToolRegistry()
        registry.register(CronTool(service))

        result = await registry.execute("cron", {
            "action": "update", "job_id": job.id, "at": "2030-01-02T15:00:00-07:00",
        })

        assert "Updated job" in result
        saved = CronService(service.store_path).get_job(job.id)
        assert saved is not None
        expected = int(datetime.fromisoformat("2030-01-02T15:00:00-07:00").timestamp() * 1000)
        assert saved.schedule.kind == "at"
        assert saved.schedule.at_ms == saved.state.next_run_at_ms == expected
        assert saved.schedule.every_ms is None
        assert saved.payload.message == "Original reminder"
        assert saved.delete_after_run

    def test_list_accepted_without_message(self, registry: ToolRegistry) -> None:
        # action='list' must pass schema validation with nothing but 'action'.
        _, _, err = registry.prepare_call("cron", {"action": "list"})
        assert err is None

    def test_remove_accepted_without_message(self, registry: ToolRegistry) -> None:
        # action='remove' must pass schema validation with just 'action' + 'job_id'.
        _, _, err = registry.prepare_call("cron", {"action": "remove", "job_id": "abc"})
        assert err is None

    def test_add_with_message_accepted(self, registry: ToolRegistry) -> None:
        _, _, err = registry.prepare_call(
            "cron", {"action": "add", "message": "ping", "at": "2030-01-01T00:00:00"}
        )
        assert err is None

    def test_add_without_message_surfaces_actionable_runtime_error(
        self, registry: ToolRegistry
    ) -> None:
        # Schema permits omitting message; the runtime must return a message
        # that tells the LLM exactly what's missing and how to retry, so it
        # doesn't loop like #3113 reports.
        import asyncio

        tool = registry._tools["cron"]  # type: ignore[attr-defined]
        out = asyncio.run(tool.execute(action="add", at="2030-01-01T00:00:00"))
        assert "message" in out
        assert "add" in out
        assert "Retry" in out or "retry" in out


class TestSchemaSelfDescribesRequirements:
    def test_message_description_flags_add_requirement(self) -> None:
        # LLMs rely on field descriptions to infer when something is actually
        # needed. Without this hint, #3113's loop returns.
        tool = CronTool(_SvcStub())
        desc = tool.parameters["properties"]["message"]["description"]
        assert "REQUIRED" in desc and "action='add'" in desc

    def test_job_id_description_flags_remove_requirement(self) -> None:
        tool = CronTool(_SvcStub())
        desc = tool.parameters["properties"]["job_id"]["description"]
        assert "REQUIRED" in desc and "action='remove'" in desc

    def test_top_level_required_stays_narrow(self) -> None:
        # If 'message' or 'job_id' ever creep back into top-level required,
        # list/remove start failing schema validation (the bug PR #3163 v1
        # accidentally introduced).
        tool = CronTool(_SvcStub())
        assert tool.parameters["required"] == ["action"]
