"""Operator CLI for triaging locally stored feedback."""

from __future__ import annotations

import argparse
import json

from .core import FeedbackConfig, FeedbackStore


def main() -> int:
    parser = argparse.ArgumentParser(prog="vocotype-feedback")
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list")
    list_parser.add_argument("--status", choices=["new", "triaged", "resolved", "spam"])
    list_parser.add_argument("--limit", type=int, default=50)
    show_parser = sub.add_parser("show")
    show_parser.add_argument("feedback_id")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("feedback_id")
    status_parser.add_argument("status", choices=["new", "triaged", "resolved", "spam"])
    status_parser.add_argument("--note", default="")
    maintenance_parser = sub.add_parser("maintenance")
    maintenance_parser.add_argument("--attachment-days", type=int, default=30)
    maintenance_parser.add_argument("--backup-dir", default="/var/backups/vocotype-feedback")
    maintenance_parser.add_argument("--backup-days", type=int, default=14)
    args = parser.parse_args()
    store = FeedbackStore(FeedbackConfig.from_env())
    if args.command == "list":
        print(json.dumps(store.list_feedback(status=args.status, limit=args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.command == "show":
        item = store.get_feedback(args.feedback_id)
        if item is None:
            parser.error("feedback not found")
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0
    if args.command == "maintenance":
        from pathlib import Path

        result = store.maintenance(
            attachment_days=args.attachment_days,
            backup_dir=Path(args.backup_dir),
            backup_days=args.backup_days,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not store.update_status(args.feedback_id, args.status, note=args.note):
        parser.error("feedback not found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
