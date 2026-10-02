"""Offline native-service integration; NOT a full Pipeline/SMTP/PIT acceptance."""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest

from src.config import Config

from src.services import evidence_traceability_registry as reg
from src.services.cost_structure_service import build_cost_structure_context
from src.services.factor_decision_summary import build_stock_factor_decision_summary
from src.services.pattern_trigger_service import build_pattern_trigger_context, _dedupe_patterns
from src.services.price_structure_service import build_price_structure_context
from src.services.research_state_projection import (
    STRATEGY_ELIGIBILITY_SCHEMA_VERSION, STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE,
    build_strategy_eligibility_identity,
)
from src.services.supply_demand_service import build_supply_demand_context
from src.services.volatility_momentum_service import build_volatility_momentum_context
from src.stock_analyzer import MACDStatus, StockTrendAnalyzer, TrendAnalysisResult

ROOT = Path(__file__).resolve().parents[1]
# Independent accepted strategy contract, not an expected set generated from the code under test.
EXPECTED_REQUIRED = {
    "market_regime_permission", "sector_industry_strength", "quality", "valuation",
    "weekly_trend_structure", "daily_trend_structure", "daily_pullback_or_supply_contraction",
    "volume_price_confirmation", "distribution_risk_clear", "thirty_minute_trigger", "risk_reward",
}


def _accepted_bindings():
    return reg.EVIDENCE_BINDINGS + reg.DEFERRED_BINDINGS + (reg.DECISION_BINDING,)


def _strict_daily_identity(target, *, stock_code="600519", market="cn", provider="synthetic", basis="qfq"):
    return {
        "data_snapshot_identity": "a" * 64,
        "provider_identity": provider,
        "adjustment_basis": basis,
        "price_identity_reasons": [],
        "stock_code": stock_code,
        "market": market,
        "target_date": target.isoformat(),
    }


def _rehash_receipt(receipt):
    receipt = deepcopy(receipt)
    body = dict(receipt)
    body.pop("receipt_hash", None)
    receipt["receipt_hash"] = reg.digest(body)
    return receipt


@pytest.fixture(autouse=True)
def explicit_native_config(monkeypatch):
    """Native service input, not an environment/provider or Pipeline substitute.

    Config loading and full Pipeline imports remain separately blocked when
    dependencies are missing; this suite does not claim to validate them.
    """
    config = Config()
    monkeypatch.setattr("src.stock_analyzer.get_config", lambda: config)
    yield


def bars(n=180):
    x = np.arange(n, dtype=float)
    close = 30 + x * .025 + 1.2 * np.sin(x / 3)
    return pd.DataFrame({"date": pd.bdate_range("2025-01-02", periods=n).date,
                         "open": close - .12, "high": close + .5, "low": close - .5,
                         "close": close, "volume": 100000 + (x % 9) * 1000,
                         "data_source": "synthetic"})


def native_summary(frame=None, target=None):
    frame = bars() if frame is None else frame
    target = target or frame["date"].iloc[-1]
    completed = frame[frame["date"] <= target].copy()
    trend = StockTrendAnalyzer().analyze(completed, "600519")
    supply = build_supply_demand_context(stock_code="600519", history=frame, target_date=target)
    price = build_price_structure_context(stock_code="600519", history=frame, target_date=target, market="cn")
    cost = build_cost_structure_context(stock_code="600519", history=frame, target_date=target, market="cn", chip_data=None)
    momentum = build_volatility_momentum_context(stock_code="600519", history=frame, target_date=target,
                                                market="cn", trend_result=trend, price_structure_context=price)
    pattern = build_pattern_trigger_context(stock_code="600519", history=frame, target_date=target,
                                            market="cn", price_structure_context=price, supply_demand_context=supply)
    return build_stock_factor_decision_summary(
        trend, supply_demand_context=supply, cost_structure_context=cost,
        price_structure_context=price, volatility_momentum_context=momentum,
        pattern_trigger_context=pattern, include_canonical=True,
    )


def test_registry_owners_and_strategy_are_source_bound():
    reg.validate_registry()
    owners = reg.validate_source_bindings(ROOT)
    assert set(owners) == {b.requirement_id for b in reg.EVIDENCE_BINDINGS} | {"DECISION"}
    assert set(STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE) == EXPECTED_REQUIRED
    assert reg.MANIFEST_HASH == reg.digest(reg.manifest_document())
    assert reg.manifest_document()["product_slots"]["baseline_sha256"] == "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823"


