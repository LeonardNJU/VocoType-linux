"""Feedback delivery with an explicit endpoint and a GitHub fallback."""

from __future__ import annotations

import base64
import json
import os
import platform
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from vocotype_version import __version__

GITHUB_NEW_ISSUE = "https://github.com/LeonardNJU/VocoType-linux/issues/new"
MAX_INLINE_BUNDLE_BYTES = 5 * 1024 * 1024


def build_issue_url(message: str, *, doctor_text: str = "") -> str:
    body = message.strip()
    if doctor_text.strip():
        body += "\n\n<details><summary>VoCoType Doctor</summary>\n\n```text\n"
        body += doctor_text.strip()[:12000]
        body += "\n```\n</details>"
    query = urllib.parse.urlencode(
        {
            "title": "[Feedback] ",
            "body": body,
            "labels": "feedback",
        }
    )
    return f"{GITHUB_NEW_ISSUE}?{query}"


def open_github_issue(message: str, *, doctor_text: str = "") -> bool:
    return webbrowser.open(build_issue_url(message, doctor_text=doctor_text), new=2)


def submit_feedback(
    endpoint: str,
    message: str,
    *,
    doctor_payload: Any = None,
    bundle_path: str | os.PathLike[str] | None = None,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        raise ValueError("未配置反馈端点")
    parsed_endpoint = urllib.parse.urlparse(endpoint)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed_endpoint.scheme != "https" and not (
        parsed_endpoint.scheme == "http" and parsed_endpoint.hostname in local_hosts
    ):
        raise ValueError("反馈端点必须使用 HTTPS（本机 localhost 调试除外）")
    payload: dict[str, Any] = {
        "product": "VoCoType-linux",
        "version": __version__,
        "message": message.strip(),
        "platform": platform.platform(),
        "doctor": doctor_payload,
    }
    if bundle_path:
        path = Path(bundle_path).expanduser()
        if path.is_file() and path.stat().st_size <= MAX_INLINE_BUNDLE_BYTES:
            payload["bundle_name"] = path.name
            payload["bundle_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
        elif path.is_file():
            payload["bundle_omitted"] = "bundle exceeds 5 MiB inline limit"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": f"VoCoType/{__version__}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8", errors="replace")
        if not body.strip():
            return {"ok": True, "status": getattr(response, "status", 200)}
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {"ok": True, "response": parsed}
