#!/usr/bin/env python3
"""M24 gate: forge next prints one next command per recorded audit state and never raises a claim.

Exit 0 = pass, 1 = fail. Offline only: it reads builder-build-report.json states, so it needs no thclaws
binary, provider key or network and never skips (exit 2 is not used).
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from forge.generate import THCLAWS_BASELINE, generate
from forge.next_step import decide, next_step
from forge.spec import load_spec

FORBIDDEN = ("deploy-ready", "read-only package", "production-ready")
DOCS = {
    "docs/DESIGN.md": ["`forge next <package>` (M24"],
    "docs/PLAN.md": ["| M24 |", "check_m24_forge_next.py"],
    "README.md": ["M24"],
    "docs/getting-started.md": ["forge next"],
    "docs/getting-started.en.md": ["forge next"],
}


def state(report: dict, **audit: str) -> dict:
    """Copy of a fresh report with audit fields and the matching static/live/security blocks set."""
    copy_ = copy.deepcopy(report)
    copy_["audit"].update(audit)
    ran = copy_["audit"]["static"] != "not_run"
    if ran:
        copy_["static_audit"] = {"strict": True, "findings": [], "thclaws_validate": {
            "status": copy_["audit"]["manifest"], "version": f"thclaws {THCLAWS_BASELINE}", "detail": "ok"}}
    passed = ran and copy_["audit"]["static"] == "passed" and copy_["audit"]["manifest"] == "passed"
    copy_["package_status"] = "draft" if passed else "unverified"
    if copy_["audit"]["live"] == "passed":
        copy_["package_status"] = "candidate"
    if copy_["audit"]["security"] == "passed" and copy_["package_status"] == "candidate":
        copy_["package_status"] = "shippable"
    return copy_


def cases(report: dict, spec: dict, pkg: str) -> list[tuple[str, dict, dict, str, str]]:
    audit_cmd, live_cmd = f"forge audit {pkg} --write", f"forge audit {pkg} --live --security --write"
    deploy = "forge export" if report["target"].startswith("atlas") else "thclaws agent run"
    failed_static = state(report, static="failed", manifest="passed")
    failed_static["static_audit"]["findings"] = [{"rule": "packs", "severity": "error", "message": "pack x stale"}]
    skipped = state(report, static="passed", manifest="skipped")
    skipped["static_audit"]["thclaws_validate"]["detail"] = "thclaws is not on PATH"
    unverified = state(report, static="passed", manifest="passed", live="passed", security="passed")
    unverified["package_status"] = "candidate"
    unverified["static_audit"]["thclaws_validate"].update(runtime="unverified", version="thclaws 0.150.0")
    unpinned = copy.deepcopy(spec)
    unpinned["model"]["mode"] = "host"
    draft = state(report, static="passed", manifest="passed")
    live_failed = state(report, static="passed", manifest="passed", live="failed")
    live_failed["live_audit"] = {"detail": "case normal mismatched"}
    candidate = state(report, static="passed", manifest="passed", live="passed")
    candidate["security_audit"] = {"detail": "security checks passed; shippable requires live evidence in the same run"}
    return [
        ("fresh", report, spec, audit_cmd, "have not run"),
        ("spec failed", state(report, spec="failed"), spec, "forge generate", "fails its schema"),
        ("static failed", failed_static, spec, audit_cmd, "packs: pack x stale"),
        ("manifest skipped", skipped, spec, audit_cmd, f"put thclaws {THCLAWS_BASELINE} on PATH"),
        ("manifest failed", state(report, static="passed", manifest="failed"), spec, audit_cmd, "validate failed"),
        ("draft", draft, spec, live_cmd, "OPENAI_API_KEY"),
        ("draft, live not applicable", draft, unpinned, deploy, "cannot exceed draft"),
        ("live failed", live_failed, spec, live_cmd, "case normal mismatched"),
        ("candidate without security", candidate, spec, live_cmd, "same run"),
        ("candidate on unverified runtime", unverified, spec, deploy, "capped at candidate"),
        ("shippable", state(report, static="passed", manifest="passed", live="passed", security="passed"),
         spec, deploy, "deployment_verified" if deploy == "forge export" else "Bot channel"),
    ]


def check_package(fixture: str, out: Path) -> int:
    package = out / fixture
    generate(load_spec(ROOT / "fixtures" / fixture / "spec.yaml"), package)
    report = json.loads((package / "builder-build-report.json").read_text(encoding="utf-8"))
    spec = json.loads((package / "agentspec.json").read_text(encoding="utf-8"))
    pkg = str(package)
    for name, state_, spec_, cmd, why in cases(report, spec, pkg):
        got_cmd, got_why = decide(state_, spec_, pkg)
        assert got_cmd.startswith(cmd), f"{fixture} {name}: next {got_cmd!r} should start with {cmd!r}"
        assert why in got_why, f"{fixture} {name}: why {got_why!r} lacks {why!r}"
        text = (got_cmd + got_why).lower()
        assert not any(word in text for word in FORBIDDEN), f"{fixture} {name}: forbidden claim in {text!r}"
    step = next_step(package)
    assert step["status"] == "unverified" and step["next"] == f"forge audit {pkg} --write", step
    runs = [subprocess.run([sys.executable, "-m", "forge.cli", "next", pkg], cwd=ROOT, capture_output=True,
                           text=True, check=False) for _ in range(2)]
    assert runs[0].returncode == 0 and runs[0].stdout == runs[1].stdout, runs[0]
    assert runs[0].stdout.splitlines()[2] == f"next: forge audit {pkg} --write", runs[0].stdout
    return len(cases(report, spec, pkg))


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="forge-m24-") as tmp:
            out = Path(tmp)
            total = sum(check_package(fixture, out) for fixture in ("text-reviewer-standalone", "sql-reader"))
            bad = subprocess.run([sys.executable, "-m", "forge.cli", "next", str(out / "missing")], cwd=ROOT,
                                 capture_output=True, text=True, check=False)
            if bad.returncode != 1 or not bad.stdout.startswith("next FAIL:"):
                raise ValueError(f"non-package should fail with exit 1: {bad}")
        for path, phrases in DOCS.items():
            text = (ROOT / path).read_text(encoding="utf-8")
            missing = [phrase for phrase in phrases if phrase not in text]
            if missing:
                raise ValueError(f"{path} lacks {missing}")
    except (AssertionError, OSError, ValueError, KeyError) as exc:
        print(f"M24 FAIL: {exc}")
        return 1
    print(f"M24 PASS: forge next answers {total} recorded audit states for a standalone and an Atlas package "
          "deterministically, refuses a non-package with exit 1, and the docs describe it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
