from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from unittest import mock

from src.config import Config
from src.notification import NotificationChannel, NotificationService
from src.services.evidence_flywheel_runtime import compile_gate6_learning_admission
from src.services.v2_5_evidence_coverage import render_v2_5_dynamic_no_send
from src.services.screening.dsa import (
    MTF_SCREENING_DELTA_BOUNDS,
    MTF_SCREENING_ROLE_GROUPS,
    compile_screening_funnel_contract,
)
from src.services.evidence_traceability_registry import (
    ACCEPTED_DATA_OBJECT_DOMAIN_COUNTS,
    ACCEPTED_DATA_OBJECT_GRAPH_SHA256,
    ACCEPTED_PRODUCT_SLOT_IDS,
    ACCEPTED_PRODUCT_SLOT_MAP_SHA256,
    ACCEPTED_REQUIREMENT_ROLES,
    ACCEPTED_VALUATION_ROUTE_IDS,
    ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS,
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
    continuity = baseline["continuity_entrypoints"]
    assert continuity["agent_rules"].startswith("AGENTS.md#0-")
    assert continuity["current_master_route"].startswith("docs/stock-dsa-roadmap.md#")
    assert continuity["exact_git_and_ci_recheck_required"] is True
    assert continuity["feature_branch_plan_is_not_main_production"] is True
    readiness = baseline["runtime_method_window_readiness"]
    assert readiness["schema_version"] == "asset-timeframe-method-window-readiness-v1"
    assert readiness["first_vertical_slice"] == "MTF_MOMENTUM_CONTEXT_MACD_RSI"
    assert readiness["asset_routes"] == ["MARKET", "SECTOR", "ETF", "STOCK"]
    assert readiness["timeframes"] == list(TIMEFRAMES)
    assert readiness["strategy_admitted"] is False
    assert readiness["learning_admitted"] is False
    assert readiness["storage_admitted"] is False
    assert locked["accepted_machine_graph_sha256"] == ACCEPTED_DATA_OBJECT_GRAPH_SHA256
    assert locked["requirements"] == len(ACCEPTED_REQUIREMENT_ROLES) == 26
    assert locked["method_profiles"] == len(policy["methods"]) == 43
    assert locked["methods_existing_reused"] == states["EXISTING_REUSED"] == 28
    assert locked["methods_deferred_with_owner"] == states["DEFERRED_WITH_OWNER_AND_REENTRY"] == 15
    assert locked["strategy_bindings"] == len(STRATEGY_BINDINGS) == 13
    assert locked["technical_matrix_cells"] == len(trace["timeframe_family_matrix"]["cells"]) == 56
    assert locked["data_obligations"] == len(trace["complete_research_universe"]["data_objects"]) == 76
    assert locked["product_slots"] == len(ACCEPTED_PRODUCT_SLOT_IDS) == 27
    assert locked["product_slot_map_sha256"] == ACCEPTED_PRODUCT_SLOT_MAP_SHA256
    assert locked["valuation_routes"] == len(ACCEPTED_VALUATION_ROUTE_IDS) == 9
    assert locked["known_atomic_subfield_obligations"] == len(
        ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS
    ) == 7
    assert locked["data_domain_counts"] == ACCEPTED_DATA_OBJECT_DOMAIN_COUNTS
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
    assert baseline["route_decision"]["next_primary"] == (
        "STAGE1_CURRENT_V25_REAL_PRODUCT_VERTICAL"
    )
    assert [stage["id"] for stage in stages if stage.get("next_primary")] == [
        "B8_FULL_DELIVERY_NO_SEND"
    ]
    assert stages[5]["blocked_by"] == (
        "CURRENT_PRODUCT_FACTS_READY_AND_RECORDING_STORAGE_RIGHTS"
    )
    assert stages[6]["blocked_by"] == "L1_LEGAL_LEDGER_OUTCOME_PIT"
    assert stages[7]["blocked_by"] == "M3_CALIBRATION_SHADOW"
    assert baseline["acceptance"]["direct_main_push"] is False
    # CI91 closes A0 and CI93 closes A1/A2 engineering on the exact feature
    # checkpoint. Neither state implies actual complete data, valuation,
    # delivery, learning, or Promotion authority.
    assert baseline["acceptance"]["required_checks"] == [
        "ai-governance", "backend-gate", "docker-build",
    ]
    acceptance = baseline["acceptance"]
    assert acceptance["state"] == (
        "A0_CI91_A1A2_CI93_REMOTE_ACCEPTED_SCOPED_STAGE1_LOCAL_CANDIDATE_PR31_DRAFT"
    )
    assert acceptance["candidate_head"] == "d75c2969f72fa3f40017bb6ae16d9ca947709057"
    assert acceptance["candidate_parent"] == "ded125634894bf2915db6a91e661ec6fc9280313"
    assert acceptance["candidate_head_scope"] == (
        "LATEST_ACCEPTED_FEATURE_CHECKPOINT_NOT_MAIN_PRODUCTION"
    )
    assert acceptance["latest_required_ci"] == {
        "run_number": 93,
        "run_id": 40480935427,
        "conclusion": "success",
    }
    # CI92 remains the direct failed predecessor; older failures remain in Git/history.
    assert acceptance["prior_failed_ci"] == {
        "run_number": 92,
        "run_id": 40472704093,
        "head": "ded125634894bf2915db6a91e661ec6fc9280313",
        "conclusion": "failure",
    }
    assert acceptance["pull_request_state"] == "DRAFT_OPEN_NOT_MERGED"
    assert baseline["route_decision"]["next_primary"] == (
        "STAGE1_CURRENT_V25_REAL_PRODUCT_VERTICAL"
    )


def test_gate5_gate6_local_evidence_matches_current_fail_closed_runtime() -> None:
    evidence = _baseline()["gate5_gate6_local_evidence"]
    projected = render_v2_5_dynamic_no_send([], report_date="2026-10-23")
    gate5 = projected["receipt"]
    gate6 = compile_gate6_learning_admission(gate5_receipt=gate5)

    expected_gate5 = evidence["gate5"]
    assert expected_gate5["state"] == "PASS_STAGE1_VERTICAL_FAIL_CLOSED_LOCAL_CANDIDATE"
    assert expected_gate5["stage1_vertical_receipt_required"] is True
    assert expected_gate5["stage1_vertical_state"] == "HOLD"
    assert expected_gate5["current_product_catalog_count"] == 72
    assert expected_gate5["future_record_object_count"] == 4
    assert expected_gate5["future_record_blocks_current_product"] is False
    assert expected_gate5["partial_admitted_leaf_projection"] is True
    assert expected_gate5["mail_count"] == gate5["frame_contract"]["mail_count"] == 3
    assert expected_gate5["card_count"] == len(gate5["frame_contract"]["card_ids"]) == 10
    assert expected_gate5["cycle_count"] == len(gate5["frame_contract"]["cycle_ids"]) == 84
    assert expected_gate5["dynamic_block_count"] == gate5["frame_contract"]["dynamic_block_count"] == 855
    assert expected_gate5["fixed_region_sha256"] == gate5["frame_contract"]["fixed_region_sha256"]
    assert expected_gate5["slot_projection_count"] == gate5["slot_projection_count"] == 270
    scope = gate5["whole_scope_projection"]
    assert expected_gate5["requirement_projection_count"] == len(scope["requirement_projections"]) == 26
    assert expected_gate5["data_object_projection_count"] == len(scope["data_object_projections"]) == 76
    assert expected_gate5["valuation_projection_count"] == len(scope["valuation_route_projections"]) == 9
    assert expected_gate5["atomic_projection_count"] == len(scope["atomic_subfield_projections"]) == 7
    assert expected_gate5["missingness_output_sha256"] == gate5["output_sha256"]
    expected_quality = expected_gate5["email_consumer_quality"]
    actual_quality = gate5["email_consumer_quality"]
    assert actual_quality["projection_mode"] == expected_quality["projection_mode"]
    assert actual_quality["raw_scope_row_count"] == expected_quality["raw_scope_row_count"] == 0
    assert actual_quality["blank_inline_tag_count"] == expected_quality["blank_inline_tag_count"] == 0
    assert actual_quality["blank_unclassified_span_count"] == expected_quality["blank_unclassified_span_count"] == 0
    assert len(actual_quality["indicator_family_labels"]) == expected_quality["indicator_family_count"] == 8
    assert actual_quality["repeated_policy_filler_count"] == expected_quality["repeated_policy_filler_count"] == 0
    assert actual_quality["generic_placeholder_count"] == expected_quality["generic_placeholder_count"] == 0
    assert actual_quality["ordinary_language_roles_preserved"] is expected_quality["ordinary_language_roles_preserved"] is True
    assert gate5["all_whole_scope_obligations_projected"] is True
    assert gate5["all_accepted_slot_roles_projected"] is True
    assert gate5["all_input_facts_product_ready"] is False

    expected_gate6 = evidence["gate6"]
    assert gate6["state"] == expected_gate6["state"] == "HOLD"
    assert len(gate6["stage_order"]) == expected_gate6["stage_count"] == 10
    assert gate6["reason_codes"] == expected_gate6["reason_codes"]
    assert gate6["current_probability_authority"] is False
    assert gate6["training_effect_authority"] is False
    assert gate6["promotion_effect_authority"] is False
    assert gate6["network_calls"] == 0
    assert gate6["database_writes"] == 0
    assert gate6["model_training_runs"] == 0


def test_current_stage1_real_run_is_scoped_partial_and_fail_closed() -> None:
    evidence = _baseline()["current_stage1_real_run_evidence"]
    assert evidence["state"] == "PASS_SCOPED_REAL_READ_NO_SEND_STAGE1_HOLD"
    assert evidence["rights_schema_version"] == "source-rights-evidence-v2"
    assert evidence["product_admitted_dataset_ids"] == [
        "P_DAILY", "P_5M", "E_PRICE_1D", "E_PRICE_5M",
    ]
    assert evidence["stock_product_admitted_count"] == 2
    assert evidence["etf_product_admitted_count"] == 2
    assert evidence["storage_training_redistribution"] == "FORBIDDEN"
    index = evidence["index_000300"]
    assert index["state"] == "SOURCE_IDENTITY_ONLY_PARTIAL"
    assert index["completed_through"] == "2026-10-27"
    assert index["future_bar_backfilled"] is False
    assert index["numeric_value_consumed"] is False
    assert index["strategy_upgrade_authority"] is False
    v25 = evidence["v2_5_no_send"]
    assert v25["bytes"] == 107013
    assert v25["baseline_sha256"] == (
        "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823"
    )
    assert v25["stage1_state"] == "HOLD"
    assert v25["blocker_codes"] == [
        "FULL_DOMAIN_PRODUCT_DATA_NOT_READY",
        "ATOMIC_SUBFIELD_V25_CONSUMER_BINDING_INCOMPLETE",
    ]
    assert v25["html_persisted"] is False
    assert v25["delivery_authority"] is False
    assert all(value == 0 or value is False for value in evidence["effects"].values())


def test_current_nonprice_probe_and_daily_completion_bridge_are_scoped() -> None:
    evidence = _baseline()["current_stage1_nonprice_and_daily_bridge_evidence"]
    assert evidence["state"] == (
        "PASS_SCOPED_LOCAL_BRIDGE_REAL_NONPRICE_SOURCE_PARTIAL_PROVIDER_REPLAY_STOP"
    )
    probe = evidence["nonprice_source_probe"]
    assert probe["asset_id"] == "600519"
    assert probe["dataset_count"] == len(probe["dataset_ids"]) == 14
    assert probe["all_states"] == "PARTIAL"
    assert probe["quality_growth_input_present"] is False
    assert probe["business_valuation_input_present"] is False
    assert probe["storage_training_strategy_authority"] is False
    operation = evidence["rights_operation"]
    assert operation["operation_id"] == "stock-dsa-nosend-20261028-baostock-v2-r002"
    assert operation["grant_count"] == 19
    bridge = evidence["daily_completion_bridge"]
    assert bridge["scope"] == "B1_NO_SEND_EXACT_ONE_XSHG_SESSION_LAG_ONLY"
    assert bridge["same_provider_basis_currency_required"] is True
    assert bridge["complete_target_5m_bar_count"] == 48
    assert bridge["focused_tests_passed"] == 41
    assert bridge["natural_regression_tests_passed"] == 446
    assert set(bridge["negative_cases"]) == {
        "INCOMPLETE_5M_SESSION", "WRONG_PROVIDER", "WRONG_ADJUSTMENT_BASIS",
        "WRONG_INSTRUMENT", "TWO_SESSION_LAG",
    }
    assert bridge["storage_admitted"] is False
    assert bridge["training_admitted"] is False
    assert bridge["strategy_admitted"] is False
    assert bridge["independent_action_authority"] is False
    replay = evidence["real_replay"]
    assert replay["state"] == "STOP_PROVIDER_NETWORK"
    assert replay["further_retries_stopped"] is True
    assert all(value == 0 or value is False for value in evidence["effects"].values())


def test_alternative_producer_matrix_preserves_real_execution_and_rights_boundaries() -> None:
    evidence = _baseline()["current_stage1_alternative_producer_evidence"]
    assert evidence["state"] == (
        "LOCAL_CODE_PASS_REAL_ENVIRONMENT_BLOCKED_NO_NEW_PRODUCT"
    )
    counts = evidence["matrix_counts"]
    assert counts["data_objects"] == 76
    assert counts["atomic_subfields"] == 7
    assert counts["current_product_admitted_data_objects"] == 4
    assert counts["current_product_admitted_atomic_subfields"] == 0
    assert counts["real_nonprice_partial_producers"] == 14
    contract = evidence["atomic_fact_contract"]
    assert contract["admission_hash_alone_is_sufficient"] is False
    assert contract["typed_facts_required"] is True
    assert contract["source_fact_hash_required"] is True
    assert contract["final_v2_5_block_fact_hash_required"] is True
    assert contract["slot_or_binding_state_can_substitute_numeric_value"] is False
    bls = evidence["bls_cpi3"]
    assert len(bls["datasets"]) == 3
    assert bls["local_contract_tests_passed"] == 4
    assert bls["pipeline_integration_tests_passed"] == 2
    assert bls["official_terms_bytes_locally_sealed"] is False
    assert bls["real_source_receipts_observed"] is False
    assert bls["product_admitted_count"] == 0
    assert all(
        value == 403
        for key, value in bls["actual_no_proxy_probe"].items()
        if key.endswith("_status")
    )
    assert bls["actual_no_proxy_probe"]["further_local_retries_stopped"] is True
    assert all(value == 0 for value in evidence["effects"].values())


def test_mature_donors_are_bound_to_consumers_and_not_misreported_as_adopted() -> None:
    evidence = _baseline()["mature_donor_activation_evidence"]
    assert evidence["state"] == "ROUTE_ROOT_FIXED_BOUNDED_CONSUMPTION_PREWRITE"
    assert evidence["failure_class"] == "DONOR_REGISTERED_BUT_NOT_CONSUMER_ACTIVATED"
    assert evidence["user_discovery"] == "TRUE_POSITIVE"
    assert evidence["maturity_states"] == [
        "DISCOVERED", "SOURCE_METHOD_REVIEWED", "CONTRACT_BOUND",
        "CODE_TEST_CONSUMED", "NATIVE_PRODUCT_VALIDATED",
    ]
    cne = evidence["cnequity"]
    assert cne["official_stable_release_observed"] == "0.16.0"
    assert cne["current_maturity"] == "CONTRACT_BOUND_PREWRITE"
    assert cne["bounded_dataset_consumers"] == 14
    assert cne["installation_performed"] is False
    assert cne["data_pull_performed"] is False
    assert cne["storage_rights_admitted"] is False
    assert cne["production_canonical_report_model_authority"] is False
    claims = evidence["aistockmind"]
    assert claims["current_maturity"] == "CLAIM_VERIFICATION_BOUNDED_TRIAL_PASS_SCOPED"
    assert claims["verified_primary_source_claims"] == 3
    assert claims["unverified_analytical_opinions"] == 1
    assert claims["market_fact_indicator_valuation_probability_canonical_authority"] is False
    graph = evidence["machine_graph"]
    assert graph["accepted_775_edge_graph_update_required"] is False
    assert graph["status_projection_update_required"] is True
    assert evidence["stage1_complex_gate_count"] == 3
    assert all(value == 0 for value in evidence["effects"].values())


def test_a1_a2_local_foundation_evidence_is_scoped_and_non_authoritative() -> None:
    evidence = _baseline()["a1_a2_local_evidence"]
    assert evidence["state"] == (
        "REMOTE_ACCEPTED_CI93_SCOPED_NO_REPLAY"
    )
    a1 = evidence["a1"]
    assert a1 == {
        "method_profiles": 43,
        "existing_methods": 28,
        "deferred_methods": 15,
        "technical_existing_methods": 25,
        "target_method_timeframe_pairs": 172,
        "raw_declared_execution_pairs": 34,
        "effective_execution_pairs": 52,
        "effective_override_methods": 5,
        "matrix_cells": 56,
        "consumer_slots": 27,
        "declaration_is_runtime_ready": False,
        "historical_receipt_backfills_current": False,
        "independent_action_authority": False,
    }
    a2 = evidence["a2"]
    assert a2["accepted_business_routes"] == 9
    assert a2["data_catalog_objects"] == 76
    assert a2["index_dataset"] == "M_ERP_VAL"
    assert a2["sector_dataset"] == "S_VALUATION"
    assert a2["etf_underlying_dataset"] == "E_UNDERLYING_VAL"
    assert a2["etf_premium_discount_dataset"] == "E_PREMIUM"
    assert a2["index_fair_value_or_action_authority"] is False
    assert a2["stock_index_etf_layers_separate"] is True


def test_stage_baseline_preserves_formal_email_contract_and_no_auto_trade() -> None:
    baseline = _baseline()
    invariants = baseline["product_invariants"]
    assert invariants["automatic_trading"] is False
    assert invariants["formal_report_email_required"] is True
    assert invariants["formal_report_telegram_optional"] is True
    assert invariants["other_chat_or_webhook_channels_are_formal_report_substitutes"] is False
    assert invariants["source_identity_implies_storage_or_training_rights"] is False
    assert invariants["operation_specific_upstream_rights_required"] is True
    assert invariants["unknown_upstream_rights_admit_product"] is False
    assert invariants["no_send_product_rights_imply_redistribution"] is False

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
    regression = _baseline()["regression_policy"]
    assert regression["whole_product_expected_sets_reject_candidate_subset"] is True
    assert regression["full_domain_accounting_is_distinct_from_product_readiness"] is True
    assert regression["known_atomic_subfields_remain_visible_until_bound"] is True
    assert regression["future_record_artifacts_do_not_block_current_product"] is True
    assert regression["partial_admitted_leaf_remains_visible"] is True
    assert regression["unadmitted_leaf_values_remain_hidden"] is True
    assert regression["record_outcome_and_pit_clocks_are_later_than_current_product"] is True
    assert regression["final_effective_method_profiles_override_raw_constructor_scope"] is True
    assert regression["foundation_receipt_binds_runtime_matrix_product_admission_canonical_and_v25"] is True
    assert regression["historical_method_execution_never_backfills_current_product"] is True
    assert regression["stock_index_sector_and_etf_valuation_layers_are_separate"] is True
    assert regression["etf_premium_discount_does_not_use_underlying_valuation_dataset"] is True
    assert regression["index_sector_valuation_has_no_fair_value_action_or_veto_authority"] is True
    assert regression[
        "stage1_vertical_requires_actual_invocation_product_admission_and_v25_leaf_consumption"
    ] is True
    assert regression["empty_materials_never_fact_ready"] is True
    assert regression["nonprice_objects_use_same_b1_closed_world_compiler"] is True
    assert regression[
        "daily_provider_lag_bridge_requires_same_provider_complete_48_bar_session"
    ] is True
    assert regression[
        "daily_provider_lag_bridge_only_fills_exact_one_completed_session"
    ] is True
    assert regression[
        "daily_provider_lag_bridge_never_persists_or_grants_strategy_authority"
    ] is True
    assert regression["software_package_license_cannot_substitute_upstream_data_terms"] is True
    assert regression["official_data_use_statement_cannot_grant_storage_training_or_redistribution"] is True
    assert regression["research_date_never_backfills_uncompleted_index_bar"] is True


def test_indicator_backlog_is_subordinate_to_the_current_product_route() -> None:
    decision = _baseline()["route_decision"]
    assert decision["next_primary"] == "STAGE1_CURRENT_V25_REAL_PRODUCT_VERTICAL"
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
