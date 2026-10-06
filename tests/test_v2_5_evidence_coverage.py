"""Exact-reference and normalized-frame tests; no live provider or renderer."""
from copy import deepcopy
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest

from src.services import evidence_traceability_registry as reg
from src.services.v2_5_evidence_coverage import (
    compile_input_coverage, compile_product_coverage, verify_original_mapping,
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
    assert rows["detail.timeframe.price_structure"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    assert rows["detail.timeframe.momentum_divergence"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    assert any(
        path.startswith("multi_timeframe_structure_context.timeframes.weekly")
        for path in rows["detail.timeframe.momentum_divergence"]["timeframe_canonical_paths"]["weekly"]
    )
    assert rows["detail.timeframe.trend_ma"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    assert rows["detail.timeframe.volume_price"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    assert rows["detail.timeframe.cost_structure"]["timeframe_states"]["weekly"] == "DATA_INSUFFICIENT"
    assert all(row["projection_state"] == "NOT_RENDERED" for row in rows.values())


def test_product_coverage_exposes_only_current_intraday_context_families():
    factor = native_summary_with_mtf(intraday=True)
    rows = {row["slot_id"]: row for row in compile_product_coverage(factor)["slots"]}

    for timeframe in ("60m", "30m", "15m", "5m"):
        assert rows["detail.timeframe.trend_ma"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.volume_price"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.momentum_divergence"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.cost_structure"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        assert rows["detail.timeframe.price_structure"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        assert rows["detail.timeframe.pattern_trigger"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
