from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.config import Config
from src.services.screening.models import ScreeningConfig
from src.services.screening.pipeline import _df_to_picks
from src.services.screening.scorer import compute_screen_scores
from src.services.screening_service import _resolve_auto_screen_bucket


_STAGE_NAMES = (
    "snapshot",
    "host_prefilter",
    "snapshot_filter",
    "daily_enrichment",
    "daily_filter",
    "shortlist",
    "post_analysis",
    "selection",
)


def _completed_execution_receipt(
    run_id: str,
    asset_type: str,
    output_count: int,
) -> dict:
    return {
        "schema_version": "screening-execution-receipt-v1",
        "scope": "RUN_LOCAL_EXECUTION_METADATA_NOT_DATA_OR_STRATEGY_PROOF",
        "run_id": run_id,
        "asset_type": asset_type,
        "terminal_reason": "SELECTION_COMPLETED",
        "stages": [
            {
                "stage": name,
                "status": (
                    "COMPLETED"
                    if name in {"snapshot", "snapshot_filter", "shortlist", "selection"}
                    else "NOT_REQUESTED"
                ),
                "input_count": output_count,
                "output_count": output_count,
                "excluded_count": 0,
                "elapsed_ms": 0.0,
            }
            for name in _STAGE_NAMES
        ],
        "dsa_status_counts": {
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "not_requested": output_count,
            "unknown": 0,
        },
        "risk_excluded_count": 0,
        "provider_http_request_count": None,
        "source_or_method_readiness_proven": False,
        "full_universe_equivalence_proven": False,
        "market_outcome_learning_admitted": False,
    }


def _complete_stock_row() -> dict:
    return {
        "code": "600519",
        "name": "贵州茅台",
        "price": 1500.0,
        "pe_ratio": 25.0,
        "pb_ratio": 8.0,
        "amount": 2_000_000_000.0,
        "change_pct": 1.0,
        "change_60d": 8.0,
        "signal_score": 68.0,
        "macd_status": "bullish",
        "volume_ratio": 1.6,
        "turnover_rate": 1.2,
        "volatility_20d_pct": 22.0,
        "industry": "白酒",
    }


def _candidate(
    asset_type: str,
    *,
    score_ready: bool | None = True,
    source_ready: bool | None = True,
    score_state: str = "OBSERVED_COMPLETE",
    source_state: str = "LIVE_OR_DIRECT",
) -> dict:
    return {
        "rank": 1,
        "code": "600519" if asset_type == "stock" else "510300",
        "name": "贵州茅台" if asset_type == "stock" else "沪深300ETF",
        "final_score": 88.0,
        "screen_score": 88.0,
        "industry": "白酒" if asset_type == "stock" else "宽基ETF",
        "screen_score_input_state": score_state,
        "screen_score_missing_inputs": (
            [] if score_ready is True else ["value:pb_ratio"]
        ),
        "screen_score_imputed_factors": (
            [] if score_ready is True else ["value"]
        ),
        "screen_score_focus_eligible": score_ready,
        "screen_score_readiness_scope": (
            "INPUT_COMPLETENESS_ONLY_NOT_SOURCE_RIGHTS_OR_7TF"
        ),
        "snapshot_source_state": source_state,
        "snapshot_source_focus_eligible": source_ready,
    }


def _resolve(
    asset_type: str,
    candidates: list[dict],
    *,
    include_receipt: bool = True,
    snapshot_source: str = "synthetic-admitted",
    fallback_used: bool = False,
    stale: bool = False,
    receipt_case: str = "valid",
) -> dict:
    run_id = f"focus-{asset_type}-{len(candidates)}"
    raw = {
        "strategy": "fixture",
        "strategy_version": "1",
        "market": "cn",
        "run_id": run_id,
        "snapshot_count": len(candidates),
        "snapshot_source": snapshot_source,
        "snapshot_fallback_used": fallback_used,
        "snapshot_stale": stale,
        "ranking_mode": "factor",
        "llm_ranked": False,
        "post_analyzers": ["scorecard"] if asset_type == "stock" else [],
        "deep_analysis_requested": False,
        "picks": candidates,
    }
    if include_receipt:
        receipt = _completed_execution_receipt(
            run_id,
            asset_type,
            len(candidates),
        )
        if receipt_case == "run_id":
            receipt["run_id"] = "different-run"
        elif receipt_case == "asset_type":
            receipt["asset_type"] = "etf" if asset_type == "stock" else "stock"
        elif receipt_case == "terminal":
            receipt["terminal_reason"] = "EMPTY_AFTER_SNAPSHOT_FILTER"
        elif receipt_case == "stage":
            receipt["stages"] = [
                item for item in receipt["stages"] if item["stage"] != "snapshot"
            ]
        elif receipt_case == "status":
            next(
                item for item in receipt["stages"] if item["stage"] == "snapshot"
            )["status"] = "FAILED"
        elif receipt_case == "count":
            next(
                item for item in receipt["stages"] if item["stage"] == "selection"
            )["output_count"] = len(candidates) + 1
        raw["execution_receipt"] = receipt
    with patch(
        "src.services.screening_service._call_screening_screen",
        return_value=raw,
    ):
        return _resolve_auto_screen_bucket(
            Config(screening_enabled=True),
            strategy="fixture",
            market="cn",
            max_results=max(1, len(candidates)),
            asset_type=asset_type,
            selection_seed="fixture",
            progress_callback=None,
        )


