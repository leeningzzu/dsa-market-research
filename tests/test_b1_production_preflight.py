from __future__ import annotations

from datetime import date, datetime, timezone
import json
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest
import pandas as pd

from data_provider.daily_data_identity import (
    attach_daily_data_identity,
    build_daily_data_identity,
)
from data_provider.intraday_data_identity import (
    attach_intraday_data_identity,
    build_intraday_data_identity,
)

from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType
from src.services.data_object_admission import (
    DataObjectAdmissionError,
    bind_b1_rights_context_as_of,
    compile_b1_runtime_admission_receipts,
    compile_data_object_admission_view,
    load_b1_rights_context,
    seal_source_rights_evidence,
    validate_b1_provider_preflight,
    validate_b1_rights_context,
)


def _grant(*, dataset_id: str, consumer: str, operation_id: str = "b1-nosend-20261023"):
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": operation_id,
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": "BaostockFetcher",
        "upstream_source_owner": "SYNTHETIC_FIXTURE_NOT_REAL_LICENSE",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": dataset_id,
        "consumer_scope": consumer,
        "terms_uri": "https://example.org/test-contract-only",
        "terms_sha256": "a" * 64,
        "terms_observed_at": "2026-10-22T09:00:00+00:00",
        "terms_scope": "SYNTHETIC_TEST_CONTRACT",
        "product_use_clause": "Synthetic fixture explicitly allows internal no-send research.",
        "product_use_clause_sha256": "c2a9b98537611cb0c9e9cb165ab0a4a699d84f5a2644a5e1e23740d0af55c6e1",
        "rights": {
            "product_use": "ALLOWED",
            "storage": "FORBIDDEN",
            "training": "FORBIDDEN",
            "redistribution": "FORBIDDEN",
        },
    })


def _bls_grant(dataset_id: str, operation_id: str):
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": operation_id,
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": "BLSPublicDataAPI",
        "upstream_source_owner": "SYNTHETIC_BLS_TEST_CONTRACT",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": dataset_id,
        "consumer_scope": "MARKET_GLOBAL_TRANSMISSION",
        "terms_uri": "https://example.org/test-bls-contract",
        "terms_sha256": "b" * 64,
        "terms_observed_at": "2026-10-22T09:00:00+00:00",
        "terms_scope": "SYNTHETIC_TEST_CONTRACT",
        "product_use_clause": "Synthetic fixture explicitly allows internal no-send research.",
        "product_use_clause_sha256": "c2a9b98537611cb0c9e9cb165ab0a4a699d84f5a2644a5e1e23740d0af55c6e1",
        "rights": {
            "product_use": "ALLOWED",
            "storage": "FORBIDDEN",
            "training": "FORBIDDEN",
            "redistribution": "FORBIDDEN",
        },
    })


@pytest.mark.parametrize("code", ["600519", "510300"])
def test_cn_b1_without_rights_cannot_call_provider_or_cache(code):
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": "b1-nosend-20261023",
        "intended_use": "NO_SEND_RESEARCH",
        "allowed_instruments": [code],
        # A valid unrelated grant selects B1 without fabricating the required
        # Stock/ETF daily authorization under test.
        "rights_by_dataset": {
            "UNRELATED": _grant(
                dataset_id="UNRELATED", consumer="ASSET_RESEARCH_BRIEF",
            ),
        },
    }
    pipeline.fetcher_manager = MagicMock()
    pipeline.db = MagicMock()
    pipeline._resolve_resume_target_date = MagicMock(return_value=date(2026, 10, 22))
    success, reason = pipeline.fetch_and_save_stock_data(code)
    assert success is False
    assert "B1_" in (reason or "")
    pipeline.fetcher_manager.get_stock_name.assert_not_called()
    pipeline.fetcher_manager.get_daily_data.assert_not_called()
    pipeline.db.has_today_data.assert_not_called()
    pipeline.db.save_daily_data.assert_not_called()


@pytest.mark.parametrize("mutation", ["wrong_operation", "wrong_dataset", "unverified", "late_terms", "redistribution"])
def test_preflight_cannot_infer_external_grant_or_lose_exact_binding(mutation):
    grant = _grant(dataset_id="P_DAILY", consumer="ASSET_RESEARCH_BRIEF")
    if mutation == "wrong_operation":
        grant = _grant(dataset_id="P_DAILY", consumer="ASSET_RESEARCH_BRIEF", operation_id="other-operation")
    if mutation == "wrong_dataset":
        grant = _grant(dataset_id="E_PRICE_1D", consumer="ASSET_RESEARCH_BRIEF")
    if mutation in ("unverified", "late_terms", "redistribution"):
        changed = {key: value for key, value in grant.items() if key != "evidence_hash"}
        if mutation == "unverified":
            changed["upstream_rights_state"] = "UNKNOWN"
        if mutation == "late_terms":
            changed["terms_observed_at"] = "2026-10-24T00:00:00+00:00"
        if mutation == "redistribution":
            changed["rights"] = {**changed["rights"], "redistribution": "ALLOWED"}
        grant = seal_source_rights_evidence(changed)
    with pytest.raises(DataObjectAdmissionError, match="B1_"):
        validate_b1_provider_preflight(
            grant, operation_id="b1-nosend-20261023",
            intended_use="NO_SEND_RESEARCH",
            dataset_id="P_DAILY",
            consumer_scope="ASSET_RESEARCH_BRIEF",
            research_as_of="2026-10-23T10:00:00+00:00",
        )


