# -*- coding: utf-8 -*-
"""Regression tests for TushareFetcher HTTP client initialization."""

import importlib.util
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    json_repair_available = importlib.util.find_spec("json_repair") is not None
except ValueError:
    json_repair_available = "json_repair" in sys.modules

if not json_repair_available and "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.tushare_fetcher import (
    TushareFetcher,
    _TushareHttpClient,
    _resolve_tushare_http_url,
)


class TestTushareHttpClient(unittest.TestCase):
    """Ensure the lightweight HTTP client preserves Tushare Pro request semantics."""

    def test_query_posts_to_official_pro_endpoint(self) -> None:
        client = _TushareHttpClient(token="demo-token", timeout=15, api_url="https://api.tushare.pro")
        response = MagicMock(
            status_code=200,
            text=json.dumps(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code", "close"],
                        "items": [["600519.SH", 1688.0]],
                    },
                }
            ),
        )

        with patch("data_provider.tushare_fetcher.requests.post", return_value=response) as post_mock:
            df = client.daily(ts_code="600519.SH", start_date="20260320", end_date="20260325")

        post_mock.assert_called_once_with(
            "https://api.tushare.pro",
            json={
                "api_name": "daily",
                "token": "demo-token",
                "params": {
                    "ts_code": "600519.SH",
                    "start_date": "20260320",
                    "end_date": "20260325",
                },
                "fields": "",
            },
            timeout=15,
            allow_redirects=False,
        )
        self.assertEqual(df.to_dict(orient="records"), [{"ts_code": "600519.SH", "close": 1688.0}])


class TestTushareFetcherInit(unittest.TestCase):
    """Ensure fetcher initialization no longer depends on the tushare SDK package."""

    def test_init_builds_client_only_with_explicit_secure_endpoint(self) -> None:
        config = SimpleNamespace(tushare_token="demo-token")
        with (
            patch("data_provider.tushare_fetcher.get_config", return_value=config),
            patch.dict("os.environ", {"TUSHARE_HTTP_URL": "https://api.tushare.pro"}),
        ):
            fetcher = TushareFetcher()
        self.assertIsInstance(fetcher._api, _TushareHttpClient)
        self.assertTrue(fetcher.is_available())
        self.assertEqual(fetcher.priority, -1)