def test_default_rank_score_marks_imputed_inputs_and_blocks_focus_authority():
    row = _complete_stock_row()
    row.pop("change_60d")
    row.pop("signal_score")
    row.pop("macd_status")
    row.pop("volatility_20d_pct")
    scored = compute_screen_scores(pd.DataFrame([row]), ScreeningConfig()).iloc[0]
    assert 0 <= scored["screen_score"] <= 100
    assert scored["screen_score_input_state"] == "IMPUTED_PARTIAL"
    assert bool(scored["screen_score_focus_eligible"]) is False
    assert "momentum" in scored["screen_score_imputed_factors"]
    assert "stability" in scored["screen_score_imputed_factors"]
    assert scored["screen_score_readiness_scope"] == (
        "INPUT_COMPLETENESS_ONLY_NOT_SOURCE_RIGHTS_OR_7TF"
    )


def test_complete_stock_and_etf_factor_inputs_are_input_complete_only():
    stock = compute_screen_scores(
        pd.DataFrame([_complete_stock_row()]),
        ScreeningConfig(),
    ).iloc[0]
    assert stock["screen_score_input_state"] == "OBSERVED_COMPLETE"
    assert bool(stock["screen_score_focus_eligible"]) is True
    assert stock["screen_score_imputed_factors"] == ()

    etf_row = _complete_stock_row()
    etf_row.pop("pe_ratio")
    etf_row.pop("pb_ratio")
    etf = compute_screen_scores(
        pd.DataFrame([etf_row]),
        ScreeningConfig(
            factor_weights={
                "liquidity": 0.30,
                "momentum": 0.30,
                "activity": 0.20,
                "stability": 0.20,
            }
        ),
    ).iloc[0]
    assert etf["screen_score_input_state"] == "OBSERVED_COMPLETE"
    assert bool(etf["screen_score_focus_eligible"]) is True
    assert etf["screen_score_readiness_scope"].endswith(
        "NOT_SOURCE_RIGHTS_OR_7TF"
    )


def test_df_to_picks_preserves_score_and_snapshot_readiness():
    frame = compute_screen_scores(
        pd.DataFrame([_complete_stock_row()]),
        ScreeningConfig(),
    )
    frame["snapshot_source_state"] = "FALLBACK_STALE"
    frame["snapshot_source_focus_eligible"] = False
    pick = _df_to_picks(frame)[0]
    assert pick.screen_score_input_state == "OBSERVED_COMPLETE"
    assert pick.screen_score_focus_eligible is True
    assert pick.snapshot_source_state == "FALLBACK_STALE"
    assert pick.snapshot_source_focus_eligible is False


@pytest.mark.parametrize(
    "asset_type,expected_group",
    [("stock", "AUTO_STOCK_REMAINING"), ("etf", "AUTO_ETF_REMAINING")],
)
def test_legacy_candidate_without_readiness_fields_never_enters_focus(
    asset_type: str,
    expected_group: str,
):
    legacy = {
        "rank": 1,
        "code": "600519" if asset_type == "stock" else "510300",
        "name": "legacy candidate",
        "final_score": 96.0,
        "screen_score": 96.0,
        "industry": "研究行业",
    }
    candidate = _resolve(asset_type, [legacy])["selected_candidates"][0]
    assert candidate["product_group"] == expected_group
    assert candidate["focus_eligible"] is False
    assert candidate["screen_score_input_state"] == "UNVERIFIED"
    assert candidate["snapshot_source_state"] == "UNVERIFIED"
    assert candidate["snapshot_source_focus_eligible"] is False


@pytest.mark.parametrize(
    "asset_type,expected_group",
    [("stock", "AUTO_STOCK_REMAINING"), ("etf", "AUTO_ETF_REMAINING")],
)
def test_imputed_candidate_remains_auditable_but_never_focus(
    asset_type: str,
    expected_group: str,
):
    candidate = _candidate(
        asset_type,
        score_ready=False,
        score_state="IMPUTED_PARTIAL",
    )
    selected = _resolve(asset_type, [candidate])["selected_candidates"][0]
    assert selected["product_group"] == expected_group
    assert selected["focus_eligible"] is False
    assert selected["screen_score_focus_eligible"] is False
    assert selected["screen_score_imputed_factors"] == ["value"]


