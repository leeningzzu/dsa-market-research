"""CSI sector daily relative strength: exact official JSON to existing DSA consumer.

All fixtures are invented synthetic JSON, NOT market facts. Independent expected
20-session mathematics is 10% - 1% = 9 percentage points. Full Sector Product
and the other six timeframes must remain unavailable.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import json

import pytest

from src.core.trading_calendar import resolve_session_range_fail_closed
from data_provider.daily_data_identity import sha256_payload

_TARGET = date(2026, 11, 4)
_ASOF = datetime(2026, 11, 5, 3, 20, tzinfo=timezone.utc)
_BASE = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
_START = "20260901"
_END = "20261104"


def _url(code: str) -> str:
    return f"{_BASE}?indexCode={code}&startDate={_START}&endDate={_END}"


def _rows(code: str) -> list[dict]:
    sessions = resolve_session_range_fail_closed("cn", date(2026, 9, 1), _TARGET)
    rows = []
    for i, day in enumerate(sessions):
        close = (100.0 if code == "000032" else 200.0)
        if i == len(sessions) - 1:
            close = 110.0 if code == "000032" else 202.0
        rows.append({
            "tradeDate": day.strftime("%Y%m%d"), "indexCode": code,
            "indexNameCn": "上证能源" if code == "000032" else "上证指数",
            "open": close - 0.5, "high": close + 1.0,
            "low": close - 1.0, "close": close,
        })
    assert len(rows) == 41
    return rows


def _raw(rows: list[dict]) -> bytes:
    return json.dumps({"data": rows}, ensure_ascii=False).encode("utf-8")


def _call(**overrides):
    from data_provider.csi_sector_daily_rs_source import (
        compile_csi_sector_daily_rs_source_observation,
    )
    args = dict(
        sector_response_json=_raw(_rows("000032")),
        benchmark_response_json=_raw(_rows("000001")),
        sector_request_url=_url("000032"),
        benchmark_request_url=_url("000001"),
        observed_at=_ASOF, research_as_of=_ASOF,
    )
    args.update(overrides)
    return compile_csi_sector_daily_rs_source_observation(**args)


def test_two_exact_official_dailies_reuse_original_rs_and_existing_sector_registry():
    from src.services import evidence_traceability_registry as reg
    row = _call()
    assert row["dataset_id"] == "S_PRICES_RS"
    assert row["state"] == "PARTIAL"
    assert row["historical_pit_eligible"] is False
    assert row["strategy_upgrade_authority"] is False
    assert row["revision"] == row["vintage"] == "UNKNOWN"
    assert row["facts"]["window_start_date"] == "2026-09-30"
    assert row["facts"]["window_end_date"] == "2026-11-04"
    assert row["facts"]["daily_sector_return_pct"] == 10.0
    assert row["facts"]["daily_benchmark_return_pct"] == 1.0
    assert row["facts"]["daily_excess_return_pct_points"] == 9.0
    assert row["facts"]["daily_relative_ratio_change_pct"] == pytest.approx(
        8.910891, abs=1e-6
    )
    assert row["facts"]["daily_relative_state"] == "OUTPERFORMING"
    assert row["facts"]["method_status"] == "READY"
    assert row["facts"]["seven_timeframes_ready"] is False
    source = reg._verified_data_object_source_scope({
        "data_object_source_observations": {"S_PRICES_RS": row},
    })
    assert source["S_PRICES_RS"]["state"] == "PARTIAL"
    assert source["S_PRICES_RS"]["strategy_upgrade_authority"] is False
    universe = reg.compile_complete_research_universe_view({
        "asset_type": "stock",
        "data_object_source_observations": {"S_PRICES_RS": row},
    })
    objects = {r["dataset_id"]: r for r in universe["data_objects"]}
    assert len(objects) == 76
    assert objects["S_PRICES_RS"]["state"] == "PARTIAL"
    for name in ("S_CLASSIFICATION", "S_BREADTH", "S_TURNOVER",
                 "S_LEADER", "S_VALUATION"):
        assert objects[name]["state"] == "UNKNOWN"
    assert universe["full_domain_data_gate"]["full_domain_product_ready"] is False


@pytest.mark.parametrize("mutation", [
    "wrong_host", "wrong_index", "extra_query", "wrong_end",
    "after_asof", "naive_asof", "before_completed_close",
    "wrong_sector_code", "wrong_benchmark_code",
    "wrong_label", "duplicate_session", "date_gap",
    "different_benchmark_grid", "future_day", "bad_close",
    "bad_ohlc", "truncated_window", "sparse_warmup",
    "missing_data", "nonfinite_json", "large_body",
])
def test_malformed_or_future_official_payload_never_enters_source_registry(mutation):
    from data_provider.csi_sector_daily_rs_source import CSISectorDailyRSError
    s = _rows("000032")
    b = _rows("000001")
    kw = {}
    if mutation == "wrong_host":
        kw["sector_request_url"] = _url("000032").replace("www.csindex.com.cn", "evil.example")
    elif mutation == "wrong_index":
        kw["benchmark_request_url"] = _url("000033")
    elif mutation == "extra_query":
        kw["sector_request_url"] = _url("000032") + "&secret=1"
    elif mutation == "wrong_end":
        kw["sector_request_url"] = _url("000032").replace("20261104", "20261103")
    elif mutation == "after_asof":
        kw["research_as_of"] = datetime(2026, 11, 5, 3, 19, tzinfo=timezone.utc)
    elif mutation == "naive_asof":
        kw["research_as_of"] = datetime(2026, 11, 5, 3, 20)
    elif mutation == "before_completed_close":
        t = datetime(2026, 11, 4, 6, 15, tzinfo=timezone.utc)
        kw["observed_at"] = t
        kw["research_as_of"] = t
    elif mutation == "wrong_sector_code":
        s[-1]["indexCode"] = "000300"
    elif mutation == "wrong_benchmark_code":
        b[-1]["indexCode"] = "000300"
    elif mutation == "wrong_label":
        s[-1]["indexNameCn"] = "上证其他"
    elif mutation == "duplicate_session":
        s[-2]["tradeDate"] = s[-1]["tradeDate"]
    elif mutation == "date_gap":
        s.pop(9)
    elif mutation == "different_benchmark_grid":
        b.pop(8)
    elif mutation == "future_day":
        s[-1]["tradeDate"] = "20261105"
    elif mutation == "bad_close":
        s[-1]["close"] = 0
    elif mutation == "bad_ohlc":
        s[-1]["low"] = 200
    elif mutation == "truncated_window":
        s = s[-20:]; b = b[-20:]
    elif mutation == "sparse_warmup":
        s = s[-21:]
        s.pop(4)
        b = b[-21:]
        b.pop(4)
    elif mutation == "missing_data":
        s = []
    elif mutation == "nonfinite_json":
        kw["sector_response_json"] = b'{"data":[NaN]}'
    elif mutation == "large_body":
        kw["sector_response_json"] = b"{}" + bytes(800000)
    kw.setdefault("sector_response_json", _raw(s))
    kw.setdefault("benchmark_response_json", _raw(b))
    with pytest.raises(CSISectorDailyRSError, match="CSI_SECTOR_RS_"):
        _call(**kw)


def test_decimal_float_overflow_is_rejected_before_ratio_or_hashing():
    from data_provider.csi_sector_daily_rs_source import (
        _positive, CSISectorDailyRSError,
    )
    with pytest.raises(CSISectorDailyRSError, match="CSI_SECTOR_RS_PRICE_INVALID"):
        _positive("1e999")


@pytest.mark.parametrize("change", [
    "rehashed_method_ready", "rehashed_seven_tf", "rehashed_excess",
    "rehashed_asof", "rehashed_code", "rehashed_data_rights",
])
def test_self_rehashed_upgrade_or_forged_rs_rejected_at_registry(change):
    from src.services import evidence_traceability_registry as reg
    row = _call()
    if change == "rehashed_method_ready":
        row["state"] = "READY"
    elif change == "rehashed_seven_tf":
        row["facts"]["seven_timeframes_ready"] = True
    elif change == "rehashed_excess":
        row["facts"]["daily_excess_return_pct_points"] = 55.0
    elif change == "rehashed_asof":
        row["completed_through"] = "2026-11-05"
    elif change == "rehashed_code":
        row["facts"]["sector_index_code"] = "000300"
    else:
        row["revision"] = "FULLY_LICENSED"
    row["observation_hash"] = sha256_payload({
        key: value for key, value in row.items() if key != "observation_hash"
    })
    with pytest.raises(reg.TraceabilityError, match="CSI_SECTOR_RS_"):
        reg._verified_data_object_source_scope({
            "data_object_source_observations": {"S_PRICES_RS": row}
        })


@pytest.mark.parametrize("replayed_owner", [
    "sector_rs_mislabeled_as_classification",
    "revoked_current_index_members_mislabeled_as_classification",
])
def test_retired_or_misrouted_csi_owner_cannot_resurrect_sector_classification(
    replayed_owner,
):
    from src.services import evidence_traceability_registry as reg

    row = _call()
    row["dataset_id"] = "S_CLASSIFICATION"
    if replayed_owner.startswith("revoked"):
        row["provider_identity"] = "CSIOfficialCurrentIndexConstituents"
        row["provider_route"] = (
            "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
            "file/autofile/cons/000032cons.xls"
        )
        row["producer_authority"] = (
            "data_provider.csi_sector_membership_source."
            "compile_csi_current_sector_membership_observation"
        )
        row["facts"]["index_is_full_industry_classification"] = False
    row["observation_hash"] = sha256_payload({
        key: val for key, val in row.items() if key != "observation_hash"
    })
    with pytest.raises(reg.TraceabilityError, match="CSI_SECTOR_SOURCE_OWNER_MISMATCH"):
        reg._verified_data_object_source_scope({
            "data_object_source_observations": {"S_CLASSIFICATION": row}
        })
