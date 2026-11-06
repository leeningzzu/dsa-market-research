"""Regression for real known-at clock and no-key BLS API contract.

All responses are synthetic; no HTTP is performed. The existing producer,
Rights-v2 gate, original CPI identifiers, and release-time parser are reused.
The expected clock is independently frozen by the test, not by the producer.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from data_provider import bls_public_data_fetcher as bls
from tests.test_bls_public_data_fetcher import (
    _Response, _Session, _rights_context, _series,
)


def _session() -> _Session:
    return _Session(
        _Response(
            payload={
                "status": "REQUEST_SUCCEEDED",
                "Results": {"series": [
                    _series("CUSR0000SA0", "310", "2.4"),
                    _series("CUSR0000SA0L1E", "320", "2.6"),
                    _series("CUSR0000SASLE", "330", "3.1"),
                ]},
            },
            content=b"BLS_TEST_ONLY_API_BYTES",
        ),
        _Response(
            content=(
                b"Transmission of material in this release is embargoed until "
                b"8:30 a.m. (ET) Wednesday, October 14, 2026"
            )
        ),
    )


class _LateObservedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 29, 0, 2, tzinfo=timezone.utc)


class _OnTimeClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 28, 23, 58, tzinfo=timezone.utc)


def test_actual_response_later_than_frozen_research_asof_never_accepted():
    session = _session()
    with patch.object(bls, "datetime", _LateObservedClock):
        with pytest.raises(bls.BLSPublicDataError, match="BLS_OBSERVED_AFTER_RESEARCH_ASOF"):
            bls.fetch_us_inflation_atomic_bundle(
                research_as_of="2026-10-28T23:59:59+00:00",
                b1_context=_rights_context(),
                session=session,
            )
    assert [item[0] for item in session.calls] == ["POST", "GET"]


def test_exact_on_time_public_bls_bundle_never_moves_the_research_clock():
    session = _session()
    with patch.object(bls, "datetime", _OnTimeClock):
        bundle = bls.fetch_us_inflation_atomic_bundle(
            research_as_of="2026-10-28T23:59:59+00:00",
            b1_context=_rights_context(),
            session=session,
        )
    assert bundle["research_as_of"] == "2026-10-28T23:59:59+00:00"
    assert all(
        row["source_observed_at"] == "2026-10-28T23:58:00+00:00"
        for row in bundle["atomic_subfield_source_observations"].values()
    )
    assert bundle["training_admitted"] is False


def test_unregistered_public_bls_request_never_asks_for_keyed_calculations():
    session = _session()
    with patch.object(bls, "datetime", _OnTimeClock):
        bls.fetch_us_inflation_atomic_bundle(
            research_as_of="2026-10-28T23:59:59+00:00",
            b1_context=_rights_context(),
            session=session,
        )
    payload = session.calls[0][2]["json"]
    assert payload["seriesid"] == [
        "CUSR0000SA0", "CUSR0000SA0L1E", "CUSR0000SASLE"
    ]
    assert "calculations" not in payload
    assert "registrationkey" not in payload


def test_no_key_raw_monthly_indices_keep_original_deterministic_yoy_fallback():
    session = _session()
    for row in session.api_response._payload["Results"]["series"]:
        for datum in row["data"]:
            datum.pop("calculations", None)
    with patch.object(bls, "datetime", _OnTimeClock):
        bundle = bls.fetch_us_inflation_atomic_bundle(
            research_as_of="2026-10-28T23:59:59+00:00",
            b1_context=_rights_context(),
            session=session,
        )
    rows = bundle["atomic_subfield_source_observations"]
    assert rows["GLOBAL_US_ACTUAL_INFLATION"]["facts"]["yoy_pct"] == pytest.approx(
        (310.0 / 300.0 - 1) * 100.0
    )
    assert rows["GLOBAL_US_CORE_INFLATION"]["facts"]["yoy_pct"] == pytest.approx(
        (320.0 / 300.0 - 1) * 100.0
    )
    assert rows["GLOBAL_US_SERVICE_INFLATION"]["facts"]["yoy_pct"] == pytest.approx(
        (330.0 / 300.0 - 1) * 100.0
    )
