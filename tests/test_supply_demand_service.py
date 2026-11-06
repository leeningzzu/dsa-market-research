# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from src.services.supply_demand_service import (
    build_supply_demand_context,
    compile_volume_price_phase_context,
)


def _history(*, periods: int = 30, source: str | None = "Fetcher", direction: int = 1) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=periods)
    rows = []
    price = 100.0
    for index, day in enumerate(dates):
        price += direction * (0.8 if index % 3 != 0 else -0.2)
        high = price + 1.0
        low = price - 1.0
        close = high - 0.2 if direction > 0 else low + 0.2
        row = {
            "date": day,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0 + index * 25.0,
        }
        if source is not None:
            row["data_source"] = source
        rows.append(row)
    return pd.DataFrame(rows)




def _completed_5m_bars() -> pd.DataFrame:
    """48 separately completed Shanghai bars in one real session's two halves."""
    morning = pd.date_range(
        "2026-10-28 09:35", periods=24, freq="5min", tz="Asia/Shanghai",
    )
    afternoon = pd.date_range(
        "2026-10-28 13:05", periods=24, freq="5min", tz="Asia/Shanghai",
    )
    times = morning.append(afternoon)
    return pd.DataFrame({
        "date": times,
        "high": [100.6 + i * 0.03 for i in range(48)],
        "low": [99.4 + i * 0.03 for i in range(48)],
        "close": [100.0 + i * 0.03 for i in range(48)],
        "volume": [1000.0 + i * 4 for i in range(48)],
        "data_source": ["FixtureFetcher"] * 48,
    })


def test_completed_intraday_supply_does_not_collapse_48_bars_into_one_day():
    bars = _completed_5m_bars()
    result = build_supply_demand_context(
        stock_code="600519", history=bars, target_date=bars.iloc[-1]["date"].date(),
        timeframe="5m",
    )
    assert result["status"] == "READY"
    assert result["timeframe"] == "5m"
    assert result["data_quality"]["observations"] == 21
    assert result["data_quality"]["start_bar_end"] != result["data_quality"]["end_bar_end"]
    assert result["data_quality"]["end_bar_end"].endswith("07:00:00+00:00")
    assert result["relative_volume"]["volume_ratio_20_completed_bars"] > 0
    assert -1 <= result["close_location_flow"]["cmf_20"] <= 1
    assert result["institutional_intent_claim"] == "NOT_INFERRED"
    assert result["independent_action_authority"] is False


def test_intraday_supply_duplicate_timestamp_and_mixed_source_reject_readiness():
    bars = _completed_5m_bars()
    duplicated = bars.copy()
    duplicated.loc[duplicated.index[-1], "date"] = duplicated.loc[duplicated.index[-2], "date"]
    rejected = build_supply_demand_context(
        stock_code="600519", history=duplicated, target_date=bars.iloc[-1]["date"].date(),
        timeframe="5m",
    )
    assert rejected["status"] == "UNKNOWN"
    assert rejected["reason"] == "TIMEFRAME_BAR_TIMESTAMP_DUPLICATE"

    mixed = bars.copy()
    mixed.loc[mixed.index[-1], "data_source"] = "OtherFetcher"
    mixed_result = build_supply_demand_context(
        stock_code="600519", history=mixed, target_date=bars.iloc[-1]["date"].date(),
        timeframe="5m",
    )
    assert mixed_result["status"] == "PARTIAL"
    assert mixed_result["reason"] == "SOURCE_ALIGNMENT_UNPROVEN"

def test_completed_single_source_demand_pressure_is_ready_and_auditable():
    history = _history(direction=1)
    target = history.iloc[-1]["date"].date()
    context = build_supply_demand_context(stock_code="600519", history=history, target_date=target)

    assert context["status"] == "READY"
    assert context["state"] == "DEMAND_PRESSURE"
    assert context["relative_volume"]["volume_ratio_20d"] > 1
    assert context["directional_volume"]["signed_volume_balance"] > 0
    assert context["close_location_flow"]["cmf_20"] > 0
    assert context["data_quality"]["observations"] == 21
    assert context["institutional_intent_claim"] == "NOT_INFERRED"
    assert context["completed_bar_only"] is True
    assert context["algorithm_version"] == "supply-demand-volume-price-v1"
    assert len(context["config_hash"]) == 64


