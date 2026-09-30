from __future__ import annotations

from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from data_provider.daily_data_identity import (
    DailyDataIdentityError,
    attach_daily_data_identity,
    build_daily_data_identity,
    build_unclassified_daily_data_identity,
    canonical_identity_json,
    extract_daily_data_identity,
    extract_daily_data_identity_from_records,
    identity_is_durable_price_ready,
    rebind_daily_data_identity,
    validate_daily_data_identity,
)


def _frame(periods: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": day,
                "open": 10.0 + index,
                "high": 11.0 + index,
                "low": 9.0 + index,
                "close": 10.5 + index,
                "volume": 1_000.0 + index,
                "amount": 10_500.0 + index,
                "pct_chg": 1.0 + index,
            }
            for index, day in enumerate(pd.bdate_range("2026-01-05", periods=periods))
        ]
    )


def _observed(frame: pd.DataFrame, *, basis: str = "qfq") -> dict:
    return build_daily_data_identity(
        frame,
        provider_identity="TencentFetcher",
        provider_route="tencent.fqkline.day",
        actual_response_branch="qfqday",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis=basis,
        basis_evidence="response_key:qfqday",
        requested_start="2026-01-01",
        requested_end="2026-01-31",
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        identity_state="OBSERVED",
        observed_at=datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc),
    )


def test_observed_identity_round_trips_through_frame_and_persisted_rows() -> None:
    frame = _frame()
    identity = _observed(frame)
    attach_daily_data_identity(frame, identity)

    extracted = extract_daily_data_identity(frame, strict=True)
    assert extracted == identity
    assert identity_is_durable_price_ready(extracted) is True

    identity_json = canonical_identity_json(identity)
    records = [
        {"data_identity_json": identity_json, "data_identity_hash": identity["identity_hash"]}
        for _ in range(len(frame))
    ]
    assert extract_daily_data_identity_from_records(records, strict=True) == identity


def test_identity_hash_excludes_fetch_time_but_binds_content_and_basis() -> None:
    frame = _frame()
    first = _observed(frame, basis="qfq")
    later = build_daily_data_identity(
        frame,
        provider_identity="TencentFetcher",
        provider_route="tencent.fqkline.day",
        actual_response_branch="qfqday",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="response_key:qfqday",
        requested_start="2026-01-01",
        requested_end="2026-01-31",
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        identity_state="OBSERVED",
        observed_at=datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc),
    )
    assert first["observed_at"] != later["observed_at"]
    assert first["identity_hash"] == later["identity_hash"]

    changed_frame = frame.copy()
    changed_frame.loc[1, "close"] += 0.5
    changed = rebind_daily_data_identity(first, changed_frame)
    assert changed["content_sha256"] != first["content_sha256"]
    assert changed["identity_hash"] != first["identity_hash"]

    hfq = _observed(frame, basis="hfq")
    assert hfq["identity_hash"] != first["identity_hash"]


def test_day_only_or_unknown_provider_identity_stays_unclassified() -> None:
    frame = _frame()
    identity = build_unclassified_daily_data_identity(
        frame,
        provider_identity="TencentFetcher",
        provider_route="tencent.fqkline.day",
        requested_start="2026-01-01",
        requested_end="2026-01-31",
        actual_response_branch="day",
    )
    assert identity["identity_state"] == "UNCLASSIFIED"
    assert identity["observed_adjustment_basis"] is None
    assert identity_is_durable_price_ready(identity) is False


def test_rebind_updates_returned_range_and_row_count() -> None:
    frame = _frame()
    identity = _observed(frame)
    subset = frame.iloc[:2].copy()
    rebound = rebind_daily_data_identity(identity, subset)

    assert rebound["row_count"] == 2
    assert rebound["returned_start"] == "2026-01-05"
    assert rebound["returned_end"] == "2026-01-06"
    assert rebound["identity_hash"] != identity["identity_hash"]


def test_tampered_or_mixed_persisted_identity_fails_closed() -> None:
    identity = _observed(_frame())
    tampered = dict(identity)
    tampered["actual_response_branch"] = "day"
    with pytest.raises(DailyDataIdentityError, match="hash mismatch"):
        validate_daily_data_identity(tampered)

    identity_json = canonical_identity_json(identity)
    with pytest.raises(DailyDataIdentityError, match="incomplete across rows"):
        extract_daily_data_identity_from_records(
            [
                {"data_identity_json": identity_json, "data_identity_hash": identity["identity_hash"]},
                {"data_identity_json": None, "data_identity_hash": None},
            ],
            strict=True,
        )

    noncanonical_json = json.dumps(identity, ensure_ascii=False)
    with pytest.raises(DailyDataIdentityError, match="not canonical"):
        extract_daily_data_identity_from_records(
            [{"data_identity_json": noncanonical_json, "data_identity_hash": identity["identity_hash"]}],
            strict=True,
        )