def test_software_package_license_cannot_stand_in_for_data_use_terms():
    grant = _grant(dataset_id="P_DAILY", consumer="ASSET_RESEARCH_BRIEF")
    changed = {key: value for key, value in grant.items() if key != "evidence_hash"}
    changed["terms_scope"] = "SOFTWARE_PACKAGE_LICENSE"
    changed["product_use_clause"] = "BSD License"
    changed["product_use_clause_sha256"] = "170c0f4cee6fc262f8902676b7aa0e88e11f1777f1531934116898c43fd5086a"
    forged = seal_source_rights_evidence(changed)
    with pytest.raises(
        DataObjectAdmissionError,
        match="SOURCE_RIGHTS_EVIDENCE_TERMS_SCOPE_INVALID|B1_UPSTREAM_RIGHTS_EVIDENCE_INVALID",
    ):
        validate_b1_provider_preflight(
            forged,
            operation_id="b1-nosend-20261023",
            intended_use="NO_SEND_RESEARCH",
            dataset_id="P_DAILY",
            consumer_scope="ASSET_RESEARCH_BRIEF",
            research_as_of="2026-10-23T10:00:00+00:00",
        )


def test_product_use_clause_hash_must_bind_exact_reviewed_text():
    grant = _grant(dataset_id="P_DAILY", consumer="ASSET_RESEARCH_BRIEF")
    changed = {key: value for key, value in grant.items() if key != "evidence_hash"}
    changed["product_use_clause"] = "Different clause"
    forged = seal_source_rights_evidence(changed)
    with pytest.raises(
        DataObjectAdmissionError,
        match="PRODUCT_USE_CLAUSE_HASH_MISMATCH|B1_UPSTREAM_RIGHTS_EVIDENCE_INVALID",
    ):
        validate_b1_provider_preflight(
            forged,
            operation_id="b1-nosend-20261023",
            intended_use="NO_SEND_RESEARCH",
            dataset_id="P_DAILY",
            consumer_scope="ASSET_RESEARCH_BRIEF",
            research_as_of="2026-10-23T10:00:00+00:00",
        )


def test_synthetic_structural_grant_is_not_storage_or_training_authorization():
    """Only verifies the code seam. The fixture is not a real provider grant."""
    preflight = validate_b1_provider_preflight(
        _grant(dataset_id="P_DAILY", consumer="ASSET_RESEARCH_BRIEF"),
        operation_id="b1-nosend-20261023",
        intended_use="NO_SEND_RESEARCH",
        dataset_id="P_DAILY",
        consumer_scope="ASSET_RESEARCH_BRIEF",
        research_as_of="2026-10-23T10:00:00+00:00",
    )
    assert preflight["provider_identity"] == "BaostockFetcher"
    assert preflight["rights"]["product_use"] == "ALLOWED"
    assert preflight["rights"]["storage"] == "FORBIDDEN"
    assert preflight["rights"]["training"] == "FORBIDDEN"


def _runtime_obligation():
    """Independent source snapshot for the code seam, never live data."""
    return {
        "dataset_id": "P_DAILY",
        "domain": "STOCK",
        "state": "PARTIAL",
        "native_frequency": "daily/completed",
        "product_consumer": "ASSET_RESEARCH_BRIEF",
        "provider_identity": "BaostockFetcher",
        "provider_route": "synthetic.test_source.query_daily",
        "source_identity_hash": "b" * 64,
        "source_content_sha256": "c" * 64,
        "source_observed_at": "2026-10-23T09:00:00+00:00",
        "source_available_at": "2026-10-23T08:00:00+00:00",
        "completed_through": "2026-10-22",
        "adjustment_basis": "QFQ",
        "currency": "CNY",
        "volume_unit": "SHARE",
        "amount_unit": "CNY",
        "units": {"price": "CNY_PER_SHARE", "volume": "SHARE", "amount": "CNY"},
        "revision": "CURRENT_PROVIDER_OBSERVATION",
        "vintage": "2026-10-23T09:00:00+00:00",
    }


def _runtime_metadata(obligation=None):
    scope = obligation or _runtime_obligation()
    return {
        **{
            field: scope[field]
            for field in (
                "provider_identity", "source_identity_hash",
                "source_content_sha256", "source_observed_at",
                "source_available_at", "completed_through",
            )
        },
        "provider_route": "synthetic.test_source.query_daily",
        "units": {"price": "CNY_PER_SHARE", "volume": "SHARE", "amount": "CNY"},
        "currency": "CNY",
        "adjustment_basis": "QFQ",
        "volume_unit": "SHARE",
        "amount_unit": "CNY",
        "revision": "CURRENT_PROVIDER_OBSERVATION",
        "vintage": "2026-10-23T09:00:00+00:00",
    }


