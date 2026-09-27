# -*- coding: utf-8 -*-
"""Deterministic tests for the local-only minimum evidence-flywheel runtime."""

from __future__ import annotations

from datetime import date, datetime, timezone
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import inspect, text

from src.analyzer import GeminiAnalyzer
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
    assert os.getenv("RESEARCH_STATE_DURABILITY_ENABLED") is None
    assert config.single_stock_notify is True
    assert config.merge_email_notification is True
    assert config.report_type == "full"
    assert config.agent_mode is True
    assert config.market_review_enabled is True


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

    notifier = MagicMock()
    notifier.generate_aggregate_report.return_value = "native zero-model audit"
    audit_path = tmp_path / "audit-not-written.md"
    notifier.save_report_to_file.return_value = str(audit_path)
    market_structure = MagicMock()
    market_structure.build_context.return_value = None
    relative_strength = MagicMock()
    relative_strength.build_context.return_value = None
    before = _table_counts(isolated_db)

    with patch("src.core.pipeline.DataFetcherManager", return_value=fetcher), \
         patch("src.core.pipeline.NotificationService", return_value=notifier), \
         patch("src.core.pipeline.MarketStructureService", return_value=market_structure), \
         patch("src.core.pipeline.RelativeStrengthService", return_value=relative_strength), \
         patch.object(GeminiAnalyzer, "_get_skill_prompt_sections", return_value=("", "", True)), \
         patch("src.analyzer.get_api_keys_for_model") as get_keys, \
         patch("src.analyzer.litellm.completion") as completion:
        receipt = record_canonical_run(
            stock_codes=["600519"],
            code_sha="8" * 40,
            config=config,
            current_time=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc),
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
    notifier.send_to_email.assert_not_called()
    notifier.send.assert_not_called()
    notifier.save_report_to_file.assert_called_once_with("native zero-model audit")
    assert not audit_path.exists()
    assert len(fetcher.daily_calls) == 1


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
