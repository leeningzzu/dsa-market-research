"""Bounded Eastmoney index 5m SOURCE adapter for existing DSA MarketReview.

Based on the documented AKShare index_zh_a_hist_min_em interface shape; it
does not import AKShare or start a second analysis/notification/storage stack.
Observed data and self-hashes do NOT grant data rights, historical PIT, native
indicator convergence, model features, or an independent regime/BUY vote.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from typing import Any

import pandas as pd

from data_provider.daily_data_identity import sha256_payload
from data_provider.intraday_data_identity import (
    IntradayDataIdentityError,
    attach_intraday_data_identity,
    build_intraday_data_identity,
    extract_intraday_data_identity,
)
from src.services.intraday_bar_service import (
    IntradayBarError,
    normalize_cn_completed_5m_bars,
    validate_complete_cn_5m_sessions,
)

_SCHEMA = "cn-index-completed-5m-source-v1"
_PROVIDER = "EastmoneyIndex5mAdapter"
_ROUTE = "eastmoney.index_zh_a_hist_min_em|push2his/qt/stock/kline/get"
_ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_ALLOWED = {"sh.000001": "1.000001", "sh.000300": "1.000300"}
_MAX_BYTES = 1_500_000


class IndexMinuteSourceError(ValueError):
    """Index minute source is unavailable or fails admission."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _source_urlopen(request: Request, *, timeout: int):
    """Exactly one HTTPS request; redirects cannot change the source origin."""
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def _exchange_session_dates(first: date, last: date) -> list[date]:
    import exchange_calendars as xcals

    sessions = xcals.get_calendar("XSHG").sessions_in_range(
        first.isoformat(), last.isoformat(),
    )
    return [s.date() for s in sessions]


