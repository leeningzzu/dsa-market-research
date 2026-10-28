from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from data_provider.baostock_fetcher import BaostockFetcher
from data_provider.base import DataFetchError
from data_provider.daily_data_identity import extract_daily_data_identity, sha256_payload
from data_provider.intraday_data_identity import extract_intraday_data_identity


class _Result:
    fields = ["date", "open", "high", "low", "close", "volume", "amount", "pctChg"]

    def __init__(self, rows, *, error_code: str = "0", error_msg: str = "") -> None:
        self.error_code = error_code
        self.error_msg = error_msg
        self._rows = list(rows)
        self._index = -1

    def next(self) -> bool:
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self):
        return self._rows[self._index]


class _NamedResult:
    def __init__(self, fields, rows, *, error_code: str = "0", error_msg: str = ""):
        self.fields = list(fields)
        self.error_code = error_code
        self.error_msg = error_msg
        self._rows = [list(row) for row in rows]
        self._index = -1

    def next(self) -> bool:
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self):
        return self._rows[self._index]



class _FakeBaostock:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls = []

    def query_history_k_data_plus(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@contextmanager
def _session(fake):
    yield fake


def test_baostock_successful_adjustflag_two_binds_observed_qfq_identity() -> None:
    fake = _FakeBaostock(
        _Result(
            [
                ["2026-01-05", "10", "11", "9", "10.5", "1000", "10500", "1.0"],
                ["2026-01-06", "10.5", "12", "10", "11.5", "1100", "12650", "9.5"],
            ]
        )
    )
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        frame = fetcher.get_daily_data("600519", start_date="2026-01-01", end_date="2026-01-31")

    assert fake.calls == [
        {
            "code": "sh.600519",
            "fields": "date,open,high,low,close,volume,amount,pctChg",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "frequency": "d",
            "adjustflag": "2",
        }
    ]
    identity = extract_daily_data_identity(frame, strict=True)
    assert identity["identity_state"] == "OBSERVED"
    assert identity["observed_adjustment_basis"] == "qfq"
    assert identity["actual_response_branch"] == "frequency=d;adjustflag=2"
    assert identity["provider_identity"] == "BaostockFetcher"
    assert identity["volume_unit"] == "share"
    assert identity["amount_unit"] == "CNY"
    # Revision of the actually observed response is data-dependent, not the
    # installed Python client version or an unobserved vendor release number.
    source_rows = [dict(zip(_Result.fields, row)) for row in fake.result._rows]
    raw_hash = sha256_payload(source_rows)
    assert identity["raw_response_sha256"] == raw_hash
    assert identity["revision"] == "observed-response-sha256:" + raw_hash


def test_baostock_failed_query_never_emits_observed_identity() -> None:
    fake = _FakeBaostock(_Result([], error_code="100", error_msg="query failed"))
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        with pytest.raises(DataFetchError, match="查询失败"):
            fetcher._fetch_raw_data("600519", "2026-01-01", "2026-01-31")

class _NonpriceBaostock:
    def __init__(self):
        self.calls = []

    def _result(self, name, fields, rows, **kwargs):
        self.calls.append((name, kwargs))
        return _NamedResult(fields, rows)

    def query_stock_basic(self, **kwargs):
        return self._result(
            "query_stock_basic",
            ["code", "code_name", "ipoDate", "outDate", "type", "status"],
            [["sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"]],
            **kwargs,
        )

    def query_stock_industry(self, **kwargs):
        return self._result(
            "query_stock_industry",
            ["updateDate", "code", "code_name", "industry", "industryClassification"],
            [["2026-10-26", "sh.600519", "贵州茅台", "C15酒、饮料和精制茶制造业", "证监会行业分类"]],
            **kwargs,
        )

    def query_all_stock(self, **kwargs):
        return self._result(
            "query_all_stock", ["code", "tradeStatus", "code_name"],
            [["sh.600519", "1", "贵州茅台"]], **kwargs,
        )

    def query_adjust_factor(self, code, **kwargs):
        return self._result(
            "query_adjust_factor",
            ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"],
            [[code, "2025-06-27", "0.5", "2.0", "1.0"]],
            code=code, **kwargs,
        )

    def query_dividend_data(self, code, **kwargs):
        return self._result(
            "query_dividend_data",
            ["code", "dividPreNoticeDate", "dividAgmPumDate", "dividPlanAnnounceDate", "dividRegistDate", "dividOperateDate", "dividPayDate", "dividStockMarketDate", "dividCashPsBeforeTax", "dividCashPsAfterTax", "dividStocksPs", "dividCashStock", "dividReserveToStockPs"],
            [[code, "2025-04-03", "2025-06-20", "2025-06-21", "2025-06-26", "2025-06-27", "2025-06-27", "", "27.67", "24.903", "0", "1", "0"]]
            if kwargs.get("year") == "2025" else [],
            code=code, **kwargs,
        )

    def query_history_k_data_plus(self, code, fields, **kwargs):
        return self._result(
            "query_history_k_data_plus", fields.split(","),
            [["2026-10-27", code, "1400", "100000", "140000000", "2", "0.3", "1.1", "20", "6", "15", "18"]],
            code=code, requested_fields=fields, **kwargs,
        )

    @staticmethod
    def _financial_fields(name):
        return {
            "profit": ["code", "pubDate", "statDate", "roeAvg", "npMargin", "gpMargin", "netProfit", "epsTTM", "MBRevenue", "totalShare", "liqaShare"],
            "operation": ["code", "pubDate", "statDate", "NRTurnRatio", "NRTurnDays", "INVTurnRatio", "INVTurnDays", "CATurnRatio", "AssetTurnRatio"],
            "growth": ["code", "pubDate", "statDate", "YOYEquity", "YOYAsset", "YOYNI", "YOYEPSBasic", "YOYPNI"],
            "balance": ["code", "pubDate", "statDate", "currentRatio", "quickRatio", "cashRatio", "YOYLiability", "liabilityToAsset", "assetToEquity"],
            "cash_flow": ["code", "pubDate", "statDate", "CAToAsset", "NCAToAsset", "tangibleAssetToAsset", "ebitToInterest", "CFOToOR", "CFOToNP", "CFOToGr"],
            "dupont": ["code", "pubDate", "statDate", "dupontROE", "dupontAssetStoEquity", "dupontAssetTurn", "dupontPnitoni", "dupontNitogr", "dupontTaxBurden", "dupontIntburden", "dupontEbittogr"],
        }[name]

    def _financial(self, name, code, year, quarter):
        fields = self._financial_fields(name)
        rows = []
        if (year, quarter) == (2026, 2):
            values = {
                "profit": [code, "2026-08-15", "2026-06-30", "0.19", "0.52", "0.91", "51000000000", "75", "95000000000", "1256197800", "1256197800"],
                "operation": [code, "2026-08-15", "2026-06-30", "36", "5", "0.15", "1200", "0.36", "0.31"],
                "growth": [code, "2026-08-15", "2026-06-30", "0.1", "0.05", "0.09", "0.09", "0.09"],
                "balance": [code, "2026-08-15", "2026-06-30", "5", "4", "3", "0.04", "0.2", "1.25"],
                "cash_flow": [code, "2026-08-15", "2026-06-30", "0.2", "0.8", "0.9", "30", "0.55", "1.1", "0.4"],
                "dupont": [code, "2026-08-15", "2026-06-30", "0.2", "1.25", "0.31", "0.52", "0.55", "0.9", "0.95", "0.6"],
            }
            rows = [values[name]]
        elif name == "profit" and (year, quarter) == (2025, 2):
            rows = [[code, "2025-08-13", "2025-06-30", "0.18", "0.51", "0.90", "47000000000", "70", "89000000000", "1256197800", "1256197800"]]
        return self._result(name, fields, rows, code=code, year=year, quarter=quarter)

    def query_profit_data(self, code, year=None, quarter=None): return self._financial("profit", code, year, quarter)
    def query_operation_data(self, code, year=None, quarter=None): return self._financial("operation", code, year, quarter)
    def query_growth_data(self, code, year=None, quarter=None): return self._financial("growth", code, year, quarter)
    def query_balance_data(self, code, year=None, quarter=None): return self._financial("balance", code, year, quarter)
    def query_cash_flow_data(self, code, year=None, quarter=None): return self._financial("cash_flow", code, year, quarter)
    def query_dupont_data(self, code, year=None, quarter=None): return self._financial("dupont", code, year, quarter)

    def query_money_supply_data_month(self, **kwargs):
        return self._result(
            "query_money_supply_data_month",
            ["statYear", "statMonth", "m0Month", "m0YOY", "m0ChainRelative", "m1Month", "m1YOY", "m1ChainRelative", "m2Month", "m2YOY", "m2ChainRelative"],
            [["2026", "09", "130000", "10", "1", "1100000", "8", "0.5", "3300000", "7", "0.4"]],
            **kwargs,
        )


def test_baostock_nonprice_bundle_is_source_bound_partial_and_non_authoritative() -> None:
    fake = _NonpriceBaostock()
    fetcher = BaostockFetcher()
    observed = datetime(2026, 10, 28, 7, 30, tzinfo=timezone.utc)

    class _Clock:
        @classmethod
        def now(cls, tz=None):
            return observed

    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)),
        patch("data_provider.baostock_fetcher.datetime", _Clock),
    ):
        bundle = fetcher.get_cn_stock_nonprice_research_bundle(
            "600519", research_date="2026-10-27",
        )

    observations = bundle["data_object_source_observations"]
    assert {
        "P_CODE_IDENTITY", "P_INDUSTRY", "S_CLASSIFICATION",
        "M_ASOF_UNIVERSE", "P_PRICE_FACTOR", "P_DIVIDEND",
        "P_VAL_RATIO", "P_QUOTE_LIQUID", "P_FIN_VINTAGE",
        "P_EARNINGS_GROWTH", "P_CASH_FLOW", "P_BALANCE_SHEET",
        "P_SHARES_EVENTS", "M_PBOC_MONEY",
    } <= set(observations)
    assert all(row["state"] == "PARTIAL" for row in observations.values())
    assert all(row["historical_pit_eligible"] is False for row in observations.values())
    assert all(row["strategy_upgrade_authority"] is False for row in observations.values())
    assert all(
        row["observation_hash"]
        == sha256_payload({key: value for key, value in row.items() if key != "observation_hash"})
        for row in observations.values()
    )
    growth = observations["P_EARNINGS_GROWTH"]["facts"]
    assert growth["revenue_growth_yoy_pct"] > 0
    assert growth["net_profit_growth_yoy_pct"] > 0
    assert bundle["quality_growth_input"] is None
    assert bundle["business_valuation_input"] is None
    assert bundle["storage_admitted"] is False
    assert bundle["training_admitted"] is False


