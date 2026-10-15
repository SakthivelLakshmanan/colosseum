"""Unit tests for adviser concurrency, timeout clock, multi-thread pipelines, and Gemini settings."""
import asyncio
import threading
import time
from unittest.mock import MagicMock, patch
import pytest

from agents.adviser_factory import (
    build_adviser,
    get_last_adviser_durations,
    get_last_adviser_slot_waits,
    get_last_adviser_tool_counts,
    run_advisers,
    run_advisers_sync,
)
from core.gemini import (
    ColosseumGemini,
    acquire_gemini_slot_async,
    generate,
    get_semaphore,
    release_gemini_slot,
    reset_semaphore,
)
from core.schemas import AdviserPosition, DecisionBrief, EvidencePack
from core.settings import get_settings, load_settings


def _norm(cfg):
    if cfg is None:
        return None
    lvl = getattr(cfg, "thinking_level", None)
    if lvl is not None:
        return str(getattr(lvl, "value", lvl)).lower()
    budget = getattr(cfg, "thinking_budget", None)
    if budget is None:
        budget = getattr(cfg, "budget", None)
    return None if budget is None else int(budget)


def test_settings_concurrency_and_timeouts():
    """Verify that settings.yaml has max_concurrent=6, advisers.timeout_s=420, etc."""
    settings = load_settings()
    assert settings.gemini.max_concurrent == 6
    assert settings.advisers.timeout_s == 420.0
    assert settings.gemini.timeout_s.flash == 90.0
    assert settings.gemini.timeout_s.pro == 240.0
    assert settings.gemini.max_retries == 3
    assert settings.gemini.retry_backoff == [2.0, 8.0, 20.0]
    assert settings.run.adviser_timeout_s == 420
    assert settings.advisers.max_tool_calls == 4
    assert settings.gemini.thinking.flash == "low"
    assert settings.gemini.thinking.pro == "medium"


@pytest.mark.asyncio
async def test_colosseum_gemini_first_slot_callback():
    """Verify that ColosseumGemini invokes on_first_slot once when acquiring its first slot."""
    reset_semaphore()
    slot_calls = []

    def on_slot():
        slot_calls.append(time.time())

    model = ColosseumGemini(caller="test_adviser", on_first_slot=on_slot)

    async def fake_stream(req, stream=False):
        yield MagicMock(text="fake response")

    with patch.object(ColosseumGemini, "generate_content_async", wraps=model.generate_content_async):
        with patch("google.adk.models.google_llm.Gemini.generate_content_async", side_effect=fake_stream):
            # First call triggers callback
            res1 = [r async for r in model.generate_content_async(MagicMock())]
            assert len(slot_calls) == 1
            assert len(res1) == 1

            # Second call does not trigger callback again
            res2 = [r async for r in model.generate_content_async(MagicMock())]
            assert len(slot_calls) == 1
            assert len(res2) == 1


