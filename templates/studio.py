#!/usr/bin/env python3
"""Thin runner: audit this package and record the verdict in builder-build-report.json.

Same rules as audit.py (forge.audit); the only difference is --write. Use it after customizing
AGENTS.md or SKILL.md so the report reflects the package as it is now.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from forge.audit import main
except ModuleNotFoundError:
    sys.exit("studio.py: install agent-builder (pip install -e <agent-builder checkout>) so forge.audit imports")

sys.exit(main([str(Path(__file__).resolve().parent), "--write", *sys.argv[1:]]))
