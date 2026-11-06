from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_stock_dsa_reentry.py"
AI_ASSET_CHECK = ROOT / "scripts" / "check_ai_assets.py"


def _module():
    spec = importlib.util.spec_from_file_location("stock_dsa_reentry_verifier", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_owned_reentry_verifier_passes_without_old_chat_or_project_sources() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "PASS"
    assert payload["errors"] == []
    assert payload["facts"]["git"]["origin_normalized"] == (
        "https://github.com/leeningzzu/dsa-market-research"
    )
    assert payload["facts"]["git"]["origin"] in {
        "https://github.com/leeningzzu/dsa-market-research",
        "https://github.com/leeningzzu/dsa-market-research.git",
    }
    assert payload["facts"]["stage"] == {
        "method_profiles": 43,
        "methods_existing": 35,
        "methods_deferred": 8,
        "product_count": 13,
        "product_denominator": 72,
        "atomic_market_product_count": 2,
    }
    assert payload["facts"]["v25"] == {
        "bytes": 125919,
        "sha256": "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823",
    }
    assert payload["proof_ceiling"].startswith("REPOSITORY_CONTINUITY_ONLY")


def test_reentry_verifier_fails_closed_on_wrong_canonical_origin() -> None:
    module = _module()
    payload = module.verify(
        ROOT,
        expected_origin="https://example.invalid/not-canonical.git",
    )
    assert payload["status"] == "STOP"
    mismatch = next(row for row in payload["errors"] if row["code"] == "ORIGIN_MISMATCH")
    assert mismatch["actual_normalized"] == (
        "https://github.com/leeningzzu/dsa-market-research"
    )
    assert mismatch["expected_normalized"] == "https://example.invalid/not-canonical.git"


def test_reentry_origin_normalization_is_narrow_and_suffix_only() -> None:
    module = _module()
    canonical = "https://github.com/leeningzzu/dsa-market-research"
    assert module._normalized_repository_url(canonical) == canonical
    assert module._normalized_repository_url(canonical + ".git") == canonical
    assert module._normalized_repository_url(canonical + ".git/") == canonical
    assert module._normalized_repository_url(
        "http://github.com/leeningzzu/dsa-market-research.git"
    ) != canonical
    assert module._normalized_repository_url(
        "https://example.invalid/leeningzzu/dsa-market-research.git"
    ) != canonical


def test_require_feature_ref_accepts_exact_github_actions_pr_checkout(monkeypatch) -> None:
    module = _module()
    original_git = module._git
    head_result = original_git(ROOT, "rev-parse", "HEAD")
    assert head_result.returncode == 0
    head = str(head_result.stdout).strip()
    remote_ref_name = f"refs/remotes/origin/{module.FEATURE_BRANCH}"

    def fake_git(root, *args, binary=False):
        if args == ("rev-parse", "--verify", remote_ref_name):
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=1, stdout="", stderr="missing"
            )
        return original_git(root, *args, binary=binary)

    monkeypatch.setattr(module, "_git", fake_git)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", head)
    monkeypatch.setenv("GITHUB_HEAD_REF", module.FEATURE_BRANCH)
    monkeypatch.setenv("GITHUB_REF", "refs/pull/31/merge")
    payload = module.verify(ROOT, require_feature_ref=True)
    assert payload["status"] == "PASS", payload
    assert payload["facts"]["git"]["feature_remote_ref"] is None
    assert payload["facts"]["git"]["feature_verification"] == (
        "GITHUB_ACTIONS_PR_CHECKOUT_EXACT_EVENT_IDENTITY"
    )


def test_require_feature_ref_still_fails_without_local_ref_or_ci_identity(monkeypatch) -> None:
    module = _module()
    original_git = module._git
    remote_ref_name = f"refs/remotes/origin/{module.FEATURE_BRANCH}"

    def fake_git(root, *args, binary=False):
        if args == ("rev-parse", "--verify", remote_ref_name):
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=1, stdout="", stderr="missing"
            )
        return original_git(root, *args, binary=binary)

    monkeypatch.setattr(module, "_git", fake_git)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
    payload = module.verify(ROOT, require_feature_ref=True)
    assert payload["status"] == "STOP"
    assert any(
        row["code"] == "FEATURE_REMOTE_REF_UNAVAILABLE"
        for row in payload["errors"]
    )


def test_github_actions_wrong_head_ref_does_not_bypass_missing_feature_ref(monkeypatch) -> None:
    module = _module()
    original_git = module._git
    head = str(original_git(ROOT, "rev-parse", "HEAD").stdout).strip()
    remote_ref_name = f"refs/remotes/origin/{module.FEATURE_BRANCH}"

    def fake_git(root, *args, binary=False):
        if args == ("rev-parse", "--verify", remote_ref_name):
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=1, stdout="", stderr="missing"
            )
        return original_git(root, *args, binary=binary)

    monkeypatch.setattr(module, "_git", fake_git)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", head)
    monkeypatch.setenv("GITHUB_HEAD_REF", "feat/not-the-authorized-branch")
    payload = module.verify(ROOT, require_feature_ref=True)
    assert payload["status"] == "STOP"
    assert any(
        row["code"] == "FEATURE_REMOTE_REF_UNAVAILABLE"
        for row in payload["errors"]
    )


def test_ai_asset_checker_preserves_git_symlink_contract_on_windows_materialization() -> None:
    index = subprocess.run(
        ["git", "ls-files", "-s", "--", "CLAUDE.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert index.returncode == 0
    assert index.stdout.startswith("120000 ")

    result = subprocess.run(
        [sys.executable, str(AI_ASSET_CHECK)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[ai-assets] OK" in result.stdout
