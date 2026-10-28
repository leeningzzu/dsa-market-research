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
    extract_daily_acquisition_receipt,
    sha256_payload,
    validate_daily_acquisition_receipt,
)
from data_provider.intraday_data_identity import (
    attach_intraday_data_identity,
    build_intraday_data_identity,
)
from src.services.intraday_bar_service import normalize_cn_completed_5m_bars
from src.core.pipeline import StockAnalysisPipeline
from src.services.data_object_admission import seal_source_rights_evidence
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
    # Independently scoped *synthetic* grant to reach the original daily
    # identity/cache/window negatives. It is never an upstream vendor license.
    rights = {
        "product_use": "ALLOWED", "storage": "ALLOWED",
        "training": "FORBIDDEN", "redistribution": "FORBIDDEN",
    }
    pipeline.b1_rights_context = {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": "test-daily-acquisition-current",
        "intended_use": "NO_SEND_RESEARCH",
        "research_as_of": "2026-10-23T10:45:00+00:00",
        "allowed_instruments": ("600519", "510300"),
        "rights_by_dataset": {
            "P_DAILY": seal_source_rights_evidence({
                "schema_version": "source-rights-evidence-v2",
                "operation_id": "test-daily-acquisition-current",
                "intended_use": "NO_SEND_RESEARCH",
                "provider_identity": "BaostockFetcher",
                "upstream_source_owner": "TEST_FIXTURE_ONLY_NOT_REAL_LICENSE",
                "upstream_rights_state": "VERIFIED",
                "dataset_id": "P_DAILY",
                "consumer_scope": "ASSET_RESEARCH_BRIEF",
                "terms_uri": "https://example.org/unit-fixture-only",
                "terms_sha256": "a" * 64,
                "terms_observed_at": "2026-10-01T00:00:00+00:00",
                "terms_scope": "SYNTHETIC_TEST_CONTRACT",
                "product_use_clause": "Synthetic fixture explicitly allows internal no-send research.",
                "product_use_clause_sha256": "c2a9b98537611cb0c9e9cb165ab0a4a699d84f5a2644a5e1e23740d0af55c6e1",
                "rights": rights,
            }),
        },
    }
    stock_grant = pipeline.b1_rights_context["rights_by_dataset"]["P_DAILY"]
    etf_payload = {key: value for key, value in stock_grant.items() if key != "evidence_hash"}
    etf_payload["dataset_id"] = "E_PRICE_1D"
    etf_payload["consumer_scope"] = "ETF_ASSET_BRIEF"
    pipeline.b1_rights_context["rights_by_dataset"]["E_PRICE_1D"] = seal_source_rights_evidence(etf_payload)
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


def _completed_target_5m(
    target: date,
    *,
    code: str = "600519",
    provider: str = "BaostockFetcher",
    observed_basis: str = "qfq",
    complete: bool = True,
) -> pd.DataFrame:
    labels = list(pd.date_range(
        f"{target.isoformat()} 09:35:00", f"{target.isoformat()} 11:30:00",
        freq="5min",
    )) + list(pd.date_range(
        f"{target.isoformat()} 13:05:00", f"{target.isoformat()} 15:00:00",
        freq="5min",
    ))
    if not complete:
        labels = labels[:-1]
    raw = pd.DataFrame([
        {
            "date": target.isoformat(),
            "time": stamp.strftime("%Y%m%d%H%M%S") + "000",
            "code": f"sh.{code}",
            "open": str(20.0 + index / 100.0),
            "high": str(20.2 + index / 100.0),
            "low": str(19.8 + index / 100.0),
            "close": str(20.1 + index / 100.0),
            "volume": "100",
            "amount": "2000",
            "adjustflag": "2",
        }
        for index, stamp in enumerate(labels)
    ])
    observed = datetime.combine(target, datetime.max.time(), tzinfo=timezone.utc)
    frame = normalize_cn_completed_5m_bars(
        raw, stock_code=code, data_source=provider, observed_at=observed,
    )
    identity = build_intraday_data_identity(
        frame,
        provider_identity=provider,
        provider_route="baostock.query_history_k_data_plus",
        package_version="0.9.4",
        query_identity={
            "code": f"sh.{code}",
            "frequency": "5",
            "adjustflag": "2",
            "start_date": target.isoformat(),
            "end_date": target.isoformat(),
        },
        requested_adjustment_basis="qfq",
        observed_adjustment_basis=observed_basis,
        basis_evidence="unit-test-completed-session",
        timeframe="5m",
        timezone_name="Asia/Shanghai",
        session_calendar="XSHG",
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        requested_start=target.isoformat(),
        requested_end=target.isoformat(),
        identity_state="OBSERVED",
        observed_at=observed,
        source_rows_sha256="d" * 64,
    )
    attach_intraday_data_identity(frame, identity)
    frame["intraday_data_identity_hash"] = identity["identity_hash"]
    return frame


