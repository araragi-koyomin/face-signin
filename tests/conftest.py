"""
Shared test fixtures. Zero external dependencies — all mocks, zero cost.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ["JWT_SECRET_KEY"] = "test-secret-key"


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for session scope — avoids 'bound to different loop' errors."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _mock_engine():
    """Mock SQLAlchemy async engine so it never tries to connect."""
    mock = AsyncMock()
    mock.begin = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock()),
        __aexit__=AsyncMock(return_value=None),
    ))
    mock.dispose = AsyncMock()
    return mock


def _mock_session_factory():
    """Mock async_sessionmaker — returns mocked session."""
    session = AsyncMock()

    async def _mock_execute(stmt):
        m = MagicMock()
        m.scalar_one_or_none = MagicMock(return_value=None)
        m.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        m.scalar = MagicMock(return_value=0)
        return m

    session.execute = _mock_execute
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.close = AsyncMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    return session
