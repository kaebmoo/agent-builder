# Changelog

## [Unreleased]

### Added

- M4 (2026-09-07): deterministic Atlas single-worker generator and `forge generate` CLI, shared AgentSpec validation, schema-checked build reports with explicit unverified guarantees/dependencies, and native thClaws validation gate
- M3 (2026-09-06): compatibility matrix and deterministic AgentSpec resolver for execution surface, package pattern and flow location, with a gate covering valid/rejected combinations and schema agreement

- M0 (2026-09-03): repo skeleton, docs/DESIGN.md baseline, docs/PLAN.md M0–M8, `scripts/check_m0_layout.py`
- M1 (2026-09-04): AgentSpec schema, target/pattern routing rules, tier/write boundaries, embedded-schema validation, and semantic gate
- M1 follow-up (2026-09-04): version-range satisfiability checks, property-based Bash/write-tool checks, and clarified artifact snapshot semantics
- M1 follow-up (2026-09-04): network-tool declaration checks and friendly schema-load failures
- M2 (2026-09-04): invoice-reviewer and sql-reader fixtures, plus deterministic golden-case validation
- M1/M2 follow-up (2026-09-04): explicit golden-case output branches for live audit

### Fixed

- M4 review follow-up: arrange gate imports for Ruff 0.5 compatibility and remove template-generated blank lines while preserving separate input/refusal list items
- M3 follow-up (2026-09-07): derive flow location per pattern; dynamic has no run.js, confirmed against thClaws v0.116.0 revision 75edc48
- Milestone scripts and shared validation now pass Ruff, including the existing executable-bit and lint findings

[Unreleased]: https://github.com/kaebmoo/agent-builder/commits/main
