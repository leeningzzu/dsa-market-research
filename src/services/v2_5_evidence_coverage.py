# -*- coding: utf-8 -*-
"""Offline coverage and exact-Golden dynamic NO_SEND projection.

Never fetches data, writes a database or sends a notification. Dynamic HTML is
an in-memory text-node projection over the accepted Golden frame only.
Units/rights are explicit attestations, not facts inferred from numeric values.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html import escape as html_escape
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
from typing import Any

from src.services.evidence_traceability_registry import (
    ACCEPTED_DATA_OBJECT_DOMAIN_COUNTS, ACCEPTED_DATA_OBJECT_SUBSET_SHA256,
    ACCEPTED_PRODUCT_SLOT_IDS,
    ACCEPTED_PRODUCT_SLOT_REQUIREMENTS, ACCEPTED_REQUIREMENT_ROLES,
    ACCEPTED_VALUATION_ROUTE_IDS, ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS,
    BASELINE_BYTES, BASELINE_SHA256, COMPLETE_RESEARCH_UNIVERSE_PLANES,
    DEFERRED_BINDINGS, EVIDENCE_BINDINGS,
    MANIFEST_HASH, MANIFEST_VERSION, TIMEFRAMES, TraceabilityError, at,
    compile_complete_research_universe_view, compile_full_domain_data_gate,
    digest, json_wire_equal, load_slot_map, timeframe_ready,
    validate_runtime_trace,
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


def compile_whole_product_scope_gate(
    trace: Mapping,
    *,
    slot_map: Mapping | None = None,
) -> dict:
    """Reject scope compression without pretending to render the Product."""
    if not isinstance(trace, Mapping):
        raise TraceabilityError("WHOLE_PRODUCT_TRACE_INVALID")
    universe = trace.get("complete_research_universe")
    if not isinstance(universe, Mapping):
        raise TraceabilityError("WHOLE_PRODUCT_UNIVERSE_MISSING")
    requirement_rows = universe.get("requirements")
    if not isinstance(requirement_rows, list):
        raise TraceabilityError("WHOLE_PRODUCT_REQUIREMENTS_INVALID")
    requirement_ids = [str(row.get("requirement_id") or "") for row in requirement_rows]
    expected_requirement_ids = frozenset(
        requirement_id for requirement_id, _role in ACCEPTED_REQUIREMENT_ROLES
    )
    if (
        len(requirement_ids) != len(expected_requirement_ids)
        or len(set(requirement_ids)) != len(requirement_ids)
        or frozenset(requirement_ids) != expected_requirement_ids
    ):
        raise TraceabilityError("WHOLE_PRODUCT_REQUIREMENT_SET_MISMATCH")
    expected_roles = dict(ACCEPTED_REQUIREMENT_ROLES)
    expected_plane_by_requirement = {
        requirement_id: plane_name
        for plane_name, plane_requirements in COMPLETE_RESEARCH_UNIVERSE_PLANES
        for requirement_id in plane_requirements
    }
    if any(
        str(row.get("contract_role") or "")
        != expected_roles[str(row.get("requirement_id") or "")]
        or str(row.get("plane") or "")
        != expected_plane_by_requirement[str(row.get("requirement_id") or "")]
        for row in requirement_rows
    ):
        raise TraceabilityError("WHOLE_PRODUCT_REQUIREMENT_BINDING_MISMATCH")
    plane_rows = universe.get("planes")
    if not isinstance(plane_rows, list) or len(plane_rows) != len(
        COMPLETE_RESEARCH_UNIVERSE_PLANES
    ):
        raise TraceabilityError("WHOLE_PRODUCT_PLANE_BINDING_MISMATCH")
    expected_planes = tuple(
        (plane_name, tuple(requirement_ids))
        for plane_name, requirement_ids in COMPLETE_RESEARCH_UNIVERSE_PLANES
    )
    actual_planes = tuple(
        (
            str(row.get("plane") or ""),
            tuple(row.get("requirement_ids") or ()),
        )
        for row in plane_rows
        if isinstance(row, Mapping)
    )
    if actual_planes != expected_planes:
        raise TraceabilityError("WHOLE_PRODUCT_PLANE_BINDING_MISMATCH")

    slots = load_slot_map(slot_map) if slot_map is not None else load_slot_map()
    slot_ids = tuple(str(slot["id"]) for slot in slots["slots"])
    if frozenset(slot_ids) != ACCEPTED_PRODUCT_SLOT_IDS:
        raise TraceabilityError("WHOLE_PRODUCT_SLOT_SET_MISMATCH")
    slot_requirements = frozenset(
        requirement_id
        for slot in slots["slots"]
        for requirement_id in slot["requirements"]
    )
    if slot_requirements != ACCEPTED_PRODUCT_SLOT_REQUIREMENTS:
        raise TraceabilityError("WHOLE_PRODUCT_SLOT_REQUIREMENT_SET_MISMATCH")

    embedded_full_domain_gate = universe.get("full_domain_data_gate")
    full_domain_gate = compile_full_domain_data_gate(
        universe.get("data_objects"),
        universe.get("data_object_admission"),
    )
    if (
        embedded_full_domain_gate is not None
        and not json_wire_equal(embedded_full_domain_gate, full_domain_gate)
    ):
        raise TraceabilityError("WHOLE_PRODUCT_FULL_DOMAIN_GATE_MISMATCH")
    if full_domain_gate.get("full_domain_accounted_pass") is not True:
        raise TraceabilityError("WHOLE_PRODUCT_FULL_DOMAIN_ACCOUNTING_FAILED")
    if dict(full_domain_gate.get("domain_counts") or {}) != ACCEPTED_DATA_OBJECT_DOMAIN_COUNTS:
        raise TraceabilityError("WHOLE_PRODUCT_DOMAIN_COUNTS_MISMATCH")
    valuation_route_ids = tuple(universe.get("valuation_route_ids") or ())
    if (
        len(valuation_route_ids) != len(ACCEPTED_VALUATION_ROUTE_IDS)
        or frozenset(valuation_route_ids) != ACCEPTED_VALUATION_ROUTE_IDS
    ):
        raise TraceabilityError("WHOLE_PRODUCT_VALUATION_ROUTE_SET_MISMATCH")
    atomic_subfields = tuple(universe.get("known_atomic_subfield_obligations") or ())
    if frozenset(atomic_subfields) != ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS:
        raise TraceabilityError("WHOLE_PRODUCT_ATOMIC_SUBFIELD_SET_MISMATCH")

    document = {
        "schema_version": "whole-product-scope-gate-v1",
        "required_requirement_count": len(expected_requirement_ids),
        "accounted_requirement_count": len(requirement_ids),
        "required_slot_count": len(ACCEPTED_PRODUCT_SLOT_IDS),
        "accounted_slot_count": len(slot_ids),
        "required_data_domain_counts": dict(ACCEPTED_DATA_OBJECT_DOMAIN_COUNTS),
        "accounted_data_domain_counts": dict(full_domain_gate["domain_counts"]),
        "required_valuation_route_ids": tuple(sorted(ACCEPTED_VALUATION_ROUTE_IDS)),
        "accounted_valuation_route_ids": tuple(sorted(valuation_route_ids)),
        "required_atomic_subfields": tuple(sorted(
            ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS
        )),
        "unresolved_atomic_subfields": tuple(sorted(
            ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS
        )),
        "full_scope_accounted_pass": True,
        "full_domain_product_ready": bool(full_domain_gate.get("full_domain_product_ready")),
        "atomic_subfield_binding_complete": False,
        "dynamic_product_admitted": False,
        "rendering_authority": False,
        "policy": {
            "global_market_sector_stock_etf_record_are_independent_scopes": True,
            "valuation_classics_risk_trigger_and_missingness_are_not_optional": True,
            "scope_accounting_does_not_imply_data_or_rendering_readiness": True,
        },
    }
    document["gate_hash"] = digest(document)
    return document


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
    whole_product_scope_gate = compile_whole_product_scope_gate(
        trace,
        slot_map=slot_map,
    )
    full_domain_data_gate = universe.get("full_domain_data_gate")
    if not isinstance(full_domain_data_gate, Mapping):
        raise TraceabilityError("FULL_DOMAIN_DATA_GATE_MISSING")
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
                "whole_product_scope_gate": whole_product_scope_gate,
                "full_domain_data_gate": dict(full_domain_data_gate),
                "baseline_sha256": BASELINE_SHA256,
                "slots": rows, "rendered": False}
    document["receipt_hash"] = digest(document)
    return document


V25_DYNAMIC_PRODUCT_SCHEMA_VERSION = "v25-dynamic-no-send-v1"
_V25_CARD_CYCLES = {
    "growth-etf": tuple(f"cycle-{i:02d}" for i in range(14, 21)),
    "dividend-etf": tuple(f"cycle-{i:02d}" for i in range(21, 28)),
    "small-etf": tuple(f"cycle-{i:02d}" for i in range(28, 35)),
    "industrial-stock": tuple(f"cycle-{i:02d}" for i in range(35, 42)),
    "semiconductor-stock": tuple(f"cycle-{i:02d}" for i in range(42, 49)),
    "grid-stock": tuple(f"cycle-{i:02d}" for i in range(49, 56)),
    "watch-x": tuple(f"cycle-{i:02d}" for i in range(56, 63)),
    "watch-y": tuple(f"cycle-{i:02d}" for i in range(63, 70)),
    "watch-etf-z": tuple(f"cycle-{i:02d}" for i in range(70, 77)),
    "watch-w": tuple(f"cycle-{i:02d}" for i in range(77, 84)),
}
_V25_EXPECTED_CARDS = tuple(_V25_CARD_CYCLES)
_V25_EXPECTED_CYCLES = tuple(f"cycle-{i:02d}" for i in range(84))
_V25_TF_LABELS = {
    "monthly": "月线", "weekly": "周线", "daily": "日线",
    "60m": "60分钟", "30m": "30分钟", "15m": "15分钟", "5m": "5分钟",
}
_V25_DYNAMIC_CLASSES = frozenset({
    "subject", "meta", "lead", "mainline", "card-title", "identity",
    "event-alert", "one-line", "why", "cycle-title", "valuation",
    "indicator-box", "flow-box", "synthesis", "attn", "fail", "small",
    "candidate-note", "footer", "status-row", "badge", "action",
})
_V25_SAMPLE_FRAGMENTS = (
    "示例成长科技ETF", "示例红利低波ETF", "示例小盘宽基ETF",
    "工业自动化领导候选", "半导体设备领导候选",
    "自选股X（6008X9）", "自选股Y（6008X8）",
    "自选ETF Z（5108X9）", "自选股W（3008X7）",
    "1.264元", "15.72元", "31.40元", "26.30元",
)
_V25_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class _V25TextFrameParser(HTMLParser):
    """Parse exact Golden text locations without normalising markup bytes."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_offsets = [0]
        self.line_offsets.extend(i + 1 for i, char in enumerate(source) if char == "\n")
        self.nodes: list[dict[str, Any]] = []
        self.text_nodes: list[dict[str, Any]] = []
        self.stack: list[int] = []
        self.mail_index = -1

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        classes = tuple((attr.get("class") or "").split())
        if tag == "div" and "mail" in classes:
            self.mail_index += 1
        index = len(self.nodes)
        self.nodes.append({
            "index": index,
            "tag": tag,
            "id": attr.get("id"),
            "classes": classes,
            "attrs": attr,
            "mail": self.mail_index,
            "parent": self.stack[-1] if self.stack else None,
            "start": self._offset(),
        })
        if tag not in _V25_VOID_TAGS:
            self.stack.append(index)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _V25_VOID_TAGS and self.stack:
            self.stack.pop()

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, -1, -1):
            if self.nodes[self.stack[position]]["tag"] == tag:
                self.stack = self.stack[:position]
                return
        # HTMLParser can report optional/legacy closing tags that did not create
        # a stack node. They remain untouched bytes and do not affect text spans.
        return

    def _record_text(self, raw: str) -> None:
        start = self._offset()
        self.text_nodes.append({
            "start": start,
            "end": start + len(raw),
            "raw": raw,
            "node": self.stack[-1] if self.stack else None,
            "mail": self.mail_index,
        })

    def handle_data(self, data):
        self._record_text(data)

    def handle_entityref(self, name):
        self._record_text(f"&{name};")

    def handle_charref(self, name):
        self._record_text(f"&#{name};")


