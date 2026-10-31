"""Security audit (DESIGN §7, M19): deterministic checks on top of a strict static audit; never calls an LLM."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from forge.generate import ROOT
from forge.packs import read_pack

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}")),
)
SENSITIVE_PATH = re.compile(r"(\.env$|\.key$|\.pem$|\.p12$|_secret|id_rsa)")


def env_value_patterns(name: str) -> list[tuple[str, re.Pattern[str]]]:
    name = re.escape(name)
    return [
        ("env_assignment", re.compile(rf"(?m)^\s*(?:export\s+)?{name}=\S+")),
        ("env_json_value", re.compile(rf'"{name}"\s*:\s*"[^"<$][^"]*"')),
    ]


def scan_files(package: Path) -> list[Path]:
    files = []
    for target in package.rglob("*"):
        path = target.relative_to(package)
        if (target.is_symlink() or not target.is_file() or path.as_posix().startswith((".thclaws/state/", ".git/"))
                or "__pycache__" in path.parts or path.suffix == ".pyc"
                or path.as_posix() == "builder-build-report.json"):
            continue
        files.append(target)
    return sorted(files, key=lambda target: target.relative_to(package).as_posix())


def strict_findings(report: dict) -> list[dict]:
    static = report["static_audit"]
    if not static["findings"] and static["strict"]:
        return []
    return [{"rule": "strict", "severity": "error", "message":
             f"static audit under --strict reports {len(static['findings'])} finding(s); security requires none"}]


def secret_findings(package: Path, spec: dict, files: list[Path]) -> list[dict]:
    patterns = [*SECRET_PATTERNS,
                *(pattern for name in sorted(spec["env"]) for pattern in env_value_patterns(name))]
    findings = []
    for target in files:
        path = target.relative_to(package).as_posix()
        if SENSITIVE_PATH.search(path):
            findings.append({"rule": "secrets", "severity": "error", "message":
                             f"{path}: sensitive path; thclaws agent pack strips it silently (DESIGN §8)"})
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line, content in enumerate(text.splitlines(), 1):
            for name in sorted({name for name, pattern in patterns if pattern.search(content)}):
                findings.append({"rule": "secrets", "severity": "error", "message":
                                 f"{path}:{line}: matches secret pattern {name}; remove the value and provision "
                                 "it from the daemon environment"})
    return findings


def pack_findings(report: dict) -> list[dict]:
    return [{"rule": "packs", "severity": "error", "message":
             f"pack {dependency['pack']}: status {dependency['status']}; security requires "
             "conformant evidence from forge pack test"}
            for dependency in report["dependencies"] if dependency["status"] != "conformant"]


def declared_hosts(spec: dict, packs_dir: Path | None) -> set[str]:
    hosts: set[str] = set()
    for capability in spec["capabilities"]:
        folder = (packs_dir or ROOT / "packs").resolve() / capability["pack"]
        if not (folder / "pack.yaml").exists():
            continue
        try:
            hosts.update(read_pack(folder)["hosts"])
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
            continue  # Invalid packs already block the strict static audit.
    return hosts


def egress_findings(spec: dict, packs_dir: Path | None) -> list[dict]:
    if spec["permissions"]["network"] != "allowlist" or declared_hosts(spec, packs_dir):
        return []
    return [{"rule": "egress", "severity": "error", "message":
             "network allowlist declared but no pack declares hosts; egress cannot be declared"}]


def security_findings(package: Path, spec: dict, report: dict, *,
                      packs_dir: Path | None = None) -> tuple[list[dict], int]:
    files = scan_files(package)
    findings = [*strict_findings(report), *secret_findings(package, spec, files),
                *pack_findings(report), *egress_findings(spec, packs_dir)]
    return sorted(findings, key=lambda finding: (finding["rule"], finding["message"])), len(files)


def security_audit(package: Path, report: dict, *, packs_dir: Path | None = None) -> dict:
    """Extend a strict static/live report produced in this same run with security evidence."""
    spec = json.loads((package / "agentspec.json").read_text(encoding="utf-8"))
    findings, scanned = security_findings(package, spec, report, packs_dir=packs_dir)
    status = "failed" if findings else "passed"
    if (not findings and report["package_status"] == "candidate"
            and report["compatibility"]["status"] == "matched" and report["static_audit"]["strict"]):
        report["package_status"] = "shippable"
        detail = ("compatibility matched, strict static audit clean, live evidence and security checks "
                  "passed in this run")
    elif not findings:
        detail = ("security checks passed; shippable requires live evidence in the same run "
                  "(forge audit --live --security) and a passing thclaws agent validate")
    else:
        detail = f"{len(findings)} security finding(s); package status unchanged"
    report["security_audit"] = {"status": status, "strict": True, "findings": findings,
                                "scanned_files": scanned, "detail": detail}
    report["audit"]["security"] = status
    return report


def exit_code_security(report: dict, *, live_requested: bool) -> int:
    audit = report["audit"]
    if any(audit[name] == "failed" for name in ("static", "manifest", "live", "security")):
        return 1
    if audit["manifest"] == "skipped" or (live_requested and audit["live"] == "skipped"):
        return 2
    return 0
