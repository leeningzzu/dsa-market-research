# -*- coding: utf-8 -*-
"""Closed-world bindings over DSA owners; never an indicator or trading engine.

The registry describes capability. A trace observes outputs, not invocation or
provider entitlement. Deferred methods cannot be enabled by a payload flag.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from hashlib import sha256
import ast
import json
import math
from numbers import Real
from pathlib import Path
from typing import Any

from src.services.data_object_admission import (
    DataObjectAdmissionError,
    compile_data_object_admission_view,
)

MANIFEST_VERSION = "evidence-product-traceability-v1"
TRACE_VERSION = "canonical-evidence-trace-v2"
METHOD_EXECUTION_RECEIPT_VERSION = "method-execution-receipt-v1"
STOCK_STRATEGY = "stock_trend_quality_pullback_v1"
BASELINE_SHA256 = "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823"
BASELINE_BYTES = 125919
TIMEFRAMES = ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m")
STATES = frozenset({"READY", "PARTIAL", "MISSING", "UNKNOWN", "NOT_APPLICABLE"})
METHOD_WINDOW_POLICY_VERSION = "method-window-policy-view-v2"
METHOD_WINDOW_CLASSES = frozenset({
    "FIXED_ROLLING",
    "RECURSIVE_WARMUP",
    "ADAPTIVE_CONTEXT",
    "VARIABLE_STRUCTURE",
    "EVENT_ANCHORED",
    "CROSS_SECTIONAL_ASOF",
    "VINTAGE_ASOF",
    "INCREMENTAL_STATE_MACHINE",
})
METHOD_WINDOW_STATES = frozenset({
    "EXISTING_REUSED",
    "DESIGN_BOUND",
    "DEFERRED_WITH_OWNER_AND_REENTRY",
})
METHOD_WINDOW_TIMEFRAMES = frozenset({"asset", *TIMEFRAMES})
NON_DAILY_TECHNICAL_TIMEFRAMES = ("monthly", "weekly", "60m", "30m", "15m", "5m")
TECHNICAL_EVIDENCE_FAMILIES = (
    "REGIME", "TREND_RS", "SUPPLY", "COST", "STRUCTURE", "MOMENTUM", "PATTERN", "MTF",
)
METHOD_CONSUMER_SCOPES = frozenset({"TECHNICAL_MATRIX", "ASSET_LAYER", "RESEARCH_SHADOW", "MODEL_LAYER"})
TIMEFRAME_FAMILY_MATRIX_STATES = frozenset({
    "READY", "PARTIAL", "MISSING", "UNKNOWN", "NOT_APPLICABLE", "NOT_ADMITTED",
})

# Read-only point-of-use projection of the accepted 2026-10-10 machine graph.
# The original 149628-byte 775-edge graph remains immutable and authoritative.
# These data obligations are NOT provider outputs, market-data rights or model features.
ACCEPTED_DATA_OBJECT_GRAPH_SHA256 = "b4f69f76807ac44bc0fea1a03f9aa38484b6083ca574a3699220b0564ac55aed"
ACCEPTED_DATA_OBJECT_SUBSET_SHA256 = "2abf66734296638b5d55ee0cfa863ae57dbabeefb70dd481860eba6adc9e789a"
_DATA_OBJECT_CATALOG = (
    ("GLOBAL", "MARKET_GLOBAL_TRANSMISSION", (
        ("G_EQ_US", "标普/纳斯达克/道指权益指数价格与成交", "daily/close"),
        ("G_EQ_ASIA", "日经/恒生/恒生科技价格", "daily/close"),
        ("G_VIX", "VIX和类型正确的期权隐波压力", "daily/close"),
        ("G_VIX3M_MOVE", "VIX3M/MOVE/VVIX及期限波动", "daily/weekly"),
        ("G_UST_NOMINAL", "US 2Y/10Y及10Y-3M期限利差", "daily/close"),
        ("G_UST_REAL", "10Y TIPS实际收益率DFII10", "daily/close"),
        ("G_INFL_BREAKEVEN", "10Y通胀补偿T10YIE", "daily/close"),
        ("G_USD_BROAD", "贸易加权美元DTWEXBGS/ICE DXY另名", "daily/close"),
        ("G_USDCNH", "离岸人民币USD/CNH", "daily/intraday_available"),
        ("G_A50", "A50期货或合法指数代理", "session/close"),
        ("G_CRUDE", "WTI/Brent原油", "daily/contract"),
        ("G_GOLD", "黄金现货或合约可核价格", "daily/contract"),
        ("G_COPPER", "铜价现货或期货", "daily/contract"),
        ("G_HYOAS", "HY信用利差ICE/FRED", "daily/close"),
        ("G_NFCI", "NFCI/ANFCI金融条件指数", "weekly/published"),
        ("G_US_POLICY", "联储政策/公告日历", "event/published"),
        ("G_US_SENTIMENT", "AAII/COT等调查/仓位代理", "weekly/published"),
        ("G_US_DISCLOSURE", "海外公司财报/科技重大事件公告", "event/published"),
    )),
    ("CN_MARKET", "MARKET_REGIME_BRIEF", (
        ("M_INDEX_CURRENT", "上证深证/沪深300/上证50/中证500/1000/2000/创业板/科创50指数现值", "daily/close"),
        ("M_INDEX_BARS", "代表指数完整OHLCV 1d/5m", "1d+5m ->7TF"),
        ("M_BASIC_BREADTH", "上涨/下跌/平盘家数+分母", "daily/market_close"),
        ("M_UP_ABOVE_MA", "全市场站上20/50/200日线家数与当时分母", "daily/universe"),
        ("M_NEW_HILO", "52周/一定窗口新高新低家数", "daily/universe"),
        ("M_MEDIAN_EQUAL", "当日收益中位数/等权收益及全市场分母", "daily/universe"),
        ("M_LIMIT", "涨跌停家数与涨停类型", "daily/market_close"),
        ("M_BROKEN_LIMIT", "炸板率/连板晋级率及定义分母", "daily/market_close"),
        ("M_TURNOVER", "两市成交额/换手/相对量", "daily/close"),
        ("M_MARGIN", "融资余额/买入/偿还/融券余额", "daily/published_next"),
        ("M_ETF_SHARES", "行业宽基ETF份额/规模变化", "daily/published_next"),
        ("M_NORTHBOUND", "互联互通成交/季度持仓", "daily/quarterly"),
        ("M_PBOC_MONEY", "M1/M2/社融原始和可比口径", "monthly/released"),
        ("M_CPI_PPI_PMI", "CPI/PPI/PMI及分项", "monthly/released"),
        ("M_INTERBANK", "DR007/R007/FR/FDR/回购定盘", "daily/intraday_publish"),
        ("M_CN_BOND", "国债收益率/期限和信用参考", "daily/released"),
        ("M_ERP_VAL", "主要指数估值分位与ERP收益补偿", "daily/period"),
        ("M_ASOF_UNIVERSE", "历史全A、上市/退市/停牌/指数成员版本", "event+daily/known_at"),
    )),
    ("SECTOR", "MARKET_SECTOR_SECTION", (
        ("S_CLASSIFICATION", "行业标准/版本/证券归属有效期", "event/known_at"),
        ("S_PRICES_RS", "行业/主题指数OHLCV及一致基准", "daily/weekly"),
        ("S_BREADTH", "行业内成员涨跌分母/MA/创新高低", "daily/universe"),
        ("S_TURNOVER", "行业成分成交/占全A成交额比例", "daily/close"),
        ("S_FUND_FLOW", "供应商行业资金流代理/ETF份额", "daily/source_method"),
        ("S_LEADER", "候选行业leader个股RS/结构/流动性/基本面", "daily/known_at"),
        ("S_VALUATION", "行业盈利和市值分位/估值差异", "quarter+daily"),
    )),
    ("ETF", "ETF_ASSET_BRIEF", (
        ("E_PRICE_1D", "ETF 1d OHLCV、价格/成交额", "daily/close"),
        ("E_PRICE_5M", "ETF 5m以及15/30/60m派生", "5m -> 60/30/15/5"),
        ("E_NAV_IOPV", "ETF NAV/iNAV/IOPV时间戳和币种", "daily+intraday_published"),
        ("E_SHARES", "ETF份额/拆分/申赎及净值匹配", "daily/published_next"),
        ("E_PREMIUM", "交易价与NAV/IOPV同一时点对照", "intraday+daily"),
        ("E_SPREAD", "ETF bid/ask、成交额、盘口深度", "intraday/orderbook"),
        ("E_TRACKING", "ETF基准总回报/净值/费用", "daily/rolling"),
        ("E_BASKET", "ETF持仓/篮子权重/指数成员版本", "daily/event"),
        ("E_PEER_RS", "同类ETF同基准价格/交易量/历史", "daily+weekly"),
        ("E_UNDERLYING_VAL", "底层指数盈利/估值/币种和成分", "quarter+daily"),
        ("E_FX_CROSS", "跨境ETF FX/海外交易时点、限额", "daily+intraday"),
        ("E_FEES", "费率/交易规则/红利处理", "event/published"),
    )),
    ("STOCK", "ASSET_RESEARCH_BRIEF", (
        ("P_DAILY", "个股1d OHLCV价格/量/额/复权", "daily/completed"),
        ("P_5M", "个股5m completed bars以及分钟单位", "5m/completed"),
        ("P_PRICE_FACTOR", "raw/qfq/hfq/分红送配除权因子与vintage", "event+daily"),
        ("P_CODE_IDENTITY", "证券代码/市场/交易所/板块/公司行为", "event/known_at"),
        ("P_QUOTE_LIQUID", "当前价格、成交量、换手、市值", "intraday+daily"),
        ("P_FIN_VINTAGE", "财报报告期/公告发布/更正版本/时间", "quarter+event"),
        ("P_EARNINGS_GROWTH", "收入/利润/增长/毛利和指引", "quarter+event"),
        ("P_CASH_FLOW", "经营现金流/资本开支/FCFF/FCFE材料", "quarter+event"),
        ("P_BALANCE_SHEET", "债务/资产/营运资本/偿债与银行质量", "quarter+event"),
        ("P_SHARES_EVENTS", "股本/股权激励/回购/注销/配股实际完成时点", "event/known_at"),
        ("P_DIVIDEND", "现金分红/派息频率/除息", "event/known_at"),
        ("P_INDUSTRY", "业务分类/收入结构/行业口径版本", "annual+event"),
        ("P_VAL_RATIO", "PE/PB/市值/EV基础比率或价格", "daily+quarter"),
        ("P_ORDERFLOW_PROXY", "供应商主力净流入/行业资金代理", "daily/vendor"),
        ("P_COST_PROXY", "chip或HLC3量权滚动参考", "daily/rolling"),
        ("P_EVENT_NEWS", "公告/新闻全文与claim发布日期/链接", "event/published"),
        ("P_EXEC_RISK", "停牌涨跌停/买卖价差/费率/流动性", "session+event"),
    )),
    ("RECORD", "HISTORY_LEDGER", (
        ("L_LEDGER", "一个canonical决策/完整机会分母/intent及权益", "decision_time"),
        ("L_OUTCOME", "后验可评价结果/净成本/未触发/未成交", "after_label_maturity"),
        ("L_PIT_MANIFEST", "合法特征的asof/vintage/rights/strategy/data/code/schema", "frozen_dataset"),
        ("L_IMMUTABLE_BYTES", "合法引用的source/universe/actions/feature原始包/manifest/hash", "frozen_artifact"),
    )),
)

VALID_ASSET_ROUTES = frozenset({"STOCK", "ETF", "MARKET"})
VALID_BINDING_TIMEFRAMES = frozenset({"asset", "daily", "multi", *TIMEFRAMES})
VALID_METRIC_TIMEFRAMES = frozenset({"daily", "weekly", "monthly"})
VALID_METRIC_UNITS = frozenset({
    "PRICE_BASIS_CURRENCY", "PRICE_BASIS_CURRENCY_PROXY", "ratio", "pct",
    "annualized_pct", "index_0_100", "METHOD_NOT_ADMITTED",
})

# This accepted contract is intentionally independent from the candidate tuples
# under validation. A self-consistent deletion therefore cannot certify itself.
ACCEPTED_REQUIREMENT_ROLES = frozenset({
    ("REGIME", "ADMITTED"), ("TREND_RS", "ADMITTED"), ("SUPPLY", "ADMITTED"),
    ("COST", "ADMITTED"), ("STRUCTURE", "ADMITTED"), ("MOMENTUM", "ADMITTED"),
    ("PATTERN", "ADMITTED"), ("MTF", "ADMITTED"),
    ("QUALITY", "ADMITTED"), ("VALUATION", "ADMITTED"), ("DISTRIBUTION", "DEFERRED"),
    ("RISK_REWARD", "DEFERRED"), ("CANDLESTICK", "ADMITTED"),
    ("EXTRA_INDICATORS", "DEFERRED"), ("AVWAP_PROFILE", "DEFERRED"),
    ("CHAN", "DEFERRED"), ("WAVE", "DEFERRED"), ("ETF_SPECIFIC", "DEFERRED"),
    ("GLOBAL", "DEFERRED"), ("BREADTH", "DEFERRED"), ("PROBABILITY", "DEFERRED"),
    ("INTRADAY_60m", "DEFERRED"), ("INTRADAY_30m", "DEFERRED"),
    ("INTRADAY_15m", "DEFERRED"), ("INTRADAY_5m", "DEFERRED"),
    ("DECISION", "DECISION"),
})


COMPLETE_RESEARCH_UNIVERSE_SCHEMA_VERSION = "complete-research-universe-view-v1"
COMPLETE_RESEARCH_UNIVERSE_PLANES = (
    (
        "TECHNICAL_EVIDENCE",
        (
            "REGIME", "TREND_RS", "SUPPLY", "COST", "STRUCTURE", "MOMENTUM",
            "PATTERN", "MTF", "CANDLESTICK", "EXTRA_INDICATORS", "AVWAP_PROFILE",
            "INTRADAY_60m", "INTRADAY_30m", "INTRADAY_15m", "INTRADAY_5m",
        ),
    ),
    ("FUNDAMENTAL_AND_STRATEGY", ("QUALITY", "VALUATION", "DISTRIBUTION", "RISK_REWARD")),
    ("MARKET_AND_ASSET", ("GLOBAL", "BREADTH", "ETF_SPECIFIC")),
    ("RESEARCH_SHADOW", ("CHAN", "WAVE")),
    ("MODEL_LAYER", ("PROBABILITY",)),
    ("DECISION", ("DECISION",)),
)


class TraceabilityError(ValueError):
    """A binding or claimed observation exceeds its actual proof."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def json_wire_equal(left: Any, right: Any) -> bool:
    """Compare exact persisted/runtime JSON semantics without Python sequence-shape drift."""
    try:
        return canonical_json(left) == canonical_json(right)
    except (TypeError, ValueError):
        return False


def digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()

METHOD_WINDOW_READINESS_TRACE_VERSION = "asset-timeframe-method-window-readiness-v1"