def test_legacy_audit_membership_and_hash_are_not_redefined():
    expected = ("strategy_id", "contract_version", "composite_score", "canonical_decision",
                "market_sector_regime", "trend_relative_strength", "supply_demand_volume_price",
                "cost_structure_evidence", "price_structure_evidence", "volatility_momentum_evidence",
                "pattern_trigger_evidence", "multi_timeframe_structure_context")
    assert reg.ledger_evidence_keys() == expected
    assert reg.digest({"schema_version": "stock-factor-evidence-v1", "fields": list(expected)}) == "426d7f21de79ad26e12ea39b4c686b489b90dc24d88f0750bcb5aec421265647"


def test_orphan_strategy_and_duplicate_bindings_are_rejected():
    with pytest.raises(reg.TraceabilityError, match="ORPHAN_STRATEGY"):
        reg.validate_registry(strategy=[replace(reg.STRATEGY_BINDINGS[0], requirement_id="INVENTED")])
    with pytest.raises(reg.TraceabilityError, match="DUPLICATE_BINDING"):
        reg.validate_registry(bindings=reg.EVIDENCE_BINDINGS + (reg.EVIDENCE_BINDINGS[0],))


@pytest.mark.parametrize("missing_id", ["CHAN", "WAVE", "REGIME"])
def test_accepted_requirement_set_cannot_self_certify_after_deletion(missing_id):
    candidate = tuple(b for b in _accepted_bindings() if b.requirement_id != missing_id)
    with pytest.raises(reg.TraceabilityError, match="ACCEPTED_REQUIREMENT_SET_MISMATCH"):
        reg.validate_registry(bindings=candidate)


def test_binding_route_timeframe_and_metric_metadata_are_validated():
    bindings = list(_accepted_bindings())
    bindings[0] = replace(bindings[0], asset_routes=())
    with pytest.raises(reg.TraceabilityError, match="INVALID_ASSET_ROUTE"):
        reg.validate_registry(bindings=bindings)

    bindings = list(_accepted_bindings())
    bindings[0] = replace(bindings[0], timeframe="2h")
    with pytest.raises(reg.TraceabilityError, match="INVALID_TIMEFRAME"):
        reg.validate_registry(bindings=bindings)

    for field, invalid, message in (
        ("kind", "text", "INVALID_METRIC_KIND"),
        ("unit", "mystery", "INVALID_METRIC_UNIT"),
        ("timeframe", "2h", "INVALID_METRIC_TIMEFRAME"),
    ):
        metrics = [dict(metric) for metric in reg.METRIC_BINDINGS]
        metrics[0][field] = invalid
        with pytest.raises(reg.TraceabilityError, match=message):
            reg.validate_registry(metrics=metrics)


def test_native_services_reach_strategy_trace_product_and_learning():
    factor = native_summary()
    assert "strategy_eligibility" in factor
    assert set(factor["strategy_eligibility"]["required_evidence"]) == EXPECTED_REQUIRED
    assert factor["strategy_eligibility"]["required_evidence"]["thirty_minute_trigger"] == "UNKNOWN"
    identity = build_strategy_eligibility_identity(factor["strategy_eligibility"], strategy_id=reg.STOCK_STRATEGY)
    assert identity["strategy_eligibility_state"] in {"UNKNOWN", "INELIGIBLE"}
    trace = reg.validate_runtime_trace(factor)
    assert trace["manifest_hash"] == reg.MANIFEST_HASH
    assert factor["evidence_product_coverage"]["runtime_trace_hash"] == trace["runtime_trace_hash"]
    assert factor["evidence_product_coverage"]["rendered"] is False
    learning = reg.learning_projection(factor)
    expected_cmf = factor["supply_demand_volume_price"]["completed_bar_context"]["close_location_flow"]["cmf_20"]
    assert learning["values"]["daily.cmf20"]["value"] == expected_cmf
    assert learning["values"]["daily.adx"]["value"] is None
    assert learning["values"]["weekly.ma5"]["value"] is None
    assert learning["training_admitted"] is False


