# -*- coding: utf-8 -*-
"""Typed same-DB learning-recording journal for canonical research runs."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Mapping, Optional

from sqlalchemy import select

from src.services.pit_identity import (
    canonical_json,
    normalize_research_selection_context,
)
from src.storage import DatabaseManager, LearningRecordingRecord, utc_naive_now


LEARNING_RECORDING_SCHEMA_VERSION = "learning-recording-intent-v1"
LEARNING_RECORDING_POLICY_VERSION = "learning-recording-policy-v1"
LEARNING_RECORDING_COHORT_SCHEMA_VERSION = "learning-recording-cohort-v1"

PENDING = "PENDING"
RECORDED = "RECORDED"
LAWFULLY_REJECTED = "LAWFULLY_REJECTED"
TECHNICALLY_LOST = "TECHNICALLY_LOST"

_TERMINAL = {RECORDED, LAWFULLY_REJECTED, TECHNICALLY_LOST}
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_REASON_RE = re.compile(r"^[A-Z0-9_:-]{1,128}$")


class LearningRecordingService:
    """Compile and reconcile one durable learning-recording intent."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    @classmethod
    def compile_intent(
        cls,
        *,
        result: Any,
        query_id: str,
        report_type: str,
        selection_context: Optional[Mapping[str, Any]] = None,
        code_sha: Optional[str] = None,
        intended_cohort_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        dashboard = cls._mapping(getattr(result, "dashboard", None))
        factor = cls._mapping(dashboard.get("factor_decision"))
        canonical_binding = cls._mapping(factor.get("canonical_decision_identity"))
        canonical_binding_hash = (
            cls._sha256(canonical_json(canonical_binding))
            if canonical_binding
            else None
        )
        trace = cls._mapping(factor.get("evidence_traceability"))
        mtf = cls._mapping(factor.get("multi_timeframe_structure_context"))
        data_snapshot_identity = cls._text(
            trace.get("data_snapshot_identity")
            or mtf.get("data_snapshot_identity")
        )
        strategy_id = cls._text(factor.get("strategy_id"))
        normalized_selection: Dict[str, Any] = {}
        if isinstance(selection_context, Mapping) and selection_context:
            try:
                normalized_selection = normalize_research_selection_context(
                    dict(selection_context)
                )
            except Exception:
                normalized_selection = {}
        selection_source = cls._text(normalized_selection.get("selection_source"))
        selection_context_hash = cls._text(
            normalized_selection.get("selection_context_hash")
        )
        explicit_cohort = cls._text(intended_cohort_id)
        if explicit_cohort is not None:
            bound_intended_cohort_id = cls._normalize_sha256(explicit_cohort)
            if bound_intended_cohort_id is None:
                raise ValueError("intended_cohort_id must be a 64-hex SHA-256")
        else:
            query_text = cls._text(query_id)
            bound_intended_cohort_id = (
                cls._sha256(
                    canonical_json(
                        {
                            "schema_version": LEARNING_RECORDING_COHORT_SCHEMA_VERSION,
                            "query_id": query_text,
                            "selection_source": selection_source,
                            "selection_context_hash": selection_context_hash,
                        }
                    )
                )
                if query_text is not None
                else None
            )
        bound_code_sha = cls._normalize_code_sha(code_sha)
        stock_code = cls._text(getattr(result, "code", None))
        market = "cn" if stock_code and stock_code.isdigit() and len(stock_code) == 6 else None

        identity = {
            "schema_version": LEARNING_RECORDING_SCHEMA_VERSION,
            "policy_version": LEARNING_RECORDING_POLICY_VERSION,
            "stock_code": stock_code,
            "market": market,
            "report_type": cls._text(report_type),
            "strategy_id": strategy_id,
            "strategy_version": strategy_id,
            "canonical_binding_hash": canonical_binding_hash,
            "data_snapshot_identity": data_snapshot_identity,
            "code_sha": bound_code_sha,
            "selection_source": selection_source,
            "selection_context_hash": selection_context_hash,
            "intended_cohort_id": bound_intended_cohort_id,
        }
        if (
            canonical_binding_hash is None
            and data_snapshot_identity is None
            and selection_context_hash is None
        ):
            identity["fallback_query_id"] = cls._text(query_id)
        recording_intent_hash = cls._sha256(canonical_json(identity))
        return {
            **identity,
            "recording_intent_hash": recording_intent_hash,
        }

    def get(self, recording_intent_hash: str) -> Optional[Dict[str, Any]]:
        intent_hash = self._require_hash(recording_intent_hash)
        with self.db.get_session() as session:
            row = session.execute(
                select(LearningRecordingRecord)
                .where(LearningRecordingRecord.recording_intent_hash == intent_hash)
                .limit(1)
            ).scalar_one_or_none()
            return self._receipt(row) if row is not None else None

    def mark_recorded(
        self,
        recording_intent_hash: str,
        *,
        prediction_hash: str,
    ) -> Optional[Dict[str, Any]]:
        prediction = self._require_hash(prediction_hash)
        return self._mark(
            recording_intent_hash,
            disposition=RECORDED,
            reason_code=None,
            prediction_hash=prediction,
        )

    def mark_lawfully_rejected(
        self,
        recording_intent_hash: str,
        *,
        reason_code: str,
    ) -> Optional[Dict[str, Any]]:
        return self._mark(
            recording_intent_hash,
            disposition=LAWFULLY_REJECTED,
            reason_code=self._reason(reason_code),
            prediction_hash=None,
        )

    def mark_technically_lost(
        self,
        recording_intent_hash: str,
        *,
        reason_code: str,
    ) -> Optional[Dict[str, Any]]:
        return self._mark(
            recording_intent_hash,
            disposition=TECHNICALLY_LOST,
            reason_code=self._reason(reason_code),
            prediction_hash=None,
        )

    def _mark(
        self,
        recording_intent_hash: str,
        *,
        disposition: str,
        reason_code: Optional[str],
        prediction_hash: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        intent_hash = self._require_hash(recording_intent_hash)
        target = str(disposition or "").strip().upper()
        if target not in _TERMINAL:
            raise ValueError("recording disposition must be terminal")

        def _write(session):
            row = session.execute(
                select(LearningRecordingRecord)
                .where(LearningRecordingRecord.recording_intent_hash == intent_hash)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            current = str(row.disposition or PENDING).upper()
            if current in {RECORDED, LAWFULLY_REJECTED} and current != target:
                raise ValueError(
                    "terminal learning-recording disposition cannot be rewritten"
                )
            if current == RECORDED and prediction_hash and row.prediction_hash != prediction_hash:
                raise ValueError("recorded prediction hash cannot change")
            if current != PENDING:
                row.retry_count = int(row.retry_count or 0) + 1
            row.disposition = target
            row.reason_code = reason_code
            if prediction_hash is not None:
                row.prediction_hash = prediction_hash
            row.updated_at = utc_naive_now()
            session.flush()
            return self._receipt(row)

        return self.db._run_write_transaction(
            f"learning-recording[{intent_hash[:12]}]",
            _write,
        )

    @staticmethod
    def _receipt(row: LearningRecordingRecord) -> Dict[str, Any]:
        return {
            "recording_intent_hash": row.recording_intent_hash,
            "schema_version": row.schema_version,
            "policy_version": row.policy_version,
            "analysis_history_id": int(row.analysis_history_id),
            "intended_cohort_id": row.intended_cohort_id,
            "disposition": row.disposition,
            "reason_code": row.reason_code,
            "prediction_hash": row.prediction_hash,
            "retry_count": int(row.retry_count or 0),
        }

    @staticmethod
    def _mapping(value: Any) -> Dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _text(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_sha256(value: Any) -> Optional[str]:
        text = str(value or "").strip().lower()
        if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
            return None
        return text

    @staticmethod
    def _normalize_code_sha(value: Any) -> Optional[str]:
        text = str(value or "").strip().lower()
        return text if _SHA40_RE.fullmatch(text) else None

    @staticmethod
    def _require_hash(value: Any) -> str:
        text = str(value or "").strip().lower()
        if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
            raise ValueError("recording identity must be a 64-hex SHA-256")
        return text

    @staticmethod
    def _reason(value: Any) -> str:
        text = str(value or "").strip().upper()
        if not _REASON_RE.fullmatch(text):
            raise ValueError("recording reason_code is invalid")
        return text