@pytest.mark.parametrize(
    "asset_type,expected_group",
    [("stock", "AUTO_STOCK_REMAINING"), ("etf", "AUTO_ETF_REMAINING")],
)
def test_stale_fallback_snapshot_remains_auditable_but_never_focus(
    asset_type: str,
    expected_group: str,
):
    candidate = _candidate(
        asset_type,
        source_ready=False,
        source_state="FALLBACK_STALE",
    )
    result = _resolve(
        asset_type,
        [candidate],
        snapshot_source="last_good_cache",
        fallback_used=True,
        stale=True,
    )
    selected = result["selected_candidates"][0]
    assert selected["product_group"] == expected_group
    assert selected["focus_eligible"] is False
    assert selected["snapshot_source_focus_eligible"] is False
    assert selected["snapshot_source_state"] == "FALLBACK_STALE"
    assert result["snapshot_fallback_used"] is True
    assert result["snapshot_stale"] is True


@pytest.mark.parametrize(
    "asset_type,expected_group",
    [("stock", "AUTO_STOCK_REMAINING"), ("etf", "AUTO_ETF_REMAINING")],
)
def test_missing_execution_receipt_never_enters_focus(
    asset_type: str,
    expected_group: str,
):
    result = _resolve(
        asset_type,
        [_candidate(asset_type)],
        include_receipt=False,
    )
    selected = result["selected_candidates"][0]
    assert selected["product_group"] == expected_group
    assert selected["focus_eligible"] is False
    assert selected["screening_execution_state"] == "UNVERIFIED"
    assert selected["screening_execution_focus_eligible"] is False


@pytest.mark.parametrize(
    "receipt_case",
    ["run_id", "asset_type", "terminal", "stage", "status", "count"],
)
def test_execution_receipt_identity_or_stage_tamper_never_enters_focus(
    receipt_case: str,
):
    result = _resolve(
        "stock",
        [_candidate("stock")],
        receipt_case=receipt_case,
    )
    selected = result["selected_candidates"][0]
    assert selected["product_group"] == "AUTO_STOCK_REMAINING"
    assert selected["focus_eligible"] is False
    assert selected["screening_execution_focus_eligible"] is False
    assert selected["screening_execution_state"] != "COMPLETED"


def test_candidate_source_claim_cannot_bypass_missing_run_source_identity():
    result = _resolve(
        "stock",
        [_candidate("stock")],
        snapshot_source="",
    )
    selected = result["selected_candidates"][0]
    assert selected["product_group"] == "AUTO_STOCK_REMAINING"
    assert selected["snapshot_source_focus_eligible"] is False
    assert result["snapshot_source_state"] == "UNVERIFIED"
    assert result["snapshot_source_focus_eligible"] is False


def test_focus_flag_cannot_bypass_unverified_score_receipt_fields():
    candidate = _candidate("stock")
    candidate["screen_score_input_state"] = "UNVERIFIED"
    result = _resolve("stock", [candidate])
    selected = result["selected_candidates"][0]
    assert selected["product_group"] == "AUTO_STOCK_REMAINING"
    assert selected["screen_score_focus_eligible"] is False


@pytest.mark.parametrize(
    "asset_type,expected_group",
    [("stock", "AUTO_STOCK_FOCUS"), ("etf", "AUTO_ETF_FOCUS")],
)
def test_complete_score_fresh_source_and_completed_execution_enter_focus(
    asset_type: str,
    expected_group: str,
):
    result = _resolve(asset_type, [_candidate(asset_type)])
    selected = result["selected_candidates"][0]
    assert selected["product_group"] == expected_group
    assert selected["focus_eligible"] is True
    assert selected["screen_score_focus_eligible"] is True
    assert selected["snapshot_source_focus_eligible"] is True
    assert selected["screening_execution_state"] == "COMPLETED"
    assert selected["screening_execution_focus_eligible"] is True


def test_partial_pool_keeps_nonfocus_candidate_without_promoting_or_dropping_it():
    good = _candidate("stock")
    bad = _candidate(
        "stock",
        score_ready=False,
        score_state="IMPUTED_PARTIAL",
    )
    bad["rank"] = 2
    bad["code"] = "000001"
    bad["name"] = "平安银行"
    result = _resolve("stock", [good, bad])
    selected = result["selected_candidates"]
    assert result["codes"] == ["600519", "000001"]
    assert [item["product_group"] for item in selected] == [
        "AUTO_STOCK_FOCUS",
        "AUTO_STOCK_REMAINING",
    ]


def test_zero_candidate_run_is_a_legal_nontrigger():
    result = _resolve("stock", [])
    assert result["codes"] == []
    assert result["selected_candidates"] == []
    assert result["selected_count"] == 0
