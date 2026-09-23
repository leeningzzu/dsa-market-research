from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

R004_BASELINE_ID = "STOCK_DSA_INFORMATION_DENSE_EMAIL_GOLD_MASTER_R004"
R004_ACCEPTANCE_ID = "STOCK_DSA_INFORMATION_DENSE_GOLD_MASTER_R004_ACCEPTANCE_20260922_R001"
R004_ARTIFACT_SHA256 = {
    "00_STOCK_DSA_INFORMATION_DENSE_GOLD_MASTER_PREVIEW_R004.html":
        "4857670d0597111b6124704169e1a8a100b035e008d9eb728f11a26ad9819e07",
    "01_MARKET_REGIME_BRIEF_INFORMATION_DENSE_SIMULATION_R004.html":
        "859b4ee255cba170d743594ec0ec591a2b16ad74b93b18a3f15422b3858c3a81",
    "02_ASSET_RESEARCH_BRIEF_AUTO_INFORMATION_DENSE_SIMULATION_R004.html":
        "827d64d5560fe79df82a0f26c3ea549df300fa17f220e71a5c8142f93fe67fba",
    "03_ASSET_RESEARCH_BRIEF_WATCHLIST_INFORMATION_DENSE_SIMULATION_R004.html":
        "4d1ac01d5e2aae31a3969ab9ff1ffeaa0a86d5131ae67e92fde34027f726d337",
    "STOCK_DSA_INFORMATION_DENSE_EMAIL_GOLD_MASTER_R004.zip":
        "aba13965b0688ea3689f5a5f78c8929e7d1b8dc9a3798374d7c25d5734525f1b",
}

EXPECTED_REQUIREMENT_IDS = {
    "ORDER-001",
    "VOICE-001",
    "TIMEFRAME-LABEL-001",
    "MTF-TECH-001",
    "MTF-ROLE-001",
    "MTF-CONFLICT-001",
    "CORR-001",
    "EVENT-RENDER-001",
    "STABLE-COMPRESS-001",
    "MARKET-MTF-001",
    "MARKET-BREADTH-001",
    "MARKET-STYLE-001",
    "MARKET-SPEC-001",
    "MARKET-FUNDING-001",
    "MARKET-GLOBAL-001",
    "MARKET-PERM-001",
    "VALUATION-STOCK-001",
    "VALUATION-ETF-001",
    "DONOR-SEAM-001",
    "ETF-001",
    "STOCK-001",
    "GROUP-001",
    "ENVELOPE-001",
    "HISTORY-001",
    "CANON-001",
    "CHANNEL-001",
    "MISSING-001",
}

