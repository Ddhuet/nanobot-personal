"""Message tool for sending messages to users."""

from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import OutboundMessage
from nanobot.utils.helpers import strip_leading_timestamp


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
    ):
        self._send_callback = send_callback
        self._context: ContextVar[tuple[str, str, str | None]] = ContextVar(
            "message_delivery_context",
            default=(default_channel, default_chat_id, default_message_id),
        )
        self._turn_state: ContextVar[dict[str, Any] | None] = ContextVar(
            "message_turn_state", default=None,
        )

    def set_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Set the current message context."""
        self._context.set((channel, chat_id, message_id))

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking."""
        self._turn_state.set({"sent_to_default": False, "messages": []})

    @property
    def _sent_in_turn(self) -> bool:
        """Whether this task's current turn sent to its default destination."""
        state = self._turn_state.get()
        return bool(state and state["sent_to_default"])

    def sent_messages_in_turn(self) -> list[OutboundMessage]:
        """Return successfully enqueued messages from this task's current turn."""
        state = self._turn_state.get()
        return list(state["messages"]) if state else []

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Send a message to the user, optionally with file attachments. "
            "This is the ONLY way to deliver files (images, documents, audio, video) to the user. "
            "Use the 'media' parameter with file paths to attach files. "
            "Do NOT use read_file to send files — that only reads content for your own analysis."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The message content to send"
                },
                "channel": {
                    "type": "string",
                    "description": "Optional: target channel (telegram, discord, etc.)"
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional: target chat/user ID"
                },
                "media": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: list of file paths to attach (images, audio, documents)"
                }
            },
            "required": ["content"]
        }

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        **kwargs: Any
    ) -> str:
        default_channel, default_chat_id, default_message_id = self._context.get()
        channel = channel or default_channel
        chat_id = chat_id or default_chat_id
        message_id = message_id or default_message_id

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        content = strip_leading_timestamp(content) or ""

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata={
                "message_id": message_id,
            },
        )

        try:
            await self._send_callback(msg)
            state = self._turn_state.get()
            if state is not None:
                state["messages"].append(msg)
                if channel == default_channel and chat_id == default_chat_id:
                    state["sent_to_default"] = True
            media_info = f" with {len(media)} attachments" if media else ""
            return f"Message sent to {channel}:{chat_id}{media_info}"
        except Exception as e:
            return f"Error sending message: {str(e)}"
