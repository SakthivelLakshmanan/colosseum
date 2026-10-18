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
import weakref

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

# Loop-bound genai Client instances for async calls
_async_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, genai.Client] = (
    weakref.WeakKeyDictionary()
)
_async_client_lock = threading.Lock()


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


# Tracking of models actually used per caller
_models_used: dict[str, str] = {}
_models_used_lock = threading.Lock()


class CallerStats:
    """Per-caller Gemini execution statistics."""

    def __init__(self) -> None:
        self.call_count: int = 0
        self.total_time_s: float = 0.0
        self.slowest_time_s: float = 0.0


_caller_stats: dict[str, CallerStats] = defaultdict(CallerStats)
_caller_stats_lock = threading.Lock()


def record_call_timing(caller: str, duration_s: float) -> None:
    """Record duration of a Gemini call for a caller."""
    if caller:
        with _caller_stats_lock:
            st = _caller_stats[caller]
            st.call_count += 1
            st.total_time_s += duration_s
            if duration_s > st.slowest_time_s:
                st.slowest_time_s = duration_s


def get_caller_stats(caller: str) -> tuple[int, float, float]:
    """Return (call_count, total_time_s, slowest_time_s) for a caller."""
    with _caller_stats_lock:
        st = _caller_stats.get(caller)
        if st is None:
            return 0, 0.0, 0.0
        return st.call_count, st.total_time_s, st.slowest_time_s


def reset_caller_stats(caller: Optional[str] = None) -> None:
    """Reset caller stats for one or all callers."""
    with _caller_stats_lock:
        if caller:
            _caller_stats.pop(caller, None)
        else:
            _caller_stats.clear()


def record_model_used(caller: str, model: str) -> None:
    """Record which model was actually used by a caller."""
    if caller:
        with _models_used_lock:
            _models_used[caller] = model


def get_model_used(caller: str) -> Optional[str]:
    """Get the model last used by a caller."""
    with _models_used_lock:
        return _models_used.get(caller)


def get_all_models_used() -> dict[str, str]:
    """Return dictionary of caller -> model for all recorded calls."""
    with _models_used_lock:
        return dict(_models_used)


def reset_models_used() -> None:
    """Reset the recorded models dictionary."""
    with _models_used_lock:
        _models_used.clear()


def _build_thinking_config(level: str) -> Optional[Any]:
    """Build types.ThinkingConfig if supported by the installed google-genai version."""
    if not hasattr(types, "ThinkingConfig"):
        return None
    lvl_lower = (level or "").lower().strip()
    try:
        try:
            return types.ThinkingConfig(thinking_level=lvl_lower)
        except (TypeError, ValueError):
            pass

        budget_map = {"low": 1024, "medium": 4096, "high": 8192, "off": 0}
        budget = budget_map.get(lvl_lower, 1024)
        try:
            return types.ThinkingConfig(thinking_budget=budget)
        except (TypeError, ValueError):
            pass

        try:
            return types.ThinkingConfig(budget=budget)
        except (TypeError, ValueError):
            pass

        return None
    except Exception as exc:
        logger.debug("ThinkingConfig not supported: %s", exc)
        return None


def get_thinking_config(caller: str = "", model: str = "", settings: Optional[Any] = None) -> Optional[Any]:
    """Return ThinkingConfig for the caller/model based on settings."""
    if settings is None:
        settings = get_settings()

    thinking_cfg = getattr(settings.gemini, "thinking", None)
    if thinking_cfg is None:
        return None

    caller_lower = (caller or "").lower()
    model_lower = (model or "").lower()
    reasoning_model = getattr(getattr(settings, "models", None), "reasoning", "")
    is_pro = "pro" in model_lower or (bool(reasoning_model) and model == reasoning_model)

    # medium for moderator, Scribe and final check
    is_medium = (
        is_pro
        or "moderator" in caller_lower
        or "scribe" in caller_lower
        or "final_check" in caller_lower
        or "checking" in caller_lower
    )
    # low for adviser, routing and summary calls
    level = getattr(thinking_cfg, "pro", "medium") if is_medium else getattr(thinking_cfg, "flash", "low")
    return _build_thinking_config(level)


