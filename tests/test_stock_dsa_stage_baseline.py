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
REENTRY_PATH = ROOT / "docs" / "stock-dsa-github-reentry.md"
REENTRY_VERIFIER_PATH = ROOT / "scripts" / "verify_stock_dsa_reentry.py"


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_current_global_observation_candidate_separates_product_from_pit() -> None:
    baseline = _baseline()
    current = baseline["current_global_observation_candidate"]
    assert current["state"] == (
        "PRODUCT13_GLOBAL5_SSE8_SAME_OPERATION_PASS_NATURAL_CI_SUCCESS"
    )
    assert current["base_head"] == "ac3cce4c2f6ada5d7641a270dd29affa25760b35"
    assert current["project_source_pair"] == "v3.553_PHYSICAL_EXACT8_PASS_KEEP_NO_CHURN"
    temporal = current["temporal_contract"]
    assert temporal["temporal_scope"] == "CURRENT_OBSERVATION"
    assert temporal["historical_pit_eligible"] is False
    assert temporal["storage_admitted"] is False
    assert temporal["learning_admitted"] is False
    assert temporal["strategy_upgrade_authority"] is False
    actual = current["actual_operation"]
    assert actual["operation_id"] == "global5-sse8-current-nosend-20261105-r001"
    assert actual["product_admitted_dataset_ids"] == [
        "E_BASKET", "E_NAV_IOPV", "E_SHARES", "G_INFL_BREAKEVEN",
        "G_USD_BROAD", "G_UST_NOMINAL", "G_UST_REAL", "G_US_POLICY",
        "P_CODE_IDENTITY", "P_DIVIDEND", "P_INDUSTRY", "P_SHARES_EVENTS",
        "S_CLASSIFICATION",
    ]
    assert actual["product_admitted_count"] == 13
    assert actual["network_requests"] == 15
    assert actual["stock_route_product_count"] == 10
    assert actual["etf_route_product_count"] == 8
    assert actual["unique_product_count_after_cross_asset_deduplication"] == 13
    assert actual["v25_fact_summary_count"] == 13
    assert actual["receipt_sha256"] == (
        "ce4fa1746e16e7c62d435b11c02f7868946d3d4d6f922bd0ce004458c5900231"
    )
    assert actual["pipeline_owner_verified_in_same_operation"] is True
    assert actual["cross_receipt_splicing"] is False
    assert actual["raw_source_bytes_persisted"] is False
    assert actual["full_domain_product_ready"] is False
    counts = current["capability_counts"]
    assert counts["typed_or_real_source_capability"] == 33
    assert counts["complete_typed_source_chain_gaps"] == 39
    assert counts["current_product_admitted"] == 13
    assert counts["current_product_denominator"] == 72
    assert counts["global_golden_atomic_ready"] == 0
    assert counts["global_golden_atomic_denominator"] == 7
    sse = current["sse_current_facts"]
    assert sse["stock_code"] == "600519" and sse["stock_name"] == "贵州茅台"
    assert sse["csrc_great_code"] == "15"
    assert sse["current_total_domestic_shares"] == 1250081600
    assert sse["share_event_count"] == 15
    assert sse["latest_share_event_total_shares"] == 1256197800
    assert sse["share_events_reconciled"] is False
    assert sse["dividend_record_count"] == 9
    assert sse["latest_ex_dividend_date"] == "2026-06-26"
    assert sse["latest_cash_dividend_before_tax_cny_per_share"] == "28.02423"
    assert sse["etf_code"] == "510300"
    assert sse["etf_nav_cny_per_share"] == "4.1785"
    assert sse["etf_basket_component_count"] == 300
    validation = current["validation"]
    assert validation["actual_product13_operation"] == "PASS"
    assert validation["same_operation_no_cross_receipt_splice"] is True
    ci = validation["accepted_product13_code_ci"]
    assert ci["remote_feature_commit"] == "ac3cce4c2f6ada5d7641a270dd29affa25760b35"
    assert ci["run_id"] == 41991363403
    assert ci["terminal_conclusion"] == "success"
    assert (ci["jobs_success"], ci["jobs_skipped"], ci["jobs_failed"]) == (6, 1, 0)
    assert ci["receipt_sha256"] == actual["receipt_sha256"]
    route = validation["parent_route_review"]
    assert route["user_reminder_class"] == "TRUE_POSITIVE_PROCESS_CONSUMER"
    assert route["auto_retrospective_before_user_reminder"] is False
    assert route["git_write_authorization_required"] is True
    assert route["route_review_did_not_require_user_reminder"] is True
    assert len(route["remaining_complex_gates"]) == 6
    assert current["full_v25_fact_ready"] is False
    assert current["git_commit"] == "ac3cce4c2f6ada5d7641a270dd29affa25760b35"
    assert current["git_push"] is True and current["merge"] is False
    roadmap = (ROOT / "docs" / "stock-dsa-roadmap.md").read_text(encoding="utf-8")
    assert "STOCK_DSA_MASTER_ROUTE_20261106_M_TURNOVER_LOCAL_REENTRY_V3" in roadmap
    assert "最近多对象 CURRENT operation：**13/72（18.1%）**" in roadmap
    assert "REENTRY_CONTRACT = `AGENTS.md` 为规则真源" in roadmap
    assert (        "SOURCE_UPDATE_DECISION = "
        "KEEP_PHYSICAL_01_05_V3_556_NO_CHURN"
        in roadmap
    )



