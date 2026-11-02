# -*- coding: utf-8 -*-
"""Narrow replay-safe Chan feature adapter over DSA's confirmed structure owner.

This module deliberately does **not** claim full Chan/CZSC semantics.  It reuses
only already-confirmed pivots and alternating swings from
``price_structure_service`` and projects three bounded primitives:

* confirmed fractal records (the existing pivot confirmations),
* confirmed stroke records (the existing alternating swings), and
* three-stroke price-overlap zone candidates.

It never fetches data, resolves inclusion relations, invents a future pivot,
creates a trading signal, owns a strategy, persists state, or grants learning
or Product authority.  Prefix replay is an acceptance guard, not a backfill
mechanism.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from typing import Any


CHAN_FEATURE_SCHEMA_VERSION = "narrow-chan-feature-v1"
CHAN_PREFIX_REPLAY_SCHEMA_VERSION = "narrow-chan-prefix-replay-v1"
SEMANTICS = "NARROW_CONFIRMED_PIVOT_SWING_OVERLAP_NOT_FULL_CZSC"
NO_WARMUP = None


class ChanFeatureError(ValueError):
    """Raised when a purported READY narrow-Chan artifact is malformed."""


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _base(*, status: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": CHAN_FEATURE_SCHEMA_VERSION,
        "semantics": SEMANTICS,
        "status": status,
        "reason": reason,
        "source_structure_status": None,
        "stock_code": None,
        "timeframe": None,
        "source_end": None,
        "completed_bar_only": True,
        "source_structure_hash": None,
        "fractals": [],
        "strokes": [],
        "overlap_zones": [],
        "full_chan_standard_claim": False,
        "independent_action_authority": False,
        "strategy_admitted": False,
        "learning_admitted": False,
        "data_rights_admitted": False,
        "receipt_hash": None,
    }


def _public_fractal(pivot: Mapping[str, Any]) -> dict[str, Any]:
    body = {
        "kind": _text(pivot.get("kind")).upper(),
        "price": float(pivot["price"]),
        "origin_time": _text(pivot.get("origin_time")),
        "confirmed_at": _text(pivot.get("confirmed_at")),
        "provisional": False,
        "algorithm_version": _text(pivot.get("algorithm_version")),
        "config_hash": _text(pivot.get("config_hash")),
        "invalidation": dict(pivot.get("invalidation") or {}),
    }
    body["fractal_id"] = _digest({key: body[key] for key in (
        "kind", "price", "origin_time", "confirmed_at", "algorithm_version", "config_hash",
    )})
    return body


def _public_stroke(swing: Mapping[str, Any]) -> dict[str, Any]:
    body = {
        "direction": _text(swing.get("direction")).upper(),
        "origin_time": _text(swing.get("origin_time")),
        "confirmed_at": _text(swing.get("confirmed_at")),
        "start_kind": _text(swing.get("start_kind")).upper(),
        "start_price": float(swing["start_price"]),
        "end_kind": _text(swing.get("end_kind")).upper(),
        "end_price": float(swing["end_price"]),
        "price_change_pct": float(swing.get("price_change_pct") or 0.0),
        "provisional": False,
        "algorithm_version": _text(swing.get("algorithm_version")),
        "config_hash": _text(swing.get("config_hash")),
    }
    body["stroke_id"] = _digest({key: body[key] for key in (
        "direction", "origin_time", "confirmed_at", "start_price", "end_price",
        "algorithm_version", "config_hash",
    )})
    return body


def _overlap_zones(strokes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for index in range(len(strokes) - 2):
        group = strokes[index:index + 3]
        lows = [min(float(item["start_price"]), float(item["end_price"])) for item in group]
        highs = [max(float(item["start_price"]), float(item["end_price"])) for item in group]
        lower = max(lows)
        upper = min(highs)
        if lower > upper:
            continue
        body = {
            "start_stroke_id": group[0]["stroke_id"],
            "end_stroke_id": group[-1]["stroke_id"],
            "lower": round(lower, 6),
            "upper": round(upper, 6),
            "confirmed_at": max(_text(item.get("confirmed_at")) for item in group),
            "provisional": False,
            "semantics": "THREE_CONFIRMED_STROKE_PRICE_OVERLAP_CANDIDATE",
        }
        body["zone_id"] = _digest(body)
        zones.append(body)
    return zones


def build_chan_feature_context(price_structure_context: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(price_structure_context, Mapping):
        return _base(status="MISSING", reason="CHAN_SOURCE_STRUCTURE_MISSING")
    source_status = _text(price_structure_context.get("status")).upper()
    base = _base(status="MISSING", reason="CHAN_SOURCE_STRUCTURE_NOT_READY")
    base.update({
        "source_structure_status": source_status or None,
        "stock_code": _text(price_structure_context.get("stock_code")) or None,
        "timeframe": _text(price_structure_context.get("timeframe")) or "daily",
        "source_end": (
            _text(price_structure_context.get("end_date"))
            or _text(price_structure_context.get("target_date"))
            or None
        ),
    })
    if source_status not in {"READY", "PARTIAL"}:
        return base
    pivots = price_structure_context.get("pivots")
    swings = price_structure_context.get("swings")
    if not isinstance(pivots, Sequence) or isinstance(pivots, (str, bytes)):
        return {**base, "status": "UNKNOWN", "reason": "CHAN_SOURCE_PIVOTS_INVALID"}
    if not isinstance(swings, Sequence) or isinstance(swings, (str, bytes)):
        return {**base, "status": "UNKNOWN", "reason": "CHAN_SOURCE_SWINGS_INVALID"}
    if any(
        not isinstance(item, Mapping) or item.get("provisional") is not False
        for item in [*pivots, *swings]
    ):
        return {**base, "status": "UNKNOWN", "reason": "CHAN_SOURCE_CONTAINS_PROVISIONAL_ITEM"}
    try:
        fractals = [_public_fractal(item) for item in pivots]
        strokes = [_public_stroke(item) for item in swings]
    except (KeyError, TypeError, ValueError):
        return {**base, "status": "UNKNOWN", "reason": "CHAN_SOURCE_GEOMETRY_INVALID"}
    if any(
        not item["origin_time"] or not item["confirmed_at"]
        or item["origin_time"] > item["confirmed_at"]
        for item in [*fractals, *strokes]
    ):
        return {**base, "status": "UNKNOWN", "reason": "CHAN_SOURCE_CONFIRMATION_TIME_INVALID"}
    zones = _overlap_zones(strokes)
    source_projection = {
        "schema_version": price_structure_context.get("schema_version"),
        "status": source_status,
        "stock_code": base["stock_code"],
        "timeframe": base["timeframe"],
        "source_end": base["source_end"],
        "fractals": fractals,
        "strokes": strokes,
    }
    document = {
        **base,
        "status": "READY" if strokes else "PARTIAL" if fractals else "MISSING",
        "reason": (
            "CHAN_NARROW_CONFIRMED_STROKES_READY" if strokes
            else "CHAN_NARROW_CONFIRMED_FRACTALS_ONLY" if fractals
            else "CHAN_NO_CONFIRMED_STRUCTURE_ITEMS"
        ),
        "source_structure_hash": _digest(source_projection),
        "fractals": fractals,
        "strokes": strokes,
        "overlap_zones": zones,
    }
    document["receipt_hash"] = _digest({
        key: value for key, value in document.items() if key != "receipt_hash"
    })
    return document


def validate_chan_feature_context(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != CHAN_FEATURE_SCHEMA_VERSION:
        raise ChanFeatureError("CHAN_FEATURE_SCHEMA_INVALID")
    result = dict(value)
    if result.get("status") not in {"READY", "PARTIAL"}:
        return result
    if result.get("semantics") != SEMANTICS or result.get("full_chan_standard_claim") is not False:
        raise ChanFeatureError("CHAN_FEATURE_SCOPE_OVERCLAIM")
    if any(result.get(key) is not False for key in (
        "independent_action_authority", "strategy_admitted", "learning_admitted", "data_rights_admitted",
    )):
        raise ChanFeatureError("CHAN_FEATURE_AUTHORITY_ESCALATION")
    if any(
        item.get("provisional") is not False
        for key in ("fractals", "strokes", "overlap_zones")
        for item in (result.get(key) or [])
        if isinstance(item, Mapping)
    ):
        raise ChanFeatureError("CHAN_FEATURE_PROVISIONAL_ITEM")
    receipt_hash = _text(result.get("receipt_hash"))
    expected = _digest({key: result[key] for key in result if key != "receipt_hash"})
    if len(receipt_hash) != 64 or receipt_hash != expected:
        raise ChanFeatureError("CHAN_FEATURE_RECEIPT_HASH_INVALID")
    return result


def _items_by_id(feature: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return immutable geometry projections for prefix comparison.

    Pivot invalidation is a legitimate later lifecycle update and therefore is
    intentionally excluded.  Origin/confirmation time, direction and prices are
    immutable once confirmed and remain covered.
    """
    result: dict[str, dict[str, Any]] = {}
    fields = {
        "fractals": (
            "fractal_id", "kind", "price", "origin_time", "confirmed_at",
            "algorithm_version", "config_hash", "provisional",
        ),
        "strokes": (
            "stroke_id", "direction", "origin_time", "confirmed_at",
            "start_kind", "start_price", "end_kind", "end_price",
            "algorithm_version", "config_hash", "provisional",
        ),
        "overlap_zones": (
            "zone_id", "start_stroke_id", "end_stroke_id", "lower", "upper",
            "confirmed_at", "semantics", "provisional",
        ),
    }
    for key, names in fields.items():
        id_key = names[0]
        for item in feature.get(key) or []:
            if isinstance(item, Mapping) and _text(item.get(id_key)):
                result[_text(item[id_key])] = {
                    name: item.get(name) for name in names
                }
    return result

