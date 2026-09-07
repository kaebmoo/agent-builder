#!/usr/bin/env python3
"""M2 gate: checked-in AgentSpec fixtures pass the M1 contract."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
    from jsonschema import Draft202012Validator, SchemaError
except ModuleNotFoundError:
    print("M2 FAIL: install project dependencies first (jsonschema>=4.20, pyyaml>=6.0)")
    raise SystemExit(1)

from check_m1_spec import all_errors

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "agentspec.schema.json"
REQUIRED_KINDS = {"normal", "missing_input", "refusal", "malformed"}
REQUIRED_FIXTURES = {
    "invoice-reviewer": {"transport": "atlas_file_handoff", "pack": None},
    "sql-reader": {"transport": "prompt_json", "pack": "sql-readonly"},
}


def load_spec(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError("fixture must be a YAML object")
    return loaded


def input_definitions(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    inputs = spec.get("inputs")
    if not isinstance(inputs, list):
        return {}
    return {
        item["name"]: item
        for item in inputs
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def input_validation_errors(definition: dict[str, Any], value: Any) -> list[str]:
    schema = definition.get("schema")
    if not isinstance(schema, dict):
        return []
    try:
        return sorted(error.message for error in Draft202012Validator(schema).iter_errors(value))
    except SchemaError as error:
        return [f"invalid input schema: {error.message}"]


def check_golden_inputs(
    path: Path, spec: dict[str, Any], findings: list[str]
) -> None:
    definitions = input_definitions(spec)
    evaluation = spec.get("evaluation")
    cases = evaluation.get("golden_cases", []) if isinstance(evaluation, dict) else []
    if not isinstance(cases, list):
        return

    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = case.get("id", "<unknown>")
        kind = case.get("kind")
        payload = case.get("input")
        if not isinstance(payload, dict):
            continue

        unknown_names = [name for name in payload if name not in definitions]
        if unknown_names:
            rendered = ", ".join(sorted(map(str, unknown_names)))
            findings.append(
                f"{path.relative_to(ROOT)}: golden case {case_id} has undeclared input: {rendered}"
            )
            continue

        invalid_names = {
            name: input_validation_errors(definitions[name], value)
            for name, value in payload.items()
        }
        invalid_names = {name: errors for name, errors in invalid_names.items() if errors}

        if kind in {"normal", "refusal"}:
            missing_names = set(definitions) - set(payload)
            if missing_names:
                findings.append(
                    f"{path.relative_to(ROOT)}: {kind} case {case_id} misses declared input "
                    f"{', '.join(sorted(missing_names))}"
                )
            for name in sorted(invalid_names):
                errors = invalid_names[name]
                findings.append(
                    f"{path.relative_to(ROOT)}: {kind} case {case_id} has invalid {name}: "
                    f"{'; '.join(errors)}"
                )
        elif kind == "missing_input":
            if not set(definitions) - set(payload):
                findings.append(
                    f"{path.relative_to(ROOT)}: missing_input case {case_id} omits no declared input"
                )
            for name in sorted(invalid_names):
                errors = invalid_names[name]
                findings.append(
                    f"{path.relative_to(ROOT)}: missing_input case {case_id} has invalid {name}: "
                    f"{'; '.join(errors)}"
                )
        elif kind == "malformed":
            missing_names = set(definitions) - set(payload)
            if missing_names:
                findings.append(
                    f"{path.relative_to(ROOT)}: malformed case {case_id} misses declared input "
                    f"{', '.join(sorted(missing_names))}"
                )
            if not invalid_names:
                if not any(isinstance(item.get("schema"), dict) for item in definitions.values()):
                    findings.append(
                        f"{path.relative_to(ROOT)}: malformed case {case_id} requires a declared input schema"
                    )
                else:
                    findings.append(
                        f"{path.relative_to(ROOT)}: malformed case {case_id} has no schema-invalid input"
                    )


def main() -> int:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, SchemaError) as error:
        print(f"M2 FAIL: unable to load AgentSpec schema: {error}")
        return 1
    validator = Draft202012Validator(schema)
    findings: list[str] = []

    paths = sorted((ROOT / "fixtures").glob("*/spec.yaml"))
    discovered = {path.parent.name for path in paths}
    missing_fixtures = set(REQUIRED_FIXTURES) - discovered
    for name in sorted(missing_fixtures):
        findings.append(f"fixtures/{name}/spec.yaml: required fixture is missing")

    for path in paths:
        name = path.parent.name
        expectation = REQUIRED_FIXTURES.get(name)
        try:
            spec = load_spec(path)
        except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError) as error:
            findings.append(f"{path.relative_to(ROOT)}: {error}")
            continue

        m1_valid = False
        try:
            m1_errors = all_errors(spec, validator)
        except (AttributeError, KeyError, TypeError) as error:
            findings.append(f"{path.relative_to(ROOT)}: M1 validation failed: {error}")
        else:
            m1_valid = not m1_errors
            for error_path, message in m1_errors:
                location = ".".join(map(str, error_path)) or "<root>"
                findings.append(f"{path.relative_to(ROOT)}:{location}: {message}")

        if not m1_valid:
            continue

        identity = spec.get("identity")
        if not isinstance(identity, dict) or identity.get("name") != name:
            findings.append(f"{path.relative_to(ROOT)}: identity.name must be {name}")
        if expectation is not None:
            if spec.get("target") != "atlas-worker":
                findings.append(f"{path.relative_to(ROOT)}: target must be atlas-worker")

            inputs = spec.get("inputs", [])
            if not isinstance(inputs, list):
                inputs = []
            matching_inputs = [
                item
                for item in inputs
                if isinstance(item, dict) and item.get("transport") == expectation["transport"]
            ]
            if len(matching_inputs) != 1:
                findings.append(
                    f"{path.relative_to(ROOT)}: expected one {expectation['transport']} input"
                )
            if name == "invoice-reviewer" and any("path" in item for item in matching_inputs):
                findings.append(
                    f"{path.relative_to(ROOT)}: Atlas must choose the file-handoff landing path"
                )

            capabilities = spec.get("capabilities", [])
            if not isinstance(capabilities, list):
                capabilities = []
            packs = {
                item.get("pack") for item in capabilities if isinstance(item, dict)
            }
            if expectation["pack"] is not None and expectation["pack"] not in packs:
                findings.append(f"{path.relative_to(ROOT)}: missing required pack {expectation['pack']}")
            if expectation["pack"] is None and packs:
                findings.append(f"{path.relative_to(ROOT)}: fixture must not require a capability pack")

        evaluation = spec.get("evaluation", {})
        cases = evaluation.get("golden_cases", []) if isinstance(evaluation, dict) else []
        kinds = [case.get("kind") for case in cases if isinstance(case, dict)]
        if len(kinds) != 4 or set(kinds) != REQUIRED_KINDS:
            findings.append(
                f"{path.relative_to(ROOT)}: golden cases must contain exactly "
                "normal, missing_input, refusal, malformed"
            )
        check_golden_inputs(path, spec, findings)

    if findings:
        print("M2 FAIL:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("M2 OK: all fixtures pass M1 with structurally meaningful golden cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
