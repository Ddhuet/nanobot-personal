"""Exercise actual cron/agent/tool/session boundaries without a network provider."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.cron import CronTool
from nanobot.bus.queue import MessageBus
from nanobot.cron.execution import run_cron_job
from nanobot.cron.heartbeat import seed_heartbeat
from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronPayload, CronSchedule
from nanobot.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from nanobot.session.delivery import pick_last_active_session, resolve_session_target


def make_loop(tmp_path, responses):
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()
    provider.chat_stream_with_retry = AsyncMock(side_effect=responses)
    cron = CronService(tmp_path / "workspace" / "cron" / "jobs.json")
    loop = AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path / "workspace",
        model="test-model", cron_service=cron,
    )
    return loop, cron


def chat(loop, key, text, timestamp="2026-01-01T12:00:00", metadata=None):
    session = loop.sessions.get_or_create(key)
    session.add_message("user", text, timestamp=timestamp)
    if metadata:
        session.metadata.update(metadata)
    loop.sessions.save(session)
    return session


def job(mode="conversation", history=None, output=None):
    return CronJob(id="report", name="report", payload=CronPayload(
        message="Do the task", session_key="discord:dm", origin_channel="discord",
        origin_chat_id="dm", context_mode=mode,
        context_session_key=history, output_session_key=output,
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["conversation", "last_active", "task", "none"])
async def test_every_context_mode_has_no_automatic_output(tmp_path, mode):
    loop, cron = make_loop(tmp_path, [LLMResponse(content="Nothing needs doing.")])
    source = chat(loop, "discord:dm", "recent user work")
    await run_cron_job(job(mode), agent=loop, cron=cron,
                       last_active=lambda: resolve_session_target(loop.sessions, "discord:dm"))
    assert loop.bus.outbound_size == 0
    assert [m["content"] for m in source.messages] == ["recent user work"]
    prompt = str(loop.provider.chat_stream_with_retry.await_args.kwargs["messages"])
    assert ("recent user work" in prompt) == (mode in {"conversation", "last_active"})
    assert "Default message destination session: discord:dm" in prompt
    assert "Use the message tool" in prompt
    assert all(not s["key"].startswith("cron:") for s in loop.sessions.list_sessions()) == (mode != "task")


@pytest.mark.asyncio
async def test_latest_history_is_read_after_waiting_and_output_is_independent(tmp_path):
    loop, cron = make_loop(tmp_path, [LLMResponse(content="internal")])
    source = chat(loop, "discord:dm", "old user work")
    chat(loop, "discord:team", "team-only information")
    lock = loop._get_session_lock(source.key)
    await lock.acquire()
    task = asyncio.create_task(run_cron_job(
        job(history="discord:dm", output="discord:team"), agent=loop, cron=cron,
        last_active=lambda: None,
    ))
    await asyncio.sleep(0)
    assert not loop.provider.chat_stream_with_retry.called
    source.add_message("user", "newest user work")
    loop.sessions.save(source)
    lock.release()
    await task
    prompt = str(loop.provider.chat_stream_with_retry.await_args.kwargs["messages"])
    assert "newest user work" in prompt
    assert "team-only information" not in prompt
    assert "Default message destination session: discord:team" in prompt
    assert loop.bus.outbound_size == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["conversation", "last_active"])
async def test_cron_history_excludes_source_session_tool_calls(tmp_path, mode):
    loop, cron = make_loop(tmp_path, [LLMResponse(content="internal")])
    source = chat(loop, "discord:dm", "What's the weather today?")
    source.add_message("assistant", "", tool_calls=[{
        "id": "call_1", "type": "function",
        "function": {"name": "web_search", "arguments": '{"query": "weather-lookup-arg"}'},
    }], reasoning_content="need to look it up")
    source.add_message("tool", "too damn hot", tool_call_id="call_1", name="web_search")
    source.add_message("assistant", "It's going to be hot today.")
    loop.sessions.save(source)
    await run_cron_job(job(mode), agent=loop, cron=cron,
                       last_active=lambda: resolve_session_target(loop.sessions, "discord:dm"))
    messages = loop.provider.chat_stream_with_retry.await_args.kwargs["messages"]
    prompt = str(messages)
    assert "What's the weather today?" in prompt
    assert "It's going to be hot today." in prompt
    assert "too damn hot" not in prompt
    assert "weather-lookup-arg" not in prompt
    assert "need to look it up" not in prompt
    assert all(m.get("role") != "tool" and not m.get("tool_calls") for m in messages)
    # The source session itself keeps its full tool history.
    assert any(m.get("role") == "tool" for m in source.messages)


@pytest.mark.asyncio
async def test_explicit_cron_message_is_recorded_once_in_destination_thread(tmp_path):
    responses = [
        LLMResponse(content="internal thinking", tool_calls=[ToolCallRequest(
            id="send", name="message", arguments={"content": "\ufeff[2026-01-01 12:00] Useful update"},
        )]),
        LLMResponse(content="Internal final text"),
    ]
    loop, cron = make_loop(tmp_path, responses)
    source = chat(loop, "discord:dm", "user work")
    destination = chat(loop, "slack:team:thread", "team messages", metadata={
        "_compaction_route": {"channel": "slack", "chat_id": "team", "metadata": {"slack": {"thread_ts": "thread"}}},
    })
    await run_cron_job(job(output=destination.key), agent=loop, cron=cron, last_active=lambda: None)
    assert loop.bus.outbound_size == 1
    sent = await loop.bus.consume_outbound()
    assert sent.content == "Useful update"
    assert sent.channel == "slack" and sent.chat_id == "team"
    assert sent.metadata["slack"]["thread_ts"] == "thread"
    assert [m["content"] for m in destination.messages] == ["team messages", "Useful update"]
    assert [m["content"] for m in source.messages] == ["user work"]


@pytest.mark.asyncio
async def test_task_history_persists_but_none_does_not(tmp_path):
    loop, cron = make_loop(tmp_path, [LLMResponse(content="first output"), LLMResponse(content="second output")])
    chat(loop, "discord:dm", "conversation that task mode must not borrow")
    await run_cron_job(job("task"), agent=loop, cron=cron, last_active=lambda: None)
    await run_cron_job(job("task"), agent=loop, cron=cron, last_active=lambda: None)
    messages = str(loop.provider.chat_stream_with_retry.await_args.kwargs["messages"])
    assert "first output" in messages
    assert "conversation that task mode must not borrow" not in messages
    assert loop.bus.outbound_size == 0


@pytest.mark.asyncio
async def test_last_active_uses_user_activity_not_cron_delivery(tmp_path):
    loop, _ = make_loop(tmp_path, [])
    old = chat(loop, "discord:old", "older", "2026-01-01T11:00:00")
    chat(loop, "discord:new", "newer", "2026-01-01T12:00:00")
    old.add_message("assistant", "proactive send", timestamp="2026-01-01T13:00:00", _channel_delivery=True)
    loop.sessions.save(old)
    assert pick_last_active_session(loop.sessions, ["discord"]).session_key == "discord:new"


def test_heartbeat_is_editable_and_deleted_job_is_not_recreated(tmp_path):
    cron = CronService(tmp_path / "cron" / "jobs.json")
    seed_heartbeat(cron, enabled=True, interval_s=60)
    heartbeat = cron.list_jobs()[0]
    assert heartbeat.payload.kind == "agent_turn"
    assert heartbeat.payload.context_mode == heartbeat.payload.output_session_key == "last_active"
    assert "IMPORTANT" in heartbeat.payload.message
    cron.update_job(heartbeat.id, message="edited", schedule=CronSchedule(kind="every", every_ms=120000))
    seed_heartbeat(CronService(cron.store_path), enabled=True, interval_s=60)
    persisted = CronService(cron.store_path).get_job(heartbeat.id)
    assert persisted.payload.message == "edited" and persisted.schedule.every_ms == 120000
    assert cron.remove_job(heartbeat.id) == "removed"
    restarted = CronService(cron.store_path)
    seed_heartbeat(restarted, enabled=True, interval_s=60)
    assert restarted.list_jobs() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule", [
    {"every_seconds": 0}, {"every_seconds": -1}, {"every_seconds": True},
    {"every_seconds": 60, "cron_expr": "* * * * *"},
    {"cron_expr": "invalid"}, {"at": "2000-01-01T00:00:00"},
    {"cron_expr": "* * * * *", "tz": "bad/timezone"},
])
async def test_bad_cron_parameters_create_nothing(tmp_path, schedule):
    loop, cron = make_loop(tmp_path, [])
    tool = CronTool(cron, sessions=loop.sessions)
    with request_context(RequestContext(channel="discord", chat_id="dm", session_key="discord:dm")):
        result = await tool.execute(action="add", message="test", **schedule)
    assert "Error" in result
    assert cron.list_jobs() == []


@pytest.mark.asyncio
async def test_background_cron_continuation_cannot_auto_send_or_report_errors(tmp_path):
    from nanobot.agent.turn_delivery import TurnDeliveryFactory
    from nanobot.bus.events import InboundMessage, OutboundMessage
    from nanobot.bus.outbound_events import ProgressEvent

    bus = MessageBus()
    factory = TurnDeliveryFactory(bus)
    # A detached subagent completion retains the cron execution session key.
    message = InboundMessage(channel="system", sender_id="subagent", chat_id="discord:dm", content="result")
    delivery = factory.create(message, "cron:task-id")
    if delivery.events.publish:
        await delivery.events.publish(ProgressEvent(content="internal progress"))
    await delivery.complete(OutboundMessage(channel="discord", chat_id="dm", content="internal final"), publish_completion=True)
    await delivery.fail(publish_completion=True)
    await delivery.idle()
    assert bus.outbound_size == 0
    assert delivery.silent


@pytest.mark.asyncio
async def test_last_active_changes_while_waiting_resolves_both_selectors_again(tmp_path):
    loop, cron = make_loop(tmp_path, [LLMResponse(content="internal")])
    old = chat(loop, "discord:old", "old conversation")
    new = chat(loop, "discord:new", "new conversation")
    active = old.key
    lock = loop.session_lock(old.key)
    await lock.acquire()
    task = asyncio.create_task(run_cron_job(
        job("last_active", output="last_active"), agent=loop, cron=cron,
        last_active=lambda: resolve_session_target(loop.sessions, active),
    ))
    await asyncio.sleep(0)
    active = new.key
    lock.release()
    await task
    prompt = str(loop.provider.chat_stream_with_retry.await_args.kwargs["messages"])
    assert "new conversation" in prompt and "old conversation" not in prompt
    assert "Default message destination session: discord:new" in prompt


@pytest.mark.asyncio
async def test_cron_update_round_trips_parameters_and_disable(tmp_path):
    loop, cron = make_loop(tmp_path, [])
    chat(loop, "discord:dm", "user")
    target = chat(loop, "discord:team", "team")
    tool = CronTool(cron, sessions=loop.sessions)
    with request_context(RequestContext(channel="discord", chat_id="dm", session_key="discord:dm")):
        added = await tool.execute(action="add", message="old", every_seconds=60)
        assert "Error" not in added
        saved = cron.list_jobs()[0]
        result = await tool.execute(action="update", job_id=saved.id, message="new",
                                    context_mode="none", output_session_key=target.key, enabled=False)
        assert "Error" not in result
    restored = CronService(cron.store_path).get_job(saved.id)
    assert restored.payload.message == "new"
    assert restored.payload.context_mode == "none"
    assert restored.payload.output_session_key == target.key
    assert not restored.enabled


@pytest.mark.asyncio
async def test_failed_one_shot_is_retried_and_only_success_is_deleted(tmp_path):
    import time

    cron = CronService(tmp_path / "jobs.json")
    saved = cron.add_job(name="retry", schedule=CronSchedule(kind="at", at_ms=int(time.time() * 1000) + 60000),
                         message="test", session_key="discord:dm", origin_channel="discord", origin_chat_id="dm",
                         delete_after_run=True)
    cron.on_job = AsyncMock(side_effect=RuntimeError("temporary failure"))
    await cron.run_job(saved.id, force=True)
    saved = cron.get_job(saved.id)
    assert saved is not None
    assert saved.state.last_status == "error" and saved.state.next_run_at_ms > int(time.time() * 1000)
    restarted = CronService(cron.store_path)
    restarted._load_store()
    restarted._recompute_next_runs()
    assert restarted.get_job(saved.id).state.next_run_at_ms is not None
    cron.on_job = AsyncMock(return_value="ok")
    await cron.run_job(saved.id, force=True)
    assert cron.get_job(saved.id) is None


def test_last_active_ignores_archived_and_disabled_routes(tmp_path):
    loop, _ = make_loop(tmp_path, [])
    chat(loop, "websocket:archived", "archived", "2026-01-01T14:00:00")
    chat(loop, "telegram:disabled", "disabled", "2026-01-01T13:00:00")
    chat(loop, "discord:active", "active", "2026-01-01T12:00:00")
    target = pick_last_active_session(loop.sessions, ["websocket", "discord"], ["websocket:archived"])
    assert target.session_key == "discord:active"


@pytest.mark.parametrize("key, channel, chat_id, metadata", [
    ("telegram:-100123:topic:42", "telegram", "-100123", {"message_thread_id": 42}),
    ("discord:456:thread:777", "discord", "777", {}),
    ("feishu:oc_abc:om_root", "feishu", "oc_abc", {"message_id": "om_root", "thread_id": "om_root", "chat_type": "group"}),
])
def test_legacy_thread_session_without_route_snapshot(tmp_path, key, channel, chat_id, metadata):
    loop, _ = make_loop(tmp_path, [])
    chat(loop, key, "legacy history")
    target = resolve_session_target(loop.sessions, key)
    assert (target.channel, target.chat_id, target.metadata) == (channel, chat_id, metadata)


@pytest.mark.asyncio
async def test_detached_cron_continuation_retains_thread_and_destination_history(tmp_path):
    loop, _ = make_loop(tmp_path, [
        LLMResponse(content="internal", tool_calls=[ToolCallRequest(id="send", name="message", arguments={"content": "update"})]),
        LLMResponse(content="internal final"),
    ])
    target = chat(loop, "slack:team:thread", "team")
    metadata = {"_cron_silent": True, "_cron_ephemeral": True,
                "_message_default_session_key": target.key, "slack": {"thread_ts": "thread"}}
    await loop.subagents._announce_result("task", "task", "instructions", "result", {
        "channel": "slack", "chat_id": "team", "session_key": "cron:detached", "metadata": metadata,
    }, "ok")
    incoming = await loop.bus.consume_inbound()
    assert incoming.metadata["slack"]["thread_ts"] == "thread"
    await loop._process_message(incoming)
    assert loop.bus.outbound_size == 1
    outgoing = await loop.bus.consume_outbound()
    assert outgoing.content == "update" and outgoing.metadata["slack"]["thread_ts"] == "thread"
    assert [m["content"] for m in target.messages] == ["team", "update"]
    assert all(info["key"] != "cron:detached" for info in loop.sessions.list_sessions())
