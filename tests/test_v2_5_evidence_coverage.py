"""Exact-reference and normalized-frame tests; no live provider or renderer."""
from copy import deepcopy
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest

from src.services import evidence_traceability_registry as reg
from src.services.v2_5_evidence_coverage import (
    compile_input_coverage, compile_product_coverage,
    compile_whole_product_scope_gate, verify_original_mapping,
)
from tests.test_evidence_traceability_registry import (  # noqa: F401
    explicit_native_config,
    native_summary,
    native_summary_with_mtf,
)

ROOT = Path(__file__).resolve().parents[1]


def input_fixture():
    frame = pd.DataFrame({"date": pd.bdate_range("2025-06-02", periods=30).date,
                          "open": 10., "high": 11., "low": 9., "close": 10.5,
                          "volume": 100000., "data_source": "unit-test-provider"})
    target = frame.date.iloc[-1].isoformat()
    meta = {"provider": "unit-test-provider", "generation": "unit-test-generation",
            "units": dict.fromkeys(("open", "high", "low", "close"), "CNY") | {"volume": "share"},
            "target_date": target, "timeframe": "1d", "completed_bar_only": True,
            "adjustment_basis": "raw", "available_at": target + "T15:01:00+08:00",
            "decision_time": target + "T19:00:00+08:00", "synthetic": True}
    return frame, meta


def test_protected_original_is_read_from_independent_git_bytes_not_regenerated():
    raw = subprocess.check_output(["git", "show", "HEAD:templates/v2_5/STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html"], cwd=ROOT)
    receipt = verify_original_mapping(raw)
    assert receipt["bytes"] == 125919
    assert receipt["baseline_sha256"] == "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823"
    assert receipt["rendered"] is False
    assert receipt["dynamic_fidelity_proven"] is False
    for changed in (raw[:-1], raw.replace(b"\n", b"\r\n"), raw[:4096] + raw[4196:]):
        with pytest.raises(reg.TraceabilityError, match="ORIGINAL_BYTES_MISMATCH"):
            verify_original_mapping(changed)


