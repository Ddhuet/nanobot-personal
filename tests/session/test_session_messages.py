from nanobot.session.session_messages import (
    SESSION_MESSAGE_METADATA_KEY,
    SessionMessageEnvelope,
    session_message_envelope,
)


def _envelope() -> SessionMessageEnvelope:
    return {
        "message_id": "message-1",
        "created_at_ms": 123,
        "expect_reply": True,
        "source_handle": "luma",
        "source_session_key": "websocket:source",
        "target_session_key": "telegram:target",
    }


def test_envelope_round_trips_tool_metadata() -> None:
    envelope = _envelope()

    assert session_message_envelope({SESSION_MESSAGE_METADATA_KEY: envelope}) == envelope


def test_envelope_rejects_invalid_session_key() -> None:
    envelope = _envelope()
    envelope["source_session_key"] = " "

    assert session_message_envelope({SESSION_MESSAGE_METADATA_KEY: envelope}) is None


def test_envelope_rejects_missing_fields() -> None:
    envelope = dict(_envelope())
    envelope.pop("target_session_key")

    assert session_message_envelope({SESSION_MESSAGE_METADATA_KEY: envelope}) is None
    assert session_message_envelope(None) is None


def test_get_history_can_exclude_tool_calls():
    from nanobot.session.manager import Session

    session = Session(key="chan:chat")
    session.add_message("user", "weather?")
    session.add_message("assistant", "Checking.", tool_calls=[{
        "id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"},
    }], thinking_blocks=[{"type": "thinking", "thinking": "hmm"}])
    session.add_message("tool", "too damn hot", tool_call_id="c1", name="web_search")
    session.add_message("assistant", "", tool_calls=[{
        "id": "c2", "type": "function", "function": {"name": "web_fetch", "arguments": "{}"},
    }])
    session.add_message("tool", "more heat", tool_call_id="c2", name="web_fetch")
    session.add_message("assistant", "It's hot.")

    assert len(session.get_history()) == 6
    assert session.get_history(include_tool_calls=False) == [
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": "Checking."},
        {"role": "assistant", "content": "It's hot."},
    ]