@pytest.mark.parametrize("dataset,allowed_queries", [
    ("P_CODE_IDENTITY", {"query_stock_basic"}),
    ("S_CLASSIFICATION", {"query_stock_industry"}),
    ("P_VAL_RATIO", {"query_history_k_data_plus"}),
    ("P_CASH_FLOW", {"profit", "cash_flow"}),
    ("P_SHARES_EVENTS", {"profit", "query_adjust_factor"}),
    ("M_PBOC_MONEY", {"query_money_supply_data_month"}),
])
def test_nonprice_selection_limits_actual_query_calls(dataset, allowed_queries):
    fake = _NonpriceBaostock()
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        bundle = fetcher.get_cn_stock_nonprice_research_bundle(
            "600519", research_date="2026-10-27", requested_dataset_ids={dataset},
        )
    assert {name for name, _ in fake.calls} == allowed_queries
    assert set(bundle["data_object_source_observations"]) == {dataset}
    assert bundle["storage_admitted"] is False


@pytest.mark.parametrize("selection", [set(), {"NOT_A_REGISTERED_OBJECT"}])
def test_nonprice_invalid_selection_is_rejected_before_login(selection):
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session") as login:
        with pytest.raises(DataFetchError, match="selection empty or unknown"):
            fetcher.get_cn_stock_nonprice_research_bundle(
                "600519", research_date="2026-10-27", requested_dataset_ids=selection,
            )
    login.assert_not_called()


