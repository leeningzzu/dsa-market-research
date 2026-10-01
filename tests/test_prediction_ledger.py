# -*- coding: utf-8 -*-
"""Deterministic tests for the append-only Prediction Ledger V1 foundation."""

from __future__ import annotations

import json
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, inspect

from src.config import Config
from src.core.pipeline import StockAnalysisPipeline
from src.repositories.prediction_ledger_repo import PredictionLedgerRepository
from src.services.prediction_ledger_service import (
    PREDICTION_FEATURE_SCHEMA_HASH,
    PREDICTION_FEATURE_SCHEMA_VERSION,
    PREDICTION_LEDGER_SCHEMA_VERSION,
    PredictionLedgerService,
)
from src.services.pit_identity import build_specified_codes_selection_context
from src.services.research_state_projection import (
    CANONICAL_OPPORTUNITY_PROJECTION_VERSION,
    STOCK_TREND_QUALITY_PULLBACK_CONTRACT_COVERAGE,
    STRATEGY_CONTRACT_COVERAGE_HASH,
    STRATEGY_CONTRACT_COVERAGE_VERSION,
    STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE,
    STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
    build_strategy_eligibility_identity,
    build_canonical_opportunity_projection,
    is_white_box_opportunity_record,
)
from src.storage import AnalysisHistory, Base, DatabaseManager, PredictionLedgerRecord


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "prediction_ledger.db")
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


def _add_history(db: DatabaseManager, code: str = "600519") -> int:
    with db.session_scope() as session:
        row = AnalysisHistory(query_id="ledger-query", code=code, report_type="simple")
        session.add(row)
        session.flush()
        return int(row.id)


def _strategy_eligibility(
    *,
    state: str = "ELIGIBLE",
    overrides: dict[str, str] | None = None,
) -> dict:
    required = {
        key: "SATISFIED"
        for key in STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE
    }
    required.update(overrides or {})
    return {
        "schema_version": STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
        "strategy_id": "stock_trend_quality_pullback_v1",
        "state": state,
        "required_evidence": required,
        "reason_codes": [],
    }


def _result(*, score: int = 67, strategy_eligibility: dict | None = None):
    factor_decision = {
        "strategy_id": "stock_trend_quality_pullback_v1",
        "contract_version": "1.0",
        "composite_score": score,
        "canonical_decision": {
            "authority": "stock_trend_quality_pullback_v1",
            "action": "WAIT",
            "public_action": "watch",
            "evidence_state": "PROVEN",
            "hard_veto": False,
            "reason_codes": ["CONDITIONAL_OBSERVATION_ONLY"],
        },
        "market_sector_regime": {"status": "READY", "schema_version": "market-sector-regime-v1"},
        "trend_relative_strength": {"status": "READY", "schema_version": "trend-relative-strength-v1"},
        "supply_demand_volume_price": {"status": "READY", "schema_version": "supply-demand-volume-price-v1"},
        "cost_structure_evidence": {"status": "READY", "schema_version": "cost-structure-v1"},
        "price_structure_evidence": {"status": "READY", "schema_version": "price-structure-v1"},
        "volatility_momentum_evidence": {"status": "READY", "schema_version": "volatility-momentum-v1"},
        "pattern_trigger_evidence": {"status": "FORMING", "schema_version": "pattern-trigger-v1"},
        "multi_timeframe_structure_context": {
            "schema_version": "multi-timeframe-structure-v1",
            "cross_run_persistence_eligible": False,
            "cross_run_persistence_reason": "ADJUSTMENT_BASIS_NOT_PERSISTED",
        },
        "investor_brief": {"human_only": "must not enter feature payload"},
    }
    if strategy_eligibility is not None:
        factor_decision["strategy_eligibility"] = strategy_eligibility
    return SimpleNamespace(
        code="600519",
        dashboard={"factor_decision": factor_decision},
    )


def _signal() -> dict:
    return {
        "id": 17,
        "stock_code": "600519",
        "market": "cn",
        "source_type": "analysis",
        "trace_id": "trace-ledger-1",
        "decision_profile": "balanced",
        "trigger_source": "system",
        "action": "watch",
        "horizon": "3d",
        "entry_low": 1600.0,
        "entry_high": 1650.0,
        "stop_loss": 1550.0,
        "target_price": 1750.0,
        "metadata": {
            "market_phase_summary": {
                "phase": "premarket",
                "session_date": "2026-09-17",
                "effective_daily_bar_date": "2026-09-16",
            }
        },
    }


