"""Compatibility helpers for IBus versions shipped by Linux distributions."""

from __future__ import annotations

from typing import Any


_CAPABILITY_NAMES = (
    ("PREEDIT_TEXT", "preedit"),
    ("AUXILIARY_TEXT", "aux"),
    ("LOOKUP_TABLE", "lookup"),
    ("FOCUS", "focus"),
    ("PROPERTY", "property"),
    ("SURROUNDING_TEXT", "surrounding"),
    ("OSK", "osk"),
    ("SYNC_PROCESS_KEY", "sync_key"),
)


def build_capability_flags(capabilite: Any) -> tuple[tuple[int, str], ...]:
    """Return capability flags exposed by the installed IBus version.

    IBus 1.5.26, as shipped by Ubuntu 22.04, does not expose ``OSK`` or
    ``SYNC_PROCESS_KEY``. Looking those attributes up while importing the
    engine makes the whole input method fail to start, so capability discovery
    must be defensive.
    """

    flags: list[tuple[int, str]] = []
    for attribute, label in _CAPABILITY_NAMES:
        value = getattr(capabilite, attribute, None)
        if value is None:
            continue
        try:
            flags.append((int(value), label))
        except (TypeError, ValueError):
            continue
    return tuple(flags)
