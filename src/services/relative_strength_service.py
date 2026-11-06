# -*- coding: utf-8 -*-
"""Deterministic completed-bar relative-strength evidence.

The service deliberately reuses DSA's existing daily-data route.  It does not
own screening, ranking, persistence, or action authority.  For the first CN
stock slice, 510300 is used explicitly as an ETF proxy for CSI 300; it is never
represented as raw index history.
"""

from __future__ import annotations

import logging
import threading
import hashlib
import json
from datetime import date
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

from data_provider.daily_data_identity import bind_verified_daily_source_view

from src.services.pit_identity import build_completed_history_identity

logger = logging.getLogger(__name__)

RELATIVE_STRENGTH_SCHEMA_VERSION = "relative-strength-v1"
DEFAULT_HORIZON_SESSIONS = 60
BENCHMARK_FETCH_DAYS = 120
ETF_PEER_RELATIVE_STRENGTH_SCHEMA_VERSION = "etf-peer-relative-strength-v1"
DEFAULT_MIN_ETF_PEERS = 3
CN_BENCHMARK_PROXY: Dict[str, Any] = {
    "code": "510300",
    "name": "华泰柏瑞沪深300ETF",
    "kind": "etf_proxy",
    "tracks": "CSI300",
}


def _status_payload(
    *,
    status: str,
    market: str,
    stock_code: str,
    reason: str,
    benchmark: Optional[Mapping[str, Any]] = None,
    horizon_sessions: int = DEFAULT_HORIZON_SESSIONS,
) -> Dict[str, Any]:
    return {
        "schema_version": RELATIVE_STRENGTH_SCHEMA_VERSION,
        "family": "trend_relative_strength",
        "status": status,
        "market": market,
        "stock_code": stock_code,
        "horizon_sessions": horizon_sessions,
        "return_basis": "provider_price_return_proxy",
        "reason": reason,
        "benchmark": dict(benchmark or {}),
        "stock": {},
        "relative": {},
        "data_quality": {},
    }


def _normalize_history(history: Any, *, target_date: date) -> pd.DataFrame:
    if not isinstance(history, pd.DataFrame) or history.empty:
        return pd.DataFrame(columns=["date", "close"])

    frame = history.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    if "date" not in frame.columns or "close" not in frame.columns:
        return pd.DataFrame(columns=["date", "close"])

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"])
    frame = frame[(frame["close"] > 0) & (frame["date"] <= target_date)]
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    columns = ["date", "close"]
    if "data_source" in frame.columns:
        columns.append("data_source")
    result = frame[columns].reset_index(drop=True)
    return bind_verified_daily_source_view(history, result)


TIMEFRAME_RELATIVE_STRENGTH_SCHEMA_VERSION = "timeframe-relative-strength-v1"


def _normalize_completed_timeframe_bars(
    bars: Any,
    *,
    target_bar_end: Any,
) -> pd.DataFrame:
    """Normalize one caller-supplied completed-bar carrier without fetching."""
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return pd.DataFrame(columns=["bar_end", "close"])
    frame = bars.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    endpoint = "bar_end" if "bar_end" in frame.columns else "date" if "date" in frame.columns else None
    if endpoint is None or "close" not in frame.columns:
        return pd.DataFrame(columns=["bar_end", "close"])
    frame["bar_end"] = pd.to_datetime(frame[endpoint], errors="coerce", utc=True)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["bar_end", "close"])
    frame = frame[frame["close"] > 0]
    target = pd.to_datetime(target_bar_end, errors="coerce", utc=True)
    if pd.isna(target):
        return pd.DataFrame(columns=["bar_end", "close"])
    frame = frame[frame["bar_end"] <= target]
    frame = frame.sort_values("bar_end").drop_duplicates(subset=["bar_end"], keep="last")
    return frame[["bar_end", "close"]].reset_index(drop=True)