# Exact private@83e6612 pre-authoring binding.  "data_capability" is deliberately
# conservative: READY means the current private tree has the bound deterministic
# surface; PARTIAL/SCHEMA states must remain fail-closed until rendered goldens
# and later data admission prove more.
REQUIREMENT_BINDINGS = {
    "ORDER-001": {
        "data_capability": "PARTIAL_CURRENT",
        "code_owner": "src/notification.py",
        "consumer": "_research_product_projection / generate_brief_report",
        "test_or_regression": "GF24_MARKET_GLOBAL_ETF_STOCK_ORDER",
        "missingness": "Unproven Market/Global sections stay absent or MISSING; ordering may not invent facts.",
    },
    "VOICE-001": {
        "data_capability": "READY_CURRENT",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "investor_brief -> report/notification renderers",
        "test_or_regression": "tests/test_research_brief_product_contract.py::test_phase_a_stock_human_synthesis_renders_material_events_with_exact_timeframe",
        "missingness": "Internal governance/status tokens must not leak into user prose.",
    },
    "TIMEFRAME-LABEL-001": {
        "data_capability": "READY_CURRENT",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "investor_brief.timeframe_thesis / short_term_execution_panel",
        "test_or_regression": "tests/test_notification.py::test_future_ready_mtf_and_short_term_schema_renders_without_new_authority",
        "missingness": "A missing timeframe is named as unavailable, never inferred from another timeframe.",
    },
    "MTF-TECH-001": {
        "data_capability": "PARTIAL_CURRENT_DWM__INTRADAY_DATA_LATER",
        "code_owner": "src/services/multi_timeframe_structure_service.py",
        "consumer": "factor_decision_summary investor_brief",
        "test_or_regression": "GF01_FULL_MTF_ALIGNMENT / GF11_INTRADAY_MISSING_DAILY_FIRST",
        "missingness": "60m/30m/15m/5m stay MISSING until completed intraday-bar contract is admitted.",
    },
    "MTF-ROLE-001": {
        "data_capability": "READY_SCHEMA",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "timeframe_thesis / short_term_execution_panel",
        "test_or_regression": "tests/test_notification.py::test_future_ready_mtf_and_short_term_schema_renders_without_new_authority",
        "missingness": "Roles remain present even when their evidence status is MISSING.",
    },
    "MTF-CONFLICT-001": {
        "data_capability": "READY_POLICY",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "canonical_decision",
        "test_or_regression": "tests/test_factor_decision_summary.py::test_lower_timeframe_schema_cannot_override_higher_level_canonical_veto",
        "missingness": "Lower-timeframe absence/conflict cannot manufacture an upgrade.",
    },
    "CORR-001": {
        "data_capability": "READY_POLICY_GOLDEN_REQUIRED",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "investor_brief.evidence_policy",
        "test_or_regression": "tests/test_pattern_trigger_service.py::test_same_swing_dedup_keeps_one_canonical_interpretation / GF06_SAME_SWING_NO_DOUBLE_COUNT",
        "missingness": "One underlying swing contributes one family confirmation/conflict, never duplicate votes.",
    },
    "EVENT-RENDER-001": {
        "data_capability": "READY_CURRENT",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "investor_brief.material_events / fused_paragraph",
        "test_or_regression": "tests/test_research_brief_product_contract.py::test_phase_a_stock_human_synthesis_renders_material_events_with_exact_timeframe",
        "missingness": "Only confirmed material events render; unavailable evidence is not converted to no-event claims.",
    },
    "STABLE-COMPRESS-001": {
        "data_capability": "READY_CURRENT",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "investor_brief.fused_paragraph",
        "test_or_regression": "tests/test_research_brief_product_contract.py::test_phase_a_stable_state_does_not_emit_no_event_checklist",
        "missingness": "Stable state compresses naturally without enumerating absent signals.",
    },
    "MARKET-MTF-001": {
        "data_capability": "PARTIAL_CURRENT",
        "code_owner": "src/market_analyzer.py",
        "consumer": "MARKET_REGIME_BRIEF",
        "test_or_regression": "GF01_FULL_MTF_ALIGNMENT / GF24_MARKET_GLOBAL_ETF_STOCK_ORDER",
        "missingness": "Current snapshot/index coverage does not imply seven-timeframe Market readiness.",
    },
    "MARKET-BREADTH-001": {
        "data_capability": "PARTIAL_CURRENT",
        "code_owner": "src/market_analyzer.py",
        "consumer": "market overview / MARKET_REGIME_BRIEF",
        "test_or_regression": "GF18_BREADTH_CONCRETE_FACTS",
        "missingness": "Unavailable denominators/ratios stay MISSING; qualitative breadth cannot replace concrete facts.",
    },
    "MARKET-STYLE-001": {
        "data_capability": "PARTIAL_CURRENT",
        "code_owner": "src/market_analyzer.py",
        "consumer": "representative-index section",
        "test_or_regression": "GF07_LARGE_CAP_STRONG_SMALL_CAP_WEAK",
        "missingness": "Missing representative index evidence cannot be averaged into a synthetic market view.",
    },
    "MARKET-SPEC-001": {
        "data_capability": "PARTIAL_CURRENT",
        "code_owner": "src/market_analyzer.py",
        "consumer": "breadth / limit-up-down market context",
        "test_or_regression": "GF08_SPECULATIVE_HEAT_BREADTH_WEAK",
        "missingness": "Speculative heat and broad breadth remain separate when either side is missing.",
    },
    "MARKET-FUNDING-001": {
        "data_capability": "SCHEMA_NOW_DATA_LATER",
        "code_owner": "src/market_context.py",
        "consumer": "MARKET_REGIME_BRIEF funding seam",
        "test_or_regression": "GF09_MARGIN_ACCELERATION_PRICE_INEFFICIENT",
        "missingness": "Margin/ETF-share/credit/ERP data must stay MISSING until source/time/units are proven.",
    },
    "MARKET-GLOBAL-001": {
        "data_capability": "SCHEMA_NOW_DATA_LATER__PARTIAL_REGION_CONTEXT",
        "code_owner": "src/core/market_review.py",
        "consumer": "MARKET_REGIME_BRIEF global context",
        "test_or_regression": "GF10_GLOBAL_SUPPORT_AND_RATE_FX_PRESSURE",
        "missingness": "Rates/FX/commodity links stay MISSING unless dated source evidence is present.",
    },
    "MARKET-PERM-001": {
        "data_capability": "READY_POLICY",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "canonical_decision market/sector regime",
        "test_or_regression": "tests/test_factor_decision_summary.py::test_market_regime_red_ready_is_canonical_veto_but_green_never_upgrades_wait",
        "missingness": "Missing market evidence cannot upgrade an asset action.",
    },
    "VALUATION-STOCK-001": {
        "data_capability": "CONSUMER_READY__FAIR_VALUE_RUNTIME_MISSING",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "investor_brief.valuation",
        "test_or_regression": "tests/test_research_brief_product_contract.py::test_phase_a_valuation_never_invents_reasonable_range_from_basic_pe_pb",
        "missingness": "PE/PB may render as partial context; no fair-value range is invented.",
    },
    "VALUATION-ETF-001": {
        "data_capability": "SCHEMA_READY__ETF_SPECIFIC_DATA_MISSING",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "investor_brief.asset_specific / valuation",
        "test_or_regression": "tests/test_notification.py::test_etf_valuation_label_and_explicit_short_timeframes_render_from_same_brief",
        "missingness": "Underlying valuation/premium-discount/spread/tracking remain explicit MISSING until proven.",
    },
    "DONOR-SEAM-001": {
        "data_capability": "POLICY_READY_GOLDEN_REQUIRED",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "user-facing investor brief",
        "test_or_regression": "GF23_DONOR_NAME_NOT_DATA_SOURCE",
        "missingness": "Method provenance never substitutes for an observed market-data source.",
    },
    "ETF-001": {
        "data_capability": "SHARED_MTF_READY__ETF_SPECIFIC_DATA_MISSING",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "etf_relative_strength_rotation_v1 investor brief",
        "test_or_regression": "tests/test_research_brief_product_contract.py::test_phase_a_etf_uses_etf_authority_and_does_not_fake_stock_or_etf_specific_fields",
        "missingness": "ETF-specific gaps force UNKNOWN/WAIT; stock chip semantics are not borrowed.",
    },
    "STOCK-001": {
        "data_capability": "PARTIAL_CURRENT",
        "code_owner": "src/services/market_structure_service.py",
        "consumer": "stock_trend_quality_pullback_v1 factor summary",
        "test_or_regression": "tests/test_market_structure_service.py::test_market_structure_service_recognizes_leader_only_for_matching_theme",
        "missingness": "Leader/fundamental/valuation evidence remains UNKNOWN when its source is incomplete.",
    },
    "GROUP-001": {
        "data_capability": "READY_CURRENT",
        "code_owner": "src/services/screening_service.py",
        "consumer": "AUTO stock/ETF focus and remaining groups",
        "test_or_regression": "tests/test_phase_b_groups_envelopes_contract.py::test_auto_screen_groups_have_independent_focus_and_remaining_buckets",
        "missingness": "Caps are maxima; no candidate is force-filled from missing/vetoed evidence.",
    },
    "ENVELOPE-001": {
        "data_capability": "READY_CURRENT_SKELETON",
        "code_owner": "src/services/pit_identity.py",
        "consumer": "AUTO / conditional WATCHLIST delivery envelopes",
        "test_or_regression": "tests/test_phase_b_groups_envelopes_contract.py::test_selection_identity_binds_auto_and_watchlist_envelopes",
        "missingness": "No watchlist material change means no separate full envelope; facts are not recomputed by projection.",
    },
    "HISTORY-001": {
        "data_capability": "READY_GUARD",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "historical_reference / current_probability",
        "test_or_regression": "tests/test_factor_decision_summary.py::test_summary_reuses_existing_score_without_inventing_probability_or_win_rate",
        "missingness": "Win rate/probability stay unavailable until same-strategy PIT history/calibration is proven.",
    },
    "CANON-001": {
        "data_capability": "READY_CURRENT",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "apply_canonical_decision_to_result / all downstream renderers",
        "test_or_regression": "tests/test_research_brief_product_contract.py::test_phase_a_production_canonical_scope_has_no_p0_user_language_and_is_consistent",
        "missingness": "Unknown required evidence fails closed; LLM text never becomes action authority.",
    },
    "CHANNEL-001": {
        "data_capability": "READY_CURRENT__DIRECT_FULL_COMPACT_PARITY_REGRESSION",
        "code_owner": "src/core/pipeline.py",
        "consumer": "_save_local_report / _send_notifications",
        "test_or_regression": "tests/test_notification.py::TestNotificationServiceReportGeneration::test_full_and_compact_reports_preserve_canonical_material_fact_parity / tests/test_pipeline_notification_image_routing.py::TestPipelineReportRouteFiltering::test_saved_full_report_and_email_telegram_compact_share_exact_result_set / GF14_REPORT_EMAIL_TELEGRAM_SAME_AUTHORITY",
        "missingness": "A channel may omit presentation-only detail but may not invent or contradict canonical facts.",
    },
    "MISSING-001": {
        "data_capability": "READY_GUARD",
        "code_owner": "src/services/factor_decision_summary.py",
        "consumer": "investor_brief + notification renderers",
        "test_or_regression": "tests/test_factor_decision_summary.py::test_asset_research_brief_payload_v1_missing_values_are_not_invented",
        "missingness": "MISSING/UNKNOWN stay distinct from no-event and are rendered once or omitted.",
    },
}

