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
from numbers import Real
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
    compile_complete_research_universe_view, compile_foundation_evidence_receipt,
    compile_full_domain_data_gate,
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
    document["foundation_evidence_receipt"] = compile_foundation_evidence_receipt(
        factor,
        product_coverage=document,
    )
    document["receipt_hash"] = digest(document)
    return document


V25_DYNAMIC_PRODUCT_SCHEMA_VERSION = "v25-dynamic-no-send-v1"
STAGE1_VERTICAL_PRODUCT_RECEIPT_VERSION = (
    "stage1-current-v25-real-product-vertical-v1"
)
_STAGE1_DIRECT_SLOT_CONSUMERS = frozenset({
    "first_screen.conclusion",
    "first_screen.price_or_valuation",
    "first_screen.supply_distribution_risk",
    "first_screen.trigger",
    "first_screen.invalidation",
    "first_screen.material_risks",
    "asset.stock.quality_growth",
    "asset.stock.valuation",
    "asset.etf.underlying_valuation",
    "asset.etf.premium_discount",
    "asset.etf.liquidity_spread",
    "asset.etf.tracking_concentration",
    "audit.coverage",
    "audit.data_time_quality",
    "audit.historical_reference",
    "audit.current_probability",
})
_STAGE1_TIMEFRAME_COMPOSITE_SLOT_CONSUMERS = frozenset({
    "first_screen.higher_timeframe_thesis",
    "first_screen.short_term_conflict_or_trigger",
})
_STAGE1_TIMEFRAME_SLOT_PREFIX = "detail.timeframe."

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
_V25_FAMILY_REQUIREMENTS = (
    ("市场/行业环境", "REGIME"),
    ("趋势/相对强弱", "TREND_RS"),
    ("供需/量价", "SUPPLY"),
    ("成本", "COST"),
    ("价格结构", "STRUCTURE"),
    ("波动/动量", "MOMENTUM"),
    ("形态/触发", "PATTERN"),
    ("多周期", "MTF"),
)
_V25_DOMAIN_LABELS = {
    "GLOBAL": "全球背景",
    "CN_MARKET": "A股市场",
    "SECTOR": "行业",
    "ETF": "ETF专项",
    "STOCK": "股票专项",
    "RECORD": "记录与学习",
}
_V25_ETF_CARD_IDS = frozenset({
    "growth-etf", "dividend-etf", "small-etf", "watch-etf-z",
})
_V25_STOCK_CARD_IDS = frozenset({
    "industrial-stock", "semiconductor-stock", "grid-stock",
    "watch-x", "watch-y", "watch-w",
})
_V25_INLINE_ROLE_LABELS = frozenset({
    "现在怎么做？", "多数股票跟上了吗？", "资金是否支持？",
    "今天为什么只做精选：", "今天的结论：", "优先顺序：",
    "数据截至：", "上证综指结论：", "宽度结论：", "全球合成：",
    "第一优先：", "筛选依据：", "龙头候选依据：",
    "底层资产估值：", "技术关注区：", "跨周期合成：",
    "关注/触发", "转弱/失效", "主要风险：", "交易合理参考价",
})
_V25_CARD_ROLES = {
    "growth-etf": "ETF重点空位1",
    "dividend-etf": "ETF重点空位2",
    "small-etf": "ETF重点空位3",
    "industrial-stock": "股票重点空位1",
    "semiconductor-stock": "股票重点空位2",
    "grid-stock": "股票重点空位3",
    "watch-x": "自选股票位1",
    "watch-y": "自选股票位2",
    "watch-etf-z": "自选ETF位",
    "watch-w": "自选股票位3",
}
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
    trace = validate_runtime_trace(factor)
    coverage = compile_product_coverage(factor)
    universe = trace.get("complete_research_universe")
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
    current_product_admitted_ids = tuple(
        str(item)
        for item in (gate.get("current_product_admitted_dataset_ids") or ())
    )
    fact_mode = (
        "SYNTHETIC_TEST"
        if synthetic
        else "ADMITTED"
        if product_ready
        else "PARTIALLY_ADMITTED"
        if current_product_admitted_ids
        else "MISSINGNESS_ONLY"
    )
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
        "trace": trace,
        "universe": universe,
        "gate": gate,
        "delivery": delivery,
        "mtf": mtf,
        "asset_type": str(factor.get("asset_type") or delivery.get("asset_type") or "stock").lower(),
        "code": code,
        "name": str(getattr(result, "name", "") or getattr(result, "code", "") or "未命名对象").strip(),
        "fact_mode": fact_mode,
        "product_ready": product_ready,
        "current_product_admitted_dataset_ids": current_product_admitted_ids,
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
    scope_as_of_values = tuple(sorted({
        str(at(material, "factor.research_as_of") or "").strip()
        for material in materials
        if str(at(material, "factor.research_as_of") or "").strip()
    }))
    scope_operation_ids = tuple(sorted({
        str(at(material, "factor.research_operation_id") or "").strip()
        for material in materials
        if str(at(material, "factor.research_operation_id") or "").strip()
    }))
    scope_intended_uses = tuple(sorted({
        str(at(material, "factor.research_intended_use") or "").strip()
        for material in materials
        if str(at(material, "factor.research_intended_use") or "").strip()
    }))
    if len(scope_as_of_values) > 1:
        raise TraceabilityError("V25_DYNAMIC_RESEARCH_ASOF_MISMATCH")
    if len(scope_operation_ids) > 1:
        raise TraceabilityError("V25_DYNAMIC_RESEARCH_OPERATION_MISMATCH")
    if len(scope_intended_uses) > 1:
        raise TraceabilityError("V25_DYNAMIC_RESEARCH_INTENDED_USE_MISMATCH")

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

    def observed_data_rows(dataset_id: str) -> list[dict]:
        observed_rows: list[dict] = []
        for material in materials:
            universe = material.get("universe")
            if not isinstance(universe, Mapping):
                continue
            admitted_ids = {
                str(item)
                for item in (
                    at(material, "gate.current_product_admitted_dataset_ids") or ()
                )
            }
            admission_lookup = {
                str(item.get("dataset_id") or ""): item
                for item in (
                    at(universe, "data_object_admission.objects") or ()
                )
                if isinstance(item, Mapping)
            }
            for candidate in universe.get("data_objects") or ():
                if (
                    not isinstance(candidate, Mapping)
                    or str(candidate.get("dataset_id") or "") != dataset_id
                ):
                    continue
                row = dict(candidate)
                if dataset_id in admitted_ids:
                    admission = admission_lookup.get(dataset_id)
                    admission_hash = (
                        str(admission.get("admission_hash") or "")
                        if isinstance(admission, Mapping) else ""
                    )
                    if (
                        not isinstance(admission, Mapping)
                        or admission.get("product_admitted") is not True
                        or len(admission_hash) != 64
                    ):
                        raise TraceabilityError(
                            "V25_DYNAMIC_PRODUCT_ADMISSION_IDENTITY_MISSING:"
                            + dataset_id
                        )
                    row.update({
                        "state": "READY",
                        "reason": "PRODUCT_OPERATION_ADMITTED",
                        "independent_source_value_admitted": True,
                        "product_admission_hash": admission_hash,
                    })
                observed_rows.append(row)
        return observed_rows

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
        observed = observed_data_rows(dataset_id)
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
        row["product_admission_hashes"] = tuple(sorted({
            str(item.get("product_admission_hash") or "")
            for item in observed
            if len(str(item.get("product_admission_hash") or "")) == 64
        }))
        if (
            str(row.get("domain") or "") in {
                "GLOBAL", "CN_MARKET", "SECTOR",
            }
            and state == "READY"
            and len(row["product_admission_hashes"]) != 1
        ):
            row["state"] = "PARTIAL"
            row["reason"] = "CROSS_MATERIAL_SHARED_DATA_IDENTITY_DRIFT"
            row["independent_source_value_admitted"] = False
        row["strategy_upgrade_authority"] = False
        data_objects.append(row)

    atomic_subfield_rows = []
    for identifier in sorted(ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS):
        observed_admissions = []
        observed_sources = []
        for material in materials:
            universe = material.get("universe")
            admissions = (
                at(universe, "atomic_subfield_admission.objects")
                if isinstance(universe, Mapping) else ()
            ) or ()
            row = next(
                (
                    item for item in admissions
                    if isinstance(item, Mapping)
                    and str(item.get("dataset_id") or "") == identifier
                ),
                None,
            )
            if isinstance(row, Mapping):
                observed_admissions.append(row)
            source = next(
                (
                    item for item in (
                        universe.get("atomic_subfield_obligations")
                        if isinstance(universe, Mapping) else ()
                    ) or ()
                    if isinstance(item, Mapping)
                    and str(item.get("dataset_id") or "") == identifier
                    and str(item.get("state") or "") in {"READY", "PARTIAL"}
                ),
                None,
            )
            if isinstance(source, Mapping):
                observed_sources.append(source)
        admission_hashes = tuple(sorted({
            str(row.get("admission_hash") or "")
            for row in observed_admissions
            if (
                row.get("product_admitted") is True
                and len(str(row.get("admission_hash") or "")) == 64
            )
        }))
        source_fact_hashes = tuple(sorted({
            str(row.get("source_fact_hash") or "")
            for row in observed_sources
            if len(str(row.get("source_fact_hash") or "")) == 64
        }))
        fact_payloads = [
            dict(row.get("facts") or {}) for row in observed_sources
            if isinstance(row.get("facts"), Mapping) and row.get("facts")
        ]
        bound = bool(materials) and (
            len(observed_admissions) == len(materials)
            and len(observed_sources) == len(materials)
            and all(
                row.get("product_admitted") is True
                for row in observed_admissions
            )
            and len(admission_hashes) == 1
            and len(source_fact_hashes) == 1
            and len(fact_payloads) == len(materials)
            and all(digest(value) == source_fact_hashes[0] for value in fact_payloads)
        )
        facts = fact_payloads[0] if bound else {}
        atomic_subfield_rows.append({
            "id": identifier,
            "state": "BOUND" if bound else "UNRESOLVED_MISSINGNESS",
            "product_admission_hashes": admission_hashes,
            "source_fact_hash": source_fact_hashes[0] if bound else None,
            "facts": facts,
            "fact_summary": _v25_atomic_fact_summary(identifier, facts) if bound else None,
            "reason": (
                "EXACT_ATOMIC_PRODUCT_ADMISSION_AND_FACT_BOUND"
                if bound else "EXACT_ATOMIC_PRODUCT_ADMISSION_OR_FACT_MISSING_OR_DRIFTED"
            ),
        })

    universe = dict(stock)
    universe["requirements"] = requirements
    universe["data_objects"] = data_objects
    universe["atomic_subfield_admission_rows"] = atomic_subfield_rows
    universe["scope_research_as_of_values"] = scope_as_of_values
    universe["scope_research_operation_ids"] = scope_operation_ids
    universe["scope_research_intended_uses"] = scope_intended_uses
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


def _v25_atomic_fact_summary(identifier: str, facts: Mapping[str, Any]) -> str:
    """Render one admitted atomic fact without treating a binding as a value."""
    if not isinstance(facts, Mapping) or not facts:
        return ""
    labels = {
        "GLOBAL_US_ACTUAL_INFLATION": "美国整体通胀",
        "GLOBAL_US_CORE_INFLATION": "美国核心通胀",
        "GLOBAL_US_SERVICE_INFLATION": "美国服务通胀",
        "GLOBAL_US_5Y_INFLATION_EXPECTATION": "美国5年通胀预期",
        "GLOBAL_SOX_INDEX": "费城半导体指数",
        "GLOBAL_TECH_ETF_FLOW": "美国科技ETF资金流",
        "GLOBAL_SEMICONDUCTOR_SOFTWARE_RELATIVE": "半导体/软件相对强弱",
    }
    label = labels.get(identifier, identifier)
    period = _v25_plain(
        facts.get("reference_period") or facts.get("as_of")
        or facts.get("completed_through"),
        "时点未标明",
    )
    released = _v25_plain(
        facts.get("release_published_at") or facts.get("published_at"),
        "发布时间未标明",
    )
    if isinstance(facts.get("yoy_pct"), (int, float)):
        value = f"同比 {float(facts['yoy_pct']):.2f}%"
    elif isinstance(facts.get("value"), (int, float)):
        unit = _v25_plain(facts.get("value_unit"), "")
        value = f"{float(facts['value']):.4g}{(' ' + unit) if unit else ''}"
    elif isinstance(facts.get("index_value"), (int, float)):
        value = f"指数 {float(facts['index_value']):.4g}"
    else:
        value = "已绑定事实缺少可显示数值"
    return f"{label}：{value}（{period}；发布 {released}）"


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
            "product_admission_hashes": tuple(
                str(value)
                for value in item.get("product_admission_hashes") or ()
                if len(str(value)) == 64
            ),
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
    atomic_lookup = {
        str(item.get("id") or ""): item
        for item in universe.get("atomic_subfield_admission_rows") or ()
        if isinstance(item, Mapping)
    }
    for identifier in sorted(ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS):
        atomic = atomic_lookup.get(identifier, {})
        state = str(atomic.get("state") or "UNRESOLVED_MISSINGNESS")
        fact_summary = str(atomic.get("fact_summary") or "").strip()
        source_fact_hash = str(atomic.get("source_fact_hash") or "").strip()
        rows.append({
            "kind": "atomic_subfield",
            "id": identifier,
            "state": state,
            "product_admission_hashes": tuple(
                str(value)
                for value in atomic.get("product_admission_hashes") or ()
                if len(str(value)) == 64
            ),
            "source_fact_hash": (
                source_fact_hash if len(source_fact_hash) == 64 else None
            ),
            "facts": dict(atomic.get("facts") or {}),
            "fact_summary": fact_summary or None,
            "text": (
                fact_summary
                if state == "BOUND" and fact_summary
                else f"Golden原子字段 {identifier}｜状态 {state}｜不得由宽泛代理静默替代"
            ),
        })
    return rows


def _v25_node_text(parser: _V25TextFrameParser, node_index: int) -> str:
    return " ".join(
        "".join(
            item["raw"]
            for item in parser.text_nodes
            if item.get("node") == node_index
        ).split()
    ).strip()


def _v25_section_heading(parser: _V25TextFrameParser, block_index: int) -> str:
    section = _v25_nearest(parser, block_index, class_name="section")
    if section is None:
        return ""
    heading = next(
        (
            node for node in parser.nodes
            if node.get("parent") == section["index"] and node.get("tag") == "h2"
        ),
        None,
    )
    return _v25_node_text(parser, heading["index"]) if heading else ""