def _runtime_b1_context(metadata=None):
    grant = _grant(dataset_id="P_DAILY", consumer="ASSET_RESEARCH_BRIEF")
    return {
        "operation_id": "b1-nosend-20261023",
        "intended_use": "NO_SEND_RESEARCH",
        "research_as_of": "2026-10-23T10:00:00+00:00",
        "rights_by_dataset": {"P_DAILY": grant},
        "source_metadata_by_dataset": (
            {} if metadata is None else {"P_DAILY": metadata}
        ),
    }


def test_runtime_product_receipt_reaches_original_76_object_admission_compiler():
    # The producer seam is Registry-closed-world, not price-object hardcoded.
    global_scope = {
        **_runtime_obligation(),
        "dataset_id": "G_VIX",
        "domain": "GLOBAL",
        "native_frequency": "daily/close",
        "product_consumer": "MARKET_GLOBAL_TRANSMISSION",
        "adjustment_basis": "NOT_APPLICABLE",
        "units": {"value": "INDEX_POINT"},
    }
    grant = _grant(
        dataset_id="G_VIX", consumer="MARKET_GLOBAL_TRANSMISSION",
    )
    metadata = {
        **_runtime_metadata(global_scope),
        "adjustment_basis": "NOT_APPLICABLE",
        "units": {"value": "INDEX_POINT"},
    }
    context = {
        "operation_id": "b1-nosend-20261023",
        "intended_use": "NO_SEND_RESEARCH",
        "research_as_of": "2026-10-23T10:00:00+00:00",
        "rights_by_dataset": {"G_VIX": grant},
        "source_metadata_by_dataset": {"G_VIX": metadata},
    }
    global_receipts = compile_b1_runtime_admission_receipts(
        [global_scope], b1_context=context,
    )
    assert set(global_receipts) == {"G_VIX"}
    global_view = compile_data_object_admission_view(
        [global_scope], global_receipts,
        operation_id="b1-nosend-20261023",
        intended_use="NO_SEND_RESEARCH",
        as_of="2026-10-23T10:00:00+00:00",
    )
    assert global_view["product_admitted_count"] == 1
    assert global_view["objects"][0]["strategy_upgrade_authority"] is False

    """Synthetic positive verifies only the executable connector, not license."""
    obligation = _runtime_obligation()
    receipts = compile_b1_runtime_admission_receipts(
        [obligation], b1_context=_runtime_b1_context(_runtime_metadata()),
    )
    assert set(receipts) == {"P_DAILY"}
    view = compile_data_object_admission_view(
        [obligation], receipts,
        operation_id="b1-nosend-20261023",
        intended_use="NO_SEND_RESEARCH",
        as_of="2026-10-23T10:00:00+00:00",
    )
    assert view["product_admitted_count"] == 1
    assert view["learning_admitted_count"] == 0
    assert view["objects"][0]["storage_admitted"] is False
    assert view["objects"][0]["strategy_upgrade_authority"] is False


def test_runtime_missing_source_is_legal_nontrigger_not_fake_product():
    obligation = _runtime_obligation()
    for key in (
        "provider_identity", "provider_route", "source_identity_hash",
        "source_content_sha256", "source_observed_at",
        "source_available_at", "completed_through",
    ):
        obligation.pop(key)
    receipts = compile_b1_runtime_admission_receipts(
        [obligation], b1_context=_runtime_b1_context(),
    )
    assert receipts == {}
    view = compile_data_object_admission_view(
        [obligation], receipts, operation_id="b1-nosend-20261023",
        intended_use="NO_SEND_RESEARCH",
        as_of="2026-10-23T10:00:00+00:00",
    )
    assert view["product_admitted_count"] == 0
    assert view["objects"][0]["admission_state"] == "NOT_OBSERVED"


@pytest.mark.parametrize("mutation", ["identity", "content", "provider", "clock", "units", "extra_dataset"])
def test_runtime_receipt_source_identity_and_scope_tampering_rejected(mutation):
    obligation = _runtime_obligation()
    metadata = _runtime_metadata()
    if mutation == "identity":
        metadata["source_identity_hash"] = "d" * 64
    if mutation == "content":
        metadata["source_content_sha256"] = "e" * 64
    if mutation == "provider":
        metadata["provider_identity"] = "OtherFetcher"
    if mutation == "clock":
        metadata["source_observed_at"] = "2026-10-24T01:00:00+00:00"
    if mutation == "units":
        metadata["volume_unit"] = "LOT"
    b1 = _runtime_b1_context(metadata)
    if mutation == "extra_dataset":
        b1["source_metadata_by_dataset"] = {
            "E_PRICE_1D": metadata,
        }
    with pytest.raises(DataObjectAdmissionError, match="B1_"):
        compile_b1_runtime_admission_receipts([obligation], b1_context=b1)


