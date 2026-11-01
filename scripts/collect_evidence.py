#!/usr/bin/env python3
"""Run every scripts/check_m*.py and write one redacted markdown summary under docs/evidence/.

File name: <YYYY-MM-DD>-<thclaws revision | nobinary>.md. The body carries no timestamp, so two runs
with identical gate output produce identical bytes. Existing files are never overwritten without --force;
--out must resolve inside the repository.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = [re.compile(p) for p in (r"sk-[A-Za-z0-9_-]+", r"Bearer \S+", r"[A-Z_]*TOKEN=\S+", r"[A-Z_]*KEY=\S+")]
HEADER = "| gate | exit | first line | last line |"
RULE = "|---|---|---|---|"


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def cell(line: str) -> str:
    return redact(line.strip()).replace("|", "\\|")


def thclaws_version() -> tuple[str, str]:
    """Return (version line for the report, revision for the file name)."""
    binary = shutil.which(os.environ.get("THCLAWS_BIN", "thclaws"))
    if binary is None:
        return "no binary", "nobinary"
    env = {**os.environ, "THCLAWS_DISABLE_KEYCHAIN": "1"}
    out = subprocess.run([binary, "--version"], check=False, capture_output=True, text=True, env=env).stdout
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    match = re.search(r"revision:\s*([0-9a-f]+)", out)
    revision = match.group(1) if match else "unknown"
    # ponytail: first two lines only; the "built:" line is a timestamp and would break determinism
    return " / ".join(f"`{ln}`" for ln in lines[:2]) or "no binary", revision


def run_gate(script: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(script)], check=False, cwd=ROOT, text=True, errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return proc.returncode, (lines[0] if lines else ""), (lines[-1] if lines else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "evidence")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file for today")
    args = parser.parse_args()

    root = os.path.realpath(ROOT) + os.sep
    out = os.path.realpath(args.out)
    if not out.startswith(root):
        print(f"--out must stay inside the repository ({ROOT}): {args.out}")
        return 1
    out_dir = Path(out)

    version_line, revision = thclaws_version()
    target = out_dir / f"{dt.date.today().isoformat()}-{revision}.md"
    if target.exists() and not args.force:
        print(f"refusing to overwrite {target} (use --force)")
        return 1

    rows = []
    for script in sorted(ROOT.glob("scripts/check_m*.py")):
        print(f"== {script.name}", flush=True)
        code, first, last = run_gate(script)
        rows.append(f"| {script.name} | {code} | {cell(first)} | {cell(last)} |")

    body = "\n".join(["# Milestone gate evidence", "", f"thclaws: {version_line}", "", HEADER, RULE, *rows, ""])
    out_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(redact(body), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