@pytest.mark.asyncio
async def test_run_advisers_timeout_starts_at_slot_acquisition(capsys):
    """Verify that an adviser's timeout clock starts when it gets a slot, finishing under 2s."""
    brief = DecisionBrief(
        decision_id="DEC-TEST",
        question="Should we choose Option A or B?",
        options={"A": "Option A", "B": "Option B"},
        constraints=[],
        criteria=[],
    )
    fake_pack = EvidencePack(
        adviser_id="finance",
        facts=[],
        summaries=[],
        total_matching_facts=0,
        truncated=False,
    )
    fake_pos = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Finance summary",
        claims=[],
        criterion_scores=[],
        confidence=0.9,
    )

    async def mock_run_adviser(aid, br, pk, on_first_slot=None):
        # Queuing delay before getting a slot: 0.3s
        await asyncio.sleep(0.3)
        if on_first_slot:
            on_first_slot()
        # Adviser work after slot acquisition: 0.3s
        await asyncio.sleep(0.3)
        return fake_pos

    # Patch settings to 0.5s timeout.
    # Total time from launch is 0.3s + 0.3s = 0.6s.
    # If timeout started at launch, 0.6s > 0.5s timeout would fail.
    # Because timeout starts at slot acquisition, 0.3s work < 0.5s timeout, so it succeeds.
    mock_settings = MagicMock()
    mock_settings.advisers.timeout_s = 0.5
    mock_settings.run.adviser_timeout_s = 0.5

    with patch("agents.adviser_factory.get_settings", return_value=mock_settings), \
         patch("agents.adviser_factory.build_pack", return_value=fake_pack), \
         patch("agents.adviser_factory.run_adviser_async", side_effect=mock_run_adviser):

        t0 = time.time()
        results = await run_advisers(["finance"], brief=brief, packs={"finance": fake_pack})
        elapsed = time.time() - t0

    assert elapsed < 2.0, f"Test took {elapsed}s, expected under 2.0s"
    assert "finance" in results
    assert isinstance(results["finance"], AdviserPosition)

    # Check stdout log format: [adviser] finance done in 1s, gemini_calls=0, slowest=0s
    captured = capsys.readouterr().out
    assert "[adviser] finance done in" in captured
    assert "gemini_calls=" in captured
    assert "slowest=" in captured


@pytest.mark.asyncio
async def test_run_advisers_times_out_after_slot_acquisition():
    """Verify that an adviser times out if it exceeds effective_timeout after acquiring its slot."""
    brief = DecisionBrief(
        decision_id="DEC-TEST",
        question="Test",
        options={"A": "A", "B": "B"},
        constraints=[],
        criteria=[],
    )
    fake_pack = EvidencePack(
        adviser_id="finance",
        facts=[],
        summaries=[],
        total_matching_facts=0,
        truncated=False,
    )

    async def mock_slow_adviser(aid, br, pk, on_first_slot=None):
        if on_first_slot:
            on_first_slot()
        # Sleep longer than the 0.3s timeout
        await asyncio.sleep(1.0)
        return MagicMock()

    mock_settings = MagicMock()
    mock_settings.advisers.timeout_s = 0.3
    mock_settings.run.adviser_timeout_s = 0.3

    with patch("agents.adviser_factory.get_settings", return_value=mock_settings), \
         patch("agents.adviser_factory.build_pack", return_value=fake_pack), \
         patch("agents.adviser_factory.run_adviser_async", side_effect=mock_slow_adviser):

        t0 = time.time()
        results = await run_advisers(["finance"], brief=brief, packs={"finance": fake_pack})
        elapsed = time.time() - t0

    assert elapsed < 2.0, f"Test took {elapsed}s, expected under 2.0s"
    assert "finance" in results
    assert isinstance(results["finance"], str)
    assert "Timeout after 0s" in results["finance"] or "Timeout" in results["finance"]


