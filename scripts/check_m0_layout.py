#!/usr/bin/env python3
"""M0 gate: required files and folders exist. Fails loudly if the skeleton drifts."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = [
    "README.md",
    "AGENTS.md",
    "pyproject.toml",
    ".gitignore",
    "docs/DESIGN.md",
    "docs/PLAN.md",
    "forge/__init__.py",
]
REQUIRED_DIRS = ["patterns", "templates", "packs", "fixtures", "tests", "scripts"]
REQUIRED_DESIGN_SECTIONS = [
    "## 2. AgentSpec",
    "## 3. Target / pattern compatibility matrix",
    "## 6. Guarantee matrix",
    "## 7. Audit",
]


def main() -> int:
    problems: list[str] = []
    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            problems.append(f"missing file: {rel}")
    for rel in REQUIRED_DIRS:
        if not (ROOT / rel).is_dir():
            problems.append(f"missing dir: {rel}")
    design = ROOT / "docs" / "DESIGN.md"
    if design.is_file():
        text = design.read_text(encoding="utf-8")
        for section in REQUIRED_DESIGN_SECTIONS:
            if section not in text:
                problems.append(f"DESIGN.md missing section: {section}")
    if problems:
        print("M0 FAIL")
        for p in problems:
            print("  -", p)
        return 1
    print("M0 OK: layout complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