def get_cn_index_completed_5m_source(
    index_code: str,
    *,
    target_date: date,
    observed_at: datetime | None = None,
) -> pd.DataFrame:
    """One read-only current observed index source, never historical as-of.

    No unbounded retries, fallback market symbols, scraping dependencies,
    business DB writes, source-value voting, or ineligible completed bars.
    """
    from src.core.trading_calendar import resolve_latest_completed_session_fail_closed

    if index_code not in _ALLOWED:
        raise IndexMinuteSourceError("INDEX_5M_UNSUPPORTED_EXACT_INSTRUMENT")
    if type(target_date) is not date:
        raise IndexMinuteSourceError("INDEX_5M_TARGET_DATE_INVALID")
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise IndexMinuteSourceError("INDEX_5M_OBSERVED_AT_MUST_BE_AWARE")
    latest = resolve_latest_completed_session_fail_closed(
        "cn", current_time=observed,
    )
    if latest is None or target_date > latest:
        raise IndexMinuteSourceError("INDEX_5M_FUTURE_OR_INCOMPLETE_SESSION")

    params = {
        "secid": _ALLOWED[index_code],
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "5",
        "fqt": "0",
        "beg": "0",
        "end": "20500000",
    }
    request_url = _ENDPOINT + "?" + urlencode(params)
    parsed = urlsplit(request_url)
    if parsed.scheme != "https" or parsed.hostname != "push2his.eastmoney.com":
        raise IndexMinuteSourceError("INDEX_5M_PROVIDER_ROUTE_INVALID")
    req = Request(
        request_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; DSA-research/1.0)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with _source_urlopen(req, timeout=20) as response:
            raw = response.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise IndexMinuteSourceError("INDEX_5M_RESPONSE_EXCEEDS_BOUNDED_SIZE")
        payload = json.loads(raw.decode("utf-8-sig"))
        info = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("rc") != 0
            or not isinstance(info, dict)
            or str(info.get("code")) != index_code.split(".")[1]
            or str(info.get("market")) != "1"
            or not isinstance(info.get("klines"), list)
            or not info["klines"]
        ):
            raise IndexMinuteSourceError("INDEX_5M_RESPONSE_CODE_OR_SCHEMA_MISMATCH")
        rows = [str(value).split(",") for value in info["klines"]]
        if any(len(row) != 11 for row in rows):
            raise IndexMinuteSourceError("INDEX_5M_RESPONSE_FIELD_DRIFT")
        source = pd.DataFrame(rows, columns=[
            "bar_end_text", "open", "close", "high", "low", "volume",
            "amount", "amplitude", "pct_chg", "change", "turnover",
        ])
        timestamps = pd.to_datetime(
            source["bar_end_text"], format="%Y-%m-%d %H:%M", errors="coerce",
        )
        if timestamps.isna().any() or timestamps.duplicated().any():
            raise IndexMinuteSourceError("INDEX_5M_INVALID_OR_DUPLICATE_TIMESTAMPS")
        source_dates = [x.date() for x in timestamps]
        if min(source_dates) > target_date or max(source_dates) > target_date:
            raise IndexMinuteSourceError("INDEX_5M_RESPONSE_FUTURE_OR_MISSING_TARGET")
        if source_dates[-1] != target_date:
            raise IndexMinuteSourceError("INDEX_5M_TARGET_SESSION_ABSENT")
        if source_dates != sorted(source_dates):
            raise IndexMinuteSourceError("INDEX_5M_TIME_ORDER_INVALID")
        expected_days = _exchange_session_dates(source_dates[0], target_date)
        if sorted(set(source_dates)) != expected_days:
            raise IndexMinuteSourceError("INDEX_5M_EXCHANGE_SESSION_GAP")
        canonical_input = pd.DataFrame({
            "date": [t.date().isoformat() for t in timestamps],
            "time": [t.strftime("%Y%m%d%H%M%S") for t in timestamps],
            "open": source["open"], "high": source["high"],
            "low": source["low"], "close": source["close"],
            "volume": source["volume"], "amount": source["amount"],
        })
        canonical = normalize_cn_completed_5m_bars(
            canonical_input, stock_code=index_code, data_source=_PROVIDER,
            observed_at=observed,
        )
        if len(canonical) != len(rows):
            raise IndexMinuteSourceError("INDEX_5M_COMPLETED_BARS_NOT_YET_AVAILABLE")
        source_digest = sha256_payload(info["klines"])
        identity = build_intraday_data_identity(
            canonical,
            provider_identity=_PROVIDER,
            provider_route=_ROUTE,
            package_version="no-external-python-dependency",
            query_identity={
                "secid": _ALLOWED[index_code], "frequency": "5",
                "fqt_requested": "0",
            },
            requested_adjustment_basis="raw",
            observed_adjustment_basis=None,
            basis_evidence="QUERY_FQT_0_NO_INDEPENDENT_RESPONSE_BASIS_ATTESTATION",
            timeframe="5m", timezone_name="Asia/Shanghai",
            session_calendar="XSHG", currency="UNKNOWN",
            volume_unit="UNKNOWN", amount_unit="UNKNOWN",
            requested_start=None, requested_end=target_date.isoformat(),
            identity_state="UNCLASSIFIED", observed_at=observed,
            source_rows_sha256=source_digest,
        )
        attach_intraday_data_identity(canonical, identity)
        canonical["intraday_data_identity_hash"] = identity["identity_hash"]
        verified_sessions = validate_complete_cn_5m_sessions(
            canonical, expected_session_dates=expected_days,
        )
        receipt = {
            "schema_version": _SCHEMA,
            "dataset_id": "M_INDEX_BARS",
            "source_code": index_code,
            "source_state": "PARTIAL",
            "provider_identity": _PROVIDER,
            "provider_route": _ROUTE,
            "completed_through": target_date.isoformat(),
            "observed_at": identity["observed_at"],
            "identity_hash": identity["identity_hash"],
            "content_sha256": identity["content_sha256"],
            "session_count": verified_sessions["session_count"],
            "row_count": int(len(canonical)),
            "source_data_rights": "UNKNOWN",
            "volume_unit": "UNKNOWN",
            "amount_unit": "UNKNOWN",
            "historical_pit_eligible": False,
            "independent_source_value_admitted": False,
            "native_recursive_stability_admitted": False,
            "strategy_upgrade_authority": False,
        }
        # Tamper-evident transfer across MarketReview/Context/Registry;
        # a self-hash does not by itself attest Eastmoney authenticity.
        receipt["receipt_sha256"] = sha256_payload(receipt)
        canonical.attrs["cn_index_completed_5m_source"] = dict(receipt)
        return canonical
    except IndexMinuteSourceError:
        raise
    except (ValueError, TypeError, KeyError, IntradayBarError,
            IntradayDataIdentityError, OSError) as exc:
        raise IndexMinuteSourceError("INDEX_5M_PROVIDER_OR_VALIDATION_FAILURE") from exc