def test_display_changes_do_not_rewrite_evidence_or_learning():
    factor = native_summary()
    before = reg.learning_projection(factor)
    factor["investor_brief"] = {"fused_paragraph": "IGNORE ME / 买入 / 99%"}
    factor["conclusion"] = "display only"
    assert reg.learning_projection(factor) == before
    assert "IGNORE ME" not in reg.canonical_json(before)


def test_orphan_evidence_and_stale_trace_fail_before_learning():
    factor = native_summary()
    orphan = deepcopy(factor)
    orphan["invented_evidence"] = {"status": "READY", "value": 99}
    with pytest.raises(reg.TraceabilityError, match="ORPHAN_CANONICAL"):
        reg.learning_projection(orphan)
    factor["supply_demand_volume_price"]["completed_bar_context"]["close_location_flow"]["cmf_20"] = .99
    with pytest.raises(reg.TraceabilityError, match="RUNTIME_TRACE"):
        reg.learning_projection(factor)


def test_a_fake_intraday_ready_flag_cannot_admit_the_deferred_method():
    factor = native_summary()
    factor["multi_timeframe_structure_context"] = {"timeframes": {"30m": {"status": "READY", "trigger": True}}}
    doc = reg.build_strategy_eligibility(factor, schema_version=STRATEGY_ELIGIBILITY_SCHEMA_VERSION)
    assert doc["required_evidence"]["thirty_minute_trigger"] == "UNKNOWN"
    assert doc["state"] != "ELIGIBLE"


def test_hard_veto_is_preserved_despite_indicator_words_or_high_score():
    trend = TrendAnalysisResult(code="600519")
    trend.signal_score = 100
    trend.macd_status = MACDStatus.GOLDEN_CROSS_ZERO
    trend.macd_signal = "强烈买入信号"
    factor = build_stock_factor_decision_summary(
        trend, include_canonical=True,
        daily_market_context={"market_light": {"status": "red", "data_quality": "ok"}},
    )
    assert factor["canonical_decision"]["hard_veto"] is True
    assert factor["canonical_decision"]["action"] == "PASS"
    assert factor["strategy_eligibility"]["required_evidence"]["market_regime_permission"] == "FAILED"
    assert "强烈买入信号" not in reg.canonical_json(factor)


def test_native_pattern_dedup_reuses_one_same_swing():
    result = _dedupe_patterns([
        {"pattern_type": "CONTRACTION_BASE", "subtype": "VCP", "correlation_group": "same-swing"},
        {"pattern_type": "CONTRACTION_BASE", "subtype": "FLAT_BASE", "correlation_group": "same-swing"},
    ])
    assert len(result) == 1
    assert result[0]["subtype"] == "VCP"


def test_future_bars_do_not_change_native_prior_evidence():
    frame = bars(200)
    target = frame["date"].iloc[159]
    baseline = native_summary(frame.iloc[:160].copy(), target)
    perturbed = frame.copy()
    perturbed.loc[160:, ["open", "high", "low", "close"]] *= 4
    actual = native_summary(perturbed, target)
    assert reg.learning_projection(actual) == reg.learning_projection(baseline)
    assert actual["evidence_traceability"] == baseline["evidence_traceability"]


def test_insufficient_warmup_never_creates_momentum_readiness():
    frame = bars(15)
    payload = build_volatility_momentum_context(stock_code="600519", history=frame, target_date=frame["date"].iloc[-1])
    assert payload["status"] == "MISSING"
    assert payload["reason"] == "WARMUP_INSUFFICIENT"


def test_macd_real_cross_is_descriptive_and_formula_source_unchanged():
    analyzer = StockTrendAnalyzer()
    frame = bars(30)
    frame["MACD_DIF"] = 1.0
    frame["MACD_DEA"] = 1.1
    frame["MACD_BAR"] = -.2
    frame.loc[29, ["MACD_DIF", "MACD_DEA", "MACD_BAR"]] = [1.2, 1.1, .2]
    result = TrendAnalysisResult(code="600519")
    analyzer._analyze_macd(frame, result)
    assert result.macd_status == MACDStatus.GOLDEN_CROSS_ZERO
    assert "不是独立买点" in result.macd_signal
    assert "强烈买入" not in result.macd_signal
    old = subprocess.check_output(["git", "show", "HEAD:src/stock_analyzer.py"], cwd=ROOT).decode("utf-8")
    new = (ROOT / "src/stock_analyzer.py").read_text(encoding="utf-8")
    for name in ("_calculate_macd", "_calculate_rsi", "_generate_signal"):
        def function_ast(text):
            return ast.dump(next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == name), include_attributes=False)
        assert function_ast(old) == function_ast(new), name


