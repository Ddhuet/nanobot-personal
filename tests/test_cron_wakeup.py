"""Regression coverage for scheduled wakeups and cron validation."""

from __future__ import annotations

import asyncio
import copy
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nanobot.agent.coordination import SessionTurnCoordinator
from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.wakeup import execute_silent_wakeup
from nanobot.bus.events import OutboundMessage
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule
from nanobot.heartbeat.service import HeartbeatService
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.session.manager import SessionManager


class SessionTurnCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiters_run_user_then_cron_then_heartbeat_with_fresh_history(self) -> None:
        coordinator = SessionTurnCoordinator()
        release_active = asyncio.Event()
        active_started = asyncio.Event()
        history = ["before"]
        order: list[str] = []
        snapshots: dict[str, list[str]] = {}

        async def active_user() -> None:
            async with coordinator.turn("telegram:123", 0):
                active_started.set()
                await release_active.wait()
                history.append("active-user-response")

        async def waiter(label: str, priority: int) -> None:
            async with coordinator.turn("telegram:123", priority):
                order.append(label)
                snapshots[label] = list(history)
                history.append(label)

        active = asyncio.create_task(active_user())
        await active_started.wait()

        heartbeat = asyncio.create_task(waiter("heartbeat", 2))
        cron = asyncio.create_task(waiter("cron", 1))
        user = asyncio.create_task(waiter("new-user", 0))
        await asyncio.sleep(0)
        release_active.set()
        await asyncio.gather(active, heartbeat, cron, user)

        self.assertEqual(order, ["new-user", "cron", "heartbeat"])
        self.assertEqual(
            snapshots["new-user"],
            ["before", "active-user-response"],
        )
        self.assertEqual(
            snapshots["cron"],
            ["before", "active-user-response", "new-user"],
        )
        self.assertEqual(
            snapshots["heartbeat"],
            ["before", "active-user-response", "new-user", "cron"],
        )


class HeartbeatDecisionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_phase_one_provider_call_runs_inside_configured_guard(self) -> None:
        guard_active = False

        class _Provider:
            async def chat_with_retry(provider_self, **_kwargs) -> LLMResponse:
                self.assertTrue(guard_active)
                return LLMResponse(content="")

        @asynccontextmanager
        async def guard():
            nonlocal guard_active
            guard_active = True
            try:
                yield
            finally:
                guard_active = False

        with tempfile.TemporaryDirectory() as tempdir:
            provider = _Provider()
            heartbeat = HeartbeatService(
                workspace=Path(tempdir),
                provider=provider,  # type: ignore[arg-type]
                model="test",
                decide_provider=provider,  # type: ignore[arg-type]
                decide_model="test",
                decision_guard=guard,
            )
            action, tasks = await heartbeat._decide_with_guard("task")

        self.assertEqual((action, tasks), ("skip", ""))
        self.assertFalse(guard_active)


class CronToolValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = CronService(Path(self.tempdir.name) / "jobs.json")
        self.tool = CronTool(self.service, default_timezone="UTC")
        self.tool.set_context("telegram", "123", "telegram:123:topic:9")

    async def asyncTearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_rejects_non_positive_interval(self) -> None:
        result = await self.tool.execute(
            action="add", message="bad interval", every_seconds=-1,
        )
        self.assertEqual(result, "Error: every_seconds must be greater than 0")
        self.assertEqual(self.service.list_jobs(), [])

    async def test_rejects_invalid_cron_expression(self) -> None:
        result = await self.tool.execute(
            action="add", message="bad cron", cron_expr="not a cron",
        )
        self.assertEqual(result, "Error: invalid cron expression 'not a cron'")
        self.assertEqual(self.service.list_jobs(), [])

    async def test_rejects_past_one_time_schedule(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        result = await self.tool.execute(
            action="add", message="too late", at=past,
        )
        self.assertEqual(result, "Error: at must be a future datetime")
        self.assertEqual(self.service.list_jobs(), [])

    async def test_rejects_multiple_schedule_modes(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        result = await self.tool.execute(
            action="add",
            message="ambiguous",
            every_seconds=60,
            at=future,
        )
        self.assertEqual(
            result,
            "Error: exactly one of every_seconds, cron_expr, or at is required",
        )
        self.assertEqual(self.service.list_jobs(), [])

    async def test_registry_returns_validation_failure_to_model(self) -> None:
        registry = ToolRegistry()
        registry.register(self.tool)

        result = await registry.execute("cron", {
            "action": "add",
            "message": "bad cron",
            "cron_expr": "not a cron",
        })

        self.assertIn("Error: invalid cron expression 'not a cron'", result)
        self.assertIn("Analyze the error above", result)

    async def test_agent_receives_tool_failure_and_can_continue(self) -> None:
        class _Provider:
            def __init__(provider_self) -> None:
                provider_self.calls: list[list[dict]] = []

            async def chat_with_retry(provider_self, **kwargs) -> LLMResponse:
                provider_self.calls.append(copy.deepcopy(kwargs["messages"]))
                if len(provider_self.calls) == 1:
                    return LLMResponse(
                        content="",
                        tool_calls=[ToolCallRequest(
                            id="bad-cron",
                            name="cron",
                            arguments={
                                "action": "add",
                                "message": "bad cron",
                                "cron_expr": "not a cron",
                            },
                        )],
                    )
                return LLMResponse(content="I saw the error and recovered.")

        provider = _Provider()
        registry = ToolRegistry()
        registry.register(self.tool)
        result = await AgentRunner(provider).run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "schedule it"}],
            tools=registry,
            model="test",
            max_iterations=2,
        ))

        self.assertEqual(result.final_content, "I saw the error and recovered.")
        self.assertEqual(len(provider.calls), 2)
        self.assertIn(
            "Error: invalid cron expression 'not a cron'",
            provider.calls[1][-1]["content"],
        )

    async def test_valid_job_persists_exact_originating_session(self) -> None:
        result = await self.tool.execute(
            action="add", message="valid", every_seconds=60,
        )

        self.assertTrue(result.startswith("Created job 'valid'"))
        job = self.service.list_jobs()[0]
        self.assertEqual(job.payload.channel, "telegram")
        self.assertEqual(job.payload.to, "123")
        self.assertEqual(job.payload.session_key, "telegram:123:topic:9")

        reloaded = CronService(Path(self.tempdir.name) / "jobs.json").list_jobs()[0]
        self.assertEqual(reloaded.payload.session_key, "telegram:123:topic:9")

    async def test_task_local_context_does_not_cross_routes(self) -> None:
        both_ready = asyncio.Event()
        ready = 0

        async def add_for(route: str, chat_id: str) -> str:
            nonlocal ready
            self.tool.set_context("telegram", chat_id, route)
            ready += 1
            if ready == 2:
                both_ready.set()
            await both_ready.wait()
            return await self.tool.execute(
                action="add", message=route, every_seconds=60,
            )

        await asyncio.gather(
            asyncio.create_task(add_for("telegram:one", "1")),
            asyncio.create_task(add_for("telegram:two", "2")),
        )

        routes = {
            job.payload.message: (job.payload.to, job.payload.session_key)
            for job in self.service.list_jobs()
        }
        self.assertEqual(routes["telegram:one"], ("1", "telegram:one"))
        self.assertEqual(routes["telegram:two"], ("2", "telegram:two"))


class CronServiceRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_one_time_callback_is_retried_instead_of_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            attempts = 0

            async def callback(_job) -> str:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("temporary failure")
                return "done"

            service = CronService(Path(tempdir) / "jobs.json", on_job=callback)
            job = service.add_job(
                name="one time",
                schedule=CronSchedule(
                    kind="at",
                    at_ms=int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000),
                ),
                message="run",
                delete_after_run=True,
            )

            await service.run_job(job.id, force=True)
            failed = service.get_job(job.id)
            self.assertIsNotNone(failed)
            self.assertEqual(failed.state.last_status, "error")
            self.assertGreater(failed.state.next_run_at_ms, int(datetime.now().timestamp() * 1000))

            await service.run_job(job.id, force=True)
            self.assertIsNone(service.get_job(job.id))
            self.assertEqual(attempts, 2)


class _FakeWakeupAgent:
    TURN_PRIORITY_CRON = 1

    def __init__(self, sessions: SessionManager, message_tool: MessageTool):
        self.sessions = sessions
        self.tools = ToolRegistry()
        self.tools.register(message_tool)
        self.coordinator = SessionTurnCoordinator()
        self.seen_history: list[dict] = []
        self.seen_instruction = ""

    def session_turn(self, session_key: str, priority: int):
        return self.coordinator.turn(session_key, priority)

    async def process_direct(self, content: str, **kwargs) -> OutboundMessage:
        scratch = self.sessions.get_or_create(kwargs["session_key"])
        self.seen_history = [dict(message) for message in scratch.messages]
        self.seen_instruction = content
        message_tool = self.tools.get("message")
        assert isinstance(message_tool, MessageTool)
        message_tool.set_context(kwargs["channel"], kwargs["chat_id"])
        message_tool.start_turn()
        await message_tool.execute(content="background update")
        return OutboundMessage(
            channel=kwargs["channel"],
            chat_id=kwargs["chat_id"],
            content="plain output that must remain log-only",
        )


class SilentWakeupTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_then_seeds_latest_history_and_mirrors_only_message_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            sessions = SessionManager(Path(tempdir))
            chat = sessions.get_or_create("telegram:123")
            chat.add_message("user", "initial question")
            sessions.save(chat)

            outbound: list[OutboundMessage] = []

            async def send(message: OutboundMessage) -> None:
                outbound.append(message)

            agent = _FakeWakeupAgent(sessions, MessageTool(send_callback=send))

            async with agent.session_turn("telegram:123", 0):
                wakeup = asyncio.create_task(execute_silent_wakeup(
                    agent,  # type: ignore[arg-type]
                    instruction=lambda: "instruction after wait",
                    scratch_session_key="cron:abcd1234",
                    chat_session_key="telegram:123",
                    channel="telegram",
                    chat_id="123",
                    priority=agent.TURN_PRIORITY_CRON,
                ))
                await asyncio.sleep(0)
                chat.add_message("assistant", "new answer before cron")
                sessions.save(chat)

            plain = await wakeup

            self.assertEqual(plain, "plain output that must remain log-only")
            self.assertEqual(agent.seen_history[-1]["content"], "new answer before cron")
            self.assertEqual(agent.seen_instruction, "instruction after wait")
            self.assertEqual([message.content for message in outbound], ["background update"])

            final_chat = sessions.get_or_create("telegram:123")
            contents = [message["content"] for message in final_chat.messages]
            self.assertEqual(contents[-2:], ["new answer before cron", "background update"])
            self.assertNotIn("plain output that must remain log-only", contents)


if __name__ == "__main__":
    unittest.main()
