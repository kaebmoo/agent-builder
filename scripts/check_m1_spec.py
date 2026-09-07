#!/usr/bin/env python3
"""M1 gate: AgentSpec schema and semantic constraints."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "agentspec.schema.json"
sys.path.insert(0, str(ROOT))

try:
    from jsonschema import Draft202012Validator, SchemaError

    from forge.spec import NETWORK_BUILTIN_TOOLS, PathPart, all_errors
except ModuleNotFoundError:
    print("M1 FAIL: install project dependencies first (jsonschema>=4.20, pyyaml>=6.0)")
    raise SystemExit(1)


def base_spec(
    name: str,
    target: str,
    input_transport: str,
    pack: str | None = None,
    package_pattern: str = "static-pipeline",
) -> dict[str, Any]:
    capabilities = [] if pack is None else [{"pack": pack, "params": {}}]
    spec: dict[str, Any] = {
        "identity": {
            "name": name,
            "version": "0.1.0",
            "owner": "example",
            "license": "Apache-2.0",
            "thclaws_min_version": ">=0.116.0",
        },
        "mission": {
            "purpose": "Process the requested input and return a structured result that can be audited.",
            "domain": "document-processing",
        },
        "target": target,
        "inputs": [{"name": "request", "transport": input_transport, "schema": {"type": "object"}}],
        "outputs": [
            {
                "name": "result",
                "transport": "assistant_json",
                "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            }
        ],
        "capabilities": capabilities,
        "permissions": {
            "tier": "T0",
            "tools": ["Read"],
            "shell": "none",
            "network": "none",
            "write_scope": "none",
        },
        "refusal": {
            "conditions": ["required input is missing", "the request is outside the mission"],
            "response": "Return a refusal object with the reason and the required handoff.",
            "schema": {
                "type": "object",
                "required": ["refused", "reason"],
                "properties": {
                    "refused": {"const": True},
                    "reason": {"type": "string"},
                },
            },
        },
        "model": {"mode": "pinned", "id": "example-model"},
        "env": [],
        "evaluation": {
            "golden_cases": [
                {
                    "id": "normal",
                    "kind": "normal",
                    "expect": "result",
                    "input": {"request": "valid"},
                },
                {
                    "id": "missing-input",
                    "kind": "missing_input",
                    "expect": "refusal",
                    "input": {},
                },
                {
                    "id": "refusal",
                    "kind": "refusal",
                    "expect": "refusal",
                    "input": {"request": "out of scope"},
                },
                {
                    "id": "malformed",
                    "kind": "malformed",
                    "expect": "refusal",
                    "input": {"request": 7},
                },
            ]
        },
        "state": {"mode": "none", "scope": "turn"},
    }
    if target == "thclaws-standalone":
        spec["package_pattern"] = package_pattern
    else:
        spec["routing"] = {"role": name, "tags": ["read-only"]}
    return spec


def assert_valid(spec: dict[str, Any], validator: Draft202012Validator) -> None:
    errors = all_errors(spec, validator)
    if errors:
        raise AssertionError("expected valid spec, got: " + "; ".join(message for _, message in errors))


def assert_invalid(
    spec: dict[str, Any],
    validator: Draft202012Validator,
    expected_path: tuple[PathPart, ...],
    expected_message: str,
) -> None:
    errors = all_errors(spec, validator)
    if not any(path == expected_path and expected_message in message for path, message in errors):
        formatted = [f"{path}: {message}" for path, message in errors]
        raise AssertionError(f"expected {expected_path} to contain {expected_message!r}, got: {formatted}")


def assert_error_path(
    spec: dict[str, Any], validator: Draft202012Validator, expected_path: tuple[PathPart, ...]
) -> None:
    if not any(path == expected_path for path, _ in all_errors(spec, validator)):
        formatted = [f"{path}: {message}" for path, message in all_errors(spec, validator)]
        raise AssertionError(f"expected an error at {expected_path}, got: {formatted}")


def main() -> int:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, SchemaError) as error:
        print(f"M1 FAIL: unable to load AgentSpec schema: {error}")
        return 1
    validator = Draft202012Validator(schema)

    invoice = base_spec("invoice-reviewer", "atlas-worker", "atlas_file_handoff")
    sql = base_spec("sql-reader", "atlas-worker", "prompt_json", "sql-readonly")
    standalone = base_spec("standalone-agent", "thclaws-standalone", "local_workspace")
    atlas_flow = base_spec("workflow-reviewer", "atlas-workflow", "prompt_json")
    readonly_snapshot = base_spec("archive-reader", "atlas-worker", "local_workspace")
    readonly_snapshot["outputs"] = [
        {
            "name": "reference-files",
            "transport": "collect_files",
            "files": {"globs": ["reference/*.md"]},
        }
    ]
    standalone_session = copy.deepcopy(standalone)
    standalone_session["state"] = {"mode": "session", "scope": "session"}
    t1 = base_spec("output-writer", "thclaws-standalone", "local_workspace")
    t1["permissions"] = {
        "tier": "T1",
        "tools": ["Write"],
        "shell": "none",
        "network": "none",
        "write_scope": "output",
    }
    networked_t2 = copy.deepcopy(atlas_flow)
    networked_t2["permissions"] = {
        "tier": "T2",
        "tools": ["Bash", "WebFetch"],
        "shell": "sandboxed",
        "network": "general-http",
        "write_scope": "workspace",
    }
    assert_valid(invoice, validator)
    assert_valid(sql, validator)
    assert_valid(standalone, validator)
    assert_valid(atlas_flow, validator)
    assert_valid(readonly_snapshot, validator)
    assert_valid(standalone_session, validator)
    assert_valid(t1, validator)
    assert_valid(networked_t2, validator)

    unknown_transport = copy.deepcopy(invoice)
    unknown_transport["inputs"][0]["transport"] = "socket"
    assert_invalid(unknown_transport, validator, ("inputs", 0, "transport"), "is not one of")

    mutating_t0 = copy.deepcopy(invoice)
    mutating_t0["capabilities"] = [{"pack": "publisher-email-sftp", "params": {}}]
    assert_invalid(mutating_t0, validator, ("capabilities", 0, "pack"), "mutating pack requires T2")

    mutating_t1 = copy.deepcopy(t1)
    mutating_t1["capabilities"] = [{"pack": "publisher-email-sftp", "params": {}}]
    assert_invalid(mutating_t1, validator, ("capabilities", 0, "pack"), "mutating pack requires T2")

    atlas_state = copy.deepcopy(invoice)
    atlas_state["state"] = {"mode": "session", "scope": "session"}
    assert_invalid(
        atlas_state,
        validator,
        ("state", "mode"),
        "Atlas targets do not support stateful execution",
    )

    missing_pattern = copy.deepcopy(standalone)
    del missing_pattern["package_pattern"]
    assert_invalid(missing_pattern, validator, (), "'package_pattern' is a required property")

    atlas_with_pattern = copy.deepcopy(invoice)
    atlas_with_pattern["package_pattern"] = "static-pipeline"
    assert_error_path(atlas_with_pattern, validator, ())

    standalone_with_routing = copy.deepcopy(standalone)
    standalone_with_routing["routing"] = {"role": "standalone-agent", "tags": []}
    assert_error_path(standalone_with_routing, validator, ())

    policy_model = copy.deepcopy(invoice)
    policy_model["model"] = {"mode": "policy", "policy": "default"}
    assert_invalid(policy_model, validator, ("model", "mode"), "Atlas targets require a pinned model")

    duplicate_outputs = copy.deepcopy(invoice)
    duplicate_outputs["outputs"].append(copy.deepcopy(duplicate_outputs["outputs"][0]))
    assert_invalid(duplicate_outputs, validator, ("outputs",), "at most one output artifact")

    duplicate_inputs = copy.deepcopy(standalone)
    duplicate_inputs["inputs"].append(copy.deepcopy(duplicate_inputs["inputs"][0]))
    assert_invalid(duplicate_inputs, validator, ("inputs", 1, "name"), "duplicate input name")

    duplicate_cases = copy.deepcopy(invoice)
    duplicate_cases["evaluation"]["golden_cases"][1]["id"] = "normal"
    assert_invalid(
        duplicate_cases,
        validator,
        ("evaluation", "golden_cases", 1, "id"),
        "duplicate golden case id",
    )

    missing_expect = copy.deepcopy(invoice)
    del missing_expect["evaluation"]["golden_cases"][0]["expect"]
    assert_error_path(missing_expect, validator, ("evaluation", "golden_cases", 0))

    normal_refusal = copy.deepcopy(invoice)
    normal_refusal["evaluation"]["golden_cases"][0]["expect"] = "refusal"
    assert_error_path(normal_refusal, validator, ("evaluation", "golden_cases", 0, "expect"))

    refusal_result = copy.deepcopy(invoice)
    refusal_result["evaluation"]["golden_cases"][2]["expect"] = "result"
    assert_error_path(refusal_result, validator, ("evaluation", "golden_cases", 2, "expect"))

    invalid_expect = copy.deepcopy(invoice)
    invalid_expect["evaluation"]["golden_cases"][3]["expect"] = "other"
    assert_error_path(invalid_expect, validator, ("evaluation", "golden_cases", 3, "expect"))

    missing_result = copy.deepcopy(invoice)
    missing_result["evaluation"]["golden_cases"][1]["expect"] = "result"
    assert_valid(missing_result, validator)

    malformed_result = copy.deepcopy(invoice)
    malformed_result["evaluation"]["golden_cases"][3]["expect"] = "result"
    assert_valid(malformed_result, validator)

    bad_embedded_schema = copy.deepcopy(invoice)
    bad_embedded_schema["outputs"][0]["schema"] = {"type": "objct"}
    assert_invalid(bad_embedded_schema, validator, ("outputs", 0, "schema"), "invalid embedded JSON Schema")

    incomplete_cases = copy.deepcopy(invoice)
    incomplete_cases["evaluation"]["golden_cases"] = [incomplete_cases["evaluation"]["golden_cases"][0]] * 4
    assert_invalid(
        incomplete_cases,
        validator,
        ("evaluation", "golden_cases"),
        "missing golden case kind: malformed",
    )

    refusal_schema = copy.deepcopy(invoice)
    refusal_schema["refusal"]["schema"] = {"type": "objct"}
    assert_invalid(
        refusal_schema,
        validator,
        ("refusal", "schema"),
        "invalid embedded JSON Schema",
    )

    atlas_file_path = copy.deepcopy(invoice)
    atlas_file_path["inputs"][0]["path"] = "inputs/request.json"
    assert_error_path(atlas_file_path, validator, ("inputs", 0))

    traversal = copy.deepcopy(readonly_snapshot)
    traversal["outputs"][0]["files"]["globs"] = [r"..\secrets\*.txt"]
    assert_invalid(
        traversal,
        validator,
        ("outputs", 0, "files", "globs", 0),
        "file glob must be workspace-relative",
    )

    t0_bash = copy.deepcopy(invoice)
    t0_bash["permissions"]["tools"] = ["Bash"]
    assert_invalid(
        t0_bash,
        validator,
        ("permissions", "tools", 0),
        "shell=none cannot declare shell tool",
    )

    t0_write = copy.deepcopy(invoice)
    t0_write["permissions"]["tools"] = ["Write"]
    assert_invalid(
        t0_write,
        validator,
        ("permissions", "tools", 0),
        "write_scope=none cannot declare write tool",
    )

    t1_bash = copy.deepcopy(t1)
    t1_bash["permissions"]["tools"] = ["Bash"]
    assert_invalid(
        t1_bash,
        validator,
        ("permissions", "tools", 0),
        "shell=none cannot declare shell tool",
    )

    for network_tool in sorted(NETWORK_BUILTIN_TOOLS):
        network_none = copy.deepcopy(invoice)
        network_none["permissions"]["tools"] = [network_tool]
        assert_invalid(
            network_none,
            validator,
            ("permissions", "tools", 0),
            "network=none cannot declare network tool",
        )

    t0_bad_shell = copy.deepcopy(invoice)
    t0_bad_shell["permissions"]["shell"] = "sandboxed"
    assert_error_path(t0_bad_shell, validator, ("permissions", "shell"))

    t0_bad_network = copy.deepcopy(invoice)
    t0_bad_network["permissions"]["network"] = "general-http"
    assert_error_path(t0_bad_network, validator, ("permissions", "network"))

    t0_bad_scope = copy.deepcopy(invoice)
    t0_bad_scope["permissions"]["write_scope"] = "output"
    assert_error_path(t0_bad_scope, validator, ("permissions", "write_scope"))

    t1_bad_scope = copy.deepcopy(t1)
    t1_bad_scope["permissions"]["write_scope"] = "workspace"
    assert_error_path(t1_bad_scope, validator, ("permissions", "write_scope"))

    t2_standalone = copy.deepcopy(standalone)
    t2_standalone["permissions"] = {
        "tier": "T2",
        "tools": ["Read"],
        "shell": "sandboxed",
        "network": "general-http",
        "write_scope": "workspace",
    }
    assert_invalid(
        t2_standalone,
        validator,
        ("permissions", "tier"),
        "T2 requires atlas-workflow with a human_gate",
    )

    durable_memory = copy.deepcopy(standalone)
    durable_memory["state"] = {"mode": "durable_memory", "scope": "workspace"}
    assert_invalid(
        durable_memory,
        validator,
        ("state", "mode"),
        "durable_memory is deferred beyond M1",
    )

    bounded_version = copy.deepcopy(invoice)
    bounded_version["identity"]["thclaws_min_version"] = ">=0.116.0,<0.120.0"
    assert_valid(bounded_version, validator)

    incompatible_version = copy.deepcopy(invoice)
    incompatible_version["identity"]["thclaws_min_version"] = ">=0.120.0,<0.116.0"
    assert_invalid(
        incompatible_version,
        validator,
        ("identity", "thclaws_min_version"),
        "version constraint is unsatisfiable",
    )

    conflicting_bare_versions = copy.deepcopy(invoice)
    conflicting_bare_versions["identity"]["thclaws_min_version"] = "0.116.0,0.117.0"
    assert_invalid(
        conflicting_bare_versions,
        validator,
        ("identity", "thclaws_min_version"),
        "bare version cannot be combined",
    )

    upper_only_version = copy.deepcopy(invoice)
    upper_only_version["identity"]["thclaws_min_version"] = "<0.120.0"
    assert_invalid(
        upper_only_version,
        validator,
        ("identity", "thclaws_min_version"),
        "requires a minimum or exact version",
    )

    unsupported_version = copy.deepcopy(invoice)
    unsupported_version["identity"]["thclaws_min_version"] = "~0.116.0"
    assert_invalid(
        unsupported_version,
        validator,
        ("identity", "thclaws_min_version"),
        "does not match",
    )

    state_scope = copy.deepcopy(standalone)
    state_scope["state"] = {"mode": "session", "scope": "turn"}
    assert_error_path(state_scope, validator, ("state", "scope"))

    # Tool names are what the runtime presents: built-ins or <server>__<tool> for MCP.
    qualified = copy.deepcopy(sql)
    qualified["permissions"]["tools"] = ["Read", "sql-readonly__query"]
    assert_valid(qualified, validator)
    dotted = copy.deepcopy(sql)
    dotted["permissions"]["tools"] = ["sql.query"]
    assert_invalid(dotted, validator, ("permissions", "tools", 0), "does not match")
    for bad_name in ["Sql__Query", "sql_readonly__query", "sql-readonly__a__b", "__query", "sql-readonly__"]:
        malformed_mcp = copy.deepcopy(sql)
        malformed_mcp["permissions"]["tools"] = [bad_name]
        assert_invalid(malformed_mcp, validator, ("permissions", "tools", 0), "MCP tool must be <server>__<tool>")

    print("M1 OK: AgentSpec schema and semantic checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
