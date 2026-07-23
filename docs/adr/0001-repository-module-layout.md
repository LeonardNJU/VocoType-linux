# ADR 0001: Organize the repository by module ownership

- Status: Accepted
- Date: 2026-07-24

## Context

The repository accumulated separate top-level directories for implementation language (`native/`), input frameworks (`fcitx5/`, `ibus/`), an independently deployed service (`feedback_service/` and `deploy/feedback/`), installation scripts, test scripts, Nix packaging, website files, and documentation. Understanding or changing one product area required crossing several unrelated top-level seams.

## Decision

Use seven tracked top-level directories: `.github/`, `docs/`, `packaging/`, `resources/`, `scripts/`, `src/`, and `web/`.

Source is grouped by runtime module under `src/`. Distribution implementations are grouped under `packaging/`; only the flake discovery files remain at the root. Repository-wide executable entrypoints are grouped by purpose under `scripts/`. All prose documentation is centralized under `docs/`. The feedback receiver and its deployment assets are one module.

Tests follow ownership: unit tests remain beside source, package tests remain beside packaging, and only cross-module contracts live under `scripts/test/`.

## Consequences

- A maintainer can locate a change by domain rather than implementation language.
- Fcitx, IBus, and feedback changes have higher locality.
- Nix is no longer a special top-level implementation while preserving standard flake discovery.
- Paths in external source-install commands change; documentation and release archives must carry the new paths.
- A repository contract rejects legacy top-level directories and undocumented root growth.
