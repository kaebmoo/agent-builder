"""forge next: read builder-build-report.json and print the one command to run next (M24).

Derived from DESIGN §7: unverified → draft (static audit + thclaws agent validate) → candidate (live audit)
→ shippable (security audit in the same run on a verified runtime). Reads only; never changes the report,
never raises the status and never calls the words deploy-ready: deployment_verified stays an operator step.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from forge.generate import THCLAWS_BASELINE

ATLAS_TARGETS = ("atlas-worker", "atlas-workflow")
AUDIT_KEYS = ("spec", "manifest", "static", "live", "security")


def _deploy(target: str, pkg: str) -> str:
    if target in ATLAS_TARGETS:
        return f"forge export {pkg} --out {pkg}-export --base-url http://<daemon-host>:4317 --workspace-dir /srv/atlas/<name>"
    return f"thclaws agent run {pkg} --workflow .thclaws/agent_workflow/run.js --args '<input envelope JSON>'"


def _deploy_why(target: str, status: str) -> str:
    if target in ATLAS_TARGETS:
        return (f"status {status}; registration follows docs/deploy-runbook.md §1–10 and the operator ticks "
                "deployment_verified there, never the builder")
    return (f"status {status}; run it from your script or cron, or install it as a thClaws bot "
            "(DESIGN §4 \"Bot channel\": audit the source package, not the installed tree)")


def decide(report: dict[str, Any], spec: dict[str, Any], pkg: str) -> tuple[str, str]:
    """Return (next command, why) from the recorded audit state only."""
    audit, status, target = report["audit"], report["package_status"], report["target"]
    static = report.get("static_audit", {})
    validate = static.get("thclaws_validate", {})
    audit_cmd = f"forge audit {pkg} --write"
    live_cmd = f"forge audit {pkg} --live --security --write"
    if audit["spec"] == "failed":
        return "forge generate <spec> --out <new dir>", "agentspec.json fails its schema; fix the spec and generate again"
    if audit["static"] == "failed":
        errors = "; ".join(f"{f['rule']}: {f['message']}" for f in static.get("findings", [])
                           if f["severity"] == "error")
        return audit_cmd, f"static audit failed ({errors}); fix the package source, then audit again"
    if audit["static"] == "not_run":
        return audit_cmd, (f"static audit and thclaws agent validate have not run; status stays unverified until "
                           f"both pass (needs thclaws {THCLAWS_BASELINE} on PATH or THCLAWS_BIN)")
    if audit["manifest"] == "skipped":
        return audit_cmd, (f"thclaws agent validate was skipped ({validate.get('detail')}); put thclaws "
                           f"{THCLAWS_BASELINE} on PATH, or pass --allow-unverified-runtime and stop at candidate")
    if audit["manifest"] == "failed":
        return audit_cmd, f"thclaws agent validate failed ({validate.get('detail')}); fix the package, then audit again"
    live_applicable = (len(spec["outputs"]) == 1 and spec["outputs"][0]["transport"] == "assistant_json"
                       and spec["model"]["mode"] == "pinned")
    if not live_applicable:
        return _deploy(target, pkg), ("live audit needs one sole assistant_json output and a pinned model, so this "
                                      "package cannot exceed draft; " + _deploy_why(target, status))
    if audit["live"] == "failed":
        detail = report.get("live_audit", {}).get("detail") or "see live_audit in the report"
        return live_cmd, f"live audit failed ({detail}); fix the agent or its golden cases, then run it again"
    if audit["live"] != "passed":
        return live_cmd, ("live audit has not passed; it needs a provider key in OPENAI_API_KEY (or "
                          "--provider-key-env) and thclaws; status stays draft until the golden cases pass")
    if validate.get("runtime") == "unverified":
        return _deploy(target, pkg), (f"status is capped at candidate: thclaws agent validate ran on unverified "
                                      f"runtime {validate.get('version')} (baseline {THCLAWS_BASELINE}); rerun "
                                      f"{live_cmd} on the baseline for shippable, or " + _deploy_why(target, status))
    if status != "shippable":
        detail = report.get("security_audit", {}).get("detail") or "security audit has not run"
        return live_cmd, f"{detail}; live and security must pass in the same run to reach shippable"
    return _deploy(target, pkg), _deploy_why(target, status)


def next_step(package: Path) -> dict[str, str]:
    """Return status, target, audit summary, next and why; ValueError when the package has no readable report."""
    try:
        report = json.loads((package / "builder-build-report.json").read_text(encoding="utf-8"))
        spec = json.loads((package / "agentspec.json").read_text(encoding="utf-8"))
        audit = " ".join(f"{key}={report['audit'][key]}" for key in AUDIT_KEYS)
        next_cmd, why = decide(report, spec, str(package))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{package} is not a generated package ({type(exc).__name__}: {exc})") from exc
    return {"status": report["package_status"], "target": report["target"], "audit": audit,
            "next": next_cmd, "why": why}


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("package", type=Path, help="generated package directory")


def command(args: argparse.Namespace) -> int:
    try:
        step = next_step(args.package)
    except ValueError as exc:
        print(f"next FAIL: {exc}")
        return 1
    print(f"{args.package}: package_status={step['status']} target={step['target']}")
    print(f"audit: {step['audit']}")
    print(f"next: {step['next']}")
    print(f"why: {step['why']}")
    return 0
