"""Bounded offline regression for the opt-in cloud latest A-share daily data probe.

No test invokes a real provider, GitHub Action, network, database, model or channel.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from data_provider.daily_data_identity import (
    attach_daily_data_identity,
    build_daily_data_identity,
    build_unclassified_daily_data_identity,
)

UTC = timezone.utc
AFTER_CLOSE = datetime(2026, 10, 8, 11, tzinfo=UTC)
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "network-smoke.yml"


def _frame(include_today: bool = True) -> pd.DataFrame:
    dates = ["2026-09-29", "2026-09-30"]
    if include_today:
        dates.append("2026-10-08")
    return pd.DataFrame([
        {
            "date": day,
            "open": 10.0 + i,
            "high": 11.0 + i,
            "low": 9.0 + i,
            "close": 10.5 + i,
            "volume": 1000.0 + i,
            "amount": 10500.0 + i,
            "pct_chg": 1.0 + i,
        }
        for i, day in enumerate(dates)
    ])


def _observed(frame: pd.DataFrame, *, observed_at: datetime = AFTER_CLOSE,
              source: str = "TencentFetcher") -> pd.DataFrame:
    attach_daily_data_identity(frame, build_daily_data_identity(
        frame,
        provider_identity=source,
        provider_route="tencent.fqkline.day",
        actual_response_branch="qfqday",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="response_key:qfqday",
        requested_start="2026-08-01",
        requested_end="2026-10-08",
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        identity_state="OBSERVED",
        observed_at=observed_at,
    ))
    frame.attrs["_b8b_native_requested_stock_code"] = "600519"
    return frame


def _evaluate(frame: pd.DataFrame, now: datetime = AFTER_CLOSE) -> dict:
    from src.services.cloud_cn_daily_probe import evaluate_daily_snapshot
    return evaluate_daily_snapshot(frame, code="600519", source_name="TencentFetcher", now=now)


def test_latest_completed_observed_tencent_qfq_is_readonly_probe_pass_not_pit() -> None:
    receipt = _evaluate(_observed(_frame()))
    assert receipt["status"] == "OBSERVED_LATEST_DAILY_READONLY"
    assert receipt["latest_session"] == "2026-10-08"
    assert receipt["row_count"] == 3
    assert receipt["observed_adjustment_basis"] == "qfq"
    assert receipt["rights_status"] == "UNKNOWN"
    assert receipt["pit_eligible"] is False
    assert receipt["training_eligible"] is False
    assert receipt["notification_suppressed"] is True
    assert len(receipt["content_sha256"]) == 64
    assert "close" not in receipt and "amount" not in receipt


def test_stale_historical_last_bar_is_rejected_even_if_identity_consistent() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    with pytest.raises(CloudDailyProbeError, match="latest completed"):
        _evaluate(_observed(_frame(include_today=False)))


def test_request_for_future_availability_cannot_pass() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    future = datetime(2026, 10, 9, 1, tzinfo=UTC)
    with pytest.raises(CloudDailyProbeError, match="observed_at"):
        _evaluate(_observed(_frame(), observed_at=future))


def test_unclassified_day_branch_is_not_promoted_to_qfq() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    frame = _frame()
    attach_daily_data_identity(frame, build_unclassified_daily_data_identity(
        frame, provider_identity="TencentFetcher", provider_route="tencent.fqkline.day",
        requested_start="2026-08-01", requested_end="2026-10-08",
        actual_response_branch="day",
    ))
    with pytest.raises(CloudDailyProbeError, match="OBSERVED"):
        _evaluate(frame)


def test_missing_transient_native_stock_request_code_is_rejected() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError

    frame = _observed(_frame())
    frame.attrs.pop("_b8b_native_requested_stock_code")
    with pytest.raises(CloudDailyProbeError, match="asset code"):
        _evaluate(frame)


def test_missing_source_identity_is_rejected() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    with pytest.raises(CloudDailyProbeError, match="identity"):
        _evaluate(_frame())


def test_changed_daily_content_after_identity_binding_is_detected() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    frame = _observed(_frame())
    frame.loc[frame.index[-1], "close"] += 0.2
    with pytest.raises(CloudDailyProbeError, match="hash"):
        _evaluate(frame)


def test_even_bound_identity_cannot_hide_invalid_ohlc() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    frame = _frame()
    frame.loc[frame.index[-1], "high"] = 1.0
    with pytest.raises(CloudDailyProbeError, match="OHLC"):
        _evaluate(_observed(frame))


def test_holiday_row_inside_current_ohlcv_is_not_an_admissible_session() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    frame = _frame()
    frame.loc[frame.index[1], "date"] = "2026-10-04"  # XSHG holiday
    with pytest.raises(CloudDailyProbeError, match="non-session"):
        _evaluate(_observed(frame))


def test_wrong_producer_identity_cannot_impersonate_tencent() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    with pytest.raises(CloudDailyProbeError, match="provider"):
        _evaluate(_observed(_frame(), source="AkshareFetcher"))


def test_valid_premarket_previous_completed_session_is_not_overblocked() -> None:
    from src.services.cloud_cn_daily_probe import evaluate_daily_snapshot
    now = datetime(2026, 10, 8, 1, tzinfo=UTC)
    observed_at = datetime(2026, 9, 30, 10, tzinfo=UTC)
    receipt = evaluate_daily_snapshot(
        _observed(_frame(include_today=False), observed_at=observed_at),
        code="600519", source_name="TencentFetcher", now=now,
    )
    assert receipt["latest_session"] == "2026-09-30"
    assert receipt["status"] == "OBSERVED_LATEST_DAILY_READONLY"


def test_absent_trading_calendar_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    import src.core.trading_calendar as calendar
    monkeypatch.setattr(calendar, "resolve_latest_completed_session_fail_closed",
                        lambda *args, **kwargs: None)
    with pytest.raises(CloudDailyProbeError, match="calendar"):
        _evaluate(_observed(_frame()))


def test_cloud_runtime_proxy_configuration_fails_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError, enforce_zero_proxy
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    with pytest.raises(CloudDailyProbeError, match="proxy"):
        enforce_zero_proxy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "SOCKS_PROXY", "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "socks_proxy"):
        monkeypatch.delenv(key, raising=False)
    enforce_zero_proxy()



def test_actual_probe_main_rejects_proxy_before_native_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Entry-point safety: shell guard, not just isolated helper, blocks the provider."""
    import json
    from unittest import mock
    from src.services import cloud_cn_daily_probe as probe

    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:7777")
    with mock.patch.object(probe, "fetch_bound_tencent_daily") as fetch:
        assert probe.main() == 2
    fetch.assert_not_called()
    outcome = json.loads(capsys.readouterr().out.strip())
    assert outcome["status"] == "STOP"
    assert outcome["failure_kind"] == "CloudDailyProbeError"
    assert outcome["live_product_admitted"] is False


