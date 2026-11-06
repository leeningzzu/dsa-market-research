from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from data_provider.cn_exchange_turnover_source import (
    CONSUMER_SCOPE,
    DATASET_ID,
    PROVIDER_IDENTITY,
    RIGHTS_COMPONENTS,
    SSE_PRODUCT_CODES,
    SSE_SQL_ID,
    SZSE_CATALOG_ID,
    CNExchangeTurnoverSourceError,
    build_cn_exchange_turnover_observation,
    validate_cn_exchange_turnover_observation,
)
from src.services import evidence_traceability_registry as reg
from src.services import v2_5_evidence_coverage as v25
from src.services.data_object_admission import (
    compile_b1_runtime_admission_receipts,
    seal_source_rights_evidence,
)
from tests.test_evidence_traceability_registry import native_summary


TRADE_DATE = "2026-11-05"
OBSERVED = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
OPERATION_ID = "fixture-cn-exchange-turnover-nosend"
SSE_URL = (
    "https://query.sse.com.cn/commonQuery.do?"
    "sqlId=COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C&"
    "PRODUCT_CODE=01%2C02%2C03%2C11%2C17&"
    "type=inParams&SEARCH_DATE=2026-11-05"
)
SZSE_URL = (
    "https://www.szse.cn/api/report/ShowReport/data?"
    "SHOWTYPE=JSON&CATALOGID=scsj_gprdgk_after&loading=first"
)


def _sse_row(
    code: str,
    amount: str,
    volume: str,
    listed: str,
    *,
    trade_date: str = "20261105",
) -> dict:
    return {
        "TOTAL_VALUE": "1",
        "TRADE_VOL": volume,
        "AVG_PE_RATE": "1",
        "TOTAL_TO_RATE": "1",
        "TRADE_AMT": amount,
        "TRADE_NUM": "1",
        "NEGO_VALUE": "1",
        "NEGO_TO_RATE": "1",
        "TRADE_DATE": trade_date,
        "LIST_NUM": listed,
        "PRODUCT_CODE": code,
    }


