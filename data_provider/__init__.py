# -*- coding: utf-8 -*-
"""
===================================
数据源策略层 - 包初始化
===================================

本包实现策略模式管理多个数据源，实现：
1. 统一的数据获取接口
2. 自动故障切换
3. 防封禁流控策略

数据源优先级（动态调整）：
【配置了 TUSHARE_TOKEN 时】
1. TushareFetcher (Priority 0) - 🔥 最高优先级（动态提升）
2. EfinanceFetcher (Priority 0) - 同优先级
3. AkshareFetcher (Priority 1) - 来自 akshare 库
4. PytdxFetcher (Priority 2) - 来自 pytdx 库（通达信）
5. BaostockFetcher (Priority 3) - 来自 baostock 库
6. YfinanceFetcher (Priority 4) - 来自 yfinance 库
7. TencentFetcher (Priority 5) - 腾讯直连日 K 最终兜底

【未配置 TUSHARE_TOKEN 时】
1. EfinanceFetcher (Priority 0) - 最高优先级，来自 efinance 库
2. AkshareFetcher (Priority 1) - 来自 akshare 库
3. PytdxFetcher (Priority 2) - 来自 pytdx 库（通达信）
4. TushareFetcher (Priority 2) - 来自 tushare 库（不可用）
5. BaostockFetcher (Priority 3) - 来自 baostock 库
6. YfinanceFetcher (Priority 4) - 来自 yfinance 库
7. TencentFetcher (Priority 5) - 腾讯直连日 K 最终兜底
8. LongbridgeFetcher (Priority 5) - 长桥 OpenAPI（美股/港股兜底，与 Tencent 市场不重叠）

提示：优先级数字越小越优先，同优先级按初始化顺序排列
"""

from importlib import import_module

# Public API is unchanged. Loading a provenance helper must not instantiate all
# optional quote vendors, trigger their patches, or require unrelated SDKs.
# A named provider is imported only when its exported symbol is requested.
_EXPORT_MODULES = {
    "BaseFetcher": ".base",
    "DataFetcherManager": ".base",
    "EfinanceFetcher": ".efinance_fetcher",
    "TencentFetcher": ".tencent_fetcher",
    "AkshareFetcher": ".akshare_fetcher",
    "TushareFetcher": ".tushare_fetcher",
    "PytdxFetcher": ".pytdx_fetcher",
    "BaostockFetcher": ".baostock_fetcher",
    "YfinanceFetcher": ".yfinance_fetcher",
    "LongbridgeFetcher": ".longbridge_fetcher",
    "FinnhubFetcher": ".finnhub_fetcher",
    "AlphaVantageFetcher": ".alphavantage_fetcher",
    "is_us_index_code": ".us_index_mapping",
    "is_us_stock_code": ".us_index_mapping",
    "is_hk_stock_code": ".us_index_mapping",
    "get_us_index_yf_symbol": ".us_index_mapping",
    "US_INDEX_MAPPING": ".us_index_mapping",
}


def __getattr__(name: str):
    module = _EXPORT_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))

__all__ = [
    'BaseFetcher',
    'DataFetcherManager',
    'EfinanceFetcher',
    'TencentFetcher',
    'AkshareFetcher',
    'TushareFetcher',
    'PytdxFetcher',
    'BaostockFetcher',
    'YfinanceFetcher',
    'LongbridgeFetcher',
    'FinnhubFetcher',
    'AlphaVantageFetcher',
    'is_us_index_code',
    'is_us_stock_code',
    'is_hk_stock_code',
    'get_us_index_yf_symbol',
    'US_INDEX_MAPPING',
]