def test_actual_probe_main_valid_completed_session_uses_one_native_bound_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A completed legal session can pass without leaking data, training or notification."""
    import json
    from unittest import mock
    from src.services import cloud_cn_daily_probe as probe

    for key in probe._PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz: timezone) -> datetime:
            assert tz is timezone.utc
            return cls(2026, 10, 8, 11, tzinfo=tz)

    frame = _observed(_frame())
    with mock.patch.object(probe, "datetime", FixedClock), mock.patch.object(
        probe, "fetch_bound_tencent_daily", return_value=frame,
    ) as fetch:
        assert probe.main() == 0
    fetch.assert_called_once()
    assert fetch.call_args.args == ("600519",)
    outcome = json.loads(capsys.readouterr().out.strip())
    assert outcome["status"] == "OBSERVED_LATEST_DAILY_READONLY"
    assert outcome["code"] == "600519"
    assert outcome["latest_session"] == "2026-10-08"
    assert outcome["rights_status"] == "UNKNOWN"
    assert outcome["pit_eligible"] is False
    assert outcome["training_eligible"] is False
    assert outcome["notification_suppressed"] is True
    assert "close" not in outcome and "open" not in outcome


def test_public_cloud_workflow_is_manual_readonly_and_private_schedule_nontrigger() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    import yaml
    doc = yaml.load(text, Loader=yaml.BaseLoader)
    assert doc["on"]["schedule"] == [{"cron": "0 2 * * 1-5"}]
    assert doc["jobs"]["smoke"]["if"] == "${{ github.repository == 'leeningzzu/daily_stock_analysis-private' }}"
    assert "cn-daily-readonly-probe" in doc["on"]["workflow_dispatch"]["inputs"]["mode"]["options"]
    probe = doc["jobs"]["cn-daily-readonly-probe"]
    assert "github.repository == 'leeningzzu/dsa-market-research'" in probe["if"]
    assert "github.event_name == 'workflow_dispatch'" in probe["if"]
    assert "inputs.mode == 'cn-daily-readonly-probe'" in probe["if"]
    assert probe["permissions"] == {"contents": "read"}
    assert "environment" not in probe
    assert "continue-on-error" not in probe
    job_wire = str(probe)
    for forbidden in ("secrets.", "send_to_email", "send_to_telegram", "DATABASE_PATH",
                      "workflow_call", "upload-artifact", "train_model"):
        assert forbidden not in job_wire
    assert "python -m src.services.cloud_cn_daily_probe" in job_wire




def test_public_cloud_dependency_install_uses_zero_proxy_boundary() -> None:
    """The networked pip step must be proxy-free, not just the later price call."""
    import yaml
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["cn-daily-readonly-probe"]["steps"]
    dependency_step = next(item for item in steps if item["name"] == "Install existing requirements only")
    commands = dependency_step["run"].splitlines()
    install_index = next(i for i, line in enumerate(commands) if "pip install" in line)
    assert install_index > 0
    preinstall = "\n".join(commands[:install_index])
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "SOCKS_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "socks_proxy"):
        assert name in preinstall
    assert "unset " in preinstall
    assert "continue-on-error" not in dependency_step

def _native_tencent_request(branch: str) -> tuple:
    """Run the actual native fetcher/normalizer with only HTTP mocked."""
    from unittest import mock
    from src.services.cloud_cn_daily_probe import (
        evaluate_daily_snapshot, fetch_bound_tencent_daily,
    )
    from src.core import trading_calendar
    from datetime import timedelta

    native_rows = [
        [day, str(10 + index), str(10.5 + index), str(11 + index),
         str(9 + index), str(100 + index), str(10500 + index)]
        for index, day in enumerate(("2026-09-29", "2026-09-30", "2026-10-08"))
    ]
    with mock.patch("data_provider.tencent_fetcher.requests.get") as getter:
        getter.return_value.json.return_value = {
            "data": {"sh600519": {branch: native_rows}},
        }
        response = fetch_bound_tencent_daily(
            "600519", start_date="2026-08-01", end_date="2026-10-08",
        )
        getter.assert_called_once()
    # Freeze exchange result only; source HTTP parsing, typed identity, price
    # normalization and consumer check are the actual implementations.
    with mock.patch.object(
        trading_calendar, "resolve_latest_completed_session_fail_closed",
        return_value=date(2026, 10, 8),
    ):
        receipt = evaluate_daily_snapshot(
            response, code="600519", source_name="TencentFetcher",
            now=datetime.now(UTC) + timedelta(minutes=1),
        )
    return response, receipt


def test_native_tencent_qfq_response_flows_to_probe_without_db_or_network() -> None:
    from data_provider.daily_data_identity import extract_daily_data_identity
    data, receipt = _native_tencent_request("qfqday")
    assert len(data) == 3
    assert receipt["status"] == "OBSERVED_LATEST_DAILY_READONLY"
    assert extract_daily_data_identity(data, strict=True)["actual_response_branch"] == "qfqday"
    assert receipt["live_product_admitted"] is False




def test_native_tencent_other_stock_cannot_be_mislabelled_as_600519() -> None:
    """The source's actual requested symbol, not a caller-provided label, controls stock identity."""
    from datetime import timedelta
    from unittest import mock
    from src.services.cloud_cn_daily_probe import (
        CloudDailyProbeError, evaluate_daily_snapshot, fetch_bound_tencent_daily,
    )
    from src.core import trading_calendar

    native_rows = [
        [day, str(10 + index), str(10.5 + index), str(11 + index),
         str(9 + index), str(100 + index), str(10500 + index)]
        for index, day in enumerate(("2026-09-29", "2026-09-30", "2026-10-08"))
    ]
    with mock.patch("data_provider.tencent_fetcher.requests.get") as getter:
        getter.return_value.json.return_value = {
            "data": {"sz000001": {"qfqday": native_rows}},
        }
        wrong_asset = fetch_bound_tencent_daily(
            "000001", start_date="2026-08-01", end_date="2026-10-08",
        )
        assert "sz000001" in getter.call_args.kwargs["params"]["param"]

    with mock.patch.object(
        trading_calendar, "resolve_latest_completed_session_fail_closed",
        return_value=date(2026, 10, 8),
    ), pytest.raises(CloudDailyProbeError, match="asset code"):
        evaluate_daily_snapshot(
            wrong_asset, code="600519", source_name="TencentFetcher",
            now=datetime.now(UTC) + timedelta(minutes=1),
        )