GF_IDS = [
    "GF01_FULL_MTF_ALIGNMENT",
    "GF02_ZERO_AXIS_BELOW_GOLDEN_CROSS_REBOUND_ONLY",
    "GF03_CONFIRMED_DIVERGENCE_WITH_HIGHER_TREND",
    "GF04_PATTERN_FORMING_CONFIRMED_FAILED",
    "GF05_LOWER_TIMEFRAME_CONFLICT",
    "GF06_SAME_SWING_NO_DOUBLE_COUNT",
    "GF07_LARGE_CAP_STRONG_SMALL_CAP_WEAK",
    "GF08_SPECULATIVE_HEAT_BREADTH_WEAK",
    "GF09_MARGIN_ACCELERATION_PRICE_INEFFICIENT",
    "GF10_GLOBAL_SUPPORT_AND_RATE_FX_PRESSURE",
    "GF11_INTRADAY_MISSING_DAILY_FIRST",
    "GF12_ETF_SPECIFIC_FIELDS",
    "GF13_WATCHLIST_QUOTA_INDEPENDENT",
    "GF14_REPORT_EMAIL_TELEGRAM_SAME_AUTHORITY",
    "GF15_NO_INTERNAL_LANGUAGE_OR_REPEATED_DISCLAIMER",
    "GF16_MARKET_BRIEF_NO_NEW_ASSET_UPGRADE",
    "GF17_EXPLICIT_TIMEFRAME_NAMING",
    "GF18_BREADTH_CONCRETE_FACTS",
    "GF19_MATERIAL_EVENT_MUST_RENDER",
    "GF20_STABLE_STATE_COMPRESS_NO_NOISE",
    "GF21_STOCK_AND_ETF_VALUATION_RENDER",
    "GF22_CONDITIONAL_WATCHLIST_ENVELOPE",
    "GF23_DONOR_NAME_NOT_DATA_SOURCE",
    "GF24_MARKET_GLOBAL_ETF_STOCK_ORDER",
    "GF25_INFORMATION_MOVED_NOT_DROPPED",
]

