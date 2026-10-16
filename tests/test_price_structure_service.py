# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.services.price_structure_service import build_price_structure_context


def _event_history(*, source: str | None = "Fetcher") -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=14)
    highs = [8.0, 8.5, 9.0, 9.5, 10.0, 12.0, 10.5, 10.0, 11.0, 12.6, 12.5, 12.4, 12.0, 11.8]
    lows = [7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 9.0, 9.0, 9.5, 11.5, 11.8, 11.4, 10.8, 10.5]
    closes = [7.5, 8.0, 8.5, 9.0, 9.5, 11.5, 10.0, 9.5, 11.0, 12.3, 12.2, 11.8, 11.5, 11.0]
    rows = []
    for day, high, low, close in zip(dates, highs, lows, closes):
        row = {"date": day, "open": close, "high": high, "low": low, "close": close}
        if source is not None:
            row["data_source"] = source
        rows.append(row)
    return pd.DataFrame(rows)


def test_pivot_confirmation_is_prefix_safe_and_never_backfills_future_knowledge():
    history = _event_history()
    before_confirmation = history.iloc[6]["date"].date()
    evaluation_target = history.iloc[9]["date"].date()
    expected_confirmed_at = history.iloc[7]["date"].date()

    before = build_price_structure_context(
        stock_code="600519", history=history, target_date=before_confirmation, market="cn"
    )
    confirmed_from_full = build_price_structure_context(
        stock_code="600519", history=history, target_date=evaluation_target, market="cn"
    )
    confirmed_from_prefix = build_price_structure_context(
        stock_code="600519", history=history.iloc[:10].copy(), target_date=evaluation_target, market="cn"
    )

    assert not any(item["origin_time"] == history.iloc[5]["date"].date().isoformat() for item in before["pivots"])
    pivot = next(item for item in confirmed_from_full["pivots"] if item["origin_time"] == history.iloc[5]["date"].date().isoformat())
    assert pivot["kind"] == "HIGH"
    assert pivot["confirmed_at"] == expected_confirmed_at.isoformat()
    assert pivot["provisional"] is False
    assert confirmed_from_full["pivots"] == confirmed_from_prefix["pivots"]
    assert confirmed_from_full["structure_event"] == confirmed_from_prefix["structure_event"]


def test_breakout_retest_and_failed_breakout_are_completed_close_states():
    history = _event_history()

    breakout = build_price_structure_context(
        stock_code="600519", history=history, target_date=history.iloc[9]["date"].date(), market="cn"
    )
    retest = build_price_structure_context(
        stock_code="600519", history=history, target_date=history.iloc[10]["date"].date(), market="cn"
    )
    failed = build_price_structure_context(
        stock_code="600519", history=history, target_date=history.iloc[11]["date"].date(), market="cn"
    )

    assert breakout["structure_event"]["state"] == "UP_BREAKOUT"
    assert breakout["structure_event"]["level"] == 12.0
    assert retest["structure_event"]["state"] == "UP_BREAKOUT_RETEST_HOLD"
    assert failed["structure_event"]["state"] == "FAILED_UP_BREAKOUT"
    assert failed["range_state"] == "FAILED_BREAKOUT_RETURNED_BELOW_LEVEL"
    assert failed["hard_veto"] is False
    assert failed["independent_action_authority"] is False



def test_completed_30m_breakout_retest_failure_preserves_source_known_at() -> None:
    """The accepted daily geometry is reused on 30m bar-end timestamps."""
    from src.services.price_structure_service import build_confirmed_30m_structure_context

    frame = _event_history()
    bar_ends = pd.date_range("2026-10-14T01:30:00Z", periods=len(frame), freq="30min")
    frame["date"] = bar_ends
    frame["available_at"] = bar_ends + pd.Timedelta(minutes=7)

    breakout = build_confirmed_30m_structure_context(frame.iloc[:10].copy())
    retest = build_confirmed_30m_structure_context(frame.iloc[:11].copy())
    failed = build_confirmed_30m_structure_context(frame.iloc[:12].copy())
    assert [x["structure_event"]["state"] for x in (breakout, retest, failed)] == [
        "UP_BREAKOUT", "UP_BREAKOUT_RETEST_HOLD", "FAILED_UP_BREAKOUT",
    ]
    pivot = next(p for p in failed["pivots"] if p["kind"] == "HIGH" and p["price"] == 12.0)
    assert pivot["origin_time"] == bar_ends[5].isoformat()
    assert pivot["confirmed_at"] == bar_ends[7].isoformat()
    assert pivot["confirmed_available_at"] == (bar_ends[7] + pd.Timedelta(minutes=7)).isoformat()
    assert pivot["provisional"] is False
    assert pivot["invalidation"]["state"] == "TRIGGERED"
    assert failed["structure_event"]["event_available_at"] == (
        bar_ends[11] + pd.Timedelta(minutes=7)
    ).isoformat()
    assert failed["strategy_admitted"] is False
    assert failed["historical_replay_eligible"] is False
    assert "FAILED_UP_BREAKOUT" in failed["summary"]
    assert "COMPLETED_CLOSE_BELOW_BROKEN_RESISTANCE" in failed["summary"]
    assert bar_ends[11].isoformat() in failed["summary"]
    assert (bar_ends[11] + pd.Timedelta(minutes=7)).isoformat() in failed["summary"]



