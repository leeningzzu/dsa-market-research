# -*- coding: utf-8 -*-
"""
===================================
交易日历模块 (Issue #373 / Issue #1386 P0)
===================================

职责：
1. 按市场（A股/港股/美股/日股/韩股/台股）判断当日是否为交易日
2. 按市场时区取“今日”日期，避免服务器 UTC 导致日期错误
3. 支持 per-stock 过滤：只分析当日开市市场的股票
4. 提供 regular-session 市场阶段推断基线，不改变现有分析入口行为

依赖：exchange-calendars（可选，交易日判断不可用时 fail-open，阶段推断不可用时 unknown）
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from src.services.market_symbol_utils import get_suffix_market

logger = logging.getLogger(__name__)

# Exchange-calendars availability
_XCALS_AVAILABLE = False
try:
    import exchange_calendars as xcals
    _XCALS_AVAILABLE = True
except ImportError:
    logger.warning(
        "exchange-calendars not installed; trading day check disabled. "
        "Run: pip install exchange-calendars"
    )

# Market -> exchange code (exchange-calendars)
MARKET_EXCHANGE = {"cn": "XSHG", "hk": "XHKG", "us": "XNYS", "jp": "XTKS", "kr": "XKRX", "tw": "XTAI"}

# Market -> IANA timezone for "today"
MARKET_TIMEZONE = {
    "cn": "Asia/Shanghai",
    "hk": "Asia/Hong_Kong",
    "us": "America/New_York",
    "jp": "Asia/Tokyo",
    "kr": "Asia/Seoul",
    "tw": "Asia/Taipei",
}

OBSERVED_SESSION_CALENDAR_SOURCE_VERSION = "observed-session-calendar-source-v1"
_OBSERVED_SESSION_CALENDAR_FIELDS = frozenset({
    "schema_version", "market", "provider_identity", "provider_route",
    "requested_start", "requested_end", "observed_at", "available_at",
    "source_content_sha256", "revision", "vintage", "sessions",
    "session_count", "first_session", "last_session",
    "historical_pit_eligible", "storage_admitted", "strategy_admitted",
    "learning_admitted", "source_identity_hash",
})


def _calendar_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _strict_calendar_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _aware_calendar_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_sha256_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def build_observed_session_calendar_source(
    *,
    market: str,
    provider_identity: str,
    provider_route: str,
    requested_start: date | str,
    requested_end: date | str,
    observed_at: datetime | str,
    available_at: datetime | str,
    source_content_sha256: str,
    revision: str,
    vintage: str,
    sessions: List[date | str] | Tuple[date | str, ...],
) -> Dict[str, Any]:
    """Seal one current observation of provider trading sessions.

    This is a run-local source identity for current research.  It is not a
    historical PIT calendar, a storage grant, a strategy signal, or a label.
    """
    start = _strict_calendar_date(requested_start)
    end = _strict_calendar_date(requested_end)
    observed = _aware_calendar_datetime(observed_at)
    available = _aware_calendar_datetime(available_at)
    parsed_sessions = tuple(_strict_calendar_date(item) for item in sessions)
    if (
        start is None or end is None or start > end
        or observed is None or available is None or available > observed
        or any(item is None for item in parsed_sessions)
        or not parsed_sessions
    ):
        raise ValueError("OBSERVED_SESSION_CALENDAR_SOURCE_INPUT_INVALID")
    typed_sessions = tuple(item for item in parsed_sessions if item is not None)
    if (
        tuple(sorted(typed_sessions)) != typed_sessions
        or len(set(typed_sessions)) != len(typed_sessions)
        or typed_sessions[0] < start
        or typed_sessions[-1] > end
        or not _is_sha256_text(source_content_sha256)
        or not str(provider_identity or "").strip()
        or not str(provider_route or "").strip()
        or not str(revision or "").strip()
        or not str(vintage or "").strip()
    ):
        raise ValueError("OBSERVED_SESSION_CALENDAR_SOURCE_CONTENT_INVALID")
    body: Dict[str, Any] = {
        "schema_version": OBSERVED_SESSION_CALENDAR_SOURCE_VERSION,
        "market": str(market or "").strip().lower(),
        "provider_identity": str(provider_identity).strip(),
        "provider_route": str(provider_route).strip(),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "observed_at": observed.isoformat(),
        "available_at": available.isoformat(),
        "source_content_sha256": str(source_content_sha256).strip().lower(),
        "revision": str(revision).strip(),
        "vintage": str(vintage).strip(),
        "sessions": tuple(item.isoformat() for item in typed_sessions),
        "session_count": len(typed_sessions),
        "first_session": typed_sessions[0].isoformat(),
        "last_session": typed_sessions[-1].isoformat(),
        "historical_pit_eligible": False,
        "storage_admitted": False,
        "strategy_admitted": False,
        "learning_admitted": False,
    }
    body["source_identity_hash"] = _calendar_digest(body)
    return validate_observed_session_calendar_source(body)


def validate_observed_session_calendar_source(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _OBSERVED_SESSION_CALENDAR_FIELDS:
        raise ValueError("OBSERVED_SESSION_CALENDAR_SOURCE_SCHEMA_MISMATCH")
    result = dict(value)
    if result.get("schema_version") != OBSERVED_SESSION_CALENDAR_SOURCE_VERSION:
        raise ValueError("OBSERVED_SESSION_CALENDAR_SOURCE_VERSION_MISMATCH")
    start = _strict_calendar_date(result.get("requested_start"))
    end = _strict_calendar_date(result.get("requested_end"))
    first = _strict_calendar_date(result.get("first_session"))
    last = _strict_calendar_date(result.get("last_session"))
    observed = _aware_calendar_datetime(result.get("observed_at"))
    available = _aware_calendar_datetime(result.get("available_at"))
    raw_sessions = result.get("sessions")
    if not isinstance(raw_sessions, (list, tuple)):
        raise ValueError("OBSERVED_SESSION_CALENDAR_SOURCE_SESSIONS_INVALID")
    sessions = tuple(_strict_calendar_date(item) for item in raw_sessions)
    if (
        start is None or end is None or start > end
        or first is None or last is None
        or observed is None or available is None or available > observed
        or any(item is None for item in sessions)
        or not sessions
    ):
        raise ValueError("OBSERVED_SESSION_CALENDAR_SOURCE_FIELDS_INVALID")
    typed_sessions = tuple(item for item in sessions if item is not None)
    if (
        tuple(sorted(typed_sessions)) != typed_sessions
        or len(set(typed_sessions)) != len(typed_sessions)
        or typed_sessions[0] != first
        or typed_sessions[-1] != last
        or first < start or last > end
        or result.get("session_count") != len(typed_sessions)
        or not _is_sha256_text(result.get("source_content_sha256"))
        or not _is_sha256_text(result.get("source_identity_hash"))
        or not str(result.get("market") or "").strip()
        or not str(result.get("provider_identity") or "").strip()
        or not str(result.get("provider_route") or "").strip()
        or not str(result.get("revision") or "").strip()
        or not str(result.get("vintage") or "").strip()
        or any(result.get(key) is not False for key in (
            "historical_pit_eligible", "storage_admitted",
            "strategy_admitted", "learning_admitted",
        ))
    ):
        raise ValueError("OBSERVED_SESSION_CALENDAR_SOURCE_CONTENT_INVALID")
    body = {key: result[key] for key in result if key != "source_identity_hash"}
    if result["source_identity_hash"] != _calendar_digest(body):
        raise ValueError("OBSERVED_SESSION_CALENDAR_SOURCE_HASH_MISMATCH")
    result["sessions"] = tuple(item.isoformat() for item in typed_sessions)
    return result

# P0 market phase baseline (Issue #1386). This is an intentionally small
# regular-session inference layer; it does not change existing fail-open
# trading-day filtering or effective-date behavior.
# tw: TWSE/TPEx run a 13:25-13:30 closing call auction (5 min). JP/KR use
# regular-session closing auction windows before the 15:30 close (JP 5 min,
# KR 10 min). Without an entry here .get(market, 0) yields a zero-width
# window, so the last regular-session minutes stay INTRADAY until POSTMARKET.
_CLOSING_AUCTION_WINDOW_MINUTES = {
    "cn": 3,
    "hk": 10,
    "us": 5,
    "jp": 5,
    "kr": 10,
    "tw": 5,
}
_SUPPORTED_ANALYSIS_PHASES = {
    "auto",
    "premarket",
    "intraday",
    "postmarket",
}


class MarketPhase(str, Enum):
    """Regular-session market phase labels for Issue #1386 P0."""

    PREMARKET = "premarket"
    INTRADAY = "intraday"
    LUNCH_BREAK = "lunch_break"
    CLOSING_AUCTION = "closing_auction"
    POSTMARKET = "postmarket"
    NON_TRADING = "non_trading"
    UNKNOWN = "unknown"


