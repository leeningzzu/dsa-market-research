"""DB-first K-line history loader for Agent tools.

Provides:
- ContextVar-based frozen target_date propagation across threads
- ``load_history_df``: read from DB first, DataFetcherManager fallback

Fixes #1066 – eliminates 45+ redundant HTTP requests per stock in Agent mode.
"""
from __future__ import annotations

import contextvars
import logging
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Any, List, Optional, Tuple

import pandas as pd

from data_provider.daily_data_identity import (
    DailyDataIdentityError,
    attach_daily_data_identity,
    extract_daily_data_identity,
    extract_daily_data_identity_from_records,
    rebind_daily_data_identity,
    verify_observed_daily_frame_request,
)

logger = logging.getLogger(__name__)
_CACHE_MIN_RECORDS = 30

# ---------------------------------------------------------------------------
# Frozen target date (ContextVar) – set once per stock in pipeline, read by
# all agent tool threads via copy_context().run().
# ---------------------------------------------------------------------------
_frozen_target_date: contextvars.ContextVar[Optional[date]] = contextvars.ContextVar(
    "_frozen_target_date", default=None,
)


def set_frozen_target_date(d: date) -> contextvars.Token:
    return _frozen_target_date.set(d)


def get_frozen_target_date() -> Optional[date]:
    return _frozen_target_date.get()


def reset_frozen_target_date(token: contextvars.Token) -> None:
    _frozen_target_date.reset(token)


# ---------------------------------------------------------------------------
# Internal DataFetcherManager singleton (fallback only)
# ---------------------------------------------------------------------------
_fetcher_singleton = None
_fetcher_lock = Lock()


def _get_fetcher_manager():
    global _fetcher_singleton
    if _fetcher_singleton is None:
        with _fetcher_lock:
            if _fetcher_singleton is None:
                from data_provider import DataFetcherManager
                _fetcher_singleton = DataFetcherManager()
    return _fetcher_singleton


# ---------------------------------------------------------------------------
# DB-first history loader
# ---------------------------------------------------------------------------
def _history_code_candidates(stock_code: str) -> Tuple[List[str], str]:
    from data_provider.base import canonical_stock_code, normalize_stock_code

    raw_code = str(stock_code or "").strip()
    normalized_code = canonical_stock_code(normalize_stock_code(raw_code))
    candidates: List[str] = []
    for candidate in (canonical_stock_code(raw_code), normalized_code):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates, normalized_code


def _coerce_bar_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return date.min
    if hasattr(value, "date"):
        try:
            coerced = value.date()
            return coerced if isinstance(coerced, date) else date.min
        except Exception:
            return date.min
    return date.min


def _bar_date(bar: Any) -> date:
    row_date = _coerce_bar_date(getattr(bar, "date", None))
    if row_date != date.min:
        return row_date
    if hasattr(bar, "to_dict"):
        try:
            return _coerce_bar_date((bar.to_dict() or {}).get("date"))
        except Exception:
            return date.min
    return date.min


def _select_best_bars(db, stock_code: str, start: date, end: date) -> Tuple[Optional[str], list]:
    candidates, normalized_code = _history_code_candidates(stock_code)
    best_code = None
    best_bars = []
    best_key = None

    for candidate in candidates:
        bars = list(db.get_data_range(candidate, start, end) or [])
        if not bars:
            continue
        latest_date = max(_bar_date(bar) for bar in bars)
        key = (latest_date, len(bars), candidate == normalized_code)
        if best_key is None or key > best_key:
            best_key = key
            best_code = candidate
            best_bars = bars

    return best_code, best_bars


def _row_record(row: Any) -> dict:
    if hasattr(row, "to_dict"):
        value = row.to_dict()
        return dict(value) if isinstance(value, dict) else {}
    return {
        key: getattr(row, key, None)
        for key in (
            "code", "date", "open", "high", "low", "close", "volume",
            "amount", "pct_chg", "ma5", "ma10", "ma20", "volume_ratio",
            "data_source", "data_identity_json", "data_identity_hash",
        )
    }