def test_two_pipelines_adviser_stages_from_two_threads():
    """Verify that two pipelines can run their adviser stages simultaneously from two threads.

    Proves that the global Gemini BoundedSemaphore and adviser runners work seamlessly
    across different threads and separate event loops without deadlocking.
    """
    reset_semaphore()

    brief1 = DecisionBrief(
        decision_id="DEC-P1",
        question="Pipeline 1 Question",
        options={"A": "Option A", "B": "Option B"},
        constraints=[],
        criteria=[],
    )
    brief2 = DecisionBrief(
        decision_id="DEC-P2",
        question="Pipeline 2 Question",
        options={"A": "Option A", "B": "Option B"},
        constraints=[],
        criteria=[],
    )

    fake_pack1 = EvidencePack(adviser_id="finance", facts=[], summaries=[], total_matching_facts=0, truncated=False)
    fake_pack2 = EvidencePack(adviser_id="legal", facts=[], summaries=[], total_matching_facts=0, truncated=False)

    async def fake_run_adviser(aid, brief, pack, on_first_slot=None):
        # Acquire slot via real async semaphore acquisition across threads
        await acquire_gemini_slot_async()
        try:
            if on_first_slot:
                on_first_slot()
            await asyncio.sleep(0.1)  # fake work 100ms
            return AdviserPosition(
                adviser_id=aid,
                stance="conditional",
                summary=f"{aid} summary for {brief.decision_id}",
                claims=[],
                criterion_scores=[],
                confidence=0.9,
            )
        finally:
            release_gemini_slot()

    thread1_results = {}
    thread2_results = {}
    errors = []

    def run_thread1():
        try:
            res = run_advisers_sync(
                ["finance"],
                brief=brief1,
                packs={"finance": fake_pack1},
                timeout_s=2.0,
            )
            thread1_results.update(res)
        except Exception as e:
            errors.append(("thread1", e))

    def run_thread2():
        try:
            res = run_advisers_sync(
                ["legal"],
                brief=brief2,
                packs={"legal": fake_pack2},
                timeout_s=2.0,
            )
            thread2_results.update(res)
        except Exception as e:
            errors.append(("thread2", e))

    with patch("agents.adviser_factory.run_adviser_async", side_effect=fake_run_adviser), \
         patch("agents.adviser_factory.build_pack", return_value=fake_pack1):

        t0 = time.time()
        th1 = threading.Thread(target=run_thread1, name="pipeline_thread_1")
        th2 = threading.Thread(target=run_thread2, name="pipeline_thread_2")

        th1.start()
        th2.start()

        th1.join(timeout=3.0)
        th2.join(timeout=3.0)
        total_time = time.time() - t0

    assert not th1.is_alive(), "Thread 1 hung or did not finish"
    assert not th2.is_alive(), "Thread 2 hung or did not finish"
    assert not errors, f"Errors encountered in threads: {errors}"
    assert total_time < 2.0, f"Execution took {total_time}s, expected < 2s"

    assert "finance" in thread1_results
    assert isinstance(thread1_results["finance"], AdviserPosition)
    assert thread1_results["finance"].summary == "finance summary for DEC-P1"

    assert "legal" in thread2_results
    assert isinstance(thread2_results["legal"], AdviserPosition)
    assert thread2_results["legal"].summary == "legal summary for DEC-P2"


def test_gemini_retries_capped_at_three():
    """Verify that generate caps retries at 3 with exponential backoff from settings."""
    from core.gemini import generate

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("429 Resource Exhausted")

    with patch("core.gemini.get_genai_client", return_value=mock_client), \
         patch("time.sleep") as mock_sleep:
        with pytest.raises(RuntimeError, match="Gemini call failed after 3 retries"):
            generate(model="fake-model", contents="test", caller="test_caller", timeout_s=10.0, max_retries=3)

        # Retried exactly 3 times
        assert mock_sleep.call_count == 3


def test_tool_call_limiter_turns_off_tools_at_budget():
    """Verify that ToolCallLimiter turns off agent tools at max_calls and blocks further calls."""
    from agents.adviser_factory import ToolCallLimiter

    mock_agent = MagicMock()
    mock_agent.tools = ["tool1", "tool2", "tool3", "tool4"]
    limiter = ToolCallLimiter(adviser_id="finance", max_calls=4, agent=mock_agent)

    # First 3 calls succeed
    for _ in range(3):
        res = limiter.before_tool_callback("tool1", {})
        assert res is None
        assert len(mock_agent.tools) == 4

    # 4th call reaches budget and turns off tools
    res4 = limiter.before_tool_callback("tool1", {})
    assert res4 is None
    assert mock_agent.tools == []

    # 5th call exceeds budget and returns instruction dict
    res5 = limiter.before_tool_callback("tool1", {})
    assert isinstance(res5, dict)
    assert res5.get("error") == "Tool limit reached"
    assert "Tools are now turned off" in res5.get("message", "")


