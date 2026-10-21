from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import exchange_calendars as xcals
import pandas as pd
import pytest

from data_provider.daily_data_identity import (
    attach_daily_data_identity,
    build_daily_data_identity,
    canonical_identity_json,
    sha256_payload,
)
from src.core.pipeline import StockAnalysisPipeline
from src.services.evidence_traceability_registry import (
    compile_mtf_method_window_readiness_trace,
)
from src.core import pipeline as pipeline_owner


RECEIPT_ATTR = "dsa_daily_acquisition_receipt"


def _sessions(target: date, count: int = 4) -> list[date]:
    calendar = xcals.get_calendar("XSHG")
    end = calendar.date_to_session(target)
    return [value.date() for value in calendar.sessions_window(end, -count)]


def _plan(target: date, sessions: list[date]) -> dict:
    identity = {
        "version": "test-daily-plan-v1",
        "market": "cn",
        "target_date": target.isoformat(),
        "timeframe_required_bars": {"daily": len(sessions), "weekly": 1, "monthly": 1},
        "source_only_timeframe_required_bars": {"daily": len(sessions), "weekly": 1, "monthly": 1},
        "method_required_bars": {
            "daily": {"MACD": len(sessions), "RSI": len(sessions)},
            "weekly": {"MTF_MOMENTUM_CONTEXT": 1},
            "monthly": {"MTF_MOMENTUM_CONTEXT": 1},
        },
        "method_window_classes": {"daily": {}, "weekly": {}, "monthly": {}},
        "mtf_recursive_comparison_required_bars": {
            "MACD_STARTUP_COMPARISON": len(sessions),
            "RSI_STARTUP_COMPARISON": len(sessions),
        },
        "mtf_recursive_calendar_status": {
            "weekly": {
                "MACD_STARTUP_COMPARISON": "ACQUISITION_PLANNED",
                "RSI_STARTUP_COMPARISON": "ACQUISITION_PLANNED",
            },
            "monthly": {
                "MACD_STARTUP_COMPARISON": "ACQUISITION_PLANNED",
                "RSI_STARTUP_COMPARISON": "ACQUISITION_PLANNED",
            },
        },
    }
    return {
        "start_date": sessions[0],
        "start_by_timeframe": {
            "daily": sessions[0], "weekly": sessions[0], "monthly": sessions[0],
        },
        "limiting_timeframes": ("daily",),
        **identity,
        "plan_hash": sha256_payload(identity),
    }


def _typed_frame(
    sessions: list[date],
    *,
    instrument: str = "600519",
    provider: str = "BaostockFetcher",
    requested_start: str | None = None,
    requested_end: str | None = None,
    requested_basis: str = "qfq",
    observed_basis: str = "qfq",
    observed_at: datetime | None = None,
) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
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
            for index, value in enumerate(sessions)
        ]
    )
    identity = build_daily_data_identity(
        frame,
        provider_identity=provider,
        provider_route="unit.daily.provider",
        actual_response_branch=f"fixture:{observed_basis}",
        requested_adjustment_basis=requested_basis,
        observed_adjustment_basis=observed_basis,
        basis_evidence="unit-test",
        requested_start=requested_start or sessions[0].isoformat(),
        requested_end=requested_end or sessions[-1].isoformat(),
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        identity_state="OBSERVED",
        observed_at=observed_at or datetime.now(timezone.utc),
    )
    # The pre-fix identity schema did not bind a security. Add the independently
    # expected field and recompute the existing semantic hash so this RED reaches
    # the original Pipeline consumer rather than failing fixture construction.
    identity["instrument"] = instrument
    identity["identity_hash"] = sha256_payload(
        {
            key: value
            for key, value in identity.items()
            if key not in {"identity_hash", "observed_at"}
        }
    )
    attach_daily_data_identity(frame, identity)
    return frame


class _DailyRow:
    def __init__(self, code: str, record: dict, identity_json: str, identity_hash: str) -> None:
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


def _persisted_rows(frame: pd.DataFrame, *, row_code: str = "600519") -> list[_DailyRow]:
    identity = frame.attrs["daily_data_identity"]
    identity_json = canonical_identity_json(identity)
    return [
        _DailyRow(row_code, record, identity_json, identity["identity_hash"])
        for record in frame.to_dict("records")
    ]


def _pipeline(target: date) -> StockAnalysisPipeline:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "SYNTHETIC"
    pipeline.db = MagicMock()
    pipeline._resolve_resume_target_date = MagicMock(return_value=target)
    return pipeline


