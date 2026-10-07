# -*- coding: utf-8 -*-
"""Deterministic tests for the local-only minimum evidence-flywheel runtime."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import inspect, select, text

from src.analyzer import GeminiAnalyzer
from src.config import Config
from src.core.pipeline import StockAnalysisPipeline
from src.notification import NotificationService
from src.repositories.prediction_ledger_repo import PredictionLedgerRepository
from src.services.evidence_flywheel_runtime import (
    EvidenceFlywheelBoundaryError,
    EvidenceFlywheelRuntimeError,
    _build_parser,
    _database_file_identity,
    _ledger_identity_snapshot,
    _require_fresh_sqlite_file,
    _write_receipt_file,
    build_pit_manifest_receipt,
    evaluate_prediction_outcome,
    record_canonical_run,
    replay_specified_codes_daily_sessions,
)
from src.services.pit_dataset_service import PITDatasetService
from src.services.evidence_traceability_registry import MANIFEST_HASH, digest
from src.services.prediction_ledger_service import (
    TRACE_FEATURE_SCHEMA_VERSION,
    traced_feature_schema_hash,
)
from src.services.research_state_projection import (
    STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
    build_strategy_eligibility_identity,
)
from src.storage import DatabaseManager, PredictionLedgerRecord, StockDaily


_PRE_INTRADAY_MANIFEST_HASH = "5818db4878f97f34c732a630ff1a5a41287b661ded299a3076473479123cefca"


def test_pipeline_execution_receipts_exist_only_for_actual_returned_producers():
    target = date(2026, 9, 30)
    identity = {
        "data_snapshot_identity": "a" * 64,
        "provider_identity": "synthetic",
        "adjustment_basis": "qfq",
        "price_identity_reasons": [],
        "stock_code": "600519",
        "market": "cn",
        "target_date": target.isoformat(),
    }
    supply = {
        "schema_version": "supply-demand-volume-price-v1",
        "family": "supply_demand_volume_price",
        "status": "MISSING",
        "stock_code": "600519",
        "target_date": target.isoformat(),
    }

    receipts = StockAnalysisPipeline._build_direct_method_execution_receipts(
        code="600519",
        market="cn",
        asset_route="STOCK",
        target_date=target,
        completed_history_identity=identity,
        chip_data=None,
        canonical_trend_result=None,
        supply_demand_context=supply,
        cost_structure_context=None,
        price_structure_context=None,
        volatility_momentum_context=None,
        pattern_trigger_context=None,
        multi_timeframe_structure_context=None,
    )

    assert set(receipts) == {"SUPPLY"}
    assert receipts["SUPPLY"]["output_hash"] == digest(supply)
    assert receipts["SUPPLY"]["input_identity"] == identity


@pytest.fixture()
def isolated_db(tmp_path):
    Config.reset_instance()
    DatabaseManager.reset_instance()
    config = Config(
        database_path=str(tmp_path / "evidence_flywheel.db"),
        sqlite_wal_enabled=False,
    )
    Config._instance = config
    db = DatabaseManager(db_url=config.get_db_url())
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _insert_traced_ledger_row(
    db: DatabaseManager,
    *,
    prediction_hash: str,
    manifest_hash: str,
    trace_manifest_hash: str | None = None,
    feature_schema_hash: str | None = None,
) -> None:
    eligibility = build_strategy_eligibility_identity(
        {
            "schema_version": STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
            "strategy_id": "stock_trend_quality_pullback_v1",
            "state": "UNKNOWN",
            "required_evidence": {},
            "reason_codes": ["STRATEGY_ELIGIBILITY_NOT_BOUND"],
        },
        strategy_id="stock_trend_quality_pullback_v1",
    )
    payload = {
        "schema_version": TRACE_FEATURE_SCHEMA_VERSION,
        "manifest_hash": manifest_hash,
        "trace_identity": {
            "manifest_version": "evidence-product-traceability-v1",
            "manifest_hash": trace_manifest_hash or manifest_hash,
            "runtime_trace_hash": "3" * 64,
            "data_snapshot_identity": "4" * 64,
        },
        "values": {},
        "training_admitted": False,
    }
    with db.session_scope() as session:
        session.add(
            PredictionLedgerRecord(
                prediction_hash=prediction_hash,
                schema_version="prediction-ledger-v6",
                analysis_history_id=int(prediction_hash[-4:], 16) + 1,
                market="cn",
                stock_code="600519",
                instrument_type="stock",
                decision_time=datetime(2025, 9, 30, 10, 0),
                decision_timezone="Asia/Shanghai",
                decision_phase="postmarket",
                session_date=date(2025, 9, 30),
                data_as_of=date(2025, 9, 30),
                strategy_id="stock_trend_quality_pullback_v1",
                strategy_version="stock_trend_quality_pullback_v1",
                canonical_action="WAIT",
                horizon="3d",
                feature_schema_version=TRACE_FEATURE_SCHEMA_VERSION,
                feature_schema_hash=feature_schema_hash or traced_feature_schema_hash(manifest_hash),
                evidence_hash=digest(payload),
                evidence_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                strategy_eligibility_version=eligibility["strategy_eligibility_version"],
                strategy_eligibility_state=eligibility["strategy_eligibility_state"],
                strategy_eligibility_hash=eligibility["strategy_eligibility_hash"],
                strategy_eligibility_json=eligibility["strategy_eligibility_json"],
                pit_eligible=False,
                pit_ineligibility_json='["TEST_ONLY"]',
                durability_state="LOCAL_DB_ONLY",
            )
        )


def test_traced_ledger_readback_preserves_frozen_manifest_identity(isolated_db) -> None:
    _insert_traced_ledger_row(isolated_db, prediction_hash="1" * 64, manifest_hash=_PRE_INTRADAY_MANIFEST_HASH)
    _insert_traced_ledger_row(isolated_db, prediction_hash="2" * 64, manifest_hash=MANIFEST_HASH)

    old_snapshot = _ledger_identity_snapshot(isolated_db, "1" * 64)
    current_snapshot = _ledger_identity_snapshot(isolated_db, "2" * 64)

    assert old_snapshot["evidence_traceability_identity"]["manifest_hash"] == _PRE_INTRADAY_MANIFEST_HASH
    assert current_snapshot["evidence_traceability_identity"]["manifest_hash"] == MANIFEST_HASH


def test_traced_ledger_readback_rejects_cross_identity_forgery(isolated_db) -> None:
    _insert_traced_ledger_row(
        isolated_db,
        prediction_hash="3" * 64,
        manifest_hash=_PRE_INTRADAY_MANIFEST_HASH,
        feature_schema_hash=traced_feature_schema_hash(MANIFEST_HASH),
    )
    _insert_traced_ledger_row(
        isolated_db,
        prediction_hash="4" * 64,
        manifest_hash=_PRE_INTRADAY_MANIFEST_HASH,
        trace_manifest_hash=MANIFEST_HASH,
    )

    with pytest.raises(EvidenceFlywheelRuntimeError, match="persisted trace/evidence identity mismatch"):
        _ledger_identity_snapshot(isolated_db, "3" * 64)
    with pytest.raises(EvidenceFlywheelRuntimeError, match="persisted trace/evidence identity mismatch"):
        _ledger_identity_snapshot(isolated_db, "4" * 64)


def test_dataset_candidate_filter_keeps_manifest_feature_hash_cohorts_separate(isolated_db) -> None:
    _insert_traced_ledger_row(isolated_db, prediction_hash="5" * 64, manifest_hash=_PRE_INTRADAY_MANIFEST_HASH)
    _insert_traced_ledger_row(isolated_db, prediction_hash="6" * 64, manifest_hash=MANIFEST_HASH)
    repo = PredictionLedgerRepository(isolated_db)

    old_rows = repo.list_dataset_candidates(
        strategy_id="stock_trend_quality_pullback_v1",
        strategy_version="stock_trend_quality_pullback_v1",
        feature_schema_version=TRACE_FEATURE_SCHEMA_VERSION,
        feature_schema_hash=traced_feature_schema_hash(_PRE_INTRADAY_MANIFEST_HASH),
    )
    current_rows = repo.list_dataset_candidates(
        strategy_id="stock_trend_quality_pullback_v1",
        strategy_version="stock_trend_quality_pullback_v1",
        feature_schema_version=TRACE_FEATURE_SCHEMA_VERSION,
        feature_schema_hash=traced_feature_schema_hash(MANIFEST_HASH),
    )

    assert [row.prediction_hash for row in old_rows] == ["5" * 64]
    assert [row.prediction_hash for row in current_rows] == ["6" * 64]


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
        daily_market_context_enabled=True,
        enable_realtime_quote=True,
        enable_realtime_technical_indicators=True,
        prefetch_realtime_quotes=True,
        enable_chip_distribution=True,
        enable_fundamental_pipeline=True,
        report_integrity_enabled=True,
        searxng_public_instances_enabled=True,
    )


def _ledger_receipt(*, created: bool = True) -> dict:
    return {
        "id": 7,
        "status": "RECORDED",
        "reason_code": None,
        "created": created,
        "prediction_hash": "a" * 64,
        "schema_version": "prediction-ledger-v6",
        "recording_intent_hash": "e" * 64,
        "intended_cohort_id": "f" * 64,
        "evidence_hash": "b" * 64,
        "feature_schema_hash": "c" * 64,
        "strategy_eligibility_version": "strategy-eligibility-v2",
        "strategy_eligibility_state": "UNKNOWN",
        "strategy_eligibility_hash": "d" * 64,
        "strategy_eligibility_reason_codes": ["STRATEGY_ELIGIBILITY_NOT_BOUND"],
        "decision_time_utc": "2026-09-17T10:05:00Z",
        "decision_timezone": "Asia/Shanghai",
        "decision_phase": "postmarket",
        "session_date": "2026-09-17",
        "effective_daily_bar_date": "2026-09-17",
        "outcome_label_anchor": "2026-09-17",
        "data_as_of": "2026-09-17",
        "available_at_max_utc": "2026-09-17T10:00:00Z",
        "pit_eligible": True,
        "pit_ineligibility_reasons": [],
        "durability_state": "LOCAL_DB_ONLY",
    }


def _pipeline_factory(observed: dict, receipt: dict | None):
    class FakePipeline:
        def __init__(self, **kwargs):
            observed["init"] = kwargs
            observed["config_at_init"] = {
                name: getattr(kwargs["config"], name)
                for name in (
                    "daily_market_context_enabled",
                    "enable_realtime_quote",
                    "enable_realtime_technical_indicators",
                    "prefetch_realtime_quotes",
                    "enable_chip_distribution",
                    "enable_fundamental_pipeline",
                    "report_integrity_enabled",
                    "searxng_public_instances_enabled",
                )
            }
            self.analyzer = SimpleNamespace(
                p0_model_request_budget=kwargs.get("p0_model_request_budget"),
                p0_model_request_count=0,
            )

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


def _synthetic_daily_history(*, end_date: date, periods: int = 320) -> pd.DataFrame:
    dates = pd.bdate_range(end=end_date, periods=periods)
    close = [
        100.0 + index * 0.03 + (-0.20 if index % 5 == 0 else 0.05)
        for index in range(periods)
    ]
    frame = pd.DataFrame(
        {
            "code": ["600519"] * periods,
            "date": dates.date,
            "open": [value - 0.10 for value in close],
            "high": [value + 0.40 for value in close],
            "low": [value - 0.40 for value in close],
            "close": close,
            "volume": [1_000_000.0 + (index % 10) * 10_000.0 for index in range(periods)],
        }
    )
    frame["amount"] = frame["close"] * frame["volume"]
    frame["pct_chg"] = frame["close"].pct_change().fillna(0.0) * 100
    frame["ma5"] = frame["close"].rolling(5).mean()
    frame["ma10"] = frame["close"].rolling(10).mean()
    frame["ma20"] = frame["close"].rolling(20).mean()
    frame["volume_ratio"] = 1.0
    return frame


class _SyntheticFetcherManager:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.daily_calls = []

    def get_stock_name(self, code, allow_realtime=False):
        return "合成样本"

    def get_daily_data(self, code, *args, **kwargs):
        self.daily_calls.append({"code": code, "args": args, "kwargs": kwargs})
        return self.frame.copy(), "SyntheticFixture"

    def prefetch_stock_names(self, stock_codes, use_bulk=False):
        return len(stock_codes)

    def get_chip_distribution(self, code):
        return None

    def get_fundamental_context(self, code, budget_seconds=None):
        return {
            "market": "cn",
            "coverage": {"boards": "not_supported"},
            "boards": {"status": "not_supported"},
            "source_chain": ["synthetic-fixture"],
        }

    def build_failed_fundamental_context(self, code, error):
        return {
            "market": "cn",
            "coverage": {"boards": "not_supported"},
            "boards": {"status": "not_supported"},
            "source_chain": [],
            "error": str(error),
        }


def _table_counts(db: DatabaseManager) -> dict[str, int]:
    table_names = sorted(inspect(db._engine).get_table_names())
    with db._engine.connect() as connection:
        return {
            table_name: int(
                connection.execute(
                    text(f'SELECT COUNT(*) FROM "{table_name}"')
                ).scalar_one()
            )
            for table_name in table_names
        }


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
    assert receipt["model_request_budget"] == 0
    assert receipt["model_request_count"] == 0
    assert receipt["ledger_receipts"][0]["prediction_hash"] == "a" * 64
    assert observed["init"]["p0_bounded_trial"] is True
    assert observed["init"]["p0_suppress_notification"] is True
    assert observed["init"]["p0_model_request_budget"] == 0
    assert observed["init"]["research_code_sha"] == "1" * 40
    assert observed["run"]["send_notification"] is False
    assert observed["run"]["merge_notification"] is False
    assert set(observed["config_at_init"].values()) == {False}
    assert os.getenv("RESEARCH_STATE_DURABILITY_ENABLED") is None
    assert config.single_stock_notify is True
    assert config.merge_email_notification is True
    assert config.report_type == "full"
    assert config.agent_mode is True
    assert config.market_review_enabled is True
    assert config.daily_market_context_enabled is True
    assert config.enable_realtime_quote is True
    assert config.enable_realtime_technical_indicators is True
    assert config.prefetch_realtime_quotes is True
    assert config.enable_chip_distribution is True
    assert config.enable_fundamental_pipeline is True
    assert config.report_integrity_enabled is True
    assert config.searxng_public_instances_enabled is True


def _fake_replay_record_runner(expected_sessions: list[date], code_sha: str, calls: list[dict]):
    def runner(**kwargs):
        index = len(calls)
        session_date = expected_sessions[index]
        session_text = session_date.isoformat()
        calls.append(kwargs)
        receipt = _ledger_receipt()
        cohort_id = kwargs["intended_cohort_id"]
        receipt["intended_cohort_id"] = cohort_id
        receipt["prediction_hash"] = f"{index + 1:064x}"
        receipt["session_date"] = session_text
        receipt["effective_daily_bar_date"] = session_text
        receipt["outcome_label_anchor"] = session_text
        receipt["data_as_of"] = session_text
        return {
            "schema_version": "evidence-flywheel-runtime-receipt-v1",
            "phase": "record",
            "status": "RECORDED",
            "record_count": 1,
            "intended_cohort_id": cohort_id,
            "notification_suppressed": True,
            "training_requested": False,
            "model_request_budget": 0,
            "model_request_count": 0,
            "ledger_receipts": [receipt],
            "ledger_identities": [
                {
                    "stock_code": "600519",
                    "code_sha": code_sha,
                    "selection_source": "SPECIFIED_CODES",
                    "intended_cohort_id": cohort_id,
                    "strategy_id": "stock_trend_quality_pullback_v1",
                    "strategy_eligibility_state": "UNKNOWN",
                    "decision_phase": "postmarket",
                    "session_date": session_text,
                    "effective_daily_bar_date": session_text,
                }
            ],
            "artifact_policy": {
                "receipt_only": True,
                "report_files_created": False,
            },
        }

    return runner


def test_replay_specified_codes_sessions_are_ordered_receipt_only(tmp_path) -> None:
    sessions = [date(2026, 9, 17), date(2026, 9, 18)]
    code_sha = "7" * 40
    calls: list[dict] = []
    config = SimpleNamespace(database_path=str(tmp_path / "replay.db"))

    receipt = replay_specified_codes_daily_sessions(
        stock_code="600519",
        session_dates=sessions,
        code_sha=code_sha,
        config=config,
        reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
        record_runner=_fake_replay_record_runner(sessions, code_sha, calls),
    )

    assert receipt["status"] == "REPLAY_RECORDED"
    assert receipt["selection_source"] == "SPECIFIED_CODES"
    assert receipt["session_dates"] == ["2026-09-17", "2026-09-18"]
    assert receipt["session_count"] == 2
    assert len(receipt["intended_cohort_id"]) == 64
    assert {
        call["intended_cohort_id"]
        for call in calls
    } == {receipt["intended_cohort_id"]}
    assert receipt["artifact_policy"] == {
        "receipt_only": True,
        "report_files_created": False,
    }
    assert receipt["outcome_requested"] is False
    assert receipt["pit_manifest_requested"] is False
    assert receipt["training_requested"] is False
    assert len(calls) == 2
    assert [call["current_time"].isoformat() for call in calls] == [
        "2026-09-17T10:00:00+00:00",
        "2026-09-18T10:00:00+00:00",
    ]
    assert all(call["receipt_only"] is True for call in calls)
    assert all(call["closed_world_database_receipt"] is False for call in calls)
    assert all(call["require_single_stock"] is True for call in calls)
    repeated_calls: list[dict] = []
    repeated = replay_specified_codes_daily_sessions(
        stock_code="600519",
        session_dates=sessions,
        code_sha=code_sha,
        config=SimpleNamespace(database_path=str(tmp_path / "replay-repeat.db")),
        reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
        record_runner=_fake_replay_record_runner(
            sessions,
            code_sha,
            repeated_calls,
        ),
    )
    assert repeated["intended_cohort_id"] == receipt["intended_cohort_id"]



def test_replay_cohort_changes_when_exact_session_set_changes(tmp_path) -> None:
    code_sha = "7" * 40
    first_sessions = [date(2026, 9, 17)]
    second_sessions = [date(2026, 9, 18)]
    first_calls: list[dict] = []
    second_calls: list[dict] = []

    first = replay_specified_codes_daily_sessions(
        stock_code="600519",
        session_dates=first_sessions,
        code_sha=code_sha,
        config=SimpleNamespace(database_path=str(tmp_path / "replay-first.db")),
        reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
        record_runner=_fake_replay_record_runner(
            first_sessions,
            code_sha,
            first_calls,
        ),
    )
    second = replay_specified_codes_daily_sessions(
        stock_code="600519",
        session_dates=second_sessions,
        code_sha=code_sha,
        config=SimpleNamespace(database_path=str(tmp_path / "replay-second.db")),
        reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
        record_runner=_fake_replay_record_runner(
            second_sessions,
            code_sha,
            second_calls,
        ),
    )

    assert first["intended_cohort_id"] != second["intended_cohort_id"]


@pytest.mark.parametrize(
    ("sessions", "message"),
    (
        ([], "explicit session dates"),
        ([date(2026, 9, 17), date(2026, 9, 17)], "duplicate sessions"),
        ([date(2026, 9, 18), date(2026, 9, 17)], "strictly ascending"),
        ([date(2026, 9, 19)], "XSHG trading session"),
        ([date(2026, 9, 22)], "future sessions"),
        ([date(2026, 9, 17)] * 21, "at most 20 sessions"),
    ),
)
def test_replay_rejects_invalid_session_sequences_before_record_runner(
    tmp_path,
    sessions,
    message,
) -> None:
    calls = {"count": 0}

    def runner(**kwargs):
        calls["count"] += 1
        raise AssertionError("record runner must not execute")

    with pytest.raises(EvidenceFlywheelBoundaryError, match=message):
        replay_specified_codes_daily_sessions(
            stock_code="600519",
            session_dates=sessions,
            code_sha="7" * 40,
            config=SimpleNamespace(database_path=str(tmp_path / "replay.db")),
            reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
            record_runner=runner,
        )
    assert calls["count"] == 0


def test_fresh_database_probe_releases_sqlite_file_handle(tmp_path) -> None:
    database = tmp_path / "occupied.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE occupied (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO occupied DEFAULT VALUES")
    connection.commit()
    connection.close()

    with pytest.raises(EvidenceFlywheelRuntimeError, match="fresh isolated database"):
        _require_fresh_sqlite_file(database)

    moved = tmp_path / "moved.db"
    database.replace(moved)
    moved.replace(database)


def test_replay_rejects_nonfresh_database_before_record_runner(tmp_path) -> None:
    database = tmp_path / "replay.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE occupied (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO occupied DEFAULT VALUES")
    calls = {"count": 0}

    def runner(**kwargs):
        calls["count"] += 1
        raise AssertionError("record runner must not execute")

    with pytest.raises(EvidenceFlywheelRuntimeError, match="fresh isolated database"):
        replay_specified_codes_daily_sessions(
            stock_code="600519",
            session_dates=[date(2026, 9, 17)],
            code_sha="7" * 40,
            config=SimpleNamespace(database_path=str(database)),
            reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
            record_runner=runner,
        )
    assert calls["count"] == 0


def test_replay_rejects_daily_only_exact_strategy_eligibility_claim(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    code_sha = "7" * 40
    calls: list[dict] = []
    runner = _fake_replay_record_runner([session_date], code_sha, calls)

    def fraudulent_runner(**kwargs):
        receipt = runner(**kwargs)
        receipt["ledger_identities"][0]["strategy_eligibility_state"] = "ELIGIBLE"
        return receipt

    with pytest.raises(EvidenceFlywheelRuntimeError, match="30m hard trigger"):
        replay_specified_codes_daily_sessions(
            stock_code="600519",
            session_dates=[session_date],
            code_sha=code_sha,
            config=SimpleNamespace(database_path=str(tmp_path / "replay.db")),
            reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
            record_runner=fraudulent_runner,
        )


def test_replay_rejects_persisted_ledger_session_mismatch(tmp_path) -> None:
    session_date = date(2026, 9, 17)
    code_sha = "7" * 40
    calls: list[dict] = []
    runner = _fake_replay_record_runner([session_date], code_sha, calls)

    def mismatched_runner(**kwargs):
        receipt = runner(**kwargs)
        receipt["ledger_identities"][0]["session_date"] = "2026-09-16"
        return receipt

    with pytest.raises(EvidenceFlywheelRuntimeError, match="Ledger session mismatch"):
        replay_specified_codes_daily_sessions(
            stock_code="600519",
            session_dates=[session_date],
            code_sha=code_sha,
            config=SimpleNamespace(database_path=str(tmp_path / "replay.db")),
            reference_time=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
            record_runner=mismatched_runner,
        )


@pytest.mark.parametrize(
    "codes",
    (
        [],
        ["600519", "000001"],
    ),
)
def test_actions_record_requires_exactly_one_stock_before_pipeline_factory(codes) -> None:
    observed = {"factory_calls": 0}

    def factory(**kwargs):
        observed["factory_calls"] += 1
        raise AssertionError("factory must not run")

    with pytest.raises(
        EvidenceFlywheelBoundaryError,
        match="exactly one CN stock",
    ):
        record_canonical_run(
            stock_codes=codes,
            code_sha="6" * 40,
            config=_config(),
            pipeline_factory=factory,
            require_single_stock=True,
        )

    assert observed["factory_calls"] == 0


def test_closed_world_record_rejects_nonempty_sqlite_before_pipeline_factory(
    tmp_path,
) -> None:
    database = tmp_path / "preexisting.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE preexisting_probe (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO preexisting_probe DEFAULT VALUES")

    config = _config()
    config.database_path = str(database)
    observed = {"factory_calls": 0}

    def factory(**kwargs):
        observed["factory_calls"] += 1
        raise AssertionError("factory must not run")

    with pytest.raises(
        EvidenceFlywheelRuntimeError,
        match="fresh isolated database before Pipeline construction",
    ):
        record_canonical_run(
            stock_codes=["600519"],
            code_sha="6" * 40,
            config=config,
            pipeline_factory=factory,
            require_single_stock=True,
            closed_world_database_receipt=True,
        )

    assert observed["factory_calls"] == 0


@pytest.mark.parametrize("suffix", ("-wal", "-shm"))
def test_closed_world_record_rejects_sqlite_sidecars_before_pipeline_factory(
    tmp_path,
    suffix,
) -> None:
    database = tmp_path / "fresh.db"
    Path(f"{database}{suffix}").write_bytes(b"stale-sidecar")
    config = _config()
    config.database_path = str(database)
    observed = {"factory_calls": 0}

    def factory(**kwargs):
        observed["factory_calls"] += 1
        raise AssertionError("factory must not run")

    with pytest.raises(
        EvidenceFlywheelRuntimeError,
        match="WAL/SHM sidecars",
    ):
        record_canonical_run(
            stock_codes=["600519"],
            code_sha="6" * 40,
            config=config,
            pipeline_factory=factory,
            require_single_stock=True,
            closed_world_database_receipt=True,
        )

    assert observed["factory_calls"] == 0


def test_closed_world_record_rechecks_initialized_database_before_pipeline_run(
    tmp_path,
) -> None:
    Config.reset_instance()
    DatabaseManager.reset_instance()
    config = Config(
        database_path=str(tmp_path / "initialized.db"),
        sqlite_wal_enabled=False,
    )
    Config._instance = config
    observed = {"run_count": 0}

    class PrepopulatedPipeline:
        def __init__(self, **kwargs):
            self.db = DatabaseManager(db_url=config.get_db_url())
            with self.db._engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE preexisting_probe (id INTEGER PRIMARY KEY)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO preexisting_probe DEFAULT VALUES"
                )
            self.analyzer = SimpleNamespace(
                p0_model_request_budget=kwargs.get("p0_model_request_budget"),
                p0_model_request_count=0,
            )

        def run(self, **kwargs):
            observed["run_count"] += 1
            return []

    try:
        with pytest.raises(
            EvidenceFlywheelRuntimeError,
            match="fresh isolated database before Pipeline run",
        ):
            record_canonical_run(
                stock_codes=["600519"],
                code_sha="6" * 40,
                config=config,
                pipeline_factory=PrepopulatedPipeline,
                require_single_stock=True,
                closed_world_database_receipt=True,
            )
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()

    assert observed["run_count"] == 0


def test_record_parser_binds_actions_receipt_flags() -> None:
    args = _build_parser().parse_args(
        [
            "record",
            "--stocks",
            "600519",
            "--code-sha",
            "1" * 40,
            "--single-stock-only",
            "--closed-world-receipt",
            "--receipt-file",
            "receipt.json",
        ]
    )
    assert args.single_stock_only is True
    assert args.closed_world_receipt is True
    assert args.product_report_receipt is False
    assert args.receipt_file == "receipt.json"


def test_record_cli_closed_world_binds_receipt_only_and_plain_record_does_not(
    monkeypatch,
    tmp_path,
) -> None:
    from src.services.evidence_flywheel_runtime import main as evidence_flywheel_main

    database = tmp_path / "closed.db"
    database.write_bytes(b"closed-world-db")
    config = Config(database_path=str(database), sqlite_wal_enabled=False)
    observed = []

    def fake_record_canonical_run(**kwargs):
        observed.append(dict(kwargs))
        return {
            "status": "RECORDED",
            "database_receipt": {},
        }

    monkeypatch.setattr(
        "src.services.evidence_flywheel_runtime.record_canonical_run",
        fake_record_canonical_run,
    )
    monkeypatch.setattr(
        "src.services.evidence_flywheel_runtime._reset_default_runtime_state",
        lambda: None,
    )
    monkeypatch.setattr("src.config.get_config", lambda: config)

    with patch.dict(
        "sys.modules",
        {"main": SimpleNamespace(validate_p0_stock_codes=lambda raw: ["600519"])},
    ):
        assert evidence_flywheel_main(
            [
                "record",
                "--stocks",
                "600519",
                "--code-sha",
                "1" * 40,
                "--single-stock-only",
                "--closed-world-receipt",
            ]
        ) == 0
        assert evidence_flywheel_main(
            [
                "record",
                "--stocks",
                "600519",
                "--code-sha",
                "1" * 40,
            ]
        ) == 0
        assert evidence_flywheel_main(
            [
                "record",
                "--stocks",
                "600519",
                "--code-sha",
                "1" * 40,
                "--single-stock-only",
                "--closed-world-receipt",
                "--product-report-receipt",
            ]
        ) == 0
        assert evidence_flywheel_main(
            [
                "record",
                "--stocks",
                "600519",
                "--code-sha",
                "1" * 40,
                "--product-report-receipt",
            ]
        ) == 1

    assert observed[0]["closed_world_database_receipt"] is True
    assert observed[0]["receipt_only"] is True
    assert observed[0]["product_report_receipt"] is False
    assert observed[1]["closed_world_database_receipt"] is False
    assert observed[1]["receipt_only"] is False
    assert observed[1]["product_report_receipt"] is False
    assert observed[2]["closed_world_database_receipt"] is True
    assert observed[2]["receipt_only"] is False
    assert observed[2]["product_report_receipt"] is True
    assert len(observed) == 3


def _matrix_receipt_fixture():
    cells = [
        {
            "timeframe": timeframe,
            "family": family,
            "state": "MISSING",
            "reason": "TEST_FIXTURE",
            "method_coverage": [],
        }
        for timeframe in ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m")
        for family in ("REGIME", "TREND_RS", "SUPPLY", "COST", "STRUCTURE", "MOMENTUM", "PATTERN", "MTF")
    ]
    return {
        "schema_version": "timeframe-family-matrix-v1",
        "method_window_policy_hash": "e" * 64,
        "cells": cells,
        "matrix_hash": "f" * 64,
    }


def _research_universe_receipt_fixture():
    document = {
        "schema_version": "complete-research-universe-view-v1",
        "requirements": [
            {"requirement_id": "VALUATION"},
            {"requirement_id": "GLOBAL"},
            {"requirement_id": "CHAN"},
        ],
        "methods": [
            {"method_id": "MA_LEVEL_ALIGNMENT_DAILY"},
            {"method_id": "CANDLESTICK"},
        ],
        "planes": [
            {"plane": "FUNDAMENTAL_AND_STRATEGY"},
            {"plane": "MARKET_AND_ASSET"},
            {"plane": "RESEARCH_SHADOW"},
            {"plane": "TECHNICAL_EVIDENCE"},
        ],
        "counts": {
            "requirements": 26,
            "methods": 43,
            "strategy_bindings": 13,
            "technical_matrix_cells": 56,
        },
    }
    document["universe_hash"] = digest(document)
    return document


def test_product_report_receipt_reuses_existing_delivery_fact_identity(
    monkeypatch,
    tmp_path,
) -> None:
    from src.services import evidence_flywheel_runtime as runtime

    universe = _research_universe_receipt_fixture()

    factor = {
        "canonical_decision": {"action": "WAIT"},
        "investor_brief": {
            "one_line_conclusion": "当前结论",
            "fused_paragraph": "月线与日线材料融合。",
            "coverage_text": "周线、60分钟、30分钟、15分钟、5分钟本次暂无可用证据。",
            "coverage": {
                "monthly": "READY",
                "weekly": "MISSING",
                "daily": "PARTIAL_CURRENT",
                "60m": "MISSING",
                "30m": "MISSING",
                "15m": "MISSING",
                "5m": "MISSING",
            },
        },
        "evidence_product_coverage": {
            "schema_version": "v25-product-coverage-v1",
            "receipt_hash": "b" * 64,
            "baseline_sha256": "c" * 64,
            "method_window_policy_hash": "e" * 64,
            "timeframe_family_matrix_hash": "f" * 64,
            "complete_research_universe_hash": universe["universe_hash"],
            "complete_research_universe_schema_version": universe["schema_version"],
            "complete_research_universe_counts": universe["counts"],
            "rendered": False,
            "slots": [
                {"state": "EVIDENCE_AVAILABLE"},
                {"state": "DATA_INSUFFICIENT"},
            ],
        },
        "evidence_traceability": {
            "schema_version": "canonical-evidence-trace-v2",
            "method_window_policy_hash": "e" * 64,
            "timeframe_family_matrix_hash": "f" * 64,
            "timeframe_family_matrix": _matrix_receipt_fixture(),
            "complete_research_universe_hash": universe["universe_hash"],
            "complete_research_universe": universe,
        },
    }
    factor["evidence_traceability"]["timeframe_family_matrix"]["cells"][0]["canonical_paths"] = (
        "market_sector_regime",
    )
    factor["evidence_product_coverage"]["slots"][0]["canonical_paths"] = (
        "market_sector_regime",
    )
    persisted_factor = json.loads(json.dumps(factor, ensure_ascii=False))

    report = tmp_path / "report_20261002.md"
    report.write_text(
        "# actual local report\n"
        "**综合结论**: 当前结论\n"
        "月线与日线材料融合。\n"
        "周线、60分钟、30分钟、15分钟、5分钟本次暂无可用证据。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "_PRODUCT_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(
        runtime,
        "_persisted_factor_snapshot",
        lambda db_manager, pipeline, prediction_hash: (persisted_factor, 7),
    )
    monkeypatch.setattr(
        runtime,
        "_ledger_identity_snapshot",
        lambda db_manager, prediction_hash: {
            "evidence_traceability_identity": {
                "method_window_policy_hash": "e" * 64,
                "timeframe_family_matrix_hash": "f" * 64,
                "complete_research_universe_hash": universe["universe_hash"],
            }
        },
    )

    class FakePipeline:
        @staticmethod
        def _delivery_fact_hash(value):
            return digest(value)

    result = SimpleNamespace(dashboard={"factor_decision": factor})
    receipt = runtime._build_product_report_receipt(
        db_manager=object(),
        pipeline=FakePipeline(),
        result=result,
        prediction_hash="d" * 64,
    )

    assert receipt["status"] == "PASS"
    assert receipt["analysis_history_id"] == 7
    assert receipt["delivery_fact_hash"] == digest(factor)
    assert receipt["runtime_database_binding"] == "EXACT_FOR_CANONICAL_BRIEF_AND_COVERAGE"
    assert receipt["report_anchor_paths"] == [
        "investor_brief.one_line_conclusion",
        "investor_brief.fused_paragraph",
        "investor_brief.coverage_text",
    ]
    assert receipt["missing_timeframes"] == ["weekly", "60m", "30m", "15m", "5m"]
    assert receipt["timeframe_family_matrix"] == {
        "cell_count": 56,
        "matrix_hash": "f" * 64,
        "method_window_policy_hash": "e" * 64,
    }
    assert receipt["complete_research_universe"] == {
        "schema_version": "complete-research-universe-view-v1",
        "universe_hash": universe["universe_hash"],
        "requirement_count": 26,
        "method_count": 43,
        "strategy_binding_count": 13,
    }
    assert receipt["report_bytes"] > 0
    assert len(receipt["report_sha256"]) == 64
    persisted_factor["canonical_decision"]["action"] = "FORGED_BUY"
    with pytest.raises(
        EvidenceFlywheelRuntimeError,
        match="runtime/database drift in canonical_decision",
    ):
        runtime._build_product_report_receipt(
            db_manager=object(),
            pipeline=FakePipeline(),
            result=result,
            prediction_hash="d" * 64,
        )
    persisted_factor["canonical_decision"]["action"] = "WAIT"

    report.write_text("# broken report\n", encoding="utf-8")
    with pytest.raises(
        EvidenceFlywheelRuntimeError,
        match="report is missing investor_brief.one_line_conclusion",
    ):
        runtime._build_product_report_receipt(
            db_manager=object(),
            pipeline=FakePipeline(),
            result=result,
            prediction_hash="d" * 64,
        )

    assert receipt["v25_semantic_coverage"]["rendered"] is False
    assert receipt["v25_semantic_coverage"]["actual_render_consumer_proven"] is False


def test_record_phase_fails_closed_when_model_request_count_is_nonzero() -> None:
    class BadPipeline:
        def __init__(self, **kwargs):
            self.analyzer = SimpleNamespace(
                p0_model_request_budget=kwargs.get("p0_model_request_budget"),
                p0_model_request_count=0,
            )

        def run(self, **kwargs):
            self.analyzer.p0_model_request_count = 1
            return [
                SimpleNamespace(
                    code="600519",
                    success=True,
                    prediction_ledger_receipt=_ledger_receipt(),
                )
            ]

    with pytest.raises(
        EvidenceFlywheelRuntimeError,
        match="observed an external model request",
    ):
        record_canonical_run(
            stock_codes=["600519"],
            code_sha="7" * 40,
            config=_config(),
            pipeline_factory=BadPipeline,
        )


def test_record_phase_fails_before_run_when_zero_budget_is_not_bound() -> None:
    observed = {"run_count": 0}

    class WrongBudgetPipeline:
        def __init__(self, **kwargs):
            self.analyzer = SimpleNamespace(
                p0_model_request_budget=2,
                p0_model_request_count=0,
            )

        def run(self, **kwargs):
            observed["run_count"] += 1
            return []

    with pytest.raises(
        EvidenceFlywheelRuntimeError,
        match="did not bind the zero external-model request budget",
    ):
        record_canonical_run(
            stock_codes=["600519"],
            code_sha="9" * 40,
            config=_config(),
            pipeline_factory=WrongBudgetPipeline,
        )

    assert observed["run_count"] == 0


def test_record_receipt_projects_only_the_strict_allowlist() -> None:
    observed = {}
    raw_receipt = _ledger_receipt()
    raw_receipt.update(
        {
            "raw_evidence_json": {"private": "payload"},
            "database_bytes": "do-not-project",
            "api_key": "do-not-project",
        }
    )

    receipt = record_canonical_run(
        stock_codes=["600519"],
        code_sha="9" * 40,
        config=_config(),
        pipeline_factory=_pipeline_factory(observed, raw_receipt),
    )

    ledger_receipt = receipt["ledger_receipts"][0]
    assert set(ledger_receipt) == {
        "status",
        "reason_code",
        "stock_code",
        "id",
        "created",
        "prediction_hash",
        "schema_version",
        "recording_intent_hash",
        "intended_cohort_id",
        "evidence_hash",
        "feature_schema_hash",
        "strategy_eligibility_version",
        "strategy_eligibility_state",
        "strategy_eligibility_hash",
        "strategy_eligibility_reason_codes",
        "decision_time_utc",
        "decision_timezone",
        "decision_phase",
        "session_date",
        "effective_daily_bar_date",
        "outcome_label_anchor",
        "data_as_of",
        "available_at_max_utc",
        "pit_eligible",
        "pit_ineligibility_reasons",
        "durability_state",
    }
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "private" not in serialized
    assert "do-not-project" not in serialized
    assert "api_key" not in serialized


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("id", True, "positive integer"),
        ("created", 1, "created must be a boolean"),
        ("status", "TECHNICALLY_LOST", "ledger receipt status must be one of"),
        ("schema_version", "prediction-ledger-v5", "ledger receipt schema_version must be one of"),
        (
            "recording_intent_hash",
            "x" * 64,
            "recording_intent_hash must be exact 64-hex",
        ),
        (
            "intended_cohort_id",
            "x" * 64,
            "intended_cohort_id must be exact 64-hex",
        ),
        ("prediction_hash", "x" * 64, "prediction_hash must be exact 64-hex"),
        ("evidence_hash", "x" * 64, "evidence_hash must be exact 64-hex"),
        ("feature_schema_hash", "x" * 64, "feature_schema_hash must be exact 64-hex"),
        (
            "strategy_eligibility_state",
            "READY",
            "ledger receipt strategy_eligibility_state must be one of",
        ),
        (
            "strategy_eligibility_hash",
            "x" * 64,
            "strategy_eligibility_hash must be exact 64-hex",
        ),
        ("decision_time_utc", "2026-09-17T10:05:00", "must explicitly identify UTC"),
        ("session_date", "not-a-date", "must be an ISO date"),
        ("pit_eligible", "false", "pit_eligible must be a boolean"),
        (
            "pit_ineligibility_reasons",
            ["VALID", 7],
            "pit_ineligibility_reasons must be a list",
        ),
        ("durability_state", "REMOTE", "durability_state must be LOCAL_DB_ONLY"),
    ),
)
def test_record_receipt_rejects_malformed_values(field, value, message) -> None:
    raw_receipt = _ledger_receipt()
    raw_receipt[field] = value

    with pytest.raises(EvidenceFlywheelRuntimeError, match=message):
        record_canonical_run(
            stock_codes=["600519"],
            code_sha="9" * 40,
            config=_config(),
            pipeline_factory=_pipeline_factory({}, raw_receipt),
        )


def test_native_zero_model_record_writes_only_the_admitted_temp_db_surfaces(
    isolated_db,
    tmp_path,
) -> None:
    # 2026-09-25 is a non-trading holiday in the XSHG calendar; use the
    # prior proven session so this native fixture exercises the positive
    # postmarket V4 clock path rather than a holiday fail-closed branch.
    target_date = date(2026, 9, 24)
    frame = _synthetic_daily_history(end_date=target_date)
    fetcher = _SyntheticFetcherManager(frame)
    config = Config.get_instance()
    config.litellm_model = "openai/offline-zero-call"
    config.generation_backend = "litellm"
    config.generation_fallback_backend = ""
    config.llm_model_list = []
    config.gemini_api_keys = []
    config.anthropic_api_keys = []
    config.openai_api_keys = []
    config.deepseek_api_keys = []
    config.searxng_public_instances_enabled = False
    config.enable_realtime_quote = False
    config.enable_chip_distribution = False
    config.market_review_enabled = False
    config.daily_market_context_enabled = False
    config.report_integrity_enabled = False
    config.report_integrity_retry = 0
    config.agent_mode = False
    config.agent_skills = []
    config.report_language = "zh"
    config.max_workers = 1

    market_structure = MagicMock()
    market_structure.build_context.return_value = None
    relative_strength = MagicMock()
    relative_strength.build_context.return_value = None
    before = _table_counts(isolated_db)
    repository_reports = Path(__file__).resolve().parents[1] / "reports"
    repository_report_state_before = (
        {
            str(path.relative_to(repository_reports)): (
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in repository_reports.rglob("*")
            if path.is_file()
        }
        if repository_reports.exists()
        else {}
    )
    notification_module_file = tmp_path / "src" / "notification.py"

    with patch("src.core.pipeline.DataFetcherManager", return_value=fetcher), \
         patch("src.core.pipeline.MarketStructureService", return_value=market_structure), \
         patch("src.core.pipeline.RelativeStrengthService", return_value=relative_strength), \
         patch("src.notification.__file__", str(notification_module_file)), \
         patch.object(
             NotificationService,
             "send_to_email",
             side_effect=AssertionError("email egress must stay disabled"),
         ) as send_email, \
         patch.object(
             NotificationService,
             "send",
             side_effect=AssertionError("notification egress must stay disabled"),
         ) as send_all, \
         patch.object(
             NotificationService,
             "send_with_results",
             side_effect=AssertionError("notification routing must stay disabled"),
         ) as send_with_results, \
         patch.object(GeminiAnalyzer, "_get_skill_prompt_sections", return_value=("", "", True)), \
         patch("src.analyzer.get_api_keys_for_model") as get_keys, \
         patch("src.analyzer.litellm.completion") as completion:
        receipt = record_canonical_run(
            stock_codes=["600519"],
            code_sha="8" * 40,
            config=config,
            current_time=datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
            require_single_stock=True,
            closed_world_database_receipt=True,
            receipt_only=True,
        )

    after = _table_counts(isolated_db)
    deltas = {
        table_name: after[table_name] - before.get(table_name, 0)
        for table_name in after
        if after[table_name] - before.get(table_name, 0)
    }
    assert deltas == {
        "analysis_history": 1,
        "decision_signals": 1,
        "fundamental_snapshot": 1,
        "learning_recording_journal": 1,
        "prediction_ledger": 1,
        "stock_daily": len(frame),
    }
    assert receipt["status"] == "RECORDED"
    assert receipt["record_count"] == 1
    assert receipt["model_request_budget"] == 0
    assert receipt["model_request_count"] == 0
    assert receipt["ledger_receipts"][0]["pit_eligible"] is False
    assert receipt["ledger_receipts"][0]["schema_version"] == "prediction-ledger-v6"
    assert len(receipt["ledger_receipts"][0]["recording_intent_hash"]) == 64
    assert len(receipt["ledger_receipts"][0]["intended_cohort_id"]) == 64
    assert receipt["ledger_receipts"][0]["strategy_eligibility_version"] == "strategy-eligibility-v2"
    assert receipt["ledger_receipts"][0]["strategy_eligibility_state"] == "UNKNOWN"
    assert "REQUIRED_EVIDENCE_INCOMPLETE" in receipt["ledger_receipts"][0][
        "strategy_eligibility_reason_codes"
    ]
    assert "STRATEGY_ELIGIBILITY_NOT_BOUND" not in receipt["ledger_receipts"][0][
        "strategy_eligibility_reason_codes"
    ]
    assert receipt["ledger_receipts"][0]["decision_time_utc"].endswith("Z")
    assert receipt["ledger_receipts"][0]["decision_phase"] == "postmarket"
    assert receipt["ledger_receipts"][0]["session_date"] == "2026-09-24"
    assert receipt["ledger_receipts"][0]["effective_daily_bar_date"] == "2026-09-24"
    assert receipt["ledger_receipts"][0]["outcome_label_anchor"] == "2026-09-24"
    if receipt["ledger_receipts"][0]["available_at_max_utc"] is not None:
        assert receipt["ledger_receipts"][0]["available_at_max_utc"].endswith("Z")
    assert "ADJUSTMENT_BASIS_NOT_PERSISTED" in receipt["ledger_receipts"][0][
        "pit_ineligibility_reasons"
    ]
    with isolated_db.get_session() as session:
        cached_rows = session.execute(select(StockDaily)).scalars().all()
    assert cached_rows
    assert all(row.data_identity_json is None for row in cached_rows)
    assert all(row.data_identity_hash is None for row in cached_rows)
    completion.assert_not_called()
    get_keys.assert_not_called()
    send_email.assert_not_called()
    send_all.assert_not_called()
    send_with_results.assert_not_called()
    report_files = sorted((tmp_path / "reports").glob("report_*.md"))
    assert report_files == []
    repository_report_state_after = (
        {
            str(path.relative_to(repository_reports)): (
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in repository_reports.rglob("*")
            if path.is_file()
        }
        if repository_reports.exists()
        else {}
    )
    assert repository_report_state_after == repository_report_state_before
    assert len(fetcher.daily_calls) == 1
    database_receipt = receipt["database_receipt"]
    assert database_receipt["core_table_counts_after"] == {
        "analysis_history": 1,
        "decision_signals": 1,
        "fundamental_snapshot": 1,
        "learning_recording_journal": 1,
        "prediction_ledger": 1,
        "prediction_outcomes": 0,
        "pit_dataset_manifests": 0,
        "stock_daily": len(frame),
        "llm_usage": 0,
        "news_intel": 0,
        "intelligence_items": 0,
        "alert_notifications": 0,
    }
    assert database_receipt["unexpected_nonzero_table_deltas"] == {}
    assert database_receipt["nonzero_table_count_deltas"] == deltas
    ledger_identity = database_receipt["ledger_identities"][0]
    assert ledger_identity["stock_code"] == "600519"
    assert ledger_identity["code_sha"] == "8" * 40
    assert ledger_identity["selection_source"] == "SPECIFIED_CODES"
    assert ledger_identity["data_snapshot_identity"]
    assert ledger_identity["strategy_id"]
    assert ledger_identity["canonical_action"] in {"WAIT", "PASS"}
    assert ledger_identity["schema_version"] == "prediction-ledger-v6"
    assert len(ledger_identity["recording_intent_hash"]) == 64
    assert len(ledger_identity["intended_cohort_id"]) == 64
    assert ledger_identity["strategy_eligibility_version"] == "strategy-eligibility-v2"
    assert ledger_identity["strategy_eligibility_state"] == "UNKNOWN"
    assert len(ledger_identity["strategy_eligibility_hash"]) == 64
    assert "REQUIRED_EVIDENCE_INCOMPLETE" in ledger_identity[
        "strategy_eligibility_reason_codes"
    ]
    assert "STRATEGY_ELIGIBILITY_NOT_BOUND" not in ledger_identity[
        "strategy_eligibility_reason_codes"
    ]
    assert ledger_identity["decision_time"].endswith("Z")
    assert ledger_identity["decision_phase"] == "postmarket"
    assert ledger_identity["session_date"] == "2026-09-24"
    assert ledger_identity["effective_daily_bar_date"] == "2026-09-24"
    assert ledger_identity["outcome_label_anchor"] == "2026-09-24"
    if ledger_identity["available_at_max"] is not None:
        assert ledger_identity["available_at_max"].endswith("Z")
    assert receipt["artifact_policy"] == {
        "receipt_only": True,
        "report_files_created": False,
        "database_uploaded": False,
        "reports_uploaded": False,
        "logs_uploaded": False,
    }
    assert receipt["route_boundaries"]["report_projection"] == "SUPPRESSED"
    assert receipt["ledger_identities"][0]["code_sha"] == "8" * 40
    assert receipt["ledger_identities"][0]["selection_source"] == "SPECIFIED_CODES"


def test_closed_database_identity_and_receipt_file_are_deterministic(tmp_path) -> None:
    database = tmp_path / "closed.db"
    database.write_bytes(b"closed-world-db")
    identity = _database_file_identity(database)
    assert identity["database_file_name"] == "closed.db"
    assert identity["database_bytes_after_close"] == len(b"closed-world-db")
    assert len(identity["database_sha256_after_close"]) == 64
    assert identity["sessions_closed_before_hash"] is True

    receipt_file = tmp_path / "receipt.json"
    _write_receipt_file(receipt_file.as_posix(), {"identity": identity})
    assert receipt_file.read_text(encoding="utf-8").endswith("\n")
    assert "closed-world-db" not in receipt_file.read_text(encoding="utf-8")


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


def test_non_bounded_pipeline_canonical_failure_propagates_instead_of_preserving_stale_product():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.p0_bounded_trial = False
    pipeline.config = SimpleNamespace(report_language="zh")
    result = SimpleNamespace(
        name="合成样本",
        report_language="zh",
        dashboard={"legacy_product": "must_not_survive_as_success"},
    )

    with (
        patch("src.core.pipeline.SearchService.is_index_or_etf", return_value=False),
        patch(
            "src.core.pipeline.build_stock_factor_decision_summary",
            side_effect=RuntimeError("canonical build failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="canonical build failed"):
            pipeline._attach_factor_decision_summary(
                result,
                code="600519",
                trend_result=SimpleNamespace(),
                fundamental_context=None,
                chip_data=None,
            )

    assert result.dashboard == {"legacy_product": "must_not_survive_as_success"}


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
