# -*- coding: utf-8 -*-
"""Exact-byte transport for the accepted V2.5 Email baseline.

This module deliberately does not render, project, simplify, or repair the
dynamic V2.5 product.  It only verifies the accepted original bytes and passes
that HTML unchanged to the existing EmailSender transport.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional, Sequence


BASELINE_BYTES = 125919
BASELINE_SHA256 = "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823"
BASELINE_PATH = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "v2_5"
    / "STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html"
)
BASELINE_SUBJECT = "【V2.5基线运输验收｜模拟事实｜非当日研究】STOCK_DSA"
BASELINE_PLAIN_TEXT = (
    "V2.5 原件运输验收：HTML 部分保留已接受的模拟示例原件，"
    "仅用于验证运输，不代表当日证券研究。"
)


class V25BaselineTransportError(RuntimeError):
    """Raised when the accepted baseline bytes are missing or changed."""


def load_v2_5_baseline_bytes(path: Path | str = BASELINE_PATH) -> bytes:
    """Read and verify the frozen original without normalizing any content."""

    resolved = Path(path)
    try:
        payload = resolved.read_bytes()
    except OSError as exc:
        raise V25BaselineTransportError(f"V2.5 baseline unreadable: {resolved}") from exc

    digest = hashlib.sha256(payload).hexdigest()
    if len(payload) != BASELINE_BYTES or digest != BASELINE_SHA256:
        raise V25BaselineTransportError(
            "V2.5 baseline identity mismatch: "
            f"bytes={len(payload)} sha256={digest}"
        )
    return payload


def load_v2_5_baseline_html(path: Path | str = BASELINE_PATH) -> str:
    """Decode the verified UTF-8 original and prove the round-trip is lossless."""

    payload = load_v2_5_baseline_bytes(path)
    try:
        html = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise V25BaselineTransportError("V2.5 baseline is not valid UTF-8") from exc
    if html.encode("utf-8") != payload:
        raise V25BaselineTransportError("V2.5 baseline UTF-8 round-trip changed bytes")
    return html


def send_v2_5_baseline_email(
    sender: Any,
    *,
    receivers: Optional[Sequence[str]] = None,
    subject: str = BASELINE_SUBJECT,
    path: Path | str = BASELINE_PATH,
) -> bool:
    """Send only the verified original through the existing EmailSender."""

    html = load_v2_5_baseline_html(path)
    resolved_receivers = list(receivers) if receivers is not None else None
    return sender.send_to_email(
        BASELINE_PLAIN_TEXT,
        subject=subject,
        receivers=resolved_receivers,
        html_content=html,
    )


def main() -> int:
    """Manual transport entry; external SMTP effect remains separately gated."""

    from src.config import get_config
    from src.notification_sender.email_sender import EmailSender

    sender = EmailSender(get_config())
    return 0 if send_v2_5_baseline_email(sender) else 2


if __name__ == "__main__":
    raise SystemExit(main())
