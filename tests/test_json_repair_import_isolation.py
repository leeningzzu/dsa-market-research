"""Test-module isolation: optional JSON repair must not be masked by collection.

A canary importable Python module is intentionally *not* imported before the
legacy fetcher test module. The process must find it after collection, rather
than seeing a test-created MagicMock occupying sys.modules. No parsing or
business/Product logic uses the canary.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("collector", [
    "tests.test_fetcher_source_optimization",
    "tests.test_hk_realtime_routing",
    "tests.test_stock_code_bse",
])
def test_fetcher_test_module_never_shadows_available_json_repair(collector: str) -> None:
    with TemporaryDirectory(prefix="dsa-json-repair-test-isolation-") as directory:
        path = Path(directory) / "json_repair.py"
        path.write_text("TEST_IMPORT_MARKER = 'AVAILABLE_NOT_YET_IMPORTED'\n", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join((directory, str(ROOT), env.get("PYTHONPATH", "")))
        env["PYTHONUTF8"] = "1"
        env["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"
        code = (
            "import importlib; "
            f"importlib.import_module({collector!r}); "
            "import json_repair; "
            "assert getattr(json_repair, 'TEST_IMPORT_MARKER', None) "
            "== 'AVAILABLE_NOT_YET_IMPORTED', type(json_repair); "
            "print('JSON_REPAIR_TEST_ISOLATION_PASS')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=35,
            check=False,
        )
        assert result.returncode == 0, result.stderr[-2000:] or result.stdout[-2000:]
        assert "JSON_REPAIR_TEST_ISOLATION_PASS" in result.stdout
