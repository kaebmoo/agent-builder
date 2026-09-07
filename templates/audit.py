#!/usr/bin/env python3
"""Thin runner: static audit of this package. Verifies only; never edits files.

Every rule lives in agent-builder (forge.audit). Exit 0 = draft, 1 = findings, 2 = thclaws unavailable.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from forge.audit import main
except ModuleNotFoundError:
    sys.exit("audit.py: install agent-builder (pip install -e <agent-builder checkout>) so forge.audit imports")

sys.exit(main([str(Path(__file__).resolve().parent), *sys.argv[1:]]))
