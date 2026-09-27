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
from sqlalchemy import inspect, text

from src.analyzer import GeminiAnalyzer
from src.config import Config
from src.notification import NotificationService
from src.services.evidence_flywheel_runtime import (
    EvidenceFlywheelBoundaryError,
    EvidenceFlywheelRuntimeError,
    _build_parser,
    _database_file_identity,
    _write_receipt_file,
    build_pit_manifest_receipt,
    evaluate_prediction_outcome,
    record_canonical_run,
)
from src.services.pit_dataset_service import PITDatasetService
from src.storage import DatabaseManager


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
    assert args.receipt_file == "receipt.json"


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
        "stock_code",
        "id",
        "created",
        "prediction_hash",
        "evidence_hash",
        "feature_schema_hash",
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
        ("prediction_hash", "x" * 64, "prediction_hash must be exact 64-hex"),
        ("evidence_hash", "x" * 64, "evidence_hash must be exact 64-hex"),
        ("feature_schema_hash", "x" * 64, "feature_schema_hash must be exact 64-hex"),
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
    target_date = date(2026, 9, 25)
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
    assert not repository_reports.exists()
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
            current_time=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc),
            require_single_stock=True,
            closed_world_database_receipt=True,
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
        "prediction_ledger": 1,
        "stock_daily": len(frame),
    }
    assert receipt["status"] == "RECORDED"
    assert receipt["record_count"] == 1
    assert receipt["model_request_budget"] == 0
    assert receipt["model_request_count"] == 0
    assert receipt["ledger_receipts"][0]["pit_eligible"] is False
    assert "ADJUSTMENT_BASIS_NOT_PERSISTED" in receipt["ledger_receipts"][0][
        "pit_ineligibility_reasons"
    ]
    completion.assert_not_called()
    get_keys.assert_not_called()
    send_email.assert_not_called()
    send_all.assert_not_called()
    send_with_results.assert_not_called()
    report_files = sorted((tmp_path / "reports").glob("report_*.md"))
    assert len(report_files) == 1
    assert "600519" in report_files[0].read_text(encoding="utf-8")
    assert not repository_reports.exists()
    assert len(fetcher.daily_calls) == 1
    database_receipt = receipt["database_receipt"]
    assert database_receipt["core_table_counts_after"] == {
        "analysis_history": 1,
        "decision_signals": 1,
        "fundamental_snapshot": 1,
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
    assert receipt["artifact_policy"] == {
        "receipt_only": True,
        "database_uploaded": False,
        "reports_uploaded": False,
        "logs_uploaded": False,
    }


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
