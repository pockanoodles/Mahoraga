from backend.orchestrator.store.base import Store


async def test_store_connects_and_closes():
    s = await Store.connect(":memory:")
    assert s.missions is not None
    assert s.tasks is not None
    assert s.artifacts is not None
    assert s.events is not None
    await s.close()


async def test_store_connect_twice_to_memory_gives_independent_stores():
    s1 = await Store.connect(":memory:")
    s2 = await Store.connect(":memory:")
    await s1.close()
    await s2.close()


async def test_store_connect_creates_the_state_directory_on_a_fresh_machine(tmp_path):
    db = tmp_path / "home" / ".mahoraga-v2" / "mahoraga.db"
    s = await Store.connect(db)
    await s.close()
    assert db.is_file()
