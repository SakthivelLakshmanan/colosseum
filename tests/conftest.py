"""Pytest fixtures for offline, deterministic, and fast test execution."""
from __future__ import annotations

import asyncio
import csv
from pathlib import Path
import time
from typing import Any
import unittest.mock
import pytest

from google.cloud import bigquery
from google.cloud import firestore
from google import genai

import core.gemini
import core.ledger
import knowledge.fact_store
import knowledge.queries


# ===========================================================================
# 1. Block real Firestore, BigQuery, and genai Client creation
# ===========================================================================

_orig_firestore_init = firestore.Client.__init__
_orig_bigquery_init = bigquery.Client.__init__
_orig_genai_init = genai.Client.__init__


def _blocked_firestore_init(self, *args: Any, **kwargs: Any) -> None:
    raise RuntimeError(
        "Network access forbidden in tests: real google.cloud.firestore.Client creation is blocked. "
        "Mock firestore or core.ledger in your test."
    )


def _blocked_bigquery_init(self, *args: Any, **kwargs: Any) -> None:
    raise RuntimeError(
        "Network access forbidden in tests: real google.cloud.bigquery.Client creation is blocked. "
        "Mock bigquery or knowledge.fact_store/queries in your test."
    )


def _blocked_genai_init(self, *args: Any, **kwargs: Any) -> None:
    raise RuntimeError(
        "Network access forbidden in tests: real google.genai.Client creation is blocked. "
        "Mock genai.Client or core.gemini in your test."
    )


@pytest.fixture(autouse=True, scope="session")
def block_real_cloud_clients():
    """Ensure no test touches the network by making real client creation fail immediately."""
    firestore.Client.__init__ = _blocked_firestore_init
    bigquery.Client.__init__ = _blocked_bigquery_init
    genai.Client.__init__ = _blocked_genai_init
    yield
    firestore.Client.__init__ = _orig_firestore_init
    bigquery.Client.__init__ = _orig_bigquery_init
    genai.Client.__init__ = _orig_genai_init


@pytest.fixture(autouse=True)
def reset_cloud_singletons():
    """Reset module-level client singletons before and after each test."""
    core.ledger._db = None
    core.gemini.reset_genai_client()
    knowledge.fact_store._client = None
    knowledge.queries._client = None
    yield
    core.ledger._db = None
    core.gemini.reset_genai_client()
    knowledge.fact_store._client = None
    knowledge.queries._client = None


# ===========================================================================
# 2. Preload facts from data/facts.csv into fact_store cache
# ===========================================================================

@pytest.fixture(autouse=True, scope="session")
def preload_local_facts():
    """Populate fact_store cache from data/facts.csv so get_fact operates offline without BigQuery."""
    root = Path(__file__).resolve().parent.parent
    csv_file = root / "data" / "facts.csv"
    if csv_file.is_file():
        with open(csv_file, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                fact = knowledge.fact_store._row_to_fact(row)
                knowledge.fact_store._fact_cache[fact.fact_id] = fact
        knowledge.fact_store._all_facts_loaded = True


# ===========================================================================
# 3. Make retry backoff sleeps instant in core.gemini
# ===========================================================================

class _GeminiTimeProxy:
    """Proxy for `time` module inside `core.gemini` that makes retry backoff sleeps instant."""

    def __getattr__(self, name: str) -> Any:
        if name == "sleep":
            # If unittest.mock has patched time.sleep on the time module in the test
            # (e.g. test_gemini_retries_capped_at_three), delegate to it so assertions work.
            real_sleep = getattr(time, "sleep")
            if isinstance(real_sleep, (unittest.mock.MagicMock, unittest.mock.Mock)):
                return real_sleep
            return lambda *args, **kwargs: None
        return getattr(time, name)


class _GeminiAsyncioProxy:
    """Proxy for `asyncio` inside `core.gemini` making retry sleep instant without touching global asyncio.sleep."""

    def __getattr__(self, name: str) -> Any:
        if name == "sleep":
            real_sleep = getattr(asyncio, "sleep")
            if isinstance(real_sleep, (unittest.mock.MagicMock, unittest.mock.Mock)):
                return real_sleep

            async def _instant_async_sleep(*args: Any, **kwargs: Any) -> None:
                pass

            return _instant_async_sleep
        return getattr(asyncio, name)


@pytest.fixture(autouse=True)
def instant_retry_sleep(monkeypatch):
    """Make the retry backoff sleep in core.gemini instant during tests.
    
    Patches only core.gemini's time.sleep and asyncio.sleep, preserving real ordering
    for concurrency and finish-order tests.
    """
    monkeypatch.setattr(core.gemini, "time", _GeminiTimeProxy())
    monkeypatch.setattr(core.gemini, "asyncio", _GeminiAsyncioProxy())