def _bridge_pipeline(target: date) -> StockAnalysisPipeline:
    pipeline = _pipeline(target)
    operation_id = pipeline.b1_rights_context["operation_id"]
    daily = pipeline.b1_rights_context["rights_by_dataset"]["P_DAILY"]
    daily_payload = {key: value for key, value in daily.items() if key != "evidence_hash"}
    daily_payload["rights"] = {
        **daily_payload["rights"], "storage": "FORBIDDEN",
    }
    pipeline.b1_rights_context["rights_by_dataset"]["P_DAILY"] = (
        seal_source_rights_evidence(daily_payload)
    )
    minute_payload = dict(daily_payload)
    minute_payload.update({
        "operation_id": operation_id,
        "dataset_id": "P_5M",
        "consumer_scope": "ASSET_RESEARCH_BRIEF",
    })
    pipeline.b1_rights_context["rights_by_dataset"]["P_5M"] = (
        seal_source_rights_evidence(minute_payload)
    )
    pipeline.fetcher_manager._get_fetcher_by_name.return_value = object()
    return pipeline


def test_b1_daily_provider_one_session_lag_uses_same_provider_completed_5m_run_local() -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target, count=5)
    pipeline = _bridge_pipeline(target)
    pipeline.fetcher_manager.get_daily_data.return_value = (
        _typed_frame(
            sessions[:-1],
            requested_start=sessions[0].isoformat(),
            requested_end=target.isoformat(),
        ),
        "BaostockFetcher",
    )
    minute = _completed_target_5m(target)
    pipeline.fetcher_manager._call_fetcher_method.return_value = minute

    with patch.object(
        pipeline_owner, "_current_daily_window_plan",
        return_value=_plan(target, sessions),
    ):
        success, error = pipeline.fetch_and_save_stock_data("600519")

    assert success is True and error is None
    admitted = pipeline._get_run_local_daily_history(
        code="600519", target_date=target,
    )
    assert admitted is not None
    assert pd.to_datetime(admitted["date"]).dt.date.iloc[-1] == target
    assert admitted.iloc[-1]["open"] == pytest.approx(minute.iloc[0]["open"])
    assert admitted.iloc[-1]["close"] == pytest.approx(minute.iloc[-1]["close"])
    assert admitted.iloc[-1]["volume"] == pytest.approx(minute["volume"].sum())
    assert admitted.iloc[-1]["amount"] == pytest.approx(minute["amount"].sum())
    receipt = extract_daily_acquisition_receipt(admitted, strict=True)
    assert receipt["provider_method_call_count"] == 2
    assert receipt["persistence_status"] == "RIGHTS_NOT_ADMITTED_RUN_LOCAL_ONLY"
    bridge = receipt["daily_completion_bridge"]
    assert bridge["target_date"] == target.isoformat()
    assert bridge["daily_parent_returned_end"] == sessions[-2].isoformat()
    assert bridge["intraday_completed_session"] == target.isoformat()
    assert bridge["minute_row_count"] == 48
    assert bridge["storage_admitted"] is False
    pipeline.db.save_daily_data.assert_not_called()


