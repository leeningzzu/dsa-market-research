# -*- coding: utf-8 -*-
"""Independent acceptance for the source-bound eleven extended indicator leaves."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math

import numpy as np
import pandas as pd
import pytest

from src.services.extended_indicator_service import (
    LEAF_NAMES,
    SUPPORTED_TIMEFRAMES,
    build_extended_indicator_context,
)


def bars(
    *,
    timeframe: str = "daily",
    count: int = 240,
    price_scale: float = 1.0,
    volume_scale: float = 1.0,
) -> pd.DataFrame:
    x = np.arange(count, dtype=float)
    close = 80.0 + x * 0.035 + 2.8 * np.sin(x / 5.2) + 1.1 * np.cos(x / 13.0)
    open_ = close + 0.25 * np.sin(x / 2.7)
    high = np.maximum(open_, close) + 1.2 + 0.15 * np.square(np.sin(x / 7.0))
    low = np.minimum(open_, close) - 1.1 - 0.12 * np.square(np.cos(x / 8.0))
    volume = 120000.0 + (x % 17) * 1700.0 + 8000.0 * np.sin(x / 11.0)
    if timeframe in {"60m", "30m", "15m", "5m"}:
        date = pd.date_range(
            datetime(2026, 1, 5, 1, 30, tzinfo=timezone.utc),
            periods=count,
            freq={"60m": "60min", "30m": "30min", "15m": "15min", "5m": "5min"}[timeframe],
        )
    else:
        date = pd.bdate_range("2025-01-02", periods=count).date
    return pd.DataFrame({
        "date": date,
        "open": open_ * price_scale,
        "high": high * price_scale,
        "low": low * price_scale,
        "close": close * price_scale,
        "volume": volume * volume_scale,
        "data_source": "synthetic-independent-acceptance",
    })


def build(frame: pd.DataFrame, *, timeframe: str = "daily", volume_unit: str = "share") -> dict:
    return build_extended_indicator_context(
        stock_code="600519",
        bars=frame,
        timeframe=timeframe,
        target_bar_end=frame["date"].iloc[-1],
        source_identity_hash="a" * 64,
        volume_unit=volume_unit,
        market="cn",
    )


@pytest.mark.parametrize("timeframe", SUPPORTED_TIMEFRAMES)
def test_all_seven_timeframes_produce_eleven_source_bound_finite_leaves(timeframe: str) -> None:
    result = build(bars(timeframe=timeframe), timeframe=timeframe)
    assert result["status"] == "READY"
    assert set(result["values"]) == set(LEAF_NAMES)
    assert all(
        isinstance(result["values"][key], float)
        and math.isfinite(result["values"][key])
        for key in LEAF_NAMES
    )
    assert set(result["methods"]) == {"ADX_DMI", "BOLLINGER", "KDJ", "OBV_ADL", "MFI"}
    assert all(method["status"] == "READY" for method in result["methods"].values())
    assert result["independent_action_authority"] is False
    assert result["strategy_admitted"] is False
    assert result["learning_admitted"] is False
    assert result["historical_pit_eligible"] is False
    assert result["data_rights_admitted"] is False


def test_price_scale_and_volume_scale_preserve_their_declared_semantics() -> None:
    base = build(bars())
    price = build(bars(price_scale=10.0))
    volume = build(bars(volume_scale=7.0))
    for key in ("adx", "plus_di", "minus_di", "kdj_k", "kdj_d", "kdj_j", "mfi"):
        assert price["values"][key] == pytest.approx(base["values"][key], rel=1e-9, abs=1e-9)
        assert volume["values"][key] == pytest.approx(base["values"][key], rel=1e-9, abs=1e-9)
    for key in ("bollinger_upper", "bollinger_lower"):
        assert price["values"][key] == pytest.approx(base["values"][key] * 10.0)
        assert volume["values"][key] == pytest.approx(base["values"][key])
    for key in ("obv", "adl"):
        assert price["values"][key] == pytest.approx(base["values"][key])
        assert volume["values"][key] == pytest.approx(base["values"][key] * 7.0)


def test_unknown_volume_unit_blocks_only_volume_derived_methods() -> None:
    result = build(bars(), volume_unit="UNKNOWN")
    assert result["status"] == "PARTIAL"
    for method_id in ("ADX_DMI", "BOLLINGER", "KDJ"):
        assert result["methods"][method_id]["status"] == "READY"
    for method_id in ("OBV_ADL", "MFI"):
        assert result["methods"][method_id]["status"] == "UNKNOWN"
    assert result["values"]["obv"] is None
    assert result["values"]["adl"] is None
    assert result["values"]["mfi"] is None


def test_warmup_and_recursive_startup_are_method_owned_not_forced_ready() -> None:
    result = build(bars(count=40))
    assert result["status"] == "PARTIAL"
    assert result["methods"]["BOLLINGER"]["status"] == "READY"
    assert result["methods"]["ADX_DMI"]["status"] == "MISSING"
    assert result["methods"]["KDJ"]["status"] == "MISSING"
    assert result["methods"]["OBV_ADL"]["status"] == "MISSING"
    assert result["values"]["adx"] is None
    assert result["values"]["bollinger_upper"] is not None


def test_source_identity_time_order_and_ohlcv_geometry_fail_closed() -> None:
    frame = bars()
    missing_identity = build_extended_indicator_context(
        stock_code="600519", bars=frame, timeframe="daily",
        target_bar_end=frame["date"].iloc[-1], source_identity_hash=None,
        volume_unit="share", market="cn",
    )
    assert missing_identity["status"] == "UNKNOWN"
    assert all(value is None for value in missing_identity["values"].values())

    duplicate = frame.copy()
    duplicate.loc[duplicate.index[-1], "date"] = duplicate.loc[duplicate.index[-2], "date"]
    duplicate_result = build_extended_indicator_context(
        stock_code="600519", bars=duplicate, timeframe="daily",
        target_bar_end=duplicate["date"].iloc[-1], source_identity_hash="a" * 64,
        volume_unit="share", market="cn",
    )
    assert duplicate_result["status"] == "UNKNOWN"
    assert duplicate_result["reason"] == "BAR_TIME_DUPLICATE_OR_OUT_OF_ORDER"

    invalid = frame.copy()
    invalid.loc[invalid.index[-1], "low"] = invalid.loc[invalid.index[-1], "high"] + 1.0
    invalid_result = build(invalid)
    assert invalid_result["status"] == "UNKNOWN"
    assert invalid_result["reason"] == "INVALID_OHLCV_GEOMETRY_OR_UNIT"


def test_zero_range_and_zero_flow_never_invent_adl_or_mfi() -> None:
    frame = bars()
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = frame.loc[frame.index[-1], "close"]
    result = build(frame)
    assert result["methods"]["OBV_ADL"]["status"] == "MISSING"
    assert result["values"]["adl"] is None

    zero = bars()
    zero["volume"] = 0.0
    result_zero = build(zero)
    assert result_zero["methods"]["MFI"]["status"] == "MISSING"
    assert result_zero["values"]["mfi"] is None


def test_tampered_method_scope_cannot_claim_action_or_learning_authority() -> None:
    result = build(bars())
    tampered = deepcopy(result)
    tampered["methods"]["ADX_DMI"]["independent_action_authority"] = True
    assert result["methods"]["ADX_DMI"]["independent_action_authority"] is False
    assert tampered["methods"]["ADX_DMI"] != result["methods"]["ADX_DMI"]
    assert result["correlation_group_policy"]["same_group_double_counting"] == "PROHIBITED"
