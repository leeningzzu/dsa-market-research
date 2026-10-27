# -*- coding: utf-8 -*-
"""Local-only orchestration for the minimum DSA evidence flywheel slice.

This module reuses the existing bounded analysis, Prediction Ledger,
PredictionOutcome, and PIT manifest owners.  It never trains a model, sends a
notification, or enables external research-state durability.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Sequence

from src.core.trading_calendar import (
    resolve_historical_daily_bar_date,
    resolve_latest_completed_session_fail_closed,
)
import uuid
from zoneinfo import ZoneInfo

from src.services.pit_identity import (
    build_cn_stock_asset_identity,
    build_specified_codes_selection_context,
    sha256_payload,
)
from src.services.prediction_ledger_service import (
    PREDICTION_LEDGER_SCHEMA_VERSION,
    TRACE_FEATURE_SCHEMA_VERSION,
    traced_feature_schema_hash,
)
from src.services.research_state_projection import (
    STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
    build_strategy_eligibility_identity,
)
from src.services.evidence_traceability_registry import (
    ACCEPTED_DATA_OBJECT_DOMAIN_COUNTS,
    ACCEPTED_DATA_OBJECT_SUBSET_SHA256,
    ACCEPTED_PRODUCT_SLOT_IDS,
    ACCEPTED_REQUIREMENT_ROLES,
    ACCEPTED_VALUATION_ROUTE_IDS,
    ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS,
    BASELINE_SHA256,
    TIMEFRAMES,
    at,
    digest,
    json_wire_equal,
)


RECEIPT_SCHEMA_VERSION = "evidence-flywheel-runtime-receipt-v1"
GATE6_LEARNING_ADMISSION_SCHEMA_VERSION = "gate6-learning-admission-v1"
_GATE6_STAGE_ORDER = (
    "GATE5_DYNAMIC_PRODUCT",
    "RECORDING_LEDGER",
    "MATURE_OUTCOMES",
    "PIT_DATASET",
    "WHITEBOX_OOT",
    "LOGISTIC_META",
    "LIGHTGBM_INCREMENTAL",
    "INDEPENDENT_CALIBRATION",
    "FROZEN_SHADOW",
    "EXPLICIT_PROMOTION",
)
MAX_IDENTITY_FILE_BYTES = 64 * 1024
ZERO_EXTERNAL_MODEL_REQUEST_BUDGET = 0
MAX_HISTORICAL_REPLAY_SESSIONS = 20
_CN_REPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
_CN_REPLAY_POSTMARKET_TIME = time(hour=18, minute=0)
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
_CLOSED_WORLD_ALLOWED_NONZERO_DELTAS = frozenset(
    {
        "analysis_history",
        "decision_signals",
        "fundamental_snapshot",
        "learning_recording_journal",
        "prediction_ledger",
        "stock_daily",
    }
)
_CLOSED_WORLD_EXACT_SINGLE_ROW_TABLES = (
    "analysis_history",
    "decision_signals",
    "fundamental_snapshot",
    "learning_recording_journal",
    "prediction_ledger",
)
_CLOSED_WORLD_CORE_TABLES = (
    "analysis_history",
    "decision_signals",
    "fundamental_snapshot",
    "learning_recording_journal",
    "prediction_ledger",
    "prediction_outcomes",
    "pit_dataset_manifests",
    "stock_daily",
    "llm_usage",
    "news_intel",
    "intelligence_items",
    "alert_notifications",
)

_PRODUCT_REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"
_PRODUCT_READY_STATES = frozenset({"READY", "PROVEN_CURRENT", "PARTIAL_CURRENT"})


class EvidenceFlywheelRuntimeError(RuntimeError):
    """Fail-closed boundary error for the local evidence-flywheel entrypoint."""

    code = "EVIDENCE_FLYWHEEL_RUNTIME_ERROR"


class EvidenceFlywheelBoundaryError(EvidenceFlywheelRuntimeError):
    code = "EVIDENCE_FLYWHEEL_BOUNDARY_ERROR"


def _require_sha(value: Any, *, length: int, field: str) -> str:
    text = str(value or "").strip().lower()
    pattern = _SHA40_RE if length == 40 else _SHA64_RE
    if pattern.fullmatch(text) is None:
        raise EvidenceFlywheelBoundaryError(f"{field} must be exact {length}-hex")
    return text


def _require_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EvidenceFlywheelRuntimeError(f"{field} must be a positive integer")
    return value


def _require_bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise EvidenceFlywheelRuntimeError(f"{field} must be a boolean")
    return value


def _require_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise EvidenceFlywheelRuntimeError(f"{field} must be a list of non-empty strings")
    return list(value)


def _require_optional_text(value: Any, *, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EvidenceFlywheelRuntimeError(f"{field} must be null or a non-empty string")
    return value.strip()


def _require_choice(value: Any, *, field: str, allowed: set[str]) -> str:
    text = _require_optional_text(value, field=field)
    if text not in allowed:
        raise EvidenceFlywheelRuntimeError(
            f"{field} must be one of {sorted(allowed)}"
        )
    return text


def _require_optional_iso_date(value: Any, *, field: str) -> Optional[str]:
    text = _require_optional_text(value, field=field)
    if text is None:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise EvidenceFlywheelRuntimeError(f"{field} must be an ISO date") from exc
    return parsed.isoformat()


def _require_optional_utc_datetime(value: Any, *, field: str) -> Optional[str]:
    text = _require_optional_text(value, field=field)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceFlywheelRuntimeError(f"{field} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceFlywheelRuntimeError(f"{field} must explicitly identify UTC")
    if parsed.utcoffset().total_seconds() != 0:
        raise EvidenceFlywheelRuntimeError(f"{field} must use UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validated_ledger_receipt(
    raw_receipt: Mapping[str, Any],
    *,
    stock_code: str,
) -> Dict[str, Any]:
    durability_state = raw_receipt.get("durability_state")
    if durability_state != "LOCAL_DB_ONLY":
        raise EvidenceFlywheelRuntimeError(
            "Prediction Ledger receipt durability_state must be LOCAL_DB_ONLY"
        )
    return {
        "stock_code": str(stock_code or "").strip(),
        "status": _require_choice(
            raw_receipt.get("status"),
            field="ledger receipt status",
            allowed={"RECORDED"},
        ),
        "reason_code": _require_optional_text(
            raw_receipt.get("reason_code"),
            field="ledger receipt reason_code",
        ),
        "id": _require_positive_int(raw_receipt.get("id"), field="ledger receipt id"),
        "created": _require_bool(
            raw_receipt.get("created"),
            field="ledger receipt created",
        ),
        "prediction_hash": _require_sha(
            raw_receipt.get("prediction_hash"),
            length=64,
            field="prediction_hash",
        ),
        "schema_version": _require_choice(
            raw_receipt.get("schema_version"),
            field="ledger receipt schema_version",
            allowed={PREDICTION_LEDGER_SCHEMA_VERSION},
        ),
        "recording_intent_hash": _require_sha(
            raw_receipt.get("recording_intent_hash"),
            length=64,
            field="ledger receipt recording_intent_hash",
        ),
        "intended_cohort_id": _require_sha(
            raw_receipt.get("intended_cohort_id"),
            length=64,
            field="ledger receipt intended_cohort_id",
        ),
        "evidence_hash": _require_sha(
            raw_receipt.get("evidence_hash"),
            length=64,
            field="evidence_hash",
        ),
        "feature_schema_hash": _require_sha(
            raw_receipt.get("feature_schema_hash"),
            length=64,
            field="feature_schema_hash",
        ),
        "strategy_eligibility_version": _require_choice(
            raw_receipt.get("strategy_eligibility_version"),
            field="ledger receipt strategy_eligibility_version",
            allowed={STRATEGY_ELIGIBILITY_SCHEMA_VERSION},
        ),
        "strategy_eligibility_state": _require_choice(
            raw_receipt.get("strategy_eligibility_state"),
            field="ledger receipt strategy_eligibility_state",
            allowed={"ELIGIBLE", "INELIGIBLE", "UNKNOWN", "NOT_APPLICABLE"},
        ),
        "strategy_eligibility_hash": _require_sha(
            raw_receipt.get("strategy_eligibility_hash"),
            length=64,
            field="strategy_eligibility_hash",
        ),
        "strategy_eligibility_reason_codes": _require_string_list(
            raw_receipt.get("strategy_eligibility_reason_codes"),
            field="ledger receipt strategy_eligibility_reason_codes",
        ),
        "decision_time_utc": _require_optional_utc_datetime(
            raw_receipt.get("decision_time_utc"),
            field="ledger receipt decision_time_utc",
        ),
        "decision_timezone": _require_optional_text(
            raw_receipt.get("decision_timezone"),
            field="ledger receipt decision_timezone",
        ),
        "decision_phase": _require_optional_text(
            raw_receipt.get("decision_phase"),
            field="ledger receipt decision_phase",
        ),
        "session_date": _require_optional_iso_date(
            raw_receipt.get("session_date"),
            field="ledger receipt session_date",
        ),
        "effective_daily_bar_date": _require_optional_iso_date(
            raw_receipt.get("effective_daily_bar_date"),
            field="ledger receipt effective_daily_bar_date",
        ),
        "outcome_label_anchor": _require_optional_iso_date(
            raw_receipt.get("outcome_label_anchor"),
            field="ledger receipt outcome_label_anchor",
        ),
        "data_as_of": _require_optional_iso_date(
            raw_receipt.get("data_as_of"),
            field="ledger receipt data_as_of",
        ),
        "available_at_max_utc": _require_optional_utc_datetime(
            raw_receipt.get("available_at_max_utc"),
            field="ledger receipt available_at_max_utc",
        ),
        "pit_eligible": _require_bool(
            raw_receipt.get("pit_eligible"),
            field="ledger receipt pit_eligible",
        ),
        "pit_ineligibility_reasons": _require_string_list(
            raw_receipt.get("pit_ineligibility_reasons"),
            field="ledger receipt pit_ineligibility_reasons",
        ),
        "durability_state": durability_state,
    }


def _validated_codes(
    stock_codes: Sequence[str],
    *,
    require_single_stock: bool = False,
) -> list[str]:
    normalized = [str(item or "").strip() for item in stock_codes]
    if require_single_stock and len(normalized) != 1:
        raise EvidenceFlywheelBoundaryError(
            "Actions record phase requires exactly one CN stock"
        )
    if not 1 <= len(normalized) <= 2:
        raise EvidenceFlywheelBoundaryError("record phase requires exactly one or two CN stocks")
    if len(set(normalized)) != len(normalized):
        raise EvidenceFlywheelBoundaryError("record phase rejects duplicate stock codes")
    for code in normalized:
        identity = build_cn_stock_asset_identity(code, "cn")
        if not identity or identity.get("symbol") != code:
            raise EvidenceFlywheelBoundaryError(f"unsupported CN stock identity: {code}")
    return normalized


def _validated_replay_sessions(
    values: Sequence[Any],
    *,
    reference_time: Optional[datetime] = None,
) -> list[date]:
    if isinstance(values, (str, bytes)) or not values:
        raise EvidenceFlywheelBoundaryError("historical replay requires explicit session dates")
    if len(values) > MAX_HISTORICAL_REPLAY_SESSIONS:
        raise EvidenceFlywheelBoundaryError(
            f"historical replay allows at most {MAX_HISTORICAL_REPLAY_SESSIONS} sessions"
        )

    sessions: list[date] = []
    for raw in values:
        if isinstance(raw, datetime):
            raise EvidenceFlywheelBoundaryError("historical replay sessions must be dates, not datetimes")
        if isinstance(raw, date):
            session_date = raw
        elif isinstance(raw, str):
            try:
                session_date = date.fromisoformat(raw.strip())
            except ValueError as exc:
                raise EvidenceFlywheelBoundaryError(
                    "historical replay sessions must be ISO dates"
                ) from exc
        else:
            raise EvidenceFlywheelBoundaryError(
                "historical replay sessions must be ISO dates"
            )
        sessions.append(session_date)

    if len(set(sessions)) != len(sessions):
        raise EvidenceFlywheelBoundaryError("historical replay rejects duplicate sessions")
    if sessions != sorted(sessions):
        raise EvidenceFlywheelBoundaryError("historical replay sessions must be strictly ascending")

    latest_completed = resolve_latest_completed_session_fail_closed(
        "cn",
        current_time=reference_time,
    )
    if latest_completed is None:
        raise EvidenceFlywheelBoundaryError(
            "historical replay cannot prove the latest completed XSHG session"
        )
    for session_date in sessions:
        if session_date > latest_completed:
            raise EvidenceFlywheelBoundaryError("historical replay rejects future sessions")
        if resolve_historical_daily_bar_date("cn", session_date, "postmarket") != session_date:
            raise EvidenceFlywheelBoundaryError(
                f"historical replay requires an XSHG trading session: {session_date.isoformat()}"
            )
    return sessions


def _replay_postmarket_current_time(session_date: date) -> datetime:
    local = datetime.combine(
        session_date,
        _CN_REPLAY_POSTMARKET_TIME,
        tzinfo=_CN_REPLAY_TIMEZONE,
    )
    return local.astimezone(timezone.utc)


def _preexisting_nonzero_table_counts(
    counts: Mapping[str, int],
) -> Dict[str, int]:
    return {
        str(table_name): int(count)
        for table_name, count in counts.items()
        if int(count) != 0 and str(table_name) != "schema_migrations"
    }


def _require_fresh_table_counts(
    counts: Mapping[str, int],
    *,
    stage: str,
) -> None:
    preexisting_nonzero = _preexisting_nonzero_table_counts(counts)
    if preexisting_nonzero:
        tables = ",".join(sorted(preexisting_nonzero))
        raise EvidenceFlywheelRuntimeError(
            f"closed-world receipt requires a fresh isolated database before {stage}: {tables}"
        )


def _require_fresh_sqlite_file(path: Path) -> Dict[str, Any]:
    database_path = path.expanduser().resolve()
    sidecars = [
        Path(f"{database_path}{suffix}")
        for suffix in ("-wal", "-shm")
        if Path(f"{database_path}{suffix}").exists()
    ]
    if sidecars:
        raise EvidenceFlywheelRuntimeError(
            "closed-world receipt rejects preexisting SQLite WAL/SHM sidecars"
        )
    if not database_path.exists():
        return {
            "database_file_present": False,
            "database_bytes_before": 0,
            "table_counts_before_factory": {},
        }
    if not database_path.is_file():
        raise EvidenceFlywheelRuntimeError(
            "closed-world receipt database path must be a regular file"
        )
    database_bytes = database_path.stat().st_size
    if database_bytes == 0:
        return {
            "database_file_present": True,
            "database_bytes_before": 0,
            "table_counts_before_factory": {},
        }

    connection = None
    try:
        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
        )
        table_names = sorted(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        counts = {}
        for table_name in table_names:
            quoted_name = table_name.replace('"', '""')
            counts[table_name] = int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{quoted_name}"'
                ).fetchone()[0]
            )
    except sqlite3.Error as exc:
        raise EvidenceFlywheelRuntimeError(
            "closed-world receipt cannot inspect the SQLite database before Pipeline construction"
        ) from exc
    finally:
        if connection is not None:
            connection.close()

    _require_fresh_table_counts(counts, stage="Pipeline construction")
    return {
        "database_file_present": True,
        "database_bytes_before": database_bytes,
        "table_counts_before_factory": counts,
    }


def _database_table_counts(db_manager: Any) -> Dict[str, int]:
    from sqlalchemy import inspect, text

    engine = getattr(db_manager, "_engine", None)
    if engine is None:
        raise EvidenceFlywheelRuntimeError("closed-world receipt requires an initialized database")
    table_names = sorted(inspect(engine).get_table_names())
    with engine.connect() as connection:
        return {
            table_name: int(
                connection.execute(
                    text(f'SELECT COUNT(*) FROM "{table_name}"')
                ).scalar_one()
            )
            for table_name in table_names
        }


def _iso_text(value: Any) -> Optional[str]:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return None if value is None else str(value)


def _utc_iso_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _ledger_identity_snapshot(db_manager: Any, prediction_hash: str) -> Dict[str, Any]:
    from sqlalchemy import select

    from src.storage import PredictionLedgerRecord

    with db_manager.get_session() as session:
        row = session.execute(
            select(PredictionLedgerRecord)
            .where(PredictionLedgerRecord.prediction_hash == prediction_hash)
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            raise EvidenceFlywheelRuntimeError(
                "closed-world receipt cannot resolve the persisted Ledger row"
            )
        try:
            pit_reasons = json.loads(row.pit_ineligibility_json or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            raise EvidenceFlywheelRuntimeError(
                "closed-world receipt found invalid PIT ineligibility JSON"
            ) from exc
        if not isinstance(pit_reasons, list):
            raise EvidenceFlywheelRuntimeError(
                "closed-world receipt requires PIT ineligibility reasons to be a list"
            )
        try:
            eligibility_document = json.loads(row.strategy_eligibility_json or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise EvidenceFlywheelRuntimeError(
                "closed-world receipt found invalid strategy eligibility JSON"
            ) from exc
        if not isinstance(eligibility_document, dict):
            raise EvidenceFlywheelRuntimeError(
                "closed-world receipt requires strategy eligibility to be an object"
            )
        eligibility = build_strategy_eligibility_identity(
            eligibility_document,
            strategy_id=row.strategy_id,
        )
        for field in (
            "strategy_eligibility_version",
            "strategy_eligibility_state",
            "strategy_eligibility_hash",
            "strategy_eligibility_json",
        ):
            if getattr(row, field) != eligibility[field]:
                raise EvidenceFlywheelRuntimeError(
                    f"closed-world receipt found inconsistent {field}"
                )
        trace_identity = None
        if row.feature_schema_version == TRACE_FEATURE_SCHEMA_VERSION:
            try:
                payload = json.loads(row.evidence_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise EvidenceFlywheelRuntimeError(
                    "persisted trace/evidence identity mismatch"
                ) from exc
            if not isinstance(payload, dict):
                raise EvidenceFlywheelRuntimeError(
                    "persisted trace/evidence identity mismatch"
                )
            trace_identity = payload.get("trace_identity")
            manifest_hash = str(payload.get("manifest_hash") or "").strip().lower()
            trace_manifest_hash = (
                str(trace_identity.get("manifest_hash") or "").strip().lower()
                if isinstance(trace_identity, dict)
                else ""
            )
            try:
                expected_feature_schema_hash = traced_feature_schema_hash(manifest_hash)
            except ValueError:
                expected_feature_schema_hash = ""
            if (
                payload.get("schema_version") != TRACE_FEATURE_SCHEMA_VERSION
                or digest(payload) != row.evidence_hash
                or not isinstance(trace_identity, dict)
                or not _SHA64_RE.fullmatch(manifest_hash)
                or trace_manifest_hash != manifest_hash
                or str(row.feature_schema_hash or "") != expected_feature_schema_hash
                or not _SHA64_RE.fullmatch(
                    str(trace_identity.get("runtime_trace_hash") or "")
                )
            ):
                raise EvidenceFlywheelRuntimeError(
                    "persisted trace/evidence identity mismatch"
                )
        return {
            "id": row.id,
            "prediction_hash": row.prediction_hash,
            "schema_version": row.schema_version,
            "market": row.market,
            "stock_code": row.stock_code,
            "instrument_type": row.instrument_type,
            "decision_time": _utc_iso_text(row.decision_time),
            "decision_timezone": row.decision_timezone,
            "decision_phase": row.decision_phase,
            "session_date": _iso_text(row.session_date),
            "effective_daily_bar_date": _iso_text(row.effective_daily_bar_date),
            "outcome_label_anchor": _iso_text(row.outcome_label_anchor),
            "data_as_of": _iso_text(row.data_as_of),
            "available_at_max": _utc_iso_text(row.available_at_max),
            "strategy_id": row.strategy_id,
            "strategy_version": row.strategy_version,
            "factor_contract_version": row.factor_contract_version,
            "canonical_action": row.canonical_action,
            "canonical_evidence_state": row.canonical_evidence_state,
            "canonical_hard_veto": row.canonical_hard_veto,
            "horizon": row.horizon,
            "decision_profile": row.decision_profile,
            "trigger_source": row.trigger_source,
            "feature_schema_version": row.feature_schema_version,
            "feature_schema_hash": row.feature_schema_hash,
            "strategy_eligibility_version": eligibility[
                "strategy_eligibility_version"
            ],
            "strategy_eligibility_state": eligibility[
                "strategy_eligibility_state"
            ],
            "strategy_eligibility_hash": eligibility[
                "strategy_eligibility_hash"
            ],
            "strategy_eligibility_reason_codes": eligibility[
                "strategy_eligibility_reason_codes"
            ],
            "evidence_hash": row.evidence_hash,
            "evidence_traceability_identity": trace_identity,
            "code_sha": row.code_sha,
            "provider_identity": row.provider_identity,
            "adjustment_basis": row.adjustment_basis,
            "universe_snapshot_id": row.universe_snapshot_id,
            "asset_identity_hash": row.asset_identity_hash,
            "data_snapshot_identity": row.data_snapshot_identity,
            "selection_source": row.selection_source,
            "selection_context_hash": row.selection_context_hash,
            "recording_intent_hash": row.recording_intent_hash,
            "intended_cohort_id": row.intended_cohort_id,
            "pit_eligible": bool(row.pit_eligible),
            "pit_ineligibility_reasons": [str(item) for item in pit_reasons],
            "durability_state": row.durability_state,
        }


def _build_closed_world_database_receipt(
    *,
    db_manager: Any,
    before_counts: Mapping[str, int],
    prediction_hashes: Sequence[str],
    expected_record_count: int,
) -> Dict[str, Any]:
    _require_fresh_table_counts(
        before_counts,
        stage="post-run receipt validation",
    )

    after_counts = _database_table_counts(db_manager)
    all_tables = sorted(set(before_counts) | set(after_counts))
    deltas = {
        table_name: int(after_counts.get(table_name, 0))
        - int(before_counts.get(table_name, 0))
        for table_name in all_tables
    }
    negative_deltas = {
        table_name: delta for table_name, delta in deltas.items() if delta < 0
    }
    if negative_deltas:
        raise EvidenceFlywheelRuntimeError(
            "closed-world receipt observed an unexpected table-count decrease"
        )
    unexpected_nonzero = {
        table_name: delta
        for table_name, delta in deltas.items()
        if delta and table_name not in _CLOSED_WORLD_ALLOWED_NONZERO_DELTAS
    }
    if unexpected_nonzero:
        raise EvidenceFlywheelRuntimeError(
            "closed-world receipt observed a forbidden database write surface"
        )
    for table_name in _CLOSED_WORLD_EXACT_SINGLE_ROW_TABLES:
        if deltas.get(table_name, 0) != expected_record_count:
            raise EvidenceFlywheelRuntimeError(
                f"closed-world receipt requires {expected_record_count} new row(s) in {table_name}"
            )
    if deltas.get("stock_daily", 0) <= 0:
        raise EvidenceFlywheelRuntimeError(
            "closed-world receipt requires completed daily bars in the isolated database"
        )

    ledger_identities = [
        _ledger_identity_snapshot(db_manager, prediction_hash)
        for prediction_hash in prediction_hashes
    ]
    if len(ledger_identities) != expected_record_count:
        raise EvidenceFlywheelRuntimeError(
            "closed-world receipt did not resolve every persisted Ledger identity"
        )
    return {
        "fresh_isolated_database": True,
        "core_table_counts_after": {
            table_name: int(after_counts.get(table_name, 0))
            for table_name in _CLOSED_WORLD_CORE_TABLES
        },
        "nonzero_table_count_deltas": {
            table_name: delta for table_name, delta in deltas.items() if delta
        },
        "unexpected_nonzero_table_deltas": {},
        "ledger_identities": ledger_identities,
    }


def _database_file_identity(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise EvidenceFlywheelRuntimeError(
            "closed-world receipt database file is missing after database close"
        )
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            total += len(chunk)
            digest.update(chunk)
    return {
        "database_file_name": path.name,
        "database_bytes_after_close": total,
        "database_sha256_after_close": digest.hexdigest(),
        "sessions_closed_before_hash": True,
    }


def _resolve_database_file(config: Any) -> Path:
    value = str(getattr(config, "database_path", "") or "").strip()
    if not value:
        raise EvidenceFlywheelBoundaryError(
            "closed-world receipt requires an explicit SQLite database path"
        )
    return Path(value).expanduser().resolve()


def _require_fresh_product_report_surface() -> None:
    existing = sorted(
        path.name
        for path in _PRODUCT_REPORTS_DIR.glob("*.md")
        if path.is_file()
    )
    if existing:
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires a fresh local Markdown report surface"
        )


def _persisted_factor_snapshot(
    db_manager: Any,
    pipeline: Any,
    prediction_hash: str,
) -> tuple[Dict[str, Any], int]:
    from sqlalchemy import select

    from src.storage import PredictionLedgerRecord

    with db_manager.get_session() as session:
        ledger = session.execute(
            select(PredictionLedgerRecord)
            .where(PredictionLedgerRecord.prediction_hash == prediction_hash)
            .limit(1)
        ).scalar_one_or_none()
        if ledger is None:
            raise EvidenceFlywheelRuntimeError(
                "product receipt cannot resolve the persisted Ledger row"
            )
        history_id = int(ledger.analysis_history_id or 0)

    if history_id <= 0:
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires a persisted AnalysisHistory identity"
        )
    history = db_manager.get_analysis_history_by_id(history_id)
    factor = pipeline._factor_from_history_record(history)
    if not isinstance(factor, dict):
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires a valid persisted Product factor"
        )
    return factor, history_id


def _build_product_report_receipt(
    *,
    db_manager: Any,
    pipeline: Any,
    result: Any,
    prediction_hash: str,
) -> Dict[str, Any]:
    report_files = sorted(
        path
        for path in _PRODUCT_REPORTS_DIR.glob("*.md")
        if path.is_file()
    )
    if len(report_files) != 1:
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires exactly one local Markdown report"
        )
    report_file = report_files[0]
    report_bytes = report_file.read_bytes()
    if not report_bytes:
        raise EvidenceFlywheelRuntimeError(
            "product receipt found an empty local report"
        )

    persisted_factor, history_id = _persisted_factor_snapshot(
        db_manager,
        pipeline,
        prediction_hash,
    )
    dashboard = getattr(result, "dashboard", None)
    runtime_factor = (
        dashboard.get("factor_decision")
        if isinstance(dashboard, dict)
        else None
    )
    if not isinstance(runtime_factor, dict):
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires runtime factor_decision"
        )
    json_wire_fields = {
        "canonical_decision",
        "investor_brief",
        "evidence_traceability",
        "evidence_product_coverage",
    }
    for field in (
        "canonical_decision",
        "investor_brief",
        "evidence_traceability",
        "evidence_product_coverage",
    ):
        runtime_value = runtime_factor.get(field)
        persisted_value = persisted_factor.get(field)
        matches = (
            json_wire_equal(runtime_value, persisted_value)
            if field in json_wire_fields
            else runtime_value == persisted_value
        )
        if not matches:
            raise EvidenceFlywheelRuntimeError(
                f"product receipt found runtime/database drift in {field}"
            )

    runtime_fact_hash = pipeline._delivery_fact_hash(runtime_factor)
    persisted_fact_hash = pipeline._delivery_fact_hash(persisted_factor)
    if (
        not isinstance(runtime_fact_hash, str)
        or len(runtime_fact_hash) != 64
        or runtime_fact_hash != persisted_fact_hash
    ):
        raise EvidenceFlywheelRuntimeError(
            "product receipt found delivery fact identity drift"
        )

    brief = persisted_factor.get("investor_brief")
    if not isinstance(brief, Mapping):
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires investor_brief"
        )
    report_text = report_bytes.decode("utf-8")
    report_anchor_paths = []
    for field in ("one_line_conclusion", "fused_paragraph", "coverage_text"):
        value = str(brief.get(field) or "").strip()
        if not value or value not in report_text:
            raise EvidenceFlywheelRuntimeError(
                f"product receipt report is missing investor_brief.{field}"
            )
        report_anchor_paths.append(f"investor_brief.{field}")
    brief_coverage = brief.get("coverage")
    missing_timeframes = []
    if isinstance(brief_coverage, Mapping):
        missing_timeframes = [
            timeframe
            for timeframe in ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m")
            if str(brief_coverage.get(timeframe) or "MISSING")
            not in _PRODUCT_READY_STATES
        ]

    coverage = persisted_factor.get("evidence_product_coverage")
    if not isinstance(coverage, Mapping) or coverage.get("rendered") is not False:
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires semantic-only V2.5 coverage sidecar"
        )
    slots = coverage.get("slots")
    if not isinstance(slots, list) or not slots:
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires V2.5 semantic slot coverage"
        )
    dynamic_v25_receipt = {
        "schema_version": coverage.get("schema_version"),
        "receipt_hash": coverage.get("receipt_hash"),
        "baseline_sha256": coverage.get("baseline_sha256"),
        "rendered": False,
        "actual_render_consumer_proven": False,
        "reason": "LEGACY_OR_INCOMPLETE_WHOLE_PRODUCT_SCOPE",
    }
    whole_scope_gate = coverage.get("whole_product_scope_gate")
    if (
        isinstance(whole_scope_gate, Mapping)
        and whole_scope_gate.get("full_scope_accounted_pass") is True
    ):
        from src.services.v2_5_evidence_coverage import (
            render_v2_5_dynamic_no_send,
        )

        projected = render_v2_5_dynamic_no_send([result])
        dynamic_receipt = projected.get("receipt")
        if (
            not isinstance(dynamic_receipt, Mapping)
            or dynamic_receipt.get("rendered") is not True
            or dynamic_receipt.get("dynamic_fidelity_proven") is not True
            or dynamic_receipt.get("all_accepted_slot_roles_projected") is not True
            or dynamic_receipt.get("all_whole_scope_obligations_projected") is not True
            or dynamic_receipt.get("no_send") is not True
            or dynamic_receipt.get("delivery_authority") is not False
            or dynamic_receipt.get("storage_authority") is not False
            or dynamic_receipt.get("learning_authority") is not False
        ):
            raise EvidenceFlywheelRuntimeError(
                "product receipt requires complete dynamic V2.5 NO_SEND projection"
            )
        dynamic_v25_receipt = {
            "schema_version": dynamic_receipt.get("schema_version"),
            "receipt_hash": dynamic_receipt.get("receipt_hash"),
            "baseline_sha256": dynamic_receipt.get("baseline_sha256"),
            "output_sha256": dynamic_receipt.get("output_sha256"),
            "output_bytes": dynamic_receipt.get("output_bytes"),
            "frame_contract_hash": dynamic_receipt.get("frame_contract_hash"),
            "whole_scope_projection_hash": at(
                dynamic_receipt, "whole_scope_projection.projection_hash"
            ),
            "slot_projection_count": dynamic_receipt.get(
                "slot_projection_count"
            ),
            "rendered": True,
            "actual_render_consumer_proven": True,
            "all_input_facts_product_ready": dynamic_receipt.get(
                "all_input_facts_product_ready"
            ),
            "no_send": True,
        }
    trace = persisted_factor.get("evidence_traceability")
    if not isinstance(trace, Mapping):
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires persisted evidence traceability"
        )
    matrix = trace.get("timeframe_family_matrix")
    if not isinstance(matrix, Mapping):
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires persisted timeframe/family matrix"
        )
    cells = matrix.get("cells")
    matrix_hash = str(matrix.get("matrix_hash") or "")
    policy_hash = str(trace.get("method_window_policy_hash") or "")
    if (
        not isinstance(cells, list)
        or len(cells) != 56
        or len(matrix_hash) != 64
        or str(trace.get("timeframe_family_matrix_hash") or "") != matrix_hash
        or str(matrix.get("method_window_policy_hash") or "") != policy_hash
        or str(coverage.get("timeframe_family_matrix_hash") or "") != matrix_hash
        or str(coverage.get("method_window_policy_hash") or "") != policy_hash
    ):
        raise EvidenceFlywheelRuntimeError(
            "product receipt found timeframe/family matrix identity drift"
        )
    universe = trace.get("complete_research_universe")
    if not isinstance(universe, Mapping):
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires complete research universe"
        )
    universe_hash = str(universe.get("universe_hash") or "")
    universe_body = {key: value for key, value in universe.items() if key != "universe_hash"}
    universe_counts = universe.get("counts")
    if (
        len(universe_hash) != 64
        or digest(universe_body) != universe_hash
        or str(trace.get("complete_research_universe_hash") or "") != universe_hash
        or str(coverage.get("complete_research_universe_hash") or "") != universe_hash
        or not isinstance(universe_counts, Mapping)
    ):
        raise EvidenceFlywheelRuntimeError(
            "product receipt found complete research universe identity drift"
        )
    ledger_identity = _ledger_identity_snapshot(db_manager, prediction_hash)
    ledger_trace_identity = ledger_identity.get("evidence_traceability_identity")
    if not isinstance(ledger_trace_identity, Mapping):
        raise EvidenceFlywheelRuntimeError(
            "product receipt requires Ledger trace identity"
        )
    if (
        str(ledger_trace_identity.get("method_window_policy_hash") or "") != policy_hash
        or str(ledger_trace_identity.get("timeframe_family_matrix_hash") or "") != matrix_hash
        or str(ledger_trace_identity.get("complete_research_universe_hash") or "") != universe_hash
    ):
        raise EvidenceFlywheelRuntimeError(
            "product receipt found Ledger matrix identity drift"
        )
    slot_state_counts: Dict[str, int] = {}
    for slot in slots:
        if isinstance(slot, Mapping):
            state = str(slot.get("state") or "UNKNOWN")
            slot_state_counts[state] = slot_state_counts.get(state, 0) + 1

    return {
        "schema_version": "product-report-path-receipt-v1",
        "status": "PASS",
        "analysis_history_id": history_id,
        "prediction_hash": prediction_hash,
        "report_consumer": "StockAnalysisPipeline._generate_aggregate_report",
        "report_file_name": report_file.name,
        "report_bytes": len(report_bytes),
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "delivery_fact_hash": runtime_fact_hash,
        "investor_brief_hash": digest(brief),
        "report_anchor_paths": report_anchor_paths,
        "runtime_database_binding": "EXACT_FOR_CANONICAL_BRIEF_AND_COVERAGE",
        "missing_timeframes": missing_timeframes,
        "timeframe_family_matrix": {
            "cell_count": len(cells),
            "matrix_hash": matrix_hash,
            "method_window_policy_hash": policy_hash,
        },
        "complete_research_universe": {
            "schema_version": universe.get("schema_version"),
            "universe_hash": universe_hash,
            "requirement_count": universe_counts.get("requirements"),
            "method_count": universe_counts.get("methods"),
            "strategy_binding_count": universe_counts.get("strategy_bindings"),
        },
        "v25_semantic_coverage": {
            **dynamic_v25_receipt,
            "slot_state_counts": slot_state_counts,
        },
    }


def _reset_default_runtime_state() -> None:
    from src.config import Config
    from src.storage import DatabaseManager

    DatabaseManager.reset_instance()
    Config.reset_instance()


def _write_receipt_file(path_value: str, receipt: Mapping[str, Any]) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(dict(receipt), ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@contextmanager
def _bounded_recording_config(config: Any) -> Iterator[None]:
    overrides = {
        "single_stock_notify": False,
        "merge_email_notification": False,
        "report_type": "simple",
        "report_language": "zh",
        "report_integrity_retry": 0,
        "agent_mode": False,
        "agent_skills": [],
        "analysis_delay": 0,
        "market_review_enabled": False,
        "daily_market_context_enabled": False,
        "enable_realtime_quote": False,
        "enable_realtime_technical_indicators": False,
        "prefetch_realtime_quotes": False,
        "enable_chip_distribution": False,
        "enable_fundamental_pipeline": False,
        "report_integrity_enabled": False,
        "searxng_public_instances_enabled": False,
    }
    previous: Dict[str, Any] = {}
    for name, value in overrides.items():
        if hasattr(config, name):
            previous[name] = getattr(config, name)
            setattr(config, name, value)
    try:
        yield
    finally:
        for name, value in previous.items():
            setattr(config, name, value)


def _default_pipeline_factory(**kwargs: Any) -> Any:
    from src.core.pipeline import StockAnalysisPipeline

    return StockAnalysisPipeline(**kwargs)


def record_canonical_run(
    *,
    stock_codes: Sequence[str],
    code_sha: str,
    config: Optional[Any] = None,
    pipeline_factory: Optional[Callable[..., Any]] = None,
    current_time: Optional[datetime] = None,
    require_single_stock: bool = False,
    closed_world_database_receipt: bool = False,
    receipt_only: bool = False,
    product_report_receipt: bool = False,
    intended_cohort_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one bounded, notification-suppressed canonical analysis into Ledger."""
    codes = _validated_codes(
        stock_codes,
        require_single_stock=require_single_stock,
    )
    bound_code_sha = _require_sha(code_sha, length=40, field="code_sha")
    bound_intended_cohort_id = (
        _require_sha(
            intended_cohort_id,
            length=64,
            field="intended_cohort_id",
        )
        if intended_cohort_id is not None
        else None
    )
    if product_report_receipt and (
        not closed_world_database_receipt or receipt_only
    ):
        raise EvidenceFlywheelBoundaryError(
            "product report receipt requires closed-world database mode and receipt_only=false"
        )
    if product_report_receipt:
        _require_fresh_product_report_surface()
    if config is None:
        from src.config import get_config

        config = get_config()
    if closed_world_database_receipt:
        _require_fresh_sqlite_file(_resolve_database_file(config))
    factory = pipeline_factory or _default_pipeline_factory
    selection_context = build_specified_codes_selection_context(
        raw_selection_source="evidence-flywheel-runtime",
        query_source="cli",
    )
    query_id = f"evidence-flywheel-{uuid.uuid4().hex}"
    database_before: Optional[Dict[str, int]] = None
    db_manager = None

    with _bounded_recording_config(config):
        pipeline = factory(
            config=config,
            max_workers=1,
            query_id=query_id,
            query_source="evidence_flywheel_runtime",
            save_context_snapshot=True,
            daily_market_context_enabled=False,
            daily_market_context_allow_generate=False,
            p0_bounded_trial=True,
            p0_stock_codes=codes,
            p0_suppress_notification=True,
            p0_receipt_only=bool(receipt_only),
            p0_model_request_budget=ZERO_EXTERNAL_MODEL_REQUEST_BUDGET,
            research_selection_context=selection_context,
            research_code_sha=bound_code_sha,
        )
        if bound_intended_cohort_id is not None:
            setattr(
                pipeline,
                "research_intended_cohort_id",
                bound_intended_cohort_id,
            )
        if closed_world_database_receipt:
            db_manager = getattr(pipeline, "db", None)
            if db_manager is None:
                raise EvidenceFlywheelRuntimeError(
                    "closed-world receipt requires the native Pipeline database owner"
                )
            database_before = _database_table_counts(db_manager)
            _require_fresh_table_counts(
                database_before,
                stage="Pipeline run",
            )
        analyzer = getattr(pipeline, "analyzer", None)
        observed_model_request_budget = int(
            getattr(analyzer, "p0_model_request_budget", -1)
        )
        if observed_model_request_budget != ZERO_EXTERNAL_MODEL_REQUEST_BUDGET:
            raise EvidenceFlywheelRuntimeError(
                "Evidence Flywheel record did not bind the zero external-model request budget"
            )
        results = pipeline.run(
            stock_codes=codes,
            dry_run=False,
            send_notification=False,
            merge_notification=False,
            current_time=current_time,
        )
        observed_model_request_count = int(
            getattr(analyzer, "p0_model_request_count", -1)
        )
        if observed_model_request_count != 0:
            raise EvidenceFlywheelRuntimeError(
                "Evidence Flywheel record observed an external model request"
            )

    if len(results) != len(codes):
        raise EvidenceFlywheelRuntimeError(
            "bounded record phase requires every target to return exactly one result"
        )

    receipts = []
    seen_hashes = set()
    for result in results:
        if result is None or bool(getattr(result, "success", False)) is not True:
            raise EvidenceFlywheelRuntimeError("bounded record phase contains an unsuccessful result")
        raw_receipt = getattr(result, "prediction_ledger_receipt", None)
        if not isinstance(raw_receipt, Mapping):
            raise EvidenceFlywheelRuntimeError("canonical result is missing a Prediction Ledger receipt")
        validated_receipt = _validated_ledger_receipt(
            raw_receipt,
            stock_code=str(getattr(result, "code", "") or ""),
        )
        prediction_hash = validated_receipt["prediction_hash"]
        if prediction_hash in seen_hashes:
            raise EvidenceFlywheelRuntimeError("duplicate prediction hash in bounded record receipt")
        seen_hashes.add(prediction_hash)
        receipts.append(validated_receipt)

    cohort_ids = {item["intended_cohort_id"] for item in receipts}
    if len(cohort_ids) != 1:
        raise EvidenceFlywheelRuntimeError(
            "bounded record phase requires one intended cohort identity"
        )
    recorded_cohort_id = next(iter(cohort_ids))
    if (
        bound_intended_cohort_id is not None
        and recorded_cohort_id != bound_intended_cohort_id
    ):
        raise EvidenceFlywheelRuntimeError(
            "bounded record phase intended cohort identity mismatch"
        )

    ledger_identities = []
    if receipt_only:
        receipt_db = getattr(pipeline, "db", None)
        if receipt_db is None:
            raise EvidenceFlywheelRuntimeError(
                "receipt-only record requires the native Pipeline database owner"
            )
        for item in receipts:
            identity = _ledger_identity_snapshot(receipt_db, item["prediction_hash"])
            if identity.get("code_sha") != bound_code_sha:
                raise EvidenceFlywheelRuntimeError(
                    "receipt-only record found Ledger code_sha drift"
                )
            if identity.get("selection_source") != "SPECIFIED_CODES":
                raise EvidenceFlywheelRuntimeError(
                    "receipt-only record requires SPECIFIED_CODES Ledger identity"
                )
            ledger_identities.append(identity)

    response = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "phase": "record",
        "status": "RECORDED",
        "query_id": query_id,
        "code_sha": bound_code_sha,
        "intended_cohort_id": recorded_cohort_id,
        "record_count": len(receipts),
        "notification_suppressed": True,
        "external_durability": "NOT_REQUESTED",
        "training_requested": False,
        "model_request_budget": observed_model_request_budget,
        "model_request_count": observed_model_request_count,
        "ledger_receipts": receipts,
    }
    if receipt_only:
        response["ledger_identities"] = ledger_identities
        response["artifact_policy"] = {
            "receipt_only": True,
            "report_files_created": False,
            "database_uploaded": False,
            "reports_uploaded": False,
            "logs_uploaded": False,
        }
        response["route_boundaries"] = {
            "search_news": "DISABLED_BY_P0_BOUNDED_TRIAL",
            "agent": "DISABLED",
            "notification": "SUPPRESSED",
            "report_projection": "SUPPRESSED",
            "outcome": "NOT_REQUESTED",
            "pit_manifest": "NOT_REQUESTED",
            "training": "NOT_REQUESTED",
        }
    if product_report_receipt:
        receipt_db = getattr(pipeline, "db", None)
        if receipt_db is None or len(results) != 1 or len(receipts) != 1:
            raise EvidenceFlywheelRuntimeError(
                "product receipt requires one native Pipeline result and database owner"
            )
        response["product_report_receipt"] = _build_product_report_receipt(
            db_manager=receipt_db,
            pipeline=pipeline,
            result=results[0],
            prediction_hash=receipts[0]["prediction_hash"],
        )

    if closed_world_database_receipt:
        if db_manager is None or database_before is None:
            raise EvidenceFlywheelRuntimeError(
                "closed-world receipt database state was not initialized"
            )
        response["database_receipt"] = _build_closed_world_database_receipt(
            db_manager=db_manager,
            before_counts=database_before,
            prediction_hashes=[item["prediction_hash"] for item in receipts],
            expected_record_count=len(codes),
        )
        response["artifact_policy"] = {
            "receipt_only": bool(receipt_only),
            "report_files_created": not bool(receipt_only),
            "database_uploaded": False,
            "reports_uploaded": False,
            "logs_uploaded": False,
        }
        response["route_boundaries"] = {
            "search_news": "DISABLED_BY_P0_BOUNDED_TRIAL",
            "agent": "DISABLED",
            "notification": "SUPPRESSED",
            "report_projection": (
                "SUPPRESSED" if receipt_only else "LOCAL_AUDIT_FILE"
            ),
            "external_durability": "NOT_REQUESTED",
            "training": "NOT_REQUESTED",
        }
    return response


