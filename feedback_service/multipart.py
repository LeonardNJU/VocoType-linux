"""Small bounded multipart parser used to avoid framework-specific upload state."""

from __future__ import annotations

from email.parser import BytesParser
from email.policy import default
from typing import Any

from .core import FeedbackError


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, Any], str | None, bytes | None]:
    if "multipart/form-data" not in content_type.casefold():
        raise FeedbackError("Content-Type 必须是 multipart/form-data")
    envelope = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii", errors="strict")
        + body
    )
    message = BytesParser(policy=default).parsebytes(envelope)
    if not message.is_multipart():
        raise FeedbackError("multipart 请求格式无效")
    payload_data: bytes | None = None
    bundle_name: str | None = None
    bundle_data: bytes | None = None
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True) or b""
        if name == "payload":
            payload_data = data
        elif name == "bundle":
            bundle_name = part.get_filename() or "support.tar.gz"
            bundle_data = data
    if payload_data is None:
        raise FeedbackError("缺少 payload 字段")
    import json

    try:
        payload = json.loads(payload_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeedbackError("payload 不是有效的 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise FeedbackError("payload 必须是 JSON 对象")
    return payload, bundle_name, bundle_data
