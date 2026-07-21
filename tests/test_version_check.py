from __future__ import annotations

import json
from types import SimpleNamespace

from settings_center.version_check import compare_versions, query_latest_release


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


def test_version_comparison_handles_different_component_counts():
    assert compare_versions("2.2.3", "2.2.3") == "equal"
    assert compare_versions("2.2.3", "2.3.0") == "older"
    assert compare_versions("2.3", "2.2.9") == "newer"
    assert compare_versions("v2.2.3", "2.2.3.0") == "equal"


def test_latest_release_query_reports_update():
    def opener(request, *, timeout):
        assert "releases/latest" in request.full_url
        assert timeout == 8.0
        return FakeResponse(
            {
                "tag_name": "v2.3.0",
                "html_url": "https://example.invalid/release",
            }
        )

    result = query_latest_release(
        current_version="2.2.3",
        opener=opener,
    )
    assert result.latest == "2.3.0"
    assert result.comparison == "older"
    assert result.update_available is True
