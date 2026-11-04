"""Independent guards against using a short history as a 20-bar denominator."""
from __future__ import annotations

import pandas as pd
import pytest

from src.services.screening.daily import _volume_ratio_20d, compute_daily_features


@pytest.mark.parametrize("prior", (
    [100.0],
    [100.0] * 19,
    [100.0] * 19 + [float("nan")],
    [100.0] * 19 + [-3.0],
    [100.0] * 19 + [float("inf")],
))
def test_20d_volume_ratio_fails_closed_without_exact_20_valid_previous_bars(prior):
    values = pd.DataFrame({"volume": prior + [200.0]})
    assert _volume_ratio_20d(values) is None


def test_20d_volume_ratio_rejects_missing_invalid_and_zero_current_volume():
    for current in [float("nan"), float("inf"), -1.0]:
        assert _volume_ratio_20d(
            pd.DataFrame({"volume": [100.0] * 20 + [current]})
        ) is None
    assert _volume_ratio_20d(
        pd.DataFrame({"volume": [0.0] * 20 + [200.0]})
    ) is None


def test_20d_volume_ratio_has_an_independent_full_window_positive():
    assert _volume_ratio_20d(
        pd.DataFrame({"volume": [100.0] * 20 + [200.0]})
    ) == pytest.approx(2.0)


def test_public_screening_feature_cannot_smuggle_short_history_ratio():
    for volumes in ([100.0, 200.0], [100.0] * 19 + [None, 200.0]):
        n = len(volumes)
        frame = pd.DataFrame({
            "date": pd.date_range("2026-09-01", periods=n, freq="D"),
            "open": [10.0] * n,
            "high": [11.0] * n,
            "low": [9.0] * n,
            "close": [10.0] * n,
            "volume": volumes,
        })
        output = compute_daily_features(frame)
        assert output["volume_ratio_20d"] is None
