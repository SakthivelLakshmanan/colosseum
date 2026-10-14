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


def test_settings_concurrency_and_timeouts():
    """Verify that settings.yaml has max_concurrent=6, advisers.timeout_s=420, etc."""
    settings = load_settings()
    assert settings.gemini.max_concurrent == 6
    assert settings.advisers.timeout_s == 420.0
    assert settings.gemini.timeout_s == 60.0
    assert settings.gemini.max_retries == 3
    assert settings.gemini.retry_backoff == [2.0, 8.0, 20.0]
    assert settings.run.adviser_timeout_s == 420


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

    # Check stdout log format: [adviser] finance done in 1s (waited 0s), tool_calls=0
    captured = capsys.readouterr().out
    assert "[adviser] finance done in" in captured
    assert "(waited 0s)" in captured
    assert "tool_calls=0" in captured


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