class TestResolveTushareHttpUrl(unittest.TestCase):
    """``TUSHARE_HTTP_URL`` 环境变量解析与校验。"""

    def test_unset_returns_none(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("TUSHARE_HTTP_URL", None)
            self.assertIsNone(_resolve_tushare_http_url())

    def test_empty_or_whitespace_returns_none(self) -> None:
        with patch.dict("os.environ", {"TUSHARE_HTTP_URL": "   "}):
            self.assertIsNone(_resolve_tushare_http_url())

    def test_plaintext_url_rejected(self) -> None:
        with patch.dict("os.environ", {"TUSHARE_HTTP_URL": "http://gw.example.com/tushare"}):
            with self.assertRaisesRegex(ValueError, "TUSHARE_TLS_OFFICIAL_ENDPOINT_REQUIRED"):
                _resolve_tushare_http_url()

    def test_official_https_url_returned(self) -> None:
        with patch.dict("os.environ", {"TUSHARE_HTTP_URL": " https://api.tushare.pro/ "}):
            self.assertEqual(_resolve_tushare_http_url(), "https://api.tushare.pro")
    def test_missing_schema_raises_value_error(self) -> None:
        # 防止有人误填纯主机名（如 'api.tushare.pro'）后被 requests 当成相对路径
        with patch.dict("os.environ", {"TUSHARE_HTTP_URL": "gw.example.com"}):
            with self.assertRaises(ValueError):
                _resolve_tushare_http_url()


class TestTushareFetcherCustomHttpUrl(unittest.TestCase):
    """``TUSHARE_HTTP_URL`` 真正打通到 HTTP client 的接入地址。"""

    def test_fetcher_uses_only_official_https_url(self) -> None:
        config = SimpleNamespace(tushare_token="demo-token")
        with (
            patch("data_provider.tushare_fetcher.get_config", return_value=config),
            patch.dict("os.environ", {"TUSHARE_HTTP_URL": "https://api.tushare.pro"}),
        ):
            fetcher = TushareFetcher()
        self.assertIsInstance(fetcher._api, _TushareHttpClient)
        self.assertEqual(fetcher._api._api_url, "https://api.tushare.pro")

    def test_fetcher_fails_closed_when_env_empty(self) -> None:
        config = SimpleNamespace(tushare_token="demo-token")
        env = {k: v for k, v in __import__("os").environ.items() if k != "TUSHARE_HTTP_URL"}
        with (
            patch("data_provider.tushare_fetcher.get_config", return_value=config),
            patch.dict("os.environ", env, clear=True),
        ):
            fetcher = TushareFetcher()
        self.assertIsNone(fetcher._api)
        self.assertFalse(fetcher.is_available())
        self.assertEqual(fetcher.priority, 2)
    def test_query_posts_to_custom_endpoint(self) -> None:
        """端到端确保自定义 url 真正驱动 requests.post 的目标地址。"""
        config = SimpleNamespace(tushare_token="demo-token")

        with patch("data_provider.tushare_fetcher.get_config", return_value=config), \
                patch.dict("os.environ", {"TUSHARE_HTTP_URL": "https://api.tushare.pro"}):
            fetcher = TushareFetcher()

        response = MagicMock(
            status_code=200,
            text=json.dumps(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code", "close"],
                        "items": [["600519.SH", 1688.0]],
                    },
                }
            ),
        )

        with patch("data_provider.tushare_fetcher.requests.post", return_value=response) as post_mock:
            fetcher._api.daily(ts_code="600519.SH", start_date="20260320", end_date="20260325")

        post_mock.assert_called_once_with(
            "https://api.tushare.pro",
            json={
                "api_name": "daily",
                "token": "demo-token",
                "params": {
                    "ts_code": "600519.SH",
                    "start_date": "20260320",
                    "end_date": "20260325",
                },
                "fields": "",
            },
            timeout=30,
            allow_redirects=False,
        )



class TestTushareTokenTransportSecurity(unittest.TestCase):
    """Independent security negatives: no Token may traverse HTTP or an untrusted mirror."""

    def test_default_and_plaintext_and_mirror_fail_before_post(self) -> None:
        endpoints = (None, "http://api.tushare.pro", "http://gw.example.com/tushare",
                     "https://gw.example.com/tushare", "https://api.tushare.pro.evil.example")
        with patch("data_provider.tushare_fetcher.requests.post") as post:
            for endpoint in endpoints:
                with self.subTest(endpoint=endpoint):
                    with self.assertRaises(ValueError):
                        if endpoint is None:
                            _TushareHttpClient(token="test-only-token")
                        else:
                            _TushareHttpClient(token="test-only-token", api_url=endpoint)
            post.assert_not_called()

    def test_https_official_mock_never_follows_redirect(self) -> None:
        client = _TushareHttpClient(token="test-only-token", api_url="https://api.tushare.pro")
        response = MagicMock(status_code=302, headers={"Location": "http://evil.example"})
        with patch("data_provider.tushare_fetcher.requests.post", return_value=response) as post:
            with self.assertRaises(Exception):
                client.daily(ts_code="600519.SH")
        self.assertEqual(post.call_args.kwargs.get("allow_redirects"), False)

    def test_configured_token_without_verified_endpoint_is_unavailable(self) -> None:
        import os
        config = SimpleNamespace(tushare_token="test-only-token")
        env = {k: v for k, v in os.environ.items() if k != "TUSHARE_HTTP_URL"}
        with (patch("data_provider.tushare_fetcher.get_config", return_value=config),
              patch.dict("os.environ", env, clear=True),
              patch("data_provider.tushare_fetcher.requests.post") as post):            fetcher = TushareFetcher()
        self.assertIsNone(fetcher._api)
        self.assertFalse(fetcher.is_available())
        self.assertEqual(fetcher.priority, 2)
        post.assert_not_called()

if __name__ == "__main__":
    unittest.main()