def _v25_chain(parser: _V25TextFrameParser, node_index: int | None):
    while node_index is not None:
        node = parser.nodes[node_index]
        yield node
        node_index = node["parent"]


def _v25_nearest(parser, node_index, *, tag=None, class_name=None):
    for node in _v25_chain(parser, node_index):
        if tag is not None and node["tag"] == tag:
            return node
        if class_name is not None and class_name in node["classes"]:
            return node
    return None


def _v25_block(parser: _V25TextFrameParser, text_node: Mapping) -> dict:
    chain = tuple(_v25_chain(parser, text_node.get("node")))
    for node in chain:
        if set(node["classes"]) & _V25_DYNAMIC_CLASSES:
            return node
        if node["tag"] in {"p", "li", "td", "th", "h1", "h2", "h3", "h4", "caption"}:
            return node
    for node in chain:
        if node["tag"] in {"div", "span"}:
            return node
    raise TraceabilityError("V25_DYNAMIC_TEXT_BLOCK_MISSING")


def _v25_fixed_block(block: Mapping) -> bool:
    classes = set(block.get("classes") or ())
    return block.get("tag") == "th" or (
        block.get("tag") in {"h2", "h3", "h4"}
        and not classes.intersection({"subject", "card-title", "cycle-title"})
    )


def _v25_plain(value: Any, fallback: str = "数据不足") -> str:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple, set)):
        rows = [_v25_plain(item, "") for item in value]
        rows = [item for item in rows if item]
        return "；".join(rows) or fallback
    if isinstance(value, Mapping):
        return fallback
    text = str(value).strip()
    return text or fallback