def verify_cn_index_completed_5m_source_frame(
    frame: pd.DataFrame,
    *,
    trade_date: str,
) -> dict[str, Any]:
    """Verify source bytes and exact completed sessions; return NO price data."""
    from src.core.trading_calendar import resolve_latest_completed_session_fail_closed

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise IndexMinuteSourceError("INDEX_5M_SOURCE_FRAME_MISSING")
    receipt = frame.attrs.get("cn_index_completed_5m_source")
    if not isinstance(receipt, dict):
        raise IndexMinuteSourceError("INDEX_5M_SOURCE_RECEIPT_MISSING")
    expected = {
        "schema_version": _SCHEMA, "dataset_id": "M_INDEX_BARS",
        "source_state": "PARTIAL", "provider_identity": _PROVIDER,
        "provider_route": _ROUTE, "completed_through": trade_date,
        "source_data_rights": "UNKNOWN", "volume_unit": "UNKNOWN",
        "amount_unit": "UNKNOWN", "historical_pit_eligible": False,
        "independent_source_value_admitted": False,
        "native_recursive_stability_admitted": False,
        "strategy_upgrade_authority": False,
    }
    if any(receipt.get(k) != v for k, v in expected.items()):
        raise IndexMinuteSourceError("INDEX_5M_SOURCE_RECEIPT_NOT_ADMITTED")
    if set(receipt) != set(expected) | {
        "source_code", "observed_at", "identity_hash",
        "content_sha256", "session_count", "row_count", "receipt_sha256",
    }:
        raise IndexMinuteSourceError("INDEX_5M_SOURCE_RECEIPT_EXTRA_OR_MISSING_FIELD")
    if sha256_payload({k: v for k, v in receipt.items() if k != "receipt_sha256"}) != receipt.get("receipt_sha256"):
        raise IndexMinuteSourceError("INDEX_5M_SOURCE_RECEIPT_CONTENT_CHANGED")
    if receipt.get("source_code") not in _ALLOWED:
        raise IndexMinuteSourceError("INDEX_5M_SOURCE_CODE_UNEXPECTED")
    try:
        identity = extract_intraday_data_identity(frame, strict=True)
        if (
            identity is None
            or identity["provider_identity"] != _PROVIDER
            or identity["identity_state"] != "UNCLASSIFIED"
            or identity["requested_adjustment_basis"] != "raw"
            or identity["observed_adjustment_basis"] is not None
            or identity["query_identity"].get("secid") != _ALLOWED[receipt["source_code"]]
            or frame["code"].astype(str).ne(receipt["source_code"]).any()
            or receipt["row_count"] != len(frame)
            or receipt["identity_hash"] != identity["identity_hash"]
            or receipt["content_sha256"] != identity["content_sha256"]
            or receipt["observed_at"] != identity["observed_at"]
            or identity["volume_unit"] != "UNKNOWN"
            or identity["amount_unit"] != "UNKNOWN"
        ):
            raise IndexMinuteSourceError("INDEX_5M_SOURCE_FRAME_IDENTITY_DRIFT")
        observed = datetime.fromisoformat(receipt["observed_at"])
        if (
            observed.tzinfo is None
            or observed.utcoffset() is None
            or resolve_latest_completed_session_fail_closed("cn", current_time=observed)
            < date.fromisoformat(trade_date)
        ):
            raise IndexMinuteSourceError("INDEX_5M_SOURCE_HISTORICAL_CLOCK_UNPROVEN")
        timestamps = pd.to_datetime(frame["bar_end"], errors="raise")
        days = [t.tz_convert("Asia/Shanghai").date() for t in timestamps]
        if not days or max(days).isoformat() != trade_date:
            raise IndexMinuteSourceError("INDEX_5M_SOURCE_TARGET_DATE_MISMATCH")
        expected_days = _exchange_session_dates(min(days), max(days))
        checked = validate_complete_cn_5m_sessions(
            frame, expected_session_dates=expected_days,
        )
        if checked["session_count"] != receipt["session_count"]:
            raise IndexMinuteSourceError("INDEX_5M_SOURCE_SESSION_COUNT_MISMATCH")
    except (ValueError, TypeError, IntradayDataIdentityError,
            IntradayBarError, KeyError) as exc:
        raise IndexMinuteSourceError("INDEX_5M_SOURCE_FRAME_UNVERIFIED") from exc
    return dict(receipt)
