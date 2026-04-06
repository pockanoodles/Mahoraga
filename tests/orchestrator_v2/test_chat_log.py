from __future__ import annotations
import pytest
import time
from backend.orchestrator.store.base import Store
from backend.orchestrator.store.chat_log import ChatLogStore, ChatLogEntry


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_chat_log_save_and_list(store):
    entry = ChatLogEntry(
        id="e1",
        user_id="web-user",
        mission_id="m1",
        user_message="hello",
        assistant_response="hi there",
        worker_id="claude:sonnet",
        cost_usd=0.001,
        created_at=time.time(),
    )
    await store.chat_log.save(entry)
    entries = await store.chat_log.list_recent(user_id="web-user", limit=10)
    assert len(entries) == 1
    assert entries[0].user_message == "hello"
    assert entries[0].assistant_response == "hi there"


@pytest.mark.asyncio
async def test_chat_log_empty(store):
    entries = await store.chat_log.list_recent(user_id="web-user", limit=10)
    assert entries == []


@pytest.mark.asyncio
async def test_chat_log_respects_limit(store):
    now = time.time()
    for i in range(5):
        entry = ChatLogEntry(
            id=f"e{i}",
            user_id="web-user",
            mission_id="m1",
            user_message=f"msg {i}",
            assistant_response=f"resp {i}",
            worker_id="claude:sonnet",
            cost_usd=0.001,
            created_at=now + i,
        )
        await store.chat_log.save(entry)
    entries = await store.chat_log.list_recent(user_id="web-user", limit=3)
    assert len(entries) == 3
    assert entries[0].user_message == "msg 4"


@pytest.mark.asyncio
async def test_chat_log_newest_first(store):
    now = time.time()
    for i in range(3):
        entry = ChatLogEntry(
            id=f"e{i}",
            user_id="web-user",
            mission_id="m1",
            user_message=f"msg {i}",
            assistant_response=f"resp {i}",
            worker_id="claude:sonnet",
            cost_usd=0.001,
            created_at=now + i,
        )
        await store.chat_log.save(entry)
    entries = await store.chat_log.list_recent(user_id="web-user", limit=10)
    assert entries[0].user_message == "msg 2"
    assert entries[2].user_message == "msg 0"


@pytest.mark.asyncio
async def test_chat_log_user_isolation(store):
    import time
    entry_a = ChatLogEntry(
        id="ea1",
        user_id="user-a",
        mission_id="m1",
        user_message="from a",
        assistant_response="resp a",
        worker_id="",
        cost_usd=0.0,
        created_at=time.time(),
    )
    entry_b = ChatLogEntry(
        id="eb1",
        user_id="user-b",
        mission_id="m1",
        user_message="from b",
        assistant_response="resp b",
        worker_id="",
        cost_usd=0.0,
        created_at=time.time(),
    )
    await store.chat_log.save(entry_a)
    await store.chat_log.save(entry_b)
    results = await store.chat_log.list_recent(user_id="user-a", limit=10)
    assert len(results) == 1
    assert results[0].user_message == "from a"
