# -*- coding: utf-8 -*-
"""Regression tests for post-merge Tushare follow-up fixes."""

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    json_repair_available = importlib.util.find_spec("json_repair") is not None
except ValueError:
    json_repair_available = "json_repair" in sys.modules

if not json_repair_available and "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.tushare_fetcher import TushareFetcher
from data_provider.daily_data_identity import extract_daily_data_identity
from data_provider.intraday_data_identity import extract_intraday_data_identity
from data_provider.base import DataFetchError, RateLimitError



class TestTushareFetcherFollowUps(unittest.TestCase):
    """Cover rate limiting and cross-day trade-calendar refresh behavior."""

    @staticmethod
    def _make_fetcher() -> TushareFetcher:
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = 2
        return fetcher

    def test_get_trade_time_refreshes_trade_calendar_when_day_changes(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.side_effect = [
            pd.DataFrame({"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}),
            pd.DataFrame({"cal_date": ["20260318", "20260317"], "is_open": [1, 1]}),
        ]

        with patch.object(
            fetcher,
            "_get_china_now",
            side_effect=[
                datetime(2026, 3, 17, 20, 0),
                datetime(2026, 3, 17, 20, 0),
                datetime(2026, 3, 18, 20, 0),
                datetime(2026, 3, 18, 20, 0),
            ],
        ), patch.object(fetcher, "_check_rate_limit") as rate_limit_mock:
            self.assertEqual(fetcher.get_trade_time(early_time="00:00", late_time="19:00"), "20260317")
            self.assertEqual(fetcher.get_trade_time(early_time="00:00", late_time="19:00"), "20260318")

        self.assertEqual(fetcher._api.trade_cal.call_count, 2)
        self.assertEqual(rate_limit_mock.call_count, 2)
    def test_get_trade_time_returns_latest_trade_date_on_non_trade_day(self) -> None:
        """Non-trade day (e.g. Saturday) should return the most recent trade
        date (Friday), not the one before it (Thursday).  Fixes #1009."""
        fetcher = self._make_fetcher()
        # 2026-03-21 is Saturday; Friday 20 and Thursday 19 are trade dates
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {
                "cal_date": ["20260314", "20260315", "20260316",
                             "20260317", "20260318", "20260319",
                             "20260320", "20260321"],
                "is_open": [0, 0, 1, 1, 1, 1, 1, 0],
            }
        )

        with patch.object(
            fetcher,
            "_get_china_now",
            # called twice: once by get_trade_time, once by _get_trade_dates
            side_effect=[datetime(2026, 3, 21, 10, 0)] * 2,
        ), patch.object(fetcher, "_check_rate_limit"):
            result = fetcher.get_trade_time(early_time="00:00", late_time="19:00")

        # Should be Friday (20th), NOT Thursday (19th)
        self.assertEqual(result, "20260320")

    def test_get_trade_time_trade_day_before_data_ready_returns_previous(self) -> None:
        """On a trade day within the early-late window, should return the
        previous trade date (data not ready yet for today)."""
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {
                "cal_date": ["20260319", "20260320"],
                "is_open": [1, 1],
            }
        )

        with patch.object(
            fetcher,
            "_get_china_now",
            # Friday 10:00 AM - within 00:00~19:00 window, data not ready
            side_effect=[datetime(2026, 3, 20, 10, 0)] * 2,
        ), patch.object(fetcher, "_check_rate_limit"):
            result = fetcher.get_trade_time(early_time="00:00", late_time="19:00")

        # Data not ready, should fall back to Thursday (19th)
        self.assertEqual(result, "20260319")
        
          
    def test_get_sector_rankings_rate_limits_calendar_and_rankings_api(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.moneyflow_ind_ths.return_value = pd.DataFrame(
            {
                "industry": ["AI", "消费"],
                "pct_change": [1.8, -0.6],
            }
        )

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 16, 0)), patch.object(
            fetcher, "_check_rate_limit"
        ) as rate_limit_mock:
            top, bottom = fetcher.get_sector_rankings(n=1)

        self.assertEqual(top, [{"name": "AI", "change_pct": 1.8}])
        self.assertEqual(bottom, [{"name": "消费", "change_pct": -0.6}])
        self.assertEqual(rate_limit_mock.call_count, 2)

    def test_get_chip_distribution_rate_limits_all_tushare_calls(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.cyq_chips.return_value = pd.DataFrame(
            {
                "price": [9.0, 10.0, 11.0],
                "percent": [20.0, 50.0, 30.0],
            }
        )
        fetcher._api.daily.return_value = pd.DataFrame({"close": [10.5]})

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 20, 0)), patch.object(
            fetcher, "_check_rate_limit"
        ) as rate_limit_mock:
            chip = fetcher.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected chip distribution data")
        self.assertEqual(chip.date, "2026-03-17")
        self.assertAlmostEqual(chip.profit_ratio, 0.7)
        self.assertAlmostEqual(chip.avg_cost, 10.1)
        self.assertAlmostEqual(chip.concentration_90, 0.1)
        self.assertAlmostEqual(chip.concentration_70, 0.1)
        self.assertEqual(rate_limit_mock.call_count, 3)

    def test_convert_stock_code_accepts_exchange_prefixed_a_share(self) -> None:
        fetcher = self._make_fetcher()

        self.assertEqual(fetcher._convert_stock_code("SZ000001"), "000001.SZ")
        self.assertEqual(fetcher._convert_stock_code("SH600519"), "600519.SH")
        self.assertEqual(fetcher._convert_stock_code("605218"), "605218.SH")
        self.assertEqual(fetcher._convert_stock_code("600519.SS"), "600519.SH")

    @patch.dict(sys.modules, {"tushare": MagicMock()})
    def test_legacy_realtime_quote_keeps_sz_hint_as_stock_symbol(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.quotation.side_effect = Exception("quota")

        tushare_module = sys.modules["tushare"]
        tushare_module.get_realtime_quotes.return_value = pd.DataFrame(
            [
                {
                    "name": "平安银行",
                    "price": "10.94",
                    "pre_close": "10.88",
                    "volume": "1000",
                    "amount": "2000",
                    "high": "11.00",
                    "low": "10.80",
                    "open": "10.90",
                }
            ]
        )

        quote = fetcher.get_realtime_quote("SZ000001")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.code, "000001")
        self.assertEqual(quote.name, "平安银行")
        tushare_module.get_realtime_quotes.assert_called_once_with("000001")


