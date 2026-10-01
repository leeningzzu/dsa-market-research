# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from src.services.research_state_package_chain import (
    COLUMN_CLASSIFICATION,
    DURABILITY_STATE,
    FORBIDDEN,
    MANIFEST_PREFIX,
    MAX_CHAIN_GENERATIONS,
    MAX_MANIFEST_READS_PER_DISCOVERY,
    MAX_OBJECT_CREATES_PER_GENERATION,
    MAX_PACKAGE_BYTES,
    PACKAGE_PREFIX,
    PUBLICATION_BARRIER,
    REQUIRED_WRITER_CONCURRENCY_GROUP,
    SAFE_PROJECTION_MODE,
    SAFE_PROJECTION_MODE_V1,
    FilesystemObjectStore,
    ImportConflictError,
    ManifestChainError,
    PackageTooLarge,
    RightsAdmissionRequired,
    ResearchStateError,
    SimulatedCrashAfterPackage,
    build_checkpoint_package,
    discover_manifest_chain,
    exported_columns,
    manifest_key,
    publish_checkpoint,
    restore_checkpoint,
    sha256_bytes,
    validate_current_schema,
)
from src.storage import Base
from src.services.research_state_projection import (
    CANONICAL_OPPORTUNITY_PROJECTION_VERSION,
    STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE,
    STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
    build_strategy_eligibility_identity,
    is_white_box_opportunity_record,
)


CODE_SHA = "a" * 40
NOW = datetime(2026, 9, 19, 4, 0, tzinfo=timezone.utc)