def _v25_material(result: Any) -> dict:
    dashboard = getattr(result, "dashboard", None)
    dashboard = dashboard if isinstance(dashboard, Mapping) else {}
    factor = dashboard.get("factor_decision")
    if not isinstance(factor, Mapping):
        raise TraceabilityError("V25_DYNAMIC_FACTOR_MISSING")
    from src.services.factor_decision_summary import validate_investor_brief_binding

    try:
        brief = validate_investor_brief_binding(factor)
    except ValueError as exc:
        raise TraceabilityError("V25_DYNAMIC_CANONICAL_BINDING_INVALID") from exc
    coverage = compile_product_coverage(factor)
    universe = at(factor, "evidence_traceability.complete_research_universe")
    gate = at(universe, "full_domain_data_gate")
    if not isinstance(universe, Mapping) or not isinstance(gate, Mapping):
        raise TraceabilityError("V25_DYNAMIC_FULL_DOMAIN_GATE_MISSING")
    delivery = dashboard.get("research_delivery")
    delivery = dict(delivery) if isinstance(delivery, Mapping) else {}
    mtf = factor.get("multi_timeframe_structure_context")
    mtf = dict(mtf) if isinstance(mtf, Mapping) else {}
    sources = tuple(
        str(item).strip().lower()
        for item in (at(mtf, "source_alignment.sources") or ())
        if str(item).strip()
    )
    synthetic = bool(sources) and all(
        item.startswith("synthetic") or item in {"fixture", "mock", "unit-test-provider"}
        for item in sources
    )
    product_ready = gate.get("full_domain_product_ready") is True
    code = str(getattr(result, "code", "") or "").strip()
    bound_codes = {
        str(value).strip()
        for value in (
            at(factor, "supply_demand_volume_price.completed_bar_context.stock_code"),
            at(factor, "cost_structure_evidence.context.stock_code"),
            at(factor, "price_structure_evidence.context.stock_code"),
            at(factor, "volatility_momentum_evidence.context.stock_code"),
            at(factor, "pattern_trigger_evidence.context.stock_code"),
        )
        if str(value or "").strip()
    }
    if not code or (bound_codes and bound_codes != {code}):
        raise TraceabilityError("V25_DYNAMIC_ASSET_IDENTITY_MISMATCH")
    return {
        "result": result,
        "factor": factor,
        "brief": brief,
        "coverage": coverage,
        "universe": universe,
        "gate": gate,
        "delivery": delivery,
        "mtf": mtf,
        "asset_type": str(factor.get("asset_type") or delivery.get("asset_type") or "stock").lower(),
        "code": code,
        "name": str(getattr(result, "name", "") or getattr(result, "code", "") or "未命名对象").strip(),
        "fact_mode": "SYNTHETIC_TEST" if synthetic else "ADMITTED" if product_ready else "MISSINGNESS_ONLY",
        "product_ready": product_ready,
    }


def _v25_assign(materials: list[dict]) -> dict:
    codes = [item["code"] for item in materials if item["code"]]
    if len(codes) != len(set(codes)):
        raise TraceabilityError("V25_DYNAMIC_DUPLICATE_RESULT")
    groups = {
        "auto_etf_focus": [], "auto_etf_remaining": [],
        "auto_stock_focus": [], "auto_stock_remaining": [], "watch": [],
    }
    for material in materials:
        delivery = material["delivery"]
        source = str(delivery.get("selection_source") or "SPECIFIED_CODES")
        group = str(delivery.get("product_group") or "")
        if source == "AUTO_SCREEN" or group.startswith("AUTO_"):
            asset = "etf" if material["asset_type"] == "etf" else "stock"
            focus = group == f"AUTO_{asset.upper()}_FOCUS"
            groups[f"auto_{asset}_{'focus' if focus else 'remaining'}"].append(material)
        else:
            groups["watch"].append(material)
    for key in groups:
        groups[key].sort(key=lambda item: item["delivery"].get("group_rank") or 999)
    if len(groups["auto_etf_focus"]) > 3 or len(groups["auto_stock_focus"]) > 3:
        raise TraceabilityError("V25_DYNAMIC_FOCUS_CAPACITY_EXCEEDED")
    if len(groups["auto_etf_focus"]) + len(groups["auto_etf_remaining"]) > 10:
        raise TraceabilityError("V25_DYNAMIC_ETF_CAPACITY_EXCEEDED")
    if len(groups["auto_stock_focus"]) + len(groups["auto_stock_remaining"]) > 10:
        raise TraceabilityError("V25_DYNAMIC_STOCK_CAPACITY_EXCEEDED")
    if len(groups["watch"]) > 4:
        raise TraceabilityError("V25_DYNAMIC_WATCHLIST_CAPACITY_EXCEEDED")
    cards: dict[str, dict | None] = {card_id: None for card_id in _V25_EXPECTED_CARDS}
    for card_id, material in zip(
        ("growth-etf", "dividend-etf", "small-etf"), groups["auto_etf_focus"]
    ):
        cards[card_id] = material
    for card_id, material in zip(
        ("industrial-stock", "semiconductor-stock", "grid-stock"),
        groups["auto_stock_focus"],
    ):
        cards[card_id] = material
    for card_id, material in zip(
        ("watch-x", "watch-y", "watch-etf-z", "watch-w"), groups["watch"]
    ):
        cards[card_id] = material
    return {**groups, "cards": cards, "materials": materials}


def _v25_scope_universe(materials: list[dict]) -> dict:
    """Build one conservative Stock+ETF whole-product scope.

    The Product scope is not the first candidate's asset route.  Missing one
    sibling route leaves its obligations UNKNOWN; it never converts ETF into
    Stock N/A (or the reverse).  Multiple candidate states aggregate
    conservatively and do not manufacture a Product-ready object.
    """
    stock = compile_complete_research_universe_view({"asset_type": "stock"})
    etf = compile_complete_research_universe_view({"asset_type": "etf"})
    material_universes = [
        item["universe"]
        for item in materials
        if isinstance(item.get("universe"), Mapping)
    ]

    def aggregate(rows: list[Mapping], *, missing_reason: str) -> tuple[str, str]:
        usable = [
            row for row in rows
            if str(row.get("state") or "UNKNOWN") != "NOT_APPLICABLE"
        ]
        if not usable:
            return "UNKNOWN", missing_reason
        states = tuple(str(row.get("state") or "UNKNOWN") for row in usable)
        state = states[0] if len(set(states)) == 1 else "PARTIAL"
        reasons = tuple(dict.fromkeys(
            str(row.get("reason") or "RUNTIME_OBSERVATION_UNKNOWN")
            for row in usable
        ))
        reason = reasons[0] if len(reasons) == 1 else "MIXED_ASSET_SCOPE:" + ",".join(reasons)
        return state, reason

    requirement_templates = {
        str(row.get("requirement_id") or ""): dict(row)
        for row in stock.get("requirements") or ()
        if isinstance(row, Mapping)
    }
    requirements = []
    for requirement_id, _role in sorted(ACCEPTED_REQUIREMENT_ROLES):
        template = requirement_templates.get(requirement_id)
        if template is None:
            raise TraceabilityError("V25_DYNAMIC_REQUIREMENT_TEMPLATE_MISSING")
        observed = [
            row
            for universe in material_universes
            for row in universe.get("requirements") or ()
            if isinstance(row, Mapping)
            and str(row.get("requirement_id") or "") == requirement_id
        ]
        state, reason = aggregate(
            observed,
            missing_reason="NO_BOUND_DYNAMIC_RESULT_FOR_REQUIREMENT",
        )
        template["state"] = state
        template["reason"] = reason
        requirements.append(template)

    stock_rows = {
        str(row.get("dataset_id") or ""): dict(row)
        for row in stock.get("data_objects") or ()
        if isinstance(row, Mapping)
    }
    etf_rows = {
        str(row.get("dataset_id") or ""): dict(row)
        for row in etf.get("data_objects") or ()
        if isinstance(row, Mapping)
    }
    data_objects = []
    for source_row in stock.get("data_objects") or ():
        if not isinstance(source_row, Mapping):
            raise TraceabilityError("V25_DYNAMIC_DATA_OBJECT_TEMPLATE_INVALID")
        dataset_id = str(source_row.get("dataset_id") or "")
        stock_template = stock_rows.get(dataset_id)
        etf_template = etf_rows.get(dataset_id)
        if stock_template is None or etf_template is None:
            raise TraceabilityError("V25_DYNAMIC_DATA_OBJECT_TEMPLATE_MISSING")
        template = (
            stock_template
            if stock_template.get("state") != "NOT_APPLICABLE"
            else etf_template
        )
        observed = [
            row
            for universe in material_universes
            for row in universe.get("data_objects") or ()
            if isinstance(row, Mapping)
            and str(row.get("dataset_id") or "") == dataset_id
        ]
        state, reason = aggregate(
            observed,
            missing_reason="NO_BOUND_DYNAMIC_RESULT_FOR_DATA_OBJECT",
        )
        row = dict(template)
        row["state"] = state
        row["reason"] = reason
        row["independent_source_value_admitted"] = bool(observed) and all(
            item.get("independent_source_value_admitted") is True
            for item in observed
            if str(item.get("state") or "") != "NOT_APPLICABLE"
        )
        row["strategy_upgrade_authority"] = False
        data_objects.append(row)

    universe = dict(stock)
    universe["requirements"] = requirements
    universe["data_objects"] = data_objects
    universe["full_domain_data_gate"] = compile_full_domain_data_gate(data_objects)
    universe["full_domain_data_gate_hash"] = universe["full_domain_data_gate"]["gate_hash"]
    requirement_rows = universe.get("requirements")
    data_rows = universe.get("data_objects")
    valuation_routes = tuple(universe.get("valuation_route_ids") or ())
    atomic_fields = tuple(universe.get("known_atomic_subfield_obligations") or ())
    if (
        not isinstance(requirement_rows, list)
        or frozenset(
            (str(row.get("requirement_id") or ""), str(row.get("contract_role") or ""))
            for row in requirement_rows
            if isinstance(row, Mapping)
        ) != ACCEPTED_REQUIREMENT_ROLES
        or not isinstance(data_rows, list)
        or len(data_rows) != 76
        or any(str(row.get("state") or "") == "NOT_APPLICABLE" for row in data_rows)
        or frozenset(valuation_routes) != ACCEPTED_VALUATION_ROUTE_IDS
        or frozenset(atomic_fields) != ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS
    ):
        raise TraceabilityError("V25_DYNAMIC_SCOPE_UNIVERSE_MISMATCH")
    return universe


