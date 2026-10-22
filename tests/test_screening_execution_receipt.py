"""Actual screening-loop diagnostics; synthetic in-memory inputs, no providers."""
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import pytest

from src.services.screening import pipeline
from src.services.screening.config import Config
from src.services.screening.models import HardFilterConfig, ScreeningConfig, Strategy


def _run(monkeypatch, *, minimum=300.0, daily=False, cap=2, fail_daily=False,
         dsa=False, reject_daily=False):
    frame = pd.DataFrame([
        {"code": f"60000{i}", "name": f"SYNTHETIC {i}", "price": 10.0,
         "change_pct": 0.0, "amount": float((i + 1) * 100)}
        for i in range(6)
    ])
    frame.attrs.update(snapshot_source="SYNTHETIC", source_errors=[])
    strategy = Strategy(
        name="receipt-test", display_name="Synthetic", description="fixture",
        screening=ScreeningConfig(
            enabled=True, market_scope=["cn"],
            hard_filters=HardFilterConfig(exclude_st=False, amount_min=minimum,
                                         require_price_above_ma20=reject_daily),
            factor_weights={"value": 1.0}, max_output=2,
        ),
    )
    cfg = Config(
        strategies_dir=Path("unused"), daily_enrich_enabled=daily,
        daily_enrich_max_candidates=cap, post_analyzers=["dsa"] if dsa else [],
        post_analysis_max_picks=2, dsa_api_url="https://dsa.invalid",
        risk_enabled=False, portfolio_diversity_enabled=False,
        llm_candidate_multiplier=2, llm_max_candidates=12,
    )
    calls = {"snapshot": 0, "daily": 0}

    def snapshot(*args, **kwargs):
        calls["snapshot"] += 1
        return frame.copy()

    def enrich(df, **kwargs):
        calls["daily"] += 1
        if fail_daily:
            raise RuntimeError("SYNTHETIC_PROVIDER_UNAVAILABLE")
        result = df.copy()
        result.attrs["daily_success_count"] = len(result)
        if reject_daily:
            result["price_above_ma20"] = False
        return result

    monkeypatch.setattr(pipeline, "load_all_strategies", lambda _: {strategy.name: strategy})
    monkeypatch.setattr(pipeline, "fetch_snapshot_with_fallback", snapshot)
    # Isolate cost instrumentation from price formulas: ordering is fixed and
    # the actual native hard-filter, budget, selection and return paths execute.
    monkeypatch.setattr(
        pipeline,
        "compute_screen_scores",
        lambda df, _: df.assign(
            screen_score=(df["amount"] / df["amount"].max() * 100.0),
            screen_score_input_state="OBSERVED_COMPLETE",
            screen_score_missing_inputs=[tuple() for _ in range(len(df))],
            screen_score_imputed_factors=[tuple() for _ in range(len(df))],
            screen_score_focus_eligible=True,
            screen_score_readiness_scope=(
                "INPUT_COMPLETENESS_ONLY_NOT_SOURCE_RIGHTS_OR_7TF"
            ),
        ),
    )
    monkeypatch.setattr(pipeline, "enrich_daily_features", enrich)
    monkeypatch.setattr(pipeline, "apply_dsa_provider_context", lambda *args: [])
    result = pipeline.screen(strategy.name, use_llm=False, config=cfg)
    return result, calls


def test_auto_screen_provenance_preserves_the_actual_cheap_execution_receipt(monkeypatch):
    from unittest.mock import MagicMock
    from src.config import Config as AppConfig
    from src.services.screening_service import resolve_auto_screen_analysis_targets
    from tests.test_screening_api import _make_screening_core, _patch_screening_core

    native, _ = _run(monkeypatch)
    raw = asdict(native)
    raw["post_analyzers"] = ["scorecard"]
    fake = _make_screening_core(screen=MagicMock(return_value=raw))
    with _patch_screening_core(fake):
        resolved = resolve_auto_screen_analysis_targets(
            AppConfig(screening_enabled=True), strategy="momentum_quality", max_results=2,
        )
    assert resolved["provenance"]["execution_receipts"]["stock"] == native.execution_receipt
    assert resolved["provenance"]["execution_receipts"]["etf"] == {}
    assert resolved["provenance"]["production_boundary"]["llm_ranking"] is False
    assert resolved["stock_codes"] == ["600005", "600004"]
    assert [
        item["product_group"]
        for item in resolved["provenance"]["selected_candidates"]
    ] == ["AUTO_STOCK_FOCUS", "AUTO_STOCK_FOCUS"]