def test_completed_30m_machine_event_and_invalidation_survive_existing_email_html() -> None:
    """Accepted event state, source times and rule survive the real Markdown->HTML consumer."""
    from html.parser import HTMLParser
    from src.formatters import markdown_to_html_document
    from src.services.price_structure_service import build_confirmed_30m_structure_context

    class VisibleText(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, value):
            self.parts.append(value)

    frame = _event_history()
    ends = pd.date_range("2026-10-14T01:30:00Z", periods=len(frame), freq="30min")
    frame["date"] = ends
    frame["available_at"] = ends + pd.Timedelta(minutes=7)
    current = build_confirmed_30m_structure_context(frame.iloc[:12].copy())
    event = current["structure_event"]
    assert event["state"] == "FAILED_UP_BREAKOUT"

    visible = VisibleText()
    visible.feed(markdown_to_html_document("**30m结构观察**: " + current["summary"]))
    text = "".join(visible.parts)
    assert event["state"] in text
    assert event["invalidation"] in text
    assert event["event_time"] in text
    assert event["event_available_at"] in text
    assert "仅为价格结构观察，不构成独立交易触发" in text


def test_missing_or_mixed_source_never_claims_ready_structure():
    missing_source = _event_history(source=None)
    target = missing_source.iloc[-1]["date"].date()
    missing = build_price_structure_context(
        stock_code="600519", history=missing_source, target_date=target, market="cn"
    )
    assert missing["status"] == "PARTIAL"
    assert missing["reason"] == "SOURCE_ALIGNMENT_UNPROVEN"
    assert missing["historical_replay_eligible"] is False

    mixed_source = _event_history()
    mixed_source.loc[mixed_source.index[-1], "data_source"] = "OtherFetcher"
    mixed = build_price_structure_context(
        stock_code="600519", history=mixed_source, target_date=target, market="cn"
    )
    assert mixed["status"] == "PARTIAL"
    assert mixed["source_alignment"]["status"] == "UNPROVEN"
    assert mixed["source_alignment"]["coverage"] == "FULL_NORMALIZED_HISTORY"
    assert mixed["source_alignment"]["observations"] == len(mixed_source)


def test_target_date_and_invalid_ohlc_fail_closed():
    history = _event_history()
    future_target = (history.iloc[-1]["date"] + pd.Timedelta(days=1)).date()
    missing = build_price_structure_context(
        stock_code="600519", history=history, target_date=future_target, market="cn"
    )
    assert missing["status"] == "MISSING"
    assert missing["reason"] == "TARGET_DATE_BAR_MISSING"

    bad = history.copy()
    bad.loc[bad.index[-1], "high"] = bad.loc[bad.index[-1], "low"] - 1.0
    unknown = build_price_structure_context(
        stock_code="600519", history=bad, target_date=bad.iloc[-1]["date"].date(), market="cn"
    )
    assert unknown["status"] == "UNKNOWN"
    assert unknown["reason"] == "INVALID_OHLC"


def test_pipeline_wires_one_price_structure_context_into_normal_and_agent_factor_paths():
    source = (Path(__file__).resolve().parents[1] / "src" / "core" / "pipeline.py").read_text(encoding="utf-8")
    assert "from src.services.price_structure_service import build_price_structure_context" in source
    assert "price_structure_context = build_price_structure_context(" in source
    assert source.count("price_structure_context=price_structure_context") >= 3


def test_full_history_source_alignment_rejects_old_source_pivot_outside_prior_60_row_suffix():
    dates = pd.bdate_range("2025-01-02", periods=100)
    rows = []
    for i, day in enumerate(dates):
        close = 50.0 + i * 0.05
        high = close + 1.0
        low = close - 1.0
        if i == 20:
            high = 100.0
        if i == 80:
            close, high, low = 101.0, 102.0, 99.0
        elif i > 80:
            close = 101.0 + (i - 80) * 0.05
            high, low = close + 1.0, close - 1.0
        rows.append(
            {
                "date": day,
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "data_source": "OldFetcher" if i < 40 else "CurrentFetcher",
            }
        )
    history = pd.DataFrame(rows)
    context = build_price_structure_context(
        stock_code="600519",
        history=history,
        target_date=history.iloc[-1]["date"].date(),
        market="cn",
    )
    assert context["status"] == "PARTIAL"
    assert context["reason"] == "SOURCE_ALIGNMENT_UNPROVEN"
    assert context["historical_replay_eligible"] is False
    assert context["source_alignment"] == {
        "status": "UNPROVEN",
        "sources": ["CurrentFetcher", "OldFetcher"],
        "rows_complete": True,
        "coverage": "FULL_NORMALIZED_HISTORY",
        "observations": 100,
        "start_date": history.iloc[0]["date"].date().isoformat(),
        "end_date": history.iloc[-1]["date"].date().isoformat(),
    }
