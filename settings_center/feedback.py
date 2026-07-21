"""Privacy-conscious feedback delivery with an official endpoint and GitHub fallback."""

from __future__ import annotations

import json
import os
import platform
import secrets
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Mapping

from vocotype_version import __version__

from .config_service import vocotype_config_dir

GITHUB_NEW_ISSUE = "https://github.com/LeonardNJU/VocoType-linux/issues/new"
OFFICIAL_FEEDBACK_ENDPOINT = (
    "https://feedback.vocotype-linux.lsamc.website/v1/feedback"
)
MAX_BUNDLE_BYTES = 5 * 1024 * 1024
MAX_MESSAGE_CHARS = 10_000
INSTALLATION_ID_FILENAME = "installation-id"


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


def get_installation_id(path: str | os.PathLike[str] | None = None) -> str:
    """Return a random, resettable installation identifier without hardware data."""

    target = Path(path).expanduser() if path else vocotype_config_dir() / INSTALLATION_ID_FILENAME
    try:
        value = target.read_text(encoding="utf-8").strip()
        return str(uuid.UUID(value))
    except (OSError, ValueError):
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    value = str(uuid.uuid4())
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)
    return value


def build_feedback_payload(
    message: str,
    *,
    category: str = "other",
    contact: str = "",
    doctor_payload: Any = None,
    installation_id: str | None = None,
) -> dict[str, Any]:
    message = str(message or "").strip()
    if not message:
        raise ValueError("反馈内容不能为空")
    if len(message) > MAX_MESSAGE_CHARS:
        raise ValueError(f"反馈内容不能超过 {MAX_MESSAGE_CHARS} 个字符")
    return {
        "schema_version": 1,
        "product": "VoCoType-linux",
        "version": __version__,
        "category": str(category or "other").strip() or "other",
        "message": message,
        "platform": platform.platform(),
        "installation_id": installation_id or get_installation_id(),
        "doctor": doctor_payload,
        "contact": str(contact or "").strip(),
    }


def _validate_endpoint(endpoint: str) -> str:
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        raise ValueError("未配置反馈端点")
    parsed = urllib.parse.urlparse(endpoint)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in local_hosts
    ):
        raise ValueError("反馈端点必须使用 HTTPS（本机 localhost 调试除外）")
    if not parsed.netloc:
        raise ValueError("反馈端点格式无效")
    return endpoint


def _multipart_body(
    payload: Mapping[str, Any],
    bundle_path: str | os.PathLike[str] | None,
) -> tuple[bytes, str]:
    boundary = f"----VoCoTypeFeedback{secrets.token_hex(16)}"
    chunks: list[bytes] = []

    def field(name: str, data: bytes, *, content_type: str, filename: str | None = None) -> None:
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            safe_name = Path(filename).name.replace('"', "_")
            disposition += f'; filename="{safe_name}"'
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f"Content-Disposition: {disposition}\r\n".encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                data,
                b"\r\n",
            ]
        )

    field(
        "payload",
        json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        content_type="application/json; charset=utf-8",
    )
    if bundle_path:
        path = Path(bundle_path).expanduser()
        if not path.is_file():
            raise ValueError(f"支持包不存在：{path}")
        size = path.stat().st_size
        if size > MAX_BUNDLE_BYTES:
            raise ValueError("支持包超过 5 MiB，未发送")
        field(
            "bundle",
            path.read_bytes(),
            content_type="application/gzip" if path.name.casefold().endswith((".tar.gz", ".tgz")) else "application/zip",
            filename=path.name,
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), boundary


def submit_feedback_payload(
    endpoint: str,
    payload: Mapping[str, Any],
    *,
    bundle_path: str | os.PathLike[str] | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    endpoint = _validate_endpoint(endpoint)
    body, boundary = _multipart_body(payload, bundle_path)
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": f"VoCoType/{__version__}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            if not response_body.strip():
                return {"ok": True, "status": getattr(response, "status", 202)}
            parsed = json.loads(response_body)
            return parsed if isinstance(parsed, dict) else {"ok": True, "response": parsed}
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        message = response_body.strip() or f"HTTP {exc.code}"
        try:
            parsed = json.loads(response_body)
            if isinstance(parsed, dict) and parsed.get("message"):
                message = str(parsed["message"])
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"反馈服务器拒绝请求（{exc.code}）：{message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接反馈服务器：{exc.reason}") from exc


def submit_feedback(
    endpoint: str,
    message: str,
    *,
    category: str = "other",
    contact: str = "",
    doctor_payload: Any = None,
    bundle_path: str | os.PathLike[str] | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    payload = build_feedback_payload(
        message,
        category=category,
        contact=contact,
        doctor_payload=doctor_payload,
    )
    return submit_feedback_payload(
        endpoint,
        payload,
        bundle_path=bundle_path,
        timeout_s=timeout_s,
    )
