#!/usr/bin/env python3
"""M5 gate: static audit verdicts, readable failures, warning/strict split, target-bound rules and wrappers."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import yaml
    from jsonschema import Draft202012Validator

    from forge.audit import audit_package, exit_code, rules_for, write_report
    from forge.generate import REPORT_SCHEMA_PATH, THCLAWS_BASELINE, WRAPPERS, generate, json_text
    from forge.spec import load_spec
except ModuleNotFoundError:
    print("M5 FAIL: install project dependencies from pyproject.toml first")
    raise SystemExit(1)

NOT_RUN = {"live": "not_run", "security": "not_run"}


def snapshot(folder: Path) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def expect(report: dict, rule: str, needle: str, severity: str = "error") -> None:
    findings = report["static_audit"]["findings"]
    assert any(f["rule"] == rule and f["severity"] == severity and needle in f["message"] for f in findings), (
        f"expected {severity} {rule!r} mentioning {needle!r}; got {findings}"
    )


def check(temporary: Path) -> int:
    validator = Draft202012Validator(json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8")))
    spec = load_spec(ROOT / "fixtures/invoice-reviewer/spec.yaml")
    package = temporary / "invoice-reviewer"
    generate(spec, package)
    for wrapper in WRAPPERS:
        text = (package / wrapper).read_text(encoding="utf-8")
        assert "forge.audit" in text and (text == (ROOT / "templates" / wrapper).read_text(encoding="utf-8"))
    assert "--write" in (package / "studio.py").read_text() and "--write" not in (package / "audit.py").read_text()

    # A correct package: no findings, deterministic, and no promotion without thclaws agent validate.
    before = snapshot(package)
    report = audit_package(package, thclaws=False)
    assert snapshot(package) == before, "audit mutated the package"
    validator.validate(report)
    assert report["static_audit"]["findings"] == []
    assert report["audit"] == {"spec": "passed", "manifest": "skipped", "static": "passed", **NOT_RUN}
    assert report["package_status"] == "unverified" and exit_code(report) == 2
    assert "thclaws" in report["static_audit"]["thclaws_validate"]["detail"]
    assert report["generated_files"] == json.loads(before["builder-build-report.json"])["generated_files"]
    write_report(package, report)
    written = snapshot(package)
    assert {path for path in written if written[path] != before[path]} == {"builder-build-report.json"}
    assert json.loads(written["builder-build-report.json"]) == report
    write_report(package, audit_package(package, thclaws=False))
    assert snapshot(package) == written, "audit result is not deterministic"

    # LLM-customizable prose stays auditable as long as the mandatory content survives.
    def customize(folder: Path) -> None:
        for path in ("AGENTS.md", ".thclaws/skills/invoice-reviewer/SKILL.md"):
            target = folder / path
            target.write_text(target.read_text(encoding="utf-8") + "\nOperator note: prefer concise findings.\n")

    def tampered(name: str, mutate: Callable[[Path], None], **kwargs) -> dict:
        target = temporary / name
        shutil.copytree(package, target)
        mutate(target)
        result = audit_package(target, thclaws=False, **kwargs)
        validator.validate(result)
        return result

    assert tampered("customized", customize)["static_audit"]["findings"] == []

    # Missing pack: sql-reader cannot reach draft until M7a ships the descriptor.
    sql = temporary / "sql-reader"
    generate(load_spec(ROOT / "fixtures/sql-reader/spec.yaml"), sql)
    report = audit_package(sql, thclaws=False)
    validator.validate(report)
    expect(report, "packs", "sql-readonly")
    expect(report, "packs", "missing")
    assert report["audit"]["static"] == "failed" and report["package_status"] == "unverified"
    assert exit_code(report) == 1

    # Broken packages fail with a readable reason.
    def drop_refuse(folder: Path) -> None:
        path = folder / "AGENTS.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text[:text.index("## Refuse")] + text[text.index("## Output"):], encoding="utf-8")

    report = tampered("no-refuse", drop_refuse)
    expect(report, "agents_md", "## Refuse")
    for condition in spec["refusal"]["conditions"]:
        expect(report, "refusal", condition)
    expect(report, "refusal", "refusal response")
    assert report["audit"]["static"] == "failed" and report["package_status"] == "unverified"

    def admin_mcp(folder: Path) -> None:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        manifest["requires"]["mcp_servers"] = ["sql-admin"]
        (folder / "manifest.json").write_text(json_text(manifest), encoding="utf-8")

    report = tampered("admin-mcp", admin_mcp)
    expect(report, "drift", "manifest.json")
    expect(report, "drift", "requires.mcp_servers")
    expect(report, "drift", "sql-admin")

    def orchestrate(folder: Path) -> None:
        (folder / ".thclaws/agents/worker.md").write_text("---\nname: worker\n---\n", encoding="utf-8")
        (folder / ".thclaws/agent_workflow").mkdir()
        (folder / ".thclaws/agent_workflow/run.js").write_text("globalThis.__wf_result = {};\n", encoding="utf-8")
        path = folder / "AGENTS.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nCall WorkflowRun for every request.\n")

    report = tampered("orchestrated", orchestrate)
    expect(report, "single_worker", ".thclaws/agents/")
    expect(report, "single_worker", "run.js")
    expect(report, "single_worker", "WorkflowRun")
    assert "single_worker" in rules_for("atlas-worker") and "single_worker" in rules_for("atlas-workflow")
    assert "single_worker" not in rules_for("thclaws-standalone"), "single-worker rules must not bind standalone"
    assert set(rules_for("thclaws-standalone")) < set(rules_for("atlas-worker"))

    def unpin_model(folder: Path) -> None:
        path = folder / "agentspec.json"
        edited = json.loads(path.read_text(encoding="utf-8"))
        edited["model"]["id"] = "other-model"
        path.write_text(json_text(edited), encoding="utf-8")

    expect(tampered("model-drift", unpin_model), "agents_md", "model block")

    def extra_script(folder: Path) -> None:
        (folder / ".thclaws/scripts/extra.py").write_text("print('undeclared')\n", encoding="utf-8")
        (folder / ".thclaws/schemas/refusal.json").unlink()
        (folder / "audit.py").write_text("print('replaced')\n", encoding="utf-8")

    report = tampered("files", extra_script)
    expect(report, "inventory", ".thclaws/scripts/extra.py")
    expect(report, "inventory", ".thclaws/schemas/refusal.json")
    expect(report, "drift", "audit.py")

    def stray_files(folder: Path) -> None:
        (folder / "secrets.env").write_text("TOKEN=example\n", encoding="utf-8")
        (folder / "notes").mkdir()
        (folder / "notes/README.md").write_text("operator notes\n", encoding="utf-8")
        (folder / "link.md").symlink_to(folder / "AGENTS.md")
        (folder / ".thclaws/state").mkdir()
        (folder / ".thclaws/state/session.jsonl").write_text("{}\n", encoding="utf-8")
        (folder / "evaluation/__pycache__").mkdir()
        (folder / "evaluation/__pycache__/cases.pyc").write_bytes(b"\x00")
        (folder / ".DS_Store").write_bytes(b"\x00")

    report = tampered("stray", stray_files)
    expect(report, "inventory", "secrets.env")
    expect(report, "inventory", "notes/README.md")
    expect(report, "inventory", "link.md")
    stray_messages = [f["message"] for f in report["static_audit"]["findings"]]
    assert len(stray_messages) == 3, f"runtime state, bytecode and .DS_Store are not findings: {stray_messages}"

    def corrupt_spec(folder: Path) -> None:
        path = folder / "agentspec.json"
        edited = json.loads(path.read_text(encoding="utf-8"))
        edited["permissions"]["tools"].append("Bash")
        path.write_text(json_text(edited), encoding="utf-8")

    report = tampered("bad-spec", corrupt_spec)
    expect(report, "spec", "shell=none")
    assert report["audit"] == {"spec": "failed", "manifest": "skipped", "static": "failed", **NOT_RUN}
    assert report["package_status"] == "unverified"
    (temporary / "bad-spec/builder-build-report.json").unlink()
    try:
        audit_package(temporary / "bad-spec", thclaws=False)
    except ValueError as error:
        assert "shell=none" in str(error)
    else:
        raise AssertionError("unrenderable spec without a report must fail loudly")

    # Unknown embedded-schema keywords: warning in draft, failure at the shippable bar.
    annotated = copy.deepcopy(spec)
    annotated["outputs"][0]["schema"]["x-note"] = "vendor annotation"
    annotated["outputs"][0]["schema"]["properties"]["findings"]["items"]["x-hint"] = "free text"
    generate(annotated, temporary / "annotated")
    report = audit_package(temporary / "annotated", thclaws=False)
    validator.validate(report)
    expect(report, "schema_vocabulary", "outputs[0].schema.x-note", "warning")
    expect(report, "schema_vocabulary", "outputs[0].schema.properties.findings.items.x-hint", "warning")
    assert all(f["severity"] == "warning" for f in report["static_audit"]["findings"])
    assert report["audit"]["static"] == "passed", "warnings must not block draft"
    strict = audit_package(temporary / "annotated", thclaws=False, strict=True)
    assert strict["audit"]["static"] == "failed" and strict["static_audit"]["strict"] is True
    assert exit_code(strict) == 1

    # Pack rules from the package side: admin MCP behind a T2 pack in a T0 package, and a lost env name.
    packs = temporary / "packs"
    pack = packs / "fixture-pack"
    (pack / "scripts").mkdir(parents=True)
    (pack / "scripts/probe.py").write_text('print("synthetic fixture only")\n', encoding="utf-8")
    metadata = {"min_tier": "T0", "tools": ["Read"], "env": ["EXAMPLE_FIXTURE_TOKEN"],
                "mcp_servers": ["fixture-mcp"], "scripts": ["probe.py"], "skills": []}
    (pack / "pack.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    with_pack = copy.deepcopy(spec)
    with_pack["capabilities"] = [{"pack": "fixture-pack", "params": {}}]
    with_pack["env"] = ["EXAMPLE_FIXTURE_TOKEN"]
    packaged = temporary / "with-pack"
    generate(with_pack, packaged, packs_dir=packs)
    report = audit_package(packaged, packs_dir=packs, thclaws=False)
    validator.validate(report)
    assert report["static_audit"]["findings"] == [] and report["dependencies"][0]["status"] == "assets_bundled"
    (pack / "scripts/probe.py").write_text('print("edited after generation")\n', encoding="utf-8")
    expect(audit_package(packaged, packs_dir=packs, thclaws=False), "drift", "fixture-pack--probe.py")
    (pack / "scripts/probe.py").write_text('print("synthetic fixture only")\n', encoding="utf-8")
    metadata.update(min_tier="T2", mcp_servers=["sql-admin"])
    (pack / "pack.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    expect(audit_package(packaged, packs_dir=packs, thclaws=False), "packs", "requires T2")
    metadata.update(min_tier="T0", mcp_servers=["fixture-mcp"])
    (pack / "pack.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    edited = json.loads((packaged / "agentspec.json").read_text(encoding="utf-8"))
    edited["env"] = []
    (packaged / "agentspec.json").write_text(json_text(edited), encoding="utf-8")
    report = audit_package(packaged, packs_dir=packs, thclaws=False)
    expect(report, "packs", "required env")
    assert report["audit"]["static"] == "failed"

    # The report schema refuses status claims the evidence does not support.
    good = audit_package(package, thclaws=False)
    earned = copy.deepcopy(good)
    earned["package_status"] = "draft"
    earned["audit"]["manifest"] = "passed"
    earned["static_audit"]["thclaws_validate"]["status"] = "passed"
    assert validator.is_valid(earned)
    warning = {"rule": "schema_vocabulary", "severity": "warning", "message": "forged"}
    failure = {"rule": "drift", "severity": "error", "message": "forged"}
    forged = {
        "draft without validate": {**good, "package_status": "draft"},
        "manifest passed while validate skipped": {**good, "audit": {**good["audit"], "manifest": "passed"}},
        "manifest skipped while validate passed": {**earned, "audit": {**earned["audit"], "manifest": "skipped"}},
        "draft with failed static": {**earned, "audit": {**earned["audit"], "static": "failed"}},
        "static passed over an error": {**earned, "static_audit": {**earned["static_audit"], "findings": [failure]}},
        "strict pass over a warning": {**earned, "static_audit": {**earned["static_audit"], "strict": True, "findings": [warning]}},
        "shippable": {**earned, "package_status": "shippable"},
        "draft without static_audit": {key: value for key, value in earned.items() if key != "static_audit"},
    }
    for label, report in forged.items():
        assert not validator.is_valid(report), f"schema accepted a forged report: {label}"
    lenient = {**earned, "static_audit": {**earned["static_audit"], "findings": [warning]}}
    assert validator.is_valid(lenient), "a warning must not block draft outside strict mode"

    print("M5 offline OK: verdicts, readable failures, warning/strict split, target-bound rules and wrappers")
    binary = shutil.which("thclaws")
    if binary is None:
        print("M5 SKIP: thclaws is not on PATH; draft promotion and wrapper runs not exercised (exit 2)")
        return 2
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True, timeout=30)
    assert version.stdout.splitlines()[0] == f"thclaws {THCLAWS_BASELINE}", (
        f"thClaws baseline changed: {version.stdout.strip()}; inspect DESIGN before updating the audit"
    )
    before = snapshot(package)
    report = audit_package(package)
    assert snapshot(package) == before, "thclaws validation left files in the package"
    validator.validate(report)
    assert report["package_status"] == "draft" and exit_code(report) == 0
    assert report["audit"] == {"spec": "passed", "manifest": "passed", "static": "passed", **NOT_RUN}
    assert report["static_audit"]["thclaws_validate"]["version"].startswith(f"thclaws {THCLAWS_BASELINE}")
    write_report(package, report)
    written = snapshot(package)
    write_report(package, audit_package(package))
    assert snapshot(package) == written, "draft report is not deterministic"

    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONPYCACHEPREFIX": str(temporary / "pycache")}
    fresh = temporary / "wrapped"
    generate(spec, fresh)
    result = subprocess.run([sys.executable, "audit.py"], cwd=fresh, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0 and "package_status=draft" in result.stdout, result.stdout + result.stderr
    assert "run studio.py" in result.stdout, "verify-only wrapper must point at the stale report"
    assert json.loads((fresh / "builder-build-report.json").read_text())["package_status"] == "unverified"
    result = subprocess.run([sys.executable, "studio.py"], cwd=fresh, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0 and "updated" in result.stdout, result.stdout + result.stderr
    assert (fresh / "builder-build-report.json").read_bytes() == written["builder-build-report.json"]
    result = subprocess.run([sys.executable, "audit.py", "--strict"], cwd=temporary / "annotated", env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1 and "schema_vocabulary" in result.stdout, result.stdout + result.stderr
    for folder, code in ((sql, 1), (temporary / "admin-mcp", 1)):
        result = subprocess.run([sys.executable, "audit.py"], cwd=folder, env=env, capture_output=True, text=True, check=False)
        assert result.returncode == code and "package_status=unverified" in result.stdout, result.stdout + result.stderr
    print(f"M5 native OK: invoice-reviewer reached draft on {version.stdout.splitlines()[0]}; sql-reader stays unverified")
    return 0


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="agent-builder-m5-") as temporary:
            return check(Path(temporary))
    except (AssertionError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError, subprocess.SubprocessError) as error:
        print(f"M5 FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