@pytest.mark.parametrize(
    "defect", ["incomplete", "wrong_provider", "wrong_basis", "wrong_code", "two_session_lag"],
)
def test_b1_daily_completion_bridge_rejects_incomplete_cross_source_or_nonadjacent(defect: str) -> None:
    target = date(2026, 10, 12)
    sessions = _sessions(target, count=5)
    pipeline = _bridge_pipeline(target)
    daily_sessions = sessions[:-2] if defect == "two_session_lag" else sessions[:-1]
    pipeline.fetcher_manager.get_daily_data.return_value = (
        _typed_frame(
            daily_sessions,
            requested_start=sessions[0].isoformat(),
            requested_end=target.isoformat(),
        ),
        "BaostockFetcher",
    )
    minute = _completed_target_5m(
        target,
        code="600000" if defect == "wrong_code" else "600519",
        provider="OtherFetcher" if defect == "wrong_provider" else "BaostockFetcher",
        observed_basis="hfq" if defect == "wrong_basis" else "qfq",
        complete=defect != "incomplete",
    )
    pipeline.fetcher_manager._call_fetcher_method.return_value = minute

    with patch.object(
        pipeline_owner, "_current_daily_window_plan",
        return_value=_plan(target, sessions),
    ):
        success, error = pipeline.fetch_and_save_stock_data("600519")

    assert success is False
    assert "DAILY_ACQUISITION_BINDING_INVALID" in (error or "")
    pipeline.db.save_daily_data.assert_not_called()


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


def _real_window_sessions(start: date, target: date) -> list[date]:
    calendar = xcals.get_calendar("XSHG")
    return [value.date() for value in calendar.sessions_in_range(start, target)]


def _monthly_receipt(
    *, observations: int, status: str = "UNKNOWN", drift: float = 1.0,
    tolerance: float = 0.1,
) -> dict:
    return {
        "method_owner": "StockTrendAnalyzer._calculate_macd",
        "status": status,
        "reason": (
            "MACD_OBSERVED_MULTI_START_NUMERIC_AND_STATE_AGREE"
            if status == "READY"
            else "MACD_MULTI_START_NUMERIC_OR_STATE_DRIFT"
        ),
        "observations": observations,
        "required_observations": pipeline_owner.macd_startup_comparison_required_bars(),
        "readiness_scope": "OBSERVED_COMPLETED_PREFIXES_ONLY",
        "max_endpoint_difference": drift,
        "absolute_tolerance": tolerance,
        "startup_state_agreement": True,
        "numeric_agreement": status == "READY",
        "strategy_admitted": False,
        "learning_admitted": False,
    }


def _extension_frames(target: date, *, instrument: str = "600519"):
    base = pipeline_owner._current_daily_window_plan(market="cn", target_date=target)
    plans = [base]
    for count in (175, 210, 219):
        plans.append(
            pipeline_owner._daily_plan_with_monthly_macd_bars(
                base_plan=base, target_date=target, monthly_bars=count,
            )
        )
    frames = [
        _typed_frame(
            _real_window_sessions(plan["start_date"], target),
            instrument=instrument,
            requested_start=plan["start_date"].isoformat(),
            requested_end=target.isoformat(),
            observed_at=datetime(2026, 10, 12, 16, tzinfo=timezone.utc),
        )
        for plan in plans
    ]
    return base, plans, frames


