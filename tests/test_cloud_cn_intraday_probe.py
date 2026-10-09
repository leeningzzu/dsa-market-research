"""Offline B8D latest completed XSHG 5m cloud consumer tests.

No provider HTTP/TCP or login, GitHub dispatch, database, SMTP, or model calls.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import exchange_calendars as xcals
import pandas as pd
import pytest
import yaml

from data_provider.intraday_data_identity import (
    attach_intraday_data_identity,
    build_intraday_data_identity,
)
from src.services.intraday_bar_service import normalize_cn_completed_5m_bars


NOW = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "network-smoke.yml"


def _sessions() -> list[date]:
    calendar = xcals.get_calendar("XSHG")
    end = calendar.date_to_session("2026-10-08")
    return [x.date() for x in calendar.sessions_window(end, -10)]


def _native_canonical_5m(*, code: str = "600519") -> pd.DataFrame:
    raw_rows = []
    ix = 0
    for session in _sessions():
        for hour, minutes in ((9, range(35, 61, 5)), (10, range(0, 61, 5)),
                              (11, range(0, 31, 5)), (13, range(5, 61, 5)),
                              (14, range(0, 61, 5)), (15, (0,))):
            for minute in minutes:
                # The duplicate 10:00/11:00/14:00 labels below are filtered
                # deterministically before the native normalizer is invoked.
                if minute == 60:
                    continue
                if (hour == 9 and minute > 55) or (hour == 10 and minute > 55):
                    continue
                if (hour == 13 and minute > 55) or (hour == 14 and minute > 55):
                    continue
                if hour == 11 and minute > 30:
                    continue
                if hour == 15 and minute != 0:
                    continue
                local = pd.Timestamp(
                    f"{session.isoformat()} {hour:02d}:{minute:02d}:00",
                    tz="Asia/Shanghai",
                )
                ix += 1
                value = 1200 + ix / 1000.0
                raw_rows.append({
                    "date": session.isoformat(),
                    "time": local.strftime("%Y%m%d%H%M%S") + "000",
                    "code": f"sh.{code}",
                    "open": str(value),
                    "high": str(value + 0.2),
                    "low": str(value - 0.2),
                    "close": str(value + 0.1),
                    "volume": "100",
                    "amount": "120000",
                    "adjustflag": "2",
                })
    raw = pd.DataFrame(raw_rows)
    frame = normalize_cn_completed_5m_bars(
        raw, stock_code=code, data_source="BaostockFetcher", observed_at=NOW,
    )
    identity = build_intraday_data_identity(
        frame, provider_identity="BaostockFetcher",
        provider_route="baostock.query_history_k_data_plus",
        package_version="0.9.4",
        query_identity={"code": f"sh.{code}", "fields": "date,time,code,open,high,low,close,volume,amount,adjustflag",
                        "frequency": "5", "adjustflag": "2",
                        "start_date": _sessions()[0].isoformat(),
                        "end_date": _sessions()[-1].isoformat()},
        requested_adjustment_basis="qfq", observed_adjustment_basis="qfq",
        basis_evidence="successful_nonempty_query:adjustflag=2",
        timeframe="5m", timezone_name="Asia/Shanghai",
        session_calendar="XSHG", currency="CNY",
        volume_unit="UNKNOWN", amount_unit="UNKNOWN",
        requested_start=_sessions()[0].isoformat(), requested_end=_sessions()[-1].isoformat(),
        identity_state="OBSERVED", observed_at=NOW,
        source_rows_sha256="a" * 64,
    )
    attach_intraday_data_identity(frame, identity)
    frame["intraday_data_identity_hash"] = identity["identity_hash"]
    return frame


def test_b8d_public_workflow_is_only_explicit_manual_readonly_mode() -> None:
    flow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert "cn-intraday-readonly-probe" in flow["on"]["workflow_dispatch"]["inputs"]["mode"]["options"]
    assert flow["on"]["schedule"] == [{"cron": "0 2 * * 1-5"}]
    assert "leeningzzu/daily_stock_analysis-private" in flow["jobs"]["smoke"]["if"]
    assert "cn-daily-readonly-probe" in flow["jobs"]
    job = flow["jobs"]["cn-intraday-readonly-probe"]
    assert job["permissions"] == {"contents": "read"}
    assert int(job["timeout-minutes"]) <= 12
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert "inputs.mode == 'cn-intraday-readonly-probe'" in job["if"]
    assert "leeningzzu/dsa-market-research" in job["if"]
    assert "secrets." not in str(job)
    assert "environment" not in job and "continue-on-error" not in job
    assert "upload-artifact" not in str(job)
    for step in job["steps"]:
        run = step.get("run", "")
        if "pip install" in run or "cloud_cn_intraday_probe" in run:
            assert run.index("unset HTTP_PROXY HTTPS_PROXY ALL_PROXY") < run.index("python -m")


def test_b8d_native_5m_to_four_timeframes_is_diagnostic_only() -> None:
    from src.services.cloud_cn_intraday_probe import evaluate_intraday_snapshot
    frame = _native_canonical_5m()
    receipt = evaluate_intraday_snapshot(
        frame, code="600519", expected_sessions=_sessions(), now=NOW + timedelta(minutes=1),
    )
    assert receipt["status"] == "OBSERVED_LATEST_INTRADAY_READONLY"
    assert receipt["latest_session"] == "2026-10-08"
    assert receipt["timeframe_row_counts"] == {"5m": 480, "15m": 160, "30m": 80, "60m": 40}
    assert receipt["amount_unit"] == "UNKNOWN"
    assert receipt["volume_unit"] == "UNKNOWN"
    assert receipt["amount_dependent_methods_ready"] is False
    assert receipt["rights_status"] == "UNKNOWN"
    assert receipt["pit_eligible"] is False and receipt["training_eligible"] is False
    assert receipt["live_product_admitted"] is False
    assert receipt["notification_suppressed"] is True
    assert receipt["persistence"] == "NONE"
    assert "close" not in receipt and "volume" not in receipt and "amount" not in receipt


def test_b8d_missing_one_five_minute_bar_is_not_full_coverage() -> None:
    from src.services.cloud_cn_intraday_probe import CloudIntradayProbeError, evaluate_intraday_snapshot
    frame = _native_canonical_5m().iloc[:-1].copy()
    with pytest.raises(CloudIntradayProbeError, match="identity|session|complete"):
        evaluate_intraday_snapshot(frame, code="600519", expected_sessions=_sessions(), now=NOW + timedelta(minutes=1))


def test_b8d_wrong_asset_fails_source_request_binding() -> None:
    from src.services.cloud_cn_intraday_probe import CloudIntradayProbeError, evaluate_intraday_snapshot
    with pytest.raises(CloudIntradayProbeError, match="asset|code"):
        evaluate_intraday_snapshot(
            _native_canonical_5m(code="000001"), code="600519",
            expected_sessions=_sessions(), now=NOW + timedelta(minutes=1),
        )




def test_b8d_wrong_dataframe_asset_rejected_even_if_query_label_falsely_matches() -> None:
    """Prove the evaluator checks actual typed rows, not just the claimed request."""
    from data_provider.intraday_data_identity import (
        attach_intraday_data_identity,
        build_intraday_data_identity,
        extract_intraday_data_identity,
    )
    from src.services.cloud_cn_intraday_probe import (
        CloudIntradayProbeError, evaluate_intraday_snapshot,
    )
    frame = _native_canonical_5m(code="000001")
    original = extract_intraday_data_identity(frame, strict=True)
    assert original is not None and set(frame["code"]) == {"000001"}
    forged = build_intraday_data_identity(
        frame,
        provider_identity=original["provider_identity"],
        provider_route=original["provider_route"],
        package_version=original["package_version"],
        query_identity={**original["query_identity"], "code": "sh.600519"},
        requested_adjustment_basis=original["requested_adjustment_basis"],
        observed_adjustment_basis=original["observed_adjustment_basis"],
        basis_evidence=original["basis_evidence"],
        timeframe="5m", timezone_name="Asia/Shanghai",
        session_calendar="XSHG", currency="CNY",
        volume_unit="UNKNOWN", amount_unit="UNKNOWN",
        requested_start=_sessions()[0].isoformat(),
        requested_end=_sessions()[-1].isoformat(),
        identity_state="OBSERVED", observed_at=NOW,
        source_rows_sha256=original["source_rows_sha256"],
    )
    attach_intraday_data_identity(frame, forged)
    frame["intraday_data_identity_hash"] = forged["identity_hash"]
    with pytest.raises(CloudIntradayProbeError, match="asset|code"):
        evaluate_intraday_snapshot(
            frame, code="600519", expected_sessions=_sessions(),
            now=NOW + timedelta(minutes=1),
        )


def test_b8d_actual_cli_entry_produces_readonly_metadata_without_provider_io(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full one-stock main entry must work when native frame is source-bound."""
    import json
    from src.services import cloud_cn_intraday_probe as probe

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "SOCKS_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "socks_proxy"):
        monkeypatch.delenv(key, raising=False)

    class FrozenTime(datetime):
        @classmethod
        def now(cls, tz: timezone) -> datetime:
            assert tz is timezone.utc
            return cls(2026, 10, 9, 2, 0, tzinfo=tz)

    frame = _native_canonical_5m()
    with mock.patch.object(probe, "datetime", FrozenTime), mock.patch(
        "data_provider.baostock_fetcher.BaostockFetcher.get_intraday_data",
        return_value=frame,
    ) as fetch:
        assert probe.main() == 0
    fetch.assert_called_once_with(
        "600519",
        start_date=_sessions()[0].isoformat(),
        end_date=_sessions()[-1].isoformat(),
        frequency="5",
    )
    receipt = json.loads(capsys.readouterr().out.strip())
    assert receipt["status"] == "OBSERVED_LATEST_INTRADAY_READONLY"
    assert receipt["timeframe_row_counts"] == {
        "5m": 480, "15m": 160, "30m": 80, "60m": 40,
    }
    assert receipt["rights_status"] == "UNKNOWN"
    assert receipt["amount_dependent_methods_ready"] is False
    assert receipt["persistence"] == "NONE"
    assert receipt["notification_suppressed"] is True
    assert receipt["live_product_admitted"] is False
    assert "close" not in receipt and "amount" not in receipt

