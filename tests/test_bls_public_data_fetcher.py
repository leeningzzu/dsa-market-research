from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

import pytest
import requests

from data_provider.bls_public_data_fetcher import (
    BLS_ATOMIC_PROVIDER_IDENTITY,
    BLS_SERIES_BY_DATASET,
    BLSPublicDataError,
    fetch_us_inflation_atomic_bundle,
)
from data_provider.daily_data_identity import sha256_payload
from src.services.data_object_admission import seal_source_rights_evidence


CLAUSE = "Synthetic contract explicitly allows internal no-send research."


def _rights_context():
    operation_id = "test-bls-nosend-20261028"
    grants = {}
    for dataset_id in BLS_SERIES_BY_DATASET:
        grants[dataset_id] = seal_source_rights_evidence({
            "schema_version": "source-rights-evidence-v2",
            "operation_id": operation_id,
            "intended_use": "NO_SEND_RESEARCH",
            "provider_identity": BLS_ATOMIC_PROVIDER_IDENTITY,
            "upstream_source_owner": "SYNTHETIC_BLS_TEST_CONTRACT",
            "upstream_rights_state": "VERIFIED",
            "dataset_id": dataset_id,
            "consumer_scope": "MARKET_GLOBAL_TRANSMISSION",
            "terms_uri": "https://example.org/test-bls-terms",
            "terms_sha256": "a" * 64,
            "terms_observed_at": "2026-10-01T00:00:00+00:00",
            "terms_scope": "SYNTHETIC_TEST_CONTRACT",
            "product_use_clause": CLAUSE,
            "product_use_clause_sha256": sha256(CLAUSE.encode()).hexdigest(),
            "rights": {
                "product_use": "ALLOWED",
                "storage": "FORBIDDEN",
                "training": "FORBIDDEN",
                "redistribution": "FORBIDDEN",
            },
        })
    return {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": operation_id,
        "intended_use": "NO_SEND_RESEARCH",
        "allowed_instruments": ("600519", "510300"),
        "rights_by_dataset": grants,
    }


class _Response:
    def __init__(self, *, payload=None, content=b"", status=200):
        self._payload = payload
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    def __init__(self, api_response, release_response):
        self.api_response = api_response
        self.release_response = release_response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.api_response

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.release_response


def _series(series_id: str, value: str, yoy: str):
    return {
        "seriesID": series_id,
        "data": [
            {
                "year": "2026",
                "period": "M09",
                "periodName": "September",
                "value": value,
                "latest": "true",
                "calculations": {"pct_changes": {"12": yoy}},
            },
            {
                "year": "2025",
                "period": "M09",
                "periodName": "September",
                "value": "300.0",
            },
        ],
    }


def test_bls_bundle_binds_three_exact_values_release_time_and_no_effects():
    payload = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                _series("CUSR0000SA0", "310.0", "2.4"),
                _series("CUSR0000SA0L1E", "320.0", "2.6"),
                _series("CUSR0000SASLE", "330.0", "3.1"),
            ]
        },
    }
    release = (
        b"Transmission of material in this release is embargoed until "
        b"8:30 a.m. (ET) Wednesday, October 14, 2026"
    )
    session = _Session(
        _Response(payload=payload, content=b"API_BYTES"),
        _Response(content=release),
    )
    bundle = fetch_us_inflation_atomic_bundle(
        research_as_of="2026-10-28T23:59:59+00:00",
        b1_context=_rights_context(),
        session=session,
    )

    assert [item[0] for item in session.calls] == ["POST", "GET"]
    observations = bundle["atomic_subfield_source_observations"]
    assert set(observations) == set(BLS_SERIES_BY_DATASET)
    assert observations["GLOBAL_US_ACTUAL_INFLATION"]["facts"]["yoy_pct"] == 2.4
    assert observations["GLOBAL_US_CORE_INFLATION"]["facts"]["yoy_pct"] == 2.6
    assert observations["GLOBAL_US_SERVICE_INFLATION"]["facts"]["yoy_pct"] == 3.1
    for row in observations.values():
        assert row["state"] == "READY"
        assert row["source_available_at"] == "2026-10-14T12:30:00+00:00"
        assert row["completed_through"] == "2026-09"
        assert row["source_fact_hash"] == sha256_payload(row["facts"])
    assert bundle["storage_admitted"] is False
    assert bundle["training_admitted"] is False
    assert bundle["strategy_upgrade_authority"] is False


def test_bls_missing_grant_stops_before_network():
    session = _Session(_Response(), _Response())
    context = _rights_context()
    context["rights_by_dataset"].pop("GLOBAL_US_CORE_INFLATION")
    with pytest.raises(BLSPublicDataError, match="RIGHTS_NOT_ADMITTED"):
        fetch_us_inflation_atomic_bundle(
            research_as_of="2026-10-28T23:59:59+00:00",
            b1_context=context,
            session=session,
        )
    assert session.calls == []


def test_bls_http_failure_is_environment_stop_not_synthetic_fact():
    session = _Session(_Response(status=403), _Response())
    with pytest.raises(BLSPublicDataError, match="PROVIDER_REQUEST_FAILED"):
        fetch_us_inflation_atomic_bundle(
            research_as_of="2026-10-28T23:59:59+00:00",
            b1_context=_rights_context(),
            session=session,
        )
    assert len(session.calls) == 1


def test_bls_release_after_research_asof_is_rejected():
    payload = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {"series": [
            _series("CUSR0000SA0", "310", "2.4"),
            _series("CUSR0000SA0L1E", "320", "2.6"),
            _series("CUSR0000SASLE", "330", "3.1"),
        ]},
    }
    release = b"For release 8:30 a.m. (ET) Wednesday, October 14, 2026"
    with pytest.raises(BLSPublicDataError, match="RELEASE_AFTER_RESEARCH_ASOF"):
        fetch_us_inflation_atomic_bundle(
            research_as_of="2026-10-13T23:59:59+00:00",
            b1_context=_rights_context(),
            session=_Session(
                _Response(payload=payload, content=b"API"),
                _Response(content=release),
            ),
        )
