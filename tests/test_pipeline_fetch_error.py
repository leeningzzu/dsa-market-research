# -*- coding: utf-8 -*-
"""Regression tests for pipeline data-fetch error handling."""

from datetime import date, datetime, timezone
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pandas as pd

from data_provider.daily_data_identity import (
    build_daily_data_identity,
    canonical_identity_json,
)

from src.core.pipeline import StockAnalysisPipeline


def _typed_cache_rows(
    sessions,
    *,
    code: str,
    observed_at: datetime,
):
    frame = pd.DataFrame([
        {
            "date": value.date() if hasattr(value, "date") else value,
            "open": 10.0 + index,
            "high": 11.0 + index,
            "low": 9.0 + index,
            "close": 10.5 + index,
            "volume": 1000.0 + index,
            "amount": 10500.0 + index,
            "pct_chg": 0.1 + index,
            "data_source": "BaostockFetcher",
        }
        for index, value in enumerate(sessions)
    ])
    identity = build_daily_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="unit.cache",
        actual_response_branch="unit:qfq",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="unit-test",
        requested_start=str(frame.iloc[0]["date"]),
        requested_end=str(frame.iloc[-1]["date"]),
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        identity_state="OBSERVED",
        observed_at=observed_at,
        instrument=code,
    )
    identity_json = canonical_identity_json(identity)
    return [
        SimpleNamespace(
            code=code,
            **record,
            data_identity_json=identity_json,
            data_identity_hash=identity["identity_hash"],
        )
        for record in frame.to_dict("records")
    ]


class PipelineFetchErrorTestCase(unittest.TestCase):
    """`fetch_and_save_stock_data` should preserve the original exception."""

    def test_fetch_and_save_handles_stock_name_lookup_failure(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.fetcher_manager = MagicMock()
        pipeline.db = MagicMock()
        pipeline.fetcher_manager.get_stock_name.side_effect = RuntimeError("name lookup failed")

        success, error = StockAnalysisPipeline.fetch_and_save_stock_data(pipeline, "600519")

        self.assertFalse(success)
        self.assertIn("name lookup failed", error or "")

    @patch.object(
        StockAnalysisPipeline,
        "_resolve_resume_target_date",
        return_value=date(2026, 3, 27),
    )
    def test_fetch_and_save_uses_effective_trading_date_for_resume_check(self, _mock_target):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.fetcher_manager = MagicMock()
        pipeline.db = MagicMock()
        pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
        pipeline.db.has_today_data.return_value = True
        # A genuine resume nontrigger needs the complete *current method*
        # history, not just a mocked latest-date flag.
        import exchange_calendars as xcals
        from src.core.pipeline import _current_daily_window_plan

        effective = date(2026, 3, 27)
        window = _current_daily_window_plan(market="cn", target_date=effective)
        sessions = xcals.get_calendar("XSHG").sessions_in_range(
            window["start_date"], effective,
        )
        current_time = datetime(2026, 3, 28, 1, 0, tzinfo=timezone.utc)
        pipeline.db.get_data_range.return_value = _typed_cache_rows(
            sessions,
            code="600519",
            observed_at=datetime(2026, 3, 27, 16, 0, tzinfo=timezone.utc),
        )

        success, error = StockAnalysisPipeline.fetch_and_save_stock_data(
            pipeline,
            "600519",
            current_time=current_time,
        )

        self.assertTrue(success)
        self.assertIsNone(error)
        _mock_target.assert_called_once_with("600519", current_time=current_time)
        pipeline.db.has_today_data.assert_called_once_with("600519", date(2026, 3, 27))
        pipeline.db.get_data_range.assert_called_once_with(
            "600519", window["start_date"], effective,
        )
        pipeline.fetcher_manager.get_daily_data.assert_not_called()

    def test_resolve_resume_target_date_normalizes_supported_a_share_formats(self):
        with patch("src.core.pipeline.get_market_for_stock", return_value="cn") as mock_market, patch(
            "src.core.pipeline.get_effective_trading_date",
            return_value=date(2026, 3, 27),
        ) as mock_target:
            for code in ("SH600519", "000001.SZ", "BJ920748"):
                result = StockAnalysisPipeline._resolve_resume_target_date(code)
                self.assertEqual(result, date(2026, 3, 27))

        self.assertEqual(
            [args.args[0] for args in mock_market.call_args_list],
            ["600519", "000001", "920748"],
        )
        self.assertEqual(mock_target.call_count, 3)


if __name__ == "__main__":
    unittest.main()
