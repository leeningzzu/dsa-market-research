# -*- coding: utf-8 -*-
"""Offline coverage receipts over observed inputs and the shared registry.

Never fetches data, renders HTML, writes a database or sends a notification.
Units/rights are explicit attestations, not facts inferred from numeric values.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import math
from typing import Any

from src.services.evidence_traceability_registry import (
    BASELINE_BYTES, BASELINE_SHA256, DEFERRED_BINDINGS, EVIDENCE_BINDINGS,
    MANIFEST_HASH, MANIFEST_VERSION, TraceabilityError, at,
    digest, load_slot_map, timeframe_ready, validate_runtime_trace,
)

_TIMEFRAME_SLOT_FAMILY = {
    "detail.timeframe.price_position": "STRUCTURE",
    "detail.timeframe.trend_ma": "MTF",
    "detail.timeframe.volume_price": "SUPPLY",
    "detail.timeframe.cost_structure": "COST",
    "detail.timeframe.price_structure": "STRUCTURE",
    "detail.timeframe.momentum_divergence": "MOMENTUM",
    "detail.timeframe.pattern_trigger": "PATTERN",
    "detail.timeframe.support_resistance": "STRUCTURE",
    "detail.timeframe.confirmation_invalidation": "STRUCTURE",
}
_MATRIX_AVAILABLE_STATES = frozenset({"READY", "PARTIAL"})


def verify_original_mapping(original: bytes) -> dict:
    """Verify independently supplied original bytes, without EOL normalization."""
    if len(original) != BASELINE_BYTES or sha256(original).hexdigest() != BASELINE_SHA256:
        raise TraceabilityError("V25_ORIGINAL_BYTES_MISMATCH")

    class Classes(HTMLParser):
        def __init__(self):
            super().__init__()
            self.classes = set()
            self.div_depth = 0
            self.card_depths = set()
            self.paragraph = None
            self.card_paragraphs = []

        def handle_starttag(self, tag, attrs):
            classes = set((dict(attrs).get("class") or "").split())
            self.classes.update(classes)
            if tag == "div":
                self.div_depth += 1
                if "card" in classes:
                    self.card_depths.add(self.div_depth)
            if tag == "p" and self.card_depths:
                self.paragraph = []

        def handle_data(self, data):
            if self.paragraph is not None:
                self.paragraph.append(data)

        def handle_endtag(self, tag):
            if tag == "p" and self.paragraph is not None:
                self.card_paragraphs.append("".join(self.paragraph))
                self.paragraph = None
            if tag == "div":
                self.card_depths.discard(self.div_depth)
                self.div_depth -= 1

    parser = Classes()
    parser.feed(original.decode("utf-8"))
    slots = load_slot_map()
    if any(s["location_class"] not in parser.classes for s in slots["slots"]):
        raise TraceabilityError("SLOT_LOCATION_ABSENT_FROM_ORIGINAL")
    for slot in slots["slots"]:
        if slot.get("leaf_tag") == "p" and not any(slot["leaf_label"] in text for text in parser.card_paragraphs):
            raise TraceabilityError("SLOT_LEAF_ABSENT_FROM_ORIGINAL")
    return {"baseline_sha256": BASELINE_SHA256, "bytes": BASELINE_BYTES,
            "manifest_hash": MANIFEST_HASH, "semantic_locations_verified": True,
            "dynamic_fidelity_proven": False, "rendered": False}


def compile_product_coverage(factor: Mapping) -> dict:
    """Semantic reachability only. A runtime payload cannot authorize rendering."""
    trace = validate_runtime_trace(factor)
    observed = {item["requirement_id"]: item for item in trace["observations"]}
    matrix = trace.get("timeframe_family_matrix")
    if not isinstance(matrix, Mapping):
        raise TraceabilityError("TIMEFRAME_FAMILY_MATRIX_MISSING")
    matrix_hash = str(matrix.get("matrix_hash") or "")
    matrix_body = {key: value for key, value in matrix.items() if key != "matrix_hash"}
    if len(matrix_hash) != 64 or digest(matrix_body) != matrix_hash:
        raise TraceabilityError("TIMEFRAME_FAMILY_MATRIX_HASH_MISMATCH")
    cells = matrix.get("cells")
    if not isinstance(cells, list) or len(cells) != 56:
        raise TraceabilityError("TIMEFRAME_FAMILY_MATRIX_CELL_COUNT")
    matrix_lookup = {}
    for cell in cells:
        if not isinstance(cell, Mapping):
            raise TraceabilityError("TIMEFRAME_FAMILY_MATRIX_CELL_INVALID")
        key = (str(cell.get("timeframe") or ""), str(cell.get("family") or ""))
        if key in matrix_lookup:
            raise TraceabilityError("TIMEFRAME_FAMILY_MATRIX_CELL_DUPLICATE")
        matrix_lookup[key] = cell
    if len(matrix_lookup) != 56:
        raise TraceabilityError("TIMEFRAME_FAMILY_MATRIX_CELL_COUNT")

    universe = trace.get("complete_research_universe")
    if not isinstance(universe, Mapping):
        raise TraceabilityError("COMPLETE_RESEARCH_UNIVERSE_MISSING")
    universe_hash = str(universe.get("universe_hash") or "")
    universe_body = {key: value for key, value in universe.items() if key != "universe_hash"}
    universe_counts = universe.get("counts")
    if (
        len(universe_hash) != 64
        or digest(universe_body) != universe_hash
        or str(trace.get("complete_research_universe_hash") or "") != universe_hash
        or not isinstance(universe_counts, Mapping)
    ):
        raise TraceabilityError("COMPLETE_RESEARCH_UNIVERSE_HASH_MISMATCH")

    rows = []
    slot_map = load_slot_map()
    for slot in slot_map["slots"]:
        states = [observed[rid]["state"] for rid in slot["requirements"]]
        available = all(s == "READY" for s in states) and all(at(factor, p) is not None for p in slot["paths"])
        timeframe_states = {}
        timeframe_canonical_paths = {}
        family = _TIMEFRAME_SLOT_FAMILY.get(slot["id"])
        if family is not None:
            for tf in slot_map["timeframes"]:
                cell = matrix_lookup.get((tf, family))
                if cell is None:
                    raise TraceabilityError("TIMEFRAME_FAMILY_MATRIX_CELL_MISSING")
                state = str(cell.get("state") or "UNKNOWN")
                has_available_path = bool(cell.get("available_paths"))
                timeframe_states[tf] = (
                    "EVIDENCE_AVAILABLE"
                    if state in _MATRIX_AVAILABLE_STATES and has_available_path
                    else "DATA_INSUFFICIENT"
                )
                timeframe_canonical_paths[tf] = tuple(cell.get("available_paths") or ())
            available = any(s == "EVIDENCE_AVAILABLE" for s in timeframe_states.values())
        rows.append({"slot_id": slot["id"], "requirements": slot["requirements"],
                     "canonical_paths": slot["paths"], "state": "EVIDENCE_AVAILABLE" if available else "DATA_INSUFFICIENT",
                     "timeframe_states": timeframe_states,
                     "timeframe_canonical_paths": timeframe_canonical_paths,
                     "projection_state": "NOT_RENDERED", "reason": "DYNAMIC_PRODUCT_NOT_AUTHORIZED"})
    document = {"schema_version": "v25-product-coverage-v1", "manifest_hash": MANIFEST_HASH,
                "runtime_trace_hash": trace["runtime_trace_hash"],
                "method_window_policy_hash": trace.get("method_window_policy_hash"),
                "timeframe_family_matrix_hash": matrix_hash,
                "timeframe_family_matrix_schema_version": matrix.get("schema_version"),
                "complete_research_universe_hash": universe_hash,
                "complete_research_universe_schema_version": universe.get("schema_version"),
                "complete_research_universe_counts": dict(universe_counts),
                "baseline_sha256": BASELINE_SHA256,
                "slots": rows, "rendered": False}
    document["receipt_hash"] = digest(document)
    return document


def _aware(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TraceabilityError("AWARE_TIME_REQUIRED")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TraceabilityError("INVALID_TIME") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise TraceabilityError("AWARE_TIME_REQUIRED")
    return result


def compile_input_coverage(frame: Any, *, metadata: Mapping) -> dict:
    """Read an already supplied normalized daily frame; no package/provider import.

    This validates an explicit normalized share/CNY contract. The caller must
    convert source-native lots before this boundary and retain that provenance.
    A synthetic fixture is never promoted as actual research input.
    """
    import pandas as pd
    from pandas.api.types import is_bool_dtype, is_numeric_dtype

    if not isinstance(frame, pd.DataFrame) or frame.empty or not isinstance(metadata, Mapping):
        raise TraceabilityError("NONEMPTY_FRAME_AND_METADATA_REQUIRED")
    required = {"date", "open", "high", "low", "close", "volume", "data_source"}
    if not frame.columns.is_unique or not required <= set(frame.columns):
        raise TraceabilityError("DAILY_SCHEMA_MISSING_OR_DUPLICATE")
    provider = metadata.get("provider")
    if not isinstance(provider, str) or not provider or set(frame["data_source"].dropna()) != {provider} or frame["data_source"].isna().any():
        raise TraceabilityError("SOURCE_IDENTITY_CONFLICT")
    units = metadata.get("units")
    if not isinstance(units, Mapping) or any(units.get(k) != "CNY" for k in ("open", "high", "low", "close")) or units.get("volume") != "share":
        raise TraceabilityError("UNIT_CONTRACT_MISMATCH")
    for column in ("open", "high", "low", "close", "volume"):
        if is_bool_dtype(frame[column]) or not is_numeric_dtype(frame[column]) or frame[column].map(lambda v: isinstance(v, bool) or not math.isfinite(float(v))).any():
            raise TraceabilityError("NON_FINITE_OR_NON_NUMERIC_FIELD")
    if ((frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame["volume"] < 0).any()
            or (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any()):
        raise TraceabilityError("INVALID_OHLCV")
    if "amount" in frame.columns:
        if units.get("amount") != "CNY" or not is_numeric_dtype(frame["amount"]) or frame["amount"].map(lambda v: not math.isfinite(float(v)) or v < 0).any():
            raise TraceabilityError("AMOUNT_UNIT_OR_VALUE_INVALID")
    try:
        timestamps = pd.to_datetime(frame["date"], errors="raise")
        if timestamps.dt.tz is not None or not timestamps.eq(timestamps.dt.normalize()).all():
            raise TraceabilityError("DAILY_DATE_MUST_NOT_TRUNCATE_INTRADAY_TIME")
        dates = timestamps.dt.date
        target = date.fromisoformat(str(metadata.get("target_date")))
    except (ValueError, TypeError) as exc:
        raise TraceabilityError("SESSION_DATE_INVALID") from exc
    if dates.isna().any() or not dates.is_monotonic_increasing or dates.duplicated().any() or dates.iloc[-1] != target:
        raise TraceabilityError("SESSION_ORDER_OR_TARGET_MISMATCH")
    if metadata.get("completed_bar_only") is not True or metadata.get("timeframe") != "1d":
        raise TraceabilityError("COMPLETED_DAILY_REQUIRED")
    decision = _aware(metadata.get("decision_time"))
    available = _aware(metadata.get("available_at"))
    if available > decision:
        raise TraceabilityError("DATA_AVAILABLE_AFTER_DECISION")
    if target > decision.astimezone(timezone(timedelta(hours=8))).date():
        raise TraceabilityError("TARGET_SESSION_AFTER_DECISION")
    basis = metadata.get("adjustment_basis")
    if basis not in {"raw", "qfq", "hfq"}:
        raise TraceabilityError("PRICE_BASIS_UNKNOWN")
    generation = metadata.get("generation")
    if not isinstance(generation, str) or not generation:
        raise TraceabilityError("GENERATION_REQUIRED")
    factor_hash = metadata.get("factor_action_hash")
    basis_proven = basis == "raw" or (isinstance(factor_hash, str) and len(factor_hash) == 64
                                     and all(c in "0123456789abcdef" for c in factor_hash)
                                     and metadata.get("factor_as_of") == target.isoformat())
    if factor_hash is not None and not (isinstance(factor_hash, str) and len(factor_hash) == 64
                                        and all(c in "0123456789abcdef" for c in factor_hash)):
        raise TraceabilityError("FACTOR_HASH_INVALID")
    synthetic = metadata.get("synthetic") is not False or provider.lower() in {"mock", "synthetic", "fixture"}
    fields = sorted(set(frame.columns) & (required | {"amount"}))
    records = frame[fields].copy()
    records["date"] = dates.map(lambda x: x.isoformat())
    content_hash = digest(records.to_dict(orient="records"))
    rows = []
    for binding in EVIDENCE_BINDINGS + DEFERRED_BINDINGS:
        absent = sorted(set(binding.fields) - set(fields))
        if synthetic or not basis_proven:
            state = "RIGHTS_OR_PIT_BLOCKED"
        elif binding.timeframe in {"asset", "multi", "60m", "30m", "15m", "5m"} and absent:
            state = "OUT_OF_TRIAL_SCOPE"
        elif absent:
            state = "SEPARATE_DATA_OWNER_REQUIRED"
        elif binding.implementation_state != "EXISTING_REUSED":
            state = "CANONICAL_ALGORITHM_GAP"
        else:
            state = "BASE_BYTES_AVAILABLE"
        rows.append({"requirement_id": binding.requirement_id, "state": state, "missing_fields": absent,
                     "method_owner": binding.owner, "method_executed": False, "canonical_path": binding.path})
    document = {"schema_version": "V25_EVIDENCE_INPUT_COVERAGE_RECEIPT_V1", "manifest_version": MANIFEST_VERSION,
                "manifest_hash": MANIFEST_HASH, "observed_columns": fields,
                "observed_dtypes": {k: str(frame[k].dtype) for k in fields}, "declared_units": {k: units.get(k) for k in fields},
                "provider": provider, "generation": generation, "content_sha256": content_hash,
                "target_date": target.isoformat(), "available_at": available.isoformat(),
                "decision_time": decision.isoformat(), "adjustment_basis": basis,
                "factor_action_hash": factor_hash, "basis_identity_declared": basis_proven,
                "synthetic": synthetic, "row_count": len(frame), "coverage": rows,
                "rights_state": "NOT_INDEPENDENTLY_VERIFIED", "pit_admitted": False,
                "product_ready": False, "training_authorized": False, "model_calls": 0}
    document["receipt_hash"] = digest(document)
    return document