def test_semantic_slots_preserve_the_independent_accepted_contract():
    expected = {
        "first_screen.conclusion", "first_screen.price_or_valuation", "first_screen.higher_timeframe_thesis",
        "first_screen.short_term_conflict_or_trigger", "first_screen.supply_distribution_risk",
        "first_screen.trigger", "first_screen.invalidation", "first_screen.material_risks",
        "detail.timeframe.price_position", "detail.timeframe.trend_ma", "detail.timeframe.volume_price",
        "detail.timeframe.cost_structure", "detail.timeframe.price_structure", "detail.timeframe.momentum_divergence",
        "detail.timeframe.pattern_trigger", "detail.timeframe.support_resistance", "detail.timeframe.confirmation_invalidation",
        "asset.stock.quality_growth", "asset.stock.valuation", "asset.etf.underlying_valuation",
        "asset.etf.premium_discount", "asset.etf.liquidity_spread", "asset.etf.tracking_concentration",
        "audit.coverage", "audit.data_time_quality", "audit.historical_reference", "audit.current_probability",
    }
    document = reg.load_slot_map()
    assert {s["id"] for s in document["slots"]} == expected
    changed = deepcopy(document)
    changed["slots"][0]["paths"] = ["investor_brief.fused_paragraph"]
    with pytest.raises(reg.TraceabilityError, match="ORPHAN_PRODUCT_PATH"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    changed["rendering_authorized"] = True
    with pytest.raises(reg.TraceabilityError, match="SLOT_AUTHORITY"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    changed["slots"] = changed["slots"][:-1]
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_SET_MISMATCH"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    changed["slots"] = changed["slots"][:1]
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_SET_MISMATCH"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    changed["slots"] = list(reversed(changed["slots"]))
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_MAP_IDENTITY_MISMATCH"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    next(
        slot for slot in changed["slots"]
        if slot["id"] == "first_screen.supply_distribution_risk"
    )["requirements"] = ["SUPPLY"]
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_REQUIREMENT_SET_MISMATCH"):
        reg.load_slot_map(changed)


def test_whole_product_scope_gate_rejects_universe_or_slot_deletion():
    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["requirements"].pop()
    with pytest.raises(reg.TraceabilityError, match="WHOLE_PRODUCT_REQUIREMENT_SET_MISMATCH"):
        compile_whole_product_scope_gate(trace)
    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["requirements"][0][
        "contract_role"
    ] = "DEFERRED"
    with pytest.raises(
        reg.TraceabilityError,
        match="WHOLE_PRODUCT_REQUIREMENT_BINDING_MISMATCH",
    ):
        compile_whole_product_scope_gate(trace)

    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["planes"].pop()
    with pytest.raises(
        reg.TraceabilityError,
        match="WHOLE_PRODUCT_PLANE_BINDING_MISMATCH",
    ):
        compile_whole_product_scope_gate(trace)

    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["valuation_route_ids"] = ("ETF",)
    with pytest.raises(reg.TraceabilityError, match="VALUATION_ROUTE_SET_MISMATCH"):
        compile_whole_product_scope_gate(trace)

    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["known_atomic_subfield_obligations"] = ()
    with pytest.raises(reg.TraceabilityError, match="ATOMIC_SUBFIELD_SET_MISMATCH"):
        compile_whole_product_scope_gate(trace)
    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["full_domain_data_gate"][
        "full_domain_product_ready"
    ] = True
    with pytest.raises(
        reg.TraceabilityError,
        match="WHOLE_PRODUCT_FULL_DOMAIN_GATE_MISMATCH",
    ):
        compile_whole_product_scope_gate(trace)

    factor = native_summary()
    slot_map = deepcopy(reg.load_slot_map())
    slot_map["slots"].pop()
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_SET_MISMATCH"):
        compile_whole_product_scope_gate(
            factor["evidence_traceability"],
            slot_map=slot_map,
        )


def test_synthetic_trial_is_never_misreported_as_real_data():
    frame, meta = input_fixture()
    receipt = compile_input_coverage(frame, metadata=meta)
    assert receipt["manifest_hash"] == reg.MANIFEST_HASH
    assert receipt["row_count"] == len(frame)
    assert receipt["synthetic"] is True
    assert all(r["state"] == "RIGHTS_OR_PIT_BLOCKED" for r in receipt["coverage"])
    assert receipt["pit_admitted"] is False
    assert receipt["product_ready"] is False
    assert receipt["training_authorized"] is False


def test_attested_source_state_is_not_rights_or_algorithm_execution_proof():
    # Tests the metadata branch only: these bytes are still synthetic test inputs.
    frame, meta = input_fixture()
    meta["synthetic"] = False
    receipt = compile_input_coverage(frame, metadata=meta)
    rows = {r["requirement_id"]: r for r in receipt["coverage"]}
    assert rows["SUPPLY"]["state"] == "BASE_BYTES_AVAILABLE"
    assert rows["SUPPLY"]["method_executed"] is False
    assert receipt["rights_state"] == "NOT_INDEPENDENTLY_VERIFIED"
    assert receipt["pit_admitted"] is False


@pytest.mark.parametrize("mutation", ["units", "source", "nan", "order", "future", "basis", "intraday_date", "completed", "boolean"])
def test_invalid_observed_inputs_are_rejected(mutation):
    frame, meta = input_fixture()
    if mutation == "units":
        meta["units"]["volume"] = "lot"
    elif mutation == "source":
        frame.loc[0, "data_source"] = "second-provider"
    elif mutation == "nan":
        frame.loc[0, "close"] = np.nan
    elif mutation == "order":
        frame = frame.iloc[::-1].copy()
    elif mutation == "future":
        meta["available_at"] = "2099-01-01T12:00:00+08:00"
    elif mutation == "basis":
        meta["adjustment_basis"] = "unspecified"
    elif mutation == "intraday_date":
        frame["date"] = pd.to_datetime(frame.date) + pd.Timedelta(hours=1)
    elif mutation == "completed":
        meta["completed_bar_only"] = False
    else:
        frame["volume"] = True
    with pytest.raises(reg.TraceabilityError):
        compile_input_coverage(frame, metadata=meta)


def test_adjusted_prices_require_factor_asof_identity():
    frame, meta = input_fixture()
    meta.update(synthetic=False, adjustment_basis="qfq")
    receipt = compile_input_coverage(frame, metadata=meta)
    assert receipt["basis_identity_declared"] is False
    assert all(r["state"] == "RIGHTS_OR_PIT_BLOCKED" for r in receipt["coverage"])


def test_daily_cmf_does_not_fill_weekly_monthly_or_intraday_slots():
    factor = native_summary()
    rows = {r["slot_id"]: r for r in compile_product_coverage(factor)["slots"]}
    states = rows["detail.timeframe.volume_price"]["timeframe_states"]
    assert states["daily"] == "EVIDENCE_AVAILABLE"
    assert all(states[tf] == "DATA_INSUFFICIENT" for tf in ("weekly", "monthly", "60m", "30m", "15m", "5m"))
    assert all(row["projection_state"] == "NOT_RENDERED" for row in rows.values())


def test_product_coverage_consumes_the_trace_matrix_for_actual_weekly_structure_and_momentum():
    factor = native_summary_with_mtf()
    coverage = compile_product_coverage(factor)
    rows = {row["slot_id"]: row for row in coverage["slots"]}
    trace_matrix = factor["evidence_traceability"]["timeframe_family_matrix"]

    assert coverage["timeframe_family_matrix_hash"] == trace_matrix["matrix_hash"]
    assert coverage["method_window_policy_hash"] == factor["evidence_traceability"]["method_window_policy_hash"]
    assert len(trace_matrix["cells"]) == 56
    trace_universe = factor["evidence_traceability"]["complete_research_universe"]
    assert coverage["complete_research_universe_hash"] == trace_universe["universe_hash"]
    assert coverage["complete_research_universe_schema_version"] == "complete-research-universe-view-v1"
    assert coverage["complete_research_universe_counts"] == {
        "requirements": 26,
        "methods": 43,
        "strategy_bindings": 13,
        "technical_matrix_cells": 56,
    }
    scope_gate = coverage["whole_product_scope_gate"]
    assert scope_gate["full_scope_accounted_pass"] is True
    assert scope_gate["required_requirement_count"] == 26
    assert scope_gate["required_slot_count"] == 27
    assert scope_gate["required_data_domain_counts"] == {
        "GLOBAL": 18, "CN_MARKET": 18, "SECTOR": 7,
        "ETF": 12, "STOCK": 17, "RECORD": 4,
    }
    assert scope_gate["dynamic_product_admitted"] is False
    assert set(scope_gate["required_valuation_route_ids"]) == {
        "MATURE_INDUSTRIAL_CONSUMER", "HIGH_GROWTH_TECH", "BANK", "INSURANCE",
        "CYCLICAL_RESOURCE", "UTILITY_INFRASTRUCTURE", "REIT",
        "LOSS_MAKING_EARLY_GROWTH", "ETF",
    }
    assert set(scope_gate["unresolved_atomic_subfields"]) == {
        "GLOBAL_US_ACTUAL_INFLATION", "GLOBAL_US_CORE_INFLATION",
        "GLOBAL_US_SERVICE_INFLATION", "GLOBAL_US_5Y_INFLATION_EXPECTATION",
        "GLOBAL_SOX_INDEX", "GLOBAL_TECH_ETF_FLOW",
        "GLOBAL_SEMICONDUCTOR_SOFTWARE_RELATIVE",
    }
    assert scope_gate["atomic_subfield_binding_complete"] is False
    data_gate = coverage["full_domain_data_gate"]
    assert data_gate["full_domain_accounted_pass"] is True
    assert data_gate["full_domain_product_ready"] is False
    assert data_gate["accounted_object_count"] == 76
    assert rows["detail.timeframe.price_structure"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    # Complete weekly structures and volume remain material; unverified
    # recursive oscillator output must not create weekly MOMENTUM coverage.
    weekly = factor["multi_timeframe_structure_context"]["timeframes"]["weekly"]
    assert weekly["macd_startup_stability"]["status"] == "MISSING"
    assert weekly["rsi_startup_stability"]["status"] == "MISSING"
    assert rows["detail.timeframe.momentum_divergence"]["timeframe_states"]["weekly"] == "DATA_INSUFFICIENT"
    assert rows["detail.timeframe.momentum_divergence"]["timeframe_canonical_paths"]["weekly"] == ()
    assert rows["detail.timeframe.trend_ma"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    assert rows["detail.timeframe.volume_price"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    assert rows["detail.timeframe.cost_structure"]["timeframe_states"]["weekly"] == "DATA_INSUFFICIENT"
    assert all(row["projection_state"] == "NOT_RENDERED" for row in rows.values())


def test_product_coverage_exposes_only_current_intraday_context_families():
    # The existing EVIDENCE_AVAILABLE assertions require a lawful synthetic share unit.
    factor = native_summary_with_mtf(intraday=True, volume_unit="share")
    rows = {row["slot_id"]: row for row in compile_product_coverage(factor)["slots"]}

    for timeframe in ("60m", "30m", "15m", "5m"):
        assert rows["detail.timeframe.trend_ma"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.volume_price"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.momentum_divergence"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.cost_structure"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        assert rows["detail.timeframe.price_structure"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        assert rows["detail.timeframe.pattern_trigger"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"


def test_product_coverage_rejects_unknown_intraday_volume_without_erasing_price_momentum():
    # Same deterministic MTF producer, but without an admitted source volume unit.
    factor = native_summary_with_mtf(intraday=True, volume_unit="UNKNOWN")
    coverage = compile_product_coverage(factor)
    rows = {row["slot_id"]: row for row in coverage["slots"]}
    matrix = factor["evidence_traceability"]["timeframe_family_matrix"]
    assert len(matrix["cells"]) == 56
    assert coverage["timeframe_family_matrix_hash"] == matrix["matrix_hash"]

    for timeframe in ("60m", "30m", "15m", "5m"):
        produced = factor["multi_timeframe_structure_context"]["timeframes"][timeframe]
        assert produced["status"] == "PARTIAL"
        assert "VOLUME" not in produced["admitted_methods"]
        assert produced["trend"]["volume_status"] is None
        assert produced["strategy_admitted"] is False
        assert produced["learning_admitted"] is False

        assert rows["detail.timeframe.volume_price"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        assert rows["detail.timeframe.trend_ma"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.momentum_divergence"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.cost_structure"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        assert rows["detail.timeframe.price_structure"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        assert rows["detail.timeframe.pattern_trigger"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"

    assert all(row["projection_state"] == "NOT_RENDERED" for row in rows.values())
