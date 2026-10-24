from __future__ import annotations

import os

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

import main


def _config(**overrides):
    values = {
        "b1_rights_evidence_path": "config/b1-source-rights.json",
        "market_review_enabled": True,
        "single_stock_notify": True,
        "merge_email_notification": True,
        "daily_market_context_enabled": True,
        "market_review_region": "cn",
        "analysis_delay": 0,
        "report_type": "simple",
        "backtest_enabled": True,
        "stock_list": ["600519", "510300"],
        "refresh_stock_list": MagicMock(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _args(**overrides):
    values = {
        "p0_bounded_trial": False,
        "portfolio": None,
        "no_market_review": False,
        "single_notify": True,
        "no_context_snapshot": True,
        "workers": 1,
        "dry_run": False,
        "no_notify": False,
        "schedule": False,
        "watchlist_conditional": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_b1_mainline_is_specified_code_in_memory_no_send_only():
    config = _config()
    args = _args()
    codes = ["600519", "510300"]
    pipeline = MagicMock()
    pipeline.run.return_value = [
        SimpleNamespace(code="600519", success=True),
        SimpleNamespace(code="510300", success=True),
    ]

    with patch.object(main, "_resolve_portfolio_stock_codes") as portfolio, \
         patch.object(main, "_refresh_stock_index_cache_for_analysis") as refresh, \
         patch.object(
             main, "_compute_trading_day_filter",
             return_value=(codes, "cn", False),
         ), patch(
             "src.core.pipeline.StockAnalysisPipeline", return_value=pipeline,
         ) as pipeline_cls, patch.object(
             main, "_run_auto_backtest",
         ) as backtest, patch.object(
             main, "_run_market_review_with_shared_lock",
         ) as market_review:
        observed = main.run_full_analysis(
            config, args, codes, raise_errors=True,
        )

    assert observed is True
    portfolio.assert_not_called()
    refresh.assert_not_called()
    backtest.assert_not_called()
    market_review.assert_not_called()
    assert config.single_stock_notify is False
    pipeline_cls.assert_called_once()
    assert pipeline_cls.call_args.kwargs["daily_market_context_enabled"] is False
    assert pipeline_cls.call_args.kwargs["daily_market_context_allow_generate"] is False
    pipeline.run.assert_called_once_with(
        stock_codes=codes,
        dry_run=False,
        send_notification=False,
        merge_notification=False,
        current_time=ANY,
    )


def test_b1_mainline_rejects_incomplete_asset_result_set():
    config = _config()
    args = _args()
    codes = ["600519", "510300"]
    pipeline = MagicMock()
    pipeline.run.return_value = [SimpleNamespace(code="600519", success=True)]

    with patch.object(main, "_compute_trading_day_filter", return_value=(codes, "cn", False)), \
         patch("src.core.pipeline.StockAnalysisPipeline", return_value=pipeline), \
         pytest.raises(ValueError, match="B1_NO_SEND_RESEARCH_INCOMPLETE"):
        main.run_full_analysis(config, args, codes, raise_errors=True)


def test_b1_mainline_rejects_broker_before_portfolio_loader():
    config = _config()
    args = _args(portfolio="futu")
    with patch.object(main, "_resolve_portfolio_stock_codes") as resolver, \
         pytest.raises(ValueError, match="BROKER_PORTFOLIO_FORBIDDEN"):
        main.run_full_analysis(config, args, ["600519"], raise_errors=True)
    resolver.assert_not_called()


def test_b1_scheduler_uses_failure_propagating_task_runner():
    config = _config()
    args = _args()
    with patch(
        "src.services.research_state_runtime.durability_enabled",
        return_value=False,
    ), patch(
        "src.services.runtime_scheduler.run_with_global_analysis_lock",
    ) as locked:
        main._run_analysis_with_runtime_scheduler_lock(
            config, args, ["600519", "510300"],
        )
    assert locked.call_args.kwargs["task_runner"] is main.run_scheduled_analysis


def test_b1_main_sets_local_litellm_cost_map_before_pipeline_import():
    config = _config()
    args = _args()
    codes = ["600519", "510300"]
    pipeline = MagicMock()
    pipeline.run.return_value = [
        SimpleNamespace(code="600519", success=True),
        SimpleNamespace(code="510300", success=True),
    ]
    with patch.dict(
        os.environ, {"LITELLM_LOCAL_MODEL_COST_MAP": "false"}, clear=False,
    ), patch.object(
        main, "_compute_trading_day_filter",
        return_value=(codes, "cn", False),
    ), patch(
        "src.core.pipeline.StockAnalysisPipeline", return_value=pipeline,
    ):
        assert main.run_full_analysis(
            config, args, codes, raise_errors=True,
        ) is True
        assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "true"


@pytest.mark.parametrize(
    "field,value",
    [
        ("auto_screen", True),
        ("market_review", True),
        ("serve", True),
        ("webui", True),
        ("backtest", True),
        ("portfolio", "futu"),
    ],
)
def test_b1_entry_rejects_parallel_or_external_routes_before_start(field, value):
    args = SimpleNamespace(debug=False, check_notify=False)
    setattr(args, field, value)
    config = SimpleNamespace(
        b1_rights_evidence_path="config/b1-source-rights.json",
        log_dir="logs",
        validate=lambda: [],
    )
    with patch.object(main, "parse_arguments", return_value=args), \
         patch.object(main, "get_config", return_value=config), \
         patch.object(main, "_setup_bootstrap_logging"), \
         patch.object(main, "_setup_runtime_logging"):
        assert main.main() == 2
