from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from data_provider.treasury_real_yield_source import (
    BREAKEVEN_DATASET_ID,
    BREAKEVEN_PROVIDER_IDENTITY,
    REAL_DATASET_ID,
    REAL_PROVIDER_IDENTITY,
    TreasuryRealYieldSourceError,
    build_treasury_breakeven_source_observation,
    build_treasury_real_yield_source_observation,
)
from data_provider.treasury_yield_curve_source import build_treasury_yield_source_observation
from src.services import evidence_traceability_registry as reg
from src.services.data_object_admission import (
    compile_b1_runtime_admission_receipts,
    compile_data_object_admission_view,
    seal_source_rights_evidence,
)
from src.services.v2_5_evidence_coverage import _v25_data_object_fact_summary


ASOF = "2026-11-04T06:00:00+00:00"
OBSERVED = datetime(2026, 11, 4, 5, 30, tzinfo=timezone.utc)
NOMINAL_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=202611"
REAL_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_real_yield_curve&field_tdr_date_value_month=202611"


def _feed(*, real: bool) -> bytes:
    fields = (
        b"<d:TC_5YEAR>2.31</d:TC_5YEAR><d:TC_7YEAR>2.45</d:TC_7YEAR>"
        b"<d:TC_10YEAR>2.62</d:TC_10YEAR><d:TC_20YEAR>2.91</d:TC_20YEAR>"
        b"<d:TC_30YEAR>3.06</d:TC_30YEAR>"
        if real else
        b"<d:BC_3MONTH>4.21</d:BC_3MONTH><d:BC_2YEAR>4.59</d:BC_2YEAR>"
        b"<d:BC_10YEAR>4.91</d:BC_10YEAR>"
    )
    fields2 = (
        b"<d:TC_5YEAR>2.35</d:TC_5YEAR><d:TC_7YEAR>2.49</d:TC_7YEAR>"
        b"<d:TC_10YEAR>2.65</d:TC_10YEAR><d:TC_20YEAR>2.94</d:TC_20YEAR>"
        b"<d:TC_30YEAR>3.09</d:TC_30YEAR>"
        if real else
        b"<d:BC_3MONTH>4.21</d:BC_3MONTH><d:BC_2YEAR>4.61</d:BC_2YEAR>"
        b"<d:BC_10YEAR>4.95</d:BC_10YEAR>"
    )
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<feed xmlns="http://www.w3.org/2005/Atom" '
        b'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" '
        b'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">'
        b'<entry><updated>2026-11-03T15:45:05Z</updated>'
        b'<content type="application/xml"><m:properties>'
        b'<d:NEW_DATE>2026-11-02T00:00:00</d:NEW_DATE>' + fields +
        b'</m:properties></content></entry>'
        b'<entry><updated>2026-11-03T15:45:05Z</updated>'
        b'<content type="application/xml"><m:properties>'
        b'<d:NEW_DATE>2026-11-03T00:00:00</d:NEW_DATE>' + fields2 +
        b'</m:properties></content></entry></feed>'
    )


def _grant(dataset_id: str, provider: str, *, terms_observed_at="2026-11-04T05:00:00+00:00"):
    clause = f"Synthetic CC0 fixture allows {dataset_id} internal no-send research."
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": "treasury-current-nosend-r001",
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": provider,
        "upstream_source_owner": "US Treasury Office of Debt Management",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": dataset_id,
        "consumer_scope": "MARKET_GLOBAL_TRANSMISSION",
        "terms_uri": "https://catalog.data.gov/dataset/daily-treasury-real-yield-curve-rates",
        "terms_sha256": "e" * 64,
        "terms_observed_at": terms_observed_at,
        "terms_scope": "SYNTHETIC_TEST_CONTRACT",
        "product_use_clause": clause,
        "product_use_clause_sha256": sha256(clause.encode("utf-8")).hexdigest(),
        "rights": {"product_use":"ALLOWED","storage":"FORBIDDEN","training":"FORBIDDEN","redistribution":"FORBIDDEN"},
    })


def _context():
    return {
        "schema_version": "b1-operation-rights-context-v1",
        "allowed_instruments": ["600519"],
        "operation_id": "treasury-current-nosend-r001",
        "intended_use": "NO_SEND_RESEARCH",
        "research_as_of": ASOF,
        "temporal_scope": "CURRENT_OBSERVATION",
        "rights_by_dataset": {
            "G_UST_NOMINAL": _grant("G_UST_NOMINAL", "USTreasuryYieldCurveOfficialXML"),
            REAL_DATASET_ID: _grant(REAL_DATASET_ID, REAL_PROVIDER_IDENTITY),
            BREAKEVEN_DATASET_ID: _grant(BREAKEVEN_DATASET_ID, BREAKEVEN_PROVIDER_IDENTITY),
        },
    }


def _sources():
    context = _context()
    nominal = build_treasury_yield_source_observation(
        _feed(real=False), request_url=NOMINAL_URL, observed_at=OBSERVED, b1_context=context,
    )
    real = build_treasury_real_yield_source_observation(
        _feed(real=True), request_url=REAL_URL, observed_at=OBSERVED, b1_context=context,
    )
    breakeven = build_treasury_breakeven_source_observation(nominal, real, b1_context=context)
    return context, nominal, real, breakeven