def test_actual_dsa_cap_statuses_count_before_final_trim(monkeypatch):
    from src.services.screening import dsa
    calls = []

    def local_response(_endpoint, *, stock_code, **kwargs):
        calls.append(stock_code)
        if len(calls) == 2:
            raise RuntimeError("SYNTHETIC_DSA_FAILURE")
        return {"query_id": "synthetic", "report": {}}

    monkeypatch.setattr(dsa, "call_dsa_analysis", local_response)
    result, _ = _run(monkeypatch, dsa=True)
    assert calls == ["600005", "600004"]
    assert result.execution_receipt["dsa_status_counts"] == {
        "completed": 1, "failed": 1, "skipped": 2, "not_requested": 0, "unknown": 0,
    }
    # Completed deep-call status alone does not certify seven-period methods.
    assert result.execution_receipt["source_or_method_readiness_proven"] is False
    assert result.execution_receipt["provider_http_request_count"] is None


def test_daily_filter_empty_keeps_budget_deferral_and_rejection_separate(monkeypatch):
    result, _ = _run(monkeypatch, daily=True, cap=2, reject_daily=True)
    receipt = result.execution_receipt
    stages = {s["stage"]: s for s in receipt["stages"]}
    assert not result.picks
    assert receipt["terminal_reason"] == "EMPTY_AFTER_DAILY_FILTER"
    assert stages["daily_enrichment"]["budget_deferred_count"] == 2
    assert stages["daily_filter"]["excluded_count"] == 2
    assert stages["shortlist"]["status"] == "NOT_REACHED"


def test_real_screen_receipt_preserves_ranking_and_does_not_claim_7tf(monkeypatch):
    result, calls = _run(monkeypatch)
    receipt = result.execution_receipt
    stages = {s["stage"]: s for s in receipt["stages"]}
    assert calls == {"snapshot": 1, "daily": 0}
    assert result.snapshot_source == "SYNTHETIC"
    assert result.snapshot_fallback_used is False
    assert result.snapshot_stale is False
    assert all(pick.snapshot_source_state == "LIVE_OR_DIRECT" for pick in result.picks)
    assert all(pick.snapshot_source_focus_eligible is True for pick in result.picks)
    assert all(pick.screen_score_focus_eligible is True for pick in result.picks)
    assert [p.code for p in result.picks] == ["600005", "600004"]
    assert stages["snapshot"]["output_count"] == 6
    assert stages["snapshot_filter"]["input_count"] == 6
    assert stages["snapshot_filter"]["output_count"] == 4
    assert stages["snapshot_filter"]["excluded_count"] == 2
    assert stages["daily_enrichment"]["status"] == "NOT_REQUESTED"
    assert stages["post_analysis"]["status"] == "NOT_REQUESTED"
    assert stages["selection"]["output_count"] == 2
    assert receipt["dsa_status_counts"] == {"completed": 0, "failed": 0, "skipped": 0, "not_requested": 4, "unknown": 0}
    assert receipt["provider_http_request_count"] is None
    assert receipt["full_universe_equivalence_proven"] is False
    assert receipt["market_outcome_learning_admitted"] is False
    assert receipt["source_or_method_readiness_proven"] is False
    assert asdict(result)["execution_receipt"] == receipt
    assert all(s["elapsed_ms"] is None or s["elapsed_ms"] >= 0 for s in receipt["stages"])


def test_budget_deferral_is_not_risk_rejection_or_failed_provider(monkeypatch):
    result, calls = _run(monkeypatch, daily=True, cap=2)
    stage = next(s for s in result.execution_receipt["stages"] if s["stage"] == "daily_enrichment")
    assert calls == {"snapshot": 1, "daily": 1}
    assert stage["input_count"] == 4
    assert stage["attempted_count"] == stage["success_count"] == 2
    assert stage["budget_deferred_count"] == 2
    assert stage["failure_count"] == 0
    assert stage["excluded_count"] == 0
    assert result.execution_receipt["risk_excluded_count"] == 0


def test_empty_coarse_stage_records_not_reached_without_fabricating_success(monkeypatch):
    result, calls = _run(monkeypatch, minimum=999.0, daily=True)
    receipt = result.execution_receipt
    stages = {s["stage"]: s for s in receipt["stages"]}
    assert not result.picks
    assert calls == {"snapshot": 1, "daily": 0}
    assert stages["snapshot_filter"]["excluded_count"] == 6
    assert stages["daily_enrichment"]["status"] == "NOT_REACHED"
    assert stages["post_analysis"]["status"] == "NOT_REACHED"
    assert receipt["terminal_reason"] == "EMPTY_AFTER_SNAPSHOT_FILTER"


def test_optional_daily_failure_is_visible_and_preserves_existing_fallback(monkeypatch):
    result, calls = _run(monkeypatch, daily=True, cap=2, fail_daily=True)
    stage = next(s for s in result.execution_receipt["stages"] if s["stage"] == "daily_enrichment")
    assert calls["daily"] == 1
    assert stage["status"] == "FAILED"
    assert stage["attempted_count"] == 2
    assert stage["success_count"] == 0
    assert stage["failure_count"] == 2
    assert stage["budget_deferred_count"] == 2
    assert stage["error_kind"] == "RuntimeError"
    assert [p.code for p in result.picks] == ["600005", "600004"]
    assert "SYNTHETIC_PROVIDER_UNAVAILABLE" not in str(result.execution_receipt)