@pytest.mark.parametrize("mutation", ["unknown_revision", "future_vintage"])
def test_runtime_receipt_keeps_unknown_or_future_asof_not_admitted(mutation):
    obligation = _runtime_obligation()
    if mutation == "unknown_revision":
        obligation["revision"] = "UNKNOWN"
    if mutation == "future_vintage":
        obligation["vintage"] = "2026-10-24T10:00:00+00:00"
    receipts = compile_b1_runtime_admission_receipts(
        [obligation], b1_context=_runtime_b1_context(),
    )
    view = compile_data_object_admission_view(
        [obligation], receipts,
        operation_id="b1-nosend-20261023",
        intended_use="NO_SEND_RESEARCH",
        as_of="2026-10-23T10:00:00+00:00",
    )
    assert view["product_admitted_count"] == 0


def _rights_package():
    return {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": "b1-nosend-20261023",
        "intended_use": "NO_SEND_RESEARCH",
        "allowed_instruments": ["600519", "510300"],
        "rights_by_dataset": {
            "P_DAILY": _grant(
                dataset_id="P_DAILY", consumer="ASSET_RESEARCH_BRIEF"
            ),
            "P_5M": _grant(
                dataset_id="P_5M", consumer="ASSET_RESEARCH_BRIEF"
            ),
            "E_PRICE_1D": _grant(
                dataset_id="E_PRICE_1D", consumer="ETF_ASSET_BRIEF"
            ),
            "E_PRICE_5M": _grant(
                dataset_id="E_PRICE_5M", consumer="ETF_ASSET_BRIEF"
            ),
        },
    }


def _clocked_daily_frame(code: str, observed_at: datetime) -> pd.DataFrame:
    frame = pd.DataFrame([{
        "date": "2026-10-23", "open": 10.0, "high": 11.0,
        "low": 9.5, "close": 10.5, "volume": 1000,
        "amount": 10500.0, "pct_chg": 1.0,
    }])
    identity = build_daily_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="synthetic.preacquire.daily",
        actual_response_branch="frequency=d;adjustflag=2",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="SYNTHETIC_TEST_ONLY",
        requested_start="2026-10-23",
        requested_end="2026-10-23",
        currency="CNY", volume_unit="share", amount_unit="CNY",
        identity_state="OBSERVED", observed_at=observed_at,
        instrument=code, revision="synthetic-revision",
        vintage=observed_at.isoformat(),
    )
    attach_daily_data_identity(frame, identity)
    return frame


def _clocked_intraday_frames(code: str, observed_at: datetime) -> dict:
    bar_end = pd.Timestamp("2026-10-23T07:00:00+00:00")
    frame = pd.DataFrame([{
        "date": "2026-10-23", "time": "20261023150000000",
        "datetime": bar_end, "bar_start": bar_end - pd.Timedelta(minutes=5),
        "bar_end": bar_end, "available_at": bar_end,
        "code": code, "open": 10.0, "high": 10.5, "low": 9.8,
        "close": 10.2, "volume": 1000, "amount": 10200.0,
        "data_source": "BaostockFetcher", "session": "2026-10-23:PM",
    }])
    identity = build_intraday_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="synthetic.preacquire.5m",
        package_version="0.9.4",
        query_identity={
            "code": "sh." + code, "fields": "synthetic",
            "start_date": "2026-10-23", "end_date": "2026-10-23",
            "frequency": "5", "adjustflag": "2",
        },
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="SYNTHETIC_TEST_ONLY",
        timeframe="5m", timezone_name="Asia/Shanghai",
        session_calendar="XSHG", currency="CNY",
        volume_unit="share", amount_unit="CNY",
        requested_start="2026-10-23", requested_end="2026-10-23",
        identity_state="OBSERVED", observed_at=observed_at,
        source_rows_sha256="f" * 64,
        revision="synthetic-revision", vintage=observed_at.isoformat(),
    )
    attach_intraday_data_identity(frame, identity)
    return {"5m": frame}


def test_b1_bls_atomic_preacquire_is_shared_run_local_and_requires_full_grant_set():
    from data_provider.bls_public_data_fetcher import BLS_SERIES_BY_DATASET

    operation_id = "b1-bls-nosend-20261028"
    rights = {
        dataset_id: _bls_grant(dataset_id, operation_id)
        for dataset_id in BLS_SERIES_BY_DATASET
    }
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": operation_id,
        "intended_use": "NO_SEND_RESEARCH",
        "allowed_instruments": ["600519", "510300"],
        "rights_by_dataset": rights,
    }
    pipeline._b1_global_atomic_observations = {}
    pipeline._b1_global_atomic_failure = None
    observations = {
        dataset_id: {
            "dataset_id": dataset_id,
            "state": "READY",
            "facts": {"yoy_pct": index + 2.0},
            "source_fact_hash": str(index + 1) * 64,
        }
        for index, dataset_id in enumerate(BLS_SERIES_BY_DATASET)
    }
    bundle = {
        "atomic_subfield_source_observations": observations,
        "storage_admitted": False,
        "training_admitted": False,
    }
    with patch(
        "data_provider.bls_public_data_fetcher.fetch_us_inflation_atomic_bundle",
        return_value=bundle,
    ) as producer:
        pipeline._preacquire_b1_global_atomic_sources(
            target_reference_time=datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc),
        )
    producer.assert_called_once()
    assert pipeline._b1_global_atomic_observations == observations
    context = pipeline._b1_nonprice_fundamental_context(
        code="510300", asset_route="ETF", market="cn",
    )
    assert context["atomic_subfield_source_observations"] == observations
    assert context["atomic_subfield_failure"] is None

    incomplete = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    incomplete.b1_rights_context = {
        **pipeline.b1_rights_context,
        "rights_by_dataset": dict(list(rights.items())[:-1]),
    }
    with patch(
        "data_provider.bls_public_data_fetcher.fetch_us_inflation_atomic_bundle",
    ) as forbidden:
        with pytest.raises(
            DataObjectAdmissionError, match="B1_BLS_ATOMIC_GRANT_SET_INCOMPLETE",
        ):
            incomplete._preacquire_b1_global_atomic_sources(
                target_reference_time=datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc),
            )
    forbidden.assert_not_called()