def compile_mtf_method_window_readiness_trace(
    *,
    asset_route: str,
    instrument: str,
    target_date: Any,
    daily_window_plan: Any,
    intraday_window_plan: Any,
    multi_timeframe_structure_context: Any,
    intraday_source_frames: Any = None,
    daily_source_frame: Any = None,
    formal_market_source_frames: Any = None,
    formal_sector_source_frames: Any = None,
) -> dict:
    """Join existing acquisition plans with actual native MACD/RSI readiness.

    This is a run-local trace projection, not a second planner, formula owner,
    strategy score, data-rights grant, persistence permit, or learning label.
    """

    route = str(asset_route or "").strip().upper()
    code = str(instrument or "").strip()
    target = (
        target_date.isoformat()
        if hasattr(target_date, "isoformat")
        else str(target_date or "").strip()
    )
    base = {
        "schema_version": METHOD_WINDOW_READINESS_TRACE_VERSION,
        "request_semantics_version": "planned-dispatched-separated-v1",
        "asset_route": route,
        "instrument": code,
        "target_date": target,
        "status": "UNKNOWN",
        "reason": "TRACE_INPUT_NOT_ADMITTED",
        "rows": [],
        "strategy_admitted": False,
        "learning_admitted": False,
        "storage_admitted": False,
        "consumer_scope": (
            "Factor.MTF",
            "Registry.timeframe_family_matrix.MOMENTUM",
            "Notification.seven_timeframe",
        ),
    }

    def finish(document: dict) -> dict:
        document = dict(document)
        document["trace_hash"] = digest(document)
        return document

    if route not in {"MARKET", "SECTOR", "ETF", "STOCK"} or not code or not target:
        return finish(base)
    if not all(isinstance(value, Mapping) for value in (
        daily_window_plan, intraday_window_plan, multi_timeframe_structure_context,
    )):
        return finish(base)
    if str(multi_timeframe_structure_context.get("target_date") or "") != target:
        return finish({**base, "reason": "TARGET_DATE_MISMATCH"})
    source_instrument = str(
        multi_timeframe_structure_context.get("stock_code") or ""
    ).strip()
    if not source_instrument:
        return finish({**base, "reason": "MTF_INSTRUMENT_IDENTITY_MISSING"})
    if source_instrument != code:
        return finish({**base, "reason": "MTF_INSTRUMENT_IDENTITY_MISMATCH"})
    if str(daily_window_plan.get("target_date") or "") != target:
        return finish({**base, "reason": "DAILY_PLAN_TARGET_DATE_MISMATCH"})
    plan_hash = str(daily_window_plan.get("plan_hash") or "")
    if len(plan_hash) != 64:
        return finish({**base, "reason": "DAILY_PLAN_IDENTITY_MISSING"})
    plan_identity_keys = (
        "version", "market", "target_date", "timeframe_required_bars",
        "source_only_timeframe_required_bars", "method_required_bars",
        "method_window_classes", "mtf_recursive_comparison_required_bars",
        "mtf_recursive_calendar_status",
    )
    if digest({key: daily_window_plan.get(key) for key in plan_identity_keys}) != plan_hash:
        return finish({**base, "reason": "DAILY_PLAN_HASH_MISMATCH"})

    from src.data.stock_mapping import resolve_cn_price_asset

    expected_asset = resolve_cn_price_asset(code)
    if (
        route in {"STOCK", "ETF"} and expected_asset["market"] == "cn"
        and (expected_asset["asset_route"] != route or expected_asset["status"] != "ADMITTED")
    ):
        return finish({**base, "reason": "PRICE_ASSET_ADMISSION_BINDING_INVALID"})
    asset_admission = multi_timeframe_structure_context.get("asset_admission")
    if asset_admission is not None:
        if (
            asset_admission != expected_asset
            or expected_asset["status"] != "ADMITTED"
            or expected_asset["asset_route"] != route
        ):
            return finish({**base, "reason": "PRICE_ASSET_ADMISSION_BINDING_INVALID"})

    frames = multi_timeframe_structure_context.get("timeframes")
    daily_frame_requirements = daily_window_plan.get("method_required_bars")
    daily_requested = daily_window_plan.get("timeframe_required_bars")
    recursive_requirements = daily_window_plan.get(
        "mtf_recursive_comparison_required_bars"
    )
    recursive_plan_states = daily_window_plan.get("mtf_recursive_calendar_status")
    minute_requirements = intraday_window_plan.get(
        "method_required_bars_by_timeframe"
    )
    minute_sessions = intraday_window_plan.get("admission_sessions_by_timeframe")
    bars_per_session = intraday_window_plan.get("bars_per_session")
    if not all(isinstance(value, Mapping) for value in (
        frames, daily_frame_requirements, daily_requested,
        recursive_requirements, recursive_plan_states,
        minute_requirements, minute_sessions, bars_per_session,
    )):
        return finish({**base, "reason": "PLAN_OR_RUNTIME_SHAPE_INVALID"})

    daily_acquisition = None
    daily_source_count = None
    daily_source_hash = None
    formal_market_receipts: dict[str, Any] = {}
    formal_sector_receipts: dict[str, Any] = {}
    formal_frames = (
        formal_market_source_frames
        if isinstance(formal_market_source_frames, Mapping)
        else {}
    )
    sector_frames = (
        formal_sector_source_frames
        if isinstance(formal_sector_source_frames, Mapping)
        else {}
    )
    if route == "MARKET" and formal_frames:
        try:
            from datetime import date
            import pandas as pd
            from data_provider.baostock_fetcher import (
                verify_cn_index_completed_close_source_frame,
            )
            from data_provider.cn_index_minute_source import (
                verify_cn_index_completed_5m_source_frame,
            )
            from src.core.trading_calendar import resolve_session_range_fail_closed

            market_daily = formal_frames.get("daily")
            market_five = formal_frames.get("5m")
            daily_observation = verify_cn_index_completed_close_source_frame(
                market_daily, trade_date=target,
            )
            minute_observation = verify_cn_index_completed_5m_source_frame(
                market_five, trade_date=target,
            )
            if (
                daily_observation.get("source_code") != code
                or minute_observation.get("source_code") != code
            ):
                raise ValueError("formal market source instrument mismatch")
            raw_daily = getattr(market_daily, "attrs", {}).get(
                "cn_index_completed_close_source"
            )
            if not isinstance(raw_daily, Mapping):
                raise ValueError("formal market daily receipt missing")
            request_start = str(raw_daily.get("requested_start") or "")
            request_end = str(raw_daily.get("requested_end") or "")
            if request_end != target or not request_start:
                raise ValueError("formal market daily request range invalid")
            sessions = resolve_session_range_fail_closed(
                "cn", date.fromisoformat(request_start), date.fromisoformat(target),
            )
            returned_dates = [
                value.date()
                for value in pd.to_datetime(market_daily["date"], errors="raise")
            ]
            if not sessions or returned_dates != sessions:
                raise ValueError("formal market daily calendar binding mismatch")
            daily_acquisition = {
                "schema_version": "formal-cn-market-index-daily-source-v1",
                "acquisition_mode": "VERIFIED_FORMAL_MARKET_INDEX_PROVIDER_CALL",
                "request_evidence_status": "VERIFIED_FORMAL_MARKET_INDEX_PROVIDER_CALL",
                "instrument": code,
                "target_date": target,
                "requested_session_count": len(sessions),
                "actual_request_start": request_start,
                "actual_request_end": target,
                "cache_lookup_start": None,
                "cache_lookup_end": None,
                "receipt_hash": daily_observation["identity_hash"],
                "persistence_status": "RIGHTS_NOT_ADMITTED_RUN_LOCAL_ONLY",
            }
            daily_source_count = len(market_daily)
            daily_source_hash = daily_observation["identity_hash"]
            formal_market_receipts = {
                "daily": daily_observation,
                "5m": minute_observation,
            }
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            return finish({**base, "reason": "FORMAL_MARKET_SOURCE_BINDING_INVALID"})
    if route == "SECTOR" and sector_frames:
        try:
            from datetime import date
            import pandas as pd
            from src.services.formal_sector_context_service import (
                verify_formal_cn_sector_5m_source_frame,
                verify_formal_cn_sector_daily_source_frame,
            )
            from src.core.trading_calendar import resolve_session_range_fail_closed

            sector_daily = sector_frames.get("daily")
            sector_five = sector_frames.get("5m")
            daily_observation = verify_formal_cn_sector_daily_source_frame(
                sector_daily,
                target_date=target,
                expected_price_code=code,
            )
            minute_observation = verify_formal_cn_sector_5m_source_frame(
                sector_five,
                target_date=target,
                expected_price_code=code,
            )
            if (
                daily_observation.get("price_code") != code
                or minute_observation.get("price_code") != code
                or daily_observation.get("sector_id")
                != minute_observation.get("sector_id")
                or daily_observation.get("standard_id")
                != minute_observation.get("standard_id")
                or daily_observation.get("version")
                != minute_observation.get("version")
            ):
                raise ValueError("formal sector source identity mismatch")
            request_start = str(daily_observation.get("requested_start") or "")
            request_end = str(daily_observation.get("requested_end") or "")
            if request_end != target or not request_start:
                raise ValueError("formal sector daily request range invalid")
            sessions = resolve_session_range_fail_closed(
                "cn", date.fromisoformat(request_start), date.fromisoformat(target),
            )
            returned_dates = [
                value.date()
                for value in pd.to_datetime(sector_daily["date"], errors="raise")
            ]
            if not sessions or returned_dates != sessions:
                raise ValueError("formal sector daily calendar binding mismatch")
            daily_acquisition = {
                "schema_version": "formal-cn-sector-daily-source-v1",
                "acquisition_mode": "VERIFIED_FORMAL_SECTOR_PROVIDER_CALL",
                "request_evidence_status": "VERIFIED_FORMAL_SECTOR_PROVIDER_CALL",
                "instrument": code,
                "target_date": target,
                "requested_session_count": len(sessions),
                "actual_request_start": request_start,
                "actual_request_end": target,
                "cache_lookup_start": None,
                "cache_lookup_end": None,
                "receipt_hash": daily_observation["identity_hash"],
                "persistence_status": "RIGHTS_NOT_ADMITTED_RUN_LOCAL_ONLY",
            }
            daily_source_count = len(sector_daily)
            daily_source_hash = daily_observation["identity_hash"]
            formal_sector_receipts = {
                "daily": daily_observation,
                "5m": minute_observation,
            }
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            return finish({**base, "reason": "FORMAL_SECTOR_SOURCE_BINDING_INVALID"})

    daily_claim = (
        None
        if daily_acquisition is not None
        else getattr(daily_source_frame, "attrs", {}).get(
            "dsa_daily_acquisition_receipt"
        )
    )
    if daily_acquisition is None and daily_source_frame is not None and daily_claim is None:
        return finish({**base, "reason": "DAILY_ACQUISITION_BINDING_INVALID"})
    if daily_acquisition is None and daily_claim is not None:
        try:
            from datetime import date
            import pandas as pd
            from data_provider.daily_data_identity import (
                extract_daily_acquisition_receipt,
                extract_daily_data_identity,
                verified_observed_daily_frame_provider,
            )
            from src.core.trading_calendar import resolve_session_range_fail_closed

            daily_identity = extract_daily_data_identity(
                daily_source_frame, strict=True,
            )
            daily_receipt = extract_daily_acquisition_receipt(
                daily_source_frame, strict=True,
            )
            if daily_identity is None or daily_receipt is None:
                raise ValueError("missing daily acquisition identity")
            if (
                route == "ETF" and expected_asset["market"] == "cn"
                and daily_identity.get("currency")
                != expected_asset["product_profile"]["currency"]
            ):
                raise ValueError("ETF daily listing currency mismatch")
            provider = verified_observed_daily_frame_provider(daily_source_frame)
            if provider is None:
                raise ValueError("daily source bytes are not content-bound")
            expected_daily = {
                "schema_version": "dsa-daily-acquisition-v1",
                "caller": "StockAnalysisPipeline.fetch_and_save_stock_data",
                "asset_route": route,
                "instrument": code,
                "target_date": target,
                "plan_hash": plan_hash,
                "source_identity_hash": daily_identity["identity_hash"],
                "provider_identity": daily_identity["provider_identity"],
                "provider_route": daily_identity["provider_route"],
                "returned_start": daily_identity["returned_start"],
                "returned_end": daily_identity["returned_end"],
                "returned_base_bars": len(daily_source_frame),
                "admitted_base_bars": len(daily_source_frame),
                "actual_consumer": "StockAnalysisPipeline.analyze_stock",
            }
            if asset_admission is not None:
                expected_daily["asset_admission"] = asset_admission
            if any(
                daily_receipt.get(key) != value
                for key, value in expected_daily.items()
            ):
                raise ValueError("daily acquisition request/response mismatch")
            if provider.casefold() != str(
                daily_identity.get("provider_identity") or ""
            ).casefold():
                raise ValueError("daily provider binding mismatch")
            if str(daily_identity.get("instrument") or "") != code:
                raise ValueError("daily instrument binding mismatch")
            plan_start_value = daily_window_plan.get("start_date")
            plan_start = (
                plan_start_value.isoformat()
                if hasattr(plan_start_value, "isoformat")
                else str(plan_start_value or "")
            )
            if (
                daily_receipt.get("planned_start") != plan_start
                or daily_receipt.get("planned_end") != target
            ):
                raise ValueError("daily acquisition plan range mismatch")
            parsed = pd.to_datetime(
                daily_source_frame["date"], errors="coerce",
            )
            if parsed.isna().any():
                raise ValueError("daily source contains invalid dates")
            returned_dates = [value.date() for value in parsed]
            if len(set(returned_dates)) != len(returned_dates):
                raise ValueError("daily source contains duplicate dates")
            mode = daily_receipt.get("acquisition_mode")
            if mode == "VERIFIED_PROVIDER_CALL":
                count = daily_receipt.get("requested_session_count")
                if type(count) is not int or count < 1:
                    raise ValueError("daily provider request count is invalid")
                if (
                    daily_receipt.get("actual_request_start") != plan_start
                    or daily_receipt.get("actual_request_end") != target
                    or daily_receipt.get("cache_lookup_start") is not None
                    or daily_receipt.get("cache_lookup_end") is not None
                ):
                    raise ValueError("daily provider request range is invalid")
                sessions = resolve_session_range_fail_closed(
                    str(daily_receipt.get("market") or ""),
                    date.fromisoformat(plan_start),
                    date.fromisoformat(target),
                )
                if (
                    not sessions
                    or len(sessions) != count
                    or returned_dates != sessions[-len(returned_dates):]
                ):
                    raise ValueError("daily provider calendar binding mismatch")
            elif mode == "VERIFIED_CACHE_HIT":
                if (
                    daily_receipt.get("requested_session_count") is not None
                    or daily_receipt.get("actual_request_start") is not None
                    or daily_receipt.get("actual_request_end") is not None
                    or daily_receipt.get("cache_lookup_start") != plan_start
                    or daily_receipt.get("cache_lookup_end") != target
                ):
                    raise ValueError("daily cache lookup identity is invalid")
                sessions = resolve_session_range_fail_closed(
                    str(daily_receipt.get("market") or ""),
                    date.fromisoformat(plan_start),
                    date.fromisoformat(target),
                )
                if not sessions or returned_dates != sessions:
                    raise ValueError("daily cache calendar binding mismatch")
            else:
                raise ValueError("daily acquisition mode is invalid")
            daily_acquisition = dict(daily_receipt)
            daily_source_count = len(daily_source_frame)
            daily_source_hash = daily_identity["identity_hash"]
        except (
            ValueError, TypeError, KeyError, AttributeError, IndexError,
        ):
            return finish({**base, "reason": "DAILY_ACQUISITION_BINDING_INVALID"})

    acquisition = None
    daily_window_extension = (
        daily_acquisition.get("bounded_window_extension")
        if isinstance(daily_acquisition, Mapping) else None
    )
    source_counts = {}
    source_hashes = {}
    source_frames = intraday_source_frames if isinstance(intraday_source_frames, Mapping) else {}
    formal_minute_acquisition = None
    active_formal_receipts = (
        formal_market_receipts
        if route == "MARKET"
        else formal_sector_receipts
        if route == "SECTOR"
        else {}
    )
    active_formal_frames = formal_frames if route == "MARKET" else sector_frames
    if route in {"MARKET", "SECTOR"} and active_formal_receipts:
        source_frames = active_formal_frames
        try:
            from data_provider.intraday_data_identity import extract_intraday_data_identity

            parent = extract_intraday_data_identity(source_frames["5m"], strict=True)
            if parent is None:
                raise ValueError("formal market/sector 5m identity missing")
            minute_receipt = active_formal_receipts["5m"]
            formal_minute_acquisition = {
                "schema_version": (
                    "formal-cn-market-index-5m-source-v1"
                    if route == "MARKET"
                    else "formal-cn-sector-5m-source-v1"
                ),
                "request_evidence_status": "VERIFIED_PROVIDER_RESPONSE_WITH_UNBOUNDED_START",
                "instrument": code,
                "target_date": target,
                "actual_request_start": None,
                "actual_request_end": target,
                "receipt_hash": minute_receipt["receipt_sha256"],
                "session_count": minute_receipt["session_count"],
                "row_count": minute_receipt["row_count"],
            }
            for timeframe, data in source_frames.items():
                if timeframe not in {"5m", "15m", "30m", "60m"}:
                    continue
                child = extract_intraday_data_identity(data, strict=True)
                if (
                    child is None
                    or child.get("timeframe") != timeframe
                    or set(data["code"].astype(str)) != {code}
                    or (
                        timeframe != "5m"
                        and child.get("parent_identity_hash") != parent["identity_hash"]
                    )
                ):
                    raise ValueError("formal market/sector derived timeframe identity mismatch")
                source_counts[timeframe] = len(data)
                source_hashes[timeframe] = child["identity_hash"]
        except (ValueError, TypeError, KeyError, AttributeError):
            return finish({
                **base,
                "reason": (
                    "FORMAL_MARKET_SOURCE_BINDING_INVALID"
                    if route == "MARKET"
                    else "FORMAL_SECTOR_SOURCE_BINDING_INVALID"
                ),
            })
    five_minute = source_frames.get("5m")
    claim = getattr(five_minute, "attrs", {}).get("dsa_intraday_acquisition_receipt")
    if claim is not None:
        # Verify the original call receipt against the same source bytes. This
        # proves a run-local provider-method seam, not vendor rights or HTTP I/O.
        try:
            from datetime import date, timedelta
            from data_provider.daily_data_identity import sha256_payload
            from data_provider.intraday_data_identity import extract_intraday_data_identity
            from src.core.trading_calendar import resolve_forward_sessions_fail_closed

            identity = extract_intraday_data_identity(five_minute, strict=True)
            if not isinstance(claim, Mapping) or identity is None:
                raise ValueError("missing acquisition identity")
            expected = {
                "schema_version": "dsa-intraday-acquisition-v1",
                "caller": "StockAnalysisPipeline._load_current_stock_intraday_timeframes",
                "asset_route": route, "instrument": code, "target_date": target,
                "source_identity_hash": identity["identity_hash"],
                "provider_identity": identity["provider_identity"],
                "provider_route": identity["provider_route"],
                "requested_start": identity["requested_start"],
                "requested_end": identity["requested_end"],
                "returned_base_bars": len(five_minute),
                "provider_method_call_count": 1, "provider_http_request_count": None,
            }
            if asset_admission is not None:
                expected["asset_admission"] = asset_admission
            if any(claim.get(key) != value for key, value in expected.items()):
                raise ValueError("acquisition request/response mismatch")
            payload = {key: value for key, value in claim.items() if key != "receipt_hash"}
            if claim.get("receipt_hash") != sha256_payload(payload):
                raise ValueError("acquisition receipt hash mismatch")
            count = claim.get("requested_session_count")
            if type(count) is not int or count < 1 or count > 10000:
                raise ValueError("invalid acquisition session count")
            start = date.fromisoformat(claim["requested_start"])
            sessions = resolve_forward_sessions_fail_closed("cn", start - timedelta(days=1), count)
            if (
                not sessions
                or [day.isoformat() for day in sessions] != claim.get("requested_session_dates")
                or sessions[-1].isoformat() != target
                or claim["requested_end"] != target
                or set(five_minute["code"].astype(str)) != {code}
            ):
                raise ValueError("acquisition calendar/instrument mismatch")
            for timeframe, data in source_frames.items():
                if timeframe not in {"5m", "15m", "30m", "60m"}:
                    continue
                child = extract_intraday_data_identity(data, strict=True)
                if (
                    child is None or child["timeframe"] != timeframe
                    or set(data["code"].astype(str)) != {code}
                    or (timeframe != "5m" and child.get("parent_identity_hash") != identity["identity_hash"])
                ):
                    raise ValueError("acquisition derived source mismatch")
                source_counts[timeframe] = len(data)
                source_hashes[timeframe] = child["identity_hash"]
            if (
                type(claim.get("admitted_session_count")) is not int
                or claim["admitted_session_count"] * 48 != len(five_minute)
                or claim["admitted_session_count"] > count
            ):
                raise ValueError("acquisition admitted coverage mismatch")
            acquisition = dict(claim)
        except (ValueError, TypeError, KeyError, AttributeError):
            return finish({**base, "reason": "ACQUISITION_BINDING_INVALID"})

    component_fields = (
        ("MACD_STARTUP_COMPARISON", "macd_startup_stability", "MACD"),
        ("RSI_STARTUP_COMPARISON", "rsi_startup_stability", "RSI"),
    )
    rows = []
    for timeframe in TIMEFRAMES:
        frame = frames.get(timeframe)
        if not isinstance(frame, Mapping):
            frame = {}
        returned = frame.get("observations")
        returned_bars = (
            returned if type(returned) is int and returned >= 0 else None
        )
        for component, field, daily_method in component_fields:
            if timeframe in {"monthly", "weekly", "daily"}:
                if timeframe == "daily":
                    requirements = daily_frame_requirements.get("daily")
                    required = (
                        requirements.get(daily_method)
                        if isinstance(requirements, Mapping)
                        else None
                    )
                else:
                    required = recursive_requirements.get(component)
                requested = daily_requested.get(timeframe)
                plan_states = recursive_plan_states.get(timeframe)
                plan_state = (
                    plan_states.get(component)
                    if isinstance(plan_states, Mapping)
                    else "ACQUISITION_PLANNED"
                )
            else:
                requirements = minute_requirements.get(timeframe)
                required = (
                    requirements.get(component)
                    if isinstance(requirements, Mapping)
                    else None
                )
                sessions = minute_sessions.get(timeframe)
                per_session = bars_per_session.get(timeframe)
                requested = (
                    sessions * per_session
                    if type(sessions) is int and type(per_session) is int
                    else None
                )
                plan_state = "ACQUISITION_PLANNED"

            receipt = frame.get(field)
            state = str((receipt or {}).get("status") or "MISSING")
            if state not in {"READY", "PARTIAL", "MISSING", "UNKNOWN"}:
                state = "UNKNOWN"
            reason = str(
                (receipt or {}).get("reason") or "METHOD_EXECUTION_RECEIPT_MISSING"
            )
            if not isinstance(receipt, Mapping):
                state, reason = "MISSING", "METHOD_EXECUTION_RECEIPT_MISSING"
            elif receipt.get("strategy_admitted") is not False or receipt.get(
                "learning_admitted"
            ) is not False:
                state, reason = "UNKNOWN", "METHOD_RECEIPT_AUTHORITY_ESCALATION"
            elif type(required) is int and receipt.get("required_observations") != required:
                state, reason = "UNKNOWN", "METHOD_REQUIRED_BARS_MISMATCH"
            elif type(returned_bars) is int and receipt.get("observations") != returned_bars:
                state, reason = "UNKNOWN", "METHOD_OBSERVATION_COUNT_MISMATCH"
            elif (
                state == "READY"
                and type(required) is int
                and type(returned_bars) is int
                and returned_bars < required
            ):
                state, reason = "UNKNOWN", "OBSERVATIONS_BELOW_REQUIRED_BARS"
            if timeframe in source_counts and returned_bars is not None and returned_bars > source_counts[timeframe]:
                state, reason = "UNKNOWN", "METHOD_OBSERVATIONS_EXCEED_BOUND_SOURCE"
            if timeframe in {"monthly", "weekly", "daily"}:
                daily_bound = daily_acquisition is not None
                daily_mode = (
                    daily_acquisition.get("acquisition_mode")
                    if daily_bound else None
                )
                actual_requested_bars = (
                    daily_acquisition.get("requested_session_count")
                    if daily_mode in {
                        "VERIFIED_PROVIDER_CALL",
                        "VERIFIED_FORMAL_MARKET_INDEX_PROVIDER_CALL",
                        "VERIFIED_FORMAL_SECTOR_PROVIDER_CALL",
                    }
                    else None
                )
                request_evidence_status = (
                    daily_acquisition.get("request_evidence_status")
                    if daily_bound else "NOT_OBSERVED"
                )
                requested_bars_basis = (
                    "EXACT_XSHG_SESSIONS_OF_SHARED_DAILY_PROVIDER_CALL_NOT_HTTP_COUNT"
                    if daily_mode == "VERIFIED_PROVIDER_CALL"
                    else (
                        "EXACT_XSHG_SESSIONS_OF_FORMAL_INDEX_DAILY_PROVIDER_CALL_NOT_HTTP_COUNT"
                        if daily_mode == "VERIFIED_FORMAL_MARKET_INDEX_PROVIDER_CALL"
                        else (
                            "EXACT_XSHG_SESSIONS_OF_FORMAL_SECTOR_DAILY_PROVIDER_CALL_NOT_HTTP_COUNT"
                            if daily_mode == "VERIFIED_FORMAL_SECTOR_PROVIDER_CALL"
                            else (
                                "VERIFIED_CACHE_LOOKUP_NOT_A_NEW_PROVIDER_REQUEST"
                                if daily_mode == "VERIFIED_CACHE_HIT"
                                else "NO_BOUND_DAILY_DISPATCH_OR_CACHE_EVIDENCE"
                            )
                        )
                    )
                )
                actual_request_start = (
                    daily_acquisition.get("actual_request_start")
                    if daily_bound else None
                )
                actual_request_end = (
                    daily_acquisition.get("actual_request_end")
                    if daily_bound else None
                )
                cache_lookup_start = (
                    daily_acquisition.get("cache_lookup_start")
                    if daily_bound else None
                )
                cache_lookup_end = (
                    daily_acquisition.get("cache_lookup_end")
                    if daily_bound else None
                )
                request_receipt_hash = (
                    daily_acquisition.get("receipt_hash")
                    if daily_bound else None
                )
                source_returned_bars = (
                    daily_source_count if daily_bound else None
                )
                source_identity_hash = (
                    daily_source_hash if daily_bound else None
                )
                persistence_status = (
                    daily_acquisition.get("persistence_status")
                    if daily_bound else None
                )
            else:
                minute_bound = acquisition is not None and timeframe in source_counts
                formal_minute_bound = (
                    formal_minute_acquisition is not None
                    and timeframe in source_counts
                )
                actual_requested_bars = (
                    acquisition["requested_session_count"]
                    * {"5m": 48, "15m": 16, "30m": 8, "60m": 4}[timeframe]
                    if minute_bound else None
                )
                request_evidence_status = (
                    "VERIFIED_PROVIDER_CALL"
                    if minute_bound
                    else (
                        formal_minute_acquisition["request_evidence_status"]
                        if formal_minute_bound
                        else "NOT_OBSERVED"
                    )
                )
                requested_bars_basis = (
                    "CALENDAR_SLOTS_OF_SHARED_5M_CALL_NOT_HTTP_COUNT"
                    if minute_bound
                    else (
                        (
                            "FORMAL_INDEX_PROVIDER_RETENTION_RANGE_NOT_REQUEST_COUNT"
                            if route == "MARKET"
                            else "FORMAL_SECTOR_PROVIDER_RETENTION_RANGE_NOT_REQUEST_COUNT"
                        )
                        if formal_minute_bound
                        else "NO_BOUND_INTRADAY_DISPATCH_EVIDENCE"
                    )
                )
                actual_request_start = (                    acquisition["requested_start"] if minute_bound else None
                )
                actual_request_end = (
                    acquisition["requested_end"]
                    if minute_bound
                    else (
                        formal_minute_acquisition["actual_request_end"]
                        if formal_minute_bound
                        else None
                    )
                )
                cache_lookup_start = None
                cache_lookup_end = None
                request_receipt_hash = (
                    acquisition["receipt_hash"]
                    if minute_bound
                    else (
                        formal_minute_acquisition["receipt_hash"]
                        if formal_minute_bound
                        else None
                    )
                )
                source_returned_bars = source_counts.get(timeframe)
                source_identity_hash = source_hashes.get(timeframe)
                persistence_status = (
                    "RIGHTS_NOT_ADMITTED_RUN_LOCAL_ONLY"
                    if formal_minute_bound else None
                )
            if (
                timeframe == "daily"
                and daily_acquisition is not None
                and returned_bars is not None
                and returned_bars > daily_source_count
            ):
                state, reason = "UNKNOWN", "METHOD_OBSERVATIONS_EXCEED_BOUND_SOURCE"
            active_daily_extension = (
                daily_window_extension
                if timeframe == "monthly"
                and component == "MACD_STARTUP_COMPARISON"
                else None
            )
            rows.append({
                "timeframe": timeframe,
                "method_id": "MTF_MOMENTUM_CONTEXT",
                "component": component,
                "window_class": "RECURSIVE_WARMUP",
                "required_bars": required,
                "planned_bars": requested,
                "requested_bars": actual_requested_bars,
                "request_evidence_status": request_evidence_status,
                "requested_bars_basis": requested_bars_basis,
                "actual_request_start": actual_request_start,
                "actual_request_end": actual_request_end,
                "cache_lookup_start": cache_lookup_start,
                "cache_lookup_end": cache_lookup_end,
                "request_receipt_hash": request_receipt_hash,
                "source_returned_bars": source_returned_bars,
                "source_identity_hash": source_identity_hash,
                "persistence_status": persistence_status,
                "bounded_window_extension_status": (
                    active_daily_extension.get("final_status")
                    if isinstance(active_daily_extension, Mapping) else None
                ),
                "bounded_window_extension_reason": (
                    active_daily_extension.get("final_reason")
                    if isinstance(active_daily_extension, Mapping) else None
                ),
                "bounded_window_extension_receipt_hash": (
                    active_daily_extension.get("receipt_hash")
                    if isinstance(active_daily_extension, Mapping) else None
                ),
                "bounded_window_extension_provider_calls": (
                    active_daily_extension.get("provider_method_call_count")
                    if isinstance(active_daily_extension, Mapping) else None
                ),
                "returned_bars": returned_bars,
                "acquisition_plan_status": plan_state,
                "status": state,
                "reason": reason,
                "source_window_sufficient": bool(
                    type(required) is int
                    and type(returned_bars) is int
                    and returned_bars >= required
                ),
                "consumer_path": (
                    "multi_timeframe_structure_context.timeframes."
                    f"{timeframe}.{field}"
                ),
                "confirmed_at": None,
                "invalidation": None,
                "independent_action_authority": False,
                "strategy_admitted": False,
                "learning_admitted": False,
            })

    states = [row["status"] for row in rows]
    if rows and all(state == "READY" for state in states):
        status, reason = "READY", "ALL_TRACED_COMPONENTS_READY"
    elif any(state in {"READY", "PARTIAL"} for state in states):
        status, reason = "PARTIAL", "MIXED_METHOD_WINDOW_READINESS"
    elif any(state == "UNKNOWN" for state in states):
        status, reason = "UNKNOWN", "METHOD_WINDOW_READINESS_UNKNOWN"
    else:
        status, reason = "MISSING", "METHOD_WINDOW_READINESS_MISSING"
    return finish({
        **base,
        "status": status,
        "reason": reason,
        "daily_plan_hash": daily_window_plan["plan_hash"],
        "intraday_plan_hash": digest(intraday_window_plan),
        "intraday_acquisition_receipt": acquisition,
        "daily_acquisition_receipt": daily_acquisition,
        "formal_market_source_receipts": formal_market_receipts or None,
        "formal_sector_source_receipts": formal_sector_receipts or None,
        "formal_market_minute_source_receipt": (
            formal_minute_acquisition if route == "MARKET" else None
        ),
        "formal_sector_minute_source_receipt": (
            formal_minute_acquisition if route == "SECTOR" else None
        ),
        "rows": rows,
    })


