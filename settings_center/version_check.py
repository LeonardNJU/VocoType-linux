"""GitHub release/version checks used by the settings center."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from vocotype_version import __version__

LATEST_RELEASE_API = (
    "https://api.github.com/repos/LeonardNJU/VocoType-linux/releases/latest"
)
RAW_MANIFEST_TEMPLATE = (
    "https://raw.githubusercontent.com/LeonardNJU/VocoType-linux/"
    "{ref}/data/install-integrity.json"
)


@dataclass(frozen=True)
class ReleaseVersion:
    current: str
    latest: str
    tag: str
    release_url: str
    comparison: str

    @property
    def update_available(self) -> bool:
        return self.comparison == "older"


def _version_parts(value: str) -> tuple[int, ...]:
    text = str(value).strip().lstrip("vV")
    match = re.match(r"^(\d+(?:\.\d+)*)", text)
    if not match:
        raise ValueError(f"无法解析版本号：{value}")
    return tuple(int(part) for part in match.group(1).split("."))


def compare_versions(current: str, latest: str) -> str:
    left = list(_version_parts(current))
    right = list(_version_parts(latest))
    width = max(len(left), len(right))
    left.extend([0] * (width - len(left)))
    right.extend([0] * (width - len(right)))
    if left < right:
        return "older"
    if left > right:
        return "newer"
    return "equal"


def _open_json(
    request: urllib.request.Request,
    *,
    timeout_s: float,
    opener: Callable[..., Any] | None = None,
) -> Any:
    open_fn = opener or urllib.request.urlopen
    with open_fn(request, timeout=timeout_s) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8"))


def query_latest_release(
    *,
    current_version: str = __version__,
    timeout_s: float = 8.0,
    opener: Callable[..., Any] | None = None,
) -> ReleaseVersion:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"VoCoType/{current_version}",
        },
    )
    try:
        payload = _open_json(request, timeout_s=timeout_s, opener=opener)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub 返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 GitHub：{exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub release 响应格式无效")
    tag = str(payload.get("tag_name", "")).strip()
    latest = tag.lstrip("vV")
    if not tag or not latest:
        raise RuntimeError("GitHub release 未提供版本号")
    release_url = str(payload.get("html_url", "")).strip()
    return ReleaseVersion(
        current=str(current_version),
        latest=latest,
        tag=tag,
        release_url=release_url,
        comparison=compare_versions(current_version, latest),
    )


def fetch_release_integrity_manifest(
    ref: str,
    *,
    timeout_s: float = 8.0,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any] | None:
    """Fetch the integrity manifest shipped by a release, when available."""

    candidates = [str(ref).strip()]
    stripped = str(ref).strip().lstrip("vV")
    candidates.extend([f"v{stripped}", stripped])
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        request = urllib.request.Request(
            RAW_MANIFEST_TEMPLATE.format(ref=candidate),
            headers={"User-Agent": f"VoCoType/{__version__}"},
        )
        try:
            payload = _open_json(request, timeout_s=timeout_s, opener=opener)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise RuntimeError(f"GitHub manifest 返回 HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法下载版本清单：{exc.reason}") from exc
        if isinstance(payload, dict):
            return payload
    return None
