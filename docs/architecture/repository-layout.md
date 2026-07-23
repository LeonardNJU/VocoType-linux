# Repository layout

VoCoType Linux is organized by **module ownership**, not by file type. A file should live beside the code and tests that change with it; repository-wide entrypoints are the exception.

```text
.
├── .github/                 GitHub Actions and curated release notes
├── docs/                    All user, operator, development, and architecture docs
│   ├── architecture/        Runtime and repository design
│   ├── adr/                 Durable architecture decisions
│   ├── development/         Packaging and contributor workflows
│   ├── integrations/        Fcitx 5 and IBus behavior
│   ├── services/            Independently deployed services
│   └── ...                  Guides, installation, and troubleshooting
├── packaging/               Distribution and release implementation
│   ├── arch/ debian/ rpm/   Distribution-specific metadata
│   ├── nix/                 Nix derivation used by the root flake
│   ├── common/              Shared wrappers and systemd units
│   ├── scripts/             Build, staging, version, and asset tooling
│   └── tests/               Package audit and install/remove smoke tests
├── resources/               Product resources installed by every packaging path
│   ├── desktop/             Desktop entry
│   ├── metainfo/            AppStream metadata
│   └── templates/           Default user-data templates
├── scripts/                 Repository-wide executable entrypoints
│   ├── install/             Source/user install and uninstall entrypoints
│   ├── test/                Cross-module contracts and full test orchestration
│   ├── diagnostics/         User/developer diagnostics
│   ├── benchmarks/          Non-product benchmarks
│   └── site/                Static site and documentation builder
├── src/                     Product and service source modules
│   ├── common/              Shared terminology/YAML implementation
│   ├── core/                ASR dispatch, ITN, terminology, SLM, and edit planning
│   ├── desktop/             GTK settings, recorder, model manager, and IBus engine
│   ├── integrations/        Input-method adapters and integration metadata
│   │   ├── fcitx5/          Fcitx global Module and IPC adapter
│   │   └── ibus/            IBus component metadata
│   ├── workers/funasr/      Pinned FunASR offline/streaming worker build
│   └── services/feedback/   Feedback service source, tests, and deployment assets
└── web/                     Static project website source
```

## Root policy

The root contains only conventional project entrypoints and metadata:

```text
README.md  LICENSE  CHANGELOG.md  THIRD_PARTY_NOTICES.md  VERSION
Makefile   flake.nix  flake.lock  .gitignore
```

`flake.nix` and `flake.lock` stay at the root because `nix build .`, `nix run .`, and GitHub flake references discover the flake there. The implementation is colocated with Debian, RPM, and Arch under `packaging/nix/`.

## Module rules

- Unit tests stay inside their source module: `src/*/tests/`.
- Package tests stay inside `packaging/tests/`, because package metadata, staging, and smoke behavior form one release module.
- Only cross-module orchestration belongs in `scripts/test/`.
- Product documentation lives only in `docs/`; source and deployment directories do not carry duplicate READMEs.
- Feedback source, tests, systemd units, Nginx config, TLS helpers, and operator tooling form one module under `src/services/feedback/`.
- Installed filesystem paths such as `/usr/share/fcitx5` do not determine source-tree layout.

The structure is enforced by `scripts/test/contracts.sh`; adding a new top-level directory or root file requires an explicit architecture decision.
