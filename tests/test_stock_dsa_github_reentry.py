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
    assert payload["facts"]["git"]["origin"] == (
        "https://github.com/leeningzzu/dsa-market-research.git"
    )
    assert payload["facts"]["stage"] == {
        "method_profiles": 43,
        "methods_existing": 35,
        "methods_deferred": 8,
        "product_count": 13,
        "product_denominator": 72,
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
    assert mismatch["actual"] == "https://github.com/leeningzzu/dsa-market-research.git"
    assert mismatch["expected"] == "https://example.invalid/not-canonical.git"


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
