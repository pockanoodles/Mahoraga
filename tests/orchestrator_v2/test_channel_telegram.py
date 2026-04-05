"""Tests for the Telegram channel adapter.

aiogram is mocked — no real network connection is made.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out aiogram before importing our module so that aiogram doesn't need
# to be installed in the test environment.
# ---------------------------------------------------------------------------

def _make_aiogram_stub():
    """Return a minimal aiogram stub module tree."""
    aiogram = MagicMock(name="aiogram")

    # types sub-module
    types_mod = MagicMock(name="aiogram.types")
    aiogram.types = types_mod

    # Filters sub-module
    filters_mod = MagicMock(name="aiogram.filters")
    aiogram.filters = filters_mod

    # Bot and Dispatcher as mock classes
    aiogram.Bot = MagicMock(name="Bot")
    aiogram.Dispatcher = MagicMock(name="Dispatcher")

    return aiogram


# Patch sys.modules before any import of our channel
_aiogram_stub = _make_aiogram_stub()
sys.modules.setdefault("aiogram", _aiogram_stub)
sys.modules.setdefault("aiogram.types", _aiogram_stub.types)
sys.modules.setdefault("aiogram.filters", _aiogram_stub.filters)

from backend.orchestrator.channels.telegram import TelegramChannel  # noqa: E402
from backend.orchestrator.channels.base import ChannelMessage  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tg_message(
    *,
    user_id: int = 12345,
    text: str | None = "Hello Mahoraga",
    document=None,
    photo=None,
    caption: str | None = None,
) -> MagicMock:
    """Create a minimal mock aiogram Message object."""
    msg = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.document = document
    msg.photo = photo
    msg.caption = caption
    return msg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_telegram_channel_name():
    """The channel name must be 'telegram'."""
    ch = TelegramChannel()
    assert ch.name == "telegram"


def test_telegram_channel_converts_message():
    """to_channel_message produces a correct ChannelMessage from a tg Message."""
    ch = TelegramChannel()
    tg_msg = _make_tg_message(user_id=12345, text="Hello Mahoraga")

    cm = ch.to_channel_message(tg_msg)

    assert isinstance(cm, ChannelMessage)
    assert cm.channel == "telegram"
    assert cm.text == "Hello Mahoraga"
    assert cm.attachments == []


def test_telegram_user_id_format():
    """User IDs must be prefixed with 'tg:'."""
    ch = TelegramChannel()
    tg_msg = _make_tg_message(user_id=99999)

    cm = ch.to_channel_message(tg_msg)

    assert cm.user_id == "tg:99999"
    assert cm.user_id.startswith("tg:")


def test_telegram_handles_document():
    """Document attachments are extracted correctly."""
    ch = TelegramChannel()

    doc = MagicMock()
    doc.file_id = "doc-file-id-abc"
    doc.file_name = "report.pdf"

    tg_msg = _make_tg_message(text=None, document=doc, caption="See attached")

    cm = ch.to_channel_message(tg_msg)

    assert cm.text == "See attached"
    assert len(cm.attachments) == 1
    att = cm.attachments[0]
    assert att["type"] == "document"
    assert att["data"] == "doc-file-id-abc"
    assert att["filename"] == "report.pdf"


def test_telegram_handles_photo():
    """Photo attachments are extracted and the highest-resolution photo is used."""
    ch = TelegramChannel()

    # Simulate a list of PhotoSize objects (smallest → largest)
    small_photo = MagicMock()
    small_photo.file_id = "photo-small-id"

    large_photo = MagicMock()
    large_photo.file_id = "photo-large-id"

    tg_msg = _make_tg_message(text=None, photo=[small_photo, large_photo], caption="Look at this")

    cm = ch.to_channel_message(tg_msg)

    assert cm.text == "Look at this"
    assert len(cm.attachments) == 1
    att = cm.attachments[0]
    assert att["type"] == "photo"
    # The largest photo (last in list) must be chosen
    assert att["data"] == "photo-large-id"
    assert att["filename"] == "photo.jpg"


def test_telegram_no_token_skips_start():
    """start() must return cleanly when no token is set."""
    ch = TelegramChannel(token="")

    import asyncio
    asyncio.run(ch.start())

    # Bot should remain None — nothing was initialised
    assert ch._bot is None


def test_telegram_message_text_falls_back_to_caption():
    """When text is None but caption is set, caption is used as text."""
    ch = TelegramChannel()
    tg_msg = _make_tg_message(text=None, caption="A caption")

    cm = ch.to_channel_message(tg_msg)

    assert cm.text == "A caption"


def test_telegram_message_empty_text_and_caption():
    """When both text and caption are None, text is an empty string."""
    ch = TelegramChannel()
    tg_msg = _make_tg_message(text=None, caption=None)

    cm = ch.to_channel_message(tg_msg)

    assert cm.text == ""