def test_durable_projection_rejects_forged_canonical_action() -> None:
    with pytest.raises(ValueError, match="canonical"):
        build_canonical_opportunity_projection(
            {
                "authority": "stock_trend_quality_pullback_v1",
                "action": "BUY",
                "public_action": "watch",
                "evidence_state": "PROVEN",
                "hard_veto": False,
                "reason_codes": ["FORGED"],
            },
            strategy_id="stock_trend_quality_pullback_v1",
        )


def test_schema_is_append_only_identity_surface(isolated_db) -> None:
    inspector = inspect(isolated_db._engine)
    unique_constraints = inspector.get_unique_constraints("prediction_ledger")
    indexes = {item["name"] for item in inspector.get_indexes("prediction_ledger")}

    assert any(
        item["name"] == "uix_prediction_ledger_hash"
        and item["column_names"] == ["prediction_hash"]
        for item in unique_constraints
    )
    assert "ix_prediction_ledger_stock_time" in indexes
    assert "ix_prediction_ledger_strategy_time" in indexes
    assert "ix_prediction_ledger_pit_time" in indexes
    assert "ix_prediction_ledger_strategy_eligibility_version" in indexes
    assert "ix_prediction_ledger_strategy_eligibility_state" in indexes
    assert "ix_prediction_ledger_strategy_eligibility_hash" in indexes


def test_service_freezes_factor_payload_idempotently_and_excludes_human_brief(isolated_db) -> None:
    history_id = _add_history(isolated_db)
    service = PredictionLedgerService(db_manager=isolated_db)
    code_sha = "1" * 40

    first = service.persist(
        analysis_history_id=history_id,
        result=_result(),
        decision_signal=_signal(),
        code_sha=code_sha,
    )
    repeated = service.persist(
        analysis_history_id=history_id,
        result=_result(),
        decision_signal=_signal(),
        code_sha=code_sha,
    )

    assert first is not None and first["created"] is True
    assert repeated is not None and repeated["created"] is False
    assert first["status"] == repeated["status"] == "RECORDED"
    assert first["reason_code"] is None
    assert repeated["id"] == first["id"]
    rows = PredictionLedgerRepository(isolated_db).list_for_history(history_id)
    assert len(rows) == 1
    row = rows[0]
    payload = json.loads(row.evidence_json)
    assert "investor_brief" not in payload
    assert payload["composite_score"] == 67
    assert row.schema_version == PREDICTION_LEDGER_SCHEMA_VERSION
    assert row.feature_schema_version == PREDICTION_FEATURE_SCHEMA_VERSION
    assert row.feature_schema_hash == PREDICTION_FEATURE_SCHEMA_HASH
    assert PREDICTION_FEATURE_SCHEMA_HASH == "426d7f21de79ad26e12ea39b4c686b489b90dc24d88f0750bcb5aec421265647"
    assert "strategy_eligibility" not in payload
    assert row.strategy_eligibility_version == STRATEGY_ELIGIBILITY_SCHEMA_VERSION
    assert row.strategy_eligibility_state == "UNKNOWN"
    eligibility = json.loads(row.strategy_eligibility_json)
    assert "STRATEGY_ELIGIBILITY_NOT_BOUND" in eligibility["reason_codes"]
    assert row.strategy_eligibility_hash == build_strategy_eligibility_identity(
        eligibility,
        strategy_id=row.strategy_id,
    )["strategy_eligibility_hash"]
    assert row.code_sha == code_sha
    assert row.canonical_action == "WAIT"
    assert row.opportunity_projection_version == CANONICAL_OPPORTUNITY_PROJECTION_VERSION
    assert row.canonical_evidence_state == "PROVEN"
    assert row.canonical_hard_veto is False
    assert row.decision_signal_id == 17
    assert row.decision_phase == "premarket"
    assert row.session_date.isoformat() == "2026-09-17"
    assert row.effective_daily_bar_date.isoformat() == "2026-09-16"
    assert row.data_as_of.isoformat() == "2026-09-16"
    assert row.outcome_label_anchor is None