def test_supply_pressure_is_observable_without_asserting_institutional_intent():
    history = _history(direction=-1)
    target = history.iloc[-1]["date"].date()
    context = build_supply_demand_context(stock_code="000001", history=history, target_date=target)

    assert context["status"] == "READY"
    assert context["state"] == "SUPPLY_PRESSURE"
    assert context["directional_volume"]["signed_volume_balance"] < 0
    assert context["close_location_flow"]["cmf_20"] < 0
    text = str(context)
    assert "吸筹" not in text
    assert "出货" not in text
    assert "主力" not in text


def test_warmup_and_target_date_fail_closed_and_future_rows_are_trimmed():
    short = _history(periods=20)
    target = short.iloc[-1]["date"].date()
    missing = build_supply_demand_context(stock_code="600519", history=short, target_date=target)
    assert missing["status"] == "MISSING"
    assert missing["reason"] == "WARMUP_INSUFFICIENT"

    history = _history(periods=30)
    exact_target = history.iloc[-2]["date"].date()
    trimmed = build_supply_demand_context(stock_code="600519", history=history, target_date=exact_target)
    assert trimmed["status"] == "READY"
    assert trimmed["data_quality"]["end_date"] == exact_target.isoformat()

    future_target = history.iloc[-1]["date"].date() + timedelta(days=1)
    absent = build_supply_demand_context(stock_code="600519", history=history, target_date=future_target)
    assert absent["status"] == "MISSING"
    assert absent["reason"] == "TARGET_DATE_BAR_MISSING"


def test_missing_or_mixed_source_is_partial_not_ready():
    history = _history(source=None)
    target = history.iloc[-1]["date"].date()
    missing_source = build_supply_demand_context(stock_code="600519", history=history, target_date=target)
    assert missing_source["status"] == "PARTIAL"
    assert missing_source["reason"] == "SOURCE_ALIGNMENT_UNPROVEN"

    mixed = _history()
    mixed.loc[mixed.index[-1], "data_source"] = "OtherFetcher"
    mixed_source = build_supply_demand_context(stock_code="600519", history=mixed, target_date=target)
    assert mixed_source["status"] == "PARTIAL"
    assert mixed_source["data_quality"]["source_alignment"] == "UNPROVEN"


def test_non_positive_volume_fails_closed_instead_of_claiming_balance():
    history = _history()
    history.loc[history.index[-1], "volume"] = 0.0
    target = history.iloc[-1]["date"].date()
    context = build_supply_demand_context(stock_code="600519", history=history, target_date=target)
    assert context["status"] == "UNKNOWN"
    assert context["reason"] == "NON_POSITIVE_VOLUME"
    assert context["state"] == "UNKNOWN"


def test_zero_spread_bar_does_not_create_divide_by_zero_or_intent_claim():
    history = _history()
    history.loc[history.index[-1], "high"] = history.loc[history.index[-1], "close"]
    history.loc[history.index[-1], "low"] = history.loc[history.index[-1], "close"]
    target = history.iloc[-1]["date"].date()
    context = build_supply_demand_context(stock_code="600519", history=history, target_date=target)
    assert context["status"] == "READY"
    assert -1 <= context["close_location_flow"]["cmf_20"] <= 1
    assert context["institutional_intent_claim"] == "NOT_INFERRED"


