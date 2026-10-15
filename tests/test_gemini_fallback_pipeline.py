"""Unit tests for Gemini Pro->Flash fallback and pipeline failure resilience."""
from __future__ import annotations

import concurrent.futures
from unittest.mock import MagicMock, patch
import pytest

from agents.pipeline import run_decision
from core.gemini import (
    _build_http_options,
    generate,
    get_model_timeout_s,
    get_model_used,
    record_model_used,
    reset_models_used,
)
from core.schemas import (
    AdviserPosition,
    AdviserScore,
    Criterion,
    DecisionBrief,
    EvidencePack,
    RoutingDecision,
    RunRecord,
    Scorecard,
    ScorecardRow,
)
from core.settings import get_settings


@pytest.fixture(autouse=True)
def clean_model_tracking():
    reset_models_used()
    yield
    reset_models_used()


@pytest.fixture
def sample_brief():
    return DecisionBrief(
        decision_id="DEC-TEST-FB",
        question="Should we migrate to vendor B?",
        options={"A": "Stay on A", "B": "Migrate to B"},
        constraints=[],
        criteria=[
            Criterion(
                name="Cost",
                weight=1.0,
                owners=["finance"],
                good_looks_like="Lower cost",
            ),
        ],
    )


def test_gemini_timeouts_by_model_and_http_options():
    """Verify that model timeouts are 90s for flash and 240s for pro, passed in ms via http_options."""
    settings = get_settings()

    flash_timeout = get_model_timeout_s("gemini-3.8-flash", settings)
    pro_timeout = get_model_timeout_s("gemini-3.1-pro-preview", settings)

    assert flash_timeout == 90.0
    assert pro_timeout == 240.0

    flash_opts = _build_http_options(flash_timeout)
    pro_opts = _build_http_options(pro_timeout)

    assert flash_opts.timeout == 90000
    assert pro_opts.timeout == 240000

    if hasattr(flash_opts, "retry_options") and flash_opts.retry_options:
        assert flash_opts.retry_options.attempts == 1


def test_gemini_pro_to_flash_fallback(caplog):
    """If a gemini-3.1-pro call still fails after its retries, it tries once with gemini-3.8-flash,
    logs [gemini] fallback pro->flash caller=<name>, and records the model used.
    """
    settings = get_settings()
    pro_model = settings.models.reasoning
    flash_model = settings.models.adviser

    mock_client = MagicMock()

    call_records = []

    def mock_generate_content(model, contents, config=None):
        call_records.append(model)
        if "pro" in model:
            raise concurrent.futures.TimeoutError("504 DEADLINE_EXCEEDED")
        elif "flash" in model:
            mock_res = MagicMock()
            mock_res.text = "Success from Flash fallback"
            return mock_res
        raise ValueError(f"Unexpected model: {model}")

    mock_client.models.generate_content.side_effect = mock_generate_content

    with patch("core.gemini.get_genai_client", return_value=mock_client), \
         patch("time.sleep", return_value=None):

        result = generate(
            model=pro_model,
            contents="Write a decision memo",
            caller="scribe",
            max_retries=2,
        )

    # Pro model was tried (initial + 2 retries = 3 attempts), followed by Flash fallback
    assert call_records == [pro_model, pro_model, pro_model, flash_model]
    assert result.text == "Success from Flash fallback"

    # Verify model_used recorded flash_model
    assert get_model_used("scribe") == flash_model

    # Verify log output
    assert any("fallback pro->flash caller=scribe" in record.message for record in caplog.records)


def test_pipeline_failing_scribe_returns_failed_without_raising(sample_brief):
    """When the Scribe stage fails, the pipeline sets run status to failed with the stage name
    and error, saves it to ledger, and returns a RunRecord with status=failed without raising.
    """
    routing = RoutingDecision(
        decision_id="DEC-TEST-FB",
        routing_query="costs",
        scores=[
            AdviserScore(
                adviser_id="finance",
                similarity=0.9,
                best_matching_capability="Costs",
                selected=True,
                reason="match",
            )
        ],
        selected=["finance"],
        created_at="2026-10-15T10:00:00Z",
    )

    pack = EvidencePack(
        adviser_id="finance",
        facts=[],
        summaries=[],
        total_matching_facts=2,
        truncated=False,
    )

    pos = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Finance position",
        claims=[],
        criterion_scores=[],
        confidence=0.9,
    )

    scorecard = Scorecard(
        rows=[
            ScorecardRow(
                criterion="Cost",
                weight=1.0,
                scores={"A": 3.0, "B": 4.0},
                evidence_ids=["FIN-01"],
            )
        ],
        weighted_totals={"A": 3.0, "B": 4.0},
        blocked_options={},
    )

    saved_sections = {}
    updated_statuses = []

    def mock_save_section(run_id, section, data):
        saved_sections[section] = data

    def mock_update_status(run_id, status, progress_note=None):
        updated_statuses.append((status, progress_note))

    with patch("agents.pipeline.route_decision", return_value=routing), \
         patch("agents.pipeline.build_pack", return_value=pack), \
         patch("agents.pipeline.run_advisers_sync", return_value={"finance": pos}), \
         patch("agents.pipeline.check_claims", return_value=[]), \
         patch("agents.pipeline.build_scorecard", return_value=scorecard), \
         patch("agents.pipeline.write_memo", side_effect=RuntimeError("504 DEADLINE_EXCEEDED after retries")), \
         patch("core.ledger.create_run"), \
         patch("core.ledger.save_routing"), \
         patch("core.ledger.update_status", side_effect=mock_update_status), \
         patch("core.ledger.save_section", side_effect=mock_save_section):

        record = run_decision(brief=sample_brief, run_id="run_test_scribe_fail")

    # Pipeline MUST return a RunRecord and NOT raise
    assert isinstance(record, RunRecord)
    assert record.run_id == "run_test_scribe_fail"
    assert record.status == "failed"
    assert "writing" in (record.error or "")
    assert "504 DEADLINE_EXCEEDED" in (record.error or "")

    # Check ledger recorded failure
    statuses_only = [s[0] for s in updated_statuses]
    assert "failed" in statuses_only

    assert "error" in saved_sections
    assert "writing" in saved_sections["error"]
    assert "failed_stage" in saved_sections
    assert saved_sections["failed_stage"] == "writing"

    # Check model_used per stage saved on run document
    assert "model_used" in saved_sections
    assert isinstance(saved_sections["model_used"], dict)
    assert "routing" in saved_sections["model_used"]


def test_evals_outcome_counts_failed_run_as_zero_of_eight(tmp_path):
    """run_evals.py outcome must count a failed run as 0/8 and continue without crashing."""
    from evals.run_evals import evaluate_outcome

    failed_record = RunRecord(
        run_id="run_eval_fail_1",
        brief=DecisionBrief(
            decision_id="DEC-001",
            question="Sample question?",
            options={"A": "Option A", "B": "Option B"},
            constraints=[],
            criteria=[],
        ),
        status="failed",
        error="Stage 'writing' failed: 504 DEADLINE_EXCEEDED",
        memo_versions=[],
        final_verdicts=[],
    )

    with patch("evals.run_evals.run_decision", return_value=failed_record):
        report = evaluate_outcome(
            brief_path=tmp_path / "dummy.json",
            num_runs=1,
            runs_dir=tmp_path,
            pause_s=0,
        )

    assert "FAILED" in report
    assert "0/8" in report
    assert "0.0%" in report
