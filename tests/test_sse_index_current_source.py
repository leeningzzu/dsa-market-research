from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from data_provider.sse_index_current_source import (
    DATASET_ID,
    PROVIDER_IDENTITY,
    SQL_ID,
    SSEIndexCurrentSourceError,
    build_sse_index_current_observation,
    validate_sse_index_current_observation,
)
from src.services import evidence_traceability_registry as reg
from src.services.data_object_admission import (
    compile_b1_runtime_admission_receipts,
    seal_source_rights_evidence,
)
from tests.test_evidence_traceability_registry import native_summary


REQUEST_URL = (
    "https://query.sse.com.cn/commonQuery.do?"
    "sqlId=COMMON_SSE_SJ_SZZS_ZDZSJYBBXXX&isPagination=false"
)
OBSERVED = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
TRADE_DATE = "2026-11-05"
OPERATION_ID = "fixture-sse-index-current-nosend"


def _row(code: str, name: str, close: str, trade_date: str = "20261105") -> dict:
    return {
        "AVG_PRICE": "",
        "AVG_VOL": "",
        "CLOSE_POINT": close,
        "INDEX_ABBR": name,
        "INDEX_CODE": code,
        "KIND_NUM": "",
        "PE_RATIO": "",
        "RANK": "",
        "TOTAL_VALUE": "",
        "TRADE_AMT": "999999.99",
        "TRADE_DATE": trade_date,
        "VALUE_RATIO": "",
    }


def _response(rows: list[dict] | None = None) -> bytes:
    document = {
        "sqlId": SQL_ID,
        "isPagination": False,
        "actionErrors": [],
        "fieldErrors": {},
        "result": rows or [
            _row("000001", "上证指数", "3689.91"),
            _row("000016", "上证50", "2811.16"),
            _row("000688", "科创50", "1286.68"),
        ],
    }
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def _rights(
    *,
    provider_identity: str = PROVIDER_IDENTITY,
    terms_observed_at: str = "2026-11-05T23:00:00+00:00",
) -> dict:
    clause = (
        "SYNTHETIC TEST ONLY: official SSE current index content may be used "
        "for this bounded NO_SEND research operation."
    )
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": OPERATION_ID,
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": provider_identity,
        "upstream_source_owner": "synthetic-unit-test-only",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": DATASET_ID,
        "consumer_scope": "MARKET_REGIME_BRIEF",
        "terms_uri": "https://example.invalid/terms/sse-index-fixture",
        "terms_sha256": "e" * 64,
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
    })


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
            DATASET_ID: _rights(
                provider_identity=provider_identity,
                terms_observed_at=terms_observed_at,
            )
        },
    }


def _observation(**kwargs) -> dict:
    return build_sse_index_current_observation(
        kwargs.pop("response_json", _response()),
        request_url=kwargs.pop("request_url", REQUEST_URL),
        expected_trade_date=kwargs.pop("expected_trade_date", TRADE_DATE),
        observed_at=kwargs.pop("observed_at", OBSERVED),
        b1_context=kwargs.pop("b1_context", _b1_context()),
        required_index_codes=kwargs.pop(
            "required_index_codes", ("000001", "000016", "000688")
        ),
        **kwargs,
    )


def _factor_with_admitted_index() -> tuple[dict, dict, dict]:
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
    factor.update({
        "research_as_of": b1["research_as_of"],
        "research_operation_id": b1["operation_id"],
        "research_intended_use": b1["intended_use"],
        "data_object_admission_receipts": receipts,
    })
    return factor, observation, b1


def test_official_sse_index_close_is_bounded_source_value_not_full_market() -> None:
    observation = _observation()
    assert validate_sse_index_current_observation(observation) == observation
    assert observation["state"] == "PARTIAL"
    assert observation["completed_through"] == TRADE_DATE
    assert observation["facts"]["indices"] == (
        {
            "index_code": "000001",
            "index_abbr": "上证指数",
            "close_point": "3689.91",
        },
        {
            "index_code": "000016",
            "index_abbr": "上证50",
            "close_point": "2811.16",
        },
        {
            "index_code": "000688",
            "index_abbr": "科创50",
            "close_point": "1286.68",
        },
    )
    assert observation["facts"]["breadth_denominator_admitted"] is False
    assert observation["facts"]["turnover_value_admitted"] is False
    assert "TRADE_AMT" not in json.dumps(
        observation["facts"], ensure_ascii=False
    )
    assert observation["historical_pit_eligible"] is False
    assert observation["strategy_upgrade_authority"] is False


