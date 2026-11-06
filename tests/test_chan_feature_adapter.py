# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
import hashlib
import json

import pandas as pd

from src.services.chan_feature_adapter import (
    build_chan_feature_context,
    compile_chan_prefix_replay_receipt,
    validate_chan_feature_context,
)
from src.services.price_structure_service import build_price_structure_context


def _history(periods: int = 72) -> pd.DataFrame:
    dates = pd.bdate_range("2026-07-01", periods=periods)
    rows = []
    # A smooth ten-bar wave has one strict peak and trough per cycle; the small
    # drift avoids identical cycle prices while retaining alternating pivots.
    for index, day in enumerate(dates):
        close = 100.0 + 8.0 * math.sin(2.0 * math.pi * index / 10.0) + index * 0.03
        rows.append({
            "date": day,
            "open": close - 0.2,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000.0 + index * 10.0,
            "data_source": "FixtureFetcher",
        })
    return pd.DataFrame(rows)


def _feature(frame: pd.DataFrame) -> dict:
    target = pd.Timestamp(frame.iloc[-1]["date"]).date()
    structure = build_price_structure_context(
        stock_code="600519", history=frame, target_date=target, market="cn",
    )
    return build_chan_feature_context(structure)


def test_narrow_chan_adapter_reuses_confirmed_pivots_without_claiming_full_czsc():
    feature = _feature(_history())
    assert feature["status"] in {"READY", "PARTIAL"}
    assert feature["semantics"] == "NARROW_CONFIRMED_PIVOT_SWING_OVERLAP_NOT_FULL_CZSC"
    assert feature["full_chan_standard_claim"] is False
    assert feature["source_structure_status"] == "READY"
    assert feature["fractals"]
    assert feature["strokes"]
    assert all(item["provisional"] is False for item in feature["fractals"])
    assert all(item["confirmed_at"] for item in feature["strokes"])
    assert feature["independent_action_authority"] is False
    assert feature["strategy_admitted"] is False
    assert feature["learning_admitted"] is False
    assert len(feature["receipt_hash"]) == 64
    assert validate_chan_feature_context(feature) == feature


def test_chan_prefix_replay_keeps_common_confirmed_items_immutable_and_detects_tamper():
    history = _history()
    features = [_feature(history.iloc[:end].copy()) for end in range(30, len(history) + 1)]
    receipt = compile_chan_prefix_replay_receipt(features)
    assert receipt["status"] == "READY"
    assert receipt["common_item_mutation_count"] == 0
    assert receipt["future_confirmation_count"] == 0
    assert receipt["strategy_admitted"] is False
    assert len(receipt["receipt_hash"]) == 64

    tampered = deepcopy(features)
    target = next(
        item for item in tampered[-2]["strokes"]
        if any(candidate["stroke_id"] == item["stroke_id"] for candidate in tampered[-1]["strokes"])
    )
    target["end_price"] += 1.0
    tampered[-2]["receipt_hash"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in tampered[-2].items() if key != "receipt_hash"},
            ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    rejected = compile_chan_prefix_replay_receipt(tampered)
    assert rejected["status"] == "UNKNOWN"
    assert rejected["reason"] == "CHAN_PREFIX_COMMON_CONFIRMED_ITEM_MUTATED"
    assert rejected["common_item_mutation_count"] >= 1


def test_chan_adapter_missing_structure_and_provisional_input_fail_closed():
    missing = build_chan_feature_context({"status": "MISSING", "reason": "NO_DATA"})
    assert missing["status"] == "MISSING"
    assert missing["fractals"] == []
    assert missing["strokes"] == []

    structure = build_price_structure_context(
        stock_code="600519", history=_history(),
        target_date=pd.Timestamp(_history().iloc[-1]["date"]).date(), market="cn",
    )
    structure["pivots"][0]["provisional"] = True
    rejected = build_chan_feature_context(structure)
    assert rejected["status"] == "UNKNOWN"
    assert rejected["reason"] == "CHAN_SOURCE_CONTAINS_PROVISIONAL_ITEM"