def _context(target: date, observations: int) -> dict:
    def receipt() -> dict:
        return {
            "status": "READY",
            "reason": "UNIT_READY",
            "observations": observations,
            "required_observations": observations,
            "strategy_admitted": False,
            "learning_admitted": False,
        }

    return {
        "stock_code": "600519",
        "target_date": target.isoformat(),
        "timeframes": {
            timeframe: {
                "observations": observations,
                "macd_startup_stability": receipt(),
                "rsi_startup_stability": receipt(),
            }
            for timeframe in ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m")
        },
    }


def test_provider_wrong_instrument_is_rejected_before_storage() -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target)
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = False
    pipeline.fetcher_manager.get_daily_data.return_value = (
        _typed_frame(
            sessions,
            instrument="510300",
            requested_start=sessions[0].isoformat(),
            requested_end=target.isoformat(),
        ),
        "BaostockFetcher",
    )

    with patch.object(pipeline_owner, "_current_daily_window_plan", return_value=_plan(target, sessions)):
        success, error = pipeline.fetch_and_save_stock_data("600519")

    assert success is False
    assert "DAILY_ACQUISITION_BINDING_INVALID" in (error or "")
    pipeline.db.save_daily_data.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        "provider",
        "basis",
        "request_range",
        "future_observation",
        "future_bar",
    ],
)
def test_provider_response_identity_time_basis_and_range_fail_closed(mutation: str) -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target)
    request_start = sessions[0].isoformat()
    frame = _typed_frame(
        sessions,
        provider="OtherFetcher" if mutation == "provider" else "BaostockFetcher",
        requested_start="2026-10-01" if mutation == "request_range" else request_start,
        requested_end=target.isoformat(),
        observed_basis="hfq" if mutation == "basis" else "qfq",
        observed_at=(
            datetime.now(timezone.utc) + timedelta(days=1)
            if mutation == "future_observation"
            else datetime.now(timezone.utc)
        ),
    )
    if mutation == "future_bar":
        frame = _typed_frame(
            sessions + [target + timedelta(days=1)],
            requested_start=request_start,
            requested_end=target.isoformat(),
        )
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = False
    pipeline.fetcher_manager.get_daily_data.return_value = (frame, "BaostockFetcher")

    with patch.object(pipeline_owner, "_current_daily_window_plan", return_value=_plan(target, sessions)):
        success, error = pipeline.fetch_and_save_stock_data("600519")

    assert success is False
    assert "DAILY_ACQUISITION_BINDING_INVALID" in (error or "")
    pipeline.db.save_daily_data.assert_not_called()


def test_verified_provider_call_remains_run_local_when_vendor_storage_is_not_admitted() -> None:
    target = date(2026, 10, 12)
    requested_sessions = _sessions(target, count=6)
    returned_sessions = requested_sessions[-3:]
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = False
    frame = _typed_frame(
        returned_sessions,
        requested_start=requested_sessions[0].isoformat(),
        requested_end=target.isoformat(),
    )
    pipeline.fetcher_manager.get_daily_data.return_value = (frame, "BaostockFetcher")
    pipeline.db.save_daily_data.side_effect = PermissionError(
        "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED"
    )

    with patch.object(
        pipeline_owner,
        "_current_daily_window_plan",
        return_value=_plan(target, requested_sessions),
    ):
        success, error = pipeline.fetch_and_save_stock_data("600519")

    assert success is True
    assert error is None
    admitted = pipeline._get_run_local_daily_history(
        code="600519", target_date=target,
    )
    assert admitted is not None
    receipt = admitted.attrs[RECEIPT_ATTR]
    assert receipt["acquisition_mode"] == "VERIFIED_PROVIDER_CALL"
    assert receipt["request_evidence_status"] == "VERIFIED_PROVIDER_CALL"
    assert receipt["requested_session_count"] == 6
    assert receipt["returned_base_bars"] == 3
    assert receipt["admitted_base_bars"] == 3
    assert receipt["persistence_status"] == "RIGHTS_NOT_ADMITTED_RUN_LOCAL_ONLY"
    assert receipt["provider_method_call_count"] == 1
    assert receipt["provider_http_request_count"] is None


