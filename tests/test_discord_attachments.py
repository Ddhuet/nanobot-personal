"""Regression coverage for workspace-scoped Discord attachments."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel, DiscordConfig
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import Config


class _Response:
    content = b"not-really-an-image"

    def raise_for_status(self) -> None:
        pass


class _HttpClient:
    async def get(self, _url: str) -> _Response:
        return _Response()


class DiscordAttachmentTests(unittest.IsolatedAsyncioTestCase):
    def test_channel_manager_injects_configured_workspace(self) -> None:
        config = Config.model_validate({
            "agents": {"defaults": {"workspace": "relative-workspace"}},
            "channels": {
                "discord": {
                    "enabled": True,
                    "token": "test",
                    "allowFrom": ["user-1"],
                },
            },
        })
        with patch(
            "nanobot.channels.registry.discover_all",
            return_value={"discord": DiscordChannel},
        ):
            manager = ChannelManager(config, MessageBus())

        self.assertEqual(
            manager.channels["discord"].workspace,
            (Path.cwd() / "relative-workspace").resolve(),
        )

    async def test_inbound_attachment_is_saved_inside_configured_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            workspace = Path(tempdir) / "custom-workspace"
            bus = MessageBus()
            channel = DiscordChannel(
                DiscordConfig(enabled=True, token="test", allow_from=["user-1"]),
                bus,
            )
            channel.workspace = workspace
            channel._http = _HttpClient()  # type: ignore[assignment]

            async def no_typing(_channel_id: str) -> None:
                pass

            channel._start_typing = no_typing  # type: ignore[method-assign]

            await channel._handle_message_create({
                "id": "message-1",
                "channel_id": "channel-1",
                "author": {"id": "user-1", "bot": False},
                "content": "please inspect this",
                "attachments": [{
                    "id": "attachment-1",
                    "filename": "notes.txt",
                    "size": len(_Response.content),
                    "url": "https://cdn.discord.test/notes.txt",
                }],
            })

            message = await bus.consume_inbound()
            expected = workspace / "media" / "discord" / "attachment-1_notes.txt"
            self.assertEqual(message.media, [str(expected)])
            self.assertIn(f"[attachment: {expected}]", message.content)
            self.assertEqual(expected.read_bytes(), _Response.content)


if __name__ == "__main__":
    unittest.main()