def test_production_daily_acquisition_boundedly_extends_monthly_macd_and_binds_trace() -> None:
    target = date(2026, 10, 12)
    base, plans, frames = _extension_frames(target)
    pipeline = _pipeline(target)
    pipeline.trend_analyzer = MagicMock()
    pipeline.db.has_today_data.return_value = False
    pipeline.db.save_daily_data.return_value = len(frames[0])
    selected_fetcher = MagicMock()
    selected_fetcher.name = "BaostockFetcher"
    pipeline.fetcher_manager.get_daily_data.return_value = (
        frames[0], "BaostockFetcher",
    )
    pipeline.fetcher_manager._get_fetcher_by_name.return_value = selected_fetcher
    pipeline.fetcher_manager._call_fetcher_method.side_effect = frames[1:]
    receipts = [
        (_monthly_receipt(observations=140, drift=4.8), 140),
        (_monthly_receipt(observations=175, drift=0.5), 175),
        (_monthly_receipt(observations=210, drift=0.145, tolerance=0.125), 210),
        (_monthly_receipt(observations=219, status="READY", drift=0.05, tolerance=0.125), 219),
    ]
    with patch.object(
        pipeline_owner, "_monthly_macd_receipt_from_daily_history",
        side_effect=receipts,
    ):
        success, error = pipeline.fetch_and_save_stock_data(
            "600519", current_time=datetime(2026, 10, 12, 16, tzinfo=timezone.utc),
        )
    assert success is True and error is None
    pipeline.fetcher_manager.get_daily_data.assert_called_once_with(
        "600519",
        start_date=plans[0]["start_date"].isoformat(),
        end_date=target.isoformat(),
        allowed_provider_names={"BaostockFetcher"},
    )
    assert pipeline.fetcher_manager._call_fetcher_method.call_count == 3
    assert [
        call.kwargs["start_date"]
        for call in pipeline.fetcher_manager._call_fetcher_method.call_args_list
    ] == [plan["start_date"].isoformat() for plan in plans[1:]]
    admitted = pipeline._get_run_local_daily_history(code="600519", target_date=target)
    receipt = extract_daily_acquisition_receipt(admitted, strict=True)
    assert receipt["provider_method_call_count"] == 4
    assert receipt["plan_hash"] == plans[-1]["plan_hash"]
    extension = receipt["bounded_window_extension"]
    assert extension["final_status"] == "READY"
    assert [row["requested_monthly_bars"] for row in extension["attempts"]] == [
        140, 175, 210, 219,
    ]
    assert extension["final_source_identity_hash"] == receipt["source_identity_hash"]
    assert receipt["persistence_status"] == "BASE_WINDOW_PERSISTED_EXTENSION_RUN_LOCAL"
    persisted_frame = pipeline.db.save_daily_data.call_args.args[0]
    assert len(persisted_frame) == len(frames[0])
    assert len(admitted) == len(frames[-1])
    assert persisted_frame.attrs["daily_data_identity"]["identity_hash"] == frames[0].attrs[
        "daily_data_identity"
    ]["identity_hash"]

    context = _context(target, 219)
    context["timeframes"]["monthly"]["macd_startup_stability"].update({
        "required_observations": 140,
        "status": "READY",
        "reason": "MACD_OBSERVED_MULTI_START_NUMERIC_AND_STATE_AGREE",
    })
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="STOCK",
        instrument="600519",
        target_date=target,
        daily_window_plan=plans[-1],
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        multi_timeframe_structure_context=context,
        daily_source_frame=admitted,
    )
    row = next(
        item for item in trace["rows"]
        if item["timeframe"] == "monthly"
        and item["component"] == "MACD_STARTUP_COMPARISON"
    )
    assert row["bounded_window_extension_status"] == "READY"
    assert row["bounded_window_extension_provider_calls"] == 4
    assert row["bounded_window_extension_receipt_hash"] == extension["receipt_hash"]

    forged = deepcopy(receipt)
    forged["bounded_window_extension"]["attempts"][1]["returned_base_bars"] += 1
    forged["receipt_hash"] = sha256_payload(
        {key: value for key, value in forged.items() if key != "receipt_hash"}
    )
    with pytest.raises(Exception, match="daily window extension receipt hash mismatch"):
        validate_daily_acquisition_receipt(forged)
    semantic_forgery = deepcopy(receipt)
    semantic_forgery["bounded_window_extension"]["final_status"] = "READY"
    semantic_forgery["bounded_window_extension"]["attempts"][-1]["macd_status"] = "UNKNOWN"
    nested = semantic_forgery["bounded_window_extension"]
    nested["receipt_hash"] = sha256_payload(
        {key: value for key, value in nested.items() if key != "receipt_hash"}
    )
    semantic_forgery["receipt_hash"] = sha256_payload(
        {key: value for key, value in semantic_forgery.items() if key != "receipt_hash"}
    )
    with pytest.raises(Exception, match="READY is not method-bound"):
        validate_daily_acquisition_receipt(semantic_forgery)


