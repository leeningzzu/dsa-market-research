# -*- coding: utf-8 -*-
"""Deterministic regressions for immutable PIT dataset manifests."""

from __future__ import annotations

import json
import hashlib
import os
from datetime import date, datetime, timedelta

import pytest

from src.config import Config
from src.repositories.pit_dataset_repo import PITDatasetRepository
from src.services.pit_dataset_service import PITDatasetService
from src.services.prediction_ledger_service import (
    PREDICTION_FEATURE_SCHEMA_HASH,
    PREDICTION_LEDGER_SCHEMA_VERSION,
)
from src.services.prediction_outcome_service import (
    PREDICTION_OUTCOME_ENGINE_VERSION,
    PRIMARY_HORIZON_IDENTITY,
    PRIMARY_LABEL_IDENTITY,
)
from src.services.research_state_projection import (
    CANONICAL_OPPORTUNITY_PROJECTION_VERSION,
    STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE,
    STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
    build_strategy_eligibility_identity,
)
from src.storage import (
    DatabaseManager,
    LearningRecordingRecord,
    PredictionLedgerRecord,
    PredictionOutcomeRecord,
)


COST_HASH = "d" * 64
CODE_SHA = "a" * 40
COHORT_ID = "c" * 64


def _strategy_eligibility_identity() -> dict:
    return build_strategy_eligibility_identity(
        {
            "schema_version": STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
            "strategy_id": "stock_trend_quality_pullback_v1",
            "state": "ELIGIBLE",
            "required_evidence": {
                key: "SATISFIED"
                for key in STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE
            },
            "reason_codes": [],
        },
        strategy_id="stock_trend_quality_pullback_v1",
    )


@pytest.fixture(autouse=True)
def _calendar_contract(monkeypatch):
    monkeypatch.setattr(
        "src.services.pit_dataset_service.resolve_historical_daily_bar_date",
        lambda market, target_date, phase: target_date,
    )


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "pit_dataset.db")
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


def _seed_prediction(
    db: DatabaseManager,
    *,
    index: int,
    session_date: date,
    route: str = "SPECIFIED_CODES",
    pit_eligible: bool = True,
    universe_snapshot_id: str | None = None,
    include_strategy_eligibility: bool = True,
) -> str:
    prediction_hash = f"{index:064x}"
    evidence_json = json.dumps(
        {"canonical_decision": {"action": "WAIT", "evidence_state": "PROVEN", "hard_veto": False}},
        sort_keys=True,
    )
    eligibility = (
        _strategy_eligibility_identity()
        if include_strategy_eligibility
        else {}
    )
    with db.session_scope() as session:
        session.add(
            PredictionLedgerRecord(
                prediction_hash=prediction_hash,
                schema_version=PREDICTION_LEDGER_SCHEMA_VERSION,
                analysis_history_id=index + 1,
                market="cn",
                stock_code=f"60{index:04d}"[-6:],
                instrument_type="stock",
                decision_time=datetime.combine(session_date, datetime.min.time()).replace(hour=10),
                decision_timezone="Asia/Shanghai",
                decision_phase="postmarket",
                session_date=session_date,
                effective_daily_bar_date=session_date,
                outcome_label_anchor=session_date,
                data_as_of=session_date,
                available_at_max=datetime.combine(session_date, datetime.min.time()).replace(hour=9),
                strategy_id="stock_trend_quality_pullback_v1",
                strategy_version="stock_trend_quality_pullback_v1",
                canonical_action="WAIT",
                horizon="3d",
                feature_schema_version="stock-factor-evidence-v1",
                feature_schema_hash=PREDICTION_FEATURE_SCHEMA_HASH,
                evidence_hash=f"{index + 10_000:064x}",
                evidence_json=evidence_json,
                opportunity_projection_version=CANONICAL_OPPORTUNITY_PROJECTION_VERSION,
                canonical_evidence_state="PROVEN",
                canonical_hard_veto=False,
                strategy_eligibility_version=eligibility.get(
                    "strategy_eligibility_version"
                ),
                strategy_eligibility_state=eligibility.get(
                    "strategy_eligibility_state"
                ),
                strategy_eligibility_hash=eligibility.get(
                    "strategy_eligibility_hash"
                ),
                strategy_eligibility_json=eligibility.get(
                    "strategy_eligibility_json"
                ),
                code_sha=CODE_SHA,
                provider_identity="AkshareFetcher",
                adjustment_basis="qfq",
                universe_snapshot_id=universe_snapshot_id,
                asset_identity_hash=f"{index + 20_000:064x}",
                asset_identity_json="{}",
                data_snapshot_identity=f"{index + 30_000:064x}",
                selection_source=route,
                selection_context_hash=f"{index + 40_000:064x}",
                selection_context_json="{}",
                pit_eligible=pit_eligible,
                pit_ineligibility_json="[]" if pit_eligible else '["TEST_GAP"]',
                durability_state="LOCAL_DB_ONLY",
                created_at=datetime.combine(session_date, datetime.min.time()).replace(hour=10, minute=1),
            )
        )
    return prediction_hash


