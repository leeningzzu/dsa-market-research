from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from src.services import evidence_traceability_registry as reg
from src.services.data_object_admission import (
    compile_b1_runtime_admission_receipts,
    compile_data_object_admission_view,
    seal_source_rights_evidence,
)
from src.services.v2_5_evidence_coverage import _v25_data_object_fact_summary


URL = "https://www.federalreserve.gov/releases/h10/current/"
ASOF = "2026-11-05T01:00:00+00:00"
OBSERVED = datetime(2026, 11, 5, 0, 55, tzinfo=timezone.utc)


def _page() -> bytes:
    return b"""<!doctype html><html><body>
    <p>Release Date: November 2, 2026</p>
    <table>
      <tr><th>COUNTRY</th><th>CURRENCY</th><th>Oct. 26</th><th>Oct. 27</th>
          <th>Oct. 28</th><th>Oct. 29</th><th>Oct. 30</th></tr>
      <tr><td>UNITED STATES</td><td>DOLLAR</td><td></td></tr>
      <tr><td>1) BROAD</td><td>JAN06=100</td><td>121.6042</td>
          <td>121.6622</td><td>121.8491</td><td>121.7361</td>
          <td>121.6656</td></tr>
      <tr><td>2) AFE</td><td>JAN06=100</td><td>114.5724</td>
          <td>114.7937</td><td>114.8540</td><td>114.7088</td>
          <td>114.6890</td></tr>
    </table>
    <p>1) A weighted average of the foreign exchange value of the U.S. dollar
       against the currencies of a broad group of major U.S. trading partners.</p>
    </body></html>"""


def _grant(*, terms_observed_at="2026-11-05T00:50:00+00:00"):
    clause = (
        "Board website information is public domain unless otherwise indicated; "
        "internal no-send research is allowed with Board attribution."
    )
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": "fed-h10-current-nosend-r001",
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": "FederalReserveBoardH10OfficialRelease",
        "upstream_source_owner": "Board of Governors of the Federal Reserve System",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": "G_USD_BROAD",
        "consumer_scope": "MARKET_GLOBAL_TRANSMISSION",
        "terms_uri": "https://www.federalreserve.gov/disclaimer.htm",
        "terms_sha256": "c" * 64,
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
        "operation_id": "fed-h10-current-nosend-r001",
        "intended_use": "NO_SEND_RESEARCH",
        "research_as_of": ASOF,
        "temporal_scope": "CURRENT_OBSERVATION",
        "rights_by_dataset": {"G_USD_BROAD": _grant()},
    }


def _source():
    from data_provider.federal_reserve_h10_source import (
        build_federal_reserve_h10_source_observation,
    )

    return build_federal_reserve_h10_source_observation(
        _page(), request_url=URL, observed_at=OBSERVED, b1_context=_context(),
    )


def test_h10_broad_dollar_reaches_registry_and_v25_as_current_product():
    source = _source()
    assert source["dataset_id"] == "G_USD_BROAD"
    assert source["facts"]["latest_completed_date"] == "2026-10-30"
    assert source["facts"]["broad_dollar_index_jan2006_100"] == "121.6656"
    assert source["facts"]["release_date"] == "2026-11-02"
    assert source["facts"]["source_published_at"] == "2026-11-02T21:15:00+00:00"
    assert source["revision"] == source["vintage"] == "UNKNOWN"

    factor = {
        "asset_type": "stock",
        "data_object_source_observations": {"G_USD_BROAD": source},
    }
    obligations = reg._compile_accepted_data_object_scope(factor)
    receipt = compile_b1_runtime_admission_receipts(
        obligations, b1_context=_context(),
    )
    view = compile_data_object_admission_view(
        obligations, receipt, as_of=ASOF,
        operation_id=_context()["operation_id"],
        intended_use="NO_SEND_RESEARCH",
    )
    admitted = next(
        row for row in view["objects"] if row["dataset_id"] == "G_USD_BROAD"
    )
    assert admitted["product_admitted"] is True
    assert admitted["temporal_scope"] == "CURRENT_OBSERVATION"
    assert admitted["historical_pit_eligible"] is False
    assert admitted["learning_admitted"] is False
    assert admitted["strategy_upgrade_authority"] is False
    assert view["product_admitted_count"] == 1
    assert reg.compile_full_domain_data_gate(
        obligations, view,
    )["full_domain_product_ready"] is False

    summary = _v25_data_object_fact_summary("G_USD_BROAD", source["facts"])
    assert "121.6656" in summary
    assert "2026-10-30" in summary
    assert "JAN06=100" in summary