class TestTushareTypedPriceSources(unittest.TestCase):
    """B1 candidate: typed daily and completed 5m observations, no network."""

    @staticmethod
    def _make_fetcher() -> TushareFetcher:
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = -1
        return fetcher

    @staticmethod
    def _daily_rows(ts_code: str) -> pd.DataFrame:
        return pd.DataFrame({
            "ts_code": [ts_code, ts_code],
            "trade_date": ["20260317", "20260316"],
            "open": [10.0, 9.8], "high": [10.4, 10.1],
            "low": [9.9, 9.7], "close": [10.2, 10.0],
            "pre_close": [10.0, 9.9], "change": [0.2, 0.1],
            "pct_chg": [2.0, 1.01], "vol": [1234.0, 1000.0],
            "amount": [12586.0, 10000.0],
        })

    @staticmethod
    def _minute_rows(ts_code: str) -> pd.DataFrame:
        labels = list(pd.date_range(
            "2026-03-17 09:35:00", "2026-03-17 11:30:00", freq="5min"
        ))
        labels += list(pd.date_range(
            "2026-03-17 13:05:00", "2026-03-17 15:00:00", freq="5min"
        ))
        return pd.DataFrame([
            {
                "ts_code": ts_code,
                "trade_time": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                "open": 10.0 + index / 100,
                "close": 10.05 + index / 100,
                "high": 10.10 + index / 100,
                "low": 9.95 + index / 100,
                "vol": 1000.0 + index,
                "amount": 10050.0 + index,
            }
            for index, stamp in enumerate(labels)
        ])

    def test_stock_and_etf_daily_are_typed_raw_share_cny(self) -> None:
        for code, api_name, ts_code in (
            ("600519", "daily", "600519.SH"),
            ("510300", "fund_daily", "510300.SH"),
        ):
            fetcher = self._make_fetcher()
            getattr(fetcher._api, api_name).return_value = self._daily_rows(ts_code)
            with patch.object(fetcher, "_check_rate_limit"):
                frame = fetcher.get_daily_data(
                    code, start_date="2026-03-16", end_date="2026-03-17"
                )
            identity = extract_daily_data_identity(frame, strict=True)
            self.assertEqual(identity["identity_state"], "OBSERVED")
            self.assertEqual(identity["provider_identity"], "TushareFetcher")
            self.assertEqual(identity["provider_route"], "tushare." + api_name)
            self.assertEqual(identity["instrument"], code)
            self.assertEqual(identity["observed_adjustment_basis"], "raw")
            self.assertEqual(identity["volume_unit"], "share")
            self.assertEqual(identity["amount_unit"], "CNY")
            self.assertEqual(float(frame.iloc[-1]["volume"]), 123400.0)
            self.assertEqual(float(frame.iloc[-1]["amount"]), 12586000.0)

    def test_stock_and_etf_historical_5m_are_canonical_typed_sources(self) -> None:
        for code, api_name, ts_code in (
            ("600519", "stk_mins", "600519.SH"),
            ("510300", "etf_mins", "510300.SH"),
        ):
            fetcher = self._make_fetcher()
            getattr(fetcher._api, api_name).return_value = self._minute_rows(ts_code)
            with patch.object(fetcher, "_check_rate_limit"):
                frame = fetcher.get_intraday_data(
                    code,
                    start_date="2026-03-17",
                    end_date="2026-03-17",
                    frequency="5",
                    observed_at=datetime(2026, 3, 17, 8, 0, tzinfo=timezone.utc),
                )
            identity = extract_intraday_data_identity(frame, strict=True)
            self.assertEqual(len(frame), 48)
            self.assertEqual(identity["provider_identity"], "TushareFetcher")
            self.assertEqual(identity["provider_route"], "tushare." + api_name)
            self.assertEqual(identity["timeframe"], "5m")
            self.assertEqual(identity["observed_adjustment_basis"], "raw")
            self.assertEqual(identity["volume_unit"], "share")
            self.assertEqual(identity["amount_unit"], "CNY")
            self.assertEqual(identity["query_identity"]["ts_code"], ts_code)
            self.assertEqual(identity["query_identity"]["freq"], "5min")
            getattr(fetcher._api, api_name).assert_called_once_with(
                ts_code=ts_code,
                freq="5min",
                start_date="2026-03-17 00:00:00",
                end_date="2026-03-17 23:59:59",
            )

    def test_intraday_rejects_wrong_frequency_or_response_instrument(self) -> None:
        fetcher = self._make_fetcher()
        with self.assertRaisesRegex(DataFetchError, "canonical 5m"):
            fetcher.get_intraday_data(
                "600519", start_date="2026-03-17", end_date="2026-03-17",
                frequency="15",
            )
        fetcher._api.stk_mins.return_value = self._minute_rows("600000.SH")
        with patch.object(fetcher, "_check_rate_limit"):
            with self.assertRaisesRegex(DataFetchError, "instrument mismatch"):
                fetcher.get_intraday_data(
                    "600519", start_date="2026-03-17", end_date="2026-03-17",
                    frequency="5",
                    observed_at=datetime(2026, 3, 17, 8, 0, tzinfo=timezone.utc),
                )

    def test_daily_rejects_response_instrument_drift(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.daily.return_value = self._daily_rows("600000.SH")
        with patch.object(fetcher, "_check_rate_limit"):
            with self.assertRaisesRegex(DataFetchError, "instrument mismatch"):
                fetcher.get_daily_data(
                    "600519", start_date="2026-03-16", end_date="2026-03-17"
                )

    def test_response_hash_and_observation_clock_are_not_upstream_revision_or_vintage(self) -> None:
        """Known-at receipt identity must not invent the provider's revision chain."""
        daily = self._make_fetcher()
        daily._api.daily.return_value = self._daily_rows("600519.SH")
        with patch.object(daily, "_check_rate_limit"):
            daily_frame = daily.get_daily_data(
                "600519", start_date="2026-03-16", end_date="2026-03-17"
            )
        daily_identity = extract_daily_data_identity(daily_frame, strict=True)
        self.assertIsNotNone(daily_identity.get("raw_response_sha256"))
        self.assertEqual(daily_identity.get("revision", "UNKNOWN"), "UNKNOWN")
        self.assertEqual(daily_identity.get("vintage", "UNKNOWN"), "UNKNOWN")

        intraday = self._make_fetcher()
        intraday._api.stk_mins.return_value = self._minute_rows("600519.SH")
        with patch.object(intraday, "_check_rate_limit"):
            minute_frame = intraday.get_intraday_data(
                "600519",
                start_date="2026-03-17",
                end_date="2026-03-17",
                frequency="5",
                observed_at=datetime(2026, 3, 17, 8, 0, tzinfo=timezone.utc),
            )
        minute_identity = extract_intraday_data_identity(minute_frame, strict=True)
        self.assertIsNotNone(minute_identity.get("source_rows_sha256"))
        self.assertEqual(minute_identity.get("revision", "UNKNOWN"), "UNKNOWN")
        self.assertEqual(minute_identity.get("vintage", "UNKNOWN"), "UNKNOWN")

    def test_intraday_permission_failure_is_fail_closed(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.stk_mins.side_effect = Exception("权限不足")
        with patch.object(fetcher, "_check_rate_limit"):
            with self.assertRaisesRegex(RateLimitError, "权限或配额"):
                fetcher.get_intraday_data(
                    "600519", start_date="2026-03-17", end_date="2026-03-17",
                    frequency="5",
                )