def compute_completed_timeframe_relative_strength_context(
    *,
    target_code: str,
    benchmark_code: str,
    market: str,
    timeframe: str,
    target_bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
    target_bar_end: Any,
    target_input_identity: Mapping[str, Any],
    benchmark_input_identity: Mapping[str, Any],
    horizon_bars: int = 20,
) -> Dict[str, Any]:
    """Compare any two already-supplied completed-bar carriers at exact endpoints.

    This pure consumer does not fetch a benchmark, discover peers, grant data
    rights, or create action authority. Missing/invalid provenance remains an
    explicit result so every one of the seven timeframe cells is executable.
    """
    normalized_timeframe = str(timeframe or "").strip()
    normalized_market = str(market or "").strip().lower()
    target_code = str(target_code or "").strip()
    benchmark_code = str(benchmark_code or "").strip()

    def result(status: str, reason: str) -> Dict[str, Any]:
        return {
            "schema_version": TIMEFRAME_RELATIVE_STRENGTH_SCHEMA_VERSION,
            "family": "trend_relative_strength",
            "status": status,
            "reason": reason,
            "market": normalized_market or "unknown",
            "timeframe": normalized_timeframe or "unknown",
            "target_code": target_code,
            "target_bar_end": (
                pd.to_datetime(target_bar_end, errors="coerce", utc=True).isoformat()
                if not pd.isna(pd.to_datetime(target_bar_end, errors="coerce", utc=True))
                else None
            ),
            "horizon_bars": horizon_bars,
            "return_basis": "provider_completed_bar_price_return_proxy",
            "benchmark": {"code": benchmark_code},
            "target": {},
            "relative": {},
            "input_identity": {
                "target": dict(target_input_identity) if isinstance(target_input_identity, Mapping) else {},
                "benchmark": dict(benchmark_input_identity) if isinstance(benchmark_input_identity, Mapping) else {},
            },
            "data_quality": {},
            "independent_action_authority": False,
            "strategy_admitted": False,
            "learning_admitted": False,
            "data_rights_admitted": False,
        }

    if (
        normalized_timeframe not in {"monthly", "weekly", "daily", "60m", "30m", "15m", "5m"}
        or not target_code
        or not benchmark_code
        or not isinstance(horizon_bars, int)
        or horizon_bars <= 0
    ):
        return result("UNKNOWN", "TIMEFRAME_RS_INPUT_INVALID")

    target_identity = dict(target_input_identity) if isinstance(target_input_identity, Mapping) else {}
    benchmark_identity = dict(benchmark_input_identity) if isinstance(benchmark_input_identity, Mapping) else {}
    target_hash = str(
        target_identity.get("identity_hash")
        or target_identity.get("data_snapshot_identity")
        or ""
    )
    benchmark_hash = str(
        benchmark_identity.get("identity_hash")
        or benchmark_identity.get("data_snapshot_identity")
        or ""
    )
    target_provider = str(target_identity.get("provider_identity") or "").strip()
    benchmark_provider = str(benchmark_identity.get("provider_identity") or "").strip()
    target_basis = str(
        target_identity.get("adjustment_basis")
        or target_identity.get("observed_adjustment_basis")
        or ""
    ).strip()
    benchmark_basis = str(
        benchmark_identity.get("adjustment_basis")
        or benchmark_identity.get("observed_adjustment_basis")
        or ""
    ).strip()
    if len(target_hash) != 64 or len(benchmark_hash) != 64:
        return result("UNKNOWN", "TIMEFRAME_RS_INPUT_IDENTITY_MISSING")
    if (
        not target_provider
        or target_provider != benchmark_provider
        or not target_basis
        or target_basis != benchmark_basis
    ):
        return result("UNKNOWN", "TIMEFRAME_RS_SOURCE_OR_BASIS_MISMATCH")
    if any(
        str(identity.get("timeframe") or normalized_timeframe) != normalized_timeframe
        or identity.get("completed_bar_only") is not True
        for identity in (target_identity, benchmark_identity)
    ):
        return result("UNKNOWN", "TIMEFRAME_RS_IDENTITY_TIMEFRAME_OR_COMPLETION_MISMATCH")

    target = _normalize_completed_timeframe_bars(
        target_bars, target_bar_end=target_bar_end,
    )
    benchmark = _normalize_completed_timeframe_bars(
        benchmark_bars, target_bar_end=target_bar_end,
    )
    required = horizon_bars + 1
    if len(target) < required or len(benchmark) < required:
        return result("MISSING", "TIMEFRAME_RS_WARMUP_INSUFFICIENT")
    benchmark_window = benchmark.tail(required).reset_index(drop=True)
    start = benchmark_window.iloc[0]["bar_end"]
    end = benchmark_window.iloc[-1]["bar_end"]
    target_by_end = target.set_index("bar_end")["close"]
    if start not in target_by_end.index or end not in target_by_end.index:
        return result("MISSING", "TIMEFRAME_RS_SHARED_ENDPOINT_MISSING")
    # Twenty completed bars are twenty *aligned observations*, not simply
    # two matching endpoint prices. A missing middle target bar cannot count
    # toward the benchmark horizon or silently award READY.
    target_window = target.tail(required).reset_index(drop=True)
    if not target_window["bar_end"].equals(benchmark_window["bar_end"]):
        return result("MISSING", "TIMEFRAME_RS_FULL_COMPLETED_GRID_MISMATCH")
    target_start = float(target_by_end.loc[start])
    target_end = float(target_by_end.loc[end])
    benchmark_start = float(benchmark_window.iloc[0]["close"])
    benchmark_end = float(benchmark_window.iloc[-1]["close"])
    if min(target_start, target_end, benchmark_start, benchmark_end) <= 0:
        return result("UNKNOWN", "TIMEFRAME_RS_NON_POSITIVE_ENDPOINT_PRICE")

    target_return = (target_end / target_start - 1.0) * 100.0
    benchmark_return = (benchmark_end / benchmark_start - 1.0) * 100.0
    ratio_change = (
        (target_end / target_start) / (benchmark_end / benchmark_start) - 1.0
    ) * 100.0
    state = (
        "NEUTRAL" if abs(ratio_change) <= 1e-12
        else "OUTPERFORMING" if ratio_change > 0
        else "UNDERPERFORMING"
    )
    output = result("READY", "EXACT_SHARED_COMPLETED_BAR_ENDPOINTS_SOURCE_ALIGNED")
    output["benchmark"] = {
        "code": benchmark_code,
        "start_bar_end": start.isoformat(),
        "end_bar_end": end.isoformat(),
        "observations": required,
        "return_pct": round(benchmark_return, 6),
    }
    output["target"] = {
        "code": target_code,
        "start_bar_end": start.isoformat(),
        "end_bar_end": end.isoformat(),
        "observations": int(target[(target["bar_end"] >= start) & (target["bar_end"] <= end)].shape[0]),
        "return_pct": round(target_return, 6),
    }
    output["relative"] = {
        "state": state,
        "excess_return_pct_points": round(target_return - benchmark_return, 6),
        "relative_ratio_change_pct": round(ratio_change, 6),
    }
    output["data_quality"] = {
        "source_alignment": "MATCHED",
        "provider_identity": target_provider,
        "adjustment_basis": target_basis,
        "exact_shared_endpoints": True,
        "target_identity_hash": target_hash,
        "benchmark_identity_hash": benchmark_hash,
    }
    return output