def get_model_timeout_s(model: str = "", settings: Optional[Any] = None) -> float:
    """Return the timeout in seconds for a model (flash: 90s, pro: 240s)."""
    if settings is None:
        settings = get_settings()

    timeout_cfg = getattr(settings.gemini, "timeout_s", None)
    if isinstance(timeout_cfg, (int, float)):
        return float(timeout_cfg)

    m_lower = (model or "").lower()
    reasoning_model = getattr(getattr(settings, "models", None), "reasoning", "")
    is_pro = "pro" in m_lower or (bool(reasoning_model) and model == reasoning_model)

    if hasattr(timeout_cfg, "pro") and hasattr(timeout_cfg, "flash"):
        return float(timeout_cfg.pro if is_pro else timeout_cfg.flash)
    elif isinstance(timeout_cfg, dict):
        return float(timeout_cfg.get("pro", 240.0) if is_pro else timeout_cfg.get("flash", 90.0))

    return 240.0 if is_pro else 90.0


def _build_http_options(timeout_s: float) -> types.HttpOptions:
    """Build HttpOptions with millisecond timeout and 1 attempt (disabling SDK internal retries)."""
    timeout_ms = int(timeout_s * 1000)
    retry_options = None
    if hasattr(types, "HttpRetryOptions"):
        try:
            retry_options = types.HttpRetryOptions(attempts=1)
        except Exception:
            pass
    if retry_options is not None:
        return types.HttpOptions(timeout=timeout_ms, retry_options=retry_options)
    return types.HttpOptions(timeout=timeout_ms)


def reset_genai_client() -> None:
    """Reset the shared client instance (useful in tests)."""
    global _genai_client
    with _client_lock:
        _genai_client = None
    with _async_client_lock:
        _async_clients.clear()


def _get_async_client(caller: Optional[str] = None) -> genai.Client:
    """Return a google-genai Client cached per running event loop.

    Each event loop gets its own client to avoid closed event loop errors
    from async connection pools (httpx/httpcore).
    """
    loop = asyncio.get_running_loop()
    with _async_client_lock:
        client = _async_clients.get(loop)
        if client is None:
            settings = get_settings()
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
            os.environ["GOOGLE_CLOUD_PROJECT"] = settings.project_id
            os.environ["GOOGLE_CLOUD_LOCATION"] = settings.models_location

            default_timeout = get_model_timeout_s("", settings)
            http_opts = _build_http_options(default_timeout)
            client = genai.Client(
                vertexai=True,
                project=settings.project_id,
                location=settings.models_location,
                http_options=http_opts,
            )
            _async_clients[loop] = client

    if caller:
        current_caller.set(caller)
    return client