def test_strategy_eligibility_fail_closed_and_binds_prediction_identity(isolated_db) -> None:
    history_id = _add_history(isolated_db)
    service = PredictionLedgerService(db_manager=isolated_db)
    signal = _signal()
    code_sha = "8" * 40

    missing = service.persist(
        analysis_history_id=history_id,
        result=_result(),
        decision_signal=signal,
        code_sha=code_sha,
    )
    incomplete_identity = _strategy_eligibility()
    incomplete_identity["required_evidence"].pop("valuation")
    incomplete = service.persist(
        analysis_history_id=history_id,
        result=_result(strategy_eligibility=incomplete_identity),
        decision_signal=signal,
        code_sha=code_sha,
    )
    invalid_reason_identity = _strategy_eligibility()
    invalid_reason_identity["reason_codes"] = ["not a canonical reason"]
    invalid_reason = service.persist(
        analysis_history_id=history_id,
        result=_result(strategy_eligibility=invalid_reason_identity),
        decision_signal=signal,
        code_sha=code_sha,
    )
    eligible = service.persist(
        analysis_history_id=history_id,
        result=_result(strategy_eligibility=_strategy_eligibility()),
        decision_signal=signal,
        code_sha=code_sha,
    )

    assert all(
        item is not None
        for item in (missing, incomplete, invalid_reason, eligible)
    )
    assert len({
        missing["prediction_hash"],
        incomplete["prediction_hash"],
        invalid_reason["prediction_hash"],
        eligible["prediction_hash"],
    }) == 4
    assert len({
        missing["evidence_hash"],
        incomplete["evidence_hash"],
        invalid_reason["evidence_hash"],
        eligible["evidence_hash"],
    }) == 1

    rows = {
        row.prediction_hash: row
        for row in PredictionLedgerRepository(isolated_db).list_for_history(history_id)
    }
    missing_row = rows[missing["prediction_hash"]]
    incomplete_row = rows[incomplete["prediction_hash"]]
    invalid_reason_row = rows[invalid_reason["prediction_hash"]]
    eligible_row = rows[eligible["prediction_hash"]]
    assert missing_row.strategy_eligibility_state == "UNKNOWN"
    assert incomplete_row.strategy_eligibility_state == "UNKNOWN"
    assert invalid_reason_row.strategy_eligibility_state == "UNKNOWN"
    assert eligible_row.strategy_eligibility_state == "ELIGIBLE"
    assert "REQUIRED_EVIDENCE_MATRIX_INCOMPLETE" in json.loads(
        incomplete_row.strategy_eligibility_json
    )["reason_codes"]
    assert "STRATEGY_ELIGIBILITY_REASON_CODES_INVALID" in json.loads(
        invalid_reason_row.strategy_eligibility_json
    )["reason_codes"]
    assert is_white_box_opportunity_record(missing_row) is False
    assert is_white_box_opportunity_record(incomplete_row) is False
    assert is_white_box_opportunity_record(invalid_reason_row) is False
    assert is_white_box_opportunity_record(eligible_row) is True


def test_exact_strategy_coverage_map_is_closed_world_and_role_aware() -> None:
    coverage = {
        clause: (classification, evidence_key)
        for clause, classification, evidence_key
        in STOCK_TREND_QUALITY_PULLBACK_CONTRACT_COVERAGE
    }
    assert coverage["leader_preference"] == ("SELECTION_PRIOR", None)
    assert coverage["monthly_trend_structure_when_ready"] == (
        "CONTEXT_WHEN_READY",
        None,
    )
    assert coverage["sector_industry_strength"] == (
        "HARD_ELIGIBILITY",
        "sector_industry_strength",
    )
    assert coverage["volume_price_confirmation"] == (
        "HARD_ELIGIBILITY",
        "volume_price_confirmation",
    )
    assert set(STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE) == {
        "market_regime_permission",
        "sector_industry_strength",
        "quality",
        "valuation",
        "weekly_trend_structure",
        "daily_trend_structure",
        "daily_pullback_or_supply_contraction",
        "volume_price_confirmation",
        "distribution_risk_clear",
        "thirty_minute_trigger",
        "risk_reward",
    }
    assert "leader_preference" not in STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE
    assert (
        "monthly_trend_structure_when_ready"
        not in STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE
    )

    eligible = build_strategy_eligibility_identity(
        _strategy_eligibility(),
        strategy_id="stock_trend_quality_pullback_v1",
    )
    document = json.loads(eligible["strategy_eligibility_json"])
    assert eligible["strategy_eligibility_state"] == "ELIGIBLE"
    assert document["contract_coverage_version"] == STRATEGY_CONTRACT_COVERAGE_VERSION
    assert document["contract_coverage_hash"] == STRATEGY_CONTRACT_COVERAGE_HASH
    assert document["contract_coverage"] == [
        {
            "clause": clause,
            "classification": classification,
            "evidence_key": evidence_key,
        }
        for clause, classification, evidence_key
        in STOCK_TREND_QUALITY_PULLBACK_CONTRACT_COVERAGE
    ]

    for missing_key in (
        "volume_price_confirmation",
        "weekly_trend_structure",
        "daily_trend_structure",
    ):
        incomplete = _strategy_eligibility()
        incomplete["required_evidence"].pop(missing_key)
        identity = build_strategy_eligibility_identity(
            incomplete,
            strategy_id="stock_trend_quality_pullback_v1",
        )
        assert identity["strategy_eligibility_state"] == "UNKNOWN"
        assert "REQUIRED_EVIDENCE_MATRIX_INCOMPLETE" in json.loads(
            identity["strategy_eligibility_json"]
        )["reason_codes"]