def test_b1_bls_environment_failure_keeps_atoms_unknown_without_aborting_price_chain():
    from data_provider.bls_public_data_fetcher import (
        BLS_SERIES_BY_DATASET,
        BLSPublicDataError,
    )

    operation_id = "b1-bls-nosend-20261028"
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": operation_id,
        "intended_use": "NO_SEND_RESEARCH",
        "allowed_instruments": ["600519"],
        "rights_by_dataset": {
            dataset_id: _bls_grant(dataset_id, operation_id)
            for dataset_id in BLS_SERIES_BY_DATASET
        },
    }
    with patch(
        "data_provider.bls_public_data_fetcher.fetch_us_inflation_atomic_bundle",
        side_effect=BLSPublicDataError("BLS_PROVIDER_REQUEST_FAILED"),
    ):
        pipeline._preacquire_b1_global_atomic_sources(
            target_reference_time=datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc),
        )
    assert pipeline._b1_global_atomic_observations == {}
    assert pipeline._b1_global_atomic_failure == "BLS_PROVIDER_REQUEST_FAILED"


def test_b1_partial_nonprice_grants_bound_requests_before_dispatch():
    """Filtering results after fetching ungranted datasets is not preflight."""
    from data_provider.baostock_fetcher import BaostockFetcher
    from tests.test_baostock_fetcher import _NonpriceBaostock, _session

    operation_id = "bounded-nonprice-requests"
    rights = {
        dataset: _grant(dataset_id=dataset, consumer=consumer, operation_id=operation_id)
        for dataset, consumer in (
            ("P_CODE_IDENTITY", "ASSET_RESEARCH_BRIEF"),
            ("S_CLASSIFICATION", "MARKET_SECTOR_SECTION"),
        )
    }
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": operation_id,
        "intended_use": "NO_SEND_RESEARCH",
        "allowed_instruments": ["600519"],
        "rights_by_dataset": rights,
    }
    fake = _NonpriceBaostock()
    with patch.object(BaostockFetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        pipeline._preacquire_b1_nonprice_sources(
            ["600519"], {"600519": date(2026, 10, 27)},
            target_reference_time=datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc),
        )
    assert [name for name, _ in fake.calls] == [
        "query_stock_basic", "query_stock_industry",
    ]
    assert set(pipeline._b1_nonprice_shared_observations) == {"S_CLASSIFICATION"}
    assert set(pipeline._b1_nonprice_stock_bundles["600519"][
        "data_object_source_observations"
    ]) == {"P_CODE_IDENTITY"}


def test_b1_nonprice_preacquire_uses_only_explicit_grants_and_separates_asset_scope():
    operation_id = "b1-nonprice-nosend-20261028"
    rights = {
        dataset_id: _grant(
            dataset_id=dataset_id,
            consumer=consumer,
            operation_id=operation_id,
        )
        for dataset_id, consumer in (
            ("P_CODE_IDENTITY", "ASSET_RESEARCH_BRIEF"),
            ("S_CLASSIFICATION", "MARKET_SECTOR_SECTION"),
        )
    }
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": operation_id,
        "intended_use": "NO_SEND_RESEARCH",
        "allowed_instruments": ["600519"],
        "rights_by_dataset": rights,
    }
    pipeline._b1_nonprice_shared_observations = {}
    pipeline._b1_nonprice_stock_bundles = {}
    bundle = {
        "schema_version": "baostock-cn-nonprice-research-bundle-v1",
        "data_object_source_observations": {
            "P_CODE_IDENTITY": {"dataset_id": "P_CODE_IDENTITY"},
            "S_CLASSIFICATION": {"dataset_id": "S_CLASSIFICATION"},
            "P_VAL_RATIO": {"dataset_id": "P_VAL_RATIO"},
        },
        "quality_growth_input": None,
        "business_valuation_input": None,
    }
    with patch(
        "data_provider.baostock_fetcher.BaostockFetcher.get_cn_stock_nonprice_research_bundle",
        return_value=bundle,
    ) as producer:
        pipeline._preacquire_b1_nonprice_sources(
            ["600519"],
            {"600519": date(2026, 10, 27)},
            target_reference_time=datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc),
        )

    producer.assert_called_once_with(
        "600519", research_date="2026-10-27",
        requested_dataset_ids={"P_CODE_IDENTITY", "S_CLASSIFICATION"},
    )
    assert set(pipeline._b1_nonprice_shared_observations) == {"S_CLASSIFICATION"}
    stock_bundle = pipeline._b1_nonprice_stock_bundles["600519"]
    assert set(stock_bundle["data_object_source_observations"]) == {"P_CODE_IDENTITY"}
    stock_context = pipeline._b1_nonprice_fundamental_context(
        code="600519", asset_route="STOCK", market="cn",
    )
    etf_context = pipeline._b1_nonprice_fundamental_context(
        code="510300", asset_route="ETF", market="cn",
    )
    assert set(stock_context["data_object_source_observations"]) == {
        "P_CODE_IDENTITY", "S_CLASSIFICATION",
    }
    assert set(etf_context["data_object_source_observations"]) == {
        "S_CLASSIFICATION",
    }
    assert "P_VAL_RATIO" not in stock_context["data_object_source_observations"]


