from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from data_provider.federal_reserve_policy_source import (
    FederalReservePolicySourceError,
    build_federal_reserve_policy_source_observation,
)
from src.services import evidence_traceability_registry as reg
from src.services.data_object_admission import (
    compile_b1_runtime_admission_receipts,
    compile_data_object_admission_view,
    seal_source_rights_evidence,
)
from src.services.v2_5_evidence_coverage import _v25_data_object_fact_summary


STATEMENT_URL = (
    "https://www.federalreserve.gov/newsevents/pressreleases/"
    "monetary20261028a.htm"
)
IMPLEMENTATION_URL = (
    "https://www.federalreserve.gov/newsevents/pressreleases/"
    "monetary20261028a1.htm"
)
ASOF = "2026-11-04T02:10:00+00:00"
OBSERVED = datetime(2026, 11, 4, 2, 5, tzinfo=timezone.utc)


def _statement() -> bytes:
    return b"""<!doctype html><html><body>
    <p>October 28, 2026</p><p>For release at 2:00 p.m. EDT</p>
    <p>The Committee decided to maintain the target range for the federal funds rate at 3-3/4 to 4 percent, in support of the Federal Reserve's dual mandate.</p>
    </body></html>"""


def _implementation() -> bytes:
    return b"""<!doctype html><html><body><p>October 28, 2026</p>
    <p>Undertake open market operations as necessary to maintain the federal funds rate in a target range of 3-3/4 to 4 percent.</p>
    </body></html>"""


def _grant(*, terms_observed_at="2026-11-04T02:00:00+00:00"):
    clause = "Official Board policy release is allowed for internal no-send research."
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": "fed-policy-current-nosend-r001",
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": "FederalReserveOfficialPolicyRelease",
        "upstream_source_owner": "Board of Governors of the Federal Reserve System",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": "G_US_POLICY",
        "consumer_scope": "MARKET_GLOBAL_TRANSMISSION",
        "terms_uri": "https://www.federalreserve.gov/disclaimer.htm",
        "terms_sha256": "d" * 64,
        "terms_observed_at": terms_observed_at,
        "terms_scope": "OFFICIAL_PROVIDER_DATA_USE_STATEMENT",
        "product_use_clause": clause,
        "product_use_clause_sha256": sha256(clause.encode("utf-8")).hexdigest(),
        "rights": {
            "product_use": "ALLOWED",
            "storage": "FORBIDDEN",
            "training": "FORBIDDEN",
            "redistribution": "FORBIDDEN",
        },
    })


def _context():
    return {
        "schema_version": "b1-operation-rights-context-v1",
        "allowed_instruments": ["600519"],
        "operation_id": "fed-policy-current-nosend-r001",
        "intended_use": "NO_SEND_RESEARCH",
        "research_as_of": ASOF,
        "temporal_scope": "CURRENT_OBSERVATION",
        "rights_by_dataset": {"G_US_POLICY": _grant()},
    }


def _source():
    return build_federal_reserve_policy_source_observation(
        _statement(), _implementation(),
        statement_url=STATEMENT_URL,
        implementation_url=IMPLEMENTATION_URL,
        observed_at=OBSERVED,
        b1_context=_context(),
    )


def test_official_policy_source_and_current_observation_reach_existing_registry():
    source = _source()
    assert source["dataset_id"] == "G_US_POLICY"
    assert source["facts"]["target_lower_pct_per_year"] == "3.75"
    assert source["facts"]["target_upper_pct_per_year"] == "4.0"
    assert source["facts"]["decision"] == "MAINTAIN"
    assert source["revision"] == source["vintage"] == "UNKNOWN"

    factor = {
        "asset_type": "stock",
        "data_object_source_observations": {"G_US_POLICY": source},
    }
    obligations = reg._compile_accepted_data_object_scope(factor)
    policy = next(row for row in obligations if row["dataset_id"] == "G_US_POLICY")
    assert policy["state"] == "READY"
    receipts = compile_b1_runtime_admission_receipts(
        obligations, b1_context=_context(),
    )
    view = compile_data_object_admission_view(
        obligations, receipts, as_of=ASOF,
        operation_id=_context()["operation_id"],
        intended_use="NO_SEND_RESEARCH",
    )
    admitted = next(row for row in view["objects"] if row["dataset_id"] == "G_US_POLICY")
    assert admitted["product_admitted"] is True
    assert admitted["temporal_scope"] == "CURRENT_OBSERVATION"
    assert admitted["historical_pit_eligible"] is False
    assert admitted["learning_admitted"] is False
    assert admitted["strategy_upgrade_authority"] is False
    assert view["product_admitted_count"] == 1
    assert reg.compile_full_domain_data_gate(obligations, view)["full_domain_product_ready"] is False

    summary = _v25_data_object_fact_summary("G_US_POLICY", source["facts"])
    assert "3.75%–4.0%" in summary
    assert "MAINTAIN" in summary
    assert "2026-10-28T18:00:00+00:00" in summary