def test_changed_evidence_creates_new_snapshot_without_mutating_old_row(isolated_db) -> None:
    history_id = _add_history(isolated_db)
    service = PredictionLedgerService(db_manager=isolated_db)

    first = service.persist(
        analysis_history_id=history_id,
        result=_result(score=67),
        decision_signal=_signal(),
        code_sha="2" * 40,
    )
    second = service.persist(
        analysis_history_id=history_id,
        result=_result(score=72),
        decision_signal=_signal(),
        code_sha="2" * 40,
    )

    assert first is not None and second is not None
    assert first["id"] != second["id"]
    rows = PredictionLedgerRepository(isolated_db).list_for_history(history_id)
    assert [json.loads(row.evidence_json)["composite_score"] for row in rows] == [67, 72]


def test_current_identity_gaps_fail_closed_for_pit_training(isolated_db) -> None:
    history_id = _add_history(isolated_db)
    outcome = PredictionLedgerService(db_manager=isolated_db).persist(
        analysis_history_id=history_id,
        result=_result(),
        decision_signal=_signal(),
        code_sha="3" * 40,
    )

    assert outcome is not None
    assert outcome["pit_eligible"] is False
    assert set(outcome["pit_ineligibility_reasons"]) >= {
        "AVAILABLE_AT_NOT_BOUND",
        "ADJUSTMENT_BASIS_NOT_PERSISTED",
        "DECISION_TIMEZONE_NOT_BOUND",
        "DATA_SNAPSHOT_IDENTITY_NOT_BOUND",
        "SELECTION_SOURCE_NOT_BOUND",
    }
    row = PredictionLedgerRepository(isolated_db).list_for_history(history_id)[0]
    assert row.pit_eligible is False
    assert row.durability_state == "LOCAL_DB_ONLY"


def test_bound_first_slice_identities_can_be_semantically_pit_eligible(isolated_db) -> None:
    history_id = _add_history(isolated_db)
    result = _result()
    result.dashboard["factor_decision"]["multi_timeframe_structure_context"].update(
        {
            "data_snapshot_identity": "a" * 64,
            "provider_identity": "AkshareFetcher",
            "adjustment_basis": "qfq",
            "available_at_max": "2026-09-17T10:00:00+00:00",
        }
    )
    result.diagnostic_context_snapshot = {
        "market_phase_summary": {
            "phase": "postmarket",
            "session_date": "2026-09-17",
            "effective_daily_bar_date": "2026-09-17",
            "market_local_time": "2026-09-17T18:00:00+08:00",
        },
        "research_decision_time_utc": "2026-09-17T10:05:00+00:00",
    }

    selection_context = build_specified_codes_selection_context(
        raw_selection_source="manual",
        query_source="api",
    )
    with patch(
        "src.services.prediction_ledger_service.resolve_historical_daily_bar_date",
        return_value=date(2026, 9, 17),
    ):
        outcome = PredictionLedgerService(db_manager=isolated_db).persist(
            analysis_history_id=history_id,
            result=result,
            decision_signal=_signal(),
            code_sha="5" * 40,
            selection_context=selection_context,
            recording_intent_hash="e" * 64,
            intended_cohort_id=selection_context["selection_context_hash"],
        )

    assert outcome is not None
    assert outcome["status"] == "RECORDED"
    assert outcome["pit_eligible"] is True
    assert outcome["pit_ineligibility_reasons"] == []
    row = PredictionLedgerRepository(isolated_db).list_for_history(history_id)[0]
    assert row.schema_version == "prediction-ledger-v6" == PREDICTION_LEDGER_SCHEMA_VERSION
    assert row.recording_intent_hash == "e" * 64
    assert row.intended_cohort_id == selection_context["selection_context_hash"]
    assert row.strategy_eligibility_state == "UNKNOWN"
    assert row.decision_timezone == "Asia/Shanghai"
    assert row.decision_phase == "postmarket"
    assert row.session_date == date(2026, 9, 17)
    assert row.effective_daily_bar_date == date(2026, 9, 17)
    assert row.outcome_label_anchor == date(2026, 9, 17)
    assert row.data_as_of == date(2026, 9, 17)
    assert outcome["decision_time_utc"] == "2026-09-17T10:05:00Z"
    assert outcome["outcome_label_anchor"] == "2026-09-17"
    assert row.asset_identity_hash
    assert row.data_snapshot_identity == "a" * 64
    assert row.selection_source == "SPECIFIED_CODES"
    assert row.selection_context_hash
    assert row.universe_snapshot_id is None