def _seed_recording_cohort(
    db: DatabaseManager,
    prediction_hashes: list[str],
    *,
    cohort_id: str = COHORT_ID,
    disposition_by_hash: dict[str, str] | None = None,
) -> None:
    overrides = disposition_by_hash or {}
    with db.session_scope() as session:
        for prediction_hash in prediction_hashes:
            row = session.query(PredictionLedgerRecord).filter_by(
                prediction_hash=prediction_hash
            ).one()
            intent_hash = hashlib.sha256(
                f"recording:{prediction_hash}".encode("utf-8")
            ).hexdigest()
            disposition = overrides.get(prediction_hash, "RECORDED")
            row.recording_intent_hash = intent_hash
            row.intended_cohort_id = cohort_id
            session.add(
                LearningRecordingRecord(
                    recording_intent_hash=intent_hash,
                    schema_version="learning-recording-intent-v1",
                    policy_version="learning-recording-policy-v1",
                    analysis_history_id=row.analysis_history_id,
                    intended_cohort_id=cohort_id,
                    stock_code=row.stock_code,
                    market=row.market,
                    report_type="simple",
                    strategy_id=row.strategy_id,
                    strategy_version=row.strategy_version,
                    canonical_binding_hash=row.evidence_hash,
                    data_snapshot_identity=row.data_snapshot_identity,
                    code_sha=row.code_sha,
                    selection_source=row.selection_source,
                    selection_context_hash=cohort_id,
                    disposition=disposition,
                    reason_code=(
                        "TEST_TECHNICAL_LOSS"
                        if disposition == "TECHNICALLY_LOST"
                        else "TEST_LAWFUL_REJECTION"
                        if disposition == "LAWFULLY_REJECTED"
                        else None
                    ),
                    prediction_hash=(
                        prediction_hash if disposition == "RECORDED" else None
                    ),
                    retry_count=0,
                )
            )


def _seed_outcome(
    db: DatabaseManager,
    *,
    prediction_hash: str,
    decision_session: date,
    exit_session: date,
    available_at: datetime,
    suffix: int,
    supersedes: str | None = None,
) -> str:
    outcome_hash = f"{suffix + 50_000:064x}"
    with db.session_scope() as session:
        session.add(
            PredictionOutcomeRecord(
                outcome_hash=outcome_hash,
                root_identity_hash=f"{suffix + 60_000:064x}",
                prediction_hash=prediction_hash,
                label_identity=PRIMARY_LABEL_IDENTITY,
                horizon_identity=PRIMARY_HORIZON_IDENTITY,
                cost_identity_hash=COST_HASH,
                cost_identity_json="{}",
                evaluation_engine_version=PREDICTION_OUTCOME_ENGINE_VERSION,
                supersedes_outcome_hash=supersedes,
                correction_reason="test-correction" if supersedes else None,
                decision_session=decision_session,
                entry_session=decision_session + timedelta(days=1),
                exit_session=exit_session,
                execution_state="FILLED",
                entry_price=100.0,
                exit_price=101.0,
                gross_return_pct=1.0,
                net_return_pct=1.0,
                label_value=1,
                label_status="TAKE_SUCCESS",
                data_snapshot_identity=f"{suffix + 70_000:064x}",
                provider_identity="AkshareFetcher",
                adjustment_basis="qfq",
                available_at=available_at,
                created_at=available_at,
            )
        )
    return outcome_hash


