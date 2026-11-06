"""Official CSI daily index performance -> existing DSA Sector RS source seam.

No network, persistent data, ranking, strategy or second RS algorithm. Parse
the original two CSI JSON response bytes INSIDE this producer; exact full
completed CN sessions, provider/benchmark/date/code checks precede the
original DSA compute_relative_strength_context (20 completed sessions).

Method-level 20d RS READY != Product/B1/7TF READY. All outputs source-only
PARTIAL with unknown independent publication vintage/rights and no PIT.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit, parse_qs

import pandas as pd

from data_provider.daily_data_identity import sha256_payload

_INDEX_PERF = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
_HORIZON = 20
_PROVIDER = "CSIOfficialIndexPerformance"
_PRODUCER = (
    "data_provider.csi_sector_daily_rs_source."
    "compile_csi_sector_daily_rs_source_observation"
)
_REASONS = [
    "CSI_DAILY_SECTOR_INDEX_BENCHMARK_SOURCE_ONLY",
    "TWENTY_COMPLETED_DAILY_SESSIONS_NOT_SEVEN_TIMEFRAMES",
    "INDEX_SUBSET_NOT_A_FULL_INDUSTRY_UNIVERSE",
    "PUBLISHER_RELEASE_REVISION_AND_SOURCE_RIGHTS_UNVERIFIED",
    "NO_HISTORICAL_PIT_STORAGE_LEARNING_OR_STRATEGY_AUTHORITY",
]
_FACT_KEYS = {
    "sector_index_code", "benchmark_index_code", "sector_index_name",
    "benchmark_index_name", "window_start_date", "window_end_date",
    "horizon_sessions", "full_source_session_count", "daily_sector_return_pct",
    "daily_benchmark_return_pct", "daily_excess_return_pct_points",
    "daily_relative_ratio_change_pct", "daily_relative_state", "method_status",
    "seven_timeframes_ready", "first_published_at", "sector_source_sha256",
    "benchmark_source_sha256", "sector_start_close", "sector_end_close",
    "benchmark_start_close", "benchmark_end_close",
}
_REQUIRED_SOURCE_IDS = {
    "dataset_id": "S_PRICES_RS",
    "state": "PARTIAL",
    "provider_identity": _PROVIDER,
    "provider_route": _INDEX_PERF,
    "producer_authority": _PRODUCER,
    "revision": "UNKNOWN",
    "vintage": "UNKNOWN",
    "adjustment_basis": "UNKNOWN",
    "currency": "NOT_APPLICABLE",
    "historical_pit_eligible": False,
    "independent_source_value_admitted": True,
    "strategy_upgrade_authority": False,
}


class CSISectorDailyRSError(ValueError):
    """Attempted source/clock/price/relative-strength authority mismatch."""


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _source_url(url: str, *, code: str, target: date) -> str:
    parts = urlsplit(str(url or ""))
    if (
        parts.scheme != "https" or parts.netloc != "www.csindex.com.cn"
        or parts.path != "/csindex-home/perf/index-perf"
        or parts.fragment
    ):
        raise CSISectorDailyRSError("CSI_SECTOR_RS_SOURCE_URL_INVALID")
    try:
        query = parse_qs(parts.query,strict_parsing=True,keep_blank_values=True)
    except ValueError as exc:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_SOURCE_QUERY_INVALID") from exc
    if (
        set(query) != {"indexCode", "startDate", "endDate"}
        or query["indexCode"] != [code]
        or query["endDate"] != [target.strftime("%Y%m%d")]
        or len(query["startDate"]) != 1
        or re.fullmatch("[0-9]{8}", query["startDate"][0]) is None
    ):
        raise CSISectorDailyRSError("CSI_SECTOR_RS_SOURCE_QUERY_INVALID")
    try:
        start = datetime.strptime(query["startDate"][0], "%Y%m%d").date()
    except ValueError as exc:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_SOURCE_DATE_INVALID") from exc
    if start >= target:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_SOURCE_DATE_INVALID")
    return query["startDate"][0]


def _positive(value: Any) -> float:
    if isinstance(value, bool):
        raise CSISectorDailyRSError("CSI_SECTOR_RS_PRICE_INVALID")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError
        converted = float(number)
        if not math.isfinite(converted):
            raise ValueError("non-finite floating conversion")
        return converted
    except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_PRICE_INVALID") from exc


def _parse_raw(
    response: bytes, *, code: str, name: str, start: str, target: date,
) -> tuple[pd.DataFrame, str]:
    from src.core.trading_calendar import resolve_session_range_fail_closed

    if not isinstance(response, bytes) or not 10 <= len(response) <= 600000:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_RAW_RESPONSE_INVALID")
    def nonfinite(x):
        raise ValueError("nonfinite JSON")
    try:
        document=json.loads(response.decode("utf-8"),parse_constant=nonfinite)
    except (UnicodeDecodeError,ValueError) as exc:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_JSON_INVALID") from exc
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise CSISectorDailyRSError("CSI_SECTOR_RS_RESPONSE_SCHEMA_INVALID")
    rows=document["data"]
    if len(rows)<_HORIZON+1 or len(rows)>300:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_WARMUP_OR_LENGTH_INVALID")
    dates=[]
    closes=[]
    for row in rows:
        if not isinstance(row, dict) or row.get("indexCode")!=code or row.get("indexNameCn")!=name:
            raise CSISectorDailyRSError("CSI_SECTOR_RS_INDEX_IDENTITY_INVALID")
        text=str(row.get("tradeDate") or "")
        if not re.fullmatch("[0-9]{8}",text):
            raise CSISectorDailyRSError("CSI_SECTOR_RS_DATE_INVALID")
        try:
            dt=datetime.strptime(text,"%Y%m%d").date()
        except ValueError as exc:
            raise CSISectorDailyRSError("CSI_SECTOR_RS_DATE_INVALID") from exc
        o,h,l,c=[_positive(row.get(key)) for key in ("open","high","low","close")]
        if h < max(o,c) or l > min(o,c) or l > h:
            raise CSISectorDailyRSError("CSI_SECTOR_RS_OHLC_INVALID")
        dates.append(dt)
        closes.append(c)
    if dates[0].strftime("%Y%m%d")!=start or dates[-1]!=target:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_REQUEST_RESPONSE_DATE_MISMATCH")
    sessions=resolve_session_range_fail_closed("cn",dates[0],target)
    if not sessions or dates!=sessions:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_EXACT_SESSION_GRID_MISMATCH")
    frame=pd.DataFrame({"date":dates,"close":closes,"data_source":_PROVIDER})
    return frame,sha256(response).hexdigest()


def _identity(row: dict) -> str:
    facts=row["facts"]
    return sha256_payload({
        "dataset_id":"S_PRICES_RS",
        "provider_route":_INDEX_PERF,
        "sector_source_sha256":facts["sector_source_sha256"],
        "benchmark_source_sha256":facts["benchmark_source_sha256"],
        "target":facts["window_end_date"],
        "start":facts["window_start_date"],
        "observed_at":row["source_observed_at"],
        "horizon_sessions":_HORIZON,
    })


def compile_csi_sector_daily_rs_source_observation(
    sector_response_json: bytes,
    benchmark_response_json: bytes,
    *,
    sector_request_url: str,
    benchmark_request_url: str,
    observed_at: datetime,
    research_as_of: datetime,
) -> dict:
    """Bound two official raw JSON responses to original DSA 20-session RS."""
    from src.core.trading_calendar import resolve_latest_completed_session_fail_closed
    from src.services.relative_strength_service import compute_relative_strength_context

    observed=_timestamp(observed_at)
    asof=_timestamp(research_as_of)
    if observed>asof:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_SOURCE_AFTER_ASOF")
    target=resolve_latest_completed_session_fail_closed("cn",current_time=observed)
    if target is None:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_COMPLETED_CLOCK_UNAVAILABLE")
    sector_start=_source_url(sector_request_url,code="000032",target=target)
    benchmark_start=_source_url(benchmark_request_url,code="000001",target=target)
    if sector_start!=benchmark_start:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_REQUEST_START_MISMATCH")
    sector,sect_sha=_parse_raw(
        sector_response_json,code="000032",name="上证能源",start=sector_start,target=target
    )
    bench,bench_sha=_parse_raw(
        benchmark_response_json,code="000001",name="上证指数",start=benchmark_start,target=target
    )
    if not sector["date"].equals(bench["date"]):
        raise CSISectorDailyRSError("CSI_SECTOR_RS_SHARED_SESSION_GRID_MISMATCH")
    result=compute_relative_strength_context(
        stock_code="sh.000032",market="cn",
        stock_history=sector,benchmark_history=bench,
        benchmark={"code":"sh.000001","name":"SSE Composite Index","source":_PROVIDER},
        target_date=target,horizon_sessions=_HORIZON,require_source_alignment=True,
    )
    if result["status"]!="READY" or result["data_quality"]["source_alignment"]!="MATCHED":
        raise CSISectorDailyRSError("CSI_SECTOR_RS_NATIVE_DSA_METHOD_UNAVAILABLE")
    selected_sector=sector.tail(_HORIZON+1)
    selected_bench=bench.tail(_HORIZON+1)
    facts={
        "sector_index_code":"000032",
        "benchmark_index_code":"000001",
        "sector_index_name":"上证能源",
        "benchmark_index_name":"上证指数",
        "window_start_date":result["stock"]["start_date"],
        "window_end_date":result["stock"]["end_date"],
        "horizon_sessions":_HORIZON,
        "full_source_session_count":len(sector),
        "daily_sector_return_pct":result["stock"]["return_pct"],
        "daily_benchmark_return_pct":result["benchmark"]["return_pct"],
        "daily_excess_return_pct_points":result["relative"]["excess_return_pct_points"],
        "daily_relative_ratio_change_pct":result["relative"]["relative_ratio_change_pct"],
        "daily_relative_state":result["relative"]["state"],
        "method_status":result["status"],
        "seven_timeframes_ready":False,
        "first_published_at":"UNKNOWN",
        "sector_source_sha256":sect_sha,
        "benchmark_source_sha256":bench_sha,
        "sector_start_close":float(selected_sector.iloc[0]["close"]),
        "sector_end_close":float(selected_sector.iloc[-1]["close"]),
        "benchmark_start_close":float(selected_bench.iloc[0]["close"]),
        "benchmark_end_close":float(selected_bench.iloc[-1]["close"]),
    }
    body={
        "schema_version":"data-object-source-observation-v1",
        "dataset_id":"S_PRICES_RS",
        "state":"PARTIAL",
        "reason_codes":list(_REASONS),
        "provider_identity":_PROVIDER,
        "provider_route":_INDEX_PERF,
        "producer_authority":_PRODUCER,
        "source_identity_hash":"",
        "source_content_sha256":sha256_payload({
            "sector":sect_sha,"benchmark":bench_sha
        }),
        "source_observed_at":observed.isoformat(),
        "source_available_at":observed.isoformat(),
        "completed_through":target.isoformat(),
        "units":{"price":"INDEX_POINTS","return":"PERCENT","excess":"PERCENTAGE_POINTS"},
        "currency":"NOT_APPLICABLE",
        "adjustment_basis":"UNKNOWN",
        "revision":"UNKNOWN",
        "vintage":"UNKNOWN",
        "historical_pit_eligible":False,
        "independent_source_value_admitted":True,
        "strategy_upgrade_authority":False,
        "facts":facts,
    }
    body["source_identity_hash"]=_identity(body)
    body["observation_hash"]=sha256_payload(body)
    return body


def validate_csi_sector_daily_rs_source_only_observation(row: Any) -> None:
    """Reject status/RS/rights/source identity jumps even if caller reseals."""
    if not isinstance(row,dict) or not isinstance(row.get("facts"),dict):
        raise CSISectorDailyRSError("CSI_SECTOR_RS_OBSERVATION_INVALID")
    f=row["facts"]
    if (
        any(row.get(k)!=v for k,v in _REQUIRED_SOURCE_IDS.items())
        or row.get("reason_codes")!=_REASONS
        or row.get("units")!={"price":"INDEX_POINTS","return":"PERCENT","excess":"PERCENTAGE_POINTS"}
        or set(f)!=_FACT_KEYS
        or f.get("sector_index_code")!="000032"
        or f.get("benchmark_index_code")!="000001"
        or f.get("sector_index_name")!="上证能源"
        or f.get("benchmark_index_name")!="上证指数"
        or f.get("method_status")!="READY"
        or f.get("seven_timeframes_ready") is not False
        or f.get("first_published_at")!="UNKNOWN"
        or f.get("daily_relative_state") not in {"OUTPERFORMING","UNDERPERFORMING","NEUTRAL"}
        or f.get("horizon_sessions")!=_HORIZON
        or type(f.get("full_source_session_count")) is not int
        or f["full_source_session_count"] < _HORIZON+1
        or any(not re.fullmatch("[0-9a-f]{64}",str(f.get(k) or "")) for k in
            ("sector_source_sha256","benchmark_source_sha256"))
        or row.get("source_content_sha256")!=sha256_payload({
            "sector":f["sector_source_sha256"],"benchmark":f["benchmark_source_sha256"]
        })
        or row.get("source_identity_hash")!=_identity(row)
        or row.get("source_available_at")!=row.get("source_observed_at")
        or row.get("completed_through")!=f.get("window_end_date")
    ):
        raise CSISectorDailyRSError("CSI_SECTOR_RS_SOURCE_ONLY_AUTHORITY_VETO")
    try:
        start=date.fromisoformat(str(f["window_start_date"]))
        end=date.fromisoformat(str(f["window_end_date"]))
        observed=_timestamp(datetime.fromisoformat(row["source_observed_at"]))
        if not start<end or end>observed.date():
            raise ValueError
        s0,s1,b0,b1=(_positive(f[k]) for k in (
            "sector_start_close","sector_end_close","benchmark_start_close","benchmark_end_close"
        ))
        ret=round((s1/s0-1.0)*100.0,6)
        ben=round((b1/b0-1.0)*100.0,6)
        exc=round(ret-ben,6)
        ratio=round(((s1/s0)/(b1/b0)-1.0)*100.0,6)
        for key,value in (
            ("daily_sector_return_pct",ret),
            ("daily_benchmark_return_pct",ben),
            ("daily_excess_return_pct_points",exc),
            ("daily_relative_ratio_change_pct",ratio),
        ):
            if round(float(f[key]),6)!=value:
                raise ValueError
        expected_state=("OUTPERFORMING" if ratio>1e-12 else
                        "UNDERPERFORMING" if ratio < -1e-12 else "NEUTRAL")
        if f["daily_relative_state"]!=expected_state:
            raise ValueError
    except (TypeError,ValueError,KeyError) as exc:
        raise CSISectorDailyRSError("CSI_SECTOR_RS_SOURCE_VALUE_VETO") from exc