@pytest.mark.asyncio
async def test_adviser_graceful_deadline_at_75_percent(capsys):
    """When an adviser reaches 75% of its timeout, tools are turned off and final position is returned."""
    from core.gemini import record_call_timing, reset_caller_stats

    brief = DecisionBrief(
        decision_id="DEC-TEST-GRACE",
        question="Migrate?",
        options={"A": "A", "B": "B"},
        constraints=[],
        criteria=[],
    )
    fake_pack = EvidencePack(adviser_id="finance", facts=[], summaries=[], total_matching_facts=0, truncated=False)
    grace_pos = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Graceful position from existing evidence",
        claims=[],
        criterion_scores=[],
        confidence=0.85,
    )

    async def mock_slow_initial_adviser(aid, br, pk, on_first_slot=None):
        if on_first_slot:
            on_first_slot()
        # Takes longer than 75% of 0.4s (0.3s)
        await asyncio.sleep(1.0)
        return MagicMock()

    async def mock_grace_final_call(aid, brief, pack, timeout_s):
        # Record a gemini call timing
        record_call_timing(f"{aid}_adviser", 0.05)
        return grace_pos

    mock_settings = MagicMock()
    mock_settings.advisers.timeout_s = 0.4
    mock_settings.run.adviser_timeout_s = 0.4

    with patch("agents.adviser_factory.get_settings", return_value=mock_settings), \
         patch("agents.adviser_factory.build_pack", return_value=fake_pack), \
         patch("agents.adviser_factory.run_adviser_async", side_effect=mock_slow_initial_adviser), \
         patch("agents.adviser_factory._request_final_position_without_tools", side_effect=mock_grace_final_call):

        t0 = time.time()
        results = await run_advisers(["finance"], brief=brief, packs={"finance": fake_pack})
        elapsed = time.time() - t0

    assert elapsed < 1.0, f"Expected under 1s, got {elapsed}s"
    assert "finance" in results
    assert isinstance(results["finance"], AdviserPosition)
    assert results["finance"].summary == "Graceful position from existing evidence"

    # Verify log format: [adviser] finance done in Xs, gemini_calls=1, slowest=0s
    captured = capsys.readouterr().out
    assert "[adviser] finance done in" in captured
    assert "gemini_calls=1" in captured
    assert "slowest=" in captured


def test_thinking_levels_configuration():
    """Verify low thinking level for adviser, routing, summary; medium for moderator, Scribe, final check."""
    from google.genai import types
    from core.gemini import get_thinking_config

    # Low thinking level for adviser, router, aggregator/summary
    adv_cfg = get_thinking_config(caller="finance_adviser", model="gemini-3.8-flash")
    router_cfg = get_thinking_config(caller="router", model="gemini-3.8-flash")
    summary_cfg = get_thinking_config(caller="aggregator_finance", model="gemini-3.8-flash")

    # Medium thinking level for moderator, Scribe, final check
    mod_cfg = get_thinking_config(caller="moderator", model="gemini-3.1-pro-preview")
    scribe_cfg = get_thinking_config(caller="scribe", model="gemini-3.1-pro-preview")
    check_cfg = get_thinking_config(caller="final_check", model="gemini-3.1-pro-preview")

    # If google-genai supports ThinkingConfig, configs must be present
    if hasattr(types, "ThinkingConfig"):
        assert adv_cfg is not None
        assert mod_cfg is not None

    # Low budget is 1024, medium budget is 4096 (as defined in core/gemini.py)
    if adv_cfg is not None:
        assert _norm(adv_cfg) in ("low", 1024)
        assert _norm(router_cfg) in ("low", 1024)
        assert _norm(summary_cfg) in ("low", 1024)
        # Verify test fails if an adviser gets medium thinking
        assert _norm(adv_cfg) not in ("medium", 4096)

    if mod_cfg is not None:
        assert _norm(mod_cfg) in ("medium", 4096)
        assert _norm(scribe_cfg) in ("medium", 4096)
        assert _norm(check_cfg) in ("medium", 4096)
        # Verify test fails if Scribe gets low thinking
        assert _norm(scribe_cfg) not in ("low", 1024)


