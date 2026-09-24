# -*- coding: utf-8 -*-
"""
===================================
股票智能分析系统 - 大盘复盘模块（支持 A 股 / 港股 / 美股 / 日本 / 韩国）
===================================

职责：
1. 根据 MARKET_REVIEW_REGION 配置选择市场区域（cn / hk / us / jp / kr / both）
2. 执行大盘复盘分析并生成复盘报告
3. 保存和发送复盘报告
"""

import logging
import inspect
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, Optional
import uuid

from src.config import get_config
from src.notification import NotificationService
from src.market_analyzer import MarketAnalyzer
from src.report_language import normalize_report_language
from src.search_service import SearchService
from src.analyzer import AnalysisResult, GeminiAnalyzer
from src.llm.generation_backend import GenerationError
from src.services.run_diagnostics import (
    current_diagnostic_snapshot,
    record_history_run,
    record_notification_run,
)
from src.schemas.market_light import MARKET_LIGHT_REGIONS
from src.utils.market_review_region import (
    MARKET_REVIEW_REGION_ORDER,
    normalize_market_review_region_lenient,
)


logger = logging.getLogger(__name__)

MARKET_REVIEW_HISTORY_CODE = "MARKET"
MARKET_REVIEW_REPORT_TYPE = "market_review"
_MARKET_REVIEW_MARKETS = (
    ('cn', 'cn_title', 'A 股'),
    ('hk', 'hk_title', '港股'),
    ('us', 'us_title', '美股'),
    ('jp', 'jp_title', '日股'),
    ('kr', 'kr_title', '韩股'),
)
_MARKET_REVIEW_REGION_ORDER = MARKET_REVIEW_REGION_ORDER


@dataclass
class MarketReviewRunResult:
    """Structured result for API/Web consumers while keeping Markdown compatibility."""

    report: str
    market_review_payload: Dict[str, Any] = field(default_factory=dict)


def _refresh_market_review_history_diagnostics(*, query_id: str) -> None:
    """Refresh persisted market-review diagnostics after late flow events are recorded."""
    diagnostic_snapshot = current_diagnostic_snapshot()
    if diagnostic_snapshot is None:
        return

    try:
        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        updater = getattr(db, "update_analysis_history_diagnostics", None)
        if callable(updater):
            updater(
                query_id=query_id,
                code=MARKET_REVIEW_HISTORY_CODE,
                diagnostics=diagnostic_snapshot,
            )
    except Exception as exc:
        logger.warning("回写大盘复盘运行诊断失败（fail-open）: %s", exc)


def _record_market_review_notification_run(
    *,
    query_id: str,
    channel: str,
    status: str,
    success: bool,
    attempts: int = 1,
    error_message: Optional[Any] = None,
) -> None:
    record_notification_run(
        channel=channel,
        status=status,
        success=success,
        attempts=attempts,
        error_message=error_message,
    )
    _refresh_market_review_history_diagnostics(query_id=query_id)


def _collect_market_light_snapshot(
    snapshots: Dict[str, Dict[str, Any]],
    *,
    region: str,
    review_result: Any,
) -> None:
    if region not in MARKET_LIGHT_REGIONS:
        return
    snapshot = getattr(review_result, "market_light_snapshot", None)
    if isinstance(snapshot, dict) and snapshot:
        snapshots[region] = snapshot


def _get_market_review_text(language: str) -> dict[str, str]:
    normalized = normalize_report_language(language)
    if normalized == "en":
        return {
            "root_title": "# 🎯 Market Review",
            "push_title": "🎯 Market Review",
            "cn_title": "# A-share Market Recap",
            "us_title": "# US Market Recap",
            "hk_title": "# HK Market Recap",
            "jp_title": "# Japan Market Recap",
            "kr_title": "# Korea Market Recap",
            "separator": "> Next market recap follows",
        }
    if normalized == "ko":
        return {
            "root_title": "# 🎯 시황 리뷰",
            "push_title": "🎯 시황 리뷰",
            "cn_title": "# 중국 A주 시황 리뷰",
            "us_title": "# 미국 시황 리뷰",
            "hk_title": "# 홍콩 시황 리뷰",
            "jp_title": "# 일본 시황 리뷰",
            "kr_title": "# 한국 시황 리뷰",
            "separator": "> 다음 시장 시황 리뷰",
        }
    return {
        "root_title": "# 🎯 大盘复盘",
        "push_title": "🎯 大盘复盘",
        "cn_title": "# A股大盘复盘",
        "us_title": "# 美股大盘复盘",
        "hk_title": "# 港股大盘复盘",
        "jp_title": "# 日股大盘复盘",
        "kr_title": "# 韩股大盘复盘",
        "separator": "> 以下为下一市场大盘复盘",
    }


def _get_market_review_market_heading(language: Any, market: str) -> str:
    review_text = _get_market_review_text(str(language or "zh"))
    title_key = next(
        (candidate_title_key for mkt, candidate_title_key, _ in _MARKET_REVIEW_MARKETS if mkt == market),
        "",
    )
    return str(review_text.get(title_key) or market.upper()).lstrip("#").strip()


def _market_review_region_metadata(region: Any) -> str:
    normalized = str(region or "").strip().lower()
    if normalized in {market for market, _, _ in _MARKET_REVIEW_MARKETS}:
        return f"[dsa-market-region]: # ({normalized})\n\n"
    return ""


def _build_market_review_email_subject(payload: Any) -> str:
    """Build a stable Market-envelope subject from already-produced payload facts."""
    if not isinstance(payload, dict):
        payload = {}
    date_text = str(payload.get("date") or "").strip()
    regions: list[str] = []
    markets = payload.get("markets")
    if isinstance(markets, dict) and markets:
        regions = [region for region in _MARKET_REVIEW_REGION_ORDER if region in markets]
        if not date_text:
            for region in regions:
                market_payload = markets.get(region)
                if isinstance(market_payload, dict):
                    date_text = str(market_payload.get("date") or "").strip()
                    if date_text:
                        break
    else:
        region = str(payload.get("region") or "").strip().lower()
        if region:
            regions = [item for item in region.split(",") if item]

    if not date_text:
        date_text = datetime.now().strftime("%Y-%m-%d")

    labels = {
        "cn": "A股",
        "hk": "港股",
        "us": "美股",
        "jp": "日股",
        "kr": "韩股",
    }
    scope = " + ".join(labels.get(region, region.upper()) for region in regions if region)
    if not scope:
        scope = "市场"
    return f"【开盘前市场｜{date_text}】{scope}｜完整市场研究"


def _resolve_market_review_regions(raw_region: Optional[str]) -> list[str]:
    """Normalize MARKET_REVIEW_REGION into an ordered, non-empty region list."""

    normalized = normalize_market_review_region_lenient(raw_region) or "cn"
    return normalized.split(",")