def _v25_scope_obligations(universe: Mapping) -> list[dict[str, Any]]:
    """Compile every accepted whole-product obligation into visible text."""
    rows: list[dict[str, Any]] = []
    for item in sorted(
        universe.get("requirements") or (),
        key=lambda row: str(row.get("requirement_id") or "")
        if isinstance(row, Mapping) else "",
    ):
        if not isinstance(item, Mapping):
            raise TraceabilityError("V25_DYNAMIC_REQUIREMENT_ROW_INVALID")
        identifier = str(item.get("requirement_id") or "")
        role = str(item.get("contract_role") or "")
        plane = str(item.get("plane") or "")
        state = str(item.get("state") or "UNKNOWN")
        reason = _v25_plain(item.get("reason"), "RUNTIME_OBSERVATION_UNKNOWN")
        rows.append({
            "kind": "requirement",
            "id": identifier,
            "role": role,
            "plane": plane,
            "state": state,
            "text": f"研究要求 {identifier}｜{plane}/{role}｜状态 {state}｜原因 {reason}",
        })
    for item in universe.get("data_objects") or ():
        if not isinstance(item, Mapping):
            raise TraceabilityError("V25_DYNAMIC_DATA_OBJECT_ROW_INVALID")
        identifier = str(item.get("dataset_id") or "")
        domain = str(item.get("domain") or "")
        state = str(item.get("state") or "UNKNOWN")
        native_frequency = str(item.get("native_frequency") or "UNKNOWN")
        fact = _v25_plain(
            item.get("fact_or_fields") or item.get("fact"),
            "事实定义缺失",
        )
        consumer = _v25_plain(item.get("product_consumer"), "消费者缺失")
        rows.append({
            "kind": "data_object",
            "id": identifier,
            "domain": domain,
            "state": state,
            "native_frequency": native_frequency,
            "fact": fact,
            "consumer": consumer,
            "text": (
                f"数据对象 {identifier}｜{domain}｜{native_frequency}｜状态 {state}｜"
                f"{fact}｜消费者 {consumer}"
            ),
        })
    for identifier in sorted(ACCEPTED_VALUATION_ROUTE_IDS):
        rows.append({
            "kind": "valuation_route",
            "id": identifier,
            "state": "ACCOUNTED_NOT_AUTOMATICALLY_APPLICABLE",
            "text": (
                f"估值路线 {identifier}｜仅在业务适用、输入版本和来源准入时消费；"
                "不适用时明确N/A"
            ),
        })
    for identifier in sorted(ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS):
        rows.append({
            "kind": "atomic_subfield",
            "id": identifier,
            "state": "UNRESOLVED_MISSINGNESS",
            "text": (
                f"Golden原子字段 {identifier}｜状态 UNRESOLVED_MISSINGNESS｜"
                "不得由宽泛代理静默替代"
            ),
        })
    return rows


def _v25_bind_scope_obligations(
    parser: _V25TextFrameParser,
    grouped: Mapping[int, list[dict]],
    obligations: list[dict[str, Any]],
) -> tuple[dict[int, str], list[dict[str, Any]]]:
    """Bind each whole-product obligation to one exact existing Golden block."""
    reserved = {
        "subject", "meta", "lead", "mainline", "card-title", "identity",
        "one-line", "cycle-title", "synthesis", "footer",
    }
    eligible = [
        block_index
        for block_index in sorted(grouped, key=lambda value: parser.nodes[value]["start"])
        if parser.nodes[block_index]["mail"] == 0
        and parser.nodes[block_index]["tag"] not in {
            "h1", "h2", "h3", "h4", "th", "caption",
        }
        and _v25_semantic_for_block(parser, block_index) not in reserved
    ]
    if len(eligible) < len(obligations):
        raise TraceabilityError("V25_DYNAMIC_SCOPE_LOCATION_CAPACITY_INSUFFICIENT")
    text_by_block: dict[int, str] = {}
    bound_rows: list[dict[str, Any]] = []
    for obligation, block_index in zip(obligations, eligible):
        text_by_block[block_index] = str(obligation["text"])
        bound_rows.append({
            **{key: value for key, value in obligation.items() if key != "text"},
            "location_ids": (f"block-{block_index}",),
        })
    return text_by_block, bound_rows


def _v25_domain_line(material: Mapping | None, index: int) -> str:
    if material is None:
        return "全域对象未绑定；GLOBAL/CN_MARKET/SECTOR/ETF/STOCK/RECORD均按缺失呈现。"
    domains = ("GLOBAL", "CN_MARKET", "SECTOR", "ETF", "STOCK", "RECORD")
    domain = domains[index % len(domains)]
    counts = Counter(
        str(row.get("state") or "UNKNOWN")
        for row in material["universe"].get("data_objects", ())
        if isinstance(row, Mapping) and str(row.get("domain") or "") == domain
    )
    body = " / ".join(
        f"{state} {counts[state]}"
        for state in ("READY", "PARTIAL", "MISSING", "UNKNOWN", "NOT_ADMITTED", "NOT_APPLICABLE")
        if counts[state]
    ) or "无对象状态"
    unresolved = tuple(sorted(
        material["coverage"]["whole_product_scope_gate"].get(
            "unresolved_atomic_subfields"
        ) or ()
    ))
    atomic = (
        "；Golden细字段未绑定：" + "、".join(unresolved)
        if domain == "GLOBAL" and unresolved else ""
    )
    return f"{domain}：{body}{atomic}"


