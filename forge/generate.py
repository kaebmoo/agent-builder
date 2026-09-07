"""Deterministic Atlas single-worker packages. thClaws owns manifest validation/packing."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from jsonschema import Draft202012Validator

from forge.compat import resolve_compatibility
from forge.spec import validate_spec

ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA_PATH = ROOT / "builder-build-report.schema.json"
THCLAWS_BASELINE = "0.116.0"


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def pack_assets(spec: dict[str, Any], packs_dir: Path) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    """Copy explicitly listed assets; declare MCP names without installing or running servers."""
    files: dict[str, bytes] = {}
    dependencies = []
    seen = set()
    for capability in spec["capabilities"]:
        name = capability["pack"]
        if name in seen:
            raise ValueError(f"duplicate capability pack: {name}")
        seen.add(name)
        folder = packs_dir / name
        manifest = folder / "pack.yaml"
        if folder.is_symlink() or manifest.is_symlink():
            raise ValueError(f"pack paths must not be symlinks: {name}")
        if not manifest.exists():
            dependencies.append({"pack": name, "status": "missing", "mcp_servers": []})
            continue
        pack = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        if not isinstance(pack, dict):
            raise TypeError(f"pack {name}: pack.yaml must be an object")
        required = {"min_tier", "tools", "env", "mcp_servers", "scripts", "skills"}
        if set(pack) != required or pack["min_tier"] not in ("T0", "T1", "T2"):
            raise ValueError(f"pack {name}: expected fields {sorted(required)} and min_tier T0/T1/T2")
        for key in required - {"min_tier"}:
            values = pack[key]
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                raise ValueError(f"pack {name}: {key} must be a list of non-empty strings")
            if len(values) != len(set(values)):
                raise ValueError(f"pack {name}: duplicate {key}")
        if capability["params"]:
            raise ValueError(f"pack {name}: parameter expansion is not supported yet")
        # Both values are validated as T0/T1/T2, whose lexical order matches tier order.
        if spec["permissions"]["tier"] < pack["min_tier"]:
            raise ValueError(f"pack {name} requires {pack['min_tier']}")
        for key, declared in (("tools", spec["permissions"]["tools"]), ("env", spec["env"])):
            if not set(pack[key]) <= set(declared):
                raise ValueError(f"pack {name}: required {key} must be declared in AgentSpec")
        for kind in ("scripts", "skills"):
            for asset in sorted(pack[kind]):
                pattern = r"[a-z0-9][a-z0-9_-]*\.py" if kind == "scripts" else r"[a-z0-9][a-z0-9_-]*"
                if not re.fullmatch(pattern, asset):
                    raise ValueError(f"pack {name}: invalid {kind} asset name: {asset!r}")
                relative = Path(kind) / asset
                destination = f".thclaws/{kind}/{name}--{asset}"
                if kind == "skills":
                    relative /= "SKILL.md"
                    destination += "/SKILL.md"
                source = folder / relative
                if any(part.is_symlink() for part in [source, *source.parents]):
                    raise ValueError(f"pack {name}: asset must not use symlinks: {relative}")
                if destination in files:
                    raise ValueError(f"pack asset collision: {destination}")
                files[destination] = source.read_bytes()
        dependencies.append({"pack": name, "status": "assets_bundled", "mcp_servers": sorted(pack["mcp_servers"])})
    return files, dependencies


def guarantee_matrix(spec: dict[str, Any]) -> list[dict[str, str]]:
    permissions = spec["permissions"]
    collects = any(item["transport"] == "collect_files" for item in spec["outputs"])
    rows = [
        ("tool_allowlist", "Declared", "AGENTS.md declares tools; /agent/run does not enforce per-role tool lists."),
        ("no_file_writes", "Not guaranteed", "Write/Edit remain available. Daemon isolation and sandbox restrictions are not verified."),
        ("output_write_scope", "Declared" if permissions["write_scope"] == "output" else "Not applicable",
         "T1 declares output/**; /agent/run does not enforce that path boundary."),
        ("collect_files", "Enforced" if collects else "Not applicable",
         "thClaws snapshots matching files after a run; pre-existing files may be included. This is not authorship evidence."),
        ("no_shell", "Not guaranteed", "/agent/run registers Bash regardless of the package declaration."),
        ("no_network_builtins", "Static check" if permissions["network"] == "none" else "Not applicable",
         "AgentSpec validation rejects declared web built-ins for network=none; this does not enforce runtime egress."),
        ("network_hosts", "Not guaranteed", "Host egress controls must be provisioned and verified outside the package."),
        ("human_approval", "Not verified" if permissions["tier"] == "T2" else "Not applicable",
         "T2 requires an Atlas human_gate and an isolated T2 daemon; neither is provisioned by generation."),
        ("output_schema", "Not verified", "Live audit has not run. Atlas JSON parsing alone does not validate an output schema."),
        ("skill_mcp_calls", "No evidence", "No live run or SSE tool/skill evidence has been collected."),
    ]
    return [{"claim": claim, "status": status, "basis": basis} for claim, status, basis in rows]


def generate(spec: dict[str, Any], destination: Path, *, packs_dir: Path | None = None) -> dict[str, Any]:
    """Build into a new directory. Never overwrite an existing package or execute pack assets.

    M4 implements the single-worker surface used by both fixtures. Standalone
    orchestration needs a separate generator; matrix compatibility is not implementation support.
    """
    validate_spec(spec)
    compatibility = resolve_compatibility(spec)
    if compatibility["package_pattern"] != "single-worker":
        raise ValueError("M4 generates Atlas single-worker packages only; standalone generation is not implemented")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    files, dependencies = pack_assets(spec, (packs_dir or ROOT / "packs").resolve())

    def add_json(path: str, value: Any) -> None:
        if path in files:
            raise ValueError(f"generated file collision: {path}")
        files[path] = json_text(value).encode("utf-8")

    identity = spec["identity"]
    add_json("agentspec.json", spec)
    # Map AgentSpec to the upstream manifest shape; validation belongs to thClaws.
    add_json("manifest.json", {
        "version": identity["version"], "license": identity["license"], "author": identity["owner"],
        "categories": ["custom"], "tags": spec["routing"]["tags"],
        "requires": {
            # v0.116.0 stores this string without parsing; retain the full spec range.
            "thclaws_min_version": identity["thclaws_min_version"],
            "mcp_servers": sorted({server for pack in dependencies for server in pack["mcp_servers"]}),
        },
        "permissions": {"shell_execution": spec["permissions"]["shell"], "filesystem_scope": "workspace"},
    })
    add_json(".thclaws/settings.json", {"agent": {
        "id": identity["name"], "name": identity["name"], "description": spec["mission"]["purpose"],
    }})
    add_json(".thclaws/schemas/refusal.json", spec["refusal"]["schema"])
    for section in ("inputs", "outputs"):
        for item in spec[section]:
            if "schema" in item:
                add_json(f".thclaws/schemas/{section}--{item['name']}.json", item["schema"])
    add_json("evaluation/golden-cases.json", spec["evaluation"]["golden_cases"])

    templates = Environment(loader=FileSystemLoader(ROOT / "templates"), undefined=StrictUndefined,
                            autoescape=False, keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)
    templates.filters["json"] = lambda value: json_text(value).rstrip("\n")
    for template, path in (
        ("AGENTS.md.j2", "AGENTS.md"),
        ("SKILL.md.j2", f".thclaws/skills/{identity['name']}/SKILL.md"),
    ):
        if path in files:
            raise ValueError(f"generated file collision: {path}")
        files[path] = templates.get_template(template).render(
            spec=spec, assistant_json=any(item["transport"] == "assistant_json" for item in spec["outputs"]),
        ).encode("utf-8")

    report_schema = json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    add_json("builder-build-report.schema.json", report_schema)
    report = {
        "format_version": 1, "package_status": "unverified", "target": spec["target"],
        "thclaws_baseline": THCLAWS_BASELINE,
        "compatibility": {"status": "matched", **compatibility},
        "generated_files": sorted([*files, "builder-build-report.json"]),
        "declared_permissions": spec["permissions"],
        "guarantee_matrix": guarantee_matrix(spec),
        "audit": {"spec": "passed", "manifest": "not_run", "static": "not_run", "live": "not_run", "security": "not_run"},
        "dependencies": dependencies,
        "deployment_hints": [
            "Run thclaws agent validate, then static/live/security audits before promoting package status.",
            "Configure MCP on the daemon; workspace settings do not install or isolate MCP servers.",
            "Provision model and environment variable names from agentspec.json at deployment time; no secret values are bundled.",
            "Manifest filesystem_scope=workspace is the packaging scope, not enforcement of the AgentSpec write_scope.",
            "Network policy remains in AgentSpec; M4 does not invent host allowlists or install egress controls.",
            "Atlas chooses file-handoff landing paths. collect_files snapshots do not prove file authorship.",
            *[f"Missing pack: {pack['pack']}; implement/install it before static audit or live use."
              for pack in dependencies if pack["status"] == "missing"],
            *(["Use an isolated T2 daemon and an Atlas human_gate before any external side effect."]
              if spec["permissions"]["tier"] == "T2" else []),
        ],
    }
    Draft202012Validator(report_schema).validate(report)
    add_json("builder-build-report.json", report)
    # Render and validate everything before touching the destination. Stage beside
    # it so the final rename stays on the same filesystem.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".forge-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "package"
        staged.mkdir()
        for path, content in sorted(files.items()):
            output = staged / path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        for directory in (".thclaws/agents", ".thclaws/scripts", ".thclaws/skills", ".thclaws/schemas"):
            (staged / directory).mkdir(exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"destination already exists: {destination}")
        staged.rename(destination)
    return report
