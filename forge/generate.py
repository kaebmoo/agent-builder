"""Deterministic Atlas and standalone packages. thClaws owns manifest validation/packing."""
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
from forge.packs import NETWORK, conformance, mcp_tools, server_config, validate_pack
from forge.spec import validate_spec

ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA_PATH = ROOT / "builder-build-report.schema.json"
# Atlas/export baseline (DESIGN §4): moved from 0.116.0 to 0.138.0 in M12 after re-checking every claim against
# source revision d452ece; the baseline catalog is patterns/tools-<baseline>.json.
THCLAWS_BASELINE = "0.138.0"
# Standalone compatibility is evidence-bound: a version without the reviewed
# source revision is not enough to reopen a runtime-sensitive guarantee.
STANDALONE_THCLAWS_COMPATIBILITY = {
    "0.116.0": frozenset({"75edc48"}),
    "0.121.0": frozenset({"35ccf16"}),
    "0.129.0": frozenset({"7a27129"}),
    THCLAWS_BASELINE: frozenset({"d452ece"}),
}
# Thin runners copied verbatim into every package; the audit rules live in forge.audit only.
WRAPPERS = ("audit.py", "studio.py")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def editable_files(spec: dict[str, Any]) -> set[str]:
    """Prose an operator or LLM may customize after generation; every other file must match the render."""
    paths = {f".thclaws/skills/{spec['identity']['name']}/SKILL.md"}
    if spec["target"] != "thclaws-standalone":
        paths.add("AGENTS.md")
    return paths


def load_pack(name: str, capability: dict[str, Any], spec: dict[str, Any], folder: Path,
              pack: Any) -> tuple[dict[str, bytes], list[str]]:
    """Check one descriptor against the spec and read its assets. Raises ValueError/TypeError with the reason."""
    validate_pack(pack, folder)
    if capability["params"]:
        raise ValueError(f"pack {name}: parameter expansion is not supported yet")
    # Both values are validated as T0/T1/T2, whose lexical order matches tier order.
    if spec["permissions"]["tier"] < pack["min_tier"]:
        raise ValueError(f"pack {name} requires {pack['min_tier']}")
    if NETWORK.index(spec["permissions"]["network"]) < NETWORK.index(pack["network"]):
        raise ValueError(f"pack {name}: required network {pack['network']} exceeds AgentSpec")
    for key, required, declared in (("tools", set(pack["tools"]) | mcp_tools(pack), spec["permissions"]["tools"]),
                                    ("env", set(pack["env"]), spec["env"])):
        if not required <= set(declared):
            raise ValueError(f"pack {name}: required {key} must be declared in AgentSpec")
    bundled: dict[str, bytes] = {}
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
            bundled[destination] = source.read_bytes()
    return bundled, sorted(s["name"] for s in pack["mcp_servers"])


def pack_assets(spec: dict[str, Any], packs_dir: Path) -> tuple[dict[str, bytes], list[dict[str, Any]], list[str]]:
    """Copy explicitly listed assets; declare MCP names without installing or running servers.

    Returns bundled files, dependency records and readable problems instead of raising,
    so the static audit can report every pack defect of an edited package at once.
    """
    files: dict[str, bytes] = {}
    dependencies = []
    problems: list[str] = []
    seen = set()
    configs = {}
    for capability in spec["capabilities"]:
        name = capability["pack"]
        if name in seen:
            problems.append(f"duplicate capability pack: {name}")
            continue
        seen.add(name)
        folder = packs_dir / name
        manifest = folder / "pack.yaml"
        if folder.is_symlink() or manifest.is_symlink():
            problems.append(f"pack paths must not be symlinks: {name}")
            continue
        if not manifest.exists():
            dependencies.append({"pack": name, "status": "missing", "mcp_servers": []})
            continue
        try:
            pack = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            bundled, servers = load_pack(name, capability, spec, folder, pack)
        except (ValueError, TypeError, KeyError, OSError, yaml.YAMLError) as error:
            problems.append(str(error))
            continue
        for destination in bundled:
            if destination in files:
                problems.append(f"pack asset collision: {destination}")
        files.update(bundled)
        for server, config in server_config(name, pack).items():
            if server in configs:
                problems.append(f"duplicate MCP server across packs: {server}")
            configs[server] = config
        try:
            evidence = conformance(folder, pack)
        except (ValueError, TypeError, KeyError, OSError):
            evidence = None  # Rendering does not depend on a successful external test; static audit checks it.
        dependency = {"pack": name, "status": "conformant" if evidence else "assets_bundled", "mcp_servers": servers,
                      "network": pack["network"], "hosts": sorted(pack["hosts"]), "env": sorted(pack["env"])}
        if evidence:
            dependency["conformance"] = evidence
        dependencies.append(dependency)
    if configs:
        files[".thclaws/mcp.json"] = json_text({"mcpServers": configs}).encode()
    return files, dependencies, problems


