"""Unit tests for loop-bound async genai.Client isolation and ColosseumGemini."""
from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import MagicMock, patch
import pytest

from core.gemini import (
    ColosseumGemini,
    _get_async_client,
    get_genai_client,
    reset_genai_client,
)
from core.settings import get_settings


@pytest.fixture(autouse=True)
def clean_clients():
    """Ensure client caches are cleared before and after each test."""
    reset_genai_client()
    yield
    reset_genai_client()


def test_async_client_cached_per_event_loop_and_thread():
    """Verify that _get_async_client():
    1. Returns the exact same client on multiple calls in the same asyncio.run() loop.
    2. Returns a different client in a second asyncio.run() loop.
    3. Returns a different client in a new thread's loop.
    4. Runs offline without network calls, configured with Vertex AI and project settings.
    """
    created_clients: list[MagicMock] = []

    def mock_client_init(*args: Any, **kwargs: Any) -> MagicMock:
        mock_c = MagicMock()
        mock_c.args = args
        mock_c.kwargs = kwargs
        created_clients.append(mock_c)
        return mock_c

    with patch("core.gemini.genai.Client", side_effect=mock_client_init):
        # 1. Two calls in the same asyncio.run(...) loop return the exact same client
        async def run_first_loop() -> tuple[Any, Any]:
            c1 = _get_async_client(caller="caller1")
            c2 = _get_async_client(caller="caller1")
            return c1, c2

        client1, client2 = asyncio.run(run_first_loop())
        assert client1 is client2, "Two calls in the same event loop must return the identical client"
        assert len(created_clients) == 1, "genai.Client should only be constructed once in the first loop"

        # Verify constructor arguments match settings
        settings = get_settings()
        kwargs1 = created_clients[0].kwargs
        assert kwargs1.get("vertexai") is True
        assert kwargs1.get("project") == settings.project_id
        assert kwargs1.get("location") == settings.models_location
        assert "http_options" in kwargs1

        # 2. A call in a second asyncio.run(...) returns a different client
        async def run_second_loop() -> Any:
            return _get_async_client(caller="caller2")

        client3 = asyncio.run(run_second_loop())
        assert client3 is not client1, "A second asyncio.run() loop must get a brand-new client"
        assert len(created_clients) == 2, "Second loop must have constructed a new client"

        # 3. A call in a new thread's loop returns a different client again
        thread_results: list[tuple[Any, Any]] = []
        thread_errors: list[Exception] = []

        def thread_target() -> None:
            try:
                async def run_thread_loop() -> tuple[Any, Any]:
                    t1 = _get_async_client(caller="thread_caller")
                    t2 = _get_async_client(caller="thread_caller")
                    return t1, t2

                thread_results.append(asyncio.run(run_thread_loop()))
            except Exception as e:
                thread_errors.append(e)

        worker = threading.Thread(target=thread_target)
        worker.start()
        worker.join(timeout=5.0)

        assert not thread_errors, f"Thread encountered unexpected error: {thread_errors}"
        assert len(thread_results) == 1, "Thread should have recorded results"
        client4, client4_again = thread_results[0]

        assert client4 is client4_again, "Multiple calls in the new thread's loop must return the same client"
        assert client4 is not client1, "New thread's client must be distinct from loop 1 client"
        assert client4 is not client3, "New thread's client must be distinct from loop 2 client"
        assert len(created_clients) == 3, "New thread's loop must have constructed its own client"


def test_colosseum_gemini_uses_async_client_dynamically():
    """Verify that ColosseumGemini uses _get_async_client dynamically and does not pin sync client."""
    created_clients: list[MagicMock] = []

    def mock_client_init(*args: Any, **kwargs: Any) -> MagicMock:
        mock_c = MagicMock()
        mock_c.kwargs = kwargs
        created_clients.append(mock_c)
        return mock_c

    with patch("core.gemini.genai.Client", side_effect=mock_client_init):
        sync_dummy = MagicMock(name="sync_dummy")
        # Instantiate model passing sync client as kwargs (like adviser_factory does)
        model = ColosseumGemini(caller="finance_adviser", client=sync_dummy)

        # Loop 1: api_client should return loop 1's async client, not sync_dummy
        async def check_loop_1() -> tuple[Any, Any]:
            c1 = model.api_client
            c2 = model.api_client
            return c1, c2

        c1, c2 = asyncio.run(check_loop_1())
        assert c1 is c2
        assert c1 is not sync_dummy, "ColosseumGemini must not use the passed sync client"

        # Loop 2: api_client should return loop 2's async client, not loop 1's or sync_dummy
        async def check_loop_2() -> Any:
            return model.api_client

        c3 = asyncio.run(check_loop_2())
        assert c3 is not c1, "ColosseumGemini must return the active loop's client on new loop"
        assert c3 is not sync_dummy, "ColosseumGemini must not fall back to sync dummy"


def test_reset_genai_client_clears_async_cache():
    """Verify that reset_genai_client() clears both sync and async client caches."""
    created_clients: list[MagicMock] = []

    def mock_client_init(*args: Any, **kwargs: Any) -> MagicMock:
        mock_c = MagicMock()
        created_clients.append(mock_c)
        return mock_c

    with patch("core.gemini.genai.Client", side_effect=mock_client_init):
        async def check_reset() -> tuple[Any, Any]:
            first = _get_async_client()
            reset_genai_client()
            second = _get_async_client()
            return first, second

        first, second = asyncio.run(check_reset())
        assert first is not second, "reset_genai_client must evict cached async client"
        assert len(created_clients) == 2