def get_genai_client(caller: Optional[str] = None) -> genai.Client:
    """Initialize or return the shared google-genai Vertex AI client.
    
    Sets timeout from settings and disables SDK internal silent retries
    so rate limits are surfaced to our shared wrapper.
    """
    global _genai_client
    with _client_lock:
        if _genai_client is None:
            settings = get_settings()
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
            os.environ["GOOGLE_CLOUD_PROJECT"] = settings.project_id
            os.environ["GOOGLE_CLOUD_LOCATION"] = settings.models_location

            default_timeout = get_model_timeout_s("", settings)
            http_opts = _build_http_options(default_timeout)
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
    fallback, semaphore and logging logic. Every caller uses this function.
    """
    settings = get_settings()
    eff_timeout_s = timeout_s if timeout_s is not None else get_model_timeout_s(model, settings)
    eff_max_retries = max_retries if max_retries is not None else int(getattr(settings.gemini, "max_retries", 3))
    delays = getattr(settings.gemini, "retry_backoff", [2.0, 8.0, 20.0])

    call_name = caller or current_caller.get()
    client = get_genai_client(caller=call_name)

    http_opts = _build_http_options(eff_timeout_s)
    call_config = config
    if isinstance(config, dict):
        call_config = dict(config)
        call_config["http_options"] = http_opts
    elif hasattr(config, "http_options"):
        try:
            config.http_options = http_opts
            call_config = config
        except Exception:
            pass
    elif config is None:
        call_config = types.GenerateContentConfig(http_options=http_opts)

    # Attach thinking config based on caller/model if supported
    thinking_cfg = get_thinking_config(caller=call_name, model=model, settings=settings)
    if thinking_cfg is not None:
        if isinstance(call_config, dict) and "thinking_config" not in call_config:
            call_config["thinking_config"] = thinking_cfg
        elif hasattr(call_config, "thinking_config"):
            if getattr(call_config, "thinking_config", None) is None:
                try:
                    call_config.thinking_config = thinking_cfg
                except Exception:
                    pass

    attempt = 0
    reasoning_model = getattr(getattr(settings, "models", None), "reasoning", "")
    is_pro = "pro" in (model or "").lower() or (bool(reasoning_model) and model == reasoning_model)

    while True:
        acquire_gemini_slot_sync()
        slot_held = True
        t_call_start = time.time()
        try:
            future = _sync_executor.submit(
                client.models.generate_content,
                model=model,
                contents=contents,
                config=call_config,
            )
            res = future.result(timeout=eff_timeout_s)
            call_dur = time.time() - t_call_start
            record_call_timing(call_name, call_dur)
            record_model_used(call_name, model)
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
            elif retryable and is_pro:
                release_gemini_slot()
                slot_held = False
                flash_model = getattr(getattr(settings, "models", None), "adviser", "gemini-3.8-flash")
                print(f"[gemini] fallback pro->flash caller={call_name}", flush=True)
                logger.warning("[gemini] fallback pro->flash caller=%s", call_name)

                fallback_timeout_s = get_model_timeout_s(flash_model, settings)
                fallback_http_opts = _build_http_options(fallback_timeout_s)
                fallback_config = config
                if isinstance(config, dict):
                    fallback_config = dict(config)
                    fallback_config["http_options"] = fallback_http_opts
                elif hasattr(config, "http_options"):
                    try:
                        fallback_config = config
                        fallback_config.http_options = fallback_http_opts
                    except Exception:
                        pass
                elif config is None:
                    fallback_config = types.GenerateContentConfig(http_options=fallback_http_opts)

                fb_thinking = get_thinking_config(caller=call_name, model=flash_model, settings=settings)
                if fb_thinking is not None:
                    if isinstance(fallback_config, dict) and "thinking_config" not in fallback_config:
                        fallback_config["thinking_config"] = fb_thinking
                    elif hasattr(fallback_config, "thinking_config"):
                        if getattr(fallback_config, "thinking_config", None) is None:
                            try:
                                fallback_config.thinking_config = fb_thinking
                            except Exception:
                                pass

                acquire_gemini_slot_sync()
                slot_held = True
                t_fb_start = time.time()
                try:
                    fb_future = _sync_executor.submit(
                        client.models.generate_content,
                        model=flash_model,
                        contents=contents,
                        config=fallback_config,
                    )
                    fb_res = fb_future.result(timeout=fallback_timeout_s)
                    fb_dur = time.time() - t_fb_start
                    record_call_timing(call_name, fb_dur)
                    record_model_used(call_name, flash_model)
                    return fb_res
                except Exception as fb_exc:
                    raise RuntimeError(
                        f"Gemini call failed after {eff_max_retries} retries and fallback to {flash_model} failed [caller={call_name}]: {fb_exc}"
                    ) from fb_exc
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
        kwargs.pop("client", None)
        super().__init__(**kwargs)
        self._caller = caller
        self._on_first_slot = on_first_slot
        self._first_slot_notified = False

    @property
    def api_client(self) -> genai.Client:
        try:
            return _get_async_client(caller=self._caller)
        except RuntimeError:
            return get_genai_client(caller=self._caller)

    @property
    def _live_api_client(self) -> genai.Client:
        try:
            return _get_async_client(caller=self._caller)
        except RuntimeError:
            return get_genai_client(caller=self._caller)

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[Any, None]:
        caller = self._caller
        settings = get_settings()
        eff_timeout_s = get_model_timeout_s(getattr(self, "model", ""), settings)
        eff_max_retries = int(getattr(settings.gemini, "max_retries", 3))
        delays = getattr(settings.gemini, "retry_backoff", [2.0, 8.0, 20.0])
        attempt = 0

        # Attach low thinking config for adviser calls if supported
        thinking_cfg = get_thinking_config(caller=caller, model=getattr(self, "model", ""), settings=settings)
        if thinking_cfg is not None and hasattr(llm_request, "config") and llm_request.config is not None:
            if hasattr(llm_request.config, "thinking_config") and getattr(llm_request.config, "thinking_config", None) is None:
                try:
                    llm_request.config.thinking_config = thinking_cfg
                except Exception:
                    pass

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
            t_call_start = time.time()
            try:
                collected = []
                async def _run_stream():
                    async for r in super(ColosseumGemini, self).generate_content_async(
                        llm_request, stream=stream
                    ):
                        collected.append(r)

                await asyncio.wait_for(_run_stream(), timeout=eff_timeout_s)
                call_dur = time.time() - t_call_start
                record_call_timing(caller, call_dur)
                adv_model = getattr(self, "model", getattr(settings.models, "adviser", "gemini-3.8-flash"))
                record_model_used(caller, adv_model)
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
