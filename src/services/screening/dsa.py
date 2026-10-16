# -*- coding: utf-8 -*-
# Derived from AlphaSift revision 9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf.
# Licensed under Apache-2.0 and modified for daily_stock_analysis.
"""Optional DSA integration for L3 deep analysis."""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

import requests

from src.services.screening.models import Pick

logger = logging.getLogger(__name__)
_DEFAULT_ANALYZE_PATH = "/api/v1/analysis/analyze"


def analyze_picks_with_dsa(
    picks: list[Pick],
    *,
    run_id: str,
    api_url: str,
    report_type: str = "detailed",
    max_picks: int = 3,
    timeout_sec: float = 120.0,
    force_refresh: bool = False,
    notify: bool = False,
) -> tuple[list[Pick], list[str]]:
    """Run DSA deep analysis for up to ``max_picks`` candidates in place.

    This low-level helper only records attempted candidates. The post-analysis
    orchestrator records an explicit ``skipped`` status for candidates beyond
    the configured cap so downstream selection cannot treat them as analyzed.
    """
    if not api_url:
        raise ValueError("DSA_API_URL is required when deep_analysis=True")
    if max_picks <= 0:
        return picks, []

    analyze_count = min(max_picks, len(picks))
    degradation: list[str] = []
    endpoint = build_dsa_analyze_url(api_url)

    for idx, pick in enumerate(picks):
        if idx >= analyze_count:
            # The post-analysis orchestrator owns the explicit skipped status.
            continue

        try:
            result = call_dsa_analysis(
                endpoint,
                stock_code=pick.code,
                stock_name=pick.name,
                report_type=report_type,
                query_id=f"{run_id}-{pick.rank}-{pick.code}",
                timeout_sec=timeout_sec,
                force_refresh=force_refresh,
                notify=notify,
            )
            pick.deep_analysis_status = "completed"
            pick.deep_analysis_query_id = str(result.get("query_id", ""))
            pick.deep_analysis_result = result
            pick.deep_analysis_summary = extract_deep_analysis_summary(result)
            _attach_deep_analysis_fields(pick, result)
        except Exception as exc:
            logger.warning("DSA deep analysis failed for %s: %s", pick.code, exc)
            pick.deep_analysis_status = "failed"
            pick.deep_analysis_error = str(exc)
            degradation.append(f"DSA deep analysis failed for {pick.code}: {exc}")

    return picks, degradation


def build_dsa_analyze_url(api_url: str) -> str:
    """Accept a base URL or a full endpoint URL."""
    stripped = api_url.rstrip("/")
    parsed = urlparse(stripped)
    if parsed.path and parsed.path not in ("", "/"):
        return stripped
    return f"{stripped}{_DEFAULT_ANALYZE_PATH}"


def check_dsa_readiness(api_url: str, *, timeout_sec: float = 5.0) -> dict:
    """Best-effort DSA endpoint readiness probe for CLI/runtime diagnostics."""
    if not api_url:
        return {
            "available": False,
            "status": "missing_url",
            "endpoint": "",
            "http_status": None,
            "error": "DSA_API_URL is not configured",
        }

    endpoint = build_dsa_analyze_url(api_url)
    try:
        response = requests.get(endpoint, timeout=timeout_sec)
    except requests.RequestException as exc:
        return {
            "available": False,
            "status": "unreachable",
            "endpoint": endpoint,
            "http_status": None,
            "error": str(exc),
        }

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in {200, 204, 405, 422}:
        status = "route_present"
        available = True
    elif status_code in {401, 403}:
        status = "unauthorized"
        available = False
    elif status_code == 404:
        status = "route_missing"
        available = False
    else:
        status = "unexpected_status"
        available = False

    return {
        "available": available,
        "status": status,
        "endpoint": endpoint,
        "http_status": status_code,
        "error": "" if available else _safe_str(getattr(response, "text", ""))[:280],
    }