def _seed_twenty_sessions(
    db: DatabaseManager,
    *,
    auto_screen: bool = False,
    bind_recording: bool = True,
) -> list[str]:
    start = date(2026, 1, 1)
    hashes = []
    for index in range(20):
        day = start + timedelta(days=index)
        prediction_hash = _seed_prediction(
            db,
            index=index + 1,
            session_date=day,
            route="AUTO_SCREEN" if auto_screen else "SPECIFIED_CODES",
        )
        hashes.append(prediction_hash)
        _seed_outcome(
            db,
            prediction_hash=prediction_hash,
            decision_session=day,
            exit_session=day + timedelta(days=3),
            available_at=datetime.combine(day + timedelta(days=3), datetime.min.time()).replace(hour=8),
            suffix=index + 1,
        )
    if bind_recording:
        _seed_recording_cohort(db, hashes)
    return hashes


def test_white_box_opportunity_survives_without_raw_evidence_json(isolated_db) -> None:
    prediction_hash = _seed_prediction(
        isolated_db,
        index=700,
        session_date=date(2026, 1, 1),
    )
    with isolated_db.session_scope() as session:
        row = session.query(PredictionLedgerRecord).filter_by(
            prediction_hash=prediction_hash
        ).one()
        row.evidence_json = None

    with isolated_db.get_session() as session:
        restored = session.query(PredictionLedgerRecord).filter_by(
            prediction_hash=prediction_hash
        ).one()
        assert PITDatasetService._is_white_box_opportunity(restored) is True


def test_pre_correction_strategy_identity_is_outside_current_denominator(
    isolated_db,
) -> None:
    hashes = _seed_twenty_sessions(isolated_db)
    rejected_hash = hashes[0]
    with isolated_db.session_scope() as session:
        row = session.query(PredictionLedgerRecord).filter_by(
            prediction_hash=rejected_hash
        ).one()
        row.opportunity_projection_version = "canonical-opportunity-v2"
        row.strategy_eligibility_version = "strategy-eligibility-v1"

    result = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )
    assert result["manifest"]["counts"]["denominator"] == 19
    assert rejected_hash not in {
        item["prediction_hash"]
        for item in result["manifest"]["assignments"]
    }


def test_manifest_is_chronological_grouped_sealed_and_idempotent(isolated_db) -> None:
    _seed_twenty_sessions(isolated_db)
    service = PITDatasetService(db_manager=isolated_db)

    first = service.build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )
    second = service.build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )

    assert first["dataset_hash"] == second["dataset_hash"]
    assert first["disposition"] == "created"
    assert second["disposition"] == "existing"
    manifest = first["manifest"]
    assert manifest["session_boundaries"] == {
        "train_first": "2026-01-01",
        "train_last": "2026-01-12",
        "validation_first": "2026-01-13",
        "validation_last": "2026-01-16",
        "final_test_first": "2026-01-17",
        "final_test_last": "2026-01-20",
    }
    assert manifest["final_test_state"] == "SEALED"
    assert manifest["schema_version"] == "pit-dataset-manifest-v3"
    assert manifest["dataset_purpose"] == "ASSET_LEVEL_META_FILTER_ON_STRATEGY_ELIGIBLE_OPPORTUNITIES_V3"
    assert manifest["recording_coverage_state"] == "COMPLETE"
    assert "RECORDING_COVERAGE_INCOMPLETE" not in first["training_admission_reasons"]
    assert manifest["opportunity_projection_version"] == CANONICAL_OPPORTUNITY_PROJECTION_VERSION
    assert manifest["strategy_eligibility_version"] == STRATEGY_ELIGIBILITY_SCHEMA_VERSION
    assert manifest["counts"]["raw_by_fold"] == {"TRAIN": 12, "VALIDATION": 4, "FINAL_TEST": 4}
    assert manifest["counts"]["included_by_fold"]["TRAIN"] > 0
    assert manifest["counts"]["included_by_fold"]["VALIDATION"] > 0
    assert all("label_value" not in item for item in manifest["assignments"])
    assert all("net_return_pct" not in item for item in manifest["assignments"])
    assert PITDatasetRepository(isolated_db).get_by_hash(first["dataset_hash"]) is not None


