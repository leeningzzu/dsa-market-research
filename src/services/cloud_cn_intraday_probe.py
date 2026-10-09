"""One-stock zero-proxy cloud diagnostic of canonical completed BaoStock 5m.

The existing BaoStockFetcher/XSHG/IntradayBarService own the data and method
semantics. This probe never admits storage rights, historical PIT, amount-based
methods, strategy eligibility, scheduled notifications or training.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from typing import Any

import exchange_calendars as xcals

from data_provider.intraday_data_identity import (
    IntradayDataIdentityError,
    extract_intraday_data_identity,
)
from src.core.trading_calendar import (
    resolve_forward_sessions_fail_closed,
    resolve_latest_completed_session_fail_closed,
)
from src.services.cloud_cn_daily_probe import enforce_zero_proxy
from src.services.intraday_bar_service import (
    IntradayBarError,
    aggregate_completed_intraday_bars,
    validate_complete_cn_5m_sessions,
)


class CloudIntradayProbeError(ValueError):
    """Fail-closed read-only probe, not an investment decision or permission grant."""


_PROBE_STOCK = "600519"
_MAX_DIAGNOSTIC_SESSIONS = 30  # bounded provider trial, NOT a method/window ceiling


def _expected_sessions(now: datetime) -> tuple[list[date], dict[str, Any]]:
    """Derive the current accepted method warm-up from its real DSA owner."""
    from src.core.pipeline import _current_intraday_window_plan

    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise CloudIntradayProbeError("aware observation time required")
    target = resolve_latest_completed_session_fail_closed("cn", current_time=now)
    if target is None:
        raise CloudIntradayProbeError("latest completed exchange session not proved")
    plan = _current_intraday_window_plan()
    count = int(plan["required_sessions"])
    if count < 1 or count > _MAX_DIAGNOSTIC_SESSIONS:
        raise CloudIntradayProbeError("method warmup is outside the bounded diagnostic budget")
    try:
        calendar = xcals.get_calendar("XSHG")
        end = calendar.date_to_session(target)
        dates = [item.date() for item in calendar.sessions_window(end, -count)]
    except Exception as exc:
        raise CloudIntradayProbeError("XSHG calendar could not derive minute history") from exc
    if len(dates) != count or dates[-1] != target:
        raise CloudIntradayProbeError("XSHG session planning is inconsistent")
    confirmed = resolve_forward_sessions_fail_closed("cn", dates[0] - timedelta(days=1), count)
    if confirmed != dates:
        raise CloudIntradayProbeError("independent completed-session comparison failed")
    return dates, plan


def evaluate_intraday_snapshot(
    frame: Any,
    *,
    code: str,
    expected_sessions: list[date],
    now: datetime,
) -> dict[str, Any]:
    """Validate one full true session set, without granting Product or PIT."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise CloudIntradayProbeError("aware observation time required")
    if not expected_sessions or expected_sessions[-1] != resolve_latest_completed_session_fail_closed(
        "cn", current_time=now,
    ):
        raise CloudIntradayProbeError("requested session is no longer latest completed")
    try:
        identity = extract_intraday_data_identity(frame, strict=True)
    except (IntradayDataIdentityError, ValueError, TypeError) as exc:
        raise CloudIntradayProbeError("native 5m identity failed verification") from exc
    if identity is None:
        raise CloudIntradayProbeError("native 5m source identity is missing")
    query = identity.get("query_identity") or {}
    if (
        identity.get("provider_identity") != "BaostockFetcher"
        or identity.get("provider_route") != "baostock.query_history_k_data_plus"
        or identity.get("identity_state") != "OBSERVED"
        or identity.get("timeframe") != "5m"
        or identity.get("observed_adjustment_basis") != "qfq"
        or identity.get("requested_adjustment_basis") != "qfq"
        or identity.get("session_calendar") != "XSHG"
        or identity.get("timezone") != "Asia/Shanghai"
        or identity.get("currency") != "CNY"
        or query.get("code") != f"sh.{code}"
        or str(query.get("frequency")) != "5"
        or str(query.get("adjustflag")) != "2"
        or identity.get("requested_start") != expected_sessions[0].isoformat()
        or identity.get("requested_end") != expected_sessions[-1].isoformat()
    ):
        raise CloudIntradayProbeError("asset/source/basis/session request identity mismatch")
    # A typed request label alone is not proof that the returned 5m rows
    # belong to the requested stock. Bind the actual payload at this consumer.
    if "code" not in frame.columns or frame.empty or (
        frame["code"].astype(str).str.strip() != str(code)
    ).any():
        raise CloudIntradayProbeError("actual 5m bar asset code differs from requested stock")
    try:
        observed = datetime.fromisoformat(str(identity["observed_at"]).replace("Z", "+00:00"))
        available = datetime.fromisoformat(str(identity["available_at_max"]).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CloudIntradayProbeError("source observation or availability time invalid") from exc
    if (
        observed.tzinfo is None or available.tzinfo is None
        or observed > now or available > now
    ):
        raise CloudIntradayProbeError("source observation is missing or in the future")
    try:
        source_receipt = validate_complete_cn_5m_sessions(
            frame, expected_session_dates=expected_sessions,
        )
        derived = {
            freq: aggregate_completed_intraday_bars(frame, target_timeframe=freq)
            for freq in ("15m", "30m", "60m")
        }
        for freq, output in derived.items():
            child = extract_intraday_data_identity(output, strict=True)
            if child is None or child.get("parent_identity_hash") != identity["identity_hash"]:
                raise CloudIntradayProbeError("aggregated bar lineage does not match source")
            agg = output.attrs.get("intraday_aggregation_receipt") or {}
            if int(agg.get("dropped_incomplete_windows", -1)) != 0:
                raise CloudIntradayProbeError("aggregation silently dropped incomplete bars")
    except (IntradayBarError, IntradayDataIdentityError, TypeError, ValueError) as exc:
        raise CloudIntradayProbeError("native session/aggregation completeness failed") from exc
    count = len(expected_sessions)
    counts = {"5m": len(frame), **{freq: len(x) for freq, x in derived.items()}}
    required = {"5m": 48 * count, "15m": 16 * count, "30m": 8 * count, "60m": 4 * count}
    if counts != required or int(source_receipt["row_count"]) != counts["5m"]:
        raise CloudIntradayProbeError("completed sessions do not meet method window cardinalities")
    return {
        "status": "OBSERVED_LATEST_INTRADAY_READONLY",
        "code": code,
        "source_name": "BaostockFetcher",
        "latest_session": expected_sessions[-1].isoformat(),
        "returned_start": expected_sessions[0].isoformat(),
        "session_count": count,
        "timeframe_row_counts": counts,
        "ohlcv_state": "READY",
        "source_identity_state": identity["identity_state"],
        "adjustment_basis": identity["observed_adjustment_basis"],
        "source_identity_sha256": identity["identity_hash"],
        "source_rows_sha256_present": bool(identity.get("source_rows_sha256")),
        "raw_response_packet_sha256_present": False,
        "volume_unit": identity["volume_unit"],
        "amount_unit": identity["amount_unit"],
        "amount_dependent_methods_ready": False,
        "adjustment_vintage": "UNKNOWN",
        "rights_status": "UNKNOWN",
        "pit_eligible": False,
        "training_eligible": False,
        "notification_suppressed": True,
        "persistence": "NONE",
        "live_product_admitted": False,
    }


def main() -> int:
    stage = "PRECHECK"
    try:
        enforce_zero_proxy()
        start_now = datetime.now(timezone.utc)
        sessions, plan = _expected_sessions(start_now)
        stage = "FETCH_5M"
        from data_provider.baostock_fetcher import BaostockFetcher
        five_minute = BaostockFetcher().get_intraday_data(
            _PROBE_STOCK,
            start_date=sessions[0].isoformat(),
            end_date=sessions[-1].isoformat(),
            frequency="5",
        )
        stage = "VALIDATE_5M"
        receipt = evaluate_intraday_snapshot(
            five_minute, code=_PROBE_STOCK, expected_sessions=sessions,
            now=datetime.now(timezone.utc),
        )
        receipt["method_required_bars"] = int(plan["required_bars"])
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        # No error text, raw quotes, provider packets, endpoint credentials,
        # prices, or market-data bytes are printed into the public CI log.
        print(json.dumps({
            "status": "STOP",
            "stage": stage,
            "failure_kind": type(exc).__name__,
            "rights_status": "UNKNOWN",
            "notification_suppressed": True,
            "persistence": "NONE",
            "live_product_admitted": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