def test_b1_constructor_skips_database_model_search_and_context_services():
    config = SimpleNamespace(
        max_workers=4, save_context_snapshot=False,
        b1_rights_evidence_path=None,
        enable_realtime_quote=True, realtime_source_priority=[],
        enable_chip_distribution=True,
    )
    with patch("src.core.pipeline.get_db") as get_db, \
         patch("src.core.pipeline.GeminiAnalyzer") as analyzer, \
         patch("src.core.pipeline.SearchService") as search, \
         patch("src.core.pipeline.MarketHotspotService") as hotspot, \
         patch("src.core.pipeline.SocialSentimentService") as social, \
         patch("src.core.pipeline.MarketStructureService") as market_structure, \
         patch("src.core.pipeline.RelativeStrengthService") as relative_strength, \
         patch("src.core.pipeline.DataFetcherManager"), \
         patch("src.core.pipeline.StockTrendAnalyzer"), \
         patch("src.core.pipeline.NotificationService"):
        pipeline = StockAnalysisPipeline(
            config=config,
            query_source="cli",
            b1_rights_context=_rights_package(),
        )
    get_db.assert_not_called()
    analyzer.assert_not_called()
    search.assert_not_called()
    hotspot.assert_not_called()
    social.assert_not_called()
    market_structure.assert_not_called()
    relative_strength.assert_not_called()
    assert pipeline.max_workers == 1
    assert pipeline.search_service is None
    assert pipeline.social_sentiment_service is None
    with pytest.raises(DataObjectAdmissionError, match="PERSISTENCE_FORBIDDEN"):
        pipeline.db.has_today_data("600519", date(2026, 10, 23))
    with pytest.raises(DataObjectAdmissionError, match="MODEL_CALL_FORBIDDEN"):
        pipeline.analyzer.analyze({})


def test_b1_preacquires_all_assets_before_one_common_decision_clock():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _rights_package()
    pipeline.p0_bounded_trial = False
    target = date(2026, 10, 23)
    observed = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
    daily = {
        code: _clocked_daily_frame(code, observed)
        for code in ("600519", "510300")
    }
    intraday = {
        code: _clocked_intraday_frames(code, observed)
        for code in ("600519", "510300")
    }
    pipeline._resolve_resume_target_date = MagicMock(return_value=target)
    pipeline.fetch_and_save_stock_data = MagicMock(return_value=(True, None))
    pipeline._get_run_local_daily_history = MagicMock(
        side_effect=lambda *, code, target_date: daily[code]
    )
    pipeline._load_current_stock_intraday_timeframes = MagicMock(
        side_effect=lambda *, code, **_kwargs: intraday[code]
    )
    pipeline._get_run_local_intraday_timeframes = MagicMock(
        side_effect=lambda *, code, target_date: intraday[code]
    )

    decision = pipeline._preacquire_b1_sources(
        ["600519", "510300"],
        target_reference_time=datetime(
            2026, 10, 23, 8, 55, tzinfo=timezone.utc,
        ),
    )

    assert decision > observed
    assert pipeline.b1_rights_context["research_as_of"] == decision.isoformat()
    assert pipeline.fetch_and_save_stock_data.call_count == 2
    assert pipeline._load_current_stock_intraday_timeframes.call_count == 2
    assert all(
        call.kwargs["market"] == "cn"
        for call in pipeline._load_current_stock_intraday_timeframes.call_args_list
    )
    assert all(
        call.kwargs["observation_time_frozen"] is False
        for call in pipeline.fetch_and_save_stock_data.call_args_list
    )
    assert pipeline._b1_preacquisition_active is False