def test_explicit_recording_cohort_is_complete_when_every_recorded_intent_reads_back(isolated_db) -> None:
    hashes = _seed_twenty_sessions(isolated_db)

    result = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )
    manifest = result["manifest"]

    assert manifest["recording_coverage_state"] == "COMPLETE"
    assert manifest["recording_coverage"]["counts"] == {
        "intended": 20,
        "recorded": 20,
        "lawfully_rejected": 0,
        "pending": 0,
        "technically_lost": 0,
    }
    assert "RECORDING_COVERAGE_INCOMPLETE" not in result["training_admission_reasons"]
    assert manifest["counts"]["denominator"] == 20


def test_lawful_reject_counts_toward_complete_cohort_but_not_opportunity_denominator(isolated_db) -> None:
    hashes = _seed_twenty_sessions(isolated_db, bind_recording=False)
    rejected_hash = hashes[0]
    _seed_recording_cohort(
        isolated_db,
        hashes,
        disposition_by_hash={rejected_hash: "LAWFULLY_REJECTED"},
    )

    result = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )
    coverage = result["manifest"]["recording_coverage"]

    assert coverage["state"] == "COMPLETE"
    assert coverage["counts"]["intended"] == 20
    assert coverage["counts"]["recorded"] == 19
    assert coverage["counts"]["lawfully_rejected"] == 1
    assert result["manifest"]["counts"]["denominator"] == 19
    assert rejected_hash not in {
        item["prediction_hash"]
        for item in result["manifest"]["assignments"]
    }


def test_pending_and_technical_loss_make_recording_coverage_incomplete(isolated_db) -> None:
    hashes = _seed_twenty_sessions(isolated_db, bind_recording=False)
    _seed_recording_cohort(
        isolated_db,
        hashes,
        disposition_by_hash={
            hashes[0]: "PENDING",
            hashes[1]: "TECHNICALLY_LOST",
        },
    )

    result = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )
    reasons = set(result["training_admission_reasons"])
    coverage = result["manifest"]["recording_coverage"]

    assert coverage["state"] == "INCOMPLETE"
    assert coverage["counts"]["pending"] == 1
    assert coverage["counts"]["technically_lost"] == 1
    assert "PENDING_RECORDING_INTENT" in reasons
    assert "TECHNICALLY_LOST_INTENDED_SAMPLE" in reasons
    assert "RECORDING_COVERAGE_INCOMPLETE" in reasons


def test_boundary_purge_and_late_correction_use_only_knowable_outcome(isolated_db) -> None:
    hashes = _seed_twenty_sessions(isolated_db)
    target = hashes[1]
    early_hash = f"{2 + 50_000:064x}"
    late_hash = _seed_outcome(
        isolated_db,
        prediction_hash=target,
        decision_session=date(2026, 1, 2),
        exit_session=date(2026, 1, 5),
        available_at=datetime(2026, 1, 18, 12, 0, 0),
        suffix=900,
        supersedes=early_hash,
    )

    manifest = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )["manifest"]
    target_assignment = next(item for item in manifest["assignments"] if item["prediction_hash"] == target)
    assert target_assignment["outcome_hash"] == early_hash
    assert target_assignment["outcome_hash"] != late_hash
    purged = [item for item in manifest["assignments"] if item["status"] == "PURGED"]
    purge_reasons = {item["purge_or_exclusion_reason"] for item in purged}
    assert "LABEL_INTERVAL_CROSSES_BOUNDARY" in purge_reasons
    assert "OUTCOME_NOT_KNOWABLE_AT_CUTOFF" in purge_reasons


def test_training_admission_stays_blocked_for_local_unapproved_foundation(isolated_db) -> None:
    _seed_twenty_sessions(isolated_db)
    result = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        cost_identity_approved=False,
        durable_references_admitted=False,
        execution_realism_approved=False,
        intended_cohort_id=COHORT_ID,
    )
    assert result["training_admission"] == "BLOCKED"
    assert "LOCAL_DB_ONLY_REFERENCES" in result["training_admission_reasons"]
    assert "DURABLE_REFERENCES_NOT_ADMITTED" in result["training_admission_reasons"]
    assert "COST_IDENTITY_NOT_APPROVED" in result["training_admission_reasons"]
    assert "EXECUTION_REALISM_NOT_APPROVED" in result["training_admission_reasons"]

    realism_approved = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        execution_realism_approved=True,
        intended_cohort_id=COHORT_ID,
    )
    assert "EXECUTION_REALISM_NOT_APPROVED" not in realism_approved["training_admission_reasons"]
    assert realism_approved["training_admission"] == "BLOCKED"