def _create_db(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


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


def _seed_state(path: Path, *, extra_ledger: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        eligibility = _strategy_eligibility_identity()
        ledger_rows = [
            (
                "1" * 64,
                "prediction-ledger-v5",
                101,
                201,
                "trace-local-only",
                "cn",
                "600519",
                "stock",
                "2026-09-18 10:00:00",
                "Asia/Shanghai",
                "2026-09-18",
                "2026-09-18 09:59:00",
                "stock_trend_quality_pullback_v1",
                "stock_trend_quality_pullback_v1",
                "factor-decision-v1",
                "WAIT",
                "3d",
                "balanced",
                "github_schedule",
                "analysis",
                100.0,
                101.0,
                95.0,
                110.0,
                "stock-factor-evidence-v1",
                "f" * 64,
                "e" * 64,
                json.dumps(
                    {
                        "canonical_decision": {
                            "action": "WAIT",
                            "evidence_state": "PROVEN",
                            "hard_veto": False,
                        },
                        "synthetic_feature": 1.25,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                CANONICAL_OPPORTUNITY_PROJECTION_VERSION,
                "PROVEN",
                0,
                eligibility["strategy_eligibility_version"],
                eligibility["strategy_eligibility_state"],
                eligibility["strategy_eligibility_hash"],
                eligibility["strategy_eligibility_json"],
                CODE_SHA,
                "SyntheticProvider",
                "qfq",
                None,
                "2" * 64,
                json.dumps(
                    {
                        "version": "cn-stock-asset-v1",
                        "market": "cn",
                        "instrument_type": "stock",
                        "symbol": "600519",
                        "exchange": "SH",
                        "calendar": "XSHG",
                        "timezone": "Asia/Shanghai",
                        "currency": "CNY",
                        "identity_hash": "2" * 64,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "3" * 64,
                "SPECIFIED_CODES",
                "4" * 64,
                json.dumps(
                    {
                        "schema_version": "research-selection-context-v1",
                        "selection_source": "SPECIFIED_CODES",
                        "request_origin": "synthetic_test",
                        "selection_context_hash": "4" * 64,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                1,
                "[]",
                "LOCAL_DB_ONLY",
                "2026-09-18 10:01:00",
            )
        ]
        if extra_ledger:
            second = list(ledger_rows[0])
            second[0] = "5" * 64
            second[2] = 102
            second[3] = 202
            second[6] = "000001"
            second[26] = "6" * 64
            second[39] = "7" * 64
            second[40] = json.dumps(
                {
                    "version": "cn-stock-asset-v1",
                    "market": "cn",
                    "instrument_type": "stock",
                    "symbol": "000001",
                    "exchange": "SZ",
                    "calendar": "XSHG",
                    "timezone": "Asia/Shanghai",
                    "currency": "CNY",
                    "identity_hash": "7" * 64,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            second[41] = "8" * 64
            second[43] = "9" * 64
            second[48] = "2026-09-19 10:01:00"
            ledger_rows.append(tuple(second))

        ledger_columns = (
            "prediction_hash",
            "schema_version",
            "analysis_history_id",
            "decision_signal_id",
            "trace_id",
            "market",
            "stock_code",
            "instrument_type",
            "decision_time",
            "decision_timezone",
            "data_as_of",
            "available_at_max",
            "strategy_id",
            "strategy_version",
            "factor_contract_version",
            "canonical_action",
            "horizon",
            "decision_profile",
            "trigger_source",
            "source_type",
            "entry_low",
            "entry_high",
            "stop_loss",
            "target_price",
            "feature_schema_version",
            "feature_schema_hash",
            "evidence_hash",
            "evidence_json",
            "opportunity_projection_version",
            "canonical_evidence_state",
            "canonical_hard_veto",
            "strategy_eligibility_version",
            "strategy_eligibility_state",
            "strategy_eligibility_hash",
            "strategy_eligibility_json",
            "code_sha",
            "provider_identity",
            "adjustment_basis",
            "universe_snapshot_id",
            "asset_identity_hash",
            "asset_identity_json",
            "data_snapshot_identity",
            "selection_source",
            "selection_context_hash",
            "selection_context_json",
            "pit_eligible",
            "pit_ineligibility_json",
            "durability_state",
            "created_at",
        )
        conn.executemany(
            "INSERT INTO prediction_ledger ("
            + ",".join(ledger_columns)
            + ") VALUES ("
            + ",".join("?" for _ in ledger_columns)
            + ")",
            ledger_rows,
        )

        outcomes = [
            (
                "a" * 64,
                "b" * 64,
                "1" * 64,
                "META_TAKE_NET_POSITIVE_NEXT_OPEN_3S_FIXED_CLOSE_V1",
                "XSHG_POSTMARKET_NEXT_OPEN_3_FORWARD_SESSIONS_FIXED_CLOSE_V1",
                "c" * 64,
                '{"schema_version":"cost-identity-v2"}',
                "d" * 64,
                '{"schema_version":"execution-identity-v1"}',
                "prediction-outcome-fixed-horizon-v3",
                None,
                None,
                "2026-09-18",
                "2026-09-19",
                "2026-09-23",
                "FILLED",
                100.0,
                101.0,
                1.0,
                0.8,
                0.5,
                1.5,
                1,
                "TAKE_SUCCESS",
                None,
                "3" * 64,
                "SyntheticProvider",
                "qfq",
                "2026-09-23 08:00:00",
                "2026-09-23 08:00:00",
            ),
            (
                "0" * 64,
                "b" * 64,
                "1" * 64,
                "META_TAKE_NET_POSITIVE_NEXT_OPEN_3S_FIXED_CLOSE_V1",
                "XSHG_POSTMARKET_NEXT_OPEN_3_FORWARD_SESSIONS_FIXED_CLOSE_V1",
                "c" * 64,
                '{"schema_version":"cost-identity-v2"}',
                "d" * 64,
                '{"schema_version":"execution-identity-v1","revision":2}',
                "prediction-outcome-fixed-horizon-v3",
                "a" * 64,
                "synthetic-correction",
                "2026-09-18",
                "2026-09-19",
                "2026-09-23",
                "FILLED",
                100.0,
                99.0,
                -1.0,
                -1.2,
                1.5,
                0.5,
                0,
                "TAKE_FAIL",
                None,
                "3" * 64,
                "SyntheticProvider",
                "qfq",
                "2026-09-23 09:00:00",
                "2026-09-23 09:00:00",
            ),
        ]
        conn.executemany(
            """INSERT INTO prediction_outcomes (
                outcome_hash,root_identity_hash,prediction_hash,label_identity,horizon_identity,
                cost_identity_hash,cost_identity_json,execution_identity_hash,execution_identity_json,
                evaluation_engine_version,supersedes_outcome_hash,correction_reason,decision_session,
                entry_session,exit_session,execution_state,entry_price,exit_price,gross_return_pct,
                net_return_pct,max_adverse_excursion_pct,max_favorable_excursion_pct,label_value,
                label_status,label_reason,data_snapshot_identity,provider_identity,adjustment_basis,
                available_at,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            outcomes,
        )

        manifest_payload = {
            "schema_version": "pit-dataset-manifest-v1",
            "assignments": [{"prediction_hash": "1" * 64, "status": "INCLUDED"}],
        }
        conn.execute(
            """INSERT INTO pit_dataset_manifests (
                dataset_hash,schema_version,dataset_purpose,strategy_id,strategy_version,
                feature_schema_version,feature_schema_hash,label_identity,horizon_identity,
                cost_identity_hash,evaluation_engine_version,split_policy,purge_policy,
                embargo_policy,selection_route_policy,code_sha,final_test_state,
                training_admission,training_admission_reasons_json,manifest_json,frozen_at,
                created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "9" * 64,
                "pit-dataset-manifest-v1",
                "ASSET_LEVEL_META_FILTER_ON_SELECTED_OPPORTUNITIES_V1",
                "stock_trend_quality_pullback_v1",
                "stock_trend_quality_pullback_v1",
                "stock-factor-evidence-v1",
                "f" * 64,
                "META_TAKE_NET_POSITIVE_NEXT_OPEN_3S_FIXED_CLOSE_V1",
                "XSHG_POSTMARKET_NEXT_OPEN_3_FORWARD_SESSIONS_FIXED_CLOSE_V1",
                "c" * 64,
                "prediction-outcome-fixed-horizon-v3",
                "XSHG_SESSION_GROUPED_CHRONO_60_20_20_PURGED_V1",
                "EXACT_LABEL_INTERVAL_AND_AVAILABLE_AT",
                "FORWARD_ONLY_ZERO_POST_BLOCK_V1",
                "AUTO_SCREEN_OR_SPECIFIED_CODES_V1",
                CODE_SHA,
                "SEALED",
                "BLOCKED",
                '["DURABLE_REFERENCES_NOT_ADMITTED"]',
                json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")),
                "2026-09-23 09:00:00",
                "2026-09-23 09:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _bind_current_recording_state(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE prediction_ledger SET schema_version=?, recording_intent_hash=?, "
            "intended_cohort_id=? WHERE prediction_hash=?",
            ("prediction-ledger-v6", "b" * 64, "c" * 64, "1" * 64),
        )
        conn.execute(
            "INSERT INTO learning_recording_journal ("
            "recording_intent_hash,schema_version,policy_version,analysis_history_id,"
            "intended_cohort_id,stock_code,market,report_type,strategy_id,strategy_version,"
            "canonical_binding_hash,data_snapshot_identity,code_sha,selection_source,"
            "selection_context_hash,disposition,reason_code,prediction_hash,retry_count,"
            "created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "b" * 64,
                "learning-recording-intent-v1",
                "learning-recording-policy-v1",
                101,
                "c" * 64,
                "600519",
                "cn",
                "simple",
                "stock_trend_quality_pullback_v1",
                "stock_trend_quality_pullback_v1",
                "d" * 64,
                "3" * 64,
                CODE_SHA,
                "SPECIFIED_CODES",
                "4" * 64,
                "RECORDED",
                None,
                "1" * 64,
                0,
                "2026-09-18 10:00:00",
                "2026-09-18 10:01:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    path = tmp_path / "source.db"
    _create_db(path)
    _seed_state(path)
    return path


def _read_package(store: FilesystemObjectStore, package_key: str) -> dict:
    return json.loads(store.get_bytes(package_key).decode("utf-8"))


def test_column_classification_covers_current_schema(seeded_db: Path) -> None:
    validate_current_schema(seeded_db)
    conn = sqlite3.connect(seeded_db)
    try:
        for table, classes in COLUMN_CLASSIFICATION.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert actual == set(classes)
            assert all(value in {FORBIDDEN, "SAFE_LOW_SENSITIVITY", "RIGHTS_CONDITIONAL"} for value in classes.values())
    finally:
        conn.close()


def test_nonempty_safe_package_excludes_conditional_fields_and_keeps_opportunity_identity(
    seeded_db: Path,
) -> None:
    package = build_checkpoint_package(seeded_db)
    document = json.loads(package.payload.decode("utf-8"))

    assert document["projection_mode"] == SAFE_PROJECTION_MODE
    for table, classes in COLUMN_CLASSIFICATION.items():
        conditional = {
            name for name, classification in classes.items()
            if classification == "RIGHTS_CONDITIONAL"
        }
        assert conditional.isdisjoint(document["tables"][table]["columns"])

    ledger = document["tables"]["prediction_ledger"]["rows"][0]
    assert ledger["opportunity_projection_version"] == CANONICAL_OPPORTUNITY_PROJECTION_VERSION
    assert ledger["canonical_action"] == "WAIT"
    assert ledger["canonical_evidence_state"] == "PROVEN"
    assert ledger["canonical_hard_veto"] == 0
    assert ledger["schema_version"] == "prediction-ledger-v5"
    assert ledger["strategy_eligibility_version"] == STRATEGY_ELIGIBILITY_SCHEMA_VERSION
    assert ledger["strategy_eligibility_state"] == "ELIGIBLE"
    assert len(ledger["strategy_eligibility_hash"]) == 64
    assert json.loads(ledger["strategy_eligibility_json"])["state"] == "ELIGIBLE"
    for clock_field in (
        "decision_phase",
        "session_date",
        "effective_daily_bar_date",
        "outcome_label_anchor",
    ):
        assert clock_field in ledger
        assert ledger[clock_field] is None
    assert "evidence_json" not in ledger
    assert "selection_context_json" not in ledger
    assert "synthetic_feature" not in package.payload.decode("utf-8")


def test_safe_package_fails_closed_on_projection_raw_evidence_mismatch(
    seeded_db: Path,
) -> None:
    conn = sqlite3.connect(seeded_db)
    try:
        conn.execute(
            "UPDATE prediction_ledger SET canonical_evidence_state='UNKNOWN' "
            "WHERE prediction_hash=?",
            ("1" * 64,),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ResearchStateError, match="safe projection mismatch"):
        build_checkpoint_package(seeded_db)


def test_safe_package_fails_closed_on_strategy_eligibility_mismatch(
    seeded_db: Path,
) -> None:
    conn = sqlite3.connect(seeded_db)
    try:
        conn.execute(
            "UPDATE prediction_ledger SET strategy_eligibility_hash=? "
            "WHERE prediction_hash=?",
            ("0" * 64, "1" * 64),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ResearchStateError, match="strategy eligibility projection mismatch"):
        build_checkpoint_package(seeded_db)


def test_empty_state_is_packageable_without_rights_admission(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    _create_db(db)
    package = build_checkpoint_package(db)
    assert package.table_counts == {
        "learning_recording_journal": 0,
        "prediction_ledger": 0,
        "prediction_outcomes": 0,
        "pit_dataset_manifests": 0,
    }


def test_safe_nonempty_roundtrip_restores_pit_identity_without_raw_evidence(
    seeded_db: Path,
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(tmp_path / "safe-objects")
    receipt = publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=NOW,
        rights_admitted=False,
        rights_classification="NO_CONDITIONAL_VALUES",
    )
    target = tmp_path / "safe-target.db"
    _create_db(target)

    restored = restore_checkpoint(store, target)
    assert restored.inserted == {
        "learning_recording_journal": 0,
        "prediction_ledger": 1,
        "prediction_outcomes": 2,
        "pit_dataset_manifests": 1,
    }

    conn = sqlite3.connect(target)
    try:
        row = conn.execute(
            "SELECT schema_version,strategy_id,evidence_json,selection_context_json,"
            "opportunity_projection_version,canonical_action,canonical_evidence_state,"
            "canonical_hard_veto,strategy_eligibility_version,strategy_eligibility_state,"
            "strategy_eligibility_hash,strategy_eligibility_json,durability_state "
            "FROM prediction_ledger WHERE prediction_hash=?",
            ("1" * 64,),
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == "prediction-ledger-v5"
    assert row[1] == "stock_trend_quality_pullback_v1"
    assert row[2] is None
    assert row[3] is None
    assert row[4:10] == (
        CANONICAL_OPPORTUNITY_PROJECTION_VERSION,
        "WAIT",
        "PROVEN",
        0,
        STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
        "ELIGIBLE",
    )
    assert len(row[10]) == 64
    assert json.loads(row[11])["state"] == "ELIGIBLE"
    assert row[12] == DURABILITY_STATE
    assert is_white_box_opportunity_record(
        SimpleNamespace(
            schema_version=row[0],
            strategy_id=row[1],
            opportunity_projection_version=row[4],
            canonical_action=row[5],
            canonical_evidence_state=row[6],
            canonical_hard_veto=bool(row[7]),
            strategy_eligibility_version=row[8],
            strategy_eligibility_state=row[9],
            strategy_eligibility_hash=row[10],
            strategy_eligibility_json=row[11],
        )
    ) is True

    rebuilt = build_checkpoint_package(target)
    assert rebuilt.payload == store.get_bytes(receipt.package_key)


def test_current_v2_roundtrip_preserves_recording_and_cohort_identity(
    seeded_db: Path,
    tmp_path: Path,
) -> None:
    _bind_current_recording_state(seeded_db)
    package = build_checkpoint_package(seeded_db)
    document = json.loads(package.payload.decode("utf-8"))
    assert document["schema_version"] == "research-state-package-v2"
    assert document["tables"]["learning_recording_journal"]["row_count"] == 1

    journal_row = document["tables"]["learning_recording_journal"]["rows"][0]
    ledger_row = document["tables"]["prediction_ledger"]["rows"][0]
    assert journal_row["recording_intent_hash"] == ledger_row["recording_intent_hash"] == "b" * 64
    assert journal_row["intended_cohort_id"] == ledger_row["intended_cohort_id"] == "c" * 64
    assert journal_row["disposition"] == "RECORDED"
    assert journal_row["prediction_hash"] == ledger_row["prediction_hash"] == "1" * 64
    assert ledger_row["schema_version"] == "prediction-ledger-v6"

    store = FilesystemObjectStore(tmp_path / "recording-v2-objects")
    publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=NOW,
        rights_admitted=False,
        rights_classification="NO_CONDITIONAL_VALUES",
    )
    target = tmp_path / "recording-v2-target.db"
    _create_db(target)
    restored = restore_checkpoint(store, target)
    assert restored.inserted["learning_recording_journal"] == 1
    assert restored.inserted["prediction_ledger"] == 1

    conn = sqlite3.connect(target)
    try:
        restored_journal = conn.execute(
            "SELECT recording_intent_hash,intended_cohort_id,disposition,prediction_hash,"
            "analysis_history_id FROM learning_recording_journal"
        ).fetchone()
        restored_ledger = conn.execute(
            "SELECT schema_version,recording_intent_hash,intended_cohort_id "
            "FROM prediction_ledger WHERE prediction_hash=?",
            ("1" * 64,),
        ).fetchone()
    finally:
        conn.close()

    assert restored_journal == (
        "b" * 64,
        "c" * 64,
        "RECORDED",
        "1" * 64,
        0,
    )
    assert restored_ledger == ("prediction-ledger-v6", "b" * 64, "c" * 64)


def test_roundtrip_is_deterministic_and_duplicate_restore_is_idempotent(
    seeded_db: Path,
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    first_package = build_checkpoint_package(seeded_db, rights_admitted=True)
    second_package = build_checkpoint_package(seeded_db, rights_admitted=True)
    assert first_package.payload == second_package.payload
    assert first_package.sha256 == second_package.sha256

    published = publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=NOW,
        rights_admitted=True,
        rights_classification="SYNTHETIC_TEST_ONLY",
    )
    target = tmp_path / "target.db"
    _create_db(target)

    restored = restore_checkpoint(store, target)
    assert restored.inserted == {
        "learning_recording_journal": 0,
        "prediction_ledger": 1,
        "prediction_outcomes": 2,
        "pit_dataset_manifests": 1,
    }
    again = restore_checkpoint(store, target)
    assert again.existing == {
        "learning_recording_journal": 0,
        "prediction_ledger": 1,
        "prediction_outcomes": 2,
        "pit_dataset_manifests": 1,
    }

    roundtrip = build_checkpoint_package(target, rights_admitted=True)
    assert roundtrip.payload == first_package.payload
    assert roundtrip.sha256 == published.package_sha256

    conn = sqlite3.connect(target)
    try:
        row = conn.execute(
            "SELECT analysis_history_id,decision_signal_id,durability_state "
            "FROM prediction_ledger WHERE prediction_hash=?",
            ("1" * 64,),
        ).fetchone()
        assert row == (0, None, DURABILITY_STATE)
        correction = conn.execute(
            "SELECT supersedes_outcome_hash,correction_reason "
            "FROM prediction_outcomes WHERE outcome_hash=?",
            ("0" * 64,),
        ).fetchone()
        assert correction == ("a" * 64, "synthetic-correction")
        pit = conn.execute(
            "SELECT manifest_json FROM pit_dataset_manifests WHERE dataset_hash=?",
            ("9" * 64,),
        ).fetchone()
        assert json.loads(pit[0])["assignments"][0]["prediction_hash"] == "1" * 64
    finally:
        conn.close()



def test_v4_clock_identity_survives_package_restore(seeded_db: Path, tmp_path: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    try:
        conn.execute(
            "UPDATE prediction_ledger SET "
            "schema_version='prediction-ledger-v4',"
            "opportunity_projection_version='canonical-opportunity-v1',"
            "strategy_eligibility_version=NULL,"
            "strategy_eligibility_state=NULL,"
            "strategy_eligibility_hash=NULL,"
            "strategy_eligibility_json=NULL,"
            "decision_phase='postmarket',"
            "session_date='2026-09-18',"
            "effective_daily_bar_date='2026-09-18',"
            "outcome_label_anchor='2026-09-18',"
            "data_as_of='2026-09-18' "
            "WHERE prediction_hash=?",
            ("1" * 64,),
        )
        conn.commit()
    finally:
        conn.close()

    store = FilesystemObjectStore(tmp_path / "clock-objects")
    publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=NOW,
        rights_admitted=False,
        rights_classification="NO_CONDITIONAL_VALUES",
    )
    target = tmp_path / "clock-target.db"
    _create_db(target)
    restore_checkpoint(store, target)

    conn = sqlite3.connect(target)
    try:
        restored = conn.execute(
            "SELECT schema_version,decision_phase,session_date,effective_daily_bar_date,"
            "outcome_label_anchor,data_as_of,strategy_eligibility_version,"
            "strategy_eligibility_state,strategy_eligibility_hash,strategy_eligibility_json "
            "FROM prediction_ledger WHERE prediction_hash=?",
            ("1" * 64,),
        ).fetchone()
    finally:
        conn.close()
    assert restored == (
        "prediction-ledger-v4",
        "postmarket",
        "2026-09-18",
        "2026-09-18",
        "2026-09-18",
        "2026-09-18",
        None,
        None,
        None,
        None,
    )
    assert is_white_box_opportunity_record(
        SimpleNamespace(
            schema_version=restored[0],
            opportunity_projection_version="canonical-opportunity-v1",
            canonical_action="WAIT",
            canonical_evidence_state="PROVEN",
            canonical_hard_veto=False,
            strategy_eligibility_version=restored[6],
            strategy_eligibility_state=restored[7],
            strategy_eligibility_hash=restored[8],
            strategy_eligibility_json=restored[9],
        )
    ) is False

def test_legacy_full_package_without_projection_mode_remains_readable(
    seeded_db: Path,
) -> None:
    package = build_checkpoint_package(seeded_db, rights_admitted=True)
    document = json.loads(package.payload.decode("utf-8"))
    document.pop("projection_mode")
    ledger = document["tables"]["prediction_ledger"]
    new_projection_columns = {
        "opportunity_projection_version",
        "canonical_evidence_state",
        "canonical_hard_veto",
        "decision_phase",
        "session_date",
        "effective_daily_bar_date",
        "outcome_label_anchor",
        "strategy_eligibility_version",
        "strategy_eligibility_state",
        "strategy_eligibility_hash",
        "strategy_eligibility_json",
    }
    ledger["columns"] = [
        column for column in ledger["columns"]
        if column not in new_projection_columns
    ]
    for row in ledger["rows"]:
        for column in new_projection_columns:
            row.pop(column, None)
    _rehash_table(document, "prediction_ledger")
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    from src.services.research_state_package_chain import _validate_package_document

    validated = _validate_package_document(payload)
    assert validated["tables"]["prediction_ledger"]["row_count"] == 1


def test_safe_v1_package_without_strategy_eligibility_remains_readable(
    seeded_db: Path,
) -> None:
    package = build_checkpoint_package(seeded_db)
    document = json.loads(package.payload.decode("utf-8"))
    document["projection_mode"] = SAFE_PROJECTION_MODE_V1
    ledger = document["tables"]["prediction_ledger"]
    eligibility_columns = {
        "strategy_eligibility_version",
        "strategy_eligibility_state",
        "strategy_eligibility_hash",
        "strategy_eligibility_json",
    }
    ledger["columns"] = [
        column for column in ledger["columns"]
        if column not in eligibility_columns
    ]
    for row in ledger["rows"]:
        row["schema_version"] = "prediction-ledger-v4"
        row["opportunity_projection_version"] = "canonical-opportunity-v1"
        for column in eligibility_columns:
            row.pop(column, None)
    _rehash_table(document, "prediction_ledger")
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    from src.services.research_state_package_chain import _validate_package_document

    validated = _validate_package_document(payload)
    assert validated["projection_mode"] == SAFE_PROJECTION_MODE_V1
    assert eligibility_columns.isdisjoint(
        validated["tables"]["prediction_ledger"]["columns"]
    )


def test_forbidden_columns_never_enter_package(seeded_db: Path) -> None:
    package = build_checkpoint_package(seeded_db, rights_admitted=True)
    doc = json.loads(package.payload.decode("utf-8"))
    ledger = doc["tables"]["prediction_ledger"]
    forbidden = {name for name, cls in COLUMN_CLASSIFICATION["prediction_ledger"].items() if cls == FORBIDDEN}
    assert forbidden.isdisjoint(ledger["columns"])
    assert forbidden.isdisjoint(ledger["rows"][0])
    assert {"analysis_history_id", "decision_signal_id", "entry_low", "target_price", "durability_state"} <= forbidden


def test_same_identity_different_durable_content_stops(seeded_db: Path, tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=NOW,
        rights_admitted=True,
        rights_classification="SYNTHETIC_TEST_ONLY",
    )
    target = tmp_path / "target.db"
    _create_db(target)
    restore_checkpoint(store, target)

    conn = sqlite3.connect(target)
    try:
        conn.execute(
            "UPDATE prediction_outcomes SET label_status='UNLABELABLE' WHERE outcome_hash=?",
            ("a" * 64,),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ImportConflictError):
        restore_checkpoint(store, target)


def test_manifest_package_key_and_rights_classification_fail_closed(
    seeded_db: Path,
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    receipt = publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=NOW,
        rights_admitted=True,
        rights_classification="SYNTHETIC_TEST_ONLY",
    )
    manifest_path = store._path(receipt.manifest_key)
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc["package_key"] = f"{PACKAGE_PREFIX}/{'0' * 64}.json"
    manifest_path.write_text(
        json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(ManifestChainError):
        discover_manifest_chain(store)

    fresh_store = FilesystemObjectStore(tmp_path / "rights")
    with pytest.raises(RightsAdmissionRequired):
        publish_checkpoint(
            fresh_store,
            seeded_db,
            source_code_sha=CODE_SHA,
            created_at=NOW,
            rights_admitted=True,
            rights_classification="NO_CONDITIONAL_VALUES",
        )


def test_cross_table_lineage_and_pit_references_fail_closed(
    seeded_db: Path,
    tmp_path: Path,
) -> None:
    package = build_checkpoint_package(seeded_db, rights_admitted=True)
    doc = json.loads(package.payload.decode("utf-8"))

    bad_outcome = json.loads(json.dumps(doc))
    bad_outcome["tables"]["prediction_outcomes"]["rows"][0]["prediction_hash"] = "f" * 64
    _rehash_table(bad_outcome, "prediction_outcomes")
    bad_bytes = json.dumps(
        bad_outcome, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    store = FilesystemObjectStore(tmp_path / "bad-outcome")
    bad_sha = sha256_bytes(bad_bytes)
    bad_key = f"{PACKAGE_PREFIX}/{bad_sha}.json"
    assert store.put_if_absent(bad_key, bad_bytes)
    with pytest.raises(ManifestChainError):
        from src.services.research_state_package_chain import _validate_package_document
        _validate_package_document(bad_bytes)

    bad_pit = json.loads(json.dumps(doc))
    pit_manifest = json.loads(
        bad_pit["tables"]["pit_dataset_manifests"]["rows"][0]["manifest_json"]
    )
    pit_manifest["assignments"][0]["prediction_hash"] = "f" * 64
    bad_pit["tables"]["pit_dataset_manifests"]["rows"][0]["manifest_json"] = json.dumps(
        pit_manifest, sort_keys=True, separators=(",", ":")
    )
    _rehash_table(bad_pit, "pit_dataset_manifests")
    bad_pit_bytes = json.dumps(
        bad_pit, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with pytest.raises(ManifestChainError):
        from src.services.research_state_package_chain import _validate_package_document
        _validate_package_document(bad_pit_bytes)


def test_package_corruption_stops_restore(seeded_db: Path, tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    receipt = publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=NOW,
        rights_admitted=True,
        rights_classification="SYNTHETIC_TEST_ONLY",
    )
    path = store._path(receipt.package_key)
    path.write_bytes(path.read_bytes() + b"x")
    target = tmp_path / "target.db"
    _create_db(target)
    with pytest.raises(ManifestChainError):
        restore_checkpoint(store, target)


def test_parent_hash_corruption_and_generation_fork_stop(
    seeded_db: Path,
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=NOW,
        rights_admitted=True,
        rights_classification="SYNTHETIC_TEST_ONLY",
    )
    publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc),
        rights_admitted=True,
        rights_classification="SYNTHETIC_TEST_ONLY",
    )
    second = store._path(manifest_key(2))
    doc = json.loads(second.read_text(encoding="utf-8"))
    doc["parent_manifest_sha256"] = "0" * 64
    second.write_text(
        json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(ManifestChainError):
        discover_manifest_chain(store)

    # A second key that tries to encode generation 2 is not part of the exact
    # key contract and therefore fails closed instead of silently forking.
    fork = store._path(f"{MANIFEST_PREFIX}/00000000000000000002-fork.json")
    fork.parent.mkdir(parents=True, exist_ok=True)
    fork.write_text("{}", encoding="utf-8")
    with pytest.raises(ManifestChainError):
        discover_manifest_chain(store)


def test_orphan_and_crash_before_manifest_do_not_advance_chain(
    seeded_db: Path,
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    package = build_checkpoint_package(seeded_db, rights_admitted=True)
    assert store.put_if_absent(package.key, package.payload) is True
    assert discover_manifest_chain(store) == []

    with pytest.raises(SimulatedCrashAfterPackage):
        publish_checkpoint(
            store,
            seeded_db,
            source_code_sha=CODE_SHA,
            created_at=NOW,
            rights_admitted=True,
            rights_classification="SYNTHETIC_TEST_ONLY",
            crash_after_package=True,
        )
    assert discover_manifest_chain(store) == []


def test_explicit_earlier_generation_restore_is_a_real_rollback(
    seeded_db: Path,
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=NOW,
        rights_admitted=True,
        rights_classification="SYNTHETIC_TEST_ONLY",
    )

    # Add one more synthetic ledger row, then publish a second full checkpoint.
    _seed_extra_ledger_only(seeded_db)
    publish_checkpoint(
        store,
        seeded_db,
        source_code_sha=CODE_SHA,
        created_at=datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc),
        rights_admitted=True,
        rights_classification="SYNTHETIC_TEST_ONLY",
    )

    old_target = tmp_path / "old.db"
    new_target = tmp_path / "new.db"
    _create_db(old_target)
    _create_db(new_target)
    restore_checkpoint(store, old_target, generation=1)
    restore_checkpoint(store, new_target)

    assert _count(old_target, "prediction_ledger") == 1
    assert _count(new_target, "prediction_ledger") == 2


def test_package_byte_cap_fails_closed(seeded_db: Path) -> None:
    with pytest.raises(PackageTooLarge):
        build_checkpoint_package(
            seeded_db,
            rights_admitted=True,
            max_package_bytes=128,
        )


def test_chain_hard_cap_and_workflow_contract_are_bounded() -> None:
    assert MAX_CHAIN_GENERATIONS == 1000
    assert MAX_PACKAGE_BYTES == 8 * 1024 * 1024
    assert MAX_OBJECT_CREATES_PER_GENERATION == 2
    assert MAX_MANIFEST_READS_PER_DISCOVERY == 1000
    assert PUBLICATION_BARRIER == "PACKAGE_FIRST_MANIFEST_LAST"
    assert REQUIRED_WRITER_CONCURRENCY_GROUP == "stock-analysis"

    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "00-daily-analysis.yml").read_text(
        encoding="utf-8"
    )
    assert "group: stock-analysis" in workflow
    assert "cancel-in-progress: false" in workflow


def _rehash_table(document: dict, table: str) -> None:
    rows = document["tables"][table]["rows"]
    row_hashes = [
        sha256_bytes(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        for row in rows
    ]
    document["tables"][table]["table_root_sha256"] = sha256_bytes(
        json.dumps(row_hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _seed_extra_ledger_only(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        source = conn.execute(
            "SELECT * FROM prediction_ledger WHERE prediction_hash=?",
            ("1" * 64,),
        ).fetchone()
        columns = [row[1] for row in conn.execute("PRAGMA table_info(prediction_ledger)")]
        item = dict(zip(columns, source))
        item.pop("id")
        item["prediction_hash"] = "5" * 64
        item["analysis_history_id"] = 102
        item["decision_signal_id"] = 202
        item["stock_code"] = "000001"
        item["evidence_hash"] = "6" * 64
        item["asset_identity_hash"] = "7" * 64
        item["data_snapshot_identity"] = "8" * 64
        item["selection_context_hash"] = "9" * 64
        item["created_at"] = "2026-09-19 10:01:00"
        columns2 = tuple(item)
        conn.execute(
            "INSERT INTO prediction_ledger ("
            + ",".join(columns2)
            + ") VALUES ("
            + ",".join("?" for _ in columns2)
            + ")",
            tuple(item[column] for column in columns2),
        )
        conn.commit()
    finally:
        conn.close()


def _count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()
