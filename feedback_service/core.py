"""Validation, rate limiting, deduplication, and storage for feedback reports."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

MAX_MESSAGE_CHARS = 10_000
MAX_CONTACT_CHARS = 320
MAX_PLATFORM_CHARS = 512
MAX_DOCTOR_BYTES = 128 * 1024
MAX_BUNDLE_BYTES = 5 * 1024 * 1024
CATEGORIES = {"bug", "feature", "installation", "compatibility", "usability", "other"}
_ALLOWED_SUFFIXES = (".tar.gz", ".tgz", ".zip")
_WS_RE = re.compile(r"\s+")


class FeedbackError(ValueError):
    """A client-visible feedback rejection."""

    def __init__(self, message: str, *, status_code: int = 400, code: str = "invalid_request"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class FeedbackConfig:
    data_dir: Path
    hmac_secret: str
    install_hour_limit: int = 3
    install_day_limit: int = 10
    network_hour_limit: int = 20
    global_minute_limit: int = 100
    duplicate_window_hours: int = 24

    @classmethod
    def from_env(cls) -> "FeedbackConfig":
        data_dir = Path(os.environ.get("VOCOTYPE_FEEDBACK_DATA_DIR", "/var/lib/vocotype-feedback"))
        secret = os.environ.get("VOCOTYPE_FEEDBACK_HMAC_SECRET", "").strip()
        if len(secret) < 32:
            raise RuntimeError("VOCOTYPE_FEEDBACK_HMAC_SECRET must contain at least 32 characters")
        return cls(data_dir=data_dir, hmac_secret=secret)


@dataclass(frozen=True)
class AcceptedFeedback:
    feedback_id: str
    duplicate: bool
    occurrence_count: int

    def as_response(self) -> dict[str, Any]:
        return {
            "ok": True,
            "feedback_id": self.feedback_id,
            "duplicate": self.duplicate,
            "occurrence_count": self.occurrence_count,
            "message": "反馈已收到",
        }


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _text(value: Any, field: str, *, maximum: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise FeedbackError(f"{field} 必须是字符串")
    value = value.strip()
    if required and not value:
        raise FeedbackError(f"{field} 不能为空")
    if len(value) > maximum:
        raise FeedbackError(f"{field} 最多允许 {maximum} 个字符", status_code=413, code="payload_too_large")
    return value


def validate_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise FeedbackError("payload 必须是 JSON 对象")
    schema_version = raw.get("schema_version", 1)
    if schema_version != 1:
        raise FeedbackError("不支持的 schema_version", code="unsupported_schema")
    product = _text(raw.get("product", "VoCoType-linux"), "product", maximum=64, required=True)
    if product != "VoCoType-linux":
        raise FeedbackError("product 必须是 VoCoType-linux")
    category = _text(raw.get("category", "other"), "category", maximum=32, required=True)
    if category not in CATEGORIES:
        raise FeedbackError("未知反馈类别")
    message = _text(raw.get("message"), "message", maximum=MAX_MESSAGE_CHARS, required=True)
    version = _text(raw.get("version", "unknown"), "version", maximum=64, required=True)
    platform = _text(raw.get("platform", ""), "platform", maximum=MAX_PLATFORM_CHARS)
    contact = _text(raw.get("contact", ""), "contact", maximum=MAX_CONTACT_CHARS)
    installation_id = _text(raw.get("installation_id", ""), "installation_id", maximum=128)
    if installation_id and not re.fullmatch(r"[0-9a-fA-F-]{32,36}", installation_id):
        raise FeedbackError("installation_id 格式无效")
    doctor = raw.get("doctor")
    if doctor is not None and not isinstance(doctor, list):
        raise FeedbackError("doctor 必须是数组或 null")
    if len(_json_bytes(doctor)) > MAX_DOCTOR_BYTES:
        raise FeedbackError("Doctor 数据过大", status_code=413, code="payload_too_large")
    return {
        "schema_version": 1,
        "product": product,
        "version": version,
        "category": category,
        "message": message,
        "platform": platform,
        "installation_id": installation_id,
        "doctor": doctor,
        "contact": contact,
    }


def validate_bundle(filename: str, data: bytes) -> tuple[str, str]:
    filename = Path(filename or "support.tar.gz").name
    lowered = filename.casefold()
    suffix = next((item for item in _ALLOWED_SUFFIXES if lowered.endswith(item)), "")
    if not suffix:
        raise FeedbackError("支持包只接受 .tar.gz、.tgz 或 .zip", code="unsupported_bundle")
    if len(data) > MAX_BUNDLE_BYTES:
        raise FeedbackError("支持包超过 5 MiB", status_code=413, code="payload_too_large")
    if suffix in {".tar.gz", ".tgz"} and not data.startswith(b"\x1f\x8b"):
        raise FeedbackError("支持包扩展名与 gzip 内容不匹配", code="invalid_bundle")
    if suffix == ".zip" and not data.startswith(b"PK"):
        raise FeedbackError("支持包扩展名与 zip 内容不匹配", code="invalid_bundle")
    return filename, suffix


def _doctor_error_ids(doctor: Any) -> list[str]:
    if not isinstance(doctor, list):
        return []
    result: list[str] = []
    for item in doctor:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status", "")).casefold() not in {"warn", "fail"}:
            continue
        result.append(str(item.get("check_id") or item.get("title") or "unknown")[:128])
    return sorted(set(result))


def duplicate_key(payload: Mapping[str, Any]) -> str:
    normalized = _WS_RE.sub(" ", str(payload["message"]).casefold()).strip()
    material = {
        "message": normalized,
        "version": payload["version"],
        "errors": _doctor_error_ids(payload.get("doctor")),
    }
    return hashlib.sha256(_json_bytes(material)).hexdigest()


def _new_id(now: datetime) -> str:
    return f"fb_{now.strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(5)}"


class FeedbackStore:
    def __init__(self, config: FeedbackConfig):
        self.config = config
        self.data_dir = config.data_dir
        self.db_path = self.data_dir / "feedback.db"
        self.attachments_dir = self.data_dir / "attachments"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(mode=0o700, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o750)
            os.chmod(self.attachments_dir, 0o700)
        except OSError:
            pass
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    product_version TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    contact TEXT NOT NULL,
                    doctor_json TEXT,
                    attachment_name TEXT,
                    attachment_path TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    internal_note TEXT NOT NULL DEFAULT '',
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    duplicate_key TEXT NOT NULL,
                    installation_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS feedback_created_idx ON feedback(created_at DESC);
                CREATE INDEX IF NOT EXISTS feedback_status_idx ON feedback(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS feedback_duplicate_idx ON feedback(duplicate_key, created_at DESC);

                CREATE TABLE IF NOT EXISTS request_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    installation_hash TEXT NOT NULL,
                    network_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS request_events_created_idx ON request_events(created_at);
                CREATE INDEX IF NOT EXISTS request_events_install_idx ON request_events(installation_hash, created_at);
                CREATE INDEX IF NOT EXISTS request_events_network_idx ON request_events(network_hash, created_at);
                """
            )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _hash(self, kind: str, value: str) -> str:
        return hmac.new(
            self.config.hmac_secret.encode("utf-8"),
            f"{kind}:{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _source_hashes(self, installation_id: str, source_ip: str) -> tuple[str, str]:
        install_value = installation_id or f"network:{source_ip}"
        try:
            network_value = str(ipaddress.ip_address(source_ip))
        except ValueError:
            network_value = "unknown"
        return self._hash("installation", install_value), self._hash("network", network_value)

    def _rate_limit(self, db: sqlite3.Connection, *, now: datetime, installation_hash: str, network_hash: str) -> None:
        minute = _iso(now - timedelta(minutes=1))
        hour = _iso(now - timedelta(hours=1))
        day = _iso(now - timedelta(days=1))
        global_count = db.execute(
            "SELECT COUNT(*) FROM request_events WHERE created_at >= ?", (minute,)
        ).fetchone()[0]
        install_hour = db.execute(
            "SELECT COUNT(*) FROM request_events WHERE installation_hash = ? AND created_at >= ?",
            (installation_hash, hour),
        ).fetchone()[0]
        install_day = db.execute(
            "SELECT COUNT(*) FROM request_events WHERE installation_hash = ? AND created_at >= ?",
            (installation_hash, day),
        ).fetchone()[0]
        network_hour = db.execute(
            "SELECT COUNT(*) FROM request_events WHERE network_hash = ? AND created_at >= ?",
            (network_hash, hour),
        ).fetchone()[0]
        if global_count >= self.config.global_minute_limit:
            raise FeedbackError("服务暂时繁忙，请稍后重试", status_code=429, code="rate_limited")
        if install_hour >= self.config.install_hour_limit or install_day >= self.config.install_day_limit:
            raise FeedbackError("本设备提交过于频繁，请稍后重试", status_code=429, code="rate_limited")
        if network_hour >= self.config.network_hour_limit:
            raise FeedbackError("当前网络提交过于频繁，请稍后重试", status_code=429, code="rate_limited")
        db.execute(
            "INSERT INTO request_events(created_at, installation_hash, network_hash) VALUES (?, ?, ?)",
            (_iso(now), installation_hash, network_hash),
        )
        db.execute("DELETE FROM request_events WHERE created_at < ?", (_iso(now - timedelta(days=2)),))

    def accept(
        self,
        raw_payload: Mapping[str, Any],
        *,
        source_ip: str,
        bundle_name: str | None = None,
        bundle_data: bytes | None = None,
        now: datetime | None = None,
    ) -> AcceptedFeedback:
        payload = validate_payload(raw_payload)
        now = (now or utc_now()).astimezone(UTC)
        attachment: tuple[str, str] | None = None
        if bundle_data is not None:
            attachment = validate_bundle(bundle_name or "support.tar.gz", bundle_data)
        installation_hash, network_hash = self._source_hashes(payload["installation_id"], source_ip)
        key = duplicate_key(payload)
        cutoff = _iso(now - timedelta(hours=self.config.duplicate_window_hours))

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._rate_limit(
                db,
                now=now,
                installation_hash=installation_hash,
                network_hash=network_hash,
            )
            if attachment is None:
                duplicate = db.execute(
                    """
                    SELECT id, occurrence_count FROM feedback
                    WHERE duplicate_key = ? AND created_at >= ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (key, cutoff),
                ).fetchone()
                if duplicate is not None:
                    occurrence_count = int(duplicate["occurrence_count"]) + 1
                    db.execute(
                        "UPDATE feedback SET occurrence_count = ?, updated_at = ? WHERE id = ?",
                        (occurrence_count, _iso(now), duplicate["id"]),
                    )
                    return AcceptedFeedback(str(duplicate["id"]), True, occurrence_count)

            feedback_id = _new_id(now)
            stored_path = ""
            stored_name = ""
            if attachment is not None and bundle_data is not None:
                original_name, suffix = attachment
                target = self.attachments_dir / f"{feedback_id}{suffix}"
                fd, tmp_name = tempfile.mkstemp(prefix=f".{feedback_id}.", dir=self.attachments_dir)
                tmp_path = Path(tmp_name)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(bundle_data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(tmp_path, 0o600)
                    os.replace(tmp_path, target)
                finally:
                    tmp_path.unlink(missing_ok=True)
                stored_path = str(target)
                stored_name = original_name

            db.execute(
                """
                INSERT INTO feedback(
                    id, created_at, updated_at, product_version, category, message,
                    platform, contact, doctor_json, attachment_name, attachment_path,
                    status, occurrence_count, duplicate_key, installation_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 1, ?, ?)
                """,
                (
                    feedback_id,
                    _iso(now),
                    _iso(now),
                    payload["version"],
                    payload["category"],
                    payload["message"],
                    payload["platform"],
                    payload["contact"],
                    json.dumps(payload["doctor"], ensure_ascii=False) if payload["doctor"] is not None else None,
                    stored_name or None,
                    stored_path or None,
                    key,
                    installation_hash,
                ),
            )
            return AcceptedFeedback(feedback_id, False, 1)

    def list_feedback(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        sql = "SELECT id, created_at, category, product_version, status, occurrence_count, substr(message,1,160) AS summary FROM feedback"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def get_feedback(self, feedback_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result.get("doctor_json"):
            result["doctor"] = json.loads(result.pop("doctor_json"))
        else:
            result.pop("doctor_json", None)
            result["doctor"] = None
        result.pop("installation_hash", None)
        result.pop("duplicate_key", None)
        return result


    def maintenance(
        self,
        *,
        attachment_days: int = 30,
        backup_dir: Path | None = None,
        backup_days: int = 14,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Expire old private bundles and create a consistent SQLite backup."""

        now = (now or utc_now()).astimezone(UTC)
        attachment_cutoff = _iso(now - timedelta(days=max(1, attachment_days)))
        removed_attachments = 0
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, attachment_path FROM feedback "
                "WHERE attachment_path IS NOT NULL AND created_at < ?",
                (attachment_cutoff,),
            ).fetchall()
            for row in rows:
                path = Path(str(row["attachment_path"]))
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
                db.execute(
                    "UPDATE feedback SET attachment_name = NULL, attachment_path = NULL, updated_at = ? WHERE id = ?",
                    (_iso(now), row["id"]),
                )
                removed_attachments += 1

        backup_path: str | None = None
        removed_backups = 0
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(backup_dir, 0o700)
            except OSError:
                pass
            target = backup_dir / f"feedback-{now.strftime('%Y%m%d-%H%M%S')}.db"
            with self._connect() as source, sqlite3.connect(target) as destination:
                source.backup(destination)
            os.chmod(target, 0o600)
            backup_path = str(target)
            backup_cutoff = now - timedelta(days=max(1, backup_days))
            for candidate in backup_dir.glob("feedback-*.db"):
                if candidate == target:
                    continue
                try:
                    modified = datetime.fromtimestamp(candidate.stat().st_mtime, UTC)
                    if modified < backup_cutoff:
                        candidate.unlink()
                        removed_backups += 1
                except OSError:
                    continue
        return {
            "removed_attachments": removed_attachments,
            "backup_path": backup_path,
            "removed_backups": removed_backups,
        }

    def update_status(self, feedback_id: str, status: str, *, note: str = "") -> bool:
        if status not in {"new", "triaged", "resolved", "spam"}:
            raise ValueError("invalid status")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE feedback SET status = ?, internal_note = ?, updated_at = ? WHERE id = ?",
                (status, note.strip()[:10_000], _iso(utc_now()), feedback_id),
            )
            return cursor.rowcount > 0