def test_real_yield_and_breakeven_reach_existing_registry_as_three_current_products():
    context, nominal, real, breakeven = _sources()
    assert real["facts"]["real_yield_10y_pct_per_year"] == "2.65"
    assert breakeven["facts"]["breakeven_10y_percentage_points"] == "2.30"
    factor={"asset_type":"stock","data_object_source_observations":{
        "G_UST_NOMINAL":nominal, REAL_DATASET_ID:real, BREAKEVEN_DATASET_ID:breakeven,
    }}
    obligations=reg._compile_accepted_data_object_scope(factor)
    receipts=compile_b1_runtime_admission_receipts(obligations,b1_context=context)
    view=compile_data_object_admission_view(obligations,receipts,as_of=ASOF,operation_id=context["operation_id"],intended_use="NO_SEND_RESEARCH")
    admitted={row["dataset_id"]:row for row in view["objects"] if row["product_admitted"]}
    assert set(admitted)=={"G_UST_NOMINAL",REAL_DATASET_ID,BREAKEVEN_DATASET_ID}
    assert view["product_admitted_count"]==3
    assert all(row["temporal_scope"]=="CURRENT_OBSERVATION" for row in admitted.values())
    assert all(row["historical_pit_eligible"] is False for row in admitted.values())
    assert all(row["learning_admitted"] is False for row in admitted.values())
    assert reg.compile_full_domain_data_gate(obligations,view)["full_domain_product_ready"] is False
    assert "4.95%" in _v25_data_object_fact_summary("G_UST_NOMINAL",nominal["facts"])
    assert "2.65%" in _v25_data_object_fact_summary(REAL_DATASET_ID,real["facts"])
    assert "2.30" in _v25_data_object_fact_summary(BREAKEVEN_DATASET_ID,breakeven["facts"])


def test_treasury_rate_family_reaches_existing_pipeline_global_owner():
    from src.core.pipeline import StockAnalysisPipeline

    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _context()
    pipeline._b1_treasury_global_observations = {}
    pipeline._b1_nonprice_shared_observations = {}
    pipeline._b1_nonprice_stock_bundles = {}
    sources = pipeline._ingest_b1_treasury_rate_family_responses(
        nominal_response_xml=_feed(real=False),
        real_response_xml=_feed(real=True),
        nominal_request_url=NOMINAL_URL,
        real_request_url=REAL_URL,
        nominal_observed_at=OBSERVED,
        real_observed_at=OBSERVED,
    )
    assert set(sources) == {"G_UST_NOMINAL", REAL_DATASET_ID, BREAKEVEN_DATASET_ID}
    context = pipeline._b1_nonprice_fundamental_context(
        code="600519", asset_route="STOCK", market="cn",
    )
    assert set(context["data_object_source_observations"]) == set(sources)
    assert context["coverage"]["source_observation_count"] == 3
    with pytest.raises(ValueError, match="B1_TREASURY_SOURCE_ALREADY_BOUND"):
        pipeline._ingest_b1_treasury_rate_family_responses(
            nominal_response_xml=_feed(real=False),
            real_response_xml=_feed(real=True),
            nominal_request_url=NOMINAL_URL,
            real_request_url=REAL_URL,
            nominal_observed_at=OBSERVED,
            real_observed_at=OBSERVED,
        )


@pytest.mark.parametrize("mutation",[
    "wrong_host","wrong_key","future_observation","missing_10y","duplicate_date","bad_rate","late_terms",
])
def test_real_yield_rejects_invalid_source_and_rights(mutation):
    body=_feed(real=True);url=REAL_URL;observed=OBSERVED;context=_context()
    if mutation=="wrong_host":url=url.replace("home.treasury.gov","example.invalid")
    elif mutation=="wrong_key":url=url.replace("daily_treasury_real_yield_curve","daily_treasury_yield_curve")
    elif mutation=="future_observation":observed=datetime(2026,11,4,6,30,tzinfo=timezone.utc)
    elif mutation=="missing_10y":body=body.replace(b"<d:TC_10YEAR>2.65</d:TC_10YEAR>",b"")
    elif mutation=="duplicate_date":body=body.replace(b"2026-11-02T00:00:00",b"2026-11-03T00:00:00")
    elif mutation=="bad_rate":body=body.replace(b"<d:TC_10YEAR>2.65</d:TC_10YEAR>",b"<d:TC_10YEAR>NaN</d:TC_10YEAR>")
    elif mutation=="late_terms":context["rights_by_dataset"][REAL_DATASET_ID]=_grant(REAL_DATASET_ID,REAL_PROVIDER_IDENTITY,terms_observed_at="2026-11-04T05:31:00+00:00")
    with pytest.raises(TreasuryRealYieldSourceError):
        build_treasury_real_yield_source_observation(body,request_url=url,observed_at=observed,b1_context=context)


@pytest.mark.parametrize("mutation",[
    "date_mismatch", "parent_hash", "parent_dataset", "future_parent",
    "parent_provider", "parent_authority", "parent_units",
])
def test_breakeven_rejects_mismatched_or_tampered_parents(mutation):
    context,nominal,real,_=_sources()
    if mutation=="date_mismatch":real["completed_through"]="2026-11-02"
    elif mutation=="parent_hash":real["facts"]={**real["facts"],"real_yield_10y_pct_per_year":"9.99"}
    elif mutation=="parent_dataset":real["dataset_id"]="G_UST_NOMINAL"
    elif mutation=="future_parent":real["source_observed_at"]="2026-11-04T06:30:00+00:00"
    elif mutation=="parent_provider":real["provider_identity"]="SelfAssertedRealYield"
    elif mutation=="parent_authority":real["producer_authority"]="tests.forged_source"
    elif mutation=="parent_units":real["units"]={**real["units"],"real_yield_10y":"DECIMAL"}
    if mutation in {"future_parent", "parent_provider", "parent_authority", "parent_units"}:
        real["observation_hash"]=__import__(
            'data_provider.daily_data_identity', fromlist=['sha256_payload']
        ).sha256_payload({k:v for k,v in real.items() if k!='observation_hash'})
    with pytest.raises(TreasuryRealYieldSourceError):
        build_treasury_breakeven_source_observation(nominal,real,b1_context=context)