def _sse_response(rows: list[dict] | None = None) -> bytes:
    return json.dumps(
        {
            "actionErrors": [],
            "actionMessages": [],
            "fieldErrors": {},
            "isPagination": "false",
            "result": rows
            or [
                _sse_row("01", "6369.34", "506.61", "1700"),
                _sse_row("02", "1.82", "0.35", "41"),
                _sse_row("03", "3441.66", "65.18", "618"),
                _sse_row("11", "0.18", "0.07", "0"),
                _sse_row("17", "9812.82", "572.14", "2359"),
            ],
            "sqlId": SSE_SQL_ID,
            "type": "inParams",
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _szse_metadata(*, trade_date: str = TRADE_DATE) -> dict:
    return {
        "catalogid": SZSE_CATALOG_ID,
        "name": "日度概况",
        "tabkey": "tab1",
        "footer": "<table><tr><td>数据统计范围包含存托凭证。</td></tr></table>",
        "conditions": [
            {"name": "txtQueryDate", "defaultValue": trade_date},
            {"name": "tjzqlb", "defaultValue": "D"},
            {"name": "bkbz", "defaultValue": "GP|XZ|CY"},
            {"name": "cjbz", "defaultValue": "cjl|cjje"},
            {
                "name": "cxzs",
                "defaultValue": "成交量（亿）|成交金额（亿元）",
            },
        ],
        "cols": {
            "zbmc": "指标名称",
            "xz": "主板",
            "cy": "创业板",
            "gp": "深市合计",
        },
    }


def _szse_response(
    *,
    metadata: dict | None = None,
    data: list[dict] | None = None,
    error=None,
) -> bytes:
    return json.dumps(
        [
            {
                "metadata": metadata or _szse_metadata(),
                "data": data
                or [
                    {
                        "zbmc": "成交量（亿）",
                        "xz": "441.73",
                        "cy": "231.50",
                        "gp": "673.23",
                    },
                    {
                        "zbmc": "成交金额（亿元）",
                        "xz": "5,601.98",
                        "cy": "5,793.94",
                        "gp": "11,395.92",
                    },
                ],
                "error": error,
            }
        ],
        ensure_ascii=False,
    ).encode("utf-8")


def _component_rights(
    provider_identity: str,
    terms_uri: str,
    *,
    terms_observed_at: str = "2026-11-05T23:00:00+00:00",
) -> dict:
    clause = (
        "SYNTHETIC TEST ONLY: this official exchange fixture may be used for "
        "the bounded internal NO_SEND research operation."
    )
    return seal_source_rights_evidence(
        {
            "schema_version": "source-rights-evidence-v2",
            "operation_id": OPERATION_ID,
            "intended_use": "NO_SEND_RESEARCH",
            "provider_identity": provider_identity,
            "upstream_source_owner": "synthetic-unit-test-only",
            "upstream_rights_state": "VERIFIED",
            "dataset_id": DATASET_ID,
            "consumer_scope": CONSUMER_SCOPE,
            "terms_uri": terms_uri,
            "terms_sha256": (
                "1" * 64
                if provider_identity.startswith("SSE")
                else "2" * 64
            ),
            "terms_observed_at": terms_observed_at,
            "terms_scope": "SYNTHETIC_TEST_CONTRACT",
            "product_use_clause": clause,
            "product_use_clause_sha256": sha256(
                clause.encode("utf-8")
            ).hexdigest(),
            "rights": {
                "product_use": "ALLOWED",
                "storage": "FORBIDDEN",
                "training": "FORBIDDEN",
                "redistribution": "FORBIDDEN",
            },
        }
    )


def _rights_bundle(
    *,
    provider_identity: str = PROVIDER_IDENTITY,
    terms_observed_at: str = "2026-11-05T23:00:00+00:00",
) -> dict:
    components = tuple(
        _component_rights(
            component_provider,
            terms_uri,
            terms_observed_at=terms_observed_at,
        )
        for component_provider, terms_uri in RIGHTS_COMPONENTS
    )
    clause = "Synthetic intersection of SSE and SZSE no-send test clauses."
    return seal_source_rights_evidence(
        {
            "schema_version": "source-rights-evidence-bundle-v1",
            "operation_id": OPERATION_ID,
            "intended_use": "NO_SEND_RESEARCH",
            "provider_identity": provider_identity,
            "upstream_source_owner": (
                "Shanghai Stock Exchange + Shenzhen Stock Exchange"
            ),
            "upstream_rights_state": "VERIFIED",
            "dataset_id": DATASET_ID,
            "consumer_scope": CONSUMER_SCOPE,
            "terms_uris": tuple(item["terms_uri"] for item in components),
            "terms_sha256s": tuple(
                item["terms_sha256"] for item in components
            ),
            "terms_observed_at": terms_observed_at,
            "terms_scope": "COMPOSITE_OPERATION_MATCHED_UPSTREAM_TERMS",
            "product_use_clause": clause,
            "product_use_clause_sha256": sha256(
                clause.encode("utf-8")
            ).hexdigest(),
            "components": components,
            "rights": {
                "product_use": "ALLOWED",
                "storage": "FORBIDDEN",
                "training": "FORBIDDEN",
                "redistribution": "FORBIDDEN",
            },
        }
    )


def _b1_context(
    *,
    provider_identity: str = PROVIDER_IDENTITY,
    terms_observed_at: str = "2026-11-05T23:00:00+00:00",
    research_as_of: str = "2026-11-06T01:30:00+00:00",
    temporal_scope: str = "CURRENT_OBSERVATION",
) -> dict:
    return {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": OPERATION_ID,
        "intended_use": "NO_SEND_RESEARCH",
        "temporal_scope": temporal_scope,
        "allowed_instruments": ["600519", "510300"],
        "research_as_of": research_as_of,
        "rights_by_dataset": {
            DATASET_ID: _rights_bundle(
                provider_identity=provider_identity,
                terms_observed_at=terms_observed_at,
            )
        },
    }


def _observation(**kwargs) -> dict:
    return build_cn_exchange_turnover_observation(
        kwargs.pop("sse_response_json", _sse_response()),
        kwargs.pop("szse_response_json", _szse_response()),
        sse_request_url=kwargs.pop("sse_request_url", SSE_URL),
        szse_request_url=kwargs.pop("szse_request_url", SZSE_URL),
        expected_trade_date=kwargs.pop("expected_trade_date", TRADE_DATE),
        observed_at=kwargs.pop("observed_at", OBSERVED),
        b1_context=kwargs.pop("b1_context", _b1_context()),
        **kwargs,
    )


def _factor_with_admitted_turnover() -> tuple[dict, dict, dict]:
    observation = _observation()
    factor = deepcopy(native_summary())
    factor["data_object_source_observations"] = {
        DATASET_ID: deepcopy(observation)
    }
    b1 = _b1_context()
    objects = reg._compile_accepted_data_object_scope(factor)
    receipts = compile_b1_runtime_admission_receipts(
        objects,
        b1_context=b1,
    )
    factor.update(
        {
            "research_as_of": b1["research_as_of"],
            "research_operation_id": b1["operation_id"],
            "research_intended_use": b1["intended_use"],
            "data_object_admission_receipts": receipts,
        }
    )
    return factor, observation, b1


def test_official_two_exchange_turnover_is_precise_partial_not_all_a_or_breadth():
    observation = _observation()
    assert validate_cn_exchange_turnover_observation(observation) == observation
    facts = observation["facts"]
    assert facts["sse_stock_total_turnover_100m_cny"] == "9812.82"
    assert facts["sse_a_share_turnover_100m_cny"] == "9811"
    assert facts["sse_b_share_turnover_100m_cny"] == "1.82"
    assert facts["sse_cdr_turnover_100m_cny"] == "0.18"
    assert facts["szse_total_turnover_100m_cny"] == "11395.92"
    assert facts["combined_two_exchange_stock_turnover_100m_cny"] == (
        "21208.74"
    )
    assert facts["combined_two_exchange_stock_turnover_cny"] == 2120874000000
    assert facts["two_exchange_turnover_value_admitted"] is True
    assert facts["szse_scope_includes_depositary_receipts"] is True
    for field in (
        "bse_included",
        "exact_all_a_turnover_admitted",
        "all_a_bse_denominator_admitted",
        "breadth_denominator_admitted",
        "turnover_rate_admitted",
        "relative_volume_admitted",
    ):
        assert facts[field] is False
    assert observation["historical_pit_eligible"] is False
    assert observation["strategy_upgrade_authority"] is False


def test_pipeline_merges_turnover_owner_into_stock_and_etf_routes():
    from src.core.pipeline import StockAnalysisPipeline

    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _b1_context()
    pipeline._b1_nonprice_shared_observations = {}
    pipeline._b1_treasury_global_observations = {}
    pipeline._b1_nonprice_stock_bundles = {}
    pipeline._b1_sse_stock_disclosure_observations = {}
    pipeline._b1_sse_etf_disclosure_observations = {}
    bound = pipeline._ingest_b1_cn_exchange_turnover_responses(
        sse_response_json=_sse_response(),
        szse_response_json=_szse_response(),
        sse_request_url=SSE_URL,
        szse_request_url=SZSE_URL,
        expected_trade_date=TRADE_DATE,
        observed_at=OBSERVED,
    )
    for code, route in (("600519", "STOCK"), ("510300", "ETF")):
        context = pipeline._b1_nonprice_fundamental_context(
            code=code,
            asset_route=route,
            market="cn",
        )
        assert context["data_object_source_observations"][DATASET_ID] == bound
        assert context["coverage"]["product_fact_count"] == 0
    with pytest.raises(
        ValueError,
        match="B1_CN_EXCHANGE_TURNOVER_SOURCE_ALREADY_BOUND",
    ):
        pipeline._ingest_b1_cn_exchange_turnover_responses(
            sse_response_json=_sse_response(),
            szse_response_json=_szse_response(),
            sse_request_url=SSE_URL,
            szse_request_url=SZSE_URL,
            expected_trade_date=TRADE_DATE,
            observed_at=OBSERVED,
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "wrong_sse_host",
        "wrong_szse_host",
        "wrong_sse_sql",
        "wrong_szse_catalog",
        "missing_sse_code",
        "duplicate_sse_code",
        "sse_total_drift",
        "wrong_sse_date",
        "wrong_szse_date",
        "szse_total_drift",
        "szse_footer_missing",
        "szse_reported_error",
        "late_terms",
        "wrong_provider",
        "response_after_asof",
        "not_current_observation",
        "future_uncompleted_session",
    ),
)
def test_turnover_rejects_source_rights_clock_scope_and_schema(mutation: str):
    sse_url = SSE_URL
    szse_url = SZSE_URL
    trade_date = TRADE_DATE
    observed = OBSERVED
    b1 = _b1_context()
    sse = json.loads(_sse_response())
    szse = json.loads(_szse_response())
    if mutation == "wrong_sse_host":
        sse_url = sse_url.replace("query.sse.com.cn", "fake.sse.com.cn")
    elif mutation == "wrong_szse_host":
        szse_url = szse_url.replace("www.szse.cn", "fake.szse.cn")
    elif mutation == "wrong_sse_sql":
        sse["sqlId"] = "WRONG_SQL"
    elif mutation == "wrong_szse_catalog":
        szse[0]["metadata"]["catalogid"] = "wrong"
    elif mutation == "missing_sse_code":
        sse["result"] = sse["result"][:-1]
    elif mutation == "duplicate_sse_code":
        sse["result"][-1]["PRODUCT_CODE"] = "01"
    elif mutation == "sse_total_drift":
        sse["result"][-1]["TRADE_AMT"] = "9812.83"
    elif mutation == "wrong_sse_date":
        sse["result"][0]["TRADE_DATE"] = "20261104"
    elif mutation == "wrong_szse_date":
        szse[0]["metadata"]["conditions"][0]["defaultValue"] = "2026-11-04"
    elif mutation == "szse_total_drift":
        szse[0]["data"][1]["gp"] = "11,395.93"
    elif mutation == "szse_footer_missing":
        szse[0]["metadata"]["footer"] = ""
    elif mutation == "szse_reported_error":
        szse[0]["error"] = "failure"
    elif mutation == "late_terms":
        b1 = _b1_context(terms_observed_at="2026-11-06T01:10:00+00:00")
    elif mutation == "wrong_provider":
        b1 = _b1_context(provider_identity="SSEOnlyTurnover")
    elif mutation == "response_after_asof":
        observed = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
    elif mutation == "not_current_observation":
        b1 = _b1_context(temporal_scope="")
    elif mutation == "future_uncompleted_session":
        trade_date = "2026-11-06"
        for row in sse["result"]:
            row["TRADE_DATE"] = "20261106"
        szse[0]["metadata"]["conditions"][0]["defaultValue"] = trade_date
        sse_url = sse_url.replace("2026-11-05", trade_date)
    with pytest.raises(
        (CNExchangeTurnoverSourceError, ValueError),
        match="CN_EXCHANGE_TURNOVER_|B1_",
    ):
        build_cn_exchange_turnover_observation(
            json.dumps(sse, ensure_ascii=False).encode("utf-8"),
            json.dumps(szse, ensure_ascii=False).encode("utf-8"),
            sse_request_url=sse_url,
            szse_request_url=szse_url,
            expected_trade_date=trade_date,
            observed_at=observed,
            b1_context=b1,
        )


def test_current_observation_rights_admit_only_m_turnover_product():
    factor, observation, b1 = _factor_with_admitted_turnover()
    trace = reg.build_runtime_trace(factor)
    universe = trace["complete_research_universe"]
    gate = universe["full_domain_data_gate"]
    assert gate["current_product_admitted_dataset_ids"] == (DATASET_ID,)
    assert gate["current_product_admitted_count"] == 1
    assert gate["full_domain_product_ready"] is False
    source = {
        row["dataset_id"]: row for row in universe["data_objects"]
    }
    assert source[DATASET_ID]["state"] == "PARTIAL"
    assert source[DATASET_ID]["source_fact_hash"] == reg.digest(
        observation["facts"]
    )
    assert source[DATASET_ID]["strategy_upgrade_authority"] is False
    assert source["M_BASIC_BREADTH"]["state"] == "UNKNOWN"
    assert source["M_ASOF_UNIVERSE"]["state"] == "UNKNOWN"
    admissions = {
        row["dataset_id"]: row
        for row in universe["data_object_admission"]["objects"]
    }
    assert admissions[DATASET_ID]["product_admitted"] is True
    assert admissions[DATASET_ID]["historical_pit_eligible"] is False
    assert admissions[DATASET_ID]["storage_admitted"] is False
    assert admissions[DATASET_ID]["learning_admitted"] is False

    summary = v25._v25_data_object_fact_summary(
        DATASET_ID,
        observation["facts"],
    )
    assert "两市完成日成交额" in summary
    assert "21208.74亿元" in summary
    assert "不含北交所" in summary
    assert "不等于全A" in summary


def test_tampered_turnover_fact_is_rejected_before_product_or_v25():
    factor, _observation_row, _b1 = _factor_with_admitted_turnover()
    factor["data_object_source_observations"][DATASET_ID]["facts"][
        "combined_two_exchange_stock_turnover_100m_cny"
    ] = "99999"
    with pytest.raises(
        reg.TraceabilityError,
        match="DATA_OBJECT_SOURCE_OBSERVATION_HASH_MISMATCH",
    ):
        reg.build_runtime_trace(factor)