def test_b8d_future_observed_source_is_rejected() -> None:
    from src.services.cloud_cn_intraday_probe import CloudIntradayProbeError, evaluate_intraday_snapshot
    with pytest.raises(CloudIntradayProbeError, match="future|observ"):
        evaluate_intraday_snapshot(
            _native_canonical_5m(), code="600519",
            expected_sessions=_sessions(), now=NOW - timedelta(minutes=1),
        )


def test_b8d_main_rejects_proxy_before_importing_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    from src.services import cloud_cn_intraday_probe as probe

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    with mock.patch("data_provider.baostock_fetcher.BaostockFetcher.get_intraday_data") as fetch:
        assert probe.main() == 2
    fetch.assert_not_called()
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "STOP"
    assert output["stage"] == "PRECHECK"


def test_b8d_no_network_on_same_schema_inspection() -> None:
    from src.services.cloud_cn_intraday_probe import _expected_sessions
    expected, plan = _expected_sessions(NOW)
    assert expected == _sessions()
    assert plan["required_sessions"] == 10
    assert plan["required_bars"] == 40



def test_b8d_method_owner_growth_expands_calendar_window_without_fixed_ten() -> None:
    from src.services.cloud_cn_intraday_probe import _expected_sessions

    with mock.patch(
        "src.core.pipeline._current_intraday_window_plan",
        return_value={"required_bars": 61, "required_sessions": 16},
    ):
        sessions, plan = _expected_sessions(NOW)
    assert plan["required_bars"] == 61
    assert len(sessions) == 16
    assert sessions[-1] == date(2026, 10, 8)
    assert sessions[0] < _sessions()[0]