def test_baostock_nonprice_bundle_rejects_etf_before_provider_dispatch() -> None:
    fake = _NonpriceBaostock()
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        with pytest.raises(DataFetchError, match="does not admit ETF"):
            fetcher.get_cn_stock_nonprice_research_bundle(
                "510300", research_date="2026-10-27",
            )
    assert fake.calls == []


class _MinuteResult(_Result):
    fields = ["date", "time", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag"]


def test_baostock_intraday_5m_seam_binds_identity_without_touching_daily_route() -> None:
    rows = [
        ["2026-09-23", "20260923093500000", "sh.600519", "10", "11", "9", "10.5", "100", "1050", "2"],
        ["2026-09-23", "20260923094000000", "sh.600519", "10.5", "11.5", "10", "11", "120", "1320", "2"],
        ["2026-09-23", "20260923094500000", "sh.600519", "11", "12", "10.5", "11.5", "130", "1495", "2"],
    ]
    fake = _FakeBaostock(_MinuteResult(rows))
    fetcher = BaostockFetcher()
    observed_at = datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc)
    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)),
        patch("data_provider.baostock_fetcher.metadata.version", return_value="0.9.4"),
    ):
        frame = fetcher.get_intraday_data(
            "600519",
            start_date="2026-09-23",
            end_date="2026-09-23",
            frequency="5",
            observed_at=observed_at,
        )

    assert fake.calls == [
        {
            "code": "sh.600519",
            "fields": "date,time,code,open,high,low,close,volume,amount,adjustflag",
            "start_date": "2026-09-23",
            "end_date": "2026-09-23",
            "frequency": "5",
            "adjustflag": "2",
        }
    ]
    identity = extract_intraday_data_identity(frame, strict=True)
    assert identity["timeframe"] == "5m"
    assert identity["provider_identity"] == "BaostockFetcher"
    assert identity["observed_adjustment_basis"] == "qfq"
    assert identity["volume_unit"] == "share"
    assert identity["amount_unit"] == "CNY"
    minute_raw = [dict(zip(_MinuteResult.fields, row)) for row in rows]
    expected_source_hash = sha256_payload(minute_raw)
    assert identity["package_version"] == "0.9.4"
    assert identity["source_rows_sha256"] == expected_source_hash
    assert identity["revision"] == "observed-response-sha256:" + expected_source_hash
    assert identity["vintage"] == observed_at.isoformat()
    assert frame["session"].tolist() == ["2026-09-23:AM"] * 3


