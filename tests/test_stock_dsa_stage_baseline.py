from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from unittest import mock

from src.config import Config
from src.notification import NotificationChannel, NotificationService
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