def test_recursive_seed_sensitivity_is_measured_not_claimed_invariant():
    frame = bars(240)
    analyzer = StockTrendAnalyzer()
    long = analyzer._calculate_macd(frame.copy())["MACD_DIF"].iloc[-1]
    short = analyzer._calculate_macd(frame.tail(40).copy())["MACD_DIF"].iloc[-1]
    assert np.isfinite(long) and np.isfinite(short)
    assert abs(long - short) > 0  # An EMA depends on initialization; do not fake prefix invariance here.


@pytest.mark.parametrize("mutation", ["version", "config", "warmup", "source", "completed"])
def test_forged_ready_is_not_consumed_by_learning_or_product(mutation):
    from src.services.v2_5_evidence_coverage import compile_product_coverage
    factor = native_summary()
    context = factor["volatility_momentum_evidence"]["context"]
    if mutation == "version":
        context["algorithm_version"] = "old-or-unapproved"
    elif mutation == "config":
        context["config_hash"] = "0" * 64
    elif mutation == "warmup":
        context["observations"] = 1
    elif mutation == "source":
        context["source_alignment"]["status"] = "UNPROVEN"
    else:
        context["completed_bar_only"] = False
    context["status"] = "READY"
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    trace = reg.validate_runtime_trace(factor)
    observed = next(x for x in trace["observations"] if x["requirement_id"] == "MOMENTUM")
    assert observed["state"] == "UNKNOWN"
    assert reg.learning_projection(factor)["values"]["daily.macd_dif"]["value"] is None
    slots = {x["slot_id"]: x for x in compile_product_coverage(factor)["slots"]}
    assert slots["detail.timeframe.momentum_divergence"]["state"] == "DATA_INSUFFICIENT"


def test_wrong_supply_version_is_not_a_strategy_confirmation():
    factor = native_summary()
    context = factor["supply_demand_volume_price"]["completed_bar_context"]
    context.update(status="READY", state="DEMAND_PRESSURE", schema_version="unapproved")
    doc = reg.build_strategy_eligibility(factor, schema_version=STRATEGY_ELIGIBILITY_SCHEMA_VERSION)
    assert doc["required_evidence"]["volume_price_confirmation"] == "UNKNOWN"


def test_macd_insufficient_history_does_not_turn_into_neutral_fact():
    result = TrendAnalysisResult(code="600519")
    result.macd_signal = "数据不足"
    assert reg.describe_macd_state(result) == ""