def test_pipeline_merges_one_official_index_owner_into_stock_and_etf_routes() -> None:
    from src.core.pipeline import StockAnalysisPipeline

    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _b1_context()
    pipeline._b1_nonprice_shared_observations = {}
    pipeline._b1_treasury_global_observations = {}
    pipeline._b1_nonprice_stock_bundles = {}
    pipeline._b1_sse_stock_disclosure_observations = {}
    pipeline._b1_sse_etf_disclosure_observations = {}
    bound = pipeline._ingest_b1_sse_index_current_response(
        response_json=_response(),
        request_url=REQUEST_URL,
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
        match="B1_SSE_INDEX_CURRENT_SOURCE_ALREADY_BOUND",
    ):
        pipeline._ingest_b1_sse_index_current_response(
            response_json=_response(),
            request_url=REQUEST_URL,
            expected_trade_date=TRADE_DATE,
            observed_at=OBSERVED,
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "wrong_host",
        "wrong_sql",
        "missing_code",
        "duplicate_code",
        "wrong_date",
        "bad_close",
        "late_terms",
        "wrong_provider",
        "response_after_asof",
        "not_current_observation",
        "future_uncompleted_session",
        "field_drift",
        "reported_error",
    ),
)
def test_sse_index_current_rejects_bad_source_rights_clock_and_schema(
    mutation: str,
) -> None:
    rows = [
        _row("000001", "上证指数", "3689.91"),
        _row("000016", "上证50", "2811.16"),
        _row("000688", "科创50", "1286.68"),
    ]
    document = json.loads(_response(rows))
    request_url = REQUEST_URL
    trade_date = TRADE_DATE
    observed = OBSERVED
    b1 = _b1_context()
    if mutation == "wrong_host":
        request_url = request_url.replace(
            "query.sse.com.cn", "fake.sse.com.cn"
        )
    elif mutation == "wrong_sql":
        document["sqlId"] = "WRONG_SQL"
    elif mutation == "missing_code":
        document["result"] = document["result"][:-1]
    elif mutation == "duplicate_code":
        document["result"].append(deepcopy(document["result"][0]))
    elif mutation == "wrong_date":
        document["result"][0]["TRADE_DATE"] = "20261104"
    elif mutation == "bad_close":
        document["result"][0]["CLOSE_POINT"] = "NaN"
    elif mutation == "late_terms":
        b1 = _b1_context(
            terms_observed_at="2026-11-06T01:10:00+00:00"
        )
    elif mutation == "wrong_provider":
        b1 = _b1_context(provider_identity="SSEOfficialPublicDisclosure")
    elif mutation == "response_after_asof":
        observed = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
    elif mutation == "not_current_observation":
        b1 = _b1_context(temporal_scope="")
    elif mutation == "future_uncompleted_session":
        trade_date = "2026-11-06"
        for row in document["result"]:
            row["TRADE_DATE"] = "20261106"
    elif mutation == "field_drift":
        document["result"][0]["NEW_FIELD"] = "drift"
    elif mutation == "reported_error":
        document["actionErrors"] = ["failure"]
    response = json.dumps(document, ensure_ascii=False).encode("utf-8")
    with pytest.raises(
        (SSEIndexCurrentSourceError, ValueError),
        match="SSE_INDEX_CURRENT_|B1_",
    ):
        build_sse_index_current_observation(
            response,
            request_url=request_url,
            expected_trade_date=trade_date,
            observed_at=observed,
            b1_context=b1,
        )


def test_current_observation_rights_admit_only_m_index_current_product() -> None:
    factor, observation, b1 = _factor_with_admitted_index()
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
    assert factor["data_object_source_observations"][DATASET_ID][
        "facts"
    ] == observation["facts"]
    assert source[DATASET_ID]["strategy_upgrade_authority"] is False
    assert source["M_TURNOVER"]["state"] == "UNKNOWN"
    assert source["M_BASIC_BREADTH"]["state"] == "UNKNOWN"
    admissions = {
        row["dataset_id"]: row
        for row in universe["data_object_admission"]["objects"]
    }
    assert admissions[DATASET_ID]["product_admitted"] is True
    assert admissions[DATASET_ID]["historical_pit_eligible"] is False
    assert admissions[DATASET_ID]["storage_admitted"] is False
    assert admissions[DATASET_ID]["learning_admitted"] is False
    assert admissions["M_TURNOVER"]["product_admitted"] is False

    rights_only = deepcopy(native_summary())
    objects = reg._compile_accepted_data_object_scope(rights_only)
    assert compile_b1_runtime_admission_receipts(
        objects,
        b1_context=b1,
    ) == {}


def test_tampered_sse_index_fact_is_rejected_before_product_or_v25() -> None:
    factor, _observation_row, _b1 = _factor_with_admitted_index()
    factor["data_object_source_observations"][DATASET_ID]["facts"][
        "indices"
    ][0]["close_point"] = "9999.99"
    with pytest.raises(
        reg.TraceabilityError,
        match="DATA_OBJECT_SOURCE_OBSERVATION_HASH_MISMATCH",
    ):
        reg.build_runtime_trace(factor)
