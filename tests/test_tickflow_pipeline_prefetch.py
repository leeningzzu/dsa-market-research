# -*- coding: utf-8 -*-
"""Pipeline-level regression tests for TickFlow batch prefetch wiring."""

import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.analyzer import AnalysisResult
from src.core.pipeline import StockAnalysisPipeline
from src.services.data_object_admission import seal_source_rights_evidence


def _make_result(code: str) -> AnalysisResult:
    return AnalysisResult(
        code=code,
        name=f"Stock{code}",
        sentiment_score=80,
        trend_prediction="bullish",
        operation_advice="hold",
        analysis_summary="ok",
        success=True,
    )


def _b1_grant(dataset_id: str):
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": "test-b1-prefetch-veto",
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": "BaostockFetcher",
        "upstream_source_owner": "TEST_FIXTURE_ONLY_NOT_REAL_LICENSE",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": dataset_id,
        "consumer_scope": "ASSET_RESEARCH_BRIEF",
        "terms_uri": "https://example.org/test-fixture-only",
        "terms_sha256": "a" * 64,
        "terms_observed_at": "2026-10-01T00:00:00+00:00",
        "terms_scope": "SYNTHETIC_TEST_CONTRACT",
        "product_use_clause": "Synthetic fixture explicitly allows internal no-send research.",
        "product_use_clause_sha256": "c2a9b98537611cb0c9e9cb165ab0a4a699d84f5a2644a5e1e23740d0af55c6e1",
        "rights": {
            "product_use": "ALLOWED",
            "storage": "FORBIDDEN",
            "training": "FORBIDDEN",
            "redistribution": "FORBIDDEN",
        },
    })


def _make_b1_admitted_result(code: str) -> AnalysisResult:
    result = _make_result(code)
    result.dashboard = {
        "factor_decision": {
            "evidence_traceability": {
                "complete_research_universe": {
                    "data_object_admission": {
                        "objects": [
                            {
                                "dataset_id": dataset_id,
                                "product_admitted": True,
                                "learning_admitted": False,
                            }
                            for dataset_id in ("P_DAILY", "P_5M")
                        ]
                    }
                }
            }
        }
    }
    return result


class _TrackingFetcherManager:
    def __init__(self, events):
        self.events = events

    def prefetch_daily_klines(self, stock_codes, days=30):
        self.events.append(("daily_prefetch", list(stock_codes), days))
        return len(stock_codes)

    def prefetch_realtime_quotes(self, stock_codes):
        self.events.append(("realtime_prefetch", list(stock_codes)))
        return len(stock_codes)

    def prefetch_stock_names(self, stock_codes, use_bulk=False):
        self.events.append(("name_prefetch", list(stock_codes), use_bulk))
        return len(stock_codes)


class TestTickFlowPipelinePrefetch(unittest.TestCase):
    def test_run_prefetches_daily_klines_before_realtime_and_stock_processing(self):
        events = []
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.max_workers = 1
        pipeline.fetcher_manager = _TrackingFetcherManager(events)
        pipeline._save_local_report = MagicMock()
        pipeline._send_notifications = MagicMock()
        pipeline.config = SimpleNamespace(
            stock_list=[],
            refresh_stock_list=lambda: None,
            single_stock_notify=False,
            report_type="simple",
            analysis_delay=0,
        )

        def _process(code, skip_analysis=False, single_stock_notify=False, report_type=None, analysis_query_id=None, current_time=None):
            events.append(("process", code))
            return _make_result(code)

        pipeline.process_single_stock = MagicMock(side_effect=_process)

        results = pipeline.run(
            stock_codes=["AAPL", "MSFT", "GOOG", "AMZN", "NVDA"],
            dry_run=False,
            send_notification=False,
        )

        self.assertEqual(len(results), 5)
        self.assertEqual(events[0][0], "daily_prefetch")
        self.assertEqual(events[0][2], 30)
        self.assertEqual(events[1][0], "realtime_prefetch")
        self.assertEqual(events[2][0], "name_prefetch")
        self.assertTrue(all(event[0] != "process" for event in events[:3]))
        self.assertEqual([event[0] for event in events[3:]], ["process"] * 5)

    def test_cn_bulk_prefetch_cannot_borrow_unverified_provider_rights(self):
        events = []
        pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
        pipeline.max_workers = 1
        pipeline.fetcher_manager = _TrackingFetcherManager(events)
        pipeline._save_local_report = MagicMock()
        pipeline._send_notifications = MagicMock()
        pipeline.config = SimpleNamespace(
            stock_list=[], refresh_stock_list=lambda: None,
            single_stock_notify=False, report_type="simple", analysis_delay=0,
        )
        codes = ["600519", "000001", "300750", "000858", "601318"]
        pipeline.b1_rights_context = {
            "schema_version": "b1-operation-rights-context-v1",
            "operation_id": "test-b1-prefetch-veto",
            "intended_use": "NO_SEND_RESEARCH",
            "allowed_instruments": codes,
            "rights_by_dataset": {
                dataset_id: _b1_grant(dataset_id)
                for dataset_id in ("P_DAILY", "P_5M")
            },
        }
        pipeline._generate_aggregate_report = MagicMock(
            return_value="B1_PREFETCH_VETO_REPORT_IN_MEMORY"
        )
        pipeline.process_single_stock = MagicMock(
            side_effect=lambda code, **kwargs: _make_b1_admitted_result(code)
        )
        pipeline._preacquire_b1_sources = MagicMock(
            return_value=datetime(2026, 10, 23, 10, 5, tzinfo=timezone.utc)
        )
        results = pipeline.run(
            stock_codes=codes,
            send_notification=False,
        )
        self.assertEqual(len(results), 5)
        self.assertEqual(events, [])
        self.assertEqual(pipeline.process_single_stock.call_count, 5)
        pipeline._send_notifications.assert_not_called()
        pipeline._preacquire_b1_sources.assert_called_once()


if __name__ == "__main__":
    unittest.main()
