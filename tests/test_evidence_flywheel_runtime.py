# -*- coding: utf-8 -*-
"""Deterministic tests for the local-only minimum evidence-flywheel runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from types import SimpleNamespace

import pytest

from src.config import Config
from src.services.evidence_flywheel_runtime import (
    EvidenceFlywheelBoundaryError,
    EvidenceFlywheelRuntimeError,
    build_pit_manifest_receipt,
    evaluate_prediction_outcome,
    record_canonical_run,
)
from src.services.pit_dataset_service import PITDatasetService
from src.storage import DatabaseManager


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "evidence_flywheel.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        single_stock_notify=True,
        merge_email_notification=True,
        report_type="full",
        report_language="en",
        report_integrity_retry=3,
        agent_mode=True,
        agent_skills=["sentiment"],
        analysis_delay=7,
        market_review_enabled=True,
    )


def _ledger_receipt(*, created: bool = True) -> dict:
    return {
        "id": 7,
        "created": created,
        "prediction_hash": "a" * 64,
        "evidence_hash": "b" * 64,
        "feature_schema_hash": "c" * 64,
        "pit_eligible": True,
        "pit_ineligibility_reasons": [],
        "durability_state": "LOCAL_DB_ONLY",
    }


def _pipeline_factory(observed: dict, receipt: dict | None):
    class FakePipeline:
        def __init__(self, **kwargs):
            observed["init"] = kwargs

        def run(self, **kwargs):
            observed["run"] = kwargs
            return [
                SimpleNamespace(
                    code="600519",
                    success=True,
                    prediction_ledger_receipt=receipt,
                )
            ]

    return FakePipeline


def _cost_identity() -> dict:
    return {
        "schema_version": "cost-identity-v2",
        "market": "cn",
        "instrument_type": "stock",
        "exchange": "SH",
        "currency": "CNY",
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
        "source": "unit-test-fixture",
        "version": "test-v2",
        "cost_model_mode": "synthetic-test-only",
        "commission_basis": "ALL_IN_INCLUDES_EXCHANGE_HANDLING_AND_REGULATORY_LEVY_OTHER_IS_TRANSFER_ONLY",
        "minimum_fee_policy": "PER_SIDE_MAX_NOTIONAL_RATE_OR_MINIMUM_CNY",
        "buy_fee_rate": 0.0,
        "sell_fee_rate": 0.0,
        "sell_tax_rate": 0.0,
        "other_buy_rate": 0.0,
        "other_sell_rate": 0.0,
        "buy_slippage_bps": 0.0,
        "sell_slippage_bps": 0.0,
        "minimum_commission_cny": 0.0,
        "reference_entry_notional_cny": 100_000.0,
    }


def test_record_phase_reuses_bounded_pipeline_and_restores_config(monkeypatch) -> None:
    monkeypatch.delenv("RESEARCH_STATE_DURABILITY_ENABLED", raising=False)
    observed = {}
    config = _config()

    receipt = record_canonical_run(
        stock_codes=["600519"],
        code_sha="1" * 40,
        config=config,
        pipeline_factory=_pipeline_factory(observed, _ledger_receipt()),
        current_time=datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
    )

    assert receipt["status"] == "RECORDED"
    assert receipt["notification_suppressed"] is True
    assert receipt["external_durability"] == "NOT_REQUESTED"
    assert receipt["training_requested"] is False
    assert receipt["ledger_receipts"][0]["prediction_hash"] == "a" * 64
    assert observed["init"]["p0_bounded_trial"] is True
    assert observed["init"]["p0_suppress_notification"] is True
    assert observed["init"]["research_code_sha"] == "1" * 40
    assert observed["run"]["send_notification"] is False
    assert observed["run"]["merge_notification"] is False
    assert os.getenv("RESEARCH_STATE_DURABILITY_ENABLED") is None
    assert config.single_stock_notify is True
    assert config.merge_email_notification is True
    assert config.report_type == "full"
    assert config.agent_mode is True
    assert config.market_review_enabled is True


def test_record_phase_accepts_idempotent_existing_ledger_receipt() -> None:
    observed = {}
    receipt = record_canonical_run(
        stock_codes=["600519"],
        code_sha="2" * 40,
        config=_config(),
        pipeline_factory=_pipeline_factory(
            observed,
            _ledger_receipt(created=False),
        ),
    )
    assert receipt["ledger_receipts"][0]["created"] is False


def test_record_phase_fails_closed_when_ledger_receipt_is_missing() -> None:
    with pytest.raises(EvidenceFlywheelRuntimeError, match="Ledger receipt"):
        record_canonical_run(
            stock_codes=["600519"],
            code_sha="3" * 40,
            config=_config(),
            pipeline_factory=_pipeline_factory({}, None),
        )


def test_record_phase_rejects_unbound_code_sha() -> None:
    with pytest.raises(EvidenceFlywheelBoundaryError, match="40-hex"):
        record_canonical_run(
            stock_codes=["600519"],
            code_sha="not-a-sha",
            config=_config(),
            pipeline_factory=_pipeline_factory({}, _ledger_receipt()),
        )


def test_outcome_phase_preserves_unmatured_state_and_frozen_identities() -> None:
    observed = {}

    class FakeOutcomeService:
        def evaluate_prediction(self, **kwargs):
            observed.update(kwargs)
            return {
                "status": "UNMATURED",
                "prediction_hash": kwargs["prediction_hash"],
                "required_forward_sessions": 3,
            }

    receipt = evaluate_prediction_outcome(
        prediction_hash="d" * 64,
        cost_identity={"cost": "frozen"},
        execution_identity={"execution": "frozen"},
        service=FakeOutcomeService(),
    )

    assert receipt["status"] == "UNMATURED"
    assert receipt["training_requested"] is False
    assert observed["cost_identity"] == {"cost": "frozen"}
    assert observed["execution_identity"] == {"execution": "frozen"}


def test_manifest_phase_is_idempotent_and_cannot_admit_training(isolated_db) -> None:
    service = PITDatasetService(db_manager=isolated_db)
    first = build_pit_manifest_receipt(
        cost_identity=_cost_identity(),
        service=service,
    )
    second = build_pit_manifest_receipt(
        cost_identity=_cost_identity(),
        service=service,
    )

    assert first["status"] == "MANIFEST_FROZEN"
    assert first["disposition"] == "created"
    assert second["disposition"] == "existing"
    assert first["dataset_hash"] == second["dataset_hash"]
    assert first["training_admission"] == "BLOCKED"
    assert first["training_requested"] is False
    assert first["external_durability"] == "NOT_REQUESTED"
    assert "NO_WHITE_BOX_OPPORTUNITIES" in first["training_admission_reasons"]
