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
    assert locked["methods_existing_reused"] == states["EXISTING_REUSED"] == 30
    assert locked["methods_deferred_with_owner"] == states["DEFERRED_WITH_OWNER_AND_REENTRY"] == 13
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
        "GITHUB_CORRECTIVE_COMMIT_PUSH_NATURAL_CI_THEN_B1_M2_49_SOURCE_CHAIN_REENTRY"
    )
    assert [stage["id"] for stage in stages if stage.get("next_primary")] == [
        "B1_SOURCE_RIGHTS"
    ]
    assert stages[1]["local_state"] == "PARTIAL_SSE_NONPRICE_OPERATION_SCOPED_PRICE_RIGHTS_STOP"
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
        "CI95_ACCEPTED_BASE_CI96_FAILED_CORRECTIVE_CANDIDATE_"
        "RESOLVE_GIT_AND_CI_AT_RUNTIME"
    )
    assert acceptance["candidate_head"] == "6651915ad2bc662fe45006639204a259bbce27c5"
    assert acceptance["candidate_parent"] == "37e453f729b0f9ad1cec6d8b27457916b7982142"
    assert acceptance["candidate_base_head"] == acceptance["candidate_head"]
    assert acceptance["current_failed_ci"] == {
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
    assert acceptance["candidate_ci_state"] == "RECHECK_PR31_NATURAL_CI_AT_RUNTIME"
    assert acceptance["candidate_head_scope"] == (
        "CI96_FAILED_HEAD_IS_CORRECTIVE_PARENT; COMMIT_CONTAINING_THIS_FILE_IS_"
        "CURRENT_CORRECTIVE_FEATURE_CANDIDATE_NOT_MAIN"
    )
    assert acceptance["latest_required_ci"] == {
        "run_number": 95,
        "run_id": 40792809015,
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
        "GITHUB_CORRECTIVE_COMMIT_PUSH_NATURAL_CI_THEN_B1_M2_49_SOURCE_CHAIN_REENTRY"
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

    assert current["schema_version"] == "stock-dsa-current-runtime-checkpoint-v2"
    assert current["source_pair"] == "01/05 v3.543 PHYSICAL_EXACT8_PASS"
    assert current["accepted_remote_base"] == {
        "head": "37e453f729b0f9ad1cec6d8b27457916b7982142",
        "ci_run_number": 95,
        "ci_run_id": 40792809015,
        "conclusion": "success",
    }
    assert current["candidate_identity"] == "RESOLVE_COMMIT_CONTAINING_THIS_FILE_FROM_GIT"
    snapshot = current["candidate_precommit_snapshot"]
    assert snapshot["status_path_count"] == 8
    assert snapshot["tracked_modified_count"] == 8
    assert snapshot["untracked_paths"] == []
    assert snapshot["staged_count"] == 0
    assert snapshot["post_commit_worktree_expected_clean"] is True
    assert current["local_regression"]["focused_passed"] == 61
    assert current["local_regression"]["focused_failed"] == 0
    assert current["local_regression"]["full_related"] == {
        "selected_modules": 46,
        "passed": 1375,
        "failed": 0,
        "duration_seconds": 612.18,
        "warnings": 2,
    }
    assert current["local_regression"]["ci96_targeted_passed"] == 5
    assert current["local_regression"]["ci96_targeted_failed"] == 0
    assert current["local_regression"]["remote_ci"] == (
        "CI96_FAILED_CORRECTIVE_LOCAL_PASS_PENDING_PUSH"
    )
    assert current["local_regression"]["litellm_local_cost_map_required_before_import"] is True

    matrix = current["seven_timeframe_eight_family"]
    assert matrix == {
        "timeframe_carriers_implemented": True,
        "technical_matrix_cell_count": 56,
        "method_profile_count": 43,
        "methods_existing_reused": 30,
        "methods_deferred_with_owner": 13,
        "technical_method_timeframe_targets": 154,
        "all_56_cells_executable": True,
        "regime_cross_timeframe_vote_counting": False,
        "relative_strength_full_bar_grid_required": True,
        "current_real_product_bound_56_cells_complete": False,
    }
    assert current["product_state"]["current_reconstructible_product_ids"] == []
    assert current["product_state"]["denominator"] == 72
    assert current["product_state"]["full_domain_product_ready"] is False
    assert current["email_reader_state"]["old_empty_numeric_email_revoked"] is True
    assert current["email_reader_state"]["current_fact_ready"] is False
    assert current["email_reader_state"]["original_v25_immutable"] is True
    assert current["email_reader_state"]["smtp_send"] == 0

    roadmap = (ROOT / "docs" / "stock-dsa-roadmap.md").read_text(encoding="utf-8")
    assert "CONTROL_ID = STOCK_DSA_MASTER_ROUTE_20261102_CI96_CORRECTION_REAL_DATA_READINESS_AUDIT" in roadmap
    assert "行业分类→适用方法→所需数据" not in roadmap  # prose must not claim an unbound shortcut
    assert "只表示可观察候选" in roadmap
    assert "NOT_FULL_CZSC" in roadmap
    assert "GitHub是跨模型连续性的唯一代码基线" in roadmap

    regression = baseline["regression_policy"]
    assert regression["historical_operation_admission_never_backfills_current_runtime"] is True
    assert regression["html_geometry_never_proves_reader_visible_numeric_leaves"] is True
    assert regression["reader_numeric_leaf_audit_required"] is True
    assert regression["industry_route_plan_required_before_business_valuation"] is True
    assert regression["distribution_or_markdown_veto_requires_strict_method_receipt"] is True
    assert regression["accumulation_or_washout_never_independently_upgrades_action"] is True
    assert regression["chan_narrow_adapter_never_claims_full_czsc"] is True


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


def test_stage_baseline_records_installed_v3543_and_current_successor_boundaries() -> None:
    baseline = _baseline()
    source = baseline["source_checkpoint"]
    assert source["installed_pair"] == "01/05 v3.543"
    assert source["installed_pair_state"] == "INSTALLED_AND_THREE_FRESH_EIGHT_OWNER_INVENTORIES_PASS"
    assert source["fresh_project_source_count"] == 8
    assert source["duplicate_logical_owners"] == 0
    assert source["prepared_successor_pair"] is None
    assert source["prepared_successor_state"] == (
        "NONE_UNTIL_CORRECTIVE_GITHUB_COMMIT_AND_CI_MATERIAL_CLOSURE"
    )
    assert source["ninth_source"] is False

    successor = baseline["current_local_foundation_successor"]
    assert successor["state"] == "CI96_CORRECTIVE_CANDIDATE_PRECOMMIT_REMOTE_OPEN"
    assert successor["changed_paths"] == 8
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
        "GITHUB_CORRECTIVE_COMMIT_PUSH_NATURAL_CI_THEN_B1_M2_49_SOURCE_CHAIN_REENTRY"
    )
    assert decision["bounded_indicator_sub_lanes"] == [
        "MONTHLY_RSI_HISTORY_IF_LEGAL",
        "15M_CONFIRMATION",
        "5M_EXECUTION_TIMING",
        "MINUTE_VOLUME_AMOUNT_UNITS",
    ]
    assert "WAVE" in decision["deferred_until_product_trigger"]
    assert "CHAN_WAVE" not in decision["deferred_until_product_trigger"]
    assert _baseline()["regression_policy"]["static_counts_never_prove_runtime_ready"] is True


def test_current_real_data_readiness_audit_separates_capability_product_database_and_email() -> None:
    audit = _baseline()["current_real_data_readiness_audit"]
    assert audit["schema_version"] == "stock-dsa-real-data-readiness-audit-v1"
    assert audit["method_catalog"] == {
        "total": 43,
        "existing_reused": 30,
        "deferred_with_owner": 13,
        "technical_matrix_methods": 25,
        "asset_layer_methods": 4,
        "research_shadow_methods": 1,
    }
    assert audit["numeric_metric_leaves"] == {
        "total": 35,
        "algorithm_admitted": 24,
        "method_not_admitted": 11,
    }
    objects = audit["product_data_objects"]
    assert objects["total"] == 72
    assert objects["current_product_admitted"] == 0
    assert objects["stock_applicable"] == 60
    assert objects["etf_applicable"] == 55
    assert objects["typed_or_real_source_capability_minimum"] == 23
    assert objects["without_complete_typed_source_chain"] == 49
    assert objects["no_current_typed_producer"] == 22
    assert objects["consumer_or_partial_code_only"] == 24
    assert objects["direct_snapshot_callable_not_typed_or_rights_admitted"] == 3
    assert len(audit["typed_or_real_source_capability_ids"]) == 23
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
    assert correction["remote_corrective_ci"] == "PENDING_ORDINARY_PUSH"


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
        "existing_reused": 30,
        "deferred": 13,
        "effective_execution_pairs": 154,
        "all_cells_executable": True,
    }
    assert closure["product_admission"] == "0_OF_72_UNCHANGED"
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
