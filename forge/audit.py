"""Static audit (DESIGN §7): deterministic rules over a generated package. thClaws owns manifest validation."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from jinja2 import TemplateError
from jsonschema import Draft202012Validator

from forge.generate import REPORT_SCHEMA_PATH, THCLAWS_BASELINE, editable_files, json_text, render
from forge.spec import ATLAS_TARGETS

Finding = dict[str, str]
REQUIRED_SECTIONS = ("Mission", "Must", "Refuse", "Output")
PURE_JSON_SENTENCE = "ตอบ JSON ล้วน"
UNIVERSAL_RULES = ("spec", "packs", "inventory", "drift", "agents_md", "refusal", "schema_vocabulary")
# Rules that only hold for one execution surface. single-worker constraints come from /agent/run
# (DESIGN §3-4); the standalone generator (M9) legitimately ships .thclaws/agents/ and WorkflowRun.
TARGET_RULES = {"single_worker": frozenset(ATLAS_TARGETS)}
# JSON Schema 2020-12 vocabularies the builder's validators implement. jsonschema silently treats any
# other keyword as an annotation, so it is a warning in draft and must be fixed before shippable.
SUBSCHEMA = {"additionalProperties", "items", "contains", "propertyNames", "not", "if", "then", "else",
             "unevaluatedItems", "unevaluatedProperties", "contentSchema"}
SUBSCHEMA_LIST = {"allOf", "anyOf", "oneOf", "prefixItems"}
SUBSCHEMA_MAP = {"properties", "patternProperties", "$defs", "dependentSchemas"}
KNOWN_KEYWORDS = SUBSCHEMA | SUBSCHEMA_LIST | SUBSCHEMA_MAP | {
    "$schema", "$id", "$ref", "$anchor", "$dynamicRef", "$dynamicAnchor", "$vocabulary", "$comment",
    "type", "enum", "const", "multipleOf", "maximum", "exclusiveMaximum", "minimum", "exclusiveMinimum",
    "maxLength", "minLength", "pattern", "maxItems", "minItems", "uniqueItems", "maxContains", "minContains",
    "maxProperties", "minProperties", "required", "dependentRequired", "format", "contentEncoding",
    "contentMediaType", "title", "description", "default", "deprecated", "readOnly", "writeOnly", "examples",
}


def rules_for(target: str) -> list[str]:
    return [*UNIVERSAL_RULES, *(rule for rule, targets in TARGET_RULES.items() if target in targets)]


def unknown_keywords(schema: Any, path: str) -> list[str]:
    """Dotted paths of keywords outside the supported vocabulary, walking only well-known subschema slots."""
    if not isinstance(schema, dict):
        return []
    found = []
    for key, value in schema.items():
        location = f"{path}.{key}"
        if key not in KNOWN_KEYWORDS:
            found.append(location)
        elif key in SUBSCHEMA:
            found += unknown_keywords(value, location)
        elif key in SUBSCHEMA_LIST and isinstance(value, list):
            for index, item in enumerate(value):
                found += unknown_keywords(item, f"{location}[{index}]")
        elif key in SUBSCHEMA_MAP and isinstance(value, dict):
            for name, item in value.items():
                found += unknown_keywords(item, f"{location}.{name}")
    return found


def describe_difference(expected: Any, actual: Any, path: str = "") -> str:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual), key=str):
            if key not in expected or key not in actual:
                return f"{path}.{key}: {'unexpected key' if key not in expected else 'missing key'}".lstrip(".")
            if expected[key] != actual[key]:
                return describe_difference(expected[key], actual[key], f"{path}.{key}")
    return f"{path.lstrip('.') or '<root>'}: expected {compact(expected)} found {compact(actual)}"


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def agents_md_findings(spec: dict[str, Any], text: str) -> list[Finding]:
    findings: list[Finding] = []
    headings = {match.group(1) for match in re.finditer(r"^## (\S+)\s*$", text, re.MULTILINE)}
    for section in REQUIRED_SECTIONS:
        if section not in headings:
            findings.append(error("agents_md", f"AGENTS.md is missing the required section '## {section}'"))
    if any(item["transport"] == "assistant_json" for item in spec["outputs"]) and PURE_JSON_SENTENCE not in text:
        findings.append(error("agents_md", f"AGENTS.md must contain '{PURE_JSON_SENTENCE}' because an output is assistant_json"))
    for label in ("permissions", "model", "state"):
        if json_text(spec[label]).rstrip("\n") not in text:
            findings.append(error("agents_md", f"AGENTS.md must embed the declared {label} block exactly as in agentspec.json"))
    for condition in spec["refusal"]["conditions"]:
        if condition not in text:
            findings.append(error("refusal", f"AGENTS.md does not state the refusal condition: {condition}"))
    if spec["refusal"]["response"] not in text:
        findings.append(error("refusal", "AGENTS.md does not state the refusal response from agentspec.json"))
    return findings


def single_worker_findings(package: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for folder in (".thclaws/agents", ".thclaws/agent_workflow"):
        stray = sorted(p.relative_to(package).as_posix() for p in (package / folder).rglob("*") if p.is_file())
        if stray:
            findings.append(error("single_worker", f"{folder}/ must be empty for an Atlas single-worker package; "
                                  f"/agent/run has no subagent factory (found {', '.join(stray)})"))
    if "WorkflowRun" in text:
        findings.append(error("single_worker", "AGENTS.md must not instruct WorkflowRun; it fails on /agent/run"))
    return findings


def ignorable(path: str) -> bool:
    """Runtime state, VCS and bytecode that thclaws agent pack strips; everything else must be rendered."""
    parts = path.split("/")
    return (path.startswith(".thclaws/state/") or ".git" in parts[:1] or "__pycache__" in parts
            or parts[-1] == ".DS_Store" or path.endswith(".pyc"))


def error(rule: str, message: str) -> Finding:
    return {"rule": rule, "severity": "error", "message": message}


def static_findings(package: Path, packs_dir: Path | None = None) -> tuple[list[Finding], dict[str, Any] | None]:
    """Run every applicable rule. Returns findings and the re-rendered report, or None when the spec cannot render."""
    findings: list[Finding] = []
    try:
        spec = json.loads((package / "agentspec.json").read_text(encoding="utf-8"))
        files, report, problems = render(spec, packs_dir)
    except (OSError, ValueError, TypeError, KeyError, TemplateError) as exc:
        findings.append(error("spec", f"agentspec.json cannot be rendered: {exc}"))
        return findings, None
    for problem in problems:
        findings.append(error("packs", problem))
    for dependency in report["dependencies"]:
        if dependency["status"] == "missing":
            findings.append(error("packs", f"pack {dependency['pack']}: no descriptor under packs/; "
                                  "a missing pack cannot pass static audit"))

    editable = editable_files(spec)
    for path, expected in sorted(files.items()):
        if path == "builder-build-report.json":
            continue  # the audit writes this file itself
        target = package / path
        if not target.is_file():
            findings.append(error("inventory", f"{path}: generated file is missing"))
        elif path not in editable and (actual := target.read_bytes()) != expected:
            detail = ""
            if path.endswith(".json"):
                try:
                    detail = ": " + describe_difference(json.loads(expected), json.loads(actual))
                except ValueError:
                    detail = ": not valid JSON"
            findings.append(error("drift", f"{path}: differs from the file rendered from agentspec.json{detail}"))
    for target in sorted(package.rglob("*")):
        path = target.relative_to(package).as_posix()
        if target.is_symlink():
            findings.append(error("inventory", f"{path}: symlinks are not allowed in a package"))
        elif (target.is_file() and not ignorable(path) and path not in files
              and not path.startswith((".thclaws/agents/", ".thclaws/agent_workflow/"))):
            findings.append(error("inventory", f"{path}: not generated from agentspec.json or a declared pack; "
                                  "remove it or declare it through the spec"))

    instructions = package / "AGENTS.md"
    text = instructions.read_text(encoding="utf-8") if instructions.is_file() else ""
    if text:
        findings += agents_md_findings(spec, text)
    schemas = [(f"{section}[{index}].schema", item["schema"]) for section in ("inputs", "outputs")
               for index, item in enumerate(spec[section]) if "schema" in item]
    for location, schema in [*schemas, ("refusal.schema", spec["refusal"]["schema"])]:
        for keyword in unknown_keywords(schema, location):
            findings.append({"rule": "schema_vocabulary", "severity": "warning",
                             "message": f"{keyword}: keyword outside the supported JSON Schema 2020-12 "
                                        "vocabulary; validators ignore it (blocks shippable)"})
    if "single_worker" in rules_for(spec["target"]):
        findings += single_worker_findings(package, text)
    return findings, report


def thclaws_validate(package: Path) -> dict[str, Any]:
    """Run `thclaws agent validate` without leaving bytecode in the package. Skips explicitly when absent."""
    binary = shutil.which(os.environ.get("THCLAWS_BIN", "thclaws"))
    if binary is None:
        return {"status": "skipped", "version": None,
                "detail": "thclaws is not on PATH (THCLAWS_BIN); manifest validation not run, package stays unverified"}
    lines = [line.strip() for line in subprocess.run([binary, "--version"], capture_output=True, text=True,
                                                     check=True, timeout=30).stdout.splitlines() if line.strip()]
    version = " ".join(lines[:2])
    if lines[0] != f"thclaws {THCLAWS_BASELINE}":
        return {"status": "failed", "version": version,
                "detail": f"{lines[0]} is not the verified baseline {THCLAWS_BASELINE}; re-check DESIGN §4 first"}
    with tempfile.TemporaryDirectory(prefix="agent-builder-pycache-") as cache:
        result = subprocess.run([binary, "agent", "validate", str(package)], capture_output=True, text=True,
                                env={**os.environ, "PYTHONPYCACHEPREFIX": cache}, timeout=120, check=False)
    if result.returncode == 0:
        return {"status": "passed", "version": version, "detail": ""}
    output = (result.stdout + result.stderr).replace(str(package), "<package>").strip()
    return {"status": "failed", "version": version, "detail": output}


def audit_package(package: Path, *, packs_dir: Path | None = None, strict: bool = False,
                  thclaws: bool = True) -> dict[str, Any]:
    """Return the build report with the static-audit verdict filled in. Never writes to the package."""
    package = Path(package)
    findings, report = static_findings(package, packs_dir)
    if report is None:
        try:
            report = json.loads((package / "builder-build-report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
        if not isinstance(report, dict) or not isinstance(report.get("audit"), dict):
            raise ValueError("; ".join(f["message"] for f in findings) + " (no build report to record the failure)")
        report["audit"]["spec"] = "failed"
    validate = thclaws_validate(package) if thclaws else {
        "status": "skipped", "version": None, "detail": "thclaws validation disabled for this run"}
    errors = [f for f in findings if f["severity"] == "error" or strict]
    report["audit"].update(static="failed" if errors else "passed", manifest=validate["status"])
    report["package_status"] = "draft" if not errors and validate["status"] == "passed" and (
        report["audit"]["spec"] == "passed") else "unverified"
    report["static_audit"] = {"strict": strict, "findings": findings, "thclaws_validate": validate}
    return report


def write_report(package: Path, report: dict[str, Any]) -> None:
    Draft202012Validator(json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))).validate(report)
    (Path(package) / "builder-build-report.json").write_text(json_text(report), encoding="utf-8")


def exit_code(report: dict[str, Any]) -> int:
    """0 = draft, 1 = static or manifest failure, 2 = static passed but thclaws validation was skipped."""
    if report["audit"]["static"] == "failed" or report["audit"]["manifest"] == "failed":
        return 1
    return 0 if report["package_status"] == "draft" else 2


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("package", type=Path, help="generated package directory")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures (the shippable bar)")
    parser.add_argument("--write", action="store_true", help="record the verdict in builder-build-report.json")


def command(args: argparse.Namespace) -> int:
    try:
        report = audit_package(args.package, strict=args.strict)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        print(f"audit FAIL: {exc}")
        return 1
    audit = report["static_audit"]
    for finding in audit["findings"]:
        mark = "✗" if finding["severity"] == "error" or args.strict else "⚠"
        print(f"  {mark} {finding['rule']}: {finding['message']}")
    validate = audit["thclaws_validate"]
    print(f"thclaws agent validate: {validate['status']}"
          + (f" ({validate['version']})" if validate["version"] else "")
          + (f"\n{validate['detail']}" if validate["detail"] else ""))
    stored = Path(args.package) / "builder-build-report.json"
    if args.write:
        write_report(args.package, report)
        print("builder-build-report.json updated")
    elif not stored.is_file() or stored.read_text(encoding="utf-8") != json_text(report):
        print("note: builder-build-report.json does not reflect this result; run studio.py to record it")
    code = exit_code(report)
    print(f"package_status={report['package_status']} ({['PASS', 'FAIL', 'SKIP'][code]})")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge audit", description="static audit of a generated package")
    configure(parser)
    return command(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