# These are the exact next-stage rendered-golden owners.  Declaring the owner
# here does not claim that the rendered golden already exists or passes.
GOLDEN_FIXTURE_OWNERS = {
    gf_id: f"tests/test_r004_rendered_goldens.py::test_{gf_id.lower()}"
    for gf_id in GF_IDS
}

SUPPORTING_REGRESSIONS = {
    "GF03_CONFIRMED_DIVERGENCE_WITH_HIGHER_TREND":
        "tests/test_research_brief_product_contract.py::test_phase_a_stock_human_synthesis_renders_material_events_with_exact_timeframe",
    "GF04_PATTERN_FORMING_CONFIRMED_FAILED":
        "tests/test_pattern_trigger_service.py::test_double_bottom_forming_confirmed_and_failed_reuse_price_structure_event_owner",
    "GF05_LOWER_TIMEFRAME_CONFLICT":
        "tests/test_factor_decision_summary.py::test_lower_timeframe_schema_cannot_override_higher_level_canonical_veto",
    "GF06_SAME_SWING_NO_DOUBLE_COUNT":
        "tests/test_pattern_trigger_service.py::test_same_swing_dedup_keeps_one_canonical_interpretation",
    "GF11_INTRADAY_MISSING_DAILY_FIRST":
        "tests/test_multi_timeframe_structure_service.py::test_weekly_ready_monthly_missing_and_intraday_stays_missing",
    "GF12_ETF_SPECIFIC_FIELDS":
        "tests/test_research_brief_product_contract.py::test_phase_a_etf_uses_etf_authority_and_does_not_fake_stock_or_etf_specific_fields",
    "GF13_WATCHLIST_QUOTA_INDEPENDENT":
        "tests/test_phase_b_groups_envelopes_contract.py::test_auto_screen_groups_have_independent_focus_and_remaining_buckets",
    "GF15_NO_INTERNAL_LANGUAGE_OR_REPEATED_DISCLAIMER":
        "tests/test_notification.py::test_degraded_explanation_status_is_transparent_and_does_not_duplicate_legacy_sections",
    "GF16_MARKET_BRIEF_NO_NEW_ASSET_UPGRADE":
        "tests/test_factor_decision_summary.py::test_market_regime_red_ready_is_canonical_veto_but_green_never_upgrades_wait",
    "GF17_EXPLICIT_TIMEFRAME_NAMING":
        "tests/test_notification.py::test_future_ready_mtf_and_short_term_schema_renders_without_new_authority",
    "GF19_MATERIAL_EVENT_MUST_RENDER":
        "tests/test_research_brief_product_contract.py::test_phase_a_stock_human_synthesis_renders_material_events_with_exact_timeframe",
    "GF20_STABLE_STATE_COMPRESS_NO_NOISE":
        "tests/test_research_brief_product_contract.py::test_phase_a_stable_state_does_not_emit_no_event_checklist",
    "GF21_STOCK_AND_ETF_VALUATION_RENDER":
        "tests/test_notification.py::test_etf_valuation_label_and_explicit_short_timeframes_render_from_same_brief",
    "GF22_CONDITIONAL_WATCHLIST_ENVELOPE":
        "tests/test_phase_b_groups_envelopes_contract.py::test_selection_identity_binds_auto_and_watchlist_envelopes",
    "GF24_MARKET_GLOBAL_ETF_STOCK_ORDER":
        "tests/test_r004_rendered_goldens.py::test_gf24_missing_global_preserves_legacy_two_block_projection",
}


