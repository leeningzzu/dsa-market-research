# -*- coding: utf-8 -*-
"""
Regression tests for pipeline email image routing with stock email groups.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.core.pipeline import StockAnalysisPipeline, NotificationChannel
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    build_run_diagnostic_summary,
    current_diagnostic_snapshot,
    reset_run_diagnostic_context,
)
from src.enums import ReportType


class _FakeNotifier:
    def __init__(self):
        self._markdown_to_image_channels = {"email"}
        self._markdown_to_image_max_chars = 15000
        self.generate_dashboard_report = MagicMock(side_effect=self._generate_dashboard_report)
        self.generate_brief_report = MagicMock(side_effect=self._generate_brief_report)
        self.save_report_to_file = MagicMock(return_value="/tmp/report.md")
        self.is_available = MagicMock(return_value=True)
        self.get_available_channels = MagicMock(return_value=[NotificationChannel.EMAIL])
        self.get_channels_for_route = MagicMock(
            side_effect=lambda route_type, channels=None: list(
                channels if channels is not None else self.get_available_channels()
            )
        )
        self.send_to_context = MagicMock(return_value=False)
        self._should_use_image_for_channel = MagicMock(
            side_effect=lambda channel, image_bytes: (
                channel.value in self._markdown_to_image_channels and image_bytes is not None
            )
        )
        self._send_email_with_inline_image = MagicMock(return_value=True)
        self.send_to_email = MagicMock(return_value=True)

    @staticmethod
    def _generate_dashboard_report(results):
        return "report:" + ",".join(r.code for r in results)

    @staticmethod
    def _generate_brief_report(results):
        return "brief:" + ",".join(r.code for r in results)


class TestPipelineEmailGroupImageRouting(unittest.TestCase):
    def _build_pipeline(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeNotifier()
        pipeline.config = SimpleNamespace(
            stock_email_groups=[
                (["000001"], ["group@example.com"]),
            ]
        )
        return pipeline

    def _make_results(self):
        return [
            SimpleNamespace(code="000001"),
            SimpleNamespace(code="600519"),
        ]

    def test_formal_full_report_rejects_missing_email_before_context_or_optional_channels(self):
        """Pipeline must not report Telegram/chat-only delivery as Email success."""
        pipeline = self._build_pipeline()
        pipeline.notifier.get_available_channels.return_value = [
            NotificationChannel.TELEGRAM, NotificationChannel.WECHAT,
        ]
        pipeline.notifier.get_channels_for_route.side_effect = (
            lambda route_type, channels=None: list(channels or [])
        )
        pipeline.notifier.send_to_context = MagicMock(return_value=True)
        pipeline.notifier.send_to_telegram = MagicMock(return_value=True)
        pipeline._build_notification_run_snapshot = MagicMock(return_value={})
        pipeline._refresh_saved_diagnostic_snapshot = MagicMock()

        with patch("src.core.pipeline.record_notification_run") as record:
            pipeline._send_notifications(self._make_results(), ReportType.FULL)

        pipeline.notifier.send_to_context.assert_not_called()
        pipeline.notifier.send_to_telegram.assert_not_called()
        pipeline.notifier.send_to_email.assert_not_called()
        assert any(
            c.kwargs.get("channel") == "report"
            and c.kwargs.get("status") == "failed"
            and isinstance(c.kwargs.get("error_message"), ValueError)
            for c in record.call_args_list
        )


    def test_formal_report_email_group_failure_not_masked_by_telegram_success(self):
        """Two failed Email groups keep the report failed even if Telegram works."""
        pipeline = self._build_pipeline()
        notifier = pipeline.notifier
        notifier.get_available_channels.return_value = [
            NotificationChannel.EMAIL, NotificationChannel.TELEGRAM,
        ]
        notifier.send_to_email.return_value = False
        notifier.send_to_telegram = MagicMock(return_value=True)
        notifier.send_to_context = MagicMock(return_value=True)
        notifier.should_broadcast_static_channels = MagicMock(return_value=False)
        notifier.evaluate_noise_control = MagicMock(return_value=SimpleNamespace(
            should_send=True, message="allowed"
        ))
        notifier.record_noise_control = MagicMock()
        notifier.release_noise_control = MagicMock()
        pipeline._build_notification_run_snapshot = MagicMock(return_value={})
        pipeline._refresh_saved_diagnostic_snapshot = MagicMock()

        with patch("src.core.pipeline.record_notification_run") as record:
            pipeline._send_notifications(self._make_results(), ReportType.FULL)
        assert notifier.send_to_email.call_count == 2
        notifier.send_to_telegram.assert_called_once()
        notifier.send_to_context.assert_not_called()
        notifier.record_noise_control.assert_not_called()
        notifier.release_noise_control.assert_called_once()
        results = [(call.kwargs.get("channel"), call.kwargs.get("success"))
                   for call in record.call_args_list]
        assert sum(1 for name, ok in results if str(name).startswith("email") and ok is False) == 2
        assert ("telegram", True) in results


    @patch("src.md2img.markdown_to_image", return_value=b"png-bytes")
    def test_send_notifications_email_group_uses_inline_image_when_enabled(self, _mock_md2img):
        pipeline = self._build_pipeline()
        results = self._make_results()

        pipeline._send_notifications(results, ReportType.SIMPLE)

        self.assertEqual(pipeline.notifier._send_email_with_inline_image.call_count, 2)
        pipeline.notifier.send_to_email.assert_not_called()
        called_receivers = [kwargs.get("receivers") for _, kwargs in pipeline.notifier._send_email_with_inline_image.call_args_list]
        self.assertIn(["group@example.com"], called_receivers)
        self.assertIn(None, called_receivers)

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_send_notifications_email_group_falls_back_to_text_when_image_unavailable(self, _mock_md2img):
        pipeline = self._build_pipeline()
        results = self._make_results()

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier._send_email_with_inline_image.assert_not_called()
        self.assertEqual(pipeline.notifier.send_to_email.call_count, 2)
        called_receivers = [kwargs.get("receivers") for _, kwargs in pipeline.notifier.send_to_email.call_args_list]
        self.assertIn(["group@example.com"], called_receivers)
        self.assertIn(None, called_receivers)

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_send_notifications_email_text_fallback_strips_hidden_market_metadata(self, _mock_md2img):
        pipeline = self._build_pipeline()
        pipeline.notifier.generate_brief_report = MagicMock(
            return_value="[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody"
        )
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_email.assert_called_once_with(
            "# 🎯 Market Review\n\nBody",
            receivers=["group@example.com"],
        )


    def test_full_email_group_does_not_drop_source_counterevidence_or_risk(self):
        pipeline = self._build_pipeline()
        results = self._make_results()
        with patch("src.md2img.markdown_to_image", return_value=None):
            pipeline._send_notifications(results, ReportType.FULL)
        payloads = [call.args[0] for call in pipeline.notifier.send_to_email.call_args_list]
        self.assertCountEqual(payloads, ["report:000001", "report:600519"])
        pipeline.notifier._send_email_with_inline_image.assert_not_called()

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_send_notifications_email_group_failure_does_not_skip_later_group(self, _mock_md2img):
        pipeline = self._build_pipeline()
        pipeline.notifier.send_to_email.side_effect = [RuntimeError("group failed"), True]
        results = self._make_results()

        pipeline._send_notifications(results, ReportType.SIMPLE)

        self.assertEqual(pipeline.notifier.send_to_email.call_count, 2)
        called_receivers = [kwargs.get("receivers") for _, kwargs in pipeline.notifier.send_to_email.call_args_list]
        self.assertIn(["group@example.com"], called_receivers)
        self.assertIn(None, called_receivers)

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_empty_email_group_compact_projection_does_not_fallback_to_full_report(self, _mock_md2img):
        pipeline = self._build_pipeline()
        results = self._make_results()
        pipeline.notifier.generate_brief_report = MagicMock(
            side_effect=["brief:000001,600519", "", "brief:600519"]
        )

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.generate_dashboard_report.assert_called_once_with(results)
        pipeline.notifier.send_to_email.assert_called_once_with(
            "brief:600519", receivers=None
        )

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_email_group_diagnostics_only_patch_group_results(self, _mock_md2img):
        pipeline = self._build_pipeline()
        pipeline.save_context_snapshot = True
        pipeline.db = MagicMock()
        pipeline.notifier.send_to_email.side_effect = [RuntimeError("group failed"), True]
        results = self._make_results()
        results[0].query_id = "query-group"
        results[1].query_id = "query-default"

        pipeline._send_notifications(results, ReportType.SIMPLE)

        calls = pipeline.db.update_analysis_history_diagnostics.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["query_id"], "query-group")
        self.assertEqual(calls[0].kwargs["code"], "000001")
        self.assertEqual(calls[0].kwargs["notification_runs"][0]["status"], "failed")
        self.assertEqual(calls[0].kwargs["notification_runs"][0]["channel"], "email:group@example.com")
        self.assertEqual(calls[1].kwargs["query_id"], "query-default")
        self.assertEqual(calls[1].kwargs["code"], "600519")
        self.assertEqual(calls[1].kwargs["notification_runs"][0]["status"], "success")
        self.assertEqual(calls[1].kwargs["notification_runs"][0]["channel"], "email:default")


class _FakeWechatNotifier:
    def __init__(self):
        self._markdown_to_image_channels = {"wechat"}
        self._markdown_to_image_max_chars = 15000
        self.generate_dashboard_report = MagicMock(return_value="dashboard-report")
        self.generate_wechat_dashboard = MagicMock(return_value="dashboard-report")
        self.save_report_to_file = MagicMock(return_value="/tmp/report.md")
        self.is_available = MagicMock(return_value=True)
        self.get_available_channels = MagicMock(return_value=[NotificationChannel.WECHAT])
        self.get_channels_for_route = MagicMock(
            side_effect=lambda route_type, channels=None: list(
                channels if channels is not None else self.get_available_channels()
            )
        )
        self.send_to_context = MagicMock(return_value=False)
        self.generate_brief_report = MagicMock(return_value="brief-report")
        self._should_use_image_for_channel = MagicMock(
            side_effect=lambda channel, image_bytes: (
                channel.value in self._markdown_to_image_channels and image_bytes is not None
            )
        )
        self._send_wechat_image = MagicMock(return_value=True)
        self.send_to_wechat = MagicMock(return_value=True)


class TestPipelineWechatOnlyImageRouting(unittest.TestCase):
    def test_wechat_only_cannot_replace_required_email_with_image(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeWechatNotifier()
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"wechat-image") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_not_called()
        pipeline.notifier._send_wechat_image.assert_not_called()
        pipeline.notifier.send_to_wechat.assert_not_called()
        pipeline.notifier.send_to_context.assert_not_called()

    def test_wechat_only_cannot_replace_required_email_with_text(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeWechatNotifier()
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=None), patch(
            "src.core.pipeline.get_config", return_value=SimpleNamespace(md2img_engine="wkhtmltoimage")
        ), patch("src.core.pipeline.logger.warning") as mock_warning:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier._send_wechat_image.assert_not_called()
        pipeline.notifier.send_to_wechat.assert_not_called()
        pipeline.notifier.send_to_context.assert_not_called()
        mock_warning.assert_not_called()


class _FakeRoutedNotifier:
    def __init__(self, routed_channels, image_channels=None, noise_should_send=True):
        self._markdown_to_image_channels = set(image_channels or [])
        self._markdown_to_image_max_chars = 15000
        self.generate_dashboard_report = MagicMock(side_effect=self._generate_dashboard_report)
        self.generate_wechat_dashboard = MagicMock(side_effect=self._generate_dashboard_report)
        self.save_report_to_file = MagicMock(return_value="/tmp/report.md")
        self.is_available = MagicMock(return_value=True)
        self.get_available_channels = MagicMock(
            return_value=[
                NotificationChannel.WECHAT,
                NotificationChannel.TELEGRAM,
                NotificationChannel.EMAIL,
                NotificationChannel.NTFY,
                NotificationChannel.GOTIFY,
            ]
        )
        self.get_channels_for_route = MagicMock(return_value=list(routed_channels))
        self.send_to_context = MagicMock(return_value=False)
        self.evaluate_noise_control = MagicMock(
            return_value=SimpleNamespace(
                should_send=noise_should_send,
                message="noise suppressed" if not noise_should_send else "",
            )
        )
        self.record_noise_control = MagicMock()
        self.release_noise_control = MagicMock()
        self._should_use_image_for_channel = MagicMock(
            side_effect=lambda channel, image_bytes: (
                channel.value in self._markdown_to_image_channels and image_bytes is not None
            )
        )
        self.generate_brief_report = MagicMock(return_value="brief-report")
        self.build_research_email_subject = MagicMock(return_value=None)
        self._send_wechat_image = MagicMock(return_value=True)
        self.send_to_wechat = MagicMock(return_value=True)
        self._send_telegram_photo = MagicMock(return_value=True)
        self.send_to_telegram = MagicMock(return_value=True)
        self._send_email_with_inline_image = MagicMock(return_value=True)
        self.send_to_email = MagicMock(return_value=True)
        self.send_to_ntfy = MagicMock(return_value=True)
        self.send_to_gotify = MagicMock(return_value=True)
        self.send_to_dingtalk = MagicMock(return_value=True)

    @staticmethod
    def _generate_dashboard_report(results):
        return "report:" + ",".join(r.code for r in results)


class TestPipelineReportRouteFiltering(unittest.TestCase):
    def test_send_notifications_applies_report_route_before_channel_iteration(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier([NotificationChannel.TELEGRAM])
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.get_channels_for_route.assert_called_once_with(
            "report",
            channels=[
                NotificationChannel.WECHAT,
                NotificationChannel.TELEGRAM,
                NotificationChannel.EMAIL,
                NotificationChannel.NTFY,
                NotificationChannel.GOTIFY,
            ],
        )
        pipeline.notifier.send_to_telegram.assert_not_called()
        pipeline.notifier.send_to_wechat.assert_not_called()
        pipeline.notifier.send_to_email.assert_not_called()
        pipeline.notifier.evaluate_noise_control.assert_not_called()
        pipeline.notifier.record_noise_control.assert_not_called()

    def test_saved_full_report_and_email_telegram_compact_share_exact_result_set(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.TELEGRAM, NotificationChannel.EMAIL]
        )
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._save_local_report(results, ReportType.SIMPLE)
        pipeline._send_notifications(results, ReportType.SIMPLE)

        self.assertGreaterEqual(
            pipeline.notifier.generate_dashboard_report.call_count,
            2,
        )
        for call in pipeline.notifier.generate_dashboard_report.call_args_list:
            self.assertIs(call.args[0], results)
        pipeline.notifier.generate_brief_report.assert_called_once_with(results)
        self.assertIs(
            pipeline.notifier.generate_brief_report.call_args.args[0],
            results,
        )
        pipeline.notifier.save_report_to_file.assert_called_once_with(
            "report:000001",
            filename=None,
        )
        pipeline.notifier.send_to_telegram.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")



    def test_full_email_and_telegram_preserve_identical_material_report(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        notifier = _FakeRoutedNotifier([NotificationChannel.EMAIL, NotificationChannel.TELEGRAM])
        notifier.generate_dashboard_report = MagicMock(return_value=(
            "FULL-SUPPORT\\nFULL-COUNTEREVIDENCE\\nFULL-RISK\\n"
            "FULL-TRIGGER\\nFULL-INVALIDATION\\nFULL-SOURCE-TIME"
        ))
        pipeline.notifier = notifier
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        pipeline._send_notifications([SimpleNamespace(code="000001")], ReportType.FULL)
        notifier.send_to_email.assert_called_once_with(notifier.generate_dashboard_report.return_value)
        notifier.send_to_telegram.assert_called_once_with(notifier.generate_dashboard_report.return_value)

    def test_full_email_cannot_be_replaced_by_png_image_only(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL], image_channels={"email"}
        )
        pipeline.notifier = notifier
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        with patch("src.md2img.markdown_to_image", return_value=b"preview-only-image"):
            pipeline._send_notifications([SimpleNamespace(code="000001")], ReportType.FULL)
        notifier.send_to_email.assert_called_once_with("report:000001")
        notifier._send_email_with_inline_image.assert_not_called()

    def test_canonical_simple_email_not_shortened_while_legacy_simple_nontrigger_stays(self):
        from tests.test_notification import _make_investor_brief_result
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        notifier = _FakeRoutedNotifier([NotificationChannel.EMAIL])
        pipeline.notifier = notifier
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        pipeline._send_notifications([_make_investor_brief_result()], ReportType.SIMPLE)
        notifier.send_to_email.assert_called_once_with("report:600519")

    def test_full_email_fails_closed_when_summary_only_would_delete_details(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        notifier = _FakeRoutedNotifier([NotificationChannel.EMAIL])
        notifier._report_summary_only = True
        pipeline.notifier = notifier
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        pipeline._send_notifications([SimpleNamespace(code="000001")], ReportType.FULL)
        notifier.send_to_email.assert_not_called()
        notifier._send_email_with_inline_image.assert_not_called()


    def test_canonical_full_email_egress_preserves_76_object_rows_and_report_material(self):
        """One synthetic canonical is projected by real DSA renderers; outbound is mocked."""
        from html.parser import HTMLParser

        from src.formatters import markdown_to_html_document
        from src.notification import NotificationService
        from src.services.evidence_traceability_registry import validate_runtime_trace
        from tests.test_notification import _make_config, _make_investor_brief_result

        class Visible(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []

            def handle_data(self, value):
                self.parts.append(value)

        result = _make_investor_brief_result()
        factor = result.dashboard["factor_decision"]
        source_universe = validate_runtime_trace(factor)["complete_research_universe"]
        notifier = _FakeRoutedNotifier([NotificationChannel.EMAIL, NotificationChannel.TELEGRAM])
        with patch("src.notification.get_config", return_value=_make_config(
            report_renderer_enabled=False, report_history_compare_n=0,
        )):
            actual = NotificationService()
            actual._get_history_compare_context = lambda *a, **kw: {}
            notifier.generate_dashboard_report.side_effect = actual.generate_dashboard_report
            notifier.generate_brief_report.side_effect = actual.generate_brief_report

            pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
            pipeline.notifier = notifier
            pipeline.config = SimpleNamespace(stock_email_groups=[])
            pipeline._send_notifications([result], ReportType.SIMPLE)

        email = notifier.send_to_email.call_args.args[0]
        telegram = notifier.send_to_telegram.call_args.args[0]
        assert email == telegram, "one canonical must deliver identical full material to both channels"
        assert "**原机器图76项数据义务与逐项准入" in email
        assert factor["investor_brief"]["one_line_conclusion"] in email

        parsed = Visible()
        parsed.feed(markdown_to_html_document(email))
        visible = "".join(parsed.parts)
        for row in source_universe["data_objects"]:
            assert row["dataset_id"] in email and row["dataset_id"] in visible
            assert row["fact_or_fields"] in email and row["fact_or_fields"] in visible
            assert row["native_frequency"] in email and row["native_frequency"] in visible
            assert row["product_consumer"] in email and row["product_consumer"] in visible
        for row in source_universe["requirements"]:
            assert row["requirement_id"] in email and row["requirement_id"] in visible
        assert len(source_universe["data_objects"]) == 76
        assert len(source_universe["requirements"]) == 26
        notifier._send_email_with_inline_image.assert_not_called()



    def test_canonical_simple_telegram_and_email_use_same_unabridged_material(self):
        from tests.test_notification import _make_investor_brief_result
        p = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        n = _FakeRoutedNotifier([NotificationChannel.EMAIL, NotificationChannel.TELEGRAM])
        n.generate_dashboard_report = MagicMock(return_value=(
            "PARENT_GLOBAL_UNKNOWN\\nSECTOR_UNKNOWN\\nMONTH_WEEK_DAY\\n"
            "30M_FAILED_UP_BREAKOUT\\n15M_UNCONFIRMED\\n5M_UNKNOWN\\n"
            "RISK_WEAK_TREND\\nSOURCE_AVAILABLE_AT\\nTRIGGER_INVALIDATION"
        ))
        p.notifier = n
        p.config = SimpleNamespace(stock_email_groups=[])
        p._send_notifications([_make_investor_brief_result()], ReportType.SIMPLE)
        n.send_to_email.assert_called_once_with(n.generate_dashboard_report.return_value)
        n.send_to_telegram.assert_called_once_with(n.generate_dashboard_report.return_value)
        n._send_telegram_photo.assert_not_called()

    def test_canonical_explicit_brief_cannot_override_complete_research(self):
        from tests.test_notification import _make_investor_brief_result
        p = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        n = _FakeRoutedNotifier([NotificationChannel.EMAIL, NotificationChannel.TELEGRAM])
        p.notifier = n
        p.config = SimpleNamespace(stock_email_groups=[])
        p._send_notifications([_make_investor_brief_result()], ReportType.BRIEF)
        n.send_to_email.assert_called_once_with("report:600519")
        n.send_to_telegram.assert_called_once_with("report:600519")
        n.generate_brief_report.assert_not_called()

    def test_canonical_telegram_must_not_suppress_full_report_with_png_or_15000_budget(self):
        from tests.test_notification import _make_investor_brief_result
        material = "PRIMARY_DATA\\n" + "PRICE_VOLUME_CONTEXT\\n" * 1200
        material += "SOURCE_AT_END_RISK_INVALIDATION_NOT_DROPPED"
        assert len(material) > 15000
        p = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        n = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM],
            image_channels={"email", "telegram"}
        )
        n.generate_dashboard_report = MagicMock(return_value=material)
        p.notifier = n
        p.config = SimpleNamespace(stock_email_groups=[])
        with patch("src.md2img.markdown_to_image", return_value=b"only-image") as converter:
            p._send_notifications([_make_investor_brief_result()], ReportType.SIMPLE)
        n.send_to_email.assert_called_once_with(material)
        n.send_to_telegram.assert_called_once_with(material)
        converter.assert_not_called()
        n._send_email_with_inline_image.assert_not_called()
        n._send_telegram_photo.assert_not_called()

    def test_canonical_telegram_only_summary_only_fails_closed(self):
        from tests.test_notification import _make_investor_brief_result
        p = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        n = _FakeRoutedNotifier([NotificationChannel.TELEGRAM])
        n._report_summary_only = True
        p.notifier = n
        p.config = SimpleNamespace(stock_email_groups=[])
        p._send_notifications([_make_investor_brief_result()], ReportType.SIMPLE)
        n.send_to_telegram.assert_not_called()
        n._send_telegram_photo.assert_not_called()

    def test_canonical_single_stock_simple_sends_full_report_not_legacy_brief(self):
        from tests.test_notification import _make_investor_brief_result
        p = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        n = SimpleNamespace(
            is_available=MagicMock(return_value=True),
            generate_dashboard_report=MagicMock(return_value="FULL_SUPPORT_AND_COUNTEREVIDENCE_RISK"),
            generate_brief_report=MagicMock(return_value="ABRIDGED_DROP_RISK"),
            send=MagicMock(return_value=True),
            _report_summary_only=False,
        )
        p.notifier = n
        p._build_notification_run_snapshot = MagicMock(return_value={})
        p._refresh_saved_diagnostic_snapshot = MagicMock()
        with patch("src.core.pipeline.record_notification_run"):
            p._send_single_stock_notification(_make_investor_brief_result(), ReportType.SIMPLE)
        n.send.assert_called_once()
        assert n.send.call_args.args[0] == "FULL_SUPPORT_AND_COUNTEREVIDENCE_RISK"
        n.generate_brief_report.assert_not_called()

    def test_canonical_single_stock_with_summary_only_sends_nothing(self):
        from tests.test_notification import _make_investor_brief_result
        p = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        n = SimpleNamespace(
            is_available=MagicMock(return_value=True),
            generate_dashboard_report=MagicMock(return_value="SHORT"),
            generate_brief_report=MagicMock(return_value="SHORT"),
            send=MagicMock(return_value=True),
            _report_summary_only=True,
        )
        p.notifier = n
        p._build_notification_run_snapshot = MagicMock(return_value={})
        p._refresh_saved_diagnostic_snapshot = MagicMock()
        with patch("src.core.pipeline.record_notification_run"):
            p._send_single_stock_notification(_make_investor_brief_result(), ReportType.SIMPLE)
        n.send.assert_not_called()
        n.generate_brief_report.assert_not_called()
        n.generate_dashboard_report.assert_not_called()


    def test_email_text_route_propagates_envelope_subject(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier([NotificationChannel.EMAIL])
        pipeline.notifier.build_research_email_subject.return_value = (
            "【晚间自动发现｜2026-09-24】完整研究"
        )
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=None):
            pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_email.assert_called_once_with(
            "brief-report",
            subject="【晚间自动发现｜2026-09-24】完整研究",
        )

    def test_email_inline_image_route_propagates_same_envelope_subject(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL],
            image_channels={"email"},
        )
        pipeline.notifier.build_research_email_subject.return_value = (
            "【我的自选研究｜2026-09-24】材料变化"
        )
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png"):
            pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier._send_email_with_inline_image.assert_called_once_with(
            b"png",
            subject="【我的自选研究｜2026-09-24】材料变化",
        )
        pipeline.notifier.send_to_email.assert_not_called()

    def test_markdown_to_image_uses_route_filtered_channels(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL],
            image_channels={"telegram"},
        )
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_not_called()
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_telegram.assert_not_called()

    def test_formal_report_telegram_image_route_keeps_required_email(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM],
            image_channels={"telegram"},
        )
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_called_once_with(
            "brief-report", max_chars=pipeline.notifier._markdown_to_image_max_chars
        )
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier._send_telegram_photo.assert_called_once_with(b"png")
        pipeline.notifier.send_to_telegram.assert_not_called()

    def test_formal_report_filters_ntfy_and_keeps_required_email(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL, NotificationChannel.NTFY],
            image_channels={"ntfy"},
        )
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_not_called()
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_ntfy.assert_not_called()
        pipeline.notifier._send_email_with_inline_image.assert_not_called()
        pipeline.notifier._send_telegram_photo.assert_not_called()

    def test_formal_report_filters_gotify_and_keeps_required_email(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL, NotificationChannel.GOTIFY],
            image_channels={"gotify"},
        )
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_not_called()
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_gotify.assert_not_called()
        pipeline.notifier._send_email_with_inline_image.assert_not_called()
        pipeline.notifier._send_telegram_photo.assert_not_called()

    def test_empty_compact_projection_fails_closed_without_legacy_static_send(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.TELEGRAM, NotificationChannel.EMAIL]
        )
        pipeline.notifier.generate_brief_report.return_value = ""
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_telegram.assert_not_called()
        pipeline.notifier.send_to_email.assert_not_called()
        pipeline.notifier.generate_dashboard_report.assert_called_once_with(results)

    def test_noise_suppression_happens_before_markdown_to_image(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.TELEGRAM],
            image_channels={"telegram"},
            noise_should_send=False,
        )
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_not_called()
        pipeline.notifier.send_to_telegram.assert_not_called()
        pipeline.notifier.record_noise_control.assert_not_called()

    def test_noise_reservation_released_when_all_formal_channels_fail(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
        )
        pipeline.notifier.send_to_email.return_value = False
        pipeline.notifier.send_to_telegram.side_effect = RuntimeError("send failed")
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_telegram.assert_called_once_with("brief-report")
        pipeline.notifier.record_noise_control.assert_not_called()
        pipeline.notifier.release_noise_control.assert_called_once()

    def test_channel_exception_does_not_skip_later_channel_and_records_noise(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier([NotificationChannel.TELEGRAM, NotificationChannel.EMAIL])
        pipeline.notifier.send_to_telegram.side_effect = RuntimeError("telegram failed")
        pipeline.notifier.send_to_email.return_value = True
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_telegram.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier.record_noise_control.assert_called_once()
        pipeline.notifier.release_noise_control.assert_not_called()

    def test_all_static_channel_failures_release_noise_reservation(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier([NotificationChannel.TELEGRAM, NotificationChannel.EMAIL])
        pipeline.notifier.send_to_telegram.side_effect = RuntimeError("telegram failed")
        pipeline.notifier.send_to_email.return_value = False
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_telegram.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier.record_noise_control.assert_not_called()
        pipeline.notifier.release_noise_control.assert_called_once()

    def test_context_cannot_substitute_for_required_email_in_diagnostics(self):
        token = activate_run_diagnostic_context(
            trace_id="trace-context",
            query_id="query-context",
            stock_code="000001",
            trigger_source="bot",
        )
        try:
            pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
            pipeline.notifier = _FakeRoutedNotifier(
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
            )
            pipeline.notifier.send_to_context.return_value = True
            pipeline.notifier.send_to_email.return_value = True
            pipeline.notifier.send_to_telegram.return_value = False
            pipeline.config = SimpleNamespace(stock_email_groups=[])
            pipeline.save_context_snapshot = True
            pipeline.db = MagicMock()
            results = [SimpleNamespace(code="000001", query_id="query-context")]

            with patch("src.core.pipeline.logger.info") as mock_info:
                pipeline._send_notifications(results, ReportType.SIMPLE)
            snapshot = current_diagnostic_snapshot() or {}
        finally:
            reset_run_diagnostic_context(token)
        notification_runs = snapshot.get("notification_runs", [])
        self.assertEqual(
            [run.get("channel") for run in notification_runs],
            ["email", "telegram"],
        )
        self.assertTrue(notification_runs[0]["success"])
        self.assertFalse(notification_runs[1]["success"])
        pipeline.notifier.send_to_context.assert_not_called()
        self.assertTrue(
            any(
                call.args and call.args[0] == "决策仪表盘推送成功"
                for call in mock_info.call_args_list
            )
        )
        final_update = pipeline.db.update_analysis_history_diagnostics.call_args_list[-1]
        persisted_runs = final_update.kwargs["diagnostics"]["notification_runs"]
        self.assertEqual(
            [run.get("channel") for run in persisted_runs],
            ["email", "telegram"],
        )

    def test_context_only_flag_does_not_suppress_formal_email_and_telegram(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
        )
        pipeline.notifier.send_to_context.return_value = True
        pipeline.notifier.should_broadcast_static_channels = MagicMock(return_value=False)
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.should_broadcast_static_channels.assert_not_called()
        pipeline.notifier.send_to_context.assert_not_called()
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_telegram.assert_called_once_with("brief-report")
        pipeline.notifier.record_noise_control.assert_called_once()
        pipeline.notifier.release_noise_control.assert_not_called()

    def test_formal_report_filters_dingtalk_even_when_adapter_returns_it(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL, NotificationChannel.DINGTALK]
        )
        pipeline.notifier.send_to_context.return_value = True
        pipeline.notifier.should_broadcast_static_channels = MagicMock(return_value=False)
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.should_broadcast_static_channels.assert_not_called()
        pipeline.notifier.send_to_context.assert_not_called()
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_dingtalk.assert_not_called()
        pipeline.notifier.record_noise_control.assert_called_once()
        pipeline.notifier.release_noise_control.assert_not_called()

    def test_telegram_context_result_cannot_replace_formal_email_delivery(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
        )
        pipeline.notifier.send_to_context.return_value = True
        pipeline.notifier.should_broadcast_static_channels = MagicMock(return_value=False)
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.should_broadcast_static_channels.assert_not_called()
        pipeline.notifier.send_to_context.assert_not_called()
        pipeline.notifier.send_to_email.assert_called_once_with("brief-report")
        pipeline.notifier.send_to_telegram.assert_called_once_with("brief-report")
        pipeline.notifier.record_noise_control.assert_called_once()
        pipeline.notifier.release_noise_control.assert_not_called()

    def test_send_notifications_records_email_and_optional_telegram_separately(self):
        token = activate_run_diagnostic_context(trace_id="trace-notify")
        try:
            pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
            pipeline.notifier = _FakeRoutedNotifier(
                [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
            )
            pipeline.notifier.send_to_email.return_value = True
            pipeline.notifier.send_to_telegram.return_value = False
            pipeline.config = SimpleNamespace(stock_email_groups=[])
            results = [SimpleNamespace(code="000001")]

            pipeline._send_notifications(results, ReportType.SIMPLE)

            snapshot = current_diagnostic_snapshot() or {}
            notification_runs = snapshot.get("notification_runs", [])
            channels = [run.get("channel") for run in notification_runs]
            self.assertEqual(channels, ["email", "telegram"])
            self.assertNotIn("report", channels)
        finally:
            reset_run_diagnostic_context(token)

    def test_notification_summary_degraded_when_optional_telegram_fails(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeRoutedNotifier(
            [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
        )
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        pipeline.notifier.send_to_email.return_value = True
        pipeline.notifier.send_to_telegram.return_value = False
        results = [SimpleNamespace(code="000001")]

        token = activate_run_diagnostic_context(
            trace_id="trace-notify",
            query_id="query-notify",
            stock_code="000001",
            trigger_source="api",
        )
        try:
            pipeline._send_notifications(results, ReportType.SIMPLE)
            snapshot = current_diagnostic_snapshot()
        finally:
            reset_run_diagnostic_context(token)

        self.assertEqual(snapshot["notification_runs"][0]["channel"], "email")
        self.assertEqual(snapshot["notification_runs"][0]["success"], True)
        self.assertEqual(snapshot["notification_runs"][1]["channel"], "telegram")
        self.assertEqual(snapshot["notification_runs"][1]["success"], False)

        summary = build_run_diagnostic_summary(
            context_snapshot={"diagnostics": snapshot},
            raw_result={"success": True, "model_used": "deepseek-chat"},
            report_saved=True,
        )

        self.assertEqual(summary["components"]["notification"]["status"], "degraded")
        self.assertIn(
            "telegram",
            summary["components"]["notification"]["details"]["failed"],
        )


class _FakeFeishuFileNotifier:
    def __init__(self):
        self._markdown_to_image_channels = set()
        self._markdown_to_image_max_chars = 15000
        self._feishu_send_as_file = True
        self.generate_dashboard_report = MagicMock(
            return_value="[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"
        )
        self.save_report_to_file = MagicMock(return_value="/tmp/dashboard_20260802.md")
        self.is_available = MagicMock(return_value=True)
        self.get_available_channels = MagicMock(return_value=[NotificationChannel.FEISHU])
        self.get_channels_for_route = MagicMock(return_value=[NotificationChannel.FEISHU])
        self.send_to_context = MagicMock(return_value=False)
        self.evaluate_noise_control = MagicMock(
            return_value=SimpleNamespace(should_send=True, message="")
        )
        self.record_noise_control = MagicMock()
        self.release_noise_control = MagicMock()
        self.send_feishu_file = MagicMock(return_value=True)
        self.send_to_feishu = MagicMock(return_value=True)
        self.generate_brief_report = MagicMock(
            return_value="[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"
        )
        self.build_research_email_subject = MagicMock(return_value=None)
        self._should_use_image_for_channel = MagicMock(return_value=False)
        self._send_email_with_inline_image = MagicMock(return_value=True)
        self.send_to_email = MagicMock(return_value=True)


class TestPipelineFeishuFileRouting(unittest.TestCase):
    def test_formal_report_filters_feishu_file_and_uses_required_email(self):
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.notifier = _FakeFeishuFileNotifier()
        pipeline.notifier.get_available_channels.return_value = [
            NotificationChannel.EMAIL,
            NotificationChannel.FEISHU,
        ]
        pipeline.notifier.get_channels_for_route.return_value = [
            NotificationChannel.EMAIL,
            NotificationChannel.FEISHU,
        ]
        pipeline.config = SimpleNamespace(stock_email_groups=[])
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        pipeline.notifier.send_to_email.assert_called_once_with(
            "# 市场复盘\n\n正文"
        )
        pipeline.notifier.save_report_to_file.assert_not_called()
        pipeline.notifier.send_feishu_file.assert_not_called()
        pipeline.notifier.send_to_feishu.assert_not_called()


if __name__ == "__main__":
    unittest.main()