def test_baostock_intraday_default_observation_time_follows_response_completion() -> None:
    """Source-observed/available clock cannot precede the received response."""
    requested_at = datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc)
    received_at = requested_at + timedelta(minutes=5)
    rows = [
        ["2026-09-23", "20260923093500000", "sh.600519",
         "10", "11", "9", "10.5", "100", "1050", "2"],
    ]
    fake = _FakeBaostock(_MinuteResult(rows))

    class _ReceiveAwareClock:
        @classmethod
        def now(cls, tz=None):
            # The source response becomes available after query invocation.
            return received_at if fake.calls else requested_at

    fetcher = BaostockFetcher()
    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)),
        patch("data_provider.baostock_fetcher.datetime", _ReceiveAwareClock),
        patch("data_provider.baostock_fetcher.metadata.version", return_value="0.9.4"),
    ):
        result = fetcher.get_intraday_data(
            "600519", start_date="2026-09-23", end_date="2026-09-23",
            frequency="5",
        )

    assert len(fake.calls) == 1
    identity = extract_intraday_data_identity(result, strict=True)
    assert datetime.fromisoformat(identity["observed_at"].replace("Z", "+00:00")) >= received_at
    assert result["available_at"].iloc[0].to_pydatetime().astimezone(timezone.utc) >= received_at


