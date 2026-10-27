from unittest.mock import MagicMock

import pytest

from nanobot.agent.context import ContextBuilder, TranscriptInput
from nanobot.agent.hook import AgentHookContext
from nanobot.agent.progress_hook import AgentProgressHook
from nanobot.bus.outbound_events import StreamDeltaEvent
from nanobot.events import EventSink
from nanobot.session.manager import Session
from nanobot.utils.helpers import strip_history_timestamps


@pytest.mark.parametrize('prefix', ['', '\n', '\u200b', '\ufeff', '\n\ufeff\u200b'])
def test_exact_copied_timestamp_prefix_is_removed(prefix):
    assert strip_history_timestamps(prefix+'[2026-01-01 12:34] Hello') == 'Hello'
    assert strip_history_timestamps(prefix+'[important] Hello') == prefix+'[important] Hello'


def test_copied_timestamps_are_removed_mid_message():
    text = (
        'Real reply.\n\n[2026-10-11 12:20] Fake check-in.\n\n'
        '[2026-10-11 12:53]Another one, sent at \u200b[2026-10-11 13:23] noon [12:20].'
    )
    assert strip_history_timestamps(text) == (
        'Real reply.\n\nFake check-in.\n\nAnother one, sent at noon [12:20].'
    )


def test_consecutive_assistant_messages_each_keep_their_timestamp(tmp_path):
    session = Session('discord:dm')
    session.add_message('user', 'Night', timestamp='2026-01-01T01:00:00+00:00')
    session.add_message('assistant', 'Check-in one', timestamp='2026-01-01T09:04:00+00:00')
    session.add_message('assistant', '[2026-01-01 11:43] Check-in two', timestamp='2026-01-01T11:43:00+00:00')
    session.add_message('user', 'Hi', timestamp='2026-01-01T12:00:00+00:00')
    session.add_message('assistant', 'Reply', timestamp='2026-01-01T12:01:00+00:00')
    builder = ContextBuilder(tmp_path, timezone='UTC')
    messages = builder.build_transcript(TranscriptInput(session.get_history(include_timestamps=True), 'Next'))
    assert [m['content'] for m in messages[1:6]] == [
        '[2026-01-01 01:00] Night',
        '[2026-01-01 09:04] Check-in one',
        '[2026-01-01 11:43] Check-in two',
        '[2026-01-01 12:00] Hi',
        '[2026-01-01 12:01] Reply',
    ]


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
@pytest.mark.parametrize('text', [
    '\ufeff[2026-01-01 12:34] Hello world', '[ordinary] text', '[',
    'Reply.\n\n[2026-01-01 12:34] Fake check-in', 'Ends with partial [2026-01',
    'Mid \u200b[2026-01-01 12:34] stamp and [12:34] kept',
])
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
    assert answer == strip_history_timestamps(text)


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
