from unittest.mock import MagicMock

import pytest

from nanobot.agent.context import ContextBuilder, TranscriptInput
from nanobot.agent.hook import AgentHookContext
from nanobot.agent.progress_hook import AgentProgressHook
from nanobot.bus.outbound_events import StreamDeltaEvent
from nanobot.events import EventSink
from nanobot.session.manager import Session
from nanobot.utils.helpers import strip_leading_timestamp


@pytest.mark.parametrize('prefix', ['', '\n', '\u200b', '\ufeff', '\n\ufeff\u200b'])
def test_exact_copied_timestamp_prefix_is_removed(prefix):
    assert strip_leading_timestamp(prefix+'[2026-01-01 12:34] Hello') == 'Hello'
    assert strip_leading_timestamp(prefix+'[important] Hello') == prefix+'[important] Hello'


def test_history_timestamps_are_annotated_once_without_mutating_storage(tmp_path):
    session = Session('discord:dm')
    session.add_message('user', 'Question', timestamp='2026-01-01T12:34:00+00:00')
    session.add_message('assistant', '[2026-01-01 12:34] Answer', timestamp='2026-01-01T12:35:00+00:00')
    builder = ContextBuilder(tmp_path, timezone='UTC')
    messages = builder.build_transcript(TranscriptInput(session.get_history(include_timestamps=True), 'New question'))
    assert messages[1]['content'] == '[2026-01-01 12:34] Question'
    assert messages[2]['content'] == '[2026-01-01 12:35] Answer'
    assert all('timestamp' not in message for message in messages)
    assert session.messages[1]['content'] == '[2026-01-01 12:34] Answer'


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['\ufeff[2026-01-01 12:34] Hello world', '[ordinary] text', '['])
async def test_timestamp_does_not_leak_through_single_character_stream(text):
    events = []

    async def publish(event):
        events.append(event)

    hook = AgentProgressHook(events=EventSink(publish), streaming=True)
    ctx = MagicMock(spec=AgentHookContext)
    ctx.stream_continues_current_message = False
    for char in text:
        await hook.on_stream(ctx, char)
    await hook.on_stream_end(ctx, resuming=False)
    answer = ''.join(event.content for event in events if isinstance(event, StreamDeltaEvent))
    assert answer == strip_leading_timestamp(text)


@pytest.mark.asyncio
async def test_timestamp_does_not_leak_through_reasoning_stream():
    events = []

    async def publish(event):
        events.append(event)

    hook = AgentProgressHook(events=EventSink(publish))
    for char in '\u200b[2026-01-01 12:34] Considering the task':
        await hook.emit_reasoning(char)
    await hook.emit_reasoning_end()
    assert ''.join(getattr(event, 'content', '') for event in events) == 'Considering the task'


def test_persisted_assistant_text_and_reasoning_drop_copied_timestamp(loop_factory):
    loop = loop_factory()
    session = Session('discord:dm')
    loop._save_turn(session, [
        {'role': 'assistant', 'content': '\ufeff[2026-01-01 12:34] Useful output',
         'reasoning_content': '\n[2026-01-01 12:34] Reasoning'},
    ], 0)
    assert session.messages[0]['content'] == 'Useful output'
    assert session.messages[0]['reasoning_content'] == 'Reasoning'
