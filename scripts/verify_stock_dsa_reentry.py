#!/usr/bin/env python3
"""Deterministically verify the repository-owned STOCK_DSA reentry contract.

The verifier is intentionally offline.  It proves repository continuity facts,
not provider data rights, current Product readiness, CI success, or model state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

EXPECTED_ORIGIN = "https://github.com/leeningzzu/dsa-market-research.git"
FEATURE_BRANCH = "feat/stock-dsa-m2-source-gates-v1"
V25_RELATIVE = "templates/v2_5/STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html"
V25_BYTES = 125919
V25_SHA256 = "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823"
MACHINE_GRAPH_SHA256 = "b4f69f76807ac44bc0fea1a03f9aa38484b6083ca574a3699220b0564ac55aed"
REQUIRED_PATHS = (
    "AGENTS.md",
    "docs/stock-dsa-github-reentry.md",
    "docs/stock-dsa-roadmap.md",
    "docs/stock-dsa-stage-baseline-v1.json",
    "docs/CHANGELOG.md",
    "scripts/verify_stock_dsa_reentry.py",
    "src/services/evidence_traceability_registry.py",
    "src/services/extended_indicator_service.py",
    "tests/test_extended_indicator_service.py",
    "data_provider/sse_index_current_source.py",
    "tests/test_sse_index_current_source.py",
    V25_RELATIVE,
)


def _git(root: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
        check=False,
    )


def _nested(document: dict[str, Any], *keys: str) -> Any:
    value: Any = document
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _normalized_repository_url(value: object) -> str:
    """Normalize only the harmless GitHub HTTPS spelling difference."""
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() == "github.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.query
        and not parsed.fragment
    ):
        path = parsed.path.rstrip("/")
        if path.lower().endswith(".git"):
            path = path[:-4]
        return f"https://github.com{path}"
    return text.rstrip("/")


def _github_actions_feature_checkout(head: str | None) -> dict[str, str] | None:
    """Return exact PR-checkout identity when no remote-tracking ref exists."""
    if str(os.environ.get("GITHUB_ACTIONS") or "").lower() != "true":
        return None
    github_sha = str(os.environ.get("GITHUB_SHA") or "").strip().lower()
    github_head_ref = str(os.environ.get("GITHUB_HEAD_REF") or "").strip()
    github_ref = str(os.environ.get("GITHUB_REF") or "").strip()
    normalized_head = str(head or "").strip().lower()
    if (
        len(github_sha) != 40
        or any(char not in "0123456789abcdef" for char in github_sha)
        or github_sha != normalized_head
        or github_head_ref != FEATURE_BRANCH
    ):
        return None
    return {
        "github_sha": github_sha,
        "github_head_ref": github_head_ref,
        "github_ref": github_ref,
    }


def verify(
    root: Path,
    *,
    expected_origin: str = EXPECTED_ORIGIN,
    require_clean: bool = False,
    require_feature_ref: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    facts: dict[str, Any] = {"root": str(root)}

    def fail(code: str, **details: Any) -> None:
        errors.append({"code": code, **details})

    def warn(code: str, **details: Any) -> None:
        warnings.append({"code": code, **details})

    missing = [path for path in REQUIRED_PATHS if not (root / path).is_file()]
    if missing:
        fail("REQUIRED_PATH_MISSING", paths=missing)

    baseline_path = root / "docs/stock-dsa-stage-baseline-v1.json"
    baseline: dict[str, Any] = {}
    if baseline_path.is_file():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            fail("STAGE_BASELINE_INVALID", error=str(exc))

    if baseline:
        checks = {
            "schema_version": (baseline.get("schema_version"), "stock-dsa-stage-baseline-v1"),
            "production_shell": (baseline.get("production_shell"), "DSA/daily_stock_analysis"),
            "machine_graph_sha256": (
                _nested(baseline, "immutable_contracts", "accepted_machine_graph_sha256"),
                MACHINE_GRAPH_SHA256,
            ),
            "v25_bytes": (_nested(baseline, "immutable_contracts", "v2_5_baseline_bytes"), V25_BYTES),
            "v25_sha256": (
                _nested(baseline, "immutable_contracts", "v2_5_baseline_sha256"),
                V25_SHA256,
            ),
            "method_profiles": (_nested(baseline, "immutable_contracts", "method_profiles"), 43),
            "methods_existing": (
                _nested(baseline, "immutable_contracts", "methods_existing_reused"), 35,
            ),
            "methods_deferred": (
                _nested(baseline, "immutable_contracts", "methods_deferred_with_owner"), 8,
            ),
            "method_timeframe_targets": (
                _nested(
                    baseline, "current_runtime_checkpoint", "seven_timeframe_eight_family",
                    "technical_method_timeframe_targets",
                ),
                189,
            ),
            "source_pair": (
                _nested(baseline, "current_runtime_checkpoint", "source_pair"),
                "01/05 v3.556 PHYSICAL_EXACT8_PASS",
            ),
            "product_count": (
                _nested(baseline, "current_runtime_checkpoint", "product_state", "latest_multi_object_operation_product_count"),
                13,
            ),
            "product_denominator": (
                _nested(baseline, "current_runtime_checkpoint", "product_state", "denominator"),
                72,
            ),
            "market_atomic_product_count": (
                _nested(baseline, "github_reentry_contract", "current_product_projection", "market_atomic_product_count"),
                1,
            ),
            "market_index_atomic_state": (
                _nested(baseline, "current_market_index_atomic_operation", "state"),
                "ACTUAL_CURRENT_OBSERVATION_PRODUCT_ADMITTED",
            ),
            "reentry_doc": (
                _nested(baseline, "continuity_entrypoints", "cross_account_reentry"),
                "docs/stock-dsa-github-reentry.md",
            ),
            "reentry_verifier": (
                _nested(baseline, "continuity_entrypoints", "deterministic_reentry_verifier"),
                "scripts/verify_stock_dsa_reentry.py",
            ),
        }
        mismatches = {
            name: {"actual": actual, "expected": expected}
            for name, (actual, expected) in checks.items()
            if actual != expected
        }
        if mismatches:
            fail("STAGE_CONTRACT_MISMATCH", fields=mismatches)
        facts["stage"] = {
            "method_profiles": _nested(baseline, "immutable_contracts", "method_profiles"),
            "methods_existing": _nested(baseline, "immutable_contracts", "methods_existing_reused"),
            "methods_deferred": _nested(baseline, "immutable_contracts", "methods_deferred_with_owner"),
            "product_count": _nested(
                baseline, "current_runtime_checkpoint", "product_state", "latest_multi_object_operation_product_count"
            ),
            "product_denominator": _nested(
                baseline, "current_runtime_checkpoint", "product_state", "denominator"
            ),
            "atomic_market_product_count": _nested(
                baseline, "current_runtime_checkpoint", "product_state", "atomic_market_product_count"
            ),
        }

    roadmap_path = root / "docs/stock-dsa-roadmap.md"
    if roadmap_path.is_file():
        roadmap = roadmap_path.read_text(encoding="utf-8")
        required_markers = (
            "CURRENT_MASTER_CONTROL_START",
            "STOCK_DSA_MASTER_ROUTE_20261106_M_INDEX_CURRENT_LOCAL_REENTRY_V2",
            "方法 Profile：**35/43（81.4%）**",
            "技术数值叶：**35/35（100%）**算法准入",
            "最近多对象 CURRENT operation：**13/72（18.1%）**",
            "独立 Market atomic：**1/18**",
            "docs/stock-dsa-github-reentry.md",
        )
        missing_markers = [marker for marker in required_markers if marker not in roadmap]
        if missing_markers:
            fail("ROADMAP_MARKER_MISSING", markers=missing_markers)

    agents_path = root / "AGENTS.md"
    if agents_path.is_file():
        agents = agents_path.read_text(encoding="utf-8")
        for marker in (
            "docs/stock-dsa-github-reentry.md",
            "python scripts/verify_stock_dsa_reentry.py --json",
        ):
            if marker not in agents:
                fail("AGENTS_REENTRY_MARKER_MISSING", marker=marker)

    v25 = _git(root, "show", f"HEAD:{V25_RELATIVE}", binary=True)
    v25_bytes: bytes | None = v25.stdout if v25.returncode == 0 else None
    if v25_bytes is None:
        path = root / V25_RELATIVE
        if path.is_file():
            v25_bytes = path.read_bytes()
            warn("V25_GIT_BLOB_UNAVAILABLE_USED_WORKTREE", stderr=v25.stderr.decode("utf-8", "replace"))
        else:
            fail("V25_MISSING")
    if v25_bytes is not None:
        digest = hashlib.sha256(v25_bytes).hexdigest()
        facts["v25"] = {"bytes": len(v25_bytes), "sha256": digest}
        if len(v25_bytes) != V25_BYTES or digest != V25_SHA256:
            fail(
                "V25_IMMUTABLE_MISMATCH",
                actual_bytes=len(v25_bytes),
                actual_sha256=digest,
                expected_bytes=V25_BYTES,
                expected_sha256=V25_SHA256,
            )

    origin_result = _git(root, "remote", "get-url", "origin")
    expected_origin_normalized = _normalized_repository_url(expected_origin)
    if origin_result.returncode != 0:
        fail("ORIGIN_UNAVAILABLE", stderr=str(origin_result.stderr).strip())
        origin = None
        origin_normalized = None
    else:
        origin = str(origin_result.stdout).strip()
        origin_normalized = _normalized_repository_url(origin)
        if origin_normalized != expected_origin_normalized:
            fail(
                "ORIGIN_MISMATCH",
                actual=origin,
                expected=expected_origin,
                actual_normalized=origin_normalized,
                expected_normalized=expected_origin_normalized,
            )

    head_result = _git(root, "rev-parse", "HEAD")
    head = str(head_result.stdout).strip() if head_result.returncode == 0 else None
    if not head:
        fail("HEAD_UNAVAILABLE", stderr=str(head_result.stderr).strip())

    branch_result = _git(root, "branch", "--show-current")
    branch = str(branch_result.stdout).strip() if branch_result.returncode == 0 else ""
    status_result = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    dirty_paths = [line for line in str(status_result.stdout).splitlines() if line.strip()]
    if status_result.returncode != 0:
        fail("WORKTREE_STATUS_UNAVAILABLE", stderr=str(status_result.stderr).strip())
    elif dirty_paths:
        if require_clean:
            fail("WORKTREE_NOT_CLEAN", entries=dirty_paths)
        else:
            warn("WORKTREE_NOT_CLEAN", entries=dirty_paths)

    remote_ref_name = f"refs/remotes/origin/{FEATURE_BRANCH}"
    feature_result = _git(root, "rev-parse", "--verify", remote_ref_name)
    feature_ref = str(feature_result.stdout).strip() if feature_result.returncode == 0 else None
    github_actions_checkout = _github_actions_feature_checkout(head)
    feature_verification = None
    if require_feature_ref:
        if feature_ref and head == feature_ref:
            feature_verification = "REMOTE_TRACKING_REF_EXACT"
        elif github_actions_checkout is not None:
            feature_verification = "GITHUB_ACTIONS_PR_CHECKOUT_EXACT_EVENT_IDENTITY"
        elif not feature_ref:
            fail("FEATURE_REMOTE_REF_UNAVAILABLE", ref=remote_ref_name)
        else:
            fail("HEAD_FEATURE_REF_MISMATCH", head=head, feature_ref=feature_ref)
    elif feature_ref and head == feature_ref:
        feature_verification = "REMOTE_TRACKING_REF_EXACT"
    elif github_actions_checkout is not None:
        feature_verification = "GITHUB_ACTIONS_PR_CHECKOUT_EXACT_EVENT_IDENTITY"
    elif not feature_ref:
        warn("FEATURE_REMOTE_REF_UNAVAILABLE", ref=remote_ref_name)
    else:
        warn("HEAD_FEATURE_REF_MISMATCH", head=head, feature_ref=feature_ref)

    facts["git"] = {
        "origin": origin,
        "origin_normalized": origin_normalized,
        "expected_origin_normalized": expected_origin_normalized,
        "head": head,
        "branch": branch or "DETACHED",
        "feature_remote_ref": feature_ref,
        "feature_verification": feature_verification,
        "github_actions_checkout": github_actions_checkout,
        "dirty_entry_count": len(dirty_paths),
    }
    return {
        "schema_version": "stock-dsa-github-reentry-verification-v1",
        "status": "PASS" if not errors else "STOP",
        "errors": errors,
        "warnings": warnings,
        "facts": facts,
        "proof_ceiling": (
            "REPOSITORY_CONTINUITY_ONLY_NOT_PROVIDER_RIGHTS_PRODUCT_READY_CI_OR_PROMOTION"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expected-origin", default=EXPECTED_ORIGIN)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-feature-ref", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = verify(
        args.root,
        expected_origin=args.expected_origin,
        require_clean=args.require_clean,
        require_feature_ref=args.require_feature_ref,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"STOCK_DSA_REENTRY={result['status']}")
        for row in result["errors"]:
            print("ERROR", json.dumps(row, ensure_ascii=False, sort_keys=True))
        for row in result["warnings"]:
            print("WARNING", json.dumps(row, ensure_ascii=False, sort_keys=True))
        print(json.dumps(result["facts"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
