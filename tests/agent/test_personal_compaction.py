import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_duplicate_idle_and_runner_compaction_are_discarded(loop_factory):
    loop = loop_factory()
    session = loop.sessions.get_or_create("discord:dm")
    session.add_message("user", "important work")
    loop.sessions.save(session)
    started, release = asyncio.Event(), asyncio.Event()

    async def archive(*args, **kwargs):
        started.set()
        await release.wait()
        return "Summary"

    loop.consolidator.archive_session = AsyncMock(side_effect=archive)
    task = asyncio.create_task(loop.consolidator.compact_idle_session(session.key, runtime=loop.llm_runtime()))
    await started.wait()
    duplicate = await asyncio.wait_for(
        loop.consolidator.compact_idle_session(session.key, runtime=loop.llm_runtime()), timeout=1,
    )
    assert duplicate == ""
    result = await asyncio.wait_for(loop.consolidator.summarize_transcript(
        [{"role": "user", "content": "work"}], None, runtime=loop.llm_runtime(),
        session_key=session.key, tools=[],
    ), timeout=1)
    assert result is None
    assert loop.consolidator.archive_session.await_count == 1
    release.set()
    await task
    assert not loop.consolidator.get_lock(session.key).locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_cached", [False, True])
async def test_reset_during_compaction_cannot_overwrite_new_turn(loop_factory, replace_cached):
    loop = loop_factory()
    key = "discord:reset"
    session = loop.sessions.get_or_create(key)
    session.add_message("user", "old work")
    loop.sessions.save(session)
    started, release = asyncio.Event(), asyncio.Event()

    async def archive(*args, **kwargs):
        started.set()
        await release.wait()
        return "Stale summary"

    loop.consolidator.archive_session = AsyncMock(side_effect=archive)
    task = asyncio.create_task(loop.consolidator.compact_idle_session(key, runtime=loop.llm_runtime()))
    await started.wait()
    session.clear()
    loop.sessions.save(session)
    if replace_cached:
        loop.sessions.invalidate(key)
        fresh = loop.sessions.get_or_create(key)
    else:
        fresh = session
    fresh.add_message("user", "new work after reset")
    loop.sessions.save(fresh)
    release.set()
    assert await task == ""
    loop.sessions.invalidate(key)
    persisted = loop.sessions.get_or_create(key)
    assert [m["content"] for m in persisted.messages] == ["new work after reset"]
    assert "_last_summary" not in persisted.metadata


@pytest.mark.asyncio
async def test_concurrent_append_survives_compaction(loop_factory):
    loop = loop_factory()
    session = loop.sessions.get_or_create("discord:append")
    session.add_message("user", "old work")
    loop.sessions.save(session)
    started, release = asyncio.Event(), asyncio.Event()

    async def archive(*args, **kwargs):
        started.set()
        await release.wait()
        return "Summary of old work"

    loop.consolidator.archive_session = AsyncMock(side_effect=archive)
    task = asyncio.create_task(loop.consolidator.compact_idle_session(session.key, runtime=loop.llm_runtime()))
    await started.wait()
    session.add_message("user", "new work")
    loop.sessions.save(session)
    release.set()
    await task
    assert session.get_history()[-1]["content"] == "new work"