def test_h10_source_reaches_existing_pipeline_global_owner():
    from src.core.pipeline import StockAnalysisPipeline

    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _context()
    pipeline._b1_treasury_global_observations = {}
    pipeline._b1_nonprice_shared_observations = {}
    pipeline._b1_nonprice_stock_bundles = {}
    source = pipeline._ingest_b1_federal_reserve_h10_response(
        response_html=_page(), request_url=URL, observed_at=OBSERVED,
    )
    context = pipeline._b1_nonprice_fundamental_context(
        code="600519", asset_route="STOCK", market="cn",
    )
    assert context["data_object_source_observations"]["G_USD_BROAD"] == source
    assert context["coverage"]["source_observation_count"] == 1
    with pytest.raises(ValueError, match="B1_GLOBAL_SOURCE_ALREADY_BOUND"):
        pipeline._ingest_b1_federal_reserve_h10_response(
            response_html=_page(), request_url=URL, observed_at=OBSERVED,
        )


@pytest.mark.parametrize("mutation", [
    "wrong_host", "query", "missing_release", "wrong_unit", "missing_broad",
    "duplicate_broad", "all_nd", "date_after_release", "future_observation",
    "late_terms",
])
def test_h10_rejects_wrong_source_semantics_and_time(mutation):
    from data_provider.federal_reserve_h10_source import (
        FederalReserveH10SourceError,
        build_federal_reserve_h10_source_observation,
    )

    body = _page()
    url = URL
    observed = OBSERVED
    context = _context()
    if mutation == "wrong_host":
        url = url.replace("www.federalreserve.gov", "example.invalid")
    elif mutation == "query":
        url += "?mirror=true"
    elif mutation == "missing_release":
        body = body.replace(b"Release Date: November 2, 2026", b"Release missing")
    elif mutation == "wrong_unit":
        body = body.replace(b"JAN06=100", b"JAN97=100", 1)
    elif mutation == "missing_broad":
        body = body.replace(b"1) BROAD", b"1) OTHER")
    elif mutation == "duplicate_broad":
        row = (
            b"<tr><td>1) BROAD</td><td>JAN06=100</td><td>1</td><td>2</td>"
            b"<td>3</td><td>4</td><td>5</td></tr>"
        )
        body = body.replace(b"</table>", row + b"</table>")
    elif mutation == "all_nd":
        for value in (b"121.6042", b"121.6622", b"121.8491", b"121.7361", b"121.6656"):
            body = body.replace(value, b"ND")
    elif mutation == "date_after_release":
        body = body.replace(b"Oct. 30", b"Nov. 3")
    elif mutation == "future_observation":
        observed = datetime(2026, 11, 5, 1, 5, tzinfo=timezone.utc)
    elif mutation == "late_terms":
        context["rights_by_dataset"] = {
            "G_USD_BROAD": _grant(terms_observed_at="2026-11-05T00:56:00+00:00")
        }
    with pytest.raises(FederalReserveH10SourceError):
        build_federal_reserve_h10_source_observation(
            body, request_url=url, observed_at=observed, b1_context=context,
        )


def test_h10_fact_tampering_is_rejected_by_registry():
    factor = {
        "asset_type": "stock",
        "data_object_source_observations": {"G_USD_BROAD": _source()},
    }
    forged = deepcopy(factor)
    forged["data_object_source_observations"]["G_USD_BROAD"]["facts"] = {
        **forged["data_object_source_observations"]["G_USD_BROAD"]["facts"],
        "broad_dollar_index_jan2006_100": "999.0",
    }
    with pytest.raises(reg.TraceabilityError, match="OBSERVATION_HASH_MISMATCH"):
        reg._compile_accepted_data_object_scope(forged)
