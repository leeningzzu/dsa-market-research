# -*- coding: utf-8 -*-
"""Focused regressions for exact V2.5 original transport."""

from __future__ import annotations

import hashlib
import importlib.util
import smtplib
import sys
import tempfile
import types
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from src.services.v2_5_baseline_transport import (
    BASELINE_BYTES,
    BASELINE_PATH,
    BASELINE_SHA256,
    V25BaselineTransportError,
    load_v2_5_baseline_bytes,
    send_v2_5_baseline_email,
)


def _isolated_email_sender_module():
    """Load the real EmailSender without requiring the repo's optional deps."""

    provider = types.ModuleType("data_provider")
    provider.__path__ = []
    base = types.ModuleType("data_provider.base")
    base.normalize_stock_code = lambda value: str(value)
    config = types.ModuleType("src.config")
    config.Config = object
    formatters = types.ModuleType("src.formatters")
    formatters.strip_hidden_markdown_metadata = lambda value: value

    def no_markdown(*args, **kwargs):
        raise AssertionError("pre-rendered baseline must not enter Markdown generation")

    formatters.markdown_to_html_document = no_markdown
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "v25_baseline_sender_under_test",
        root / "src/notification_sender/email_sender.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        sys.modules,
        {
            "data_provider": provider,
            "data_provider.base": base,
            "src.config": config,
            "src.formatters": formatters,
        },
    ):
        spec.loader.exec_module(module)
    return module


class OfflineSMTP(smtplib.SMTP):
    """Real send_message implementation with network operations replaced."""

    captured: list[bytes] = []

    def __init__(self, *args, **kwargs):
        pass

    def ehlo_or_helo_if_needed(self):
        pass

    def has_extn(self, name):
        return False

    def login(self, *args):
        return 235, b"OFFLINE"

    def sendmail(self, from_addr, to_addrs, message, *args, **kwargs):
        self.__class__.captured.append(message)
        return {}

    def quit(self):
        return 221, b"OFFLINE"

    def close(self):
        pass


class TestV25BaselineTransport(unittest.TestCase):
    def test_frozen_original_identity(self):
        payload = load_v2_5_baseline_bytes()
        self.assertEqual(len(payload), BASELINE_BYTES)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), BASELINE_SHA256)

    def test_destructive_changes_are_rejected(self):
        original = BASELINE_PATH.read_bytes()
        cases: dict[str, bytes] = {
            "trim": original[:-1],
            "delete_block": original[:4096] + original[4196:],
        }

        css_marker = b"color"
        css_index = original.find(css_marker)
        self.assertGreaterEqual(css_index, 0)
        css_changed = bytearray(original)
        css_changed[css_index] = ord("C")
        cases["css_change_same_length"] = bytes(css_changed)

        cycle_marker = "月线".encode("utf-8")
        cycle_index = original.find(cycle_marker)
        self.assertGreaterEqual(cycle_index, 0)
        cycle_cleared = bytearray(original)
        start = cycle_index + len(cycle_marker)
        end = min(start + 24, len(cycle_cleared))
        cycle_cleared[start:end] = b" " * (end - start)
        cases["empty_cycle_same_length"] = bytes(cycle_cleared)

        with tempfile.TemporaryDirectory() as tmp:
            for name, payload in cases.items():
                with self.subTest(name=name):
                    candidate = Path(tmp) / f"{name}.html"
                    candidate.write_bytes(payload)
                    with self.assertRaises(V25BaselineTransportError):
                        load_v2_5_baseline_bytes(candidate)

    def test_real_smtplib_send_message_roundtrip_preserves_html_bytes(self):
        module = _isolated_email_sender_module()
        sender = module.EmailSender(
            SimpleNamespace(
                email_sender="baseline@example.invalid",
                email_password="OFFLINE_ONLY",
                email_receivers=["review@example.invalid"],
                email_sender_name="DSA offline baseline acceptance",
                stock_email_groups=[],
            )
        )
        OfflineSMTP.captured = []

        with (
            mock.patch.object(module.smtplib, "SMTP_SSL", OfflineSMTP),
            mock.patch.object(module.smtplib, "SMTP", OfflineSMTP),
            mock.patch(
                "socket.create_connection",
                side_effect=AssertionError("NETWORK_FORBIDDEN"),
            ),
        ):
            self.assertTrue(send_v2_5_baseline_email(sender))

        self.assertEqual(len(OfflineSMTP.captured), 1)
        wire = OfflineSMTP.captured[0]
        message = BytesParser(policy=policy.default).parsebytes(wire)
        html_parts = [
            part for part in message.walk()
            if part.get_content_type() == "text/html"
        ]
        self.assertEqual(len(html_parts), 1)
        decoded_html = html_parts[0].get_payload(decode=True)
        self.assertEqual(decoded_html, BASELINE_PATH.read_bytes())
        self.assertEqual(len(decoded_html), BASELINE_BYTES)
        self.assertEqual(hashlib.sha256(decoded_html).hexdigest(), BASELINE_SHA256)

    def test_transport_module_does_not_import_dynamic_renderer(self):
        import src.services.v2_5_baseline_transport as transport

        source = Path(transport.__file__).read_text(encoding="utf-8")
        self.assertNotIn("v2_5_email_contract", source)
        self.assertNotIn("render_asset_email_html", source)
        self.assertNotIn("render_market_email_html", source)


if __name__ == "__main__":
    unittest.main()