def test_untyped_complete_date_cache_cannot_suppress_provider_dispatch() -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target)
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = True
    pipeline.db.get_data_range.return_value = [
        SimpleNamespace(
            code="600519", date=value, open=10.0, high=11.0, low=9.0,
            close=10.5, volume=1000.0, amount=10500.0, pct_chg=0.1,
            data_source="legacy", data_identity_json=None, data_identity_hash=None,
        )
        for value in sessions
    ]
    frame = _typed_frame(
        sessions,
        requested_start=sessions[0].isoformat(),
        requested_end=target.isoformat(),
    )
    pipeline.fetcher_manager.get_daily_data.return_value = (frame, "BaostockFetcher")
    pipeline.db.save_daily_data.side_effect = PermissionError(
        "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED"
    )

    with patch.object(pipeline_owner, "_current_daily_window_plan", return_value=_plan(target, sessions)):
        success, error = pipeline.fetch_and_save_stock_data("600519")

    assert success is True and error is None
    pipeline.fetcher_manager.get_daily_data.assert_called_once()
    admitted = pipeline._get_run_local_daily_history(code="600519", target_date=target)
    assert admitted.attrs[RECEIPT_ATTR]["acquisition_mode"] == "VERIFIED_PROVIDER_CALL"


def test_lawful_content_bound_cache_hit_is_a_no_dispatch_nontrigger() -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target)
    frame = _typed_frame(
        sessions,
        requested_start=sessions[0].isoformat(),
        requested_end=target.isoformat(),
    )
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = True
    pipeline.db.get_data_range.return_value = _persisted_rows(frame)

    with patch.object(pipeline_owner, "_current_daily_window_plan", return_value=_plan(target, sessions)):
        success, error = pipeline.fetch_and_save_stock_data("600519")

    assert success is True and error is None
    pipeline.fetcher_manager.get_daily_data.assert_not_called()
    pipeline.db.save_daily_data.assert_not_called()
    admitted = pipeline._get_run_local_daily_history(code="600519", target_date=target)
    receipt = admitted.attrs[RECEIPT_ATTR]
    assert receipt["acquisition_mode"] == "VERIFIED_CACHE_HIT"
    assert receipt["request_evidence_status"] == "VERIFIED_LAWFUL_CACHE_HIT"
    assert receipt["provider_method_call_count"] == 0
    assert receipt["requested_session_count"] is None
    assert receipt["cache_lookup_start"] == sessions[0].isoformat()
    assert receipt["cache_lookup_end"] == target.isoformat()


@pytest.mark.parametrize("defect", ["wrong_code", "mixed_identity", "future_observation"])
def test_cache_identity_defects_fall_through_to_provider_without_false_hit(defect: str) -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target)
    frame = _typed_frame(
        sessions,
        observed_at=(
            datetime.now(timezone.utc) + timedelta(days=1)
            if defect == "future_observation"
            else datetime.now(timezone.utc)
        ),
        requested_start=sessions[0].isoformat(),
        requested_end=target.isoformat(),
    )
    rows = _persisted_rows(frame, row_code="510300" if defect == "wrong_code" else "600519")
    if defect == "mixed_identity":
        rows[-1].data_identity_hash = "0" * 64
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = True
    pipeline.db.get_data_range.return_value = rows
    provider_frame = _typed_frame(
        sessions,
        requested_start=sessions[0].isoformat(),
        requested_end=target.isoformat(),
    )
    pipeline.fetcher_manager.get_daily_data.return_value = (
        provider_frame, "BaostockFetcher",
    )
    pipeline.db.save_daily_data.side_effect = PermissionError(
        "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED"
    )

    with patch.object(pipeline_owner, "_current_daily_window_plan", return_value=_plan(target, sessions)):
        success, error = pipeline.fetch_and_save_stock_data("600519")

    assert success is True and error is None
    pipeline.fetcher_manager.get_daily_data.assert_called_once()
    admitted = pipeline._get_run_local_daily_history(code="600519", target_date=target)
    assert admitted.attrs[RECEIPT_ATTR]["acquisition_mode"] == "VERIFIED_PROVIDER_CALL"


def test_failed_dispatch_never_creates_run_local_acquisition_evidence() -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target)
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = False
    pipeline.fetcher_manager.get_daily_data.side_effect = RuntimeError("provider down")

    with patch.object(pipeline_owner, "_current_daily_window_plan", return_value=_plan(target, sessions)):
        success, error = pipeline.fetch_and_save_stock_data("600519")

    assert success is False
    assert "provider down" in (error or "")
    assert pipeline._get_run_local_daily_history(code="600519", target_date=target) is None