def _v25_audit_pool_key(obligation: Mapping) -> str:
    kind = str(obligation.get("kind") or "")
    if kind == "data_object":
        return str(obligation.get("domain") or "").lower()
    if kind == "valuation_route":
        return "valuation"
    if kind == "atomic_subfield":
        return "global"
    identifier = str(obligation.get("id") or "")
    if identifier == "GLOBAL":
        return "global"
    if identifier in {"BREADTH", "REGIME"}:
        return "cn_market"
    if identifier == "ETF_SPECIFIC":
        return "etf"
    if identifier == "QUALITY":
        return "stock"
    if identifier == "VALUATION":
        return "valuation"
    if identifier in {"DECISION", "RISK_REWARD"}:
        return "decision"
    return "technical"


def _v25_bind_scope_obligations(
    parser: _V25TextFrameParser,
    grouped: Mapping[int, list[dict]],
    obligations: list[dict[str, Any]],
    assignment: Mapping,
) -> tuple[dict[int, str], list[dict[str, Any]]]:
    """Bind scope to semantically related blocks without dumping registry rows.

    The email remains an investor-facing Product.  Exact IDs, facts, frequencies,
    consumers and hashes stay in the machine receipt; the body shows concise,
    role-correct summaries.  Multiple obligations may therefore share one
    semantically correct section location.
    """
    pools: dict[str, list[int]] = defaultdict(list)
    fallback: list[int] = []
    for block_index in sorted(grouped, key=lambda value: parser.nodes[value]["start"]):
        block = parser.nodes[block_index]
        if _v25_fixed_block(block):
            continue
        fallback.append(block_index)
        semantic = _v25_semantic_for_block(parser, block_index)
        heading = _v25_section_heading(parser, block_index)
        card = _v25_nearest(parser, block_index, class_name="card")
        card_id = card.get("id") if card else None
        material = assignment.get("cards", {}).get(card_id) if card_id else None
        asset_type = str(material.get("asset_type") or "") if isinstance(material, Mapping) else ""
        if not asset_type and card_id in _V25_ETF_CARD_IDS:
            asset_type = "etf"
        elif not asset_type and card_id in _V25_STOCK_CARD_IDS:
            asset_type = "stock"

        if block["mail"] == 0:
            pools["cn_market"].append(block_index)
            if "全球背景" in heading:
                pools["global"].append(block_index)
            if "优先看哪些行业" in heading:
                pools["sector"].append(block_index)
            if "数据时点与质量" in heading:
                pools["record"].append(block_index)
        if asset_type:
            pools[asset_type].append(block_index)
            pools["asset"].append(block_index)
        if semantic == "valuation" or any(
            "valuation" in node["classes"]
            for node in _v25_chain(parser, block_index)
        ):
            pools["valuation"].append(block_index)
        if semantic in {"indicator-box", "cycle-title", "p", "flow-box"}:
            pools["technical"].append(block_index)
        if semantic in {"event-alert", "one-line", "synthesis", "attn", "fail"}:
            pools["decision"].append(block_index)

    if not fallback:
        raise TraceabilityError("V25_DYNAMIC_SCOPE_LOCATION_CAPACITY_INSUFFICIENT")
    counters: Counter = Counter()
    bound_rows: list[dict[str, Any]] = []
    scope_text_by_block: dict[int, list[str]] = defaultdict(list)
    for obligation in obligations:
        key = _v25_audit_pool_key(obligation)
        options = pools.get(key) or pools.get("asset") or fallback
        block_index = options[counters[key] % len(options)]
        counters[key] += 1
        bound_rows.append({
            **{name: value for name, value in obligation.items() if name != "text"},
            "location_ids": (f"block-{block_index}",),
            "consumer_projection": "MACHINE_RECEIPT_WITH_ROLE_SUMMARIZED_EMAIL",
        })
        if (
            obligation.get("kind") == "atomic_subfield"
            and obligation.get("state") == "BOUND"
            and str(obligation.get("fact_summary") or "").strip()
            and len(str(obligation.get("source_fact_hash") or "")) == 64
        ):
            scope_text_by_block[block_index].append(
                str(obligation["fact_summary"]).strip()
            )
    return {
        block_index: "；".join(values)
        for block_index, values in scope_text_by_block.items()
    }, bound_rows


def _v25_universe(value: Mapping | None) -> Mapping | None:
    if not isinstance(value, Mapping):
        return None
    if isinstance(value.get("data_objects"), list):
        return value
    universe = value.get("universe")
    return universe if isinstance(universe, Mapping) else None


def _v25_domain_line(value: Mapping | None, index: int) -> str:
    universe = _v25_universe(value)
    if universe is None:
        return "全域材料未绑定；全球、A股市场、行业、股票、ETF和记录均按缺失呈现。"
    domains = ("GLOBAL", "CN_MARKET", "SECTOR", "ETF", "STOCK", "RECORD")
    domain = domains[index % len(domains)]
    counts = Counter(
        str(row.get("state") or "UNKNOWN")
        for row in universe.get("data_objects", ())
        if isinstance(row, Mapping) and str(row.get("domain") or "") == domain
    )
    body = "、".join(
        f"{state} {counts[state]}"
        for state in ("READY", "PARTIAL", "MISSING", "UNKNOWN", "NOT_ADMITTED", "NOT_APPLICABLE")
        if counts[state]
    ) or "无对象状态"
    atomic_rows = tuple(
        row for row in universe.get("atomic_subfield_admission_rows") or ()
        if isinstance(row, Mapping)
    )
    unresolved = tuple(sorted(
        str(row.get("id") or "") for row in atomic_rows
        if row.get("state") != "BOUND"
    ))
    admitted_summaries = tuple(
        str(row.get("fact_summary") or "").strip() for row in atomic_rows
        if row.get("state") == "BOUND" and str(row.get("fact_summary") or "").strip()
    )
    atomic = ""
    if domain == "GLOBAL":
        if admitted_summaries:
            atomic += "；" + "；".join(admitted_summaries)
        if unresolved:
            atomic += f"；另有{len(unresolved)}项全球细字段待独立绑定"
    return f"{_V25_DOMAIN_LABELS[domain]}：{body}{atomic}"


def _v25_requirement_row(universe: Mapping | None, requirement_id: str) -> Mapping:
    if not isinstance(universe, Mapping):
        return {}
    return next(
        (
            row for row in universe.get("requirements", ())
            if isinstance(row, Mapping)
            and str(row.get("requirement_id") or "") == requirement_id
        ),
        {},
    )


def _v25_state_explanation(state: str, *, object_missing: bool = False) -> str:
    if object_missing:
        return "对象尚未绑定，不能沿用示例事实。"
    return {
        "READY": "当前证据已准入，可进入合成但不单独决定动作。",
        "PARTIAL": "只有部分材料，保留冲突和缺失，不独立行动。",
        "MISSING": "当前输出未形成，不能用其他周期或指标补齐。",
        "UNKNOWN": "当前事实或来源未准入，维持等待。",
        "NOT_ADMITTED": "方法或来源尚未准入，不进入投票和结论。",
        "NOT_APPLICABLE": "对本资产不适用，不作为失败或负面票。",
    }.get(state, "当前证据不足，维持等待。")


def _v25_indicator_family_line(
    material: Mapping | None,
    scope_universe: Mapping,
    ordinal: int,
) -> str:
    label, requirement_id = _V25_FAMILY_REQUIREMENTS[ordinal % len(_V25_FAMILY_REQUIREMENTS)]
    object_missing = material is None
    universe = _v25_universe(material) or scope_universe
    row = _v25_requirement_row(universe, requirement_id)
    state = str(row.get("state") or ("MISSING" if object_missing else "UNKNOWN"))
    if (
        not object_missing
        and str(material.get("fact_mode") or "") in {
            "MISSINGNESS_ONLY", "PARTIALLY_ADMITTED",
        }
        and state in {"READY", "PARTIAL"}
    ):
        explanation = (
            "方法输出已形成；仅获Product准入的叶事实可展示，"
            "本族状态不单独升级动作。"
        )
    else:
        explanation = _v25_state_explanation(state, object_missing=object_missing)
    return f"{label}｜{state}｜{explanation}"


def _v25_missing(material: Mapping | None) -> str:
    if material is None:
        return "本位置没有已准入对象；保留框架，不沿用示例证券、价格或结论。"
    if material["fact_mode"] == "MISSINGNESS_ONLY":
        unresolved = len(tuple(material["gate"].get("unresolved_product_dataset_ids") or ()))
        return (
            f"全域Product尚未准入（{unresolved}项当前适用材料待核；未来记录另计）；"
            "当前只呈现状态、缺失和WAIT。"
        )
    if material["fact_mode"] == "PARTIALLY_ADMITTED":
        admitted = len(tuple(material["gate"].get("current_product_admitted_dataset_ids") or ()))
        unresolved = len(tuple(material["gate"].get("unresolved_product_dataset_ids") or ()))
        return (
            f"当前Product部分准入（{admitted}项事实可用，{unresolved}项待核）；"
            "仅显示已准入叶事实，其余保持缺失，结论仍受canonical与veto约束。"
        )
    if material["fact_mode"] == "SYNTHETIC_TEST":
        return "当前为显式合成测试材料，只验证动态投影，不构成真实研究事实。"
    return "已准入事实可呈现；未准入项继续明确缺失。"


def _v25_decision(material: Mapping | None) -> str:
    if material is None:
        return "当前无已准入对象｜无独立行动权限"
    canonical = material["brief"].get("canonical") or {}
    return (
        f"公开动作 {_v25_plain(canonical.get('public_action') or canonical.get('action'), 'watch')}｜"
        f"硬否决 {'是' if canonical.get('hard_veto') is True else '否'}｜"
        f"理由 {_v25_plain(canonical.get('reason_codes'), '无已绑定理由码')}"
    )


def _v25_timeframe_dataset_id(material: Mapping, timeframe: str) -> str:
    asset_type = str(material.get("asset_type") or "stock").lower()
    daily = timeframe in {"monthly", "weekly", "daily"}
    if asset_type == "etf":
        return "E_PRICE_1D" if daily else "E_PRICE_5M"
    return "P_DAILY" if daily else "P_5M"


def _v25_timeframe_leaf_admitted(material: Mapping, timeframe: str) -> bool:
    if str(material.get("fact_mode") or "") in {"ADMITTED", "SYNTHETIC_TEST"}:
        return True
    admitted = {
        str(item)
        for item in (
            at(material, "gate.current_product_admitted_dataset_ids") or ()
        )
    }
    return _v25_timeframe_dataset_id(material, timeframe) in admitted


def _v25_missing_or_partial(material: Mapping | None) -> bool:
    return material is None or str(material.get("fact_mode") or "") in {
        "MISSINGNESS_ONLY", "PARTIALLY_ADMITTED",
    }


