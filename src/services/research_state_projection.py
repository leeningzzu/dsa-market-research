# -*- coding: utf-8 -*-
"""Low-sensitivity opportunity and strategy-eligibility identities."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any, Dict, Optional

from src.services.evidence_traceability_registry import strategy_contract_coverage


CANONICAL_OPPORTUNITY_PROJECTION_VERSION = "canonical-opportunity-v3"
STRATEGY_ELIGIBILITY_SCHEMA_VERSION = "strategy-eligibility-v2"
STRATEGY_ELIGIBILITY_LEDGER_SCHEMA_VERSION = "prediction-ledger-v5"
STRATEGY_ELIGIBILITY_RECORDING_LEDGER_SCHEMA_VERSION = "prediction-ledger-v6"
STRATEGY_ELIGIBILITY_LEDGER_SCHEMA_VERSIONS = frozenset(
    {
        STRATEGY_ELIGIBILITY_LEDGER_SCHEMA_VERSION,
        STRATEGY_ELIGIBILITY_RECORDING_LEDGER_SCHEMA_VERSION,
    }
)
STOCK_TREND_QUALITY_PULLBACK_STRATEGY_ID = "stock_trend_quality_pullback_v1"
ETF_RELATIVE_STRENGTH_ROTATION_STRATEGY_ID = "etf_relative_strength_rotation_v1"
CANONICAL_DECISION_SEMANTIC_VERSION = "canonical-decision-semantic-v1"
CANONICAL_DECISION_IDENTITY_VERSION = "canonical-decision-identity-v1"

_SUPPORTED_CANONICAL_AUTHORITIES = {
    STOCK_TREND_QUALITY_PULLBACK_STRATEGY_ID,
    ETF_RELATIVE_STRENGTH_ROTATION_STRATEGY_ID,
}
_LEGAL_CANONICAL_TUPLES = {
    ("WAIT", "watch", "UNKNOWN", False),
    ("WAIT", "watch", "PROVEN", False),
    ("PASS", "avoid", "PROVEN", True),
}

STRATEGY_CONTRACT_COVERAGE_VERSION = "stock-trend-quality-pullback-contract-coverage-v1"
# Preserve the accepted v1 clause document/hash; derive its membership from one registry.
STOCK_TREND_QUALITY_PULLBACK_CONTRACT_COVERAGE = strategy_contract_coverage()

STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE = tuple(
    evidence_key
    for _, classification, evidence_key in STOCK_TREND_QUALITY_PULLBACK_CONTRACT_COVERAGE
    if classification == "HARD_ELIGIBILITY" and evidence_key is not None
)
_STRATEGY_CONTRACT_COVERAGE_DOCUMENT = [
    {
        "clause": clause,
        "classification": classification,
        "evidence_key": evidence_key,
    }
    for clause, classification, evidence_key in STOCK_TREND_QUALITY_PULLBACK_CONTRACT_COVERAGE
]
STRATEGY_CONTRACT_COVERAGE_HASH = hashlib.sha256(
    json.dumps(
        _STRATEGY_CONTRACT_COVERAGE_DOCUMENT,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()

_REQUIRED_STATES = {"SATISFIED", "FAILED", "UNKNOWN", "NOT_APPLICABLE"}
_ELIGIBILITY_STATES = {"ELIGIBLE", "INELIGIBLE", "UNKNOWN", "NOT_APPLICABLE"}
_REASON_CODE_RE = re.compile(r"^[A-Z0-9_:-]{1,128}$")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _normalized_reason_codes(value: Any) -> tuple[list[str], bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return [], False
    normalized: list[str] = []
    valid = True
    for item in value:
        text = str(item or "").strip().upper()
        if not _REASON_CODE_RE.fullmatch(text):
            valid = False
            continue
        normalized.append(text)
    return sorted(set(normalized)), valid


def validate_canonical_decision_semantics(
    canonical_decision: Mapping[str, Any],
    *,
    strategy_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate and normalize the one legal deterministic action state."""

    if not isinstance(canonical_decision, Mapping) or not canonical_decision:
        raise ValueError("canonical decision is required")
    authority = str(canonical_decision.get("authority") or "").strip()
    action = str(canonical_decision.get("action") or "").strip().upper()
    public_action = str(canonical_decision.get("public_action") or "").strip().lower()
    evidence_state = str(canonical_decision.get("evidence_state") or "").strip().upper()
    raw_hard_veto = canonical_decision.get("hard_veto")
    if authority not in _SUPPORTED_CANONICAL_AUTHORITIES:
        raise ValueError("canonical authority is not admitted")
    expected_strategy = str(strategy_id or "").strip()
    if expected_strategy and authority != expected_strategy:
        raise ValueError("canonical authority does not match strategy_id")
    if not isinstance(raw_hard_veto, bool):
        raise ValueError("canonical hard_veto must be boolean")
    if (action, public_action, evidence_state, raw_hard_veto) not in _LEGAL_CANONICAL_TUPLES:
        raise ValueError("canonical action/public_action/evidence_state/hard_veto tuple is illegal")
    reason_codes, reasons_valid = _normalized_reason_codes(canonical_decision.get("reason_codes"))
    if not reasons_valid:
        raise ValueError("canonical reason_codes are invalid")
    return {
        "authority": authority,
        "action": action,
        "public_action": public_action,
        "evidence_state": evidence_state,
        "hard_veto": raw_hard_veto,
        "reason_codes": reason_codes,
    }