def test_policy_source_reaches_existing_pipeline_global_observation_owner():
    from src.core.pipeline import StockAnalysisPipeline

    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _context()
    pipeline._b1_treasury_global_observations = {}
    pipeline._b1_nonprice_shared_observations = {}
    pipeline._b1_nonprice_stock_bundles = {}
    source = pipeline._ingest_b1_federal_reserve_policy_responses(
        statement_html=_statement(),
        implementation_html=_implementation(),
        statement_url=STATEMENT_URL,
        implementation_url=IMPLEMENTATION_URL,
        observed_at=OBSERVED,
    )
    bound = pipeline._bind_b1_run_context(persist=False)
    assert bound["temporal_scope"] == "CURRENT_OBSERVATION"
    context = pipeline._b1_nonprice_fundamental_context(
        code="600519", asset_route="STOCK", market="cn",
    )
    assert context["data_object_source_observations"]["G_US_POLICY"] == source
    assert context["coverage"]["source_observation_count"] == 1
    with pytest.raises(ValueError, match="B1_GLOBAL_SOURCE_ALREADY_BOUND"):
        pipeline._ingest_b1_federal_reserve_policy_responses(
            statement_html=_statement(),
            implementation_html=_implementation(),
            statement_url=STATEMENT_URL,
            implementation_url=IMPLEMENTATION_URL,
            observed_at=OBSERVED,
        )


@pytest.mark.parametrize("mutation", [
    "wrong_statement_host", "wrong_implementation_date", "missing_release_clock",
    "implementation_conflict", "inverted_band", "future_observation", "late_terms",
])
def test_policy_source_rejects_wrong_identity_time_and_crosscheck(mutation):
    statement_url = STATEMENT_URL
    implementation_url = IMPLEMENTATION_URL
    statement = _statement()
    implementation = _implementation()
    observed = OBSERVED
    context = _context()
    if mutation == "wrong_statement_host":
        statement_url = statement_url.replace("www.federalreserve.gov", "example.invalid")
    elif mutation == "wrong_implementation_date":
        implementation_url = implementation_url.replace("20261028", "20261029")
    elif mutation == "missing_release_clock":
        statement = statement.replace(b"For release at 2:00 p.m. EDT", b"Release clock missing")
    elif mutation == "implementation_conflict":
        implementation = implementation.replace(b"3-3/4 to 4 percent", b"3 to 4 percent")
    elif mutation == "inverted_band":
        statement = statement.replace(b"at 3-3/4 to 4 percent", b"at 5 to 4 percent")
    elif mutation == "future_observation":
        observed = datetime(2026, 11, 4, 2, 20, tzinfo=timezone.utc)
    elif mutation == "late_terms":
        context["rights_by_dataset"] = {
            "G_US_POLICY": _grant(terms_observed_at="2026-11-04T02:06:00+00:00")
        }
    with pytest.raises(FederalReservePolicySourceError):
        build_federal_reserve_policy_source_observation(
            statement, implementation,
            statement_url=statement_url,
            implementation_url=implementation_url,
            observed_at=observed,
            b1_context=context,
        )


def test_policy_fact_hash_tampering_is_rejected_by_registry():
    factor = {
        "asset_type": "stock",
        "data_object_source_observations": {"G_US_POLICY": _source()},
    }
    forged = deepcopy(factor)
    forged["data_object_source_observations"]["G_US_POLICY"]["facts"] = {
        **forged["data_object_source_observations"]["G_US_POLICY"]["facts"],
        "target_upper_pct_per_year": "9.0",
    }
    with pytest.raises(reg.TraceabilityError, match="OBSERVATION_HASH_MISMATCH"):
        reg._compile_accepted_data_object_scope(forged)
