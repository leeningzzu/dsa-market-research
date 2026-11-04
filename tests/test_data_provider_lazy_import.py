"""Import-boundary regression: typed source checks do not initialize market vendors.

Uses fresh native Python processes. No monkeypatched providers, network or installs.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


_ROOT = Path(__file__).resolve().parents[1]


def _fresh_import(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=20,
        check=False,
    )


class TestLightweightProviderPackageImports(unittest.TestCase):
    def _require_success(self, code: str) -> None:
        result = _fresh_import(code)
        self.assertEqual(result.returncode, 0, result.stderr[-2500:])
        self.assertIn("NATIVE_IMPORT_PASS", result.stdout)

    def test_daily_data_identity_import_does_not_load_optional_vendors(self) -> None:
        self._require_success(
            "import sys; "
            "from data_provider.daily_data_identity import sha256_payload; "
            "assert len(sha256_payload({'test': 1})) == 64; "
            "assert 'data_provider.efinance_fetcher' not in sys.modules; "
            "print('NATIVE_IMPORT_PASS')"
        )

    def test_strict_admission_import_does_not_require_market_vendor_extras(self) -> None:
        self._require_success(
            "from src.services.data_object_admission import "
            "compile_b1_runtime_admission_receipts; "
            "assert callable(compile_b1_runtime_admission_receipts); "
            "print('NATIVE_IMPORT_PASS')"
        )

    def test_legacy_public_export_names_remain_discoverable(self) -> None:
        self._require_success(
            "import data_provider; "
            "assert 'DataFetcherManager' in data_provider.__all__; "
            "assert 'EfinanceFetcher' in data_provider.__all__; "
            "assert 'data_provider.efinance_fetcher' not in __import__('sys').modules; "
            "from data_provider import is_us_index_code; "
            "assert callable(is_us_index_code); "
            "print('NATIVE_IMPORT_PASS')"
        )

    def test_manager_export_keeps_legacy_import_contract(self) -> None:
        self._require_success(
            "from data_provider import DataFetcherManager; "
            "assert DataFetcherManager.__name__ == 'DataFetcherManager'; "
            "print('NATIVE_IMPORT_PASS')"
        )

    def test_lazy_map_covers_all_public_symbols(self) -> None:
        self._require_success(
            "import data_provider; "
            "assert set(data_provider.__all__) == set(data_provider._EXPORT_MODULES); "
            "print('NATIVE_IMPORT_PASS')"
        )

    def test_analyzer_import_does_not_require_json_repair_until_json_repair_is_used(self) -> None:
        self._require_success(
            "import sys; import src.analyzer; "
            "assert 'json_repair' not in sys.modules; "
            "print('NATIVE_IMPORT_PASS')"
        )

    def test_notification_import_does_not_require_markdown_renderer(self) -> None:
        self._require_success(
            "import sys; import src.notification; "
            "assert 'markdown2' not in sys.modules; "
            "print('NATIVE_IMPORT_PASS')"
        )

    def test_pipeline_import_does_not_require_newspaper_until_article_fetch(self) -> None:
        self._require_success(
            "import sys; import src.core.pipeline; "
            "assert 'newspaper' not in sys.modules; "
            "print('NATIVE_IMPORT_PASS')"
        )

    def test_hk_classifier_does_not_load_akshare_provider(self) -> None:
        self._require_success(
            "import sys; from data_provider import is_hk_stock_code; "
            "assert is_hk_stock_code('00700'); "
            "assert is_hk_stock_code('HK01810'); "
            "assert not is_hk_stock_code('600519'); "
            "assert 'data_provider.akshare_fetcher' not in sys.modules; "
            "print('NATIVE_IMPORT_PASS')"
        )

    def test_missing_export_not_silently_resolved(self) -> None:
        self._require_success(
            "import data_provider; "
            "assert not hasattr(data_provider, 'UnknownNonexistentVendor'); "
            "print('NATIVE_IMPORT_PASS')"
        )


if __name__ == "__main__":
    unittest.main()