def test_r004_baseline_identity_and_artifact_hashes_are_frozen() -> None:
    assert R004_BASELINE_ID == "STOCK_DSA_INFORMATION_DENSE_EMAIL_GOLD_MASTER_R004"
    assert R004_ACCEPTANCE_ID == (
        "STOCK_DSA_INFORMATION_DENSE_GOLD_MASTER_R004_ACCEPTANCE_20260922_R001"
    )
    assert len(R004_ARTIFACT_SHA256) == 5
    assert all(len(value) == 64 for value in R004_ARTIFACT_SHA256.values())


def test_r004_requirement_coverage_matrix_is_complete_and_source_bound() -> None:
    assert set(REQUIREMENT_BINDINGS) == EXPECTED_REQUIREMENT_IDS
    required_fields = {
        "data_capability",
        "code_owner",
        "consumer",
        "test_or_regression",
        "missingness",
    }
    for requirement_id, binding in REQUIREMENT_BINDINGS.items():
        assert set(binding) == required_fields, requirement_id
        assert all(str(value).strip() for value in binding.values()), requirement_id
        assert (ROOT / binding["code_owner"]).exists(), (
            requirement_id,
            binding["code_owner"],
        )


def test_r004_gf01_gf25_have_exact_rendered_fixture_owners() -> None:
    assert list(GOLDEN_FIXTURE_OWNERS) == GF_IDS
    assert len(GOLDEN_FIXTURE_OWNERS) == 25
    assert len(set(GOLDEN_FIXTURE_OWNERS.values())) == 25
    for gf_id, owner in GOLDEN_FIXTURE_OWNERS.items():
        expected = f"tests/test_r004_rendered_goldens.py::test_{gf_id.lower()}"
        assert owner == expected
        assert "PENDING" not in owner


def test_r004_supporting_regressions_reference_existing_test_files() -> None:
    for gf_id, owner in SUPPORTING_REGRESSIONS.items():
        assert gf_id in GOLDEN_FIXTURE_OWNERS
        path, separator, test_name = owner.partition("::")
        assert separator == "::"
        assert test_name.startswith("test_")
        assert (ROOT / path).exists(), owner