def _v25_number(value: Any, *, digits: int = 4) -> str | None:
    if not isinstance(value, Real) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _v25_level_price(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("price")
    return value


def _v25_family_execution_verified(
    material: Mapping,
    timeframe: str,
    family: str,
) -> bool:
    """Require the exact method receipt and source-bound cell before leaf display."""
    if (
        not _v25_timeframe_leaf_admitted(material, timeframe)
        or at(material, "factor.method_execution_receipt_policy") != "REQUIRED"
    ):
        return False
    cells = at(material, "trace.timeframe_family_matrix.cells")
    cell = next(
        (
            row for row in (cells or ())
            if isinstance(row, Mapping)
            and row.get("timeframe") == timeframe
            and row.get("family") == family
        ),
        None,
    )
    if (
        not isinstance(cell, Mapping)
        or cell.get("state") not in _MATRIX_AVAILABLE_STATES
        or at(cell, "runtime_binding.runtime_source_bound") is not True
    ):
        return False
    current_methods = set(cell.get("current_method_ids") or ())
    observed = tuple(
        row for row in cell.get("method_coverage") or ()
        if isinstance(row, Mapping)
        and row.get("method_id") in current_methods
        and row.get("state") in _MATRIX_AVAILABLE_STATES
    )
    return bool(observed) and all(
        len(str(row.get("method_receipt_hash") or "")) == 64
        and at(row, "runtime_source_binding.runtime_source_bound") is True
        for row in observed
    )


def _v25_timeframe_leaf_payload(
    material: Mapping | None,
    timeframe: str,
) -> dict[str, Any]:
    """One bounded leaf bundle consumed by the unchanged V2.5 cycle text node."""
    if material is None:
        return {}
    current = at(material.get("mtf") or {}, f"timeframes.{timeframe}")
    current = current if isinstance(current, Mapping) else {}
    factor = material.get("factor") if isinstance(material.get("factor"), Mapping) else {}
    leaf_admitted = _v25_timeframe_leaf_admitted(material, timeframe)
    family_verified = {
        family: _v25_family_execution_verified(material, timeframe, family)
        for family in (
            "TREND_RS", "SUPPLY", "COST", "STRUCTURE",
            "MOMENTUM", "PATTERN", "MTF",
        )
    }
    trend = current.get("trend") if isinstance(current.get("trend"), Mapping) else {}
    ma = current.get("ma_structure") if isinstance(current.get("ma_structure"), Mapping) else {}
    structure = (
        current.get("price_structure")
        if isinstance(current.get("price_structure"), Mapping) else {}
    )
    macd_startup = (
        current.get("macd_startup_stability")
        if isinstance(current.get("macd_startup_stability"), Mapping) else {}
    )
    rsi_startup = (
        current.get("rsi_startup_stability")
        if isinstance(current.get("rsi_startup_stability"), Mapping) else {}
    )
    payload: dict[str, Any] = {
        "timeframe": timeframe,
        "dataset_id": _v25_timeframe_dataset_id(material, timeframe),
        "product_admitted": leaf_admitted,
        "family_execution_verified": family_verified,
        "status": str(current.get("status") or "UNKNOWN"),
        "role": str(current.get("role") or "ROLE_UNAVAILABLE"),
        "summary": current.get("summary"),
        "observations": current.get("observations"),
        "completed_through": (
            current.get("completed_through")
            or current.get("latest_bar_date")
            or current.get("latest_bar_end")
        ),
        "source_alignment": at(current, "source_alignment.status"),
        "direction": trend.get("direction"),
        "ma_alignment": trend.get("ma_alignment"),
        "ma_values": dict(ma.get("ma_values") or {}),
        "ma_slopes_pct": dict(ma.get("slope_vector_pct") or {}),
        "ma_state": ma.get("state"),
        "ma_ordering": ma.get("ordering"),
        "ma_invalidation": ma.get("invalidation"),
        "volume_status": trend.get("volume_status"),
        "volume_ratio_5bar": trend.get("volume_ratio_5bar"),
        "macd_status": trend.get("macd_status"),
        "macd_values": dict(macd_startup.get("native_endpoint_values") or {}),
        "macd_startup_state": macd_startup.get("status"),
        "macd_startup_reason": macd_startup.get("reason"),
        "rsi_status": trend.get("rsi_status"),
        "rsi_values": dict(rsi_startup.get("reference_values") or {}),
        "rsi_startup_state": rsi_startup.get("status"),
        "rsi_startup_reason": rsi_startup.get("reason"),
        "current_close": structure.get("current_close"),
        "nearest_support": _v25_level_price(structure.get("nearest_support")),
        "nearest_resistance": _v25_level_price(structure.get("nearest_resistance")),
        "structure_event": at(structure, "structure_event.state"),
        "structure_invalidation": at(structure, "structure_event.invalidation"),
        "confirmed_pivot_count": len(structure.get("pivots") or ()),
        "pattern_status": at(current, "pattern_trigger.status"),
        "pattern_reason": at(current, "pattern_trigger.reason"),
    }
    if timeframe == "daily":
        payload.update({
            "relative_volume_20d": at(
                factor,
                "supply_demand_volume_price.completed_bar_context.relative_volume.volume_ratio_20d",
            ),
            "directional_volume_balance": at(
                factor,
                "supply_demand_volume_price.completed_bar_context.directional_volume.signed_volume_balance",
            ),
            "cmf_20": at(
                factor,
                "supply_demand_volume_price.completed_bar_context.close_location_flow.cmf_20",
            ),
            "cost_reference_20": at(
                factor,
                "cost_structure_evidence.context.bar_reference_cost.window_20.rolling_reference_price",
            ),
            "cost_reference_60": at(
                factor,
                "cost_structure_evidence.context.bar_reference_cost.window_60.rolling_reference_price",
            ),
            "macd_dif": at(
                factor,
                "volatility_momentum_evidence.context.momentum.macd.dif",
            ),
            "macd_dea": at(
                factor,
                "volatility_momentum_evidence.context.momentum.macd.dea",
            ),
            "macd_bar": at(
                factor,
                "volatility_momentum_evidence.context.momentum.macd.bar",
            ),
            "rsi_6": at(
                factor,
                "volatility_momentum_evidence.context.momentum.rsi.rsi_6",
            ),
            "rsi_12": at(
                factor,
                "volatility_momentum_evidence.context.momentum.rsi.rsi_12",
            ),
            "rsi_24": at(
                factor,
                "volatility_momentum_evidence.context.momentum.rsi.rsi_24",
            ),
            "roc_20_pct": at(
                factor,
                "volatility_momentum_evidence.context.momentum.roc_20_pct",
            ),
            "atr_wilder_14": at(
                factor,
                "volatility_momentum_evidence.context.volatility.atr_wilder_14",
            ),
            "primary_pattern_type": at(
                factor,
                "pattern_trigger_evidence.context.primary_pattern.pattern_type",
            ),
            "primary_pattern_lifecycle": at(
                factor,
                "pattern_trigger_evidence.context.primary_pattern.lifecycle",
            ),
            "candlestick_state": at(
                factor,
                "pattern_trigger_evidence.context.candlestick.status",
            ),
        })
    family_fields = {
        "TREND_RS": (
            "direction", "ma_alignment", "ma_values", "ma_slopes_pct",
            "ma_state", "ma_ordering", "ma_invalidation",
        ),
        "SUPPLY": (
            "volume_status", "volume_ratio_5bar", "relative_volume_20d",
            "directional_volume_balance", "cmf_20",
        ),
        "COST": ("cost_reference_20", "cost_reference_60"),
        "STRUCTURE": (
            "current_close", "nearest_support", "nearest_resistance",
            "structure_event", "structure_invalidation", "confirmed_pivot_count",
        ),
        "MOMENTUM": (
            "macd_status", "macd_values", "macd_startup_state",
            "macd_startup_reason", "rsi_status", "rsi_values",
            "rsi_startup_state", "rsi_startup_reason", "macd_dif",
            "macd_dea", "macd_bar", "rsi_6", "rsi_12", "rsi_24",
            "roc_20_pct", "atr_wilder_14",
        ),
        "PATTERN": (
            "pattern_status", "pattern_reason", "primary_pattern_type",
            "primary_pattern_lifecycle", "candlestick_state",
        ),
    }
    for family, fields in family_fields.items():
        if family_verified[family] is not True:
            for field in fields:
                payload.pop(field, None)
    if not all(
        family_verified[family]
        for family in ("TREND_RS", "SUPPLY", "STRUCTURE", "MOMENTUM")
    ):
        payload["summary"] = None
    return payload


def _v25_timeframe_leaf_text(payload: Mapping) -> str:
    if not payload:
        return "周期叶事实未绑定。"
    if payload.get("product_admitted") is not True:
        return (
            f"{payload.get('timeframe')}｜{payload.get('status')}｜职责 "
            f"{payload.get('role')}｜当前Product数值未准入。"
        )
    parts = [
        f"状态 {payload.get('status')}",
        f"职责 {payload.get('role')}",
        "方法执行 " + (
            "/".join(
                family for family, verified in (
                    payload.get("family_execution_verified") or {}
                ).items() if verified
            ) or "METHOD_NOT_EXECUTED"
        ),
        *(
            [f"摘要 {payload.get('summary')}"]
            if str(payload.get("summary") or "").strip() else []
        ),
    ]
    ma_values = payload.get("ma_values") if isinstance(payload.get("ma_values"), Mapping) else {}
    if ma_values:
        values = "/".join(
            f"{name.upper()}={_v25_number(value) or 'NA'}"
            for name, value in sorted(ma_values.items())
        )
        parts.append(
            "SMA " + values
            + f"；排列={payload.get('ma_ordering') or payload.get('ma_alignment') or 'UNKNOWN'}"
            + f"；状态={payload.get('ma_state') or 'UNKNOWN'}"
        )
    slopes = payload.get("ma_slopes_pct") if isinstance(payload.get("ma_slopes_pct"), Mapping) else {}
    if slopes:
        parts.append("MA斜率% " + "/".join(
            f"{name.upper()}={_v25_number(value) or 'NA'}"
            for name, value in sorted(slopes.items())
        ))
    volume_bits = []
    for label, key in (
        ("5bar量比", "volume_ratio_5bar"),
        ("20日量比", "relative_volume_20d"),
        ("方向量", "directional_volume_balance"),
        ("CMF20", "cmf_20"),
    ):
        number = _v25_number(payload.get(key))
        if number is not None:
            volume_bits.append(f"{label}={number}")
    if payload.get("volume_status"):
        volume_bits.insert(0, f"状态={payload.get('volume_status')}")
    parts.append("供需/量价 " + ("；".join(volume_bits) if volume_bits else "DATA_MISSING"))
    cost_bits = []
    for label, key in (("成本20", "cost_reference_20"), ("成本60", "cost_reference_60")):
        number = _v25_number(payload.get(key))
        if number is not None:
            cost_bits.append(f"{label}={number}")
    parts.append("成本 " + ("；".join(cost_bits) if cost_bits else "ALGORITHM_OR_DATA_GAP"))
    structure_bits = []
    for label, key in (
        ("现价", "current_close"),
        ("支撑", "nearest_support"),
        ("压力", "nearest_resistance"),
    ):
        number = _v25_number(payload.get(key))
        if number is not None:
            structure_bits.append(f"{label}={number}")
    if payload.get("structure_event"):
        structure_bits.append(f"事件={payload.get('structure_event')}")
    structure_bits.append(f"确认枢轴={payload.get('confirmed_pivot_count') or 0}")
    parts.append("结构 " + "；".join(structure_bits))
    momentum_bits = []
    for label, key in (
        ("DIF", "macd_dif"), ("DEA", "macd_dea"), ("BAR", "macd_bar"),
        ("RSI6", "rsi_6"), ("RSI12", "rsi_12"), ("RSI24", "rsi_24"),
        ("ROC20%", "roc_20_pct"), ("ATR14", "atr_wilder_14"),
    ):
        number = _v25_number(payload.get(key))
        if number is not None:
            momentum_bits.append(f"{label}={number}")
    if not momentum_bits:
        macd_values = payload.get("macd_values") if isinstance(payload.get("macd_values"), Mapping) else {}
        rsi_values = payload.get("rsi_values") if isinstance(payload.get("rsi_values"), Mapping) else {}
        momentum_bits.extend(
            f"{name}={_v25_number(value) or 'NA'}"
            for name, value in sorted({**macd_values, **rsi_values}.items())
            if name.endswith("latest") or name.startswith("MACD_")
        )
    momentum_bits.append(
        f"MACD启动={payload.get('macd_startup_state') or 'UNKNOWN'}"
    )
    momentum_bits.append(
        f"RSI启动={payload.get('rsi_startup_state') or 'UNKNOWN'}"
    )
    parts.append("波动/动量 " + "；".join(momentum_bits))
    pattern_bits = [
        value for value in (
            f"形态={payload.get('primary_pattern_type')}" if payload.get("primary_pattern_type") else None,
            f"生命周期={payload.get('primary_pattern_lifecycle')}" if payload.get("primary_pattern_lifecycle") else None,
            f"K线={payload.get('candlestick_state')}" if payload.get("candlestick_state") else None,
            f"周期形态={payload.get('pattern_status')}" if payload.get("pattern_status") else None,
        ) if value
    ]
    parts.append("形态/触发 " + ("；".join(pattern_bits) if pattern_bits else "ALGORITHM_GAP"))
    parts.append(
        "多周期/来源 "
        f"方向={payload.get('direction') or 'UNKNOWN'}；"
        f"source={payload.get('source_alignment') or 'UNKNOWN'}；"
        f"完成至={payload.get('completed_through') or 'UNKNOWN'}；"
        f"observations={payload.get('observations') if payload.get('observations') is not None else 'UNKNOWN'}"
    )
    return "｜".join(parts)


def _v25_timeframe(material: Mapping | None, timeframe: str, detail_index: int) -> str:
    label = _V25_TF_LABELS[timeframe]
    if material is None:
        if detail_index == 0:
            return f"{label}｜对象未绑定｜不沿用Golden示例数值。"
        return f"{label}｜来源与完成时点未绑定｜无独立行动权限。"
    current = at(material["mtf"], f"timeframes.{timeframe}")
    current = current if isinstance(current, Mapping) else {}
    status = str(current.get("status") or at(material["brief"], f"coverage.{timeframe}") or "UNKNOWN")
    role = str(current.get("role") or at(material["brief"], f"timeframe_thesis.{timeframe}.role") or "ROLE_UNAVAILABLE")
    leaf_admitted = _v25_timeframe_leaf_admitted(material, timeframe)
    if detail_index == 0:
        payload = _v25_timeframe_leaf_payload(material, timeframe)
        return f"{label}｜{_v25_timeframe_leaf_text(payload)}"
    if not leaf_admitted:
        return f"{label}｜来源与完成时点未准入｜独立行动权限：否"
    return (
        f"来源 {_v25_plain(at(current, 'source_alignment.status'), 'SOURCE_UNKNOWN')}｜"
        f"完成至 {_v25_plain(current.get('completed_through') or current.get('latest_bar_date') or current.get('latest_bar_end'), '时间未知')}｜"
        f"原因 {_v25_plain(current.get('reason'), '无额外原因')}｜独立行动权限：否"
    )


def _v25_safe_role_text(original_text: str, fallback: str) -> str:
    cleaned = " ".join(original_text.split()).strip()
    if (
        not cleaned
        or any(fragment in cleaned for fragment in _V25_SAMPLE_FRAGMENTS)
        or any(char.isdigit() for char in cleaned)
        or len(cleaned) > 80
    ):
        return fallback
    return cleaned


def _v25_static_role_label(original_text: str, fallback: str) -> str:
    cleaned = " ".join(original_text.split()).strip(" ：:|｜")
    if (
        not cleaned
        or any(fragment in cleaned for fragment in _V25_SAMPLE_FRAGMENTS)
        or len(cleaned) > 40
    ):
        return fallback
    return cleaned


def _v25_inline_role_label(
    parser: _V25TextFrameParser,
    block: Mapping,
    original_text: str,
    ordinal: int,
) -> str:
    cleaned = " ".join(original_text.split()).strip()
    if cleaned in _V25_INLINE_ROLE_LABELS:
        return cleaned.strip(" ：:|｜")
    semantic = _v25_semantic_for_block(parser, block["index"])
    cycle = _v25_nearest(parser, block["index"], class_name="cycle")
    labels = {
        "lead": ("结论", "证据边界"),
        "mainline": ("支持", "反证", "结论"),
        "one-line": ("结论", "边界"),
        "synthesis": ("合成", "边界"),
        "valuation": ("估值边界",),
        "flow-box": ("证据边界",),
        "attn": ("触发",),
        "fail": ("失效",),
    }.get(semantic)
    if cycle is not None:
        labels = ("证据边界", "确认条件")
    if not labels:
        labels = ("状态说明", "证据边界")
    return labels[ordinal % len(labels)]


def _v25_kpi_text(index: int) -> tuple[str, str]:
    rows = (
        ("现在怎么做？｜维持WAIT", "完整市场许可未形成前，不增加方向性风险。"),
        ("多数股票跟上了吗？｜尚不能判断", "宽度、中期参与和历史分母均未准入。"),
        ("资金是否支持？｜尚不能判断", "成交、两融和ETF份额未准入，不推断资金方向。"),
    )
    return rows[index % len(rows)]


def _v25_card_role(card_id: str | None) -> str:
    return _V25_CARD_ROLES.get(str(card_id or ""), "研究卡位")


def _v25_card_asset_type(material: Mapping | None, card_id: str | None) -> str:
    if isinstance(material, Mapping):
        value = str(material.get("asset_type") or "").lower()
        if value in {"stock", "etf"}:
            return value
    return "etf" if card_id in _V25_ETF_CARD_IDS else "stock"


def _v25_card_missing_line(
    material: Mapping | None,
    card_id: str | None,
    ordinal: int,
) -> str:
    role = _v25_card_role(card_id)
    if material is None:
        rows = (
            f"{role}：本轮没有已准入对象，不强制填充。",
            f"{role}：对象身份、价格、单位与时点未绑定。",
            f"{role}：七周期、资产专项与执行条件均保持缺失。",
            f"{role}：没有触发、失效、历史参考或概率输出。",
        )
        return rows[ordinal % len(rows)]
    unresolved = len(tuple(material["gate"].get("unresolved_product_dataset_ids") or ()))
    return (
        f"{material['name']}（{material['code']}）：全域Product尚未准入"
        f"（{unresolved}项适用材料待核）；当前只呈现状态、缺失和WAIT。"
    )


def _v25_quality_line(material: Mapping | None) -> str:
    quality = at(material, "factor.quality_evidence") if isinstance(material, Mapping) else None
    if not isinstance(quality, Mapping) or quality.get("product_admitted") is not True:
        return ""
    summary = at(material, "brief.quality_growth.summary")
    return _v25_plain(summary, "质量成长证据已准入，但没有可展示摘要")


def _v25_etf_specific_line(material: Mapping | None) -> str:
    evidence = at(material, "factor.etf_specific_evidence") if isinstance(material, Mapping) else None
    if not isinstance(evidence, Mapping) or evidence.get("product_admitted") is not True:
        return ""
    summary = _v25_plain(evidence.get("summary"), "")
    if summary:
        return summary
    values = []
    for label, key in (
        ("折溢价", "premium_discount_pct"),
        ("点差", "spread_bps"),
        ("份额变化", "shares_change_pct"),
        ("跟踪误差", "tracking_error_pct"),
    ):
        number = _v25_number(evidence.get(key))
        if number is not None:
            values.append(f"{label}={number}")
    return "；".join(values) or "ETF专项事实已准入"


def _v25_audit_line(material: Mapping | None) -> str:
    if not isinstance(material, Mapping):
        return ""
    mtf = material.get("mtf") if isinstance(material.get("mtf"), Mapping) else {}
    frames = mtf.get("timeframes") if isinstance(mtf.get("timeframes"), Mapping) else {}
    states = "/".join(
        f"{tf}:{str((frames.get(tf) or {}).get('status') or 'MISSING')}"
        for tf in TIMEFRAMES
    )
    snapshot = str(mtf.get("data_snapshot_identity") or "")
    available = str(mtf.get("available_at_max") or "")
    return (
        f"7周期={states}；数据快照={'已绑定' if len(snapshot) == 64 else '未绑定'}；"
        f"available_at={available or 'UNKNOWN'}"
    )


def _v25_block_canonical_path_hashes(
    material: Mapping | None,
    *,
    semantic: str,
    cycle_timeframe: str | None,
) -> tuple[str, ...]:
    """Hash only canonical inputs that the selected text branch actually consumed."""
    if not isinstance(material, Mapping):
        return ()
    factor = material.get("factor") if isinstance(material.get("factor"), Mapping) else {}
    paths: list[str] = []
    if cycle_timeframe:
        payload = _v25_timeframe_leaf_payload(material, cycle_timeframe)
        if (
            payload.get("product_admitted") is True
            and any((payload.get("family_execution_verified") or {}).values())
        ):
            paths.append(
                f"multi_timeframe_structure_context.timeframes.{cycle_timeframe}"
            )
    elif semantic == "identity":
        daily = _v25_timeframe_leaf_payload(material, "daily")
        if (
            daily.get("product_admitted") is True
            and daily.get("current_close") is not None
            and any((daily.get("family_execution_verified") or {}).values())
        ):
            paths.append("price_structure_evidence.context.current_close")
        if _v25_audit_line(material):
            paths.extend((
                "multi_timeframe_structure_context.timeframes",
                "multi_timeframe_structure_context.data_snapshot_identity",
            ))
    elif semantic == "event-alert":
        paths.extend(("canonical_decision.action", "canonical_decision.reason_codes"))
    elif semantic == "valuation":
        valuation = at(factor, "valuation_evidence")
        if (
            isinstance(valuation, Mapping)
            and valuation.get("status") == "READY"
            and valuation.get("product_admitted") is True
        ):
            paths.append("valuation_evidence")
        etf_specific = at(factor, "etf_specific_evidence")
        if (
            isinstance(etf_specific, Mapping)
            and etf_specific.get("product_admitted") is True
            and _v25_etf_specific_line(material)
        ):
            paths.append("etf_specific_evidence")
    elif semantic == "flow-box" and not _v25_missing_or_partial(material):
        if at(factor, "supply_demand_volume_price.completed_bar_context.state") is not None:
            paths.append("supply_demand_volume_price.completed_bar_context.state")
        if _v25_quality_line(material):
            paths.append("quality_evidence")
        if _v25_etf_specific_line(material):
            paths.append("etf_specific_evidence")
    elif semantic == "attn" and not _v25_missing_or_partial(material):
        if at(factor, "pattern_trigger_evidence.context") is not None:
            paths.append("pattern_trigger_evidence.context")
        if at(factor, "multi_timeframe_structure_context.timeframes.30m") is not None:
            paths.append("multi_timeframe_structure_context.timeframes.30m")
    elif semantic == "fail" and not _v25_missing_or_partial(material):
        if at(factor, "price_structure_evidence.context.structure_event") is not None:
            paths.append("price_structure_evidence.context.structure_event")
        if at(factor, "canonical_decision.reason_codes") is not None:
            paths.append("canonical_decision.reason_codes")
    elif semantic == "small":
        if not _v25_missing_or_partial(material) and at(
            factor, "calibrated_probability_evidence"
        ) is not None:
            paths.append("calibrated_probability_evidence")
        if _v25_audit_line(material):
            paths.extend((
                "multi_timeframe_structure_context.timeframes",
                "multi_timeframe_structure_context.data_snapshot_identity",
            ))
    return tuple(sorted({
        digest(value)
        for path in paths
        for value in (at(factor, path),)
        if value is not None
    }))


def _v25_valuation_line(
    material: Mapping | None,
    card_id: str | None,
    ordinal: int,
) -> str:
    asset_type = _v25_card_asset_type(material, card_id)
    role = _v25_card_role(card_id)
    if material is not None:
        business = (
            at(material, "factor.valuation_evidence")
            or at(material, "factor.business_valuation")
        )
        shared = at(material, "factor.index_sector_valuations") or ()
        business_ready = (
            isinstance(business, Mapping)
            and business.get("status") == "READY"
            and business.get("product_admitted") is True
        )
        shared_ready = any(
            isinstance(row, Mapping)
            and row.get("status") == "READY"
            and row.get("product_admitted") is True
            for row in shared
        )
        if business_ready or shared_ready:
            valuation = material["brief"].get("valuation") or {}
            text = _v25_plain(
                valuation.get("summary"),
                f"估值状态 {_v25_plain(valuation.get('status'), 'UNKNOWN')}",
            )
            etf_specific = _v25_etf_specific_line(material)
            return "｜".join(value for value in (text, etf_specific) if value)
    if asset_type == "etf":
        rows = (
            f"{role}：交易价格与同一时点NAV/IOPV未绑定，不判断折溢价。",
            f"{role}：底层成分、盈利与估值vintage未准入，不判断贵或便宜。",
            f"{role}：技术关注区、底层估值和交易合理价必须分开，当前均缺失。",
        )
    else:
        rows = (
            f"{role}：业务分类、财报版本和适用估值路线未完整准入。",
            f"{role}：盈利、现金流、资产负债与同行参数缺失，不输出合理价。",
            f"{role}：主方法、交叉核验与情景敏感性均待当前数据支持。",
        )
    return rows[ordinal % len(rows)]


def _v25_flow_line(
    material: Mapping | None,
    card_id: str | None,
    ordinal: int,
    *,
    market_card: bool = False,
) -> str:
    if market_card:
        rows = (
            "市场成交与换手未准入，不判断增量资金。",
            "两融、ETF份额与行业资金未准入，不判断杠杆或风格流向。",
            "宽度、量价和执行条件未形成，不解释为吸筹、洗盘或派发。",
        )
        return rows[ordinal % len(rows)]
    asset_type = _v25_card_asset_type(material, card_id)
    role = _v25_card_role(card_id)
    if asset_type == "etf":
        rows = (
            f"{role}：份额、成交与价格方向未同时准入，不判断资金流入。",
            f"{role}：折溢价、点差与跟踪数据缺失，不判断拥挤或撤退。",
            f"{role}：没有同类RS与篮子变化，不把单段量价解释为机构意图。",
        )
    else:
        rows = (
            f"{role}：成交量价与成本材料未完整准入，不判断承接强弱。",
            f"{role}：资金流代理没有独立来源，不解释为主力吸筹。",
            f"{role}：失败突破、放量滞涨和宽度证据缺失，不判断派发。",
        )
    return rows[ordinal % len(rows)]


def _v25_history_line(material: Mapping | None, card_id: str | None) -> str:
    if _v25_missing_or_partial(material):
        return f"{_v25_card_role(card_id)}：历史参考与当前概率未独立准入。"
    return (
        f"{material['name']}（{material['code']}）｜"
        f"历史参考：{_v25_plain(at(material, 'brief.historical_reference.reason'), '不可用')}｜"
        f"当前概率：{_v25_plain(at(material, 'brief.current_probability.reason'), '未独立校准')}"
    )


def _v25_empty_candidate_cell(row: int, column: int, *, asset_type: str) -> str:
    rank = row + 4
    label = "ETF" if asset_type == "etf" else "股票"
    values = {
        0: str(rank),
        1: f"本次无第{rank}名{label}候选",
        2: "无可比较排序分",
        3: "身份、覆盖和基础筛选未形成",
        4: "无当前价格与适用估值",
        5: "无已形成触发",
        6: "无可核失效条件",
        7: "保持空位，不用示例补齐",
    }
    return values.get(column, "无材料")


def _v25_list_line(mail: int, ordinal: int, assignment: Mapping) -> str:
    if mail == 0:
        rows = (
            "先核全球、A股市场和行业是否处于同一研究时点；缺失时不判断风险偏好。",
            "先核月周日趋势与市场宽度是否一致；短线改善不能替代中期参与。",
            "先核60/30/15/5分钟是否按上级周期职责确认；低周期不抢跑。",
            "先核成交、两融、ETF份额和执行条件；没有来源就不推断资金意图。",
            "本轮没有新准入事实，不沿用昨晚示例证券或价格。",
            "股票只保留当前canonical、支持/反证、触发/失效和缺失。",
            "ETF另核NAV、份额、折溢价、点差、跟踪和篮子，不套股票语义。",
            "当前为NO_SEND；未形成Product时不创建Recording或概率。",
        )
    else:
        rows = (
            "本轮对象只按当前材料变化展示；没有新事实就维持原WAIT。",
            "七周期、八证据族和资产专项分别核对，缺失不相互补齐。",
            "估值、量价、风险、触发和失效必须同时保留，不能只报一个分数。",
            "当前报告不发送、不落业务库，也不生成概率或训练标签。",
        )
    return rows[ordinal % len(rows)]


def _v25_card_detail_line(material: Mapping | None, card_id: str | None, ordinal: int) -> str:
    asset_type = str(material.get("asset_type") or "") if isinstance(material, Mapping) else ""
    if not asset_type:
        asset_type = "etf" if card_id in _V25_ETF_CARD_IDS else "stock"
    if asset_type == "etf":
        rows = (
            "ETF身份、价格与NAV/IOPV时点未完整绑定。",
            "份额、折溢价、点差、跟踪、费率和持仓篮子按各自状态核对。",
            "七周期与同类相对强弱未形成时，不输出追涨或抄底结论。",
        )
    else:
        rows = (
            "股票身份、行业归属、价格与财报版本未完整绑定。",
            "质量成长、业务适用估值、量价结构和执行风险分别核对。",
            "七周期未形成触发与失效闭环时，维持WAIT而不制造买点。",
        )
    if _v25_missing_or_partial(material):
        return rows[ordinal % len(rows)]
    return _v25_missing(material)


def _v25_market_table_text(
    parser: _V25TextFrameParser,
    block: Mapping,
    original_text: str,
    assignment: Mapping,
    coordinates: Mapping,
) -> str:
    heading = _v25_section_heading(parser, block["index"])
    row = int(coordinates["row"])
    column = int(coordinates["column"])
    if column == 0:
        return _v25_static_role_label(original_text, _v25_structural_label(original_text))
    scope = assignment["scope_universe"]
    if "当前市场结论" in heading:
        domains = ("GLOBAL", "CN_MARKET", "CN_MARKET", "SECTOR", "RECORD")
        if column == 1:
            return _v25_domain_line(scope, ("GLOBAL", "CN_MARKET", "SECTOR", "ETF", "STOCK", "RECORD").index(domains[row % 5]))
        return "证据未准入时维持WAIT；不把缺失当支持、反证或行动许可。"
    if "代表指数职责" in heading:
        if column == 1:
            return _v25_safe_role_text(original_text, "回答该指数对应的市场层级和风格职责。")
        if column == 2:
            return "指数价格、宽度和七周期状态未完整准入。"
        return "等待同一asof的结构、量价、宽度与估值冲突核对。"
    if "市场宽度" in heading:
        return "宽度分母和当前值未准入。" if column == 1 else "暂不判断上涨扩散深度或中期参与。"
    if "成交、资金、杠杆" in heading:
        if column == 1:
            return "相应成交、杠杆、资金或估值数据未准入。"
        if column == 2:
            return "未准入时不作正向资金或估值解释。"
        return "待原生公布频率、时点和反证同时核对。"
    if "全球背景" in heading:
        if column == 1:
            return _v25_domain_line(scope, 0)
        if column == 2:
            return "全球证据只能确认、降级或失效，不能独立升级BUY。"
        return "来源、公布时点或本地量价缺失时继续等待。"
    if "优先看哪些行业" in heading:
        if column == 1:
            return "行业与资产支持证据未完整准入。"
        if column == 2:
            return "反证、估值和执行风险未完整准入。"
        return "维持观察；不按Golden示例方向强制排序。"
    if "数据时点与质量" in heading:
        return "尚无准入时点。" if column == 1 else "按原生频率、available_at与vintage使用，不后见回填。"
    return _v25_domain_line(scope, row + column)


def _v25_market_paragraph(parser: _V25TextFrameParser, block: Mapping, assignment: Mapping) -> str:
    heading = _v25_section_heading(parser, block["index"])
    scope = assignment["scope_universe"]
    if "市场宽度" in heading:
        return "市场宽度、分母、新高新低和均线上方比例未准入；当前不判断扩散深度。"
    if "成交、资金、杠杆" in heading:
        return "成交、两融、ETF份额、信用、估值和波动材料未准入；不推断资金方向。"
    if "全球背景" in heading:
        return "全球价格、利率、通胀、汇率、商品和科技资金未准入；只保留影响路径，不输出方向。"
    if "优先看哪些行业" in heading:
        return "行业分类、价格相对强弱、宽度、成交、资金和估值未准入；不强制给出优先方向。"
    if "数据时点与质量" in heading:
        return "所有材料按各自原生频率和known-at时点核对；当前缺失不得用旧值或示例值补齐。"
    return _v25_domain_line(scope, block["index"])


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
    card = next((node for node in chain if "card" in node["classes"]), None)
    cycle = next((node for node in chain if "cycle" in node["classes"]), None)
    pool = next((node for node in chain if "candidate-pool" in node["classes"]), None)
    card_id = card.get("id") if card else None
    material = assignment["cards"].get(card_id) if card_id else None
    mail = int(block["mail"])
    market_card = mail == 0 and card is not None
    scope_universe = assignment["scope_universe"]
    kpi = next((node for node in chain if "kpi" in node["classes"]), None)
    if kpi is not None:
        kpis = [
            node for node in parser.nodes
            if node["mail"] == mail and "kpi" in node["classes"]
        ]
        kpi_index = next(
            index for index, node in enumerate(kpis) if node["index"] == kpi["index"]
        )
        return _v25_kpi_text(kpi_index)[1 if block["tag"] == "span" else 0]
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
            return (
                "当前全球、A股市场和行业事实尚未完成Product准入；"
                "市场结论维持WAIT，只呈现可核状态、缺失和下一确认条件。"
            )
        if mail == 1:
            return "AUTO允许零或部分候选；筛选排名不是买入权限，最终动作服从canonical和四类独立否决。"
        return "自选/指定代码只投影当前材料变化；无变化或缺失不制造新结论。"
    if semantic == "card-title":
        if market_card:
            return "代表指数综合｜市场价格、宽度与七周期事实未准入，当前不判断方向"
        if material is None:
            return f"{_v25_card_role(card_id)}｜本轮无已准入研究对象"
        prefix = "【合成测试】" if material["fact_mode"] == "SYNTHETIC_TEST" else ""
        if material["fact_mode"] == "PARTIALLY_ADMITTED":
            return f"{material['name']}（{material['code']}）｜部分事实已准入；完整结论仍按当前canonical"
        return f"{prefix}{material['name']}（{material['code']}）｜{_v25_plain(material['brief'].get('one_line_conclusion'), '结论待补齐')}"
    if semantic == "identity":
        if market_card:
            return "代表指数、市场宽度、成交与估值需绑定同一asof、来源、单位和完成bar。"
        if material is None:
            return f"{_v25_card_role(card_id)}｜对象身份、价格、单位与时点均未绑定"
        payload = _v25_timeframe_leaf_payload(material, "daily")
        price = _v25_number(payload.get("current_close"))
        audit = _v25_audit_line(material)
        return (
            f"{material['name']}（{material['code']}）｜"
            f"{_v25_plain(material['delivery'].get('asset_identity_text'), '市场/板块待确认')}｜"
            f"现价={price if price is not None else 'DATA_MISSING'}｜{audit}｜"
            f"{_v25_missing(material)}"
        )
    if semantic == "event-alert":
        if market_card:
            return "市场Product事实未准入｜无独立行动权限"
        if material is None:
            return f"{_v25_card_role(card_id)}｜无已准入对象｜无独立行动权限"
        return _v25_decision(material)
    if semantic in {"one-line", "synthesis"}:
        if market_card:
            return "当前只确认完整义务均可追溯；市场数值未准入，维持WAIT并等待同一asof证据。"
        if _v25_missing_or_partial(material):
            return _v25_card_missing_line(
                material,
                card_id,
                0 if semantic == "one-line" else 1,
            )
        return _v25_plain(material["brief"].get("fused_paragraph"), _v25_missing(material))
    if semantic == "why":
        if _v25_missing_or_partial(material):
            return _v25_card_missing_line(material, card_id, 2)
        return _v25_plain(
            at(material, "delivery.candidate_reason"),
            _v25_card_missing_line(material, card_id, 2),
        )
    if cycle is not None:
        timeframe = TIMEFRAMES[int(str(cycle["id"]).split("-")[1]) % len(TIMEFRAMES)]
        if market_card:
            if semantic == "cycle-title":
                return f"{_V25_TF_LABELS[timeframe]}｜市场环境数据未准入｜不具有独立行动权限"
            if ordinal % 2 == 0:
                return (
                    f"{_V25_TF_LABELS[timeframe]}｜代表指数、宽度与量价未完整准入；"
                    "不使用Golden示例数值。"
                )
            return (
                f"{_V25_TF_LABELS[timeframe]}｜来源、完成bar与时点未绑定；"
                "等待同一asof确认，独立行动权限为否。"
            )
        if semantic == "cycle-title":
            status = _v25_plain(
                at(material, f"mtf.timeframes.{timeframe}.status")
                if material and _v25_timeframe_leaf_admitted(material, timeframe)
                else None,
                "DATA_INSUFFICIENT",
            )
            return f"{_V25_TF_LABELS[timeframe]}｜{status}｜不具有独立行动权限"
        return _v25_timeframe(material, timeframe, ordinal)
    if semantic == "valuation" or any("valuation" in node["classes"] for node in chain):
        if market_card:
            return "市场估值、ERP与利率参照未完整准入；不能输出便宜或昂贵结论。"
        return _v25_valuation_line(material, card_id, ordinal)
    if semantic == "indicator-box" or any("indicator-box" in node["classes"] for node in chain):
        return _v25_indicator_family_line(
            None if market_card else material,
            scope_universe,
            ordinal,
        )
    if semantic == "flow-box" or any("flow-box" in node["classes"] for node in chain):
        if market_card:
            return _v25_flow_line(None, card_id, ordinal, market_card=True)
        if _v25_missing_or_partial(material):
            return _v25_flow_line(material, card_id, ordinal)
        supply = _v25_plain(
            at(material, "factor.supply_demand_volume_price.summary"),
            "供需材料无独立事件",
        )
        asset_leaf = (
            _v25_etf_specific_line(material)
            if _v25_card_asset_type(material, card_id) == "etf"
            else _v25_quality_line(material)
        )
        return "｜".join(value for value in (supply, asset_leaf) if value)
    if semantic == "attn":
        fallback = (
            f"{_v25_card_role(card_id)}：没有已形成触发"
            if material is None else "触发条件未形成"
        )
        if _v25_missing_or_partial(material):
            return fallback
        return _v25_plain(at(material, "brief.trigger"), fallback)
    if semantic == "fail":
        fallback = (
            f"{_v25_card_role(card_id)}：没有可核失效条件"
            if material is None else "失效条件材料不足"
        )
        if _v25_missing_or_partial(material):
            return fallback
        return _v25_plain(at(material, "brief.invalidation"), fallback)
    if semantic == "small":
        history = _v25_history_line(material, card_id)
        audit = _v25_audit_line(material)
        return "｜".join(value for value in (history, audit) if value)
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
                return _v25_empty_candidate_cell(
                    coordinates["row"],
                    column,
                    asset_type="etf" if pool_index == 0 else "stock",
                )
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
        if mail == 0:
            return _v25_market_table_text(parser, block, original_text, assignment, coordinates)
        if coordinates["column"] == 0:
            return _v25_structural_label(original_text)
        if card_id:
            return _v25_indicator_family_line(material, scope_universe, coordinates["row"] * 3 + coordinates["column"])
        return _v25_domain_line(scope_universe, coordinates["table"] + coordinates["row"])
    if block["tag"] == "li":
        return _v25_list_line(mail, ordinal, assignment)
    if block["tag"] == "p" and card_id:
        return _v25_card_missing_line(material, card_id, ordinal + 2)
    if block["tag"] == "p" and mail == 0:
        return _v25_market_paragraph(parser, block, assignment)
    if block["tag"] == "p":
        return _v25_domain_line(scope_universe, ordinal)
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


def _v25_email_consumer_quality(output: str) -> dict:
    raw_scope_prefixes = ("研究要求 ", "数据对象 ", "估值路线 ", "Golden原子字段 ")
    raw_scope_row_count = sum(output.count(prefix) for prefix in raw_scope_prefixes)
    blank_inline_tag_count = len(
        re.findall(r"<(?:b|strong)>\s*</(?:b|strong)>", output)
    )
    blank_unclassified_span_count = len(
        re.findall(r"<span>\s*</span>", output)
    )
    indicator_family_labels = tuple(
        label for label, _requirement in _V25_FAMILY_REQUIREMENTS
    )
    missing_indicator_family_labels = tuple(
        label for label in indicator_family_labels if label not in output
    )
    repeated_policy_filler_count = output.count(
        "证据、反证、风险、触发和失效必须同时保留；缺失不被技术分抵消。"
    )
    generic_placeholder_count = sum(
        output.count(text)
        for text in (
            "供需/资金代理未完整准入；不把量价片段解释为机构意图。",
            "估值输入、路线、日期或财报版本未完整准入；不能输出合理价。",
            "本次无已准入候选",
            "本位置没有已准入对象；保留框架，不沿用示例证券、价格或结论。",
        )
    )
    required_section_headings = (
        "当前市场结论与五条依据",
        "一、代表指数职责与上证综指完整分析",
        "二、市场宽度：短线扩散，中期参与仍不足",
        "四、成交、资金、杠杆、信用、估值与波动",
        "五、全球背景怎样影响A股方向",
        "六、今天优先看哪些行业和资产",
        "八、数据时点与质量",
    )
    if raw_scope_row_count:
        raise TraceabilityError("V25_DYNAMIC_RAW_SCOPE_ROW_LEAK")
    if blank_inline_tag_count:
        raise TraceabilityError("V25_DYNAMIC_BLANK_INLINE_TAG")
    if blank_unclassified_span_count:
        raise TraceabilityError("V25_DYNAMIC_BLANK_INLINE_TAG")
    if missing_indicator_family_labels:
        raise TraceabilityError("V25_DYNAMIC_INDICATOR_FAMILY_SUMMARY_MISSING")
    if repeated_policy_filler_count:
        raise TraceabilityError("V25_DYNAMIC_REPEATED_POLICY_FILLER")
    if generic_placeholder_count:
        raise TraceabilityError("V25_DYNAMIC_GENERIC_PLACEHOLDER_REPETITION")
    if any(heading not in output for heading in required_section_headings):
        raise TraceabilityError("V25_DYNAMIC_ORDINARY_LANGUAGE_ROLE_LOST")
    return {
        "projection_mode": "ROLE_SUMMARIZED_EMAIL_PLUS_MACHINE_RECEIPT",
        "raw_scope_row_count": raw_scope_row_count,
        "blank_inline_tag_count": blank_inline_tag_count,
        "blank_unclassified_span_count": blank_unclassified_span_count,
        "indicator_family_labels": indicator_family_labels,
        "missing_indicator_family_labels": missing_indicator_family_labels,
        "repeated_policy_filler_count": repeated_policy_filler_count,
        "generic_placeholder_count": generic_placeholder_count,
        "ordinary_language_roles_preserved": True,
    }


def _stage1_rollup_method_state(states: tuple[str, ...]) -> str:
    current = tuple(state for state in states if state != "NOT_ADMITTED")
    if not current:
        return "NOT_ADMITTED"
    if all(state == "NOT_APPLICABLE" for state in current):
        return "NOT_APPLICABLE"
    if any(state in {"READY", "PARTIAL"} for state in current):
        return "READY" if all(state == "READY" for state in current) else "PARTIAL"
    if any(state == "UNKNOWN" for state in current):
        return "UNKNOWN"
    return "MISSING"


def _stage1_exact_method_invocation_verified(
    rows: tuple[Mapping[str, Any], ...],
    *,
    strict: bool,
    runtime_state: str,
) -> bool:
    """Verify one method from its own source-bound execution receipts."""
    observed_rows = tuple(
        row for row in rows
        if row.get("state") in {"READY", "PARTIAL"}
    )
    return bool(
        strict
        and runtime_state in {"READY", "PARTIAL"}
        and observed_rows
        and all(
            len(str(row.get("method_receipt_hash") or "")) == 64
            and row.get("runtime_source_bound") is True
            for row in observed_rows
        )
    )


def _stage1_method_rows(material: Mapping) -> tuple[dict[str, Any], ...]:
    factor = material["factor"]
    trace = validate_runtime_trace(factor)
    universe = trace["complete_research_universe"]
    matrix = trace["timeframe_family_matrix"]
    runtime_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cell in matrix.get("cells") or ():
        if not isinstance(cell, Mapping):
            continue
        for row in cell.get("method_coverage") or ():
            if not isinstance(row, Mapping):
                continue
            runtime_rows[str(row.get("method_id") or "")].append({
                "timeframe": row.get("timeframe"),
                "family": row.get("family"),
                "state": row.get("state"),
                "reason": row.get("reason"),
                "method_receipt_hash": row.get("method_receipt_hash"),
                "runtime_source_bound": bool(
                    at(row, "runtime_source_binding.runtime_source_bound")
                ),
            })
    for row in matrix.get("non_matrix_methods") or ():
        if isinstance(row, Mapping):
            runtime_rows[str(row.get("method_id") or "")].append({
                "timeframe": "asset",
                "family": tuple(row.get("family_targets") or ()),
                "state": row.get("state"),
                "reason": row.get("reason"),
                "method_receipt_hash": None,
                "runtime_source_bound": False,
            })
    strict = factor.get("method_execution_receipt_policy") == "REQUIRED"
    result = []
    for profile in universe.get("methods") or ():
        if not isinstance(profile, Mapping):
            continue
        method_id = str(profile.get("method_id") or "")
        rows = tuple(runtime_rows.get(method_id) or ())
        states = tuple(str(row.get("state") or "UNKNOWN") for row in rows)
        runtime_state = _stage1_rollup_method_state(states)
        requirement_id = str(profile.get("requirement_id") or "")
        # One sibling method may remain missing while this method has actually
        # executed on source-bound inputs; a parent-family rollup must not erase
        # that exact leaf proof.
        invocation_verified = _stage1_exact_method_invocation_verified(
            rows,
            strict=strict,
            runtime_state=runtime_state,
        )
        result.append({
            "method_id": method_id,
            "requirement_id": requirement_id,
            "implementation_state": profile.get("leaf_implementation_state"),
            "consumer_scope": profile.get("consumer_scope"),
            "target_timeframes": tuple(profile.get("target_timeframes") or ()),
            "effective_execution_timeframes": tuple(
                profile.get("current_execution_timeframes") or ()
            ),
            "runtime_state": runtime_state,
            "runtime_observation_count": len(rows),
            "actual_invocation_verified": invocation_verified,
            "runtime_source_bound": any(
                row.get("runtime_source_bound") is True for row in rows
            ),
            "method_receipt_hashes": tuple(sorted({
                str(row.get("method_receipt_hash"))
                for row in rows if len(str(row.get("method_receipt_hash") or "")) == 64
            })),
            "product_slot_ids": tuple(profile.get("parent_product_slot_ids") or ()),
            "strategy_clauses": tuple(profile.get("parent_strategy_clauses") or ()),
        })
    if len(result) != 43 or len({row["method_id"] for row in result}) != 43:
        raise TraceabilityError("STAGE1_METHOD_CLOSED_WORLD_MISMATCH")
    return tuple(result)


def _stage1_scope_projection_binding(
    scope_projection: Mapping,
    rendered_lookup: Mapping[str, Mapping],
) -> dict[str, Any]:
    """Normalize one scope projection/render binding for all Stage-1 consumers."""
    locations = tuple(
        str(value) for value in scope_projection.get("location_ids") or ()
    )
    projection_hash = str(scope_projection.get("projection_sha256") or "")
    projection_hashes = (
        tuple(projection_hash for _ in locations) if projection_hash else ()
    )
    admission_hashes = tuple(
        str(value)
        for value in scope_projection.get("product_admission_hashes") or ()
        if len(str(value)) == 64
    )
    rendered_matches = bool(
        locations
        and len(locations) == len(projection_hashes)
        and all(
            rendered_lookup.get(location_id, {}).get("projection_sha256")
            == expected_hash
            for location_id, expected_hash in zip(
                locations, projection_hashes,
            )
        )
    )
    return {
        "locations": locations,
        "projection_hashes": projection_hashes,
        "admission_hashes": admission_hashes,
        "rendered_matches": rendered_matches,
    }


def _stage1_data_object_consumer_row(
    row: Mapping,
    *,
    product_admitted: bool,
    admission: Mapping,
    scope_projection: Mapping,
    rendered_lookup: Mapping[str, Mapping],
    direct_leaf_dataset_ids: set[str],
) -> dict[str, Any]:
    """Bind one admitted object either to a numeric leaf or the exact scope audit.

    The V2.5 body remains investor-facing.  A non-price object may therefore be
    consumed by the accepted machine-scope projection plus its ordinary-language
    location; that is not a numeric leaf and never grants action authority.
    """
    dataset_id = str(row.get("dataset_id") or "")
    domain = str(row.get("domain") or "")
    current_applicable = (
        domain != "RECORD"
        and str(row.get("state") or "") != "NOT_APPLICABLE"
    )
    admission_hash = str(admission.get("admission_hash") or "")
    scope_binding = _stage1_scope_projection_binding(
        scope_projection, rendered_lookup,
    )
    scope_admission_hashes = scope_binding["admission_hashes"]
    scope_locations = scope_binding["locations"]
    scope_hashes = scope_binding["projection_hashes"]
    rendered_scope_matches = scope_binding["rendered_matches"]
    admission_bound = bool(
        product_admitted
        and admission.get("dataset_id") == dataset_id
        and admission.get("product_admitted") is True
        and len(admission_hash) == 64
    )
    scope_consumed = bool(
        admission_bound
        and scope_projection.get("id") == dataset_id
        and scope_projection.get("domain") == domain
        and scope_projection.get("state") == "READY"
        and admission_hash in scope_admission_hashes
        and scope_projection.get("consumer_projection")
        == "MACHINE_RECEIPT_WITH_ROLE_SUMMARIZED_EMAIL"
        and rendered_scope_matches
    )
    direct_leaf_consumed = bool(
        admission_bound and dataset_id in direct_leaf_dataset_ids
    )
    consumer_bound = direct_leaf_consumed or scope_consumed
    consumer_state = (
        "NOT_APPLICABLE"
        if not current_applicable
        else "NOT_PRODUCT_ADMITTED"
        if not product_admitted
        else "V25_PRICE_TIMEFRAME_LEAF_CONSUMED"
        if direct_leaf_consumed
        else "V25_MACHINE_SCOPE_CONSUMED"
        if scope_consumed
        else "V25_CONSUMER_BINDING_MISSING"
    )
    result = {
        "dataset_id": dataset_id,
        "domain": domain,
        "state": str(row.get("state") or "UNKNOWN"),
        "reason": str(row.get("reason") or ""),
        "product_admitted": product_admitted,
        "current_product_applicable": current_applicable,
        "source_identity_observed": str(row.get("state") or "") in {
            "READY", "PARTIAL",
        },
        "admission_hash": admission_hash or None,
        "v25_leaf_consumed": direct_leaf_consumed,
        "v25_scope_audit_consumed": scope_consumed,
        "v25_consumer_bound": consumer_bound,
        "v25_consumer_state": consumer_state,
        "scope_projection_state": scope_projection.get("state"),
        "scope_consumer_projection": scope_projection.get(
            "consumer_projection"
        ),
        "scope_location_ids": scope_locations,
        "scope_projection_hashes": scope_hashes,
        "scope_product_admission_hashes": scope_admission_hashes,
    }
    result["v25_consumer_binding_hash"] = digest(result)
    return result


def _stage1_atomic_subfield_consumer_row(
    identifier: str,
    *,
    admission: Mapping,
    scope_projection: Mapping,
    rendered_lookup: Mapping[str, Mapping],
) -> dict[str, Any]:
    admission_hash = str(admission.get("admission_hash") or "")
    scope_binding = _stage1_scope_projection_binding(
        scope_projection, rendered_lookup,
    )
    scope_admission_hashes = scope_binding["admission_hashes"]
    scope_locations = scope_binding["locations"]
    scope_hashes = scope_binding["projection_hashes"]
    rendered_scope_matches = scope_binding["rendered_matches"]
    source_fact_hash = str(scope_projection.get("source_fact_hash") or "")
    rendered_fact_hashes = tuple(sorted({
        str(value)
        for location_id in scope_locations
        for value in (
            rendered_lookup.get(location_id, {}).get("input_atomic_fact_hashes") or ()
        )
        if len(str(value)) == 64
    }))
    product_admitted = bool(
        admission.get("dataset_id") == identifier
        and admission.get("product_admitted") is True
        and len(admission_hash) == 64
    )
    # A visible, hashed missingness location proves that the V2.5 consumer is
    # wired even when the underlying atomic fact is not yet Product-admitted.
    # Keep that consumer proof separate from consumption of an admitted fact;
    # otherwise an external data gap is misreported as an implementation gap.
    scope_projection_bound = bool(
        scope_projection.get("id") == identifier
        and scope_projection.get("state") in {
            "BOUND", "UNRESOLVED_MISSINGNESS",
        }
        and scope_projection.get("consumer_projection")
        == "MACHINE_RECEIPT_WITH_ROLE_SUMMARIZED_EMAIL"
        and rendered_scope_matches
        and scope_locations
        and len(scope_locations) == len(scope_hashes)
        and all(len(str(value)) == 64 for value in scope_hashes)
    )
    admitted_fact_consumed = bool(
        product_admitted
        and scope_projection_bound
        and scope_projection.get("state") == "BOUND"
        and admission_hash in scope_admission_hashes
        and len(source_fact_hash) == 64
        and source_fact_hash in rendered_fact_hashes
        and str(scope_projection.get("fact_summary") or "").strip()
    )
    result = {
        "id": identifier,
        "product_admitted": product_admitted,
        "admission_hash": admission_hash or None,
        "v25_scope_audit_consumed": scope_projection_bound,
        "v25_consumer_bound": scope_projection_bound,
        "v25_atomic_fact_consumed": admitted_fact_consumed,
        "v25_consumer_state": (
            "V25_ATOMIC_SCOPE_CONSUMED"
            if admitted_fact_consumed
            else "V25_ATOMIC_MISSINGNESS_CONSUMED"
            if scope_projection_bound
            else "V25_ATOMIC_CONSUMER_BINDING_MISSING"
        ),
        "scope_projection_state": scope_projection.get("state"),
        "scope_consumer_projection": scope_projection.get(
            "consumer_projection"
        ),
        "scope_location_ids": scope_locations,
        "scope_projection_hashes": scope_hashes,
        "scope_product_admission_hashes": scope_admission_hashes,
        "source_fact_hash": source_fact_hash or None,
        "rendered_atomic_fact_hashes": rendered_fact_hashes,
        "v25_atomic_fact_hash_consumed": admitted_fact_consumed,
    }
    result["v25_consumer_binding_hash"] = digest(result)
    return result


def compile_stage1_vertical_product_receipt(
    *,
    materials: list[dict],
    assignment: Mapping,
    report_date: str,
    rendered_blocks: list[dict[str, Any]],
    slot_projections: list[dict[str, Any]],
    whole_scope_projection: Mapping,
) -> dict:
    """Compile one no-substitution Stage-1 Product vertical over existing owners."""
    rendered_lookup = {
        str(row.get("block_id") or ""): row
        for row in rendered_blocks if isinstance(row, Mapping)
    }
    scope_projection_lookup = {
        str(row.get("id") or ""): row
        for row in whole_scope_projection.get("data_object_projections") or ()
        if isinstance(row, Mapping)
    }
    if len(scope_projection_lookup) != 76:
        raise TraceabilityError("STAGE1_SCOPE_DATA_OBJECT_SET_MISMATCH")
    atomic_scope_projection_lookup = {
        str(row.get("id") or ""): row
        for row in whole_scope_projection.get("atomic_subfield_projections") or ()
        if isinstance(row, Mapping)
    }
    if (
        frozenset(atomic_scope_projection_lookup)
        != ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS
    ):
        raise TraceabilityError("STAGE1_SCOPE_ATOMIC_SUBFIELD_SET_MISMATCH")
    material_rows = []
    for material in materials:
        code = str(material.get("code") or "")
        factor = material["factor"]
        trace = validate_runtime_trace(factor)
        universe = trace["complete_research_universe"]
        gate = universe["full_domain_data_gate"]
        admitted_ids = {
            str(value) for value in gate.get("current_product_admitted_dataset_ids") or ()
        }
        methods = _stage1_method_rows(material)
        method_lookup = {row["method_id"]: row for row in methods}
        source_data_objects = tuple(
            dict(row) for row in universe.get("data_objects") or ()
            if isinstance(row, Mapping)
        )
        admission_lookup = {
            str(row.get("dataset_id") or ""): row
            for row in at(universe, "data_object_admission.objects") or ()
            if isinstance(row, Mapping)
        }
        atomic_admission_lookup = {
            str(row.get("dataset_id") or ""): row
            for row in at(universe, "atomic_subfield_admission.objects") or ()
            if isinstance(row, Mapping)
        }
        atomic_subfields = tuple(
            _stage1_atomic_subfield_consumer_row(
                identifier,
                admission=atomic_admission_lookup.get(identifier, {}),
                scope_projection=atomic_scope_projection_lookup.get(
                    identifier, {}
                ),
                rendered_lookup=rendered_lookup,
            )
            for identifier in sorted(
                ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS
            )
        )
        cells = []
        for cell in trace["timeframe_family_matrix"].get("cells") or ():
            timeframe = str(cell.get("timeframe") or "")
            dataset_id = (
                "E_PRICE_1D" if material["asset_type"] == "etf" else "P_DAILY"
            ) if timeframe in {"monthly", "weekly", "daily"} else (
                "E_PRICE_5M" if material["asset_type"] == "etf" else "P_5M"
            )
            current_method_ids = tuple(cell.get("current_method_ids") or ())
            cells.append({
                "cell_id": cell.get("cell_id"),
                "timeframe": timeframe,
                "family": cell.get("family"),
                "state": cell.get("state"),
                "reason": cell.get("reason"),
                "runtime_source_bound": bool(
                    at(cell, "runtime_binding.runtime_source_bound")
                ),
                "price_dataset_id": dataset_id,
                "price_dataset_product_admitted": dataset_id in admitted_ids,
                "current_method_ids": current_method_ids,
                "all_current_methods_invocation_verified": bool(current_method_ids) and all(
                    method_lookup.get(method_id, {}).get("actual_invocation_verified") is True
                    for method_id in current_method_ids
                ),
                "cell_hash": cell.get("cell_hash"),
            })
        coverage_lookup = {
            str(row.get("slot_id") or ""): row
            for row in material["coverage"].get("slots") or ()
            if isinstance(row, Mapping)
        }
        projected = [
            row for row in slot_projections
            if isinstance(row, Mapping) and str(row.get("asset_code") or "") == code
        ]
        slot_rows = []
        for row in projected:
            slot_id = str(row.get("slot_id") or "")
            coverage = coverage_lookup.get(slot_id, {})
            locations = tuple(str(value) for value in row.get("location_ids") or ())
            location_hashes = tuple(
                str(rendered_lookup.get(value, {}).get("projection_sha256") or "")
                for value in locations
            )
            leaf_hashes = tuple(sorted({
                str(rendered_lookup.get(value, {}).get("input_leaf_hash") or "")
                for value in locations
                if (
                    len(str(rendered_lookup.get(value, {}).get("input_leaf_hash") or "")) == 64
                    and rendered_lookup.get(value, {}).get(
                        "input_leaf_product_admitted"
                    ) is True
                    and rendered_lookup.get(value, {}).get(
                        "input_leaf_method_execution_verified"
                    ) is True
                )
            }))
            path_hashes = tuple(sorted({
                digest(value)
                for path in coverage.get("canonical_paths") or ()
                for value in (at(factor, str(path)),)
                if value is not None
            }))
            rendered_path_hashes = tuple(sorted({
                str(path_hash)
                for location_id in locations
                for path_hash in (
                    rendered_lookup.get(location_id, {}).get(
                        "input_canonical_path_hashes"
                    ) or ()
                )
                if len(str(path_hash)) == 64
            }))
            direct = slot_id in _STAGE1_DIRECT_SLOT_CONSUMERS
            timeframe_bundle = (
                slot_id.startswith(_STAGE1_TIMEFRAME_SLOT_PREFIX)
                or slot_id in _STAGE1_TIMEFRAME_COMPOSITE_SLOT_CONSUMERS
            )
            evidence_available = coverage.get("state") == "EVIDENCE_AVAILABLE"
            rendered_evidence = row.get("projection_state") in {
                "RENDERED_EVIDENCE", "RENDERED_PARTIAL_EVIDENCE",
            }
            leaf_consumed = bool(
                evidence_available
                and rendered_evidence
                and locations
                and all(len(value) == 64 for value in location_hashes)
                and (
                    (timeframe_bundle and leaf_hashes)
                    or (
                        direct
                        and path_hashes
                        and set(path_hashes) <= set(rendered_path_hashes)
                    )
                )
            )
            consumer_state = (
                "V25_LEAF_CONSUMED"
                if leaf_consumed and material["fact_mode"] == "ADMITTED"
                else "V25_PARTIAL_LEAF_CONSUMED"
                if leaf_consumed and material["fact_mode"] == "PARTIALLY_ADMITTED"
                else "SYNTHETIC_CONTRACT_ONLY"
                if leaf_consumed and material["fact_mode"] == "SYNTHETIC_TEST"
                else "V25_ROLE_SUMMARY_OR_MISSINGNESS_ONLY"
            )
            slot_row = {
                "slot_id": slot_id,
                "coverage_state": coverage.get("state"),
                "projection_state": row.get("projection_state"),
                "consumer_state": consumer_state,
                "location_ids": locations,
                "location_projection_hashes": location_hashes,
                "canonical_path_hashes": path_hashes,
                "rendered_canonical_path_hashes": rendered_path_hashes,
                "timeframe_leaf_hashes": leaf_hashes,
                "leaf_consumed": leaf_consumed,
            }
            slot_row["consumer_binding_hash"] = digest(slot_row)
            slot_rows.append(slot_row)
        rendered_price_dataset_ids = {
            str(rendered_lookup.get(location_id, {}).get("input_leaf_dataset_id") or "")
            for slot_row in slot_rows
            if slot_row.get("projection_state") in {
                "RENDERED_EVIDENCE", "RENDERED_PARTIAL_EVIDENCE",
            }
            for location_id in slot_row.get("location_ids") or ()
            if (
                rendered_lookup.get(location_id, {}).get(
                    "input_leaf_product_admitted"
                ) is True
                and rendered_lookup.get(location_id, {}).get(
                    "input_leaf_method_execution_verified"
                ) is True
            )
        }
        data_objects = tuple(
            _stage1_data_object_consumer_row(
                row,
                product_admitted=(
                    str(row.get("dataset_id") or "") in admitted_ids
                ),
                admission=admission_lookup.get(
                    str(row.get("dataset_id") or ""), {}
                ),
                scope_projection=scope_projection_lookup.get(
                    str(row.get("dataset_id") or ""), {}
                ),
                rendered_lookup=rendered_lookup,
                direct_leaf_dataset_ids=rendered_price_dataset_ids,
            )
            for row in source_data_objects
        )
        factor_as_of_instant = str(
            factor.get("research_as_of")
            or at(factor, "multi_timeframe_structure_context.snapshot_observed_at")
            or at(factor, "multi_timeframe_structure_context.available_at_max")
            or ""
        ).strip()
        try:
            factor_as_of_aware = _aware(factor_as_of_instant)
        except TraceabilityError:
            factor_as_of_aware = None
        factor_as_of = (
            factor_as_of_aware.date().isoformat()
            if factor_as_of_aware is not None else ""
        )
        as_of_current = bool(factor_as_of) and factor_as_of == report_date
        research_operation_id = str(
            factor.get("research_operation_id") or ""
        ).strip()
        research_intended_use = str(
            factor.get("research_intended_use") or ""
        ).strip()
        operation_context_valid = bool(research_operation_id) and (
            research_intended_use == "NO_SEND_RESEARCH"
        )
        thirty = at(factor, "multi_timeframe_structure_context.timeframes.30m")
        thirty = thirty if isinstance(thirty, Mapping) else {}
        trigger_requirement = next(
            (
                row for row in universe.get("requirements") or ()
                if isinstance(row, Mapping)
                and row.get("requirement_id") == "INTRADAY_30m"
            ),
            {},
        )
        carrier_trigger = {
            "carrier_state": str(thirty.get("status") or "MISSING"),
            "carrier_strategy_admitted": thirty.get("strategy_admitted") is True,
            "trigger_requirement_state": str(
                trigger_requirement.get("state") or "UNKNOWN"
            ),
            "carrier_is_not_trigger": thirty.get("strategy_admitted") is not True,
        }
        blockers = []
        if material["fact_mode"] != "ADMITTED" or gate.get("full_domain_product_ready") is not True:
            blockers.append("FULL_DOMAIN_PRODUCT_DATA_NOT_READY")
        if not as_of_current:
            blockers.append("RESEARCH_ASOF_MISSING_OR_REPORT_DATE_MISMATCH")
        if (
            material["fact_mode"] == "ADMITTED"
            and not operation_context_valid
        ):
            blockers.append(
                "RESEARCH_OPERATION_CONTEXT_MISSING_OR_INVALID"
            )
        if any(
            row["runtime_state"] in {"READY", "PARTIAL"}
            and row["actual_invocation_verified"] is not True
            for row in methods
        ):
            blockers.append("METHOD_OUTPUT_NOT_ACTUAL_INVOCATION_VERIFIED")
        if any(
            row["state"] in {"READY", "PARTIAL"}
            and (
                row["runtime_source_bound"] is not True
                or row["price_dataset_product_admitted"] is not True
            )
            for row in cells
        ):
            blockers.append("TECHNICAL_CELL_NOT_SOURCE_AND_PRODUCT_BOUND")
        if any(
            row["coverage_state"] == "EVIDENCE_AVAILABLE"
            and row["projection_state"] in {
                "RENDERED_EVIDENCE", "RENDERED_PARTIAL_EVIDENCE",
            }
            and row["leaf_consumed"] is not True
            for row in slot_rows
        ):
            blockers.append("V25_AVAILABLE_SLOT_LEAF_CONSUMPTION_INCOMPLETE")
        if any(
            row["current_product_applicable"] is True
            and row["product_admitted"] is True
            and row["v25_consumer_bound"] is not True
            for row in data_objects
        ):
            blockers.append("PRODUCT_DATA_OBJECT_V25_CONSUMER_BINDING_INCOMPLETE")
        if any(
            row["v25_consumer_bound"] is not True
            or (
                row["product_admitted"] is True
                and row["v25_atomic_fact_consumed"] is not True
            )
            for row in atomic_subfields
        ):
            blockers.append(
                "ATOMIC_SUBFIELD_V25_CONSUMER_BINDING_INCOMPLETE"
            )
        if carrier_trigger["carrier_strategy_admitted"]:
            blockers.append("TIMEFRAME_CARRIER_ILLEGALLY_PROMOTED_TO_TRIGGER")
        material_row = {
            "code": code,
            "asset_type": material["asset_type"],
            "fact_mode": material["fact_mode"],
            "research_as_of": factor_as_of or None,
            "research_as_of_instant": factor_as_of_instant or None,
            "research_operation_id": research_operation_id or None,
            "research_intended_use": research_intended_use or None,
            "research_operation_context_valid": operation_context_valid,
            "report_date": report_date,
            "research_as_of_current": as_of_current,
            "runtime_trace_hash": trace.get("runtime_trace_hash"),
            "method_rows": methods,
            "method_state_counts": dict(Counter(
                row["runtime_state"] for row in methods
            )),
            "actual_invocation_verified_count": sum(
                row["actual_invocation_verified"] is True for row in methods
            ),
            "data_object_rows": data_objects,
            "atomic_subfield_rows": atomic_subfields,
            "current_product_admitted_dataset_ids": tuple(sorted(admitted_ids)),
            "cell_rows": tuple(cells),
            "slot_rows": tuple(slot_rows),
            "carrier_trigger_separation": carrier_trigger,
            "blocker_codes": tuple(dict.fromkeys(blockers)),
            "state": "FACT_READY" if not blockers else "HOLD",
        }
        material_row["material_hash"] = digest(material_row)
        material_rows.append(material_row)
    blockers = tuple(dict.fromkeys(
        code
        for material in material_rows
        for code in material["blocker_codes"]
    ))
    if not material_rows:
        blockers = ("NO_BOUND_RESEARCH_MATERIAL",)
    document = {
        "schema_version": STAGE1_VERTICAL_PRODUCT_RECEIPT_VERSION,
        "report_date": report_date,
        "material_count": len(material_rows),
        "materials": tuple(material_rows),
        "whole_scope_projection_hash": whole_scope_projection.get("projection_hash"),
        "state": "FACT_READY" if material_rows and not blockers else "HOLD",
        "blocker_codes": blockers,
        "proof_scope": (
            "CURRENT_RUNTIME_TO_V25_NO_SEND_CONSUMER_BINDING_"
            "NOT_PROVIDER_RIGHTS_OR_DELIVERY_OR_LEARNING"
        ),
        "policy": {
            "registered_is_not_executed": True,
            "executed_is_not_product_admitted": True,
            "product_admitted_is_not_v25_consumed": True,
            "timeframe_carrier_is_not_strategy_trigger": True,
            "historical_execution_never_backfills_current_report": True,
            "role_summary_is_not_numeric_leaf_consumption": True,
            "role_summary_is_not_data_object_leaf_consumption": True,
            "machine_scope_receipt_is_data_object_consumer_not_numeric_leaf": True,
            "exact_atomic_admission_is_required_for_atomic_fact_consumption": True,
            "truthful_atomic_missingness_is_consumer_bound_not_fact_consumed": True,
            "strategy_training_delivery_authority": False,
        },
    }
    document["receipt_hash"] = digest(document)
    return document


def validate_stage1_vertical_product_receipt(value: Mapping) -> dict:
    if not isinstance(value, Mapping) or value.get("schema_version") != STAGE1_VERTICAL_PRODUCT_RECEIPT_VERSION:
        raise TraceabilityError("STAGE1_VERTICAL_RECEIPT_SCHEMA_MISMATCH")
    body = dict(value)
    claimed_hash = str(body.pop("receipt_hash", ""))
    if len(claimed_hash) != 64 or digest(body) != claimed_hash:
        raise TraceabilityError("STAGE1_VERTICAL_RECEIPT_HASH_MISMATCH")
    materials = tuple(value.get("materials") or ())
    if value.get("material_count") != len(materials):
        raise TraceabilityError("STAGE1_VERTICAL_MATERIAL_COUNT_MISMATCH")
    calculated_all_blockers: list[str] = []
    for material in materials:
        if not isinstance(material, Mapping):
            raise TraceabilityError("STAGE1_VERTICAL_MATERIAL_INVALID")
        material_body = dict(material)
        material_hash = str(material_body.pop("material_hash", ""))
        if len(material_hash) != 64 or digest(material_body) != material_hash:
            raise TraceabilityError("STAGE1_VERTICAL_MATERIAL_HASH_MISMATCH")
        methods = tuple(material.get("method_rows") or ())
        cells = tuple(material.get("cell_rows") or ())
        data_objects = tuple(material.get("data_object_rows") or ())
        atomic_subfields = tuple(
            material.get("atomic_subfield_rows") or ()
        )
        slots = tuple(material.get("slot_rows") or ())
        if len(methods) != 43 or len({row.get("method_id") for row in methods}) != 43:
            raise TraceabilityError("STAGE1_VERTICAL_METHOD_COUNT_MISMATCH")
        if len(cells) != 56 or len({row.get("cell_id") for row in cells}) != 56:
            raise TraceabilityError("STAGE1_VERTICAL_CELL_COUNT_MISMATCH")
        if len(data_objects) != 76 or len({row.get("dataset_id") for row in data_objects}) != 76:
            raise TraceabilityError("STAGE1_VERTICAL_DATA_OBJECT_COUNT_MISMATCH")
        if (
            len(atomic_subfields) != 7
            or frozenset(
                str(row.get("id") or "") for row in atomic_subfields
            ) != ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS
        ):
            raise TraceabilityError(
                "STAGE1_VERTICAL_ATOMIC_SUBFIELD_COUNT_MISMATCH"
            )
        for atomic_row in atomic_subfields:
            if not isinstance(atomic_row, Mapping):
                raise TraceabilityError(
                    "STAGE1_VERTICAL_ATOMIC_SUBFIELD_INVALID"
                )
            binding_body = dict(atomic_row)
            binding_hash = str(
                binding_body.pop("v25_consumer_binding_hash", "")
            )
            if len(binding_hash) != 64 or digest(binding_body) != binding_hash:
                raise TraceabilityError(
                    "STAGE1_VERTICAL_ATOMIC_CONSUMER_HASH_MISMATCH"
                )
            consumer_bound = atomic_row.get("v25_consumer_bound") is True
            fact_consumed = atomic_row.get("v25_atomic_fact_consumed") is True
            expected_state = (
                "V25_ATOMIC_SCOPE_CONSUMED"
                if fact_consumed
                else "V25_ATOMIC_MISSINGNESS_CONSUMED"
                if consumer_bound
                else "V25_ATOMIC_CONSUMER_BINDING_MISSING"
            )
            if atomic_row.get("v25_consumer_state") != expected_state:
                raise TraceabilityError(
                    "STAGE1_VERTICAL_FALSE_ATOMIC_CONSUMPTION"
                )
            if consumer_bound and not (
                atomic_row.get("scope_projection_state") in {
                    "BOUND", "UNRESOLVED_MISSINGNESS",
                }
                and atomic_row.get("scope_consumer_projection")
                == "MACHINE_RECEIPT_WITH_ROLE_SUMMARIZED_EMAIL"
                and tuple(atomic_row.get("scope_location_ids") or ())
                and len(tuple(atomic_row.get("scope_location_ids") or ()))
                == len(tuple(atomic_row.get("scope_projection_hashes") or ()))
                and all(
                    len(str(item)) == 64
                    for item in atomic_row.get("scope_projection_hashes") or ()
                )
            ):
                raise TraceabilityError(
                    "STAGE1_VERTICAL_FALSE_ATOMIC_CONSUMPTION"
                )
            if fact_consumed and not (
                atomic_row.get("product_admitted") is True
                and len(str(atomic_row.get("admission_hash") or "")) == 64
                and len(str(atomic_row.get("source_fact_hash") or "")) == 64
                and atomic_row.get("v25_atomic_fact_hash_consumed") is True
                and str(atomic_row.get("source_fact_hash") or "") in {
                    str(item)
                    for item in (
                        atomic_row.get("rendered_atomic_fact_hashes") or ()
                    )
                }
                and atomic_row.get("scope_projection_state") == "BOUND"
                and str(atomic_row.get("admission_hash") or "") in {
                    str(item)
                    for item in (
                        atomic_row.get("scope_product_admission_hashes") or ()
                    )
                }
            ):
                raise TraceabilityError(
                    "STAGE1_VERTICAL_FALSE_ATOMIC_CONSUMPTION"
                )
            if atomic_row.get("product_admitted") is True and not fact_consumed:
                raise TraceabilityError(
                    "STAGE1_VERTICAL_FALSE_ATOMIC_CONSUMPTION"
                )
        for data_row in data_objects:
            if not isinstance(data_row, Mapping):
                raise TraceabilityError("STAGE1_VERTICAL_DATA_OBJECT_INVALID")
            binding_body = dict(data_row)
            binding_hash = str(
                binding_body.pop("v25_consumer_binding_hash", "")
            )
            if len(binding_hash) != 64 or digest(binding_body) != binding_hash:
                raise TraceabilityError(
                    "STAGE1_VERTICAL_DATA_OBJECT_CONSUMER_HASH_MISMATCH"
                )
            dataset_id = str(data_row.get("dataset_id") or "")
            direct_leaf = data_row.get("v25_leaf_consumed") is True
            scope_audit = data_row.get("v25_scope_audit_consumed") is True
            consumer_bound = data_row.get("v25_consumer_bound") is True
            applicable = data_row.get("current_product_applicable") is True
            product_admitted = data_row.get("product_admitted") is True
            if direct_leaf and not (
                product_admitted
                and len(str(data_row.get("admission_hash") or "")) == 64
                and dataset_id in {
                    "P_DAILY", "P_5M", "E_PRICE_1D", "E_PRICE_5M",
                }
            ):
                raise TraceabilityError(
                    "STAGE1_VERTICAL_FALSE_DATA_OBJECT_CONSUMPTION"
                )
            if scope_audit and not (
                product_admitted
                and len(str(data_row.get("admission_hash") or "")) == 64
                and data_row.get("scope_projection_state") == "READY"
                and str(data_row.get("admission_hash") or "") in {
                    str(item)
                    for item in (
                        data_row.get("scope_product_admission_hashes") or ()
                    )
                }
                and data_row.get("scope_consumer_projection")
                == "MACHINE_RECEIPT_WITH_ROLE_SUMMARIZED_EMAIL"
                and tuple(data_row.get("scope_location_ids") or ())
                and all(
                    str(item).startswith("block-")
                    for item in data_row.get("scope_location_ids") or ()
                )
                and all(
                    len(str(item)) == 64
                    for item in data_row.get("scope_projection_hashes") or ()
                )
                and len(tuple(data_row.get("scope_location_ids") or ()))
                == len(tuple(data_row.get("scope_projection_hashes") or ()))
            ):
                raise TraceabilityError(
                    "STAGE1_VERTICAL_FALSE_DATA_OBJECT_CONSUMPTION"
                )
            if consumer_bound != (direct_leaf or scope_audit):
                raise TraceabilityError(
                    "STAGE1_VERTICAL_FALSE_DATA_OBJECT_CONSUMPTION"
                )
            expected_consumer_state = (
                "NOT_APPLICABLE"
                if not applicable
                else "NOT_PRODUCT_ADMITTED"
                if not product_admitted
                else "V25_PRICE_TIMEFRAME_LEAF_CONSUMED"
                if direct_leaf
                else "V25_MACHINE_SCOPE_CONSUMED"
                if scope_audit
                else "V25_CONSUMER_BINDING_MISSING"
            )
            if data_row.get("v25_consumer_state") != expected_consumer_state:
                raise TraceabilityError(
                    "STAGE1_VERTICAL_FALSE_DATA_OBJECT_CONSUMPTION"
                )
        carrier = material.get("carrier_trigger_separation", {})
        if carrier.get("carrier_is_not_trigger") is not True:
            raise TraceabilityError("STAGE1_VERTICAL_CARRIER_TRIGGER_COLLAPSE")
        for slot in slots:
            if not isinstance(slot, Mapping):
                raise TraceabilityError("STAGE1_VERTICAL_SLOT_INVALID")
            slot_body = dict(slot)
            slot_hash = str(slot_body.pop("consumer_binding_hash", ""))
            if len(slot_hash) != 64 or digest(slot_body) != slot_hash:
                raise TraceabilityError("STAGE1_VERTICAL_SLOT_HASH_MISMATCH")
            slot_id = str(slot.get("slot_id") or "")
            direct = slot_id in _STAGE1_DIRECT_SLOT_CONSUMERS
            timeframe_bundle = (
                slot_id.startswith(_STAGE1_TIMEFRAME_SLOT_PREFIX)
                or slot_id in _STAGE1_TIMEFRAME_COMPOSITE_SLOT_CONSUMERS
            )
            canonical_hashes = {
                str(item) for item in slot.get("canonical_path_hashes") or ()
            }
            rendered_hashes = {
                str(item)
                for item in slot.get("rendered_canonical_path_hashes") or ()
            }
            binding_verified = bool(
                (timeframe_bundle and slot.get("timeframe_leaf_hashes"))
                or (
                    direct
                    and canonical_hashes
                    and canonical_hashes <= rendered_hashes
                )
            )
            if slot.get("consumer_state") in {
                "V25_LEAF_CONSUMED", "V25_PARTIAL_LEAF_CONSUMED",
            } and not (
                slot.get("leaf_consumed") is True
                and slot.get("location_ids")
                and all(
                    len(str(item)) == 64
                    for item in slot.get("location_projection_hashes") or ()
                )
                and binding_verified
            ):
                raise TraceabilityError("STAGE1_VERTICAL_FALSE_V25_CONSUMPTION")
        calculated: list[str] = []
        current_applicable = tuple(
            row for row in data_objects
            if row.get("current_product_applicable") is True
        )
        if (
            material.get("fact_mode") != "ADMITTED"
            or not current_applicable
            or any(row.get("product_admitted") is not True for row in current_applicable)
        ):
            calculated.append("FULL_DOMAIN_PRODUCT_DATA_NOT_READY")
        if material.get("research_as_of_current") is not True:
            calculated.append("RESEARCH_ASOF_MISSING_OR_REPORT_DATE_MISMATCH")
        operation_context_valid = (
            material.get("research_operation_context_valid") is True
        )
        if material.get("fact_mode") == "ADMITTED" and not operation_context_valid:
            calculated.append(
                "RESEARCH_OPERATION_CONTEXT_MISSING_OR_INVALID"
            )
        if operation_context_valid:
            try:
                instant = _aware(material.get("research_as_of_instant"))
            except TraceabilityError as exc:
                raise TraceabilityError(
                    "STAGE1_VERTICAL_RESEARCH_ASOF_INSTANT_INVALID"
                ) from exc
            if (
                instant.date().isoformat() != material.get("research_as_of")
                or not str(material.get("research_operation_id") or "").strip()
                or material.get("research_intended_use") != "NO_SEND_RESEARCH"
                or (
                    material.get("fact_mode") == "ADMITTED"
                    and material.get("research_as_of")
                    != material.get("report_date")
                )
            ):
                raise TraceabilityError(
                    "STAGE1_VERTICAL_RESEARCH_OPERATION_CONTEXT_DRIFT"
                )
        if any(
            row.get("runtime_state") in {"READY", "PARTIAL"}
            and row.get("actual_invocation_verified") is not True
            for row in methods
        ):
            calculated.append("METHOD_OUTPUT_NOT_ACTUAL_INVOCATION_VERIFIED")
        if any(
            row.get("state") in {"READY", "PARTIAL"}
            and (
                row.get("runtime_source_bound") is not True
                or row.get("price_dataset_product_admitted") is not True
            )
            for row in cells
        ):
            calculated.append("TECHNICAL_CELL_NOT_SOURCE_AND_PRODUCT_BOUND")
        if any(
            row.get("coverage_state") == "EVIDENCE_AVAILABLE"
            and row.get("projection_state") in {
                "RENDERED_EVIDENCE", "RENDERED_PARTIAL_EVIDENCE",
            }
            and row.get("leaf_consumed") is not True
            for row in slots
        ):
            calculated.append("V25_AVAILABLE_SLOT_LEAF_CONSUMPTION_INCOMPLETE")
        if any(
            row.get("current_product_applicable") is True
            and row.get("product_admitted") is True
            and row.get("v25_consumer_bound") is not True
            for row in data_objects
        ):
            calculated.append(
                "PRODUCT_DATA_OBJECT_V25_CONSUMER_BINDING_INCOMPLETE"
            )
        if any(
            row.get("v25_consumer_bound") is not True
            or (
                row.get("product_admitted") is True
                and row.get("v25_atomic_fact_consumed") is not True
            )
            for row in atomic_subfields
        ):
            calculated.append(
                "ATOMIC_SUBFIELD_V25_CONSUMER_BINDING_INCOMPLETE"
            )
        if carrier.get("carrier_strategy_admitted") is True:
            calculated.append("TIMEFRAME_CARRIER_ILLEGALLY_PROMOTED_TO_TRIGGER")
        calculated_blockers = tuple(dict.fromkeys(calculated))
        claimed_blockers = tuple(material.get("blocker_codes") or ())
        if claimed_blockers != calculated_blockers:
            raise TraceabilityError("STAGE1_VERTICAL_BLOCKER_VECTOR_MISMATCH")
        expected_state = "FACT_READY" if not calculated_blockers else "HOLD"
        if material.get("state") != expected_state:
            raise TraceabilityError("STAGE1_VERTICAL_FALSE_FACT_READY")
        calculated_all_blockers.extend(calculated_blockers)
    expected_blockers = tuple(dict.fromkeys(calculated_all_blockers))
    if not materials:
        expected_blockers = ("NO_BOUND_RESEARCH_MATERIAL",)
    if tuple(value.get("blocker_codes") or ()) != expected_blockers:
        raise TraceabilityError("STAGE1_VERTICAL_GLOBAL_BLOCKER_VECTOR_MISMATCH")
    expected_state = "FACT_READY" if materials and not expected_blockers else "HOLD"
    if value.get("state") != expected_state:
        raise TraceabilityError("STAGE1_VERTICAL_GLOBAL_STATE_MISMATCH")
    return dict(value)


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
    assignment["scope_universe"] = scope_universe
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
        assignment,
    )
    assignment["scope_text_by_block"] = scope_text_by_block
    atomic_fact_hashes_by_block: dict[str, tuple[str, ...]] = {}
    for row in scope_projection_rows:
        if row.get("kind") != "atomic_subfield":
            continue
        fact_hash = str(row.get("source_fact_hash") or "")
        if len(fact_hash) != 64:
            continue
        for location_id in row.get("location_ids") or ():
            values = set(atomic_fact_hashes_by_block.get(str(location_id), ()))
            values.add(fact_hash)
            atomic_fact_hashes_by_block[str(location_id)] = tuple(sorted(values))
    assignment["scope_atomic_fact_hashes_by_block"] = atomic_fact_hashes_by_block

    replacements: list[tuple[int, int, str]] = []
    rendered_blocks = []
    for block_index, text_nodes in grouped.items():
        block = parser.nodes[block_index]
        inline = [
            item for item in text_nodes
            if parser.nodes[item["node"]]["tag"] in {"b", "strong"}
            and item["raw"].strip()
        ]
        primary = inline[0] if inline else next(
            (item for item in text_nodes if item["node"] == block_index),
            text_nodes[0],
        )
        original_text = "".join(item["raw"] for item in text_nodes).strip()
        projected = _v25_project_block(
            parser, block, original_text, assignment, block_ordinals[block_index], report_date
        ).strip() or "DATA_INSUFFICIENT"
        scope_text = str(scope_text_by_block.get(block_index) or "").strip()
        if scope_text and scope_text not in projected:
            projected = f"{projected}｜{scope_text}"
        escaped = html_escape(projected, quote=False)
        inline_rank = 0
        for item in text_nodes:
            replacement = ""
            node_tag = parser.nodes[item["node"]]["tag"]
            if item is primary:
                replacement = escaped
            elif node_tag in {"b", "strong"}:
                replacement = html_escape(
                    _v25_inline_role_label(
                        parser, block, item["raw"], inline_rank,
                    ),
                    quote=False,
                )
                inline_rank += 1
            elif node_tag == "span" and any(
                "kpi" in node["classes"]
                for node in _v25_chain(parser, block["index"])
            ):
                kpis = [
                    node for node in parser.nodes
                    if node["mail"] == block["mail"] and "kpi" in node["classes"]
                ]
                kpi_node = next(
                    node for node in _v25_chain(parser, block["index"])
                    if "kpi" in node["classes"]
                )
                kpi_index = next(
                    index for index, node in enumerate(kpis)
                    if node["index"] == kpi_node["index"]
                )
                replacement = html_escape(
                    _v25_kpi_text(kpi_index)[1], quote=False,
                )
            replacements.append((item["start"], item["end"], replacement))
        card = _v25_nearest(parser, block_index, class_name="card")
        cycle = _v25_nearest(parser, block_index, class_name="cycle")
        semantic = _v25_semantic_for_block(parser, block_index)
        card_id = card.get("id") if card else None
        cycle_id = cycle.get("id") if cycle else None
        cycle_timeframe = (
            TIMEFRAMES[int(str(cycle_id).split("-")[1]) % len(TIMEFRAMES)]
            if cycle_id else None
        )
        material = assignment["cards"].get(card_id) if card_id else None
        leaf_payload = (
            _v25_timeframe_leaf_payload(material, cycle_timeframe)
            if cycle_timeframe and semantic != "cycle-title" else {}
        )
        indicator_family = next(
            (
                requirement
                for label, requirement in _V25_FAMILY_REQUIREMENTS
                if projected.startswith(label + "｜")
            ),
            None,
        )
        rendered_blocks.append({
            "block_id": f"block-{block_index}",
            "mail": block["mail"],
            "card_id": card_id,
            "cycle_id": cycle_id,
            "timeframe": cycle_timeframe,
            "semantic": semantic,
            "tag": block["tag"],
            "projection_contract": (
                "TIMEFRAME_LEAF_BUNDLE_V1" if leaf_payload else
                "INDICATOR_FAMILY_ROLE_SUMMARY_V1" if indicator_family else
                "ROLE_SUMMARY_OR_DIRECT_LEAF_V1"
            ),
            "indicator_family": indicator_family,
            "input_leaf_hash": digest(leaf_payload) if leaf_payload else None,
            "input_leaf_product_admitted": (
                leaf_payload.get("product_admitted") is True if leaf_payload else False
            ),
            "input_leaf_dataset_id": (
                leaf_payload.get("dataset_id") if leaf_payload else None
            ),
            "input_leaf_method_execution_verified": (
                any((leaf_payload.get("family_execution_verified") or {}).values())
                if leaf_payload else False
            ),
            "input_canonical_path_hashes": _v25_block_canonical_path_hashes(
                material,
                semantic=semantic,
                cycle_timeframe=cycle_timeframe,
            ),
            "input_atomic_fact_hashes": tuple(
                assignment.get("scope_atomic_fact_hashes_by_block", {}).get(
                    f"block-{block_index}", ()
                )
            ),
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
    email_consumer_quality = _v25_email_consumer_quality(output)
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

    rendered_block_lookup = {
        str(row.get("block_id") or ""): row for row in rendered_blocks
    }
    slot_projections = []
    for card_id, material in assignment["cards"].items():
        by_slot = {
            row["slot_id"]: row
            for row in material["coverage"]["slots"]
        } if material else {}
        for slot_id in sorted(ACCEPTED_PRODUCT_SLOT_IDS):
            coverage_row = by_slot.get(slot_id)
            location_ids = _v25_slot_block_ids(
                slot_id,
                card_id,
                rendered_blocks,
            )
            product_leaf_visible = any(
                rendered_block_lookup.get(location_id, {}).get(
                    "input_leaf_product_admitted"
                ) is True
                for location_id in location_ids
            )
            evidence_available = (
                isinstance(coverage_row, Mapping)
                and coverage_row.get("state") == "EVIDENCE_AVAILABLE"
            )
            state = (
                "RENDERED_EVIDENCE"
                if material is not None
                and material["fact_mode"] in {"ADMITTED", "SYNTHETIC_TEST"}
                and evidence_available
                else "RENDERED_PARTIAL_EVIDENCE"
                if material is not None
                and material["fact_mode"] == "PARTIALLY_ADMITTED"
                and evidence_available
                and product_leaf_visible
                else "RENDERED_MISSINGNESS"
            )
            slot_projections.append({
                "card_id": card_id,
                "asset_code": material.get("code") if material else None,
                "slot_id": slot_id,
                "projection_state": state,
                "product_leaf_visible": product_leaf_visible,
                "location_ids": location_ids,
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
        "scope_research_as_of_values": tuple(
            scope_universe.get("scope_research_as_of_values") or ()
        ),
        "scope_research_operation_ids": tuple(
            scope_universe.get("scope_research_operation_ids") or ()
        ),
        "scope_research_intended_uses": tuple(
            scope_universe.get("scope_research_intended_uses") or ()
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
        "email_consumer_quality": email_consumer_quality,
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
            "current_product_admitted_count": item["gate"].get(
                "current_product_admitted_count", 0,
            ),
            "unresolved_record_dataset_ids": tuple(
                item["gate"].get("unresolved_record_dataset_ids") or ()
            ),
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
                if row.get("domain") != "RECORD"
            )
            and len(
                whole_scope_projection["scope_research_as_of_values"]
            ) == 1
            and len(
                whole_scope_projection["scope_research_operation_ids"]
            ) == 1
            and whole_scope_projection[
                "scope_research_intended_uses"
            ] == ("NO_SEND_RESEARCH",)
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
    receipt["stage1_vertical_product_receipt"] = (
        compile_stage1_vertical_product_receipt(
            materials=materials,
            assignment=assignment,
            report_date=report_date,
            rendered_blocks=rendered_blocks,
            slot_projections=slot_projections,
            whole_scope_projection=whole_scope_projection,
        )
    )
    validate_stage1_vertical_product_receipt(
        receipt["stage1_vertical_product_receipt"]
    )
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