def test_current_market_turnover_atomic_operation_is_separate_and_bounded() -> None:
    baseline = _baseline()
    current = baseline["current_market_turnover_atomic_operation"]
    assert current["state"] == "ACTUAL_CURRENT_OBSERVATION_PRODUCT_ADMITTED_PARTIAL_SCOPE"
    assert current["operation_id"] == "cn-exchange-turnover-nosend-20261106T040925Z"
    assert current["dataset_id"] == "M_TURNOVER"
    assert current["market_atomic_product_count"] == 1
    assert current["cross_operation_sum_forbidden"] is True
    assert current["terms"]["sse"]["sha256"] == (
        "7442f73b132ae46702513abd5e67183b0baaca0c298db66d0337b0d6f2d606f1"
    )
    assert current["terms"]["szse"]["sha256"] == (
        "5398c463263421a98cbf90918dcaa16d6d70ef80f2052a9d0c19c1606ab27936"
    )
    facts = current["facts"]
    assert facts["trade_date"] == "2026-11-05"
    assert facts["sse_stock_total_turnover_100m_cny"] == "9812.82"
    assert facts["szse_total_turnover_100m_cny"] == "11395.92"
    assert facts["combined_two_exchange_stock_turnover_100m_cny"] == "21208.74"
    assert facts["bse_included"] is False
    assert facts["exact_all_a_turnover_admitted"] is False
    assert facts["breadth_denominator_admitted"] is False
    assert current["receipt_sha256"] == (
        "79e1e7369cb2687164038022722fd12c59279b0d1cdef06200a64e5a03959ffe"
    )
    assert current["product_admitted"] is True
    assert current["storage_admitted"] is False
    assert current["learning_admitted"] is False
    assert current["historical_pit_eligible"] is False
    assert current["redistribution_admitted"] is False
    assert current["strategy_upgrade_authority"] is False
    assert current["raw_source_persisted"] is False