def test_asset_level_auto_screen_does_not_require_universe_snapshot(isolated_db) -> None:
    _seed_twenty_sessions(isolated_db, auto_screen=True)
    result = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )
    reasons = result["training_admission_reasons"]
    assert "UNIVERSE_SNAPSHOT_NOT_BOUND" not in reasons
    assert result["manifest"]["counts"]["pit_eligible"] == 20


def test_pit_ineligible_prediction_is_retained_as_gap_and_blocks_admission(isolated_db) -> None:
    _seed_twenty_sessions(isolated_db)
    gap_day = date(2026, 2, 1)
    gap_hash = _seed_prediction(
        isolated_db,
        index=999,
        session_date=gap_day,
        pit_eligible=False,
    )
    _seed_recording_cohort(isolated_db, [gap_hash])
    result = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )
    assert "PIT_GAPS_PRESENT" in result["training_admission_reasons"]
    gap_assignment = next(
        item for item in result["manifest"]["assignments"] if item["prediction_hash"] == gap_hash
    )
    assert gap_assignment["status"] == "EXCLUDED"
    assert "LEDGER_PIT_INELIGIBLE" in gap_assignment["purge_or_exclusion_reason"]


def test_legacy_v4_runtime_pit_true_is_outside_v3_denominator(isolated_db) -> None:
    _seed_twenty_sessions(isolated_db)
    legacy_hash = _seed_prediction(
        isolated_db,
        index=998,
        session_date=date(2026, 2, 2),
        pit_eligible=True,
    )
    _seed_recording_cohort(isolated_db, [legacy_hash])
    with isolated_db.session_scope() as session:
        row = session.query(PredictionLedgerRecord).filter_by(
            prediction_hash=legacy_hash
        ).one()
        row.schema_version = "prediction-ledger-v4"
        row.opportunity_projection_version = "canonical-opportunity-v1"
        row.strategy_eligibility_version = None
        row.strategy_eligibility_state = None
        row.strategy_eligibility_hash = None
        row.strategy_eligibility_json = None
        row.pit_eligible = True
        row.pit_ineligibility_json = "[]"

    result = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )
    assert result["training_admission"] == "BLOCKED"
    assert result["manifest"]["counts"]["denominator"] == 20
    assert legacy_hash not in {
        item["prediction_hash"]
        for item in result["manifest"]["assignments"]
    }


def test_missing_strategy_eligibility_is_outside_v3_denominator(isolated_db) -> None:
    _seed_twenty_sessions(isolated_db)
    missing_hash = _seed_prediction(
        isolated_db,
        index=996,
        session_date=date(2026, 2, 4),
        include_strategy_eligibility=False,
    )
    _seed_recording_cohort(isolated_db, [missing_hash])

    result = PITDatasetService(db_manager=isolated_db).build_manifest(
        cost_identity_hash=COST_HASH,
        intended_cohort_id=COHORT_ID,
    )

    assert result["manifest"]["counts"]["denominator"] == 20
    assert missing_hash not in {
        item["prediction_hash"]
        for item in result["manifest"]["assignments"]
    }


def test_calendar_unproven_clock_is_a_pit_gap(isolated_db, monkeypatch) -> None:
    prediction_hash = _seed_prediction(
        isolated_db,
        index=997,
        session_date=date(2026, 2, 3),
    )
    monkeypatch.setattr(
        "src.services.pit_dataset_service.resolve_historical_daily_bar_date",
        lambda market, target_date, phase: None,
    )
    with isolated_db.get_session() as session:
        row = session.query(PredictionLedgerRecord).filter_by(
            prediction_hash=prediction_hash
        ).one()
        reasons = PITDatasetService._pit_gap_reasons(row)
    assert "POSTMARKET_SESSION_NOT_CALENDAR_PROVEN" in reasons