def at(value: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    return value


@dataclass(frozen=True)
class EvidenceBinding:
    requirement_id: str
    path: str
    owner: str
    callable_name: str
    fields: tuple[str, ...]
    version_ref: str
    warmup_ref: str
    correlation_group: str
    timeframe: str = "daily"
    asset_routes: tuple[str, ...] = ("STOCK", "ETF", "MARKET")
    implementation_state: str = "EXISTING_REUSED"
    reentry: str = "CURRENT_OWNER_VERSION_AND_OUTPUT_VALIDATION"
    ledger: bool = True


@dataclass(frozen=True)
class MethodWindowProfile:
    """Leaf-method design view bound to, but not part of, the runtime manifest hash."""

    method_id: str
    requirement_id: str
    window_classes: tuple[str, ...]
    target_timeframes: tuple[str, ...]
    current_execution_timeframes: tuple[str, ...] = ()
    implementation_state: str = "DEFERRED_WITH_OWNER_AND_REENTRY"
    consumer_scope: str = ""
    family_targets: tuple[str, ...] = ()


# References name existing formula/config owners; no copied thresholds or formulas.
EVIDENCE_BINDINGS = (
    EvidenceBinding("REGIME", "market_sector_regime", "src/services/factor_decision_summary.py", "_build_market_sector_regime_evidence", ("market_light", "stock_market_position"), "_MARKET_SECTOR_REGIME_VERSION", "UPSTREAM_REGIME_READINESS", "market_sector", "asset"),
    EvidenceBinding("TREND_RS", "trend_relative_strength", "src/services/factor_decision_summary.py", "_build_trend_relative_strength_evidence", ("date", "close", "data_source", "benchmark_close"), "_TREND_RELATIVE_STRENGTH_VERSION", "RELATIVE_STRENGTH_AND_TREND_OWNERS", "trend_price"),
    EvidenceBinding("SUPPLY", "supply_demand_volume_price", "src/services/supply_demand_service.py", "build_supply_demand_context", ("date", "high", "low", "close", "volume", "data_source"), "SUPPLY_DEMAND_SCHEMA_VERSION", "REQUIRED_OBSERVATIONS", "volume_pressure"),
    EvidenceBinding("COST", "cost_structure_evidence", "src/services/cost_structure_service.py", "build_cost_structure_context", ("date", "high", "low", "close", "volume", "data_source"), "COST_STRUCTURE_SCHEMA_VERSION", "REFERENCE_WINDOWS", "price_volume_cost"),
    EvidenceBinding("STRUCTURE", "price_structure_evidence", "src/services/price_structure_service.py", "build_price_structure_context", ("date", "open", "high", "low", "close", "data_source"), "PIVOT_ALGORITHM_VERSION", "MIN_OBSERVATIONS", "confirmed_price_swing"),
    EvidenceBinding("MOMENTUM", "volatility_momentum_evidence", "src/services/volatility_momentum_service.py", "build_volatility_momentum_context", ("date", "open", "high", "low", "close", "data_source"), "ALGORITHM_VERSION", "READY_OBSERVATIONS", "same_swing_momentum"),
    EvidenceBinding("PATTERN", "pattern_trigger_evidence", "src/services/pattern_trigger_service.py", "build_pattern_trigger_context", ("date", "open", "high", "low", "close", "volume", "data_source"), "ALGORITHM_VERSION", "MIN_OBSERVATIONS", "price_contraction"),
    EvidenceBinding(
        "CANDLESTICK",
        "pattern_trigger_evidence.context.candlestick",
        "src/services/candlestick_pattern_service.py",
        "build_candlestick_pattern_context",
        ("date", "open", "high", "low", "close", "data_source"),
        "ALGORITHM_VERSION",
        "MIN_OBSERVATIONS",
        "confirmed_price_swing",
        timeframe="daily",
        implementation_state="EXISTING_REUSED",
        reentry="CURRENT_OWNER_VERSION_AND_OUTPUT_VALIDATION",
        ledger=False,
    ),
    EvidenceBinding(
        "QUALITY",
        "quality_evidence",
        "src/services/fundamental_quality_service.py",
        "compile_fundamental_quality",
        (
            "asset_id", "asset_type", "currency", "financial_period_end",
            "published_at", "financial_version", "data_admissions",
            "metrics", "metric_sources",
        ),
        "SCHEMA_VERSION",
        "REQUIRED_DATASET_IDS",
        "fundamentals",
        timeframe="asset",
        asset_routes=("STOCK",),
        implementation_state="EXISTING_REUSED",
        reentry="CURRENT_DATED_FINANCIAL_INPUT_AND_SOURCE_ADMISSION",
        ledger=False,
    ),
    EvidenceBinding(
        "VALUATION",
        "valuation_evidence",
        "src/services/business_valuation_service.py",
        "compile_business_valuation",
        (
            "route_id", "asset_id", "asset_type", "currency", "current_price",
            "financial_period_end", "published_at", "financial_version",
            "data_admissions", "inputs", "assumptions",
        ),
        "SCHEMA_VERSION",
        "VALUATION_ROUTE_IDS",
        "valuation",
        timeframe="asset",
        asset_routes=("STOCK", "ETF"),
        implementation_state="EXISTING_REUSED",
        reentry="CURRENT_ROUTE_INPUT_AND_SOURCE_ADMISSION",
        ledger=False,
    ),
    EvidenceBinding("MTF", "multi_timeframe_structure_context", "src/services/multi_timeframe_structure_service.py", "build_multi_timeframe_structure_context", ("date", "open", "high", "low", "close", "volume", "data_source"), "ALGORITHM_VERSION", "TREND_MIN_BARS", "nested_price_structure", "multi"),
)

DECISION_BINDING = EvidenceBinding(
    "DECISION", "canonical_decision", "src/services/factor_decision_summary.py", "_canonical_decision",
    ("canonical_evidence",), "_CANONICAL_AUTHORITY", "STRATEGY_REQUIRED_EVIDENCE", "decision", "asset", ledger=False)

# A producer gap is an executable UNKNOWN, not a fake READY stub.
DEFERRED_BINDINGS = tuple(
    EvidenceBinding(rid, path, owner, "NOT_ADMITTED", fields, "NOT_ADMITTED", "NOT_ADMITTED", group,
                    timeframe=tf, implementation_state="DEFERRED_WITH_OWNER_AND_REENTRY", reentry=reentry, ledger=False)
    for rid, path, owner, fields, group, tf, reentry in (
        ("DISTRIBUTION", "distribution_risk_evidence", "FACTOR:distribution_risk", ("date", "open", "high", "low", "close", "volume"), "volume_pressure", "daily", "DETERMINISTIC_DISTRIBUTION_CLEAR_RESOLVER"),
        ("RISK_REWARD", "risk_reward_evidence", "FACTOR:execution", ("entry", "stop", "cost", "execution_identity"), "execution", "asset", "ACCEPTED_RISK_REWARD_RESOLVER"),
        ("EXTRA_INDICATORS", "extended_indicator_evidence", "FACTOR:VolatilityMomentum", ("date", "open", "high", "low", "close", "volume"), "same_swing_momentum", "daily", "ADX_DMI_BOLLINGER_OBV_ADL_MFI_KDJ_INDEPENDENT_ADMISSION"),
        ("AVWAP_PROFILE", "anchored_cost_evidence", "FACTOR:CostStructure", ("anchor", "price_volume_distribution"), "price_volume_cost", "asset", "ANCHOR_AND_PRICE_VOLUME_METHOD_NOT_DAILY_PROXY"),
        ("CHAN", "chan_evidence", "FACTOR:ChanFeatureAdapter", ("date", "open", "high", "low", "close"), "confirmed_price_swing", "multi", "NARROW_ADAPTER_BAR_REPLAY_NO_BACKFILL"),
        ("WAVE", "wave_evidence", "FACTOR:WaveShadow", ("date", "open", "high", "low", "close"), "confirmed_price_swing", "multi", "RESEARCH_SHADOW_ONLY"),
        ("ETF_SPECIFIC", "etf_specific_evidence", "FACTOR:ETF", ("nav", "nav_at", "shares", "bid", "ask", "tracking_error"), "etf_execution", "asset", "ETF_DATA_IDENTITY_AND_METHOD_ADMISSION"),
        ("GLOBAL", "global_evidence", "FACTOR:MarketGlobal", ("published_at", "currency", "macro_series_version"), "market_global", "asset", "GLOBAL_SOURCE_AND_DATE_ADMISSION"),
        ("BREADTH", "market_breadth_evidence", "FACTOR:MarketBreadth", ("universe_as_of", "constituents", "close"), "market_breadth", "asset", "HISTORICAL_UNIVERSE_AND_COVERAGE"),
        ("PROBABILITY", "calibrated_probability_evidence", "FACTOR:Calibration", ("dataset_hash", "model_hash", "calibrator_hash", "promotion_id"), "model", "asset", "OUTCOME_PIT_OOT_CALIBRATION_SHADOW_PROMOTION"),
    )
) + tuple(
    EvidenceBinding("INTRADAY_" + tf, "multi_timeframe_structure_context.timeframes." + tf,
                    "src/services/multi_timeframe_structure_service.py", "NOT_ADMITTED",
                    ("bar_end", "open", "high", "low", "close", "volume", "session", "available_at"),
                    "NOT_ADMITTED", "NOT_ADMITTED", "nested_price_structure", timeframe=tf,
                    implementation_state="DEFERRED_WITH_OWNER_AND_REENTRY",
                    reentry="ONE_COMPLETED_5M_SOURCE_AND_SESSION_AGGREGATION", ledger=False)
    for tf in ("60m", "30m", "15m", "5m")
)


# Independent accepted leaf-method set. Candidate tuples below cannot certify their
# own deletion; this is the same fail-closed pattern as ACCEPTED_REQUIREMENT_ROLES.
ACCEPTED_METHOD_WINDOW_IDS = frozenset({
    "MARKET_SECTOR_REGIME",
    "RELATIVE_STRENGTH",
    "MA_LEVEL_ALIGNMENT_DAILY",
    "SUPPLY_RELATIVE_VOLUME",
    "SUPPLY_DIRECTIONAL_VOLUME",
    "SUPPLY_CMF",
    "COST_ROLLING_REFERENCE",
    "PRICE_PIVOT_SWING",
    "BREAKOUT_RETEST_FAILED",
    "MACD",
    "RSI",
    "ROC",
    "VOLATILITY_TR_SMA",
    "CONFIRMED_DIVERGENCE",
    "CUP_HANDLE",
    "DOUBLE_BOTTOM",
    "VCP",
    "FLAT_BASE",
    "TIGHT_CONSOLIDATION",
    "MTF_MA_LEVEL_ALIGNMENT",
    "MTF_MOMENTUM_CONTEXT",
    "MTF_PRICE_STRUCTURE",
    "MA_SLOPE_CROSS",
    "MA_COMPRESSION_RELEASE",
    "QUALITY",
    "VALUATION",
    "DISTRIBUTION",
    "RISK_REWARD",
    "CANDLESTICK",
    "ATR_WILDER",
    "ADX_DMI",
    "BOLLINGER",
    "KDJ",
    "OBV_ADL",
    "MFI",
    "VWAP",
    "AVWAP_VOLUME_PROFILE",
    "CHAN",
    "WAVE",
    "ETF_SPECIFIC",
    "GLOBAL",
    "BREADTH",
    "PROBABILITY",
})

METHOD_WINDOW_PROFILES = (
    MethodWindowProfile(
        "MARKET_SECTOR_REGIME", "REGIME",
        ("CROSS_SECTIONAL_ASOF", "VINTAGE_ASOF"), ("asset",), ("asset",),
        "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "RELATIVE_STRENGTH", "TREND_RS",
        ("FIXED_ROLLING", "CROSS_SECTIONAL_ASOF"), TIMEFRAMES, ("daily",),
        "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MA_LEVEL_ALIGNMENT_DAILY", "TREND_RS",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "SUPPLY_RELATIVE_VOLUME", "SUPPLY",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "SUPPLY_DIRECTIONAL_VOLUME", "SUPPLY",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "SUPPLY_CMF", "SUPPLY",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "COST_ROLLING_REFERENCE", "COST",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "PRICE_PIVOT_SWING", "STRUCTURE",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "BREAKOUT_RETEST_FAILED", "STRUCTURE",
        ("VARIABLE_STRUCTURE", "EVENT_ANCHORED"), TIMEFRAMES, ("daily",),
        "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MACD", "MOMENTUM",
        ("RECURSIVE_WARMUP",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "RSI", "MOMENTUM",
        ("RECURSIVE_WARMUP",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "ROC", "MOMENTUM",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "VOLATILITY_TR_SMA", "MOMENTUM",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "CONFIRMED_DIVERGENCE", "MOMENTUM",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "CUP_HANDLE", "PATTERN",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "DOUBLE_BOTTOM", "PATTERN",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "VCP", "PATTERN",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "FLAT_BASE", "PATTERN",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "TIGHT_CONSOLIDATION", "PATTERN",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MTF_MA_LEVEL_ALIGNMENT", "MTF",
        ("FIXED_ROLLING",), NON_DAILY_TECHNICAL_TIMEFRAMES,
        ("monthly", "weekly"), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MTF_MOMENTUM_CONTEXT", "MTF",
        ("FIXED_ROLLING", "RECURSIVE_WARMUP"), NON_DAILY_TECHNICAL_TIMEFRAMES,
        ("monthly", "weekly"), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MTF_PRICE_STRUCTURE", "MTF",
        ("VARIABLE_STRUCTURE",), NON_DAILY_TECHNICAL_TIMEFRAMES,
        ("monthly", "weekly", "60m", "30m"), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MA_SLOPE_CROSS", "MTF",
        ("FIXED_ROLLING",), TIMEFRAMES, ("monthly", "weekly", "daily"),
        "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MA_COMPRESSION_RELEASE", "MTF",
        ("FIXED_ROLLING", "ADAPTIVE_CONTEXT", "INCREMENTAL_STATE_MACHINE"),
        TIMEFRAMES, ("monthly", "weekly", "daily"), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "QUALITY", "QUALITY",
        ("VINTAGE_ASOF",), ("asset",), ("asset",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "VALUATION", "VALUATION",
        ("VINTAGE_ASOF", "CROSS_SECTIONAL_ASOF"), ("asset",), ("asset",),
        "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "DISTRIBUTION", "DISTRIBUTION",
        ("ADAPTIVE_CONTEXT", "INCREMENTAL_STATE_MACHINE"), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "RISK_REWARD", "RISK_REWARD",
        ("EVENT_ANCHORED",), ("asset",), (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "CANDLESTICK", "CANDLESTICK",
        ("ADAPTIVE_CONTEXT", "EVENT_ANCHORED"), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "ATR_WILDER", "MOMENTUM",
        ("RECURSIVE_WARMUP",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "ADX_DMI", "EXTRA_INDICATORS",
        ("FIXED_ROLLING", "RECURSIVE_WARMUP"), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "BOLLINGER", "EXTRA_INDICATORS",
        ("FIXED_ROLLING",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "KDJ", "EXTRA_INDICATORS",
        ("FIXED_ROLLING", "RECURSIVE_WARMUP"), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "OBV_ADL", "EXTRA_INDICATORS",
        ("RECURSIVE_WARMUP",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "MFI", "EXTRA_INDICATORS",
        ("FIXED_ROLLING",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "VWAP", "AVWAP_PROFILE",
        ("EVENT_ANCHORED",), ("daily", "60m", "30m", "15m", "5m"), (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "AVWAP_VOLUME_PROFILE", "AVWAP_PROFILE",
        ("EVENT_ANCHORED",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "CHAN", "CHAN",
        ("INCREMENTAL_STATE_MACHINE",), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "WAVE", "WAVE",
        ("VARIABLE_STRUCTURE", "INCREMENTAL_STATE_MACHINE"), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "ETF_SPECIFIC", "ETF_SPECIFIC",
        ("FIXED_ROLLING", "CROSS_SECTIONAL_ASOF", "VINTAGE_ASOF"), ("asset",), (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "GLOBAL", "GLOBAL",
        ("FIXED_ROLLING", "VINTAGE_ASOF"), ("asset",), (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "BREADTH", "BREADTH",
        ("FIXED_ROLLING", "CROSS_SECTIONAL_ASOF"), ("asset",), (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "PROBABILITY", "PROBABILITY",
        ("VINTAGE_ASOF",), ("asset",), (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
)


_METHOD_CONSUMER_CLASSIFICATION = {
    "MARKET_SECTOR_REGIME": ("ASSET_LAYER", ("REGIME",)),
    "RELATIVE_STRENGTH": ("TECHNICAL_MATRIX", ("TREND_RS",)),
    "MA_LEVEL_ALIGNMENT_DAILY": ("TECHNICAL_MATRIX", ("TREND_RS",)),
    "SUPPLY_RELATIVE_VOLUME": ("TECHNICAL_MATRIX", ("SUPPLY",)),
    "SUPPLY_DIRECTIONAL_VOLUME": ("TECHNICAL_MATRIX", ("SUPPLY",)),
    "SUPPLY_CMF": ("TECHNICAL_MATRIX", ("SUPPLY",)),
    "COST_ROLLING_REFERENCE": ("TECHNICAL_MATRIX", ("COST",)),
    "PRICE_PIVOT_SWING": ("TECHNICAL_MATRIX", ("STRUCTURE",)),
    "BREAKOUT_RETEST_FAILED": ("TECHNICAL_MATRIX", ("STRUCTURE",)),
    "MACD": ("TECHNICAL_MATRIX", ("MOMENTUM",)),
    "RSI": ("TECHNICAL_MATRIX", ("MOMENTUM",)),
    "ROC": ("TECHNICAL_MATRIX", ("MOMENTUM",)),
    "VOLATILITY_TR_SMA": ("TECHNICAL_MATRIX", ("MOMENTUM",)),
    "CONFIRMED_DIVERGENCE": ("TECHNICAL_MATRIX", ("MOMENTUM",)),
    "CUP_HANDLE": ("TECHNICAL_MATRIX", ("PATTERN",)),
    "DOUBLE_BOTTOM": ("TECHNICAL_MATRIX", ("PATTERN",)),
    "VCP": ("TECHNICAL_MATRIX", ("PATTERN",)),
    "FLAT_BASE": ("TECHNICAL_MATRIX", ("PATTERN",)),
    "TIGHT_CONSOLIDATION": ("TECHNICAL_MATRIX", ("PATTERN",)),
    "MTF_MA_LEVEL_ALIGNMENT": ("TECHNICAL_MATRIX", ("TREND_RS", "MTF")),
    "MTF_MOMENTUM_CONTEXT": ("TECHNICAL_MATRIX", ("SUPPLY", "MOMENTUM", "MTF")),
    "MTF_PRICE_STRUCTURE": ("TECHNICAL_MATRIX", ("STRUCTURE", "MTF")),
    "MA_SLOPE_CROSS": ("TECHNICAL_MATRIX", ("TREND_RS", "MTF")),
    "MA_COMPRESSION_RELEASE": ("TECHNICAL_MATRIX", ("MTF",)),
    "QUALITY": ("ASSET_LAYER", ()),
    "VALUATION": ("ASSET_LAYER", ()),
    "DISTRIBUTION": ("ASSET_LAYER", ("SUPPLY", "STRUCTURE", "PATTERN")),
    "RISK_REWARD": ("ASSET_LAYER", ()),
    "CANDLESTICK": ("TECHNICAL_MATRIX", ("PATTERN",)),
    "ATR_WILDER": ("TECHNICAL_MATRIX", ("MOMENTUM",)),
    "ADX_DMI": ("TECHNICAL_MATRIX", ("MOMENTUM",)),
    "BOLLINGER": ("TECHNICAL_MATRIX", ("PATTERN",)),
    "KDJ": ("TECHNICAL_MATRIX", ("MOMENTUM",)),
    "OBV_ADL": ("TECHNICAL_MATRIX", ("SUPPLY",)),
    "MFI": ("TECHNICAL_MATRIX", ("SUPPLY",)),
    "VWAP": ("TECHNICAL_MATRIX", ("COST",)),
    "AVWAP_VOLUME_PROFILE": ("TECHNICAL_MATRIX", ("COST",)),
    "CHAN": ("RESEARCH_SHADOW", ("STRUCTURE", "PATTERN", "MTF")),
    "WAVE": ("RESEARCH_SHADOW", ("STRUCTURE", "PATTERN", "MTF")),
    "ETF_SPECIFIC": ("ASSET_LAYER", ()),
    "GLOBAL": ("ASSET_LAYER", ("REGIME",)),
    "BREADTH": ("ASSET_LAYER", ("REGIME",)),
    "PROBABILITY": ("MODEL_LAYER", ()),
}
if frozenset(_METHOD_CONSUMER_CLASSIFICATION) != ACCEPTED_METHOD_WINDOW_IDS:
    raise TraceabilityError("METHOD_CONSUMER_CLASSIFICATION_SET_MISMATCH")
INTRADAY_CONTEXT_METHOD_IDS = frozenset({
    "MTF_MA_LEVEL_ALIGNMENT",
    "MTF_MOMENTUM_CONTEXT",
    "MTF_PRICE_STRUCTURE",  # Confirmed 60/30/15/5m pivots are descriptive only; trigger gate stays deferred.
    "MA_SLOPE_CROSS",
    "MA_COMPRESSION_RELEASE",
})
_METHOD_CURRENT_EXECUTION_OVERRIDES = {
    "MTF_MA_LEVEL_ALIGNMENT": ("monthly", "weekly", "60m", "30m", "15m", "5m"),
    "MTF_MOMENTUM_CONTEXT": ("monthly", "weekly", "60m", "30m", "15m", "5m"),
    "MTF_PRICE_STRUCTURE": NON_DAILY_TECHNICAL_TIMEFRAMES,
    "MA_SLOPE_CROSS": TIMEFRAMES,
    "MA_COMPRESSION_RELEASE": TIMEFRAMES,
}
METHOD_WINDOW_PROFILES = tuple(
    replace(
        profile,
        current_execution_timeframes=_METHOD_CURRENT_EXECUTION_OVERRIDES.get(
            profile.method_id,
            profile.current_execution_timeframes,
        ),
        consumer_scope=_METHOD_CONSUMER_CLASSIFICATION[profile.method_id][0],
        family_targets=_METHOD_CONSUMER_CLASSIFICATION[profile.method_id][1],
    )
    for profile in METHOD_WINDOW_PROFILES
)


@dataclass(frozen=True)
class StrategyBinding:
    clause: str
    classification: str
    requirement_id: str
    resolver: str


STRATEGY_BINDINGS = (
    StrategyBinding("market_regime_permission", "HARD_ELIGIBILITY", "REGIME", "market"),
    StrategyBinding("sector_industry_strength", "HARD_ELIGIBILITY", "REGIME", "sector"),
    StrategyBinding("leader_preference", "SELECTION_PRIOR", "REGIME", "not_used"),
    StrategyBinding("quality", "HARD_ELIGIBILITY", "QUALITY", "deferred"),
    StrategyBinding("valuation", "HARD_ELIGIBILITY", "VALUATION", "deferred"),
    StrategyBinding("monthly_trend_structure_when_ready", "CONTEXT_WHEN_READY", "MTF", "not_used"),
    StrategyBinding("weekly_trend_structure", "HARD_ELIGIBILITY", "MTF", "weekly"),
    StrategyBinding("daily_trend_structure", "HARD_ELIGIBILITY", "MTF", "daily"),
    StrategyBinding("daily_pullback_or_supply_contraction", "HARD_ELIGIBILITY", "SUPPLY", "pullback"),
    StrategyBinding("volume_price_confirmation", "HARD_ELIGIBILITY", "SUPPLY", "volume"),
    StrategyBinding("distribution_risk_clear", "HARD_ELIGIBILITY", "DISTRIBUTION", "deferred"),
    StrategyBinding("thirty_minute_trigger", "HARD_ELIGIBILITY", "INTRADAY_30m", "deferred"),
    StrategyBinding("risk_reward", "HARD_ELIGIBILITY", "RISK_REWARD", "deferred"),
)


def strategy_contract_coverage() -> tuple:
    return tuple((b.clause, b.classification, b.clause if b.classification == "HARD_ELIGIBILITY" else None)
                 for b in STRATEGY_BINDINGS)


def ledger_evidence_keys() -> tuple[str, ...]:
    # Existing v1 audit schema preserved; the registry owns this set from now on.
    return ("strategy_id", "contract_version", "composite_score", "canonical_decision") + tuple(
        b.path for b in EVIDENCE_BINDINGS if b.ledger)


# Leaf identities are also the numeric/state learning allowlist. No paragraph is a feature.
# Every weekly/monthly path names that actual timeframe; daily values never fill it.
METRIC_BINDINGS = tuple(
    {"id": name, "requirement_id": rid, "path": path, "kind": "number", "unit": unit, "timeframe": tf}
    for name, rid, path, unit, tf in (
        ("daily.price", "STRUCTURE", "price_structure_evidence.context.current_close", "PRICE_BASIS_CURRENCY", "daily"),
        ("daily.volume_ratio20", "SUPPLY", "supply_demand_volume_price.completed_bar_context.relative_volume.volume_ratio_20d", "ratio", "daily"),
        ("daily.directional_volume", "SUPPLY", "supply_demand_volume_price.completed_bar_context.directional_volume.signed_volume_balance", "ratio", "daily"),
        ("daily.cmf20", "SUPPLY", "supply_demand_volume_price.completed_bar_context.close_location_flow.cmf_20", "ratio", "daily"),
        ("daily.cost20_proxy", "COST", "cost_structure_evidence.context.bar_reference_cost.window_20.rolling_reference_price", "PRICE_BASIS_CURRENCY_PROXY", "daily"),
        ("daily.cost60_proxy", "COST", "cost_structure_evidence.context.bar_reference_cost.window_60.rolling_reference_price", "PRICE_BASIS_CURRENCY_PROXY", "daily"),
        ("daily.macd_dif", "MOMENTUM", "volatility_momentum_evidence.context.momentum.macd.dif", "PRICE_BASIS_CURRENCY", "daily"),
        ("daily.macd_dea", "MOMENTUM", "volatility_momentum_evidence.context.momentum.macd.dea", "PRICE_BASIS_CURRENCY", "daily"),
        ("daily.macd_bar", "MOMENTUM", "volatility_momentum_evidence.context.momentum.macd.bar", "PRICE_BASIS_CURRENCY", "daily"),
        ("daily.rsi6", "MOMENTUM", "volatility_momentum_evidence.context.momentum.rsi.rsi_6", "index_0_100", "daily"),
        ("daily.rsi12", "MOMENTUM", "volatility_momentum_evidence.context.momentum.rsi.rsi_12", "index_0_100", "daily"),
        ("daily.rsi24", "MOMENTUM", "volatility_momentum_evidence.context.momentum.rsi.rsi_24", "index_0_100", "daily"),
        ("daily.roc20", "MOMENTUM", "volatility_momentum_evidence.context.momentum.roc_20_pct", "pct", "daily"),
        ("daily.roc60", "MOMENTUM", "volatility_momentum_evidence.context.momentum.roc_60_pct", "pct", "daily"),
        ("daily.realized_volatility20", "MOMENTUM", "volatility_momentum_evidence.context.volatility.realized_volatility_20d_annualized_pct", "annualized_pct", "daily"),
        ("daily.tr_sma20_not_wilder_atr", "MOMENTUM", "volatility_momentum_evidence.context.volatility.true_range_sma_20_pct", "pct", "daily"),
        ("daily.atr_wilder14", "MOMENTUM", "volatility_momentum_evidence.context.volatility.atr_wilder_14", "PRICE_BASIS_CURRENCY", "daily"),
        ("daily.atr_wilder14_pct", "MOMENTUM", "volatility_momentum_evidence.context.volatility.atr_wilder_14_pct", "pct", "daily"),
    )
) + tuple(
    {"id": tf + "." + ma, "requirement_id": "MTF", "path": "multi_timeframe_structure_context.timeframes." + tf + ".trend." + ma,
     "kind": "number", "unit": "PRICE_BASIS_CURRENCY", "timeframe": tf}
    for tf in ("monthly", "weekly") for ma in ("ma5", "ma10", "ma20")
) + tuple(
    {"id": "daily." + name, "requirement_id": "EXTRA_INDICATORS", "path": "extended_indicator_evidence." + name,
     "kind": "number", "unit": "METHOD_NOT_ADMITTED", "timeframe": "daily"}
    for name in ("adx", "plus_di", "minus_di", "bollinger_upper", "bollinger_lower", "kdj_k", "kdj_d", "kdj_j", "obv", "adl", "mfi")
)


def load_slot_map(document=None) -> dict:
    if document is None:
        path = Path(__file__).resolve().parents[2] / "templates/v2_5/STOCK_DSA_V2_5_EVIDENCE_SLOT_MAP_V1.json"
        document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != "v25-evidence-slot-map-v1":
        raise TraceabilityError("SLOT_SCHEMA_MISMATCH")
    if (document.get("baseline_sha256") != BASELINE_SHA256 or document.get("baseline_bytes") != BASELINE_BYTES
            or document.get("rendering_authorized") is not False or document.get("authority") != "SEMANTIC_SIDECAR_ONLY"):
        raise TraceabilityError("SLOT_AUTHORITY_MISMATCH")
    if tuple(document.get("timeframes", ())) != TIMEFRAMES or set(document.get("envelopes", {})) != {"MARKET", "AUTO", "WATCHLIST"}:
        raise TraceabilityError("SLOT_ROUTE_MISMATCH")
    bindings = {b.requirement_id: b for b in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)}
    slots = document.get("slots")
    if not isinstance(slots, list) or not slots:
        raise TraceabilityError("SLOT_LIST_EMPTY")
    ids = []
    for slot in slots:
        if not isinstance(slot, dict) or not slot.get("id") or not slot.get("requirements") or not slot.get("paths") or not slot.get("location_class"):
            raise TraceabilityError("SLOT_BINDING_INCOMPLETE")
        ids.append(slot["id"])
        if any(rid not in bindings for rid in slot["requirements"]):
            raise TraceabilityError("ORPHAN_PRODUCT_REQUIREMENT")
        owners = [bindings[rid].path for rid in slot["requirements"]]
        if any(not any(p == owner or p.startswith(owner + ".") for owner in owners) for p in slot["paths"]):
            raise TraceabilityError("ORPHAN_PRODUCT_PATH")
    if len(set(ids)) != len(ids):
        raise TraceabilityError("DUPLICATE_PRODUCT_SLOT")
    return document


@lru_cache(maxsize=16)
def _owner_literals(owner: str) -> dict:
    """Read named constants without importing provider-dependent modules.

    Source/config versions are process-frozen, like imported Python code. An
    edited candidate must be validated in a new process, not hot reloaded.
    Only literal values and an already-bound constant plus a literal are read;
    calls, attributes and arbitrary expressions are never executed.
    """
    source = (Path(__file__).resolve().parents[2] / owner).read_text(encoding="utf-8-sig")
    values = {}

    class BoundLiterals(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.parse(repr(values[node.id]), mode="eval").body if node.id in values else node

    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        expression = BoundLiterals().visit(node.value)
        try:
            if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
                left, right = ast.literal_eval(expression.left), ast.literal_eval(expression.right)
                if type(left) is not int or type(right) is not int:
                    continue
                value = left + right
            else:
                value = ast.literal_eval(expression)
            values[node.targets[0].id] = value
        except (ValueError, TypeError, SyntaxError):
            continue
    return values


def method_contract(binding: EvidenceBinding) -> dict:
    values = _owner_literals(binding.owner)
    config = values.get("_CONFIG")
    return {"version": values.get(binding.version_ref), "warmup": values.get(binding.warmup_ref),
            "config_hash": digest(config) if isinstance(config, dict) else None}


def manifest_document() -> dict:
    return {"schema_version": MANIFEST_VERSION, "baseline_sha256": BASELINE_SHA256,
            "evidence": [asdict(b) for b in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)],
            "metrics": METRIC_BINDINGS, "product_slots": load_slot_map(),
            "method_contracts": {b.requirement_id: method_contract(b) for b in EVIDENCE_BINDINGS},
            "strategy": {"strategy_id": STOCK_STRATEGY, "bindings": [asdict(b) for b in STRATEGY_BINDINGS]},
            "ledger_keys": ledger_evidence_keys(), "timeframes": TIMEFRAMES,
            "data_policy": {"completed_only": True, "source_mixing": False, "units": "EXPLICIT_SOURCE_UNITS",
                            "price_basis": "EXPLICIT_RAW_QFQ_HFQ", "availability": "KNOWN_AT_DECISION"},
            "product_policy": "SEMANTIC_SIDECAR_ONLY_NOT_RENDERED", "learning_policy": "NO_PROSE_FEATURES"}


MANIFEST_HASH = digest(manifest_document())


def _validate_method_window_profiles(
    profiles: tuple[MethodWindowProfile, ...],
    bindings: tuple[EvidenceBinding, ...],
) -> tuple[MethodWindowProfile, ...]:
    method_ids = [profile.method_id for profile in profiles]
    if len(method_ids) != len(set(method_ids)):
        raise TraceabilityError("DUPLICATE_METHOD_WINDOW_PROFILE")
    if frozenset(method_ids) != ACCEPTED_METHOD_WINDOW_IDS:
        raise TraceabilityError("ACCEPTED_METHOD_WINDOW_SET_MISMATCH")

    binding_lookup = {
        binding.requirement_id: binding
        for binding in bindings
        if binding.requirement_id != "DECISION"
    }
    expected_requirements = {
        requirement_id
        for requirement_id in binding_lookup
        if not requirement_id.startswith("INTRADAY_")
    }
    covered_requirements = set()
    intraday_gates = {
        binding.timeframe: binding
        for binding in binding_lookup.values()
        if binding.requirement_id.startswith("INTRADAY_")
    }

    for profile in profiles:
        binding = binding_lookup.get(profile.requirement_id)
        if binding is None or profile.requirement_id.startswith("INTRADAY_"):
            raise TraceabilityError("ORPHAN_METHOD_WINDOW_REQUIREMENT:" + profile.method_id)
        if not profile.window_classes or not set(profile.window_classes) <= METHOD_WINDOW_CLASSES:
            raise TraceabilityError("INVALID_METHOD_WINDOW_CLASS:" + profile.method_id)
        if profile.implementation_state not in METHOD_WINDOW_STATES:
            raise TraceabilityError("INVALID_METHOD_WINDOW_STATE:" + profile.method_id)
        if not profile.target_timeframes or not set(profile.target_timeframes) <= METHOD_WINDOW_TIMEFRAMES:
            raise TraceabilityError("INVALID_METHOD_TARGET_TIMEFRAME:" + profile.method_id)
        if len(profile.target_timeframes) != len(set(profile.target_timeframes)):
            raise TraceabilityError("DUPLICATE_METHOD_TARGET_TIMEFRAME:" + profile.method_id)
        if profile.consumer_scope not in METHOD_CONSUMER_SCOPES:
            raise TraceabilityError("INVALID_METHOD_CONSUMER_SCOPE:" + profile.method_id)
        if len(profile.family_targets) != len(set(profile.family_targets)) or not set(profile.family_targets) <= set(TECHNICAL_EVIDENCE_FAMILIES):
            raise TraceabilityError("INVALID_METHOD_FAMILY_TARGET:" + profile.method_id)
        if profile.consumer_scope == "TECHNICAL_MATRIX" and not profile.family_targets:
            raise TraceabilityError("TECHNICAL_METHOD_FAMILY_TARGET_MISSING:" + profile.method_id)
        if "asset" in profile.target_timeframes and profile.target_timeframes != ("asset",):
            raise TraceabilityError("ASSET_METHOD_TIMEFRAME_MIXED:" + profile.method_id)
        if not set(profile.current_execution_timeframes) <= set(profile.target_timeframes):
            raise TraceabilityError("CURRENT_EXECUTION_OUTSIDE_TARGET:" + profile.method_id)
        if profile.implementation_state == "EXISTING_REUSED":
            if not profile.current_execution_timeframes:
                raise TraceabilityError("CURRENT_EXECUTION_MISSING:" + profile.method_id)
            if binding.implementation_state != "EXISTING_REUSED":
                raise TraceabilityError("LEAF_METHOD_EXCEEDS_PARENT_ADMISSION:" + profile.method_id)
        elif profile.current_execution_timeframes:
            raise TraceabilityError("UNADMITTED_METHOD_CLAIMS_CURRENT_EXECUTION:" + profile.method_id)

        for timeframe in profile.current_execution_timeframes:
            gate = intraday_gates.get(timeframe)
            if gate is not None and gate.implementation_state != "EXISTING_REUSED":
                if profile.method_id not in INTRADAY_CONTEXT_METHOD_IDS:
                    raise TraceabilityError("CURRENT_EXECUTION_INTRADAY_GATE_DEFERRED:" + profile.method_id)
                if profile.requirement_id != "MTF":
                    raise TraceabilityError("INTRADAY_CONTEXT_METHOD_PARENT_MISMATCH:" + profile.method_id)
        covered_requirements.add(profile.requirement_id)

    if covered_requirements != expected_requirements:
        raise TraceabilityError("METHOD_WINDOW_REQUIREMENT_COVERAGE_MISMATCH")
    return profiles


def validate_method_window_profiles(profiles=None) -> tuple[MethodWindowProfile, ...]:
    """Validate the leaf-method view against the current accepted registry."""

    profiles = tuple(METHOD_WINDOW_PROFILES if profiles is None else profiles)
    bindings = EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)
    return _validate_method_window_profiles(profiles, bindings)


def compile_method_window_policy_view(profiles=None) -> dict:
    """Derive method/window/Product/Learning coverage without mutating MANIFEST_HASH."""

    validate_registry()
    profiles = validate_method_window_profiles(profiles)
    slots = load_slot_map()["slots"]
    product_slots = {}
    for slot in slots:
        for requirement_id in slot["requirements"]:
            product_slots.setdefault(requirement_id, []).append(slot["id"])

    strategy_clauses = {}
    for binding in STRATEGY_BINDINGS:
        strategy_clauses.setdefault(binding.requirement_id, []).append(
            {
                "clause": binding.clause,
                "classification": binding.classification,
                "resolver": binding.resolver,
            }
        )

    learning_metrics = {}
    for metric in METRIC_BINDINGS:
        learning_metrics.setdefault(metric["requirement_id"], []).append(metric["id"])

    binding_lookup = {
        binding.requirement_id: binding
        for binding in EVIDENCE_BINDINGS + DEFERRED_BINDINGS
    }
    methods = []
    for profile in profiles:
        binding = binding_lookup[profile.requirement_id]
        parent_contract = (
            method_contract(binding)
            if binding.implementation_state == "EXISTING_REUSED"
            else {"version": None, "warmup": None, "config_hash": None}
        )
        methods.append(
            {
                "method_id": profile.method_id,
                "requirement_id": profile.requirement_id,
                "canonical_path": binding.path,
                "consumer_scope": profile.consumer_scope,
                "family_targets": profile.family_targets,
                "owner": binding.owner,
                "parent_callable": binding.callable_name,
                "correlation_group": binding.correlation_group,
                "asset_routes": binding.asset_routes,
                "window_classes": profile.window_classes,
                "target_timeframes": profile.target_timeframes,
                "current_execution_timeframes": profile.current_execution_timeframes,
                "leaf_implementation_state": profile.implementation_state,
                "parent_implementation_state": binding.implementation_state,
                "parent_method_contract": parent_contract,
                "parent_product_slot_ids": tuple(product_slots.get(profile.requirement_id, ())),
                "parent_strategy_clauses": tuple(strategy_clauses.get(profile.requirement_id, ())),
                "parent_learning_metric_ids": tuple(learning_metrics.get(profile.requirement_id, ())),
                "reentry": binding.reentry,
            }
        )

    timeframe_gates = tuple(
        {
            "requirement_id": binding.requirement_id,
            "timeframe": binding.timeframe,
            "canonical_path": binding.path,
            "implementation_state": binding.implementation_state,
            "reentry": binding.reentry,
        }
        for binding in DEFERRED_BINDINGS
        if binding.requirement_id.startswith("INTRADAY_")
    )
    document = {
        "schema_version": METHOD_WINDOW_POLICY_VERSION,
        "source_manifest_version": MANIFEST_VERSION,
        "source_manifest_hash": MANIFEST_HASH,
        "window_classes": tuple(sorted(METHOD_WINDOW_CLASSES)),
        "timeframes": TIMEFRAMES,
        "consumer_scopes": tuple(sorted(METHOD_CONSUMER_SCOPES)),
        "technical_families": TECHNICAL_EVIDENCE_FAMILIES,
        "accepted_method_ids": tuple(sorted(ACCEPTED_METHOD_WINDOW_IDS)),
        "methods": methods,
        "timeframe_gates": timeframe_gates,
        "decision_authority": {
            "requirement_id": DECISION_BINDING.requirement_id,
            "canonical_path": DECISION_BINDING.path,
            "window_method": False,
        },
        "policy": {
            "derived_view_only": True,
            "changes_runtime_manifest": False,
            "window_type_is_method_property": True,
            "seven_by_eight_is_coverage_not_votes": True,
            "product_wider_than_learning": True,
            "missing_unknown_never_filled_by_prose": True,
        },
    }
    document["view_hash"] = digest(document)
    return document


def validate_registry(bindings=None, strategy=None, metrics=None) -> None:
    bindings = tuple(
        bindings
        if bindings is not None
        else EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)
    )
    strategy = tuple(strategy if strategy is not None else STRATEGY_BINDINGS)
    metrics = tuple(metrics if metrics is not None else METRIC_BINDINGS)
    ids = [b.requirement_id for b in bindings]
    paths = [b.path for b in bindings]
    if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
        raise TraceabilityError("DUPLICATE_BINDING")
    observed_roles = frozenset(
        (
            binding.requirement_id,
            "DECISION"
            if binding.requirement_id == "DECISION"
            else "ADMITTED"
            if binding.implementation_state == "EXISTING_REUSED"
            else "DEFERRED",
        )
        for binding in bindings
    )
    if observed_roles != ACCEPTED_REQUIREMENT_ROLES:
        raise TraceabilityError("ACCEPTED_REQUIREMENT_SET_MISMATCH")
    plane_requirement_ids = [
        requirement_id
        for _, requirement_ids in COMPLETE_RESEARCH_UNIVERSE_PLANES
        for requirement_id in requirement_ids
    ]
    accepted_requirement_ids = frozenset(requirement_id for requirement_id, _ in ACCEPTED_REQUIREMENT_ROLES)
    if (
        len(plane_requirement_ids) != len(set(plane_requirement_ids))
        or frozenset(plane_requirement_ids) != accepted_requirement_ids
    ):
        raise TraceabilityError("COMPLETE_RESEARCH_UNIVERSE_REQUIREMENT_SET_MISMATCH")
    for binding in bindings:
        if not binding.owner or not binding.fields or not binding.reentry or not binding.correlation_group:
            raise TraceabilityError("INCOMPLETE_BINDING")
        if binding.implementation_state == "EXISTING_REUSED" and binding.callable_name == "NOT_ADMITTED":
            raise TraceabilityError("MISSING_METHOD_OWNER")
        if not binding.asset_routes or not set(binding.asset_routes) <= VALID_ASSET_ROUTES:
            raise TraceabilityError("INVALID_ASSET_ROUTE:" + binding.requirement_id)
        if binding.timeframe not in VALID_BINDING_TIMEFRAMES:
            raise TraceabilityError("INVALID_TIMEFRAME:" + binding.requirement_id)
    for binding in strategy:
        if binding.requirement_id not in ids:
            raise TraceabilityError("ORPHAN_STRATEGY_KEY:" + binding.clause)
    if len({b.clause for b in strategy}) != len(strategy):
        raise TraceabilityError("DUPLICATE_STRATEGY_KEY")
    lookup = {b.requirement_id: b for b in bindings}
    metric_ids = [m.get("id") for m in metrics]
    if len(metric_ids) != len(set(metric_ids)):
        raise TraceabilityError("DUPLICATE_METRIC")
    for metric in metrics:
        if metric.get("kind") != "number":
            raise TraceabilityError("INVALID_METRIC_KIND")
        if metric.get("unit") not in VALID_METRIC_UNITS:
            raise TraceabilityError("INVALID_METRIC_UNIT")
        if metric.get("timeframe") not in VALID_METRIC_TIMEFRAMES:
            raise TraceabilityError("INVALID_METRIC_TIMEFRAME")
        owner = lookup.get(metric["requirement_id"])
        if owner is None or not metric["path"].startswith(owner.path + "."):
            raise TraceabilityError("ORPHAN_LEARNING_METRIC")
    _validate_method_window_profiles(METHOD_WINDOW_PROFILES, bindings)


def validate_source_bindings(root: Path) -> dict:
    """Read-only adoption test; source existence is not runtime success."""
    identities = {}
    for binding in EVIDENCE_BINDINGS + (DECISION_BINDING,):
        path = root / binding.owner
        payload = path.read_bytes()
        tree = ast.parse(payload.decode("utf-8-sig"))
        symbols = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if binding.callable_name not in symbols:
            raise TraceabilityError("CALLABLE_NOT_FOUND:" + binding.requirement_id)
        identities[binding.requirement_id] = {"path": binding.owner, "sha256": sha256(payload).hexdigest(),
                                              "callable": binding.callable_name}
    return identities

@lru_cache(maxsize=32)
def _owner_source_sha256(owner: str) -> str:
    return sha256((Path(__file__).resolve().parents[2] / owner).read_bytes()).hexdigest()


def _binding(requirement_id: str) -> EvidenceBinding:
    for binding in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,):
        if binding.requirement_id == requirement_id:
            return binding
    raise TraceabilityError("UNKNOWN_REQUIREMENT:" + str(requirement_id))


def _identity_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value).strip() or None


def build_method_execution_receipt(
    requirement_id: str,
    *,
    output: Any,
    asset_route: str,
    stock_code: Any,
    market: Any,
    target_date: Any,
    timeframe: str,
    input_identity: Any = None,
    upstream_hashes: Any = None,
) -> dict:
    """Freeze one actual producer invocation after its output exists."""
    binding = _binding(requirement_id)
    if binding.implementation_state != "EXISTING_REUSED":
        raise TraceabilityError("METHOD_NOT_ADMITTED:" + requirement_id)
    route = str(asset_route or "").strip().upper()
    if route not in VALID_ASSET_ROUTES or route not in binding.asset_routes:
        raise TraceabilityError("INVALID_RECEIPT_ASSET_ROUTE:" + requirement_id)
    if timeframe != binding.timeframe:
        raise TraceabilityError("INVALID_RECEIPT_TIMEFRAME:" + requirement_id)
    contract = method_contract(binding)
    payload = {
        "schema_version": METHOD_EXECUTION_RECEIPT_VERSION,
        "requirement_id": requirement_id,
        "asset_route": route,
        "stock_code": str(stock_code or "").strip() or None,
        "market": str(market or "").strip().lower() or None,
        "target_date": _identity_text(target_date),
        "timeframe": timeframe,
        "input_identity": dict(input_identity) if isinstance(input_identity, Mapping) else None,
        "upstream_hashes": dict(upstream_hashes) if isinstance(upstream_hashes, Mapping) else {},
        "producer": {
            "owner": binding.owner,
            "callable": binding.callable_name,
            "source_sha256": _owner_source_sha256(binding.owner),
            "version": contract["version"],
            "config_hash": contract["config_hash"],
        },
        "output_hash": digest(output),
    }
    payload["receipt_hash"] = digest(payload)
    return payload


def _observed_method_output(binding: EvidenceBinding, value: Mapping) -> Any:
    if binding.requirement_id == "SUPPLY":
        return value.get("completed_bar_context")
    if binding.requirement_id in {"COST", "STRUCTURE", "MOMENTUM", "PATTERN"}:
        return value.get("context")
    return value


def _receipt_required(factor: Mapping) -> bool:
    return factor.get("method_execution_receipt_policy") == "REQUIRED"


def _receipt_for(factor: Mapping, requirement_id: str) -> Any:
    receipts = factor.get("method_execution_receipts")
    return receipts.get(requirement_id) if isinstance(receipts, Mapping) else None


def _receipt_state(binding: EvidenceBinding, value: Mapping, receipt: Any) -> tuple[bool, str]:
    if not isinstance(receipt, Mapping):
        return False, "METHOD_INVOCATION_RECEIPT_MISSING"
    body = dict(receipt)
    receipt_hash = body.pop("receipt_hash", None)
    if body.get("schema_version") != METHOD_EXECUTION_RECEIPT_VERSION or digest(body) != receipt_hash:
        return False, "METHOD_RECEIPT_INVALID"
    if body.get("requirement_id") != binding.requirement_id:
        return False, "METHOD_RECEIPT_REQUIREMENT_MISMATCH"
    if body.get("asset_route") not in binding.asset_routes:
        return False, "METHOD_RECEIPT_ASSET_MISMATCH"
    if body.get("timeframe") != binding.timeframe:
        return False, "METHOD_RECEIPT_TIMEFRAME_MISMATCH"
    contract = method_contract(binding)
    expected_producer = {
        "owner": binding.owner,
        "callable": binding.callable_name,
        "source_sha256": _owner_source_sha256(binding.owner),
        "version": contract["version"],
        "config_hash": contract["config_hash"],
    }
    if body.get("producer") != expected_producer:
        return False, "METHOD_RECEIPT_PRODUCER_MISMATCH"
    observed_output = _observed_method_output(binding, value)
    if body.get("output_hash") != digest(observed_output):
        return False, "METHOD_RECEIPT_OUTPUT_MISMATCH"
    if (
        binding.requirement_id in {"SUPPLY", "COST", "STRUCTURE", "MOMENTUM", "PATTERN", "CANDLESTICK", "MTF"}
        and isinstance(observed_output, Mapping)
    ):
        for key, receipt_key in (("stock_code", "stock_code"), ("market", "market"), ("target_date", "target_date")):
            observed = str(observed_output.get(key) or "").strip().lower()
            claimed = str(body.get(receipt_key) or "").strip().lower()
            if observed and observed != claimed:
                return False, "METHOD_RECEIPT_" + key.upper() + "_MISMATCH"
    if binding.requirement_id in {"SUPPLY", "COST", "STRUCTURE", "MOMENTUM", "PATTERN", "CANDLESTICK", "MTF"}:
        identity = body.get("input_identity")
        if not isinstance(identity, Mapping) or not identity.get("data_snapshot_identity"):
            return False, "METHOD_INPUT_IDENTITY_MISSING"
        if not identity.get("provider_identity") or not identity.get("adjustment_basis"):
            return False, "METHOD_PRICE_IDENTITY_UNPROVEN"
        if identity.get("price_identity_reasons"):
            return False, "METHOD_PRICE_IDENTITY_UNPROVEN"
        for key in ("stock_code", "market", "target_date"):
            actual = str(identity.get(key) or "").strip().lower()
            claimed = str(body.get(key) or "").strip().lower()
            if actual and actual != claimed:
                return False, "METHOD_INPUT_" + key.upper() + "_MISMATCH"
        if binding.requirement_id == "MTF" and value.get("data_snapshot_identity") != identity.get("data_snapshot_identity"):
            return False, "METHOD_INPUT_SNAPSHOT_MISMATCH"
    if binding.requirement_id == "TREND_RS":
        identity = body.get("input_identity")
        stock = identity.get("stock") if isinstance(identity, Mapping) else None
        benchmark = identity.get("benchmark") if isinstance(identity, Mapping) else None
        if not isinstance(stock, Mapping) or not isinstance(benchmark, Mapping):
            return False, "RS_INPUT_IDENTITY_MISSING"
        if not stock.get("data_snapshot_identity") or not benchmark.get("data_snapshot_identity"):
            return False, "RS_INPUT_IDENTITY_MISSING"
        if not stock.get("provider_identity") or not benchmark.get("provider_identity"):
            return False, "RS_PROVIDER_IDENTITY_UNPROVEN"
        if not stock.get("adjustment_basis") or stock.get("adjustment_basis") != benchmark.get("adjustment_basis"):
            return False, "RS_PRICE_BASIS_UNPROVEN"
        if stock.get("price_identity_reasons") or benchmark.get("price_identity_reasons"):
            return False, "RS_PRICE_IDENTITY_UNPROVEN"
        rs = value.get("relative_strength")
        if not isinstance(rs, Mapping) or rs.get("input_identity") != identity:
            return False, "RS_WRAPPER_IDENTITY_MISMATCH"
        if rs.get("schema_version") != "relative-strength-v1":
            return False, "RS_SCHEMA_MISMATCH"
        if str(rs.get("stock_code") or "").strip() != str(body.get("stock_code") or "").strip():
            return False, "RS_STOCK_IDENTITY_MISMATCH"
        if str(stock.get("stock_code") or "").strip() != str(body.get("stock_code") or "").strip():
            return False, "RS_STOCK_INPUT_IDENTITY_MISMATCH"
        if str(stock.get("market") or "").strip().lower() != str(body.get("market") or "").strip().lower():
            return False, "RS_MARKET_IDENTITY_MISMATCH"
        if str(stock.get("target_date") or "") != str(body.get("target_date") or ""):
            return False, "RS_TARGET_IDENTITY_MISMATCH"
        benchmark_payload = rs.get("benchmark")
        if not isinstance(benchmark_payload, Mapping):
            return False, "RS_BENCHMARK_IDENTITY_MISSING"
        if str(benchmark.get("stock_code") or "").strip() != str(benchmark_payload.get("code") or "").strip():
            return False, "RS_BENCHMARK_IDENTITY_MISMATCH"
        quality = rs.get("data_quality")
        if not isinstance(quality, Mapping) or quality.get("source_alignment") != "MATCHED":
            return False, "RS_SOURCE_ALIGNMENT_UNPROVEN"
        if str(benchmark_payload.get("source") or "").strip() != str(benchmark.get("provider_identity") or "").strip():
            return False, "RS_BENCHMARK_SOURCE_MISMATCH"
        if quality.get("stock_endpoint_sources") != [stock.get("provider_identity")]:
            return False, "RS_STOCK_SOURCE_MISMATCH"
    if binding.requirement_id == "QUALITY":
        from src.services.fundamental_quality_service import (
            FundamentalQualityError,
            validate_fundamental_quality,
        )
        try:
            validated = validate_fundamental_quality(value)
        except FundamentalQualityError:
            return False, "QUALITY_OUTPUT_RECEIPT_INVALID"
        if validated.get("status") != "READY":
            return False, "QUALITY_OUTPUT_NOT_READY"
        body_asset = str(body.get("stock_code") or "").strip().split(".")[-1]
        value_asset = str(validated.get("asset_id") or "").strip().split(".")[-1]
        if not body_asset or body_asset != value_asset:
            return False, "QUALITY_ASSET_IDENTITY_MISMATCH"
        if body.get("asset_route") != "STOCK":
            return False, "QUALITY_ASSET_ROUTE_MISMATCH"
        value_as_of = str(validated.get("as_of") or "")[:10]
        receipt_target = str(body.get("target_date") or "")[:10]
        if not value_as_of or value_as_of != receipt_target:
            return False, "QUALITY_ASOF_RECEIPT_MISMATCH"
        admission_hashes = tuple(validated.get("data_admission_hashes") or ())
        identity = body.get("input_identity")
        expected_identity = {
            "quality_receipt_hash": validated.get("receipt_hash"),
            "data_admission_hashes": admission_hashes,
            "asset_id": validated.get("asset_id"),
            "as_of": validated.get("as_of"),
        }
        if identity != expected_identity:
            return False, "QUALITY_INPUT_IDENTITY_MISMATCH"
        if body.get("upstream_hashes") != {
            "data_admission_hashes": admission_hashes,
        }:
            return False, "QUALITY_DATA_ADMISSION_HASH_MISMATCH"
    if binding.requirement_id == "VALUATION":
        from src.services.business_valuation_service import (
            BusinessValuationError,
            validate_business_valuation,
        )
        try:
            validated = validate_business_valuation(value)
        except BusinessValuationError:
            return False, "VALUATION_OUTPUT_RECEIPT_INVALID"
        if validated.get("status") != "READY":
            return False, "VALUATION_OUTPUT_NOT_READY"
        body_asset = str(body.get("stock_code") or "").strip().split(".")[-1]
        value_asset = str(validated.get("asset_id") or "").strip().split(".")[-1]
        if not body_asset or body_asset != value_asset:
            return False, "VALUATION_ASSET_IDENTITY_MISMATCH"
        expected_route = (
            "ETF" if validated.get("asset_type") == "etf" else "STOCK"
        )
        if body.get("asset_route") != expected_route:
            return False, "VALUATION_ASSET_ROUTE_MISMATCH"
        value_as_of = str(validated.get("as_of") or "")[:10]
        receipt_target = str(body.get("target_date") or "")[:10]
        if not value_as_of or value_as_of != receipt_target:
            return False, "VALUATION_ASOF_RECEIPT_MISMATCH"
        admission_hashes = tuple(validated.get("data_admission_hashes") or ())
        identity = body.get("input_identity")
        if not isinstance(identity, Mapping):
            return False, "VALUATION_INPUT_IDENTITY_MISSING"
        expected_identity = {
            "valuation_receipt_hash": validated.get("receipt_hash"),
            "data_admission_hashes": admission_hashes,
            "route_id": validated.get("route_id"),
            "asset_id": validated.get("asset_id"),
            "as_of": validated.get("as_of"),
        }
        if identity != expected_identity:
            return False, "VALUATION_INPUT_IDENTITY_MISMATCH"
        if body.get("upstream_hashes") != {
            "data_admission_hashes": admission_hashes,
        }:
            return False, "VALUATION_DATA_ADMISSION_HASH_MISMATCH"
    if binding.requirement_id == "REGIME":
        parents = body.get("upstream_hashes")
        daily = parents.get("daily_market_context") if isinstance(parents, Mapping) else None
        structure = parents.get("market_structure_context") if isinstance(parents, Mapping) else None
        if not isinstance(daily, Mapping) or not isinstance(structure, Mapping):
            return False, "REGIME_UPSTREAM_IDENTITY_MISSING"
        if not daily.get("hash") or not structure.get("hash"):
            return False, "REGIME_UPSTREAM_HASH_MISSING"
        if value.get("upstream_identity") != parents:
            return False, "REGIME_UPSTREAM_HASH_MISMATCH"
        target = str(body.get("target_date") or "")
        for parent in (daily, structure):
            parent_date = str(parent.get("trade_date") or "")
            if parent_date and target and parent_date != target:
                return False, "REGIME_UPSTREAM_DATE_MISMATCH"
        structure_market = str(structure.get("market") or "").strip().lower()
        if structure_market and structure_market != str(body.get("market") or "").strip().lower():
            return False, "REGIME_MARKET_IDENTITY_MISMATCH"
        structure_stock = str(structure.get("stock_code") or "").strip()
        if structure_stock and structure_stock != str(body.get("stock_code") or "").strip():
            return False, "REGIME_STOCK_IDENTITY_MISMATCH"
    return True, "METHOD_INVOCATION_VERIFIED"



def _state(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "MISSING"
    state = str(value.get("evidence_state", value.get("status", "UNKNOWN"))).upper()
    return state if state in STATES else "UNKNOWN"


def method_observation(
    binding: EvidenceBinding,
    value: Any,
    *,
    receipt: Any = None,
    require_receipt: bool = False,
) -> tuple[str, str]:
    """Validate metadata and, on Production paths, the actual execution receipt."""
    state = _state(value)
    if binding.implementation_state != "EXISTING_REUSED":
        return "UNKNOWN", "METHOD_NOT_ADMITTED"
    if not isinstance(value, Mapping):
        return "MISSING", "OUTPUT_NOT_OBSERVED"
    if binding.requirement_id == "DECISION":
        return ("READY" if value.get("evidence_state") == "PROVEN" else state), "CANONICAL_DECISION_OBSERVED"
    if state not in {"READY", "PARTIAL"}:
        return state, "OUTPUT_NOT_READY"
    context = value.get("completed_bar_context") if binding.requirement_id == "SUPPLY" else value.get("context", value)
    if not isinstance(context, Mapping):
        return "UNKNOWN", "METHOD_CONTEXT_MISSING"
    contract = method_contract(binding)
    version_key = ("schema_version" if "SCHEMA_VERSION" in binding.version_ref else
                   "algorithm_version" if "ALGORITHM_VERSION" in binding.version_ref else "version")
    if contract["version"] is None or context.get(version_key) != contract["version"]:
        return "UNKNOWN", "METHOD_VERSION_MISMATCH"
    if contract["config_hash"] and context.get("config_hash") != contract["config_hash"]:
        return "UNKNOWN", "METHOD_CONFIG_MISMATCH"
    if binding.requirement_id in {"COST", "STRUCTURE", "MOMENTUM", "PATTERN", "CANDLESTICK", "MTF"} and context.get("completed_bar_only") is not True:
        return "UNKNOWN", "COMPLETED_BAR_NOT_PROVEN"
    if state == "READY" and binding.timeframe == "daily" and isinstance(contract["warmup"], (int, tuple)):
        minimum = contract["warmup"]
        count = context.get("observations")
        alignment = at(context, "source_alignment.status")
        if binding.requirement_id == "SUPPLY":
            count = at(context, "data_quality.observations")
            alignment = at(context, "data_quality.source_alignment")
        elif binding.requirement_id == "COST":
            minimum = max(minimum)
            window = at(context, "bar_reference_cost.window_" + str(minimum)) or {}
            count, alignment = window.get("observations"), window.get("source_alignment")
        if type(count) is not int or count < minimum:
            return "UNKNOWN", "WARMUP_NOT_PROVEN"
        if alignment != "SINGLE_SOURCE":
            return "UNKNOWN", "SOURCE_ALIGNMENT_NOT_PROVEN"
    if require_receipt:
        receipt_ok, receipt_reason = _receipt_state(binding, value, receipt)
        if not receipt_ok:
            return "UNKNOWN", receipt_reason
        return state, receipt_reason
    return state, "METHOD_METADATA_VERIFIED_NOT_INVOCATION_PROOF"


def timeframe_ready(factor: Mapping, timeframe: str) -> bool:
    binding = next(b for b in EVIDENCE_BINDINGS if b.requirement_id == "MTF")
    parent = at(factor, binding.path)
    strict = _receipt_required(factor)
    state, _ = method_observation(
        binding, parent, receipt=_receipt_for(factor, "MTF"), require_receipt=strict,
    )
    frame = at(parent, "timeframes." + timeframe)
    minimum = method_contract(binding)["warmup"]
    return bool(
        timeframe in {"monthly", "weekly", "daily"} and state in {"READY", "PARTIAL"}
        and isinstance(frame, Mapping) and frame.get("completed_bar_only") is True
        and _state(frame) == "READY" and at(frame, "trend.status") == "READY"
        and at(frame, "source_alignment.status") == "SINGLE_SOURCE"
        and type(frame.get("observations")) is int and isinstance(minimum, int)
        and frame["observations"] >= minimum
    )


def _resolve(binding: StrategyBinding, factor: Mapping) -> str:
    kind = binding.resolver
    if kind in {"deferred", "not_used"}:
        return "UNKNOWN"
    owner = next(b for b in EVIDENCE_BINDINGS if b.requirement_id == binding.requirement_id)
    checked, _ = method_observation(
        owner,
        at(factor, owner.path),
        receipt=_receipt_for(factor, owner.requirement_id),
        require_receipt=_receipt_required(factor),
    )
    if checked not in {"READY", "PARTIAL"}:
        return "UNKNOWN"
    regime = at(factor, "market_sector_regime") or {}
    if kind == "market":
        if at(regime, "market.data_quality") != "ok":
            return "UNKNOWN"
        return {"PERMISSIVE": "SATISFIED", "RISK_OFF": "FAILED"}.get(at(regime, "market.state"), "UNKNOWN")
    if kind == "sector":
        if at(regime, "sector.status") != "ok":
            return "UNKNOWN"
        return {"SUPPORTIVE": "SATISFIED", "COOLING": "FAILED"}.get(at(regime, "sector.state"), "UNKNOWN")
    if kind in {"weekly", "daily"}:
        frame = at(factor, "multi_timeframe_structure_context.timeframes." + kind)
        if not timeframe_ready(factor, kind):
            return "UNKNOWN"
        if at(frame, "source_alignment.status") != "SINGLE_SOURCE" or _state(frame) != "READY":
            return "UNKNOWN"
        if _state(frame.get("price_structure")) != "READY" or _state(frame.get("trend")) != "READY":
            return "UNKNOWN"
        return {"BULLISH": "SATISFIED", "BEARISH": "FAILED"}.get(at(frame, "trend.direction"), "UNKNOWN")
    supply = at(factor, "supply_demand_volume_price")
    if _state(supply) != "READY" or _state(at(supply, "completed_bar_context")) != "READY":
        return "UNKNOWN"
    volume = at(supply, "legacy_volume.status")
    if volume == "放量下跌":
        return "FAILED"
    if kind == "pullback":
        return "SATISFIED" if volume == "缩量回调" else "UNKNOWN"
    # Only the existing completed-bar state is reused, never a new numeric threshold.
    return {"DEMAND_PRESSURE": "SATISFIED", "SUPPLY_PRESSURE": "FAILED"}.get(
        at(supply, "completed_bar_context.state"), "UNKNOWN")


def build_strategy_eligibility(factor: Mapping, *, schema_version: str) -> dict:
    validate_registry()
    required = {b.clause: _resolve(b, factor) for b in STRATEGY_BINDINGS if b.classification == "HARD_ELIGIBILITY"}
    if factor.get("strategy_id") != STOCK_STRATEGY:
        required = dict.fromkeys(required, "UNKNOWN")
    state = "INELIGIBLE" if "FAILED" in required.values() else "UNKNOWN"
    # No positive full-strategy admission: multiple methods, including 30m, are deferred.
    reasons = ["REQUIRED_EVIDENCE_FAILED"] if state == "INELIGIBLE" else ["REQUIRED_EVIDENCE_INCOMPLETE"]
    return {"schema_version": schema_version, "strategy_id": factor.get("strategy_id"),
            "state": state, "required_evidence": required, "reason_codes": reasons}


def _runtime_observations(factor: Mapping) -> list[dict]:
    observations = []
    for binding in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,):
        value = at(factor, binding.path)
        declared = _state(value)
        deferred = binding.implementation_state != "EXISTING_REUSED"
        admitted_state, admission_reason = method_observation(
            binding,
            value,
            receipt=_receipt_for(factor, binding.requirement_id),
            require_receipt=_receipt_required(factor),
        )
        observations.append({"requirement_id": binding.requirement_id, "path": binding.path,
                             "state": admitted_state, "declared_state": declared,
                             "method_state": "NOT_ADMITTED" if deferred else ("OUTPUT_OBSERVED" if value is not None else "NOT_OBSERVED"),
                             "output_hash": digest(value) if value is not None else None,
                             "correlation_group": binding.correlation_group,
                             "reason": binding.reentry if deferred else admission_reason})
    return observations


def _mtf_method_matrix_state(method_id: str, factor: Mapping, timeframe: str, family: str) -> tuple[str, str]:
    frame = at(factor, f"multi_timeframe_structure_context.timeframes.{timeframe}")
    if not isinstance(frame, Mapping):
        return "MISSING", "MTF_FRAME_MISSING"
    frame_state = _state(frame)
    if frame_state not in {"READY", "PARTIAL"}:
        return frame_state if frame_state in TIMEFRAME_FAMILY_MATRIX_STATES else "UNKNOWN", str(frame.get("reason") or "MTF_FRAME_NOT_READY")
    if method_id == "MTF_MA_LEVEL_ALIGNMENT":
        trend = frame.get("trend")
        trend_state = _state(trend)
        if trend_state == "READY" and isinstance(trend, Mapping) and str(trend.get("ma_alignment") or "").strip():
            return "READY", "MTF_MA_ALIGNMENT_READY"
        return ("PARTIAL", "MTF_TREND_PARTIAL") if trend_state == "PARTIAL" else ("MISSING", "MTF_MA_ALIGNMENT_MISSING")
    if method_id == "MTF_MOMENTUM_CONTEXT":
        trend = frame.get("trend")
        if not isinstance(trend, Mapping) or _state(trend) not in {"READY", "PARTIAL"}:
            return "MISSING", "MTF_MOMENTUM_CONTEXT_MISSING"
        if timeframe in {"60m", "30m", "15m", "5m"}:
            if family == "SUPPLY":
                admitted = frame.get("admitted_methods")
                volume_ready = (
                    isinstance(admitted, (list, tuple))
                    and "VOLUME" in admitted
                    and str(trend.get("volume_status") or "").strip()
                )
                return (
                    ("PARTIAL", "MTF_VERIFIED_VOLUME_CONTEXT_ONLY")
                    if volume_ready else ("MISSING", "MTF_VOLUME_CONTEXT_NOT_ADMITTED")
                )
            if family == "MOMENTUM":
                admitted = frame.get("admitted_methods")
                methods = admitted if isinstance(admitted, (tuple, list)) else ()
                startup = frame.get("macd_startup_stability")
                macd_ready = (
                    "MACD" in methods
                    and isinstance(startup, Mapping)
                    and startup.get("status") == "READY"
                    and str(trend.get("macd_status") or "").strip()
                )
                rsi_startup = frame.get("rsi_startup_stability")
                rsi_ready = (
                    "RSI" in methods
                    and isinstance(rsi_startup, Mapping)
                    and rsi_startup.get("status") == "READY"
                    and str(trend.get("rsi_status") or "").strip()
                )
                return (
                    ("PARTIAL", "MTF_MACD_RSI_CONTEXT_ONLY")
                    if macd_ready or rsi_ready else ("MISSING", "MTF_MACD_RSI_CONTEXT_MISSING")
                )
        if family == "SUPPLY":
            # Higher-frame volume belongs to SUPPLY, never to MOMENTUM.
            return (
                ("PARTIAL", "MTF_HIGHER_VOLUME_CONTEXT_ONLY")
                if str(trend.get("volume_status") or "").strip()
                else ("MISSING", "MTF_HIGHER_VOLUME_NOT_OBSERVED")
            )
        if family == "MOMENTUM":
            macd_receipt = frame.get("macd_startup_stability")
            rsi_receipt = frame.get("rsi_startup_stability")
            macd_ok = (
                isinstance(macd_receipt, Mapping)
                and macd_receipt.get("status") == "READY"
                and str(trend.get("macd_status") or "").strip()
            )
            rsi_ok = (
                isinstance(rsi_receipt, Mapping)
                and rsi_receipt.get("status") == "READY"
                and str(trend.get("rsi_status") or "").strip()
            )
            return (
                ("PARTIAL", "MTF_HIGHER_VERIFIED_RECURSIVE_CONTEXT_ONLY")
                if macd_ok or rsi_ok
                else ("MISSING", "MTF_HIGHER_RECURSIVE_NOT_ADMITTED")
            )
        fields = [str(trend.get(key) or "").strip() for key in ("volume_status", "macd_status", "rsi_status")]
        ready = sum(bool(value) for value in fields)
        if ready == len(fields):
            return "READY", "MTF_MOMENTUM_CONTEXT_READY"
        if ready:
            return "PARTIAL", "MTF_MOMENTUM_CONTEXT_PARTIAL"
        return "MISSING", "MTF_MOMENTUM_CONTEXT_MISSING"
    if method_id == "MTF_PRICE_STRUCTURE":
        structure = frame.get("price_structure")
        state = _state(structure)
        if timeframe in {"60m", "30m", "15m", "5m"} and state == "READY":
            methods = frame.get("admitted_methods")
            pivots = structure.get("pivots") if isinstance(structure, Mapping) else None
            identity_hash = str(frame.get("intraday_identity_hash") or "")
            verified = (
                frame.get("completed_bar_only") is True
                and isinstance(structure, Mapping)
                and structure.get("timeframe") == timeframe
                and structure.get("completed_bar_only") is True
                and len(identity_hash) == 64
                and structure.get("intraday_identity_hash") == identity_hash
                and isinstance(methods, (list, tuple))
                and "PRICE_STRUCTURE" in methods
                and isinstance(pivots, list) and bool(pivots)
                and all(
                    isinstance(pivot, Mapping)
                    and pivot.get("provisional") is False
                    and str(pivot.get("origin_time") or "")
                    and str(pivot.get("confirmed_at") or "")
                    and str(pivot.get("confirmed_available_at") or "")
                    for pivot in pivots
                )
                and frame.get("strategy_admitted") is False
            )
            return (
                ("READY", f"MTF_{timeframe.upper()}_CONFIRMED_CONTEXT_ONLY")
                if verified else ("UNKNOWN", f"MTF_{timeframe.upper()}_STRUCTURE_RECEIPT_UNBOUND")
            )
        return (state, "MTF_PRICE_STRUCTURE_" + state) if state in TIMEFRAME_FAMILY_MATRIX_STATES else ("UNKNOWN", "MTF_PRICE_STRUCTURE_UNKNOWN")
    ma_structure = frame.get("ma_structure")
    if not isinstance(ma_structure, Mapping):
        return "MISSING", "MTF_MA_STRUCTURE_MISSING"
    readiness = ma_structure.get("readiness") if isinstance(ma_structure.get("readiness"), Mapping) else {}
    if method_id == "MA_SLOPE_CROSS":
        state = str((readiness.get("slope_cross") or {}).get("status") or "MISSING")
        return ("READY", "MA_SLOPE_CROSS_READY") if state == "READY" else ("MISSING", "MA_SLOPE_CROSS_NOT_READY")
    if method_id == "MA_COMPRESSION_RELEASE":
        compression = str((readiness.get("compression_context") or {}).get("status") or "MISSING")
        lifecycle = str((readiness.get("event_lifecycle") or {}).get("status") or "MISSING")
        if compression == "READY" and lifecycle == "READY" and ma_structure.get("status") == "READY":
            return "READY", "MA_COMPRESSION_RELEASE_READY"
        return "MISSING", "MA_COMPRESSION_RELEASE_NOT_READY"
    return frame_state, "MTF_METHOD_FRAME_STATE"


def _method_runtime_source_binding(
    factor: Mapping,
    binding: EvidenceBinding,
    profile: MethodWindowProfile,
    timeframe: str,
    state: str,
) -> dict:
    """Bind a current method leaf to its deterministic source receipt.

    The MTF carrier remains the preferred full-frame identity. This fallback is
    only for daily canonical evidence whose deterministic owner retained a
    completed-bar source receipt even when an MTF envelope was not supplied.
    It never grants data rights, PIT, learning or action authority.
    """
    result = {
        "timeframe": timeframe,
        "binding_scope": "NONE",
        "runtime_source_bound": False,
        "completed_bar_only": False,
        "observations": None,
        "completed_through": None,
        "source_alignment_state": "MISSING",
        "sources": (),
        "provider_identity": None,
        "adjustment_basis": None,
        "available_at_max": None,
        "source_receipt_hash": None,
        "identity_kind": None,
        "data_rights_admitted": False,
        "strategy_admitted": False,
        "learning_admitted": False,
        "independent_action_authority": False,
    }
    if timeframe != "daily" or state not in {"READY", "PARTIAL"}:
        return result
    payload = at(factor, binding.path)
    if not isinstance(payload, Mapping):
        return result
    context = payload.get("context")
    context = context if isinstance(context, Mapping) else payload

    completed_bar_only = context.get("completed_bar_only") is True
    observations = None
    completed_through = context.get("target_date") or context.get("end_date")
    sources: tuple[str, ...] = ()
    alignment_state = "MISSING"
    algorithm_version = context.get("algorithm_version") or payload.get("version")
    config_hash = context.get("config_hash")

    alignment = context.get("source_alignment")
    if isinstance(alignment, Mapping):
        alignment_state = str(alignment.get("status") or "")
        raw_sources = alignment.get("sources")
        if isinstance(raw_sources, (list, tuple)):
            sources = tuple(sorted({
                str(item).strip() for item in raw_sources if str(item).strip()
            }))
        value = alignment.get("observations", context.get("observations"))
        observations = value if type(value) is int and value > 0 else None
        completed_through = completed_through or alignment.get("end_date")
    elif binding.requirement_id == "SUPPLY":
        completed = payload.get("completed_bar_context")
        completed = completed if isinstance(completed, Mapping) else {}
        quality = completed.get("data_quality")
        quality = quality if isinstance(quality, Mapping) else {}
        alignment_state = str(quality.get("source_alignment") or "")
        raw_sources = quality.get("sources")
        if isinstance(raw_sources, (list, tuple)):
            sources = tuple(sorted({
                str(item).strip() for item in raw_sources if str(item).strip()
            }))
        value = quality.get("observations")
        observations = value if type(value) is int and value > 0 else None
        completed_through = completed.get("target_date") or quality.get("end_date")
        completed_bar_only = completed.get("completed_bar_only") is True
        algorithm_version = (
            completed.get("algorithm_version") or payload.get("version")
        )
        config_hash = completed.get("config_hash")
    elif binding.requirement_id == "COST":
        references = context.get("bar_reference_cost")
        references = references if isinstance(references, Mapping) else {}
        windows = [
            value for key, value in references.items()
            if key.startswith("window_") and isinstance(value, Mapping)
            and value.get("status") == "READY"
            and value.get("source_alignment") == "SINGLE_SOURCE"
        ]
        if windows:
            source_set = {
                str(source).strip()
                for window in windows
                for source in (window.get("sources") or ())
                if str(source).strip()
            }
            sources = tuple(sorted(source_set))
            alignment_state = "SINGLE_SOURCE" if len(sources) == 1 else "UNKNOWN"
            observed = [window.get("observations") for window in windows]
            observations = max(
                (value for value in observed if type(value) is int and value > 0),
                default=None,
            )
            completed_through = context.get("target_date") or max(
                (str(window.get("end_date")) for window in windows if window.get("end_date")),
                default=None,
            )
            completed_bar_only = context.get("completed_bar_only") is True
            algorithm_version = payload.get("version")

    source_bound = bool(
        completed_bar_only
        and alignment_state == "SINGLE_SOURCE"
        and len(sources) == 1
        and observations is not None
        and completed_through
    )
    if not source_bound:
        return {
            **result,
            "completed_bar_only": completed_bar_only,
            "observations": observations,
            "completed_through": completed_through,
            "source_alignment_state": alignment_state or "UNKNOWN",
            "sources": sources,
        }
    receipt = {
        "requirement_id": binding.requirement_id,
        "method_id": profile.method_id,
        "timeframe": timeframe,
        "canonical_path": binding.path,
        "sources": sources,
        "observations": observations,
        "completed_through": completed_through,
        "algorithm_version": algorithm_version,
        "config_hash": config_hash,
        "completed_bar_only": True,
    }
    source_receipt_hash = digest(receipt)
    return {
        **result,
        "binding_scope": "METHOD_CANONICAL_EVIDENCE",
        "runtime_source_bound": True,
        "completed_bar_only": True,
        "observations": observations,
        "completed_through": completed_through,
        "source_alignment_state": "SINGLE_SOURCE",
        "sources": sources,
        "provider_identity": sources[0],
        "source_receipt_hash": source_receipt_hash,
        "identity_kind": "METHOD_EVIDENCE_SOURCE_RECEIPT",
    }


def _cell_runtime_binding(frame_binding: Mapping, method_coverage: list[dict]) -> dict:
    """Choose a source-bound frame receipt or a same-source method receipt."""
    result = dict(frame_binding)
    if result.get("runtime_source_bound") is True:
        result["binding_scope"] = "TIMEFRAME_CARRIER"
        result["bound_method_ids"] = tuple(
            item["method_id"] for item in method_coverage
            if item["state"] in {"READY", "PARTIAL"}
        )
        result["source_receipt_hashes"] = ()
        return result
    bindings = [
        item.get("runtime_source_binding")
        for item in method_coverage
        if item.get("state") in {"READY", "PARTIAL"}
        and isinstance(item.get("runtime_source_binding"), Mapping)
        and item["runtime_source_binding"].get("runtime_source_bound") is True
    ]
    if not bindings:
        result["binding_scope"] = "NONE"
        result["bound_method_ids"] = ()
        result["source_receipt_hashes"] = ()
        return result
    sources = tuple(sorted({
        source for row in bindings for source in row.get("sources", ())
    }))
    hashes = tuple(sorted(str(row.get("source_receipt_hash")) for row in bindings))
    if len(sources) != 1 or any(len(value) != 64 for value in hashes):
        result["binding_scope"] = "METHOD_CANONICAL_EVIDENCE_UNALIGNED"
        result["bound_method_ids"] = ()
        result["source_receipt_hashes"] = hashes
        return result
    completed = tuple(sorted({
        str(row.get("completed_through"))
        for row in bindings if row.get("completed_through")
    }))
    observed = [row.get("observations") for row in bindings]
    result.update({
        "binding_scope": "METHOD_CANONICAL_EVIDENCE",
        "runtime_source_bound": True,
        "completed_bar_only": all(
            row.get("completed_bar_only") is True for row in bindings
        ),
        "observations": max(
            (value for value in observed if type(value) is int),
            default=None,
        ),
        "completed_through": completed[-1] if completed else None,
        "source_alignment_state": "SINGLE_SOURCE",
        "sources": sources,
        "provider_identity": sources[0],
        "data_identity_hash": digest({
            "method_source_receipt_hashes": hashes,
        }),
        "identity_kind": "METHOD_EVIDENCE_SOURCE_RECEIPT_SET",
        "bound_method_ids": tuple(
            item["method_id"] for item in method_coverage
            if isinstance(item.get("runtime_source_binding"), Mapping)
            and item["runtime_source_binding"].get("runtime_source_bound") is True
        ),
        "source_receipt_hashes": hashes,
    })
    return result


def _matrix_method_observation(
    profile: MethodWindowProfile,
    factor: Mapping,
    timeframe: str,
    family: str,
    observations: Mapping[str, Mapping],
) -> dict:
    binding = next(
        binding
        for binding in EVIDENCE_BINDINGS + DEFERRED_BINDINGS
        if binding.requirement_id == profile.requirement_id
    )
    canonical_path = binding.path
    if profile.requirement_id == "MTF":
        canonical_path = f"{binding.path}.timeframes.{timeframe}"
    if profile.implementation_state != "EXISTING_REUSED":
        state, reason = "NOT_ADMITTED", binding.reentry
    elif timeframe not in profile.current_execution_timeframes:
        state, reason = "NOT_ADMITTED", "METHOD_TIMEFRAME_NOT_ADMITTED"
    elif profile.requirement_id == "MTF":
        parent = observations.get("MTF", {})
        parent_state = str(parent.get("state") or "UNKNOWN")
        parent_state = (
            parent_state
            if parent_state in TIMEFRAME_FAMILY_MATRIX_STATES
            else "UNKNOWN"
        )
        if _receipt_required(factor) and parent_state not in {"READY", "PARTIAL"}:
            state = parent_state
            reason = str(
                parent.get("reason") or "MTF_PARENT_OBSERVATION_NOT_ADMITTED"
            )
        else:
            state, reason = _mtf_method_matrix_state(
                profile.method_id, factor, timeframe, family
            )
    else:
        observation = observations.get(profile.requirement_id, {})
        state = str(observation.get("state") or "UNKNOWN")
        state = state if state in TIMEFRAME_FAMILY_MATRIX_STATES else "UNKNOWN"
        reason = str(observation.get("reason") or "RUNTIME_OBSERVATION_UNKNOWN")
    if state == "READY" and family == "TREND_RS" and profile.method_id in {
        "MTF_MA_LEVEL_ALIGNMENT", "MA_SLOPE_CROSS",
    } and timeframe != "daily":
        state, reason = "PARTIAL", "TREND_MA_CONTEXT_ONLY_NO_RELATIVE_STRENGTH"
    if state == "READY" and family == "SUPPLY" and profile.method_id == "MTF_MOMENTUM_CONTEXT":
        state, reason = "PARTIAL", "VOLUME_CONTEXT_ONLY_NO_FULL_SUPPLY_ALGORITHM"
    if state == "READY" and family == "MOMENTUM" and profile.method_id == "MTF_MOMENTUM_CONTEXT":
        state, reason = "PARTIAL", "MACD_RSI_CONTEXT_ONLY_NO_FULL_MOMENTUM_FAMILY"
    runtime_source_binding = _method_runtime_source_binding(
        factor, binding, profile, timeframe, state,
    )
    parent_contract = (
        method_contract(binding)
        if binding.implementation_state == "EXISTING_REUSED"
        else {"version": None, "warmup": None, "config_hash": None}
    )
    method_receipt = {
        "method_id": profile.method_id,
        "requirement_id": profile.requirement_id,
        "timeframe": timeframe,
        "family": family,
        "owner": binding.owner,
        "parent_callable": binding.callable_name,
        "owner_source_sha256": (
            _owner_source_sha256(binding.owner)
            if binding.implementation_state == "EXISTING_REUSED"
            else None
        ),
        "parent_method_contract": parent_contract,
        "canonical_path": canonical_path,
        "state": state,
        "reason": reason,
        "leaf_implementation_state": profile.implementation_state,
        "runtime_source_binding": runtime_source_binding,
    }
    method_receipt["receipt_hash"] = digest(method_receipt)
    return {
        "method_id": profile.method_id,
        "requirement_id": profile.requirement_id,
        "consumer_scope": profile.consumer_scope,
        "family_targets": profile.family_targets,
        "canonical_path": canonical_path,
        "target_timeframes": profile.target_timeframes,
        "current_execution_timeframes": profile.current_execution_timeframes,
        "leaf_implementation_state": profile.implementation_state,
        "owner": binding.owner,
        "parent_callable": binding.callable_name,
        "owner_source_sha256": method_receipt["owner_source_sha256"],
        "parent_method_contract": parent_contract,
        "method_receipt_hash": method_receipt["receipt_hash"],
        "runtime_source_binding": runtime_source_binding,
        "state": state,
        "reason": reason,
        "correlation_group": binding.correlation_group,
        "independent_action_authority": False,
        "strategy_authority": "EXISTING_STRATEGY_MATRIX_ONLY",
    }

def _aggregate_family_state(method_coverage: list[dict]) -> tuple[str, str]:
    if not method_coverage:
        return "NOT_APPLICABLE", "NO_APPLICABLE_TECHNICAL_METHOD"
    current = [item for item in method_coverage if item["state"] != "NOT_ADMITTED"]
    if not current:
        return "NOT_ADMITTED", "NO_METHOD_ADMITTED_FOR_TIMEFRAME"
    if any(item["state"] in {"READY", "PARTIAL"} for item in current):
        if all(item["state"] == "READY" for item in current):
            return "READY", "ALL_CURRENT_METHODS_READY"
        return "PARTIAL", "PARTIAL_CURRENT_METHOD_COVERAGE"
    if any(item["state"] == "UNKNOWN" for item in current):
        return "UNKNOWN", "CURRENT_METHOD_STATE_UNKNOWN"
    if all(item["state"] == "NOT_APPLICABLE" for item in current):
        return "NOT_APPLICABLE", "CURRENT_METHOD_NOT_APPLICABLE"
    return "MISSING", "CURRENT_METHODS_MISSING"


def _timeframe_runtime_binding(factor: Mapping, timeframe: str) -> dict:
    """Bind one 7TF matrix row to the exact runtime carrier that produced it.

    This receipt proves only source/clock/identity attachment for the current
    matrix. It does not grant data rights, strategy authority, PIT eligibility
    or numeric readiness to a MISSING/UNKNOWN/NOT_ADMITTED family.
    """
    parent = at(factor, "multi_timeframe_structure_context")
    parent = parent if isinstance(parent, Mapping) else {}
    frame = at(parent, "timeframes." + timeframe)
    frame = frame if isinstance(frame, Mapping) else {}
    frame_state = _state(frame) if frame else "MISSING"
    if frame_state not in TIMEFRAME_FAMILY_MATRIX_STATES:
        frame_state = "UNKNOWN"
    alignment = frame.get("source_alignment")
    alignment = alignment if isinstance(alignment, Mapping) else {}
    alignment_state = str(alignment.get("status") or "")
    if alignment_state not in {"SINGLE_SOURCE", "MISSING", "UNKNOWN", "NOT_APPLICABLE"}:
        alignment_state = "UNKNOWN" if frame else "MISSING"
    observations = frame.get("observations")
    observations = observations if type(observations) is int and observations >= 0 else None
    completed_through = (
        frame.get("completed_through")
        or frame.get("latest_bar_end")
        or frame.get("latest_bar_date")
    )
    data_identity_hash = (
        frame.get("intraday_identity_hash")
        if timeframe in {"60m", "30m", "15m", "5m"}
        else parent.get("data_snapshot_identity")
    )
    data_identity_hash = str(data_identity_hash or "") or None
    sources = alignment.get("sources")
    sources = tuple(str(item) for item in sources) if isinstance(sources, (list, tuple)) else ()
    runtime_source_bound = bool(
        frame.get("completed_bar_only") is True
        and alignment_state == "SINGLE_SOURCE"
        and observations is not None
        and observations > 0
        and isinstance(data_identity_hash, str)
        and len(data_identity_hash) == 64
    )
    return {
        "timeframe": timeframe,
        "frame_state": frame_state,
        "completed_bar_only": frame.get("completed_bar_only") is True,
        "observations": observations,
        "completed_through": completed_through,
        "source_alignment_state": alignment_state,
        "sources": sources,
        "provider_identity": parent.get("provider_identity") or (sources[0] if len(sources) == 1 else None),
        "adjustment_basis": parent.get("adjustment_basis"),
        "available_at_max": parent.get("available_at_max"),
        "data_identity_hash": data_identity_hash,
        "runtime_source_bound": runtime_source_bound,
        "data_rights_admitted": False,
        "strategy_admitted": False,
        "learning_admitted": False,
        "independent_action_authority": False,
    }


def compile_timeframe_family_matrix(factor: Mapping, *, observations=None, policy_view=None) -> dict:
    """Derive one closed-world 7x8 Product coverage matrix from current method execution."""
    validate_registry()
    policy_view = compile_method_window_policy_view() if policy_view is None else policy_view
    if policy_view.get("schema_version") != METHOD_WINDOW_POLICY_VERSION:
        raise TraceabilityError("METHOD_WINDOW_POLICY_VERSION_MISMATCH")
    if digest({key: value for key, value in policy_view.items() if key != "view_hash"}) != policy_view.get("view_hash"):
        raise TraceabilityError("METHOD_WINDOW_POLICY_HASH_MISMATCH")
    observations = _runtime_observations(factor) if observations is None else list(observations)
    observation_lookup = {item["requirement_id"]: item for item in observations}
    profile_lookup = {profile.method_id: profile for profile in validate_method_window_profiles()}
    cells = []
    matrix_method_ids = set()
    for timeframe in TIMEFRAMES:
        runtime_binding = _timeframe_runtime_binding(factor, timeframe)
        for family in TECHNICAL_EVIDENCE_FAMILIES:
            method_coverage = []
            for row in policy_view["methods"]:
                if row["consumer_scope"] != "TECHNICAL_MATRIX" or family not in row["family_targets"] or timeframe not in row["target_timeframes"]:
                    continue
                profile = profile_lookup[row["method_id"]]
                method_coverage.append(
                    _matrix_method_observation(
                        profile,
                        factor,
                        timeframe,
                        family,
                        observation_lookup,
                    )
                )
                matrix_method_ids.add(profile.method_id)
            state, reason = _aggregate_family_state(method_coverage)
            if family == "MTF":
                mtf_parent = observation_lookup.get("MTF", {})
                parent_state = str(mtf_parent.get("state") or "UNKNOWN")
                if (
                    not _receipt_required(factor)
                    or parent_state in {"READY", "PARTIAL"}
                ):
                    frame = at(
                        factor,
                        f"multi_timeframe_structure_context.timeframes.{timeframe}",
                    )
                    if isinstance(frame, Mapping):
                        frame_state = _state(frame)
                        if frame_state in {"READY", "PARTIAL", "MISSING", "UNKNOWN"}:
                            state, reason = frame_state, "MTF_FRAME_" + frame_state
            cell = {
                "cell_id": timeframe + ":" + family,
                "timeframe": timeframe,
                "family": family,
                "state": state,
                "reason": reason,
                # One immutable-value snapshot per cell; callers must not be
                # able to mutate all eight families through a shared dict.
                "runtime_binding": _cell_runtime_binding(runtime_binding, method_coverage),
                "canonical_paths": tuple(dict.fromkeys(item["canonical_path"] for item in method_coverage)),
                "available_paths": tuple(dict.fromkeys(item["canonical_path"] for item in method_coverage if item["state"] in {"READY", "PARTIAL"})),
                "applicable_method_ids": tuple(item["method_id"] for item in method_coverage),
                "current_method_ids": tuple(item["method_id"] for item in method_coverage if item["state"] in {"READY", "PARTIAL"}),
                "deferred_method_ids": tuple(item["method_id"] for item in method_coverage if item["state"] == "NOT_ADMITTED"),
                "method_coverage": method_coverage,
                "correlation_groups": tuple(dict.fromkeys(item["correlation_group"] for item in method_coverage)),
                "independent_action_authority": False,
                "strategy_authority": "EXISTING_STRATEGY_MATRIX_ONLY",
            }
            cell["cell_hash"] = digest(cell)
            cells.append(cell)
    non_matrix_methods = []
    for profile in validate_method_window_profiles():
        if profile.consumer_scope == "TECHNICAL_MATRIX":
            continue
        binding = next(binding for binding in EVIDENCE_BINDINGS + DEFERRED_BINDINGS if binding.requirement_id == profile.requirement_id)
        observation = observation_lookup.get(profile.requirement_id, {})
        state = "NOT_ADMITTED" if profile.implementation_state != "EXISTING_REUSED" else str(observation.get("state") or "UNKNOWN")
        if state not in TIMEFRAME_FAMILY_MATRIX_STATES:
            state = "UNKNOWN"
        non_matrix_methods.append({
            "method_id": profile.method_id,
            "requirement_id": profile.requirement_id,
            "consumer_scope": profile.consumer_scope,
            "family_targets": profile.family_targets,
            "state": state,
            "reason": binding.reentry if state == "NOT_ADMITTED" else str(observation.get("reason") or "RUNTIME_OBSERVATION_UNKNOWN"),
            "canonical_path": binding.path,
            "correlation_group": binding.correlation_group,
            "independent_action_authority": False,
            "strategy_authority": "EXISTING_STRATEGY_MATRIX_ONLY",
        })
    if matrix_method_ids | {item["method_id"] for item in non_matrix_methods} != ACCEPTED_METHOD_WINDOW_IDS:
        raise TraceabilityError("METHOD_MATRIX_CLOSED_WORLD_MISMATCH")
    document = {
        "schema_version": "timeframe-family-matrix-v1",
        "manifest_hash": MANIFEST_HASH,
        "method_window_policy_version": METHOD_WINDOW_POLICY_VERSION,
        "method_window_policy_hash": policy_view["view_hash"],
        "timeframes": TIMEFRAMES,
        "families": TECHNICAL_EVIDENCE_FAMILIES,
        "cells": cells,
        "non_matrix_methods": non_matrix_methods,
        "policy": {
            "cell_state_is_family_availability_not_vote_count": True,
            "deferred_optional_methods_do_not_downgrade_admitted_family": True,
            "asset_research_model_methods_remain_closed_world_outside_matrix": True,
            "strategy_authority_remains_existing_strategy_matrix": True,
            "every_cell_has_runtime_source_binding": True,
            "cell_hash_covers_state_methods_and_runtime_binding": True,
        },
    }
    document["matrix_hash"] = digest(document)
    return document



def _verified_typed_price_bar_scope(factor: Mapping) -> dict[str, dict]:
    """Point-of-use 1d/5m provenance via the existing strict DSA MTF receipt."""
    if not _receipt_required(factor):
        return {}
    mtf = factor.get("multi_timeframe_structure_context")
    if not isinstance(mtf, Mapping):
        return {}
    binding = next(b for b in EVIDENCE_BINDINGS if b.requirement_id == "MTF")
    state, reason = method_observation(
        binding, mtf, receipt=_receipt_for(factor, "MTF"), require_receipt=True,
    )
    if state not in {"READY", "PARTIAL"} or reason != "METHOD_INVOCATION_VERIFIED":
        return {}
    frames = mtf.get("timeframes")
    if not isinstance(frames, Mapping):
        return {}
    target = str(mtf.get("target_date") or "")
    provider = str(mtf.get("provider_identity") or "")
    basis = str(mtf.get("adjustment_basis") or "")
    observed = str(mtf.get("available_at_max") or "")
    daily_identity = mtf.get("daily_data_identity") or {}
    daily_route = str(daily_identity.get("provider_route") or "") if isinstance(daily_identity, Mapping) else ""
    daily_currency = str(daily_identity.get("currency") or "UNKNOWN") if isinstance(daily_identity, Mapping) else "UNKNOWN"
    daily_volume_unit = str(daily_identity.get("volume_unit") or "UNKNOWN") if isinstance(daily_identity, Mapping) else "UNKNOWN"
    daily_amount_unit = str(daily_identity.get("amount_unit") or "UNKNOWN") if isinstance(daily_identity, Mapping) else "UNKNOWN"
    daily_revision = str(daily_identity.get("revision") or "UNKNOWN") if isinstance(daily_identity, Mapping) else "UNKNOWN"
    daily_vintage = str(daily_identity.get("vintage") or "UNKNOWN") if isinstance(daily_identity, Mapping) else "UNKNOWN"
    if not (target and provider and basis and observed):
        return {}
    if mtf.get("price_identity_reasons") or mtf.get("daily_data_identity_state") != "OBSERVED":
        return {}
    scope = {}
    daily = frames.get("daily")
    identity = str(mtf.get("daily_data_identity_hash") or "")
    # The existing MTF carrier publishes the exact consumed snapshot identity,
    # not the provider frame's nested content hash. Bind both identities instead
    # of adding a second provenance producer or guessing a hidden field.
    daily_content = str(mtf.get("data_snapshot_identity") or "")
    if (isinstance(daily, Mapping) and len(identity) == 64
        and len(daily_content) == 64
        and daily.get("status") == "READY"
        and daily.get("completed_bar_only") is True
        and daily.get("completed_through") == target
        and (daily.get("source_alignment") or {}).get("status") == "SINGLE_SOURCE"):
        scope["P_DAILY"] = {
            "state": "PARTIAL",
            "reason": "TYPED_1D_SOURCE_OBSERVED_RIGHTS_HISTORICAL_PIT_NOT_PROVEN",
            "provider_identity": provider,
            "source_identity_hash": identity,
            "source_content_sha256": daily_content,
            "source_observed_at": observed,
            "source_available_at": observed,
            "completed_through": target,
            "adjustment_basis": basis,
            "provider_route": daily_route,
            "currency": daily_currency,
            "volume_unit": daily_volume_unit,
            "amount_unit": daily_amount_unit,
            "units": {
                "price": f"{daily_currency}_PER_LISTED_UNIT" if daily_currency != "UNKNOWN" else "UNKNOWN",
                "volume": daily_volume_unit.upper(),
                "amount": daily_amount_unit.upper(),
            },
            "revision": daily_revision,
            "vintage": daily_vintage,
        }
    minute = frames.get("5m")
    session = minute.get("completed_source_session_receipt") if isinstance(minute, Mapping) else None
    if isinstance(session, Mapping):
        minute_hash = str(minute.get("intraday_identity_hash") or "")
        sessions = session.get("session_count")
        rows = session.get("row_count")
        dates = session.get("expected_session_dates")
        if (minute.get("status") in {"READY", "PARTIAL"}
            and minute.get("completed_bar_only") is True
            and (minute.get("source_alignment") or {}).get("status") == "SINGLE_SOURCE"
            and str(minute.get("latest_bar_end") or "").startswith(target + "T")
            and len(minute_hash) == 64 and session.get("identity_hash") == minute_hash
            and session.get("schema_version") == "cn-complete-5m-session-set-v1"
            and session.get("identity_state") == "OBSERVED"
            and session.get("provider_identity") == provider
            and session.get("observed_adjustment_basis") == basis
            and session.get("currency") not in (None, "", "UNKNOWN")
            and isinstance(sessions, int) and sessions >= 10
            and isinstance(rows, int) and rows == sessions * 48
            and minute.get("observations") == rows
            and isinstance(dates, list) and len(dates) == sessions
            and dates[-1] == target and session.get("bars_per_session") == 48
            and session.get("observed_at") and session.get("available_at_max")):
            unknown_units = any(
                session.get(k) in (None, "", "UNKNOWN")
                for k in ("volume_unit", "amount_unit")
            )
            scope["P_5M"] = {
                "state": "PARTIAL",
                "reason": ("TYPED_5M_PRICE_ONLY_UNITS_UNKNOWN_RIGHTS_PIT_NOT_PROVEN"
                           if unknown_units else "TYPED_5M_SOURCE_RIGHTS_PIT_NOT_PROVEN"),
                "provider_identity": session["provider_identity"],
                "provider_route": session.get("provider_route"),
                "source_identity_hash": minute_hash,
                "source_content_sha256": session["content_sha256"],
                "source_observed_at": session["observed_at"],
                "source_available_at": session["available_at_max"],
                "completed_through": target,
                "adjustment_basis": session["observed_adjustment_basis"],
                "currency": session["currency"],
                "volume_unit": session["volume_unit"],
                "amount_unit": session["amount_unit"],
                "completed_sessions": sessions,
                "units": {
                    "price": f"{session['currency']}_PER_LISTED_UNIT",
                    "volume": str(session["volume_unit"] or "UNKNOWN").upper(),
                    "amount": str(session["amount_unit"] or "UNKNOWN").upper(),
                },
                "revision": str(session.get("revision") or "UNKNOWN"),
                "vintage": str(session.get("vintage") or "UNKNOWN"),
            }
    return scope



def _verified_cn_market_source_scope(factor: Mapping) -> dict[str, dict]:
    """Admit only source identity for M_INDEX_CURRENT, never the index value."""
    if not _receipt_required(factor):
        return {}
    evidence = factor.get("market_sector_regime")
    if not isinstance(evidence, Mapping):
        return {}
    binding = next(b for b in EVIDENCE_BINDINGS if b.requirement_id == "REGIME")
    state, reason = method_observation(
        binding,
        evidence,
        receipt=_receipt_for(factor, "REGIME"),
        require_receipt=True,
    )
    if state not in {"READY", "PARTIAL"} or reason != "METHOD_INVOCATION_VERIFIED":
        return {}
    sources = evidence.get("source_observations")
    observation = (
        sources.get("M_INDEX_CURRENT") if isinstance(sources, Mapping) else None
    )
    if not isinstance(observation, Mapping):
        return {}
    upstream = evidence.get("upstream_identity")
    daily_upstream = (
        upstream.get("daily_market_context")
        if isinstance(upstream, Mapping) else None
    )
    target = (
        str(daily_upstream.get("trade_date") or "")
        if isinstance(daily_upstream, Mapping) else ""
    )
    static_expected = {
        "dataset_id": "M_INDEX_CURRENT",
        "source_state": "PARTIAL",
        "provider_identity": "BaostockFetcher",
        "completed_through": target,
        "source_data_rights": "UNKNOWN",
        "volume_unit": "UNKNOWN",
        "amount_unit": "UNKNOWN",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": False,
        "strategy_upgrade_authority": False,
    }
    if (
        not target
        or any(observation.get(key) != expected for key, expected in static_expected.items())
        or observation.get("source_code") not in {"sh.000001", "sh.000300"}
    ):
        return {}
    for key in ("content_sha256", "identity_hash"):
        value = str(observation.get(key) or "")
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
            return {}
    observed_at = str(observation.get("observed_at") or "")
    if not observed_at or ("+" not in observed_at[10:] and not observed_at.endswith("Z")):
        return {}
    return {
        "M_INDEX_CURRENT": {
            "state": "PARTIAL",
            "reason": "CURRENT_SOURCE_IDENTITY_VERIFIED_VALUE_NOT_ADMITTED",
            "provider_identity": observation["provider_identity"],
            "source_code": observation["source_code"],
            "completed_through": observation["completed_through"],
            "source_observed_at": observed_at,
            "source_identity_hash": observation["identity_hash"],
            "source_content_sha256": observation["content_sha256"],
            "source_data_rights": "UNKNOWN",
            "historical_pit_eligible": False,
            "independent_source_value_admitted": False,
            "strategy_upgrade_authority": False,
        }
    }


def _verified_cn_market_5m_source_scope(factor: Mapping) -> dict[str, dict]:
    """Only a strict REGIME parent may carry a 5m source receipt forward.

    Market index returns, numeric vote, warmup, storage, PIT and rights all
    remain independent closed gates.
    """
    if not _receipt_required(factor):
        return {}
    evidence = factor.get("market_sector_regime")
    if not isinstance(evidence, Mapping):
        return {}
    parent = next(b for b in EVIDENCE_BINDINGS if b.requirement_id == "REGIME")
    state, reason = method_observation(
        parent, evidence, receipt=_receipt_for(factor, "REGIME"),
        require_receipt=True,
    )
    if state not in {"READY", "PARTIAL"} or reason != "METHOD_INVOCATION_VERIFIED":
        return {}
    sources = evidence.get("source_observations")
    receipt = sources.get("M_INDEX_BARS") if isinstance(sources, Mapping) else None
    upstream = evidence.get("upstream_identity")
    market_context = upstream.get("daily_market_context") if isinstance(upstream, Mapping) else None
    target = str(market_context.get("trade_date") or "") if isinstance(market_context, Mapping) else ""
    expected = {
        "schema_version": "cn-index-completed-5m-source-v1",
        "dataset_id": "M_INDEX_BARS",
        "source_state": "PARTIAL",
        "provider_identity": "EastmoneyIndex5mAdapter",
        "provider_route": "eastmoney.index_zh_a_hist_min_em|push2his/qt/stock/kline/get",
        "completed_through": target,
        "source_data_rights": "UNKNOWN",
        "volume_unit": "UNKNOWN",
        "amount_unit": "UNKNOWN",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": False,
        "native_recursive_stability_admitted": False,
        "strategy_upgrade_authority": False,
    }
    if (
        not target or not isinstance(receipt, Mapping)
        or set(receipt) != set(expected) | {
            "source_code", "observed_at", "content_sha256",
            "identity_hash", "session_count", "row_count", "receipt_sha256",
        }
        or any(receipt.get(k) != v for k, v in expected.items())
        or receipt.get("source_code") not in {"sh.000001", "sh.000300"}
    ):
        return {}
    from data_provider.daily_data_identity import sha256_payload
    if sha256_payload({k: v for k, v in receipt.items() if k != "receipt_sha256"}) != receipt.get("receipt_sha256"):
        return {}
    for key in ("content_sha256", "identity_hash"):
        digest = str(receipt.get(key) or "")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
            return {}
    sessions, rows = receipt.get("session_count"), receipt.get("row_count")
    if (
        type(sessions) is not int or type(rows) is not int or sessions < 1
        or rows != 48 * sessions
    ):
        return {}
    from datetime import datetime, date
    from src.core.trading_calendar import resolve_latest_completed_session_fail_closed
    try:
        observed = datetime.fromisoformat(str(receipt.get("observed_at") or ""))
        if observed.tzinfo is None or observed.utcoffset() is None:
            return {}
        if resolve_latest_completed_session_fail_closed("cn", current_time=observed) < date.fromisoformat(target):
            return {}
    except (TypeError, ValueError):
        return {}
    return {
        "M_INDEX_BARS": {
            "state": "PARTIAL",
            "reason": "INDEX_5M_COMPLETED_SOURCE_VERIFIED_RECURSIVE_METHOD_NOT_ADMITTED",
            "provider_identity": receipt["provider_identity"],
            "source_code": receipt["source_code"],
            "completed_through": receipt["completed_through"],
            "source_observed_at": receipt["observed_at"],
            "source_identity_hash": receipt["identity_hash"],
            "source_content_sha256": receipt["content_sha256"],
            "source_data_rights": "UNKNOWN",
            "historical_pit_eligible": False,
            "independent_source_value_admitted": False,
            "native_recursive_stability_admitted": False,
            "strategy_upgrade_authority": False,
            "volume_unit": receipt["volume_unit"],
            "amount_unit": receipt["amount_unit"],
        },
    }


def _verified_cn_market_7tf_scope(factor: Mapping) -> dict[str, dict]:
    """Project one verified formal-index 7TF context onto existing Market objects."""
    if not _receipt_required(factor):
        return {}
    evidence = factor.get("market_sector_regime")
    if not isinstance(evidence, Mapping):
        return {}
    parent = next(b for b in EVIDENCE_BINDINGS if b.requirement_id == "REGIME")
    state, reason = method_observation(
        parent,
        evidence,
        receipt=_receipt_for(factor, "REGIME"),
        require_receipt=True,
    )
    if state not in {"READY", "PARTIAL"} or reason != "METHOD_INVOCATION_VERIFIED":
        return {}
    sources = evidence.get("source_observations")
    context = sources.get("M_INDEX_7TF") if isinstance(sources, Mapping) else None
    if not isinstance(context, Mapping):
        return {}
    try:
        from src.services.multi_timeframe_structure_service import (
            verify_formal_cn_market_index_7tf_context,
        )

        verified = verify_formal_cn_market_index_7tf_context(context)
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}
    upstream = evidence.get("upstream_identity")
    daily_upstream = (
        upstream.get("daily_market_context")
        if isinstance(upstream, Mapping)
        else None
    )
    target = (
        str(daily_upstream.get("trade_date") or "")
        if isinstance(daily_upstream, Mapping)
        else ""
    )
    if not target or verified.get("target_date") != target:
        return {}
    trace = verified.get("method_window_readiness_trace")
    timeframes = verified.get("timeframes")
    if not isinstance(trace, Mapping) or not isinstance(timeframes, Mapping):
        return {}
    rows = trace.get("rows")
    if not isinstance(rows, list) or len(rows) != 14:
        return {}
    states = {
        str(item.get("status") or "UNKNOWN")
        for item in rows
        if isinstance(item, Mapping)
    }
    if not states or not states <= {"READY", "PARTIAL", "MISSING", "UNKNOWN"}:
        return {}
    completed_timeframes = {
        key: {
            "status": str((timeframes.get(key) or {}).get("status") or "UNKNOWN"),
            "observations": (timeframes.get(key) or {}).get("observations"),
        }
        for key in TIMEFRAMES
    }
    common = {
        "state": "PARTIAL",
        "reason": "FORMAL_INDEX_7TF_METHOD_TRACE_VERIFIED_RIGHTS_UNITS_PIT_NOT_ADMITTED",
        "source_code": verified["source_code"],
        "completed_through": verified["target_date"],
        "source_identity_hash": verified["context_hash"],
        "method_trace_hash": trace.get("trace_hash"),
        "method_trace_status": trace.get("status"),
        "timeframes": completed_timeframes,
        "source_data_rights": "UNKNOWN",
        "volume_unit": "UNKNOWN",
        "amount_unit": "UNKNOWN",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": False,
        "strategy_upgrade_authority": False,
        "storage_admitted": False,
        "learning_admitted": False,
    }
    daily = verified.get("daily_source_observation")
    minute = verified.get("minute_source_observation")
    current = dict(common)
    bars = dict(common)
    if isinstance(daily, Mapping):
        current.update({
            "provider_identity": daily.get("provider_identity"),
            "source_observed_at": daily.get("observed_at"),
            "source_content_sha256": daily.get("content_sha256"),
        })
    if isinstance(minute, Mapping):
        bars.update({
            "provider_identity": minute.get("provider_identity"),
            "source_observed_at": minute.get("observed_at"),
            "source_content_sha256": minute.get("content_sha256"),
            "completed_sessions": minute.get("session_count"),
        })
    return {
        "M_INDEX_CURRENT": current,
        "M_INDEX_BARS": bars,
    }


def _verified_cn_sector_scope(factor: Mapping) -> dict[str, dict]:
    """Project one verified formal-Sector composite onto existing Sector objects."""
    if not _receipt_required(factor):
        return {}
    evidence = factor.get("market_sector_regime")
    if not isinstance(evidence, Mapping):
        return {}
    parent = next(b for b in EVIDENCE_BINDINGS if b.requirement_id == "REGIME")
    state, reason = method_observation(
        parent,
        evidence,
        receipt=_receipt_for(factor, "REGIME"),
        require_receipt=True,
    )
    if state not in {"READY", "PARTIAL"} or reason != "METHOD_INVOCATION_VERIFIED":
        return {}
    sources = evidence.get("source_observations")
    context = sources.get("S_FORMAL_CONTEXT") if isinstance(sources, Mapping) else None
    if not isinstance(context, Mapping):
        return {}
    try:
        from src.services.formal_sector_context_service import (
            verify_formal_cn_sector_known_at_context,
        )

        verified = verify_formal_cn_sector_known_at_context(context)
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}
    upstream = evidence.get("upstream_identity")
    daily_upstream = (
        upstream.get("daily_market_context")
        if isinstance(upstream, Mapping)
        else None
    )
    target = (
        str(daily_upstream.get("trade_date") or "")
        if isinstance(daily_upstream, Mapping)
        else ""
    )
    if not target or verified.get("target_date") != target:
        return {}
    classification = verified.get("classification")
    membership = verified.get("membership")
    prices = verified.get("prices_7tf")
    relative = verified.get("relative_strength")
    nonprice = verified.get("nonprice")
    if not all(
        isinstance(item, Mapping)
        for item in (classification, membership, prices, relative, nonprice)
    ):
        return {}
    trace = prices.get("method_window_readiness_trace")
    timeframes = prices.get("timeframes")
    rows = trace.get("rows") if isinstance(trace, Mapping) else None
    if (
        not isinstance(trace, Mapping)
        or not isinstance(timeframes, Mapping)
        or not isinstance(rows, list)
        or len(rows) != 14
    ):
        return {}
    completed_timeframes = {
        key: {
            "status": str((timeframes.get(key) or {}).get("status") or "UNKNOWN"),
            "observations": (timeframes.get(key) or {}).get("observations"),
        }
        for key in TIMEFRAMES
    }
    common = {
        "state": "PARTIAL",
        "reason": "FORMAL_SECTOR_COMPOSITE_VERIFIED_RIGHTS_PIT_NOT_ADMITTED",
        "sector_id": verified.get("sector_id"),
        "sector_name": verified.get("sector_name"),
        "classification_standard": classification.get("standard_id"),
        "classification_version": classification.get("version"),
        "membership_denominator": membership.get("denominator"),
        "completed_through": verified.get("target_date"),
        "source_identity_hash": verified.get("context_hash"),
        "source_data_rights": "UNKNOWN",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": False,
        "strategy_upgrade_authority": False,
        "storage_admitted": False,
        "learning_admitted": False,
    }

    def component_row(name: str, *, reason_code: str) -> dict:
        component = nonprice.get(name)
        row = dict(common)
        if not isinstance(component, Mapping):
            row.update({"state": "UNKNOWN", "reason": "FORMAL_SECTOR_COMPONENT_INVALID"})
            return row
        status = str(component.get("status") or "UNKNOWN")
        if status == "MISSING":
            row.update({
                "state": "MISSING",
                "reason": str(component.get("reason") or "FORMAL_SECTOR_COMPONENT_MISSING"),
            })
        elif status in {"READY", "PARTIAL"}:
            row.update({
                "state": "PARTIAL",
                "reason": reason_code,
                "source_observed_at": component.get("observed_at"),
                "source_available_at": component.get("available_at"),
                "provider_identity": component.get("provider_identity"),
                "component": dict(component),
            })
        else:
            row.update({"state": "UNKNOWN", "reason": "FORMAL_SECTOR_COMPONENT_STATE_INVALID"})
        return row

    classification_row = dict(common)
    classification_row.update({
        "reason": "FORMAL_SECTOR_CLASSIFICATION_AND_ASOF_MEMBERSHIP_VERIFIED_RIGHTS_PIT_NOT_ADMITTED",
        "standard_name": classification.get("standard_name"),
        "effective_from": classification.get("effective_from"),
        "effective_to": classification.get("effective_to"),
        "member_codes_sha256": membership.get("member_codes_sha256"),
        "provider_identity": classification.get("provider_identity"),
        "source_observed_at": classification.get("observed_at"),
        "source_available_at": classification.get("available_at"),
    })
    price_row = dict(common)
    price_row.update({
        "reason": "FORMAL_SECTOR_7TF_AND_MARKET_RS_VERIFIED_RIGHTS_UNITS_PIT_NOT_ADMITTED",
        "sector_price_code": verified.get("sector_price_code"),
        "method_trace_hash": trace.get("trace_hash"),
        "method_trace_status": trace.get("status"),
        "timeframes": completed_timeframes,
        "relative_strength": dict(relative),
        "volume_unit": "UNKNOWN",
        "amount_unit": "UNKNOWN",
    })
    return {
        "S_CLASSIFICATION": classification_row,
        "S_PRICES_RS": price_row,
        "S_BREADTH": component_row(
            "breadth", reason_code="FORMAL_SECTOR_BREADTH_WITH_EXACT_DENOMINATOR_VERIFIED"
        ),
        "S_TURNOVER": component_row(
            "turnover", reason_code="FORMAL_SECTOR_TURNOVER_AND_MARKET_SHARE_VERIFIED"
        ),
        "S_FUND_FLOW": component_row(
            "fund_flow", reason_code="FORMAL_SECTOR_FUND_FLOW_PROXY_BOUNDED_NO_INTENT_INFERENCE"
        ),
        "S_LEADER": component_row(
            "leader", reason_code="FORMAL_SECTOR_LEADER_IS_RANK_ONLY_NOT_ACTION_AUTHORITY"
        ),
        "S_VALUATION": component_row(
            "valuation", reason_code="FORMAL_SECTOR_DATED_VALUATION_EARNINGS_VINTAGE_VERIFIED"
        ),
    }


def _compile_accepted_data_object_scope(factor: Mapping) -> list[dict]:
    """Expose accepted DATA obligations, not invented data values or strategy votes."""
    rows = [
        (dataset_id, domain, fact, frequency, consumer)
        for domain, consumer, entries in _DATA_OBJECT_CATALOG
        for dataset_id, fact, frequency in entries
    ]
    ids = {row[0] for row in rows}
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if (
        len(rows) != 76
        or len(ids) != 76
        or sha256(payload).hexdigest() != ACCEPTED_DATA_OBJECT_SUBSET_SHA256
    ):
        raise TraceabilityError("ACCEPTED_DATA_OBJECT_CATALOG_MISMATCH")
    # Asset route is a producer-owned Factor fact. A mutable investor_brief
    # presentation label must never change trace identity after construction.
    asset_route = str(factor.get("asset_type") or "stock").lower().strip()
    typed_price_scope = (
        _verified_typed_price_bar_scope(factor)
        if asset_route in {"stock", "etf"} else {}
    )
    if asset_route == "stock":
        base_source_scope = typed_price_scope
    elif asset_route == "etf":
        base_source_scope = {}
        for source_id, target_id in (
            ("P_DAILY", "E_PRICE_1D"),
            ("P_5M", "E_PRICE_5M"),
        ):
            if source_id in typed_price_scope:
                mapped = dict(typed_price_scope[source_id])
                mapped["reason"] = "ETF_" + str(mapped.get("reason") or "")
                mapped["source_data_rights"] = "UNKNOWN"
                mapped["historical_pit_eligible"] = False
                mapped["independent_source_value_admitted"] = False
                mapped["strategy_upgrade_authority"] = False
                base_source_scope[target_id] = mapped
    else:
        base_source_scope = {}
    market_source_scope = _verified_cn_market_source_scope(factor)
    market_source_scope.update(_verified_cn_market_5m_source_scope(factor))
    market_source_scope.update(_verified_cn_market_7tf_scope(factor))
    sector_source_scope = _verified_cn_sector_scope(factor)
    objects = []
    for dataset_id, domain, fact, frequency, consumer in rows:
        applicable = (
            (domain != "ETF" or asset_route == "etf")
            and (domain != "STOCK" or asset_route == "stock")
        )
        row = {
            "dataset_id": dataset_id,
            "domain": domain,
            "fact_or_fields": fact,
            "native_frequency": frequency,
            "product_consumer": consumer,
            "state": "UNKNOWN" if applicable else "NOT_APPLICABLE",
            "reason": (
                "NO_INDEPENDENT_TYPED_DATA_OBJECT_RECEIPT"
                if applicable else "DIFFERENT_ASSET_ROUTE"
            ),
            "independent_source_value_admitted": False,
            "strategy_upgrade_authority": False,
        }
        if applicable and dataset_id in base_source_scope:
            row.update(base_source_scope[dataset_id])
        if applicable and dataset_id in market_source_scope:
            row.update(market_source_scope[dataset_id])
        if applicable and dataset_id in sector_source_scope:
            row.update(sector_source_scope[dataset_id])
        objects.append(row)
    return objects



def compile_complete_research_universe_view(
    factor: Mapping,
    *,
    observations=None,
    policy_view=None,
    matrix=None,
) -> dict:
    """Compile the full research universe; the 7x8 matrix is only its technical subset."""
    validate_registry()
    observations = _runtime_observations(factor) if observations is None else list(observations)
    observation_lookup = {item["requirement_id"]: item for item in observations}
    policy_view = compile_method_window_policy_view() if policy_view is None else policy_view
    matrix = (
        compile_timeframe_family_matrix(
            factor,
            observations=observations,
            policy_view=policy_view,
        )
        if matrix is None
        else matrix
    )
    method_rows = tuple(dict(item) for item in policy_view.get("methods", ()))
    method_ids = tuple(item.get("method_id") for item in method_rows)
    if frozenset(method_ids) != ACCEPTED_METHOD_WINDOW_IDS or len(method_ids) != len(ACCEPTED_METHOD_WINDOW_IDS):
        raise TraceabilityError("COMPLETE_RESEARCH_UNIVERSE_METHOD_SET_MISMATCH")

    bindings = EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)
    binding_lookup = {binding.requirement_id: binding for binding in bindings}
    role_lookup = dict(ACCEPTED_REQUIREMENT_ROLES)
    strategy_rows = tuple(asdict(binding) for binding in STRATEGY_BINDINGS)
    requirements = []
    planes = []
    seen_requirements = set()

    for plane_name, requirement_ids in COMPLETE_RESEARCH_UNIVERSE_PLANES:
        plane_method_ids = []
        for requirement_id in requirement_ids:
            if requirement_id in seen_requirements:
                raise TraceabilityError("COMPLETE_RESEARCH_UNIVERSE_REQUIREMENT_DUPLICATE:" + requirement_id)
            seen_requirements.add(requirement_id)
            binding = binding_lookup.get(requirement_id)
            if binding is None:
                raise TraceabilityError("COMPLETE_RESEARCH_UNIVERSE_REQUIREMENT_MISSING:" + requirement_id)

            if requirement_id == "DECISION":
                decision = factor.get("canonical_decision")
                state = "READY" if isinstance(decision, Mapping) else "UNKNOWN"
                reason = "CANONICAL_DECISION_PRESENT" if state == "READY" else "CANONICAL_DECISION_MISSING"
            else:
                observation = observation_lookup.get(requirement_id, {})
                state = str(observation.get("state") or "UNKNOWN")
                if state not in TIMEFRAME_FAMILY_MATRIX_STATES:
                    state = "UNKNOWN"
                reason = str(observation.get("reason") or "RUNTIME_OBSERVATION_UNKNOWN")

            requirement_methods = tuple(
                item["method_id"]
                for item in method_rows
                if item.get("requirement_id") == requirement_id
            )
            plane_method_ids.extend(requirement_methods)
            requirement_scopes = tuple(dict.fromkeys(
                str(item.get("consumer_scope") or "")
                for item in method_rows
                if item.get("requirement_id") == requirement_id
            ))
            family_targets = tuple(dict.fromkeys(
                family
                for item in method_rows
                if item.get("requirement_id") == requirement_id
                for family in item.get("family_targets", ())
            ))
            requirements.append({
                "plane": plane_name,
                "requirement_id": requirement_id,
                "contract_role": role_lookup[requirement_id],
                "state": state,
                "reason": reason,
                "owner": binding.owner,
                "canonical_path": binding.path,
                "timeframe": binding.timeframe,
                "asset_routes": binding.asset_routes,
                "correlation_group": binding.correlation_group,
                "reentry": binding.reentry,
                "method_ids": requirement_methods,
                "consumer_scopes": requirement_scopes,
                "family_targets": family_targets,
                "strategy_clauses": tuple(
                    row["clause"] for row in strategy_rows
                    if row["requirement_id"] == requirement_id
                ),
            })
        planes.append({
            "plane": plane_name,
            "requirement_ids": requirement_ids,
            "method_ids": tuple(dict.fromkeys(plane_method_ids)),
        })

    accepted_requirement_ids = frozenset(requirement_id for requirement_id, _ in ACCEPTED_REQUIREMENT_ROLES)
    if frozenset(seen_requirements) != accepted_requirement_ids:
        raise TraceabilityError("COMPLETE_RESEARCH_UNIVERSE_REQUIREMENT_SET_MISMATCH")

    cells = matrix.get("cells")
    if not isinstance(cells, list) or len(cells) != 56:
        raise TraceabilityError("COMPLETE_RESEARCH_UNIVERSE_TECHNICAL_MATRIX_INVALID")

    matrix_state_counts = {
        state: sum(1 for cell in cells if cell.get("state") == state)
        for state in sorted(TIMEFRAME_FAMILY_MATRIX_STATES)
    }
    source_bound_timeframes = tuple(
        timeframe for timeframe in TIMEFRAMES
        if any(
            cell.get("timeframe") == timeframe
            and (cell.get("runtime_binding") or {}).get("runtime_source_bound") is True
            for cell in cells
        )
    )
    data_objects = _compile_accepted_data_object_scope(factor)
    admission_receipts = factor.get("data_object_admission_receipts")
    admission_view = None
    if admission_receipts is not None:
        if not isinstance(admission_receipts, Mapping):
            raise TraceabilityError("DATA_OBJECT_ADMISSION_RECEIPTS_INVALID")
        target_as_of = (
            factor.get("research_as_of")
            or at(factor, "multi_timeframe_structure_context.snapshot_observed_at")
            or at(factor, "multi_timeframe_structure_context.available_at_max")
            or at(factor, "shared_report_context.research_as_of")
        )
        # A Product receipt on a bare historical/legacy factor, or one with
        # only a trade-date label, has no precise frozen known-at deadline.
        # A valid hash and asserted rights alone cannot admit it to the
        # current research consumer.
        # Empty receipts are the lawful missing-data nontrigger; they must
        # not fail the report, but also cannot produce a READY object.
        if admission_receipts and not target_as_of:
            raise TraceabilityError(
                "DATA_OBJECT_ADMISSION_RESEARCH_ASOF_MISSING"
            )
        research_operation_id = str(factor.get("research_operation_id") or "").strip()
        research_intended_use = str(factor.get("research_intended_use") or "").strip()
        if admission_receipts and (
            not research_operation_id or research_intended_use != "NO_SEND_RESEARCH"
        ):
            raise TraceabilityError(
                "DATA_OBJECT_ADMISSION_OPERATION_CONTEXT_MISSING_OR_INVALID"
            )
        try:
            admission_view = compile_data_object_admission_view(
                data_objects,
                admission_receipts,
                as_of=target_as_of,
                operation_id=research_operation_id,
                intended_use=research_intended_use,
            )
        except DataObjectAdmissionError as exc:
            raise TraceabilityError(str(exc)) from exc

    document = {
        "schema_version": COMPLETE_RESEARCH_UNIVERSE_SCHEMA_VERSION,
        "manifest_hash": MANIFEST_HASH,
        "method_window_policy_hash": policy_view["view_hash"],
        "timeframe_family_matrix_hash": matrix["matrix_hash"],
        "technical_matrix": {
            "cell_count": len(cells),
            "timeframes": matrix.get("timeframes"),
            "families": matrix.get("families"),
            "state_counts": matrix_state_counts,
            "source_bound_timeframes": source_bound_timeframes,
            "all_cells_have_runtime_binding": all(
                isinstance(cell.get("runtime_binding"), Mapping) for cell in cells
            ),
            "all_cells_have_hash": all(
                len(str(cell.get("cell_hash") or "")) == 64 for cell in cells
            ),
        },
        "planes": planes,
        "requirements": requirements,
        "data_objects": data_objects,
        "data_objects_count": 76,
        **(
            {
                "data_object_admission": admission_view,
                "data_object_admission_hash": admission_view["view_hash"],
            }
            if admission_view is not None else {}
        ),
        "data_object_scope_schema": "accepted-data-obligation-scope-v1",
        "data_object_source_graph_sha256": ACCEPTED_DATA_OBJECT_GRAPH_SHA256,
        "data_object_subset_sha256": ACCEPTED_DATA_OBJECT_SUBSET_SHA256,
        "methods": method_rows,
        "strategy_bindings": strategy_rows,
        "counts": {
            "requirements": len(requirements),
            "methods": len(method_rows),
            "strategy_bindings": len(strategy_rows),
            "technical_matrix_cells": len(cells),
        },
        "policy": {
            "timeframe_family_matrix_is_technical_subset_only": True,
            "deferred_requirements_remain_named_obligations": True,
            "asset_research_model_layers_remain_in_same_closed_world": True,
            "same_source_same_swing_methods_are_not_independent_votes": True,
            "email_llm_are_projection_consumers_not_evidence_producers": True,
            "data_object_rights_are_operation_specific_and_fail_closed": True,
            "product_admission_never_implies_storage_training_or_redistribution": True,
        },
    }
    document["universe_hash"] = digest(document)
    return document


def build_runtime_trace(factor: Mapping) -> dict:
    validate_registry()
    known_paths = {b.path for b in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)}
    if digest(manifest_document()) != MANIFEST_HASH:
        raise TraceabilityError("MANIFEST_CHANGED_DURING_PROCESS")
    for key, value in factor.items():
        if (key.endswith("_evidence") or (isinstance(value, Mapping) and "family" in value)) and key not in known_paths:
            raise TraceabilityError("ORPHAN_CANONICAL_EVIDENCE:" + key)
    observations = _runtime_observations(factor)
    policy_view = compile_method_window_policy_view()
    matrix = compile_timeframe_family_matrix(factor, observations=observations, policy_view=policy_view)
    universe = compile_complete_research_universe_view(
        factor,
        observations=observations,
        policy_view=policy_view,
        matrix=matrix,
    )
    mtf = at(factor, "multi_timeframe_structure_context") or {}
    document = {"schema_version": TRACE_VERSION, "manifest_version": MANIFEST_VERSION,
                "manifest_hash": MANIFEST_HASH, "strategy_id": factor.get("strategy_id"),
                "method_window_policy_version": METHOD_WINDOW_POLICY_VERSION,
                "method_window_policy_hash": policy_view["view_hash"],
                "timeframe_family_matrix_hash": matrix["matrix_hash"],
                "timeframe_family_matrix": matrix,
                "complete_research_universe_hash": universe["universe_hash"],
                "complete_research_universe": universe,
                "data_snapshot_identity": mtf.get("data_snapshot_identity"),
                "data_identity": {key: mtf.get(key) for key in ("provider_identity", "adjustment_basis", "available_at_max", "target_date", "algorithm_version", "config_hash")},
                "observations": observations,
                "strategy_eligibility": factor.get("strategy_eligibility"),
                "canonical_decision": factor.get("canonical_decision"),
                "product_state": "NOT_RENDERED_NOT_AUTHORIZED",
                "ledger_state": "NOT_YET_PERSISTED",
                "independent_vote_count": None}
    # New qualified-score receipts are part of the same runtime identity,
    # not a separate scoring/graph authority. Legacy historical traces without
    # this field retain their prior exact wire shape and manifest identity.
    if isinstance(factor.get("score_admission"), Mapping):
        document["score_admission"] = factor.get("score_admission")
        document["composite_score"] = factor.get("composite_score")
    document["runtime_trace_hash"] = digest(document)
    return document


def validate_runtime_trace(factor: Mapping) -> dict:
    actual = factor.get("evidence_traceability")
    expected = build_runtime_trace(factor)
    if not isinstance(actual, Mapping) or not json_wire_equal(actual, expected):
        raise TraceabilityError("RUNTIME_TRACE_MISSING_OR_STALE")
    return expected


def learning_projection(factor: Mapping) -> dict:
    """Versioned allowlisted values only; old audit snapshots remain untouched."""
    trace = validate_runtime_trace(factor)
    states = {o["requirement_id"]: o["state"] for o in trace["observations"]}
    values = {}
    for metric in METRIC_BINDINGS:
        value = at(factor, metric["path"])
        ready = states[metric["requirement_id"]] == "READY"
        if metric["timeframe"] in {"monthly", "weekly"}:
            ready = timeframe_ready(factor, metric["timeframe"]) and states["MTF"] in {"READY", "PARTIAL"}
        numeric = _metric_value_valid(metric, value)
        values[metric["id"]] = {"value": float(value) if ready and numeric else None,
                               "state": "READY" if ready and numeric else "MISSING_OR_UNADMITTED"}
    return {"schema_version": "stock-factor-numeric-evidence-v2", "manifest_hash": MANIFEST_HASH,
            "trace_identity": trace_identity(trace), "values": values,
            "training_admitted": False}


def _metric_value_valid(metric: Mapping, value: Any) -> bool:
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(float(value)):
        return False
    number = float(value)
    metric_id = str(metric.get("id") or "")
    if metric_id in {"daily.macd_dif", "daily.macd_dea", "daily.macd_bar"}:
        return True
    unit = metric.get("unit")
    if unit == "index_0_100":
        return 0.0 <= number <= 100.0
    if unit == "annualized_pct":
        return number >= 0.0
    if unit in {"PRICE_BASIS_CURRENCY", "PRICE_BASIS_CURRENCY_PROXY"}:
        return number > 0.0
    if metric_id in {"daily.volume_ratio20", "daily.tr_sma20_not_wilder_atr"}:
        return number >= 0.0
    if metric_id in {"daily.directional_volume", "daily.cmf20"}:
        return -1.0 <= number <= 1.0
    return True


def describe_macd_state(result: Any) -> str:
    """Project an existing enum, never trust an action-like free-text signal."""
    if getattr(result, "macd_signal", None) == "数据不足":
        return ""
    raw = getattr(result, "macd_status", None)
    status = str(getattr(raw, "value", raw) or "")
    dif, dea = getattr(result, "macd_dif", None), getattr(result, "macd_dea", None)
    numeric = all(isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(float(v)) for v in (dif, dea))
    if status in {"多头", "空头"}:
        if not numeric:
            return ""
        if dif > 0 and dea > 0:
            return "MACD DIF/DEA位于零轴上方，动量偏强"
        if dif < 0 and dea < 0:
            return "MACD DIF/DEA位于零轴下方，动量偏弱"
        return "MACD处于零轴附近或两线异侧，方向需结构确认"
    if status == "金叉" and numeric and dif < 0:
        return "MACD零轴下金叉，局部动量修复；不是趋势反转证明"
    descriptions = {
        "零轴上金叉": "MACD零轴上金叉，动量交叉确认；不是独立买点",
        "金叉": "MACD金叉，仅为动量修复；需核零轴位置与高周期结构",
        "死叉": "MACD死叉，动量转弱；结合高周期结构与风险条件",
        "上穿零轴": "MACD DIF上穿零轴，动量改善",
        "下穿零轴": "MACD DIF下穿零轴，动量走弱",
        "多头": "MACD零轴上方动量状态，仍需结构确认",
        "空头": "MACD零轴下方动量状态，仍需结构确认",
    }
    return descriptions.get(status, "")


def trace_identity(trace: Mapping) -> dict:
    return {key: trace.get(key) for key in (
        "manifest_version", "manifest_hash", "runtime_trace_hash", "data_snapshot_identity",
        "method_window_policy_hash", "timeframe_family_matrix_hash", "complete_research_universe_hash",
    )}


validate_registry()
