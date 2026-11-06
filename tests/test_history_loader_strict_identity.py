from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from data_provider.daily_data_identity import (
    attach_daily_data_identity,
    build_daily_data_identity,
    canonical_identity_json,
)


class _DailyRow:
    def __init__(self, code: str, record: dict, identity_json: str | None, identity_hash: str | None) -> None:
        self.code = code
        for key, value in record.items():
            setattr(self, key, value)
        self.data_identity_json = identity_json
        self.data_identity_hash = identity_hash

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
            "pct_chg": self.pct_chg,
            "data_source": self.data_source,
            "data_identity_json": self.data_identity_json,
            "data_identity_hash": self.data_identity_hash,
        }


class _FakeDb:
    def __init__(self, rows_by_code: dict[str, list[_DailyRow]]) -> None:
        self.rows_by_code = rows_by_code

    def get_data_range(self, code: str, start_date: date, end_date: date):
        return [
            row for row in self.rows_by_code.get(code, [])
            if start_date <= row.date <= end_date
        ]


def _typed_frame(
    *,
    code: str,
    end: date,
    count: int,
    requested_start: date,
    provider: str = "Fetcher",
) -> pd.DataFrame:
    dates = [end - timedelta(days=offset) for offset in reversed(range(count))]
    frame = pd.DataFrame([
        {
            "date": value,
            "open": 10.0 + index,
            "high": 11.0 + index,
            "low": 9.0 + index,
            "close": 10.5 + index,
            "volume": 1000.0 + index,
            "amount": 10500.0 + index,
            "pct_chg": 0.1 + index,
            "data_source": provider,
        }
        for index, value in enumerate(dates)
    ])
    identity = build_daily_data_identity(
        frame,
        provider_identity=provider,
        provider_route="unit.strict-history",
        actual_response_branch="fixture:qfq",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="unit-test",
        requested_start=requested_start.isoformat(),
        requested_end=end.isoformat(),
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        identity_state="OBSERVED",
        observed_at=datetime.now(timezone.utc),
        instrument=code,
    )
    attach_daily_data_identity(frame, identity)
    return frame


def _rows(frame: pd.DataFrame, *, row_code: str, typed: bool = True) -> list[_DailyRow]:
    identity = frame.attrs["daily_data_identity"]
    identity_json = canonical_identity_json(identity) if typed else None
    identity_hash = identity["identity_hash"] if typed else None
    return [
        _DailyRow(row_code, record, identity_json, identity_hash)
        for record in frame.to_dict("records")
    ]


def _loader_start(end: date, days: int) -> date:
    return end - timedelta(days=int(days * 1.8) + 10)


def test_strict_history_accepts_only_content_bound_typed_cache() -> None:
    from src.services.history_loader import load_history_df

    end = date(2026, 4, 24)
    start = _loader_start(end, 60)
    frame = _typed_frame(code="600519", end=end, count=40, requested_start=start)
    db = _FakeDb({"600519": _rows(frame, row_code="600519")})
    manager = SimpleNamespace(get_daily_data=MagicMock())

    with patch("src.storage.get_db", return_value=db), patch(
        "src.services.history_loader._get_fetcher_manager", return_value=manager,
    ):
        result, source = load_history_df(
            "600519", days=60, target_date=end, require_verified_identity=True,
        )

    assert source == "db_cache"
    assert result is not None and len(result) == 40
    assert result.attrs["daily_data_identity"]["instrument"] == "600519"
    manager.get_daily_data.assert_not_called()


def test_strict_history_rejects_untyped_cache_and_uses_bound_provider() -> None:
    from src.services.history_loader import load_history_df

    end = date(2026, 4, 24)
    start = _loader_start(end, 60)
    legacy = _typed_frame(code="600519", end=end, count=40, requested_start=start)
    db = _FakeDb({"600519": _rows(legacy, row_code="600519", typed=False)})
    provider = _typed_frame(code="600519", end=end, count=5, requested_start=start)
    manager = SimpleNamespace(
        get_daily_data=MagicMock(return_value=(provider, "Fetcher")),
    )

    with patch("src.storage.get_db", return_value=db), patch(
        "src.services.history_loader._get_fetcher_manager", return_value=manager,
    ):
        result, source = load_history_df(
            "600519", days=60, target_date=end, require_verified_identity=True,
        )

    assert source == "Fetcher"
    assert result is not None and len(result) == 5
    manager.get_daily_data.assert_called_once()


def test_strict_history_wrong_instrument_cache_cannot_feed_calculation() -> None:
    from src.services.history_loader import load_history_df

    end = date(2026, 4, 24)
    start = _loader_start(end, 60)
    wrong = _typed_frame(code="510300", end=end, count=40, requested_start=start)
    db = _FakeDb({"600519": _rows(wrong, row_code="600519")})
    manager = SimpleNamespace(get_daily_data=MagicMock(return_value=(None, "none")))

    with patch("src.storage.get_db", return_value=db), patch(
        "src.services.history_loader._get_fetcher_manager", return_value=manager,
    ):
        result, source = load_history_df(
            "600519", days=60, target_date=end, require_verified_identity=True,
        )

    assert result is None and source == "none"
    manager.get_daily_data.assert_called_once()


def test_strict_history_rejects_untyped_provider_frame() -> None:
    from src.services.history_loader import load_history_df

    end = date(2026, 4, 24)
    db = _FakeDb({})
    untyped = pd.DataFrame([
        {
            "date": end,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10,
        }
    ])
    manager = SimpleNamespace(
        get_daily_data=MagicMock(return_value=(untyped, "Fetcher")),
    )

    with patch("src.storage.get_db", return_value=db), patch(
        "src.services.history_loader._get_fetcher_manager", return_value=manager,
    ):
        result, source = load_history_df(
            "600519", days=60, target_date=end, require_verified_identity=True,
        )

    assert result is None and source == "none"


def test_screening_and_technical_analysis_require_verified_history() -> None:
    with patch(
        "src.services.history_loader.load_history_df",
        return_value=(None, "none"),
    ) as loader:
        from src.services.screening_service import get_dsa_daily_history
        get_dsa_daily_history("600519", lookback_days=120)
        loader.assert_called_once_with(
            "600519", days=120, require_verified_identity=True,
        )

    with patch(
        "src.services.history_loader.load_history_df",
        return_value=(None, "none"),
    ) as loader:
        from src.agent.tools.analysis_tools import _fetch_trend_data
        _fetch_trend_data("600519")
        loader.assert_called_once_with(
            "600519", days=60, require_verified_identity=True,
        )