def test_rights_package_loader_and_run_clock_binding(tmp_path):
    path = tmp_path / "b1-rights.json"
    path.write_text(json.dumps(_rights_package()), encoding="utf-8")
    loaded = load_b1_rights_context(path)
    assert loaded["operation_id"] == "b1-nosend-20261023"
    assert len(loaded["rights_context_sha256"]) == 64
    bound = bind_b1_rights_context_as_of(
        loaded, datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
    )
    assert bound["research_as_of"] == "2026-10-23T10:00:00+00:00"


def test_rights_package_rejects_extra_fields_and_invalid_json(tmp_path):
    package = _rights_package()
    package["api_token"] = "must-not-be-stored-here"
    with pytest.raises(DataObjectAdmissionError, match="SCHEMA_MISMATCH"):
        validate_b1_rights_context(package)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")
    with pytest.raises(DataObjectAdmissionError, match="JSON_INVALID"):
        load_b1_rights_context(invalid)
    assert load_b1_rights_context(MagicMock()) is None


def test_direct_daily_call_rejects_malformed_package_before_provider():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.fetcher_manager = MagicMock()
    pipeline.db = MagicMock()
    pipeline._resolve_resume_target_date = MagicMock(
        return_value=date(2026, 10, 22)
    )
    malformed = _rights_package()
    malformed["api_token"] = "forbidden-extra-field"
    pipeline.b1_rights_context = malformed

    success, reason = pipeline.fetch_and_save_stock_data(
        "600519",
        current_time=datetime(2026, 10, 23, 10, tzinfo=timezone.utc),
    )

    assert success is False
    assert "B1_RIGHTS_CONTEXT_SCHEMA_MISMATCH" in (reason or "")
    pipeline.fetcher_manager.get_stock_name.assert_not_called()
    pipeline.fetcher_manager.get_daily_data.assert_not_called()
    pipeline.db.has_today_data.assert_not_called()
    pipeline.db.save_daily_data.assert_not_called()


def test_b1_run_and_single_stock_notification_reject_before_any_effect():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _rights_package()
    with pytest.raises(ValueError, match="B1_NO_SEND"):
        pipeline.run(stock_codes=["600519"], send_notification=True)
    assert pipeline.process_single_stock(
        "600519", single_stock_notify=True
    ) is None


def _admitted_result(code: str, dataset_ids=("P_DAILY", "P_5M")):
    objects = [
        {
            "dataset_id": dataset_id,
            "product_admitted": True,
            "learning_admitted": False,
        }
        for dataset_id in dataset_ids
    ]
    return SimpleNamespace(
        success=True,
        error_message=None,
        code=code,
        dashboard={
            "factor_decision": {
                "evidence_traceability": {
                    "complete_research_universe": {
                        "data_object_admission": {"objects": objects}
                    }
                }
            }
        },
    )


def test_b1_target_contract_rejects_unlisted_or_missing_5m_before_provider():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.p0_bounded_trial = False
    pipeline.max_workers = 1
    pipeline.query_id = None
    pipeline.config = SimpleNamespace(stock_list=[], refresh_stock_list=lambda: None)
    pipeline.b1_rights_context = _rights_package()
    with pytest.raises(DataObjectAdmissionError, match="TARGET_INSTRUMENT"):
        pipeline.run(
            stock_codes=["000001"], send_notification=False,
            current_time=datetime(2026, 10, 23, 10, tzinfo=timezone.utc),
        )

    incomplete = _rights_package()
    incomplete["rights_by_dataset"].pop("P_5M")
    pipeline.b1_rights_context = incomplete
    with pytest.raises(DataObjectAdmissionError, match="UPSTREAM_RIGHTS_EVIDENCE_MISSING"):
        pipeline.run(
            stock_codes=["600519"], send_notification=False,
            current_time=datetime(2026, 10, 23, 10, tzinfo=timezone.utc),
        )


def test_b1_result_requires_both_daily_and_5m_product_admission():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    with pytest.raises(DataObjectAdmissionError, match="P_5M"):
        pipeline._assert_b1_results_product_admitted(
            [_admitted_result("600519", ("P_DAILY",))], ["600519"]
        )
    pipeline._assert_b1_results_product_admitted(
        [_admitted_result("600519")], ["600519"]
    )


def test_b1_run_renders_original_report_only_in_memory():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _rights_package()
    pipeline.p0_bounded_trial = False
    pipeline.max_workers = 1
    pipeline.query_id = None
    pipeline.research_selection_context = {}
    pipeline.fetcher_manager = MagicMock()
    pipeline.config = SimpleNamespace(
        stock_list=[], refresh_stock_list=lambda: None,
        single_stock_notify=False, report_type="simple", analysis_delay=0,
    )
    result = _admitted_result("600519")
    pipeline.process_single_stock = MagicMock(return_value=result)
    decision_time = datetime(2026, 10, 23, 10, 5, tzinfo=timezone.utc)
    pipeline._preacquire_b1_sources = MagicMock(return_value=decision_time)
    pipeline._generate_aggregate_report = MagicMock(
        return_value="B1_ORIGINAL_REPORT_IN_MEMORY"
    )
    pipeline._save_local_report = MagicMock()
    pipeline._send_notifications = MagicMock()

    observed = pipeline.run(
        stock_codes=["600519"], send_notification=False,
        current_time=datetime(2026, 10, 23, 10, tzinfo=timezone.utc),
    )

    assert observed == [result]
    assert pipeline.b1_no_send_report == "B1_ORIGINAL_REPORT_IN_MEMORY"
    pipeline._generate_aggregate_report.assert_called_once_with(
        [result], ReportType.SIMPLE,
    )
    pipeline._save_local_report.assert_not_called()
    pipeline._send_notifications.assert_not_called()
    pipeline.fetcher_manager.prefetch_daily_klines.assert_not_called()
    pipeline.fetcher_manager.prefetch_realtime_quotes.assert_not_called()
    pipeline.fetcher_manager.prefetch_stock_names.assert_not_called()
    pipeline._preacquire_b1_sources.assert_called_once()
    assert pipeline.process_single_stock.call_args.kwargs["current_time"] == decision_time