@pytest.mark.parametrize(
    ("phase", "session_date", "effective_date"),
    (
        ("premarket", "2026-09-18", "2026-09-17"),
        ("intraday", "2026-09-18", "2026-09-17"),
        ("non_trading", "2026-09-20", "2026-09-18"),
        ("non_trading", "2026-10-01", "2026-09-30"),
    ),
    ids=("premarket", "intraday", "weekend", "holiday"),
)
def test_v1_non_postmarket_clock_is_retained_but_pit_ineligible(
    isolated_db,
    phase,
    session_date,
    effective_date,
) -> None:
    history_id = _add_history(isolated_db)
    result = _result()
    result.dashboard["factor_decision"]["multi_timeframe_structure_context"].update(
        {
            "data_snapshot_identity": "a" * 64,
            "provider_identity": "AkshareFetcher",
            "adjustment_basis": "qfq",
            "available_at_max": "2026-09-17T10:00:00+00:00",
        }
    )
    result.diagnostic_context_snapshot = {
        "market_phase_summary": {
            "phase": phase,
            "session_date": session_date,
            "effective_daily_bar_date": effective_date,
            "market_local_time": f"{session_date}T10:00:00+08:00",
        },
        "research_decision_time_utc": "2026-09-17T10:05:00+00:00",
    }
    receipt = PredictionLedgerService(db_manager=isolated_db).persist(
        analysis_history_id=history_id,
        result=result,
        decision_signal=_signal(),
        code_sha="6" * 40,
        selection_context=build_specified_codes_selection_context(
            raw_selection_source="manual",
            query_source="api",
        ),
    )
    assert receipt is not None
    assert receipt["pit_eligible"] is False
    assert "PRIMARY_HORIZON_ROUTE_NOT_POSTMARKET" in receipt["pit_ineligibility_reasons"]
    assert "OUTCOME_LABEL_ANCHOR_NOT_BOUND" in receipt["pit_ineligibility_reasons"]
    row = PredictionLedgerRepository(isolated_db).list_for_history(history_id)[0]
    assert row.decision_phase == phase
    assert row.session_date.isoformat() == session_date
    assert row.effective_daily_bar_date.isoformat() == effective_date
    assert row.data_as_of.isoformat() == effective_date
    assert row.outcome_label_anchor is None


def test_same_snapshot_with_distinct_clock_identity_cannot_collapse(isolated_db) -> None:
    history_id = _add_history(isolated_db)
    service = PredictionLedgerService(db_manager=isolated_db)
    selection = build_specified_codes_selection_context(
        raw_selection_source="manual",
        query_source="api",
    )

    def frozen_result(phase: str, session_date: str, effective_date: str):
        result = _result()
        result.dashboard["factor_decision"]["multi_timeframe_structure_context"].update(
            {
                "data_snapshot_identity": "d" * 64,
                "provider_identity": "AkshareFetcher",
                "adjustment_basis": "qfq",
                "available_at_max": "2026-09-17T09:00:00+00:00",
            }
        )
        result.diagnostic_context_snapshot = {
            "market_phase_summary": {
                "phase": phase,
                "session_date": session_date,
                "effective_daily_bar_date": effective_date,
                "market_local_time": f"{session_date}T09:00:00+08:00",
            },
            "research_decision_time_utc": "2026-09-17T09:05:00+00:00",
        }
        return result

    first = service.persist(
        analysis_history_id=history_id,
        result=frozen_result("premarket", "2026-09-18", "2026-09-17"),
        decision_signal=_signal(),
        code_sha="7" * 40,
        selection_context=selection,
    )
    second = service.persist(
        analysis_history_id=history_id,
        result=frozen_result("non_trading", "2026-09-20", "2026-09-18"),
        decision_signal=_signal(),
        code_sha="7" * 40,
        selection_context=selection,
    )
    assert first is not None and second is not None
    assert first["prediction_hash"] != second["prediction_hash"]
    assert len(PredictionLedgerRepository(isolated_db).list_for_history(history_id)) == 2