def standalone_roles(spec: dict[str, Any], packs_dir: Path) -> dict[str, list[str]]:
    """Nonempty native allowlists; only pack-declared nonmutating MCP tools reach read roles."""
    from forge.packs import read_pack

    if spec["permissions"]["tier"] != "T0":
        raise ValueError("M9 standalone currently supports T0 only; T1 is disabled until upstream "
                         "writePaths rejects traversal and every symlink escape")
    readonly = {"Read", "Grep", "Glob"}
    for capability in spec["capabilities"]:
        folder = packs_dir / capability["pack"]
        if (folder / "pack.yaml").exists():
            pack = read_pack(folder)
            for server in pack["mcp_servers"]:
                readonly.update(f"{server['name']}__{tool}" for tool in server["tools"]
                                if tool not in server["mutating"])
    declared = set(spec["permissions"]["tools"])
    unsupported = declared - readonly
    if unsupported:
        raise ValueError("M9 T0 tools must be Read/Grep/Glob or pack-declared nonmutating MCP tools; "
                         "unsupported: " + ", ".join(sorted(unsupported)))
    roles = {role: sorted(declared) for role in ("planner", "worker", "verifier")}
    if any(not tools for tools in roles.values()):
        raise ValueError("standalone roles require nonempty tool subsets; empty tools would inherit all tools")
    return roles


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
         "T2 requires the approval edge and dedicated daemon bindings in atlas-node-template.json; "
         "generation/static audit do not verify deployment or bind approval to tool arguments."
         if permissions["tier"] == "T2" else "T0/T1 declare no external side effects; no T2 approval gate applies."),
        ("output_schema", "Not verified", "Live audit has not run. Atlas JSON parsing alone does not validate an output schema."),
        ("skill_mcp_calls", "No evidence", "No live run or SSE tool/skill evidence has been collected."),
    ]
    if spec["target"] == "thclaws-standalone":
        replacements = {
            "tool_allowlist": ("Enforced", "Native named subagents intersect their tool registry with nonempty role tools; orchestration itself is outside this claim."),
            "no_file_writes": ("Not guaranteed", "All three T0 roles exclude built-in write tools and Bash. Pack declarations and runtime state are not filesystem isolation."),
            "output_write_scope": ("Declared" if permissions["write_scope"] == "output" else "Not applicable",
                                   "Native writePaths scopes supported file-write tools, but T1 remains disabled until traversal and every symlink escape are blocked; Bash/MCP are outside this boundary."),
            "no_shell": ("Enforced" if permissions["shell"] == "none" else "Not applicable",
                         "Bash is absent from every role when shell=none; planner/verifier always exclude Bash."),
            "collect_files": ("Not applicable", "agent run does not provide the /agent/run collect_files snapshot contract."),
            "output_schema": ("Not verified", "Live audit must validate the workflow result against the expected and opposite JSON branches."),
            "skill_mcp_calls": ("No evidence", "CLI stdout is a workflow result, not paired SSE tool evidence."),
        }
        rows = [(claim, *replacements[claim]) if claim in replacements else (claim, status, basis)
                for claim, status, basis in rows]
    return [{"claim": claim, "status": status, "basis": basis} for claim, status, basis in rows]


def input_envelope(spec: dict[str, Any]) -> str:
    """Atlas prompt template rendering the JSON envelope the live audit sends: {"<input>": {input.<input>}}.

    Renderable only because AgentSpec validation (forge.spec, Atlas targets) already requires placeholder-safe
    input names and object/array input schemas; Atlas leaves other names literal and str()s scalars.
    """
    return "{" + ", ".join(json.dumps(item["name"]) + ": {input." + item["name"] + "}"
                           for item in spec["inputs"]) + "}"