def test_baostock_intraday_rejects_response_identity_drift() -> None:
    rows = [
        ["2026-09-23", "20260923093500000", "sh.600519", "10", "11", "9", "10.5", "100", "1050", "3"],
    ]
    fake = _FakeBaostock(_MinuteResult(rows))
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        with pytest.raises(DataFetchError, match="adjustflag mismatch"):
            fetcher.get_intraday_data(
                "600519",
                start_date="2026-09-23",
                end_date="2026-09-23",
                observed_at=datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc),
            )


def test_baostock_intraday_rejects_unadmitted_frequency_without_query() -> None:
    fake = _FakeBaostock(_MinuteResult([]))
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        with pytest.raises(DataFetchError, match="canonical 5m"):
            fetcher.get_intraday_data(
                "600519",
                start_date="2026-09-23",
                end_date="2026-09-23",
                frequency="15",
            )
    assert fake.calls == []

class _IndexDailyResult(_Result):
    fields = ["date", "code", "open", "high", "low", "close", "volume", "amount", "pctChg"]


def _complete_index_rows(code="sh.000001"):
    return [
        ["2026-10-12", code, "3000", "3020", "2990", "3010", "100000", "30000000", "0.5"],
        ["2026-10-13", code, "3010", "3035", "3000", "3025", "110000", "31000000", "0.4"],
        ["2026-10-14", code, "3025", "3040", "3015", "3030", "120000", "32000000", "0.2"],
    ]


class _IndexAfterCloseClock:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc)


class _IndexBeforeCloseClock:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc)


def test_cn_index_completed_close_source_is_index_qualified_and_not_stock_or_pit() -> None:
    """A legitimate index source may be observed, but never becomes an accepted strategy/PIT vote."""
    fake = _FakeBaostock(_IndexDailyResult(_complete_index_rows()))
    fetcher = BaostockFetcher()
    clock = datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc)
    received_clock = clock + timedelta(minutes=5)

    class _ReceiveAfterQueryClock:
        @classmethod
        def now(cls, tz=None):
            return received_clock if fake.calls else clock

    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)),
        patch("data_provider.baostock_fetcher.datetime", _ReceiveAfterQueryClock),
    ):
        frame = fetcher.get_cn_index_completed_close_source(
            "sh.000001", start_date="2026-10-12", end_date="2026-10-14",
        )
    receipt = frame.attrs["cn_index_completed_close_source"]
    assert fake.calls == [{
        "code": "sh.000001",
        "fields": "date,code,open,high,low,close,volume,amount,pctChg",
        "start_date": "2026-10-12", "end_date": "2026-10-14",
        "frequency": "d", "adjustflag": "3",
    }]
    assert list(frame["code"]) == ["sh.000001"] * 3
    assert receipt["dataset_id"] == "M_INDEX_CURRENT"
    assert receipt["source_code"] == "sh.000001"
    assert receipt["instrument_kind"] == "INDEX"
    assert receipt["timeframe"] == "1d"
    assert receipt["completed_through"] == "2026-10-14"
    assert receipt["observed_at"] == received_clock.isoformat()
    assert receipt["available_at_max"] == received_clock.isoformat()
    assert receipt["provider_identity"] == "BaostockFetcher"
    assert receipt["requested_adjustment_basis"] == "raw"
    assert receipt["observed_adjustment_basis"] == "UNKNOWN"
    assert receipt["volume_unit"] == "UNKNOWN" and receipt["amount_unit"] == "UNKNOWN"
    assert receipt["data_rights"] == "UNKNOWN" and receipt["historical_pit_eligible"] is False
    assert receipt["source_state"] == "PARTIAL"
    assert len(receipt["content_sha256"]) == 64 and len(receipt["identity_hash"]) == 64
    assert "daily_data_identity" not in frame.attrs  # stock Price/Volume schema is not an index source identity

    from src.services.evidence_traceability_registry import compile_complete_research_universe_view
    universe = compile_complete_research_universe_view({"asset_type": "stock"})
    index_item = next(row for row in universe["data_objects"] if row["dataset_id"] == "M_INDEX_CURRENT")
    assert index_item["state"] == "UNKNOWN"
    assert index_item["independent_source_value_admitted"] is False
    assert index_item["strategy_upgrade_authority"] is False