def test_daily_acquisition_receipt_reaches_method_window_consumer_without_inventing_cache_request() -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target)
    frame = _typed_frame(
        sessions,
        requested_start=sessions[0].isoformat(),
        requested_end=target.isoformat(),
    )
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = True
    pipeline.db.get_data_range.return_value = _persisted_rows(frame)
    plan = _plan(target, sessions)

    with patch.object(pipeline_owner, "_current_daily_window_plan", return_value=plan):
        assert pipeline.fetch_and_save_stock_data("600519") == (True, None)
    admitted = pipeline._get_run_local_daily_history(code="600519", target_date=target)
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="STOCK",
        instrument="600519",
        target_date=target,
        daily_window_plan=plan,
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        multi_timeframe_structure_context=_context(target, len(sessions)),
        daily_source_frame=admitted,
    )
    rows = [row for row in trace["rows"] if row["timeframe"] in {"daily", "weekly", "monthly"}]
    assert rows
    assert all(row["request_evidence_status"] == "VERIFIED_LAWFUL_CACHE_HIT" for row in rows)
    assert all(row["requested_bars"] is None for row in rows)
    assert all(row["source_returned_bars"] == len(sessions) for row in rows)
    assert trace["daily_acquisition_receipt"]["acquisition_mode"] == "VERIFIED_CACHE_HIT"


def test_provider_daily_acquisition_receipt_reaches_all_higher_timeframe_rows() -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target, count=6)
    plan = _plan(target, sessions)
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = False
    provider_frame = _typed_frame(
        sessions,
        requested_start=sessions[0].isoformat(),
        requested_end=target.isoformat(),
    )
    pipeline.fetcher_manager.get_daily_data.return_value = (
        provider_frame, "BaostockFetcher",
    )
    pipeline.db.save_daily_data.side_effect = PermissionError(
        "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED"
    )

    with patch.object(
        pipeline_owner, "_current_daily_window_plan", return_value=plan,
    ):
        assert pipeline.fetch_and_save_stock_data("600519") == (True, None)
    admitted = pipeline._get_run_local_daily_history(
        code="600519", target_date=target,
    )
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="STOCK",
        instrument="600519",
        target_date=target,
        daily_window_plan=plan,
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        multi_timeframe_structure_context=_context(target, len(sessions)),
        daily_source_frame=admitted,
    )

    rows = [
        row for row in trace["rows"]
        if row["timeframe"] in {"daily", "weekly", "monthly"}
    ]
    assert len(rows) == 6
    assert all(row["request_evidence_status"] == "VERIFIED_PROVIDER_CALL" for row in rows)
    assert all(row["requested_bars"] == len(sessions) for row in rows)
    assert all(row["actual_request_start"] == sessions[0].isoformat() for row in rows)
    assert all(row["actual_request_end"] == target.isoformat() for row in rows)
    assert all(row["source_returned_bars"] == len(sessions) for row in rows)
    assert all(
        row["persistence_status"] == "RIGHTS_NOT_ADMITTED_RUN_LOCAL_ONLY"
        for row in rows
    )
    assert trace["daily_acquisition_receipt"]["acquisition_mode"] == (
        "VERIFIED_PROVIDER_CALL"
    )
    assert trace["daily_acquisition_receipt"]["provider_http_request_count"] is None


@pytest.mark.parametrize("tamper", ["receipt", "content"])
def test_daily_acquisition_tamper_fails_entire_trace_closed(tamper: str) -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target, count=6)
    plan = _plan(target, sessions)
    pipeline = _pipeline(target)
    pipeline.db.has_today_data.return_value = False
    provider_frame = _typed_frame(
        sessions,
        requested_start=sessions[0].isoformat(),
        requested_end=target.isoformat(),
    )
    pipeline.fetcher_manager.get_daily_data.return_value = (
        provider_frame, "BaostockFetcher",
    )
    pipeline.db.save_daily_data.side_effect = PermissionError(
        "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED"
    )

    with patch.object(
        pipeline_owner, "_current_daily_window_plan", return_value=plan,
    ):
        assert pipeline.fetch_and_save_stock_data("600519") == (True, None)
    admitted = pipeline._get_run_local_daily_history(
        code="600519", target_date=target,
    )
    assert admitted is not None
    if tamper == "receipt":
        claim = deepcopy(admitted.attrs[RECEIPT_ATTR])
        claim["actual_request_start"] = sessions[1].isoformat()
        admitted.attrs[RECEIPT_ATTR] = claim
    else:
        admitted.loc[admitted.index[-1], "close"] += 1.0

    trace = compile_mtf_method_window_readiness_trace(
        asset_route="STOCK",
        instrument="600519",
        target_date=target,
        daily_window_plan=plan,
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        multi_timeframe_structure_context=_context(target, len(sessions)),
        daily_source_frame=admitted,
    )
    assert trace["status"] == "UNKNOWN"
    assert trace["reason"] == "DAILY_ACQUISITION_BINDING_INVALID"
    assert trace["rows"] == []
    assert trace["strategy_admitted"] is False
    assert trace["learning_admitted"] is False