def test_production_daily_extension_keeps_listing_history_short_as_nontrigger() -> None:
    target = date(2026, 10, 12)
    base, plans, frames = _extension_frames(target, instrument="510300")
    short = frames[1].iloc[40:].copy()
    short.attrs = deepcopy(frames[1].attrs)
    identity = deepcopy(short.attrs["daily_data_identity"])
    from data_provider.daily_data_identity import rebind_daily_data_identity
    attach_daily_data_identity(short, rebind_daily_data_identity(identity, short))
    pipeline = _pipeline(target)
    pipeline.trend_analyzer = MagicMock()
    pipeline.db.has_today_data.return_value = False
    pipeline.db.save_daily_data.side_effect = PermissionError(
        "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED"
    )
    selected_fetcher = MagicMock()
    selected_fetcher.name = "BaostockFetcher"
    pipeline.fetcher_manager.get_daily_data.return_value = (
        frames[0], "BaostockFetcher",
    )
    pipeline.fetcher_manager._get_fetcher_by_name.return_value = selected_fetcher
    pipeline.fetcher_manager._call_fetcher_method.return_value = short
    with patch.object(
        pipeline_owner, "_monthly_macd_receipt_from_daily_history",
        side_effect=[
            (_monthly_receipt(observations=140, drift=1.0), 140),
            (_monthly_receipt(observations=173, drift=0.2), 173),
        ],
    ):
        assert pipeline.fetch_and_save_stock_data(
            "510300",
            current_time=datetime(2026, 10, 12, 16, tzinfo=timezone.utc),
        ) == (True, None)
    admitted = pipeline._get_run_local_daily_history(code="510300", target_date=target)
    extension = extract_daily_acquisition_receipt(
        admitted, strict=True,
    )["bounded_window_extension"]
    assert extension["final_status"] == "SOURCE_HISTORY_SHORT"
    assert extension["final_reason"] == "SOURCE_RETURNED_FEWER_COMPLETED_MONTHS_THAN_REQUESTED"
    assert extension["provider_method_call_count"] == 2
    assert extension["attempts"][-1]["completed_monthly_bars"] == 173
    assert extension["attempts"][-1]["requested_monthly_bars"] == 175


def test_monthly_macd_ready_is_a_no_extension_provider_nontrigger() -> None:
    target = date(2026, 10, 12)
    _base, _plans, frames = _extension_frames(target)
    pipeline = _pipeline(target)
    pipeline.trend_analyzer = MagicMock()
    pipeline.db.has_today_data.return_value = False
    pipeline.db.save_daily_data.side_effect = PermissionError(
        "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED"
    )
    pipeline.fetcher_manager.get_daily_data.return_value = (
        frames[0], "BaostockFetcher",
    )
    with patch.object(
        pipeline_owner, "_monthly_macd_receipt_from_daily_history",
        return_value=(_monthly_receipt(observations=140, status="READY", drift=0.01), 140),
    ):
        assert pipeline.fetch_and_save_stock_data(
            "600519",
            current_time=datetime(2026, 10, 12, 16, tzinfo=timezone.utc),
        ) == (True, None)
    assert pipeline.fetcher_manager.get_daily_data.call_count == 1
    admitted = pipeline._get_run_local_daily_history(code="600519", target_date=target)
    assert "bounded_window_extension" not in extract_daily_acquisition_receipt(
        admitted, strict=True,
    )