def test_current_market_breadth_candidate_is_ci107_accepted_but_not_product() -> None:
    current = _baseline()["current_market_breadth_candidate"]
    assert current["state"] == "CI107_BREADTH_ISOLATION_NATURAL_CI_SUCCESS"
    assert current["checkpoint_commit"] == "55a27f56babdb2d8945097fc4a5eacf43181ae04"
    contract = current["provider_contract"]
    assert contract["intraday_rt_k_breadth_forbidden"] is True
    assert contract["typed_claims_must_validate_before_preemption"] is True
    assert contract["missing_trading_member_without_suspend_reason"] == "STOP"
    isolation = current["consumer_isolation"]
    assert isolation["compiled_b1_product_admission_required"] is True
    assert "revision" in isolation["admission_source_semantics_bound"]
    assert "vintage" in isolation["admission_source_semantics_bound"]
    assert isolation["source_only_breadth_enters_llm_market_light_or_action"] is False
    assert isolation["m_basic_breadth_opens_m_limit_or_turnover"] is False
    assert isolation["caller_market_light_mismatch"] == "STOP"
    actual = current["actual_tushare_operation"]
    assert actual["token_present_in_local_executor"] is False
    assert actual["network_requests"] == 0
    assert actual["rights_v2_bound"] is False
    trial = current["real_baostock_research_trial"]
    assert trial["duration_seconds"] == 796.61217
    assert trial["amount_fact_admitted"] is False
    assert trial["closure_sha256"] == "fcf71b2f3f20367d767bc69e8441a54d2798c8893b9e4e27644f6cf6726c832d"
    assert trial["blockers"] == [
        "BSE_NOT_COVERED",
        "UPSTREAM_DATA_USE_RIGHTS_NOT_VERIFIED",
        "NOT_A_PRODUCTION_EFFICIENT_ROUTE_5510_PER_CODE_CALLS",
    ]
    validation = current["validation"]
    assert (validation["base_requirement_red"], validation["consumer_isolation_red"]) == (8, 3)
    assert (validation["root_passed"], validation["related_passed"], validation["deep_consumer_passed"]) == (32, 385, 496)
    assert validation["natural_ci"] == "CI107_RUN41940325069_SUCCESS"
    effect = current["capability_effect"]
    assert effect["typed_or_real_source_capability_before"] == effect["typed_or_real_source_capability_after"] == 31
    assert effect["complete_typed_source_chain_gaps_before"] == effect["complete_typed_source_chain_gaps_after"] == 41
    assert effect["current_product_before"] == effect["current_product_after"] == 4
    assert effect["full_v25_fact_ready"] is False
    assert current["git_push"] is True and current["merge"] is False




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
    assert continuity["cross_account_reentry"] == "docs/stock-dsa-github-reentry.md"
    assert continuity["same_machine_local_repo_preferred"] is True
    assert continuity["current_master_route"].startswith("docs/stock-dsa-roadmap.md#")
    assert continuity["deterministic_reentry_verifier"] == "scripts/verify_stock_dsa_reentry.py"
    assert continuity["required_read_order"][:2] == [
        "AGENTS.md", "docs/stock-dsa-github-reentry.md",
    ]
    assert continuity["exact_git_and_ci_recheck_required"] is True
    assert continuity["feature_branch_plan_is_not_main_production"] is True
    assert continuity["old_chat_or_project_source_is_not_git_currentness"] is True
    reentry = baseline["github_reentry_contract"]
    assert reentry["schema_version"] == "stock-dsa-cross-account-reentry-v2"
    assert reentry["repository_url"] == "https://github.com/leeningzzu/dsa-market-research.git"
    assert reentry["development_branch"] == "feat/stock-dsa-m2-source-gates-v1"
    assert reentry["candidate_parent"] == "f084e8562ea8fc7b9564c541c537e618a2e40c89"
    assert reentry["same_machine_local_pointer_supported"] is True
    assert reentry["github_connector_required"] is False
    assert reentry["project_sources_required_for_code_reentry"] is False
    assert reentry["old_chat_required_for_code_reentry"] is False
    assert reentry["public_repo_contains_private_secrets_or_ledger"] is False
    assert reentry["current_algorithm_projection"] == {
        "method_profiles": 43,
        "methods_existing_reused": 35,
        "methods_deferred_with_owner": 8,
        "technical_numeric_leaves_admitted": 35,
        "technical_numeric_leaves_total": 35,
        "technical_method_timeframe_targets": 189,
    }
    assert reentry["current_product_projection"] == {
        "latest_multi_object_operation_product_count": 13,
        "denominator": 72,
        "market_atomic_product_count": 2,
        "market_atomic_product_ids": ["M_INDEX_CURRENT", "M_TURNOVER"],
        "cross_operation_sum_forbidden": True,
        "full_domain_product_ready": False,
    }
    assert REENTRY_PATH.is_file() and REENTRY_VERIFIER_PATH.is_file()
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
    assert locked["methods_existing_reused"] == states["EXISTING_REUSED"] == 35
    assert locked["methods_deferred_with_owner"] == states["DEFERRED_WITH_OWNER_AND_REENTRY"] == 8
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
        "MARKET_ALL_A_BSE_DENOMINATOR_BREADTH_AND_LIMITS_GATE"
    )
    assert [stage["id"] for stage in stages if stage.get("next_primary")] == [
        "B1_SOURCE_RIGHTS"
    ]
    assert stages[1]["local_state"] == (
        "TYPED_SOURCE_CAPABILITY35_GAP37_PRODUCT13_PLUS_TWO_SEPARATE_MARKET_ATOMIC_CAPABILITIES_FULL_DOMAIN_RIGHTS_STOP"
    )
    assert stages[3]["blocked_by"] == "B1_UPSTREAM_DATA_RIGHTS_AND_FULL_DOMAIN_PRODUCT_DATA"
    assert stages[5]["blocked_by"] == (
        "CURRENT_PRODUCT_FACTS_READY_AND_RECORDING_STORAGE_RIGHTS"
    )
    assert stages[6]["blocked_by"] == "L1_LEGAL_LEDGER_OUTCOME_PIT"
    assert stages[7]["blocked_by"] == "M3_CALIBRATION_SHADOW"
    assert baseline["acceptance"]["direct_main_push"] is False
    # CI91 closes A0, CI93 closes A1/A2 engineering, and CI95 accepts the
    # frozen-backend compatibility repair on the exact feature checkpoint.
    # None of these states implies complete current Product data, delivery,
    # learning, or Promotion authority.
    assert baseline["acceptance"]["required_checks"] == [
        "ai-governance", "Change Detection", "docker-build", "backend-gate",
        "desktop-futu-package-macos", "desktop-futu-package-windows",
    ]
    acceptance = baseline["acceptance"]
    assert acceptance["state"] == (
        "CI100_REMOTE_ACCEPTED_PREMIUM_EXECUTION_CONTINUITY_"
        "RESOLVE_AT_RUNTIME"
    )
    assert acceptance["candidate_head"] == "16963cee711392605b2515eef00e390802f5eeeb"
    assert acceptance["candidate_parent"] == "6eb822f5a7efb07284b35472b6f88e4ea8e5fd7a"
    assert acceptance["candidate_base_head"] == acceptance["candidate_head"]
    assert acceptance["last_failed_ci"] == {
        "run_number": 96,
        "run_id": 41402275878,
        "head": "6651915ad2bc662fe45006639204a259bbce27c5",
        "conclusion": "failure",
        "passed": 6812,
        "failed": 3,
        "deselected": 30,
        "warnings": 31,
    }
    assert acceptance["candidate_commit_identity"] == (
        "RESOLVE_COMMIT_CONTAINING_THIS_FILE_FROM_GIT"
    )
    assert acceptance["candidate_ci_state"] == (
        "RECHECK_CONTINUITY_NATURAL_CI_AT_RUNTIME"
    )
    assert acceptance["candidate_head_scope"] == (
        "CI100_ACCEPTED_FUNCTIONAL_HEAD; COMMIT_CONTAINING_THIS_FILE_IS_"
        "CONTINUITY_PROJECTION_NOT_MAIN"
    )
    assert acceptance["latest_required_ci"] == {
        "run_number": 100,
        "run_id": 41570430451,
        "head": "16963cee711392605b2515eef00e390802f5eeeb",
        "conclusion": "success",
    }
    # CI94 is the direct failed predecessor; CI92 remains in Git/history.
    assert acceptance["prior_failed_ci"] == {
        "run_number": 94,
        "run_id": 40677519496,
        "head": "e10aa72c575071fd037971b58a5c75f18eb4c74a",
        "conclusion": "failure",
    }
    assert acceptance["pull_request_state"] == "DRAFT_OPEN_NOT_MERGED"
    assert baseline["route_decision"]["next_primary"] == (
        "MARKET_ALL_A_BSE_DENOMINATOR_BREADTH_AND_LIMITS_GATE"
    )


