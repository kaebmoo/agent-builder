"""Shared AgentSpec validation used by milestone gates and the generator."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from jsonschema import Draft202012Validator, SchemaError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "agentspec.schema.json"
ATLAS_TARGETS = {"atlas-worker", "atlas-workflow"}
REQUIRED_GOLDEN_KINDS = {"normal", "missing_input", "refusal", "malformed"}

# M1 keeps the catalog tiny and deterministic. M7 will replace this with the
# checked-in packs/*/pack.yaml catalog.
MUTATING_PACKS = {"publisher-email-sftp"}
SHELL_BUILTIN_TOOLS = {"bash"}
WRITE_BUILTIN_TOOLS = {
    "write",
    "edit",
    "docxcreate",
    "docxedit",
    "xlsxcreate",
    "xlsxedit",
    "pptxcreate",
    "pptxedit",
    "epubcreate",
    "fetchimages",
    "pdfcreate",
}
NETWORK_BUILTIN_TOOLS = {
    "fetchimages",
    "webfetch",
    "webscrape",
    "websearch",
    "youtubetranscript",
}
VERSION_OPERATORS = (">=", "<=", "==", ">", "<")

PathPart = str | int
Finding = tuple[tuple[PathPart, ...], str]


def schema_errors(spec: dict[str, Any], validator: Draft202012Validator) -> list[Finding]:
    return [
        (tuple(error.absolute_path), error.message)
        for error in sorted(validator.iter_errors(spec), key=lambda item: list(item.absolute_path))
    ]


def semantic_errors(spec: dict[str, Any]) -> list[Finding]:
    errors: list[Finding] = []
    target = spec.get("target")
    permissions = spec.get("permissions", {})
    tier = permissions.get("tier")

    for index, tool in enumerate(permissions.get("tools", [])):
        if not isinstance(tool, str):
            continue
        normalized_tool = tool.casefold()
        if permissions.get("shell") == "none" and normalized_tool in SHELL_BUILTIN_TOOLS:
            errors.append(
                (("permissions", "tools", index), f"shell=none cannot declare shell tool: {tool}")
            )
        if permissions.get("write_scope") == "none" and normalized_tool in WRITE_BUILTIN_TOOLS:
            errors.append(
                (
                    ("permissions", "tools", index),
                    f"write_scope=none cannot declare write tool: {tool}",
                )
            )
        if permissions.get("network") == "none" and normalized_tool in NETWORK_BUILTIN_TOOLS:
            errors.append(
                (
                    ("permissions", "tools", index),
                    f"network=none cannot declare network tool: {tool}",
                )
            )
    for index, capability in enumerate(spec.get("capabilities", [])):
        if capability.get("pack") in MUTATING_PACKS and tier != "T2":
            errors.append(
                (("capabilities", index, "pack"), f"mutating pack requires T2: {capability['pack']}")
            )

    state_mode = spec.get("state", {}).get("mode")
    if state_mode == "durable_memory":
        errors.append((("state", "mode"), "durable_memory is deferred beyond M1"))
    if target in ATLAS_TARGETS and state_mode != "none":
        errors.append((("state", "mode"), "Atlas targets do not support stateful execution in M1"))
    if target in {"atlas-worker", "thclaws-standalone"} and tier == "T2":
        errors.append((("permissions", "tier"), "T2 requires atlas-workflow with a human_gate"))
    if target in ATLAS_TARGETS and spec.get("model", {}).get("mode") != "pinned":
        errors.append((("model", "mode"), "Atlas targets require a pinned model in M1"))
    if target in ATLAS_TARGETS and len(spec.get("outputs", [])) > 1:
        errors.append((("outputs",), "Atlas nodes support at most one output artifact"))

    for section in ("inputs", "outputs"):
        entries = spec.get(section, [])
        seen: dict[str, int] = {}
        for index, entry in enumerate(entries):
            name = entry.get("name")
            if name in seen:
                errors.append(((section, index, "name"), f"duplicate {section[:-1]} name: {name}"))
            seen[name] = index

    cases = spec.get("evaluation", {}).get("golden_cases", [])
    seen_cases: dict[str, int] = {}
    present_kinds: set[str] = set()
    for index, case in enumerate(cases):
        case_id = case.get("id")
        if case_id in seen_cases:
            errors.append(
                (("evaluation", "golden_cases", index, "id"), f"duplicate golden case id: {case_id}")
            )
        seen_cases[case_id] = index
        present_kinds.add(case.get("kind"))
    for kind in sorted(REQUIRED_GOLDEN_KINDS - present_kinds):
        errors.append((("evaluation", "golden_cases"), f"missing golden case kind: {kind}"))

    for section, entries in (("inputs", spec.get("inputs", [])), ("outputs", spec.get("outputs", []))):
        for index, entry in enumerate(entries):
            if "schema" in entry:
                errors.extend(check_json_schema(entry["schema"], (section, index, "schema")))
            if section == "outputs" and entry.get("transport") == "collect_files":
                globs = entry.get("files", {}).get("globs", [])
                for glob_index, pattern in enumerate(globs):
                    normalized_pattern = pattern.replace("\\", "/")
                    if normalized_pattern.startswith("/") or ".." in PurePosixPath(normalized_pattern).parts:
                        errors.append(
                            (
                                (section, index, "files", "globs", glob_index),
                                "file glob must be workspace-relative",
                            )
                        )

    if "schema" in spec.get("refusal", {}):
        errors.extend(check_json_schema(spec["refusal"]["schema"], ("refusal", "schema")))

    errors.extend(
        check_version_constraint(
            spec.get("identity", {}).get("thclaws_min_version"),
            ("identity", "thclaws_min_version"),
        )
    )

    return errors


def check_json_schema(candidate: Any, path: tuple[PathPart, ...]) -> list[Finding]:
    if not isinstance(candidate, dict) or not candidate:
        return [(path, "embedded JSON Schema must be a non-empty object")]
    try:
        Draft202012Validator.check_schema(candidate)
    except SchemaError as error:
        return [(path, f"invalid embedded JSON Schema: {error.message}")]
    return []


def check_version_constraint(candidate: Any, path: tuple[PathPart, ...]) -> list[Finding]:
    if not isinstance(candidate, str):
        return []

    clauses: list[tuple[str, tuple[int, int, int], bool]] = []
    for raw_clause in candidate.split(","):
        operator = next((op for op in VERSION_OPERATORS if raw_clause.startswith(op)), "")
        version_text = raw_clause[len(operator) :]
        parts = version_text.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            return []  # JSON Schema owns lexical validation.
        clauses.append((operator or "==", tuple(int(part) for part in parts), not operator))

    if any(is_bare for _, _, is_bare in clauses) and len(clauses) > 1:
        return [(path, "bare version cannot be combined with other clauses")]
    if not any(operator in {">=", ">", "=="} for operator, _, _ in clauses):
        return [(path, "version constraint requires a minimum or exact version")]

    exact_versions = {version for operator, version, _ in clauses if operator == "=="}
    if len(exact_versions) > 1:
        return [(path, "version constraint has conflicting exact versions")]
    if exact_versions:
        exact = next(iter(exact_versions))
        if not all(version_satisfies(exact, operator, bound) for operator, bound, _ in clauses):
            return [(path, "version constraint is unsatisfiable")]
        return []

    lower: tuple[tuple[int, int, int], bool] | None = None
    upper: tuple[tuple[int, int, int], bool] | None = None
    for operator, version, _ in clauses:
        if operator in {">=", ">"} and (
            lower is None or version > lower[0] or (version == lower[0] and operator == ">")
        ):
            lower = (version, operator == ">")
        if operator in {"<=", "<"} and (
            upper is None or version < upper[0] or (version == upper[0] and operator == "<")
        ):
            upper = (version, operator == "<")

    if lower and upper and (lower[0] > upper[0] or (lower[0] == upper[0] and (lower[1] or upper[1]))):
        return [(path, "version constraint is unsatisfiable")]
    return []


def version_satisfies(
    version: tuple[int, int, int], operator: str, bound: tuple[int, int, int]
) -> bool:
    return {
        ">=": version >= bound,
        ">": version > bound,
        "<=": version <= bound,
        "<": version < bound,
        "==": version == bound,
    }[operator]


def all_errors(spec: dict[str, Any], validator: Draft202012Validator) -> list[Finding]:
    return schema_errors(spec, validator) + semantic_errors(spec)


def validate_spec(spec: Any) -> None:
    """Validate structure before semantic checks; never validate a thClaws manifest."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = schema_errors(spec, validator)
    if not errors:
        errors = semantic_errors(spec)
    if errors:
        raise ValueError("Invalid AgentSpec: " + "; ".join(
            f"{'.'.join(map(str, path)) or '<root>'}: {message}" for path, message in errors
        ))
    # Reject YAML-specific values and NaN rather than emitting non-JSON artifacts.
    json.dumps(spec, allow_nan=False)


def load_spec(path: Path) -> dict[str, Any]:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_spec(spec)
    return spec