def test_b8d_budget_rejects_unbounded_owner_instead_of_truncating() -> None:
    from src.services.cloud_cn_intraday_probe import (
        CloudIntradayProbeError, _expected_sessions,
    )

    with mock.patch(
        "src.core.pipeline._current_intraday_window_plan",
        return_value={"required_bars": 150, "required_sessions": 38},
    ), pytest.raises(CloudIntradayProbeError, match="budget"):
        _expected_sessions(NOW)


def test_b8d_stale_target_fails_even_with_consistent_source() -> None:
    from src.services.cloud_cn_intraday_probe import CloudIntradayProbeError, evaluate_intraday_snapshot
    old = _sessions()[:-1]
    with pytest.raises(CloudIntradayProbeError, match="session"):
        evaluate_intraday_snapshot(
            _native_canonical_5m(), code="600519",
            expected_sessions=old, now=NOW + timedelta(minutes=1),
        )


def test_b8d_after_io_exchange_rollover_is_not_silent_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import cloud_cn_intraday_probe as probe
    old = _sessions()
    monkeypatch.setattr(probe, "resolve_latest_completed_session_fail_closed", lambda *a, **kw: date(2026, 10, 9))
    with pytest.raises(probe.CloudIntradayProbeError, match="latest|session"):
        probe.evaluate_intraday_snapshot(
            _native_canonical_5m(), code="600519", expected_sessions=old,
            now=NOW + timedelta(minutes=1),
        )