def _phase_parent(*, supply_state: str, event_state: str, range_state: str = "BETWEEN_CONFIRMED_LEVELS"):
    supply = {
        "schema_version": "supply-demand-volume-price-v1",
        "status": "READY", "reason": "COMPLETED_OHLCV_SINGLE_SOURCE",
        "stock_code": "600519", "target_date": "2026-10-30", "timeframe": "daily",
        "state": supply_state, "completed_bar_only": True,
        "relative_volume": {"volume_ratio_20d": 1.25},
        "directional_volume": {"signed_volume_balance": 0.3 if supply_state == "DEMAND_PRESSURE" else -0.3},
        "close_location_flow": {"cmf_20": 0.2 if supply_state == "DEMAND_PRESSURE" else -0.2},
        "data_quality": {"source_alignment": "SINGLE_SOURCE"},
        "institutional_intent_claim": "NOT_INFERRED",
    }
    structure = {
        "schema_version": "price-structure-v1", "status": "READY",
        "reason": "CONFIRMED_PIVOTS_READY", "stock_code": "600519",
        "target_date": "2026-10-30", "timeframe": "daily",
        "completed_bar_only": True, "range_state": range_state,
        "structure_event": {"state": event_state, "provisional": False},
        "nearest_support": {"status": "READY", "price": 100.0},
        "nearest_resistance": {"status": "READY", "price": 110.0},
    }
    return supply, structure


@pytest.mark.parametrize(
    "supply_state,event_state,range_state,expected_phase,hard_veto",
    [
        ("DEMAND_PRESSURE", "NONE", "BETWEEN_CONFIRMED_LEVELS", "ACCUMULATION_CANDIDATE", False),
        ("DEMAND_PRESSURE", "FAILED_DOWN_BREAKDOWN", "FAILED_BREAKDOWN_RETURNED_ABOVE_LEVEL", "WASHOUT_CANDIDATE", False),
        ("SUPPLY_PRESSURE", "FAILED_UP_BREAKOUT", "FAILED_BREAKOUT_RETURNED_BELOW_LEVEL", "DISTRIBUTION_CANDIDATE", True),
        ("DEMAND_PRESSURE", "UP_BREAKOUT_RETEST_HOLD", "ABOVE_OR_TESTING_CONFIRMED_RESISTANCE", "MARKUP_CANDIDATE", False),
        ("SUPPLY_PRESSURE", "DOWN_BREAKDOWN_RETEST_REJECT", "BELOW_OR_TESTING_CONFIRMED_SUPPORT", "MARKDOWN_CANDIDATE", True),
    ],
)
def test_volume_price_phase_is_deterministic_candidate_not_institutional_intent(
    supply_state, event_state, range_state, expected_phase, hard_veto,
):
    supply, structure = _phase_parent(
        supply_state=supply_state, event_state=event_state, range_state=range_state,
    )
    result = compile_volume_price_phase_context(
        supply_demand_context=supply,
        price_structure_context=structure,
        timeframe="daily",
    )
    assert result["status"] == "READY"
    assert result["phase"] == expected_phase
    assert result["hard_veto"] is hard_veto
    assert result["institutional_intent_claim"] == "NOT_INFERRED"
    assert result["independent_action_authority"] is False
    assert result["strategy_upgrade_authority"] is False
    assert len(result["receipt_hash"]) == 64
    assert "主力" not in str(result)
    assert result["veto_codes"] == (
        ("DISTRIBUTION_OR_MARKDOWN_CANDIDATE",) if hard_veto else ()
    )


def test_volume_price_phase_rejects_mismatched_identity_and_mixed_evidence():
    supply, structure = _phase_parent(
        supply_state="DEMAND_PRESSURE", event_state="FAILED_DOWN_BREAKDOWN",
        range_state="FAILED_BREAKDOWN_RETURNED_ABOVE_LEVEL",
    )
    structure["stock_code"] = "000001"
    mismatch = compile_volume_price_phase_context(
        supply_demand_context=supply, price_structure_context=structure,
        timeframe="daily",
    )
    assert mismatch["status"] == "UNKNOWN"
    assert mismatch["reason_codes"] == ("VOLUME_PRICE_PHASE_ASSET_OR_TIME_MISMATCH",)
    assert mismatch["phase"] == "INDETERMINATE"

    structure["stock_code"] = "600519"
    supply["state"] = "CONFLICT"
    mixed = compile_volume_price_phase_context(
        supply_demand_context=supply, price_structure_context=structure,
        timeframe="daily",
    )
    assert mixed["status"] == "PARTIAL"
    assert mixed["phase"] == "INDETERMINATE"
    assert mixed["hard_veto"] is False
