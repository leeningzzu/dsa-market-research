from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from unittest import mock

from src.config import Config
from src.notification import NotificationChannel, NotificationService
from src.services.screening.dsa import (
    MTF_SCREENING_DELTA_BOUNDS,
    MTF_SCREENING_ROLE_GROUPS,
    compile_screening_funnel_contract,
)
from src.services.evidence_traceability_registry import (
    ACCEPTED_DATA_OBJECT_GRAPH_SHA256,
    ACCEPTED_REQUIREMENT_ROLES,
    STRATEGY_BINDINGS,
    TECHNICAL_EVIDENCE_FAMILIES,
    TIMEFRAMES,
    build_runtime_trace,
    compile_method_window_policy_view,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "docs" / "stock-dsa-stage-baseline-v1.json"
V25_PATH = ROOT / "templates" / "v2_5" / "STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html"


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_stage_baseline_is_derived_from_the_existing_machine_graph() -> None:
    baseline = _baseline()
    locked = baseline["immutable_contracts"]
    policy = compile_method_window_policy_view()
    trace = build_runtime_trace({})
    states = Counter(row["leaf_implementation_state"] for row in policy["methods"])

    assert baseline["authority"] == "CI_DERIVED_GUARDRAIL_ONLY_NOT_RUNTIME_PROOF"
    assert baseline["production_shell"] == "DSA/daily_stock_analysis"
    assert locked["accepted_machine_graph_sha256"] == ACCEPTED_DATA_OBJECT_GRAPH_SHA256
    assert locked["requirements"] == len(ACCEPTED_REQUIREMENT_ROLES) == 26
    assert locked["method_profiles"] == len(policy["methods"]) == 43
    assert locked["methods_existing_reused"] == states["EXISTING_REUSED"] == 26
    assert locked["methods_deferred_with_owner"] == states["DEFERRED_WITH_OWNER_AND_REENTRY"] == 17
    assert locked["strategy_bindings"] == len(STRATEGY_BINDINGS) == 13
    assert locked["technical_matrix_cells"] == len(trace["timeframe_family_matrix"]["cells"]) == 56
    assert locked["data_obligations"] == len(trace["complete_research_universe"]["data_objects"]) == 76
    assert locked["timeframes"] == list(TIMEFRAMES)
    assert locked["evidence_families"] == list(TECHNICAL_EVIDENCE_FAMILIES)

    # Read the exact Git blob: Windows checkout autocrlf may change worktree
    # bytes, but cannot redefine the independently accepted immutable original.
    original = subprocess.check_output([
        "git", "show",
        "HEAD:templates/v2_5/STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html",
    ])
    assert len(original) == locked["v2_5_baseline_bytes"] == 125919
    assert sha256(original).hexdigest() == locked["v2_5_baseline_sha256"]


def test_stage_ladder_keeps_parent_product_and_blocking_order_explicit() -> None:
    baseline = _baseline()
    stages = baseline["stage_ladder"]
    ids = [stage["id"] for stage in stages]
    assert ids == [
        "G0_GITHUB_BASELINE",
        "B1_SOURCE_RIGHTS",
        "B5_7TF_METHOD_PRODUCT",
        "B8_FULL_DELIVERY_NO_SEND",
        "M2_MULTI_ASSET_KNOWN_AT",
        "L1_LEGAL_LEDGER_OUTCOME_PIT",
        "M3_CALIBRATION_SHADOW",
        "P1_PROMOTION_RELEASE",
    ]
    assert baseline["route_decision"]["next_primary"] == "M2_MULTI_ASSET_KNOWN_AT"
    assert [stage["id"] for stage in stages if stage.get("next_primary")] == ["M2_MULTI_ASSET_KNOWN_AT"]
    assert stages[5]["blocked_by"] == "B1_SOURCE_RIGHTS"
    assert stages[6]["blocked_by"] == "L1_LEGAL_LEDGER_OUTCOME_PIT"
    assert stages[7]["blocked_by"] == "M3_CALIBRATION_SHADOW"
    assert baseline["acceptance"]["direct_main_push"] is False
    assert baseline["acceptance"]["required_checks"] == [
        "ai-governance", "backend-gate", "docker-build",
    ]


def test_stage_baseline_preserves_formal_email_contract_and_no_auto_trade() -> None:
    baseline = _baseline()
    invariants = baseline["product_invariants"]
    assert invariants["automatic_trading"] is False
    assert invariants["formal_report_email_required"] is True
    assert invariants["formal_report_telegram_optional"] is True
    assert invariants["other_chat_or_webhook_channels_are_formal_report_substitutes"] is False
    assert invariants["source_identity_implies_storage_or_training_rights"] is False

    cfg = Config(
        stock_list=[],
        email_sender="baseline@example.com",
        email_password="TEST_ONLY",
        email_receivers=["baseline@example.com"],
        telegram_bot_token="TEST_TOKEN",
        telegram_chat_id="123456",
        wechat_webhook_url="https://wechat.invalid/hook",
        notification_report_channels=["wechat", "telegram"],
    )
    with mock.patch("src.notification.get_config", return_value=cfg):
        service = NotificationService()
    assert service.get_channels_for_route("report") == [
        NotificationChannel.EMAIL,
        NotificationChannel.TELEGRAM,
    ]
    assert NotificationChannel.WECHAT not in service.get_channels_for_route("report")


def test_stage_baseline_locks_bounded_hierarchical_mtf_screening() -> None:
    contract = _baseline()["screening_contract"]
    assert contract["stage"] == "BOUNDED_L3_AFTER_CHEAP_L1_L2"
    assert contract["asset_routes"] == ["stock", "etf"]
    assert contract["strict_mtf_receipt_required"] is True
    assert contract["canonical_binding_required"] is True
    assert contract["role_groups"] == list(MTF_SCREENING_ROLE_GROUPS)
    assert [contract["delta_min"], contract["delta_max"]] == list(
        MTF_SCREENING_DELTA_BOUNDS
    )
    assert contract["low_timeframe_independent_upgrade"] is False
    assert contract["cross_timeframe_vote_counting"] is False
    assert contract["independent_action_authority"] is False
    assert _baseline()["regression_policy"][
        "mtf_screening_requires_strict_receipt_and_canonical_binding"
    ] is True


def test_indicator_backlog_is_subordinate_to_the_multi_asset_parent_route() -> None:
    decision = _baseline()["route_decision"]
    assert decision["next_primary"] == "M2_MULTI_ASSET_KNOWN_AT"
    assert decision["bounded_indicator_sub_lanes"] == [
        "MONTHLY_RSI_HISTORY_IF_LEGAL",
        "15M_CONFIRMATION",
        "5M_EXECUTION_TIMING",
        "MINUTE_VOLUME_AMOUNT_UNITS",
    ]
    assert "CHAN_WAVE" in decision["deferred_until_product_trigger"]
    assert _baseline()["regression_policy"]["static_counts_never_prove_runtime_ready"] is True


def test_screening_funnel_contract_keeps_gates_caps_and_dedup_machine_readable() -> None:
    compiled = compile_screening_funnel_contract()
    locked = _baseline()["screening_funnel_contract"]

    assert compiled["schema_version"] == "screening-funnel-contract-v1"
    assert compiled["stages"] == [
        "L0_SOURCE_ADMISSION",
        "L1_MARKET_SECTOR_PERMISSION",
        "L2_ASSET_COARSE_FILTER",
        "L3_ASSET_FINE_RANK",
        "L4_BOUNDED_7TF_DEEP_OVERLAY",
        "L5_CANONICAL_VETO_AND_FULL_REPORT",
    ]
    assert locked["stages"] == compiled["stages"]
    assert locked["weight_mode"] == compiled["weight_mode"] == (
        "GATES_THEN_BOUNDED_HEURISTIC_CAPS_NOT_PROBABILITY"
    )
    assert compiled["hard_veto_is_unweighted"] is True
    assert compiled["missing_unknown_is_zero_vote"] is True
    assert compiled["learned_weights"] == "DEFERRED_UNTIL_LEGAL_PIT_OOT"
    assert compiled["active_numeric_weights"]["mtf_role_deltas"] == {
        "context": {"SUPPORTIVE": 1.25, "ADVERSE": -2.0},
        "setup": {"SUPPORTIVE": 1.75, "ADVERSE": -2.5},
        "bridge_structure": {"SUPPORTIVE": 0.75, "ADVERSE": -1.0},
        "confirmation_timing": {
            "SUPPORTIVE_CONDITIONAL": 0.25,
            "ADVERSE": -0.5,
        },
    }
    assert locked["active_numeric_weights"] == compiled["active_numeric_weights"]

    groups = compiled["correlation_groups"]
    assert {"MACD", "RSI", "ROC", "CONFIRMED_DIVERGENCE"}.issubset(
        groups["same_swing_momentum"]
    )
    assert {"PRICE_PIVOT_SWING", "BREAKOUT_RETEST_FAILED", "CHAN", "WAVE"}.issubset(
        groups["confirmed_price_swing"]
    )
    assert {"VCP", "CUP_HANDLE", "DOUBLE_BOTTOM", "FLAT_BASE"}.issubset(
        groups["price_contraction"]
    )
    flattened = [method for values in groups.values() for method in values]
    assert len(flattened) == len(set(flattened)) == 43
    assert locked["correlation_group_count"] == len(groups)

    composition = compiled["email_evidence_composition"]
    assert composition["TREND_RS"]["includes"] == [
        "relative_strength", "ma_level_alignment", "ma_slope_compression",
    ]
    assert "chan_fx_bi_zs" in composition["STRUCTURE"]["includes"]
    assert composition["MOMENTUM"]["max_independent_contribution_per_group"] == 1
    assert composition["MTF"]["recomputes_underlying_indicators"] is False
    assert compiled["email_section_order"] == [
        "canonical_conclusion",
        "market_sector_permission",
        "asset_specific_quality_valuation_or_etf",
        "eight_evidence_families",
        "seven_timeframe_role_summary",
        "correlation_dedup",
        "data_risk_liquidity_execution_veto",
        "trigger_invalidation",
        "source_time_rights_and_missingness",
    ]
    assert locked["email_section_order"] == compiled["email_section_order"]
    assert compiled["nontechnical_lanes"] == [
        "QUALITY_GROWTH", "VALUATION", "ETF_SPECIFIC",
        "DATA_RISK_LIQUIDITY_EXECUTION",
    ]
    assert locked["asset_routes"] == compiled["asset_routes"]
    assert compiled["asset_routes"]["stock"]["fine_rank_priority"][:3] == [
        "sector_peer_relative_strength", "quality_growth", "valuation",
    ]
    assert compiled["asset_routes"]["etf"]["fine_rank_priority"][:3] == [
        "peer_relative_strength", "liquidity_spread", "nav_premium_tracking",
    ]