def test_current_local_successor_tracks_official_r003_without_product_inflation() -> None:
    current = _baseline()["current_local_successor"]
    assert current["state"] == "HISTORICAL_INVALIDATED_LOCAL_SUCCESSOR_NOT_CURRENT"
    assert current["historical_only"] is True
    assert current["base_head"] == "37e453f729b0f9ad1cec6d8b27457916b7982142"
    assert current["base_tree"] == "6b54b197327501e2673b5c499eed4a1628ac95f9"
    assert len(current["modified_paths"]) == 15
    assert "docs/b1-source-rights.md" in current["modified_paths"]
    assert current["staged_paths"] == current["untracked_paths"] == 0
    assert current["commit_push_merge"] == 0
    assert current["scoped_regression"] == {
        "trading_calendar": 57,
        "daily_acquisition_binding": 34,
        "multi_timeframe": 57,
        "rights_admission": 35,
        "total": 183,
    }

    rights = current["official_baostock_rights"]
    assert rights["operation_id"] == "stock-dsa-nosend-20261029-baostock-official-r003"
    assert rights["grant_count"] == 19
    assert rights["package_bytes"] == 24662
    assert rights["package_sha256"] == (
        "04d59e90952fafc015752d607f4033c2b9c3c4e2af85ec340e19d0be0038f691"
    )
    assert rights["terms_uri"] == "https://www.baostock.com/mainContent?file=stockKData.md"
    assert rights["terms_classification"] == "OFFICIAL_TECHNICAL_API_DOCUMENTATION_NOT_DATA_TERMS"
    assert rights["state"] == "INVALIDATED_TECHNICAL_DOCUMENTATION_NOT_TERMS"
    assert rights["upstream_rights_state"] == "UNKNOWN"
    assert rights["verified_grant_count"] == 0
    assert rights["product_admission_valid"] is False
    assert rights["storage"] == rights["training"] == rights["redistribution"] == "FORBIDDEN"

    terms_review = current["official_terms_scope_review"]
    assert terms_review["receipt_path"] == ".tmp/stock_dsa_b1/STOCK_DSA_B1_OFFICIAL_CONTRACT_SCOPE_INVALIDATION_R002.json"
    assert terms_review["api_market_data_use_terms_found"] is False
    assert terms_review["upstream_rights_state"] == "UNKNOWN"
    assert terms_review["product_admission_valid"] is False
    assert len(terms_review["contracts"]) == 3

    run = current["formal_stage1_run"]
    assert run["state"] == "INVALIDATED_FOR_RIGHTS_EVIDENCE_SOURCE_OBSERVED_ONLY"
    assert run["prior_reported_data_objects"] == {"READY": 18, "UNKNOWN": 58}
    assert run["current_product_admission"] == {
        "PRODUCT_ADMITTED": 0, "RIGHTS_UNKNOWN": 72, "future_record_unknown": 4,
    }
    assert run["matrix_scope"] == "SOURCE_OBSERVED_METHOD_MATRIX_NOT_PRODUCT_ADMISSION"
    assert run["canonical_scope"] == "FAIL_CLOSED_OUTPUT_NOT_PRODUCT_ADMISSION"
    assert run["atomic_subfields"] == {"UNRESOLVED_MISSINGNESS": 7}
    assert run["valuation_routes"] == {
        "ACCOUNTED_NOT_AUTOMATICALLY_APPLICABLE": 9,
    }
    assert sum(run["stock_600519_matrix"].values()) == 56
    assert sum(run["etf_510300_matrix"].values()) == 56
    assert run["stock_canonical_hash"] == (
        "687235855b5b73dc78a9671da93825195d5b2310ee4afc73cda3017dac5f269d"
    )
    assert run["etf_canonical_hash"] == (
        "4f74f1c1bbda23ab538b1bb9a2abd4956ec1f6b5f348768a5b2b803ea80d56d9"
    )
    v25 = run["v2_5"]
    assert v25 == {
        "bytes": 107186,
        "sha256": "187a99d58b2b8bf26b5c6fb89a40c315f1b13ab8775c0afd0ea63f3d617199d5",
        "persisted": False,
        "no_send": True,
        "stage1_state": "HOLD",
        "projection_scope": "HISTORICAL_PARTIAL_NO_SEND_NOT_CURRENT_PRODUCT",
        "blocker_codes": [
            "RIGHTS_TERMS_NOT_VERIFIED",
            "FULL_DOMAIN_PRODUCT_DATA_NOT_READY",
            "ATOMIC_SUBFIELD_V25_CONSUMER_BINDING_INCOMPLETE",
        ],
    }
    assert all(value == 0 or value is False for value in run["effects"].values())
    assert current["gate_b"] == "STOP_RIGHTS_TERMS_NOT_VERIFIED_OFFICIAL_TERMS_SCOPE_MISMATCH"
    assert current["gate_c"] == "HOLD_RIGHTS_TERMS_NOT_VERIFIED_AND_FULL_DOMAIN_PRODUCT_DATA_NOT_READY"
    assert current["proof_ceiling"] == (
        "SOURCE_OBSERVED_METHOD_MATRIX_AND_FAIL_CLOSED_CANONICAL_ONLY_NOT_CURRENT_PRODUCT_OR_FULL_V25"
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


def test_current_control_rejects_historical_product_and_email_geometry_substitution() -> None:
    baseline = _baseline()
    current = baseline["current_runtime_checkpoint"]

    assert current["schema_version"] == "stock-dsa-current-runtime-checkpoint-v6"
    assert current["source_pair"] == "01/05 v3.556 PHYSICAL_EXACT8_PASS"
    accepted = current["accepted_remote_base"]
    assert accepted["head"] == "b8f5d9258b717cf08d9ad6590e6143ee7e0e15ce"
    assert accepted["parent"] == "ac3cce4c2f6ada5d7641a270dd29affa25760b35"
    assert accepted["ci_run_id"] == 41995009888
    assert accepted["event"] == "pull_request"
    assert accepted["conclusion"] == "success"
    assert (accepted["jobs_success"], accepted["jobs_skipped"]) == (6, 1)
    assert accepted["draft"] is True and accepted["merged"] is False
    predecessor = current["local_predecessor_checkpoint"]
    assert predecessor == {
        "head": "f084e8562ea8fc7b9564c541c537e618a2e40c89",
        "scope": "M_INDEX_CURRENT_AND_CI_REENTRY_IDENTITY_ROOTFIX",
        "remote_currentness": "CI_SCREENSHOTS_OBSERVED_BACKEND_GATE_FAILED",
    }
    assert current["candidate_identity"] == "RESOLVE_COMMIT_CONTAINING_THIS_FILE_FROM_GIT"
    local = current["local_regression"]
    assert local["product13_same_operation"] == "PASS_PREDECESSOR_NOT_REPLAYED"
    assert local["product13_receipt_sha256"] == (
        "ce4fa1746e16e7c62d435b11c02f7868946d3d4d6f922bd0ce004458c5900231"
    )
    assert local["extended_indicator_checkpoint_selected_passed"] == 447
    assert local["sse_index_current_vertical_passed"] == 17
    assert local["current_observation_b1_v25_adjacent_passed"] == 48
    assert local["ci_screenshot_rootfix_targeted_passed"] == 10
    assert local["evidence_flywheel_full_passed"] == 64
    assert local["cn_exchange_turnover_and_b1_passed"] == 60
    assert local["integrated_reentry_stage_turnover_flywheel_passed"] == 112
    assert local["market_b1_registry_v25_adjacent_passed"] == 331
    assert local["actual_cn_exchange_turnover_operation"] == "PASS"
    assert local["local_full_offline_suite"] == (
        "COLLECTION_BLOCKED_BY_UNINSTALLED_DECLARED_FAKE_USERAGENT_AND_MARKDOWN2_REMOTE_CI_REQUIRED"
    )
    assert local["selected_failed"] == 0
    matrix = current["seven_timeframe_eight_family"]
    assert matrix == {
        "timeframe_carriers_implemented": True,
        "technical_matrix_cell_count": 56,
        "method_profile_count": 43,
        "methods_existing_reused": 35,
        "methods_deferred_with_owner": 8,
        "technical_method_timeframe_targets": 189,
        "all_56_cells_executable": True,
        "regime_cross_timeframe_vote_counting": False,
        "relative_strength_full_bar_grid_required": True,
        "current_real_product_bound_56_cells_complete": False,
    }
    product = current["product_state"]
    assert len(product["latest_multi_object_operation_product_ids"]) == 13
    assert product["latest_multi_object_operation_product_count"] == 13
    assert product["atomic_market_product_ids"] == ["M_INDEX_CURRENT", "M_TURNOVER"]
    assert product["atomic_market_product_count"] == 2
    assert product["latest_atomic_market_operation_id"] == (
        "cn-exchange-turnover-nosend-20261106T040925Z"
    )
    assert product["cross_operation_sum_forbidden"] is True
    assert product["denominator"] == 72
    assert product["full_domain_product_ready"] is False
    assert product["typed_or_real_source_capability_minimum"] == 35
    assert product["without_complete_typed_source_chain"] == 37
    email = current["email_reader_state"]
    assert email["old_empty_numeric_email_revoked"] is True
    assert email["current_fact_ready"] is False
    assert email["original_v25_immutable"] is True
    assert email["latest_multi_object_v25_fact_summary_count"] == 13
    assert email["atomic_market_v25_fact_summary_count"] == 2
    assert email["cross_operation_render_forbidden"] is True
    assert email["smtp_send"] == 0
    roadmap = (ROOT / "docs" / "stock-dsa-roadmap.md").read_text(encoding="utf-8")
    assert "CONTROL_ID = STOCK_DSA_MASTER_ROUTE_20261106_M_TURNOVER_LOCAL_REENTRY_V3" in roadmap
    assert "最近多对象 CURRENT operation：**13/72（18.1%）**" in roadmap
    assert "full_domain_product_ready=false" in roadmap
    assert "绝不倒灌历史" not in roadmap or "CURRENT" in roadmap
    assert "SOURCE_UPDATE_DECISION = KEEP_PHYSICAL_01_05_V3_556_NO_CHURN" in roadmap
    assert "实际Files inventory均证明Project Source恰好8个唯一Owner" not in roadmap
    assert "方法 Profile：**35/43（81.4%）**" in roadmap
    assert "技术数值叶：**35/35（100%）**算法准入" in roadmap
    assert "189 个有效方法－周期执行对" in roadmap
    assert "docs/stock-dsa-github-reentry.md" in roadmap
    assert "scripts/verify_stock_dsa_reentry.py --json" in roadmap
    regression = baseline["regression_policy"]
    assert regression[
        "canonical_github_https_origin_optional_dot_git_and_trailing_slash_equivalent"
    ] is True
    assert regression[
        "github_actions_pr_detached_checkout_requires_exact_sha_and_head_ref_when_remote_ref_absent"
    ] is True
    assert regression[
        "ordinary_local_reentry_still_requires_real_feature_ref_or_exact_bundle"
    ] is True
    assert regression[
        "historical_trace_schema_identity_uses_frozen_manifest_hash_mapping_not_current_catalog_rebuild"
    ] is True
    assert regression[
        "m_turnover_two_exchange_value_does_not_grant_bse_all_a_breadth_turnover_rate_or_relative_volume"
    ] is True
    assert regression["historical_operation_admission_never_backfills_current_runtime"] is True
    assert regression["html_geometry_never_proves_reader_visible_numeric_leaves"] is True
    assert regression["reader_numeric_leaf_audit_required"] is True
    assert regression[
        "extended_indicators_require_source_identity_completed_bars_volume_units_and_method_receipts"
    ] is True
    assert regression[
        "extended_indicators_never_grant_product_strategy_pit_learning_or_independent_votes"
    ] is True
    assert regression["industry_route_plan_required_before_business_valuation"] is True
    assert regression["distribution_or_markdown_veto_requires_strict_method_receipt"] is True
    assert regression["accumulation_or_washout_never_independently_upgrades_action"] is True
    assert regression["chan_narrow_adapter_never_claims_full_czsc"] is True
    assert regression[
        "etf_valuation_band_never_substitutes_observed_premium_discount"
    ] is True
    assert regression[
        "p_exec_risk_partial_never_substitutes_suspension_limit_spread_and_fees"
    ] is True


    regression = baseline["regression_policy"]
    assert regression["historical_operation_admission_never_backfills_current_runtime"] is True
    assert regression["html_geometry_never_proves_reader_visible_numeric_leaves"] is True
    assert regression["reader_numeric_leaf_audit_required"] is True
    assert regression["industry_route_plan_required_before_business_valuation"] is True
    assert regression["distribution_or_markdown_veto_requires_strict_method_receipt"] is True
    assert regression["accumulation_or_washout_never_independently_upgrades_action"] is True
    assert regression["chan_narrow_adapter_never_claims_full_czsc"] is True
    assert regression[
        "etf_valuation_band_never_substitutes_observed_premium_discount"
    ] is True
    assert regression[
        "p_exec_risk_partial_never_substitutes_suspension_limit_spread_and_fees"
    ] is True
    assert regression[
        "etf_nav_premium_requires_raw_same_asset_same_day_price_identity"
    ] is True


def test_current_stage1_real_run_is_scoped_partial_and_fail_closed() -> None:
    evidence = _baseline()["current_stage1_real_run_evidence"]
    assert evidence["state"] == "INVALIDATED_HISTORICAL_PRICE_RIGHTS_FALSE_ADMISSION"
    assert evidence["rights_schema_version"] == "source-rights-evidence-v2"
    assert evidence["product_admitted_dataset_ids"] == []
    assert evidence["prior_claimed_product_admitted_dataset_ids"] == [
        "P_DAILY", "P_5M", "E_PRICE_1D", "E_PRICE_5M",
    ]
    assert evidence["stock_product_admitted_count"] == 0
    assert evidence["etf_product_admitted_count"] == 0
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
        "RIGHTS_TERMS_NOT_VERIFIED",
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
    assert counts["current_product_admitted_data_objects"] == 0
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


def test_stage_baseline_records_installed_v3545_and_current_successor_boundaries() -> None:
    baseline = _baseline()
    source = baseline["source_checkpoint"]
    assert source["installed_pair"] == "01/05 v3.545"
    assert source["installed_pair_state"] == "INSTALLED_AND_THREE_FRESH_EIGHT_OWNER_INVENTORIES_PASS"
    assert source["fresh_project_source_count"] == 8
    assert source["duplicate_logical_owners"] == 0
    assert source["prepared_successor_pair"] is None
    assert source["prepared_successor_state"] == (
        "NONE_UNTIL_PREMIUM_EXECUTION_GITHUB_COMMIT_AND_CI_MATERIAL_CLOSURE"
    )
    assert source["ninth_source"] is False

    successor = baseline["current_local_foundation_successor"]
    assert successor["state"] == "PREMIUM_EXECUTION_VERTICAL_REMOTE_CI100_ACCEPTED"
    assert successor["base_head"] == "16963cee711392605b2515eef00e390802f5eeeb"
    assert successor["accepted_head"] == successor["base_head"]
    assert successor["accepted_ci"] == {
        "run_number": 100,
        "run_id": 41570430451,
        "conclusion": "success",
    }
    assert successor["changed_paths"] == 15
    assert successor["method_profiles"] == 43
    assert successor["effective_execution_pairs"] == 154
    assert successor["effective_override_methods"] == 22
    assert successor["current_product_admission"] == "UNCHANGED_0_OF_72"
    assert successor["gate_b"] == "STOP_FULL_DOMAIN_PRODUCT_RIGHTS_AND_CURRENT_FACTS"
    assert successor["gate_c"] == "HOLD_ORIGINAL_V25_FACT_READY_AND_DELIVERY"
    assert successor["original_v25_unchanged"] is True


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
    assert decision["next_primary"] == (
        "MARKET_ALL_A_BSE_DENOMINATOR_BREADTH_AND_LIMITS_GATE"
    )
    assert decision["bounded_indicator_sub_lanes"] == [
        "RISK_REWARD_AFTER_EXECUTION_FACTS",
        "VWAP_AFTER_COMPLETED_INTRADAY_VOLUME",
        "ETF_SPECIFIC_AFTER_SPREAD_TRACKING_FEES",
    ]
    assert decision["deferred_until_product_trigger"] == [
        "AVWAP_VOLUME_PROFILE", "WAVE", "GLOBAL", "BREADTH", "PROBABILITY",
    ]
    for method_id in ("ADX_DMI", "BOLLINGER", "KDJ", "OBV_ADL", "MFI"):
        assert method_id not in decision["deferred_until_product_trigger"]
    assert "CHAN_WAVE" not in decision["deferred_until_product_trigger"]
    assert _baseline()["regression_policy"]["static_counts_never_prove_runtime_ready"] is True


def test_current_real_data_readiness_audit_separates_capability_product_database_and_email() -> None:
    audit = _baseline()["current_real_data_readiness_audit"]
    assert audit["schema_version"] == "stock-dsa-real-data-readiness-audit-v1"
    assert audit["audit_date"] == "2026-11-05"
    assert audit["method_catalog"] == {
        "total": 43,
        "existing_reused": 35,
        "deferred_with_owner": 8,
        "technical_matrix_methods": 30,
        "asset_layer_methods": 4,
        "research_shadow_methods": 1,
    }
    assert audit["numeric_metric_leaves"] == {
        "total": 35,
        "algorithm_admitted": 35,
        "method_not_admitted": 0,
    }
    objects = audit["product_data_objects"]
    assert objects["total"] == 72
    assert objects["current_product_admitted"] == 11
    assert objects["stock_applicable"] == 60
    assert objects["etf_applicable"] == 55
    assert objects["typed_or_real_source_capability_minimum"] == 33
    assert objects["without_complete_typed_source_chain"] == 39
    assert objects["no_current_typed_producer"] == 18
    assert objects["consumer_or_partial_code_only"] == 18
    assert objects["direct_snapshot_callable_not_typed_or_rights_admitted"] == 3
    capabilities = audit["typed_or_real_source_capability_ids"]
    assert len(capabilities) == 33
    assert {
        "S_PRICES_RS", "S_CLASSIFICATION", "E_NAV_IOPV", "E_SHARES",
        "E_BASKET", "P_CODE_IDENTITY", "P_INDUSTRY", "G_USD_BROAD",
        "G_US_POLICY", "G_UST_REAL", "G_INFL_BREAKEVEN",
    }.issubset(capabilities)
    assert audit["global_atomic_subfields"] == {
        "total": 7,
        "ready": 0,
        "bls_adapter_but_runtime_or_terms_blocked": 3,
        "no_current_producer": 4,
    }
    database = audit["database"]
    assert database["schema_exists"] is True
    assert database["database_framework_missing"] is False
    assert database["product_tables_with_current_rows"] == 0
    assert len(database["zero_row_tables"]) == 11
    email = audit["email"]
    assert email["template_rebuild_required"] is False
    assert email["all_real_values_possible_architecturally"] is True
    assert email["all_real_values_available_now"] is False
    assert email["original_v25_fact_ready"] is False
    assert email["smtp_send"] == 0


    correction = _baseline()["ci96_corrective_closure"]
    assert correction["remote_result"] == {
        "passed": 6812,
        "failed": 3,
        "deselected": 30,
        "warnings": 31,
        "duration_seconds": 548.59,
    }
    assert correction["targeted_regression"] == {"passed": 5, "failed": 0}
    assert correction["related_regression"]["passed"] == 1375
    assert correction["related_regression"]["failed"] == 0
    assert correction["state"] == "CLOSED_NO_REPLAY_BY_CI97"
    assert correction["remote_corrective_ci"] == {
        "run_number": 97,
        "run_id": 41417008187,
        "head": "746e402e0fff0247fdf153347e66f326573db9ce",
        "conclusion": "success",
    }


def test_multi_level_goal_graph_capabilities_role_review_and_github_continuity_are_machine_readable() -> None:
    baseline = _baseline()
    graph = baseline["multi_level_goal_graph"]
    assert graph["levels"] == ["PROGRAM", "PRODUCT", "LANE", "MILESTONE", "ATOMIC"]
    assert ["PROGRAM", "PRODUCT"] in graph["edges"]
    assert ["B8_FULL_REPORT", "L1_LEDGER_PIT"] in graph["edges"]
    assert set(graph["horizontal_gates"]) == {
        "DATA", "RISK", "LIQUIDITY", "EXECUTION", "KNOWN_AT",
        "RIGHTS_PRIVACY", "VERSION_IDENTITY", "COST", "USER_BURDEN", "STOP_LOSS",
    }
    assert graph["sibling_lane_success_never_overrides_required_gate"] is True

    closure = baseline["current_capability_closure"]
    assert closure["industry_valuation_router"]["route_count"] == 9
    assert closure["industry_valuation_router"]["universal_default_route"] is False
    assert closure["volume_price_phase"]["institutional_intent_inferred"] is False
    assert closure["volume_price_phase"]["positive_candidate_upgrades_buy"] is False
    assert closure["narrow_chan_adapter"]["full_czsc_claim"] is False
    assert closure["narrow_chan_adapter"]["independent_action_authority"] is False
    assert closure["seven_timeframe_eight_family"] == {
        "matrix_cells": 56,
        "method_profiles": 43,
        "existing_reused": 35,
        "deferred": 8,
        "effective_execution_pairs": 189,
        "all_cells_executable": True,
    }
    assert closure["product_admission"] == "0_OF_72_UNCHANGED"
    assert closure["focused_regression"]["remote_ci"] == (
        "CI100_SUCCESS_PREMIUM_EXECUTION_REMOTE_ACCEPTED"
    )
    typed = closure["typed_source_vertical_slice"]
    assert typed["dataset_ids"] == ["P_COST_PROXY", "E_PEER_RS", "M_INDEX_BARS"]
    assert typed["focused_passed"] == 5
    assert typed["related_passed"] == 583
    assert typed["product_admitted"] is False
    assert typed["independent_source_value_admitted"] is True
    premium_execution = closure["premium_execution_vertical_slice"]
    assert premium_execution["dataset_ids"] == ["E_PREMIUM", "P_EXEC_RISK"]
    assert premium_execution["focused_passed"] == 6
    assert premium_execution["core_passed"] == 531
    assert premium_execution["adjacent_passed"] == 271
    assert premium_execution["related_passed"] == 802
    assert premium_execution["valuation_band_substitution_rejected"] is True
    assert premium_execution["product_admitted"] is False
    assert premium_execution["independent_source_value_admitted"] is True
    accepted = baseline["ci98_remote_acceptance"]
    assert accepted == {
        "schema_version": "stock-dsa-ci98-remote-acceptance-v1",
        "head": "c2629c07168764b2cdf2bf733697447e45848365",
        "parent": "746e402e0fff0247fdf153347e66f326573db9ce",
        "tree": "ca629a383af6e15d958a2d184df9be4b0f8933b9",
        "run_number": 98,
        "run_id": 41422089007,
        "conclusion": "success",
        "pr": 31,
        "pr_state": "DRAFT_OPEN_NOT_MERGED",
        "main_unchanged": True,
        "product_admission": "0_OF_72_UNCHANGED",
        "fact_ready": False,
    }
    accepted99 = baseline["ci99_remote_acceptance"]
    assert accepted99 == {
        "schema_version": "stock-dsa-ci99-remote-acceptance-v1",
        "head": "6eb822f5a7efb07284b35472b6f88e4ea8e5fd7a",
        "parent": "c2629c07168764b2cdf2bf733697447e45848365",
        "tree": "7df6259ea8f11eb0964e9195d23aa5a1b6f1feb4",
        "run_number": 99,
        "run_id": 41434978379,
        "conclusion": "success",
        "pr": 31,
        "pr_state": "DRAFT_OPEN_NOT_MERGED",
        "main_unchanged": True,
        "product_admission": "0_OF_72_UNCHANGED",
        "fact_ready": False,
    }
    accepted100 = baseline["ci100_remote_acceptance"]
    assert accepted100["head"] == "16963cee711392605b2515eef00e390802f5eeeb"
    assert accepted100["parent"] == "6eb822f5a7efb07284b35472b6f88e4ea8e5fd7a"
    assert accepted100["tree"] == "96afb8ddcf21198ee7490d0474c96049f4aba2f3"
    assert accepted100["run_number"] == 100
    assert accepted100["run_id"] == 41570430451
    assert accepted100["conclusion"] == "success"
    assert accepted100["pr_state"] == "DRAFT_OPEN_NOT_MERGED"
    assert accepted100["product_admission"] == "0_OF_72_UNCHANGED"
    assert accepted100["fact_ready"] is False

    slice_ = baseline["premium_execution_vertical_slice"]
    assert slice_["dataset_ids"] == ["E_PREMIUM", "P_EXEC_RISK"]
    assert slice_["focused_regression"] == {"passed": 6, "failed": 0}
    assert slice_["related_regression"] == {
        "selected_modules": 18,
        "core_passed": 531,
        "adjacent_passed": 271,
        "passed": 802,
        "failed": 0,
        "duration_seconds": 597.10,
        "warnings": 2,
        "environment_recovery": (
            "TWO_OPTIONAL_LARK_OAPI_TEST_SHAPE_STOPS_THEN_"
            "PROCESS_ONLY_COMPLETE_SDK_SHIM_GREEN"
        ),
    }
    assert slice_["source_capability_after"] == 28
    assert slice_["source_chain_gaps_after"] == 44
    assert slice_["full_intraday_premium_ready"] is False
    assert slice_["full_execution_risk_ready"] is False
    assert closure["original_v25_fact_ready"] is False

    review = baseline["role_review"]
    assert set(review) >= {
        "PRODUCT", "DATA_MODEL", "PROCESS_CONSUMER", "AUTHORITY_CURRENTNESS",
        "ENVIRONMENT", "ADAPTER_INTERFACE", "DEV_TOOLING", "USER_BURDEN",
    }
    assert review["current_route_still_worth_continuing"] == "YES_KEEP_ONLY_DSA"

    github = baseline["github_continuity_contract"]
    assert github["canonical_origin"] == "https://github.com/leeningzzu/dsa-market-research.git"
    assert github["feature_branch"] == "feat/stock-dsa-m2-source-gates-v1"
    assert github["pull_request"] == 31
    assert github["direct_main_push"] is github["force_push"] is False
    assert github["merge_authority"] is github["manual_ci_dispatch"] is False
    assert github["runtime_git_evidence_overrides_embedded_sha"] is True
    assert github["every_material_capability_requires"] == [
        "PRODUCTION_OWNER", "ACTUAL_CONSUMER", "INDEPENDENT_NEGATIVE",
        "LAWFUL_NONTRIGGER", "ROADMAP_PROJECTION", "MACHINE_JSON", "NATURAL_CI",
    ]


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
