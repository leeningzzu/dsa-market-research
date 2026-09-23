# -*- coding: utf-8 -*-
"""Low-sensitivity canonical opportunity identity for durable research state."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any, Dict, Optional


CANONICAL_OPPORTUNITY_PROJECTION_VERSION = "canonical-opportunity-v1"


def build_canonical_opportunity_projection(
    canonical_decision: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Return the minimal projection required by Outcome/PIT consumers."""

    decision = dict(canonical_decision) if isinstance(canonical_decision, Mapping) else {}
    action = str(decision.get("action") or "").strip().upper() or None
    evidence_state = str(decision.get("evidence_state") or "").strip().upper() or None
    raw_hard_veto = decision.get("hard_veto")
    hard_veto = raw_hard_veto if isinstance(raw_hard_veto, bool) else None
    return {
        "opportunity_projection_version": CANONICAL_OPPORTUNITY_PROJECTION_VERSION,
        "canonical_action": action,
        "canonical_evidence_state": evidence_state,
        "canonical_hard_veto": hard_veto,
    }


def is_white_box_opportunity_record(record: Any) -> bool:
    """Classify a Ledger row from durable projection, with legacy raw fallback."""

    projection_version = str(
        getattr(record, "opportunity_projection_version", None) or ""
    ).strip()
    if projection_version:
        return bool(
            projection_version == CANONICAL_OPPORTUNITY_PROJECTION_VERSION
            and str(getattr(record, "canonical_action", None) or "").upper() == "WAIT"
            and str(getattr(record, "canonical_evidence_state", None) or "").upper()
            == "PROVEN"
            and getattr(record, "canonical_hard_veto", None) is False
        )

    raw_evidence = getattr(record, "evidence_json", None)
    try:
        evidence = json.loads(raw_evidence or "{}")
    except (TypeError, ValueError):
        return False
    decision = evidence.get("canonical_decision") if isinstance(evidence, dict) else None
    projection = build_canonical_opportunity_projection(decision)
    return bool(
        projection["canonical_action"] == "WAIT"
        and projection["canonical_evidence_state"] == "PROVEN"
        and projection["canonical_hard_veto"] is False
    )