def test_b1_daily_context_ma_status_is_pure_pipeline_computation():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.db = MagicMock()
    # This isolated pure-function fixture enables B1 without claiming any
    # source grant or invoking Provider/Registry/production storage.
    pipeline.b1_rights_context = {"intended_use": "NO_SEND_RESEARCH"}
    frame = pd.DataFrame([
        {
            "date": "2026-10-22", "close": 9.0, "volume": 100,
            "ma5": 8.5, "ma10": 8.0, "ma20": 7.5,
        },
        {
            "date": "2026-10-23", "close": 11.0, "volume": 120,
            "ma5": 10.0, "ma10": 9.5, "ma20": 9.0,
        },
    ])

    context = pipeline._build_analysis_context_from_daily_df("600519", frame)

    assert context["ma_status"].startswith("多头排列")
    assert context["volume_change_ratio"] == 1.2
    assert context["price_change_ratio"] == pytest.approx(22.22)
    pipeline.db._analyze_ma_status.assert_not_called()
    assert pipeline.db.mock_calls == []


def test_b1_analyze_stock_skips_unadmitted_sources_db_and_model():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = bind_b1_rights_context_as_of(
        validate_b1_rights_context(_rights_package()),
        datetime(2026, 10, 23, 10, tzinfo=timezone.utc),
    )
    pipeline.p0_bounded_trial = False
    pipeline.p0_receipt_only = False
    pipeline.query_source = "system"
    pipeline.analysis_phase = "auto"
    pipeline.portfolio_context = None
    pipeline.analysis_skills = None
    pipeline.daily_market_context_enabled = False
    pipeline.config = SimpleNamespace(
        report_language="zh", enable_realtime_quote=True,
        agent_mode=False, agent_skills=[], save_context_snapshot=False,
        litellm_model="forbidden-model",
    )
    pipeline.fetcher_manager = MagicMock()
    pipeline.db = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.search_service = MagicMock()
    pipeline.social_sentiment_service = MagicMock()
    pipeline.trend_analyzer = MagicMock()
    pipeline._emit_progress = MagicMock()
    pipeline._coerce_daily_market_context_date = MagicMock(
        return_value=date(2026, 10, 22)
    )
    pipeline._get_run_local_daily_history = MagicMock(return_value=None)
    pipeline._load_current_stock_intraday_timeframes = MagicMock(return_value={})
    pipeline._build_analysis_context_from_daily_df = MagicMock(return_value=None)
    pipeline._enhance_context = MagicMock(return_value={"realtime": {}})
    pipeline._attach_daily_market_context = MagicMock()
    pipeline._build_legacy_analysis_artifacts = MagicMock(return_value={})
    pipeline._build_analysis_context_pack_outputs = MagicMock(return_value=("", {}))
    pipeline._build_method_execution_receipts = MagicMock(return_value={})
    pipeline._refresh_decision_action_for_final_result = MagicMock()
    pipeline._attach_factor_decision_summary = MagicMock()
    pipeline._assert_b1_results_product_admitted = MagicMock()
    pipeline._attach_research_delivery_state = MagicMock()
    pipeline._promote_p0_deterministic_result_after_explanation_failure = MagicMock()

    result = pipeline.analyze_stock(
        "600519", ReportType.SIMPLE, query_id="b1-test",
        current_time=datetime(2026, 10, 23, 10, tzinfo=timezone.utc),
    )

    assert result is not None and result.success is True
    pipeline.fetcher_manager.get_stock_name.assert_not_called()
    pipeline.fetcher_manager.get_realtime_quote.assert_not_called()
    pipeline.fetcher_manager.get_chip_distribution.assert_not_called()
    pipeline.fetcher_manager.get_fundamental_context.assert_not_called()
    pipeline.db.save_fundamental_snapshot.assert_not_called()
    pipeline.db.get_data_range.assert_not_called()
    pipeline.db.save_analysis_history.assert_not_called()
    pipeline.analyzer.analyze.assert_not_called()
    pipeline.search_service.search_comprehensive_intel.assert_not_called()
    pipeline._assert_b1_results_product_admitted.assert_called_once_with(
        [result], ["600519"]
    )
