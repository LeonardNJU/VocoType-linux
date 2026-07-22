#!/usr/bin/env python3
"""Canonical VoCoType version parsing and distro-package mappings."""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

_VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:(?:-?(?P<label>beta|b|rc)(?:\.|)?)(?P<serial>[1-9][0-9]*))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReleaseVersion:
    major: int
    minor: int
    patch: int
    stage: str | None = None
    serial: int | None = None

    @classmethod
    def parse(cls, value: str) -> "ReleaseVersion":
        normalized = str(value or "").strip()
        if normalized.startswith(("v", "V")):
            normalized = normalized[1:]
        match = _VERSION_RE.fullmatch(normalized)
        if not match:
            raise ValueError(
                "version must be MAJOR.MINOR.PATCH, MAJOR.MINOR.PATCHbN, "
                "vMAJOR.MINOR.PATCH-beta.N, MAJOR.MINOR.PATCHrcN, "
                "or vMAJOR.MINOR.PATCH-rc.N"
            )
        label = (match.group("label") or "").lower()
        stage = "beta" if label in {"b", "beta"} else "rc" if label == "rc" else None
        serial = int(match.group("serial")) if match.group("serial") else None
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            stage,
            serial,
        )

    @property
    def base(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def python(self) -> str:
        if self.stage is None:
            return self.base
        marker = "b" if self.stage == "beta" else "rc"
        return f"{self.base}{marker}{self.serial}"

    @property
    def tag(self) -> str:
        if self.stage is None:
            return f"v{self.base}"
        return f"v{self.base}-{self.stage}.{self.serial}"

    @property
    def debian(self) -> str:
        if self.stage is None:
            return self.base
        return f"{self.base}~{self.stage}{self.serial}"

    @property
    def rpm_version(self) -> str:
        return self.base

    @property
    def rpm_release(self) -> str:
        if self.stage is None:
            return "1"
        return f"0.{self.stage}{self.serial}"

    @property
    def arch(self) -> str:
        return self.python

    @property
    def prerelease(self) -> bool:
        return self.stage is not None

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "python": self.python,
            "tag": self.tag,
            "debian": self.debian,
            "rpm_version": self.rpm_version,
            "rpm_release": self.rpm_release,
            "arch": self.arch,
            "prerelease": self.prerelease,
        }


def normalize_expected_version(value: str) -> str:
    return ReleaseVersion.parse(value).python


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument(
        "--field",
        choices=(
            "python",
            "tag",
            "debian",
            "rpm_version",
            "rpm_release",
            "arch",
            "prerelease",
        ),
        default="python",
    )
    args = parser.parse_args()
    version = ReleaseVersion.parse(args.version)
    value = version.as_dict()[args.field]
    print("true" if value is True else "false" if value is False else value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
