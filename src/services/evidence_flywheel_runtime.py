# -*- coding: utf-8 -*-
"""Local-only orchestration for the minimum DSA evidence flywheel slice.

This module reuses the existing bounded analysis, Prediction Ledger,
PredictionOutcome, and PIT manifest owners.  It never trains a model, sends a
notification, or enables external research-state durability.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Sequence
import uuid

from src.services.pit_identity import (
    build_cn_stock_asset_identity,
    build_specified_codes_selection_context,
    sha256_payload,
)


RECEIPT_SCHEMA_VERSION = "evidence-flywheel-runtime-receipt-v1"
MAX_IDENTITY_FILE_BYTES = 64 * 1024
ZERO_EXTERNAL_MODEL_REQUEST_BUDGET = 0
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _validated_codes(stock_codes: Sequence[str]) -> list[str]:
    normalized = [str(item or "").strip() for item in stock_codes]
    if not 1 <= len(normalized) <= 2:
        raise EvidenceFlywheelBoundaryError("record phase requires exactly one or two CN stocks")
    if len(set(normalized)) != len(normalized):
        raise EvidenceFlywheelBoundaryError("record phase rejects duplicate stock codes")
    for code in normalized:
        identity = build_cn_stock_asset_identity(code, "cn")
        if not identity or identity.get("symbol") != code:
            raise EvidenceFlywheelBoundaryError(f"unsupported CN stock identity: {code}")
    return normalized


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
) -> Dict[str, Any]:
    """Run one bounded, notification-suppressed canonical analysis into Ledger."""
    codes = _validated_codes(stock_codes)
    bound_code_sha = _require_sha(code_sha, length=40, field="code_sha")
    if config is None:
        from src.config import get_config

        config = get_config()
    factory = pipeline_factory or _default_pipeline_factory
    selection_context = build_specified_codes_selection_context(
        raw_selection_source="evidence-flywheel-runtime",
        query_source="cli",
    )
    query_id = f"evidence-flywheel-{uuid.uuid4().hex}"

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
            p0_model_request_budget=ZERO_EXTERNAL_MODEL_REQUEST_BUDGET,
            research_selection_context=selection_context,
            research_code_sha=bound_code_sha,
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
        prediction_hash = _require_sha(
            raw_receipt.get("prediction_hash"),
            length=64,
            field="prediction_hash",
        )
        if prediction_hash in seen_hashes:
            raise EvidenceFlywheelRuntimeError("duplicate prediction hash in bounded record receipt")
        seen_hashes.add(prediction_hash)
        receipts.append(
            {
                "stock_code": str(getattr(result, "code", "") or ""),
                **{
                    key: raw_receipt.get(key)
                    for key in (
                        "id",
                        "created",
                        "prediction_hash",
                        "evidence_hash",
                        "feature_schema_hash",
                        "pit_eligible",
                        "pit_ineligibility_reasons",
                        "durability_state",
                    )
                },
            }
        )

    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "phase": "record",
        "status": "RECORDED",
        "query_id": query_id,
        "code_sha": bound_code_sha,
        "record_count": len(receipts),
        "notification_suppressed": True,
        "external_durability": "NOT_REQUESTED",
        "training_requested": False,
        "model_request_budget": observed_model_request_budget,
        "model_request_count": observed_model_request_count,
        "ledger_receipts": receipts,
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

    evaluate = subparsers.add_parser("evaluate-outcome", help="evaluate one matured prediction")
    evaluate.add_argument("--prediction-hash", required=True)
    evaluate.add_argument("--cost-identity-file", required=True)
    evaluate.add_argument("--execution-identity-file", required=True)
    evaluate.add_argument("--correction-reason")

    manifest = subparsers.add_parser("build-manifest", help="freeze a blocked PIT manifest")
    manifest.add_argument("--cost-identity-file", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.phase == "record":
            from main import validate_p0_stock_codes

            receipt = record_canonical_run(
                stock_codes=validate_p0_stock_codes(args.stocks),
                code_sha=args.code_sha,
            )
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
                )
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
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
