import pytest
from backend.orchestrator.store.base import Store


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()
