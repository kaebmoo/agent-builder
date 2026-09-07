#!/usr/bin/env python3
"""M4 gate: deterministic generation, report contract and native thClaws validation."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import yaml
    from jsonschema import Draft202012Validator

    from forge.compat import resolve_compatibility
    from forge.generate import REPORT_SCHEMA_PATH, THCLAWS_BASELINE, generate
    from forge.spec import load_spec
except ModuleNotFoundError:
    print("M4 FAIL: install project dependencies from pyproject.toml first")
    raise SystemExit(1)


def snapshot(folder: Path) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def check(temporary: Path) -> int:
    report_schema = json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(report_schema)
    validator = Draft202012Validator(report_schema)
    packages = []
    for name in ("invoice-reviewer", "sql-reader"):
        spec = load_spec(ROOT / "fixtures" / name / "spec.yaml")
        before = copy.deepcopy(spec)
        first, second = temporary / name, temporary / (name + "-repeat")
        report = generate(spec, first)
        generate(spec, second)
        files = snapshot(first)
        assert files == snapshot(second), f"{name}: output differs byte-for-byte between runs"
        assert spec == before, "generator mutated AgentSpec"
        assert sorted(files) == report["generated_files"], "report file inventory is incomplete"
        assert json.loads(files["agentspec.json"]) == spec, "AgentSpec content lost"
        assert json.loads(files["evaluation/golden-cases.json"]) == spec["evaluation"]["golden_cases"]
        assert json.loads(files["builder-build-report.json"]) == report
        validator.validate(report)
        assert report["package_status"] == "unverified"
        assert report["audit"] == {key: "passed" if key == "spec" else "not_run"
                                   for key in ("spec", "manifest", "static", "live", "security")}
        assert report["declared_permissions"] == spec["permissions"]
        assert report["compatibility"] == {"status": "matched", **resolve_compatibility(spec)}
        rows = {row["claim"]: row["status"] for row in report["guarantee_matrix"]}
        assert set(rows) == {
            "tool_allowlist", "no_file_writes", "output_write_scope", "collect_files", "no_shell",
            "no_network_builtins", "network_hosts", "human_approval", "output_schema", "skill_mcp_calls",
        }
        assert rows["no_shell"] == rows["no_file_writes"] == rows["network_hosts"] == "Not guaranteed"
        assert rows["output_schema"] == "Not verified" and rows["skill_mcp_calls"] == "No evidence"
        assert rows["no_network_builtins"] == "Static check"
        for section in ("inputs", "outputs"):
            for item in spec[section]:
                if "schema" in item:
                    assert json.loads(files[f".thclaws/schemas/{section}--{item['name']}.json"]) == item["schema"]
        assert json.loads(files[".thclaws/schemas/refusal.json"]) == spec["refusal"]["schema"]
        instructions = files["AGENTS.md"].decode()
        assert "\n\n\n" not in instructions, "template control blocks added extra blank lines"
        for item in spec["inputs"]:
            assert any(line.startswith(f"- {item['name']}: ") for line in instructions.splitlines()), (
                "input list items must remain on separate lines"
            )
        for condition in spec["refusal"]["conditions"]:
            assert f"- {condition}" in instructions.splitlines(), "refusal list item lost or joined"
        assert all(f"## {heading}" in instructions for heading in ("Mission", "Must", "Refuse", "Output"))
        assert "ตอบ JSON ล้วน" in instructions
        assert not any(path.startswith((".thclaws/agents/", ".thclaws/agent_workflow/")) for path in files)
        for directory in ("agents", "scripts", "skills", "schemas"):
            assert (first / ".thclaws" / directory).is_dir()
        if name == "sql-reader":
            assert report["dependencies"][0]["status"] in ("assets_bundled", "conformant")
            assert report["dependencies"][0]["mcp_servers"] == ["sql-readonly"]
        else:
            assert report["dependencies"] == []
        try:
            generate(spec, first)
        except FileExistsError:
            pass
        else:
            raise AssertionError("generator overwrote an existing package")
        assert snapshot(first) == files
        packages.append(first)

    spec = load_spec(ROOT / "fixtures/invoice-reviewer/spec.yaml")

    def rejects(candidate: dict, reason: str, *, packs: Path | None = None) -> None:
        output = temporary / "rejected"
        try:
            generate(candidate, output, packs_dir=packs)
        except (ValueError, TypeError, OSError) as error:
            assert reason in str(error), f"unexpected failure: {error}"
        else:
            raise AssertionError(f"accepted invalid generation: {reason}")
        assert not output.exists(), "failed generation left a partial package"

    malformed = copy.deepcopy(spec)
    malformed["permissions"]["tools"].append("Bash")
    rejects(malformed, "shell=none")
    malformed = copy.deepcopy(spec)
    malformed["inputs"][0]["name"] = "../../escape"
    rejects(malformed, "Invalid AgentSpec")
    rejects({"target": "atlas-worker"}, "Invalid AgentSpec")
    standalone = copy.deepcopy(spec)
    standalone.update(target="thclaws-standalone", package_pattern="dynamic")
    del standalone["routing"]
    standalone["inputs"] = [item for item in standalone["inputs"] if item["transport"] == "prompt_json"]
    rejects(standalone, "standalone generation is not implemented")

    # A synthetic local pack proves placement without pretending the M7 SQL MCP exists.
    packs = temporary / "packs"
    pack = packs / "fixture-pack"
    (pack / "scripts").mkdir(parents=True)
    (pack / "scripts/probe.py").write_text('print("synthetic fixture only")\n', encoding="utf-8")
    (pack / "skills/guide").mkdir(parents=True)
    (pack / "skills/guide/SKILL.md").write_text("# Synthetic guide\n", encoding="utf-8")
    metadata = {"min_tier": "T0", "tools": ["Read"], "env": ["EXAMPLE_FIXTURE_TOKEN"],
                "network": "none", "hosts": [],
                "fixture": {"setup": "scripts/probe.py", "cases": []}, "mcp_servers": [], "scripts": ["probe.py"], "skills": ["guide"]}
    (pack / "pack.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    with_pack = copy.deepcopy(spec)
    with_pack["capabilities"] = [{"pack": "fixture-pack", "params": {}}]
    rejects(with_pack, "required env", packs=packs)
    with_pack["env"] = ["EXAMPLE_FIXTURE_TOKEN"]
    output = temporary / "with-pack"
    generate(with_pack, output, packs_dir=packs)
    files = snapshot(output)
    assert files[".thclaws/scripts/fixture-pack--probe.py"] == (pack / "scripts/probe.py").read_bytes()
    assert files[".thclaws/skills/fixture-pack--guide/SKILL.md"] == (pack / "skills/guide/SKILL.md").read_bytes()
    assert not any("mcp_servers" in key for key in json.loads(files[".thclaws/settings.json"]))
    assert json.loads(files["manifest.json"])["requires"]["mcp_servers"] == []
    repeated_pack = temporary / "with-pack-repeat"
    generate(with_pack, repeated_pack, packs_dir=packs)
    assert files == snapshot(repeated_pack), "pack assets are not deterministic"
    packages.append(output)
    metadata["scripts"] = ["../escape.py"]
    (pack / "pack.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    rejects(with_pack, "scripts: duplicate or invalid name", packs=packs)
    metadata["scripts"] = ["probe.py"]
    metadata["min_tier"] = "T2"
    (pack / "pack.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    rejects(with_pack, "requires T2", packs=packs)
    metadata["min_tier"] = "T0"
    (pack / "pack.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    (pack / "scripts/probe.py").unlink()
    (pack / "scripts/probe.py").symlink_to(pack / "skills/guide/SKILL.md")
    rejects(with_pack, "symlinks", packs=packs)

    collector = copy.deepcopy(spec)
    collector["permissions"].update(tier="T1", tools=["Read", "Write"], write_scope="output")
    collector["outputs"] = [{"name": "artifacts", "transport": "collect_files", "files": {"globs": ["output/*.json"]}}]
    collected = temporary / "collector"
    report = generate(collector, collected)
    assert "ตอบ JSON ล้วน" not in (collected / "AGENTS.md").read_text(encoding="utf-8")
    rows = {row["claim"]: row["status"] for row in report["guarantee_matrix"]}
    assert rows["output_write_scope"] == "Declared" and rows["collect_files"] == "Enforced"
    packages.append(collected)
    side_effect = copy.deepcopy(collector)
    side_effect["target"] = "atlas-workflow"
    side_effect["permissions"].update(tier="T2", write_scope="workspace")
    report = generate(side_effect, temporary / "t2")
    assert any("human_gate" in hint for hint in report["deployment_hints"])
    assert next(row for row in report["guarantee_matrix"] if row["claim"] == "human_approval")["status"] == "Not verified"
    bad_report = copy.deepcopy(report)
    bad_report["package_status"] = "shippable"
    assert not validator.is_valid(bad_report), "M4 report schema permits an unsupported status claim"

    print("M4 offline OK: byte-identical fixtures/pack assets, report schema, guarantees and failure cases")
    binary = shutil.which("thclaws")
    if binary is None:
        print("M4 SKIP: thclaws is not on PATH; agent new/validate not run (exit 2). Offline checks passed.")
        return 2
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True, timeout=30)
    assert version.stdout.splitlines()[0] == f"thclaws {THCLAWS_BASELINE}", (
        f"thClaws baseline changed: {version.stdout.strip()}; inspect DESIGN before updating templates"
    )
    print(version.stdout.strip())
    for pattern in ("static-pipeline", "batch-fanout", "dynamic"):
        folder = temporary / ("upstream-" + pattern)
        subprocess.run([binary, "agent", "new", str(folder), "--pattern", pattern, "--name", "compat-reference"],
                       capture_output=True, text=True, check=True, timeout=30)
        compatibility = resolve_compatibility({"target": "thclaws-standalone", "package_pattern": pattern})
        assert (folder / ".thclaws/agent_workflow/run.js").exists() == (compatibility["flow_location"] is not None)
        upstream = json.loads((folder / "manifest.json").read_text())
        generated = json.loads((packages[0] / "manifest.json").read_text())
        assert set(upstream) <= set(generated), "upstream scaffold manifest fields changed"
        upstream_agent = json.loads((folder / ".thclaws/settings.json").read_text())["agent"]
        generated_agent = json.loads((packages[0] / ".thclaws/settings.json").read_text())["agent"]
        assert set(upstream_agent) == set(generated_agent), "upstream identity shape changed"
    for folder in packages:
        before = snapshot(folder)
        # thClaws py_compile writes bytecode; keep it outside the generated package.
        env = {**os.environ, "PYTHONPYCACHEPREFIX": str(temporary / "pycache")}
        result = subprocess.run([binary, "agent", "validate", str(folder)], env=env,
                                capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 0, f"{folder.name}: thclaws agent validate failed:\n{result.stdout}{result.stderr}"
        assert snapshot(folder) == before, "native validation changed package bytes"
        print(f"M4 native OK: {folder.name} — thclaws agent validate exit=0")
    print("M4 OK: offline checks, upstream scaffold compatibility and native validation passed")
    return 0


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="agent-builder-m4-") as temporary:
            return check(Path(temporary))
    except (AssertionError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError, subprocess.SubprocessError) as error:
        print(f"M4 FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
