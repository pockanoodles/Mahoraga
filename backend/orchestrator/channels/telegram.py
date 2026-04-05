from __future__ import annotations

import logging
import os
from typing import AsyncGenerator, Callable, Awaitable

from .base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)

_TG_LIMIT = 4096


class TelegramChannel(ChannelAdapter):
    """Channel adapter for Telegram using aiogram."""

    def __init__(
        self,
        token: str | None = None,
        on_message: Callable[[ChannelMessage], Awaitable[AsyncGenerator[str, None]]] | None = None,
    ) -> None:
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._on_message = on_message
        self._bot = None
        self._dp = None

    @property
    def name(self) -> str:
        return "telegram"

    def to_channel_message(self, tg_msg) -> ChannelMessage:
        """Convert aiogram Message to ChannelMessage."""
        from aiogram import types

        user_id = f"tg:{tg_msg.from_user.id}"
        text = tg_msg.text or tg_msg.caption or ""
        attachments: list[dict] = []

        if tg_msg.document is not None:
            attachments.append({
                "type": "document",
                "data": tg_msg.document.file_id,
                "filename": tg_msg.document.file_name or "document",
            })

        if tg_msg.photo is not None and len(tg_msg.photo) > 0:
            # Use the highest resolution photo (last in list)
            photo = tg_msg.photo[-1]
            attachments.append({
                "type": "photo",
                "data": photo.file_id,
                "filename": "photo.jpg",
            })

        return ChannelMessage.new(
            user_id=user_id,
            channel="telegram",
            text=text,
            attachments=attachments,
        )

    async def send(self, user_id: str, text: str) -> None:
        """Send a message to a Telegram user. Splits at the 4096-char limit."""
        if self._bot is None:
            logger.warning("TelegramChannel.send called but bot is not started")
            return

        # Strip the "tg:" prefix to get the raw Telegram chat ID
        if user_id.startswith("tg:"):
            chat_id = int(user_id[3:])
        else:
            chat_id = int(user_id)

        # Split into chunks if needed
        chunks = [text[i:i + _TG_LIMIT] for i in range(0, len(text), _TG_LIMIT)]
        for chunk in chunks:
            await self._bot.send_message(chat_id=chat_id, text=chunk)

    async def start(self) -> None:
        """Start long polling. Skips gracefully if no token is configured."""
        if not self._token:
            logger.warning(
                "TelegramChannel: TELEGRAM_BOT_TOKEN not set — skipping start"
            )
            return

        from aiogram import Bot, Dispatcher, types
        from aiogram.filters import Command

        self._bot = Bot(token=self._token)
        self._dp = Dispatcher()

        on_message = self._on_message

        @self._dp.message()
        async def _handle(tg_msg: types.Message) -> None:
            channel_msg = self.to_channel_message(tg_msg)
            if on_message is None:
                return

            chunks: list[str] = []
            async for chunk in await on_message(channel_msg):
                chunks.append(chunk)

            reply = "".join(chunks)
            if reply:
                await self.send(channel_msg.user_id, reply)

        await self._dp.start_polling(self._bot)

    async def stop(self) -> None:
        """Clean shutdown of the bot and dispatcher."""
        if self._dp is not None:
            await self._dp.stop_polling()
            self._dp = None
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None