def call_dsa_analysis(
    endpoint: str,
    *,
    stock_code: str,
    stock_name: str = "",
    report_type: str = "detailed",
    query_id: str = "",
    timeout_sec: float = 120.0,
    force_refresh: bool = False,
    notify: bool = False,
) -> dict:
    """Call the DSA sync analysis endpoint and return parsed JSON."""
    payload = {
        "stock_code": stock_code,
        "report_type": report_type,
        "force_refresh": force_refresh,
        "async_mode": False,
        "stock_name": stock_name or None,
        "original_query": stock_code,
        "selection_source": "import",
        "notify": notify,
    }
    if query_id:
        # The current DSA public API does not require query_id,
        # but we keep it in the payload for forward compatibility.
        payload["query_id"] = query_id

    response = requests.post(endpoint, json=payload, timeout=timeout_sec)
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError:
        return {"raw_text": response.text}
    if not isinstance(body, dict):
        return {"raw_result": body}
    return body


def extract_deep_analysis_summary(result: dict) -> str:
    """Best-effort extraction of a short summary from a DSA response."""
    if not isinstance(result, dict):
        return ""

    for key in ("summary", "analysis_summary", "conclusion", "message"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    report = result.get("report")
    if isinstance(report, dict):
        summary = report.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        if isinstance(summary, dict):
            for key in (
                "analysis_summary",
                "summary",
                "conclusion",
                "recommendation",
                "operation_advice",
                "signal_level",
            ):
                value = summary.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            rendered = json.dumps(summary, ensure_ascii=False)
            return rendered[:280]

    rendered = json.dumps(result, ensure_ascii=False)
    return rendered[:280]


def apply_dsa_overlay(picks: list[Pick]) -> list[Pick]:
    """Use DSA structured output as the final low-frequency risk/alpha overlay."""
    rescored: list[tuple[int, Pick]] = []
    for idx, pick in enumerate(picks):
        base_score = float(pick.final_score)
        if pick.deep_analysis_status == "completed" and not pick.excluded_by_risk:
            base_score += _compute_dsa_overlay_score(pick)
        pick.final_score = round(base_score, 4)
        rescored.append((idx, pick))

    rescored.sort(key=lambda item: (-item[1].final_score, item[0]))
    reranked = [pick for _, pick in rescored]
    for i, pick in enumerate(reranked, start=1):
        pick.rank = i
    return reranked


def _attach_deep_analysis_fields(pick: Pick, result: dict) -> None:
    summary = _extract_report_summary(result)
    trend = _extract_trend_result(result)

    pick.deep_analysis_signal_score = _safe_int(trend.get("signal_score"))
    pick.deep_analysis_sentiment_score = _safe_int(summary.get("sentiment_score"))
    pick.deep_analysis_operation_advice = _safe_str(summary.get("operation_advice"))
    pick.deep_analysis_trend_prediction = _safe_str(summary.get("trend_prediction"))
    pick.deep_analysis_risk_flags = _extract_risk_flags(trend)

    mtf_projection = _qualified_mtf_screening_projection(
        result,
        expected_code=pick.code,
    )
    pick.mtf_screening_status = str(mtf_projection.get("status") or "REJECTED")
    pick.mtf_screening_delta = float(mtf_projection.get("delta") or 0.0)
    pick.mtf_screening_summary = str(mtf_projection.get("summary") or "")
    pick.mtf_screening_groups = dict(mtf_projection.get("groups") or {})
    pick.mtf_screening_timeframes = dict(mtf_projection.get("timeframes") or {})
    pick.post_analysis_score_deltas["dsa_mtf_roles"] = pick.mtf_screening_delta
    pick.post_analysis_results["dsa_mtf_roles"] = mtf_projection


def _qualified_dsa_overlay_reference_score(result: dict | None) -> int | None:
    """Use the exact existing Factor score identity, not the raw API score.

    A public report containing only a legacy score or explanation is not
    positive evidence of method execution or permission to reorder picks.
    """
    factor = _extract_factor_decision(result)
    if not isinstance(factor, dict):
        return None
    if factor.get("method_execution_receipt_policy") != "REQUIRED":
        return None
    admission = factor.get("score_admission")
    if not isinstance(admission, dict) or admission.get("eligible_for_ranking") is not True:
        return None
    if admission.get("producer_scheme") != "StockTrendAnalyzer.signal_score/legacy":
        return None
    decision = factor.get("canonical_decision")
    if isinstance(decision, dict) and decision.get("hard_veto") is True:
        return None  # A certified veto cannot be offset by a positive score.
    score = factor.get("composite_score")
    if type(score) is not int or not 0 <= score <= 100:
        return None
    if admission.get("raw_reference_score_audit") != score:
        return None
    from src.services.factor_decision_summary import validate_canonical_factor_binding

    try:
        validate_canonical_factor_binding(factor)
    except (ValueError, TypeError):
        return None
    return score


def _compute_dsa_overlay_score(pick: Pick) -> float:
    """Combine two bounded, independently audited DSA shortlist preferences.

    The legacy Factor reference remains conditional on its exact admission.
    The MTF component consumes four hierarchical role groups, not seven
    independent votes or presentation text. Neither component can override an
    existing risk exclusion or create canonical BUY authority.
    """
    qualified = _qualified_dsa_overlay_reference_score(pick.deep_analysis_result)
    reference_delta = (qualified - 50) * 0.20 if qualified is not None else 0.0
    pick.post_analysis_score_deltas["dsa_reference_score"] = round(
        reference_delta, 4
    )
    mtf_delta = (
        float(pick.mtf_screening_delta)
        if pick.mtf_screening_status == "VERIFIED"
        else 0.0
    )
    return reference_delta + mtf_delta


def _extract_factor_decision(result: dict | None) -> dict | None:
    if not isinstance(result, dict):
        return None
    report = result.get("report")
    details = report.get("details") if isinstance(report, dict) else None
    raw = details.get("raw_result") if isinstance(details, dict) else None
    dashboard = raw.get("dashboard") if isinstance(raw, dict) else None
    factor = dashboard.get("factor_decision") if isinstance(dashboard, dict) else None
    return factor if isinstance(factor, dict) else None


_MTF_TIMEFRAMES = ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m")
MTF_SCREENING_ROLE_GROUPS = (
    "context", "setup", "bridge_structure", "confirmation_timing",
)
MTF_SCREENING_DELTA_BOUNDS = (-6.0, 4.0)
_MTF_POSITIVE_MACD = {
    "GOLDEN_CROSS_ZERO", "GOLDEN_CROSS", "BULLISH", "CROSSING_UP",
    "零轴上金叉", "金叉", "多头", "上穿零轴",
}
_MTF_NEGATIVE_MACD = {
    "BEARISH", "DEATH_CROSS", "CROSSING_DOWN",
    "空头", "死叉", "下穿零轴",
}
_MTF_ADVERSE_RSI = {"OVERBOUGHT", "WEAK", "OVERSOLD", "超买", "弱势", "超卖"}
_MTF_POSITIVE_STRUCTURE = {
    "UP_BREAKOUT", "UP_BREAKOUT_RETEST_HOLD", "FAILED_DOWN_BREAKDOWN",
    # Backward-compatible names from older structure projections.
    "FAILED_DOWN_BREAKOUT",
}
_MTF_NEGATIVE_STRUCTURE = {
    "FAILED_UP_BREAKOUT", "DOWN_BREAKDOWN", "DOWN_BREAKDOWN_RETEST_REJECT",
    # Backward-compatible names from older structure projections.
    "DOWN_BREAKOUT", "DOWN_BREAKOUT_RETEST_HOLD",
}


def _qualified_mtf_screening_projection(
    result: dict | None,
    *,
    expected_code: str = "",
) -> dict:
    """Project verified seven-timeframe evidence into four ordered role groups.

    This is a bounded shortlist preference. It never recomputes indicators,
    counts correlated timeframes as votes, changes canonical, or grants action,
    data-rights, PIT, or training authority.
    """
    rejected = {
        "status": "REJECTED",
        "reason": "STRICT_MTF_FACTOR_NOT_VERIFIED",
        "delta": 0.0,
        "summary": "",
        "groups": {},
        "timeframes": {},
        "role_group_count": len(MTF_SCREENING_ROLE_GROUPS),
        "cross_timeframe_vote_counting": False,
        "independent_action_authority": False,
        "canonical_veto_suppressed_positive": False,
    }
    factor = _extract_factor_decision(result)
    if not isinstance(factor, dict):
        return {**rejected, "status": "NOT_AVAILABLE", "reason": "FACTOR_MISSING"}
    if factor.get("method_execution_receipt_policy") != "REQUIRED":
        return {**rejected, "reason": "STRICT_METHOD_RECEIPTS_NOT_REQUIRED"}

    from src.services.evidence_traceability_registry import (
        TraceabilityError,
        validate_runtime_trace,
    )
    from src.services.factor_decision_summary import validate_canonical_factor_binding

    try:
        validate_canonical_factor_binding(factor)
        trace = validate_runtime_trace(factor)
    except (TraceabilityError, ValueError, TypeError, KeyError):
        return {**rejected, "reason": "CANONICAL_OR_RUNTIME_BINDING_REJECTED"}

    mtf_observation = next(
        (
            item
            for item in trace.get("observations", ())
            if isinstance(item, dict) and item.get("requirement_id") == "MTF"
        ),
        None,
    )
    if not isinstance(mtf_observation, dict) or (
        mtf_observation.get("state") not in {"READY", "PARTIAL"}
        or mtf_observation.get("reason") != "METHOD_INVOCATION_VERIFIED"
    ):
        return {**rejected, "reason": "MTF_METHOD_INVOCATION_NOT_VERIFIED"}

    mtf = factor.get("multi_timeframe_structure_context")
    if not isinstance(mtf, dict) or not isinstance(mtf.get("timeframes"), dict):
        return {**rejected, "reason": "MTF_CONTEXT_MISSING"}
    if mtf.get("cross_timeframe_vote_counting") is not False:
        return {**rejected, "reason": "MTF_VOTE_COUNTING_POLICY_INVALID"}
    if mtf.get("independent_action_authority") is not False:
        return {**rejected, "reason": "MTF_ACTION_AUTHORITY_INVALID"}
    if expected_code and _screening_code_key(mtf.get("stock_code")) != _screening_code_key(
        expected_code
    ):
        return {**rejected, "reason": "MTF_CANDIDATE_CODE_MISMATCH"}

    rows = {
        timeframe: _timeframe_screening_projection(
            mtf["timeframes"].get(timeframe)
        )
        for timeframe in _MTF_TIMEFRAMES
    }
    groups = {
        "context": _combine_role_group(
            rows["monthly"], rows["weekly"], primary="weekly"
        ),
        "setup": rows["daily"]["state"],
        "bridge_structure": _combine_role_group(
            rows["60m"], rows["30m"], primary="30m"
        ),
        "confirmation_timing": _combine_role_group(
            rows["15m"], rows["5m"], require_both=True
        ),
    }

    delta = 0.0
    delta += {"SUPPORTIVE": 1.25, "ADVERSE": -2.0}.get(groups["context"], 0.0)
    delta += {"SUPPORTIVE": 1.75, "ADVERSE": -2.5}.get(groups["setup"], 0.0)
    delta += {"SUPPORTIVE": 0.75, "ADVERSE": -1.0}.get(
        groups["bridge_structure"], 0.0
    )
    if groups["confirmation_timing"] == "ADVERSE":
        delta -= 0.5
    elif (
        groups["confirmation_timing"] == "SUPPORTIVE"
        and groups["context"] == "SUPPORTIVE"
        and groups["setup"] == "SUPPORTIVE"
        and groups["bridge_structure"] != "ADVERSE"
    ):
        delta += 0.25
    delta = max(
        MTF_SCREENING_DELTA_BOUNDS[0],
        min(MTF_SCREENING_DELTA_BOUNDS[1], round(delta, 4)),
    )

    decision = factor.get("canonical_decision")
    canonical_suppressed = bool(
        isinstance(decision, dict)
        and (
            decision.get("hard_veto") is True
            or str(decision.get("action") or "").upper() == "PASS"
        )
        and delta > 0
    )
    if canonical_suppressed:
        delta = 0.0

    summary = "; ".join(
        f"{name}={state}" for name, state in groups.items()
    )
    return {
        "status": "VERIFIED",
        "reason": "STRICT_MTF_METHOD_AND_CANONICAL_BINDING_VERIFIED",
        "asset_type": str(factor.get("asset_type") or "stock").lower(),
        "delta": delta,
        "summary": summary,
        "groups": groups,
        "timeframes": rows,
        "role_group_count": len(MTF_SCREENING_ROLE_GROUPS),
        "cross_timeframe_vote_counting": False,
        "independent_action_authority": False,
        "canonical_veto_suppressed_positive": canonical_suppressed,
    }


def _timeframe_screening_projection(value: object) -> dict[str, str]:
    row = value if isinstance(value, dict) else {}
    row_status = str(row.get("status") or "MISSING").upper()
    trend = row.get("trend") if isinstance(row.get("trend"), dict) else {}
    direction = str(trend.get("direction") or "UNKNOWN").upper()
    ma = row.get("ma_structure") if isinstance(row.get("ma_structure"), dict) else {}
    ma_status = str(ma.get("status") or "MISSING").upper()
    macd = (
        row.get("macd_startup_stability")
        if isinstance(row.get("macd_startup_stability"), dict)
        else {}
    )
    rsi = (
        row.get("rsi_startup_stability")
        if isinstance(row.get("rsi_startup_stability"), dict)
        else {}
    )
    macd_status = str(
        trend.get("macd_status") or macd.get("reference_macd_state") or "UNKNOWN"
    ).upper()
    rsi_status = str(
        trend.get("rsi_status") or rsi.get("reference_state") or "UNKNOWN"
    ).upper()
    structure = (
        row.get("price_structure")
        if isinstance(row.get("price_structure"), dict)
        else {}
    )
    event = (
        structure.get("structure_event")
        if isinstance(structure.get("structure_event"), dict)
        else {}
    )
    structure_state = str(event.get("state") or structure.get("status") or "MISSING").upper()

    if row_status not in {"READY", "PARTIAL"} or direction == "UNKNOWN":
        state = "MISSING"
    elif (
        direction == "BEARISH"
        or macd_status in _MTF_NEGATIVE_MACD
        or structure_state in _MTF_NEGATIVE_STRUCTURE
    ):
        state = "ADVERSE"
    elif (
        direction == "BULLISH"
        and ma_status in {"READY", "PARTIAL"}
        and str(macd.get("status") or "").upper() == "READY"
        and macd_status in _MTF_POSITIVE_MACD
        and not (
            str(rsi.get("status") or "").upper() == "READY"
            and rsi_status in _MTF_ADVERSE_RSI
        )
        and structure_state not in _MTF_NEGATIVE_STRUCTURE
    ):
        state = "SUPPORTIVE"
    else:
        state = "NEUTRAL"
    return {
        "state": state,
        "period_status": row_status,
        "direction": direction,
        "ma": ma_status,
        "macd": macd_status,
        "rsi": rsi_status,
        "structure": structure_state,
    }


def _combine_role_group(
    first: dict[str, str],
    second: dict[str, str],
    *,
    primary: str = "",
    require_both: bool = False,
) -> str:
    states = (first.get("state"), second.get("state"))
    if "ADVERSE" in states:
        return "ADVERSE"
    if states == ("MISSING", "MISSING"):
        return "MISSING"
    if require_both:
        return "SUPPORTIVE" if states == ("SUPPORTIVE", "SUPPORTIVE") else "PARTIAL"
    primary_state = second.get("state") if primary else None
    if primary_state == "SUPPORTIVE" and first.get("state") != "MISSING":
        return "SUPPORTIVE"
    if states == ("SUPPORTIVE", "SUPPORTIVE"):
        return "SUPPORTIVE"
    if "SUPPORTIVE" in states:
        return "PARTIAL"
    if "MISSING" in states:
        return "PARTIAL"
    return "NEUTRAL"


def _screening_code_key(value: object) -> str:
    text = str(value or "").strip().upper()
    digits = "".join(character for character in text if character.isdigit())
    return digits[-6:] if len(digits) >= 6 else text


def _extract_report_summary(result: dict) -> dict:
    report = result.get("report")
    if not isinstance(report, dict):
        return {}
    summary = report.get("summary")
    return summary if isinstance(summary, dict) else {}


def _extract_trend_result(result: dict) -> dict:
    report = result.get("report")
    if not isinstance(report, dict):
        return {}
    details = report.get("details")
    if not isinstance(details, dict):
        return {}
    context_snapshot = details.get("context_snapshot")
    if not isinstance(context_snapshot, dict):
        return {}
    trend = context_snapshot.get("trend_result")
    return trend if isinstance(trend, dict) else {}


def _extract_risk_flags(trend_result: dict) -> list[str]:
    risks = trend_result.get("risk_factors")
    if not isinstance(risks, list):
        return []
    return [str(item).strip() for item in risks if str(item).strip()]


def _safe_int(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()