def build_canonical_decision_identity(
    canonical_decision: Mapping[str, Any],
    *,
    strategy_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a stable semantic identity for the validated canonical decision."""

    normalized = validate_canonical_decision_semantics(
        canonical_decision,
        strategy_id=strategy_id,
    )
    document = {
        "schema_version": CANONICAL_DECISION_SEMANTIC_VERSION,
        **normalized,
    }
    return {
        "schema_version": CANONICAL_DECISION_IDENTITY_VERSION,
        "strategy_id": str(strategy_id or normalized["authority"]).strip(),
        "canonical_decision_hash": hashlib.sha256(
            _canonical_json(document).encode("utf-8")
        ).hexdigest(),
    }


def build_strategy_eligibility_identity(
    strategy_eligibility: Optional[Mapping[str, Any]],
    *,
    strategy_id: Optional[str],
) -> Dict[str, Any]:
    """Build one fail-closed eligibility identity for an exact strategy contract.

    The declared top-level state is never trusted by itself.  It must agree with
    the complete required-evidence matrix.  Missing or malformed input becomes
    ``UNKNOWN`` and cannot enter the white-box opportunity denominator.
    """

    source = dict(strategy_eligibility) if isinstance(strategy_eligibility, Mapping) else {}
    expected_strategy_id = str(strategy_id or source.get("strategy_id") or "").strip()
    reasons, reasons_valid = _normalized_reason_codes(source.get("reason_codes"))
    system_reasons: set[str] = set()

    if not source:
        system_reasons.add("STRATEGY_ELIGIBILITY_NOT_BOUND")
    if source and source.get("schema_version") != STRATEGY_ELIGIBILITY_SCHEMA_VERSION:
        system_reasons.add("STRATEGY_ELIGIBILITY_SCHEMA_MISMATCH")
    if not expected_strategy_id:
        system_reasons.add("STRATEGY_ID_NOT_BOUND")
    elif source and str(source.get("strategy_id") or "").strip() != expected_strategy_id:
        system_reasons.add("STRATEGY_ID_MISMATCH")
    if not reasons_valid:
        system_reasons.add("STRATEGY_ELIGIBILITY_REASON_CODES_INVALID")

    raw_required = source.get("required_evidence")
    required_source = dict(raw_required) if isinstance(raw_required, Mapping) else {}
    if set(required_source) != set(STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE):
        system_reasons.add("REQUIRED_EVIDENCE_MATRIX_INCOMPLETE")

    required: Dict[str, str] = {}
    invalid_required_state = False
    for key in STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE:
        state = str(required_source.get(key) or "UNKNOWN").strip().upper()
        if state == "MISSING":
            state = "UNKNOWN"
        if state not in _REQUIRED_STATES:
            state = "UNKNOWN"
            invalid_required_state = True
        required[key] = state
    if invalid_required_state:
        system_reasons.add("REQUIRED_EVIDENCE_STATE_INVALID")

    structural_gap = bool(
        {
            "STRATEGY_ELIGIBILITY_SCHEMA_MISMATCH",
            "STRATEGY_ID_NOT_BOUND",
            "STRATEGY_ID_MISMATCH",
            "STRATEGY_ELIGIBILITY_REASON_CODES_INVALID",
            "REQUIRED_EVIDENCE_MATRIX_INCOMPLETE",
            "REQUIRED_EVIDENCE_STATE_INVALID",
        }
        & system_reasons
    )
    if structural_gap:
        derived_state = "UNKNOWN"
    elif "FAILED" in required.values():
        derived_state = "INELIGIBLE"
        system_reasons.add("REQUIRED_EVIDENCE_FAILED")
    elif "NOT_APPLICABLE" in required.values():
        derived_state = "NOT_APPLICABLE"
        system_reasons.add("REQUIRED_EVIDENCE_NOT_APPLICABLE")
    elif all(value == "SATISFIED" for value in required.values()):
        derived_state = "ELIGIBLE"
    else:
        derived_state = "UNKNOWN"
        system_reasons.add("REQUIRED_EVIDENCE_INCOMPLETE")
    if derived_state == "UNKNOWN":
        system_reasons.add("REQUIRED_EVIDENCE_INCOMPLETE")

    declared_state = str(source.get("state") or "").strip().upper()
    if declared_state not in _ELIGIBILITY_STATES:
        final_state = "UNKNOWN"
        system_reasons.add("STRATEGY_ELIGIBILITY_STATE_NOT_BOUND")
    elif declared_state != derived_state:
        final_state = "UNKNOWN"
        system_reasons.add("STRATEGY_ELIGIBILITY_STATE_MISMATCH")
    else:
        final_state = derived_state

    document = {
        "schema_version": STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
        "strategy_id": expected_strategy_id or None,
        "contract_coverage_version": STRATEGY_CONTRACT_COVERAGE_VERSION,
        "contract_coverage_hash": STRATEGY_CONTRACT_COVERAGE_HASH,
        "contract_coverage": _STRATEGY_CONTRACT_COVERAGE_DOCUMENT,
        "state": final_state,
        "required_evidence": required,
        "reason_codes": sorted(set(reasons) | system_reasons),
    }
    canonical = _canonical_json(document)
    return {
        "strategy_eligibility_version": STRATEGY_ELIGIBILITY_SCHEMA_VERSION,
        "strategy_eligibility_state": final_state,
        "strategy_eligibility_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "strategy_eligibility_json": canonical,
        "strategy_eligibility_reason_codes": list(document["reason_codes"]),
    }


def _normalize_opportunity_decision_for_projection(
    canonical_decision: Mapping[str, Any],
    *,
    strategy_id: Optional[str],
) -> Dict[str, Any]:
    """Validate current canonical state while preserving legacy package readability."""

    decision = dict(canonical_decision)
    if "authority" in decision or "public_action" in decision:
        if "authority" not in decision or "public_action" not in decision:
            raise ValueError("canonical semantic identity is incomplete")
        return validate_canonical_decision_semantics(
            decision,
            strategy_id=strategy_id,
        )

    if set(decision) - {"action", "evidence_state", "hard_veto"}:
        raise ValueError("legacy canonical projection contains unsupported fields")
    authority = str(strategy_id or "").strip()
    if authority not in _SUPPORTED_CANONICAL_AUTHORITIES:
        raise ValueError("legacy canonical projection requires admitted strategy_id")
    action = str(decision.get("action") or "").strip().upper()
    evidence_state = str(decision.get("evidence_state") or "").strip().upper()
    hard_veto = decision.get("hard_veto")
    if not isinstance(hard_veto, bool):
        raise ValueError("legacy canonical hard_veto must be boolean")
    legacy_tuple = (action, evidence_state, hard_veto)
    public_action_by_tuple = {
        ("WAIT", "UNKNOWN", False): "watch",
        ("WAIT", "PROVEN", False): "watch",
        ("PASS", "PROVEN", True): "avoid",
    }
    public_action = public_action_by_tuple.get(legacy_tuple)
    if public_action is None:
        raise ValueError("legacy canonical projection tuple is illegal")
    return {
        "authority": authority,
        "action": action,
        "public_action": public_action,
        "evidence_state": evidence_state,
        "hard_veto": hard_veto,
        "reason_codes": [],
    }


def build_canonical_opportunity_projection(
    canonical_decision: Optional[Mapping[str, Any]],
    *,
    strategy_eligibility: Optional[Mapping[str, Any]] = None,
    strategy_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the durable projection required by Outcome/PIT consumers."""

    decision = dict(canonical_decision) if isinstance(canonical_decision, Mapping) else {}
    if decision:
        decision = _normalize_opportunity_decision_for_projection(
            decision,
            strategy_id=strategy_id,
        )
    action = str(decision.get("action") or "").strip().upper() or None
    evidence_state = str(decision.get("evidence_state") or "").strip().upper() or None
    raw_hard_veto = decision.get("hard_veto")
    hard_veto = raw_hard_veto if isinstance(raw_hard_veto, bool) else None
    eligibility = build_strategy_eligibility_identity(
        strategy_eligibility,
        strategy_id=strategy_id,
    )
    return {
        "opportunity_projection_version": CANONICAL_OPPORTUNITY_PROJECTION_VERSION,
        "canonical_action": action,
        "canonical_evidence_state": evidence_state,
        "canonical_hard_veto": hard_veto,
        "strategy_eligibility_version": eligibility["strategy_eligibility_version"],
        "strategy_eligibility_state": eligibility["strategy_eligibility_state"],
        "strategy_eligibility_hash": eligibility["strategy_eligibility_hash"],
        "strategy_eligibility_json": eligibility["strategy_eligibility_json"],
    }


def _record_has_valid_eligibility_identity(record: Any) -> bool:
    raw_json = getattr(record, "strategy_eligibility_json", None)
    if not isinstance(raw_json, str) or not raw_json:
        return False
    try:
        raw = json.loads(raw_json)
    except (TypeError, ValueError):
        return False
    if not isinstance(raw, Mapping):
        return False
    expected = build_strategy_eligibility_identity(
        raw,
        strategy_id=str(getattr(record, "strategy_id", None) or "").strip() or None,
    )
    return bool(
        getattr(record, "strategy_eligibility_version", None)
        == expected["strategy_eligibility_version"]
        and getattr(record, "strategy_eligibility_state", None)
        == expected["strategy_eligibility_state"]
        and getattr(record, "strategy_eligibility_hash", None)
        == expected["strategy_eligibility_hash"]
        and raw_json == expected["strategy_eligibility_json"]
        and expected["strategy_eligibility_state"] == "ELIGIBLE"
    )


def is_white_box_opportunity_record(record: Any) -> bool:
    """Require the current canonical opportunity plus an exact eligible strategy identity."""

    return bool(
        str(getattr(record, "schema_version", None) or "").strip()
        in STRATEGY_ELIGIBILITY_LEDGER_SCHEMA_VERSIONS
        and str(getattr(record, "strategy_id", None) or "").strip()
        == STOCK_TREND_QUALITY_PULLBACK_STRATEGY_ID
        and str(
            getattr(record, "opportunity_projection_version", None) or ""
        ).strip()
        == CANONICAL_OPPORTUNITY_PROJECTION_VERSION
        and str(getattr(record, "canonical_action", None) or "").upper() == "WAIT"
        and str(getattr(record, "canonical_evidence_state", None) or "").upper()
        == "PROVEN"
        and getattr(record, "canonical_hard_veto", None) is False
        and _record_has_valid_eligibility_identity(record)
    )