def test_strict_supply_receipt_binds_actual_target_input_producer_and_output():
    factor = native_summary()
    context = factor["supply_demand_volume_price"]["completed_bar_context"]
    target = pd.to_datetime(context["target_date"]).date()
    identity = _strict_daily_identity(target)
    receipt = reg.build_method_execution_receipt(
        "SUPPLY",
        output=context,
        asset_route="STOCK",
        stock_code="600519",
        market="cn",
        target_date=target,
        timeframe="daily",
        input_identity=identity,
    )
    factor["method_execution_receipt_policy"] = "REQUIRED"
    factor["method_execution_receipts"] = {"SUPPLY": receipt}
    trace = reg.build_runtime_trace(factor)
    supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
    assert supply["state"] == "READY"
    assert supply["reason"] == "METHOD_INVOCATION_VERIFIED"

    wrong_target = reg.build_method_execution_receipt(
        "SUPPLY",
        output=context,
        asset_route="STOCK",
        stock_code="600519",
        market="cn",
        target_date="2099-01-01",
        timeframe="daily",
        input_identity=identity,
    )
    factor["method_execution_receipts"]["SUPPLY"] = wrong_target
    trace = reg.build_runtime_trace(factor)
    supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
    assert supply["state"] == "UNKNOWN"
    assert "TARGET" in supply["reason"]

    wrong_output = deepcopy(receipt)
    wrong_output["output_hash"] = "0" * 64
    factor["method_execution_receipts"]["SUPPLY"] = _rehash_receipt(wrong_output)
    trace = reg.build_runtime_trace(factor)
    supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
    assert supply["state"] == "UNKNOWN"
    assert supply["reason"] == "METHOD_RECEIPT_OUTPUT_MISMATCH"

    wrong_source = deepcopy(receipt)
    wrong_source["producer"]["source_sha256"] = "0" * 64
    factor["method_execution_receipts"]["SUPPLY"] = _rehash_receipt(wrong_source)
    trace = reg.build_runtime_trace(factor)
    supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
    assert supply["state"] == "UNKNOWN"
    assert supply["reason"] == "METHOD_RECEIPT_PRODUCER_MISMATCH"

    for identity_mutation, reason in (
        ({"adjustment_basis": None}, "METHOD_PRICE_IDENTITY_UNPROVEN"),
        ({"data_snapshot_identity": None}, "METHOD_INPUT_IDENTITY_MISSING"),
        ({"stock_code": "000001"}, "METHOD_INPUT_STOCK_CODE_MISMATCH"),
        ({"market": "us"}, "METHOD_INPUT_MARKET_MISMATCH"),
    ):
        bad_identity = {**identity, **identity_mutation}
        bad_receipt = reg.build_method_execution_receipt(
            "SUPPLY",
            output=context,
            asset_route="STOCK",
            stock_code="600519",
            market="cn",
            target_date=target,
            timeframe="daily",
            input_identity=bad_identity,
        )
        factor["method_execution_receipts"]["SUPPLY"] = bad_receipt
        trace = reg.build_runtime_trace(factor)
        supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
        assert supply["state"] == "UNKNOWN"
        assert supply["reason"] == reason


@pytest.mark.parametrize("mutation", ["schema", "stock", "benchmark", "source", "basis", "identity"])
def test_strict_trend_rs_rejects_forged_upstream_semantics(mutation):
    trend = TrendAnalysisResult(code="600519")
    target = "2026-09-30"
    identity = {
        "stock": {
            **_strict_daily_identity(pd.Timestamp(target).date()),
            "stock_code": "600519",
        },
        "benchmark": {
            **_strict_daily_identity(pd.Timestamp(target).date(), stock_code="510300"),
            "stock_code": "510300",
        },
    }
    rs = {
        "schema_version": "relative-strength-v1",
        "family": "trend_relative_strength",
        "status": "READY",
        "market": "cn",
        "stock_code": "600519",
        "target_date": target,
        "benchmark": {"code": "510300", "source": "synthetic"},
        "stock": {},
        "relative": {"state": "OUTPERFORMING"},
        "data_quality": {
            "source_alignment": "MATCHED",
            "stock_endpoint_sources": ["synthetic"],
        },
        "input_identity": deepcopy(identity),
    }
    if mutation == "schema":
        rs["schema_version"] = "forged"
    elif mutation == "stock":
        rs["stock_code"] = "000001"
    elif mutation == "benchmark":
        rs["benchmark"]["code"] = "159919"
    elif mutation == "source":
        rs["data_quality"]["source_alignment"] = "UNPROVEN"
    elif mutation == "basis":
        rs["input_identity"]["benchmark"]["adjustment_basis"] = "raw"
    else:
        rs.pop("input_identity")

    factor = build_stock_factor_decision_summary(
        trend,
        relative_strength_context=rs,
        method_execution_receipts={},
        include_canonical=True,
    )
    trace = reg.validate_runtime_trace(factor)
    observed = next(item for item in trace["observations"] if item["requirement_id"] == "TREND_RS")
    assert observed["state"] == "UNKNOWN"


def test_regime_parent_date_mismatch_is_unknown_under_strict_receipts():
    trend = TrendAnalysisResult(code="600519")
    factor = build_stock_factor_decision_summary(
        trend,
        daily_market_context={
            "trade_date": "2026-09-29",
            "region": "cn",
            "market_light": {"status": "green", "data_quality": "ok"},
        },
        market_structure_context={
            "status": "ok",
            "market": "cn",
            "trade_date": "2026-09-30",
            "stock_market_position": {},
        },
        method_execution_receipts={},
        include_canonical=True,
    )
    trace = reg.validate_runtime_trace(factor)
    observed = next(item for item in trace["observations"] if item["requirement_id"] == "REGIME")
    assert observed["state"] == "UNKNOWN"
    assert observed["reason"] == "REGIME_UPSTREAM_DATE_MISMATCH"