def test_v3_sqlite_clock_migration_is_additive_and_does_not_backfill(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-ledger.db'}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE prediction_ledger ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "prediction_hash VARCHAR(64),"
                "schema_version VARCHAR(32),"
                "decision_timezone VARCHAR(64),"
                "data_as_of DATE,"
                "pit_eligible BOOLEAN NOT NULL DEFAULT 0,"
                "pit_ineligibility_json TEXT NOT NULL,"
                "durability_state VARCHAR(32) NOT NULL DEFAULT 'LOCAL_DB_ONLY'"
                ")"
            )
            connection.exec_driver_sql(
                "INSERT INTO prediction_ledger "
                "(prediction_hash,schema_version,decision_timezone,data_as_of,pit_eligible,pit_ineligibility_json,durability_state) "
                "VALUES ('legacy','prediction-ledger-v3','Asia/Shanghai','2026-09-17',1,'[]','LOCAL_DB_ONLY')"
            )
        manager = object.__new__(DatabaseManager)
        manager._engine = engine
        manager._is_sqlite_engine = True
        manager._ensure_prediction_ledger_pit_schema()
        manager._ensure_prediction_ledger_strategy_eligibility_schema()
        migrated_inspector = inspect(engine)
        columns = {item["name"] for item in migrated_inspector.get_columns("prediction_ledger")}
        assert {
            "decision_phase",
            "session_date",
            "effective_daily_bar_date",
            "outcome_label_anchor",
            "strategy_eligibility_version",
            "strategy_eligibility_state",
            "strategy_eligibility_hash",
            "strategy_eligibility_json",
        } <= columns
        expected_indexes = {
            "ix_prediction_ledger_strategy_eligibility_version",
            "ix_prediction_ledger_strategy_eligibility_state",
            "ix_prediction_ledger_strategy_eligibility_hash",
        }
        assert expected_indexes <= {
            item["name"] for item in migrated_inspector.get_indexes("prediction_ledger")
        }
        with engine.connect() as connection:
            migrated = connection.exec_driver_sql(
                "SELECT schema_version,decision_phase,session_date,effective_daily_bar_date,"
                "outcome_label_anchor,strategy_eligibility_version,strategy_eligibility_state,"
                "strategy_eligibility_hash,strategy_eligibility_json "
                "FROM prediction_ledger WHERE prediction_hash='legacy'"
            ).one()
        assert migrated == (
            "prediction-ledger-v3",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )

        fresh_engine = create_engine(f"sqlite:///{tmp_path / 'fresh-ledger.db'}")
        try:
            Base.metadata.create_all(fresh_engine)
            fresh_inspector = inspect(fresh_engine)
            assert expected_indexes <= {
                item["name"]
                for item in fresh_inspector.get_indexes("prediction_ledger")
            }
            assert {
                "strategy_eligibility_version",
                "strategy_eligibility_state",
                "strategy_eligibility_hash",
                "strategy_eligibility_json",
            } <= {
                item["name"]
                for item in fresh_inspector.get_columns("prediction_ledger")
            }
        finally:
            fresh_engine.dispose()
    finally:
        engine.dispose()

def test_history_deletion_keeps_prediction_ledger_snapshot(isolated_db) -> None:
    history_id = _add_history(isolated_db)
    assert PredictionLedgerService(db_manager=isolated_db).persist(
        analysis_history_id=history_id,
        result=_result(),
        decision_signal=_signal(),
        code_sha="4" * 40,
    ) is not None

    assert isolated_db.delete_analysis_history_records([history_id]) == 1
    with isolated_db.get_session() as session:
        rows = session.query(PredictionLedgerRecord).all()
        assert len(rows) == 1
        assert rows[0].analysis_history_id == history_id


