"""Current-main B8A at-send consumer contract: no provider, database, or live transport."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from src.notification import NotificationChannel, NotificationService
from src.services.factor_decision_summary import build_stock_factor_decision_summary
from tests.test_factor_decision_summary import _trend
from tests.test_notification import _make_config


def _utc(ymd, hour=10, minute=17):
    return datetime.fromisoformat(ymd).replace(
        hour=hour, minute=minute, tzinfo=timezone.utc,
    )


def _asset(ymd, *, available_at=None, market="cn", provider="unit-fixture"):
    data = {
        "target_date": ymd,
        "market": market,
        "stock_code": "600519",
        "completed_bar_only": True,
        "source_alignment": {"status": "SINGLE_SOURCE"},
        "provider_identity": provider,
        "adjustment_basis": "qfq",
        "data_snapshot_identity": "typed-synthetic-" + ymd,
        "available_at_max": available_at or ymd + "T10:00:00+00:00",
        "timeframes": {
            "daily": {
                "status": "READY",
                "completed_bar_only": True,
                "latest_bar_date": ymd,
                "completed_through": ymd,
            }
        },
    }
    factor = build_stock_factor_decision_summary(
        _trend(), include_canonical=True,
        multi_timeframe_structure_context=data,
    )
    return SimpleNamespace(code="600519", dashboard={"factor_decision": factor})


def _notifier():
    with mock.patch(
        "src.notification.get_config",
        return_value=_make_config(custom_webhook_urls=["https://example.com/simulated"]),
    ):
        return NotificationService()


def _deliver(assets, at, *, route="report", text="SUPPORT\nCOUNTEREVIDENCE\nRISK\nTRIGGER\nINVALIDATION"):
    notifier = _notifier()
    with mock.patch("src.notification._delivery_time_utc", return_value=at, create=True), mock.patch.object(
        notifier, "send_to_custom", return_value=True,
    ) as sink:
        result = notifier.send_with_results(text, route_type=route, asset_results=assets)
    return result, sink, text


def test_stale_old_snapshot_rejected_before_any_channel():
    output, channel, _ = _deliver([_asset("2025-09-30")], _utc("2026-10-08", 11))
    assert output.status == "data_stale"
    assert not output.success and not output.dispatched
    channel.assert_not_called()


def test_valid_latest_completed_session_sends_full_unmodified_material():
    value, channel, original = _deliver([_asset("2026-10-08")], _utc("2026-10-08", 11))
    assert value.status == "sent" and value.success
    channel.assert_called_once_with(original)
    for fragment in ("SUPPORT", "COUNTEREVIDENCE", "RISK", "TRIGGER", "INVALIDATION"):
        assert fragment in channel.call_args.args[0]


def test_valid_premarket_previous_completed_session_not_blocked():
    result, channel, _ = _deliver([_asset("2026-10-08")], _utc("2026-10-09", 1))
    assert result.status == "sent" and result.success
    channel.assert_called_once()


def test_delayed_egress_past_next_exchange_close_is_rejected():
    result, channel, _ = _deliver([_asset("2026-10-08")], _utc("2026-10-09", 11))
    assert result.status == "data_stale"
    channel.assert_not_called()


def test_future_provider_observation_fails_closed():
    result, channel, _ = _deliver(
        [_asset("2026-10-08", available_at="2026-10-09T10:00:00+00:00")],
        _utc("2026-10-08", 11),
    )
    assert result.status == "data_stale"
    channel.assert_not_called()


def test_explicit_empty_asset_list_cannot_bypass_freshness():
    result, channel, _ = _deliver([], _utc("2026-10-08", 11))
    assert result.status == "data_stale"
    assert not result.dispatched
    channel.assert_not_called()


def test_mixed_old_and_current_asset_results_reject_entire_report():
    result, channel, _ = _deliver(
        [_asset("2026-10-08"), _asset("2025-09-30")], _utc("2026-10-08", 11),
    )
    assert result.status == "data_stale"
    channel.assert_not_called()


def test_canonical_not_bound_or_provider_unknown_rejected():
    for asset in (SimpleNamespace(code="600519", dashboard={}), _asset("2026-10-08", provider="")):
        result, channel, _ = _deliver([asset], _utc("2026-10-08", 11))
        assert result.status == "data_stale"
        channel.assert_not_called()


def test_market_only_report_without_assets_preserves_existing_send_route():
    notifier = _notifier()
    with mock.patch.object(notifier, "send_to_custom", return_value=True) as channel:
        result = notifier.send_with_results("MARKET_FACTS", route_type="report")
    assert result.status == "sent" and result.success
    channel.assert_called_once_with("MARKET_FACTS")


@pytest.mark.parametrize("route", ["alert", "system_error"])
def test_system_and_alert_routes_not_overblocked(route):
    result, channel, original = _deliver([_asset("2025-09-30")], _utc("2026-10-08", 11), route=route)
    assert result.status == "sent" and result.success
    channel.assert_called_once_with(original)


def test_delayed_egress_between_precheck_and_channel_rejects():
    notifier = _notifier()
    sample = _asset("2026-10-08")
    actual_times = iter([_utc("2026-10-09", 1), _utc("2026-10-09", 11)])
    with mock.patch(
        "src.notification._delivery_time_utc", side_effect=lambda: next(actual_times),
    ), mock.patch.object(notifier, "send_to_custom", return_value=True) as sink:
        receipt = notifier.send_with_results(
            "NONTRUNCATED_MESSAGE", route_type="report", asset_results=[sample],
        )
    assert receipt.status == "data_stale"
    assert not receipt.success
    sink.assert_not_called()


def test_missing_exchange_calendar_blocks_even_internally_matching_data():
    with mock.patch(
        "src.core.trading_calendar.resolve_latest_completed_session_fail_closed",
        return_value=None,
    ):
        receipt, sink, _ = _deliver([_asset("2026-10-08")], _utc("2026-10-08", 11))
    assert receipt.status == "data_stale"
    sink.assert_not_called()


def test_source_identity_tampering_after_canonical_binding_is_rejected():
    sample = _asset("2026-10-08")
    sample.dashboard["factor_decision"]["multi_timeframe_structure_context"][
        "data_snapshot_identity"
    ] = "forged-after-binding"
    receipt, sink, _ = _deliver([sample], _utc("2026-10-08", 11))
    assert receipt.status == "data_stale"
    sink.assert_not_called()


def test_original_boolean_send_can_forward_explicit_asset_evidence():
    notifier = _notifier()
    with mock.patch("src.notification._delivery_time_utc", return_value=_utc("2026-10-08", 11)), mock.patch.object(
        notifier, "send_to_custom", return_value=True,
    ) as channel:
        value = notifier.send("FULL_ASSET_DETAILS", route_type="report", asset_results=[_asset("2025-09-30")])
    assert value is False
    channel.assert_not_called()


def test_single_stock_pipeline_passes_current_asset_result_to_real_send_contract():
    from src.core.pipeline import StockAnalysisPipeline
    from src.enums import ReportType

    asset = _asset("2026-10-08")
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    observed = {}

    def fake_send(content, *, asset_results=None, route_type=None, **kwargs):
        observed["content"] = content
        observed["asset_results"] = asset_results
        observed["route_type"] = route_type
        return True

    pipeline.notifier = SimpleNamespace(
        is_available=lambda: True,
        generate_brief_report=lambda results: "SUPPORT\nCOUNTEREVIDENCE\nRISK\nTRIGGER",
        send=fake_send,
    )
    pipeline._build_notification_run_snapshot = mock.MagicMock(return_value={})
    pipeline._refresh_saved_diagnostic_snapshot = mock.MagicMock()

    with mock.patch("src.core.pipeline.record_notification_run") as record:
        pipeline._send_single_stock_notification(asset, ReportType.BRIEF)
    assert observed["content"] == "SUPPORT\nCOUNTEREVIDENCE\nRISK\nTRIGGER"
    assert observed["asset_results"] == [asset]
    assert observed["route_type"] == "report"
    record.assert_called()


@pytest.mark.parametrize(
    ("date_str", "send_at", "expected_count"),
    [
        ("2025-09-30", _utc("2026-10-08", 11), 0),
        ("2026-10-08", _utc("2026-10-08", 11), 1),
    ],
)
def test_single_stock_pipeline_real_notifier_blocks_stale_before_egress(
    date_str, send_at, expected_count,
):
    from src.core.pipeline import StockAnalysisPipeline
    from src.enums import ReportType

    asset = _asset(date_str)
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    notifier = _notifier()
    material = "COMPLETE_SUPPORT\nCOMPLETE_COUNTEREVIDENCE\nRISK\nTRIGGER\nINVALIDATION"
    notifier.generate_brief_report = mock.MagicMock(return_value=material)
    pipeline.notifier = notifier
    pipeline._build_notification_run_snapshot = mock.MagicMock(return_value={})
    pipeline._refresh_saved_diagnostic_snapshot = mock.MagicMock()

    with mock.patch("src.notification._delivery_time_utc", return_value=send_at), mock.patch.object(
        notifier, "send_to_custom", return_value=True,
    ) as sent, mock.patch("src.core.pipeline.record_notification_run"):
        pipeline._send_single_stock_notification(asset, ReportType.BRIEF)

    assert sent.call_count == expected_count
    if expected_count:
        sent.assert_called_once_with(material)
    else:
        sent.assert_not_called()


@pytest.mark.parametrize(
    ("asset_date", "expected_channels"),
    [("2025-09-30", 0), ("2026-10-08", 1)],
)
def test_aggregate_pipeline_direct_webhook_respects_freshness(
    asset_date, expected_channels,
):
    from src.core.pipeline import StockAnalysisPipeline
    from src.enums import ReportType

    asset = _asset(asset_date)
    notifier = _notifier()
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.notifier = notifier
    pipeline.config = _make_config()
    pipeline._generate_aggregate_report = mock.MagicMock(
        return_value="COMPLETE_AGGREGATE_SUPPORT_AND_RISK",
    )
    pipeline._build_notification_run_snapshot = mock.MagicMock(return_value={})
    pipeline._refresh_saved_diagnostic_snapshot = mock.MagicMock()
    with mock.patch(
        "src.notification._delivery_time_utc", return_value=_utc("2026-10-08", 11),
    ), mock.patch.object(notifier, "send_to_custom", return_value=True) as outbound, mock.patch.object(
        notifier, "send_to_context", return_value=False,
    ) as context, mock.patch.object(
        notifier, "evaluate_noise_control",
        return_value=SimpleNamespace(should_send=True, message="allowed-fixture"),
    ), mock.patch.object(notifier, "record_noise_control"), mock.patch.object(
        notifier, "release_noise_control",
    ), mock.patch("src.core.pipeline.record_notification_run"):
        pipeline._send_notifications([asset], ReportType.FULL, skip_push=False)
    assert outbound.call_count == expected_channels
    assert context.call_count == expected_channels
    if expected_channels:
        assert "COMPLETE_AGGREGATE_SUPPORT_AND_RISK" in outbound.call_args.args[0]
    else:
        outbound.assert_not_called()
        context.assert_not_called()


def _aggregate_two_channel_fixture():
    from src.core.pipeline import StockAnalysisPipeline

    notifier = _notifier()
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.notifier = notifier
    pipeline.config = _make_config()
    pipeline._generate_aggregate_report = mock.MagicMock(return_value="COMPLETE_FULL_RISK")
    pipeline._build_notification_run_snapshot = mock.MagicMock(return_value={})
    pipeline._refresh_saved_diagnostic_snapshot = mock.MagicMock()
    return pipeline, notifier


def test_aggregate_channel_fanout_rejects_when_exchange_closes_mid_delivery():
    from src.enums import ReportType

    pipeline, notifier = _aggregate_two_channel_fixture()
    times = iter([
        _utc("2026-10-09", 1),
        _utc("2026-10-09", 1, minute=5),
        _utc("2026-10-09", 11),
    ])
    channels = [NotificationChannel.CUSTOM, NotificationChannel.TELEGRAM]
    with mock.patch(
        "src.notification._delivery_time_utc", side_effect=lambda: next(times),
    ), mock.patch.object(
        notifier, "get_available_channels", return_value=channels,
    ), mock.patch.object(
        notifier, "get_channels_for_route", return_value=channels,
    ), mock.patch.object(
        notifier, "generate_brief_report", return_value="CURRENT_BRIEF_WITH_RISK",
    ), mock.patch.object(
        notifier, "_should_use_image_for_channel", return_value=False,
    ), mock.patch.object(notifier, "send_to_custom", return_value=True) as first, mock.patch.object(
        notifier, "send_to_telegram", return_value=True,
    ) as second, mock.patch.object(
        notifier, "send_to_context", return_value=False,
    ), mock.patch.object(
        notifier, "evaluate_noise_control",
        return_value=SimpleNamespace(should_send=True, message="legal-fixture"),
    ), mock.patch.object(
        notifier, "record_noise_control",
    ), mock.patch.object(
        notifier, "release_noise_control",
    ), mock.patch(
        "src.core.pipeline.record_notification_run",
    ) as record:
        pipeline._send_notifications(
            [_asset("2026-10-08")], ReportType.FULL, skip_push=False,
        )
    first.assert_called_once_with("COMPLETE_FULL_RISK")
    second.assert_not_called()
    assert any(
        call.kwargs.get("channel") == NotificationChannel.TELEGRAM.value
        and call.kwargs.get("status") == "failed"
        for call in record.call_args_list
    )


def test_aggregate_transport_value_error_does_not_disable_unrelated_channels():
    from src.enums import ReportType

    pipeline, notifier = _aggregate_two_channel_fixture()
    channels = [NotificationChannel.CUSTOM, NotificationChannel.TELEGRAM]
    with mock.patch(
        "src.notification._delivery_time_utc", return_value=_utc("2026-10-09", 1),
    ), mock.patch.object(
        notifier, "get_available_channels", return_value=channels,
    ), mock.patch.object(
        notifier, "get_channels_for_route", return_value=channels,
    ), mock.patch.object(
        notifier, "generate_brief_report", return_value="CURRENT_BRIEF_WITH_RISK",
    ), mock.patch.object(
        notifier, "_should_use_image_for_channel", return_value=False,
    ), mock.patch.object(
        notifier, "send_to_custom", side_effect=ValueError("channel-only-error"),
    ) as first, mock.patch.object(
        notifier, "send_to_telegram", return_value=True,
    ) as second, mock.patch.object(
        notifier, "send_to_context", return_value=False,
    ), mock.patch.object(
        notifier, "evaluate_noise_control",
        return_value=SimpleNamespace(should_send=True, message="legal-fixture"),
    ), mock.patch.object(
        notifier, "record_noise_control",
    ), mock.patch.object(
        notifier, "release_noise_control",
    ), mock.patch(
        "src.core.pipeline.record_notification_run",
    ) as record:
        pipeline._send_notifications(
            [_asset("2026-10-08")], ReportType.FULL, skip_push=False,
        )
    first.assert_called_once()
    second.assert_called_once_with("CURRENT_BRIEF_WITH_RISK")
    assert any(
        call.kwargs.get("channel") == NotificationChannel.TELEGRAM.value
        and call.kwargs.get("status") == "success"
        for call in record.call_args_list
    )

@pytest.mark.parametrize(
    ("date_str", "allowed"),
    [("2025-09-30", False), ("2026-10-08", True)],
)
def test_p0_direct_email_must_revalidate_native_asset_at_egress(date_str, allowed):
    from src.core.pipeline import P0BoundedTrialError, StockAnalysisPipeline
    from src.enums import ReportType

    asset = _asset(date_str)
    notifier = _notifier()
    notifier.generate_brief_report = mock.MagicMock(
        return_value="COMPLETE_P0_EMAIL_SUPPORT_AND_RISK",
    )
    notifier.save_report_to_file = mock.MagicMock(return_value="mock-report-path")
    notifier.send_to_email = mock.MagicMock(return_value=True)
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.notifier = notifier
    pipeline._generate_aggregate_report = mock.MagicMock(
        return_value="COMPLETE_UNMODIFIED_AUDIT",
    )
    with mock.patch(
        "src.core.pipeline.assert_canonical_consumer_consistency",
    ), mock.patch(
        "src.notification._delivery_time_utc", return_value=_utc("2026-10-08", 11),
    ):
        if allowed:
            assert pipeline._finalize_p0_bounded_run(
                [asset], ["600519"], ReportType.SIMPLE,
            ) == "COMPLETE_UNMODIFIED_AUDIT"
        else:
            with pytest.raises(P0BoundedTrialError, match="current|stale|latest"):
                pipeline._finalize_p0_bounded_run(
                    [asset], ["600519"], ReportType.SIMPLE,
                )
    if allowed:
        notifier.send_to_email.assert_called_once_with("COMPLETE_P0_EMAIL_SUPPORT_AND_RISK")
    else:
        notifier.send_to_email.assert_not_called()
    notifier.save_report_to_file.assert_called_once_with("COMPLETE_UNMODIFIED_AUDIT")


def test_daily_report_shortcut_preserves_structured_asset_freshness():
    from src.notification import send_daily_report

    asset = _asset("2026-10-08")
    notifier = _notifier()
    notifier.generate_daily_report = mock.MagicMock(
        return_value="COMPLETE_DAILY_REPORT_MATERIAL",
    )
    notifier.save_report_to_file = mock.MagicMock()
    notifier.send = mock.MagicMock(return_value=True)
    with mock.patch("src.notification.get_notification_service", return_value=notifier):
        assert send_daily_report([asset]) is True
    notifier.send.assert_called_once_with(
        "COMPLETE_DAILY_REPORT_MATERIAL", asset_results=[asset],
    )
    notifier.save_report_to_file.assert_called_once_with(
        "COMPLETE_DAILY_REPORT_MATERIAL",
    )

@pytest.mark.parametrize("include_assets", [True, False])
def test_main_fused_research_send_binds_assets_without_blocking_market_only(include_assets):
    import main
    from pathlib import Path
    from tests.test_main_schedule_mode import MainScheduleModeTestCase

    test_owner = MainScheduleModeTestCase(
        methodName="test_fused_research_notification_orders_market_global_etf_stock",
    )
    test_owner.setUp()
    try:
        args = test_owner._make_args()
        config = test_owner._make_config(
            trading_day_check_enabled=False,
            market_review_enabled=True,
            daily_market_context_enabled=False,
            single_stock_notify=False,
            merge_email_notification=True,
            analysis_delay=0,
            database_path=str(Path(test_owner.temp_dir.name) / "fused-stub.db"),
            report_type="simple",
        )
        asset = _asset("2026-10-08")
        asset.name = "SYNTHETIC_ASSET"
        asset.sentiment_score = 43
        asset.operation_advice = "watch"
        asset.trend_prediction = "neutral"
        asset.get_emoji = lambda: "symbol"
        assets = [asset] if include_assets else []
        observed = {}

        def typed_send(content, *, asset_results=None, route_type=None,
                       email_send_to_all=False, email_subject=None):
            observed["content"] = content
            observed["asset_results"] = asset_results
            observed["route_type"] = route_type
            return True

        pipeline = mock.MagicMock()
        pipeline.run.return_value = assets
        pipeline.notifier.is_available.return_value = True
        pipeline.notifier.generate_brief_report.return_value = "COMPLETE_ASSET_SUPPORT_RISK"
        pipeline.notifier.send = typed_send
        market_review = SimpleNamespace(
            report="LEGACY_NOT_FOR_PRODUCT",
            market_review_payload={
                "region": "cn",
                "title": "MARKET",
                "sections": [
                    {"key": "overview", "title": "MARKET", "markdown": "CN_ONLY"},
                ],
            },
        )

        with mock.patch.object(main, "_refresh_stock_index_cache_for_analysis"), mock.patch(
            "main._compute_trading_day_filter", return_value=([], "cn", False),
        ), mock.patch(
            "src.core.pipeline.StockAnalysisPipeline", return_value=pipeline,
        ), mock.patch(
            "main._run_market_review_with_shared_lock", return_value=market_review,
        ), mock.patch("src.core.market_review.run_market_review"):
            main.run_full_analysis(config, args, [])
        assert "CN_ONLY" in observed["content"]
        assert observed["route_type"] == "report"
        if include_assets:
            assert observed["asset_results"] is assets
            assert "COMPLETE_ASSET_SUPPORT_RISK" in observed["content"]
        else:
            assert observed["asset_results"] is None
    finally:
        test_owner.tearDown()
