from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from feedback_service.core import (
    FeedbackConfig,
    FeedbackError,
    FeedbackStore,
    validate_bundle,
    validate_payload,
)
from feedback_service.multipart import parse_multipart
from settings_center.feedback import _multipart_body, build_feedback_payload


INSTALLATION_ID = "12345678-1234-1234-1234-123456789abc"


def payload(message: str = "F9 does not work") -> dict:
    return {
        "schema_version": 1,
        "product": "VoCoType-linux",
        "version": "2.2.3",
        "category": "bug",
        "message": message,
        "platform": "Linux-test",
        "installation_id": INSTALLATION_ID,
        "doctor": [{"status": "fail", "check_id": "service"}],
        "contact": "",
    }


def make_store(tmp_path: Path, **kwargs) -> FeedbackStore:
    return FeedbackStore(
        FeedbackConfig(
            data_dir=tmp_path,
            hmac_secret="test-secret-" * 8,
            **kwargs,
        )
    )


def test_payload_validation_is_strict():
    assert validate_payload(payload())["category"] == "bug"
    with pytest.raises(FeedbackError, match="未知反馈类别"):
        validate_payload({**payload(), "category": "anything"})
    with pytest.raises(FeedbackError, match="不能为空"):
        validate_payload({**payload(), "message": " "})
    with pytest.raises(FeedbackError, match="installation_id"):
        validate_payload({**payload(), "installation_id": "hardware-serial"})


def test_duplicate_reports_are_collapsed(tmp_path: Path):
    store = make_store(tmp_path, install_hour_limit=10)
    now = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    first = store.accept(payload(), source_ip="192.0.2.1", now=now)
    second = store.accept(payload("  F9   does not work "), source_ip="192.0.2.1", now=now + timedelta(minutes=1))
    assert first.duplicate is False
    assert second.duplicate is True
    assert second.feedback_id == first.feedback_id
    assert second.occurrence_count == 2
    assert store.list_feedback()[0]["occurrence_count"] == 2


def test_installation_rate_limit_is_enforced(tmp_path: Path):
    store = make_store(tmp_path, install_hour_limit=2, install_day_limit=20)
    now = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    store.accept(payload("one"), source_ip="192.0.2.1", now=now)
    store.accept(payload("two"), source_ip="192.0.2.1", now=now + timedelta(minutes=1))
    with pytest.raises(FeedbackError) as caught:
        store.accept(payload("three"), source_ip="192.0.2.1", now=now + timedelta(minutes=2))
    assert caught.value.status_code == 429
    assert caught.value.code == "rate_limited"


def test_support_bundle_is_private_and_server_named(tmp_path: Path):
    store = make_store(tmp_path)
    data = gzip.compress(b"diagnostics")
    accepted = store.accept(
        payload(),
        source_ip="192.0.2.1",
        bundle_name="../../client-name.tar.gz",
        bundle_data=data,
    )
    item = store.get_feedback(accepted.feedback_id)
    assert item is not None
    stored = Path(item["attachment_path"])
    assert stored.parent == tmp_path / "attachments"
    assert stored.name.startswith(accepted.feedback_id)
    assert stored.read_bytes() == data
    assert stored.stat().st_mode & 0o777 == 0o600


def test_bundle_magic_must_match_extension():
    with pytest.raises(FeedbackError, match="不匹配"):
        validate_bundle("support.tar.gz", b"not gzip")


def test_desktop_multipart_round_trip(tmp_path: Path):
    bundle = tmp_path / "support.tar.gz"
    bundle.write_bytes(gzip.compress(b"support"))
    client_payload = build_feedback_payload(
        "测试反馈",
        category="bug",
        installation_id=INSTALLATION_ID,
    )
    body, boundary = _multipart_body(client_payload, bundle)
    parsed, name, data = parse_multipart(
        f"multipart/form-data; boundary={boundary}", body
    )
    assert parsed == client_payload
    assert name == "support.tar.gz"
    assert data == bundle.read_bytes()


def test_maintenance_expires_attachments_and_backs_up_database(tmp_path: Path):
    store = make_store(tmp_path / "data")
    old = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    accepted = store.accept(
        payload("old attachment"),
        source_ip="192.0.2.1",
        bundle_name="support.tar.gz",
        bundle_data=gzip.compress(b"old"),
        now=old,
    )
    stored = Path(store.get_feedback(accepted.feedback_id)["attachment_path"])
    result = store.maintenance(
        attachment_days=30,
        backup_dir=tmp_path / "backups",
        backup_days=14,
        now=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
    )
    assert result["removed_attachments"] == 1
    assert not stored.exists()
    assert Path(result["backup_path"]).is_file()
    assert store.get_feedback(accepted.feedback_id)["attachment_path"] is None