TIMEFRAME_ETF_PEER_RS_SCHEMA_VERSION = "timeframe-etf-peer-relative-strength-v1"


def compute_completed_timeframe_etf_peer_relative_strength_context(
    *,
    target_code: str,
    market: str,
    timeframe: str,
    target_bars: pd.DataFrame,
    peer_bars: Mapping[str, pd.DataFrame],
    target_bar_end: Any,
    target_input_identity: Mapping[str, Any],
    peer_input_identities: Mapping[str, Mapping[str, Any]],
    horizon_bars: int = 20,
    min_peer_count: int = DEFAULT_MIN_ETF_PEERS,
) -> Dict[str, Any]:
    """Rank one ETF against caller-supplied same-timeframe completed peers.

    The function is a pure deterministic consumer.  It does not discover peers,
    fetch data, persist results, grant upstream rights, or create action/model
    authority.  Every eligible peer must share the target's exact endpoints,
    provider identity, adjustment basis, timeframe and completed-bar contract.
    """
    normalized_market = str(market or "").strip().lower()
    normalized_timeframe = str(timeframe or "").strip()
    normalized_code = str(target_code or "").strip()

    def payload(status: str, reason: str) -> Dict[str, Any]:
        endpoint = pd.to_datetime(target_bar_end, errors="coerce", utc=True)
        return {
            "schema_version": TIMEFRAME_ETF_PEER_RS_SCHEMA_VERSION,
            "family": "trend_relative_strength",
            "asset_type": "etf",
            "status": status,
            "reason": reason,
            "market": normalized_market or "unknown",
            "timeframe": normalized_timeframe or "unknown",
            "target_code": normalized_code,
            "target_bar_end": None if pd.isna(endpoint) else endpoint.isoformat(),
            "horizon_bars": horizon_bars,
            "return_basis": "provider_completed_bar_price_return_proxy",
            "peer_universe": {
                "requested_peer_count": len(peer_bars) if isinstance(peer_bars, Mapping) else 0,
                "eligible_peer_count": 0,
                "excluded_peer_count": 0,
                "minimum_required": min_peer_count,
                "excluded_peers": [],
                "universe_hash": None,
            },
            "target": {},
            "peers": [],
            "relative": {},
            "input_identity": {
                "schema_version": "timeframe-etf-peer-universe-input-v1",
                "timeframe": normalized_timeframe or "unknown",
                "target": dict(target_input_identity)
                if isinstance(target_input_identity, Mapping) else {},
                "peers": [],
            },
            "data_quality": {},
            "independent_action_authority": False,
            "strategy_admitted": False,
            "learning_admitted": False,
            "data_rights_admitted": False,
        }

    if (
        normalized_market != "cn"
        or normalized_timeframe not in {"monthly", "weekly", "daily", "60m", "30m", "15m", "5m"}
        or not normalized_code
        or not isinstance(horizon_bars, int)
        or horizon_bars <= 0
        or not isinstance(min_peer_count, int)
        or min_peer_count <= 0
    ):
        return payload("UNKNOWN", "TIMEFRAME_ETF_PEER_RS_INPUT_INVALID")
    if not isinstance(peer_bars, Mapping) or not isinstance(peer_input_identities, Mapping):
        return payload("MISSING", "TIMEFRAME_ETF_PEER_UNIVERSE_MISSING")

    target_identity = (
        dict(target_input_identity) if isinstance(target_input_identity, Mapping) else {}
    )
    target_hash = str(
        target_identity.get("identity_hash")
        or target_identity.get("data_snapshot_identity")
        or ""
    )
    target_provider = str(target_identity.get("provider_identity") or "").strip()
    target_basis = str(
        target_identity.get("adjustment_basis")
        or target_identity.get("observed_adjustment_basis")
        or ""
    ).strip()
    if (
        len(target_hash) != 64
        or not target_provider
        or not target_basis
        or str(target_identity.get("timeframe") or normalized_timeframe) != normalized_timeframe
        or target_identity.get("completed_bar_only") is not True
    ):
        return payload("UNKNOWN", "TIMEFRAME_ETF_PEER_TARGET_IDENTITY_UNPROVEN")

    target = _normalize_completed_timeframe_bars(
        target_bars, target_bar_end=target_bar_end,
    )
    required = horizon_bars + 1
    if len(target) < required:
        return payload("MISSING", "TIMEFRAME_ETF_PEER_TARGET_WARMUP_INSUFFICIENT")
    target_window = target.tail(required).reset_index(drop=True)
    start = target_window.iloc[0]["bar_end"]
    end = target_window.iloc[-1]["bar_end"]
    target_start = float(target_window.iloc[0]["close"])
    target_end = float(target_window.iloc[-1]["close"])
    if min(target_start, target_end) <= 0:
        return payload("UNKNOWN", "TIMEFRAME_ETF_PEER_TARGET_NON_POSITIVE_ENDPOINT_PRICE")
    target_return = (target_end / target_start - 1.0) * 100.0

    eligible: list[Dict[str, Any]] = []
    excluded: list[Dict[str, str]] = []
    for raw_code, raw_frame in sorted(peer_bars.items(), key=lambda item: str(item[0])):
        code = str(raw_code or "").strip()
        identity_raw = peer_input_identities.get(raw_code)
        if identity_raw is None:
            identity_raw = peer_input_identities.get(code)
        identity = dict(identity_raw) if isinstance(identity_raw, Mapping) else {}
        if not code or code == normalized_code:
            excluded.append({
                "code": code or "UNKNOWN",
                "reason": "TIMEFRAME_ETF_PEER_CODE_INVALID_OR_TARGET_DUPLICATE",
            })
            continue
        identity_hash = str(
            identity.get("identity_hash")
            or identity.get("data_snapshot_identity")
            or ""
        )
        provider = str(identity.get("provider_identity") or "").strip()
        basis = str(
            identity.get("adjustment_basis")
            or identity.get("observed_adjustment_basis")
            or ""
        ).strip()
        if (
            len(identity_hash) != 64
            or provider != target_provider
            or basis != target_basis
            or str(identity.get("timeframe") or normalized_timeframe) != normalized_timeframe
            or identity.get("completed_bar_only") is not True
        ):
            excluded.append({
                "code": code,
                "reason": "TIMEFRAME_ETF_PEER_SOURCE_BASIS_OR_IDENTITY_MISMATCH",
            })
            continue
        peer = _normalize_completed_timeframe_bars(
            raw_frame, target_bar_end=target_bar_end,
        )
        by_end = peer.set_index("bar_end")["close"] if not peer.empty else pd.Series(dtype=float)
        if start not in by_end.index or end not in by_end.index:
            excluded.append({
                "code": code,
                "reason": "TIMEFRAME_ETF_PEER_SHARED_ENDPOINT_MISSING",
            })
            continue
        # Ranking peers by two endpoints alone allows a sparse or shifted
        # middle-grid peer to inflate the eligible peer count. Require the
        # entire completed target horizon to coincide, bar for bar.
        peer_window = peer[
            (peer["bar_end"] >= start) & (peer["bar_end"] <= end)
        ].reset_index(drop=True)
        if len(peer_window) != required or not peer_window["bar_end"].equals(target_window["bar_end"]):
            excluded.append({
                "code": code,
                "reason": "TIMEFRAME_ETF_PEER_FULL_COMPLETED_GRID_MISMATCH",
            })
            continue
        peer_start = float(by_end.loc[start])
        peer_end = float(by_end.loc[end])
        if min(peer_start, peer_end) <= 0:
            excluded.append({
                "code": code,
                "reason": "TIMEFRAME_ETF_PEER_NON_POSITIVE_ENDPOINT_PRICE",
            })
            continue
        peer_return = (peer_end / peer_start - 1.0) * 100.0
        eligible.append({
            "code": code,
            "start_bar_end": start.isoformat(),
            "end_bar_end": end.isoformat(),
            "observations": int(
                peer[(peer["bar_end"] >= start) & (peer["bar_end"] <= end)].shape[0]
            ),
            "return_pct": round(peer_return, 6),
            "input_identity_hash": identity_hash,
            "input_identity": identity,
        })

    universe_document = {
        "timeframe": normalized_timeframe,
        "target_code": normalized_code,
        "target_identity_hash": target_hash,
        "start_bar_end": start.isoformat(),
        "end_bar_end": end.isoformat(),
        "horizon_bars": horizon_bars,
        "peer_identities": [
            {"code": row["code"], "input_identity_hash": row["input_identity_hash"]}
            for row in eligible
        ],
        "excluded_peers": excluded,
    }
    universe_hash = hashlib.sha256(
        json.dumps(
            universe_document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    status = (
        "MISSING" if len(eligible) < min_peer_count
        else "PARTIAL" if excluded
        else "READY"
    )
    reason = (
        "TIMEFRAME_ETF_PEER_UNIVERSE_INSUFFICIENT"
        if len(eligible) < min_peer_count
        else "TIMEFRAME_ETF_PEER_UNIVERSE_PARTIAL"
        if excluded
        else "TIMEFRAME_ETF_PEER_UNIVERSE_READY"
    )
    result = payload(status, reason)
    result["peer_universe"] = {
        "requested_peer_count": len(peer_bars),
        "eligible_peer_count": len(eligible),
        "excluded_peer_count": len(excluded),
        "minimum_required": min_peer_count,
        "excluded_peers": excluded,
        "universe_hash": universe_hash,
    }
    result["target"] = {
        "code": normalized_code,
        "start_bar_end": start.isoformat(),
        "end_bar_end": end.isoformat(),
        "observations": int(len(target_window)),
        "return_pct": round(target_return, 6),
        "input_identity_hash": target_hash,
    }
    result["peers"] = eligible
    result["input_identity"] = {
        "schema_version": "timeframe-etf-peer-universe-input-v1",
        "market": normalized_market,
        "timeframe": normalized_timeframe,
        "target_bar_end": result["target_bar_end"],
        "horizon_bars": horizon_bars,
        "universe_hash": universe_hash,
        "target": target_identity,
        "peers": [row["input_identity"] for row in eligible],
        "excluded_peers": excluded,
    }
    result["data_quality"] = {
        "source_alignment": "MATCHED",
        "provider_identity": target_provider,
        "adjustment_basis": target_basis,
        "exact_shared_endpoints": True,
        "target_input_identity_hash": target_hash,
    }
    if len(eligible) >= min_peer_count:
        peer_returns = [float(row["return_pct"]) for row in eligible]
        peer_median = float(pd.Series(peer_returns, dtype=float).median())
        ranked = sorted(
            [(normalized_code, target_return)]
            + [(row["code"], float(row["return_pct"])) for row in eligible],
            key=lambda item: (-item[1], item[0]),
        )
        rank = next(
            index for index, item in enumerate(ranked, start=1)
            if item[0] == normalized_code
        )
        lower = sum(target_return > value + 1e-12 for value in peer_returns)
        ties = sum(abs(target_return - value) <= 1e-12 for value in peer_returns)
        percentile = (lower + 0.5 * ties) / len(peer_returns) * 100.0
        state = (
            "NEUTRAL" if abs(target_return - peer_median) <= 1e-12
            else "OUTPERFORMING" if target_return > peer_median
            else "UNDERPERFORMING"
        )
        result["relative"] = {
            "state": state,
            "rank": rank,
            "rank_denominator": len(ranked),
            "percentile": round(percentile, 6),
            "peer_median_return_pct": round(peer_median, 6),
            "excess_vs_peer_median_pct_points": round(
                target_return - peer_median, 6,
            ),
        }
    return result


def compute_relative_strength_context(
    *,
    stock_code: str,
    market: str,
    stock_history: pd.DataFrame,
    benchmark_history: pd.DataFrame,
    benchmark: Mapping[str, Any],
    target_date: date,
    horizon_sessions: int = DEFAULT_HORIZON_SESSIONS,
    require_source_alignment: bool = False,
) -> Dict[str, Any]:
    """Compare stock and benchmark on the benchmark's exact completed-session endpoints."""
    benchmark_meta = dict(benchmark)
    required_observations = horizon_sessions + 1
    stock = _normalize_history(stock_history, target_date=target_date)
    bench = _normalize_history(benchmark_history, target_date=target_date)

    if len(bench) < required_observations:
        return _status_payload(
            status="MISSING",
            market=market,
            stock_code=stock_code,
            reason="BENCHMARK_WARMUP_INSUFFICIENT",
            benchmark=benchmark_meta,
            horizon_sessions=horizon_sessions,
        )
    if bench.empty or bench.iloc[-1]["date"] != target_date:
        return _status_payload(
            status="MISSING",
            market=market,
            stock_code=stock_code,
            reason="BENCHMARK_TARGET_DATE_MISSING",
            benchmark=benchmark_meta,
            horizon_sessions=horizon_sessions,
        )

    bench_window = bench.tail(required_observations).reset_index(drop=True)
    start_date = bench_window.iloc[0]["date"]
    end_date = bench_window.iloc[-1]["date"]
    stock_window = stock[(stock["date"] >= start_date) & (stock["date"] <= end_date)]
    stock_by_date = stock.set_index("date")["close"] if not stock.empty else pd.Series(dtype=float)

    if start_date not in stock_by_date.index or end_date not in stock_by_date.index:
        return _status_payload(
            status="MISSING",
            market=market,
            stock_code=stock_code,
            reason="STOCK_SHARED_ENDPOINT_MISSING",
            benchmark={
                **benchmark_meta,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "observations": int(len(bench_window)),
            },
            horizon_sessions=horizon_sessions,
        )

    stock_start = float(stock_by_date.loc[start_date])
    stock_end = float(stock_by_date.loc[end_date])
    benchmark_start = float(bench_window.iloc[0]["close"])
    benchmark_end = float(bench_window.iloc[-1]["close"])
    if min(stock_start, stock_end, benchmark_start, benchmark_end) <= 0:
        return _status_payload(
            status="UNKNOWN",
            market=market,
            stock_code=stock_code,
            reason="NON_POSITIVE_ENDPOINT_PRICE",
            benchmark=benchmark_meta,
            horizon_sessions=horizon_sessions,
        )

    stock_return = (stock_end / stock_start - 1.0) * 100.0
    benchmark_return = (benchmark_end / benchmark_start - 1.0) * 100.0
    excess_return = stock_return - benchmark_return
    ratio_change = ((stock_end / stock_start) / (benchmark_end / benchmark_start) - 1.0) * 100.0
    if abs(ratio_change) <= 1e-12:
        state = "NEUTRAL"
    elif ratio_change > 0:
        state = "OUTPERFORMING"
    else:
        state = "UNDERPERFORMING"

    stock_start_row = stock[stock["date"] == start_date].iloc[-1]
    stock_end_row = stock[stock["date"] == end_date].iloc[-1]
    stock_endpoint_sources = {
        str(value).strip()
        for value in (
            stock_start_row.get("data_source"),
            stock_end_row.get("data_source"),
        )
        if value not in (None, "") and str(value).strip()
    }
    benchmark_source = str(benchmark_meta.get("source") or "").strip()
    if not require_source_alignment:
        source_alignment = "NOT_REQUIRED"
        status = "READY"
        reason = "EXACT_SHARED_ENDPOINTS"
    elif benchmark_source and stock_endpoint_sources == {benchmark_source}:
        source_alignment = "MATCHED"
        status = "READY"
        reason = "EXACT_SHARED_ENDPOINTS_SOURCE_ALIGNED"
    else:
        source_alignment = "UNPROVEN"
        status = "PARTIAL"
        reason = "SOURCE_ALIGNMENT_UNPROVEN"

    return {
        "schema_version": RELATIVE_STRENGTH_SCHEMA_VERSION,
        "family": "trend_relative_strength",
        "status": status,
        "market": market,
        "stock_code": stock_code,
        "horizon_sessions": horizon_sessions,
        "return_basis": "provider_price_return_proxy",
        "reason": reason,
        "benchmark": {
            **benchmark_meta,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "observations": int(len(bench_window)),
            "return_pct": round(benchmark_return, 6),
        },
        "stock": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "observations": int(len(stock_window)),
            "return_pct": round(stock_return, 6),
        },
        "relative": {
            "state": state,
            "excess_return_pct_points": round(excess_return, 6),
            "relative_ratio_change_pct": round(ratio_change, 6),
        },
        "data_quality": {
            "source_alignment": source_alignment,
            "benchmark_source": benchmark_source or None,
            "stock_endpoint_sources": sorted(stock_endpoint_sources),
        },
    }




def compute_etf_peer_relative_strength_context(
    *,
    etf_code: str,
    market: str,
    target_history: pd.DataFrame,
    peer_histories: Mapping[str, pd.DataFrame],
    target_date: date,
    horizon_sessions: int = DEFAULT_HORIZON_SESSIONS,
    min_peer_count: int = DEFAULT_MIN_ETF_PEERS,
) -> Dict[str, Any]:
    """Rank one ETF against a typed same-provider peer universe at one as-of.

    This is a deterministic cross-sectional consumer over already supplied
    histories.  It does not discover peers, fetch data, grant data rights, or
    create action/model authority.  Every eligible peer must share the target's
    exact start/end dates, provider identity, and adjustment basis.
    """

    normalized_market = str(market or "").strip().lower()
    normalized_code = str(etf_code or "").strip()

    def payload(status: str, reason: str) -> Dict[str, Any]:
        return {
            "schema_version": ETF_PEER_RELATIVE_STRENGTH_SCHEMA_VERSION,
            "family": "trend_relative_strength",
            "asset_type": "etf",
            "status": status,
            "reason": reason,
            "market": normalized_market or "unknown",
            "etf_code": normalized_code,
            "target_date": target_date.isoformat() if isinstance(target_date, date) else None,
            "horizon_sessions": horizon_sessions,
            "return_basis": "provider_price_return_proxy",
            "peer_universe": {
                "requested_peer_count": len(peer_histories) if isinstance(peer_histories, Mapping) else 0,
                "eligible_peer_count": 0,
                "excluded_peer_count": 0,
                "minimum_required": min_peer_count,
                "excluded_peers": [],
                "universe_hash": None,
            },
            "target": {},
            "peers": [],
            "relative": {},
            "data_quality": {},
            "independent_action_authority": False,
            "strategy_admitted": False,
            "learning_admitted": False,
            "data_rights_admitted": False,
        }

    if normalized_market != "cn":
        return payload("NOT_SUPPORTED", "MARKET_NOT_SUPPORTED_V1")
    if not normalized_code or not isinstance(target_date, date):
        return payload("UNKNOWN", "TARGET_IDENTITY_OR_DATE_INVALID")
    if not isinstance(horizon_sessions, int) or horizon_sessions <= 0:
        return payload("UNKNOWN", "HORIZON_INVALID")
    if not isinstance(min_peer_count, int) or min_peer_count <= 0:
        return payload("UNKNOWN", "MINIMUM_PEER_COUNT_INVALID")
    if not isinstance(peer_histories, Mapping):
        return payload("MISSING", "ETF_PEER_UNIVERSE_MISSING")

    required = horizon_sessions + 1
    target = _normalize_history(target_history, target_date=target_date)
    if len(target) < required:
        return payload("MISSING", "TARGET_WARMUP_INSUFFICIENT")
    if target.empty or target.iloc[-1]["date"] != target_date:
        return payload("MISSING", "TARGET_DATE_MISSING")

    target_window = target.tail(required).reset_index(drop=True)
    start_date = target_window.iloc[0]["date"]
    end_date = target_window.iloc[-1]["date"]
    target_start = float(target_window.iloc[0]["close"])
    target_end = float(target_window.iloc[-1]["close"])
    if min(target_start, target_end) <= 0:
        return payload("UNKNOWN", "TARGET_NON_POSITIVE_ENDPOINT_PRICE")

    target_identity = build_completed_history_identity(
        target_history,
        stock_code=normalized_code,
        market="cn",
        target_date=target_date,
    )
    target_provider = str(target_identity.get("provider_identity") or "").strip()
    target_basis = str(target_identity.get("adjustment_basis") or "").strip()
    target_hash = str(target_identity.get("data_snapshot_identity") or "").strip()
    target_identity_ready = bool(
        len(target_hash) == 64
        and target_provider
        and target_basis
        and not target_identity.get("price_identity_reasons")
    )
    if not target_identity_ready:
        result = payload("UNKNOWN", "TARGET_PRICE_IDENTITY_UNPROVEN")
        result["data_quality"] = {
            "target_input_identity": target_identity,
        }
        return result

    target_return = (target_end / target_start - 1.0) * 100.0
    eligible: list[Dict[str, Any]] = []
    excluded: list[Dict[str, str]] = []

    for raw_code, raw_history in sorted(peer_histories.items(), key=lambda item: str(item[0])):
        code = str(raw_code or "").strip()
        if not code or code == normalized_code:
            excluded.append({"code": code or "UNKNOWN", "reason": "PEER_CODE_INVALID_OR_TARGET_DUPLICATE"})
            continue
        if not isinstance(raw_history, pd.DataFrame) or raw_history.empty:
            excluded.append({"code": code, "reason": "PEER_HISTORY_MISSING"})
            continue
        peer = _normalize_history(raw_history, target_date=target_date)
        by_date = peer.set_index("date")["close"] if not peer.empty else pd.Series(dtype=float)
        if start_date not in by_date.index or end_date not in by_date.index:
            excluded.append({"code": code, "reason": "PEER_SHARED_ENDPOINT_MISSING"})
            continue
        identity = build_completed_history_identity(
            raw_history,
            stock_code=code,
            market="cn",
            target_date=target_date,
        )
        provider = str(identity.get("provider_identity") or "").strip()
        basis = str(identity.get("adjustment_basis") or "").strip()
        identity_hash = str(identity.get("data_snapshot_identity") or "").strip()
        if (
            len(identity_hash) != 64
            or not provider
            or not basis
            or identity.get("price_identity_reasons")
        ):
            excluded.append({"code": code, "reason": "PEER_PRICE_IDENTITY_UNPROVEN"})
            continue
        if provider != target_provider or basis != target_basis:
            excluded.append({"code": code, "reason": "PEER_SOURCE_OR_BASIS_MISMATCH"})
            continue
        peer_start = float(by_date.loc[start_date])
        peer_end = float(by_date.loc[end_date])
        if min(peer_start, peer_end) <= 0:
            excluded.append({"code": code, "reason": "PEER_NON_POSITIVE_ENDPOINT_PRICE"})
            continue
        peer_return = (peer_end / peer_start - 1.0) * 100.0
        observations = int(
            peer[(peer["date"] >= start_date) & (peer["date"] <= end_date)].shape[0]
        )
        eligible.append({
            "code": code,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "observations": observations,
            "return_pct": round(peer_return, 6),
            "input_identity_hash": identity_hash,
        })
        eligible[-1]["input_identity"] = {
            **identity,
            "stock_code": code,
            "market": "cn",
            "target_date": target_date.isoformat(),
        }

    universe_document = {
        "target_code": normalized_code,
        "target_identity_hash": target_hash,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "horizon_sessions": horizon_sessions,
        "peer_identities": [
            {"code": row["code"], "input_identity_hash": row["input_identity_hash"]}
            for row in eligible
        ],
        "excluded_peers": excluded,
    }
    universe_hash = hashlib.sha256(
        json.dumps(
            universe_document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    input_identity = {
        "schema_version": "etf-peer-universe-input-v1",
        "market": "cn",
        "target_date": target_date.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "horizon_sessions": horizon_sessions,
        "universe_hash": universe_hash,
        "target": {
            **target_identity,
            "stock_code": normalized_code,
            "market": "cn",
            "target_date": target_date.isoformat(),
        },
        "peers": [row["input_identity"] for row in eligible],
        "excluded_peers": excluded,
    }

    result = payload(
        "MISSING" if len(eligible) < min_peer_count else "PARTIAL" if excluded else "READY",
        "ETF_PEER_UNIVERSE_INSUFFICIENT"
        if len(eligible) < min_peer_count
        else "EXACT_SHARED_ENDPOINTS_PEER_UNIVERSE_PARTIAL"
        if excluded
        else "EXACT_SHARED_ENDPOINTS_PEER_UNIVERSE_READY",
    )
    result["peer_universe"] = {
        "requested_peer_count": len(peer_histories),
        "eligible_peer_count": len(eligible),
        "excluded_peer_count": len(excluded),
        "minimum_required": min_peer_count,
        "excluded_peers": excluded,
        "universe_hash": universe_hash,
    }
    result["target"] = {
        "code": normalized_code,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "observations": int(len(target_window)),
        "return_pct": round(target_return, 6),
        "input_identity_hash": target_hash,
    }
    result["peers"] = eligible
    result["input_identity"] = input_identity
    result["data_quality"] = {
        "source_alignment": "MATCHED",
        "provider_identity": target_provider,
        "adjustment_basis": target_basis,
        "exact_shared_endpoints": True,
        "target_input_identity_hash": target_hash,
    }

    if len(eligible) >= min_peer_count:
        peer_returns = [float(row["return_pct"]) for row in eligible]
        peer_median = float(pd.Series(peer_returns, dtype=float).median())
        ranked = sorted(
            [(normalized_code, target_return), *[(row["code"], float(row["return_pct"])) for row in eligible]],
            key=lambda item: (-item[1], item[0]),
        )
        rank = next(index for index, item in enumerate(ranked, start=1) if item[0] == normalized_code)
        lower = sum(target_return > value + 1e-12 for value in peer_returns)
        ties = sum(abs(target_return - value) <= 1e-12 for value in peer_returns)
        percentile = (lower + 0.5 * ties) / len(peer_returns) * 100.0
        if abs(target_return - peer_median) <= 1e-12:
            state = "NEUTRAL"
        elif target_return > peer_median:
            state = "OUTPERFORMING"
        else:
            state = "UNDERPERFORMING"
        result["relative"] = {
            "state": state,
            "rank": rank,
            "rank_denominator": len(ranked),
            "percentile": round(percentile, 6),
            "peer_median_return_pct": round(peer_median, 6),
            "excess_vs_peer_median_pct_points": round(target_return - peer_median, 6),
        }
    return result


class RelativeStrengthService:
    """Build one fail-closed CN stock-vs-benchmark RS context per completed date."""

    def __init__(self, *, fetcher_manager: Any) -> None:
        self.fetcher_manager = fetcher_manager
        self._benchmark_cache: Dict[str, Tuple[Optional[pd.DataFrame], Optional[str], Optional[str]]] = {}
        self._benchmark_cache_lock = threading.Lock()

    def build_context(
        self,
        *,
        stock_code: str,
        market: Optional[str],
        stock_history: Optional[pd.DataFrame],
        target_date: Optional[date],
    ) -> Dict[str, Any]:
        normalized_market = str(market or "").strip().lower()
        benchmark_meta = dict(CN_BENCHMARK_PROXY)
        if normalized_market != "cn":
            return _status_payload(
                status="NOT_SUPPORTED",
                market=normalized_market or "unknown",
                stock_code=stock_code,
                reason="MARKET_NOT_SUPPORTED_V1",
                benchmark=benchmark_meta,
            )
        if not isinstance(target_date, date):
            return _status_payload(
                status="UNKNOWN",
                market="cn",
                stock_code=stock_code,
                reason="TARGET_DATE_UNKNOWN",
                benchmark=benchmark_meta,
            )
        if not isinstance(stock_history, pd.DataFrame) or stock_history.empty:
            return _status_payload(
                status="MISSING",
                market="cn",
                stock_code=stock_code,
                reason="STOCK_COMPLETED_HISTORY_MISSING",
                benchmark=benchmark_meta,
            )

        benchmark_history, source, error = self._load_benchmark(target_date)
        if error is not None or benchmark_history is None:
            return _status_payload(
                status="UNKNOWN",
                market="cn",
                stock_code=stock_code,
                reason="BENCHMARK_FETCH_UNAVAILABLE",
                benchmark={**benchmark_meta, "source": source},
            )

        stock_identity = build_completed_history_identity(
            stock_history,
            stock_code=stock_code,
            market="cn",
            target_date=target_date,
        )
        benchmark_identity = build_completed_history_identity(
            benchmark_history,
            stock_code=CN_BENCHMARK_PROXY["code"],
            market="cn",
            target_date=target_date,
        )
        context = compute_relative_strength_context(
            stock_code=stock_code,
            market="cn",
            stock_history=stock_history,
            benchmark_history=benchmark_history,
            benchmark={**benchmark_meta, "source": source},
            target_date=target_date,
            require_source_alignment=True,
        )
        context["target_date"] = target_date.isoformat()
        context["input_identity"] = {
            "stock": {
                **stock_identity,
                "stock_code": stock_code,
                "market": "cn",
                "target_date": target_date.isoformat(),
            },
            "benchmark": {
                **benchmark_identity,
                "stock_code": CN_BENCHMARK_PROXY["code"],
                "market": "cn",
                "target_date": target_date.isoformat(),
            },
        }
        identity_ready = bool(
            stock_identity.get("data_snapshot_identity")
            and benchmark_identity.get("data_snapshot_identity")
            and stock_identity.get("provider_identity")
            and benchmark_identity.get("provider_identity")
            and stock_identity.get("provider_identity") == benchmark_identity.get("provider_identity")
            and str(source or "").strip() == str(benchmark_identity.get("provider_identity") or "").strip()
            and stock_identity.get("adjustment_basis")
            and stock_identity.get("adjustment_basis") == benchmark_identity.get("adjustment_basis")
            and not stock_identity.get("price_identity_reasons")
            and not benchmark_identity.get("price_identity_reasons")
        )
        if context.get("status") == "READY" and not identity_ready:
            context["status"] = "PARTIAL"
            context["reason"] = "SEMANTIC_PRICE_IDENTITY_UNPROVEN"
        return context

    def _load_benchmark(
        self,
        target_date: date,
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], Optional[str]]:
        cache_key = target_date.isoformat()
        with self._benchmark_cache_lock:
            cached = self._benchmark_cache.get(cache_key)
            if cached is not None:
                frame, source, error = cached
                return (frame.copy() if isinstance(frame, pd.DataFrame) else None, source, error)

            try:
                frame, source = self.fetcher_manager.get_daily_data(
                    CN_BENCHMARK_PROXY["code"],
                    end_date=target_date.isoformat(),
                    days=BENCHMARK_FETCH_DAYS,
                )
                stored = frame.copy() if isinstance(frame, pd.DataFrame) else None
                result = (stored, str(source or "unknown"), None)
            except Exception as exc:
                logger.warning("benchmark proxy history unavailable for %s: %s", target_date, exc)
                result = (None, None, str(exc))

            self._benchmark_cache[cache_key] = result
            frame, source, error = result
            return (frame.copy() if isinstance(frame, pd.DataFrame) else None, source, error)
