# -*- coding: utf-8 -*-
"""Regression contracts for the DSA-owned screening implementation."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import call, patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient as FastAPITestClient

from api.v1.router import router
from src.services.screening import REFERENCE_REVISION
from src.services.screening.dsa_provider import apply_dsa_provider_context
from src.services.screening import pipeline as screening_pipeline
from src.services.screening import post_analysis as screening_post_analysis
from src.services.screening.filter import apply_hard_filters
from src.services.screening.config import Config as ScreeningRuntimeConfig
from src.services.screening.models import HardFilterConfig, Pick, ScreeningConfig, Strategy
from src.services.screening.scorer import compute_screen_scores
from src.services.screening import snapshot as screening_snapshot
from src.services.screening.strategy import list_strategies, load_all_strategies


REPO_ROOT = Path(__file__).resolve().parents[1]
SCREENING_ROOT = REPO_ROOT / "src" / "services" / "screening"


def test_screening_engine_is_collected_from_the_internal_package() -> None:
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")

    assert "alphasift.git" not in requirements
    assert "#egg=alphasift" not in requirements
    assert "import src.services.screening.pipeline" in dockerfile


def test_screening_routes_have_a_primary_prefix_and_no_install_endpoint() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    schema_paths = app.openapi()["paths"]
    assert "/api/v1/screening/status" in schema_paths
    assert "/api/v1/alphasift/status" not in schema_paths

    client = FastAPITestClient(app)
    assert client.request("OPTIONS", "/api/v1/screening/status").status_code != 404
    assert client.request("OPTIONS", "/api/v1/alphasift/status").status_code == 404
    assert client.request("POST", "/api/v1/screening/install").status_code == 404


def test_bundled_engine_keeps_source_and_license_notices() -> None:
    notice = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    license_text = (SCREENING_ROOT / "LICENSE").read_text(encoding="utf-8")

    assert REFERENCE_REVISION in notice
    assert "Apache License" in license_text
    derived_files = [
        *SCREENING_ROOT.glob("*.py"),
        *(SCREENING_ROOT / "strategies").glob("*.yaml"),
    ]
    assert derived_files
    for path in derived_files:
        source = path.read_text(encoding="utf-8")
        assert f"Derived from AlphaSift revision {REFERENCE_REVISION}." in source


def test_bundled_strategies_are_loaded_from_the_internal_package() -> None:
    strategies = load_all_strategies(SCREENING_ROOT / "strategies")

    assert set(strategies) == {
        "balanced_alpha",
        "blue_chip_income",
        "capital_heat",
        "dual_low",
        "etf_candidate_prefilter",
        "low_volatility_quality",
        "momentum_quality",
        "oversold_reversal",
        "quality_value",
        "shrink_pullback",
        "volume_breakout",
    }
    assert strategies["dual_low"].screening.factor_weights["value"] < 0.40


def test_list_strategies_preserves_legacy_strategies_dir_override() -> None:
    strategy_yaml = """
name: custom_demo
display_name: 自定义策略
description: custom strategy
screening:
  enabled: true
  market_scope: [cn]
  hard_filters: {}
  factor_weights:
    value: 1.0
  max_output: 3
