# Changelog

## [Unreleased]

### Added

- M7b follow-up (2026-09-08): every package carries a `.thclaws/prompt/system.md` mission-runner profile that replaces the thClaws coding base prompt when the daemon starts from the package, and the publisher MCP sends its skill guidance as `InitializeResult.instructions`; static audit covers the profile and the M7b gate checks initialize propagation and brace-free profile text
- M7b (2026-09-08): publisher email/SFTP pack with explicit dry runs, fixed operator destinations, persistent idempotency keys and fail-closed handling of unknown delivery outcomes; T2 packages include a statically audited Atlas approval fragment requiring a dedicated daemon and explicit worker/workspace bindings. Generation does not verify deployment or approve delivery

- M7a (2026-09-07): SQL capability pack wrapping project AI, direct MCP conformance testing with `forge pack test`, generated `.thclaws/mcp.json`, pinned thClaws tool catalog, and static audit of pack tool/env/network requirements; SQL conformance uses synthetic SQLite data without a provider key

- M1/M2 follow-up (2026-09-07): tool names must be runtime names (provider regex; MCP tools as `<server>__<tool>`), and the `sql-reader` fixture now declares `sql-readonly__*`
- M5 (2026-09-07): deterministic static audit (`forge audit`, `forge/audit.py`) that re-renders a package from its `agentspec.json` and reports readable findings, warning/strict handling for unknown schema keywords, Atlas-only single-worker rules, `audit.py`/`studio.py` runners in every package, and `draft` promotion gated on `thclaws agent validate`
- M4 (2026-09-07): deterministic Atlas single-worker generator and `forge generate` CLI, shared AgentSpec validation, schema-checked build reports with explicit unverified guarantees/dependencies, and native thClaws validation gate
- M3 (2026-09-06): compatibility matrix and deterministic AgentSpec resolver for execution surface, package pattern and flow location, with a gate covering valid/rejected combinations and schema agreement

- M0 (2026-09-03): repo skeleton, docs/DESIGN.md baseline, docs/PLAN.md M0–M8, `scripts/check_m0_layout.py`
- M1 (2026-09-04): AgentSpec schema, target/pattern routing rules, tier/write boundaries, embedded-schema validation, and semantic gate
- M1 follow-up (2026-09-04): version-range satisfiability checks, property-based Bash/write-tool checks, and clarified artifact snapshot semantics
- M1 follow-up (2026-09-04): network-tool declaration checks and friendly schema-load failures
- M2 (2026-09-04): invoice-reviewer and sql-reader fixtures, plus deterministic golden-case validation
- M1/M2 follow-up (2026-09-04): explicit golden-case output branches for live audit

### Changed

- M7a: capability pack descriptors now require the v2 contract in DESIGN §5; v1 descriptors are no longer supported. Build reports include pack conformance evidence and deployment requirements when available

### Fixed

- Clarify generated mission, input-envelope and output instructions, separate publisher preview from approved delivery in the fixture mission, and add opt-in publisher live regression; publisher passed 6/6 live audit rounds on `oai/gpt-5.4-mini` (pinned `gpt-5.4` untested). Golden cases and schema/SSE audit criteria are unchanged

- M5 review follow-up: report schema ties `audit.manifest`/`audit.static` to the recorded validate status and findings, and the inventory rule now covers the whole package tree (stray files and symlinks fail)
- M4 review follow-up: arrange gate imports for Ruff 0.5 compatibility and remove template-generated blank lines while preserving separate input/refusal list items
- M3 follow-up (2026-09-07): derive flow location per pattern; dynamic has no run.js, confirmed against thClaws v0.116.0 revision 75edc48
- Milestone scripts and shared validation now pass Ruff, including the existing executable-bit and lint findings

[Unreleased]: https://github.com/kaebmoo/agent-builder/commits/main