@dataclass
class MarketPhaseContext:
    """Runtime market-phase context for stock analysis plumbing."""

    market: Optional[str]
    phase: MarketPhase
    market_local_time: datetime
    session_date: date
    effective_daily_bar_date: date
    is_trading_day: Optional[bool]
    is_market_open_now: Optional[bool]
    is_partial_bar: Optional[bool]
    minutes_to_open: Optional[int] = None
    minutes_to_close: Optional[int] = None
    trigger_source: str = "system"
    analysis_intent: str = "auto"
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe representation for runtime context passing."""
        return {
            "market": self.market,
            "phase": self.phase.value,
            "market_local_time": self.market_local_time.isoformat(),
            "session_date": self.session_date.isoformat(),
            "effective_daily_bar_date": self.effective_daily_bar_date.isoformat(),
            "is_trading_day": self.is_trading_day,
            "is_market_open_now": self.is_market_open_now,
            "is_partial_bar": self.is_partial_bar,
            "minutes_to_open": self.minutes_to_open,
            "minutes_to_close": self.minutes_to_close,
            "trigger_source": self.trigger_source,
            "analysis_intent": self.analysis_intent,
            "warnings": list(self.warnings),
        }


def get_market_for_stock(code: str) -> Optional[str]:
    """
    Infer market region for a stock code.

    Returns:
        'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'tw' | None (None = unrecognized, fail-open: treat as open)
    """
    if not code or not isinstance(code, str):
        return None
    code = (code or "").strip().upper()

    from data_provider import is_us_stock_code, is_us_index_code, is_hk_stock_code

    if is_us_stock_code(code) or is_us_index_code(code):
        return "us"
    if is_hk_stock_code(code):
        return "hk"
    suffix_market = get_suffix_market(code)
    if suffix_market:
        return suffix_market
    # A-share: 6-digit numeric
    if code.isdigit() and len(code) == 6:
        return "cn"
    return None


def is_market_open(market: str, check_date: date) -> bool:
    """
    Check if the given market is open on the given date.

    Fail-open: returns True if exchange-calendars unavailable or date out of range.

    Args:
        market: 'cn' | 'hk' | 'us'
        check_date: Date to check

    Returns:
        True if trading day (or fail-open), False otherwise
    """
    if not _XCALS_AVAILABLE:
        return True
    ex = MARKET_EXCHANGE.get(market)
    if not ex:
        return True
    try:
        cal = xcals.get_calendar(ex)
        session = datetime(check_date.year, check_date.month, check_date.day)
        return cal.is_session(session)
    except Exception as e:
        logger.warning("trading_calendar.is_market_open fail-open: %s", e)
        return True


def get_market_now(
    market: Optional[str], current_time: Optional[datetime] = None
) -> datetime:
    """
    Return current time in the market's local timezone.

    If current_time is naive, treat it as already expressed in the market timezone.
    Unknown markets fall back to the given datetime (or local system time).
    """
    tz_name = MARKET_TIMEZONE.get(market or "")

    if current_time is None:
        if tz_name:
            return datetime.now(ZoneInfo(tz_name))
        return datetime.now()

    if not tz_name:
        return current_time

    tz = ZoneInfo(tz_name)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=tz)
    return current_time.astimezone(tz)


def get_effective_trading_date(
    market: Optional[str], current_time: Optional[datetime] = None
) -> date:
    """
    Resolve the latest reusable daily-bar date for checkpoint/resume logic.

    Rules:
    - Non-trading day / holiday: previous trading session
    - Trading day before market close: previous completed trading session
    - Trading day after market close: current trading session
    - Calendar lookup failure: fail-open to market-local natural date
    """
    market_now = get_market_now(market, current_time=current_time)
    fallback_date = market_now.date()

    if not _XCALS_AVAILABLE:
        return fallback_date

    ex = MARKET_EXCHANGE.get(market or "")
    tz_name = MARKET_TIMEZONE.get(market or "")
    if not ex or not tz_name:
        return fallback_date

    try:
        cal = xcals.get_calendar(ex)
        local_date = market_now.date()

        if not cal.is_session(local_date):
            return cal.date_to_session(local_date, direction="previous").date()

        session = cal.date_to_session(local_date, direction="previous")
        session_close = cal.session_close(session)
        if hasattr(session_close, "tz_convert"):
            close_local = session_close.tz_convert(tz_name).to_pydatetime()
        elif session_close.tzinfo is not None:
            close_local = session_close.astimezone(ZoneInfo(tz_name))
        else:
            close_local = session_close.replace(tzinfo=ZoneInfo(tz_name))

        if market_now >= close_local:
            return session.date()

        return cal.previous_session(session).date()
    except Exception as e:
        logger.warning("trading_calendar.get_effective_trading_date fail-open: %s", e)
        return fallback_date


def resolve_forward_sessions_fail_closed(
    market: Optional[str],
    after_date: date,
    count: int,
) -> Optional[List[date]]:
    """Return exact exchange sessions strictly after ``after_date``.

    This helper is for research/outcome identities and therefore fails closed;
    it must not inherit the fail-open semantics used by runtime scheduling.
    """
    if (
        market not in MARKET_EXCHANGE
        or not _XCALS_AVAILABLE
        or not isinstance(after_date, date)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
    ):
        return None
    try:
        cal = xcals.get_calendar(MARKET_EXCHANGE[market])
        if bool(cal.is_session(after_date)):
            cursor = cal.next_session(cal.date_to_session(after_date, direction="previous"))
        else:
            cursor = cal.date_to_session(after_date, direction="next")
        sessions = [cursor.date()]
        while len(sessions) < count:
            cursor = cal.next_session(cursor)
            sessions.append(cursor.date())
        return sessions
    except Exception as e:
        logger.warning("trading_calendar.resolve_forward_sessions_fail_closed: %s", e)
        return None


def resolve_session_range_fail_closed(
    market: Optional[str],
    start_date: date,
    end_date: date,
) -> Optional[List[date]]:
    """Return the exact inclusive exchange-session range, failing closed."""
    if (
        market not in MARKET_EXCHANGE
        or not _XCALS_AVAILABLE
        or not isinstance(start_date, date)
        or isinstance(start_date, datetime)
        or not isinstance(end_date, date)
        or isinstance(end_date, datetime)
        or start_date > end_date
    ):
        return None
    try:
        # Exchange-calendar's default moving window may start after a lawful
        # older source's first session (e.g. 2004). Construction requires a
        # preceding calendar-day margin for a non-trading range boundary.
        # Actual sessions/holidays still come from the exchange schedule.
        cal = xcals.get_calendar(
            MARKET_EXCHANGE[market],
            start=start_date - timedelta(days=31),
        )
        if not bool(cal.is_session(start_date)) or not bool(cal.is_session(end_date)):
            return None
        return [value.date() for value in cal.sessions_in_range(start_date, end_date)]
    except Exception as exc:
        logger.warning("trading_calendar.resolve_session_range_fail_closed: %s", exc)
        return None


def resolve_latest_completed_session_fail_closed(
    market: Optional[str],
    current_time: Optional[datetime] = None,
) -> Optional[date]:
    """Return the latest fully completed exchange session, failing closed."""
    if market not in MARKET_EXCHANGE or market not in MARKET_TIMEZONE or not _XCALS_AVAILABLE:
        return None
    try:
        cal = xcals.get_calendar(MARKET_EXCHANGE[market])
        market_now = get_market_now(market, current_time=current_time)
        local_date = market_now.date()
        if not bool(cal.is_session(local_date)):
            return cal.date_to_session(local_date, direction="previous").date()

        session = cal.date_to_session(local_date, direction="previous")
        session_close = cal.session_close(session)
        tz_name = MARKET_TIMEZONE[market]
        if hasattr(session_close, "tz_convert"):
            close_local = session_close.tz_convert(tz_name).to_pydatetime()
        elif session_close.tzinfo is not None:
            close_local = session_close.astimezone(ZoneInfo(tz_name))
        else:
            close_local = session_close.replace(tzinfo=ZoneInfo(tz_name))
        if market_now >= close_local:
            return session.date()
        return cal.previous_session(session).date()
    except Exception as e:
        logger.warning("trading_calendar.resolve_latest_completed_session_fail_closed: %s", e)
        return None


def resolve_historical_daily_bar_date(
    market: Optional[str],
    target_date: date,
    phase: Optional[str],
) -> Optional[date]:
    """Resolve the completed daily bar that a historical phase could consume.

    A persisted ``effective_daily_bar_date`` remains the primary authority.
    This fallback is only for older snapshots that still contain a trustworthy
    phase. It fails closed for missing, unknown, or calendar-inconsistent phases.
    """
    if market not in MARKET_EXCHANGE or not _XCALS_AVAILABLE:
        return None

    normalized_phase = str(phase or "").strip().lower()
    if normalized_phase not in {
        "premarket",
        "intraday",
        "lunch_break",
        "closing_auction",
        "postmarket",
        "non_trading",
    }:
        return None

    try:
        cal = xcals.get_calendar(MARKET_EXCHANGE[market])
        is_session = bool(cal.is_session(target_date))

        if normalized_phase in {
            "premarket",
            "intraday",
            "lunch_break",
            "closing_auction",
        }:
            if not is_session:
                return None
            session = cal.date_to_session(target_date, direction="previous")
            return cal.previous_session(session).date()

        if normalized_phase == "postmarket":
            return target_date if is_session else None

        if is_session:
            return None
        return cal.date_to_session(target_date, direction="previous").date()
    except Exception as e:
        logger.warning(
            "trading_calendar.resolve_historical_daily_bar_date fail-closed: %s",
            e,
        )
        return None


def resolve_completed_timeframe_bar_date(
    market: Optional[str],
    target_date: date,
    timeframe: str,
) -> Optional[date]:
    """Resolve the latest completed weekly/monthly bar end at ``target_date``.

    The daily completed-bar owner remains authoritative. This helper only
    projects that completed daily session onto a higher-timeframe boundary and
    fails closed when the exchange calendar cannot prove the boundary.
    """
    normalized = str(timeframe or "").strip().lower()
    period_freq = {
        "1w": "W-FRI",
        "weekly": "W-FRI",
        "1mo": "M",
        "monthly": "M",
    }.get(normalized)
    if period_freq is None or market not in MARKET_EXCHANGE or not _XCALS_AVAILABLE:
        return None

    try:
        cal = xcals.get_calendar(MARKET_EXCHANGE[market])
        if not bool(cal.is_session(target_date)):
            return None
        session = cal.date_to_session(target_date, direction="previous")
        current_period = pd.Timestamp(target_date).to_period(period_freq)
        next_session = cal.next_session(session)
        next_period = pd.Timestamp(next_session.date()).to_period(period_freq)
        if next_period != current_period:
            return target_date

        first_current_session = cal.date_to_session(
            current_period.start_time.date(), direction="next"
        )
        previous_session = cal.previous_session(first_current_session)
        return previous_session.date()
    except Exception as e:
        logger.warning(
            "trading_calendar.resolve_completed_timeframe_bar_date fail-closed: %s",
            e,
        )
        return None


def resolve_session_range_from_observed_sessions_fail_closed(
    observed_calendar_source: Mapping[str, Any],
    start_date: date,
    end_date: date,
) -> Optional[List[date]]:
    """Resolve an exact source-observed inclusive session suffix, fail closed."""
    if (
        not isinstance(start_date, date) or isinstance(start_date, datetime)
        or not isinstance(end_date, date) or isinstance(end_date, datetime)
        or start_date > end_date
    ):
        return None
    try:
        source = validate_observed_session_calendar_source(observed_calendar_source)
        sessions = [date.fromisoformat(item) for item in source["sessions"]]
        if (
            start_date < date.fromisoformat(source["requested_start"])
            or end_date > date.fromisoformat(source["requested_end"])
            or start_date not in sessions or end_date not in sessions
        ):
            return None
        selected = [item for item in sessions if start_date <= item <= end_date]
        return selected if selected and selected[0] == start_date and selected[-1] == end_date else None
    except (TypeError, ValueError):
        return None


def resolve_required_daily_history_start_from_observed_sessions_fail_closed(
    market: Optional[str],
    target_date: date,
    required_bars_by_timeframe: Mapping[str, int],
    observed_calendar_source: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Map method-owned D/W/M bars using a content-bound provider calendar.

    Recent period completion is still proven by the installed exchange owner;
    the provider source only extends older session coverage.  The current
    observation is never promoted to historical PIT or learning authority.
    """
    if (
        market not in MARKET_EXCHANGE
        or not isinstance(target_date, date) or isinstance(target_date, datetime)
        or not isinstance(required_bars_by_timeframe, Mapping)
    ):
        return None
    allowed = {"daily", "weekly", "monthly"}
    normalized: Dict[str, int] = {}
    for timeframe, value in required_bars_by_timeframe.items():
        key = str(timeframe or "").strip().lower()
        if key not in allowed or isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        normalized[key] = value
    if not any(normalized.values()):
        return None
    try:
        source = validate_observed_session_calendar_source(observed_calendar_source)
        if (
            source["market"] != market
            or date.fromisoformat(source["requested_end"]) != target_date
            or date.fromisoformat(source["last_session"]) != target_date
        ):
            return None
        sessions = [date.fromisoformat(item) for item in source["sessions"]]
        start_by_timeframe: Dict[str, date] = {}
        daily_count = normalized.get("daily", 0)
        if daily_count:
            if len(sessions) < daily_count:
                return None
            start_by_timeframe["daily"] = sessions[-daily_count]

        for timeframe, frequency in {"weekly": "W-FRI", "monthly": "M"}.items():
            count = normalized.get(timeframe, 0)
            if not count:
                continue
            completed_through = resolve_completed_timeframe_bar_date(
                market, target_date, timeframe,
            )
            if completed_through is None:
                return None
            completed_sessions = [item for item in sessions if item <= completed_through]
            if not completed_sessions or completed_sessions[-1] != completed_through:
                return None
            ordered_periods = []
            first_session_by_period: Dict[Any, date] = {}
            for session_date in completed_sessions:
                period = pd.Timestamp(session_date).to_period(frequency)
                if period not in first_session_by_period:
                    ordered_periods.append(period)
                    first_session_by_period[period] = session_date
            if len(ordered_periods) < count:
                return None
            earliest_period = ordered_periods[-count]
            start_by_timeframe[timeframe] = first_session_by_period[earliest_period]

        if not start_by_timeframe:
            return None
        start_date = min(start_by_timeframe.values())
        calendar_source_identity = {
            key: source[key] for key in (
                "schema_version", "market", "provider_identity", "provider_route",
                "requested_start", "requested_end", "observed_at", "available_at",
                "source_content_sha256", "source_identity_hash", "session_count",
                "first_session", "last_session", "revision", "vintage",
            )
        }
        return {
            "target_date": target_date,
            "required_bars_by_timeframe": dict(normalized),
            "start_by_timeframe": start_by_timeframe,
            "start_date": start_date,
            "limiting_timeframes": tuple(
                timeframe for timeframe, value in start_by_timeframe.items()
                if value == start_date
            ),
            "calendar_source_identity": calendar_source_identity,
        }
    except (TypeError, ValueError):
        return None