def worker_outputs(spec: dict[str, Any]) -> dict[str, Any]:
    """Atlas node output fields: outputs + output_format json only for assistant_json; collect_files globs otherwise."""
    fields: dict[str, Any] = {}
    names = [output["name"] for output in spec["outputs"] if output["transport"] == "assistant_json"]
    if names:
        fields["outputs"], fields["output_format"] = names[:1], "json"
    collects = sorted({glob for output in spec["outputs"] if output["transport"] == "collect_files"
                       for glob in output["files"]["globs"]})
    if collects:
        fields["collect_files"] = collects
    return fields


def t2_node_template(spec: dict[str, Any]) -> dict[str, Any]:
    """M7b approval fragment; forge.atlas_export binds the placeholders and exports the complete flow (M8)."""
    worker_id, workspace_id = "__OPERATOR_T2_WORKER_ID__", "__OPERATOR_T2_WORKSPACE_ID__"
    worker = {"id": "publish", "type": "worker", "role": spec["routing"]["role"],
              "tags": spec["routing"]["tags"], "worker_id": worker_id, "workspace_id": workspace_id,
              "model": spec["model"]["id"], "prompt": "Execute only the approved input: " + input_envelope(spec),
              **worker_outputs(spec)}
    return {
        "deployment_requirements": {"tier": "T2", "isolated_daemon_required": True,
                                    "worker_id": worker_id, "workspace_id": workspace_id,
                                    "deployment_verified": False},
        "workflow": {
            "name": spec["identity"]["name"],
            "graph": {
                "start": "approval",
                "nodes": [{"id": "approval", "type": "human_gate",
                           "reason": "Review the run input, payload, configured destination and idempotency key.",
                           "choices": [{"id": "approve", "label": "Approve publication"},
                                       {"id": "reject", "label": "Reject publication"}]}, worker],
                "edges": [{"from": "approval", "to": "publish",
                           "condition": {"type": "human_selected", "choice": "approve"}}],
            },
            "policy": {"allowed_worker_ids": [worker_id], "allowed_workspace_ids": [workspace_id],
                       "max_jobs": 1, "max_attempts_per_node": 1},
        },
    }