def test_missing_canonical_signal_identity_or_history_returns_typed_outcome_without_snapshot(isolated_db) -> None:
    history_id = _add_history(isolated_db)
    service = PredictionLedgerService(db_manager=isolated_db)

    missing_strategy = service.persist(
        analysis_history_id=history_id,
        result=SimpleNamespace(code="600519", dashboard={}),
        decision_signal=_signal(),
    )
    assert missing_strategy == {
        "status": "LAWFULLY_REJECTED",
        "reason_code": "STRATEGY_ID_NOT_BOUND",
    }

    missing_canonical = _result()
    del missing_canonical.dashboard["factor_decision"]["canonical_decision"]
    canonical_outcome = service.persist(
        analysis_history_id=history_id,
        result=missing_canonical,
        decision_signal=_signal(),
    )
    assert canonical_outcome == {
        "status": "LAWFULLY_REJECTED",
        "reason_code": "LEDGER_PRECONDITION_NOT_BOUND",
    }

    signal_without_horizon = _signal()
    signal_without_horizon.pop("horizon")
    signal_outcome = service.persist(
        analysis_history_id=history_id,
        result=_result(),
        decision_signal=signal_without_horizon,
    )
    assert signal_outcome == {
        "status": "LAWFULLY_REJECTED",
        "reason_code": "LEDGER_PRECONDITION_NOT_BOUND",
    }

    missing_history = service.persist(
        analysis_history_id=history_id + 999,
        result=_result(),
        decision_signal=_signal(),
    )
    assert missing_history == {
        "status": "TECHNICALLY_LOST",
        "reason_code": "ANALYSIS_HISTORY_READBACK_MISSING",
    }
    with isolated_db.get_session() as session:
        assert session.query(PredictionLedgerRecord).count() == 0


def test_postcommit_readback_mismatch_is_typed_technical_loss(isolated_db) -> None:
    history_id = _add_history(isolated_db)
    service = PredictionLedgerService(db_manager=isolated_db)
    with patch.object(service.repo, "get_by_prediction_hash", return_value=None):
        outcome = service.persist(
            analysis_history_id=history_id,
            result=_result(),
            decision_signal=_signal(),
            code_sha="9" * 40,
            recording_intent_hash="a" * 64,
            intended_cohort_id="b" * 64,
        )
    assert outcome == {
        "status": "TECHNICALLY_LOST",
        "reason_code": "LEDGER_POSTCOMMIT_READBACK_MISMATCH",
    }


def test_pipeline_helper_uses_same_database_and_fails_open() -> None:
    pipeline = object.__new__(StockAnalysisPipeline)
    pipeline.db = MagicMock()
    service = MagicMock()
    service.persist.side_effect = RuntimeError("ledger unavailable")

    with patch(
        "src.services.prediction_ledger_service.PredictionLedgerService",
        return_value=service,
    ) as service_class:
        outcome = pipeline._persist_prediction_ledger_after_history_save(
            result=_result(),
            analysis_history_id=42,
            decision_signal={"item": _signal()},
        )

    assert outcome is None
    service_class.assert_called_once_with(db_manager=pipeline.db)
    service.persist.assert_called_once()
    assert service.persist.call_args.kwargs["analysis_history_id"] == 42


def test_pipeline_helper_returns_and_attaches_machine_readable_receipt() -> None:
    pipeline = object.__new__(StockAnalysisPipeline)
    pipeline.db = MagicMock()
    pipeline.research_selection_context = {"selection_source": "SPECIFIED_CODES"}
    pipeline.research_code_sha = "a" * 40
    result = _result()
    receipt = {
        "status": "RECORDED",
        "reason_code": None,
        "id": 9,
        "created": True,
        "prediction_hash": "b" * 64,
        "pit_eligible": True,
    }
    service = MagicMock()
    service.persist.return_value = receipt

    with patch(
        "src.services.prediction_ledger_service.PredictionLedgerService",
        return_value=service,
    ):
        outcome = pipeline._persist_prediction_ledger_after_history_save(
            result=result,
            analysis_history_id=43,
            decision_signal={"item": _signal()},
        )

    assert outcome == receipt
    assert result.prediction_ledger_receipt == receipt
    assert service.persist.call_args.kwargs["code_sha"] == "a" * 40