def test_native_tencent_six_field_qfqday_admits_ohlcv_but_never_fabricates_amount() -> None:
    """Native six-field OHLCV is useful, but missing turnover value stays MISSING."""
    from unittest import mock
    from datetime import timedelta
    from src.services.cloud_cn_daily_probe import (
        fetch_bound_tencent_daily, evaluate_daily_snapshot,
    )
    from src.core import trading_calendar

    six_field_rows = [
        [day, str(10 + index), str(10.5 + index), str(11 + index),
         str(9 + index), str(100 + index)]
        for index, day in enumerate(("2026-09-29", "2026-09-30", "2026-10-08"))
    ]
    with mock.patch("data_provider.tencent_fetcher.requests.get") as getter:
        getter.return_value.json.return_value = {
            "data": {"sh600519": {"qfqday": six_field_rows}},
        }
        frame = fetch_bound_tencent_daily(
            "600519", start_date="2026-08-01", end_date="2026-10-08",
        )
        getter.assert_called_once()
    assert frame["amount"].isna().all()
    with mock.patch.object(
        trading_calendar, "resolve_latest_completed_session_fail_closed",
        return_value=date(2026, 10, 8),
    ):
        receipt = evaluate_daily_snapshot(
            frame, code="600519", source_name="TencentFetcher",
            now=datetime.now(UTC) + timedelta(minutes=1),
        )
    assert receipt["status"] == "OBSERVED_LATEST_DAILY_READONLY"
    assert receipt["ohlcv_state"] == "READY"
    assert receipt["amount_state"] == "MISSING"
    assert receipt["amount_dependent_methods_ready"] is False
    assert receipt["pit_eligible"] is False
    assert receipt["live_product_admitted"] is False
    assert "amount" not in receipt and "turnover" not in receipt


