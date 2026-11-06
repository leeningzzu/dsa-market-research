"""Consumer regression: failed ranking refresh must never replay old leaders.

Provider ranking is source-only context; these tests do not invent Sector RS
admission or call any market API.
"""
from types import SimpleNamespace

import pytest

from src.market_analyzer import MarketAnalyzer, MarketOverview


@pytest.mark.parametrize("category", ("sector", "concept"))
@pytest.mark.parametrize("failure", ("empty", "exception"))
def test_failed_refresh_discards_stale_rankings_from_report(category, failure):
    manager = MarketAnalyzer.__new__(MarketAnalyzer)
    manager._log_context = lambda: "test-sector-refresh"
    manager._get_review_language = lambda: "zh"
    old = {"name": "STALE_LEADER_FROM_PREVIOUS_RUN", "change_pct": 9.99}
    overview = MarketOverview(
        date="2026-11-04",
        top_sectors=[dict(old)], bottom_sectors=[dict(old)],
        top_concepts=[dict(old)], bottom_concepts=[dict(old)],
    )
    def fresh(_n):
        if failure == "exception":
            raise RuntimeError("provider temporarily unavailable")
        return [], []
    manager.data_manager = SimpleNamespace(
        get_sector_rankings=fresh, get_concept_rankings=fresh
    )
    getattr(manager, "_get_" + category + "_rankings")(overview)
    assert getattr(overview, "top_" + ("sectors" if category == "sector" else "concepts")) == []
    assert getattr(overview, "bottom_" + ("sectors" if category == "sector" else "concepts")) == []
    # A separate surviving category is still evidence in its own right;
    # only the failed category must be removed from the consumer.
    text = manager._build_sector_block(overview)
    if category == "sector":
        assert "行业板块领涨" not in text and "行业板块领跌" not in text
    else:
        assert "概念板块领涨" not in text and "概念板块领跌" not in text


@pytest.mark.parametrize("category", ("sector", "concept"))
def test_successful_refresh_replaces_prior_rankings_without_dropping_valid_data(category):
    manager = MarketAnalyzer.__new__(MarketAnalyzer)
    manager._log_context = lambda: "test-sector-refresh"
    manager.data_manager = SimpleNamespace(
        get_sector_rankings=lambda _n: ([{"name": "NEW", "change_pct": 1.0}], []),
        get_concept_rankings=lambda _n: ([{"name": "NEW", "change_pct": 1.0}], []),
    )
    old = {"name": "OLD", "change_pct": 8.0}
    overview = MarketOverview(
        date="2026-11-04",
        top_sectors=[dict(old)], bottom_sectors=[dict(old)],
        top_concepts=[dict(old)], bottom_concepts=[dict(old)],
    )
    getattr(manager, "_get_" + category + "_rankings")(overview)
    plural = "sectors" if category == "sector" else "concepts"
    assert getattr(overview, "top_" + plural) == [{"name": "NEW", "change_pct": 1.0}]
    assert getattr(overview, "bottom_" + plural) == []