def _v25_missing(material: Mapping | None) -> str:
    if material is None:
        return "本位置无已准入对象；保留原框架，不使用示例值强制填充。"
    if material["fact_mode"] == "MISSINGNESS_ONLY":
        unresolved = len(tuple(material["gate"].get("unresolved_product_dataset_ids") or ()))
        return f"全域Product准入未完成（未解决适用对象 {unresolved}）；只呈现状态和缺失。"
    if material["fact_mode"] == "SYNTHETIC_TEST":
        return "当前为显式合成测试材料，只验证动态投影，不构成真实研究事实。"
    return "已准入事实可呈现；未准入项继续明确缺失。"


def _v25_decision(material: Mapping | None) -> str:
    if material is None:
        return "无对象｜无独立行动权限"
    canonical = material["brief"].get("canonical") or {}
    return (
        f"公开动作 {_v25_plain(canonical.get('public_action') or canonical.get('action'), 'watch')}｜"
        f"硬否决 {'是' if canonical.get('hard_veto') is True else '否'}｜"
        f"理由 {_v25_plain(canonical.get('reason_codes'), '无已绑定理由码')}"
    )


def _v25_timeframe(material: Mapping | None, timeframe: str, detail_index: int) -> str:
    label = _V25_TF_LABELS[timeframe]
    if material is None:
        return f"{label}：对象缺失；不得沿用Golden示例事实。"
    current = at(material["mtf"], f"timeframes.{timeframe}")
    current = current if isinstance(current, Mapping) else {}
    status = str(current.get("status") or at(material["brief"], f"coverage.{timeframe}") or "UNKNOWN")
    role = str(current.get("role") or at(material["brief"], f"timeframe_thesis.{timeframe}.role") or "ROLE_UNAVAILABLE")
    if detail_index == 0:
        if material["fact_mode"] == "MISSINGNESS_ONLY":
            return f"{label}｜运行状态 {status}｜职责 {role}｜Product数值未准入。"
        summary = current.get("summary") or at(material["brief"], f"timeframe_thesis.{timeframe}.summary")
        return f"{label}｜状态 {status}｜职责 {role}｜{_v25_plain(summary, '无可核材料事件')}"
    return (
        f"来源 {_v25_plain(at(current, 'source_alignment.status'), 'SOURCE_UNKNOWN')}｜"
        f"完成至 {_v25_plain(current.get('completed_through') or current.get('latest_bar_date') or current.get('latest_bar_end'), '时间未知')}｜"
        f"原因 {_v25_plain(current.get('reason'), '无额外原因')}｜独立行动权限：否"
    )


def _v25_table_coordinates(parser: _V25TextFrameParser, block: Mapping) -> dict | None:
    cell = block if block.get("tag") == "td" else _v25_nearest(parser, block.get("index"), tag="td")
    if cell is None:
        return None
    row = _v25_nearest(parser, cell["index"], tag="tr")
    table = _v25_nearest(parser, cell["index"], tag="table")
    if row is None or table is None:
        return None
    cells = [
        node for node in parser.nodes
        if node["parent"] == row["index"] and node["tag"] in {"td", "th"}
    ]
    rows = [
        node for node in parser.nodes
        if node["tag"] == "tr" and _v25_nearest(parser, node["index"], tag="table") == table
        and any(child["parent"] == node["index"] and child["tag"] == "td" for child in parser.nodes)
    ]
    tables = [node for node in parser.nodes if node["mail"] == table["mail"] and node["tag"] == "table"]
    return {
        "column": next((i for i, node in enumerate(cells) if node["index"] == cell["index"]), 0),
        "row": next((i for i, node in enumerate(rows) if node["index"] == row["index"]), 0),
        "table": next((i for i, node in enumerate(tables) if node["index"] == table["index"]), 0),
    }


def _v25_structural_label(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" ：:|｜")
    if not cleaned or any(char.isdigit() for char in cleaned) or len(cleaned) > 28:
        return "材料项目"
    return cleaned