def test_cn_index_source_rejects_cross_instrument_response_even_same_six_digits() -> None:
    fake = _FakeBaostock(_IndexDailyResult(_complete_index_rows("sz.000001")))
    fetcher = BaostockFetcher()
    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)),
        patch("data_provider.baostock_fetcher.datetime", _IndexAfterCloseClock),
    ):
        with pytest.raises(DataFetchError, match="index response code mismatch"):
            fetcher.get_cn_index_completed_close_source(
                "sh.000001", start_date="2026-10-12", end_date="2026-10-14",
            )


def test_cn_index_source_rejects_ambiguous_stock_code_without_external_query() -> None:
    fake = _FakeBaostock(_IndexDailyResult(_complete_index_rows()))
    fetcher = BaostockFetcher()
    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)),
        patch("data_provider.baostock_fetcher.datetime", _IndexAfterCloseClock),
    ):
        with pytest.raises(DataFetchError, match="explicit supported index code"):
            fetcher.get_cn_index_completed_close_source(
                "000001", start_date="2026-10-12", end_date="2026-10-14",
            )
    assert fake.calls == []


def test_cn_index_source_rejects_session_not_yet_completed_without_pit_backfill() -> None:
    fake = _FakeBaostock(_IndexDailyResult(_complete_index_rows()))
    fetcher = BaostockFetcher()
    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)),
        patch("data_provider.baostock_fetcher.datetime", _IndexBeforeCloseClock),
    ):
        with pytest.raises(DataFetchError, match="index daily session not completed"):
            fetcher.get_cn_index_completed_close_source(
                "sh.000001", start_date="2026-10-12", end_date="2026-10-14",
            )
    assert fake.calls == []


def test_cn_index_source_rejects_field_drift_and_missing_target_bar() -> None:
    fetcher = BaostockFetcher()
    wrong = _IndexDailyResult(_complete_index_rows())
    wrong.fields = ["date", "code", "close"]
    fake_wrong = _FakeBaostock(wrong)
    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake_wrong)),
        patch("data_provider.baostock_fetcher.datetime", _IndexAfterCloseClock),
    ):
        with pytest.raises(DataFetchError, match="index daily field drift"):
            fetcher.get_cn_index_completed_close_source(
                "sh.000001", start_date="2026-10-12", end_date="2026-10-14",
            )
    absent = _FakeBaostock(_IndexDailyResult(_complete_index_rows()[:-1]))
    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(absent)),
        patch("data_provider.baostock_fetcher.datetime", _IndexAfterCloseClock),
    ):
        with pytest.raises(DataFetchError, match="index target close absent"):
            fetcher.get_cn_index_completed_close_source(
                "sh.000001", start_date="2026-10-12", end_date="2026-10-14",
            )


def test_cn_index_source_caller_cannot_backdate_the_observation_time() -> None:
    """A caller must not fabricate known-at by passing a clock after session close."""
    fake = _FakeBaostock(_IndexDailyResult(_complete_index_rows()))
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        with pytest.raises(TypeError, match="observed_at"):
            fetcher.get_cn_index_completed_close_source(
                "sh.000001", start_date="2026-10-12", end_date="2026-10-14",
                observed_at=datetime(2026, 10, 14, 8, tzinfo=timezone.utc),
            )
    assert fake.calls == []
