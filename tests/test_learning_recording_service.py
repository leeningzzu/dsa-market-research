# -*- coding: utf-8 -*-
"""Deterministic tests for the same-DB learning-recording journal."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from src.config import Config
from src.services.learning_recording_service import (
    LearningRecordingService,
    PENDING,
    RECORDED,
    TECHNICALLY_LOST,
)
from src.services.pit_identity import build_specified_codes_selection_context
from src.storage import DatabaseManager, LearningRecordingRecord


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "learning_recording.db")
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


def _result() -> SimpleNamespace:
    return SimpleNamespace(
        code="600519",
        name="贵州茅台",
        sentiment_score=60,
        operation_advice="观望",
        trend_prediction="震荡",
        analysis_summary="test",
        dashboard={
            "factor_decision": {
                "strategy_id": "stock_trend_quality_pullback_v1",
                "canonical_decision_identity": {
                    "schema_version": "canonical-decision-identity-v1",
                    "canonical_decision_hash": "a" * 64,
                },
                "multi_timeframe_structure_context": {
                    "data_snapshot_identity": "b" * 64,
                },
            }
        },
    )


def _selection_context() -> dict:
    return build_specified_codes_selection_context(
        raw_selection_source="manual",
        query_source="api",
    )


def test_compile_intent_separates_selection_identity_from_run_cohort() -> None:
    result = _result()
    selection = _selection_context()
    first = LearningRecordingService.compile_intent(
        result=result,
        query_id="query-one",
        report_type="simple",
        selection_context=selection,
        code_sha="c" * 40,
    )
    repeated = LearningRecordingService.compile_intent(
        result=result,
        query_id="query-one",
        report_type="simple",
        selection_context=selection,
        code_sha="c" * 40,
    )
    separate_run = LearningRecordingService.compile_intent(
        result=result,
        query_id="query-two",
        report_type="simple",
        selection_context=selection,
        code_sha="c" * 40,
    )

    assert first == repeated
    assert first["recording_intent_hash"]
    assert first["selection_context_hash"] == selection["selection_context_hash"]
    assert separate_run["selection_context_hash"] == selection["selection_context_hash"]
    assert first["intended_cohort_id"] != selection["selection_context_hash"]
    assert separate_run["intended_cohort_id"] != first["intended_cohort_id"]
    assert separate_run["recording_intent_hash"] != first["recording_intent_hash"]
    assert first["data_snapshot_identity"] == "b" * 64
    assert "fallback_query_id" not in first


def test_explicit_frozen_cohort_can_span_multiple_queries() -> None:
    result = _result()
    frozen_cohort = "9" * 64
    first = LearningRecordingService.compile_intent(
        result=result,
        query_id="query-one",
        report_type="simple",
        selection_context=_selection_context(),
        code_sha="c" * 40,
        intended_cohort_id=frozen_cohort,
    )
    second = LearningRecordingService.compile_intent(
        result=result,
        query_id="query-two",
        report_type="simple",
        selection_context=_selection_context(),
        code_sha="c" * 40,
        intended_cohort_id=frozen_cohort,
    )

    assert first["intended_cohort_id"] == frozen_cohort
    assert second["intended_cohort_id"] == frozen_cohort
    assert first["recording_intent_hash"] == second["recording_intent_hash"]


def test_invalid_explicit_cohort_is_rejected() -> None:
    with pytest.raises(ValueError, match="intended_cohort_id"):
        LearningRecordingService.compile_intent(
            result=_result(),
            query_id="query-invalid-cohort",
            report_type="simple",
            selection_context=_selection_context(),
            intended_cohort_id="not-a-hash",
        )


def test_journal_transitions_are_typed_reconcilable_and_terminal(isolated_db) -> None:
    result = _result()
    intent = LearningRecordingService.compile_intent(
        result=result,
        query_id="query-recording",
        report_type="simple",
        selection_context=_selection_context(),
        code_sha="d" * 40,
    )
    history_id = isolated_db.save_analysis_history(
        result=result,
        query_id="query-recording",
        report_type="simple",
        news_content=None,
        save_snapshot=False,
        recording_intent=intent,
    )
    assert history_id > 0

    service = LearningRecordingService(db_manager=isolated_db)
    pending = service.get(intent["recording_intent_hash"])
    assert pending is not None
    assert pending["analysis_history_id"] == history_id
    assert pending["disposition"] == PENDING
    assert pending["retry_count"] == 0

    lost = service.mark_technically_lost(
        intent["recording_intent_hash"],
        reason_code="LEDGER_TIMEOUT",
    )
    assert lost is not None
    assert lost["disposition"] == TECHNICALLY_LOST
    assert lost["reason_code"] == "LEDGER_TIMEOUT"

    recorded = service.mark_recorded(
        intent["recording_intent_hash"],
        prediction_hash="e" * 64,
    )
    assert recorded is not None
    assert recorded["disposition"] == RECORDED
    assert recorded["prediction_hash"] == "e" * 64
    assert recorded["reason_code"] is None
    assert recorded["retry_count"] == 1

    with pytest.raises(ValueError, match="terminal"):
        service.mark_lawfully_rejected(
            intent["recording_intent_hash"],
            reason_code="STRATEGY_ID_NOT_BOUND",
        )

    with isolated_db.get_session() as session:
        rows = session.query(LearningRecordingRecord).all()
        assert len(rows) == 1
        assert rows[0].recording_intent_hash == intent["recording_intent_hash"]


def test_recorded_prediction_hash_cannot_be_rewritten(isolated_db) -> None:
    result = _result()
    intent = LearningRecordingService.compile_intent(
        result=result,
        query_id="query-stable-prediction",
        report_type="simple",
        selection_context=_selection_context(),
        code_sha="f" * 40,
    )
    assert isolated_db.save_analysis_history(
        result=result,
        query_id="query-stable-prediction",
        report_type="simple",
        news_content=None,
        save_snapshot=False,
        recording_intent=intent,
    ) > 0
    service = LearningRecordingService(db_manager=isolated_db)
    service.mark_recorded(intent["recording_intent_hash"], prediction_hash="1" * 64)

    with pytest.raises(ValueError, match="prediction hash cannot change"):
        service.mark_recorded(intent["recording_intent_hash"], prediction_hash="2" * 64)


def test_invalid_reason_code_is_rejected_before_db_mutation(isolated_db) -> None:
    service = LearningRecordingService(db_manager=isolated_db)
    with pytest.raises(ValueError, match="reason_code"):
        service.mark_technically_lost("a" * 64, reason_code="not canonical")
