"""Resolve DESIGN §3 compatibility; callers still need full AgentSpec validation."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

MATRIX_PATH = Path(__file__).resolve().parents[1] / "patterns" / "matrix.yaml"


def resolve_compatibility(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Derive surface, selected/allowed patterns and flow location from an AgentSpec.

    Reject incompatible target/pattern/routing declarations with ValueError.
    A non-object spec raises TypeError.
    This does not validate manifests, permissions or runtime compatibility.
    """
    if not isinstance(spec, Mapping):
        raise TypeError("AgentSpec must be an object")
    matrix = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    target = spec.get("target")
    if not isinstance(target, str) or target not in matrix:
        raise ValueError(f"unsupported target: {target!r}")
    if "execution_surface" in spec:
        raise ValueError("execution_surface is derived from target; do not declare it in AgentSpec")

    entry = matrix[target]
    if target == "thclaws-standalone":
        if "routing" in spec:
            raise ValueError("thclaws-standalone must not declare routing")
        pattern = spec.get("package_pattern")
        if pattern not in entry["allowed_patterns"]:
            raise ValueError(
                f"{target} requires package_pattern from {entry['allowed_patterns']}; got {pattern!r}"
            )
    else:
        if "package_pattern" in spec:
            raise ValueError(f"{target} must not declare package_pattern; single-worker is derived")
        if "routing" not in spec:
            raise ValueError(f"{target} requires routing")
        pattern = entry["allowed_patterns"][0]

    return {"target": target, **entry, "package_pattern": pattern}