def replay_specified_codes_daily_sessions(
    *,
    stock_code: str,
    session_dates: Sequence[Any],
    code_sha: str,
    config: Optional[Any] = None,
    pipeline_factory: Optional[Callable[..., Any]] = None,
    reference_time: Optional[datetime] = None,
    record_runner: Optional[Callable[..., Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Replay explicit XSHG postmarket sessions through the existing bounded Pipeline."""
    code = _validated_codes([stock_code], require_single_stock=True)[0]
    bound_code_sha = _require_sha(code_sha, length=40, field="code_sha")
    sessions = _validated_replay_sessions(
        session_dates,
        reference_time=reference_time,
    )
    replay_cohort_id = sha256_payload(
        {
            "schema_version": "evidence-flywheel-replay-cohort-v1",
            "stock_code": code,
            "session_dates": [item.isoformat() for item in sessions],
            "code_sha": bound_code_sha,
            "selection_source": "SPECIFIED_CODES",
        }
    )
    if config is None:
        from src.config import get_config

        config = get_config()
    _require_fresh_sqlite_file(_resolve_database_file(config))
    runner = record_runner or record_canonical_run

    session_receipts = []
    for session_date in sessions:
        current_time = _replay_postmarket_current_time(session_date)
        receipt = runner(
            stock_codes=[code],
            code_sha=bound_code_sha,
            config=config,
            pipeline_factory=pipeline_factory,
            current_time=current_time,
            require_single_stock=True,
            closed_world_database_receipt=False,
            receipt_only=True,
            intended_cohort_id=replay_cohort_id,
        )
        if not isinstance(receipt, Mapping):
            raise EvidenceFlywheelRuntimeError("historical replay record returned no receipt")
        if receipt.get("phase") != "record" or receipt.get("status") != "RECORDED":
            raise EvidenceFlywheelRuntimeError("historical replay record phase did not complete")
        if receipt.get("record_count") != 1:
            raise EvidenceFlywheelRuntimeError("historical replay requires exactly one Ledger row per session")
        if receipt.get("intended_cohort_id") != replay_cohort_id:
            raise EvidenceFlywheelRuntimeError(
                "historical replay intended cohort identity mismatch"
            )
        if receipt.get("notification_suppressed") is not True:
            raise EvidenceFlywheelRuntimeError("historical replay must suppress notification")
        if receipt.get("model_request_count") != 0 or receipt.get("training_requested") is not False:
            raise EvidenceFlywheelRuntimeError("historical replay forbids model or training effects")
        artifact_policy = receipt.get("artifact_policy")
        if not isinstance(artifact_policy, Mapping) or artifact_policy.get("receipt_only") is not True:
            raise EvidenceFlywheelRuntimeError("historical replay requires receipt-only artifact policy")
        if artifact_policy.get("report_files_created") is not False:
            raise EvidenceFlywheelRuntimeError("historical replay forbids local report artifacts")

        ledger_receipts = receipt.get("ledger_receipts")
        ledger_identities = receipt.get("ledger_identities")
        if not isinstance(ledger_receipts, list) or len(ledger_receipts) != 1:
            raise EvidenceFlywheelRuntimeError("historical replay requires one Ledger receipt")
        if not isinstance(ledger_identities, list) or len(ledger_identities) != 1:
            raise EvidenceFlywheelRuntimeError("historical replay requires one persisted Ledger identity")
        ledger_receipt = dict(ledger_receipts[0])
        ledger_identity = dict(ledger_identities[0])
        session_text = session_date.isoformat()
        if ledger_identity.get("stock_code") != code:
            raise EvidenceFlywheelRuntimeError("historical replay Ledger stock identity mismatch")
        if ledger_identity.get("code_sha") != bound_code_sha:
            raise EvidenceFlywheelRuntimeError("historical replay Ledger code_sha mismatch")
        if ledger_identity.get("selection_source") != "SPECIFIED_CODES":
            raise EvidenceFlywheelRuntimeError("historical replay selection source drift")
        if ledger_receipt.get("intended_cohort_id") != replay_cohort_id:
            raise EvidenceFlywheelRuntimeError(
                "historical replay Ledger receipt cohort mismatch"
            )
        if ledger_identity.get("intended_cohort_id") != replay_cohort_id:
            raise EvidenceFlywheelRuntimeError(
                "historical replay persisted Ledger cohort mismatch"
            )
        if (
            ledger_identity.get("strategy_id") == "stock_trend_quality_pullback_v1"
            and ledger_identity.get("strategy_eligibility_state") == "ELIGIBLE"
        ):
            raise EvidenceFlywheelRuntimeError(
                "daily-only historical replay cannot claim the exact stock strategy is ELIGIBLE without its 30m hard trigger"
            )
        if ledger_identity.get("decision_phase") != "postmarket":
            raise EvidenceFlywheelRuntimeError("historical replay Ledger phase mismatch")
        if ledger_identity.get("session_date") != session_text:
            raise EvidenceFlywheelRuntimeError("historical replay Ledger session mismatch")
        if ledger_identity.get("effective_daily_bar_date") != session_text:
            raise EvidenceFlywheelRuntimeError("historical replay effective daily bar mismatch")
        if ledger_receipt.get("session_date") != session_text:
            raise EvidenceFlywheelRuntimeError("historical replay receipt session mismatch")
        if ledger_receipt.get("effective_daily_bar_date") != session_text:
            raise EvidenceFlywheelRuntimeError("historical replay receipt daily bar mismatch")

        session_receipts.append(
            {
                "requested_session_date": session_text,
                "current_time_utc": current_time.isoformat().replace("+00:00", "Z"),
                "prediction_hash": ledger_receipt.get("prediction_hash"),
                "pit_eligible": ledger_receipt.get("pit_eligible"),
                "pit_ineligibility_reasons": list(
                    ledger_receipt.get("pit_ineligibility_reasons") or []
                ),
                "strategy_eligibility_state": ledger_receipt.get(
                    "strategy_eligibility_state"
                ),
                "strategy_eligibility_reason_codes": list(
                    ledger_receipt.get("strategy_eligibility_reason_codes") or []
                ),
                "ledger_identity": ledger_identity,
            }
        )

    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "phase": "historical-replay-record",
        "status": "REPLAY_RECORDED",
        "stock_code": code,
        "code_sha": bound_code_sha,
        "selection_source": "SPECIFIED_CODES",
        "intended_cohort_id": replay_cohort_id,
        "session_count": len(session_receipts),
        "session_dates": [item["requested_session_date"] for item in session_receipts],
        "notification_suppressed": True,
        "model_request_budget": ZERO_EXTERNAL_MODEL_REQUEST_BUDGET,
        "model_request_count": 0,
        "training_requested": False,
        "outcome_requested": False,
        "pit_manifest_requested": False,
        "artifact_policy": {
            "receipt_only": True,
            "report_files_created": False,
        },
        "session_receipts": session_receipts,
    }


def evaluate_prediction_outcome(
    *,
    prediction_hash: str,
    cost_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    correction_reason: Optional[str] = None,
    service: Optional[Any] = None,
) -> Dict[str, Any]:
    """Evaluate one already-recorded prediction without changing its identity."""
    bound_hash = _require_sha(prediction_hash, length=64, field="prediction_hash")
    if not isinstance(cost_identity, Mapping) or not isinstance(execution_identity, Mapping):
        raise EvidenceFlywheelBoundaryError("cost and execution identities must be JSON objects")
    if service is None:
        from src.services.prediction_outcome_service import PredictionOutcomeService

        service = PredictionOutcomeService()
    outcome = service.evaluate_prediction(
        prediction_hash=bound_hash,
        cost_identity=dict(cost_identity),
        execution_identity=dict(execution_identity),
        correction_reason=correction_reason,
    )
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "phase": "evaluate-outcome",
        "status": str(outcome.get("status") or "UNKNOWN"),
        "prediction_hash": bound_hash,
        "external_durability": "NOT_REQUESTED",
        "training_requested": False,
        "outcome": dict(outcome),
    }


def build_pit_manifest_receipt(
    *,
    cost_identity: Mapping[str, Any],
    intended_cohort_id: Optional[str] = None,
    service: Optional[Any] = None,
) -> Dict[str, Any]:
    """Freeze one explicitly non-training PIT manifest receipt."""
    if not isinstance(cost_identity, Mapping):
        raise EvidenceFlywheelBoundaryError("cost_identity must be a JSON object")
    from src.services.prediction_outcome_service import PredictionOutcomeService

    normalized_cost = PredictionOutcomeService.normalize_cost_identity(cost_identity)
    cost_identity_hash = sha256_payload(normalized_cost)
    if service is None:
        from src.services.pit_dataset_service import PITDatasetService

        service = PITDatasetService()
    result = service.build_manifest(
        cost_identity_hash=cost_identity_hash,
        cost_identity_approved=False,
        durable_references_admitted=False,
        execution_realism_approved=False,
        intended_cohort_id=intended_cohort_id,
    )
    if result.get("training_admission") != "BLOCKED":
        raise EvidenceFlywheelRuntimeError(
            "minimum evidence-flywheel slice must not admit model training"
        )
    manifest = result.get("manifest")
    if not isinstance(manifest, Mapping):
        raise EvidenceFlywheelRuntimeError("PIT manifest service returned no manifest")
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "phase": "build-manifest",
        "status": "MANIFEST_FROZEN",
        "dataset_hash": result.get("dataset_hash"),
        "disposition": result.get("disposition"),
        "cost_identity_hash": cost_identity_hash,
        "intended_cohort_id": manifest.get("intended_cohort_id"),
        "recording_coverage_state": manifest.get("recording_coverage_state"),
        "final_test_state": result.get("final_test_state"),
        "training_admission": result.get("training_admission"),
        "training_admission_reasons": list(
            result.get("training_admission_reasons") or []
        ),
        "counts": dict(manifest.get("counts") or {}),
        "session_boundaries": dict(manifest.get("session_boundaries") or {}),
        "external_durability": "NOT_REQUESTED",
        "training_requested": False,
    }


def _gate6_exact_hash(value: Any, *, field: str, length: int = 64) -> str:
    return _require_sha(value, length=length, field=field)


def _gate6_projection_rows(
    value: Any,
    *,
    kind: str,
    expected_count: int,
) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise EvidenceFlywheelBoundaryError(f"Gate5 {kind} projections invalid")
    rows = tuple(dict(row) for row in value if isinstance(row, Mapping))
    if len(rows) != expected_count or len(rows) != len(value):
        raise EvidenceFlywheelBoundaryError(f"Gate5 {kind} projection count mismatch")
    identifiers = [str(row.get("id") or "") for row in rows]
    if any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
        raise EvidenceFlywheelBoundaryError(f"Gate5 {kind} projection identity invalid")
    for row in rows:
        locations = tuple(row.get("location_ids") or ())
        if len(locations) != 1 or not str(locations[0]).startswith("block-"):
            raise EvidenceFlywheelBoundaryError(
                f"Gate5 {kind} projection location invalid"
            )
        _gate6_exact_hash(
            row.get("projection_sha256"),
            field=f"Gate5 {kind} projection_sha256",
        )
    if len({str(row["location_ids"][0]) for row in rows}) != len(rows):
        raise EvidenceFlywheelBoundaryError(
            f"Gate5 {kind} projection location duplicate"
        )
    return rows


def _gate6_validate_gate5_receipt(receipt: Mapping[str, Any]) -> dict:
    if not isinstance(receipt, Mapping):
        raise EvidenceFlywheelBoundaryError("Gate6 requires a Gate5 receipt object")
    body = dict(receipt)
    receipt_hash = body.pop("receipt_hash", None)
    if receipt_hash is not None:
        bound_hash = _gate6_exact_hash(
            receipt_hash,
            field="Gate5 receipt_hash",
        )
        if digest(body) != bound_hash:
            raise EvidenceFlywheelBoundaryError("Gate5 receipt hash mismatch")
    required = {
        "schema_version": "v25-dynamic-no-send-v1",
        "authority": "ORIGINAL_GOLDEN_TEXT_NODE_PROJECTION_NO_SEND",
        "baseline_sha256": BASELINE_SHA256,
        "rendered": True,
        "dynamic_product_projection_admitted": True,
        "dynamic_fidelity_proven": True,
        "all_accepted_slot_roles_projected": True,
        "all_whole_scope_obligations_projected": True,
        "no_send": True,
        "delivery_authority": False,
        "storage_authority": False,
        "learning_authority": False,
        "model_calls": 0,
        "network_calls": 0,
        "database_writes": 0,
    }
    for key, expected in required.items():
        if receipt.get(key) != expected:
            raise EvidenceFlywheelBoundaryError(
                f"Gate5 receipt invariant mismatch: {key}"
            )
    _gate6_exact_hash(receipt.get("output_sha256"), field="Gate5 output_sha256")
    frame_hash = _gate6_exact_hash(
        receipt.get("frame_contract_hash"),
        field="Gate5 frame_contract_hash",
    )
    frame = receipt.get("frame_contract")
    if not isinstance(frame, Mapping) or digest(frame) != frame_hash:
        raise EvidenceFlywheelBoundaryError("Gate5 frame contract hash mismatch")
    card_ids = tuple(str(item) for item in frame.get("card_ids") or ())
    cycle_ids = tuple(str(item) for item in frame.get("cycle_ids") or ())
    if (
        frame.get("mail_count") != 3
        or len(card_ids) != 10
        or len(set(card_ids)) != 10
        or cycle_ids != tuple(f"cycle-{index:02d}" for index in range(84))
    ):
        raise EvidenceFlywheelBoundaryError("Gate5 frame contract incomplete")
    _gate6_exact_hash(
        frame.get("fixed_region_sha256"),
        field="Gate5 fixed_region_sha256",
    )

    scope = receipt.get("whole_scope_projection")
    if not isinstance(scope, Mapping):
        raise EvidenceFlywheelBoundaryError("Gate5 whole-scope projection missing")
    projection_hash = _gate6_exact_hash(
        scope.get("projection_hash"),
        field="Gate5 whole-scope projection_hash",
    )
    scope_body = dict(scope)
    scope_body.pop("projection_hash", None)
    if digest(scope_body) != projection_hash:
        raise EvidenceFlywheelBoundaryError(
            "Gate5 whole-scope projection hash mismatch"
        )
    if tuple(scope.get("timeframes") or ()) != TIMEFRAMES:
        raise EvidenceFlywheelBoundaryError("Gate5 timeframe scope mismatch")

    requirement_rows = _gate6_projection_rows(
        scope.get("requirement_projections"),
        kind="requirement",
        expected_count=len(ACCEPTED_REQUIREMENT_ROLES),
    )
    if frozenset(
        (row["id"], str(row.get("role") or ""))
        for row in requirement_rows
    ) != ACCEPTED_REQUIREMENT_ROLES:
        raise EvidenceFlywheelBoundaryError("Gate5 requirement projection mismatch")
    if tuple(row["id"] for row in requirement_rows) != tuple(
        sorted(identifier for identifier, _role in ACCEPTED_REQUIREMENT_ROLES)
    ):
        raise EvidenceFlywheelBoundaryError(
            "Gate5 requirement projection order mismatch"
        )

    data_rows = _gate6_projection_rows(
        scope.get("data_object_projections"),
        kind="data-object",
        expected_count=76,
    )
    if Counter(str(row.get("domain") or "") for row in data_rows) != Counter(
        ACCEPTED_DATA_OBJECT_DOMAIN_COUNTS
    ):
        raise EvidenceFlywheelBoundaryError("Gate5 data-domain projection mismatch")
    if scope.get("data_object_catalog_binding_sha256") != (
        ACCEPTED_DATA_OBJECT_SUBSET_SHA256
    ):
        raise EvidenceFlywheelBoundaryError(
            "Gate5 data-object catalog binding mismatch"
        )
    observed_catalog_hash = hashlib.sha256(
        json.dumps(
            [
                (
                    row["id"], row.get("domain"), row.get("fact"),
                    row.get("native_frequency"), row.get("consumer"),
                )
                for row in data_rows
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if observed_catalog_hash != ACCEPTED_DATA_OBJECT_SUBSET_SHA256:
        raise EvidenceFlywheelBoundaryError(
            "Gate5 data-object semantic binding mismatch"
        )

    valuation_rows = _gate6_projection_rows(
        scope.get("valuation_route_projections"),
        kind="valuation-route",
        expected_count=len(ACCEPTED_VALUATION_ROUTE_IDS),
    )
    if frozenset(row["id"] for row in valuation_rows) != ACCEPTED_VALUATION_ROUTE_IDS:
        raise EvidenceFlywheelBoundaryError("Gate5 valuation projection mismatch")

    atomic_rows = _gate6_projection_rows(
        scope.get("atomic_subfield_projections"),
        kind="atomic-subfield",
        expected_count=len(ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS),
    )
    if frozenset(row["id"] for row in atomic_rows) != ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS:
        raise EvidenceFlywheelBoundaryError("Gate5 atomic projection mismatch")

    scope_locations = [
        str(row["location_ids"][0])
        for rows in (requirement_rows, data_rows, valuation_rows, atomic_rows)
        for row in rows
    ]
    if len(scope_locations) != len(set(scope_locations)):
        raise EvidenceFlywheelBoundaryError("Gate5 whole-scope locations overlap")

    slots = receipt.get("slot_projections")
    if isinstance(slots, (str, bytes)) or not isinstance(slots, Sequence):
        raise EvidenceFlywheelBoundaryError("Gate5 slot projections invalid")
    slot_rows = tuple(dict(row) for row in slots if isinstance(row, Mapping))
    if len(slot_rows) != 270 or len(slot_rows) != len(slots):
        raise EvidenceFlywheelBoundaryError("Gate5 slot projection count mismatch")
    for card_id in card_ids:
        card_slots = {
            str(row.get("slot_id") or "")
            for row in slot_rows
            if str(row.get("card_id") or "") == card_id
        }
        if card_slots != ACCEPTED_PRODUCT_SLOT_IDS:
            raise EvidenceFlywheelBoundaryError("Gate5 slot projection set mismatch")
    if any(
        not tuple(row.get("location_ids") or ())
        or str(row.get("projection_state") or "")
        not in {"RENDERED_EVIDENCE", "RENDERED_MISSINGNESS"}
        for row in slot_rows
    ):
        raise EvidenceFlywheelBoundaryError("Gate5 slot projection material invalid")

    results = receipt.get("results")
    if not isinstance(results, list):
        raise EvidenceFlywheelBoundaryError("Gate5 result projection invalid")
    product_ready = receipt.get("all_input_facts_product_ready") is True
    if product_ready:
        if (
            not results
            or any(
                not isinstance(row, Mapping)
                or row.get("fact_mode") != "ADMITTED"
                or row.get("full_domain_product_ready") is not True
                for row in results
            )
            or tuple(scope.get("atomic_subfields_unresolved") or ())
            or any(row.get("state") != "BOUND" for row in atomic_rows)
            or any(
                str(row.get("state") or "") not in {"READY", "NOT_APPLICABLE"}
                for row in data_rows
                if str(row.get("domain") or "") != "RECORD"
            )
        ):
            raise EvidenceFlywheelBoundaryError(
                "Gate5 Product-ready claim is not supported by projections"
            )
    return dict(receipt)


def _gate6_validate_recording(receipt: Mapping[str, Any]) -> dict:
    if not isinstance(receipt, Mapping):
        raise EvidenceFlywheelBoundaryError("recording receipt must be an object")
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or receipt.get("phase") != "record"
        or receipt.get("status") != "RECORDED"
        or receipt.get("notification_suppressed") is not True
        or receipt.get("training_requested") is not False
        or receipt.get("model_request_count") != 0
    ):
        raise EvidenceFlywheelBoundaryError("recording receipt boundary mismatch")
    rows = receipt.get("ledger_receipts")
    count = receipt.get("record_count")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        or not isinstance(rows, list)
        or len(rows) != count
    ):
        raise EvidenceFlywheelBoundaryError("recording receipt count mismatch")
    prediction_hashes = []
    pit_eligible = 0
    for row in rows:
        if not isinstance(row, Mapping) or row.get("status") != "RECORDED":
            raise EvidenceFlywheelBoundaryError("recording Ledger receipt invalid")
        prediction_hashes.append(
            _gate6_exact_hash(
                row.get("prediction_hash"),
                field="recording prediction_hash",
            )
        )
        if row.get("pit_eligible") is True:
            pit_eligible += 1
        elif row.get("pit_eligible") is not False:
            raise EvidenceFlywheelBoundaryError(
                "recording pit_eligible must be boolean"
            )
    if len(set(prediction_hashes)) != len(prediction_hashes):
        raise EvidenceFlywheelBoundaryError("recording prediction hash duplicate")
    return {
        "record_count": count,
        "prediction_hashes": tuple(prediction_hashes),
        "pit_eligible_count": pit_eligible,
        "intended_cohort_id": receipt.get("intended_cohort_id"),
    }


def _gate6_validate_outcomes(
    receipts: Sequence[Mapping[str, Any]],
    prediction_hashes: Sequence[str],
) -> dict:
    if isinstance(receipts, (str, bytes)) or not isinstance(receipts, Sequence):
        raise EvidenceFlywheelBoundaryError("outcome receipts must be a sequence")
    by_hash: dict[str, Mapping[str, Any]] = {}
    for receipt in receipts:
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION
            or receipt.get("phase") != "evaluate-outcome"
            or receipt.get("training_requested") is not False
        ):
            raise EvidenceFlywheelBoundaryError("outcome receipt boundary mismatch")
        prediction_hash = _gate6_exact_hash(
            receipt.get("prediction_hash"),
            field="outcome prediction_hash",
        )
        if prediction_hash in by_hash:
            raise EvidenceFlywheelBoundaryError("duplicate outcome receipt")
        by_hash[prediction_hash] = receipt
    expected = set(prediction_hashes)
    if set(by_hash) != expected:
        raise EvidenceFlywheelBoundaryError("outcome receipt set mismatch")
    labelable = 0
    unmatured = 0
    unlabelable = 0
    for receipt in by_hash.values():
        status = str(receipt.get("status") or "UNKNOWN")
        if status in {"TAKE_SUCCESS", "TAKE_FAIL"}:
            labelable += 1
        elif status == "UNMATURED":
            unmatured += 1
        else:
            unlabelable += 1
    return {
        "outcome_count": len(by_hash),
        "labelable_count": labelable,
        "unmatured_count": unmatured,
        "unlabelable_count": unlabelable,
    }


def _gate6_validate_pit(receipt: Mapping[str, Any]) -> dict:
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or receipt.get("phase") != "build-manifest"
        or receipt.get("status") != "MANIFEST_FROZEN"
        or receipt.get("training_requested") is not False
    ):
        raise EvidenceFlywheelBoundaryError("PIT manifest receipt boundary mismatch")
    dataset_hash = _gate6_exact_hash(
        receipt.get("dataset_hash"),
        field="PIT dataset_hash",
    )
    if receipt.get("final_test_state") != "SEALED":
        raise EvidenceFlywheelBoundaryError("PIT final test must be sealed")
    admission = str(receipt.get("training_admission") or "BLOCKED")
    reasons = tuple(str(item) for item in receipt.get("training_admission_reasons") or ())
    if admission not in {"BLOCKED", "ADMITTED"}:
        raise EvidenceFlywheelBoundaryError("PIT training admission invalid")
    if admission == "ADMITTED" and reasons:
        raise EvidenceFlywheelBoundaryError(
            "PIT admitted manifest cannot retain blocking reasons"
        )
    return {
        "dataset_hash": dataset_hash,
        "training_admission": admission,
        "training_admission_reasons": reasons,
        "counts": dict(receipt.get("counts") or {}),
    }


def _gate6_model_receipt(
    receipt: Mapping[str, Any],
    *,
    schema_version: str,
    dataset_hash: str,
    stage: str,
) -> dict:
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version") != schema_version
        or receipt.get("status") != "PASS"
        or receipt.get("dataset_hash") != dataset_hash
    ):
        raise EvidenceFlywheelBoundaryError(f"{stage} receipt boundary mismatch")
    return dict(receipt)


def compile_gate6_learning_admission(
    *,
    gate5_receipt: Mapping[str, Any],
    recording_receipt: Optional[Mapping[str, Any]] = None,
    outcome_receipts: Optional[Sequence[Mapping[str, Any]]] = None,
    pit_manifest_receipt: Optional[Mapping[str, Any]] = None,
    whitebox_oot_receipt: Optional[Mapping[str, Any]] = None,
    logistic_receipt: Optional[Mapping[str, Any]] = None,
    lightgbm_meta_receipt: Optional[Mapping[str, Any]] = None,
    calibration_receipt: Optional[Mapping[str, Any]] = None,
    shadow_receipt: Optional[Mapping[str, Any]] = None,
    promotion_receipt: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Compile the whole Gate6 chain without training, promotion, or I/O.

    This is a point-of-use admission receipt.  It never treats an absent future
    stage as complete and never grants the external effect it validates.
    """
    gate5 = _gate6_validate_gate5_receipt(gate5_receipt)
    stages: dict[str, dict[str, Any]] = {
        name: {"state": "NOT_OBSERVED", "reason_codes": []}
        for name in _GATE6_STAGE_ORDER
    }
    reasons: list[str] = []
    product_ready = gate5.get("all_input_facts_product_ready") is True
    stages["GATE5_DYNAMIC_PRODUCT"] = {
        "state": "PASS_FACT_READY" if product_ready else "PASS_MISSINGNESS_COMPLETE",
        "reason_codes": [] if product_ready else ["FULL_DOMAIN_PRODUCT_DATA_NOT_READY"],
        "output_sha256": gate5.get("output_sha256"),
    }
    if not product_ready:
        reasons.append("FULL_DOMAIN_PRODUCT_DATA_NOT_READY")

    recording = None
    if recording_receipt is None:
        reasons.append("RECORDING_RECEIPT_MISSING")
        stages["RECORDING_LEDGER"]["reason_codes"] = [
            "RECORDING_RECEIPT_MISSING"
        ]
    else:
        recording = _gate6_validate_recording(recording_receipt)
        all_pit_eligible = (
            recording["pit_eligible_count"] == recording["record_count"]
        )
        stages["RECORDING_LEDGER"] = {
            "state": "PASS" if all_pit_eligible else "PARTIAL",
            "reason_codes": [] if all_pit_eligible else [
                "PIT_ELIGIBILITY_INCOMPLETE"
            ],
            **recording,
        }
        if not all_pit_eligible:
            reasons.append("PIT_ELIGIBILITY_INCOMPLETE")

    outcomes = None
    if recording is not None and outcome_receipts is not None:
        outcomes = _gate6_validate_outcomes(
            outcome_receipts,
            recording["prediction_hashes"],
        )
        complete = (
            outcomes["labelable_count"] == recording["record_count"]
            and outcomes["unmatured_count"] == 0
            and outcomes["unlabelable_count"] == 0
        )
        outcome_reasons = []
        if outcomes["unmatured_count"]:
            outcome_reasons.append("OUTCOMES_UNMATURED")
        if outcomes["unlabelable_count"]:
            outcome_reasons.append("OUTCOMES_UNLABELABLE")
        stages["MATURE_OUTCOMES"] = {
            "state": "PASS" if complete else "PARTIAL",
            "reason_codes": outcome_reasons,
            **outcomes,
        }
        reasons.extend(outcome_reasons)
    elif recording is not None:
        stages["MATURE_OUTCOMES"]["reason_codes"] = [
            "OUTCOME_RECEIPTS_MISSING"
        ]
        reasons.append("OUTCOME_RECEIPTS_MISSING")

    pit = None
    if pit_manifest_receipt is not None:
        pit = _gate6_validate_pit(pit_manifest_receipt)
        admitted = pit["training_admission"] == "ADMITTED"
        stages["PIT_DATASET"] = {
            "state": "PASS" if admitted else "BLOCKED",
            "reason_codes": list(pit["training_admission_reasons"]),
            **pit,
        }
        if not admitted:
            reasons.extend(pit["training_admission_reasons"] or (
                "PIT_TRAINING_ADMISSION_BLOCKED",
            ))
    else:
        stages["PIT_DATASET"]["reason_codes"] = ["PIT_MANIFEST_MISSING"]
        reasons.append("PIT_MANIFEST_MISSING")

    dataset_hash = pit["dataset_hash"] if pit is not None else None
    whitebox = None
    if whitebox_oot_receipt is not None:
        if not dataset_hash:
            raise EvidenceFlywheelBoundaryError(
                "whitebox OOT cannot precede PIT dataset"
            )
        whitebox = _gate6_model_receipt(
            whitebox_oot_receipt,
            schema_version="whitebox-oot-receipt-v1",
            dataset_hash=dataset_hash,
            stage="whitebox OOT",
        )
        if (
            whitebox.get("model_family") != "WHITE_BOX_RULE_BASELINE"
            or whitebox.get("walk_forward") is not True
            or whitebox.get("leakage_checks_passed") is not True
            or whitebox.get("final_test_untouched") is not True
        ):
            raise EvidenceFlywheelBoundaryError(
                "whitebox OOT proof is incomplete"
            )
        stages["WHITEBOX_OOT"] = {
            "state": "PASS",
            "reason_codes": [],
            "receipt_hash": _gate6_exact_hash(
                whitebox.get("receipt_hash"),
                field="whitebox receipt_hash",
            ),
        }
    elif pit is not None and pit["training_admission"] == "ADMITTED":
        stages["WHITEBOX_OOT"]["reason_codes"] = [
            "WHITEBOX_OOT_RECEIPT_MISSING"
        ]
        reasons.append("WHITEBOX_OOT_RECEIPT_MISSING")

    logistic = None
    if logistic_receipt is not None:
        if whitebox is None or not dataset_hash:
            raise EvidenceFlywheelBoundaryError(
                "Logistic cannot precede whitebox OOT"
            )
        logistic = _gate6_model_receipt(
            logistic_receipt,
            schema_version="logistic-meta-receipt-v1",
            dataset_hash=dataset_hash,
            stage="Logistic",
        )
        if logistic.get("model_family") != "LOGISTIC_REGRESSION":
            raise EvidenceFlywheelBoundaryError("Logistic model family mismatch")
        stages["LOGISTIC_META"] = {
            "state": "PASS",
            "reason_codes": [],
            "model_hash": _gate6_exact_hash(
                logistic.get("model_hash"), field="Logistic model_hash"
            ),
            "receipt_hash": _gate6_exact_hash(
                logistic.get("receipt_hash"), field="Logistic receipt_hash"
            ),
        }
    elif whitebox is not None:
        stages["LOGISTIC_META"]["reason_codes"] = [
            "LOGISTIC_RECEIPT_MISSING"
        ]
        reasons.append("LOGISTIC_RECEIPT_MISSING")

    selected_model_hash = None
    if logistic is not None:
        selected_model_hash = logistic["model_hash"]
        stages["LIGHTGBM_INCREMENTAL"] = {
            "state": "NOT_REQUESTED",
            "reason_codes": [],
            "policy": "ONLY_IF_OOT_INCREMENTAL_GAIN_PROVEN",
        }
    if lightgbm_meta_receipt is not None:
        if logistic is None or not dataset_hash:
            raise EvidenceFlywheelBoundaryError(
                "LightGBM cannot precede Logistic"
            )
        lightgbm = _gate6_model_receipt(
            lightgbm_meta_receipt,
            schema_version="lightgbm-meta-receipt-v1",
            dataset_hash=dataset_hash,
            stage="LightGBM",
        )
        if (
            lightgbm.get("model_family") != "LIGHTGBM_META"
            or lightgbm.get("incremental_gain_proven") is not True
            or lightgbm.get("logistic_model_hash") != logistic["model_hash"]
        ):
            raise EvidenceFlywheelBoundaryError(
                "LightGBM incremental proof is incomplete"
            )
        selected_model_hash = _gate6_exact_hash(
            lightgbm.get("model_hash"), field="LightGBM model_hash"
        )
        stages["LIGHTGBM_INCREMENTAL"] = {
            "state": "PASS",
            "reason_codes": [],
            "model_hash": selected_model_hash,
            "receipt_hash": _gate6_exact_hash(
                lightgbm.get("receipt_hash"), field="LightGBM receipt_hash"
            ),
        }

    calibration = None
    if calibration_receipt is not None:
        if not selected_model_hash or not dataset_hash:
            raise EvidenceFlywheelBoundaryError(
                "calibration cannot precede an admitted model"
            )
        calibration = _gate6_model_receipt(
            calibration_receipt,
            schema_version="independent-calibration-receipt-v1",
            dataset_hash=dataset_hash,
            stage="calibration",
        )
        if (
            calibration.get("model_hash") != selected_model_hash
            or calibration.get("independent_calibration_set") is not True
        ):
            raise EvidenceFlywheelBoundaryError(
                "independent calibration proof is incomplete"
            )
        stages["INDEPENDENT_CALIBRATION"] = {
            "state": "PASS",
            "reason_codes": [],
            "calibrator_hash": _gate6_exact_hash(
                calibration.get("calibrator_hash"),
                field="calibrator_hash",
            ),
            "receipt_hash": _gate6_exact_hash(
                calibration.get("receipt_hash"),
                field="calibration receipt_hash",
            ),
        }
    elif selected_model_hash:
        stages["INDEPENDENT_CALIBRATION"]["reason_codes"] = [
            "CALIBRATION_RECEIPT_MISSING"
        ]
        reasons.append("CALIBRATION_RECEIPT_MISSING")

    shadow = None
    if shadow_receipt is not None:
        if calibration is None or not dataset_hash:
            raise EvidenceFlywheelBoundaryError(
                "Shadow cannot precede calibration"
            )
        shadow = _gate6_model_receipt(
            shadow_receipt,
            schema_version="frozen-shadow-receipt-v1",
            dataset_hash=dataset_hash,
            stage="Shadow",
        )
        if (
            shadow.get("status") != "PASS"
            or shadow.get("model_hash") != selected_model_hash
            or shadow.get("calibrator_hash") != calibration["calibrator_hash"]
            or shadow.get("frozen") is not True
            or shadow.get("automatic_trading") is not False
            or shadow.get("automatic_parameter_updates") is not False
        ):
            raise EvidenceFlywheelBoundaryError("Shadow proof is incomplete")
        stages["FROZEN_SHADOW"] = {
            "state": "PASS",
            "reason_codes": [],
            "shadow_id": _gate6_exact_hash(
                shadow.get("shadow_id"), field="shadow_id"
            ),
            "receipt_hash": _gate6_exact_hash(
                shadow.get("receipt_hash"), field="Shadow receipt_hash"
            ),
        }
    elif calibration is not None:
        stages["FROZEN_SHADOW"]["reason_codes"] = [
            "FROZEN_SHADOW_RECEIPT_MISSING"
        ]
        reasons.append("FROZEN_SHADOW_RECEIPT_MISSING")

    promotion_valid = False
    if promotion_receipt is not None:
        if shadow is None or not dataset_hash:
            raise EvidenceFlywheelBoundaryError(
                "Promotion cannot precede frozen Shadow"
            )
        if (
            not isinstance(promotion_receipt, Mapping)
            or promotion_receipt.get("schema_version")
            != "explicit-promotion-receipt-v1"
            or promotion_receipt.get("status") != "APPROVED"
            or promotion_receipt.get("explicit_promotion") is not True
            or promotion_receipt.get("automatic") is not False
            or promotion_receipt.get("dataset_hash") != dataset_hash
            or promotion_receipt.get("model_hash") != selected_model_hash
            or promotion_receipt.get("calibrator_hash")
            != calibration["calibrator_hash"]
            or promotion_receipt.get("shadow_id") != shadow["shadow_id"]
        ):
            raise EvidenceFlywheelBoundaryError(
                "explicit Promotion receipt boundary mismatch"
            )
        promotion_valid = True
        stages["EXPLICIT_PROMOTION"] = {
            "state": "PASS_RECEIPT_ONLY",
            "reason_codes": [],
            "promotion_id": _gate6_exact_hash(
                promotion_receipt.get("promotion_id"),
                field="promotion_id",
            ),
        }
    elif shadow is not None:
        stages["EXPLICIT_PROMOTION"]["reason_codes"] = [
            "EXPLICIT_PROMOTION_RECEIPT_MISSING"
        ]
        reasons.append("EXPLICIT_PROMOTION_RECEIPT_MISSING")

    reasons = sorted(set(reasons))
    all_stage_receipts_valid = all(
        stages[name]["state"] in {
            "PASS_FACT_READY", "PASS", "NOT_REQUESTED", "PASS_RECEIPT_ONLY"
        }
        for name in _GATE6_STAGE_ORDER
    )
    ready_for_separate_effect = (
        product_ready and not reasons and all_stage_receipts_valid and promotion_valid
    )
    document = {
        "schema_version": GATE6_LEARNING_ADMISSION_SCHEMA_VERSION,
        "state": (
            "READY_FOR_SEPARATELY_AUTHORIZED_PROMOTION_EFFECT"
            if ready_for_separate_effect else "HOLD"
        ),
        "stage_order": _GATE6_STAGE_ORDER,
        "stages": stages,
        "reason_codes": reasons,
        "current_probability_authority": False,
        "training_effect_authority": False,
        "promotion_effect_authority": False,
        "broker_or_automatic_trading_authority": False,
        "network_calls": 0,
        "database_writes": 0,
        "model_training_runs": 0,
    }
    document["admission_hash"] = digest(document)
    return document


def _read_json_object(path_value: str, *, label: str) -> Dict[str, Any]:
    path = Path(path_value)
    if not path.is_file():
        raise EvidenceFlywheelBoundaryError(f"{label} file does not exist")
    if path.stat().st_size > MAX_IDENTITY_FILE_BYTES:
        raise EvidenceFlywheelBoundaryError(f"{label} file exceeds byte cap")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceFlywheelBoundaryError(f"{label} file is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise EvidenceFlywheelBoundaryError(f"{label} must contain a JSON object")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DSA minimum evidence-flywheel runtime")
    subparsers = parser.add_subparsers(dest="phase", required=True)

    record = subparsers.add_parser("record", help="run one bounded non-notifying analysis")
    record.add_argument("--stocks", required=True, help="one or two ordinary CN stock codes")
    record.add_argument("--code-sha", default=os.getenv("GITHUB_SHA", ""))
    record.add_argument("--single-stock-only", action="store_true")
    record.add_argument("--closed-world-receipt", action="store_true")
    record.add_argument("--product-report-receipt", action="store_true")
    record.add_argument("--receipt-file")

    replay = subparsers.add_parser(
        "replay-record",
        help="record explicit historical SPECIFIED_CODES sessions without report artifacts",
    )
    replay.add_argument("--stock", required=True, help="one ordinary CN stock code")
    replay.add_argument("--sessions", required=True, help="comma-separated ISO XSHG sessions")
    replay.add_argument("--code-sha", default=os.getenv("GITHUB_SHA", ""))
    replay.add_argument("--receipt-file")

    evaluate = subparsers.add_parser("evaluate-outcome", help="evaluate one matured prediction")
    evaluate.add_argument("--prediction-hash", required=True)
    evaluate.add_argument("--cost-identity-file", required=True)
    evaluate.add_argument("--execution-identity-file", required=True)
    evaluate.add_argument("--correction-reason")

    manifest = subparsers.add_parser("build-manifest", help="freeze a blocked PIT manifest")
    manifest.add_argument("--cost-identity-file", required=True)
    manifest.add_argument("--intended-cohort-id")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    runtime_state_closed = False
    try:
        if args.phase == "record":
            from main import validate_p0_stock_codes

            if args.closed_world_receipt and not args.single_stock_only:
                raise EvidenceFlywheelBoundaryError(
                    "closed-world Actions receipt requires --single-stock-only"
                )
            if args.product_report_receipt and not args.closed_world_receipt:
                raise EvidenceFlywheelBoundaryError(
                    "product report receipt requires --closed-world-receipt"
                )
            config = None
            database_file = None
            if args.closed_world_receipt:
                from src.config import get_config

                config = get_config()
                database_file = _resolve_database_file(config)

            receipt = record_canonical_run(
                stock_codes=validate_p0_stock_codes(args.stocks),
                code_sha=args.code_sha,
                config=config,
                require_single_stock=args.single_stock_only,
                closed_world_database_receipt=args.closed_world_receipt,
                receipt_only=(
                    args.closed_world_receipt
                    and not args.product_report_receipt
                ),
                product_report_receipt=args.product_report_receipt,
            )
            if args.closed_world_receipt:
                _reset_default_runtime_state()
                runtime_state_closed = True
                if database_file is None:
                    raise EvidenceFlywheelRuntimeError(
                        "closed-world receipt database identity is unavailable"
                    )
                receipt["database_receipt"].update(
                    _database_file_identity(database_file)
                )
            if args.receipt_file:
                _write_receipt_file(args.receipt_file, receipt)
        elif args.phase == "replay-record":
            from src.config import get_config

            receipt = replay_specified_codes_daily_sessions(
                stock_code=args.stock,
                session_dates=[item.strip() for item in args.sessions.split(",") if item.strip()],
                code_sha=args.code_sha,
                config=get_config(),
            )
            if args.receipt_file:
                _write_receipt_file(args.receipt_file, receipt)
        elif args.phase == "evaluate-outcome":
            receipt = evaluate_prediction_outcome(
                prediction_hash=args.prediction_hash,
                cost_identity=_read_json_object(
                    args.cost_identity_file,
                    label="cost_identity",
                ),
                execution_identity=_read_json_object(
                    args.execution_identity_file,
                    label="execution_identity",
                ),
                correction_reason=args.correction_reason,
            )
        else:
            receipt = build_pit_manifest_receipt(
                cost_identity=_read_json_object(
                    args.cost_identity_file,
                    label="cost_identity",
                ),
                intended_cohort_id=args.intended_cohort_id,
            )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": RECEIPT_SCHEMA_VERSION,
                    "phase": args.phase,
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        if (
            args.phase == "record"
            and getattr(args, "closed_world_receipt", False)
            and not runtime_state_closed
        ):
            _reset_default_runtime_state()
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