def resolve_required_daily_history_start_fail_closed(
    market: Optional[str],
    target_date: date,
    required_bars_by_timeframe: Mapping[str, int],
) -> Optional[Dict[str, Any]]:
    """Map method-owned D/W/M bar counts to one exact daily-session start.

    The caller owns the method warm-ups.  This calendar owner only converts
    those counts into exchange sessions, so no fixed calendar-day lookback can
    silently survive a method or timeframe change.
    """
    if (
        market not in MARKET_EXCHANGE
        or not _XCALS_AVAILABLE
        or not isinstance(target_date, date)
        or not isinstance(required_bars_by_timeframe, Mapping)
    ):
        return None

    allowed = {"daily", "weekly", "monthly"}
    normalized: Dict[str, int] = {}
    for timeframe, value in required_bars_by_timeframe.items():
        key = str(timeframe or "").strip().lower()
        if key not in allowed or isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        normalized[key] = value
    if not any(normalized.values()):
        return None

    try:
        # The installed exchange_calendars default schedule starts at a moving
        # ~20-year boundary. Method-owned 268-month RSI history can be older.
        # Widen only the exchange calendar's schedule construction, never the
        # provider's data request or method-specific number of completed bars.
        calendar_start = target_date
        daily_count = normalized.get("daily", 0)
        if daily_count:
            # Over-allocate exchange schedule days, then count ACTUAL sessions.
            calendar_start = min(
                calendar_start, target_date - timedelta(days=3 * daily_count)
            )
        for timeframe, frequency in (("weekly", "W-FRI"), ("monthly", "M")):
            count = normalized.get(timeframe, 0)
            if count:
                # One preceding period also handles the unfinished W/M bar.
                earliest_period = (
                    pd.Timestamp(target_date).to_period(frequency) - count
                )
                calendar_start = min(
                    calendar_start, earliest_period.start_time.date()
                )
        # A period's first calendar day may be a weekend/holiday: schedule
        # must start before that date so date_to_session(direction="next")
        # can prove its first session instead of raising DateOutOfBounds.
        cal = xcals.get_calendar(
            MARKET_EXCHANGE[market],
            start=calendar_start - timedelta(days=31),
        )
        if not bool(cal.is_session(target_date)):
            return None
        target_session = cal.date_to_session(target_date, direction="previous")
        start_by_timeframe: Dict[str, date] = {}

        daily_count = normalized.get("daily", 0)
        if daily_count:
            cursor = target_session
            for _ in range(daily_count - 1):
                cursor = cal.previous_session(cursor)
            start_by_timeframe["daily"] = cursor.date()

        period_frequency = {"weekly": "W-FRI", "monthly": "M"}
        for timeframe, frequency in period_frequency.items():
            count = normalized.get(timeframe, 0)
            if not count:
                continue
            completed_through = resolve_completed_timeframe_bar_date(
                market,
                target_date,
                timeframe,
            )
            if completed_through is None:
                return None
            latest_period = pd.Timestamp(completed_through).to_period(frequency)
            earliest_period = latest_period - (count - 1)
            first_session = cal.date_to_session(
                earliest_period.start_time.date(),
                direction="next",
            )
            if pd.Timestamp(first_session.date()).to_period(frequency) != earliest_period:
                return None
            start_by_timeframe[timeframe] = first_session.date()

        if not start_by_timeframe:
            return None
        start_date = min(start_by_timeframe.values())
        return {
            "target_date": target_date,
            "required_bars_by_timeframe": dict(normalized),
            "start_by_timeframe": start_by_timeframe,
            "start_date": start_date,
            "limiting_timeframes": tuple(
                timeframe
                for timeframe, value in start_by_timeframe.items()
                if value == start_date
            ),
        }
    except Exception as e:
        logger.warning(
            "trading_calendar.resolve_required_daily_history_start_fail_closed: %s",
            e,
        )
        return None