def test_ma60_is_missing_before_sixty_rows_and_exact_at_sixty():
    analyzer = StockTrendAnalyzer()
    for observations in (30, 59):
        calculated = analyzer._calculate_mas(bars(observations))
        assert pd.isna(calculated["MA60"].iloc[-1])
        assert pd.notna(calculated["MA20"].iloc[-1])
    calculated = analyzer._calculate_mas(bars(60))
    assert calculated["MA60"].iloc[-1] == pytest.approx(calculated["close"].mean())


def test_learning_metric_economic_domains_reject_invalid_values():
    factor = native_summary()
    factor["volatility_momentum_evidence"]["context"]["momentum"]["rsi"]["rsi_6"] = 1000.0
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    assert reg.learning_projection(factor)["values"]["daily.rsi6"]["value"] is None

    factor = native_summary()
    factor["volatility_momentum_evidence"]["context"]["volatility"]["realized_volatility_20d_annualized_pct"] = -12.0
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    assert reg.learning_projection(factor)["values"]["daily.realized_volatility20"]["value"] is None

    metrics = {metric["id"]: metric for metric in reg.METRIC_BINDINGS}
    rsi_metric = metrics["daily.rsi6"]
    volatility_metric = metrics["daily.realized_volatility20"]
    macd_metric = metrics["daily.macd_dif"]
    price_metric = metrics["daily.price"]
    cost_metric = metrics["daily.cost20_proxy"]
    ma_metric = metrics["weekly.ma5"]

    assert reg._metric_value_valid(rsi_metric, float("nan")) is False
    assert reg._metric_value_valid(rsi_metric, float("inf")) is False
    assert reg._metric_value_valid(volatility_metric, -0.01) is False
    assert reg._metric_value_valid(rsi_metric, -0.1) is False
    assert reg._metric_value_valid(rsi_metric, 0.0) is True
    assert reg._metric_value_valid(rsi_metric, 100.0) is True
    assert reg._metric_value_valid(rsi_metric, 100.1) is False

    for invalid in (float("nan"), float("inf"), True, False):
        assert reg._metric_value_valid(macd_metric, invalid) is False
    for positive_only_metric in (price_metric, cost_metric, ma_metric):
        assert reg._metric_value_valid(positive_only_metric, 0.0) is False
        assert reg._metric_value_valid(positive_only_metric, 1.0) is True


@pytest.mark.parametrize(
    ("dif", "dea", "bar"),
    [
        (-0.27126150004790617, -0.29420148919316635, 0.04587997829052037),
        (0.0, 0.0, 0.0),
        (0.27126150004790617, 0.29420148919316635, -0.04587997829052037),
    ],
)
def test_learning_projection_preserves_finite_signed_macd_values(dif, dea, bar):
    factor = native_summary()
    macd = factor["volatility_momentum_evidence"]["context"]["momentum"]["macd"]
    macd.update(dif=dif, dea=dea, bar=bar)
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)

    values = reg.learning_projection(factor)["values"]
    assert values["daily.macd_dif"] == {"value": pytest.approx(dif), "state": "READY"}
    assert values["daily.macd_dea"] == {"value": pytest.approx(dea), "state": "READY"}
    assert values["daily.macd_bar"] == {"value": pytest.approx(bar), "state": "READY"}

    for metric_id in ("daily.macd_dif", "daily.macd_dea", "daily.macd_bar"):
        metric = next(item for item in reg.METRIC_BINDINGS if item["id"] == metric_id)
        assert metric["unit"] == "PRICE_BASIS_CURRENCY"


def test_noncanonical_legacy_call_does_not_trigger_new_product_or_learning():
    result = TrendAnalysisResult(code="600519")
    factor = build_stock_factor_decision_summary(result)
    assert "canonical_decision" not in factor
    assert "evidence_traceability" not in factor
    assert "evidence_product_coverage" not in factor