def _load_verified_cache_for_calculation(
    db: Any,
    stock_code: str,
    start: date,
    end: date,
    required_records: int,
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Admit only content-bound typed DB history for calculations/ranking."""
    from data_provider.base import normalize_stock_code

    candidates, normalized_code = _history_code_candidates(stock_code)
    requested_key = normalize_stock_code(stock_code)
    best = None
    best_key = None
    for candidate in candidates:
        try:
            rows = list(db.get_data_range(candidate, start, end) or [])
            if not rows:
                continue
            records = [_row_record(row) for row in rows]
            identity = extract_daily_data_identity_from_records(records, strict=True)
            if (
                identity is None
                or identity.get("identity_state") != "OBSERVED"
                or normalize_stock_code(str(identity.get("instrument") or ""))
                != requested_key
                or identity.get("returned_end") != end.isoformat()
            ):
                continue
            origin_start = date.fromisoformat(str(identity.get("returned_start") or ""))
            origin_end = date.fromisoformat(str(identity.get("returned_end") or ""))
            if origin_end != end:
                continue
            full_rows = (
                rows
                if origin_start >= start
                else list(db.get_data_range(candidate, origin_start, origin_end) or [])
            )
            if not full_rows:
                continue
            full_records = [_row_record(row) for row in full_rows]
            if any(
                normalize_stock_code(str(record.get("code") or candidate))
                != requested_key
                for record in full_records
            ):
                continue
            full_frame = pd.DataFrame(full_records)
            verify_observed_daily_frame_request(
                full_frame,
                instrument=stock_code,
                provider_identity=identity.get("provider_identity"),
                requested_start=identity.get("requested_start"),
                requested_end=identity.get("requested_end"),
                target_date=origin_end,
            )
            dates = pd.to_datetime(full_frame["date"], errors="coerce")
            if dates.isna().any():
                continue
            selected = full_frame.loc[
                dates.dt.date.between(start, end)
            ].copy()
            selected_dates = pd.to_datetime(
                selected["date"], errors="coerce",
            ).dt.date
            if (
                selected.empty
                or selected_dates.isna().any()
                or max(selected_dates) != end
                or len(selected) < required_records
            ):
                continue
            selected.drop(
                columns=["data_identity_json", "data_identity_hash"],
                errors="ignore",
                inplace=True,
            )
            view_identity = rebind_daily_data_identity(identity, selected)
            attach_daily_data_identity(selected, view_identity)
            key = (len(selected), candidate == normalized_code)
            if best_key is None or key > best_key:
                best_key = key
                best = (selected, candidate)
        except (
            DailyDataIdentityError, ValueError, TypeError, KeyError,
            AttributeError, IndexError,
        ):
            continue
    return best if best is not None else (None, None)


def load_history_df(
    stock_code: str,
    days: int = 60,
    target_date: Optional[date] = None,
    *,
    require_verified_identity: bool = False,
) -> Tuple[Optional[pd.DataFrame], str]:
    """Load K-line history, DB first with DataFetcherManager fallback.

    Returns ``(df, source)`` where *source* is ``"db_cache"`` on DB hit or the
    actual provider name on network fallback.  Returns ``(None, "none")`` when
    both paths fail.

    ``require_verified_identity=True`` is mandatory for any consumer that can
    rank, calculate indicators, or affect the canonical. It rejects legacy
    date-only caches and unbound provider frames while preserving the default
    compatibility path for raw data viewing only.
    """
    from src.storage import get_db

    # Resolve effective end date
    if target_date is not None:
        end = target_date
    else:
        frozen = get_frozen_target_date()
        end = frozen if frozen else date.today()

    # Calendar-day buffer: ~1.8x trading days + margin for long holidays
    start = end - timedelta(days=int(days * 1.8) + 10)

    # --- 1. DB lookup (canonical code, then prefix-stripped fallback) ------
    try:
        db = get_db()
        required_records = max(min(days, _CACHE_MIN_RECORDS), 1)
        if require_verified_identity:
            df, _code = _load_verified_cache_for_calculation(
                db, stock_code, start, end, required_records,
            )
            if df is not None:
                logger.debug(
                    "load_history_df(%s): %d verified bars from DB (requested %d)",
                    stock_code, len(df), days,
                )
                return df, "db_cache"
        else:
            _code, bars = _select_best_bars(db, stock_code, start, end)
            latest_date = max((_bar_date(bar) for bar in bars), default=date.min)
            if bars and latest_date >= end and len(bars) >= required_records:
                df = pd.DataFrame([b.to_dict() for b in bars])
                identity = extract_daily_data_identity(df, strict=False)
                if identity is not None:
                    attach_daily_data_identity(df, rebind_daily_data_identity(identity, df))
                logger.debug(
                    "load_history_df(%s): %d bars from DB (requested %d)",
                    stock_code, len(df), days,
                )
                return df, "db_cache"
    except Exception as e:
        logger.debug("load_history_df(%s): DB read failed: %s", stock_code, e)

    # --- 2. Network fallback via singleton DataFetcherManager -------------
    # Historical replay must bind the provider request to the same completed
    # date window as the DB lookup. Passing only days would let a provider
    # default its end date to today and make a past target_date depend on
    # later bars.
    try:
        manager = _get_fetcher_manager()
        df, source = manager.get_daily_data(
            stock_code,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            days=days,
        )
        if df is not None and not df.empty:
            if "date" not in df.columns:
                logger.warning(
                    "load_history_df(%s): provider %s returned history without date identity",
                    stock_code,
                    source,
                )
                return None, "none"

            try:
                identity = extract_daily_data_identity(df, strict=True)
                if require_verified_identity:
                    verify_observed_daily_frame_request(
                        df,
                        instrument=stock_code,
                        provider_identity=source,
                        requested_start=start,
                        requested_end=end,
                        target_date=end,
                    )
            except DailyDataIdentityError as exc:
                logger.warning(
                    "load_history_df(%s): provider %s returned malformed daily identity: %s",
                    stock_code,
                    source,
                    exc,
                )
                return None, "none"
            frame = df.copy()
            bar_dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
            in_window = bar_dates.notna() & (bar_dates >= start) & (bar_dates <= end)
            frame = frame.loc[in_window].copy()
            if frame.empty:
                logger.warning(
                    "load_history_df(%s): provider %s returned no bars inside %s..%s",
                    stock_code,
                    source,
                    start,
                    end,
                )
                return None, "none"

            # DataFetcherManager chose exactly one provider for this frame.
            # Persist that route on every consumed row so downstream PIT/price
            # identity never has to guess from a side-channel.
            frame["data_source"] = str(source or "").strip() or None
            if identity is not None:
                attach_daily_data_identity(frame, rebind_daily_data_identity(identity, frame))
            return frame, source
    except Exception as e:
        logger.warning("load_history_df(%s): DataFetcherManager failed: %s", stock_code, e)

    return None, "none"