def run_market_review(
    notifier: NotificationService,
    analyzer: Optional[GeminiAnalyzer] = None,
    search_service: Optional[SearchService] = None,
    config: Optional[object] = None,
    send_notification: bool = True,
    merge_notification: bool = False,
    override_region: Optional[str] = None,
    query_id: Optional[str] = None,
    return_structured: bool = False,
    save_report_file: bool = True,
    persist_history: bool = True,
    trigger_source: str = "cli",
) -> Optional[str] | Optional[MarketReviewRunResult]:
    """
    执行大盘复盘分析

    Args:
        notifier: 通知服务
        analyzer: AI分析器（可选）
        search_service: 搜索服务（可选）
        config: 本次复盘使用的配置（可选，未传时读取全局配置）
        send_notification: 是否发送通知
        merge_notification: 是否合并推送（跳过本次推送，由 main 层合并个股+大盘后统一发送，Issue #190）
        override_region: 覆盖 config 的 market_review_region（Issue #373 交易日过滤后有效子集）
        query_id: 历史记录关联 ID；API 后台任务会传入 task_id，CLI/Bot 为空时自动生成
        save_report_file: 是否保存 Markdown 文件；上下文生成路径可关闭以避免多区域临时复盘互相覆盖
        persist_history: 是否写入 analysis_history；预热路径可关闭以避免覆盖用户可见的同日大盘复盘记录
        trigger_source: 触发来源，用于日志排障（cli/schedule/api/bot/service 等）

    Returns:
        复盘报告文本
    """
    runtime_config = config or get_config()
    history_query_id = query_id or f"market_review_{uuid.uuid4().hex}"
    review_text = _get_market_review_text(getattr(runtime_config, "report_language", "zh"))
    raw_region = (
        override_region
        if override_region is not None
        else (getattr(runtime_config, 'market_review_region', 'cn') or 'cn')
    )
    run_markets = _resolve_market_review_regions(raw_region)
    persist_region = ','.join(run_markets) if len(run_markets) > 1 else run_markets[0]
    logger.info(
        "[MarketReview] component=market_review action=start trigger_source=%s query_id=%s region=%s",
        trigger_source,
        history_query_id,
        persist_region,
    )

    try:
        if len(run_markets) > 1:
            # 多市场顺序执行，合并报告
            parts = []
            market_light_snapshots: Dict[str, Dict[str, Any]] = {}
            market_review_payloads: Dict[str, Dict[str, Any]] = {}
            for mkt, title_key, label in _MARKET_REVIEW_MARKETS:
                if mkt not in run_markets:
                    continue
                logger.info(
                    "[MarketReview] component=market_review action=build_report "
                    "trigger_source=%s query_id=%s region=%s label=%s",
                    trigger_source,
                    history_query_id,
                    mkt,
                    label,
                )
                mkt_analyzer = MarketAnalyzer(
                    search_service=search_service,
                    analyzer=analyzer,
                    region=mkt,
                    config=runtime_config,
                )
                review_result = mkt_analyzer.run_daily_review_with_snapshot()
                mkt_report = review_result.report
                _collect_market_light_snapshot(
                    market_light_snapshots,
                    region=mkt,
                    review_result=review_result,
                )
                market_review_payloads[mkt] = _coerce_market_review_payload(
                    review_result,
                    region=mkt,
                    report=mkt_report,
                )
                if mkt_report:
                    parts.append(f"{review_text[title_key]}\n\n{mkt_report}")
            if parts:
                review_report = f"\n\n---\n\n{review_text['separator']}\n\n".join(parts)
            else:
                review_report = None
        else:
            run_region = run_markets[0]
            label = next(
                (market_label for mkt, _, market_label in _MARKET_REVIEW_MARKETS if mkt == run_region),
                run_region,
            )
            logger.info(
                "[MarketReview] component=market_review action=build_report "
                "trigger_source=%s query_id=%s region=%s label=%s",
                trigger_source,
                history_query_id,
                run_region,
                label,
            )
            market_analyzer = MarketAnalyzer(
                search_service=search_service,
                analyzer=analyzer,
                region=run_region,
                config=runtime_config,
            )
            review_result = market_analyzer.run_daily_review_with_snapshot()
            review_report = review_result.report
            market_light_snapshots = {}
            _collect_market_light_snapshot(
                market_light_snapshots,
                region=run_region,
                review_result=review_result,
            )
            market_review_payloads = {
                run_region: _coerce_market_review_payload(
                    review_result,
                    region=run_region,
                    report=review_report,
                )
            }
        
        if review_report:
            market_review_payload = _build_combined_market_review_payload(
                review_report=review_report,
                payloads=market_review_payloads,
                region=persist_region,
                language=getattr(runtime_config, "report_language", "zh"),
                root_title=review_text["root_title"],
            )
            markdown_report = _render_market_review_payload_markdown(
                market_review_payload,
                wrapper_title=review_text["root_title"],
            )
            merge_markdown_report = _render_market_review_merge_markdown(
                market_review_payload,
                review_report=review_report,
            )
            if save_report_file:
                # 保存报告到文件
                date_str = datetime.now().strftime('%Y%m%d')
                report_filename = f"market_review_{date_str}.md"
                filepath = notifier.save_report_to_file(
                    markdown_report,
                    report_filename
                )
                logger.info(
                    "[MarketReview] component=market_review action=save_report "
                    "trigger_source=%s query_id=%s region=%s path=%s",
                    trigger_source,
                    history_query_id,
                    persist_region,
                    filepath,
                )

            if persist_history:
                _persist_market_review_history(
                    review_report=review_report,
                    markdown_report=markdown_report,
                    region=persist_region,
                    config=runtime_config,
                    query_id=history_query_id,
                    market_light_snapshots=market_light_snapshots,
                    market_review_payload=market_review_payload,
                )
            
            # 推送通知（合并模式下跳过，由 main 层统一发送）
            if merge_notification and send_notification:
                logger.info(
                    "[MarketReview] component=market_review action=skip_standalone_notification "
                    "trigger_source=%s query_id=%s region=%s",
                    trigger_source,
                    history_query_id,
                    persist_region,
                )
                _record_market_review_notification_run(
                    query_id=history_query_id,
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
            elif send_notification and notifier.is_available():
                # 添加标题
                report_content = _render_market_review_payload_markdown(
                    market_review_payload,
                    wrapper_title=review_text["push_title"],
                )

                send_kwargs: Dict[str, Any] = {
                    "email_send_to_all": True,
                    "route_type": "report",
                }
                try:
                    send_parameters = inspect.signature(notifier.send).parameters
                except (TypeError, ValueError):
                    send_parameters = {}
                if "structured_payload" in send_parameters:
                    send_kwargs["structured_payload"] = market_review_payload
                if "email_subject" in send_parameters:
                    send_kwargs["email_subject"] = _build_market_review_email_subject(
                        market_review_payload
                    )
                success = notifier.send(report_content, **send_kwargs)
                _record_market_review_notification_run(
                    query_id=history_query_id,
                    channel="report",
                    status="success" if success else "failed",
                    success=success,
                )
                if success:
                    logger.info(
                        "[MarketReview] component=market_review action=send_notification "
                        "status=success trigger_source=%s query_id=%s region=%s",
                        trigger_source,
                        history_query_id,
                        persist_region,
                    )
                else:
                    logger.warning(
                        "[MarketReview] component=market_review action=send_notification "
                        "status=failed trigger_source=%s query_id=%s region=%s",
                        trigger_source,
                        history_query_id,
                        persist_region,
                    )
            elif not send_notification:
                logger.info(
                    "[MarketReview] component=market_review action=skip_notification "
                    "reason=no_notify trigger_source=%s query_id=%s region=%s",
                    trigger_source,
                    history_query_id,
                    persist_region,
                )
                _record_market_review_notification_run(
                    query_id=history_query_id,
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
            else:
                logger.info(
                    "[MarketReview] component=market_review action=skip_notification "
                    "reason=not_configured trigger_source=%s query_id=%s region=%s",
                    trigger_source,
                    history_query_id,
                    persist_region,
                )
                _record_market_review_notification_run(
                    query_id=history_query_id,
                    channel="report",
                    status="not_configured",
                    success=False,
                    attempts=0,
                )
            
            if return_structured:
                return MarketReviewRunResult(
                    report=review_report,
                    market_review_payload=market_review_payload,
                )
            if merge_notification:
                return merge_markdown_report
            return review_report
        
    except GenerationError:
        logger.exception(
            "[MarketReview] component=market_review action=failed "
            "reason=generation_backend_config trigger_source=%s query_id=%s region=%s",
            trigger_source,
            history_query_id,
            persist_region,
        )
        raise
    except Exception:
        logger.exception(
            "[MarketReview] component=market_review action=failed "
            "trigger_source=%s query_id=%s region=%s",
            trigger_source,
            history_query_id,
            persist_region,
        )
    
    return None


def _coerce_market_review_payload(
    review_result: Any,
    *,
    region: str,
    report: Optional[str],
) -> Dict[str, Any]:
    payload = getattr(review_result, "structured_payload", None)
    if isinstance(payload, dict) and payload:
        return payload
    return {
        "version": 1,
        "kind": MARKET_REVIEW_REPORT_TYPE,
        "region": region,
        "title": "",
        "sections": [{"key": "full_review", "title": "Review", "markdown": report or ""}],
        "markdown_report": report or "",
    }


def _build_combined_market_review_payload(
    *,
    review_report: str,
    payloads: Dict[str, Dict[str, Any]],
    region: str,
    language: str,
    root_title: str,
) -> Dict[str, Any]:
    normalized_language = normalize_report_language(language)
    title = root_title.lstrip("#").strip()
    if len(payloads) == 1:
        payload = dict(next(iter(payloads.values())))
        payload["version"] = payload.get("version") or 1
        payload["kind"] = MARKET_REVIEW_REPORT_TYPE
        payload["region"] = region
        payload["language"] = payload.get("language") or normalized_language
        payload["root_title"] = title
        payload["markdown_report"] = review_report
        return payload
    return {
        "version": 1,
        "kind": MARKET_REVIEW_REPORT_TYPE,
        "region": region,
        "language": normalized_language,
        "title": title,
        "root_title": title,
        "markets": payloads,
        "markdown_report": review_report,
    }


def _render_market_review_payload_markdown(
    payload: Dict[str, Any],
    *,
    wrapper_title: Optional[str] = None,
) -> str:
    """Render Markdown from the structured market-review payload for file/push compatibility."""
    metadata = _market_review_region_metadata(payload.get("region"))
    body = _render_market_review_payload_body(payload)
    body = _insert_morning_plain_language_overlay(body, payload)
    if wrapper_title:
        return f"{metadata}{wrapper_title}\n\n{body}".strip()
    return f"{metadata}{body}".strip()


def _render_market_review_merge_markdown(
    payload: Dict[str, Any],
    *,
    review_report: str,
) -> str:
    """Render market-review body for the outer combined notification wrapper."""
    markets = payload.get("markets")
    if isinstance(markets, dict) and markets:
        return _render_market_review_payload_markdown(payload)
    rendered = _append_missing_market_brief_payload_block(review_report, payload)
    rendered = _append_missing_sector_payload_block(rendered, payload)
    return _insert_morning_plain_language_overlay(rendered, payload)


def render_market_review_region_projection(
    payload: Any,
    *,
    regions: Iterable[str],
) -> str:
    """Render only already-produced market payloads for the requested regions."""

    if not isinstance(payload, dict) or not payload:
        return ""

    requested = {
        str(region or "").strip().lower()
        for region in regions
        if str(region or "").strip().lower() in _MARKET_REVIEW_REGION_ORDER
    }
    if not requested:
        return ""

    markets = payload.get("markets")
    if isinstance(markets, dict) and markets:
        parts = []
        for market in _MARKET_REVIEW_REGION_ORDER:
            if market not in requested:
                continue
            market_payload = markets.get(market)
            if not isinstance(market_payload, dict) or not market_payload:
                continue
            rendered = _render_single_market_review_payload(market_payload).strip()
            if rendered:
                parts.append(rendered)
        return "\n\n---\n\n".join(parts).strip()

    region = str(payload.get("region") or "").strip().lower()
    if region not in requested:
        return ""
    return _render_single_market_review_payload(payload).strip()


def _render_market_review_payload_body(payload: Dict[str, Any]) -> str:
    markets = payload.get("markets")
    if isinstance(markets, dict) and markets:
        markdown_report = payload.get("markdown_report")
        if isinstance(markdown_report, str) and markdown_report.strip():
            original_markdown = markdown_report.strip()
            rendered = original_markdown
            for market in _MARKET_REVIEW_REGION_ORDER:
                market_payload = markets.get(market)
                if not isinstance(market_payload, dict):
                    continue
                title_prefix = str(market_payload.get("title") or market.upper()).strip()
                wrapper_title = _get_market_review_market_heading(payload.get("language"), market)
                segment_title_prefix = title_prefix
                if wrapper_title and _extract_market_markdown_segment(original_markdown, wrapper_title):
                    segment_title_prefix = wrapper_title
                rendered = _append_missing_market_brief_payload_block_to_market_segment(
                    rendered,
                    market_payload,
                    title_prefix=title_prefix,
                    segment_title_prefix=segment_title_prefix,
                )
                rendered = _append_missing_sector_payload_block_to_market_segment(
                    rendered,
                    market_payload,
                    title_prefix=title_prefix,
                    segment_title_prefix=segment_title_prefix,
                )
            return rendered
        parts = []
        for market in _MARKET_REVIEW_REGION_ORDER:
            market_payload = markets.get(market)
            if isinstance(market_payload, dict):
                parts.append(_render_single_market_review_payload(market_payload))
        return "\n\n---\n\n".join(part for part in parts if part).strip()
    return _render_single_market_review_payload(payload)


def _render_single_market_review_payload(payload: Dict[str, Any]) -> str:
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        markdown = payload.get("markdown_report")
        rendered = markdown if isinstance(markdown, str) else ""
        rendered = _append_missing_market_brief_payload_block(rendered, payload)
        return _append_missing_sector_payload_block(rendered, payload)

    title = payload.get("title")
    normalized_title = _normalize_market_review_heading(title)
    lines = []
    if isinstance(title, str) and title.strip():
        lines.extend([f"## {title.strip()}", ""])
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_title = str(section.get("title") or "").strip()
        markdown = str(section.get("markdown") or "").strip()
        if not markdown:
            continue
        should_render_section_title = (
            section_title
            and section.get("key") != "overview"
            and _normalize_market_review_heading(section_title) != normalized_title
        )
        if should_render_section_title:
            lines.extend([f"### {section_title}", ""])
        lines.extend([markdown, ""])
    rendered = _append_missing_market_brief_payload_block(
        "\n".join(lines).strip(),
        payload,
    )
    return _append_missing_sector_payload_block(rendered, payload)


def _append_missing_market_brief_payload_block(
    markdown: str,
    payload: Dict[str, Any],
    *,
    title_prefix: str = "",
    existing_markdown: Optional[Any] = None,
    segment_title_prefix: str = "",
) -> str:
    market_brief_block = _render_market_brief_payload_markdown_block(
        payload,
        title_prefix=title_prefix,
    )
    if not market_brief_block:
        return markdown.strip()
    markdown_to_check = markdown if existing_markdown is None else existing_markdown
    check_title_prefix = segment_title_prefix or title_prefix
    if _markdown_has_market_brief_block(
        markdown_to_check,
        title_prefix=check_title_prefix,
    ):
        return markdown.strip()

    base = markdown.strip()
    if not base:
        return market_brief_block
    return f"{base}\n\n{market_brief_block}".strip()


def _append_missing_market_brief_payload_block_to_market_segment(
    markdown: str,
    payload: Dict[str, Any],
    *,
    title_prefix: str = "",
    segment_title_prefix: str = "",
) -> str:
    base = markdown.strip()
    check_title_prefix = segment_title_prefix or title_prefix
    market_brief_block = _render_market_brief_payload_markdown_block(
        payload,
        title_prefix=title_prefix,
    )
    if not market_brief_block:
        return base
    if _markdown_has_market_brief_block(base, title_prefix=check_title_prefix):
        return base

    segment_span = _find_market_markdown_segment_span(base, check_title_prefix)
    if segment_span is None:
        return _append_missing_market_brief_payload_block(
            base,
            payload,
            title_prefix=title_prefix,
            existing_markdown=base,
            segment_title_prefix=check_title_prefix,
        )

    start, end = segment_span
    segment = base[start:end].strip()
    rendered_segment = (
        f"{segment}\n\n{market_brief_block}".strip()
        if segment
        else market_brief_block
    )
    suffix = base[end:]
    if suffix and not suffix.startswith(("\n", "\r")):
        rendered_segment = f"{rendered_segment}\n\n"
    return f"{base[:start]}{rendered_segment}{suffix}".strip()


def _append_missing_sector_payload_block(
    markdown: str,
    payload: Dict[str, Any],
    *,
    title_prefix: str = "",
    existing_markdown: Optional[Any] = None,
    segment_title_prefix: str = "",
) -> str:
    sector_block = _render_sector_payload_markdown_block(payload, title_prefix=title_prefix)
    if not sector_block:
        return markdown.strip()
    markdown_to_check = markdown if existing_markdown is None else existing_markdown
    check_title_prefix = segment_title_prefix or title_prefix
    if _markdown_has_sector_table(markdown_to_check, title_prefix=check_title_prefix):
        return markdown.strip()

    base = markdown.strip()
    if not base:
        return sector_block
    return f"{base}\n\n{sector_block}".strip()


def _append_missing_sector_payload_block_to_market_segment(
    markdown: str,
    payload: Dict[str, Any],
    *,
    title_prefix: str = "",
    segment_title_prefix: str = "",
) -> str:
    base = markdown.strip()
    check_title_prefix = segment_title_prefix or title_prefix
    sector_block = _render_sector_payload_markdown_block(payload, title_prefix=title_prefix)
    if not sector_block:
        return base
    if _markdown_has_sector_table(base, title_prefix=check_title_prefix):
        return base

    segment_span = _find_market_markdown_segment_span(base, check_title_prefix)
    if segment_span is None:
        return _append_missing_sector_payload_block(
            base,
            payload,
            title_prefix=title_prefix,
            existing_markdown=base,
            segment_title_prefix=check_title_prefix,
        )

    start, end = segment_span
    segment = base[start:end].strip()
    rendered_segment = f"{segment}\n\n{sector_block}".strip() if segment else sector_block
    suffix = base[end:]
    if suffix and not suffix.startswith(("\n", "\r")):
        rendered_segment = f"{rendered_segment}\n\n"
    return f"{base[:start]}{rendered_segment}{suffix}".strip()


def _iter_market_payloads(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    markets = payload.get("markets")
    if isinstance(markets, dict) and markets:
        for region in _MARKET_REVIEW_REGION_ORDER:
            market_payload = markets.get(region)
            if isinstance(market_payload, dict):
                yield market_payload
        return
    yield payload


def _payload_for_market(payload: Dict[str, Any], region: str) -> Optional[Dict[str, Any]]:
    markets = payload.get("markets")
    if isinstance(markets, dict):
        candidate = markets.get(region)
        return candidate if isinstance(candidate, dict) else None
    payload_region = str(payload.get("region") or "").strip().lower()
    if payload_region == region:
        return payload
    return None


def _is_risk_pressure_index(index: Dict[str, Any]) -> bool:
    role = str(index.get("instrument_role") or "").strip().lower()
    if role == "risk_pressure":
        return True
    code = str(index.get("code") or "").strip().upper()
    name = str(index.get("name") or "").strip().upper()
    if code in {"VIX", "^VIX", "VIX1D", "^VIX1D", "VIX3M", "^VIX3M", "VVIX", "^VVIX", "VSTOXX", "VHSI", "MOVE"}:
        return True
    return any(
        marker in name
        for marker in ("VIX", "VVIX", "VSTOXX", "VHSI", "VOLATILITY", "波动率", "恐慌指数", "MOVE INDEX")
    )


def _format_ranked_names(rows: Any, *, limit: int = 3) -> str:
    if not isinstance(rows, list):
        return ""
    items = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        change_pct = row.get("change_pct")
        if not name:
            continue
        if isinstance(change_pct, (int, float)):
            items.append(f"{name} {float(change_pct):+.2f}%")
        else:
            items.append(name)
    return "、".join(items)


def _plain_market_backdrop(payload: Dict[str, Any], *, language: str) -> str:
    light = payload.get("market_light")
    if not isinstance(light, dict) or light.get("data_quality") == "unavailable":
        return (
            "Insufficient equity-index or participation data; no composite market view."
            if language == "en"
            else "可用的权益指数或市场参与数据不足，暂不生成综合判断。"
        )
    status = str(light.get("status") or "").strip().lower()
    label = {
        "green": "偏进攻",
        "yellow": "需观察",
        "red": "偏防守",
    }.get(status, str(light.get("label") or "需观察"))
    quality = "数据完整" if light.get("data_quality") == "ok" else "部分数据"
    score = light.get("score")
    guidance = str(light.get("guidance") or "").strip()
    if language == "en":
        score_text = f"reference score {score}/100" if isinstance(score, (int, float)) else "score unavailable"
        return f"{light.get('label') or 'mixed'}; {score_text}; {light.get('data_quality')}. {guidance}".strip()
    score_text = f"市场参考分 {int(score)}/100" if isinstance(score, (int, float)) else "参考分暂不提供"
    return f"{label}；{score_text}，{quality}。{guidance}".strip()


def _plain_global_risk(payload: Dict[str, Any], *, language: str) -> str:
    risk_indices = []
    for market_payload in _iter_market_payloads(payload):
        indices = market_payload.get("indices")
        if not isinstance(indices, list):
            continue
        for index in indices:
            if isinstance(index, dict) and _is_risk_pressure_index(index):
                risk_indices.append(index)
    if not risk_indices:
        return (
            "No reliable VIX or comparable risk-pressure input was available; no value is filled in."
            if language == "en"
            else "本次未取得可核的VIX或同类风险压力数据，不补写、不按中性处理。"
        )
    parts = []
    for index in risk_indices[:2]:
        name = str(index.get("name") or index.get("code") or "VIX").strip()
        current = index.get("current")
        change_pct = index.get("change_pct")
        level = f" {float(current):.2f}" if isinstance(current, (int, float)) else ""
        if isinstance(change_pct, (int, float)):
            if float(change_pct) > 0:
                direction = "风险压力升高" if language != "en" else "risk pressure increased"
            elif float(change_pct) < 0:
                direction = "风险压力缓和" if language != "en" else "risk pressure eased"
            else:
                direction = "风险压力变化不大" if language != "en" else "risk pressure was little changed"
            parts.append(f"{name}{level}（{float(change_pct):+.2f}%，{direction}）")
        else:
            parts.append(f"{name}{level}")
    joined = "；".join(parts)
    if language == "en":
        return f"{joined}. VIX-type indices describe expected volatility / risk pressure, not next-day direction."
    return f"{joined}。VIX类指标表示美股未来约30天预期波动/风险压力，不是明日涨跌预测。"


def _plain_a_share_health(payload: Dict[str, Any], *, language: str) -> str:
    cn_payload = _payload_for_market(payload, "cn")
    if cn_payload is None:
        return (
            "No A-share participation data in this report."
            if language == "en"
            else "本次报告没有A股内部参与数据，不据此判断A股是否健康。"
        )
    brief = cn_payload.get("market_brief")
    if not isinstance(brief, dict):
        return (
            "A-share breadth and limit structure are unavailable."
            if language == "en"
            else "本次没有可靠的上涨/下跌家数和涨跌停结构，不用指数涨跌替代。"
        )
    parts = []
    breadth = brief.get("breadth")
    if isinstance(breadth, dict) and breadth.get("status") == "READY":
        denominator = breadth.get("breadth_denominator")
        ratio = breadth.get("breadth_ratio")
        if isinstance(denominator, int) and denominator > 0 and isinstance(ratio, (int, float)):
            parts.append(
                f"市场宽度（有多少股票一起上涨）：上涨 {breadth.get('up_count', 0)} / "
                f"总参与 {denominator} = {float(ratio) * 100:.1f}%，"
                f"下跌 {breadth.get('down_count', 0)}、平盘 {breadth.get('flat_count', 0)}"
            )
    speculative = brief.get("speculative_heat")
    if isinstance(speculative, dict) and speculative.get("status") == "READY":
        parts.append(
            f"短线投机温度：涨停 {speculative.get('limit_up_count', 0)}、"
            f"跌停 {speculative.get('limit_down_count', 0)}"
        )
    if parts:
        return "；".join(parts) + "。宽度与涨跌停热度分开判断。"
    return "本次A股宽度或涨跌停数据不足，不补成中性结论。"


def _plain_sector_leadership(
    payload: Dict[str, Any],
    *,
    avoid: bool = False,
    language: str = "zh",
) -> str:
    cn_payload = _payload_for_market(payload, "cn") or payload
    section = cn_payload.get("sectors")
    concepts = cn_payload.get("concepts")
    key = "bottom" if avoid else "top"
    items = []
    if isinstance(section, dict):
        rendered = _format_ranked_names(section.get(key), limit=3)
        if rendered:
            items.append(rendered)
    if isinstance(concepts, dict):
        rendered = _format_ranked_names(concepts.get(key), limit=2)
        if rendered:
            items.append(rendered)
    if not items:
        if language == "en":
            return (
                "No reliable laggard ranking; no avoid list is forced."
                if avoid
                else "No reliable leader ranking; news attention is not relabelled as money flow."
            )
        return (
            "本次没有可靠的领跌板块排行，不强行列回避方向。"
            if avoid
            else "本次没有可靠的领涨板块排行，不把新闻热度写成资金流向。"
        )
    joined = "；".join(items)
    if language == "en":
        if avoid:
            return f"Short-term laggards: {joined}. Check for stabilization; a large decline is not an automatic dip-buy signal."
        return f"Price leadership: {joined}. Relative performance is not proof of institutional net inflow."
    if avoid:
        return f"短期较弱线索：{joined}。先核趋势是否止跌，不因跌幅大就自动抄底。"
    return (
        f"价格领先线索：{joined}。这里表示相对表现较强，"
        "不等于已经证明主力或机构资金净流入。"
    )


def _plain_turnover_context(payload: Dict[str, Any], *, language: str) -> str:
    cn_payload = _payload_for_market(payload, "cn") or payload
    breadth = cn_payload.get("breadth")
    if not isinstance(breadth, dict):
        return (
            "Comparable turnover data are unavailable; volume strength is not guessed."
            if language == "en"
            else "本次没有可靠的成交额或可比量能数据，不把“量能足/不足”硬猜出来。"
        )
    total_amount = breadth.get("total_amount")
    unit = str(breadth.get("turnover_unit") or "").strip()
    if not isinstance(total_amount, (int, float)) or float(total_amount) <= 0:
        return (
            "Comparable turnover data are unavailable; volume strength is not guessed."
            if language == "en"
            else "本次没有可靠的成交额或可比量能数据，不把“量能足/不足”硬猜出来。"
        )
    amount_text = f"{float(total_amount):,.2f}"
    if language == "en":
        return (
            f"Turnover {amount_text} {unit or 'reported units'}; "
            "without a comparable baseline, one absolute value is not called expansion or contraction."
        )
    return (
        f"两市成交额 {amount_text}{unit}；本次若没有可靠的历史可比基准，"
        "只报事实，不把单日绝对额直接写成“放量”或“缩量”。"
    )


def _plain_fused_market_judgment(payload: Dict[str, Any], *, language: str) -> str:
    cn_payload = _payload_for_market(payload, "cn")
    primary_payload = cn_payload or next(iter(_iter_market_payloads(payload)), payload)
    light = primary_payload.get("market_light") if isinstance(primary_payload, dict) else None
    status = str(light.get("status") or "").strip().lower() if isinstance(light, dict) else ""

    risk_changes = []
    for market_payload in _iter_market_payloads(payload):
        indices = market_payload.get("indices")
        if not isinstance(indices, list):
            continue
        for index in indices:
            if not isinstance(index, dict) or not _is_risk_pressure_index(index):
                continue
            change_pct = index.get("change_pct")
            if isinstance(change_pct, (int, float)):
                risk_changes.append(float(change_pct))

    if risk_changes and all(value > 0 for value in risk_changes):
        global_clause = "海外风险压力正在升高"
    elif risk_changes and all(value < 0 for value in risk_changes):
        global_clause = "海外风险压力正在缓和"
    elif risk_changes:
        global_clause = "海外风险信号有分歧"
    else:
        global_clause = "海外风险压力数据不足"

    breadth_clause = "A股内部参与数据不足"
    if isinstance(cn_payload, dict):
        brief = cn_payload.get("market_brief")
        breadth = brief.get("breadth") if isinstance(brief, dict) else None
        if isinstance(breadth, dict) and breadth.get("status") == "READY":
            up_count = int(breadth.get("up_count") or 0)
            down_count = int(breadth.get("down_count") or 0)
            if up_count > down_count:
                breadth_clause = f"A股上涨家数多于下跌家数（{up_count} 比 {down_count}），短线参与偏正面"
            elif down_count > up_count:
                breadth_clause = f"A股下跌家数多于上涨家数（{down_count} 比 {up_count}），内部参与偏弱"
            else:
                breadth_clause = f"A股上涨与下跌家数接近（各 {up_count}），内部参与没有明显优势"

    def has_ranked_rows(section: Any, key: str) -> bool:
        if not isinstance(section, dict):
            return False
        rows = section.get(key)
        return bool(
            isinstance(rows, list)
            and any(
                isinstance(row, dict) and str(row.get("name") or "").strip()
                for row in rows
            )
        )

    sectors = cn_payload.get("sectors") if isinstance(cn_payload, dict) else None
    concepts = cn_payload.get("concepts") if isinstance(cn_payload, dict) else None
    has_leaders = has_ranked_rows(sectors, "top") or has_ranked_rows(concepts, "top")
    has_laggards = has_ranked_rows(sectors, "bottom") or has_ranked_rows(concepts, "bottom")
    if has_leaders and has_laggards:
        leadership_clause = "板块强弱并存，市场分化明显"
    elif has_leaders:
        leadership_clause = "出现价格领先方向，但仍需观察持续性"
    elif has_laggards:
        leadership_clause = "弱势方向存在扩散迹象"
    else:
        leadership_clause = "板块领导性数据不足"

    if status == "green":
        action = (
            "大环境偏进攻，但只允许精选强势方向，不等于全面追涨。"
            "没有高周期结构确认时，不把短线强弱直接升级成“牛市/熊市”结论。"
        )
    elif status == "red":
        action = (
            "大环境偏防守，暂停新增风险敞口；已有持仓按各自失效条件逐项复核，"
            "不因为一份晨报就一刀切空仓。只有月/周/日高周期结构也同步转坏时，"
            "才把表述升级为“牛转熊风险明显上升”。"
        )
    elif status == "yellow":
        action = (
            "大环境需观察，先控制新增风险，等指数、市场宽度、量价和领涨板块重新形成共振。"
            "短线风险升高不等于已经牛转熊。"
        )
    else:
        action = "关键证据不足，暂不提高风险暴露，等更多独立证据确认。"

    if language == "en":
        return (
            "Integrated view: combine global risk pressure, A-share participation, turnover, "
            "leadership and the existing market-permission state; do not infer a bull/bear regime "
            "or portfolio liquidation from one indicator."
        )

    return (
        f"{action} 主要依据：{global_clause}；{breadth_clause}；{leadership_clause}。"
    )


def _render_morning_plain_language_overlay(payload: Dict[str, Any]) -> str:
    language = normalize_report_language(payload.get("language"))
    english = language in {"en", "ko"}
    display_language = "en" if english else "zh"
    cn_payload = _payload_for_market(payload, "cn")
    primary_payload = cn_payload or next(iter(_iter_market_payloads(payload)), payload)
    generated_at = str(primary_payload.get("generated_at") or payload.get("generated_at") or "").strip()
    data_date = str(primary_payload.get("date") or payload.get("date") or "").strip()

    if english:
        lines = [
            "### Pre-open: six questions first",
            "",
            f"- **Overall backdrop**: {_plain_market_backdrop(primary_payload, language=display_language)}",
            f"- **Has overseas risk increased?**: {_plain_global_risk(payload, language=display_language)}",
            f"- **Is A-share participation healthy?**: {_plain_a_share_health(payload, language=display_language)}",
            f"- **Where is leadership?**: {_plain_sector_leadership(payload, language=display_language)}",
            f"- **What should not be chased?**: {_plain_sector_leadership(payload, avoid=True, language=display_language)}",
            "- **What matters today / what changes the view?**: watch whether major indices, participation and leading groups improve together; downgrade if they weaken together or risk pressure keeps rising.",
        ]
    else:
        lines = [
            "### 开盘前先看这六件事",
            "",
            f"> **先给综合结论**：{_plain_fused_market_judgment(payload, language=display_language)}",
            "",
            f"- **今天的大环境**：{_plain_market_backdrop(primary_payload, language=display_language)}",
            f"- **海外风险有没有升高**：{_plain_global_risk(payload, language=display_language)}",
            f"- **A股内部健康吗**：{_plain_a_share_health(payload, language=display_language)}",
            f"- **成交和量能是否支持**：{_plain_turnover_context(payload, language=display_language)}",
            f"- **板块主线与需要回避的方向**：{_plain_sector_leadership(payload)} {_plain_sector_leadership(payload, avoid=True)}",
            "- **今天重点看什么 / 什么变化会推翻判断**：重点看主要指数、上涨家数、量能和领涨板块能否同向改善，以及海外风险压力是否继续走高；只有多项独立证据一起变化，才调整大环境判断。",
        ]
    time_parts = []
    if data_date:
        time_parts.append(f"data date {data_date}" if english else f"数据日期 {data_date}")
    if generated_at:
        time_parts.append(f"generated {generated_at}" if english else f"生成时间 {generated_at}")
    if time_parts:
        suffix = "; missing inputs are not treated as neutral evidence." if english else "；缺失项不按中性证据处理。"
        lines.extend(["", "> " + "；".join(time_parts) + suffix])
    return "\n".join(lines).strip()


def _insert_morning_plain_language_overlay(markdown: Any, payload: Dict[str, Any]) -> str:
    text = str(markdown or "").strip()
    block = _render_morning_plain_language_overlay(payload)
    if not block:
        return text
    markers = ("### 开盘前先看这六件事", "### Pre-open: six questions first")
    if any(marker in text for marker in markers):
        return text
    if not text:
        return block

    # Preserve the accepted R004 conclusion-first lead.  The additive overlay
    # sits between that existing lead and the first detailed section instead of
    # replacing or pushing the original conclusion below a new top summary.
    detail_heading = re.search(r"(?m)^###\s+.+$", text)
    if detail_heading is not None:
        return (
            text[: detail_heading.start()].rstrip()
            + "\n\n"
            + block
            + "\n\n"
            + text[detail_heading.start() :].lstrip()
        ).strip()

    top_heading = re.search(r"(?m)^#{1,2}\s+.+$", text)
    if top_heading is None:
        return f"{block}\n\n{text}".strip()
    return (
        text[: top_heading.end()].rstrip()
        + "\n\n"
        + block
        + "\n\n"
        + text[top_heading.end() :].lstrip()
    ).strip()


def _render_market_brief_payload_markdown_block(
    payload: Dict[str, Any],
    *,
    title_prefix: str = "",
) -> str:
    brief = payload.get("market_brief")
    if not isinstance(brief, dict):
        return ""

    language = normalize_report_language(payload.get("language"))
    title = "Market Breadth & Limit Structure" if language == "en" else "市场宽度与涨跌停结构"
    heading = f"{title_prefix} / {title}" if title_prefix else title
    lines = [f"### {heading}", ""]

    representative_roles = brief.get("representative_index_roles")
    if isinstance(representative_roles, dict):
        roles = representative_roles.get("roles")
        if isinstance(roles, dict):
            rendered_roles = []
            missing_roles = []
            for role in roles.values():
                if not isinstance(role, dict):
                    continue
                label = str(role.get("label") or "").strip()
                indices = role.get("indices")
                if role.get("status") == "READY" and isinstance(indices, list) and indices:
                    items = []
                    for index in indices:
                        if not isinstance(index, dict):
                            continue
                        name = str(index.get("name") or "").strip()
                        change_pct = index.get("change_pct")
                        if name and isinstance(change_pct, (int, float)):
                            items.append(f"{name} {float(change_pct):+.2f}%")
                        elif name:
                            items.append(name)
                    if label and items:
                        rendered_roles.append(f"{label}：{'、'.join(items)}")
                elif label:
                    missing_roles.append(label)
            if rendered_roles:
                role_label = "Representative index roles" if language == "en" else "代表指数职责"
                lines.append(f"- **{role_label}**：{'；'.join(rendered_roles)}")
            if missing_roles:
                if language == "en":
                    lines.append(
                        "- **Role coverage gap**: no reliable current data for "
                        + ", ".join(missing_roles)
                        + "."
                    )
                else:
                    lines.append(
                        "- **代表指数缺口**：本次暂无"
                        + "、".join(missing_roles)
                        + "的可靠当前数据，不据此补写风格结论。"
                    )

    breadth = brief.get("breadth")
    if isinstance(breadth, dict) and breadth.get("status") == "READY":
        denominator = breadth.get("breadth_denominator")
        ratio = breadth.get("breadth_ratio")
        if isinstance(denominator, int) and denominator > 0 and isinstance(ratio, (int, float)):
            ratio_pct = float(ratio) * 100
            if language == "en":
                lines.append(
                    f"- **Breadth**: advancers {breadth.get('up_count', 0)} / "
                    f"participants {denominator} = {ratio_pct:.1f}%; "
                    f"decliners {breadth.get('down_count', 0)}, flat {breadth.get('flat_count', 0)}."
                )
            else:
                lines.append(
                    f"- **市场宽度**：上涨 {breadth.get('up_count', 0)} / "
                    f"总参与 {denominator} = {ratio_pct:.1f}%；"
                    f"下跌 {breadth.get('down_count', 0)}，平盘 {breadth.get('flat_count', 0)}。"
                )

    speculative_heat = brief.get("speculative_heat")
    if isinstance(speculative_heat, dict) and speculative_heat.get("status") == "READY":
        limit_total = speculative_heat.get("limit_total")
        limit_up_ratio = speculative_heat.get("limit_up_ratio")
        if isinstance(limit_total, int) and limit_total > 0 and isinstance(limit_up_ratio, (int, float)):
            ratio_pct = float(limit_up_ratio) * 100
            if language == "en":
                lines.append(
                    f"- **Speculative-heat proxy (limit structure)**: limit-up "
                    f"{speculative_heat.get('limit_up_count', 0)} / total {limit_total} "
                    f"= {ratio_pct:.1f}%; limit-down {speculative_heat.get('limit_down_count', 0)}."
                )
            else:
                lines.append(
                    f"- **投机热度代理（涨跌停结构）**：涨停 "
                    f"{speculative_heat.get('limit_up_count', 0)} / 涨跌停总数 {limit_total} "
                    f"= {ratio_pct:.1f}%；跌停 {speculative_heat.get('limit_down_count', 0)}。"
                )

    if len(lines) == 2:
        return ""
    return "\n".join(lines).strip()


def _markdown_has_market_brief_block(markdown: Any, *, title_prefix: str = "") -> bool:
    text = str(markdown or "")
    language_markers = (
        "市场宽度与涨跌停结构",
        "Market Breadth & Limit Structure",
    )
    if title_prefix:
        title = title_prefix.strip()
        prefixed_markers = tuple(f"### {title} / {marker}" for marker in language_markers)
        if any(marker in text for marker in prefixed_markers):
            return True
        segment = _extract_market_markdown_segment(text, title)
        if segment is None:
            return False
        text = segment
    return any(marker in text for marker in language_markers)


def _render_sector_payload_markdown_block(
    payload: Dict[str, Any],
    *,
    title_prefix: str = "",
) -> str:
    sector_block = _render_sector_payload_block(payload)
    if not sector_block:
        return ""
    language = normalize_report_language(payload.get("language"))
    title = "Sector Highlights" if language == "en" else "板块主线"
    heading = f"{title_prefix} / {title}" if title_prefix else title
    return f"### {heading}\n\n{sector_block}".strip()


def _markdown_has_sector_table(markdown: Any, *, title_prefix: str = "") -> bool:
    text = str(markdown or "")
    if title_prefix:
        title = title_prefix.strip()
        prefixed_markers = (
            f"### {title} / 板块主线",
            f"### {title} / Sector Highlights",
        )
        if any(marker in text for marker in prefixed_markers):
            return True
        segment = _extract_market_markdown_segment(text, title)
        if segment is None:
            return False
        text = segment

    return _markdown_contains_sector_markers(text)


def _extract_market_markdown_segment(markdown: str, title: str) -> Optional[str]:
    segment_span = _find_market_markdown_segment_span(markdown, title)
    if segment_span is None:
        return None
    start, end = segment_span
    return markdown[start:end]


def _find_market_markdown_segment_span(markdown: str, title: str) -> Optional[tuple[int, int]]:
    if not title:
        return None
    heading_pattern = re.compile(rf"(?m)^(#{{1,2}})\s+{re.escape(title)}\s*$")
    match = heading_pattern.search(markdown)
    if not match:
        return None
    heading_level = len(match.group(1))
    next_heading = re.search(
        rf"(?m)^(?:#{{1,{heading_level}}}\s+|---\s*$)",
        markdown[match.end():],
    )
    end = match.end() + next_heading.start() if next_heading else len(markdown)
    return match.start(), end


def _markdown_contains_sector_markers(text: str) -> bool:
    markers = (
        "#### 领涨板块",
        "#### 领跌板块",
        "#### 行业板块领涨",
        "#### 行业板块领跌",
        "#### Leading Sectors",
        "#### Lagging Sectors",
        "#### Leading Industry Sectors",
        "#### Lagging Industry Sectors",
        "| 排名 | 板块 |",
        "| 排名 | 行业板块 |",
        "| Rank | Sector |",
    )
    return any(marker in text for marker in markers)


def _render_sector_payload_block(payload: Dict[str, Any]) -> str:
    sectors = payload.get("sectors")
    if not isinstance(sectors, dict):
        return ""
    top = sectors.get("top") if isinstance(sectors.get("top"), list) else []
    bottom = sectors.get("bottom") if isinstance(sectors.get("bottom"), list) else []
    if not top and not bottom:
        return ""

    language = normalize_report_language(payload.get("language"))
    lines = []
    if top:
        if language == "en":
            lines.extend(["#### Leading Sectors", "| Rank | Sector | Change |", "|------|--------|--------|"])
        else:
            lines.extend(["#### 领涨板块 Top 5", "| 排名 | 板块 | 涨跌幅 |", "|------|------|--------|"])
        for rank, sector in enumerate(top[:5], 1):
            if not isinstance(sector, dict):
                continue
            name = str(sector.get("name") or "-").strip() or "-"
            lines.append(f"| {rank} | {name} | {_format_sector_change_pct(sector)} |")
    if bottom:
        if lines:
            lines.append("")
        if language == "en":
            lines.extend(["#### Lagging Sectors", "| Rank | Sector | Change |", "|------|--------|--------|"])
        else:
            lines.extend(["#### 领跌板块 Top 5", "| 排名 | 板块 | 涨跌幅 |", "|------|------|--------|"])
        for rank, sector in enumerate(bottom[:5], 1):
            if not isinstance(sector, dict):
                continue
            name = str(sector.get("name") or "-").strip() or "-"
            lines.append(f"| {rank} | {name} | {_format_sector_change_pct(sector)} |")
    return "\n".join(lines).strip()


def _format_sector_change_pct(sector: Dict[str, Any]) -> str:
    raw = sector.get("change_pct", sector.get("changePct"))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "--"
    return f"{value:+.2f}%"


def _normalize_market_review_heading(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.lstrip("#").strip().lower().split())


def _persist_market_review_history(
    *,
    review_report: str,
    markdown_report: str,
    region: str,
    config: object,
    query_id: Optional[str] = None,
    market_light_snapshots: Optional[Dict[str, Dict[str, Any]]] = None,
    market_review_payload: Optional[Dict[str, Any]] = None,
) -> int:
    """Persist market review output into the existing analysis history table."""
    try:
        from src.storage import DatabaseManager

        report_language = normalize_report_language(getattr(config, "report_language", "zh"))
        summary = _summarize_market_review(review_report, report_language)
        if report_language == "en":
            stock_name = "Market Review"
            operation_advice = "View review"
            trend_prediction = "Market review"
        elif report_language == "ko":
            stock_name = "시황 리뷰"
            operation_advice = "리뷰 보기"
            trend_prediction = "시황 리뷰"
        else:
            stock_name = "大盘复盘"
            operation_advice = "查看复盘"
            trend_prediction = "大盘复盘"

        result = AnalysisResult(
            code=MARKET_REVIEW_HISTORY_CODE,
            name=stock_name,
            sentiment_score=50,
            trend_prediction=trend_prediction,
            operation_advice=operation_advice,
            analysis_summary=summary,
            report_language=report_language,
            news_summary=review_report,
            raw_response=markdown_report,
            data_sources="market_review",
        )

        history_query_id = query_id or f"market_review_{uuid.uuid4().hex}"
        context_snapshot = {
            "report_kind": MARKET_REVIEW_REPORT_TYPE,
            "market_review_region": region,
            "report_language": report_language,
        }
        if market_light_snapshots:
            context_snapshot["market_light_snapshots"] = market_light_snapshots
        if market_review_payload:
            context_snapshot["market_review_payload"] = market_review_payload
        diagnostic_snapshot = current_diagnostic_snapshot()
        if diagnostic_snapshot is not None:
            context_snapshot["diagnostics"] = diagnostic_snapshot
        context_snapshot["analysis_context_pack_overview"] = _build_market_review_context_overview(
            region=region,
            report_language=report_language,
            diagnostic_snapshot=diagnostic_snapshot,
        )

        db = DatabaseManager.get_instance()
        saved_history_id = db.save_analysis_history(
            result=result,
            query_id=history_query_id,
            report_type=MARKET_REVIEW_REPORT_TYPE,
            news_content=review_report,
            context_snapshot=context_snapshot,
            save_snapshot=True,
        )
        valid_saved_history_id = (
            saved_history_id
            if (
                isinstance(saved_history_id, int)
                and not isinstance(saved_history_id, bool)
                and saved_history_id > 0
            )
            else None
        )
        record_history_run(
            report_saved=bool(saved_history_id),
            metadata_saved=bool(saved_history_id),
            analysis_history_id=valid_saved_history_id,
        )
        _refresh_market_review_history_diagnostics(query_id=history_query_id)
        if saved_history_id:
            logger.info("大盘复盘历史记录已保存: query_id=%s", history_query_id)
        else:
            logger.warning("大盘复盘历史记录保存失败: query_id=%s", history_query_id)
        return saved_history_id
    except Exception as exc:
        record_history_run(
            report_saved=False,
            metadata_saved=False,
            error_message=exc,
        )
        logger.warning("大盘复盘历史记录保存异常，报告文件与推送流程继续: %s", exc, exc_info=True)
        return 0


def _build_market_review_context_overview(
    *,
    region: str,
    report_language: str,
    diagnostic_snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a low-sensitivity overview block for market-review run-flow rendering."""
    warnings: list[str] = []
    counts = {
        "available": 1,
        "missing": 0,
        "not_supported": 0,
        "fallback": 0,
        "stale": 0,
        "estimated": 0,
        "partial": 0,
        "fetch_failed": 0,
    }
    metadata: Dict[str, Any] = {
        "trigger_source": "market_review",
        "scope": "market_review",
        "report_type": MARKET_REVIEW_REPORT_TYPE,
    }
    if isinstance(diagnostic_snapshot, dict):
        metadata["trigger_source"] = diagnostic_snapshot.get("trigger_source") or metadata["trigger_source"]
        metadata["scope"] = diagnostic_snapshot.get("scope") or metadata["scope"]

    label = (
        "Market review" if report_language == "en"
        else "시황 리뷰" if report_language == "ko"
        else "大盘复盘"
    )
    return {
        "pack_version": "market_review/1.0",
        "created_at": datetime.now().isoformat(),
        "subject": {
            "code": MARKET_REVIEW_HISTORY_CODE,
            "stock_name": label,
            "market": region,
        },
        "blocks": [
            {
                "key": MARKET_REVIEW_REPORT_TYPE,
                "label": label,
                "status": "available",
                "source": MARKET_REVIEW_REPORT_TYPE,
                "warnings": warnings,
                "missing_reasons": [],
            }
        ],
        "counts": counts,
        "warnings": warnings,
        "metadata": metadata,
        "data_quality": {
            "level": "good",
            "overall_score": 100,
            "available": 1,
            "total": 1,
            "missing": 0,
        },
    }


def _summarize_market_review(review_report: str, report_language: str) -> str:
    for line in (review_report or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text and not text.startswith("---") and not text.startswith(">"):
            return text[:200]
    if report_language == "en":
        return "Market review report generated."
    if report_language == "ko":
        return "시황 리뷰 리포트가 생성되었습니다."
    return "大盘复盘报告已生成。"
