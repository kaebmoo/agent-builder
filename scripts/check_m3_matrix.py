#!/usr/bin/env python3
"""M3 gate: DESIGN §3 combinations, AgentSpec agreement and fixture resolution."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import yaml
    from jsonschema import Draft202012Validator

    from forge.compat import MATRIX_PATH, resolve_compatibility
except ModuleNotFoundError:
    print("M3 FAIL: install project dependencies first (jsonschema>=4.20, pyyaml>=6.0)")
    raise SystemExit(1)

from check_m1_spec import all_errors, base_spec

# Independent expectations from DESIGN §3, so a changed YAML row cannot bless itself.
EXPECTED = {
    "thclaws-standalone": {
        "execution_surface": "thclaws-gui-cli-catalog",
        "allowed_patterns": ["static-pipeline", "batch-fanout", "dynamic"],
        "flow_location": ".thclaws/agent_workflow/run.js",
    },
    "atlas-worker": {
        "execution_surface": "POST /agent/run",
        "allowed_patterns": ["single-worker"],
        "flow_location": None,
    },
    "atlas-workflow": {
        "execution_surface": "atlas-workflow-engine",
        "allowed_patterns": ["single-worker"],
        "flow_location": "atlas-workflow-json",
    },
}


def check() -> None:
    matrix = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    assert matrix == EXPECTED, "matrix differs from DESIGN §3"
    schema = json.loads((ROOT / "agentspec.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    assert set(schema["properties"]["target"]["enum"]) == set(matrix), "schema target drift"
    assert schema["properties"]["package_pattern"]["enum"] == matrix["thclaws-standalone"]["allowed_patterns"], (
        "schema pattern drift"
    )

    def rejected(spec: object, reason: str) -> None:
        try:
            resolve_compatibility(spec)
        except (ValueError, TypeError) as error:
            assert reason in str(error), f"unexpected rejection: {error}"
        else:
            raise AssertionError(f"accepted incompatible spec: {spec!r}")

    # Missing, every known package/flow pattern, unknown values, and wrong types.
    patterns = [
        "static-pipeline", "batch-fanout", "dynamic", "single-worker",
        "human-approval", "manager-loop", "unknown", "", None, [], {},
    ]
    for target, entry in EXPECTED.items():
        base = base_spec("matrix-check", target, "prompt_json")
        base.pop("package_pattern", None)
        for present, pattern in [(False, None), *((True, item) for item in patterns)]:
            spec = copy.deepcopy(base)
            if present:
                spec["package_pattern"] = pattern
            valid = (
                present and pattern in entry["allowed_patterns"]
                if target == "thclaws-standalone" else not present
            )
            assert (not all_errors(spec, validator)) == valid, f"M1 disagreement: {target}/{pattern!r}"
            if not valid:
                rejected(spec, "package_pattern")
                continue
            before = copy.deepcopy(spec)
            expected = {"target": target, **entry, "package_pattern": pattern if present else "single-worker"}
            result = resolve_compatibility(spec)
            assert result == expected, f"wrong resolution: {target}/{pattern!r}"
            assert resolve_compatibility(spec) == result, "resolution is not deterministic"
            assert spec == before, "resolver mutated input"
            result["allowed_patterns"].clear()
            assert resolve_compatibility(spec) == expected, "result mutation leaks into later calls"

        routed = copy.deepcopy(base)
        if target == "thclaws-standalone":
            routed.update(package_pattern="static-pipeline", routing={"role": "worker", "tags": []})
        else:
            del routed["routing"]
        rejected(routed, "routing")
        assert all_errors(routed, validator), "M1 accepted incompatible routing"
        overridden = base_spec("matrix-check", target, "prompt_json")
        overridden["execution_surface"] = entry["execution_surface"]
        rejected(overridden, "execution_surface")
        assert all_errors(overridden, validator), "M1 accepted execution_surface input"

    for target in ["unknown", "", "single-worker", "human-approval", "manager-loop", None, [], {}, 42]:
        rejected({"target": target}, "unsupported target")
    rejected({}, "unsupported target")
    for spec in [None, [], "atlas-worker", 42]:
        rejected(spec, "AgentSpec must be an object")

    for name in ["invoice-reviewer", "sql-reader"]:
        spec = yaml.safe_load((ROOT / "fixtures" / name / "spec.yaml").read_text(encoding="utf-8"))
        assert not all_errors(spec, validator), f"fixture fails M1: {name}"
        assert resolve_compatibility(spec) == {
            "target": "atlas-worker", **EXPECTED["atlas-worker"], "package_pattern": "single-worker",
        }, f"wrong fixture resolution: {name}"


def main() -> int:
    try:
        check()
    except (AssertionError, OSError, UnicodeError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        print(f"M3 FAIL: {error}")
        return 1
    print("M3 OK: DESIGN §3 matrix, rejected combinations, M1 agreement and both fixtures passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
