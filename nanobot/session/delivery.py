"""Resolve known conversation sessions into concrete message destinations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, cast

from nanobot.channels.notification_routes import notification_metadata
from nanobot.session.keys import UNIFIED_SESSION_KEY, last_channel_from_metadata
from nanobot.session.manager import SessionManager
from nanobot.session.session_handles import SessionHandleResolver, normalize_session_handle


@dataclass(frozen=True)
class SessionTarget:
    session_key: str
    channel: str
    chat_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


def resolve_session_target(sessions: SessionManager, key: str) -> SessionTarget:
    """Resolve a persisted session, preserving its channel's thread address."""
    if key.startswith("@"):
        handle = SessionHandleResolver(sessions).resolve(normalize_session_handle(key))
        if handle is None:
            raise ValueError(f"unknown session handle: {key}")
        key = handle.session_key
    if sessions.read_session_metadata(key) is None and sessions.get_cached(key) is None:
        raise ValueError(f"unknown conversation session: {key}")
    session = sessions.get_or_create(key)
    metadata: dict[str, Any]
    route_value = session.metadata.get("_compaction_route")
    if isinstance(route_value, dict):
        route = cast(dict[str, Any], route_value)
        channel, chat_id = route.get("channel"), route.get("chat_id")
        source_metadata: object = route.get("metadata")
        if source_metadata is None:
            source_metadata = {}
        if not isinstance(channel, str) or not isinstance(chat_id, str) or not isinstance(source_metadata, dict):
            raise ValueError(f"invalid delivery route for session: {key}")
        metadata = notification_metadata(channel, cast(dict[str, Any], source_metadata))
    else:
        address = last_channel_from_metadata(session.metadata) if key == UNIFIED_SESSION_KEY else None
        if address is None:
            if ":" not in key:
                raise ValueError(f"session has no delivery route: {key}")
            channel, chat_id = key.split(":", 1)
        else:
            channel, chat_id = address
        metadata = {}
        # Old sessions may predate persisted route snapshots.
        if channel == "slack" and ":" in chat_id:
            chat_id, thread = chat_id.split(":", 1)
            metadata["slack"] = {"thread_ts": thread}
        elif channel == "telegram" and ":" in chat_id:
            chat_id, topic = chat_id.split(":", 1)
            topic = topic.removeprefix("topic:")
            metadata["message_thread_id"] = int(topic)
        elif channel == "discord" and ":thread:" in chat_id:
            _, chat_id = chat_id.split(":thread:", 1)
        elif channel == "feishu" and ":" in chat_id:
            chat_id, root_id = chat_id.split(":", 1)
            metadata.update({"message_id": root_id, "thread_id": root_id, "chat_type": "group"})
    if channel in {"cron", "dream", "heartbeat", "system"} or not chat_id:
        raise ValueError(f"session is not a conversation destination: {key}")
    return SessionTarget(key, channel, chat_id, metadata)


def pick_last_active_session(
    sessions: SessionManager,
    enabled_channels: Iterable[str],
    archived_keys: Iterable[str] = (),
) -> SessionTarget | None:
    """Choose the latest real user chat; proactive sends must not change this choice."""
    enabled, archived = set(enabled_channels), set(archived_keys)
    candidates: list[tuple[float, SessionTarget]] = []
    for info in sessions.list_sessions():
        key = info["key"]
        if key in archived:
            continue
        try:
            target = resolve_session_target(sessions, key)
            if target.channel not in enabled or target.channel == "cli":
                continue
            session = sessions.get_or_create(key)
            activity = session.metadata.get("_last_user_activity")
            if not activity:
                activity = next((
                    m.get("timestamp") for m in reversed(session.messages)
                    if m.get("role") == "user" and not m.get("_cron_turn")
                    and not m.get("_automation_turn") and not m.get("_hidden_history")
                ), info.get("updated_at"))
            score = datetime.fromisoformat(activity).timestamp() if isinstance(activity, str) else 0
            candidates.append((score, target))
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    return max(candidates, key=lambda item: item[0])[1] if candidates else None