def render(spec: dict[str, Any], packs_dir: Path | None = None) -> tuple[dict[str, bytes], dict[str, Any], list[str]]:
    """Render every package file and the unverified build report in memory.

    Returns (files, report, pack problems). The static audit re-renders a package from its
    agentspec.json and diffs, so this must stay free of timestamps, absolute paths and environment values.
    Standalone supports only the fixture-backed static-pipeline pattern.
    """
    validate_spec(spec)
    compatibility = resolve_compatibility(spec)
    standalone = spec["target"] == "thclaws-standalone"
    if compatibility["package_pattern"] not in {"single-worker", "static-pipeline"}:
        raise ValueError("standalone generation is not implemented for this pattern; use static-pipeline")
    roles = standalone_roles(spec, packs_dir or ROOT / "packs") if standalone else {}
    if standalone and (len(spec["outputs"]) != 1 or spec["outputs"][0]["transport"] != "assistant_json"):
        raise ValueError("M9 requires one sole assistant_json output")
    files, dependencies, problems = pack_assets(spec, (packs_dir or ROOT / "packs").resolve())

    def add_json(path: str, value: Any) -> None:
        if path in files:
            raise ValueError(f"generated file collision: {path}")
        files[path] = json_text(value).encode("utf-8")

    identity = spec["identity"]
    add_json("agentspec.json", spec)
    # Map AgentSpec to the upstream manifest shape; validation belongs to thClaws.
    add_json("manifest.json", {
        "version": identity["version"], "license": identity["license"], "author": identity["owner"],
        "categories": ["custom"], "tags": spec.get("routing", {}).get("tags", []),
        "requires": {
            # thClaws stores this string without parsing (checked 0.116.0 and 0.138.0); retain the full spec range.
            "thclaws_min_version": identity["thclaws_min_version"],
            "mcp_servers": sorted({server for pack in dependencies for server in pack["mcp_servers"]}),
        },
        "permissions": {"shell_execution": spec["permissions"]["shell"], "filesystem_scope": "workspace"},
    })
    settings = {"agent": {
        "id": identity["name"], "name": identity["name"], "description": spec["mission"]["purpose"],
    }}
    if standalone and spec["model"]["mode"] == "pinned":
        settings["model"] = spec["model"]["id"]
    add_json(".thclaws/settings.json", settings)
    add_json(".thclaws/schemas/refusal.json", spec["refusal"]["schema"])
    for section in ("inputs", "outputs"):
        for item in spec[section]:
            if "schema" in item:
                add_json(f".thclaws/schemas/{section}--{item['name']}.json", item["schema"])
    add_json("evaluation/golden-cases.json", spec["evaluation"]["golden_cases"])
    if spec["permissions"]["tier"] == "T2":
        add_json("atlas-node-template.json", t2_node_template(spec))
    for wrapper in WRAPPERS:
        files[wrapper] = (ROOT / "templates" / wrapper).read_bytes()

    templates = Environment(loader=FileSystemLoader(ROOT / "templates"), undefined=StrictUndefined,
                            autoescape=False, keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)
    templates.filters["json"] = lambda value: json_text(value).rstrip("\n")
    for template, path in (
        ("AGENTS.md.j2", "AGENTS.md"),
        ("SKILL.md.j2", f".thclaws/skills/{identity['name']}/SKILL.md"),
        ("system.md.j2", ".thclaws/prompt/system.md"),
    ):
        if path in files:
            raise ValueError(f"generated file collision: {path}")
        files[path] = templates.get_template(template).render(
            spec=spec, assistant_json=any(item["transport"] == "assistant_json" for item in spec["outputs"]),
        ).encode("utf-8")

    if standalone:
        add_json(".thclaws/schemas/branch.json", {"oneOf": [spec["outputs"][0]["schema"], spec["refusal"]["schema"]]})
        for role, tools in roles.items():
            files[f".thclaws/agents/{role}.md"] = templates.get_template("role.md.j2").render(
                spec=spec, role=role, tools=tools,
                contract=files["AGENTS.md"].decode(),
            ).encode()
        files[".thclaws/agent_workflow/run.js"] = templates.get_template("run.js.j2").render().encode()

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
            ("Start the daemon with CWD=package and THCLAWS_AUTO_MIGRATE=0 to load .thclaws/mcp.json and "
             ".thclaws/prompt/system.md; never open the package in the thClaws desktop app or run thclaws bots migrate "
             "on it (a host layout moves the agent files and breaks the Atlas workspace_dir contract); "
             "install external MCP runtimes separately."),
            "Provision model and environment variable names from agentspec.json at deployment time; no secret values are bundled.",
            "Manifest filesystem_scope=workspace is the packaging scope, not enforcement of the AgentSpec write_scope.",
            "Provision host egress controls outside the package according to pack network/hosts requirements.",
            *[f"Pack {p['pack']}: network={p['network']}; hosts={', '.join(p['hosts']) or 'none'}; "
              f"daemon env names={', '.join(p['env']) or 'none'}."
              for p in dependencies if p["status"] != "missing"],
            "Atlas chooses file-handoff landing paths. collect_files snapshots do not prove file authorship.",
            *[f"Missing pack: {pack['pack']}; implement/install it before static audit or live use."
              for pack in dependencies if pack["status"] == "missing"],
            *(["Use an isolated T2 daemon and an Atlas human_gate before any external side effect."]
              if spec["permissions"]["tier"] == "T2" else []),
        ],
    }
    if standalone:
        report["deployment_hints"][1] = (
            "Run thclaws agent run <package> --workflow .thclaws/agent_workflow/run.js --args '<input envelope>'; "
            "provision the pinned provider and external MCP runtimes. GUI orchestrators must call this same workflow.")
        report["deployment_hints"] = [hint for hint in report["deployment_hints"]
                                      if not hint.startswith("Atlas chooses")]
    Draft202012Validator(report_schema).validate(report)
    add_json("builder-build-report.json", report)
    return files, report, problems


def generate(spec: dict[str, Any], destination: Path, *, packs_dir: Path | None = None) -> dict[str, Any]:
    """Build into a new directory. Never overwrite an existing package or execute pack assets."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    files, report, problems = render(spec, packs_dir)
    if problems:
        raise ValueError("; ".join(problems))
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
            if path in WRAPPERS:
                output.chmod(0o755)
        for directory in (".thclaws/agents", ".thclaws/scripts", ".thclaws/skills", ".thclaws/schemas"):
            (staged / directory).mkdir(exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"destination already exists: {destination}")
        staged.rename(destination)
    return report