def test_seven_field_response_must_preserve_real_observed_amount() -> None:
    receipt = _evaluate(_observed(_frame()))
    assert receipt["ohlcv_state"] == "READY"
    assert receipt["amount_state"] == "READY"
    assert receipt["amount_dependent_methods_ready"] is False



def test_actual_cli_main_six_field_native_response_keeps_amount_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Real CLI consumer, actual native parser, mocked HTTP: no made-up turnover."""
    import json
    from unittest import mock
    from src.services import cloud_cn_daily_probe as probe

    for key in probe._PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz: timezone) -> datetime:
            assert tz is timezone.utc
            return cls(2026, 10, 8, 11, tzinfo=tz)

    six_rows = [
        [day, str(10 + i), str(10.5 + i), str(11 + i),
         str(9 + i), str(100 + i)]
        for i, day in enumerate(("2026-09-29", "2026-09-30", "2026-10-08"))
    ]
    import data_provider.daily_data_identity as identity_module

    # Provider observed_at and the consumer clock must share one test instant.
    with mock.patch.object(probe, "datetime", FixedClock), mock.patch.object(
        identity_module, "datetime", FixedClock,
    ), mock.patch(
        "data_provider.tencent_fetcher.requests.get",
    ) as getter:
        getter.return_value.json.return_value = {
            "data": {"sh600519": {"qfqday": six_rows}},
        }
        assert probe.main() == 0
        getter.assert_called_once()
        assert "sh600519" in getter.call_args.kwargs["params"]["param"]

    receipt = json.loads(capsys.readouterr().out.strip())
    assert receipt["status"] == "OBSERVED_LATEST_DAILY_READONLY"
    assert receipt["latest_session"] == "2026-10-08"
    assert receipt["code"] == "600519"
    assert receipt["ohlcv_state"] == "READY"
    assert receipt["amount_state"] == "MISSING"
    assert receipt["amount_dependent_methods_ready"] is False
    assert receipt["rights_status"] == "UNKNOWN"
    assert receipt["live_product_admitted"] is False
    assert receipt["notification_suppressed"] is True
    assert "amount" not in receipt and "close" not in receipt


def test_partly_missing_amount_does_not_mix_with_complete_amount() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    frame = _frame()
    frame.loc[frame.index[0], "amount"] = float("nan")
    with pytest.raises(CloudDailyProbeError, match="amount"):
        _evaluate(_observed(frame))


def test_native_tencent_day_fallback_stays_unclassified_and_blocked() -> None:
    from src.services.cloud_cn_daily_probe import CloudDailyProbeError
    with pytest.raises(CloudDailyProbeError, match="OBSERVED"):
        _native_tencent_request("day")