def _v25_project_block(
    parser: _V25TextFrameParser,
    block: Mapping,
    original_text: str,
    assignment: Mapping,
    ordinal: int,
    report_date: str,
) -> str:
    chain = tuple(_v25_chain(parser, block["index"]))
    semantic = next((name for node in chain for name in node["classes"] if name in _V25_DYNAMIC_CLASSES), None)
    scope_text = assignment.get("scope_text_by_block", {}).get(block["index"])
    if scope_text is not None:
        return str(scope_text)
    card = next((node for node in chain if "card" in node["classes"]), None)
    cycle = next((node for node in chain if "cycle" in node["classes"]), None)
    pool = next((node for node in chain if "candidate-pool" in node["classes"]), None)
    card_id = card.get("id") if card else None
    material = assignment["cards"].get(card_id) if card_id else None
    mail = int(block["mail"])
    if material is None and mail == 0 and assignment["materials"]:
        material = assignment["materials"][0]
    if semantic == "subject":
        if mail == 0:
            return f"【开盘前市场】全域状态与七周期职责｜{report_date}｜事实和缺失分开"
        if mail == 1:
            return (
                f"【晚间自动发现】ETF重点{len(assignment['auto_etf_focus'])}/3、观察{len(assignment['auto_etf_remaining'])}/7｜"
                f"股票重点{len(assignment['auto_stock_focus'])}/3、观察{len(assignment['auto_stock_remaining'])}/7"
            )
        return f"【我的自选研究】本轮{len(assignment['watch'])}只｜完整证据与缺失"
    if semantic == "meta":
        return f"DSA完整研究｜{report_date}｜NO_SEND｜原V2.5固定框架"
    if semantic in {"lead", "mainline"}:
        if mail == 0:
            return _v25_domain_line(assignment["materials"][0] if assignment["materials"] else None, ordinal)
        if mail == 1:
            return "AUTO允许零或部分候选；筛选排名不是买入权限，最终动作服从canonical和四类独立否决。"
        return "自选/指定代码只投影当前材料变化；无变化或缺失不制造新结论。"
    if semantic == "card-title":
        if material is None:
            return "本位置无已准入研究对象｜不强制填充"
        prefix = "【合成测试】" if material["fact_mode"] == "SYNTHETIC_TEST" else ""
        return f"{prefix}{material['name']}（{material['code']}）｜{_v25_plain(material['brief'].get('one_line_conclusion'), '结论待补齐')}"
    if semantic == "identity":
        if material is None:
            return "对象身份、价格、单位与时点均未绑定"
        return f"{material['name']}（{material['code']}）｜{_v25_plain(material['delivery'].get('asset_identity_text'), '市场/板块待确认')}｜{_v25_missing(material)}"
    if semantic == "event-alert":
        return _v25_decision(material)
    if semantic in {"one-line", "synthesis"}:
        if material is None or material["fact_mode"] == "MISSINGNESS_ONLY":
            return _v25_missing(material)
        return _v25_plain(material["brief"].get("fused_paragraph"), _v25_missing(material))
    if semantic == "why":
        return _v25_plain(at(material, "delivery.candidate_reason") if material else None, _v25_missing(material))
    if cycle is not None:
        timeframe = TIMEFRAMES[int(str(cycle["id"]).split("-")[1]) % len(TIMEFRAMES)]
        if semantic == "cycle-title":
            status = _v25_plain(at(material, f"mtf.timeframes.{timeframe}.status") if material else None, "DATA_INSUFFICIENT")
            return f"{_V25_TF_LABELS[timeframe]}｜{status}｜不具有独立行动权限"
        return _v25_timeframe(material, timeframe, ordinal)
    if semantic == "valuation" or any("valuation" in node["classes"] for node in chain):
        if material is None or material["fact_mode"] == "MISSINGNESS_ONLY":
            return "估值输入、路线、日期或财报版本未完整准入；不能输出合理价。"
        valuation = material["brief"].get("valuation") or {}
        return _v25_plain(valuation.get("summary"), f"估值状态 {_v25_plain(valuation.get('status'), 'UNKNOWN')}")
    if semantic == "indicator-box" or any("indicator-box" in node["classes"] for node in chain):
        timeframe = TIMEFRAMES[ordinal % len(TIMEFRAMES)]
        return _v25_timeframe(material, timeframe, 0)
    if semantic == "flow-box" or any("flow-box" in node["classes"] for node in chain):
        if material is None or material["fact_mode"] == "MISSINGNESS_ONLY":
            return "供需/资金代理未完整准入；不把量价片段解释为机构意图。"
        return _v25_plain(at(material, "factor.supply_demand_volume_price.summary"), "供需材料无独立事件")
    if semantic == "attn":
        return _v25_plain(at(material, "brief.trigger") if material else None, "触发条件未形成")
    if semantic == "fail":
        return _v25_plain(at(material, "brief.invalidation") if material else None, "失效条件材料不足")
    if semantic == "small":
        if material is None:
            return "历史参考与当前概率均不可用。"
        return (
            f"历史参考：{_v25_plain(at(material, 'brief.historical_reference.reason'), '不可用')}｜"
            f"当前概率：{_v25_plain(at(material, 'brief.current_probability.reason'), '未独立校准')}"
        )
    if semantic == "candidate-note":
        return "候选允许为零或部分；未进入重点不等于永久否决。"
    if semantic == "footer":
        return "只呈现当前canonical或诚实缺失；无SMTP、Telegram、数据库或模型效果。"
    coordinates = _v25_table_coordinates(parser, block)
    if coordinates is not None and block["tag"] == "td":
        if pool is not None:
            pools = [node for node in parser.nodes if node["mail"] == mail and "candidate-pool" in node["classes"]]
            pool_index = next(i for i, node in enumerate(pools) if node["index"] == pool["index"])
            candidates = assignment["auto_etf_remaining" if pool_index == 0 else "auto_stock_remaining"]
            candidate = candidates[coordinates["row"]] if coordinates["row"] < len(candidates) else None
            column = coordinates["column"]
            if candidate is None:
                return str(coordinates["row"] + 4) if column == 0 else "本次无已准入候选"
            values = {
                0: str(candidate["delivery"].get("group_rank") or coordinates["row"] + 4),
                1: f"{candidate['name']}（{candidate['code']}）",
                2: _v25_plain(candidate["delivery"].get("screen_score") or candidate["delivery"].get("candidate_score"), "不作为概率"),
                3: _v25_plain(candidate["delivery"].get("candidate_reason"), _v25_decision(candidate)),
                4: _v25_missing(candidate),
                5: _v25_plain(candidate["brief"].get("trigger"), "触发不足"),
                6: _v25_plain(candidate["brief"].get("invalidation"), "失效不足"),
                7: _v25_missing(candidate),
            }
            return values.get(column, _v25_missing(candidate))
        if coordinates["column"] == 0:
            return _v25_structural_label(original_text)
        if card_id:
            return _v25_timeframe(material, TIMEFRAMES[coordinates["row"] % len(TIMEFRAMES)], 0)
        return _v25_domain_line(assignment["materials"][0] if assignment["materials"] else None, coordinates["table"] + coordinates["row"])
    if block["tag"] == "li":
        return "证据、反证、风险、触发和失效必须同时保留；缺失不被技术分抵消。"
    if block["tag"] == "p" and card_id:
        if ordinal % 3 == 0:
            if material is None:
                return _v25_missing(None)
            if material["asset_type"] == "etf":
                return "ETF专属NAV/IOPV、份额、折溢价、点差、跟踪与篮子材料按各自准入状态呈现。"
            return "股票质量成长、行业归属和业务适用估值按财报版本与来源状态呈现。"
        if ordinal % 3 == 1:
            return _v25_plain(at(material, "brief.risk_notes") if material else None, "主要风险材料不足")
        return _v25_missing(material)
    if block["tag"] == "p" or mail == 0:
        return _v25_domain_line(assignment["materials"][0] if assignment["materials"] else None, ordinal)
    return _v25_decision(material) if material else "DATA_INSUFFICIENT｜不保留示例事实"


def _v25_semantic_for_block(
    parser: _V25TextFrameParser,
    block_index: int,
) -> str:
    block = parser.nodes[block_index]
    return next(
        (
            name
            for node in _v25_chain(parser, block_index)
            for name in node["classes"]
            if name in _V25_DYNAMIC_CLASSES
        ),
        block["tag"],
    )


def _v25_slot_block_ids(
    slot_id: str,
    card_id: str,
    rendered_blocks: list[dict[str, Any]],
) -> tuple[str, ...]:
    card_blocks = [
        row for row in rendered_blocks if row.get("card_id") == card_id
    ]
    cycle_ids = _V25_CARD_CYCLES[card_id]
    if slot_id.startswith("detail.timeframe."):
        selected = [
            row for row in card_blocks if row.get("cycle_id") in cycle_ids
        ]
    elif slot_id == "first_screen.conclusion":
        selected = [
            row for row in card_blocks
            if row.get("semantic") in {
                "card-title", "event-alert", "one-line", "synthesis",
            }
        ]
    elif slot_id == "first_screen.price_or_valuation":
        selected = [
            row for row in card_blocks
            if row.get("semantic") in {"identity", "valuation"}
        ]
    elif slot_id == "first_screen.higher_timeframe_thesis":
        selected = [
            row for row in card_blocks
            if row.get("cycle_id") in cycle_ids[:2]
        ]
    elif slot_id == "first_screen.short_term_conflict_or_trigger":
        selected = [
            row for row in card_blocks
            if row.get("cycle_id") in cycle_ids[-3:]
            or row.get("semantic") == "attn"
        ]
    elif slot_id == "first_screen.supply_distribution_risk":
        selected = [
            row for row in card_blocks
            if row.get("semantic") in {"flow-box", "fail", "why"}
        ]
    elif slot_id == "first_screen.trigger":
        selected = [
            row for row in card_blocks if row.get("semantic") == "attn"
        ]
    elif slot_id == "first_screen.invalidation":
        selected = [
            row for row in card_blocks if row.get("semantic") == "fail"
        ]
    elif slot_id == "first_screen.material_risks":
        selected = [
            row for row in card_blocks
            if row.get("semantic") in {"fail", "flow-box", "small"}
        ]
    elif slot_id in {
        "asset.stock.valuation", "asset.etf.underlying_valuation",
    }:
        selected = [
            row for row in card_blocks if row.get("semantic") == "valuation"
        ]
    elif slot_id == "asset.stock.quality_growth":
        selected = [
            row for row in card_blocks
            if row.get("semantic") in {"flow-box", "p"}
            and row.get("cycle_id") is None
        ]
    elif slot_id in {
        "asset.etf.premium_discount", "asset.etf.liquidity_spread",
        "asset.etf.tracking_concentration",
    }:
        selected = [
            row for row in card_blocks
            if row.get("semantic") in {"valuation", "flow-box", "p"}
            and row.get("cycle_id") is None
        ]
    elif slot_id == "audit.coverage":
        selected = [
            row for row in card_blocks
            if row.get("semantic") in {"small", "identity"}
        ]
    elif slot_id == "audit.data_time_quality":
        selected = [
            row for row in card_blocks
            if row.get("semantic") in {"identity", "small"}
        ]
    elif slot_id in {
        "audit.historical_reference", "audit.current_probability",
    }:
        selected = [
            row for row in card_blocks if row.get("semantic") == "small"
        ]
    else:
        selected = []
    block_ids = tuple(dict.fromkeys(
        str(row["block_id"]) for row in selected if row.get("block_id")
    ))
    if not block_ids:
        raise TraceabilityError(
            "V25_DYNAMIC_SLOT_LOCATION_MISSING:" + slot_id + ":" + card_id
        )
    return block_ids


