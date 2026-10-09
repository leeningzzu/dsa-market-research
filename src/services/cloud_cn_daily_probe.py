"""Opt-in, in-memory cloud probe of one completed Chinese A-share daily bar.

This does not admit commercial data rights, financial PIT, storage, models, or
notifications. It reuses the existing DSA TencentFetcher and XSHG calendar.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from typing import Any

import pandas as pd

from data_provider.daily_data_identity import (
    DailyDataIdentityError,
    extract_daily_data_identity,
    identity_is_durable_price_ready,
    rebind_daily_data_identity,
)


class CloudDailyProbeError(ValueError):
    """Fail-closed data-probe boundary, independent from DSA report authority."""


_PROXY_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "SOCKS_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "socks_proxy",
)


def enforce_zero_proxy(environ: Any = None) -> None:
    """Cloud runtime must not depend on an HTTP(S)/SOCKS project proxy."""
    values = os.environ if environ is None else environ
    if any(str(values.get(key) or "").strip() for key in _PROXY_KEYS):
        raise CloudDailyProbeError("cloud proxy configuration is forbidden")


_NATIVE_REQUEST_CODE_ATTR = "_b8b_native_requested_stock_code"


def fetch_bound_tencent_daily(
    code: str, *, start_date: str, end_date: str,
) -> pd.DataFrame:
    """Bind a single native Tencent request to its in-process data frame.

    Native TencentFetcher looks up only the requested symbol in the actual
    response branch. The transient code binding must not be persisted or used
    as a historical-PIT / source-rights authority.
    """
    from data_provider.tencent_fetcher import TencentFetcher

    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        raise CloudDailyProbeError("asset code must be a six-digit CN stock code")
    frame = TencentFetcher().get_daily_data(
        code, start_date=start_date, end_date=end_date,
    )
    if not isinstance(frame, pd.DataFrame):
        raise CloudDailyProbeError("native Tencent request returned no data frame")
    frame.attrs[_NATIVE_REQUEST_CODE_ATTR] = code
    return frame


def evaluate_daily_snapshot(
    frame: pd.DataFrame,
    *,
    code: str,
    source_name: str,
    now: datetime,
) -> dict[str, Any]:
    """Accept only a source-bound, fully completed and actually observed 1d slice.

    A PASS here is a single non-persisted diagnostic; it never authorizes
    research storage, financial rights, PIT history, a BUY, or Product delivery.
    """
    from src.core.trading_calendar import resolve_latest_completed_session_fail_closed

    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise CloudDailyProbeError("probe UTC time must be timezone-aware")
    target = resolve_latest_completed_session_fail_closed("cn", current_time=now)
    if target is None:
        raise CloudDailyProbeError("exchange calendar could not prove latest completed session")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise CloudDailyProbeError("provider returned zero daily observations")
    # Tencent qfqday can provide six OHLCV fields with no transaction amount.
    # Do not invent amount just to make the first read-only source trial green.
    required = ("date", "open", "high", "low", "close", "volume")
    if any(field not in frame.columns for field in required):
        raise CloudDailyProbeError("provider did not return complete native OHLCV fields")

    try:
        identity = extract_daily_data_identity(frame, strict=True)
    except DailyDataIdentityError as exc:
        raise CloudDailyProbeError("source identity is invalid") from exc
    if identity is None:
        raise CloudDailyProbeError("source identity is absent")
    if identity.get("provider_identity") != source_name:
        raise CloudDailyProbeError("provider identity differs from selected native fetcher")
    if identity.get("identity_state") != "OBSERVED" or not identity_is_durable_price_ready(identity):
        raise CloudDailyProbeError("source basis is not OBSERVED and independently evidenced")
    if identity.get("observed_adjustment_basis") != "qfq" or identity.get("actual_response_branch") != "qfqday":
        raise CloudDailyProbeError("source qfq must be supported by the actual response branch")
    if (identity.get("currency"), identity.get("volume_unit"), identity.get("amount_unit")) != (
        "CNY", "share", "CNY"
    ):
        raise CloudDailyProbeError("source price/volume/amount units are not independently bound")

    try:
        observed = datetime.fromisoformat(str(identity.get("observed_at") or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise CloudDailyProbeError("source observed_at is invalid") from exc
    if observed.tzinfo is None or observed.utcoffset() is None or observed > now:
        raise CloudDailyProbeError("source observed_at is missing, naive or later than the probe")

    try:
        rebound = rebind_daily_data_identity(identity, frame)
    except (DailyDataIdentityError, TypeError, ValueError) as exc:
        raise CloudDailyProbeError("source content hash could not be verified") from exc
    if rebound["content_sha256"] != identity["content_sha256"]:
        raise CloudDailyProbeError("source content hash drift after identity binding")
    if frame.attrs.get(_NATIVE_REQUEST_CODE_ATTR) != str(code):
        raise CloudDailyProbeError("asset code does not match the native source request")

    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise CloudDailyProbeError("daily dates are missing, duplicated or unordered")
    actual_dates = dates.dt.date
    # A self-consistent current last bar cannot legitimize intermediary holiday
    # rows. Check every returned date against the actual exchange calendar.
    try:
        import exchange_calendars as xcals
        calendar = xcals.get_calendar("XSHG")
        invalid_date = any(not bool(calendar.is_session(day)) for day in actual_dates)
    except Exception as exc:
        raise CloudDailyProbeError("exchange calendar could not verify every source row") from exc
    if invalid_date:
        raise CloudDailyProbeError("daily source includes a non-session bar")
    if actual_dates.iloc[-1] != target or (actual_dates > target).any():
        raise CloudDailyProbeError("provider last bar differs from latest completed session")
    for field in ("open", "high", "low", "close", "volume"):
        values = pd.to_numeric(frame[field], errors="coerce")
        if values.isna().any() or not all(math.isfinite(float(value)) for value in values):
            raise CloudDailyProbeError("daily OHLCV includes missing or nonfinite numbers")
    prices = frame[["open", "high", "low", "close"]].apply(pd.to_numeric)
    if (
        (prices <= 0).any().any()
        or (prices["high"] < prices[["open", "low", "close"]].max(axis=1)).any()
        or (prices["low"] > prices[["open", "high", "close"]].min(axis=1)).any()
    ):
        raise CloudDailyProbeError("daily OHLC envelope is invalid")
    if (pd.to_numeric(frame["volume"]) < 0).any():
        raise CloudDailyProbeError("daily volume is negative")

    amount_state = "MISSING"
    if "amount" in frame.columns:
        actual_amount = frame["amount"]
        if not actual_amount.isna().all():
            amounts = pd.to_numeric(actual_amount, errors="coerce")
            if (
                actual_amount.isna().any()
                or amounts.isna().any()
                or not all(math.isfinite(float(value)) for value in amounts)
                or (amounts < 0).any()
            ):
                raise CloudDailyProbeError("daily amount is partially missing or invalid")
            amount_state = "READY"

    return {
        "status": "OBSERVED_LATEST_DAILY_READONLY",
        "code": str(code),
        "source_name": source_name,
        "latest_session": target.isoformat(),
        "returned_start": actual_dates.iloc[0].isoformat(),
        "row_count": len(frame),
        "ohlcv_state": "READY",
        "amount_state": amount_state,
        # Field presence is not independent method, storage or Product admission.
        "amount_dependent_methods_ready": False,
        "identity_state": identity["identity_state"],
        "actual_response_branch": identity["actual_response_branch"],
        "observed_adjustment_basis": identity["observed_adjustment_basis"],
        "observed_at": identity["observed_at"],
        "content_sha256": identity["content_sha256"],
        "identity_sha256": identity["identity_hash"],
        "raw_response_sha256_present": bool(identity.get("raw_response_sha256")),
        "rights_status": "UNKNOWN",
        "pit_eligible": False,
        "training_eligible": False,
        "notification_suppressed": True,
        "persistence": "NONE",
        "live_product_admitted": False,
    }


def main() -> int:
    try:
        enforce_zero_proxy()
        from src.core.trading_calendar import resolve_latest_completed_session_fail_closed

        now = datetime.now(timezone.utc)
        session = resolve_latest_completed_session_fail_closed("cn", current_time=now)
        if session is None:
            raise CloudDailyProbeError("exchange calendar could not prove latest completed session")
        frame = fetch_bound_tencent_daily(
            "600519",
            start_date=(session - timedelta(days=200)).isoformat(),
            end_date=session.isoformat(),
        )
        # Recheck after I/O. A session boundary crossed during a slow request
        # must never let a newly stale frame self-certify as latest.
        receipt = evaluate_daily_snapshot(
            frame, code="600519", source_name="TencentFetcher",
            now=datetime.now(timezone.utc),
        )
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        # Do not print raw provider responses, request URLs or exception text
        # to public CI logs. The failure family remains diagnosable.
        print(json.dumps({
            "status": "STOP",
            "failure_kind": type(exc).__name__,
            "rights_status": "UNKNOWN",
            "notification_suppressed": True,
            "persistence": "NONE",
            "live_product_admitted": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
