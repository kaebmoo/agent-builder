#!/usr/bin/env python3
"""M23 gate: committed gate evidence under docs/evidence/ is parseable and carries no secret.

Exit 0 = every file parses as the collect_evidence.py table and matches no secret pattern;
1 = a file fails either check; 2 = docs/evidence/ holds no file yet (run scripts/collect_evidence.py).
Offline; needs no thClaws binary or provider key.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True

from collect_evidence import HEADER, RULE, SECRET_PATTERNS

GATE_CELL = re.compile(r"^\| check_m\w+\.py$")


def row_ok(line: str) -> bool:
    """Four cells; the collector escapes `|` inside a cell as `\\|`, so ` | ` only separates cells."""
    cells = line.split(" | ")
    return len(cells) == 4 and bool(GATE_CELL.match(cells[0])) and cells[1] in {"0", "1", "2"} and cells[3].endswith(" |")


def problems(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    found = [f"secret pattern {p.pattern!r}" for p in SECRET_PATTERNS if p.search(text)]
    lines = text.splitlines()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-\w+\.md", path.name):
        found.append("file name is not <date>-<revision>.md")
    if not any(ln.startswith("thclaws: ") for ln in lines):
        found.append("missing 'thclaws:' line")
    if HEADER not in lines or RULE not in lines:
        found.append("missing table header")
    rows = [ln for ln in lines if ln.startswith("| check_m")]
    if not rows:
        found.append("no gate rows")
    found.extend(f"bad row: {ln[:60]}" for ln in rows if not row_ok(ln))
    return found


def main() -> int:
    files = sorted((ROOT / "docs" / "evidence").glob("*.md"))
    if not files:
        print("M23 SKIP: docs/evidence/ has no file; run scripts/collect_evidence.py and commit the result")
        return 2
    failed = False
    for path in files:
        issues = problems(path)
        print(f"{'FAIL' if issues else 'ok  '} {path.relative_to(ROOT)}" + "".join(f"\n  - {i}" for i in issues))
        failed |= bool(issues)
    print("M23 FAIL" if failed else f"M23 PASS: {len(files)} evidence file(s) parseable, no secret pattern")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