def render_v2_5_dynamic_no_send(
    results: list[Any] | tuple[Any, ...],
    *,
    report_date: str | None = None,
    original: bytes | None = None,
) -> dict:
    """Return an in-memory dynamic copy of the exact V2.5 frame; never send."""
    if not isinstance(results, (list, tuple)):
        raise TraceabilityError("V25_DYNAMIC_RESULTS_INVALID")
    path = (
        Path(__file__).resolve().parents[2]
        / "templates" / "v2_5" / "STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html"
    )
    original = path.read_bytes() if original is None else original
    verify_original_mapping(original)
    source = original.decode("utf-8")
    parser = _V25TextFrameParser(source)
    parser.feed(source)
    mail_count = sum(node["tag"] == "div" and "mail" in node["classes"] for node in parser.nodes)
    card_ids = tuple(
        node["id"] for node in parser.nodes
        if node["tag"] == "div" and "card" in node["classes"] and node["id"]
    )
    cycle_ids = tuple(
        node["id"] for node in parser.nodes
        if node["tag"] == "div" and "cycle" in node["classes"]
    )
    if mail_count != 3 or card_ids != _V25_EXPECTED_CARDS or cycle_ids != _V25_EXPECTED_CYCLES:
        raise TraceabilityError("V25_DYNAMIC_ORIGINAL_STRUCTURE_MISMATCH")
    materials = [_v25_material(result) for result in results]
    assignment = _v25_assign(materials)
    scope_universe = _v25_scope_universe(materials)
    scope_obligations = _v25_scope_obligations(scope_universe)
    report_date = report_date or datetime.now().date().isoformat()
    try:
        date.fromisoformat(report_date)
    except ValueError as exc:
        raise TraceabilityError("V25_DYNAMIC_REPORT_DATE_INVALID") from exc

    grouped: dict[int, list[dict]] = defaultdict(list)
    fixed_text_nodes = 0
    for text_node in parser.text_nodes:
        if text_node["mail"] < 0 or not text_node["raw"].strip():
            continue
        block = _v25_block(parser, text_node)
        if _v25_fixed_block(block):
            fixed_text_nodes += 1
        else:
            grouped[block["index"]].append(text_node)
    ordinal_counter: Counter = Counter()
    block_ordinals: dict[int, int] = {}
    for block_index in sorted(grouped, key=lambda value: parser.nodes[value]["start"]):
        block = parser.nodes[block_index]
        card = _v25_nearest(parser, block_index, class_name="card")
        cycle = _v25_nearest(parser, block_index, class_name="cycle")
        key = (block["mail"], card.get("id") if card else None, cycle.get("id") if cycle else None, block["tag"])
        block_ordinals[block_index] = ordinal_counter[key]
        ordinal_counter[key] += 1
    scope_text_by_block, scope_projection_rows = _v25_bind_scope_obligations(
        parser,
        grouped,
        scope_obligations,
    )
    assignment["scope_text_by_block"] = scope_text_by_block

    replacements: list[tuple[int, int, str]] = []
    rendered_blocks = []
    for block_index, text_nodes in grouped.items():
        block = parser.nodes[block_index]
        primary = next((item for item in text_nodes if item["node"] == block_index), text_nodes[0])
        original_text = "".join(item["raw"] for item in text_nodes).strip()
        projected = _v25_project_block(
            parser, block, original_text, assignment, block_ordinals[block_index], report_date
        ).strip() or "DATA_INSUFFICIENT"
        escaped = html_escape(projected, quote=False)
        for item in text_nodes:
            replacements.append((item["start"], item["end"], escaped if item is primary else ""))
        card = _v25_nearest(parser, block_index, class_name="card")
        cycle = _v25_nearest(parser, block_index, class_name="cycle")
        rendered_blocks.append({
            "block_id": f"block-{block_index}",
            "mail": block["mail"],
            "card_id": card.get("id") if card else None,
            "cycle_id": cycle.get("id") if cycle else None,
            "semantic": _v25_semantic_for_block(parser, block_index),
            "tag": block["tag"],
            "projection_sha256": sha256(escaped.encode("utf-8")).hexdigest(),
        })
    replacements.sort(key=lambda row: row[0])
    if any(left[1] > right[0] for left, right in zip(replacements, replacements[1:])):
        raise TraceabilityError("V25_DYNAMIC_TEXT_SPAN_OVERLAP")
    parts: list[str] = []
    fixed_parts: list[str] = []
    cursor = 0
    for start, end, replacement in replacements:
        fixed = source[cursor:start]
        parts.extend((fixed, replacement))
        fixed_parts.append(fixed)
        cursor = end
    parts.append(source[cursor:])
    fixed_parts.append(source[cursor:])
    output = "".join(parts)
    if any(fragment in output for fragment in _V25_SAMPLE_FRAGMENTS):
        raise TraceabilityError("V25_DYNAMIC_GOLDEN_EXAMPLE_LEAK")
    output_parser = _V25TextFrameParser(output)
    output_parser.feed(output)
    output_cards = tuple(
        node["id"] for node in output_parser.nodes
        if node["tag"] == "div" and "card" in node["classes"] and node["id"]
    )
    output_cycles = tuple(
        node["id"] for node in output_parser.nodes
        if node["tag"] == "div" and "cycle" in node["classes"]
    )
    if output_cards != card_ids or output_cycles != cycle_ids:
        raise TraceabilityError("V25_DYNAMIC_OUTPUT_STRUCTURE_DRIFT")

    slot_projections = []
    for card_id, material in assignment["cards"].items():
        by_slot = {
            row["slot_id"]: row
            for row in material["coverage"]["slots"]
        } if material else {}
        for slot_id in sorted(ACCEPTED_PRODUCT_SLOT_IDS):
            coverage_row = by_slot.get(slot_id)
            state = "RENDERED_EVIDENCE" if (
                material is not None
                and material["fact_mode"] in {"ADMITTED", "SYNTHETIC_TEST"}
                and isinstance(coverage_row, Mapping)
                and coverage_row.get("state") == "EVIDENCE_AVAILABLE"
            ) else "RENDERED_MISSINGNESS"
            slot_projections.append({
                "card_id": card_id,
                "asset_code": material.get("code") if material else None,
                "slot_id": slot_id,
                "projection_state": state,
                "location_ids": _v25_slot_block_ids(
                    slot_id,
                    card_id,
                    rendered_blocks,
                ),
            })
    frame = {
        "mail_count": 3,
        "card_ids": card_ids,
        "cycle_ids": cycle_ids,
        "dynamic_block_count": len(rendered_blocks),
        "dynamic_text_node_count": len(replacements),
        "fixed_text_node_count": fixed_text_nodes,
        "fixed_region_sha256": sha256("".join(fixed_parts).encode("utf-8")).hexdigest(),
    }
    rendered_by_id = {
        str(row["block_id"]): row for row in rendered_blocks
    }
    for row in scope_projection_rows:
        location_id = row["location_ids"][0]
        rendered = rendered_by_id.get(location_id)
        if not rendered:
            raise TraceabilityError("V25_DYNAMIC_SCOPE_LOCATION_NOT_RENDERED")
        row["projection_sha256"] = rendered["projection_sha256"]
    requirement_projections = tuple(
        row for row in scope_projection_rows if row["kind"] == "requirement"
    )
    data_object_projections = tuple(
        row for row in scope_projection_rows if row["kind"] == "data_object"
    )
    valuation_route_projections = tuple(
        row for row in scope_projection_rows if row["kind"] == "valuation_route"
    )
    atomic_subfield_projections = tuple(
        row for row in scope_projection_rows if row["kind"] == "atomic_subfield"
    )
    data_object_catalog_binding_sha256 = sha256(
        json.dumps(
            [
                (
                    row["id"], row["domain"], row.get("fact"),
                    row.get("native_frequency"), row.get("consumer"),
                )
                for row in data_object_projections
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if data_object_catalog_binding_sha256 != ACCEPTED_DATA_OBJECT_SUBSET_SHA256:
        raise TraceabilityError("V25_DYNAMIC_DATA_OBJECT_CATALOG_MISMATCH")
    whole_scope_projection = {
        "requirement_count": len(ACCEPTED_REQUIREMENT_ROLES),
        "requirement_roles": tuple(sorted(ACCEPTED_REQUIREMENT_ROLES)),
        "requirement_projections": requirement_projections,
        "product_slot_count": len(ACCEPTED_PRODUCT_SLOT_IDS),
        "product_slot_ids": tuple(sorted(ACCEPTED_PRODUCT_SLOT_IDS)),
        "data_domain_counts": dict(ACCEPTED_DATA_OBJECT_DOMAIN_COUNTS),
        "data_object_projections": data_object_projections,
        "data_object_catalog_binding_sha256": data_object_catalog_binding_sha256,
        "valuation_route_ids": tuple(sorted(ACCEPTED_VALUATION_ROUTE_IDS)),
        "valuation_route_projections": valuation_route_projections,
        "atomic_subfields": tuple(sorted(ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS)),
        "atomic_subfield_projections": atomic_subfield_projections,
        "atomic_subfields_unresolved": tuple(
            row["id"]
            for row in atomic_subfield_projections
            if row.get("state") != "BOUND"
        ),
        "timeframes": TIMEFRAMES,
        "market_cycle_ids": tuple(f"cycle-{i:02d}" for i in range(14)),
        "asset_cycle_ids": tuple(f"cycle-{i:02d}" for i in range(14, 84)),
        "market_block_ids": tuple(
            row["block_id"]
            for row in rendered_blocks
            if row.get("mail") == 0
        ),
        "truthful_missingness_is_material": True,
    }
    whole_scope_projection["projection_hash"] = digest(whole_scope_projection)
    receipt = {
        "schema_version": V25_DYNAMIC_PRODUCT_SCHEMA_VERSION,
        "authority": "ORIGINAL_GOLDEN_TEXT_NODE_PROJECTION_NO_SEND",
        "baseline_bytes": len(original),
        "baseline_sha256": sha256(original).hexdigest(),
        "output_bytes": len(output.encode("utf-8")),
        "output_sha256": sha256(output.encode("utf-8")).hexdigest(),
        "report_date": report_date,
        "frame_contract": frame,
        "frame_contract_hash": digest(frame),
        "whole_scope_projection": whole_scope_projection,
        "envelopes": {
            "MARKET": {"mail_index": 0, "cycle_ids": tuple(f"cycle-{i:02d}" for i in range(14))},
            "AUTO": {
                "mail_index": 1,
                "focus_etf_count": len(assignment["auto_etf_focus"]),
                "remaining_etf_count": len(assignment["auto_etf_remaining"]),
                "focus_stock_count": len(assignment["auto_stock_focus"]),
                "remaining_stock_count": len(assignment["auto_stock_remaining"]),
            },
            "WATCHLIST": {"mail_index": 2, "result_count": len(assignment["watch"])},
        },
        "results": [{
            "code": item["code"],
            "asset_type": item["asset_type"],
            "fact_mode": item["fact_mode"],
            "full_domain_product_ready": item["product_ready"],
            "runtime_trace_hash": item["coverage"].get("runtime_trace_hash"),
            "canonical_decision_hash": at(item["brief"], "canonical_binding.canonical_decision_hash"),
        } for item in materials],
        "rendered_blocks": rendered_blocks,
        "slot_projections": slot_projections,
        "slot_projection_count": len(slot_projections),
        "all_accepted_slot_roles_projected": all(
            {row["slot_id"] for row in slot_projections if row["card_id"] == card_id}
            == ACCEPTED_PRODUCT_SLOT_IDS
            and all(
                row.get("location_ids")
                for row in slot_projections
                if row["card_id"] == card_id
            )
            for card_id in card_ids
        ),
        "all_whole_scope_obligations_projected": (
            frozenset(
                (row["id"], row["role"])
                for row in requirement_projections
            ) == ACCEPTED_REQUIREMENT_ROLES
            and len(data_object_projections) == 76
            and Counter(row["domain"] for row in data_object_projections)
            == Counter(ACCEPTED_DATA_OBJECT_DOMAIN_COUNTS)
            and frozenset(row["id"] for row in valuation_route_projections)
            == ACCEPTED_VALUATION_ROUTE_IDS
            and frozenset(row["id"] for row in atomic_subfield_projections)
            == ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS
            and all(
                row.get("location_ids") and row.get("projection_sha256")
                for row in scope_projection_rows
            )
            and whole_scope_projection["product_slot_count"] == 27
            and len(whole_scope_projection["market_cycle_ids"]) == 14
            and len(whole_scope_projection["asset_cycle_ids"]) == 70
        ),
        "all_input_facts_product_ready": (
            bool(materials)
            and all(item["product_ready"] for item in materials)
            and all(
                row.get("state") in {"READY", "NOT_APPLICABLE"}
                for row in data_object_projections
            )
            and not whole_scope_projection["atomic_subfields_unresolved"]
        ),
        "dynamic_product_projection_admitted": True,
        "dynamic_fidelity_proven": True,
        "rendered": True,
        "no_send": True,
        "delivery_authority": False,
        "storage_authority": False,
        "learning_authority": False,
        "model_calls": 0,
        "network_calls": 0,
        "database_writes": 0,
    }
    receipt["receipt_hash"] = digest(receipt)
    return {"html": output, "receipt": receipt}


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