def _as_market_datetime(value: Any, tz_name: str) -> Optional[datetime]:
    """
    Convert exchange-calendar timestamps into market-local datetimes.

    Returns None for missing or pandas NaT-like values. Naive datetimes are
    interpreted as already expressed in the target market timezone, matching
    get_market_now()'s current_time contract.
    """
    if value is None:
        return None
    if pd.isna(value):
        return None

    try:
        if isinstance(value, pd.Timestamp):
            if value.tzinfo is None:
                dt = value.to_pydatetime()
            else:
                dt = value.tz_convert(tz_name).to_pydatetime()
        elif isinstance(value, datetime):
            dt = value
        elif hasattr(value, "to_pydatetime"):
            dt = value.to_pydatetime()
        else:
            return None
    except (AttributeError, TypeError, ValueError):
        return None

    tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def infer_market_phase(
    market: Optional[str], current_time: Optional[datetime] = None
) -> MarketPhase:
    """
    Infer the regular-session market phase for a market.

    This P0 helper is intentionally fail-closed: unknown markets, unavailable
    exchange calendars, and calendar errors return ``MarketPhase.UNKNOWN``.
    That differs from ``is_market_open()`` and ``get_effective_trading_date()``,
    which keep their existing fail-open behavior for backwards compatibility.

    ``premarket`` and ``postmarket`` mean before/after the regular trading
    session only; they do not imply that extended-hours quote data is available.
    ``closing_auction`` uses a small per-market near-close heuristic window and
    does not model full exchange auction microstructure.
    """
    if market not in MARKET_EXCHANGE or market not in MARKET_TIMEZONE:
        return MarketPhase.UNKNOWN
    if not _XCALS_AVAILABLE:
        return MarketPhase.UNKNOWN

    ex = MARKET_EXCHANGE[market]
    tz_name = MARKET_TIMEZONE[market]
    market_now = get_market_now(market, current_time=current_time)
    local_date = market_now.date()

    try:
        cal = xcals.get_calendar(ex)
        if not cal.is_session(local_date):
            return MarketPhase.NON_TRADING

        session = cal.date_to_session(local_date, direction="previous")
        session_open = _as_market_datetime(cal.session_open(session), tz_name)
        session_close = _as_market_datetime(cal.session_close(session), tz_name)
        if session_open is None or session_close is None:
            return MarketPhase.UNKNOWN

        if market_now < session_open:
            return MarketPhase.PREMARKET
        if market_now >= session_close:
            return MarketPhase.POSTMARKET

        # Calendars without session_has_break may still expose break timestamps.
        has_break = True
        if hasattr(cal, "session_has_break"):
            has_break = bool(cal.session_has_break(session))

        break_start = None
        break_end = None
        if has_break:
            break_start = _as_market_datetime(cal.session_break_start(session), tz_name)
            break_end = _as_market_datetime(cal.session_break_end(session), tz_name)

        window_minutes = _CLOSING_AUCTION_WINDOW_MINUTES.get(market, 0)
        closing_window_start = session_close - timedelta(minutes=window_minutes)

        if break_start is not None and break_end is not None:
            if market_now < break_start:
                return MarketPhase.INTRADAY
            if market_now < break_end:
                return MarketPhase.LUNCH_BREAK
            if market_now < closing_window_start:
                return MarketPhase.INTRADAY
            return MarketPhase.CLOSING_AUCTION

        if market_now < closing_window_start:
            return MarketPhase.INTRADAY
        return MarketPhase.CLOSING_AUCTION
    except Exception as e:
        logger.warning("trading_calendar.infer_market_phase fail-closed: %s", e)
        return MarketPhase.UNKNOWN