def test_live_current_run_uses_actual_provider_clock_while_frozen_replay_rejects_it() -> None:
    target = date(2026, 10, 12)
    _base, plans, frames = _extension_frames(target)
    late = _typed_frame(
        _real_window_sessions(plans[1]["start_date"], target),
        requested_start=plans[1]["start_date"].isoformat(),
        requested_end=target.isoformat(),
        observed_at=datetime(2026, 10, 12, 17, 30, tzinfo=timezone.utc),
    )
    pipeline = _pipeline(target)
    pipeline.trend_analyzer = MagicMock()
    pipeline.db.has_today_data.return_value = False
    pipeline.db.save_daily_data.side_effect = PermissionError(
        "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED"
    )
    selected_fetcher = MagicMock()
    selected_fetcher.name = "BaostockFetcher"
    pipeline.fetcher_manager.get_daily_data.return_value = (
        frames[0], "BaostockFetcher",
    )
    pipeline.fetcher_manager._get_fetcher_by_name.return_value = selected_fetcher
    pipeline.fetcher_manager._call_fetcher_method.return_value = late
    with patch.object(
        pipeline_owner, "resolve_latest_completed_session_fail_closed", return_value=target,
    ), patch.object(
        pipeline_owner, "_monthly_macd_receipt_from_daily_history",
        side_effect=[
            (_monthly_receipt(observations=140, drift=1.0), 140),
            (_monthly_receipt(observations=173, drift=0.2), 173),
        ],
    ):
        assert pipeline.fetch_and_save_stock_data(
            "600519",
            current_time=datetime(2026, 10, 12, 16, tzinfo=timezone.utc),
            observation_time_frozen=False,
        ) == (True, None)


@pytest.mark.parametrize("mutation", ["provider", "basis", "instrument", "future_clock"])
def test_monthly_extension_source_drift_fails_before_run_local_or_storage(mutation: str) -> None:
    target = date(2026, 10, 12)
    _base, plans, frames = _extension_frames(target)
    kwargs = {
        "instrument": "510300" if mutation == "instrument" else "600519",
        "provider": "OtherFetcher" if mutation == "provider" else "BaostockFetcher",
        "requested_start": plans[1]["start_date"].isoformat(),
        "requested_end": target.isoformat(),
        "observed_basis": "hfq" if mutation == "basis" else "qfq",
        "observed_at": (
            datetime(2026, 10, 13, 17, tzinfo=timezone.utc)
            if mutation == "future_clock"
            else datetime(2026, 10, 12, 16, tzinfo=timezone.utc)
        ),
    }
    bad = _typed_frame(_real_window_sessions(plans[1]["start_date"], target), **kwargs)
    pipeline = _pipeline(target)
    pipeline.trend_analyzer = MagicMock()
    pipeline.db.has_today_data.return_value = False
    selected_fetcher = MagicMock()
    selected_fetcher.name = "BaostockFetcher"
    pipeline.fetcher_manager.get_daily_data.return_value = (
        frames[0], "BaostockFetcher",
    )
    pipeline.fetcher_manager._get_fetcher_by_name.return_value = selected_fetcher
    pipeline.fetcher_manager._call_fetcher_method.return_value = bad
    with patch.object(
        pipeline_owner, "_monthly_macd_receipt_from_daily_history",
        return_value=(_monthly_receipt(observations=140, drift=1.0), 140),
    ):
        success, error = pipeline.fetch_and_save_stock_data(
            "600519", current_time=datetime(2026, 10, 12, 16, tzinfo=timezone.utc),
        )
    assert success is False
    assert "DAILY_ACQUISITION_BINDING_INVALID" in (error or "")
    assert pipeline._get_run_local_daily_history(code="600519", target_date=target) is None
    pipeline.db.save_daily_data.assert_not_called()
