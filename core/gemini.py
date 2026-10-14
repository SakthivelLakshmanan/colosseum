"""Shared Gemini wrapper for Colosseum.

Implements:
1. Concurrency limit across the process via settings.gemini.max_concurrent semaphore.
2. 90-second timeout per call.
3. 429 RESOURCE_EXHAUSTED retry loop up to 5 times with exponential backoff and jitter
   (2s, 4s, 8s, 16s, 30s) logging `[gemini] 429 retry n/5 caller=<name>`.
4. Raises a clear error after 5 failures rather than waiting indefinitely.
5. ColosseumGemini subclass of ADK Gemini model for native async advisers.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
import concurrent.futures
from contextvars import ContextVar
import logging
import os
import random
import threading
import time
from typing import Any, AsyncGenerator, Callable, Optional

from google import genai
from google.genai import types
from google.adk.models.google_llm import Gemini

from core.settings import get_settings

logger = logging.getLogger(__name__)

# Context variable for tracking caller identity
current_caller: ContextVar[str] = ContextVar("current_caller", default="gemini")

# 429 retry tracking per caller
_retry_counts: dict[str, int] = defaultdict(int)
_retry_counts_lock = threading.Lock()

# Process-wide concurrency semaphore
_gemini_semaphore: Optional[threading.BoundedSemaphore] = None
_sem_lock = threading.Lock()

# Shared genai Client instance
_genai_client: Optional[genai.Client] = None
_client_lock = threading.Lock()


def get_retry_counts() -> dict[str, int]:
    """Return dictionary of 429 retry counts keyed by caller."""
    with _retry_counts_lock:
        return dict(_retry_counts)


def record_retry(caller: str) -> None:
    """Record a 429 retry attempt for the given caller."""
    with _retry_counts_lock:
        _retry_counts[caller] += 1


def reset_retry_counts() -> None:
    """Reset the 429 retry counters."""
    with _retry_counts_lock:
        _retry_counts.clear()


def get_semaphore() -> threading.BoundedSemaphore:
    """Get or create the process-wide concurrency semaphore."""
    global _gemini_semaphore
    with _sem_lock:
        if _gemini_semaphore is None:
            settings = get_settings()
            max_c = getattr(settings.gemini, "max_concurrent", 6)
            _gemini_semaphore = threading.BoundedSemaphore(max_c)
        return _gemini_semaphore


def reset_semaphore() -> None:
    """Reset the process-wide concurrency semaphore."""
    global _gemini_semaphore
    with _sem_lock:
        _gemini_semaphore = None


def acquire_gemini_slot_sync() -> None:
    """Acquire a slot in the concurrency semaphore synchronously."""
    get_semaphore().acquire()


async def acquire_gemini_slot_async() -> None:
    """Acquire a slot in the concurrency semaphore asynchronously without blocking event loop."""
    sem = get_semaphore()
    await asyncio.to_thread(sem.acquire)


def release_gemini_slot() -> None:
    """Release a slot in the concurrency semaphore."""
    get_semaphore().release()


def is_retryable_error(exc: Exception) -> tuple[bool, str]:
    """Check if an exception represents 429, 503, 504 or equivalent transient rate/timeout error."""
    if isinstance(exc, (concurrent.futures.TimeoutError, TimeoutError, asyncio.TimeoutError)):
        return True, "504"
    msg = str(exc).upper()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429 or "429" in msg or "RESOURCE_EXHAUSTED" in msg or "RESOURCE EXHAUSTED" in msg:
        return True, "429"
    if code == 503 or "503" in msg or "UNAVAILABLE" in msg or "SERVICE UNAVAILABLE" in msg:
        return True, "503"
    if code == 504 or "504" in msg or "DEADLINE_EXCEEDED" in msg or "DEADLINE EXCEEDED" in msg or "GATEWAY TIMEOUT" in msg or "TIMED OUT" in msg:
        return True, "504"
    return False, ""


def is_429_error(exc: Exception) -> bool:
    """Check if an exception represents a 429 / RESOURCE_EXHAUSTED rate limit error."""
    retryable, code = is_retryable_error(exc)
    return retryable and code == "429"


# Persistent executor for synchronous Gemini calls
_sync_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=16, thread_name_prefix="colosseum_gemini"
)


def get_genai_client(caller: Optional[str] = None) -> genai.Client:
    """Initialize or return the shared google-genai Vertex AI client.
    
    Sets timeout from settings (default 60s) and disables SDK internal silent retries
    so rate limits are surfaced to our shared wrapper.
    """
    global _genai_client
    with _client_lock:
        if _genai_client is None:
            settings = get_settings()
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
            os.environ["GOOGLE_CLOUD_PROJECT"] = settings.project_id
            os.environ["GOOGLE_CLOUD_LOCATION"] = settings.models_location

            timeout_s = getattr(settings.gemini, "timeout_s", 60.0)
            http_opts = types.HttpOptions(
                timeout=int(timeout_s * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            )
            _genai_client = genai.Client(
                vertexai=True,
                project=settings.project_id,
                location=settings.models_location,
                http_options=http_opts,
            )

        if caller:
            current_caller.set(caller)
        return _genai_client


def generate(
    model: str,
    contents: Any,
    config: Any = None,
    caller: str = "",
    timeout_s: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> Any:
    """Shared Gemini generate wrapper.
    
    Calls client.models.generate_content and holds retry, backoff, timeout,
    semaphore and logging logic. Every caller uses this function.
    """
    settings = get_settings()
    eff_timeout_s = timeout_s if timeout_s is not None else float(getattr(settings.gemini, "timeout_s", 60.0))
    eff_max_retries = max_retries if max_retries is not None else int(getattr(settings.gemini, "max_retries", 3))
    delays = getattr(settings.gemini, "retry_backoff", [2.0, 8.0, 20.0])

    call_name = caller or current_caller.get()
    client = get_genai_client(caller=call_name)

    call_config = config
    http_opts = types.HttpOptions(
        timeout=int(eff_timeout_s * 1000),
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    if isinstance(config, dict):
        call_config = dict(config)
        call_config["http_options"] = http_opts
    elif hasattr(config, "http_options"):
        config.http_options = http_opts
        call_config = config
    elif config is None:
        call_config = types.GenerateContentConfig(http_options=http_opts)

    attempt = 0

    while True:
        acquire_gemini_slot_sync()
        slot_held = True
        try:
            future = _sync_executor.submit(
                client.models.generate_content,
                model=model,
                contents=contents,
                config=call_config,
            )
            res = future.result(timeout=eff_timeout_s)
            return res
        except Exception as exc:
            retryable, err_code = is_retryable_error(exc)
            if retryable and attempt < eff_max_retries:
                attempt += 1
                record_retry(call_name)
                print(f"[gemini] {err_code} retry {attempt}/{eff_max_retries} caller={call_name}", flush=True)
                logger.warning("[gemini] %s retry %d/%d caller=%s", err_code, attempt, eff_max_retries, call_name)
                delay = (delays[attempt - 1] if attempt - 1 < len(delays) else delays[-1]) + random.uniform(0.1, 1.0)
                release_gemini_slot()
                slot_held = False
                time.sleep(delay)
                continue
            elif retryable:
                raise RuntimeError(
                    f"Gemini call failed after {eff_max_retries} retries due to {err_code} [caller={call_name}]: {exc}"
                ) from exc
            raise
        finally:
            if slot_held:
                release_gemini_slot()


def generate_content(
    client: Any = None,
    model: str = "",
    contents: Any = None,
    config: Any = None,
    caller: Optional[str] = None,
    timeout_s: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> Any:
    """Backwards-compatibility alias for generate()."""
    if isinstance(client, str) and not model:
        model = client
        contents = contents
    call_name = caller or ""
    return generate(
        model=model,
        contents=contents,
        config=config,
        caller=call_name,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )


class ColosseumGemini(Gemini):
    """ADK Gemini model subclass with concurrency control, timeout, and 429/503/504 retry backoff."""

    def __init__(
        self,
        caller: str = "adviser",
        on_first_slot: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._caller = caller
        self._on_first_slot = on_first_slot
        self._first_slot_notified = False

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[Any, None]:
        caller = self._caller
        settings = get_settings()
        eff_timeout_s = float(getattr(settings.gemini, "timeout_s", 60.0))
        eff_max_retries = int(getattr(settings.gemini, "max_retries", 3))
        delays = getattr(settings.gemini, "retry_backoff", [2.0, 8.0, 20.0])
        attempt = 0

        while True:
            await acquire_gemini_slot_async()
            if not self._first_slot_notified:
                self._first_slot_notified = True
                if self._on_first_slot:
                    try:
                        self._on_first_slot()
                    except Exception as e:
                        logger.warning("Error in on_first_slot callback: %s", e)
            slot_held = True
            try:
                collected = []
                async def _run_stream():
                    async for r in super(ColosseumGemini, self).generate_content_async(
                        llm_request, stream=stream
                    ):
                        collected.append(r)

                await asyncio.wait_for(_run_stream(), timeout=eff_timeout_s)
                for r in collected:
                    yield r
                return
            except Exception as exc:
                retryable, err_code = is_retryable_error(exc)
                if retryable and attempt < eff_max_retries:
                    attempt += 1
                    record_retry(caller)
                    print(f"[gemini] {err_code} retry {attempt}/{eff_max_retries} caller={caller}", flush=True)
                    logger.warning("[gemini] %s retry %d/%d caller=%s", err_code, attempt, eff_max_retries, caller)
                    delay = (delays[attempt - 1] if attempt - 1 < len(delays) else delays[-1]) + random.uniform(0.1, 1.0)
                    release_gemini_slot()
                    slot_held = False
                    await asyncio.sleep(delay)
                    continue
                elif retryable:
                    raise RuntimeError(
                        f"Gemini call failed after {eff_max_retries} retries due to {err_code} [caller={caller}]: {exc}"
                    ) from exc
                raise
            finally:
                if slot_held:
                    release_gemini_slot()