def _add_warning_code(warnings: List[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)


def _phase_booleans(
    phase: MarketPhase,
) -> Tuple[Optional[bool], Optional[bool], Optional[bool]]:
    if phase == MarketPhase.UNKNOWN:
        return None, None, None

    is_trading_day = phase != MarketPhase.NON_TRADING
    is_market_open_now = phase in {
        MarketPhase.INTRADAY,
        MarketPhase.CLOSING_AUCTION,
    }
    is_partial_bar = phase in {
        MarketPhase.INTRADAY,
        MarketPhase.LUNCH_BREAK,
        MarketPhase.CLOSING_AUCTION,
    }
    return is_trading_day, is_market_open_now, is_partial_bar


def _session_open_close_for_today(
    market: str,
    market_now: datetime,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    ex = MARKET_EXCHANGE.get(market)
    tz_name = MARKET_TIMEZONE.get(market)
    if not ex or not tz_name or not _XCALS_AVAILABLE:
        return None, None

    cal = xcals.get_calendar(ex)
    local_date = market_now.date()
    if not cal.is_session(local_date):
        return None, None

    session = cal.date_to_session(local_date, direction="previous")
    return (
        _as_market_datetime(cal.session_open(session), tz_name),
        _as_market_datetime(cal.session_close(session), tz_name),
    )


def _phase_minutes(
    market: Optional[str],
    market_now: datetime,
    phase: MarketPhase,
) -> Tuple[Optional[int], Optional[int], bool]:
    if (
        market not in MARKET_EXCHANGE
        or phase in {MarketPhase.UNKNOWN, MarketPhase.NON_TRADING, MarketPhase.POSTMARKET}
    ):
        return None, None, False
    if not _XCALS_AVAILABLE:
        return None, None, False

    try:
        session_open, session_close = _session_open_close_for_today(market, market_now)
    except Exception as e:
        logger.warning("trading_calendar.market_phase_context calendar_error: %s", e)
        return None, None, True

    if session_open is None or session_close is None:
        return None, None, False

    if phase == MarketPhase.PREMARKET and market_now < session_open:
        seconds = (session_open - market_now).total_seconds()
        return max(0, int(seconds // 60)), None, False

    if phase in {
        MarketPhase.INTRADAY,
        MarketPhase.LUNCH_BREAK,
        MarketPhase.CLOSING_AUCTION,
    } and market_now < session_close:
        seconds = (session_close - market_now).total_seconds()
        return None, max(0, int(seconds // 60)), False

    return None, None, False


def _normalize_analysis_phase(
    analysis_phase: Optional[str],
    analysis_intent: Optional[str],
) -> str:
    def _coerce(value: Optional[str]) -> str:
        if isinstance(value, MarketPhase):
            return value.value
        return str(value or "").strip().lower()

    requested = _coerce(analysis_phase) or "auto"
    legacy_intent = _coerce(analysis_intent)
    if requested == "auto" and legacy_intent and legacy_intent != "auto":
        requested = legacy_intent
    if requested not in _SUPPORTED_ANALYSIS_PHASES:
        raise ValueError(
            f"invalid analysis_phase: {requested}. "
            f"Must be one of {sorted(_SUPPORTED_ANALYSIS_PHASES)}"
        )
    return requested


def build_market_phase_context(
    *,
    market: Optional[str],
    current_time: Optional[datetime] = None,
    trigger_source: str = "system",
    analysis_intent: str = "auto",
    analysis_phase: str = "auto",
) -> MarketPhaseContext:
    """
    Build a JSON-safe runtime market-phase context for analysis plumbing.

    ``analysis_phase="auto"`` keeps calendar inference. Explicit supported
    phases override only the phase and derived flags/minute fields; they do
    not rewrite market-local time or the effective daily-bar date. The legacy
    ``analysis_intent`` argument remains a compatibility alias when
    ``analysis_phase`` is left as ``auto``.
    """
    requested_phase = _normalize_analysis_phase(analysis_phase, analysis_intent)
    market_now = get_market_now(market, current_time=current_time)
    warnings: List[str] = []

    if market not in MARKET_EXCHANGE or market not in MARKET_TIMEZONE:
        phase = MarketPhase.UNKNOWN
        _add_warning_code(warnings, "unknown_market")
    else:
        if not _XCALS_AVAILABLE:
            _add_warning_code(warnings, "calendar_unavailable")
        if requested_phase == "auto":
            phase = infer_market_phase(market, current_time=current_time)
            if phase == MarketPhase.UNKNOWN and _XCALS_AVAILABLE:
                _add_warning_code(warnings, "calendar_error")
        else:
            phase = MarketPhase(requested_phase)

    if requested_phase != "auto" and phase == MarketPhase.UNKNOWN:
        phase = MarketPhase(requested_phase)

    effective_daily_bar_date = get_effective_trading_date(
        market,
        current_time=current_time,
    )
    is_trading_day, is_market_open_now, is_partial_bar = _phase_booleans(phase)
    minutes_to_open, minutes_to_close, minutes_calendar_error = _phase_minutes(
        market,
        market_now,
        phase,
    )
    if minutes_calendar_error:
        _add_warning_code(warnings, "calendar_error")

    return MarketPhaseContext(
        market=market,
        phase=phase,
        market_local_time=market_now,
        session_date=market_now.date(),
        effective_daily_bar_date=effective_daily_bar_date,
        is_trading_day=is_trading_day,
        is_market_open_now=is_market_open_now,
        is_partial_bar=is_partial_bar,
        minutes_to_open=minutes_to_open,
        minutes_to_close=minutes_to_close,
        trigger_source=trigger_source or "system",
        analysis_intent=requested_phase,
        warnings=warnings,
    )


def get_open_markets_today() -> Set[str]:
    """
    Get markets that are open today (by each market's local timezone).

    Returns:
        Set of market keys that are trading today
    """
    if not _XCALS_AVAILABLE:
        return set(MARKET_TIMEZONE)
    result: Set[str] = set()
    for mkt, tz_name in MARKET_TIMEZONE.items():
        try:
            tz = ZoneInfo(tz_name)
            today = datetime.now(tz).date()
            if is_market_open(mkt, today):
                result.add(mkt)
        except Exception as e:
            logger.warning("get_open_markets_today fail-open for %s: %s", mkt, e)
            result.add(mkt)
    return result


def compute_effective_region(
    config_region: str, open_markets: Set[str]
) -> Optional[str]:
    """
    Compute effective market review region given config and open markets.

    Args:
        config_region: From MARKET_REVIEW_REGION ('cn' | 'hk' | 'us' | 'jp' | 'kr' | 'both' or comma subset)
        open_markets: Markets open today

    Returns:
        None: caller uses config default (check disabled)
        '': all relevant markets closed, skip market review
        'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'both': effective subset for today
    """
    markets = ("cn", "hk", "us", "jp", "kr")
    normalized = (config_region or "cn").strip().lower()
    if not normalized:
        normalized = "cn"

    requested = {
        item.strip() for item in normalized.split(",") if item.strip()
    }
    if not requested:
        requested = {"cn"}

    if "both" in requested:
        requested = set(markets)
    else:
        # Ignore invalid tokens and only keep known markets.
        requested = {item for item in requested if item in markets}

    if not requested:
        # No valid market token left after filtering; follow parser fallback behavior.
        requested = {"cn"}

    # single explicit region: keep single-region return semantics (empty when closed)
    if len(requested) == 1:
        region = next(iter(requested))
        return region if region in open_markets else ""

    # multi-region subset: keep only markets open today, in canonical order
    open_selected = [m for m in markets if m in requested and m in open_markets]
    if not open_selected:
        return ""
    if len(open_selected) == 1:
        return open_selected[0]
    return ",".join(open_selected)
