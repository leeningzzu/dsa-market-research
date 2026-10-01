import json
from datetime import datetime
from types import SimpleNamespace

from src.services.factor_decision_summary import build_stock_factor_decision_summary
from src.services.history_comparison_service import _record_to_signal


def _record(**overrides):
    values = {
        "created_at": datetime(2026, 7, 11, 9, 0),
        "query_id": "q1",
        "sentiment_score": 72,
        "operation_advice": "Hold",
        "trend_prediction": "Bullish",
        "report_type": "stock",
        "report_language": "en",
        "raw_result": "{}",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _canonical_raw_result(*, hard_veto: bool = False) -> str:
    trend = SimpleNamespace(
        signal_score=72,
        trend_status="多头排列",
        buy_signal="买入",
        volume_status="放量下跌" if hard_veto else "缩量回调",
        risk_factors=[],
        current_price=10.0,
        support_levels=[9.5],
        resistance_levels=[10.5],
        ma_alignment="MA5>MA10>MA20",
        trend_strength=72,
        volume_ratio_5d=0.8,
        volume_trend="缩量回调",
        macd_signal="MACD多头结构",
        rsi_signal="RSI中性",
    )
    factor = build_stock_factor_decision_summary(trend, include_canonical=True)
    return json.dumps(
        {
            "report_language": "en",
            "dashboard": {"factor_decision": factor},
        },
        ensure_ascii=False,
    )


def test_legacy_history_without_canonical_identity_is_not_comparable() -> None:
    assert _record_to_signal(_record(), report_language="en") is None

    guarded_legacy = _record(
        raw_result=(
            '{"action":"hold","dashboard":{"decision_stability":'
            '{"applied":true,"reason":"Wait for confirmation"}}}'
        )
    )
    assert _record_to_signal(guarded_legacy, report_language="en") is None


def test_history_signal_uses_valid_canonical_public_action_not_legacy_score() -> None:
    signal = _record_to_signal(
        _record(raw_result=_canonical_raw_result()),
        report_language="en",
    )

    assert signal is not None
    assert signal["action"] == "watch"
    assert signal["action_label"] == "Watch"
    assert signal["sentiment_score"] == 72
    assert signal["trend_prediction"] == "Bullish"
    assert signal["canonical_identity"]["schema_version"] == "canonical-decision-binding-v1"


def test_history_signal_rejects_stale_canonical_identity() -> None:
    payload = json.loads(_canonical_raw_result())
    factor = payload["dashboard"]["factor_decision"]
    factor["canonical_decision"]["public_action"] = "avoid"

    signal = _record_to_signal(
        _record(raw_result=json.dumps(payload, ensure_ascii=False)),
        report_language="en",
    )

    assert signal is None