def compile_chan_prefix_replay_receipt(features: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    base = {
        "schema_version": CHAN_PREFIX_REPLAY_SCHEMA_VERSION,
        "status": "MISSING",
        "reason": "CHAN_PREFIX_HISTORY_INSUFFICIENT",
        "prefix_count": len(features) if isinstance(features, Sequence) else 0,
        "common_item_mutation_count": 0,
        "future_confirmation_count": 0,
        "strategy_admitted": False,
        "learning_admitted": False,
        "independent_action_authority": False,
        "receipt_hash": None,
    }
    if not isinstance(features, Sequence) or isinstance(features, (str, bytes)) or len(features) < 2:
        return base
    mutations = 0
    future = 0
    previous: dict[str, dict[str, Any]] | None = None
    for raw in features:
        try:
            feature = validate_chan_feature_context(raw)
        except ChanFeatureError:
            return {**base, "status": "UNKNOWN", "reason": "CHAN_PREFIX_FEATURE_INVALID"}
        source_end = _text(feature.get("source_end"))
        current = _items_by_id(feature)
        if any(
            _text(item.get("confirmed_at")) and source_end
            and _text(item.get("confirmed_at")) > source_end
            for item in current.values()
        ):
            future += 1
        if previous is not None:
            for item_id in previous.keys() & current.keys():
                if previous[item_id] != current[item_id]:
                    mutations += 1
        previous = current
    reason = (
        "CHAN_PREFIX_COMMON_CONFIRMED_ITEM_MUTATED" if mutations
        else "CHAN_PREFIX_FUTURE_CONFIRMATION_DETECTED" if future
        else "CHAN_PREFIX_REPLAY_STABLE"
    )
    document = {
        **base,
        "status": "UNKNOWN" if mutations or future else "READY",
        "reason": reason,
        "common_item_mutation_count": mutations,
        "future_confirmation_count": future,
    }
    document["receipt_hash"] = _digest({
        key: value for key, value in document.items() if key != "receipt_hash"
    })
    return document