""".strip()

    with tempfile.TemporaryDirectory() as temp_dir:
        strategy_path = Path(temp_dir) / "custom_demo.yaml"
        strategy_path.write_text(strategy_yaml, encoding="utf-8")

        with patch.dict(os.environ, {"STRATEGIES_DIR": temp_dir}, clear=False):
            config = ScreeningRuntimeConfig.from_env()
            strategies = list_strategies()

    assert config.strategies_dir == Path(temp_dir)
    assert [item.name for item in strategies] == ["custom_demo"]


def test_screening_config_reads_snapshot_cache_ttl() -> None:
    with patch.dict(
        os.environ,
        {"SCREENING_SNAPSHOT_CACHE_TTL_SEC": "120"},
        clear=False,
    ):
        config = ScreeningRuntimeConfig.from_env()

    assert config.snapshot_cache_ttl_seconds == 120.0


def test_pipeline_passes_daily_history_cache_settings_to_enrichment(monkeypatch) -> None:
    snapshot_df = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "Ping An",
                "price": 10.0,
                "change_pct": 1.2,
                "amount": 200_000_000.0,
            }
        ]
    )
    snapshot_df.attrs.update({
        "snapshot_source": "sina",
        "source_errors": [],
        "fallback_used": False,
    })
    strategy = Strategy(
        name="demo",
        display_name="Demo",
        description="demo",
        screening=ScreeningConfig(
            enabled=True,
            market_scope=["cn"],
            hard_filters=HardFilterConfig(),
            factor_weights={"value": 1.0},
            max_output=3,
        ),
    )
    config = ScreeningRuntimeConfig(
        strategies_dir=SCREENING_ROOT / "strategies",
        daily_enrich_enabled=True,
        daily_history_cache_dir=Path("/tmp/daily-history-cache"),
        daily_history_cache_ttl_hours=6,
        post_analyzers=[],
        risk_enabled=False,
        portfolio_diversity_enabled=False,
    )
    captured: dict[str, object] = {}

    def fake_enrich_daily_features(df: pd.DataFrame, **kwargs):
        captured.update(kwargs)
        enriched = df.copy()
        enriched["daily_source"] = "cache"
        enriched.attrs["daily_errors"] = []
        enriched.attrs["daily_success_count"] = len(enriched)
        enriched.attrs["daily_source_counts"] = {"cache": len(enriched)}
        enriched.attrs["daily_quality_flag_counts"] = {}
        enriched.attrs["daily_source_order_notes"] = []
        enriched.attrs["daily_source_health"] = {}
        return enriched

    monkeypatch.setattr(screening_pipeline, "load_all_strategies", lambda _path: {"demo": strategy})
    monkeypatch.setattr(screening_pipeline, "fetch_snapshot_with_fallback", lambda *args, **kwargs: snapshot_df.copy())
    monkeypatch.setattr(screening_pipeline, "apply_hard_filters", lambda df, _filters: df.copy())
    monkeypatch.setattr(
        screening_pipeline,
        "compute_screen_scores",
        lambda df, _screening: df.assign(screen_score=88.0),
    )
    monkeypatch.setattr(screening_pipeline, "enrich_daily_features", fake_enrich_daily_features)
    monkeypatch.setattr(screening_pipeline, "apply_dsa_provider_context", lambda picks, _context: [])
    monkeypatch.setattr(screening_pipeline, "apply_risk_overlay", lambda picks, **kwargs: (picks, []))
    monkeypatch.setattr(screening_pipeline, "apply_portfolio_overlay", lambda picks, **kwargs: (picks, []))
    monkeypatch.setattr(screening_pipeline, "run_post_analyzers", lambda picks, **kwargs: (picks, []))

    history_fetcher = lambda *_args, **_kwargs: pd.DataFrame()
    result = screening_pipeline.screen(
        "demo",
        use_llm=False,
        config=config,
        daily_history_fetcher=history_fetcher,
    )

    assert captured["cache_dir"] == Path("/tmp/daily-history-cache")
    assert captured["cache_ttl_seconds"] == 6 * 60 * 60
    assert captured["history_fetcher"] is history_fetcher
    assert result.daily_enriched is True


def test_dsa_post_analyzer_records_attempted_and_capped_statuses(monkeypatch) -> None:
    picks = [
        Pick(
            rank=index,
            code=f"00000{index}",
            name=f"Stock {index}",
            final_score=90.0 - index,
            screen_score=90.0 - index,
        )
        for index in range(1, 5)
    ]
    config = ScreeningRuntimeConfig(
        strategies_dir=SCREENING_ROOT / "strategies",
        dsa_api_url="https://dsa.example",
    )

    def fake_analyze(candidates, **_kwargs):
        candidates[0].deep_analysis_status = "completed"
        candidates[0].deep_analysis_summary = "completed"
        candidates[1].deep_analysis_status = "failed"
        candidates[1].deep_analysis_summary = "failed"
        return candidates, ["one failure"]

    monkeypatch.setattr(screening_post_analysis, "analyze_picks_with_dsa", fake_analyze)
    # A completed remote overlay can move an attempted pick below an
    # unattempted one. Status ownership must follow the original attempted
    # objects, not the post-overlay list positions.
    monkeypatch.setattr(
        screening_post_analysis,
        "apply_dsa_overlay",
        lambda candidates: [candidates[2], candidates[0], candidates[3], candidates[1]],
    )

    analyzed, degradation = screening_post_analysis.run_post_analyzers(
        picks,
        analyzer_names=["dsa"],
        run_id="run-1",
        config=config,
        max_picks=2,
    )

    status_by_code = {
        pick.code: pick.post_analysis_status.get("dsa")
        for pick in analyzed
    }
    assert status_by_code == {
        "000001": "completed",
        "000002": "failed",
        "000003": "skipped",
        "000004": "skipped",
    }
    assert degradation == ["one failure"]


def test_unadmitted_native_rsi_must_not_flip_dsa_deep_analysis_ranking() -> None:
    """The real L3 overlay cannot re-import the 47/52 raw audit score."""
    from src.services.screening.dsa import (
        _attach_deep_analysis_fields, apply_dsa_overlay,
    )
    from tests.test_factor_decision_summary import _root_admission_score_pair

    inputs = _root_admission_score_pair(strict=True)
    assert [case[0].signal_score for case in inputs] == [47, 52]

    def ordered(values):
        picks = [
            Pick(rank=i + 1, code=code, name="SYNTHETIC", final_score=50.0, screen_score=50.0)
            for i, code in enumerate(("A", "B"))
        ]
        for pick, (trend, factor, public) in zip(picks, values):
            assert factor["score_admission"]["eligible_for_ranking"] is False
            payload = {
                "report": {
                    "summary": {
                        "sentiment_score": trend.signal_score,
                        "operation_advice": "观望",
                        "trend_prediction": "中性",
                    },
                    "details": {
                        "raw_result": public.to_dict(),
                        "context_snapshot": {"trend_result": trend.to_dict()},
                    },
                }
            }
            pick.deep_analysis_status = "completed"
            pick.deep_analysis_result = payload
            _attach_deep_analysis_fields(pick, payload)
            assert pick.deep_analysis_signal_score == trend.signal_score
        return apply_dsa_overlay(picks)

    first = ordered(inputs)
    flipped = ordered(list(reversed(inputs)))
    assert [p.code for p in first] == ["A", "B"]
    assert [p.code for p in flipped] == ["A", "B"]
    assert [p.final_score for p in first] == [50.0, 50.0]
    assert [p.final_score for p in flipped] == [50.0, 50.0]


def test_missing_or_forged_dsa_score_admission_cannot_buy_rank() -> None:
    """Absent receipt or altered eligibility cannot grant L3 score authority."""
    from copy import deepcopy
    from src.services.screening.dsa import _attach_deep_analysis_fields, apply_dsa_overlay
    from tests.test_factor_decision_summary import _root_admission_score_pair

    trend, factor, public = _root_admission_score_pair(strict=True)[1]
    assert trend.signal_score == 52
    for flavor in ("missing", "forged"):
        source = public.to_dict()
        factor_copy = deepcopy(factor)
        if flavor == "missing":
            source["dashboard"]["factor_decision"] = None
        else:
            factor_copy["score_admission"]["eligible_for_ranking"] = True
            source["dashboard"]["factor_decision"] = factor_copy

        data = {
            "report": {
                "summary": {"sentiment_score": 52, "operation_advice": "观望", "trend_prediction": "中性"},
                "details": {
                    "raw_result": source,
                    "context_snapshot": {"trend_result": trend.to_dict()},
                },
            }
        }
        pick = Pick(rank=1, code="SYN", name="SYNTHETIC", final_score=50.0, screen_score=50.0)
        pick.deep_analysis_status = "completed"
        pick.deep_analysis_result = data
        _attach_deep_analysis_fields(pick, data)
        [result] = apply_dsa_overlay([pick])
        assert result.final_score == 50.0
        assert result.rank == 1


def _synthetic_strict_admitted_dsa_score_payload() -> tuple[dict, int]:
    """One real DSA-native method receipt; synthetic bars, not production rights."""
    from tests.test_evidence_traceability_registry import bars
    from src.stock_analyzer import StockTrendAnalyzer
    from src.services.volatility_momentum_service import build_volatility_momentum_context
    from src.services.evidence_traceability_registry import build_method_execution_receipt
    from src.services.factor_decision_summary import (
        build_stock_factor_decision_summary, validate_canonical_factor_binding,
    )

    hist = bars(320)
    target = hist["date"].iloc[-1]
    trend = StockTrendAnalyzer().analyze(hist, "600519")
    momentum = build_volatility_momentum_context(
        stock_code="600519", history=hist, target_date=target,
        market="cn", trend_result=trend,
    )
    assert momentum["momentum"]["macd"]["admission"]["status"] == "READY"
    assert momentum["momentum"]["rsi"]["admission"]["status"] == "READY"
    identity = {
        "data_snapshot_identity": "a" * 64,
        "provider_identity": "synthetic",
        "adjustment_basis": "qfq",
        "price_identity_reasons": [],
        "stock_code": "600519",
        "market": "cn",
        "target_date": str(target),
    }
    native_receipt = build_method_execution_receipt(
        "MOMENTUM", output=momentum, asset_route="STOCK",
        stock_code="600519", market="cn", target_date=target,
        timeframe="daily", input_identity=identity,
    )
    factor = build_stock_factor_decision_summary(
        trend, volatility_momentum_context=momentum,
        method_execution_receipts={"MOMENTUM": native_receipt},
        include_canonical=True,
    )
    assert factor["score_admission"]["eligible_for_ranking"] is True
    assert factor["method_execution_receipt_policy"] == "REQUIRED"
    assert validate_canonical_factor_binding(factor)["runtime_trace_hash"]
    score = factor["composite_score"]
    assert type(score) is int
    body = {
        "report": {
            "summary": {
                "sentiment_score": 99,
                "operation_advice": "强烈买入",
                "trend_prediction": "看多",
            },
            "details": {
                "raw_result": {"dashboard": {"factor_decision": factor}},
                "context_snapshot": {
                    "trend_result": {"signal_score": 99, "risk_factors": []},
                },
            },
        },
    }
    return body, score


def test_bounded_strict_native_score_receipt_keeps_one_legitimate_dsa_overlay():
    """A valid bound Factor numeric reference stays useful; LLM prose adds no votes."""
    from src.services.screening.dsa import _attach_deep_analysis_fields, apply_dsa_overlay

    payload, score = _synthetic_strict_admitted_dsa_score_payload()
    pick = Pick(rank=1, code="600519", name="SYNTHETIC", final_score=50., screen_score=50.)
    pick.deep_analysis_status = "completed"
    pick.deep_analysis_result = payload
    _attach_deep_analysis_fields(pick, payload)
    [result] = apply_dsa_overlay([pick])
    assert result.final_score == round(50.0 + (score - 50) * .20, 4)
    assert result.deep_analysis_sentiment_score == 99  # audit only
    assert result.deep_analysis_operation_advice == "强烈买入"  # no rank bonus
    assert result.deep_analysis_signal_score == 99  # not the qualified score
    assert result.rank == 1
    assert result.excluded_by_risk is False


def _synthetic_strict_mtf_screening_payload(
    *,
    asset_type: str = "stock",
    mode: str = "supportive",
    hard_veto: bool = False,
) -> dict:
    """Build one strict synthetic MTF receipt; no provider rights or trade claim."""
    from copy import deepcopy

    from src.services.evidence_traceability_registry import build_method_execution_receipt
    from src.services.factor_decision_summary import build_stock_factor_decision_summary
    from tests.test_evidence_traceability_registry import (
        _closed_typed_etf_source_factor,
        _closed_typed_stock_source_factor,
    )

    raw_factor, _minute = (
        _closed_typed_etf_source_factor()
        if asset_type == "etf"
        else _closed_typed_stock_source_factor()
    )
    mtf = deepcopy(raw_factor["multi_timeframe_structure_context"])
    timeframe_modes = {
        "supportive": {name: "BULLISH" for name in mtf["timeframes"]},
        "adverse": {name: "BEARISH" for name in mtf["timeframes"]},
        "low_only": {
            "monthly": "MISSING",
            "weekly": "MISSING",
            "daily": "MISSING",
            "60m": "MISSING",
            "30m": "MISSING",
            "15m": "BULLISH",
            "5m": "BULLISH",
        },
    }
    directions = timeframe_modes[mode]
    mtf["status"] = "READY"
    mtf["reason"] = "SYNTHETIC_STRICT_MTF_SCREENING_FIXTURE"
    mtf["cross_timeframe_vote_counting"] = False
    mtf["independent_action_authority"] = False
    for timeframe, direction in directions.items():
        row = mtf["timeframes"][timeframe]
        if direction == "MISSING":
            row.update(
                status="MISSING",
                reason="SYNTHETIC_TIMEFRAME_MISSING",
                trend={"status": "MISSING"},
                ma_structure={"status": "MISSING", "state": "UNKNOWN"},
                macd_startup_stability={"status": "MISSING"},
                rsi_startup_stability={"status": "MISSING"},
                price_structure={"status": "MISSING", "reason": "NO_CONFIRMED_PIVOTS"},
                strategy_admitted=False,
                independent_action_authority=False,
                learning_admitted=False,
            )
            continue
        bullish = direction == "BULLISH"
        row.update(
            status="READY",
            reason="SYNTHETIC_TIMEFRAME_READY",
            trend={
                "status": "READY",
                "direction": direction,
                "ma_alignment": (
                    "MA5 > MA10 > MA20" if bullish else "MA5 < MA10 < MA20"
                ),
                "macd_status": "多头" if bullish else "空头",
                "rsi_status": "中性" if bullish else "弱势",
            },
            ma_structure={
                "status": "READY",
                "state": "BULLISH_STACK" if bullish else "BEARISH_STACK",
                "readiness": {"level": {"status": "READY"}},
            },
            macd_startup_stability={
                "status": "READY",
                "reference_macd_state": "BULLISH" if bullish else "BEARISH",
            },
            rsi_startup_stability={
                "status": "READY",
                "reference_state": "NEUTRAL" if bullish else "WEAK",
            },
            price_structure={
                "status": "READY",
                "structure_event": {
                    "state": "UP_BREAKOUT" if bullish else "DOWN_BREAKDOWN"
                },
            },
            strategy_admitted=False,
            independent_action_authority=False,
            learning_admitted=False,
        )

    old_receipt = raw_factor["method_execution_receipts"]["MTF"]
    receipt = build_method_execution_receipt(
        "MTF",
        output=mtf,
        asset_route="ETF" if asset_type == "etf" else "STOCK",
        stock_code=old_receipt["stock_code"],
        market=old_receipt["market"],
        target_date=old_receipt["target_date"],
        timeframe="multi",
        input_identity=old_receipt["input_identity"],
    )
    kwargs = {}
    if hard_veto:
        kwargs = {
            "daily_market_context": {
                "trade_date": old_receipt["target_date"],
                "region": "cn",
                "market_light": {
                    "status": "red",
                    "data_quality": "ok",
                    "score": 20,
                },
            },
            "market_structure_context": {
                "status": "not_supported",
                "market": "cn",
                "trade_date": old_receipt["target_date"],
            },
        }
    factor = build_stock_factor_decision_summary(
        None,
        multi_timeframe_structure_context=mtf,
        method_execution_receipts={"MTF": receipt},
        include_canonical=True,
        asset_type=asset_type,
        **kwargs,
    )
    return {
        "report": {
            "summary": {
                "sentiment_score": 99,
                "operation_advice": "强烈买入",
                "trend_prediction": "看多",
            },
            "details": {
                "raw_result": {"dashboard": {"factor_decision": factor}},
                "context_snapshot": {
                    "trend_result": {"signal_score": 99, "risk_factors": []},
                },
            },
        },
    }


def test_strict_mtf_roles_rerank_shortlist_without_seven_independent_votes() -> None:
    from src.services.screening.dsa import _attach_deep_analysis_fields, apply_dsa_overlay

    supportive = Pick(
        rank=2, code="600519", name="SUPPORTIVE", final_score=50.0, screen_score=50.0,
    )
    adverse = Pick(
        rank=1, code="600519", name="ADVERSE", final_score=50.0, screen_score=50.0,
    )
    for pick, payload in (
        (supportive, _synthetic_strict_mtf_screening_payload(mode="supportive")),
        (adverse, _synthetic_strict_mtf_screening_payload(mode="adverse")),
    ):
        pick.deep_analysis_status = "completed"
        pick.deep_analysis_result = payload
        _attach_deep_analysis_fields(pick, payload)

    ranked = apply_dsa_overlay([adverse, supportive])
    assert [pick.name for pick in ranked] == ["SUPPORTIVE", "ADVERSE"]
    assert supportive.mtf_screening_status == "VERIFIED"
    assert adverse.mtf_screening_status == "VERIFIED"
    assert supportive.mtf_screening_delta == 4.0
    assert adverse.mtf_screening_delta == -6.0
    assert supportive.mtf_screening_groups == {
        "context": "SUPPORTIVE",
        "setup": "SUPPORTIVE",
        "bridge_structure": "SUPPORTIVE",
        "confirmation_timing": "SUPPORTIVE",
    }
    assert supportive.mtf_screening_timeframes["monthly"]["direction"] == "BULLISH"
    assert supportive.mtf_screening_timeframes["5m"]["direction"] == "BULLISH"
    assert supportive.post_analysis_score_deltas["dsa_mtf_roles"] == 4.0
    assert supportive.post_analysis_results["dsa_mtf_roles"]["role_group_count"] == 4
    assert supportive.post_analysis_results["dsa_mtf_roles"]["cross_timeframe_vote_counting"] is False
    assert supportive.post_analysis_results["dsa_mtf_roles"]["independent_action_authority"] is False


def test_low_timeframe_momentum_cannot_upgrade_without_context_and_daily_setup() -> None:
    from src.services.screening.dsa import _attach_deep_analysis_fields, apply_dsa_overlay

    payload = _synthetic_strict_mtf_screening_payload(mode="low_only")
    pick = Pick(rank=1, code="600519", name="LOW_ONLY", final_score=50.0, screen_score=50.0)
    pick.deep_analysis_status = "completed"
    pick.deep_analysis_result = payload
    _attach_deep_analysis_fields(pick, payload)
    [result] = apply_dsa_overlay([pick])

    assert result.mtf_screening_status == "VERIFIED"
    assert result.mtf_screening_groups["confirmation_timing"] == "SUPPORTIVE"
    assert result.mtf_screening_groups["context"] == "MISSING"
    assert result.mtf_screening_groups["setup"] == "MISSING"
    assert result.mtf_screening_delta == 0.0
    assert result.final_score == 50.0


def test_mtf_screening_projection_requires_current_receipt_and_canonical_binding() -> None:
    from copy import deepcopy
    from src.services.screening.dsa import _attach_deep_analysis_fields, apply_dsa_overlay

    payload = _synthetic_strict_mtf_screening_payload(mode="supportive")
    factor = payload["report"]["details"]["raw_result"]["dashboard"]["factor_decision"]
    factor["multi_timeframe_structure_context"]["timeframes"]["weekly"]["trend"][
        "direction"
    ] = "BEARISH"
    pick = Pick(rank=1, code="600519", name="TAMPERED", final_score=50.0, screen_score=50.0)
    pick.deep_analysis_status = "completed"
    pick.deep_analysis_result = deepcopy(payload)
    _attach_deep_analysis_fields(pick, pick.deep_analysis_result)
    [result] = apply_dsa_overlay([pick])

    assert result.mtf_screening_status == "REJECTED"
    assert result.mtf_screening_delta == 0.0
    assert result.final_score == 50.0
    assert result.mtf_screening_groups == {}

    valid_payload = _synthetic_strict_mtf_screening_payload(mode="supportive")
    wrong_candidate = Pick(
        rank=1, code="000001", name="WRONG_CODE", final_score=50.0, screen_score=50.0,
    )
    wrong_candidate.deep_analysis_status = "completed"
    wrong_candidate.deep_analysis_result = valid_payload
    _attach_deep_analysis_fields(wrong_candidate, valid_payload)
    [wrong_candidate] = apply_dsa_overlay([wrong_candidate])
    assert wrong_candidate.mtf_screening_status == "REJECTED"
    assert wrong_candidate.mtf_screening_delta == 0.0
    assert wrong_candidate.post_analysis_results["dsa_mtf_roles"]["reason"] == (
        "MTF_CANDIDATE_CODE_MISMATCH"
    )


def test_etf_uses_same_strict_mtf_role_projection_without_stock_semantics() -> None:
    from src.services.screening.dsa import _attach_deep_analysis_fields, apply_dsa_overlay

    payload = _synthetic_strict_mtf_screening_payload(
        asset_type="etf", mode="supportive",
    )
    factor = payload["report"]["details"]["raw_result"]["dashboard"]["factor_decision"]
    pick = Pick(rank=1, code="510300", name="ETF", final_score=50.0, screen_score=50.0)
    pick.deep_analysis_status = "completed"
    pick.deep_analysis_result = payload
    _attach_deep_analysis_fields(pick, payload)
    [result] = apply_dsa_overlay([pick])

    assert factor["asset_type"] == "etf"
    assert result.mtf_screening_status == "VERIFIED"
    assert result.mtf_screening_delta == 4.0
    assert result.final_score == 54.0
    assert result.post_analysis_results["dsa_mtf_roles"]["asset_type"] == "etf"


def test_positive_mtf_roles_cannot_offset_a_certified_canonical_hard_veto() -> None:
    from src.services.screening.dsa import _attach_deep_analysis_fields, apply_dsa_overlay

    payload = _synthetic_strict_mtf_screening_payload(
        mode="supportive", hard_veto=True,
    )
    factor = payload["report"]["details"]["raw_result"]["dashboard"]["factor_decision"]
    assert factor["canonical_decision"]["hard_veto"] is True
    pick = Pick(rank=1, code="600519", name="VETO", final_score=50.0, screen_score=50.0)
    pick.deep_analysis_status = "completed"
    pick.deep_analysis_result = payload
    _attach_deep_analysis_fields(pick, payload)
    [result] = apply_dsa_overlay([pick])

    assert result.mtf_screening_status == "VERIFIED"
    assert result.mtf_screening_delta == 0.0
    assert result.final_score == 50.0
    assert result.post_analysis_results["dsa_mtf_roles"]["canonical_veto_suppressed_positive"] is True


def test_existing_independent_risk_exclusion_cannot_be_offset_by_dsa_overlay():
    """No positive score may erase an already enforced hard risk exclusion."""
    from src.services.screening.dsa import _attach_deep_analysis_fields, apply_dsa_overlay

    payload, score = _synthetic_strict_admitted_dsa_score_payload()
    assert score > 50
    pick = Pick(rank=1, code="600519", name="SYNTHETIC", final_score=22., screen_score=50.)
    pick.deep_analysis_status = "completed"
    pick.deep_analysis_result = payload
    pick.excluded_by_risk = True
    pick.risk_flags = ["INDEPENDENT_SYNTHETIC_RISK_EXCLUSION"]
    _attach_deep_analysis_fields(pick, payload)
    [result] = apply_dsa_overlay([pick])
    assert result.final_score == 22.0
    assert result.excluded_by_risk is True
    assert result.risk_flags == ["INDEPENDENT_SYNTHETIC_RISK_EXCLUSION"]


def test_scorecard_reranks_before_capped_dsa_analyzer(monkeypatch) -> None:
    picks = [
        Pick(
            rank=index,
            code=f"00000{index}",
            name=f"Stock {index}",
            final_score=91.0 - index,
            screen_score=91.0 - index,
            factor_scores={"value": 50.0, "stability": 50.0},
        )
        for index in range(1, 5)
    ]
    # The original fourth candidate crosses the remote Top-3 cutoff after the
    # full-pool scorecard applies its value/quality bonus.
    picks[3].factor_scores = {"value": 100.0, "stability": 100.0}
    config = ScreeningRuntimeConfig(
        strategies_dir=SCREENING_ROOT / "strategies",
        dsa_api_url="https://dsa.example",
    )
    attempted_codes: list[str] = []

    def fake_analyze(candidates, *, max_picks, **_kwargs):
        attempted = candidates[:max_picks]
        attempted_codes.extend(pick.code for pick in attempted)
        for pick in attempted:
            pick.deep_analysis_status = "completed"
        return candidates, []

    monkeypatch.setattr(screening_post_analysis, "analyze_picks_with_dsa", fake_analyze)
    monkeypatch.setattr(screening_post_analysis, "apply_dsa_overlay", lambda candidates: candidates)

    analyzed, degradation = screening_post_analysis.run_post_analyzers(
        picks,
        analyzer_names=["scorecard", "dsa"],
        run_id="run-1",
        config=config,
        max_picks=3,
    )

    assert attempted_codes == ["000001", "000004", "000002"]
    assert {
        pick.code: pick.post_analysis_status.get("dsa")
        for pick in analyzed
    } == {
        "000004": "completed",
        "000001": "completed",
        "000002": "completed",
        "000003": "skipped",
    }
    assert degradation == []


def test_external_post_analyzer_rejects_results_beyond_remote_cap(monkeypatch) -> None:
    picks = [
        Pick(
            rank=index,
            code=f"00000{index}",
            name=f"Stock {index}",
            final_score=90.0 - index,
            screen_score=90.0 - index,
        )
        for index in range(1, 4)
    ]
    config = ScreeningRuntimeConfig(
        strategies_dir=SCREENING_ROOT / "strategies",
        post_analyzer_url="https://analyzer.example/rank",
    )
    captured = {}

    def fake_post(_url, *, json, timeout):
        captured["payload"] = json
        captured["timeout"] = timeout
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: [
                {"code": "000001", "score_delta": 1.0, "risk_flags": ["submitted"]},
                {"code": "000001", "score_delta": 50.0, "risk_flags": ["duplicate"]},
                {"code": "000003", "score_delta": 50.0, "risk_flags": ["not-submitted"]},
            ],
        )

    monkeypatch.setattr(screening_post_analysis.requests, "post", fake_post)

    analyzed, degradation = screening_post_analysis.run_post_analyzers(
        picks,
        analyzer_names=["external_http"],
        run_id="run-1",
        config=config,
        max_picks=2,
    )

    by_code = {pick.code: pick for pick in analyzed}
    assert [item["code"] for item in captured["payload"]["candidates"]] == ["000001", "000002"]
    assert by_code["000001"].final_score == 90.0
    assert by_code["000001"].risk_flags == ["submitted"]
    assert by_code["000001"].post_analysis_status["external_http"] == "completed"
    assert by_code["000002"].post_analysis_status["external_http"] == "failed"
    assert by_code["000003"].final_score == 87.0
    assert by_code["000003"].risk_flags == []
    assert by_code["000003"].post_analysis_status["external_http"] == "skipped"
    assert degradation == []


def test_pipeline_uses_ranker_success_flag_instead_of_partial_llm_scores(monkeypatch) -> None:
    snapshot_df = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "Ping An",
                "price": 10.0,
                "change_pct": 1.2,
                "amount": 200_000_000.0,
            }
        ]
    )
    snapshot_df.attrs.update({
        "snapshot_source": "sina",
        "source_errors": [],
        "fallback_used": False,
    })
    strategy = Strategy(
        name="demo",
        display_name="Demo",
        description="demo",
        screening=ScreeningConfig(
            enabled=True,
            market_scope=["cn"],
            hard_filters=HardFilterConfig(),
            factor_weights={"value": 1.0},
            max_output=3,
        ),
    )
    config = ScreeningRuntimeConfig(
        strategies_dir=SCREENING_ROOT / "strategies",
        llm_model="openai/gpt-5-mini",
        llm_api_key="test-key",
        post_analyzers=[],
        risk_enabled=False,
        portfolio_diversity_enabled=False,
    )

    def fake_ranker(picks, *_args, **_kwargs):
        leaked_pick = picks[0]
        leaked_pick.llm_score = 99.0
        return SimpleNamespace(
            picks=picks,
            ranked=False,
            market_view="",
            selection_logic="",
            portfolio_risk="",
            coverage=0.5,
            errors=["coverage below threshold"],
        )

    monkeypatch.setattr(screening_pipeline, "load_all_strategies", lambda _path: {"demo": strategy})
    monkeypatch.setattr(screening_pipeline, "fetch_snapshot_with_fallback", lambda *args, **kwargs: snapshot_df.copy())
    monkeypatch.setattr(screening_pipeline, "apply_hard_filters", lambda df, _filters: df.copy())
    monkeypatch.setattr(
        screening_pipeline,
        "compute_screen_scores",
        lambda df, _screening: df.assign(screen_score=88.0),
    )
    monkeypatch.setattr(screening_pipeline, "apply_dsa_provider_context", lambda picks, _context: [])
    monkeypatch.setattr(screening_pipeline, "rank_candidates_with_metadata", fake_ranker)
    monkeypatch.setattr(screening_pipeline, "apply_risk_overlay", lambda picks, **kwargs: (picks, []))
    monkeypatch.setattr(screening_pipeline, "apply_portfolio_overlay", lambda picks, **kwargs: (picks, []))
    monkeypatch.setattr(screening_pipeline, "run_post_analyzers", lambda picks, **kwargs: (picks, []))

    result = screening_pipeline.screen("demo", use_llm=True, config=config)

    assert result.llm_ranked is False
    assert result.picks[0].llm_score is None
    assert result.picks[0].final_score == result.picks[0].screen_score
    assert "LLM ranking failed: fell back to screen_score" in result.degradation


def test_default_scorecard_scores_full_pool_before_seeded_rotation(monkeypatch) -> None:
    snapshot_df = pd.DataFrame([
        {
            "code": f"00000{index}",
            "name": f"Stock {index}",
            "price": 10.0,
            "change_pct": 1.0,
            "amount": 200_000_000.0,
            "raw_score": score,
        }
        for index, score in enumerate([84.2, 84.1, 84.0, 83.9, 83.8], start=1)
    ])
    snapshot_df.attrs.update({
        "snapshot_source": "sina",
        "source_errors": [],
        "fallback_used": False,
    })
    strategy = Strategy(
        name="demo",
        display_name="Demo",
        description="demo",
        screening=ScreeningConfig(
            enabled=True,
            market_scope=["cn"],
            hard_filters=HardFilterConfig(),
            factor_weights={"value": 1.0},
            max_output=3,
        ),
    )
    config = ScreeningRuntimeConfig(
        strategies_dir=SCREENING_ROOT / "strategies",
        post_analyzers=["scorecard"],
        post_analysis_max_picks=3,
        llm_candidate_multiplier=2,
        llm_max_candidates=10,
        risk_enabled=False,
        portfolio_diversity_enabled=False,
    )
    observed_statuses: list[list[str | None]] = []
    original_variant = screening_pipeline.apply_seeded_selection_variant

    def capture_variant(picks, **kwargs):
        observed_statuses.append([
            pick.post_analysis_status.get("scorecard") for pick in picks
        ])
        return original_variant(picks, **kwargs)

    run_ids = iter(f"{index:012d}" for index in range(20))
    monkeypatch.setattr(screening_pipeline, "load_all_strategies", lambda _path: {"demo": strategy})
    monkeypatch.setattr(screening_pipeline, "fetch_snapshot_with_fallback", lambda *args, **kwargs: snapshot_df.copy())
    monkeypatch.setattr(screening_pipeline, "apply_hard_filters", lambda df, _filters: df.copy())
    monkeypatch.setattr(
        screening_pipeline,
        "compute_screen_scores",
        lambda df, _screening: df.assign(screen_score=df["raw_score"]),
    )
    monkeypatch.setattr(screening_pipeline, "apply_dsa_provider_context", lambda picks, _context: [])
    monkeypatch.setattr(screening_pipeline, "apply_seeded_selection_variant", capture_variant)
    monkeypatch.setattr(screening_pipeline.uuid, "uuid4", lambda: SimpleNamespace(hex=next(run_ids)))

    variants = {
        tuple(
            pick.code
            for pick in screening_pipeline.screen(
                "demo",
                max_output=3,
                use_llm=False,
                selection_seed="browser-a",
                config=config,
            ).picks
        )
        for _ in range(20)
    }

    assert all(statuses == ["completed"] * 5 for statuses in observed_statuses)
    assert len(variants) >= 2


def test_dsa_provider_context_respects_host_max_candidates_setting() -> None:
    picks = [
        Pick(rank=index + 1, code=f"00000{index + 1}", name=f"Stock {index + 1}", final_score=90.0, screen_score=90.0)
        for index in range(5)
    ]
    requested_codes: list[str] = []

    def get_candidate_context(code: str, _name: str) -> dict[str, object]:
        requested_codes.append(code)
        return {"enriched": True, "quote": {"price": 10.0}}

    notes = apply_dsa_provider_context(
        picks,
        {"dsa": {"max_candidates": 3, "get_candidate_context": get_candidate_context}},
    )

    assert requested_codes == ["000001", "000002", "000003"]
    assert all(pick.dsa_context.get("enriched") is True for pick in picks[:3])
    assert all(pick.dsa_context == {} for pick in picks[3:])
    assert notes == ["DSA provider context applied 3 of 3 candidates"]


def test_hard_filter_and_factor_scoring_keep_core_semantics() -> None:
    frame = pd.DataFrame(
        [
            {
                "code": "low_value",
                "name": "Low Value",
                "price": 10.0,
                "amount": 200_000_000,
                "pe_ratio": 5.0,
                "pb_ratio": 0.6,
                "turnover_rate": 2.0,
                "volume_ratio": 1.2,
                "change_pct": 0.0,
            },
            {
                "code": "high_value",
                "name": "High Value",
                "price": 10.0,
                "amount": 20_000_000,
                "pe_ratio": 15.0,
                "pb_ratio": 2.0,
                "turnover_rate": 2.0,
                "volume_ratio": 1.2,
                "change_pct": 0.0,
            },
        ]
    )

    filtered = apply_hard_filters(frame, HardFilterConfig(amount_min=100_000_000))
    assert filtered["code"].tolist() == ["low_value"]

    scored = compute_screen_scores(
        frame,
        ScreeningConfig(factor_weights={"value": 1.0}),
    ).set_index("code")
    assert scored.loc["low_value", "screen_score"] > scored.loc["high_value", "screen_score"]


def test_snapshot_schema_mismatch_counts_toward_source_circuit_breaker(
    monkeypatch,
) -> None:
    bad_snapshot = pd.DataFrame([{"code": "000001", "name": "Ping An", "price": 10.0}])
    good_snapshot = pd.DataFrame(
        [{"code": "000001", "name": "Ping An", "price": 10.0, "volume_ratio": 1.5}]
    )
    source_health: dict[str, dict[str, object]] = {}
    calls: list[str] = []

    def fake_fetch(source: str) -> pd.DataFrame:
        calls.append(source)
        if source == "sina":
            return bad_snapshot
        if source == "efinance":
            return good_snapshot.copy()
        raise AssertionError(f"unexpected source {source}")

    monkeypatch.setattr(screening_snapshot, "_SOURCE_HEALTH", source_health)
    monkeypatch.setattr(screening_snapshot, "fetch_cn_snapshot", fake_fetch)

    for _ in range(3):
        result = screening_snapshot.fetch_snapshot_with_fallback(
            ["sina", "efinance"],
            required_columns=["volume_ratio"],
        )
        assert result.attrs["snapshot_source"] == "efinance"

    health = screening_snapshot.snapshot_source_health_snapshot(["sina"])
    assert health["sina"]["failures"] == 3
    assert health["sina"]["disabled"] is True

    calls.clear()
    result = screening_snapshot.fetch_snapshot_with_fallback(
        ["sina", "efinance"],
        required_columns=["volume_ratio"],
    )

    assert calls == ["efinance"]
    assert result.attrs["snapshot_source"] == "efinance"
    assert "temporarily disabled" in result.attrs["source_errors"][0]


def test_sina_snapshot_uses_timeout_wrapper(monkeypatch) -> None:
    expected = pd.DataFrame([{"code": "000001"}])
    captured: dict[str, object] = {}

    def fake_wrapper(fetcher, *, source: str) -> pd.DataFrame:
        captured["fetcher"] = fetcher
        captured["source"] = source
        return expected

    monkeypatch.setattr(screening_snapshot, "_call_snapshot_wrapper", fake_wrapper)
    monkeypatch.setattr(screening_snapshot, "_fetch_sina", lambda: expected)

    result = screening_snapshot.fetch_cn_snapshot("sina")

    assert result is expected
    assert captured["source"] == "sina"
    assert captured["fetcher"] is screening_snapshot._fetch_sina


def test_em_datacenter_snapshot_uses_timeout_wrapper(monkeypatch) -> None:
    expected = pd.DataFrame([{"code": "000001"}])
    captured: dict[str, object] = {}

    def fake_wrapper(fetcher, *, source: str) -> pd.DataFrame:
        captured["fetcher"] = fetcher
        captured["source"] = source
        return expected

    monkeypatch.setattr(screening_snapshot, "_call_snapshot_wrapper", fake_wrapper)
    monkeypatch.setattr(screening_snapshot, "_fetch_em_datacenter", lambda: expected)

    result = screening_snapshot.fetch_cn_snapshot("em_datacenter")

    assert result is expected
    assert captured["source"] == "em_datacenter"
    assert captured["fetcher"] is screening_snapshot._fetch_em_datacenter


def test_em_datacenter_timeout_falls_back_to_last_good_snapshot(tmp_path, monkeypatch) -> None:
    cached = pd.DataFrame(
        [{"code": "000001", "name": "Ping An", "price": 10.0, "volume_ratio": 1.5}]
    )
    cached.attrs["snapshot_source"] = "cached:last_good"
    cache_path = tmp_path / "snapshot-cache.json"
    screening_snapshot._write_last_good_snapshot(cache_path, cached)

    def fake_wrapper(fetcher, *, source: str) -> pd.DataFrame:
        assert source == "em_datacenter"
        assert fetcher is screening_snapshot._fetch_em_datacenter
        raise RuntimeError("snapshot source em_datacenter timed out")

    monkeypatch.setattr(screening_snapshot, "_call_snapshot_wrapper", fake_wrapper)

    result = screening_snapshot.fetch_snapshot_with_fallback(
        ["em_datacenter"],
        required_columns=["volume_ratio"],
        fallback_snapshot_path=cache_path,
    )

    assert result.attrs["fallback_used"] is True
    assert result.attrs["snapshot_source"] == "last_good_cache"
    assert result.loc[0, "code"] == "000001"


def test_fresh_snapshot_cache_skips_live_sources(tmp_path, monkeypatch) -> None:
    cached = pd.DataFrame(
        [{"code": "000001", "name": "Ping An", "price": 10.0, "volume_ratio": 1.5}]
    )
    cached.attrs["snapshot_source"] = "sina"
    cache_path = tmp_path / "snapshot-cache.json"
    screening_snapshot._write_last_good_snapshot(cache_path, cached)
    live_fetch = patch.object(
        screening_snapshot,
        "fetch_cn_snapshot",
        side_effect=AssertionError("fresh cache should avoid live snapshot calls"),
    )

    with live_fetch as fetch_mock:
        result = screening_snapshot.fetch_snapshot_with_fallback(
            ["sina"],
            required_columns=["volume_ratio"],
            fallback_snapshot_path=cache_path,
            cache_ttl_seconds=300,
        )

    fetch_mock.assert_not_called()
    assert result.attrs["snapshot_source"] == "last_good_cache"
    assert result.attrs["cache_used"] is True
    assert result.attrs["fallback_used"] is False
    assert result.attrs["stale"] is False
    assert result.attrs["last_good_snapshot_source"] == "sina"


def test_fresh_snapshot_cache_ignores_mismatched_source(tmp_path, monkeypatch) -> None:
    cached = pd.DataFrame(
        [{"code": "000001", "name": "Ping An", "price": 10.0, "volume_ratio": 1.5}]
    )
    cached.attrs["snapshot_source"] = "sina"
    cache_path = tmp_path / "snapshot-cache.json"
    screening_snapshot._write_last_good_snapshot(cache_path, cached)

    live = pd.DataFrame(
        [{"code": "000002", "name": "Vanke", "price": 11.0, "volume_ratio": 2.0}]
    )
    live.attrs["snapshot_source"] = "tushare"
    live_fetch = patch.object(
        screening_snapshot,
        "fetch_cn_snapshot",
        autospec=True,
        return_value=live,
    )

    with live_fetch as fetch_mock:
        result = screening_snapshot.fetch_snapshot_with_fallback(
            ["tushare"],
            required_columns=["volume_ratio"],
            fallback_snapshot_path=cache_path,
            cache_ttl_seconds=300,
        )

    fetch_mock.assert_called_once_with("tushare")
    assert result.loc[0, "code"] == "000002"
    assert result.attrs["snapshot_source"] == "tushare"
    assert result.attrs["fallback_used"] is False
    assert result.attrs["stale"] is False


def test_fresh_snapshot_cache_ignores_non_primary_source(tmp_path, monkeypatch) -> None:
    cached = pd.DataFrame(
        [{"code": "000001", "name": "Ping An", "price": 10.0, "volume_ratio": 1.5}]
    )
    cached.attrs["snapshot_source"] = "sina"
    cache_path = tmp_path / "snapshot-cache.json"
    screening_snapshot._write_last_good_snapshot(cache_path, cached)

    live = pd.DataFrame(
        [{"code": "000002", "name": "Vanke", "price": 11.0, "volume_ratio": 2.0}]
    )
    live.attrs["snapshot_source"] = "tushare"
    live_fetch = patch.object(
        screening_snapshot,
        "fetch_cn_snapshot",
        autospec=True,
        return_value=live,
    )

    with live_fetch as fetch_mock:
        result = screening_snapshot.fetch_snapshot_with_fallback(
            ["tushare", "sina"],
            required_columns=["volume_ratio"],
            fallback_snapshot_path=cache_path,
            cache_ttl_seconds=300,
        )

    fetch_mock.assert_called_once_with("tushare")
    assert result.loc[0, "code"] == "000002"
    assert result.attrs["snapshot_source"] == "tushare"
    assert result.attrs["fallback_used"] is False
    assert result.attrs["stale"] is False


def test_fresh_snapshot_cache_reuses_fallback_from_same_source_chain(tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "snapshot-cache.json"
    live = pd.DataFrame(
        [{"code": "000002", "name": "Vanke", "price": 11.0, "volume_ratio": 2.0}]
    )
    live.attrs["snapshot_source"] = "sina"

    first_fetch = patch.object(
        screening_snapshot,
        "fetch_cn_snapshot",
        autospec=True,
        side_effect=[RuntimeError("tushare unavailable"), live],
    )
    with first_fetch as fetch_mock:
        first = screening_snapshot.fetch_snapshot_with_fallback(
            ["tushare", "sina"],
            required_columns=["volume_ratio"],
            fallback_snapshot_path=cache_path,
            cache_ttl_seconds=300,
        )

    assert fetch_mock.call_args_list == [call("tushare"), call("sina")]
    assert first.attrs["snapshot_source"] == "sina"

    second_fetch = patch.object(
        screening_snapshot,
        "fetch_cn_snapshot",
        side_effect=AssertionError("same-chain fresh cache should avoid live calls"),
    )
    with second_fetch as fetch_mock:
        second = screening_snapshot.fetch_snapshot_with_fallback(
            ["tushare", "sina"],
            required_columns=["volume_ratio"],
            fallback_snapshot_path=cache_path,
            cache_ttl_seconds=300,
        )

    fetch_mock.assert_not_called()
    assert second.attrs["snapshot_source"] == "last_good_cache"
    assert second.attrs["last_good_snapshot_source"] == "sina"
    assert second.attrs["fallback_used"] is False
